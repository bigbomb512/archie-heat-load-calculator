"""Draft AI resolution of AHU systems and air-side inputs.

This module is deliberately separate from :mod:`ai.ahu_airside`.  It turns
source-linked AI proposals into a normalized, auditable artifact and can
materialize an isolated preliminary model.  The reviewed AHU artifacts remain
the authority for engineering calculations.
"""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import re


SCHEMA_VERSION = 1
SYSTEM_TYPES = {"single_zone_constant_volume", "vav"}
PATH_TYPES = {"supply", "return", "outside_air", "exhaust", "relief", "make_up", "transfer", "leakage"}
NODE_TYPES = {"outside_air", "return_air", "mixed_air", "supply_air", "room_return", "exhaust_air", "relief_air", "make_up_air"}
COMPONENT_TYPES = {"fans", "duct_effects", "leakage", "heat_recovery", "preconditioning", "coils"}
STATUSES = {"proposed", "provisional", "resolved", "needs_review", "blocked", "excluded", "stale"}
ORIGINS = {"project_evidence", "ai_evidence", "released_source_pack", "research_candidate", "contractor_override", "controlled_fallback", "unresolved"}
DAY_TYPES = ("weekday", "saturday", "sunday", "holiday")


def now():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")).hexdigest()


def empty_ahu_resolution():
    result = {
        "schema_version": SCHEMA_VERSION, "systems": [], "airflow_records": [],
        "fans": [], "duct_effects": [], "leakage": [], "heat_recovery": [],
        "preconditioning": [], "coils": [], "issues": [], "overrides": [],
        "source_fingerprints": {}, "status": "needs_review", "updated_at": "",
    }
    result["fingerprint"] = fingerprint(result)
    return result


def _text(value):
    return str(value or "").strip()


def _number(value, *, positive=False):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or (positive and value <= 0):
        return None
    return value


def _confidence(value, fallback=.4):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
        return max(0.0, min(1.0, float(value)))
    return {"high": .85, "medium": .65, "low": .4}.get(_text(value).casefold(), fallback)


def confidence_band(value):
    return "high" if value >= .8 else "medium" if value >= .5 else "low"


def _slug(value):
    return re.sub(r"[^a-z0-9]+", "-", _text(value).casefold()).strip("-") or "unassigned"


def stable_id(kind, source_fingerprint, tag="", page="", anchor="", owner=""):
    seed = [kind, source_fingerprint, _text(tag), _text(page), _text(anchor), _text(owner)]
    return f"{kind}_{fingerprint(seed)[:18]}"


def _citation_rows(item):
    raw = item.get("evidence", item.get("citations", [])) if isinstance(item, dict) else []
    if isinstance(raw, dict):
        raw = [raw]
    rows = []
    for row in raw or []:
        if not isinstance(row, dict):
            continue
        page = row.get("page")
        if isinstance(page, bool):
            continue
        if page is not None:
            try:
                page = int(page)
            except (TypeError, ValueError):
                continue
        rows.append({"page": page, "drawing_number": _text(row.get("drawing_number")), "reference": _text(row.get("reference")), "excerpt": _text(row.get("excerpt"))})
    if not rows and isinstance(item, dict) and item.get("page") is not None:
        rows.append({"page": item.get("page"), "drawing_number": _text(item.get("drawing_number")), "reference": _text(item.get("source", "AI evidence")), "excerpt": _text(item.get("excerpt", item.get("label", "")))})
    return rows


def _origin(item, fallback="unresolved"):
    raw = _text(item.get("origin")) if isinstance(item, dict) else ""
    if raw in ORIGINS:
        return raw
    if isinstance(item, dict) and item.get("contractor_override"):
        return "contractor_override"
    if isinstance(item, dict) and item.get("research_candidate"):
        return "research_candidate"
    # A source label alone is not a citation.  Numeric AI observations remain
    # unresolved until they carry a page/reference or another explicit source
    # record that can be audited.
    if _citation_rows(item):
        return "ai_evidence" if item.get("ai_generated", True) else "project_evidence"
    return fallback


def _flow_lps(item):
    if not isinstance(item, dict):
        return None, {}
    if item.get("flow_lps") is not None or item.get("airflow_lps") is not None:
        key = "flow_lps" if item.get("flow_lps") is not None else "airflow_lps"
        value = _number(item.get(key))
        return (value if value is not None and value >= 0 else None), {"value": item.get(key), "unit": "L/s", "conversion": "identity"}
    value = item.get("airflow_value", item.get("value", item.get("flow")))
    value = _number(value)
    if value is None or value < 0:
        return None, {}
    unit = _text(item.get("airflow_unit", item.get("unit", "L/s"))).casefold().replace("³", "3").replace(" ", "")
    factors = {"l/s": 1.0, "ls": 1.0, "m3/s": 1000.0, "m3s": 1000.0, "m3/h": 1000.0 / 3600.0, "m3h": 1000.0 / 3600.0}
    if unit not in factors:
        return None, {"value": value, "unit": item.get("airflow_unit", item.get("unit", "")), "conversion": "unsupported"}
    return value * factors[unit], {"value": value, "unit": item.get("airflow_unit", item.get("unit", "L/s")), "conversion": f"multiply by {factors[unit]} to L/s"}


def _schedule(value, fallback=None):
    fallback = fallback or [1.0] * 24
    source = value if isinstance(value, dict) else {}
    result = {}
    for day in DAY_TYPES:
        values = source.get(day, fallback) if isinstance(source, dict) else fallback
        if not isinstance(values, list) or len(values) != 24:
            values = fallback
        if any(_number(item) is None or not 0 <= float(item) <= 1 for item in values):
            values = fallback
        result[day] = [float(item) for item in values]
    return result


def _state(item):
    if not isinstance(item, dict):
        return {}
    state = {}
    for key in ("dry_bulb_c", "wet_bulb_c", "pressure_kpa"):
        value = _number(item.get(key))
        if value is not None:
            state[key] = value
    return state


def _profile(pack, system_type):
    profiles = (pack or {}).get("ahu_profiles", {}) if isinstance(pack, dict) else {}
    return profiles.get(system_type) or profiles.get("default") or {}


def _candidate_sources(vision, proposal):
    candidates = []
    if isinstance(vision, dict):
        candidates.extend(vision.get("air_side_candidates", []))
        result = vision.get("result", {})
        if isinstance(result, dict):
            auto = result.get("auto_extraction", {})
            candidates.extend(auto.get("air_side_candidates", []) if isinstance(auto, dict) else [])
    if isinstance(proposal, dict):
        candidates.extend(proposal.get("air_side_candidates", []))
        candidates.extend(proposal.get("ahu_systems", []))
    return [item for item in candidates if isinstance(item, dict)]


def _normalize_path_type(value):
    aliases = {"outside": "outside_air", "oa": "outside_air", "makeup": "make_up", "make_up_air": "make_up", "transfer_air": "transfer", "return_air": "return"}
    value = _text(value).casefold()
    return aliases.get(value, value)


def _normalize_node(value, path_type, source=True):
    value = _text(value).casefold()
    aliases = {"outside": "outside_air", "oa": "outside_air", "return": "return_air", "room": "room_return", "supply": "supply_air", "exhaust": "exhaust_air", "relief": "relief_air", "make_up": "make_up_air", "makeup": "make_up_air"}
    value = aliases.get(value, value)
    if value in NODE_TYPES:
        return value
    defaults = {"outside_air": ("outside_air", "mixed_air"), "return": ("room_return", "return_air"), "supply": ("mixed_air", "supply_air"), "exhaust": ("room_return", "exhaust_air"), "relief": ("return_air", "relief_air"), "make_up": ("outside_air", "mixed_air"), "transfer": ("room_return", "room_return"), "leakage": ("outside_air", "mixed_air")}
    return defaults.get(path_type, ("outside_air", "mixed_air"))[0 if source else 1]


def _component_status(origin, value, unresolved):
    if unresolved:
        return "needs_review"
    if origin == "unresolved":
        return "needs_review"
    if origin in {"controlled_fallback", "research_candidate", "ai_evidence"}:
        return "provisional"
    return "resolved"


def resolve(vision=None, proposal=None, known_zone_ids=None, known_room_ids=None, airflow_resolution=None, pack=None, source_fingerprints=None, existing=None):
    """Normalize current AI/system proposals into ``ahu_resolution.json``."""
    existing = existing if isinstance(existing, dict) else empty_ahu_resolution()
    source_fingerprints = deepcopy(source_fingerprints or {})
    candidates = _candidate_sources(vision, proposal)
    systems, paths, components, issues = [], [], {key: [] for key in COMPONENT_TYPES}, []
    known_zone_ids, known_room_ids = set(known_zone_ids or []), set(known_room_ids or [])
    seen_ids, assigned_zones = set(), {}
    for candidate in candidates:
        tag = _text(candidate.get("ahu_id", candidate.get("tag", candidate.get("system_id", ""))))
        system_type = _text(candidate.get("system_type", candidate.get("type", ""))).casefold()
        if system_type in {"constant_volume", "single_zone_cv", "cv"}:
            system_type = "single_zone_constant_volume"
        elif system_type in {"variable_air_volume", "variable_volume", "vav"}:
            system_type = "vav"
        page = candidate.get("page", (candidate.get("source_pages") or [""])[0] if isinstance(candidate.get("source_pages"), list) else "")
        source_fp = source_fingerprints.get("vision_response", "")
        ahu_id = tag if tag and re.fullmatch(r"[a-z][a-z0-9_-]*", tag) else stable_id("ahu", source_fp, tag, page, candidate.get("geometry_key", candidate.get("location", "")), candidate.get("owner_zone_id", ""))
        if ahu_id in seen_ids:
            issues.append({"type": "duplicate_ahu", "ahu_id": ahu_id, "remediation": "Confirm whether the sightings describe one physical AHU or separate systems."})
            continue
        seen_ids.add(ahu_id)
        confidence = _confidence(candidate.get("confidence_score", candidate.get("confidence")))
        evidence = _citation_rows(candidate)
        origin = _origin(candidate, "unresolved")
        zones = list(dict.fromkeys([_text(value) for value in candidate.get("served_zone_ids", candidate.get("zone_ids", [])) if _text(value)]))
        rooms = list(dict.fromkeys([_text(value) for value in candidate.get("served_room_ids", candidate.get("room_ids", [])) if _text(value)]))
        if not zones and rooms:
            zones = [f"zone:{_slug(value)}" for value in rooms]
        unresolved = []
        if system_type not in SYSTEM_TYPES:
            unresolved.append("system type is unresolved or unsupported")
        if not zones:
            unresolved.append("served zone ownership is unresolved")
        unknown_zones = sorted(set(zones) - known_zone_ids) if known_zone_ids else []
        if unknown_zones:
            unresolved.append("unknown served zone IDs: " + ", ".join(unknown_zones))
        unknown_rooms = sorted(set(rooms) - known_room_ids) if known_room_ids else []
        if unknown_rooms:
            unresolved.append("unknown served room IDs: " + ", ".join(unknown_rooms))
        if system_type == "single_zone_constant_volume" and len(zones) != 1:
            unresolved.append("constant-volume AHU must serve exactly one zone")
        for zone in zones:
            if zone in assigned_zones and assigned_zones[zone] != ahu_id:
                unresolved.append(f"zone {zone} is assigned to both {assigned_zones[zone]} and {ahu_id}")
                issues.append({"type": "duplicate_zone_owner", "zone_id": zone, "ahu_ids": sorted({assigned_zones[zone], ahu_id}), "remediation": "Confirm the AHU serving this zone."})
            assigned_zones[zone] = ahu_id
        status = "blocked" if unresolved else "needs_review" if origin == "unresolved" else "provisional" if origin in {"ai_evidence", "controlled_fallback", "research_candidate"} else "resolved"
        systems.append({
            "ahu_id": ahu_id, "name": _text(candidate.get("name", candidate.get("label", ahu_id))) or ahu_id,
            "system_type": system_type, "number_off": int(_number(candidate.get("number_off"), positive=True) or 1),
            "served_zone_ids": zones, "served_room_ids": rooms, "review_status": status,
            "status": status, "origin": origin, "confidence": round(confidence, 4), "confidence_band": confidence_band(confidence),
            "source": _text(candidate.get("source", "AI mechanical evidence")), "citations": evidence,
            "source_pages": sorted({row["page"] for row in evidence if isinstance(row.get("page"), int)}),
            "rationale": _text(candidate.get("rationale", "AI proposed AHU/system topology.")),
            "profile_id": f"{(pack or {}).get('ahu_profile_pack_version', 'au-preliminary-v4')}-{system_type}" if origin == "controlled_fallback" else "",
            "preliminary_assumption": origin == "controlled_fallback",
            "assumptions": list(candidate.get("assumptions", [])) if isinstance(candidate.get("assumptions", []), list) else [],
            "conflicts": list(candidate.get("conflicts", [])) if isinstance(candidate.get("conflicts", []), list) else [],
            "unresolved_fields": sorted(set(unresolved + [str(value) for value in candidate.get("unresolved_fields", [])])),
            "remediation": "Resolve AHU/system ownership and cite the mechanical drawing or schedule." if unresolved else "",
        })
        profile = _profile(pack, system_type)
        raw_paths = candidate.get("airflow_records", candidate.get("air_paths", candidate.get("paths", [])))
        for raw in raw_paths if isinstance(raw_paths, list) else []:
            if not isinstance(raw, dict):
                continue
            path_type = _normalize_path_type(raw.get("path_type", raw.get("type")))
            path_unresolved = []
            if path_type not in PATH_TYPES:
                path_unresolved.append("unsupported airflow path type")
            value, conversion = _flow_lps(raw)
            path_origin = _origin(raw, origin if origin != "unresolved" else "unresolved")
            fallback_value = profile.get("airflow_lps", {}).get(path_type) if isinstance(profile.get("airflow_lps"), dict) else None
            if value is None and fallback_value is not None:
                value, conversion, path_origin = _number(fallback_value, positive=True), {"value": fallback_value, "unit": "L/s", "conversion": "controlled profile"}, "controlled_fallback"
            if value is None:
                path_unresolved.append("airflow value is unresolved")
            zone_id = _text(raw.get("zone_id", raw.get("owner_zone_id")))
            room_id = _text(raw.get("room_id", raw.get("owner_room_id")))
            if zone_id and known_zone_ids and zone_id not in known_zone_ids:
                path_unresolved.append("airflow path references an unknown zone")
            evidence = _citation_rows(raw) or _citation_rows(candidate)
            path_id = stable_id("airpath", source_fp, raw.get("tag", raw.get("path_id", path_type)), raw.get("page", page), raw.get("geometry_key", raw.get("location", "")), f"{ahu_id}|{zone_id}|{room_id}")
            status = "blocked" if path_unresolved else _component_status(path_origin, value, path_unresolved)
            paths.append({
                "path_id": path_id, "ahu_id": ahu_id, "zone_id": zone_id, "room_id": room_id,
            "path_type": path_type, "source_node": _normalize_node(raw.get("source_node"), path_type, True),
                "destination_node": _normalize_node(raw.get("destination_node"), path_type, False),
                "flow_lps": value, "flow_basis": conversion, "schedule": _schedule(raw.get("schedule"), profile.get("schedule")),
                "state": _state(raw.get("state")), "origin": path_origin, "status": status,
                "review_status": "confirmed" if status == "resolved" else "provisional" if status == "provisional" else "missing",
                "confidence": round(_confidence(raw.get("confidence_score", raw.get("confidence")), confidence), 4),
                "confidence_band": confidence_band(_confidence(raw.get("confidence_score", raw.get("confidence")), confidence)),
                "source": _text(raw.get("source", candidate.get("source", "AI mechanical evidence"))), "citations": evidence,
                "rationale": _text(raw.get("rationale", "AI proposed air path.")), "assumptions": list(raw.get("assumptions", [])) if isinstance(raw.get("assumptions", []), list) else [],
                "profile_id": f"{(pack or {}).get('ahu_profile_pack_version', 'au-preliminary-v4')}-{system_type}" if path_origin == "controlled_fallback" else "",
                "preliminary_assumption": path_origin == "controlled_fallback",
                "conflicts": list(raw.get("conflicts", [])) if isinstance(raw.get("conflicts", []), list) else [],
                "unresolved_fields": sorted(set(path_unresolved + [str(value) for value in raw.get("unresolved_fields", [])])),
                "remediation": "Add a cited airflow value, valid owner, and complete path evidence." if path_unresolved else "",
            })
        for kind in COMPONENT_TYPES:
            raw_rows = candidate.get(kind, [])
            if isinstance(raw_rows, dict):
                raw_rows = [raw_rows]
            for index, raw in enumerate(raw_rows if isinstance(raw_rows, list) else [], start=1):
                if not isinstance(raw, dict):
                    continue
                item = deepcopy(raw)
                item["ahu_id"] = ahu_id
                evidence = _citation_rows(item) or _citation_rows(candidate)
                comp_origin = _origin(item, origin if origin != "unresolved" else "unresolved")
                comp_unresolved = []
                if kind == "fans":
                    item["location"] = _text(item.get("location", "supply"))
                    item["heat_kw"] = _number(item.get("heat_kw"))
                    if item["heat_kw"] is None:
                        fallback = profile.get("fan_heat_kw")
                        if fallback is not None:
                            item["heat_kw"], comp_origin = _number(fallback, positive=True), "controlled_fallback"
                        else:
                            comp_unresolved.append("fan heat is unresolved")
                elif kind == "duct_effects":
                    item["sensible_kw"] = _number(item.get("sensible_kw"))
                    if item["sensible_kw"] is None:
                        fallback = profile.get("duct_sensible_kw")
                        if fallback is not None:
                            item["sensible_kw"], comp_origin = _number(fallback), "controlled_fallback"
                        else:
                            comp_unresolved.append("duct sensible effect is unresolved")
                elif kind == "leakage":
                    item["airflow_lps"], item["airflow_basis"] = _flow_lps(item)
                    if item["airflow_lps"] is None:
                        fallback = profile.get("leakage_lps")
                        if fallback is not None:
                            item["airflow_lps"], comp_origin = _number(fallback, positive=True), "controlled_fallback"
                        else:
                            comp_unresolved.append("leakage airflow is unresolved")
                    if not _text(item.get("source_node")) or not _text(item.get("destination_node")):
                        comp_unresolved.append("leakage source and destination are unresolved")
                elif kind in {"heat_recovery", "preconditioning"}:
                    for field in ("sensible_effectiveness", "latent_effectiveness"):
                        item[field] = _number(item.get(field))
                        if item[field] is None:
                            fallback = profile.get(kind, {}).get(field) if isinstance(profile.get(kind), dict) else None
                            if fallback is not None:
                                item[field], comp_origin = _number(fallback), "controlled_fallback"
                            else:
                                comp_unresolved.append(f"{kind} {field} is unresolved")
                    if kind == "preconditioning":
                        for field in ("reference_db_c", "reference_wb_c"):
                            item[field] = _number(item.get(field))
                            if item[field] is None:
                                fallback = profile.get(kind, {}).get(field) if isinstance(profile.get(kind), dict) else None
                                if fallback is not None:
                                    item[field], comp_origin = _number(fallback), "controlled_fallback"
                                else:
                                    comp_unresolved.append(f"preconditioning {field} is unresolved")
                elif kind == "coils":
                    for field in ("leaving_db_c", "leaving_wb_c"):
                        item[field] = _number(item.get(field))
                        if item[field] is None:
                            fallback = profile.get("coil", {}).get(field) if isinstance(profile.get("coil"), dict) else None
                            if fallback is not None:
                                item[field], comp_origin = _number(fallback), "controlled_fallback"
                            else:
                                comp_unresolved.append(f"coil {field} is unresolved")
                item_id = stable_id(kind[:-1] if kind.endswith("s") else kind, source_fp, item.get("tag", item.get("record_id", kind)), item.get("page", page), item.get("geometry_key", item.get("location", "")), ahu_id)
                status = "blocked" if comp_unresolved else _component_status(comp_origin, True, comp_unresolved)
                components[kind].append({
                    **item, "record_id": item_id, "ahu_id": ahu_id, "origin": comp_origin, "status": status,
                    "review_status": "confirmed" if status == "resolved" else "provisional" if status == "provisional" else "missing",
                    "source": _text(item.get("source", candidate.get("source", "AI mechanical evidence"))), "citations": evidence,
                    "confidence": round(_confidence(item.get("confidence_score", item.get("confidence")), confidence), 4),
                    "confidence_band": confidence_band(_confidence(item.get("confidence_score", item.get("confidence")), confidence)),
                    "unresolved_fields": sorted(set(comp_unresolved + [str(value) for value in item.get("unresolved_fields", [])])),
                    "rationale": _text(item.get("rationale", f"AI proposed {kind.replace('_', ' ')}.")),
                    "profile_id": f"{(pack or {}).get('ahu_profile_pack_version', 'au-preliminary-v4')}-{system_type}" if comp_origin == "controlled_fallback" else "",
                    "preliminary_assumption": comp_origin == "controlled_fallback",
                    "assumptions": list(item.get("assumptions", [])) if isinstance(item.get("assumptions", []), list) else [],
                    "remediation": "Provide cited component data or review the controlled preliminary profile." if comp_unresolved else "",
                })
    # Reconcile duplicate physical paths and ownership.  Never silently pick a
    # room or AHU when two candidates describe one source location.
    groups = {}
    for row in paths:
        key = (_text(row.get("ahu_id")), _text(row.get("zone_id")), _text(row.get("path_type")), _text(row.get("source_node")), _text(row.get("destination_node")))
        groups.setdefault(key, []).append(row)
    for key, rows in groups.items():
        if len(rows) > 1:
            ids = sorted(row["path_id"] for row in rows)
            issues.append({"type": "duplicate_air_path", "path_ids": ids, "remediation": "Confirm one canonical physical air path."})
            for row in rows:
                row["status"] = "blocked"; row["review_status"] = "missing"; row["conflicts"] = sorted(set(row.get("conflicts", []) + ids)); row["unresolved_fields"] = sorted(set(row.get("unresolved_fields", []) + ["duplicate physical air path requires review"]))
    # Room-level outside air and central AHU outside air must have one owner.
    # Keep the proposed paths visible but block the central duplicate instead
    # of silently adding the same airflow twice.
    room_outside = {
        _text(row.get("owner_room_id", row.get("room_id")))
        for row in (airflow_resolution or {}).get("records", [])
        if isinstance(row, dict) and row.get("air_path_type") == "outside_air" and row.get("status") not in {"blocked", "excluded"}
    }
    for row in paths:
        if row.get("path_type") == "outside_air" and _text(row.get("room_id")) in room_outside:
            row["status"] = "blocked"; row["review_status"] = "missing"
            row["conflicts"] = sorted(set(row.get("conflicts", []) + ["room-level outside-air ownership conflict"]))
            row["unresolved_fields"] = sorted(set(row.get("unresolved_fields", []) + ["central AHU and room-level outside air are both assigned"]))
            issues.append({"type": "duplicate_outside_air_owner", "room_id": row.get("room_id"), "ahu_id": row.get("ahu_id"), "remediation": "Choose room-level or central AHU outside-air ownership."})
    for row in systems:
        # A malformed airflow topology blocks the AHU, while a missing fan,
        # duct, recovery, or similar component is retained as an individual
        # exclusion so a valid partial preliminary subtotal can continue.
        if any(item.get("ahu_id") == row["ahu_id"] and item.get("status") == "blocked" for item in paths):
            row["status"] = "blocked"; row["review_status"] = "missing"
    source_fingerprints = deepcopy(source_fingerprints)
    source_fingerprints["ahu_profile_pack"] = fingerprint(pack or {})
    result = {"schema_version": SCHEMA_VERSION, "systems": sorted(systems, key=lambda row: row["ahu_id"]), "airflow_records": sorted(paths, key=lambda row: row["path_id"]), **{key: sorted(value, key=lambda row: row["record_id"]) for key, value in components.items()}, "issues": issues, "overrides": deepcopy(existing.get("overrides", [])), "source_fingerprints": source_fingerprints, "status": "draft" if systems else "needs_review", "updated_at": now()}
    result["summary"] = {"systems": len(systems), "provisional_systems": sum(row["status"] == "provisional" for row in systems), "blocked_systems": sum(row["status"] == "blocked" for row in systems), "paths": len(paths), "blocked_paths": sum(row["status"] == "blocked" for row in paths), "components": sum(len(result[key]) for key in COMPONENT_TYPES), "issues": len(issues)}
    result["fingerprint"] = fingerprint({key: value for key, value in result.items() if key != "fingerprint"})
    return result


def validate(raw):
    if not isinstance(raw, dict):
        raise ValueError("AHU resolution must be an object.")
    result = deepcopy(empty_ahu_resolution())
    result.update({key: deepcopy(raw[key]) for key in result if key in raw})
    for key in ("systems", "airflow_records", *COMPONENT_TYPES, "issues", "overrides"):
        if not isinstance(result.get(key), list):
            raise ValueError(f"AHU resolution {key} must be a list.")
    ids = set()
    for row in result["systems"]:
        if not isinstance(row, dict) or not _text(row.get("ahu_id")):
            raise ValueError("AHU systems need stable IDs.")
        if row["ahu_id"] in ids:
            raise ValueError("AHU IDs must be unique.")
        ids.add(row["ahu_id"])
        if row.get("system_type") not in SYSTEM_TYPES or row.get("status") not in STATUSES:
            raise ValueError("AHU system type or status is invalid.")
    path_ids = set()
    for row in result["airflow_records"]:
        if not isinstance(row, dict) or not _text(row.get("path_id")):
            raise ValueError("AHU airflow paths need stable IDs.")
        if row["path_id"] in path_ids:
            raise ValueError("AHU airflow path IDs must be unique.")
        path_ids.add(row["path_id"])
        if row.get("path_type") not in PATH_TYPES or row.get("status") not in STATUSES:
            raise ValueError("AHU airflow path type or status is invalid.")
        if row.get("flow_lps") is not None and (_number(row.get("flow_lps")) is None or float(row["flow_lps"]) < 0):
            raise ValueError("AHU airflow values must be finite and non-negative.")
        schedule = row.get("schedule", {})
        if not isinstance(schedule, dict) or any(day not in schedule or not isinstance(schedule[day], list) or len(schedule[day]) != 24 or any(_number(value) is None or not 0 <= float(value) <= 1 for value in schedule[day]) for day in DAY_TYPES):
            raise ValueError("AHU airflow schedules must contain 24 finite values between 0 and 1 for every day type.")
        for key in ("dry_bulb_c", "wet_bulb_c", "pressure_kpa"):
            if key in (row.get("state") or {}) and _number(row["state"].get(key)) is None:
                raise ValueError("AHU psychrometric state values must be finite.")
    component_ids = set()
    for kind in COMPONENT_TYPES:
        for row in result[kind]:
            if not isinstance(row, dict) or not _text(row.get("record_id")):
                raise ValueError(f"AHU {kind} need stable IDs.")
            if row["record_id"] in component_ids:
                raise ValueError("AHU component IDs must be unique.")
            component_ids.add(row["record_id"])
            if row.get("status") not in STATUSES:
                raise ValueError(f"AHU {kind} status is invalid.")
            for key in ("heat_kw", "sensible_kw", "airflow_lps", "leaving_db_c", "leaving_wb_c", "reference_db_c", "reference_wb_c"):
                if key in row and row.get(key) is not None and _number(row.get(key)) is None:
                    raise ValueError(f"AHU {kind} {key} must be finite.")
            for key in ("sensible_effectiveness", "latent_effectiveness"):
                if key in row and row.get(key) is not None and (_number(row.get(key)) is None or not 0 <= float(row[key]) <= 1):
                    raise ValueError(f"AHU {kind} effectiveness values must be between 0 and 1.")
    result["fingerprint"] = fingerprint({key: value for key, value in result.items() if key != "fingerprint"})
    return result


def is_current(artifact, source_fingerprints):
    return isinstance(artifact, dict) and all(artifact.get("source_fingerprints", {}).get(key) == value for key, value in (source_fingerprints or {}).items())


def apply_override(artifact, target_type, target_id, values, reviewer, note=""):
    result = validate(artifact)
    reviewer = _text(reviewer)
    if not reviewer:
        raise ValueError("AHU overrides require a reviewer.")
    values = values if isinstance(values, dict) else {}
    rows = result["systems"] if target_type == "system" else result["airflow_records"] if target_type == "path" else next((result[k] for k in COMPONENT_TYPES if target_type == k.rstrip("s")), [])
    row = next((item for item in rows if item.get("ahu_id") == target_id or item.get("path_id") == target_id or item.get("record_id") == target_id), None)
    if row is None:
        raise ValueError("AHU override references an unknown system, path, or component.")
    for key, value in values.items():
        if key not in {"flow_lps", "airflow_lps", "heat_kw", "sensible_kw", "leaving_db_c", "leaving_wb_c", "sensible_effectiveness", "latent_effectiveness", "reference_db_c", "reference_wb_c", "number_off"}:
            continue
        number = _number(value)
        if number is None:
            raise ValueError(f"AHU override value for {key} must be finite.")
        row[key] = number
    row["origin"] = "contractor_override"; row["status"] = "resolved"; row["review_status"] = "confirmed"
    row["override"] = {"reviewer": reviewer, "note": _text(note), "updated_at": now(), "values": deepcopy(values)}
    result.setdefault("overrides", []).append({"target_type": target_type, "target_id": target_id, "reviewer": reviewer, "note": _text(note), "values": deepcopy(values), "updated_at": now()})
    result["fingerprint"] = fingerprint({key: value for key, value in result.items() if key != "fingerprint"})
    return result


def clear_override(artifact, target_type, target_id):
    result = validate(artifact)
    for collection in [result["systems"], result["airflow_records"]] + [result[key] for key in COMPONENT_TYPES]:
        for row in collection:
            if row.get("ahu_id") == target_id or row.get("path_id") == target_id or row.get("record_id") == target_id:
                row.pop("override", None); row["status"] = "needs_review"; row["review_status"] = "missing"; row["origin"] = "unresolved"
                row.setdefault("unresolved_fields", []).append("override cleared; resolve again")
    result["overrides"] = [item for item in result.get("overrides", []) if item.get("target_id") != target_id]
    result["fingerprint"] = fingerprint({key: value for key, value in result.items() if key != "fingerprint"})
    return result


def materialize(artifact, *, preliminary=False):
    """Return isolated Stage 9-shaped inputs plus exclusions and provenance."""
    artifact = validate(artifact)
    review_blocked = {"blocked", "excluded"} | (set() if preliminary else {"needs_review"})
    systems = []
    for row in artifact["systems"]:
        # Preliminary AI is intentionally permissive about review state.  A
        # structurally valid proposal may still be labelled ``needs_review``
        # because its source/citation or confidence needs contractor review.
        # Keep it visibly provisional in the isolated draft model; only
        # topology-invalid/explicitly excluded systems remain ineligible.
        if row.get("status") in review_blocked:
            continue
        systems.append({"ahu_id": row["ahu_id"], "name": row["name"], "system_type": row["system_type"], "number_off": row["number_off"], "served_zone_ids": row["served_zone_ids"], "review_status": "provisional" if preliminary else "missing", "source": row.get("source", ""), "citations": deepcopy(row.get("citations", [])), "profile_id": row.get("profile_id", ""), "preliminary_assumption": bool(row.get("preliminary_assumption"))})
    model = {"schema_version": 1, "updated_at": now(), "outside_air_ownership": "central_ahu", "airflow_records": [], "fans": [], "duct_effects": [], "leakage": [], "heat_recovery": [], "preconditioning": [], "coils": []}
    excluded = []
    active = {row["ahu_id"] for row in systems}
    for row in artifact["airflow_records"]:
        # A needs-review path with a finite, normalized flow can contribute to
        # the draft.  Missing flow remains excluded rather than being treated
        # as zero, and blocked paths still fail closed.
        if row.get("ahu_id") not in active or row.get("status") in review_blocked or row.get("flow_lps") is None:
            excluded.append({"kind": "airflow", "id": row.get("path_id", ""), "reason": "; ".join(row.get("unresolved_fields", [])) or "Airflow path is not eligible."})
            continue
        model["airflow_records"].append({"record_id": row["path_id"], "ahu_id": row["ahu_id"], "zone_id": row.get("zone_id", ""), "path_type": row["path_type"], "source_node": row["source_node"], "destination_node": row["destination_node"], "flow_lps": row["flow_lps"], "schedule": row.get("schedule", {}).get("weekday", []), "state": deepcopy(row.get("state", {})), "review_status": "provisional" if preliminary else "missing", "source": row.get("source", ""), "citations": deepcopy(row.get("citations", [])), "profile_id": row.get("profile_id", ""), "preliminary_assumption": bool(row.get("preliminary_assumption"))})
    for kind in COMPONENT_TYPES:
        for row in artifact[kind]:
            # As with airflow paths, retain finite needs-review component
            # values for draft calculation and surface their provisional
            # provenance.  Invalid/blocked components remain excluded.
            if row.get("ahu_id") not in active or row.get("status") in review_blocked:
                excluded.append({"kind": kind, "id": row.get("record_id", ""), "reason": "; ".join(row.get("unresolved_fields", [])) or "Component is not eligible."})
                continue
            clean = deepcopy(row); clean.pop("status", None); clean.pop("origin", None); clean.pop("confidence", None); clean.pop("confidence_band", None); clean.pop("unresolved_fields", None); clean["review_status"] = "provisional" if preliminary else "missing"
            model[kind].append(clean)
    return {"systems": {"schema_version": 1, "updated_at": now(), "systems": systems}, "model": model, "excluded": excluded, "policy": {"mode": "ai_preliminary" if preliminary else "reviewed", "artifact_fingerprint": artifact["fingerprint"]}}
