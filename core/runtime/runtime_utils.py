from __future__ import annotations

from core import config
from core.constants.timeframes import TIMEFRAME_SECONDS
from core.trade_management import TradeManagementConfig
from data.mt5_data import Candle
from risk.manager import RiskManager

# ─── UTILITIES ────────────────────────────────────────────────────────────────


def _timeframe_seconds(timeframe: int) -> int:
    """Convert MT5 timeframe constant to seconds. Pure lookup, no MT5 dependency."""
    return TIMEFRAME_SECONDS.get(timeframe, 60)


def _closed_bar_index(candles: list[Candle]) -> int | None:
    """
    Return the index of the last CLOSED bar in the candle array.

    MT5 copy_rates_from_pos(symbol, timeframe, 0, count) returns bars including
    the current forming bar as the last element — BUT only when the market is
    actively ticking into that bar. During low-activity periods or at market open,
    MT5 may return only fully closed bars.

    Strategy: Use the LAST bar in the array. The engine's pattern detection and
    scoring already handle the case where the last bar is still forming (via
    candle body quality checks). The bar dedup (last_closed_time) ensures we
    don't re-process the same bar twice regardless.
    """
    if len(candles) < 2:
        return None
    return len(candles) - 1


def _build_trade_management_config() -> TradeManagementConfig:
    return TradeManagementConfig(
        break_even_trigger_rr=float(getattr(config, "TM_BREAK_EVEN_TRIGGER_RR", 0.0)),
        break_even_buffer_rr=float(getattr(config, "TM_BREAK_EVEN_BUFFER_RR", 0.0)),
        trailing_step=float(getattr(config, "TM_TRAILING_STEP", 0.0)),
        trailing_start_rr=float(getattr(config, "TM_TRAILING_START_RR", 0.0)),
        partial_tp_fraction=float(getattr(config, "TM_PARTIAL_TP_FRACTION", 0.0)),
        partial_tp_path_fraction=float(getattr(config, "TM_PARTIAL_TP_PATH_FRACTION", 0.0)),
        max_time_in_trade_seconds=float(getattr(config, "TM_MAX_TIME_IN_TRADE_SECONDS", 0.0)),
    )


def _build_risk_manager() -> RiskManager:
    return RiskManager(
        fixed_lot=config.FIXED_LOT,
        base_rr=config.BASE_RR,
        rr3_patterns=config.RR3_PATTERNS,
        sl_buffer=config.SL_BUFFER,
        min_rr=config.MIN_RR,
    )


# ─── REPLAY TIME WINDOW ──────────────────────────────────────────────────────

def _apply_replay_window(candles: list[Candle], min_required: int) -> tuple[int, int]:
    """
    Compute effective start_i and end_i for replay based on optional time window config.

    Returns (start_i, end_i) where:
      - start_i: first bar to evaluate (respects both warmup and REPLAY_START_TIME)
      - end_i: exclusive upper bound (respects REPLAY_END_TIME or defaults to len(candles))

    Config keys (optional, unix timestamps):
      - REPLAY_START_TIME: earliest bar time to begin evaluation
      - REPLAY_END_TIME: latest bar time to include in evaluation

    If neither is set, returns (min_required, len(candles)) — full replay.
    """
    start_time = getattr(config, "REPLAY_START_TIME", None)
    end_time = getattr(config, "REPLAY_END_TIME", None)

    start_i = min_required
    end_i = len(candles)

    if start_time is not None:
        start_time = int(start_time)
        # Find first bar at or after start_time, but never before min_required
        for i in range(min_required, len(candles)):
            if candles[i].time >= start_time:
                start_i = i
                break
        else:
            # All bars are before start_time — nothing to replay
            start_i = len(candles)

    if end_time is not None:
        end_time = int(end_time)
        # Find first bar after end_time
        for i in range(start_i, len(candles)):
            if candles[i].time > end_time:
                end_i = i
                break

    return start_i, end_i