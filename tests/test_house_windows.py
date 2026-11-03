"""House windows from the text layer: schedules (row and column tables), plan tags and placement on outside walls."""

import unittest

from ai import house_windows as hw


def word(text, x, y, width=None):
    width = width if width is not None else 4.5 * len(text)
    return {"text": text, "x0": x, "x1": x + width, "top": y, "bottom": y + 6}


def line(y, *cells):
    """A printed line: (text, x) pairs."""
    return [word(text, x, y) for text, x in cells]


def row_table():
    """Set 18's layout: a window schedule with a door schedule beside it on the same lines; one row wraps."""
    words = line(45, ("Window", 217), ("Schedule", 247), ("Door", 711), ("Schedule", 730))
    words += line(63, ("Level", 69), ("Mark", 129), ("Width", 172), ("Height", 212), ("Head", 248), ("Height", 269),
                  ("Description", 333), ("Count", 421),
                  ("Level", 575), ("Mark", 656), ("Width", 708), ("Height", 758), ("Description", 826), ("Count", 910))
    words += line(93, ("S1", 133), ("940", 176), ("3450", 214), ("Sky", 336), ("Light", 351), ("1", 429),
                  ("Ground", 532), ("Floor", 560), ("D2", 660), ("820", 712), ("2400", 760), ("Single", 825), ("Door", 849), ("1", 918))
    words += line(189, ("Ground", 44), ("Floor", 73), ("W4", 132), ("1600", 174), ("600", 216), ("1600", 261),
                  ("Sliding", 301), ("Window", 327), ("1", 429),
                  ("First", 532), ("Floor", 549), ("D9", 660), ("1000", 709), ("2100", 760), ("Barn", 805), ("Style", 823),
                  ("Sliding", 843), ("Door", 869), ("1", 918))
    words += line(213, ("Ground", 44), ("Floor", 73), ("W6", 132), ("5000", 173), ("2400", 214), ("2400", 261),
                  ("Operable", 321), ("Window", 355), ("1", 429),
                  ("First", 532), ("Floor", 549), ("D10", 658), ("1640", 709), ("2400", 760), ("Glass", 805), ("Door", 830), ("1", 918))
    # W20: mark, head and description on one line, its level and sizes printed 3 pt lower (as on the real sheet).
    words += line(372, ("W20", 130), ("2400", 261), ("Fixed", 328), ("Window", 349), ("1", 429))
    words += line(375, ("First", 44), ("Floor", 62), ("2770", 173), ("900", 216))
    words += line(384, ("First", 44), ("Floor", 62), ("W21", 130), ("3800", 174), ("5300", 214), ("2400", 261),
                  ("Fixed", 328), ("Window", 349), ("1", 429))
    words += line(762, ("Issue", 27), ("Description", 90), ("Drawing", 896))
    return words


def column_table():
    """Sets 15/16's layout: one column per window; a size wrapped over two lines; sills below; then a door table."""
    words = line(144, ("WINDOW", 1066), ("SCHEDULE", 1157))
    words += line(195, ("WINDOW", 59), ("NUMBER", 150))
    words += line(198, ("WE_01", 366), ("WE_09", 451), ("WG_01", 536), ("WG_01", 621))
    words += line(240, ("1,290×", 448))
    words += line(246, ("UNIT", 59), ("DIMENSIONS", 110))
    words += line(249, ("894×1,490", 354), ("1,200×2,700", 524), ("600×2,700", 610))
    words += line(258, ("1,300", 452))
    words += line(297, ("W/D", 59), ("NOMINAL", 103), ("SILL", 198), ("HEIGHTS", 245))
    words += line(300, ("740", 378), ("740", 463), ("0", 548), ("0", 633))
    words += line(396, ("VIEW", 59), ("FROM", 115), ("OPENING", 177), ("SIDE", 272))
    words += line(930, ("DOOR", 59), ("NUMBER", 123))
    words += line(933, ("DL_01", 354))
    words += line(981, ("UNIT", 59), ("DIMENSIONS", 110))
    words += line(984, ("6,000×2,600", 340))
    return words


class ScheduleTests(unittest.TestCase):
    def test_row_table_reads_columns_by_their_headers_and_keeps_tables_apart(self):
        result = hw.read_schedules(row_table(), 20)
        windows = {row["mark"]: row for row in result["windows"]}
        self.assertEqual(sorted(windows), ["D10", "W20", "W21", "W4", "W6"])
        self.assertEqual((windows["W4"]["width_mm"], windows["W4"]["height_mm"], windows["W4"]["head_mm"]), (1600, 600, 1600))
        self.assertEqual(windows["W4"]["level"], "Ground Floor")
        self.assertEqual(windows["W4"]["type"], "Sliding Window")
        # The wrapped row takes its level and sizes from the line below.
        self.assertEqual((windows["W20"]["level"], windows["W20"]["width_mm"], windows["W20"]["height_mm"]), ("First Floor", 2770, 900))
        # A glass door counts as glazing; a solid sliding door does not; a skylight is kept apart.
        self.assertIn("D10", windows)
        self.assertEqual([row["mark"] for row in result["doors"]], ["D2", "D9"])
        self.assertEqual([row["mark"] for row in result["skylights"]], ["S1"])

    def test_column_table_reads_wrapped_sizes_sills_and_repeated_marks(self):
        result = hw.read_schedules(column_table(), 15)
        windows = result["windows"]
        self.assertEqual([(row["mark"], row["width_mm"], row["height_mm"], row["sill_mm"]) for row in windows],
                         [("WE_01", 894, 1490, 740), ("WE_09", 1290, 1300, 740), ("WG_01", 1200, 2700, 0), ("WG_01", 600, 2700, 0)])
        self.assertEqual([row["mark"] for row in result["doors"]], ["DL_01"])

    def test_a_page_without_a_schedule_reads_nothing(self):
        self.assertEqual(hw.read_schedules(line(10, ("GROUND", 10), ("FLOOR", 60), ("PLAN", 100), ("W4", 300)), 3),
                         {"windows": [], "doors": [], "skylights": [], "unread": []})

    def test_sill_and_head(self):
        self.assertEqual(hw.sill_and_head({"height_mm": 600, "sill_mm": None, "head_mm": 1600}), (1000, 1600, None))
        self.assertEqual(hw.sill_and_head({"height_mm": 1490, "sill_mm": 740, "head_mm": None}), (740, 2230, None))
        sill, head, note = hw.sill_and_head({"height_mm": 1200, "sill_mm": None, "head_mm": None})
        self.assertEqual((sill, head), (900, 2100))
        self.assertIn("assumed 2,100 mm", note)
        sill, head, note = hw.sill_and_head({"height_mm": 5300, "sill_mm": None, "head_mm": 2400})
        self.assertEqual((sill, head), (0, 5300))
        self.assertIn("check", note)


class TagTests(unittest.TestCase):
    def test_marks_but_not_sizes(self):
        words = line(100, ("W4", 10), ("WG_01", 40), ("HLW_02", 80), ("W1200", 120), ("WIR", 160), ("W.I.R.", 200), ("WC", 240))
        self.assertEqual([tag["mark"] for tag in hw.find_window_tags(words, 7, "Ground Floor")], ["W4", "WG_01", "HLW_02"])


class PlacementTests(unittest.TestCase):
    # A 6 m × 4 m room, 1 point = 10 mm: outline in points 0..600 × 0..400; edge 0 is the top wall (outside).
    room = {"trace_id": "bed", "points": [[0, 0], [600, 0], [600, 400], [0, 400], [0, 0]], "outside_edges": [0, 1]}
    schedule = [{"mark": "W1", "page": 20, "width_mm": 1800, "height_mm": 1200, "sill_mm": None, "head_mm": 2100,
                 "level": "Ground Floor", "evidence": "W1 1800 1200 2100"},
                {"mark": "W2", "page": 20, "width_mm": 2400, "height_mm": 1200, "sill_mm": 900, "head_mm": None,
                 "level": None, "evidence": "W2 2400 1200"}]

    def tag(self, mark, x, y, level="Ground Floor"):
        return {"mark": mark, "x": x, "y": y, "page": 5, "level": level}

    def test_several_windows_share_one_wall_while_they_fit(self):
        result = hw.place_windows([self.tag("W1", 150, 30), self.tag("W2", 450, 30)], self.schedule, [self.room], 10)
        self.assertEqual([(row["mark"], row["edge_index"]) for row in result["openings"]], [("W1", 0), ("W2", 0)])
        opening = result["openings"][0]
        self.assertEqual((opening["width_m"], opening["sill_height_m"], opening["head_height_m"]), (1.8, 0.9, 2.1))
        self.assertEqual(result["unplaced"], [])

    def test_a_wall_already_filled_refuses_the_next_window(self):
        tags = [self.tag("W2", 100, 20), self.tag("W2", 300, 20), self.tag("W2", 500, 20)]
        result = hw.place_windows(tags, self.schedule, [self.room], 10)
        self.assertEqual(len(result["openings"]), 2)                  # 2 × 2.4 m fit a 6 m wall; a third doesn't
        self.assertIn("already filled", result["unplaced"][0]["reason"])

    def test_tag_too_far_from_an_outside_wall_or_only_near_an_inside_wall(self):
        room = dict(self.room, outside_edges=[1])                      # the top wall is inside
        result = hw.place_windows([self.tag("W1", 300, 30), self.tag("W1", 300, 200)], self.schedule, [room], 10)
        self.assertEqual(result["openings"], [])
        self.assertEqual({row["reason"] for row in result["unplaced"]}, {"no outside wall within 900 mm of the tag"})

    def test_unknown_mark_and_a_mark_with_two_sizes_are_listed_not_guessed(self):
        schedule = self.schedule + [dict(self.schedule[1], width_mm=900)]
        result = hw.place_windows([self.tag("W9", 100, 20), self.tag("W2", 100, 20)], schedule, [self.room], 10)
        self.assertEqual(result["openings"], [])
        reasons = sorted(row["reason"] for row in result["unplaced"])
        self.assertEqual(reasons, ["no window schedule row with this mark",
                                   "the schedule gives this mark more than one size; which one is here isn't printed"])

    def test_the_tag_level_picks_the_schedule_row(self):
        schedule = [dict(self.schedule[0], level="Ground Floor"), dict(self.schedule[0], level="First Floor", width_mm=900)]
        result = hw.place_windows([self.tag("W1", 150, 30, level="First Floor")], schedule, [self.room], 10)
        self.assertEqual(result["openings"][0]["width_m"], 0.9)

    def test_an_assumed_head_is_carried_with_the_opening(self):
        schedule = [dict(self.schedule[0], head_mm=None)]
        opening = hw.place_windows([self.tag("W1", 150, 30)], schedule, [self.room], 10)["openings"][0]
        self.assertEqual((opening["sill_height_m"], opening["head_height_m"]), (0.9, 2.1))
        self.assertIn("assumed 2,100 mm", opening["assumptions"][0])


if __name__ == "__main__":
    unittest.main()
