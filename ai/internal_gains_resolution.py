"""Draft-only resolution of room internal gains and operating schedules.

The module is deliberately an evidence resolver, not a second heat-load
engine.  It selects counted or cited inputs first and falls back to the
released preliminary room-use profile only for draft coverage.  Every
materialised field retains its origin, formula and provenance.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import re


SCHEMA_VERSION = 1
DAY_TYPES = ("weekday", "saturday", "sunday", "holiday")
STATUSES = {"resolved", "provisional", "needs_review", "excluded", "stale"}


def now():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value):
    def normalise(item):
        if isinstance(item, dict):
            return {key: normalise(value) for key, value in sorted(item.items())
                    if key not in {"updated_at", "created_at"}}
        if isinstance(item, list):
            return [normalise(value) for value in item]
        return item
    return hashlib.sha256(json.dumps(normalise(value), sort_keys=True,
                                     separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def _text(value):
    return str(value or "").strip()


def _positive(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    except (TypeError, ValueError):
        return None


def _nonnegative(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError):
        return None


def _confidence(value, fallback=0.4):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
        return max(0.0, min(1.0, float(value)))
    return {"high": .85, "medium": .65, "low": .4}.get(_text(value).casefold(), fallback)


def confidence_band(value):
    return "high" if value >= .8 else "medium" if value >= .5 else "low"


def empty_internal_gains_resolution():
    result = {"schema_version": SCHEMA_VERSION, "records": [], "schedules": [],
              "source_fingerprints": {}, "status": "needs_review", "updated_at": ""}
    result["fingerprint"] = fingerprint(result)
    return result


def _evidence(item):
    if not isinstance(item, dict):
        return []
    rows = item.get("evidence", item.get("citations", []))
    if isinstance(rows, dict):
        rows = [rows]
    result = [deepcopy(row) for row in rows if isinstance(row, dict) and row.get("page")]
    if not result and item.get("page"):
        result.append({"page": item.get("page"), "drawing_number": _text(item.get("drawing_number")),
                       "excerpt": _text(item.get("excerpt", item.get("label")))})
    return result


def _has_citation(item):
    return bool(_evidence(item) or _text(item.get("source")) or _text(item.get("source_id")) or
                _text(item.get("citation"))) if isinstance(item, dict) else False


def _room_id(label, level):
    def slug(value):
        return re.sub(r"[^a-z0-9]+", "-", _text(value).casefold()).strip("-") or "unassigned"
    return f"room-use:{slug(level)}:{slug(label)}"


def _schedule_values(profile_id):
    active = {
        "retail": range(9, 18), "office": range(8, 18), "hospitality": range(10, 23),
        "storage": range(7, 18), "residential": range(6, 23),
        "generic_conditioned_room": range(8, 18),
    }.get(profile_id, range(8, 18))
    return [1.0 if hour in active else 0.0 for hour in range(24)]


def _valid_schedule(values):
    return isinstance(values, list) and len(values) == 24 and all(
        isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))
        and 0 <= float(value) <= 1 for value in values
    )


def _schedule_for(item, profile_id):
    supplied = item.get("schedules") if isinstance(item, dict) else None
    values = _schedule_values(profile_id)
    profiles = {}
    if isinstance(supplied, dict):
        for day in DAY_TYPES:
            candidate = supplied.get(day)
            if _valid_schedule(candidate):
                profiles[day] = [float(value) for value in candidate]
    for day in DAY_TYPES:
        profiles.setdefault(day, list(values))
    return profiles


def _profile(pack, profile_id):
    profiles = (pack or {}).get("profiles", {}) if isinstance(pack, dict) else {}
    return profiles.get(profile_id) or profiles.get("generic_conditioned_room", {})


def _room_rows(building, vision, proposal, room_use):
    grouped = {}
    for source in ((building or {}).get("spaces", []) if isinstance(building, dict) else []):
        if not isinstance(source, dict) or not _text(source.get("name")):
            continue
        label = _text(source.get("name")); level = _text(source.get("level_name")) or "Unassigned level"
        grouped.setdefault(_room_id(label, level), {"label": label, "level": level, "sources": []})["sources"].append(source)
    for source in ((proposal or {}).get("rooms", []) if isinstance(proposal, dict) else []):
        if not isinstance(source, dict) or not _text(source.get("label")):
            continue
        label = _text(source.get("label")); level = _text(source.get("level_name")) or "Unassigned level"
        grouped.setdefault(_room_id(label, level), {"label": label, "level": level, "sources": []})["sources"].append(source)
    entities = (((vision or {}).get("result", {}) or {}).get("auto_extraction", {}) or {}).get("entities", [])
    for source in entities if isinstance(entities, list) else []:
        if not isinstance(source, dict) or source.get("kind") != "room" or not _text(source.get("label")):
            continue
        label = _text(source.get("label")); level = _text(source.get("level_name")) or "Unassigned level"
        grouped.setdefault(_room_id(label, level), {"label": label, "level": level, "sources": []})["sources"].append(source)
    records = []
    room_use_rows = {row.get("room_id"): row for row in (room_use or {}).get("records", []) if isinstance(row, dict)}
    for key in sorted(grouped):
        row = grouped[key]
        use = room_use_rows.get(key, {})
        row["room_use"] = use
        records.append(row)
    return records


def _find_number(sources, keys):
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in keys:
            value = _positive(source.get(key))
            if value is not None:
                return value, source
    return None, None


def _field(value, origin, confidence, rationale, evidence, formula="", operands=None, source_id=""):
    return {"value": value, "origin": origin, "confidence": round(confidence, 4),
            "confidence_band": confidence_band(confidence), "rationale": rationale,
            "evidence": deepcopy(evidence), "source_id": source_id,
            "formula": formula, "operands": deepcopy(operands or {})}


def _equipment_sources(sources):
    result = []
    for source in sources:
        rows = source.get("equipment") if isinstance(source, dict) else None
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list):
            continue
        for item in rows:
            if isinstance(item, dict):
                result.append(item)
    return result


def resolve(building, vision=None, proposal=None, room_use=None, pack=None,
            source_fingerprints=None, existing=None):
    previous = {row.get("room_id"): row for row in (existing or {}).get("records", []) if isinstance(row, dict)}
    records, schedules = [], []
    for room in _room_rows(building, vision or {}, proposal or {}, room_use or {}):
        label, level, sources = room["label"], room["level"], room["sources"]
        use = room.get("room_use") or {}
        profile_id = use.get("preliminary_profile_id") or "generic_conditioned_room"
        profile = _profile(pack, profile_id)
        evidence = []
        for source in sources:
            for ref in _evidence(source):
                if ref not in evidence:
                    evidence.append(ref)
        scope = use.get("space_scope", "unresolved_scope")
        room_id = _room_id(label, level)
        override = (previous.get(room_id) or {}).get("override") or {}
        fields = {}
        # Counts may be interpreted from a plan, but only cited watts/properties
        # are allowed to override the controlled physical-value profile.
        count, count_source = _find_number(sources, ("occupancy_count", "seat_count", "workstation_count", "desk_count", "bed_count"))
        if count is not None:
            fields["occupancy_count"] = _field(round(count), "direct_project_evidence", .85,
                "Counted seats/workstations/beds from source evidence.", _evidence(count_source),
                "round(count)", {"count": count})
        else:
            area, area_source = _find_number(sources, ("area_m2", "room_area_m2"))
            density = _positive(profile.get("occupancy_density_people_m2"))
            if area and density:
                count = round(area * density)
                fields["occupancy_count"] = _field(count, "controlled_preliminary_profile", .3,
                    "No counted occupants were available; controlled room-use density fallback.",
                    _evidence(area_source), "round(area_m2 × occupancy_density_people_m2)",
                    {"area_m2": area, "occupancy_density_people_m2": density})
            else:
                fields["occupancy_count"] = _field(None, "unresolved", .0,
                    "Room area or occupancy density is unavailable.", evidence)
        explicit_people_s = None; explicit_people_l = None
        for source in sources:
            if _has_citation(source):
                explicit_people_s = _positive(source.get("people_sensible_w_per_person")) or explicit_people_s
                explicit_people_l = _positive(source.get("people_latent_w_per_person")) or explicit_people_l
        people_s = explicit_people_s or _positive(profile.get("people_sensible_w"))
        people_l = explicit_people_l or _positive(profile.get("people_latent_w"))
        people_origin = "direct_project_evidence" if explicit_people_s or explicit_people_l else "controlled_preliminary_profile"
        fields["people_sensible_w_per_person"] = _field(people_s, people_origin, .85 if explicit_people_s else .3,
            "Cited people sensible gain." if explicit_people_s else "Controlled room-use profile fallback.", evidence)
        fields["people_latent_w_per_person"] = _field(people_l, people_origin, .85 if explicit_people_l else .3,
            "Cited people latent gain." if explicit_people_l else "Controlled room-use profile fallback.", evidence)
        diversity = _positive((override or {}).get("people_diversity")) or _positive(profile.get("people_diversity"))
        fields["people_diversity"] = _field(diversity, "contractor_override" if (override or {}).get("people_diversity") else "controlled_preliminary_profile",
            1.0 if (override or {}).get("people_diversity") else .3, "People diversity factor.", evidence)
        fixtures = []
        for source in sources:
            values = source.get("lighting_fixtures") if isinstance(source, dict) else None
            if isinstance(values, dict): values = [values]
            if isinstance(values, list): fixtures.extend(item for item in values if isinstance(item, dict))
        fixture_w = sum((_positive(item.get("wattage_w")) or _positive(item.get("watts")) or 0) *
                        (_positive(item.get("quantity")) or 1) for item in fixtures if _has_citation(item))
        area, area_source = _find_number(sources, ("area_m2", "room_area_m2"))
        if fixture_w > 0:
            lighting = _field(fixture_w, "direct_project_evidence", .85, "Luminaire quantity × cited nominal wattage.",
                              sum((_evidence(item) for item in fixtures), []), "Σ(quantity × wattage_w)", {"fixtures": fixtures})
        elif area and _positive(profile.get("lighting_w_m2")):
            lighting = _field(area * profile["lighting_w_m2"], "controlled_preliminary_profile", .3,
                               "Controlled lighting-density fallback.", _evidence(area_source),
                               "area_m2 × lighting_w_m2", {"area_m2": area, "lighting_w_m2": profile["lighting_w_m2"]})
        else:
            lighting = _field(None, "unresolved", .0, "Lighting area or fixture evidence is unavailable.", evidence)
        fields["lighting_load_w"] = lighting
        fields["lighting_diversity"] = _field(_positive(profile.get("lighting_diversity")), "controlled_preliminary_profile", .3, "Controlled lighting diversity.", evidence)
        equipment = _equipment_sources(sources)
        equipment_records = []
        for item in equipment:
            rated = _positive(item.get("rated_input_w", item.get("watts")))
            qty = _positive(item.get("quantity")) or 1
            factor = _nonnegative(item.get("heat_to_space_factor"))
            if rated and factor is not None and _has_citation(item):
                equipment_records.append({"name": _text(item.get("name", item.get("model", "equipment"))), "quantity": qty,
                    "rated_input_w": rated, "heat_to_space_factor": factor,
                    "diversity": _positive(item.get("diversity_factor")) or _positive(profile.get("equipment_diversity")),
                    "origin": "direct_project_evidence", "confidence": .85, "evidence": _evidence(item),
                    "formula": "quantity × rated_input_w × heat_to_space_factor"})
        if not equipment_records and scope not in {"refrigeration_process", "unresolved_scope"} and area and _positive(profile.get("equipment_w_m2")):
            equipment_records.append({"name": "Controlled preliminary profile equipment", "quantity": 1,
                "rated_input_w": area * profile["equipment_w_m2"], "heat_to_space_factor": _positive(profile.get("equipment_space_gain")) or 0,
                "diversity": _positive(profile.get("equipment_diversity")) or 0, "origin": "controlled_preliminary_profile",
                "confidence": .3, "evidence": _evidence(area_source), "formula": "area_m2 × equipment_w_m2 × heat_to_space_factor"})
        fields["equipment"] = _field(equipment_records, "direct_project_evidence" if equipment_records and equipment_records[0]["origin"] == "direct_project_evidence" else "controlled_preliminary_profile" if equipment_records else "unresolved",
                                      .85 if equipment_records and equipment_records[0]["origin"] == "direct_project_evidence" else .3 if equipment_records else 0,
                                      "Cited equipment heat-to-space records." if equipment_records and equipment_records[0]["origin"] == "direct_project_evidence" else "Controlled equipment-density fallback." if equipment_records else "Equipment heat-to-space data is unresolved.",
                                      sum((item.get("evidence", []) for item in equipment_records), []), "Σ(quantity × rated_input_w × heat_to_space_factor)")
        schedule_id = "internal-gains-" + re.sub(r"[^a-z0-9_-]+", "-", room_id.casefold()).strip("-")
        schedule_input = next((source.get("schedules") for source in sources if isinstance(source, dict) and isinstance(source.get("schedules"), dict)), {})
        prior_schedule_input = (previous.get(room_id) or {}).get("schedule_input", {})
        schedule_profiles = _schedule_for({"schedules": schedule_input or prior_schedule_input}, profile_id)
        invalid_schedule_days = [day for day in DAY_TYPES if isinstance(schedule_input, dict) and day in schedule_input and not _valid_schedule(schedule_input.get(day))]
        schedules.append({"schedule_id": schedule_id, "room_id": room_id, "status": "needs_review" if invalid_schedule_days else "provisional",
                          "source": "direct project/PDF evidence" if schedule_input else "controlled preliminary room-use profile",
                          "origin": "direct_project_evidence" if schedule_input else "controlled_preliminary_profile",
                          "confidence": .85 if schedule_input and not invalid_schedule_days else .3,
                          "invalid_day_types": invalid_schedule_days, "day_profiles": schedule_profiles,
                          "fields": {name: {"schedule_id": schedule_id, "day_types": list(DAY_TYPES)} for name in ("people", "lighting", "equipment", "outside_air", "infiltration", "process_equipment")},
                          "fingerprint": fingerprint(schedule_profiles)})
        status = "excluded" if scope in {"refrigeration_process", "unresolved_scope"} else "needs_review" if invalid_schedule_days or any(field.get("origin") == "unresolved" for field in fields.values() if isinstance(field, dict)) else "provisional"
        record = {"room_id": room_id, "level": level, "zone_id": f"zone-{room_id}", "geometry_proof_id": next((s.get("geometry_proof_id", "") for s in sources if s.get("geometry_proof_id")), ""),
                  "original_label": label, "room_use_taxonomy": use.get("taxonomy_id", "generic_conditioned"), "preliminary_profile_id": profile_id,
                  "space_scope": scope, "fields": fields, "occupancy_count": fields["occupancy_count"]["value"],
                  "people_sensible_w_per_person": fields["people_sensible_w_per_person"]["value"], "people_latent_w_per_person": fields["people_latent_w_per_person"]["value"],
                  "lighting_load_w": lighting["value"], "equipment": equipment_records, "schedule_id": schedule_id,
                  "schedule_profiles": schedule_profiles, "schedule_input": deepcopy(schedule_input or prior_schedule_input), "evidence": evidence, "confidence_score": min((field.get("confidence", 0) for field in fields.values() if isinstance(field, dict)), default=0),
                  "rationale": "Resolved from room evidence and controlled preliminary profile precedence.", "unresolved_fields": [key for key, field in fields.items() if isinstance(field, dict) and field.get("origin") == "unresolved"] + (["schedules"] if invalid_schedule_days else []),
                  "remediation": "Provide cited occupancy, lighting, equipment heat-to-space, or schedule evidence." if status == "needs_review" else "",
                  "override": deepcopy(override) if override else None, "status": status, "source_fingerprints": deepcopy(source_fingerprints or {})}
        record["confidence_band"] = confidence_band(record["confidence_score"])
        record["record_fingerprint"] = fingerprint(record)
        records.append(record)
    present = {row["room_id"] for row in records}
    retained_schedules = []
    for room_id, prior in previous.items():
        override = prior.get("override")
        if room_id in present or not isinstance(override, dict) or not override:
            continue
        retained = deepcopy(prior)
        retained["status"] = "stale"
        retained["rationale"] = "This room is absent from the current proposal; its internal-gains overrides were retained for review."
        retained["unresolved_fields"] = sorted(set(retained.get("unresolved_fields", [])) | {"room removed from current proposal"})
        retained["remediation"] = "Confirm whether the room was intentionally removed before clearing its internal-gains overrides."
        retained["source_fingerprints"] = deepcopy(source_fingerprints or {})
        retained["record_fingerprint"] = fingerprint(retained)
        records.append(retained)
        retained_schedules.extend(schedule for schedule in (existing or {}).get("schedules", [])
                                  if isinstance(schedule, dict) and schedule.get("room_id") == room_id)
    schedules.extend(retained_schedules)
    result = {"schema_version": SCHEMA_VERSION, "records": records, "schedules": schedules,
              "source_fingerprints": deepcopy(source_fingerprints or {}),
              "status": "needs_review" if any(row["status"] in {"needs_review", "stale"} for row in records) else "provisional" if records else "needs_review", "updated_at": now()}
    retained_overrides = [row for row in records if row.get("status") == "stale"]
    if retained_overrides:
        result["stale_reasons"] = [
            f"{len(retained_overrides)} room(s) no longer appear in the current proposal; saved internal-gains overrides were retained for review."
        ]
    result["fingerprint"] = fingerprint(result)
    return result


def validate(raw):
    raw = raw or empty_internal_gains_resolution()
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported internal-gains resolution artifact.")
    records = raw.get("records", [])
    schedules = raw.get("schedules", [])
    if not isinstance(records, list) or not isinstance(schedules, list):
        raise ValueError("Internal-gains records and schedules must be lists.")
    ids = [row.get("room_id") for row in records if isinstance(row, dict)]
    if len(ids) != len(set(ids)) or any(not value for value in ids):
        raise ValueError("Internal-gains room IDs must be unique and non-empty.")
    schedule_ids = [row.get("schedule_id") for row in schedules if isinstance(row, dict)]
    if len(schedule_ids) != len(set(schedule_ids)):
        raise ValueError("Internal-gains schedule IDs must be unique.")
    for schedule in schedules:
        for day in DAY_TYPES:
            if not _valid_schedule((schedule.get("day_profiles") or {}).get(day)):
                raise ValueError(f"Schedule {schedule.get('schedule_id', '')} needs 24 values for {day} in the range 0..1.")
    result = deepcopy(raw)
    result["fingerprint"] = fingerprint({key: value for key, value in result.items() if key != "fingerprint"})
    return result


def is_current(artifact, source_fingerprints, pack=None):
    if not isinstance(artifact, dict):
        return False
    expected = deepcopy(source_fingerprints or {})
    if pack is not None:
        expected["preliminary_pack"] = fingerprint(pack)
    return artifact.get("source_fingerprints") == expected


def apply_override(artifact, room_id, field, value, reviewer, note=""):
    if not _text(reviewer):
        raise ValueError("Internal-gains override needs a reviewer.")
    artifact = validate(artifact)
    for row in artifact["records"]:
        if row["room_id"] != room_id:
            continue
        field_row = row.get("fields", {}).get(field)
        if not isinstance(field_row, dict):
            raise ValueError("Unknown internal-gains field.")
        if field == "equipment":
            if not isinstance(value, list):
                raise ValueError("Equipment override must be a list.")
        elif _nonnegative(value) is None:
            raise ValueError("Internal-gains override must be a finite non-negative value.")
        field_row["value"] = value
        field_row["origin"] = "contractor_override"
        field_row["confidence"] = 1.0
        field_row["confidence_band"] = "high"
        field_row["rationale"] = _text(note) or "Contractor override."
        row["override"] = row.get("override") or {}
        row["override"][field] = {"value": value, "reviewer": _text(reviewer), "note": _text(note), "updated_at": now()}
        if field in {"occupancy_count", "people_sensible_w_per_person", "people_latent_w_per_person", "lighting_load_w"}:
            row[field] = value
        row["record_fingerprint"] = fingerprint(row)
        artifact["updated_at"] = now()
        artifact["fingerprint"] = fingerprint(artifact)
        return artifact
    raise ValueError("Internal-gains override references an unknown room.")


def clear_override(artifact, room_id, field=None):
    artifact = validate(artifact)
    row = next((row for row in artifact["records"] if row["room_id"] == room_id), None)
    if row is None:
        raise ValueError("Internal-gains override references an unknown room.")
    overrides = row.get("override") or {}
    fields = [field] if field else list(overrides)
    for key in fields:
        overrides.pop(key, None)
    row["override"] = overrides or None
    artifact["updated_at"] = now()
    artifact["fingerprint"] = fingerprint(artifact)
    return artifact
