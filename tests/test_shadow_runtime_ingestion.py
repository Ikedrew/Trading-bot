"""Shadow Runtime Ingestion tests — canonical shadow_runtime_v1 → research shape.

Covers the contract enforced by
research_engine/data_access/shadow_runtime_ingestion.py:

    1. shadow_runtime_v1 S3 events are the authoritative production shadow source.
    2. Completed lifecycles reconstruct from PLAN/OPEN/PROGRESS/CLOSE.
    3. Canonical ``nshadow_*`` IDs are accepted verbatim.
    4. Canonical runtime fields map into the existing internal research shape
       (identity / decision_snapshot / simulated_outcome) preserving:
       shadow_trade_id, plan_id, observation_id, canonical_opportunity_id,
       symbol, horizon, direction, entry/stop/target, close timestamp,
       pnl_r_multiple, mfe_r, mae_r.
    5. Incomplete lifecycles NEVER become completed shadow outcomes.
    6. No local logs/shadow_trades fallback; S3 failures surface explicitly.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _s3_fake import install_fake_s3, reset_fake_s3

from research_engine.data_access import shadow_runtime_ingestion as ingestion
from research_engine.data_access.s3_source import ResearchDataSourceError


ROOT = "nopp_20260828_0001_EURUSD"
PLAN_ID = "nplan_7_EURUSD_1777700000"
OBSERVATION_ID = "nobs_20260828_0001_EURUSD"
TRADE_ID = "nshadow_7_EURUSD_SCALP"


def _plan_event() -> dict:
    return {
        "schema_version": "shadow_runtime_v1",
        "event_type": "PLAN",
        "canonical_opportunity_id": ROOT,
        "observation_id": OBSERVATION_ID,
        "symbol": "EURUSD",
        "plan_id": PLAN_ID,
        "cycle_id": 7,
        "entity_id": "EURUSD_1777700000",
        "direction": "BUY",
        "constructed_count": 1,
    }


def _open_event() -> dict:
    return {
        "schema_version": "shadow_runtime_v1",
        "event_type": "OPEN",
        "canonical_opportunity_id": ROOT,
        "observation_id": OBSERVATION_ID,
        "shadow_trade_id": TRADE_ID,
        "symbol": "EURUSD",
        "plan_id": PLAN_ID,
        "entry_price_basis": "ASK",
        "identity": {
            "entity_id": "EURUSD_1777700000",
            "cycle_id": 7,
            "trade_horizon": "SCALP",
            "evaluated_horizon": "SCALP",
            "shadow_type": "HORIZON_ALTERNATIVE",
        },
        "live_facts": {
            "v10_action": "NO_TRADE",
            "v10_rejection_stage": "",
            "v10_selected_horizon": "INTRADAY",
            "horizon_selection_status": "ALTERNATIVE",
            "pattern": "HAMMER",
            "strategy": "REVERSAL",
            "score": 0.72,
            "regime": "TRENDING",
            "h4_regime": "TRENDING",
            "h1_bias": "BULLISH",
            "market_phase": "IMPULSE",
            "market_phase_confidence": 0.8,
        },
        "construction": {
            "direction": "BUY",
            "entry_price": 1.08000,
            "stop_loss": 1.07900,
            "take_profit": 1.08125,
            "risk_distance": 0.001,
            "risk_pips": 10.0,
            "intended_rr": 1.25,
        },
        "market_entry_facts": {
            "bid_at_entry": 1.07998,
            "ask_at_entry": 1.08000,
            "spread_at_entry": 0.00002,
            "entry_price": 1.08000,
            "entry_price_basis": "ASK",
        },
        "entry_market_time": 1777700000,
        "entry_market_time_utc_epoch_s": 1777700000,
        "entry_market_time_utc_iso8601": "2026-04-01T00:00:00Z",
    }


def _progress_event() -> dict:
    return {
        "schema_version": "shadow_runtime_v1",
        "event_type": "PROGRESS",
        "canonical_opportunity_id": ROOT,
        "observation_id": OBSERVATION_ID,
        "shadow_trade_id": TRADE_ID,
        "symbol": "EURUSD",
        "lifecycle": {
            "bars_elapsed": 2,
            "max_favourable_price": 1.08110,
            "max_adverse_price": 1.07970,
        },
    }


def _close_event() -> dict:
    return {
        "schema_version": "shadow_runtime_v1",
        "event_type": "CLOSE",
        "canonical_opportunity_id": ROOT,
        "observation_id": OBSERVATION_ID,
        "shadow_trade_id": TRADE_ID,
        "symbol": "EURUSD",
        "exit_market_time": 1777701500,
        "exit_market_time_utc_epoch_s": 1777701500,
        "exit_market_time_utc_iso8601": "2026-04-01T00:25:00Z",
        "exit_price": 1.08125,
        "exit_reason": "take_profit",
        "exit_bar_index": 5,
        "bars_held": 5,
        "outcome": {
            "pnl_r_multiple": 1.25,
            "mfe_r": 1.4,
            "mae_r": -0.3,
            "risk_distance": 0.001,
            "intended_rr": 1.25,
        },
    }


def _full_lifecycle() -> list[dict]:
    return [_plan_event(), _open_event(), _progress_event(), _close_event()]


# ═══════════════════════════════════════════════════════════════════════════════
# RECONSTRUCTION (pure, in-process)
# ═══════════════════════════════════════════════════════════════════════════════


class TestReconstruction:
    def test_completed_lifecycle_reconstructs_one_record(self):
        records = ingestion.reconstruct_completed_shadow_trades(_full_lifecycle())
        assert len(records) == 1
        rec = records[0]

        # Internal research shape
        assert rec["schema_version"] == "shadow_trades_v1"
        assert set(("identity", "decision_snapshot", "simulated_outcome")) <= set(rec)

        identity = rec["identity"]
        snap = rec["decision_snapshot"]
        outcome = rec["simulated_outcome"]

        # Required preserved fields
        assert identity["shadow_trade_id"] == TRADE_ID
        assert identity["trade_id"] == TRADE_ID  # consumer-facing alias
        assert identity["plan_id"] == PLAN_ID
        assert identity["observation_id"] == OBSERVATION_ID
        assert identity["canonical_opportunity_id"] == ROOT
        assert identity["symbol"] == "EURUSD"
        assert identity["evaluated_horizon"] == "SCALP"  # horizon
        assert snap["direction"] == "BUY"  # direction
        assert snap["entry_intent_price"] == pytest.approx(1.08000)  # entry
        assert snap["stop_loss_intent"] == pytest.approx(1.07900)  # stop
        assert snap["take_profit_intent"] == pytest.approx(1.08125)  # target
        assert outcome["exit_timestamp"] == 1777701500  # close timestamp
        assert outcome["pnl_r_multiple"] == pytest.approx(1.25)
        assert outcome["mfe_r"] == pytest.approx(1.4)
        assert outcome["mae_r"] == pytest.approx(-0.3)

    def test_canonical_nshadow_id_accepted_verbatim(self):
        records = ingestion.reconstruct_completed_shadow_trades(_full_lifecycle())
        assert records[0]["identity"]["shadow_trade_id"].startswith("nshadow_")

    def test_observation_id_backfilled_from_paired_plan(self):
        """Older-era OPENs carry no observation_id; the paired PLAN of the SAME
        lifecycle (matched by plan_id) is canonical same-lifecycle evidence."""
        plan = _plan_event()
        open_ev = _open_event()
        del open_ev["observation_id"]
        records = ingestion.reconstruct_completed_shadow_trades(
            [plan, open_ev, _close_event()]
        )
        assert len(records) == 1
        assert records[0]["identity"]["observation_id"] == OBSERVATION_ID

    def test_observation_id_never_cross_joined_across_lifecycles(self):
        """A PLAN from a DIFFERENT lifecycle must not supply observation_id."""
        other_plan = _plan_event()
        other_plan["plan_id"] = "nplan_9_EURUSD_1777700000"
        other_plan["observation_id"] = "nobs_OTHER_LIFECYCLE"
        open_ev = _open_event()
        del open_ev["observation_id"]
        close_ev = _close_event()
        del close_ev["observation_id"]
        records = ingestion.reconstruct_completed_shadow_trades(
            [other_plan, open_ev, close_ev]
        )
        assert len(records) == 1
        assert records[0]["identity"]["observation_id"] == ""

    def test_exit_reason_vocabulary_normalised(self):
        events = _full_lifecycle()
        events[-1]["exit_reason"] = "timeout"
        records = ingestion.reconstruct_completed_shadow_trades(events)
        assert records[0]["simulated_outcome"]["exit_reason"] == "max_bars_timeout"

    def test_open_without_close_never_becomes_outcome(self):
        records = ingestion.reconstruct_completed_shadow_trades(
            [_plan_event(), _open_event(), _progress_event()]
        )
        assert records == []

    def test_close_without_open_never_becomes_outcome(self):
        records = ingestion.reconstruct_completed_shadow_trades([_close_event()])
        assert records == []

    def test_close_without_outcome_r_never_becomes_outcome(self):
        close = _close_event()
        del close["outcome"]["pnl_r_multiple"]
        records = ingestion.reconstruct_completed_shadow_trades(
            [_open_event(), close]
        )
        assert records == []

    def test_duplicate_events_tolerated(self):
        records = ingestion.reconstruct_completed_shadow_trades(
            _full_lifecycle() + [_open_event(), _close_event()]
        )
        assert len(records) == 1

    def test_non_canonical_ids_excluded(self):
        open_ev = _open_event()
        open_ev["shadow_trade_id"] = "shadow_32547_EURUSD"
        close_ev = _close_event()
        close_ev["shadow_trade_id"] = "shadow_32547_EURUSD"
        records = ingestion.reconstruct_completed_shadow_trades([open_ev, close_ev])
        assert records == []

    def test_wrong_schema_version_excluded(self):
        events = _full_lifecycle()
        for ev in events:
            ev["schema_version"] = "shadow_trades_v2"
        assert ingestion.reconstruct_completed_shadow_trades(events) == []

    def test_deterministic_ordering(self):
        second = _full_lifecycle()
        for ev in second:
            if ev.get("shadow_trade_id"):
                ev["shadow_trade_id"] = "nshadow_7_EURUSD_INTRADAY"
        records = ingestion.reconstruct_completed_shadow_trades(
            _full_lifecycle() + second
        )
        ids = [r["identity"]["shadow_trade_id"] for r in records]
        assert ids == sorted(ids)


# ═══════════════════════════════════════════════════════════════════════════════
# S3 INGESTION (fake S3 — canonical dataset, no local fallback)
# ═══════════════════════════════════════════════════════════════════════════════


class TestS3Ingestion:
    def test_ingests_from_canonical_shadow_runtime_dataset(self):
        fake = install_fake_s3()
        try:
            fake.add("shadow_runtime", _full_lifecycle(), symbol="EURUSD")
            records = ingestion.ingest_completed_shadow_trades()
            assert len(records) == 1
            assert records[0]["identity"]["shadow_trade_id"] == TRADE_ID
        finally:
            reset_fake_s3()

    def test_empty_canonical_source_returns_empty(self, caplog):
        install_fake_s3()
        try:
            with caplog.at_level("WARNING", logger=ingestion.logger.name):
                records = ingestion.ingest_completed_shadow_trades()
            assert records == []
            # Explicit collection-gap accounting, never silent success.
            assert any("collection gap" in r.getMessage().lower() for r in caplog.records)
        finally:
            reset_fake_s3()

    def test_s3_failure_surfaces_not_silently_empty(self):
        class _ExplodingClient:
            def list_objects_v2(self, **kw):
                raise RuntimeError("S3 unreachable")

        from research_engine.data_access.s3_source import (
            S3ResearchDataSource,
            set_default_source,
        )

        set_default_source(
            S3ResearchDataSource(bucket="test-bucket", client=_ExplodingClient())
        )
        try:
            with pytest.raises(ResearchDataSourceError):
                ingestion.ingest_completed_shadow_trades()
        finally:
            reset_fake_s3()


# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════


def test_canonical_source_constants():
    assert ingestion._RUNTIME_DATASET == "shadow_runtime"
    assert ingestion._RUNTIME_SCHEMA_VERSION == "shadow_runtime_v1"
    assert ingestion._RESEARCH_SCHEMA_VERSION == "shadow_trades_v1"
    assert ingestion._VALID_TRADE_ID_PREFIX == "nshadow_"
