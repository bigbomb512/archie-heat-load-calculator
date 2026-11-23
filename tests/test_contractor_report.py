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
    def test_ahu_results_show_each_system_at_its_own_governing_hour(self):
        inputs = {"input_fingerprint": "snapshot-1", "material": {"hourly_load_model": {"rooms": [
            {"room_id": "room-1", "name": "Living"}, {"room_id": "room-2", "name": "Office"}]}}}
        room_report = {"preliminary_input_set_fingerprint": "snapshot-1"}
        def system(ident, room, hour, coil):
            return {"ahu_id": ident, "name": ident, "system_type": "constant_volume", "number_off": 1,
                    "served_zone_ids": ["zone-" + ident], "room_ids": [room],
                    "peak": {"hour": hour, "design_total_kw": coil + 0.4},
                    "hours": [{"hour": hour, "coil_total_kw": coil, "coil_sensible_kw": coil - 0.5,
                               "coil_latent_kw": 0.5, "fan_heat_kw": 0.3, "duct_effect_kw": 0.1,
                               "flows_lps": {"supply": 250, "outside_air": 20},
                               "mixed_air_state": {"dry_bulb_c": 23.5, "wet_bulb_c": 16.5},
                               "coil_leaving_state": {"dry_bulb_c": 12, "wet_bulb_c": 11.5}}]}
        ahu_report = {"status": "draft", "calculation_mode": "ai_preliminary",
                      "input_snapshot_fingerprint": "snapshot-1",
                      "input_fingerprints": {"calculator_input_snapshot": "snapshot-1", "ahu_resolution": "ahu-1"},
                      "scenario_results": [{"scenario_id": "summer", "ahus": [
                          system("AHU-1", "room-1", 15, 4.0), system("AHU-2", "room-2", 12, 2.0)]}]}
        rows = cr.ahu_results(ahu_report, room_report, inputs,
                              [{"name": "Living", "display_name": "Family"}], {"fingerprint": "ahu-1"})
        self.assertEqual(rows["status"], "draft")
        self.assertEqual([(row["name"], row["hour"], row["coil_total_kw"]) for row in rows["systems"]],
                         [("AHU-1", 15, 4.0), ("AHU-2", 12, 2.0)])
        self.assertEqual(rows["systems"][0]["rooms"], ["Family"])
        html = cr._ahu_html(rows)
        for text in ("Air systems and coil duty", "4.0 kW", "250.0 L/s", "Mixed air", "fan 0.3 kW", "Family"):
            self.assertIn(text, html)
        self.assertNotIn("4.4 kW</strong>", html)  # system total is not relabelled coil duty

    def test_ahu_missing_blocked_or_stale_never_shows_a_coil_value(self):
        inputs = {"input_fingerprint": "current"}
        room_report = {"preliminary_input_set_fingerprint": "current"}
        base = {"status": "draft", "calculation_mode": "ai_preliminary",
                "input_snapshot_fingerprint": "current",
                "input_fingerprints": {"calculator_input_snapshot": "current", "ahu_resolution": "old"},
                "scenario_results": []}
        cases = [({}, "No preliminary"),
                 ({**base, "status": "blocked", "input_fingerprints": {
                     "calculator_input_snapshot": "current", "ahu_resolution": "new"},
                   "blocked_ahus": [{"reasons": ["Missing supply path."]}]}, "Missing supply path"),
                 ({**base, "input_snapshot_fingerprint": "old"}, "older room-load"),
                 (base, "AHU inputs changed")]
        for report, reason in cases:
            with self.subTest(reason=reason):
                result = cr.ahu_results(report, room_report, inputs, [], {"fingerprint": "new"})
                self.assertEqual(result["status"], "unavailable")
                self.assertIn(reason, result["reason"])
                self.assertIn("AHU duty not calculated", cr._ahu_html(result))
                self.assertNotIn(" kW</strong>", cr._ahu_html(result))

    def test_design_temperatures_come_from_governing_hour_and_saved_room_setpoints(self):
        report = {"included_scope_peak": {"scenario_id": "hot", "hour": 15},
                  "design_conditions_basis": {"design_day": {"source": "Cited weather pack", "days": [
                      {"day": "hot", "design_point": {"db": 31.1, "wb": 19.8}}]}},
                  "scenario_results": [{"scenario_id": "hot", "rooms": [{"name": "Living", "status": "calculated"}]}]}
        inputs = {"material": {"design_day_scenarios": {"scenarios": [{
            "scenario_id": "hot", "title": "Summer design day: design dry bulb with coincident wet bulb",
            "status": "provisional", "source": "Cited weather pack", "hours": [
                {"hour": 15, "outdoor_dry_bulb_c": {"value": 30.1}, "outdoor_wet_bulb_c": {"value": 19.8}}]}]},
            "hourly_load_model": {"rooms": [
                {"name": "Living", "indoor_cooling_setpoint_c": 24,
                 "cooling_load_conditions": {"indoor_cooling_wet_bulb_c": 18}},
                {"name": "Excluded", "indoor_cooling_setpoint_c": 20}]}}}
        conditions = cr.design_conditions(report, inputs)
        self.assertEqual((conditions["sizing"]["db"], conditions["summer"]["db"],
                          conditions["indoor"]["db_values"], conditions["difference_k"]),
                         (30.1, 31.1, [24.0], 7.1))      # the excluded room's 20 °C setpoint isn't counted

    def test_missing_or_mixed_conditions_are_not_filled_with_a_guess(self):
        self.assertIsNone(cr.design_conditions({}, {})["difference_k"])
        self.assertEqual(cr.design_conditions({}, {})["indoor"]["db_values"], [])
        report = {"included_scope_peak": {"scenario_id": "a", "hour": 12},
                  "design_conditions_basis": {"design_day": {"days": [{"day": "hot", "design_point": {"db": 32, "wb": 20}}]}}}
        inputs = {"material": {"design_day_scenarios": {"scenarios": [{"scenario_id": "a", "hours": [
            {"hour": 12, "outdoor_dry_bulb_c": 32}]}]},
            "hourly_load_model": {"rooms": [{"indoor_cooling_setpoint_c": 22}, {"indoor_cooling_setpoint_c": 24}]}}}
        conditions = cr.design_conditions(report, inputs)
        self.assertEqual(conditions["indoor"]["db_values"], [22.0, 24.0])
        self.assertIsNone(conditions["indoor"]["db"])
        self.assertIsNone(conditions["difference_k"])
        page = cr.design_conditions_html(conditions)
        self.assertIn("22.0–24.0 °C", page)
        self.assertIn("differs by room", page)

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


def _scenario(scenario_id, month, top):
    return {"scenario_id": scenario_id, "representative_month": month, "atmospheric_pressure_kpa": {"value": 101.325},
            "hours": [{"hour": h, "outdoor_dry_bulb_c": {"value": top - abs(h - 15) * 0.5}, "outdoor_wet_bulb_c": {"value": 19.0}}
                      for h in range(24)]}


CONDITIONS_REPORT = {
    "included_scope_peak": {"scenario_id": "feb", "month": "February", "hour": 16, "display_hour": 16},
    "design_conditions_basis": {"design_day": {
        "location": "Sydney", "state": "NSW", "source": "AIRAH Technical Handbook, 4th edition (2007), p. 37",
        "far_from_site": False, "days": [{"day": "hot", "design_point": {"db": 31.1, "wb": 19.8}},
                                         {"day": "humid", "design_point": {"db": 29.5, "wb": 22.7}}]}}}
CONDITIONS_INPUT = {"material": {
    "design_day_scenarios": {"scenarios": [_scenario("jan", "January", 31.1), _scenario("feb", "February", 30.57)]},
    "hourly_load_model": {"rooms": [{"indoor_cooling_setpoint_c": 24.0, "cooling_load_conditions": {"indoor_cooling_wet_bulb_c": 18.0}},
                                    {"indoor_cooling_setpoint_c": 24.0, "cooling_load_conditions": {"indoor_cooling_wet_bulb_c": 18.0}}]}}}


class DesignConditionsTests(unittest.TestCase):
    """A contractor checks the design temperatures first (a mechanical contractor's review, 2026-11-15)."""

    def test_the_summer_point_the_sizing_hour_indoor_and_the_difference(self):
        c = cr.design_conditions(CONDITIONS_REPORT, CONDITIONS_INPUT)
        self.assertEqual((c["summer"]["db"], c["summer"]["wb"]), (31.1, 19.8))
        self.assertEqual(c["location"], "Sydney, NSW")
        self.assertEqual((c["sizing"]["month"], c["sizing"]["hour"]), ("February", 16))
        self.assertAlmostEqual(c["sizing"]["db"], 30.07)                 # the February day's 16:00, not January's
        self.assertAlmostEqual(c["sizing"]["day_max_db"], 30.57)
        self.assertEqual((c["indoor"]["db"], c["indoor"]["wb"]), (24.0, 18.0))
        self.assertTrue(54 <= c["indoor"]["rh_percent"] <= 58)           # 24 °C DB / 18 °C WB at sea level is about 56 %
        self.assertEqual(c["difference_k"], 7.1)
        text = cr.design_conditions_text(c)
        for part in ("31.1 °C DB / 19.8 °C WB", "Sydney, NSW", "February 16:00", "24.0 °C DB / 18.0 °C WB", "7.1 K"):
            self.assertIn(part, text)

    def test_the_page_shows_them_under_the_total(self):
        details = PageTests().details()
        details["design_conditions"] = cr.design_conditions(CONDITIONS_REPORT, CONDITIONS_INPUT)
        page = cr.render_html(details)
        self.assertLess(page.index("Design conditions"), page.index("Units to consider"))
        for text in ("31.1 °C", "19.8 °C wet bulb", "Outdoor at the sizing hour", "30.1 °C", "19.0 °C wet bulb", "0.5 K below the summer design day",
                     "relative humidity", "7.1 K", "29.5 °C DB / 22.7 °C WB", "cooling only"):
            self.assertIn(text, page)

    def test_missing_conditions_are_said_plainly_not_left_blank(self):
        self.assertIn("weren't recorded", cr.design_conditions_html({}))
        self.assertEqual(cr.design_conditions_text({}), "")
        far = cr.design_conditions({**CONDITIONS_REPORT, "design_conditions_basis": {"design_day": {
            **CONDITIONS_REPORT["design_conditions_basis"]["design_day"], "far_from_site": True, "distance_km": 140}}}, CONDITIONS_INPUT)
        self.assertIn("140 km from the site", cr.design_conditions_html(far))


class PageTests(unittest.TestCase):
    def test_preliminary_label_is_on_cover_and_summary(self):
        details = self.details()
        details["result_label"] = {"status": "preliminary", "text": "Preliminary (2 answers that could change the unit still open)",
                                   "reason": "The range is not settled."}
        page = cr.render_html(details)
        self.assertIn("<title>Preliminary (2 answers that could change the unit still open) cooling load", page)
        self.assertIn("data-result-label", page)
        self.assertIn("The range is not settled.", page)
        self.assertIn("Preliminary (2 answers that could change the unit still open) cooling load for the whole building", page)

    def test_design_label_is_on_cover_and_summary(self):
        details = self.details()
        details["result_label"] = {"status": "design", "text": "Design", "reason": "No unit-changing inputs remain open."}
        page = cr.render_html(details)
        self.assertIn("<h1>Design cooling load", page)
        self.assertIn("Design cooling load for the whole building", page)

    def details(self):
        return {"name": "Test job", "address": "1 Street", "status": "draft", "generated_at": "now", "total_kw": 9.0,
                "sensible_kw": 7.5, "latent_kw": 1.5, "peak_month": "January", "peak_hour": 15, "rooms_counted": 3,
                "household_note": "", "design_basis": "Sydney", "components": [], "rooms": [], "not_cooled": [], "months": [],
                "assumptions": [("Uses typical lighting.", 2)], "exclusions": ["Duct heat: not calculated."], "stale_reasons": [],
                "room_sizing": cr.room_sizing(REPORT, PLAN_ROOMS), "ducted": cr.ducted(9.0), "design_day": cr.design_day(REPORT),
                "breakdown": cr.breakdown(REPORT["included_scope_peak"]), "room_values": [], "equipment": [], "envelope": [],
                "assumed": [], "sources": cr.sources({}, False)}

    def test_current_heating_replaces_cooling_only_notice_with_load_and_rooms(self):
        details = self.details()
        details["design_conditions"] = cr.design_conditions(CONDITIONS_REPORT, CONDITIONS_INPUT)
        details["heating"] = {"status": "current", "total_kw": 3.3, "outdoor_design_c": -2.0,
                              "indoor_design_c": 20.0, "draft": True, "gate": {"approved": False},
                              "rooms": [{"name": "Living <north>", "peak_kw": 2.5,
                                         "components": {"Walls, roof and floor conduction": 1.0,
                                                        "Fresh air": 0.8}}]}
        page = cr.render_html(details)
        for expected in ("Design heating load", "3.3 kW", "-2 °C", "20 °C", "Living &lt;north&gt;",
                         "2.5", "Walls, roof and floor conduction: 1.0 kW",
                         "Draft: heating method gate not approved by an engineer"):
            self.assertIn(expected, page)
        self.assertNotIn("no winter (heating) design temperature or heating load is calculated", page)

    def test_missing_or_not_calculated_heating_keeps_notice(self):
        details = self.details()
        details["design_conditions"] = cr.design_conditions(CONDITIONS_REPORT, CONDITIONS_INPUT)
        self.assertIn("no winter (heating) design temperature or heating load is calculated", cr.render_html(details))
        details["heating"] = {"status": "not_calculated", "reason": "No heating report has been calculated for this job."}
        page = cr.render_html(details)
        self.assertIn("Heating is not calculated", page)
        self.assertIn("No heating report has been calculated for this job", page)

    def test_the_page_carries_the_label_sizes_charts_and_sources(self):
        details = self.details()
        details["design_conditions"] = cr.design_conditions(CONDITIONS_REPORT, CONDITIONS_INPUT)
        page = cr.render_html(details)
        for text in ("not an engineer&#x27;s design", "Units to consider", "7.1 kW", "10 kW", "<svg", "Sensible heat ratio",
                     "0.83", "Still to confirm", "Uses typical lighting.", "Duct heat", "Daikin", "AS/NZS 3823",
                     "Design conditions", "31.1 °C", "19.8 °C wet bulb", "30.1 °C", "7.1 K", "cooling only"):
            self.assertIn(text, page)
        self.assertNotIn("Air systems and coil duty", page)     # no AHU result passed: no section
        self.assertNotIn("<script", page)         # prints and opens offline

    def test_plain_text_pdf_fallback_keeps_the_temperatures(self):
        details = self.details()
        details["design_conditions"] = cr.design_conditions(CONDITIONS_REPORT, CONDITIONS_INPUT)
        details["ahu"] = cr.ahu_results({}, {}, {}, [])          # a shop with no AHU report: said plainly
        text = "\n".join(productization._job_report_lines(details))
        self.assertIn("Outdoor summer design 31.1 °C DB / 19.8 °C WB", text)
        self.assertIn("at the sizing hour (February 16:00) 30.1 °C DB", text)
        self.assertIn("indoor 24.0 °C DB / 18.0 °C WB", text)
        self.assertIn("Cooling only", text)
        self.assertIn("AHU duty not calculated", text)
        details["ahu"] = cr.ahu_results({}, {}, {}, [], dwelling=True)   # a house with no AHU: nothing to say
        self.assertNotIn("AHU", "\n".join(productization._job_report_lines(details)))
        details["ahu"] = {"status": "draft", "systems": [{
            "name": "AHU-1", "type": "Constant volume", "number_off": 1, "scenario": "January", "hour": 15,
            "coil_total_kw": 4.0, "coil_sensible_kw": 3.5, "coil_latent_kw": 0.5, "supply_lps": 250,
            "outside_lps": 20, "fan_kw": 0.3, "duct_kw": 0.1, "rooms": ["Family"],
            "mixed_air": {"dry_bulb_c": 23.5, "wet_bulb_c": 16.5},
            "coil_leaving": {"dry_bulb_c": 12, "wet_bulb_c": 11.5}}]}
        text = "\n".join(productization._job_report_lines(details))
        for value in ("AHU-1", "coil 4.0 kW", "Supply 250.0 L/s", "Mixed air: 23.5 °C", "Rooms: Family"):
            self.assertIn(value, text)

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
