"""Isolated, controlled-assumption cooling inputs for AI preliminary reports.

This module deliberately does not write reviewed calculator artifacts.  It
turns source-linked AI/PDF topology into a *draft-only* hourly-engine payload,
using values selected from a versioned product pack rather than arbitrary AI
numbers.
"""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re

from ai.design_requirements import validate_design_requirements
from ai.hourly_loads import build_hourly_load_model, calculate_hourly_load_report
from ai import design_weather
from ai import value_resolution as value_resolver
from ai import model_input_resolution as shared_resolution
from ai import room_use_resolution as room_use_resolver
from ai import ceiling_volume_resolution as ceiling_volume_resolver
from ai import thermal_surface_resolution as thermal_surface_resolver


ROOT = Path(__file__).resolve().parents[1]
PACK_PATH = ROOT / "config" / "ai_preliminary_assumption_pack.json"
PACK_VERSION = "au-preliminary-v3"
LEGACY_PACK_VERSIONS = {"au-preliminary-v2"}
PROFILE_IDS = {"retail", "office", "hospitality", "storage", "residential", "generic_conditioned_room"}
SPACE_SCOPES = {"comfort_hvac", "comfort_hvac_with_process_exception", "refrigeration_process", "unresolved_scope", "not_a_room"}
# The proposal/ledger contract retains the complete opaque inventory.  The
# preliminary hourly adapter currently materializes only externally exposed
# wall/roof/ceiling surfaces; floors, partitions, and other boundaries remain
# visible ledger records until their approved boundary method is available.
OPAQUE_TYPES = {"wall", "roof", "floor", "ceiling", "partition"}
CARDINALS = {"N", "E", "S", "W"}
SHADING_CATEGORIES = {"unshaded", "partial", "deep"}
CODEX_HANDOFF_SCHEMA_VERSION = 1


def now():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value):
    def normalise(item):
        if isinstance(item, dict):
            return {key: normalise(value) for key, value in sorted(item.items())}
        if isinstance(item, list):
            values = [normalise(value) for value in item]
            return sorted(values, key=lambda value: json.dumps(value, sort_keys=True, separators=(",", ":")))
        return item
    return hashlib.sha256(json.dumps(normalise(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def content_fingerprint(value):
    """Fingerprint authored inputs without volatile artifact timestamps."""
    def strip(item):
        if isinstance(item, dict):
            return {key: strip(value) for key, value in item.items()
                    if key not in {"updated_at", "created_at", "finished_at", "source_requirements_updated_at"}}
        if isinstance(item, list):
            return [strip(value) for value in item]
        return item
    return fingerprint(strip(value))


def load_pack():
    pack = json.loads(PACK_PATH.read_text(encoding="utf-8"))
    if pack.get("version") != PACK_VERSION or not PROFILE_IDS <= set(pack.get("profiles", {})):
        raise ValueError("The AI preliminary assumption pack is incomplete or has an unsupported version.")
    if len(pack.get("scenario", {}).get("hours", [])) != 24:
        raise ValueError("The AI preliminary assumption pack needs a 24-hour cooling scenario.")
    envelope = pack.get("preliminary_envelope", {})
    profiles = envelope.get("cardinal_solar_profiles_w_m2", {})
    if set(profiles) != CARDINALS or any(not isinstance(profiles[key], list) or len(profiles[key]) != 24 for key in CARDINALS):
        raise ValueError("The AI preliminary assumption pack needs four 24-hour cardinal solar profiles.")
    if not SHADING_CATEGORIES <= set(envelope.get("shading_categories", {})):
        raise ValueError("The AI preliminary assumption pack needs controlled shading categories.")
    height = pack.get("preliminary_defaults", {}).get("ceiling_height_mm")
    if not isinstance(height, (int, float)) or isinstance(height, bool) or height <= 0:
        raise ValueError("The AI preliminary assumption pack needs a positive named ceiling-height fallback.")
    opaque = envelope.get("opaque_constructions", {})
    if not isinstance(opaque, dict) or not {"wall", "roof", "floor", "ceiling", "partition"} <= set(opaque):
        raise ValueError("The AI preliminary assumption pack needs controlled opaque construction records.")
    for kind in ("wall", "roof", "floor", "ceiling", "partition"):
        row = opaque[kind]
        if not isinstance(row, dict) or not row.get("construction_id") or not isinstance(row.get("u_value_w_m2k"), (int, float)) or row["u_value_w_m2k"] <= 0:
            raise ValueError(f"The AI preliminary assumption pack has an invalid {kind} construction record.")
    return pack


def empty_settings():
    return {
        "schema_version": 1,
        "saved_project_consent": False,
        "automatic_analysis_enabled": False,
        "preliminary_pack_version": PACK_VERSION,
        "updated_at": "",
    }


def validate_settings(raw):
    result = empty_settings()
    result.update({key: raw[key] for key in result if isinstance(raw, dict) and key in raw})
    if not isinstance(result["saved_project_consent"], bool) or not isinstance(result["automatic_analysis_enabled"], bool):
        raise ValueError("AI preliminary consent and automatic-analysis settings must be true or false.")
    if result["preliminary_pack_version"] not in {PACK_VERSION, *LEGACY_PACK_VERSIONS}:
        raise ValueError("The selected AI preliminary pack version is unavailable.")
    return result


def provider_eligibility(settings, provider_configured):
    settings = validate_settings(settings)
    if not settings["automatic_analysis_enabled"]:
        return "disabled"
    if not settings["saved_project_consent"]:
        return "awaiting_consent"
    if not provider_configured:
        return "awaiting_provider"
    return "eligible"


def _handoff_evidence(value):
    """Return local evidence without paths, credentials, or opaque provider data."""
    blocked_keys = {"api_key", "authorization", "password", "secret", "source_pdf", "token"}
    if isinstance(value, dict):
        return {str(key): _handoff_evidence(item) for key, item in value.items()
                if str(key).casefold() not in blocked_keys}
    if isinstance(value, list):
        return [_handoff_evidence(item) for item in value]
    return deepcopy(value)


def build_local_codex_handoff(project, building, vision=None, fusion=None, source_fingerprints=None):
    """Build a local, provider-free request for a Codex-operated draft model.

    The handoff is deliberately an evidence package, not a calculator input.
    A later response must still satisfy ``validate_placeholder_proposal`` and
    can affect only the isolated draft preliminary model.
    """
    pack = load_pack()
    building = building if isinstance(building, dict) else {}
    vision = vision if isinstance(vision, dict) else {}
    fusion = fusion if isinstance(fusion, dict) else {}
    evidence_keys = (
        "levels", "spaces", "surfaces", "openings", "lighting", "equipment",
        "constructions", "cross_sheet_links", "exceptions", "vision_conflicts",
        "vision_missing_evidence",
    )
    payload = {
        "schema_version": CODEX_HANDOFF_SCHEMA_VERSION,
        "kind": "archie_local_codex_preliminary_handoff",
        "project": {
            "id": str(project.get("id", "")),
            "name": str(project.get("name", "")),
        },
        "mode": "local_codex_placeholder",
        "instructions": [
            "Act as the local preliminary visual interpreter for this project.",
            "Return only a proposal matching response_contract.proposal.",
            "Use only supplied source pages and evidence; do not invent a room, area, ceiling height, wall, opening, U-value, weather value, or equipment heat value.",
            "For ceiling heights, identify floor-to-finished-ceiling evidence only. Distinguish RCP, section, elevation and ceiling-note evidence from door/joinery dimensions, ceiling voids, title blocks, legends and unrelated details.",
            "Classify each room only with an allowed room-use taxonomy ID, then select its compatible preliminary profile.",
            "Keep unresolved or conflicting components out of the proposal and list them in issues.",
            "The resulting model is draft-only and must never be described as engineering reviewed or validated.",
        ],
        "allowed_preliminary_profiles": sorted(PROFILE_IDS),
        "allowed_room_use_categories": sorted(room_use_resolver.load_taxonomy()["categories"]),
        "allowed_space_scopes": sorted(SPACE_SCOPES),
        "allowed_surface_types": sorted(OPAQUE_TYPES),
        "allowed_orientations": sorted(CARDINALS),
        "allowed_shading_categories": sorted(SHADING_CATEGORIES),
        "response_contract": {
            "proposal": {
                "rooms": "source-linked room candidates: kind, label, level_name, geometry {boundary_points_px or ordered wall_ids, walls, dimensions, dimension_wall_links, scale_mm_per_px, source_crop}, area_m2 only when explicitly cited, ceiling_height_mm, ceiling_scope, ceiling_applies_to, ceiling_evidence, ceiling_datum_operands, room_use_category, preliminary_profile_id, confidence, page, rationale, alternatives",
                "surfaces": "source-linked external wall, roof, or ceiling candidates",
                "openings": "source-linked opening candidates linked to a proposed host surface",
                "issues": "unresolved, conflicting, or intentionally excluded components",
            },
            "required_response_fields": ["schema_version", "handoff_fingerprint", "proposal"],
        },
        "source_fingerprints": deepcopy(source_fingerprints or {}),
        "evidence": {
            "building": {key: _handoff_evidence(building.get(key, [])) for key in evidence_keys},
            "vision": _handoff_evidence(vision.get("result", vision)),
            "evidence_fusion": _handoff_evidence(fusion),
        },
        "assumption_pack": {
            "version": pack["version"],
            "profile_names": {profile_id: pack["profiles"][profile_id].get("label", profile_id)
                              for profile_id in sorted(PROFILE_IDS)},
        },
    }
    payload["handoff_fingerprint"] = fingerprint(payload)
    return payload


def empty_local_codex_response(handoff):
    """Return the writeable response template paired with a local handoff."""
    return {
        "schema_version": CODEX_HANDOFF_SCHEMA_VERSION,
        "kind": "archie_local_codex_preliminary_response",
        "handoff_fingerprint": str(handoff.get("handoff_fingerprint", "")),
        "proposal": {"rooms": [], "surfaces": [], "openings": [], "issues": []},
    }


def _number(value):
    try:
        value = float(value)
        return value if value > 0 else None
    except (TypeError, ValueError):
        match = re.search(r"\d+(?:\.\d+)?", str(value or ""))
        return float(match.group(0)) if match else None


def _slug(value):
    result = re.sub(r"[^a-z0-9]+", "-", str(value).casefold()).strip("-")
    return result or "conditioned-room"


def _confidence(value, fallback=0.45):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0.0, min(1.0, float(value)))
    return {"high": 0.85, "medium": 0.65, "low": 0.4}.get(str(value).casefold(), fallback)


def confidence_band(score):
    return "high" if score >= 0.8 else "medium" if score >= 0.5 else "low"


def _profile_from_label(label):
    text = str(label).casefold()
    if any(word in text for word in ("office", "meeting", "admin", "reception")):
        return "office"
    if any(word in text for word in ("restaurant", "cafe", "bar", "kitchen", "dining", "hospitality")):
        return "hospitality"
    if any(word in text for word in ("store", "storage", "warehouse", "cool room", "freezer")):
        return "storage"
    if any(word in text for word in ("bedroom", "apartment", "residential", "living")):
        return "residential"
    if any(word in text for word in ("retail", "shop", "showroom", "tenancy")):
        return "retail"
    return "generic_conditioned_room"


def _scope_from_label(label):
    text = str(label).casefold()
    if "cool room" in text or "coolroom" in text or "freezer" in text:
        return "refrigeration_process"
    if "kitchen" in text or "cook" in text or "food preparation" in text:
        return "comfort_hvac_with_process_exception"
    return "comfort_hvac"


def _room_identity(name, level):
    """Merge cross-source sightings without merging same-named floor rooms."""
    level = re.sub(r"\b(level|floor|fl)\b", "", str(level).casefold())
    return _slug(name), re.sub(r"[^a-z0-9]+", "", level) or "unassigned"


def _evidence_refs(space):
    return deepcopy(space.get("evidence", [])) if isinstance(space.get("evidence"), list) else []


def _combined_evidence(*values):
    rows, seen = [], set()
    for value in values:
        for row in _evidence_refs(value):
            key = json.dumps(row, sort_keys=True, separators=(",", ":"))
            if key not in seen:
                seen.add(key)
                rows.append(row)
    return rows


def _vision_rooms(vision, manual_entities=None):
    rows = vision.get("result", {}).get("auto_extraction", {}).get("entities", []) if isinstance(vision, dict) else []
    rows = list(rows) + list(manual_entities or [])
    return [row for row in rows if isinstance(row, dict) and row.get("kind") == "room"]


def _space_rows(building, vision, manual_entities=None, geometry_resolution=None):
    rows, seen, identities = [], set(), set()
    vision_rooms = _vision_rooms(vision, manual_entities)
    for space in building.get("spaces", []) if isinstance(building, dict) else []:
        if not isinstance(space, dict) or not str(space.get("name", "")).strip():
            continue
        name, level = str(space["name"]).strip(), str(space.get("level_name", "")).strip() or "Unassigned level"
        matches = [row for row in vision_rooms if str(row.get("label", "")).casefold() == name.casefold()
                   and (not row.get("level_name") or str(row.get("level_name")).casefold() == level.casefold())]
        # A source-linked placeholder can complete an existing PDF label (for
        # example, add its measured area) without creating a duplicate room.
        matches.sort(key=lambda row: (
            _number(row.get("area_m2")) is not None,
            str(row.get("preliminary_profile_id", "")) in PROFILE_IDS,
            _confidence(row.get("confidence")),
        ), reverse=True)
        ai = matches[0] if matches else {}
        evidence = _combined_evidence(space, ai)
        key = fingerprint({"name": name.casefold(), "level": level.casefold(), "evidence": evidence})[:16]
        if key in seen:
            continue
        seen.add(key)
        identities.add(_room_identity(name, level))
        rows.append({"key": key, "name": name, "level": level, "source_room_id": str(space.get("id", "")),
                     "area_m2": _number(space.get("area")) or _number(ai.get("area_m2")),
                     "area_origin": "pdf_evidence" if _number(space.get("area")) else (ai.get("area_origin") or ("ai_geometry" if _number(ai.get("area_m2")) else "")),
                     "area_verification_status": ai.get("area_verification_status", "provisional"),
                     "area_quality_label": ai.get("area_quality_label", ""),
                     "reviewer_trace_id": ai.get("reviewer_trace_id", ""),
                     "geometry_proof_id": ai.get("geometry_proof_id", ""),
                     "profile_id": ai.get("preliminary_profile_id", ""), "confidence": _confidence(ai.get("confidence", space.get("confidence"))),
                     "scope": ai.get("space_scope", _scope_from_label(name)), "evidence": evidence, "ai": ai})
    for ai in vision_rooms:
        name, level = str(ai.get("label", "")).strip(), str(ai.get("level_name", "")).strip() or "Unassigned level"
        if not name:
            continue
        key = fingerprint({"name": name.casefold(), "level": level.casefold(), "page": ai.get("page")})[:16]
        if key in seen or _room_identity(name, level) in identities:
            continue
        seen.add(key)
        identities.add(_room_identity(name, level))
        rows.append({"key": key, "name": name, "level": level, "source_room_id": "", "area_m2": _number(ai.get("area_m2")),
                     "area_origin": ai.get("area_origin") or ("ai_geometry" if _number(ai.get("area_m2")) else ""),
                     "area_verification_status": ai.get("area_verification_status", "provisional"),
                     "area_quality_label": ai.get("area_quality_label", ""),
                     "reviewer_trace_id": ai.get("reviewer_trace_id", ""),
                     "geometry_proof_id": ai.get("geometry_proof_id", ""),
                     "profile_id": ai.get("preliminary_profile_id", ""), "confidence": _confidence(ai.get("confidence")),
                     "scope": ai.get("space_scope", _scope_from_label(name)), "evidence": _evidence_refs(ai) or [{"page": ai.get("page"), "drawing_number": ai.get("drawing_number", ""), "excerpt": ai.get("excerpt", name)}], "ai": ai})
    if not rows:
        rows.append({"key": "whole-building-generic", "name": "Whole building conditioned area — AI review required", "level": "Unassigned level",
                     "source_room_id": "", "area_m2": None, "area_origin": "", "profile_id": "generic_conditioned_room", "confidence": 0.2, "scope": "unresolved_scope", "evidence": [], "ai": {}})
    return _apply_geometry_area_resolution(rows, geometry_resolution)


def _apply_geometry_area_resolution(rows, geometry_resolution):
    """Use only active normalized geometry areas in the draft-only path."""
    candidates = {}
    for entity in (geometry_resolution or {}).get("entities", []):
        if not isinstance(entity, dict) or entity.get("kind") != "area" or entity.get("geometry_status") not in {"ai_estimated", "geometry_confirmed"}:
            continue
        value = entity.get("value") if isinstance(entity.get("value"), dict) else {}
        area = _number(value.get("area_m2"))
        label, level = str(entity.get("label", "")).strip(), str(entity.get("level_candidate", "")).strip()
        if not area or not label or not level:
            continue
        key = _room_identity(label, level)
        candidates.setdefault(key, []).append((area, entity, value))
    for row in rows:
        matches = candidates.get(_room_identity(row["name"], row["level"]), [])
        # Multiple proofs are a geometry conflict even if they happen to
        # calculate to the same rounded area.  Choosing one would hide a
        # competing boundary or source and make the draft look more certain
        # than its evidence supports.
        if len(matches) != 1:
            continue
        area, entity, value = matches[0]
        row["area_m2"] = area
        row["area_origin"] = "ai_geometry" if entity.get("geometry_status") == "ai_estimated" else "geometry_proof"
        row["geometry_proof_id"] = value.get("geometry_proof_id") or entity.get("entity_id", "")
        row["geometry_mode"] = "preliminary_ai_estimate" if entity.get("geometry_status") == "ai_estimated" else "engineering_reviewed"
        row["geometry_evidence"] = {"page": entity.get("source", {}).get("page"), "drawing_number": entity.get("source", {}).get("drawing_number", ""),
                                    "derivation": deepcopy(value.get("derivation", {}))}
    return rows


def _apply_room_use_resolution(rows, artifact):
    """Apply controlled room-use decisions without changing room identity or geometry."""
    records = {row.get("room_id"): row for row in (artifact or {}).get("records", []) if isinstance(row, dict)}
    for row in rows:
        record = records.get(room_use_resolver.room_identity(row["name"], row["level"]))
        if not record:
            continue
        row["profile_id"] = record.get("preliminary_profile_id") or "generic_conditioned_room"
        row["scope"] = record.get("space_scope", "unresolved_scope")
        row["confidence"] = min(row["confidence"], float(record.get("confidence_score", row["confidence"])))
        row["room_use_resolution"] = record
    return rows


def _proposal_with_room_use(proposal, rows):
    """Provide the value resolver the same controlled room profile used by assembly."""
    result = deepcopy(proposal)
    result.setdefault("rooms", [])
    known = {_room_identity(row.get("label", ""), row.get("level_name", "")) for row in result["rooms"] if isinstance(row, dict)}
    for row in result["rooms"]:
        if not isinstance(row, dict):
            continue
        match = next((candidate for candidate in rows if _room_identity(candidate["name"], candidate["level"]) == _room_identity(row.get("label", ""), row.get("level_name", ""))), None)
        if match:
            row["preliminary_profile_id"] = match["profile_id"]
            row["space_scope"] = match["scope"]
            row["room_use_category"] = (match.get("room_use_resolution") or {}).get("taxonomy_id", "")
            # Keep the normalized proposal aligned with the authoritative
            # geometry proof.  The value is copied only after
            # _apply_geometry_area_resolution found exactly one active proof;
            # no visual or profile area is introduced here.
            if not _number(row.get("area_m2")) and _number(match.get("area_m2")):
                row["area_m2"] = match["area_m2"]
                row["area_origin"] = match.get("area_origin", "ai_geometry")
                row["geometry_proof_id"] = match.get("geometry_proof_id", "")
                row["geometry_mode"] = match.get("geometry_mode", "preliminary_ai_estimate")
                row["geometry_evidence"] = deepcopy(match.get("geometry_evidence", {}))
    for row in rows:
        identity = _room_identity(row["name"], row["level"])
        if identity not in known:
            result["rooms"].append({"kind": "room", "label": row["name"], "level_name": row["level"], "area_m2": row["area_m2"],
                                    "preliminary_profile_id": row["profile_id"], "space_scope": row["scope"],
                                    "confidence": row["confidence"], "page": (row["evidence"] or [{}])[0].get("page", 1),
                                    "evidence": row["evidence"], "room_use_category": (row.get("room_use_resolution") or {}).get("taxonomy_id", "")})
    return result


def _has_geometry_area_candidate(item):
    """Return whether a room carries enough geometry for the shared resolver.

    The geometry resolver remains authoritative for area activation.  This
    narrow admission check only prevents the preliminary proposal validator
    from rejecting a room before that resolver can calculate its area.
    """
    geometry = item.get("geometry") if isinstance(item, dict) else None
    if not isinstance(geometry, dict):
        return False
    coordinate_units = str(geometry.get("coordinate_units", "px")).casefold()
    points = (geometry.get("boundary_points_mm") if coordinate_units == "mm" else None) or geometry.get("boundary_points_px") or geometry.get("polygon_points_px") or geometry.get("points_px") or []
    if isinstance(points, list) and len(points) >= 4 and points[0] == points[-1]:
        try:
            return all(
                isinstance(point, (list, tuple)) and len(point) == 2
                and all(isinstance(value, (int, float)) and math.isfinite(value) for value in point)
                for point in points
            ) and ((coordinate_units == "mm" and geometry.get("dimension_wall_links"))
                   or geometry.get("scale_mm_per_px") is not None or geometry.get("dimension_wall_links"))
        except (TypeError, ValueError):
            return False
    # Ordered wall candidates are validated structurally by geometry_resolution.
    return bool(geometry.get("wall_ids") and (geometry.get("scale_mm_per_px") is not None or geometry.get("dimension_wall_links")))


def validate_manual_placeholder_entities(raw):
    """Validate the narrow, source-linked manual-AI recovery contract.

    The recovery path is intentionally room-only.  It lets a human-operated
    placeholder or future provider add an interpreted whole-building topology
    to the *separate preliminary model* without mutating reviewed evidence.
    """
    if not isinstance(raw, list) or not raw:
        raise ValueError("Manual placeholder evidence must contain at least one room entity.")
    result = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict) or item.get("kind") != "room":
            raise ValueError(f"Manual placeholder entity {index + 1} must be a room.")
        label = str(item.get("label", "")).strip()
        level = str(item.get("level_name", "")).strip()
        area = _number(item.get("area_m2"))
        geometry_area_candidate = _has_geometry_area_candidate(item)
        profile = str(item.get("preliminary_profile_id", "")).strip()
        page = item.get("page")
        if page in (None, ""):
            source_pages = item.get("source_pages")
            if isinstance(source_pages, list) and source_pages:
                page = source_pages[0]
            elif isinstance(item.get("evidence"), list):
                first_evidence = next((row for row in item["evidence"] if isinstance(row, dict) and row.get("page") is not None), None)
                page = first_evidence.get("page") if first_evidence else None
        try:
            page = int(page)
        except (TypeError, ValueError) as error:
            raise ValueError(f"Manual placeholder room '{label or index + 1}' needs a physical source page.") from error
        if not label or not level or (not area and not geometry_area_candidate) or profile not in PROFILE_IDS or page <= 0:
            raise ValueError(
                "Each manual placeholder room needs a label, validated area or structured geometry candidate, supported preliminary profile, and source page."
            )
        scope = str(item.get("space_scope", _scope_from_label(label))).strip()
        if scope not in SPACE_SCOPES:
            raise ValueError(f"Manual placeholder room '{label}' has an unsupported preliminary space scope.")
        score = _confidence(item.get("confidence"))
        evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else []
        evidence = [row for row in evidence if isinstance(row, dict) and row.get("page")]
        if not evidence:
            evidence = [{"page": page, "drawing_number": str(item.get("drawing_number", "")).strip(),
                         "excerpt": str(item.get("excerpt", label)).strip() or label}]
        category = str(item.get("room_use_category", "")).strip()
        taxonomy = room_use_resolver.load_taxonomy()
        if category and category not in taxonomy["categories"]:
            raise ValueError(f"Manual placeholder room '{label}' has an unsupported room-use category.")
        alternatives = item.get("room_use_alternatives", [])
        if not isinstance(alternatives, list) or any(value not in taxonomy["categories"] for value in alternatives):
            raise ValueError(f"Manual placeholder room '{label}' has unsupported room-use alternatives.")
        ceiling_height = _number(item.get("ceiling_height_mm"))
        if item.get("ceiling_height_mm") not in (None, "") and ceiling_height is None:
            raise ValueError(f"Manual placeholder room '{label}' has an invalid ceiling height.")
        ceiling_scope = str(item.get("ceiling_scope", "room")).strip().casefold() or "room"
        if ceiling_scope not in {"room", "zone", "level"}:
            raise ValueError(f"Manual placeholder room '{label}' has an unsupported ceiling scope.")
        ceiling_applies_to = item.get("ceiling_applies_to", [])
        if not isinstance(ceiling_applies_to, list):
            raise ValueError(f"Manual placeholder room '{label}' has an invalid ceiling applicability list.")
        if ceiling_scope != "room" and ceiling_height is not None and not ceiling_applies_to:
            raise ValueError(f"Manual placeholder room '{label}' needs explicit affected rooms for a shared ceiling height.")
        ceiling_evidence = item.get("ceiling_evidence") if isinstance(item.get("ceiling_evidence"), list) else evidence
        ceiling_evidence = [row for row in ceiling_evidence if isinstance(row, dict) and row.get("page")]
        ceiling_conflicts = item.get("ceiling_conflicts", [])
        if not isinstance(ceiling_conflicts, list):
            raise ValueError(f"Manual placeholder room '{label}' has an invalid ceiling conflict list.")
        result.append({
            "kind": "room", "label": label, "level_name": level, "area_m2": area,
            "preliminary_profile_id": profile, "confidence": score, "page": page,
            "space_scope": scope,
            "drawing_number": str(item.get("drawing_number", "")).strip(),
            "excerpt": str(item.get("excerpt", label)).strip() or label,
            "evidence": evidence,
            "rationale": str(item.get("rationale", "")).strip(),
            "room_use_category": category, "room_use_rationale": str(item.get("room_use_rationale", "")).strip(),
            "room_use_alternatives": sorted(set(alternatives)),
            "assumptions": list(item.get("assumptions", [])) if isinstance(item.get("assumptions"), list) else [],
            "ceiling_height_mm": ceiling_height, "ceiling_scope": ceiling_scope,
            "ceiling_applies_to": deepcopy(ceiling_applies_to), "ceiling_evidence": ceiling_evidence,
            "ceiling_rationale": str(item.get("ceiling_rationale", "")).strip(),
            "ceiling_datum_operands": deepcopy(item.get("ceiling_datum_operands", {})) if isinstance(item.get("ceiling_datum_operands", {}), dict) else {},
            "ceiling_conflicts": list(ceiling_conflicts),
            "geometry": deepcopy(item.get("geometry", {})) if isinstance(item.get("geometry", {}), dict) else {},
            "area_origin": str(item.get("area_origin", "")) if str(item.get("area_origin", "")) in {"pdf_evidence", "printed (read from image)", "edited", "reviewer_traced", "ai_geometry", "ai_determined"} else "",
            "area_verification_status": "provisional",
            "area_quality_label": str(((item.get("reviewer_traced_area") or {}).get("ai_quality_label") if isinstance(item.get("reviewer_traced_area"), dict) else "") or item.get("area_quality_label", "")),
            "reviewer_trace_id": str(item.get("reviewer_trace_id", "")),
            "geometry_proof_id": str(item.get("geometry_proof_id", "")),
            "reviewer_traced_area": deepcopy(item.get("reviewer_traced_area", {})) if isinstance(item.get("reviewer_traced_area", {}), dict) else {},
            "source_pages": sorted({int(row.get("page")) for row in evidence if str(row.get("page", "")).isdigit()}),
            # Internal-gains evidence is preserved as proposed evidence.  The
            # resolver below applies citation and controlled-value checks; this
            # validator must not discard the AI's counted seats, fixtures,
            # equipment, or explicit schedule candidates.
            "occupancy_count": _number(item.get("occupancy_count")),
            "seat_count": _number(item.get("seat_count")),
            "workstation_count": _number(item.get("workstation_count")),
            "desk_count": _number(item.get("desk_count")),
            "bed_count": _number(item.get("bed_count")),
            "people_sensible_w_per_person": _number(item.get("people_sensible_w_per_person")),
            "people_latent_w_per_person": _number(item.get("people_latent_w_per_person")),
            "lighting_fixtures": deepcopy(item.get("lighting_fixtures", [])) if isinstance(item.get("lighting_fixtures", []), list) else [],
            "equipment": deepcopy(item.get("equipment", [])) if isinstance(item.get("equipment", []), list) else [],
            "schedules": deepcopy(item.get("schedules", {})) if isinstance(item.get("schedules", {}), dict) else {},
        })
    identities = [_room_identity(row["label"], row["level_name"]) for row in result]
    if len(set(identities)) != len(identities):
        raise ValueError("Manual placeholder rooms must have unique name-and-level identities.")
    return sorted(result, key=lambda row: (_room_identity(row["label"], row["level_name"]), row["page"]))


def _profile_schedule(profile_id):
    active = {"retail": range(9, 18), "office": range(8, 18), "hospitality": range(10, 23), "storage": range(7, 18), "residential": range(6, 23), "generic_conditioned_room": range(8, 18)}[profile_id]
    return [1.0 if hour in active else 0.0 for hour in range(24)]


def _source(profile_id):
    return f"AI preliminary assumption pack {PACK_VERSION}: {profile_id}"


def _schedule(schedule_id, profile_id):
    values = _profile_schedule(profile_id)
    day_profiles = {day: {"values": values, "status": "provisional", "source": _source(profile_id), "citations": []}
                    for day in ("weekday", "saturday", "sunday_holiday")}
    return {"schedule_id": schedule_id, "title": f"Preliminary {profile_id} operating profile", "description": "Controlled preliminary pack schedule; review required.",
            "status": "provisional", "source": _source(profile_id), "citations": [], "day_profiles": day_profiles}


GENERIC_WEATHER_ORIGINS = {"preliminary_fallback", "unresolved", ""}


def design_conditions_basis(resolution_artifact, site_location=None, pack_version=PACK_VERSION):
    """Say plainly which design day, sun values and site the draft used.

    The design day is site-specific only when value resolution selected a
    cited source; the sun values are always the pack's generic façade
    profiles until a cited site solar source exists.
    """
    record = next((row for row in (resolution_artifact or {}).get("records", [])
                   if isinstance(row, dict) and row.get("target") == "scenario.weather_profile"), {})
    origin = str(record.get("origin", ""))
    citation = record.get("citation", {}) if isinstance(record.get("citation"), dict) else {}
    cited = origin not in GENERIC_WEATHER_ORIGINS and bool(record.get("value"))
    source_text = " — ".join(part for part in (citation.get("publisher", ""), citation.get("citation", "")) if part)
    design_day = ({"site_specific": True, "origin": origin, "source": source_text or origin,
                   "label": f"Site design day: {source_text or origin}"}
                  if cited else
                  {"site_specific": False, "origin": origin or "preliminary_fallback", "source": f"AI preliminary assumption pack {pack_version}",
                   "label": f"Generic Australian cooling design day (assumption pack {pack_version}) — not site-specific"})
    sun = {"site_specific": False, "source": f"AI preliminary assumption pack {pack_version}",
           "directions": ["N", "E", "S", "W"],
           "label": (f"Generic preliminary sun values by façade direction (assumption pack {pack_version}, N/E/S/W) — "
                     "not site-specific; no flat-roof sun")}
    location = (site_location or {}).get("location", {}) if isinstance(site_location, dict) else {}
    address = str((site_location or {}).get("confirmed_address", "")).strip() if isinstance(site_location, dict) else ""
    if address and location.get("latitude_deg") is not None:
        basis = {"reviewer_cited_map": "reviewer-cited map position"}.get(location.get("basis"), "geocoded address")
        site = {"confirmed": True, "address": address, "basis": location.get("basis", "gnaf"),
                "label": f"Site: {address} ({basis})"}
    else:
        site = {"confirmed": False, "address": address, "basis": "", "label": "Site location not confirmed"}
    return {"design_day": design_day, "sun": sun, "site": site}


def _scenario(pack, resolved=None, basis=None, site_days=None):
    """The design days to calculate: the resolved (or preliminary) day, and with a site's design days both of them:
    the dry-bulb day sizes the load, the humid day is a dehumidification check (see calculate)."""
    if site_days:
        days = []
        for index, day in enumerate(site_days["days"]):
            single = _scenario(pack, {**(resolved or {}), "weather_profile": {**((resolved or {}).get("weather_profile") or {}), "hours": day["hours"]}}, basis)
            row = single["scenarios"][0]
            row.update({"scenario_id": "ai_preliminary_cooling_day" if index == 0 else f"ai_preliminary_{day['day']}_day",
                        "title": f"{site_days['location']} — {day['title']}"})
            days.append(row)
        return {"scenarios": days}
    scenario = deepcopy(pack["scenario"])
    resolved = resolved or {}
    for key in ("indoor_dry_bulb_c", "indoor_wet_bulb_c"):
        if resolved.get(key) is not None:
            scenario[key] = resolved[key]
    weather = resolved.get("weather_profile")
    if isinstance(weather, dict) and isinstance(weather.get("hours"), list) and len(weather["hours"]) == 24:
        scenario = {**scenario, **weather}
    design_day = (basis or {}).get("design_day", {})
    title = ("Site cooling design day" if design_day.get("site_specific")
             else "AI preliminary generic Australian cooling day (not site-specific)")
    return {"scenarios": [{
        "scenario_id": "ai_preliminary_cooling_day", "title": title, "mode": "cooling",
        "representative_month": "January", "day_type": "weekday", "status": "provisional",
        "source": design_day.get("source") or f"AI preliminary assumption pack {pack['version']}", "citations": [],
        "atmospheric_pressure_kpa": {"value": scenario["pressure_kpa"], "status": "provisional", "source": _source("scenario"), "citations": []},
        "hours": [{"hour": hour, "outdoor_dry_bulb_c": {"value": point["db"], "status": "provisional", "source": _source("scenario"), "citations": []},
                   "outdoor_wet_bulb_c": {"value": point["wb"], "status": "provisional", "source": _source("scenario"), "citations": []},
                   "outdoor_wet_bulb_basis": point.get("wet_bulb_basis", "legacy_unverified")}
                  for hour, point in enumerate(scenario["hours"])],
    }]}


def _proposal_evidence(item):
    evidence = _evidence_refs(item)
    page = item.get("page")
    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 0
    if not evidence and page > 0:
        evidence = [{"page": page, "drawing_number": str(item.get("drawing_number", "")).strip(),
                     "excerpt": str(item.get("excerpt", item.get("label", ""))).strip()}]
    return [row for row in evidence if isinstance(row, dict) and _number(row.get("page"))]


def _proposal_id(prefix, item, owner=""):
    anchor = item.get("surface_key") or item.get("opening_key") or item.get("id") or item.get("label") or prefix
    return f"{prefix}-{fingerprint({'anchor': anchor, 'owner': owner, 'page': item.get('page'), 'evidence': _proposal_evidence(item), 'geometry': {key: item.get(key) for key in ('gross_area_m2', 'net_opaque_area_m2', 'opening_area_m2', 'width_m', 'height_m', 'quantity', 'orientation')}})[:16]}"


def validate_placeholder_proposal(raw):
    """Normalise AI/manual envelope proposals without allowing them to alter reviewed inputs.

    Invalid candidate rows are retained as exclusions.  Only a malformed top-level
    payload is rejected, so an AI can provide a useful partial-building estimate.
    """
    if raw in (None, ""):
        raw = {}
    if isinstance(raw, list):  # Legacy room-only manual placeholder payload.
        raw = {"rooms": raw}
    if not isinstance(raw, dict):
        raise ValueError("AI preliminary placeholder proposal must be an object.")
    room_rows = raw.get("rooms", raw.get("entities", []))
    if room_rows and not isinstance(room_rows, list):
        raise ValueError("AI preliminary proposal rooms must be a list.")
    rooms = validate_manual_placeholder_entities(room_rows) if room_rows else []
    raw_issues = raw.get("issues", [])
    result = {"rooms": rooms, "surfaces": [], "openings": [],
              "envelope_assessments": [deepcopy(row) for row in raw.get("envelope_assessments", []) if isinstance(row, dict)]
              if isinstance(raw.get("envelope_assessments", []), list) else [],
              "issues": [deepcopy(item) for item in raw_issues if isinstance(item, dict)] if isinstance(raw_issues, list) else []}
    for group, prefix in (("surfaces", "ai-preliminary-surface"), ("openings", "ai-preliminary-opening")):
        rows = raw.get(group, [])
        if not isinstance(rows, list):
            raise ValueError(f"AI preliminary proposal {group} must be a list.")
        for index, raw_item in enumerate(rows, start=1):
            if not isinstance(raw_item, dict):
                result["issues"].append({"component": group[:-1], "reason": f"Candidate {index} is not an object."})
                continue
            item = deepcopy(raw_item)
            item["label"] = str(item.get("label", item.get("surface_key", item.get("opening_key", "")))).strip()
            item["owner_room_label"] = str(item.get("owner_room_label", item.get("room_label", ""))).strip()
            item["owner_level_name"] = str(item.get("owner_level_name", item.get("level_name", ""))).strip()
            item["evidence"] = _proposal_evidence(item)
            item["confidence"] = _confidence(item.get("confidence"))
            item["confidence_band"] = confidence_band(item["confidence"])
            raw_conflicts = item.get("conflicts", [])
            item["conflicts"] = list(raw_conflicts) if isinstance(raw_conflicts, list) else (["Malformed conflict list."] if raw_conflicts not in (None, "") else [])
            item["assumptions"] = list(item.get("assumptions", [])) if isinstance(item.get("assumptions"), list) else []
            item["candidate_id"] = _proposal_id(prefix, item, item["owner_room_label"])
            errors = []
            if not item["owner_room_label"] or not item["owner_level_name"]:
                errors.append("an unambiguous room owner and level")
            if not item["evidence"]:
                errors.append("at least one physical source page")
            if group == "surfaces":
                item["physical_type"] = str(item.get("physical_type", item.get("surface_kind", "wall"))).casefold()
                item["thermal_role"] = str(item.get("thermal_role", item.get("boundary_reference", "external"))).casefold()
                item["external_exposure"] = str(item.get("external_exposure", "external")).casefold()
                item["orientation"] = str(item.get("orientation", "")).upper()
                item["gross_area_m2"] = _number(item.get("gross_area_m2", item.get("area_m2")))
                item["net_opaque_area_m2"] = _number(item.get("net_opaque_area_m2"))
                item["opening_coverage"] = str(item.get("opening_coverage", "not_applicable")).casefold()
                if item["physical_type"] not in OPAQUE_TYPES:
                    errors.append("a supported opaque physical type (wall, roof, floor, ceiling, or partition)")
                if item["thermal_role"] not in {"external", "outside", "outdoors", "ground_contact", "fixed_adjacent", "room_to_room", "roof_void", "ceiling_below_roof", "internal_floor", "unresolved"}:
                    errors.append("a supported thermal role")
                if item["physical_type"] in {"wall", "roof", "ceiling"} and item["thermal_role"] in {"external", "outside", "outdoors"} and item["external_exposure"] != "external":
                    errors.append("confirmed external thermal exposure")
                if not item["gross_area_m2"]:
                    errors.append("positive gross surface area")
                if item["net_opaque_area_m2"] and item["gross_area_m2"] and item["net_opaque_area_m2"] > item["gross_area_m2"]:
                    errors.append("net opaque area no greater than gross area")
            else:
                item["host_surface_key"] = str(item.get("host_surface_key", item.get("host_surface_id", ""))).strip()
                item["external_exposure"] = str(item.get("external_exposure", "unresolved")).casefold()
                item["orientation"] = str(item.get("orientation", "")).upper()
                item["shading_category"] = str(item.get("shading_category", "unshaded")).casefold()
                item["opening_area_m2"] = _number(item.get("opening_area_m2"))
                width, height, quantity = _number(item.get("width_m")), _number(item.get("height_m")), _number(item.get("quantity", 1))
                if not item["opening_area_m2"] and width and height and quantity:
                    item["opening_area_m2"] = width * height * quantity
                item["explicit_glass_area_m2"] = _number(item.get("explicit_glass_area_m2"))
                if not item["host_surface_key"]:
                    errors.append("a host surface key")
                if item["external_exposure"] not in {"external", "internal"}:
                    errors.append("external or internal exposure decision")
                if not item["opening_area_m2"]:
                    errors.append("positive opening geometry")
                if item["explicit_glass_area_m2"] and item["opening_area_m2"] and item["explicit_glass_area_m2"] > item["opening_area_m2"]:
                    errors.append("glass area no greater than opening area")
                if item["shading_category"] not in SHADING_CATEGORIES:
                    errors.append("a controlled shading category")
                glazing_choice = item.get("glazing_choice")
                if glazing_choice and glazing_choice not in load_pack().get("profiles", {}):
                    errors.append("a glazing choice in the preliminary assumption pack")
                if item["shading_category"] == "unshaded" and "unshaded" not in item["assumptions"]:
                    item["assumptions"].append("unshaded")
            item["validation_errors"] = errors + (["AI reported competing evidence."] if item["conflicts"] else [])
            result[group].append(item)
    for key in ("surfaces", "openings"):
        result[key].sort(key=lambda item: item["candidate_id"])
    return result


def _schedule_with_values(schedule_id, title, values, source):
    return {"schedule_id": schedule_id, "title": title, "description": "Controlled preliminary pack schedule; review required.",
            "status": "provisional", "source": source, "citations": [],
            "day_profiles": {day: {"values": values, "status": "provisional", "source": source, "citations": []}
                             for day in ("weekday", "saturday", "sunday_holiday")}}


def _find_owner(rows, label, level):
    candidates = [row for row in rows if row["name"].casefold() == label.casefold() and row["level"].casefold() == level.casefold()]
    return candidates[0] if len(candidates) == 1 else None


def _legacy_surface_candidates(vision, rows):
    entities = vision.get("result", {}).get("auto_extraction", {}).get("entities", []) if isinstance(vision, dict) else []
    output = []
    for entity in entities:
        if not isinstance(entity, dict) or entity.get("kind") != "surface":
            continue
        label = str(entity.get("label", ""))
        owner = next((row for row in rows if row["name"].casefold() in label.casefold()), None)
        if not owner:
            continue
        output.append({"candidate_id": _proposal_id("legacy-surface", entity, owner["name"]), "label": label,
                       "owner_room_label": owner["name"], "owner_level_name": owner["level"],
                       "physical_type": str(entity.get("surface_kind", "wall")).casefold(),
                       "thermal_role": str(entity.get("boundary_reference", "external")).casefold(),
                       "external_exposure": "external", "orientation": str(entity.get("orientation", "")).upper(),
                       "gross_area_m2": _number(entity.get("area_m2")), "net_opaque_area_m2": None,
                       "opening_coverage": "not_applicable", "confidence": _confidence(entity.get("confidence")),
                       "confidence_band": confidence_band(_confidence(entity.get("confidence"))),
                       "evidence": _proposal_evidence(entity), "assumptions": [], "conflicts": [], "validation_errors": []})
    return output


def assemble(building, vision=None, contractor_overrides=None, source_fingerprints=None, manual_placeholder_entities=None,
             preliminary_proposal=None, value_resolution=None, research_cache=None, source_pack_releases=None,
             site_location=None, site_design_weather=None, room_use_resolution=None, geometry_resolution=None,
             ceiling_volume_resolution=None, internal_gains_resolution=None, airflow_resolution=None, ahu_resolution=None,
             plant_resolution=None, allow_area_fallbacks=True, process_exhaust=None, design_weather_table=None):
    """Return a materialized preliminary payload plus its transparent ledger.

    process_exhaust: answered kitchen exhaust per room name (lower case): {"lps", "method", "method_assumed"}.
    design_weather_table: an imported design-temperature table (ai.design_weather.load_table()); with a confirmed
    site and no cited site design day, the site's two summer design days come from its nearest listed location.
    """
    pack = load_pack()
    overrides = contractor_overrides or {}
    process_exhaust = process_exhaust or {}
    rooms, zones, floors, ledger, exclusions, schedules = [], [], [], [], [], []
    floor_ids, zone_ids = {}, set()
    requirements_zones = []
    proposal = validate_placeholder_proposal(preliminary_proposal if preliminary_proposal is not None else (manual_placeholder_entities or {}))
    for issue in proposal.get("issues", []):
        if not isinstance(issue, dict):
            continue
        exclusions.append({"room_id": issue.get("room_id", ""), "component": "room area",
                           "reason": issue.get("reason", "No validated room area is available."),
                           "evidence": deepcopy(issue.get("evidence", [])),
                           "remediation": issue.get("remediation", "Resolve the room boundary and area from cited dimensions.")})
    manual_entities = proposal["rooms"]
    space_rows = _space_rows(building, vision, manual_entities, geometry_resolution)
    room_use_artifact = room_use_resolver.validate(room_use_resolution) if room_use_resolution else room_use_resolver.resolve(
        building, vision or {}, proposal, source_fingerprints or {},
    )
    space_rows = _apply_room_use_resolution(space_rows, room_use_artifact)
    effective_proposal = _proposal_with_room_use(proposal, space_rows)
    ceiling_artifact = ceiling_volume_resolver.validate(ceiling_volume_resolution) if ceiling_volume_resolution else ceiling_volume_resolver.resolve(
        building, vision or {}, effective_proposal, geometry_resolution, pack, source_fingerprints or {},
    )
    # Internal-gains resolution is a separate, auditable input layer.  Keep a
    # deterministic fallback for callers that predate the artifact, but never
    # silently replace a supplied record with profile values.
    try:
        from ai import internal_gains_resolution as internal_gains_resolver
        internal_artifact = internal_gains_resolver.validate(internal_gains_resolution) if internal_gains_resolution else internal_gains_resolver.resolve(
            building, vision or {}, effective_proposal, room_use_artifact, pack, source_fingerprints or {},
        )
    except ImportError:  # pragma: no cover - retained for old isolated imports
        internal_artifact = {"records": [], "schedules": [], "fingerprint": ""}
    internal_records = {row.get("room_id"): row for row in internal_artifact.get("records", [])
                        if isinstance(row, dict) and row.get("status") != "stale"}
    airflow_artifact = airflow_resolution if isinstance(airflow_resolution, dict) else {"records": [], "fingerprint": ""}
    ahu_artifact = ahu_resolution if isinstance(ahu_resolution, dict) else {"systems": [], "airflow_records": [], "fingerprint": ""}
    plant_artifact = plant_resolution if isinstance(plant_resolution, dict) else {"systems": [], "circuits": [], "mappings": [], "fingerprint": ""}
    airflow_by_room = {}
    airflow_precedence = {"unresolved": 0, "controlled_fallback": 1, "research_candidate": 2,
                          "released_source_pack": 3, "project_evidence": 4, "contractor_override": 5}
    for airflow_row in airflow_artifact.get("records", []):
        if (not isinstance(airflow_row, dict) or airflow_row.get("status") == "stale"
                or airflow_row.get("air_path_type") not in {"outside_air", "infiltration"}):
            continue
        room_paths = airflow_by_room.setdefault(airflow_row.get("owner_room_id"), {})
        path = airflow_row.get("air_path_type")
        current = room_paths.get(path)
        if current is None or (airflow_row.get("status") == "blocked" and current.get("status") != "blocked") or (
            airflow_row.get("status") != "blocked" and current.get("status") != "blocked"
            and airflow_precedence.get(airflow_row.get("origin"), 0) > airflow_precedence.get(current.get("origin"), 0)
        ):
            room_paths[path] = airflow_row
    resolution_artifact, resolved_values = value_resolver.build_value_resolution(
        pack, building, effective_proposal, research_cache or {"schema_version": 1, "revision": 0, "source_pack_version": "", "records": []},
        value_resolution, source_pack_releases, source_fingerprints, site_location, site_design_weather, ceiling_artifact,
        internal_artifact,
        ((geometry_resolution or {}).get("thermal_surface_ledger", {}) if isinstance(geometry_resolution, dict) else {}),
        airflow_resolution=airflow_artifact,
    )
    scenario_values = resolved_values["scenario"]
    conditions_basis = design_conditions_basis(resolution_artifact, site_location, pack["version"])
    site_days = None
    if not conditions_basis["design_day"]["site_specific"] and design_weather_table:
        # No cited site design day (e.g. an approved DA09 pack): the nearest listed location's two summer design days.
        site_days = design_weather.for_site(site_location, scenario_values["weather_profile"]["hours"], table=design_weather_table)
    if site_days:
        scenario_values = {**scenario_values, "weather_profile": {**scenario_values["weather_profile"], "hours": site_days["days"][0]["hours"]}}
        distance = f"{site_days['distance_km']:g} km from the site"
        conditions_basis["design_day"] = {
            "site_specific": True, "origin": "design_temperature_table_nearest_location", "source": site_days["source"],
            "location": site_days["location"], "state": site_days["state"], "distance_km": site_days["distance_km"],
            "far_from_site": site_days["far"],
            "days": [{key: day[key] for key in ("day", "title", "design_point")} for day in site_days["days"]],
            "label": (f"Summer design days for {site_days['location']} ({distance}{', the nearest listed location — check it suits the site' if site_days['far'] else ''}): "
                      f"{site_days['values']['db']:g} °C dry bulb with {site_days['values']['cwb']:g} °C wet bulb, and "
                      f"{site_days['values']['wb']:g} °C wet bulb with {site_days['values']['cdb']:g} °C dry bulb as a dehumidification check. "
                      f"Source: {site_days['source']}.")}
    active_rows, excluded_spaces = [], []
    excluded_spaces.extend({"room_name": issue.get("label", ""), "level": issue.get("level", ""),
                            "scope": issue.get("scope", "unresolved_scope"),
                            "reason": issue.get("reason", "No validated room area is available."),
                            "evidence": deepcopy(issue.get("evidence", []))}
                           for issue in proposal.get("issues", []) if isinstance(issue, dict) and issue.get("component") == "room area")
    for row in space_rows:
        row["scope"] = row["scope"] if row["scope"] in SPACE_SCOPES else "unresolved_scope"
        if row["scope"] in {"refrigeration_process", "unresolved_scope", "not_a_room"}:
            reason = ("Refrigeration load required; this room is excluded from the comfort-HVAC subtotal."
                      if row["scope"] == "refrigeration_process" else
                      "A reviewer marked this detection as not a room." if row["scope"] == "not_a_room" else
                      "AI could not resolve whether this room belongs in comfort-HVAC scope.")
            excluded_spaces.append({"room_name": row["name"], "level": row["level"], "scope": row["scope"], "reason": reason,
                                    "evidence": row["evidence"]})
            exclusions.append({"room_id": f"room-{row['key']}", "component": "room scope", "reason": reason})
            continue
        override_area = _number((overrides.get(row["key"], {}) if isinstance(overrides, dict) else {}).get("area_m2"))
        if not allow_area_fallbacks and not (override_area or row.get("area_m2")):
            reason = "No explicit or geometry-resolved room area is available; a generic area fallback was not applied."
            excluded_spaces.append({"room_name": row["name"], "level": row["level"], "scope": row["scope"],
                                    "reason": reason, "evidence": row["evidence"]})
            exclusions.append({"room_id": f"room-{row['key']}", "component": "room area", "reason": reason,
                               "evidence": row["evidence"],
                               "remediation": "Complete the cited room boundary and dimension chain, or provide an explicit project area override."})
            continue
        active_rows.append(row)
        profile_id = row["profile_id"] if row["profile_id"] in PROFILE_IDS else _profile_from_label(row["name"])
        profile = deepcopy(pack["profiles"][profile_id])
        room_resolution = resolved_values["rooms"].get(value_resolver.room_target_id(row["name"], row["level"]), {})
        profile.update({key: value for key, value in room_resolution.items() if key in profile and value is not None})
        internal_record = internal_records.get(room_use_resolver.room_identity(row["name"], row["level"]), {})
        internal_fields = internal_record.get("fields", {}) if isinstance(internal_record, dict) else {}
        override = overrides.get(row["key"], {}) if isinstance(overrides, dict) else {}
        area = _number(override.get("area_m2")) or row["area_m2"] or (profile["fallback_area_m2"] if allow_area_fallbacks else None)
        area_origin = "contractor_override" if _number(override.get("area_m2")) else (row.get("area_origin") or "ai_assumption")
        score = row["confidence"] if row["area_m2"] else min(row["confidence"], 0.4)
        level_key = _slug(row["level"])
        floor_id = floor_ids.setdefault(level_key, f"floor-{level_key}")
        if not any(item["floor_id"] == floor_id for item in floors):
            floors.append({"floor_id": floor_id, "name": row["level"], "elevation_m": None, "verification_status": "provisional", "source": _source("topology"), "citations": []})
        room_id = f"room-{row['key']}"
        zone_id = f"zone-{row['key']}"
        zone_ids.add(zone_id)
        def _resolved_internal_value(field_name, record_key):
            field = internal_fields.get(field_name, {}) if isinstance(internal_fields, dict) else {}
            origin = field.get("origin") if isinstance(field, dict) else ""
            # A controlled profile is already represented by the value
            # resolver/profile below.  Only evidence/override/AI-specific
            # fields replace a newer released value.
            return internal_record.get(record_key) if origin in {"direct_project_evidence", "contractor_override", "project_evidence", "ai_interpretation", "ai_estimated"} else None
        occupancy = _resolved_internal_value("occupancy_count", "occupancy_count") if internal_record else None
        occupancy = int(occupancy) if isinstance(occupancy, (int, float)) and occupancy >= 0 else max(1, round(area * profile["occupancy_density_people_m2"]))
        schedule_id = internal_record.get("schedule_id") if internal_record else f"prelim-{profile_id}"
        internal_schedule = next((item for item in internal_artifact.get("schedules", []) if item.get("schedule_id") == schedule_id), None)
        if internal_schedule and not any(item["schedule_id"] == schedule_id for item in schedules):
            day_profiles = internal_schedule.get("day_profiles", {})
            schedules.append({"schedule_id": schedule_id, "title": "Resolved internal-gains schedule", "description": "Draft schedule from internal-gains resolver.", "status": internal_schedule.get("status", "provisional"), "source": internal_schedule.get("source", "internal-gains resolution"), "citations": [], "day_profiles": {
                "weekday": {"values": day_profiles.get("weekday", _profile_schedule(profile_id)), "status": "provisional", "source": internal_schedule.get("source", "internal-gains resolution"), "citations": []},
                "saturday": {"values": day_profiles.get("saturday", _profile_schedule(profile_id)), "status": "provisional", "source": internal_schedule.get("source", "internal-gains resolution"), "citations": []},
                "sunday_holiday": {"values": day_profiles.get("sunday", day_profiles.get("holiday", _profile_schedule(profile_id))), "status": "provisional", "source": internal_schedule.get("source", "internal-gains resolution"), "citations": []},
            }})
        elif not any(item["schedule_id"] == schedule_id for item in schedules):
            schedules.append(_schedule(schedule_id, profile_id))
        people_sensible = _resolved_internal_value("people_sensible_w_per_person", "people_sensible_w_per_person") if internal_record else None
        people_latent = _resolved_internal_value("people_latent_w_per_person", "people_latent_w_per_person") if internal_record else None
        people_diversity = (internal_fields.get("people_diversity", {}).get("value") if isinstance(internal_fields.get("people_diversity"), dict) else None) or profile["people_diversity"]
        lighting_load_w = _resolved_internal_value("lighting_load_w", "lighting_load_w") if internal_record else None
        lighting_w_m2 = lighting_load_w / area if lighting_load_w is not None and area else profile["lighting_w_m2"]
        lighting_diversity = (internal_fields.get("lighting_diversity", {}).get("value") if isinstance(internal_fields.get("lighting_diversity"), dict) else None) or profile["lighting_diversity"]
        room_airflows = airflow_by_room.get(ceiling_volume_resolver.room_identity(row["name"], row["level"]), {})
        outside_record = room_airflows.get("outside_air", {})
        outside_air = round(occupancy * profile["outside_air_lps_person"] + area * profile["outside_air_lps_m2"], 3)
        if outside_record.get("air_path_type") == "outside_air" and outside_record.get("status") not in {"blocked", "excluded"} and outside_record.get("value") is not None:
            outside_air = round(float(outside_record["value"]), 3)
        exhaust = process_exhaust.get(str(row["name"]).casefold())
        exhaust_note = ""
        if exhaust and _number(exhaust.get("lps")):
            method = exhaust.get("method") or "through_space"
            assumed = " (method assumed: not given)" if exhaust.get("method_assumed") else ""
            if method == "through_space":
                # The hood's air is replaced through the conditioned space: outside air enters to replace it, so the
                # room takes the larger of its ventilation air and the exhaust (ventilation air already counted once).
                replacement = max(0.0, float(exhaust["lps"]) - outside_air)
                ledger.append({"room_id": room_id, "field": "process_exhaust_replacement_lps", "value": round(replacement, 3),
                               "origin": "operator_answer", "profile_id": profile_id, "confidence": 0.7, "confidence_band": confidence_band(0.7),
                               "rationale": f"Kitchen exhaust {exhaust['lps']:g} L/s replaced through the air-conditioned space{assumed}; "
                                            f"outside air = max(ventilation {outside_air:g} L/s, exhaust {exhaust['lps']:g} L/s).",
                               "evidence": []})
                outside_air = round(max(outside_air, float(exhaust["lps"])), 3)
            else:
                exhaust_note = ("Exhaust replaced by untempered make-up air at the hood: no cooling load on this system."
                                if method == "untempered_makeup" else
                                f"Exhaust {exhaust['lps']:g} L/s replaced by a tempered make-up air unit: its load belongs to that unit, not this system.")
        # Heat per person by the room's typical activity (kitchen work gives off far more moisture than dining); a
        # value from the drawings or an operator's answer replaces it.
        room_use = row.get("room_use_category") or (row.get("room_use_resolution") or {}).get("taxonomy_id") or row.get("taxonomy_id") or ""
        activity = (pack.get("people_activity", {}).get("by_room_use", {}) or {}).get(room_use)
        if activity and not (people_sensible and people_latent):
            ledger.append({"room_id": room_id, "field": "people_activity", "value": activity["activity"],
                           "origin": "controlled_preliminary_profile", "profile_id": profile_id, "confidence": 0.5,
                           "confidence_band": confidence_band(0.5),
                           "rationale": f"{activity['sensible_w']} W sensible + {activity['latent_w']} W latent per person "
                                        f"({activity['activity']}): {pack['people_activity']['source']}.", "evidence": []})
        default_sensible = activity["sensible_w"] if activity else profile["people_sensible_w"]
        default_latent = activity["latent_w"] if activity else profile["people_latent_w"]
        cooling = {"people_sensible_w_per_person": people_sensible or default_sensible, "people_latent_w_per_person": people_latent or default_latent,
                   "people_diversity_factor": people_diversity, "lighting_w_m2": lighting_w_m2,
                   "lighting_diversity_factor": lighting_diversity,
                   "outside_air_lps": outside_air,
                   "safety_factor": scenario_values["safety_factor"],
                   "envelope_not_applicable": row.get("envelope_not_applicable") is True,
                   "verification_status": "provisional", "source": _source(profile_id), "envelope_surfaces": [], "glazing_surfaces": []}
        equipment_rows = internal_record.get("equipment", []) if internal_record and internal_record.get("fields", {}).get("equipment", {}).get("origin") in {"direct_project_evidence", "contractor_override", "project_evidence", "ai_interpretation", "ai_estimated"} else []
        allowance_w = area * profile["equipment_w_m2"] * profile["equipment_space_gain"] * profile["equipment_diversity"]
        if not equipment_rows:
            equipment_rows = [{"name": "Preliminary profile equipment", "quantity": 1, "rated_input_w": area * profile["equipment_w_m2"], "heat_to_space_factor": profile["equipment_space_gain"], "diversity": profile["equipment_diversity"], "origin": "controlled_preliminary_profile"}]
        else:
            # Listed items never take a room below its typical allowance: accepting a few of a room's items used to
            # replace the allowance with just those (a shop with one 100 W item fell from about 1 kW to 0.075 kW). The
            # room takes the larger of its listed items and the allowance; the difference is a labelled top-up.
            listed_w = sum((item.get("quantity") or 1) * (item.get("rated_input_w") or 0)
                           * item.get("heat_to_space_factor", profile["equipment_space_gain"])
                           * item.get("diversity", profile["equipment_diversity"]) for item in equipment_rows)
            if listed_w < allowance_w:
                top_up_w = allowance_w - listed_w
                equipment_rows = [*equipment_rows, {
                    "name": "Typical equipment allowance top-up (listed items are below the room's typical level)", "quantity": 1,
                    "rated_input_w": round(top_up_w / (profile["equipment_space_gain"] * profile["equipment_diversity"]), 3),
                    "heat_to_space_factor": profile["equipment_space_gain"], "diversity": profile["equipment_diversity"],
                    "origin": "controlled_preliminary_profile"}]
                ledger.append({"room_id": room_id, "field": "equipment_allowance_top_up_w", "value": round(top_up_w, 1),
                               "origin": "controlled_preliminary_profile", "profile_id": profile_id, "confidence": 0.3,
                               "confidence_band": confidence_band(0.3),
                               "rationale": f"Listed equipment gives {listed_w:.0f} W to the room, below the typical allowance of "
                                            f"{allowance_w:.0f} W for {area:g} m² of {profile['label']}; topped up to the allowance.",
                               "evidence": []})
        heat_sources = [{"name": item.get("name", "Equipment"), "quantity": item.get("quantity", 1), "watts": round(item.get("rated_input_w", 0), 3), "kind": "other", "diversity_factor": item.get("diversity", profile["equipment_diversity"]), "space_gain_factor": item.get("heat_to_space_factor", profile["equipment_space_gain"]), **({"latent_w": item["latent_w"]} if item.get("latent_w") else {}), "verification_status": "provisional", "source": item.get("origin", _source(profile_id))} for item in equipment_rows]
        requirements_zones.append({"zone_id": zone_id, "name": row["name"], "usage": profile["label"], "source_room_labels": [row["name"]],
                                   "area_m2": area, "occupancy": occupancy, "ceiling_height_mm": room_resolution.get("ceiling_height_mm"),
                                   "heat_sources": heat_sources,
                                   "cooling_load": cooling})
        zones.append({"zone_id": zone_id, "name": row["name"], "floor_id": floor_id, "ceiling_height_mm": room_resolution.get("ceiling_height_mm"),
                      "verification_status": "provisional", "source": _source("topology"), "citations": []})
        ledger.extend([
            {"room_id": room_id, "field": "area_m2", "value": area, "origin": area_origin, "verification_status": row.get("area_verification_status", "provisional"), "quality_label": row.get("area_quality_label", ""), "profile_id": profile_id, "confidence": score, "confidence_band": confidence_band(score), "rationale": ("Current calibrated reviewer trace; provisional until the room area is accepted in the calculator draft." if area_origin == "reviewer_traced" else "AI-determined calibrated room outline; provisional until reviewed." if area_origin == "ai_determined" else "Normalized geometry proof when available; otherwise a direct PDF value or controlled profile fallback."), "evidence": row["evidence"], "geometry_proof_id": row.get("geometry_proof_id", ""), "reviewer_trace_id": row.get("reviewer_trace_id", ""), "geometry_mode": row.get("geometry_mode", ""), "derivation": row.get("geometry_evidence", {}).get("derivation", {})},
            {"room_id": room_id, "field": "ceiling_height_mm", "value": room_resolution.get("ceiling_height_mm"), "origin": room_resolution.get("ceiling_height_origin", "unresolved"), "profile_id": profile_id, "confidence": room_resolution.get("ceiling_height_confidence", 0.0), "confidence_band": confidence_band(room_resolution.get("ceiling_height_confidence", 0.0)), "rationale": room_resolution.get("ceiling_height_rationale", "Ceiling-height resolver."), "evidence": room_resolution.get("ceiling_height_evidence", []), "derivation": room_resolution.get("volume_derivation", {})},
            {"room_id": room_id, "field": "room_profile", "value": profile_id, "origin": "ai_profile", "profile_id": profile_id, "confidence": row["confidence"], "confidence_band": confidence_band(row["confidence"]), "rationale": "AI proposal when valid; otherwise label-based controlled profile selection.", "evidence": row["evidence"]},
            {"room_id": room_id, "field": "space_scope", "value": row["scope"], "origin": "ai_profile", "profile_id": profile_id, "confidence": row["confidence"], "confidence_band": confidence_band(row["confidence"]), "rationale": "AI preliminary scope classification.", "evidence": row["evidence"]},
        ])
        exclusions.extend({"room_id": room_id, "component": component, "reason": reason} for component, reason in (
            ("opaque envelope", "No structurally valid surface geometry and boundary area was available for the preliminary model."),
            ("glazing and façade solar", "No resolved reviewed/AI opening geometry, external exposure, orientation, and cited property set was available."),
            ("infiltration", "Preliminary profile airflow is not passed through the approved infiltration method gate."),
        ))
        if exhaust_note:
            exclusions.append({"room_id": room_id, "component": "process exhaust", "reason": exhaust_note})
        if row["scope"] == "comfort_hvac_with_process_exception":
            exclusions.extend({"room_id": room_id, "component": component, "reason": reason} for component, reason in (
                *((("process exhaust", "Kitchen process exhaust remains a project-specific preliminary exclusion."),) if not exhaust else ()),
                ("steam and named equipment", "Steam and named equipment heat remain explicit project-specific exclusions."),
            ))
    weather_values = scenario_values["weather_profile"]
    requirements_raw = {"space_usage": "AI preliminary multi-room project", "occupancy": None, "operating_hours": "Controlled preliminary profiles", "indoor_cooling_setpoint_c": scenario_values["indoor_dry_bulb_c"], "outdoor_summer_db_c": max(point["db"] for point in weather_values["hours"]),
                        "cooling_load_conditions": {"indoor_cooling_wet_bulb_c": scenario_values["indoor_wet_bulb_c"], "indoor_wet_bulb_basis": "legacy_unverified", "outdoor_summer_wet_bulb_c": max(point["wb"] for point in weather_values["hours"]), "outdoor_wet_bulb_basis": max(weather_values["hours"], key=lambda point: point["wb"]).get("wet_bulb_basis", "legacy_unverified"), "atmospheric_pressure_kpa": weather_values["pressure_kpa"], "verification_status": "provisional", "source": _source("scenario")},
                        "zones": requirements_zones,
                        "verification": {key: {"status": "provisional", "source": _source("scenario")} for key in ("occupancy", "design_conditions", "outside_air", "exhaust", "heat_sources", "ceiling", "existing_services")}}
    requirements = validate_design_requirements(requirements_raw)
    model = build_hourly_load_model(requirements)
    model["floors"], model["zones"] = floors, zones
    room_rows = {}
    def airflow_citations(record):
        """Keep cited project evidence; cite the released pack for its fallback."""
        raw = record.get("evidence", record.get("citations", [])) if isinstance(record, dict) else []
        if isinstance(raw, dict):
            raw = [raw]
        valid = [deepcopy(item) for item in raw if isinstance(item, dict) and (str(item.get("reference", "")).strip() or str(item.get("excerpt", "")).strip())]
        if valid:
            return valid
        if isinstance(record, dict) and record.get("origin") == "controlled_fallback":
            return [{"reference": f"product-owned:{pack['pack_id']}:{pack['version']}", "excerpt": "Controlled preliminary infiltration profile from the released assumption pack."}]
        return []

    for room, row in zip(model["rooms"], active_rows):
        profile_id = next(item["profile_id"] for item in ledger if item["room_id"] == f"room-{row['key']}" and item["field"] == "room_profile")
        profile = deepcopy(pack["profiles"][profile_id])
        profile.update({key: value for key, value in resolved_values["rooms"].get(value_resolver.room_target_id(row["name"], row["level"]), {}).items() if key in profile and value is not None})
        room.update({"room_id": f"room-{row['key']}", "name": row["name"], "zone_id": f"zone-{row['key']}", "source_zone_id": f"zone-{row['key']}",
                     "mapping_status": "confirmed", "verification_status": "provisional", "source": _source("topology"), "citations": [], "source_room_labels": [row["name"]], "ceiling_height_mm": resolved_values["rooms"].get(value_resolver.room_target_id(row["name"], row["level"]), {}).get("ceiling_height_mm")})
        resolved_internal = internal_records.get(room_use_resolver.room_identity(row["name"], row["level"]), {})
        internal_schedule_id = resolved_internal.get("schedule_id") or f"prelim-{profile_id}"
        room["schedule_assignments"] = {"people": internal_schedule_id, "lighting": internal_schedule_id, "outside_air": internal_schedule_id, "infiltration": "", "equipment": {source["source_id"]: internal_schedule_id for source in room["heat_sources"]}, "solar": {}}
        infiltration_record = room_airflows.get("infiltration", {})
        if infiltration_record.get("air_path_type") == "infiltration" and infiltration_record.get("status") not in {"blocked", "excluded"} and infiltration_record.get("value") is not None:
            infiltration_schedule_id = f"airflow-infiltration-{room['room_id']}"
            day_profiles = infiltration_record.get("schedule", {})
            citations = airflow_citations(infiltration_record)
            schedules.append({"schedule_id": infiltration_schedule_id, "title": "Resolved preliminary infiltration schedule", "description": "Dedicated schedule from airflow resolver; review required.", "status": "provisional", "source": infiltration_record.get("source", "airflow resolution"), "citations": citations, "day_profiles": {"weekday": {"values": day_profiles.get("weekday", [1.0] * 24), "status": "provisional", "source": infiltration_record.get("source", "airflow resolution"), "citations": citations}, "saturday": {"values": day_profiles.get("saturday", [1.0] * 24), "status": "provisional", "source": infiltration_record.get("source", "airflow resolution"), "citations": citations}, "sunday_holiday": {"values": day_profiles.get("sunday", day_profiles.get("holiday", [1.0] * 24)), "status": "provisional", "source": infiltration_record.get("source", "airflow resolution"), "citations": citations}}})
            room["schedule_assignments"]["infiltration"] = infiltration_schedule_id
        for component in room["unapproved_components"]:
            component.update({"value": None, "unit": "", "source_room_id": "", "source": "Excluded from AI preliminary model pending a project-specific method.", "citations": [], "verification_status": "provisional", "calculation_status": "not_assessed"})
            if component.get("component_type") == "infiltration" and infiltration_record.get("value") is not None and infiltration_record.get("status") not in {"blocked", "excluded"}:
                component.update({"value": infiltration_record.get("value"), "unit": infiltration_record.get("unit", "ACH"), "source": infiltration_record.get("source", "airflow resolution"), "citations": airflow_citations(infiltration_record), "verification_status": "provisional", "calculation_status": "calculated", "method_id": "infiltration_psychrometric_v1", "air_path": "uncontrolled_infiltration", "flow_reference": "outdoor_design_condition", "preliminary_assumption": infiltration_record.get("origin") == "controlled_fallback"})
        room_rows[_room_identity(row["name"], row["level"])] = {"room": room, "row": row, "profile": profile, "profile_id": profile_id}

    # A legacy vision surface remains useful as a backwards-compatible
    # proposal, but new integrations pass the richer surface/opening proposal.
    surface_candidates = list(effective_proposal["surfaces"]) or _legacy_surface_candidates(vision or {}, active_rows)
    # Normalise opaque properties through the authoritative thermal-surface
    # ledger before materialising hourly inputs.  The legacy proposal shape is
    # bridged here so existing callers keep working while new geometry
    # responses can provide a full ledger directly.
    ledger_rows = []
    geometry_ledger = (geometry_resolution or {}).get("thermal_surface_ledger", {}) if isinstance(geometry_resolution, dict) else {}
    geometry_rows = geometry_ledger.get("surfaces", []) if isinstance(geometry_ledger, dict) else []
    if geometry_rows:
        ledger_rows = deepcopy(geometry_rows)
        # A normalized geometry ledger is authoritative even when the AI
        # response did not repeat the richer preliminary ``surfaces`` shape.
        # Bridge those records into the existing materialization loop so a
        # valid ledger surface is not silently lost between resolution and
        # the hourly model.  The bridge only copies identifiers, ownership,
        # geometry, and provenance; construction/U-values remain resolved by
        # ``resolve_opaque_envelope`` below.
        surface_candidates = []
        for surface in geometry_rows:
            if not isinstance(surface, dict):
                continue
            owner_id = str(surface.get("owner_room_id") or "")
            owner = next((value for value in active_rows
                          if owner_id in {str(value.get("id", "")), str(value.get("source_room_id", "")), f"room-{value.get('key', '')}", value.get("key", "")}
                          or (surface.get("owner_room_label") and value["name"].casefold() == str(surface["owner_room_label"]).casefold())), None)
            level_name = str(surface.get("level_name") or (owner or {}).get("level", ""))
            owner_label = str(surface.get("owner_room_label") or (owner or {}).get("name", ""))
            score = surface.get("confidence_score")
            if score is None:
                score = _confidence(surface.get("confidence"))
            candidate_id = str(surface.get("surface_id") or surface.get("ai_surface_id") or "")
            if not candidate_id:
                continue
            surface_candidates.append({
                "candidate_id": candidate_id,
                "surface_key": candidate_id,
                "label": surface.get("label", candidate_id),
                "owner_room_label": owner_label,
                "owner_level_name": level_name,
                "physical_type": surface.get("physical_type", "wall"),
                "thermal_role": surface.get("thermal_role", "unresolved"),
                "external_exposure": "external" if surface.get("boundary_condition") == "outside" or surface.get("thermal_role") == "external" else surface.get("external_exposure", ""),
                "boundary_condition": surface.get("boundary_condition", "unresolved"),
                "orientation": str(surface.get("orientation", "")).upper(),
                "gross_area_m2": surface.get("gross_area_m2", surface.get("area_m2")),
                "net_opaque_area_m2": surface.get("net_opaque_area_m2"),
                "opening_coverage": surface.get("opening_coverage_status", surface.get("opening_coverage", "not_applicable")),
                "opening_ids": list(surface.get("linked_opening_ids", surface.get("opening_ids", []))),
                "construction_id": surface.get("construction_id", ""),
                "u_value_w_m2k": surface.get("u_value_w_m2k"),
                "boundary_temperature_c": surface.get("boundary_temperature_c"),
                "evidence": deepcopy(surface.get("evidence_refs", surface.get("citations", []))),
                "confidence": score,
                "confidence_band": confidence_band(score),
                "assumptions": deepcopy(surface.get("assumptions", [])),
                "conflicts": deepcopy(surface.get("conflicts", [])),
                "validation_errors": deepcopy(surface.get("unresolved_fields", [])),
            })
        # Reviewer-trace surfaces are independently sourced declarations. Keep
        # them alongside the normalized ledger records even when that ledger
        # is otherwise authoritative for AI/PDF surface proposals.
        for surface in effective_proposal.get("surfaces", []):
            if not isinstance(surface, dict) or not surface.get("reviewer_trace_id"):
                continue
            owner = _find_owner(active_rows, surface.get("owner_room_label", ""), surface.get("owner_level_name", ""))
            target = room_rows.get(_room_identity(owner["name"], owner["level"]), {}) if owner else {}
            candidate_id = str(surface.get("candidate_id", ""))
            if not candidate_id or not target:
                continue
            candidate = deepcopy(surface)
            candidate["owner_room_id"] = target["room"].get("room_id", "")
            surface_candidates.append(candidate)
            fixed_adjacent = surface.get("thermal_role") == "fixed_adjacent"
            ledger_rows.append({
                "surface_id": candidate_id,
                "physical_type": surface.get("physical_type", "wall"),
                "thermal_role": "fixed_adjacent" if fixed_adjacent else "external",
                "boundary_condition": surface.get("boundary_condition", "unconditioned_space") if fixed_adjacent else "outside",
                "owner_room_id": target["room"].get("room_id", ""),
                "owner_zone_id": target["room"].get("zone_id", ""),
                "gross_area_m2": surface.get("gross_area_m2"),
                "net_opaque_area_m2": None,
                "opening_coverage_status": "not_applicable",
                "linked_opening_ids": [],
                "construction_id": surface.get("construction_id", ""),
                "u_value_w_m2k": surface.get("u_value_w_m2k"),
                "construction_source": surface.get("construction_source", ""),
                "boundary_temperature_c": surface.get("boundary_temperature_c") if fixed_adjacent else None,
                "evidence_refs": deepcopy(surface.get("evidence", [])),
                "confidence": surface.get("confidence", 0.65),
                "confidence_score": surface.get("confidence", 0.65),
                "assumptions": deepcopy(surface.get("assumptions", [])),
                "status": "proposed",
                "area_basis": "reviewer_traced_boundary",
                "area_derivation": {"formula": "trace_edge_length_m × ceiling_height_m" if surface.get("physical_type") == "wall" else "reviewer_traced_footprint_area_m2",
                                    "reviewer_trace_id": surface.get("reviewer_trace_id")},
            })
    else:
        for candidate in surface_candidates:
            owner = _find_owner(active_rows, candidate.get("owner_room_label", ""), candidate.get("owner_level_name", ""))
            owner_id = ""
            if owner:
                owner_id = room_rows.get(_room_identity(owner["name"], owner["level"]), {}).get("room", {}).get("room_id", "")
            ledger_rows.append({
                "surface_id": candidate.get("candidate_id", candidate.get("surface_key", "")),
                "physical_type": candidate.get("physical_type", "wall"),
                "thermal_role": candidate.get("thermal_role", "external"),
                "boundary_condition": "outside" if candidate.get("external_exposure") == "external" else candidate.get("boundary_condition", "unresolved"),
                "owner_room_id": owner_id,
                "owner_zone_id": room_rows.get(_room_identity(owner["name"], owner["level"]), {}).get("room", {}).get("zone_id", "") if owner else "",
                "gross_area_m2": candidate.get("gross_area_m2"),
                "net_opaque_area_m2": candidate.get("net_opaque_area_m2"),
                "opening_coverage": candidate.get("opening_coverage", "not_applicable"),
                "opening_ids": [item.get("candidate_id") for item in effective_proposal.get("openings", []) if isinstance(item, dict) and item.get("host_surface_key") in {candidate.get("surface_key"), candidate.get("candidate_id")}],
                "construction_id": candidate.get("construction_id", ""),
                "u_value_w_m2k": candidate.get("u_value_w_m2k"),
                "boundary_temperature_c": candidate.get("boundary_temperature_c"),
                "evidence_refs": candidate.get("evidence", []),
                "confidence": candidate.get("confidence", "low"),
                "status": "ai_estimated" if candidate.get("confidence", 0) >= 0.8 else "proposed",
            })
    opening_rows = []
    for opening in effective_proposal.get("openings", []):
        if isinstance(opening, dict):
            opening_rows.append({"opening_id": opening.get("candidate_id"), "opening_area_m2": opening.get("opening_area_m2"), "width_m": opening.get("width_m"), "height_m": opening.get("height_m"), "quantity": opening.get("quantity", 1)})
    opaque_resolution = thermal_surface_resolver.resolve_opaque_envelope(
        {"source_fingerprint": fingerprint(effective_proposal), "surfaces": ledger_rows},
        {"openings": opening_rows}, value_resolution=resolution_artifact,
        source_pack=pack, resolution_mode="preliminary_ai_estimate",
        weather_available=bool(scenario_values.get("weather_profile")),
    )
    opaque_by_id = {row.get("surface_id"): row for row in opaque_resolution.get("surfaces", [])}
    for candidate in surface_candidates:
        resolved = opaque_by_id.get(candidate.get("candidate_id"))
        if resolved:
            candidate["gross_area_m2"] = resolved.get("gross_area_m2", candidate.get("gross_area_m2"))
            candidate["net_opaque_area_m2"] = resolved.get("net_opaque_area_m2", candidate.get("net_opaque_area_m2"))
            candidate["resolved_u_value_w_m2k"] = resolved.get("u_value_w_m2k")
            candidate["resolved_construction_id"] = resolved.get("construction_id")
            candidate["opaque_resolution_status"] = resolved.get("status")
            candidate["opaque_resolution_issues"] = resolved.get("unresolved_fields", [])
    opening_candidates = effective_proposal["openings"]
    surface_index, accepted_surface_ids, accepted_opening_ids = {}, set(), set()
    surface_summary = {"discovered": len(surface_candidates), "included": 0, "blocked": 0, "excluded": 0,
                       "openings_discovered": len(opening_candidates), "openings_included": 0, "openings_excluded": 0}
    for issue in effective_proposal["issues"]:
        exclusions.append({"room_id": "", **issue})
    for candidate in surface_candidates:
        owner = _find_owner(active_rows, candidate.get("owner_room_label", ""), candidate.get("owner_level_name", ""))
        errors = list(candidate.get("validation_errors", []))
        errors.extend(candidate.get("opaque_resolution_issues", []))
        if owner is None:
            errors.append("owner is not an included comfort-HVAC room")
        if errors:
            surface_summary["blocked"] += 1
            exclusions.append({"room_id": f"room-{owner['key']}" if owner else "", "component": "opaque envelope", "reason": "; ".join(errors), "candidate_id": candidate.get("candidate_id", "")})
            continue
        # Preserve the complete ledger, but keep the existing preliminary
        # hourly adapter conservative: floors, partitions, ground-contact,
        # room-to-room, and unresolved boundaries need their reviewed coupling
        # or boundary method before they can contribute to draft conduction.
        # A reviewer-traced wall to an unconditioned space, with the operators' answered temperature beyond it, is
        # conducted against that temperature (no sun).
        fixed_adjacent = (candidate.get("physical_type") == "wall" and candidate.get("thermal_role") == "fixed_adjacent"
                          and candidate.get("reviewer_trace_id") and _number(candidate.get("boundary_temperature_c")) is not None)
        if not fixed_adjacent and (candidate.get("physical_type") not in {"wall", "roof", "ceiling"}
                or candidate.get("thermal_role") not in {"external", "outside", "outdoors"}
                or candidate.get("external_exposure") != "external"):
            surface_summary["excluded"] += 1
            exclusions.append({"room_id": f"room-{owner['key']}", "component": "opaque envelope", "candidate_id": candidate.get("candidate_id", ""),
                               "reason": "Surface is retained in the thermal ledger but its non-external boundary is outside the preliminary hourly envelope policy."})
            continue
        target = room_rows[_room_identity(owner["name"], owner["level"])]
        candidate = deepcopy(candidate)
        candidate["owner_room_id"] = target["room"]["room_id"]
        surface_index[str(candidate.get("surface_key", candidate["candidate_id"]))] = candidate
    openings_by_host = {}
    for candidate in opening_candidates:
        owner = _find_owner(active_rows, candidate.get("owner_room_label", ""), candidate.get("owner_level_name", ""))
        errors = list(candidate.get("validation_errors", []))
        host = surface_index.get(candidate.get("host_surface_key", ""))
        if owner is None:
            errors.append("owner is not an included comfort-HVAC room")
        if host is None:
            errors.append("host surface is unresolved or excluded")
        elif owner and host.get("owner_room_id") != room_rows[_room_identity(owner["name"], owner["level"])]["room"]["room_id"]:
            errors.append("host surface belongs to another room")
        if candidate.get("external_exposure") == "internal":
            errors.append("internal glazing has no external conduction or façade solar")
        if errors:
            surface_summary["openings_excluded"] += 1
            exclusions.append({"room_id": f"room-{owner['key']}" if owner else "", "component": "glazing and façade solar", "reason": "; ".join(errors), "candidate_id": candidate.get("candidate_id", "")})
            continue
        openings_by_host.setdefault(candidate["host_surface_key"], []).append(candidate)

    envelope = pack["preliminary_envelope"]
    for host_key, candidate in surface_index.items():
        target = next(value for value in room_rows.values() if value["room"]["room_id"] == candidate["owner_room_id"])
        room, row, profile, profile_id = target["room"], target["row"], target["profile"], target["profile_id"]
        openings = openings_by_host.get(host_key, [])
        gross = candidate["gross_area_m2"]
        net = candidate.get("net_opaque_area_m2")
        if openings and candidate.get("opening_coverage") not in {"complete", "reviewer_entered"}:
            surface_summary["blocked"] += 1
            exclusions.append({"room_id": room["room_id"], "component": "opaque envelope", "candidate_id": candidate["candidate_id"],
                               "reason": "Opening coverage is incomplete; opaque host area is excluded to prevent wall/window double counting."})
            continue
        if openings:
            net = gross - sum(item["opening_area_m2"] for item in openings)
        net = net if net is not None else gross
        if net <= 0:
            surface_summary["blocked"] += 1
            exclusions.append({"room_id": room["room_id"], "component": "opaque envelope", "candidate_id": candidate["candidate_id"], "reason": "Net opaque area is not positive after opening coverage."})
            continue
        physical = candidate["physical_type"]
        fixed_adjacent = candidate.get("thermal_role") == "fixed_adjacent"
        orientation = ("" if fixed_adjacent else candidate.get("orientation", "") if candidate.get("orientation", "") in CARDINALS
                       else "horizontal" if physical in {"roof", "ceiling"} else "")
        solar_profile = envelope["cardinal_solar_profiles_w_m2"].get(orientation, [0] * 24)
        solar_peak = max(solar_profile)
        solar_schedule_id = f"prelim-solar-{candidate['candidate_id']}"
        if solar_peak:
            operating = _profile_schedule(profile_id)
            schedules.append(_schedule_with_values(solar_schedule_id, f"Preliminary {orientation} façade solar profile", [round(operating[hour] * solar_profile[hour] / solar_peak, 6) for hour in range(24)], _source(f"solar-{orientation}")))
            room["schedule_assignments"]["solar"][candidate["candidate_id"]] = solar_schedule_id
        opaque = {"surface_id": candidate["candidate_id"], "kind": "roof" if physical == "roof" else "ceiling" if physical == "ceiling" else "opaque_wall",
                  "orientation": orientation, "area_m2": net, "u_value_w_m2k": profile["roof_u_w_m2k"] if physical in {"roof", "ceiling"} else profile["wall_u_w_m2k"],
                  "solar_design_w_m2": solar_peak, "solar_gain_factor": envelope["opaque_solar_gain_factor"] if solar_peak else 0,
                  "shading_factor": 1, "boundary_method": "external", "boundary_temperature_c": None, "construction_id": "",
                  "verification_status": "provisional",
                  "source": (_source(profile_id) + "; reviewer-declared boundary" if candidate.get("reviewer_trace_id")
                             else _source(profile_id) + "; AI preliminary surface classification"),
                  "preliminary_assumption": True, "source_pages": candidate["evidence"], "orientation": orientation}
        if candidate.get("reviewer_trace_id"):
            opaque.update({"reviewer_trace_id": candidate.get("reviewer_trace_id"),
                           "reviewer": candidate.get("reviewer", ""),
                           "boundary_rationale": candidate.get("rationale", "Reviewer-declared boundary."),
                           "provenance_status": "provisional"})
        if candidate.get("resolved_u_value_w_m2k") is not None:
            opaque["u_value_w_m2k"] = candidate["resolved_u_value_w_m2k"]
        if candidate.get("resolved_construction_id"):
            opaque["construction_id"] = candidate["resolved_construction_id"]
        if candidate.get("construction_source"):          # the operators' answered construction, not the pack's U-value
            opaque["source"] = candidate["construction_source"]
        if fixed_adjacent:
            opaque.update({"boundary_method": "fixed_adjacent_temperature", "boundary_temperature_c": float(candidate["boundary_temperature_c"]),
                           "boundary_source": candidate.get("boundary_source", "")})
        opaque["area_derivation"] = deepcopy(opaque_by_id.get(candidate["candidate_id"], {}).get("area_derivation", {}))
        opaque["opaque_resolution_fingerprint"] = opaque_by_id.get(candidate["candidate_id"], {}).get("resolution_fingerprint", "")
        room["cooling_load"]["envelope_not_applicable"] = False
        room["cooling_load"]["envelope_surfaces"].append(opaque)
        accepted_surface_ids.add(candidate["candidate_id"])
        surface_summary["included"] += 1
        exclusions[:] = [item for item in exclusions if not (item.get("room_id") == room["room_id"] and item.get("component") == "opaque envelope" and "No structurally valid" in item.get("reason", ""))]
        ledger.append({"room_id": room["room_id"], "field": "envelope_surface", "value": net, "origin": "ai_geometry", "profile_id": profile_id,
                       "confidence": candidate["confidence"], "confidence_band": candidate["confidence_band"],
                       "rationale": candidate.get("rationale", "AI preliminary external surface; controlled profile U-value and solar basis."),
                       "evidence": candidate["evidence"], "surface_id": candidate["candidate_id"],
                       "reviewer_trace_id": candidate.get("reviewer_trace_id", ""),
                       "reviewer": candidate.get("reviewer", "")})
        for opening in openings:
            shade = envelope["shading_categories"][opening["shading_category"]]
            opening_orientation = opening.get("orientation") if opening.get("orientation") in CARDINALS else orientation if orientation in CARDINALS else ""
            glazing_profile = envelope["cardinal_solar_profiles_w_m2"].get(opening_orientation, [0] * 24)
            glazing_peak = max(glazing_profile)
            solar_schedule_id = f"prelim-solar-{opening['candidate_id']}"
            if glazing_peak:
                operating = _profile_schedule(profile_id)
                schedules.append(_schedule_with_values(solar_schedule_id, f"Preliminary {opening_orientation} glazing solar profile", [round(operating[hour] * glazing_profile[hour] / glazing_peak, 6) for hour in range(24)], _source(f"solar-{opening_orientation}")))
                room["schedule_assignments"]["solar"][opening["candidate_id"]] = solar_schedule_id
            # Glass performance answered by the operators replaces the preliminary pack values (bounded, else ignored).
            answered_u = _number(opening.get("u_value_w_m2k")) if 0.5 <= (_number(opening.get("u_value_w_m2k")) or 0) <= 7 else None
            answered_shgc = _number(opening.get("shgc")) if 0.05 <= (_number(opening.get("shgc")) or 0) <= 0.95 else None
            glazing = {"surface_id": opening["candidate_id"], "owner_room_id": room["room_id"], "owner_zone_id": room["zone_id"],
                       "host_surface_id": candidate["candidate_id"], "opening_mapping_status": "proposed", "geometry_mode": "preliminary_ai_estimate",
                       "review_status": "provisional", "verification_status": "provisional", "boundary_method": "external", "external_exposure": "external",
                       "explicit_opening_area_m2": opening["opening_area_m2"], "explicit_glass_area_m2": opening.get("explicit_glass_area_m2"),
                       "window": {"record_id": f"window-{opening['candidate_id']}", "u_value_w_m2k": answered_u or pack["profiles"].get(opening.get("glazing_choice"), profile)["glazing_u_w_m2k"], "shgc": answered_shgc or pack["profiles"].get(opening.get("glazing_choice"), profile)["shgc"],
                                  "frame_fraction": envelope["glazing_frame_fraction"], "glass_area_correction": envelope["glazing_glass_area_correction"],
                                  "internal_shading_factor": envelope["glazing_internal_shading_factor"]},
                       "manual_solar": {"enabled": bool(glazing_peak), "incident_solar_w_m2": glazing_peak, "external_shading_factor": shade},
                       "solar_basis": "ai_preliminary_cardinal_profile", "preliminary_solar_profile_w_m2": glazing_profile,
                       "orientation": opening_orientation, "shading_category": opening["shading_category"], "preliminary_assumption": True,
                       "source": (opening.get("glazing_source") if answered_u and answered_shgc else
                                  _source(opening.get("glazing_choice") or profile_id) + "; preliminary assumption-pack glazing U-value and SHGC"),
                       "citations": [], "source_pages": opening["evidence"]}
            room["cooling_load"]["glazing_surfaces"].append(glazing)
            accepted_opening_ids.add(opening["candidate_id"])
            surface_summary["openings_included"] += 1
            exclusions[:] = [item for item in exclusions if not (item.get("room_id") == room["room_id"] and item.get("component") == "glazing and façade solar" and "No resolved" in item.get("reason", ""))]
            if not opening_orientation:
                exclusions.append({"room_id": room["room_id"], "component": "Glazing sun — orientation not assessed", "candidate_id": opening["candidate_id"], "reason": "Glazing conduction is included; glazing solar gain is omitted because page north is not declared."})
            if opening["shading_category"] == "unshaded":
                exclusions.append({"room_id": room["room_id"], "component": "shading review", "candidate_id": opening["candidate_id"], "reason": "Unknown façade shading uses the explicit conservative unshaded preliminary assumption."})
            ledger.append({"room_id": room["room_id"], "field": "glazing_opening", "value": opening["opening_area_m2"], "origin": "ai_geometry", "profile_id": profile_id,
                           "confidence": opening["confidence"], "confidence_band": opening["confidence_band"], "rationale": "AI preliminary glazing with controlled U-value, SHGC, directional solar, and shading category.", "evidence": opening["evidence"], "surface_id": opening["candidate_id"]})
    for assessment in effective_proposal.get("envelope_assessments", []):
        if not isinstance(assessment, dict):
            continue
        owner = _find_owner(active_rows, assessment.get("owner_room_label", ""), assessment.get("owner_level_name", ""))
        target = room_rows.get(_room_identity(owner["name"], owner["level"]), {}) if owner else {}
        if not target:
            continue
        room_id = target["room"].get("room_id", "")
        if assessment.get("envelope_not_applicable"):
            target["room"].setdefault("cooling_load", {})["envelope_not_applicable"] = True
            exclusions[:] = [item for item in exclusions if not (
                item.get("room_id") == room_id
                and ((item.get("component") == "opaque envelope"
                      and "No structurally valid surface geometry" in item.get("reason", ""))
                     or (item.get("component") == "glazing and façade solar"
                         and "No resolved reviewed/AI opening geometry" in item.get("reason", "")))
            )]
        for item in assessment.get("not_assessed", []):
            if isinstance(item, dict):
                exclusions.append({"room_id": room_id, "component": item.get("component", "opaque envelope"),
                                   "component_id": item.get("component_id", "envelope"), "reason": item.get("reason", "Envelope item was not assessed."),
                                   "page": item.get("page", assessment.get("page")),
                                   "reviewer_trace_id": assessment.get("trace_id", ""),
                                   "reviewer": assessment.get("reviewer", ""), "envelope_not_assessed": True})
        for item in assessment.get("excluded", []):
            if isinstance(item, dict):
                exclusions.append({"room_id": room_id, "component": item.get("component", "Envelope boundary"),
                                   "component_id": item.get("component_id", "envelope_boundary"),
                                   "reason": item.get("reason", "This boundary is not part of the room's external envelope."),
                                   "page": item.get("page", assessment.get("page")),
                                   "reviewer_trace_id": assessment.get("trace_id", ""),
                                   "reviewer": item.get("reviewer", assessment.get("reviewer", "")),
                                   "source": item.get("boundary_source", ""), "envelope_exclusion": True})
    surface_summary["excluded"] = surface_summary["discovered"] - surface_summary["included"] - surface_summary["blocked"]
    model["updated_at"] = now()
    model["source_requirements_updated_at"] = requirements["updated_at"]
    material = {"requirements": requirements, "schedule_library": {"schema_version": 1, "updated_at": now(), "schedules": schedules}, "design_day_scenarios": _scenario(pack, scenario_values, conditions_basis, site_days), "hourly_load_model": model,
                "preliminary_policy": {"pack_version": pack["version"], "mode": "ai_preliminary", "surface_ids": sorted(accepted_surface_ids), "opening_ids": sorted(accepted_opening_ids)}}
    dependency_fingerprints = dict(source_fingerprints or {})
    dependency_fingerprints.update({"preliminary_pack": fingerprint(pack), "room_use_taxonomy": room_use_artifact["taxonomy_fingerprint"], "room_use_resolution": room_use_artifact["fingerprint"], "ceiling_volume_resolution": ceiling_artifact["fingerprint"], "internal_gains_resolution": internal_artifact.get("fingerprint", ""), "airflow_resolution": airflow_artifact.get("fingerprint", ""), "ahu_resolution": ahu_artifact.get("fingerprint", ""), "plant_resolution": plant_artifact.get("fingerprint", ""), "geometry_resolution": fingerprint(geometry_resolution or {}), "thermal_surface_ledger": opaque_resolution.get("fingerprint", ""), "vision_response": fingerprint(vision or {}), "building_evidence": fingerprint(building or {}), "contractor_overrides": fingerprint(overrides), "process_exhaust": fingerprint(process_exhaust),
                                     # Prefer fingerprints of the source records used by
                                     # stale-state checks; the normalized/effective proposal
                                     # below is materialized data, not the source artifact.
                                     "manual_placeholder_entities": dependency_fingerprints.get("manual_placeholder_entities", fingerprint(manual_entities)),
                                     "ai_preliminary_proposal": dependency_fingerprints.get("ai_preliminary_proposal", fingerprint(preliminary_proposal if preliminary_proposal is not None else manual_entities)),
                                     "value_resolution": resolution_artifact["fingerprint"]})
    input_fingerprint = content_fingerprint({"material": material, "ledger": ledger, "exclusions": exclusions, "dependencies": dependency_fingerprints})
    review_queue = [item for item in ledger if item["confidence"] < 0.8]
    # High-confidence draft geometry may be usable after deterministic shape
    # and scale validation, while its level/vector/dimension caveats still need
    # to remain visible to the contractor. Keep these warnings out of the
    # blocking list but never hide them from the review queue.
    for entity in (geometry_resolution or {}).get("entities", []):
        if not isinstance(entity, dict) or entity.get("kind") != "ai_room_geometry":
            continue
        warnings = entity.get("review_warnings", [])
        if warnings:
            review_queue.append({
                "room_id": entity.get("entity_id", ""), "field": "area_m2",
                "value": (entity.get("value") or {}).get("area_m2"),
                "origin": "ai_geometry", "profile_id": "", "confidence": (entity.get("value") or {}).get("confidence_score", 0),
                "confidence_band": "medium", "rationale": "Draft area is scale-calibrated, but geometry review warnings remain.",
                "review_warnings": warnings, "evidence": [entity.get("source", {})],
            })
    return {"schema_version": 1, "status": "draft", "label": "AI preliminary estimate — not engineering reviewed or validated", "created_at": now(), "input_fingerprint": input_fingerprint,
            "pack": {"pack_id": pack["pack_id"], "version": pack["version"], "fingerprint": fingerprint(pack)}, "dependency_fingerprints": dependency_fingerprints,
            "material": material, "materialized_fields": ledger, "resolved_value_records": resolution_artifact["records"], "exclusions": exclusions, "excluded_spaces": excluded_spaces,
            "surface_summary": surface_summary, "design_conditions_basis": conditions_basis, "proposal": effective_proposal, "opaque_envelope_resolution": opaque_resolution, "room_use_resolution": room_use_artifact, "ceiling_volume_resolution": ceiling_artifact, "internal_gains_resolution": internal_artifact, "airflow_resolution": airflow_artifact, "ahu_resolution": ahu_artifact, "plant_resolution": plant_artifact, "geometry_resolution": deepcopy(geometry_resolution or {}), "value_resolution": resolution_artifact,
            "review_queue": sorted(review_queue, key=lambda item: (item["confidence"], item["room_id"], item["field"]))}


def _with_fan_heat(peak, fan, safety_factor):
    """A peak with the supply fan's heat added: a percentage of the room sensible heat (the sensible load without
    outside air), and the safety allowance and design total recalculated."""
    if not peak or not fan:
        return peak, 0.0
    components = peak.get("components") or {}
    outside = (components.get("outside_air") or {}).get("sensible_kw") or 0.0
    room_sensible = max(0.0, (peak.get("sensible_kw") or 0.0) - outside)
    heat = round(room_sensible * fan["percent_of_room_sensible"] / 100, 4)
    raw = round((peak.get("raw_coincident_total_kw", peak.get("total_kw")) or 0.0) + heat, 4)
    factor = safety_factor if isinstance(safety_factor, (int, float)) and safety_factor > 0 else (peak.get("safety_factor") or 1.0)
    updated = {**peak, "components": {**components, "fan_heat": {"sensible_kw": heat, "latent_kw": 0.0, "total_kw": heat}},
               "sensible_kw": round((peak.get("sensible_kw") or 0.0) + heat, 4), "total_kw": round((peak.get("total_kw") or 0.0) + heat, 4),
               "raw_coincident_total_kw": raw}
    if peak.get("final_design_total_kw") is not None:
        updated.update({"safety_allowance_kw": round(raw * (factor - 1), 4), "final_design_total_kw": round(raw * factor, 4),
                        "design_total_kw": round(raw * factor, 4)})
    return updated, heat


def calculate(input_set, safety_factor_policy=None):
    material = input_set["material"]
    # The cooling load is sized on the design day ("ai_preliminary_cooling_day": with a site's design days, the design
    # dry bulb with its coincident wet bulb, the usual comfort basis). The humid design day (design wet bulb with its
    # coincident dry bulb) is calculated alongside as a dehumidification check and reported, not used for the size: on
    # Butcher Buffet the dry-bulb day gave 38.8 kW before the safety factor against the engineer's 39.6 kW, the humid
    # day 56.4 kW, nearly all of the difference outside-air moisture.
    report = calculate_hourly_load_report(material["requirements"], material["schedule_library"], material["design_day_scenarios"], material["hourly_load_model"], ["ai_preliminary_cooling_day"], preliminary_policy=material.get("preliminary_policy"), safety_factor_policy=safety_factor_policy)
    fan = load_pack().get("fan_heat")
    report["included_scope_peak"], fan_kw = _with_fan_heat(report.get("included_scope_peak") or {}, fan, report.get("safety_factor"))
    if fan_kw:
        peak = report["included_scope_peak"]
        report.update({key: peak[key] for key in ("raw_coincident_total_kw", "safety_allowance_kw", "final_design_total_kw") if key in peak})
        report["fan_heat"] = {"kw": fan_kw, **fan}
        report["excluded_components"] = sorted(set(report.get("excluded_components", []) + ["supply duct heat gain"]))
        report["known_exclusions"] = [*report.get("known_exclusions", []), {
            "room_id": "", "component_id": "duct_heat_gain", "component_type": "duct_heat",
            "component": "Supply duct heat gain (matters where supply ducts run through an unconditioned roof space)",
            "reason": "Not calculated: it depends on the duct route and insulation."}]
    humid = next((row for row in material["design_day_scenarios"].get("scenarios", []) if row.get("scenario_id") == "ai_preliminary_humid_day"), None)
    if humid:
        check = calculate_hourly_load_report(material["requirements"], material["schedule_library"], material["design_day_scenarios"], material["hourly_load_model"], ["ai_preliminary_humid_day"], preliminary_policy=material.get("preliminary_policy"), safety_factor_policy=safety_factor_policy)
        peak, _fan = _with_fan_heat(check.get("included_scope_peak") or {}, fan, check.get("safety_factor"))
        report["humid_day_check"] = {"scenario_id": "ai_preliminary_humid_day", "title": humid.get("title", ""),
                                     "raw_total_kw": peak.get("raw_coincident_total_kw", peak.get("total_kw")),
                                     "final_design_total_kw": peak.get("final_design_total_kw", peak.get("design_total_kw")),
                                     "latent_kw": peak.get("latent_kw"), "hour": peak.get("hour"),
                                     "outside_air_kw": (peak.get("components", {}).get("outside_air") or {}).get("total_kw")}
    report["report_type"] = "hourly_ai_preliminary_cooling_load"
    report["status"] = "draft"
    report["project_peak"] = {}
    report["label"] = "AI preliminary estimate — not engineering reviewed or validated"
    report["preliminary_input_set_fingerprint"] = input_set["input_fingerprint"]
    report["assumption_coverage"] = {"field_count": len(input_set["materialized_fields"]), "low_confidence_count": len(input_set["review_queue"]), "excluded_component_count": len(input_set["exclusions"])}
    report["preliminary_surface_summary"] = deepcopy(input_set.get("surface_summary", {}))
    report["design_conditions_basis"] = deepcopy(input_set.get("design_conditions_basis")
                                                 or design_conditions_basis(input_set.get("value_resolution", {})))
    report["value_resolution"] = deepcopy(input_set.get("value_resolution", {}))
    report["safety_factor_resolution"] = deepcopy(input_set.get("safety_factor_resolution", {}))
    report["value_resolution_coverage"] = deepcopy(input_set.get("value_resolution", {}).get("coverage_summary", {}))
    report["refrigeration_process_exclusions"] = deepcopy(input_set.get("excluded_spaces", []))
    report["review_queue"] = deepcopy(input_set["review_queue"])
    report["provenance"] = shared_resolution.build_report_provenance(report, input_set.get("value_resolution", {}))
    report["excluded_components"] = sorted(set(report.get("excluded_components", []) + [item["component"] for item in input_set["exclusions"]]))
    room_names = {room.get("room_id"): room.get("name", "")
                  for room in material.get("hourly_load_model", {}).get("rooms", []) if isinstance(room, dict)}
    coverage_rows = report.get("scope_summary", {}).get("room_input_coverage", [])
    coverage_by_id = {row.get("room_id"): row for row in coverage_rows if isinstance(row, dict)}
    for item in input_set.get("exclusions", []):
        if not isinstance(item, dict) or not item.get("room_id"):
            continue
        if item.get("envelope_not_assessed"):
            record = {"room_id": item["room_id"], "room_name": room_names.get(item["room_id"], ""),
                      "component_id": item.get("component_id", "envelope"), "component_type": "envelope",
                      "component": item.get("component", "Envelope item not assessed"),
                      "value": None, "unit": "", "source": "Reviewer-declared boundary",
                      "citations": ([{"page": item["page"], "reference": f"Reviewer trace {item.get('reviewer_trace_id', '')}",
                                      "reviewer": item.get("reviewer", "")}] if item.get("page") else []),
                      "verification_status": "provisional", "reason": item.get("reason", "Envelope item was not assessed."),
                      "reviewer_trace_id": item.get("reviewer_trace_id", "")}
            if not any(row.get("room_id") == record["room_id"] and row.get("component_id") == record["component_id"]
                       for row in report.get("unresolved_room_inputs", []) if isinstance(row, dict)):
                report.setdefault("unresolved_room_inputs", []).append(record)
            row = coverage_by_id.get(record["room_id"])
            if row is not None:
                row.setdefault("not_assessed", []).append(record)
                row["status"] = "incomplete"
            report.setdefault("scope_summary", {})["complete_scope"] = False
        elif item.get("envelope_exclusion"):
            report.setdefault("known_exclusions", []).append({
                "room_id": item["room_id"], "component_id": item.get("component_id", "envelope"),
                "component": item.get("component", "Envelope boundary"),
                "component_type": "envelope", "reason": item.get("reason", "Envelope item is outside the preliminary method scope."),
                "source": item.get("reviewer") or "Reviewer-declared boundary",
                "reviewer_trace_id": item.get("reviewer_trace_id", ""),
            })
    warning = "Envelope and listed air-side loads were not assessed; the total excludes them and understates the load."
    if report.get("unresolved_room_inputs") and warning not in report["warnings"]:
        report["warnings"].append(warning)
    report["input_fingerprints"].update(input_set["dependency_fingerprints"])
    report["input_fingerprints"]["ai_preliminary_input_set"] = input_set["input_fingerprint"]
    return report
