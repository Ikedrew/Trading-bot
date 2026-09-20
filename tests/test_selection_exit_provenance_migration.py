"""Focused governed-provenance contract for Repair 1B.3 targets."""
from __future__ import annotations

from copy import deepcopy

import pytest

from research_engine.control_plane.evidence_provenance import (
    CURRENT,
    evidence_digest,
    validate_evidence_provenance,
)
from research_engine.control_plane.models import ReportValidity
from research_engine.control_plane.report_resolver import resolve_report_validity
from research_engine.experiments import exit_management, selection_research


def _shadow(
    index: int,
    *,
    horizon: str = "SCALP",
    shadow_type: str = "PRIMARY_HORIZON_SIMULATION",
    pnl: float = 1.0,
    epoch: str = "CURRENT",
) -> dict:
    return {
        "schema_version": "shadow_trades_v1",
        "epoch": epoch,
        "identity": {
            "shadow_trade_id": f"nshadow_{index:016x}_{horizon}",
            "canonical_opportunity_id": f"opp-{index}",
            "entity_id": f"entity-{index}",
            "correlation_id": f"corr-{index}",
            "symbol": "EURUSD",
            "shadow_type": shadow_type,
            "evaluated_horizon": horizon,
            "trade_horizon": horizon,
        },
        "decision_snapshot": {
            "strategy": "REVERSAL",
            "pattern": "HAMMER",
            "h4_regime": "TRENDING",
            "market_phase": "IMPULSE" if index % 2 else "PULLBACK",
            "direction": "BUY",
        },
        "simulated_outcome": {
            "pnl_r_multiple": pnl,
            "mfe_r": max(pnl, 0.5),
            "mae_r": -0.4,
            "exit_reason": "take_profit" if pnl > 0 else "stop_loss",
            "bars_held": 10,
        },
    }


def _shadow_population(opportunities: int = 35) -> list[dict]:
    rows: list[dict] = []
    for index in range(opportunities):
        rows.extend((
            _shadow(index, pnl=1.0),
            _shadow(index, horizon="INTRADAY", shadow_type="HORIZON_ALTERNATIVE", pnl=0.2),
            _shadow(index, horizon="EXTENDED", shadow_type="HORIZON_ALTERNATIVE", pnl=-0.5),
        ))
    return rows


def _horizon_candidates(count: int = 35, *, epoch: str = "CURRENT") -> list[dict]:
    return [
        {
            "schema_version": "horizon_candidates_v1",
            "epoch": epoch,
            "candidate_id": f"horizon-{index}",
            "canonical_opportunity_id": f"opp-{index}",
            "selection_status": "SELECTED",
            "horizon": "SCALP",
        }
        for index in range(count)
    ]


def _strategy_candidates(count: int = 36, *, epoch: str = "CURRENT") -> list[dict]:
    return [
        {
            "schema_version": "strategy_candidates_v1",
            "epoch": epoch,
            "candidate_id": f"strategy-{index}",
            "canonical_opportunity_id": f"opp-{index}",
            "strategy_family": "REVERSAL",
            "confidence": 0.3 + (index % 3) * 0.3,
            "rank": 1,
            "selected": True,
        }
        for index in range(count)
    ]


def _provenance(report: dict) -> dict:
    return report["fingerprint"]["evidence_provenance"]


def _assert_current(report: dict) -> None:
    assert validate_evidence_provenance(_provenance(report))[:2] == (True, CURRENT)
    assert report["fingerprint"]["epoch"] == CURRENT


@pytest.mark.parametrize(
    "runner",
    (selection_research.run_s2, selection_research.run_s3, selection_research.run_s4),
)
def test_selection_targets_prove_pre_transform_current_population(runner):
    current = _shadow_population()
    stale = _shadow(999, epoch="LEGACY", pnl=100.0)
    report = runner(shadow_trades=[*current, stale])

    _assert_current(report)
    component = _provenance(report)["components"][0]
    assert component["records_used"] == len(current)
    assert component["records_excluded"] == 1
    assert component["digest"] == evidence_digest(current)


def test_stale_supplied_selection_evidence_cannot_be_stamped_current():
    report = selection_research.run_s2(
        shadow_trades=_shadow_population(12)
        + [_shadow(999, epoch="LEGACY", pnl=100.0)]
    )
    assert _provenance(report)["records_excluded"] == 1
    _assert_current(report)

    stale_only = selection_research.run_s2(
        shadow_trades=[_shadow(999, epoch="LEGACY")]
    )
    assert stale_only["status"] == "INSUFFICIENT_DATA"
    assert stale_only["fingerprint"]["epoch"] == "UNVERIFIED"


def test_horizon1_represents_both_join_inputs_and_fails_when_one_is_stale():
    shadows = _shadow_population()
    candidates = _horizon_candidates()
    stale_candidate = _horizon_candidates(1, epoch="LEGACY")[0]
    stale_candidate["candidate_id"] = "horizon-stale"
    stale_candidate["canonical_opportunity_id"] = "opp-stale"
    report = selection_research.run_horizon1(
        shadow_trades=shadows,
        horizon_candidates=[*candidates, stale_candidate],
    )

    _assert_current(report)
    components = {item["source"]: item for item in _provenance(report)["components"]}
    assert set(components) == {"shadow_trades", "horizon_candidates"}
    assert components["shadow_trades"]["digest"] == evidence_digest(shadows)
    assert components["horizon_candidates"]["digest"] == evidence_digest(candidates)
    assert components["horizon_candidates"]["records_excluded"] == 1
    assert report["overall"]["selection_engine_agreement"]["checked"] == 35

    stale = selection_research.run_horizon1(
        shadow_trades=shadows,
        horizon_candidates=_horizon_candidates(epoch="LEGACY"),
    )
    assert stale["status"] == "COMPLETE"
    assert stale["fingerprint"]["epoch"] == "UNVERIFIED"


def test_strat1_join_uses_two_proven_components_and_exact_lineage_population():
    candidates = _strategy_candidates()
    shadows = [_shadow(index, pnl=float(index % 3)) for index in range(36)]
    report = selection_research.run_strat1(
        strategy_candidates=candidates, shadow_trades=shadows,
    )

    _assert_current(report)
    components = {item["source"]: item for item in _provenance(report)["components"]}
    assert set(components) == {"strategy_candidates", "shadow_trades"}
    assert components["strategy_candidates"]["digest"] == evidence_digest(candidates)
    assert components["shadow_trades"]["digest"] == evidence_digest(shadows)
    assert report["overall"]["matched_pairs"] == 36

    stale_candidates = deepcopy(candidates)
    for item in stale_candidates:
        item["epoch"] = "LEGACY"
    stale = selection_research.run_strat1(
        strategy_candidates=stale_candidates, shadow_trades=shadows,
    )
    assert stale["status"] == "INSUFFICIENT_DATA"
    assert stale["fingerprint"]["epoch"] == "UNVERIFIED"


@pytest.mark.parametrize("runner", (exit_management.run_ex3, exit_management.run_ex4))
def test_exit_targets_attest_before_flattening_and_exclude_stale(monkeypatch, runner):
    current = [_shadow(index, pnl=1.0 if index % 2 else -0.5) for index in range(200)]
    stale = _shadow(999, epoch="LEGACY", pnl=100.0)
    monkeypatch.setattr(
        "research_engine.data_access.shadow_runtime_ingestion.ingest_completed_shadow_trades",
        lambda: [*current, stale],
    )

    report = runner()
    _assert_current(report)
    component = _provenance(report)["components"][0]
    assert report["status"] == "COMPLETE"
    assert report["overall"]["sample_size"] == 200
    assert component["records_used"] == 200
    assert component["records_excluded"] == 1
    assert component["digest"] == evidence_digest(current)


def test_all_seven_targets_emit_structured_provenance(monkeypatch):
    shadows = _shadow_population()
    exit_rows = [
        _shadow(index, pnl=1.0 if index % 2 else -0.5)
        for index in range(200)
    ]
    monkeypatch.setattr(
        "research_engine.data_access.shadow_runtime_ingestion.ingest_completed_shadow_trades",
        lambda: exit_rows,
    )
    reports = {
        "S2": selection_research.run_s2(shadow_trades=shadows),
        "S3": selection_research.run_s3(shadow_trades=shadows),
        "S4": selection_research.run_s4(shadow_trades=shadows),
        "HORIZON-1": selection_research.run_horizon1(
            shadow_trades=shadows, horizon_candidates=_horizon_candidates(),
        ),
        "STRAT-1": selection_research.run_strat1(
            strategy_candidates=_strategy_candidates(),
            shadow_trades=[_shadow(index) for index in range(36)],
        ),
        "EX3": exit_management.run_ex3(),
        "EX4": exit_management.run_ex4(),
    }
    assert set(reports) == {"S2", "S3", "S4", "HORIZON-1", "STRAT-1", "EX3", "EX4"}
    for report in reports.values():
        _assert_current(report)


def test_resolver_accepts_real_current_provenance_but_keeps_scientific_gates(monkeypatch):
    rows = [_shadow(index) for index in range(200)]
    monkeypatch.setattr(
        "research_engine.data_access.shadow_runtime_ingestion.ingest_completed_shadow_trades",
        lambda: rows,
    )
    report = exit_management.run_ex3()
    validity, _ = resolve_report_validity(
        "ex3_tp_distance.json", report, expected_question_id="EX3",
    )
    assert validity == ReportValidity.VALID_CURRENT

    blocked = deepcopy(report)
    blocked["warnings"].append("EPOCH_WARNING: synthetic scientific blocker")
    blocked_validity, _ = resolve_report_validity(
        "ex3_tp_distance.json", blocked, expected_question_id="EX3",
    )
    assert blocked_validity == ReportValidity.INVALIDATED


def test_non_target_exit_reports_remain_unverified(monkeypatch):
    rows = [_shadow(index) for index in range(40)]
    monkeypatch.setattr(
        "research_engine.data_access.shadow_runtime_ingestion.ingest_completed_shadow_trades",
        lambda: rows,
    )
    assert exit_management.run_ex1()["fingerprint"]["epoch"] == "UNVERIFIED"
    assert exit_management.run_ex2()["fingerprint"]["epoch"] == "UNVERIFIED"
