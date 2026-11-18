"""The local work between rounds of drawing questions is done only when it is needed (Turramurra 2026-11-18: a
16-round run took 22.6 min, of which the AI calls were about 4 min): the calculation evidence is rebuilt once per run
or when a question reads it, not after every round; the plan choice, plan levels, printed dimensions, vector page
fingerprints, north words and window-tag words are worked out once per version of their files; loaded plan pages are
shared across operations; and a round's questions are sent in parallel, at most the configured number at once."""

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from ai import autonomous_tasks, reviewer_room_geometry
from backend import autonomous_tasks_service as tasks_service, drawing_tasks_service as drawing
from backend import page_analysis_cache, reviewer_room_geometry_service
from tests.test_drawing_tasks import BatchTasks


def touch(path, text):
    """Rewrite a file so its signature changes even within the clock's resolution."""
    path.write_text(text, encoding="utf-8")
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))


class Unlocking(BatchTasks):
    """Each applied answer unlocks one more question, for `rounds` rounds; or every answer is refused."""

    def __init__(self, root, rounds=3, refuse=False):
        super().__init__(root, count=1)
        self.rounds, self.refuse, self.round = rounds, refuse, 1

    def post(self, web, project, data):
        if data["action"] == "run_all":
            self.builds += 1
            return {}
        record = self.records[(data["task"], data["target"])]
        if self.refuse:
            record["reply_attempts"].append({"outcome": "rejected", "reason": "No."})
            return {}
        record.update(status="applied", applied_value={"answer": json.loads(data["reply"])})
        self.applied.append(data["target"])
        if self.round < self.rounds:
            self.add("P6_kitchen", f"kitchen-{self.round}")
            self.round += 1
        return {}


class Provider:
    model = "fake"

    def propose(self, prompt, image_paths=()):
        return {"ok": True}, {}


class EvidenceRebuildTests(unittest.TestCase):
    def run_rounds(self, fake, folder, builds=None):
        builds = [] if builds is None else builds
        with patch.object(tasks_service, "post", fake.post), patch.object(tasks_service, "_all_current", fake.all_current), \
                patch.object(tasks_service, "_current_task", fake.current), patch.object(tasks_service, "_task_dir", fake.task_dir), \
                patch.object(tasks_service, "_export_determinations"), \
                patch("backend.productization.record_change_if_fingerprint_changed"), \
                patch("backend.phase1_supervisor_service._build_calculation_evidence",
                      side_effect=lambda web, project: builds.append(len(fake.applied))):
            result = drawing.answer_all(object(), {"id": "j", "review_dir": folder}, provider_for=lambda task: Provider())
        return result, builds

    def test_three_rounds_of_answers_rebuild_the_evidence_once_at_the_end(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = Unlocking(folder, rounds=3)
            result, builds = self.run_rounds(fake, folder)
        self.assertEqual(fake.applied, ["kitchen-0", "kitchen-1", "kitchen-2"])
        self.assertEqual(builds, [3])                   # once, after every answer was in
        self.assertEqual(result["evidence_rebuilds"], 1)

    def test_rounds_that_apply_nothing_do_not_rebuild_the_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = Unlocking(folder, refuse=True)
            result, builds = self.run_rounds(fake, folder)
        self.assertEqual(builds, [])
        self.assertEqual(result["evidence_rebuilds"], 0)
        self.assertEqual(result["refused"], 2)          # asked twice, refused twice, nothing rebuilt

    def test_a_question_reading_the_traced_areas_gets_them_rebuilt_first_and_only_when_stale(self):
        builds = []
        with patch.object(reviewer_room_geometry_service, "current_traced_areas", return_value={"a": {"area_m2": 9}}) as areas:
            with tasks_service.deferred_evidence_rebuild(lambda: builds.append(1)) as state:
                tasks_service.evidence_changed(object(), {"review_dir": "x"})
                tasks_service.evidence_changed(object(), {"review_dir": "x"})
                self.assertEqual(builds, [])                                 # marked stale, not rebuilt
                self.assertEqual(tasks_service._current_traced_areas(Path("x")), {"a": {"area_m2": 9}})
                tasks_service._current_traced_areas(Path("x"))
                self.assertEqual(builds, [1])                                # rebuilt once, before the first read
                tasks_service.ensure_evidence_current()
                self.assertEqual((builds, state["builds"]), ([1], 1))       # nothing stale at the end
            self.assertEqual(areas.call_count, 2)
        # Outside a drawing run the evidence is rebuilt at once, as before.
        with patch("backend.phase1_supervisor_service._build_calculation_evidence") as build:
            tasks_service.evidence_changed("web", {"review_dir": "x"})
        build.assert_called_once_with("web", {"review_dir": "x"})

    def test_a_run_that_stops_on_an_error_still_rebuilds_for_the_answers_it_applied(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = Unlocking(folder, rounds=3)
            original = fake.post

            def post(web, project, data):
                if data["action"] == "run_all" and len(fake.applied) == 2:
                    raise RuntimeError("builder failed")
                return original(web, project, data)
            fake.post = post
            builds = []
            with self.assertRaises(RuntimeError):
                self.run_rounds(fake, folder, builds)
            self.assertEqual(fake.applied, ["kitchen-0", "kitchen-1"])
            self.assertEqual(builds, [2])


class ParallelSendingTests(unittest.TestCase):
    def test_a_rounds_questions_are_sent_together_but_never_more_than_the_limit_at_once(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = BatchTasks(folder, count=7)
            lock, active, maximum = threading.Lock(), [0], [0]

            class Slow:
                model = "fake"

                def propose(self, prompt, image_paths=()):
                    with lock:
                        active[0] += 1
                        maximum[0] = max(maximum[0], active[0])
                    time.sleep(0.05)
                    with lock:
                        active[0] -= 1
                    return {"ok": True}, {}
            started = time.monotonic()
            with patch.dict(os.environ, {"ARCHIE_AI_PARALLEL_CALLS": "3"}), \
                    patch.object(tasks_service, "post", fake.post), patch.object(tasks_service, "_all_current", fake.all_current), \
                    patch.object(tasks_service, "_current_task", fake.current), patch.object(tasks_service, "_task_dir", fake.task_dir), \
                    patch.object(drawing, "MAX_ROUNDS", 1):
                drawing.answer_all(None, {"id": "j", "review_dir": folder}, provider_for=lambda task: Slow())
            elapsed = time.monotonic() - started
        self.assertEqual(maximum[0], 3)
        self.assertLess(elapsed, 7 * 0.05)                  # not one after another
        self.assertEqual(fake.applied, [f"kitchen-{index}" for index in range(7)])   # applied in task order


class OncePerFileVersionTests(unittest.TestCase):
    def test_the_plan_choice_is_worked_out_once_while_its_files_are_unchanged(self):
        root = Path(tempfile.mkdtemp())
        for name in tasks_service._PAGE_SELECTION_FILES:
            touch(root / name, "{}")
        with patch.object(tasks_service, "_compute_p0_page_selection",
                          side_effect=lambda root: {"selected": [{"page": 3}], "skipped": [], "notes": []}) as compute:
            first = tasks_service._p0_page_selection(root)
            first["selected"].append({"page": 99})                   # a caller's change doesn't reach the next one
            self.assertEqual(tasks_service._p0_main_geometry_pages(root), [{"page": 3}])
            self.assertEqual(compute.call_count, 1)
            touch(root / "page_inventory.json", '{"pages": {}}')
            tasks_service._p0_page_selection(root)
            self.assertEqual(compute.call_count, 2)

    def test_north_words_are_read_once_per_pdf_version(self):
        folder = Path(tempfile.mkdtemp())
        pdf = folder / "set.pdf"
        touch(pdf, "pdf one")
        with patch.object(autonomous_tasks, "_read_north_word_points", return_value=[(0.5, 0.9)]) as read:
            self.assertEqual(autonomous_tasks.north_word_points(pdf, 2), [(0.5, 0.9)])
            autonomous_tasks.north_word_points(pdf, 2)
            self.assertEqual(read.call_count, 1)
            autonomous_tasks.north_word_points(pdf, 3)
            touch(pdf, "pdf two, changed")
            autonomous_tasks.north_word_points(pdf, 2)
            self.assertEqual(read.call_count, 3)
        self.assertEqual(autonomous_tasks.north_word_points(folder / "missing.pdf", 1), [])

    def test_trace_checks_use_each_vector_pages_fingerprint_worked_out_once(self):
        root = Path(tempfile.mkdtemp())
        pages = [{"page": 1, "lines": [1, 2]}, {"page": 2, "lines": [3]}]
        touch(root / "vector_geometry.json", json.dumps({"geometry_key_points": {"pages": pages}}))
        paths = reviewer_room_geometry_service._paths({"review_dir": str(root)})
        with patch.object(reviewer_room_geometry_service, "_vector_pages", wraps=reviewer_room_geometry_service._vector_pages) as read:
            first = reviewer_room_geometry_service._vector_page_fingerprints(paths)
            reviewer_room_geometry_service._vector_page_fingerprints(paths)
            self.assertEqual(read.call_count, 1)
        self.assertEqual(first, {1: reviewer_room_geometry_service._page_fp(pages[0]),
                                 2: reviewer_room_geometry_service._page_fp(pages[1])})
        records = [{"page": page, "source_fingerprints": {"source_pdf": "pdf", "vector_page": fp}}
                   for page, fp in ((1, first[1]), (2, "stale"), (3, reviewer_room_geometry_service._page_fp({})))]
        artifact = {"records": records}
        # The same records as from the pages themselves.
        self.assertEqual(reviewer_room_geometry.active_records(artifact, "pdf", [], page_fingerprints=first),
                         reviewer_room_geometry.active_records(artifact, "pdf", pages))
        self.assertEqual([row["page"] for row in reviewer_room_geometry.active_records(artifact, "pdf", pages)], [1, 3])

    def test_window_tag_words_are_read_once_per_pdf_version(self):
        from backend import house_windows_service

        class Page:
            reads = 0

            def extract_words(self, use_text_flow=False):
                Page.reads += 1
                return [{"text": "W1", "x0": 1.0}]

        class Doc:
            pages = [Page(), Page()]

            def close(self):
                pass
        pdf = Path(tempfile.mkdtemp()) / "set.pdf"
        touch(pdf, "pdf")
        with patch("pdfplumber.open", return_value=Doc()) as opened:
            close, read = house_windows_service._pdf_pages(pdf)
            words = read.flow(2)
            words[0]["text"] = "changed by a caller"
            close()
            close, read = house_windows_service._pdf_pages(pdf)
            self.assertEqual(read.flow(2), [{"text": "W1", "x0": 1.0}])
            close()
        self.assertEqual(Page.reads, 1)
        self.assertEqual(opened.call_count, 1)          # the second placement didn't open the PDF at all


class SiteEvidenceTests(unittest.TestCase):
    def test_site_text_joined_from_two_excerpts_keeps_the_cited_pages_excerpt_instead_of_stopping_the_run(self):
        # The checker adds the lot/DP printed elsewhere to the address, so no one excerpt holds the whole text; this
        # ended the drawing step with StopIteration (found replaying Turramurra, 2026-11-19).
        root = Path(tempfile.mkdtemp())
        record = {"run_id": "r", "reply_hash": "h", "input_fingerprint": "f", "quality_label": "AI-determined",
                  "accuracy": {"auto_apply": True},
                  "packet": {"excerpts": [{"page": 1, "text": "139A Burns Road Turramurra"},
                                          {"page": 2, "text": "Site is Lot 2 DP 123456"}]}}
        validated = {"site": {"text": "139A Burns Road Turramurra Lot 2 DP 123456", "page": 1, "kind": "street_address"},
                     "consultant_addresses": []}
        with patch.object(autonomous_tasks, "site_cross_check", return_value={"status": "agrees"}):
            applied = tasks_service._apply_p1(root, {"review_dir": str(root)}, record, validated)
        self.assertEqual(applied["applied_value"]["evidence"], "139A Burns Road Turramurra")
        self.assertEqual(applied["applied_value"]["site_text"], "139A Burns Road Turramurra Lot 2 DP 123456")


class SharedPageContextTests(unittest.TestCase):
    def test_a_loaded_plan_page_is_shared_by_later_operations_until_its_cache_is_removed(self):
        from PIL import Image
        root = Path(tempfile.mkdtemp())
        pdf = root / "set.pdf"
        touch(pdf, "pdf")
        touch(root / "ai_input.json", json.dumps({"source_pdf": str(pdf)}))
        builds = []

        def build(page):
            builds.append(page)
            return {"objects": [{"style": ("a",), "points": [(0, 0), (1, 1)]}], "image": Image.new("RGB", (4, 4)),
                    "viewport": (0, 0, 4, 4)}
        with page_analysis_cache.operation(root, code_fingerprint="code") as cache:
            first = cache.get(1, lambda: build(1))
            cache.get(2, lambda: build(2))
        with page_analysis_cache.operation(root, code_fingerprint="code") as cache:
            again = cache.get(1, lambda: build(1))
            self.assertIs(again, first)                               # not reloaded from disk
            self.assertEqual(cache.stats["cache_hits"], {"1": 1})
        self.assertEqual(builds, [1, 2])
        import shutil
        shutil.rmtree(root / "page_analysis_cache")
        with page_analysis_cache.operation(root, code_fingerprint="code") as cache:
            cache.get(1, lambda: build(1))
        self.assertEqual(builds, [1, 2, 1])                           # a removed cache is built again


if __name__ == "__main__":
    unittest.main()
