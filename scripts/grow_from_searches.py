#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
grow_from_searches.py  —  訪問者の検索で地図を育てる (Ask the World / Phase 2b)

流れ:
  1. 無料Googleフォームに記録された検索語(公開CSV)を読む。鍵不要・サーバ側でのみ実行。
  2. 「学校」検索のうち、まだ未収録の日本の大学を OpenAlex で解決し、
     data/institutions.json と data/universities_seed.csv に追記(=次の sync で研究者も入る)。
  3. 検索需要を data/search_demand.json に記録(キーワードの伸ばしどころが見える化)。

安全装置:
  - 1回に追加する新規校は NEW_SCHOOLS_PER_RUN(既定15)まで(API/重さの暴走防止)。
  - 非JPに解決された候補は採用しない(build_master と同じガード)。
  - 大きな site_data/researchers は触らない(再生成は tested な sync に任せる)。

env:
  OPENALEX_API_KEY  … 新規校の解決に必要(未設定なら需要記録のみ)
  SEARCH_CSV_URL    … フォーム回答シートの公開CSV URL
  NEW_SCHOOLS_PER_RUN … 既定 15
"""

import csv
import io
import json
import os
import time
import urllib.parse
import urllib.request
import urllib.error
from collections import Counter, OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
INST = DATA / "institutions.json"
SEED = DATA / "universities_seed.csv"
DEMAND = DATA / "search_demand.json"

API = "https://api.openalex.org"
API_KEY = os.environ.get("OPENALEX_API_KEY", "").strip()
CSV_URL = os.environ.get("SEARCH_CSV_URL", "").strip()
CAP = int(os.environ.get("NEW_SCHOOLS_PER_RUN", "15"))


def has_cjk(s):
    return any("぀" <= c <= "ヿ" or "一" <= c <= "鿿" or "ｦ" <= c <= "ﾝ" for c in (s or ""))


def fetch_csv(url):
    if not url:
        return []
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ask-the-world"})
        with urllib.request.urlopen(req, timeout=30) as r:
            text = r.read().decode("utf-8", "replace")
    except Exception as e:
        print(f"[csv] 取得失敗: {type(e).__name__}: {e}")
        return []
    rows = list(csv.reader(io.StringIO(text)))
    return rows[1:] if rows else []   # 先頭はヘッダー


def parse_query(cell):
    """'kw:睡眠' / 'school:京都大学' → (type, query)"""
    cell = (cell or "").strip()
    if ":" in cell:
        t, q = cell.split(":", 1)
        return t.strip(), q.strip()
    return "?", cell


def oa_resolve_jp(name):
    """日本の機関として OpenAlex で解決(1件)。非JPは弾く。"""
    if not API_KEY:
        return None
    params = {
        "search": name,
        "filter": "country_code:JP",
        "per_page": 1,
        "select": "id,display_name,country_code,type,works_count,homepage_url,ror",
        "api_key": API_KEY,
    }
    url = f"{API}/institutions?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            d = json.load(r)
    except Exception as e:
        print(f"[oa] {name}: {type(e).__name__}")
        return None
    res = d.get("results") or []
    if res and res[0].get("country_code") == "JP":
        return res[0]
    return None


def main():
    rows = fetch_csv(CSV_URL)
    pairs = [parse_query(r[-1]) for r in rows if r and r[-1].strip()]
    demand = Counter(f"{t}:{q}" for t, q in pairs)
    schools = [q for t, q in pairs if t == "school" and q]
    print(f"検索ログ: {len(pairs)} 件 / 学校検索 {len(set(schools))} 種")

    # 需要を記録(透明性・キーワード拡張の指針)
    DEMAND.write_text(json.dumps({
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total": len(pairs),
        "top": demand.most_common(100),
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    if not schools:
        print("追加対象の学校検索なし。需要記録のみ。")
        return

    # 既存カバー集合(和名・英名・解決名)
    payload = json.loads(INST.read_text(encoding="utf-8"))
    insts = payload["institutions"] if isinstance(payload, dict) else payload
    covered, ids = set(), set()
    for i in insts:
        ids.add(i.get("id"))
        for k in ("name_ja", "name_en", "resolved_name", "name"):
            v = (i.get(k) or "").strip().lower()
            if v:
                covered.add(v)

    # 人気順に、未カバーの学校だけ解決して追加(CAPまで)
    added = 0
    for q, _ in Counter(schools).most_common():
        if added >= CAP:
            break
        if q.lower() in covered:
            continue
        inst = oa_resolve_jp(q)
        time.sleep(0.1)
        if not inst:
            print(f"  [skip] 解決できず/非JP: {q}")
            continue
        bid = inst["id"].rsplit("/", 1)[-1]
        if bid in ids:
            continue
        rec = OrderedDict(
            id=bid, name=inst["display_name"], resolved_name=inst["display_name"],
            country=inst.get("country_code"), type=inst.get("type"),
            works_count=inst.get("works_count", 0), homepage=inst.get("homepage_url"),
            ror=inst.get("ror"), field="Unknown", source="searched",
            name_ja=q if has_cjk(q) else "", name_en=inst["display_name"],
            prefecture="", kind="discovered", org_kind="university",
        )
        insts.append(rec); ids.add(bid); covered.add(q.lower()); added += 1
        # シードにも残す(将来のフル再構築でも保持)
        with SEED.open("a", encoding="utf-8", newline="") as f:
            csv.writer(f).writerow([rec["name_ja"], rec["name_en"], "discovered", "", "searched", "university"])
        print(f"  [add] {q} -> {rec['name']}")

    if added:
        if isinstance(payload, dict):
            payload["institutions"] = insts
            payload["count"] = len(insts)
        else:
            payload = insts
        INST.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== 完了 === 新規追加 {added} 校(次の sync で研究者が入ります)")


if __name__ == "__main__":
    main()
