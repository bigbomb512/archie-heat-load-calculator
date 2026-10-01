"""Draft-only resolution of licensed Australian HVAC design-weather profiles.

This module deliberately keeps licensed source-pack data outside projects.  A
project artifact stores only the selected, cited design profile and enough
metadata to reproduce the selection; it never stores the source spreadsheet.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from urllib.parse import urlparse

from ai.annual_energy import _wet_bulb_from_dew_point


SCHEMA_VERSION = 1
BASES = {"comfort", "critical"}
SCENARIOS = {"cooling", "heating"}
STATUSES = {"awaiting_location", "awaiting_design_basis", "draft_ready", "needs_review", "blocked"}


def now():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()


def _clean_for_fingerprint(value):
    if isinstance(value, dict):
        return {key: _clean_for_fingerprint(item) for key, item in value.items()
                if key not in {"updated_at", "retrieved_at"}}
    if isinstance(value, list):
        return [_clean_for_fingerprint(item) for item in value]
    return value


def _text(value):
    return str(value or "").strip()


def _number(value, label, low=None, high=None):
    if isinstance(value, bool):
        raise ValueError(f"{label} must be numeric.")
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be numeric.") from error
    if not math.isfinite(result) or (low is not None and result < low) or (high is not None and result > high):
        raise ValueError(f"{label} is outside its valid range.")
    return result


def _time(value, label):
    try:
        result = datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{label} must be an ISO timestamp.") from error
    return result if result.tzinfo else result.replace(tzinfo=timezone.utc)


def _empty_mode():
    return {"candidates": [], "selected": {}, "selection": "", "conflicts": []}


def empty_site_design_weather_resolution():
    result = {
        "schema_version": SCHEMA_VERSION,
        "status": "awaiting_location",
        "location_fingerprint": "",
        "design_basis": "",
        "pack": {},
        "cooling": _empty_mode(),
        "heating": _empty_mode(),
        "stale_reasons": [],
        "updated_at": "",
    }
    result["fingerprint"] = fingerprint(_clean_for_fingerprint(result))
    return result


def _compatible_scope(scope, location):
    if not isinstance(scope, dict) or _text(scope.get("country")).upper() != "AU":
        return None
    points = 0
    for key, weight in (("locality", 3), ("climate_zone", 2), ("state", 1)):
        wanted, found = _text(location.get(key)).casefold(), _text(scope.get(key)).casefold()
        if found and wanted:
            if found != wanted:
                return None
            points += weight
    return points


def _distance_km(location, scope):
    try:
        latitude = float(location["latitude_deg"])
        longitude = float(location["longitude_deg"])
        station_latitude = float(scope.get("station_latitude_deg", scope.get("latitude_deg")))
        station_longitude = float(scope.get("station_longitude_deg", scope.get("longitude_deg")))
    except (KeyError, TypeError, ValueError):
        return None
    radius = 6371.0088
    lat1, lon1, lat2, lon2 = map(math.radians, (latitude, longitude, station_latitude, station_longitude))
    return round(radius * 2 * math.asin(math.sqrt(math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)), 3)


def _normalise_profile(raw, record_id):
    if not isinstance(raw, dict):
        return None, {}
    from ai.heat_loads import WET_BULB_BASES, wet_bulb_method_id

    hours = raw.get("hours")
    if not isinstance(hours, list) or len(hours) != 24:
        return None, {}
    pressure = _number(raw.get("atmospheric_pressure_kpa", raw.get("pressure_kpa")), f"{record_id} atmospheric pressure", 50, 120)
    identifiers = set()
    rows, conversion = [], {}
    for index, item in enumerate(hours):
        if not isinstance(item, dict):
            raise ValueError(f"{record_id} hour {index} must be an object.")
        hour = item.get("hour", index)
        if isinstance(hour, bool) or not isinstance(hour, int) or hour not in range(24) or hour in identifiers:
            raise ValueError(f"{record_id} must contain distinct hours 0 through 23.")
        identifiers.add(hour)
        db = _number(item.get("outdoor_dry_bulb_c", item.get("db")), f"{record_id} hour {hour} dry-bulb", -100, 100)
        wb_raw = item.get("outdoor_wet_bulb_c", item.get("wb"))
        dew_raw = item.get("outdoor_dew_point_c", item.get("dew_point_c"))
        if wb_raw in (None, "") and dew_raw in (None, ""):
            raise ValueError(f"{record_id} hour {hour} needs wet-bulb or dew point.")
        dew = None
        if dew_raw not in (None, ""):
            dew = _number(dew_raw, f"{record_id} hour {hour} dew point", -100, 100)
            if dew > db:
                raise ValueError(f"{record_id} hour {hour} dew point cannot exceed dry-bulb.")
        if wb_raw in (None, ""):
            if not _text(raw.get("psychrometric_method_citation")):
                raise ValueError(f"{record_id} needs a cited psychrometric method when deriving wet-bulb from dew point.")
            wb = _wet_bulb_from_dew_point(db, dew, pressure)
            basis = "thermodynamic" if dew >= 0 else "legacy_unverified"
            conversion[str(hour)] = {"method": "dew_point_to_wet_bulb_psychrometric_bisection_v1", "citation": _text(raw.get("psychrometric_method_citation")), "operands": {"dry_bulb_c": db, "dew_point_c": dew, "pressure_kpa": pressure}, "result_wet_bulb_c": wb}
            conversion[str(hour)]["wet_bulb_basis"] = basis
            conversion[str(hour)]["wet_bulb_method_id"] = wet_bulb_method_id(basis)
        else:
            wb = _number(wb_raw, f"{record_id} hour {hour} wet-bulb", -100, 100)
            if wb > db:
                raise ValueError(f"{record_id} hour {hour} wet-bulb cannot exceed dry-bulb.")
        basis = item.get("outdoor_wet_bulb_basis", item.get("wet_bulb_basis", "legacy_unverified"))
        if basis not in WET_BULB_BASES:
            raise ValueError(f"{record_id} hour {hour} has an invalid outdoor wet-bulb basis.")
        if wb_raw in (None, ""):
            basis = conversion[str(hour)]["wet_bulb_basis"]
        row = {"hour": hour, "db": db, "wb": wb, "wet_bulb_basis": basis}
        if dew is not None:
            row["dew_point_c"] = dew
        if item.get("outdoor_relative_humidity_percent", item.get("relative_humidity_percent")) not in (None, ""):
            row["relative_humidity_percent"] = _number(item.get("outdoor_relative_humidity_percent", item.get("relative_humidity_percent")), f"{record_id} hour {hour} humidity", 0, 100)
        rows.append(row)
    if identifiers != set(range(24)):
        raise ValueError(f"{record_id} must contain each hour from 0 through 23 exactly once.")
    return {"hours": sorted(rows, key=lambda item: item["hour"]), "pressure_kpa": pressure,
            "representative_month": _text(raw.get("representative_month")), "weather_day_type": _text(raw.get("weather_day_type")) or "design_day"}, conversion


def validate_licensed_airah_pack(raw):
    """Validate a protected admin-imported AIRAH DA09 pack without publishing it."""
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError("Licensed AIRAH pack must use schema version 1.")
    pack = raw.get("pack", raw)
    if not isinstance(pack, dict):
        raise ValueError("Licensed AIRAH pack must contain a pack object.")
    attestation = pack.get("licence_attestation", {})
    release = pack.get("release", {})
    if not _text(pack.get("pack_id")) or not _text(pack.get("pack_version")):
        raise ValueError("Licensed AIRAH pack needs an ID and version.")
    if not isinstance(attestation, dict) or not _text(attestation.get("reference")) or not _text(attestation.get("authorised_by")):
        raise ValueError("Licensed AIRAH pack needs an administrator licence attestation.")
    if not isinstance(release, dict) or release.get("status") != "released" or not _text(release.get("approval_reference")) or not _text((release.get("engineer") or {}).get("name")) or not _text((release.get("engineer") or {}).get("credential")):
        raise ValueError("Licensed AIRAH pack needs a current engineer-released approval.")
    _time(release.get("approved_at"), "Licensed AIRAH pack approval date")
    _time(release.get("expiry"), "Licensed AIRAH pack expiry")
    if _time(release["expiry"], "Licensed AIRAH pack expiry") <= datetime.now(timezone.utc):
        raise ValueError("Licensed AIRAH pack release has expired.")
    records, seen = [], set()
    for index, raw_record in enumerate(pack.get("records", []), start=1):
        if not isinstance(raw_record, dict):
            raise ValueError(f"Licensed AIRAH record {index} must be an object.")
        record_id, scenario, basis = _text(raw_record.get("record_id")), _text(raw_record.get("scenario")).casefold(), _text(raw_record.get("basis")).casefold()
        if not record_id or record_id in seen or scenario not in SCENARIOS or basis not in BASES:
            raise ValueError(f"Licensed AIRAH record {index} needs a unique ID plus cooling/heating and comfort/critical values.")
        scope = deepcopy(raw_record.get("scope", {}))
        if not isinstance(scope, dict) or _text(scope.get("country")).upper() != "AU" or not any(_text(scope.get(key)) for key in ("locality", "state", "climate_zone")):
            raise ValueError(f"Licensed AIRAH record {record_id} needs Australian locality, state, or climate-zone scope.")
        if not _text(raw_record.get("citation")):
            raise ValueError(f"Licensed AIRAH record {record_id} needs a citation.")
        source_reference = _text(raw_record.get("source_reference"))
        host = (urlparse(source_reference).hostname or "").casefold()
        if not source_reference.startswith("https://") or not (host == "airah.org.au" or host.endswith(".airah.org.au")):
            raise ValueError(f"Licensed AIRAH record {record_id} needs an AIRAH HTTPS source reference.")
        expiry = _time(raw_record.get("expiry", release["expiry"]), f"Licensed AIRAH record {record_id} expiry")
        if expiry <= datetime.now(timezone.utc):
            raise ValueError(f"Licensed AIRAH record {record_id} has expired.")
        profile, conversion = _normalise_profile(raw_record.get("profile"), record_id) if raw_record.get("profile") else (None, {})
        point = deepcopy(raw_record.get("design_point", {}))
        if profile is None and not point:
            raise ValueError(f"Licensed AIRAH record {record_id} needs a 24-hour profile or a visible point-only design condition.")
        records.append({
            "record_id": record_id, "scenario": scenario, "basis": basis, "scope": scope,
            "content_hash": _text(raw_record.get("content_hash")) or fingerprint({key: raw_record.get(key) for key in ("record_id", "scenario", "basis", "scope", "profile", "design_point")}),
            "publisher": _text(raw_record.get("publisher")) or "AIRAH", "citation": _text(raw_record.get("citation")),
            "source_reference": source_reference, "station_reference": _text(raw_record.get("station_reference")),
            "release_version": _text(raw_record.get("release_version")) or _text(pack.get("pack_version")),
            "expiry": expiry.isoformat(), "profile": profile, "conversion": conversion,
            "design_point": point, "calculation_eligible": profile is not None,
        })
        seen.add(record_id)
    if not records:
        raise ValueError("Licensed AIRAH pack needs at least one record.")
    result = {"schema_version": 1, "pack": {"pack_id": _text(pack["pack_id"]), "pack_version": _text(pack["pack_version"]),
              "licence_attestation": {"reference": _text(attestation["reference"]), "authorised_by": _text(attestation["authorised_by"])},
              "release": {"status": "released", "engineer": {"name": _text(release["engineer"]["name"]), "credential": _text(release["engineer"]["credential"])}, "approved_at": _time(release["approved_at"], "Licensed AIRAH pack approval date").isoformat(), "approval_reference": _text(release["approval_reference"]), "expiry": _time(release["expiry"], "Licensed AIRAH pack expiry").isoformat()},
              "records": records}}
    result["fingerprint"] = fingerprint(result)
    return result


def validate_site_design_weather_resolution(raw):
    source = deepcopy(raw or empty_site_design_weather_resolution())
    if not isinstance(source, dict) or source.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported site design-weather resolution schema.")
    result = empty_site_design_weather_resolution()
    result.update({key: source.get(key, result[key]) for key in result if key not in {"cooling", "heating", "fingerprint", "updated_at"}})
    result["design_basis"] = _text(result["design_basis"]).casefold()
    if result["design_basis"] and result["design_basis"] not in BASES:
        raise ValueError("Design-weather basis must be comfort or critical.")
    result["location_fingerprint"] = _text(result["location_fingerprint"])
    result["pack"] = deepcopy(result["pack"]) if isinstance(result["pack"], dict) else {}
    for mode in SCENARIOS:
        data = source.get(mode, _empty_mode())
        if not isinstance(data, dict):
            raise ValueError(f"{mode.capitalize()} design-weather data must be an object.")
        candidates = [deepcopy(row) for row in data.get("candidates", []) if isinstance(row, dict)]
        selected = deepcopy(data.get("selected", {})) if isinstance(data.get("selected", {}), dict) else {}
        result[mode] = {"candidates": candidates, "selected": selected, "selection": _text(data.get("selection")),
                        "conflicts": [deepcopy(row) for row in data.get("conflicts", []) if isinstance(row, dict)]}
    result["stale_reasons"] = [_text(item) for item in source.get("stale_reasons", []) if _text(item)]
    result["status"] = _text(source.get("status")) or "awaiting_location"
    if result["status"] not in STATUSES:
        raise ValueError("Invalid site design-weather resolution status.")
    result["updated_at"] = now()
    result["fingerprint"] = fingerprint(_clean_for_fingerprint(result))
    return result


def set_design_basis(current, basis, location):
    result = validate_site_design_weather_resolution(current)
    if not _text((location or {}).get("fingerprint")) or not (location or {}).get("location", {}).get("latitude_deg"):
        raise ValueError("Resolve and confirm the project location before selecting design weather.")
    basis = _text(basis).casefold()
    if basis not in BASES:
        raise ValueError("Select comfort or critical design conditions.")
    result = empty_site_design_weather_resolution()
    result["location_fingerprint"] = _text(location.get("fingerprint"))
    result["design_basis"] = basis
    result["status"] = "awaiting_design_basis"
    return validate_site_design_weather_resolution(result)


def _candidate(record, location):
    scope_score = _compatible_scope(record["scope"], location)
    if scope_score is None:
        return None
    distance = _distance_km(location, record["scope"])
    return {**deepcopy(record), "scope_specificity": scope_score, "distance_km": distance,
            "selection_rationale": "Matches confirmed Australian location, requested scenario, selected design basis, and current engineer-released licensed pack."}


def resolve_candidates(current, location, licensed_pack):
    result = validate_site_design_weather_resolution(current)
    if not _text((location or {}).get("fingerprint")) or not (location or {}).get("location", {}).get("latitude_deg"):
        raise ValueError("Resolve and confirm the project location before resolving design-weather candidates.")
    if not result["design_basis"]:
        raise ValueError("Select the project comfort or critical design basis first.")
    pack = validate_licensed_airah_pack(licensed_pack)
    result["location_fingerprint"] = _text(location["fingerprint"])
    result["pack"] = {"pack_id": pack["pack"]["pack_id"], "pack_version": pack["pack"]["pack_version"], "fingerprint": pack["fingerprint"],
                      "release": deepcopy(pack["pack"]["release"])}
    needs_review = False
    for mode in SCENARIOS:
        rows = [_candidate(record, location["location"]) for record in pack["pack"]["records"]
                if record["scenario"] == mode and record["basis"] == result["design_basis"]]
        rows = [row for row in rows if row is not None]
        rows.sort(key=lambda row: (-row["scope_specificity"], row["distance_km"] is None, row["distance_km"] if row["distance_km"] is not None else float("inf"), row["release_version"], row["record_id"]))
        eligible = [row for row in rows if row["calculation_eligible"]]
        conflicts = []
        selected, selection = {}, ""
        if eligible:
            first = eligible[0]
            tied = [row for row in eligible if (row["scope_specificity"], row["distance_km"] is None, row["distance_km"] if row["distance_km"] is not None else float("inf"), row["release_version"]) == (first["scope_specificity"], first["distance_km"] is None, first["distance_km"] if first["distance_km"] is not None else float("inf"), first["release_version"])]
            profiles = {fingerprint(row["profile"]) for row in tied}
            if len(tied) > 1 and len(profiles) > 1:
                conflicts.append({"code": "equally_ranked_design_weather_profiles", "message": "Equally ranked design-weather profiles differ; choose one explicitly.", "candidate_ids": [row["record_id"] for row in tied]})
                needs_review = True
            else:
                selected, selection = deepcopy(first), "auto_selected"
        elif rows:
            conflicts.append({"code": "point_only_design_weather", "message": "Only point-only conditions matched. An engineer-released 24-hour profile is required for hourly calculation.", "candidate_ids": [row["record_id"] for row in rows]})
            needs_review = True
        else:
            conflicts.append({"code": "no_eligible_design_weather", "message": "No current licensed AIRAH profile matches this location, scenario, and design basis.", "candidate_ids": []})
            needs_review = True
        result[mode] = {"candidates": rows, "selected": selected, "selection": selection, "conflicts": conflicts}
    result["stale_reasons"] = []
    result["status"] = "needs_review" if needs_review else "draft_ready"
    return validate_site_design_weather_resolution(result)


def select_candidate(current, mode, record_id):
    result = validate_site_design_weather_resolution(current)
    mode, record_id = _text(mode).casefold(), _text(record_id)
    if mode not in SCENARIOS:
        raise ValueError("Design-weather selection must target cooling or heating.")
    candidate = next((row for row in result[mode]["candidates"] if row.get("record_id") == record_id), None)
    if not candidate or not candidate.get("calculation_eligible"):
        raise ValueError("Choose a current 24-hour licensed design-weather candidate.")
    result[mode]["selected"] = deepcopy(candidate)
    result[mode]["selection"] = "contractor_selected"
    result[mode]["conflicts"] = []
    result["status"] = "draft_ready" if all(result[item]["selected"] for item in SCENARIOS) else "needs_review"
    return validate_site_design_weather_resolution(result)


def clear_selection(current, mode):
    result = validate_site_design_weather_resolution(current)
    mode = _text(mode).casefold()
    if mode not in SCENARIOS:
        raise ValueError("Design-weather selection must target cooling or heating.")
    result[mode]["selected"] = {}
    result[mode]["selection"] = ""
    result["status"] = "needs_review"
    return validate_site_design_weather_resolution(result)
