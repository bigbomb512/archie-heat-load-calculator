"""Versioned evidence review and additive calculator changes. No new physics."""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import re

from ai.building_evidence import slug
from ai.site_design_conditions import validate_citations
from ai.hourly_loads import (
    DAY_TYPES, empty_hourly_load_model, empty_schedule_library,
    validate_hourly_load_model, validate_schedule_library,
)
from ai.envelope import (
    empty_envelope_library, empty_envelope_model, validate_envelope_library,
    validate_envelope_model, CONSTRUCTION_KINDS,
)

DECISIONS = {"accept", "edit", "reject", "needs_evidence", "pending"}
GROUPS = ("floors", "zones", "rooms", "room_inputs", "schedules", "envelope")


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class DraftConflict(ValueError):
    def __init__(self, message, code="revision_conflict"):
        super().__init__(message)
        self.code = code


def empty_calculator_draft():
    return {"schema_version": 2, "revision": 0, "status": "not_built", "updated_at": "",
            "candidates": {group: [] for group in GROUPS}, "review_items": [],
            "source_artifacts": {}, "source_fingerprints": {}, "decisions": {},
            "review_history": [], "application_receipts": [], "page_roles": [],
            "evidence_summary": {}, "evidence_fusion": {}, "readiness": {"status": "blocked", "issues": []}}


def all_candidates(draft):
    return [item for group in GROUPS for item in draft.get("candidates", {}).get(group, [])]


def citations(evidence, document):
    rows = [{"reference": item.get("reference") or document,
             "page": item.get("page"), "excerpt": item.get("excerpt", "")}
            for item in evidence or [] if isinstance(item, dict)]
    checked = validate_citations(rows, "Proposal") if rows else []
    return sorted(checked, key=lambda row: (str(row.get("reference", "")), row.get("page") or 0, row.get("excerpt", "")))


def supported_citations(rows):
    return bool(rows) and all(row.get("reference") and row.get("excerpt") for row in rows)


def quantity(raw, unit):
    """Accept explicit units; never extract a number from an arbitrary label."""
    if isinstance(raw, dict) and raw.get("unit") == unit:
        value = raw.get("value")
    elif isinstance(raw, str):
        patterns = {"m2": r"m(?:2|²)", "mm": "mm", "W": "W"}
        match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*" + patterns[unit] + r"\s*", raw, re.I)
        value = float(match[1]) if match else None
    else:
        value = None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        return None
    return value


def build_calculator_draft(thermal_model, building_evidence, drawing_coverage, previous=None,
                           source_artifacts=None, thermal_evidence=None, evidence_fusion=None):
    previous = previous or {}
    thermal_evidence = thermal_evidence or {}
    draft = empty_calculator_draft()
    draft.update(revision=previous.get("revision", 0) + 1, status="review_required", updated_at=timestamp(),
                 source_artifacts=source_artifacts or {},
                 source_fingerprints={name: fingerprint(value) for name, value in {
                     "thermal_model": thermal_model, "building_evidence": building_evidence,
                     "drawing_coverage": drawing_coverage, "thermal_evidence": thermal_evidence,
                     "evidence_fusion": evidence_fusion or {}}.items()},
                 review_history=deepcopy(previous.get("review_history", [])),
                 application_receipts=deepcopy(previous.get("application_receipts", [])),
                 page_roles=deepcopy((evidence_fusion or {}).get("pages") or drawing_coverage.get("page_roles", [])),
                 evidence_fusion={"schema_version": (evidence_fusion or {}).get("schema_version"), "fingerprint": (evidence_fusion or {}).get("fingerprint", ""),
                                  "facts": deepcopy((evidence_fusion or {}).get("facts", [])), "fact_registry": deepcopy((evidence_fusion or {}).get("fact_registry", {}))},
                 evidence_summary={key: len(building_evidence.get(key, [])) for key in
                                   ("spaces", "levels", "surfaces", "openings", "constructions", "lighting", "equipment")})
    document = building_evidence.get("source_pdf") or thermal_model.get("source_pdf") or "building_evidence.json"

    def add(group, kind, identity, value, evidence, reason, evidence_ids=(), dependencies=(), confidence="unknown"):
        candidate_id = kind + "_" + fingerprint([document, identity])[:16]
        row = {"candidate_id": candidate_id, "kind": kind, "value": value,
               "source": document, "citations": citations(evidence, document),
               "evidence_ids": sorted(str(i) for i in evidence_ids if i), "confidence": confidence,
               "dependencies": list(dependencies), "proposal_status": "pending_review", "reason": reason,
               "target_artifact": "hourly_load_model" if group in GROUPS[:4] else
                   "schedule_library" if group == "schedules" else
                   "envelope_model" if kind == "surface" else "envelope_library"}
        row["fingerprint"] = fingerprint(row)
        draft["candidates"][group].append(row)
        return row

    def issue(identity, reason, evidence=(), affected_id="project"):
        item = {"item_id": "issue_" + fingerprint([document, identity])[:16],
            "scope": "room" if affected_id != "project" else "project", "affected_id": affected_id,
            "reason": reason, "status": "needs_evidence", "source": document,
            "citations": citations(evidence, document), "evidence_ids": [], "confidence": "unknown",
            "effect": "blocks_room" if affected_id != "project" else "blocks_project",
            "remediation": "Provide source-backed evidence or make an explicit engineer review decision."}
        draft["review_items"].append(item)

    # Preserve source-level exceptions as actionable review items. They are
    # evidence findings, not approvals or calculated inputs.
    seen_source_issues = set()
    for source_issue in list(building_evidence.get("exceptions", [])) + list(drawing_coverage.get("coverage_exceptions", [])):
        if isinstance(source_issue, dict):
            item = deepcopy(source_issue)
            item.setdefault("item_id", "source_issue_" + fingerprint(item)[:16])
            issue_key = item.get("item_id") or fingerprint(item)
            if issue_key in seen_source_issues:
                continue
            seen_source_issues.add(issue_key)
            item.setdefault("scope", "project")
            item.setdefault("affected_id", item.get("level_name", "project"))
            item.setdefault("reason", item.get("question", "Source evidence requires review."))
            item.setdefault("status", "needs_evidence")
            item.setdefault("source", document)
            item.setdefault("citations", [])
            item.setdefault("effect", "blocks_project")
            item.setdefault("remediation", "Review the cited source and provide the missing relationship or value.")
            draft["review_items"].append(item)

    # Fusion findings retain page/entity provenance and remain review-only;
    # they are never interpreted as calculator facts here.
    for fusion_issue in (evidence_fusion or {}).get("review_items", []):
        item = deepcopy(fusion_issue)
        item.setdefault("scope", "project")
        item.setdefault("source", "architect_evidence_fusion.json")
        item.setdefault("citations", [])
        item.setdefault("effect", "blocks_project")
        draft["review_items"].append(item)

    floors = {}
    for level in sorted(drawing_coverage.get("levels", []), key=lambda r: r.get("level_name", "")):
        name = level.get("level_name", "").strip()
        if not name or name.lower().startswith("unassigned"):
            issue("floor_unknown", "Confirm the drawing level; no real floor was identified.")
            continue
        evidence = [{"page": page, "excerpt": name} for page in level.get("page_numbers", [])]
        evidence = evidence or level.get("purpose_evidence", [])
        floor_id = "floor_" + fingerprint([document, name.casefold()])[:12]
        floors[name.casefold()] = add("floors", "floor", [name.casefold()],
            {"floor_id": floor_id, "name": name, "elevation_m": None}, evidence,
            "Confirm this drawing level; elevation remains optional.", confidence="inferred")

    spaces = {}
    for space in building_evidence.get("spaces", []):
        if space.get("name"):
            key = (space.get("level_name", "").casefold(), space["name"].casefold())
            spaces.setdefault(key, []).append(space)
    room_lookup = {}
    for key, matches in sorted(spaces.items()):
        space = matches[0]
        evidence = [e for row in matches for e in row.get("evidence", [])]
        if len(matches) != 1:
            issue([key, "duplicate"], "Ambiguous repeated room identity. Resolve duplicate rooms and conflicting areas before drafting topology.", evidence)
            continue
        floor = floors.get(key[0])
        if not floor:
            issue([key, "floor"], "Room evidence is present but its drawing level is unresolved. Map it to a reviewed floor.", evidence)
        # Candidate identity is anchored to the evidence record and sheet/page,
        # not the extracted value. A corrected area/excerpt must revisit the
        # same proposal rather than silently creating a new room.
        location = sorted((space.get("id", ""), e.get("page")) for e in evidence)
        identity = [key, location]
        suffix = fingerprint([document, identity])[:12]
        zone_id, room_id = "zone_" + suffix, "room_" + suffix
        zone = add("zones", "zone", identity, {"zone_id": zone_id, "name": space["name"],
            "floor_id": floor["value"]["floor_id"] if floor else "",
            "floor_status": "proposed" if floor else "unresolved"}, evidence,
            "Review the proposed one-room zone and floor mapping.", [space.get("id")], [floor["candidate_id"]] if floor else [], space.get("confidence", "unknown"))
        room = add("rooms", "room", identity, {"room_id": room_id, "name": space["name"], "zone_id": zone_id,
            "floor_id": floor["value"]["floor_id"] if floor else "", "geometry_status": space.get("geometry_status", "label_detected"),
            "geometry_reference": space.get("geometry_reference"), "unresolved_fields": list(space.get("unresolved_fields", []))},
            evidence, "Confirm room identity and mapping; missing load inputs remain missing.",
            [space.get("id")], [zone["candidate_id"]], space.get("confidence", "unknown"))
        room_lookup[space.get("id")] = room
        area = quantity(space.get("area"), "m2")
        if area is not None:
            # A room label alone does not evidence an area.
            area_evidence = [e for e in evidence if re.search(r"\b" + re.escape(str(area).removesuffix(".0")) + r"(?:\.0)?\s*m[2²]", e.get("excerpt", ""), re.I)]
            add("room_inputs", "area", identity, {"room_id": room_id, "area_m2": area}, area_evidence,
                "Confirm the cited room area.", [space.get("id")], [room["candidate_id"]], space.get("confidence", "unknown"))
        else:
            issue([identity, "area"], "Supply a cited positive room area with explicit m² units.", evidence, room_id)
        for field in space.get("unresolved_fields", []):
            if field not in {"area", "geometry", "floor"}:
                continue
            issue([identity, field], f"Resolve the room {field} from source-backed evidence before activation.", evidence, room_id)
        if not floor:
            issue([identity, "floor"], "Map the room to a reviewed floor before activation.", evidence, room_id)
        if space.get("geometry_status") not in {"geometry_confirmed"}:
            issue([identity, "geometry"], "Confirm a room boundary or geometry witness; a label alone cannot activate a room.", evidence, room_id)
        issue([identity, "schedules"], "Assign reviewed schedules for each non-zero supported room load in the room editor.", evidence, room_id)

    # Only explicit source-room links permit assignment. Same name/page/level is insufficient.
    for fact in thermal_evidence.get("facts", []):
        if fact.get("field") != "ceiling_height_mm":
            continue
        room = room_lookup.get(fact.get("building_evidence_id") or fact.get("space_id"))
        add("room_inputs", "ceiling", [fact.get("fact_id"), fact.get("evidence", [])],
            {"room_id": room["value"]["room_id"] if room else "", "ceiling_height_mm": quantity({"value": fact.get("value"), "unit": fact.get("unit")}, "mm")},
            fact.get("evidence", []), "Confirm room allocation and ceiling evidence; height is metadata only.",
            [fact.get("fact_id")], [room["candidate_id"]] if room else [], fact.get("confidence", "unknown"))
    for family, kind in (("lighting", "lighting"), ("equipment", "equipment")):
        for item in building_evidence.get(family, []):
            room = room_lookup.get(item.get("space_id"))
            value = {"room_id": room["value"]["room_id"] if room else "", "schedule_id": "",
                     "diversity_factor": None}
            if kind == "lighting":
                value.update(connected_w=item.get("connected_w"), lighting_w_m2=None)
            else:
                value.update(name=item.get("name", ""), quantity=item.get("quantity"), watts=item.get("watts"),
                             kind=item.get("kind", ""), heat_input_basis="", space_gain_factor=None)
            identity = [kind, item.get("level_name"), item.get("name", item.get("fixture_tag")), item.get("evidence", [])]
            add("room_inputs", kind, identity, value, item.get("evidence", []),
                "Confirm room allocation, heat basis, factors, and schedule before applying this load.",
                [item.get("id")], [room["candidate_id"]] if room else [], item.get("confidence", "unknown"))
    for item in thermal_model.get("schedule_candidates", []):
        if not isinstance(item, dict):
            raise ValueError("Schedule evidence must be an object.")
        profiles = item.get("day_profiles", {})
        complete = {day: profile for day, profile in profiles.items() if day in DAY_TYPES and
                    isinstance(profile, dict) and len(profile.get("values", [])) == 24}
        if not complete or len(complete) != len(profiles):
            issue(["schedule", item.get("schedule_id")], "Supply exactly 24 values and citations for each declared schedule day type.", item.get("evidence", []))
            continue
        add("schedules", "schedule", [item.get("schedule_id"), item.get("evidence", [])],
            {"schedule_id": item.get("schedule_id", ""), "title": item.get("title", ""), "day_profiles": complete},
            item.get("evidence", []), "Review the declared day profiles; other day types remain missing.", [item.get("id")], confidence=item.get("confidence", "unknown"))
    for family, kind in (("constructions", "construction"), ("openings", "window"), ("surfaces", "surface")):
        for item in building_evidence.get(family, []):
            evidence_location = sorted((e.get("page"), e.get("kind", "")) for e in item.get("evidence", []))
            identity = [family, item.get("id", ""), item.get("level_name"), item.get("tag", item.get("reference", item.get("adjacency"))), evidence_location]
            suffix = fingerprint([document, identity])[:12]
            value = {"title": item.get("reference") or item.get("tag") or item.get("adjacency") or kind}
            if kind == "construction":
                value.update(record_id="construction_" + suffix, kind=item.get("kind", ""),
                    u_value_w_m2k=(item.get("thermal_performance") or {}).get("u_value_w_m2k"), absorptivity=None)
            elif kind == "window":
                value.update(record_id="window_" + suffix, opening_kind=item.get("kind", ""),
                    u_value_w_m2k=(item.get("performance") or {}).get("u_value_w_m2k"))
            else:
                value.update(surface_id="surface_" + suffix, owner_room_id="", owner_zone_id="", kind=item.get("kind", ""),
                    area_m2=None, orientation="", boundary_method="", construction_id="", window_id="", adjacent_temperature_c=None)
            add("envelope", kind, identity, value, item.get("evidence", []),
                "Supply missing reviewed properties and geometry. Envelope activation requires a separate editor action.",
                [item.get("id")], confidence=item.get("confidence", "unknown"))

    # Reordering never carries an approval to different content or dependencies.
    lookup = {row["candidate_id"]: row for row in all_candidates(draft)}
    for row in all_candidates(draft):
        row["fingerprint"] = fingerprint([row["fingerprint"], [lookup[d]["fingerprint"] for d in row["dependencies"]]])
    for cid, decision in previous.get("decisions", {}).items():
        row = lookup.get(cid)
        if previous.get("schema_version") == 2 and row and decision.get("candidate_fingerprint") == row["fingerprint"]:
            draft["decisions"][cid] = deepcopy(decision)
        else:
            draft["review_history"].append({"candidate_id": cid, "decision": decision, "reason": "Evidence changed, removed, or legacy approval requires review."})
    topology_ready = bool(draft["candidates"]["floors"] and draft["candidates"]["rooms"])
    if not topology_ready:
        draft["status"] = "blocked"
        draft["readiness"] = {"status": "blocked", "issues": [
            {"status": "blocked", "affected_id": "project", "source_artifact": "building_evidence.json",
             "reason": "No source-backed floor and room topology candidates are available.",
             "effect": "blocks_project", "remediation": "Provide an identifiable plan page and room evidence."}
        ]}
    else:
        draft["readiness"] = {"status": "review_required", "issues": deepcopy(draft["review_items"])}
    return draft


def save_review(draft, changes, expected_revision):
    check_revision(draft, expected_revision)
    if not isinstance(changes, dict):
        raise ValueError("Review decisions must be an object.")
    result = deepcopy(draft)
    lookup = {row["candidate_id"]: row for row in all_candidates(draft)}
    issues = {row["item_id"]: row for row in draft["review_items"]}
    for cid, raw in changes.items():
        row = lookup.get(cid) or issues.get(cid)
        if row is None or not isinstance(raw, dict) or raw.get("decision") not in DECISIONS:
            raise ValueError("Unknown candidate or invalid review decision: " + cid)
        decision = deepcopy(raw)
        if decision["decision"] != "pending" and not str(decision.get("reviewer", "")).strip():
            raise ValueError("Reviewer attribution is required: " + cid)
        if decision["decision"] == "edit":
            if not isinstance(decision.get("value"), dict) or not str(decision.get("source", "")).strip():
                raise ValueError("Edited values need a field object and engineering review source: " + cid)
            if not supported_citations(validate_citations(decision.get("citations", []), cid)):
                raise ValueError("Edited values need a citation and supporting excerpt: " + cid)
        decision.update(candidate_fingerprint=row.get("fingerprint", fingerprint(row)), reviewed_at=timestamp())
        result["decisions"][cid] = decision
    result.update(revision=draft["revision"] + 1, updated_at=timestamp())
    return result


def check_revision(draft, expected):
    if draft.get("schema_version") != 2:
        raise DraftConflict("Rebuild this legacy draft and review its evidence again.", "legacy_draft")
    if expected != draft.get("revision"):
        raise DraftConflict("The draft changed. Reload and review the latest version.")


def apply_calculator_draft(draft, decisions=None, hourly_model=None, schedule_library=None,
                           envelope_library=None, envelope_model=None, source_requirements_updated_at=""):
    """Pure preview: returns validated artifacts and exact additive changes."""
    check_revision(draft, draft.get("revision"))
    originals = {"hourly_load_model": hourly_model or empty_hourly_load_model(),
                 "schedule_library": schedule_library or empty_schedule_library(),
                 "envelope_library": envelope_library or empty_envelope_library(),
                 "envelope_model": envelope_model or empty_envelope_model()}
    model = validate_hourly_load_model(originals["hourly_load_model"])
    if not hourly_model:
        model["source_requirements_updated_at"] = source_requirements_updated_at
    artifacts = {"hourly_load_model": model,
                 "schedule_library": validate_schedule_library(originals["schedule_library"]),
                 "envelope_library": validate_envelope_library(originals["envelope_library"])}
    artifacts["envelope_model"] = validate_envelope_model(originals["envelope_model"], artifacts["envelope_library"])
    for name, artifact in artifacts.items():
        artifact["updated_at"] = originals[name].get("updated_at", "")
    summary = {name: [] for name in ("created", "populated_fields", "already_present", "skipped_conflicts", "missing_dependencies", "unresolved", "reports_marked_stale")}
    success = set()
    modified = set()
    decisions = draft.get("decisions", {}) if decisions is None else decisions
    order = {kind: i for i, kind in enumerate(("floor", "zone", "room", "area", "ceiling", "schedule", "lighting", "equipment", "construction", "window", "surface"))}
    for item in sorted(all_candidates(draft), key=lambda r: order[r["kind"]]):
        cid = item["candidate_id"]
        decision = decisions.get(cid, {})
        action = decision.get("decision", "pending")
        if action not in {"accept", "edit"}:
            if action != "reject":
                summary["unresolved"].append({"candidate_id": cid, "reason": item["reason"], "decision": action})
            continue
        if decision.get("candidate_fingerprint") != item["fingerprint"]:
            raise DraftConflict("Approval no longer matches proposal " + cid, "evidence_changed")
        value = deepcopy(item["value"])
        supplied = decision.get("value", {}) if action == "edit" else {}
        if set(supplied) - set(value):
            raise ValueError("Unknown editable fields for " + cid)
        # Stable target IDs are immutable; relationships are explicitly editable.
        id_key = {"floor": "floor_id", "zone": "zone_id", "room": "room_id", "schedule": "schedule_id",
                  "construction": "record_id", "window": "record_id", "surface": "surface_id"}.get(item["kind"])
        if id_key in supplied and supplied[id_key] != value[id_key]:
            raise ValueError("Target ID cannot be edited: " + cid)
        value.update(supplied)
        # A label-only proposal is useful evidence, but it is not a calculator
        # room.  Topology activation requires an explicitly reviewed floor and
        # geometry witness; the bridge must never turn proximity into geometry.
        if item["kind"] == "zone" and not value.get("floor_id"):
            summary["unresolved"].append({"candidate_id": cid, "reason": "A zone cannot be applied without a reviewed floor assignment."})
            continue
        if item["kind"] == "room":
            if not value.get("floor_id"):
                summary["unresolved"].append({"candidate_id": cid, "reason": "A room cannot be applied without a reviewed floor assignment."})
                continue
            if value.get("geometry_status") != "geometry_confirmed":
                summary["unresolved"].append({"candidate_id": cid, "reason": "Room geometry must be explicitly reviewed and confirmed before activation."})
                continue
        evidence = validate_citations(item["citations"] + decision.get("citations", []), cid)
        if not supported_citations(evidence):
            summary["unresolved"].append({"candidate_id": cid, "reason": "Supply supporting citations and excerpts."})
            continue
        dependencies = [d for d in item["dependencies"] if d not in success]
        # An explicit edited parent mapping may reference an existing authored record.
        parent = {"zone": ("floor_id", "floors"), "room": ("zone_id", "zones"),
                  "area": ("room_id", "rooms"), "ceiling": ("room_id", "rooms"),
                  "lighting": ("room_id", "rooms"), "equipment": ("room_id", "rooms")}.get(item["kind"])
        mapped = parent and parent[0] in supplied and any(r[parent[0]] == value[parent[0]] for r in model[parent[1]])
        if dependencies and not mapped:
            summary["missing_dependencies"].append({"candidate_id": cid, "dependencies": dependencies, "reason": "Review the parent proposals or explicitly map to an existing parent."})
            continue
        trial = deepcopy(artifacts)
        provenance = {"candidate_id": cid, "candidate_fingerprint": item["fingerprint"], "evidence_ids": item["evidence_ids"],
                      "original_citations": item["citations"], "reviewer": decision.get("reviewer"), "reviewed_at": decision.get("reviewed_at"),
                      "source": decision.get("source") or item["source"], "citations": evidence}
        try:
            target, bucket, key, record, fill = prepare_record(item["kind"], value, trial, provenance)
            rows = trial[target][bucket]
            existing = next((r for r in rows if r[key] == record[key]), None)
            changes, conflicts = {}, {}
            if existing:
                for field, proposed in record.items():
                    if field in ({key, "source", "citations", "verification_status", "review_status", "mapping_status", "status", "revision", "bridge_provenance", "floor_status", "geometry_status", "geometry_reference", "unresolved_fields"} | ({"floor_id"} if item["kind"] == "room" else set())):
                        continue
                    current = existing.get(field)
                    if isinstance(proposed, dict) and fill:
                        for subkey, subvalue in proposed.items():
                            old = (current or {}).get(subkey)
                            if old == subvalue:
                                continue
                            if old in (None, ""):
                                changes.setdefault(field, deepcopy(current or {}))[subkey] = subvalue
                            else:
                                conflicts[field + "." + subkey] = {"current": old, "proposed": subvalue}
                    elif current != proposed and proposed is not None:
                        if fill and current in (None, ""):
                            changes[field] = proposed
                        else:
                            conflicts[field] = {"current": current, "proposed": proposed}
                if conflicts:
                    summary["skipped_conflicts"].append({"candidate_id": cid, "fields": conflicts, "source": existing.get("source"), "proposed_source": provenance["source"], "reason": "Authored values are preserved; resolve in the detailed editor."})
                    continue
                if not changes:
                    summary["already_present"].append({"candidate_id": cid, "id": record[key]})
                    success.add(cid)
                    continue
                existing.update(changes)
                existing.setdefault("bridge_provenance", {})[cid] = provenance
                existing["citations"] = list(existing.get("citations", [])) + [c for c in evidence if c not in existing.get("citations", [])]
            else:
                rows.append(record)
            validate_artifacts(trial)
        except (ValueError, KeyError, TypeError) as error:
            summary["unresolved"].append({"candidate_id": cid, "reason": str(error), "editor": item["target_artifact"]})
            continue
        artifacts = trial
        model = artifacts["hourly_load_model"]
        modified.add(target)
        summary["populated_fields" if existing else "created"].append({"candidate_id": cid, "artifact": target, "id": record[key], "fields": sorted(changes) if existing else sorted(record), "target_fingerprint": fingerprint(existing or record)})
        success.add(cid)
    summary["unresolved"].extend({"item_id": row["item_id"], "reason": row["reason"], "affected_id": row["affected_id"]} for row in draft["review_items"])
    for name in modified:
        artifacts[name]["updated_at"] = timestamp()
    return {**artifacts, "changed": {name: name in modified for name in originals}, "summary": summary}


def require(value, *fields):
    missing = [field for field in fields if value.get(field) in (None, "")]
    if missing:
        raise ValueError("Missing reviewed fields: " + ", ".join(missing))


def number(value, field, low=0, high=1e8):
    raw = value.get(field)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw) or not low <= raw <= high:
        raise ValueError(f"{field} must be a reviewed number between {low} and {high}.")
    return raw


def prepare_record(kind, value, artifacts, provenance):
    common = {"source": provenance["source"] + " — engineer review: " + provenance["reviewer"],
              "citations": provenance["citations"], "bridge_provenance": {provenance["candidate_id"]: provenance}}
    model = artifacts["hourly_load_model"]
    if kind in {"floor", "zone", "room"}:
        record = {**value, **common, "verification_status": "confirmed"}
        if kind == "room":
            record["mapping_status"] = "confirmed"
        return "hourly_load_model", kind + "s", kind + "_id", record, False
    if kind in {"area", "ceiling", "lighting", "equipment"}:
        require(value, "room_id")
        room = next((r for r in model["rooms"] if r["room_id"] == value["room_id"]), None)
        if not room:
            raise ValueError("Select an accepted or existing room.")
        record = {"room_id": value["room_id"], **common}
        if kind in {"area", "ceiling"}:
            field = "area_m2" if kind == "area" else "ceiling_height_mm"
            record[field] = number(value, field, 0.001, 1000000)
        else:
            require(value, "schedule_id", "diversity_factor")
            number(value, "diversity_factor", 0, 1)
            schedule = next((s for s in artifacts["schedule_library"]["schedules"] if s["schedule_id"] == value["schedule_id"]), None)
            if not schedule or schedule["status"] != "confirmed" or not any(p["status"] == "confirmed" and len(p["values"]) == 24 and supported_citations(p["citations"]) for p in schedule["day_profiles"].values()):
                raise ValueError("Select a confirmed cited schedule with a complete declared profile.")
            if kind == "lighting":
                density = value.get("lighting_w_m2")
                if density is None:
                    require(room, "area_m2")
                    watts = number(value, "connected_w")
                    density = watts / number(room, "area_m2", 0.001)
                    provenance["derivation"] = {"formula": "connected_w / area_m2", "connected_w": watts, "area_m2": room["area_m2"]}
                else:
                    number(value, "lighting_w_m2")
                record.update(cooling_load={"lighting_w_m2": density, "lighting_diversity_factor": value["diversity_factor"]}, schedule_assignments={"lighting": value["schedule_id"]})
            else:
                require(value, "name", "heat_input_basis", "kind")
                for field in ("quantity", "watts", "space_gain_factor"):
                    number(value, field, 0.001 if field == "quantity" else 0, 1 if field == "space_gain_factor" else 1e8)
                source_id = "equipment_" + provenance["candidate_id"].split("_")[-1]
                source = {**{k: value[k] for k in ("name", "quantity", "watts", "kind", "diversity_factor", "space_gain_factor")}, **common,
                          "source_id": source_id, "verification_status": "confirmed", "heat_input_basis": value["heat_input_basis"]}
                prior = next((s for s in room["heat_sources"] if s["source_id"] == source_id), None)
                if prior and any(prior.get(k) != source[k] for k in ("name", "quantity", "watts", "kind", "diversity_factor", "space_gain_factor")):
                    raise ValueError("Equipment conflict: an authored heat-source value differs.")
                if not prior:
                    room["heat_sources"].append(source)
                assignments = room["schedule_assignments"]["equipment"]
                if assignments.get(source_id) not in (None, "", value["schedule_id"]):
                    raise ValueError("Equipment schedule conflict; use the detailed editor.")
                # Return a room patch; changes to its children remain part of this trial.
                record["schedule_assignments"] = {"equipment": {**assignments, source_id: value["schedule_id"]}}
        return "hourly_load_model", "rooms", "room_id", record, True
    if kind == "schedule":
        require(value, "schedule_id", "title", "day_profiles")
        profiles = deepcopy(value["day_profiles"])
        if not profiles or set(profiles) - set(DAY_TYPES):
            raise ValueError("Declare supported schedule day types.")
        for day, profile in profiles.items():
            if len(profile.get("values", [])) != 24:
                raise ValueError("Each declared schedule profile requires exactly 24 values.")
            cites = validate_citations(profile.get("citations") or common["citations"], day)
            if not supported_citations(cites):
                raise ValueError("Each declared profile requires source citations.")
            profile.update(status="confirmed", source=common["source"], citations=cites)
        return "schedule_library", "schedules", "schedule_id", {**value, **common, "day_profiles": profiles, "status": "confirmed"}, False
    if kind in {"construction", "window"}:
        require(value, "record_id", "title")
        if kind == "construction":
            if value["kind"] not in CONSTRUCTION_KINDS:
                raise ValueError("Select a supported construction kind.")
            number(value, "u_value_w_m2k", 0.0001)
        elif value.get("opening_kind") not in {"window_or_glazing", "glazing", "window"}:
            raise ValueError("Resolve opening type; a door cannot be assumed to be glazing.")
        return "envelope_library", "constructions" if kind == "construction" else "windows", "record_id", {**value, **common, "revision": 1, "review_status": "confirmed"}, False
    if kind == "surface":
        if artifacts["envelope_model"]["active_for_calculation"]:
            raise ValueError("Envelope is active. Integrate this surface explicitly in the envelope editor.")
        require(value, "owner_room_id", "owner_zone_id", "orientation", "boundary_method", "area_m2")
        room = next((r for r in model["rooms"] if r["room_id"] == value["owner_room_id"]), None)
        if not room or room["zone_id"] != value["owner_zone_id"]:
            raise ValueError("Surface owner must match an existing room and its zone.")
        return "envelope_model", "surfaces", "surface_id", {**value, **common, "review_status": "confirmed"}, False
    raise ValueError("Unsupported proposal type.")


def validate_artifacts(artifacts):
    validators = {"hourly_load_model": validate_hourly_load_model, "schedule_library": validate_schedule_library,
                  "envelope_library": validate_envelope_library}
    for name, validator in validators.items():
        old = artifacts[name].get("updated_at", "")
        artifacts[name] = validator(artifacts[name])
        artifacts[name]["updated_at"] = old
    old = artifacts["envelope_model"].get("updated_at", "")
    artifacts["envelope_model"] = validate_envelope_model(artifacts["envelope_model"], artifacts["envelope_library"])
    artifacts["envelope_model"]["updated_at"] = old
