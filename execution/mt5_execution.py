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
from core.mt5_symbol_spec import MT5SymbolSpec, validate_stops, validate_volume
from core.symbol_resolver import broker_symbol_for

logger = logging.getLogger(__name__)

_logger_degraded_reported: bool = False
_execution_mode_logged: bool = False
_validated_specs: dict[str, MT5SymbolSpec] = {}


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
    ownership: "PositionOwnership | None" = None

    @classmethod
    def from_account_result(cls, result):
        """Convert an A–F worker result without changing its execution outcome."""
        from core.position_ownership import PositionOwnership
        raw = result.get('ownership')
        return cls(bool(result.get('ok')), int(result.get('retcode', -1)),
                   int(result.get('deal') or 0), int(result.get('order') or 0),
                   str(result.get('comment', '')), result.get('fill_price'),
                   PositionOwnership(**raw) if raw else None)


# ─── IDEMPOTENCY GUARD ────────────────────────────────────────────────────────

import hashlib

import hashlib
import uuid

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



# ─── EXECUTION ATTEMPTS PERSISTENCE ───────────────────────────────────────────

def _persist_attempt(
    *,
    symbol: str,
    side: str,
    volume: float,
    entry_reference: float,
    sl: float,
    tp: float,
    bid: float,
    ask: float,
    broker_ok: bool,
    retcode: int,
    deal: int,
    order_ticket: int,
    comment: str,
    fill_price: float | None,
    attempt_number: int,
    retry_reason: str | None,
    action_type: str = "",
    cycle_id: int = 0,
    canonical_opportunity_id: str = "",
    observation_id: str = "",
    decision_id: str = "",
    correlation_id: str = "",
    trade_id: str = "",
    protection_status: str | None = None,
    broker_confirmed_sl: float | None = None,
    broker_confirmed_tp: float | None = None,
) -> None:
    """Persist one execution attempt. Observational only — never raises.

    ``trade_id`` uses the SAME identity as the existing execution/outcome
    lifecycle.  It is propagated verbatim from the caller when it genuinely
    exists at the attempt stage (e.g. CLOSE / SLTP_MODIFY attempts carry the
    open position's ``pos_{deal}`` trade identity from TradeStateManager).
    For ENTRY attempts no broker-side trade identity exists yet — the ``pos_``
    ID is only materialised downstream (Position registration, Trade Journal)
    after a successful fill — so ``trade_id`` stays empty (→ null in the
    record).  It is **never fabricated** here.

    ``protection_status`` / ``broker_confirmed_sl`` / ``broker_confirmed_tp``
    carry ONLY genuine broker-side confirmation of SL/TP.  The MT5
    ``order_send`` result does not echo confirmed SL/TP prices (it carries
    only ``retcode``/``deal``/``order``/``comment``), so at the attempt point
    no authoritative confirmation exists and these fields are left null.
    The requested ``sl``/``tp`` are recorded separately as ``requested_sl``/
    ``requested_tp`` and are NEVER labelled as broker-confirmed.  Authoritative
    confirmation lives in the post-fill ``verify_protection()`` audit
    (protection_audit dataset) and is never inferred here.
    """
    try:
        from core.persistence.execution_attempts_writer import persist_execution_attempt
        slippage = None
        # Guard against fill_price == 0.0 which the MT5 API uses to signal
        # "no fill" — must not produce a spurious slippage measurement.
        _effective_fill = (
            float(fill_price)
            if fill_price is not None and fill_price != 0
            else None
        )
        if _effective_fill is not None and entry_reference > 0:
            slippage = round(abs(_effective_fill - entry_reference), 6)
        persist_execution_attempt(
            attempt_id=str(uuid.uuid4()),
            symbol=symbol,
            side=side,
            volume=volume,
            entry_reference=entry_reference,
            requested_sl=sl,
            requested_tp=tp,
            bid_at_attempt=bid,
            ask_at_attempt=ask,
            broker_ok=broker_ok,
            retcode=retcode,
            deal=deal,
            order_ticket=order_ticket,
            comment=comment,
            fill_price=_effective_fill,
            slippage=slippage,
            attempt_number=attempt_number,
            retry_reason=retry_reason,
            action_type=action_type,
            cycle_id=cycle_id,
            canonical_opportunity_id=canonical_opportunity_id,
            observation_id=observation_id,
            decision_id=decision_id,
            correlation_id=correlation_id,
            trade_id=trade_id,
            protection_status=protection_status,
            broker_confirmed_sl=broker_confirmed_sl,
            broker_confirmed_tp=broker_confirmed_tp,
        )
    except Exception:
        pass

# ─── END EXECUTION ATTEMPTS PERSISTENCE ───────────────────────────────────────

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

def _validate_order(
    symbol: str,
    volume: float,
    *,
    market_price: float | None = None,
    sl: float = 0.0,
    tp: float = 0.0,
) -> tuple[bool, str]:
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
        spec = MT5SymbolSpec.from_info(symbol, sym_info)
        _validated_specs[symbol] = spec
        volume_error = validate_volume(spec, volume)
        if volume_error:
            return False, volume_error
        if market_price is not None:
            stops_error = validate_stops(spec, market_price=market_price, sl=sl, tp=tp)
            if stops_error:
                return False, stops_error
        return True, ""
    except Exception as exc:
        logger.warning("[PREVALIDATION_ERROR] symbol=%s error=%s", symbol, exc)
        return False, "VALIDATION_ERROR"


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
        cycle_id: int = 0,
        canonical_opportunity_id: str = "",
        observation_id: str = "",
        action_type: str = "ENTRY",
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
            cycle_id: Cycle number for execution grouping (observability)
            canonical_opportunity_id: Canonical opportunity lineage (observability)
            observation_id: Observation lineage (observability)
            action_type: Execution action type — ENTRY, PARTIAL_CLOSE, FULL_CLOSE, SLTP_MODIFY

        Returns:
            ExecutionResult with ok/retcode/deal/order/comment/fill_price
        """
        return self.place_market(
            order_intent,
            decision_ts_utc_ms=decision_ts_utc_ms,
            decision_id=decision_id,
            correlation_id=correlation_id,
            cycle_id=cycle_id,
            canonical_opportunity_id=canonical_opportunity_id,
            observation_id=observation_id,
            action_type=action_type,
        )

    # ═══════════════════════════════════════════════════════════════════
    # IMPLEMENTATION (place_market — shared by execute() and legacy callers)
    # ═══════════════════════════════════════════════════════════════════

    def place_market(self, intent: OrderIntent, *, decision_ts_utc_ms: int = 0, decision_id: str = "", correlation_id: str = "", cycle_id: int = 0, canonical_opportunity_id: str = "", observation_id: str = "", action_type: str = "ENTRY") -> ExecutionResult:
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
        broker_symbol = broker_symbol_for(intent.symbol)
        tick = mt5_call(mt5.symbol_info_tick, broker_symbol)
        if tick is None:
            err = mt5.last_error()
            return ExecutionResult(False, -1, 0, 0, f"no_tick:{err}")
        market_price = float(tick.ask if intent.side is Side.BUY else tick.bid)
        _validated_specs.pop(broker_symbol, None)
        valid, reason = _validate_order(
            broker_symbol, intent.volume, market_price=market_price,
            sl=intent.sl, tp=intent.tp,
        )
        if not valid:
            _safe_log(logging.WARNING,
                f"[PREVALIDATION_FAILED] symbol={intent.symbol} volume={intent.volume:.4f} reason={reason}")
            return ExecutionResult(False, -1, 0, 0, f"PREVALIDATION_FAILED:{reason}")
        # ─── END PRE-FLIGHT VALIDATION ────────────────────────────────

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

        fill = _filling_mode(broker_symbol)
        spec = _validated_specs.get(broker_symbol)
        # Unit tests and legacy adapters may mock the validator as a whole. In
        # production the real validator always populates this cache.
        if spec is not None:
            price = spec.normalize_price(price)
            normalized_sl = spec.normalize_price(intent.sl) if intent.sl else 0.0
            normalized_tp = spec.normalize_price(intent.tp) if intent.tp else 0.0
            normalized_stops_error = validate_stops(
                spec, market_price=price, sl=normalized_sl, tp=normalized_tp,
            )
            if normalized_stops_error:
                return ExecutionResult(
                    False, -1, 0, 0,
                    f"PREVALIDATION_FAILED:{normalized_stops_error}",
                )
        else:
            normalized_sl = float(intent.sl)
            normalized_tp = float(intent.tp)

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": broker_symbol,
            "volume": float(intent.volume),
            "type": typ,
            "price": price,
            "sl": normalized_sl,
            "tp": normalized_tp,
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
        _attempt_number = 1
        mt5_result = mt5_call(mt5.order_send, request)
        latency_ms = int((_time.perf_counter() - t0) * 1000)

        if mt5_result is None:
            result = ExecutionResult(False, -1, 0, 0, f"order_send_none:{mt5.last_error()}")
            _record_metrics(False, -1, float(latency_ms))
            _safe_log(logging.WARNING, _fmt_result(
                False, -1, "ORDER_SEND_NONE", 0, 0, result.comment,
                intent.symbol, intent.volume, latency_ms,
            ))
            _persist_attempt(
                symbol=intent.symbol,
                side=intent.side.name,
                volume=intent.volume,
                entry_reference=intent.entry_reference,
                sl=intent.sl,
                tp=intent.tp,
                bid=_bid,
                ask=_ask,
                broker_ok=False,
                retcode=-1,
                deal=0,
                order_ticket=0,
                comment=result.comment,
                fill_price=None,
                attempt_number=_attempt_number,
                retry_reason=None,
                action_type=action_type,
                cycle_id=cycle_id,
                canonical_opportunity_id=canonical_opportunity_id,
                observation_id=observation_id,
                decision_id=decision_id,
                correlation_id=correlation_id,
            )
            return result

                # ─── C4: EXECUTION RETRY (REQUOTES/TIMEOUTS) ──────────────────
        retcode = int(mt5_result.retcode)
        _RETCODE_REQUOTE = 10004
        _RETCODE_TIMEOUT = 10006

        # Initialise retry state at function scope so it is always defined
        # for downstream persistence (replaces fragile 'vars()' lookup).
        retry_reason: str | None = None

        # Persist attempt 1 (initial broker call) before any retry logic
        _persist_attempt(
            symbol=intent.symbol,
            side=intent.side.name,
            volume=intent.volume,
            entry_reference=intent.entry_reference,
            sl=intent.sl,
            tp=intent.tp,
            bid=_bid,
            ask=_ask,
            broker_ok=(retcode == mt5.TRADE_RETCODE_DONE),
            retcode=retcode,
            deal=int(getattr(mt5_result, "deal", 0)),
            order_ticket=int(getattr(mt5_result, "order", 0)),
            comment=str(getattr(mt5_result, "comment", "")),
            fill_price=(float(getattr(mt5_result, "price", 0)) if getattr(mt5_result, "price", None) is not None else None),
            attempt_number=_attempt_number,
            retry_reason=None,
            action_type=action_type,
            cycle_id=cycle_id,
            canonical_opportunity_id=canonical_opportunity_id,
            observation_id=observation_id,
            decision_id=decision_id,
            correlation_id=correlation_id,
        )

        if retcode in (_RETCODE_REQUOTE, _RETCODE_TIMEOUT) and retcode != mt5.TRADE_RETCODE_DONE:
            # Determine retry type
            if retcode == _RETCODE_REQUOTE:
                retry_reason = "REQUOTE"
                _execution_metrics["requote_retry_count"] += 1
                _safe_log(logging.INFO,
                    f"[EXECUTION_RETRY] Reason: REQUOTE (10004) Action: retrying with fresh tick "
                    f"Symbol: {intent.symbol}")
                # Retry immediately with fresh tick
                retry_tick = mt5_call(mt5.symbol_info_tick, broker_symbol)
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
                retry_tick = mt5_call(mt5.symbol_info_tick, broker_symbol)
                if retry_tick is not None:
                    if intent.side is Side.BUY:
                        request["price"] = float(retry_tick.ask)
                    else:
                        request["price"] = float(retry_tick.bid)

            # Market snapshot for the retry attempt — from the fresh retry tick
            # (Issue B fix: attempt #2 must record the retry tick, not the original)
            _bid_retry = float(retry_tick.bid) if retry_tick is not None else _bid
            _ask_retry = float(retry_tick.ask) if retry_tick is not None else _ask

            _execution_metrics["total_retries"] += 1

            # Single retry attempt
            _attempt_number = 2
            t1 = _time.perf_counter()
            mt5_result = mt5_call(mt5.order_send, request)
            latency_ms = int((_time.perf_counter() - t1) * 1000)

            if mt5_result is None:
                result = ExecutionResult(False, -1, 0, 0, f"retry_send_none:{retry_reason}")
                _record_metrics(False, -1, float(latency_ms))
                _safe_log(logging.WARNING,
                    f"[EXECUTION_FAILED] Retries exhausted (max 1) "
                    f"Reason: {retry_reason} Symbol: {intent.symbol}")
                _persist_attempt(
                    symbol=intent.symbol,
                    side=intent.side.name,
                    volume=intent.volume,
                    entry_reference=intent.entry_reference,
                    sl=intent.sl,
                    tp=intent.tp,
                    bid=_bid_retry,
                    ask=_ask_retry,
                    broker_ok=False,
                    retcode=-1,
                    deal=0,
                    order_ticket=0,
                    comment=result.comment,
                    fill_price=None,
                    attempt_number=_attempt_number,
                    retry_reason=retry_reason,
                    action_type=action_type,
                    cycle_id=cycle_id,
                    canonical_opportunity_id=canonical_opportunity_id,
                    observation_id=observation_id,
                    decision_id=decision_id,
                    correlation_id=correlation_id,
                )
                return result

            retcode = int(mt5_result.retcode)
            if retcode != mt5.TRADE_RETCODE_DONE:
                _safe_log(logging.WARNING,
                    f"[EXECUTION_FAILED] Retries exhausted (max 1) "
                    f"Retcode: {retcode} Reason: {retry_reason} Symbol: {intent.symbol}")
        # ─── END C4 RETRY ─────────────────────────────────────────────

        # Persist attempt 2 if a retry occurred
        if _attempt_number == 2 and mt5_result is not None:
            _persist_attempt(
                symbol=intent.symbol,
                side=intent.side.name,
                volume=intent.volume,
                entry_reference=intent.entry_reference,
                sl=intent.sl,
                tp=intent.tp,
                bid=_bid_retry,
                ask=_ask_retry,
                broker_ok=(int(mt5_result.retcode) == mt5.TRADE_RETCODE_DONE),
                retcode=int(mt5_result.retcode),
                deal=int(getattr(mt5_result, "deal", 0)),
                order_ticket=int(getattr(mt5_result, "order", 0)),
                comment=str(getattr(mt5_result, "comment", "")),
                fill_price=(float(getattr(mt5_result, "price", 0)) if getattr(mt5_result, "price", None) is not None else None),
                attempt_number=_attempt_number,
                retry_reason=retry_reason,
                action_type=action_type,
                cycle_id=cycle_id,
                canonical_opportunity_id=canonical_opportunity_id,
                observation_id=observation_id,
                decision_id=decision_id,
                correlation_id=correlation_id,
            )

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

        if result.ok and result.order:
            try:
                from dataclasses import replace
                from core.accounts.lifecycle import LifecycleRouter
                from core.accounts.worker import AccountReader
                from core.accounts.position_state import ownership_from_fill, save
                # This existing entry adapter is the explicit baseline terminal
                # path. Verify that named account, never infer from a ticket.
                account = LifecycleRouter().account("METAQUOTES")
                reader = AccountReader(account, mt5)
                reader.verify()
                owner = ownership_from_fill(account, reader, result, symbol=intent.symbol,
                    broker_symbol=broker_symbol, magic=self._magic,
                    canonical_opportunity_id=canonical_opportunity_id,
                    correlation_id=correlation_id, decision_id=decision_id)
                result = replace(result, ownership=owner)
                if owner:
                    save(owner, pattern=intent.pattern, trade_horizon=intent.metadata.get("horizon", "SCALP"),
                         sl=intent.sl, tp=intent.tp, status="open")
            except Exception as exc:
                _safe_log(logging.ERROR, "[POSITION_OWNERSHIP_UNRESOLVED] " + type(exc).__name__)
        return result

    def _owned_operation(self, operation, *, symbol, position_ticket,
                         ownership=None, account_id="", broker="", broker_server="",
                         broker_symbol="", **arguments):
        from core.accounts.lifecycle import LifecycleRouter, legacy_owner
        from core.accounts.worker import AccountReadError
        from core.kill_switch import is_kill_switch_active
        if is_kill_switch_active():
            return ExecutionResult(False, -1, 0, 0, "KILL_SWITCH_BLOCKED")
        if operation == "close" and not getattr(_cfg, "POSITION_CLOSE_ENABLED", True):
            return ExecutionResult(False, -1, 0, 0, "POSITION_CLOSE_DISABLED")
        try:
            router = getattr(self, "lifecycle_router", None) or LifecycleRouter()
            if ownership is None:
                ownership = legacy_owner(ticket=position_ticket, symbol=symbol,
                    broker_symbol=broker_symbol or broker_symbol_for(symbol), mt5=mt5,
                    accounts=router.accounts)
            if (ownership.position_ticket != position_ticket or
                    ownership.canonical_symbol != symbol or
                    (account_id and ownership.account_id != account_id) or
                    (broker and ownership.broker != broker) or
                    (broker_server and ownership.broker_server != broker_server) or
                    (broker_symbol and ownership.broker_symbol != broker_symbol)):
                raise AccountReadError("OWNERSHIP_ACCOUNT_MISMATCH")
            value = router.read(ownership, operation, magic=self._magic,
                deviation=self._deviation, allowed=True, dry_run=self.DRY_RUN,
                **arguments)
            return ExecutionResult(**value, ownership=ownership)
        except Exception as exc:
            # Unknown account, failed verification, IPC timeout and unavailable
            # reads never fall back to another connection or mean 'closed'.
            return ExecutionResult(False, -1, 0, 0, "OWNERSHIP_OR_WORKER_FAILED:" + str(exc))

    def position_modify_sl_tp(self, *, symbol, position_ticket, sl, tp,
                              ownership=None, account_id="", broker="", broker_server="",
                              broker_symbol="", decision_id="", correlation_id="",
                              cycle_id=0, canonical_opportunity_id="", observation_id="", trade_id=""):
        return self._owned_operation("modify", symbol=symbol, position_ticket=position_ticket,
            ownership=ownership, account_id=account_id, broker=broker, broker_server=broker_server,
            broker_symbol=broker_symbol, sl=sl, tp=tp)

    def close_position(self, symbol, position_ticket, volume=None,
                       ownership=None, account_id="", broker="", broker_server="",
                       broker_symbol="", decision_id="", correlation_id="", cycle_id=0,
                       canonical_opportunity_id="", observation_id="", trade_id=""):
        return self._owned_operation("close", symbol=symbol, position_ticket=position_ticket,
            ownership=ownership, account_id=account_id, broker=broker, broker_server=broker_server,
            broker_symbol=broker_symbol, volume=volume)
