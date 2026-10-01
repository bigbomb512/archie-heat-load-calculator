"""Draft-only, source-linked ceiling height and room-volume resolution."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import re


SCHEMA_VERSION = 1
STATUSES = {"resolved", "provisional", "needs_review", "excluded", "conflict", "stale"}
ORIGINS = {
    "contractor_override", "project_evidence", "scoped_project_evidence",
    "ai_estimated", "preliminary_fallback", "unresolved",
}
SCOPE_TYPES = {"room", "zone", "level"}


def now():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value):
    def normalise(item):
        if isinstance(item, dict):
            return {key: normalise(child) for key, child in sorted(item.items())
                    if key not in {"updated_at", "created_at"}}
        if isinstance(item, list):
            return [normalise(child) for child in item]
        return item
    return hashlib.sha256(json.dumps(normalise(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def _text(value):
    return str(value or "").strip()


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def _confidence(value, fallback=0.4):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0.0, min(1.0, float(value)))
    return {"high": 0.85, "medium": 0.65, "low": 0.4}.get(_text(value).casefold(), fallback)


def confidence_band(value):
    return "high" if value >= 0.8 else "medium" if value >= 0.5 else "low"


def _slug(value):
    return re.sub(r"[^a-z0-9]+", "-", _text(value).casefold()).strip("-") or "unassigned"


def room_identity(label, level):
    return f"room:{_slug(level)}:{_slug(label)}"


def _evidence(row):
    values = row.get("ceiling_evidence", row.get("evidence", [])) if isinstance(row, dict) else []
    result = [deepcopy(value) for value in values if isinstance(value, dict) and value.get("page")]
    if not result and isinstance(row, dict) and row.get("page"):
        result.append({"page": row.get("page"), "drawing_number": _text(row.get("drawing_number")),
                       "excerpt": _text(row.get("ceiling_excerpt", row.get("excerpt", row.get("label", ""))))})
    return result


def empty_ceiling_volume_resolution():
    result = {
        "schema_version": SCHEMA_VERSION,
        "records": [],
        "source_fingerprints": {},
        "pack_fingerprint": "",
        "status": "needs_review",
        "updated_at": "",
    }
    result["fingerprint"] = fingerprint(result)
    return result


def _area_by_room(building, proposal, geometry_resolution):
    values = {}
    for room in (building or {}).get("spaces", []) if isinstance(building, dict) else []:
        if isinstance(room, dict) and _text(room.get("name")):
            value = _number(room.get("area_m2", room.get("area")))
            if value:
                values[room_identity(room.get("name"), room.get("level_name", "Unassigned level"))] = {
                    "area_m2": value, "geometry_proof_id": "", "origin": "project_evidence"}
    for room in (proposal or {}).get("rooms", []) if isinstance(proposal, dict) else []:
        if isinstance(room, dict) and _text(room.get("label")):
            value = _number(room.get("area_m2"))
            if value:
                values.setdefault(room_identity(room.get("label"), room.get("level_name", "Unassigned level")), {
                    "area_m2": value, "geometry_proof_id": "", "origin": "ai_geometry"})
    for entity in (geometry_resolution or {}).get("entities", []):
        if not isinstance(entity, dict) or entity.get("kind") != "area" or entity.get("geometry_status") not in {"ai_estimated", "geometry_confirmed"}:
            continue
        detail = entity.get("value") if isinstance(entity.get("value"), dict) else {}
        value = _number(detail.get("area_m2"))
        label, level = _text(entity.get("label")), _text(entity.get("level_candidate"))
        if value and label and level:
            values[room_identity(label, level)] = {
                "area_m2": value,
                "geometry_proof_id": _text(detail.get("geometry_proof_id", entity.get("entity_id"))),
                "origin": "ai_geometry" if entity.get("geometry_status") == "ai_estimated" else "geometry_proof",
            }
    return values


def _rooms(building, vision, proposal):
    grouped = {}
    all_ai = []
    if isinstance(vision, dict):
        all_ai.extend((((vision.get("result") or {}).get("auto_extraction") or {}).get("entities") or []))
    if isinstance(proposal, dict):
        all_ai.extend(proposal.get("rooms", []))
    for item in (building or {}).get("spaces", []) if isinstance(building, dict) else []:
        if not isinstance(item, dict) or not _text(item.get("name")):
            continue
        label, level = _text(item.get("name")), _text(item.get("level_name")) or "Unassigned level"
        grouped.setdefault(room_identity(label, level), {"label": label, "level": level, "building": item, "ai": []})
    for item in all_ai:
        if not isinstance(item, dict) or item.get("kind") != "room" or not _text(item.get("label")):
            continue
        label, level = _text(item.get("label")), _text(item.get("level_name")) or "Unassigned level"
        grouped.setdefault(room_identity(label, level), {"label": label, "level": level, "building": {}, "ai": []})["ai"].append(item)
    return [grouped[key] for key in sorted(grouped)]


def _candidate(height, origin, evidence, *, confidence=0.4, rationale="", scope_type="room", applies_to=None,
               source_page=None, datum_operands=None, conflicts=None):
    value = _number(height)
    if value is None:
        return None
    scope_type = _text(scope_type).casefold() or "room"
    if scope_type not in SCOPE_TYPES:
        return None
    evidence = [item for item in evidence if isinstance(item, dict) and item.get("page")]
    if origin != "preliminary_fallback" and not evidence:
        return None
    return {
        "height_mm": value, "origin": origin, "evidence": evidence, "confidence": _confidence(confidence),
        "rationale": _text(rationale), "scope_type": scope_type,
        "applies_to": sorted(set(applies_to or [])), "source_page": source_page,
        "datum_operands": deepcopy(datum_operands or {}), "conflicts": list(conflicts or []),
    }


def _proposal_candidates(room, all_rooms):
    output = []
    for item in all_rooms:
        if not isinstance(item, dict) or item.get("kind") != "room":
            continue
        scope = _text(item.get("ceiling_scope", "room")).casefold() or "room"
        applies = item.get("ceiling_applies_to", [])
        if scope == "room":
            applies = [room_identity(item.get("label"), item.get("level_name", "Unassigned level"))]
        elif not isinstance(applies, list) or not applies:
            continue
        normalized = []
        for value in applies:
            if isinstance(value, dict):
                normalized.append(room_identity(value.get("label", value.get("name", "")), value.get("level_name", value.get("level", "Unassigned level"))))
            elif isinstance(value, str) and value.startswith("room:"):
                normalized.append(value)
        if room_identity(room["label"], room["level"]) not in normalized:
            continue
        candidate = _candidate(
            item.get("ceiling_height_mm"), "ai_estimated", _evidence(item), confidence=item.get("confidence"),
            rationale=item.get("ceiling_rationale", item.get("rationale", "AI-linked ceiling-height evidence.")),
            scope_type=scope, applies_to=normalized, source_page=item.get("page"),
            datum_operands=item.get("ceiling_datum_operands", {}), conflicts=item.get("ceiling_conflicts", []),
        )
        if candidate:
            output.append(candidate)
    return output


def _select(candidates):
    ranked = {
        "contractor_override": 0, "project_evidence": 1, "scoped_project_evidence": 2,
        "ai_estimated": 3, "preliminary_fallback": 4,
    }
    candidates = [candidate for candidate in candidates if candidate]
    candidates.sort(key=lambda item: (ranked.get(item["origin"], 99), -item["confidence"], item["height_mm"]))
    if not candidates:
        return None, []
    best = candidates[0]
    same_rank = [item for item in candidates if ranked.get(item["origin"], 99) == ranked.get(best["origin"], 99)]
    if len({round(item["height_mm"], 6) for item in same_rank}) > 1:
        return None, ["Competing equally ranked ceiling-height candidates."]
    return best, sorted({conflict for item in candidates for conflict in item.get("conflicts", [])})


def _record(room, all_ai, area, fallback_height, existing):
    room_id = room_identity(room["label"], room["level"])
    candidates = []
    prior = existing if isinstance(existing, dict) else {}
    override = prior.get("override") if isinstance(prior.get("override"), dict) else None
    if override:
        candidates.append(_candidate(override.get("height_mm"), "contractor_override", [{"page": 1, "excerpt": "Contractor override"}],
                                     confidence=1.0, rationale=override.get("note", "Contractor ceiling-height override.")))
    direct = room["building"]
    if _number(direct.get("ceiling_height_mm")):
        direct_scope = _text(direct.get("ceiling_scope", "room")).casefold() or "room"
        direct_applies = direct.get("ceiling_applies_to", [])
        if direct_scope == "room":
            direct_applies = [room_id]
        elif isinstance(direct_applies, list):
            direct_applies = [room_identity(item.get("label", item.get("name", "")), item.get("level_name", item.get("level", "Unassigned level")))
                              for item in direct_applies if isinstance(item, dict)]
        if room_id in direct_applies:
            candidates.append(_candidate(direct.get("ceiling_height_mm"), "project_evidence" if direct_scope == "room" else "scoped_project_evidence",
                                         _evidence(direct), confidence=0.9,
                                         rationale="Cited room-specific ceiling height from project evidence." if direct_scope == "room" else "Cited shared ceiling height with explicit project scope.",
                                         scope_type=direct_scope, applies_to=direct_applies))
    candidates.extend(_proposal_candidates(room, all_ai))
    candidates.append(_candidate(fallback_height, "preliminary_fallback", [], confidence=0.35,
                                 rationale="Controlled Australia-first preliminary ceiling-height assumption."))
    selected, conflicts = _select(candidates)
    if selected is None:
        status, height, origin = "conflict", None, "unresolved"
        rationale = "Competing equally ranked ceiling-height evidence requires review."
        evidence, confidence = [], 0.0
    else:
        height, origin, evidence, confidence = selected["height_mm"], selected["origin"], selected["evidence"], selected["confidence"]
        status = "resolved" if origin in {"contractor_override", "project_evidence", "scoped_project_evidence"} else "provisional"
        rationale = selected["rationale"]
        if selected["scope_type"] != "room" and status == "resolved":
            status = "provisional"
    volume = None
    derivation = {}
    if height is not None and area.get("area_m2") is not None:
        volume = area["area_m2"] * height / 1000
        derivation = {"formula": "area_m2 × ceiling_height_mm / 1000", "operands": {"area_m2": area["area_m2"], "ceiling_height_mm": height}}
    result = {
        "room_id": room_id, "component_id": room_id, "original_label": room["label"], "level_name": room["level"],
        "geometry_proof_id": area.get("geometry_proof_id", ""), "area_m2": area.get("area_m2"),
        "ceiling_height_mm": height, "volume_m3": volume, "derivation": derivation,
        "origin": origin, "confidence_score": round(confidence, 4), "confidence_band": confidence_band(confidence),
        "rationale": rationale, "evidence": evidence,
        "scope_type": selected.get("scope_type", "room") if selected else "room",
        "applicability_scope": selected.get("applies_to", [room_id]) if selected else [room_id],
        "datum_operands": selected.get("datum_operands", {}) if selected else {},
        "conflicts": conflicts, "override": deepcopy(override), "status": status,
        "remediation": (
            "Confirm the controlled preliminary height or provide a cited RCP, section, elevation, or ceiling note." if origin == "preliminary_fallback" else
            "Resolve the competing ceiling dimensions or state which value applies to this room." if status == "conflict" else
            "Provide a current room area before room volume can be calculated." if volume is None else ""
        ),
    }
    result["record_fingerprint"] = fingerprint(result)
    return result


def resolve(building, vision=None, proposal=None, geometry_resolution=None, assumption_pack=None, source_fingerprints=None, existing=None):
    fallback = _number(((assumption_pack or {}).get("preliminary_defaults") or {}).get("ceiling_height_mm"))
    if fallback is None:
        raise ValueError("The preliminary assumption pack needs a positive named ceiling-height fallback.")
    all_ai = []
    if isinstance(vision, dict):
        all_ai.extend((((vision.get("result") or {}).get("auto_extraction") or {}).get("entities") or []))
    if isinstance(proposal, dict):
        all_ai.extend(proposal.get("rooms", []))
    areas = _area_by_room(building, proposal or {}, geometry_resolution or {})
    previous = {item.get("room_id"): item for item in (existing or {}).get("records", []) if isinstance(item, dict)}
    records = [_record(room, all_ai, areas.get(room_identity(room["label"], room["level"]), {}), fallback,
                       previous.get(room_identity(room["label"], room["level"])))
               for room in _rooms(building, vision or {}, proposal or {})]
    present = {row["room_id"] for row in records}
    for room_id, prior in previous.items():
        override = prior.get("override")
        if room_id in present or not isinstance(override, dict) or not override:
            continue
        retained = deepcopy(prior)
        retained["status"] = "stale"
        retained["rationale"] = "This room is absent from the current proposal; its ceiling override was retained for review."
        retained["remediation"] = "Confirm whether the room was intentionally removed before clearing its ceiling override."
        retained["conflicts"] = sorted(set(retained.get("conflicts", [])) | {"Room no longer appears in the current proposal."})
        retained["record_fingerprint"] = fingerprint(retained)
        records.append(retained)
    result = {
        "schema_version": SCHEMA_VERSION, "records": records,
        "source_fingerprints": deepcopy(source_fingerprints or {}), "pack_fingerprint": fingerprint(assumption_pack or {}),
        "status": "needs_review" if any(row["status"] in {"conflict", "needs_review", "stale"} for row in records) else "draft_ready" if records else "needs_review",
        "updated_at": now(),
    }
    retained_overrides = [row for row in records if row.get("status") == "stale"]
    if retained_overrides:
        result["stale_reasons"] = [
            f"{len(retained_overrides)} room(s) no longer appear in the current proposal; saved ceiling overrides were retained for review."
        ]
    result["fingerprint"] = fingerprint(result)
    return result


def validate(raw):
    raw = raw or empty_ceiling_volume_resolution()
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION or not isinstance(raw.get("records"), list):
        raise ValueError("Unsupported ceiling-volume resolution artifact.")
    ids = []
    for row in raw["records"]:
        if not isinstance(row, dict) or not _text(row.get("room_id")) or row.get("origin") not in ORIGINS or row.get("status") not in STATUSES:
            raise ValueError("Ceiling-volume resolution record is invalid.")
        if _number(row.get("ceiling_height_mm")) is None:
            raise ValueError("Ceiling-volume resolution record needs a positive ceiling height.")
        ids.append(row["room_id"])
    if len(ids) != len(set(ids)):
        raise ValueError("Ceiling-volume resolution room IDs must be unique.")
    result = deepcopy(raw)
    result["fingerprint"] = fingerprint({key: value for key, value in result.items() if key != "fingerprint"})
    return result


def is_current(artifact, source_fingerprints, assumption_pack):
    return bool(artifact) and artifact.get("source_fingerprints") == (source_fingerprints or {}) and artifact.get("pack_fingerprint") == fingerprint(assumption_pack or {})


def values_by_room(artifact):
    return {row["room_id"]: {"ceiling_height_mm": row.get("ceiling_height_mm"), "volume_m3": row.get("volume_m3"),
                              "origin": row.get("origin"), "confidence_score": row.get("confidence_score"),
                              "record_id": row.get("record_fingerprint", "")}
            for row in (artifact or {}).get("records", []) if isinstance(row, dict)
            and row.get("status") != "stale" and row.get("ceiling_height_mm") is not None}
