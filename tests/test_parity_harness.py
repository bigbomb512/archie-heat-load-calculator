#!/usr/bin/env python3

from pathlib import Path
from tempfile import TemporaryDirectory

from ai.parity_harness import MAPPING_FAMILIES, archie_results_from_heat_report, compare_case, create_case_folder, empty_benchmark_case, validate_benchmark_case


def check(name, actual, expected=True):
    if actual != expected:
        raise AssertionError(f"{name}: expected {expected}, got {actual}")
    print("PASS - " + name)


def ready_case():
    case = empty_benchmark_case("reference_case")
    case["authorisation"] = {"da09_reference": "provided", "camel_export": "provided"}
    case["source_files"] = {"drawings": "source/drawings.pdf", "camel_input_or_export": "reference/camel_input.csv", "camel_results": "reference/camel_results.csv", "da09_basis": "source/authorised_da09_reference.pdf", "assumption_register": "source/assumptions.json"}
    case["input_mapping"] = {family: [{"field": family, "status": "matched"}] for family in MAPPING_FAMILIES}
    case["input_reconciliation"] = [{"field": "outdoor design conditions", "status": "matched"}]
    case["reference_results"] = {"peak": {"month": "January", "hour": 15}, "rooms": [{"room_id": "room_1", "sensible_kw": 5, "latent_kw": 1, "total_kw": 6, "components": {"solar": 2, "lighting": 1}}], "zones": [{"zone_id": "zone_1", "total_kw": 6, "components": {"solar": 2}}]}
    return case


def main():
    with TemporaryDirectory() as folder:
        path = create_case_folder(Path(folder) / "case", "new_case")
        check("case template created", path.exists())
    case = ready_case()
    validate_benchmark_case(case)
    archie = {"peak": {"month": "January", "hour": 15}, "rooms": [{"room_id": "room_1", "sensible_kw": 5.1, "latent_kw": 1, "total_kw": 6.1, "components": {"solar": 2.2, "lighting": 1}}], "zones": [{"zone_id": "zone_1", "total_kw": 6.1, "components": {"solar": 2.2}}]}
    report = compare_case(case, archie)
    check("ready case compares without parity claim", report["status"], "baseline_compared")
    check("component difference retained", report["rooms"][0]["components"][0]["difference_kw"], 0.2)
    check("final parity remains blocked", report["final_parity_allowed"], False)
    case["input_reconciliation"].append({"field": "storage mass", "status": "unresolved"})
    blocked = compare_case(case, archie)
    check("unresolved input blocks benchmark", blocked["status"], "blocked")
    check("missing material blocks benchmark", compare_case(empty_benchmark_case(), archie)["status"], "blocked")
    incomplete = ready_case()
    incomplete["input_mapping"]["storage_mass"] = []
    check("missing mapping blocks benchmark", compare_case(incomplete, archie)["status"], "blocked")
    adapted = archie_results_from_heat_report({"zone_results": [{"zone_id": "zone_1", "subtotal_kw": 6, "contributions": [{"name": "solar", "sensible_kw": 2, "latent_kw": 0, "total_kw": 2}]}]})
    check("preliminary engine adapter preserves components", adapted["zones"][0]["components"]["solar"]["total_kw"], 2)


if __name__ == "__main__":
    main()
