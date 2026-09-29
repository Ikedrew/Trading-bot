"""
NEW Shadow Runtime — Domain models and event contracts.

Implements the approved Shadow Runtime contract:
    canonical_opportunity_id
        └── shadow_trade_id (/SCALP | /INTRADAY | /EXTENDED)

Event stream: PLAN → OPEN → PROGRESS* → CLOSE (append-only, single writer).

Identity rules:
    - canonical_opportunity_id is INHERITED, copied verbatim onto every event.
    - It is NEVER regenerated here and timestamps NEVER replace lineage joins.
    - shadow_trade_id is the only ID minted by this domain.

There is NO shadow decision stage. Live V10 facts are inherited observations,
namespaced under ``live_facts``, never presented as Shadow decisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# ═══════════════════════════════════════════════════════════════════════════════
# VERSIONS (three orthogonal dimensions — see contract §22)
# ═══════════════════════════════════════════════════════════════════════════════

SCHEMA_VERSION = "shadow_runtime_v1"
"""Record structure version of THIS dataset."""

CONSTRUCTION_MODEL_VERSION = "construction_v1"
"""Geometry/provenance rules: SL sources, buffers, RR targets, TP construction."""

SIMULATION_MODEL_VERSION = "simulation_v1"
"""Lifecycle policy: fill model, ordering, costs, timeout policy, pip convention."""

MARKET_TIMESTAMP_SEMANTICS = "canonical_utc_bar_open_v1"
"""Canonical meaning of market timestamps written by the current producer."""

MARKET_TIMESTAMP_NORMALIZATION_VERSION = "mt5_broker_to_utc_once_v1"
"""The MT5 feed normalises broker/server bar time exactly once before shadow use."""

# ─── Market-time constants ────────────────────────────────────────────────────

M5_BAR_INTERVAL_S = 300
"""Authoritative closed-bar interval (seconds) for gap detection."""

HORIZONS = ("SCALP", "INTRADAY", "EXTENDED")

TIMEOUT_BARS = {
    "SCALP": 9,      # profile expected_hold_minutes_max = 45 min
    "INTRADAY": 96,  # 480 min
    "EXTENDED": 864, # 4320 min (3 days)
}

EVENT_TYPES = ("PLAN", "OPEN", "PROGRESS", "CLOSE")

# Horizon-plan states (contract §6)
PLAN_NOT_ELIGIBLE = "NOT_ELIGIBLE"
PLAN_UNCONSTRUCTIBLE = "ELIGIBLE_BUT_UNCONSTRUCTIBLE"
PLAN_CONSTRUCTED = "CONSTRUCTED"
PLAN_SIMULATED = "SIMULATED"
PLAN_CLOSED = "CLOSED"

# Exit reasons
EXIT_STOP_LOSS = "stop_loss"
EXIT_TAKE_PROFIT = "take_profit"
EXIT_TIMEOUT = "timeout"


def utc_derived(raw_market_time: float, broker_offset_seconds: int) -> dict[str, Any]:
    """
    Derive the UTC representation of a raw broker-server epoch-second market time.

        utc = raw_market_time - broker_offset_seconds

    The raw value is NEVER modified; this only produces companion representations
    so the conversion stays reproducible from the persisted provenance.
    """
    utc_epoch = int(raw_market_time) - int(broker_offset_seconds)
    iso = datetime.fromtimestamp(utc_epoch, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    return {"utc_epoch_s": utc_epoch, "utc_iso8601": iso}


def market_block(field_prefix: str, raw_market_time: float, broker_offset_seconds: int) -> dict[str, Any]:
    """Standard market-time block for one named timestamp (contract §13)."""
    derived = utc_derived(raw_market_time, broker_offset_seconds)
    return {
        field_prefix: int(raw_market_time),
        f"{field_prefix}_utc_epoch_s": derived["utc_epoch_s"],
        f"{field_prefix}_utc_iso8601": derived["utc_iso8601"],
    }


def utc_market_block(field_prefix: str, market_time_utc: float) -> dict[str, Any]:
    """Persist a canonical UTC bar timestamp without applying another offset.

    ``data.mt5_data`` owns the broker/server-to-UTC conversion.  Values passed
    into the current shadow integration are therefore already UTC.  The
    legacy-named base field is retained for schema compatibility, but both it
    and its explicit UTC companion carry the same canonical epoch.
    """
    utc_epoch = int(market_time_utc)
    iso = datetime.fromtimestamp(utc_epoch, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    return {
        field_prefix: utc_epoch,
        f"{field_prefix}_utc_epoch_s": utc_epoch,
        f"{field_prefix}_utc_iso8601": iso,
    }


@dataclass
class LifecycleState:
    """Forward-only simulation lifecycle state (mutable, checkpointed)."""

    bars_elapsed: int = 0
    max_favourable_price: float = 0.0
    max_adverse_price: float = 0.0
    last_evaluated_bar_time: int = 0
    state_log: list[dict[str, float]] = field(default_factory=list)
    data_gaps: list[dict[str, Any]] = field(default_factory=list)
    # ROOT-04: ordered M5 OHLC bars this lifecycle actually evaluated.  Kept
    # on the lifecycle (not only on CLOSE) so a PROGRESS checkpoint can carry
    # it and a crash cannot lose the path already observed.
    m5_path: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "bars_elapsed": self.bars_elapsed,
            "max_favourable_price": self.max_favourable_price,
            "max_adverse_price": self.max_adverse_price,
            "last_evaluated_bar_time": self.last_evaluated_bar_time,
            "state_log_tail": self.state_log[-10:],
            "state_log_len": len(self.state_log),
            "data_gaps": list(self.data_gaps),
            # The full M5 path is checkpointed verbatim: truncating it would
            # silently destroy the evidence ROOT-04 exists to provide.
            "m5_path": [dict(row) for row in self.m5_path],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LifecycleState":
        return cls(
            bars_elapsed=int(d.get("bars_elapsed", 0)),
            max_favourable_price=float(d.get("max_favourable_price", 0.0)),
            max_adverse_price=float(d.get("max_adverse_price", 0.0)),
            last_evaluated_bar_time=int(d.get("last_evaluated_bar_time", 0)),
            state_log=list(d.get("state_log_tail", [])),
            data_gaps=list(d.get("data_gaps", [])),
            m5_path=[dict(row) for row in (d.get("m5_path") or [])],
        )
