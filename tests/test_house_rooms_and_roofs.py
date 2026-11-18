"""Turramurra house (2026-11-18): cooled rooms lost from the room model, and the tenancy roof question asked of a house.

A. The room-size step's follow-up for one room replaced the plan's earlier answer for five others; the ground-floor
   plan's wall-style answer chose no wall pen, so its rooms were never named or outlined; and the Phase 1 check asked
   the first-floor and basement plans to outline the ground floor's rooms.
B. A house's above-exposure follows from its levels, never from "what is above the tenancy".
"""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend import ai_preliminary_service as model
from backend import autonomous_tasks_service as tasks
from backend import job_service, need_answers_service
from backend import phase1_supervisor_service as supervisor
from backend import reviewer_room_geometry_service as geometry


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _size_run(target, run_id, created, rooms_asked, answered, *, scale=27.0, status="below_accuracy_bar", not_found=()):
    record = {"task": "P0_room_sizes", "run_id": run_id, "created_at": created, "status": status,
              "packet": {"task": "P0_room_sizes", "page": 8, "room_names": rooms_asked, "mm_per_image_px": scale,
                         "level_name": "Ground Floor"},
              "applied_value": {"rooms": [{"label": label, "area_m2": area, "source": "AI estimate from the plan",
                                           "method": "room_size_estimated", "page": 8, "level_name": "Ground Floor"}
                                          for label, area in answered],
                                "not_found": [{"room": name, "reason": "not here"} for name in not_found]}}
    _write(target / "runs" / run_id / "record.json", record)
    return record


class RoomSizeAnswersTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.target = Path(self.directory.name) / "ai_tasks" / "P0_room_sizes" / "page-8"

    def tearDown(self):
        self.directory.cleanup()

    def labels(self, current):
        return {area["label"]: area["area_m2"] for _record, area in geometry.room_size_answers(self.target, current)}

    def test_follow_up_for_one_room_keeps_the_other_rooms_answers(self):
        _size_run(self.target, "r1", "2026-11-18T06:18:19", ["Kitchen", "Media", "Sitting"], [("Kitchen", 19.34), ("Media", 18.04)])
        current = _size_run(self.target, "r2", "2026-11-18T06:28:14", ["Sitting"], [("Sitting", 21.05)])
        self.assertEqual(self.labels(current), {"Kitchen": 19.34, "Media": 18.04, "Sitting": 21.05})

    def test_a_room_asked_again_takes_the_later_answer_found_or_not(self):
        _size_run(self.target, "r1", "2026-11-18T06:18:19", ["Kitchen", "Garage"], [("Kitchen", 19.34), ("Garage", 30.0)])
        current = _size_run(self.target, "r2", "2026-11-18T06:20:00", ["Kitchen", "Garage"], [("Kitchen", 17.0)],
                            not_found=["Garage"])
        self.assertEqual(self.labels(current), {"Kitchen": 17.0})

    def test_answers_at_another_plan_scale_are_not_carried_forward(self):
        _size_run(self.target, "r1", "2026-11-18T06:18:19", ["Kitchen"], [("Kitchen", 19.34)], scale=40.0)
        current = _size_run(self.target, "r2", "2026-11-18T06:28:14", ["Sitting"], [("Sitting", 21.05)])
        self.assertEqual(self.labels(current), {"Sitting": 21.05})

    def test_pending_follow_up_keeps_earlier_answers(self):
        _size_run(self.target, "r1", "2026-11-18T06:18:19", ["Kitchen"], [("Kitchen", 19.34)])
        current = _size_run(self.target, "r2", "2026-11-18T06:28:14", ["Kitchen"], [], status="waiting_for_reply")
        self.assertEqual(self.labels(current), {"Kitchen": 19.34})


class WallStyleBorrowingTests(unittest.TestCase):
    def records(self, page, styles):
        return [{"status": "applied", "applied_value": {"wall_style_ids": styles}, "packet": {"page": page}}]

    def run_pages(self, chosen, summary):
        plans = [{"page": page} for page in chosen]
        with patch.object(tasks, "_p0_main_geometry_pages", return_value=plans), \
             patch.object(tasks, "_p0_wall_style_records", side_effect=lambda _root, page: self.records(page, chosen[page])), \
             patch.object(tasks, "_p0_context", return_value={"summary": summary}):
            return tasks._page_wall_style_ids(Path("."), 8)

    def test_a_plan_with_no_chosen_wall_pen_borrows_the_sets_other_plans_pens_it_draws(self):
        summary = [{"style_id": "wall", "hatch": False}, {"style_id": "other", "hatch": False}]
        styles, borrowed = self.run_pages({8: [], 9: ["wall"], 25: ["wall", "basement-only"]}, summary)
        self.assertEqual(styles, ["wall"])
        self.assertEqual(borrowed, [{"style_id": "wall", "pages": [9, 25]}])
        self.assertIn("labelled assumption", tasks._borrowed_styles_note(8, borrowed))

    def test_a_plans_own_choice_is_never_replaced(self):
        styles, borrowed = self.run_pages({8: ["own"], 9: ["wall"]}, [{"style_id": "wall", "hatch": False}])
        self.assertEqual((styles, borrowed), (["own"], []))

    def test_hatch_styles_are_not_borrowed(self):
        styles, borrowed = self.run_pages({8: [], 9: ["wall"]}, [{"style_id": "wall", "hatch": True}])
        self.assertEqual((styles, borrowed), ([], []))


class OutlineRoutingTests(unittest.TestCase):
    def test_requested_rooms_are_only_asked_on_the_plan_of_their_level(self):
        plans = [{"page": 8, "level_name": "Ground Floor"}, {"page": 9, "level_name": "First Floor"}]
        rooms = {8: [{"label": "Kitchen"}, {"label": "Dining"}], 9: [{"label": "Bedroom 2"}]}
        applied = {"status": "applied", "packet": {"room_names": []}, "reply": "{}"}
        with patch.object(tasks, "_p0_main_geometry_pages", return_value=plans), \
             patch.object(tasks, "_current_task", return_value=applied), \
             patch.object(tasks, "_inferred_rooms_for_plan", side_effect=lambda _root, page, *a: rooms[page]), \
             patch("backend.house_windows_service.is_dwelling", return_value=False), \
             patch.object(tasks, "_p0_wall_style_records", return_value=[{"status": "applied"}]), \
             patch.object(tasks, "_p0_context", return_value={"image": None, "viewport": (0, 0, 1, 1)}), \
             patch.object(tasks, "_p0_calibration_ready", return_value=({"mm_per_px": 10}, "")), \
             patch.object(tasks, "_p0_is_raster", return_value=False), \
             patch.object(tasks, "_page_wall_style_ids", return_value=(["wall"], [])), \
             patch.object(tasks, "_completed_wall_styles", return_value=(["wall"], [])), \
             patch.object(tasks, "_p0_walls_areas", return_value=(None, [], [])), \
             patch.object(tasks.room_outline, "render_candidate_areas", return_value=(None, {"factor": 1})), \
             patch.object(tasks, "_p0_name_sheets", return_value=([], [])), \
             patch.object(tasks, "_p0_page_room_text", return_value=[]), \
             patch.object(tasks, "_png_bytes", return_value=b""):
            rows = tasks._p0_outline_packets(Path("."), requested_rooms=["Kitchen"])
        self.assertEqual([(row[1], row[2]["room_names"]) for row in rows], [("page-8", ["Kitchen"])])

    def test_a_plan_with_no_wall_line_at_all_still_gets_its_open_plan_outlines(self):
        plans = [{"page": 8, "level_name": "Ground Floor"}]
        blocked = {"status": "blocked", "packet": {"task": "P0_room_names", "page": 8},
                   "block_reason": "No wall styles were identified across the complete legend"}
        with patch.object(tasks, "_p0_main_geometry_pages", return_value=plans), \
             patch.object(tasks, "_tenancy_waiting", return_value=False), \
             patch.object(tasks, "_current_task", return_value=blocked), \
             patch.object(tasks, "_inferred_rooms_for_plan", return_value=[{"label": "Kitchen"}, {"label": "Entry"}]), \
             patch.object(tasks, "_inferred_room_data", return_value=[]), \
             patch("backend.house_windows_service.is_dwelling", return_value=False), \
             patch.object(tasks, "_p0_wall_style_records", return_value=[{"status": "applied"}]), \
             patch.object(tasks, "_p0_context", return_value={"image": None, "viewport": (0, 0, 1, 1), "objects": []}), \
             patch.object(tasks, "_p0_calibration_ready", return_value=({"mm_per_px": 10}, "")), \
             patch.object(tasks, "_p0_is_raster", return_value=False), \
             patch.object(tasks, "_page_wall_style_ids", return_value=([], [])), \
             patch.object(tasks, "_p0_walls_areas", return_value=(None, [], [])), \
             patch.object(tasks.room_outline, "render_candidate_areas", return_value=(None, {"factor": 1})), \
             patch.object(tasks, "_p0_name_sheets", return_value=([], [])), \
             patch.object(tasks, "_p0_page_room_text", return_value=[]), \
             patch.object(tasks, "_png_bytes", return_value=b""):
            rows = tasks._p0_outline_packets(Path("."))
        self.assertEqual([(row[1], row[2]["room_names"]) for row in rows], [("page-8", ["Entry", "Kitchen"])])


def house(levels, roof_pages=()):
    return {"rooms": {}, "floors": {}, "roofs": {}, "levels": levels, "roof_plan_pages": list(roof_pages)}


class HouseLevelRoofTests(unittest.TestCase):
    levels = {-2: "Basement", 0: "Ground Floor", 1: "First Floor"}

    def test_top_storey_is_under_the_roof_citing_the_roof_plan(self):
        basis = model._house_level_roof(house(self.levels, [12]), {"label": "Bedroom 2", "level_name": "First Floor"})
        self.assertEqual(basis["parts"][0]["below"], "open")
        self.assertIn("top level", basis["reason"])
        self.assertIn("roof plan, p. 12", basis["reason"])

    def test_lower_storeys_are_under_the_storey_above_as_a_labelled_assumption(self):
        ground = model._house_level_roof(house(self.levels), {"label": "Kitchen", "level_name": "Ground Floor"}, area_only=True)
        basement = model._house_level_roof(house(self.levels), {"label": "Cellar", "level_name": "Basement"})
        self.assertEqual((ground["parts"][0]["below"], ground["level_above"]), ("cooled", "First Floor"))
        self.assertEqual(basement["level_above"], "Ground Floor")
        self.assertIn("labelled assumption", ground["parts"][0]["reason"])
        self.assertIn("found by its area only", ground["parts"][0]["reason"])
        surfaces = model._house_roof_surfaces({"label": "Kitchen", "level_name": "Ground Floor"}, {"trace_id": "area-only-k", "page": 8},
                                              19.3, ground, {"page": 8}, "")
        self.assertEqual([(row["physical_type"], row["boundary_condition"]) for row in surfaces], [("ceiling", "floor_above")])
        self.assertEqual(surfaces[0]["phase1_assumptions"],
                         [{"component": model.HOUSE_ROOF_NOT_LINED_UP, "reason": ground["parts"][0]["reason"]}])

    def test_single_storey_house_and_unnamed_level(self):
        only = model._house_level_roof(house({0: "Ground Floor"}), {"label": "Living", "level_name": "Ground Floor"})
        self.assertEqual(only["parts"][0]["below"], "open")
        self.assertIsNone(model._house_level_roof(house(self.levels), {"label": "Shed", "level_name": "Unassigned level"}))


class SupervisorHouseTests(unittest.TestCase):
    def room(self, **changes):
        return {"id": "r", "name": "Dining", "scope": "comfort_hvac", "level": "Ground Floor", "area_m2": 40,
                "area_source": "ai_determined", "height": {}, "pages": [8],
                "traces": [{"page": 8, "roof": "unknown", "edges": [{"index": 0, "boundary": "external"}]}], **changes}

    def snapshot(self, room, dwelling):
        return {"rooms": [room], "tasks": [], "height_pages": [], "storefront_pages": [], "report": {},
                "model_inputs": [], "dwelling": dwelling}

    def test_house_rooms_are_not_sent_the_tenancy_roof_check(self):
        self.assertNotIn("roof", [gap["kind"] for gap in supervisor.audit(self.snapshot(self.room(), True))])

    def test_a_shop_keeps_the_roof_check(self):
        self.assertIn("roof", [gap["kind"] for gap in supervisor.audit(self.snapshot(self.room(), False))])

    def test_an_ai_estimated_area_is_still_a_first_priority_gap(self):
        gaps = supervisor.audit(self.snapshot(self.room(area_source="AI estimate from the plan", traces=[]), True))
        self.assertEqual((gaps[0]["kind"], gaps[0]["rank"]), ("room_area", 1))


class HouseFactsTests(unittest.TestCase):
    def test_house_facts_say_what_is_above_each_level(self):
        with tempfile.TemporaryDirectory() as directory:
            _write(Path(directory) / "room_use_resolution.json", {
                "dwelling_context": {"is_dwelling": True},
                "records": [{"level_name": "Ground Floor"}, {"level_name": "First Floor"}, {"level_name": "Basement"}]})
            self.assertEqual(job_service.house_above(directory), {
                "dwelling": True, "above_summary": "Roof above: First Floor rooms; Ground Floor under the First Floor; "
                                                   "Basement under the Ground Floor."})
            project = {"review_dir": directory}
            job_service.save_job_setup(project, {"above": "roof"})
            self.assertEqual(job_service.apply_roof_answer(None, project), 0)
            answer = need_answers_service.apply(None, project, {"kind": "roof_above", "value": "roof"})
            self.assertFalse(answer["applied"])

    def test_a_tenancy_keeps_the_above_the_tenancy_question(self):
        with tempfile.TemporaryDirectory() as directory:
            _write(Path(directory) / "room_use_resolution.json", {"dwelling_context": {"is_dwelling": False}, "records": []})
            self.assertEqual(job_service.house_above(directory), {"dwelling": False})


def _word(text, x, y):
    return {"text": text, "bbox": [x - 5, y - 3, x + 5, y + 3]}


def _sheet(dx=0.0, dy=0.0, title_block=True):
    words = []
    for index, letter in enumerate("ABCDEFG"):
        words += [_word(letter, 380 + 100 * index + dx, 70 + dy), _word(letter, 380 + 100 * index + dx, 1110 + dy)]
    for index, figure in enumerate(["4,420", "3,040", "4,470", "3,710", "4,430", "1,070"]):
        words += [_word(figure, 430 + 100 * index + dx, 95 + dy), _word(figure, 430 + 100 * index + dx, 1085 + dy)]
    if title_block:       # identical on every sheet, never shifted with the plan
        words += [_word("22008", 1500, 1150), _word("2158", 360, 1150), _word("1672", 80, 1135)]
    return words


class PlanRegistrationTests(unittest.TestCase):
    def test_repeated_grid_figures_and_letters_line_two_plans_up(self):
        from ai import house_envelope
        fit = house_envelope.plan_text_registration(_sheet(1.0, -1.2), _sheet())
        self.assertAlmostEqual(fit["dx_pt"], -1.0, places=3)
        self.assertAlmostEqual(fit["dy_pt"], 1.2, places=3)
        self.assertGreaterEqual(fit["votes"], 20)
        self.assertEqual(fit["anchors"][:3], ["A", "B", "C"])

    def test_no_registration_without_enough_shared_text(self):
        from ai import house_envelope
        roof_plan = [_word("CONCRETE", 1040, 270), _word("22008", 1500, 1150), _word("2158", 360, 1150)]
        self.assertIsNone(house_envelope.plan_text_registration(roof_plan, _sheet()))
        self.assertIsNone(house_envelope.plan_text_registration(_sheet()[:6], _sheet()[:6]))


def square(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


class AboveSplitTests(unittest.TestCase):
    def test_a_lower_room_is_split_into_roof_floor_above_void_balcony_and_untraced_upper_floor(self):
        from ai import house_envelope
        upper = [{"label": "Bedroom", "scope": "comfort_hvac", "category": "house_bedroom", "points_mm": square(0, 0, 4000, 4000)},
                 {"label": "Void", "scope": "not_cooled", "category": "not_a_room", "points_mm": square(5000, 0, 8000, 4000)},
                 {"label": "Balcony", "scope": "not_cooled", "category": "outdoor", "points_mm": square(0, 4000, 4000, 6000)}]
        footprint = house_envelope.upper_footprint(upper[:2])           # the 1 m gap (a stair) between them is closed
        room = square(0, 0, 12000, 6000)                               # 72 m²: 16 bedroom, 12 void, 8 balcony, 4 gap, rest roof
        split = {row["over"]: round(row["fraction"] * 72, 1) for row in house_envelope.above_split(room, upper, footprint)}
        self.assertEqual(split, {"cooled": 16.0, "void": 12.0, "outdoor": 8.0, "upper_untraced": 4.0, "roof": 32.0})


def _traces():
    def trace(trace_id, label, level, page, points_px, room_id):
        return {"trace_id": trace_id, "room_id": room_id, "room_label": label, "level_name": level, "page": page,
                "points_image_px": points_px, "calibration": {"mm_per_px": 10.0}}
    return [trace("up-stair", "Stair", "First Floor", 9, square(0, 0, 200, 300), "ff-stair"),
            trace("up-bed", "Bedroom 2", "First Floor", 9, square(200, 0, 600, 400), "ff-bed"),
            trace("gf-stair", "Stair", "Ground Floor", 8, square(1000, 1000, 1200, 1300), "gf-stair"),
            # Living: half under the bedroom above, half single-storey (roof directly over it)
            trace("gf-living", "Living", "Ground Floor", 8, square(1200, 1000, 2000, 1400), "gf-living")]


ROOMS = {"ff-stair": {"label": "Stair", "category": "circulation", "scope": "comfort_hvac"},
         "ff-bed": {"label": "Bedroom 2", "category": "house_bedroom", "scope": "comfort_hvac"},
         "gf-stair": {"label": "Stair", "category": "circulation", "scope": "comfort_hvac"},
         "gf-living": {"label": "Living", "category": "house_living", "scope": "comfort_hvac"}}


class HouseRoofOverlayTests(unittest.TestCase):
    def bases(self, first_floor_area):
        with tempfile.TemporaryDirectory() as directory:
            _write(Path(directory) / "page_inventory.json", {"pages": {
                "9": {"title": "First Floor Plan", "information": [{"kind": "room_geometry", "what": f"First floor area {first_floor_area} m²"}]},
                "12": {"title": "Roof Plan", "level": "Roof", "information": [{"kind": "room_geometry", "what": "concrete roof below; balcony below"}]}}})
            return model._house_floor_bases({"root": Path(directory)}, _traces(), ROOMS)

    def test_the_single_storey_part_of_a_lower_room_gets_the_roof_with_cited_evidence(self):
        roofs = self.bases(22)["roofs"]
        parts = {row["below"]: row for row in roofs["gf-living"]["parts"]}
        self.assertAlmostEqual(parts["open"]["fraction"], 0.5, places=3)
        self.assertAlmostEqual(parts["cooled"]["fraction"], 0.5, places=3)
        self.assertEqual(parts["cooled"]["rooms"], ["Bedroom 2"])
        self.assertIn("roof is directly over it", parts["open"]["reason"])
        self.assertIn("Stair above Stair", parts["open"]["reason"])
        self.assertIn("Roof plan p. 12", parts["open"]["reason"])
        surfaces = model._house_roof_surfaces({"label": "Living", "level_name": "Ground Floor"}, {"trace_id": "gf-living", "page": 8},
                                              32.0, roofs["gf-living"], {"page": 8}, "")
        self.assertEqual(sorted((row["physical_type"], round(row["gross_area_m2"], 1)) for row in surfaces),
                         [("ceiling", 16.0), ("roof", 16.0)])

    def test_a_poorly_traced_level_above_is_not_read_as_roof(self):
        roofs = self.bases(200)["roofs"]
        self.assertIn("cover only", roofs["gf-living"]["unmatched"])
        basis = model._house_level_roof({"levels": {0: "Ground Floor", 1: "First Floor"}, "roof_plan_pages": [12]},
                                        {"label": "Living", "level_name": "Ground Floor"}, why=roofs["gf-living"]["unmatched"])
        surfaces = model._house_roof_surfaces({"label": "Living", "level_name": "Ground Floor"}, {"trace_id": "gf-living", "page": 8},
                                              32.0, basis, {"page": 8}, "")
        self.assertEqual(surfaces[0]["physical_type"], "ceiling")
        self.assertEqual(surfaces[0]["phase1_assumptions"][0]["component"], model.HOUSE_ROOF_NOT_LINED_UP)
        self.assertNotIn("phase1_assumption", surfaces[0])

    def test_supervisor_says_when_the_roof_plan_could_not_be_lined_up(self):
        room = {"id": "r", "name": "Kitchen", "scope": "comfort_hvac", "level": "Ground Floor", "area_m2": 19, "area_source": "edited",
                "height": {}, "pages": [8], "traces": [],
                "house_roof_not_lined_up": "Under the First Floor (the house's next level up) — Kitchen was found by its area "
                                           "only (no outline), so it can't be laid over the First Floor plan. Whether part..."}
        snapshot = {"rooms": [room], "tasks": [], "height_pages": [], "storefront_pages": [], "report": {}, "model_inputs": [],
                    "dwelling": True}
        gap = next(row for row in supervisor.audit(snapshot) if row["kind"] == "roof")
        self.assertIn("the roof plan couldn't be lined up with the Ground Floor's plan", gap["message"])
        self.assertIn("found by its area only", gap["message"])


class RegisteredSheetScaleTests(unittest.TestCase):
    def test_a_sheet_with_no_printed_scale_takes_the_scale_of_a_registered_sibling_plan(self):
        contexts = {8: {"declared_mm_per_px": 14.109, "image_scale": 2.5, "declared_scale_candidates": [{"text": "1:100"}]},
                    9: {"declared_mm_per_px": None, "image_scale": 2.5}}
        with patch.object(tasks, "_p0_main_geometry_pages", return_value=[{"page": 8}, {"page": 9}]), \
             patch.object(tasks, "_p0_context", side_effect=lambda _root, page: contexts[page]), \
             patch.object(tasks, "_sheets_registered", return_value=True):
            calibration = tasks._registered_sibling_scale(Path("."), 9, contexts[9])
        self.assertEqual((calibration["source"], calibration["mm_per_px"], calibration["scale_page"]), ("registered_sheet_scale", 14.109, 8))
        self.assertIn("1:100 of plan page 8", calibration["label"])
        with patch.object(tasks, "_p0_main_geometry_pages", return_value=[{"page": 8}, {"page": 9}]), \
             patch.object(tasks, "_p0_context", side_effect=lambda _root, page: contexts[page]), \
             patch.object(tasks, "_sheets_registered", return_value=False):
            self.assertIsNone(tasks._registered_sibling_scale(Path("."), 9, contexts[9]))


if __name__ == "__main__":
    unittest.main()
