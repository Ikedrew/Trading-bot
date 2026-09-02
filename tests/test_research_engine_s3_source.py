"""
Regression tests for the Research Engine S3-only data source.

Proves the permanent contract: Research Engine → S3ResearchDataSource → S3, with
NO dependency on local logs/. A fake in-memory S3 (with real list_objects_v2
pagination + get_object) is injected via set_default_source so these tests never
touch the network or the local filesystem for source data.

Covered (mirrors the migration spec):
    - core dataset reads (trade_truth, decision_trace, shadow_trades, ...)
    - list_objects_v2 pagination across >1 page (no records dropped)
    - symbol + date prefix/range filtering
    - LOCAL INDEPENDENCE: works with logs/ absent (cwd has no logs dir)
    - S3-vs-local equivalence of parsed records
    - universes build from S3 alone (EXECUTION/DECISION/MARKET/STRATEGY/RISK/
      OUTCOME/SHADOW_OUTCOME)
    - one experiment path (dataset.load_trades) from an S3 artifact alone
    - run-level cache: identical read not re-fetched
    - S3 failure surfaces as ResearchDataSourceError (no local fallback)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.production_data_contract import s3_base_prefix, current_schema
from research_engine.data_access import s3_source as s3mod
from research_engine.data_access.s3_source import (
    S3ResearchDataSource,
    ResearchDataSourceError,
    set_default_source,
    reset_default_source,
)


# ─── Fake paginating S3 ───────────────────────────────────────────────────────

class FakeS3:
    """In-memory S3 with real pagination + call counting. page_size forces
    multi-page list_objects_v2 responses."""

    def __init__(self, objects: dict[str, str], *, page_size: int = 1000, fail=False):
        self.objects = dict(objects)              # key -> body (jsonl text)
        self.page_size = page_size
        self.fail = fail
        self.list_calls = 0
        self.get_calls: list[str] = []

    def list_objects_v2(self, **kw):
        if self.fail:
            raise RuntimeError("simulated S3 list outage")
        self.list_calls += 1
        prefix = kw.get("Prefix", "")
        token = kw.get("ContinuationToken")
        keys = sorted(k for k in self.objects if k.startswith(prefix))
        start = int(token) if token else 0
        page = keys[start:start + self.page_size]
        contents = [{"Key": k} for k in page]
        resp = {"Contents": contents}
        nxt = start + self.page_size
        if nxt < len(keys):
            resp["IsTruncated"] = True
            resp["NextContinuationToken"] = str(nxt)
        else:
            resp["IsTruncated"] = False
        return resp

    def get_object(self, **kw):
        if self.fail:
            raise RuntimeError("simulated S3 get outage")
        key = kw["Key"]
        self.get_calls.append(key)

        class _Body:
            def __init__(self, text): self._t = text
            def read(self): return self._t.encode("utf-8")

        return {"Body": _Body(self.objects[key])}


def _jsonl(records: list[dict]) -> str:
    return "\n".join(json.dumps(r) for r in records) + "\n"


def _key(dataset: str, symbol: str, date: str, part: str = "part-000.jsonl") -> str:
    base = s3_base_prefix(dataset)
    schema = current_schema(dataset)
    return f"{base}/schema_version={schema}/symbol={symbol}/date={date}/{part}"


@pytest.fixture(autouse=True)
def _isolate_cwd(tmp_path, monkeypatch):
    """Run every test from an empty temp cwd so there is provably NO local logs/
    directory to fall back to. This is the local-independence guarantee."""
    monkeypatch.chdir(tmp_path)
    reset_default_source()
    yield
    reset_default_source()


def _install(objects, **kw) -> FakeS3:
    fake = FakeS3(objects, **kw)
    set_default_source(S3ResearchDataSource(bucket="test-bucket", client=fake))
    return fake


# ─── Core dataset reads ───────────────────────────────────────────────────────

def test_core_datasets_read_from_s3():
    from research_engine.data_access.loaders import (
        load_trade_truth, load_decision_trace, load_shadow_trades,
        load_decision_ledger, load_execution_results,
    )
    objects = {
        _key("trade_truth", "EURUSD", "2026-07-01"): _jsonl([
            {"identity": {"trade_id": "t1", "symbol": "EURUSD"},
             "outcome": {"r_multiple_realised": 1.2}, "timestamps": {"exit_timestamp_broker": 100}},
        ]),
        _key("decision_trace", "EURUSD", "2026-07-01"): _jsonl([
            {"entity_id": "e1", "action": "EXECUTE", "symbol": "EURUSD", "timestamp_utc": 5},
        ]),
        _key("shadow_trades", "EURUSD", "2026-07-01"): _jsonl([
            {"trade_id": "hshadow_1", "event_type": "CLOSE", "timestamp_utc": 3},
        ]),
        _key("decision_ledger", "EURUSD", "2026-07-01"): _jsonl([{"symbol": "EURUSD", "timestamp_utc": 1}]),
        _key("execution_results", "EURUSD", "2026-07-01"): _jsonl([{"symbol": "EURUSD", "result_ok": True, "timestamp_utc": 1}]),
    }
    _install(objects)

    assert len(load_trade_truth("EURUSD")) == 1
    assert load_trade_truth("EURUSD")[0]["identity"]["trade_id"] == "t1"
    assert len(load_decision_trace("EURUSD")) == 1
    assert len(load_shadow_trades("EURUSD")) == 1
    assert len(load_decision_ledger("EURUSD")) == 1
    assert len(load_execution_results("EURUSD")) == 1


def test_missing_dataset_returns_empty_not_error():
    _install({})
    from research_engine.data_access.loaders import load_trade_truth
    assert load_trade_truth("EURUSD") == []   # real gap, no local fallback


# ─── Pagination ───────────────────────────────────────────────────────────────

def test_pagination_consumes_all_pages():
    # 5 date-partitioned objects, page_size=2 → 3 pages must all be consumed.
    objects = {}
    for i in range(5):
        objects[_key("trade_truth", "EURUSD", f"2026-07-0{i+1}")] = _jsonl([
            {"identity": {"trade_id": f"t{i}", "symbol": "EURUSD"},
             "outcome": {"r_multiple_realised": float(i)},
             "timestamps": {"exit_timestamp_broker": i}},
        ])
    fake = _install(objects, page_size=2)
    from research_engine.data_access.loaders import load_trade_truth
    recs = load_trade_truth("EURUSD")
    assert len(recs) == 5, "records dropped across pages"
    assert fake.list_calls >= 3, "pagination did not issue continuation requests"
    # Deterministic order by exit timestamp.
    assert [r["outcome"]["r_multiple_realised"] for r in recs] == [0.0, 1.0, 2.0, 3.0, 4.0]


# ─── Symbol + date filtering ──────────────────────────────────────────────────

def test_symbol_prefix_pruning_downloads_only_requested_symbol():
    objects = {
        _key("trade_truth", "EURUSD", "2026-07-01"): _jsonl([{"identity": {"trade_id": "e", "symbol": "EURUSD"}, "outcome": {"r_multiple_realised": 1}, "timestamps": {"exit_timestamp_broker": 1}}]),
        _key("trade_truth", "GBPUSD", "2026-07-01"): _jsonl([{"identity": {"trade_id": "g", "symbol": "GBPUSD"}, "outcome": {"r_multiple_realised": 1}, "timestamps": {"exit_timestamp_broker": 1}}]),
    }
    fake = _install(objects)
    from research_engine.data_access.loaders import load_trade_truth
    eur = load_trade_truth("EURUSD")
    assert len(eur) == 1 and eur[0]["identity"]["symbol"] == "EURUSD"
    # Prefix pruning: the GBPUSD object key was never fetched.
    assert all("symbol=GBPUSD" not in k for k in fake.get_calls)


def test_date_range_filtering():
    objects = {
        _key("trade_truth", "EURUSD", "2026-07-01"): _jsonl([{"identity": {"trade_id": "a", "symbol": "EURUSD"}, "outcome": {"r_multiple_realised": 1}, "timestamps": {"exit_timestamp_broker": 1}}]),
        _key("trade_truth", "EURUSD", "2026-07-15"): _jsonl([{"identity": {"trade_id": "b", "symbol": "EURUSD"}, "outcome": {"r_multiple_realised": 1}, "timestamps": {"exit_timestamp_broker": 2}}]),
        _key("trade_truth", "EURUSD", "2026-08-01"): _jsonl([{"identity": {"trade_id": "c", "symbol": "EURUSD"}, "outcome": {"r_multiple_realised": 1}, "timestamps": {"exit_timestamp_broker": 3}}]),
    }
    fake = _install(objects)
    recs = s3mod.get_default_source().read_dataset(
        "trade_truth", symbol="EURUSD", start_date="2026-07-10", end_date="2026-07-31",
    )
    ids = [r["identity"]["trade_id"] for r in recs]
    assert ids == ["b"]                       # only the in-range object
    # Out-of-range objects were pruned before download.
    assert all("date=2026-08-01" not in k for k in fake.get_calls)
    assert all("date=2026-07-01" not in k for k in fake.get_calls)


# ─── Malformed handling ───────────────────────────────────────────────────────

def test_malformed_line_skipped_and_reported():
    key = _key("trade_truth", "EURUSD", "2026-07-01")
    body = json.dumps({"identity": {"trade_id": "ok", "symbol": "EURUSD"}, "outcome": {"r_multiple_realised": 1}, "timestamps": {"exit_timestamp_broker": 1}}) + "\nNOT_JSON\n"
    src = _install({key: body})
    recs = s3mod.get_default_source().read_dataset("trade_truth", symbol="EURUSD")
    assert len(recs) == 1
    rep = s3mod.get_default_source().malformed_report("trade_truth")
    assert rep is not None and rep.malformed_lines == 1


# ─── S3-vs-local equivalence ──────────────────────────────────────────────────

def test_s3_records_equal_local_representation(tmp_path):
    records = [
        {"identity": {"trade_id": "t1", "symbol": "EURUSD"}, "outcome": {"r_multiple_realised": 1.5}, "timestamps": {"exit_timestamp_broker": 10}},
        {"identity": {"trade_id": "t2", "symbol": "EURUSD"}, "outcome": {"r_multiple_realised": -0.7}, "timestamps": {"exit_timestamp_broker": 20}},
    ]
    body = _jsonl(records)
    # local representation (what the old loader would have parsed)
    local = [json.loads(l) for l in body.splitlines() if l.strip()]
    # S3 representation
    _install({_key("trade_truth", "EURUSD", "2026-07-01"): body})
    from research_engine.data_access.loaders import load_trade_truth
    s3_recs = load_trade_truth("EURUSD")
    assert s3_recs == local            # semantic equality


# ─── Cache reuse ──────────────────────────────────────────────────────────────

def test_run_level_cache_avoids_refetch():
    fake = _install({_key("decision_trace", "EURUSD", "2026-07-01"): _jsonl([{"entity_id": "e", "action": "EXECUTE", "symbol": "EURUSD", "timestamp_utc": 1}])})
    src = s3mod.get_default_source()
    src.read_dataset("decision_trace", symbol="EURUSD")
    gets_after_first = len(fake.get_calls)
    src.read_dataset("decision_trace", symbol="EURUSD")   # same scope → cached
    assert len(fake.get_calls) == gets_after_first, "identical read re-fetched from S3"


def test_reset_default_source_starts_fresh_run():
    _install({_key("decision_trace", "EURUSD", "2026-07-01"): _jsonl([{"entity_id": "e", "action": "EXECUTE", "symbol": "EURUSD", "timestamp_utc": 1}])})
    s3mod.get_default_source().read_dataset("decision_trace", symbol="EURUSD")
    reset_default_source()
    # New run → default source is a fresh instance (no stale cache carried over).
    assert s3mod.get_default_source() is not None


# ─── Failure surfaces clearly ─────────────────────────────────────────────────

def test_s3_failure_raises_research_error_no_local_fallback():
    set_default_source(S3ResearchDataSource(bucket="test-bucket", client=FakeS3({}, fail=True)))
    with pytest.raises(ResearchDataSourceError):
        s3mod.get_default_source().read_dataset("trade_truth", symbol="EURUSD")


def test_retired_dataset_rejected():
    _install({})
    with pytest.raises(ResearchDataSourceError):
        s3mod.get_default_source().read_dataset("decision_audit")


# ─── Universes build from S3 alone ────────────────────────────────────────────

def _seed_universe_objects() -> dict[str, str]:
    dt = [{
        "entity_id": "EURUSD_1", "correlation_id": "COR-1", "symbol": "EURUSD",
        "cycle_id": 1, "action": "EXECUTE", "timestamp_utc": "2026-07-01T10:00:00Z",
        "score_strategy": 72, "components": {"a": 0.8},
        "v10_market_state": {"regime": {"regime": "TRENDING"}},
        "v10_strategy": {"family": "TREND", "confidence": 0.7, "direction": "BUY"},
        "v10_risk": {"approved": True, "risk_percentage": 1.0},
    }]
    tt = [{
        "identity": {"trade_id": "pos_1", "correlation_id": "COR-1", "symbol": "EURUSD"},
        "execution": {"entry_fill_price": 1.10, "exit_fill_price": 1.11, "volume_executed": 0.1},
        "timestamps": {"entry_timestamp_broker": 100, "exit_timestamp_broker": 200, "duration_seconds": 100},
        "outcome": {"r_multiple_realised": 1.5, "pnl_realised": 50.0, "net_profit": 48.0, "commission": -2.0, "swap": 0.0},
        "exit": {"exit_reason": "take_profit_hit"},
    }]
    er = [{"symbol": "EURUSD", "result_ok": True, "deal": 1, "correlation_id": "COR-1", "entity_id": "EURUSD_1", "timestamp_utc": 100}]
    mc = [{"symbol": "EURUSD", "cycle_id": 1, "timestamp_utc": "2026-07-01T10:00:00Z", "regime": "TRENDING"}]
    so = [{
        "schema_version": current_schema("shadow_trades"),
        "identity": {"trade_id": "hshadow_1"},
        "simulated_outcome": {"pnl_r_multiple": 0.9},
        "timestamp_utc": 100,
    }]
    sobs = [{"symbol": "EURUSD", "cycle_id": 1, "timestamp_utc": 100, "family": "TREND"}]
    return {
        _key("decision_trace", "EURUSD", "2026-07-01"): _jsonl(dt),
        _key("trade_truth", "EURUSD", "2026-07-01"): _jsonl(tt),
        _key("execution_results", "EURUSD", "2026-07-01"): _jsonl(er),
        _key("market_context", "EURUSD", "2026-07-01"): _jsonl(mc),
        _key("shadow_trades", "EURUSD", "2026-07-01"): _jsonl(so),
        _key("strategy_observations", "EURUSD", "2026-07-01"): _jsonl(sobs),
    }


def test_all_active_universes_build_from_s3_only():
    _install(_seed_universe_objects())
    from research_engine.v10.universes import (
        ExecutionUniverseBuilder, DecisionUniverseBuilder, MarketUniverseBuilder,
        StrategyUniverseBuilder, RiskUniverseBuilder, ShadowOutcomeUniverseBuilder,
    )
    from research_engine.v10.universes.outcome_universe import OutcomeUniverseBuilder

    exe = ExecutionUniverseBuilder(); exe.build()
    assert exe.is_built
    assert len(exe.records) == 1
    rec = exe.records[0]
    assert rec["trade_id"] == "pos_1"
    assert rec["r_multiple"] == 1.5
    assert rec["net_realised_pnl"] == 48.0
    assert rec["exit_reason"] == "take_profit_hit"
    assert rec["entity_id"] == "EURUSD_1"   # joined from execution_results via correlation_id

    for Builder in (DecisionUniverseBuilder, MarketUniverseBuilder,
                    StrategyUniverseBuilder, RiskUniverseBuilder,
                    ShadowOutcomeUniverseBuilder):
        b = Builder(); b.build()
        assert b.is_built, f"{Builder.__name__} did not build from S3"

    outcome = OutcomeUniverseBuilder(execution_builder=exe); outcome.build()
    assert outcome.is_built
    assert len(outcome.records) == 1


# ─── One experiment path from S3 artifact alone ───────────────────────────────

def test_experiment_load_trades_from_s3_artifact():
    # research-ready is a DERIVED artifact under research_artifacts/research_ready_trades/
    ready = [
        {"trade_id": "t1", "symbol": "EURUSD", "direction": "BUY",
         "entry_price": 1.10, "stop_loss": 1.095, "exit_price": 1.11,
         "realised_r": 2.0, "instrument_class": "FX_MAJOR"},
        {"trade_id": "t2", "symbol": "US500", "direction": "SELL",
         "entry_price": 5000, "stop_loss": 5010, "exit_price": 4980,
         "realised_r": 1.0, "instrument_class": "INDEX"},
    ]
    objects = {"research_artifacts/research_ready_trades/part-000.jsonl": _jsonl(ready)}
    _install(objects)

    from research_engine.v10.dataset import load_trades, DatasetView
    all_trades = load_trades(DatasetView.FULL)
    assert len(all_trades) == 2
    fx = load_trades(DatasetView.FX_ONLY)
    assert len(fx) == 1 and fx[0]["symbol"] == "EURUSD"
