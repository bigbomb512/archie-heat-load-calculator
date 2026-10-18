"""The equipment schedule read from the PDF's text layer: its rows, matching plan labels and misread codes to them,
and equipment reading using it. Layout text below copies the quirks of Butcher Buffet's page 5 (pdftotext -layout)."""

import json
from pathlib import Path
import unittest
from unittest import mock

from ai import page_extraction as extraction
from backend import page_extraction_service as service, page_inventory_service as pass1
from tests.test_page_extraction import EQUIPMENT, FakeReader, ServiceTests, item, wait

PAD = " " * 40
LAYOUT = "\n".join([
    PAD + "EQUIPMENT",
    PAD + "CODE ITEMS                     DIMENSION                          QTY    SUPPLIED BY",
    PAD + "E01   POS                       700MM(W)X800MM(D)X900MM(H)         1      CLIENT",
    PAD + "E02   UB FRIDGE                 1200MM(W)X700MM(D)X850MM(H)        1      CLIENT",
    PAD + "E06   COMBI OVEN                750MM(W)X790MM(D)X675MM(H)         1      CLIENT",
    # A plan tag printed on the row's line, just before it.
    "   UB FR 1500   E40 E41                 E21   UB FRIDGE    1500MM(W)X700MM(D)X850MM(H)        10     CLIENT",
    PAD + "E17   FOOD STATION              4000MM(W)X700MM(D)X850MM(H)        1      CLIENT",
    PAD + "E27   WOK BURNER                1200MM(W)X840MM(D)X900MM(H)        1      CLIENT",
    # The text layer put this row's code on the line below it.
    PAD + "      DEEP FRYER                450MM(W)X840MM(D)X900MM(H)         3      CLIENT",
    PAD + "E29",
    PAD + "E36   PIZZA DECK OVEN           1300MM(W)X1000MM(D)X1600MM(H)      1      CLIENT",
    PAD + "E40   WIRE SHELVING             1524MM(W)X640MM(D)X1828MM(H)              CLIENT",
    PAD + "E48   6 BURNER                  1200MM(W)X840MM(D)X900MM(H)        1      CLIENT",
    # Plan tags only, no row.
    "        E25          E28         E29     E29    E29    E30      E36         E06",
])


class PrintedScheduleTests(unittest.TestCase):
    def setUp(self):
        self.schedule = extraction.printed_schedule({5: LAYOUT})

    def test_every_row_is_read_with_its_name_size_and_quantity(self):
        self.assertEqual(list(self.schedule), ["E01", "E02", "E06", "E17", "E21", "E27", "E29", "E36", "E40", "E48"])
        self.assertEqual(self.schedule["E21"], {"code": "E21", "name": "UB FRIDGE", "size": "1500MM(W)X700MM(D)X850MM(H)",
                                                "quantity": 10, "page": 5})
        self.assertEqual((self.schedule["E29"]["name"], self.schedule["E29"]["quantity"]), ("DEEP FRYER", 3))
        self.assertIsNone(self.schedule["E40"]["quantity"])           # no quantity printed
        self.assertNotIn("E25", self.schedule)                        # a plan tag, not a row

    def test_a_page_without_a_schedule_gives_nothing(self):
        plan = "\n".join(["   UB FR 1500     E21        E21", "   COMBI OVEN  E06   750X790", "   E01 POS 1"])
        self.assertEqual(extraction.printed_schedule({19: plan}), {})
        self.assertEqual(extraction.printed_schedule({}), {})

    def test_a_plan_label_matches_one_row_by_name_and_width_or_by_width_and_depth(self):
        match = lambda name, size="": extraction.schedule_match({"name": name, "size": size}, self.schedule)
        self.assertEqual(match("UB FR 1500"), "E21")                  # abbreviations, and the width picks the row
        self.assertEqual(match("UB FRIDGE", "1200X700X850"), "E02")
        self.assertEqual(match("6 BURNER", "1200MM(W)X840MM(D)X900MM(H)"), "E48")    # same size as E27: name decides
        self.assertEqual(match("BUFFET, FISH CAKE, SAUCE", "4000X700"), "E17")       # the size alone is one row
        self.assertIsNone(match("UB FRIDGE"))                         # two underbench fridges, no size: no guess
        self.assertIsNone(match("OVEN"))                              # combi oven or pizza deck oven: no guess
        self.assertIsNone(match("65 INCH TV"))

    def test_merge_corrects_a_misread_code_and_counts_a_plan_label_once(self):
        readings = [
            (5, 0, [item("UB FRIDGE", code="E21", quantity=10, size="1500MM(W)X700MM(D)X850MM(H)"),
                    item("6 BURNER", code="E49", quantity=1, size="1200MM(W)X840MM(D)X900MM(H)")]),
            (19, 0, [item("UB FR 1500", quantity=8, evidence="UB FR 1500 x8"), item("TV", quantity=1)]),
        ]
        before = [list(items) for _page, _section, items in readings]
        findings = extraction.merge(EQUIPMENT, readings, schedule=self.schedule)
        by_code = {row["value"]["code"]: row for row in findings}
        self.assertEqual(sorted(by_code), ["", "E21", "E48"])         # the TV, and two scheduled items
        fridge = by_code["E21"]
        self.assertEqual((fridge["value"]["quantity"], fridge["pages"], fridge["conflicts"]), (10, [5, 19], {}))
        self.assertIn("Also labelled on the plans as UB FR 1500 (page 19)", fridge["schedule_notes"][0])
        burner = by_code["E48"]
        self.assertEqual(burner["schedule_notes"], ["Code read as E49; the printed schedule lists it as E48 6 BURNER."])
        self.assertEqual(burner["schedule_row"]["size"], "1200MM(W)X840MM(D)X900MM(H)")
        self.assertEqual([list(items) for _page, _section, items in readings], before)    # readings left unchanged
        # Without a printed schedule the merge is as before: the plan label stays its own item.
        plain = extraction.merge(EQUIPMENT, readings)
        self.assertIn("name:ub fr 1500", {row["identity"] for row in plain})


class PrintedScheduleServiceTests(unittest.TestCase):
    setUp, tearDown = ServiceTests.setUp, ServiceTests.tearDown

    def test_the_plans_are_read_with_the_printed_codes_and_the_findings_tie_to_its_rows(self):
        reader = FakeReader({1: {"items": [{"name": "UB FRIDGE", "code": "E21", "quantity": 10}]},
                             3: {"items": [{"name": "UB FR 1500", "quantity": 8}]}})
        pass1.PROVIDER_FACTORY = lambda: reader
        texts = {1: LAYOUT, 3: "   UB FR 1500"}
        with mock.patch.object(service, "page_texts", lambda _pdf, _folder, pages: {page: texts.get(page, "") for page in pages}):
            service.start(None, self.project)
            done = wait(self.project)
        self.assertEqual(done["status"], "done")
        # The plan prompt lists every printed code, not only what the AI read from the schedule.
        self.assertIn("E48 6 BURNER", reader.prompts[1])
        self.assertIn("E29 DEEP FRYER", reader.prompts[1])
        fridge = [row for row in done["findings"] if row["value"]["code"] == "E21"]
        self.assertEqual(len(done["findings"]), 1)
        self.assertEqual((fridge[0]["value"]["quantity"], fridge[0]["pages"]), (10, [1, 3]))
        saved = json.loads((Path(self.project["review_dir"]) / service.RESULT_FILE).read_text())
        self.assertEqual(saved["equipment_appliances"]["findings"][0]["schedule_row"]["code"], "E21")


if __name__ == "__main__":
    unittest.main()
