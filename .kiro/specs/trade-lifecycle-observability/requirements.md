# Requirements Document

## Introduction

This feature adds structured trade lifecycle observability to the MK1 trading bot. A central `emit_lifecycle_event` helper records key trade lifecycle facts (signal, entry, exit, rejection, modification, heartbeat) through the existing logging infrastructure. A `TradeLifecycleListener` implementation wires into `TradeStateManager` so that position-level events (close, SL modification) are captured without altering strategy logic. All lifecycle logging respects the existing log-switch architecture in `config.py`.

## Glossary

- **Lifecycle_Emitter**: The `emit_lifecycle_event` helper function responsible for timestamping, formatting, and routing lifecycle events into the Python logger
- **Trade_Lifecycle_Listener**: A concrete implementation of the `TradeLifecycleListener` protocol that receives `TradeEvent` objects from `TradeStateManager` and delegates to the Lifecycle_Emitter
- **TradeStateManager**: The existing reactive trade state machine that tracks open positions and emits `TradeEvent` objects to its listener
- **Loop**: The `run_live` / `run_replay` control loop in `core/loop.py` that orchestrates candle processing, decision-making, and execution
- **Config**: The `core/config.py` module containing all runtime switches and parameters
- **ExecutionResult**: The frozen dataclass returned by `MT5Execution.place_market` indicating success/failure of order placement
- **Decision**: The output of `process_bar` containing `should_trade`, `intent`, `bias`, `patterns`, `score`, and `reason`

## Requirements

### Requirement 1: Central Lifecycle Event Emitter

**User Story:** As a bot operator, I want a single entry point for all lifecycle log emissions, so that formatting, timestamping, and routing are consistent across all trade events.

#### Acceptance Criteria

1. THE Lifecycle_Emitter SHALL accept an event type string and a data dictionary as parameters
2. WHEN the Lifecycle_Emitter is called, THE Lifecycle_Emitter SHALL attach a monotonic timestamp to the emitted log record
3. WHEN the Lifecycle_Emitter is called, THE Lifecycle_Emitter SHALL format the data dictionary into a standardised key=value string representation
4. THE Lifecycle_Emitter SHALL route all output through the existing Python `logging.getLogger` infrastructure used by `_emit_event`
5. WHILE the ENABLE_TRADE_LIFECYCLE_LOGS switch is False, THE Lifecycle_Emitter SHALL suppress all lifecycle event output
6. WHILE the ESSENTIAL_LOGS switch is False, THE Lifecycle_Emitter SHALL suppress all lifecycle event output regardless of the ENABLE_TRADE_LIFECYCLE_LOGS value

### Requirement 2: TRADE_SIGNAL Event

**User Story:** As a bot operator, I want to see when the decision engine produces a trade signal, so that I can audit signal generation independently of execution.

#### Acceptance Criteria

1. WHEN `process_bar` returns a Decision with `should_trade=True`, THE Loop SHALL emit a TRADE_SIGNAL lifecycle event
2. THE Lifecycle_Emitter SHALL include the symbol, direction, pattern name, and score in the TRADE_SIGNAL payload
3. THE Loop SHALL emit at most one TRADE_SIGNAL event per closed bar per symbol

### Requirement 3: TRADE_ENTRY Event

**User Story:** As a bot operator, I want a structured record of every successful trade entry, so that I can reconstruct the trade journal from logs.

#### Acceptance Criteria

1. WHEN `MT5Execution.place_market` returns an ExecutionResult with `ok=True`, THE Loop SHALL emit a TRADE_ENTRY lifecycle event
2. THE Lifecycle_Emitter SHALL include timestamp, symbol, direction, entry price, stop-loss, take-profit, lot size, and pattern name in the TRADE_ENTRY payload
3. THE Lifecycle_Emitter SHALL include the confluence score in the TRADE_ENTRY payload
4. WHERE confirmation factor metadata is available in the Decision, THE Lifecycle_Emitter SHALL include confirmation factors in the TRADE_ENTRY payload

### Requirement 4: TRADE_EXIT Event

**User Story:** As a bot operator, I want a structured record of every position close, so that I can calculate PnL and review exit reasons.

#### Acceptance Criteria

1. WHEN TradeStateManager emits an ON_TRADE_CLOSE event, THE Trade_Lifecycle_Listener SHALL emit a TRADE_EXIT lifecycle event
2. THE Lifecycle_Emitter SHALL include timestamp, symbol, exit reason (TP, SL, time, management), entry price, and exit price in the TRADE_EXIT payload
3. THE Lifecycle_Emitter SHALL include trade duration in seconds in the TRADE_EXIT payload
4. WHERE unrealised PnL is available on the Position, THE Lifecycle_Emitter SHALL include PnL in the TRADE_EXIT payload
5. WHERE initial stop-loss and entry price are available, THE Lifecycle_Emitter SHALL include the R-multiple in the TRADE_EXIT payload

### Requirement 5: TRADE_REJECT Event

**User Story:** As a bot operator, I want to know when and why a trade was rejected, so that I can diagnose execution failures and gate blocks.

#### Acceptance Criteria

1. WHEN `MT5Execution.place_market` returns an ExecutionResult with `ok=False`, THE Loop SHALL emit a TRADE_REJECT lifecycle event
2. WHEN a gate or risk check blocks execution before `place_market` is called, THE Loop SHALL emit a TRADE_REJECT lifecycle event with the blocking reason
3. THE Lifecycle_Emitter SHALL include timestamp, symbol, direction, and rejection reason in the TRADE_REJECT payload

### Requirement 6: TRADE_MODIFY Event

**User Story:** As a bot operator, I want to see when stop-loss is modified (break-even or trailing), so that I can verify trade management behaviour.

#### Acceptance Criteria

1. WHEN TradeStateManager modifies a position's stop-loss (break-even or trailing adjustment), THE Trade_Lifecycle_Listener SHALL emit a TRADE_MODIFY lifecycle event
2. THE Lifecycle_Emitter SHALL include timestamp, symbol, position ID, previous stop-loss, new stop-loss, and modification reason in the TRADE_MODIFY payload
3. THE Trade_Lifecycle_Listener SHALL distinguish between break-even and trailing modifications in the event detail

### Requirement 7: HEARTBEAT Event

**User Story:** As a bot operator, I want periodic runtime summaries, so that I can confirm the bot is alive and see its current state at a glance.

#### Acceptance Criteria

1. WHILE the bot is running in live mode, THE Loop SHALL emit a HEARTBEAT lifecycle event at a configurable interval
2. THE Lifecycle_Emitter SHALL include uptime in seconds, list of active symbols, and open trade count in the HEARTBEAT payload
3. THE Lifecycle_Emitter SHALL include the timestamp of the last processed candle in the HEARTBEAT payload
4. WHILE the ENABLE_HEARTBEAT_LOGS switch is False, THE Lifecycle_Emitter SHALL suppress HEARTBEAT events
5. THE Config SHALL expose a HEARTBEAT_INTERVAL_SECONDS parameter with a default value of 300

### Requirement 8: Configuration Switches

**User Story:** As a bot operator, I want granular control over lifecycle logging, so that I can enable or disable specific event categories without restarting the bot.

#### Acceptance Criteria

1. THE Config SHALL expose an ENABLE_TRADE_LIFECYCLE_LOGS boolean switch defaulting to True
2. THE Config SHALL expose an ENABLE_HEARTBEAT_LOGS boolean switch defaulting to True
3. THE Config SHALL expose a HEARTBEAT_INTERVAL_SECONDS integer parameter defaulting to 300
4. WHILE ESSENTIAL_LOGS is False, THE Lifecycle_Emitter SHALL suppress all lifecycle events regardless of individual switch values
5. WHILE ENABLE_TRADE_LIFECYCLE_LOGS is True and ESSENTIAL_LOGS is True, THE Lifecycle_Emitter SHALL emit TRADE_SIGNAL, TRADE_ENTRY, TRADE_EXIT, TRADE_REJECT, and TRADE_MODIFY events

### Requirement 9: TradeLifecycleListener Wiring

**User Story:** As a developer, I want the lifecycle listener connected in `run_live`, so that TradeStateManager events are captured without modifying the state machine itself.

#### Acceptance Criteria

1. WHEN `run_live` initialises TradeStateManager, THE Loop SHALL pass a concrete Trade_Lifecycle_Listener instance as the `listener` parameter
2. WHEN TradeStateManager emits a TradeEvent with kind ON_TRADE_CLOSE, THE Trade_Lifecycle_Listener SHALL call the Lifecycle_Emitter with event type TRADE_EXIT
3. WHEN TradeStateManager emits a TradeEvent where the position's stop-loss differs from the previous value, THE Trade_Lifecycle_Listener SHALL call the Lifecycle_Emitter with event type TRADE_MODIFY
4. THE Trade_Lifecycle_Listener SHALL not modify any Position fields or influence strategy logic

### Requirement 10: No Duplicate Events

**User Story:** As a bot operator, I want each lifecycle fact recorded exactly once, so that log analysis and trade journal export produce accurate counts.

#### Acceptance Criteria

1. THE Loop SHALL emit at most one TRADE_SIGNAL event per decision cycle per symbol
2. THE Loop SHALL emit at most one TRADE_ENTRY event per successful execution per symbol
3. THE Loop SHALL emit at most one TRADE_REJECT event per failed execution or gate block per symbol
4. THE Trade_Lifecycle_Listener SHALL emit at most one TRADE_EXIT event per position close
5. THE Trade_Lifecycle_Listener SHALL emit at most one TRADE_MODIFY event per stop-loss change per position per price update cycle

### Requirement 11: Architectural Constraints

**User Story:** As a developer, I want the observability layer to be purely passive and lightweight, so that it cannot introduce regressions in strategy or execution logic and does not degrade execution performance.

#### Acceptance Criteria

1. THE Lifecycle_Emitter SHALL not modify any Decision, OrderIntent, Position, or ExecutionResult object
2. THE Lifecycle_Emitter SHALL not perform any calculation of SL, TP, lot size, or trade parameters
3. THE Trade_Lifecycle_Listener SHALL not call any execution method or modify broker state
4. THE Lifecycle_Emitter SHALL not emit events inside tight polling loops unless triggered by a discrete state change
5. IF the Lifecycle_Emitter raises an exception, THEN THE Loop SHALL log the error and continue normal operation without interrupting trade processing
6. THE Lifecycle_Emitter SHALL perform only string formatting and dictionary reads in the logging path with no heavy computation or redundant calculations
7. THE Lifecycle_Emitter SHALL read values already present on existing objects (Position, ExecutionResult, Decision) rather than recomputing derived values
8. THE Trade_Lifecycle_Listener SHALL not allocate large data structures or perform iteration beyond the immediate event payload construction

### Requirement 12: MT5_RECONNECT Extension Point

**User Story:** As a developer, I want a defined event type for connection recovery, so that future monitoring integrations can detect connectivity issues.

#### Acceptance Criteria

1. THE Lifecycle_Emitter SHALL recognise MT5_RECONNECT as a valid event type
2. WHEN an MT5 disconnect and subsequent reconnect is detected, THE Loop SHALL emit an MT5_RECONNECT lifecycle event (implementation deferred to future iteration)
3. THE Lifecycle_Emitter SHALL include timestamp and reconnect duration in the MT5_RECONNECT payload schema definition

