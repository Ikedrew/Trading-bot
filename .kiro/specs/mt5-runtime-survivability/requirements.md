# Requirements Document

## Introduction

MT5 Runtime Survivability (Tier 1) hardens the live trading runtime against temporary MetaTrader 5 terminal disconnections and transient network failures. Currently, any MT5 API failure raises a `RuntimeError` that immediately terminates the session. This feature adds exception-safe guards, proactive connection validation, bounded reconnect logic, and failure escalation so the runtime can recover from brief outages without corrupting state or duplicating trades.

## Glossary

- **Runtime**: The `run_live` loop in `core/loop.py` that polls MT5 for data and drives the trading pipeline
- **MT5_Terminal**: The MetaTrader 5 terminal process accessed via the `MetaTrader5` Python package
- **Connection_Validator**: The component that proactively checks MT5 terminal connectivity before runtime-critical operations
- **Reconnect_Manager**: The component that performs bounded reconnect attempts with backoff when a disconnection is detected
- **EngineState**: Mutable runtime state (`core/engine_state.py`) carried across bars during live execution
- **TradeStateManager**: The trade management state tracker that monitors open positions post-entry
- **MT5DataFeed**: The data access layer (`data/mt5_data.py`) wrapping MT5 market data calls
- **Lifecycle_Event**: A structured log event emitted through the observability layer to record connection state transitions
- **Reconnect_Attempt**: A single cycle of `mt5.shutdown()` → `mt5.initialize()` → terminal validation → symbol re-selection
- **Failure_Escalation**: The process of cleanly shutting down the Runtime when all Reconnect_Attempts are exhausted

## Requirements

### Requirement 1: Exception-Safe MT5 Data Guards

**User Story:** As a live trader, I want MT5 API failures in the data layer to be caught and handled gracefully, so that a single transient failure does not terminate my trading session.

#### Acceptance Criteria

1. WHEN `last_tick()` raises an exception during the live loop, THE Runtime SHALL catch the exception and enter connection recovery instead of terminating
2. WHEN `copy_rates_closed()` raises an exception during the live loop, THE Runtime SHALL catch the exception and enter connection recovery instead of terminating
3. WHEN an MT5 data call exception is caught, THE Runtime SHALL emit a Lifecycle_Event with event type `disconnect_detected` containing the exception message
4. WHILE the Runtime is handling a caught MT5 exception, THE Runtime SHALL preserve the current EngineState and TradeStateManager without modification
5. WHEN consecutive MT5 exceptions occur within the same recovery cycle, THE Runtime SHALL emit a single Lifecycle_Event per recovery cycle rather than one per exception

### Requirement 2: Proactive Connection Validation

**User Story:** As a live trader, I want the runtime to detect MT5 disconnections before attempting data fetches, so that failures are caught early and recovery starts immediately.

#### Acceptance Criteria

1. WHEN the Runtime begins a new poll iteration, THE Connection_Validator SHALL call `mt5.terminal_info()` to check terminal connectivity
2. WHEN `mt5.terminal_info()` returns None, THE Connection_Validator SHALL treat the terminal as disconnected and trigger connection recovery
3. WHEN `mt5.terminal_info()` returns a result with `connected` equal to False, THE Connection_Validator SHALL treat the terminal as disconnected and trigger connection recovery
4. WHEN the Connection_Validator detects a disconnected state, THE Runtime SHALL emit a Lifecycle_Event with event type `disconnect_detected` before initiating recovery
5. WHILE the MT5_Terminal is validated as connected, THE Runtime SHALL proceed with `last_tick()` and `copy_rates_closed()` calls without additional delay

### Requirement 3: Bounded Reconnect Logic

**User Story:** As a live trader, I want reconnect attempts to be bounded and use backoff delays, so that the runtime does not enter infinite reconnect loops or spam the MT5 terminal.

#### Acceptance Criteria

1. THE Reconnect_Manager SHALL limit reconnect attempts to a configurable maximum defined by `MT5_MAX_RECONNECT_ATTEMPTS`
2. WHEN a reconnect attempt begins, THE Reconnect_Manager SHALL emit a Lifecycle_Event with event type `reconnect_started` including the current attempt number
3. WHEN a Reconnect_Attempt is performed, THE Reconnect_Manager SHALL execute the sequence: `mt5.shutdown()` → `mt5.initialize()` → terminal connectivity validation → symbol re-selection
4. WHEN a Reconnect_Attempt succeeds, THE Reconnect_Manager SHALL emit a Lifecycle_Event with event type `reconnect_success` and return control to the Runtime live loop
5. WHEN a Reconnect_Attempt fails, THE Reconnect_Manager SHALL emit a Lifecycle_Event with event type `reconnect_failed` including the attempt number and error details
6. WHEN `MT5_RECONNECT_BACKOFF_ENABLED` is True, THE Reconnect_Manager SHALL increase the delay between attempts using exponential backoff starting from `MT5_RECONNECT_DELAY_SECONDS`
7. WHEN `MT5_RECONNECT_BACKOFF_ENABLED` is False, THE Reconnect_Manager SHALL use a fixed delay of `MT5_RECONNECT_DELAY_SECONDS` between attempts
8. THE Runtime SHALL resume normal live loop execution from the next poll iteration after a successful reconnect without reprocessing the last closed bar

### Requirement 4: Failure Escalation

**User Story:** As a live trader, I want the runtime to shut down cleanly when reconnection is impossible, so that there are no zombie processes or partially connected states.

#### Acceptance Criteria

1. WHEN all reconnect attempts are exhausted, THE Reconnect_Manager SHALL emit a Lifecycle_Event with event type `reconnect_exhausted` including the total attempts made
2. WHEN reconnect attempts are exhausted, THE Runtime SHALL emit a Lifecycle_Event with event type `runtime_shutdown_unrecoverable` before exiting
3. WHEN the Runtime exits due to unrecoverable failure, THE Runtime SHALL call `mt5.shutdown()` to release terminal resources
4. WHEN the Runtime exits due to unrecoverable failure, THE Runtime SHALL exit the live loop cleanly without raising an unhandled exception to the caller
5. IF the Runtime is in a partially connected state after failed reconnection, THEN THE Runtime SHALL call `mt5.shutdown()` to ensure no zombie connection persists

### Requirement 5: Runtime State Safety During Reconnect

**User Story:** As a live trader, I want my engine state and trade tracking to survive reconnect attempts intact, so that no duplicate trades or duplicate bar processing occurs after recovery.

#### Acceptance Criteria

1. WHILE the Reconnect_Manager is performing reconnect attempts, THE Runtime SHALL preserve the EngineState instance without resetting any fields
2. WHILE the Reconnect_Manager is performing reconnect attempts, THE Runtime SHALL preserve the TradeStateManager instance and all tracked positions
3. WHEN the Runtime resumes after a successful reconnect, THE Runtime SHALL use the preserved `last_closed_time` value to prevent reprocessing the same bar
4. WHEN the Runtime resumes after a successful reconnect, THE Runtime SHALL use the preserved `last_successful_open_mono` value to enforce cooldown correctly
5. IF an exception occurs during a Reconnect_Attempt itself, THEN THE Reconnect_Manager SHALL catch the exception and proceed to the next attempt without corrupting Runtime state
6. WHEN the Runtime resumes after a successful reconnect, THE Runtime SHALL validate that no duplicate trade execution occurs by checking EngineState cooldown fields before any new trade

### Requirement 6: Reconnect Configuration

**User Story:** As a system operator, I want reconnect behaviour to be configurable via the config module, so that I can tune recovery aggressiveness without code changes.

#### Acceptance Criteria

1. THE Runtime SHALL read `MT5_MAX_RECONNECT_ATTEMPTS` from the config module with a default value of 5
2. THE Runtime SHALL read `MT5_RECONNECT_DELAY_SECONDS` from the config module with a default value of 5.0
3. THE Runtime SHALL read `MT5_RECONNECT_BACKOFF_ENABLED` from the config module with a default value of True
4. WHEN `MT5_MAX_RECONNECT_ATTEMPTS` is set to 0, THE Runtime SHALL skip reconnect logic and escalate to failure immediately on disconnection

### Requirement 7: Lifecycle Observability Events

**User Story:** As a system operator, I want structured lifecycle events emitted for all connection state transitions, so that I can monitor runtime health and diagnose failures post-hoc.

#### Acceptance Criteria

1. THE Runtime SHALL emit Lifecycle_Events using the existing `logger` infrastructure with level INFO for recoverable events
2. THE Runtime SHALL emit Lifecycle_Events using the existing `logger` infrastructure with level CRITICAL for unrecoverable failure events
3. WHEN a Lifecycle_Event is emitted, THE Runtime SHALL include the active symbol, event type, and a Unix timestamp in the log message
4. THE Runtime SHALL support the following Lifecycle_Event types: `disconnect_detected`, `reconnect_started`, `reconnect_success`, `reconnect_failed`, `reconnect_exhausted`, `runtime_shutdown_unrecoverable`
5. WHILE the Runtime is in a reconnect cycle, THE Runtime SHALL suppress repeated `disconnect_detected` events to avoid log spam


### Requirement 8: Reconnect Cooldown / Recovery Stabilisation

**User Story:** As a live trader, I want the runtime to stabilise after a successful reconnect before resuming trade execution, so that I do not trade on potentially stale or partially restored MT5 state.

#### Acceptance Criteria

1. WHEN a reconnect succeeds, THE Runtime SHALL require at least one successful fresh data fetch cycle (`last_tick()` + `copy_rates_closed()` both returning valid data) before resuming normal signal evaluation and trade execution
2. WHILE the Runtime has not completed a successful post-reconnect data fetch cycle, THE Runtime SHALL suppress all trade execution (no `place_market` calls) even if `process_bar` returns `should_trade=True`
3. WHEN the post-reconnect stabilisation cycle completes successfully, THE Runtime SHALL emit a Lifecycle_Event with event type `recovery_stabilised` and transition to normal execution mode
4. WHILE in post-reconnect stabilisation, THE Runtime SHALL continue calling `TradeStateManager.on_price_update` with fresh tick data to maintain position tracking continuity

---

### Requirement 9: Tick Freshness Validation During Recovery

**User Story:** As a live trader, I want the runtime to verify that tick data is genuinely fresh after reconnect, so that a successful reconnect does not falsely restore execution while the feed is still frozen or stale.

#### Acceptance Criteria

1. THE Runtime SHALL read `MT5_MAX_TICK_STALENESS_SECONDS` from the config module with a default value of 30.0
2. WHEN the Runtime receives a tick after reconnect, THE Runtime SHALL compare the tick timestamp (`tick.time`) against the current system time
3. IF the tick timestamp is older than `MT5_MAX_TICK_STALENESS_SECONDS` relative to system time, THEN THE Runtime SHALL remain in recovery mode and not resume trade execution
4. WHEN a fresh tick (within staleness threshold) is received after reconnect, THE Runtime SHALL consider the feed restored and proceed with post-reconnect stabilisation
5. WHILE ticks remain stale beyond the threshold, THE Runtime SHALL emit a Lifecycle_Event with event type `tick_stale_during_recovery` at most once per recovery cycle

---

### Requirement 10: Recovery State Visibility

**User Story:** As a system operator, I want the runtime to maintain an explicit internal recovery state, so that reconnect flow is clear, duplicate recovery attempts are prevented, and lifecycle events accurately reflect runtime mode.

#### Acceptance Criteria

1. THE Runtime SHALL maintain an internal state flag distinguishing between `NORMAL` and `RECOVERY` runtime modes
2. WHILE the Runtime is in `RECOVERY` mode, THE Runtime SHALL prevent duplicate reconnect attempts from being triggered by subsequent MT5 failures within the same recovery cycle
3. WHEN the Runtime transitions from `NORMAL` to `RECOVERY`, THE Runtime SHALL emit a Lifecycle_Event with event type `recovery_entered`
4. WHEN the Runtime transitions from `RECOVERY` to `NORMAL`, THE Runtime SHALL emit a Lifecycle_Event with event type `recovery_exited`
5. WHILE the Runtime is in `RECOVERY` mode, THE Runtime SHALL suppress all trade execution and signal processing until recovery completes and stabilisation is confirmed


---

### Requirement 11: Candle Staleness Detection

**User Story:** As a live trader, I want the runtime to detect when candle progression has frozen beyond the expected timeframe interval, so that I can distinguish between normal market closure and a stalled/broken feed.

#### Acceptance Criteria

1. THE Runtime SHALL track the elapsed wall-clock time since the last new closed candle was processed
2. THE Runtime SHALL read `MT5_MAX_CANDLE_STALENESS_MULTIPLIER` from the config module with a default value of 2.0
3. WHEN the elapsed time since the last new candle exceeds `timeframe_interval_seconds * MT5_MAX_CANDLE_STALENESS_MULTIPLIER`, THE Runtime SHALL emit a Lifecycle_Event with event type `candle_stale_detected` containing the elapsed duration and expected interval
4. THE Runtime SHALL emit `candle_stale_detected` at most once per staleness episode — repeated iterations while stale SHALL NOT produce additional events
5. WHEN a new closed candle is subsequently detected after a staleness episode, THE Runtime SHALL emit a Lifecycle_Event with event type `candle_stale_resolved` containing the total stale duration
6. WHILE candle staleness is detected, THE Runtime SHALL continue normal tick-driven operations (trade management price updates) but SHALL log the stale condition for operational awareness
7. THE Runtime SHALL NOT treat candle staleness alone as a disconnection trigger — it remains a passive monitoring signal, not a recovery trigger, since market closure produces the same symptom
