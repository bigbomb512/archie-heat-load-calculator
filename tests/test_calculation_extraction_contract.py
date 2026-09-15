"""Synthetic calculation-extraction contract; no PDF or private fixtures."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from ai.calculation_extraction import extract_calculation_input_evidence as extract


def record(field, value, unit='', label='Studio A', entity='room', **extra):
    return dict(field=field, value=value, unit=unit, label=label, entity=entity,
                excerpt=f'{label}: {field} {value} {unit}', coordinates=[12, 20, 80, 30], **extra)


def page(number, role, text='', records=()):
    return dict(page=number, drawing_number=f'SYN-{number}', sheet_classification=role,
                structured_content=dict(text=text, records=list(records)))


def packet(*pages):
    return dict(source_pdf='synthetic.pdf', drawing_set=dict(pages=list(pages)))


class CalculationExtractionContractTests(unittest.TestCase):
    def test_all_page_capabilities_preserve_citations(self):
        groups = [
            ('floor_plan', [record('identity', 'Studio A'), record('area', 24, 'm²'),
                            record('dimension_chain', [2, 3, 4], 'm'),
                            record('wall', {'from': 'Studio A', 'to': 'EW7'}, vector_id='wall-v'),
                            record('partition', {'from': 'Studio A', 'to': 'Studio B'}),
                            record('room_surface', {'from': 'Studio A', 'to': 'EW7'})]),
            ('reflected_ceiling_plan', [record('ceiling_height', 3.2, 'm'), record('ceiling_type', 'CT7'),
                                      record('fixture_tag', 'LT7'), record('quantity', 8, 'count'),
                                      record('room_service', {'from': 'Studio A', 'to': 'LT7'})]),
            ('opening_schedule', [record(f, v, 'm', label='W77', entity='opening') for f, v in
                                  [('width', 1.2), ('height', 2.1), ('sill', 0), ('head', 2.1)]]),
            ('equipment_schedule', [record(f, v, u, label='EQ7', entity='equipment') for f, v, u in
                                    [('name', 'Process unit', ''), ('quantity', 2, 'count'), ('model', 'MX7', ''),
                                     ('location', 'Studio A', ''), ('nameplate_power', 2.4, 'kW')]]),
            ('legend_or_general_notes', [record('occupancy', 7, 'people'), record('operating_hours', {'weekday': ['08:00', '17:00']}),
                                         record('setpoint', 23, 'C'), record('outside_air', 360, 'm³/h'),
                                         record('construction', 'EW7'), record('u_value', 0.3, 'W/m²K'),
                                         record('glazing_reference', 'GL7')]),
            ('section', [record('floor_to_floor_height', 4, 'm', entity='level', label='L1'),
                         record('ceiling_height', 3.2, 'm'), record('vertical_boundary', {'from': 'L1', 'to': 'L2'})]),
        ]
        result = extract(packet(*(page(70+i, role, records=rows) for i, (role, rows) in enumerate(groups))))
        self.assertEqual(len(result['candidates']), sum(len(rows) for _, rows in groups))
        for row in result['candidates']:
            self.assertTrue(row['source_fingerprint'])
            self.assertTrue(row['source']['drawing_number'])
            self.assertTrue(row['source']['excerpt'])
            self.assertEqual(row['source']['location']['coordinates'], [12, 20, 80, 30])
        outside = next(r for r in result['candidates'] if r['category'] == 'outside_air')
        self.assertEqual((outside['value'], outside['unit']), (100, 'L/s'))

    def test_explicit_units_only_and_mixed_opening_units(self):
        result = extract(packet(page(1, 'floor_plan', 'W77 1.2 m x 2100 mm\nW78 1200 x 2100')))
        openings = {r['value']['tag']: r for r in result['candidates'] if r['category'] == 'opening'}
        self.assertEqual(openings['W77']['value']['width_mm'], 1200)
        self.assertEqual(openings['W77']['value']['height_mm'], 2100)
        self.assertNotIn('width_mm', openings['W78']['value'])
        self.assertIn('unit', openings['W78']['unresolved_fields'])

    def test_missing_unsupported_units_and_fields_remain_unresolved(self):
        result = extract(packet(page(1, 'floor_plan', records=[record('area', 42), record('ceiling_height', 10, 'ft'), record('unknown', 9)])))
        self.assertTrue(all(r['value'] is None and r['unresolved_fields'] for r in result['candidates']))

    def test_no_room_value_leakage(self):
        p = page(8, 'reflected_ceiling_plan', 'Studio A ceiling 3 m\nStudio B ceiling 4 m\nceiling 5 m')
        p['room_label_candidates'] = [{'text': 'Studio A'}, {'text': 'Studio B'}]
        rows = [r for r in extract(packet(p))['candidates'] if r['category'] == 'ceiling_height']
        self.assertEqual({(r['room_id'], r['value']) for r in rows}, {('Studio A', 3000), ('Studio B', 4000), ('', 5000)})
        self.assertIn('room_allocation', next(r for r in rows if not r['room_id'])['unresolved_fields'])

    def test_equipment_never_becomes_heat_implicitly(self):
        rows = [record('nameplate_power', 5, 'kW', entity='equipment'),
                record('heat_to_space', 2, 'kW', entity='equipment'),
                record('heat_to_space', 1, 'kW', entity='equipment', label='EQ2', heat_basis='Explicit sensible heat to room')]
        result = extract(packet(page(3, 'equipment_schedule', records=rows)))
        self.assertTrue(all(r['status'] == 'evidence_only' for r in result['candidates']))
        heats = [r for r in result['candidates'] if r['field'] == 'heat_to_space']
        self.assertEqual({r['value'] for r in heats}, {None, 1000})

    def test_fingerprints_and_ids_survive_all_evidence_reordering(self):
        data = packet(page(2, 'section', records=[record('ceiling_height', 3, 'm'), record('ceiling_type', 'CT1')]), page(1, 'floor_plan'))
        ocr = {'pages': [{'page': 1, 'word_samples': [{'text': 'A', 'bbox': [1, 2, 3, 4]}, {'text': 'B', 'bbox': [5, 6, 7, 8]}]}]}
        first = extract(data, spatial_ocr=ocr)
        data['drawing_set']['pages'].reverse()
        data['drawing_set']['pages'][1]['structured_content']['records'].reverse()
        ocr['pages'][0]['word_samples'].reverse()
        second = extract(data, spatial_ocr=ocr)
        self.assertEqual(first['fingerprint'], second['fingerprint'])
        self.assertEqual(first['binding']['fingerprint'], second['binding']['fingerprint'])

    def test_3d_values_are_cross_check_only_even_with_auto_activate(self):
        data = packet(page(9, '3d_reference', records=[record('area', 77, 'm²')]))
        vision = {'result': {'auto_extraction': {'entities': [{'kind': 'room', 'label': 'Studio A', 'page': 9, 'area_m2': 77, 'auto_activate': True}]}}}
        rows = extract(data, vision_response=vision)['candidates']
        self.assertTrue(rows)
        self.assertTrue(all(r['status'] == 'evidence_only' and r['cross_check_only'] and r['value'] is None for r in rows))

    def test_conflicts_remain_visible_even_at_identical_locations(self):
        a = record('area', 20, 'm²'); b = {**a, 'value': 22}
        rows = extract(packet(page(1, 'floor_plan', records=[a, b])))['candidates']
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r['status'] == 'conflict' and r['competing_candidates'] for r in rows))

    def test_plan_elevation_and_schedule_relationships_retain_sources(self):
        result = extract(packet(page(1, 'floor_plan', 'W77'), page(2, 'elevation', 'W77 1200 x 2100 mm')))
        links = result['binding']['relationships']
        self.assertTrue(any(r['kind'] == 'opening_plan_elevation_binding' for r in links))
        observations = {r['observation_id']: r for r in result['binding']['observations']}
        for link in links:
            for key in ('from_observation_id', 'to_observation_id'):
                self.assertTrue(observations[link[key]]['source']['drawing_number'])

    def test_plan_elevation_schedule_record_links_include_all_three_pages(self):
        data = packet(page(1, 'floor_plan', 'W77'),
                      page(2, 'elevation', records=[record('width', 1.2, 'm', label='W77', entity='opening')]),
                      page(3, 'opening_schedule', records=[record('height', 2100, 'mm', label='W77', entity='opening', table_id='WS', row='W77', column='height')]))
        result = extract(data)
        links = [r for r in result['binding']['relationships'] if r['kind'] == 'opening_tag_cross_reference']
        self.assertEqual({tuple(r['pages']) for r in links}, {(1, 2), (1, 3), (2, 3)})
        self.assertTrue(all(len(r['citations']) == 2 for r in links))

    def test_invalid_numbers_hours_and_counts_are_unresolved(self):
        rows = [record('area', -1, 'm²'), record('ceiling_height', float('nan'), 'm'),
                record('quantity', 2.5, 'count'), record('dimension_chain', [2, -1], 'm'),
                record('operating_hours', {'weekday': ['25:70', '17:00']})]
        result = extract(packet(page(1, 'floor_plan', records=rows)))
        self.assertTrue(all(r['value'] is None and r['unresolved_fields'] for r in result['candidates']))

    def test_arbitrary_equipment_row_retains_nameplate_and_explicit_heat(self):
        result = extract(packet(page(1, 'equipment_schedule',
            'Equipment EQ7: Process unit; quantity 2; model MX-7; location Studio A; nameplate power 2 kW; heat-to-space 0.8 kW')))
        rows = {r['field']: r for r in result['candidates']}
        self.assertEqual(rows['name']['value'], 'Process unit')
        self.assertEqual(rows['model']['value'], 'MX-7')
        self.assertEqual(rows['nameplate_power']['value'], 2000)
        self.assertEqual(rows['heat_to_space']['value'], 800)
        self.assertTrue(all(r['status'] == 'evidence_only' for r in rows.values()))

    def test_unlabelled_missing_unit_text_does_not_disappear(self):
        result = extract(packet(page(1, 'reflected_ceiling_plan', 'ceiling 3000')))
        row = result['candidates'][0]
        self.assertIsNone(row['value'])
        self.assertIn('unit', row['unresolved_fields'])
        self.assertIn('room_allocation', row['unresolved_fields'])

    def test_three_d_disagreement_does_not_block_primary_fact(self):
        data = packet(page(1, 'floor_plan', 'Studio A AREA: 20 m²'), page(2, '3d_reference'))
        vision = {'result': {'auto_extraction': {'entities': [{'kind': 'room', 'label': 'Studio A', 'page': 2, 'area_m2': 77, 'auto_activate': True}]}}}
        result = extract(data, vision_response=vision)
        primary = next(r for r in result['candidates'] if r['source']['page'] == 1)
        self.assertEqual(primary['status'], 'active')
        self.assertFalse(primary['competing_candidates'])

    def test_raw_table_cells_normalize_only_with_explicit_field_and_unit(self):
        data = packet(page(1, 'schedule'))
        ocr = {'pages': [{'page': 1, 'table_cells': [record('outside_air', 360, 'm³/h', table_id='T', row='Studio A', column='OA'),
            {'table_id': 'T', 'row': 'Studio B', 'column': 'OA', 'text': '360'}]}]}
        result = extract(data, spatial_ocr=ocr)
        self.assertEqual(len(result['candidates']), 1)
        self.assertEqual(result['candidates'][0]['value'], 100)
        self.assertEqual(len([r for r in result['binding']['observations'] if r['type'] == 'table_cell']), 2)

    def test_notes_retain_explicit_room_hours_and_references_without_u_value(self):
        data = packet(page(1, 'general_notes', 'Room Studio A: weekdays 08:00-17:00; construction: EW7; glazing reference: GL7'))
        rows = extract(data)['candidates']
        self.assertEqual({r['category'] for r in rows}, {'schedule', 'construction', 'glazing_reference'})
        self.assertTrue(all(r['room_id'] == 'Studio A' for r in rows))

    def test_creation_timestamp_is_not_part_of_fingerprint(self):
        data = packet(page(1, 'floor_plan', records=[record('area', 20, 'm²')]))
        with patch('ai.calculation_extraction.timestamp', return_value='first'):
            first = extract(data)
        with patch('ai.calculation_extraction.timestamp', return_value='later'):
            second = extract(data)
        self.assertEqual(first['fingerprint'], second['fingerprint'])

    def test_does_not_write_any_artifact_or_mutate_input(self):
        data = packet(page(1, 'floor_plan', records=[record('area', 20, 'm²')]))
        before = deepcopy(data)
        with patch('builtins.open', side_effect=AssertionError('Extraction must not access files')):
            extract(data)
        self.assertEqual(data, before)


if __name__ == '__main__':
    unittest.main()
