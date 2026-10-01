import json
import math
from pathlib import Path

from ai.heat_loads import (ashrae_ice_saturation_pressure_kpa, ashrae_saturation_pressure_kpa, humidity_ratio_from_db_wb,
                           moist_air_enthalpy_kj_kg, outside_air_load, wet_bulb_method_id)
from ai.annual_energy import _annual_weather_row, _wet_bulb_from_dew_point


FIXTURE = Path(__file__).parent / "fixtures" / "psychrometric_basis_benchmarks_v1.json"
FIXTURE_V2 = Path(__file__).parent / "fixtures" / "psychrometric_basis_benchmarks_v2.json"
FIXTURE_V3 = Path(__file__).parent / "fixtures" / "psychrometric_basis_benchmarks_v3.json"


def test_psychrometric_basis_benchmarks():
    suite = json.loads(FIXTURE.read_text())
    cases = {case["case_id"]: case for case in suite["cases"]}
    ashrae = cases["ashrae_example_1_thermodynamic_above_freezing"]
    inputs = ashrae["inputs"]
    ratio = humidity_ratio_from_db_wb(inputs["dry_bulb_c"], inputs["wet_bulb_c"], inputs["pressure_kpa"], ashrae["basis"])
    enthalpy = moist_air_enthalpy_kj_kg(inputs["dry_bulb_c"], ratio)
    for metric, actual in (("humidity_ratio_kg_kg", ratio), ("enthalpy_kj_kg", enthalpy)):
        expected = ashrae["expected_chart"][metric]
        deviation = actual - expected
        assert abs(deviation) <= ashrae["tolerances"][metric], (
            f"{ashrae['case_id']} {metric}: expected {expected}, actual {actual}, deviation {deviation}"
        )

    legacy = cases["legacy_relation_psychrometer_basis_characterization"]
    inputs = legacy["inputs"]
    actual = humidity_ratio_from_db_wb(inputs["dry_bulb_c"], inputs["wet_bulb_c"], inputs["pressure_kpa"], legacy["basis"])
    expected = legacy["expected_characterization"]["humidity_ratio_kg_kg"]
    assert math.isclose(actual, expected, abs_tol=legacy["tolerances"]["humidity_ratio_kg_kg"], rel_tol=0), (
        f"{legacy['case_id']} humidity_ratio_kg_kg: expected {expected}, actual {actual}, deviation {actual - expected}"
    )

    load = outside_air_load(1, 24, 18, 35, 24, 101.325,
                            indoor_wet_bulb_basis="thermodynamic", outdoor_wet_bulb_basis="psychrometer")
    assert load["inputs"]["indoor_wet_bulb_basis"] == "thermodynamic"
    assert load["inputs"]["outdoor_wet_bulb_basis"] == "psychrometer"

    comparison = cases["outside_air_load_basis_sensitivity"]
    actual = outside_air_load(**comparison["inputs"], indoor_wet_bulb_basis="psychrometer", outdoor_wet_bulb_basis="psychrometer")
    for metric in ("sensible_kw", "latent_kw", "total_kw"):
        expected = comparison["expected"]["psychrometer_approximation"][metric]
        deviation = actual[metric] - expected
        assert abs(deviation) <= comparison["tolerance_kw"], (
            f"{comparison['case_id']} psychrometer_approximation {metric}: expected {expected}, actual {actual[metric]}, deviation {deviation}"
        )


def test_psychrometric_basis_v2_benchmarks():
    suite = json.loads(FIXTURE_V2.read_text())
    cases = {case["case_id"]: case for case in suite["cases"]}
    saturation = cases["ashrae_f25_iapws_liquid_water_saturation_pressure"]
    for temperature, expected in zip(saturation["inputs"]["temperatures_c"], saturation["expected_saturation_pressure_kpa"]):
        actual = ashrae_saturation_pressure_kpa(temperature)
        assert abs(actual - expected) <= saturation["tolerance_kpa"], (
            f"{saturation['case_id']} at {temperature} C: expected {expected}, actual {actual}, deviation {actual - expected}"
        )

    f25 = cases["ashrae_f25_thermodynamic_state_and_outside_air_load"]
    ratio = humidity_ratio_from_db_wb(f25["inputs"]["dry_bulb_c"], f25["inputs"]["wet_bulb_c"],
                                      f25["inputs"]["pressure_kpa"], "thermodynamic")
    expected = f25["expected"]
    assert abs(ratio - expected["humidity_ratio_40_20_kg_kg"]) <= f25["tolerance"]["humidity_ratio_kg_kg"]
    enthalpy = moist_air_enthalpy_kj_kg(f25["inputs"]["dry_bulb_c"], ratio)
    assert abs(enthalpy - expected["enthalpy_40_20_kj_kg"]) <= f25["tolerance"]["enthalpy_kj_kg"]
    f25_load = outside_air_load(100, 24, 18, 35, 24, 101.325,
                                indoor_wet_bulb_basis="thermodynamic", outdoor_wet_bulb_basis="thermodynamic")
    for metric, expected_value in (("sensible_kw", expected["outside_air_sensible_kw"]),
                                   ("latent_kw", expected["outside_air_latent_kw"]),
                                   ("total_kw", expected["outside_air_total_kw"])):
        actual = f25_load[metric]
        assert abs(actual - expected_value) <= f25["tolerance"]["load_kw"], (
            f"{f25['case_id']} {metric}: expected {expected_value}, actual {actual}, deviation {actual - expected_value}"
        )
    assert f25_load["inputs"]["outdoor_wet_bulb_method"] == wet_bulb_method_id("thermodynamic")
    assert wet_bulb_method_id("thermodynamic") == "thermodynamic_ashrae_eq33_iapws_water_ice_v3"

    dew_point = cases["ashrae_f25_dew_point_to_thermodynamic_wet_bulb"]
    inputs = dew_point["inputs"]
    actual_wet_bulb = _wet_bulb_from_dew_point(inputs["dry_bulb_c"], inputs["dew_point_c"], inputs["pressure_kpa"])
    expected_wet_bulb = dew_point["expected"]["thermodynamic_wet_bulb_c"]
    assert abs(actual_wet_bulb - expected_wet_bulb) <= dew_point["tolerance"]["wet_bulb_c"], (
        f"{dew_point['case_id']} wet_bulb_c: expected {expected_wet_bulb}, actual {actual_wet_bulb}, deviation {actual_wet_bulb - expected_wet_bulb}"
    )
    converted = _annual_weather_row({
        "outdoor_dry_bulb_c": inputs["dry_bulb_c"], "outdoor_dew_point_c": inputs["dew_point_c"],
        "atmospheric_pressure_kpa": inputs["pressure_kpa"],
    })
    assert converted["outdoor_wet_bulb_basis"] == "thermodynamic"
    assert converted["outdoor_wet_bulb_c"]["value"] == actual_wet_bulb


def test_psychrometric_basis_v3_ice_benchmarks():
    suite = json.loads(FIXTURE_V3.read_text())
    cases = {case["case_id"]: case for case in suite["cases"]}
    saturation = cases["ashrae_f25_iapws_ice_saturation_pressure"]
    for temperature, expected in zip(saturation["inputs"]["temperatures_c"], saturation["expected_saturation_pressure_kpa"]):
        actual = ashrae_ice_saturation_pressure_kpa(temperature)
        assert abs(actual - expected) <= saturation["tolerance_kpa"], (
            f"{saturation['case_id']} at {temperature} C: expected {expected}, actual {actual}, deviation {actual - expected}"
        )
    case = cases["ashrae_f25_below_freezing_thermodynamic_state"]
    inputs = case["inputs"]
    expected = case["expected"]
    pressure = ashrae_ice_saturation_pressure_kpa(inputs["wet_bulb_c"])
    ratio = humidity_ratio_from_db_wb(inputs["dry_bulb_c"], inputs["wet_bulb_c"], inputs["pressure_kpa"], "thermodynamic")
    enthalpy = moist_air_enthalpy_kj_kg(inputs["dry_bulb_c"], ratio)
    for metric, actual, expected_value, tolerance in (
        ("saturation_pressure_kpa", pressure, expected["saturation_pressure_kpa"], case["tolerance"]["pressure_kpa"]),
        ("humidity_ratio_kg_kg", ratio, expected["humidity_ratio_kg_kg"], case["tolerance"]["humidity_ratio_kg_kg"]),
        ("enthalpy_kj_kg", enthalpy, expected["enthalpy_kj_kg"], case["tolerance"]["enthalpy_kj_kg"]),
    ):
        assert abs(actual - expected_value) <= tolerance, (
            f"{case['case_id']} {metric}: expected {expected_value}, actual {actual}, deviation {actual - expected_value}"
        )


def test_thermodynamic_basis_validation():
    for temperature in (-0.01, 373.946):
        try:
            ashrae_saturation_pressure_kpa(temperature)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Expected saturation-pressure domain validation to fail at {temperature} C")
    for temperature in (-223.151, 0.0):
        try:
            ashrae_ice_saturation_pressure_kpa(temperature)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Expected ice saturation-pressure domain validation to fail at {temperature} C")
    for args in ((24, 25, 101.325, "thermodynamic"), (24, 18, 0, "thermodynamic"),
                 (-20, -25, 101.325, "thermodynamic")):
        try:
            humidity_ratio_from_db_wb(*args)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Expected invalid psychrometric state to fail: {args}")
    assert humidity_ratio_from_db_wb(0, -5, 101.325, "thermodynamic") > 0


if __name__ == "__main__":
    test_psychrometric_basis_benchmarks()
    test_psychrometric_basis_v2_benchmarks()
    test_psychrometric_basis_v3_ice_benchmarks()
    test_thermodynamic_basis_validation()
    print("PASS - basis-aware psychrometric reference and boundary checks")
