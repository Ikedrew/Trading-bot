# Implementation Plan: MT5 Runtime Hardening (Final Tier)

## Overview

Safely migrates MT5 lifecycle ownership to `main.py`, adds startup validation, symbol re-subscription after reconnect, and graceful SIGINT/SIGTERM shutdown. Implementation is strictly ordered to prevent double-init, zombie states, or broken reconnect flows. The `MT5_CENTRALISED_INIT` toggle provides full rollback safety at every wave.

## Tasks

- [ ] 1. Add configuration parameters and feature flags
  - [ ] 1.1 Add hardening config switches to `core/config.py`
    - Add `MT5_CENTRALISED_INIT = True` (bool, enables centralised lifecycle ownership)
    - Add `MT5_SHUTDOWN_GRACE_SECONDS = 10.0` (float, max wait for reconnect to abort on shutdown)
    - Place after the existing `MT5_MAX_CANDLE_STALENESS_MULTIPLIER` block with comment header `# --- MT5 runtime hardening (final tier) ---`
    - _Requirements: 6.3, 6.4, 6.1, 6.2_

- [ ] 2. Add startup account validation function
  - [ ] 2.1 Implement `validate_account()` in `main.py`
    - Add `import MetaTrader5 as mt5` to main.py imports
    - Define `validate_account() -> bool` function before `main()`
    - Implementation: check `mt5.terminal_info()` is not None and `.connected` is True, check `.trade_allowed` is True, check `mt5.account_info()` is not None
    - On any failure: log critical event with reason, return False
    - On success: log `[account_validation_success]` with account login, return True
    - Wrap entire body in try/except — return False on any exception
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8_

- [ ] 3. Checkpoint — verify system still runs normally
  - Config added, validation function defined but not yet called. System behaviour unchanged. Verify replay and live modes still start correctly.

- [ ] 4. Centralise MT5 lifecycle ownership in `main.py`
  - [ ] 4.1 Add centralised `mt5.initialize()` and `mt5.shutdown()` to `main()`
    - In `main()`, after `configure_logging()`:
    - If `MT5_CENTRALISED_INIT` is True and `REPLAY_MODE` is False:
      - Call `mt5.initialize()` — if fails, log critical, `sys.exit(1)`
      - Call `validate_account()` — if fails, call `mt5.shutdown()`, `sys.exit(1)`
    - Wrap the symbol loop in `try/finally`
    - In `finally`: if `MT5_CENTRALISED_INIT` is True and `REPLAY_MODE` is False: call `mt5.shutdown()`, log `[mt5_terminal_released]`
    - Log `[shutdown_complete]` as final line
    - _Requirements: 3.1, 3.2, 3.6, 3.7_

- [ ] 5. Refactor MT5DataFeed to respect centralised ownership
  - [ ] 5.1 Gate `connect()` and `disconnect()` on `MT5_CENTRALISED_INIT` flag
    - In `data/mt5_data.py`, import `core.config` (or use getattr pattern)
    - In `connect()`: if `getattr(config, "MT5_CENTRALISED_INIT", True)` is True, skip `mt5.initialize()` call entirely (no-op)
    - In `disconnect()`: if `getattr(config, "MT5_CENTRALISED_INIT", True)` is True, skip `mt5.shutdown()` call entirely (no-op)
    - When `MT5_CENTRALISED_INIT` is False: retain existing behaviour unchanged (backward compatibility)
    - _Requirements: 3.3, 3.4, 3.8, 6.4_

- [ ] 6. Checkpoint — verify centralised lifecycle works
  - Verify: system starts with single init, runs multi-symbol without per-symbol cycling, shuts down with single shutdown. Verify `MT5_CENTRALISED_INIT=False` restores legacy behaviour.

- [ ] 7. Add symbol re-subscription to reconnect flow
  - [ ] 7.1 Add `mt5.symbol_select()` to `_attempt_reconnect` after successful init
    - In `core/loop.py`, inside `_attempt_reconnect(symbol)`:
    - After `mt5.initialize()` succeeds and terminal_info validates:
    - Log `[symbol_resubscribe_started] symbol=...`
    - Call `mt5.symbol_select(symbol, True)`
    - If fails: log `[symbol_resubscribe_failed]`, return False
    - If succeeds: log `[symbol_resubscribe_success]`, continue to return True
    - Wrap symbol_select in try/except — return False on exception
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8_

- [ ] 8. Add graceful shutdown signal handling
  - [ ] 8.1 Add signal handlers and shutdown flag to `main.py`
    - Add `import signal` to main.py
    - Define module-level `_shutdown_requested = False`
    - Define `_signal_handler(signum, frame)`:
      - If `_shutdown_requested` already True: `sys.exit(1)` (force on second signal)
      - Set `_shutdown_requested = True`
      - Log `[shutdown_signal_received] signal=...`
    - In `main()`, before the symbol loop: register `signal.signal(signal.SIGINT, _signal_handler)` and `signal.signal(signal.SIGTERM, _signal_handler)`
    - _Requirements: 4.1, 4.2, 4.3, 4.10_

  - [ ] 8.2 Add shutdown flag observation to `run_live`
    - Add `shutdown_flag: Callable[[], bool] | None = None` parameter to `run_live` signature
    - At the top of the `while` loop (first line inside): `if shutdown_flag is not None and shutdown_flag(): break`
    - Log `[shutdown_initiated] trigger=signal symbol=...` before breaking
    - In `main.py`, pass `shutdown_flag=lambda: _shutdown_requested` to `run_live()`
    - Also check `_shutdown_requested` between symbols in the for loop — skip remaining symbols if True
    - _Requirements: 4.3, 4.4, 4.6, 4.8, 4.9_

- [ ] 9. Checkpoint — verify shutdown works from all states
  - Verify: SIGINT during NORMAL exits cleanly, SIGINT during RECOVERY exits cleanly, second SIGINT forces exit, no traceback spam, mt5.shutdown() called exactly once.

- [ ] 10. Add lifecycle observability events
  - [ ] 10.1 Ensure all lifecycle events are emitted at correct points
    - Verify/add emissions for:
    - `startup_validation_started` — before validate_account checks
    - `account_validation_success` / `account_validation_failed` — in validate_account
    - `symbol_resubscribe_started/success/failed` — in _attempt_reconnect
    - `shutdown_signal_received` — in signal handler
    - `shutdown_initiated` — in run_live before break
    - `shutdown_loop_exited` — after while loop exits in run_live
    - `mt5_terminal_released` — in main.py finally block
    - `shutdown_complete` — final log in main.py
    - Use `logger.info` for normal events, `logger.critical` for failures
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_

- [ ] 11. Final checkpoint — full runtime validation
  - Verify: single init/shutdown enforcement, reconnect stability after centralisation, no zombie MT5 state, safe exit during RECOVERY, multi-symbol stability, replay mode compatibility, MT5_CENTRALISED_INIT=False rollback works. Ask the user if questions arise.

## Notes

- `MT5_CENTRALISED_INIT = False` provides full rollback at any wave — the system reverts to per-symbol init/shutdown behaviour
- Reconnect's internal `mt5.shutdown()` → `mt5.initialize()` cycle is explicitly permitted — it only fires during RECOVERY state when the central owner is not calling init/shutdown
- Signal handling uses a simple boolean flag — no complex async/thread coordination needed
- Second SIGINT forces `sys.exit(1)` — intentional escape hatch for stuck processes
- `mt5.shutdown()` is idempotent in the MT5 Python API — redundant calls are harmless
- Replay mode skips all MT5 lifecycle management (no init, no shutdown, no validation)
- The `shutdown_flag` callable pattern avoids sharing mutable global state directly with loop.py

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["2.1"] },
    { "id": 2, "tasks": ["4.1"] },
    { "id": 3, "tasks": ["5.1"] },
    { "id": 4, "tasks": ["7.1"] },
    { "id": 5, "tasks": ["8.1", "8.2"] },
    { "id": 6, "tasks": ["10.1"] }
  ]
}
```
