# Requirements Document

## Introduction

MT5 Runtime Hardening (Final Tier) builds on the existing `mt5-runtime-survivability` spec to complete the MT5 lifecycle hardening. This spec covers four remaining areas: symbol re-subscription after reconnect, account validation on startup, centralised MT5 initialize/shutdown ownership, and graceful shutdown signal handling. Together these eliminate per-symbol init/shutdown cycling, add startup safety checks, ensure symbols are restored after reconnect, and allow the process to terminate cleanly on OS signals — all within the existing synchronous, single-process architecture.

## Glossary

- **Runtime**: The `run_live` loop in `core/loop.py` that polls MT5 for data and drives the trading pipeline
- **MT5_Terminal**: The MetaTrader 5 terminal process accessed via the `MetaTrader5` Python package
- **MT5DataFeed**: The data access layer (`data/mt5_data.py`) wrapping MT5 market data calls
- **EngineState**: Mutable runtime state (`core/engine_state.py`) carried across bars during live execution
- **TradeStateManager**: The trade management state tracker that monitors open positions post-entry
- **Lifecycle_Event**: A structured log event emitted through the logger infrastructure to record connection and runtime state transitions
- **Symbol_Restorer**: The component responsible for re-validating and re-selecting symbols after a successful reconnect
- **Account_Validator**: The component that checks MT5 account health (connectivity, trading permission) after initialization
- **Shutdown_Handler**: The component that intercepts OS termination signals and orchestrates a clean exit
- **Main_Process**: The top-level entry point (`main.py`) that owns the MT5 terminal lifecycle
- **Reconnect_Manager**: The bounded reconnect component defined in the mt5-runtime-survivability spec
- **RECOVERY_Mode**: The internal runtime state flag (from survivability spec) indicating active recovery

## Requirements

### Requirement 1: Symbol Re-Subscription After Reconnect

**User Story:** As a live trader, I want symbols to be re-validated and re-selected after a successful MT5 reconnect, so that the data feed is fully restored before execution resumes.

#### Acceptance Criteria

1. WHEN the Reconnect_Manager completes a successful `mt5.initialize()` cycle, THE Symbol_Restorer SHALL call `mt5.symbol_select(symbol, True)` for the active symbol before returning control to the Runtime
2. WHEN symbol re-subscription begins, THE Symbol_Restorer SHALL emit a Lifecycle_Event with event type `symbol_resubscribe_started` containing the target symbol name
3. WHEN symbol re-subscription succeeds, THE Symbol_Restorer SHALL emit a Lifecycle_Event with event type `symbol_resubscribe_success` containing the restored symbol name
4. WHEN symbol re-subscription fails, THE Symbol_Restorer SHALL emit a Lifecycle_Event with event type `symbol_resubscribe_failed` containing the symbol name and error details
5. IF symbol re-subscription fails, THEN THE Reconnect_Manager SHALL treat the reconnect attempt as failed and proceed to the next attempt or escalate
6. WHILE symbol re-subscription is in progress, THE Runtime SHALL suppress all trade execution and signal processing
7. WHEN multiple consecutive reconnect attempts occur, THE Symbol_Restorer SHALL emit at most one `symbol_resubscribe_started` event per reconnect attempt to prevent log spam
8. IF an exception occurs during symbol re-subscription, THEN THE Symbol_Restorer SHALL catch the exception and report failure without crashing the reconnect cycle

### Requirement 2: Account Validation on Startup

**User Story:** As a live trader, I want the runtime to validate that the MT5 account is operational after initialization, so that I do not attempt to trade on a disconnected or restricted account.

#### Acceptance Criteria

1. WHEN `mt5.initialize()` succeeds during startup, THE Account_Validator SHALL call `mt5.account_info()` and verify the result is not None
2. WHEN `mt5.account_info()` returns None, THE Account_Validator SHALL emit a Lifecycle_Event with event type `account_validation_failed` containing the error details and prevent the Runtime from starting
3. WHEN `mt5.account_info()` returns a valid result, THE Account_Validator SHALL verify that the terminal reports `connected` status via `mt5.terminal_info().connected`
4. WHEN the terminal reports `connected` equal to False, THE Account_Validator SHALL emit a Lifecycle_Event with event type `account_validation_failed` with reason `terminal_not_connected` and prevent the Runtime from starting
5. WHEN `mt5.account_info()` returns a valid result, THE Account_Validator SHALL verify that `mt5.terminal_info().trade_allowed` is True
6. WHEN `trade_allowed` is False, THE Account_Validator SHALL emit a Lifecycle_Event with event type `account_validation_failed` with reason `trading_not_allowed` and prevent the Runtime from starting
7. WHEN all account validation checks pass, THE Account_Validator SHALL emit a Lifecycle_Event with event type `account_validation_success` containing the account login number
8. IF an exception occurs during account validation, THEN THE Account_Validator SHALL emit a Lifecycle_Event with event type `account_validation_failed` containing the exception message and prevent the Runtime from starting

### Requirement 3: Centralise MT5 Initialize/Shutdown Ownership

**User Story:** As a system operator, I want MT5 terminal initialization and shutdown to occur exactly once at process boundaries, so that multi-symbol execution does not cycle the terminal connection per symbol.

#### Acceptance Criteria

1. THE Main_Process SHALL call `mt5.initialize()` exactly once at process startup before any symbol loop begins
2. THE Main_Process SHALL call `mt5.shutdown()` exactly once at process exit after all symbol loops have completed
3. THE MT5DataFeed `connect()` method SHALL NOT call `mt5.initialize()` when operating in centralised ownership mode
4. THE MT5DataFeed `disconnect()` method SHALL NOT call `mt5.shutdown()` when operating in centralised ownership mode
5. WHILE the Reconnect_Manager is performing its internal `mt5.shutdown()` → `mt5.initialize()` cycle, THE Runtime SHALL permit the reconnect-owned init/shutdown calls without conflict
6. WHEN `REPLAY_MODE` is True, THE Main_Process SHALL skip `mt5.initialize()` and `mt5.shutdown()` calls if the replay does not require a live MT5 connection
7. WHEN `DRY_RUN_EXECUTION_LOGS` is True and no live connection is needed, THE Main_Process SHALL skip MT5 initialization to preserve DRY_RUN compatibility
8. THE MT5DataFeed SHALL continue to perform `resolve_symbol()` and `symbol_select()` per symbol to maintain symbol isolation

### Requirement 4: Graceful Shutdown Signal Handling

**User Story:** As a system operator, I want the runtime to handle SIGINT and SIGTERM gracefully, so that the process exits cleanly without traceback spam, partial recovery state, or leaked MT5 connections.

#### Acceptance Criteria

1. WHEN the Main_Process starts, THE Shutdown_Handler SHALL register handlers for SIGINT and SIGTERM signals
2. WHEN a SIGINT or SIGTERM signal is received, THE Shutdown_Handler SHALL emit a Lifecycle_Event with event type `shutdown_signal_received` containing the signal name
3. WHEN a shutdown signal is received, THE Shutdown_Handler SHALL set a process-level shutdown flag that the Runtime can observe
4. WHEN the Runtime observes the shutdown flag during its poll loop, THE Runtime SHALL exit the current iteration cleanly without processing further bars
5. WHEN the Runtime exits due to a shutdown signal, THE Main_Process SHALL call `mt5.shutdown()` exactly once to release terminal resources
6. WHEN a shutdown signal is received, THE Shutdown_Handler SHALL prevent any new reconnect attempts from starting
7. WHILE the Reconnect_Manager is mid-cycle when a shutdown signal arrives, THE Runtime SHALL allow the current reconnect attempt to complete or abort within a bounded timeout before proceeding with shutdown
8. WHEN the process exits after a shutdown signal, THE Main_Process SHALL exit with return code 0 and produce no unhandled exception tracebacks
9. WHEN a shutdown signal is received during RECOVERY_Mode, THE Runtime SHALL abandon recovery and proceed directly to clean shutdown
10. IF a second SIGINT is received after the first, THEN THE Shutdown_Handler SHALL force immediate exit without waiting for graceful completion

### Requirement 5: Shutdown Lifecycle Observability

**User Story:** As a system operator, I want structured lifecycle events emitted during shutdown, so that I can confirm clean termination and diagnose incomplete shutdowns.

#### Acceptance Criteria

1. WHEN the shutdown sequence begins, THE Runtime SHALL emit a Lifecycle_Event with event type `shutdown_initiated` containing the trigger source (signal name or unrecoverable failure)
2. WHEN the Runtime completes its final poll iteration, THE Runtime SHALL emit a Lifecycle_Event with event type `shutdown_loop_exited` containing the active symbol and total iterations completed
3. WHEN `mt5.shutdown()` is called during the shutdown sequence, THE Main_Process SHALL emit a Lifecycle_Event with event type `mt5_terminal_released`
4. WHEN the process exits cleanly, THE Main_Process SHALL emit a Lifecycle_Event with event type `shutdown_complete` as the final log entry
5. THE Runtime SHALL support the following additional Lifecycle_Event types beyond those in the survivability spec: `symbol_resubscribe_started`, `symbol_resubscribe_success`, `symbol_resubscribe_failed`, `account_validation_success`, `account_validation_failed`, `shutdown_signal_received`, `shutdown_initiated`, `shutdown_loop_exited`, `mt5_terminal_released`, `shutdown_complete`

### Requirement 6: Shutdown Configuration

**User Story:** As a system operator, I want shutdown behaviour to be configurable, so that I can tune graceful shutdown timing without code changes.

#### Acceptance Criteria

1. THE Runtime SHALL read `MT5_SHUTDOWN_GRACE_SECONDS` from the config module with a default value of 10.0
2. WHEN a shutdown signal is received during an active reconnect cycle, THE Shutdown_Handler SHALL wait at most `MT5_SHUTDOWN_GRACE_SECONDS` before forcing termination
3. THE Runtime SHALL read `MT5_CENTRALISED_INIT` from the config module with a default value of True
4. WHEN `MT5_CENTRALISED_INIT` is False, THE MT5DataFeed SHALL retain its existing per-instance `connect()`/`disconnect()` behaviour for backward compatibility
