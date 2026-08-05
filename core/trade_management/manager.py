"""Reactive trade state machine driven by price/time only."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from core.trade_management.config import TradeManagementConfig
from core.trade_management.events import (
    TradeEvent,
    TradeLifecycleEvent,
    TradeLifecycleListener,
)
from core.trade_management.position import Position, PositionStatus
from core.trade_management.sl_tp_rules import (
    check_exit_trigger,
    maybe_break_even_sl,
    maybe_trailing_sl,
    update_mfe_extreme,
)
from risk.models import OrderIntent
from execution.mt5_execution import ExecutionResult
from strategy.signals import Side

logger = logging.getLogger(__name__)

# ─── RETRY QUEUE ──────────────────────────────────────────────────────────────

_MAX_RETRIES = 5


@dataclass
class _SltpRetryEntry:
    """A queued SL/TP modification that failed and needs retry."""
    symbol: str
    position_ticket: int
    sl: float
    tp: float
    retry_count: int = 0
    last_attempt_time: float = 0.0


@dataclass
class _CloseRetryEntry:
    """A queued position close that failed and needs retry."""
    position_id: str
    symbol: str
    position_ticket: int
    volume: float | None  # None = full close; float = partial close
    kind: "TradeLifecycleEvent"
    prices: tuple[float, float]
    detail: dict
    retry_count: int = 0
    last_attempt_time: float = 0.0


class TradeStateManager:
    """
    Owns open/partial positions for a single strategy session.
    Call `on_price_update` on each poll/tick with the same cadence you receive quotes.
    """

    def __init__(
        self,
        config: TradeManagementConfig,
        listener: TradeLifecycleListener | None = None,
        *,
        execution: Any | None = None,
    ) -> None:
        self._cfg = config
        self._listener = listener
        self._execution = execution
        self._by_id: dict[str, Position] = {}
        self._sltp_retry_queue: dict[int, _SltpRetryEntry] = {}  # keyed by position_ticket
        self._close_retry_queue: dict[str, _CloseRetryEntry] = {}  # keyed by position_id

    def positions_open(self) -> list[Position]:
        return [p for p in self._by_id.values() if p.status in (PositionStatus.OPEN, PositionStatus.PARTIAL)]

    def _resolve_config_for_position(self, pos: Position) -> TradeManagementConfig:
        """
        Resolve trade management config for a specific position's horizon.

        Phase 3 architecture: every position resolves its management parameters
        through HorizonManager.get_profile(position.trade_horizon). The profile
        is the single source of truth for trade management behaviour.

        Falls back to self._cfg (global config) if HorizonManager is unavailable.
        This ensures backward compatibility with tests and legacy code paths.
        """
        try:
            from core.horizon.horizon_manager import get_horizon_manager
            _profile = get_horizon_manager().get_profile(
                getattr(pos, "trade_horizon", "SCALP")
            )
            return TradeManagementConfig(
                break_even_trigger_rr=_profile.break_even_trigger_rr,
                break_even_buffer_rr=_profile.break_even_buffer_rr,
                trailing_step=_profile.trailing_step,
                trailing_start_rr=_profile.trailing_start_rr,
                partial_tp_fraction=_profile.partial_tp_fraction,
                partial_tp_path_fraction=_profile.partial_tp_path_fraction,
                max_time_in_trade_seconds=_profile.max_time_in_trade_seconds,
            )
        except Exception:
            # Fallback: use global config (backward compatibility)
            return self._cfg

    def register_from_execution(
        self,
        intent: OrderIntent,
        *,
        magic: int,
        execution: ExecutionResult,
        entry_fill_price: float,
        open_time_s: float | None = None,
        bid: float,
        ask: float,
        trade_identity: Any | None = None,
    ) -> Position | None:
        """
        Call after a successful `place_market`; does not send orders.

        Args:
            intent: The OrderIntent that was executed.
            magic: Bot magic number.
            execution: Broker execution result.
            entry_fill_price: Actual fill price from broker.
            open_time_s: Open timestamp (defaults to now).
            bid: Current bid price.
            ask: Current ask price.
            trade_identity: Immutable TradeIdentity from the originating decision.
                           Carried by the Position for its entire lifecycle.
        """

        if not execution.ok:
            return None

        ts = open_time_s if open_time_s is not None else time.time()
        pid = f"pos_{execution.deal}" if execution.deal else f"pos_{uuid.uuid4().hex[:12]}"
        mfe0 = bid if intent.side is Side.BUY else ask

        pos = Position(
            position_id=pid,
            symbol=intent.symbol,
            side=intent.side,
            magic=magic,
            entry_price=entry_fill_price,
            initial_sl=intent.sl,
            initial_tp=intent.tp,
            stop_loss=intent.sl,
            take_profit=intent.tp,
            volume=intent.volume,
            open_time=ts,
            status=PositionStatus.OPEN,
            mt5_ticket=execution.deal if execution.deal else None,
            deal_id=execution.deal,
            order_id=execution.order,
            pattern_tag=intent.pattern,
            trade_horizon=intent.metadata.get("horizon", "SCALP") if intent.metadata else "SCALP",
            max_favourable_price=mfe0,
            trade_identity=trade_identity,
        )
        self._by_id[pid] = pos
        self._emit(
            TradeLifecycleEvent.ON_TRADE_OPEN,
            pos,
            (bid, ask),
            ts,
            {"execution": execution},
        )
        return pos

    def on_price_update(self, symbol: str, bid: float, ask: float, time_s: float | None = None) -> None:
        """Update all tracked positions for `symbol`."""

        ts = time_s if time_s is not None else time.time()
        for pos in list(self._by_id.values()):
            if pos.symbol != symbol:
                continue
            if pos.status == PositionStatus.CLOSED:
                continue
            self._process_one_position(pos, bid, ask, ts)

    def _emit(
        self,
        kind: TradeLifecycleEvent,
        position: Position,
        prices: tuple[float, float],
        ts: float,
        detail: dict[str, Any],
    ) -> None:
        # Listener (Discord notifications)
        if self._listener is not None:
            self._listener.on_trade_event(TradeEvent(kind=kind, position=position, price_snapshot=prices, time_s=ts, detail=detail))

        # ─── UNIFIED EVENT STREAM: TRADE_MANAGEMENT (Layer 9) ─────────
        # Only emit meaningful state transitions (skip ON_PRICE_UPDATE noise)
        if kind != TradeLifecycleEvent.ON_PRICE_UPDATE:
            try:
                from core.event_stream import emit_trade_management
                emit_trade_management(position.symbol, {
                    "action": kind.value,
                    "position_id": position.position_id,
                    "side": position.side.value if position.side else None,
                    "entry_price": position.entry_price,
                    "current_sl": position.stop_loss,
                    "current_tp": position.take_profit,
                    "initial_sl": position.initial_sl,
                    "initial_tp": position.initial_tp,
                    "volume": position.volume,
                    "unrealised_pnl": getattr(position, "unrealised_pnl", None),
                    "mfe": position.max_favourable_price,
                    "bid": prices[0],
                    "ask": prices[1],
                    "detail": {k: str(v) for k, v in detail.items()} if detail else {},
                }, source="trade_management")
            except Exception:
                pass  # Event emission must never affect trade management
        # ─── END UNIFIED EVENT STREAM ─────────────────────────────────

    def _push_stops_to_server_if_possible(self, pos: Position) -> None:
        if self._execution is None or pos.mt5_ticket is None or pos.mt5_ticket <= 0:
            return
        ticket = int(pos.mt5_ticket)
        result = self._execution.position_modify_sl_tp(
            symbol=pos.symbol,
            position_ticket=ticket,
            sl=pos.stop_loss,
            tp=pos.take_profit,
        )
        if result.ok:
            # Success — remove from retry queue if previously queued
            self._sltp_retry_queue.pop(ticket, None)
        else:
            # Failed — queue for retry
            entry = _SltpRetryEntry(
                symbol=pos.symbol,
                position_ticket=ticket,
                sl=pos.stop_loss,
                tp=pos.take_profit,
                retry_count=0,
                last_attempt_time=time.time(),
            )
            self._sltp_retry_queue[ticket] = entry
            logger.info(
                "[SLTP_RETRY_QUEUED] ticket=%d symbol=%s sl=%.5f tp=%.5f reason=%s",
                ticket, pos.symbol, pos.stop_loss, pos.take_profit, result.comment,
            )

    def drain_sltp_retry_queue(self) -> None:
        """
        Process pending SL/TP retry queue. Call once per tick cycle.
        Non-blocking, idempotent, safe to call even when queue is empty.
        """
        if not self._sltp_retry_queue or self._execution is None:
            return

        completed: list[int] = []
        for ticket, entry in list(self._sltp_retry_queue.items()):
            result = self._execution.position_modify_sl_tp(
                symbol=entry.symbol,
                position_ticket=entry.position_ticket,
                sl=entry.sl,
                tp=entry.tp,
            )
            if result.ok:
                completed.append(ticket)
                logger.info(
                    "[SLTP_RETRY_SUCCESS] ticket=%d symbol=%s sl=%.5f tp=%.5f attempts=%d",
                    ticket, entry.symbol, entry.sl, entry.tp, entry.retry_count + 1,
                )
            else:
                entry.retry_count += 1
                entry.last_attempt_time = time.time()
                if entry.retry_count >= _MAX_RETRIES:
                    completed.append(ticket)
                    logger.warning(
                        "[SLTP_RETRY_FAILED_FINAL] ticket=%d symbol=%s sl=%.5f tp=%.5f attempts=%d reason=%s",
                        ticket, entry.symbol, entry.sl, entry.tp, entry.retry_count, result.comment,
                    )

        for ticket in completed:
            self._sltp_retry_queue.pop(ticket, None)

    def _process_one_position(self, pos: Position, bid: float, ask: float, ts: float) -> None:
        # Resolve trade management config for this position's horizon.
        # Phase 3: each position gets its profile-specific parameters.
        _cfg = self._resolve_config_for_position(pos)

        # unrealised (directional in price space × volume; not normalised to account currency)
        if pos.side is Side.BUY:
            pos.unrealised_pnl = (bid - pos.entry_price) * pos.volume
        else:
            pos.unrealised_pnl = (pos.entry_price - ask) * pos.volume

        pos.max_favourable_price = update_mfe_extreme(pos.side, bid, ask, pos.max_favourable_price)

        self._emit(TradeLifecycleEvent.ON_PRICE_UPDATE, pos, (bid, ask), ts, {})

        # time-based exit
        if _cfg.max_time_in_trade_seconds > 0:
            age = ts - pos.open_time
            if age >= _cfg.max_time_in_trade_seconds:
                # If trailing stop is active (SL has moved beyond initial),
                # allow the trailing mechanism to manage exit naturally.
                # Otherwise close at market — thesis has expired.
                _trailing_active = pos.stop_loss > pos.initial_sl if pos.side is Side.BUY else pos.stop_loss < pos.initial_sl
                _horizon = getattr(pos, "trade_horizon", "SCALP")
                logger.info(
                    "[HORIZON_TIME_EXIT] symbol=%s horizon=%s duration_minutes=%.1f "
                    "max_minutes=%.1f trailing_active=%s reason=MAX_HORIZON_DURATION_REACHED",
                    pos.symbol, _horizon, age / 60.0,
                    _cfg.max_time_in_trade_seconds / 60.0, _trailing_active,
                )
                # Emit structured event for observability
                try:
                    from core.event_stream import emit_trade_management
                    emit_trade_management(pos.symbol, {
                        "action": "HORIZON_TIME_EXIT",
                        "position_id": pos.position_id,
                        "horizon": _horizon,
                        "duration_minutes": round(age / 60.0, 1),
                        "max_duration_minutes": round(_cfg.max_time_in_trade_seconds / 60.0, 1),
                        "trailing_active": _trailing_active,
                        "reason": "MAX_HORIZON_DURATION_REACHED",
                    }, source="trade_management")
                except Exception:
                    pass
                self._close_local(pos, TradeLifecycleEvent.ON_MANAGEMENT_EXIT, (bid, ask), ts, {"reason": "max_time", "horizon": _horizon})
                return

        # dynamic stops (must run before exit checks that use updated SL)
        new_sl = maybe_break_even_sl(
            pos.side,
            bid,
            ask,
            entry=pos.entry_price,
            initial_sl=pos.initial_sl,
            current_sl=pos.stop_loss,
            trigger_rr=_cfg.break_even_trigger_rr,
            buffer_rr=_cfg.break_even_buffer_rr,
        )
        if new_sl is not None and new_sl != pos.stop_loss:
            _old_sl = pos.stop_loss
            pos.stop_loss = new_sl
            self._push_stops_to_server_if_possible(pos)
            # ─── TRADE_MANAGEMENT: break-even SL move ─────────────────
            try:
                from core.event_stream import emit_trade_management
                emit_trade_management(pos.symbol, {
                    "action": "SL_MOVED_BREAKEVEN",
                    "position_id": pos.position_id,
                    "previous_sl": _old_sl,
                    "new_sl": new_sl,
                    "entry_price": pos.entry_price,
                    "trigger_rr": _cfg.break_even_trigger_rr,
                }, source="trade_management")
            except Exception:
                pass
            # ─── END ──────────────────────────────────────────────────

        trail = maybe_trailing_sl(
            pos.side,
            bid,
            ask,
            entry=pos.entry_price,
            initial_sl=pos.initial_sl,
            current_sl=pos.stop_loss,
            mfe_extreme=pos.max_favourable_price,
            trail_step=_cfg.trailing_step,
            start_rr=_cfg.trailing_start_rr,
        )
        if trail is not None and trail != pos.stop_loss:
            _old_sl = pos.stop_loss
            pos.stop_loss = trail
            self._push_stops_to_server_if_possible(pos)
            # ─── TRADE_MANAGEMENT: trailing SL move ───────────────────
            try:
                from core.event_stream import emit_trade_management
                emit_trade_management(pos.symbol, {
                    "action": "SL_MOVED_TRAILING",
                    "position_id": pos.position_id,
                    "previous_sl": _old_sl,
                    "new_sl": trail,
                    "entry_price": pos.entry_price,
                    "mfe": pos.max_favourable_price,
                }, source="trade_management")
            except Exception:
                pass
            # ─── END ──────────────────────────────────────────────────

        hit = check_exit_trigger(pos.side, bid, ask, pos.stop_loss, pos.take_profit)
        if hit == "sl":
            self._close_local(pos, TradeLifecycleEvent.ON_STOP_LOSS_HIT, (bid, ask), ts, {})
            return
        if hit == "tp":
            self._close_local(pos, TradeLifecycleEvent.ON_TAKE_PROFIT_HIT, (bid, ask), ts, {})
            return

        # optional partial TP (same tick geometry as TP path)
        if (
            pos.status == PositionStatus.OPEN
            and _cfg.partial_tp_fraction > 0
            and _cfg.partial_tp_path_fraction > 0
        ):
            if self._maybe_partial(pos, bid, ask, ts, _cfg):
                return

    def _maybe_partial(self, pos: Position, bid: float, ask: float, ts: float, cfg: TradeManagementConfig | None = None) -> bool:
        """
        Attempt partial close at TP1 level.

        Sends partial close to broker FIRST. Only updates local volume on broker
        confirmation. If broker close fails, queues retry and leaves local state
        unchanged.

        Returns True if position was fully consumed (should not continue processing).
        """
        _c = cfg if cfg is not None else self._cfg
        path = _c.partial_tp_path_fraction
        frac = _c.partial_tp_fraction
        entry, tp = pos.entry_price, pos.take_profit
        if pos.side is Side.BUY:
            trigger_px = entry + (tp - entry) * path
            if bid < trigger_px:
                return False
        else:
            trigger_px = entry - (entry - tp) * path
            if ask > trigger_px:
                return False

        close_vol = pos.volume * min(1.0, frac)
        if close_vol <= 0 or close_vol >= pos.volume:
            return False

        # ─── BROKER PARTIAL CLOSE ─────────────────────────────────────
        if self._execution is not None and pos.mt5_ticket is not None and pos.mt5_ticket > 0:
            result = self._execution.close_position(
                symbol=pos.symbol,
                position_ticket=int(pos.mt5_ticket),
                volume=close_vol,
            )
            if not result.ok:
                # Broker partial close failed — queue for retry, do NOT modify local state
                self._queue_close_retry(
                    pos=pos,
                    volume=close_vol,
                    kind=TradeLifecycleEvent.ON_PARTIAL_CLOSE,
                    prices=(bid, ask),
                    detail={"closed_volume": close_vol, "remaining_volume": pos.volume - close_vol},
                )
                return False
        # ─── END BROKER PARTIAL CLOSE ─────────────────────────────────

        # Broker confirmed (or no execution layer / no ticket) — update local state
        pos.volume -= close_vol
        pos.status = PositionStatus.PARTIAL
        self._emit(
            TradeLifecycleEvent.ON_PARTIAL_CLOSE,
            pos,
            (bid, ask),
            ts,
            {"closed_volume": close_vol, "remaining_volume": pos.volume},
        )
        return False

    def _close_local(self, pos: Position, kind: TradeLifecycleEvent, prices: tuple[float, float], ts: float, detail: dict[str, Any]) -> None:
        """
        Close a position: sends close to broker FIRST, then updates local state.

        If broker close fails, queues retry and leaves position OPEN locally.
        If broker returns POSITION_NOT_FOUND, the position was already closed
        server-side (SL/TP/manual) — treat as confirmed close.
        If no execution layer or no ticket, falls through to local-only close
        (DRY_RUN mode or positions without MT5 tickets).
        """
        # ─── BROKER CLOSE ─────────────────────────────────────────────
        if self._execution is not None and pos.mt5_ticket is not None and pos.mt5_ticket > 0:
            result = self._execution.close_position(
                symbol=pos.symbol,
                position_ticket=int(pos.mt5_ticket),
                volume=None,  # Full close
            )
            if not result.ok:
                # POSITION_NOT_FOUND means broker already closed it (server-side SL/TP/manual)
                if result.comment == "POSITION_NOT_FOUND":
                    logger.info(
                        "[BROKER_CONFIRMED_CLOSED] ticket=%d symbol=%s reason=position_not_found "
                        "interpretation=broker_server_side_close",
                        pos.mt5_ticket, pos.symbol,
                    )
                    # Attempt to retrieve actual close details from MT5 history
                    _broker_detail = self._query_broker_close_history(pos)
                    if _broker_detail:
                        detail = {**detail, **_broker_detail}
                    # Fall through to mark CLOSED locally (below)
                else:
                    # Genuine failure — queue for retry, do NOT mark closed locally
                    self._queue_close_retry(
                        pos=pos,
                        volume=None,
                        kind=kind,
                        prices=prices,
                        detail=detail,
                    )
                    return
            else:
                # Successful broker close — query history for authoritative profit
                _broker_detail = self._query_broker_close_history(pos)
                if _broker_detail:
                    detail = {**detail, **_broker_detail}
        # ─── END BROKER CLOSE ─────────────────────────────────────────

        # Broker confirmed (or position_not_found or no execution layer / no ticket)
        pos.status = PositionStatus.CLOSED
        pos.closed_time = ts
        self._emit(kind, pos, prices, ts, detail)

        # Enrich detail with close_reason for ON_TRADE_CLOSE event (used by journal persistence)
        _close_detail = dict(detail)

        # Map lifecycle event kind to close reason (authoritative — bot knows why it closed)
        _lifecycle_reason_map = {
            TradeLifecycleEvent.ON_STOP_LOSS_HIT: "stop_loss",
            TradeLifecycleEvent.ON_TAKE_PROFIT_HIT: "take_profit",
            TradeLifecycleEvent.ON_MANAGEMENT_EXIT: "management_exit",
        }
        _lifecycle_reason = _lifecycle_reason_map.get(kind, "")

        # Lifecycle reason ALWAYS wins over generic broker reasons.
        # Broker reason only used if lifecycle doesn't know (kind not in map)
        # OR if broker provides a MORE specific reason (stop_loss, take_profit, stop_out).
        _SPECIFIC_BROKER_REASONS = frozenset({"stop_loss", "take_profit", "stop_out"})
        _broker_reason = _close_detail.get("reason", "")

        if _lifecycle_reason:
            # Bot already knows the reason — use it
            _close_detail["reason"] = _lifecycle_reason
        elif _broker_reason in _SPECIFIC_BROKER_REASONS:
            # Broker provided a specific, trustworthy reason — keep it
            pass  # already in _close_detail
        elif _broker_reason:
            # Generic broker reason (broker_close, expert_close, etc.) — keep as-is
            pass
        else:
            _close_detail["reason"] = "unknown"

        self._emit(TradeLifecycleEvent.ON_TRADE_CLOSE, pos, prices, ts, _close_detail)

    def _query_broker_close_history(self, pos: Position) -> dict[str, Any] | None:
        """
        Query MT5 deal history to find actual close details for a position
        that was closed server-side. Returns enriched detail dict or None.
        Never raises.
        """
        try:
            import MetaTrader5 as mt5
            from core.mt5_timeout import mt5_call
            from core.mt5_timestamp import normalize_mt5_timestamp
            from datetime import datetime, timezone

            ticket = int(pos.mt5_ticket) if pos.mt5_ticket else 0
            if ticket <= 0:
                return None

            # Search recent history for exit deal matching this position
            from_time = datetime(2020, 1, 1, tzinfo=timezone.utc)
            to_time = datetime(2030, 1, 1, tzinfo=timezone.utc)
            deals = mt5_call(mt5.history_deals_get, from_time, to_time, position=ticket)

            if not deals:
                return None

            # Find the EXIT deal (entry=1 means exit in MT5 deal history)
            for deal in deals:
                if int(deal.entry) == 1:  # 1 = DEAL_ENTRY_OUT
                    # ─── Use MT5 deal.reason (authoritative enum) ─────
                    # MT5 DEAL_REASON constants:
                    #   0 = CLIENT, 1 = MOBILE, 2 = WEB, 3 = EXPERT,
                    #   4 = SL, 5 = TP, 6 = SO (stop out)
                    _DEAL_REASON_MAP = {
                        4: "stop_loss",       # DEAL_REASON_SL
                        5: "take_profit",     # DEAL_REASON_TP
                        6: "stop_out",        # DEAL_REASON_SO (genuine margin call)
                        3: "expert_close",    # DEAL_REASON_EXPERT
                        0: "client_close",    # DEAL_REASON_CLIENT
                        1: "mobile_close",    # DEAL_REASON_MOBILE
                        2: "web_close",       # DEAL_REASON_WEB
                    }

                    deal_reason_int = int(deal.reason) if hasattr(deal, "reason") else -1
                    reason = _DEAL_REASON_MAP.get(deal_reason_int, "")

                    # Fallback: parse comment if deal.reason unavailable or unknown
                    if not reason:
                        comment = str(deal.comment) if deal.comment else ""
                        if "[sl" in comment.lower():
                            reason = "stop_loss"
                        elif "[tp" in comment.lower():
                            reason = "take_profit"
                        else:
                            reason = "broker_close"
                    else:
                        comment = str(deal.comment) if deal.comment else ""

                    # Normalize broker timestamp to UTC
                    exit_time_utc = normalize_mt5_timestamp(float(deal.time))

                    return {
                        "reason": reason,
                        "broker_exit_price": float(deal.price),
                        "broker_exit_time": exit_time_utc,
                        "broker_profit": float(deal.profit),
                        "broker_deal_id": int(deal.ticket),
                        "broker_comment": comment,
                        "broker_deal_reason": deal_reason_int,
                    }
            return None
        except Exception:
            return None

    # ─── CLOSE RETRY INFRASTRUCTURE ───────────────────────────────────────────

    def _queue_close_retry(
        self,
        pos: Position,
        volume: float | None,
        kind: TradeLifecycleEvent,
        prices: tuple[float, float],
        detail: dict[str, Any],
    ) -> None:
        """Queue a failed close/partial-close for retry."""
        # Avoid duplicate queue entries for the same position
        if pos.position_id in self._close_retry_queue:
            return

        entry = _CloseRetryEntry(
            position_id=pos.position_id,
            symbol=pos.symbol,
            position_ticket=int(pos.mt5_ticket) if pos.mt5_ticket else 0,
            volume=volume,
            kind=kind,
            prices=prices,
            detail=dict(detail),
            retry_count=0,
            last_attempt_time=time.time(),
        )
        self._close_retry_queue[pos.position_id] = entry
        action = "PARTIAL_CLOSE" if volume is not None else "CLOSE"
        logger.warning(
            "[CLOSE_RETRY_QUEUED] action=%s position_id=%s symbol=%s ticket=%d volume=%s",
            action, pos.position_id, pos.symbol,
            entry.position_ticket, str(volume),
        )

    def drain_close_retry_queue(self) -> None:
        """
        Process pending close retry queue. Call once per tick cycle alongside
        drain_sltp_retry_queue(). Non-blocking, idempotent, safe when empty.
        """
        if not self._close_retry_queue or self._execution is None:
            return

        completed: list[str] = []
        for pid, entry in list(self._close_retry_queue.items()):
            if entry.position_ticket <= 0:
                # No valid ticket — cannot retry, abandon
                completed.append(pid)
                logger.warning(
                    "[CLOSE_RETRY_ABANDONED] position_id=%s reason=no_valid_ticket",
                    pid,
                )
                continue

            result = self._execution.close_position(
                symbol=entry.symbol,
                position_ticket=entry.position_ticket,
                volume=entry.volume,
            )

            if result.ok:
                completed.append(pid)
                # Apply local state change now that broker confirmed
                pos = self._by_id.get(pid)
                if pos is not None:
                    if entry.volume is None:
                        # Full close
                        pos.status = PositionStatus.CLOSED
                        pos.closed_time = time.time()
                        self._emit(entry.kind, pos, entry.prices, time.time(), entry.detail)
                        # Enrich detail with close_reason for journal persistence
                        _close_detail = dict(entry.detail)
                        if "reason" not in _close_detail:
                            _reason_map = {
                                TradeLifecycleEvent.ON_STOP_LOSS_HIT: "stop_loss",
                                TradeLifecycleEvent.ON_TAKE_PROFIT_HIT: "take_profit",
                                TradeLifecycleEvent.ON_MANAGEMENT_EXIT: "management_exit",
                            }
                            _close_detail["reason"] = _reason_map.get(entry.kind, "unknown")
                        self._emit(TradeLifecycleEvent.ON_TRADE_CLOSE, pos, entry.prices, time.time(), _close_detail)
                    else:
                        # Partial close
                        pos.volume -= entry.volume
                        pos.status = PositionStatus.PARTIAL
                        self._emit(TradeLifecycleEvent.ON_PARTIAL_CLOSE, pos, entry.prices, time.time(), entry.detail)

                action = "PARTIAL_CLOSE" if entry.volume is not None else "CLOSE"
                logger.info(
                    "[CLOSE_RETRY_SUCCESS] action=%s position_id=%s symbol=%s attempts=%d",
                    action, pid, entry.symbol, entry.retry_count + 1,
                )
            elif result.comment == "POSITION_NOT_FOUND":
                # Broker confirms position no longer exists (server-side SL/TP/manual close)
                completed.append(pid)
                pos = self._by_id.get(pid)
                if pos is not None:
                    logger.info(
                        "[BROKER_CONFIRMED_CLOSED_RETRY] position_id=%s symbol=%s ticket=%d "
                        "reason=position_not_found interpretation=broker_server_side_close",
                        pid, entry.symbol, entry.position_ticket,
                    )
                    # Query history for actual close details
                    _broker_detail = self._query_broker_close_history(pos)
                    _close_detail = dict(entry.detail)
                    if _broker_detail:
                        _close_detail.update(_broker_detail)
                    if "reason" not in _close_detail:
                        _close_detail["reason"] = "broker_close"

                    pos.status = PositionStatus.CLOSED
                    pos.closed_time = time.time()
                    self._emit(entry.kind, pos, entry.prices, time.time(), _close_detail)
                    self._emit(TradeLifecycleEvent.ON_TRADE_CLOSE, pos, entry.prices, time.time(), _close_detail)
            else:
                entry.retry_count += 1
                entry.last_attempt_time = time.time()
                if entry.retry_count >= _MAX_RETRIES:
                    completed.append(pid)
                    action = "PARTIAL_CLOSE" if entry.volume is not None else "CLOSE"
                    logger.error(
                        "[CLOSE_RETRY_FAILED_FINAL] action=%s position_id=%s symbol=%s "
                        "ticket=%d attempts=%d reason=%s",
                        action, pid, entry.symbol, entry.position_ticket,
                        entry.retry_count, result.comment,
                    )

        for pid in completed:
            self._close_retry_queue.pop(pid, None)

    # ─── B5: CLOSED POSITION EVICTION ─────────────────────────────────────────

    def evict_closed_positions(self) -> int:
        """
        Remove closed positions from _by_id after they are safely journaled.

        Safety requirements:
        - Position must be CLOSED
        - closed_time must exist
        - Eviction delay must have elapsed
        - Position must be journaled (confirmed persisted)

        Returns number of positions evicted.
        """
        try:
            from core import config as _cfg
            enabled = bool(getattr(_cfg, "POSITION_EVICTION_ENABLED", True))
            delay = float(getattr(_cfg, "POSITION_EVICTION_DELAY_SECONDS", 3600))
        except ImportError:
            enabled = True
            delay = 3600.0

        if not enabled:
            return 0

        now = time.time()
        to_evict: list[str] = []

        for pid, pos in self._by_id.items():
            # Only evict CLOSED positions
            if pos.status != PositionStatus.CLOSED:
                continue

            # Must have closed_time
            if pos.closed_time is None:
                continue

            # Delay must have elapsed
            if now - pos.closed_time < delay:
                continue

            # Journal safety: verify position is persisted
            try:
                from core.trade_journal import is_already_journaled
                trade_id = f"close_{pos.position_id}"
                if not is_already_journaled(trade_id) and not is_already_journaled(pos.position_id):
                    logger.debug(
                        "[POSITION_EVICTION_SKIPPED] id=%s reason=not_journaled",
                        pos.position_id,
                    )
                    continue
            except ImportError:
                # No journal module — allow eviction (journal not mandatory)
                pass
            except Exception:
                # Journal check failed — skip eviction to be safe
                continue

            to_evict.append(pid)

        # Perform eviction
        for pid in to_evict:
            pos = self._by_id.pop(pid, None)
            if pos is not None:
                logger.info(
                    "[POSITION_EVICTED] id=%s symbol=%s closed_at=%.0f reason=memory_cleanup",
                    pos.position_id, pos.symbol, pos.closed_time or 0,
                )

        return len(to_evict)
