"""Surgical remediation script for execution_attempts dataset."""
import re

FILE = 'execution/mt5_execution.py'

with open(FILE, 'r', encoding='utf-8', errors='replace') as f:
    content = f.read()

# ───────── Issue D: position_modify_sl_tp — add lineage params + persistence ─
# 1. Update method signature
old_sig = '''    def position_modify_sl_tp(
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
        """'''
new_sig = '''    def position_modify_sl_tp(
        self,
        *,
        symbol: str,
        position_ticket: int,
        sl: float,
        tp: float,
        # — Observational lineage (optional, propagated when available) —
        decision_id: str = "",
        correlation_id: str = "",
        cycle_id: int = 0,
        canonical_opportunity_id: str = "",
        observation_id: str = "",
    ) -> ExecutionResult:
        """
        Update SL/TP on an open position (e.g. trailing / break-even).
        Layer 9 trade management calls this; entry path unchanged.
        """'''
assert old_sig in content, "position_modify_sl_tp signature not found"
content = content.replace(old_sig, new_sig, 1)
print("[OK] position_modify_sl_tp signature updated")

# 2. Add tick fetch + market snapshot before DRY_RUN check in position_modify_sl_tp
old_dry = '''        request = {
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
            return result'''
new_dry = '''        # Market snapshot for this attempt (observational only — does not
        # alter the broker request or execution behaviour).
        _mod_tick = mt5_call(mt5.symbol_info_tick, symbol)
        _mod_bid = float(_mod_tick.bid) if _mod_tick is not None else 0.0
        _mod_ask = float(_mod_tick.ask) if _mod_tick is not None else 0.0

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
            _persist_attempt(
                symbol=symbol,
                side="",
                volume=0.0,
                entry_reference=0.0,
                sl=sl,
                tp=tp,
                bid=_mod_bid,
                ask=_mod_ask,
                broker_ok=False,
                retcode=-1,
                deal=0,
                order_ticket=0,
                comment=result.comment,
                fill_price=None,
                attempt_number=1,
                retry_reason=None,
                action_type="SLTP_MODIFY",
                cycle_id=cycle_id,
                canonical_opportunity_id=canonical_opportunity_id,
                observation_id=observation_id,
                decision_id=decision_id,
                correlation_id=correlation_id,
            )
            _safe_log(logging.WARNING, _fmt_result(
                False, -1, "ORDER_SEND_NONE", 0, 0, result.comment,
                symbol, 0.0, latency_ms, action="MODIFY",
            ))
            return result'''
assert old_dry in content, "position_modify_sl_tp order_send block not found"
content = content.replace(old_dry, new_dry, 1)
print("[OK] position_modify_sl_tp None path + persistence added")

# 3. Add persistence for the success/failure result in position_modify_sl_tp
old_result = '''        ok = int(mt5_result.retcode) == mt5.TRADE_RETCODE_DONE
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
        return result'''
new_result = '''        ok = int(mt5_result.retcode) == mt5.TRADE_RETCODE_DONE
        result = ExecutionResult(
            ok,
            int(mt5_result.retcode),
            int(mt5_result.deal),
            int(mt5_result.order),
            str(mt5_result.comment),
        )

        _persist_attempt(
            symbol=symbol,
            side="",
            volume=0.0,
            entry_reference=0.0,
            sl=sl,
            tp=tp,
            bid=_mod_bid,
            ask=_mod_ask,
            broker_ok=ok,
            retcode=result.retcode,
            deal=result.deal,
            order_ticket=result.order,
            comment=result.comment,
            fill_price=None,
            attempt_number=1,
            retry_reason=None,
            action_type="SLTP_MODIFY",
            cycle_id=cycle_id,
            canonical_opportunity_id=canonical_opportunity_id,
            observation_id=observation_id,
            decision_id=decision_id,
            correlation_id=correlation_id,
        )

        _safe_log(
            logging.DEBUG if ok else logging.WARNING,
            _fmt_result(
                ok, result.retcode, describe_retcode(result.retcode),
                result.deal, result.order, result.comment,
                symbol, 0.0, latency_ms, action="MODIFY",
            ),
        )
        return result'''
assert old_result in content, "position_modify_sl_tp result block not found"
content = content.replace(old_result, new_result, 1)
print("[OK] position_modify_sl_tp result persistence added")

# ───────── Issue D: close_position — add lineage params + persistence ─
old_close_sig = '''    def close_position(
        self,
        symbol: str,
        position_ticket: int,
        volume: float | None = None,
    ) -> ExecutionResult:
        """
        Close (or partially close) an open position by ticket.
        If volume is None, closes the full position volume.
        """'''
new_close_sig = '''    def close_position(
        self,
        symbol: str,
        position_ticket: int,
        volume: float | None = None,
        # — Observational lineage (optional, propagated when available) —
        decision_id: str = "",
        correlation_id: str = "",
        cycle_id: int = 0,
        canonical_opportunity_id: str = "",
        observation_id: str = "",
    ) -> ExecutionResult:
        """
        Close (or partially close) an open position by ticket.
        If volume is None, closes the full position volume.
        """'''
assert old_close_sig in content, "close_position signature not found"
content = content.replace(old_close_sig, new_close_sig, 1)
print("[OK] close_position signature updated")

# 4. Add persistence for mt5_result is None in close_position
old_close_none = '''        if mt5_result is None:
            result = ExecutionResult(False, -1, 0, 0, f"close_send_none:{mt5.last_error()}")
            _safe_log(logging.WARNING, _fmt_result(
                False, -1, "ORDER_SEND_NONE", 0, 0, result.comment,
                symbol, close_volume, latency_ms, action="CLOSE",
            ))
            return result'''
new_close_none = '''        if mt5_result is None:
            result = ExecutionResult(False, -1, 0, 0, f"close_send_none:{mt5.last_error()}")
            _persist_attempt(
                symbol=symbol,
                side=_close_side,
                volume=close_volume,
                entry_reference=0.0,
                sl=0.0,
                tp=0.0,
                bid=_close_bid,
                ask=_close_ask,
                broker_ok=False,
                retcode=-1,
                deal=0,
                order_ticket=0,
                comment=result.comment,
                fill_price=None,
                attempt_number=1,
                retry_reason=None,
                action_type=_close_action_type,
                cycle_id=cycle_id,
                canonical_opportunity_id=canonical_opportunity_id,
                observation_id=observation_id,
                decision_id=decision_id,
                correlation_id=correlation_id,
            )
            _safe_log(logging.WARNING, _fmt_result(
                False, -1, "ORDER_SEND_NONE", 0, 0, result.comment,
                symbol, close_volume, latency_ms, action="CLOSE",
            ))
            return result'''
assert old_close_none in content, "close_position None path not found"
content = content.replace(old_close_none, new_close_none, 1)
print("[OK] close_position None path persistence added")

# 5. Add persistence for close_position result, and define _close_side/_close_action_type
old_close_result = '''        ok = int(mt5_result.retcode) == mt5.TRADE_RETCODE_DONE
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
        return result'''
new_close_result = '''        ok = int(mt5_result.retcode) == mt5.TRADE_RETCODE_DONE
        fill_price = getattr(mt5_result, "price", None)
        result = ExecutionResult(
            ok,
            int(mt5_result.retcode),
            int(mt5_result.deal),
            int(mt5_result.order),
            str(mt5_result.comment),
            fill_price=float(fill_price) if fill_price is not None else None,
        )

        _persist_attempt(
            symbol=symbol,
            side=_close_side,
            volume=close_volume,
            entry_reference=0.0,
            sl=0.0,
            tp=0.0,
            bid=_close_bid,
            ask=_close_ask,
            broker_ok=ok,
            retcode=result.retcode,
            deal=result.deal,
            order_ticket=result.order,
            comment=result.comment,
            fill_price=result.fill_price,
            attempt_number=1,
            retry_reason=None,
            action_type=_close_action_type,
            cycle_id=cycle_id,
            canonical_opportunity_id=canonical_opportunity_id,
            observation_id=observation_id,
            decision_id=decision_id,
            correlation_id=correlation_id,
        )

        _safe_log(
            logging.INFO if ok else logging.WARNING,
            _fmt_result(
                ok, result.retcode, describe_retcode(result.retcode),
                result.deal, result.order, result.comment,
                symbol, close_volume, latency_ms, action="CLOSE",
            ) + (f" fill_price={fill_price}" if fill_price is not None else ""),
        )
        return result'''
assert old_close_result in content, "close_position result block not found"
content = content.replace(old_close_result, new_close_result, 1)
print("[OK] close_position result persistence added")

# 6. Add _close_side, _close_action_type, _close_bid, _close_ask before request dict
# in close_position (after determining order_type and price)
old_close_setup = '''        if price <= 0:
            result = ExecutionResult(False, -1, 0, 0, "no_tick_for_close")
            _safe_log(logging.WARNING, _fmt_result(
                False, -1, "NO_TICK", 0, 0, result.comment,
                symbol, close_volume, 0, action="CLOSE",
            ))
            return result

        if self.DRY_RUN:'''
new_close_setup = '''        if price <= 0:
            result = ExecutionResult(False, -1, 0, 0, "no_tick_for_close")
            _safe_log(logging.WARNING, _fmt_result(
                False, -1, "NO_TICK", 0, 0, result.comment,
                symbol, close_volume, 0, action="CLOSE",
            ))
            return result

        # Observational lineage helpers for attempt persistence
        _close_action_type = "PARTIAL_CLOSE" if volume is not None else "CLOSE"
        _close_side = pos.side.name if hasattr(pos, "side") else ""
        _close_bid = float(tick.bid) if tick else 0.0
        _close_ask = float(tick.ask) if tick else 0.0

        if self.DRY_RUN:'''
assert old_close_setup in content, "close_position setup block not found"
content = content.replace(old_close_setup, new_close_setup, 1)
print("[OK] close_position observational helpers added")

with open(FILE, 'w', encoding='utf-8') as f:
    f.write(content)
print("\n=== All mt5_execution.py edits applied ===")
