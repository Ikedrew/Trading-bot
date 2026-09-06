"""
No-fallback proof tests — active research production readers are S3-backed ONLY.

Proves for every migrated active production reader:
    1. valid S3 evidence is consumed (existing suites cover this in depth);
    2. stale/absent local ``logs/`` production files are IRRELEVANT — local
       production records never leak into research results;
    3. an S3 failure surfaces loudly (raises ResearchDataSourceError or an
       explicit unavailable state) — never a silent fallback to stale local
       production data.

No test hits production AWS: the default research source is injected with an
in-memory fake or a raising stub.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _s3_fake import FakeS3, install_fake_s3, reset_fake_s3
from research_engine.data_access.s3_source import (
    ResearchDataSourceError,
    S3ResearchDataSource,
    set_default_source,
    reset_default_source,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Stubs
# ═══════════════════════════════════════════════════════════════════════════════


class _RaisingClient:
    """S3 client stub that simulates a total S3 outage."""

    def list_objects_v2(self, **kwargs):  # noqa: ANN003
        raise RuntimeError("SimulatedS3Outage")

    def get_object(self, **kwargs):  # noqa: ANN003
        raise RuntimeError("SimulatedS3Outage")


def _install_raising_source() -> None:
    set_default_source(S3ResearchDataSource(bucket="test-bucket", client=_RaisingClient()))


@pytest.fixture
def stale_local_logs(tmp_path):
    """Seed STALE local production logs and chdir there.

    If any reader still consulted local production evidence, these records
    would leak into results or satisfy the read — the tests below prove they
    do not.
    """
    stale_trade = {
        "trade_id": "pos_STALE_LOCAL",
        "symbol": "EURUSD",
        "direction": "BUY",
        "entry_price": 1.0,
        "exit_price": 2.0,
        "initial_sl": 0.5,
        "timestamp_utc": "2020-01-01T00:00:00Z",
    }
    (tmp_path / "logs" / "trade_journal").mkdir(parents=True)
    (tmp_path / "logs" / "trade_journal" / "2020-01-01.jsonl").write_text(
        json.dumps(stale_trade) + "\n", encoding="utf-8"
    )
    (tmp_path / "logs" / "trade_truth").mkdir(parents=True)
    (tmp_path / "logs" / "trade_truth" / "2020-01-01.jsonl").write_text(
        json.dumps(stale_trade) + "\n", encoding="utf-8"
    )
    (tmp_path / "logs" / "shadow_trades").mkdir(parents=True)
    (tmp_path / "logs" / "shadow_trades" / "2020-01-01.jsonl").write_text(
        json.dumps(stale_trade) + "\n", encoding="utf-8"
    )
    (tmp_path / "logs" / "decision_trace").mkdir(parents=True)
    (tmp_path / "logs" / "decision_trace" / "2020-01-01.jsonl").write_text(
        json.dumps({"trade_id": "pos_STALE_LOCAL", "action": "EXECUTE"}) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "logs" / "execution_results").mkdir(parents=True)
    (tmp_path / "logs" / "execution_results" / "2020-01-01.jsonl").write_text(
        json.dumps({"trade_id": "pos_STALE_LOCAL", "result_ok": True,
                    "correlation_id": "COR-STALE"}) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "logs" / "execution_context").mkdir(parents=True)
    (tmp_path / "logs" / "execution_context" / "2020-01-01.jsonl").write_text(
        json.dumps({"correlation_id": "COR-STALE"}) + "\n", encoding="utf-8"
    )
    (tmp_path / "logs" / "decision_ledger").mkdir(parents=True)
    (tmp_path / "logs" / "decision_ledger" / "2020-01-01.jsonl").write_text(
        json.dumps({"trade_id": "pos_STALE_LOCAL", "decision": "EXECUTE"}) + "\n",
        encoding="utf-8",
    )

    old = os.getcwd()
    os.chdir(tmp_path)
    try:
        yield tmp_path
    finally:
        os.chdir(old)


@pytest.fixture(autouse=True)
def _clean_default_source():
    """Every test starts with no default source; nothing leaks between tests."""
    reset_default_source()
    yield
    reset_default_source()


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Valid S3 → works; stale local production logs are irrelevant
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidS3WinsOverStaleLocal:
    def test_horizon_research_uses_only_s3_records(self, stale_local_logs):
        from research_engine.horizon_research import _load_trade_journal_records

        fake = install_fake_s3()
        s3_record = {
            "trade_id": "pos_S3_FRESH",
            "symbol": "EURUSD",
            "direction": "BUY",
            "entry_price": 1.1,
            "exit_price": 1.2,
            "initial_sl": 1.0,
            "exit_time": 1784809000,
            "trade_horizon": "SCALP",
        }
        fake.add("trade_journal", [s3_record], symbol="EURUSD")

        records = _load_trade_journal_records()
        ids = [r["trade_id"] for r in records]
        assert ids == ["pos_S3_FRESH"]  # stale local record NOT included

    def test_replay_shadow_lookup_uses_only_s3(self, stale_local_logs):
        from core.causal.replay import _load_shadow_trade

        fake = install_fake_s3()
        open_ev = {
            "schema_version": "shadow_runtime_v1",
            "event_type": "OPEN",
            "shadow_trade_id": "nshadow_1_EURUSD_SCALP",
            "plan_id": "nplan_1_EURUSD_1",
            "canonical_opportunity_id": "EURUSD*1*X",
            "symbol": "EURUSD",
            "identity": {"entity_id": "E", "cycle_id": 1,
                         "trade_horizon": "SCALP", "evaluated_horizon": "SCALP"},
            "live_facts": {},
            "construction": {"direction": "BUY", "entry_price": 1.0,
                             "stop_loss": 0.9, "take_profit": 1.2},
            "market_entry_facts": {},
        }
        close_ev = {
            "schema_version": "shadow_runtime_v1",
            "event_type": "CLOSE",
            "shadow_trade_id": "nshadow_1_EURUSD_SCALP",
            "exit_reason": "take_profit",
            "outcome": {"pnl_r_multiple": 2.0, "mfe_r": 2.1, "mae_r": -0.1},
        }
        fake.add("shadow_runtime", [open_ev, close_ev], symbol="EURUSD")

        record = _load_shadow_trade("nshadow_1_EURUSD_SCALP")
        assert record is not None
        assert record["identity"]["trade_id"] == "nshadow_1_EURUSD_SCALP"
        # The stale local shadow_trades record is not the answer
        assert record["identity"].get("trade_id") != "pos_STALE_LOCAL"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. S3 failure → loud error, never a silent local fallback
# ═══════════════════════════════════════════════════════════════════════════════


class TestS3FailureSurfacesLoudly:
    def test_horizon_research_trade_journal(self, stale_local_logs):
        from research_engine.horizon_research import _load_trade_journal_records

        _install_raising_source()
        with pytest.raises(ResearchDataSourceError):
            _load_trade_journal_records()

    def test_data_governance_journal(self, stale_local_logs):
        from research_engine.v10.data_governance import DataGovernanceValidator

        _install_raising_source()
        with pytest.raises(ResearchDataSourceError):
            DataGovernanceValidator()._load_journal_records()

    def test_data_governance_decision_trace(self, stale_local_logs):
        from research_engine.v10.data_governance import DataGovernanceValidator

        _install_raising_source()
        with pytest.raises(ResearchDataSourceError):
            DataGovernanceValidator()._load_execute_decisions()

    def test_decision_enrichment_traces(self, stale_local_logs):
        from research_engine.v10.decision_enrichment import _load_decision_traces

        _install_raising_source()
        with pytest.raises(ResearchDataSourceError):
            _load_decision_traces()

    def test_decision_enrichment_execution_results(self, stale_local_logs):
        from research_engine.v10.decision_enrichment import _load_execution_results

        _install_raising_source()
        with pytest.raises(ResearchDataSourceError):
            _load_execution_results()

    def test_replay_shadow_trade(self, stale_local_logs):
        from core.causal.replay import _load_shadow_trade

        _install_raising_source()
        with pytest.raises(ResearchDataSourceError):
            _load_shadow_trade("nshadow_1_EURUSD_SCALP")

    def test_replay_trade_truth(self, stale_local_logs):
        from core.causal.replay import _load_trade_truth

        _install_raising_source()
        with pytest.raises(ResearchDataSourceError):
            _load_trade_truth("pos_1")

    def test_replay_decision_ledger(self, stale_local_logs):
        from core.causal.replay import _load_decision_ledger_record

        _install_raising_source()
        with pytest.raises(ResearchDataSourceError):
            _load_decision_ledger_record({"entity_id": "E"})

    def test_replay_execution_context(self, stale_local_logs):
        from core.causal.replay import _load_execution_context

        _install_raising_source()
        with pytest.raises(ResearchDataSourceError):
            _load_execution_context("COR-1")

    def test_shadow_reality_universe_load(self, stale_local_logs):
        """Shadow reality build fails loudly — and continues to source shadow
        evidence from the canonical normalized shadow ingestion layer."""
        from research_engine.v10.universes.shadow_reality_universe import (
            ShadowRealityUniverseBuilder,
        )

        _install_raising_source()
        with pytest.raises(ResearchDataSourceError):
            ShadowRealityUniverseBuilder().load()

    def test_universe_base_dataset_loader(self, stale_local_logs):
        from research_engine.v10.universes.base import UniverseBuilder

        _install_raising_source()
        with pytest.raises(ResearchDataSourceError):
            UniverseBuilder._load_dataset(object(), "trade_truth")  # type: ignore[arg-type]


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Explicit unavailable state where the design surfaces failure (cockpit)
# ═══════════════════════════════════════════════════════════════════════════════


class TestExplicitUnavailableState:
    def test_cockpit_readiness_surfaces_s3_failure(self, stale_local_logs):
        """The cockpit folds S3 failures into explicit readiness reasons — it
        must NOT silently satisfy itself from stale local production logs."""
        from research_engine.v10.cockpit.aggregator import CockpitData
        from research_engine.v10.cockpit.aggregator import CockpitDataAggregator

        _install_raising_source()
        data = CockpitData()
        CockpitDataAggregator()._compute_prop_readiness(data)

        assert data.prop_realised_n == 0
        reasons_text = " ".join(data.prop_readiness_reasons)
        # The readiness reasons must carry the S3 failure, not stale local data.
        assert data.prop_realised_expectancy in (None, 0.0)
        assert "S3" in reasons_text or "Failed to compute realised expectancy" in reasons_text
