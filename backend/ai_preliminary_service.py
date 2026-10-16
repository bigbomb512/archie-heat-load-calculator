"""Project-local orchestration for the draft-only AI preliminary cooling path."""

import json
import threading
import math
import os
import re
import tempfile
from copy import deepcopy
from pathlib import Path

from ai import design_weather
from ai import ai_preliminary
from ai import reviewer_room_geometry
from ai import value_resolution as value_resolver
from ai import room_use_resolution
from ai import room_scope_confirmation
from ai import ceiling_volume_resolution
from ai import internal_gains_resolution
from ai import airflow_resolution
from ai import ahu_resolution
from ai import plant_resolution
from ai import safety_factor_resolution
from ai.geometry_resolution import build_geometry_resolution
from ai.research_cache import empty_research_cache, validate_cache
from backend import productization, weather_pick_service
from backend.room_proposal import room_proposal


def _read(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep the automatic local room proposal when provider-status updates are
    # written later in the same run. The proposal is an input artifact, not a
    # provider response, and must not disappear just because no provider is
    # configured.
    if path.name == "ai_preliminary_run.json" and path.exists() and isinstance(value, dict):
        previous = _read(path, {})
        if isinstance(previous, dict):
            value = {**previous, **value}
    staged = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".stage", delete=False
        ) as handle:
            staged = Path(handle.name)
            json.dump(value, handle, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staged, path)
    finally:
        if staged is not None and staged.exists():
            staged.unlink(missing_ok=True)


def _paths(project):
    root = Path(project["review_dir"])
    return {
        "root": root,
        "settings": root / "ai_preliminary_settings.json",
        "run": root / "ai_preliminary_run.json",
        "model": root / "ai_preliminary_model.json",
        "sets": root / "ai_preliminary_input_sets",
        "current_set": root / "ai_preliminary_input_set.json",
        "report": root / "hourly_ai_preliminary_load_report.json",
        "base_report": root / "phase1_base_room_load_report.json",
        "codex_handoff": root / "codex_preliminary_handoff.json",
        "codex_response": root / "codex_preliminary_response.json",
        "value_resolution": root / "value_resolution.json",
        "site_location": root / "site_location_resolution.json",
        "site_design_weather": root / "site_design_weather_resolution.json",
        "room_use": root / "room_use_resolution.json",
        "ceiling_volume": root / "ceiling_volume_resolution.json",
        "internal_gains": root / "internal_gains_resolution.json",
        "airflow": root / "airflow_resolution.json",
        "ahu_resolution": root / "ahu_resolution.json",
        "plant_resolution": root / "plant_resolution.json",
        "safety_factor_resolution": root / "safety_factor_resolution.json",
        "thermal_surface_resolution": root / "thermal_surface_resolution.json",
        "research_cache": root / "research_cache.json",
        "building": root / "building_evidence.json",
        "vision": root / "vision_response.json",
        "fusion": root / "architect_evidence_fusion.json",
        "room_scope": root / "room_scope_confirmation.json",
    }


def _sources(paths):
    run = _read(paths["run"], {})
    proposal = _proposal_for_resolution(paths)
    resolution = _read(paths["value_resolution"], value_resolver.empty_value_resolution())
    state = {
        key: resolution.get(key) for key in ("research_consent", "source_pack_version", "research_jobs", "project_sources", "overrides")
    }
    def artifact_fingerprint(path, default):
        artifact = _read(path, default)
        return artifact.get("fingerprint") or ai_preliminary.fingerprint(artifact)

    skill_manifest = _read(paths["root"] / "skill_workflow_run.json", {})
    skill_run_id = skill_manifest.get("run_id")
    skill_proposal_dir = (paths["root"] / "skill_workflow_runs" / str(skill_run_id) / "proposals"
                          if skill_run_id else None)
    phase1_skill_proposals = {}
    if skill_proposal_dir:
        for skill_id in ("room_identity_use", "room_boundaries_areas", "surface_inventory", "surface_area",
                         "construction_matching", "boundary_resolution", "cross_sheet_opening_match",
                         "glazing_properties", "exposure_orientation"):
            phase1_skill_proposals[skill_id] = _read(skill_proposal_dir / f"{skill_id}.json", {})

    return {
        "building_evidence": ai_preliminary.fingerprint(_read(paths["building"], {})),
        "vision_response": ai_preliminary.fingerprint(_read(paths["vision"], {})),
        "evidence_fusion": ai_preliminary.fingerprint(_read(paths["fusion"], {})),
        "manual_placeholder_entities": ai_preliminary.fingerprint(run.get("manual_placeholder_entities", [])),
        "ai_preliminary_proposal": ai_preliminary.fingerprint(proposal),
        "phase1_skill_proposals": ai_preliminary.fingerprint({
            "source_fingerprint": skill_manifest.get("source_fingerprint", ""),
            "run_id": skill_run_id,
            "proposals": phase1_skill_proposals,
            "decisions": _read(paths["root"] / "skill_review_decisions.json", {}).get("decisions", {}),
        }),
        "research_cache": ai_preliminary.fingerprint(_read(paths["research_cache"], empty_research_cache())),
        "value_resolution_state": value_resolver.fingerprint(state),
        "site_location_resolution": ai_preliminary.fingerprint(_read(paths["site_location"], {})),
        "site_design_weather_resolution": ai_preliminary.fingerprint(_read(paths["site_design_weather"], {})),
        "reviewer_room_geometry_north": ai_preliminary.fingerprint(
            _read(paths["root"] / "reviewer_room_geometry.json", {}).get("page_north", {})
        ),
        "room_use_resolution": artifact_fingerprint(paths["room_use"], {}),
        "ceiling_volume_resolution": artifact_fingerprint(paths["ceiling_volume"], {}),
        "internal_gains_resolution": artifact_fingerprint(paths["internal_gains"], {}),
        "thermal_surface_resolution": artifact_fingerprint(paths["thermal_surface_resolution"], {}),
        "airflow_resolution": artifact_fingerprint(paths["airflow"], {}),
        "ahu_resolution": artifact_fingerprint(paths["ahu_resolution"], ahu_resolution.empty_ahu_resolution()),
        "plant_resolution": artifact_fingerprint(paths["plant_resolution"], plant_resolution.empty_plant_resolution()),
        "safety_factor_resolution": artifact_fingerprint(paths["safety_factor_resolution"], safety_factor_resolution.empty_safety_factor_resolution()),
        "preliminary_geometry_resolution": ai_preliminary.fingerprint(_preliminary_geometry(paths)),
        # Answers from the What-we-need-to-find list that the model reads directly.
        "glazing_answers": ai_preliminary.fingerprint(_read(paths["root"] / "glazing_answers.json", {})),
        "construction_answers": ai_preliminary.fingerprint(_read(paths["root"] / "construction_answers.json", {})),
        "weather_location": "|".join(weather_pick_service.effective(paths["root"])),
        "unconditioned_answers": ai_preliminary.fingerprint(_read(paths["root"] / "unconditioned_answers.json", {})),
        "exhaust_answers": ai_preliminary.fingerprint(_read(paths["root"] / "exhaust_answers.json", {})),
        "hours_answers": ai_preliminary.fingerprint(_read(paths["root"] / "hours_answers.json", {})),
    }


# Every file the draft geometry is built from (directly, or through room_proposal,
# _room_geometry_skill_proposals and reviewer_room_geometry_service.current_artifact_input).
_GEOMETRY_INPUT_FILES = ("ai_preliminary_run.json", "ai_input.json", "drawing_coverage.json", "building_evidence.json",
                         "spatial_ocr.json", "vector_geometry.json", "dimension_wall_matches.json",
                         "geometry_confirmation.json", "vision_response.json", "reviewer_room_geometry.json",
                         "room_use_resolution.json", "room_area_overrides.json", "skill_workflow_run.json")
_GEOMETRY_MEMO = {}
_GEOMETRY_MEMO_LOCK = threading.Lock()
_GEOMETRY_MEMO_LIMIT = 4


def _geometry_input_signature(paths):
    """Size and modification time of every geometry input; any write to an input changes it."""
    root = Path(paths["root"])
    files = [root / name for name in _GEOMETRY_INPUT_FILES] + [Path(paths["vision"]), Path(paths["building"]), Path(paths["run"])]
    files += sorted(root.glob("skill_workflow_runs/*/proposals/room_boundaries_areas.json"))
    files += sorted((root / "ai_tasks" / "S1_printed_areas").rglob("*.json"))
    signature = []
    for path in files:
        try:
            stat = path.stat()
            signature.append((str(path), stat.st_mtime_ns, stat.st_size, getattr(stat, "st_ino", 0)))
        except OSError:
            signature.append((str(path), None))
    return tuple(signature)


def _preliminary_geometry(paths):
    """Draft geometry for the current inputs, built once per set of inputs.

    One Calculate asks for it about ten times (source fingerprints, ceiling heights, freshness checks,
    each response); building it took several seconds each time on a 38-page set. The cached value is
    reused only while every input file is unchanged, and callers always get their own copy.
    """
    key = str(Path(paths["root"]).resolve())
    signature = _geometry_input_signature(paths)
    with _GEOMETRY_MEMO_LOCK:
        hit = _GEOMETRY_MEMO.get(key)
        if hit and hit[0] == signature:
            return deepcopy(hit[1])
    result = _build_preliminary_geometry(paths)
    with _GEOMETRY_MEMO_LOCK:
        _GEOMETRY_MEMO.pop(key, None)
        _GEOMETRY_MEMO[key] = (signature, deepcopy(result))
        while len(_GEOMETRY_MEMO) > _GEOMETRY_MEMO_LIMIT:
            _GEOMETRY_MEMO.pop(next(iter(_GEOMETRY_MEMO)))
    return result


def _build_preliminary_geometry(paths):
    """Rebuild current draft geometry in memory without mutating reviewed artifacts."""
    run = _read(paths["run"], {})
    proposal = room_proposal(run, paths["root"])
    from backend.calculation_extraction_service import _room_geometry_skill_proposals
    from backend import reviewer_room_geometry_service
    return build_geometry_resolution(
        _read(paths["root"] / "ai_input.json", {}),
        _read(paths["root"] / "drawing_coverage.json", {}),
        _read(paths["building"], {}),
        _read(paths["root"] / "spatial_ocr.json", {}),
        _read(paths["root"] / "vector_geometry.json", {}),
        dimension_matches=_read(paths["root"] / "dimension_wall_matches.json", {}),
        geometry_confirmation=_read(paths["root"] / "geometry_confirmation.json", {}),
        vision_response=_read(paths["vision"], {}),
        resolution_mode="preliminary_ai_estimate",
        room_proposals=((proposal or {}).get("rooms", []) if isinstance(proposal, dict) else []) +
            _room_geometry_skill_proposals(paths["root"]),
        reviewer_room_geometry=reviewer_room_geometry_service.current_artifact_input(paths["root"]),
    )


def _rebuild_geometry_evidence(web, project):
    """Persist the shared proof artifact after an AI/local-AI handoff."""
    paths = _paths(project)
    # Development/recovery handoffs can be prepared from a deliberately
    # minimal fixture. They have no architect packet to normalize, so retain
    # the local response instead of turning the optional rebuild into a
    # failure.
    if not (paths["root"] / "ai_input.json").exists():
        return {}
    from backend import calculation_extraction_service
    calculation_extraction_service.post(web, project, {"action": "build"})
    return _read(paths["root"] / "geometry_resolution.json", {})


def _resolution(paths):
    raw = _read(paths["value_resolution"], value_resolver.empty_value_resolution())
    if not raw.get("source_pack_version"):
        raw["source_pack_version"] = "au-cooling-v1"
    return value_resolver.validate_value_resolution(raw)


def _room_use_sources(paths):
    proposal = _proposal_for_resolution(paths)
    return {
        "building_evidence": ai_preliminary.fingerprint(_read(paths["building"], {})),
        "vision_response": ai_preliminary.fingerprint(_read(paths["vision"], {})),
        "ai_preliminary_proposal": ai_preliminary.fingerprint(proposal),
    }


def _resolve_room_uses(paths, persist=False):
    existing = _read(paths["room_use"], room_use_resolution.empty_room_use_resolution())
    proposal = _proposal_for_resolution(paths)
    artifact = room_use_resolution.resolve(
        _read(paths["building"], {}), _read(paths["vision"], {}),
        proposal,
        _room_use_sources(paths), existing,
    )
    if persist:
        _write(paths["room_use"], artifact)
        productization.record_change_if_fingerprint_changed(
            paths["root"], action="room_use_resolution_automatically_resolved", target=paths["room_use"].name,
            previous_fingerprint=room_use_resolution.fingerprint(existing), new_fingerprint=artifact["fingerprint"],
            affected_ids=[row["room_id"] for row in artifact.get("records", [])],
        )
    return artifact


def _ceiling_sources(paths):
    proposal = _proposal_for_resolution(paths)
    return {
        "building_evidence": ai_preliminary.fingerprint(_read(paths["building"], {})),
        "vision_response": ai_preliminary.fingerprint(_read(paths["vision"], {})),
        "ai_preliminary_proposal": ai_preliminary.fingerprint(proposal),
        "preliminary_geometry_resolution": ai_preliminary.fingerprint(_preliminary_geometry(paths)),
    }


def _internal_gains_sources(paths):
    proposal = _proposal_for_resolution(paths)
    pack = ai_preliminary.load_pack()
    return {
        "building_evidence": ai_preliminary.fingerprint(_read(paths["building"], {})),
        "vision_response": ai_preliminary.fingerprint(_read(paths["vision"], {})),
        "ai_preliminary_proposal": ai_preliminary.fingerprint(proposal),
        "room_use_resolution": ai_preliminary.fingerprint(_read(paths["room_use"], {})),
        "preliminary_pack": internal_gains_resolution.fingerprint(pack),
    }


def _airflow_sources(paths):
    proposal = _proposal_for_resolution(paths)
    site_weather_path = paths.get("site_design_weather") or paths.get("site_weather")
    pack = ai_preliminary.load_pack()
    return {
        "building_evidence": ai_preliminary.fingerprint(_read(paths["building"], {})),
        "vision_response": ai_preliminary.fingerprint(_read(paths["vision"], {})),
        "ai_preliminary_proposal": ai_preliminary.fingerprint(proposal),
        "room_use_resolution": ai_preliminary.fingerprint(_read(paths["room_use"], {})),
        "ceiling_volume_resolution": ai_preliminary.fingerprint(_read(paths["ceiling_volume"], {})),
        "site_design_weather_resolution": ai_preliminary.fingerprint(_read(site_weather_path, {}) if site_weather_path else {}),
        "value_resolution": ai_preliminary.fingerprint(_read(paths["value_resolution"], {})),
        "preliminary_pack": airflow_resolution.fingerprint(pack),
    }


def _resolve_ceiling_volumes(paths, persist=False):
    from backend import job_service
    existing = _read(paths["ceiling_volume"], ceiling_volume_resolution.empty_ceiling_volume_resolution())
    proposal = _proposal_for_resolution(paths)
    # Heights typed in the job workspace enter as contractor overrides (they beat drawing and default heights).
    artifact = job_service.drop_typed_height_stubs(ceiling_volume_resolution.resolve(
        _read(paths["building"], {}), _read(paths["vision"], {}), proposal if isinstance(proposal, dict) else {"rooms": []},
        _preliminary_geometry(paths), ai_preliminary.load_pack(), _ceiling_sources(paths),
        job_service.with_typed_heights(existing, paths["root"]),
    ))
    if persist:
        _write(paths["ceiling_volume"], artifact)
        productization.record_change_if_fingerprint_changed(
            paths["root"], action="ceiling_volume_resolution_automatically_resolved", target=paths["ceiling_volume"].name,
            previous_fingerprint=ceiling_volume_resolution.fingerprint(existing), new_fingerprint=artifact["fingerprint"],
            affected_ids=[row["room_id"] for row in artifact.get("records", [])],
        )
    return artifact


def _resolve_internal_gains(paths, persist=False):
    existing = _read(paths["internal_gains"], internal_gains_resolution.empty_internal_gains_resolution())
    room_use = room_use_resolution.validate(_read(paths["room_use"], room_use_resolution.empty_room_use_resolution()))
    artifact = internal_gains_resolution.resolve(
        _read(paths["building"], {}), _read(paths["vision"], {}), _proposal_for_resolution(paths),
        room_use, ai_preliminary.load_pack(), _internal_gains_sources(paths), existing,
    )
    if persist:
        _write(paths["internal_gains"], artifact)
        productization.record_change_if_fingerprint_changed(
            paths["root"], action="internal_gains_resolution_automatically_resolved",
            target=paths["internal_gains"].name,
            previous_fingerprint=internal_gains_resolution.fingerprint(existing),
            new_fingerprint=artifact["fingerprint"],
            affected_ids=[row.get("room_id") for row in artifact.get("records", [])],
        )
    return artifact


def _resolve_airflow(paths, persist=False):
    existing = _read(paths["airflow"], airflow_resolution.empty_airflow_resolution())
    room_use = room_use_resolution.validate(_read(paths["room_use"], room_use_resolution.empty_room_use_resolution()))
    ceiling = ceiling_volume_resolution.validate(_read(paths["ceiling_volume"], ceiling_volume_resolution.empty_ceiling_volume_resolution()))
    artifact = airflow_resolution.resolve(
        _read(paths["building"], {}), _read(paths["vision"], {}), _proposal_for_resolution(paths), room_use,
        ceiling, ai_preliminary.load_pack(), _airflow_sources(paths), existing,
    )
    if persist:
        _write(paths["airflow"], artifact)
        productization.record_change_if_fingerprint_changed(
            paths["root"], action="airflow_resolution_automatically_resolved", target=paths["airflow"].name,
            previous_fingerprint=airflow_resolution.fingerprint(existing), new_fingerprint=artifact["fingerprint"],
            affected_ids=[row.get("airflow_id") for row in artifact.get("records", [])],
        )
    return artifact


def _proposal_for_resolution(paths, equipment=True):
    """The room proposal the resolvers read: room-use findings, then accepted equipment from the PDF review."""
    from backend import need_answers_service
    proposal = need_answers_service.apply_hours(paths["root"], _proposal_with_skill_findings(paths))
    return _with_accepted_equipment(paths, proposal) if equipment else proposal


def _with_accepted_equipment(paths, proposal):
    """Add each accepted pass-2 equipment item to its room, as cited equipment the internal-gains resolver uses.

    Only items accepted on the current reading, assigned to a room, with a rated input and a heat-to-room
    factor, or the heat to the room from the data sheet, are added. A data-sheet item enters as its sensible heat
    (factor 1) with its moisture heat. A room with any such item uses its listed equipment instead of the
    area-based allowance (never below its typical level).
    """
    from ai import equipment_heat
    from backend import page_extraction_service
    items = page_extraction_service.accepted({"review_dir": str(paths["root"])}, "equipment_appliances")
    if not items:
        return proposal
    by_label = {str(row.get("label", "")).strip().casefold(): row for row in proposal.get("rooms", [])
                if isinstance(row, dict) and row.get("label")}
    for item in items:
        room = by_label.get(str(item.get("room") or "").strip().casefold())
        if room is None or equipment_heat.heat_w(item) is None:
            continue
        sheet = equipment_heat.from_data_sheet(item)
        room.setdefault("equipment", []).append({
            "name": item.get("name"), "quantity": item.get("quantity") or 1,
            "rated_input_w": item["sensible_to_room_w"] if sheet else item["rated_input_w"],
            "heat_to_space_factor": 1.0 if sheet else item["heat_to_space_factor"],
            **({"latent_w": item.get("latent_to_room_w") or 0} if sheet else {}),
            "source": (f"Data sheet ({item.get('heat_reference')}), entered by the operator" if sheet
                       else "PDF review (accepted by the operator)"),
            "evidence": [{"page": page, "excerpt": f"{item.get('code') or ''} {item.get('name')}".strip()} for page in item.get("pages", [])]})
    return proposal


def _skill_room_display_label(finding):
    """Turn a cited room-scope description into a display label when the plan has no text label."""
    label = str(finding.get("original_label") or finding.get("label") or "").strip()
    if label:
        return label
    scope = str(finding.get("scope") or "").strip()
    scope = re.split(r"[.;]", scope, maxsplit=1)[0]
    scope = re.sub(r"^(?:candidate|enclosed)\s+", "", scope, flags=re.IGNORECASE)
    scope = re.split(r"\s+(?:with|including|containing)\s+", scope, maxsplit=1, flags=re.IGNORECASE)[0]
    scope = re.sub(r"\b(?:compartment|space|room|area)\s*$", "", scope, flags=re.IGNORECASE)
    scope = re.sub(r"\baccess/lobby\b", "lobby", scope, flags=re.IGNORECASE)
    scope = re.sub(r"\s+", " ", scope).strip(" -,:;")
    if not scope:
        return ""
    words = scope.split()
    return " ".join("WC" if word.casefold() == "wc" else word.capitalize() for word in words)


def _proposal_with_skill_findings(paths):
    run = _read(paths["run"], {})
    proposal = room_proposal(run, paths["root"])

    # Runtime skills produce bounded proposals, not calculation artifacts.
    # Join their room-use findings into the existing preliminary proposal so
    # the established deterministic room-use/internal-gains resolvers actually
    # consume them. Previously the skill run could finish successfully while
    # the resolver still classified legacy labels on its own.
    manifest = _read(paths["root"] / "skill_workflow_run.json", {})
    run_id = manifest.get("run_id")
    skill_dir = paths["root"] / "skill_workflow_runs" / str(run_id) / "proposals" if run_id else None
    if not skill_dir:
        return proposal
    from backend import skill_workflow_service
    current_source_fingerprint = skill_workflow_service._source_fingerprint(
        skill_workflow_service._project_paths({"id": "", "review_dir": str(paths["root"])}),
        skill_workflow_service.load_catalog())
    manifest_source_fingerprint = manifest.get("source_fingerprint")
    if not manifest_source_fingerprint or manifest_source_fingerprint != current_source_fingerprint:
        return proposal
    decisions = _read(paths["root"] / "skill_review_decisions.json", {}).get("decisions", {})
    identity_proposal = _read(skill_dir / "room_identity_use.json", {})
    fields = identity_proposal.get("proposal_fields", {}) if isinstance(identity_proposal, dict) else {}
    identity_rows = fields.get("rooms", []) if isinstance(fields, dict) else []
    citations = {
        str(row.get("citation_id") or row.get("id")): row for row in identity_proposal.get("citations", [])
        if isinstance(row, dict) and (row.get("citation_id") or row.get("id"))
    } if isinstance(identity_proposal, dict) else {}
    citations_by_page = {}
    for citation in citations.values():
        page = citation.get("physical_pdf_page") or citation.get("physical_page") or citation.get("page")
        if isinstance(page, int):
            citations_by_page.setdefault(page, citation)
    inference_by_id = {}
    for inference in identity_proposal.get("inferences", []) if isinstance(identity_proposal, dict) else []:
        if not isinstance(inference, dict):
            continue
        match = re.search(r"rooms\[([^\]]+)\]", str(inference.get("field", "")))
        if match:
            inference_by_id.setdefault(match.group(1), []).append(inference)
    envelope_confidence = identity_proposal.get("confidence") if isinstance(identity_proposal, dict) else None
    try:
        envelope_confidence = float(envelope_confidence)
    except (TypeError, ValueError):
        envelope_confidence = 0.0
    taxonomy_ids = set(room_use_resolution.load_taxonomy()["categories"])
    from backend.calculation_extraction_service import _room_geometry_skill_proposals
    geometry_candidates = {
        str(row.get("room_id")): row for row in _room_geometry_skill_proposals(paths["root"])
        if isinstance(row, dict) and row.get("room_id")
    }
    # Only add an identity-only sanitary room to the preliminary model when
    # the same current geometry run produced one active, calibrated area for
    # that exact stable room ID.  This prevents furniture/function labels from
    # becoming duplicate rooms and keeps unclosed boundaries out.
    active_area_by_identity = {}
    possible_sanitary_geometry_ids = {
        str(row.get("room_id")): row for row in (identity_rows if isinstance(identity_rows, list) else [])
        if isinstance(row, dict) and not row.get("taxonomy_id")
        and any(term in str(row.get("scope") or "").casefold()
                for term in ("wc", "toilet", "bathroom", "sanitary", "lobby", "amenities"))
        and str(row.get("room_id") or "") in geometry_candidates
    }
    if possible_sanitary_geometry_ids:
        geometry = _preliminary_geometry(paths)
        for entity in geometry.get("entities", []) if isinstance(geometry, dict) else []:
            if not isinstance(entity, dict) or entity.get("kind") != "area" or entity.get("geometry_status") not in {"ai_estimated", "geometry_confirmed"}:
                continue
            value = entity.get("value") if isinstance(entity.get("value"), dict) else {}
            identity_id = str(value.get("room_identity_id") or "").strip()
            area = ai_preliminary._number(value.get("area_m2"))
            if identity_id and area and area > 0:
                active_area_by_identity.setdefault(identity_id, []).append((entity, value))
    accepted_rows = []
    for index, suggested in enumerate(identity_rows if isinstance(identity_rows, list) else []):
        if not isinstance(suggested, dict):
            continue
        decision = decisions.get(f"room_identity_use:rooms:{index}", {})
        current_decision = (decision.get("run_id") == run_id
                            and decision.get("source_fingerprint") == current_source_fingerprint)
        if current_decision and decision.get("status") == "rejected":
            continue
        finding = decision.get("value", suggested) if current_decision and decision.get("status") == "accepted" else suggested
        room_id = str(finding.get("room_id", ""))
        supporting = inference_by_id.get(room_id, [])
        inference_confidence = max((float(item.get("confidence", 0)) for item in supporting
                                    if isinstance(item.get("confidence"), (int, float))), default=0.0)
        confidence = finding.get("confidence", inference_confidence or envelope_confidence)
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 0.0
        evidence_pages = finding.get("evidence_page_ids", [])
        has_source = isinstance(evidence_pages, list) and any(
            isinstance(page, int) and page in citations_by_page for page in evidence_pages
        )
        category = finding.get("taxonomy_id")
        geometry_candidate = geometry_candidates.get(room_id)
        active_area = active_area_by_identity.get(room_id, [])
        scope_text = str(finding.get("scope") or "").casefold()
        sanitary_scope = any(term in scope_text for term in ("wc", "toilet", "bathroom", "sanitary", "lobby", "amenities"))
        can_draft_sanitary_room = (
            not category and sanitary_scope and confidence >= 0.65 and has_source
            and geometry_candidate is not None and len(active_area) == 1
        )
        if not (current_decision and decision.get("status") == "accepted"):
            if not can_draft_sanitary_room and (confidence < 0.8 or category not in taxonomy_ids or not has_source):
                continue
        if isinstance(finding, dict):
            finding = deepcopy(finding)
            if can_draft_sanitary_room:
                geometry_value = active_area[0][1]
                geometry_confidence = ai_preliminary._number(
                    geometry_candidate.get("confidence_score", geometry_candidate.get("confidence"))
                )
                area_confidence = ai_preliminary._number(geometry_value.get("confidence_score"))
                draft_confidence = min(confidence, geometry_confidence or confidence, area_confidence or confidence)
                finding["_draft_category"] = "ancillary_conditioned"
                finding["_draft_label"] = _skill_room_display_label(finding)
                finding["_draft_level"] = str(finding.get("level") or geometry_value.get("level_name") or "Unassigned level")
                finding["_draft_confidence"] = draft_confidence
            else:
                finding["_draft_confidence"] = confidence
            accepted_rows.append((index, finding))
    if not accepted_rows:
        return proposal
    rooms_by_id = {
        str(row.get("room_id")): row for row in proposal["rooms"]
        if isinstance(row, dict) and row.get("room_id")
    }
    rooms_by_label = {
        str(row.get("label", "")).strip().casefold(): row for row in proposal["rooms"]
        if isinstance(row, dict) and row.get("label")
    }
    unresolved_fields = identity_proposal.get("unresolved_fields", []) if isinstance(identity_proposal, dict) else []
    candidates = list(geometry_candidates.values())
    accepted_room_ids = set()
    for index, finding in accepted_rows:
        if not isinstance(finding, dict):
            continue
        room_id = str(finding.get("room_id", ""))
        accepted_room_ids.add(room_id)
        row = rooms_by_id.get(room_id) or rooms_by_label.get(str(finding.get("original_label", "")).strip().casefold())
        if not row:
            source_refs = []
            for cited in finding.get("citations", []) if isinstance(finding.get("citations"), list) else []:
                if not isinstance(cited, dict):
                    continue
                page = cited.get("physical_pdf_page") or cited.get("physical_page") or cited.get("page")
                if isinstance(page, int):
                    source_refs.append({"page": page, "drawing_number": cited.get("drawing_identity", ""),
                                        "title": cited.get("title", ""),
                                        "excerpt": cited.get("visual_evidence") or cited.get("excerpt") or ""})
            for citation_ref in finding.get("evidence_page_ids", []):
                citation = citations.get(str(citation_ref)) or citations_by_page.get(citation_ref)
                if not citation:
                    continue
                page = citation.get("physical_pdf_page") or citation.get("physical_page") or citation.get("page")
                if isinstance(page, int):
                    source_refs.append({"page": page, "drawing_number": citation.get("drawing_identity", ""),
                                        "title": citation.get("title", ""), "excerpt": citation.get("excerpt_or_crop", "")})
            if not source_refs:
                continue
            label = str(finding.get("_draft_label") or finding.get("original_label") or finding.get("label") or "").strip()
            if not label:
                continue
            row = {"kind": "room", "room_id": room_id, "label": label,
                   "level_name": finding.get("_draft_level") or finding.get("level") or "Unassigned level",
                   "page": source_refs[0]["page"], "source_pages": [item["page"] for item in source_refs],
                   "evidence": source_refs, "identity_source": "current_cited_room_identity_skill",
                   "geometry_identity_id": room_id, "direct_label_verified": False}
            proposal.setdefault("rooms", []).append(row)
            rooms_by_id[room_id] = row
            rooms_by_label[label.casefold()] = row
        row.setdefault("room_id", room_id)
        row.setdefault("level_name", finding.get("level") or "Unassigned level")
        category = finding.get("_draft_category") or finding.get("taxonomy_id")
        supporting_inferences = inference_by_id.get(room_id, [])
        taxonomy_inference = next((item for item in supporting_inferences if item.get("field", "").endswith(".taxonomy_id")), None)
        inferred_value = taxonomy_inference.get("value") if isinstance(taxonomy_inference, dict) else None
        if category:
            row["room_use_category"] = category
            row["room_use_rationale"] = (
                "AI identifies this as an amenity from the cited WC/toilet/lobby plan evidence; inclusion in comfort-HVAC scope is a provisional Phase 1 assumption because the drawing does not establish conditioning."
                if finding.get("_draft_category") else
                str(taxonomy_inference.get("method", "Skill interpretation of cited room evidence.")) if taxonomy_inference else
                "Skill-selected controlled room-use category."
            )
            row["confidence"] = finding.get("_draft_confidence", row.get("confidence", 0.65))
            if finding.get("_draft_category"):
                row["geometry_identity_id"] = room_id
                row["direct_label_verified"] = False
                row["room_use_assumption"] = "Assumed comfort-conditioned for this draft; confirm only if the HVAC drawings show it is exhaust-only or otherwise unconditioned."
        evidence = row.get("evidence") if isinstance(row.get("evidence"), list) else []
        for citation_id in finding.get("evidence_page_ids", []):
            citation = citations.get(str(citation_id)) or citations_by_page.get(citation_id)
            if citation:
                ref = {"page": citation.get("physical_pdf_page"), "drawing_number": citation.get("drawing_identity", ""),
                       "title": citation.get("title", ""), "excerpt": citation.get("excerpt_or_crop", "")}
                if ref.get("page") and ref not in evidence:
                    evidence.append(ref)
        row["evidence"] = evidence
        # A source-system label explicitly reported absent from the plan is not
        # direct-label evidence and cannot overrule the AI's cited category.
        if any(isinstance(item, dict) and room_id in str(item.get("field", "")) and "original_label" in str(item.get("field", ""))
               for item in unresolved_fields):
            row["direct_label_verified"] = False
        matching_inference = next((item for item in supporting_inferences if isinstance(item.get("value"), dict)
                                   and item["value"].get("boundary_status") == "Functional zone; independent room separation unresolved"), None)
        candidate = next((item for item in candidates if isinstance(item, dict) and item.get("room_id") == room_id), None)
        if matching_inference or (candidate and "parent_connected_zone_boundary" in candidate.get("unresolved_fields", [])):
            # A bar/service subarea without its own boundary must not receive a
            # second room load on top of the connected customer zone.
            row["non_counting_functional_subarea"] = True
            row["calculation_scope_override"] = "unresolved_scope"
            row["room_use_rationale"] = (row.get("room_use_rationale", "") +
                " Independent area is not counted because the drawing shows a functional subarea without a separate closed boundary.").strip()
    # Capture an explicitly described counted seating schedule as occupancy
    # evidence. The citation is retained; no schedule/profile number is
    # manufactured when the skill did not report a count.
    for observation in identity_proposal.get("observations", []) if isinstance(identity_proposal, dict) else []:
        if not isinstance(observation, dict):
            continue
        seat_match = re.search(r"\b(\d{1,4})\s+seats?\b", str(observation.get("detail", "")), re.IGNORECASE)
        if not seat_match or "schedule" not in str(observation.get("detail", "")).casefold():
            continue
        citation = next((citations.get(key) for key in observation.get("citation_ids", []) if citations.get(key)), None)
        page = citation.get("physical_pdf_page") if citation else None
        if page:
            for row in proposal["rooms"]:
                if (isinstance(row, dict) and str(row.get("room_id", "")).endswith(":shop")
                        and str(row.get("room_id", "")) in accepted_room_ids):
                    row["seat_count"] = int(seat_match.group(1))
                    row["evidence"] = row.get("evidence", []) + [{"page": page, "drawing_number": citation.get("drawing_identity", ""),
                        "title": citation.get("title", ""), "excerpt": f"Seating schedule: {seat_match.group(1)} seats."}]
                    break
            break
    return proposal


def _current_high_confidence_skill_rows(paths, subskill_id, field_name, decisions, *, min_confidence=0.6):
    """Read current, sufficiently confident skill rows for the Phase 1 draft only.

    A current explicit rejection wins. Otherwise high-confidence rows may feed
    the draft; the existing geometry/material validators still decide whether
    the row can contribute to a room load.
    """
    manifest = _read(paths["root"] / "skill_workflow_run.json", {})
    run_id = manifest.get("run_id")
    if not run_id:
        return [], {}
    from backend import skill_workflow_service
    source_fp = skill_workflow_service._source_fingerprint(
        skill_workflow_service._project_paths({"id": "", "review_dir": str(paths["root"])}),
        skill_workflow_service.load_catalog(),
    )
    if manifest.get("source_fingerprint") != source_fp:
        return [], {}
    subskill_state = manifest.get("subskills", {}).get(subskill_id, {})
    if subskill_state.get("status") not in {"running", "provisional", "needs_review", "resolved", "completed"}:
        return [], {}
    path = paths["root"] / "skill_workflow_runs" / str(run_id) / "proposals" / f"{subskill_id}.json"
    raw = _read(path, {})
    confidence = raw.get("confidence")
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.0
    citations = {}
    for citation in raw.get("citations", []) if isinstance(raw.get("citations"), list) else []:
        if not isinstance(citation, dict):
            continue
        citation_id = str(citation.get("citation_id") or citation.get("id") or "")
        page = next((citation.get(key) for key in ("physical_pdf_page", "physical_page", "page", "source_page")
                     if isinstance(citation.get(key), int)), None)
        if citation_id and page:
            citations[citation_id] = {
                "page": page,
                "drawing_identity": str(citation.get("drawing_identity") or ""),
                "excerpt": str(citation.get("excerpt_or_crop") or citation.get("excerpt") or citation.get("title") or ""),
                "visual_evidence": str(citation.get("visual_evidence") or citation.get("crop_description") or ""),
            }
    inference_by_target = {}
    for inference in raw.get("inferences", []) if isinstance(raw.get("inferences"), list) else []:
        if not isinstance(inference, dict):
            continue
        target_field = str(inference.get("field") or "")
        if "." not in target_field:
            continue
        target_id, inferred_field = target_field.rsplit(".", 1)
        inference_by_target.setdefault(target_id, {})[inferred_field] = deepcopy(inference)
    fields = raw.get("proposal_fields", {})
    rows = fields.get(field_name, []) if isinstance(fields, dict) else []
    selected = []
    for index, suggested in enumerate(rows if isinstance(rows, list) else []):
        if not isinstance(suggested, dict):
            continue
        decision = decisions.get(f"{subskill_id}:{field_name}:{index}", {})
        current_decision = (decision.get("run_id") == run_id and decision.get("source_fingerprint") == source_fp)
        if current_decision and decision.get("status") == "rejected":
            continue
        row = decision.get("value", suggested) if current_decision and decision.get("status") == "accepted" else suggested
        row_confidence = row.get("confidence", confidence) if isinstance(row, dict) else confidence
        try:
            row_confidence = float(row_confidence)
        except (TypeError, ValueError):
            row_confidence = confidence
        # These findings feed a draft calculation, not an accepted ledger value.
        # Domain-specific callers may admit cited scale traces below the general
        # threshold; their original confidence remains attached to the result.
        if not (current_decision and decision.get("status") == "accepted") and row_confidence < min_confidence:
            continue
        if isinstance(row, dict):
            row = deepcopy(row)
            target_id = str(row.get("surface_id") or row.get("opening_id") or "")
            if target_id and target_id in inference_by_target:
                row["_numeric_inferences"] = inference_by_target[target_id]
            selected.append((index, row, row_confidence))
    return selected, citations


def _supported_uncertainty_range(row, field, citations, point_value=None):
    """Return a cited numeric interval from a skill inference, if it is complete.

    Intervals are deliberately optional. Confidence scores are not converted
    into guessed error bars; a range is shown only when the skill supplied
    finite bounds, a method, and citations that resolve to this source set.
    """
    inferences = row.get("_numeric_inferences", {}) if isinstance(row, dict) else {}
    inference = inferences.get(field) if isinstance(inferences, dict) else None
    interval = inference.get("uncertainty_range") if isinstance(inference, dict) else None
    if not isinstance(interval, dict):
        return None
    try:
        low, high = float(interval.get("low")), float(interval.get("high"))
        point = float(point_value if point_value is not None else inference.get("value"))
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (low, high, point)) or low < 0 or high < low:
        return None
    if not low - 1e-9 <= point <= high + 1e-9:
        return None
    method = str(interval.get("method") or "").strip()
    citation_ids = interval.get("citation_ids")
    if not method or not isinstance(citation_ids, list) or not citation_ids:
        return None
    supported_citations = [deepcopy(citations[str(item)]) for item in citation_ids if str(item) in citations]
    if len(supported_citations) != len({str(item) for item in citation_ids}) or not supported_citations:
        return None
    return {"low": low, "high": high, "method": method, "citations": supported_citations}


def _pixel_rectangle(inference):
    """Return a validated axis-aligned pixel rectangle and its scale from a cited inference."""
    if not isinstance(inference, dict) or not str(inference.get("method") or "").strip():
        return None
    operands = inference.get("unrounded_operands", {})
    points = operands.get("boundary_points_px") if isinstance(operands, dict) else None
    if not isinstance(points, list) or len(points) != 4:
        return None
    try:
        xs = [float(point[0]) for point in points]
        ys = [float(point[1]) for point in points]
        scale_x = float(operands["scale_x_m_per_px"])
        scale_y = float(operands["scale_y_m_per_px"])
    except (KeyError, TypeError, ValueError, IndexError):
        return None
    if not all(math.isfinite(value) for value in (*xs, *ys, scale_x, scale_y)) or min(scale_x, scale_y) <= 0:
        return None
    if len({round(value, 5) for value in xs}) != 2 or len({round(value, 5) for value in ys}) != 2:
        return None
    if abs(scale_x - scale_y) > max(scale_x, scale_y) * 0.01:
        return None
    left, right, top, bottom = min(xs), max(xs), min(ys), max(ys)
    if right <= left or bottom <= top:
        return None
    computed = (right - left) * scale_x * (bottom - top) * scale_y
    try:
        reported = float(inference.get("value"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(reported) or abs(computed - reported) > max(0.05, computed * 0.01):
        return None
    return {"left": left, "right": right, "top": top, "bottom": bottom,
            "scale_x": scale_x, "scale_y": scale_y, "area_m2": computed}


def _complete_opening_coverage(area_rows, opening_matches, opening_properties, *, area_citations, property_citations):
    """Derive a complete host opening total only from a contiguous, cited, full-height pixel trace."""
    properties_by_id = {str(row.get("opening_id")): (row, score)
                        for _, row, score in opening_properties if row.get("opening_id")}
    matches_by_host = {}
    for _, match, score in opening_matches:
        host_id = str(match.get("host_wall_id") or "")
        opening_id = str(match.get("opening_id") or "")
        if host_id and opening_id:
            matches_by_host.setdefault(host_id, []).append((match, score))

    derived = {}
    for _, area_row, area_score in area_rows:
        host_id = str(area_row.get("surface_id") or "")
        gross = ai_preliminary._number(area_row.get("gross_area_m2"))
        host_inference = (area_row.get("_numeric_inferences") or {}).get("gross_area_m2")
        host_rect = _pixel_rectangle(host_inference)
        host_inference_citations = host_inference.get("citation_ids", []) if isinstance(host_inference, dict) else []
        host_sources = [area_citations.get(str(item)) for item in host_inference_citations]
        host_sources = [item for item in host_sources if item]
        if not host_id or gross is None or gross <= 0 or not host_rect or not host_sources:
            continue
        host_page = host_sources[0]["page"]
        host_drawing = host_sources[0]["drawing_identity"]
        if not host_drawing or any(item["page"] != host_page or item["drawing_identity"] != host_drawing for item in host_sources):
            continue
        host_matches = matches_by_host.get(host_id, [])
        if not host_matches:
            continue
        openings = []
        all_citation_ids = list(area_row.get("citation_ids", []))
        confidence = float(area_score)
        for match, match_score in host_matches:
            opening_id = str(match.get("opening_id") or "")
            prop_and_score = properties_by_id.get(opening_id)
            if not prop_and_score:
                openings = []
                break
            prop, prop_score = prop_and_score
            opening_area = ai_preliminary._number(prop.get("opening_area_m2"))
            inference = (prop.get("_numeric_inferences") or {}).get("opening_area_m2")
            rect = _pixel_rectangle(inference)
            inference_citations = inference.get("citation_ids", []) if isinstance(inference, dict) else []
            sources = [property_citations.get(str(item)) for item in inference_citations]
            sources = [item for item in sources if item]
            source_id = str(prop.get("source_id") or "").casefold()
            if (opening_area is None or opening_area <= 0 or not rect or not sources
                    or not re.search(r"elevation[-_ ]?1", source_id)
                    or any(item["page"] != host_page or item["drawing_identity"] != host_drawing for item in sources)):
                openings = []
                break
            if (abs(rect["top"] - host_rect["top"]) > 2 or abs(rect["bottom"] - host_rect["bottom"]) > 2
                    or abs(rect["scale_x"] - host_rect["scale_x"]) > host_rect["scale_x"] * 0.01
                    or abs(rect["scale_y"] - host_rect["scale_y"]) > host_rect["scale_y"] * 0.01
                    or rect["left"] < host_rect["left"] - 2 or rect["right"] > host_rect["right"] + 2
                    or abs(rect["area_m2"] - opening_area) > max(0.05, opening_area * 0.01)):
                openings = []
                break
            openings.append((rect, opening_area))
            confidence = min(confidence, float(match_score), float(prop_score))
            all_citation_ids.extend(prop.get("citation_ids", []))
            all_citation_ids.extend(inference_citations)
        if len(openings) != len(host_matches):
            continue
        openings.sort(key=lambda pair: pair[0]["left"])
        for (previous, _), (current, _) in zip(openings, openings[1:]):
            gap = current["left"] - previous["right"]
            if gap < -2 or gap > 2:
                openings = []
                break
        if not openings:
            continue
        first, last = openings[0][0], openings[-1][0]
        host_width = host_rect["right"] - host_rect["left"]
        if (first["left"] - host_rect["left"] > host_width * 0.05
                or host_rect["right"] - last["right"] > host_width * 0.05):
            continue
        opening_area_total = sum(area for _, area in openings)
        pixel_union_area = ((last["right"] - first["left"]) * host_rect["scale_x"]
                            * (host_rect["bottom"] - host_rect["top"]) * host_rect["scale_y"])
        if abs(pixel_union_area - opening_area_total) > max(0.1, opening_area_total * 0.01):
            continue
        net_area = gross - opening_area_total
        if net_area <= 0:
            continue
        derived_row = deepcopy(area_row)
        derived_row.update({
            "opening_area_m2": round(opening_area_total, 2),
            "net_opaque_area_m2": round(net_area, 2),
            "opening_coverage": "complete",
            "confidence": confidence,
            "citation_ids": sorted({str(item) for item in all_citation_ids if item}),
            "formula": (f"{area_row.get('formula') or 'host gross area'}; subtract {len(openings)} matched, contiguous, full-height opening areas "
                        "whose cited pixel rectangles share the same elevation-1 coordinate frame"),
            "_coverage_method": "cited_contiguous_full_height_pixel_rectangles",
        })
        derived[host_id] = (derived_row, confidence)
    return derived


def _merge_phase1_skill_proposals(paths, proposal):
    """Join validated high-confidence envelope and opening skill findings into the draft."""
    review = _read(paths["root"] / "skill_review_decisions.json", {})
    decisions = review.get("decisions", {}) if isinstance(review, dict) else {}
    room_rows = [row for row in proposal.get("rooms", []) if isinstance(row, dict)]
    rooms_by_id = {str(row.get("room_id")): row for row in room_rows if row.get("room_id")}
    rooms_by_label = {str(row.get("label", "")).strip().casefold(): row for row in room_rows if row.get("label")}
    surfaces, surface_citations = _current_high_confidence_skill_rows(paths, "surface_inventory", "surfaces", decisions)
    areas, area_citations = _current_high_confidence_skill_rows(
        paths, "surface_area", "areas", decisions, min_confidence=0.5,
    )
    constructions, construction_citations = _current_high_confidence_skill_rows(paths, "construction_matching", "constructions", decisions)
    boundaries, boundary_citations = _current_high_confidence_skill_rows(paths, "boundary_resolution", "boundaries", decisions)
    matches, match_citations = _current_high_confidence_skill_rows(paths, "cross_sheet_opening_match", "matches", decisions)
    properties, property_citations = _current_high_confidence_skill_rows(
        paths, "glazing_properties", "properties", decisions, min_confidence=0.55,
    )
    complete_coverage = _complete_opening_coverage(
        areas, matches, properties, area_citations=area_citations, property_citations=property_citations,
    )
    areas_by_id = {str(row.get("surface_id")): (row, score) for _, row, score in areas if row.get("surface_id")}
    areas_by_id.update(complete_coverage)
    construction_by_id = {str(row.get("surface_id")): (row, score) for _, row, score in constructions if row.get("surface_id")}
    boundary_by_id = {str(row.get("surface_id")): (row, score) for _, row, score in boundaries if row.get("surface_id")}

    def evidence_for(row, citations, page_fallback=None):
        evidence = [deepcopy(item) for item in row.get("evidence", []) if isinstance(item, dict) and item.get("page")]
        row_citations = row.get("citations", [])
        if isinstance(row_citations, list):
            evidence.extend({"page": item.get("page"), "drawing_number": item.get("drawing_identity", ""),
                             "excerpt": item.get("excerpt") or item.get("excerpt_or_crop") or item.get("crop_description", "")}
                            for item in row_citations if isinstance(item, dict) and isinstance(item.get("page"), int))
        ids = row.get("citation_ids", [])
        if isinstance(ids, list):
            evidence.extend(deepcopy(citations[str(item)]) for item in ids if str(item) in citations)
        if not evidence:
            page = row.get("page") or page_fallback
            if isinstance(page, int):
                evidence.append({"page": page, "excerpt": str(row.get("excerpt") or row.get("label") or row.get("surface_id") or "AI skill finding")})
        unique = {(item.get("page"), item.get("excerpt", "")): item for item in evidence}
        return list(unique.values())

    existing_surface_keys = {str(row.get("surface_key") or row.get("candidate_id") or "") for row in proposal.get("surfaces", []) if isinstance(row, dict)}
    surface_lookup = {}
    for _, raw, inventory_score in surfaces:
        surface_id = str(raw.get("surface_id") or "").strip()
        if not surface_id or surface_id in existing_surface_keys:
            continue
        owners = raw.get("owner_ids", [])
        owners = owners if isinstance(owners, list) else [owners]
        owner = next((rooms_by_id.get(str(value)) for value in owners if str(value) in rooms_by_id), None)
        if owner is None:
            owner = next((rooms_by_label.get(str(value).strip().casefold()) for value in owners
                          if str(value).strip().casefold() in rooms_by_label), None)
        if owner is None or len(owners) != 1:
            continue
        area_record, area_score = areas_by_id.get(surface_id, ({}, inventory_score))
        construction, construction_score = construction_by_id.get(surface_id, ({}, inventory_score))
        boundary, boundary_score = boundary_by_id.get(surface_id, ({}, inventory_score))
        physical_type = str(raw.get("type") or raw.get("physical_type") or "").casefold()
        if physical_type in {"opaque_wall", "external_wall"}:
            physical_type = "wall"
        if physical_type not in {"wall", "roof", "floor", "ceiling", "partition"}:
            continue
        thermal_role = str(boundary.get("method") or raw.get("thermal_role") or "unresolved").casefold()
        if thermal_role in {"outdoor", "outside", "outdoors"}:
            thermal_role = "external"
        gross_area = ai_preliminary._number(area_record.get("gross_area_m2"))
        if gross_area is None:
            gross_area = ai_preliminary._number(raw.get("gross_area_m2", raw.get("area_m2")))
        if gross_area is None or gross_area <= 0:
            continue
        evidence = evidence_for(raw, surface_citations)
        evidence.extend(evidence_for(area_record, area_citations))
        evidence.extend(evidence_for(construction, construction_citations))
        evidence.extend(evidence_for(boundary, boundary_citations))
        if not evidence:
            continue
        owner_label = str(owner.get("label") or raw.get("owner_label") or "")
        owner_level = str(owner.get("level_name") or raw.get("level") or "Unassigned level")
        confidence = min(inventory_score, area_score, construction_score, boundary_score)
        opening_area = ai_preliminary._number(area_record.get("opening_area_m2"))
        net_area = ai_preliminary._number(area_record.get("net_opaque_area_m2"))
        opening_coverage = str(area_record.get("opening_coverage") or "unknown").casefold()
        if physical_type in {"floor", "ceiling", "roof"} and opening_coverage == "unknown":
            # Phase 1's opening subtraction applies to wall-hosted doors and
            # glazing. Horizontal penetrations are outside this surface-area
            # contract and must not make a measured floor/ceiling disappear.
            opening_coverage = "not_applicable"
        if physical_type == "wall" and (opening_area is None or net_area is None) and opening_coverage not in {"complete", "reviewer_entered", "not_applicable"}:
            # The opaque-wall solver currently falls back from a null net area
            # to gross area. Exclude uncertain mixed-opening walls until that
            # solver can represent a bounded opaque-area estimate safely.
            continue
        supported_ranges = []
        area_range_field = "net_opaque_area_m2" if net_area is not None else "gross_area_m2"
        area_range_point = net_area if net_area is not None else gross_area
        # Gross-area bounds can be reused for the opaque area only when no
        # opening subtraction is needed. Otherwise retain only a range the
        # drawing skill explicitly gave for net opaque area.
        if area_range_field == "gross_area_m2" and opening_area not in (None, 0):
            area_range_field = ""
        area_range = (_supported_uncertainty_range(area_record, area_range_field, area_citations, area_range_point)
                      if area_range_field else None)
        if area_range:
            supported_ranges.append({"field": "area_m2", **area_range})
        u_value = ai_preliminary._number(construction.get("u_value_w_m2k"))
        u_range = _supported_uncertainty_range(construction, "u_value_w_m2k", construction_citations, u_value)
        if u_range:
            supported_ranges.append({"field": "u_value_w_m2k", **u_range})
        row = {
            "surface_key": surface_id, "label": str(raw.get("label") or surface_id),
            "owner_room_id": owner.get("room_id", ""), "owner_room_label": owner_label,
            "owner_level_name": owner_level, "physical_type": physical_type,
            "thermal_role": thermal_role,
            "external_exposure": "external" if thermal_role == "external" else str(raw.get("external_exposure") or ""),
            "boundary_condition": str(boundary.get("adjacent_owner") or raw.get("adjacent_space") or "outside" if thermal_role == "external" else "unresolved"),
            "orientation": str(raw.get("orientation") or "").upper(),
            "gross_area_m2": gross_area, "net_opaque_area_m2": net_area,
            "opening_coverage": opening_coverage, "construction_id": str(construction.get("assembly_id") or ""),
            "u_value_w_m2k": u_value, "supported_uncertainty_ranges": supported_ranges,
            "confidence": confidence, "page": evidence[0]["page"], "evidence": evidence,
            "rationale": ("Cited AI surface inventory joined to draft area, construction and boundary findings; "
                          "contiguous full-height opening coverage was checked from the shared elevation pixel geometry."
                          if area_record.get("_coverage_method") == "cited_contiguous_full_height_pixel_rectangles"
                          else "Cited AI surface inventory joined to draft area, construction and boundary findings; provisional calculation input."),
            "assumptions": (["ai_skill_surface_proposal"] +
                            (["cited_contiguous_full_height_pixel_rectangles"]
                             if area_record.get("_coverage_method") == "cited_contiguous_full_height_pixel_rectangles" else [])),
            "conflicts": [],
        }
        proposal.setdefault("surfaces", []).append(row)
        existing_surface_keys.add(surface_id)
        surface_lookup[surface_id] = row

    exposures, exposure_citations = _current_high_confidence_skill_rows(paths, "exposure_orientation", "exposures", decisions)
    properties_by_id = {str(row.get("opening_id")): (row, score) for _, row, score in properties if row.get("opening_id")}
    exposures_by_id = {str(row.get("opening_id")): (row, score) for _, row, score in exposures if row.get("opening_id")}
    existing_opening_keys = {str(row.get("opening_key") or row.get("candidate_id") or "") for row in proposal.get("openings", []) if isinstance(row, dict)}
    for _, match, match_score in matches:
        opening_id = str(match.get("opening_id") or "").strip()
        if not opening_id or opening_id in existing_opening_keys:
            continue
        prop, prop_score = properties_by_id.get(opening_id, ({}, match_score))
        exposure, exposure_score = exposures_by_id.get(opening_id, ({}, match_score))
        host_id = str(match.get("host_wall_id") or "")
        host = surface_lookup.get(host_id) or next((row for row in proposal.get("surfaces", [])
            if isinstance(row, dict) and str(row.get("surface_key") or row.get("candidate_id") or "") == host_id), None)
        owner = rooms_by_id.get(str(match.get("room_id") or ""))
        if owner is None and host:
            owner = rooms_by_label.get(str(host.get("owner_room_label") or "").strip().casefold())
        if owner is None or host is None:
            continue
        opening_area = ai_preliminary._number(prop.get("opening_area_m2"))
        if opening_area is None or opening_area <= 0:
            continue
        exposure_value = str(exposure.get("exposure") or "").casefold()
        if exposure_value not in {"external", "outdoor", "outside", "outdoors", "internal"}:
            host_exposure = str(host.get("external_exposure") or "").casefold()
            host_role = str(host.get("thermal_role") or "").casefold()
            if host_exposure == "external" or host_role in {"external", "outdoor", "outside", "outdoors"}:
                exposure_value = "external"
                # An opening inherits the host boundary only when there is no
                # explicit, current opening-level classification. Carry the
                # host's confidence and source evidence with that inference.
                exposure_score = min(match_score, float(host.get("confidence") or match_score))
            else:
                exposure_value = "unresolved"
        confidence = min(match_score, prop_score, exposure_score)
        # Draft proposals with cited scale-derived areas may score below 0.8.
        # Keep them provisional and visible so the model can produce its
        # automated Phase 1 estimate without treating the result as accepted.
        if confidence < 0.55:
            continue
        azimuth = ai_preliminary._number(exposure.get("azimuth_deg"))
        orientation = ("N", "E", "S", "W")[int(((azimuth or 0) % 360 + 45) // 90) % 4] if azimuth is not None else ""
        evidence = evidence_for(match, match_citations)
        evidence.extend(evidence_for(prop, property_citations))
        evidence.extend(evidence_for(exposure, exposure_citations))
        if exposure_value == "external" and not exposure.get("exposure"):
            evidence.extend(deepcopy(item) for item in host.get("evidence", []) if isinstance(item, dict))
        if not evidence:
            continue
        supported_ranges = []
        area_range = _supported_uncertainty_range(prop, "opening_area_m2", property_citations, opening_area)
        if area_range:
            supported_ranges.append({"field": "area_m2", **area_range})
        u_value = ai_preliminary._number(prop.get("u_value_w_m2k"))
        u_range = _supported_uncertainty_range(prop, "u_value_w_m2k", property_citations, u_value)
        if u_range:
            supported_ranges.append({"field": "u_value_w_m2k", **u_range})
        shgc = ai_preliminary._number(prop.get("solar_property")) if prop.get("solar_property_type") == "shgc" else None
        shgc_range = _supported_uncertainty_range(prop, "solar_property", property_citations, shgc) if shgc is not None else None
        if shgc_range:
            supported_ranges.append({"field": "shgc", **shgc_range})
        opening = {
            "opening_key": opening_id, "label": str(prop.get("label") or opening_id),
            "owner_room_id": owner.get("room_id", ""), "owner_room_label": owner.get("label", ""),
            "owner_level_name": owner.get("level_name", "Unassigned level"),
            "host_surface_key": host_id, "opening_area_m2": opening_area,
            "explicit_glass_area_m2": ai_preliminary._number(prop.get("glass_area_m2")),
            "external_exposure": exposure_value,
            "orientation": orientation, "shading_category": "unshaded", "glazing_choice": prop.get("glazing_choice"),
            "u_value_w_m2k": u_value, "shgc": shgc,
            "glazing_source": str(prop.get("source_id") or "Cited PDF glazing property"),
            "confidence": confidence, "page": evidence[0]["page"], "evidence": evidence,
            "supported_uncertainty_ranges": supported_ranges,
            "assumptions": ["ai_skill_opening_proposal", "unshaded"],
        }
        proposal.setdefault("openings", []).append(opening)
        existing_opening_keys.add(opening_id)
    return proposal


def _save_resolution(paths, resolution, action, affected_ids=None):
    before = _read(paths["value_resolution"], value_resolver.empty_value_resolution())
    _write(paths["value_resolution"], resolution)
    productization.record_change_if_fingerprint_changed(
        paths["root"], action=action, target=paths["value_resolution"].name,
        previous_fingerprint=value_resolver.fingerprint(before), new_fingerprint=value_resolver.fingerprint(resolution),
        affected_ids=affected_ids or [],
    )


def _resolve_from_packs(paths, project):
    building, vision = _read(paths["building"], {}), _read(paths["vision"], {})
    run = _read(paths["run"], {})
    proposal = room_proposal(run, paths["root"])
    # Older projects stored the placeholder as a room list.  Retain that
    # compatibility rather than assuming every existing project has the new
    # proposal envelope.
    if isinstance(proposal, list):
        proposal = {"rooms": proposal}
    if not isinstance(proposal, dict):
        proposal = {"rooms": []}
    room_use = _resolve_room_uses(paths, persist=True)
    ceiling_artifact = _resolve_ceiling_volumes(paths, persist=True)
    internal_gains_artifact = _resolve_internal_gains(paths, persist=True)
    airflow_artifact = _resolve_airflow(paths, persist=True)
    geometry = _preliminary_geometry(paths)
    rows = ai_preliminary._apply_room_use_resolution(
        ai_preliminary._space_rows(building, vision, proposal.get("rooms", []), geometry), room_use
    )
    effective_proposal = ai_preliminary._proposal_with_room_use(proposal, rows)
    artifact, _values = value_resolver.build_value_resolution(
        ai_preliminary.load_pack(), building, effective_proposal,
        validate_cache(_read(paths["research_cache"], empty_research_cache())), _resolution(paths),
        source_fingerprints=_sources(paths), site_location=_read(paths["site_location"], {}),
        site_design_weather=_read(paths["site_design_weather"], {}),
        ceiling_volume_resolution=ceiling_artifact,
        internal_gains_resolution=internal_gains_artifact,
        airflow_resolution=airflow_artifact,
    )
    _save_resolution(paths, artifact, "value_resolution_resolved")
    return artifact


def _ledger_has_included_external_surface(geometry, room_id, room_label, level_name, physical_type):
    ledger = (geometry or {}).get("thermal_surface_ledger", {})
    for surface in ledger.get("surfaces", []) if isinstance(ledger, dict) else []:
        if not isinstance(surface, dict):
            continue
        physical = str(surface.get("physical_type", surface.get("kind", ""))).casefold()
        boundary = str(surface.get("boundary_condition", "")).casefold()
        role = str(surface.get("thermal_role", "")).casefold()
        external = (physical in {"wall", "roof", "ceiling", "opaque_wall"}
                    and (surface.get("external_exposure") == "external" or boundary == "outside"
                         or role in {"external", "outside", "outdoors"}))
        if not external or surface.get("thermal_eligible") is False or surface.get("status") in {"blocked", "excluded", "stale"}:
            continue
        surface_type = "wall" if physical in {"wall", "opaque_wall"} else physical
        requested_type = "wall" if physical_type in {"wall", "opaque_wall"} else physical_type
        if surface_type != requested_type:
            continue
        owner_id = str(surface.get("owner_room_id", ""))
        owner_matches = owner_id == str(room_id)
        label_matches = (str(surface.get("owner_room_label", "")).casefold() == str(room_label).casefold()
                         and (not surface.get("level_name") or str(surface.get("level_name")).casefold() == str(level_name).casefold()))
        if owner_matches or label_matches:
            return True
    return False


def _prepare_preliminary_proposal(paths, raw_proposal, room_use, geometry):
    """Join controlled room-use decisions and skill geometry proposals before validation.

    The local room detector intentionally returns identity/evidence first.  The
    room-use resolver owns profile/scope selection, while the geometry skill
    owns boundary proposals.  Previously the preliminary assembler validated
    the raw identity-only rows before either resolver's data could be joined,
    so it failed on missing profile/area even when those records existed in
    their authoritative artifacts.
    """
    from backend.calculation_extraction_service import _room_geometry_skill_proposals
    from backend import reviewer_room_geometry_service
    from backend import need_answers_service

    if isinstance(raw_proposal, list):
        proposal = {"rooms": deepcopy(raw_proposal)}
    elif isinstance(raw_proposal, dict):
        proposal = deepcopy(raw_proposal)
    else:
        proposal = {"rooms": []}
    proposal.setdefault("rooms", [])
    proposal.setdefault("openings", [])
    # Envelope assessments are always rebuilt from the current reviewer trace
    # registry; never accept provider-supplied trace provenance or exclusions.
    proposal.pop("envelope_assessments", None)
    proposal["surfaces"] = [row for row in proposal.get("surfaces", []) if isinstance(row, dict)]
    use_by_id = {
        row.get("room_id"): row for row in (room_use or {}).get("records", [])
        if isinstance(row, dict) and row.get("room_id")
    }
    geometry_by_id = {}
    for row in _room_geometry_skill_proposals(paths["root"]):
        if isinstance(row, dict) and row.get("room_id"):
            geometry_by_id[row["room_id"]] = row
    trace_review_issue = None
    try:
        trace_registry_path = paths["root"] / "reviewer_room_geometry.json"
        trace_registry = {}
        if trace_registry_path.exists():
            # The shared reader tolerates malformed JSON by returning an empty
            # default. Validate strictly here so that corruption is visible to
            # the reviewer instead of looking like simply absent evidence.
            trace_registry = json.loads(trace_registry_path.read_text(encoding="utf-8"))
        traced_areas = reviewer_room_geometry_service.current_traced_areas(paths["root"])
    except (OSError, ValueError, TypeError, KeyError):
        traced_areas = {}
        trace_registry = {}
        trace_review_issue = {
            "component": "room area",
            "reason": "Reviewer trace artifact could not be read; trace-derived areas were ignored.",
            "remediation": "Repair reviewer_room_geometry.json, then resolve room areas from current calibrated traces.",
        }
    try:
        ceiling_values = ceiling_volume_resolution.values_by_room(
            _read(paths["root"] / "ceiling_volume_resolution.json", {})
        )
    except (OSError, TypeError, ValueError):
        ceiling_values = {}
    envelope_assessments = []
    # Phase 1 must give every traced room its surfaces every time (user spec 2026-10-16): what the AI could not settle
    # is filled with a labelled fallback, never dropped. Read once for all rooms.
    phase1_ai_input = _read(paths["root"] / "ai_input.json", {})
    phase1_site = _read(paths["root"] / "site_location_ai_determination.json", {})
    floor_basis = _floor_basis(phase1_ai_input)
    default_height_mm = float(ai_preliminary.load_pack()["preliminary_defaults"]["ceiling_height_mm"])
    partition = ai_preliminary.load_pack()["preliminary_envelope"]["opaque_constructions"]["partition"]
    try:
        outlines_by_page = _outlines_by_page(reviewer_room_geometry_service.current_records(
            reviewer_room_geometry_service._paths({"review_dir": str(paths["root"])})))
    except (OSError, ValueError, TypeError, KeyError):
        outlines_by_page = {}
    for room in proposal["rooms"]:
        if not isinstance(room, dict):
            continue
        # These provenance fields are reserved for this service's fresh
        # reviewer-registry match. Never accept them from a provider proposal.
        for field in ("area_origin", "area_verification_status", "reviewer_trace_id",
                      "geometry_proof_id", "reviewer_traced_area"):
            room.pop(field, None)
        room_id = room.get("room_id")
        use = use_by_id.get(room_id)
        if not use:
            identity = room_use_resolution.room_identity(room.get("label", ""), room.get("level_name", ""))
            use = use_by_id.get(identity)
        if use:
            room["preliminary_profile_id"] = use.get("preliminary_profile_id") or "generic_conditioned_room"
            room["space_scope"] = use.get("space_scope", "unresolved_scope")
            room["room_use_category"] = use.get("taxonomy_id", "")
            room["room_use_status"] = use.get("status", "needs_review")
            room["evidence"] = ai_preliminary._combined_evidence(room, use)
        else:
            # Keep an explicit low-confidence controlled profile so excluded
            # or unresolved rooms can remain visible without being mistaken
            # for an evidence-backed room-use decision.
            room.setdefault("preliminary_profile_id", "generic_conditioned_room")
        room_use_id = str((use or {}).get("room_id") or room_id or
                          room_use_resolution.room_identity(room.get("label", ""), room.get("level_name", "")))
        traced_area = traced_areas.get(room_use_id)
        if traced_area and not traced_area.get("conflict"):
            room["reviewer_traced_area"] = deepcopy(traced_area)
            existing_area = ai_preliminary._number(room.get("area_m2"))
            existing_origin = str(room.get("area_origin", ""))
            trace_area_is_printed = traced_area.get("area_source") in {"printed_on_drawing", "printed (read from image)"}
            trace_area_is_edited = traced_area.get("area_source") == "edited"
            if (trace_area_is_printed or trace_area_is_edited or existing_area is None or existing_area <= 0
                    or existing_origin in {"", "ai_geometry", "ai_assumption"}):
                page = traced_area.get("page")
                trace_id = str(traced_area.get("trace_id", ""))
                proof_id = str(traced_area.get("proof_id", ""))
                room["area_m2"] = traced_area["area_m2"]
                ai_determined = (traced_area.get("declaration_source") in {"ai_determined", "ai_fallback"}
                                 and not trace_area_is_printed and not trace_area_is_edited)
                room["area_origin"] = ("edited" if trace_area_is_edited else
                                       "printed (read from image)" if traced_area.get("area_only") else
                                       "pdf_evidence" if trace_area_is_printed else
                                       "ai_determined" if ai_determined else "reviewer_traced")
                room["area_verification_status"] = "provisional"
                room["reviewer_trace_id"] = trace_id if not traced_area.get("area_only") else ""
                room["geometry_proof_id"] = proof_id if not traced_area.get("area_only") else ""
                room["reviewer_traced_area"] = deepcopy(traced_area)
                trace_citation = {"page": page,
                                  "reference": ("Room area entered by a person" if trace_area_is_edited else
                                                "Printed room area read from image" if traced_area.get("area_only") else f"Room trace {trace_id}"),
                                  "excerpt": (f"Room area typed in by {traced_area.get('edited_by') or 'an operator'}: {traced_area.get('area_m2')} m²."
                                              if trace_area_is_edited else
                                              f"Printed room area read from image: {traced_area.get('printed_text') or room.get('label', '')}."
                                              if traced_area.get("area_only") else
                                              f"AI-determined room outline; {traced_area.get('ai_quality_label') or 'below accuracy bar'}; "
                                              f"calibration status {traced_area.get('calibration_status')}." if ai_determined else
                                              f"Reviewer-traced room boundary; calibration status {traced_area.get('calibration_status')}."),
                                  **({} if traced_area.get("area_only") else {"reviewer_trace_id": trace_id, "geometry_proof_id": proof_id})}
                room["evidence"] = ai_preliminary._combined_evidence(room, {"evidence": [trace_citation]})
            if str(room.get("space_scope", "")).startswith("comfort_hvac"):
                trace_parts = [] if traced_area.get("area_only") else (traced_area.get("supporting_traces") or [traced_area])
                assessment = {"owner_room_label": room.get("label", ""),
                              "owner_level_name": room.get("level_name", ""),
                              "trace_id": traced_area.get("trace_id", ""),
                              "page": traced_area.get("page"), "reviewer": traced_area.get("envelope_reviewer", ""),
                              "not_assessed": [], "excluded": []}
                ceiling_identity = ceiling_volume_resolution.room_identity(
                    room.get("label", ""), room.get("level_name", "")
                )
                ceiling = ceiling_values.get(room_use_id) or ceiling_values.get(ceiling_identity, {})
                height_mm = ai_preliminary._number(ceiling.get("ceiling_height_mm"))
                height_note = ""
                if height_mm is None:
                    height_mm = default_height_mm
                    height_note = (f"No ceiling height was found for {room.get('label', 'this room')}; its walls use the "
                                   f"{height_mm / 1000:g} m preliminary default height.")
                skip_roof = _ledger_has_included_external_surface(
                    geometry, room_use_id, room.get("label", ""), room.get("level_name", ""), "roof",
                )
                skip_walls = _ledger_has_included_external_surface(
                    geometry, room_use_id, room.get("label", ""), room.get("level_name", ""), "wall",
                )
                fully_internal_parts = []
                if traced_area.get("area_only"):
                    assessment["not_assessed"].extend([
                        {"component_id": "area_only_walls", "component": "Walls — not assessed",
                         "reason": "No room outline is available; wall boundaries and areas were not assessed.",
                         "page": traced_area.get("page")},
                        {"component_id": "area_only_roof", "component": "Roof — not assessed",
                         "reason": "No room outline or roof declaration is available; roof exposure and gains were not assessed.",
                         "page": traced_area.get("page")},
                    ])
                for part in trace_parts:
                    part_component_suffix = f"_{part.get('trace_id')}" if len(trace_parts) > 1 else ""
                    declaration_reviewer = str(part.get("envelope_reviewer", ""))
                    citation = {"page": part.get("page"), "reference": f"Room trace {part.get('trace_id', '')}",
                                "excerpt": "Reviewer-declared room boundary for preliminary envelope assessment.",
                                "reviewer_trace_id": part.get("trace_id", ""), "reviewer": declaration_reviewer}
                    room["evidence"] = ai_preliminary._combined_evidence(room, {"evidence": [citation]})
                    edges = part.get("edges", [])
                    edge_evidence = {int(row["index"]): row for row in part.get("boundary_evidence", [])
                                     if isinstance(row, dict) and type(row.get("index")) is int}
                    roof = part.get("roof", "unknown")
                    roof_note = ""
                    if roof == "unknown":
                        roof, roof_note = _roof_assumption(paths, phase1_ai_input, phase1_site, room)
                    area = ai_preliminary._number(part.get("area_m2"))
                    calibration = part.get("calibration", {})
                    mm_per_px = ai_preliminary._number(calibration.get("mm_per_px"))
                    points = part.get("points_image_px", [])
                    north = (trace_registry.get("page_north", {}) or {}).get(str(part.get("page")), {})
                    plan_up_bearing = north.get("plan_up_azimuth_deg")
                    part_unknown_edges = []
                    if roof == "exposed":
                        if area and not skip_roof:
                            proposal["surfaces"].append({
                                "surface_key": f"reviewer-trace:{part['trace_id']}:roof",
                                "label": f"{room.get('label', 'Room')} traced footprint roof",
                                "owner_room_label": room.get("label", ""), "owner_level_name": room.get("level_name", ""),
                                "physical_type": "roof", "thermal_role": "external", "external_exposure": "external",
                                "orientation": "horizontal", "gross_area_m2": area,
                                "opening_coverage": "not_applicable", "confidence": 0.65,
                                "page": part.get("page"), "evidence": [citation],
                                "reviewer_trace_id": part.get("trace_id", ""), "reviewer": declaration_reviewer,
                                "verification_status": "provisional",
                                "rationale": "Reviewer-declared exposed roof over the calibrated room trace; assumes a flat roof over the traced footprint.",
                                "assumptions": ["flat_roof_over_traced_footprint"],
                                **({"phase1_assumption": roof_note} if roof_note else {}),
                            })
                        if area:
                            assessment["not_assessed"].append({
                                "component_id": f"roof_solar{part_component_suffix}", "component": "Roof sun — not assessed",
                                "reason": "Roof solar gain not assessed — no cited horizontal solar profile; conduction only.",
                                "page": part.get("page"),
                            })
                    elif roof == "not_exposed" and area:
                        # A floor or other conditioned space above: itemised at the room's temperature (0 kW).
                        proposal["surfaces"].append({
                            "surface_key": f"reviewer-trace:{part['trace_id']}:ceiling",
                            "label": f"{room.get('label', 'Room')} ceiling (floor above)",
                            "owner_room_label": room.get("label", ""), "owner_level_name": room.get("level_name", ""),
                            "physical_type": "ceiling", "thermal_role": "conditioned_adjacent", "external_exposure": "",
                            "boundary_condition": "floor_above", "orientation": "", "gross_area_m2": area,
                            "boundary_source": "A floor or other conditioned space above, at the room's temperature: no heat flows.",
                            "opening_coverage": "not_applicable", "confidence": 0.65, "page": part.get("page"),
                            "evidence": [citation], "reviewer_trace_id": part.get("trace_id", ""), "reviewer": declaration_reviewer,
                            "verification_status": "provisional", "rationale": "Ceiling under a floor or other space above.",
                            **({"phase1_assumption": roof_note} if roof_note else {}),
                        })
                    if area:
                        proposal["surfaces"].append(_floor_surface(room, part, area, floor_basis, citation, declaration_reviewer))
                    # Walls the AI couldn't classify: one along another traced room faces that room; the rest are
                    # taken as a neighbouring tenancy (the usual case for a shop), both at the same temperature.
                    unknown = [edge.get("index") for edge in edges if edge.get("boundary", "unknown") == "unknown"]
                    along_rooms = _edges_along_other_rooms(part, unknown, outlines_by_page, mm_per_px)
                    unknown_note = ""
                    if unknown:
                        numbers = ", ".join(str(index + 1) for index in unknown)
                        unknown_note = (f"What {'wall' if len(unknown) == 1 else 'walls'} {numbers} of "
                                        f"{room.get('label', 'this room')} {'faces' if len(unknown) == 1 else 'face'} wasn't found "
                                        "on the drawings: a wall along another traced room is counted as a wall to that room, "
                                        "the others as walls to a neighbouring tenancy, all at the room's temperature.")
                    for edge in edges:
                        index, boundary = edge.get("index"), edge.get("boundary", "unknown")
                        wall_note = ""
                        if boundary == "unknown":
                            part_unknown_edges.append(index)
                            boundary = "internal" if index in along_rooms else "adjacent_tenancy"
                            wall_note, unknown_note = unknown_note, ""      # said once per room
                        if boundary in {"adjacent_tenancy", "internal", "mall"}:
                            boundary_label = str(edge_evidence.get(index, {}).get("label") or declaration_reviewer)
                            if mm_per_px and isinstance(points, list) and index < len(points) - 1:
                                length_m = math.dist(points[index], points[index + 1]) * mm_per_px / 1000.0
                                faces = {"mall": "the mall", "adjacent_tenancy": "a neighbouring tenancy",
                                         "internal": "another room"}[boundary]
                                proposal["surfaces"].append({
                                    "surface_key": f"reviewer-trace:{part['trace_id']}:adjacent-wall:{index}",
                                    "label": f"{room.get('label', 'Room')} wall {index + 1} to {faces}",
                                    "owner_room_label": room.get("label", ""), "owner_level_name": room.get("level_name", ""),
                                    "physical_type": "wall", "thermal_role": "conditioned_adjacent", "external_exposure": "",
                                    "boundary_condition": boundary, "orientation": "",
                                    "gross_area_m2": length_m * height_mm / 1000.0, "opening_coverage": "not_applicable",
                                    # A wall between two rooms is a partition, not a demising or outside wall.
                                    **({"construction_id": partition["construction_id"],
                                        "u_value_w_m2k": partition["u_value_w_m2k"]} if boundary == "internal" else {}),
                                    "boundary_source": f"Faces {faces}, air-conditioned to the same temperature: no heat flows "
                                                       "(Phase 1 rule, 2026-10-15).",
                                    "confidence": 0.65, "page": part.get("page"), "evidence": [citation],
                                    "reviewer_trace_id": part.get("trace_id", ""), "reviewer": boundary_label,
                                    "verification_status": "provisional", "rationale": f"Wall to {faces}.",
                                    **({"phase1_assumption": " ".join(filter(None, [wall_note, height_note]))}
                                       if wall_note or height_note else {}),
                                })
                                height_note = ""      # said once per room
                            if boundary == "mall" and any(row.get("edge_index") == index for row in part.get("openings", [])):
                                assessment["excluded"].append({
                                    "component_id": f"mall_shopfront_glazing_{index}{part_component_suffix}",
                                    "component": "glazing and façade solar",
                                    "reason": "Shopfront glazing faces an enclosed mall — no sun or conduction in this method",
                                    "page": part.get("page"), "reviewer": boundary_label,
                                    "boundary_source": edge_evidence.get(index, {}).get("source", "")})
                            continue
                        if boundary not in {"external", "unconditioned"}:
                            continue
                        wall_id = "external_wall_edge" if boundary == "external" else "unconditioned_wall_edge"
                        wall_name = "External wall" if boundary == "external" else "Wall to an unconditioned space"
                        if not mm_per_px or not isinstance(points, list) or index >= len(points) - 1:
                            assessment["not_assessed"].append({"component_id": f"{wall_id}_{index}{part_component_suffix}",
                                "component": f"{wall_name} area — not assessed",
                                "reason": "Calibrated trace edge length is unavailable; wall area cannot be derived.", "page": part.get("page")})
                            continue
                        length_m = math.dist(points[index], points[index + 1]) * mm_per_px / 1000.0
                        wall_area = length_m * height_mm / 1000.0
                        if wall_area <= 0 or not math.isfinite(wall_area):
                            assessment["not_assessed"].append({"component_id": f"{wall_id}_{index}{part_component_suffix}",
                                "component": f"{wall_name} area — not assessed",
                                "reason": "Calibrated trace edge produced no positive wall area.", "page": part.get("page")})
                            continue
                        if skip_walls:
                            continue
                        if boundary == "unconditioned":
                            # Conducted against the operators' answered temperature of the space beyond; without an
                            # answer, the method's cited buffer-space rule (outdoor temperature minus 3 K) is used and
                            # listed as an assumption, so the wall is counted rather than left out.
                            temperature, answer = need_answers_service.unconditioned_temperature(paths["root"], room.get("label", ""))
                            if temperature is None:
                                proposal["surfaces"].append({
                                    "surface_key": f"reviewer-trace:{part['trace_id']}:unconditioned-wall:{index}",
                                    "label": f"{room.get('label', 'Room')} wall {index + 1} to an unconditioned space",
                                    "owner_room_label": room.get("label", ""), "owner_level_name": room.get("level_name", ""),
                                    "physical_type": "wall", "thermal_role": "fixed_adjacent", "external_exposure": "",
                                    "boundary_condition": "unconditioned_space", "orientation": "", "gross_area_m2": wall_area,
                                    "opening_coverage": "not_applicable", "confidence": 0.5, "page": part.get("page"),
                                    "evidence": [citation], "reviewer_trace_id": part.get("trace_id", ""),
                                    "reviewer": declaration_reviewer, "verification_status": "provisional",
                                    "rationale": "Wall to an unconditioned space; no temperature given, so the standard buffer-space estimate is used.",
                                    **({"phase1_assumption": height_note} if height_note else {}),
                                })
                                height_note = ""
                                continue
                            proposal["surfaces"].append({
                                "surface_key": f"reviewer-trace:{part['trace_id']}:unconditioned-wall:{index}",
                                "label": f"{room.get('label', 'Room')} wall {index + 1} to an unconditioned space",
                                "owner_room_label": room.get("label", ""), "owner_level_name": room.get("level_name", ""),
                                "physical_type": "wall", "thermal_role": "fixed_adjacent", "external_exposure": "",
                                "boundary_condition": "unconditioned_space", "boundary_temperature_c": temperature,
                                "boundary_source": (f"Answered by the operators ({answer.get('source') or 'source not given'})"
                                                    + (f": {answer['space']}" if answer.get("space") else "")),
                                "orientation": "", "gross_area_m2": wall_area, "opening_coverage": "not_applicable", "confidence": 0.65,
                                "page": part.get("page"), "evidence": [citation],
                                "reviewer_trace_id": part.get("trace_id", ""), "reviewer": declaration_reviewer,
                                "verification_status": "provisional",
                                "rationale": f"Reviewer-declared wall to an unconditioned space at {temperature:g} °C (operators' answer); conduction only, no sun.",
                                "assumptions": ["answered_unconditioned_space_temperature"],
                            })
                            continue
                        surface_key = f"reviewer-trace:{part['trace_id']}:wall:{index}"
                        orientation = ""
                        if plan_up_bearing is not None:
                            orientation, _azimuth = reviewer_room_geometry.oriented_edge_cardinal(points, index, plan_up_bearing)
                        wall_openings = [row for row in part.get("openings", []) if row.get("edge_index") == index]
                        proposal["surfaces"].append({
                            "surface_key": surface_key,
                            "label": f"{room.get('label', 'Room')} traced external wall edge {index + 1}",
                            "owner_room_label": room.get("label", ""), "owner_level_name": room.get("level_name", ""),
                            "physical_type": "wall", "thermal_role": "external", "external_exposure": "external",
                            "orientation": orientation, "gross_area_m2": wall_area,
                            "opening_coverage": "reviewer_entered" if wall_openings else "not_applicable", "confidence": 0.65,
                            "page": part.get("page"), "evidence": [citation],
                            "reviewer_trace_id": part.get("trace_id", ""), "reviewer": declaration_reviewer,
                            "verification_status": "provisional",
                            "rationale": ("Reviewer-declared external boundary edge; outward orientation derived from the calibrated polygon winding and page north."
                                          if orientation else "Reviewer-declared external boundary edge; no north reference is available, so the documented west-facing preliminary solar profile is used."),
                            "assumptions": ([] if orientation else ["unknown_orientation_west_facing_solar_estimate"]),
                        })
                        for opening in wall_openings:
                            height = opening["head_height_m"] - opening["sill_height_m"]
                            proposal.setdefault("openings", []).append({
                                "opening_key": opening["opening_id"], "label": f"{room.get('label', 'Room')} shopfront opening",
                                "owner_room_label": room.get("label", ""), "owner_level_name": room.get("level_name", ""),
                                "host_surface_key": surface_key, "width_m": opening["width_m"], "height_m": height,
                                "quantity": 1, "opening_area_m2": opening["width_m"] * height,
                                "external_exposure": "external", "orientation": orientation,
                                "shading_category": opening["shading_category"], "glazing_choice": opening["glazing_choice"],
                                "preliminary_profile_id": opening["glazing_choice"], "page": opening["elevation_page"],
                                "evidence": [{"page": opening["elevation_page"], "reference": "Reviewer-cited shopfront elevation",
                                    "excerpt": f"Opening width {opening['width_m']} m; sill {opening['sill_height_m']} m; head {opening['head_height_m']} m.",
                                    "reviewer": declaration_reviewer, "reviewer_trace_id": part.get("trace_id", "")}],
                                "confidence": 0.65, "verification_status": "provisional",
                                "rationale": "Reviewer-entered opening geometry; glazing U-value and SHGC are preliminary assumption-pack values.",
                                "assumptions": ["preliminary_glazing_profile", "reviewer_cited_elevation_geometry"],
                            })
                    if part_unknown_edges:
                        pass  # each such wall is itemised as an assumed neighbouring tenancy, with its own note
                    fully_internal_parts.append(bool(edges) and roof == "not_exposed"
                        and all(edge.get("boundary") in {"internal", "adjacent_tenancy", "mall", "unknown"} for edge in edges))
                fully_internal = bool(fully_internal_parts) and all(fully_internal_parts)
                if fully_internal:
                    # Every wall and the ceiling face conditioned space: the room keeps its itemised 0 kW surfaces; the
                    # flag only adds the plain explanation to the result.
                    assessment["envelope_not_applicable"] = True
                if assessment["not_assessed"] or assessment["excluded"] or fully_internal:
                    envelope_assessments.append(assessment)
        geometry_row = geometry_by_id.get(room_id)
        if geometry_row:
            room["geometry"] = deepcopy(geometry_row.get("geometry", {}))
            room["page"] = geometry_row.get("page") or room.get("page")
            room["evidence"] = ai_preliminary._combined_evidence(room, geometry_row)
            room["geometry_candidate_status"] = "proposed"
            geometry_area = ai_preliminary._number(room["geometry"].get("area_m2"))
            calibration = room["geometry"].get("calibration", {})
            if (geometry_area and geometry_area > 0
                    and calibration.get("source") == "printed_floor_finish_area_sum"):
                # Keep a cited, arithmetic-checked area-only tenant candidate
                # eligible for the preliminary load draft. Its empty boundary
                # still prevents this value from fabricating wall or window
                # areas; those remain explicit envelope unknowns.
                room["area_m2"] = geometry_area
                room["area_origin"] = "ai_geometry"
                room["area_verification_status"] = "provisional"
    if envelope_assessments:
        proposal["envelope_assessments"] = envelope_assessments
    proposal = _merge_phase1_skill_proposals(paths, proposal)
    # A room explicitly outside the comfort-HVAC scope cannot contribute any
    # reviewer-traced or provider-proposed envelope surface to this subtotal.
    excluded_rooms = [row for row in proposal["rooms"] if isinstance(row, dict)
                      and row.get("space_scope") and not str(row.get("space_scope")).startswith("comfort_hvac")]
    excluded_owners = {(str(row.get("label", "")).casefold(), str(row.get("level_name", "")).casefold())
                       for row in excluded_rooms}
    excluded_owner_ids = {str(row.get("room_id", "")) for row in excluded_rooms if row.get("room_id")}
    if excluded_owners or excluded_owner_ids:
        proposal["surfaces"] = [surface for surface in proposal.get("surfaces", [])
            if str(surface.get("owner_room_id", "")) not in excluded_owner_ids
            and (str(surface.get("owner_room_label", "")).casefold(), str(surface.get("owner_level_name", "")).casefold())
            not in excluded_owners]
    # Make sure all stable identities have evidence pages even when their
    # detector supplied only source_pages. The normal validator still checks
    # that a physical page is present.
    for room in proposal["rooms"]:
        if not isinstance(room, dict):
            continue
        if not room.get("page"):
            pages = room.get("source_pages", [])
            if isinstance(pages, list) and pages:
                room["page"] = pages[0]
        if not room.get("evidence") and room.get("page"):
            room["evidence"] = [{"page": room["page"], "excerpt": room.get("label", "Room candidate")}]
    # Room identity is useful evidence, but the strict placeholder proposal
    # contract is calculation-facing. Keep identity-only rows in the domain
    # resolvers and geometry review queue; do not pass them to the load model
    # until an explicit area or structured boundary exists.
    calculation_rooms, area_issues = [], []
    for room in proposal["rooms"]:
        if not isinstance(room, dict):
            continue
        has_area = ai_preliminary._number(room.get("area_m2")) is not None and ai_preliminary._number(room.get("area_m2")) > 0
        has_geometry = ai_preliminary._has_geometry_area_candidate(room)
        if has_area or has_geometry:
            calculation_rooms.append(room)
            continue
        area_issues.append({
            "component": "room area", "room_id": str(room.get("room_id", "")),
            "label": str(room.get("label", "")), "level": str(room.get("level_name", "")),
            "scope": str(room.get("space_scope", "unresolved_scope")),
            "reason": "Room identity was detected, but no validated area or structured boundary is available; it is excluded from this draft subtotal.",
            "evidence": deepcopy(room.get("evidence", [])),
            "remediation": "Trace and calibrate the room, then accept its area in the calculator draft.",
        })
    proposal["rooms"] = calculation_rooms
    proposal.setdefault("issues", []).extend(area_issues)
    if trace_review_issue:
        proposal["issues"].append(trace_review_issue)
    from backend import need_answers_service
    proposal = need_answers_service.apply_glazing(paths["root"], proposal)  # answered glass performance, where given
    return need_answers_service.apply_constructions(paths["root"], proposal)  # answered wall and roof U-values


def _queue_missing_research(paths, project, approved):
    """Create bounded lookup manifests; this never fetches external content."""
    current = _resolution(paths)
    if approved:
        current["research_consent"] = True
        _save_resolution(paths, value_resolver.validate_value_resolution(current), "value_resolution_research_consent")
    if not current["research_consent"]:
        raise ValueError("Approve external source research before queueing missing-value lookups.")
    artifact = _resolve_from_packs(paths, project)
    current = _resolution(paths)
    research_fallbacks = {
        "scenario.weather_profile", "room.outside_air_lps_per_person", "room.outside_air_lps_per_m2",
        "room.wall_u_value_w_m2k", "room.roof_u_value_w_m2k", "room.glazing_u_value_w_m2k",
        "room.glazing_shgc", "room.equipment_w_m2",
    }
    candidates = [record for record in artifact.get("records", [])
                  if record.get("status") == "excluded" or
                  (record.get("origin") == "preliminary_fallback" and record.get("target") in research_fallbacks)]
    for record in candidates[:40]:
        if record.get("target") not in value_resolver.ALLOWED_TARGETS:
            continue
        current = value_resolver.queue_research_job(
            current, record["target"], record["target_id"], record.get("context", {}), record.get("rationale", "")
        )
    _save_resolution(paths, current, "value_resolution_research_queued")
    return current


def _settings(paths):
    return ai_preliminary.validate_settings(_read(paths["settings"], ai_preliminary.empty_settings()))


def _save_settings(paths, incoming):
    current = _settings(paths)
    for key in ai_preliminary.empty_settings():
        if isinstance(incoming, dict) and key in incoming:
            current[key] = incoming[key]
    current["updated_at"] = ai_preliminary.now()
    current = ai_preliminary.validate_settings(current)
    _write(paths["settings"], current)
    return current


def _stale_reasons(paths, input_set):
    if not input_set:
        return []
    current = _sources(paths)
    previous = input_set.get("dependency_fingerprints", {})
    return [name for name, value in current.items() if previous.get(name) != value]


def _workspace_value_resolution(resolution):
    resolution = resolution if isinstance(resolution, dict) else {}
    records = resolution.get("records", []) if isinstance(resolution.get("records"), list) else []
    important = [row for row in records if isinstance(row, dict)
                 and (row.get("status") == "excluded" or row.get("origin") == "preliminary_fallback")]
    jobs = resolution.get("research_jobs", []) if isinstance(resolution.get("research_jobs"), list) else []
    fields = ("target_id", "target", "status", "origin", "rationale")
    return {
        "research_consent": bool(resolution.get("research_consent")),
        "coverage_summary": resolution.get("coverage_summary", {}),
        "record_count": len(records),
        "research_job_count": len(jobs),
        "records": [{key: row.get(key) for key in fields} for row in important[:8]],
    }


def _workspace_report(report):
    report = report if isinstance(report, dict) else {}
    names = {}
    for scenario in report.get("scenario_results", []) if isinstance(report.get("scenario_results"), list) else []:
        for room in scenario.get("rooms", []) if isinstance(scenario, dict) and isinstance(scenario.get("rooms"), list) else []:
            if isinstance(room, dict) and room.get("room_id") and room.get("name"):
                names[str(room["room_id"])] = str(room["name"])
    rows = {}
    for key in ("known_exclusions", "unresolved_room_inputs"):
        rows[key] = []
        items = report.get(key, []) if isinstance(report.get(key), list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            room_id = str(item.get("room_id", ""))
            if room_id and item.get("room_name"):
                names.setdefault(room_id, str(item["room_name"]))
            rows[key].append({field: item.get(field) for field in ("room_id", "room_name", "component_type", "component", "component_id", "reason")
                              if item.get(field) is not None})
    review_queue = report.get("review_queue", []) if isinstance(report.get("review_queue"), list) else []
    queue_fields = ("room_id", "field", "confidence_band", "rationale")
    confirmed = report.get("confirmed_rooms", []) if isinstance(report.get("confirmed_rooms"), list) else []
    confirmed_fields = ("label", "level", "area_m2", "area_origin", "area_quality_label", "source_pages")
    confirmation = report.get("room_scope_confirmation", {})
    assumption_coverage = report.get("assumption_coverage")
    assumption_coverage = assumption_coverage if isinstance(assumption_coverage, dict) else {}
    return {
        "status": report.get("status", "not_calculated"),
        "report_type": report.get("report_type", ""),
        "calculation_phase": report.get("calculation_phase"),
        "blocked_reasons": [str(item) for item in (report.get("blocked_reasons", []) or [])[:5]],
        "label": report.get("label", ""),
        "included_scope_peak": report.get("included_scope_peak", {}),
        "assumption_coverage": {"low_confidence_count": assumption_coverage.get("low_confidence_count")},
        "review_queue": [{key: row.get(key) for key in queue_fields} for row in review_queue[:8] if isinstance(row, dict)],
        "unresolved_room_inputs": rows.get("unresolved_room_inputs", []),
        "known_exclusions": rows.get("known_exclusions", []),
        "excluded_spaces": [{key: item.get(key) for key in ("room_name", "level", "scope", "reason") if item.get(key) is not None}
                            for item in (report.get("excluded_spaces", []) or [])[:30] if isinstance(item, dict)],
        "room_names": [{"room_id": room_id, "name": name} for room_id, name in sorted(names.items())],
        "room_peaks": _room_peaks(report),
        "preliminary_surface_summary": report.get("preliminary_surface_summary", {}),
        "phase1_automation_note": str(report.get("phase1_automation_note", "")),
        "phase1_surface_audit": [item for item in (report.get("phase1_surface_audit", []) or [])[:200]
                                 if isinstance(item, dict)],
        "phase1_assumptions": [{**item, "room_name": names.get(str(item.get("room_id", "")), "")}
                                for item in (report.get("phase1_assumptions", []) or [])[:200] if isinstance(item, dict)],
        "design_conditions_basis": report.get("design_conditions_basis", {}),
        "refrigeration_process_exclusions": [{key: item.get(key) for key in ("room_name", "reason") if item.get(key) is not None}
                                              for item in report.get("refrigeration_process_exclusions", [])
                                              if isinstance(item, dict)],
        "confirmed_rooms": [{key: row.get(key) for key in confirmed_fields} for row in confirmed if isinstance(row, dict)],
        "phase1_coverage": report.get("phase1_coverage") if isinstance(report.get("phase1_coverage"), dict) else {},
        "room_scope_confirmation": {key: confirmation.get(key) for key in ("reviewer", "confirmed_at")
                                     if isinstance(confirmation, dict) and confirmation.get(key)},
    }


def _room_peaks(report):
    """Per-room design peak (kW) for the compact view: the contractor result lists cooling by room."""
    peaks = []
    for scenario in report.get("scenario_results", []) if isinstance(report.get("scenario_results"), list) else []:
        for room in scenario.get("rooms", []) if isinstance(scenario, dict) and isinstance(scenario.get("rooms"), list) else []:
            peak = room.get("peak") if isinstance(room, dict) else None
            if isinstance(peak, dict) and room.get("room_id") and isinstance(peak.get("design_total_kw"), (int, float)):
                peaks.append({"room_id": str(room["room_id"]), "name": str(room.get("name", "")),
                              "design_total_kw": peak["design_total_kw"], "hour": peak.get("display_hour", peak.get("hour"))})
        break  # the included-scope peak comes from the first (design) scenario
    return peaks


def _response(web, project, response_view="full", check_freshness=None):
    if response_view not in {"full", "workspace"}:
        raise ValueError("Response view must be full or workspace.")
    if check_freshness is None:
        # The workspace needs room candidates before the expensive geometry
        # freshness rebuild. The browser follows with a compact status check.
        check_freshness = response_view != "workspace"
    paths = _paths(project)
    settings = _settings(paths)
    input_set = _read(paths["current_set"], {})
    report = _read(paths["report"], {})
    base_report = _read(paths["base_report"], {})
    stale_reasons = _stale_reasons(paths, input_set) if check_freshness else []
    base_report_stale = bool(base_report and (stale_reasons or
                                base_report.get("preliminary_input_set_fingerprint") != input_set.get("input_fingerprint")))
    run = _read(paths["run"], {})
    value_resolution = _resolution(paths)
    link_names = ({"codex_handoff", "codex_response"} if response_view == "workspace" else
                  {"settings", "run", "model", "current_set", "report", "base_report", "codex_handoff", "codex_response", "value_resolution", "room_use", "room_scope", "ceiling_volume", "internal_gains", "airflow", "ahu_resolution", "plant_resolution", "safety_factor_resolution"})
    artifact_links = {name: web.safe_link(path) for name, path in paths.items()
                      if name in link_names and path.exists()}
    if response_view == "workspace":
        workspace_run = {key: run.get(key) for key in ("status", "message", "manual_placeholder_proposal",
                          "manual_placeholder_entities", "codex_handoff_status") if run.get(key) is not None}
        return {
            "id": project["id"], "settings": settings, "run": workspace_run,
            "hourly_ai_preliminary_load_report": _workspace_report(report),
            "phase1_base_room_load_report": _workspace_report(base_report),
            "phase1_base_room_load_report_stale": base_report_stale,
            "status": ("stale" if stale_reasons else ("current" if input_set else "not_calculated"))
                      if check_freshness else "freshness_pending",
            "freshness_pending": not check_freshness,
            "stale_reasons": stale_reasons,
            "provider_configured": bool(os.environ.get("OPENAI_API_KEY")),
            "value_resolution": _workspace_value_resolution(value_resolution),
            "room_scope": _room_scope_state(paths, input_set),
            "artifact_links": artifact_links,
        }
    return {
        "id": project["id"], "settings": settings, "run": run,
        "model": _read(paths["model"], {}), "input_set": input_set,
        "hourly_ai_preliminary_load_report": report,
        "phase1_base_room_load_report": base_report,
        "phase1_base_room_load_report_stale": base_report_stale,
        "status": "stale" if stale_reasons else ("current" if input_set else "not_calculated"),
        "stale_reasons": stale_reasons,
        "provider_configured": bool(os.environ.get("OPENAI_API_KEY")),
        "value_resolution": value_resolution,
        "room_use_resolution": room_use_resolution.validate(_read(paths["room_use"], room_use_resolution.empty_room_use_resolution())),
        "ceiling_volume_resolution": ceiling_volume_resolution.validate(_read(paths["ceiling_volume"], ceiling_volume_resolution.empty_ceiling_volume_resolution())),
        "internal_gains_resolution": internal_gains_resolution.validate(_read(paths["internal_gains"], internal_gains_resolution.empty_internal_gains_resolution())),
        "airflow_resolution": airflow_resolution.validate(_read(paths["airflow"], airflow_resolution.empty_airflow_resolution())),
        "ahu_resolution": ahu_resolution.validate(_read(paths["ahu_resolution"], ahu_resolution.empty_ahu_resolution())),
        "plant_resolution": plant_resolution.validate(_read(paths["plant_resolution"], plant_resolution.empty_plant_resolution())),
        "safety_factor_resolution": safety_factor_resolution.validate(_read(paths["safety_factor_resolution"], safety_factor_resolution.empty_safety_factor_resolution())),
        "room_scope": _room_scope_state(paths, input_set),
        "artifact_links": artifact_links,
    }


def get(web, project, response_view="full", check_freshness=None):
    return _response(web, project, response_view, check_freshness)


class RoomScopeNotConfirmed(ValueError):
    """The draft load needs a current reviewer-confirmed room list."""


def _room_use_artifact(paths):
    return _read(paths["room_use"], room_use_resolution.empty_room_use_resolution())


def _room_scope_state(paths, input_set):
    if not input_set:
        return {"status": "not_available", "candidates": [], "candidate_fingerprint": "", "confirmation": None, "uses": {}}
    current = room_scope_confirmation.state(input_set, _read(paths["room_scope"], None), _room_use_artifact(paths))
    try:
        categories = room_use_resolution.load_taxonomy()["categories"]
        current["uses"] = {key: row["label"] for key, row in categories.items()}
    except ValueError:
        current["uses"] = {}
    return current


def _confirm_room_scope(paths, data):
    input_set = _read(paths["current_set"], {})
    if not input_set:
        raise ValueError("Resolve model inputs before confirming the room list.")
    stale = _stale_reasons(paths, input_set)
    if stale:
        raise ValueError("The draft model is out of date (" + ", ".join(stale) + " changed). Resolve model inputs again, then confirm the room list.")
    confirmation = room_scope_confirmation.confirm(input_set, _room_use_artifact(paths), data, ai_preliminary.now())
    _write(paths["room_scope"], confirmation)
    return confirmation


def _prepare_codex_handoff(web, project):
    """Write a local, source-linked request for the Codex development workflow."""
    paths = _paths(project)
    building = _read(paths["building"], {})
    if not building:
        raise ValueError("Analyse the PDF before preparing a local Codex handoff.")
    handoff = ai_preliminary.build_local_codex_handoff(
        project,
        building,
        _read(paths["vision"], {}),
        _read(paths["fusion"], {}),
        _sources(paths),
    )
    _write(paths["codex_handoff"], handoff)
    _write(paths["codex_response"], ai_preliminary.empty_local_codex_response(handoff))
    prior = _read(paths["run"], {})
    _write(paths["run"], {**prior, "schema_version": 1, "codex_handoff_fingerprint": handoff["handoff_fingerprint"],
                           "codex_handoff_prepared_at": ai_preliminary.now(), "codex_handoff_status": "awaiting_local_response"})
    return handoff


def _apply_codex_response(web, project):
    """Apply a Codex-written response only if it still matches the source handoff."""
    paths = _paths(project)
    handoff = _read(paths["codex_handoff"], {})
    response = _read(paths["codex_response"], {})
    if not handoff or not response:
        raise ValueError("Prepare a local Codex handoff before applying a response.")
    if handoff.get("source_fingerprints") != _sources(paths):
        raise ValueError("The local Codex handoff is stale because its source evidence changed. Prepare it again.")
    if response.get("handoff_fingerprint") != handoff.get("handoff_fingerprint"):
        raise ValueError("The local Codex response belongs to a different handoff. Prepare a new handoff and response template.")
    proposal = ai_preliminary.validate_placeholder_proposal(response.get("proposal"))
    prior = _read(paths["run"], {})
    _write(paths["run"], {**prior, "schema_version": 1, "status": "local_codex_response_ready", "source": "local_codex_placeholder",
                           "updated_at": ai_preliminary.now(), "manual_placeholder_entities": proposal["rooms"],
                           "manual_placeholder_proposal": proposal,
                           "local_room_inference_proposal": None,
                           "manual_placeholder_fingerprint": ai_preliminary.fingerprint(proposal),
                           "codex_handoff_status": "response_applied",
                           "previous_input_fingerprint": prior.get("input_fingerprint", "")})
    _rebuild_geometry_evidence(web, project)
    _resolve_room_uses(paths, persist=True)
    _resolve_ceiling_volumes(paths, persist=True)
    return proposal


def _assemble(web, project, source="manual_placeholder"):
    paths = _paths(project)
    building, vision = _read(paths["building"], {}), _read(paths["vision"], {})
    if not building:
        raise ValueError("Analyse the PDF before assembling an AI preliminary model.")
    # Use the same current skill findings that feed room-use resolution.
    # Previously assembly kept the base detector's shorter room list, so
    # newly inferred enclosed spaces could be classified but never get into
    # the room-by-room Phase 1 proposal.
    proposal = _proposal_for_resolution(paths)
    room_use = _resolve_room_uses(paths, persist=True)
    geometry = _preliminary_geometry(paths)
    ceiling_artifact = _resolve_ceiling_volumes(paths, persist=True)
    internal_gains_artifact = _resolve_internal_gains(paths, persist=True)
    airflow_artifact = _resolve_airflow(paths, persist=True)
    ahu_artifact = ahu_resolution.validate(_read(paths["ahu_resolution"], ahu_resolution.empty_ahu_resolution()))
    plant_artifact = plant_resolution.validate(_read(paths["plant_resolution"], plant_resolution.empty_plant_resolution()))
    safety_path = paths["safety_factor_resolution"]
    if safety_path.exists():
        safety_artifact = safety_factor_resolution.validate(_read(safety_path, safety_factor_resolution.empty_safety_factor_resolution()))
    else:
        safety_artifact = safety_factor_resolution.resolve({}, pack_fingerprint=ai_preliminary.fingerprint(ai_preliminary.load_pack()))
        _write(safety_path, safety_artifact)
        productization.record_change_if_fingerprint_changed(paths["root"], action="safety_factor_resolution_automatically_resolved", target=safety_path.name, previous_fingerprint="", new_fingerprint=safety_artifact["fingerprint"], affected_ids=["cooling", "heating"])
    resolution = _resolve_from_packs(paths, project)
    # _resolve_from_packs rebuilds these artifacts. Use their persisted latest
    # versions when materializing the input set so the dependency fingerprints
    # recorded in that set match the current project state exactly.
    room_use = _read(paths["room_use"], room_use)
    ceiling_artifact = _read(paths["ceiling_volume"], ceiling_artifact)
    internal_gains_artifact = _read(paths["internal_gains"], internal_gains_artifact)
    airflow_artifact = _read(paths["airflow"], airflow_artifact)
    geometry = _preliminary_geometry(paths)
    proposal = _prepare_preliminary_proposal(paths, proposal, room_use, geometry)
    from backend import need_answers_service
    input_set = ai_preliminary.assemble(
        building, vision, source_fingerprints=_sources(paths), preliminary_proposal=proposal,
        value_resolution=resolution, research_cache=validate_cache(_read(paths["research_cache"], empty_research_cache())),
        site_location=_read(paths["site_location"], {}),
        site_design_weather=_read(paths["site_design_weather"], {}),
        weather_location=weather_pick_service.effective(paths["root"])[0],
        weather_note=weather_pick_service.effective(paths["root"])[1],
        room_use_resolution=room_use,
        geometry_resolution=geometry,
        ceiling_volume_resolution=ceiling_artifact,
        internal_gains_resolution=internal_gains_artifact,
        airflow_resolution=airflow_artifact,
        ahu_resolution=ahu_artifact,
        plant_resolution=plant_artifact,
        process_exhaust=need_answers_service.process_exhaust(paths["root"]),
        # The operators' imported design-temperature table (kept outside the repo): a confirmed site's design days.
        design_weather_table=design_weather.load_table(),
        # The consolidated real-PDF workflow must not turn a missing measured
        # room area into an 80/100 m2 profile assumption.  Older direct
        # preview callers retain their explicit legacy fallback behavior.
        allow_area_fallbacks=False,
    )
    input_set["safety_factor_resolution"] = safety_artifact
    input_set["run_source"] = source
    input_set["phase1_floor_basis"] = _floor_basis(_read(paths["root"] / "ai_input.json", {}))
    _write(paths["sets"] / f"{input_set['input_fingerprint']}.json", input_set)
    _write(paths["current_set"], input_set)
    model = {
        "schema_version": 1, "status": "draft", "label": input_set["label"],
        "input_fingerprint": input_set["input_fingerprint"], "topology": input_set["material"]["hourly_load_model"],
        "materialized_fields": input_set["materialized_fields"], "exclusions": input_set["exclusions"],
        "review_queue": input_set["review_queue"], "pack": input_set["pack"],
        "surface_summary": input_set.get("surface_summary", {}), "excluded_spaces": input_set.get("excluded_spaces", []),
        "value_resolution": input_set.get("value_resolution", {}),
        "geometry_resolution": input_set.get("geometry_resolution", {}),
        "ceiling_volume_resolution": input_set.get("ceiling_volume_resolution", {}),
        "internal_gains_resolution": input_set.get("internal_gains_resolution", {}),
        "airflow_resolution": input_set.get("airflow_resolution", {}),
        "ahu_resolution": input_set.get("ahu_resolution", {}),
        "plant_resolution": input_set.get("plant_resolution", {}),
        "safety_factor_resolution": input_set.get("safety_factor_resolution", {}),
        "dependency_fingerprints": input_set["dependency_fingerprints"],
    }
    _write(paths["model"], model)
    _write(paths["run"], {"schema_version": 1, "status": "assembled", "source": source, "finished_at": ai_preliminary.now(),
                           "input_fingerprint": input_set["input_fingerprint"], "provider_payload_stored": False,
                           # _write merges this status update into the existing run
                           # record. Keep the original AI/manual proposal fields
                           # intact so recalculating dependencies does not make
                           # the just-assembled input set immediately stale.
                           "assembled_proposal_fingerprint": ai_preliminary.fingerprint(input_set.get("proposal", {})),
                           "assumptions": input_set["materialized_fields"], "unresolved_components": input_set["exclusions"]})
    return input_set


def _calculate(web, project, scope="full"):
    paths = _paths(project)
    input_set = _read(paths["current_set"], {})
    if not input_set:
        raise ValueError("Assemble an AI preliminary model before calculating.")
    stale = _stale_reasons(paths, input_set)
    if stale:
        raise ValueError("AI preliminary inputs are stale because " + ", ".join(stale) + " changed. Assemble again before calculating.")
    required = {
        "project inputs": paths["root"] / "design_requirements.json",
        "schedule library": paths["root"] / "schedule_library.json",
        "design-day weather": paths["root"] / "design_day_scenarios.json",
        "hourly load model": paths["root"] / "hourly_load_model.json",
    }
    missing = [label for label, path in required.items() if not path.exists()]
    if missing:
        raise ValueError("Cannot calculate the preliminary load yet. Missing required artifacts: " + ", ".join(missing) + ".")
    candidates = []
    if scope == "phase1_base_room":
        candidates = room_scope_confirmation.candidates(input_set, _room_use_artifact(paths))
        has_calculable_room = any(row["status"] == "calculated" and row.get("area_m2") is not None
                                  for row in candidates)
        if has_calculable_room:
            rows = [{"key": row["key"], "include": row["status"] == "calculated" and row.get("area_m2") is not None,
                     "reason": "AI did not resolve a supported room use or area; excluded from this subtotal."
                     if row["status"] != "calculated" or row.get("area_m2") is None else ""}
                    for row in candidates]
            try:
                _confirm_room_scope(paths, {"reviewer": "AI Phase 1 scope resolver", "rows": rows,
                                            "candidate_fingerprint": room_scope_confirmation.candidate_fingerprint(candidates),
                                            "note": "Automatically included resolved rooms with areas; unresolved candidates remain listed as exceptions."})
            except ValueError as error:
                raise RoomScopeNotConfirmed(str(error)) from error
        if has_calculable_room:
            try:
                confirmed_set, confirmed_rooms, confirmation_summary = room_scope_confirmation.apply(
                    input_set, _read(paths["room_scope"], None), _room_use_artifact(paths))
            except ValueError as error:
                raise RoomScopeNotConfirmed(str(error)) from error
        else:
            confirmed_set, confirmed_rooms = input_set, []
            confirmation_summary = {"reviewer": "AI Phase 1 scope resolver", "confirmed_at": ai_preliminary.now(),
                                    "note": "No rooms had both a resolved use and area; see the room exceptions."}
    else:
        try:
            confirmed_set, confirmed_rooms, confirmation_summary = room_scope_confirmation.apply(
                input_set, _read(paths["room_scope"], None), _room_use_artifact(paths))
        except ValueError as error:
            raise RoomScopeNotConfirmed(str(error)) from error
    policy_artifact = _read(paths["safety_factor_resolution"], safety_factor_resolution.empty_safety_factor_resolution())
    policy = safety_factor_resolution.policy_for(policy_artifact, "cooling", preliminary=True)
    report = ai_preliminary.calculate(confirmed_set, safety_factor_policy=policy, scope=scope)
    report["confirmed_rooms"] = confirmed_rooms
    report["room_scope_confirmation"] = confirmation_summary
    if scope == "phase1_base_room":
        _phase1_coverage(report, candidates, confirmed_set)
    # Provenance is attached at render time; it does not change any hourly
    # load value or reviewed artifact.
    try:
        from ai import model_input_resolution as shared_resolution
        register_path = paths["root"] / "model_input_resolution.json"
        register = _read(register_path, _read(paths["value_resolution"], {"records": []}))
        report["provenance"] = shared_resolution.build_report_provenance(report, register)
    except (OSError, TypeError, ValueError):
        report["provenance"] = {"schema_version": 1, "register_fingerprint": "", "by_component_id": {}, "governing_components": []}
    _write(paths["base_report"] if scope == "phase1_base_room" else paths["report"], report)
    return report


def _outlines_by_page(records):
    """{page: [(room_id, polygon)]} of the current room outlines, to tell which walls run along another room."""
    from shapely.geometry import Polygon
    result = {}
    for row in records or []:
        points = row.get("points_image_px") if isinstance(row, dict) else None
        if isinstance(points, list) and len(points) >= 4:
            polygon = Polygon(points)
            if polygon.is_valid and polygon.area > 0:
                result.setdefault(row.get("page"), []).append((str(row.get("room_id") or ""), polygon))
    return result


def _edges_along_other_rooms(part, indices, outlines_by_page, mm_per_px, reach_mm=600):
    """The wall indices whose middle lies inside another room's outline or within reach_mm of it (a shared partition
    is drawn as two outlines a wall's thickness apart)."""
    from shapely.geometry import Point
    points = part.get("points_image_px") or []
    others = [polygon for room_id, polygon in outlines_by_page.get(part.get("page"), [])
              if room_id != str(part.get("room_id") or "")]
    if not others or not mm_per_px or not indices:
        return set()
    reach = reach_mm / mm_per_px
    result = set()
    for index in indices:
        if type(index) is not int or index >= len(points) - 1:
            continue
        middle = Point((points[index][0] + points[index + 1][0]) / 2, (points[index][1] + points[index + 1][1]) / 2)
        if any(polygon.contains(middle) or polygon.exterior.distance(middle) <= reach for polygon in others):
            result.add(index)
    return result


def _roof_assumption(paths, ai_input, site, room):
    """(roof, note) when no roof answer exists for a room: the AI roof task's most-likely rule over the same drawing
    facts (a slab or building above -> not exposed; otherwise exposed), so the roof is never left unassessed."""
    from ai import autonomous_tasks
    packet = autonomous_tasks.build_roof_facts(ai_input, site, {"room_id": room.get("room_id"), "room_label": room.get("label"),
                                                                "level_name": room.get("level_name")})
    roof, why = autonomous_tasks.roof_fallback(packet)
    return roof, why


def _floor_surface(room, part, area, floor_basis, citation, reviewer):
    """The room's floor as a surface: a ground slab (shown or assumed) adds no load under the ASHRAE preliminary rule;
    a floor over an unconditioned space uses the buffer-space estimate. Both are listed with their reason."""
    over_unconditioned = floor_basis.get("kind") == "unconditioned_below"
    return {"surface_key": f"reviewer-trace:{part['trace_id']}:floor",
            "label": f"{room.get('label', 'Room')} floor" + (" over an unconditioned space" if over_unconditioned else " (slab)"),
            "owner_room_label": room.get("label", ""), "owner_level_name": room.get("level_name", ""),
            "physical_type": "floor", "thermal_role": "fixed_adjacent" if over_unconditioned else "ground_contact",
            "external_exposure": "", "boundary_condition": "unconditioned_below" if over_unconditioned else "ground",
            "orientation": "", "gross_area_m2": area, "opening_coverage": "not_applicable", "confidence": 0.6,
            "page": part.get("page"), "evidence": [citation], "reviewer_trace_id": part.get("trace_id", ""),
            "reviewer": reviewer, "verification_status": "provisional",
            "rationale": floor_basis.get("reason", ""), "phase1_assumption": floor_basis.get("reason", "")}


_UNCONDITIONED_BELOW = re.compile(r"\b(?:car\s*park\s+(?:below|under)|(?:over|above)\s+(?:the\s+)?(?:car\s*park|basement|"
                                  r"loading\s+dock|void)|void\s+(?:below|under)|basement\s+below|suspended\s+(?:slab|floor))\b", re.I)
_ON_GROUND = re.compile(r"\b(?:slab\s+on\s+(?:ground|grade)|ground\s+(?:floor\s+)?slab|on[- ]grade\s+slab)\b", re.I)


def _floor_basis(ai_input):
    """What is under the tenancy's floor, for Phase 1 (user decision 2026-10-15): a ground slab adds no cooling load.
    "unconditioned_below" when the drawings show a car park, void, basement or suspended slab below (the floor stays
    an unresolved exception); otherwise a ground slab, from the drawings or assumed as the most likely case for a shop.
    Returns {"kind", "reason", "page"}."""
    pages = (ai_input or {}).get("drawing_set", {}).get("pages", []) if isinstance(ai_input, dict) else []
    shown = None
    for page in pages:
        if not isinstance(page, dict):
            continue
        text = " ".join(str((page.get("structured_content") or {}).get("markdown", "")).split())
        below = _UNCONDITIONED_BELOW.search(text)
        if below:
            excerpt = text[max(0, below.start() - 60):below.end() + 60]
            return {"kind": "unconditioned_below", "page": page.get("page"),
                    "reason": f"The drawings show an unconditioned space below (p. {page.get('page')}: “{excerpt}”); "
                              "the floor needs its construction and the space's temperature."}
        ground = _ON_GROUND.search(text)
        if ground and shown is None:
            shown = {"page": page.get("page"), "excerpt": text[max(0, ground.start() - 40):ground.end() + 40]}
    if shown:
        return {"kind": "ground", "page": shown["page"],
                "reason": f"Slab on ground (p. {shown['page']}: “{shown['excerpt']}”): no cooling load through it."}
    return {"kind": "assumed_ground", "page": None,
            "reason": "Assumed: the floor is a slab on the ground or over another conditioned space (most likely for a "
                      "shop; the drawings don't say), so it adds no cooling load."}


def _phase1_coverage(report, candidates, input_set=None):
    """Say how much of the job a Phase 1 subtotal covers, so a partial or empty one is never read as the answer.

    report["phase1_coverage"] lists the rooms included and the rooms left out (with why). When the included rooms
    have no wall, roof or window resolved, there is no envelope load to report: the result is blocked with that
    reason instead of showing 0 kW (an island bar with no outside walls once came out as a 0.0 kW "result"). A room
    whose every boundary resolved to conditioned space (a floor above, neighbours, a mall) is different: its envelope
    load really is zero, so it counts as resolved and the result says why."""
    rooms = (((input_set or {}).get("material") or {}).get("hourly_load_model") or {}).get("rooms", [])
    all_internal = sorted(str(room.get("name") or "") for room in rooms if isinstance(room, dict)
                          and (room.get("cooling_load") or {}).get("envelope_not_applicable") is True)
    included = [row["label"] for row in candidates if row.get("status") == "calculated" and row.get("area_m2") is not None]
    left_out = [{"label": row["label"],
                 "reason": "no area yet" if row.get("area_m2") is None else "its use isn't resolved"}
                for row in candidates if row["label"] not in included]
    summary = report.get("preliminary_surface_summary") or {}
    surfaces = int(summary.get("included") or 0) + int(summary.get("openings_included") or 0)
    internal = [name for name in included if name in all_internal]
    resolved = surfaces > 0 or (bool(included) and len(internal) == len(included))
    report["phase1_coverage"] = {"rooms_found": len(candidates), "rooms_included": included, "rooms_left_out": left_out,
                                 "surfaces_included": surfaces, "rooms_fully_internal": internal,
                                 "complete": bool(included) and not left_out and resolved}
    if internal:
        report["phase1_automation_note"] = " ".join(filter(None, [
            str(report.get("phase1_automation_note") or ""),
            f"{_names(internal)}: every wall and the ceiling face conditioned space (a floor above, neighbours or a "
            f"mall), so {'its' if len(internal) == 1 else 'their'} envelope load is 0 kW."]))
    if included and not resolved:
        missing = f" {_names(row['label'] for row in left_out)} {'has' if len(left_out) == 1 else 'have'} no area yet." if left_out else ""
        report["status"] = "blocked"
        report["blocked_reasons"] = [f"No wall, roof or window of {_names(included)} could be resolved, so there is no "
                                     f"envelope load to report yet.{missing}"] + list(report.get("blocked_reasons") or [])


def _names(names):
    names = list(names)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1] if names else ""


def _auto_start_provider(web, project):
    """Start the existing bounded provider job only when all consent guards pass."""
    paths = _paths(project)
    settings = _settings(paths)
    configured = bool(os.environ.get("OPENAI_API_KEY"))
    status = ai_preliminary.provider_eligibility(settings, configured)
    if status != "eligible":
        _write(paths["run"], {"schema_version": 1, "status": status, "updated_at": ai_preliminary.now(),
                               "message": "No provider request was created."})
        return status
    # The evidence-only provider remains authoritative for transport, budget,
    # request manifests, and input redaction. The preliminary layer only asks
    # it to run using its already-bounded page selection.
    from backend import vision_extraction_service
    state = vision_extraction_service.get(web, project)
    group_ids = [item["group_id"] for item in state.get("available_groups", [])]
    if not group_ids:
        _write(paths["run"], {"schema_version": 1, "status": "awaiting_provider", "updated_at": ai_preliminary.now(),
                               "message": "No eligible ranked evidence groups are available."})
        return "awaiting_provider"
    try:
        vision_extraction_service.post(web, project, {"action": "start", "settings": {
            "owner_opt_in": True, "selected_group_ids": group_ids,
        }})
    except Exception as error:
        _write(paths["run"], {"schema_version": 1, "status": "provider_pending", "updated_at": ai_preliminary.now(), "message": str(error)[:500]})
        return "provider_pending"
    _write(paths["run"], {"schema_version": 1, "status": "provider_running", "updated_at": ai_preliminary.now(),
                           "message": "The bounded evidence-only provider job has started; preliminary assembly follows successful validation."})
    return "provider_running"


def after_pdf_analysis(web, project):
    """Queue local room detection before any downstream resolver runs.

    Room inference is local and does not require AI-provider consent.  The
    persisted job invokes the shared model-input resolver after its normalized
    room-use and geometry artifacts are written.  Provider-backed work remains
    a separate, explicitly opted-in path.
    """
    if not project.get("review_dir"):
        return "not_available"
    try:
        from backend import room_inference_service
        room_inference_service.post(web, project, {"action": "start"})
    except (ValueError, KeyError, OSError, TypeError, AttributeError):
        # The UI exposes a retry/remediation path; analysis itself remains
        # successful even when local room extraction cannot start.
        pass
    return _auto_start_provider(web, project)


def provider_completed(web, project):
    """Called only after the existing provider result is validated/materialized."""
    paths = _paths(project)
    settings = _settings(paths)
    if not settings["automatic_analysis_enabled"]:
        return "disabled"
    try:
        from backend import model_input_resolution_service
        model_input_resolution_service.post(web, project, {"action": "resolve"})
    except (ValueError, KeyError, OSError, TypeError, AttributeError):
        pass
    _resolve_room_uses(_paths(project), persist=True)
    _assemble(web, project, source="provider")
    try:
        _calculate(web, project)
    except RoomScopeNotConfirmed:
        # The provider can propose rooms, but only a reviewer can confirm
        # which ones make up the draft total.
        return "awaiting_room_confirmation"
    return "completed"


def post(web, project, data):
    paths = _paths(project)
    action = data.get("action", "get")
    # The UI submits the current settings with every action. Persisting them
    # here prevents a stale form from silently starting a provider job under
    # older consent or budget values.
    if action != "get" and isinstance(data.get("settings"), dict):
        _save_settings(paths, data["settings"])
    if action == "save_settings":
        pass
    elif action == "resolve_from_packs":
        current = _resolution(paths)
        if "research_consent" in data:
            current["research_consent"] = bool(data["research_consent"])
            _save_resolution(paths, value_resolver.validate_value_resolution(current), "value_resolution_research_consent")
        _resolve_from_packs(paths, project)
    elif action == "queue_source_research":
        current = _resolution(paths)
        if "research_consent" in data:
            current["research_consent"] = bool(data["research_consent"])
        updated = value_resolver.queue_research_job(current, data.get("target", ""), data.get("target_id", ""), data.get("context", {}), data.get("reason", ""))
        _save_resolution(paths, updated, "value_resolution_research_queued", [data.get("target_id", "")])
    elif action == "queue_missing_source_research":
        _queue_missing_research(paths, project, bool(data.get("research_consent", False)))
    elif action == "accept_project_source":
        current = _resolution(paths)
        updated = value_resolver.add_project_source(current, data.get("project_source", {}))
        _save_resolution(paths, updated, "value_resolution_project_source_accepted", [data.get("project_source", {}).get("target_id", "")])
    elif action == "apply_override":
        current = _resolution(paths)
        updated = value_resolver.add_override(current, data.get("override", {}))
        _save_resolution(paths, updated, "value_resolution_override_applied", [data.get("override", {}).get("target_id", "")])
    elif action == "rebuild_preliminary_model":
        _assemble(web, project, source="value_resolution")
    elif action == "assemble":
        _assemble(web, project)
    elif action == "calculate":
        _calculate(web, project)
    elif action == "confirm_room_scope":
        _confirm_room_scope(paths, data)
    elif action == "run":
        _auto_start_provider(web, project)
    elif action == "prepare_codex_handoff":
        _prepare_codex_handoff(web, project)
    elif action == "apply_codex_response":
        _apply_codex_response(web, project)
    elif action in {"save_manual_placeholder", "save_placeholder_proposal"}:
        raw_proposal = data.get("placeholder_proposal") if action == "save_placeholder_proposal" else data.get("manual_placeholder_entities")
        proposal = ai_preliminary.validate_placeholder_proposal(raw_proposal)
        prior = _read(paths["run"], {})
        _write(paths["run"], {"schema_version": 1, "status": "manual_placeholder_ready", "source": "manual_placeholder",
                               "updated_at": ai_preliminary.now(), "manual_placeholder_entities": proposal["rooms"],
                               "manual_placeholder_proposal": proposal,
                               "local_room_inference_proposal": None,
                               "manual_placeholder_fingerprint": ai_preliminary.fingerprint(proposal),
                               "previous_input_fingerprint": prior.get("input_fingerprint", "")})
        _resolve_room_uses(paths, persist=True)
    else:
        raise ValueError("AI preliminary action must be save_settings, save_manual_placeholder, save_placeholder_proposal, prepare_codex_handoff, apply_codex_response, resolve_from_packs, queue_source_research, queue_missing_source_research, accept_project_source, apply_override, rebuild_preliminary_model, run, assemble, confirm_room_scope, or calculate.")
    project["updated_at"] = ai_preliminary.now()
    web.update_project(project)
    return _response(web, project, data.get("response_view", "full"), check_freshness=True)
