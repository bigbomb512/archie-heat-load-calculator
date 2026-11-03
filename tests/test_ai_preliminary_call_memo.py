"""One Assemble/Calculate works each derived value out once per saved state of the files it reads."""

import builtins
import io
import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from contextlib import ExitStack
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai import ai_preliminary, airflow_resolution, ceiling_volume_resolution, internal_gains_resolution, room_use_resolution
from backend import ai_preliminary_service as service


def frozen_clock():
    """Saved timestamps flow into the draft geometry's fingerprint, so two runs only compare with one clock."""
    stack = ExitStack()
    for module in (ai_preliminary, airflow_resolution, ceiling_volume_resolution, internal_gains_resolution,
                   room_use_resolution):
        stack.enter_context(patch.object(module, "now", return_value="2026-10-25T12:00:00+00:00"))
    return stack


def write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def bump(path, value):
    """Save a file again so its modification time and inode change even within one clock tick."""
    staged = path.with_suffix(".stage")
    write(staged, value)
    os.replace(staged, path)


def without_timestamps(value):
    if isinstance(value, dict):
        return {key: without_timestamps(item) for key, item in value.items() if key != "updated_at"}
    if isinstance(value, list):
        return [without_timestamps(item) for item in value]
    return value


class JobFolder:
    """The smallest job the resolvers run on: two local rooms, no drawings."""

    def __init__(self, root):
        self.root = Path(root)
        write(self.root / "building_evidence.json", {"spaces": []})
        write(self.root / "vision_response.json", {"result": {"auto_extraction": {"entities": []}}})
        write(self.root / "ai_preliminary_run.json", {
            "local_room_inference_proposal": {"rooms": [
                {"kind": "room", "room_id": "local:kitchen", "label": "Kitchen", "level_name": "Ground",
                 "area_m2": 28, "source_pages": [1]},
                {"kind": "room", "room_id": "local:dining", "label": "Dining", "level_name": "Ground",
                 "area_m2": 54, "source_pages": [1]},
            ]},
            "manual_placeholder_proposal": None,
        })
        self.paths = service._paths({"id": "memo", "review_dir": str(self.root)})

    def resolve_all(self):
        service._resolve_room_uses(self.paths, persist=True)
        service._resolve_ceiling_volumes(self.paths, persist=True)
        service._resolve_internal_gains(self.paths, persist=True)
        service._resolve_airflow(self.paths, persist=True)
        names = ("room_use", "ceiling_volume", "internal_gains", "airflow")
        return {name: without_timestamps(service._read(self.paths[name], {})) for name in names}


class OnceTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.root = Path(self.directory.name)
        write(self.root / "room_use_resolution.json", {"records": []})
        write(self.root / "airflow_resolution.json", {"records": []})
        write(self.root / "internal_gains_resolution.json", {"records": []})
        self.paths = {"root": self.root}
        self.calls = 0

    def tearDown(self):
        self.directory.cleanup()

    def compute(self):
        self.calls += 1
        return {"rooms": [{"label": "Kitchen"}]}

    def test_outside_one_computation_every_call_runs(self):
        service._once("value", self.paths, self.compute)
        service._once("value", self.paths, self.compute)
        self.assertEqual(self.calls, 2)

    def test_inside_one_computation_it_runs_once_and_hands_out_copies(self):
        with service._one_computation():
            first = service._once("value", self.paths, self.compute)
            first["rooms"].append({"label": "changed by a caller"})
            second = service._once("value", self.paths, self.compute)
            self.assertEqual(second, {"rooms": [{"label": "Kitchen"}]})
            self.assertIsNot(first, second)
            self.assertEqual(service._once("value", self.paths, self.compute), second)
        self.assertEqual(self.calls, 1)

    def test_arguments_and_names_are_part_of_the_key(self):
        with service._one_computation():
            service._once("value", self.paths, self.compute, "a")
            service._once("value", self.paths, self.compute, "b")
            service._once("other", self.paths, self.compute, "a")
        self.assertEqual(self.calls, 3)

    def test_saving_an_input_file_works_it_out_again(self):
        with service._one_computation():
            service._once("value", self.paths, self.compute)
            bump(self.root / "room_use_resolution.json", {"records": [{"room_id": "kitchen"}]})
            service._once("value", self.paths, self.compute)
            write(self.root / "new_input.json", {})
            service._once("value", self.paths, self.compute)
        self.assertEqual(self.calls, 3)

    def test_saving_a_file_no_memoised_value_reads_keeps_the_value(self):
        with service._one_computation():
            service._once("value", self.paths, self.compute)
            bump(self.root / "airflow_resolution.json", {"records": [{"airflow_id": "a"}]})
            bump(self.root / "internal_gains_resolution.json", {"records": [{"room_id": "kitchen"}]})
            service._once("value", self.paths, self.compute)
        self.assertEqual(self.calls, 1)

    def test_a_value_that_reads_an_excluded_file_puts_it_back(self):
        state = lambda paths: service._job_state(paths, include={"internal_gains_resolution.json"})
        with service._one_computation():
            service._once("gains", self.paths, self.compute, state=state)
            bump(self.root / "internal_gains_resolution.json", {"records": [{"room_id": "kitchen"}]})
            service._once("gains", self.paths, self.compute, state=state)
        self.assertEqual(self.calls, 2)

    def test_nested_blocks_share_one_memo_that_ends_with_the_outer_block(self):
        with service._one_computation():
            service._once("value", self.paths, self.compute)
            with service._one_computation():
                service._once("value", self.paths, self.compute)
            service._once("value", self.paths, self.compute)
        self.assertEqual(self.calls, 1)
        self.assertIsNone(service._CALL_MEMO.get())
        with service._one_computation():
            service._once("value", self.paths, self.compute)
        self.assertEqual(self.calls, 2)


class ResolverTests(unittest.TestCase):
    def test_resolvers_give_the_same_artifacts_and_prepare_rooms_fewer_times(self):
        original = service._prepare_preliminary_proposal
        counts = {}

        def counted(label):
            def prepare(*args, **kwargs):
                counts[label] = counts.get(label, 0) + 1
                return original(*args, **kwargs)
            return prepare

        with TemporaryDirectory() as plain_dir, TemporaryDirectory() as memo_dir, frozen_clock():
            with patch.object(service, "_prepare_preliminary_proposal", side_effect=counted("plain")):
                plain = JobFolder(plain_dir).resolve_all()
            with patch.object(service, "_prepare_preliminary_proposal", side_effect=counted("memo")), \
                    service._one_computation():
                memo = JobFolder(memo_dir).resolve_all()
        self.assertEqual(json.dumps(memo, sort_keys=True).replace(memo_dir, "<root>"),
                         json.dumps(plain, sort_keys=True).replace(plain_dir, "<root>"))
        self.assertLess(counts["memo"], counts["plain"])

    def test_memoised_values_read_none_of_the_files_left_out_of_their_key(self):
        with TemporaryDirectory() as directory:
            job = JobFolder(directory)
            job.resolve_all()
            for name in ("value_resolution.json", "safety_factor_resolution.json", "audit_log_head.json"):
                write(job.root / name, {"records": []})
            opened = []
            real_open = io.open

            def recording_open(file, *args, **kwargs):
                opened.append(Path(os.fsdecode(file)).name if isinstance(file, (str, bytes, os.PathLike)) else "")
                return real_open(file, *args, **kwargs)

            with patch.object(io, "open", recording_open), patch.object(builtins, "open", recording_open), \
                    service._one_computation():
                service._preliminary_geometry(job.paths)
                service._proposal_for_resolution(job.paths, equipment=False)
                service._proposal_for_resolution(job.paths)
                service._resolved_room_areas(job.paths)
                service._internal_gains_sources(job.paths)
            self.assertIn("room_use_resolution.json", opened)
            self.assertFalse(set(opened) & service._NOT_READ_BY_MEMOISED_VALUES, sorted(set(opened)))


if __name__ == "__main__":
    unittest.main()
