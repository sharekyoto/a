#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync_researchers.py  —  研究者の取得・差分・アーカイブ (Ask the World)

decision 1: Gemini は使わない (クリティカルパスから除外)。
decision 2: 職階ではなく「所属研究者を活動量順に」。topics を必ず持たせる。
消さない設計: いなくなった研究者も status=archived で残す。

入力 : data/institutions.json   (institutions のリスト)
出力 : data/researchers.json    (researchers のリスト)
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

MAX_PER_INST = int(os.environ.get("MAX_PER_INST", "120"))
PER_PAGE = 100
TODAY = time.strftime("%Y-%m-%d", time.gmtime())

# --- HTTP ----------------------------------------------------------------
def fetch(path, **params):
    """OpenAlex API fetch with exponential backoff."""
    params["mailto"] = MAILTO
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"

    for attempt in range(5):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": f"ask-the-world ({MAILTO})"}
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 2 ** attempt
                print(f"  [429] waiting {wait}s...")
                time.sleep(wait)
                continue
            else:
                raise
        except Exception as e:
            wait = 1 + attempt
            print(f"  [retry {attempt}] {type(e).__name__}, waiting {wait}s...")
            time.sleep(wait)

    raise RuntimeError(f"Failed after 5 attempts: {url}")


def bare_id(u):
    return u.rsplit("/", 1)[-1] if u else None


def normalize(author, inst):
    """Normalize author to researcher record."""
    raw_topics = author.get("topics") or []
    topics = [t.get("display_name", "") for t in raw_topics[:6]]

    # Extract field IDs from topics
    field_ids = []
    for t in raw_topics:
        f = t.get("field") or {}
        fid = bare_id(f.get("id"))
        if fid and fid not in field_ids:
            field_ids.append(fid)

    return OrderedDict(
        id=bare_id(author.get("id")),
        name=author.get("display_name", ""),
        orcid=author.get("orcid"),
        inst_id=inst.get("openalex_id") or inst.get("id", ""),
        inst_name=inst.get("resolved_name") or inst.get("name_ja") or inst.get("name", ""),
        country=inst.get("country"),
        works_count=author.get("works_count", 0),
        cited_by_count=author.get("cited_by_count", 0),
        h_index=(author.get("summary_stats") or {}).get("h_index", 0),
        topics=topics,
        fields=field_ids,
    )


def content_hash(rec):
    """Hash for change detection."""
    key = json.dumps(
        [rec["name"], rec["inst_id"], rec["works_count"],
         rec["cited_by_count"], rec["h_index"], rec["topics"], rec["fields"]],
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def load_existing():
    """Load previous researchers.json (supports both list and dict formats)."""
    if RES_OUT.exists():
        prev = json.loads(RES_OUT.read_text(encoding="utf-8"))
        if isinstance(prev, dict):
            return {r["id"]: r for r in prev.get("researchers", []) if r.get("id")}
        else:
            return {r["id"]: r for r in prev if r.get("id")}
    return {}


def fetch_researchers_page(inst_id, cursor):
    """Fetch one page of researchers."""
    return fetch(
        "/authors",
        filter=f"last_known_institutions.id:{inst_id}",
        sort="cited_by_count:desc",
        per_page=PER_PAGE,
        cursor=cursor,
        select="id,display_name,orcid,works_count,cited_by_count,summary_stats,topics",
    )


def fetch_researchers(inst):
    """Fetch all researchers for an institution (with cursor pagination)."""
    found = []
    inst_id = inst.get("openalex_id") or inst.get("id")

    if not inst_id:
        return found

    cursor = "*"
    page = 0

    while cursor and (not MAX_PER_INST or len(found) < MAX_PER_INST):
        try:
            page += 1
            data = fetch_researchers_page(inst_id, cursor)

            results = data.get("results") or []
            if not results:
                break

            for author in results:
                if MAX_PER_INST and len(found) >= MAX_PER_INST:
                    break
                found.append(normalize(author, inst))

            # Check for next page
            next_cursor = (data.get("meta") or {}).get("next_cursor")
            if next_cursor == cursor:  # No progress
                break
            cursor = next_cursor

            if page % 5 == 0:
                time.sleep(0.5)  # Be nice to the API

        except Exception as e:
            print(f"    error on page {page}: {type(e).__name__}: {str(e)[:80]}")
            break

    return found


def main():
    print(f"\n=== {TODAY} ===\n")

    DATA.mkdir(parents=True, exist_ok=True)

    # Load institutions
    if not INST_IN.exists():
        print(f"ERROR: {INST_IN} not found")
        return

    raw = json.loads(INST_IN.read_text(encoding="utf-8"))
    institutions = raw if isinstance(raw, list) else raw.get("institutions", [])
    print(f"Loaded {len(institutions)} institutions")

    # Load existing researchers
    existing = load_existing()
    print(f"Previous: {len(existing)} researchers")

    seen_ids = set()
    added = updated = unchanged = archived = 0

    # Fetch for each institution
    for i, inst in enumerate(institutions, 1):
        inst_name = inst.get("resolved_name") or inst.get("name_ja") or inst.get("name", "?")
        inst_id = inst.get("openalex_id") or inst.get("id", "?")

        try:
            people = fetch_researchers(inst)
            print(f"[{i:3d}/{len(institutions)}] {inst_name:40s} → {len(people):5d} people")
        except Exception as e:
            print(f"[{i:3d}/{len(institutions)}] {inst_name:40s} → ERROR: {str(e)[:60]}")
            continue

        # Process researchers
        for rec in people:
            if not rec.get("id"):
                continue

            seen_ids.add(rec["id"])
            h = content_hash(rec)
            old = existing.get(rec["id"])

            if old is None:
                rec.update(status="active", first_seen=TODAY, updated=TODAY, hash=h)
                existing[rec["id"]] = rec
                added += 1
            elif old.get("hash") != h:
                rec.update(
                    status="active",
                    first_seen=old.get("first_seen", TODAY),
                    updated=TODAY,
                    hash=h
                )
                existing[rec["id"]] = rec
                updated += 1
            else:
                old["status"] = "active"
                old["last_checked"] = TODAY
                unchanged += 1

    # Archive missing researchers
    for rid, rec in existing.items():
        if rid not in seen_ids and rec.get("status") != "archived":
            rec["status"] = "archived"
            rec["archived_on"] = TODAY
            archived += 1

    # Write outputs
    researchers = sorted(
        existing.values(),
        key=lambda r: r.get("cited_by_count", 0),
        reverse=True
    )

    RES_OUT.write_text(
        json.dumps(researchers, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    # Site data (active only, lightweight)
    site = [
        OrderedDict(
            id=r["id"],
            name=r["name"],
            inst=r["inst_name"],
            country=r["country"],
            works=r["works_count"],
            cited=r["cited_by_count"],
            h=r["h_index"],
            topics=r["topics"],
            fields=r.get("fields", []),
            orcid=r.get("orcid"),
        )
        for r in researchers
        if r.get("status") == "active"
    ]

    SITE_OUT.write_text(
        json.dumps(site, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    # Summary
    print(f"\n=== Summary ===")
    print(f"New: {added} | Updated: {updated} | Unchanged: {unchanged} | Archived: {archived}")
    print(f"Total: {len(researchers)} ({len(site)} active)")
    print(f"Written to: {RES_OUT.name}, {SITE_OUT.name}\n")


if __name__ == "__main__":
    main()
