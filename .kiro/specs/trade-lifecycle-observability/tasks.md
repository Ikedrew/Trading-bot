# Implementation Plan: Trade Lifecycle Observability

## Overview

Adds a passive, structured lifecycle logging layer to the MK1 trading bot. Implementation is strictly incremental and additive: configuration switches first, then the central emitter module, then the listener class, then event insertion points in the loop, then heartbeat scheduling, and finally validation. No existing strategy, execution, or trade management logic is modified.

## Tasks

- [ ] 1. Add configuration switches to `core/config.py`
  - [ ] 1.1 Add ENABLE_TRADE_LIFECYCLE_LOGS, ENABLE_HEARTBEAT_LOGS, and HEARTBEAT_INTERVAL_SECONDS to `core/config.py`
    - Add `ENABLE_TRADE_LIFECYCLE_LOGS = True` boolean switch
    - Add `ENABLE_HEARTBEAT_LOGS = True` boolean switch
    - Add `HEARTBEAT_INTERVAL_SECONDS = 300` integer parameter
    - Place after the existing `DRY_RUN_EXECUTION_LOGS` switch block with a comment header `# --- Trade lifecycle observability ---`
    - _Requirements: 8.1, 8.2, 8.3_

- [ ] 2. Create central lifecycle event emitter module
  - [ ] 2.1 Create `core/lifecycle_logger.py` with `emit_lifecycle_event` function
    - Create new file `core/lifecycle_logger.py`
    - Import `time`, `logging`, `typing.Any`, and `core.config`
    - Define `logger = logging.getLogger("core.lifecycle")`
    - Implement `emit_lifecycle_event(event_type: str, data: dict[str, Any]) -> None`
    - Wrap entire body in `try/except Exception` with `logger.debug("lifecycle_logger: emission failed for %s", event_type, exc_info=True)` on failure
    - Gate on `ESSENTIAL_LOGS` first (return if False)
    - Gate HEARTBEAT events on `ENABLE_HEARTBEAT_LOGS`
    - Gate all other events on `ENABLE_TRADE_LIFECYCLE_LOGS`
    - Attach `ts=time.time()` as first field
    - Format data dict as space-separated `key=value` pairs
    - Emit via `logger.info("[%s] %s", event_type, payload)`
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 8.4, 8.5, 11.1, 11.5, 11.6, 11.7_

  - [ ]* 2.2 Write unit tests for `emit_lifecycle_event`
    - **Property 5: Exception Safety**
    - **Validates: Requirements 1.5, 1.6, 11.5**
    - Test that when logger raises, function returns without propagating
    - Test gating: ESSENTIAL_LOGS=False suppresses all events
    - Test gating: ENABLE_TRADE_LIFECYCLE_LOGS=False suppresses non-heartbeat events
    - Test gating: ENABLE_HEARTBEAT_LOGS=False suppresses HEARTBEAT events
    - Test formatting: verify key=value output format with timestamp

- [ ] 3. Implement `_LifecycleListener` class in `core/loop.py`
  - [ ] 3.1 Add `_LifecycleListener` class to `core/loop.py`
    - Add import of `emit_lifecycle_event` from `core.lifecycle_logger`
    - Add import of `TradeLifecycleEvent` from `core.trade_management.events` (if not already imported)
    - Define `_LifecycleListener` class with `__init__` initialising `self._last_sl: dict[str, float] = {}`
    - Implement `on_trade_event(self, event: TradeEvent) -> None`
    - On `ON_PRICE_UPDATE`: compare `_last_sl.get(pos.position_id)` with `pos.stop_loss`; if different and prev is not None, emit `TRADE_MODIFY` with symbol, position_id, prev_sl, new_sl; always update `_last_sl`
    - On `ON_STOP_LOSS_HIT`, `ON_TAKE_PROFIT_HIT`, `ON_MANAGEMENT_EXIT`: emit `TRADE_EXIT` with symbol, position_id, reason (event.kind.value), entry, exit (bid from price_snapshot), duration_s, pnl_r; pop position from `_last_sl`
    - On `ON_TRADE_OPEN`: record initial SL in `_last_sl`
    - Ignore `ON_TRADE_CLOSE` and `ON_PARTIAL_CLOSE` to avoid duplicate exits
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 6.1, 6.2, 6.3, 9.2, 9.3, 9.4, 10.4, 10.5, 11.2, 11.3, 11.8_

  - [ ]* 3.2 Write unit tests for `_LifecycleListener`
    - **Property 1: No Duplicate TRADE_EXIT**
    - **Property 2: No Duplicate TRADE_MODIFY**
    - **Validates: Requirements 4.1, 6.1, 10.4, 10.5**
    - Test that ON_STOP_LOSS_HIT emits exactly one TRADE_EXIT
    - Test that ON_TRADE_CLOSE after ON_STOP_LOSS_HIT does NOT emit a second TRADE_EXIT
    - Test that repeated ON_PRICE_UPDATE with same SL emits zero TRADE_MODIFY
    - Test that ON_PRICE_UPDATE with changed SL emits exactly one TRADE_MODIFY

- [ ] 4. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 5. Insert lifecycle event emissions in `run_live`
  - [ ] 5.1 Add TRADE_SIGNAL emission in `run_live`
    - After `if not decision.should_trade or decision.intent is None: continue` and before execution, insert `emit_lifecycle_event("TRADE_SIGNAL", {...})` with symbol, direction, pattern, score, reason
    - Place after the existing `_emit_trade_events(..., execution_ok=None)` call
    - _Requirements: 2.1, 2.2, 2.3, 10.1_

  - [ ] 5.2 Add TRADE_ENTRY emission in `run_live`
    - After `result.ok` block, after `register_from_execution` and existing `_emit_trade_events(..., execution_ok=True)`, insert `emit_lifecycle_event("TRADE_ENTRY", {...})` with symbol, direction, entry, sl, tp, volume, pattern, score
    - _Requirements: 3.1, 3.2, 3.3, 10.2_

  - [ ] 5.3 Add TRADE_REJECT emission in `run_live`
    - In the `else` block (result.ok=False), after existing `_emit_trade_events(..., execution_ok=False)`, insert `emit_lifecycle_event("TRADE_REJECT", {...})` with symbol, direction, reason
    - _Requirements: 5.1, 5.3, 10.3_

  - [ ] 5.4 Wire `_LifecycleListener` into `TradeStateManager` in `run_live`
    - Where `TradeStateManager` is instantiated, pass `listener=_LifecycleListener()` as the listener parameter
    - This replaces the default `None` listener with the lifecycle observer
    - _Requirements: 9.1, 9.4_

- [ ] 6. Add HEARTBEAT scheduling to `run_live`
  - [ ] 6.1 Implement heartbeat emission logic in `run_live` main loop
    - Add `_last_heartbeat_time = time.time()` and `engine_start_mono = time.time()` before the while loop
    - Inside the main loop, after candle processing and decision handling, add elapsed-time check: `if now - _last_heartbeat_time >= getattr(config, "HEARTBEAT_INTERVAL_SECONDS", 300)`
    - On trigger, call `emit_lifecycle_event("HEARTBEAT", {...})` with symbol, uptime_s, open_trades, last_candle_time, iterations
    - Update `_last_heartbeat_time = now`
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 10.1_

- [ ] 7. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 8. Validation and integration verification
  - [ ]* 8.1 Write integration test verifying no strategy logic changes
    - **Property 4: Passivity**
    - **Validates: Requirements 11.1, 11.2, 11.3**
    - Verify `emit_lifecycle_event` does not modify Decision, OrderIntent, Position, or ExecutionResult objects
    - Verify `_LifecycleListener` does not call any execution method

  - [ ]* 8.2 Write deduplication tests
    - **Property 3: No Duplicate TRADE_SIGNAL**
    - **Validates: Requirements 10.1, 10.2, 10.3**
    - Verify at most one TRADE_SIGNAL per bar
    - Verify at most one TRADE_ENTRY per successful execution
    - Verify at most one TRADE_REJECT per failed execution

- [ ] 9. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design
- Unit tests validate specific examples and edge cases
- All changes are strictly additive — no existing strategy, execution, or trade management logic is modified
- Implementation order: config → emitter module → listener class → event insertion points → heartbeat → validation
- The `MT5_RECONNECT` event type (Requirement 12) is recognised by the emitter but implementation is deferred to a future iteration per the requirements

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["2.1"] },
    { "id": 2, "tasks": ["2.2", "3.1"] },
    { "id": 3, "tasks": ["3.2", "5.1", "5.2", "5.3", "5.4"] },
    { "id": 4, "tasks": ["6.1"] },
    { "id": 5, "tasks": ["8.1", "8.2"] }
  ]
}
```
