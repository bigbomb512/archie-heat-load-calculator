"""Draft-only, evidence-preserving room-use classification for AI preliminary models."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
PACK_PATH = ROOT / "config" / "au_room_use_taxonomy_v1.json"
PACK_ID = "au-room-use-v1"
STATUSES = {"resolved", "needs_review", "excluded", "stale"}
SCOPES = {"comfort_hvac", "comfort_hvac_with_process_exception", "refrigeration_process", "unresolved_scope", "not_a_room"}
# Scopes that never enter comfort-HVAC totals. "not_a_room" is a reviewer
# decision that a detected label is not a real space at all.
EXCLUDED_SCOPES = {"refrigeration_process", "unresolved_scope", "not_a_room"}
PROFILES = {"retail", "office", "hospitality", "storage", "residential", "generic_conditioned_room"}


def now():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value):
    def normalise(item):
        if isinstance(item, dict):
            return {key: normalise(value) for key, value in sorted(item.items()) if key not in {"updated_at", "created_at"}}
        if isinstance(item, list):
            return [normalise(value) for value in item]
        return item
    return hashlib.sha256(json.dumps(normalise(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def _text(value):
    return str(value or "").strip()


def _slug(value):
    return re.sub(r"[^a-z0-9]+", "-", _text(value).casefold()).strip("-") or "unassigned"


def room_identity(label, level):
    return f"room-use:{_slug(level)}:{_slug(label)}"


def _confidence(value, fallback=0.4):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0.0, min(1.0, float(value)))
    return {"high": 0.85, "medium": 0.65, "low": 0.4}.get(_text(value).casefold(), fallback)


def confidence_band(value):
    return "high" if value >= 0.8 else "medium" if value >= 0.5 else "low"


def load_taxonomy():
    try:
        raw = json.loads(PACK_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("The Australia-first room-use taxonomy pack is unavailable.") from error
    if raw.get("schema_version") != 1 or raw.get("pack_id") != PACK_ID or raw.get("country") != "AU" or raw.get("status") != "released":
        raise ValueError("The Australia-first room-use taxonomy pack is invalid.")
    categories = raw.get("categories")
    if not isinstance(categories, dict) or "generic_conditioned" not in categories:
        raise ValueError("The Australia-first room-use taxonomy pack is incomplete.")
    for category_id, row in categories.items():
        if not isinstance(row, dict) or not _text(row.get("label")) or not isinstance(row.get("aliases"), list):
            raise ValueError(f"Room-use taxonomy category '{category_id}' is invalid.")
        if row.get("space_scope") not in SCOPES or row.get("profile_id", "") not in PROFILES | {""}:
            raise ValueError(f"Room-use taxonomy category '{category_id}' has an invalid profile or scope.")
    return raw


def empty_room_use_resolution():
    result = {"schema_version": 1, "taxonomy_pack_id": PACK_ID, "taxonomy_fingerprint": "", "records": [],
              "source_fingerprints": {}, "status": "needs_review", "updated_at": ""}
    result["fingerprint"] = fingerprint(result)
    return result


def _evidence(row):
    evidence = row.get("evidence", []) if isinstance(row, dict) else []
    result = [deepcopy(item) for item in evidence if isinstance(item, dict) and item.get("page")]
    if not result and isinstance(row, dict) and row.get("page"):
        result.append({"page": row.get("page"), "drawing_number": _text(row.get("drawing_number")), "excerpt": _text(row.get("excerpt", row.get("label")))})
    return result


def _room_rows(building, vision, proposal):
    vision_entities = (((vision or {}).get("result", {}) or {}).get("auto_extraction", {}) or {}).get("entities", []) if isinstance(vision, dict) else []
    ai_rooms = [row for row in list(vision_entities) + list((proposal or {}).get("rooms", []))
                if isinstance(row, dict) and row.get("kind") == "room"]
    grouped = {}
    for row in (building or {}).get("spaces", []) if isinstance(building, dict) else []:
        if not isinstance(row, dict) or not _text(row.get("name")):
            continue
        label, level = _text(row.get("name")), _text(row.get("level_name")) or "Unassigned level"
        grouped.setdefault(room_identity(label, level), {"label": label, "level_name": level, "building": row, "ai": []})["ai"].extend(
            item for item in ai_rooms if _text(item.get("label")).casefold() == label.casefold() and
            (not _text(item.get("level_name")) or _text(item.get("level_name")).casefold() == level.casefold())
        )
    for row in ai_rooms:
        label, level = _text(row.get("label")), _text(row.get("level_name")) or "Unassigned level"
        if label:
            grouped.setdefault(room_identity(label, level), {"label": label, "level_name": level, "building": {}, "ai": []})["ai"].append(row)
    return [grouped[key] for key in sorted(grouped)]


def _label_matches(label, taxonomy):
    text = _text(label).casefold()
    matches = []
    for category_id, category in taxonomy["categories"].items():
        # Use word boundaries so a short alias such as "bar" does not classify
        # unrelated labels such as "barrier".  The original PDF label remains
        # the evidence; this is only controlled-taxonomy matching.
        if any(re.search(r"(?<![a-z0-9])" + re.escape(alias.casefold()) + r"(?![a-z0-9])", text)
               for alias in category.get("aliases", []) if alias):
            matches.append(category_id)
    # Specific retail/front-of-house phrases take precedence over the broad
    # "service" alias used for unresolved plant/service rooms.
    if re.search(r"(?<![a-z0-9])(?:service\s+counter|counter|kiosk)(?![a-z0-9])", text):
        if "kitchen" in matches:
            return ["kitchen"]
        return ["retail"] if "retail" in taxonomy["categories"] else sorted(set(matches))
    return sorted(set(matches))


def _ai_candidate(rows, taxonomy):
    candidates = []
    reverse_profiles = {}
    for category_id, category in taxonomy["categories"].items():
        if category.get("profile_id"):
            reverse_profiles.setdefault(category["profile_id"], []).append(category_id)
    for row in rows:
        category_id = _text(row.get("room_use_category"))
        if not category_id:
            profile = _text(row.get("preliminary_profile_id"))
            mapped = reverse_profiles.get(profile, [])
            category_id = mapped[0] if len(mapped) == 1 else ""
        if category_id in taxonomy["categories"]:
            candidates.append({"category_id": category_id, "confidence": _confidence(row.get("confidence")), "row": row,
                               "rationale": _text(row.get("room_use_rationale", row.get("rationale"))) or "AI-selected controlled room-use category.",
                               "alternatives": [value for value in row.get("room_use_alternatives", []) if value in taxonomy["categories"]]})
    candidates.sort(key=lambda item: (-item["confidence"], item["category_id"]))
    return candidates[0] if candidates else None, candidates


def _record(room, taxonomy, source_fingerprints, existing=None):
    label, level = room["label"], room["level_name"]
    room_id = room_identity(label, level)
    skill_context = next((row for row in reversed(room.get("ai", [])) if isinstance(row, dict) and (
        "direct_label_verified" in row or "calculation_scope_override" in row or "non_counting_functional_subarea" in row
    )), {})
    # Runtime room-identity skills may establish that a supplied legacy label
    # is not actually printed in the drawing (for example, a source-system
    # placeholder). In that case it is context, not direct label evidence, and
    # must not outrank the cited visual interpretation.
    direct = [] if skill_context.get("direct_label_verified") is False else _label_matches(label, taxonomy)
    ai, ai_candidates = _ai_candidate(room["ai"], taxonomy)
    override = deepcopy((existing or {}).get("override")) if isinstance(existing, dict) else None
    alternatives, conflicts = [], []
    if override and override.get("taxonomy_id") not in taxonomy["categories"]:
        override = None
    if override:
        selected, origin, score, rationale = override["taxonomy_id"], "contractor_override", 1.0, _text(override.get("note")) or "Contractor room-use override."
    elif len(direct) == 1:
        selected, origin, score, rationale = direct[0], "direct_project_evidence", 0.9, "Explicit room label matches the controlled taxonomy."
    elif len(direct) > 1:
        selected, origin, score, rationale = "generic_conditioned", "conflict", 0.35, "The room label matches competing controlled room-use categories."
        alternatives = direct
        conflicts.append("Competing explicit room-label classifications.")
    elif ai:
        selected, origin, score, rationale = ai["category_id"], "ai_interpretation", ai["confidence"], ai["rationale"]
        alternatives = ai["alternatives"]
    else:
        selected, origin, score, rationale = "generic_conditioned", "generic_fallback", 0.3, "No specific room-use evidence was available; a controlled generic conditioned-room profile is used for draft coverage."
    category = taxonomy["categories"][selected]
    scope, profile = category["space_scope"], category.get("profile_id", "")
    scope_override = skill_context.get("calculation_scope_override")
    if scope_override in SCOPES:
        scope = scope_override
        category = deepcopy(category)
        category["space_scope"] = scope
    special = scope in EXCLUDED_SCOPES
    status = "excluded" if special else "needs_review" if origin in {"generic_fallback", "conflict"} or score < 0.5 else "resolved"
    evidence = []
    for item in [room["building"], *(room["ai"] or [])]:
        for reference in _evidence(item):
            if reference not in evidence:
                evidence.append(reference)
    geometry_reference = {"room_identity": room_id, "source_pages": sorted({item.get("page") for item in evidence if item.get("page")})}
    result = {
        "room_id": room_id, "component_id": room_id, "original_label": label, "level_name": level,
        "geometry_reference": geometry_reference, "taxonomy_id": selected, "taxonomy_label": category["label"],
        "preliminary_profile_id": profile, "space_scope": scope, "confidence_score": round(score, 4),
        "confidence_band": confidence_band(score), "classification_origin": origin, "rationale": rationale,
        "evidence": evidence, "direct_label_evidence": direct, "alternatives": sorted(set(alternatives)),
        "conflicts": conflicts, "ai_candidates": [{"taxonomy_id": item["category_id"], "confidence_score": item["confidence"],
                                                        "rationale": item["rationale"]} for item in ai_candidates],
        "override": override, "status": status, "remediation": (
            "Confirm the room use before relying on this generic preliminary profile." if status == "needs_review" else
            "Provide a refrigeration/process load method; this space is excluded from comfort-HVAC totals." if scope == "refrigeration_process" else
            "Confirm whether this plant, service, or unconditioned space belongs in comfort-HVAC scope." if scope == "unresolved_scope" else
            "A reviewer marked this detection as not a room; it is excluded from tracing, the calculator draft and preliminary totals." if scope == "not_a_room" else ""
        ),
        "source_fingerprints": deepcopy(source_fingerprints or {}),
    }
    if skill_context.get("non_counting_functional_subarea"):
        result["non_counting_functional_subarea"] = True
    result["record_fingerprint"] = fingerprint(result)
    return result


def resolve(building, vision=None, proposal=None, source_fingerprints=None, existing=None):
    taxonomy = load_taxonomy()
    previous = {row.get("room_id"): row for row in (existing or {}).get("records", []) if isinstance(row, dict)}
    records = [_record(room, taxonomy, source_fingerprints or {}, previous.get(room_identity(room["label"], room["level_name"])))
               for room in _room_rows(building, vision or {}, proposal or {})]
    result = {"schema_version": 1, "taxonomy_pack_id": PACK_ID, "taxonomy_fingerprint": fingerprint(taxonomy),
              "records": records, "source_fingerprints": deepcopy(source_fingerprints or {}),
              "status": "needs_review" if any(row["status"] == "needs_review" for row in records) else "draft_ready" if records else "needs_review",
              "updated_at": now()}
    result["fingerprint"] = fingerprint(result)
    return result


def validate(raw):
    raw = raw or empty_room_use_resolution()
    if not isinstance(raw, dict) or raw.get("schema_version") != 1 or raw.get("taxonomy_pack_id") != PACK_ID:
        raise ValueError("Unsupported room-use resolution artifact.")
    taxonomy = load_taxonomy()
    records = raw.get("records", [])
    if not isinstance(records, list):
        raise ValueError("Room-use resolution records must be a list.")
    ids = []
    for row in records:
        if not isinstance(row, dict) or not _text(row.get("room_id")) or row.get("taxonomy_id") not in taxonomy["categories"]:
            raise ValueError("Room-use resolution record is invalid.")
        if row.get("space_scope") not in SCOPES or row.get("preliminary_profile_id", "") not in PROFILES | {""} or row.get("status") not in STATUSES - {"stale"}:
            raise ValueError("Room-use resolution record has an invalid profile, scope, or status.")
        ids.append(row["room_id"])
    if len(ids) != len(set(ids)):
        raise ValueError("Room-use resolution room IDs must be unique.")
    result = deepcopy(raw)
    result["taxonomy_fingerprint"] = fingerprint(taxonomy)
    result["fingerprint"] = fingerprint({key: value for key, value in result.items() if key != "fingerprint"})
    return result


def apply_override(artifact, room_id, taxonomy_id, reviewer, note):
    taxonomy = load_taxonomy()
    if taxonomy_id not in taxonomy["categories"] or not _text(reviewer):
        raise ValueError("Room-use override needs a valid taxonomy category and reviewer.")
    artifact = deepcopy(validate(artifact))
    for row in artifact["records"]:
        if row["room_id"] == room_id:
            row["override"] = {"taxonomy_id": taxonomy_id, "reviewer": _text(reviewer), "note": _text(note), "updated_at": now()}
            return resolve_from_existing(artifact)
    raise ValueError("Room-use override references an unknown room.")


def clear_override(artifact, room_id):
    artifact = deepcopy(validate(artifact))
    for row in artifact["records"]:
        if row["room_id"] == room_id:
            row["override"] = None
            return resolve_from_existing(artifact)
    raise ValueError("Room-use override references an unknown room.")


def resolve_from_existing(artifact):
    """Reapply an override without needing to re-run PDF/AI interpretation."""
    taxonomy = load_taxonomy()
    rows = []
    for prior in artifact["records"]:
        category_id = (prior.get("override") or {}).get("taxonomy_id", prior["taxonomy_id"])
        category = taxonomy["categories"][category_id]
        row = deepcopy(prior)
        if prior.get("override"):
            row.update({"taxonomy_id": category_id, "taxonomy_label": category["label"], "preliminary_profile_id": category.get("profile_id", ""),
                        "space_scope": category["space_scope"], "classification_origin": "contractor_override", "confidence_score": 1.0,
                        "confidence_band": "high", "rationale": _text(prior["override"].get("note")) or "Contractor room-use override."})
        row["status"] = "excluded" if row["space_scope"] in EXCLUDED_SCOPES else "resolved"
        row["record_fingerprint"] = fingerprint({key: value for key, value in row.items() if key != "record_fingerprint"})
        rows.append(row)
    result = {**artifact, "records": rows, "status": "needs_review" if any(row["status"] == "needs_review" for row in rows) else "draft_ready", "updated_at": now()}
    result["fingerprint"] = fingerprint({key: value for key, value in result.items() if key != "fingerprint"})
    return result


def is_current(artifact, source_fingerprints):
    return bool(artifact) and artifact.get("source_fingerprints") == (source_fingerprints or {}) and artifact.get("taxonomy_fingerprint") == fingerprint(load_taxonomy())


def effective_proposal(proposal, artifact):
    """Overlay controlled selections onto draft proposal rooms, never reviewed data."""
    output = deepcopy(proposal or {})
    output.setdefault("rooms", [])
    index = {row["room_id"]: row for row in artifact.get("records", []) if isinstance(row, dict)}
    for room in output["rooms"]:
        if not isinstance(room, dict):
            continue
        record = index.get(room_identity(room.get("label"), room.get("level_name", "Unassigned level")))
        if record:
            room["preliminary_profile_id"] = record.get("preliminary_profile_id") or "generic_conditioned_room"
            room["space_scope"] = record.get("space_scope", "unresolved_scope")
            room["room_use_category"] = record.get("taxonomy_id", "")
            room["room_use_rationale"] = record.get("rationale", "")
    return output
