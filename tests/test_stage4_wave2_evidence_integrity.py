"""Stage IV Wave 2 evidence-integrity guarantees."""

from __future__ import annotations

from datetime import datetime, timezone
import json

from research_engine.v10.universes import models
from research_engine.v10.universes.evidence_integrity import (
    CounterpartStatus,
    EvidenceBatch,
    EvidenceObject,
    ExpectedEvidence,
    GapClass,
    IntegrityStatus,
    ReconstructionState,
    audit_batch,
    build_manifest,
    build_report,
    describe_evidence_integrity,
    evaluate_counterparts,
    reconcile_manifest,
    reconstruct_outcomes,
    reconstruct_shadow_outcomes,
    reconstruct_shadow_outcomes_report,
    write_report,
)
from research_engine.v10.universes.models import Universe


NOW = datetime(2026, 9, 26, tzinfo=timezone.utc)


def _decision(entity: str = "e1", **updates):
    record = {
        "entity_id": entity,
        "correlation_id": f"c-{entity}",
        "symbol": "EURUSD",
        "timestamp_utc": "2026-09-25T10:00:00+00:00",
        "action": "EXECUTE",
    }
    record.update(updates)
    return record


def _execution(trade: str = "t1", **updates):
    record = {
        "trade_id": trade,
        "entity_id": "e1",
        "correlation_id": "c-e1",
        "symbol": "EURUSD",
        "entry_time": "2026-09-25T10:01:00+00:00",
        "exit_time": "2026-09-25T11:00:00+00:00",
        "r_multiple": 1.2,
    }
    record.update(updates)
    return record


def _batch(universe=Universe.DECISION, dataset="decision_trace", records=(), **updates):
    values = {
        "universe": universe,
        "dataset": dataset,
        "records": tuple(records),
        "scope": {"symbol": "EURUSD", "date": "2026-09-25"},
        "schema_version": f"{dataset}_v1",
    }
    values.update(updates)
    return EvidenceBatch(**values)


def _shadow_events():
    common = {
        "schema_version": "shadow_runtime_v1",
        "shadow_trade_id": "nshadow_1",
        "plan_id": "plan-1",
        "canonical_opportunity_id": "opp-1",
        "horizon": "SCALP",
        "symbol": "EURUSD",
    }
    opened = {
        **common,
        "event_type": "OPEN",
        "entry_market_time_utc_epoch_s": 1790326800,
        "identity": {
            "entity_id": "e1", "evaluated_horizon": "SCALP",
            "trade_horizon": "SCALP", "shadow_type": "HORIZON_ALTERNATIVE",
        },
        "construction": {
            "direction": "BUY", "entry_price": 1.1, "stop_loss": 1.09,
            "take_profit": 1.12, "risk_distance": 0.01,
        },
        "live_facts": {"v10_action": "EXECUTE"},
    }
    closed = {
        **common,
        "event_type": "CLOSE",
        "exit_reason": "take_profit",
        "exit_market_time_utc_epoch_s": 1790330400,
        "exit_price": 1.12,
        "bars_held": 12,
        "outcome": {"pnl_r_multiple": 2.0, "mfe_r": 2.1, "mae_r": -0.2},
    }
    return opened, closed


def test_wave2_consumes_wave1_and_active_universe_authority():
    description = describe_evidence_integrity()
    assert description["authority"] == "research_engine.v10.universes.assurance"
    assert description["active_universes"] == [u.value for u in models.ACTIVE_UNIVERSES]
    assert [item["universe"] for item in description["universes"]] == [
        universe.value for universe in models.ACTIVE_UNIVERSES
    ]
    assert "SHADOW_REALITY" not in [item["universe"] for item in description["universes"]]
    assert description["mutates_source_evidence"] is False


def test_manifest_is_deterministic_and_semantic_not_just_a_file_list():
    batch = _batch(
        records=(_decision(),),
        source_objects=(EvidenceObject("key-b", checksum="b"), EvidenceObject("key-a", checksum="a")),
    )
    first = build_manifest(batch)
    second = build_manifest(batch)
    assert first == second
    assert first.fingerprint == second.fingerprint
    assert first.record_count == first.unique_identity_count == 1
    assert first.storage_prefix == "supporting/decision_trace"
    assert first.partition_model == "symbol_date"
    assert [item["identifier"] for item in first.source_objects] == ["key-a", "key-b"]


def test_manifest_reconciliation_detects_added_missing_and_modified_source():
    old = build_manifest(_batch(records=(_decision(),)))
    added = build_manifest(_batch(records=(_decision(), _decision("e2"))))
    modified = build_manifest(_batch(records=(_decision(action="NO_TRADE"),)))
    assert reconcile_manifest(old, old)[0]["status"] == IntegrityStatus.INTACT.value
    assert reconcile_manifest(added, None)[0]["status"] == IntegrityStatus.NEW_UNMANIFESTED.value
    assert reconcile_manifest(None, old)[0]["status"] == IntegrityStatus.MANIFEST_SOURCE_MISSING.value
    assert {item["field"] for item in reconcile_manifest(added, old)} >= {"record_count", "content_hash"}
    assert reconcile_manifest(modified, old)[0]["status"] == IntegrityStatus.SOURCE_MODIFIED.value


def test_presence_distinguishes_legitimate_unexpected_and_unknown_absence():
    legitimate = audit_batch(_batch(records=(), qualifying_activity_expected=False))[1]
    missing = audit_batch(_batch(records=(), qualifying_activity_expected=True))[1]
    unknown = audit_batch(_batch(records=(), qualifying_activity_expected=None))[1]
    assert {f.status for f in legitimate} == {IntegrityStatus.ABSENT_LEGITIMATE.value}
    assert IntegrityStatus.MISSING_EXPECTED.value in {f.status for f in missing}
    assert IntegrityStatus.AMBIGUOUS.value in {f.status for f in unknown}


def test_expected_gap_classes_remain_distinct_and_carry_upstream_proof():
    classes = list(GapClass)
    expected = tuple(
        ExpectedEvidence(
            Universe.DECISION, "decision_trace", {"entity_id": f"e-{index}"},
            "upstream pipeline fact requires a terminal decision", (f"upstream-{index}",),
            gap_class=gap_class,
        )
        for index, gap_class in enumerate(classes)
    )
    findings = audit_batch(_batch(records=(), qualifying_activity_expected=None), expected=expected)[1]
    gaps = [item for item in findings if item.category == "EVIDENCE_GAP"]
    assert {item.status for item in gaps} == {
        IntegrityStatus.ABSENT_LEGITIMATE.value,
        IntegrityStatus.MISSING_EXPECTED.value,
        IntegrityStatus.DELAYED.value,
        IntegrityStatus.HISTORICAL_LIMITATION.value,
        IntegrityStatus.AMBIGUOUS.value,
    }
    assert all(item.evidence for item in gaps)


def test_duplicate_classes_exact_same_payload_across_objects_and_conflict():
    exact = _decision(_source_object="object-a")
    same_other_object = _decision(_source_object="object-b")
    conflict = _decision(action="NO_TRADE", _source_object="object-c")

    exact_findings = audit_batch(_batch(records=(exact, dict(exact))))[1]
    same_findings = audit_batch(_batch(records=(exact, same_other_object)))[1]
    conflict_findings = audit_batch(_batch(records=(exact, conflict)))[1]
    assert "EXACT_RECORD" in {item.category for item in exact_findings}
    assert "IDENTITY_SAME_PAYLOAD" in {item.category for item in same_findings}
    assert "IDENTITY_CONFLICTING_PAYLOAD" in {item.category for item in conflict_findings}
    assert IntegrityStatus.IDENTITY_CONFLICT.value in {item.status for item in conflict_findings}


def test_identity_nullability_and_universe_specific_timestamp_ordering():
    missing_identity = audit_batch(_batch(records=(_decision(entity=""),)), now=NOW)[1]
    bad_order = audit_batch(_batch(
        universe=Universe.EXECUTION, dataset="trade_truth",
        records=(_execution(entry_time="2026-09-25T12:00:00+00:00"),),
        schema_version="trade_truth_v1",
    ), now=NOW)[1]
    naive = audit_batch(_batch(records=(_decision(timestamp_utc="2026-09-25T10:00:00"),)), now=NOW)[1]
    assert "IDENTITY_NULLABILITY" in {item.category for item in missing_identity}
    assert "EVENT_ORDERING" in {item.category for item in bad_order}
    assert "TIMESTAMP_REPRESENTATION" in {item.category for item in naive}


def test_partition_consistency_and_late_arrival_are_distinct_from_event_ordering():
    record = _decision(
        _partition_date="2026-09-24",
        _persistence_timestamp="2026-09-25T10:10:01+00:00",
    )
    findings = audit_batch(_batch(
        records=(record,), expected_arrival_lag_seconds=600,
    ), now=NOW)[1]
    categories = {item.category: item.status for item in findings}
    assert categories["PARTITION_TIMESTAMP_MISMATCH"] == IntegrityStatus.ORDERING_VIOLATION.value
    assert categories["LATE_ARRIVAL"] == IntegrityStatus.DELAYED.value


def test_shadow_lifecycle_complete_open_cross_partition_and_invalid_cases():
    opened, closed = _shadow_events()
    complete = _batch(
        Universe.SHADOW_OUTCOME, "shadow_runtime", (opened, closed),
        schema_version="shadow_runtime_v1",
        source_objects=(
            EvidenceObject("date=2026-09-24/part-a.jsonl"),
            EvidenceObject("date=2026-09-25/part-b.jsonl"),
        ),
    )
    assert not [f for f in audit_batch(complete)[1] if f.severity == "ERROR"]

    currently_open = _batch(Universe.SHADOW_OUTCOME, "shadow_runtime", (opened,), schema_version="shadow_runtime_v1")
    assert not [f for f in audit_batch(currently_open)[1] if f.category == "BROKEN_CONTINUITY"]

    close_only = _batch(Universe.SHADOW_OUTCOME, "shadow_runtime", (closed,), schema_version="shadow_runtime_v1")
    duplicate_close = _batch(Universe.SHADOW_OUTCOME, "shadow_runtime", (opened, closed, dict(closed)), schema_version="shadow_runtime_v1")
    conflicting_open = dict(opened, construction={**opened["construction"], "entry_price": 2.0})
    multiple_open = _batch(Universe.SHADOW_OUTCOME, "shadow_runtime", (opened, conflicting_open), schema_version="shadow_runtime_v1")
    assert "CLOSE_WITHOUT_OPEN" in {f.category for f in audit_batch(close_only)[1]}
    assert "DUPLICATE_TERMINAL" in {f.category for f in audit_batch(duplicate_close)[1]}
    assert "MULTIPLE_OPEN" in {f.category for f in audit_batch(multiple_open)[1]}

    early_close = dict(closed, exit_market_time_utc_epoch_s=1790320000)
    invalid_order = _batch(Universe.SHADOW_OUTCOME, "shadow_runtime", (early_close, opened), schema_version="shadow_runtime_v1")
    assert "LIFECYCLE_ORDERING" in {f.category for f in audit_batch(invalid_order)[1]}


def test_counterparts_present_not_required_pending_missing_and_ambiguous():
    execute = _batch(records=(_decision(),))
    no_trade = _batch(records=(_decision("e2", action="NO_TRADE"),))
    available = {Universe.EXECUTION: (_execution(),)}
    present = evaluate_counterparts(execute, available)
    not_required = evaluate_counterparts(no_trade, {})
    pending = evaluate_counterparts(execute, {}, pending_identities=({"entity_id": "e1", "correlation_id": "c-e1"},))
    missing = evaluate_counterparts(execute, {})
    ambiguous_market = [item for item in present if item["counterpart_universe"] == "MARKET"]
    assert CounterpartStatus.PRESENT.value in {item["status"] for item in present}
    assert CounterpartStatus.NOT_REQUIRED.value in {item["status"] for item in not_required}
    assert CounterpartStatus.PENDING.value in {item["status"] for item in pending}
    assert CounterpartStatus.MISSING.value in {item["status"] for item in missing}
    assert ambiguous_market[0]["status"] == CounterpartStatus.AMBIGUOUS.value


def test_shadow_reconstruction_is_deterministic_idempotent_and_provenanced():
    opened, closed = _shadow_events()
    first = reconstruct_shadow_outcomes((opened, closed), reconstruction_timestamp="2026-09-26T12:00:00Z")
    second = reconstruct_shadow_outcomes((opened, closed), reconstruction_timestamp="2026-09-26T12:00:00Z")
    assert first == second
    assert len(first) == 1
    assert first[0].state == ReconstructionState.RECONSTRUCTED.value
    assert first[0].source_datasets == ("shadow_runtime",)
    assert first[0].target_dataset == "shadow_trades"
    assert first[0].artifact["source_schema_version"] == "shadow_runtime_v1"
    assert "legacy" not in first[0].reconstruction_rule


def test_shadow_reconstruction_indexes_lifecycle_events_once(monkeypatch):
    from research_engine.v10.universes import evidence_integrity as module

    events = []
    for index in range(12):
        opened, closed = _shadow_events()
        updates = {
            "shadow_trade_id": f"nshadow_{index}",
            "canonical_opportunity_id": f"opp-{index}",
        }
        events.extend(({**opened, **updates}, {**closed, **updates}))
    calls = 0
    original = module._shadow_lifecycle_identity

    def counted(record):
        nonlocal calls
        calls += 1
        return original(record)

    monkeypatch.setattr(module, "_shadow_lifecycle_identity", counted)
    result = module.reconstruct_shadow_outcomes_report(
        events, reconstruction_timestamp="2026-09-26T12:00:00Z",
    )
    assert len(result.artifacts) == 12
    assert calls == len(events) + len(result.artifacts)


def test_shadow_provenance_isolated_by_opportunity_and_horizon():
    events = []
    cases = (("opp-a", "SCALP"), ("opp-b", "SCALP"), ("opp-a", "INTRADAY"))
    for opportunity, horizon in cases:
        opened, closed = _shadow_events()
        updates = {
            "shadow_trade_id": "nshadow_reused",
            "canonical_opportunity_id": opportunity,
            "horizon": horizon,
        }
        opened = {**opened, **updates, "identity": {
            **opened["identity"], "evaluated_horizon": horizon,
            "trade_horizon": horizon,
        }}
        events.extend((opened, {**closed, **updates}))

    result = reconstruct_shadow_outcomes_report(
        events, reconstruction_timestamp="2026-09-26T12:00:00Z",
    )
    assert len(result.artifacts) == 3
    for artifact in result.artifacts:
        identity = artifact.artifact["identity"]
        opportunity = identity["canonical_opportunity_id"]
        horizon = identity["evaluated_horizon"]
        assert len(artifact.source_identities) == 2
        assert all(f":{opportunity}:{horizon}:" in item for item in artifact.source_identities)


def test_shadow_provenance_and_fingerprint_are_order_deterministic():
    opened, closed = _shadow_events()
    first = reconstruct_shadow_outcomes_report(
        (opened, closed), reconstruction_timestamp="2026-09-26T12:00:00Z",
    )
    second = reconstruct_shadow_outcomes_report(
        (closed, opened), reconstruction_timestamp="2026-09-26T12:00:00Z",
    )
    assert first == second
    assert first.fingerprint == second.fingerprint


def test_incomplete_and_ambiguous_shadow_identity_fail_closed():
    opened, closed = _shadow_events()
    incomplete = reconstruct_shadow_outcomes_report(
        ({**opened, "canonical_opportunity_id": ""},
         {**closed, "canonical_opportunity_id": ""}),
        reconstruction_timestamp="2026-09-26T12:00:00Z",
    )
    assert incomplete.artifacts == ()
    assert [item.reason for item in incomplete.limitations] == [
        "INCOMPLETE_CANONICAL_LIFECYCLE_IDENTITY"
    ]

    ambiguous = reconstruct_shadow_outcomes_report(
        (opened, dict(opened), closed),
        reconstruction_timestamp="2026-09-26T12:00:00Z",
    )
    assert ambiguous.artifacts == ()
    assert ambiguous.limitations[0].reason == (
        "AMBIGUOUS_CANONICAL_LIFECYCLE_CARDINALITY:open=2,close=1"
    )


def test_valid_shadow_reconstruction_record_semantics_are_unchanged():
    from research_engine.data_access.shadow_runtime_ingestion import (
        reconstruct_completed_shadow_trades,
    )

    opened, closed = _shadow_events()
    expected = reconstruct_completed_shadow_trades([opened, closed])
    actual = reconstruct_shadow_outcomes_report(
        (opened, closed), reconstruction_timestamp="2026-09-26T12:00:00Z",
    )
    assert [item.artifact for item in actual.artifacts] == expected
    assert actual.limitations == ()


def test_outcome_reconstruction_is_exact_non_destructive_and_idempotent():
    source = _execution()
    before = json.loads(json.dumps(source))
    first = reconstruct_outcomes((source,), reconstruction_timestamp="2026-09-26T12:00:00Z")
    second = reconstruct_outcomes((source,), reconstruction_timestamp="2026-09-26T12:00:00Z")
    assert first == second
    assert source == before
    assert first[0].target_universe == "OUTCOME"
    assert first[0].artifact == source


def test_historical_legacy_shadow_is_limitation_never_primary():
    batch = _batch(
        Universe.SHADOW_OUTCOME, "shadow_trades", (),
        schema_version="shadow_trades_v1", qualifying_activity_expected=False,
    )
    findings = audit_batch(batch)[1]
    assert "LEGACY_COMPATIBILITY_SOURCE" in {item.category for item in findings}
    assert IntegrityStatus.HISTORICAL_LIMITATION.value in {item.status for item in findings}
    assert describe_evidence_integrity()["reconstructable"]["SHADOW_OUTCOME"].startswith("shadow_runtime")


def test_reports_findings_and_persistence_are_restart_deterministic(tmp_path):
    batch = _batch(records=(_decision(), _decision()))
    first = build_report((batch,), now=NOW)
    second = build_report((batch,), now=NOW)
    assert first == second
    assert [f.finding_id for f in first.findings] == [f.finding_id for f in second.findings]
    path_a = write_report(first, tmp_path / "a.json")
    path_b = write_report(second, tmp_path / "b.json")
    assert path_a.read_bytes() == path_b.read_bytes()
    assert json.loads(path_a.read_text(encoding="utf-8"))["active_universes"] == [
        universe.value for universe in models.ACTIVE_UNIVERSES
    ]


def test_unreconstructable_gaps_are_explicit_and_never_synthesised():
    expectation = ExpectedEvidence(
        Universe.MARKET, "market_context", {"entity_id": "lost"},
        "decision references missing market context", ("decision:e1",),
        gap_class=GapClass.EXPECTED_MISSING,
    )
    batch = _batch(
        Universe.MARKET, "market_context", (), schema_version="market_context_v1",
        qualifying_activity_expected=True,
    )
    report = build_report((batch,), expectations=(expectation,), now=NOW)
    gaps = [finding for finding in report.findings if finding.category == "EVIDENCE_GAP"]
    assert gaps[0].reconstruction_status == ReconstructionState.UNRECONSTRUCTABLE.value
    assert report.reconstructions == ()
