"""Project lifecycle for draft-only AI room-use resolution."""

import json
import re
import threading
from copy import deepcopy
from pathlib import Path

from ai import room_use_resolution
from ai import ai_preliminary
from backend.ai_preliminary_service import _proposal_for_resolution, _room_use_sources
from backend.vision_extraction_service import _atomic_json


def _paths(project):
    root = Path(project["review_dir"])
    return {
        "root": root,
        "artifact": root / "room_use_resolution.json",
        "building": root / "building_evidence.json",
        "vision": root / "vision_response.json",
        "run": root / "ai_preliminary_run.json",
    }


def _read(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


_USE_CACHE = {}
_USE_CACHE_LOCK = threading.Lock()


def gather_use_evidence(root):
    """Cache read-only evidence by every source file version; calorific values do not enter the evidence hash."""
    root = Path(root)
    names = ["reviewer_room_geometry.json", "ai_input.json", "spatial_ocr.json", "vector_geometry.json",
             "page_inventory.json", "equipment_catalogue_resolution.json"]
    files = [root / name for name in names] + sorted((root / "ai_tasks").rglob("*.json"))
    # Include extraction files by their public constants rather than guessing their names.
    from backend import page_extraction_service, page_inventory_service
    files += [root / page_extraction_service.RESULT_FILE, root / page_extraction_service.DECISIONS_FILE,
              root / page_inventory_service.RESULT_FILE]
    inputs = _read(root / "ai_input.json", {})
    source_pdf = inputs.get("source_pdf") or inputs.get("source_files", {}).get("pdf")
    if source_pdf:
        files.append(Path(source_pdf))
    signature = []
    for path in files:
        try:
            stat = path.stat()
            signature.append((str(path), stat.st_mtime_ns, stat.st_size))
        except OSError:
            signature.append((str(path), None))
    key = str(root.resolve())
    with _USE_CACHE_LOCK:
        saved = _USE_CACHE.get(key)
        if saved and saved[0] == signature:
            return deepcopy(saved[1])
    value = _gather_use_evidence(root)
    with _USE_CACHE_LOCK:
        _USE_CACHE[key] = (signature, deepcopy(value))
        while len(_USE_CACHE) > 8:
            _USE_CACHE.pop(next(iter(_USE_CACHE)))
    return value

def _gather_use_evidence(root):
    """Read existing placement and plan geometry; no provider, catalogue writes, or name changes."""
    from shapely.geometry import Polygon, Point, box
    from ai import equipment_heat
    from backend import equipment_catalogue_service as equipment, autonomous_tasks_service as tasks
    from backend.job_runner import read_json
    root = Path(root)
    traces = [row for row in read_json(root / "reviewer_room_geometry.json").get("records", [])
              if len(row.get("points_image_px") or []) >= 4 and row.get("freshness") != "stale" and not row.get("stand_in")]
    contexts, polygons = {}, []
    for trace in traces:
        polygon = Polygon(trace["points_image_px"])
        if not polygon.is_valid or polygon.area <= 0:
            continue
        key = trace.get("room_id") or room_use_resolution.room_identity(trace.get("room_label"), trace.get("level_name") or "Unassigned level")
        context = contexts.setdefault(key, {"contents": [], "area_m2": 0, "enclosed": True, "outside_wall": False, "storefront": False})
        context["area_m2"] += trace.get("ai_measured_area_m2") or 0
        context["enclosed"] = context["enclosed"] and trace.get("method") not in {"open_zone", "functional_zone"}
        edges = {edge.get("index"): edge.get("boundary") for edge in trace.get("edges", [])}
        context["outside_wall"] |= any(value in {"external", "mall"} for value in edges.values())
        context.setdefault("position_evidence", []).append({"page": trace.get("page"), "excerpt": "Traced wall boundaries and openings", "trace_id": trace.get("trace_id")})
        context["storefront"] |= any(edges.get(opening.get("edge_index")) in {"external", "mall"} for opening in trace.get("openings", []))
        polygons.append((key, trace, polygon))
    if not contexts:
        return {}
    names = sorted({row[1].get("room_label") for row in polygons if row[1].get("room_label")})
    name_ids = {name: sorted({key for key, trace, _ in polygons if trace.get("room_label") == name}) for name in names}
    def place_name(name):
        ids = name_ids.get(name, [])
        return ids[0] if len(ids) == 1 else None
    def add(key, name, page, source):
        if key in contexts and name:
            contexts[key]["contents"].append({"name": str(name), "evidence": [{"page": page, "excerpt": str(name), "method": source}] if page else [], "source": source})
    resolved = equipment.resolve({"review_dir": str(root)}, names, persist=False)
    records = [row for row in resolved["records"] if not (row.get("status") == "excluded" and row.get("note") == "Rejected by the operator.")]
    code_items = {}
    for row in records:
        code = str(row.get("value", {}).get("code") or "").casefold()
        if code:
            code_items.setdefault(code, []).append(row)
    vectors = read_json(root / "vector_geometry.json").get("geometry_key_points", {}).get("pages", [])
    systems = {page.get("page"): page.get("coordinate_systems", {}).get("image_px", {}) for page in vectors}
    spatial = {page.get("page"): page for page in read_json(root / "spatial_ocr.json").get("pages", [])}
    inputs = read_json(root / "ai_input.json")
    inventory = read_json(root / "page_inventory.json").get("pages", {})
    doc = None
    pdf_path = inputs.get("source_pdf") or inputs.get("source_files", {}).get("pdf")
    if pdf_path and Path(pdf_path).is_file():
        import pdfplumber
        try:
            doc = pdfplumber.open(pdf_path)
        except (OSError, RuntimeError):
            pass
    located = {}
    registered_pages = {}
    def page_words(number):
        sheet = doc.pages[number - 1]
        return [{"text": word["text"], "bbox": [word["x0"], word["top"], word["x1"], word["bottom"]]}
                for word in sheet.extract_words()]
    try:
        for page in sorted({trace.get("page") for _, trace, _ in polygons if isinstance(trace.get("page"), int)}):
            system, saved = systems.get(page, {}), spatial.get(page, {})
            width, height = system.get("image_width"), system.get("image_height")
            words, sx, sy = [], None, None
            if doc is not None and 0 < page <= len(doc.pages) and width and height:
                sheet = doc.pages[page - 1]
                sx, sy = width / sheet.width, height / sheet.height
                words = [{"text": word["text"], "bbox": [word["x0"], word["top"], word["x1"], word["bottom"]]}
                         for word in sheet.extract_words()]
            elif width and height and saved.get("width") and saved.get("height"):
                words = saved.get("word_samples", []) + saved.get("standalone_text_items", [])
                sx, sy = width / saved["width"], height / saved["height"]
            if not sx or not sy:
                continue
            local_words = [{**word, "source_page": page} for word in words]
            if doc is not None:
                for number, reading in sorted(inventory.items()):
                    number = int(number)
                    if number == page or not 0 < number <= len(doc.pages) or reading.get("page_type") != "floor_plan" or reading.get("floor_plan_kind") != "proposed_layout":
                        continue
                    sheet = doc.pages[number - 1]
                    if abs(sheet.width - doc.pages[page - 1].width) > 1 or abs(sheet.height - doc.pages[page - 1].height) > 1:
                        continue
                    source_words = page_words(number)
                    registration = room_use_resolution.plan_text_translation(source_words, words)
                    if not registration:
                        continue
                    registered_pages[(page, number)] = registration
                    dx, dy = registration["dx_pt"], registration["dy_pt"]
                    local_words.extend({"text": word["text"], "source_page": number,
                        "bbox": [word["bbox"][0]+dx, word["bbox"][1]+dy, word["bbox"][2]+dx, word["bbox"][3]+dy]}
                        for word in source_words)
            for word in local_words:
                bounds = word.get("bbox", [])
                if len(bounds) != 4:
                    continue
                point = Point((bounds[0] + bounds[2]) * sx / 2, (bounds[1] + bounds[3]) * sy / 2)
                hits = [(key, polygon.area) for key, trace, polygon in polygons if trace.get("page") == page and polygon.contains(point)]
                # Nested enclosing tenancy outlines must not inherit the contents of their smaller room.
                if not hits:
                    continue
                key = min(hits, key=lambda row: (row[1], row[0]))[0]
                text = str(word.get("text", ""))
                for item in code_items.get(text.casefold(), []):
                    located.setdefault(item["id"], set()).add(key)
                if re.search(r"\b(wc|toilet|urinal|basin|counter|seating|desk|workstation|prep|pizza|dishwash)\b", text, re.I):
                    add(key, text, word["source_page"], "text_inside_outline" if word["source_page"] == page else "registered_plan_text")
                    if word["source_page"] != page:
                        contexts[key].setdefault("registration", []).append({"page": word["source_page"], "target_page": page,
                            **registered_pages[(page, word["source_page"])]})
    finally:
        if doc is not None:
            doc.close()
    for row in records:
        physical = located.get(row["id"], set())
        key = next(iter(physical)) if len(physical) == 1 else place_name(row.get("room")) if not physical else None
        if key:
            add(key, row.get("name"), next(iter(row.get("pages") or []), None), "equipment_code_inside_outline" if physical else
                "equipment_room_placement" if row.get("applicability_evidence", {}).get("room", {}).get("satisfied") is True
                    or any(zone.get("room") == row.get("room") and zone.get("location", "").casefold() == str(row.get("value", {}).get("location", "")).casefold() for zone in resolved.get("zones", {}).values())
                else "equipment_location_label")
    for record in tasks._all_current(root):
        if record.get("status") not in {"applied", "applied_fallback", "below_accuracy_bar"}:
            continue
        packet = record.get("packet", {})
        if record.get("task") == "P4_storefront_wall":
            key = packet.get("room_id")
            if key in contexts and record.get("applied_value"):
                contexts[key]["storefront"] = True
        if record.get("task") != "P6_kitchen":
            continue
        # Bind the plan crop to its physical outline; never attach this evidence just to a room called Kitchen.
        crop = next((image for image in packet.get("images", []) if image.get("page") == packet.get("plan_page") and image.get("bbox_px")), None)
        key = packet.get("room_id")
        if crop:
            region = box(*crop["bbox_px"])
            hits = [(candidate, polygon.area) for candidate, trace, polygon in polygons
                    if trace.get("page") == packet.get("plan_page") and region.area
                    and polygon.contains(region.centroid)
                    and polygon.intersection(region).area / min(region.area, polygon.area) >= .5]
            if hits:
                key = min(hits, key=lambda row: (row[1], row[0]))[0]
        for item in record.get("applied_value", {}).get("items", []):
            if item.get("page") == packet.get("plan_page"):
                add(key, str(item.get("type", "")).replace("_", " "), item.get("page"), "P6_plan_crop")
    inventory = read_json(root / "page_inventory.json").get("pages", {})
    context_text = json.dumps([{key: value for key, value in page.items() if key in {"title", "information", "site"}}
                               for page in inventory.values()], ensure_ascii=False).casefold()
    # Tenancy type selects dining versus retail only; cooking evidence remains room-local.
    tenancy = "hospitality" if re.search(r"\b(restaurant|cafe|café|buffet|bar fitout)\b", context_text) or any(
        equipment_heat.equipment_type(row.get("name")) == "cooking" for row in records) else "retail" if re.search(
        r"\b(retail|shop fitout|store fitout)\b", context_text) else "office" if re.search(r"\boffice\b", context_text) else "unknown"
    tenancy_evidence = [{"page": int(number), "excerpt": str(item.get("evidence") or item.get("what"))[:300]}
        for number, reading in sorted(inventory.items()) for item in reading.get("information", [])
        if re.search(r"\b(restaurant|cafe|café|buffet|retail|office)\b", str(item), re.I)][:8]
    largest = max((context["area_m2"] for context in contexts.values()), default=0)
    for context in contexts.values():
        context["tenancy"] = tenancy
        context["tenancy_evidence"] = tenancy_evidence
        context["largest_front_room"] = context["storefront"] and context["area_m2"] == largest and largest > 6
        context["counter"] = any("counter" in item["name"].casefold() and item["source"] != "equipment_location_label" for item in context["contents"])
        # Collapse duplicate snippets from the text sample and standalone list in a stable order.
        context["contents"] = sorted({json.dumps(item, sort_keys=True): item for item in context["contents"]}.values(), key=lambda item: json.dumps(item, sort_keys=True))
    return contexts

def _sources(paths):
    return _room_use_sources(paths)


def _resolve(paths, existing=None):
    return room_use_resolution.resolve(
        _read(paths["building"], {}), _read(paths["vision"], {}), _proposal_for_resolution(paths, equipment=False), _sources(paths), existing,
        use_evidence=gather_use_evidence(paths["root"]),
    )


def _response(web, project):
    paths = _paths(project)
    raw = _read(paths["artifact"], room_use_resolution.empty_room_use_resolution())
    artifact = room_use_resolution.validate(raw)
    if artifact.get("records") and not room_use_resolution.is_current(artifact, _sources(paths)):
        artifact = deepcopy(artifact)
        artifact["status"] = "stale"
        artifact["stale_reasons"] = ["Drawing evidence, AI proposal, or room-use taxonomy changed. Resolve room uses again."]
    return {"id": project["id"], "room_use_resolution": artifact, "status": artifact.get("status", "needs_review"),
            "taxonomy": {key: value["label"] for key, value in room_use_resolution.load_taxonomy()["categories"].items()},
            "artifact_url": web.safe_link(paths["artifact"]) if paths["artifact"].exists() else ""}


def get(web, project):
    return _response(web, project)


def _save(web, project, artifact, action, affected_ids=None):
    from backend import productization
    paths = _paths(project)
    before = _read(paths["artifact"], room_use_resolution.empty_room_use_resolution())
    checked = room_use_resolution.validate(artifact)
    _atomic_json(paths["artifact"], checked)
    productization.record_change_if_fingerprint_changed(
        paths["root"], action="room_use_resolution_" + action, target=paths["artifact"].name,
        previous_fingerprint=room_use_resolution.fingerprint(before), new_fingerprint=checked["fingerprint"],
        affected_ids=affected_ids or [],
    )
    project["room_use_resolution"] = str(paths["artifact"])
    project["updated_at"] = ai_preliminary.now()
    web.update_project(project)
    return _response(web, project)


def post(web, project, data):
    paths = _paths(project)
    action = str(data.get("action", ""))
    current = _read(paths["artifact"], room_use_resolution.empty_room_use_resolution())
    if action == "resolve":
        artifact = _resolve(paths, current)
        affected = [row["room_id"] for row in artifact.get("records", [])]
    elif action in {"apply_override", "clear_override"}:
        if current.get("records") and not room_use_resolution.is_current(room_use_resolution.validate(current), _sources(paths)):
            raise ValueError("Room-use evidence is stale. Resolve room uses again before changing a classification.")
        room_id = str(data.get("room_id", "")).strip()
        records = current.get("records", [])
        target = next((row for row in records if isinstance(row, dict) and row.get("room_id") == room_id), None)
        if target is None:
            raise ValueError("Room-use action references an unknown room.")
        if action == "apply_override":
            target["override"] = {"taxonomy_id": data.get("taxonomy_id"), "reviewer": str(data.get("reviewer", "")).strip(),
                                  "note": str(data.get("note", "")).strip(), "updated_at": ai_preliminary.now()}
            if not target["override"]["reviewer"]:
                raise ValueError("A room-use override needs a reviewer.")
        else:
            target["override"] = None
        artifact = _resolve(paths, current)
        affected = [room_id]
    else:
        raise ValueError("Room-use action must be resolve, apply_override, or clear_override.")
    return _save(web, project, artifact, action, affected)
