"""A room switched to "not cooled" on the quick-fix screen gets no wall, window or roof drawing work (2026-11-23)."""
import json
import tempfile
import unittest
from pathlib import Path

from backend import autonomous_tasks_service


class UncooledRoomsTests(unittest.TestCase):
    def test_quick_fix_cooled_choices_change_the_rooms_worked_on(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "room_use_resolution.json").write_text(json.dumps({"records": [
                {"room_id": "living", "space_scope": "comfort_hvac"},
                {"room_id": "driveway", "space_scope": "comfort_hvac"},
                {"room_id": "garage", "space_scope": "not_cooled"}]}))
            self.assertEqual(autonomous_tasks_service._comfort_room_ids(root), {"living", "driveway"})
            (root / "quick_fixes.json").write_text(json.dumps({"rooms": {
                "driveway": {"label": "Driveway", "cooled": False},
                "garage": {"label": "Garage", "cooled": True},
                "living": {"label": "Living", "name": "Lounge"}}, "windows": {}}))
            self.assertEqual(autonomous_tasks_service._comfort_room_ids(root), {"living", "garage"})


if __name__ == "__main__":
    unittest.main()
