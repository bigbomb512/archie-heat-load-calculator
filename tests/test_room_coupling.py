import unittest

from ai.room_coupling import (
    empty_room_coupling_method_gate,
    room_coupling_gate_is_approved,
    solve_dynamic_partition,
    validate_coupling_record,
    validate_room_coupling_method_gate,
)
from ai.parity_harness import compare_stage6_benchmark_case, empty_benchmark_case


def citation(reference):
    return [{"reference": reference, "page": 1, "excerpt": "Reviewed method"}]


def record():
    return {
        "coupling_id": "partition-1-coupling",
        "surface_id": "partition-1",
        "owner_room_id": "room-a",
        "adjacent_room_id": "room-b",
        "owner_zone_id": "zone-a",
        "adjacent_zone_id": "zone-b",
        "area_m2": 10,
        "r_owner_m2k_w": 1,
        "r_adjacent_m2k_w": 1,
        "r_partition_m2k_w": 2,
        "capacitance_owner_kj_m2k": 100,
        "capacitance_adjacent_kj_m2k": 100,
        "initial_owner_state_temperature_c": 24,
        "initial_adjacent_state_temperature_c": 30,
        "source": "Partition schedule",
        "citations": citation("P-1"),
        "review_status": "confirmed",
    }


class RoomCouplingTests(unittest.TestCase):
    def test_placeholder_gate_is_inactive(self):
        self.assertFalse(room_coupling_gate_is_approved(empty_room_coupling_method_gate()))

    def test_approved_gate_requires_metadata(self):
        gate = empty_room_coupling_method_gate()
        gate.update({
            "approval_status": "approved", "engineer_name": "A. Engineer",
            "engineer_credential": "CPEng", "approved_at": "2026-09-17",
            "method_citation": "RC coupling method", "citations": citation("M-1"),
        })
        self.assertTrue(room_coupling_gate_is_approved(validate_room_coupling_method_gate(gate)))

    def test_coupling_is_equal_and_opposite(self):
        result = solve_dynamic_partition(record(), 24, 30, gate_version="gate-1")
        self.assertEqual(result["status"], "calculated")
        self.assertAlmostEqual(result["owner_sensible_kw"], -result["adjacent_sensible_kw"])
        self.assertEqual(result["gate_version"], "gate-1")

    def test_bad_record_and_nonconvergence_fail_closed(self):
        bad = record()
        bad["citations"] = []
        with self.assertRaises(ValueError):
            validate_coupling_record(bad)
        result = solve_dynamic_partition(record(), 24, 30, tolerance_c=1e-12, max_iterations=1)
        self.assertEqual(result["status"], "blocked")

    def test_benchmark_comparison_never_validates(self):
        case = empty_benchmark_case("stage6-test")
        report = compare_stage6_benchmark_case(case, {}, report_is_current=True)
        self.assertFalse(report["stage6_validation"]["validated"])
        self.assertEqual(report["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
