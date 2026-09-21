"""Purpose-specific historical sizing governance; no production writes/network."""
import copy
import json
from pathlib import Path

import pytest

from research_engine.data_quality.execution_sizing import (
    EvidencePurpose as P, ExecutionSizingQuality as Q,
    annotate_record, build_fill_index, build_trade_eligibility,
    classify_distortion, eligible_for_fields, governed_record,
    is_eligible, reconciliation_counts,
)
from research_engine.v10.base import compute_metrics
from research_engine.v10.universes.execution_universe import ExecutionUniverseBuilder


def fill(distortion=1, corr="COR-test", deal=1):
    return dict(correlation_id=corr, deal=deal, account_id="test",
                result_ok=True, fill_price=100 + 10 * distortion,
                entry_reference=110, sl=100)


@pytest.mark.parametrize("distortion,quality,monetary", [
    (1, Q.CLEAN, True), (1.4, Q.SUSPECT, False), (2, Q.AFFECTED, False),
    (None, Q.UNKNOWN, False),
])
def test_purpose_matrix(distortion, quality, monetary):
    rows = [fill(distortion)] if distortion is not None else []
    overlay = build_trade_eligibility(rows)
    assert is_eligible("COR-test", P.PRICE_R, overlay)
    assert is_eligible("COR-test", P.MONETARY_RISK, overlay) is monetary
    assert is_eligible("COR-test", P.VOLUME, overlay) is monetary
    record = annotate_record({"correlation_id": "COR-test", "r_multiple": -1}, overlay)
    assert record["sizing_quality"]["execution_sizing_quality"] == quality
    assert is_eligible(record, P.MONETARY_RISK) is monetary


@pytest.mark.parametrize("value,quality", [
    (.80, Q.CLEAN), (1.25, Q.CLEAN), (.667, Q.SUSPECT), (1.50, Q.SUSPECT),
    (.6669, Q.AFFECTED), (1.5001, Q.AFFECTED), (.7999, Q.SUSPECT),
    (1.2501, Q.SUSPECT), (None, Q.UNKNOWN), (float("nan"), Q.UNKNOWN),
    (float("inf"), Q.UNKNOWN), (True, Q.UNKNOWN),
])
def test_frozen_bands_and_unknown_geometry(value, quality):
    assert classify_distortion(value) == quality


def test_unknown_flags_cannot_claim_clean_and_unknown_purpose_rejected():
    row = {"monetary_risk_eligible": True, "volume_analysis_eligible": True}
    assert not is_eligible(row, P.MONETARY_RISK)
    assert not is_eligible(row, P.VOLUME)
    assert is_eligible(row, P.PRICE_R)
    assert not is_eligible(row, "typo", is_shadow=True)


@pytest.mark.parametrize("shadow", [
    {"is_shadow": True}, {"shadow_type": "PRIMARY_HORIZON_SIMULATION"},
    {"identity": {"shadow_trade_id": "nshadow_test"}},
    {"provenance": {"population": "SHADOW"}},
])
def test_shadow_independent_of_live_contamination(shadow):
    row = dict(shadow, correlation_id="COR-test", final_pnl=12, volume=.1)
    overlay = build_trade_eligibility([fill(2)])
    assert all(is_eligible(row, purpose, overlay) for purpose in P)
    assert governed_record(row) == row


def test_deterministic_duplicates_and_raw_immutability():
    rows = [fill(1), fill(1.1), fill(2, deal=2), fill(1, deal=3)]
    original = copy.deepcopy(rows)
    forward = build_trade_eligibility(rows)
    assert forward == build_trade_eligibility(list(reversed(rows)))
    assert build_fill_index(rows) == build_fill_index(rows * 2)
    assert not is_eligible("COR-test", P.VOLUME, forward)
    assert is_eligible("COR-test", P.PRICE_R, forward)
    assert rows == original
    annotated = annotate_record(rows[0], forward)
    assert "sizing_quality" not in rows[0]
    assert annotated is not rows[0]


def test_historical_57_fill_reconciliation():
    manifest = json.loads((Path(__file__).parent / "fixtures/historical_market_sizing.json").read_text())
    rows = [dict(correlation_id=a["correlation_id"], deal=a["deal_id"],
                 account_id=a["account_id"], fill_price=a["fill_price"],
                 entry_reference=a["reference_entry"], sl=a["submitted_sl"], result_ok=True)
            for a in manifest["fills"]]
    assert reconciliation_counts(rows) == dict(CLEAN=6, SUSPECT=8, AFFECTED=43, UNKNOWN=0, total=57)
    assert [a.to_dict() for a in build_fill_index(rows).values()] == manifest["fills"]
    assert build_trade_eligibility(rows) == build_trade_eligibility(rows[::-1])


@pytest.mark.parametrize("distortion,allowed", [(1, True), (1.4, False), (2, False), (None, False)])
def test_execution_consumer_preserves_r_and_withholds_money(monkeypatch, distortion, allowed):
    truth = {"identity": {"trade_id": "pos_test", "correlation_id": "COR-test"},
             "execution": {"volume_executed": .2},
             "outcome": {"r_multiple_realised": -1.25, "net_profit": -30, "pnl_realised": -29}}
    original = copy.deepcopy(truth)
    builder = ExecutionUniverseBuilder()
    executions = [fill(distortion)] if distortion is not None else []
    monkeypatch.setattr(builder, "_load_dataset", lambda name, **kw: [truth] if name == "trade_truth" else executions)
    record, = builder.build()
    assert record["r_multiple"] == -1.25
    assert record["price_r_eligible"] is True
    assert record["net_realised_pnl"] == (-30 if allowed else None)
    assert record["volume"] == (.2 if allowed else None)
    assert eligible_for_fields(record, ["r_multiple"])
    assert eligible_for_fields(record, ["volume"]) is allowed
    assert eligible_for_fields(record, ["net_realised_pnl"]) is allowed
    assert truth == original


def test_metrics_keep_all_r_but_only_clean_monetary_samples():
    trades = [dict(realised_r=r, final_pnl=pnl, execution_sizing_quality=q)
              for r, pnl, q in [(1, 10, "CLEAN"), (-1, -900, "AFFECTED"),
                                (2, 500, "SUSPECT"), (0, 500, "UNKNOWN")]]
    metrics = compute_metrics(trades)
    assert metrics["count"] == 4
    assert metrics["average_r"] == .5
    assert metrics["total_pnl"] == 10
    assert metrics["average_pnl"] == 10
    assert metrics["monetary_sample_size"] == 1
    assert metrics["monetary_excluded"] == 3
    unknown = compute_metrics([dict(realised_r=2, final_pnl=1000)])
    assert unknown["average_r"] == 2
    assert unknown["total_pnl"] is None
    assert unknown["profit_factor"] is None


def test_unfinished_execution_never_creates_truth(monkeypatch):
    builder = ExecutionUniverseBuilder()
    monkeypatch.setattr(builder, "_load_dataset", lambda name, **kw: [fill(2)] if name == "execution_results" else [])
    assert builder.build() == []
    assert len(build_fill_index([fill(2)])) == 1


@pytest.mark.parametrize("quality", ["CLEAN", "SUSPECT", "AFFECTED", "UNKNOWN"])
def test_enrichment_gates_monetary_but_keeps_outcomes(quality):
    from types import SimpleNamespace
    from research_engine.v10.universes.outcome_enrichment import OutcomeEnrichment
    source = dict(entity_id="entity", trade_id="pos_1", r_multiple=2.5,
                  net_realised_pnl=250, execution_sizing_quality=quality)
    enrichment = OutcomeEnrichment(SimpleNamespace(records=[source]))
    outcome = enrichment._outcome_lookup["entity"]
    assert outcome["r_multiple"] == 2.5
    assert outcome["net_realised_pnl"] == (250 if quality == "CLEAN" else None)
    assert source["net_realised_pnl"] == 250


@pytest.mark.parametrize("distortion", [1, 1.4, 2, None])
def test_shadow_live_match_retains_r_pair(distortion):
    from test_q16_shadow_live_matching import _shadow, _truth
    from research_engine.correlation.linker import match_shadow_to_live
    truth = _truth(correlation_id="COR-test")
    executions = [fill(distortion)] if distortion is not None else []
    records, diagnostics = match_shadow_to_live([_shadow()], [truth], executions)
    record, = records
    assert record.live_r == 1.2
    assert record.shadow_r == 1.5
    assert record.has_live and record.has_shadow
    assert record.price_r_eligible
    assert record.live_pnl == (100 if distortion == 1 else None)


@pytest.mark.parametrize("distortion,allowed", [(1, True), (1.4, False), (2, False), (None, False)])
def test_risk_consumer_gates_volume_and_account_risk(monkeypatch, distortion, allowed):
    from research_engine.v10.universes.risk_universe import RiskUniverseBuilder
    raw = dict(entity_id="entity", correlation_id="COR-test",
               v10_risk=dict(approved=True, risk_percentage=1, position_size=.2))
    builder = RiskUniverseBuilder()
    executions = [fill(distortion)] if distortion is not None else []
    monkeypatch.setattr(builder, "_load_dataset", lambda name, **kw: [raw] if name == "decision_trace" else executions)
    row, = builder.build()
    assert row["risk_control_result"] == "APPROVED"
    assert row["risk_percentage"] == (1 if allowed else None)
    assert row["position_size"] == (.2 if allowed else None)
    assert raw["v10_risk"]["position_size"] == .2


@pytest.mark.parametrize("module", ["e1_expectancy", "e2_pattern", "m1_regime", "d1_scoring",
                                    "d3_threshold_effectiveness", "r1_risk_model"])
def test_r_only_legacy_reports_operate_without_monetary_metadata(module, monkeypatch):
    import importlib
    rows = [dict(realised_r=r, final_pnl=100*r, symbol="EURUSD", direction="BUY",
                 entry_price=110, stop_loss=100, take_profit=130, duration_seconds=100,
                 score=70, dt_score_strategy=70, pattern="HAMMER", dt_pattern="HAMMER",
                 dt_v10_regime="TRENDING", regime="TRENDING", exit_reason_validated="STOP_LOSS")
            for r in [-1, 1, 2, -1] * 8]
    experiment = importlib.import_module("research_engine.v10." + module)
    if hasattr(experiment, "enrich_with_decision_trace"):
        monkeypatch.setattr(experiment, "enrich_with_decision_trace", lambda rows: 0)
    result = experiment.run(trades=rows)
    assert result
    assert "N/A" in result["markdown"]


def test_replay_and_snapshot_metrics_keep_sizing_provenance(monkeypatch):
    from research_engine.v10.validation_lab.replay_engine import ReplayEngine
    from research_engine.v10.baselines.snapshot_builder import SnapshotBuilder
    events = [{"execution": {"r_multiple": r, "net_realised_pnl": pnl,
                             "execution_sizing_quality": quality}}
              for r, pnl, quality in [(1, 10, "CLEAN"), (2, 1000, "AFFECTED")]]
    replay = ReplayEngine()._compute(events)
    assert replay["count"] == 2
    assert replay["average_r"] == 1.5
    assert replay["total_pnl"] == 10
    builder = SnapshotBuilder()
    monkeypatch.setattr(builder, "_load_universe", lambda: events)
    snapshot = builder._collect_performance()
    assert snapshot["trade_count"] == 2
    assert snapshot["net_realised_pnl"] == 10


@pytest.mark.parametrize("kind", ["risk", "decision"])
def test_unavailable_sizing_source_preserves_nonmonetary_population(monkeypatch, kind):
    from research_engine.data_access.s3_source import ResearchDataSourceError
    from research_engine.v10.universes.risk_universe import RiskUniverseBuilder
    from research_engine.v10.universes.decision_universe import DecisionUniverseBuilder
    builder = (RiskUniverseBuilder if kind == "risk" else DecisionUniverseBuilder)()
    raw = dict(entity_id="entity", correlation_id="COR-test", action="EXECUTE",
               v10_risk=dict(approved=True, risk_percentage=1, position_size=.2))

    def load(name, **kwargs):
        if name == "execution_results":
            raise ResearchDataSourceError("Sizing source unavailable")
        return [raw]

    monkeypatch.setattr(builder, "_load_dataset", load)
    row, = builder.build()
    assert row["entity_id"] == "entity"
    assert row["sizing_quality"]["execution_sizing_quality"] == "UNKNOWN"
    assert row["risk_percentage"] is None
    assert row["position_size"] is None
    assert is_eligible(row, P.PRICE_R)
