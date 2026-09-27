"""Stage IV Wave 3 conditional cross-universe reconciliation guarantees."""

from __future__ import annotations

from datetime import datetime, timezone
import json

from research_engine.v10.universes.assurance import get_universe_contract
from research_engine.v10.universes.evidence_integrity import (
    EvidenceBatch,
    audit_batch,
    build_manifest,
    reconstruct_shadow_outcomes,
)
from research_engine.v10.universes.models import Universe
from research_engine.v10.universes.reconciliation import (
    ExpectationState,
    MatchKind,
    ReconciliationEngine,
    ReconciliationInput,
    RelationshipStatus,
    describe_reconciliation,
    get_reconciliation_rules,
)


NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)


def _batch(universe, dataset, records, **kwargs):
    return EvidenceBatch(
        universe=universe, dataset=dataset, records=tuple(records),
        schema_version=(f"{dataset}_v1" if not dataset.startswith("derived:") else "derived_v1"),
        **kwargs,
    )


def _decision(entity="e1", **updates):
    value = {
        "entity_id": entity, "correlation_id": f"c-{entity}", "symbol": "EURUSD",
        "cycle_id": 7, "timestamp_utc": "2026-09-26T10:00:00+00:00",
        "action": "EXECUTE", "terminal_stage": "COMPLETED",
        "stages_reached": ["OPPORTUNITY", "STRATEGY", "ENTRY", "RISK", "EXECUTION"],
        "strategy_family": "TREND_CONTINUATION", "strategy_direction": "BUY",
        "risk_approved": True, "regime": "TRENDING", "market_context_captured": True,
        "execution_state": "COMPLETED",
    }
    value.update(updates)
    return value


def _execution(trade="t1", entity="e1", **updates):
    value = {
        "trade_id": trade, "entity_id": entity, "correlation_id": f"c-{entity}",
        "symbol": "EURUSD", "direction": "BUY",
        "entry_time": "2026-09-26T10:01:00+00:00",
        "exit_time": "2026-09-26T11:00:00+00:00",
        "entry_price": 1.1, "exit_price": 1.12,
        "r_multiple": 2.0, "net_realised_pnl": 100.0,
    }
    value.update(updates)
    return value


def _risk(entity="e1", **updates):
    value = {
        "entity_id": entity, "symbol": "EURUSD", "cycle_id": 7,
        "timestamp_utc": "2026-09-26T10:00:00+00:00",
        "risk_control_result": "APPROVED",
    }
    value.update(updates)
    return value


def _strategy(entity="e1", source="strategy_observations", **updates):
    value = {
        "entity_id": entity, "symbol": "EURUSD", "cycle_id": 7,
        "timestamp_utc": "2026-09-26T09:59:59+00:00", "source": source,
        "family": "TREND_CONTINUATION", "direction": "BUY",
    }
    value.update(updates)
    return value


def _market(entity="e1", source="decision_trace", **updates):
    value = {
        "entity_id": entity, "symbol": "EURUSD", "cycle_id": 7,
        "timestamp_utc": "2026-09-26T10:00:00+00:00", "source": source,
        "regime": "TRENDING",
    }
    value.update(updates)
    return value


def _engine(*batches, findings=(), manifests=(), reconstructions=()):
    return ReconciliationEngine(ReconciliationInput(
        tuple(batches), tuple(findings), tuple(manifests), tuple(reconstructions),
    ))


def _statuses(engine, rule):
    return [result.relationship_status for result in engine.reconcile_rule(rule)]


def _shadow_events(close=True):
    common = {
        "schema_version": "shadow_runtime_v1", "shadow_trade_id": "nshadow_1",
        "plan_id": "plan-1", "canonical_opportunity_id": "opp-1",
        "horizon": "SCALP", "symbol": "EURUSD",
    }
    opened = {
        **common, "event_type": "OPEN", "entry_market_time_utc_epoch_s": 1790416800,
        "identity": {"entity_id": "e1", "evaluated_horizon": "SCALP", "trade_horizon": "SCALP", "shadow_type": "HORIZON_ALTERNATIVE"},
        "construction": {"direction": "BUY", "entry_price": 1.1, "stop_loss": 1.09, "take_profit": 1.12},
        "live_facts": {"v10_action": "EXECUTE"},
    }
    closed = {
        **common, "event_type": "CLOSE", "exit_market_time_utc_epoch_s": 1790420400,
        "exit_reason": "take_profit", "exit_price": 1.12,
        "outcome": {"pnl_r_multiple": 2.0},
    }
    return (opened, closed) if close else (opened,)


def test_rules_derive_topology_and_primary_joins_from_wave1():
    rules = get_reconciliation_rules()
    assert [rule.rule_id for rule in rules] == [
        "DECISION_EXECUTION", "EXECUTION_OUTCOME", "DECISION_RISK",
        "DECISION_STRATEGY", "DECISION_MARKET", "SHADOW_LIFECYCLE_OUTCOME",
    ]
    for rule in rules[:-1]:
        source = get_universe_contract(rule.source_universe)
        target = get_universe_contract(rule.target_universe)
        counterpart = next((c for c in source.counterparts if c.universe is rule.target_universe), None)
        counterpart = counterpart or next(c for c in target.counterparts if c.universe is rule.source_universe)
        assert rule.join_fields == counterpart.join_fields
    description = describe_reconciliation()
    assert description["wave1_authority"].endswith("universes.assurance")
    assert description["wave2_authority"].endswith("universes.evidence_integrity")
    assert description["joins_by_symbol_or_time_proximity"] is False


def test_decision_execution_not_required_required_present_missing_pending_and_unknown():
    no_trade = _batch(Universe.DECISION, "decision_trace", [_decision(action="NO_TRADE", execution_state="NOT_ATTEMPTED")])
    assert _statuses(_engine(no_trade), "DECISION_EXECUTION") == [RelationshipStatus.NOT_REQUIRED.value]

    decision = _batch(Universe.DECISION, "decision_trace", [_decision()])
    execution = _batch(Universe.EXECUTION, "trade_truth", [_execution()])
    assert RelationshipStatus.RECONCILED.value in _statuses(_engine(decision, execution), "DECISION_EXECUTION")
    assert _statuses(_engine(decision), "DECISION_EXECUTION") == [RelationshipStatus.MISSING_COUNTERPART.value]

    pending = _batch(Universe.DECISION, "decision_trace", [_decision(execution_state="OPEN")])
    assert _statuses(_engine(pending), "DECISION_EXECUTION") == [RelationshipStatus.PENDING.value]
    unknown = _batch(Universe.DECISION, "decision_trace", [_decision(execution_state="")])
    assert _statuses(_engine(unknown), "DECISION_EXECUTION") == [RelationshipStatus.AMBIGUOUS.value]


def test_decision_execution_extra_counterpart_and_orphan_execution():
    no_trade = _batch(Universe.DECISION, "decision_trace", [_decision(action="NO_TRADE", execution_state="NOT_ATTEMPTED")])
    execution = _batch(Universe.EXECUTION, "trade_truth", [_execution()])
    assert _statuses(_engine(no_trade, execution), "DECISION_EXECUTION") == [RelationshipStatus.EXTRA_COUNTERPART.value]
    assert _statuses(_engine(execution), "DECISION_EXECUTION") == [RelationshipStatus.ORPHAN_EVIDENCE.value]


def test_decision_execution_condition_uses_execution_results_without_treating_attempt_as_terminal():
    decision = _batch(Universe.DECISION, "decision_trace", [_decision(execution_state="")])
    accepted = _batch(Universe.EXECUTION, "execution_results", [{
        "entity_id": "e1", "correlation_id": "c-e1", "result_ok": True,
    }])
    rejected = _batch(Universe.EXECUTION, "execution_results", [{
        "entity_id": "e1", "correlation_id": "c-e1", "result_ok": False,
    }])
    accepted_result = _engine(decision, accepted).reconcile_rule("DECISION_EXECUTION")[0]
    rejected_result = _engine(decision, rejected).reconcile_rule("DECISION_EXECUTION")[0]
    assert accepted_result.relationship_status == RelationshipStatus.PENDING.value
    assert rejected_result.relationship_status == RelationshipStatus.NOT_REQUIRED.value
    assert any("execution_results" in item for item in accepted_result.trace.condition_evidence)


def test_wave2_integrity_blocks_reconciliation_and_controls_cascade():
    decision_record = _decision(timestamp_utc="2026-09-28T10:00:00+00:00")
    decision = _batch(Universe.DECISION, "decision_trace", [decision_record])
    _, findings = audit_batch(decision, now=NOW)
    execution = _batch(Universe.EXECUTION, "trade_truth", [_execution(entry_time="2026-09-28T10:01:00+00:00")])
    results = _engine(decision, execution, findings=findings).reconcile_rule("DECISION_EXECUTION")
    assert [item.relationship_status for item in results] == [RelationshipStatus.BLOCKED_BY_INTEGRITY.value]
    assert results[0].root_finding_ids
    assert results[0].dependent_impact is True


def test_wave2_missing_target_scope_becomes_insufficient_not_false_missing():
    execution = _batch(Universe.EXECUTION, "trade_truth", [_execution()])
    absent_outcome = _batch(
        Universe.OUTCOME, "derived:outcome", [],
        qualifying_activity_expected=True,
    )
    _, findings = audit_batch(absent_outcome)
    result = _engine(execution, absent_outcome, findings=findings).reconcile_rule("EXECUTION_OUTCOME")[0]
    assert result.relationship_status == RelationshipStatus.INSUFFICIENT_EVIDENCE.value
    assert result.root_finding_ids


def test_execution_outcome_exact_missing_duplicate_identity_result_and_orphan():
    execution = _batch(Universe.EXECUTION, "trade_truth", [_execution()])
    outcome = _batch(Universe.OUTCOME, "derived:outcome", [_execution()])
    assert _statuses(_engine(execution, outcome), "EXECUTION_OUTCOME") == [RelationshipStatus.RECONCILED.value]
    assert _statuses(_engine(execution), "EXECUTION_OUTCOME") == [RelationshipStatus.MISSING_COUNTERPART.value]

    duplicate = _batch(Universe.OUTCOME, "derived:outcome", [_execution(), _execution()])
    assert RelationshipStatus.CARDINALITY_CONFLICT.value in _statuses(_engine(execution, duplicate), "EXECUTION_OUTCOME")

    wrong_identity = _batch(Universe.OUTCOME, "derived:outcome", [_execution(entity="different")])
    assert RelationshipStatus.IDENTITY_MISMATCH.value in _statuses(_engine(execution, wrong_identity), "EXECUTION_OUTCOME")

    wrong_result = _batch(Universe.OUTCOME, "derived:outcome", [_execution(r_multiple=-1.0)])
    assert RelationshipStatus.SEMANTIC_MISMATCH.value in _statuses(_engine(execution, wrong_result), "EXECUTION_OUTCOME")

    assert _statuses(_engine(outcome), "EXECUTION_OUTCOME") == [RelationshipStatus.ORPHAN_EVIDENCE.value]


def test_decision_risk_conditional_present_missing_semantic_and_orphan():
    early = _batch(Universe.DECISION, "decision_trace", [_decision(
        action="NO_TRADE", terminal_stage="OPPORTUNITY", stages_reached=["OPPORTUNITY"],
        risk_approved=None, execution_state="NOT_ATTEMPTED",
    )])
    assert _statuses(_engine(early), "DECISION_RISK") == [RelationshipStatus.NOT_REQUIRED.value]

    decision = _batch(Universe.DECISION, "decision_trace", [_decision()])
    risk = _batch(Universe.RISK, "decision_trace", [_risk()])
    assert RelationshipStatus.RECONCILED.value in _statuses(_engine(decision, risk), "DECISION_RISK")
    assert _statuses(_engine(decision), "DECISION_RISK") == [RelationshipStatus.MISSING_COUNTERPART.value]

    blocked = _batch(Universe.RISK, "decision_trace", [_risk(risk_control_result="BLOCKED")])
    assert RelationshipStatus.SEMANTIC_MISMATCH.value in _statuses(_engine(decision, blocked), "DECISION_RISK")
    assert _statuses(_engine(risk), "DECISION_RISK") == [RelationshipStatus.ORPHAN_EVIDENCE.value]


def test_decision_strategy_primary_allowed_fallback_missing_and_ambiguous_fallback():
    decision = _batch(Universe.DECISION, "decision_trace", [_decision()])
    primary = _batch(Universe.STRATEGY, "strategy_observations", [_strategy()])
    result = _engine(decision, primary).reconcile_rule("DECISION_STRATEGY")[0]
    assert result.relationship_status == RelationshipStatus.RECONCILED.value
    assert result.match_kind == MatchKind.DETERMINISTIC.value

    fallback = _batch(Universe.STRATEGY, "decision_trace", [_strategy(source="decision_trace")])
    result = _engine(decision, fallback).reconcile_rule("DECISION_STRATEGY")[0]
    assert result.relationship_status == RelationshipStatus.RECONCILED.value
    assert result.match_kind == MatchKind.CONTRACT_FALLBACK.value
    assert _statuses(_engine(decision), "DECISION_STRATEGY") == [RelationshipStatus.MISSING_COUNTERPART.value]

    standalone = _batch(Universe.STRATEGY, "strategy_observations", [
        _strategy(entity="", cycle_id=7), _strategy(entity="", cycle_id=7, family="BREAKOUT"),
    ])
    assert RelationshipStatus.CARDINALITY_CONFLICT.value in _statuses(_engine(decision, standalone), "DECISION_STRATEGY")


def test_decision_market_primary_fallback_valid_many_and_no_false_orphans():
    decision = _batch(Universe.DECISION, "decision_trace", [_decision()])
    primary = _batch(Universe.MARKET, "decision_trace", [_market()])
    assert RelationshipStatus.RECONCILED.value in _statuses(_engine(decision, primary), "DECISION_MARKET")

    standalone = _batch(Universe.MARKET, "market_context", [
        _market(entity="", source="market_context", timestamp_utc="2026-09-26T09:59:00+00:00"),
        _market(entity="", source="market_context", timestamp_utc="2026-09-26T09:58:00+00:00"),
    ])
    results = _engine(decision, standalone).reconcile_rule("DECISION_MARKET")
    assert results[0].relationship_status == RelationshipStatus.RECONCILED.value
    assert results[0].observed_counterpart_count == 2
    assert results[0].match_kind == MatchKind.CONTRACT_FALLBACK.value
    assert RelationshipStatus.ORPHAN_EVIDENCE.value not in [item.relationship_status for item in results]


def test_semantic_and_temporal_mismatches_are_distinct():
    decision = _batch(Universe.DECISION, "decision_trace", [_decision()])
    semantic = _batch(Universe.MARKET, "decision_trace", [_market(regime="RANGING")])
    assert RelationshipStatus.SEMANTIC_MISMATCH.value in _statuses(_engine(decision, semantic), "DECISION_MARKET")

    temporal = _batch(Universe.MARKET, "decision_trace", [_market(timestamp_utc="2026-09-26T10:01:00+00:00")])
    assert RelationshipStatus.TIMING_MISMATCH.value in _statuses(_engine(decision, temporal), "DECISION_MARKET")

    # Fields outside the rule's semantic set are deliberately non-comparable.
    non_comparable = _batch(Universe.MARKET, "decision_trace", [_market(debug_note="different producer detail")])
    assert RelationshipStatus.RECONCILED.value in _statuses(_engine(decision, non_comparable), "DECISION_MARKET")


def test_shadow_open_only_pending_complete_missing_reconstructed_and_orphan():
    open_batch = _batch(Universe.SHADOW_OUTCOME, "shadow_runtime", _shadow_events(close=False))
    assert _statuses(_engine(open_batch), "SHADOW_LIFECYCLE_OUTCOME") == [RelationshipStatus.PENDING.value]

    events = _shadow_events()
    runtime = _batch(Universe.SHADOW_OUTCOME, "shadow_runtime", events)
    assert _statuses(_engine(runtime), "SHADOW_LIFECYCLE_OUTCOME") == [RelationshipStatus.MISSING_COUNTERPART.value]
    reconstructed = reconstruct_shadow_outcomes(events, reconstruction_timestamp="2026-09-27T12:00:00Z")
    result = _engine(runtime, reconstructions=reconstructed).reconcile_rule("SHADOW_LIFECYCLE_OUTCOME")[0]
    assert result.relationship_status == RelationshipStatus.RECONCILED.value
    assert "RECONSTRUCTED_EXACT" in result.provenance

    orphan = _engine(reconstructions=reconstructed).reconcile_rule("SHADOW_LIFECYCLE_OUTCOME")
    assert [item.relationship_status for item in orphan] == [RelationshipStatus.ORPHAN_EVIDENCE.value]


def test_shadow_broken_lifecycle_and_duplicate_terminal_output_fail_closed():
    _, closed = _shadow_events()
    close_only = _batch(Universe.SHADOW_OUTCOME, "shadow_runtime", [closed])
    _, findings = audit_batch(close_only)
    result = _engine(close_only, findings=findings).reconcile_rule("SHADOW_LIFECYCLE_OUTCOME")[0]
    assert result.relationship_status == RelationshipStatus.BLOCKED_BY_INTEGRITY.value

    events = _shadow_events()
    runtime = _batch(Universe.SHADOW_OUTCOME, "shadow_runtime", events)
    reconstructed = reconstruct_shadow_outcomes(events, reconstruction_timestamp="2026-09-27T12:00:00Z")
    duplicated = reconstructed + reconstructed
    assert RelationshipStatus.CARDINALITY_CONFLICT.value in _statuses(
        _engine(runtime, reconstructions=duplicated), "SHADOW_LIFECYCLE_OUTCOME"
    )


def test_historical_limitations_from_wave2_precede_missing_counterparts():
    decision = _batch(
        Universe.DECISION, "decision_trace", [_decision()],
        historical_boundary="pre-lineage decision trace",
    )
    _, findings = audit_batch(decision)
    result = _engine(decision, findings=findings).reconcile_rule("DECISION_EXECUTION")[0]
    assert result.relationship_status == RelationshipStatus.HISTORICAL_LIMITATION.value
    assert result.relationship_status != RelationshipStatus.MISSING_COUNTERPART.value


def test_manifest_state_trace_counts_and_reports_are_machine_readable():
    decision = _batch(Universe.DECISION, "decision_trace", [_decision()])
    execution = _batch(Universe.EXECUTION, "trade_truth", [_execution()])
    manifest = build_manifest(decision)
    engine = _engine(decision, execution, manifests=(manifest,))
    result = engine.reconcile_rule("DECISION_EXECUTION")[0]
    assert result.trace.join_identity == {"entity_id": "e1", "correlation_id": "c-e1"}
    assert result.trace.candidate_references
    report = engine.reconcile_all()
    assert manifest.fingerprint in report.manifests
    assert sum(report.counts.values()) == len(report.results)
    json.dumps(report.to_dict(), sort_keys=True)


def test_reconciliation_and_fingerprints_are_restart_deterministic():
    batches = (
        _batch(Universe.DECISION, "decision_trace", [_decision()]),
        _batch(Universe.EXECUTION, "trade_truth", [_execution()]),
        _batch(Universe.OUTCOME, "derived:outcome", [_execution()]),
        _batch(Universe.RISK, "decision_trace", [_risk()]),
        _batch(Universe.STRATEGY, "strategy_observations", [_strategy()]),
        _batch(Universe.MARKET, "decision_trace", [_market()]),
    )
    first = _engine(*batches).reconcile_all()
    second = _engine(*reversed(batches)).reconcile_all()
    assert first.to_dict() == second.to_dict()
    assert [item.fingerprint for item in first.results] == [item.fingerprint for item in second.results]


def test_scoped_entity_and_pair_interfaces():
    decision = _batch(Universe.DECISION, "decision_trace", [_decision()])
    execution = _batch(Universe.EXECUTION, "trade_truth", [_execution()])
    engine = _engine(decision, execution)
    assert engine.reconcile_pair(Universe.DECISION, Universe.EXECUTION)
    assert engine.reconcile_entity("e1")
