"""Focused provenance migration tests for E2/E4/E5/M2/M4/M6/M9/M10/L5."""
from __future__ import annotations

from copy import deepcopy
import json

import pytest

from research_engine.control_plane.evidence_provenance import (
    CURRENT,
    evidence_digest,
    validate_evidence_provenance,
)
from research_engine.control_plane.models import ReportValidity
from research_engine.control_plane.report_resolver import resolve_report_validity
from research_engine.control_plane import build_question_state
from research_engine.experiments import (
    edge_depth,
    legacy_canonical,
    m9_phase_pattern,
    m10_strategy_family_per_phase,
    market_research,
    out_of_sample_validation,
)


def _shadow(index: int, *, epoch: str = "CURRENT", pnl: float | None = None) -> dict:
    phases = ("IMPULSE", "PULLBACK", "CONSOLIDATION", "EXHAUSTION", "REVERSAL")
    patterns = ("HAMMER", "BULLISH_ENGULFING", "THREE_WHITE_SOLDIERS")
    value = float((index % 5) - 2) / 2 if pnl is None else pnl
    return {
        "schema_version": "shadow_trades_v1",
        "epoch": epoch,
        "identity": {
            "shadow_trade_id": f"nshadow_{index:016x}",
            "canonical_opportunity_id": f"opp-{index}",
            "entity_id": f"entity-{index}",
            "correlation_id": f"corr-{index}",
            "symbol": "EURUSD",
            "evaluated_horizon": "INTRADAY",
            "strategy_id": "REVERSAL",
            "entry_time": float(index + 1),
        },
        "decision_snapshot": {
            "strategy": "REVERSAL",
            "pattern": patterns[index % len(patterns)],
            "h4_regime": "TRENDING",
            "market_phase": phases[index % len(phases)],
            "direction": "BUY",
            "entry_time": float(index + 1),
            "score": 0.7,
        },
        "simulated_outcome": {
            "pnl_r_multiple": value,
            "mfe_r": max(value, 0.5),
            "mae_r": min(value, -0.25),
            "exit_reason": "take_profit" if value > 0 else "stop_loss",
            "bars_held": index + 2,
        },
    }


def _structured(report: dict) -> dict:
    return report["fingerprint"]["evidence_provenance"]


def _assert_current(report: dict) -> None:
    provenance = _structured(report)
    assert validate_evidence_provenance(provenance)[:2] == (True, CURRENT)
    assert report["fingerprint"]["epoch"] == CURRENT


def test_e2_selects_before_flattening_and_does_not_complete_l1(monkeypatch, tmp_path):
    current = [_shadow(index, pnl=1.0) for index in range(100)]
    stale = _shadow(1000, epoch="LEGACY", pnl=-1.0)
    monkeypatch.setattr(
        "research_engine.data_access.shadow_runtime_ingestion.ingest_completed_shadow_trades",
        lambda: [*current, stale],
    )
    monkeypatch.setattr(legacy_canonical, "_load_jsonl", lambda dataset: [])

    report = legacy_canonical.run_q05()
    _assert_current(report)
    component = _structured(report)["components"][0]
    assert report["overall"]["finding"].endswith("from 100 trades")
    assert component["records_used"] == 100
    assert component["records_excluded"] == 1
    assert component["digest"] == evidence_digest(current)

    report_path = tmp_path / "q5_pattern_degradation.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    e2 = build_question_state("E2", reports_dir=tmp_path, evidence_source={})
    l1 = build_question_state("L1", reports_dir=tmp_path, evidence_source={})
    assert e2.report_validity == ReportValidity.VALID_CURRENT, e2.report_validity_reason
    assert e2.state_status == "COMPLETE"
    assert l1.state_status != "COMPLETE"
    assert l1.report_validity != ReportValidity.VALID_CURRENT


@pytest.mark.parametrize(
    ("runner_name", "question_id"),
    (("run_e4", "E4"), ("run_l5", "L5")),
)
def test_edge_depth_targets_prove_selected_population(
    monkeypatch, runner_name, question_id,
):
    current = _shadow(4, pnl=1.25)
    stale = _shadow(5, epoch="LEGACY", pnl=-5.0)
    monkeypatch.setattr(edge_depth, "load_shadow_trades", lambda: [current, stale])

    report = getattr(edge_depth, runner_name)()
    _assert_current(report)
    component = _structured(report)["components"][0]
    assert report["question_id"] == question_id
    assert component["records_used"] == 1
    assert component["records_excluded"] == 1
    assert component["digest"] == evidence_digest([current])


def test_e5_live_and_injected_paths_share_boundary_and_stale_fails_closed(monkeypatch):
    current = [_shadow(i) for i in range(100)]
    stale = _shadow(1000, epoch="LEGACY", pnl=99.0)
    monkeypatch.setattr(out_of_sample_validation, "persist_report", lambda *args: None)
    monkeypatch.setattr(out_of_sample_validation, "update_knowledge_map", lambda *args: None)
    monkeypatch.setattr(out_of_sample_validation, "load_shadow_trades", lambda: current + [stale])

    live = out_of_sample_validation.run_out_of_sample_validation()
    injected = out_of_sample_validation.run_out_of_sample_validation(current + [stale])
    stale_only = out_of_sample_validation.run_out_of_sample_validation([stale])

    _assert_current(live)
    assert _structured(live) == _structured(injected)
    assert live["overall"] == injected["overall"]
    assert _structured(live)["records_excluded"] == 1
    assert stale_only["status"] == "INSUFFICIENT_DATA"
    assert stale_only["fingerprint"]["epoch"] == "UNVERIFIED"


@pytest.mark.parametrize(
    ("runner_name", "question_id"),
    (("run_m2", "M2"), ("run_m4", "M4"), ("run_m6", "M6")),
)
def test_market_targets_keep_outputs_and_add_current_provenance(
    monkeypatch, runner_name, question_id,
):
    records = [_shadow(i) for i in range(12)]
    monkeypatch.setattr(market_research, "load_shadow_trades", lambda: deepcopy(records))
    first = getattr(market_research, runner_name)()
    second = getattr(market_research, runner_name)()

    _assert_current(first)
    assert first["question_id"] == question_id
    assert first["overall"] == second["overall"]
    assert _structured(first) == _structured(second)


@pytest.mark.parametrize(
    ("module", "runner_name", "question_id"),
    (
        (m9_phase_pattern, "run_m9_phase_pattern", "M9"),
        (m10_strategy_family_per_phase, "run_m10_strategy_family_per_phase", "M10"),
    ),
)
def test_phase_targets_exclude_stale_and_emit_structured_current(
    monkeypatch, module, runner_name, question_id,
):
    current = [_shadow(i) for i in range(100)]
    stale = _shadow(500, epoch="LEGACY", pnl=100.0)
    monkeypatch.setattr(module, "persist_report", lambda *args: None)
    monkeypatch.setattr(module, "update_knowledge_map", lambda *args: None)

    report = getattr(module, runner_name)(current + [stale])
    _assert_current(report)
    assert report["question_id"] == question_id
    assert report["overall"]["total_analysed"] == 100
    assert _structured(report)["records_excluded"] == 1


def test_all_nine_target_reports_use_structured_provenance(monkeypatch):
    records = [_shadow(i) for i in range(100)]
    monkeypatch.setattr(
        "research_engine.data_access.shadow_runtime_ingestion.ingest_completed_shadow_trades",
        lambda: deepcopy(records),
    )
    monkeypatch.setattr(legacy_canonical, "_load_jsonl", lambda dataset: [])
    monkeypatch.setattr(edge_depth, "load_shadow_trades", lambda: deepcopy(records))
    monkeypatch.setattr(market_research, "load_shadow_trades", lambda: deepcopy(records))
    monkeypatch.setattr(out_of_sample_validation, "persist_report", lambda *args: None)
    monkeypatch.setattr(out_of_sample_validation, "update_knowledge_map", lambda *args: None)
    for module in (m9_phase_pattern, m10_strategy_family_per_phase):
        monkeypatch.setattr(module, "persist_report", lambda *args: None)
        monkeypatch.setattr(module, "update_knowledge_map", lambda *args: None)

    reports = {
        "E2": legacy_canonical.run_q05(),
        "E4": edge_depth.run_e4(),
        "E5": out_of_sample_validation.run_out_of_sample_validation(deepcopy(records)),
        "M2": market_research.run_m2(),
        "M4": market_research.run_m4(),
        "M6": market_research.run_m6(),
        "M9": m9_phase_pattern.run_m9_phase_pattern(deepcopy(records)),
        "M10": m10_strategy_family_per_phase.run_m10_strategy_family_per_phase(deepcopy(records)),
        "L5": edge_depth.run_l5(),
    }

    assert set(reports) == {"E2", "E4", "E5", "M2", "M4", "M6", "M9", "M10", "L5"}
    for report in reports.values():
        _assert_current(report)


def test_representative_target_resolves_current_but_gates_still_block(monkeypatch):
    records = [_shadow(i) for i in range(100)]
    monkeypatch.setattr(edge_depth, "load_shadow_trades", lambda: records)
    report = edge_depth.run_e4()

    validity, _ = resolve_report_validity(
        "e4_strategy_pattern_combinations.json", report, expected_question_id="E4"
    )
    assert validity == ReportValidity.VALID_CURRENT

    blocked = deepcopy(report)
    blocked["warnings"].append("EPOCH_WARNING: synthetic scientific blocker")
    blocked_validity, _ = resolve_report_validity(
        "e4_strategy_pattern_combinations.json", blocked, expected_question_id="E4"
    )
    assert blocked_validity == ReportValidity.INVALIDATED
