"""Focused contract tests for the shared X3/EXEC1/X6 evidence foundation."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from research_engine.control_plane.evidence_provenance import (
    CURRENT,
    validate_evidence_provenance,
)
from research_engine.control_plane.execution_evidence import (
    CONTEXT_SOURCE,
    RESULT_SOURCE,
    TRACE_SOURCE,
    ExecutionStatus,
    attest_execution_analytical_subset,
    build_governed_execution_evidence,
    interpret_execution_status,
    measured_absolute_slippage,
)
from research_engine.registry.baseline_manifest import BASELINE_QUESTION_IDS
from research_engine.registry.definition_validator import build_definitions_from_registry
from research_engine.registry.master_repair_ledger import MASTER_REPAIR_LEDGER, operational_baseline
from research_engine.registry.research_question_registry import REGISTRY, REGISTRY_BY_ID
from research_engine.registry.wave_a_no_runner_definitions import WAVE_A_NO_RUNNER_TARGETS


def _result(
    correlation: str = "COR-1",
    account: str = "ACCOUNT-A",
    *,
    result_ok=True,
    slippage: float | None = -0.0002,
    semantic: str = "measured_execution_slippage",
    opportunity: str = "OPP-1",
    decision: str = "DEC-1",
    symbol: str = "EURUSD",
    order: int = 101,
) -> dict:
    return {
        "schema_version": "execution_results_v1",
        "data_epoch": "CURRENT",
        "correlation_id": correlation,
        "canonical_opportunity_id": opportunity,
        "decision_id": decision,
        "entity_id": f"ENTITY-{correlation}",
        "symbol": symbol,
        "account_id": account,
        "broker": "Broker",
        "broker_server": "Server",
        "broker_symbol": f"{symbol}.raw",
        "order_ticket": order,
        "deal": order + 1000,
        "position_ticket": order,
        "result_ok": result_ok,
        "retcode": 10009 if result_ok is True else 10030,
        "comment": "filled" if result_ok is True else "rejected",
        "entry_reference": 1.1000,
        "fill_price": 1.1002 if result_ok is True else None,
        "slippage": slippage,
        "slippage_semantic": semantic,
        "request": {"volume": 0.1, "entry_reference": 1.1000, "sl": 1.09, "tp": 1.12},
        "submission": {"volume": 0.1, "sl": 1.09, "tp": 1.12},
    }


def _context(
    correlation: str = "COR-1",
    *,
    opportunity: str = "OPP-1",
    decision: str = "DEC-1",
    symbol: str = "EURUSD",
    session: str = "LONDON",
) -> dict:
    return {
        "schema_version": "execution_context_v1",
        "data_epoch": "CURRENT",
        "correlation_id": correlation,
        "canonical_opportunity_id": opportunity,
        "decision_id": decision,
        "entity_id": f"ENTITY-{correlation}",
        "symbol": symbol,
        "timestamp_utc": "2026-09-21T10:00:00Z",
        "market_access": {
            "session_state": session,
            "spread": 0.0001,
            "spread_atr_ratio": 0.20,
            "bid": 1.1000,
            "ask": 1.1001,
        },
    }


def _trace(
    correlation: str = "COR-1",
    *,
    opportunity: str = "OPP-1",
    decision: str = "DEC-1",
    symbol: str = "EURUSD",
) -> dict:
    return {
        "schema_version": "decision_trace_v1",
        "data_epoch": "CURRENT",
        "correlation_id": correlation,
        "canonical_opportunity_id": opportunity,
        "decision_id": decision,
        "entity_id": f"ENTITY-{correlation}",
        "symbol": symbol,
        "action": "EXECUTE",
        "v10_market_state": {
            "regime": {"volatility_state": "EXPANDING"},
            "h4": {"volatility_state": "WRONG_FALLBACK"},
        },
    }


def _build(results=None, contexts=None, traces=None, *, require_trace=False):
    return build_governed_execution_evidence(
        results if results is not None else [_result()],
        contexts if contexts is not None else [_context()],
        traces,
        require_decision_trace=require_trace,
    )


def test_current_selection_accounts_for_stale_and_incompatible_components():
    stale_result = {**_result(account="STALE", order=202), "data_epoch": "LEGACY"}
    bad_result = {**_result(account="BAD", order=303), "schema_version": "execution_results_v0"}
    stale_context = {**_context("COR-X"), "data_epoch": "TRANSITIONAL"}
    bad_context = {**_context("COR-Y"), "schema_version": "execution_context_v0"}
    stale_trace = {**_trace("COR-X"), "data_epoch": "LEGACY"}
    bad_trace = {**_trace("COR-Y"), "schema_version": "decision_trace_v0"}

    evidence = _build(
        [_result(), stale_result, bad_result],
        [_context(), stale_context, bad_context],
        [_trace(), stale_trace, bad_trace],
        require_trace=True,
    )

    assert evidence.account_result_count == 1
    assert evidence.component_accounting[RESULT_SOURCE] == {
        "input_records": 3,
        "accepted_current_records": 1,
        "stale_records_excluded": 1,
        "schema_incompatible_records_excluded": 1,
        "records_excluded": 2,
        "digest": evidence.component_accounting[RESULT_SOURCE]["digest"],
        "state": CURRENT,
        "malformed_invalid_records_excluded": 0,
        "input_records_including_malformed": 3,
    }
    assert evidence.component_accounting[CONTEXT_SOURCE]["accepted_current_records"] == 1
    assert evidence.component_accounting[CONTEXT_SOURCE]["stale_records_excluded"] == 1
    assert evidence.component_accounting[TRACE_SOURCE]["schema_incompatible_records_excluded"] == 1
    valid, state, _ = validate_evidence_provenance(evidence.provenance)
    assert valid and state == CURRENT
    assert {c["source"] for c in evidence.provenance["components"]} == {
        RESULT_SOURCE, CONTEXT_SOURCE, TRACE_SOURCE,
    }


def test_fanout_children_remain_distinct_but_share_one_decision_cluster():
    results = [_result(account="A", order=101), _result(account="B", order=202)]
    evidence = _build(results)

    assert evidence.account_result_count == 2
    assert evidence.distinct_decision_count == 1
    assert len(evidence.clusters["COR-1"]) == 2
    assert {dict(row.observation_identity)["account_id"] for row in evidence.observations} == {"A", "B"}


def test_observation_identity_uses_explicit_execution_id_and_is_never_invented():
    explicit = _result(account="", order=0)
    explicit["position_ticket"] = 0
    explicit["deal"] = 0
    explicit["execution_id"] = "EXEC-1"
    accepted = _build([explicit])
    assert accepted.account_result_count == 1
    assert dict(accepted.observations[0].observation_identity)["execution_id"] == "EXEC-1"

    missing = deepcopy(explicit)
    missing.pop("execution_id")
    rejected = _build([missing])
    assert rejected.account_result_count == 0
    assert rejected.exclusion_accounting["missing_account_execution_identity"] == 1


def test_duplicates_do_not_inflate_and_conflicting_identity_fails_closed():
    original = _result()
    exact = deepcopy(original)
    deduplicated = _build([original, exact])
    assert deduplicated.account_result_count == 1
    assert deduplicated.exclusion_accounting["duplicate_persistence"] == 1

    conflict = {**original, "result_ok": False}
    rejected = _build([original, conflict])
    assert rejected.account_result_count == 0
    assert rejected.exclusion_accounting["conflicting_duplicate_identity"] == 2


def test_non_observation_supplement_is_not_a_second_account_result():
    original = _result()
    supplement = deepcopy(original)
    supplement["comment"] = "protection_verification"
    supplement["protection_status"] = "PROTECTED"
    supplement["order_ticket"] = 0
    supplement["result_ok"] = False
    supplement["slippage"] = None
    supplement["slippage_semantic"] = "unknown"
    evidence = _build([original, supplement])

    assert evidence.account_result_count == 1
    assert evidence.exclusion_accounting["supplemental_non_observation"] == 1
    assert evidence.observations[0].result_fields["comment"] == "filled"

    supplemental_only = _build([supplement])
    assert supplemental_only.account_result_count == 0
    assert supplemental_only.exclusion_accounting["supplemental_non_observation"] == 1


@pytest.mark.parametrize(
    ("contexts", "reason"),
    [
        ([], "missing_context"),
        ([_context(), {**_context(), "market_access": {"session_state": "NEW_YORK"}}],
         "ambiguous_conflicting_context"),
        ([_context(opportunity="OPP-X")], "canonical_opportunity_conflict"),
        ([_context(symbol="GBPUSD")], "canonical_symbol_conflict"),
        ([_context(decision="DEC-X")], "decision_identity_conflict"),
    ],
)
def test_result_context_join_fails_closed(contexts, reason):
    evidence = _build(contexts=contexts)
    assert evidence.account_result_count == 0
    assert evidence.exclusion_accounting[reason] >= 1


def test_no_symbol_or_timestamp_fallback_when_correlation_is_missing():
    result = _result()
    result["correlation_id"] = ""
    evidence = _build([result], [_context()])
    assert evidence.account_result_count == 0
    assert evidence.exclusion_accounting["missing_correlation_id"] == 1


@pytest.mark.parametrize(
    ("traces", "reason"),
    [
        ([], "missing_decision_trace"),
        ([_trace(), {**_trace(), "action": "REJECT"}], "ambiguous_conflicting_decision_trace"),
        ([_trace(opportunity="OPP-X")], "canonical_opportunity_conflict"),
        ([_trace(symbol="GBPUSD")], "canonical_symbol_conflict"),
        ([_trace(decision="DEC-X")], "decision_identity_conflict"),
    ],
)
def test_required_decision_trace_join_fails_closed(traces, reason):
    evidence = _build(traces=traces, require_trace=True)
    assert evidence.account_result_count == 0
    assert evidence.exclusion_accounting[reason] >= 1


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, ExecutionStatus.SUCCESS),
        (False, ExecutionStatus.FAILURE),
        (None, ExecutionStatus.UNKNOWN),
        ("true", ExecutionStatus.UNKNOWN),
        (1, ExecutionStatus.UNKNOWN),
        ({}, ExecutionStatus.UNKNOWN),
    ],
)
def test_result_ok_has_strict_tri_state_semantics(value, expected):
    assert interpret_execution_status(value) is expected


@pytest.mark.parametrize(
    ("semantic", "value", "expected"),
    [
        ("measured_execution_slippage", -1.25, 1.25),
        ("unknown", 1.25, None),
        (None, 1.25, None),
        ("measured_execution_slippage", float("nan"), None),
        ("measured_execution_slippage", float("inf"), None),
        ("measured_execution_slippage", True, None),
    ],
)
def test_measured_slippage_requires_producer_semantic_and_finite_value(semantic, value, expected):
    assert measured_absolute_slippage({"slippage_semantic": semantic, "slippage": value}) == expected


def test_foundation_never_reconstructs_requested_vs_fill_slippage():
    result = _result(slippage=None, semantic="unknown")
    row = _build([result]).observations[0]
    assert row.absolute_measured_slippage is None
    assert "derived_compatibility_slippage" not in row.result


def test_context_and_trace_authorities_are_exposed_without_fallbacks():
    evidence = _build(traces=[_trace()], require_trace=True)
    row = evidence.observations[0]
    assert row.session_state == "LONDON"
    assert row.spread == 0.0001
    assert row.spread_atr_ratio == 0.20
    assert row.bid == 1.1000 and row.ask == 1.1001
    assert row.action == "EXECUTE"
    assert row.volatility_state == "EXPANDING"

    context = _context()
    context["market_access"].pop("spread_atr_ratio")
    context["spread_atr_ratio"] = 999
    context["session_state"] = "TIMESTAMP_INFERRED"
    trace = _trace()
    trace["v10_market_state"]["regime"].pop("volatility_state")
    row = _build(contexts=[context], traces=[trace], require_trace=True).observations[0]
    assert row.session_state == "LONDON"
    assert row.spread_atr_ratio is None
    assert row.volatility_state is None


def test_two_and_three_component_exact_subset_provenance_is_deterministic():
    results = [
        _result(account="A", order=101),
        _result(account="B", order=202),
    ]
    base = _build(results)
    forward = attest_execution_analytical_subset(base, base.observations)
    reverse = attest_execution_analytical_subset(base, reversed(base.observations))
    assert forward["digest"] == reverse["digest"] == base.provenance["digest"]
    assert len(forward["components"]) == 2

    traced = _build(results, traces=[_trace()], require_trace=True)
    assert len(traced.provenance["components"]) == 3
    assert validate_evidence_provenance(traced.provenance)[:2] == (True, CURRENT)


def test_provenance_is_change_sensitive_and_rejects_fabricated_or_stale_rows():
    original = _build()
    changed_result = _result(slippage=-0.0009)
    changed = _build([changed_result])
    assert changed.provenance["digest"] != original.provenance["digest"]

    fabricated = replace(original.observations[0], session_state="FABRICATED")
    with pytest.raises(ValueError, match="not part of the governed"):
        attest_execution_analytical_subset(original, [fabricated])

    stale = {**_result(), "data_epoch": "LEGACY"}
    stale_fabrication = replace(original.observations[0], _result_source_record=stale)
    with pytest.raises(ValueError, match="not part of the governed"):
        attest_execution_analytical_subset(original, [stale_fabrication])


def test_accounting_is_order_invariant_and_retains_exclusion_reasons():
    records = [
        _result(account="A", order=101),
        deepcopy(_result(account="A", order=101)),
        {**_result(correlation="", account="B", order=202)},
        _result(correlation="COR-X", account="C", order=303),
    ]
    forward = _build(records)
    reverse = _build(list(reversed(records)))
    assert forward.account_result_count == reverse.account_result_count == 1
    assert forward.exclusion_accounting == reverse.exclusion_accounting == {
        "duplicate_persistence": 1,
        "missing_context": 1,
        "missing_correlation_id": 1,
    }


def test_shared_foundation_preserves_current_canonical_question_state():
    assert MASTER_REPAIR_LEDGER["X3"].structurally_operational
    assert MASTER_REPAIR_LEDGER["EXEC1"].structurally_operational
    assert not MASTER_REPAIR_LEDGER["X6"].structurally_operational
    assert not REGISTRY_BY_ID["X6"].runner_module
    assert not REGISTRY_BY_ID["X6"].runner_function
    assert not REGISTRY_BY_ID["X6"].report_filename
    assert operational_baseline() == (47, 23)
    assert WAVE_A_NO_RUNNER_TARGETS == {"X6", "L6", "G1", "G2", "G3"}
    assert len(REGISTRY) == len({item.id for item in REGISTRY}) == 70
    assert tuple(item.id for item in REGISTRY) == BASELINE_QUESTION_IDS
    assert all(
        definition.definition_version == 1
        for definition in build_definitions_from_registry(REGISTRY).values()
    )
