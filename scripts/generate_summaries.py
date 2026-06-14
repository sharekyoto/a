#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_summaries.py  —  研究者の日本語要約を少しずつ無料で作り置き (Ask the World)

方針:
  - 研究者の英語トピックを Gemini 2.5 Flash-Lite(無料枠)で中高生向けの日本語1〜2文に要約。
  - 被引用の多い順に、まだ要約の無い人を 1日 DAILY_CAP 人だけ生成して data/summaries.json に追記。
  - ブラウザには鍵を出さない。生成はビルド時(GitHub Actions)だけ。表示は静的JSONを読むのみ。

コスト安全装置(「急に増額が怖い」対策):
  - 無料キーに Billing を紐付けない限り課金は発生しない(無料枠超過は失敗するだけ)。
  - DAILY_CAP(既定1200 < 無料枠1500/日)で1回の生成量を制限。
  - 既に要約済み(トピック不変)はスキップ。トピックが変われば作り直す。

env:
  GEMINI_API_KEY   … Google AI Studio の無料APIキー(Secret から注入)。未設定なら何もせず終了。
  DAILY_CAP        … 1回の最大生成数(既定 1200)
  GEMINI_MODEL     … 既定 gemini-2.5-flash-lite
"""

import json
import os
import time
import hashlib
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SITE = DATA / "site_data.json"
OUT = DATA / "summaries.json"

API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite").strip()
DAILY_CAP = int(os.environ.get("DAILY_CAP", "1200"))
ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"

PROMPT_HEAD = (
    "あなたは中高生に研究者を紹介する編集者です。次の研究者が『何を研究しているか』を、"
    "中高生にも分かる平易な日本語で1〜2文(全角60字以内)に要約してください。"
    "専門用語はかみくだき、トピックに無いことは足さない(誇張・創作禁止)。"
    "敬称・固有名詞の羅列は避け、研究内容のみ。出力は要約文だけ。\n\n"
)


def topics_hash(topics):
    return hashlib.sha1(json.dumps(topics, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]


def build_prompt(r):
    topics = [t for t in (r.get("topics") or []) if t]
    body = f"所属: {r.get('inst','')}\n研究トピック(英語): {', '.join(topics[:6]) or '(不明)'}\n"
    return PROMPT_HEAD + body


def call_gemini(prompt):
    """Gemini generateContent を1回呼ぶ。失敗時は None。"""
    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 120},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{ENDPOINT}?key={API_KEY}",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError):
        return None


def load_summaries():
    if OUT.exists():
        d = json.loads(OUT.read_text(encoding="utf-8"))
        return d.get("summaries", {}) if isinstance(d, dict) else {}
    return {}


def select_targets(site, existing, cap):
    """被引用の多い順に、未生成 or トピックが変わった人を cap 人選ぶ。"""
    rows = site if isinstance(site, list) else site.get("researchers", [])
    rows = sorted(rows, key=lambda r: r.get("cited", 0), reverse=True)
    targets = []
    for r in rows:
        rid = r.get("id")
        if not rid or not (r.get("topics")):
            continue
        h = topics_hash(r.get("topics"))
        old = existing.get(rid)
        if old and old.get("h") == h:
            continue                      # 生成済み(トピック不変)
        targets.append((r, h))
        if len(targets) >= cap:
            break
    return targets


def main():
    if not API_KEY:
        print("GEMINI_API_KEY 未設定。要約生成をスキップ(フロントは分野チップにフォールバック)。")
        return
    site = json.loads(SITE.read_text(encoding="utf-8"))
    summaries = load_summaries()
    targets = select_targets(site, summaries, DAILY_CAP)
    print(f"対象: {len(targets)} 人 (cap {DAILY_CAP}) / 既存 {len(summaries)} 件 / model {MODEL}")

    done = 0
    for i, (r, h) in enumerate(targets, 1):
        try:
            ja = call_gemini(build_prompt(r))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                print(f"  [429] 無料枠の上限に到達。今日はここまで({done}件)。翌日続行。")
                break
            print(f"  [HTTP {e.code}] skip {r.get('id')}")
            ja = None
        except Exception as e:
            print(f"  [err] {type(e).__name__} skip {r.get('id')}")
            ja = None
        if ja:
            summaries[r["id"]] = {"ja": ja, "h": h}
            done += 1
        time.sleep(2.1)                   # 30 RPM 無料枠を尊重
        if i % 100 == 0:
            print(f"  ... {i}/{len(targets)} (生成 {done})")

    payload = {
        "version": 1,
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model": MODEL,
        "count": len(summaries),
        "summaries": summaries,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"\n=== 完了 === 今回生成 {done} 件 / 累計 {len(summaries)} 件 -> {OUT.name}")


if __name__ == "__main__":
    main()
