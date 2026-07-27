# Implementation Plan: MT5 Runtime Survivability

## Overview

Adds bounded reconnect, exception-safe guards, recovery state management, and post-reconnect stabilisation to the live loop. Implementation is strictly incremental: config first, then state scaffolding, then guards, then reconnect logic, then stabilisation, then validation. All changes are additive to `core/config.py` and `core/loop.py`. No strategy or execution logic is modified.

## Tasks

- [ ] 1. Add MT5 reconnect configuration parameters
  - [ ] 1.1 Add reconnect config switches to `core/config.py`
    - Add `MT5_MAX_RECONNECT_ATTEMPTS = 5` (int, max reconnect retries)
    - Add `MT5_RECONNECT_DELAY_SECONDS = 5.0` (float, base delay between attempts)
    - Add `MT5_RECONNECT_BACKOFF_ENABLED = True` (bool, exponential backoff toggle)
    - Add `MT5_MAX_TICK_STALENESS_SECONDS = 30.0` (float, max tick age for freshness check)
    - Add `MT5_MAX_CANDLE_STALENESS_MULTIPLIER = 2.0` (float, multiplier on timeframe interval for candle staleness threshold)
    - Place after the existing `DRY_RUN_EXECUTION_LOGS` switch block with comment header `# --- MT5 runtime survivability ---`
    - _Requirements: 6.1, 6.2, 6.3, 9.1, 11.2_

- [ ] 2. Add recovery state scaffolding to `run_live`
  - [ ] 2.1 Add recovery state variables inside `run_live` before the main loop
    - Add `recovery_state = "NORMAL"` (str flag: "NORMAL" or "RECOVERY")
    - Add `stabilising = False` (bool: post-reconnect stabilisation active)
    - Add `reconnect_count = 0` (int: current attempt counter)
    - Place after `engine_start_time = 0` and before `while max_iterations is None or iterations < max_iterations:`
    - These are local variables — they persist across loop iterations but reset per symbol run
    - _Requirements: 10.1, 10.2_

- [ ] 3. Add `_attempt_reconnect` helper function to `core/loop.py`
  - [ ] 3.1 Implement bounded reconnect helper
    - Define `_attempt_reconnect(symbol: str) -> bool` as a module-level function in `core/loop.py`
    - Implementation: `mt5.shutdown()` → `time.sleep(delay)` → `mt5.initialize()` → check `mt5.terminal_info().connected` → `mt5.symbol_select(symbol, True)`
    - Wrap entire body in `try/except Exception` — return False on any failure
    - Return True only if all steps succeed
    - Place before `run_replay` definition
    - _Requirements: 3.3, 5.5_

- [ ] 4. Checkpoint — verify system still runs normally
  - Ensure the system starts and runs in both replay and live mode without errors. The new config params and helper function should have zero runtime impact since nothing calls them yet.

- [ ] 5. Add proactive connection validation at loop top
  - [ ] 5.1 Add `terminal_info()` check at the start of each loop iteration in `run_live`
    - At the top of the `while` loop, before `bid, ask = feed.last_tick(symbol)`:
    - Call `info = mt5.terminal_info()`
    - If `info is None or not info.connected`:
      - If `recovery_state == "NORMAL"`: set `recovery_state = "RECOVERY"`, log `disconnect_detected` and `recovery_entered` events
      - `time.sleep(config.POLL_SECONDS)` then `continue` (skip this iteration)
    - If already in RECOVERY: skip duplicate detection, proceed to reconnect logic
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 10.3_

- [ ] 6. Add exception-safe guards around MT5 data calls
  - [ ] 6.1 Wrap `feed.last_tick()` and `feed.copy_rates_closed()` in try/except
    - Wrap the existing `bid, ask = feed.last_tick(symbol)` in `try/except RuntimeError`
    - Wrap the existing `candles = feed.copy_rates_closed(...)` in `try/except RuntimeError`
    - On exception: if `recovery_state == "NORMAL"`, set `recovery_state = "RECOVERY"`, log `disconnect_detected` and `recovery_entered`
    - `time.sleep(config.POLL_SECONDS)` then `continue`
    - Preserve EngineState, TradeStateManager, last_closed_time unchanged
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 5.1, 5.2_

- [ ] 7. Add reconnect orchestration logic
  - [ ] 7.1 Add reconnect execution block inside the RECOVERY state path
    - When `recovery_state == "RECOVERY"` at loop top (after terminal_info check or after exception):
    - If `reconnect_count >= MT5_MAX_RECONNECT_ATTEMPTS`: emit `reconnect_exhausted` + `runtime_shutdown_unrecoverable`, call `mt5.shutdown()`, `break` out of loop
    - Otherwise: emit `reconnect_started` with attempt number, call `_attempt_reconnect(symbol)`
    - If success: emit `reconnect_success`, set `stabilising = True`, reset `reconnect_count = 0`
    - If failure: emit `reconnect_failed`, increment `reconnect_count`, apply delay (with backoff if enabled)
    - `continue` after reconnect attempt (do not proceed to data fetch on same iteration)
    - _Requirements: 3.1, 3.2, 3.4, 3.5, 3.6, 3.7, 4.1, 4.2, 4.3, 4.4, 4.5, 6.4, 10.2_

- [ ] 8. Add post-reconnect stabilisation and tick freshness validation
  - [ ] 8.1 Add stabilisation cycle logic after successful reconnect
    - When `stabilising == True` at the point where data fetch succeeds:
    - Check tick freshness: call `mt5.symbol_info_tick(symbol)`, compare `tick.time` to `time.time()`
    - If `(time.time() - tick.time) > MT5_MAX_TICK_STALENESS_SECONDS`: emit `tick_stale_during_recovery` (once), `time.sleep(config.POLL_SECONDS)`, `continue`
    - If tick is fresh AND `copy_rates_closed` succeeded: set `stabilising = False`, set `recovery_state = "NORMAL"`, emit `recovery_stabilised` + `recovery_exited`
    - While stabilising: allow `trade_manager.on_price_update()` but suppress `process_bar` and execution
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 9.2, 9.3, 9.4, 9.5, 10.4, 10.5_

- [ ] 9. Add execution suppression during RECOVERY/stabilisation
  - [ ] 9.1 Gate trade execution on recovery state
    - Before the `if not decision.should_trade or decision.intent is None: continue` block:
    - Add check: `if recovery_state != "NORMAL" or stabilising: continue`
    - This ensures no `process_bar` evaluation or `place_market` calls occur during recovery
    - _Requirements: 8.2, 10.5, 5.6_

- [ ] 10. Checkpoint — verify reconnect flow works end-to-end
  - Ensure all tests pass. Verify: system enters RECOVERY on simulated disconnect, attempts bounded reconnect, stabilises after fresh tick, resumes normal execution. Ask the user if questions arise.

- [ ] 11. Add lifecycle event emissions for all recovery transitions
  - [ ] 11.1 Wire lifecycle events into recovery flow
    - Ensure all lifecycle events are emitted at correct points using `logger.info` or `logger.critical`:
    - `disconnect_detected` — on first MT5 failure or terminal disconnected
    - `recovery_entered` — on NORMAL → RECOVERY transition
    - `reconnect_started` — before each attempt
    - `reconnect_success` — on successful reconnect
    - `reconnect_failed` — on failed attempt
    - `tick_stale_during_recovery` — on stale tick (once per cycle)
    - `recovery_stabilised` — on stabilisation complete
    - `recovery_exited` — on RECOVERY → NORMAL transition
    - `reconnect_exhausted` — when max attempts reached
    - `runtime_shutdown_unrecoverable` — before clean exit
    - Use existing `_emit_event` or direct `logger.info`/`logger.critical` calls
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 3.2, 3.5_

- [ ] 12. Add candle staleness detection
  - [ ] 12.1 Add candle staleness monitoring to `run_live`
    - Add `last_new_candle_wall_time = time.time()` before the main loop
    - Add `candle_stale_emitted = False` flag
    - After `last_closed_time = closed_time` (new candle detected): update `last_new_candle_wall_time = time.time()`, and if `candle_stale_emitted`: emit `candle_stale_resolved` with duration, reset flag
    - In the `last_closed_time == closed_time` branch (no new candle): compute `elapsed = time.time() - last_new_candle_wall_time`
    - If `elapsed > _timeframe_seconds(config.TIMEFRAME) * MT5_MAX_CANDLE_STALENESS_MULTIPLIER` and not `candle_stale_emitted`: emit `candle_stale_detected` with elapsed and expected interval, set `candle_stale_emitted = True`
    - Do NOT trigger recovery — this is passive monitoring only
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7_

- [ ] 13. Final checkpoint — runtime validation
  - Ensure all tests pass. Verify: long-duration runtime stability, candle progression continuity after reconnect, no duplicate bars processed, no duplicate trades, clean shutdown on exhaustion, candle staleness detection fires correctly during market close. Ask the user if questions arise.

## Notes

- All changes are strictly additive — no existing strategy, execution, or trade management logic is modified
- Recovery state variables are local to `run_live` — they reset per symbol run (correct for sequential multi-symbol)
- `_attempt_reconnect` is exception-safe — it cannot crash the runtime
- The stabilisation cycle ensures no trading occurs on stale data after reconnect
- `last_closed_time` preservation prevents duplicate bar processing after recovery
- The `MT5_MAX_RECONNECT_ATTEMPTS = 0` edge case immediately escalates to shutdown (per Requirement 6.4)
- Lifecycle events integrate with the existing logger infrastructure — no new logging system required
- The `while running` placeholder was already removed — only the `while max_iterations` loop exists

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["2.1", "3.1"] },
    { "id": 2, "tasks": ["5.1", "6.1"] },
    { "id": 3, "tasks": ["7.1"] },
    { "id": 4, "tasks": ["8.1", "9.1"] },
    { "id": 5, "tasks": ["11.1", "12.1"] }
  ]
}
```
