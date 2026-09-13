#!/usr/bin/env python3
"""Seed a project-local Australia-first default-pack candidate cache.

This tool copies only cited, versioned candidate records from the checked-in
pack manifest into an explicitly supplied private ``research_cache.json``.
Records remain ``proposed`` and unreleased.  A source-pack release review is
required before ``eligible_bindings`` can use them during calculation.
"""

import argparse
import json
from copy import deepcopy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai.research_cache import empty_research_cache, upsert_record, validate_cache, validate_record


def _load(path, default):
    if not path.exists():
        return deepcopy(default)
    return json.loads(path.read_text(encoding="utf-8"))


def _record(raw, source, pack_version):
    row = deepcopy(raw)
    row.update({
        "url": source["url"],
        "publisher": source["publisher"],
        "retrieved_at": source["retrieved_at"],
        "content_hash": source["content_hash"],
        "source_pack_version": pack_version,
        "review_status": "proposed",
        "reviewed_by": "",
        "released": False,
        "citation": row.get("citation") or source["citation"],
        "expiry": row.get("expiry", ""),
    })
    return validate_record(row)


def seed(cache_path, pack_path):
    pack = _load(pack_path, {})
    pack_version = str(pack.get("pack_version", "")).strip()
    source = pack.get("source") or {}
    rows = pack.get("records") or []
    if not pack_version or not source or not rows:
        raise ValueError("The default-pack manifest must contain pack_version, source, and records.")
    cache = validate_cache(_load(cache_path, empty_research_cache()))
    if cache.get("records") and cache.get("source_pack_version") not in {"", pack_version}:
        raise ValueError("The cache already uses a different source pack; review it before replacing the pack.")
    cache["source_pack_version"] = pack_version
    existing = {row["record_id"]: row for row in cache["records"]}
    changed = []
    for raw in rows:
        record = _record(raw, source, pack_version)
        previous = existing.get(record["record_id"])
        if previous == record:
            continue
        cache = upsert_record(cache, record)
        existing[record["record_id"]] = record
        changed.append(record["record_id"])
    cache = validate_cache(cache)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=2) + "\n", encoding="utf-8")
    return {"cache_path": str(cache_path), "pack_version": pack_version, "changed_record_ids": changed, "record_count": len(cache["records"]), "released_record_count": sum(row.get("released") is True for row in cache["records"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-path", required=True, type=Path, help="Private project research_cache.json path")
    parser.add_argument("--pack", type=Path, default=ROOT / "config" / "au_cooling_default_pack.json")
    args = parser.parse_args()
    print(json.dumps(seed(args.cache_path, args.pack), indent=2))


if __name__ == "__main__":
    main()
