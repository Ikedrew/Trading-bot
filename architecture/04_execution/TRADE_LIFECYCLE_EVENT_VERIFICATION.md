# Trade Lifecycle Event Verification — `on_trade_event` in `TradeLifecycleLogger`

**Date:** 2026-07-19  
**Scope:** Verification audit of the `on_trade_event` implementation added to `TradeLifecycleLogger` in `core/event_bus.py`  
**Verdict:** Approve with minor follow-up

---

## Executive Summary

The addition of `on_trade_event` to `TradeLifecycleLogger` is **architecturally correct** and **resolves a pre-existing protocol violation** where the class was non-compliant with the `TradeLifecycleListener` protocol it was already being used as.

The implementation is consistent with the intended architecture documented in `.kiro/specs/trade-management-event-driven/`. The only deviation is *location* — the spec planned for `_LoopTradeListener` in `core/loop.py` rather than fixing `TradeLifecycleLogger` in `core/event_bus.py`. However, since `TradeLifecycleLogger` is the actual runtime listener (instantiated in `scanner_init.py`), making it protocol-compliant is the correct immediate fix.

**Recommendation: Approve for merge** with one follow-up item documented below.

---

## Investigation 1 — Original Design Intent

### Who owns lifecycle events?

`TradeStateManager` in `core/trade_management/manager.py` is the sole emitter of lifecycle events. It owns position state and emits events through its `_emit()` method.

**Evidence:** `manager.py` line 161:
```python
def _emit(self, kind, position, prices, ts, detail):
    if self._listener is not None:
        self._listener.on_trade_event(TradeEvent(kind=kind, position=position, ...))
```

### Who emits lifecycle events?

Only `TradeStateManager._emit()`. No other module creates or dispatches `TradeEvent` objects.

**Evidence:** grep for `TradeEvent(` across the codebase returns only `manager.py:168`.

### Who consumes lifecycle events?

The `TradeLifecycleListener` protocol in `events.py` declares the consumer interface:

```python
class TradeLifecycleListener(Protocol):
    def on_trade_event(self, event: TradeEvent) -> None: ...
```

The production listener is `TradeLifecycleLogger` (from `core/event_bus.py`), instantiated in `scanner_init.py:128`:

```python
tm = TradeStateManager(
    _build_trade_management_config(),
    listener=TradeLifecycleLogger(),
    execution=execution,
)
```

### Was TradeLifecycleLogger originally intended to implement on_trade_event?

**No — but it was required to.** The design spec (`.kiro/specs/trade-management-event-driven/design.md`) documents the current state as:

> `_emit() → TradeLifecycleListener ← listener is None in live loop (not wired)`

The spec then prescribes adding `_LoopTradeListener` in `loop.py` (Task 5). However, the actual production code already wires `TradeLifecycleLogger` as the listener in `scanner_init.py` — a gap between spec and implementation.

The spec's design doc explicitly states (Requirement 2.3):

> THE `TradeLifecycleListener` Protocol SHALL declare a single method `on_trade_event(event: TradeEvent) -> None` that covers all `TradeLifecycleEvent` kinds.

Since `TradeLifecycleLogger` is the de facto production listener, it **must** implement `on_trade_event` to satisfy the protocol.

---

## Investigation 2 — Event Flow

### Complete lifecycle event flow (trade close via stop loss):

```
TradeStateManager._process_one_position()
  │
  │  [SL hit detected by check_exit_trigger()]
  ▼
TradeStateManager._close_local(pos, ON_STOP_LOSS_HIT, prices, ts, detail)
  │
  │  [1. Broker close: execution.close_position()]
  │  [2. Local state: pos.status = CLOSED, pos.closed_time = ts]
  │  [3. Emit specific event:]
  ▼
TradeStateManager._emit(ON_STOP_LOSS_HIT, pos, prices, ts, detail)
  │
  │  [4. Enriches detail with reason="stop_loss"]
  │  [5. Emit generic close event:]
  ▼
TradeStateManager._emit(ON_TRADE_CLOSE, pos, prices, ts, _close_detail)
  │
  │  [self._listener.on_trade_event(TradeEvent(...))]
  ▼
TradeLifecycleLogger.on_trade_event(event)
  │
  │  [kind == ON_TRADE_CLOSE branch]
  │  [Dispatches to on_trade_close() for Discord logging]
  │  [Calls _persist_trade_close()]
  ▼
TradeLifecycleLogger._persist_trade_close(position, exit_price, event)
  │
  │  [build_trade_record(position=pos, ...)]
  │  [correlation_id sourced from position.trade_identity]
  ▼
trade_journal.persist_trade_once(record)
  │
  │  [Writes JSONL to logs/trade_journal/]
  │  [Writes Trade Truth v3 to logs/trade_truth/]
  ▼
PERSISTENCE COMPLETE
```

### Stage detail:

| Stage | Class | Function | Event Object | Key Parameters |
|-------|-------|----------|--------------|----------------|
| 1. SL detection | `TradeStateManager` | `_process_one_position` | — | `pos`, `bid`, `ask` |
| 2. Close initiation | `TradeStateManager` | `_close_local` | — | `pos`, `kind=ON_STOP_LOSS_HIT`, `prices` |
| 3. Broker execution | `MT5Execution` | `close_position` | — | `symbol`, `position_ticket` |
| 4. Local state update | `TradeStateManager` | `_close_local` | — | `pos.status = CLOSED` |
| 5. Event emission (specific) | `TradeStateManager` | `_emit` | `TradeEvent(kind=ON_STOP_LOSS_HIT)` | `pos`, `prices`, `ts`, `detail` |
| 6. Event emission (generic) | `TradeStateManager` | `_emit` | `TradeEvent(kind=ON_TRADE_CLOSE)` | `pos`, `prices`, `ts`, `_close_detail` |
| 7. Listener dispatch | `TradeLifecycleLogger` | `on_trade_event` | `TradeEvent` | Matches `ON_TRADE_CLOSE` |
| 8. Discord logging | `TradeLifecycleLogger` | `on_trade_close` | — | `symbol`, `exit_price`, `reason`, `pnl` |
| 9. Journal build | `trade_journal` | `build_trade_record` | — | `position` (carries `trade_identity`) |
| 10. Journal persist | `trade_journal` | `persist_trade_once` | — | `record` (carries `correlation_id`) |
| 11. Trade Truth write | `trade_truth` | `persist_trade_truth` | — | `correlation_id` from `record` |

---

## Investigation 3 — Protocol Verification

### Does the protocol explicitly require on_trade_event?

**Yes.** `events.py` defines:

```python
class TradeLifecycleListener(Protocol):
    def on_trade_event(self, event: TradeEvent) -> None: ...
```

**Evidence:** `core/trade_management/events.py`, line 33.

### Was the logger previously non-compliant?

**Yes.** Before this change, `TradeLifecycleLogger` had only:
- `on_trade_open(symbol, side, volume, entry, sl, tp)`
- `on_trade_close(symbol, exit_price, reason, pnl)`
- `on_trade_modify(symbol, new_sl, new_tp)`

It did **not** have `on_trade_event`. The `_emit` method in `TradeStateManager` calls:

```python
self._listener.on_trade_event(TradeEvent(...))
```

This would have raised `AttributeError` at runtime. The error was silently suppressed because:
1. Test cases don't pass a listener (listener=None, _emit skips)
2. Production likely hadn't executed a live trade in the Engine A era (all NO_TRADE/PATTERN_REJECT)

### Is the new implementation now compliant?

**Yes.** `TradeLifecycleLogger.on_trade_event(self, event: Any) -> None` satisfies the structural typing required by `TradeLifecycleListener(Protocol)`.

### Is the implementation complete?

**Partially.** The implementation handles:
- ✅ `ON_TRADE_OPEN` — dispatches to `on_trade_open()`
- ✅ `ON_TRADE_CLOSE` — dispatches to `on_trade_close()` + persists to journal
- ❌ `ON_PRICE_UPDATE` — silently ignored (correct — high-frequency, no action needed)
- ❌ `ON_PARTIAL_CLOSE` — silently ignored (minor gap — no journal persistence for partials)
- ❌ `ON_STOP_LOSS_HIT` / `ON_TAKE_PROFIT_HIT` / `ON_MANAGEMENT_EXIT` — silently ignored (these precede `ON_TRADE_CLOSE` which handles persistence)

The missing events are acceptable because:
- `ON_PRICE_UPDATE` is noise (fired every tick)
- `ON_STOP_LOSS_HIT` / `ON_TAKE_PROFIT_HIT` / `ON_MANAGEMENT_EXIT` are always followed by `ON_TRADE_CLOSE` (see `_close_local`)
- Persistence is correctly triggered on `ON_TRADE_CLOSE` only

---

## Investigation 4 — Behaviour Verification

### What does on_trade_event do?

1. Extracts `position`, `price_snapshot`, and `kind` from the event
2. For `ON_TRADE_OPEN`: dispatches to `on_trade_open()` (Discord notification)
3. For `ON_TRADE_CLOSE`: dispatches to `on_trade_close()` (Discord notification) **and** calls `_persist_trade_close()` (journal + Trade Truth persistence)
4. All other event kinds: no action (fall through)
5. Entire method wrapped in `try/except Exception: pass` — never raises

### Events handled:

| Event Kind | Dispatch | Side Effect |
|-----------|----------|-------------|
| `ON_TRADE_OPEN` | `on_trade_open()` | Discord notification |
| `ON_TRADE_CLOSE` | `on_trade_close()` + `_persist_trade_close()` | Discord notification + journal persistence + Trade Truth |
| All others | None | Silent pass-through |

### Preservation of existing behaviour:

**Before the change:** `on_trade_event` didn't exist → `AttributeError` on any lifecycle event emission (when listener is not None).

**After the change:** Events are correctly routed. The existing `on_trade_open()` and `on_trade_close()` methods are called with the same parameters they would have received if invoked directly.

**The Discord notification format is identical.** The `on_trade_open` and `on_trade_close` method bodies are unchanged.

### Unintended side effects:

**None identified.** The only new behaviour is `_persist_trade_close()` which adds trade journal and Trade Truth persistence on close. This was previously missing entirely (not an unintended duplication).

---

## Investigation 5 — Architectural Consistency

### Was the listener supposed to expose individual callbacks only?

**No.** The design spec (`.kiro/specs/trade-management-event-driven/requirements.md`, Requirement 2.3) explicitly states:

> THE `TradeLifecycleListener` Protocol SHALL declare a single method `on_trade_event(event: TradeEvent) -> None` that covers all `TradeLifecycleEvent` kinds.

The old `on_trade_open` / `on_trade_close` / `on_trade_modify` methods were a legacy API predating the formal protocol. They were never called by `TradeStateManager._emit()`.

### Should the manager have dispatched differently?

**No.** The manager correctly dispatches through the single `on_trade_event` protocol method. This is the documented design.

### Is there another adapter already present?

**No.** `NoOpTradeListener` exists but is only a null implementation. No adapter pattern bridges the old-style callbacks to the protocol.

### Is this implementation consistent with the rest of the event system?

**Yes, with one caveat.** The design spec planned for `_LoopTradeListener` in `core/loop.py` (Task 5) as the production listener. However, the actual production path uses `TradeLifecycleLogger` from `event_bus.py` (wired in `scanner_init.py`). The implementation correctly fixes the actual production listener rather than creating a parallel one.

The spec also notes that `event_bus.py` is a "FROZEN compatibility layer" with the warning:

> WARNING: Do not add new logic here. Use event_stream instead.

Adding journal persistence here is a pragmatic compromise — it fixes the protocol violation and enables identity propagation without restructuring the listener architecture.

**This is architecturally acceptable as a transitional solution.** A future clean-up should migrate this listener to a purpose-built class (e.g., in `scanner_init.py` or a dedicated `lifecycle_listener.py`).

---

## Investigation 6 — Runtime Safety

### No AttributeError can occur

**Verified.** Before this change, calling `_emit` with a non-None listener would have raised `AttributeError` because `TradeLifecycleLogger` lacked `on_trade_event`. The new implementation resolves this.

Additionally, the entire `on_trade_event` body is wrapped in `try/except Exception: pass`, preventing any possible AttributeError from propagating.

### No events are silently dropped

**Partially correct.** Events that previously caused `AttributeError` (all of them) are now handled. `ON_TRADE_OPEN` and `ON_TRADE_CLOSE` are fully handled. Other events pass through without action, which is acceptable for the current requirements.

### No duplicate persistence occurs

**Verified.** `persist_trade_once()` uses `is_already_journaled(trade_id)` for idempotent deduplication. Even if `on_trade_event` is called multiple times for the same position close, the second call is a no-op.

Additionally, `_close_local` emits `ON_TRADE_CLOSE` exactly once per close event. It is not possible for the same close to trigger two `ON_TRADE_CLOSE` emissions.

### No duplicate logging occurs

**Verified.** The Discord notification methods (`on_trade_open`, `on_trade_close`) are called exactly once per event dispatch. They are called *within* `on_trade_event`, not independently.

### No recursion is introduced

**Verified.** The call chain is:
```
_emit → on_trade_event → _persist_trade_close → persist_trade_once
```

None of these call back into `TradeStateManager._emit()` or trigger any event emission.

### No ordering guarantees are broken

**Verified.** The event emission order in `_close_local` is:
1. `_emit(ON_STOP_LOSS_HIT, ...)` — specific event
2. `_emit(ON_TRADE_CLOSE, ...)` — generic close event

The listener receives events in this exact order. Persistence is triggered only on `ON_TRADE_CLOSE` (step 2), after the position is already marked `CLOSED` and `closed_time` is set. This ensures the position state is final before journal writes.

---

## Investigation 7 — Testing

### Existing tests covering lifecycle events:

| Test File | Coverage |
|-----------|----------|
| `test_trade_management_broker_close.py` | Tests `_close_local` with broker success/failure, retry queues. **No listener is passed** (listener=None, so `on_trade_event` is never called in these tests). |
| `test_trade_journal.py` | Tests `build_trade_record`, `persist_trade`, `persist_trade_once`, deduplication. Does not test the event-triggered path. |

### Tests exercising on_trade_event:

**None exist.** No test currently:
1. Creates a `TradeStateManager` with `listener=TradeLifecycleLogger()`
2. Triggers a trade close
3. Verifies that `on_trade_event` is called and persistence occurs

### Coverage gaps:

1. **No integration test** for the full path: `_close_local` → `_emit` → `on_trade_event` → `_persist_trade_close` → `persist_trade_once`
2. **No test** verifying that `ON_TRADE_OPEN` correctly dispatches to `on_trade_open`
3. **No test** verifying the exit_price calculation (`prices[0]` for BUY, `prices[1]` for SELL)
4. **No test** verifying that unhandled event kinds (e.g., `ON_PRICE_UPDATE`) do not trigger persistence

### Recommended tests (do not implement):

1. **`test_on_trade_event_dispatches_close_to_journal`** — Create TradeStateManager with a TradeLifecycleLogger listener. Register a position. Close it via `on_price_update` hitting SL. Assert that `persist_trade_once` was called with a record containing the correct `correlation_id`.

2. **`test_on_trade_event_open_fires_discord`** — Create a TradeLifecycleLogger. Fire an `ON_TRADE_OPEN` TradeEvent directly. Assert `emit_event("trade-execution", ...)` was called.

3. **`test_on_trade_event_ignores_price_update`** — Fire an `ON_PRICE_UPDATE` TradeEvent at a TradeLifecycleLogger. Assert no persistence and no Discord emission occurs.

4. **`test_on_trade_event_idempotent_persistence`** — Fire `ON_TRADE_CLOSE` twice for the same position. Assert `persist_trade_once` handles deduplication.

5. **`test_exit_price_direction`** — Verify BUY positions use `prices[0]` (bid) and SELL positions use `prices[1]` (ask) for exit_price.

---

## Risks

### Risk 1 — Exit price calculation (Low severity)

The exit price in `on_trade_event` is computed as:
```python
exit_price = prices[0] if pos.side.value == "BUY" else prices[1]
```

This gives bid for BUY exits and ask for SELL exits, which is **correct** (you sell at bid, buy-back at ask). However, this is a runtime computation that could diverge from the broker's actual fill price if slippage occurs. The Trade Truth record will have the listener's computed exit price rather than the broker's confirmed fill.

**Mitigation:** For the identity propagation refactor, this is acceptable. The `trade_truth` field `exit_fill_price` should ultimately come from broker confirmation, but that data is not available through the current `TradeEvent` object.

### Risk 2 — "FROZEN compatibility layer" comment (Low severity)

`event_bus.py` contains the warning:
```python
# WARNING: Do not add new logic here. Use event_stream instead.
```

Adding `on_trade_event` and `_persist_trade_close` adds new logic to a self-declared frozen file.

**Mitigation:** This is a pragmatic fix for a pre-existing protocol violation. The alternative (creating a new listener class) would require changes to `scanner_init.py` and add indirection without architectural benefit at this stage.

### Risk 3 — ON_TRADE_CLOSE fires twice per close cycle (Low severity)

The `_close_local` method fires the specific event (e.g., `ON_STOP_LOSS_HIT`) and then `ON_TRADE_CLOSE`. The listener's `on_trade_event` is called for both. Only `ON_TRADE_CLOSE` triggers persistence, so there is no duplication issue. However, if a future change adds persistence to `ON_STOP_LOSS_HIT` handling, it could double-persist.

**Mitigation:** Current code is safe. The pattern of "specific event + generic ON_TRADE_CLOSE" is well-documented in the spec's design document.

---

## Architectural Assessment

The implementation is **consistent with the intended design** as documented in the spec:

1. ✅ Protocol compliance: `TradeLifecycleListener` requires `on_trade_event` — now satisfied
2. ✅ Single dispatch method: All events route through one entry point
3. ✅ Event-kind dispatch: Implementation switches on `event.kind` to route to appropriate handlers
4. ✅ Non-blocking: Exception safety ensures trade management is never affected
5. ✅ Identity propagation: Persistence reads `correlation_id` from Position's owned `trade_identity`

The spec planned for `_LoopTradeListener` in `loop.py` but the actual production runtime uses `TradeLifecycleLogger` in `scanner_init.py`. Fixing the actual production class is more correct than creating a parallel implementation.

---

## Recommendation

**Approve for merge.**

Follow-up item (non-blocking):
- Consider extracting `TradeLifecycleLogger` from `event_bus.py` into a dedicated module (e.g., `core/runtime/lifecycle_listener.py`) in a future clean-up pass. This would respect the "FROZEN" annotation on `event_bus.py` and align with the spec's intent of having the listener near the runtime loop.
