"""Project-local Card P packet, manual transport and apply service."""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote
import uuid

from ai import autonomous_task_scoring, autonomous_tasks, reviewer_room_geometry
from backend import productization, reviewer_room_geometry_service, site_location_service
from backend.vision_extraction_service import _atomic_json

ACCURACY_PATH = Path(__file__).resolve().parents[1] / "evaluations" / "autonomous" / "accuracy.json"
ROOF_CONTRACTOR_QUESTION = "Is there a floor or another tenancy directly above this shop, or is it the roof?"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _read(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else deepcopy(default)
    except (OSError, json.JSONDecodeError):
        return deepcopy(default)


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_json(path, value)


def _root(project):
    return Path(project["review_dir"])


def _task_dir(root, task, target):
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(target)).strip("._")[:100] or "project"
    return root / "ai_tasks" / task / safe


def _accuracy(task):
    row = _read(ACCURACY_PATH, {}).get("tasks", {}).get(task)
    if (not isinstance(row, dict) or not isinstance(row.get("accuracy"), (int, float))
            or isinstance(row.get("accuracy"), bool) or int(row.get("scored", 0)) < 10):
        return {"accuracy": None, "scored": 0, "auto_apply": False, "report": ""}
    accuracy = float(row["accuracy"])
    return {"accuracy": accuracy, "scored": int(row["scored"]),
            "auto_apply": accuracy >= autonomous_task_scoring.AUTO_APPLY_BAR,
            "report": "accuracy.json"}


def _current_task(run_root, task, target):
    current_file = _task_dir(run_root, task, target) / "current.json"
    pointer = _read(current_file, {})
    relative = pointer.get("run_path")
    if not relative:
        return None
    path = (current_file.parent / relative).resolve()
    try:
        path.relative_to(current_file.parent.resolve())
    except ValueError:
        return None
    return _read(path / "record.json", None)


def _load_inputs(root):
    ai_input = _read(root / "ai_input.json", {})
    spatial = _read(root / "spatial_ocr.json", {})
    building = _read(root / "building_evidence.json", {})
    return ai_input, spatial, building


def _latest_site(root):
    rows = list((root / "ai_tasks" / "P1_site").glob("*/current.json"))
    for pointer_path in rows:
        pointer = _read(pointer_path, {})
        run_path = pointer_path.parent / pointer.get("run_path", "")
        record = _read(run_path / "record.json", {})
        if record.get("status") in {"applied", "applied_fallback", "below_accuracy_bar"} and record.get("applied_value"):
            return record.get("applied_value")
    return {}


def _latest_roof(root, room_id):
    pointer_path = _task_dir(root, "P5_roof", room_id) / "current.json"
    pointer = _read(pointer_path, {})
    record = _read(pointer_path.parent / pointer.get("run_path", "") / "record.json", {})
    return record.get("applied_value", {}) if record.get("status") in {"applied", "applied_fallback", "below_accuracy_bar"} else {}


def _status_for_accuracy(source, accuracy):
    if source == "ai_fallback":
        return "applied_fallback"
    return "applied" if accuracy.get("auto_apply") else "below_accuracy_bar"


def _site_packet(root):
    ai_input, spatial, building = _load_inputs(root)
    packet, prompt = autonomous_tasks.build_site_packet(ai_input, spatial, building)
    return packet, prompt, {"ai_input": ai_input, "spatial_ocr": spatial, "building_evidence": building}


def _north_packets(root):
    ai_input, _spatial, _building = _load_inputs(root)
    rows = []
    for page in autonomous_tasks.plan_pages(ai_input):
        number = page["page"]
        target = f"page-{number}"
        try:
            with TemporaryDirectory(prefix="archie-north-crops-") as temporary:
                crops, paths = autonomous_tasks.make_north_crops(root, number, Path(temporary))
                packet = {"task": "P2_north", "page": number, "drawing_number": page.get("drawing_number", ""),
                          "title": page.get("title", ""), "crops": crops}
                prompt = autonomous_tasks.north_prompt(packet)
                rows.append((target, packet, prompt, [path.read_bytes() for path in paths]))
        except (OSError, ValueError) as error:
            rows.append((target, {"task": "P2_north", "page": number, "crops": []}, "", [], str(error)))
    return rows


def _roof_packets(root):
    ai_input, _spatial, _building = _load_inputs(root)
    site = _latest_site(root)
    try:
        traces = reviewer_room_geometry_service.current_records(reviewer_room_geometry_service._paths({"review_dir": str(root)}))
    except (OSError, ValueError, TypeError, KeyError):
        traces = []
    room_use = _read(root / "room_use_resolution.json", {})
    comfort_scopes = {"comfort_hvac", "comfort_hvac_with_process_exception"}
    comfort_ids = {str(row.get("room_id")) for row in room_use.get("records", [])
                   if isinstance(row, dict) and row.get("room_id") and row.get("space_scope") in comfort_scopes}
    selected = {}
    for trace in traces:
        if (trace.get("room_id") in comfort_ids
                and trace.get("calibration", {}).get("status") in {"agreed", "declared_scale_rejected"}):
            selected.setdefault(trace["room_id"], trace)
    result = []
    for room_id, trace in selected.items():
        packet = autonomous_tasks.build_roof_facts(ai_input, site, {
            "room_id": room_id, "room_label": trace.get("room_label"),
            "level_name": trace.get("level_name"), "page": trace.get("page"),
        })
        prompt = autonomous_tasks.roof_prompt(packet)
        result.append((room_id, packet, prompt, []))
    return result


def _create_run(root, task, target, packet, prompt, image_bytes=None, blocked_reason=""):
    accuracy = _accuracy(task)
    packet_bytes = json.dumps(packet, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    image_hashes = [hashlib.sha256(data).hexdigest() for data in (image_bytes or [])]
    input_fingerprint = autonomous_tasks.fingerprint({"packet": packet, "images": image_hashes})
    parent = _task_dir(root, task, target)
    current = _current_task(root, task, target)
    if current and current.get("input_fingerprint") == input_fingerprint:
        return current
    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    run_dir = parent / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    packet["prompt_fingerprint"] = hashlib.sha256(prompt.encode("utf-8")).hexdigest() if prompt else ""
    record = {
        "schema_version": 1, "run_id": run_id, "task": task, "target": target, "status": "blocked" if blocked_reason else "waiting_for_reply",
        "created_at": _now(), "input_fingerprint": input_fingerprint, "packet_fingerprint": hashlib.sha256(packet_bytes).hexdigest(),
        "prompt_fingerprint": packet["prompt_fingerprint"], "budget": autonomous_tasks.TASKS[task],
        "packet": packet, "prompt": prompt, "image_hashes": image_hashes,
        "accuracy": accuracy, "quality_label": "AI-determined" if accuracy["auto_apply"] else "AI-determined (below accuracy bar)",
        "reply": "", "reply_hash": "", "reply_attempts": [], "validation": {}, "applied_value": {}, "source": "",
        "retry_count": 0, "block_reason": blocked_reason, "cross_check": {},
    }
    for index, content in enumerate(image_bytes or []):
        (run_dir / f"image-{index + 1}.png").write_bytes(content)
    _write(run_dir / "packet.json", packet)
    (run_dir / "prompt.md").write_text(prompt, encoding="utf-8")
    _write(run_dir / "record.json", record)
    _write(parent / "current.json", {"run_path": f"runs/{run_id}", "run_id": run_id, "input_fingerprint": input_fingerprint})
    return record


def _record_file(root, record):
    parent = _task_dir(root, record["task"], record["target"])
    return parent / "runs" / record["run_id"] / "record.json"


def _public_record(web, project, record):
    row = deepcopy(record)
    parent = _task_dir(_root(project), record["task"], record["target"]) / "runs" / record["run_id"]
    row["images"] = []
    for path in sorted(parent.glob("image-*.png")):
        row["images"].append({"name": path.name, "url": f"/api/autonomous-tasks/image?project_id={quote(project['id'])}&task={quote(record['task'])}&target={quote(record['target'])}&run_id={quote(record['run_id'])}&name={quote(path.name)}"})
    row.pop("image_hashes", None)
    return row


def _all_current(root, web=None, project=None):
    records = []
    for task in ("P1_site", "P2_north", "P5_roof"):
        for pointer in sorted((root / "ai_tasks" / task).glob("*/current.json")):
            current = _read(pointer, {})
            record = _read(pointer.parent / current.get("run_path", "") / "record.json", None)
            if record:
                records.append(_public_record(web, project, record) if web and project else record)
    return records


def _site_determination(root):
    return _read(root / "ai_task_determinations.json", {})


def get(web, project):
    root = _root(project)
    return {"id": project["id"], "tasks": _all_current(root, web, project), "auto_apply_bar": autonomous_task_scoring.AUTO_APPLY_BAR,
            "determinations": _site_determination(root), "supported_tasks": ["P1_site", "P2_north", "P5_roof"],
            "out_of_scope_tasks": {"P0_rooms": "Owned by the room-outline task.", "P3_boundaries": "Next Card P task.", "P4_openings": "Next Card P task."}}


def get_labels(project):
    """Small result-only view for contractor screens; never returns prompts or replies."""
    root = _root(project)
    rows = []
    for record in _all_current(root):
        if record.get("applied_value"):
            rows.append({key: deepcopy(record.get(key)) for key in
                         ("task", "status", "source", "quality_label", "applied_value", "accuracy", "target")})
        elif record.get("task") == "P5_roof" and record.get("status") in {"needs_contractor_answer", "contractor_answered_not_sure"}:
            row = {"task": "P5_roof", "status": record["status"], "target": record.get("target"),
                   "room_label": record.get("packet", {}).get("room", {}).get("room_label") or record.get("target")}
            if record.get("status") == "needs_contractor_answer":
                row["question"] = ROOF_CONTRACTOR_QUESTION
            else:
                row["message"] = "Not sure — roof exposure remains unknown and not assessed."
            rows.append(row)
    return {"id": project["id"], "tasks": rows, "auto_apply_bar": autonomous_task_scoring.AUTO_APPLY_BAR}


def run_all(web, project):
    root = _root(project)
    site_packet, site_prompt, _ = _site_packet(root)
    _create_run(root, "P1_site", "project", site_packet, site_prompt,
                blocked_reason="No cited site excerpts were found." if not site_packet.get("excerpts") else
                "Prompt exceeds the 4,000-character limit." if len(site_prompt) > 4000 else "")
    for target, packet, prompt, images, *error in _north_packets(root):
        reason = error[0] if error else ""
        if not reason and len(packet.get("crops", [])) > 3:
            reason = "More than three crops were generated for one plan page."
        if not reason and any(max(crop["width_px"], crop["height_px"]) > 1024 for crop in packet.get("crops", [])):
            reason = "A north-arrow crop exceeds 1,024 px."
        _create_run(root, "P2_north", target, packet, prompt, images, reason)
    for target, packet, prompt, images in _roof_packets(root):
        record = _create_run(root, "P5_roof", target, packet, prompt, images,
                             "Prompt exceeds the 2,000-character limit; evidence was not shortened." if len(prompt) > 2000 else "")
        if record.get("status") == "waiting_for_reply" and not _has_explicit_roof_evidence(packet):
            record.update({"status": "needs_contractor_answer", "block_reason": ROOF_CONTRACTOR_QUESTION})
            _update_record(root, record)
    return get(web, project)


def _export_determinations(root):
    result = {}
    p1 = _latest_site(root)
    if p1:
        result["P1_site"] = {"site_text": p1.get("site_text", ""),
                             "source": "fallback" if p1.get("source") == "ai_fallback" else "ai_determined"}
    north = {}
    for record in _all_current(root):
        if record.get("task") == "P2_north" and record.get("applied_value"):
            value = record["applied_value"]
            north[value["page"]] = {"page": value["page"], "plan_up_azimuth_deg": value["plan_up_azimuth_deg"],
                                     "source": "fallback" if record.get("source") == "ai_fallback" else "ai_determined"}
    if north:
        result["P2_north"] = [north[key] for key in sorted(north)]
    roof = {}
    for record in _all_current(root):
        if record.get("task") == "P5_roof" and record.get("applied_value"):
            value = record["applied_value"]
            roof[value["room"]] = {"room": value["room"], "roof": value["roof"],
                                    "source": {"ai_fallback": "fallback", "reviewer": "reviewer"}.get(record.get("source"), "ai_determined")}
    if roof:
        result["P5_roof"] = [roof[key] for key in sorted(roof)]
    if any(record.get("stand_in") is True for record in _all_current(root)):
        result["stand_in"] = True
    _write(root / "ai_tasks" / "determinations.json", result)
    _write(root / "ai_task_determinations.json", result)
    return result


def _update_record(root, record):
    record["updated_at"] = _now()
    _write(_record_file(root, record), record)
    return record


def _archive_reply(root, record, reply, model_note):
    run_dir = _record_file(root, record).parent
    attempts_dir = run_dir / "reply_attempts"
    attempts_dir.mkdir(parents=True, exist_ok=True)
    attempt_id = f"{len(list(attempts_dir.glob('*.json'))) + 1:03d}_{uuid.uuid4().hex[:8]}"
    path = attempts_dir / f"{attempt_id}.json"
    reply_hash = hashlib.sha256(reply.encode("utf-8")).hexdigest()
    entry = {"attempt_id": attempt_id, "created_at": _now(), "reply_hash": reply_hash,
             "model_note": model_note, "outcome": "validating", "raw_reply": reply}
    _write(path, entry)
    record.setdefault("reply_attempts", []).append({key: entry[key] for key in
                                                    ("attempt_id", "created_at", "reply_hash", "model_note", "outcome")})
    return path


def _apply_p1(root, project, record, validated):
    site = deepcopy(validated.get("site"))
    cross_check = autonomous_tasks.site_cross_check(record["packet"], validated)
    source = "ai_determined"
    if cross_check.get("status") == "disagrees":
        if record.get("retry_count", 0) < 1:
            candidate = cross_check.get("rule_based_candidate", {})
            record["retry_count"] = record.get("retry_count", 0) + 1
            record["cross_check"] = cross_check
            record["status"] = "waiting_for_reply"
            record["block_reason"] = f"The reply conflicts with the rule-based site clue on page {candidate.get('page')}: {candidate.get('text')}. Retry once using the same excerpts."
            record["prompt"] = record["prompt"].split("\n\nCross-check conflict:")[0] + "\n\nCross-check conflict: the rule-based candidate is " + json.dumps(candidate, ensure_ascii=False) + ". Re-check the supplied excerpts and return a corrected JSON answer."
            record["prompt_fingerprint"] = hashlib.sha256(record["prompt"].encode("utf-8")).hexdigest()
            record["packet"]["prompt_fingerprint"] = record["prompt_fingerprint"]
            return record
        candidate = record["packet"].get("rule_based_top_candidate")
        if candidate:
            site = {"text": candidate["text"], "page": candidate["page"], "kind": candidate["kind"]}
            source = "ai_fallback"
            record["fallback"] = {"reason": "AI and rule-based site identification disagreed after one retry.",
                                  "selected_candidate": deepcopy(site), "basis": "rule_based_top_candidate"}
    if not site:
        record.update({"status": "blocked", "block_reason": "The reply found no site excerpt to apply, and no rule-based candidate was available.", "validation": validated, "cross_check": cross_check})
        return record
    # The location service's cited-coordinate path requires coordinates; this
    # task records textual site identity separately and never fabricates them.
    determination = {"site_text": site["text"], "site_kind": site["kind"], "page": site["page"],
                     "source": source, "label": ("Assumed (rule-based site fallback)" +
                         ("; task below accuracy bar" if not record["accuracy"].get("auto_apply") else ""))
                         if source == "ai_fallback" else record["quality_label"], "ai_run_id": record["run_id"],
                     "reply_hash": record["reply_hash"], "input_fingerprint": record["input_fingerprint"],
                     "evidence": next(row["text"] for row in record["packet"]["excerpts"] if row["page"] == site["page"] and site["text"] in row["text"]),
                     "consultant_addresses": deepcopy(validated["consultant_addresses"])}
    cross_check = autonomous_tasks.site_cross_check(record["packet"], validated)
    determination["cross_check"] = cross_check
    reviewer_site = _read(root / "site_location_resolution.json", {}).get("confirmed_address", "")
    if reviewer_site:
        determination["reviewer_override"] = reviewer_site
        determination["applied_site_text"] = reviewer_site
        determination["applied_source"] = "reviewer"
    else:
        determination["applied_site_text"] = site["text"]
        determination["applied_source"] = source
    _write(root / "site_location_ai_determination.json", determination)
    record.update({"status": _status_for_accuracy(source, record["accuracy"]), "source": source,
                   "applied_value": determination, "validation": validated, "cross_check": cross_check, "block_reason": ""})
    return record


def _apply_p2(web, project, root, record, validated):
    if not validated.get("found"):
        record.update({"status": "blocked", "block_reason": "No north arrow was found; façade sun remains not assessed.", "validation": validated})
        return record
    page = record["packet"]["page"]
    north_artifact = _read(root / "reviewer_room_geometry.json", {})
    existing_declaration = north_artifact.get("page_north", {}).get(str(page), {})
    existing_source = existing_declaration.get("declaration_source", "reviewer" if existing_declaration else "")
    if existing_source == "reviewer":
        record.update({"status": "applied", "source": "reviewer", "applied_value": {"page": page, "plan_up_azimuth_deg": existing_declaration.get("plan_up_azimuth_deg"), "overrode_by": "reviewer"}, "validation": validated})
        return record
    candidates_by_page = {}
    completed_pages = set()
    for other_page, declaration in north_artifact.get("page_north", {}).items():
        declaration_source = declaration.get("declaration_source", "reviewer")
        if declaration_source == "reviewer":
            candidates_by_page[int(other_page)] = float(declaration["plan_up_azimuth_deg"])
            completed_pages.add(int(other_page))
    other_records = {}
    for pointer_path in (root / "ai_tasks" / "P2_north").glob("*/current.json"):
        current = _read(pointer_path, {})
        other = _read(pointer_path.parent / current.get("run_path", "") / "record.json", None)
        if not other or other.get("target") == record.get("target"):
            continue
        other_validation = other.get("validation", {})
        if other_validation.get("valid") and isinstance(other_validation.get("result"), dict):
            other_validation = other_validation["result"]
        if other_validation.get("found") is not None:
            completed_pages.add(int(other.get("packet", {}).get("page", 0)))
        if other_validation.get("found"):
            other_page = int(other.get("packet", {}).get("page", 0))
            candidates_by_page[other_page] = float(other_validation["plan_up_azimuth_deg"])
            other_records[other_page] = other
    candidates_by_page[page] = float(validated["plan_up_azimuth_deg"])
    completed_pages.add(page)
    plan_page_numbers = [row["page"] for row in autonomous_tasks.plan_pages(_read(root / "ai_input.json", {}))]
    missing_pages = [number for number in plan_page_numbers if number not in completed_pages]
    if missing_pages:
        record.update({"status": "waiting_for_reply", "block_reason": "Waiting for a north-arrow check on plan page(s) " + ", ".join(map(str, missing_pages)) + ". All available pages must be checked before cross-page agreement is applied.", "validation": validated})
        return record
    candidates = sorted(candidates_by_page.items())
    consensus = autonomous_tasks.north_consensus(candidates)
    if consensus["status"] == "disagreement":
        if record.get("retry_count", 0) < 1:
            record["retry_count"] = record.get("retry_count", 0) + 1
            record["status"] = "waiting_for_reply"
            record["block_reason"] = "Plan pages disagree by more than 5°. Retry this page once; after that Archie will apply a strict-majority direction or block if there is no majority."
            record["validation"] = validated
            record["cross_check"] = consensus
            return record
        record.update({"status": "blocked", "block_reason": "The plan pages have no strict-majority north direction after one retry; façade sun remains not assessed.", "validation": validated, "cross_check": consensus})
        return record
    applied_angle = consensus["winner"]
    declaration_source = "ai_determined"
    reviewer = "Archie AI"
    task_records = {**other_records, page: record}
    for north_page in plan_page_numbers:
        declaration = _read(root / "reviewer_room_geometry.json", {}).get("page_north", {}).get(str(north_page), {})
        if declaration.get("declaration_source", "reviewer" if declaration else "") == "reviewer":
            continue
        north_record = task_records.get(north_page)
        if not north_record:
            continue
        reviewer_room_geometry_service.post(web, project, {
            "action": "declare_north", "page": north_page, "reviewer": reviewer,
            "plan_up_azimuth_deg": applied_angle, "declaration_source": declaration_source,
            "ai_run_id": north_record["run_id"],
        })
        north_record.update({"status": _status_for_accuracy("ai_determined", north_record["accuracy"]),
                             "source": "ai_determined", "applied_value": {"page": north_page,
                             "plan_up_azimuth_deg": applied_angle, "source": declaration_source,
                             "evidence": (north_record.get("validation", {}).get("result", {}) or north_record.get("validation", {})).get("description", "North arrow in source crop"),
                             "ai_run_id": north_record["run_id"]}, "cross_check": consensus, "block_reason": ""})
        if north_record is not record:
            _update_record(root, north_record)
    record.update({"status": _status_for_accuracy("ai_determined", record["accuracy"]), "source": "ai_determined",
                   "applied_value": {"page": page, "plan_up_azimuth_deg": applied_angle, "source": declaration_source,
                                     "evidence": validated.get("description", "North arrow in source crop"), "ai_run_id": record["run_id"]},
                   "validation": validated, "cross_check": consensus, "block_reason": ""})
    return record


def _apply_p5(web, project, root, record, validated):
    room_id = record["target"]
    paths = reviewer_room_geometry_service._paths(project)
    artifact = _read(paths["artifact"], reviewer_room_geometry.empty_artifact())
    trace = next((row for row in artifact.get("records", []) if row.get("room_id") == room_id), None)
    if trace is None:
        record.update({"status": "blocked", "block_reason": "The current traced room no longer exists.", "validation": validated})
        return record
    existing_roof_source = trace.get("roof_source") or (
        "reviewer" if trace.get("envelope_reviewer") and trace.get("roof") != "unknown"
        else trace.get("declaration_source", "")
    )
    if existing_roof_source == "reviewer":
        record.update({"status": "applied", "source": "reviewer", "applied_value": {"room": trace.get("room_label"), "roof": trace.get("roof"), "overrode_by": "reviewer"}, "validation": validated})
        return record
    selected = validated["roof"]
    source = "ai_determined"
    if selected == "unknown":
        record.update({"status": "needs_contractor_answer", "block_reason": ROOF_CONTRACTOR_QUESTION,
                       "validation": validated, "applied_value": {}, "source": ""})
        return record
    edges = deepcopy(trace.get("edges", []))
    reviewer = str(trace.get("envelope_reviewer") or "Archie AI")
    reviewer_room_geometry_service.post(web, project, {
        "action": "classify_envelope", "trace_id": trace["trace_id"], "reviewer": reviewer,
        "edges": edges, "roof": selected, "openings": trace.get("openings", []),
        "declaration_source": source, "ai_run_id": record["run_id"],
    })
    record.update({"status": _status_for_accuracy(source, record["accuracy"]), "source": source,
                   "applied_value": {"room": trace.get("room_label", room_id), "roof": selected,
                                     "source": source, "ai_run_id": record["run_id"], "evidence": validated.get("evidence", ""),
                                     "label": record["quality_label"]},
                   "validation": validated, "block_reason": ""})
    return record


def _has_explicit_roof_evidence(packet):
    for row in packet.get("facts", []) if isinstance(packet, dict) else []:
        text = str(row.get("text", "")) if isinstance(row, dict) else ""
        if re.search(r"\b(?:roof|slab|floor|tenancy|level)\b.{0,60}\b(?:above|over|below|beneath|directly)\b|"
                     r"\b(?:above|over|below|beneath|directly)\b.{0,60}\b(?:roof|slab|floor|tenancy|level)\b",
                     text, re.I):
            return True
    return False


def _answer_roof(web, project, root, data):
    if data.get("task") != "P5_roof":
        raise ValueError("Contractor answer is only supported for roof exposure.")
    target = str(data.get("target", ""))
    record = _current_task(root, "P5_roof", target)
    if not record or record.get("status") != "needs_contractor_answer":
        raise ValueError("This roof question is no longer awaiting a contractor answer.")
    answer = str(data.get("answer", ""))
    answer_roof = {"floor_tenancy_above": "not_exposed", "roof_directly_above": "exposed", "not_sure": "unknown"}
    if answer not in answer_roof:
        raise ValueError("Choose floor/tenancy above, roof directly above, or not sure.")
    paths = reviewer_room_geometry_service._paths(project)
    artifact = _read(paths["artifact"], reviewer_room_geometry.empty_artifact())
    trace = next((row for row in artifact.get("records", []) if row.get("room_id") == target), None)
    if not trace:
        raise ValueError("The current room trace was not found; refresh the project and try again.")
    reviewer_room_geometry_service.post(web, project, {
        "action": "classify_envelope", "trace_id": trace["trace_id"],
        "reviewer": "Answered by the contractor", "edges": deepcopy(trace.get("edges", [])),
        "roof": answer_roof[answer], "openings": deepcopy(trace.get("openings", [])),
        "confirm_roof": answer != "not_sure",
    })
    if answer == "not_sure":
        record.update({"status": "contractor_answered_not_sure", "source": "reviewer",
                       "applied_value": {}, "contractor_answer": answer,
                       "contractor_answered_by": "Answered by the contractor",
                       "block_reason": "Roof exposure remains unknown and not assessed."})
    else:
        record.update({"status": "applied", "source": "reviewer",
                       "applied_value": {"room": trace.get("room_label", target), "roof": answer_roof[answer],
                                         "source": "reviewer", "label": "Answered by the contractor"},
                       "contractor_answer": answer, "contractor_answered_by": "Answered by the contractor",
                       "block_reason": ""})
    _update_record(root, record)
    _export_determinations(root)
    return get(web, project)


def post(web, project, data):
    root = _root(project)
    action = data.get("action", "")
    if action in {"run_all", "build"}:
        return run_all(web, project)
    if action == "answer_roof":
        return _answer_roof(web, project, root, data)
    task, target = str(data.get("task", "")), str(data.get("target", ""))
    if task not in autonomous_tasks.TASKS or not target:
        raise ValueError("Choose a supported task and target.")
    record = _current_task(root, task, target)
    if not record:
        raise ValueError("Build the task packet before validating a reply.")
    if record.get("status") == "blocked" and record.get("block_reason") and not data.get("reply"):
        return get(web, project)
    if action != "validate_apply":
        raise ValueError("Action must be run_all or validate_apply.")
    reply = str(data.get("reply", ""))
    if len(reply.encode("utf-8")) > 64_000:
        raise ValueError("Reply exceeds the 64 KB task limit.")
    model_note = str(data.get("model_note", ""))[:160]
    record["stand_in"] = data.get("stand_in") is True
    reply_path = _archive_reply(root, record, reply, model_note)
    record["reply"] = reply
    record["reply_hash"] = hashlib.sha256(reply.encode("utf-8")).hexdigest()
    record["model_note"] = model_note
    try:
        if task == "P1_site":
            validated = autonomous_tasks.validate_site_reply(record["packet"], reply)
            record = _apply_p1(root, project, record, validated)
        elif task == "P2_north":
            validated = autonomous_tasks.validate_north_reply(record["packet"], reply)
            record = _apply_p2(web, project, root, record, validated)
        else:
            validated = autonomous_tasks.validate_roof_reply(record["packet"], reply)
            record = _apply_p5(web, project, root, record, validated)
    except (ValueError, KeyError, TypeError) as error:
        record["status"] = "waiting_for_reply"
        record["block_reason"] = str(error)
        record["validation"] = {"valid": False, "error": str(error)}
        archived = _read(reply_path, {})
        archived.update({"outcome": "rejected", "reason": str(error)})
        _write(reply_path, archived)
        record["reply_attempts"][-1].update({"outcome": "rejected", "reason": str(error)})
        _update_record(root, record)
        return get(web, project)
    archived = _read(reply_path, {})
    archived["outcome"] = record.get("status", "accepted")
    archived["reason"] = record.get("block_reason", "")
    _write(reply_path, archived)
    record["reply_attempts"][-1].update({"outcome": archived["outcome"], "reason": archived["reason"]})
    record["validation"] = {"valid": True, "result": record.get("validation", {})}
    _update_record(root, record)
    _export_determinations(root)
    productization.record_change_if_fingerprint_changed(
        root, action="autonomous_task_applied", target=f"{task}/{target}",
        previous_fingerprint="", new_fingerprint=record["input_fingerprint"], affected_ids=[record["run_id"]])
    return get(web, project)


def image(web, project, data):
    task, target, run_id, name = (str(data.get(key, "")) for key in ("task", "target", "run_id", "name"))
    if task not in autonomous_tasks.TASKS or not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", run_id) or not re.fullmatch(r"image-\d+\.png", name):
        raise ValueError("Autonomous task image request is invalid.")
    path = _task_dir(_root(project), task, target) / "runs" / run_id / name
    resolved = path.resolve()
    if not resolved.is_file() or not resolved.is_relative_to(_root(project).resolve()):
        raise ValueError("Task image is unavailable.")
    return {"path": str(resolved), "content_type": "image/png"}
