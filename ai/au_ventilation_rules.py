"""Fail-closed matching of reviewed Australian ventilation rules to projects.

No numerical defaults are bundled. The release-ready interface is versioned so
authorized, engineer-reviewed rule records can be added without embedding
standards tables in calculation code.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date
import math
import hashlib
import json
from pathlib import Path
import re


PACK_PATH = Path(__file__).resolve().parents[1] / "config" / "au_ventilation_rules_v1.json"
PACK_ID = "au-ventilation-rules-v1"
JURISDICTIONS = {"ACT", "NSW", "NT", "QLD", "SA", "TAS", "VIC", "WA"}
BUILDING_CLASSES = {f"class_{number}" for number in range(1, 11)} | {"other", "unknown"}
HOSPITALITY_ROOM_USES = {
    "dining", "kitchen", "retail", "office", "storage", "ancillary_conditioned",
}


def fingerprint(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def empty_regulatory_context():
    return {
        "building_approval_application_date": "",
        "building_class": "unknown",
        "building_use": "",
        "project_specific_ventilation_basis": "",
    }


def validate_regulatory_context(raw):
    if not isinstance(raw, dict):
        raise ValueError("Project regulatory context must be an object.")
    result = empty_regulatory_context()
    result.update({key: raw.get(key, default) for key, default in result.items()})
    result["building_approval_application_date"] = str(result["building_approval_application_date"] or "").strip()
    result["building_use"] = str(result["building_use"] or "").strip()[:120]
    result["project_specific_ventilation_basis"] = str(result["project_specific_ventilation_basis"] or "").strip()[:500]
    result["building_class"] = str(result["building_class"] or "unknown").strip().casefold()
    if result["building_class"] not in BUILDING_CLASSES:
        raise ValueError("Building class must be an NCC class from 1 to 10, other, or unknown.")
    if result["building_approval_application_date"]:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", result["building_approval_application_date"]):
            raise ValueError("Building-approval application date must use YYYY-MM-DD.")
        try:
            date.fromisoformat(result["building_approval_application_date"])
        except ValueError as error:
            raise ValueError("Building-approval application date must use YYYY-MM-DD.") from error
    return result


def empty_ruleset():
    return {
        "schema_version": 1,
        "pack_id": PACK_ID,
        "pack_version": "1.0.0",
        "country": "AU",
        "status": "candidate",
        "jurisdictions": {},
        "rules": [],
    }


def load_ruleset():
    try:
        return validate_ruleset(json.loads(PACK_PATH.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("Australian ventilation ruleset is unavailable or invalid.") from error


def validate_ruleset(raw):
    if not isinstance(raw, dict) or raw.get("schema_version") != 1 or raw.get("pack_id") != PACK_ID or raw.get("country") != "AU":
        raise ValueError("Unsupported Australian ventilation ruleset.")
    jurisdictions = raw.get("jurisdictions")
    rules = raw.get("rules")
    if not isinstance(jurisdictions, dict) or set(jurisdictions) != JURISDICTIONS:
        raise ValueError("Ventilation ruleset must declare all eight Australian jurisdictions.")
    if not isinstance(rules, list):
        raise ValueError("Ventilation rules must be a list.")
    for code, row in jurisdictions.items():
        if not isinstance(row, dict) or row.get("status") not in {"awaiting_review", "released", "not_applicable"}:
            raise ValueError(f"Jurisdiction {code} has invalid ruleset status.")
        adoptions = row.get("ncc_adoptions", [])
        if not isinstance(adoptions, list):
            raise ValueError(f"Jurisdiction {code} NCC adoption history must be a list.")
        for adoption in adoptions:
            _validate_adoption(adoption, code)
    ids = set()
    for rule in rules:
        _validate_rule(rule)
        if rule["rule_id"] in ids:
            raise ValueError("Ventilation rule IDs must be unique.")
        ids.add(rule["rule_id"])
    return deepcopy(raw)


def _validate_date(value, label, optional=False):
    value = str(value or "").strip()
    if not value and optional:
        return ""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"{label} must use YYYY-MM-DD.")
    try:
        date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{label} must use YYYY-MM-DD.") from error
    return value


def _validate_adoption(row, jurisdiction):
    if not isinstance(row, dict) or row.get("jurisdiction") != jurisdiction or not row.get("ncc_edition"):
        raise ValueError(f"Invalid NCC adoption record for {jurisdiction}.")
    start = _validate_date(row.get("effective_from"), "NCC effective date")
    end = _validate_date(row.get("effective_to"), "NCC expiry date", optional=True)
    if end and end < start:
        raise ValueError("NCC adoption expiry must not precede its start date.")
    if row.get("status") not in {"released", "candidate"}:
        raise ValueError("NCC adoption status must be released or candidate.")
    if row.get("status") == "released" and (not row.get("source") or not row.get("reviewed_by") or not row.get("reviewed_at")):
        raise ValueError("Released NCC adoption records require a source and reviewer/date.")


def _validate_rule(row):
    if not isinstance(row, dict) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{2,99}", str(row.get("rule_id", ""))):
        raise ValueError("Ventilation rule needs a valid stable rule_id.")
    if row.get("jurisdiction") not in JURISDICTIONS:
        raise ValueError(f"Rule {row.get('rule_id')} needs a supported Australian jurisdiction.")
    if row.get("status") not in {"candidate", "released", "expired"}:
        raise ValueError(f"Rule {row.get('rule_id')} has invalid status.")
    start = _validate_date(row.get("effective_from"), "Rule effective date")
    end = _validate_date(row.get("effective_to"), "Rule expiry date", optional=True)
    if end and end < start:
        raise ValueError("Ventilation rule expiry must not precede its start date.")
    scope = row.get("scope")
    if not isinstance(scope, dict) or scope.get("building_class") not in BUILDING_CLASSES or scope.get("room_use") not in HOSPITALITY_ROOM_USES:
        raise ValueError(f"Rule {row.get('rule_id')} must identify a supported building class and room use.")
    requirements = row.get("requirements")
    if not isinstance(requirements, dict):
        raise ValueError(f"Rule {row.get('rule_id')} requirements must be an object.")
    if row.get("status") == "released":
        required = ("ncc_edition", "ncc_clause", "standard_edition", "standard_clause", "source", "source_access_basis", "reviewed_by", "reviewer_credential", "reviewed_at")
        if any(not str(row.get(key, "")).strip() for key in required):
            raise ValueError(f"Released rule {row['rule_id']} lacks source, edition, clause, or engineer-review provenance.")
        method = requirements.get("outside_air_method")
        rates = requirements.get("rates")
        if method not in {"occupancy", "area", "fixed", "combined"} or not isinstance(rates, dict):
            raise ValueError(f"Released rule {row['rule_id']} lacks reviewed ventilation method/rates.")
        rate_keys = {"people_rate_lps_per_person", "area_rate_lps_per_m2", "fixed_minimum_lps"}
        if set(rates) - rate_keys or not rates:
            raise ValueError(f"Released rule {row['rule_id']} contains unsupported ventilation rates.")
        for key, value in rates.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"Released rule {row['rule_id']} has invalid {key}.")
        if method == "combined" and len(rates) < 2:
            raise ValueError(f"Released combined rule {row['rule_id']} needs at least two rate bases.")
        if method != "combined" and len(rates) != 1:
            raise ValueError(f"Released {method} rule {row['rule_id']} must have exactly one rate basis.")
        expected_key = {"occupancy": "people_rate_lps_per_person", "area": "area_rate_lps_per_m2", "fixed": "fixed_minimum_lps"}
        if method in expected_key and expected_key[method] not in rates:
            raise ValueError(f"Released {method} rule {row['rule_id']} is missing its corresponding rate.")


def _adoption_for(jurisdiction, approval_date, pack):
    if pack["jurisdictions"][jurisdiction].get("status") != "released":
        return None, f"The {jurisdiction} code-adoption timeline has not been reviewed and released."
    records = [row for row in pack["jurisdictions"][jurisdiction].get("ncc_adoptions", [])
               if row.get("status") == "released"
               and row["effective_from"] <= approval_date
               and (not row.get("effective_to") or approval_date <= row["effective_to"])]
    if len(records) == 1:
        return records[0], ""
    if len(records) > 1:
        return None, "Overlapping NCC adoption records make the applicable edition ambiguous."
    return None, f"No released NCC adoption record covers {jurisdiction} on {approval_date}."


def resolve(location, context, zones, ruleset=None):
    """Resolve rule candidates without inventing values; only released records match."""
    pack = validate_ruleset(ruleset or load_ruleset())
    regulatory = validate_regulatory_context(context or {})
    state = str((location or {}).get("state", "")).upper()
    resolution = {
        "schema_version": 1, "ruleset_id": pack["pack_id"], "ruleset_version": pack["pack_version"],
        "ruleset_fingerprint": fingerprint(pack), "jurisdiction": state,
        "building_approval_application_date": regulatory["building_approval_application_date"],
        "building_class": regulatory["building_class"], "status": "blocked", "ncc_edition": "",
        "project_specific_basis": regulatory["project_specific_ventilation_basis"], "zone_results": [], "conflicts": [],
    }
    if state not in JURISDICTIONS:
        resolution["conflicts"].append("Confirm a resolved Australian project location before applying jurisdiction rules.")
    if not regulatory["building_approval_application_date"]:
        resolution["conflicts"].append("Enter the building-approval application date to resolve the edition in force.")
    if regulatory["building_class"] in {"unknown", "other"}:
        resolution["conflicts"].append("Confirm the NCC building class before matching ventilation rules.")
    if regulatory["project_specific_ventilation_basis"]:
        resolution["conflicts"].append("A project-specific ventilation basis is recorded; reconcile it with code requirements before applying automatic values.")
    adoption = None
    if state in JURISDICTIONS and regulatory["building_approval_application_date"]:
        adoption, message = _adoption_for(state, regulatory["building_approval_application_date"], pack)
        if message:
            resolution["conflicts"].append(message)
        elif adoption:
            resolution["ncc_edition"] = adoption["ncc_edition"]
    for zone in zones or []:
        if not isinstance(zone, dict):
            continue
        zone_id = str(zone.get("zone_id", "")).strip()
        use = str(zone.get("room_use_category", "")).strip()
        result = {"zone_id": zone_id, "room_use_category": use, "status": "blocked", "rule_id": "", "message": ""}
        if use not in HOSPITALITY_ROOM_USES:
            result["message"] = "Room use is unresolved or outside the initial hospitality/food-service coverage."
        elif state not in JURISDICTIONS or not adoption or regulatory["building_class"] in {"unknown", "other"}:
            result["message"] = "Resolve project jurisdiction, code edition, and building class first."
        elif regulatory["project_specific_ventilation_basis"]:
            result["message"] = "Review project-specific ventilation requirements before applying a default."
        else:
            candidates = [rule for rule in pack["rules"] if rule.get("status") == "released"
                          and rule["jurisdiction"] == state
                          and rule["effective_from"] <= regulatory["building_approval_application_date"]
                          and (not rule.get("effective_to") or regulatory["building_approval_application_date"] <= rule["effective_to"])
                          and rule.get("ncc_edition") == adoption["ncc_edition"]
                          and rule["scope"].get("building_class") == regulatory["building_class"]
                          and rule["scope"].get("room_use") == use]
            if len(candidates) == 1:
                result.update({"status": "matched", "rule_id": candidates[0]["rule_id"], "message": "One released rule matches this project and room."})
            elif len(candidates) > 1:
                result["message"] = "Multiple released rules match; no automatic value was applied."
            else:
                result["message"] = f"No released rule covers {state}, {adoption['ncc_edition']}, class {regulatory['building_class'].removeprefix('class_')}, and {use}."
        resolution["zone_results"].append(result)
    if resolution["conflicts"]:
        resolution["status"] = "needs_review"
    elif any(item["status"] == "matched" for item in resolution["zone_results"]):
        resolution["status"] = "matched"
    elif resolution["zone_results"]:
        resolution["status"] = "rules_unavailable"
    else:
        resolution["status"] = "no_rooms"
    resolution["fingerprint"] = fingerprint(resolution)
    return resolution


def apply_matches(requirements, resolution, ruleset=None):
    """Fill blank ventilation inputs from one released rule; preserve conflicts and manual values."""
    pack = validate_ruleset(ruleset or load_ruleset())
    result = deepcopy(requirements)
    by_id = {rule["rule_id"]: rule for rule in pack["rules"] if rule.get("status") == "released"}
    outcomes = []
    def read_field(zone, key):
        if key.startswith("cooling_load."):
            return zone.get("cooling_load", {}).get(key.split(".", 1)[1])
        return zone.get("ventilation_requirements", {}).get(key)

    def write_field(zone, key, value):
        if key.startswith("cooling_load."):
            zone.setdefault("cooling_load", {})[key.split(".", 1)[1]] = value
        else:
            zone.setdefault("ventilation_requirements", {})[key] = value

    def restore_auto_values(zone, vent):
        state = vent.get("rule_applied_values", {})
        applied, previous = state.get("applied", {}), state.get("previous", {})
        edited = []
        for key, applied_value in applied.items():
            if read_field(zone, key) == applied_value:
                write_field(zone, key, previous.get(key))
            else:
                edited.append(key)
        return previous, edited

    for zone in result.get("zones", []):
        vent = zone.get("ventilation_requirements", {})
        match = next((item for item in resolution.get("zone_results", [])
                      if item.get("zone_id") == zone.get("zone_id") and item.get("status") == "matched"), None)
        if not match:
            if vent.get("rule_id"):
                _previous, edited = restore_auto_values(zone, vent)
                vent["rule_id"] = ""
                vent["rule_pack_version"] = ""
                vent["rule_citation"] = ""
                vent["rule_applied_values"] = {}
                outcomes.append({"zone_id": zone.get("zone_id"), "status": "stale_values_preserved" if edited else "previous_rule_invalidated",
                                 "fields": edited, "rule_id": ""})
            continue
        rule = by_id.get(match.get("rule_id"))
        if not rule:
            continue
        previous_auto, edited_auto = restore_auto_values(zone, vent)
        rates = rule["requirements"]["rates"]
        keys = ("outside_air_method", "people_rate_lps_per_person", "area_rate_lps_per_m2", "fixed_minimum_lps")
        has_manual_rates = any(vent.get(key) is not None for key in keys[1:])
        conflicts = [key for key in keys if (key != "outside_air_method" or has_manual_rates)
                     and vent.get(key) not in (None, "") and vent.get(key) != rates.get(key)]
        expected_text = {
            "basis_name": f"{rule['ncc_edition']} / {rule['standard_edition']}",
            "basis_source": f"{rule['ncc_clause']}; {rule['standard_clause']}", "source": rule["source"],
        }
        conflicts.extend(key for key, expected in expected_text.items()
                         if vent.get(key) not in (None, "") and vent.get(key) != expected)
        preview = deepcopy(zone)
        preview_vent = preview.setdefault("ventilation_requirements", {})
        preview_vent.update({key: value for key, value in rates.items()})
        preview_vent.update({"basis_name": expected_text["basis_name"], "basis_source": expected_text["basis_source"],
                             "source": rule["source"], "verification_status": "confirmed"})
        from ai.ventilation import calculate_zone_ventilation
        preview_result = calculate_zone_ventilation(preview)
        calculated_flow = (preview_result.get("outside_air", {}).get("required_lps")
                           if preview_result.get("status") != "blocked" else None)
        current_flow = zone.get("cooling_load", {}).get("outside_air_lps")
        if calculated_flow is not None and current_flow is not None and current_flow != calculated_flow:
            conflicts.append("cooling_load.outside_air_lps")
        if conflicts:
            outcomes.append({"zone_id": zone.get("zone_id"), "status": "manual_conflict", "fields": conflicts, "rule_id": rule["rule_id"]})
            continue
        previous_values = {}
        for key in (*keys, "basis_name", "basis_source", "source", "verification_status", "cooling_load.outside_air_lps", "cooling_load.source", "cooling_load.outside_air_source", "cooling_load.outside_air_verification_status"):
            previous_values[key] = previous_auto.get(key, read_field(zone, key))
        for key in keys:
            if rates.get(key) is not None:
                vent[key] = rates[key]
        vent["basis_name"] = f"{rule['ncc_edition']} / {rule['standard_edition']}"
        vent["basis_source"] = f"{rule['ncc_clause']}; {rule['standard_clause']}"
        vent["source"] = rule["source"]
        vent["verification_status"] = "confirmed"
        vent["rule_id"] = rule["rule_id"]
        vent["rule_pack_version"] = pack["pack_version"]
        vent["rule_citation"] = rule["source"]
        cooling = zone.setdefault("cooling_load", {})
        if calculated_flow is not None:
            manual_flow = cooling.get("outside_air_lps")
            if manual_flow is None or "cooling_load.outside_air_lps" in edited_auto:
                cooling["outside_air_lps"] = calculated_flow
                if not cooling.get("source"):
                    cooling["source"] = f"Outside-air flow from {rule['rule_id']}: {rule['source']}"
                cooling["outside_air_source"] = rule["source"]
                cooling["outside_air_verification_status"] = "confirmed"
        vent["rule_applied_values"] = {"previous": previous_values, "applied": {
            **{key: vent.get(key) for key in keys}, "basis_name": vent["basis_name"],
            "basis_source": vent["basis_source"], "source": vent["source"],
            "verification_status": vent["verification_status"],
            "cooling_load.outside_air_lps": cooling.get("outside_air_lps"),
            "cooling_load.source": cooling.get("source"),
            "cooling_load.outside_air_source": cooling.get("outside_air_source"),
            "cooling_load.outside_air_verification_status": cooling.get("outside_air_verification_status"),
        }}
        outcomes.append({"zone_id": zone.get("zone_id"), "status": "applied", "rule_id": rule["rule_id"]})
    return result, outcomes


def sync_hourly_model_outside_air(model, requirements):
    """Keep hourly room outside-air inputs aligned with applied rules, without replacing manual flows."""
    result = deepcopy(model)
    zones = {zone.get("zone_id"): zone for zone in requirements.get("zones", [])}
    outcomes = []
    for room in result.get("rooms", []):
        zone = zones.get(room.get("source_zone_id") or room.get("zone_id"))
        if not zone:
            continue
        req = zone.get("ventilation_requirements", {})
        rule_id = req.get("rule_id", "")
        calculated = zone.get("cooling_load", {}).get("outside_air_lps")
        load = room.setdefault("cooling_load", {})
        current = load.get("outside_air_lps")
        source = str(load.get("source", ""))
        if rule_id and calculated is not None:
            if current is None or source.startswith("Outside-air flow from "):
                load["outside_air_lps"] = calculated
                load["source"] = zone.get("cooling_load", {}).get("source") or f"Outside-air flow from {rule_id}: {req.get('rule_citation', '')}"
                outcomes.append({"room_id": room.get("room_id", ""), "status": "applied", "rule_id": rule_id})
            elif current != calculated:
                outcomes.append({"room_id": room.get("room_id", ""), "status": "manual_conflict", "rule_id": rule_id,
                                 "message": "The hourly room already has a different outside-air flow; the manual value was preserved."})
        elif not rule_id and source.startswith("Outside-air flow from "):
            load["outside_air_lps"] = None
            load["source"] = ""
            outcomes.append({"room_id": room.get("room_id", ""), "status": "invalidated", "message": "The automatic rule context changed; the previous rule-derived room flow was cleared."})
    return result, outcomes
