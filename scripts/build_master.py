#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_master.py  —  厳選機関の解決 (Ask the World)

選定基準 (decision 3):
  研究規模 (works_count) × 国 × 分野の代表性。
  - data/universities_seed.csv の手動リストを OpenAlex ID に解決し、
  - さらに各対象国で works_count 上位を自動で top-up して国の網羅性を担保、
  - x_concepts から分野カバレッジを集計してレポート表示する。

OpenAlex のみ。APIキー不要・Gemini不要。
出力: data/institutions.json
"""

import csv
import json
import time
import urllib.parse
import urllib.request
import urllib.error
from collections import Counter, OrderedDict
from pathlib import Path

# --- 設定 ----------------------------------------------------------------
MAILTO = "sharekyoto@gmail.com"          # OpenAlex polite pool
API = "https://api.openalex.org"

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SEED_CSV = DATA / "universities_seed.csv"   # 任意。列: name[,country_code][,note]
OUT = DATA / "institutions.json"

# 国×分野の代表性を担保するための自動 top-up
COUNTRIES = [
    "JP", "US", "GB", "DE", "FR", "CH", "NL", "SE", "CA", "AU", "SG",
    "KR", "CN", "IT", "ES", "BR", "IN", "IL", "DK", "FI", "NO", "BE",
    "AT", "NZ", "TW", "HK",
]
TOP_PER_COUNTRY = 8                  # 各国 works_count 上位 N を自動採用
INCLUDE_FACILITIES = True            # 研究機関 (RIKEN, Max Planck 等) も含める
SELECT = "id,display_name,country_code,type,works_count,homepage_url,ror,x_concepts"

# --- HTTP ----------------------------------------------------------------
def fetch(path, **params):
    params["mailto"] = MAILTO
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    for attempt in range(6):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": f"ask-the-world ({MAILTO})"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 ** attempt)      # exponential backoff
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            time.sleep(1 + attempt)
    raise RuntimeError(f"request failed: {url}")


def bare_id(openalex_url):
    """'https://openalex.org/I123' -> 'I123'"""
    return openalex_url.rsplit("/", 1)[-1]


def top_field(inst):
    """level-0 x_concept (OpenAlex ルート概念 ≒ 分野) を1つ返す。"""
    roots = [c for c in (inst.get("x_concepts") or []) if c.get("level") == 0]
    roots.sort(key=lambda c: c.get("score", 0), reverse=True)
    return roots[0]["display_name"] if roots else "Unknown"


def compact(inst, source):
    return OrderedDict(
        id=bare_id(inst["id"]),
        name=inst["display_name"],
        country=inst.get("country_code"),
        type=inst.get("type"),
        works_count=inst.get("works_count", 0),
        homepage=inst.get("homepage_url"),
        ror=inst.get("ror"),
        field=top_field(inst),
        source=source,                  # 'seed' | 'auto'
    )


# --- 収集 ----------------------------------------------------------------
def resolve_seed():
    out = []
    if not SEED_CSV.exists():
        print(f"[seed] {SEED_CSV} なし — 自動収集のみで進めます")
        return out
    with SEED_CSV.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            name = (row.get("name") or "").strip()
            if not name:
                continue
            d = fetch("/institutions", search=name, per_page=1, select=SELECT)
            res = d.get("results") or []
            if res:
                rec = compact(res[0], "seed")
                rec["seed_name"] = name
                out.append(rec)
                print(f"[seed] {name}  ->  {rec['name']} ({rec['country']})")
            else:
                print(f"[seed] !! 解決できず: {name}")
            time.sleep(0.1)
    return out


def auto_top_up():
    out = []
    types = ["education", "facility"] if INCLUDE_FACILITIES else ["education"]
    for cc in COUNTRIES:
        for t in types:
            d = fetch(
                "/institutions",
                filter=f"country_code:{cc},type:{t}",
                sort="works_count:desc",
                per_page=TOP_PER_COUNTRY,
                select=SELECT,
            )
            for inst in d.get("results") or []:
                out.append(compact(inst, "auto"))
            time.sleep(0.1)
        print(f"[auto] {cc}: 上位 {TOP_PER_COUNTRY} を取得")
    return out


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    seed = resolve_seed()
    auto = auto_top_up()

    # seed を優先してマージ・重複排除 (同一 OpenAlex ID)
    merged = OrderedDict()
    for rec in seed + auto:
        if rec["id"] not in merged:
            merged[rec["id"]] = rec
    institutions = list(merged.values())
    institutions.sort(key=lambda r: r["works_count"], reverse=True)

    # 分野・国カバレッジのレポート (代表性の確認用)
    by_country = Counter(r["country"] for r in institutions)
    by_field = Counter(r["field"] for r in institutions)

    payload = OrderedDict(
        generated=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        count=len(institutions),
        coverage=OrderedDict(
            countries=dict(by_country.most_common()),
            fields=dict(by_field.most_common()),
        ),
        institutions=institutions,
    )
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== 完了 ===")
    print(f"機関数: {len(institutions)}  ->  {OUT}")
    print(f"国: {len(by_country)} か国 / 分野: {len(by_field)} 種")
    print("分野カバレッジ:", dict(by_field.most_common(8)))


if __name__ == "__main__":
    main()
