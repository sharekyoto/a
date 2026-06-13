#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync_researchers.py  —  研究者の取得・差分・アーカイブ (Ask the World)

decision 1: Gemini は使わない (クリティカルパスから除外)。
decision 2: 職階ではなく「所属研究者を活動量順に」。topics を必ず持たせる。
消さない設計: いなくなった研究者も status=archived で残す。

入力 : data/institutions.json   (build_master.py の出力)
出力 : data/researchers.json    (フルデータ + 差分メタ)
        data/site_data.json     (フロント用の軽量サブセット)

OpenAlex のみ。APIキー不要。
"""

import hashlib
import json
import os
import time
import urllib.parse
import urllib.request
import urllib.error
from collections import OrderedDict
from pathlib import Path

# --- 設定 ----------------------------------------------------------------
MAILTO = "sharekyoto@gmail.com"
API = "https://api.openalex.org"

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
INST_IN = DATA / "institutions.json"
RES_OUT = DATA / "researchers.json"
SITE_OUT = DATA / "site_data.json"

# シードは「薄く広く」。深い層はフロントのオンデマンド取得 (index.html) に任せる。
# 0 を渡すと「全員」 (上限なし)。
MAX_PER_INST = int(os.environ.get("MAX_PER_INST", "120"))
PER_PAGE = 200
SELECT = ("id,display_name,orcid,works_count,cited_by_count,"
          "summary_stats,topics,last_known_institutions")
TODAY = time.strftime("%Y-%m-%d", time.gmtime())

# --- HTTP ----------------------------------------------------------------
def fetch(path, **params):
    params["mailto"] = MAILTO
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    for attempt in range(6):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": f"ask-the-world ({MAILTO})"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 ** attempt)
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            time.sleep(1 + attempt)
    raise RuntimeError(f"request failed: {url}")


def bare_id(u):
    return u.rsplit("/", 1)[-1]


# --- 1人分のレコードへ正規化 --------------------------------------------
def normalize(author, inst):
    raw = author.get("topics") or []
    topics = [t["display_name"] for t in raw[:6]]
    # 問いの木との突合キー: OpenAlex の field id (例 "31") を重複なく集める
    field_ids, field_names = [], []
    for t in raw:
        f = t.get("field") or {}
        fid = (f.get("id") or "").rsplit("/", 1)[-1]
        if fid and fid not in field_ids:
            field_ids.append(fid)
            field_names.append(f.get("display_name"))
    rec = OrderedDict(
        id=bare_id(author["id"]),
        name=author["display_name"],
        orcid=author.get("orcid"),
        inst_id=inst.get("openalex_id") or inst.get("id"),
        inst_name=inst.get("resolved_name") or inst.get("name_ja") or inst.get("name"),
        country=inst.get("country"),
        works_count=author.get("works_count", 0),
        cited_by_count=author.get("cited_by_count", 0),
        h_index=(author.get("summary_stats") or {}).get("h_index", 0),
        topics=topics,
        fields=field_ids,            # ["31","19"] のような field id 配列
        field_names=field_names,
    )
    return rec


def content_hash(rec):
    """変化検出用。表示に効くフィールドだけを対象にする。"""
    key = json.dumps(
        [rec["name"], rec["inst_id"], rec["works_count"],
         rec["cited_by_count"], rec["h_index"], rec["topics"], rec["fields"]],
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


# --- 機関ごとの研究者取得 (cursor ページング) ---------------------------
def fetch_researchers(inst):
    found, cursor = [], "*"
    inst_id = inst.get("openalex_id") or inst.get("id")
    if not inst_id:
        return found
    while cursor:
        try:
            d = fetch(
                "/authors",
                filter=f"last_known_institutions.id:{inst_id}",
                sort="cited_by_count:desc",
                per_page=PER_PAGE,
                cursor=cursor,
                select=SELECT,
            )
            for a in d.get("results") or []:
                found.append(normalize(a, inst))
                if MAX_PER_INST and len(found) >= MAX_PER_INST:
                    return found
            cursor = (d.get("meta") or {}).get("next_cursor")
            time.sleep(0.1)
        except Exception as e:
            print(f"  error fetching {inst_id}: {e}")
            break
    return found


# --- 既存データ読み込み --------------------------------------------------
def load_existing():
    if RES_OUT.exists():
        prev = json.loads(RES_OUT.read_text(encoding="utf-8"))
        # 古いバージョンは { "researchers": [...] } 形式、新しいのは直リスト
        if isinstance(prev, dict):
            return {r["id"]: r for r in prev.get("researchers", [])}
        else:
            return {r["id"]: r for r in prev}
    return {}


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    if not INST_IN.exists():
        raise SystemExit(f"先に build_master.py を実行してください ({INST_IN} がありません)")

    raw = json.loads(INST_IN.read_text(encoding="utf-8"))
    institutions = raw if isinstance(raw, list) else raw.get("institutions", [])
    existing = load_existing()
    seen_ids = set()
    added = updated = unchanged = 0

    for i, inst in enumerate(institutions, 1):
        try:
            people = fetch_researchers(inst)
        except Exception as e:
            name = inst.get("resolved_name") or inst.get("name_ja") or inst.get("name")
            print(f"[skip] {name}: {e}")
            continue

        for rec in people:
            seen_ids.add(rec["id"])
            h = content_hash(rec)
            old = existing.get(rec["id"])
            if old is None:
                rec.update(status="active", first_seen=TODAY, updated=TODAY, hash=h)
                existing[rec["id"]] = rec
                added += 1
            elif old.get("hash") != h:
                rec.update(status="active",
                           first_seen=old.get("first_seen", TODAY),
                           updated=TODAY, hash=h)
                existing[rec["id"]] = rec
                updated += 1
            else:
                old["status"] = "active"
                old["last_checked"] = TODAY
                unchanged += 1

        print(f"[{i}/{len(institutions)}] {inst['name']}: {len(people)}人")

    # 消さない設計: 今回見つからなかった既存研究者は archived にして残す
    archived = 0
    for rid, rec in existing.items():
        if rid not in seen_ids and rec.get("status") != "archived":
            rec["status"] = "archived"
            rec["archived_on"] = TODAY
            archived += 1

    researchers = sorted(existing.values(),
                         key=lambda r: r.get("cited_by_count", 0), reverse=True)

    RES_OUT.write_text(json.dumps(
        OrderedDict(generated=TODAY, count=len(researchers), researchers=researchers),
        ensure_ascii=False, indent=2), encoding="utf-8")

    # フロント用の軽量サブセット (active のみ・余分なメタを落とす)
    site = [
        OrderedDict(
            id=r["id"], name=r["name"], inst=r["inst_name"], country=r["country"],
            works=r["works_count"], cited=r["cited_by_count"], h=r["h_index"],
            topics=r["topics"], fields=r.get("fields", []), orcid=r.get("orcid"),
        )
        for r in researchers if r.get("status") == "active"
    ]
    SITE_OUT.write_text(json.dumps(
        OrderedDict(generated=TODAY, count=len(site),
                    institutions=[{"id": x["id"], "name": x["name"],
                                   "country": x.get("country"), "field": x.get("field")}
                                  for x in institutions],
                    researchers=site),
        ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== 完了 ===")
    print(f"新規 {added} / 更新 {updated} / 不変 {unchanged} / アーカイブ {archived}")
    print(f"合計 {len(researchers)}人 (active {len(site)}人)  ->  {RES_OUT.name}, {SITE_OUT.name}")


if __name__ == "__main__":
    main()
