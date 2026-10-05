from __future__ import annotations

import pytest

from core.production_data_contract import current_schema
from research_engine.control_plane.evidence_resolver import (
    EvidenceResolution,
    EvidenceSnapshot,
    normalise_evidence_record,
    resolve_question_evidence,
)
from research_engine.control_plane.stage4_impl_ownership_labels import (
    D1_OWNED_REPORT,
    L3_OWNED_REPORT,
)
from research_engine.data_access.s3_source import set_default_source
from research_engine.experiments.experiment_base import load_shadow_trades
from research_engine.registry.research_question_registry import REGISTRY, REGISTRY_BY_ID
from research_engine.v10.continuous.canonical_question_cycle import (
    _discover_runners,
    _normalise_report,
    _runner_kwargs,
)
from research_engine.v10.continuous.question_cycle_state import CanonicalQuestionResult
from research_engine.v10.investigation_snapshot import InvestigationSnapshotError


def _current_shadow(**overrides):
    row = {
        "schema_version": current_schema("shadow_trades"),
        "data_epoch": "CURRENT",
        "identity": {
            "canonical_opportunity_id": "nopp_pass_a",
            "entity_id": "EURUSD_pass_a",
            "trade_id": "nshadow_pass_a",
            "shadow_trade_id": "nshadow_pass_a",
            "evaluated_horizon": "SCALP",
            "trade_horizon": "SCALP",
        },
        "decision_snapshot": {
            "timestamp_decision_utc": 1777700000,
            "pattern": "HAMMER",
            "strategy": "MEAN_REVERSION",
            "regime": "TRENDING",
            "h4_regime": "TRENDING",
            "market_phase": "IMPULSE",
        },
        "simulated_outcome": {
            "pnl_r_multiple": 1.0,
            "exit_timestamp": 1777700900,
            "trade_state_progression": [{"bar": 1, "r": 1.0}],
        },
    }
    row.update(overrides)
    return row


def test_l3_and_d1_have_distinct_owned_runners_and_reports():
    assert len(REGISTRY) == 70
    d1, l3 = REGISTRY_BY_ID["D1"], REGISTRY_BY_ID["L3"]
    assert (d1.runner_module, d1.runner_function) != (l3.runner_module, l3.runner_function)
    assert d1.report_filename == D1_OWNED_REPORT
    assert l3.report_filename == L3_OWNED_REPORT
    assert d1.report_filename != l3.report_filename

    from research_engine.experiments.architecture_assumption_validity import validate_l3_report

    allowed, reason = validate_l3_report({
        "question_id": "D1",
        "report_schema_version": "l3_architecture_assumption_validity_v3",
        "dataset": {"sample_size": 95},
        "provenance": {"d1_report_consumed": True},
    })
    assert allowed is False
    assert "identity" in reason or "D1" in reason


def test_l6_runner_is_discoverable_and_registry_owned():
    question = REGISTRY_BY_ID["L6"]
    runners, failures = _discover_runners((question,))
    assert failures == {}
    assert runners["L6"].__name__ == "run_l6"
    assert question.report_filename == "l6_learning_cycle_validation.json"


def test_l3_l6_receive_resolver_population_not_generic_shadow_rows():
    population = [{"canonical_opportunity_id": f"OPP-{index}"} for index in range(3)]
    generic = [{"canonical_opportunity_id": "GENERIC"}]
    context = type("Context", (), {
        "datasets": {"shadow_trades": generic},
        "reader": object(),
        "runner_artifacts": {},
    })()
    for question_id in ("L3", "L6"):
        runner = _discover_runners((REGISTRY_BY_ID[question_id],))[0][question_id]
        kwargs = _runner_kwargs(runner, REGISTRY_BY_ID[question_id], population, context)
        assert kwargs["records"] is population
        assert kwargs["records"] is not generic


class _BoundSource:
    def __init__(self, legacy_error: Exception):
        self.legacy_error = legacy_error
        self.calls = []

    def read_dataset(self, dataset, **kwargs):
        self.calls.append(dataset)
        if dataset == "shadow_runtime":
            return []
        raise self.legacy_error


def test_only_legacy_research_shadow_binding_miss_is_tolerated():
    previous = None
    from research_engine.data_access import s3_source
    previous = s3_source._default_source
    source = _BoundSource(InvestigationSnapshotError(
        "DATASET_NOT_BOUND:research_shadow_trades"))
    set_default_source(source)
    try:
        assert load_shadow_trades() == []
        assert source.calls == ["shadow_runtime", "research_shadow_trades"]
    finally:
        s3_source._default_source = previous


def test_m1_m2_execute_using_bound_shadow_runtime_when_legacy_binding_is_absent():
    from research_engine.data_access import s3_source
    from research_engine.experiments.market_prediction_rw2 import run_m1
    from research_engine.experiments.market_research import run_m2

    previous = s3_source._default_source
    source = _BoundSource(InvestigationSnapshotError(
        "DATASET_NOT_BOUND:research_shadow_trades"))
    set_default_source(source)
    try:
        assert run_m1()["status"] in {"INSUFFICIENT_DATA", "WAITING_DATA", "BLOCKED"}
        assert run_m2()["status"] in {"INSUFFICIENT_DATA", "WAITING_DATA", "BLOCKED"}
        assert source.calls.count("shadow_runtime") == 2
        assert source.calls.count("research_shadow_trades") == 2
    finally:
        s3_source._default_source = previous


@pytest.mark.parametrize("message", [
    "DATASET_NOT_BOUND:portfolio_rankings",
    "SNAPSHOT_LEGACY_SCHEMA_READ_FORBIDDEN",
])
def test_other_snapshot_reader_failures_are_not_swallowed(message):
    from research_engine.data_access import s3_source
    previous = s3_source._default_source
    source = _BoundSource(InvestigationSnapshotError(message))
    set_default_source(source)
    try:
        with pytest.raises(InvestigationSnapshotError, match=message):
            load_shadow_trades()
    finally:
        s3_source._default_source = previous


def test_non_snapshot_exception_with_legacy_text_is_not_swallowed():
    from research_engine.data_access import s3_source
    previous = s3_source._default_source
    source = _BoundSource(ValueError(
        "DATASET_NOT_BOUND:research_shadow_trades"))
    set_default_source(source)
    try:
        with pytest.raises(ValueError, match="research_shadow_trades"):
            load_shadow_trades()
    finally:
        s3_source._default_source = previous


def test_entry_time_resolves_canonical_timestamp_and_fails_closed_on_conflict():
    canonical = normalise_evidence_record(_current_shadow(), "shadow_trades")
    assert canonical["entry_time"] == 1777700000

    explicit = _current_shadow(entry_time=1777700000)
    assert normalise_evidence_record(explicit, "shadow_trades")["entry_time"] == 1777700000

    conflict = _current_shadow(entry_time=1777700001)
    assert "entry_time" not in normalise_evidence_record(conflict, "shadow_trades")


@pytest.mark.parametrize("question_id", ["E5", "L4", "P1", "R4"])
def test_pass_a_timestamp_questions_receive_nonzero_eligible_population(question_id):
    question = REGISTRY_BY_ID[question_id]
    datasets = {source.value: [_current_shadow()] for source in question.data_sources}
    resolution = resolve_question_evidence(question, EvidenceSnapshot(datasets=datasets))
    assert resolution.usable_count == 1
    assert resolution.usable_records[0]["entry_time"] == 1777700000


def test_identical_timestamp_resolution_is_shared_by_ex10_l1_l2():
    for question_id in ("EX10", "L1", "L2"):
        question = REGISTRY_BY_ID[question_id]
        resolution = resolve_question_evidence(
            question, EvidenceSnapshot(datasets={"shadow_trades": [_current_shadow()]}))
        assert resolution.usable_count == 1
        assert resolution.usable_records[0]["entry_time"] == 1777700000


def test_blocked_reason_survives_normalisation_and_result_serialisation():
    question = REGISTRY_BY_ID["D1"]
    definition = type("Definition", (), {
        "minimum_sample": None, "definition_version": "pass-a-test",
    })()
    context = type("Context", (), {
        "snapshot_id": "ISNAP-PASS-A",
        "fingerprint": "f" * 64,
        "investigation_epoch": "CURRENT",
        "reader": type("Reader", (), {
            "snapshot": type("Snapshot", (), {"created_at": "2026-10-04T00:00:00Z"})()
        })(),
        "evidence_references": lambda self, q: ({"dataset": "shadow_runtime"},),
    })()
    resolution = EvidenceResolution(
        question_id="D1", usable_count=12, excluded_count=0, sources=[],
        metrics={"join_rate": 0.5}, requirements=[])
    result = _normalise_report(
        question, definition,
        {"status": "BLOCKED", "reason": "DUPLICATE_LINEAGE", "sample_n": 12,
         "limitations": ["ambiguous identity"],
         "statistics": {"conflict_count": 2}},
        resolution, context, None)
    persisted = result.to_dict()
    restored = CanonicalQuestionResult.from_dict(persisted)
    assert result.status == "IMPLEMENTATION_BLOCKED"
    assert result.failure_reason == "DUPLICATE_LINEAGE"
    assert restored.failure_reason == "DUPLICATE_LINEAGE"
    assert restored.sample_n == 12
    assert restored.key_metrics["join_rate"] == 0.5
    assert restored.statistical_output == {"conflict_count": 2}
    assert restored.limitations == ("ambiguous identity",)
    assert restored.evidence_references == ({"dataset": "shadow_runtime"},)


def test_blocked_without_explicit_reason_gets_truthful_state_fallback():
    question = REGISTRY_BY_ID["L3"]
    definition = type("Definition", (), {
        "minimum_sample": None, "definition_version": "pass-a-test",
    })()
    context = type("Context", (), {
        "snapshot_id": "ISNAP-PASS-A",
        "fingerprint": "f" * 64,
        "investigation_epoch": "CURRENT",
        "reader": type("Reader", (), {
            "snapshot": type("Snapshot", (), {"created_at": "2026-10-04T00:00:00Z"})()
        })(),
        "evidence_references": lambda self, q: (),
    })()
    resolution = EvidenceResolution("L3", [], 0, 0, {}, [])
    result = _normalise_report(
        question, definition, {"status": "BLOCKED"}, resolution, context, None)
    assert result.status == "IMPLEMENTATION_BLOCKED"
    assert result.failure_reason == "RUNNER_STATUS:BLOCKED"
