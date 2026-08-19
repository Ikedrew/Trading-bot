"""
Tests for Shadow → Reality Bridge V1.

Covers:
    - Identity / joining semantics
    - R calculation (BUY, SELL, zero risk)
    - Comparison field derivation
    - Failure / edge cases
    - Status classification
    - Architectural isolation
"""

import json
import math
import pytest
from pathlib import Path

from research_engine.v10.universes.shadow_reality_models import (
    ComparisonStatus,
    GEOMETRY_RELATIVE_TOLERANCE,
    ShadowRealityComparison,
    ShadowRealityCoverageReport,
)
from research_engine.v10.universes.shadow_reality_universe import (
    ShadowRealityUniverseBuilder,
    _filter_authoritative_shadows,
    _build_comparison,
    _build_shadow_only,
    _index_journal,
)


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _make_shadow(
    correlation_id="COR-20260814-100-EURUSD-ABCD",
    symbol="EURUSD",
    direction="BUY",
    entry_price=1.1000,
    stop_loss=1.0950,
    take_profit=1.1150,
    exit_price=1.1150,
    r_multiple=3.0,
    exit_reason="take_profit",
    bars_held=10,
    mfe_r=3.2,
    mae_r=0.5,
    shadow_type="V10_PRIMARY",
    v10_action="EXECUTE",
    entity_id="EURUSD_1786000000",
    pattern="TBC",
    trade_horizon="SCALP",
    spread_at_entry=0.00012,
    timestamp_decision_utc=1786000000.0,
):
    return {
        "schema_version": "shadow_trades_v2",
        "source": "shadow_trade_engine",
        "identity": {
            "trade_id": f"shadow_100_{symbol}",
            "correlation_id": correlation_id,
            "symbol": symbol,
            "strategy_id": "REVERSAL",
            "cycle_id": "100",
            "entity_id": entity_id,
            "shadow_type": shadow_type,
            "v10_selected_horizon": "SCALP",
            "horizon_selection_status": "SELECTED",
            "evaluated_horizon": "SCALP",
            "horizon_geometry_source": "V10_ENTRY_ENGINE",
            "v10_rejection_stage": "",
            "v10_action": v10_action,
        },
        "decision_snapshot": {
            "timestamp_decision_utc": timestamp_decision_utc,
            "entry_intent_price": entry_price,
            "stop_loss_intent": stop_loss,
            "take_profit_intent": take_profit,
            "direction": direction,
            "position_size": 0.01,
            "risk_config_snapshot": {"risk_price_distance": abs(entry_price - stop_loss)},
            "pattern": pattern,
            "score": 0.65,
            "spread_at_entry": spread_at_entry,
            "bid_at_entry": entry_price - 0.00005,
            "ask_at_entry": entry_price + 0.00005,
            "market_phase": None,
            "regime": None,
            "h4_regime": None,
            "h1_bias": None,
            "trade_horizon": trade_horizon,
        },
        "simulation_environment": {"htf_snapshot": None, "entry_bar_index": 0, "events_ref": {"bar_time": timestamp_decision_utc}},
        "simulated_outcome": {
            "exit_price": exit_price,
            "exit_timestamp": timestamp_decision_utc + bars_held * 300,
            "pnl_r_multiple": r_multiple,
            "mfe_r": mfe_r,
            "mae_r": mae_r,
            "exit_reason": exit_reason,
            "bars_held": bars_held,
            "trade_state_progression": [],
        },
    }


def _make_journal(
    correlation_id="COR-20260814-100-EURUSD-ABCD",
    symbol="EURUSD",
    direction="BUY",
    entry_price=1.1001,
    exit_price=1.1150,
    initial_sl=1.0950,
    initial_tp=1.1150,
    close_reason="take_profit",
    duration_seconds=3000.0,
    realised_pnl=14.9,
    net_pnl=14.7,
    commission=0.2,
    swap=0.0,
    max_favourable_price=1.1155,
    pattern_name="TBC",
    trade_horizon="SCALP",
):
    return {
        "schema_version": "trade_journal_v1",
        "trade_id": "pos_12345",
        "position_ticket": 12345,
        "symbol": symbol,
        "magic": 713001,
        "pattern_name": pattern_name,
        "direction": direction,
        "entry_time": 1786000001.0,
        "exit_time": 1786000001.0 + duration_seconds,
        "duration_seconds": duration_seconds,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "initial_volume": 0.01,
        "final_volume": 0.01,
        "realised_pnl": realised_pnl,
        "commission": commission,
        "swap": swap,
        "net_pnl": net_pnl,
        "close_reason": close_reason,
        "initial_sl": initial_sl,
        "initial_tp": initial_tp,
        "max_favourable_price": max_favourable_price,
        "recorded_at_utc": "2026-08-14T10:00:00Z",
        "correlation_id": correlation_id,
        "trade_horizon": trade_horizon,
    }


def _make_exec_result(correlation_id="COR-20260814-100-EURUSD-ABCD", slippage=0.00015):
    return {
        "schema_version": "execution_results_v1",
        "correlation_id": correlation_id,
        "result_ok": True,
        "fill_price": 1.1001,
        "slippage": slippage,
        "symbol": "EURUSD",
        "side": "BUY",
    }


def _write_jsonl(path: Path, records: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")


# ═══════════════════════════════════════════════════════════════════════════════
# IDENTITY / JOINING
# ═══════════════════════════════════════════════════════════════════════════════

class TestJoining:
    def test_perfect_match(self, tmp_path):
        shadow_dir = tmp_path / "shadows" / "EURUSD"
        journal_dir = tmp_path / "journal"
        _write_jsonl(shadow_dir / "2026-08-14.jsonl", [_make_shadow()])
        _write_jsonl(journal_dir / "2026-08-14.jsonl", [_make_journal()])

        builder = ShadowRealityUniverseBuilder(shadow_dir=tmp_path / "shadows", journal_dir=journal_dir)
        comps = builder.build()
        matched = [c for c in comps if c.comparison_status == ComparisonStatus.MATCHED]
        assert len(matched) == 1
        assert matched[0].correlation_id == "COR-20260814-100-EURUSD-ABCD"

    def test_symbol_agreement(self, tmp_path):
        shadow_dir = tmp_path / "shadows" / "EURUSD"
        journal_dir = tmp_path / "journal"
        _write_jsonl(shadow_dir / "2026-08-14.jsonl", [_make_shadow()])
        _write_jsonl(journal_dir / "2026-08-14.jsonl", [_make_journal()])

        builder = ShadowRealityUniverseBuilder(shadow_dir=tmp_path / "shadows", journal_dir=journal_dir)
        matched = builder.get_population(ComparisonStatus.MATCHED)
        assert matched[0].symbol == "EURUSD"

    def test_direction_agreement(self, tmp_path):
        shadow_dir = tmp_path / "shadows" / "EURUSD"
        journal_dir = tmp_path / "journal"
        _write_jsonl(shadow_dir / "d.jsonl", [_make_shadow(direction="SELL", stop_loss=1.1050, take_profit=1.0850, exit_price=1.0850, r_multiple=3.0)])
        _write_jsonl(journal_dir / "d.jsonl", [_make_journal(direction="SELL", initial_sl=1.1050, initial_tp=1.0850, exit_price=1.0850)])

        builder = ShadowRealityUniverseBuilder(shadow_dir=tmp_path / "shadows", journal_dir=journal_dir)
        matched = builder.get_population(ComparisonStatus.MATCHED)
        assert len(matched) == 1
        assert matched[0].direction == "SELL"

    def test_identity_mismatch_symbol(self, tmp_path):
        """Symbol disagrees → IDENTITY_MISMATCH."""
        shadow_dir = tmp_path / "shadows" / "EURUSD"
        journal_dir = tmp_path / "journal"
        _write_jsonl(shadow_dir / "d.jsonl", [_make_shadow(symbol="EURUSD")])
        _write_jsonl(journal_dir / "d.jsonl", [_make_journal(symbol="GBPUSD")])

        builder = ShadowRealityUniverseBuilder(shadow_dir=tmp_path / "shadows", journal_dir=journal_dir)
        mismatches = builder.get_population(ComparisonStatus.IDENTITY_MISMATCH)
        assert len(mismatches) == 1

    def test_identity_mismatch_direction(self, tmp_path):
        """Direction disagrees → IDENTITY_MISMATCH."""
        shadow_dir = tmp_path / "shadows" / "EURUSD"
        journal_dir = tmp_path / "journal"
        _write_jsonl(shadow_dir / "d.jsonl", [_make_shadow(direction="BUY")])
        _write_jsonl(journal_dir / "d.jsonl", [_make_journal(direction="SELL")])

        builder = ShadowRealityUniverseBuilder(shadow_dir=tmp_path / "shadows", journal_dir=journal_dir)
        mismatches = builder.get_population(ComparisonStatus.IDENTITY_MISMATCH)
        assert len(mismatches) == 1

    def test_duplicate_shadow_correlation_id(self, tmp_path):
        """Duplicate shadow cor_id → AMBIGUOUS."""
        shadow_dir = tmp_path / "shadows" / "EURUSD"
        journal_dir = tmp_path / "journal"
        s1 = _make_shadow()
        s2 = _make_shadow()  # Same correlation_id
        _write_jsonl(shadow_dir / "d.jsonl", [s1, s2])
        _write_jsonl(journal_dir / "d.jsonl", [_make_journal()])

        builder = ShadowRealityUniverseBuilder(shadow_dir=tmp_path / "shadows", journal_dir=journal_dir)
        ambiguous = builder.get_population(ComparisonStatus.AMBIGUOUS)
        assert len(ambiguous) >= 1
        report = builder.get_coverage_report()
        assert report.duplicate_shadow_correlation_ids >= 1

    def test_no_trade_shadows_excluded(self, tmp_path):
        """NO_TRADE shadows are not in the authoritative population."""
        shadow_dir = tmp_path / "shadows" / "EURUSD"
        journal_dir = tmp_path / "journal"
        _write_jsonl(shadow_dir / "d.jsonl", [_make_shadow(v10_action="NO_TRADE")])
        _write_jsonl(journal_dir / "d.jsonl", [_make_journal()])

        builder = ShadowRealityUniverseBuilder(shadow_dir=tmp_path / "shadows", journal_dir=journal_dir)
        comps = builder.build()
        # Should be REAL_ONLY since the shadow is excluded
        real_only = [c for c in comps if c.comparison_status == ComparisonStatus.REAL_ONLY]
        assert len(real_only) == 1

    def test_v10shadow_prefix_excluded(self, tmp_path):
        """V10SHADOW-* prefix is legacy → excluded."""
        shadow_dir = tmp_path / "shadows" / "EURUSD"
        journal_dir = tmp_path / "journal"
        _write_jsonl(shadow_dir / "d.jsonl", [_make_shadow(correlation_id="V10SHADOW-100-EURUSD")])
        _write_jsonl(journal_dir / "d.jsonl", [_make_journal(correlation_id="V10SHADOW-100-EURUSD")])

        builder = ShadowRealityUniverseBuilder(shadow_dir=tmp_path / "shadows", journal_dir=journal_dir)
        comps = builder.build()
        matched = [c for c in comps if c.comparison_status == ComparisonStatus.MATCHED]
        assert len(matched) == 0

    def test_horizon_alternative_excluded(self, tmp_path):
        """HORIZON_ALTERNATIVE shadows are not authoritative."""
        shadow_dir = tmp_path / "shadows" / "EURUSD"
        journal_dir = tmp_path / "journal"
        _write_jsonl(shadow_dir / "d.jsonl", [_make_shadow(shadow_type="HORIZON_ALTERNATIVE")])
        _write_jsonl(journal_dir / "d.jsonl", [_make_journal()])

        builder = ShadowRealityUniverseBuilder(shadow_dir=tmp_path / "shadows", journal_dir=journal_dir)
        matched = builder.get_population(ComparisonStatus.MATCHED)
        assert len(matched) == 0

    def test_missing_correlation_id_shadow(self, tmp_path):
        """Shadow without correlation_id is excluded."""
        shadow_dir = tmp_path / "shadows" / "EURUSD"
        journal_dir = tmp_path / "journal"
        _write_jsonl(shadow_dir / "d.jsonl", [_make_shadow(correlation_id="")])
        _write_jsonl(journal_dir / "d.jsonl", [_make_journal()])

        builder = ShadowRealityUniverseBuilder(shadow_dir=tmp_path / "shadows", journal_dir=journal_dir)
        report = builder.get_coverage_report()
        assert report.shadows_without_correlation_id >= 1
        matched = builder.get_population(ComparisonStatus.MATCHED)
        assert len(matched) == 0

    def test_shadow_only(self, tmp_path):
        """Shadow with no journal match → SHADOW_ONLY."""
        shadow_dir = tmp_path / "shadows" / "EURUSD"
        journal_dir = tmp_path / "journal"
        _write_jsonl(shadow_dir / "d.jsonl", [_make_shadow()])
        journal_dir.mkdir(parents=True, exist_ok=True)  # Empty journal

        builder = ShadowRealityUniverseBuilder(shadow_dir=tmp_path / "shadows", journal_dir=journal_dir)
        shadow_only = builder.get_population(ComparisonStatus.SHADOW_ONLY)
        assert len(shadow_only) == 1
        assert shadow_only[0].shadow_r != 0

    def test_real_only(self, tmp_path):
        """Journal with no shadow match → REAL_ONLY."""
        shadow_dir = tmp_path / "shadows"
        journal_dir = tmp_path / "journal"
        shadow_dir.mkdir(parents=True, exist_ok=True)  # Empty shadows
        _write_jsonl(journal_dir / "d.jsonl", [_make_journal()])

        builder = ShadowRealityUniverseBuilder(shadow_dir=shadow_dir, journal_dir=journal_dir)
        real_only = builder.get_population(ComparisonStatus.REAL_ONLY)
        assert len(real_only) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# R CALCULATION
# ═══════════════════════════════════════════════════════════════════════════════

class TestRCalculation:
    def test_buy_realised_r(self, tmp_path):
        """BUY: R = (exit - entry) / abs(entry - sl)"""
        shadow_dir = tmp_path / "shadows" / "EURUSD"
        journal_dir = tmp_path / "journal"
        # entry=1.1000, sl=1.0950, exit=1.1100 → R = 0.01/0.005 = 2.0
        _write_jsonl(shadow_dir / "d.jsonl", [_make_shadow(r_multiple=2.5)])
        _write_jsonl(journal_dir / "d.jsonl", [_make_journal(
            entry_price=1.1000, exit_price=1.1100, initial_sl=1.0950,
        )])

        builder = ShadowRealityUniverseBuilder(shadow_dir=tmp_path / "shadows", journal_dir=journal_dir)
        matched = builder.get_population(ComparisonStatus.MATCHED)
        assert len(matched) == 1
        assert abs(matched[0].realised_gross_r - 2.0) < 0.001

    def test_sell_realised_r(self, tmp_path):
        """SELL: R = (entry - exit) / abs(entry - sl)"""
        shadow_dir = tmp_path / "shadows" / "EURUSD"
        journal_dir = tmp_path / "journal"
        # SELL: entry=1.1000, sl=1.1050, exit=1.0900 → R = 0.01/0.005 = 2.0
        _write_jsonl(shadow_dir / "d.jsonl", [_make_shadow(
            direction="SELL", entry_price=1.1000, stop_loss=1.1050,
            take_profit=1.0850, exit_price=1.0900, r_multiple=2.0,
        )])
        _write_jsonl(journal_dir / "d.jsonl", [_make_journal(
            direction="SELL", entry_price=1.1000, exit_price=1.0900,
            initial_sl=1.1050, initial_tp=1.0850,
        )])

        builder = ShadowRealityUniverseBuilder(shadow_dir=tmp_path / "shadows", journal_dir=journal_dir)
        matched = builder.get_population(ComparisonStatus.MATCHED)
        assert abs(matched[0].realised_gross_r - 2.0) < 0.001

    def test_zero_risk_distance(self, tmp_path):
        """entry == sl → GEOMETRY_INVALID."""
        shadow_dir = tmp_path / "shadows" / "EURUSD"
        journal_dir = tmp_path / "journal"
        _write_jsonl(shadow_dir / "d.jsonl", [_make_shadow(stop_loss=1.1000)])
        _write_jsonl(journal_dir / "d.jsonl", [_make_journal(entry_price=1.1000, initial_sl=1.1000)])

        builder = ShadowRealityUniverseBuilder(shadow_dir=tmp_path / "shadows", journal_dir=journal_dir)
        invalid = builder.get_population(ComparisonStatus.GEOMETRY_INVALID)
        assert len(invalid) == 1

    def test_shadow_r_preserved(self, tmp_path):
        """Shadow R comes directly from the persisted record, not recalculated."""
        shadow_dir = tmp_path / "shadows" / "EURUSD"
        journal_dir = tmp_path / "journal"
        _write_jsonl(shadow_dir / "d.jsonl", [_make_shadow(r_multiple=1.2345)])
        _write_jsonl(journal_dir / "d.jsonl", [_make_journal()])

        builder = ShadowRealityUniverseBuilder(shadow_dir=tmp_path / "shadows", journal_dir=journal_dir)
        matched = builder.get_population(ComparisonStatus.MATCHED)
        assert matched[0].shadow_r == round(1.2345, 6)

    def test_delta_r_sign_convention(self, tmp_path):
        """delta_r = shadow_r - realised_gross_r (exactly)."""
        shadow_dir = tmp_path / "shadows" / "EURUSD"
        journal_dir = tmp_path / "journal"
        # Shadow says +2.0R, reality is +1.5R → delta = +0.5
        _write_jsonl(shadow_dir / "d.jsonl", [_make_shadow(r_multiple=2.0)])
        # entry=1.1001, exit=1.1076, sl=1.0950 → R = 0.0075/0.0051 ≈ 1.4706
        _write_jsonl(journal_dir / "d.jsonl", [_make_journal(
            entry_price=1.1001, exit_price=1.1076, initial_sl=1.0950,
        )])

        builder = ShadowRealityUniverseBuilder(shadow_dir=tmp_path / "shadows", journal_dir=journal_dir)
        matched = builder.get_population(ComparisonStatus.MATCHED)
        c = matched[0]
        expected_real_r = (1.1076 - 1.1001) / abs(1.1001 - 1.0950)
        assert abs(c.realised_gross_r - expected_real_r) < 0.0001
        assert abs(c.delta_r - (2.0 - expected_real_r)) < 0.0001

    def test_negative_r_buy(self, tmp_path):
        """BUY loss: exit below entry → negative R."""
        shadow_dir = tmp_path / "shadows" / "EURUSD"
        journal_dir = tmp_path / "journal"
        _write_jsonl(shadow_dir / "d.jsonl", [_make_shadow(r_multiple=-1.0, exit_price=1.0950, exit_reason="stop_loss")])
        _write_jsonl(journal_dir / "d.jsonl", [_make_journal(
            entry_price=1.1000, exit_price=1.0950, initial_sl=1.0950, close_reason="stop_loss",
        )])

        builder = ShadowRealityUniverseBuilder(shadow_dir=tmp_path / "shadows", journal_dir=journal_dir)
        matched = builder.get_population(ComparisonStatus.MATCHED)
        assert matched[0].realised_gross_r == pytest.approx(-1.0, abs=0.001)


# ═══════════════════════════════════════════════════════════════════════════════
# COMPARISON FIELDS
# ═══════════════════════════════════════════════════════════════════════════════

class TestComparisonFields:
    def test_entry_slippage(self, tmp_path):
        """entry_slippage = real_entry - shadow_entry (signed)."""
        shadow_dir = tmp_path / "shadows" / "EURUSD"
        journal_dir = tmp_path / "journal"
        # Shadow intent=1.1000, real fill=1.1003 → slippage = +0.0003
        _write_jsonl(shadow_dir / "d.jsonl", [_make_shadow(entry_price=1.1000)])
        _write_jsonl(journal_dir / "d.jsonl", [_make_journal(entry_price=1.1003)])

        builder = ShadowRealityUniverseBuilder(shadow_dir=tmp_path / "shadows", journal_dir=journal_dir)
        matched = builder.get_population(ComparisonStatus.MATCHED)
        assert abs(matched[0].entry_slippage - 0.0003) < 1e-7

    def test_exit_reason_stop_loss_match(self, tmp_path):
        shadow_dir = tmp_path / "shadows" / "EURUSD"
        journal_dir = tmp_path / "journal"
        _write_jsonl(shadow_dir / "d.jsonl", [_make_shadow(exit_reason="stop_loss", r_multiple=-1.0, exit_price=1.0950)])
        _write_jsonl(journal_dir / "d.jsonl", [_make_journal(close_reason="stop_loss", exit_price=1.0950)])

        builder = ShadowRealityUniverseBuilder(shadow_dir=tmp_path / "shadows", journal_dir=journal_dir)
        matched = builder.get_population(ComparisonStatus.MATCHED)
        assert matched[0].exit_reason_match is True

    def test_exit_reason_take_profit_match(self, tmp_path):
        shadow_dir = tmp_path / "shadows" / "EURUSD"
        journal_dir = tmp_path / "journal"
        _write_jsonl(shadow_dir / "d.jsonl", [_make_shadow(exit_reason="take_profit")])
        _write_jsonl(journal_dir / "d.jsonl", [_make_journal(close_reason="take_profit")])

        builder = ShadowRealityUniverseBuilder(shadow_dir=tmp_path / "shadows", journal_dir=journal_dir)
        matched = builder.get_population(ComparisonStatus.MATCHED)
        assert matched[0].exit_reason_match is True

    def test_exit_reason_timeout_maps_to_time_exit(self, tmp_path):
        shadow_dir = tmp_path / "shadows" / "EURUSD"
        journal_dir = tmp_path / "journal"
        _write_jsonl(shadow_dir / "d.jsonl", [_make_shadow(exit_reason="max_bars_timeout", r_multiple=-0.3, exit_price=1.0985)])
        _write_jsonl(journal_dir / "d.jsonl", [_make_journal(close_reason="time_exit", exit_price=1.0985)])

        builder = ShadowRealityUniverseBuilder(shadow_dir=tmp_path / "shadows", journal_dir=journal_dir)
        matched = builder.get_population(ComparisonStatus.MATCHED)
        assert matched[0].exit_reason_match is True

    def test_management_exit_no_match(self, tmp_path):
        """management_exit has no shadow equivalent."""
        shadow_dir = tmp_path / "shadows" / "EURUSD"
        journal_dir = tmp_path / "journal"
        _write_jsonl(shadow_dir / "d.jsonl", [_make_shadow(exit_reason="take_profit")])
        _write_jsonl(journal_dir / "d.jsonl", [_make_journal(close_reason="management_exit")])

        builder = ShadowRealityUniverseBuilder(shadow_dir=tmp_path / "shadows", journal_dir=journal_dir)
        matched = builder.get_population(ComparisonStatus.MATCHED)
        assert matched[0].exit_reason_match is False

    def test_manual_close_no_match(self, tmp_path):
        shadow_dir = tmp_path / "shadows" / "EURUSD"
        journal_dir = tmp_path / "journal"
        _write_jsonl(shadow_dir / "d.jsonl", [_make_shadow(exit_reason="stop_loss", r_multiple=-1.0, exit_price=1.0950)])
        _write_jsonl(journal_dir / "d.jsonl", [_make_journal(close_reason="manual_close", exit_price=1.1020)])

        builder = ShadowRealityUniverseBuilder(shadow_dir=tmp_path / "shadows", journal_dir=journal_dir)
        matched = builder.get_population(ComparisonStatus.MATCHED)
        assert matched[0].exit_reason_match is False

    def test_broker_close_no_match(self, tmp_path):
        shadow_dir = tmp_path / "shadows" / "EURUSD"
        journal_dir = tmp_path / "journal"
        _write_jsonl(shadow_dir / "d.jsonl", [_make_shadow(exit_reason="take_profit")])
        _write_jsonl(journal_dir / "d.jsonl", [_make_journal(close_reason="broker_close")])

        builder = ShadowRealityUniverseBuilder(shadow_dir=tmp_path / "shadows", journal_dir=journal_dir)
        matched = builder.get_population(ComparisonStatus.MATCHED)
        assert matched[0].exit_reason_match is False

    def test_geometry_match(self, tmp_path):
        """Identical SL/TP → geometry_match=True."""
        shadow_dir = tmp_path / "shadows" / "EURUSD"
        journal_dir = tmp_path / "journal"
        _write_jsonl(shadow_dir / "d.jsonl", [_make_shadow(stop_loss=1.0950, take_profit=1.1150)])
        _write_jsonl(journal_dir / "d.jsonl", [_make_journal(initial_sl=1.0950, initial_tp=1.1150)])

        builder = ShadowRealityUniverseBuilder(shadow_dir=tmp_path / "shadows", journal_dir=journal_dir)
        matched = builder.get_population(ComparisonStatus.MATCHED)
        assert matched[0].geometry_match is True

    def test_geometry_diverged(self, tmp_path):
        """Different SL → GEOMETRY_DIVERGED status."""
        shadow_dir = tmp_path / "shadows" / "EURUSD"
        journal_dir = tmp_path / "journal"
        _write_jsonl(shadow_dir / "d.jsonl", [_make_shadow(stop_loss=1.0950)])
        _write_jsonl(journal_dir / "d.jsonl", [_make_journal(initial_sl=1.0900)])  # Different

        builder = ShadowRealityUniverseBuilder(shadow_dir=tmp_path / "shadows", journal_dir=journal_dir)
        diverged = builder.get_population(ComparisonStatus.GEOMETRY_DIVERGED)
        assert len(diverged) == 1
        assert diverged[0].geometry_match is False

    def test_commission_preserved(self, tmp_path):
        shadow_dir = tmp_path / "shadows" / "EURUSD"
        journal_dir = tmp_path / "journal"
        _write_jsonl(shadow_dir / "d.jsonl", [_make_shadow()])
        _write_jsonl(journal_dir / "d.jsonl", [_make_journal(commission=1.50)])

        builder = ShadowRealityUniverseBuilder(shadow_dir=tmp_path / "shadows", journal_dir=journal_dir)
        matched = builder.get_population(ComparisonStatus.MATCHED)
        assert matched[0].commission == 1.50

    def test_swap_preserved(self, tmp_path):
        shadow_dir = tmp_path / "shadows" / "EURUSD"
        journal_dir = tmp_path / "journal"
        _write_jsonl(shadow_dir / "d.jsonl", [_make_shadow()])
        _write_jsonl(journal_dir / "d.jsonl", [_make_journal(swap=-0.30)])

        builder = ShadowRealityUniverseBuilder(shadow_dir=tmp_path / "shadows", journal_dir=journal_dir)
        matched = builder.get_population(ComparisonStatus.MATCHED)
        assert matched[0].swap == -0.30

    def test_execution_slippage_enrichment(self, tmp_path):
        """Execution results enrich with slippage field."""
        shadow_dir = tmp_path / "shadows" / "EURUSD"
        journal_dir = tmp_path / "journal"
        exec_dir = tmp_path / "exec" / "EURUSD"
        _write_jsonl(shadow_dir / "d.jsonl", [_make_shadow()])
        _write_jsonl(journal_dir / "d.jsonl", [_make_journal()])
        _write_jsonl(exec_dir / "d.jsonl", [_make_exec_result(slippage=0.00025)])

        builder = ShadowRealityUniverseBuilder(
            shadow_dir=tmp_path / "shadows", journal_dir=journal_dir, exec_results_dir=tmp_path / "exec",
        )
        matched = builder.get_population(ComparisonStatus.MATCHED)
        assert matched[0].execution_slippage == pytest.approx(0.00025)


# ═══════════════════════════════════════════════════════════════════════════════
# FAILURE / EDGE CASES
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_malformed_shadow_record(self, tmp_path):
        """Malformed records are counted but don't crash."""
        shadow_dir = tmp_path / "shadows" / "EURUSD"
        journal_dir = tmp_path / "journal"
        path = shadow_dir / "d.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"bad": true}\n{not json\n', encoding="utf-8")
        journal_dir.mkdir(parents=True, exist_ok=True)

        builder = ShadowRealityUniverseBuilder(shadow_dir=tmp_path / "shadows", journal_dir=journal_dir)
        builder.build()  # Should not raise

    def test_empty_directories(self, tmp_path):
        """Empty source dirs produce empty results."""
        builder = ShadowRealityUniverseBuilder(
            shadow_dir=tmp_path / "none1", journal_dir=tmp_path / "none2",
        )
        comps = builder.build()
        assert comps == []
        report = builder.get_coverage_report()
        assert report.total_shadow_records == 0

    def test_deterministic_rebuild(self, tmp_path):
        """Same inputs → same outputs."""
        shadow_dir = tmp_path / "shadows" / "EURUSD"
        journal_dir = tmp_path / "journal"
        _write_jsonl(shadow_dir / "d.jsonl", [_make_shadow()])
        _write_jsonl(journal_dir / "d.jsonl", [_make_journal()])

        b1 = ShadowRealityUniverseBuilder(shadow_dir=tmp_path / "shadows", journal_dir=journal_dir)
        r1 = b1.build()
        b2 = ShadowRealityUniverseBuilder(shadow_dir=tmp_path / "shadows", journal_dir=journal_dir)
        r2 = b2.build()

        assert len(r1) == len(r2)
        assert r1[0].to_dict() == r2[0].to_dict()

    def test_duplicate_journal_correlation_id(self, tmp_path):
        """Duplicate journal cor_id → excluded from matching."""
        shadow_dir = tmp_path / "shadows" / "EURUSD"
        journal_dir = tmp_path / "journal"
        _write_jsonl(shadow_dir / "d.jsonl", [_make_shadow()])
        j1 = _make_journal()
        j2 = _make_journal()  # Same correlation_id
        _write_jsonl(journal_dir / "d.jsonl", [j1, j2])

        builder = ShadowRealityUniverseBuilder(shadow_dir=tmp_path / "shadows", journal_dir=journal_dir)
        report = builder.get_coverage_report()
        assert report.duplicate_journal_correlation_ids >= 1
        # Shadow should be SHADOW_ONLY since journal duplicate was removed
        shadow_only = builder.get_population(ComparisonStatus.SHADOW_ONLY)
        assert len(shadow_only) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# COVERAGE REPORT
# ═══════════════════════════════════════════════════════════════════════════════

class TestCoverageReport:
    def test_report_fields(self, tmp_path):
        shadow_dir = tmp_path / "shadows" / "EURUSD"
        journal_dir = tmp_path / "journal"
        _write_jsonl(shadow_dir / "d.jsonl", [_make_shadow()])
        _write_jsonl(journal_dir / "d.jsonl", [_make_journal()])

        builder = ShadowRealityUniverseBuilder(shadow_dir=tmp_path / "shadows", journal_dir=journal_dir)
        builder.build()
        report = builder.get_coverage_report()
        assert report.total_shadow_records == 1
        assert report.authoritative_v10_primary_execute == 1
        assert report.matched == 1
        assert report.match_rate == 1.0


# ═══════════════════════════════════════════════════════════════════════════════
# ARCHITECTURAL ISOLATION
# ═══════════════════════════════════════════════════════════════════════════════

class TestArchitecturalIsolation:
    def test_no_production_imports(self):
        """Bridge modules must not import production execution code."""
        import ast
        import os

        files = [
            'research_engine/v10/universes/shadow_reality_models.py',
            'research_engine/v10/universes/shadow_reality_universe.py',
        ]
        forbidden = {'MT5Execution', 'RiskManager', 'ExecutionOrchestrator', 'order_send',
                     'TradeStateManager', 'live_scanner'}

        for fpath in files:
            assert os.path.exists(fpath), f"Missing: {fpath}"
            with open(fpath, 'r', encoding='utf-8') as f:
                tree = ast.parse(f.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ''
                    names = [a.name for a in (node.names or [])]
                    for fb in forbidden:
                        assert fb not in module, f"{fpath} imports module containing {fb}"
                        assert fb not in names, f"{fpath} imports name {fb}"
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        for fb in forbidden:
                            assert fb not in alias.name, f"{fpath} imports {alias.name}"
