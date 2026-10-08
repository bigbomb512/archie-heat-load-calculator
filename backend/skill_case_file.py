"""The case file a skill works from (idea 1): what pass 1 and pass 2 found that is relevant to it.

A sub-skill needs context from across the drawing set: glazing needs the elevations, the window schedule
and the finishes; equipment needs the schedule, the plans and the elevations. Its case file holds:
- an index of every page (type, title, level, kinds of information), so it knows the whole set;
- every pass 1 reading of the kinds of information it works on, with page references;
- the pass 2 values already read for those kinds (equipment so far), with their evidence labels;
- images of the pages that matter most to it (most relevant readings first), from pass 1's renders.

Mostly text, so a skill gets far more context than images alone would allow within the plan's usage.
"""

import json
from pathlib import Path

from backend import page_extraction_service as pass2, page_inventory_service as pass1
from backend.job_runner import read_json

ALL_KINDS = ("room_geometry", "ceiling_height", "materials_construction", "windows_glazing", "equipment_appliances",
             "lighting", "people_occupancy", "operating_hours", "airflow_ventilation", "hvac_plant", "location_orientation")
ENVELOPE = ("materials_construction", "room_geometry", "windows_glazing", "location_orientation")
AIR = ("airflow_ventilation", "hvac_plant", "people_occupancy", "room_geometry")
# The kinds of pass 1 information each sub-skill works on. Unlisted sub-skills get the page index only.
SUBSKILL_KINDS = {
    "sheet_identity": (), "revision_scope": (), "page_relationships": (),
    "site_clue_extraction": ("location_orientation",),
    "room_identity_use": ("room_geometry", "people_occupancy", "equipment_appliances"),
    "room_boundaries_areas": ("room_geometry",),
    "ceiling_height_volume": ("ceiling_height", "room_geometry"),
    "occupancy_seating": ("people_occupancy", "room_geometry"),
    "lighting_evidence": ("lighting",),
    "equipment_evidence": ("equipment_appliances",),
    "schedule_evidence": ("operating_hours", "people_occupancy"),
    "surface_inventory": ENVELOPE, "surface_area": ENVELOPE, "construction_matching": ENVELOPE, "boundary_resolution": ENVELOPE,
    "cross_sheet_opening_match": ("windows_glazing", "room_geometry"),
    "glazing_properties": ("windows_glazing", "materials_construction"),
    "exposure_orientation": ("windows_glazing", "location_orientation"),
    "shading": ("windows_glazing", "materials_construction"),
    "solar_source": ("location_orientation",),
    "outside_air": AIR, "infiltration": AIR, "process_exhaust": AIR, "make_up_air": AIR, "airflow_deduplication": AIR,
    "system_detection": ("hvac_plant", "airflow_ventilation"), "zone_ownership": ("hvac_plant", "airflow_ventilation"),
    "plant_detection": ("hvac_plant",),
    "information_needs": ALL_KINDS,
}
IMAGE_LIMIT = {"room_boundaries_areas": 6, "room_identity_use": 6, "cross_sheet_opening_match": 6, "glazing_properties": 6,
               "information_needs": 0}
DEFAULT_IMAGES = 4
MAX_READINGS = 400
# Pages that carry values first: schedules and plans before renders and covers.
TYPE_RANK = {"schedule": 0, "floor_plan": 1, "reflected_ceiling_plan": 1, "elevation": 2, "section": 2, "services_plan": 2,
             "detail": 3, "notes_or_specification": 3, "site_or_location_plan": 3}


def available(project):
    """The case-file route can run: page reading is on for the job and pass 1 has read the pages."""
    root = Path(project["review_dir"])
    return pass1.enabled(project) and bool(read_json(root / pass1.RESULT_FILE).get("pages"))


def build(subskill_id, project):
    """(case file dict, [page image paths]) for one sub-skill."""
    root = Path(project["review_dir"])
    pages = {int(page): row for page, row in (read_json(root / pass1.RESULT_FILE).get("pages") or {}).items()}
    kinds = set(SUBSKILL_KINDS.get(subskill_id, ()))
    index = [{"page": page, "type": row.get("page_type", ""), "title": row.get("title", ""), "level": row.get("level", ""),
              "kinds": sorted({item.get("kind") for item in row.get("information", [])})}
             for page, row in sorted(pages.items())]
    readings, counts = [], {}
    for page, row in sorted(pages.items()):
        for item in row.get("information", []):
            if item.get("kind") in kinds:
                readings.append({"page": page, "kind": item["kind"], "what": item.get("what", ""), "evidence": item.get("evidence", "")[:160]})
                counts[page] = counts.get(page, 0) + 1
    extracted = {}
    results = read_json(root / pass2.RESULT_FILE)
    for kind in sorted(kinds & set(results)):
        extracted[kind] = [{"value": {key: value for key, value in row["value"].items() if value not in ("", None)},
                            "pages": row["pages"], "evidence": row["evidence"], "conflicts": row.get("conflicts", {}),
                            "name_found_in_page_text": row.get("text_match")}
                           for row in results[kind].get("findings", [])]
    limit = IMAGE_LIMIT.get(subskill_id, DEFAULT_IMAGES)
    ranked = sorted(counts, key=lambda page: (-counts[page], TYPE_RANK.get(pages[page].get("page_type"), 4), page))[:limit]
    renders = {int(path.stem.split("-")[1]): path for path in (root / pass1.WORK_DIR / "pages").glob("p-*.png")}
    images = [renders[page] for page in ranked if page in renders]
    case = {"job": job_context(project),
            "note": ("Pages are numbered as in the PDF. 'readings' are what an AI page-by-page pass found on each page; "
                     "'extracted' are values already read for these kinds. Attached images are the listed pages. Cite pages "
                     "for every value; treat readings as leads to check, not as approved values."),
            "page_index": index, "readings": readings[:MAX_READINGS], "readings_omitted": max(0, len(readings) - MAX_READINGS),
            "extracted": extracted, "attached_pages": [page for page in ranked if page in renders]}
    return case, images


def job_context(project):
    """What every skill needs to name things the way the app does: the source, ID formats, rooms and room types."""
    import hashlib
    import re
    root = Path(project["review_dir"])
    pages = read_json(root / pass1.RESULT_FILE).get("pages") or {}
    pdf = Path(str(project.get("pdf") or ""))
    source_id = "pdf-" + hashlib.sha256(json_bytes(pages)).hexdigest()[:12]
    rooms = []
    for row in read_json(root / "room_use_resolution.json").get("records", []):
        if isinstance(row, dict) and row.get("room_id"):
            rooms.append({"room_id": row["room_id"], "label": row.get("original_label") or row.get("label", ""),
                          "level": row.get("level", ""), "taxonomy_id": row.get("taxonomy_id", ""), "scope": row.get("space_scope", "")})
    try:
        taxonomy = json.loads((Path(__file__).resolve().parents[1] / "config" / "au_room_use_taxonomy_v1.json").read_text(encoding="utf-8"))
        room_types = [{"taxonomy_id": key, "label": value.get("label", key)} for key, value in taxonomy.get("categories", {}).items()]
    except (OSError, ValueError):
        room_types = []
    slug = lambda value: re.sub(r"[^a-z0-9]+", "-", str(value or "").casefold()).strip("-") or "unassigned"
    return {
        "source_document": {"source_document_id": source_id, "file_name": pdf.name, "page_count": len(pages),
                            "note": "This is the project's source identity and version. Physical page numbers are the PDF's pages."},
        "id_formats": {"room_id": "room-use:<level slug>:<room label slug> (e.g. " + ", ".join(
                           f"room-use:{slug(row['level'] or 'Unassigned level')}:{slug(row['label'])}" for row in rooms[:3]) + ")",
                       "component_id": "<source_document_id>:<drawing number or p<page>>:<printed tag or label slug>"},
        "known_rooms": rooms,
        "room_types": room_types,
        "when_prerequisites_are_empty": ("If a prerequisite proposal is empty, failed or missing, use known_rooms, the readings and the "
                                          "attached pages directly. Do not refuse for lack of validated prerequisites: give the values the "
                                          "evidence supports, record how you matched each to a room as an inference, and leave a field "
                                          "unresolved only when the drawings really don't show it."),
    }


def json_bytes(value):
    return json.dumps(value, sort_keys=True, default=str).encode("utf-8")
