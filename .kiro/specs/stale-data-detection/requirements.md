# Requirements Document

## Introduction

Stale Data Detection provides runtime integrity monitoring for the MT5 trading bot. MT5 can report a connected state while the data feed is frozen (no tick updates, no bar progression). The current system only checks `terminal_info().connected`, which does not detect stale feeds. This feature detects when market data is no longer updating and transitions the system into a safe degraded state BEFORE trading decisions are made on frozen data.

The feature integrates with the existing reconnect state machine (CONNECTED/DISCONNECTED/RECONNECTING) and degraded mode infrastructure without modifying trading logic.

## Glossary

- **Stale_Data_Monitor**: The component responsible for tracking data freshness per symbol and escalating when thresholds are breached.
- **Run_Live_Loop**: The main live trading loop (`run_live`) that polls MT5 for ticks and candles each iteration.
- **Reconnect_State_Machine**: The existing state machine managing MT5 connection states (CONNECTED, DISCONNECTED, RECONNECTING).
- **Degraded_Mode**: The existing safe state where trading decisions are suspended while connectivity is restored.
- **Tick_Timestamp**: The server-side timestamp embedded in each MT5 tick, representing when the price was generated.
- **Candle_Time**: The open-time of the latest closed candle returned by `copy_rates_closed`.
- **Wall_Clock_Time**: The local system time (`time.time()`) used to measure elapsed duration since last fresh data.
- **Heartbeat_Timeout**: The maximum allowed duration without any fresh market data before escalation to degraded mode.
- **Escalation_Level**: A severity tier (1=warning, 2=escalation, 3=critical) representing how long data has been stale.
- **Stale_State_Flag**: A per-symbol boolean indicating whether the data feed is currently considered stale.

## Requirements

### Requirement 1: Per-Symbol Stale State Tracking

**User Story:** As a trading system operator, I want the system to track data freshness per symbol, so that stale conditions are detected independently for each instrument.

#### Acceptance Criteria

1. WHEN the Run_Live_Loop initializes for a symbol, THE Stale_Data_Monitor SHALL initialize `last_tick_time`, `last_candle_time`, `last_data_update_time`, and `stale_state` tracking fields for that symbol.
2. WHEN a fresh tick is received (tick timestamp newer than `last_tick_time`), THE Stale_Data_Monitor SHALL update `last_tick_time` to the new tick timestamp and update `last_data_update_time` to the current Wall_Clock_Time.
3. WHEN a fresh candle is received (candle time newer than `last_candle_time`), THE Stale_Data_Monitor SHALL update `last_candle_time` to the new candle time and update `last_data_update_time` to the current Wall_Clock_Time.
4. THE Stale_Data_Monitor SHALL store all tracking fields in O(1) memory per symbol using fixed-size state variables.

### Requirement 2: Tick Freshness Detection

**User Story:** As a trading system operator, I want the system to detect when tick data stops updating, so that frozen price feeds are identified before trading decisions use stale prices.

#### Acceptance Criteria

1. WHEN `feed.last_tick()` returns a tick with a timestamp equal to or older than the previously recorded `last_tick_time`, THE Stale_Data_Monitor SHALL classify the tick as stale.
2. WHEN a stale tick is detected, THE Stale_Data_Monitor SHALL log a warning containing the symbol name, the stale tick timestamp, and the elapsed seconds since last fresh tick.
3. WHEN a fresh tick is received after a stale period, THE Stale_Data_Monitor SHALL clear the stale tick condition and log a recovery message.
4. THE Stale_Data_Monitor SHALL compare tick timestamps using the server-side tick timestamp, not Wall_Clock_Time.

### Requirement 3: Candle Freshness Detection

**User Story:** As a trading system operator, I want the system to detect when candle data stops progressing, so that prolonged bar stagnation triggers appropriate escalation.

#### Acceptance Criteria

1. WHEN `copy_rates_closed` returns candles with the latest candle time equal to the previously recorded `last_candle_time`, THE Stale_Data_Monitor SHALL recognize this as a non-progressing candle state.
2. WHILE the candle time has not progressed AND the elapsed Wall_Clock_Time since `last_candle_time` update is less than STALE_CANDLE_TIMEOUT_SECONDS, THE Stale_Data_Monitor SHALL treat the condition as normal inter-bar waiting.
3. WHEN the elapsed Wall_Clock_Time since last candle progression exceeds STALE_CANDLE_TIMEOUT_SECONDS, THE Stale_Data_Monitor SHALL escalate the condition as a stale candle event.
4. THE Stale_Data_Monitor SHALL use STALE_CANDLE_TIMEOUT_SECONDS with a default value of 600.0 seconds (2x the M5 interval).

### Requirement 4: Market Heartbeat Monitor

**User Story:** As a trading system operator, I want a heartbeat monitor that detects total data silence, so that the system enters degraded mode when no market updates arrive within a timeout.

#### Acceptance Criteria

1. WHEN the elapsed Wall_Clock_Time since `last_data_update_time` exceeds MARKET_HEARTBEAT_TIMEOUT_SECONDS, THE Stale_Data_Monitor SHALL emit a MARKET_HEARTBEAT_LOSS event.
2. WHEN a MARKET_HEARTBEAT_LOSS event is emitted, THE Stale_Data_Monitor SHALL transition the Reconnect_State_Machine to the DISCONNECTED state.
3. WHEN the Reconnect_State_Machine transitions to DISCONNECTED due to heartbeat loss, THE Degraded_Mode SHALL activate and suspend trading decisions.
4. THE Stale_Data_Monitor SHALL use MARKET_HEARTBEAT_TIMEOUT_SECONDS with a default value of 120.0 seconds.
5. WHEN fresh data resumes after a heartbeat loss, THE Stale_Data_Monitor SHALL allow the existing Reconnect_State_Machine recovery flow to restore the CONNECTED state.

### Requirement 5: Three-Tier Escalation

**User Story:** As a trading system operator, I want graduated escalation levels for stale data, so that minor staleness produces warnings while prolonged staleness triggers protective action.

#### Acceptance Criteria

1. WHILE data has been stale for less than STALE_ESCALATION_WARNING_SECONDS, THE Stale_Data_Monitor SHALL classify the condition as Escalation_Level 1 and log a warning-level message only.
2. WHEN data has been stale for longer than STALE_ESCALATION_WARNING_SECONDS but less than STALE_ESCALATION_CRITICAL_SECONDS, THE Stale_Data_Monitor SHALL classify the condition as Escalation_Level 2 and log an escalation warning with elapsed stale duration.
3. WHEN data has been stale for longer than STALE_ESCALATION_CRITICAL_SECONDS, THE Stale_Data_Monitor SHALL classify the condition as Escalation_Level 3, transition the Reconnect_State_Machine to DISCONNECTED, and force a reconnect cycle.
4. THE Stale_Data_Monitor SHALL use STALE_ESCALATION_WARNING_SECONDS with a default value of 60.0 seconds.
5. THE Stale_Data_Monitor SHALL use STALE_ESCALATION_CRITICAL_SECONDS with a default value of 300.0 seconds.
6. WHEN fresh data resumes at any Escalation_Level, THE Stale_Data_Monitor SHALL reset the escalation state to Level 0 (normal).

### Requirement 6: Configuration Parameters

**User Story:** As a trading system operator, I want all stale detection thresholds to be configurable, so that I can tune sensitivity without code changes.

#### Acceptance Criteria

1. THE Stale_Data_Monitor SHALL read STALE_TICK_TIMEOUT_SECONDS from the config module with a default value of 30.0 seconds.
2. THE Stale_Data_Monitor SHALL read STALE_CANDLE_TIMEOUT_SECONDS from the config module with a default value of 600.0 seconds.
3. THE Stale_Data_Monitor SHALL read MARKET_HEARTBEAT_TIMEOUT_SECONDS from the config module with a default value of 120.0 seconds.
4. THE Stale_Data_Monitor SHALL read STALE_ESCALATION_WARNING_SECONDS from the config module with a default value of 60.0 seconds.
5. THE Stale_Data_Monitor SHALL read STALE_ESCALATION_CRITICAL_SECONDS from the config module with a default value of 300.0 seconds.
6. THE Stale_Data_Monitor SHALL access configuration values using `getattr(config, PARAM_NAME, default)` to maintain backward compatibility with existing config files.

### Requirement 7: Integration Safety

**User Story:** As a trading system operator, I want stale data detection to integrate safely with the existing runtime, so that monitoring never crashes the bot or alters trading logic.

#### Acceptance Criteria

1. IF an exception occurs within the Stale_Data_Monitor, THEN THE Run_Live_Loop SHALL catch the exception, log it as a non-fatal monitoring error, and continue the iteration without interruption.
2. THE Stale_Data_Monitor SHALL perform all freshness checks in O(1) time complexity per iteration using only timestamp comparisons and arithmetic.
3. THE Stale_Data_Monitor SHALL operate exclusively within the Run_Live_Loop and SHALL NOT modify any trading decision logic, order execution, or risk management behavior.
4. THE Stale_Data_Monitor SHALL integrate with the existing Reconnect_State_Machine by setting `mt5_state` to DISCONNECTED when critical thresholds are breached, reusing the existing reconnect and degraded mode flows.
5. WHEN the Stale_Data_Monitor transitions the system to DISCONNECTED, THE existing Degraded_Mode infrastructure SHALL handle trade suspension without additional logic in the Stale_Data_Monitor.

### Requirement 8: Tick Timestamp Availability

**User Story:** As a trading system operator, I want the data feed to expose tick timestamps, so that the Stale_Data_Monitor can compare successive tick times.

#### Acceptance Criteria

1. WHEN `feed.last_tick()` is called, THE MT5DataFeed SHALL return the tick timestamp alongside bid and ask prices.
2. THE MT5DataFeed SHALL extract the tick timestamp from the MT5 `symbol_info_tick` response `time` field.
3. IF `symbol_info_tick` returns None, THEN THE MT5DataFeed SHALL raise a RuntimeError as it does today, and the Stale_Data_Monitor SHALL not update tick freshness state for that iteration.
