"""Proposal-only AI names and confidence for extracted evidence components.

This module deliberately sits beside, rather than inside, the evidence and
calculation models.  It gives an AI (or the local placeholder workflow) a
place to improve the human-facing interpretation of a component without ever
changing the component's canonical identity, type, geometry, or eligibility.
"""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math


SCHEMA_VERSION = 1
AI_REVISION_KINDS = {"ai_update", "ai_update_conflict", "reviewer_update", "reviewer_unlock"}
SUPPORTED_TYPES = {
    "room", "zone", "wall", "surface", "opening", "equipment", "schedule",
    "floor", "construction", "lighting", "dimension", "other",
}


def now():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def confidence_band(score):
    score = validate_confidence(score)
    return "low" if score < 0.50 else "medium" if score < 0.80 else "high"


def validate_confidence(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("AI confidence must be a finite number from 0.0 to 1.0.")
    value = float(value)
    if not 0.0 <= value <= 1.0:
        raise ValueError("AI confidence must be from 0.0 to 1.0.")
    return value


def _text(value, field, *, required=False, limit=240):
    value = str(value or "").strip()
    if required and not value:
        raise ValueError(f"{field} is required.")
    if len(value) > limit:
        raise ValueError(f"{field} is too long.")
    return value


def _source_label(value, field, *, limit=240):
    """Bound untrusted PDF labels for display without weakening AI-input validation.

    A drawing's OCR or structured text can contain a whole note paragraph in
    a field that nominally represents a component label.  That is evidence to
    retain elsewhere, not a reason to abort the full drawing analysis.  The
    interpretation register only needs a concise display label; its immutable
    ID and citations remain sourced from the original component.
    """
    value = str(value or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit - 1].rstrip() + "…"


def _normalise_refs(rows, *, require=False, source_derived=False):
    """Normalise citations without allowing raw PDF text to abort analysis.

    Source-derived citations originate in OCR and drawing extraction. They are
    retained in their authoritative evidence artifacts, while this display
    register keeps a bounded summary. AI and reviewer payloads stay strict:
    overlong submitted values are rejected rather than silently changed.
    """
    if not isinstance(rows, list):
        raise ValueError("Evidence references must be a list.")
    text = _source_label if source_derived else _text
    result = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Each evidence reference must be an object.")
        page = row.get("page")
        if page is not None and (isinstance(page, bool) or not isinstance(page, int) or page <= 0):
            raise ValueError("Evidence-reference page numbers must be positive integers.")
        reference = text(row.get("reference") or row.get("artifact") or row.get("drawing_number"), "Evidence reference")
        excerpt = text(row.get("excerpt"), "Evidence excerpt", limit=1000)
        if not page and not reference:
            raise ValueError("Each evidence reference needs a page or a source reference.")
        result.append({
            "page": page, "drawing_number": text(row.get("drawing_number"), "Drawing number"),
            "reference": reference, "excerpt": excerpt, "crop": text(row.get("crop"), "Evidence crop", limit=500),
        })
    if require and not result:
        raise ValueError("AI interpretations require at least one cited evidence reference.")
    return sorted(result, key=lambda row: (row.get("page") or 0, row["drawing_number"], row["reference"], row["excerpt"]))


def _canonical_type(value):
    value = str(value or "other").strip().lower().replace(" ", "_")
    aliases = {"window": "opening", "glazing": "opening", "partition": "surface", "ceiling": "surface", "roof": "surface", "floor": "floor"}
    value = aliases.get(value, value)
    return value if value in SUPPORTED_TYPES else "other"


def _component_id(canonical_type, source_fingerprint, page, drawing_number, anchors):
    # Display names are intentionally absent.  Anchor values are source IDs,
    # tags, geometry references, and source locations; they identify the same
    # component even after an interpretation changes its visible name.
    payload = {
        "canonical_type": canonical_type, "source_fingerprint": source_fingerprint,
        "page": page, "drawing_number": drawing_number, "anchors": sorted(str(item) for item in anchors if item),
    }
    return "component_" + fingerprint(payload)[:20]


def _refs_from_entity(entity):
    refs = list(entity.get("citations") or [])
    if entity.get("source"):
        refs.append(entity["source"])
    return _normalise_refs(refs, source_derived=True)


def _seed_from_entity(entity, source_fingerprint):
    value = entity.get("value") or {}
    source = entity.get("source") or {}
    canonical_type = _canonical_type(entity.get("kind"))
    anchors = [
        entity.get("entity_id"), *(entity.get("evidence_ids") or []),
        value.get("id"), value.get("tag"), value.get("geometry_reference"),
        value.get("fixture_tag"), value.get("reference"),
    ]
    return {
        "component_id": _component_id(canonical_type, source_fingerprint, source.get("page"), source.get("drawing_number"), anchors),
        "canonical_type": canonical_type,
        "source_component_ids": [str(entity.get("entity_id", ""))],
        "legacy_aliases": [str(item) for item in [entity.get("entity_id"), *(entity.get("evidence_ids") or [])] if item],
        "original_label": _source_label(entity.get("label") or value.get("name") or value.get("tag") or value.get("reference"), "Original label"),
        "evidence_refs": _refs_from_entity(entity),
    }


def _seed_from_candidate(candidate, source_fingerprint):
    source = candidate.get("source") or {}
    category = candidate.get("category") or candidate.get("kind")
    canonical_type = _canonical_type(category)
    anchors = [candidate.get("candidate_id"), candidate.get("target"), candidate.get("room_id"), candidate.get("field"), candidate.get("binding_id")]
    return {
        "component_id": _component_id(canonical_type, source_fingerprint, source.get("page"), source.get("drawing_number"), anchors),
        "canonical_type": canonical_type,
        "source_component_ids": [str(candidate.get("candidate_id", ""))],
        "legacy_aliases": [str(item) for item in [candidate.get("candidate_id"), candidate.get("target")] if item],
        "original_label": _source_label(candidate.get("label") or candidate.get("target") or candidate.get("field"), "Original label"),
        "evidence_refs": _normalise_refs([source] if source else [], source_derived=True),
    }


def component_seeds(fusion=None, calculation_evidence=None, calculator_draft=None):
    """Create display-component seeds from the existing normalized artifacts."""
    fusion = fusion or {}
    calculation_evidence = calculation_evidence or {}
    calculator_draft = calculator_draft or {}
    source_fingerprint = str(fusion.get("source_fingerprint") or calculation_evidence.get("source_fingerprint") or "")
    seeds = []
    for entity in fusion.get("entities", []):
        if isinstance(entity, dict) and entity.get("entity_id"):
            seeds.append(_seed_from_entity(entity, source_fingerprint))
    for candidate in calculation_evidence.get("candidates", []):
        if isinstance(candidate, dict) and candidate.get("candidate_id"):
            seeds.append(_seed_from_candidate(candidate, source_fingerprint))
    for group, rows in (calculator_draft.get("candidates") or {}).items():
        for candidate in rows if isinstance(rows, list) else []:
            if not isinstance(candidate, dict) or not candidate.get("candidate_id"):
                continue
            value = candidate.get("value") or {}
            source = (candidate.get("citations") or [{}])[0]
            canonical_type = _canonical_type(candidate.get("kind") or group)
            anchors = [candidate.get("candidate_id"), value.get("room_id"), value.get("zone_id"), value.get("floor_id"), value.get("schedule_id"), value.get("record_id")]
            seeds.append({
                "component_id": _component_id(canonical_type, source_fingerprint, source.get("page"), source.get("drawing_number"), anchors),
                "canonical_type": canonical_type,
                "source_component_ids": [str(candidate["candidate_id"])],
                "legacy_aliases": [str(candidate["candidate_id"])],
                "original_label": _source_label(value.get("name") or value.get("title") or candidate.get("kind") or group, "Original label"),
                "evidence_refs": _normalise_refs(candidate.get("citations") or [], source_derived=True),
            })
    unique = {}
    for seed in seeds:
        current = unique.get(seed["component_id"])
        if current is None:
            unique[seed["component_id"]] = seed
            continue
        current["source_component_ids"] = sorted(set(current["source_component_ids"] + seed["source_component_ids"]))
        current["legacy_aliases"] = sorted(set(current["legacy_aliases"] + seed["legacy_aliases"]))
        current["evidence_refs"] = _normalise_refs(current["evidence_refs"] + seed["evidence_refs"])
    return sorted(unique.values(), key=lambda item: item["component_id"])


def source_artifact_fingerprints(fusion=None, calculation_evidence=None, calculator_draft=None):
    return {
        "architect_evidence_fusion": str((fusion or {}).get("fingerprint") or fingerprint(fusion or {})),
        "calculation_input_evidence": str((calculation_evidence or {}).get("fingerprint") or fingerprint(calculation_evidence or {})),
        "calculator_draft": str((calculator_draft or {}).get("fingerprint") or fingerprint(calculator_draft or {})),
    }


def _empty_interpretation(seed):
    return {
        **deepcopy(seed), "display_name": seed["original_label"], "display_name_origin": "source",
        "confidence_score": 0.0, "confidence_band": "low", "confidence_origin": "unassigned",
        "rationale": "", "provider": {"provider": "", "model": "", "prompt_policy_fingerprint": ""},
        "latest_ai_proposal": None,
        "reviewer_locks": {"display_name": None, "confidence": None},
        "competing_updates": [], "revision_history": [],
    }


def build_artifact(existing=None, *, fusion=None, calculation_evidence=None, calculator_draft=None):
    """Refresh the source register while retaining reviews for stable IDs."""
    existing_by_id = {row.get("component_id"): row for row in (existing or {}).get("interpretations", []) if isinstance(row, dict)}
    seeds = component_seeds(fusion, calculation_evidence, calculator_draft)
    rows = []
    for seed in seeds:
        prior = existing_by_id.get(seed["component_id"])
        if prior:
            row = deepcopy(prior)
            row.update({key: deepcopy(value) for key, value in seed.items()})
        else:
            row = _empty_interpretation(seed)
        rows.append(row)
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "source_fingerprint": str((fusion or {}).get("source_fingerprint") or (calculation_evidence or {}).get("source_fingerprint") or ""),
        "source_artifact_fingerprints": source_artifact_fingerprints(fusion, calculation_evidence, calculator_draft),
        "interpretations": rows,
        "fingerprint": "",
    }
    artifact["fingerprint"] = fingerprint({key: value for key, value in artifact.items() if key != "fingerprint"})
    return artifact


def is_current(artifact, *, fusion=None, calculation_evidence=None, calculator_draft=None):
    if not artifact:
        return False
    return artifact.get("source_artifact_fingerprints") == source_artifact_fingerprints(fusion, calculation_evidence, calculator_draft)


def _history(kind, *, actor, fields, proposal=None, note=""):
    row = {"revision_id": "interpretation_" + hashlib.sha256(f"{now()}:{kind}:{actor}:{fields}".encode()).hexdigest()[:16],
           "at": now(), "kind": kind, "actor": _text(actor, "Actor", required=True), "fields": sorted(fields), "note": _text(note, "Note", limit=1000)}
    if proposal is not None:
        row["proposal"] = deepcopy(proposal)
    return row


def _proposal(payload):
    if not isinstance(payload, dict):
        raise ValueError("Each AI interpretation update must be an object.")
    display_name = _text(payload.get("display_name"), "AI display name", required=True)
    score = validate_confidence(payload.get("confidence_score", payload.get("confidence")))
    refs = _normalise_refs(payload.get("evidence_refs") or payload.get("evidence") or [], require=True)
    return {
        "display_name": display_name, "confidence_score": score, "confidence_band": confidence_band(score),
        "rationale": _text(payload.get("rationale"), "AI rationale", required=True, limit=2000),
        "evidence_refs": refs,
        "provider": {
            "provider": _text((payload.get("provider") or {}).get("provider") if isinstance(payload.get("provider"), dict) else payload.get("provider_name"), "AI provider", required=True),
            "model": _text((payload.get("provider") or {}).get("model") if isinstance(payload.get("provider"), dict) else payload.get("model"), "AI model", required=True),
            "prompt_policy_fingerprint": _text((payload.get("provider") or {}).get("prompt_policy_fingerprint") if isinstance(payload.get("provider"), dict) else payload.get("prompt_policy_fingerprint"), "Prompt-policy fingerprint", required=True),
        },
    }


def apply_ai_updates(artifact, updates, *, source_artifact_fingerprints_value, actor="ai"):
    if artifact.get("source_artifact_fingerprints") != source_artifact_fingerprints_value:
        raise ValueError("Component interpretations are stale; rebuild them from the current evidence before applying AI updates.")
    if not isinstance(updates, list) or not updates:
        raise ValueError("At least one AI interpretation update is required.")
    rows = {row.get("component_id"): row for row in artifact.get("interpretations", [])}
    changed_ids, conflict_ids = [], []
    for update in updates:
        component_id = str((update or {}).get("component_id", ""))
        row = rows.get(component_id)
        if not row:
            raise ValueError(f"Unknown component interpretation: {component_id or 'missing component_id'}.")
        proposal = _proposal(update)
        known_pages = {ref.get("page") for ref in row.get("evidence_refs", []) if ref.get("page")}
        proposal_pages = {ref.get("page") for ref in proposal["evidence_refs"] if ref.get("page")}
        if known_pages and proposal_pages and not proposal_pages.issubset(known_pages):
            raise ValueError(f"AI evidence references do not belong to component {component_id}.")
        row["latest_ai_proposal"] = deepcopy(proposal)
        locked = row.get("reviewer_locks") or {}
        changed_fields, conflicts = [], []
        if locked.get("display_name"):
            conflicts.append("display_name")
        elif row.get("display_name") != proposal["display_name"]:
            row["display_name"] = proposal["display_name"]
            row["display_name_origin"] = "ai"
            changed_fields.append("display_name")
        if locked.get("confidence"):
            conflicts.append("confidence")
        elif row.get("confidence_score") != proposal["confidence_score"]:
            row["confidence_score"] = proposal["confidence_score"]
            row["confidence_band"] = proposal["confidence_band"]
            row["confidence_origin"] = "ai"
            changed_fields.append("confidence")
        row["rationale"] = proposal["rationale"]
        row["provider"] = proposal["provider"]
        row["revision_history"].append(_history("ai_update_conflict" if conflicts else "ai_update", actor=actor, fields=changed_fields or conflicts, proposal=proposal))
        if conflicts:
            row.setdefault("competing_updates", []).append({"fields": conflicts, "proposal": deepcopy(proposal), "at": now()})
            conflict_ids.append(component_id)
        if changed_fields or conflicts:
            changed_ids.append(component_id)
    artifact["fingerprint"] = fingerprint({key: value for key, value in artifact.items() if key != "fingerprint"})
    return artifact, sorted(set(changed_ids)), sorted(set(conflict_ids))


def save_reviewer_change(artifact, payload, *, actor="reviewer"):
    component_id = str((payload or {}).get("component_id", ""))
    row = next((item for item in artifact.get("interpretations", []) if item.get("component_id") == component_id), None)
    if not row:
        raise ValueError("A valid component_id is required.")
    fields = []
    if "display_name" in payload:
        row["display_name"] = _text(payload["display_name"], "Display name", required=True)
        row["display_name_origin"] = "reviewer"
        fields.append("display_name")
    if "confidence_score" in payload or "confidence" in payload:
        score = validate_confidence(payload.get("confidence_score", payload.get("confidence")))
        row["confidence_score"], row["confidence_band"], row["confidence_origin"] = score, confidence_band(score), "reviewer"
        fields.append("confidence")
    locks = row.setdefault("reviewer_locks", {"display_name": None, "confidence": None})
    for field, key in (("display_name", "lock_display_name"), ("confidence", "lock_confidence")):
        if key in payload:
            if not isinstance(payload[key], bool):
                raise ValueError(f"{key} must be true or false.")
            locks[field] = {"actor": _text(actor, "Reviewer", required=True), "at": now()} if payload[key] else None
            fields.append(field + "_lock")
    if not fields:
        raise ValueError("Supply a name, confidence, or lock change.")
    row["revision_history"].append(_history("reviewer_update", actor=actor, fields=fields, note=payload.get("note", "")))
    artifact["fingerprint"] = fingerprint({key: value for key, value in artifact.items() if key != "fingerprint"})
    return artifact, component_id


def unlock_and_restore_ai(artifact, component_id, field, *, actor="reviewer", note=""):
    if field not in {"display_name", "confidence"}:
        raise ValueError("Only display_name or confidence can be unlocked.")
    row = next((item for item in artifact.get("interpretations", []) if item.get("component_id") == component_id), None)
    if not row:
        raise ValueError("A valid component_id is required.")
    row.setdefault("reviewer_locks", {"display_name": None, "confidence": None})[field] = None
    proposal = row.get("latest_ai_proposal")
    if proposal:
        if field == "display_name":
            row["display_name"], row["display_name_origin"] = proposal["display_name"], "ai"
        else:
            row["confidence_score"], row["confidence_band"], row["confidence_origin"] = proposal["confidence_score"], proposal["confidence_band"], "ai"
    row["revision_history"].append(_history("reviewer_unlock", actor=actor, fields=[field], proposal=proposal, note=note))
    artifact["fingerprint"] = fingerprint({key: value for key, value in artifact.items() if key != "fingerprint"})
    return artifact, component_id
