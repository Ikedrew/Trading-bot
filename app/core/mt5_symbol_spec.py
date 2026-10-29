"""Broker symbol constraints used only at the MT5 boundary."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


@dataclass(frozen=True)
class MT5SymbolSpec:
    name: str
    point: float
    digits: int
    volume_min: float
    volume_max: float
    volume_step: float
    stops_level: int
    freeze_level: int
    filling_mode: int
    trade_mode: int
    contract_size: float

    @classmethod
    def from_info(cls, name: str, info: Any) -> "MT5SymbolSpec":
        def number(field: str, default: int | float) -> int | float:
            value = getattr(info, field, default)
            return value if isinstance(value, (int, float)) else default

        return cls(
            name=name,
            point=float(number("point", 0.0)),
            digits=int(number("digits", 0)),
            volume_min=float(number("volume_min", 0.0)),
            volume_max=float(number("volume_max", 0.0)),
            volume_step=float(number("volume_step", 0.0)),
            stops_level=int(number("trade_stops_level", 0)),
            freeze_level=int(number("trade_freeze_level", 0)),
            filling_mode=int(number("filling_mode", 0)),
            trade_mode=int(number("trade_mode", 0)),
            contract_size=float(number("trade_contract_size", 0.0)),
        )

    def normalize_price(self, price: float) -> float:
        quantum = Decimal(1).scaleb(-self.digits)
        return float(Decimal(str(price)).quantize(quantum, rounding=ROUND_HALF_UP))

    @property
    def minimum_stop_distance(self) -> float:
        return self.stops_level * self.point


def validate_volume(spec: MT5SymbolSpec, volume: float) -> str | None:
    value = Decimal(str(volume))
    minimum = Decimal(str(spec.volume_min))
    maximum = Decimal(str(spec.volume_max))
    step = Decimal(str(spec.volume_step))
    if value < minimum:
        return "VOLUME_BELOW_MIN"
    if value > maximum:
        return "VOLUME_ABOVE_MAX"
    if step <= 0:
        return "INVALID_VOLUME_STEP"
    # MT5 volume grids are anchored at zero.
    if value % step != 0:
        return "INVALID_VOLUME_STEP"
    return None


def validate_stops(
    spec: MT5SymbolSpec, *, market_price: float, sl: float, tp: float,
    include_freeze: bool = False,
) -> str | None:
    level = max(spec.stops_level, spec.freeze_level if include_freeze else 0)
    minimum = level * spec.point
    if minimum <= 0:
        return None
    tolerance = max(spec.point * 1e-6, 1e-12)
    if sl > 0 and abs(market_price - sl) + tolerance < minimum:
        return "SL_TOO_CLOSE"
    if tp > 0 and abs(market_price - tp) + tolerance < minimum:
        return "TP_TOO_CLOSE"
    return None


# ─── CANONICAL BROKER FILLING-MODE NEGOTIATION (shared) ──────────────────────
# MQL5 SYMBOL_FILLING_* bitmask (not always exposed on the Python mt5 module).
# This is the ONE interpretation of broker filling support shared between the
# legacy execution boundary (execution.mt5_execution) and the fan-out execution
# worker (core.accounts.execution_worker). Never hardcode a mode.
FILLING_FOK = 1
FILLING_IOC = 2
FILLING_RETURN = 4


def select_filling_mode(filling_mode) -> str | None:
    """Broker-aware filling negotiation from ``symbol_info.filling_mode``.

    Preference matches the existing canonical execution implementation
    (IOC → FOK → RETURN). Returns the semantic mode name, or ``None`` when the
    broker reports no supported market filling mode — the caller must then fail
    closed with an explicit reason instead of defaulting to IOC.
    """
    try:
        mask = int(filling_mode)
    except (TypeError, ValueError):
        return None
    if mask & FILLING_IOC:
        return "IOC"
    if mask & FILLING_FOK:
        return "FOK"
    if mask & FILLING_RETURN:
        return "RETURN"
    return None


def filling_mode_constant(mode: str, mt5_module: Any) -> int:
    """Map a semantic mode from :func:`select_filling_mode` onto the runtime
    MT5 module's ORDER_FILLING_* constant. Raises for an unknown mode (fail
    closed — callers must negotiate before reaching this)."""
    names = {"IOC": "ORDER_FILLING_IOC", "FOK": "ORDER_FILLING_FOK",
             "RETURN": "ORDER_FILLING_RETURN"}
    return int(getattr(mt5_module, names[mode]))


def validate_trade_mode(spec: "MT5SymbolSpec", side: str) -> str | None:
    """Destination-broker trade-mode gate for NEW market entries.

    MT5 SYMBOL_TRADE_MODE: 0=disabled, 1=longonly, 2=shortonly,
    3=closeonly, 4=full. Same semantics as the read-side broker preflight
    (core.accounts.eligibility.evaluate): only full mode, or the side's
    one-way mode, may open a position. Mode 0 (e.g. MetaQuotes USTEC/US500)
    is reported explicitly so disabled instruments clean-skip before
    order_send.
    """
    if side not in ("BUY", "SELL"):
        return "INVALID_SIDE"
    if spec.trade_mode == 0:
        return "SYMBOL_TRADE_MODE_DISABLED"
    if spec.trade_mode not in (4, 1 if side == "BUY" else 2):
        return "SYMBOL_TRADE_MODE_BLOCKED"
    return None
