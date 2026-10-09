#!/usr/bin/env python3
"""Pass 1 of the PDF review: page readings, page roles from them, and the server job (with a stand-in AI)."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from ai import page_inventory as inventory
from ai.page_role_evaluation import score
from backend import page_inventory_service as service


def reading(page_type, kind="", whole=True, level="", info=()):
    return inventory.validate_reply({"page_type": page_type, "floor_plan_kind": kind, "whole_floor": whole, "title": "",
                                     "level": level, "information": [{"kind": k, "what": w, "evidence": ""} for k, w in info]})


class ReplyTests(unittest.TestCase):
    def test_a_good_reply_is_cleaned(self):
        row = inventory.validate_reply({"page_type": "Floor_Plan", "floor_plan_kind": "proposed_layout", "whole_floor": True,
                                        "title": "  PROPOSED   FLOOR PLAN ", "drawing_number": "A-101", "level": "Ground",
                                        "information": [{"kind": "equipment_appliances", "what": "75 inch TV", "evidence": "wall elevation"}]})
        self.assertEqual((row["page_type"], row["title"], row["information"][0]["kind"]),
                         ("floor_plan", "PROPOSED FLOOR PLAN", "equipment_appliances"))
        elevation = inventory.validate_reply({"page_type": "elevation", "floor_plan_kind": "proposed_layout", "whole_floor": True})
        self.assertEqual((elevation["floor_plan_kind"], elevation["whole_floor"], elevation["information"]), ("", False, []))

    def test_replies_outside_the_vocabulary_are_refused(self):
        for bad in ([], {"page_type": "plan"}, {"page_type": "floor_plan", "floor_plan_kind": "big"},
                    {"page_type": "schedule", "information": {}}, {"page_type": "schedule", "information": ["x"]},
                    {"page_type": "schedule", "information": [{"kind": "colour", "what": "red"}]},
                    {"page_type": "schedule", "information": [{"kind": "lighting", "what": " "}]}):
            with self.assertRaises(ValueError):
                inventory.validate_reply(bad)

    def test_level_names_group_together(self):
        self.assertEqual({inventory.level_key(name) for name in ("Ground Floor", "GF", "ground", "")}, {"ground", ""})
        self.assertEqual(inventory.level_key("Ground Floor"), inventory.level_key("GF"))
        self.assertEqual(inventory.level_key("Level 01"), inventory.level_key("L1"))
        self.assertEqual(inventory.level_key("Mezzanine Level"), "mezzanine")


class PageRoleTests(unittest.TestCase):
    def test_one_or_more_main_plans_per_level_and_existing_plans_only_without_a_proposed_one(self):
        readings = {1: reading("floor_plan", "existing_or_demolition", level="Ground"),
                    2: reading("floor_plan", "proposed_layout", level="Ground Floor"),
                    3: reading("floor_plan", "dimension_setout", level="GF"),
                    4: reading("floor_plan", "proposed_layout", level="Mezzanine"),
                    5: reading("floor_plan", "partial_or_enlarged", whole=False, level="Ground"),
                    6: reading("floor_plan", "proposed_layout", whole=False)}
        self.assertEqual(inventory.main_plan_pages(readings), {"ground": [2, 3], "mezzanine": [4]})
        existing_only = {1: reading("floor_plan", "existing_or_demolition"), 2: reading("floor_plan", "furniture_or_equipment")}
        self.assertEqual(inventory.main_plan_pages(existing_only), {"": [1]})

    def test_pages_with_information_are_never_thrown_away_and_unread_pages_are_kept(self):
        readings = {1: reading("cover_or_drawing_list", info=[("location_orientation", "Melrose Central")]),
                    2: reading("render_or_photo"), 3: reading("render_or_photo", info=[("equipment_appliances", "TV")]),
                    4: reading("floor_plan", "proposed_layout"), 5: reading("reflected_ceiling_plan"), 6: None}
        packet = inventory.packet_from_readings(readings, 6)
        sheet = {"case_id": "c", "pages": {"geometry": [4], "rcp": [5], "must_keep": [1, 3, 6], "not_geometry": [1, 2]}}
        report = score(sheet, packet)
        self.assertEqual(report["totals"]["passed"], report["totals"]["facts"], report["facts"])
        self.assertEqual([row["page"] for row in packet["discarded_pages"]], [2])


class FakeReader:
    model = "fake"

    def __init__(self, replies, gate=None):
        self.replies, self.calls, self.gate = replies, [], gate

    def propose(self, prompt, image_paths=()):
        pages = [int(Path(path).stem.split("-")[1]) for path in image_paths]
        self.calls.extend(pages)
        self.batches = getattr(self, "batches", []) + [pages]
        if self.gate:
            self.gate.wait(5)
        if len(pages) > 1:
            # Several pages in one call: a page that would fail is left out of the reply (it's then read on its own).
            return {"pages": {str(page): self.replies[page] for page in pages
                              if not isinstance(self.replies[page], Exception)}}, {"provider": "fake"}
        reply = self.replies[pages[0]]
        if isinstance(reply, Exception):
            raise reply
        return reply, {"provider": "fake"}


def wait(project, seconds=10):
    deadline = time.monotonic() + seconds
    while service._JOB.key(project) in service._RUNNING and time.monotonic() < deadline:
        time.sleep(0.02)
    return service.status(None, project)


@unittest.skipUnless(shutil.which("pdftoppm"), "needs pdftoppm")
class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.pdf = root / "set.pdf"
        # A three-page PDF made with no extra tools: blank pages of different sizes (identical images share a reading).
        pages = "".join(f"{3 + i} 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 {200 + 40 * i} 100]>>endobj\n" for i in range(3))
        body = (f"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
                f"2 0 obj<</Type/Pages/Kids[3 0 R 4 0 R 5 0 R]/Count 3>>endobj\n{pages}trailer<</Root 1 0 R>>\n%%EOF\n")
        self.pdf.write_text(body)
        (root / "job").mkdir()
        self.project = {"id": "job", "pdf": str(self.pdf), "review_dir": str(root / "job")}
        self.plan = {"page_type": "floor_plan", "floor_plan_kind": "proposed_layout", "whole_floor": True,
                     "information": [{"kind": "room_geometry", "what": "Shop 120 m2", "evidence": "label"}]}
        self.schedule = {"page_type": "schedule", "information": [{"kind": "materials_construction", "what": "Glass 6.38 mm", "evidence": "row 3"}]}

    def tearDown(self):
        service._RUNNING.clear()
        service.PROVIDER_FACTORY = None
        self.temp.cleanup()

    def test_every_page_is_read_saved_and_a_repeat_run_calls_the_ai_for_nothing(self):
        reader = FakeReader({1: self.plan, 2: self.schedule, 3: {"page_type": "render_or_photo"}})
        service.PROVIDER_FACTORY = lambda: reader
        service.start(None, self.project, {"requested_by": "Sam"})
        done = wait(self.project)
        self.assertEqual((done["status"], done["read"], done["failed"], done["pages_done"]), ("done", 3, 0, 3))
        self.assertEqual(sorted(reader.calls), [1, 2, 3])
        self.assertEqual(done["main_plans"], {"": [1]})
        self.assertEqual(done["pages"][1]["information"][0]["what"], "Glass 6.38 mm")
        saved = json.loads((Path(self.project["review_dir"]) / service.RESULT_FILE).read_text())
        self.assertEqual(saved["prompt_version"], inventory.PROMPT_VERSION)
        self.assertEqual([row["page"] for row in saved["packet"]["discarded_pages"]], [3])
        service.start(None, self.project)
        again = wait(self.project)
        self.assertEqual((again["status"], again["calls"]), ("done", 0))
        self.assertEqual(sorted(reader.calls), [1, 2, 3])

    def test_a_failed_page_fails_the_run_keeps_the_others_and_retry_reads_only_it(self):
        reader = FakeReader({1: self.plan, 2: RuntimeError("Codex CLI failed (exit 1): usage limit reached"), 3: {"page_type": "bad"}})
        service.PROVIDER_FACTORY = lambda: reader
        service.start(None, self.project)
        failed = wait(self.project)
        self.assertEqual((failed["status"], failed["read"], failed["failed"]), ("failed", 1, 2))
        self.assertIn("2 of 3 pages couldn't be read (pages 2, 3)", failed["problem"])
        self.assertIn("usage limit reached", failed["problem"])
        self.assertEqual([row["status"] for row in failed["pages"]], ["read", "failed", "failed"])
        reader.replies.update({2: self.schedule, 3: {"page_type": "render_or_photo"}})
        service.start(None, self.project, {"action": "retry"})
        done = wait(self.project)
        self.assertEqual((done["status"], done["read"]), ("done", 3))
        self.assertEqual(reader.calls.count(1), 1)                     # never read again
        self.assertEqual(reader.batches[-1], [2, 3])                    # the retry reads only the failed pages, together

    def test_pages_are_read_several_per_call_and_cached_one_by_one(self):
        reader = FakeReader({1: self.plan, 2: self.schedule, 3: {"page_type": "render_or_photo"}})
        service.PROVIDER_FACTORY = lambda: reader
        service.start(None, self.project)
        done = wait(self.project)
        self.assertEqual((done["status"], done["read"]), ("done", 3))
        self.assertEqual(reader.batches, [[1, 2, 3]])                   # one call for three pages
        cached = [json.loads(path.read_text()) for path in (Path(self.project["review_dir"]) / service.WORK_DIR / "replies").glob("*.json")]
        self.assertEqual(sorted(row["page"] for row in cached), [1, 2, 3])
        self.assertTrue(all(row["read_with_pages"] == [1, 2, 3] for row in cached))
        with patch.dict(os.environ, {"ARCHIE_PAGE_BATCH": "1"}):
            self.assertEqual(service.batch_size(), 1)
        with patch.dict(os.environ, {"ARCHIE_PAGE_BATCH": "x"}):
            self.assertEqual(service.batch_size(), 4)

    def test_a_second_start_joins_the_running_job(self):
        gate = threading.Event()
        reader = FakeReader({1: self.plan, 2: self.schedule, 3: self.plan}, gate)
        service.PROVIDER_FACTORY = lambda: reader
        first = service.start(None, self.project)
        second = service.start(None, self.project)
        self.assertTrue(second.get("deduplicated"))
        self.assertEqual(second["job_id"], first["job_id"])
        gate.set()
        self.assertEqual(wait(self.project)["status"], "done")
        self.assertEqual(sorted(reader.calls), [1, 2, 3])

    def test_switched_off_missing_cli_and_missing_pdf(self):
        service.set_enabled(None, self.project, {"enabled": False})
        self.assertFalse(service.status(None, self.project)["enabled"])
        with self.assertRaisesRegex(ValueError, "switched off"):
            service.start(None, self.project)
        self.assertIsNone(service.start_after_analysis(None, self.project))
        service.set_enabled(None, self.project, {"enabled": True})
        with patch.object(service.shutil, "which", return_value=None):
            blocked = service.start(None, self.project)
        self.assertEqual(blocked["status"], "blocked")
        self.assertIn("isn't installed", blocked["problem"])
        with patch.dict("os.environ", {"ARCHIE_PAGE_READING": "off"}):
            self.assertIsNone(service.start_after_analysis(None, self.project))
        with self.assertRaisesRegex(ValueError, "PDF isn't available"):
            service.start(None, {**self.project, "pdf": str(self.pdf) + ".missing"})

    def test_a_cli_that_isnt_signed_in_is_blocked(self):
        reader = service.CodexCliPageReader(executable="/bin/echo")
        with patch.object(service.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "Not logged in", "")):
            with self.assertRaisesRegex(service.PageReadingUnavailable, "isn't signed in"):
                reader.check_signed_in()


if __name__ == "__main__":
    unittest.main()


class UsageLimitTests(unittest.TestCase):
    def test_after_the_usage_limit_no_more_pages_are_sent_and_the_reason_says_when_to_retry(self):
        message = ("ERROR: You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), "
                   "visit https://chatgpt.com/settings/usage to purchase more credits or try again at 6:01 PM.")
        reason = service._usage_limit_message(message)
        self.assertIn("usage limit is reached; it resets at 6:01 PM", reason)
        self.assertEqual(service._usage_limit_message("Codex CLI failed: network down"), "")

        class LimitedReader:
            model, calls = "fake", []

            def propose(self, prompt, image_paths=()):
                self.calls.append(image_paths[0])
                raise service.UsageLimitReached(reason)

        with tempfile.TemporaryDirectory() as folder:
            images = {}
            for page in (1, 2, 3):
                images[page] = Path(folder) / f"p-{page}.png"
                images[page].write_bytes(bytes([page]))
            reader = LimitedReader()
            readings, failures, calls = service.read_pages(images, Path(folder) / "replies", reader, workers=1)
        self.assertEqual((readings, calls, len(reader.calls)), ({}, 1, 1))
        self.assertEqual(set(failures.values()), {reason})
