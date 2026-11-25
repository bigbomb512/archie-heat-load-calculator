"""Synthetic coverage checks for the read-only room tracing evaluator."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import eval_room_tracing


class RoomTracingEvalTests(unittest.TestCase):
    def test_untraced_merged_mismatched_and_small_traces(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'spatial_ocr.json').write_text(json.dumps({'pages': [{'page': 1, 'width': 100,
                'height': 100, 'standalone_text_items': [
                    {'text': 'KITCHEN', 'bbox': [10, 10, 20, 20]},
                    {'text': 'DINING', 'bbox': [50, 10, 60, 20]},
                    {'text': 'STUDY', 'bbox': [90, 10, 100, 20]}]}]}))
            (root / 'vector_geometry.json').write_text(json.dumps({'geometry_key_points': {'pages': [
                {'page': 1, 'coordinate_systems': {'image_px': {'image_width': 100,
                                                               'image_height': 100}}}]}}))
            records = [{'trace_id': 'one', 'page': 1, 'room_label': 'Corridor',
                        'method': 'ai_corner_outline', 'points_image_px':
                        [[0, 0], [70, 0], [70, 30], [0, 30], [0, 0]],
                        'calibration': {'mm_per_px': 10}}]
            with patch.object(eval_room_tracing.tasks, '_p0_main_geometry_pages',
                              return_value=[{'page': 1, 'level_name': 'Ground'}]), \
                 patch.object(eval_room_tracing.tasks, '_p0_plan_names',
                              return_value={'Kitchen', 'Dining', 'Study'}), \
                 patch.object(eval_room_tracing.reviewer_room_geometry_service,
                              'current_records', return_value=records):
                report = eval_room_tracing.evaluate(root)
        self.assertEqual(report['summary']['untraced_names'], 1)
        self.assertEqual(report['summary']['merged_traces'], 1)
        self.assertEqual(report['summary']['name_mismatches'], 1)
        self.assertEqual(report['summary']['implausible_areas'], 1)
        self.assertEqual(report['pages'][0]['traces'][0]['method'], 'ai_corner_outline')


if __name__ == '__main__':
    unittest.main()
