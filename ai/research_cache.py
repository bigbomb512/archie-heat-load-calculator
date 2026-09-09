"""Reproducible, project-local cache for approved external engineering facts.

This module intentionally has no network client.  A separate evidence workflow
may populate the cache; calculations only read records that are approved,
in-scope, and not expired.
"""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json


RESEARCH_CATEGORIES = {
    "weather", "material_thermal_property", "glazing_property", "occupancy_default",
    "lighting_density_default", "equipment_manufacturer", "ventilation_requirement",
    "calculation_method",
}
REVIEW_STATUSES = {"proposed", "approved", "expired", "rejected"}


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def empty_research_cache():
    return {"schema_version": 1, "revision": 0, "records": [], "updated_at": "", "fingerprint": ""}


def _parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def validate_record(record):
    if not isinstance(record, dict):
        raise ValueError("Research record must be an object.")
    required = ("record_id", "url", "publisher", "retrieved_at", "content_hash", "category", "value", "unit", "scope", "citation", "review_status", "expiry")
    missing = [key for key in required if record.get(key) in (None, "")]
    if missing:
        raise ValueError("Research record missing: " + ", ".join(missing))
    if record["category"] not in RESEARCH_CATEGORIES:
        raise ValueError("Unsupported research category: " + str(record["category"]))
    if record["review_status"] not in REVIEW_STATUSES:
        raise ValueError("Invalid research review status.")
    if record["review_status"] == "approved" and not str(record.get("reviewed_by", "")).strip():
        raise ValueError("Approved research records require reviewed_by.")
    if not _parse_time(record["retrieved_at"]):
        raise ValueError("Research retrieved_at must be an ISO timestamp.")
    if record["expiry"] and not _parse_time(record["expiry"]):
        raise ValueError("Research expiry must be an ISO timestamp.")
    return deepcopy(record)


def validate_cache(cache):
    if not isinstance(cache, dict) or cache.get("schema_version") != 1:
        raise ValueError("Unsupported research cache schema.")
    records = [validate_record(row) for row in cache.get("records", [])]
    ids = [row["record_id"] for row in records]
    if len(ids) != len(set(ids)):
        raise ValueError("Research record IDs must be unique.")
    result = deepcopy(cache)
    result["records"] = records
    result["fingerprint"] = fingerprint({"revision": result.get("revision", 0), "records": records})
    return result


def eligible_records(cache, category, scope=None, now=None):
    cache = validate_cache(cache)
    now = now or datetime.now(timezone.utc)
    scope = scope or {}
    eligible = []
    for record in cache["records"]:
        if record["category"] != category or record["review_status"] != "approved":
            continue
        expiry = _parse_time(record.get("expiry"))
        if expiry and expiry <= now:
            continue
        record_scope = record.get("scope") or {}
        if any(scope.get(key) is not None and record_scope.get(key) != scope.get(key) for key in scope):
            continue
        eligible.append(record)
    return eligible


def upsert_record(cache, record):
    result = validate_cache(cache)
    record = validate_record(record)
    result["records"] = [row for row in result["records"] if row["record_id"] != record["record_id"]] + [record]
    result["revision"] = int(result.get("revision", 0)) + 1
    result["updated_at"] = timestamp()
    result["fingerprint"] = fingerprint({"revision": result["revision"], "records": result["records"]})
    return result
