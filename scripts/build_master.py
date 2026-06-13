#!/usr/bin/env python3
"""
build_master.py — 大学シードCSVをOpenAlexの機関IDに解決する
==============================================================
入力 : data/universities_seed.csv
出力 : data/institutions.json   (解決済みマスター)
       data/unresolved.txt      (解決できなかった大学 → 手動確認用)

OpenAlex Institutions API(無料・認証不要)で英語名を検索し、
日本(JP)の教育機関に絞って最良一致を選ぶ。
シードリストの誤字や名称変更はここで自動検出される(自己修正の仕組み)。

実行: python scripts/build_master.py
環境変数: OPENALEX_MAILTO=you@example.com (推奨: politeプールで高速化)
"""
import csv, json, os, re, sys, time, urllib.parse, urllib.request

API = "https://api.openalex.org/institutions"
MAILTO = os.environ.get("OPENALEX_MAILTO", "")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED = os.path.join(ROOT, "data", "universities_seed.csv")
OUT = os.path.join(ROOT, "data", "institutions.json")
UNRESOLVED = os.path.join(ROOT, "data", "unresolved.txt")


def fetch(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "toi-univ-search/0.1"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def score(query: str, cand: dict) -> int:
    """名称一致度のスコアリング。完全一致 > 別名一致 > 部分一致。"""
    q = norm(query)
    names = [cand.get("display_name", "")] + cand.get("display_name_alternatives", [])
    best = 0
    for n in names:
        nn = norm(n)
        if nn == q:
            best = max(best, 100)
        elif q in nn or nn in q:
            best = max(best, 60)
    # 教育・研究組織であること・日本であることを優先
    # (universityだけでなくinstitute等も収容する: org_kind一般化)
    if cand.get("type") in ("education", "facility", "government", "nonprofit"):
        best += 10
    if (cand.get("country_code") or "") == "JP":
        best += 10
    return best


def resolve(name_en: str) -> dict | None:
    params = {"search": name_en, "filter": "country_code:JP", "per_page": "5"}
    if MAILTO:
        params["mailto"] = MAILTO
    url = API + "?" + urllib.parse.urlencode(params)
    try:
        data = fetch(url)
    except Exception as e:
        print(f"  ! API error for {name_en}: {e}", file=sys.stderr)
        return None
    results = data.get("results", [])
    if not results:
        return None
    ranked = sorted(results, key=lambda c: score(name_en, c), reverse=True)
    top = ranked[0]
    return top if score(name_en, top) >= 60 else None


def main():
    rows = list(csv.DictReader(open(SEED, encoding="utf-8")))
    print(f"シード: {len(rows)}校を解決します")
    institutions, unresolved = [], []

    for i, row in enumerate(rows, 1):
        hit = resolve(row["name_en"])
        if hit:
            institutions.append({
                "name_ja": row["name_ja"],
                "name_en": row["name_en"],
                "type": row["type"],                 # national / public / private
                "org_kind": row.get("org_kind", "university"),  # university / institute / 将来: corporate_lab, online_community...
                "prefecture": row["prefecture"],
                "selection_reason": row.get("reason", ""),
                "openalex_id": hit["id"].rsplit("/", 1)[-1],   # 例: I123456789
                "ror": hit.get("ror"),
                "homepage": hit.get("homepage_url"),
                "works_count": hit.get("works_count"),
                "resolved_name": hit.get("display_name"),
            })
            print(f"[{i}/{len(rows)}] ✓ {row['name_ja']} → {hit['display_name']}")
        else:
            unresolved.append(f"{row['name_ja']},{row['name_en']}")
            print(f"[{i}/{len(rows)}] ✗ 未解決: {row['name_ja']}")
        time.sleep(0.12)  # polite rate limit (~8 req/s 上限の余裕内)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(institutions, open(OUT, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    open(UNRESOLVED, "w", encoding="utf-8").write("\n".join(unresolved))

    print(f"\n解決: {len(institutions)} / 未解決: {len(unresolved)}")
    print(f"→ {OUT}")
    if unresolved:
        print(f"→ 未解決リスト: {UNRESOLVED} を確認し、name_en を修正して再実行してください")


if __name__ == "__main__":
    main()
