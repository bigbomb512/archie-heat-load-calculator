"""Card O part 2: a reviewer adds a room detection could not find, then traces it."""

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai.drawing_coverage import build_drawing_coverage
from ai.room_use_resolution import room_identity
from backend import ai_preliminary_service
from backend import model_input_resolution_service
from backend import reviewer_room_geometry_service as service
from backend.room_proposal import base_proposal, room_proposal


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print("PASS - " + name)


def expect_error(name, call, fragment):
    try:
        call()
    except ValueError as error:
        check(name, fragment in str(error))
        return
    raise AssertionError(name + " (no error raised)")


VECTOR_PAGE = {"page": 1, "coordinate_systems": {"image_px": {"image_width": 1000, "image_height": 800}},
               "confirmation_line_candidates": [], "image": "screenshots/page_001.png"}
PAGE_CONTEXT = {"page": 1, "title": "Ductwork plan", "proposed_role": "existing_hvac_plan", "level_name": "Level 2",
                "scale_denominator": 100, "image_px_per_pt": 3.5277777778, "image_width_px": 1000, "image_height_px": 800,
                "preview_url": "", "preview_matches_vector_coordinates": True,
                "fallback_plan": True, "fallback_reason": service.FALLBACK_TRACE_REASON}


def fixture(root):
    ai = {"source_pdf_fingerprint": "pdf-fixture", "drawing_set": {"pages": [
        {"page": 1, "title": "Fitout mechanical services ductwork plan", "drawing_number": "M-210", "level_name": "Level 2"}]}}
    coverage = build_drawing_coverage(ai)
    coverage["page_roles"][0].update(proposed_role="existing_hvac_plan", main_scale="1:100")
    for name, value in {"ai_input": ai, "drawing_coverage": coverage,
                        "vector_geometry": {"geometry_key_points": {"pages": [VECTOR_PAGE]}},
                        "building_evidence": {"spaces": []}, "ai_preliminary_run": {}}.items():
        (root / f"{name}.json").write_text(json.dumps(value), encoding="utf-8")
    web = SimpleNamespace(safe_link=lambda path: str(path), update_project=lambda project: None)
    return web, {"id": "mechanical-only", "name": "Mechanical fixture", "review_dir": str(root)}


def add(web, project, **extra):
    data = {"action": "add_room", "label": "Kiosk", "level_name": "Level 2", "taxonomy_id": "retail",
            "reviewer": "QA", "page": 1, **extra}
    return service.post(web, project, data)


def trace(web, project, room_id):
    payload = {"action": "save", "room_id": room_id, "page": 1,
               "points_image_px": [[100, 100], [300, 100], [300, 300], [100, 300], [100, 100]],
               "snapped_line_ids": [None] * 5, "dimension_points_image_px": [[100, 600], [200, 600]],
               "dimension_value_mm": 1000, "reviewer": "QA", "source_pdf_fingerprint": "pdf-fixture",
               "vector_page_fingerprint": service._page_fp(VECTOR_PAGE)}
    with patch.object(service, "_page_context", return_value=[PAGE_CONTEXT]):
        return service.post(web, project, payload)


def validation_checks():
    with tempfile.TemporaryDirectory() as directory:
        web, project = fixture(Path(directory))
        expect_error("a room use is required", lambda: add(web, project, taxonomy_id=""), "Choose a room use")
        expect_error("not a room is not a use for an added room", lambda: add(web, project, taxonomy_id="not_a_room"), "Choose a room use")
        expect_error("a reviewer is required", lambda: add(web, project, reviewer=" "), "name or initials")
        expect_error("a room name is required", lambda: add(web, project, label=" "), "room name")
        add(web, project)
        expect_error("duplicate rooms are refused", lambda: add(web, project, label="kiosk"), "already exists on Level 2")
        expect_error("only reviewer-added rooms can be removed",
                     lambda: service.post(web, project, {"action": "remove_room", "room_id": "room-use:level-2:office", "reviewer": "QA"}),
                     "Only rooms added by a reviewer")


def end_to_end_checks():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        web, project = fixture(root)
        check("no rooms before the reviewer adds one", service.get(web, project)["rooms"] == [])
        response = add(web, project)
        kiosk_id = room_identity("Kiosk", "Level 2")
        kiosk = next(row for row in response["rooms"] if row["room_id"] == kiosk_id)
        check("the added room is offered for tracing and marked as reviewer-added", kiosk.get("reviewer_added") is True)
        check("the trace response offers room uses without not-a-room and the page level",
              "retail" in response["room_uses"] and "not_a_room" not in response["room_uses"] and "Level 2" in response["levels"])
        room_use = json.loads((root / "room_use_resolution.json").read_text())
        record = next(row for row in room_use["records"] if row["room_id"] == kiosk_id)
        check("the reviewer's room use is applied, so the room is never unresolved",
              record["taxonomy_id"] == "retail" and record["space_scope"] == "comfort_hvac" and record["override"]["reviewer"] == "QA")
        proposal = room_proposal(json.loads((root / "ai_preliminary_run.json").read_text()), root)
        check("the shared room proposal includes the added room",
              [row["label"] for row in proposal["rooms"]] == ["Kiosk"] and proposal["rooms"][0]["source"] == "reviewer_added")

        saved = trace(web, project, kiosk_id)
        check("a trace on a services plan is saved and flagged",
              saved["reviewer_room_geometry"]["records"][0].get("fallback_plan") is True)
        check("saving a trace keeps the reviewer-added room", [row["label"] for row in saved["reviewer_room_geometry"].get("rooms", [])] == ["Kiosk"])

        model_input_resolution_service.post(web, project, {"action": "resolve"})
        scope = ai_preliminary_service.get(web, project)["room_scope"]
        row = next((row for row in scope["candidates"] if row["label"] == "Kiosk"), None)
        check("the traced added room reaches the room confirmation with its traced area",
              row is not None and row["status"] == "calculated" and row["area_m2"] == 4.0 and row["area_origin"] == "reviewer_traced")
        ai_preliminary_service.post(web, project, {"action": "confirm_room_scope", "reviewer": "QA",
                                                   "candidate_fingerprint": scope["candidate_fingerprint"],
                                                   "rows": [{"key": item["key"], "include": True} for item in scope["candidates"]]})
        report = ai_preliminary_service.post(web, project, {"action": "calculate"})["hourly_ai_preliminary_load_report"]
        check("the draft load is calculated from the added room",
              [room["label"] for room in report["confirmed_rooms"]] == ["Kiosk"] and report["included_scope_peak"].get("total_kw", 0) > 0)

        removed = service.post(web, project, {"action": "remove_room", "room_id": kiosk_id, "reviewer": "QA"})
        check("removing the room drops it and its traces",
              not removed["rooms"] and not removed["reviewer_room_geometry"]["records"]
              and not removed["reviewer_room_geometry"].get("rooms"))
        check("removing the room makes the draft model out of date",
              ai_preliminary_service.get(web, project)["status"] == "stale")


def compatibility_checks():
    run = {"local_room_inference_proposal": {"rooms": [{"label": "Bar", "level_name": "Unassigned level"}]}}
    with tempfile.TemporaryDirectory() as directory:
        check("without reviewer rooms the shared reader returns the detected proposal unchanged",
              room_proposal(run, directory) == base_proposal(run) == {"rooms": [{"label": "Bar", "level_name": "Unassigned level"}]})
    check("a legacy placeholder room list is normalised", base_proposal({"manual_placeholder_entities": [{"label": "A"}]}) == {"rooms": [{"label": "A"}]})


def main():
    validation_checks()
    end_to_end_checks()
    compatibility_checks()


if __name__ == "__main__":
    main()
