"""The read caches added for speed (2026-11-24) give the same answer as a fresh read, and re-read once a file changes."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ai import certificate_constructions
from backend import assumption_range_service


class ReadCacheTests(unittest.TestCase):
    def test_big_files_are_parsed_once_until_they_change(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "report.json"
            path.write_text(json.dumps({"kw": 1}))
            with mock.patch.object(assumption_range_service, "_read", wraps=assumption_range_service._read) as read:
                self.assertEqual(assumption_range_service._read_big(path), {"kw": 1})
                self.assertEqual(assumption_range_service._read_big(path), {"kw": 1})
                self.assertEqual(read.call_count, 1)
                path.write_text(json.dumps({"kw": 22}))
                os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000))
                self.assertEqual(assumption_range_service._read_big(path), {"kw": 22})
                self.assertEqual(read.call_count, 2)
            self.assertEqual(assumption_range_service._read_big(Path(folder) / "missing.json"), {})

    def test_certificate_reading_is_remembered_until_the_pdf_or_inventory_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pdf = root / "set.pdf"
            pdf.write_bytes(b"%PDF-1.4")
            (root / "spatial_ocr.json").write_text(json.dumps({"source_pdf": str(pdf)}))
            (root / "page_inventory.json").write_text(json.dumps({"pages": {}}))
            rows = [{"page": 2, "certificate": "BASIX", "wall": {"construction": "Brick Veneer", "insulation_r": 2.0}}]
            with mock.patch.object(certificate_constructions, "read_job_uncached", return_value=rows) as uncached:
                first = certificate_constructions.read_job(root)
                second = certificate_constructions.read_job(root)
                self.assertEqual(first, rows)
                self.assertEqual(second, rows)
                self.assertEqual(uncached.call_count, 1)
                second[0]["page"] = 99                      # a caller changing its copy doesn't change the cache
                self.assertEqual(certificate_constructions.read_job(root)[0]["page"], 2)
                pdf.write_bytes(b"%PDF-1.4 changed")
                self.assertEqual(certificate_constructions.read_job(root), rows)
                self.assertEqual(uncached.call_count, 2)


if __name__ == "__main__":
    unittest.main()
