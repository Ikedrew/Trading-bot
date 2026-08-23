"""
Shadow Trade Execution Layer — Simulates full trade lifecycle from live signals.

Generates synthetic trades WITHOUT broker execution. Computes canonical R-multiples
and persists Trade Truth v2 records to S3 for profitability analysis.

S3 path: s3://trading-bot-data-mk1/shadow_trades/{symbol}/{YYYY-MM-DD}.jsonl

Rules:
    - Never triggers broker execution
    - Never interferes with live engine
    - Uses ONLY closed-bar data for exit evaluation
    - Uses HTF snapshot read-only
    - Deterministic: same inputs → same outputs

Usage:
    from core.shadow_trades import ShadowTradeEngine

    engine = ShadowTradeEngine()
    engine.open_trade(signal, cycle_id, htf_snapshot)
    engine.evaluate_bar(candle_high, candle_low, candle_close, bar_time)
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.trade_truth import compute_r_multiple, compute_mfe_r, compute_mae_r

logger = logging.getLogger(__name__)

_S3_BUCKET = "v10-engine"
_S3_PREFIX = "shadow_trades"
_LOCAL_DIR = "logs/shadow_trades"
_MAX_BARS_DEFAULT = 60  # 5h at M5


# ═══════════════════════════════════════════════════════════════════════════════
# DEEP FREEZE UTILITY
# ═══════════════════════════════════════════════════════════════════════════════

def _deep_freeze(obj: Any) -> Any:
    """
    Recursively convert mutable structures into immutable equivalents.

    dict → MappingProxyType (read-only view)
    list → tuple
    set  → frozenset
    nested structures → recursively frozen

    Primitives (str, int, float, bool, None) pass through unchanged.
    """
    from types import MappingProxyType

    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return MappingProxyType({k: _deep_freeze(v) for k, v in obj.items()})
    if isinstance(obj, (list, tuple)):
        return tuple(_deep_freeze(item) for item in obj)
    if isinstance(obj, set):
        return frozenset(_deep_freeze(item) for item in obj)
    if isinstance(obj, frozenset):
        return obj
    # MappingProxyType already frozen
    from types import MappingProxyType as _MPT
    if isinstance(obj, _MPT):
        return obj
    # Unknown type — return as-is (primitives, enums, etc.)
    return obj


def _unfreeze(obj: Any) -> Any:
    """
    Recursively convert frozen structures back to plain dicts/lists for JSON serialisation.

    MappingProxyType → dict
    tuple → list
    frozenset → list
    """
    from types import MappingProxyType

    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, MappingProxyType):
        return {k: _unfreeze(v) for k, v in obj.items()}
    if isinstance(obj, dict):
        return {k: _unfreeze(v) for k, v in obj.items()}
    if isinstance(obj, (tuple, list)):
        return [_unfreeze(item) for item in obj]
    if isinstance(obj, frozenset):
        return [_unfreeze(item) for item in obj]
    return obj


# ═══════════════════════════════════════════════════════════════════════════════
# SHADOW TRADE DATA MODEL
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ShadowTrade:
    """
    Active shadow trade being tracked through lifecycle.

    IMMUTABILITY CONTRACT (OPTION B — STRICT MODE):
        Decision-time snapshot fields are STRUCTURALLY IMMUTABLE at the type level.
        - All dicts are frozen (MappingProxyType)
        - All lists in snapshots are tuples
        - No mutable nested structure exists after __post_init__
        - No shared reference to upstream engine objects

        Lifecycle simulation state is MUTABLE but FORWARD-ONLY.
        - Only progresses via evaluate_bar()
        - Never modifies snapshot fields
        - _state_log is append-only (mutable list by design — lifecycle data)
    """

    # ─── DECISION-TIME SNAPSHOT (STRUCTURALLY IMMUTABLE) ──────────────
    # Frozen at creation via __post_init__ deep freeze.
    # After creation: impossible to modify directly or indirectly.
    trade_id: str
    cycle_id: int
    symbol: str
    direction: str          # "BUY" or "SELL"
    entry_price: float
    stop_loss: float
    take_profit: float
    entry_time: float       # unix seconds (closed bar time at signal)
    strategy: str
    pattern: str
    score: float
    lot_size: float
    htf_snapshot: Any = None  # Frozen to MappingProxyType in __post_init__
    entry_bar_index: int = 0
    correlation_id: str = ""  # Decision Spine ID — links all artefacts from this decision
    market_phase: str = ""            # IMPULSE | PULLBACK | CONSOLIDATION | EXHAUSTION | REVERSAL | ""
    market_phase_confidence: float = 0.0
    entity_id: str = ""               # Deterministic join key: f"{symbol}_{bar_time}" — links to DecisionTrace
    regime: str = ""                  # TRENDING | RANGE | TRANSITIONAL | ""
    h4_regime: str = ""               # Raw H4 regime classification
    h1_bias: str = ""                 # BULLISH | BEARISH | NEUTRAL | ""
    trade_horizon: str = ""           # SCALP | INTRADAY | EXTENDED | "" (independent of strategy)

    # ─── SHADOW LINEAGE CONTRACT (approved specification) ────────────
    # These fields preserve the relationship between this shadow observation
    # and the authoritative Live V10 decision, per the approved Shadow Design.
    shadow_type: str = ""             # "V10_PRIMARY" | "HORIZON_ALTERNATIVE" | "" (legacy)
    v10_selected_horizon: str = ""    # What V10 HorizonEngine chose for this opportunity
    horizon_selection_status: str = ""  # "SELECTED" | "ALTERNATIVE" | "UNKNOWN" (legacy)
    evaluated_horizon: str = ""       # Which horizon THIS shadow observation evaluates
    horizon_geometry_source: str = "" # "V10_ENTRY_ENGINE" | "STRUCTURE_BASED" | "" (legacy)
    v10_rejection_stage: str = ""     # Where Live V10 stopped: "" | "opportunity" | "strategy" | "entry" | "risk" | "execution"
    v10_action: str = ""              # "EXECUTE" | "NO_TRADE" | "" (legacy)

    # ─── EXECUTION COST CONTEXT (captured at entry for research) ──────
    spread_at_entry: float = 0.0      # ask - bid at decision time (price units)
    bid_at_entry: float = 0.0         # Bid price at decision time
    ask_at_entry: float = 0.0         # Ask price at decision time

    # ─── LIFECYCLE SIMULATION STATE (MUTABLE, forward-only) ───────────
    # Updated only through deterministic forward bar progression.
    bars_elapsed: int = 0
    max_favourable_price: float = 0.0
    max_adverse_price: float = 0.0
    closed: bool = False
    exit_price: float = 0.0
    exit_time: float = 0.0
    exit_reason: str = ""
    exit_bar_index: int = 0

    # State progression (MFE/MAE per bar — forward-append only)
    _state_log: list[dict[str, float]] = field(default_factory=list)

    def __post_init__(self) -> None:
        # ─── DEEP FREEZE: htf_snapshot → structurally immutable ───────
        # Converts any mutable dict/list to MappingProxyType/tuple.
        # After this, it is IMPOSSIBLE to modify snapshot fields.
        if self.htf_snapshot is not None:
            object.__setattr__(self, "htf_snapshot", _deep_freeze(self.htf_snapshot))

        # Initialize MFE/MAE tracking from entry
        if self.direction == "BUY":
            self.max_favourable_price = self.entry_price
            self.max_adverse_price = self.entry_price
        else:
            self.max_favourable_price = self.entry_price
            self.max_adverse_price = self.entry_price


# ═══════════════════════════════════════════════════════════════════════════════
# SHADOW TRADE ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class ShadowTradeEngine:
    """
    Manages shadow trade lifecycle: open → evaluate bars → close → persist.

    Per-symbol, per-cycle. Stateless between bot restarts (no recovery needed).
    """

    def __init__(self, *, max_bars: int = _MAX_BARS_DEFAULT) -> None:
        self._active: dict[str, ShadowTrade] = {}  # trade_id → ShadowTrade
        self._max_bars = max_bars
        self._closed_count = 0
        # In-memory per-symbol dedup: symbol → last evaluated bar_time.
        # Guarantees lifecycle mutation occurs at most once per (symbol, bar_time)
        # even when callers (e.g. BarProvider) dispatch on every poll.
        # Intentionally NOT persisted — this engine is stateless across restarts.
        self._last_evaluated_bar: dict[str, float] = {}

    @property
    def active_count(self) -> int:
        return len(self._active)

    @property
    def closed_count(self) -> int:
        return self._closed_count

    def open_trade(
        self,
        *,
        trade_id: str,
        cycle_id: int,
        symbol: str,
        direction: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        entry_time: float,
        strategy: str = "",
        pattern: str = "",
        score: float = 0.0,
        lot_size: float = 0.01,
        htf_snapshot: dict[str, Any] | None = None,
        entry_bar_index: int = 0,
        correlation_id: str = "",
        market_phase: str = "",
        market_phase_confidence: float = 0.0,
        entity_id: str = "",
        regime: str = "",
        h4_regime: str = "",
        h1_bias: str = "",
        trade_horizon: str = "",
        spread_at_entry: float = 0.0,
        bid_at_entry: float = 0.0,
        ask_at_entry: float = 0.0,
        # ─── Shadow lineage contract fields ───────────────────────────
        shadow_type: str = "",
        v10_selected_horizon: str = "",
        horizon_selection_status: str = "",
        evaluated_horizon: str = "",
        horizon_geometry_source: str = "",
        v10_rejection_stage: str = "",
        v10_action: str = "",
    ) -> ShadowTrade:
        """
        Open a new shadow trade from a live signal.

        Called when the decision engine produces a valid entry signal.
        Does NOT execute any broker order.
        """
        trade = ShadowTrade(
            trade_id=trade_id,
            cycle_id=cycle_id,
            symbol=symbol,
            direction=direction.upper(),
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            entry_time=entry_time,
            strategy=strategy,
            pattern=pattern,
            score=score,
            lot_size=lot_size,
            htf_snapshot=htf_snapshot,
            entry_bar_index=entry_bar_index,
            correlation_id=correlation_id,
            market_phase=market_phase,
            market_phase_confidence=market_phase_confidence,
            entity_id=entity_id,
            regime=regime,
            h4_regime=h4_regime,
            h1_bias=h1_bias,
            trade_horizon=trade_horizon,
            spread_at_entry=spread_at_entry,
            bid_at_entry=bid_at_entry,
            ask_at_entry=ask_at_entry,
            # Shadow lineage contract
            shadow_type=shadow_type,
            v10_selected_horizon=v10_selected_horizon,
            horizon_selection_status=horizon_selection_status,
            evaluated_horizon=evaluated_horizon or trade_horizon,
            horizon_geometry_source=horizon_geometry_source,
            v10_rejection_stage=v10_rejection_stage,
            v10_action=v10_action,
        )
        self._active[trade_id] = trade
        return trade

    def evaluate_bar(
        self,
        *,
        symbol: str,
        bar_high: float,
        bar_low: float,
        bar_close: float,
        bar_time: float,
        bar_index: int = 0,
    ) -> list[dict[str, Any]]:
        """
        Evaluate all active shadow trades for the given symbol against a CLOSED bar.

        Called once per M5 cycle per symbol AFTER closed bar is confirmed.
        This is the state machine transition trigger — NOT a tick handler.

        IMMUTABILITY GUARANTEE:
            This method ONLY modifies lifecycle state (forward-only).
            Decision-time snapshot fields are NEVER touched.

        Returns list of Trade Truth v2 records for any trades that closed this bar.
        """
        closed_records: list[dict[str, Any]] = []

        # ─── BAR DEDUP GUARD (in-memory, per symbol+bar_time) ──────────
        # Callers may invoke evaluate_bar repeatedly for the same
        # (symbol, bar_time) — e.g. BarProvider dispatches Shadow before its
        # own dedup gate. Lifecycle/statistical mutation below must occur at
        # most once per (symbol, bar_time). In-memory only: intentionally
        # stateless across restarts, matching this engine's stateless design.
        _last_evaluated = self._last_evaluated_bar.get(symbol)
        if _last_evaluated is not None and bar_time == _last_evaluated:
            return closed_records
        self._last_evaluated_bar[symbol] = bar_time

        trades_to_close: list[str] = []

        for trade_id, trade in self._active.items():
            if trade.symbol != symbol:
                continue
            if trade.closed:
                continue

            # ─── FORWARD-ONLY LIFECYCLE PROGRESSION ───────────────────
            trade.bars_elapsed += 1

            # ─── Update MFE / MAE tracking ────────────────────────────
            if trade.direction == "BUY":
                trade.max_favourable_price = max(trade.max_favourable_price, bar_high)
                trade.max_adverse_price = min(trade.max_adverse_price, bar_low)
            else:
                trade.max_favourable_price = min(trade.max_favourable_price, bar_low)
                trade.max_adverse_price = max(trade.max_adverse_price, bar_high)

            # ─── State progression log (lightweight) ──────────────────
            _current_r = compute_r_multiple(
                direction=trade.direction,
                entry_price=trade.entry_price,
                exit_price=bar_close,
                stop_loss=trade.stop_loss,
            )
            trade._state_log.append({
                "bar": trade.bars_elapsed,
                "r": _current_r,
                "close": bar_close,
            })

            # ─── Exit condition evaluation (CLOSED bar only) ──────────
            exit_price: float | None = None
            exit_reason = ""

            if trade.direction == "BUY":
                # Stop loss hit
                if bar_low <= trade.stop_loss:
                    exit_price = trade.stop_loss
                    exit_reason = "stop_loss"
                # Take profit hit
                elif bar_high >= trade.take_profit:
                    exit_price = trade.take_profit
                    exit_reason = "take_profit"
            else:  # SELL
                # Stop loss hit
                if bar_high >= trade.stop_loss:
                    exit_price = trade.stop_loss
                    exit_reason = "stop_loss"
                # Take profit hit
                elif bar_low <= trade.take_profit:
                    exit_price = trade.take_profit
                    exit_reason = "take_profit"

            # Max bars timeout
            if exit_price is None and trade.bars_elapsed >= self._max_bars:
                exit_price = bar_close
                exit_reason = "max_bars_timeout"

            # ─── Close trade if exit condition met ────────────────────
            if exit_price is not None:
                trade.exit_price = exit_price
                trade.exit_time = bar_time
                trade.exit_reason = exit_reason
                trade.exit_bar_index = bar_index
                trade.closed = True
                trades_to_close.append(trade_id)

                record = self._build_truth_record(trade)
                closed_records.append(record)

                # Persist
                _persist_shadow_trade(record)

                # Minimal debug event
                _emit_close_event(record)

                self._closed_count += 1

        # Remove closed trades from active
        for tid in trades_to_close:
            del self._active[tid]

        return closed_records


    def _build_truth_record(self, trade: ShadowTrade) -> dict[str, Any]:
        """
        Build a Simulated Trade Lifecycle Record (STR) from a closed shadow trade.

        Schema: shadow_trades_v2 (STR contract)
        Contains EXACTLY 4 domains:
            1. IDENTITY — trade identification + correlation
            2. DECISION_SNAPSHOT — pre-trade intent frozen at decision time
            3. SIMULATION_ENVIRONMENT — market state references at decision time
            4. SIMULATED_OUTCOME — model-generated result (NOT live execution)

        NEVER CONTAINS:
            - broker fills, slippage, order_id, position_id
            - real execution timestamps
            - trade_truth references
            - external PnL reconciliation
        """
        risk_dist = abs(trade.entry_price - trade.stop_loss)
        pip_size = 0.01 if "JPY" in trade.symbol.upper() else 0.0001

        # Canonical R (price-space)
        r_multiple = compute_r_multiple(
            direction=trade.direction,
            entry_price=trade.entry_price,
            exit_price=trade.exit_price,
            stop_loss=trade.stop_loss,
        )

        # MFE / MAE
        mfe_r = compute_mfe_r(
            direction=trade.direction,
            entry_price=trade.entry_price,
            max_favourable_price=trade.max_favourable_price,
            stop_loss=trade.stop_loss,
        )
        mae_r = compute_mae_r(
            direction=trade.direction,
            entry_price=trade.entry_price,
            max_adverse_price=trade.max_adverse_price,
            stop_loss=trade.stop_loss,
        )

        return {
            "schema_version": "shadow_trades_v2",
            "source": "shadow_trade_engine",

            # ─── DOMAIN 1: IDENTITY ───────────────────────────────────
            "identity": {
                "trade_id": trade.trade_id,
                "correlation_id": trade.correlation_id,
                "symbol": trade.symbol,
                "strategy_id": trade.strategy,
                "cycle_id": str(trade.cycle_id),
                "entity_id": trade.entity_id or None,
                # Shadow lineage contract (approved specification)
                "shadow_type": trade.shadow_type or None,
                "v10_selected_horizon": trade.v10_selected_horizon or None,
                "horizon_selection_status": trade.horizon_selection_status or None,
                "evaluated_horizon": trade.evaluated_horizon or None,
                "horizon_geometry_source": trade.horizon_geometry_source or None,
                "v10_rejection_stage": trade.v10_rejection_stage or None,
                "v10_action": trade.v10_action or None,
            },

            # ─── DOMAIN 2: DECISION SNAPSHOT (pre-trade state) ────────
            "decision_snapshot": {
                "timestamp_decision_utc": trade.entry_time,
                "entry_intent_price": trade.entry_price,
                "stop_loss_intent": trade.stop_loss,
                "take_profit_intent": trade.take_profit,
                "direction": trade.direction,
                "position_size": trade.lot_size,
                "risk_config_snapshot": {
                    "risk_price_distance": round(risk_dist, 8),
                    "risk_pips": round(risk_dist / pip_size, 2),
                    "reward_risk_ratio": round(abs(trade.take_profit - trade.entry_price) / risk_dist, 3) if risk_dist > 0 else 0.0,
                },
                "pattern": trade.pattern,
                "score": round(trade.score, 4),
                "execution_context_ref": trade.correlation_id,  # Joinable to execution_context/
                "market_phase": trade.market_phase or None,
                "market_phase_confidence": round(trade.market_phase_confidence, 4),
                "regime": trade.regime or None,
                "h4_regime": trade.h4_regime or None,
                "h1_bias": trade.h1_bias or None,
                "trade_horizon": trade.trade_horizon or None,
                "spread_at_entry": round(trade.spread_at_entry, 8) if trade.spread_at_entry else None,
                "bid_at_entry": round(trade.bid_at_entry, 8) if trade.bid_at_entry else None,
                "ask_at_entry": round(trade.ask_at_entry, 8) if trade.ask_at_entry else None,
            },

            # ─── DOMAIN 3: SIMULATION ENVIRONMENT REFERENCE ───────────
            "simulation_environment": {
                "htf_snapshot": _unfreeze(trade.htf_snapshot) if trade.htf_snapshot else None,
                "entry_bar_index": trade.entry_bar_index,
                "events_ref": {
                    "bar_time": trade.entry_time,
                },
            },

            # ─── DOMAIN 4: SIMULATED OUTCOME (model-generated only) ───
            "simulated_outcome": {
                "exit_price": trade.exit_price,
                "exit_timestamp": trade.exit_time,
                "pnl_r_multiple": r_multiple,
                "mfe_r": mfe_r,
                "mae_r": mae_r,
                "exit_reason": trade.exit_reason,
                "bars_held": trade.bars_elapsed,
                "trade_state_progression": trade._state_log[-10:] if len(trade._state_log) <= 10 else (
                    trade._state_log[:3] + [{"...": len(trade._state_log) - 6}] + trade._state_log[-3:]
                ),
            },
        }

    def get_active_trades(self, symbol: str | None = None) -> list[ShadowTrade]:
        """Return active trades, optionally filtered by symbol."""
        if symbol:
            return [t for t in self._active.values() if t.symbol == symbol]
        return list(self._active.values())

    def stats(self) -> dict[str, Any]:
        """Return engine statistics."""
        return {
            "active_trades": self.active_count,
            "closed_trades": self._closed_count,
            "symbols_tracked": list(set(t.symbol for t in self._active.values())),
        }


# ═══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE (local + S3)
# ═══════════════════════════════════════════════════════════════════════════════

def _persist_shadow_trade(record: dict[str, Any]) -> None:
    """Persist to local JSONL and mirror to S3. Never raises."""
    try:
        # Support both v2 (nested identity) and legacy (top-level) schemas
        symbol = (
            record.get("identity", {}).get("symbol")
            or record.get("symbol")
            or "UNKNOWN"
        )
        exit_time = (
            record.get("simulated_outcome", {}).get("exit_timestamp")
            or record.get("timestamps", {}).get("exit_time")
            or 0
        )
        date_str = datetime.fromtimestamp(exit_time, tz=timezone.utc).strftime("%Y-%m-%d")

        # Local write (primary)
        local_path = Path(_LOCAL_DIR) / symbol / f"{date_str}.jsonl"
        local_path.parent.mkdir(parents=True, exist_ok=True)

        line = json.dumps(record, separators=(",", ":"), default=str) + "\n"
        fd = os.open(str(local_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, line.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

        # S3 mirror (fire-and-forget)
        try:
            from core import config as _cfg
            if getattr(_cfg, "EVENT_STREAM_S3_MIRROR", False):
                _s3_append(symbol, date_str, line)
        except Exception:
            pass

    except Exception as exc:
        logger.debug("[SHADOW_PERSIST_FAIL] %s", exc)


def _s3_append(symbol: str, date_str: str, line: str) -> None:
    """Append a single line to S3 shadow trades JSONL."""
    try:
        import boto3
        s3 = boto3.client(
            "s3",
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=os.getenv("AWS_REGION", "eu-west-2"),
        )
        key = f"{_S3_PREFIX}/schema_version=shadow_trades_v2/symbol={symbol}/date={date_str}/part-000.jsonl"

        # Read-append-write (safe for low-volume shadow trades)
        try:
            existing = s3.get_object(Bucket=_S3_BUCKET, Key=key)
            body = existing["Body"].read().decode("utf-8") + line
        except Exception:
            body = line

        s3.put_object(
            Bucket=_S3_BUCKET, Key=key,
            Body=body.encode("utf-8"),
            ContentType="application/x-ndjson",
        )
    except Exception:
        pass  # S3 failure must never affect shadow engine


# ═══════════════════════════════════════════════════════════════════════════════
# MINIMAL DEBUG EVENT (no log spam)
# ═══════════════════════════════════════════════════════════════════════════════

def _emit_close_event(record: dict[str, Any]) -> None:
    """Emit shadow trade close event. Handles both legacy and v2 schemas."""
    try:
        # v2 schema (shadow_trades_v2)
        identity = record.get("identity", {})
        sim_outcome = record.get("simulated_outcome", {})

        trade_id = identity.get("trade_id", "") or record.get("trade_id", "?")
        symbol = identity.get("symbol", "") or record.get("symbol", "?")
        strategy = identity.get("strategy_id", "") or record.get("strategy_meta", {}).get("strategy", "?")
        r_multiple = sim_outcome.get("pnl_r_multiple", 0) or 0
        mfe_r = sim_outcome.get("mfe_r", 0) or 0
        mae_r = sim_outcome.get("mae_r", 0) or 0
        exit_reason = sim_outcome.get("exit_reason", "?")
        bars_held = sim_outcome.get("bars_held", 0) or 0

        # Determine if this is a horizon shadow trade
        horizon = ""
        if "_INTRADAY" in trade_id:
            horizon = "INTRADAY"
        elif "_EXTENDED" in trade_id:
            horizon = "EXTENDED"
        elif "_INTRADAY" in strategy or "_EXTENDED" in strategy:
            horizon = strategy.split("_")[-1] if "_" in strategy else ""

        if horizon:
            # Horizon-specific outcome log
            logger.info(
                "[HORIZON_SHADOW_CLOSED] symbol=%s horizon=%s outcome=%s "
                "r_multiple=%.4f mfe=%.4f mae=%.4f bars=%d trade_id=%s",
                symbol, horizon, exit_reason,
                r_multiple, mfe_r, mae_r, bars_held, trade_id,
            )
        else:
            # Standard shadow trade close log
            logger.info(
                "[SHADOW_TRADE_CLOSED] trade_id=%s symbol=%s strategy=%s "
                "r_multiple=%.4f exit=%s bars=%d mfe=%.4f mae=%.4f",
                trade_id, symbol, strategy,
                r_multiple, exit_reason, bars_held, mfe_r, mae_r,
            )
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# MODULE-LEVEL SINGLETON
# ═══════════════════════════════════════════════════════════════════════════════

_engine: ShadowTradeEngine | None = None


def get_shadow_engine() -> ShadowTradeEngine:
    """Get or create the singleton shadow trade engine."""
    global _engine
    if _engine is None:
        _engine = ShadowTradeEngine()
    return _engine
