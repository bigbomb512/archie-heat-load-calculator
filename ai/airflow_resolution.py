"""Draft-only resolution of ventilation, infiltration, and process air paths.

This module is an evidence adapter.  It does not calculate psychrometrics or
replace the reviewed ventilation/infiltration engines; it materialises traced
airflow inputs for the isolated AI-preliminary cooling model.
"""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import re


SCHEMA_VERSION = 1
AIR_PATH_TYPES = {"outside_air", "infiltration", "process_exhaust", "make_up_air", "transfer_air", "supply", "return", "relief"}
STATUSES = {"resolved", "provisional", "needs_review", "blocked", "excluded", "stale"}
ORIGINS = {"contractor_override", "project_evidence", "released_source_pack", "research_candidate", "controlled_fallback", "unresolved"}
DAY_TYPES = ("weekday", "saturday", "sunday", "holiday")


def now():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def empty_airflow_resolution():
    result = {"schema_version": SCHEMA_VERSION, "records": [], "issues": [], "overrides": [], "research_jobs": [], "source_fingerprints": {}, "status": "needs_review", "updated_at": ""}
    result["fingerprint"] = fingerprint(result)
    return result


def _text(value):
    return str(value or "").strip()


def _number(value, positive=False):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or (positive and value <= 0):
        return None
    return value


def _confidence(value, fallback=0.4):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
        return max(0.0, min(1.0, float(value)))
    return {"high": .85, "medium": .65, "low": .4}.get(_text(value).casefold(), fallback)


def confidence_band(value):
    return "high" if value >= .8 else "medium" if value >= .5 else "low"


def room_id(label, level):
    slug = lambda value: re.sub(r"[^a-z0-9]+", "-", _text(value).casefold()).strip("-") or "unassigned"
    return f"room:{slug(level)}:{slug(label)}"


def _evidence(item):
    if not isinstance(item, dict):
        return []
    raw = item.get("evidence", item.get("citations", []))
    if isinstance(raw, dict):
        raw = [raw]
    rows = [deepcopy(row) for row in raw if isinstance(row, dict) and row.get("page")]
    if not rows and item.get("page"):
        rows = [{"page": item.get("page"), "drawing_number": _text(item.get("drawing_number")),
                 "excerpt": _text(item.get("excerpt", item.get("label", "")))}]
    return rows


def _profile(pack, profile_id):
    profiles = (pack or {}).get("profiles", {}) if isinstance(pack, dict) else {}
    return profiles.get(profile_id) or profiles.get("generic_conditioned_room", {})


def _room_rows(building, proposal, room_use):
    grouped = {}
    for source in ((building or {}).get("spaces", []) if isinstance(building, dict) else []):
        if isinstance(source, dict) and _text(source.get("name")):
            label, level = _text(source.get("name")), _text(source.get("level_name", "Unassigned level")) or "Unassigned level"
            grouped.setdefault(room_id(label, level), {"label": label, "level": level, "sources": []})["sources"].append(source)
    for source in ((proposal or {}).get("rooms", []) if isinstance(proposal, dict) else []):
        if isinstance(source, dict) and _text(source.get("label")):
            label, level = _text(source.get("label")), _text(source.get("level_name", "Unassigned level")) or "Unassigned level"
            grouped.setdefault(room_id(label, level), {"label": label, "level": level, "sources": []})["sources"].append(source)
    for row in (room_use or {}).get("records", []) if isinstance(room_use, dict) else []:
        if isinstance(row, dict) and _text(row.get("original_label", row.get("label"))):
            label, level = _text(row.get("original_label", row.get("label"))), _text(row.get("level", row.get("level_name", "Unassigned level"))) or "Unassigned level"
            grouped.setdefault(room_id(label, level), {"label": label, "level": level, "sources": []})
    return [grouped[key] for key in sorted(grouped)]


def _first(sources, keys):
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in keys:
            value = source.get(key)
            if value not in (None, ""):
                return value, source
    return None, None


def _origin(source, fallback="unresolved"):
    if not isinstance(source, dict):
        return fallback
    raw = _text(source.get("origin", ""))
    if raw in {"contractor_override", "project_evidence", "released_source_pack", "research_candidate", "controlled_fallback"}:
        return raw
    if source.get("research_candidate"):
        return "research_candidate"
    if source.get("override"):
        return "contractor_override"
    if _evidence(source) or _text(source.get("source")) or _text(source.get("source_id")):
        return "project_evidence"
    return fallback


def _record_id(path_type, owner, source, anchor=""):
    return "airflow_" + fingerprint([path_type, owner, source, anchor])[:18]


def _schedule(profile_id, supplied=None):
    active = {"retail": range(9, 18), "office": range(8, 18), "hospitality": range(10, 23),
              "storage": range(7, 18), "residential": range(6, 23)}.get(profile_id, range(8, 18))
    default = [1.0 if hour in active else 0.0 for hour in range(24)]
    result = {}
    for day in DAY_TYPES:
        values = supplied.get(day) if isinstance(supplied, dict) else None
        if not isinstance(values, list) or len(values) != 24 or any(_number(value) is None or not 0 <= float(value) <= 1 for value in values):
            values = default
        result[day] = [float(value) for value in values]
    return result


def _field_record(value, unit, origin, confidence, source, evidence, rationale, formula="", operands=None, source_id=""):
    return {"value": value, "unit": unit, "origin": origin, "confidence": round(confidence, 4),
            "confidence_band": confidence_band(confidence), "source_id": source_id,
            "source": source, "evidence": deepcopy(evidence), "rationale": rationale,
            "formula": formula, "operands": deepcopy(operands or {})}


def _make_record(path_type, owner, source, *, value=None, unit="L/s", origin="unresolved", confidence=.0,
                 evidence=None, rationale="", formula="", operands=None, schedule=None, status=None,
                 system_owner="", conditioning_owner="", source_id="", conflicts=None, unresolved=None,
                 assumptions=None, extra=None):
    evidence = evidence or []
    unresolved = list(unresolved or [])
    if value is None:
        unresolved.append("airflow value is unresolved")
    if path_type not in AIR_PATH_TYPES:
        unresolved.append("unsupported airflow path type")
    if origin not in ORIGINS:
        origin = "unresolved"
    if status is None:
        status = "resolved" if value is not None and origin in {"contractor_override", "project_evidence", "released_source_pack"} else "provisional" if value is not None else "excluded"
    item = {
        "airflow_id": _record_id(path_type, owner, source, (source_id or rationale) + "|" + system_owner),
        "air_path_type": path_type, "owner_room_id": owner, "owner_zone_id": "", "owner_floor_id": "",
        "ahu_id": system_owner, "plant_id": "", "conditioning_owner": conditioning_owner,
        "direction": "", "value": value, "unit": unit, "schedule": schedule or _schedule("generic_conditioned_room"),
        "source": source, "source_id": source_id, "evidence": deepcopy(evidence),
        "confidence": round(confidence, 4), "confidence_band": confidence_band(confidence),
        "origin": origin, "status": status, "rationale": rationale, "assumptions": list(assumptions or []),
        "conflicts": list(conflicts or []), "unresolved_fields": sorted(set(unresolved)),
        "formula": formula, "operands": deepcopy(operands or {}), "source_fingerprints": {},
    }
    if extra:
        item.update(deepcopy(extra))
    item["resolution_fingerprint"] = fingerprint({key: value for key, value in item.items() if key != "resolution_fingerprint"})
    return item


def _room_context(building, proposal, room, room_use, ceiling, pack):
    rid = room_id(room["label"], room["level"])
    sources = list(room.get("sources", []))
    use = next((row for row in (room_use or {}).get("records", [])
                if row.get("room_id") == rid
                or (_text(row.get("original_label", row.get("label"))).casefold() == room["label"].casefold()
                    and _text(row.get("level", row.get("level_name"))).casefold() == room["level"].casefold())), {})
    if use:
        sources.append(use)
    record = next((row for row in (ceiling or {}).get("records", []) if row.get("room_id") == rid), {})
    area, area_source = _first(sources, ("area_m2", "room_area_m2", "area"))
    area = _number(area, positive=True)
    volume = _number(record.get("volume_m3"), positive=True) if isinstance(record, dict) else None
    if volume is None and area is not None:
        height = _number(record.get("height_mm", record.get("ceiling_height_mm")), positive=True) if isinstance(record, dict) else None
        if height is not None:
            volume = area * height / 1000
    profile_id = _text(use.get("preliminary_profile_id", use.get("profile_id", "generic_conditioned_room"))) or "generic_conditioned_room"
    profile = _profile(pack, profile_id)
    return rid, sources, use, record, area, volume, profile_id, profile, area_source


def _existing_overrides(existing):
    """Collect path-specific overrides, including legacy record-level copies."""
    overrides = {}
    for record in existing.get("records", []) if isinstance(existing, dict) else []:
        if not isinstance(record, dict) or not isinstance(record.get("overrides"), dict):
            continue
        key = (_text(record.get("owner_room_id")), _text(record.get("air_path_type")))
        details = record["overrides"]
        value = _number(details.get("value", record.get("value")), positive=True)
        if key[0] and key[1] in AIR_PATH_TYPES and value is not None:
            overrides[key] = {"room_id": key[0], "path_type": key[1], **deepcopy(details), "value": value}
    for item in existing.get("overrides", []) if isinstance(existing, dict) else []:
        if not isinstance(item, dict):
            continue
        key = (_text(item.get("room_id")), _text(item.get("path_type")))
        value = _number(item.get("value"), positive=True)
        if key[0] and key[1] in AIR_PATH_TYPES and value is not None:
            overrides[key] = {**deepcopy(item), "room_id": key[0], "path_type": key[1], "value": value}
    return overrides


def _apply_override(record, override):
    record["value"] = override["value"]
    record["origin"] = "contractor_override"
    record["status"] = "resolved"
    record["overrides"] = {key: deepcopy(override.get(key, "")) for key in ("reviewer", "note", "updated_at")}
    record["overrides"]["value"] = override["value"]
    record["unresolved_fields"] = [
        item for item in record.get("unresolved_fields", [])
        if "airflow value" not in item and "override cleared" not in item
    ]


def _mark_stale_orphan(record, override, source_fingerprints):
    row = deepcopy(record)
    _apply_override(row, override)
    row["status"] = "stale"
    row["stale_reason"] = "The room or airflow path is no longer present in the current proposal."
    row["rationale"] = "Retained engineer override for a room/path absent from the current proposal; review before reuse."
    row["remediation"] = "Confirm the room and air path still apply, then resolve it from current evidence."
    row["unresolved_fields"] = sorted(set(row.get("unresolved_fields", []) + [row["stale_reason"]]))
    row["source_fingerprints"] = deepcopy(source_fingerprints or {})
    row["resolution_fingerprint"] = fingerprint({key: value for key, value in row.items() if key != "resolution_fingerprint"})
    return row


def resolve(building, vision, proposal, room_use, ceiling_volume, pack, source_fingerprints, existing=None):
    """Resolve draft air paths from the current AI/PDF context."""
    existing = existing if isinstance(existing, dict) else empty_airflow_resolution()
    proposal = proposal if isinstance(proposal, dict) else {"rooms": []}
    source_fingerprints = deepcopy(source_fingerprints or {})
    source_fingerprints["preliminary_pack"] = fingerprint(pack)
    records, issues = [], []
    overrides = _existing_overrides(existing)
    previous_records = {
        (_text(row.get("owner_room_id")), _text(row.get("air_path_type"))): row
        for row in existing.get("records", []) if isinstance(row, dict)
    }
    for room in _room_rows(building, proposal, room_use):
        rid, sources, use, ceiling, area, volume, profile_id, profile, area_source = _room_context(building, proposal, room, room_use, ceiling_volume, pack)
        evidence = next((value for value in (_evidence(source) for source in sources) if value), [])
        confidence = _confidence(use.get("confidence_score", use.get("confidence", .4))) if isinstance(use, dict) else .4
        schedule = _schedule(profile_id, next((source.get("schedules") for source in sources if isinstance(source, dict) and source.get("schedules")), None))

        people, people_source = _first(sources, ("occupancy_count", "people_count", "occupancy"))
        people = _number(people, positive=True)
        if people is None and area is not None and _number(profile.get("occupancy_density_people_m2"), positive=True):
            people = area * float(profile["occupancy_density_people_m2"])
            people_source = {"origin": "controlled_fallback"}
        people_rate, people_rate_source = _first(sources, ("outside_air_lps_per_person", "people_rate_lps_per_person"))
        people_rate = _number(people_rate, positive=True) or _number(profile.get("outside_air_lps_person"), positive=True)
        area_rate, area_rate_source = _first(sources, ("outside_air_lps_per_m2", "area_rate_lps_per_m2"))
        area_rate = _number(area_rate, positive=True) or _number(profile.get("outside_air_lps_m2"), positive=True)
        fixed, fixed_source = _first(sources, ("outside_air_lps", "fixed_minimum_lps", "outside_air_fixed_lps"))
        fixed = _number(fixed, positive=True)
        people_flow = people * people_rate if people is not None and people_rate is not None else None
        area_flow = area * area_rate if area is not None and area_rate is not None else None
        rates = [value for value in (people_flow, area_flow, fixed) if value is not None]
        outside = max(rates) if rates else None
        outside_origin = "project_evidence" if any(_origin(source) == "project_evidence" for source in (people_rate_source, area_rate_source, fixed_source)) else "controlled_fallback"
        outside_status = "resolved" if outside is not None and outside_origin == "project_evidence" else "provisional" if outside is not None else "excluded"
        outside_record = _make_record("outside_air", rid, "room ventilation", value=outside, origin=outside_origin, confidence=confidence if outside_origin != "controlled_fallback" else .35,
            evidence=evidence, rationale="Governing people/area/fixed ventilation rate.", schedule=schedule,
            formula="max(occupancy × L/s/person, area × L/s/m², fixed minimum)",
            operands={"occupancy": people, "people_rate_lps_per_person": people_rate, "area_m2": area, "area_rate_lps_per_m2": area_rate, "fixed_minimum_lps": fixed}, status=outside_status,
            assumptions=["controlled room-use ventilation profile"] if outside_origin == "controlled_fallback" else [])
        outside_record["field_resolution"] = {"occupancy": _field_record(people, "people", _origin(people_source, "controlled_fallback"), confidence, "room evidence", _evidence(people_source), "Resolved occupancy for outside-air calculation."),
                                               "people_rate": _field_record(people_rate, "L/s/person", _origin(people_rate_source, "controlled_fallback"), confidence, "ventilation profile", _evidence(people_rate_source), "Selected people rate."),
                                               "area_rate": _field_record(area_rate, "L/s/m²", _origin(area_rate_source, "controlled_fallback"), confidence, "ventilation profile", _evidence(area_rate_source), "Selected area rate.")}
        records.append(outside_record)

        infiltration_value, infiltration_source = _first(sources, ("infiltration_ach", "ach"))
        infiltration_unit = "ACH"
        infiltration_value = _number(infiltration_value, positive=True)
        if infiltration_value is None:
            infiltration_value, infiltration_source = _first(sources, ("infiltration_lps", "infiltration_flow_lps"))
            infiltration_unit = "L/s"
            infiltration_value = _number(infiltration_value, positive=True)
        if infiltration_value is None:
            infiltration_value = _number((pack.get("preliminary_defaults", {}) or {}).get("infiltration_ach"), positive=True)
            infiltration_source = {"origin": "controlled_fallback"}
            infiltration_unit = "ACH"
        infiltration_origin = _origin(infiltration_source, "controlled_fallback")
        unresolved = []
        if infiltration_unit == "ACH" and volume is None:
            unresolved.append("resolved room volume is required for ACH infiltration")
        infiltration = _make_record("infiltration", rid, "uncontrolled infiltration", value=infiltration_value, unit=infiltration_unit,
            origin=infiltration_origin, confidence=confidence if infiltration_origin != "controlled_fallback" else .35,
            evidence=evidence, rationale="Door, exposure, cited leakage, or controlled preliminary infiltration basis.", schedule=_schedule(profile_id),
            formula="ACH × room volume × 1000 ÷ 3600" if infiltration_unit == "ACH" else "direct airflow",
            operands={"room_volume_m3": volume, "ach": infiltration_value if infiltration_unit == "ACH" else None, "resolved_flow_lps": volume * infiltration_value * 1000 / 3600 if volume is not None and infiltration_unit == "ACH" else infiltration_value},
            status="blocked" if unresolved else "provisional" if infiltration_origin in {"controlled_fallback", "research_candidate"} else "resolved", unresolved=unresolved,
            assumptions=["controlled preliminary infiltration ACH"] if infiltration_origin == "controlled_fallback" else [], extra={"room_volume_id": ceiling.get("room_id", rid) if isinstance(ceiling, dict) else rid, "air_path": "uncontrolled_infiltration"})
        records.append(infiltration)

        process_kind = _text(use.get("taxonomy_id", use.get("category", ""))).casefold() if isinstance(use, dict) else ""
        process_scope = _text(use.get("space_scope", "")).casefold() if isinstance(use, dict) else ""
        process_value, process_source = _first(sources, ("process_exhaust_lps", "hood_exhaust_lps", "exhaust_lps"))
        process_value = _number(process_value, positive=True)
        if process_kind in {"kitchen", "process", "baking"} or process_scope == "comfort_hvac_with_process_exception" or any("kitchen" in _text(source.get("label", source.get("name"))).casefold() for source in sources if isinstance(source, dict)):
            process = _make_record("process_exhaust", rid, "kitchen/process exhaust", value=process_value, unit="L/s", origin=_origin(process_source), confidence=confidence,
                evidence=evidence, rationale="Kitchen/process exhaust requires a separately owned airflow basis.", schedule=schedule,
                formula="cited hood/process exhaust airflow", status="provisional" if process_value is not None else "excluded",
                unresolved=[] if process_value is not None else ["process exhaust airflow is unresolved"], extra={"process_exception": True})
            records.append(process)
            transfer, _ = _first(sources, ("transfer_air_lps", "allowable_transfer_air_lps")); transfer = _number(transfer, positive=True) or 0.0
            credit, _ = _first(sources, ("outside_air_credit_lps", "allowable_outside_air_credit_lps")); credit = _number(credit, positive=True) or 0.0
            make_up = max(0.0, process_value - transfer - credit) if process_value is not None else None
            records.append(_make_record("make_up_air", rid, "process-air replacement", value=make_up, unit="L/s", origin=_origin(process_source), confidence=confidence,
                evidence=evidence, rationale="Replacement air after transfer and outside-air credits.", schedule=schedule,
                formula="max(0, exhaust − transfer credit − outside-air credit)", operands={"process_exhaust_lps": process_value, "transfer_air_lps": transfer, "outside_air_credit_lps": credit},
                status="provisional" if make_up is not None else "excluded", unresolved=[] if make_up is not None else ["make-up-air ownership and quantity are unresolved"], extra={"conditioning_owner": _text(next((source.get("make_up_air_owner") for source in sources if isinstance(source, dict) and source.get("make_up_air_owner")), ""))}))

        for raw in proposal.get("airflows", proposal.get("air_paths", [])) if isinstance(proposal, dict) else []:
            if not isinstance(raw, dict) or _text(raw.get("room_id", raw.get("owner_room_id"))) not in {rid, room["label"]}:
                continue
            path = _text(raw.get("air_path_type", raw.get("type"))).casefold()
            value = _number(raw.get("value", raw.get("flow_lps")), positive=True)
            if path in AIR_PATH_TYPES:
                proposal_origin = _origin(raw)
                if proposal_origin == "unresolved":
                    value = None
                records.append(_make_record(path, rid, _text(raw.get("source", "AI airflow evidence")), value=value, unit=_text(raw.get("unit", "L/s")) or "L/s", origin=proposal_origin, confidence=_confidence(raw.get("confidence"), confidence), evidence=_evidence(raw), rationale=_text(raw.get("rationale", "AI airflow candidate.")), schedule=_schedule(profile_id, raw.get("schedule")), status="provisional" if value is not None else "excluded", unresolved=[] if value is not None else ["airflow value requires cited evidence or a released source/fallback"], system_owner=_text(raw.get("ahu_id", raw.get("system_id", ""))), source_id=_text(raw.get("tag", raw.get("id", ""))), extra={"geometry_key": _text(raw.get("geometry_key", raw.get("location", "")))}))

    # Apply overrides by room AND path type. A room can legitimately have
    # multiple independently reviewed airflow paths.
    current_keys = set()
    for row in records:
        key = (_text(row.get("owner_room_id")), _text(row.get("air_path_type")))
        current_keys.add(key)
        override = overrides.get(key)
        if override:
            _apply_override(row, override)

    # Preserve engineer decisions for rooms/paths removed from the proposal,
    # but keep them explicitly stale so no downstream calculation can use them.
    for key, override in sorted(overrides.items()):
        if key in current_keys or key not in previous_records:
            continue
        records.append(_mark_stale_orphan(previous_records[key], override, source_fingerprints))

    # Reconcile duplicate physical paths before the draft model consumes them.
    groups = {}
    for record in records:
        key = (_text(record.get("air_path_type")), _text(record.get("owner_room_id")),
               _text(record.get("geometry_key") or record.get("source_id", "")))
        if key[2]:
            groups.setdefault(key, []).append(record)
    for group in groups.values():
        owners = {(_text(row.get("ahu_id")), _text(row.get("conditioning_owner"))) for row in group}
        values = {None if row.get("value") is None else float(row["value"]) for row in group}
        units = {_text(row.get("unit", "L/s")).casefold() for row in group}
        directions = {_text(row.get("direction")).casefold() for row in group}
        reasons = []
        if len(owners) > 1:
            reasons.append("duplicate airflow ownership requires review")
        if len(values) > 1 or len(units) > 1 or len(directions) > 1:
            reasons.append("records for the same physical airflow path disagree on value, unit, or direction")
        if reasons:
            ids = sorted(row["airflow_id"] for row in group)
            for row in group:
                row["status"] = "blocked"
                row["conflicts"] = sorted(set(row.get("conflicts", []) + ids))
                row["unresolved_fields"] = sorted(set(row.get("unresolved_fields", []) + reasons))

    result = {"schema_version": SCHEMA_VERSION, "records": sorted(records, key=lambda row: row["airflow_id"]), "issues": issues,
              "overrides": [deepcopy(row) for _, row in sorted(overrides.items())],
              "research_jobs": deepcopy(existing.get("research_jobs", [])),
              "source_fingerprints": source_fingerprints, "status": "draft" if records else "needs_review", "updated_at": now()}
    result["summary"] = {"discovered": len(records), "resolved": sum(row["status"] == "resolved" for row in records),
                          "provisional": sum(row["status"] == "provisional" for row in records), "blocked": sum(row["status"] == "blocked" for row in records),
                          "excluded": sum(row["status"] == "excluded" for row in records), "stale": sum(row["status"] == "stale" for row in records),
                          "outside_air_lps": round(sum(row.get("value") or 0 for row in records if row["air_path_type"] == "outside_air" and row["status"] not in {"blocked", "stale"}), 3)}
    result["fingerprint"] = fingerprint({key: value for key, value in result.items() if key != "fingerprint"})
    return result


def validate(raw):
    if not isinstance(raw, dict):
        raise ValueError("Airflow resolution must be an object.")
    result = deepcopy(empty_airflow_resolution())
    result.update({key: deepcopy(raw[key]) for key in result if key in raw})
    if not isinstance(result.get("records"), list):
        raise ValueError("Airflow records must be a list.")
    seen = set()
    for row in result["records"]:
        if not isinstance(row, dict) or not _text(row.get("airflow_id")):
            raise ValueError("Airflow records need stable IDs.")
        if row["airflow_id"] in seen:
            raise ValueError("Airflow IDs must be unique.")
        seen.add(row["airflow_id"])
        if row.get("air_path_type") not in AIR_PATH_TYPES or row.get("status") not in STATUSES:
            raise ValueError("Airflow record has an unsupported type or status.")
        if row.get("value") is not None and (_number(row.get("value")) is None or float(row["value"]) < 0):
            raise ValueError("Airflow values must be finite and non-negative.")
    result["fingerprint"] = fingerprint({key: value for key, value in result.items() if key != "fingerprint"})
    return result


def is_current(artifact, source_fingerprints):
    return isinstance(artifact, dict) and artifact.get("source_fingerprints") == (source_fingerprints or {})


def apply_override(artifact, room_id_value, path_type, value, reviewer, note=""):
    result = validate(artifact)
    reviewer = _text(reviewer)
    if not reviewer:
        raise ValueError("Airflow overrides require a reviewer.")
    if path_type not in AIR_PATH_TYPES:
        raise ValueError("Unsupported airflow path type.")
    row = next((item for item in result["records"] if item.get("owner_room_id") == room_id_value and item.get("air_path_type") == path_type), None)
    if row is None:
        raise ValueError("Airflow override references an unknown room/path.")
    if row.get("status") == "stale":
        raise ValueError("This airflow room/path is stale. Confirm it still applies and resolve current airflow inputs before editing.")
    number = _number(value, positive=True)
    if number is None:
        raise ValueError("Airflow override needs a positive numeric value.")
    row["value"] = number
    row["origin"] = "contractor_override"
    row["status"] = "resolved"
    row["overrides"] = {"reviewer": reviewer, "note": _text(note), "updated_at": now(), "value": number}
    row["unresolved_fields"] = [item for item in row.get("unresolved_fields", []) if "airflow value" not in item]
    overrides = result.setdefault("overrides", [])
    overrides[:] = [item for item in overrides if not (item.get("room_id") == room_id_value and item.get("path_type") == path_type)]
    overrides.append({"room_id": room_id_value, "path_type": path_type, "value": number,
                      "reviewer": reviewer, "note": _text(note), "updated_at": now()})
    result["fingerprint"] = fingerprint({key: value for key, value in result.items() if key != "fingerprint"})
    return result


def clear_override(artifact, room_id_value, path_type):
    result = validate(artifact)
    for row in result["records"]:
        if row.get("owner_room_id") == room_id_value and row.get("air_path_type") == path_type:
            row.pop("overrides", None)
            was_stale = row.get("status") == "stale"
            row["status"] = "stale" if was_stale else "needs_review"
            row["origin"] = "unresolved"
            row["value"] = None
            row.setdefault("unresolved_fields", []).append("airflow override cleared; resolve again")
    result["overrides"] = [item for item in result.get("overrides", [])
                            if not (item.get("room_id") == room_id_value and item.get("path_type") == path_type)]
    result["fingerprint"] = fingerprint({key: value for key, value in result.items() if key != "fingerprint"})
    return result
