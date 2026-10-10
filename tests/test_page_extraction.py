#!/usr/bin/env python3
"""Pass 2 of the PDF review: sections of large sheets, equipment readings, merging across pages, and the server job."""

import json
from pathlib import Path
import shutil
import tempfile
import time
import unittest

from ai import page_extraction as extraction
from backend import page_extraction_service as service, page_inventory_service as pass1

EQUIPMENT = extraction.EXTRACTORS["equipment_appliances"]


def item(name, **fields):
    return extraction.validate_equipment_item({"name": name, **fields})


class SectionTests(unittest.TestCase):
    def test_a_page_that_fits_is_one_image_and_a_large_sheet_is_cut_into_overlapping_sections(self):
        self.assertEqual(extraction.tiles(2481, 1754), [(0, 0, 2481, 1754)])           # A3 at 150 dpi
        sections = extraction.tiles(4967, 3508)                                         # A1 at 150 dpi
        self.assertEqual(len(sections), 6)                                               # 3 x 2, overlap included
        self.assertEqual((sections[0][0], sections[0][1], sections[-1][2], sections[-1][3]), (0, 0, 4967, 3508))
        self.assertLess(sections[1][0], sections[0][2])                                  # neighbours overlap
        self.assertTrue(all(x1 - x0 <= extraction.MAX_IMAGE_PX for x0, _y0, x1, _y1 in sections))


class EquipmentReadingTests(unittest.TestCase):
    def test_items_are_cleaned_and_bad_ones_refused(self):
        row = item("  Combi   oven ", code="E01", quantity="2", size="750W x 790D x 675H mm", rated_power="", under_hood=True)
        self.assertEqual((row["name"], row["quantity"], row["under_hood"]), ("Combi oven", 2, True))
        for bad in ({"name": ""}, {"name": "Oven", "quantity": -1}, {"name": "Oven", "quantity": 1.5},
                    {"name": "Oven", "under_hood": "yes"}, "oven"):
            with self.assertRaises(ValueError):
                extraction.validate_equipment_item(bad)
        with self.assertRaises(ValueError):
            extraction.validate_reply(EQUIPMENT, {"equipment": []})
        self.assertEqual(extraction.validate_reply(EQUIPMENT, {"items": []}), [])
        # A nameless item is dropped; it doesn't cost the page its other items. Other bad items still refuse the reply.
        kept = extraction.validate_reply(EQUIPMENT, {"items": [{"name": "", "quantity": 2}, {"name": "  "}, {"name": "Oven", "quantity": 1}]})
        self.assertEqual([row["name"] for row in kept], ["Oven"])
        with self.assertRaises(ValueError):
            extraction.validate_reply(EQUIPMENT, {"items": [{"name": "Oven", "quantity": -1}]})

    def test_the_prompt_names_the_page_or_section(self):
        self.assertIn("page 5 of", extraction.build_prompt(EQUIPMENT, 5))
        self.assertIn("coolroom", extraction.build_prompt(EQUIPMENT, 5))
        self.assertIn("section 2 of 4 of page 5", extraction.build_prompt(EQUIPMENT, 5, 1, 4))

    def test_identity_uses_the_code_else_name_and_size(self):
        self.assertEqual(extraction.equipment_identity(item("Combi oven", code="E-01")),
                         extraction.equipment_identity(item("COMBI OVEN 10 TRAY", code="e 01")))
        small, large = item("U/B fridge", size="1200 x 700"), item("Underbench fridge", size="1500x700")
        self.assertNotEqual(extraction.equipment_identity(small), extraction.equipment_identity(large))
        self.assertEqual(extraction.equipment_identity(small), extraction.equipment_identity(item("underbench fridge", size="1200W 700D")))


class ScheduleCodeTests(unittest.TestCase):
    def test_schedule_pages_are_found_by_type_or_title_and_their_codes_feed_the_plan_prompt(self):
        pass1_pages = {"5": {"page_type": "floor_plan", "title": "EQUIPMENT SCHEDULE"}, "6": {"page_type": "schedule", "title": ""},
                       "19": {"page_type": "floor_plan", "title": "PROPOSED FLOOR LAYOUT"}}
        self.assertEqual(service.schedule_pages([5, 6, 19], pass1_pages), [5, 6])
        codes = service.schedule_codes([(5, 0, [item("COMBI OVEN", code="E06"), item("Shelf")]), (6, 0, [item("Oven (dup)", code="E06"), item("UB FRIDGE", code="E21")])])
        self.assertEqual(codes, {"E06": "COMBI OVEN", "E21": "UB FRIDGE"})
        prompt = extraction.build_prompt(EQUIPMENT, 19, codes=codes)
        self.assertIn("lists these codes: E06 COMBI OVEN; E21 UB FRIDGE.", prompt)
        self.assertIn("Give no code for an item that matches none of them, or matches more than one.", prompt)
        self.assertEqual(extraction.build_prompt(EQUIPMENT, 19), extraction.build_prompt(EQUIPMENT, 19, codes={}))
        self.assertIn("lists these codes", extraction.batch_prompt(EQUIPMENT, [(19, 0, 1), (20, 0, 1)], codes))
        # A page read with the codes is cached apart from one read without them.
        image = Path(__file__)
        self.assertNotEqual(service._cache_key(image, EQUIPMENT, "m", codes), service._cache_key(image, EQUIPMENT, "m"))
        self.assertEqual(service._cache_key(image, EQUIPMENT, "m", None), service._cache_key(image, EQUIPMENT, "m"))


class DuplicateHintTests(unittest.TestCase):
    def test_an_uncoded_plan_label_points_at_the_one_or_two_scheduled_items_it_probably_is(self):
        def row(name, code="", size=""):
            return {"id": f"{code or name}:{size}", "value": {"code": code, "name": name, "size": size}}
        findings = [row("UB FRIDGE", "E02", "1200x700"), row("UB FRIDGE", "E21", "1500x700"), row("WOK BURNER", "E27"),
                    row("2 DOOR FRIDGE", "E05"), row("DRINK FRIDGE", "E13"), row("DISPLAY FRIDGE", "E31"),
                    row("UB FR 1500", size="1500"),          # abbreviation and size -> E21 only
                    row("WOK"),                               # -> E27
                    row("UB FRIDGE"),                         # two sizes scheduled -> both offered
                    row("FRIDGE"),                            # four fridges -> too vague, no hint
                    row("Heat lamp")]                         # nothing scheduled
        hints = {key: [match["value"]["code"] for match in value] for key, value in extraction.possible_duplicates(findings).items()}
        self.assertEqual(hints, {"UB FR 1500:1500": ["E21"], "WOK:": ["E27"], "UB FRIDGE:": ["E02", "E21"]})

class MergeTests(unittest.TestCase):
    def test_a_plan_label_without_a_code_folds_into_the_one_scheduled_item_of_that_name(self):
        readings = [
            (5, 0, [item("COMBI OVEN", code="E06", quantity=2, evidence="schedule E06"),
                    item("UB FRIDGE", code="E21", size="1200"), item("UB FRIDGE", code="E22", size="1500")]),
            (19, 0, [item("Combi oven", quantity=1, evidence="plan label"),     # same item, labelled on the plan
                     item("UB fridge"),                                       # two scheduled fridges: ambiguous
                     item("Wok burner")]),                                   # nothing scheduled by that name
        ]
        findings = {row["identity"]: row for row in extraction.merge(EQUIPMENT, readings)}
        oven = findings[extraction.equipment_identity(item("x", code="E06"))]
        self.assertEqual((oven["pages"], oven["value"]["quantity"], oven["conflicts"]), ([5, 19], 2, {}))
        self.assertEqual([cite["excerpt"] for cite in oven["citations"]], ["schedule E06", "plan label"])
        self.assertEqual(len(findings), 5)                      # E06, E21, E22, the unscheduled fridge label, wok burner
        self.assertIn(extraction.equipment_identity(item("UB fridge")), findings)
        self.assertIn(extraction.equipment_identity(item("Wok burner")), findings)

    def test_the_same_item_on_several_pages_is_one_finding_and_disagreement_is_conflicting(self):
        readings = [
            (5, 0, [item("Combi oven", code="E01", quantity=1, size="750x790", evidence="schedule row E01"),
                    item("Ice maker", quantity=2)]),
            (20, 0, [item("Combi oven", code="E01", quantity=1, under_hood=True)]),
            (24, 0, [item("Ice maker", quantity=1, rated_power="1.2 kW")]),
        ]
        findings = {row["value"]["name"]: row for row in extraction.merge(EQUIPMENT, readings)}
        oven = findings["Combi oven"]
        self.assertEqual((oven["pages"], oven["evidence"], oven["value"]["under_hood"], oven["not_printed"]),
                         ([5, 20], "supported", True, ["rated_power"]))
        self.assertEqual(oven["citations"][0], {"page": 5, "excerpt": "schedule row E01"})
        ice = findings["Ice maker"]
        self.assertEqual(ice["evidence"], "conflicting")
        self.assertEqual(ice["conflicts"]["quantity"], [{"value": 2, "pages": [5]}, {"value": 1, "pages": [24]}])
        self.assertEqual(ice["value"]["rated_power"], "1.2 kW")
        self.assertEqual(ice["not_printed"], [])

    def test_items_seen_only_in_renders_are_inferred(self):
        readings = [(5, 0, [item("Combi oven", code="E01")]), (9, 0, [item("Combi oven", code="E01"), item("Wall-mounted screen")])]
        findings = {row["value"]["name"]: row for row in extraction.merge(EQUIPMENT, readings, page_types={5: "schedule", 9: "render_or_photo"})}
        self.assertEqual((findings["Combi oven"]["evidence"], findings["Combi oven"]["pictures_only"]), ("supported", False))
        self.assertEqual((findings["Wall-mounted screen"]["evidence"], findings["Wall-mounted screen"]["pictures_only"]), ("inferred", True))

    def test_sizes_agreeing_in_their_numbers_are_one_reading_and_different_numbers_still_conflict(self):
        readings = [(5, 0, [item("Hot food station", code="E10", size="4900MM(W)X700MM(D)X850MM(H)"),
                            item("UB freezer", code="E22", size="1200MM(W)X700MM(D)")]),
                    (19, 0, [item("Hot food station", code="E10", size="4900X700"), item("UB freezer", code="E22", size="1500")])]
        findings = {row["value"]["code"]: row for row in extraction.merge(EQUIPMENT, readings)}
        self.assertEqual((findings["E10"]["value"]["size"], findings["E10"]["conflicts"]), ("4900MM(W)X700MM(D)X850MM(H)", {}))
        self.assertEqual([option["value"] for option in findings["E22"]["conflicts"]["size"]], ["1200MM(W)X700MM(D)", "1500"])

    def test_overlapping_sections_of_one_page_take_the_largest_count(self):
        readings = [(9, 0, [item("Display fridge", quantity=2)]), (9, 1, [item("Display fridge", quantity=3)])]
        [finding] = extraction.merge(EQUIPMENT, readings)
        self.assertEqual((finding["value"]["quantity"], finding["conflicts"]), (3, {}))

    def test_the_text_check_finds_misreadings_and_skips_scans(self):
        text = "EQUIPMENT SCHEDULE\nE01  COMBI OVEN  750W X 790D\nE02  UNDERBENCH FRIDGE 1500 X 700\n" + "NOTE " * 500
        readings = [(5, 0, [item("Combi oven", code="E01"), item("Pizza oven", code="E09")]), (6, 0, [item("Coffee machine")])]
        findings = {row["value"]["name"]: row for row in extraction.merge(EQUIPMENT, readings, {5: text, 6: ""})}
        self.assertTrue(findings["Combi oven"]["text_match"])
        self.assertFalse(findings["Pizza oven"]["text_match"])
        self.assertIsNone(findings["Coffee machine"]["text_match"])           # a scanned page has no text layer
        self.assertTrue(extraction.text_match(item("U/B fridge"), text))       # common shorthand
        self.assertIsNone(extraction.text_match(item("Combi oven"), "TITLE BLOCK  PROJECT  E01 COMBI OVEN"))  # title-block-only page
        found_once = extraction.merge(EQUIPMENT, [(5, 0, [item("Combi oven")]), (7, 0, [item("Combi oven")])], {5: text, 7: "PLAN " * 500})
        self.assertTrue(found_once[0]["text_match"])                            # printed on one of its pages is enough


class FakeReader:
    model = "fake"

    def __init__(self, replies):
        self.replies, self.calls = replies, []

    def propose(self, prompt, image_paths=()):
        pages = [int(Path(path).stem.split("_")[1]) for path in image_paths]
        self.calls.extend(pages)
        self.batches = getattr(self, "batches", []) + [pages]
        self.prompts = getattr(self, "prompts", []) + [prompt]
        if len(pages) > 1:
            # Several images in one call: one that would fail is left out of the reply (it's then read on its own).
            return {"images": {str(index): self.replies[page] for index, page in enumerate(pages, 1)
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


@unittest.skipUnless(shutil.which("pdftoppm") and shutil.which("pdftotext"), "needs poppler")
class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.pdf = root / "set.pdf"
        pages = "".join(f"{3 + i} 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 {200 + 40 * i} 100]>>endobj\n" for i in range(3))
        self.pdf.write_text(f"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R 4 0 R 5 0 R]/Count 3>>endobj\n"
                            f"{pages}trailer<</Root 1 0 R>>\n%%EOF\n")
        job = root / "job"
        job.mkdir()
        equipment = [{"kind": "equipment_appliances", "what": "ovens", "evidence": ""}]
        (job / pass1.RESULT_FILE).write_text(json.dumps({"pages": {
            "1": {"page_type": "schedule", "information": equipment}, "2": {"page_type": "elevation", "information": []},
            "3": {"page_type": "floor_plan", "information": equipment}}}))
        self.project = {"id": "job", "pdf": str(self.pdf), "review_dir": str(job)}

    def tearDown(self):
        service._RUNNING.clear()
        pass1.PROVIDER_FACTORY = None
        self.temp.cleanup()

    def test_only_flagged_pages_are_read_findings_are_proposals_and_a_rerun_calls_nothing(self):
        reader = FakeReader({1: {"items": [{"name": "Combi oven", "code": "E01", "quantity": 1}]},
                             3: {"items": [{"name": "Combi oven", "code": "E01", "quantity": 2}, {"name": "TV", "quantity": 1}]}})
        pass1.PROVIDER_FACTORY = lambda: reader
        service.start(None, self.project)
        done = wait(self.project)
        # The schedule (page 1) is read first; the plan (page 3) is then read with the schedule's codes.
        self.assertEqual((done["status"], done["pages"], reader.batches), ("done", [1, 3], [[1], [3]]))
        self.assertNotIn("schedule lists these codes", reader.prompts[0])
        self.assertIn("schedule lists these codes: E01 Combi oven.", reader.prompts[1])
        oven = next(row for row in done["findings"] if row["value"]["name"] == "Combi oven")
        self.assertEqual((oven["evidence"], oven["status"], oven["pages"]), ("conflicting", "proposed", [1, 3]))
        self.assertEqual(done["open"], 2)
        self.assertEqual(service.accepted(self.project), [])
        service.start(None, self.project)
        self.assertEqual(wait(self.project)["status"], "done")
        self.assertEqual(sorted(reader.calls), [1, 3])

    def test_review_needs_a_choice_for_conflicts_and_decisions_expire_with_the_run(self):
        reader = FakeReader({1: {"items": [{"name": "Combi oven", "code": "E01", "quantity": 1}]},
                             3: {"items": [{"name": "Combi oven", "code": "E01", "quantity": 2}, {"name": "TV", "quantity": 1}]}})
        pass1.PROVIDER_FACTORY = lambda: reader
        service.start(None, self.project)
        done = wait(self.project)
        oven = next(row for row in done["findings"] if row["value"]["name"] == "Combi oven")
        tv = next(row for row in done["findings"] if row["value"]["name"] == "TV")
        with self.assertRaisesRegex(ValueError, "pages disagree"):
            service.review(None, self.project, {"finding_id": oven["id"], "decision": "accepted"})
        service.review(None, self.project, {"finding_id": oven["id"], "decision": "accepted",
                                            "value": {**oven["value"], "quantity": 1}, "reviewer": "Sam"})
        state = service.review(None, self.project, {"finding_id": tv["id"], "decision": "rejected"})
        self.assertEqual(state["open"], 0)
        self.assertEqual([(row["name"], row["quantity"]) for row in service.accepted(self.project)], [("Combi oven", 1)])
        with self.assertRaisesRegex(ValueError, "accept or reject"):
            service.review(None, self.project, {"finding_id": tv["id"], "decision": "maybe"})
        # New pass 1 readings make a new run: the old decisions no longer apply.
        result = json.loads((Path(self.project["review_dir"]) / pass1.RESULT_FILE).read_text())
        result["pages"]["2"]["title"] = "changed"
        (Path(self.project["review_dir"]) / pass1.RESULT_FILE).write_text(json.dumps(result))
        service.start(None, self.project)
        again = wait(self.project)
        self.assertEqual((again["open"], service.accepted(self.project)), (2, []))

    def test_a_failed_section_fails_the_run_and_without_pass_1_nothing_starts(self):
        reader = FakeReader({1: RuntimeError("usage limit reached"), 3: {"items": []}})
        pass1.PROVIDER_FACTORY = lambda: reader
        service.start(None, self.project)
        failed = wait(self.project)
        self.assertEqual(failed["status"], "failed")
        self.assertIn("1 of 2 page sections couldn't be read (pages 1)", failed["problem"])
        self.assertIn("usage limit reached", failed["problem"])
        (Path(self.project["review_dir"]) / pass1.RESULT_FILE).write_text("{}")
        with self.assertRaisesRegex(ValueError, "Read every page first"):
            service.start(None, self.project)


class UsageLimitTests(unittest.TestCase):
    def test_after_the_usage_limit_no_more_sections_are_sent(self):
        class LimitedReader:
            model, calls = "fake", []

            def propose(self, prompt, image_paths=()):
                self.calls.append(image_paths[0])
                raise pass1.UsageLimitReached("limit reached; it resets at 6:01 PM")

        with tempfile.TemporaryDirectory() as folder:
            images = {}
            for page in (1, 2, 3):
                path = Path(folder) / f"page_{page:03d}.png"
                path.write_bytes(bytes([page]))
                images[(page, 0)] = (path, 1)
            reader = LimitedReader()
            readings, failures, calls = service.read_sections(EQUIPMENT, images, Path(folder) / "replies", reader, workers=1)
        self.assertEqual((readings, calls, len(reader.calls), len(failures)), ([], 1, 1, 3))


if __name__ == "__main__":
    unittest.main()
