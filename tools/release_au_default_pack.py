#!/usr/bin/env python3
"""Create a controlled engineer-release entry for an AU default cache.

This is deliberately a command-line release boundary, not a browser/API
action. It refuses candidate/proposed records and records exact content hashes
so a later cache edit cannot silently change a calculation.
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai.research_cache import source_pack_release_manifest, validate_cache, validate_source_pack_release_manifest


def release(cache_path, manifest_path, *, pack_version, record_ids, engineer_name,
            engineer_credential, scope, approval_reference, expiry=""):
    cache = validate_cache(json.loads(Path(cache_path).read_text(encoding="utf-8")))
    if cache.get("source_pack_version") != pack_version:
        raise ValueError("Cache source-pack version does not match the requested release.")
    requested = set(record_ids)
    records = [row for row in cache["records"] if row["record_id"] in requested]
    missing = requested - {row["record_id"] for row in records}
    if missing:
        raise ValueError("Unknown record IDs: " + ", ".join(sorted(missing)))
    if not records:
        raise ValueError("At least one record must be released.")
    not_approved = [row["record_id"] for row in records if row.get("review_status") != "approved"]
    if not_approved:
        raise ValueError("Only engineer-approved records may be released: " + ", ".join(sorted(not_approved)))
    if any(not row.get("content_hash") for row in records):
        raise ValueError("Every released record needs an exact content hash.")
    current = source_pack_release_manifest(manifest_path) if Path(manifest_path).exists() else validate_source_pack_release_manifest({"schema_version": 1, "releases": []})
    release_row = {
        "release_id": "release-" + uuid.uuid4().hex[:12],
        "pack_version": pack_version,
        "status": "released",
        "engineer": {"name": engineer_name.strip(), "credential": engineer_credential.strip()},
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "approval_reference": approval_reference.strip(),
        "scope": dict(scope or {}),
        "expiry": expiry.strip(),
        "records": [{"record_id": row["record_id"], "content_hash": row["content_hash"]} for row in records],
    }
    checked = validate_source_pack_release_manifest({"schema_version": 1, "releases": current["releases"] + [release_row]})
    Path(manifest_path).write_text(json.dumps(checked, indent=2) + "\n", encoding="utf-8")
    return checked


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True, help="Approved project/source-pack cache")
    parser.add_argument("--manifest", type=Path, default=ROOT / "config" / "research_source_pack_releases.json")
    parser.add_argument("--pack-version", required=True)
    parser.add_argument("--record-id", action="append", required=True)
    parser.add_argument("--engineer-name", required=True)
    parser.add_argument("--engineer-credential", required=True)
    parser.add_argument("--approval-reference", required=True)
    parser.add_argument("--scope", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--expiry", default="")
    args = parser.parse_args()
    scope = {}
    for item in args.scope:
        if "=" not in item:
            parser.error("--scope must be KEY=VALUE")
        key, value = item.split("=", 1)
        scope[key.strip()] = value.strip()
    result = release(args.cache, args.manifest, pack_version=args.pack_version, record_ids=args.record_id,
                     engineer_name=args.engineer_name, engineer_credential=args.engineer_credential,
                     scope=scope, approval_reference=args.approval_reference, expiry=args.expiry)
    print(json.dumps({"manifest": str(args.manifest), "release_count": len(result["releases"]), "fingerprint": result["fingerprint"]}, indent=2))


if __name__ == "__main__":
    main()
