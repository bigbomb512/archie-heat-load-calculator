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
    def test_window_list_id_and_w_by_h_columns(self):
        # Set 17 prints two horizontal WINDOW LIST blocks, not WINDOW NUMBER.
        words = line(10, ("WINDOW", 20), ("LIST", 55))
        words += line(20, ("ID", 20), ("W01", 100), ("W02", 200))
        words += line(30, ("W", 20), ("x", 28), ("H", 36), ("3,250×2,200", 100), ("1,090×2,200", 200))
        words += line(40, ("Head", 20), ("Height", 40), ("2,400", 100), ("2,400", 200))
        words += line(90, ("WINDOW", 20), ("LIST", 55))
        words += line(100, ("ID", 20), ("W03", 100))
        words += line(110, ("W", 20), ("x", 28), ("H", 36), ("610×945", 100))
        words += line(120, ("Head", 20), ("Height", 40), ("2,200", 100))
        result = hw.read_schedules(words, 17)
        self.assertEqual([(row["mark"], row["width_mm"], row["height_mm"], row["head_mm"])
                          for row in result["windows"]],
                         [("W01", 3250, 2200, 2400), ("W02", 1090, 2200, 2400), ("W03", 610, 945, 2200)])

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

    def test_a_tag_read_in_both_word_orders_is_one_tag(self):
        # The page is read in position order and in text order (house_windows_service); a tag found both ways is once.
        words = line(100, ("WG_07", 40)) + line(100, ("WG_07", 41)) + line(300, ("WG_07", 40))
        tags = hw.find_window_tags(words, 7)
        self.assertEqual([tag["mark"] for tag in tags], ["WG_07", "WG_07"])     # the same mark on two windows stays two
        self.assertEqual(round(tags[1]["y"] - tags[0]["y"]), 200)

    def test_legend_window_samples_and_wall_type_marks_are_not_plan_tags(self):
        words = line(100, ("W01", 10), ("Window", 45), ("numbers", 90), ("(as", 130), ("scheduled)", 150))
        words += line(180, ("W2", 10), ("-", 25), ("110mm", 35), ("brick", 70), ("wall", 100))
        words += line(1000, ("W2", 300))
        self.assertEqual([tag["mark"] for tag in hw.find_window_tags(words, 7)], ["W2"])


class PlacementTests(unittest.TestCase):
    # A 6 m × 4 m room, 1 point = 10 mm: outline in points 0..600 × 0..400; edge 0 is the top wall (outside).
    room = {"trace_id": "bed", "points": [[0, 0], [600, 0], [600, 400], [0, 400], [0, 0]], "outside_edges": [0, 1]}
    schedule = [{"mark": "W1", "page": 20, "width_mm": 1800, "height_mm": 1200, "sill_mm": None, "head_mm": 2100,
                 "level": "Ground Floor", "evidence": "W1 1800 1200 2100"},
                {"mark": "W2", "page": 20, "width_mm": 2400, "height_mm": 1200, "sill_mm": 900, "head_mm": None,
                 "level": None, "evidence": "W2 2400 1200"}]

    def tag(self, mark, x, y, level="Ground Floor"):
        return {"mark": mark, "x": x, "y": y, "page": 5, "level": level}

    def test_zero_padded_and_hyphenated_marks_match_the_same_schedule_row(self):
        result = hw.place_windows([self.tag("W-01", 100, 20)], self.schedule, [self.room], 10)
        self.assertEqual([row["mark"] for row in result["openings"]], ["W-01"])
        self.assertEqual(result["openings"][0]["distance_mm"], 200)
        self.assertEqual(result["openings"][0]["tag_position"], {"page": 5, "x": 100, "y": 20})

    def test_levels_compare_loosely_and_an_unmatched_level_falls_back_to_the_mark(self):
        # "Ground Floor" on the plan and "Ground" in the schedule are the same level; a level that matches nothing
        # (schedules name levels loosely) falls back to the mark alone rather than losing the window.
        self.assertEqual(hw.normalized_level("Ground Floor"), hw.normalized_level("ground"))
        result = hw.place_windows([self.tag("W1", 100, 20, "First Floor")], self.schedule, [self.room], 10)
        self.assertEqual([row["mark"] for row in result["openings"]], ["W1"])

    def test_offset_keeps_full_precision_at_end_of_wall(self):
        room = {**self.room, "points": [[0, 0], [421.007, 0], [421.007, 400], [0, 400], [0, 0]],
                "outside_edges": [0]}
        result = hw.place_windows([self.tag("W1", 421.007, 0)], self.schedule, [room], 10)
        self.assertEqual(len(result["openings"]), 1)
        opening = result["openings"][0]
        self.assertAlmostEqual(opening["offset_m"], 2.41007)
        self.assertLessEqual(opening["offset_m"] + opening["width_m"], 4.21007)

    def test_a_tag_far_from_the_traced_wall_is_placed_and_labelled(self):
        # An AI outline can stop short of the wall: a tag 1.5 m outside it still goes on that room's outside wall,
        # with the distance stated; one beside the wall carries no such note.
        far = hw.place_windows([self.tag("W1", 300, -150)], self.schedule, [self.room], 10)
        self.assertEqual([(row["mark"], row["edge_index"]) for row in far["openings"]], [("W1", 0)])
        self.assertTrue(any("1.5 m from this room's traced outside wall" in note for note in far["openings"][0]["assumptions"]))
        near = hw.place_windows([self.tag("W1", 300, 30)], self.schedule, [self.room], 10)
        self.assertFalse(any("traced outside wall" in note for note in near["openings"][0]["assumptions"]))
        too_far = hw.place_windows([self.tag("W1", 300, -350)], self.schedule, [self.room], 10)
        self.assertEqual(too_far["openings"], [])

    def test_a_window_a_person_already_entered_is_not_added_again(self):
        # Set 18's hand-checked job got its windows twice when the drawing-text step ran over it (2026-11-17).
        room = {**self.room, "existing_openings": [{"opening_id": "op-W1", "edge_index": 0, "width_m": 1.8,
                                                    "sill_height_m": 0.9, "head_height_m": 2.1, "declaration_source": "reviewer"}]}
        result = hw.place_windows([self.tag("W1", 150, 30), self.tag("W1", 450, 30), self.tag("W2", 300, 370)],
                                  self.schedule, [room], 10)
        self.assertEqual(sorted(row["mark"] for row in result["openings"]), ["W1", "W2"])   # the second W1 and W2 are new
        skipped = [row for row in result["unplaced"] if row.get("skipped")]
        self.assertEqual([(row["mark"], "op-W1" in row["reason"]) for row in skipped], [("W1", True)])

    def test_several_windows_share_one_wall_while_they_fit(self):
        result = hw.place_windows([self.tag("W1", 150, 30), self.tag("W2", 450, 30)], self.schedule, [self.room], 10)
        self.assertEqual([(row["mark"], row["edge_index"]) for row in result["openings"]], [("W1", 0), ("W2", 0)])
        self.assertEqual([row["position_basis"] for row in result["openings"]], ["plan_tag_projection"] * 2)
        self.assertLess(result["openings"][0]["offset_m"], result["openings"][1]["offset_m"])
        opening = result["openings"][0]
        self.assertEqual((opening["width_m"], opening["sill_height_m"], opening["head_height_m"]), (1.8, 0.9, 2.1))
        self.assertEqual(result["unplaced"], [])

    def test_a_wall_already_filled_refuses_the_next_window(self):
        tags = [self.tag("W2", 100, 20), self.tag("W2", 300, 20), self.tag("W2", 500, 20),
                self.tag("W2", 150, 20)]
        result = hw.place_windows(tags, self.schedule, [self.room], 10)
        self.assertEqual(len(result["openings"]), 3)                  # widths are 2.4 + 2.4 + 1.2 m
        self.assertAlmostEqual(sum(row["width_m"] for row in result["openings"]), 6.0)
        self.assertIn("already filled", result["unplaced"][0]["reason"])
        trimmed = min(result["openings"], key=lambda row: row["width_m"])
        self.assertAlmostEqual(trimmed["width_m"], 1.2)
        self.assertIn("trimmed to fit", trimmed["assumptions"][0])

    def test_tag_too_far_from_an_outside_wall_or_only_near_an_inside_wall(self):
        room = dict(self.room, outside_edges=[1])                      # the top wall is inside
        result = hw.place_windows([self.tag("W1", 100, 30), self.tag("W1", 100, 200)], self.schedule, [room], 10)
        self.assertEqual(result["openings"], [])
        self.assertEqual({row["reason"] for row in result["unplaced"]}, {"no outside wall within 3000 mm of the tag"})

    def test_a_tag_inside_a_room_cannot_jump_to_a_neighbouring_rooms_closer_outside_edge(self):
        left = {"trace_id": "left", "points": [[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]],
                "outside_edges": [0]}
        right = {"trace_id": "right", "points": [[100, 0], [200, 0], [200, 100], [100, 100], [100, 0]],
                 "outside_edges": [3]}
        # The tag is inside left; the right room's eligible edge is only 10 mm away.
        tag = self.tag("W1", 90, 20)
        result = hw.place_windows([tag], self.schedule, [left, right], 1)
        self.assertEqual([(row["trace_id"], row["edge_index"]) for row in result["openings"]], [("left", 0)])

    def test_existing_reviewed_openings_reserve_edge_capacity_before_trimming(self):
        room = dict(self.room, existing_openings=[{"edge_index": 0, "width_m": 5.0,
                                                    "declaration_source": "reviewer"}])
        result = hw.place_windows([self.tag("W2", 300, 20)], self.schedule, [room], 10)
        self.assertAlmostEqual(result["openings"][0]["width_m"], 1.0)
        self.assertIn("trimmed to fit", result["openings"][0]["assumptions"][0])

    def test_room_selection_uses_each_traces_own_calibration(self):
        first = {"trace_id": "first", "points": [[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]],
                 "outside_edges": [0], "mm_per_point": 10}
        second = {"trace_id": "second", "points": [[200, 0], [300, 0], [300, 100], [200, 100], [200, 0]],
                  "outside_edges": [0], "mm_per_point": 1}
        result = hw.place_windows([self.tag("W1", 150, 20)], self.schedule, [first, second], 5)
        self.assertEqual(result["openings"][0]["trace_id"], "second")

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



def char(text, x, top, matrix, size=3.0):
    return {"text": text, "x0": x, "x1": x + size, "top": top, "bottom": top + size, "size": size, "matrix": matrix}


UP_THE_PAGE = (0.0, 1.0, -1.0, 0.0, 0, 0)      # set 16 p6's "WG_09": rotated 90°, read bottom to top
ACROSS = (1.0, 0.0, 0.0, 1.0, 0, 0)


class RotatedTagTests(unittest.TestCase):
    """A tag beside a wall running up the sheet is printed rotated; position-order words split it into letters, so
    every window on those walls was missed (set 16: no east or west glass, 2026-11-28)."""

    def rotated(self, text, x, bottom):
        return [char(letter, x, bottom - 2.2 * index, UP_THE_PAGE) for index, letter in enumerate(text)]

    def test_rotated_marks_are_read_as_whole_words(self):
        chars = (self.rotated("WG_09", 1087.5, 679.6) + self.rotated("WG_11", 1087.5, 1013.9)
                 + [char(letter, 1259.9 + 3.6 * index, 562.1, ACROSS) for index, letter in enumerate("WG_07")])
        words = hw.rotated_words(chars)
        self.assertEqual(sorted(word["text"] for word in words), ["WG_09", "WG_11"])
        tags = hw.find_window_tags(words, 6)
        self.assertEqual(sorted(tag["mark"] for tag in tags), ["WG_09", "WG_11"])
        nine = [tag for tag in tags if tag["mark"] == "WG_09"][0]
        self.assertAlmostEqual(nine["x"], 1089.0, delta=1.0)        # the tag sits where its letters are
        self.assertTrue(670 <= nine["y"] <= 683)

    def test_text_read_down_the_page_is_read_top_to_bottom(self):
        down = (0.0, -1.0, 1.0, 0.0, 0, 0)
        chars = [char(letter, 40.0, 100 + 2.2 * index, down) for index, letter in enumerate("W12")]
        self.assertEqual([word["text"] for word in hw.rotated_words(chars)], ["W12"])

    def test_a_wide_letter_after_a_narrow_one_stays_in_the_word(self):
        # Set 16 p7's "HLW_01", as printed: each letter's extent along the line, in page coordinates.
        extents = [("H", 933.29, 936.10, 2.81), ("L", 931.23, 933.39, 2.16), ("W", 927.66, 931.33, 3.67),
                   ("_", 925.59, 927.76, 2.16), ("0", 923.44, 925.60, 2.16), ("1", 921.29, 923.45, 2.16)]
        chars = [{"text": text, "x0": 1664.82, "x1": 1668.7, "top": top, "bottom": bottom, "size": size,
                  "matrix": UP_THE_PAGE} for text, top, bottom, size in extents]
        self.assertEqual([word["text"] for word in hw.rotated_words(chars)], ["HLW_01"])

    def test_a_gap_along_the_line_ends_the_word(self):
        chars = self.rotated("W1", 50.0, 300) + self.rotated("W2", 50.0, 250)
        self.assertEqual(sorted(word["text"] for word in hw.rotated_words(chars)), ["W1", "W2"])


if __name__ == "__main__":
    unittest.main()
