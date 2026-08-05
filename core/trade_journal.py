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
_S3_BUCKET = "v10-engine"
_S3_PREFIX = "trade_journal"
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

    # P&L (account currency)
    realised_pnl: float
    commission: float
    swap: float
    net_pnl: float  # realised_pnl + swap - commission

    # Context
    close_reason: str
    initial_sl: float
    initial_tp: float
    max_favourable_price: float

    # Metadata
    recorded_at_utc: str  # ISO format

    # Trade Identity (from Position.trade_identity — never from thread-local context)
    correlation_id: str = ""

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


def build_trade_record(
    *,
    position,  # Position dataclass
    exit_price: float,
    exit_time: float,
    close_reason: str,
    commission: float = 0.0,
    swap: float = 0.0,
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
        commission: Broker commission (positive = cost)
        swap: Swap/rollover amount (positive = credit, negative = cost)
        realised_pnl_override: If broker provides exact P&L, use it instead of calculating
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

    net_pnl = realised_pnl + swap - commission

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

    # Extract correlation_id from Position's owned trade_identity (authoritative source).
    # Never falls back to thread-local context — identity is owned by the Position.
    _identity = getattr(position, "trade_identity", None)
    _cor_id = _identity.correlation_id if _identity is not None else ""

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
        commission=round(commission, 4),
        swap=round(swap, 4),
        net_pnl=round(net_pnl, 4),
        close_reason=close_reason,
        initial_sl=position.initial_sl,
        initial_tp=position.initial_tp,
        max_favourable_price=position.max_favourable_price,
        recorded_at_utc=utc_ms_to_iso(utc_ms()),
        correlation_id=_cor_id,
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

            # Compute realised RR
            _risk_dist = abs(record.entry_price - record.initial_sl)
            _rr_realised = 0.0
            if _risk_dist > 0:
                _pnl_pips = abs(record.exit_price - record.entry_price)
                _rr_realised = round(_pnl_pips / _risk_dist, 3)
                if record.net_pnl < 0:
                    _rr_realised = -_rr_realised

            # Compute MFE/MAE in R-multiples
            _mfe_r = 0.0
            _mae_r = 0.0
            if _risk_dist > 0:
                _mfe_dist = abs(record.max_favourable_price - record.entry_price)
                _mfe_r = round(_mfe_dist / _risk_dist, 3)

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
                "final_r": record.net_pnl / abs(record.entry_price - record.initial_sl) if abs(record.entry_price - record.initial_sl) > 0 else 0.0,
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
                ) if _risk_dist > 0 and record.net_pnl < 0 else 0.0,
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
            # If identity was not restored during recovery, generate a synthetic ID
            # so trade_truth validation passes (better partial record than none).
            _cor_id = record.correlation_id
            if not _cor_id:
                _cor_id = f"RECOVERED-{record.trade_id}"

            _truth_record = build_trade_truth(
                trade_id=record.trade_id,
                correlation_id=_cor_id,
                symbol=record.symbol,
                entry_fill_price=record.entry_price,
                exit_fill_price=record.exit_price,
                volume_executed=record.final_volume,
                order_type="market",
                slippage_entry=0.0,
                slippage_exit=0.0,
                spread_at_entry=0.0,
                spread_at_exit=0.0,
                entry_timestamp_broker=record.entry_time,
                exit_timestamp_broker=record.exit_time,
                pnl_realised=record.realised_pnl,
                r_multiple_realised=_r_realised,
                commission=record.commission,
                swap=record.swap,
                net_profit=record.net_pnl,
                exit_reason=_exit_reason,
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
            )
            persist_risk_deviation(_rd_result)
        except Exception:
            pass  # Risk deviation failure must never block journal persistence
        # ─── END RISK DEVIATION TRACKING ──────────────────────────────

        # ─── TRADE TRUTH GRAPH (relationship node) ────────────────────
        # Build a graph node linking this trade to its source datasets.
        # Pure references only — no execution data, no P&L.
        try:
            from core.trade_truth_graph import build_graph_node, persist_graph_node
            from datetime import datetime, timezone as _tz

            _graph_date = datetime.fromtimestamp(record.exit_time, tz=_tz.utc).strftime("%Y-%m-%d")
            _graph_node = build_graph_node(
                trade_id=record.trade_id,
                correlation_id=record.correlation_id or f"RECOVERED-{record.trade_id}",
                symbol=record.symbol,
                cycle_id=0,  # Not available at trade close time
                event_window_start_ts=record.entry_time,
                event_window_end_ts=record.exit_time,
                decision_to_execution_lag_ms=0.0,
                execution_to_exit_lag_ms=(record.exit_time - record.entry_time) * 1000,
                trade_truth_ref=f"s3://v10-engine/trades/schema_version=trade_truth_v3/symbol={record.symbol}/date={_graph_date}/part-000.jsonl",
                execution_context_ref=record.correlation_id or "",
            )
            persist_graph_node(_graph_node)
        except Exception:
            pass  # Graph node failure must never block journal persistence
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

        logger.info(
            "[TRADE_JOURNAL] PERSISTED trade_id=%s symbol=%s pnl=%.2f reason=%s",
            record.trade_id, record.symbol, record.net_pnl, record.close_reason,
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
    return round(sum(t.net_pnl for t in trades), 4)


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

    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl <= 0]
    total_pnl = sum(t.net_pnl for t in trades)
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
