import copy
import csv
import io
import unittest
from datetime import date, datetime, timedelta

from ai.annual_energy import (
    HOURS_PER_YEAR,
    _annual_summary,
    _annual_room,
    _aggregate_annual_section,
    annual_hourly_csv,
    annual_monthly_csv,
    _annual_heating_hour,
    calculate_annual_report,
    empty_annual_radiation,
    import_epw,
    _wet_bulb_from_dew_point,
    validate_annual_radiation,
    validate_annual_calendar,
    validate_annual_weather,
)
from ai.hourly_loads import empty_hourly_load_model, empty_schedule_library
from ai.heat_loads import humidity_ratio_from_db_wb


def citation(reference="SYN-ANNUAL"):
    return [{"reference": reference, "page": 1, "excerpt": "Synthetic development fixture"}]


def weather():
    start = datetime(2025, 1, 1)
    return {
        "weather_id": "weather_test", "location": "Test", "timezone": "Australia/Sydney",
        "source": "Synthetic annual weather", "citations": citation(),
        "records": [{
            "hour_index": index, "timestamp": (start + timedelta(hours=index)).isoformat(),
            "outdoor_dry_bulb_c": 25.0, "outdoor_wet_bulb_c": 18.0,
            "atmospheric_pressure_kpa": 101.3,
        } for index in range(HOURS_PER_YEAR)],
    }


def calendar():
    start = date(2025, 1, 1)
    holidays = [{"date": "2025-01-01", "name": "New Year"}]
    dates = []
    for index in range(365):
        current = start + timedelta(days=index)
        day_type = "sunday_holiday" if current.weekday() == 6 or current.isoformat() == "2025-01-01" else "saturday" if current.weekday() == 5 else "weekday"
        dates.append({"date": current.isoformat(), "day_type": day_type})
    return {"calendar_id": "calendar_test", "year": 2025, "timezone": "Australia/Sydney", "source": "Synthetic calendar", "citations": citation(), "holidays": holidays, "dates": dates}


class AnnualEnergyTests(unittest.TestCase):
    def test_weather_requires_exactly_8760_hours(self):
        checked = validate_annual_weather(weather())
        self.assertEqual(len(checked["records"]), HOURS_PER_YEAR)
        short = copy.deepcopy(weather())
        short["records"] = short["records"][:-1]
        with self.assertRaises(ValueError):
            validate_annual_weather(short)

    def test_weather_requires_explicit_pressure_and_timestamp(self):
        missing_pressure = copy.deepcopy(weather())
        missing_pressure["records"][0].pop("atmospheric_pressure_kpa")
        with self.assertRaises(ValueError):
            validate_annual_weather(missing_pressure)
        missing_timestamp = copy.deepcopy(weather())
        missing_timestamp["records"][0].pop("timestamp")
        with self.assertRaises(ValueError):
            validate_annual_weather(missing_timestamp)

    def test_weather_rejects_missing_repeated_and_daylight_saving_hours(self):
        gap = copy.deepcopy(weather())
        gap["records"][100]["timestamp"] = (datetime(2025, 1, 1) + timedelta(hours=101)).isoformat()
        with self.assertRaisesRegex(ValueError, "exactly one local hour"):
            validate_annual_weather(gap)

        repeat = copy.deepcopy(weather())
        repeat["records"][100]["timestamp"] = repeat["records"][99]["timestamp"]
        with self.assertRaisesRegex(ValueError, "exactly one local hour"):
            validate_annual_weather(repeat)

        dst_transition = copy.deepcopy(weather())
        dst_transition["records"][100]["timestamp"] = "2025-01-05T04:00:00+11:00"
        with self.assertRaisesRegex(ValueError, "fixed time offset"):
            validate_annual_weather(dst_transition)

    def test_dew_point_is_converted_to_wet_bulb(self):
        wet_bulb = _wet_bulb_from_dew_point(25.0, 15.0, 101.3)
        self.assertLess(wet_bulb, 25.0)
        self.assertGreater(wet_bulb, 15.0)

    def test_annual_heating_uses_thermodynamic_basis_for_dewpoint_derived_wet_bulb(self):
        room = {
            "indoor_heating_setpoint_c": 20.0,
            "heating_internal_gain_status": "not_applicable",
            "heating_safety_factor": 1.0,
            "cooling_load": {
                "outside_air_lps": 100.0,
                "outside_air_flow_reference_basis": "outdoor_design_condition",
                "envelope_surfaces": [],
                "glazing_surfaces": [],
            },
            "cooling_load_conditions": {
                "indoor_cooling_wet_bulb_c": 18.0,
                "indoor_wet_bulb_basis": "thermodynamic",
            },
        }
        weather_row = {
            "outdoor_dry_bulb_c": 30.0,
            "outdoor_wet_bulb_c": None,
            "outdoor_dew_point_c": 20.0,
            "atmospheric_pressure_kpa": 101.3,
        }
        result, _ = _annual_heating_hour(
            room, {}, {"outside_air": [1.0] * 24}, 0, weather_row, {}, {}, {}
        )
        self.assertEqual(result["safety_factor"], 1.0)
        component = result["components"]["heating_outside_air"]
        self.assertEqual(component["inputs"]["outdoor_wet_bulb_basis"], "thermodynamic")
        self.assertEqual(component["inputs"]["outdoor_wet_bulb_method"], "thermodynamic_ashrae_eq33_iapws_water_ice_v3")
        expected_ratio = humidity_ratio_from_db_wb(
            weather_row["outdoor_dry_bulb_c"], component["inputs"]["outdoor_wb_c"],
            weather_row["atmospheric_pressure_kpa"], "thermodynamic",
        )
        self.assertAlmostEqual(component["inputs"]["flow_reference_state"]["humidity_ratio"], expected_ratio, places=9)

    def test_annual_heating_blocks_missing_safety_factor_instead_of_defaulting_to_one(self):
        room = {"indoor_heating_setpoint_c": 20.0, "cooling_load": {"envelope_surfaces": [], "glazing_surfaces": []}}
        row, issues = _annual_heating_hour(
            room, {}, {}, 0,
            {"outdoor_dry_bulb_c": 0, "outdoor_wet_bulb_c": -2, "atmospheric_pressure_kpa": 101.325},
            {}, {}, {},
        )
        self.assertEqual(row["design_total_kw"], 0.0)
        self.assertIn("cited heating safety factor is missing", issues)

    def test_annual_room_reports_legacy_safety_factor_basis(self):
        room = {
            "room_id": "room_1", "name": "Room 1", "zone_id": "zone_1", "source": "Reviewed model",
            "area_m2": 10.0, "occupancy": 1, "indoor_cooling_setpoint_c": 24.0,
            "indoor_heating_setpoint_c": 20.0, "heating_safety_factor": 1.15,
            "heating_safety_factor_source": "Heating brief", "heating_safety_factor_citations": citation("H-1"),
            "citations": citation("C-1"),
            "cooling_load_conditions": {"indoor_cooling_wet_bulb_c": 18.0, "source": "Reviewed conditions"},
            "cooling_load": {"safety_factor": 1.10, "source": "Cooling basis", "envelope_not_applicable": True},
            "heat_sources": [], "unapproved_components": [],
        }
        result = _annual_room(room, {}, {}, {}, {}, {}, {}, {}, {}, {}, {}, {}, {})
        self.assertEqual(result["safety_factor_application"]["cooling"], {
            "factor": 1.10, "basis": "legacy_room_factor", "source": "Cooling basis", "citations": [], "citation_status": "not_recorded_on_cooling_factor", "status": "not_applied_blocked", "applied_at": "",
        })
        self.assertEqual(result["safety_factor_application"]["heating"]["factor"], 1.15)
        self.assertEqual(result["safety_factor_application"]["heating"]["source"], "Heating brief")
        self.assertEqual(result["safety_factor_application"]["heating"]["citations"], citation("H-1"))

    def test_missing_cooling_factor_is_not_reported_as_applied(self):
        room = {
            "room_id": "room_1", "name": "Room 1", "zone_id": "zone_1", "source": "Reviewed model",
            "area_m2": 10.0, "occupancy": 1, "indoor_cooling_setpoint_c": 24.0,
            "indoor_heating_setpoint_c": 20.0,
            "cooling_load_conditions": {"indoor_cooling_wet_bulb_c": 18.0, "source": "Reviewed conditions"},
            "cooling_load": {"safety_factor": None, "source": "Cooling basis", "envelope_not_applicable": True},
            "heat_sources": [], "unapproved_components": [],
        }
        result = _annual_room(room, {}, {}, {}, {}, {}, {}, {}, {}, {}, {}, {}, {})
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["safety_factor_application"]["cooling"]["status"], "missing")

    def test_radiation_surface_requires_owner_orientation_and_source(self):
        radiation = {
            "radiation_id": "rad", "location": "Test", "timezone": "Australia/Sydney",
            "source": "Synthetic", "citations": citation(), "surfaces": [{
                "surface_id": "wall_1", "owner_room_id": "room_1", "orientation": "north",
                "source": "Synthetic", "citations": citation(), "irradiance_w_m2": [0.0] * HOURS_PER_YEAR,
            }],
        }
        self.assertEqual(len(validate_annual_radiation(radiation)["surfaces"]), 1)
        radiation["surfaces"][0]["orientation"] = ""
        with self.assertRaises(ValueError):
            validate_annual_radiation(radiation)

    def test_calendar_explicit_holiday_overrides_weekday(self):
        checked = validate_annual_calendar(calendar())
        self.assertEqual(len(checked["dates"]), 365)
        self.assertEqual(checked["dates"][0]["day_type"], "sunday_holiday")

    def test_epw_import_rejects_leap_year_file(self):
        headers = ["LOCATION,Test", "DESIGN CONDITIONS", "TYPICAL/EXTREME PERIODS", "GROUND TEMPERATURES", "HOLIDAYS/DAYLIGHT SAVINGS", "COMMENTS 1", "COMMENTS 2", "DATA PERIODS"]
        row = ["2024", "2", "29", "1", "60", "0", "25", "18", "50", "101325"] + ["0"] * 12
        with self.assertRaises(ValueError):
            import_epw("\n".join(headers + [",".join(row)] * 8784), weather_id="leap", source="Synthetic", citations=citation(), location="Test", timezone_name="Australia/Sydney")

    def test_annual_summary_converts_hourly_kw_to_kwh_and_tracks_peaks(self):
        rows = [{"hour_index": 0, "month": 1, "demand_kw": 2.0}, {"hour_index": 1, "month": 1, "demand_kw": 3.0}, {"hour_index": 2, "month": 2, "demand_kw": 1.0}]
        summary = _annual_summary(rows, "demand_kw")
        self.assertEqual(summary["annual_energy_kwh"], 6.0)
        self.assertEqual(summary["monthly_kwh"]["1"], 5.0)
        self.assertEqual(summary["monthly_kwh"]["2"], 1.0)
        self.assertEqual(summary["peak_hours"], [1])

    def test_annual_room_aggregation_aligns_sparse_rows_by_hour_index(self):
        room = {
            "room_id": "room_1", "zone_id": "zone_1", "status": "draft",
            "cooling_hours": [
                {"hour_index": 0, "month": 1, "design_total_kw": 1.0},
                {"hour_index": 744, "month": 2, "design_total_kw": 3.0},
            ],
        }
        start = date(2025, 1, 1)
        month_by_hour = [month for day in range(365) for month in [(start + timedelta(days=day)).month] * 24]
        summary = _aggregate_annual_section([room], "cooling", month_by_hour=month_by_hour)
        self.assertEqual(summary["hours"][0]["demand_kw"], 1.0)
        self.assertEqual(summary["hours"][1]["demand_kw"], 0.0)
        self.assertEqual(summary["hours"][744]["demand_kw"], 3.0)
        self.assertEqual(summary["hours"][744]["month"], 2)
        self.assertEqual(summary["annual_energy_kwh"], 4.0)
        self.assertEqual(summary["monthly_kwh"]["1"], 1.0)
        self.assertEqual(summary["monthly_kwh"]["2"], 3.0)
        self.assertEqual(summary["monthly_incomplete_hours"]["1"], 743)
        self.assertEqual(summary["monthly_incomplete_hours"]["2"], 671)
        self.assertEqual(summary["incomplete_room_hours"]["room_1"], {"count": HOURS_PER_YEAR - 2, "ranges": [[1, 743], [745, HOURS_PER_YEAR - 1]]})
        self.assertEqual(summary["zone_summaries"]["zone_1"]["status"], "draft")
        self.assertEqual(summary["zone_summaries"]["zone_1"]["monthly_incomplete_hours"]["1"], 743)
        self.assertTrue(any(f"{HOURS_PER_YEAR - 2} missing cooling hour(s)" in reason for reason in summary["blocked_reasons"]))
        self.assertEqual(summary["status"], "draft")
        hourly_rows = list(csv.DictReader(io.StringIO(annual_hourly_csv({"cooling": summary, "heating": {}}))))
        self.assertEqual(hourly_rows[1]["cooling_status"], "incomplete")
        self.assertEqual(hourly_rows[1]["cooling_demand_kw"], "")
        self.assertEqual(hourly_rows[1]["cooling_missing_rooms"], "room_1")
        self.assertEqual(hourly_rows[744]["cooling_energy_kwh"], "3.0")
        monthly_rows = list(csv.DictReader(io.StringIO(annual_monthly_csv({"cooling": summary, "heating": {}}))))
        self.assertEqual(monthly_rows[0]["cooling_incomplete_hours"], "743")
        self.assertEqual(monthly_rows[1]["cooling_incomplete_hours"], "671")

    def test_explicit_empty_or_unknown_annual_section_selection_blocks(self):
        for sections, expected in (([], "At least one annual report section must be selected."),
                                   (["cooling", "unknown"], "Unsupported annual report section(s): unknown.")):
            with self.subTest(sections=sections):
                report = calculate_annual_report(
                    {}, empty_schedule_library(), empty_hourly_load_model(), weather(), calendar(),
                    annual_radiation=empty_annual_radiation(), calculator_input_snapshot_fingerprint="snapshot",
                    selected_sections=sections,
                )
                self.assertEqual(report["status"], "blocked")
                self.assertIn(expected, report["blocked_reasons"])

    def test_annual_section_without_included_rooms_exports_unavailable_values_as_blank(self):
        summary = _aggregate_annual_section([], "cooling")
        self.assertEqual(summary["status"], "blocked")
        self.assertEqual(summary["hours"][0]["status"], "not_calculated")
        rows = list(csv.DictReader(io.StringIO(annual_hourly_csv({"cooling": summary, "heating": {}}))))
        self.assertEqual(rows[0]["cooling_status"], "not_calculated")
        self.assertEqual(rows[0]["cooling_demand_kw"], "")

    def test_empty_project_remains_draft_without_claiming_complete_scope(self):
        report = calculate_annual_report({}, empty_schedule_library(), empty_hourly_load_model(), weather(), calendar(), annual_radiation=empty_annual_radiation(), calculator_input_snapshot_fingerprint="snapshot")
        self.assertEqual(report["status"], "draft")
        self.assertFalse(report["scope_summary"]["complete_scope"])
        self.assertFalse(report["validated"])

    def test_annual_weather_calendar_and_radiation_timezones_must_match(self):
        mismatched_calendar = calendar()
        mismatched_calendar["timezone"] = "UTC"
        report = calculate_annual_report({}, empty_schedule_library(), empty_hourly_load_model(), weather(), mismatched_calendar, annual_radiation=empty_annual_radiation(), calculator_input_snapshot_fingerprint="snapshot")
        self.assertIn("Annual weather and calendar timezones must match before hourly schedules can be aligned.", report["blocked_reasons"])

        radiation = {
            "radiation_id": "radiation_test", "location": "Test", "timezone": "UTC",
            "source": "Synthetic radiation", "citations": citation(), "surfaces": [{
                "surface_id": "wall_1", "owner_room_id": "room_1", "orientation": "north",
                "source": "Synthetic radiation", "citations": citation(), "irradiance_w_m2": [0.0] * HOURS_PER_YEAR,
            }],
        }
        report = calculate_annual_report({}, empty_schedule_library(), empty_hourly_load_model(), weather(), calendar(), annual_radiation=radiation, calculator_input_snapshot_fingerprint="snapshot")
        self.assertIn("Annual radiation and calendar timezones must match before hourly solar values can be aligned.", report["blocked_reasons"])


if __name__ == "__main__":
    unittest.main()
