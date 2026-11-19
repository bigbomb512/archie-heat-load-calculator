"""Per-page work run in worker processes gives exactly what the one-page-at-a-time loop gave."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from pdf_pipeline import page_pool


def square(value):
    return value * value


def fail_on_three(value):
    if value == 3:
        raise ValueError("page 3 could not be read")
    return value


def page_words(pdf_page, item):
    return {"page": item["page"], "words": [word["text"] for word in pdf_page.extract_words()],
            "width": float(pdf_page.width), "process": os.getpid()}


def write_text_pdf(path, texts):
    """A minimal PDF with one page per text, written by hand so the test needs no PDF library."""
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", None,
               "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for text in texts:
        stream = f"BT /F1 24 Tf 72 700 Td ({text}) Tj ET"
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                       f"/Resources << /Font << /F1 3 0 R >> >> /Contents {len(objects)} 0 R >>")
        kids.append(f"{len(objects)} 0 R")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(texts)} >>"
    body, offsets = b"%PDF-1.4\n", []
    for number, value in enumerate(objects, 1):
        offsets.append(len(body))
        body += f"{number} 0 obj\n{value}\nendobj\n".encode("latin-1")
    xref = len(body)
    body += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    body += "".join(f"{offset:010d} 00000 n \n" for offset in offsets).encode()
    body += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    Path(path).write_bytes(body)


class PagePoolTests(unittest.TestCase):
    def test_worker_processes_keep_item_order(self):
        with patch.dict(os.environ, {"ARCHIE_PAGE_WORKERS": "3"}):
            self.assertEqual(page_pool.map_items(square, list(range(12))), [value * value for value in range(12)])

    def test_one_worker_runs_in_this_process_without_a_pool(self):
        with patch.dict(os.environ, {"ARCHIE_PAGE_WORKERS": "1"}), patch.object(page_pool, "_pool") as pool:
            self.assertEqual(page_pool.map_items(square, [2, 3]), [4, 9])
        pool.assert_not_called()

    def test_a_single_page_never_starts_a_pool(self):
        with patch.dict(os.environ, {"ARCHIE_PAGE_WORKERS": "4"}), patch.object(page_pool, "_pool") as pool:
            self.assertEqual(page_pool.map_items(square, [5]), [25])
        pool.assert_not_called()

    def test_page_errors_reach_the_caller_unchanged(self):
        for workers in ("1", "3"):
            with self.subTest(workers=workers), patch.dict(os.environ, {"ARCHIE_PAGE_WORKERS": workers}):
                with self.assertRaisesRegex(ValueError, "page 3 could not be read"):
                    page_pool.map_items(fail_on_three, [1, 2, 3, 4])

    def test_worker_count_respects_the_setting_and_the_page_count(self):
        with patch.dict(os.environ, {"ARCHIE_PAGE_WORKERS": "4"}):
            self.assertEqual((page_pool.worker_count(2), page_pool.worker_count(10)), (2, 4))
        with patch.dict(os.environ, {"ARCHIE_PAGE_WORKERS": "not a number"}):
            self.assertEqual(page_pool.worker_count(10), 1)
        with patch.dict(os.environ, {"ARCHIE_PAGE_WORKERS": ""}):
            self.assertLessEqual(page_pool.worker_count(50), page_pool.DEFAULT_MAX_WORKERS)

    def test_pdf_pages_read_in_workers_match_the_sequential_reading(self):
        with TemporaryDirectory() as folder:
            source = Path(folder) / "plans.pdf"
            write_text_pdf(source, ["GROUND", "FIRST", "ROOF"])
            items = [{"page": 3}, {"page": 1}, {"page": 2}]
            with patch.dict(os.environ, {"ARCHIE_PAGE_WORKERS": "1"}):
                sequential = page_pool.map_pdf_pages(page_words, source, items)
            with patch.dict(os.environ, {"ARCHIE_PAGE_WORKERS": "3"}):
                parallel = page_pool.map_pdf_pages(page_words, source, items)
        strip = lambda rows: [{key: value for key, value in row.items() if key != "process"} for row in rows]
        self.assertEqual(strip(sequential), [{"page": 3, "words": ["ROOF"], "width": 612.0},
                                             {"page": 1, "words": ["GROUND"], "width": 612.0},
                                             {"page": 2, "words": ["FIRST"], "width": 612.0}])
        self.assertEqual(strip(parallel), strip(sequential))
        self.assertTrue(all(row["process"] != os.getpid() for row in parallel))


if __name__ == "__main__":
    unittest.main()
