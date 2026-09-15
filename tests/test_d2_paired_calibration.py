"""Focused D2 paired-calibration repair tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from research_engine.experiments.d2_paired_calibration import (
    D2_REPORT_FILENAME,
    analyse,
    build_paired_observations,
    chronological_split,
)
from research_engine.registry.definition_validator import (
    build_definitions_from_registry,
    get_question_health,
    validate_all_definitions,
)
from research_engine.registry.research_question_registry import REGISTRY, REGISTRY_BY_ID


def _shadow(index: int, *, opportunity: str | None = None, outcome: float | None = None, horizon: str = "SCALP") -> dict:
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=index)
    opportunity = opportunity or f"OPP-{index:04d}"
    return {
        "data_epoch": "CURRENT",
        "identity": {
            "canonical_opportunity_id": opportunity,
            "entity_id": f"ENTITY-{index:04d}",
            "shadow_trade_id": f"nshadow_{index:016x}",
            "symbol": "EURUSD",
        },
        "decision_snapshot": {
            "timestamp_decision_utc": timestamp.isoformat(),
            "h4_regime": "TRENDING",
            "market_phase": "IMPULSE",
            "h1_bias": "BULLISH",
            "pattern": "PIN_BAR",
            "strategy": "REVERSAL",
            "trade_horizon": horizon,
        },
        "simulated_outcome": {"pnl_r_multiple": outcome},
    }


def _decision(index: int, *, opportunity: str | None = None, probability: float | None = None, epoch: str = "CURRENT", offset_hours: int = 0) -> dict:
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=index + offset_hours)
    return {
        "data_epoch": epoch,
        "canonical_opportunity_id": opportunity or f"OPP-{index:04d}",
        "timestamp_utc": timestamp.isoformat(),
        "p_success": probability,
        "p_success_model_version": "heuristic_score_v1",
    }


def _population(count: int = 120):
    decisions, shadows = [], []
    for index in range(count):
        won = bool(index % 2)
        decisions.append(_decision(index, probability=1.0 if won else 0.0))
        shadows.append(_shadow(index, outcome=1.0 if won else -1.0))
    return decisions, shadows


def test_d2_pairs_one_observation_per_canonical_opportunity_without_fanout():
    decision = _decision(1, probability=0.8)
    first = _shadow(1, outcome=1.0, horizon="SCALP")
    duplicate = _shadow(1, outcome=1.0, horizon="SCALP")
    duplicate["identity"]["shadow_trade_id"] = "nshadow_ffffffffffffffff"
    alternative = _shadow(1, outcome=3.0, horizon="EXTENDED")
    alternative["identity"]["shadow_trade_id"] = "nshadow_eeeeeeeeeeeeeeee"
    rows, diagnostics = build_paired_observations([decision], [first, duplicate, alternative])
    assert len(rows) == 1
    assert rows[0]["outcome_r"] == 2.0
    assert diagnostics["repeated_rows_collapsed"] == 2


def test_missing_outcome_is_excluded_never_converted_to_loss():
    rows, diagnostics = build_paired_observations(
        [_decision(1, probability=0.7)], [_shadow(1, outcome=None)]
    )
    assert rows == []
    assert diagnostics["missing_outcomes_excluded"] == 1


def test_non_current_prediction_is_excluded():
    rows, diagnostics = build_paired_observations(
        [_decision(1, probability=0.7, epoch="LEGACY")], [_shadow(1, outcome=1.0)]
    )
    assert rows == []
    assert diagnostics["excluded_non_current_predictions"] == 1


def test_conflicting_prediction_and_post_decision_chronology_fail_closed():
    decisions, shadows = _population()
    conflicting = dict(decisions[3])
    conflicting["p_success"] = 0.4
    assert analyse([*decisions, conflicting], shadows)["status"] == "BLOCKED"
    late = list(decisions)
    late[3] = _decision(3, probability=1.0, offset_hours=1)
    assert analyse(late, shadows)["status"] == "BLOCKED"


def test_d2_uses_strict_later_unseen_chronological_validation():
    decisions, shadows = _population()
    paired, _ = build_paired_observations(decisions, shadows)
    discovery, validation = chronological_split(paired)
    assert len(discovery) == 72 and len(validation) == 48
    assert max(row["timestamp"] for row in discovery) < min(row["timestamp"] for row in validation)
    assert {row["canonical_opportunity_id"] for row in discovery}.isdisjoint(
        row["canonical_opportunity_id"] for row in validation
    )


def test_d2_reports_paired_brier_reliability_and_validated_calibration():
    decisions, shadows = _population()
    report = analyse(decisions, shadows)
    assert report["status"] == "COMPLETE"
    validation = report["overall"]["later_unseen_validation"]
    assert validation["brier_score"] == 0.0
    assert validation["expected_calibration_error"] == 0.0
    assert validation["sufficient_bins"] == 2
    assert report["overall"]["calibration_status"] == "VALIDATED_CALIBRATED"


def test_heuristic_score_v1_remains_uncalibrated_without_validating_evidence():
    report = analyse(
        [_decision(i, probability=0.8) for i in range(20)],
        [_shadow(i, outcome=1.0 if i % 2 else -1.0) for i in range(20)],
    )
    assert report["status"] == "WAITING_DATA"
    assert report["overall"]["model_version"] == "heuristic_score_v1"
    assert report["overall"]["calibration_status"] == "UNCALIBRATED"


def test_d2_has_unique_canonical_runner_report_and_complete_definition():
    question = REGISTRY_BY_ID["D2"]
    assert question.runner_module == "research_engine.experiments.d2_paired_calibration"
    assert question.runner_function == "run_d2"
    assert question.report_filename == D2_REPORT_FILENAME
    assert sum(item.report_filename == D2_REPORT_FILENAME for item in REGISTRY) == 1
    definitions = build_definitions_from_registry(REGISTRY)
    health = validate_all_definitions(definitions)
    assert get_question_health(health["D2"]) == "VALID"
    assert definitions["D2"].join_contract.join_keys == ("canonical_opportunity_id",)


def test_d2_control_plane_uses_paired_population_and_report_status():
    from research_engine.control_plane.evidence_resolver import EvidenceSnapshot, resolve_question_evidence
    from research_engine.control_plane.models import ReadinessStatus, ReportValidity, RunnerStatus
    from research_engine.control_plane.readiness import resolve_readiness

    decisions, shadows = _population()
    evidence = resolve_question_evidence(
        REGISTRY_BY_ID["D2"],
        EvidenceSnapshot({"decision_trace": decisions, "shadow_trades": shadows}),
    )
    assert evidence.usable_count == 120
    sample = next(item for item in evidence.requirements if item.name == "sample_size")
    assert sample.current == 120 and sample.satisfied is True
    waiting, _ = resolve_readiness(
        REGISTRY_BY_ID["D2"], evidence, RunnerStatus.READY,
        ReportValidity.VALID_CURRENT, "WAITING_DATA", {},
    )
    assert waiting == ReadinessStatus.WAITING_DATA


def test_d2_is_operational_while_rw3_remains_in_progress():
    from research_engine.registry.master_repair_ledger import (
        MASTER_REPAIR_LEDGER, REPAIR_WAVES, STRUCTURALLY_NON_OPERATIONAL_IDS,
        operational_baseline,
    )

    # After RW3.4 repaired D5, the derived baseline is 35/35. D2 itself is
    # unchanged; only the global count reflects D5 becoming operational.
    assert operational_baseline() == (35, 35)
    assert MASTER_REPAIR_LEDGER["D2"].structurally_operational
    assert REPAIR_WAVES["RW3"].implemented is False
    # RW3.4 repaired D5, so RW3's outstanding direct gain is now X5 only.
    assert set(REPAIR_WAVES["RW3"].direct_gain) == {"X5"}
    assert {"X5"} <= STRUCTURALLY_NON_OPERATIONAL_IDS
