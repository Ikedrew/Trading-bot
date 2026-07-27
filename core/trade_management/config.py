"""Trade management configuration — price/time/risk only; no entry or strategy keys."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TradeManagementConfig:
    """
    All optional; zero values disable a feature.
    R (one risk unit) = |entry − initial_sl| in price.
    """

    #: Move SL to break-even when unrealised profit >= this multiple of R (0 = disabled).
    break_even_trigger_rr: float = 0.0
    #: Buffer beyond entry in R-fraction units when moving to BE (0.1 = 0.1R beyond entry).
    break_even_buffer_rr: float = 0.0

    #: Trail SL by keeping it `trailing_step` price units behind best favourable price (0 = disabled).
    trailing_step: float = 0.0
    #: Only start trailing after profit >= this multiple of R (0 = use trailing_step immediately when enabled).
    trailing_start_rr: float = 0.0

    #: Close this fraction of volume at TP1 when price reaches this fraction of the way to TP (0 = disabled).
    partial_tp_fraction: float = 0.0
    partial_tp_path_fraction: float = 0.0

    #: Force flatten if position age exceeds this many seconds (0 = disabled).
    max_time_in_trade_seconds: float = 0.0
