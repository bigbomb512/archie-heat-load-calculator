"""Small saved-text fixtures for room labels split into PDF glyph runs."""
import unittest
from pathlib import Path
from unittest.mock import patch

from ai import room_inference
from backend import autonomous_tasks_service as tasks


class HouseRoomTextRecovery(unittest.TestCase):
    def test_split_glyph_room_labels_keep_bedroom_number(self):
        expected = {'BE D R OOM 2': 'Bedroom 2', 'W IR': 'Wir',
                    'E NSU ITE': 'Ensuite', 'S T U D Y N OOK': 'Study nook',
                    'WAL K I N R O B E': 'Walk in robe'}
        for printed, name in expected.items():
            with self.subTest(printed=printed):
                self.assertEqual(room_inference.parse_room_label_item(printed)[0], name)
        for note in ('BEDROOM 2 NOTE', 'DINING KITCHEN', '2 BEDROOM'):
            with self.subTest(note=note):
                self.assertIsNone(room_inference.parse_room_label_item(note)[0])
        self.assertIn('study nook', room_inference.HOUSE_ONLY_TERMS)

    def test_text_line_bbox_is_local_to_label(self):
        items = [{'text': 'BED', 'bbox': [10, 10, 25, 20]},
                 {'text': 'ROOM', 'bbox': [26, 10, 50, 20]},
                 {'text': 'DINING', 'bbox': [100, 12, 140, 22]}]
        lines = room_inference._text_item_lines(items)
        self.assertEqual(lines[0], ('BED ROOM', [10, 10, 50, 20]))
        self.assertEqual(lines[1], ('DINING', [100, 12, 140, 22]))

    def test_house_plan_names_add_text_layer_names_only_for_dwelling(self):
        spatial = {'pages': [{'page': 9, 'width': 100, 'height': 100,
                              'standalone_text_items': [
                                  {'text': 'BE', 'bbox': [10, 10, 20, 20]},
                                  {'text': 'D', 'bbox': [21, 10, 25, 20]},
                                  {'text': 'R', 'bbox': [26, 10, 30, 20]},
                                  {'text': 'OOM', 'bbox': [31, 10, 48, 20]},
                                  {'text': '2', 'bbox': [49, 10, 53, 20]}]}]}
        with patch.object(tasks, '_inferred_rooms_for_plan', return_value=[]), \
             patch.object(tasks, '_reading_room_names', return_value=[]), \
             patch.object(tasks, '_read', return_value=spatial), \
             patch('backend.house_windows_service.is_dwelling', return_value=True):
            self.assertIn('Bedroom 2', tasks._p0_plan_names(Path('/tmp/example'), 9))
        with patch.object(tasks, '_inferred_rooms_for_plan', return_value=[]), \
             patch('backend.house_windows_service.is_dwelling', return_value=False):
            self.assertEqual(tasks._p0_plan_names(Path('/tmp/example'), 9), set())

    def test_fallback_outlines_include_names_found_after_saved_inference(self):
        inferred = [{'label': 'Bedroom 3', 'page': 9, 'area_m2': None}]
        with patch.object(tasks, '_p0_plan_names', return_value={'Bedroom 2', 'Bedroom 3'}):
            names, missing = tasks._p0_outline_room_names(
                Path('/tmp/example'), None, 0, inferred_rooms=inferred, dwelling=True)
        self.assertEqual(names, ['Bedroom 2', 'Bedroom 3'])
        self.assertEqual(missing, ['Bedroom 2', 'Bedroom 3'])


if __name__ == '__main__':
    unittest.main()
