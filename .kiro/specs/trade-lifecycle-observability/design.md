# Technical Design — Trade Lifecycle Observability

## Overview

This design adds a passive, structured lifecycle logging layer to the trading bot. A single new module (`core/lifecycle_logger.py`) provides the central `emit_lifecycle_event` helper. A concrete `TradeLifecycleListener` implementation wires into `TradeStateManager` to capture position-level events. All emissions respect the existing `ESSENTIAL_LOGS` hierarchy and new granular switches. No strategy, execution, or trade management logic is modified.

---

## Architecture

### Event Flow

```
Strategy/Execution Layer (existing, unchanged)
    │
    ├── process_bar() returns Decision
    │       └── loop.py calls emit_lifecycle_event("TRADE_SIGNAL", {...})
    │
    ├── execution.place_market() returns ExecutionResult
    │       ├── ok=True  → loop.py calls emit_lifecycle_event("TRADE_ENTRY", {...})
    │       └── ok=False → loop.py calls emit_lifecycle_event("TRADE_REJECT", {...})
    │
    └── TradeStateManager emits TradeEvent
            └── _LifecycleListener.on_trade_event(event)
                    ├── ON_TRADE_CLOSE → emit_lifecycle_event("TRADE_EXIT", {...})
                    ├── ON_STOP_LOSS_HIT → emit_lifecycle_event("TRADE_EXIT", {...})
                    ├── ON_TAKE_PROFIT_HIT → emit_lifecycle_event("TRADE_EXIT", {...})
                    ├── ON_MANAGEMENT_EXIT → emit_lifecycle_event("TRADE_EXIT", {...})
                    └── SL changed → emit_lifecycle_event("TRADE_MODIFY", {...})

emit_lifecycle_event(event_type, data)
    │
    ├── check ESSENTIAL_LOGS → if False, return
    ├── check ENABLE_TRADE_LIFECYCLE_LOGS → if False, return (except HEARTBEAT)
    ├── check ENABLE_HEARTBEAT_LOGS → if False and event is HEARTBEAT, return
    ├── attach timestamp
    ├── format key=value string
    └── logger.info("[%s] %s", event_type, formatted_string)
            │
            └── (future) optional alert dispatcher hook
```

---

## New File: `core/lifecycle_logger.py`

### Responsibilities
- Single entry point for all lifecycle event emissions
- Timestamp attachment (uses `time.time()`)
- Key=value formatting
- Log-switch gating
- Exception isolation (try/except around all formatting + logging)

### Function Signature

```python
def emit_lifecycle_event(event_type: str, data: dict[str, Any]) -> None:
```

### Gating Logic

```python
def emit_lifecycle_event(event_type: str, data: dict[str, Any]) -> None:
    try:
        if not getattr(config, "ESSENTIAL_LOGS", True):
            return
        if event_type == "HEARTBEAT":
            if not getattr(config, "ENABLE_HEARTBEAT_LOGS", True):
                return
        else:
            if not getattr(config, "ENABLE_TRADE_LIFECYCLE_LOGS", True):
                return

        ts = time.time()
        parts = [f"ts={ts:.3f}"]
        for k, v in data.items():
            parts.append(f"{k}={v}")
        payload = " ".join(parts)
        logger.info("[%s] %s", event_type, payload)
    except Exception:
        logger.debug("lifecycle_logger: emission failed for %s", event_type, exc_info=True)
```

---

## Insertion Points

### TRADE_SIGNAL
**Location:** `core/loop.py` → `run_live`, after `process_bar` returns and `decision.should_trade=True` is confirmed, before execution gates.

```python
if not decision.should_trade or decision.intent is None:
    continue
# ← INSERT HERE
emit_lifecycle_event("TRADE_SIGNAL", {
    "symbol": symbol,
    "direction": decision.intent.side.value,
    "pattern": decision.intent.pattern,
    "score": decision.score,
    "reason": decision.reason,
})
```

### TRADE_ENTRY
**Location:** `core/loop.py` → `run_live`, after `result.ok=True` and `register_from_execution` completes.

```python
if result.ok:
    state.last_successful_open_mono = float(closed_time)
    # ... register_from_execution ...
    # ← INSERT HERE
    emit_lifecycle_event("TRADE_ENTRY", {
        "symbol": symbol,
        "direction": decision.intent.side.value,
        "entry": fill_px,
        "sl": decision.intent.sl,
        "tp": decision.intent.tp,
        "volume": decision.intent.volume,
        "pattern": decision.intent.pattern,
        "score": decision.score,
    })
```

### TRADE_REJECT
**Location:** `core/loop.py` → `run_live`, after `result.ok=False`.

```python
else:
    emit_lifecycle_event("TRADE_REJECT", {
        "symbol": symbol,
        "direction": decision.intent.side.value,
        "reason": describe_retcode(result.retcode),
    })
```

### TRADE_EXIT
**Location:** `_LifecycleListener.on_trade_event` — triggered by `ON_STOP_LOSS_HIT`, `ON_TAKE_PROFIT_HIT`, `ON_MANAGEMENT_EXIT`, or `ON_TRADE_CLOSE` (deduplicated).

### TRADE_MODIFY
**Location:** `_LifecycleListener.on_trade_event` — triggered when SL changes are detected (comparing previous SL to current SL in the event detail or position snapshot).

### HEARTBEAT
**Location:** `core/loop.py` → `run_live`, inside the main loop, gated by elapsed time since last heartbeat emission.

---

## TradeLifecycleListener Implementation

### Class: `_LifecycleListener` (defined in `core/loop.py`)

```python
class _LifecycleListener:
    def __init__(self) -> None:
        self._last_sl: dict[str, float] = {}  # position_id → last known SL

    def on_trade_event(self, event: TradeEvent) -> None:
        pos = event.position

        # TRADE_MODIFY: detect SL change
        if event.kind == TradeLifecycleEvent.ON_PRICE_UPDATE:
            prev_sl = self._last_sl.get(pos.position_id)
            if prev_sl is not None and prev_sl != pos.stop_loss:
                emit_lifecycle_event("TRADE_MODIFY", {
                    "symbol": pos.symbol,
                    "position_id": pos.position_id,
                    "prev_sl": prev_sl,
                    "new_sl": pos.stop_loss,
                })
            self._last_sl[pos.position_id] = pos.stop_loss
            return

        # TRADE_EXIT: on close events
        if event.kind in (
            TradeLifecycleEvent.ON_STOP_LOSS_HIT,
            TradeLifecycleEvent.ON_TAKE_PROFIT_HIT,
            TradeLifecycleEvent.ON_MANAGEMENT_EXIT,
        ):
            exit_price = event.price_snapshot[0]  # bid
            duration = event.time_s - pos.open_time
            r_unit = abs(pos.entry_price - pos.initial_sl)
            pnl_r = ((exit_price - pos.entry_price) / r_unit) if r_unit > 0 else 0.0
            if pos.side == Side.SELL:
                pnl_r = -pnl_r

            emit_lifecycle_event("TRADE_EXIT", {
                "symbol": pos.symbol,
                "position_id": pos.position_id,
                "reason": event.kind.value,
                "entry": pos.entry_price,
                "exit": exit_price,
                "duration_s": f"{duration:.0f}",
                "pnl_r": f"{pnl_r:.2f}R",
            })
            self._last_sl.pop(pos.position_id, None)
            return

        # Track SL on open
        if event.kind == TradeLifecycleEvent.ON_TRADE_OPEN:
            self._last_sl[pos.position_id] = pos.stop_loss
```

### Wiring in `run_live`

```python
trade_manager = TradeStateManager(
    _build_trade_management_config(),
    listener=_LifecycleListener(),  # ← NEW
    execution=execution,
)
```

---

## Heartbeat Scheduling

Inside `run_live`, track last heartbeat time:

```python
_last_heartbeat_time = time.time()

# Inside the main loop, after candle processing:
now = time.time()
interval = getattr(config, "HEARTBEAT_INTERVAL_SECONDS", 300)
if now - _last_heartbeat_time >= interval:
    emit_lifecycle_event("HEARTBEAT", {
        "symbol": symbol,
        "uptime_s": f"{now - engine_start_mono:.0f}",
        "open_trades": len(trade_manager.positions_open()) if trade_manager else 0,
        "last_candle_time": closed_time,
        "iterations": iterations,
    })
    _last_heartbeat_time = now
```

---

## Payload Structure

### Standard Fields (all events)

| Field | Type | Description |
|---|---|---|
| `ts` | float | Unix timestamp (added by emitter) |
| `symbol` | str | Trading symbol |

### TRADE_SIGNAL Additional Fields

| Field | Type |
|---|---|
| `direction` | str (BUY/SELL) |
| `pattern` | str |
| `score` | int |
| `reason` | str |

### TRADE_ENTRY Additional Fields

| Field | Type |
|---|---|
| `direction` | str |
| `entry` | float |
| `sl` | float |
| `tp` | float |
| `volume` | float |
| `pattern` | str |
| `score` | int |

### TRADE_EXIT Additional Fields

| Field | Type |
|---|---|
| `position_id` | str |
| `reason` | str (on_stop_loss_hit / on_take_profit_hit / on_management_exit) |
| `entry` | float |
| `exit` | float |
| `duration_s` | str |
| `pnl_r` | str |

### TRADE_REJECT Additional Fields

| Field | Type |
|---|---|
| `direction` | str |
| `reason` | str |

### TRADE_MODIFY Additional Fields

| Field | Type |
|---|---|
| `position_id` | str |
| `prev_sl` | float |
| `new_sl` | float |

### HEARTBEAT Additional Fields

| Field | Type |
|---|---|
| `uptime_s` | str |
| `open_trades` | int |
| `last_candle_time` | int |
| `iterations` | int |

---

## Configuration Switches (added to `config.py`)

```python
ENABLE_TRADE_LIFECYCLE_LOGS = True
ENABLE_HEARTBEAT_LOGS = True
HEARTBEAT_INTERVAL_SECONDS = 300
```

---

## Event Deduplication Safeguards

| Event | Deduplication Mechanism |
|---|---|
| TRADE_SIGNAL | Emitted once per `should_trade=True` decision, inside the `if not decision.should_trade: continue` gate — only fires when execution path is entered |
| TRADE_ENTRY | Emitted once per `result.ok=True` — only one execution attempt per bar |
| TRADE_REJECT | Emitted once per `result.ok=False` — same single-attempt guarantee |
| TRADE_EXIT | Emitted on ON_STOP_LOSS_HIT / ON_TAKE_PROFIT_HIT / ON_MANAGEMENT_EXIT only — NOT on ON_TRADE_CLOSE (which fires as a secondary event after the specific close reason) |
| TRADE_MODIFY | Emitted only when `prev_sl != pos.stop_loss` — `_last_sl` dict tracks per-position |
| HEARTBEAT | Gated by `now - _last_heartbeat_time >= interval` — at most once per interval |

---

## File Impact Analysis

| File | Change Type | Description |
|---|---|---|
| `core/lifecycle_logger.py` | **New** | Central `emit_lifecycle_event` helper |
| `core/config.py` | Additive | 3 new switches |
| `core/loop.py` | Additive | `_LifecycleListener` class, TRADE_SIGNAL/ENTRY/REJECT emissions, heartbeat scheduling, listener wiring |
| `core/trade_management/manager.py` | None | Unchanged — already emits events to listener |
| `core/trade_management/events.py` | None | Unchanged — protocol already defined |
| `execution/mt5_execution.py` | None | Unchanged |
| `core/engine.py` | None | Unchanged |
| `strategy/` | None | Unchanged |
| `core/questions/` | None | Unchanged |

---

## Performance Constraints

- `emit_lifecycle_event` performs only: 2 `getattr` checks, 1 `time.time()`, string formatting, 1 `logger.info` call
- No SL/TP recalculation — reads `pos.stop_loss`, `pos.entry_price` directly
- No iteration over positions — listener receives individual events
- `_last_sl` dict is bounded by open position count (typically 1)
- Exception isolation via try/except — logging failures cannot interrupt execution

---

## Failure Isolation

```python
try:
    # format + emit
except Exception:
    logger.debug("lifecycle_logger: emission failed", exc_info=True)
```

If `emit_lifecycle_event` raises for any reason (formatting error, logger misconfiguration), the exception is caught, logged at DEBUG level, and execution continues uninterrupted.

---

## Future Extension Points

The `emit_lifecycle_event` function is the single point where all lifecycle facts pass through. Future integrations attach here:

```python
def emit_lifecycle_event(event_type: str, data: dict[str, Any]) -> None:
    # ... existing gating + formatting ...
    logger.info("[%s] %s", event_type, payload)

    # Future: alert dispatcher
    # if _alert_dispatcher is not None:
    #     _alert_dispatcher.dispatch(event_type, data)
```

Potential integrations:
- **Telegram/Discord alerts** — filter on TRADE_ENTRY/TRADE_EXIT only
- **Dashboard websocket** — push all events to a local web UI
- **Trade journal CSV** — append TRADE_ENTRY + TRADE_EXIT pairs to file
- **Metrics (Prometheus/StatsD)** — increment counters per event type
- **Persistent storage** — SQLite/JSON append for post-session analysis

---

## Assumptions

1. `TradeStateManager` continues to emit `ON_PRICE_UPDATE` on every tick — this is where SL change detection occurs
2. Only one position per symbol is open at a time (`MAX_OPEN_POSITIONS = 1`) — `_last_sl` dict stays small
3. `ON_TRADE_CLOSE` always fires after the specific close reason event — we emit TRADE_EXIT on the specific event, not on ON_TRADE_CLOSE, to avoid duplicates
4. The existing `_emit_event` system for ESSENTIAL events (BIAS_CHANGE, SETUP_FOUND, etc.) remains unchanged — lifecycle events are a parallel stream

---

## Risks / Tradeoffs

| Risk | Mitigation |
|---|---|
| `ON_PRICE_UPDATE` fires every tick — SL comparison runs every tick | Single dict lookup + float comparison — negligible cost |
| `_last_sl` grows if positions are never cleaned | Cleaned on TRADE_EXIT (`.pop(position_id)`) |
| Heartbeat in live loop adds a `time.time()` call per iteration | Only the comparison; actual emission is gated by interval |
| Lifecycle logger adds import to loop.py | Single import, no circular dependency risk |

---

## Components and Interfaces

### `core/lifecycle_logger.py` (New)

```python
def emit_lifecycle_event(event_type: str, data: dict[str, Any]) -> None: ...
```

- Accepts event type string and payload dict
- Gates on `ESSENTIAL_LOGS`, `ENABLE_TRADE_LIFECYCLE_LOGS`, `ENABLE_HEARTBEAT_LOGS`
- Formats as `[EVENT_TYPE] ts=... key=value key=value`
- Routes to `logging.getLogger("core.lifecycle")`
- Exception-safe (catches all errors internally)

### `_LifecycleListener` (in `core/loop.py`)

```python
class _LifecycleListener:
    def __init__(self) -> None: ...
    def on_trade_event(self, event: TradeEvent) -> None: ...
```

- Implements `TradeLifecycleListener` protocol
- Tracks `_last_sl` per position for TRADE_MODIFY detection
- Emits TRADE_EXIT on close events, TRADE_MODIFY on SL changes
- Cleans up state on position close

### Integration Interface

- `run_live` passes `_LifecycleListener()` to `TradeStateManager(listener=...)`
- `run_live` calls `emit_lifecycle_event` at TRADE_SIGNAL, TRADE_ENTRY, TRADE_REJECT points
- Heartbeat scheduled via elapsed-time check in the main loop

---

## Data Models

### LifecycleEvent (conceptual — not a class, just the dict contract)

All events share:
```
ts: float          — Unix timestamp (added by emitter)
symbol: str        — Trading symbol
```

Event-specific fields are documented in the Payload Structure section above.

### _LifecycleListener Internal State

```python
_last_sl: dict[str, float]   — Maps position_id to last known stop_loss value
```

Bounded by `MAX_OPEN_POSITIONS` (currently 1). Cleaned on TRADE_EXIT.

---

## Correctness Properties

### Property 1: No Duplicate TRADE_EXIT
For any position, exactly one TRADE_EXIT event is emitted — on the first close-reason event (ON_STOP_LOSS_HIT, ON_TAKE_PROFIT_HIT, ON_MANAGEMENT_EXIT), not on the subsequent ON_TRADE_CLOSE.

### Property 2: No Duplicate TRADE_MODIFY
For any position on any single price update, at most one TRADE_MODIFY is emitted — only when `_last_sl[pid] != pos.stop_loss`.

### Property 3: No Duplicate TRADE_SIGNAL
At most one TRADE_SIGNAL per bar per symbol — gated by the existing `if not decision.should_trade: continue` check.

### Property 4: Passivity
`emit_lifecycle_event` never modifies any Position, Decision, ExecutionResult, or EngineState field.

### Property 5: Exception Safety
Any exception in `emit_lifecycle_event` is caught and logged at DEBUG level without propagating to the caller.

---

## Error Handling

- `emit_lifecycle_event` wraps all logic in `try/except Exception`
- On failure: logs at DEBUG level with `exc_info=True`, then returns silently
- The caller (loop.py or _LifecycleListener) never sees the exception
- Trade processing continues uninterrupted regardless of logging failures
- If `config` attributes are missing, `getattr(..., default)` provides safe fallbacks

---

## Testing Strategy

1. **Unit test `emit_lifecycle_event`**: Mock the logger, verify correct formatting and gating for each switch combination.
2. **Unit test `_LifecycleListener`**: Construct with mock `emit_lifecycle_event`, feed synthetic `TradeEvent` objects, verify correct event types and payloads emitted.
3. **Integration test**: Run `run_replay` with `TradeStateManager` wired (future), verify TRADE_ENTRY + TRADE_EXIT pairs match expected count.
4. **Deduplication test**: Feed multiple ON_PRICE_UPDATE events with same SL — verify no TRADE_MODIFY emitted. Change SL — verify exactly one TRADE_MODIFY.
5. **Exception safety test**: Patch logger to raise, verify `emit_lifecycle_event` does not propagate and returns normally.
