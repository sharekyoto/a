#!/usr/bin/env python3
"""
sync_researchers.py — 各大学の研究者をOpenAlexから取得し、差分を検出する
==========================================================================
入力 : data/institutions.json
出力 : data/researchers.json  (全研究者 + 出典 + 取得日時)
       data/changed_ids.txt   (前回から変化した研究者ID → 要約の再生成対象)

更新の仕組み(無料で自走させる設計):
  OpenAlexの from_updated_date フィルタは有料プラン限定のため、
  代わりに「毎週全件を再取得 → レコードのハッシュを前回と比較」する。
  機関259組織 × 1リクエスト程度なので毎週数分・コストゼロで完了する。
  変化があった研究者だけが summarize.py でAI要約を再生成される。

アーカイブポリシー(消さない設計):
  このツールの目的は大学選びではなく「師匠との出会い」なので、
  一度収集した研究者は、所属組織が選定基準から外れても・取得上位から
  落ちても削除しない。archived=true を付けて検索に残し続け、
  最終確認日(retrieved_at)は最後に実在確認できた日のまま据え置く。
  容量影響: 1人約1KB。1万人でも約10MBで、無料枠の誤差の範囲。

実行: python scripts/sync_researchers.py [--per-univ 25] [--min-works 20]
環境変数: OPENALEX_MAILTO=you@example.com
"""
import argparse, hashlib, json, os, sys, time, urllib.parse, urllib.request
from datetime import date

API = "https://api.openalex.org/authors"
MAILTO = os.environ.get("OPENALEX_MAILTO", "")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INST = os.path.join(ROOT, "data", "institutions.json")
OUT = os.path.join(ROOT, "data", "researchers.json")
CHANGED = os.path.join(ROOT, "data", "changed_ids.txt")


def fetch(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "toi-univ-search/0.1"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def top_authors(inst_id: str, per_univ: int, min_works: int) -> list[dict]:
    """被引用数上位の現役研究者を取得。"""
    params = {
        "filter": f"last_known_institutions.id:{inst_id},works_count:>{min_works}",
        "sort": "cited_by_count:desc",
        "per_page": str(per_univ),
        "select": "id,display_name,orcid,works_count,cited_by_count,"
                  "summary_stats,topics,last_known_institutions",
    }
    if MAILTO:
        params["mailto"] = MAILTO
    return fetch(API + "?" + urllib.parse.urlencode(params)).get("results", [])


def record_hash(rec: dict) -> str:
    """要約の再生成が必要かを判定するための内容ハッシュ。
    研究テーマ(topics)と業績規模が変わったときだけ変化する。"""
    core = {
        "name": rec["name_en"],
        "topics": rec["topics"],
        "works_count": rec["works_count"] // 10,  # 細かい増減では発火させない
    }
    return hashlib.sha256(
        json.dumps(core, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:16]


def current_institution(author_id: str) -> dict | None:
    """個別著者の最新所属を1件取得(転出追跡用)。"""
    params = {"select": "id,last_known_institutions"}
    if MAILTO:
        params["mailto"] = MAILTO
    url = f"{API}/{author_id}?" + urllib.parse.urlencode(params)
    insts = fetch(url).get("last_known_institutions") or []
    if not insts:
        return None
    top = insts[0]
    return {"name_en": top.get("display_name"),
            "openalex_id": (top.get("id") or "").rsplit("/", 1)[-1]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-univ", type=int, default=25,
                    help="1大学あたりの研究者数(初期値25 → 全体で約6,000人)")
    ap.add_argument("--min-works", type=int, default=20)
    ap.add_argument("--track-moves", action="store_true",
                    help="新たにアーカイブされた研究者の現所属を追跡(週次の追加コストは微小)")
    args = ap.parse_args()

    institutions = json.load(open(INST, encoding="utf-8"))
    prev_records: dict[str, dict] = {}
    if os.path.exists(OUT):
        prev_records = {r["openalex_id"]: r for r in
                        json.load(open(OUT, encoding="utf-8"))}
    prev_hash = {k: v.get("hash") for k, v in prev_records.items()}

    today = date.today().isoformat()
    researchers, changed = [], []

    for i, inst in enumerate(institutions, 1):
        try:
            authors = top_authors(inst["openalex_id"], args.per_univ, args.min_works)
        except Exception as e:
            print(f"  ! {inst['name_ja']}: {e}", file=sys.stderr)
            continue

        for a in authors:
            aid = a["id"].rsplit("/", 1)[-1]
            rec = {
                "openalex_id": aid,
                "name_en": a["display_name"],
                "orcid": a.get("orcid"),
                "university_ja": inst["name_ja"],
                "university_en": inst["name_en"],
                "univ_type": inst["type"],
                "org_kind": inst.get("org_kind", "university"),
                "prefecture": inst["prefecture"],
                "works_count": a.get("works_count", 0),
                "cited_by_count": a.get("cited_by_count", 0),
                "h_index": (a.get("summary_stats") or {}).get("h_index"),
                "topics": [t["display_name"] for t in (a.get("topics") or [])[:6]],
                "sources": [
                    {"label": "OpenAlex", "url": a["id"]},
                    {"label": "researchmap検索",
                     "url": "https://researchmap.jp/researchers?q="
                            + urllib.parse.quote(a["display_name"])},
                ] + ([{"label": "ORCID", "url": a["orcid"]}] if a.get("orcid") else []),
                "retrieved_at": today,
            }
            rec["hash"] = record_hash(rec)
            rec["archived"] = False        # 今回の取得で実在確認できた
            if prev_hash.get(aid) != rec["hash"]:
                changed.append(aid)
            researchers.append(rec)

        print(f"[{i}/{len(institutions)}] {inst['name_ja']}: {len(authors)}人")
        time.sleep(0.12)

    # ---- アーカイブ統合(消さない設計の本体) ----
    # 前回いたが今回の取得に出てこなかった研究者(所属が選定外になった/
    # 取得上位から落ちた等)は、削除せず archived=true で残す。
    # retrieved_at は据え置き = 「最終確認日が古いまま検索に出る」仕様。
    current_ids = {r["openalex_id"] for r in researchers}
    archived_count = moved_count = 0
    for aid, old in prev_records.items():
        if aid not in current_ids:
            newly_archived = not old.get("archived")
            old["archived"] = True
            # 転出追跡: 今回初めてアーカイブされた人だけ現所属を1回確認。
            # 「師匠を追いかける」ための機能 — 引退ではなく転出なら行き先を示す。
            if args.track_moves and newly_archived and not old.get("moved_to"):
                try:
                    now = current_institution(aid)
                    if now and now["name_en"] and now["name_en"] != old["university_en"]:
                        old["moved_to"] = now
                        moved_count += 1
                    time.sleep(0.12)
                except Exception:
                    pass  # 追跡失敗は致命的でない。次回再試行される
            researchers.append(old)   # 古い retrieved_at のまま保持
            archived_count += 1

    json.dump(researchers, open(OUT, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    open(CHANGED, "w").write("\n".join(changed))
    active = len(researchers) - archived_count
    print(f"\n現役: {active}人 / アーカイブ: {archived_count}人"
          f"(うち今回転出検出: {moved_count}人) / "
          f"要再要約(新規+変更): {len(changed)}人")
    print(f"→ {OUT}\n→ {CHANGED}")


if __name__ == "__main__":
    main()
