"""Reproducible, project-local cache for approved external engineering facts.

This module intentionally has no network client.  A separate evidence workflow
may populate the cache; calculations only read records that are approved,
in-scope, and not expired.
"""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse


RESEARCH_CATEGORIES = {
    "weather", "material_thermal_property", "glazing_property", "occupancy_default",
    "lighting_density_default", "equipment_manufacturer", "ventilation_requirement",
    "calculation_method", "schedule_default",
}
REVIEW_STATUSES = {"proposed", "approved", "expired", "rejected"}

# A record only becomes calculation-eligible when it is approved *and* released
# by a named reviewer.  Every other status is stored, visible and inert.
RELEASABLE_STATUSES = {"approved"}
ALLOWED_URL_SCHEMES = {"https"}

# Targets whose value may never come from a source pack, whatever the pack says.
# Geometry, envelope construction and room use are project facts that must be
# read from the drawings or confirmed by the engineer, never defaulted.
PROHIBITED_BINDING_TARGETS = {
    "room.area_m2", "room.volume_m3", "room.height_m", "room.occupancy",
    "room.envelope_surfaces", "room.u_value_w_m2k", "room.shgc",
    "room.glazing_area_m2", "room.orientation", "room.use",
}

# Bindings for these target families must carry a building/use scope, so a
# generic record can never be applied to an unrelated room type.
USE_SCOPED_TARGET_PREFIXES = ("room.", "schedule.")


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def empty_research_cache():
    return {"schema_version": 1, "revision": 0, "source_pack_version": "", "records": [], "updated_at": "", "fingerprint": ""}


def approved_source_pack(pack_version):
    path = Path(__file__).resolve().parents[1] / "config" / "approved_research_source_packs.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return (data.get("packs") or {}).get(str(pack_version), {})


def permitted_binding_targets(pack):
    """Targets this pack is allowed to supply automatic defaults for."""
    return {str(target).strip() for target in pack.get("released_binding_targets", []) if str(target).strip()}


def prohibited_binding_targets(pack):
    declared = {str(target).strip() for target in pack.get("blocked_binding_targets", []) if str(target).strip()}
    return declared | PROHIBITED_BINDING_TARGETS


def permitted_binding_target(pack, target):
    """A pack may only widen within the allowlist; the denylist always wins."""
    target = str(target).strip()
    if not target or target in prohibited_binding_targets(pack):
        return False
    return target in permitted_binding_targets(pack)


def _allowed_domain(url, pack):
    host = (urlparse(str(url)).hostname or "").lower()
    return any(host == domain or host.endswith("." + domain) for domain in pack.get("allowed_domains", []))


def released_source_record(cache, record):
    """Whether a record may supply an automatic default, not merely be stored."""
    pack_version = cache.get("source_pack_version", "")
    pack = approved_source_pack(pack_version)
    return bool(
        pack
        and pack_version
        and record.get("source_pack_version") == pack_version
        and record.get("review_status") in RELEASABLE_STATUSES
        and record.get("released") is True
        and str(record.get("reviewed_by", "")).strip()
        and str(record.get("publisher", "")).strip()
        and str(record.get("citation", "")).strip()
        and str(record.get("content_hash", "")).strip()
        and _parse_time(record.get("retrieved_at"))
        and _allowed_domain(record.get("url", ""), pack)
    )


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
    for key in ("publisher", "citation", "content_hash"):
        if not str(record.get(key, "")).strip():
            raise ValueError(f"Research record {key} must not be blank.")
    parsed_url = urlparse(str(record["url"]))
    if parsed_url.scheme not in ALLOWED_URL_SCHEMES or not parsed_url.hostname:
        raise ValueError("Research url must be an https URL with a hostname.")
    retrieved = _parse_time(record["retrieved_at"])
    if not retrieved:
        raise ValueError("Research retrieved_at must be an ISO timestamp.")
    expiry = _parse_time(record["expiry"]) if record["expiry"] else None
    if record["expiry"] and not expiry:
        raise ValueError("Research expiry must be an ISO timestamp.")
    if expiry and retrieved and expiry <= retrieved:
        raise ValueError("Research expiry must be after retrieved_at.")
    if not isinstance(record.get("scope"), dict):
        raise ValueError("Research record scope must be an object.")
    result = deepcopy(record)
    result["source_pack_version"] = str(result.get("source_pack_version", "")).strip()
    result["released"] = bool(result.get("released", False))
    if result["released"]:
        # A release is never implied.  It requires an approval, a named
        # reviewer, a pack version and a declared geographic scope.
        if result["review_status"] not in RELEASABLE_STATUSES:
            raise ValueError("Only approved research records may be released.")
        if not result["source_pack_version"]:
            raise ValueError("Released research records require a source_pack_version.")
        if not str(result["scope"].get("country", "")).strip():
            raise ValueError("Released research records require a geographic scope (scope.country).")
        if retrieved and retrieved > datetime.now(timezone.utc):
            raise ValueError("Released research records cannot be retrieved in the future.")
    bindings = result.get("bindings", [])
    if not isinstance(bindings, list):
        raise ValueError("Research record bindings must be a list.")
    checked_bindings = []
    for index, binding in enumerate(bindings, start=1):
        if not isinstance(binding, dict):
            raise ValueError("Research binding must be an object.")
        target = str(binding.get("target", "")).strip()
        if not target or binding.get("value") in (None, ""):
            raise ValueError(f"Research binding {index} needs a target and value.")
        binding_scope = binding.get("scope", {})
        if not isinstance(binding_scope, dict):
            raise ValueError(f"Research binding {index} scope must be an object.")
        if result["released"]:
            if target in prohibited_binding_targets(approved_source_pack(result["source_pack_version"])):
                raise ValueError(f"Research binding {index} targets a field that may never be defaulted: {target}")
            combined = dict(result["scope"])
            combined.update(binding_scope)
            if target.startswith(USE_SCOPED_TARGET_PREFIXES) and not any(
                str(combined.get(key, "")).strip() for key in ("room_use", "building_use")
            ):
                raise ValueError(f"Research binding {index} needs a building or room use scope for target {target}.")
        checked_bindings.append({"target": target, "value": deepcopy(binding["value"]), "unit": str(binding.get("unit", result["unit"])).strip(), "scope": deepcopy(binding_scope)})
    result["bindings"] = checked_bindings
    return result


def validate_cache(cache):
    if not isinstance(cache, dict) or cache.get("schema_version") != 1:
        raise ValueError("Unsupported research cache schema.")
    records = [validate_record(row) for row in cache.get("records", [])]
    ids = [row["record_id"] for row in records]
    if len(ids) != len(set(ids)):
        raise ValueError("Research record IDs must be unique.")
    result = deepcopy(cache)
    result["records"] = records
    result["source_pack_version"] = str(cache.get("source_pack_version", ""))
    result["fingerprint"] = fingerprint({"revision": result.get("revision", 0), "source_pack_version": result["source_pack_version"], "records": records})
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


def _scope_matches(record_scope, requested):
    """A record can be broader than a project, but may not contradict it."""
    for key, value in (requested or {}).items():
        if value in (None, ""):
            continue
        if key in record_scope and record_scope[key] not in (None, "", value):
            return False
    return True


def eligible_bindings(cache, target, scope=None, now=None):
    """Return approved, unexpired target-specific records for deterministic use.

    Bindings make a source record useful only for fields it explicitly declares;
    a generic occupancy or weather record can never be guessed onto a field.
    """
    cache = validate_cache(cache)
    now = now or datetime.now(timezone.utc)
    pack = approved_source_pack(cache.get("source_pack_version", ""))
    if not permitted_binding_target(pack, target):
        return []
    result = []
    for record in cache["records"]:
        if not released_source_record(cache, record):
            continue
        expiry = _parse_time(record.get("expiry"))
        if expiry and expiry <= now:
            continue
        for binding in record.get("bindings", []):
            if binding["target"] != target:
                continue
            combined_scope = dict(record.get("scope") or {})
            combined_scope.update(binding.get("scope") or {})
            if not _scope_matches(combined_scope, scope or {}):
                continue
            item = deepcopy(record)
            item.update({"value": deepcopy(binding["value"]), "unit": binding.get("unit", record["unit"]), "binding_target": target, "scope": combined_scope})
            result.append(item)
    return sorted(result, key=lambda row: row["record_id"])


def upsert_record(cache, record):
    result = validate_cache(cache)
    record = validate_record(record)
    result["records"] = [row for row in result["records"] if row["record_id"] != record["record_id"]] + [record]
    result["revision"] = int(result.get("revision", 0)) + 1
    result["updated_at"] = timestamp()
    result["fingerprint"] = fingerprint({"revision": result["revision"], "source_pack_version": result.get("source_pack_version", ""), "records": result["records"]})
    return result
