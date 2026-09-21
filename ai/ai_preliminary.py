"""Isolated, controlled-assumption cooling inputs for AI preliminary reports.

This module deliberately does not write reviewed calculator artifacts.  It
turns source-linked AI/PDF topology into a *draft-only* hourly-engine payload,
using values selected from a versioned product pack rather than arbitrary AI
numbers.
"""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from ai.design_requirements import validate_design_requirements
from ai.hourly_loads import build_hourly_load_model, calculate_hourly_load_report


ROOT = Path(__file__).resolve().parents[1]
PACK_PATH = ROOT / "config" / "ai_preliminary_assumption_pack.json"
PACK_VERSION = "au-preliminary-v2"
PROFILE_IDS = {"retail", "office", "hospitality", "storage", "residential", "generic_conditioned_room"}
SPACE_SCOPES = {"comfort_hvac", "comfort_hvac_with_process_exception", "refrigeration_process", "unresolved_scope"}
OPAQUE_TYPES = {"wall", "roof", "ceiling"}
CARDINALS = {"N", "E", "S", "W"}
SHADING_CATEGORIES = {"unshaded", "partial", "deep"}


def now():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value):
    def normalise(item):
        if isinstance(item, dict):
            return {key: normalise(value) for key, value in sorted(item.items())}
        if isinstance(item, list):
            values = [normalise(value) for value in item]
            return sorted(values, key=lambda value: json.dumps(value, sort_keys=True, separators=(",", ":")))
        return item
    return hashlib.sha256(json.dumps(normalise(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def content_fingerprint(value):
    """Fingerprint authored inputs without volatile artifact timestamps."""
    def strip(item):
        if isinstance(item, dict):
            return {key: strip(value) for key, value in item.items()
                    if key not in {"updated_at", "created_at", "finished_at", "source_requirements_updated_at"}}
        if isinstance(item, list):
            return [strip(value) for value in item]
        return item
    return fingerprint(strip(value))


def load_pack():
    pack = json.loads(PACK_PATH.read_text(encoding="utf-8"))
    if pack.get("version") != PACK_VERSION or not PROFILE_IDS <= set(pack.get("profiles", {})):
        raise ValueError("The AI preliminary assumption pack is incomplete or has an unsupported version.")
    if len(pack.get("scenario", {}).get("hours", [])) != 24:
        raise ValueError("The AI preliminary assumption pack needs a 24-hour cooling scenario.")
    envelope = pack.get("preliminary_envelope", {})
    profiles = envelope.get("cardinal_solar_profiles_w_m2", {})
    if set(profiles) != CARDINALS or any(not isinstance(profiles[key], list) or len(profiles[key]) != 24 for key in CARDINALS):
        raise ValueError("The AI preliminary assumption pack needs four 24-hour cardinal solar profiles.")
    if not SHADING_CATEGORIES <= set(envelope.get("shading_categories", {})):
        raise ValueError("The AI preliminary assumption pack needs controlled shading categories.")
    return pack


def empty_settings():
    return {
        "schema_version": 1,
        "saved_project_consent": False,
        "maximum_provider_budget_aud": None,
        "automatic_analysis_enabled": False,
        "preliminary_pack_version": PACK_VERSION,
        "updated_at": "",
    }


def validate_settings(raw):
    result = empty_settings()
    result.update({key: raw[key] for key in result if isinstance(raw, dict) and key in raw})
    if not isinstance(result["saved_project_consent"], bool) or not isinstance(result["automatic_analysis_enabled"], bool):
        raise ValueError("AI preliminary consent and automatic-analysis settings must be true or false.")
    budget = result["maximum_provider_budget_aud"]
    if budget in (None, ""):
        result["maximum_provider_budget_aud"] = None
    else:
        try:
            result["maximum_provider_budget_aud"] = float(budget)
        except (TypeError, ValueError) as error:
            raise ValueError("AI preliminary provider budget must be a positive number or blank.") from error
        if result["maximum_provider_budget_aud"] <= 0:
            raise ValueError("AI preliminary provider budget must be positive.")
    if result["preliminary_pack_version"] != PACK_VERSION:
        raise ValueError("The selected AI preliminary pack version is unavailable.")
    return result


def provider_eligibility(settings, provider_configured, estimated_cost=None):
    settings = validate_settings(settings)
    if not settings["automatic_analysis_enabled"]:
        return "disabled"
    if not settings["saved_project_consent"]:
        return "awaiting_consent"
    if not provider_configured:
        return "awaiting_provider"
    if settings["maximum_provider_budget_aud"] is None:
        return "awaiting_budget"
    if estimated_cost is not None and estimated_cost > settings["maximum_provider_budget_aud"]:
        return "budget_exceeded"
    return "eligible"


def _number(value):
    try:
        value = float(value)
        return value if value > 0 else None
    except (TypeError, ValueError):
        match = re.search(r"\d+(?:\.\d+)?", str(value or ""))
        return float(match.group(0)) if match else None


def _slug(value):
    result = re.sub(r"[^a-z0-9]+", "-", str(value).casefold()).strip("-")
    return result or "conditioned-room"


def _confidence(value, fallback=0.45):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0.0, min(1.0, float(value)))
    return {"high": 0.85, "medium": 0.65, "low": 0.4}.get(str(value).casefold(), fallback)


def confidence_band(score):
    return "high" if score >= 0.8 else "medium" if score >= 0.5 else "low"


def _profile_from_label(label):
    text = str(label).casefold()
    if any(word in text for word in ("office", "meeting", "admin", "reception")):
        return "office"
    if any(word in text for word in ("restaurant", "cafe", "bar", "kitchen", "dining", "hospitality")):
        return "hospitality"
    if any(word in text for word in ("store", "storage", "warehouse", "cool room", "freezer")):
        return "storage"
    if any(word in text for word in ("bedroom", "apartment", "residential", "living")):
        return "residential"
    if any(word in text for word in ("retail", "shop", "showroom", "tenancy")):
        return "retail"
    return "generic_conditioned_room"


def _scope_from_label(label):
    text = str(label).casefold()
    if "cool room" in text or "coolroom" in text or "freezer" in text:
        return "refrigeration_process"
    if "kitchen" in text or "cook" in text or "food preparation" in text:
        return "comfort_hvac_with_process_exception"
    return "comfort_hvac"


def _room_identity(name, level):
    """Merge cross-source sightings without merging same-named floor rooms."""
    level = re.sub(r"\b(level|floor|fl)\b", "", str(level).casefold())
    return _slug(name), re.sub(r"[^a-z0-9]+", "", level) or "unassigned"


def _evidence_refs(space):
    return deepcopy(space.get("evidence", [])) if isinstance(space.get("evidence"), list) else []


def _combined_evidence(*values):
    rows, seen = [], set()
    for value in values:
        for row in _evidence_refs(value):
            key = json.dumps(row, sort_keys=True, separators=(",", ":"))
            if key not in seen:
                seen.add(key)
                rows.append(row)
    return rows


def _vision_rooms(vision, manual_entities=None):
    rows = vision.get("result", {}).get("auto_extraction", {}).get("entities", []) if isinstance(vision, dict) else []
    rows = list(rows) + list(manual_entities or [])
    return [row for row in rows if isinstance(row, dict) and row.get("kind") == "room"]


def _space_rows(building, vision, manual_entities=None):
    rows, seen, identities = [], set(), set()
    vision_rooms = _vision_rooms(vision, manual_entities)
    for space in building.get("spaces", []) if isinstance(building, dict) else []:
        if not isinstance(space, dict) or not str(space.get("name", "")).strip():
            continue
        name, level = str(space["name"]).strip(), str(space.get("level_name", "")).strip() or "Unassigned level"
        matches = [row for row in vision_rooms if str(row.get("label", "")).casefold() == name.casefold()
                   and (not row.get("level_name") or str(row.get("level_name")).casefold() == level.casefold())]
        # A source-linked placeholder can complete an existing PDF label (for
        # example, add its measured area) without creating a duplicate room.
        matches.sort(key=lambda row: (
            _number(row.get("area_m2")) is not None,
            str(row.get("preliminary_profile_id", "")) in PROFILE_IDS,
            _confidence(row.get("confidence")),
        ), reverse=True)
        ai = matches[0] if matches else {}
        evidence = _combined_evidence(space, ai)
        key = fingerprint({"name": name.casefold(), "level": level.casefold(), "evidence": evidence})[:16]
        if key in seen:
            continue
        seen.add(key)
        identities.add(_room_identity(name, level))
        rows.append({"key": key, "name": name, "level": level, "area_m2": _number(space.get("area")) or _number(ai.get("area_m2")),
                     "profile_id": ai.get("preliminary_profile_id", ""), "confidence": _confidence(ai.get("confidence", space.get("confidence"))),
                     "scope": ai.get("space_scope", _scope_from_label(name)), "evidence": evidence, "ai": ai})
    for ai in vision_rooms:
        name, level = str(ai.get("label", "")).strip(), str(ai.get("level_name", "")).strip() or "Unassigned level"
        if not name:
            continue
        key = fingerprint({"name": name.casefold(), "level": level.casefold(), "page": ai.get("page")})[:16]
        if key in seen or _room_identity(name, level) in identities:
            continue
        seen.add(key)
        identities.add(_room_identity(name, level))
        rows.append({"key": key, "name": name, "level": level, "area_m2": _number(ai.get("area_m2")),
                     "profile_id": ai.get("preliminary_profile_id", ""), "confidence": _confidence(ai.get("confidence")),
                     "scope": ai.get("space_scope", _scope_from_label(name)), "evidence": _evidence_refs(ai) or [{"page": ai.get("page"), "drawing_number": ai.get("drawing_number", ""), "excerpt": ai.get("excerpt", name)}], "ai": ai})
    if not rows:
        rows.append({"key": "whole-building-generic", "name": "Whole building conditioned area — AI review required", "level": "Unassigned level",
                     "area_m2": None, "profile_id": "generic_conditioned_room", "confidence": 0.2, "scope": "unresolved_scope", "evidence": [], "ai": {}})
    return rows


def validate_manual_placeholder_entities(raw):
    """Validate the narrow, source-linked manual-AI recovery contract.

    The recovery path is intentionally room-only.  It lets a human-operated
    placeholder or future provider add an interpreted whole-building topology
    to the *separate preliminary model* without mutating reviewed evidence.
    """
    if not isinstance(raw, list) or not raw:
        raise ValueError("Manual placeholder evidence must contain at least one room entity.")
    result = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict) or item.get("kind") != "room":
            raise ValueError(f"Manual placeholder entity {index + 1} must be a room.")
        label = str(item.get("label", "")).strip()
        level = str(item.get("level_name", "")).strip()
        area = _number(item.get("area_m2"))
        profile = str(item.get("preliminary_profile_id", "")).strip()
        page = item.get("page")
        try:
            page = int(page)
        except (TypeError, ValueError) as error:
            raise ValueError(f"Manual placeholder room '{label or index + 1}' needs a physical source page.") from error
        if not label or not level or not area or profile not in PROFILE_IDS or page <= 0:
            raise ValueError(
                "Each manual placeholder room needs a label, level, positive area, supported preliminary profile, and source page."
            )
        scope = str(item.get("space_scope", _scope_from_label(label))).strip()
        if scope not in SPACE_SCOPES:
            raise ValueError(f"Manual placeholder room '{label}' has an unsupported preliminary space scope.")
        score = _confidence(item.get("confidence"))
        evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else []
        evidence = [row for row in evidence if isinstance(row, dict) and row.get("page")]
        if not evidence:
            evidence = [{"page": page, "drawing_number": str(item.get("drawing_number", "")).strip(),
                         "excerpt": str(item.get("excerpt", label)).strip() or label}]
        result.append({
            "kind": "room", "label": label, "level_name": level, "area_m2": area,
            "preliminary_profile_id": profile, "confidence": score, "page": page,
            "space_scope": scope,
            "drawing_number": str(item.get("drawing_number", "")).strip(),
            "excerpt": str(item.get("excerpt", label)).strip() or label,
            "evidence": evidence,
            "rationale": str(item.get("rationale", "")).strip(),
            "assumptions": list(item.get("assumptions", [])) if isinstance(item.get("assumptions"), list) else [],
        })
    identities = [_room_identity(row["label"], row["level_name"]) for row in result]
    if len(set(identities)) != len(identities):
        raise ValueError("Manual placeholder rooms must have unique name-and-level identities.")
    return sorted(result, key=lambda row: (_room_identity(row["label"], row["level_name"]), row["page"]))


def _profile_schedule(profile_id):
    active = {"retail": range(9, 18), "office": range(8, 18), "hospitality": range(10, 23), "storage": range(7, 18), "residential": range(6, 23), "generic_conditioned_room": range(8, 18)}[profile_id]
    return [1.0 if hour in active else 0.0 for hour in range(24)]


def _source(profile_id):
    return f"AI preliminary assumption pack {PACK_VERSION}: {profile_id}"


def _schedule(schedule_id, profile_id):
    values = _profile_schedule(profile_id)
    day_profiles = {day: {"values": values, "status": "provisional", "source": _source(profile_id), "citations": []}
                    for day in ("weekday", "saturday", "sunday_holiday")}
    return {"schedule_id": schedule_id, "title": f"Preliminary {profile_id} operating profile", "description": "Controlled preliminary pack schedule; review required.",
            "status": "provisional", "source": _source(profile_id), "citations": [], "day_profiles": day_profiles}


def _scenario(pack):
    scenario = pack["scenario"]
    return {"scenarios": [{
        "scenario_id": "ai_preliminary_cooling_day", "title": "AI preliminary Australian cooling day", "mode": "cooling",
        "representative_month": "January", "day_type": "weekday", "status": "provisional",
        "source": f"AI preliminary assumption pack {pack['version']}", "citations": [],
        "atmospheric_pressure_kpa": {"value": scenario["pressure_kpa"], "status": "provisional", "source": _source("scenario"), "citations": []},
        "hours": [{"hour": hour, "outdoor_dry_bulb_c": {"value": point["db"], "status": "provisional", "source": _source("scenario"), "citations": []},
                   "outdoor_wet_bulb_c": {"value": point["wb"], "status": "provisional", "source": _source("scenario"), "citations": []}}
                  for hour, point in enumerate(scenario["hours"])],
    }]}


def _proposal_evidence(item):
    evidence = _evidence_refs(item)
    page = item.get("page")
    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 0
    if not evidence and page > 0:
        evidence = [{"page": page, "drawing_number": str(item.get("drawing_number", "")).strip(),
                     "excerpt": str(item.get("excerpt", item.get("label", ""))).strip()}]
    return [row for row in evidence if isinstance(row, dict) and _number(row.get("page"))]


def _proposal_id(prefix, item, owner=""):
    anchor = item.get("surface_key") or item.get("opening_key") or item.get("id") or item.get("label") or prefix
    return f"{prefix}-{fingerprint({'anchor': anchor, 'owner': owner, 'page': item.get('page'), 'evidence': _proposal_evidence(item), 'geometry': {key: item.get(key) for key in ('gross_area_m2', 'net_opaque_area_m2', 'opening_area_m2', 'width_m', 'height_m', 'quantity', 'orientation')}})[:16]}"


def validate_placeholder_proposal(raw):
    """Normalise AI/manual envelope proposals without allowing them to alter reviewed inputs.

    Invalid candidate rows are retained as exclusions.  Only a malformed top-level
    payload is rejected, so an AI can provide a useful partial-building estimate.
    """
    if raw in (None, ""):
        raw = {}
    if isinstance(raw, list):  # Legacy room-only manual placeholder payload.
        raw = {"rooms": raw}
    if not isinstance(raw, dict):
        raise ValueError("AI preliminary placeholder proposal must be an object.")
    room_rows = raw.get("rooms", raw.get("entities", []))
    if room_rows and not isinstance(room_rows, list):
        raise ValueError("AI preliminary proposal rooms must be a list.")
    rooms = validate_manual_placeholder_entities(room_rows) if room_rows else []
    result = {"rooms": rooms, "surfaces": [], "openings": [], "issues": []}
    for group, prefix in (("surfaces", "ai-preliminary-surface"), ("openings", "ai-preliminary-opening")):
        rows = raw.get(group, [])
        if not isinstance(rows, list):
            raise ValueError(f"AI preliminary proposal {group} must be a list.")
        for index, raw_item in enumerate(rows, start=1):
            if not isinstance(raw_item, dict):
                result["issues"].append({"component": group[:-1], "reason": f"Candidate {index} is not an object."})
                continue
            item = deepcopy(raw_item)
            item["label"] = str(item.get("label", item.get("surface_key", item.get("opening_key", "")))).strip()
            item["owner_room_label"] = str(item.get("owner_room_label", item.get("room_label", ""))).strip()
            item["owner_level_name"] = str(item.get("owner_level_name", item.get("level_name", ""))).strip()
            item["evidence"] = _proposal_evidence(item)
            item["confidence"] = _confidence(item.get("confidence"))
            item["confidence_band"] = confidence_band(item["confidence"])
            raw_conflicts = item.get("conflicts", [])
            item["conflicts"] = list(raw_conflicts) if isinstance(raw_conflicts, list) else (["Malformed conflict list."] if raw_conflicts not in (None, "") else [])
            item["assumptions"] = list(item.get("assumptions", [])) if isinstance(item.get("assumptions"), list) else []
            item["candidate_id"] = _proposal_id(prefix, item, item["owner_room_label"])
            errors = []
            if not item["owner_room_label"] or not item["owner_level_name"]:
                errors.append("an unambiguous room owner and level")
            if not item["evidence"]:
                errors.append("at least one physical source page")
            if group == "surfaces":
                item["physical_type"] = str(item.get("physical_type", item.get("surface_kind", "wall"))).casefold()
                item["thermal_role"] = str(item.get("thermal_role", item.get("boundary_reference", "external"))).casefold()
                item["external_exposure"] = str(item.get("external_exposure", "external")).casefold()
                item["orientation"] = str(item.get("orientation", "")).upper()
                item["gross_area_m2"] = _number(item.get("gross_area_m2", item.get("area_m2")))
                item["net_opaque_area_m2"] = _number(item.get("net_opaque_area_m2"))
                item["opening_coverage"] = str(item.get("opening_coverage", "not_applicable")).casefold()
                if item["physical_type"] not in OPAQUE_TYPES:
                    errors.append("a supported opaque physical type (wall, roof, or ceiling)")
                if item["thermal_role"] not in {"external", "outside", "outdoors"} or item["external_exposure"] != "external":
                    errors.append("confirmed external thermal exposure")
                if not item["gross_area_m2"]:
                    errors.append("positive gross surface area")
                if item["net_opaque_area_m2"] and item["gross_area_m2"] and item["net_opaque_area_m2"] > item["gross_area_m2"]:
                    errors.append("net opaque area no greater than gross area")
            else:
                item["host_surface_key"] = str(item.get("host_surface_key", item.get("host_surface_id", ""))).strip()
                item["external_exposure"] = str(item.get("external_exposure", "unresolved")).casefold()
                item["orientation"] = str(item.get("orientation", "")).upper()
                item["shading_category"] = str(item.get("shading_category", "unshaded")).casefold()
                item["opening_area_m2"] = _number(item.get("opening_area_m2"))
                width, height, quantity = _number(item.get("width_m")), _number(item.get("height_m")), _number(item.get("quantity", 1))
                if not item["opening_area_m2"] and width and height and quantity:
                    item["opening_area_m2"] = width * height * quantity
                item["explicit_glass_area_m2"] = _number(item.get("explicit_glass_area_m2"))
                if not item["host_surface_key"]:
                    errors.append("a host surface key")
                if item["external_exposure"] not in {"external", "internal"}:
                    errors.append("external or internal exposure decision")
                if not item["opening_area_m2"]:
                    errors.append("positive opening geometry")
                if item["explicit_glass_area_m2"] and item["opening_area_m2"] and item["explicit_glass_area_m2"] > item["opening_area_m2"]:
                    errors.append("glass area no greater than opening area")
                if item["shading_category"] not in SHADING_CATEGORIES:
                    errors.append("a controlled shading category")
                if item["shading_category"] == "unshaded" and "unshaded" not in item["assumptions"]:
                    item["assumptions"].append("unshaded")
            item["validation_errors"] = errors + (["AI reported competing evidence."] if item["conflicts"] else [])
            result[group].append(item)
    for key in ("surfaces", "openings"):
        result[key].sort(key=lambda item: item["candidate_id"])
    return result


def _schedule_with_values(schedule_id, title, values, source):
    return {"schedule_id": schedule_id, "title": title, "description": "Controlled preliminary pack schedule; review required.",
            "status": "provisional", "source": source, "citations": [],
            "day_profiles": {day: {"values": values, "status": "provisional", "source": source, "citations": []}
                             for day in ("weekday", "saturday", "sunday_holiday")}}


def _find_owner(rows, label, level):
    candidates = [row for row in rows if row["name"].casefold() == label.casefold() and row["level"].casefold() == level.casefold()]
    return candidates[0] if len(candidates) == 1 else None


def _legacy_surface_candidates(vision, rows):
    entities = vision.get("result", {}).get("auto_extraction", {}).get("entities", []) if isinstance(vision, dict) else []
    output = []
    for entity in entities:
        if not isinstance(entity, dict) or entity.get("kind") != "surface":
            continue
        label = str(entity.get("label", ""))
        owner = next((row for row in rows if row["name"].casefold() in label.casefold()), None)
        if not owner:
            continue
        output.append({"candidate_id": _proposal_id("legacy-surface", entity, owner["name"]), "label": label,
                       "owner_room_label": owner["name"], "owner_level_name": owner["level"],
                       "physical_type": str(entity.get("surface_kind", "wall")).casefold(),
                       "thermal_role": str(entity.get("boundary_reference", "external")).casefold(),
                       "external_exposure": "external", "orientation": str(entity.get("orientation", "")).upper(),
                       "gross_area_m2": _number(entity.get("area_m2")), "net_opaque_area_m2": None,
                       "opening_coverage": "not_applicable", "confidence": _confidence(entity.get("confidence")),
                       "confidence_band": confidence_band(_confidence(entity.get("confidence"))),
                       "evidence": _proposal_evidence(entity), "assumptions": [], "conflicts": [], "validation_errors": []})
    return output


def assemble(building, vision=None, contractor_overrides=None, source_fingerprints=None, manual_placeholder_entities=None, preliminary_proposal=None):
    """Return a materialized preliminary payload plus its transparent ledger."""
    pack = load_pack()
    overrides = contractor_overrides or {}
    rooms, zones, floors, ledger, exclusions, schedules = [], [], [], [], [], []
    floor_ids, zone_ids = {}, set()
    requirements_zones = []
    proposal = validate_placeholder_proposal(preliminary_proposal if preliminary_proposal is not None else (manual_placeholder_entities or {}))
    manual_entities = proposal["rooms"]
    space_rows = _space_rows(building, vision, manual_entities)
    active_rows, excluded_spaces = [], []
    for row in space_rows:
        row["scope"] = row["scope"] if row["scope"] in SPACE_SCOPES else "unresolved_scope"
        if row["scope"] in {"refrigeration_process", "unresolved_scope"}:
            reason = ("Refrigeration load required; this room is excluded from the comfort-HVAC subtotal."
                      if row["scope"] == "refrigeration_process" else
                      "AI could not resolve whether this room belongs in comfort-HVAC scope.")
            excluded_spaces.append({"room_name": row["name"], "level": row["level"], "scope": row["scope"], "reason": reason,
                                    "evidence": row["evidence"]})
            exclusions.append({"room_id": f"room-{row['key']}", "component": "room scope", "reason": reason})
            continue
        active_rows.append(row)
        profile_id = row["profile_id"] if row["profile_id"] in PROFILE_IDS else _profile_from_label(row["name"])
        profile = pack["profiles"][profile_id]
        override = overrides.get(row["key"], {}) if isinstance(overrides, dict) else {}
        area = _number(override.get("area_m2")) or row["area_m2"] or profile["fallback_area_m2"]
        area_origin = "contractor_override" if _number(override.get("area_m2")) else "pdf_evidence" if row["area_m2"] else "ai_assumption"
        score = row["confidence"] if row["area_m2"] else min(row["confidence"], 0.4)
        level_key = _slug(row["level"])
        floor_id = floor_ids.setdefault(level_key, f"floor-{level_key}")
        if not any(item["floor_id"] == floor_id for item in floors):
            floors.append({"floor_id": floor_id, "name": row["level"], "elevation_m": None, "verification_status": "provisional", "source": _source("topology"), "citations": []})
        room_id = f"room-{row['key']}"
        zone_id = f"zone-{row['key']}"
        zone_ids.add(zone_id)
        occupancy = max(1, round(area * profile["occupancy_density_people_m2"]))
        schedule_id = f"prelim-{profile_id}"
        if not any(item["schedule_id"] == schedule_id for item in schedules):
            schedules.append(_schedule(schedule_id, profile_id))
        cooling = {"people_sensible_w_per_person": profile["people_sensible_w"], "people_latent_w_per_person": profile["people_latent_w"],
                   "people_diversity_factor": profile["people_diversity"], "lighting_w_m2": profile["lighting_w_m2"],
                   "lighting_diversity_factor": profile["lighting_diversity"],
                   "outside_air_lps": round(occupancy * profile["outside_air_lps_person"] + area * profile["outside_air_lps_m2"], 3),
                   "safety_factor": pack["scenario"]["safety_factor"], "envelope_not_applicable": True,
                   "verification_status": "provisional", "source": _source(profile_id), "envelope_surfaces": [], "glazing_surfaces": []}
        requirements_zones.append({"zone_id": zone_id, "name": row["name"], "usage": profile["label"], "source_room_labels": [row["name"]],
                                   "area_m2": area, "occupancy": occupancy, "ceiling_height_mm": 2700,
                                   "heat_sources": [{"name": "Preliminary profile equipment", "quantity": 1, "watts": round(area * profile["equipment_w_m2"], 3), "kind": "other", "diversity_factor": profile["equipment_diversity"], "space_gain_factor": profile["equipment_space_gain"], "verification_status": "provisional", "source": _source(profile_id)}],
                                   "cooling_load": cooling})
        zones.append({"zone_id": zone_id, "name": row["name"], "floor_id": floor_id, "ceiling_height_mm": 2700,
                      "verification_status": "provisional", "source": _source("topology"), "citations": []})
        ledger.extend([
            {"room_id": room_id, "field": "area_m2", "value": area, "origin": area_origin, "profile_id": profile_id, "confidence": score, "confidence_band": confidence_band(score), "rationale": "PDF/AI geometry when available; controlled profile fallback otherwise.", "evidence": row["evidence"]},
            {"room_id": room_id, "field": "room_profile", "value": profile_id, "origin": "ai_profile", "profile_id": profile_id, "confidence": row["confidence"], "confidence_band": confidence_band(row["confidence"]), "rationale": "AI proposal when valid; otherwise label-based controlled profile selection.", "evidence": row["evidence"]},
            {"room_id": room_id, "field": "space_scope", "value": row["scope"], "origin": "ai_profile", "profile_id": profile_id, "confidence": row["confidence"], "confidence_band": confidence_band(row["confidence"]), "rationale": "AI preliminary scope classification.", "evidence": row["evidence"]},
        ])
        exclusions.extend({"room_id": room_id, "component": component, "reason": reason} for component, reason in (
            ("opaque envelope", "No structurally valid surface geometry and boundary area was available for the preliminary model."),
            ("glazing and façade solar", "No resolved reviewed/AI opening geometry, external exposure, orientation, and cited property set was available."),
            ("infiltration", "Preliminary profile airflow is not passed through the approved infiltration method gate."),
        ))
        if row["scope"] == "comfort_hvac_with_process_exception":
            exclusions.extend({"room_id": room_id, "component": component, "reason": reason} for component, reason in (
                ("process exhaust", "Kitchen process exhaust remains a project-specific preliminary exclusion."),
                ("steam and named equipment", "Steam and named equipment heat remain explicit project-specific exclusions."),
            ))
    requirements_raw = {"space_usage": "AI preliminary multi-room project", "occupancy": None, "operating_hours": "Controlled preliminary profiles", "indoor_cooling_setpoint_c": pack["scenario"]["indoor_dry_bulb_c"], "outdoor_summer_db_c": max(point["db"] for point in pack["scenario"]["hours"]),
                        "cooling_load_conditions": {"indoor_cooling_wet_bulb_c": pack["scenario"]["indoor_wet_bulb_c"], "outdoor_summer_wet_bulb_c": max(point["wb"] for point in pack["scenario"]["hours"]), "atmospheric_pressure_kpa": pack["scenario"]["pressure_kpa"], "verification_status": "provisional", "source": _source("scenario")},
                        "zones": requirements_zones,
                        "verification": {key: {"status": "provisional", "source": _source("scenario")} for key in ("occupancy", "design_conditions", "outside_air", "exhaust", "heat_sources", "ceiling", "existing_services")}}
    requirements = validate_design_requirements(requirements_raw)
    model = build_hourly_load_model(requirements)
    model["floors"], model["zones"] = floors, zones
    room_rows = {}
    for room, row in zip(model["rooms"], active_rows):
        profile_id = next(item["profile_id"] for item in ledger if item["room_id"] == f"room-{row['key']}" and item["field"] == "room_profile")
        profile = pack["profiles"][profile_id]
        room.update({"room_id": f"room-{row['key']}", "name": row["name"], "zone_id": f"zone-{row['key']}", "source_zone_id": f"zone-{row['key']}",
                     "mapping_status": "confirmed", "verification_status": "provisional", "source": _source("topology"), "citations": [], "source_room_labels": [row["name"]], "ceiling_height_mm": 2700})
        room["schedule_assignments"] = {"people": f"prelim-{profile_id}", "lighting": f"prelim-{profile_id}", "outside_air": f"prelim-{profile_id}", "infiltration": "", "equipment": {source["source_id"]: f"prelim-{profile_id}" for source in room["heat_sources"]}, "solar": {}}
        for component in room["unapproved_components"]:
            component.update({"value": None, "unit": "", "source_room_id": "", "source": "Excluded from AI preliminary model pending a project-specific method.", "citations": [], "verification_status": "confirmed", "calculation_status": "not_present_confirmed"})
        room_rows[_room_identity(row["name"], row["level"])] = {"room": room, "row": row, "profile": profile, "profile_id": profile_id}

    # A legacy vision surface remains useful as a backwards-compatible
    # proposal, but new integrations pass the richer surface/opening proposal.
    surface_candidates = list(proposal["surfaces"]) or _legacy_surface_candidates(vision or {}, active_rows)
    opening_candidates = proposal["openings"]
    surface_index, accepted_surface_ids, accepted_opening_ids = {}, set(), set()
    surface_summary = {"discovered": len(surface_candidates), "included": 0, "blocked": 0, "excluded": 0,
                       "openings_discovered": len(opening_candidates), "openings_included": 0, "openings_excluded": 0}
    for issue in proposal["issues"]:
        exclusions.append({"room_id": "", **issue})
    for candidate in surface_candidates:
        owner = _find_owner(active_rows, candidate.get("owner_room_label", ""), candidate.get("owner_level_name", ""))
        errors = list(candidate.get("validation_errors", []))
        if owner is None:
            errors.append("owner is not an included comfort-HVAC room")
        if errors:
            surface_summary["blocked"] += 1
            exclusions.append({"room_id": f"room-{owner['key']}" if owner else "", "component": "opaque envelope", "reason": "; ".join(errors), "candidate_id": candidate.get("candidate_id", "")})
            continue
        target = room_rows[_room_identity(owner["name"], owner["level"])]
        candidate = deepcopy(candidate)
        candidate["owner_room_id"] = target["room"]["room_id"]
        surface_index[str(candidate.get("surface_key", candidate["candidate_id"]))] = candidate
    openings_by_host = {}
    for candidate in opening_candidates:
        owner = _find_owner(active_rows, candidate.get("owner_room_label", ""), candidate.get("owner_level_name", ""))
        errors = list(candidate.get("validation_errors", []))
        host = surface_index.get(candidate.get("host_surface_key", ""))
        if owner is None:
            errors.append("owner is not an included comfort-HVAC room")
        if host is None:
            errors.append("host surface is unresolved or excluded")
        elif owner and host.get("owner_room_id") != room_rows[_room_identity(owner["name"], owner["level"])]["room"]["room_id"]:
            errors.append("host surface belongs to another room")
        if candidate.get("external_exposure") == "internal":
            errors.append("internal glazing has no external conduction or façade solar")
        if errors:
            surface_summary["openings_excluded"] += 1
            exclusions.append({"room_id": f"room-{owner['key']}" if owner else "", "component": "glazing and façade solar", "reason": "; ".join(errors), "candidate_id": candidate.get("candidate_id", "")})
            continue
        openings_by_host.setdefault(candidate["host_surface_key"], []).append(candidate)

    envelope = pack["preliminary_envelope"]
    for host_key, candidate in surface_index.items():
        target = next(value for value in room_rows.values() if value["room"]["room_id"] == candidate["owner_room_id"])
        room, row, profile, profile_id = target["room"], target["row"], target["profile"], target["profile_id"]
        openings = openings_by_host.get(host_key, [])
        gross = candidate["gross_area_m2"]
        net = candidate.get("net_opaque_area_m2")
        if openings and candidate.get("opening_coverage") != "complete":
            surface_summary["blocked"] += 1
            exclusions.append({"room_id": room["room_id"], "component": "opaque envelope", "candidate_id": candidate["candidate_id"],
                               "reason": "Opening coverage is incomplete; opaque host area is excluded to prevent wall/window double counting."})
            continue
        if openings:
            net = gross - sum(item["opening_area_m2"] for item in openings)
        net = net if net is not None else gross
        if net <= 0:
            surface_summary["blocked"] += 1
            exclusions.append({"room_id": room["room_id"], "component": "opaque envelope", "candidate_id": candidate["candidate_id"], "reason": "Net opaque area is not positive after opening coverage."})
            continue
        physical = candidate["physical_type"]
        orientation = candidate.get("orientation", "") if candidate.get("orientation", "") in CARDINALS else "horizontal" if physical in {"roof", "ceiling"} else ""
        solar_profile = envelope["cardinal_solar_profiles_w_m2"].get(orientation, [0] * 24)
        solar_peak = max(solar_profile)
        solar_schedule_id = f"prelim-solar-{candidate['candidate_id']}"
        if solar_peak:
            operating = _profile_schedule(profile_id)
            schedules.append(_schedule_with_values(solar_schedule_id, f"Preliminary {orientation} façade solar profile", [round(operating[hour] * solar_profile[hour] / solar_peak, 6) for hour in range(24)], _source(f"solar-{orientation}")))
            room["schedule_assignments"]["solar"][candidate["candidate_id"]] = solar_schedule_id
        opaque = {"surface_id": candidate["candidate_id"], "kind": "roof" if physical == "roof" else "ceiling" if physical == "ceiling" else "opaque_wall",
                  "orientation": orientation, "area_m2": net, "u_value_w_m2k": profile["roof_u_w_m2k"] if physical in {"roof", "ceiling"} else profile["wall_u_w_m2k"],
                  "solar_design_w_m2": solar_peak, "solar_gain_factor": envelope["opaque_solar_gain_factor"] if solar_peak else 0,
                  "shading_factor": 1, "boundary_method": "external", "boundary_temperature_c": None, "construction_id": "",
                  "verification_status": "provisional", "source": _source(profile_id) + "; AI preliminary surface classification",
                  "preliminary_assumption": True, "source_pages": candidate["evidence"], "orientation": orientation}
        room["cooling_load"]["envelope_not_applicable"] = False
        room["cooling_load"]["envelope_surfaces"].append(opaque)
        accepted_surface_ids.add(candidate["candidate_id"])
        surface_summary["included"] += 1
        exclusions[:] = [item for item in exclusions if not (item.get("room_id") == room["room_id"] and item.get("component") == "opaque envelope" and "No structurally valid" in item.get("reason", ""))]
        ledger.append({"room_id": room["room_id"], "field": "envelope_surface", "value": net, "origin": "ai_geometry", "profile_id": profile_id,
                       "confidence": candidate["confidence"], "confidence_band": candidate["confidence_band"], "rationale": "AI preliminary external surface; controlled profile U-value and solar basis.", "evidence": candidate["evidence"], "surface_id": candidate["candidate_id"]})
        for opening in openings:
            shade = envelope["shading_categories"][opening["shading_category"]]
            opening_orientation = opening.get("orientation") if opening.get("orientation") in CARDINALS else orientation if orientation in CARDINALS else ""
            glazing_profile = envelope["cardinal_solar_profiles_w_m2"].get(opening_orientation, [0] * 24)
            glazing_peak = max(glazing_profile)
            solar_schedule_id = f"prelim-solar-{opening['candidate_id']}"
            if glazing_peak:
                operating = _profile_schedule(profile_id)
                schedules.append(_schedule_with_values(solar_schedule_id, f"Preliminary {opening_orientation} glazing solar profile", [round(operating[hour] * glazing_profile[hour] / glazing_peak, 6) for hour in range(24)], _source(f"solar-{opening_orientation}")))
                room["schedule_assignments"]["solar"][opening["candidate_id"]] = solar_schedule_id
            glazing = {"surface_id": opening["candidate_id"], "owner_room_id": room["room_id"], "owner_zone_id": room["zone_id"],
                       "host_surface_id": candidate["candidate_id"], "opening_mapping_status": "proposed", "geometry_mode": "preliminary_ai_estimate",
                       "review_status": "provisional", "verification_status": "provisional", "boundary_method": "external", "external_exposure": "external",
                       "explicit_opening_area_m2": opening["opening_area_m2"], "explicit_glass_area_m2": opening.get("explicit_glass_area_m2"),
                       "window": {"record_id": f"window-{opening['candidate_id']}", "u_value_w_m2k": profile["glazing_u_w_m2k"], "shgc": profile["shgc"],
                                  "frame_fraction": envelope["glazing_frame_fraction"], "glass_area_correction": envelope["glazing_glass_area_correction"],
                                  "internal_shading_factor": envelope["glazing_internal_shading_factor"]},
                       "manual_solar": {"enabled": bool(glazing_peak), "incident_solar_w_m2": glazing_peak, "external_shading_factor": shade},
                       "solar_basis": "ai_preliminary_cardinal_profile", "preliminary_solar_profile_w_m2": glazing_profile,
                       "orientation": opening_orientation, "shading_category": opening["shading_category"], "preliminary_assumption": True,
                       "source": _source(profile_id) + "; AI preliminary opening classification", "citations": [], "source_pages": opening["evidence"]}
            room["cooling_load"]["glazing_surfaces"].append(glazing)
            accepted_opening_ids.add(opening["candidate_id"])
            surface_summary["openings_included"] += 1
            exclusions[:] = [item for item in exclusions if not (item.get("room_id") == room["room_id"] and item.get("component") == "glazing and façade solar" and "No resolved" in item.get("reason", ""))]
            if not opening_orientation:
                exclusions.append({"room_id": room["room_id"], "component": "glazing solar", "candidate_id": opening["candidate_id"], "reason": "Opening orientation is unresolved; preliminary glazing conduction is included but façade solar is excluded."})
            if opening["shading_category"] == "unshaded":
                exclusions.append({"room_id": room["room_id"], "component": "shading review", "candidate_id": opening["candidate_id"], "reason": "Unknown façade shading uses the explicit conservative unshaded preliminary assumption."})
            ledger.append({"room_id": room["room_id"], "field": "glazing_opening", "value": opening["opening_area_m2"], "origin": "ai_geometry", "profile_id": profile_id,
                           "confidence": opening["confidence"], "confidence_band": opening["confidence_band"], "rationale": "AI preliminary glazing with controlled U-value, SHGC, directional solar, and shading category.", "evidence": opening["evidence"], "surface_id": opening["candidate_id"]})
    surface_summary["excluded"] = surface_summary["discovered"] - surface_summary["included"] - surface_summary["blocked"]
    model["updated_at"] = now()
    model["source_requirements_updated_at"] = requirements["updated_at"]
    material = {"requirements": requirements, "schedule_library": {"schema_version": 1, "updated_at": now(), "schedules": schedules}, "design_day_scenarios": _scenario(pack), "hourly_load_model": model,
                "preliminary_policy": {"pack_version": pack["version"], "mode": "ai_preliminary", "surface_ids": sorted(accepted_surface_ids), "opening_ids": sorted(accepted_opening_ids)}}
    dependency_fingerprints = dict(source_fingerprints or {})
    dependency_fingerprints.update({"preliminary_pack": fingerprint(pack), "vision_response": fingerprint(vision or {}), "building_evidence": fingerprint(building or {}), "contractor_overrides": fingerprint(overrides), "manual_placeholder_entities": fingerprint(manual_entities), "ai_preliminary_proposal": fingerprint(proposal)})
    input_fingerprint = content_fingerprint({"material": material, "ledger": ledger, "exclusions": exclusions, "dependencies": dependency_fingerprints})
    return {"schema_version": 1, "status": "draft", "label": "AI preliminary estimate — not engineering reviewed or validated", "created_at": now(), "input_fingerprint": input_fingerprint,
            "pack": {"pack_id": pack["pack_id"], "version": pack["version"], "fingerprint": fingerprint(pack)}, "dependency_fingerprints": dependency_fingerprints,
            "material": material, "materialized_fields": ledger, "exclusions": exclusions, "excluded_spaces": excluded_spaces,
            "surface_summary": surface_summary, "proposal": proposal,
            "review_queue": sorted([item for item in ledger if item["confidence"] < 0.8], key=lambda item: (item["confidence"], item["room_id"], item["field"]))}


def calculate(input_set):
    material = input_set["material"]
    report = calculate_hourly_load_report(material["requirements"], material["schedule_library"], material["design_day_scenarios"], material["hourly_load_model"], ["ai_preliminary_cooling_day"], preliminary_policy=material.get("preliminary_policy"))
    report["report_type"] = "hourly_ai_preliminary_cooling_load"
    report["status"] = "draft"
    report["project_peak"] = {}
    report["label"] = "AI preliminary estimate — not engineering reviewed or validated"
    report["preliminary_input_set_fingerprint"] = input_set["input_fingerprint"]
    report["assumption_coverage"] = {"field_count": len(input_set["materialized_fields"]), "low_confidence_count": len(input_set["review_queue"]), "excluded_component_count": len(input_set["exclusions"])}
    report["preliminary_surface_summary"] = deepcopy(input_set.get("surface_summary", {}))
    report["refrigeration_process_exclusions"] = deepcopy(input_set.get("excluded_spaces", []))
    report["review_queue"] = deepcopy(input_set["review_queue"])
    report["excluded_components"] = sorted(set(report.get("excluded_components", []) + [item["component"] for item in input_set["exclusions"]]))
    report["input_fingerprints"].update(input_set["dependency_fingerprints"])
    report["input_fingerprints"]["ai_preliminary_input_set"] = input_set["input_fingerprint"]
    return report
