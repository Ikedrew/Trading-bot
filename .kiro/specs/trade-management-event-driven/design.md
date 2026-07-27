# Design Document — Trade Management Event-Driven Refactor

## Overview

This document describes the architectural changes required to harden the Trade Management Layer (Stage 9) into a fully event-driven, encapsulated lifecycle engine. The refactor is a structural hardening pass — no trading outcomes change. All SL/TP logic already lives in `sl_tp_rules.py` and `TradeStateManager` already owns position state. The gaps are: two missing event types in `TradeLifecycleEvent`, no broker reconciliation path, and `loop.py` not subscribing a listener at construction time.

---

## Architecture

### Current State (as-built)

```
loop.py
  ├── on every tick  → trade_manager.on_price_update(symbol, bid, ask, time.time())
  ├── on bar close   → process_bar() → UnifiedDecision
  └── on trade fill  → execution.place_market(intent)
                     → trade_manager.register_from_execution(...)

TradeStateManager (manager.py)
  ├── _by_id: dict[str, Position]          ← authoritative position store
  ├── on_price_update()                    ← evaluates all SL/TP rules per tick
  ├── register_from_execution()            ← registers new position after fill
  └── _emit() → TradeLifecycleListener     ← listener is None in live loop (not wired)

TradeLifecycleEvent (events.py)
  ├── ON_TRADE_OPEN, ON_PRICE_UPDATE, ON_PARTIAL_CLOSE
  ├── ON_TRADE_CLOSE, ON_STOP_LOSS_HIT, ON_TAKE_PROFIT_HIT
  └── ON_MANAGEMENT_EXIT                  ← missing: ON_SL_MODIFIED, ON_POSITION_UPDATE

MT5Execution (mt5_execution.py)
  ├── place_market(intent)                 ← passes sl/tp from intent unchanged ✓
  └── position_modify_sl_tp(...)           ← dumb executor ✓
```

**Gaps identified:**
1. `TradeLifecycleEvent` missing `ON_SL_MODIFIED` and `ON_POSITION_UPDATE`
2. `TradeStateManager` does not emit `ON_SL_MODIFIED` when it modifies a stop
3. No `on_position_update(position_event)` method for broker reconciliation
4. `loop.py` instantiates `TradeStateManager` with no listener — events are silently dropped
5. No `PositionEvent` dataclass for broker reconciliation payloads

### Target State (after refactor)

```
loop.py
  ├── constructs LoopTradeListener (implements TradeLifecycleListener)
  ├── constructs TradeStateManager(config, listener=loop_listener, execution=execution)
  ├── on every tick  → trade_manager.on_price_update(symbol, bid, ask, time.time())
  ├── on bar close   → process_bar() → UnifiedDecision  [unchanged]
  ├── on trade fill  → execution.place_market(intent)
  │                  → trade_manager.register_from_execution(...)  [unchanged]
  └── on broker poll → trade_manager.on_position_update(position_event)  [new]

TradeStateManager (manager.py)
  ├── _by_id: dict[str, Position]          ← unchanged
  ├── on_price_update()                    ← emits ON_SL_MODIFIED when SL changes
  ├── register_from_execution()            ← unchanged
  └── on_position_update(PositionEvent)    ← new: broker reconciliation

TradeLifecycleEvent (events.py)
  ├── [all existing events unchanged]
  ├── ON_SL_MODIFIED                       ← new
  └── ON_POSITION_UPDATE                   ← new

PositionEvent (events.py)                  ← new dataclass
  └── position_id, symbol, status, stop_loss, take_profit, volume, time_s

LoopTradeListener (loop.py)               ← new inner class / function
  └── on_trade_event(event) → logs at INFO level
```

---

## Component Design

### 1. `events.py` — Two New Event Types + PositionEvent

**Changes:**

Add two values to `TradeLifecycleEvent`:

```python
ON_SL_MODIFIED = "on_sl_modified"
ON_POSITION_UPDATE = "on_position_update"
```

Add `PositionEvent` frozen dataclass:

```python
@dataclass(frozen=True)
class PositionEvent:
    position_id: str
    symbol: str
    status: PositionStatus
    stop_loss: float
    take_profit: float
    volume: float
    time_s: float
```

`TradeEvent`, `TradeLifecycleListener`, and `NoOpTradeListener` are **unchanged**.

---

### 2. `manager.py` — Emit ON_SL_MODIFIED + Add on_position_update

**Change A — Emit ON_SL_MODIFIED when stop is modified:**

In `_process_one_position`, after each `_push_stops_to_server_if_possible` call that results in a changed SL, emit `ON_SL_MODIFIED`:

```python
if new_sl is not None and new_sl != pos.stop_loss:
    old_sl = pos.stop_loss
    pos.stop_loss = new_sl
    self._push_stops_to_server_if_possible(pos)
    self._emit(
        TradeLifecycleEvent.ON_SL_MODIFIED,
        pos,
        (bid, ask),
        ts,
        {"previous_sl": old_sl, "new_sl": new_sl, "source": "break_even"},
    )
```

Same pattern for trailing SL with `"source": "trailing"`.

**Change B — Add `on_position_update` method:**

```python
def on_position_update(self, position_event: PositionEvent) -> None:
    pos = self._by_id.get(position_event.position_id)
    if pos is None:
        logger.warning("on_position_update: unknown position_id=%s", position_event.position_id)
        return

    self._emit(
        TradeLifecycleEvent.ON_POSITION_UPDATE,
        pos,
        (0.0, 0.0),
        position_event.time_s,
        {"broker_event": position_event},
    )

    if position_event.status == PositionStatus.CLOSED:
        self._close_local(
            pos,
            TradeLifecycleEvent.ON_TRADE_CLOSE,
            (0.0, 0.0),
            position_event.time_s,
            {"reason": "broker_closed"},
        )
        return

    # Sync SL/TP if broker differs from local
    if position_event.stop_loss != pos.stop_loss or position_event.take_profit != pos.take_profit:
        pos.stop_loss = position_event.stop_loss
        pos.take_profit = position_event.take_profit
```

---

### 3. `loop.py` — Wire Listener at Construction

**Change — Add `LoopTradeListener` and pass it to `TradeStateManager`:**

```python
class _LoopTradeListener:
    def on_trade_event(self, event: TradeEvent) -> None:
        _NOTIFY_KINDS = {
            TradeLifecycleEvent.ON_TRADE_OPEN,
            TradeLifecycleEvent.ON_STOP_LOSS_HIT,
            TradeLifecycleEvent.ON_TAKE_PROFIT_HIT,
            TradeLifecycleEvent.ON_MANAGEMENT_EXIT,
            TradeLifecycleEvent.ON_SL_MODIFIED,
            TradeLifecycleEvent.ON_POSITION_UPDATE,
        }
        if event.kind not in _NOTIFY_KINDS:
            return
        logger.info(
            "TRADE_EVENT | kind=%s | position_id=%s | symbol=%s | time=%.0f",
            event.kind.value,
            event.position.position_id,
            event.position.symbol,
            event.time_s,
        )
```

In `run_live`, replace:

```python
trade_manager = TradeStateManager(
    _build_trade_management_config(),
    execution=execution,
)
```

with:

```python
trade_manager = TradeStateManager(
    _build_trade_management_config(),
    listener=_LoopTradeListener(),
    execution=execution,
)
```

No other changes to `loop.py`. The existing `on_price_update` call order is preserved.

---

## Data Flow

### Tick Update Flow (unchanged except ON_SL_MODIFIED emission)

```
MT5 tick arrives
  → loop.py: trade_manager.on_price_update(symbol, bid, ask, ts)
    → TradeStateManager._process_one_position(pos, bid, ask, ts)
      → update unrealised_pnl, mfe_extreme
      → emit ON_PRICE_UPDATE
      → check max_time → maybe emit ON_MANAGEMENT_EXIT
      → maybe_break_even_sl() → if changed: push to broker, emit ON_SL_MODIFIED  ← NEW
      → maybe_trailing_sl()   → if changed: push to broker, emit ON_SL_MODIFIED  ← NEW
      → check_exit_trigger()  → if hit: emit ON_STOP_LOSS_HIT or ON_TAKE_PROFIT_HIT
      → maybe_partial()       → if triggered: emit ON_PARTIAL_CLOSE
```

### Trade Entry Flow (unchanged)

```
process_bar() → UnifiedDecision.should_trade=True
  → execution.place_market(intent)  [dumb executor, passes sl/tp unchanged]
  → trade_manager.register_from_execution(...)
    → creates Position, emits ON_TRADE_OPEN
    → _LoopTradeListener logs ON_TRADE_OPEN
```

### Broker Reconciliation Flow (new)

```
MT5 position poll (caller's responsibility to invoke)
  → loop.py: trade_manager.on_position_update(PositionEvent(...))
    → TradeStateManager.on_position_update()
      → emit ON_POSITION_UPDATE
      → if CLOSED: _close_local() → emit ON_TRADE_CLOSE
      → if SL/TP differs: sync local Position
```

---

## File Change Summary

| File | Change Type | Description |
|------|-------------|-------------|
| `core/trade_management/events.py` | Additive | Add `ON_SL_MODIFIED`, `ON_POSITION_UPDATE` to enum; add `PositionEvent` dataclass |
| `core/trade_management/manager.py` | Additive | Emit `ON_SL_MODIFIED` on SL change; add `on_position_update` method |
| `core/loop.py` | Additive | Add `_LoopTradeListener` class; pass it to `TradeStateManager` constructor |
| `core/trade_management/position.py` | None | No changes required |
| `core/trade_management/sl_tp_rules.py` | None | No changes required — already fully encapsulated |
| `execution/mt5_execution.py` | None | No changes required — already a dumb executor |
| `core/engine.py` | None | No changes required — process_bar already pure |
| `core/trade_management/config.py` | None | No changes required |

---

## Behavioural Parity Guarantee

The following are **not changed** by this refactor:

- `sl_tp_rules.py` functions: `check_exit_trigger`, `maybe_break_even_sl`, `maybe_trailing_sl`, `update_mfe_extreme`, `risk_unit_r` — identical signatures and logic
- `_process_one_position` evaluation order: unrealised PnL → MFE → break-even → trailing → exit trigger → partial TP
- `TradeManagementConfig` fields: all preserved unchanged
- `MT5Execution.place_market` and `position_modify_sl_tp`: pass-through unchanged
- `process_bar` return type and logic: unchanged
- `register_from_execution` signature and logic: unchanged
- `on_price_update` call site in `loop.py`: unchanged

The only observable difference is that `_LoopTradeListener` now logs lifecycle events that were previously silently dropped.

---

## Property-Based Testing Targets

| Property | Description |
|----------|-------------|
| **SL monotonicity (BUY)** | For a BUY position, `stop_loss` must never decrease after a break-even or trailing update |
| **SL monotonicity (SELL)** | For a SELL position, `stop_loss` must never increase after a break-even or trailing update |
| **ON_SL_MODIFIED detail consistency** | `detail["previous_sl"] != detail["new_sl"]` must hold for every emitted `ON_SL_MODIFIED` event |
| **on_position_update unknown ID** | Calling `on_position_update` with an unknown `position_id` must not raise and must not modify any tracked position |
| **on_position_update CLOSED** | After `on_position_update` with `status=CLOSED`, the position must have `status=PositionStatus.CLOSED` and must not appear in `positions_open()` |
| **on_position_update SL sync** | After `on_position_update` with a different SL, `pos.stop_loss` must equal `position_event.stop_loss` |
| **Listener receives ON_SL_MODIFIED** | Every SL change in `_process_one_position` must produce exactly one `ON_SL_MODIFIED` event with correct `previous_sl` and `new_sl` |
| **Behavioural parity** | For any sequence of price updates, the sequence of `ON_STOP_LOSS_HIT` / `ON_TAKE_PROFIT_HIT` events must be identical before and after the refactor |
