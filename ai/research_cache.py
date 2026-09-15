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
    "calculation_method", "schedule_default", "indoor_cooling_condition",
    "people_gain_default", "safety_allowance",
}
REVIEW_STATUSES = {"proposed", "approved", "expired", "rejected"}
RELEASE_STATUSES = {"candidate", "released", "revoked"}

# This is intentionally separate from the calculator field registry.  It is
# the ingestion boundary for reusable external defaults, and excludes every
# geometry, envelope, glazing and equipment-heat field by construction.
CANDIDATE_BINDING_POLICY = {
    "weather": {"scenario.weather_profile": {"profile"}},
    "indoor_cooling_condition": {
        "room.indoor_cooling_setpoint_c": {"C"},
        "room.indoor_cooling_wet_bulb_c": {"C"},
    },
    "safety_allowance": {"room.safety_factor": {""}},
    "occupancy_default": {"room.occupancy_density_per_m2": {"people/m2"}},
    "people_gain_default": {
        "room.people_sensible_w_per_person": {"W/person"},
        "room.people_latent_w_per_person": {"W/person"},
        "room.people_diversity_factor": {""},
    },
    "lighting_density_default": {
        "room.lighting_w_m2": {"W/m2"},
        "room.lighting_diversity_factor": {""},
    },
    "ventilation_requirement": {
        "room.outside_air_lps_per_person": {"L/s/person"},
        "room.outside_air_lps_per_m2": {"L/s/m2"},
    },
    "schedule_default": {
        "schedule.people": {"profile"},
        "schedule.lighting": {"profile"},
        "schedule.outside_air": {"profile"},
    },
}


def _release_manifest_path():
    return Path(__file__).resolve().parents[1] / "config" / "research_source_pack_releases.json"

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


def _parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def empty_source_pack_release_manifest():
    return {"schema_version": 1, "releases": [], "fingerprint": ""}


def validate_source_pack_release_manifest(raw):
    """Validate the controlled engineering releases for automatic defaults.

    A release is deliberately separate from a project-local cache record.  It
    lets an engineer approve the exact cited content hash once, while a normal
    project user can still store research candidates without granting them
    calculation authority.
    """
    source = deepcopy(raw or empty_source_pack_release_manifest())
    if not isinstance(source, dict) or source.get("schema_version") != 1:
        raise ValueError("Unsupported research source-pack release manifest schema.")
    releases, release_ids = [], set()
    for index, release in enumerate(source.get("releases", []), start=1):
        if not isinstance(release, dict):
            raise ValueError(f"Release {index} must be an object.")
        release_id = str(release.get("release_id", "")).strip()
        pack_version = str(release.get("pack_version", "")).strip()
        status = str(release.get("status", "candidate")).strip()
        engineer = release.get("engineer") or {}
        if not release_id or release_id in release_ids or not pack_version:
            raise ValueError(f"Release {index} needs a unique release_id and pack_version.")
        if status not in RELEASE_STATUSES:
            raise ValueError(f"Release {release_id} has an invalid status.")
        if status == "released":
            if not isinstance(engineer, dict) or not str(engineer.get("name", "")).strip() or not str(engineer.get("credential", "")).strip():
                raise ValueError(f"Released pack {release_id} needs the approving engineer name and credential.")
            if not _parse_time(release.get("approved_at")):
                raise ValueError(f"Released pack {release_id} needs an ISO approval date.")
        expiry = release.get("expiry", "")
        if expiry and not _parse_time(expiry):
            raise ValueError(f"Release {release_id} has an invalid expiry date.")
        scope = release.get("scope") or {}
        if not isinstance(scope, dict):
            raise ValueError(f"Release {release_id} scope must be an object.")
        records, record_ids = [], set()
        for record in release.get("records", []):
            if not isinstance(record, dict):
                raise ValueError(f"Release {release_id} record must be an object.")
            record_id = str(record.get("record_id", "")).strip()
            content_hash = str(record.get("content_hash", "")).strip()
            if not record_id or not content_hash or record_id in record_ids:
                raise ValueError(f"Release {release_id} records need unique record_id and content_hash values.")
            records.append({"record_id": record_id, "content_hash": content_hash})
            record_ids.add(record_id)
        if status == "released" and not records:
            raise ValueError(f"Released pack {release_id} must list at least one approved record hash.")
        releases.append({
            "release_id": release_id, "pack_version": pack_version, "status": status,
            "engineer": {"name": str(engineer.get("name", "")).strip(), "credential": str(engineer.get("credential", "")).strip()},
            "approved_at": str(release.get("approved_at", "")), "approval_reference": str(release.get("approval_reference", "")).strip(),
            "scope": deepcopy(scope), "expiry": str(expiry), "records": records,
        })
        release_ids.add(release_id)
    result = {"schema_version": 1, "releases": releases}
    result["fingerprint"] = fingerprint(result)
    return result


def source_pack_release_manifest(path=None):
    try:
        raw = json.loads((path or _release_manifest_path()).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = empty_source_pack_release_manifest()
    return validate_source_pack_release_manifest(raw)


def _allowed_domain(url, pack):
    host = (urlparse(str(url)).hostname or "").lower()
    return any(host == domain or host.endswith("." + domain) for domain in pack.get("allowed_domains", []))


def source_domain_allowed(url, pack_version):
    """Whether a source URL is allowed for the named reusable source pack."""
    return bool(approved_source_pack(pack_version) and _allowed_domain(url, approved_source_pack(pack_version)))


def _scope_matches(record_scope, requested):
    """A record can be broader than a project, but may not contradict it."""
    for key, value in (requested or {}).items():
        if value in (None, ""):
            continue
        if key in record_scope and record_scope[key] not in (None, "", value):
            return False
    return True


def source_record_release_status(cache, record, scope=None, now=None, release_manifest=None):
    """Explain whether a cited record can supply an automatic default."""
    now = now or datetime.now(timezone.utc)
    pack_version = cache.get("source_pack_version", "")
    pack = approved_source_pack(pack_version)
    if not pack:
        return {"eligible": False, "status": "pack_missing", "reason": "source-pack version is not configured"}
    if record.get("source_pack_version") != pack_version:
        return {"eligible": False, "status": "pack_mismatch", "reason": "record uses a different source-pack version"}
    if record.get("review_status") != "approved":
        return {"eligible": False, "status": "record_not_approved", "reason": f"record is {record.get('review_status') or 'not approved'}"}
    if not _allowed_domain(record.get("url", ""), pack):
        return {"eligible": False, "status": "domain_not_allowlisted", "reason": "record source domain is not allowlisted"}
    expiry = _parse_time(record.get("expiry"))
    if expiry and expiry <= now:
        return {"eligible": False, "status": "record_expired", "reason": "record has expired"}
    manifest = release_manifest or source_pack_release_manifest()
    matches = []
    for release in manifest["releases"]:
        if release["pack_version"] != pack_version or release["status"] != "released":
            continue
        release_expiry = _parse_time(release.get("expiry"))
        if release_expiry and release_expiry <= now:
            continue
        if not _scope_matches(release.get("scope", {}), scope or {}):
            continue
        if any(item["record_id"] == record.get("record_id") and item["content_hash"] == record.get("content_hash") for item in release["records"]):
            matches.append(release)
    if not matches:
        return {"eligible": False, "status": "awaiting_engineering_release", "reason": "record is not in a current engineer-released source pack"}
    release = sorted(matches, key=lambda row: (row.get("approved_at", ""), row["release_id"]), reverse=True)[0]
    return {"eligible": True, "status": "released", "reason": "engineer-released source-pack record", "release": deepcopy(release)}


def released_source_record(cache, record, scope=None, now=None, release_manifest=None):
    """Whether a record may supply an automatic default, not merely be stored."""
    return source_record_release_status(cache, record, scope, now, release_manifest)["eligible"]


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
    if parsed_url.scheme != "https" or not parsed_url.hostname:
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
    if not isinstance(result.get("scope"), dict):
        raise ValueError("Research record scope must be an object.")
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
        checked_bindings.append({"target": target, "value": deepcopy(binding["value"]), "unit": str(binding.get("unit", result["unit"])).strip(), "scope": deepcopy(binding_scope)})
    result["bindings"] = checked_bindings
    return result


def _validate_profile(values, label):
    if not isinstance(values, list) or len(values) != 24:
        raise ValueError(f"{label} must contain exactly 24 hourly values.")
    if any(not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0 or value > 1 for value in values):
        raise ValueError(f"{label} values must be numeric fractions from 0 to 1.")


def _validate_weather_profile(value):
    if not isinstance(value, dict):
        raise ValueError("Weather candidate must contain a profile object.")
    hours = value.get("hours")
    if not isinstance(hours, list) or len(hours) != 24:
        raise ValueError("Weather candidate must contain 24 hourly conditions.")
    hour_ids = {row.get("hour") for row in hours if isinstance(row, dict)}
    if hour_ids != set(range(24)):
        raise ValueError("Weather candidate hours must contain each hour 0 through 23 exactly once.")
    for row in hours:
        if not isinstance(row.get("outdoor_dry_bulb_c"), (int, float)) or not isinstance(row.get("outdoor_wet_bulb_c"), (int, float)):
            raise ValueError("Weather candidate hours need numeric dry-bulb and wet-bulb values.")
    if not isinstance(value.get("atmospheric_pressure_kpa"), (int, float)) or value["atmospheric_pressure_kpa"] <= 0:
        raise ValueError("Weather candidate needs a positive atmospheric pressure in kPa.")


def validate_candidate_record(record):
    """Validate a reusable Australia-first automatic-default candidate.

    This is stricter than general research-cache validation. It rejects values
    that could otherwise look cited but target a high-risk calculator input.
    """
    result = validate_record(record)
    if result.get("scope", {}).get("country") != "AU":
        raise ValueError(f"Candidate {result['record_id']} must declare country scope AU.")
    allowed = CANDIDATE_BINDING_POLICY.get(result["category"], {})
    if not allowed:
        raise ValueError(f"Category {result['category']} is not eligible for automatic default candidates.")
    if not result["bindings"]:
        raise ValueError(f"Candidate {result['record_id']} needs at least one target binding.")
    for binding in result["bindings"]:
        target = binding["target"]
        units = allowed.get(target)
        if units is None:
            raise ValueError(f"Candidate {result['record_id']} target {target} is not an allowed low-risk default.")
        if binding["unit"] not in units:
            raise ValueError(f"Candidate {result['record_id']} target {target} has invalid unit {binding['unit']!r}.")
        if result["category"] == "schedule_default":
            if not binding.get("scope", {}).get("day_type"):
                raise ValueError(f"Schedule candidate {result['record_id']} needs a day_type scope.")
            _validate_profile(binding["value"], f"Schedule candidate {result['record_id']}")
        if result["category"] == "weather":
            scope = {**result.get("scope", {}), **binding.get("scope", {})}
            if not scope.get("scenario") or not any(scope.get(key) for key in ("locality", "state", "climate_zone")):
                raise ValueError(f"Weather candidate {result['record_id']} needs scenario and locality, state, or climate-zone scope.")
            _validate_weather_profile(binding["value"])
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


def eligible_bindings(cache, target, scope=None, now=None, release_manifest=None):
    """Return approved, unexpired target-specific records for deterministic use.

    Bindings make a source record useful only for fields it explicitly declares;
    a generic occupancy or weather record can never be guessed onto a field.
    """
    cache = validate_cache(cache)
    now = now or datetime.now(timezone.utc)
    result = []
    for record in cache["records"]:
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
            release_status = source_record_release_status(cache, record, scope, now, release_manifest)
            if not release_status["eligible"]:
                continue
            item = deepcopy(record)
            item.update({
                "value": deepcopy(binding["value"]), "unit": binding.get("unit", record["unit"]),
                "binding_target": target, "scope": combined_scope,
                "source_pack_release": deepcopy(release_status.get("release", {})),
            })
            result.append(item)
    return sorted(result, key=lambda row: row["record_id"])


def default_record_statuses(cache, scope=None, now=None, release_manifest=None):
    """Return UI/audit status for every stored candidate without enabling it."""
    checked = validate_cache(cache)
    return [
        {
            "record_id": record["record_id"], "category": record["category"],
            "review_status": record.get("review_status", ""), "scope": deepcopy(record.get("scope") or {}),
            "publisher": record.get("publisher", ""), "citation": record.get("citation", ""),
            "binding_targets": [row.get("target", "") for row in record.get("bindings", [])],
            **source_record_release_status(checked, record, scope, now, release_manifest),
        }
        for record in checked["records"]
    ]


def upsert_record(cache, record):
    result = validate_cache(cache)
    record = validate_record(record)
    result["records"] = [row for row in result["records"] if row["record_id"] != record["record_id"]] + [record]
    result["revision"] = int(result.get("revision", 0)) + 1
    result["updated_at"] = timestamp()
    result["fingerprint"] = fingerprint({"revision": result["revision"], "source_pack_version": result.get("source_pack_version", ""), "records": result["records"]})
    return result
