"""Card O part 1: scaled services plans are traceable when a set has no floor plan."""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai.vector_geometry import FALLBACK_BUCKET, fallback_services_plan_pages, geometry_page_refs
from backend import reviewer_room_geometry_service as service


class Web:
    def safe_link(self, path):
        return "/artifact/" + Path(path).name


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print("PASS - " + name)


def sheet(page, role, title, scale="1:50", detected="existing_hvac_or_services_plan"):
    return {"page": page, "plan_role": role, "detected_type": detected, "title": title, "scale": scale}


def mechanical_set():
    return {"drawing_set": {"pages": [
        sheet(1, "reference_context", "Cover sheet & drawing list", "", "cover_or_drawing_list"),
        sheet(4, "existing_hvac_plan", "Base building mechanical services plan"),
        sheet(5, "existing_hvac_plan", "Fitout mechanical services ductwork plan"),
        sheet(6, "existing_hvac_plan", "Fitout mechanical services pipework plan", "NTS"),
        sheet(7, "existing_hvac_plan", "Fan coil unit schedule"),
    ]}, "confirmed_pages": {"floor_plans": [], "existing_hvac_or_services_plans": []}, "design_inputs": {}}


def vector_page_rules():
    pages = [page["page"] for page in fallback_services_plan_pages(mechanical_set())]
    check("scaled services plans fall back when the set has no floor plan", pages == [4, 5])
    check("unscaled plans, schedules and covers never fall back", not {1, 6, 7} & set(pages))
    refs = geometry_page_refs(mechanical_set())
    check("vector geometry includes the fallback pages with their own bucket",
          [(ref["page"], ref["evidence_bucket"]) for ref in refs] == [(4, FALLBACK_BUCKET), (5, FALLBACK_BUCKET)])

    with_plan = mechanical_set()
    with_plan["drawing_set"]["pages"].append(sheet(2, "main_floor_plan", "General arrangement plan", "1:50", "floor_plan"))
    check("a set with a floor plan never falls back", fallback_services_plan_pages(with_plan) == [])
    check("a set with a floor plan adds no services pages to vector geometry",
          all(ref["evidence_bucket"] != FALLBACK_BUCKET for ref in geometry_page_refs(with_plan)))
    confirmed_plan = mechanical_set()
    confirmed_plan["confirmed_pages"]["floor_plans"] = [{"page": 9, "title": "Floor plan"}]
    check("a confirmed floor plan also disables the fallback", fallback_services_plan_pages(confirmed_plan) == [])


def roles(*rows):
    return {page: {"page": page, "proposed_role": role, "main_scale": scale} for page, role, scale in rows}


def trace_page_rules():
    vector = {4: {}, 5: {}, 6: {}}
    eligible = service._fallback_trace_page_numbers(
        roles((4, "existing_hvac_plan", "1:50"), (5, "existing_hvac_plan", "1:50"), (6, "existing_hvac_plan", ""), (7, "reference", "")),
        {}, {}, vector)
    check("trace fallback needs a services role, a declared scale and vector geometry", eligible == {4, 5})
    check("trace fallback is off when any page is a floor plan",
          service._fallback_trace_page_numbers(roles((2, "main_floor_plan", "1:100"), (4, "existing_hvac_plan", "1:50")), {}, {}, vector) == set())


def page_context_integration():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        image_system = {"image_px": {"image_width": 2000, "image_height": 1400}}
        (root / "vector_geometry.json").write_text(json.dumps({"geometry_key_points": {"pages": [
            {"page": 5, "coordinate_systems": image_system, "image": "screenshots/page_005.png"},
        ]}}), encoding="utf-8")
        (root / "drawing_coverage.json").write_text(json.dumps({"page_roles": [
            {"page": 5, "proposed_role": "existing_hvac_plan", "main_scale": "1:50", "title": "Ductwork plan"},
            {"page": 7, "proposed_role": "reference", "main_scale": ""},
        ]}), encoding="utf-8")
        (root / "ai_input.json").write_text(json.dumps({"drawing_set": {"pages": []}}), encoding="utf-8")
        pages = service._page_context(service._paths({"review_dir": str(root)}), Web())
        check("the services plan is offered for tracing and flagged as a fallback",
              [(page["page"], page["fallback_plan"]) for page in pages] == [(5, True)]
              and "No architectural floor plan" in pages[0]["fallback_reason"]
              and pages[0]["scale_denominator"] == 50)
        check("a services plan without printed dimensions warns that tracing cannot be calibrated",
              pages[0]["printed_dimensions_found"] is False
              and "Upload the architectural drawings" in pages[0]["calibration_warning"])
        (root / "spatial_ocr.json").write_text(json.dumps({"pages": [
            {"page": 5, "dimension_candidates": [{"text": "4200", "bbox": [10, 10, 40, 20]}]}]}), encoding="utf-8")
        pages = service._page_context(service._paths({"review_dir": str(root)}), Web())
        check("a services plan with printed dimensions has no calibration warning",
              pages[0]["printed_dimensions_found"] is True and pages[0]["calibration_warning"] == "")


def main():
    vector_page_rules()
    trace_page_rules()
    page_context_integration()


if __name__ == "__main__":
    main()
