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

from ai.research_cache import (
    CANDIDATE_BINDING_POLICY,
    approved_source_pack,
    empty_research_cache,
    upsert_record,
    validate_cache,
    validate_candidate_record,
    source_domain_allowed,
)


def _load(path, default):
    if not path.exists():
        return deepcopy(default)
    return json.loads(path.read_text(encoding="utf-8"))


def _record(raw, source, pack_version):
    row = deepcopy(raw)
    record_source = row.pop("source", source)
    if not isinstance(record_source, dict):
        raise ValueError(f"Candidate {row.get('record_id', '<unknown>')} source must be an object.")
    row.update({
        "url": record_source.get("url", ""),
        "publisher": record_source.get("publisher", ""),
        "retrieved_at": record_source.get("retrieved_at", ""),
        "content_hash": record_source.get("content_hash", ""),
        "source_pack_version": pack_version,
        # This developer-only importer is not a release workflow.  It always
        # makes candidates non-calculating, regardless of input-file flags.
        "review_status": "proposed",
        "reviewed_by": "",
        "released": False,
        "citation": row.get("citation") or record_source.get("citation", ""),
        "expiry": row.get("expiry", ""),
    })
    return validate_candidate_record(row)


def inspect(pack_path):
    """Read and validate a candidate pack without writing a project cache."""
    pack = _load(pack_path, {})
    pack_version = str(pack.get("pack_version", "")).strip()
    source = pack.get("source") or {}
    rows = pack.get("records") or []
    if not pack_version or not isinstance(source, dict) or not rows:
        raise ValueError("The default-pack manifest must contain pack_version, source, and records.")
    pack_policy = approved_source_pack(pack_version)
    if not pack_policy:
        raise ValueError(f"Candidate pack version {pack_version} is not configured.")
    checked, errors = [], []
    seen = set()
    for index, raw in enumerate(rows, start=1):
        try:
            record = _record(raw, source, pack_version)
            if record["record_id"] in seen:
                raise ValueError(f"Duplicate candidate record_id {record['record_id']}.")
            if not source_domain_allowed(record["url"], pack_version):
                raise ValueError(f"Candidate {record['record_id']} source domain is not allowlisted for {pack_version}.")
            if record["category"] not in set(pack_policy.get("permitted_default_categories", [])):
                raise ValueError(f"Candidate {record['record_id']} category {record['category']} is not permitted by {pack_version}.")
            seen.add(record["record_id"])
            checked.append(record)
        except (TypeError, ValueError) as error:
            errors.append({"index": index, "record_id": str((raw or {}).get("record_id", "")), "reason": str(error)})
    coverage = pack.get("required_coverage", [])
    if not isinstance(coverage, list):
        raise ValueError("required_coverage must be a list when supplied.")
    target_counts = {}
    category_counts = {}
    for record in checked:
        category_counts[record["category"]] = category_counts.get(record["category"], 0) + 1
        for binding in record["bindings"]:
            target_counts[binding["target"]] = target_counts.get(binding["target"], 0) + 1
    missing_coverage = []
    for item in coverage:
        category, target = str(item.get("category", "")), str(item.get("target", ""))
        coverage_scope = item.get("scope") or {}
        if not isinstance(coverage_scope, dict):
            errors.append({"record_id": "coverage", "reason": f"Required coverage scope for {category}:{target} must be an object."})
            continue
        if category not in CANDIDATE_BINDING_POLICY or target not in CANDIDATE_BINDING_POLICY.get(category, {}):
            errors.append({"record_id": "coverage", "reason": f"Unsupported required coverage {category}:{target}."})
        elif not any(
            record["category"] == category and any(
                binding["target"] == target and all(
                    ({**record.get("scope", {}), **binding.get("scope", {})}).get(key) == value
                    for key, value in coverage_scope.items()
                )
                for binding in record["bindings"]
            )
            for record in checked
        ):
            missing_coverage.append({"category": category, "target": target, "scope": coverage_scope})
    return {
        "pack_version": pack_version,
        "candidate_count": len(checked),
        "candidate_counts_by_category": dict(sorted(category_counts.items())),
        "candidate_counts_by_target": dict(sorted(target_counts.items())),
        "missing_coverage": missing_coverage,
        "ineligible_records": errors,
        "records": checked,
    }


def seed(cache_path, pack_path):
    report = inspect(pack_path)
    if report["ineligible_records"]:
        messages = "; ".join(row["reason"] for row in report["ineligible_records"])
        raise ValueError("Candidate pack cannot be seeded: " + messages)
    pack_version = report["pack_version"]
    cache = validate_cache(_load(cache_path, empty_research_cache()))
    if cache.get("records") and cache.get("source_pack_version") not in {"", pack_version}:
        raise ValueError("The cache already uses a different source pack; review it before replacing the pack.")
    cache["source_pack_version"] = pack_version
    existing = {row["record_id"]: row for row in cache["records"]}
    changed = []
    for record in report["records"]:
        previous = existing.get(record["record_id"])
        if previous == record:
            continue
        cache = upsert_record(cache, record)
        existing[record["record_id"]] = record
        changed.append(record["record_id"])
    cache = validate_cache(cache)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=2) + "\n", encoding="utf-8")
    return {
        "cache_path": str(cache_path), "pack_version": pack_version,
        "changed_record_ids": changed, "record_count": len(cache["records"]),
        "released_record_count": 0,
        "candidate_counts_by_category": report["candidate_counts_by_category"],
        "candidate_counts_by_target": report["candidate_counts_by_target"],
        "missing_coverage": report["missing_coverage"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-path", type=Path, help="Private project research_cache.json path")
    parser.add_argument("--pack", type=Path, default=ROOT / "config" / "au_cooling_default_pack.json")
    parser.add_argument("--check", action="store_true", help="Validate and report a candidate pack without writing a cache")
    args = parser.parse_args()
    if args.check:
        report = inspect(args.pack)
        # The full normalised records are useful to seed(), but a developer
        # checking a pack needs the compact coverage report, not profile data.
        display = {key: value for key, value in report.items() if key != "records"}
        print(json.dumps(display, indent=2))
        if report["ineligible_records"]:
            raise SystemExit(1)
    else:
        if not args.cache_path:
            parser.error("--cache-path is required unless --check is used")
        print(json.dumps(seed(args.cache_path, args.pack), indent=2))


if __name__ == "__main__":
    main()
