"""Update trade_management/manager.py to propagate lineage to execution attempts."""
with open('core/trade_management/manager.py', 'r', encoding='utf-8', errors='replace') as f:
    content = f.read()

# ─── Add lineage fields to _SltpRetryEntry ───
old_sltp = '''@dataclass
class _SltpRetryEntry:
    """A queued SL/TP modification that failed and needs retry."""
    symbol: str
    position_ticket: int
    sl: float
    tp: float
    retry_count: int = 0
    last_attempt_time: float = 0.0'''

new_sltp = '''@dataclass
class _SltpRetryEntry:
    """A queued SL/TP modification that failed and needs retry."""
    symbol: str
    position_ticket: int
    sl: float
    tp: float
    retry_count: int = 0
    last_attempt_time: float = 0.0
    # Observational lineage preserved for execution_attempts persistence
    decision_id: str = ""
    correlation_id: str = ""
    cycle_id: int = 0
    canonical_opportunity_id: str = ""
    observation_id: str = ""'''

assert old_sltp in content, "_SltpRetryEntry not found"
content = content.replace(old_sltp, new_sltp, 1)
print("[OK] _SltpRetryEntry lineage fields added")

# ─── Add lineage fields to _CloseRetryEntry + helper ───
old_close_entry = '''@dataclass
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


class TradeStateManager:'''

new_close_entry = '''@dataclass
class _CloseRetryEntry:
    """A queued position close that failed and needs retry."""
    position_id: str
    symbol: str
    position_ticket: int
    volume: float | None  # None = full close; float = partial close
    kind: "TradeLifecycleEvent"
    prices: tuple[float, float]
    detail: dict[str, Any]
    retry_count: int = 0
    last_attempt_time: float = 0.0
    # Observational lineage preserved for execution_attempts persistence
    decision_id: str = ""
    correlation_id: str = ""
    cycle_id: int = 0
    canonical_opportunity_id: str = ""
    observation_id: str = ""


def _lineage_from_pos(pos: "Position") -> dict:
    """Extract observational lineage from a Position's trade_identity.

    Returns an empty dict when no identity is available (e.g. recovered
    positions), so the keyword unpacking is a no-op.
    """
    ti = getattr(pos, "trade_identity", None)
    if ti is None:
        return {}
    return {
        "decision_id": ti.decision_id or "",
        "correlation_id": ti.correlation_id or "",
        "cycle_id": int(ti.cycle_id) if ti.cycle_id else 0,
        "canonical_opportunity_id": ti.canonical_opportunity_id or "",
        "observation_id": ti.observation_id or "",
    }


class TradeStateManager:'''

assert old_close_entry in content, "_CloseRetryEntry block not found"
content = content.replace(old_close_entry, new_close_entry, 1)
print("[OK] _CloseRetryEntry lineage fields + helper added")

# ─── Update _push_stops_to_server_if_possible call ───
old_push = '''        result = self._execution.position_modify_sl_tp(
            symbol=pos.symbol,
            position_ticket=ticket,
            sl=pos.stop_loss,
            tp=pos.take_profit,
        )'''
new_push = '''        result = self._execution.position_modify_sl_tp(
            symbol=pos.symbol,
            position_ticket=ticket,
            sl=pos.stop_loss,
            tp=pos.take_profit,
            **_lineage_from_pos(pos),
        )'''
assert old_push in content, "_push_stops call not found"
content = content.replace(old_push, new_push, 1)
print("[OK] _push_stops_to_server_if_possible updated")

# ─── Update _SltpRetryEntry creation ───
old_entry = '''            entry = _SltpRetryEntry(
                symbol=pos.symbol,
                position_ticket=ticket,
                sl=pos.stop_loss,
                tp=pos.take_profit,
                retry_count=0,
                last_attempt_time=time.time(),
            )'''
new_entry = '''            entry = _SltpRetryEntry(
                symbol=pos.symbol,
                position_ticket=ticket,
                sl=pos.stop_loss,
                tp=pos.take_profit,
                retry_count=0,
                last_attempt_time=time.time(),
                **_lineage_from_pos(pos),
            )'''
assert old_entry in content, "_SltpRetryEntry creation not found"
content = content.replace(old_entry, new_entry, 1)
print("[OK] _SltpRetryEntry creation updated")

# ─── Update drain_sltp_retry_queue call ───
old_drain = '''        for ticket, entry in list(self._sltp_retry_queue.items()):
            result = self._execution.position_modify_sl_tp(
                symbol=entry.symbol,
                position_ticket=entry.position_ticket,
                sl=entry.sl,
                tp=entry.tp,
            )'''
new_drain = '''        for ticket, entry in list(self._sltp_retry_queue.items()):
            result = self._execution.position_modify_sl_tp(
                symbol=entry.symbol,
                position_ticket=entry.position_ticket,
                sl=entry.sl,
                tp=entry.tp,
                decision_id=entry.decision_id,
                correlation_id=entry.correlation_id,
                cycle_id=entry.cycle_id,
                canonical_opportunity_id=entry.canonical_opportunity_id,
                observation_id=entry.observation_id,
            )'''
assert old_drain in content, "drain_sltp_retry_queue call not found"
content = content.replace(old_drain, new_drain, 1)
print("[OK] drain_sltp_retry_queue updated")

# ─── Update partial close broker call ───
old_partial = '''            result = self._execution.close_position(
                symbol=pos.symbol,
                position_ticket=int(pos.mt5_ticket),
                volume=close_vol,
            )'''
new_partial = '''            result = self._execution.close_position(
                symbol=pos.symbol,
                position_ticket=int(pos.mt5_ticket),
                volume=close_vol,
                **_lineage_from_pos(pos),
            )'''
assert old_partial in content, "partial close call not found"
content = content.replace(old_partial, new_partial, 1)
print("[OK] partial close caller updated")

# ─── Update full close broker call ───
old_full = '''            result = self._execution.close_position(
                symbol=pos.symbol,
                position_ticket=int(pos.mt5_ticket),
                volume=None,  # Full close
            )'''
new_full = '''            result = self._execution.close_position(
                symbol=pos.symbol,
                position_ticket=int(pos.mt5_ticket),
                volume=None,  # Full close
                **_lineage_from_pos(pos),
            )'''
assert old_full in content, "full close call not found"
content = content.replace(old_full, new_full, 1)
print("[OK] full close caller updated")

# ─── Update _CloseRetryEntry creation ───
old_close_create = '''        entry = _CloseRetryEntry(
            position_id=pos.position_id,
            symbol=pos.symbol,
            position_ticket=int(pos.mt5_ticket) if pos.mt5_ticket else 0,
            volume=volume,
            kind=kind,
            prices=prices,
            detail=dict(detail),
            retry_count=0,
            last_attempt_time=time.time(),
        )'''
new_close_create = '''        entry = _CloseRetryEntry(
            position_id=pos.position_id,
            symbol=pos.symbol,
            position_ticket=int(pos.mt5_ticket) if pos.mt5_ticket else 0,
            volume=volume,
            kind=kind,
            prices=prices,
            detail=dict(detail),
            retry_count=0,
            last_attempt_time=time.time(),
            **_lineage_from_pos(pos),
        )'''
assert old_close_create in content, "_CloseRetryEntry creation not found"
content = content.replace(old_close_create, new_close_create, 1)
print("[OK] _CloseRetryEntry creation updated")

# ─── Update drain_close_retry_queue call ───
old_drain_close = '''            result = self._execution.close_position(
                symbol=entry.symbol,
                position_ticket=entry.position_ticket,
                volume=entry.volume,
            )'''
new_drain_close = '''            result = self._execution.close_position(
                symbol=entry.symbol,
                position_ticket=entry.position_ticket,
                volume=entry.volume,
                decision_id=entry.decision_id,
                correlation_id=entry.correlation_id,
                cycle_id=entry.cycle_id,
                canonical_opportunity_id=entry.canonical_opportunity_id,
                observation_id=entry.observation_id,
            )'''
assert old_drain_close in content, "drain_close_retry_queue call not found"
content = content.replace(old_drain_close, new_drain_close, 1)
print("[OK] drain_close_retry_queue updated")

with open('core/trade_management/manager.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("\n=== All manager.py edits applied ===")
