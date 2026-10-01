"""Shared normalization, ranking, and provenance for model-input resolution.

This module deliberately does not calculate heat loads.  It gives the existing
domain resolvers one stable, auditable register and gives reports a common
source/formula trail.
"""

from copy import deepcopy
import hashlib
import json
import math


SCHEMA_VERSION = 1
ORIGIN_ALIASES = {
    "project_evidence": "direct_project_evidence",
    "scoped_project_evidence": "direct_project_evidence",
    "ai_estimated": "ai_inference",
    "derived": "derived",
    "preliminary_fallback": "controlled_fallback",
    "controlled_fallback": "controlled_fallback",
    "released_source_pack": "released_source_pack",
    "research_candidate": "research_candidate",
    "contractor_override": "contractor_override",
    "unresolved": "unresolved",
}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")).hexdigest()


def _text(value):
    return str(value or "").strip()


def _number(value, default=0.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _confidence(value):
    if isinstance(value, str):
        return {"low": 0.3, "medium": 0.6, "high": 0.85}.get(value.casefold().strip(), 0.0)
    return max(0.0, min(1.0, _number(value, 0.0)))


def target_category(target):
    prefix = _text(target).split(".", 1)[0].casefold()
    return {
        "room": "rooms and geometry",
        "schedule": "internal gains and schedules",
        "surface": "opaque envelope",
        "opening": "openings, glazing, and solar",
        "scenario": "project context",
        "airflow": "ventilation, infiltration, and process air",
        "ahu": "AHU and air-side",
        "plant": "plant and hydraulics",
        "safety": "final design policy",
    }.get(prefix, "model inputs")


def affected_ids(record):
    ids = record.get("affected_component_ids") if isinstance(record, dict) else None
    if isinstance(ids, list) and ids:
        return sorted({_text(item) for item in ids if _text(item)})
    target_id = _text(record.get("target_id")) if isinstance(record, dict) else ""
    return [target_id] if target_id else []


def impact_band(score):
    score = _number(score)
    return "high" if score >= 0.66 else "medium" if score >= 0.33 else "low"


def estimate_impact(record, load_hint=None):
    """Return a stable 0..1 impact score from load influence and uncertainty."""
    if not isinstance(record, dict):
        return 0.0
    confidence = max(0.0, min(1.0, _number(record.get("confidence"), 0.0)))
    uncertainty = 1.0 - confidence
    load = record.get("estimated_load_kw", record.get("load_impact_kw", load_hint))
    load = max(0.0, _number(load, 0.0))
    # log scaling prevents a large project from drowning every smaller issue.
    load_score = load / (load + 10.0) if load else 0.0
    critical = target_category(record.get("target")) in {
        "project context", "rooms and geometry", "opaque envelope",
        "openings, glazing, and solar", "ventilation, infiltration, and process air",
        "AHU and air-side", "plant and hydraulics",
    }
    score = min(1.0, 0.55 * load_score + 0.35 * uncertainty + (0.10 if critical else 0.0))
    return round(score, 6)


def normalize_record(record, *, domain="", component_ids=None, source_fingerprints=None, load_hint=None):
    """Normalize old and new resolver rows without changing their old fields."""
    row = deepcopy(record or {})
    target = _text(row.get("target"))
    row["target_category"] = _text(row.get("target_category")) or (domain or target_category(target))
    row["field"] = _text(row.get("field")) or (target.rsplit(".", 1)[-1] if target else "")
    row["affected_component_ids"] = affected_ids({**row, "affected_component_ids": component_ids or row.get("affected_component_ids")})
    origin = _text(row.get("origin")) or "unresolved"
    # Keep the legacy origin value stable for existing APIs/tests, while
    # exposing a canonical name for cross-domain consumers.
    row["origin"] = origin
    row["normalized_origin"] = ORIGIN_ALIASES.get(origin, origin)
    chain = row.get("source_chain")
    if isinstance(chain, str):
        chain = [chain]
    row["source_chain"] = list(chain or ([row["normalized_origin"]] if row["normalized_origin"] else []))
    row["source_fingerprints"] = deepcopy(row.get("source_fingerprints") or source_fingerprints or {})
    row["formula"] = _text(row.get("formula")) or _text((row.get("derivation") or {}).get("formula"))
    row["operands"] = deepcopy(row.get("operands") or (row.get("derivation") or {}).get("operands") or {})
    row["confidence"] = _confidence(row.get("confidence"))
    row["impact_score"] = estimate_impact(row, load_hint)
    row["impact_band"] = impact_band(row["impact_score"])
    if row.get("status") == "excluded" and not row.get("remediation"):
        row["remediation"] = "Provide cited evidence or an approved project override for this value."
    row["resolution_status"] = row.get("resolution_status") or row.get("status", "needs_review")
    return row


def rank_records(records):
    normalized = [normalize_record(row) for row in records if isinstance(row, dict)]
    return sorted(normalized, key=lambda row: (-row.get("impact_score", 0.0), row.get("target_category", ""), row.get("target_id", ""), row.get("field", "")))


def empty_register():
    result = {
        "schema_version": SCHEMA_VERSION,
        "records": [],
        "coverage_summary": {},
        "review_queue": [],
        "affected_component_ids": [],
        "dependency_fingerprints": {},
        "updated_at": "",
    }
    result["fingerprint"] = fingerprint(result)
    return result


def build_register(records, *, dependency_fingerprints=None, updated_at=""):
    rows = rank_records(records)
    # Several domain artifacts can describe the same target (for example a
    # released room profile plus the room-use resolver's summary). Keep one
    # canonical row in the cross-domain register, while retaining the losing
    # candidates as explicit alternatives for audit/review.
    precedence = {
        "contractor_override": 6,
        "direct_project_evidence": 5,
        "released_source_pack": 4,
        "research_candidate": 3,
        "ai_inference": 2,
        "derived": 2,
        "controlled_fallback": 1,
        "unresolved": 0,
    }
    chosen = {}
    alternatives = {}
    for row in rows:
        key = (row.get("target", ""), row.get("target_id", ""))
        quality = (precedence.get(row.get("normalized_origin", row.get("origin", "")), 0),
                   1 if row.get("status") == "resolved" else 0,
                   row.get("confidence", 0.0), row.get("record_id", ""))
        current = chosen.get(key)
        if current is None:
            chosen[key] = (quality, row)
            continue
        if quality > current[0]:
            alternatives.setdefault(key, []).append(current[1])
            chosen[key] = (quality, row)
        else:
            alternatives.setdefault(key, []).append(row)
    rows = []
    for key, (_quality, row) in chosen.items():
        if alternatives.get(key):
            row["alternatives"] = [
                {"record_id": candidate.get("record_id", ""),
                 "origin": candidate.get("origin", ""),
                 "status": candidate.get("status", ""),
                 "value": deepcopy(candidate.get("value"))}
                for candidate in alternatives[key]
            ]
        rows.append(row)
    rows.sort(key=lambda row: (-row.get("impact_score", 0.0), row.get("target_category", ""), row.get("target_id", ""), row.get("field", "")))
    dependencies = deepcopy(dependency_fingerprints or {})
    for row in rows:
        if not row.get("source_fingerprints"):
            row["source_fingerprints"] = deepcopy(dependencies)
    by_category = {}
    for row in rows:
        category = row.get("target_category", target_category(row.get("target", "")))
        by_category[category] = by_category.get(category, 0) + 1
    status_names = ("resolved", "provisional", "needs_review", "blocked", "excluded", "stale", "proposed")
    counts = {status: sum(row.get("status") == status for row in rows) for status in status_names}
    research_pending = sum(
        row.get("normalized_origin") == "research_candidate" and row.get("status") != "resolved"
        for row in rows
    )
    affected = sorted({item for row in rows if row.get("status") in {"needs_review", "blocked", "excluded", "stale"} for item in affected_ids(row)})
    result = {
        "schema_version": SCHEMA_VERSION,
        "records": rows,
        "coverage_summary": {"total": len(rows), **counts, "research_pending": research_pending,
                             "by_category": by_category,
                             "affected_component_count": len(affected), "complete": not affected,
                             "included_scope_only": bool(affected)},
        "review_queue": [row for row in rows if row.get("status") in {"needs_review", "blocked", "excluded", "stale"} or row.get("origin") == "controlled_fallback"],
        "affected_component_ids": affected,
        "dependency_fingerprints": deepcopy(dependency_fingerprints or {}),
        "updated_at": updated_at,
    }
    result["fingerprint"] = fingerprint({key: value for key, value in result.items() if key != "fingerprint"})
    return result


def _walk_load_components(report):
    for scenario in report.get("scenario_results", []) if isinstance(report, dict) else []:
        for level in ("rooms", "zones", "floors"):
            for entity in scenario.get(level, []) if isinstance(scenario, dict) else []:
                entity_id = entity.get("room_id") or entity.get("zone_id") or entity.get("floor_id") or ""
                for hour in entity.get("hours", []) if isinstance(entity, dict) else []:
                    for component_name, component in (hour.get("components") or {}).items() if isinstance(hour, dict) else []:
                        if isinstance(component, dict):
                            yield entity_id, hour.get("hour"), component_name, component


def build_report_provenance(report, register):
    """Add a render-time-friendly provenance index; calculation values are untouched."""
    rows = register.get("records", []) if isinstance(register, dict) else []
    by_id = {item for row in rows for item in row.get("affected_component_ids", [])}
    index = {}
    for row in rows:
        for component_id in row.get("affected_component_ids", []):
            index.setdefault(component_id, []).append({
                "record_id": row.get("record_id", ""), "target": row.get("target", ""),
                "target_category": row.get("target_category", ""), "origin": row.get("origin", ""),
                "status": row.get("status", ""), "value": row.get("value"), "unit": row.get("unit", ""),
                "formula": row.get("formula", ""), "operands": deepcopy(row.get("operands", {})),
                "source_chain": deepcopy(row.get("source_chain", [])), "citation": deepcopy(row.get("citation", {})),
                "confidence": row.get("confidence", 0.0), "impact_score": row.get("impact_score", 0.0),
                "rationale": row.get("rationale", ""), "remediation": row.get("remediation", ""),
            })
    components = []
    for entity_id, hour, name, component in _walk_load_components(report):
        refs = index.get(entity_id, [])
        components.append({"entity_id": entity_id, "hour": hour, "component": name, "load_kw": component.get("total_kw"), "provenance": refs})
    return {"schema_version": 1, "register_fingerprint": register.get("fingerprint", ""), "by_component_id": index, "governing_components": components}
