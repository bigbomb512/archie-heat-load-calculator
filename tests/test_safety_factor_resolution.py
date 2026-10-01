from ai import safety_factor_resolution as policy


def test_normalizes_percentages_and_rejects_invalid_values():
    assert policy.normalize_factor(10, "percentage") == 1.1
    assert policy.normalize_factor(1.1) == 1.1
    for value in (0, 0.9, -1, float("nan")):
        try:
            policy.normalize_factor(value)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid safety factor was accepted")


def test_draft_fallback_is_explicit_and_final_rollup_is_applied_once():
    artifact = policy.resolve({})
    row = policy.policy_for(artifact, "cooling", preliminary=True)
    assert row["factor"] == 1.1
    assert row["origin"] == "controlled_preliminary_fallback"
    report = {"status": "draft", "scope_summary": {"complete_scope": True}, "included_scope_peak": {"subtotal_kw": 100.0}, "scenario_results": []}
    result = policy.apply_final_policy(report, row, mode="cooling", preliminary=True)
    assert result["raw_coincident_total_kw"] == 100.0
    assert result["safety_allowance_kw"] == 10.0
    assert result["final_design_total_kw"] == 110.0


def test_reviewed_policy_requires_approval_and_citation():
    artifact = policy.resolve({"cooling_safety_factor": {"factor": 1.15, "source": "brief", "citations": [{"reference": "brief p. 4"}]}})
    assert policy.policy_for(artifact, "cooling")["blocked"] is True
    artifact = policy.approve(artifact, "cooling", "Engineer", "2026-09-24", "final cooling total")
    row = policy.policy_for(artifact, "cooling")
    assert row["blocked"] is False


def test_approved_project_policy_uses_neutralized_room_factor_evidence_once():
    report = {
        "status": "review_ready",
        "scope_summary": {"complete_scope": True},
        "included_scope_peak": {"subtotal_kw": 100.0},
        "legacy_room_safety_factors": [{"room_id": "in-scope", "factor": 1.1}],
        "scenario_results": [{"rooms": [
            {"room_id": "in-scope", "status": "review_ready", "hours": [{"safety_factor": 1.0}]},
            {"room_id": "out-of-scope", "status": "blocked", "hours": []},
        ]}],
    }
    result = policy.apply_final_policy(report, {"factor": 1.1, "blocked": False})
    assert result["safety_policy_applied"] is True
    assert result["project_peak"]["final_design_total_kw"] == 110.0
    assert result["legacy_room_safety_factors"] == report["legacy_room_safety_factors"]


def test_policy_blocks_only_when_room_factor_survives_in_calculated_hours():
    report = {
        "status": "review_ready",
        "scope_summary": {"complete_scope": True},
        "included_scope_peak": {"subtotal_kw": 100.0},
        "scenario_results": [{"rooms": [{"room_id": "room-1", "hours": [{"safety_factor": 1.1}]}]}],
    }
    result = policy.apply_final_policy(report, {"factor": 1.1, "blocked": False})
    assert result["safety_policy_applied"] is False
    assert result["status"] == "blocked"
    assert any("compounded" in reason for reason in result["blocked_reasons"])


def test_preliminary_policy_also_blocks_actual_room_hour_compounding():
    report = {
        "status": "draft",
        "scope_summary": {"complete_scope": True},
        "included_scope_peak": {"subtotal_kw": 100.0},
        "scenario_results": [{"rooms": [{"room_id": "room-1", "hours": [{"safety_factor": 1.1}]}]}],
    }
    result = policy.apply_final_policy(
        report,
        {"factor": 1.1, "blocked": False, "origin": "controlled_preliminary_fallback"},
        preliminary=True,
    )
    assert result["safety_policy_applied"] is False
    assert result["status"] == "blocked"
    assert result["project_peak"] == {}
    assert any("compounded" in reason for reason in result["blocked_reasons"])


def main():
    tests = [
        test_normalizes_percentages_and_rejects_invalid_values,
        test_draft_fallback_is_explicit_and_final_rollup_is_applied_once,
        test_reviewed_policy_requires_approval_and_citation,
        test_approved_project_policy_uses_neutralized_room_factor_evidence_once,
        test_policy_blocks_only_when_room_factor_survives_in_calculated_hours,
        test_preliminary_policy_also_blocks_actual_room_hour_compounding,
    ]
    for test in tests:
        test()
        print(f"PASS - {test.__name__}")


if __name__ == "__main__":
    main()
