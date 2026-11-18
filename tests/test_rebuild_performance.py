"""Performance regressions in the model rebuild, checked by counting work rather than timing it.

One Assemble of a real house job read the same PDF pages, fingerprinted the same 90 MB vector geometry and
canonicalised the same evidence many times over. These pin the "once per saved version" behaviour, and that each
shortcut gives exactly the value the long way gives.
"""

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from ai import geometry_resolution, reviewer_room_geometry
from backend import ai_preliminary_service, pdf_page_text_cache, reviewer_room_geometry_service


class _Page:
    def __init__(self, words):
        self.width, self.height, self._words, self.reads = 1683.78, 1190.55, words, 0

    def extract_words(self):
        self.reads += 1
        return [{"text": text, "x0": x, "top": y, "x1": x + 9.0, "bottom": y + 13.6} for text, x, y in self._words]


class _Document:
    def __init__(self, pages):
        self.pages = pages


def _vector_pages(count, points=400):
    return [{"page": page, "coordinate_systems": {"image_px": {"image_width": 7016, "image_height": 4961}},
             "lines": [[page + index * 0.5, index * 1.25, index, page] for index in range(points)]}
            for page in range(1, count + 1)]


class PlanRegistrationReadsEachPdfPageOnce(unittest.TestCase):
    def setUp(self):
        pdf_page_text_cache.clear_memory()
        self.addCleanup(pdf_page_text_cache.clear_memory)

    def test_registering_level_plans_opens_the_pdf_once_and_parses_each_page_once(self):
        grid = [("A", 380.0, 74.0), ("B", 505.0, 74.0), ("C", 630.0, 74.0), ("1", 300.0, 200.0), ("2", 300.0, 330.0),
                ("3600", 440.0, 90.0), ("4200", 570.0, 90.0)]
        pages = [_Page(grid), _Page([(text, x + 12.0, y - 5.0) for text, x, y in grid]), _Page(grid)]
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "set.pdf"
            source.write_bytes(b"%PDF-1.7 fixture")
            (root / "ai_input.json").write_text(json.dumps({"source_pdf": str(source)}), encoding="utf-8")
            (root / "vector_geometry.json").write_text(json.dumps({"geometry_key_points": {"pages": _vector_pages(3, 4)}}),
                                                       encoding="utf-8")
            paths = {"root": root}
            with patch("pdfplumber.open", return_value=_Document(pages)) as open_pdf:
                results = [ai_preliminary_service._plan_text_registration(paths, upper, lower, 5.0)
                           for upper, lower in ((2, 1), (3, 2), (2, 1), (3, 1), (2, 3))]
            self.assertEqual(open_pdf.call_count, 1)
            self.assertEqual([page.reads for page in pages], [1, 1, 1])
            # A later rebuild in a new process takes the words from the job folder without opening the PDF.
            pdf_page_text_cache.clear_memory()
            with patch("pdfplumber.open", side_effect=AssertionError("PDF reopened")):
                again = [ai_preliminary_service._plan_text_registration(paths, upper, lower, 5.0)
                         for upper, lower in ((2, 1), (3, 2))]
        self.assertEqual([row and row["evidence"] for row in again], [row and row["evidence"] for row in results[:2]])


class CurrentTracesFingerprintTheVectorGeometryOncePerVersion(unittest.TestCase):
    def test_page_fingerprints_are_worked_out_once_per_saved_file_and_match_active_records(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "vector_geometry.json"
            pages = _vector_pages(5)
            path.write_text(json.dumps({"geometry_key_points": {"pages": pages}}), encoding="utf-8")
            paths = reviewer_room_geometry_service._paths({"review_dir": str(root)})
            records = [{"page": page, "source_fingerprints": {"source_pdf": "pdf", "vector_page": reviewer_room_geometry.fingerprint(row)}}
                       for page, row in ((row["page"], row) for row in pages)] + [{"page": 9, "source_fingerprints": {
                           "source_pdf": "pdf", "vector_page": reviewer_room_geometry.fingerprint({})}}]
            original = reviewer_room_geometry.vector_page_fingerprints
            with patch.object(reviewer_room_geometry, "vector_page_fingerprints", side_effect=original) as worked_out:
                for _ in range(14):                      # current_records runs about this often in one Assemble
                    fingerprints = reviewer_room_geometry_service._vector_page_fingerprints(paths)
                self.assertEqual(worked_out.call_count, 1)
                fast = reviewer_room_geometry.active_records({"records": records}, "pdf", None, page_fingerprints=fingerprints)
                self.assertEqual(fast, reviewer_room_geometry.active_records({"records": records}, "pdf", pages))
                self.assertEqual(len(fast), 6)
                before = worked_out.call_count
                pages[2]["lines"].append([0, 0, 1, 1])            # the geometry is saved again
                path.write_text(json.dumps({"geometry_key_points": {"pages": pages}}), encoding="utf-8")
                changed = reviewer_room_geometry_service._vector_page_fingerprints(paths)
                reviewer_room_geometry_service._vector_page_fingerprints(paths)
                self.assertEqual(worked_out.call_count, before + 1)
            self.assertNotEqual(changed[3], fingerprints[3])
            self.assertEqual(changed[1], fingerprints[1])


class GeometryEvidenceIsCanonicalisedOncePerValue(unittest.TestCase):
    def test_repeated_evidence_fingerprints_reuse_the_canonical_text_and_keep_their_values(self):
        geometry_resolution._CANONICAL_MEMO.clear()
        vector = {"geometry_key_points": {"pages": _vector_pages(6, 600)}}
        spatial = {"pages": [{"page": 1, "standalone_text_items": [{"text": "LIVING", "bbox": [1.5, 2.0, 3.0, 4.25]}]}]}

        def long_way(evidence_inputs):
            names = ("ai_input", "coverage", "spatial_ocr", "vector_geometry", "dimension_matches",
                     "geometry_confirmation", "vision_response", "building", "reviewer_room_geometry")
            evidence = {name: value or {} for name, value in zip(names, evidence_inputs)}
            return hashlib.sha256(geometry_resolution._canonical_json(evidence).encode()).hexdigest()

        original = geometry_resolution._canonical_json
        values = []
        with patch.object(geometry_resolution, "_canonical_json", side_effect=original) as canonical:
            for run in range(3):                   # three geometry builds in one Assemble, from fresh file reads
                inputs = ({"source_pdf": "set.pdf", "run": run}, {}, json.loads(json.dumps(spatial)),
                          json.loads(json.dumps(vector)), {}, {}, {}, {"spaces": []}, None)
                values.append((geometry_resolution._evidence_fingerprint(*inputs), long_way(inputs)))
            top_level = sum(1 for call in canonical.call_args_list if call.args[0] is not None
                            and isinstance(call.args[0], dict) and "geometry_key_points" in call.args[0])
        self.assertEqual(top_level, 1 + 3)         # once by the memo, three times by the long way
        self.assertTrue(all(fast == slow for fast, slow in values))
        self.assertEqual(len({fast for fast, _slow in values}), 3)   # ai_input differs each run


if __name__ == "__main__":
    unittest.main()
