"""The packet's second build reuses the page screenshots of the first instead of rendering them again."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from ai import chatgpt_packet


def fake_render(calls):
    def render(ai_input, image, target, dpi):
        calls.append(image["page"])
        target.write_bytes(f"page {image['page']} at {dpi}".encode())
        return chatgpt_packet.render_status(True, "rendered_high_res", "high_res", "")
    return render


class PacketPageImageTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "plans.pdf").write_bytes(b"%PDF-1.4 stand-in")
        self.ai_input_path = self.root / "ai_input.json"
        self.write_ai_input([1, 2, 3])

    def tearDown(self):
        self.temp.cleanup()

    def write_ai_input(self, pages):
        self.ai_input_path.write_text(json.dumps({
            "source_pdf": str(self.root / "plans.pdf"),
            "source_files": {"page_images": [{"page": page, "type": "floor_plan", "path": f"thumb_{page}.png"}
                                             for page in (1, 2, 3)]},
            "confirmed_pages": {"floor_plans": [{"page": page} for page in pages]},
        }), encoding="utf-8")

    def build(self, calls, **options):
        with patch.object(chatgpt_packet, "render_high_res_page", side_effect=fake_render(calls)):
            return chatgpt_packet.create_chatgpt_packet(self.ai_input_path, zip_packet=False, **options)

    def test_second_build_reuses_the_first_builds_screenshots(self):
        first_calls, second_calls = [], []
        first = self.build(first_calls, return_page_images=True)
        rows = first.pop("page_images")
        second = self.build(second_calls, page_images=rows)
        self.assertEqual(sorted(first_calls), [1, 2, 3])
        self.assertEqual(second_calls, [])
        self.assertEqual(second["screenshots"], first["screenshots"])
        self.assertNotIn("page_images", second)
        self.assertEqual([Path(path).read_bytes() for path in second["screenshots"]],
                         [f"page {page} at {chatgpt_packet.DEFAULT_DPI}".encode() for page in (1, 2, 3)])

    def test_screenshots_are_rendered_again_when_the_pages_changed(self):
        rows = self.build([], return_page_images=True)["page_images"]
        self.write_ai_input([1, 3])
        calls = []
        rebuilt = self.build(calls, page_images=rows)
        self.assertEqual(sorted(calls), [1, 3])
        self.assertEqual([Path(path).name for path in rebuilt["screenshots"]],
                         ["page_001_floor_plan.png", "page_003_floor_plan.png"])

    def test_screenshots_are_rendered_again_when_a_file_is_missing_or_the_dpi_differs(self):
        rows = self.build([], return_page_images=True)["page_images"]
        Path(rows[1]["packet_path"]).unlink()
        calls = []
        self.build(calls, page_images=rows)
        self.assertEqual(sorted(calls), [1, 2, 3])
        rows = self.build([], return_page_images=True)["page_images"]
        calls = []
        self.build(calls, page_images=rows, dpi=chatgpt_packet.DEFAULT_DPI + 20)
        self.assertEqual(sorted(calls), [1, 2, 3])

    def test_parallel_rendering_keeps_page_order(self):
        rows = self.build([], return_page_images=True)["page_images"]
        self.assertEqual([row["page"] for row in rows], [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
