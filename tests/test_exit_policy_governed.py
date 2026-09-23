from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from research_engine.experiments import exit_policy_governed as governed
from research_engine.registry.exit_policy_adjudication import CANDIDATE_POLICIES_V1


def _row(test_id, opportunity, horizon, value, shadow=None):
    shadow = shadow or f"shadow-{opportunity}-{horizon}"
    return {
        "test_id": test_id,
        "lifecycle_identity": (shadow, opportunity, horizon),
        "canonical_opportunity_id": opportunity,
        "value": value,
    }


def test_cr0_clustered_covariance_and_ci():
    result = governed.clustered_cr0_cell_means([
        _row("t", "o1", "SCALP", 1.0),
        _row("t", "o2", "SCALP", 3.0),
    ], ("t",))[0]
    assert result["weighted_effect_estimate"] == 2.0
    assert result["standard_error"] == pytest.approx(2 ** -0.5)
    assert result["confidence_interval_95"] == pytest.approx([
        2 - governed.CLUSTERED_INFERENCE["critical_value"] / 2 ** 0.5,
        2 + governed.CLUSTERED_INFERENCE["critical_value"] / 2 ** 0.5,
    ])


def test_horizon_weighting_sums_to_one_per_opportunity():
    result = governed.clustered_cr0_cell_means([
        _row("t", "o1", "SCALP", 0.0),
        _row("t", "o1", "INTRADAY", 2.0),
        _row("t", "o2", "SCALP", 3.0),
    ], ("t",))[0]
    assert result["weighted_effect_estimate"] == 2.0  # mean of opportunity means 1 and 3


def test_inference_is_exactly_reorder_invariant():
    rows = [
        _row("t", "o1", "SCALP", 0.1),
        _row("t", "o1", "INTRADAY", 0.2),
        _row("t", "o2", "SCALP", 0.3),
    ]
    assert governed.clustered_cr0_cell_means(rows, ("t",)) == (
        governed.clustered_cr0_cell_means(reversed(rows), ("t",))
    )


def test_account_fanout_or_duplicate_fails_closed():
    rows = [_row("t", "o1", "SCALP", 1.0), _row("t", "o1", "SCALP", 1.0)]
    with pytest.raises(governed.GovernedAnalysisError, match="duplicate"):
        governed.clustered_cr0_cell_means(rows, ("t",))


def test_cluster_must_be_canonical_opportunity():
    row = _row("t", "o1", "SCALP", 1.0)
    row["canonical_opportunity_id"] = "other"
    with pytest.raises(governed.GovernedAnalysisError, match="cluster identity"):
        governed.clustered_cr0_cell_means([row], ("t",))


def test_non_estimable_required_test_fails_closed():
    with pytest.raises(governed.GovernedAnalysisError, match="no eligible rows"):
        governed.clustered_cr0_cell_means([
            _row("a", "o1", "SCALP", 0.0),
            _row("a", "o2", "SCALP", 0.0),
        ], ("a", "b"))


def test_normal_inference_and_degenerate_rule_are_deterministic():
    assert governed._normal_two_sided_p(0.0, 0.0) == 1.0
    assert governed._normal_two_sided_p(1.0, 0.0) == 0.0
    assert governed._normal_two_sided_p(1.0, 1.0) == pytest.approx(0.31731050786291415)


def test_holm_adjustment_and_frozen_tie_order():
    adjusted = governed.holm_adjust([("a", .01), ("b", .01), ("c", .04)], ("a", "b", "c"))
    assert list(adjusted) == ["a", "b", "c"]
    assert adjusted == pytest.approx({"a": .03, "b": .03, "c": .04})


def test_incomplete_holm_family_fails_closed():
    with pytest.raises(governed.GovernedAnalysisError, match="incomplete"):
        governed.holm_adjust([("a", .01)], ("a", "b"))


def _populations(n=200, *, candidate_r=-0.5, baseline_r=-0.5, mfe=1.0,
                 baseline_reason="timeout", candidate_reason="timeout"):
    baseline_records, candidate_records = [], []
    for index in range(n):
        identity = (f"s{index}", f"o{index}", "SCALP")
        replay = SimpleNamespace(
            pnl_r_multiple=baseline_r,
            exit_reason=baseline_reason,
        )
        baseline_records.append(SimpleNamespace(lifecycle_identity=identity, replay=replay))
        for policy in CANDIDATE_POLICIES_V1:
            candidate_records.append(SimpleNamespace(
                lifecycle_identity=identity,
                canonical_opportunity_id=identity[1],
                candidate_policy_id=policy["policy_id"],
                candidate_r=candidate_r,
                baseline_window_mfe_r=mfe,
                exit_reason=candidate_reason,
            ))
    path = SimpleNamespace(
        provenance={"digest": "path"},
        summary=SimpleNamespace(
            total_completed_lifecycles=n,
            exclusions_by_reason={},
        ),
    )
    reproduction = SimpleNamespace(
        records=tuple(baseline_records), exclusions=(), provenance={"digest": "base"},
    )
    candidate = SimpleNamespace(
        records=tuple(candidate_records), exclusions=(),
        provenance={
            "digest": "candidate", "candidate_exclusion_digest": "empty",
        },
        summary=SimpleNamespace(
            candidate_eligible_lifecycles=n,
            distinct_eligible_opportunities=n,
            exclusions_by_reason={},
        ),
    )
    return candidate, reproduction, path


@pytest.fixture(autouse=True)
def _bypass_foundation_fixture_validation(monkeypatch):
    # Endpoint tests use minimal immutable stand-ins; foundation integrity has
    # its own production validator and candidate-replay focused test suite.
    monkeypatch.setattr(governed, "_validate_foundations", lambda *args: None)


def test_ex1_exact_nine_test_endpoint_and_valid_null_complete():
    report = governed.analyse_ex1(*_populations())
    assert report["status"] == "COMPLETE"
    assert len(report["overall"]["tests"]) == 9
    assert {test["weighted_effect_estimate"] for test in report["overall"]["tests"]} == {0.0}
    assert all(test["interpretation"] == "NOT_SUPPORTED" for test in report["overall"]["tests"])


def test_ex1_positive_raw_mean_alone_is_not_improvement():
    candidate, reproduction, path = _populations()
    candidate.records[0].candidate_r = 0.5
    report = governed.analyse_ex1(candidate, reproduction, path)
    first = report["overall"]["tests"][0]
    assert first["weighted_effect_estimate"] > 0
    assert first["interpretation"] == "NOT_SUPPORTED"


def test_ex1_requires_adjusted_significance_and_positive_lower_ci():
    tests = [{
        "test_identity": f"EX1:{policy}", "weighted_effect_estimate": 1.0,
        "confidence_interval_95": [-0.1, 2.1], "raw_two_sided_p_value": .001,
    } for policy in governed.CANDIDATE_POLICY_IDS]
    family = tuple(test["test_identity"] for test in tests)
    governed._apply_holm_and_interpret(tests, family, "EX1")
    assert all(test["holm_adjusted_p_value"] <= .05 for test in tests)
    assert all(test["interpretation"] == "NOT_SUPPORTED" for test in tests)


def test_ex2_only_three_trailing_policies_and_exact_normalized_endpoint():
    report = governed.analyse_ex2(*_populations(candidate_r=0.5, baseline_r=-0.5, mfe=2.0))
    tests = report["overall"]["tests"]
    assert len(tests) == 3
    assert [test["policy_identity"] for test in tests] == list(governed.TRAILING_POLICY_IDS)
    assert {test["weighted_effect_estimate"] for test in tests} == {0.5}


def test_ex2_mfe_must_be_strictly_positive():
    with pytest.raises(governed.GovernedAnalysisError, match="no eligible rows"):
        governed.analyse_ex2(*_populations(mfe=0.0))


def test_ex2_valid_null_can_complete():
    report = governed.analyse_ex2(*_populations())
    assert report["status"] == "COMPLETE"
    assert len(report["provenance"]["holm_family_order"]) == 3


def test_ex9_exact_endpoints_strict_positive_and_18_test_family():
    report = governed.analyse_ex9(*_populations(candidate_r=0.0))
    tests = report["overall"]["tests"]
    assert report["status"] == "COMPLETE"
    assert len(tests) == 18
    assert len(report["provenance"]["holm_family_order"]) == 18
    assert {test["weighted_effect_estimate"] for test in tests[9:]} == {0.0}


def test_ex9_timeout_indicator_treats_time_cap_as_timeout():
    report = governed.analyse_ex9(*_populations(candidate_reason="time_cap"))
    assert {test["weighted_effect_estimate"] for test in report["overall"]["tests"][:9]} == {0.0}


def test_ex9_endpoint_b_exact_population():
    with pytest.raises(governed.GovernedAnalysisError, match="no eligible rows"):
        governed.analyse_ex9(*_populations(baseline_r=0.1))


def test_ex9_endpoint_b_thirty_opportunity_gate_waits():
    report = governed.analyse_ex9(*_populations(n=29))
    assert report["status"] == "WAITING_DATA"
    assert "ENDPOINT_B_DISTINCT_COUNT_BELOW_REQUIRED" in report["provenance"]["readiness"]["blockers"]


def test_ex9_required_non_estimable_member_fails_whole_family():
    candidate, reproduction, path = _populations()
    candidate.records = tuple(
        row for row in candidate.records
        if row.candidate_policy_id != governed.CANDIDATE_POLICY_IDS[-1]
    )
    with pytest.raises(governed.GovernedAnalysisError, match="no eligible rows"):
        governed.analyse_ex9(candidate, reproduction, path)


def test_insufficient_common_sample_is_waiting_data():
    report = governed.analyse_ex1(*_populations(n=30))
    assert report["status"] == "WAITING_DATA"


def test_report_reorder_invariance_and_digest_sensitivity():
    candidate, reproduction, path = _populations()
    first = governed.analyse_ex1(candidate, reproduction, path)
    candidate.records = tuple(reversed(candidate.records))
    second = governed.analyse_ex1(candidate, reproduction, path)
    assert first["provenance"]["analytical_digest"] == second["provenance"]["analytical_digest"]
    second["overall"]["tests"][0]["weighted_effect_estimate"] = 99
    valid, reason = governed.validate_governed_exit_report(second, "EX1")
    assert not valid and "digest" in reason


def test_valid_report_and_status_are_cryptographically_bound():
    report = governed.analyse_ex9(*_populations())
    assert governed.validate_governed_exit_report(report, "EX9")[0]
    report["status"] = "BLOCKED"
    assert not governed.validate_governed_exit_report(report, "EX9")[0]


def test_only_target_registry_runners_use_governed_module():
    from research_engine.registry.research_question_registry import REGISTRY_BY_ID

    for question_id in ("EX1", "EX2", "EX9"):
        assert REGISTRY_BY_ID[question_id].runner_module == governed.__name__
    for question_id in ("EX5", "EX6", "EX7", "EX8", "EX10"):
        assert REGISTRY_BY_ID[question_id].runner_module != governed.__name__
