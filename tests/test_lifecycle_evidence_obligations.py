from __future__ import annotations

import json

import pytest

from core.lifecycle_evidence_obligations import (
    DATASET_DISPOSITIONS,
    EXACT_IDENTITY_FIELDS,
    EvidenceObligation,
    LifecycleEvidenceLedger,
    ObligationStatus as S,
    begin_active_cycle,
    create_account_execution_obligations,
    create_closed_trade_obligations,
    create_dataset_obligation,
    create_filled_position_obligations,
    create_no_trade_obligations,
    create_terminal_decision_obligations,
    record_producer_outcome,
    reconcile_obligation,
)
from core.production_data_contract import PRODUCTION_SCHEMA_REGISTRY


TS = "2026-10-01T10:00:00Z"


def _ledger(tmp_path):
    return LifecycleEvidenceLedger(tmp_path / "obligations.jsonl")


def _trade_identity(account="A", trade="T1", correlation="C1"):
    return {
        "trade_id": trade, "account_id": account, "correlation_id": correlation,
        "broker": "BROKER", "broker_server": "SERVER", "position_ticket": 101,
        "symbol": "EURUSD", "cycle_id": 1, "decision_id": "D1",
        "entity_id": "E1", "canonical_opportunity_id": "O1",
    }


def test_active_cycle_creates_execution_and_conditional_market_obligations(tmp_path):
    execution, market = begin_active_cycle(
        _ledger(tmp_path), event_id="cycle-1", symbol="EURUSD", cycle_id=1,
        entity_id="E1", correlation_id="C1", timestamp=TS)

    assert execution.expected_dataset == "execution_context"
    assert execution.current_status == S.NOT_YET_DUE.value
    assert market.expected_dataset == "market_context"
    assert market.requirement_type == "CONDITIONAL"
    assert market.current_status == S.NOT_YET_DUE.value
    assert execution.expected_identity == {"correlation_id": "C1"}


def test_market_context_unchanged_is_explicit_not_applicable_and_failure_is_explicit(tmp_path):
    ledger = _ledger(tmp_path)
    _, unchanged = begin_active_cycle(
        ledger, event_id="cycle-1", symbol="EURUSD", cycle_id=1,
        entity_id="E1", correlation_id="C1", timestamp=TS)
    unchanged = ledger.update(
        unchanged.obligation_id, S.NOT_APPLICABLE,
        failure_reason="UNCHANGED_MATERIAL_CHANGE_GATE",
        provenance={"semantic": "UNCHANGED"})
    assert unchanged.current_status == S.NOT_APPLICABLE.value
    assert unchanged.provenance["semantic"] == "UNCHANGED"

    _, failed = begin_active_cycle(
        ledger, event_id="cycle-2", symbol="EURUSD", cycle_id=2,
        entity_id="E2", correlation_id="C2", timestamp=TS)
    failed = ledger.update(failed.obligation_id, S.PRODUCER_FAILED,
                           failure_reason="SERIALIZER_EXCEPTION")
    assert failed.current_status == S.PRODUCER_FAILED.value
    assert failed.failure_reason == "SERIALIZER_EXCEPTION"


def test_no_trade_marks_execution_evidence_not_applicable(tmp_path):
    obligations = create_no_trade_obligations(
        _ledger(tmp_path), event_id="decision-1",
        identity={"symbol": "EURUSD", "cycle_id": 1, "entity_id": "E1",
                  "correlation_id": "C1", "decision_id": "D1"},
        timestamp=TS)
    by_dataset = {item.expected_dataset: item for item in obligations}

    assert by_dataset["decision_ledger"].current_status == S.NOT_YET_DUE.value
    for dataset in ("execution_attempts", "execution_results", "protection_audit", "trade_truth"):
        assert by_dataset[dataset].current_status == S.NOT_APPLICABLE.value


def test_attempt_is_expected_only_after_order_send_and_fanout_is_per_account(tmp_path):
    ledger = _ledger(tmp_path)
    a_result, a_attempt = create_account_execution_obligations(
        ledger, event_id="fanout-1", identity=_trade_identity("A"), timestamp=TS,
        order_send_expected=True, attempt_id="AT-A")
    b_result, b_attempt = create_account_execution_obligations(
        ledger, event_id="fanout-1", identity=_trade_identity("B"), timestamp=TS,
        order_send_expected=False)

    assert a_result.obligation_id != b_result.obligation_id
    assert a_result.expected_identity["account_id"] == "A"
    assert b_result.expected_identity["account_id"] == "B"
    assert a_attempt.current_status == S.NOT_YET_DUE.value
    assert b_attempt.current_status == S.NOT_APPLICABLE.value


def test_other_account_evidence_cannot_satisfy_account_obligation(tmp_path):
    ledger = _ledger(tmp_path)
    result, _ = create_account_execution_obligations(
        ledger, event_id="fanout-1", identity=_trade_identity("A"), timestamp=TS,
        order_send_expected=True, attempt_id="AT-A")

    updated = reconcile_obligation(ledger, result, [{
        "correlation_id": "C1", "account_id": "B", "order_ticket": 101,
    }], due=True)

    assert updated.current_status == S.EXPECTED_BUT_MISSING.value
    assert updated.observed_record_id is None


def test_filled_trade_truth_is_not_yet_due_until_close(tmp_path):
    ledger = _ledger(tmp_path)
    _, truth = create_filled_position_obligations(
        ledger, event_id="fill-1", identity=_trade_identity(), timestamp=TS)

    still_open = reconcile_obligation(ledger, truth, [], due=False)

    assert still_open.current_status == S.NOT_YET_DUE.value
    assert still_open.expected_dataset == "trade_truth"
    assert still_open.due_after == "POSITION_CLOSED"

    closed_truth, _, _ = create_closed_trade_obligations(
        ledger, event_id="close-1", identity=_trade_identity(), timestamp=TS,
        risk_geometry_valid=True)
    assert closed_truth.obligation_id == truth.obligation_id
    assert closed_truth.current_status == S.EXPECTED_BUT_MISSING.value


def test_repeated_close_does_not_reset_present_trade_truth(tmp_path):
    ledger = _ledger(tmp_path)
    identity = _trade_identity()
    truth, _, _ = create_closed_trade_obligations(
        ledger, event_id="close-1", identity=identity, timestamp=TS,
        risk_geometry_valid=True)
    truth = reconcile_obligation(ledger, truth, [{
        "identity": {"trade_id": "T1", "account_id": "A"},
    }], due=True)

    repeated, _, _ = create_closed_trade_obligations(
        ledger, event_id="close-1", identity=identity, timestamp=TS,
        risk_geometry_valid=True)

    assert repeated.current_status == S.PRESENT.value
    assert repeated.revision == truth.revision


def test_missing_fill_account_is_recorded_as_producer_failure(tmp_path):
    protection, truth = create_filled_position_obligations(
        _ledger(tmp_path), event_id="fill-missing-account",
        identity=_trade_identity(account=""), timestamp=TS)

    assert protection.current_status == S.PRODUCER_FAILED.value
    assert truth.current_status == S.PRODUCER_FAILED.value
    assert protection.account_id == ""
    assert truth.account_id == ""


def test_closed_trade_truth_present_or_expected_missing(tmp_path):
    ledger = _ledger(tmp_path)
    truth, risk, _ = create_closed_trade_obligations(
        ledger, event_id="close-1", identity=_trade_identity(), timestamp=TS,
        risk_geometry_valid=True)
    _, other_account_risk, _ = create_closed_trade_obligations(
        ledger, event_id="close-other-account",
        identity=_trade_identity(account="B"), timestamp=TS,
        risk_geometry_valid=True)
    present = reconcile_obligation(ledger, truth, [{
        "identity": {"trade_id": "T1", "account_id": "A"},
        "schema_version": "trade_truth_v1",
    }], due=True)
    missing, _, _ = create_closed_trade_obligations(
        ledger, event_id="close-2", identity=_trade_identity(trade="T2"),
        timestamp=TS, risk_geometry_valid=True)
    missing = reconcile_obligation(ledger, missing, [], due=True)

    assert present.current_status == S.PRESENT.value
    assert present.observed_record_id == "T1"
    assert risk.expected_identity == {"trade_id": "T1", "account_id": "A"}
    assert other_account_risk.obligation_id != risk.obligation_id
    assert other_account_risk.expected_identity["account_id"] == "B"
    assert missing.current_status == S.EXPECTED_BUT_MISSING.value


def test_multiple_exact_matches_are_ambiguous_and_timestamps_are_not_join_keys(tmp_path):
    ledger = _ledger(tmp_path)
    result, _ = create_account_execution_obligations(
        ledger, event_id="fanout-1", identity=_trade_identity("A"), timestamp=TS,
        order_send_expected=True, attempt_id="AT-A")
    exact = {"correlation_id": "C1", "account_id": "A", "attempt_id": "AT1"}
    ambiguous = reconcile_obligation(ledger, result, [exact, dict(exact)], due=True)
    assert ambiguous.current_status == S.AMBIGUOUS.value

    other, _ = create_account_execution_obligations(
        ledger, event_id="fanout-2", identity=_trade_identity("A", correlation="C2"),
        timestamp=TS, order_send_expected=True, attempt_id="AT-C2")
    timestamp_only = reconcile_obligation(
        ledger, other,
        [{"timestamp_utc": TS, "correlation_id": "C1", "account_id": "A"}],
        due=True)
    assert timestamp_only.current_status == S.EXPECTED_BUT_MISSING.value


def test_duplicate_creation_is_idempotent_and_restart_reloads_latest_revision(tmp_path):
    ledger = _ledger(tmp_path)
    first, _ = begin_active_cycle(
        ledger, event_id="cycle-1", symbol="EURUSD", cycle_id=1,
        entity_id="E1", correlation_id="C1", timestamp=TS)
    duplicate, _ = begin_active_cycle(
        ledger, event_id="cycle-1", symbol="EURUSD", cycle_id=1,
        entity_id="E1", correlation_id="C1", timestamp=TS)
    assert duplicate.obligation_id == first.obligation_id
    assert len(ledger.obligations()) == 2

    ledger.update(first.obligation_id, S.PRODUCER_FAILED,
                  failure_reason="WRITE_FAILED")
    reloaded = LifecycleEvidenceLedger(ledger.path)
    current = reloaded.get(first.obligation_id)
    assert current is not None
    assert current.current_status == S.PRODUCER_FAILED.value
    assert current.revision == 2
    assert len(ledger.path.read_text(encoding="utf-8").splitlines()) == 3


def test_account_scoped_obligations_fail_closed_without_account_id(tmp_path):
    with pytest.raises(ValueError, match="ACCOUNT_ID_REQUIRED:execution_results"):
        create_account_execution_obligations(
            _ledger(tmp_path), event_id="fanout-1",
            identity={"symbol": "EURUSD", "correlation_id": "C1"},
            timestamp=TS, order_send_expected=True, attempt_id="AT-MISSING-ACCOUNT")


def test_historical_absence_is_not_backfilled_by_obligation_ledger(tmp_path):
    ledger = _ledger(tmp_path)
    assert ledger.obligations() == ()
    assert not ledger.path.exists()


def test_every_active_registry_dataset_has_disposition_and_exact_identity():
    active = set(PRODUCTION_SCHEMA_REGISTRY)
    assert set(DATASET_DISPOSITIONS) == active
    assert set(EXACT_IDENTITY_FIELDS) == active
    assert all(EXACT_IDENTITY_FIELDS[dataset] for dataset in active)


def test_legacy_shadow_trade_cannot_satisfy_shadow_runtime_obligation(tmp_path):
    ledger = _ledger(tmp_path)
    obligation = create_dataset_obligation(
        ledger, event_id="shadow-runtime:event-1",
        lifecycle_stage="SHADOW_RUNTIME_EVENT", dataset="shadow_runtime",
        identity={"event_id": "event-1", "symbol": "EURUSD"},
        timestamp=TS, producer="test", trigger="OPEN",
    )

    result = reconcile_obligation(ledger, obligation, [{
        "schema_version": "shadow_trades_v1",
        "event_type": "CLOSE",
        "identity": {"trade_id": "event-1"},
    }], due=True)

    assert result.current_status == S.EXPECTED_BUT_MISSING.value


def test_quarantine_obligation_exists_only_for_rejected_records(tmp_path, monkeypatch):
    from core.contracts.quarantine import QuarantineStore
    from core.contracts.severity import Severity
    from core.contracts.violation import ContractViolation

    ledger = _ledger(tmp_path)
    monkeypatch.setattr(
        "core.lifecycle_evidence_obligations.obligation_ledger",
        lambda path=None: ledger,
    )
    store = QuarantineStore(local_dir=str(tmp_path / "quarantine"))
    store.quarantine(
        record={"record_id": "REC-1", "symbol": "EURUSD"},
        violations=[ContractViolation(
            contract_name="sample", validator_name="sample_validator",
            severity=Severity.ERROR, reason="invalid fixture",
        )],
        layer="assessments",
    )

    obligations = ledger.obligations()
    assert len(obligations) == 1
    assert obligations[0].expected_dataset == "quarantine"
    assert obligations[0].current_status == S.NOT_YET_DUE.value
    assert obligations[0].provenance["local_path_authority"] == "LOCAL_ONLY"
    assert obligations[0].provenance["canonical_mirror_acknowledgement"] == "NOT_OBSERVED"


def test_quarantine_fallback_identity_is_content_deterministic(tmp_path):
    from core.contracts.quarantine import QuarantineStore
    from core.contracts.severity import Severity
    from core.contracts.violation import ContractViolation

    store = QuarantineStore(local_dir=str(tmp_path / "quarantine"))
    violation = ContractViolation(
        contract_name="sample", validator_name="sample_validator",
        severity=Severity.ERROR, reason="invalid fixture",
    )
    first = store.quarantine(
        record={"symbol": "EURUSD", "value": 1},
        violations=[violation], layer="assessments",
    )
    second = store.quarantine(
        record={"symbol": "EURUSD", "value": 1},
        violations=[violation], layer="assessments",
    )

    assert first.record_id == second.record_id
    assert first.record_id.startswith("payload_")


def test_quarantine_local_write_failure_marks_producer_failed(tmp_path, monkeypatch):
    from core.contracts.quarantine import QuarantineStore
    from core.contracts.severity import Severity
    from core.contracts.violation import ContractViolation

    ledger = _ledger(tmp_path)
    monkeypatch.setattr(
        "core.lifecycle_evidence_obligations.obligation_ledger",
        lambda path=None: ledger,
    )
    store = QuarantineStore(local_dir=str(tmp_path / "quarantine"))
    monkeypatch.setattr(store, "_persist", lambda record, timestamp: False)
    store.quarantine(
        record={"record_id": "REC-FAIL", "symbol": "EURUSD"},
        violations=[ContractViolation(
            contract_name="sample", validator_name="sample_validator",
            severity=Severity.ERROR, reason="invalid fixture",
        )],
        layer="assessments",
    )

    obligation, = ledger.find_exact("quarantine", {"record_id": "REC-FAIL"})
    assert obligation.current_status == S.PRODUCER_FAILED.value
    assert obligation.failure_reason == "QUARANTINE_LOCAL_WRITE_FAILED"


def test_legacy_and_research_shadow_outcomes_keep_separate_authority(tmp_path, monkeypatch):
    import core.shadow_trades as legacy_shadow
    import core.research_assessment.research_shadow_engine as research_shadow

    ledger = _ledger(tmp_path)
    monkeypatch.setattr(
        "core.lifecycle_evidence_obligations.obligation_ledger",
        lambda path=None: ledger,
    )
    monkeypatch.setattr(legacy_shadow, "_LOCAL_DIR", str(tmp_path / "legacy"))
    monkeypatch.setattr(research_shadow, "_LOCAL_DIR", str(tmp_path / "research"))
    record = {
        "event_type": "CLOSE",
        "identity": {"trade_id": "SHADOW-1", "symbol": "EURUSD"},
        "timestamps": {"exit_time": 1_790_000_000},
        "simulated_outcome": {"pnl_r_multiple": 0.5},
    }

    legacy_shadow._persist_shadow_trade(dict(record))
    research_shadow._persist_research_trade(dict(record))

    by_dataset = {item.expected_dataset: item for item in ledger.obligations()}
    assert by_dataset["shadow_trades"].provenance["authority"] == "LEGACY_NON_AUTHORITY"
    assert by_dataset["research_shadow_trades"].provenance["authority"] == "DERIVED_RESEARCH_ONLY"
    assert by_dataset["shadow_trades"].obligation_id != by_dataset["research_shadow_trades"].obligation_id
    assert not any(item.expected_dataset == "shadow_runtime" for item in ledger.obligations())


def test_ledger_persists_exact_status_vocabulary_and_revision_provenance(tmp_path):
    ledger = _ledger(tmp_path)
    _, market = begin_active_cycle(
        ledger, event_id="cycle-1", symbol="EURUSD", cycle_id=1,
        entity_id="E1", correlation_id="C1", timestamp=TS)
    updated = ledger.update(market.obligation_id, S.PRESENT,
                            observed_record_id="E1", provenance={"durable_scope": "LOCAL_FSYNC"})
    row = json.loads(ledger.path.read_text(encoding="utf-8").splitlines()[-1])

    assert {status.value for status in S} == {
        "PRESENT", "NOT_APPLICABLE", "NOT_YET_DUE", "EXPECTED_BUT_MISSING",
        "AMBIGUOUS", "PRODUCER_FAILED",
    }
    assert row["current_status"] == S.PRESENT.value
    assert row["previous_status"] == S.NOT_YET_DUE.value
    assert row["observed_record_id"] == "E1"
    assert updated.provenance["durable_scope"] == "LOCAL_FSYNC"


def test_demonstration_lifecycle_status_matrix(tmp_path):
    ledger = _ledger(tmp_path)
    no_trade = create_no_trade_obligations(
        ledger, event_id="no-trade", identity={"symbol": "EURUSD", "cycle_id": 1,
        "entity_id": "E1", "correlation_id": "C1", "decision_id": "D1"}, timestamp=TS)
    _, unchanged = begin_active_cycle(
        ledger, event_id="unchanged", symbol="EURUSD", cycle_id=2,
        entity_id="E2", correlation_id="C2", timestamp=TS)
    unchanged = ledger.update(unchanged.obligation_id, S.NOT_APPLICABLE,
                              failure_reason="UNCHANGED")
    _, failed_context = begin_active_cycle(
        ledger, event_id="context-fail", symbol="EURUSD", cycle_id=3,
        entity_id="E3", correlation_id="C3", timestamp=TS)
    failed_context = ledger.update(failed_context.obligation_id, S.PRODUCER_FAILED,
                                   failure_reason="SERIALIZE_FAILED")
    account_success, attempt_success = create_account_execution_obligations(
        ledger, event_id="single", identity=_trade_identity("A"), timestamp=TS,
        order_send_expected=True, attempt_id="AT-A")
    account_failure, _ = create_account_execution_obligations(
        ledger, event_id="multi", identity=_trade_identity("B"), timestamp=TS,
        order_send_expected=True, attempt_id="AT-B")
    account_failure = ledger.update(account_failure.obligation_id, S.PRODUCER_FAILED,
                                    failure_reason="ACCOUNT_RESULT_WRITE_FAILED")
    account_success = reconcile_obligation(ledger, account_success, [{
        "correlation_id": "C1", "account_id": "A",
    }], due=True)
    _, open_truth = create_filled_position_obligations(
        ledger, event_id="open", identity=_trade_identity("A"), timestamp=TS)
    closed_truth, _, _ = create_closed_trade_obligations(
        ledger, event_id="closed-present", identity=_trade_identity("A"), timestamp=TS,
        risk_geometry_valid=True)
    closed_present = reconcile_obligation(ledger, closed_truth, [
        {"identity": {"trade_id": "T1", "account_id": "A"}}
    ], due=True)
    closed_missing, _, _ = create_closed_trade_obligations(
        ledger, event_id="closed-missing", identity=_trade_identity("A", trade="T2"),
        timestamp=TS, risk_geometry_valid=True)
    closed_missing = reconcile_obligation(ledger, closed_missing, [], due=True)
    matrix = [
        ("NO_TRADE", "execution_results", None, next(x for x in no_trade if x.expected_dataset == "execution_results").current_status),
        ("UNCHANGED", "market_context", None, unchanged.current_status),
        ("CONTEXT_FAILURE", "execution_context", None, failed_context.current_status),
        ("SINGLE_ACCOUNT", "execution_results", "A", account_success.current_status),
        ("SINGLE_ACCOUNT", "execution_attempts", "A", attempt_success.current_status),
        ("MULTI_ACCOUNT_FAILURE", "execution_results", "B", account_failure.current_status),
        ("OPEN_TRADE", "trade_truth", "A", open_truth.current_status),
        ("CLOSED_PRESENT", "trade_truth", "A", closed_present.current_status),
        ("CLOSED_MISSING", "trade_truth", "A", closed_missing.current_status),
    ]

    assert [row[3] for row in matrix] == [
        S.NOT_APPLICABLE.value, S.NOT_APPLICABLE.value, S.PRODUCER_FAILED.value,
        S.PRESENT.value, S.NOT_YET_DUE.value, S.PRODUCER_FAILED.value,
        S.NOT_YET_DUE.value, S.PRESENT.value, S.EXPECTED_BUT_MISSING.value,
    ]


def test_full_forward_evidence_acceptance_matrix_and_restart(tmp_path):
    ledger = _ledger(tmp_path)
    rows: list[dict] = []

    def capture(event, obligation, account="", reason=""):
        rows.append({
            "event": event,
            "dataset": obligation.expected_dataset,
            "account": account,
            "obligation": obligation.obligation_id,
            "status": obligation.current_status,
            "exact_key": obligation.expected_identity_key,
            "identity": dict(obligation.expected_identity),
            "reason": reason or obligation.failure_reason or "",
        })

    execution_context, market = begin_active_cycle(
        ledger, event_id="accept-cycle", symbol="EURUSD", cycle_id=11,
        entity_id="EURUSD_1100", correlation_id="COR-11", timestamp=TS)
    market = reconcile_obligation(ledger, market, [{"entity_id": "EURUSD_1100"}], due=True)
    _, unchanged = begin_active_cycle(
        ledger, event_id="accept-unchanged", symbol="EURUSD", cycle_id=12,
        entity_id="EURUSD_1200", correlation_id="COR-12", timestamp=TS)
    unchanged = ledger.update(unchanged.obligation_id, S.NOT_APPLICABLE,
                              provenance={"semantic": "UNCHANGED"})
    no_opportunity = create_dataset_obligation(
        ledger, event_id="no-opportunity:EURUSD:13", lifecycle_stage="OPPORTUNITY_EVALUATED",
        dataset="opportunities", identity={"opportunity_record_id": "", "symbol": "EURUSD"},
        timestamp=TS, producer="test", trigger="NO_PATTERN", applicable=False)
    opportunity = create_dataset_obligation(
        ledger, event_id="opportunity:OPP-14", lifecycle_stage="OPPORTUNITY_EVALUATED",
        dataset="opportunities", identity={"opportunity_record_id": "OPP-14", "symbol": "EURUSD"},
        timestamp=TS, producer="test", trigger="CANONICAL_OPPORTUNITY")
    assessment = create_dataset_obligation(
        ledger, event_id="assessment:AS-14", lifecycle_stage="ASSESSMENT_SCORED",
        dataset="assessments", identity={"assessment_id": "AS-14", "symbol": "EURUSD"},
        timestamp=TS, producer="test", trigger="SCORING_REACHED")
    strategy_candidate = create_dataset_obligation(
        ledger, event_id="strategy-candidate:SC-14", lifecycle_stage="STRATEGY_CANDIDATE",
        dataset="strategy_candidates", identity={"candidate_id": "SC-14", "symbol": "EURUSD"},
        timestamp=TS, producer="test", trigger="STRATEGY_EVALUATED")
    horizon_candidate = create_dataset_obligation(
        ledger, event_id="horizon-candidate:HC-14", lifecycle_stage="HORIZON_CANDIDATE",
        dataset="horizon_candidates", identity={"candidate_id": "HC-14", "symbol": "EURUSD"},
        timestamp=TS, producer="test", trigger="HORIZON_CLASSIFIER_REACHED")
    trace = create_dataset_obligation(
        ledger, event_id="trace:E14:14:S1", lifecycle_stage="DECISION_TRACE_OBSERVER",
        dataset="decision_trace", identity={"entity_id": "E14", "cycle_id": 14,
        "runtime_session_id": "S1", "symbol": "EURUSD"}, timestamp=TS,
        producer="test", trigger="OBSERVER_REACHED")
    strategy_observation = create_dataset_obligation(
        ledger, event_id="strategy-observation:E14:SO14", lifecycle_stage="STRATEGY_OBSERVER",
        dataset="strategy_observations", identity={"entity_id": "E14",
        "observation_id": "SO14", "symbol": "EURUSD"}, timestamp=TS,
        producer="test", trigger="OBSERVER_REACHED")

    no_trade = create_no_trade_obligations(
        ledger, event_id="decision:NT15", identity={"symbol": "EURUSD", "cycle_id": 15,
        "decision_id": "NT15", "entity_id": "E15"}, timestamp=TS)
    execute_decision, = create_terminal_decision_obligations(
        ledger, event_id="decision:EX16", identity={"symbol": "EURUSD", "cycle_id": 16,
        "decision_id": "EX16", "entity_id": "E16"}, timestamp=TS, no_trade=False)

    account_identity_a = _trade_identity("A", trade="T16", correlation="COR-16")
    account_identity_b = _trade_identity("B", trade="T16", correlation="COR-16")
    result_a, attempt_a = create_account_execution_obligations(
        ledger, event_id="fanout:16", identity=account_identity_a, timestamp=TS,
        order_send_expected=True, attempt_id="AT-A")
    result_b, attempt_b = create_account_execution_obligations(
        ledger, event_id="fanout:16", identity=account_identity_b, timestamp=TS,
        order_send_expected=False)
    result_a = reconcile_obligation(ledger, result_a, [{
        "correlation_id": "COR-16", "account_id": "A",
    }], due=True)
    record_producer_outcome(ledger, attempt_a, succeeded=True, observed_record_id="AT-A")
    attempt_a = ledger.get(attempt_a.obligation_id)
    assert attempt_a is not None

    protection, open_truth = create_filled_position_obligations(
        ledger, event_id="fill:A:1601", identity=_trade_identity("A", trade="T16"), timestamp=TS)
    protection = reconcile_obligation(ledger, protection, [{
        "correlation_id": "C1", "account_id": "A", "position_ticket": 101,
    }], due=True)
    closed_truth, risk, journal = create_closed_trade_obligations(
        ledger, event_id="close:A:T16", identity=_trade_identity("A", trade="T16"),
        timestamp=TS, risk_geometry_valid=True)
    closed_truth = reconcile_obligation(ledger, closed_truth, [{
        "identity": {"trade_id": "T16", "account_id": "A"},
    }], due=True)
    record_producer_outcome(ledger, risk, succeeded=True, observed_record_id="T16")
    risk = ledger.get(risk.obligation_id)
    record_producer_outcome(ledger, journal, succeeded=True, observed_record_id="T16")
    journal = ledger.get(journal.obligation_id)
    missing_truth, _, _ = create_closed_trade_obligations(
        ledger, event_id="close:A:T17", identity=_trade_identity("A", trade="T17"),
        timestamp=TS, risk_geometry_valid=True)
    missing_truth = reconcile_obligation(ledger, missing_truth, [], due=True)

    management = create_dataset_obligation(
        ledger, event_id="management:MA16", lifecycle_stage="MANAGEMENT_ACTION",
        dataset="management_actions", identity={"management_action_id": "MA16",
        "account_id": "A", "position_ticket": 101, "symbol": "EURUSD"},
        timestamp=TS, producer="test", trigger="ACTION_INITIATED")
    ranking = create_dataset_obligation(
        ledger, event_id="portfolio-ranking:R16", lifecycle_stage="PORTFOLIO_CYCLE",
        dataset="portfolio_rankings", identity={"ranking_id": "R16", "symbol": "EURUSD"},
        timestamp=TS, producer="test", trigger="RANKING_POOL_EXISTS")
    comparison = create_dataset_obligation(
        ledger, event_id="portfolio-shadow:16:S1", lifecycle_stage="PORTFOLIO_CYCLE",
        dataset="portfolio_shadow", identity={"cycle_id": 16,
        "runtime_session_id": "S1", "symbol": "PORTFOLIO"}, timestamp=TS,
        producer="test", trigger="COMPARISON_ENABLED")
    shadow_progress = [create_dataset_obligation(
        ledger, event_id=f"shadow-runtime:SH1:PROGRESS:{bar}",
        lifecycle_stage="SHADOW_RUNTIME_EVENT", dataset="shadow_runtime",
        identity={"event_id": f"SH1:PROGRESS:{bar}", "symbol": "EURUSD"},
        timestamp=TS, producer="test", trigger="PROGRESS") for bar in (12, 24)]
    producer_failed = ledger.update(
        assessment.obligation_id, S.PRODUCER_FAILED,
        failure_reason="ASSESSMENT_SERIALIZATION_FAILED")
    ambiguous = create_dataset_obligation(
        ledger, event_id="candidate:AMB1", lifecycle_stage="STRATEGY_CANDIDATE",
        dataset="strategy_candidates", identity={"candidate_id": "AMB1", "symbol": "EURUSD"},
        timestamp=TS, producer="test", trigger="CANDIDATE")
    ambiguous = reconcile_obligation(ledger, ambiguous, [
        {"candidate_id": "AMB1"}, {"candidate_id": "AMB1"},
    ], due=True)
    quarantine = create_dataset_obligation(
        ledger, event_id="quarantine:Q16", lifecycle_stage="CONTRACT_REJECTION",
        dataset="quarantine", identity={"record_id": "Q16", "symbol": "EURUSD"},
        timestamp=TS, producer="test", trigger="CONTRACT_REJECTED")

    for event, obligation, account, reason in (
        ("ACTIVE_CYCLE", execution_context, "", "cycle opened"),
        ("MARKET_CHANGED", market, "", "material context reconciled"),
        ("MARKET_UNCHANGED", unchanged, "", "semantic unchanged"),
        ("NO_OPPORTUNITY", no_opportunity, "", "no pattern"),
        ("OPPORTUNITY_ASSESSMENT", opportunity, "", "opportunity evaluated"),
        ("ASSESSMENT_FAILED", producer_failed, "", "writer failure"),
        ("STRATEGY_CANDIDATE", strategy_candidate, "", "candidate evaluated"),
        ("HORIZON_CANDIDATE", horizon_candidate, "", "classifier reached"),
        ("DECISION_TRACE", trace, "", "observer reached"),
        ("STRATEGY_OBSERVATION", strategy_observation, "", "observer reached"),
        ("NO_TRADE", next(x for x in no_trade if x.expected_dataset == "decision_ledger"), "", "terminal"),
        ("EXECUTE", execute_decision, "", "terminal"),
        ("ACCOUNT_A_RESULT", result_a, "A", "exact reconciliation"),
        ("ACCOUNT_A_ATTEMPT", attempt_a, "A", "local fsync pending canonical"),
        ("ACCOUNT_B_RESULT", result_b, "B", "preflight route"),
        ("ACCOUNT_B_ATTEMPT", attempt_b, "B", "no order send"),
        ("FILLED_PROTECTION", protection, "A", "exact account/ticket reconciliation"),
        ("OPEN_TRADE", open_truth, "A", "not due until close"),
        ("CLOSED_TRADE_TRUTH", closed_truth, "A", "nested identity reconciled"),
        ("CLOSED_TRADE_MISSING", missing_truth, "A", "exact record absent after close"),
        ("RISK_DEVIATION", risk, "A", "local fsync pending canonical"),
        ("TRADE_JOURNAL", journal, "A", "local fsync pending canonical"),
        ("MANAGEMENT_ACTION", management, "A", "exact action/account/ticket"),
        ("PORTFOLIO_RANKING", ranking, "", "ranking pool exists"),
        ("PORTFOLIO_COMPARISON", comparison, "", "cycle/session identity"),
        ("SHADOW_PROGRESS_12", shadow_progress[0], "", "deterministic event id"),
        ("SHADOW_PROGRESS_24", shadow_progress[1], "", "distinct deterministic event id"),
        ("AMBIGUOUS_CANDIDATE", ambiguous, "", "duplicate exact matches"),
        ("QUARANTINE", quarantine, "", "actual contract rejection"),
    ):
        capture(event, obligation, account, reason)

    rows_by_obligation: dict[str, list[dict]] = {}
    for row in rows:
        rows_by_obligation.setdefault(row["obligation"], []).append(row)
    duplicates = [items for items in rows_by_obligation.values() if len(items) > 1]
    assert all({item["dataset"] for item in items} == {"trade_truth"}
               and {item["event"] for item in items} == {"OPEN_TRADE", "CLOSED_TRADE_TRUTH"}
               for items in duplicates)
    assert {row["status"] for row in rows} == {status.value for status in S}
    assert next(row for row in rows if row["event"] == "ACCOUNT_A_RESULT")["status"] == S.PRESENT.value
    assert next(row for row in rows if row["event"] == "ACCOUNT_B_RESULT")["status"] == S.NOT_YET_DUE.value
    assert next(row for row in rows if row["event"] == "CLOSED_TRADE_TRUTH")["status"] == S.PRESENT.value
    assert next(row for row in rows if row["event"] == "OPEN_TRADE")["status"] == S.NOT_YET_DUE.value
    assert len({row["obligation"] for row in rows if row["event"].startswith("SHADOW_PROGRESS")}) == 2

    reloaded = LifecycleEvidenceLedger(ledger.path)
    assert len(reloaded.obligations()) == len(ledger.obligations())
    assert {item.current_status for item in reloaded.obligations()} >= {status.value for status in S}