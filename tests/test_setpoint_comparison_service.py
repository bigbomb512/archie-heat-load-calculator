"""The setpoint comparison the Results page reads (backend/setpoint_comparison_service.py) and the design-day hour
profiles the compact workspace report carries (backend/ai_preliminary_service._workspace_report)."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from backend import ai_preliminary_service, setpoint_comparison_service as service


def hours(scale=1.0, fan=0.0):
    rows = []
    for hour in range(24):
        people = scale if 9 <= hour < 18 else 0.0
        envelope = -0.2 if hour < 6 else 0.5 * scale
        rows.append({"hour": hour, "components": {"people": {"total_kw": people}, "envelope": {"total_kw": envelope},
                                                  "outside_air": {"total_kw": 0.1}},
                     "design_total_kw": round(people + envelope + 0.1, 4)})
    return rows


def report(fingerprint="set-a", total=9.0):
    room_hours = hours(2.0)
    return {
        "preliminary_input_set_fingerprint": fingerprint,
        "safety_policy_fingerprint": "policy",
        "included_scope_peak": {"scenario_id": "feb", "hour": 17, "display_hour": 17, "final_design_total_kw": total,
                                "design_total_kw": total, "sensible_kw": 8.0, "latent_kw": 1.0, "month": "February"},
        "room_identities": {"r1": "plan-living"},
        "scenario_results": [
            {"scenario_id": "feb", "representative_month": "February", "included_scope_hours": hours(4.0),
             "rooms": [{"room_id": "r1", "name": "Living", "hours": room_hours,
                        "peak": {"design_total_kw": 2.6, "hour": 16, "display_hour": 16}},
                       {"room_id": "r2", "name": "Bed 1", "hours": room_hours[:12],
                        "peak": {"design_total_kw": 1.1, "hour": 15, "display_hour": 15}}]},
            {"scenario_id": "jan", "representative_month": "January", "included_scope_hours": hours(3.0),
             "rooms": [{"room_id": "r1", "name": "Living", "peak": {"design_total_kw": 2.9, "hour": 15}},
                       {"room_id": "r2", "name": "Bed 1", "peak": {"design_total_kw": 1.0, "hour": 15}}]}],
        "seasonal": {"months": [{"scenario_id": "feb", "hourly_kw": [round(row["design_total_kw"] + 0.25, 4)
                                                                     for row in hours(4.0)]}]},
    }


def comparison(baseline):
    def variant(setpoint, total, month, living):
        return {"setpoint_c": setpoint, "wet_bulb_c": 17.0, "relative_humidity": 0.5, "status": "calculated",
                "peak": {"final_design_total_kw": total, "sensible_kw": total - 1, "latent_kw": 1.0, "month": month,
                         "hour": 17, "scenario_id": "feb"},
                "scenario_results": [{"scenario_id": "feb", "representative_month": month,
                                      "rooms": [{"room_id": "r1", "name": "Living",
                                                 "peak": {"design_total_kw": living, "display_hour": 16}}]}]}
    return {"status": "calculated", "pressure_kpa": 101.3, "relative_humidity": 0.5,
            "variants": [{**variant(24.0, 9.0, "February", 2.9), "status": "baseline", "peak": baseline["included_scope_peak"],
                          "scenario_results": baseline["scenario_results"]},
                         variant(22.5, 9.45, "January", 3.0),
                         {"setpoint_c": 22.0, "status": "not_calculated", "reason": "No wet bulb for that state."}]}


class CompactTests(unittest.TestCase):
    def test_rows_changes_and_room_peaks(self):
        base = report()
        result = service.compact(comparison(base), base)
        self.assertEqual(result["status"], "calculated")
        rows = result["variants"]
        self.assertEqual([row["setpoint_c"] for row in rows], [24.0, 22.5, 22.0])
        self.assertTrue(rows[0]["baseline"])
        self.assertEqual(rows[0]["delta_kw"], 0.0)
        self.assertAlmostEqual(rows[1]["delta_kw"], 0.45)
        self.assertAlmostEqual(rows[1]["delta_pct"], 5.0)
        self.assertEqual((rows[1]["month"], rows[1]["hour"]), ("January", 17))
        # A setpoint that wasn't calculated keeps its reason and no number (never a zero).
        self.assertEqual(rows[2]["status"], "not_calculated")
        self.assertNotIn("total_kw", rows[2])
        self.assertIn("wet bulb", rows[2]["reason"])
        living = next(row for row in result["rooms"] if row["room_id"] == "r1")
        self.assertEqual(living["source_room_id"], "plan-living")
        # The room's own peak over the design days: January's 2.9 kW at the baseline.
        self.assertEqual(living["peaks"]["24.0"], {"kw": 2.9, "hour": 15, "month": "January"})
        self.assertEqual(living["peaks"]["22.5"]["kw"], 3.0)

    def test_not_calculated_passes_reason(self):
        result = service.compact({"status": "not_calculated", "reason": "Rooms use different indoor cooling conditions."}, {})
        self.assertEqual(result, {"status": "not_calculated", "reason": "Rooms use different indoor cooling conditions."})


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = {"id": "house", "review_dir": str(self.root)}
        self.paths = ai_preliminary_service._paths(self.project)

    def tearDown(self):
        self.temp.cleanup()

    def save(self, base, input_set, *, snapshot=True, current=True):
        self.paths["report"].write_text(json.dumps(base))
        if snapshot:
            self.paths["sets"].mkdir(exist_ok=True)
            (self.paths["sets"] / f"{input_set['input_fingerprint']}.json").write_text(json.dumps(input_set))
        if current:
            self.paths["current_set"].write_text(json.dumps(input_set))

    def test_calculated_once_then_read_from_the_saved_comparison(self):
        base = report()
        self.save(base, {"input_fingerprint": "set-a"})
        with mock.patch.object(service.setpoint_comparison, "compare", return_value=comparison(base)) as compare:
            first = service.get(self.project)
            second = service.get(self.project)
        self.assertEqual(compare.call_count, 1)
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "calculated")
        self.assertTrue((self.root / service.CACHE_NAME).exists())
        # The variants come from the input set the result was calculated from.
        self.assertEqual(compare.call_args.args[0]["input_fingerprint"], "set-a")

    def test_a_new_result_is_compared_again(self):
        base = report()
        self.save(base, {"input_fingerprint": "set-a"})
        with mock.patch.object(service.setpoint_comparison, "compare", return_value=comparison(base)) as compare:
            service.get(self.project)
            newer = report(total=9.5)
            self.paths["report"].write_text(json.dumps(newer, indent=1))
            service.get(self.project)
        self.assertEqual(compare.call_count, 2)

    def test_newer_inputs_are_never_compared_with_an_older_result(self):
        """The current input set has moved on and the result's own inputs weren't kept: no comparison, a reason."""
        base = report(fingerprint="set-old")
        self.save(base, {"input_fingerprint": "set-new"}, snapshot=False)
        with mock.patch.object(service.setpoint_comparison, "compare") as compare:
            result = service.get(self.project)
        compare.assert_not_called()
        self.assertEqual(result["status"], "not_calculated")
        self.assertIn("no longer saved", result["reason"])

    def test_old_result_compared_from_its_own_snapshot(self):
        base = report(fingerprint="set-old")
        self.save(base, {"input_fingerprint": "set-old"}, current=False)
        self.paths["current_set"].write_text(json.dumps({"input_fingerprint": "set-new"}))
        with mock.patch.object(service.setpoint_comparison, "compare", return_value=comparison(base)) as compare:
            result = service.get(self.project)
        self.assertEqual(compare.call_args.args[0]["input_fingerprint"], "set-old")
        self.assertEqual(result["status"], "calculated")

    def test_no_result_yet(self):
        result = service.get(self.project)
        self.assertEqual(result["status"], "not_calculated")
        self.assertIn("No final cooling load", result["reason"])
        self.assertEqual(service.get({"id": "new"})["status"], "not_calculated")


class WorkspaceProfileTests(unittest.TestCase):
    def test_design_day_profile_reaches_the_saved_totals(self):
        compact = ai_preliminary_service._workspace_report(report())
        day = compact["design_day_profile"]
        self.assertEqual(day["month"], "February")
        self.assertEqual((day["peak_hour"], day["peak_kw"]), (17, 9.0))
        self.assertEqual(len(day["total_kw"]), 24)
        # Fan heat is the month's saved hour total less its parts.
        self.assertAlmostEqual(day["groups"][12]["fan"], 0.25)
        self.assertAlmostEqual(sum(day["groups"][12].values()), day["total_kw"][12])
        # A cool hour's heat leaving through the fabric stays negative.
        self.assertLess(day["groups"][2]["conduction"], 0)
        self.assertEqual({row["key"] for row in day["group_labels"]} >= {"air", "people", "conduction", "fan"}, True)

    def test_room_profiles_and_own_peak(self):
        peaks = {row["room_id"]: row for row in ai_preliminary_service._workspace_report(report())["room_peaks"]}
        self.assertEqual(len(peaks["r1"]["hourly"]["total_kw"]), 24)
        self.assertEqual(peaks["r1"]["hour"], 16)
        self.assertEqual(peaks["r1"]["own_peak"]["month"], "January")
        self.assertEqual(peaks["r1"]["own_peak"]["kw"], 2.9)
        # 12 saved hours are not a day: no profile rather than a padded one.
        self.assertNotIn("hourly", peaks["r2"])

    def test_no_profile_without_a_governing_day(self):
        base = report()
        base["included_scope_peak"]["scenario_id"] = "missing"
        self.assertEqual(ai_preliminary_service._workspace_report(base)["design_day_profile"], {})


if __name__ == "__main__":
    unittest.main()
