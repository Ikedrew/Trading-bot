"""Phase 2 acceptance tests for per-question evidence and readiness."""
from __future__ import annotations

from dataclasses import replace
import json

from research_engine.control_plane import (
    EvidenceSnapshot,
    ReportValidity,
    RunnerStatus,
    build_all_question_states,
    build_question_state,
)
from research_engine.control_plane.evidence_resolver import resolve_question_evidence
from research_engine.control_plane.readiness import resolve_readiness
from research_engine.registry.research_question_models import ValidationRule
from research_engine.registry.research_question_registry import get_question


class _EmptyCandidates:
    def list_all(self):
        return []


def _shadow(index: int, **changes):
    record = {
        "epoch": "CURRENT",
        "identity": {
            "entity_id": f"entity-{index}",
            "canonical_opportunity_id": f"opp-{index}",
            "shadow_trade_id": f"shadow-{index}",
            "shadow_type": "PRIMARY_HORIZON_SIMULATION",
            "evaluated_horizon": "INTRADAY",
            "strategy_id": "REVERSAL",
        },
        "decision_snapshot": {
            "pattern": "ENGULFING",
            "score": 0.7,
            "h4_regime": "TRENDING",
            "market_phase": "EXPANSION",
        },
        "simulated_outcome": {
            "pnl_r_multiple": 1.0,
            "mfe_r": 1.2,
            "mae_r": -0.4,
            "exit_reason": "TP",
            "bars_held": 12,
            "trade_state_progression": [{"bar": 1, "r": 0.1}],
        },
        "entry_time": "2026-09-13T10:00:00Z",
    }
    for key, value in changes.items():
        if key == "pattern":
            record["decision_snapshot"]["pattern"] = value
        elif key == "mfe_r":
            record["simulated_outcome"]["mfe_r"] = value
        elif key == "epoch":
            record["epoch"] = value
        else:
            record[key] = value
    return record


def _report(question_id: str, status: str = "COMPLETE"):
    return {
        "question_id": question_id,
        "status": status,
        "overall": {"finding": "Current result", "confidence": "HIGH"},
        "confidence": "HIGH",
        "dataset": {"source": "shadow_trades", "sample_size": 200},
        "fingerprint": {
            "epoch": "CURRENT",
            "architecture_version": "new_pipeline_v1.2",
            "records_used": 200,
            "source": "shadow_trades",
        },
        "recommendation": "MONITOR",
        "warnings": [],
        "generated": "2026-09-13T00:00:00Z",
    }


def _write_report(tmp_path, filename, report):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / filename).write_text(json.dumps(report), encoding="utf-8")


def _state(question_id, tmp_path, datasets):
    return build_question_state(
        question_id,
        reports_dir=tmp_path,
        evidence_source=datasets,
        candidate_registry=_EmptyCandidates(),
    )


def test_simple_sample_question_uses_its_eligible_current_population(tmp_path):
    state = _state("EX1", tmp_path, {"shadow_trades": [_shadow(i) for i in range(37)]})

    assert state.current_sample_size == 37
    assert state.evidence_metrics["total_eligible"] == 37
    assert state.required_sample_size == 200


def test_below_threshold_waits_and_crossing_becomes_ready_without_rerun(tmp_path):
    _write_report(tmp_path, "ex1_exit_efficiency.json", _report("EX1", "INSUFFICIENT_DATA"))
    below = _state("EX1", tmp_path, {"shadow_trades": [_shadow(i) for i in range(199)]})
    above = _state("EX1", tmp_path, {"shadow_trades": [_shadow(i) for i in range(200)]})

    assert below.readiness_status.value == "WAITING_DATA"
    assert "199" in below.readiness_reason
    assert above.readiness_status.value == "READY"
    assert above.latest_report_status == "INSUFFICIENT_DATA"
    assert above.current_sample_size == 200


def test_valid_current_completed_report_is_complete(tmp_path):
    _write_report(tmp_path, "ex1_exit_efficiency.json", _report("EX1"))

    state = _state("EX1", tmp_path, {"shadow_trades": [_shadow(1)]})

    assert state.report_validity == ReportValidity.VALID_CURRENT
    assert state.readiness_status.value == "COMPLETE"
    assert state.state_status == "COMPLETE"


def test_missing_dataset_and_required_field_are_blocked(tmp_path):
    missing_dataset = _state("E1", tmp_path, {})
    rows = [_shadow(i, pattern="") for i in range(10)]
    missing_field = _state("E1", tmp_path, {"shadow_trades": rows})

    assert missing_dataset.readiness_status.value == "BLOCKED"
    assert "dataset" in missing_dataset.readiness_reason.lower()
    assert missing_field.readiness_status.value == "BLOCKED"
    assert "pattern" in missing_field.readiness_reason


def test_coverage_requirements_are_evaluated(tmp_path):
    failing = [_shadow(i, pattern="ENGULFING" if i < 4 else "") for i in range(10)]
    passing = [_shadow(i, pattern="ENGULFING" if i < 5 else "") for i in range(10)]

    blocked = _state("E2", tmp_path, {"shadow_trades": failing})
    ready = _state("E2", tmp_path, {"shadow_trades": passing})

    assert blocked.evidence_metrics["pattern_coverage"] == 0.4
    assert blocked.readiness_status.value == "BLOCKED"
    assert ready.evidence_metrics["pattern_coverage"] == 0.5
    assert ready.readiness_status.value == "READY"


def test_dependency_block_is_explicit(tmp_path):
    rows = [_shadow(i) for i in range(20)]

    state = _state("E4", tmp_path, {"shadow_trades": rows})

    assert state.readiness_status.value == "WAITING_DATA"
    assert "Dependency E2" in state.readiness_reason


def test_forward_registry_dependency_is_resolved_before_dependent(tmp_path):
    for question_id in ("M6", "E2", "M9"):
        question = get_question(question_id)
        _write_report(tmp_path, question.report_filename, _report(question_id))
    states = build_all_question_states(
        reports_dir=tmp_path,
        evidence_source={},
        candidate_registry=_EmptyCandidates(),
    )
    by_id = {state.question_id: state for state in states}

    assert by_id["S1"].readiness_status.value == "BLOCKED"
    assert by_id["M10"].readiness_status.value == "BLOCKED"
    assert by_id["M10"].readiness_reason == "Dependency S1 is BLOCKED"


def test_no_runner_remains_no_runner(tmp_path):
    state = _state("S5", tmp_path, {"shadow_trades": [_shadow(1)]})

    assert state.runner_status == RunnerStatus.NO_RUNNER
    assert state.readiness_status.value == "NO_RUNNER"


def test_legacy_and_transitional_records_never_count(tmp_path):
    rows = [
        *[_shadow(i, epoch="LEGACY") for i in range(200)],
        *[_shadow(300 + i, epoch="TRANSITIONAL") for i in range(100)],
        *[_shadow(500 + i) for i in range(7)],
    ]

    state = _state("EX1", tmp_path, {"shadow_trades": rows})

    assert state.current_sample_size == 7
    assert state.evidence_metrics["legacy_excluded"] == 200
    assert state.evidence_metrics["transitional_excluded"] == 100


def test_global_shadow_volume_cannot_satisfy_narrow_exit_population(tmp_path):
    rows = [_shadow(i, mfe_r=0.1) for i in range(250)]

    state = _state("EX2", tmp_path, {"shadow_trades": rows})

    assert state.evidence_metrics["total_current_population"] == 250
    assert state.current_sample_size == 0
    assert state.readiness_status.value == "WAITING_DATA"


def test_opportunity_question_uses_runner_opportunity_population(tmp_path):
    opportunities = [
        {
            "opportunity_id": f"local-{i}",
            "canonical_opportunity_id": f"opp-{i}",
            "state": "ASSESSED",
            "overall_score": 0.7,
        }
        for i in range(10)
    ]
    horizons = [
        {"canonical_opportunity_id": f"opp-{i}", "selection_status": "SELECTED"}
        for i in range(10)
    ]
    assessments = [{"opportunity_id": f"local-{i}"} for i in range(10)]
    shadows = [
        *[_shadow(i) for i in range(10)],
        *[_shadow(1000 + i) for i in range(100)],
    ]

    state = _state("OPP-1", tmp_path, {
        "opportunities": opportunities,
        "assessments": assessments,
        "horizon_candidates": horizons,
        "shadow_trades": shadows,
    })

    assert state.current_sample_size == 10
    assert state.evidence_metrics["opportunities_total"] == 10
    assert state.evidence_metrics["assessed_opportunities"] == 10
    assert state.readiness_status.value == "READY"


def test_unknown_requirement_type_does_not_pass():
    question = replace(
        get_question("E1"),
        validation_rules=(ValidationRule("future_metric", ">=", 1, "not implemented"),),
    )
    snapshot = EvidenceSnapshot({"shadow_trades": [_shadow(1)]})
    evidence = resolve_question_evidence(question, snapshot)

    status, reason = resolve_readiness(
        question, evidence, RunnerStatus.READY, ReportValidity.MISSING, "NOT_RUN", {}
    )

    assert status.value == "UNKNOWN"
    assert "future_metric" in reason


def test_ranking_rows_and_candidate_population_are_distinct(tmp_path):
    rankings = []
    for cycle in range(10):
        rankings.append({
            "cycle_id": f"cycle-{cycle}",
            "candidates": [
                {
                    "rank_position": rank,
                    "selection_status": "SELECTED" if rank == 1 else "REJECTED",
                    "rank_score": 1.0 / rank,
                }
                for rank in (1, 2, 3)
            ],
        })

    state = _state(
        "D6",
        tmp_path,
        {"portfolio_rankings": rankings, "shadow_trades": []},
    )

    assert state.current_sample_size == 30
    assert state.evidence_metrics["ranking_rows"] == 10
    assert state.evidence_metrics["ranked_candidates"] == 30
    assert state.readiness_status.value == "READY"


def test_build_all_is_deterministic_serializable_and_loads_each_source_once(tmp_path):
    snapshot = EvidenceSnapshot({"shadow_trades": [_shadow(1)]})
    first = build_all_question_states(
        reports_dir=tmp_path,
        evidence_source=snapshot,
        candidate_registry=_EmptyCandidates(),
    )
    second = build_all_question_states(
        reports_dir=tmp_path,
        evidence_source={"shadow_trades": [_shadow(1)]},
        candidate_registry=_EmptyCandidates(),
    )

    first_dict = [state.to_dict() for state in first]
    second_dict = [state.to_dict() for state in second]
    assert first_dict == second_dict
    json.dumps(first_dict, sort_keys=True)
    assert all(count == 1 for count in snapshot.load_counts.values())
