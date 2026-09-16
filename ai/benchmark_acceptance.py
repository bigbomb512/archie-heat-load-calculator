"""Explicit, fingerprint-bound acceptance of a room/zone cooling benchmark.

An acceptance is a project-local reviewer declaration, not a credential verifier
or a blanket certification of the engine. No acceptance is inferred from tests.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

from ai.parity_harness import COMPONENTS, mapped_inputs

SCOPE = "room_zone_peak_comparison"
MATERIALS = ("camel_input_or_export", "camel_results", "da09_basis", "assumption_register")


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def engine_fingerprint():
    root = Path(__file__).parent
    names = ("hourly_loads.py", "heat_loads.py", "envelope.py", "glazing_calculation.py", "calculator_inputs.py",
             "infiltration_gate.py", "cooling_readiness.py", "parity_harness.py", "benchmark_acceptance.py")
    files = {"ai/" + name: root / name for name in names}
    files.update({"backend/" + name: root.parent / "backend" / name for name in ("web_app.py", "benchmark_service.py")})
    return fingerprint({name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in files.items()})


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _material_hashes(case, root):
    hashes, issues = {}, []
    root = Path(root).resolve()
    for name in MATERIALS:
        supplied = case.get("source_files", {}).get(name)
        path = (root / str(supplied or "")).resolve()
        if not supplied or not path.is_relative_to(root) or not path.is_file():
            issues.append(f"Reference file unavailable within benchmark folder: {name}")
            continue
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        hashes[name] = digest.hexdigest()
    return hashes, issues


def assess_acceptance(case, actual, comparison, *, case_root, source_report, current=True):
    issues = []
    materials, material_issues = _material_hashes(case, case_root)
    issues.extend(material_issues)
    if comparison.get("status") != "baseline_compared" or comparison.get("missing_reference_material"):
        issues.append("Benchmark reference readiness is incomplete.")
    if any(row.get("status") != "matched" for row in mapped_inputs(case)):
        issues.append("All input mappings must be matched before acceptance.")
    if comparison.get("peak_comparison", {}).get("status") != "matched":
        issues.append("Reference and Archie governing month/hour must match.")
    if not current or not source_report or source_report.get("status") != "review_ready" or not source_report.get("project_peak"):
        issues.append("A current, review-ready, complete-scope hourly report is required.")
    if source_report and source_report.get("calculation_engine_fingerprint") != engine_fingerprint():
        issues.append("Recalculate the hourly report with the current calculation code before acceptance.")
    if source_report and (source_report.get("blocked_reasons") or source_report.get("calculator_input_coverage", {}).get("complete_scope") is False):
        issues.append("Blocked or partial hourly scope cannot be accepted.")
    policy = case.get("comparison_policy") or {}
    if policy.get("tolerance_status") != "approved" or not str(policy.get("rounding_policy", "")).strip():
        issues.append("An explicitly approved tolerance policy and rounding rationale are required.")
    limits = [policy.get(key) for key in ("component_tolerance_percent", "total_tolerance_percent", "absolute_tolerance_kw")]
    valid_limits = all(_finite(v) and v >= 0 for v in limits)
    if not valid_limits:
        issues.append("Supply finite non-negative component/total percent and absolute kW tolerances.")
    exclusions = policy.get("excluded_components", [])
    if not isinstance(exclusions, list) or any(name not in COMPONENTS for name in exclusions):
        issues.append("Excluded components must be explicit supported component names.")
        exclusions = []

    def check(expected, observed, percent, label):
        if not _finite(expected) or not _finite(observed):
            issues.append(f"Missing or invalid numeric result: {label}")
        elif valid_limits and abs(observed - expected) > max(limits[2], abs(expected) * percent / 100) + 1e-12:
            issues.append(f"Outside approved tolerance: {label}")

    references = case.get("reference_results") or {}
    for scope, key in (("rooms", "room_id"), ("zones", "zone_id")):
        expected_rows, actual_rows = references.get(scope) or [], actual.get(scope) or []
        expected_ids = [row.get("entity_id") or row.get(key) for row in expected_rows]
        actual_ids = [row.get("entity_id") or row.get(key) for row in actual_rows]
        if (not expected_rows or not actual_rows or None in expected_ids or None in actual_ids
                or len(set(expected_ids)) != len(expected_ids) or len(set(actual_ids)) != len(actual_ids)
                or set(expected_ids) != set(actual_ids)):
            issues.append(f"Complete unique matched {scope} are required.")
            continue
        lookup = dict(zip(actual_ids, actual_rows))
        for identity, expected in zip(expected_ids, expected_rows):
            observed = lookup[identity]
            unknown = set(expected.get("components") or {}) | set(observed.get("components") or {})
            if unknown - set(COMPONENTS):
                issues.append(f"Unsupported component comparison: {scope}/{identity}")
            for metric in ("sensible_kw", "latent_kw", "total_kw", "design_total_kw"):
                check(expected.get(metric), observed.get(metric), limits[1] if valid_limits else 0, f"{scope}/{identity}/{metric}")
            for component in COMPONENTS:
                left = (expected.get("components") or {}).get(component)
                right = (observed.get("components") or {}).get(component)
                left = left.get("total_kw") if isinstance(left, dict) else left
                right = right.get("total_kw") if isinstance(right, dict) else right
                if component in exclusions:
                    if left not in (None, 0) or right not in (None, 0):
                        issues.append(f"A nonzero component cannot be excluded: {scope}/{identity}/{component}")
                    continue
                check(left, right, limits[0] if valid_limits else 0, f"{scope}/{identity}/{component}")
    binding = {
        "case_fingerprint": fingerprint(case), "results_fingerprint": fingerprint(actual),
        "source_report_fingerprint": fingerprint(source_report), "reference_hashes": materials,
        "engine_fingerprint": engine_fingerprint(), "scope": SCOPE,
    }
    return {"eligible": not issues, "issues": sorted(set(issues)), "binding": binding,
            "binding_fingerprint": fingerprint(binding)}


def record_acceptance(assessment, reviewer):
    if not assessment["eligible"]:
        raise ValueError("Benchmark acceptance blocked: " + "; ".join(assessment["issues"]))
    required = ("engineer_name", "credential", "rationale")
    if not isinstance(reviewer, dict) or any(not isinstance(reviewer.get(k), str) or not reviewer[k].strip() for k in required):
        raise ValueError("Acceptance requires engineer_name, credential, and rationale.")
    return {"schema_version": 1, "status": "accepted", "scope": SCOPE,
            "reviewer": {key: reviewer[key].strip() for key in required},
            "accepted_at": datetime.now(timezone.utc).isoformat(),
            "binding_fingerprint": assessment["binding_fingerprint"], "binding": deepcopy(assessment["binding"])}


def apply_acceptance(comparison, assessment, acceptance):
    result = deepcopy(comparison)
    accepted = bool(acceptance and acceptance.get("status") == "accepted"
                    and acceptance.get("scope") == SCOPE and acceptance.get("accepted_at")
                    and all(str((acceptance.get("reviewer") or {}).get(k, "")).strip() for k in ("engineer_name", "credential", "rationale"))
                    and acceptance.get("binding_fingerprint") == assessment["binding_fingerprint"]
                    and acceptance.get("binding") == assessment["binding"] and assessment["eligible"])
    result["validation"] = {"status": "accepted" if accepted else "revoked" if acceptance and acceptance.get("status") == "revoked" else "stale" if acceptance else "not_accepted",
                            "scope": SCOPE, "eligible_for_acceptance": assessment["eligible"],
                            "issues": assessment["issues"], "binding_fingerprint": assessment["binding_fingerprint"]}
    if acceptance and not accepted and acceptance.get("status") != "revoked":
        result["validation"]["issues"] = list(result["validation"]["issues"]) + ["Recorded acceptance no longer matches the current benchmark; review and accept the new binding."]
    result["final_parity_allowed"] = accepted
    if accepted:
        result["status"] = "validated"
        result["validation"]["acceptance"] = deepcopy(acceptance)
        result["reason"] = "Named reviewer accepted this exact room/zone peak benchmark. No floor/project, other-scenario, or engine-wide validation is implied."
    return result
