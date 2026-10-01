"""Project-level safety-factor policy resolution and final-rollup helpers.

The policy is deliberately separate from room inputs.  Legacy room factors are
still retained in the input artifacts, but policy-mode calculations apply the
factor once, after the coincident project total has been selected.
"""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math

SCHEMA_VERSION = 1
MODES = ("cooling", "heating")
STATUSES = {"proposed", "provisional", "approved", "blocked", "stale"}


def now():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def empty_safety_factor_resolution():
    result = {
        "schema_version": SCHEMA_VERSION, "policies": {}, "source_fingerprints": {},
        "status": "blocked", "updated_at": "", "issues": [],
    }
    for mode in MODES:
        result["policies"][mode] = _empty_policy(mode)
    result["fingerprint"] = _artifact_fingerprint(result)
    return result


def _empty_policy(mode):
    return {"mode": mode, "factor": None, "allowance_percent": None, "source_type": "",
            "source": "", "citations": [], "scope": "", "applicability": "",
            "origin": "unresolved", "confidence": 0.0, "rationale": "",
            "assumptions": [], "status": "blocked", "reviewer": "",
            "approval_date": "", "approval_scope": "", "source_fingerprint": "",
            "requirements_fingerprint": "", "ai_fingerprint": "", "pack_fingerprint": "",
            "override_fingerprint": "", "unresolved_fields": ["safety factor policy"],
            "remediation": "Provide a cited project brief or engineer-approved safety-factor policy."}


def _artifact_fingerprint(artifact):
    return fingerprint({key: value for key, value in artifact.items() if key != "fingerprint"})


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def normalize_factor(value, kind="factor"):
    """Normalize a multiplier or explicitly-labelled percentage to a multiplier."""
    if isinstance(value, str) and value.strip().endswith("%"):
        value = value.strip()[:-1]
        kind = "percentage"
    number = _number(value)
    if number is None:
        raise ValueError("Safety factor must be a finite number.")
    kind = str(kind or "factor").strip().lower()
    if kind in {"percent", "percentage", "%"}:
        if number < 0 or number > 100:
            raise ValueError("Safety percentage must be between 0 and 100.")
        number = 1.0 + number / 100.0
    elif kind not in {"factor", "multiplier"}:
        raise ValueError("Safety factor input kind must be factor or percentage.")
    if number < 1.0:
        raise ValueError("Safety factor must be at least 1.0.")
    return round(number, 6)


def _citations(row):
    value = row.get("citations", row.get("evidence", [])) if isinstance(row, dict) else []
    if isinstance(value, dict):
        value = [value]
    return [deepcopy(item) for item in value if isinstance(item, dict)]


def _candidate(mode, row, origin, source_type, pack_fingerprint=""):
    row = row if isinstance(row, dict) else {}
    raw = row.get("factor", row.get("multiplier", row.get("value")))
    kind = row.get("input_kind", row.get("unit", "factor"))
    factor = normalize_factor(raw, kind) if raw is not None else None
    citations = _citations(row)
    source = str(row.get("source", row.get("reference", "")) or "").strip()
    if origin not in {"controlled_preliminary_fallback", "contractor_override"} and (not source or not citations):
        return None
    result = _empty_policy(mode)
    result.update({
        "factor": factor, "allowance_percent": round((factor - 1.0) * 100, 4) if factor is not None else None,
        "source_type": source_type, "source": source, "citations": citations,
        "scope": str(row.get("scope", "project final coincident total") or "project final coincident total"),
        "applicability": str(row.get("applicability", mode) or mode), "origin": origin,
        "confidence": float(row.get("confidence", 0.95 if origin in {"project_brief", "engineer_instruction", "contractor_override"} else 0.65)),
        "rationale": str(row.get("rationale", "Resolved from the highest-precedence applicable safety-factor policy.") or ""),
        "assumptions": list(row.get("assumptions", [])) if isinstance(row.get("assumptions", []), list) else [],
        "status": "provisional" if origin == "controlled_preliminary_fallback" else "proposed",
        "source_fingerprint": fingerprint({"source": source, "citations": citations}),
        "pack_fingerprint": pack_fingerprint,
        "unresolved_fields": [], "remediation": "",
    })
    return result


def _mode_row(sources, mode):
    for key in (f"{mode}_safety_factor", f"{mode}_policy", "safety_factor_policy"):
        value = sources.get(key) if isinstance(sources, dict) else None
        if isinstance(value, dict) and mode in value and isinstance(value[mode], dict):
            return value[mode]
        if isinstance(value, dict) and ("factor" in value or "value" in value or "multiplier" in value):
            return value
        if value is not None and not isinstance(value, (dict, list)):
            return {"value": value, "input_kind": sources.get(f"{mode}_safety_factor_kind", "factor"), "source": sources.get(f"{mode}_safety_factor_source", ""), "citations": sources.get(f"{mode}_safety_factor_citations", [])}
    return None


def resolve(sources=None, pack_fingerprint="", overrides=None, mode_scope=None):
    """Resolve cooling/heating policies from deterministic source precedence."""
    sources = sources if isinstance(sources, dict) else {}
    overrides = overrides if isinstance(overrides, dict) else {}
    result = empty_safety_factor_resolution()
    result["source_fingerprints"] = deepcopy(sources.get("source_fingerprints", {})) if isinstance(sources.get("source_fingerprints", {}), dict) else {}
    for mode in (mode_scope or MODES):
        row = None
        candidates = [
            (overrides.get(mode), "contractor_override", "contractor_override"),
            (_mode_row(sources, mode), "project_brief", "project_brief"),
            (sources.get(f"{mode}_engineer_instruction"), "engineer_instruction", "engineer_instruction"),
            (sources.get(f"{mode}_approved_rule"), "approved_rule", "approved_rule"),
        ]
        for candidate, origin, label in candidates:
            if candidate is None:
                continue
            try:
                row = _candidate(mode, candidate, origin, label, pack_fingerprint)
            except ValueError as error:
                row = _empty_policy(mode)
                row["unresolved_fields"] = [str(error)]
                row["remediation"] = "Provide an explicit multiplier or percentage with a valid citation."
            if row:
                break
        if row is None:
            row = _candidate(mode, {"factor": 1.10, "source": "au-preliminary-v3 controlled safety-factor assumption", "rationale": "No project policy was supplied; draft mode uses the named controlled fallback.", "assumptions": ["controlled preliminary assumption"]}, "controlled_preliminary_fallback", "controlled preliminary fallback", pack_fingerprint)
        result["policies"][mode] = row
    result["status"] = "provisional" if any(row["status"] == "provisional" for row in result["policies"].values()) else ("proposed" if any(row["factor"] is not None for row in result["policies"].values()) else "blocked")
    result["updated_at"] = now()
    result["fingerprint"] = _artifact_fingerprint(result)
    return result


def validate(artifact):
    result = deepcopy(artifact if isinstance(artifact, dict) else empty_safety_factor_resolution())
    result.setdefault("policies", {})
    for mode in MODES:
        row = result["policies"].setdefault(mode, _empty_policy(mode))
        if row.get("factor") is not None:
            row["factor"] = normalize_factor(row["factor"])
            row["allowance_percent"] = round((row["factor"] - 1) * 100, 4)
        if row.get("status") not in STATUSES:
            row["status"] = "blocked"
        if row.get("factor") is None:
            row["status"] = "blocked"
    result["fingerprint"] = _artifact_fingerprint(result)
    return result


def apply_override(artifact, mode, value, input_kind="factor", source="contractor override", citations=None, reviewer="", rationale=""):
    result = validate(artifact)
    if mode not in MODES:
        raise ValueError("Safety-factor mode must be cooling or heating.")
    factor = normalize_factor(value, input_kind)
    row = _candidate(mode, {"factor": factor, "source": source, "citations": citations or [{"reference": source, "excerpt": "Contractor project override"}], "rationale": rationale or "Contractor override."}, "contractor_override", "contractor override")
    row["reviewer"] = reviewer
    result["policies"][mode] = row
    result["status"] = "proposed"
    result["updated_at"] = now()
    result["fingerprint"] = _artifact_fingerprint(result)
    return result


def clear_override(artifact, mode):
    result = validate(artifact)
    if mode not in MODES:
        raise ValueError("Safety-factor mode must be cooling or heating.")
    result["policies"][mode]["origin"] = "unresolved"
    result["policies"][mode]["status"] = "blocked"
    result["policies"][mode]["factor"] = None
    result["policies"][mode]["allowance_percent"] = None
    result["policies"][mode]["remediation"] = "Resolve a cited policy or use the draft-only 1.10 fallback."
    result["updated_at"] = now()
    result["fingerprint"] = _artifact_fingerprint(result)
    return result


def approve(artifact, mode, engineer, approval_date, scope):
    result = validate(artifact)
    row = result["policies"].get(mode)
    if not row or row.get("factor") is None or not row.get("citations"):
        raise ValueError("An engineer-approved safety policy requires a valid factor and citation.")
    if not str(engineer or "").strip() or not str(approval_date or "").strip() or not str(scope or "").strip():
        raise ValueError("Engineer identity, approval date, and policy scope are required.")
    row.update({"status": "approved", "reviewer": str(engineer), "approval_date": str(approval_date), "approval_scope": str(scope)})
    result["status"] = "approved"
    result["updated_at"] = now()
    result["fingerprint"] = _artifact_fingerprint(result)
    return result


def policy_for(artifact, mode, *, preliminary=False):
    artifact = validate(artifact)
    row = deepcopy(artifact.get("policies", {}).get(mode, _empty_policy(mode)))
    if row.get("factor") is None:
        return None
    row["policy_fingerprint"] = artifact.get("fingerprint", "")
    if not preliminary and row.get("status") != "approved":
        return {**row, "blocked": True, "blocked_reason": "A cited engineer-approved safety policy is required for reviewed final design."}
    return {**row, "blocked": False}


def apply_final_policy(report, policy, *, mode="cooling", preliminary=False):
    """Apply one factor to the selected project peak, never to room totals."""
    result = deepcopy(report)
    policy = deepcopy(policy or {})
    if not policy or policy.get("factor") is None or policy.get("blocked"):
        result.setdefault("blocked_reasons", []).append(policy.get("blocked_reason", "Safety-factor policy is unresolved."))
        result["safety_policy_applied"] = False
        result["safety_policy"] = policy
        if not preliminary:
            result["status"] = "blocked"
            result["project_peak"] = {}
        return result
    if not preliminary and (result.get("status") != "review_ready" or not result.get("scope_summary", {}).get("complete_scope", False)):
        result.setdefault("blocked_reasons", []).append("A complete reviewed project scope is required before applying the final design policy.")
        result["safety_policy_applied"] = False
        result["safety_policy"] = policy
        result["status"] = "blocked"
        return result
    factor = float(policy["factor"])
    peak = deepcopy(result.get("included_scope_peak") or result.get("project_peak") or {})
    raw = peak.get("subtotal_kw")
    if raw is None:
        raw = peak.get("raw_coincident_total_kw", peak.get("design_total_kw"))
    if raw is None:
        result.setdefault("blocked_reasons", []).append("No coincident project total is available for final safety policy.")
        result["safety_policy_applied"] = False
        return result
    # Model-level room factors are retained as cited evidence, but the hourly
    # cooling/heating calculators neutralize them whenever a project policy is
    # supplied. Only flag a room factor that actually survived into calculated
    # room hours, where it would be compounded by this project roll-up.
    legacy = [
        {"room_id": room.get("room_id", ""), "factor": float(hour.get("safety_factor", 1.0))}
        for scenario in result.get("scenario_results", [])
        for room in scenario.get("rooms", [])
        for hour in room.get("hours", [])
        if float(hour.get("safety_factor", 1.0)) > 1.0
    ]
    if legacy:
        result["safety_policy_applied"] = False
        result["blocked_reasons"] = list(dict.fromkeys(result.get("blocked_reasons", []) + ["Legacy room-level safety factor would be compounded with the project policy."]))
        result["status"] = "blocked"
        result["project_peak"] = {}
        return result
    raw = round(float(raw), 4)
    allowance = round(raw * (factor - 1.0), 4)
    final = round(raw * factor, 4)
    peak.update({"raw_coincident_total_kw": raw, "safety_factor": factor, "safety_allowance_kw": allowance, "design_total_kw": final, "final_design_total_kw": final})
    result["included_scope_peak"] = peak
    result["project_peak"] = deepcopy(peak)
    result.update({"raw_coincident_total_kw": raw, "safety_factor": factor, "safety_allowance_kw": allowance, "final_design_total_kw": final, "safety_policy": policy, "safety_policy_applied": True, "safety_policy_mode": mode, "safety_policy_fingerprint": policy.get("policy_fingerprint", policy.get("source_fingerprint", ""))})
    if preliminary:
        result["status"] = "draft"
        result["label"] = "AI preliminary estimate — not engineering reviewed or validated"
    return result
