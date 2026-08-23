"""
Contract 5 policy-freeze regression tests (Phase 1H).

Frozen owner policy (Phase 1G/1H):
    For every opportunity, Shadow evaluates each horizon independently from the
    eligible/constructible horizon set (SCALP / INTRADAY / EXTENDED).

    - If V10 successfully identifies/selects a horizon:
        the selected horizon is recorded as SELECTED;
        every other eligible/constructible horizon is recorded as ALTERNATIVE.
    - If V10 does not select a horizon:
        ALL eligible/constructible horizons are recorded as ALTERNATIVE
        and there is NO SELECTED horizon.
    - There is NO artificial minimum of 2 or 3 simulations.
      (The legacy "2 alt horizons if passed_identification_condition, else 3"
      wording is superseded/ambiguous legacy text — NOT implemented.)

These tests pin the CURRENT frozen runtime behaviour exactly as produced by
core/runtime/live_scanner.py:729 (gate), :781 (per-horizon loop) and :795
(status ternary). No production code change is covered or implied.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.shadow_trades import ShadowTradeEngine


# ─── HELPERS ──────────────────────────────────────────────────────────────────


def _simulate_contract5(
    engine: ShadowTradeEngine,
    *,
    cycle_id: int,
    symbol: str,
    horizons: list[str],
    v10_selected: str,
):
    """
    Mirror the production Shadow-initialisation block
    (core/runtime/live_scanner.py:781–825), including the horizon-selection
    status ternary at live_scanner.py:795:

        _hz_status = "SELECTED" if _sh_t.horizon == _v10_hz_for_shadow
                     else "ALTERNATIVE"

    where _v10_hz_for_shadow defaults to "" when the V10 pipeline produced no
    horizon selection (live_scanner.py:784–790).
    """
    created: list[tuple[str, str]] = []
    _v10_hz_for_shadow = v10_selected or ""
    for horizon in horizons:
        trade_id = f"hshadow_{cycle_id}_{symbol}_{horizon}"
        # live_scanner.py:795 — verbatim contract
        hz_status = "SELECTED" if horizon == _v10_hz_for_shadow else "ALTERNATIVE"
        engine.open_trade(
            trade_id=trade_id,
            cycle_id=cycle_id,
            symbol=symbol,
            direction="SELL",
            entry_price=1.25000,
            stop_loss=1.25200,
            take_profit=1.24600,
            entry_time=1784800000.0,
            pattern="TWEEZER_TOP",
            score=0.62,
            correlation_id=f"HORIZON-{cycle_id}-{symbol}",   # live_scanner.py:809
            entity_id=f"{symbol}_1784800000",                # live_scanner.py:810
            trade_horizon=horizon,                           # live_scanner.py:816
            shadow_type="HORIZON_ALTERNATIVE",               # live_scanner.py:818
            v10_selected_horizon=_v10_hz_for_shadow,         # live_scanner.py:819
            horizon_selection_status=hz_status,              # live_scanner.py:820
            evaluated_horizon=horizon,                       # live_scanner.py:821
            horizon_geometry_source="STRUCTURE_BASED",       # live_scanner.py:822
        )
        created.append((trade_id, hz_status))
    return created


# ─── TESTS ────────────────────────────────────────────────────────────────────


class TestContract5PolicyFreeze:
    """Frozen matrix: N = |eligible ∩ constructible|, no artificial minimum."""

    def test_zero_eligible_produces_zero_shadows(self):
        """0 eligible/constructible → 0 shadows (live_scanner.py:729 gate)."""
        engine = ShadowTradeEngine(max_bars=5)
        created = _simulate_contract5(
            engine, cycle_id=1, symbol="EURUSD", horizons=[], v10_selected="SCALP",
        )
        assert created == []
        assert engine.active_count == 0

    def test_one_eligible_with_selection_is_selected(self):
        """1 eligible + selected → 1 SELECTED."""
        engine = ShadowTradeEngine(max_bars=5)
        created = _simulate_contract5(
            engine, cycle_id=2, symbol="EURUSD", horizons=["SCALP"],
            v10_selected="SCALP",
        )
        assert len(created) == 1
        trade = engine._active["hshadow_2_EURUSD_SCALP"]
        assert trade.horizon_selection_status == "SELECTED"
        assert trade.v10_selected_horizon == "SCALP"
        assert trade.evaluated_horizon == "SCALP"
        assert trade.trade_horizon == "SCALP"

    def test_one_eligible_without_selection_is_alternative(self):
        """1 eligible + no selection → 1 ALTERNATIVE, no SELECTED horizon."""
        engine = ShadowTradeEngine(max_bars=5)
        created = _simulate_contract5(
            engine, cycle_id=3, symbol="GBPUSD", horizons=["INTRADAY"],
            v10_selected="",
        )
        assert len(created) == 1
        trade = engine._active["hshadow_3_GBPUSD_INTRADAY"]
        assert trade.horizon_selection_status == "ALTERNATIVE"
        assert trade.v10_selected_horizon == ""

    def test_two_eligible_with_selection_one_selected_one_alternative(self):
        """2 eligible + selected among them → 1 SELECTED + 1 ALTERNATIVE."""
        engine = ShadowTradeEngine(max_bars=5)
        created = _simulate_contract5(
            engine, cycle_id=4, symbol="USDJPY",
            horizons=["SCALP", "INTRADAY"], v10_selected="INTRADAY",
        )
        assert len(created) == 2
        statuses = {
            t.trade_horizon: t.horizon_selection_status for t in engine._active.values()
        }
        assert statuses == {"SCALP": "ALTERNATIVE", "INTRADAY": "SELECTED"}
        # All siblings share the correlation group
        assert {t.correlation_id for t in engine._active.values()} == {"HORIZON-4-USDJPY"}

    def test_three_eligible_no_selection_all_alternative(self):
        """3 eligible + no selection → 3 ALTERNATIVE, no SELECTED horizon."""
        engine = ShadowTradeEngine(max_bars=5)
        created = _simulate_contract5(
            engine, cycle_id=5, symbol="XAUUSD",
            horizons=["SCALP", "INTRADAY", "EXTENDED"], v10_selected="",
        )
        assert len(created) == 3
        assert engine.active_count == 3
        statuses = [t.horizon_selection_status for t in engine._active.values()]
        assert statuses == ["ALTERNATIVE", "ALTERNATIVE", "ALTERNATIVE"]
        assert {t.v10_selected_horizon for t in engine._active.values()} == {""}

    def test_three_eligible_with_selection_one_selected_two_alternative(self):
        """3 eligible + selected → 1 SELECTED + 2 ALTERNATIVE."""
        engine = ShadowTradeEngine(max_bars=5)
        created = _simulate_contract5(
            engine, cycle_id=6, symbol="AUDUSD",
            horizons=["SCALP", "INTRADAY", "EXTENDED"], v10_selected="EXTENDED",
        )
        assert len(created) == 3
        by_horizon = {t.trade_horizon: t for t in engine._active.values()}
        assert by_horizon["EXTENDED"].horizon_selection_status == "SELECTED"
        assert by_horizon["SCALP"].horizon_selection_status == "ALTERNATIVE"
        assert by_horizon["INTRADAY"].horizon_selection_status == "ALTERNATIVE"
        # Every record carries the group's selection for research filtering
        assert {t.v10_selected_horizon for t in engine._active.values()} == {"EXTENDED"}
        # Sibling identity: shared entity_id + per-member composite trade_id
        assert {t.entity_id for t in engine._active.values()} == {"AUDUSD_1784800000"}
        assert set(engine._active.keys()) == {
            "hshadow_6_AUDUSD_SCALP",
            "hshadow_6_AUDUSD_INTRADAY",
            "hshadow_6_AUDUSD_EXTENDED",
        }
