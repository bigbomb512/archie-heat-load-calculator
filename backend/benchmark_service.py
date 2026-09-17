"""Project-local benchmark acceptance; never edits the hourly report."""
from copy import deepcopy
from datetime import datetime, timezone

from ai.benchmark_acceptance import assess_acceptance, apply_acceptance, fingerprint, record_acceptance
from ai.parity_harness import archie_results_from_hourly_load_report, compare_case


def prepare(web, project, case, actual, *, action="build", data=None):
    data = data or {}
    if action not in {"build", "accept", "revoke"}:
        raise ValueError("Benchmark action must be build, accept, or revoke.")
    root = web.benchmark_case_dir(project)
    acceptance_path = root / "benchmark_acceptance.json"
    acceptance = web.load_json(acceptance_path) if acceptance_path.exists() else None
    current_path = web.current_hourly_load_report_path(project)
    source = web.load_json(current_path) if current_path else None
    comparison = compare_case(case, actual)
    assessment = assess_acceptance(case, actual, comparison, case_root=root, source_report=source, current=bool(current_path))
    if source and archie_results_from_hourly_load_report(source) != actual:
        assessment["eligible"] = False
        assessment["issues"].append("Comparison results do not match the current hourly report.")
    if action in {"accept", "revoke"}:
        if data.get("expected_binding_fingerprint") != assessment["binding_fingerprint"]:
            raise ValueError("Benchmark changed or no reviewed binding supplied; rebuild and review before accepting/revoking.")
        if action == "accept":
            acceptance = record_acceptance(assessment, data.get("reviewer"))
        else:
            reviewer = data.get("reviewer") or {}
            if not acceptance or any(not isinstance(reviewer.get(k), str) or not reviewer[k].strip() for k in ("engineer_name", "credential", "rationale")):
                raise ValueError("Revocation requires an existing acceptance and a named reviewer, credential, and rationale.")
            acceptance = {**deepcopy(acceptance), "status": "revoked", "revoked_by": deepcopy(reviewer),
                          "revoked_at": datetime.now(timezone.utc).isoformat()}
        # Retain previous decisions, including revoked or superseded records.
        history = root / "acceptance_history"
        history.mkdir(parents=True, exist_ok=True)
        web._atomic_write(history / (fingerprint(acceptance) + ".json"), acceptance)
        web._atomic_write(acceptance_path, acceptance)
    result = apply_acceptance(comparison, assessment, acceptance)
    result["comparison_inputs"] = deepcopy(actual)
    return result


def read(web, project, case, saved):
    """Re-evaluate acceptance on every read; stale evidence never stays green."""
    if not case:
        result = deepcopy(saved)
        result["status"] = "blocked"
        result["final_parity_allowed"] = False
        result["validation"] = {"status": "stale", "issues": ["The benchmark case is missing; rebuild before acceptance."]}
        return result
    if "comparison_inputs" not in saved:
        result = deepcopy(saved)
        result["final_parity_allowed"] = False
        if result.get("status") == "validated":
            result["status"] = "baseline_compared"
        result["validation"] = {"status": "not_accepted", "issues": ["Rebuild the legacy comparison before acceptance."]}
        return result
    return prepare(web, project, case, saved["comparison_inputs"])
