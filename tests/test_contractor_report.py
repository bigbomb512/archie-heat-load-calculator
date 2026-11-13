"""The contractor's report (backend/contractor_report.py): unit sizes, the design day and the load by group, the values
and assumptions it lists, the page itself and its PDF."""

from pathlib import Path
import tempfile
import unittest

from backend import contractor_report as cr, productization


def hours(people=1.0, glass=0.0):
    return [{"hour": h, "components": {"people": {"total_kw": people if 9 <= h < 18 else 0.0},
                                       "glazing_solar": {"total_kw": glass * max(0, 1 - abs(h - 15) / 5)},
                                       "outside_air": {"total_kw": 0.5 if 9 <= h < 18 else 0.0}}} for h in range(24)]


def room(name, peak_kw, sensible_kw, hour=15, status="calculated"):
    return {"name": name, "status": status, "peak": {"design_total_kw": peak_kw, "sensible_kw": sensible_kw, "hour": hour,
                                                    "display_hour": hour}}


REPORT = {
    "fan_heat": {"percent_of_room_sensible": 3.2},
    "included_scope_peak": {"scenario_id": "jan", "hour": 15, "display_hour": 15, "final_design_total_kw": 9.0,
                            "components": {"people": {"total_kw": 4.0}, "glazing_solar": {"total_kw": 3.0},
                                           "solar": {"total_kw": 0.5}, "outside_air": {"total_kw": 1.2},
                                           "fan_heat": {"total_kw": 0.3}}},
    "scenario_results": [
        {"scenario_id": "jan", "representative_month": "January", "included_scope_hours": hours(4.0, 3.0),
         "rooms": [room("Living", 6.0, 5.0), room("Bed 1", 2.2, 2.0, 17), room("Hall", 0.4, 0.4), room("Old", 9.9, 9.0, status="blocked")]},
        {"scenario_id": "mar", "representative_month": "March", "included_scope_hours": hours(),
         "rooms": [room("Living", 5.0, 4.0), room("Bed 1", 2.6, 2.5, 16), room("Hall", 0.3, 0.3)]}],
    "seasonal": {"months": [{"scenario_id": "jan", "hourly_kw": [round(sum(c["total_kw"] for c in row["components"].values()) + 0.2, 3)
                                                                 for row in hours(4.0, 3.0)]}]},
}
PLAN_ROOMS = [{"name": "Living", "level": "Ground", "area_m2": 30, "final_kw": 6.0},
              {"name": "Bed 1", "level": "First", "area_m2": 14, "final_kw": 2.2},
              {"name": "Hall", "level": "Ground", "area_m2": 8, "final_kw": 0.4}]


class SizingTests(unittest.TestCase):
    def test_the_smallest_size_at_or_above_the_load(self):
        steps = cr.sizes()["wall_split_kw"]
        self.assertEqual(cr.pick(3.5, steps), (1, 3.5))           # exactly a size: that size, no margin
        self.assertEqual(cr.pick(3.51, steps), (1, 4.6))
        self.assertEqual(cr.pick(0.2, steps), (1, 2.0))
        self.assertEqual(cr.pick(16.4, steps), (2, 8.5))         # above the largest: fewest equal units
        self.assertEqual(cr.pick(30.4, cr.sizes()["ducted_kw"]), (2, 16.0))

    def test_each_room_is_sized_at_its_own_worst_hour_of_any_month_with_fan_heat(self):
        rows = {row["name"]: row for row in cr.room_sizing(REPORT, PLAN_ROOMS)}
        self.assertNotIn("Old", rows)                                       # blocked rooms aren't sized
        self.assertEqual((rows["Bed 1"]["month"], rows["Bed 1"]["hour"]), ("March", 16))   # its worst is March, not the job's January
        self.assertAlmostEqual(rows["Bed 1"]["load_kw"], round(2.6 + 2.5 * 0.032, 2))
        self.assertEqual(rows["Bed 1"]["suggested"], "3.5 kW")           # 2.68 kW: above 2.5, so 3.5
        self.assertEqual(rows["Living"]["suggested"], "7.1 kW")
        self.assertIn("Small load", rows["Hall"]["note"])
        self.assertEqual([row["name"] for row in cr.room_sizing(REPORT, PLAN_ROOMS)], ["Living", "Bed 1", "Hall"])

    def test_a_room_too_big_for_one_wall_split_is_offered_a_ducted_unit(self):
        report = {**REPORT, "scenario_results": [{**REPORT["scenario_results"][0], "rooms": [room("Shop", 30.0, 24.0)]}]}
        row = cr.room_sizing(report, [{"name": "Shop", "final_kw": 30.0}])[0]
        self.assertEqual(row["suggested"], "4 × 8.5 kW")
        self.assertEqual(row["ducted_alternative"], "2 × 16 kW")
        self.assertIn("2 × 16 kW ducted is the usual choice", row["note"])

    def test_the_whole_building_option_uses_the_coincident_peak(self):
        self.assertEqual(cr.ducted(9.0)["suggested"], "10 kW")
        self.assertEqual(cr.ducted(9.0)["note"], "")
        big = cr.ducted(49.4)
        self.assertEqual(big["suggested"], "3 × 18 kW")
        self.assertIn("commercial packaged unit", big["note"])
        self.assertIn("one per floor", cr.ducted(41.5, dwelling=True)["note"])


class ChartDataTests(unittest.TestCase):
    def test_the_design_day_groups_add_up_to_each_hours_total_fan_heat_included(self):
        day = cr.design_day(REPORT)
        self.assertEqual(day["month"], "January")
        for hour, groups in enumerate(day["hours"]):
            self.assertAlmostEqual(sum(groups.values()), REPORT["seasonal"]["months"][0]["hourly_kw"][hour], places=2)
            self.assertAlmostEqual(groups["fan"], 0.2, places=3)
        self.assertEqual(cr.design_day({"included_scope_peak": {"scenario_id": "x"}}), {})

    def test_the_breakdown_groups_components_and_shares_add_to_one(self):
        rows = {row["key"]: row for row in cr.breakdown(REPORT["included_scope_peak"])}
        self.assertAlmostEqual(rows["sun"]["kw"], 3.5)              # glass and wall/roof sun together
        self.assertAlmostEqual(sum(row["share"] for row in rows.values()), 1.0)
        self.assertNotIn("conduction", rows)                         # nothing there: not drawn

    def test_the_axis_tops_just_above_the_largest_value(self):
        self.assertEqual(cr._nice_top(55.3), (60, 10))
        self.assertEqual(cr._nice_top(46.5), (50, 10))
        self.assertEqual(cr._nice_top(2.9), (3, 0.5))
        self.assertEqual(cr._nice_top(0.0), (0.5, 0.5))

    def test_the_charts_are_inline_svg_with_a_title_on_every_mark(self):
        svg = cr.design_day_svg(cr.design_day(REPORT))
        self.assertTrue(svg.startswith("<svg") and "<title>15:00 People" in svg)
        self.assertIn("at 15:00", svg)
        months = [{"month": "January", "kw": 9.0, "hour": 15}, {"month": "March", "kw": 6.0, "hour": 16}]
        self.assertIn("<title>March: 6.0 kW at 16:00</title>", cr.months_svg(months, "January"))
        self.assertEqual(cr.design_day_svg({}), "")


class ValuesAndAssumptionsTests(unittest.TestCase):
    def test_internal_source_notes_read_plainly(self):
        self.assertEqual(cr._source_head("Generic value TV-0054: https://example"), "Generic value TV-0054")
        self.assertEqual(cr._source_head("Generic value: Rating 100 W (generic placeholder (AIRAH DA09 pending)) × 1"),
                         "Generic value: Rating 100 W")
        self.assertIn("no published source", cr._source_head("AI preliminary assumption pack au-preliminary-v3: retail"))
        self.assertIn("allowance", cr._source_head("controlled_preliminary_profile"))

    def test_surface_notes_are_grouped_by_rule_and_stored_heat_is_one_line(self):
        record = {"status": "calculated", "floor_covering": "carpet",
                  "constructions": [{"label": "lightweight framed wall", "chosen": "default"}]}
        input_set = {"material": {"hourly_load_model": {"rooms": [
            {"room_id": "a", "name": "Bed 1", "stored_heat": record}, {"room_id": "b", "name": "Bed 2", "stored_heat": record}]}},
            "phase1_assumptions": [
                {"room_id": "a", "component": "Bed 1 floor over Hall", "reason": "Over Hall, air-conditioned to the same temperature: no heat flows. Matched to the Ground Floor plan by Stairs."},
                {"room_id": "b", "component": "Bed 2 floor over Lounge", "reason": "Over Lounge, air-conditioned to the same temperature: no heat flows."},
                {"room_id": "a", "component": "Bed 1 roof", "reason": "First Floor is the house's top level: the roof is over it."},
                {"room_id": "a", "component": "stored heat", "reason": "per-room text"},
                {"room_id": "b", "component": "surroundings", "reason": "Full sun."}]}
        rows = cr.assumed(input_set)
        self.assertEqual(rows[0]["component"], "stored heat")
        self.assertIn("lightweight framed wall", rows[0]["reason"])
        self.assertIn("carpet in 2 rooms", rows[0]["reason"])
        floors = next(row for row in rows if row["component"] == "Floors")
        self.assertEqual(floors["rooms"], ["Bed 1", "Bed 2"])
        self.assertEqual(floors["reason"], "next to an air-conditioned room at the same temperature: no heat flows.")
        self.assertEqual(len(rows), 4)

    def test_sources_drop_archies_own_bookkeeping(self):
        texts = " ".join(row["text"] for row in cr.sources({}, dwelling=True))
        self.assertNotIn("unchanged", texts)
        self.assertIn("CSIRO", texts)                    # a house lists its leakage source
        self.assertIn("Daikin", texts)


class PageTests(unittest.TestCase):
    def details(self):
        return {"name": "Test job", "address": "1 Street", "status": "draft", "generated_at": "now", "total_kw": 9.0,
                "sensible_kw": 7.5, "latent_kw": 1.5, "peak_month": "January", "peak_hour": 15, "rooms_counted": 3,
                "household_note": "", "design_basis": "Sydney", "components": [], "rooms": [], "not_cooled": [], "months": [],
                "assumptions": [("Uses typical lighting.", 2)], "exclusions": ["Duct heat: not calculated."], "stale_reasons": [],
                "room_sizing": cr.room_sizing(REPORT, PLAN_ROOMS), "ducted": cr.ducted(9.0), "design_day": cr.design_day(REPORT),
                "breakdown": cr.breakdown(REPORT["included_scope_peak"]), "room_values": [], "equipment": [], "envelope": [],
                "assumed": [], "sources": cr.sources({}, False)}

    def test_the_page_carries_the_label_sizes_charts_and_sources(self):
        page = cr.render_html(self.details())
        for text in ("not an engineer&#x27;s design", "Units to consider", "7.1 kW", "10 kW", "<svg", "Sensible heat ratio",
                     "0.83", "Still to confirm", "Uses typical lighting.", "Duct heat", "Daikin", "AS/NZS 3823"):
            self.assertIn(text, page)
        self.assertNotIn("<script", page)         # prints and opens offline

    @unittest.skipUnless(productization._chrome_binary(), "no local Chrome or Chromium")
    def test_a_local_chrome_prints_it_as_a_real_pdf(self):
        with tempfile.TemporaryDirectory() as folder:
            html_path, pdf_path = Path(folder) / "report.html", Path(folder) / "report.pdf"
            html_path.write_text(cr.render_html(self.details()), encoding="utf-8")
            self.assertEqual(productization._render_browser_pdf(html_path, pdf_path), "chrome-headless")
            data = pdf_path.read_bytes()
            self.assertTrue(data.startswith(b"%PDF-"))
            self.assertGreater(len(data), 20_000)     # a laid-out page with charts, not the one-page text fallback


if __name__ == "__main__":
    unittest.main()
