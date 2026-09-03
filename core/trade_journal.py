"""
Trade Journal — Persistent record of every completed trade.

Append-only JSONL persistence. Crash-safe (fsync per write).
Provides realised P&L tracking, daily P&L reconstruction, and query capability.

Storage: logs/trade_journal/{date}.jsonl (one file per day, one trade per line)

This module is the single source of truth for:
- Historical trade outcomes
- Daily realised P&L (survives restart)
- Pattern/symbol profitability queries
- Prop firm audit trail
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from core.clock import utc_ms, utc_ms_to_iso, utc_ms_to_date, utc_ms_from_unix
from strategy.signals import Side

logger = logging.getLogger(__name__)

# ─── CONFIGURATION ───────────────────────────────────────────────────────────

_JOURNAL_DIR = "logs/trade_journal"
from core.config import NEW_RUNTIME_S3_BUCKET
from core.production_data_contract import s3_base_prefix
from core.production_data_contract import current_schema

_S3_BUCKET = NEW_RUNTIME_S3_BUCKET
_S3_PREFIX = s3_base_prefix("trade_journal")
_SCHEMA_VERSION = "trade_journal_v1"


def _get_journal_dir() -> Path:
    try:
        from core import config
        return Path(getattr(config, "TRADE_JOURNAL_DIR", _JOURNAL_DIR))
    except ImportError:
        return Path(_JOURNAL_DIR)


# ─── TRADE RECORD ─────────────────────────────────────────────────────────────

class CloseReason(str, Enum):
    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"
    TIME_EXIT = "time_exit"
    MANAGEMENT_EXIT = "management_exit"
    MANUAL_CLOSE = "manual_close"
    BROKER_CLOSE = "broker_close"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TradeRecord:
    """Immutable record of a completed trade."""

    # Identity
    trade_id: str
    position_ticket: int | None
    symbol: str
    magic: int

    # Strategy
    pattern_name: str
    direction: str  # "BUY" or "SELL"

    # Timing
    entry_time: float  # unix timestamp
    exit_time: float   # unix timestamp
    duration_seconds: float

    # Prices
    entry_price: float
    exit_price: float

    # Volume
    initial_volume: float
    final_volume: float

    # P&L (account currency). None = UNKNOWN (broker/runtime did not prove it).
    # A measured 0.0 is preserved as 0.0 and distinguished via *_status below.
    realised_pnl: float
    commission: float | None = None
    swap: float | None = None
    net_pnl: float | None = None  # realised_pnl + swap + commission (raw MT5 signs; None if any unknown)

    # Outcome provenance status: "unknown" | "measured_zero" | "measured_nonzero".
    commission_status: str = "unknown"
    swap_status: str = "unknown"
    pnl_status: str = "measured_nonzero"

    # Context
    close_reason: str = ""
    initial_sl: float = 0.0
    initial_tp: float = 0.0
    max_favourable_price: float = 0.0

    # Adverse excursion (observational). None = unknown (never fabricated).
    max_adverse_price: float | None = None
    # Excursion in R (null-aware): None when geometry/observation unavailable.
    mfe_r: float | None = None
    mae_r: float | None = None
    # Excursion provenance: full_lifecycle | recovery_seeded | unknown.
    excursion_provenance: str = "full_lifecycle"

    # Metadata
    recorded_at_utc: str = ""  # ISO format

    # Trade Identity (from Position.trade_identity — never from thread-local context)
    correlation_id: str = ""

    # Canonical lineage (remediation) — THE authoritative opportunity root,
    # carried frozen from Position.trade_identity.
    canonical_opportunity_id: str = ""

    # Full lineage identity (carried frozen from Position.trade_identity).
    # Propagated so the close projection preserves the exact original lineage
    # of the trade that opened the position — including across restart recovery.
    observation_id: str = ""
    decision_id: str = ""

    # Horizon Identity (from Position.trade_horizon — set at execution time)
    trade_horizon: str = "SCALP"


def _compute_pnl(
    side: Side,
    entry_price: float,
    exit_price: float,
    volume: float,
    pip_value_per_lot: float = 100_000.0,
    *,
    tick_size: float = 0.0,
    tick_value: float = 0.0,
    symbol: str = "",
) -> float:
    """
    Compute realised P&L.

    Priority:
        1. If tick_size and tick_value are provided → instrument-aware calculation
        2. Otherwise → legacy FX formula (volume × contract_size × price_move)

    Instrument-aware formula:
        ticks_moved = abs(exit - entry) / tick_size
        pnl = ticks_moved × tick_value × volume
        Apply direction sign.

    Legacy FX formula (fallback):
        pnl = (exit - entry) × volume × pip_value_per_lot
    """
    price_move = exit_price - entry_price

    # Instrument-aware path (when broker metadata available)
    if tick_size > 0 and tick_value > 0:
        ticks_moved = abs(price_move) / tick_size
        unsigned_pnl = ticks_moved * tick_value * volume
        if side is Side.BUY:
            return unsigned_pnl if price_move > 0 else -unsigned_pnl
        else:
            return unsigned_pnl if price_move < 0 else -unsigned_pnl

    # Legacy FX fallback (only correct for FX standard lots)
    if side is Side.BUY:
        return price_move * volume * pip_value_per_lot
    else:
        return -price_move * volume * pip_value_per_lot


def _cost_status(value: float | None) -> str:
    """Classify a realised cost value for research provenance.

    unknown        → value was not proven (None)
    measured_zero  → broker/runtime explicitly reported 0.0
    measured_nonzero → a real non-zero value
    """
    if value is None:
        return "unknown"
    return "measured_zero" if value == 0.0 else "measured_nonzero"


def _effective_pnl(record: "TradeRecord") -> float | None:
    """Operational P&L for aggregation.

    Prefers net_pnl (realised - costs). When net_pnl is unknown (commission/swap
    not proven), falls back to gross realised_pnl so operational daily-P&L math
    stays meaningful. Returns None only when neither is known. This does NOT
    weaken the persisted null semantics on the record fields themselves.
    """
    if record.net_pnl is not None:
        return record.net_pnl
    return record.realised_pnl


def _excursion_r(
    direction: str,
    entry_price: float,
    excursion_price: float | None,
    initial_stop_price: float,
    *,
    favourable: bool,
) -> float | None:
    """Excursion (favourable=MFE / adverse=MAE) in R, or None when unprovable.

    Uses the ORIGINAL initial stop distance for risk (never a later BE/trail
    stop). Returns None when the excursion observation is unknown or the initial
    risk geometry is invalid — NEVER a fake 0.0. A genuinely-observed trade that
    never moved (favourably/adversely) beyond entry yields a measured 0.0.
    """
    if excursion_price is None or entry_price is None or initial_stop_price is None:
        return None
    initial_risk_distance = abs(entry_price - initial_stop_price)
    if initial_risk_distance <= 0:
        return None
    is_buy = str(direction).upper() == "BUY"
    if favourable:
        move = (excursion_price - entry_price) if is_buy else (entry_price - excursion_price)
    else:
        move = (entry_price - excursion_price) if is_buy else (excursion_price - entry_price)
    return round(max(0.0, move) / initial_risk_distance, 4)


def build_trade_record(
    *,
    position,  # Position dataclass
    exit_price: float,
    exit_time: float,
    close_reason: str,
    commission: float | None = None,
    swap: float | None = None,
    realised_pnl_override: float | None = None,
) -> TradeRecord:
    """
    Build a TradeRecord from a closed Position.

    Args:
        position: The Position dataclass (from trade management).
                  If the Position carries a trade_identity, the correlation_id
                  is sourced from it (authoritative). Otherwise falls back to empty.
        exit_price: Price at which position was closed
        exit_time: Unix timestamp of close
        close_reason: Why the trade was closed (CloseReason value)
        commission: Broker commission, RAW MT5 sign (NEGATIVE = cost). None = UNKNOWN (not 0).
        swap: Swap/rollover amount, RAW MT5 sign. None = UNKNOWN (not 0).
        realised_pnl_override: If broker provides exact P&L, use it instead of calculating.

    NULL SEMANTICS: commission/swap are None when the broker/runtime did not
    prove the value (persisted as JSON null), and preserved numerically when a
    real value (including a measured 0.0) is supplied. net_pnl is only computed
    when realised_pnl AND both cost inputs are known — otherwise None.
    """
    duration = exit_time - position.open_time

    if realised_pnl_override is not None:
        realised_pnl = realised_pnl_override
        _pnl_source = "BROKER"
    else:
        realised_pnl = _compute_pnl(
            side=position.side,
            entry_price=position.entry_price,
            exit_price=exit_price,
            volume=position.volume,
        )
        _pnl_source = "CALCULATED"

    # net_pnl requires realised P&L AND both cost components to be known.
    # A missing commission/swap must NOT be treated as a measured zero.
    # SIGN CONVENTION (V1): raw MT5 signs are preserved end-to-end — commission
    # is NEGATIVE when it is a cost, swap is signed as the broker reports it.
    # Therefore net = gross + swap + commission (adding a negative commission
    # correctly reduces net). (Previously this subtracted commission while the
    # source value was already negative, double-counting the sign.)
    if commission is not None and swap is not None:
        net_pnl = realised_pnl + swap + commission
    else:
        net_pnl = None

    # Provenance status so research distinguishes unknown vs measured-zero vs
    # measured-nonzero without inferring from the numeric field alone.
    _commission_status = _cost_status(commission)
    _swap_status = _cost_status(swap)
    # realised P&L is always a proven value here (broker override or computed
    # from real entry/exit/volume) — never an unknown-as-zero.
    _pnl_status = "measured_zero" if realised_pnl == 0.0 else "measured_nonzero"

    # ─── PNL SOURCE LOGGING ───────────────────────────────────────
    _calc_pnl = _compute_pnl(
        side=position.side,
        entry_price=position.entry_price,
        exit_price=exit_price,
        volume=position.volume,
    )
    logger.info(
        "[PNL_AUDIT] symbol=%s trade_id=%s pnl_source=%s "
        "broker_profit=%s calculated_profit=%.4f final_profit=%.4f",
        position.symbol,
        position.position_id,
        _pnl_source,
        f"{realised_pnl_override:.4f}" if realised_pnl_override is not None else "N/A",
        _calc_pnl,
        realised_pnl,
    )
    # ─── END PNL SOURCE LOGGING ───────────────────────────────────

    # ─── EXCURSION IN R (observational; null-aware) ───────────────
    # Uses the ORIGINAL initial stop distance (never a later BE/trail stop).
    # Returns None when geometry/observation is unavailable — never fake 0.0.
    _direction = position.side.value if isinstance(position.side, Enum) else str(position.side)
    _max_adverse_price = getattr(position, "max_adverse_price", None)
    _mfe_r = _excursion_r(
        _direction, position.entry_price, position.max_favourable_price,
        position.initial_sl, favourable=True,
    )
    _mae_r = _excursion_r(
        _direction, position.entry_price, _max_adverse_price,
        position.initial_sl, favourable=False,
    )

    # Extract the full lineage from the Position's owned trade_identity
    # (authoritative source). Never falls back to thread-local context —
    # identity is owned by the Position and, on restart, is restored onto the
    # Position at reconstruction time (startup_recovery). All four lineage IDs
    # are carried through the close projection unchanged.
    _identity = getattr(position, "trade_identity", None)
    _cor_id = _identity.correlation_id if _identity is not None else ""
    _canonical_opp_id = _identity.canonical_opportunity_id if _identity is not None else ""
    _observation_id = _identity.observation_id if _identity is not None else ""
    _decision_id = _identity.decision_id if _identity is not None else ""

    return TradeRecord(
        trade_id=position.position_id,
        position_ticket=position.mt5_ticket,
        symbol=position.symbol,
        magic=position.magic,
        pattern_name=position.pattern_tag or "UNKNOWN",
        direction=position.side.value if isinstance(position.side, Enum) else str(position.side),
        entry_time=position.open_time,
        exit_time=exit_time,
        duration_seconds=round(duration, 1),
        entry_price=position.entry_price,
        exit_price=exit_price,
        initial_volume=getattr(position, "_meta", {}).get("initial_volume", position.volume),
        final_volume=position.volume,
        realised_pnl=round(realised_pnl, 4),
        commission=round(commission, 4) if commission is not None else None,
        swap=round(swap, 4) if swap is not None else None,
        net_pnl=round(net_pnl, 4) if net_pnl is not None else None,
        commission_status=_commission_status,
        swap_status=_swap_status,
        pnl_status=_pnl_status,
        close_reason=close_reason,
        initial_sl=position.initial_sl,
        initial_tp=position.initial_tp,
        max_favourable_price=position.max_favourable_price,
        max_adverse_price=_max_adverse_price,
        mfe_r=_mfe_r,
        mae_r=_mae_r,
        excursion_provenance=getattr(position, "excursion_provenance", "full_lifecycle"),
        recorded_at_utc=utc_ms_to_iso(utc_ms()),
        correlation_id=_cor_id,
        canonical_opportunity_id=_canonical_opp_id,
        observation_id=_observation_id,
        decision_id=_decision_id,
        trade_horizon=getattr(position, "trade_horizon", "SCALP"),
    )


# ─── PERSISTENCE (JSONL) ──────────────────────────────────────────────────────

def _record_to_dict(record: TradeRecord) -> dict[str, Any]:
    """Convert TradeRecord to JSON-safe dict."""
    d = asdict(record)
    d["schema_version"] = _SCHEMA_VERSION
    return d


def _date_from_timestamp(ts: float) -> str:
    """Extract date string (YYYY-MM-DD) from unix timestamp."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _write_s3_trade_journal(symbol: str, date_str: str, line: str) -> None:
    """
    Mirror trade journal record to S3. Fire-and-forget. Never raises.

    S3 Layout (Hive-compatible, Athena-queryable):
        trade_journal/schema_version=trade_journal_v1/symbol={SYMBOL}/date={DATE}/part-000.jsonl

    Partition keys:
        - schema_version: enables future schema evolution without breaking queries
        - symbol: enables per-pair analysis
        - date: enables time-range partition pruning
    """
    try:
        from core import config as _cfg
        if not getattr(_cfg, "EVENT_STREAM_S3_MIRROR", False):
            return

        import boto3
        from botocore.config import Config as BotoConfig
        s3 = boto3.client(
            "s3",
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=os.getenv("AWS_REGION", "eu-west-2"),
            config=BotoConfig(
                connect_timeout=3,
                read_timeout=5,
                retries={"max_attempts": 0},
            ),
        )
        key = (
            f"{_S3_PREFIX}/schema_version={_SCHEMA_VERSION}"
            f"/symbol={symbol}/date={date_str}/part-000.jsonl"
        )
        body = line  # line already has trailing newline

        # Read-append-write (acceptable for trade journal volume — max ~20 trades/day)
        try:
            existing = s3.get_object(Bucket=_S3_BUCKET, Key=key)
            body = existing["Body"].read().decode("utf-8") + body
        except Exception:
            pass  # New file

        s3.put_object(
            Bucket=_S3_BUCKET, Key=key,
            Body=body.encode("utf-8"),
            ContentType="application/x-ndjson",
        )
    except Exception:
        pass  # S3 failure must NEVER affect trade journal


def persist_trade(record: TradeRecord) -> bool:
    """
    Persist a single trade record to JSONL.

    Append-only, crash-safe (fsync after write).
    Never raises — returns False on failure.
    One file per day: logs/trade_journal/2026-06-04.jsonl
    """
    try:
        journal_dir = _get_journal_dir()
        journal_dir.mkdir(parents=True, exist_ok=True)

        date_str = _date_from_timestamp(record.exit_time)
        filepath = journal_dir / f"{date_str}.jsonl"

        line = json.dumps(_record_to_dict(record), separators=(",", ":")) + "\n"

        # Append with fsync for crash safety
        fd = os.open(str(filepath), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, line.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

        # ─── S3 MIRROR (Hive-partitioned, fire-and-forget) ───────────
        try:
            _write_s3_trade_journal(record.symbol, date_str, line)
        except Exception:
            pass  # S3 failure must NEVER affect trade journal persistence
        # ─── END S3 MIRROR ────────────────────────────────────────────

        # ─── UNIFIED EVENT STREAM: OUTCOME ────────────────────────────
        # backward compatibility — legacy journal write above preserved during migration
        try:
            from core.event_stream import emit_outcome
            _entry_ms = utc_ms_from_unix(record.entry_time)
            _exit_ms = utc_ms_from_unix(record.exit_time)
            _duration_ms = _exit_ms - _entry_ms

            # Compute realised RR (price-space, direction-signed). Uses realised_pnl
            # sign (always known) rather than net_pnl (which may be None when
            # commission/swap are unknown).
            _risk_dist = abs(record.entry_price - record.initial_sl)
            _rr_realised = 0.0
            if _risk_dist > 0:
                _pnl_pips = abs(record.exit_price - record.entry_price)
                _rr_realised = round(_pnl_pips / _risk_dist, 3)
                _signed = record.net_pnl if record.net_pnl is not None else record.realised_pnl
                if _signed is not None and _signed < 0:
                    _rr_realised = -_rr_realised

            # Authoritative MFE/MAE in R computed ONCE in build_trade_record.
            # Project them downstream (do not recompute) — null-aware.
            _mfe_r = record.mfe_r
            _mae_r = record.mae_r

            emit_outcome(record.symbol, {
                "trade_id": record.trade_id,
                "decision_id": getattr(record, "decision_id", ""),
                "decision_ts_utc_ms": _entry_ms,
                "execution_ts_utc_ms": _entry_ms,
                "execution_status": "FILLED",
                "order_ticket": getattr(record, "position_ticket", None),
                "deal": getattr(record, "position_ticket", None),
                "entry_price": record.entry_price,
                "exit_price": record.exit_price,
                "pnl": record.net_pnl,
                "final_r": (
                    record.net_pnl / abs(record.entry_price - record.initial_sl)
                    if (record.net_pnl is not None and abs(record.entry_price - record.initial_sl) > 0)
                    else None
                ),
                "rr_realised": _rr_realised,
                "duration_ms": _duration_ms,
                "exit_reason": record.close_reason,
                "mfe_r": _mfe_r,
                "mae_r": _mae_r,
                "breakeven_triggered": getattr(record, "breakeven_triggered", False),
                "trailing_triggered": getattr(record, "trailing_triggered", False),
                "pattern": record.pattern_name,
                "direction": record.direction,
                "volume": record.final_volume,
                "initial_sl": record.initial_sl,
                "initial_tp": record.initial_tp,
                # Risk deviation fields (Phase 1 hardening)
                "planned_risk_R": -1.0 if _risk_dist > 0 else 0.0,
                "actual_risk_R": round(
                    ((record.exit_price - record.entry_price) / _risk_dist if record.direction == "BUY"
                     else (record.entry_price - record.exit_price) / _risk_dist), 4
                ) if _risk_dist > 0 else 0.0,
                "risk_deviation": round(
                    abs((record.exit_price - record.entry_price) / _risk_dist if record.direction == "BUY"
                        else (record.entry_price - record.exit_price) / _risk_dist), 4
                ) if (_risk_dist > 0 and (record.net_pnl if record.net_pnl is not None else record.realised_pnl) < 0) else 0.0,
            }, source="trade_journal")
        except Exception:
            pass  # Event stream failure must never block journal persistence
        # ─── END UNIFIED EVENT STREAM ─────────────────────────────────

        # ─── TRADE TRUTH v3 WRITE (Pure Execution Reality) ─────────────
        # Persists ONLY execution data — no strategy, intent, or analysis.
        # trade_truth/ is the immutable record of what ACTUALLY happened.
        try:
            from core.trade_truth import build_trade_truth, persist_trade_truth, compute_r_multiple

            # Compute R-multiple from execution prices
            _risk_dist = abs(record.entry_price - record.initial_sl)
            _r_realised = compute_r_multiple(
                direction=record.direction,
                entry_price=record.entry_price,
                exit_price=record.exit_price,
                stop_loss=record.initial_sl,
            )

            # Map close_reason to valid exit_reason enum
            _exit_map = {
                "take_profit": "take_profit_hit",
                "stop_loss": "stop_loss_hit",
                "time_exit": "system_close",
                "management_exit": "system_close",
                "manual_close": "manual_close",
                "broker_close": "system_close",
                "stop_out": "margin_call",
                "expert_close": "system_close",
                "client_close": "manual_close",
                "mobile_close": "manual_close",
                "web_close": "manual_close",
            }
            _exit_reason = _exit_map.get(record.close_reason, "system_close")

            # Correlation ID sourced from TradeRecord (which got it from Position.trade_identity).
            # This is the authoritative, Position-owned identity — never from thread-local context.
            # On restart, startup_recovery restores the original lineage onto the
            # Position from execution_results, so this is the ORIGINAL correlation_id.
            # The synthetic RECOVERED-* id is a LAST RESORT used ONLY when the
            # original lineage genuinely could not be proven from persisted state
            # (so trade_truth validation still passes with a diagnosable record).
            _cor_id = record.correlation_id
            if not _cor_id:
                _cor_id = f"RECOVERED-{record.trade_id}"
                logger.warning(
                    "[TRADE_TRUTH] lineage_unrecoverable trade_id=%s ticket=%s "
                    "canonical=%s — original correlation_id absent from persisted "
                    "state; using synthetic fallback (NOT the original lineage)",
                    record.trade_id, record.position_ticket,
                    record.canonical_opportunity_id or "-",
                )

            _truth_record = build_trade_truth(
                trade_id=record.trade_id,
                correlation_id=_cor_id,
                canonical_opportunity_id=getattr(record, "canonical_opportunity_id", ""),
                symbol=record.symbol,
                entry_fill_price=record.entry_price,
                exit_fill_price=record.exit_price,
                volume_executed=record.final_volume,
                order_type="market",
                slippage_entry=None,
                slippage_exit=None,
                spread_at_entry=None,
                spread_at_exit=None,
                entry_timestamp_broker=record.entry_time,
                exit_timestamp_broker=record.exit_time,
                pnl_realised=record.realised_pnl,
                r_multiple_realised=_r_realised,
                commission=record.commission,
                swap=record.swap,
                net_profit=record.net_pnl,
                exit_reason=_exit_reason,
                # Excursion metrics — trade_truth is the authoritative research
                # owner. Computed once in build_trade_record; projected here.
                max_favourable_price=record.max_favourable_price,
                max_adverse_price=record.max_adverse_price,
                mfe_r=record.mfe_r,
                mae_r=record.mae_r,
                excursion_provenance=getattr(record, "excursion_provenance", "full_lifecycle"),
                field_provenance={
                    "entry_fill_price": "broker_position_lifecycle",
                    "exit_fill_price": "broker_deal_lifecycle",
                    "volume_executed": "broker_position_lifecycle",
                    "entry_timestamp_broker": "broker_position_lifecycle",
                    "exit_timestamp_broker": "broker_deal_lifecycle",
                    "pnl_realised": "broker_deal_lifecycle",
                    "r_multiple_realised": "calculated_from_broker_prices",
                    "commission": "broker_deal_lifecycle",
                    "swap": "broker_deal_lifecycle",
                    "net_profit": "calculated_from_broker_outcome",
                    "max_favourable_price": "runtime_excursion_tracker",
                    "max_adverse_price": "runtime_excursion_tracker",
                    "mfe_r": "calculated_from_initial_risk_geometry",
                    "mae_r": "calculated_from_initial_risk_geometry",
                },
            )

            persist_trade_truth(_truth_record)

        except Exception as _tt_exc:
            logger.warning("[TRADE_TRUTH] write_failed: %s", _tt_exc)
        # ─── END TRADE TRUTH v3 WRITE ─────────────────────────────────

        # ─── RISK DEVIATION TRACKING (Phase 1 hardening) ──────────────
        # Compute and persist risk deviation to identify execution/protection failures.
        # A normal loss has deviation ≈ 1.0. A -4.5R loss has deviation = 4.5.
        try:
            from core.risk_deviation import compute_risk_deviation, persist_risk_deviation
            _rd_result = compute_risk_deviation(
                trade_id=record.trade_id,
                symbol=record.symbol,
                correlation_id=record.correlation_id,
                direction=record.direction,
                entry_price=record.entry_price,
                exit_price=record.exit_price,
                initial_sl=record.initial_sl,
                # Phase 3 Step 5: risk deviation joins to the originating
                # opportunity via the Position-owned immutable identity.
                canonical_opportunity_id=getattr(record, "canonical_opportunity_id", ""),
            )
            persist_risk_deviation(_rd_result)
        except Exception:
            pass  # Risk deviation failure must never block journal persistence
        # ─── END RISK DEVIATION TRACKING ──────────────────────────────

        # ─── TRADE TRUTH GRAPH (relationship node) ────────────────────
        # RETIRED (Production V1 consolidation): the trade_truth_graph dataset
        # stored only reference pointers between layers (no execution data,
        # no outcome, no P&L). Full lineage is reconstructable via correlation_id
        # / canonical_opportunity_id joins across trade_truth + trade_journal +
        # decision_trace, so the separate graph projection was redundant and the
        # fan-out write has been removed. No trading behaviour change.
        # ─── END TRADE TRUTH GRAPH ────────────────────────────────────

        # ─── EDGE ATTRIBUTION (deferred — requires post-processing) ───
        # Edge Attribution needs market context from ENTRY time (not exit time).
        # It must JOIN: Trade Truth + Execution Context + Market Context.
        # This is computed by a separate batch process, not inline.
        # Log that attribution is pending for this trade.
        try:
            logger.info(
                "[EDGE_ATTRIBUTION_PENDING] trade_id=%s cor_id=%s symbol=%s r=%.4f — needs offline computation",
                record.trade_id, record.correlation_id or "", record.symbol,
                _r_realised if "_r_realised" in dir() else 0.0,
            )
        except Exception:
            pass
        # ─── END EDGE ATTRIBUTION ─────────────────────────────────────

        _log_pnl = record.net_pnl if record.net_pnl is not None else record.realised_pnl
        logger.info(
            "[TRADE_JOURNAL] PERSISTED trade_id=%s symbol=%s pnl=%s reason=%s",
            record.trade_id, record.symbol,
            f"{_log_pnl:.2f}" if _log_pnl is not None else "unknown",
            record.close_reason,
        )
        return True

    except Exception as exc:
        logger.error("[TRADE_JOURNAL] PERSIST_FAILED trade_id=%s error=%s", record.trade_id, exc)
        return False


# ─── DEDUPLICATION ────────────────────────────────────────────────────────────

_persisted_ids: set[str] = set()


def is_already_journaled(trade_id: str) -> bool:
    """Check if trade has already been persisted (in-memory dedup)."""
    return trade_id in _persisted_ids


def mark_journaled(trade_id: str) -> None:
    """Mark trade as persisted (call after successful persist)."""
    _persisted_ids.add(trade_id)


def persist_trade_once(record: TradeRecord) -> bool:
    """
    Persist trade if not already journaled. Idempotent.
    Returns True if persisted (or already existed), False on write failure.
    """
    if is_already_journaled(record.trade_id):
        return True
    if persist_trade(record):
        mark_journaled(record.trade_id)
        return True
    return False


# ─── QUERY / READ ─────────────────────────────────────────────────────────────

def _load_journal_file(filepath: Path) -> list[TradeRecord]:
    """Load all trade records from a single JSONL file."""
    records: list[TradeRecord] = []
    if not filepath.exists():
        return records
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    data.pop("schema_version", None)  # Not a TradeRecord field
                    records.append(TradeRecord(**data))
                except (json.JSONDecodeError, TypeError, KeyError):
                    continue
    except Exception as exc:
        logger.warning("[TRADE_JOURNAL] READ_ERROR file=%s error=%s", filepath, exc)
    return records


def get_trades_by_date(target_date: date | str) -> list[TradeRecord]:
    """Load all trades for a specific date."""
    if isinstance(target_date, date):
        date_str = target_date.strftime("%Y-%m-%d")
    else:
        date_str = str(target_date)
    filepath = _get_journal_dir() / f"{date_str}.jsonl"
    return _load_journal_file(filepath)


def get_trades_today() -> list[TradeRecord]:
    """Load all trades for today (UTC)."""
    today = utc_ms_to_date(utc_ms())
    return get_trades_by_date(today)


def get_trade(trade_id: str) -> TradeRecord | None:
    """Find a specific trade by ID. Searches recent files (last 7 days)."""
    journal_dir = _get_journal_dir()
    if not journal_dir.exists():
        return None
    # Search recent files (most recent first)
    files = sorted(journal_dir.glob("*.jsonl"), reverse=True)
    for filepath in files[:7]:  # Last 7 days
        for record in _load_journal_file(filepath):
            if record.trade_id == trade_id:
                return record
    return None


def get_trades_by_symbol(symbol: str, days: int = 30) -> list[TradeRecord]:
    """Get all trades for a symbol within the last N days."""
    journal_dir = _get_journal_dir()
    if not journal_dir.exists():
        return []
    files = sorted(journal_dir.glob("*.jsonl"), reverse=True)
    results: list[TradeRecord] = []
    for filepath in files[:days]:
        for record in _load_journal_file(filepath):
            if record.symbol == symbol:
                results.append(record)
    return results


def get_trades_by_pattern(pattern: str, days: int = 30) -> list[TradeRecord]:
    """Get all trades for a pattern within the last N days."""
    journal_dir = _get_journal_dir()
    if not journal_dir.exists():
        return []
    files = sorted(journal_dir.glob("*.jsonl"), reverse=True)
    results: list[TradeRecord] = []
    for filepath in files[:days]:
        for record in _load_journal_file(filepath):
            if record.pattern_name == pattern:
                results.append(record)
    return results


def get_recent_trades(limit: int = 50) -> list[TradeRecord]:
    """Get the most recent N trades across all days."""
    journal_dir = _get_journal_dir()
    if not journal_dir.exists():
        return []
    files = sorted(journal_dir.glob("*.jsonl"), reverse=True)
    results: list[TradeRecord] = []
    for filepath in files:
        records = _load_journal_file(filepath)
        results.extend(records)
        if len(results) >= limit:
            break
    # Sort by exit_time descending, take limit
    results.sort(key=lambda r: r.exit_time, reverse=True)
    return results[:limit]


# ─── DAILY P&L ────────────────────────────────────────────────────────────────

def get_daily_realised_pnl(target_date: date | str | None = None) -> float:
    """
    Sum of net_pnl for all closed trades on the specified day.
    If target_date is None, uses today (UTC).
    Reconstructed from persisted journal — survives restart.
    """
    if target_date is None:
        target_date = utc_ms_to_date(utc_ms())
    trades = get_trades_by_date(target_date)
    return round(sum((_effective_pnl(t) or 0.0) for t in trades), 4)


def get_daily_trade_count(target_date: date | str | None = None) -> int:
    """Count of closed trades on the specified day."""
    if target_date is None:
        target_date = utc_ms_to_date(utc_ms())
    return len(get_trades_by_date(target_date))


def get_current_daily_pnl(unrealised_pnl: float = 0.0) -> float:
    """
    Today's total P&L including open trade unrealised P&L.

    Args:
        unrealised_pnl: Sum of unrealised P&L from all open positions.

    Returns:
        daily_realised_pnl + unrealised_pnl
    """
    realised = get_daily_realised_pnl()
    return round(realised + unrealised_pnl, 4)


# ─── PERFORMANCE SUMMARY ─────────────────────────────────────────────────────

def get_daily_summary(target_date: date | str | None = None) -> dict[str, Any]:
    """
    Summary statistics for a given day.
    Returns dict with: trades, wins, losses, net_pnl, win_rate, avg_pnl.
    """
    if target_date is None:
        target_date = utc_ms_to_date(utc_ms())
    trades = get_trades_by_date(target_date)
    if not trades:
        return {
            "date": str(target_date),
            "trades": 0, "wins": 0, "losses": 0,
            "net_pnl": 0.0, "win_rate": 0.0, "avg_pnl": 0.0,
        }

    wins = [t for t in trades if (_effective_pnl(t) or 0.0) > 0]
    losses = [t for t in trades if (_effective_pnl(t) or 0.0) <= 0]
    total_pnl = sum((_effective_pnl(t) or 0.0) for t in trades)
    avg_pnl = total_pnl / len(trades)

    return {
        "date": str(target_date),
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "net_pnl": round(total_pnl, 2),
        "win_rate": round(len(wins) / len(trades), 4) if trades else 0.0,
        "avg_pnl": round(avg_pnl, 2),
    }


# ─── STARTUP RELOAD ──────────────────────────────────────────────────────────

def reload_persisted_ids() -> int:
    """
    Reload trade IDs from today's journal into dedup set (call on startup).
    Returns count of IDs loaded.
    """
    trades = get_trades_today()
    count = 0
    for t in trades:
        _persisted_ids.add(t.trade_id)
        count += 1
    if count:
        logger.info("[TRADE_JOURNAL] RELOAD dedup_ids_loaded=%d", count)
    return count
