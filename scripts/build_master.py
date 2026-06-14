#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_master.py  —  掲載機関の解決 (Ask the World)

日本版方針 (2026-06):
  data/universities_seed.csv の日本の大学・研究機関 (国立/公立/私立) を
  OpenAlex 機関ID に解決して data/institutions.json を作る。
  シードの和名・英名・都道府県・種別を保持し、フロントの日本語校名検索に使う。

  ※ 以前はここで全世界 26 か国の works_count 上位を自動採用していたため
    データが「全世界・薄く」化していた。日本網羅へ寄せるため AUTO_TOPUP は既定オフ。
    (AUTO_TOPUP=1 かつ COUNTRIES 指定時のみ従来の自動収集を行う)

認証 (2026-02-13 以降必須):
  OpenAlex は api_key URL パラメータ方式。Authorization ヘッダではない。
  キーは GitHub Actions Secret から OPENALEX_API_KEY で注入。キー無しは即エラー。

出力: data/institutions.json
"""

import csv
import json
import os
import time
import urllib.parse
import urllib.request
import urllib.error
from collections import Counter, OrderedDict
from pathlib import Path

# --- 設定 ----------------------------------------------------------------
CONTACT = "sharekyoto@gmail.com"          # User-Agent 用の連絡先のみ
API = "https://api.openalex.org"
API_KEY = os.environ.get("OPENALEX_API_KEY", "").strip()

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SEED_CSV = DATA / "universities_seed.csv"   # 列: name_ja,name_en,type,prefecture,reason,org_kind
OUT = DATA / "institutions.json"
UNRESOLVED = DATA / "unresolved.txt"

# 全世界自動収集 (既定オフ)。1 のときだけ COUNTRIES の上位を追加採用する。
AUTO_TOPUP = os.environ.get("AUTO_TOPUP", "0") == "1"
COUNTRIES = [c for c in os.environ.get("COUNTRIES", "").split(",") if c]
TOP_PER_COUNTRY = int(os.environ.get("TOP_PER_COUNTRY", "8"))
INCLUDE_FACILITIES = True
SELECT = "id,display_name,country_code,type,works_count,homepage_url,ror,x_concepts"


# --- HTTP ----------------------------------------------------------------
def fetch(path, **params):
    """OpenAlex API fetch (api_key 認証・指数バックオフ)。"""
    if not API_KEY:
        raise SystemExit(
            "OPENALEX_API_KEY が未設定です。2026-02-13 以降 OpenAlex はキー必須 "
            "(mailto 廃止)。GitHub Actions の Secret から注入してください。"
        )
    params["api_key"] = API_KEY
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    for attempt in range(6):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": f"ask-the-world ({CONTACT})"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 ** attempt)            # exponential backoff
                continue
            if e.code in (401, 403, 409):
                raise RuntimeError(
                    f"OpenAlex auth/credit error {e.code}. "
                    f"OPENALEX_API_KEY と 1日$1 の無料枠を確認してください。"
                ) from e
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


def compact(inst, source, seed=None):
    rec = OrderedDict(
        id=bare_id(inst["id"]),
        name=inst["display_name"],          # OpenAlex 上の英語表示名 (= site_data.inst の結合キー)
        resolved_name=inst["display_name"], # sync の inst_name 優先キー。英語名で site_data.inst を統一
        country=inst.get("country_code"),
        type=inst.get("type"),
        works_count=inst.get("works_count", 0),
        homepage=inst.get("homepage_url"),
        ror=inst.get("ror"),
        field=top_field(inst),
        source=source,                       # 'seed' | 'auto'
    )
    if seed:                                 # 日本語校名検索のためにシード情報を保持
        rec["name_ja"] = seed.get("name_ja", "")
        rec["name_en"] = seed.get("name_en", "")
        rec["prefecture"] = seed.get("prefecture", "")
        rec["kind"] = seed.get("type", "")   # national / public / private
        rec["org_kind"] = seed.get("org_kind", "")
    return rec


# --- 収集 ----------------------------------------------------------------
def resolve_seed():
    out, unresolved = [], []
    if not SEED_CSV.exists():
        print(f"[seed] {SEED_CSV} なし — スキップ")
        return out, unresolved
    with SEED_CSV.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            name_en = (row.get("name_en") or "").strip()
            name_ja = (row.get("name_ja") or "").strip()
            query = name_en or name_ja        # 英名優先 (解決率が高い)
            if not query:
                continue
            d = fetch("/institutions", search=query, per_page=1, select=SELECT)
            res = d.get("results") or []
            if res:
                rec = compact(res[0], "seed", seed=row)
                out.append(rec)
                print(f"[seed] {name_ja or query}  ->  {rec['name']} ({rec['country']})")
            else:
                unresolved.append(f"{name_ja},{name_en}")
                print(f"[seed] !! 解決できず: {name_ja or query}")
            time.sleep(0.1)
    return out, unresolved


def auto_top_up():
    out = []
    if not (AUTO_TOPUP and COUNTRIES):
        return out
    types = ["education", "facility"] if INCLUDE_FACILITIES else ["education"]
    for cc in COUNTRIES:
        for t in types:
            d = fetch("/institutions", filter=f"country_code:{cc},type:{t}",
                      sort="works_count:desc", per_page=TOP_PER_COUNTRY, select=SELECT)
            for inst in d.get("results") or []:
                out.append(compact(inst, "auto"))
            time.sleep(0.1)
        print(f"[auto] {cc}: 上位 {TOP_PER_COUNTRY} を取得")
    return out


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    seed, unresolved = resolve_seed()
    auto = auto_top_up()

    # seed を優先してマージ・重複排除 (同一 OpenAlex ID)
    merged = OrderedDict()
    for rec in seed + auto:
        if rec["id"] not in merged:
            merged[rec["id"]] = rec
    institutions = list(merged.values())
    institutions.sort(key=lambda r: r["works_count"], reverse=True)

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
    if unresolved:
        UNRESOLVED.write_text("\n".join(unresolved) + "\n", encoding="utf-8")

    print("\n=== 完了 ===")
    print(f"機関数: {len(institutions)} (seed {len(seed)} / auto {len(auto)})  ->  {OUT}")
    print(f"国: {len(by_country)} か国 / 分野: {len(by_field)} 種")
    if unresolved:
        print(f"未解決: {len(unresolved)} 件  ->  {UNRESOLVED}")


if __name__ == "__main__":
    main()
