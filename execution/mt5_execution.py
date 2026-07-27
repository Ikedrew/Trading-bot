"""All MetaTrader5 trade sends live here — MK1: single attempt, no retry spam."""

from __future__ import annotations

import logging
import time as _time
from dataclasses import dataclass

import MetaTrader5 as mt5

from core import config as _cfg
from core.clock import utc_ms
from core.mt5_timeout import mt5_call, is_circuit_open
from risk.models import OrderIntent
from risk.spread_guard import check_spread
from strategy.signals import Side

logger = logging.getLogger(__name__)

_logger_degraded_reported: bool = False
_execution_mode_logged: bool = False


def _report_logger_degraded_once() -> None:
    """Emit a single degradation alert per process lifetime."""
    global _logger_degraded_reported
    if _logger_degraded_reported:
        return
    _logger_degraded_reported = True
    try:
        print("[EXECUTION_LOGGER_DEGRADED] reason=exception_in_logger mode=fallback_print")
    except Exception:
        pass


def _safe_log(level: int, msg: str) -> None:
    """Emit log safely — never raises, never blocks execution."""
    try:
        logger.log(level, msg)
    except Exception:
        try:
            print(f"[EXECUTION_FALLBACK_LOG] {msg}")
        except Exception:
            pass
        _report_logger_degraded_once()


def _log_execution_mode_once() -> None:
    """Emit execution mode exactly once per process lifetime."""
    global _execution_mode_logged
    if _execution_mode_logged:
        return
    _execution_mode_logged = True
    dry = getattr(_cfg, "DRY_RUN", True)
    mode = "DRY_RUN" if dry else "LIVE"
    _safe_log(logging.INFO, f"[EXECUTION_MODE] {mode}")


def _fmt_submitted(symbol: str, side: str, volume: float, sl: float, tp: float,
                   magic: int, deviation: int, fill: int, price: float) -> str:
    """Pre-format submission log. Never raises."""
    try:
        return (
            f"[EXECUTION_SUBMITTED] symbol={symbol} side={side} volume={volume:.4f} "
            f"sl={sl:.5f} tp={tp:.5f} magic={magic} deviation={deviation} "
            f"filling_mode={fill} price={price:.5f}"
        )
    except Exception:
        return f"[EXECUTION_SUBMITTED] symbol={symbol} side={side}"


def _fmt_result(ok: bool, retcode: int, desc: str, deal: int, order: int,
                comment: str, symbol: str, volume: float, latency_ms: int,
                action: str = "") -> str:
    """Pre-format result log. Never raises."""
    try:
        prefix = f"action={action} " if action else ""
        return (
            f"[EXECUTION_RESULT] {prefix}ok={ok} retcode={retcode} retcode_desc={desc} "
            f"deal={deal} order={order} comment={comment} symbol={symbol} "
            f"volume={volume:.4f} latency_ms={latency_ms}"
        )
    except Exception:
        return f"[EXECUTION_RESULT] ok={ok} symbol={symbol}"


@dataclass(frozen=True)
class ExecutionResult:
    ok: bool
    retcode: int
    deal: int
    order: int
    comment: str
    fill_price: float | None = None


# ─── IDEMPOTENCY GUARD ────────────────────────────────────────────────────────

import hashlib

_INTENT_WINDOW_SECONDS = 30.0
_recent_intents: dict[str, float] = {}


def _hash_intent(symbol: str, side: str, volume: float, sl: float, tp: float, magic: int) -> str:
    """Deterministic hash of trade identity fields."""
    try:
        raw = f"{symbol}|{side}|{volume:.8f}|{sl:.8f}|{tp:.8f}|{magic}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]
    except Exception:
        return ""


def _cleanup_intents(now: float) -> None:
    """Remove expired entries from intent cache."""
    expired = [h for h, t in _recent_intents.items() if now - t > _INTENT_WINDOW_SECONDS]
    for h in expired:
        del _recent_intents[h]


def _is_duplicate_intent(intent_hash: str) -> bool:
    """Check if this intent was recently submitted."""
    return intent_hash in _recent_intents


# ─── END IDEMPOTENCY GUARD ────────────────────────────────────────────────────


# ─── EXECUTION METRICS ────────────────────────────────────────────────────────

_execution_metrics: dict = {
    "total_submitted": 0,
    "total_success": 0,
    "total_failed": 0,
    "total_blocked": 0,
    "latency_sum_ms": 0.0,
    "latency_count": 0,
    "retcodes": {},
    "requote_retry_count": 0,
    "timeout_retry_count": 0,
    "total_retries": 0,
}


def _record_metrics(success: bool, retcode: int, latency_ms: float) -> None:
    """Record execution outcome metrics. Never raises."""
    try:
        _execution_metrics["total_submitted"] += 1
        if success:
            _execution_metrics["total_success"] += 1
        else:
            _execution_metrics["total_failed"] += 1
        _execution_metrics["latency_sum_ms"] += latency_ms
        _execution_metrics["latency_count"] += 1
        rc = int(retcode)
        _execution_metrics["retcodes"][rc] = _execution_metrics["retcodes"].get(rc, 0) + 1
    except Exception:
        pass


def get_execution_success_rate() -> float:
    """Return success rate as fraction (0.0–1.0)."""
    total = _execution_metrics["total_submitted"]
    if total == 0:
        return 0.0
    return _execution_metrics["total_success"] / total


def get_average_latency_ms() -> float:
    """Return average execution latency in milliseconds."""
    count = _execution_metrics["latency_count"]
    if count == 0:
        return 0.0
    return _execution_metrics["latency_sum_ms"] / count


def get_execution_metrics() -> dict:
    """Return full metrics snapshot."""
    return {
        "total_submitted": _execution_metrics["total_submitted"],
        "total_success": _execution_metrics["total_success"],
        "total_failed": _execution_metrics["total_failed"],
        "total_blocked": _execution_metrics["total_blocked"],
        "requote_retry_count": _execution_metrics["requote_retry_count"],
        "timeout_retry_count": _execution_metrics["timeout_retry_count"],
        "total_retries": _execution_metrics["total_retries"],
        "success_rate": round(get_execution_success_rate() * 100, 1),
        "avg_latency_ms": round(get_average_latency_ms(), 1),
        "retcodes": dict(_execution_metrics["retcodes"]),
    }


def log_execution_metrics_snapshot() -> None:
    """Emit structured execution metrics summary."""
    _safe_log(
        logging.INFO,
        f"[EXECUTION_METRICS] total={_execution_metrics['total_submitted']} "
        f"success_rate={get_execution_success_rate():.2%} "
        f"avg_latency_ms={get_average_latency_ms():.1f} "
        f"retcodes={_execution_metrics['retcodes']}",
    )


# ─── END EXECUTION METRICS ────────────────────────────────────────────────────


# MQL5 SYMBOL_FILLING_* bitmask (not always exposed on Python mt5 module)
_FILL_FOK = 1
_FILL_IOC = 2
_FILL_RETURN = 4


# ─── EVENT STREAM EMISSION ────────────────────────────────────────────────────

def _emit_execution_event(
    *,
    symbol: str,
    status: str,
    side: str,
    volume: float,
    sl: float,
    tp: float,
    fill_price: float,
    fill_latency_ms: int,
    spread: float,
    slippage: float,
    retcode: int,
    deal: int,
    order_ticket: int,
    decision_ts_utc_ms: int,
    decision_id: str = "",
    correlation_id: str = "",
    pattern: str = "",
    comment: str = "",
) -> None:
    """
    Emit EXECUTION event to unified stream. Only called AFTER broker response.

    Requires decision_ts_utc_ms for causal chain linking.
    Never raises — failures are swallowed.
    """
    if not decision_ts_utc_ms:
        return  # Cannot emit without causal link to decision

    try:
        from core.event_stream import emit_execution
        emit_execution(symbol, {
            "status": status,
            "decision_id": decision_id,
            "correlation_id": correlation_id,
            "decision_ts_utc_ms": decision_ts_utc_ms,
            "side": side,
            "volume": volume,
            "sl": sl,
            "tp": tp,
            "fill_price": fill_price,
            "fill_latency_ms": fill_latency_ms,
            "spread": round(spread, 6),
            "slippage": round(slippage, 6),
            "retcode": retcode,
            "retcode_desc": describe_retcode(retcode) if retcode > 0 else "OK",
            "deal": deal,
            "order_ticket": order_ticket,
            "pattern": pattern,
            "comment": comment,
        }, source="mt5_execution")
    except Exception:
        pass


# ─── END EVENT STREAM EMISSION ────────────────────────────────────────────────


# ─── PRE-EXECUTION VALIDATION ─────────────────────────────────────────────────

def _validate_order(symbol: str, volume: float) -> tuple[bool, str]:
    """
    Pre-flight validation: confirm symbol is tradeable and volume meets broker constraints.
    Returns (True, "") if valid, (False, reason) if invalid. Never raises.
    """
    try:
        sym_info = mt5_call(mt5.symbol_info, symbol)
        if sym_info is None:
            return False, "SYMBOL_NOT_FOUND"
        if not sym_info.visible:
            return False, "SYMBOL_NOT_VISIBLE"
        # trade_mode: 0=disabled, check for any non-zero tradeable state
        if hasattr(sym_info, "trade_mode") and sym_info.trade_mode == 0:
            return False, "SYMBOL_NOT_TRADEABLE"
        # Volume constraints
        if volume < sym_info.volume_min:
            return False, "VOLUME_BELOW_MIN"
        if volume > sym_info.volume_max:
            return False, "VOLUME_ABOVE_MAX"
        step = sym_info.volume_step
        if step > 0:
            # Check step alignment (allow floating point tolerance)
            remainder = volume % step
            if remainder > 1e-10 and (step - remainder) > 1e-10:
                return False, "VOLUME_INVALID_STEP"
        return True, ""
    except Exception as exc:
        # If validation itself fails, allow execution to proceed (fail-open for validation)
        return True, ""


# ─── END PRE-EXECUTION VALIDATION ─────────────────────────────────────────────


def _filling_mode(symbol: str) -> int:
    info = mt5_call(mt5.symbol_info, symbol)
    if info is None:
        return mt5.ORDER_FILLING_IOC
    fm = int(info.filling_mode)
    if fm & _FILL_IOC:
        return mt5.ORDER_FILLING_IOC
    if fm & _FILL_FOK:
        return mt5.ORDER_FILLING_FOK
    if fm & _FILL_RETURN:
        return mt5.ORDER_FILLING_RETURN
    return mt5.ORDER_FILLING_IOC


def describe_retcode(code: int) -> str:
    """Human-readable MT5 trade return code."""
    names = {int(v): k for k, v in vars(mt5).items() if k.startswith("TRADE_RETCODE_") and isinstance(v, int)}
    return names.get(code, f"UNKNOWN({code})")


class MT5Execution:
    def __init__(self, *, magic: int = 713_001, deviation: int = 20) -> None:
        self._magic = magic
        self._deviation = deviation
        _log_execution_mode_once()

    @property
    def DRY_RUN(self) -> bool:
        """Config-driven dry run flag. Preserved as property for backward compatibility."""
        return bool(getattr(_cfg, "DRY_RUN", True))

    # ═══════════════════════════════════════════════════════════════════
    # PRIMARY INTERFACE (OrderIntent-only)
    # ═══════════════════════════════════════════════════════════════════

    def execute(
        self,
        *,
        order_intent: OrderIntent,
        decision_ts_utc_ms: int = 0,
        decision_id: str = "",
        correlation_id: str = "",
    ) -> ExecutionResult:
        """
        Execute an approved order instruction.

        This is the canonical execution entry point. Receives ONLY the
        approved OrderIntent — no analytical context, no strategy objects,
        no signal interpretation.

        Execution's responsibility:
            - Submit order to broker
            - Handle broker communication (fills, requotes, timeouts)
            - Verify fill
            - Report execution result

        Args:
            order_intent: Approved execution instruction (from Risk layer)
            decision_ts_utc_ms: Timestamp for causal chain linkage (observability)
            decision_id: Correlation ID for audit trail (observability)
            correlation_id: Decision spine ID for lifecycle linkage (observability)

        Returns:
            ExecutionResult with ok/retcode/deal/order/comment/fill_price
        """
        return self.place_market(order_intent, decision_ts_utc_ms=decision_ts_utc_ms, decision_id=decision_id, correlation_id=correlation_id)

    # ═══════════════════════════════════════════════════════════════════
    # IMPLEMENTATION (place_market — shared by execute() and legacy callers)
    # ═══════════════════════════════════════════════════════════════════

    def place_market(self, intent: OrderIntent, *, decision_ts_utc_ms: int = 0, decision_id: str = "", correlation_id: str = "") -> ExecutionResult:
        # ─── EXECUTION_ENABLED GATE ───────────────────────────────────
        if not getattr(_cfg, "EXECUTION_ENABLED", True):
            _safe_log(logging.INFO, f"[EXECUTION_DISABLED] symbol={intent.symbol} — EXECUTION_ENABLED=False")
            return ExecutionResult(False, -1, 0, 0, "EXECUTION_DISABLED")
        # ─── END EXECUTION_ENABLED GATE ───────────────────────────────

        # ─── IDEMPOTENCY CHECK ────────────────────────────────────────
        now = _time.time()
        _cleanup_intents(now)
        intent_hash = _hash_intent(
            intent.symbol, intent.side.name, intent.volume,
            intent.sl, intent.tp, self._magic,
        )
        if intent_hash and _is_duplicate_intent(intent_hash):
            _safe_log(logging.WARNING,
                f"[EXECUTION_BLOCKED] reason=DUPLICATE_INTENT symbol={intent.symbol} "
                f"side={intent.side.name} volume={intent.volume:.4f}")
            return ExecutionResult(False, -1, 0, 0, "DUPLICATE_INTENT_BLOCKED")
        if intent_hash:
            _recent_intents[intent_hash] = now
        # ─── END IDEMPOTENCY CHECK ────────────────────────────────────

        # ─── PRE-FLIGHT VALIDATION ────────────────────────────────────
        valid, reason = _validate_order(intent.symbol, intent.volume)
        if not valid:
            _safe_log(logging.WARNING,
                f"[PREVALIDATION_FAILED] symbol={intent.symbol} volume={intent.volume:.4f} reason={reason}")
            return ExecutionResult(False, -1, 0, 0, f"PREVALIDATION_FAILED:{reason}")
        # ─── END PRE-FLIGHT VALIDATION ────────────────────────────────

        tick = mt5_call(mt5.symbol_info_tick, intent.symbol)

        if tick is None:
            err = mt5.last_error()
            result = ExecutionResult(False, -1, 0, 0, f"no_tick:{err}")
            _safe_log(logging.WARNING, _fmt_result(
                False, -1, "NO_TICK", 0, 0, result.comment,
                intent.symbol, intent.volume, 0,
            ))
            return result

        # ─── SPREAD GUARD (hard pre-execution block) ──────────────────
        _bid = float(tick.bid)
        _ask = float(tick.ask)
        _risk_distance = abs(intent.entry_reference - intent.sl)
        _sg = check_spread(
            symbol=intent.symbol,
            bid=_bid,
            ask=_ask,
            risk_distance=_risk_distance,
        )
        if not _sg.allowed:
            _execution_metrics["total_blocked"] += 1
            _safe_log(logging.WARNING,
                f"[SPREAD_GUARD_BLOCKED] symbol={intent.symbol} side={intent.side.name} "
                f"spread={_sg.spread:.6f} ratio={_sg.ratio:.4f} reason={_sg.reason}")
            # Discord: spread guard block
            try:
                _dl = getattr(_cfg, "_discord_logger", None)
                if _dl is not None:
                    _dl.event("RISK_BLOCK", {"guard": "spread", "symbol": intent.symbol, "reason": _sg.reason, "details": {"spread": round(_sg.spread, 6), "ratio": round(_sg.ratio, 4), "side": intent.side.name}})
            except Exception:
                pass
            return ExecutionResult(False, -1, 0, 0, _sg.reason)
        # ─── END SPREAD GUARD ─────────────────────────────────────────

        if intent.side is Side.BUY:
            typ = mt5.ORDER_TYPE_BUY
            price = float(tick.ask)
        else:
            typ = mt5.ORDER_TYPE_SELL
            price = float(tick.bid)

        fill = _filling_mode(intent.symbol)

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": intent.symbol,
            "volume": float(intent.volume),
            "type": typ,
            "price": price,
            "sl": float(intent.sl),
            "tp": float(intent.tp),
            "deviation": self._deviation,
            "magic": self._magic,
            "comment": f"py:{intent.pattern}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": fill,
        }

        # Pre-execution log
        _safe_log(logging.INFO, _fmt_submitted(
            intent.symbol, intent.side.name, intent.volume,
            intent.sl, intent.tp, self._magic, self._deviation, fill, price,
        ))

        if self.DRY_RUN:
            if getattr(_cfg, "DRY_RUN_EXECUTION_LOGS", True):
                try:
                    print(f"[DRY RUN] Trade blocked: {intent.symbol} {intent.side.name}")
                except Exception:
                    pass
            result = ExecutionResult(True, 0, 0, 0, "dry_run")
            _record_metrics(True, 0, 0.0)
            _safe_log(logging.INFO, _fmt_result(
                True, 0, "DRY_RUN", 0, 0, "dry_run",
                intent.symbol, intent.volume, 0,
            ))
            # ─── EVENT STREAM: DRY RUN FILL ───────────────────────────
            _emit_execution_event(
                symbol=intent.symbol,
                status="DRY_RUN_FILLED",
                side=intent.side.name,
                volume=float(intent.volume),
                sl=float(intent.sl),
                tp=float(intent.tp),
                fill_price=price,
                fill_latency_ms=0,
                spread=abs(_ask - _bid),
                slippage=0.0,
                retcode=0,
                deal=0,
                order_ticket=0,
                decision_ts_utc_ms=decision_ts_utc_ms,
                decision_id=decision_id,
                correlation_id=correlation_id,
                pattern=intent.pattern,
            )
            return result

        # Discord: order attempt notification (before broker submission)
        try:
            _dl = getattr(_cfg, "_discord_logger", None)
            if _dl is not None:
                _dl.event("ORDER_ATTEMPT", {
                    "symbol": intent.symbol,
                    "side": intent.side.name,
                    "volume": float(intent.volume),
                    "sl": float(intent.sl),
                    "tp": float(intent.tp),
                })
        except Exception:
            pass

        # ─── PRE-SUBMIT OBSERVABILITY (no MT5 calls — memory-only) ─────
        try:
            print(
                f"[EXECUTION_DEBUG] symbol={request.get('symbol')} "
                f"side={'BUY' if request.get('type') == mt5.ORDER_TYPE_BUY else 'SELL'} "
                f"volume={request.get('volume')} price={request.get('price')} "
                f"sl={request.get('sl')} tp={request.get('tp')} "
                f"magic={request.get('magic')} deviation={request.get('deviation')} "
                f"filling={request.get('type_filling')} comment={request.get('comment')}"
            )
        except Exception:
            pass
        # ─── END PRE-SUBMIT OBSERVABILITY ─────────────────────────────

        t0 = _time.perf_counter()
        mt5_result = mt5_call(mt5.order_send, request)
        latency_ms = int((_time.perf_counter() - t0) * 1000)

        if mt5_result is None:
            result = ExecutionResult(False, -1, 0, 0, f"order_send_none:{mt5.last_error()}")
            _record_metrics(False, -1, float(latency_ms))
            _safe_log(logging.WARNING, _fmt_result(
                False, -1, "ORDER_SEND_NONE", 0, 0, result.comment,
                intent.symbol, intent.volume, latency_ms,
            ))
            return result

        # ─── C4: EXECUTION RETRY (REQUOTES/TIMEOUTS) ──────────────────
        retcode = int(mt5_result.retcode)
        _RETCODE_REQUOTE = 10004
        _RETCODE_TIMEOUT = 10006

        if retcode in (_RETCODE_REQUOTE, _RETCODE_TIMEOUT) and retcode != mt5.TRADE_RETCODE_DONE:
            # Determine retry type
            if retcode == _RETCODE_REQUOTE:
                retry_reason = "REQUOTE"
                _execution_metrics["requote_retry_count"] += 1
                _safe_log(logging.INFO,
                    f"[EXECUTION_RETRY] Reason: REQUOTE (10004) Action: retrying with fresh tick "
                    f"Symbol: {intent.symbol}")
                # Retry immediately with fresh tick
                retry_tick = mt5_call(mt5.symbol_info_tick, intent.symbol)
                if retry_tick is not None:
                    if intent.side is Side.BUY:
                        request["price"] = float(retry_tick.ask)
                    else:
                        request["price"] = float(retry_tick.bid)

            elif retcode == _RETCODE_TIMEOUT:
                retry_reason = "TIMEOUT"
                _execution_metrics["timeout_retry_count"] += 1
                _safe_log(logging.INFO,
                    f"[EXECUTION_RETRY] Reason: TIMEOUT (10006) Delay: 1s "
                    f"Retry attempt: 1 Symbol: {intent.symbol}")
                _time.sleep(1.0)
                # Refresh tick after delay
                retry_tick = mt5_call(mt5.symbol_info_tick, intent.symbol)
                if retry_tick is not None:
                    if intent.side is Side.BUY:
                        request["price"] = float(retry_tick.ask)
                    else:
                        request["price"] = float(retry_tick.bid)

            _execution_metrics["total_retries"] += 1

            # Single retry attempt
            t1 = _time.perf_counter()
            mt5_result = mt5_call(mt5.order_send, request)
            latency_ms = int((_time.perf_counter() - t1) * 1000)

            if mt5_result is None:
                result = ExecutionResult(False, -1, 0, 0, f"retry_send_none:{retry_reason}")
                _record_metrics(False, -1, float(latency_ms))
                _safe_log(logging.WARNING,
                    f"[EXECUTION_FAILED] Retries exhausted (max 1) "
                    f"Reason: {retry_reason} Symbol: {intent.symbol}")
                return result

            retcode = int(mt5_result.retcode)
            if retcode != mt5.TRADE_RETCODE_DONE:
                _safe_log(logging.WARNING,
                    f"[EXECUTION_FAILED] Retries exhausted (max 1) "
                    f"Retcode: {retcode} Reason: {retry_reason} Symbol: {intent.symbol}")
        # ─── END C4 RETRY ─────────────────────────────────────────────

        ok = int(mt5_result.retcode) == mt5.TRADE_RETCODE_DONE
        fill_price = getattr(mt5_result, "price", None)
        result = ExecutionResult(
            ok,
            int(mt5_result.retcode),
            int(mt5_result.deal),
            int(mt5_result.order),
            str(mt5_result.comment),
            fill_price=float(fill_price) if fill_price is not None else None,
        )
        _record_metrics(ok, result.retcode, float(latency_ms))

        _safe_log(
            logging.INFO if ok else logging.WARNING,
            _fmt_result(
                ok, result.retcode, describe_retcode(result.retcode),
                result.deal, result.order, result.comment,
                intent.symbol, intent.volume, latency_ms,
            ) + (f" fill_price={fill_price}" if fill_price is not None else ""),
        )

        # Discord: order filled notification (success only)
        if ok:
            try:
                _dl = getattr(_cfg, "_discord_logger", None)
                if _dl is not None:
                    _dl.event("ORDER_FILLED", {
                        "symbol": intent.symbol,
                        "ticket": result.order,
                        "fill_price": float(fill_price) if fill_price is not None else None,
                        "volume": float(intent.volume),
                        "side": intent.side.name,
                    })
            except Exception:
                pass

        # ─── EVENT STREAM: BROKER RESPONSE (always emit — FILLED or REJECTED) ─
        _status = "FILLED" if ok else "REJECTED"
        _slippage = 0.0
        if ok and fill_price is not None:
            _slippage = abs(float(fill_price) - price)
        _emit_execution_event(
            symbol=intent.symbol,
            status=_status,
            side=intent.side.name,
            volume=float(intent.volume),
            sl=float(intent.sl),
            tp=float(intent.tp),
            fill_price=float(fill_price) if fill_price is not None else price,
            fill_latency_ms=latency_ms,
            spread=abs(_ask - _bid),
            slippage=_slippage,
            retcode=result.retcode,
            deal=result.deal,
            order_ticket=result.order,
            decision_ts_utc_ms=decision_ts_utc_ms,
            decision_id=decision_id,
            correlation_id=correlation_id,
            pattern=intent.pattern,
            comment=result.comment,
        )

        return result

    def position_modify_sl_tp(
        self,
        *,
        symbol: str,
        position_ticket: int,
        sl: float,
        tp: float,
    ) -> ExecutionResult:
        """
        Update SL/TP on an open position (e.g. trailing / break-even).
        Layer 9 trade management calls this; entry path unchanged.
        """
        # ─── KILL SWITCH SAFETY NET (execution boundary) ──────────────
        from core.kill_switch import is_kill_switch_active
        if is_kill_switch_active():
            _safe_log(logging.WARNING,
                f"[EXECUTION_BLOCKED] reason=KILL_SWITCH action=MODIFY "
                f"symbol={symbol} ticket={position_ticket}")
            return ExecutionResult(False, -1, 0, 0, "KILL_SWITCH_BLOCKED")
        # ─── END KILL SWITCH SAFETY NET ───────────────────────────────

        # ─── B4: POSITION OWNERSHIP CHECK ─────────────────────────────
        try:
            from core.position_ownership import enforce_position_ownership
            _pos_info = mt5_call(mt5.positions_get, ticket=position_ticket)
            if _pos_info and len(_pos_info) > 0:
                _pos_magic = int(_pos_info[0].magic)
                if not enforce_position_ownership(
                    position_magic=_pos_magic,
                    action="MODIFY_SL_TP",
                    symbol=symbol,
                    ticket=position_ticket,
                    expected_magic=self._magic,
                ):
                    return ExecutionResult(False, -1, 0, 0, "OWNERSHIP_VIOLATION")
        except Exception:
            pass  # Ownership check failure must not block legitimate operations
        # ─── END OWNERSHIP CHECK ──────────────────────────────────────

        _safe_log(logging.DEBUG, (
            f"[EXECUTION_SUBMITTED] action=MODIFY symbol={symbol} "
            f"ticket={position_ticket} sl={sl:.5f} tp={tp:.5f}"
        ))

        if self.DRY_RUN:
            result = ExecutionResult(True, 0, 0, 0, "dry_run_modify")
            _safe_log(logging.DEBUG, _fmt_result(
                True, 0, "DRY_RUN", 0, 0, "dry_run_modify",
                symbol, 0.0, 0, action="MODIFY",
            ))
            return result

        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": symbol,
            "position": int(position_ticket),
            "sl": float(sl),
            "tp": float(tp),
        }

        t0 = _time.perf_counter()
        mt5_result = mt5_call(mt5.order_send, request)
        latency_ms = int((_time.perf_counter() - t0) * 1000)

        if mt5_result is None:
            result = ExecutionResult(False, -1, 0, 0, f"modify_none:{mt5.last_error()}")
            _safe_log(logging.WARNING, _fmt_result(
                False, -1, "ORDER_SEND_NONE", 0, 0, result.comment,
                symbol, 0.0, latency_ms, action="MODIFY",
            ))
            return result

        ok = int(mt5_result.retcode) == mt5.TRADE_RETCODE_DONE
        result = ExecutionResult(
            ok,
            int(mt5_result.retcode),
            int(mt5_result.deal),
            int(mt5_result.order),
            str(mt5_result.comment),
        )

        _safe_log(
            logging.DEBUG if ok else logging.WARNING,
            _fmt_result(
                ok, result.retcode, describe_retcode(result.retcode),
                result.deal, result.order, result.comment,
                symbol, 0.0, latency_ms, action="MODIFY",
            ),
        )
        return result

    def close_position(
        self,
        symbol: str,
        position_ticket: int,
        volume: float | None = None,
    ) -> ExecutionResult:
        """
        Close (or partially close) an open position by ticket.
        If volume is None, closes the full position volume.
        """
        # ─── POSITION_CLOSE_ENABLED GATE ──────────────────────────────
        if not getattr(_cfg, "POSITION_CLOSE_ENABLED", True):
            _safe_log(logging.INFO, f"[CLOSE_DISABLED] symbol={symbol} — POSITION_CLOSE_ENABLED=False")
            return ExecutionResult(False, -1, 0, 0, "POSITION_CLOSE_DISABLED")
        # ─── END POSITION_CLOSE_ENABLED GATE ──────────────────────────

        # ─── KILL SWITCH SAFETY NET (execution boundary) ──────────────
        from core.kill_switch import is_kill_switch_active
        if is_kill_switch_active():
            _safe_log(logging.WARNING,
                f"[EXECUTION_BLOCKED] reason=KILL_SWITCH action=CLOSE "
                f"symbol={symbol} ticket={position_ticket}")
            return ExecutionResult(False, -1, 0, 0, "KILL_SWITCH_BLOCKED")
        # ─── END KILL SWITCH SAFETY NET ───────────────────────────────

        # ─── B4: POSITION OWNERSHIP CHECK ─────────────────────────────
        try:
            from core.position_ownership import enforce_position_ownership
            _pos_info = mt5_call(mt5.positions_get, ticket=position_ticket)
            if _pos_info and len(_pos_info) > 0:
                _pos_magic = int(_pos_info[0].magic)
                _action = "PARTIAL_CLOSE" if volume is not None else "CLOSE"
                if not enforce_position_ownership(
                    position_magic=_pos_magic,
                    action=_action,
                    symbol=symbol,
                    ticket=position_ticket,
                    expected_magic=self._magic,
                ):
                    return ExecutionResult(False, -1, 0, 0, "OWNERSHIP_VIOLATION")
        except Exception:
            pass  # Ownership check failure must not block legitimate operations
        # ─── END OWNERSHIP CHECK ──────────────────────────────────────

        _safe_log(logging.INFO, (
            f"[EXECUTION_SUBMITTED] action=CLOSE symbol={symbol} "
            f"ticket={position_ticket} volume={volume}"
        ))

        # Fetch position details
        try:
            positions = mt5_call(mt5.positions_get, ticket=position_ticket)
        except Exception as exc:
            result = ExecutionResult(False, -1, 0, 0, f"positions_get_error:{exc}")
            _safe_log(logging.WARNING, _fmt_result(
                False, -1, "POSITIONS_GET_ERROR", 0, 0, result.comment,
                symbol, 0.0, 0, action="CLOSE",
            ))
            try:
                _dl = getattr(_cfg, "_discord_logger", None)
                if _dl is not None:
                    _dl.event("TRADE_CLOSED", {"symbol": symbol, "ticket": position_ticket, "reason": "execution_failure", "details": {"error_type": type(exc).__name__, "message": str(exc)[:200], "close_type": "error"}})
            except Exception:
                pass
            return result

        if positions is None or len(positions) == 0:
            result = ExecutionResult(False, -1, 0, 0, "POSITION_NOT_FOUND")
            _safe_log(logging.WARNING, _fmt_result(
                False, -1, "POSITION_NOT_FOUND", 0, 0, result.comment,
                symbol, 0.0, 0, action="CLOSE",
            ))
            return result

        pos = positions[0]
        close_volume = volume if volume is not None else float(pos.volume)

        # Determine opposite direction
        if int(pos.type) == mt5.ORDER_TYPE_BUY:
            order_type = mt5.ORDER_TYPE_SELL
            tick = mt5_call(mt5.symbol_info_tick, symbol)
            price = float(tick.bid) if tick else 0.0
        else:
            order_type = mt5.ORDER_TYPE_BUY
            tick = mt5_call(mt5.symbol_info_tick, symbol)
            price = float(tick.ask) if tick else 0.0

        if price <= 0:
            result = ExecutionResult(False, -1, 0, 0, "no_tick_for_close")
            _safe_log(logging.WARNING, _fmt_result(
                False, -1, "NO_TICK", 0, 0, result.comment,
                symbol, close_volume, 0, action="CLOSE",
            ))
            return result

        if self.DRY_RUN:
            result = ExecutionResult(True, 0, 0, 0, "dry_run_close")
            _safe_log(logging.INFO, _fmt_result(
                True, 0, "DRY_RUN", 0, 0, "dry_run_close",
                symbol, close_volume, 0, action="CLOSE",
            ))
            return result

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(close_volume),
            "type": order_type,
            "position": int(position_ticket),
            "price": price,
            "deviation": self._deviation,
            "magic": self._magic,
            "comment": "CLOSE_POSITION",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": _filling_mode(symbol),
        }

        t0 = _time.perf_counter()
        mt5_result = mt5_call(mt5.order_send, request)
        latency_ms = int((_time.perf_counter() - t0) * 1000)

        if mt5_result is None:
            result = ExecutionResult(False, -1, 0, 0, f"close_send_none:{mt5.last_error()}")
            _safe_log(logging.WARNING, _fmt_result(
                False, -1, "ORDER_SEND_NONE", 0, 0, result.comment,
                symbol, close_volume, latency_ms, action="CLOSE",
            ))
            return result

        ok = int(mt5_result.retcode) == mt5.TRADE_RETCODE_DONE
        fill_price = getattr(mt5_result, "price", None)
        result = ExecutionResult(
            ok,
            int(mt5_result.retcode),
            int(mt5_result.deal),
            int(mt5_result.order),
            str(mt5_result.comment),
            fill_price=float(fill_price) if fill_price is not None else None,
        )

        _safe_log(
            logging.INFO if ok else logging.WARNING,
            _fmt_result(
                ok, result.retcode, describe_retcode(result.retcode),
                result.deal, result.order, result.comment,
                symbol, close_volume, latency_ms, action="CLOSE",
            ) + (f" fill_price={fill_price}" if fill_price is not None else ""),
        )
        return result
