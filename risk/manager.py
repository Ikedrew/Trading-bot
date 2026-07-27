"""
Risk Manager — SL/TP + volume composition into RiskDecision (accept or reject).

Primary interface:
    risk_manager.evaluate(assessment=..., candles=..., bid=..., ask=...)

Assessment provides analytical identity (pattern, side, symbol, bar_time).
Candles/bid/ask provide execution context (live prices, geometry source).

Risk does NOT:
    - Interpret scores or strategy classification
    - Make policy decisions (trade_allowed, block_reason)
    - Depend on expected value or confirmation
    - Reconstruct analysis that upstream already completed

Risk ONLY:
    - Computes SL/TP geometry from candle structure
    - Validates risk distance feasibility
    - Sizes position based on account/config
    - Produces OrderIntent or structured rejection
"""

from __future__ import annotations

import logging
from typing import Any

import MetaTrader5 as mt5

from core import config as _cfg
from data.mt5_data import Candle
from risk.decision import RiskDecision, accept, reject
from risk.levels import build_sl_tp, RiskRejection, _log_rejection
from risk.metrics import risk_metrics
from risk.models import OrderIntent
from risk.position_sizing import volume_for_risk
from strategy.signals import Side, Signal

logger = logging.getLogger(__name__)

# ─── REJECTION REASONS (manager-level) ────────────────────────────────────────

REJECT_SLTP_FAILED = "SLTP_CALCULATION_FAILED"
REJECT_ZERO_RISK_AT_ENTRY = "ZERO_RISK_AT_EXECUTION_ENTRY"
REJECT_FIXED_LOT_ZERO = "FIXED_LOT_ZERO"
REJECT_DYNAMIC_SIZING_FAILED = "DYNAMIC_SIZING_FAILED"


# ─── BAR INDEX RESOLUTION (assessment → signal bridge) ────────────────────────

def _resolve_bar_index(candles: list[Any], bar_time: int) -> int | None:
    """
    Resolve bar_time (unix seconds) to candle array index.

    Scans from the end (most recent) since the target bar is typically
    the last closed bar. Returns None if not found.
    """
    for i in range(len(candles) - 1, -1, -1):
        if getattr(candles[i], "time", None) == bar_time:
            return i
    return None


# ─── ADAPTIVE MINIMUM SL COMPUTATION ──────────────────────────────────────────

def _compute_adaptive_min_sl(
    symbol: str,
    candles: list[Any],
    closed_i: int,
    bid: float,
    ask: float,
) -> float:
    """
    Compute the adaptive minimum SL distance (in pips) for a symbol.

    Formula: max(absolute_floor, ATR_noise, spread_min)

    When ADAPTIVE_MIN_SL_ENABLED=False, returns the fixed per-symbol minimum.
    Never raises — falls back to absolute floor on any failure.

    Args:
        symbol: Trading symbol
        candles: Candle array (for ATR calculation)
        closed_i: Index of the last closed bar
        bid: Current bid price
        ask: Current ask price

    Returns:
        Minimum acceptable SL distance in pips.
    """
    try:
        _adaptive = bool(getattr(_cfg, "ADAPTIVE_MIN_SL_ENABLED", True))
    except Exception:
        _adaptive = True

    if not _adaptive:
        # Fixed mode — use per-symbol dict
        try:
            _min_sl_dict = getattr(_cfg, "MIN_SL_PIPS", {})
            return float(_min_sl_dict.get(symbol, getattr(_cfg, "MIN_SL_PIPS_DEFAULT", 5.0)))
        except Exception:
            return 5.0

    # ─── ADAPTIVE MODE ────────────────────────────────────────────────
    _pip_size = 0.01 if "JPY" in symbol.upper() else 0.0001

    # Component 1: Absolute floor (safety net)
    try:
        _floor = float(getattr(_cfg, "MIN_SL_ABSOLUTE_FLOOR_PIPS", 3.0))
    except Exception:
        _floor = 3.0

    # Component 2: ATR-based market noise (ATR14 in pips × multiplier)
    _atr_min = 0.0
    try:
        _atr_mult = float(getattr(_cfg, "ATR_SL_MULTIPLIER", 1.0))
        _atr_period = 14
        if candles and closed_i >= _atr_period:
            _tr_sum = 0.0
            for i in range(closed_i - _atr_period + 1, closed_i + 1):
                _h = float(candles[i].high)
                _l = float(candles[i].low)
                _pc = float(candles[i - 1].close) if i > 0 else _l
                _tr = max(_h - _l, abs(_h - _pc), abs(_l - _pc))
                _tr_sum += _tr
            _atr_price = _tr_sum / _atr_period
            _atr_pips = _atr_price / _pip_size
            _atr_min = _atr_pips * _atr_mult
    except Exception:
        _atr_min = 0.0

    # Component 3: Spread-based minimum (current spread × multiplier)
    _spread_min = 0.0
    try:
        _spread_mult = float(getattr(_cfg, "SPREAD_SL_MULTIPLIER", 2.0))
        _spread = abs(ask - bid)
        _spread_pips = _spread / _pip_size
        _spread_min = _spread_pips * _spread_mult
    except Exception:
        _spread_min = 0.0

    # Result: maximum of all three components
    return max(_floor, _atr_min, _spread_min)


# ─── REJECTION METRICS (in-memory counters) ───────────────────────────────────

_rejection_counts: dict[str, int] = {}


def get_rejection_metrics() -> dict[str, int]:
    """Return current rejection counts by reason. For observability/audit."""
    return dict(_rejection_counts)


def reset_rejection_metrics() -> None:
    """Reset counters (e.g. between sessions)."""
    _rejection_counts.clear()


def _track_rejection(reason: str) -> None:
    """Increment rejection counter for a reason."""
    _rejection_counts[reason] = _rejection_counts.get(reason, 0) + 1


def _record_rejection(reason: str, pattern: str, *, symbol: str = "", metadata: dict | None = None) -> None:
    """Track rejection in both legacy counters, metrics system, and event stream."""
    _track_rejection(reason)
    risk_metrics.record_rejected(reason=reason, pattern=pattern)
    # ─── UNIFIED EVENT STREAM: RISK_CHECK (Layer 7) — REJECTED ────
    if symbol:
        try:
            from core.event_stream import emit_risk_check
            emit_risk_check(symbol, {
                "result": "REJECTED",
                "guard": "risk_manager",
                "reason": reason,
                "pattern": pattern,
                "metadata": metadata or {},
            }, source="risk_manager")
        except Exception:
            pass
    # ─── END UNIFIED EVENT STREAM ─────────────────────────────────


class RiskManager:
    def __init__(
        self,
        *,
        fixed_lot: float,
        base_rr: float,
        rr3_patterns: frozenset[str],
        sl_buffer: float,
        min_rr: float,
    ) -> None:
        self._fixed_lot = fixed_lot
        self._base_rr = base_rr
        self._rr3_patterns = rr3_patterns
        self._sl_buffer = sl_buffer
        self._min_rr = min_rr

    # ═══════════════════════════════════════════════════════════════════
    # PRIMARY INTERFACE (assessment-based)
    # ═══════════════════════════════════════════════════════════════════

    def evaluate(
        self,
        *,
        assessment: Any,
        candles: list[Candle],
        bid: float,
        ask: float,
    ) -> RiskDecision:
        """
        Structured risk evaluation — primary interface.

        Assessment provides analytical identity:
            - symbol: instrument name
            - pattern: authoritative pattern name (SLTP rule lookup)
            - side: trade direction ("BUY" or "SELL")
            - bar_time: unix timestamp of the evaluated bar (→ candle index)

        Execution context (separate from assessment):
            - candles: full candle history (geometry source for SL/TP)
            - bid: live bid price (SELL entry reference)
            - ask: live ask price (BUY entry reference)

        Returns:
            RiskDecision (RiskAccepted with OrderIntent, or RiskRejected with reason)
        """
        # Resolve analytical identity from assessment
        symbol = getattr(assessment, "symbol", None)
        _pattern = getattr(assessment, "pattern", None)
        _side_str = getattr(assessment, "side", None)
        _bar_time = getattr(assessment, "bar_time", None)

        if not symbol or not _pattern or not _side_str or not _bar_time:
            rej = RiskRejection(
                reason="INCOMPLETE_ASSESSMENT",
                pattern=_pattern or "unknown",
                symbol=symbol or "unknown",
                metadata={"missing": [
                    k for k, v in [("symbol", symbol), ("pattern", _pattern),
                                   ("side", _side_str), ("bar_time", _bar_time)]
                    if not v
                ]},
            )
            _log_rejection(rej)
            return reject(rej)

        # Resolve bar_time → candle index
        _side = Side(_side_str) if isinstance(_side_str, str) else _side_str
        _bar_index = _resolve_bar_index(candles, _bar_time)

        if _bar_index is None:
            rej = RiskRejection(
                reason="BAR_TIME_NOT_FOUND",
                pattern=_pattern,
                symbol=symbol,
                metadata={"bar_time": _bar_time, "candle_count": len(candles)},
            )
            _log_rejection(rej)
            return reject(rej)

        # Construct execution-context Signal (thin carrier for SL/TP geometry)
        signal = Signal(
            pattern=_pattern,
            side=_side,
            bar_index=_bar_index,
            bar_time=_bar_time,
        )

        return self._execute_risk(symbol=symbol, signal=signal, candles=candles, bid=bid, ask=ask)

    # ═══════════════════════════════════════════════════════════════════
    # LEGACY INTERFACE (signal-based — for shadow rooms + old pipeline)
    # ═══════════════════════════════════════════════════════════════════

    def evaluate_signal(
        self,
        symbol: str,
        signal: Signal,
        candles: list[Candle],
        bid: float,
        ask: float,
    ) -> RiskDecision:
        """
        Legacy signal-based evaluation.

        Used by:
            - shadow_rooms.py (shadow engine — no assessment available)
            - intent_builder.py (old pipeline — via build_intent)

        Behaviour is IDENTICAL to evaluate() — same SL/TP/sizing logic.
        Only the input contract differs (Signal instead of Assessment).
        """
        return self._execute_risk(symbol=symbol, signal=signal, candles=candles, bid=bid, ask=ask)

    def build_intent(
        self,
        symbol: str,
        signal: Signal,
        candles: list[Candle],
        bid: float,
        ask: float,
    ) -> OrderIntent | None:
        """
        Build OrderIntent or return None.

        Legacy convenience wrapper for old pipeline callers (intent_builder.py).
        Internally uses evaluate_signal() for structured risk evaluation.
        """
        decision = self.evaluate_signal(symbol, signal, candles, bid, ask)
        if decision.accepted:
            return decision.intent
        return None

    # ═══════════════════════════════════════════════════════════════════
    # CORE RISK LOGIC (shared by both interfaces)
    # ═══════════════════════════════════════════════════════════════════

    def _execute_risk(
        self,
        *,
        symbol: str,
        signal: Signal,
        candles: list[Candle],
        bid: float,
        ask: float,
    ) -> RiskDecision:
        """
        Core risk computation — SL/TP geometry + volume sizing.

        This is the single implementation that both evaluate() and
        evaluate_signal() delegate to. No analytical reconstruction.
        Pure execution-context computation.
        """
        levels = build_sl_tp(
            signal,
            candles,
            base_rr=self._base_rr,
            rr3_patterns=self._rr3_patterns,
            sl_buffer=self._sl_buffer,
            min_rr=self._min_rr,
        )
        if levels is None:
            rej = RiskRejection(
                reason=REJECT_SLTP_FAILED, pattern=signal.pattern, symbol=symbol,
                metadata={"bar_index": signal.bar_index},
            )
            _log_rejection(rej)
            _record_rejection(REJECT_SLTP_FAILED, signal.pattern, symbol=symbol, metadata={"bar_index": signal.bar_index})
            return reject(rej)
        sl, tp = levels

        if signal.side is Side.BUY:
            entry = ask
            risk_price = entry - sl
            if risk_price <= 0:
                rej = RiskRejection(
                    reason=REJECT_ZERO_RISK_AT_ENTRY, pattern=signal.pattern, symbol=symbol,
                    metadata={"entry": entry, "sl": sl, "risk": risk_price, "side": "BUY", "ask": ask},
                )
                _log_rejection(rej)
                _record_rejection(REJECT_ZERO_RISK_AT_ENTRY, signal.pattern, symbol=symbol, metadata={"entry": entry, "sl": sl, "side": "BUY"})
                return reject(rej)
            min_tp = entry + risk_price * self._min_rr
            tp = max(tp, min_tp)
        else:
            entry = bid
            risk_price = sl - entry
            if risk_price <= 0:
                rej = RiskRejection(
                    reason=REJECT_ZERO_RISK_AT_ENTRY, pattern=signal.pattern, symbol=symbol,
                    metadata={"entry": entry, "sl": sl, "risk": risk_price, "side": "SELL", "bid": bid},
                )
                _log_rejection(rej)
                _record_rejection(REJECT_ZERO_RISK_AT_ENTRY, signal.pattern, symbol=symbol, metadata={"entry": entry, "sl": sl, "side": "SELL"})
                return reject(rej)
            max_tp = entry - risk_price * self._min_rr
            tp = min(tp, max_tp)

        # ─── VOLUME CALCULATION (mode-switchable) ─────────────────────
        sizing_mode = str(getattr(_cfg, "POSITION_SIZING_MODE", "FIXED")).upper()

        if sizing_mode == "DYNAMIC":
            order_type = mt5.ORDER_TYPE_BUY if signal.side is Side.BUY else mt5.ORDER_TYPE_SELL
            risk_pct = float(getattr(_cfg, "RISK_PER_TRADE_PERCENT", 1.0))
            volume = volume_for_risk(symbol, order_type, entry, sl, risk_pct)
            if volume is None or volume <= 0:
                rej = RiskRejection(
                    reason=REJECT_DYNAMIC_SIZING_FAILED, pattern=signal.pattern, symbol=symbol,
                    metadata={"entry": entry, "sl": sl, "risk_pct": risk_pct, "sizing_mode": "DYNAMIC"},
                )
                _log_rejection(rej)
                _record_rejection(REJECT_DYNAMIC_SIZING_FAILED, signal.pattern, symbol=symbol, metadata={"sizing_mode": "DYNAMIC", "risk_pct": risk_pct})
                return reject(rej)
        else:
            # FIXED mode (default)
            if self._fixed_lot <= 0:
                rej = RiskRejection(
                    reason=REJECT_FIXED_LOT_ZERO, pattern=signal.pattern, symbol=symbol,
                    metadata={"fixed_lot": self._fixed_lot},
                )
                _log_rejection(rej)
                _record_rejection(REJECT_FIXED_LOT_ZERO, signal.pattern, symbol=symbol, metadata={"fixed_lot": self._fixed_lot})
                return reject(rej)
            volume = float(self._fixed_lot)
        # ─── END VOLUME CALCULATION ───────────────────────────────────

        # ─── MINIMUM SL DISTANCE GUARD ────────────────────────────────
        # Rejects trades where the structure-based SL is below minimum
        # viable pip distance. Does NOT modify the SL — only rejects.
        # Adaptive mode: min = max(absolute_floor, ATR_noise, spread_min)
        try:
            _min_sl_enabled = bool(getattr(_cfg, "MIN_SL_GUARD_ENABLED", True))
        except Exception:
            _min_sl_enabled = True

        if _min_sl_enabled:
            _sl_distance_price = abs(entry - sl)
            _pip_size = 0.01 if "JPY" in symbol.upper() else 0.0001
            _sl_pips = _sl_distance_price / _pip_size

            _min_sl_required = _compute_adaptive_min_sl(symbol, candles, signal.bar_index, bid, ask)

            if _sl_pips < _min_sl_required:
                _reason = f"MIN_SL_DISTANCE_FAILED: {_sl_pips:.2f} pips < {_min_sl_required:.1f} required"
                rej = RiskRejection(
                    reason=_reason, pattern=signal.pattern, symbol=symbol,
                    metadata={
                        "calculated_sl_pips": round(_sl_pips, 2),
                        "required_min_sl_pips": round(_min_sl_required, 2),
                        "entry_price": entry,
                        "stop_loss_price": sl,
                        "sl_distance_price": round(_sl_distance_price, 8),
                        "adaptive": bool(getattr(_cfg, "ADAPTIVE_MIN_SL_ENABLED", True)),
                    },
                )
                _log_rejection(rej)
                _record_rejection("MIN_SL_DISTANCE_FAILED", signal.pattern, symbol=symbol, metadata={
                    "calculated_sl_pips": round(_sl_pips, 2),
                    "required_min_sl_pips": round(_min_sl_required, 2),
                })
                return reject(rej)
        # ─── END MINIMUM SL DISTANCE GUARD ────────────────────────────

        intent = OrderIntent(
            symbol=symbol,
            side=signal.side,
            volume=volume,
            entry_reference=entry,
            sl=sl,
            tp=tp,
            pattern=signal.pattern,
            metadata={"horizon": "SCALP"},
        )

        # Record acceptance metrics
        sl_distance = abs(entry - sl)
        rr = abs(tp - entry) / sl_distance if sl_distance > 0 else 0.0
        risk_metrics.record_accepted(pattern=signal.pattern, rr=rr, sl_distance=sl_distance)

        # ─── UNIFIED EVENT STREAM: RISK_CHECK (Layer 7) — APPROVED ────
        try:
            from core.event_stream import emit_risk_check
            emit_risk_check(symbol, {
                "result": "APPROVED",
                "guard": "risk_manager",
                "pattern": signal.pattern,
                "side": signal.side.value if signal.side else None,
                "entry": entry,
                "sl": sl,
                "tp": tp,
                "volume": volume,
                "rr": round(rr, 3),
                "sl_distance": round(sl_distance, 8),
                "sizing_mode": sizing_mode,
            }, source="risk_manager")
        except Exception:
            pass  # Event emission must never affect risk decisions
        # ─── END UNIFIED EVENT STREAM ─────────────────────────────────

        return accept(intent)
