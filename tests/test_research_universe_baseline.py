"""Research-universe boundaries that must remain stable before enrichment."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _s3_fake import install_fake_s3, reset_fake_s3

from research_engine.v10.universes.contracts import UNIVERSE_CONTRACTS
from research_engine.v10.universes.models import (
    ACTIVE_UNIVERSES,
    RETIRED_UNIVERSES,
    Universe,
)
from research_engine.v10.universes.question_bank import (
    QUESTION_BANK,
    RETIRED_QUESTIONS,
)
from research_engine.v10.universes.shadow_outcome_universe import (
    ShadowOutcomeUniverseBuilder,
)


def test_shadow_outcome_is_active_and_contracted():
    assert Universe.SHADOW_OUTCOME in ACTIVE_UNIVERSES
    assert Universe.SHADOW_OUTCOME in UNIVERSE_CONTRACTS
    contract = UNIVERSE_CONTRACTS[Universe.SHADOW_OUTCOME]
    # Fresh Production V1 baseline: the shadow-outcome contract accepts the V1
    # shadow schema only — no v2/v3 schema compatibility is retained.
    assert contract.source_schema_versions == ("shadow_trades_v1",)
    assert "counterfactual" in contract.description.lower()


def test_shadow_reality_is_explicitly_retired():
    assert RETIRED_UNIVERSES == (Universe.SHADOW_REALITY,)
    assert Universe.SHADOW_REALITY not in ACTIVE_UNIVERSES
    assert Universe.SHADOW_REALITY not in UNIVERSE_CONTRACTS
    assert {q.question_id for q in RETIRED_QUESTIONS} == {
        "SR-001", "SR-002", "SR-003", "SR-004", "SR-005",
    }
    assert all(
        Universe.SHADOW_REALITY not in question.required_universes
        for question in QUESTION_BANK
    )


def test_shadow_runtime_stream_is_not_silently_reclassified():
    # The canonical production shadow source is the S3 shadow_runtime_v1 event
    # stream. An incomplete lifecycle (a CLOSE with NO matching OPEN) must
    # never become a completed shadow outcome, and raw runtime events must
    # never be silently reclassified as shadow_trades_v1 records.
    fake = install_fake_s3()
    try:
        fake.add("shadow_runtime", [{
            "schema_version": "shadow_runtime_v1",
            "event_type": "CLOSE",
            "shadow_trade_id": "nshadow_1_EURUSD_SCALP",
            "canonical_opportunity_id": "nopp_1",
            "observation_id": "nobs_1",
            "symbol": "EURUSD",
            "exit_reason": "take_profit",
            "exit_price": 1.09,
            "bars_held": 3,
            "outcome": {"pnl_r_multiple": 1.0},
        }], symbol="EURUSD")
        builder = ShadowOutcomeUniverseBuilder()
        assert builder.build() == []
    finally:
        reset_fake_s3()


def test_shadow_outcome_universe_ingests_completed_nshadow_lifecycle():
    # A complete PLAN→OPEN→PROGRESS→CLOSE nshadow_* lifecycle normalises into
    # the internal research shape and enters the shadow outcome population.
    fake = install_fake_s3()
    try:
        fake.add("shadow_runtime", _completed_lifecycle_events(), symbol="EURUSD")
        builder = ShadowOutcomeUniverseBuilder()
        records = builder.build()
        assert len(records) == 1
        rec = records[0]
        assert rec["shadow_trade_id"] == "nshadow_7_EURUSD_SCALP"
        assert rec["r_multiple"] == pytest.approx(1.25)
        assert rec["exit_reason"] == "take_profit"
        assert rec["mfe_r"] == pytest.approx(1.4)
        assert rec["mae_r"] == pytest.approx(-0.3)
        assert rec["evaluated_horizon"] == "SCALP"
        assert rec["shadow_type"] == "HORIZON_ALTERNATIVE"
        assert rec["evidence_source"] == "COUNTERFACTUAL"
        # Canonical lineage preserved end-to-end
        assert rec["correlation_id"] == ""  # never fabricated
    finally:
        reset_fake_s3()


def _completed_lifecycle_events() -> list[dict]:
    """Realistic shadow_runtime_v1 PLAN/OPEN/PROGRESS/CLOSE for one nshadow_* trade."""
    root = "nopp_20260828_0001_EURUSD"
    plan_id = "nplan_7_EURUSD_1777700000"
    observation_id = "nobs_20260828_0001_EURUSD"
    trade_id = "nshadow_7_EURUSD_SCALP"
    plan = {
        "schema_version": "shadow_runtime_v1",
        "event_type": "PLAN",
        "canonical_opportunity_id": root,
        "observation_id": observation_id,
        "symbol": "EURUSD",
        "plan_id": plan_id,
        "cycle_id": 7,
        "entity_id": "EURUSD_1777700000",
        "direction": "BUY",
        "entry_price_basis": "ASK",
        "horizons": [{"horizon": "SCALP", "state": "CONSTRUCTED"}],
        "constructed_count": 1,
    }
    opening = {
        "schema_version": "shadow_runtime_v1",
        "event_type": "OPEN",
        "canonical_opportunity_id": root,
        "observation_id": observation_id,
        "shadow_trade_id": trade_id,
        "symbol": "EURUSD",
        "plan_id": plan_id,
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
    progress = {
        "schema_version": "shadow_runtime_v1",
        "event_type": "PROGRESS",
        "canonical_opportunity_id": root,
        "observation_id": observation_id,
        "shadow_trade_id": trade_id,
        "symbol": "EURUSD",
        "lifecycle": {"bars_elapsed": 2, "max_favourable_price": 1.08110,
                      "max_adverse_price": 1.07970},
    }
    closing = {
        "schema_version": "shadow_runtime_v1",
        "event_type": "CLOSE",
        "canonical_opportunity_id": root,
        "observation_id": observation_id,
        "shadow_trade_id": trade_id,
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
    return [plan, opening, progress, closing]
