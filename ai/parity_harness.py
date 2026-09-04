"""Non-proprietary CAMEL+/DA09 benchmark reconciliation and comparison tools."""

import json
from pathlib import Path


COMPONENTS = ("solar", "envelope", "partitions", "people", "lighting", "equipment_refrigeration", "infiltration", "outside_air")
STATUSES = {"matched", "archie_only", "camel_only", "unresolved"}
MAPPING_FAMILIES = (
    "location_design_conditions", "schedules", "room_zone_hierarchy", "surfaces",
    "glazing", "shading", "partitions", "storage_mass", "people", "lighting",
    "equipment", "infiltration", "outside_air",
)


def empty_benchmark_case(case_id="new_case"):
    return {
        "schema_version": 1,
        "case_id": case_id,
        "authorisation": {"da09_reference": "missing", "camel_export": "missing"},
        "source_files": {"drawings": "", "camel_input_or_export": "", "camel_results": "", "da09_basis": "", "assumption_register": ""},
        "input_mapping": {family: [] for family in MAPPING_FAMILIES},
        "input_reconciliation": [],
        "reference_results": {"peak": {"month": "", "hour": None}, "rooms": [], "zones": []},
        "comparison_policy": {"tolerance_status": "baseline_mapping_required", "component_tolerance_percent": None, "total_tolerance_percent": None},
    }


def validate_benchmark_case(case):
    if not isinstance(case, dict):
        raise ValueError("Benchmark case must be a JSON object.")
    for key in ("case_id", "authorisation", "source_files", "input_mapping", "input_reconciliation", "reference_results", "comparison_policy"):
        if key not in case:
            raise ValueError(f"Benchmark case is missing '{key}'.")
    if not str(case["case_id"]).strip():
        raise ValueError("Benchmark case needs a stable case_id.")
    if not isinstance(case["input_reconciliation"], list):
        raise ValueError("input_reconciliation must be a list.")
    for family in MAPPING_FAMILIES:
        if not isinstance(case["input_mapping"].get(family), list):
            raise ValueError(f"input_mapping.{family} must be a list.")
    for item in mapped_inputs(case):
        if item.get("status") not in STATUSES:
            raise ValueError("Each input reconciliation item needs a valid status.")
    return case


def create_case_folder(folder, case_id="new_case"):
    folder = Path(folder)
    for name in ("source", "archie", "reference", "reports"):
        (folder / name).mkdir(parents=True, exist_ok=True)
    path = folder / "benchmark_case.json"
    path.write_text(json.dumps(empty_benchmark_case(case_id), indent=2), encoding="utf-8")
    return path


def compare_case(case, archie_report):
    case = validate_benchmark_case(case)
    missing = case_missing_material(case)
    reconciliation = mapped_inputs(case)
    unresolved = [item for item in reconciliation if item.get("status") == "unresolved"]
    unmapped = [family for family in MAPPING_FAMILIES if not case["input_mapping"].get(family)]
    report = {
        "report_type": "da09_camel_room_to_zone_parity",
        "case_id": case["case_id"],
        "status": "blocked" if missing or unresolved or unmapped else "baseline_compared",
        "reference_peak": case["reference_results"].get("peak", {}),
        "archie_peak": archie_report.get("peak", {}),
        "peak_comparison": compare_peak(case["reference_results"].get("peak", {}), archie_report.get("peak", {})),
        "missing_reference_material": missing,
        "unresolved_inputs": unresolved,
        "unmapped_input_families": unmapped,
        "input_reconciliation": reconciliation,
        "rooms": compare_entities(case["reference_results"].get("rooms", []), archie_report.get("rooms", [])),
        "zones": compare_entities(case["reference_results"].get("zones", []), archie_report.get("zones", [])),
        "tolerance_policy": case["comparison_policy"],
        "final_parity_allowed": False,
        "reason": "Numeric tolerance is intentionally unset until the authorised baseline mapping explains method, data, schedule, and rounding differences.",
    }
    report["summary"] = comparison_summary(report)
    return report


def mapped_inputs(case):
    """Flatten explicit family mappings while preserving legacy reconciliation rows."""
    records = list(case.get("input_reconciliation", []))
    for family, items in case.get("input_mapping", {}).items():
        for item in items:
            records.append({"family": family, **item})
    return records


def archie_results_from_heat_report(heat_load_report):
    """Expose the preliminary engine's zone output in the parity schema.

    This is an adapter, not a claim that the preliminary engine implements
    DA09.  It deliberately leaves peak timing empty because that engine does
    not yet perform hourly calculations.
    """
    zones = []
    for result in heat_load_report.get("zone_results", []):
        components = {
            item["name"]: {
                "sensible_kw": item.get("sensible_kw"),
                "latent_kw": item.get("latent_kw"),
                "total_kw": item.get("total_kw"),
            }
            for item in result.get("contributions", [])
            if item.get("name")
        }
        zones.append({
            "zone_id": result.get("zone_id", ""),
            "name": result.get("zone_name", ""),
            "sensible_kw": result.get("subtotal_sensible_kw"),
            "latent_kw": result.get("subtotal_latent_kw"),
            "total_kw": result.get("subtotal_kw"),
            "design_total_kw": result.get("design_total_kw"),
            "components": components,
            "status": result.get("status", ""),
        })
    return {"peak": {}, "rooms": [], "zones": zones}


def archie_results_from_hourly_load_report(hourly_load_report):
    """Expose a current hourly design-day report without declaring DA09 parity."""
    governing = hourly_load_report.get("governing_project_peak", {})
    scenario_id = governing.get("scenario_id")
    scenario = next((item for item in hourly_load_report.get("scenario_results", []) if item.get("scenario_id") == scenario_id), {})
    return {
        "peak": {
            "month": governing.get("month", ""), "hour": governing.get("hour"),
            "sensible_kw": governing.get("sensible_kw"), "latent_kw": governing.get("latent_kw"),
            "total_kw": governing.get("total_kw"), "design_total_kw": governing.get("design_total_kw"),
            "components": governing.get("components", {}),
        },
        "rooms": [hourly_entity(item, "room_id") for item in scenario.get("rooms", []) if item.get("peak")],
        "zones": [hourly_entity(item, "zone_id") for item in scenario.get("zones", []) if item.get("peak")],
    }


def hourly_entity(item, key):
    peak = item.get("peak", {})
    return {
        key: item.get(key, ""), "name": item.get("name", item.get(key, "")),
        "sensible_kw": peak.get("sensible_kw"), "latent_kw": peak.get("latent_kw"),
        "total_kw": peak.get("total_kw"), "design_total_kw": peak.get("design_total_kw"),
        "components": peak.get("components", {}), "status": item.get("status", ""),
    }


def case_missing_material(case):
    missing = []
    if case["authorisation"].get("da09_reference") != "provided":
        missing.append("authorised DA09 reference")
    if case["authorisation"].get("camel_export") != "provided":
        missing.append("completed CAMEL+ export/results")
    for key in ("camel_input_or_export", "camel_results", "da09_basis", "assumption_register"):
        if not str(case["source_files"].get(key, "")).strip():
            missing.append(key.replace("_", " "))
    if not case["reference_results"].get("rooms") and not case["reference_results"].get("zones"):
        missing.append("normalised CAMEL+ room or zone results")
    return missing


def compare_peak(reference, archie):
    if not reference.get("month") or reference.get("hour") is None:
        return {"status": "camel_only", "reference": reference, "archie": archie}
    if not archie.get("month") or archie.get("hour") is None:
        return {"status": "archie_missing_hourly_result", "reference": reference, "archie": archie}
    return {
        "status": "matched" if reference.get("month") == archie.get("month") and reference.get("hour") == archie.get("hour") else "different",
        "reference": reference,
        "archie": archie,
    }


def compare_entities(reference, archie):
    archie_by_id = {entity_key(item): item for item in archie}
    rows = []
    for expected in reference:
        key = entity_key(expected)
        actual = archie_by_id.pop(key, None)
        rows.append(compare_entity(expected, actual))
    for actual in archie_by_id.values():
        rows.append({"entity_id": entity_key(actual), "status": "archie_only", "archie": actual, "reference": {}})
    return rows


def compare_entity(reference, archie):
    if not archie:
        return {"entity_id": entity_key(reference), "status": "camel_only", "reference": reference, "archie": {}, "components": []}
    return {
        "entity_id": entity_key(reference), "status": "compared",
        "reference": totals(reference), "archie": totals(archie),
        "components": [compare_component(
            name, component_value(reference, name), component_value(archie, name),
            component_source(reference.get("components", {}).get(name)),
            component_source(archie.get("components", {}).get(name)),
        ) for name in COMPONENTS],
    }


def compare_component(name, expected, actual, expected_source="", actual_source=""):
    if expected is None and actual is None:
        return {"name": name, "status": "not_comparable"}
    if expected is None:
        return {"name": name, "status": "archie_only", "archie": actual, "archie_source": actual_source}
    if actual is None:
        return {"name": name, "status": "camel_only", "reference": expected, "reference_source": expected_source}
    return {"name": name, "status": "compared", "reference_kw": expected, "archie_kw": actual, "difference_kw": round(actual - expected, 4), "difference_percent": percent_difference(expected, actual), "reference_source": expected_source, "archie_source": actual_source}


def component_value(entity, name):
    components = entity.get("components", {})
    value = components.get(name)
    if isinstance(value, dict):
        return value.get("total_kw")
    return value


def component_source(value):
    return value.get("source", "") if isinstance(value, dict) else ""


def totals(entity):
    return {key: entity.get(key) for key in ("sensible_kw", "latent_kw", "total_kw", "design_total_kw") if entity.get(key) is not None}


def entity_key(entity):
    return str(entity.get("entity_id") or entity.get("zone_id") or entity.get("room_id") or entity.get("name") or "unnamed")


def percent_difference(reference, actual):
    if reference == 0:
        return None
    return round((actual - reference) / reference * 100, 3)


def comparison_summary(report):
    components = [component for entity in report["rooms"] + report["zones"] for component in entity.get("components", []) if component.get("status") == "compared"]
    return {"compared_component_count": len(components), "unresolved_input_count": len(report["unresolved_inputs"]), "unmapped_input_family_count": len(report["unmapped_input_families"]), "missing_material_count": len(report["missing_reference_material"]), "non_comparable_component_count": sum(component.get("status") != "compared" for entity in report["rooms"] + report["zones"] for component in entity.get("components", []))}


def render_markdown(report):
    lines = [f"# DA09/CAMEL+ Parity Report: {report['case_id']}", "", f"Status: **{report['status']}**", "", "## Reference readiness", ""]
    if report["missing_reference_material"]:
        lines.extend("- Missing: " + item for item in report["missing_reference_material"])
    else:
        lines.append("- Authorised reference material recorded.")
    if report["unresolved_inputs"]:
        lines.extend("- Unresolved input: " + str(item.get("field", "unnamed")) for item in report["unresolved_inputs"])
    if report["unmapped_input_families"]:
        lines.extend("- Unmapped input family: " + item.replace("_", " ") for item in report["unmapped_input_families"])
    lines.extend(["", "## Comparison summary", "", f"- Peak timing: {report['peak_comparison']['status']}", f"- Comparable components: {report['summary']['compared_component_count']}", f"- Final parity allowed: {report['final_parity_allowed']}", "", "## Component variance", "", "| Scope | Component | CAMEL+ kW | Archie kW | Difference kW | Difference % | Status |", "| --- | --- | ---: | ---: | ---: | ---: | --- |"])
    for entity in report["rooms"] + report["zones"]:
        for component in entity.get("components", []):
            lines.append("| {scope} | {name} | {reference} | {archie} | {difference} | {percent} | {status} |".format(
                scope=entity["entity_id"], name=component["name"],
                reference=component.get("reference_kw", ""), archie=component.get("archie_kw", ""),
                difference=component.get("difference_kw", ""), percent=component.get("difference_percent", ""), status=component["status"],
            ))
    return "\n".join(lines) + "\n"
