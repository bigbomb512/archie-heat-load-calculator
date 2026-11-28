"""Self-checks keep a plainly short result "Preliminary" (Build queue task 9, 2026-11-28)."""

import unittest
from pathlib import Path
from unittest.mock import patch

from backend import result_label_service, result_self_checks as checks


def hour(kw):
    return {"components": {"walls": {"total_kw": kw}}}


def job(total_kw=10.0, rooms=(("living", 60, 5.0), ("bed", 40, 5.0)), left_out=(), scheduled=10, placed=9):
    full = {"included_scope_peak": {"final_design_total_kw": total_kw},
            "scenario_results": [{"rooms": [{"room_id": room_id, "hours": [hour(kw)]} for room_id, _area, kw in rooms]}]}
    model = {"topology": {"rooms": [{"room_id": room_id, "area_m2": area} for room_id, area, _kw in rooms]}}
    base = {"phase1_coverage": {"rooms_left_out": [{"label": name, "reason": "no area yet"} for name in left_out]},
            "glazing_coverage": {"scheduled": scheduled, "placed": placed}}
    return base, full, model


class ChecksTests(unittest.TestCase):
    def test_a_sound_house_passes(self):
        self.assertEqual(checks.checks(*job(), dwelling=True), [])       # 10 kW over 100 m² = 100 W/m²

    def test_rooms_not_counted_are_named(self):
        reasons = checks.checks(*job(left_out=("Bedroom", "Family")), dwelling=True)
        self.assertEqual(len(reasons), 1)
        self.assertIn("2 rooms on the room list aren't counted (Bedroom, Family)", reasons[0])

    def test_too_few_windows_placed(self):
        # The PDF-only set 16 run: 15 of 28 scheduled windows on a wall.
        reasons = checks.checks(*job(scheduled=28, placed=15), dwelling=True)
        self.assertEqual(len(reasons), 1)
        self.assertIn("Only 15 of the 28 windows", reasons[0])

    def test_load_per_m2_outside_the_checked_houses(self):
        reasons = checks.checks(*job(total_kw=3.0, rooms=(("living", 100, 3.0),)), dwelling=True)   # 30 W/m²
        self.assertEqual(len(reasons), 1)
        self.assertIn("30 W per m²", reasons[0])

    def test_a_room_with_no_load_is_not_cooled_floor(self):
        # The set 17 reference keeps a 986 m² driveway on its room list with no load: still 100 W/m².
        base, full, model = job(rooms=(("living", 100, 10.0), ("drive", 986, 0.0)))
        self.assertEqual(checks.cooled_area_m2(full, model), 100)
        self.assertEqual(checks.checks(base, full, model, dwelling=True), [])

    def test_house_limits_are_not_applied_to_a_shop(self):
        self.assertEqual(checks.checks(*job(total_kw=30.0, scheduled=28, placed=2), dwelling=False), [])
        self.assertEqual(len(checks.checks(*job(left_out=("Store",)), dwelling=False)), 1)


class LabelTests(unittest.TestCase):
    def call(self, base, full, model, dwelling=True, open_rows=()):
        files = {"report": full, "base_report": base, "model": model}
        paths = {name: Path(f"/unused/{name}.json") for name in files} | {"root": Path("/unused")}
        with patch.object(result_label_service.assumption_range_service.ai_preliminary_service, "_paths", return_value=paths), \
             patch.object(result_label_service.assumption_range_service, "_read_big", side_effect=lambda path: files[Path(path).stem]), \
             patch.object(result_label_service.assumption_range_service, "open_unknowns", return_value=list(open_rows)), \
             patch.object(result_label_service.assumption_range_service, "get", return_value={"status": "nothing_open"}), \
             patch("backend.house_windows_service.is_dwelling", return_value=dwelling):
            return result_label_service.label({"review_dir": "/unused"}, status={"results_ready": True})

    def test_a_failed_self_check_keeps_the_result_preliminary(self):
        result = self.call(*job(scheduled=28, placed=15))
        self.assertEqual(result["status"], "preliminary")
        self.assertEqual(result["text"], "Preliminary (1 self-check to look at)")
        self.assertIn("Only 15 of the 28 windows", result["reason"])
        self.assertEqual(len(result["self_checks"]), 1)

    def test_load_per_m2_outside_the_band_warns_but_leaves_design(self):
        # The band comes from only three checked houses, so it is shown but doesn't hold a design at Preliminary.
        result = self.call(*job(total_kw=3.0, rooms=(("living", 100, 3.0),)))
        self.assertEqual((result["status"], result["self_checks"]), ("design", []))
        self.assertEqual(len(result["self_check_warnings"]), 1)
        self.assertIn("30 W per m²", result["self_check_warnings"][0])

    def test_passing_self_checks_leave_design(self):
        result = self.call(*job())
        self.assertEqual((result["status"], result["self_checks"]), ("design", []))
        self.assertEqual(result["self_check_warnings"], [])


if __name__ == "__main__":
    unittest.main()
