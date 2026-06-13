#!/usr/bin/env python3
"""
summarize.py — 研究テーマを高校生に届く「問い」に翻訳する(変更分のみ)
========================================================================
入力 : data/researchers.json, data/changed_ids.txt, cache/summaries.json
出力 : cache/summaries.json (ハッシュキーのキャッシュ・蓄積)
       data/site_data.json  (サイトが読む最終データ)

コスト設計:
  - changed_ids.txt にある研究者だけAPIを呼ぶ(初回以降はほぼゼロ)
  - キャッシュキーは内容ハッシュなので、同じ研究内容に二度課金しない
  - モデルは Haiku(安価)で十分。1人あたり約0.1〜0.2円程度

実行: python scripts/summarize.py [--limit 100]
環境変数: ANTHROPIC_API_KEY=sk-ant-...   MODEL=claude-haiku-4-5-20251001
"""
import argparse, json, os, sys, time, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "data", "researchers.json")
CHANGED = os.path.join(ROOT, "data", "changed_ids.txt")
CACHE = os.path.join(ROOT, "cache", "summaries.json")
SITE = os.path.join(ROOT, "data", "site_data.json")

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = os.environ.get("MODEL", "claude-haiku-4-5-20251001")

PROMPT = """あなたは高校1・2年生(15〜17歳)に研究の面白さを伝える編集者です。
以下の研究者情報をもとに、JSONだけを出力してください(前置き・コードブロック禁止)。

研究者: {name}
所属: {univ}
研究トピック(OpenAlexによる機械分類): {topics}

出力形式:
{{
 "toi_ja": "この研究者が人生をかけて追いかけている問いを、高校生の心に刺さる一文の疑問文で(専門用語を使わない)",
 "toi_en": "the same question in natural English",
 "summary_ja": "研究内容を3行以内・中学生でもわかる言葉で。比喩を1つ使う",
 "summary_en": "the same in English, max 3 lines",
 "tags_ja": ["分野タグ2〜3個(高校生が知る言葉で)", "関連する学部名1個"],
 "tags_en": ["same in English"],
 "field": "life / ai / earth / society / humanities / space のどれか1つ"
}}

注意: トピックは機械分類なので矛盾があれば多数派を信じる。誇張しない。断定できないことは書かない。"""


def call_claude(rec: dict) -> dict:
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 700,
        "messages": [{"role": "user", "content": PROMPT.format(
            name=rec["name_en"], univ=rec["university_ja"],
            topics=", ".join(rec["topics"]) or "(不明)")}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"Content-Type": "application/json",
                 "x-api-key": API_KEY, "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=60) as r:
        out = json.load(r)
    text = "".join(b.get("text", "") for b in out.get("content", []))
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
    return json.loads(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0,
                    help="このランで要約する最大人数(0=無制限)。コスト管理用")
    args = ap.parse_args()

    researchers = json.load(open(RES, encoding="utf-8"))
    changed = set(open(CHANGED).read().split()) if os.path.exists(CHANGED) else set()
    cache = json.load(open(CACHE, encoding="utf-8")) if os.path.exists(CACHE) else {}

    todo = [r for r in researchers
            if r["openalex_id"] in changed and r["hash"] not in cache]
    if args.limit:
        todo = todo[: args.limit]
    print(f"要約対象: {len(todo)}人(キャッシュ済みはスキップ)")

    if todo and not API_KEY:
        sys.exit("ANTHROPIC_API_KEY が未設定です")

    for i, rec in enumerate(todo, 1):
        try:
            cache[rec["hash"]] = call_claude(rec)
            print(f"[{i}/{len(todo)}] ✓ {rec['name_en']} ({rec['university_ja']})")
        except Exception as e:
            print(f"[{i}/{len(todo)}] ✗ {rec['name_en']}: {e}", file=sys.stderr)
        time.sleep(0.3)
        if i % 50 == 0:  # 中断に強いよう逐次保存
            os.makedirs(os.path.dirname(CACHE), exist_ok=True)
            json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False)

    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # ---- サイト用データの書き出し(要約があるものだけ公開) ----
    site = []
    for r in researchers:
        s = cache.get(r["hash"])
        if not s:
            continue
        site.append({
            "field": s.get("field", "life"),
            "region": "jp",
            "toi": {"ja": s["toi_ja"], "en": s["toi_en"]},
            "name": {"ja": r["name_en"], "en": r["name_en"]},
            "aff": {"ja": r["university_ja"], "en": r["university_en"]},
            "sum": {"ja": s["summary_ja"], "en": s["summary_en"]},
            "tags": {"ja": s["tags_ja"], "en": s["tags_en"]},
            "univ_type": r["univ_type"],
            "org_kind": r.get("org_kind", "university"),
            "archived": r.get("archived", False),
            "moved_to": r.get("moved_to"),
            "prefecture": r["prefecture"],
            "sources": r["sources"],
            "checked": r["retrieved_at"],
        })
    json.dump(site, open(SITE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\nサイトデータ: {len(site)}人 → {SITE}")
    print("HTML側は const professors = [...] を "
          "fetch('data/site_data.json') に差し替えれば本番データで動きます")


if __name__ == "__main__":
    main()
