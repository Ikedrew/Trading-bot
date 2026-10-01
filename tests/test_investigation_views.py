from __future__ import annotations

from copy import deepcopy

from research_engine.v10.investigation_views import (
    InvestigationFilters,
    InvestigationViews,
)


class Source:
    def __init__(self, datasets):
        self.datasets = deepcopy(datasets)
        self.calls = []

    def read_dataset(self, dataset, **kwargs):
        self.calls.append((dataset, kwargs))
        rows = deepcopy(self.datasets.get(dataset, []))
        symbol = kwargs.get("symbol")
        if symbol:
            rows = [row for row in rows if (row.get("symbol") or (row.get("identity") or {}).get("symbol")) == symbol]
        return rows


def truth(trade_id="T1", correlation="C1", symbol="EURUSD", opportunity="O1", r=1.5):
    return {
        "schema_version": "trade_truth_v1",
        "identity": {"trade_id": trade_id, "correlation_id": correlation, "symbol": symbol,
                 "account_id": "ACCOUNT-1",
                     "position_ticket": 901,
                     "canonical_opportunity_id": opportunity},
        "execution": {"entry_fill_price": 1.1, "exit_fill_price": 1.2, "volume_executed": 0.1},
        "timestamps": {"entry_timestamp_broker": "2026-01-01T00:00:00Z",
                       "exit_timestamp_broker": "2026-01-01T01:00:00Z", "duration_seconds": 3600},
        "outcome": {"r_multiple_realised": r, "pnl_realised": 15.0, "net_profit": 14.0},
        "exit": {"exit_reason": "take_profit"},
    }


def decision(correlation="C1", entity="E1", decision_id="D1", symbol="EURUSD"):
    return {
        "schema_version": "decision_trace_v1", "correlation_id": correlation,
        "entity_id": entity, "decision_id": decision_id, "symbol": symbol,
        "timestamp_utc": "2026-01-01T00:00:00Z", "action": "EXECUTE",
        "pattern_name": "HAMMER", "score_strategy": 71,
        "v10_strategy": {"family": "REVERSAL", "direction": "BUY", "confidence": 0.8},
        "v10_entry": {"stop_loss": 1.09, "take_profit": 1.12},
        "v10_market_state": {"market_phase": "IMPULSE", "regime": {"regime": "TRENDING"}},
        "v10_risk": {"risk_percentage": 0.5, "position_size": 0.1},
    }


def execution(correlation="C1", entity="E1"):
    return {
        "schema_version": "execution_results_v1", "correlation_id": correlation,
        "entity_id": entity, "account_id": "ACCOUNT-1", "position_ticket": 901,
        "result_ok": True, "deal_ticket": "DEAL1",
        "fill_price": 1.1, "entry_reference": 1.0999,
    }


def datasets():
    return {
        "trade_truth": [truth()], "execution_results": [execution()],
        "decision_trace": [decision()], "protection_audit": [], "risk_deviation": [],
        "execution_attempts": [], "execution_context": [], "shadow_runtime": [],
    }


def shadow_events():
    common = {
        "schema_version": "shadow_runtime_v1", "shadow_trade_id": "nshadow_1",
        "canonical_opportunity_id": "O1", "symbol": "EURUSD", "horizon": "SCALP",
    }
    opened = {
        **common, "event_type": "OPEN", "entry_market_time_utc_epoch_s": 1_767_225_600,
        "entry_market_time_utc_iso8601": "2026-01-01T00:00:00Z",
        "identity": {"entity_id": "E1", "cycle_id": 1, "trade_horizon": "SCALP",
                     "evaluated_horizon": "SCALP", "shadow_type": "TREATMENT"},
        "construction": {"direction": "BUY", "entry_price": 1.1, "stop_loss": 1.09,
                         "take_profit": 1.12, "risk_distance": 0.01, "intended_rr": 2.0},
        "live_facts": {"strategy": "REVERSAL", "pattern": "HAMMER", "regime": "TRENDING"},
        "experiment_arm": {"treatment_id": "TR1", "experiment_id": "EX1"},
    }
    closed = {
        **common, "event_type": "CLOSE", "exit_market_time_utc_epoch_s": 1_767_229_200,
        "exit_market_time_utc_iso8601": "2026-01-01T01:00:00Z",
        "exit_reason": "take_profit", "exit_price": 1.12, "bars_held": 12,
        "outcome": {"pnl_r_multiple": 2.0, "mfe_r": 2.1, "mae_r": -0.2,
                    "risk_distance": 0.01, "intended_rr": 2.0},
    }
    return [opened, closed]


def test_trade_truth_is_authority_and_optional_absence_does_not_remove_it():
    data = datasets()
    data["execution_results"] = []
    data["decision_trace"] = []
    result = InvestigationViews(Source(data)).build_trade_investigation()
    assert result.records[0]["outcome_authority"] == "trade_truth_v1"
    assert result.records[0]["r_multiple"] == 1.5
    assert {"execution_results", "decision_trace"} <= set(result.records[0]["_provenance"]["missing_optional_evidence"])
    assert result.accounting["balanced"] is True


def test_exact_identity_join_and_provenance_survive():
    result = InvestigationViews(Source(datasets())).build_trade_investigation()
    row = result.records[0]
    assert row["decision_id"] == "D1"
    assert row["entity_id"] == "E1"
    assert row["pattern"] == "HAMMER"
    assert [source["dataset"] for source in row["_provenance"]["sources"]] == [
        "trade_truth", "execution_results", "decision_trace"
    ]


def test_ambiguous_primary_identity_is_excluded_not_selected():
    data = datasets()
    data["trade_truth"].append(truth(r=-1.0))
    result = InvestigationViews(Source(data)).build_trade_investigation()
    assert result.records == ()
    assert result.accounting["rows_excluded_ambiguous_identity"] == 2
    assert result.accounting["balanced"] is True


def test_ambiguous_optional_join_retains_truth_but_does_not_choose():
    data = datasets()
    data["execution_results"].append({**execution(), "deal_ticket": "DEAL2"})
    result = InvestigationViews(Source(data)).build_trade_investigation()
    row = result.records[0]
    assert row["execution_result"] is None
    assert "execution_results:ambiguous_exact_identity" in row["_provenance"]["join_issues"]


def test_all_58_account_scoped_trade_rows_resolve_exact_execution_identity():
    data = datasets()
    data["trade_truth"] = []
    data["execution_results"] = []
    data["decision_trace"] = []
    for index in range(58):
        correlation = f"C{index}"
        account = f"ACCOUNT-{index % 3}"
        data["trade_truth"].append({
            **truth(f"T{index}", correlation, opportunity=f"O{index}"),
            "identity": {
                **truth(f"T{index}", correlation, opportunity=f"O{index}")["identity"],
                "account_id": account,
            },
        })
        data["execution_results"].append({
            **execution(correlation, f"E{index}"), "account_id": account,
        })
        data["decision_trace"].append(decision(correlation, f"E{index}", f"D{index}"))

    result = InvestigationViews(Source(data)).build_trade_investigation()

    assert len(result.records) == 58
    assert result.accounting["eligible_rows"] == 58
    assert result.accounting["joined_rows"] == 58
    assert result.accounting["ambiguous_evidence_by_source_field"] == {}
    assert all(row["execution_result"] is not None for row in result.records)
    assert all(row["_provenance"]["join_issues"] == [] for row in result.records)


def test_decision_truth_never_attaches_across_accounts_and_fanout_is_explicit():
    data = datasets()
    data["execution_results"] = [
        {**execution(), "account_id": "ACCOUNT-A", "deal_ticket": "A"},
        {**execution(), "account_id": "ACCOUNT-B", "deal_ticket": "B"},
    ]
    data["trade_truth"] = [{
        **truth(),
        "identity": {**truth()["identity"], "account_id": "ACCOUNT-B"},
    }]

    result = InvestigationViews(Source(data)).build_decision_execution_outcome()
    by_account = {row["execution_account_id"]: row for row in result.records}

    assert by_account["ACCOUNT-A"]["realised_outcome"] is None
    assert "trade_truth:account_id_mismatch" in by_account["ACCOUNT-A"]["_provenance"]["join_issues"]
    assert by_account["ACCOUNT-B"]["realised_account_id"] == "ACCOUNT-B"
    assert result.accounting["unique_decision_population"] == 1
    assert result.accounting["account_fanned_execution_result_rows"] == 2
    assert result.accounting["outcome_attached_rows"] == 1
    assert result.accounting["fan_out"]["additional_account_fan_out_rows"] == 1
    assert result.accounting["balanced_scope"] == "unique_decision_id_population_only"
    assert result.accounting["row_expansion_balanced"] is True


def test_protection_audit_uses_account_ticket_identity():
    data = datasets()
    data["protection_audit"] = [
        {"audit_id": "right", "correlation_id": "C1", "account_id": "ACCOUNT-1",
         "position_ticket": 901, "protection_status": "VERIFIED"},
        {"audit_id": "other-account", "correlation_id": "C1", "account_id": "ACCOUNT-2",
         "position_ticket": 901, "protection_status": "FAILED_MISMATCH"},
    ]

    row = InvestigationViews(Source(data)).build_trade_investigation().records[0]
    audit_ids = [
        source["source_record_identity"]
        for source in row["_provenance"]["sources"]
        if source["dataset"] == "protection_audit"
    ]

    assert audit_ids == ["right"]


def test_risk_deviation_uses_exact_trade_id_without_account_inference():
    data = datasets()
    data["risk_deviation"] = [
        {"observation_id": "right", "trade_id": "T1", "correlation_id": "C1"},
        {"observation_id": "other-trade", "trade_id": "T2", "correlation_id": "C1"},
    ]

    row = InvestigationViews(Source(data)).build_trade_investigation().records[0]
    deviation_ids = [
        source["source_record_identity"]
        for source in row["_provenance"]["sources"]
        if source["dataset"] == "risk_deviation"
    ]

    assert deviation_ids == ["right"]
    assert "account_id" not in data["risk_deviation"][0]


def test_missing_and_ambiguous_exact_execution_keys_are_reported_separately():
    data = datasets()
    data["execution_results"] = [
        {**execution(), "account_id": "ACCOUNT-OTHER"},
    ]
    missing_result = InvestigationViews(Source(data)).build_trade_investigation()
    missing_row = missing_result.records[0]
    assert missing_row["execution_result"] is None
    assert missing_result.accounting["missing_evidence_by_source_field"][
        "execution_results.correlation_id+account_id"
    ] == 1
    assert missing_result.accounting["ambiguous_evidence_by_source_field"] == {}

    data["execution_results"] = [
        execution(), {**execution(), "deal_ticket": "DEAL2"},
    ]
    ambiguous_result = InvestigationViews(Source(data)).build_trade_investigation()
    ambiguous_row = ambiguous_result.records[0]
    assert ambiguous_row["execution_result"] is None
    assert ambiguous_result.accounting["missing_evidence_by_source_field"].get(
        "execution_results.correlation_id+account_id", 0
    ) == 0
    assert ambiguous_result.accounting["ambiguous_evidence_by_source_field"][
        "execution_results.correlation_id+account_id"
    ] == 1


def test_duplicate_trade_id_risk_history_fails_closed():
    data = datasets()
    data["risk_deviation"] = [
        {"observation_id": "one", "trade_id": "T1"},
        {"observation_id": "two", "trade_id": "T1"},
    ]

    result = InvestigationViews(Source(data)).build_trade_investigation()
    row = result.records[0]

    assert row["risk_deviation_evidence"] is None
    assert "risk_deviation:ambiguous_trade_id" in row["_provenance"]["join_issues"]


def test_execution_quality_excludes_protection_verification_primary_rows():
    data = datasets()
    verification = {
        **execution(), "account_execution_id": "verification-row",
        "comment": "protection_verification",
    }
    data["execution_results"].append(verification)

    result = InvestigationViews(Source(data)).build_execution_quality()

    assert result.accounting["source_rows_loaded"]["execution_results"] == 2
    assert result.accounting["execution_results_primary_rows"] == 1
    assert result.accounting["execution_results_protection_verification_rows"] == 1
    assert result.accounting["primary_rows"] == 1
    assert result.accounting["output_rows"] == 1


def test_execution_quality_bridges_risk_by_exact_truth_trade_id():
    data = datasets()
    data["execution_attempts"] = []
    data["risk_deviation"] = [{"observation_id": "R1", "trade_id": "T1"}]

    result = InvestigationViews(Source(data)).build_execution_quality()
    row = result.records[0]
    source_ids = {
        (source["dataset"], source["source_record_identity"])
        for source in row["_provenance"]["sources"]
    }

    assert ("trade_truth", "T1") in source_ids
    assert ("risk_deviation", "R1") in source_ids
    assert row["related_evidence"]["risk_deviation"] is not None
    assert result.accounting["missing_evidence_by_source_field"].get(
        "risk_deviation.trade_id", 0
    ) == 0


def test_execution_quality_fails_closed_on_ambiguous_truth_bridge():
    data = datasets()
    data["execution_attempts"] = []
    data["trade_truth"].append({
        **truth("T2"),
        "identity": {**truth("T2")["identity"], "account_id": "ACCOUNT-1"},
    })
    data["risk_deviation"] = [{"observation_id": "R1", "trade_id": "T1"}]

    result = InvestigationViews(Source(data)).build_execution_quality()
    row = result.records[0]

    assert row["related_evidence"]["trade_truth"] is None
    assert row["related_evidence"]["risk_deviation"] is None
    assert "trade_truth:ambiguous_correlation_account_identity" in row["_provenance"]["join_issues"]
    assert result.accounting["ambiguous_evidence_by_source_field"][
        "trade_truth.correlation_id+account_id"
    ] == 1


def test_filters_and_repeated_builds_are_deterministic_and_read_only():
    data = datasets()
    data["trade_truth"].append(truth("T2", "C2", "GBPUSD", "O2", -1.0))
    before = deepcopy(data)
    source = Source(data)
    views = InvestigationViews(source)
    filters = InvestigationFilters(symbol="EURUSD", regime="TRENDING")
    one = views.build_context_performance(filters)
    two = views.build_context_performance(filters)
    assert [row["trade_id"] for row in one.records] == ["T1"]
    assert one.fingerprint == two.fingerprint
    assert source.datasets == before


def test_shadow_runtime_is_authority_and_shadow_trades_is_never_read():
    data = datasets()
    data["shadow_runtime"] = shadow_events()
    source = Source(data)
    result = InvestigationViews(source).build_shadow_comparison(
        InvestigationFilters(treatment_id="TR1")
    )
    assert len(result.records) == 1
    assert result.records[0]["shadow_r_multiple"] == 2.0
    assert result.records[0]["incumbent_r_multiple"] == 1.5
    assert result.records[0]["comparison_semantics"].startswith("SIMULATED_SHADOW")
    assert "shadow_trades" not in [call[0] for call in source.calls]
    assert result.accounting["balanced"] is True


def test_risk_sequence_is_ordered_and_accounts_population():
    data = datasets()
    data["trade_truth"].append({
        **truth("T2", "C2", "EURUSD", "O2", -1.0),
        "timestamps": {"entry_timestamp_broker": "2026-01-01T01:00:00Z",
                       "exit_timestamp_broker": "2026-01-01T02:00:00Z", "duration_seconds": 3600},
    })
    result = InvestigationViews(Source(data)).build_risk_sequence()
    assert [row["trade_id"] for row in result.records] == ["T1", "T2"]
    assert result.records[-1]["cumulative_r"] == 0.5
    assert result.records[-1]["drawdown_r"] == -1.0
    assert result.records[-1]["losing_streak"] == 1
    assert result.accounting["balanced"] is True
