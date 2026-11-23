"""Certificate text fixtures, with no AI or PDF service calls."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai import certificate_constructions, stored_heat_rooms
from backend import ai_preliminary_service, assumption_range_service

TEXT = """BASIX CERTIFICATE
WALLS
Ground
Brick Veneer
1st Floor
Brick Veneer
Insulation External Walls
R2.5
Internal Walls to be Insulated
Bathroom
CEILINGS
Ceilings with cavity to roof
R4.0
ROOF AREA
Colours
Not yet selected
Material
Tiles
Insulation
Sarking
WINDOWS
"""


class CertificateConstructionTests(unittest.TestCase):
    def test_parses_only_printed_values(self):
        value = certificate_constructions.parse_page(TEXT, 2)
        self.assertEqual(value['wall']['insulation_r'], 2.5)
        self.assertEqual(value['ceiling']['insulation_r'], 4)
        self.assertEqual(value['roof_material'], 'Tiles')
        self.assertNotIn('roof', value)
        self.assertNotIn('north', value)
        self.assertNotIn('wall', certificate_constructions.parse_page(TEXT.replace('R2.5', 'not specified'), 2))
        self.assertNotIn('ceiling', certificate_constructions.parse_page(TEXT.replace('R4.0', 'Sarking'), 2))
        self.assertNotIn('north', certificate_constructions.parse_page('NORTH\nNE window', 2))
        self.assertEqual(certificate_constructions.parse_page('Plan-up azimuth: 45 degrees', 2)['north']['plan_up_azimuth_deg'], 45)
        self.assertNotIn('north', certificate_constructions.parse_page('Plan-up azimuth: 360 degrees', 2))

    def test_certificate_beats_pack_but_typed_answer_wins_and_brick_mass_reaches_model(self):
        value = certificate_constructions.parse_page(TEXT, 2)
        use = {'dwelling_context': {'is_dwelling': True}}
        proposal = {'surfaces': [{'physical_type': 'wall', 'owner_room_label': 'Living', 'u_value_w_m2k': 1.8},
                                 {'physical_type': 'roof', 'owner_room_label': 'Living', 'u_value_w_m2k': 1.9}]}
        with patch.object(certificate_constructions, 'read_job', return_value=[value]):
            out = ai_preliminary_service._with_certificate_constructions({'root': Path('.')}, proposal, use)
        wall, roof = out['surfaces']
        self.assertEqual(wall['u_value_w_m2k'], certificate_constructions.u_value('wall', 'Brick Veneer', 2.5))
        self.assertIn('p2: Brick Veneer; Insulation External Walls R2.5', wall['construction_source'])
        self.assertIn('Tiles', roof['construction_source'])
        self.assertEqual(roof['u_value_w_m2k'], certificate_constructions.u_value('roof', 'Tiles', 0, 4))
        cfg = json.loads((Path(__file__).parents[1] / 'config/stored_heat.json').read_text())
        self.assertEqual(stored_heat_rooms._choose(cfg, {'kind': 'opaque_wall', 'boundary_method': 'external', **wall}, True)[0], 'wall_brick_veneer')
        wall.update(u_value_w_m2k=.3, construction_source='Answered by the operators (typed): brick')
        with patch.object(certificate_constructions, 'read_job', return_value=[value]):
            ai_preliminary_service._with_certificate_constructions({'root': Path('.')}, out, use)
        self.assertEqual(wall['u_value_w_m2k'], .3)

    def test_unknowns_accept_certificate_values(self):
        value = certificate_constructions.parse_page(TEXT + '\nPlan-up azimuth: 45 degrees', 2)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'ai_preliminary_input_set.json').write_text(json.dumps({'material': {'hourly_load_model': {'rooms': [
                {'cooling_load': {'envelope_surfaces': [{'kind': 'opaque_wall', 'boundary_method': 'external'}, {'kind': 'roof'}]}}]}}}))
            with patch.object(certificate_constructions, 'read_job', return_value=[value]), patch.object(assumption_range_service, 'north_origin', return_value='ai_estimate'):
                rows = {row['key']: row for row in assumption_range_service.open_unknowns(root, {})}
            for key in ('wall', 'roof', 'north'):
                self.assertTrue(rows[key]['answered'])
                self.assertFalse(rows[key]['open'])


if __name__ == '__main__':
    unittest.main()
