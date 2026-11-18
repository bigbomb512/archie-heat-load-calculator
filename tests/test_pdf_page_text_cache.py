"""The PDF page word cache parses each page once without changing the words or sizes it hands out."""

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from backend import pdf_page_text_cache, seating_service


class FakePage:
    def __init__(self, text, width=612.0, height=792.0):
        self.text, self.width, self.height = text, width, height
        self.reads = 0

    def extract_words(self):
        self.reads += 1
        return [{"text": self.text, "x0": 10.5, "top": 20.25, "x1": 30.0, "bottom": 40.125}]


class FakeDocument:
    def __init__(self, *pages):
        self.pages = list(pages) or [FakePage("A1"), FakePage("B2", 841.89, 595.28)]


class PdfPageTextCacheTests(unittest.TestCase):
    def setUp(self):
        pdf_page_text_cache.clear_memory()
        self.addCleanup(pdf_page_text_cache.clear_memory)
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.source = self.root / "drawing.pdf"
        self.source.write_bytes(b"stable pdf fixture")

    def test_words_and_sizes_match_seating_and_pdf_is_opened_and_parsed_once(self):
        document = FakeDocument()
        with patch("pdfplumber.open", return_value=document) as open_pdf:
            first = pdf_page_text_cache.page_words(self.root, self.source, 2)
            second = pdf_page_text_cache.PdfText(self.root, self.source).words(2)
            size = pdf_page_text_cache.PdfText(self.root, self.source).size(2)
            outside = pdf_page_text_cache.page_words(self.root, self.source, 3)
        self.assertEqual(first, (seating_service._words(FakePage("B2")), 841.89))
        self.assertEqual(second, first[0])
        self.assertEqual(size, (841.89, 595.28))
        self.assertIsNone(outside)
        self.assertEqual(open_pdf.call_count, 1)
        self.assertEqual(document.pages[1].reads, 1)
        second[0]["bbox"][0] = -1                                  # a caller changing its copy changes nothing here
        self.assertEqual(pdf_page_text_cache.PdfText(self.root, self.source).words(2), first[0])

    def test_a_new_process_reads_the_job_folder_cache_without_opening_the_pdf(self):
        with patch("pdfplumber.open", return_value=FakeDocument()):
            expected = pdf_page_text_cache.page_words(self.root, self.source, 1)
        pdf_page_text_cache.clear_memory()
        with patch("pdfplumber.open", side_effect=AssertionError("the PDF should not be reopened")):
            self.assertEqual(pdf_page_text_cache.page_words(self.root, self.source, 1), expected)
        # Kept out of the job-state signatures and the project export, which only take *.json files.
        self.assertEqual(list((self.root / "pdf_page_text_cache").rglob("*.json")), [])

    def test_corrupt_cache_is_rebuilt(self):
        with patch("pdfplumber.open", return_value=FakeDocument()):
            expected = pdf_page_text_cache.page_words(self.root, self.source, 1)
        cache = next((self.root / "pdf_page_text_cache").rglob("page_0001.words"))
        cache.write_text("{broken", encoding="utf-8")
        pdf_page_text_cache.clear_memory()
        with patch("pdfplumber.open", return_value=FakeDocument()) as open_pdf:
            self.assertEqual(pdf_page_text_cache.page_words(self.root, self.source, 1), expected)
        self.assertEqual(open_pdf.call_count, 1)
        self.assertEqual(json.loads(cache.read_text())["page"], 1)

    def test_a_replaced_pdf_is_read_again_and_the_old_cache_removed(self):
        with patch("pdfplumber.open", return_value=FakeDocument()):
            pdf_page_text_cache.page_words(self.root, self.source, 1)
        old = next((self.root / "pdf_page_text_cache").iterdir())
        self.source.write_bytes(b"revised pdf fixture")
        os.utime(self.source, ns=(1, 1))
        with patch("pdfplumber.open", return_value=FakeDocument(FakePage("C3"))) as open_pdf:
            words, _width = pdf_page_text_cache.page_words(self.root, self.source, 1)
        self.assertEqual(words[0]["text"], "C3")
        self.assertEqual(open_pdf.call_count, 1)
        self.assertFalse(old.exists())


if __name__ == "__main__":
    unittest.main()
