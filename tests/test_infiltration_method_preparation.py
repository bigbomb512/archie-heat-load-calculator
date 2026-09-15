#!/usr/bin/env python3
"""Isolated characterization, not engineer approval or Drawing 6 integration.

All room/weather/schedule values are synthetic test fixtures. No project files
are read or written. No approved gate is manufactured, patched or bypassed.
Numerical expectations are frozen independent Decimal worksheet results.
"""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai.heat_loads import (
    humidity_ratio_from_db_wb, infiltration_flow_lps, infiltration_load,
    moist_air_enthalpy_kj_kg, outside_air_load, specific_volume_m3_kg,
)
from ai.hourly_loads import (
    aggregate_hours, calculate_hourly_load_report, default_room_components, hour_total,
    resolved_profiles, room_contributions, room_is_provisional,
    room_static_missing, room_volume_m3, validate_day_profile,
    validate_room_component, validate_room_components,
)
from ai.infiltration_gate import (
    METHOD_ID, empty_infiltration_method_gate, gate_is_approved,
    validate_infiltration_method_gate,
)
from ai.design_requirements import validate_design_requirements
from ai.hourly_loads import build_hourly_load_model

VECTORS = json.loads((Path(__file__).parent / 'fixtures/infiltration_method_vectors.json').read_text())
CASES = {row['case_id']: row for row in VECTORS['cases']}
SYNTHETIC = 'Synthetic unit-test fixture; not project evidence or engineer approval'
# Real code-policy reference, not a fabricated leakage measurement or approval.
POLICY_CITATION = {'reference': 'docs/infiltration_method_gate.md', 'page': None,
                   'excerpt': 'All resolved airflow is treated as outdoor-design-condition volume flow.'}


def inputs(name):
    return {k: v if k == 'unit' else float(v) for k, v in CASES[name]['inputs'].items()}


def component(status='provisional'):
    return dict(component_id='test-infiltration', component_type='infiltration',
                value=0.36, unit='ACH', source_room_id='', source=SYNTHETIC,
                citations=[deepcopy(POLICY_CITATION)], verification_status=status,
                calculation_status='calculated', method_id=METHOD_ID,
                air_path='uncontrolled_infiltration', flow_reference='outdoor_design_condition')


def fixture():
    """A complete synthetic static shape, never a real reviewed building."""
    requirements = validate_design_requirements({
        'indoor_cooling_setpoint_c': 24, 'outdoor_summer_db_c': 35,
        'cooling_load_conditions': dict(indoor_cooling_wet_bulb_c=18,
            outdoor_summer_wet_bulb_c=24, atmospheric_pressure_kpa=101.325,
            verification_status='provisional', source=SYNTHETIC),
        'zones': [dict(zone_id='test-zone', name='Synthetic room', area_m2=20,
            occupancy=1, heat_sources=[dict(name='Synthetic zero gain', quantity=1,
                watts=0, kind='other', diversity_factor=1, space_gain_factor=1,
                verification_status='provisional', source=SYNTHETIC)],
            cooling_load=dict(people_sensible_w_per_person=0, people_latent_w_per_person=0,
                people_diversity_factor=1, lighting_w_m2=0, lighting_diversity_factor=1,
                outside_air_lps=100, safety_factor=1.1, envelope_not_applicable=True,
                verification_status='provisional', source=SYNTHETIC))]})
    model = build_hourly_load_model(requirements)
    room = model['rooms'][0]
    room.update(name='Synthetic room', source=SYNTHETIC, ceiling_height_mm=3000)
    room['schedule_assignments'].update(outside_air='test-outside', infiltration='test-infiltration')
    room['unapproved_components'] = [component()] + [
        c for c in default_room_components() if c['component_type'] != 'infiltration']
    return requirements, model, room


def schedule_library():
    def schedule(identifier):
        return dict(schedule_id=identifier, title=identifier, status='provisional',
            source=SYNTHETIC, citations=[], day_profiles={
                'weekday': dict(values=[1.0] * 24, status='provisional', source=SYNTHETIC, citations=[]),
                'saturday': dict(values=[], status='missing', source='', citations=[]),
                'sunday_holiday': dict(values=[], status='missing', source='', citations=[])})
    return {'schedules': [schedule('test-outside'), schedule('test-infiltration')]}


def scenario():
    def number(value):
        return dict(value=value, status='provisional', source=SYNTHETIC, citations=[])
    return dict(scenario_id='test-day', title='Synthetic day', mode='cooling',
        representative_month='January', day_type='weekday', status='provisional',
        source=SYNTHETIC, citations=[], atmospheric_pressure_kpa=number(101.325),
        hours=[dict(hour=h, outdoor_dry_bulb_c=number(35), outdoor_wet_bulb_c=number(24))
               for h in range(24)])


class Arithmetic(unittest.TestCase):
    def test_frozen_independent_load_vectors(self):
        for name, row in CASES.items():
            with self.subTest(case=name):
                result = infiltration_load(**inputs(name))
                for field in ('sensible_kw', 'latent_kw', 'total_kw'):
                    self.assertAlmostEqual(result[field], float(row['expected'][field]), delta=0.000051)
                for field in ('raw_signed_sensible_kw', 'raw_signed_latent_kw',
                              'mass_flow_kg_s', 'applied_flow_lps'):
                    self.assertAlmostEqual(result['inputs'][field], float(row['expected'][field]),
                                           delta=0.000051 if 'kw' in field else 0.00000051)

    def test_independent_intermediate_properties(self):
        for name, row in CASES.items():
            x = inputs(name)
            for state in ('indoor', 'outdoor'):
                with self.subTest(case=name, state=state):
                    expected = row[state]
                    db, wb, pressure = x[state + '_db_c'], x[state + '_wb_c'], x['pressure_kpa']
                    self.assertAlmostEqual(humidity_ratio_from_db_wb(db, wb, pressure),
                                           float(expected['humidity_ratio']), delta=1e-11)
                    # Feed independent w, not the function's result, into each downstream check.
                    w = float(expected['humidity_ratio'])
                    self.assertAlmostEqual(moist_air_enthalpy_kj_kg(db, w),
                                           float(expected['enthalpy_kj_kg']), delta=2e-9)
                    self.assertAlmostEqual(specific_volume_m3_kg(db, w, pressure),
                                           float(expected['volume_m3_kg']), delta=2e-11)

    def test_equivalent_units_match_independent_six_lps_vector(self):
        for value, unit in ((0.36, 'ACH'), (6, 'L/s'), (0.006, 'm3/s'), (21.6, 'm3/h')):
            with self.subTest(unit=unit):
                self.assertAlmostEqual(infiltration_flow_lps(value, unit, 60), 6)
                result = infiltration_load(**{**inputs('hot_humid'), 'value': value, 'unit': unit}, room_volume_m3=60)
                for field in ('sensible_kw', 'latent_kw', 'total_kw'):
                    self.assertAlmostEqual(result[field], float(CASES['hot_humid']['expected'][field]), delta=0.000051)

    def test_room_height_sensitivity_with_synthetic_area_and_ach(self):
        for height, expected_volume, expected_flow in ((2600, 52, 5.2), (2900, 58, 5.8), (3150, 63, 6.3)):
            with self.subTest(height=height):
                volume = room_volume_m3({'area_m2': 20, 'ceiling_height_mm': height}, {})
                self.assertEqual(volume, expected_volume)
                self.assertAlmostEqual(infiltration_flow_lps(0.36, 'ACH', volume), expected_flow)
                result = infiltration_load(**{**inputs('hot_humid'), 'value': 0.36, 'unit': 'ACH'}, room_volume_m3=volume)
                self.assertAlmostEqual(result['total_kw'], float(CASES[f'height_{height}']['expected']['total_kw']), delta=0.000051)

    def test_double_volume_matches_independent_double_flow(self):
        result = infiltration_load(**{**inputs('hot_humid'), 'value': 0.36, 'unit': 'ACH'}, room_volume_m3=120)
        self.assertEqual(result['inputs']['resolved_flow_lps'], 12)
        self.assertAlmostEqual(result['total_kw'], float(CASES['double_flow']['expected']['total_kw']), delta=0.000051)

    def test_direct_flow_is_independent_of_room_height(self):
        for volume in (None, 52, 63, 120):
            result = infiltration_load(**inputs('hot_humid'), room_volume_m3=volume)
            self.assertAlmostEqual(result['total_kw'], float(CASES['hot_humid']['expected']['total_kw']), delta=0.000051)

    def test_zero_schedule_zeroes_applied_load_and_signed_diagnostics(self):
        result = infiltration_load(**inputs('schedule_off'))
        self.assertEqual(result['inputs']['resolved_flow_lps'], 6)
        for field in ('sensible_kw', 'latent_kw', 'total_kw'):
            self.assertEqual(result[field], 0)
        for field in ('applied_flow_lps', 'mass_flow_kg_s', 'raw_signed_sensible_kw', 'raw_signed_latent_kw'):
            self.assertEqual(result['inputs'][field], 0)

    def test_sign_directions_and_componentwise_clamp(self):
        for name, sensible_sign, latent_sign in (
            ('hot_humid', 1, 1), ('hot_dry', 1, -1),
            ('cool_humid', -1, 1), ('cool_dry', -1, -1)):
            result = infiltration_load(**inputs(name))
            with self.subTest(case=name):
                self.assertGreater(result['inputs']['raw_signed_sensible_kw'] * sensible_sign, 0)
                self.assertGreater(result['inputs']['raw_signed_latent_kw'] * latent_sign, 0)
                if sensible_sign < 0:
                    self.assertEqual(result['sensible_kw'], 0)
                if latent_sign < 0:
                    self.assertEqual(result['latent_kw'], 0)

    def test_outside_air_has_different_existing_sign_policy(self):
        x = inputs('cool_dry')
        result = outside_air_load(x['value'], x['indoor_db_c'], x['indoor_wb_c'],
                                  x['outdoor_db_c'], x['outdoor_wb_c'], x['pressure_kpa'])
        self.assertLess(result['sensible_kw'], 0)
        self.assertLess(result['latent_kw'], 0)

    def test_invalid_units_values_and_ach_volume_raise(self):
        for value, unit, volume in ((0, 'ACH', 60), (-1, 'L/s', None),
            (1, 'cfm', None), (1, 'ach', 60), (1, 'ACH', None), (1, 'ACH', 0), (1, 'ACH', -1)):
            with self.subTest(value=value, unit=unit, volume=volume), self.assertRaises(ValueError):
                infiltration_flow_lps(value, unit, volume)

    def test_invalid_psychrometrics_rejected_even_when_schedule_off(self):
        for changed in ({'indoor_wb_c': 25}, {'pressure_kpa': 0}, {'outdoor_wb_c': 0}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                infiltration_load(**{**inputs('schedule_off'), **changed})

    def test_component_shape_and_safety_once(self):
        result = infiltration_load(**inputs('hot_humid'))
        self.assertEqual(set(result), {'name', 'sensible_kw', 'latent_kw', 'total_kw', 'inputs', 'formula'})
        total = hour_total(0, [result], 1.1)
        # Independent vector rounds to 0.0744 + 0.0644 = 0.1388 kW;
        # allowance 0.01388 rounds to 0.0139, design total = 0.1527.
        self.assertEqual(total['subtotal_kw'], 0.1388)
        self.assertEqual(total['safety_allowance_kw'], 0.0139)
        self.assertEqual(total['design_total_kw'], 0.1527)
        self.assertIn('input_rows', total['components']['infiltration'])


    def test_aggregation_sums_existing_allowances_without_second_factor(self):
        room = hour_total(0, [infiltration_load(**inputs('hot_humid'))], 1.1)
        combined = aggregate_hours([room, deepcopy(room)], 0)
        self.assertEqual(combined['subtotal_kw'], 0.2776)
        self.assertEqual(combined['safety_allowance_kw'], 0.0278)
        self.assertEqual(combined['design_total_kw'], 0.3054)


class InputContract(unittest.TestCase):
    def test_placeholder_gate_stays_unapproved(self):
        gate = validate_infiltration_method_gate(empty_infiltration_method_gate())
        self.assertFalse(gate_is_approved(gate))
        for field in ('engineer_name', 'engineer_credential', 'approved_at', 'method_citation'):
            self.assertEqual(gate[field], '')
        self.assertEqual(gate['citations'], [])

    def test_approval_without_real_evidence_rejected(self):
        gate = empty_infiltration_method_gate()
        gate['approval_status'] = 'approved'
        with self.assertRaisesRegex(ValueError, 'engineer name'):
            validate_infiltration_method_gate(gate)

    def test_changed_policy_and_unknown_method_rejected(self):
        for change in ('policy', 'method_id'):
            gate = empty_infiltration_method_gate()
            if change == 'policy':
                gate['policy']['negative_cooling_policy'] = 'unsigned'
            else:
                gate['method_id'] = ''
            with self.assertRaises(ValueError):
                validate_infiltration_method_gate(gate)

    def test_missing_source_citation_and_wrong_air_path_rejected(self):
        for changed in ({'source': ''}, {'citations': []}, {'air_path': 'outside_air'},
                        {'air_path': 'make_up_air'}, {'flow_reference': 'standard_air'},
                        {'method_id': ''}, {'value': float('nan')}, {'value': float('inf')}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                validate_room_component({**component(), **changed}, 'test-room', 1)

    def test_explicit_absence_is_not_a_zero_flow_calculation(self):
        # Synthetic validator state only; this does not confirm a real room absent.
        _, _, room = fixture()
        absent = {**component(), 'calculation_status': 'not_present_confirmed',
                  'verification_status': 'confirmed', 'value': None, 'unit': ''}
        room['unapproved_components'][0] = validate_room_component(absent, 'test-room', 1)
        rows = room_contributions(room, {}, empty_infiltration_method_gate(),
                                  {'outside_air': [1] * 24}, 0, scenario()['hours'][0], 101.325)
        self.assertFalse(any(row['name'] == 'infiltration' for row in rows))
        self.assertEqual(hour_total(0, rows, 1)['subtotal_kw'], 2.3125)

    def test_missing_gate_and_ach_height_remain_blocked(self):
        _, _, room = fixture()
        room['ceiling_height_mm'] = None
        reasons = room_static_missing(room, {}, empty_infiltration_method_gate())
        self.assertIn('approved infiltration method gate', reasons)
        self.assertIn('reviewed room or zone ceiling height for ACH infiltration', reasons)

    def test_unassessed_and_stored_infiltration_remain_blocked(self):
        _, _, room = fixture()
        for state, reason in (('not_assessed', 'infiltration assessment'),
                              ('stored_not_calculated', 'infiltration calculation eligibility')):
            room['unapproved_components'][0]['calculation_status'] = state
            self.assertIn(reason, room_static_missing(room, {}, empty_infiltration_method_gate()))

    def test_full_synthetic_report_without_approval_cannot_publish_project_peak(self):
        requirements, model, _ = fixture()
        before = deepcopy(model)
        report = calculate_hourly_load_report(requirements, schedule_library(),
            {'scenarios': [scenario()]}, model, ['test-day'])
        self.assertEqual(report['status'], 'blocked')
        self.assertEqual(report['project_peak'], {})
        self.assertIn('approved infiltration method gate',
                      report['scenario_results'][0]['rooms'][0]['blocked_reasons'])
        self.assertEqual(model, before)

    def test_incomplete_scenario_cannot_publish_project_peak(self):
        requirements, model, _ = fixture()
        report = calculate_hourly_load_report(requirements, schedule_library(),
            {'scenarios': []}, model, [])
        self.assertEqual(report['status'], 'blocked')
        self.assertEqual(report['project_peak'], {})

    def test_infiltration_has_no_implicit_outside_air_schedule_fallback(self):
        _, _, room = fixture()
        room['schedule_assignments']['infiltration'] = ''
        profiles, missing, _ = resolved_profiles(schedule_library(), 'weekday', room)
        self.assertIn('infiltration schedule assignment', missing)
        self.assertNotIn('infiltration', profiles)
        self.assertIn('outside_air', profiles)

    def test_missing_and_incomplete_day_profiles_block(self):
        _, _, room = fixture()
        for length in (0, 23):
            lib = schedule_library()
            lib['schedules'][1]['day_profiles']['weekday']['values'] = [1] * length
            _, missing, _ = resolved_profiles(lib, 'weekday', room)
            self.assertTrue(any('infiltration' in reason for reason in missing))

    def test_schedule_bounds_and_nonfinite_values_rejected(self):
        for value in (-0.1, 1.1, float('nan'), float('inf'), True):
            day = dict(values=[value] * 24, status='provisional', source=SYNTHETIC)
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_day_profile(day, 'test-infiltration', 'weekday')

    def test_provisional_infiltration_and_profiles_flag_draft_basis(self):
        _, _, room = fixture()
        self.assertTrue(room_is_provisional(room))
        _, missing, provisional = resolved_profiles(schedule_library(), 'weekday', room)
        self.assertFalse(missing)
        self.assertTrue(provisional)

    def test_volume_prefers_room_then_zone_never_project_default(self):
        self.assertEqual(room_volume_m3({'area_m2': 20, 'ceiling_height_mm': 2600},
                                       {'ceiling_height_mm': 3150}), 52)
        self.assertEqual(room_volume_m3({'area_m2': 20}, {'ceiling_height_mm': 3150}), 63)
        self.assertIsNone(room_volume_m3({'area_m2': 20}, {}))

    def test_outside_air_separate_once_and_not_inferred_from_infiltration(self):
        # Call arithmetic assembly only, not report eligibility. This proves
        # separate summation, NOT engineering approval or physical path uniqueness.
        _, _, room = fixture()
        profiles = {'outside_air': [1] * 24, 'infiltration': [1] * 24}
        before = deepcopy(room)
        rows = room_contributions(room, {}, empty_infiltration_method_gate(), profiles,
                                 0, scenario()['hours'][0], 101.325)
        self.assertEqual(sum(row['name'] == 'outside_air' for row in rows), 1)
        self.assertEqual(sum(row['name'] == 'infiltration' for row in rows), 1)
        total = hour_total(0, rows, 1.1)
        self.assertEqual(total['components']['outside_air']['inputs']['flow_lps'], 100)
        self.assertEqual(total['components']['infiltration']['inputs']['applied_flow_lps'], 6)
        # Fixed independent values: OA 1.2398 + 1.0727 and infiltration .1388.
        self.assertEqual(total['subtotal_kw'], 2.4513)
        self.assertEqual(total['design_total_kw'], 2.6964)
        self.assertEqual(room, before)
        profiles['infiltration'] = [0] * 24
        off = hour_total(0, room_contributions(room, {}, empty_infiltration_method_gate(),
                          profiles, 0, scenario()['hours'][0], 101.325), 1.1)
        self.assertEqual(off['components']['outside_air'], total['components']['outside_air'])
        self.assertEqual(off['components']['infiltration']['total_kw'], 0)

    def test_direct_flow_requires_no_volume_but_gate_still_blocks(self):
        _, _, room = fixture()
        room['unapproved_components'][0].update(unit='L/s', value=6)
        room['ceiling_height_mm'] = None
        reasons = room_static_missing(room, {}, empty_infiltration_method_gate())
        self.assertIn('approved infiltration method gate', reasons)
        self.assertFalse(any('height' in reason for reason in reasons))



class KnownGaps(unittest.TestCase):
    """Passing characterization of limitations, not proof of release readiness."""
    def test_shared_schedule_id_currently_accepted_needs_engineer_decision(self):
        _, _, room = fixture()
        room['schedule_assignments']['infiltration'] = 'test-outside'
        profiles, missing, _ = resolved_profiles(schedule_library(), 'weekday', room)
        self.assertFalse(missing)
        self.assertEqual(profiles['infiltration'], profiles['outside_air'])

    def test_zero_area_not_caught_until_arithmetic_volume_validation(self):
        _, _, room = fixture()
        room['area_m2'] = 0
        volume = room_volume_m3(room, {})
        self.assertEqual(volume, 0)
        self.assertFalse(any('height' in r for r in room_static_missing(room, {}, None)))
        with self.assertRaises(ValueError):
            infiltration_flow_lps(0.36, 'ACH', volume)

    def test_duplicate_type_with_different_ids_is_not_rejected(self):
        first = component()
        second = {**component(), 'component_id': 'another-infiltration'}
        rows = validate_room_components([first, second], 'test-room')
        self.assertEqual(sum(row['component_type'] == 'infiltration' for row in rows), 2)
        # Existing infiltration_component selects the first one: future contract
        # must decide uniqueness; do not silently change production in this task.


if __name__ == '__main__':
    unittest.main(verbosity=2)
