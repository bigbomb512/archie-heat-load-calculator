import copy
import unittest
from datetime import date, timedelta

from ai.annual_energy import (
    HOURS_PER_YEAR,
    _annual_summary,
    calculate_annual_report,
    empty_annual_radiation,
    import_epw,
    _wet_bulb_from_dew_point,
    validate_annual_radiation,
    validate_annual_calendar,
    validate_annual_weather,
)
from ai.hourly_loads import empty_hourly_load_model, empty_schedule_library


def citation(reference="SYN-ANNUAL"):
    return [{"reference": reference, "page": 1, "excerpt": "Synthetic development fixture"}]


def weather():
    return {
        "weather_id": "weather_test", "location": "Test", "timezone": "Australia/Sydney",
        "source": "Synthetic annual weather", "citations": citation(),
        "records": [{
            "hour_index": index, "timestamp": f"2025-01-01T{index % 24:02d}:00:00",
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

    def test_dew_point_is_converted_to_wet_bulb(self):
        wet_bulb = _wet_bulb_from_dew_point(25.0, 15.0, 101.3)
        self.assertLess(wet_bulb, 25.0)
        self.assertGreater(wet_bulb, 15.0)

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

    def test_empty_project_remains_draft_without_claiming_complete_scope(self):
        report = calculate_annual_report({}, empty_schedule_library(), empty_hourly_load_model(), weather(), calendar(), annual_radiation=empty_annual_radiation(), calculator_input_snapshot_fingerprint="snapshot")
        self.assertEqual(report["status"], "draft")
        self.assertFalse(report["scope_summary"]["complete_scope"])
        self.assertFalse(report["validated"])


if __name__ == "__main__":
    unittest.main()
