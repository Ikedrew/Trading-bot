# Technical Design — MT5 Runtime Survivability

## Overview

Adds exception-safe guards, bounded reconnect logic, proactive connection validation, recovery state management, and post-reconnect stabilisation to the live trading loop. All changes are additive to `core/loop.py` and `core/config.py`. No strategy, execution, or trade management logic is modified.

---

## Architecture

### Recovery State Machine

```
NORMAL ──(MT5 failure or terminal disconnected)──→ RECOVERY
   ↑                                                    │
   │                                                    ├── reconnect attempt 1
   │                                                    ├── reconnect attempt 2
   │                                                    ├── ...
   │                                                    ├── reconnect attempt N (max)
   │                                                    │
   │    ←──(reconnect success + stabilisation)──────────┘
   │
   └── NORMAL (resumed)

RECOVERY ──(all attempts exhausted)──→ SHUTDOWN (clean exit)
```

### Live Loop Flow (Modified)

```
while running:
    ┌─ CONNECTION CHECK ─────────────────────────────────┐
    │ if _recovery_state == RECOVERY:                     │
    │     skip data fetch + execution                     │
    │     attempt reconnect (bounded)                     │
    │     if success → stabilisation cycle                │
    │     if exhausted → clean shutdown                   │
    │     continue                                        │
    │                                                     │
    │ terminal_info = mt5.terminal_info()                 │
    │ if disconnected → enter RECOVERY, continue          │
    └─────────────────────────────────────────────────────┘

    ┌─ DATA FETCH (exception-safe) ──────────────────────┐
    │ try:                                                │
    │     bid, ask = feed.last_tick(symbol)               │
    │     candles = feed.copy_rates_closed(...)           │
    │ except RuntimeError:                                │
    │     enter RECOVERY, continue                        │
    └─────────────────────────────────────────────────────┘

    ┌─ STABILISATION CHECK ──────────────────────────────┐
    │ if _stabilising:                                    │
    │     validate tick freshness                         │
    │     if stale → remain in stabilisation, continue    │
    │     if fresh → exit stabilisation, emit event       │
    │     suppress execution during stabilisation         │
    └─────────────────────────────────────────────────────┘

    ┌─ NORMAL EXECUTION (unchanged) ─────────────────────┐
    │ trade_manager.on_price_update(...)                  │
    │ candle deduplication                                │
    │ process_bar(...)                                    │
    │ execution gates                                     │
    │ place_market(...)                                   │
    └─────────────────────────────────────────────────────┘
```

---

## Components and Interfaces

### Recovery State (in `core/loop.py`)

```python
_NORMAL = "NORMAL"
_RECOVERY = "RECOVERY"

# Inside run_live:
recovery_state = _NORMAL
stabilising = False
reconnect_count = 0
```

### `_attempt_reconnect(symbol, feed)` helper

```python
def _attempt_reconnect(symbol: str, feed: MT5DataFeed) -> bool:
    try:
        mt5.shutdown()
        time.sleep(delay)
        if not mt5.initialize():
            return False
        info = mt5.terminal_info()
        if info is None or not info.connected:
            return False
        mt5.symbol_select(symbol, True)
        return True
    except Exception:
        return False
```

### `_is_tick_fresh(symbol)` helper

```python
def _is_tick_fresh(symbol: str, max_staleness: float) -> bool:
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return False
    return (time.time() - tick.time) <= max_staleness
```

---

## Data Models

### Configuration Parameters (added to `config.py`)

```python
MT5_MAX_RECONNECT_ATTEMPTS = 5
MT5_RECONNECT_DELAY_SECONDS = 5.0
MT5_RECONNECT_BACKOFF_ENABLED = True
MT5_MAX_TICK_STALENESS_SECONDS = 30.0
```

### Lifecycle Events Emitted

| Event Type | When | Level |
|---|---|---|
| `disconnect_detected` | Terminal disconnected or MT5 call fails | INFO |
| `recovery_entered` | State transitions NORMAL → RECOVERY | INFO |
| `reconnect_started` | Each reconnect attempt begins | INFO |
| `reconnect_success` | Reconnect attempt succeeds | INFO |
| `reconnect_failed` | Reconnect attempt fails | INFO |
| `tick_stale_during_recovery` | Post-reconnect tick is stale | INFO |
| `recovery_stabilised` | Stabilisation cycle completes | INFO |
| `recovery_exited` | State transitions RECOVERY → NORMAL | INFO |
| `reconnect_exhausted` | All attempts used | CRITICAL |
| `runtime_shutdown_unrecoverable` | Clean exit due to failure | CRITICAL |

---

## Correctness Properties

### Property 1: No Duplicate Trade Execution After Reconnect
After successful reconnect, `last_closed_time` is preserved. The candle deduplication check (`if last_closed_time == closed_time: continue`) prevents reprocessing the same bar.

### Property 2: No Infinite Reconnect Loop
Reconnect attempts are bounded by `MT5_MAX_RECONNECT_ATTEMPTS`. Counter increments on each attempt. When exhausted, clean shutdown occurs.

### Property 3: State Preservation During Recovery
`EngineState`, `TradeStateManager`, `EventState`, `last_closed_time`, and `iterations` are local variables that persist across the recovery cycle — they are never reset during reconnect.

### Property 4: Execution Suppression During Recovery
While `recovery_state == RECOVERY` or `stabilising == True`, no `process_bar` or `place_market` calls occur.

### Property 5: Recovery Deduplication
While already in RECOVERY state, subsequent MT5 failures do not trigger additional recovery entries or reconnect cascades.

---

## Error Handling

- All reconnect logic is wrapped in try/except — a failed reconnect attempt cannot crash the runtime
- `_attempt_reconnect` catches all exceptions and returns False
- Lifecycle event emission is wrapped in try/except (from lifecycle_logger design)
- Clean shutdown always calls `mt5.shutdown()` regardless of state

---

## Testing Strategy

1. **Unit test `_attempt_reconnect`**: Mock `mt5.initialize()` to return False, verify bounded retry and backoff timing
2. **Unit test recovery state transitions**: Verify NORMAL → RECOVERY → NORMAL flow
3. **Unit test execution suppression**: Verify no `process_bar` calls while in RECOVERY
4. **Unit test tick freshness**: Mock stale tick timestamps, verify stabilisation remains active
5. **Integration test**: Simulate MT5 disconnect mid-loop, verify recovery + resume without duplicate bars

---

## File Impact

| File | Change Type | Description |
|---|---|---|
| `core/config.py` | Additive | 4 new config parameters |
| `core/loop.py` | Additive | Recovery state, connection check, exception guards, reconnect helper, stabilisation logic |
| `data/mt5_data.py` | None | Unchanged |
| `execution/mt5_execution.py` | None | Unchanged |
| `core/engine.py` | None | Unchanged |
| `strategy/` | None | Unchanged |
