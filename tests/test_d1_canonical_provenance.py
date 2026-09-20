"""Focused D1 canonical-evidence and governed-provenance migration tests."""
from __future__ import annotations

from copy import deepcopy

import pytest

from research_engine.control_plane.evidence_provenance import (
    CURRENT,
    evidence_digest,
    validate_evidence_provenance,
)
from research_engine.control_plane.report_ownership import (
    canonical_report_owner,
    resolve_report_ownership,
)
from research_engine.control_plane.report_resolver import (
    ReportValidity,
    report_contains_authoritative_result,
    resolve_report_validity,
)
from research_engine.experiments import component_reward
from research_engine.experiments import experiment_base
from research_engine.registry.research_question_registry import REGISTRY_BY_ID


def _decision(index: int, *, epoch: str | None = None) -> dict:
    record = {
        "schema_version": "decision_trace_v1",
        "record_role": "immutable_diagnostic_projection",
        "authority": "projection_of_canonical_decision",
        "canonical_opportunity_id": f"opp-{index}",
        "observation_id": f"obs-{index}",
        "decision_id": f"dec-{index}",
        "entity_id": f"entity-{index}",
        "correlation_id": f"corr-{index}",
        "symbol": "EURUSD",
        "cycle_id": index,
        "timestamp_utc": f"2026-09-01T00:{index:02d}:00Z",
        "pattern_detected": True,
        "pattern_name": "ENGULFING",
        "regime": "TRENDING",
        "selected_strategy": "MEAN_REVERSION",
        "trade_horizon": "INTRADAY",
        "components": {
            "pattern_quality": 0.9 if index % 2 else 0.2,
            "bias_alignment": 0.2 if index % 2 else 0.9,
        },
        "score_neutral": 0.5,
        "score_strategy": 0.6,
    }
    if epoch is not None:
        record["data_epoch"] = epoch
    return record


def _shadow(index: int, *, epoch: str | None = None, primary: bool = True) -> dict:
    record = {
        "schema_version": "shadow_trades_v1",
        "source": "shadow_runtime_ingestion",
        "source_schema_version": "shadow_runtime_v1",
        "identity": {
            "trade_id": f"nshadow_{index:016x}",
            "shadow_trade_id": f"nshadow_{index:016x}",
            "canonical_opportunity_id": f"opp-{index}",
            "observation_id": f"obs-{index}",
            "entity_id": f"entity-{index}",
            "correlation_id": f"corr-{index}",
            "symbol": "EURUSD",
            "strategy_id": "MEAN_REVERSION",
            "evaluated_horizon": "INTRADAY",
            "trade_horizon": "INTRADAY",
            "shadow_type": (
                "PRIMARY_HORIZON_SIMULATION" if primary
                else "HORIZON_ALTERNATIVE"
            ),
        },
        "decision_snapshot": {
            "pattern": "ENGULFING",
            "h4_regime": "TRENDING",
            "trade_horizon": "INTRADAY",
        },
        "simulated_outcome": {
            "pnl_r_multiple": 1.5 if index % 2 else -1.0,
            "exit_reason": "take_profit" if index % 2 else "stop_loss",
            "bars_held": 10,
            "mfe_r": 1.8,
            "mae_r": -0.5,
        },
    }
    if epoch is not None:
        record["data_epoch"] = epoch
    return record


class _Source:
    def __init__(self, decisions: list[dict]) -> None:
        self.decisions = decisions
        self.calls: list[str] = []

    def read_dataset(self, name: str, **_kwargs):
        self.calls.append(name)
        if name != "decision_trace":
            raise AssertionError(f"D1 read non-canonical dataset {name!r}")
        return deepcopy(self.decisions)


def _run(monkeypatch: pytest.MonkeyPatch, decisions: list[dict], shadows: list[dict]):
    source = _Source(decisions)
    monkeypatch.setattr(
        "research_engine.data_access.s3_source.get_default_source",
        lambda: source,
    )
    monkeypatch.setattr(
        "research_engine.data_access.shadow_runtime_ingestion.ingest_completed_shadow_trades",
        lambda: deepcopy(shadows),
    )
    monkeypatch.setattr(experiment_base, "persist_report", lambda *_args, **_kwargs: None)
    return component_reward.run(), source


def _structured(report: dict) -> dict:
    return report["fingerprint"]["evidence_provenance"]


def test_d1_uses_only_canonical_sources_and_proves_exact_selected_populations(monkeypatch):
    current_decisions = [_decision(index) for index in range(1, 61)]
    stale_decision = _decision(90, epoch="LEGACY")
    current_shadows = [_shadow(index) for index in range(1, 61)]
    stale_shadow = _shadow(90, epoch="LEGACY")
    alternative = _shadow(1, primary=False)

    report, source = _run(
        monkeypatch,
        current_decisions + [stale_decision],
        current_shadows + [stale_shadow, alternative],
    )

    assert source.calls == ["decision_trace"]
    assert report["question_id"] == "D1"
    assert report["status"] == "COMPLETE"
    assert report["dataset"]["join_key"] == "canonical_opportunity_id"
    assert report["dataset"]["non_primary_shadow_records_excluded"] == 1
    provenance = _structured(report)
    valid, state, _ = validate_evidence_provenance(provenance)
    assert valid is True and state == CURRENT
    components = {item["source"]: item for item in provenance["components"]}
    assert set(components) == {"decision_trace", "shadow_trades"}
    assert components["decision_trace"]["records_used"] == 60
    assert components["decision_trace"]["records_excluded"] == 1
    assert components["decision_trace"]["digest"] == evidence_digest(current_decisions)
    assert components["shadow_trades"]["records_used"] == 60
    assert components["shadow_trades"]["records_excluded"] == 1
    assert components["shadow_trades"]["digest"] == evidence_digest(current_shadows)
    assert "research_shadow_trades" not in str(report)
    assert "shadow_runtime_v1" in report["dataset"]["source"]


def test_d1_stale_required_side_fails_closed(monkeypatch):
    report, _ = _run(
        monkeypatch,
        [_decision(index) for index in range(1, 7)],
        [_shadow(index, epoch="LEGACY") for index in range(1, 7)],
    )

    assert report["status"] == "INSUFFICIENT_DATA"
    assert report["fingerprint"]["epoch"] == "UNVERIFIED"
    validity, reason = resolve_report_validity(
        "q1_component_reward.json", report,
        expected_question_id="D1", accepted_question_ids=("Q1",),
    )
    assert validity == ReportValidity.STALE


def test_d1_duplicate_canonical_lineage_blocks_instead_of_silently_overwriting(monkeypatch):
    decisions = [_decision(index) for index in range(1, 7)]
    duplicate = deepcopy(decisions[0])
    duplicate["components"]["pattern_quality"] = 0.1

    report, _ = _run(
        monkeypatch, decisions + [duplicate], [_shadow(index) for index in range(1, 7)],
    )

    assert report["status"] == "BLOCKED"
    assert report["overall"]["duplicate_decision_opportunities"] == ["opp-1"]
    assert report["overall"]["matched_outcomes"] == 5


def test_d1_scientific_gate_and_component_result_are_preserved(monkeypatch):
    decisions = [_decision(index) for index in range(1, 61)]
    shadows = [_shadow(index) for index in range(1, 61)]
    expected = component_reward.run_component_reward(decisions, shadows)

    report, _ = _run(monkeypatch, decisions, shadows)

    assert report["overall"]["component_stats"] == expected.to_dict()["component_stats"]
    assert report["overall"]["best_predictor"] == expected.best_predictor

    insufficient, _ = _run(monkeypatch, decisions[:4], shadows[:4])
    assert insufficient["status"] == "INSUFFICIENT_DATA"
    assert insufficient["fingerprint"]["epoch"] == CURRENT
    validity, _ = resolve_report_validity(
        "q1_component_reward.json", insufficient,
        expected_question_id="D1", accepted_question_ids=("Q1",),
    )
    assert validity == ReportValidity.VALID_CURRENT
    assert report_contains_authoritative_result(insufficient, validity) is False


def test_d1_current_complete_resolves_for_d1_but_not_l3(monkeypatch):
    report, _ = _run(
        monkeypatch,
        [_decision(index) for index in range(1, 61)],
        [_shadow(index) for index in range(1, 61)],
    )

    validity, reason = resolve_report_validity(
        "q1_component_reward.json", report,
        expected_question_id="D1", accepted_question_ids=("Q1",),
    )
    assert validity == ReportValidity.VALID_CURRENT, reason
    assert canonical_report_owner("q1_component_reward.json") == "D1"
    assert resolve_report_ownership(
        "q1_component_reward.json", "D1", report_metadata=report,
    ).allowed is True
    assert resolve_report_ownership(
        "q1_component_reward.json", "L3", report_metadata=report,
    ).allowed is False
    assert REGISTRY_BY_ID["L3"].description != REGISTRY_BY_ID["D1"].description


def test_d1_join_never_uses_correlation_or_symbol_cycle_fallback():
    decision = _decision(1)
    shadow = _shadow(2)
    shadow["identity"]["correlation_id"] = decision["correlation_id"]
    shadow["identity"]["symbol"] = decision["symbol"]
    shadow["identity"]["cycle_id"] = decision["cycle_id"]

    result = component_reward.run_component_reward([decision], [shadow])

    assert result.decisions_with_outcome == 0
    assert result.unmatched_decisions == 1
