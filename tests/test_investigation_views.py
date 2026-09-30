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
        "entity_id": entity, "result_ok": True, "deal_ticket": "DEAL1",
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
    assert "execution_results:ambiguous_correlation_id" in row["_provenance"]["join_issues"]


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
