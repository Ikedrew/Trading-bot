# Technical Design — MT5 Runtime Hardening (Final Tier)

## Overview

Centralises MT5 terminal lifecycle ownership in `main.py`, adds startup account validation, symbol re-subscription after reconnect, and graceful SIGINT/SIGTERM shutdown handling. All changes are additive. The existing reconnect/recovery architecture from `mt5-runtime-survivability` continues to function — reconnect's internal init/shutdown cycle is explicitly permitted within the centralised ownership model.

---

## Architecture

### MT5 Lifecycle Ownership Model

```
main.py (OWNS MT5 LIFECYCLE)
  │
  ├── mt5.initialize()                    ← ONCE at process start
  ├── validate_account()                  ← startup checks
  │
  ├── for symbol in SYMBOLS:
  │     ├── MT5DataFeed(symbol)           ← no init/shutdown (centralised mode)
  │     │     └── resolve_symbol()        ← symbol_select per symbol
  │     ├── run_live(symbol=...)          ← runtime loop
  │     │     └── reconnect cycle         ← ALLOWED to call shutdown/init internally
  │     └── (next symbol)
  │
  ├── mt5.shutdown()                      ← ONCE at process exit
  └── sys.exit(0)

Signal Handler (SIGINT/SIGTERM):
  └── sets _shutdown_requested = True
      └── loop observes flag → clean exit → main.py calls mt5.shutdown()
```

### Key Ownership Rules

| Operation | Owner | When |
|---|---|---|
| `mt5.initialize()` (startup) | `main.py` | Once, before symbol loop |
| `mt5.shutdown()` (final) | `main.py` | Once, after all symbols complete |
| `mt5.shutdown()` + `mt5.initialize()` (reconnect) | `_attempt_reconnect` in `loop.py` | During RECOVERY only |
| `mt5.symbol_select()` | `MT5DataFeed.resolve_symbol()` | Per symbol, and after reconnect |
| Signal handling | `main.py` | Registered at startup |

---

## Components and Interfaces

### `main.py` — Centralised Lifecycle

```python
import signal
import MetaTrader5 as mt5

_shutdown_requested = False

def _signal_handler(signum, frame):
    global _shutdown_requested
    if _shutdown_requested:
        sys.exit(1)  # Force on second signal
    _shutdown_requested = True
    logger.info("[shutdown_signal_received] signal=%s", signal.Signals(signum).name)

def main():
    configure_logging()
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    if getattr(config, "MT5_CENTRALISED_INIT", True) and not config.REPLAY_MODE:
        if not mt5.initialize():
            logger.critical("[startup_validation_failed] reason=mt5_init_failed")
            sys.exit(1)
        if not validate_account():
            mt5.shutdown()
            sys.exit(1)

    try:
        symbols = getattr(config, "SYMBOLS", [config.SYMBOL])
        for symbol in symbols:
            if _shutdown_requested:
                break
            if config.REPLAY_MODE:
                run_replay(symbol=symbol)
            else:
                run_live(symbol=symbol, shutdown_flag=_shutdown_requested_ref)
    finally:
        if getattr(config, "MT5_CENTRALISED_INIT", True) and not config.REPLAY_MODE:
            mt5.shutdown()
            logger.info("[mt5_terminal_released]")
        logger.info("[shutdown_complete]")
```

### `validate_account()` — Startup Validation

```python
def validate_account() -> bool:
    logger.info("[startup_validation_started]")
    info = mt5.terminal_info()
    if info is None or not info.connected:
        logger.critical("[account_validation_failed] reason=terminal_not_connected")
        return False
    if not info.trade_allowed:
        logger.critical("[account_validation_failed] reason=trading_not_allowed")
        return False
    acct = mt5.account_info()
    if acct is None:
        logger.critical("[account_validation_failed] reason=account_info_none")
        return False
    logger.info("[account_validation_success] login=%d", acct.login)
    return True
```

### `MT5DataFeed` — Conditional Init/Shutdown

```python
class MT5DataFeed:
    def connect(self) -> None:
        if not getattr(config, "MT5_CENTRALISED_INIT", True):
            if not mt5.initialize():
                raise RuntimeError(...)

    def disconnect(self) -> None:
        if not getattr(config, "MT5_CENTRALISED_INIT", True):
            mt5.shutdown()
```

### `_attempt_reconnect` — Symbol Re-Subscription

```python
def _attempt_reconnect(symbol: str) -> bool:
    try:
        mt5.shutdown()
        time.sleep(delay)
        if not mt5.initialize():
            return False
        info = mt5.terminal_info()
        if info is None or not info.connected:
            return False
        # Symbol re-subscription
        logger.info("[symbol_resubscribe_started] symbol=%s", symbol)
        if not mt5.symbol_select(symbol, True):
            logger.info("[symbol_resubscribe_failed] symbol=%s", symbol)
            return False
        logger.info("[symbol_resubscribe_success] symbol=%s", symbol)
        return True
    except Exception:
        return False
```

### `run_live` — Shutdown Flag Observation

```python
def run_live(*, symbol=None, shutdown_flag=None, ...):
    ...
    while max_iterations is None or iterations < max_iterations:
        if shutdown_flag is not None and shutdown_flag():
            logger.info("[shutdown_initiated] trigger=signal symbol=%s", symbol)
            break
        ...
```

The `shutdown_flag` is a callable (e.g. `lambda: _shutdown_requested`) passed from main.py.

---

## Data Models

### Configuration Parameters (added to `config.py`)

```python
MT5_CENTRALISED_INIT = True
MT5_SHUTDOWN_GRACE_SECONDS = 10.0
```

### Lifecycle Events (new)

| Event | Level | When |
|---|---|---|
| `startup_validation_started` | INFO | Before account checks |
| `account_validation_success` | INFO | All startup checks pass |
| `account_validation_failed` | CRITICAL | Any startup check fails |
| `symbol_resubscribe_started` | INFO | After reconnect init succeeds |
| `symbol_resubscribe_success` | INFO | symbol_select returns True |
| `symbol_resubscribe_failed` | INFO | symbol_select returns False |
| `shutdown_signal_received` | INFO | SIGINT/SIGTERM caught |
| `shutdown_initiated` | INFO | Loop begins exit |
| `shutdown_loop_exited` | INFO | Loop iteration ends |
| `mt5_terminal_released` | INFO | mt5.shutdown() called |
| `shutdown_complete` | INFO | Process about to exit |

---

## Correctness Properties

### Property 1: Single Init/Shutdown Per Process
`mt5.initialize()` is called exactly once in `main.py` at startup. `mt5.shutdown()` is called exactly once in `main.py`'s `finally` block. Reconnect's internal init/shutdown is the only exception — it's bounded and occurs within RECOVERY state only.

**Validates: Requirements 3.1, 3.2**

### Property 2: Reconnect Compatibility
`_attempt_reconnect` continues to call `mt5.shutdown()` → `mt5.initialize()` internally. This is permitted because reconnect only fires during RECOVERY state, and the centralised owner (`main.py`) does not call shutdown/init during runtime — only at process boundaries.

**Validates: Requirements 3.5**

### Property 3: Shutdown Idempotency
The `finally` block in `main.py` calls `mt5.shutdown()` regardless of how the loop exited (signal, exhaustion, or normal completion). If reconnect already called shutdown during its last failed attempt, the extra `mt5.shutdown()` call is harmless (MT5 API tolerates redundant shutdown).

**Validates: Requirements 4.5**

### Property 4: No Execution After Signal
Once `_shutdown_requested` is True, the loop's first check at the top of each iteration breaks out. No `process_bar` or `place_market` calls occur after the flag is set.

**Validates: Requirements 4.4, 4.6**

### Property 5: Symbol Restoration Before Resume
After reconnect success, `mt5.symbol_select(symbol, True)` must succeed before the reconnect is considered complete. If it fails, the attempt is treated as failed and the next retry (or escalation) occurs.

**Validates: Requirements 1.1, 1.5**

---

## Error Handling

- `validate_account()` catches all exceptions and returns False — startup cannot partially succeed
- `_attempt_reconnect` is fully wrapped in try/except — reconnect cannot crash the runtime
- Signal handler sets a flag only — no complex logic in signal context
- Second SIGINT forces immediate exit via `sys.exit(1)` — escape hatch for stuck shutdowns
- `MT5_CENTRALISED_INIT = False` provides full backward compatibility rollback

---

## Testing Strategy

1. **Unit test `validate_account`**: Mock `mt5.terminal_info()` and `mt5.account_info()` — verify all failure paths return False and emit correct events
2. **Unit test signal handling**: Set `_shutdown_requested`, verify loop exits on next iteration
3. **Unit test symbol re-subscription**: Mock `mt5.symbol_select` to return False — verify reconnect attempt is treated as failed
4. **Integration test**: Run multi-symbol with `MT5_CENTRALISED_INIT=True` — verify single init/shutdown, no per-symbol cycling
5. **Rollback test**: Set `MT5_CENTRALISED_INIT=False` — verify existing per-symbol behaviour is preserved

---

## File Impact

| File | Change Type | Description |
|---|---|---|
| `main.py` | Modified | Add signal handlers, centralised init/shutdown, validate_account, shutdown_flag passing |
| `core/config.py` | Additive | 2 new parameters: `MT5_CENTRALISED_INIT`, `MT5_SHUTDOWN_GRACE_SECONDS` |
| `core/loop.py` | Modified | Add `shutdown_flag` parameter to `run_live`, add shutdown check at loop top, add symbol re-subscription to `_attempt_reconnect` |
| `data/mt5_data.py` | Modified | Gate `connect()`/`disconnect()` on `MT5_CENTRALISED_INIT` flag |
| `execution/mt5_execution.py` | None | Unchanged |
| `core/engine.py` | None | Unchanged |
| `strategy/` | None | Unchanged |
| `core/questions/` | None | Unchanged |

---

## Risks / Tradeoffs

| Risk | Mitigation |
|---|---|
| Reconnect's internal shutdown/init conflicts with central ownership | Explicitly permitted — reconnect only fires in RECOVERY, central owner only acts at process boundaries |
| Signal handler in Python is limited (only main thread) | Acceptable for synchronous single-process architecture |
| `MT5_CENTRALISED_INIT=True` breaks replay if replay needs MT5 | Gated: centralised init skipped when `REPLAY_MODE=True` |
| Second SIGINT forces `sys.exit(1)` — may skip cleanup | Intentional escape hatch for stuck processes |
| `mt5.shutdown()` called redundantly after failed reconnect + process exit | MT5 API tolerates redundant shutdown — no harm |
