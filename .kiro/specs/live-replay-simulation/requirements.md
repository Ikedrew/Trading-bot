# Requirements Document

## Introduction

The live-replay-simulation feature transforms the existing `run_replay` function in `core/loop.py` from a bulk historical batch processor into a stateful, event-driven, paced simulation engine that mirrors the runtime behaviour of `run_live`. The engine replays a historical candle sequence one bar at a time, maintaining full state continuity (bias, cooldowns, positions, EngineState), integrating `TradeStateManager` for post-entry lifecycle management, supporting configurable pacing modes (instant, slowed, step-by-step), and emitting structured observability output. No strategy logic, signal generation, or alpha rules are added or modified.

---

## Glossary

- **Replay_Engine**: The transformed `run_replay` function and its supporting helpers inside `core/loop.py` that execute the live-like simulation.
- **Candle_Sequence**: The ordered list of historical `Candle` objects fetched once at startup and iterated one bar at a time.
- **Sim_Clock**: The monotonic simulation timestamp derived from each candle's `time` field, used as the authoritative time source for all state machine calls during replay.
- **EngineState**: The `core.engine_state.EngineState` dataclass that carries bias, cooldown, regime, and failure-zone state across bars.
- **TradeStateManager**: The `core.trade_management.manager.TradeStateManager` instance that owns open/partial positions and applies post-entry SL/TP/trailing/partial-TP rules.
- **Replay_Position**: A `Position` object registered with `TradeStateManager` during replay, using simulated fill prices and Sim_Clock timestamps.
- **Pacing_Mode**: One of three replay speed controls — `INSTANT` (no delay), `SLOWED` (configurable wall-clock delay per bar), or `STEP` (pause and wait for user input between bars).
- **Replay_Listener**: An optional `TradeLifecycleListener` implementation that receives `TradeEvent` callbacks from `TradeStateManager` during replay.
- **Observability_Output**: Structured log lines emitted by the Replay_Engine using the existing `_emit_event` helper, extended with replay-specific event types.
- **Spread_Model**: The configurable bid/ask spread applied to each candle's OHLC prices to simulate broker-style quote separation during replay.
- **ExecutionResult**: The `execution.mt5_execution.ExecutionResult` datatype used to register simulated fills with `TradeStateManager` without calling MT5.
- **Replay_Config**: The set of configuration keys read from `core.config` that govern replay behaviour (pacing, spread, trade management enablement).

---

## Requirements

### Requirement 1: Sequential Candle Arrival

**User Story:** As a developer testing the trading bot, I want historical candles to arrive one at a time in chronological order, so that the engine processes each bar exactly as it would in live trading without look-ahead.

#### Acceptance Criteria

1. WHEN the Replay_Engine starts, THE Replay_Engine SHALL fetch the full Candle_Sequence once from `MT5DataFeed.copy_rates_closed` before beginning iteration.
2. WHEN processing bar `i`, THE Replay_Engine SHALL pass only the candle slice `candles[0 : i + 1]` to `process_bar`, ensuring no future candles are visible.
3. THE Replay_Engine SHALL process candles in strictly ascending order of their `time` field, with no skips or reordering.
4. WHEN the Candle_Sequence contains zero candles, THE Replay_Engine SHALL emit a `REPLAY_ABORT` event and return immediately without processing any bars, without mutating `EngineState`, without instantiating `TradeStateManager`, and without invoking any callbacks. The return value SHALL be a safe no-op state equivalent to a session that never started.
5. WHEN the Candle_Sequence contains fewer bars than `config.SETUP_MA_PERIOD + 3` (but more than zero), THE Replay_Engine SHALL emit a `REPLAY_ABORT` event and return without processing any bars.
6. THE Replay_Engine SHALL use `candles[closed_i].time` as the Sim_Clock value for every `process_bar` call on that bar.

---

### Requirement 2: Stateful Continuity

**User Story:** As a developer, I want bias, cooldowns, positions, and all EngineState fields to persist correctly across every bar, so that the replay produces the same decisions a live engine would have made on the same data.

#### Acceptance Criteria

1. THE Replay_Engine SHALL create exactly one `EngineState` instance before the first bar and reuse it for every subsequent `process_bar` call.
2. WHEN `process_bar` returns a decision with `should_trade=True`, THE Replay_Engine SHALL update `state.last_successful_open_mono` to the current Sim_Clock value, exactly mirroring the update performed by `run_live` in the same conditional branch.
3. WHILE a `TradeStateManager` is active, THE Replay_Engine SHALL call `TradeStateManager.on_price_update` once per bar using the bar's simulated bid and ask prices and the Sim_Clock timestamp.
4. IF an `EngineState` reset occurs, THE Replay_Engine SHALL reset only volatile execution state (e.g. per-bar signal flags, transient evaluation buffers) and SHALL preserve all core trading continuity fields — specifically `last_successful_open_mono`, open position references, and execution history — restoring them to the new `EngineState` instance before the next `process_bar` call.
5. WHEN `TradeStateManager.on_price_update` closes a position (SL, TP, or time exit), THE Replay_Engine SHALL continue processing subsequent bars without re-initialising `EngineState`.

---

### Requirement 3: TradeStateManager Integration

**User Story:** As a developer, I want simulated trades to be registered with TradeStateManager so that post-entry management rules (break-even, trailing stop, partial TP, time exit) are exercised during replay exactly as they are in live mode.

#### Acceptance Criteria

1. WHEN `config.TRADE_MANAGEMENT_ENABLED` is `True`, THE Replay_Engine SHALL instantiate a `TradeStateManager` using `_build_trade_management_config()` before the first bar.
2. WHEN `process_bar` returns a decision with `should_trade=True` and a non-`None` intent, THE Replay_Engine SHALL call `TradeStateManager.register_from_execution` with a synthetic `ExecutionResult` that has `ok=True` and a unique `deal` identifier.
3. WHEN registering a simulated position, THE Replay_Engine SHALL pass the Sim_Clock value as `open_time_s` to `TradeStateManager.register_from_execution`.
4. WHEN registering a simulated position, THE Replay_Engine SHALL derive `entry_fill_price` from the ask price for BUY intents and the bid price for SELL intents, matching the fill-price logic in `run_live`.
5. WHEN `config.TRADE_MANAGEMENT_ENABLED` is `False`, THE Replay_Engine SHALL strictly prevent instantiation of `TradeStateManager` and SHALL process bars without post-entry management.
6. WHERE a `Replay_Listener` is provided, THE Replay_Engine SHALL pass it to `TradeStateManager` so that all `TradeEvent` callbacks are forwarded to the listener.

---

### Requirement 4: Spread Model

**User Story:** As a developer, I want a configurable bid/ask spread applied to each candle's close price during replay, so that entry fills and SL/TP evaluations reflect realistic broker-style quote separation.

#### Acceptance Criteria

1. THE Replay_Engine SHALL read a `REPLAY_SPREAD` value from `core.config` (default `0.0` if absent) representing the full bid/ask spread in price units.
2. WHEN computing the bid price for a bar, THE Replay_Engine SHALL set `bid = candle.close - REPLAY_SPREAD / 2`.
3. WHEN computing the ask price for a bar, THE Replay_Engine SHALL set `ask = candle.close + REPLAY_SPREAD / 2`.
4. WHEN `REPLAY_SPREAD` is `0.0`, THE Replay_Engine SHALL set `bid == ask == candle.close`, preserving the existing zero-spread behaviour.
5. IF a candle's `close` price is less than `0.0`, THEN THE Replay_Engine SHALL skip that bar, emit a `REPLAY_BAR_SKIPPED` event with `reason=invalid_close_price`, and continue to the next bar. A `close` price of exactly `0.0` SHALL be processed normally with `bid = ask = 0.0` when `REPLAY_SPREAD` is `0.0`.
6. THE Replay_Engine SHALL use the same bid/ask values for both `process_bar` and `TradeStateManager.on_price_update` on the same bar.
7. AFTER computing `bid` and `ask` from the spread formula, IF either derived value is less than or equal to `0.0`, THEN THE Replay_Engine SHALL clamp the offending value to a minimum of `0.0`, log a warning containing the bar index, `close` price, `REPLAY_SPREAD`, and the clamped value, and continue processing the bar with the clamped bid/ask. THE Replay_Engine SHALL NOT skip the bar solely because spread derivation produced a non-positive intermediate value.

---

### Requirement 5: Replay Pacing Controls

**User Story:** As a developer, I want to control the speed of replay — running instantly for batch analysis, slowed for visual observation, or step-by-step for debugging — so that I can inspect engine behaviour at any granularity.

#### Acceptance Criteria

1. THE Replay_Engine SHALL read a `REPLAY_PACING_MODE` value from `core.config` (one of `"INSTANT"`, `"SLOWED"`, `"STEP"`; default `"INSTANT"` if absent).
2. WHEN `REPLAY_PACING_MODE` is `"INSTANT"`, THE Replay_Engine SHALL process all bars without any wall-clock delay between bars.
3. WHEN `REPLAY_PACING_MODE` is `"SLOWED"`, THE Replay_Engine SHALL call `time.sleep` with a duration of `REPLAY_BAR_DELAY_SECONDS` (read from `core.config`, default `1.0` if absent) after processing each bar.
4. WHEN `REPLAY_PACING_MODE` is `"STEP"`, THE Replay_Engine SHALL pause after each bar and wait for the user to press Enter before processing the next bar.
5. WHEN `REPLAY_PACING_MODE` is `"STEP"`, THE Replay_Engine SHALL print a prompt to `stdout` indicating the current bar index and Sim_Clock before waiting.
6. IF `REPLAY_PACING_MODE` contains an unrecognised value, THEN THE Replay_Engine SHALL log a warning, reset only mode-specific runtime state (e.g. pending sleep timers, step-mode input buffers), preserve all core trading state (open positions, `last_successful_open_mono`, execution history), and process all remaining bars without any wall-clock delay, as if `REPLAY_PACING_MODE` were `"INSTANT"`.

---

### Requirement 6: Observability and Debugging Output

**User Story:** As a developer, I want structured log events emitted throughout the replay lifecycle, so that I can trace engine decisions, position events, and pacing transitions without modifying strategy code.

#### Acceptance Criteria

1. WHEN the Replay_Engine starts, THE Replay_Engine SHALL emit a `REPLAY_START` event containing the symbol, timeframe, total candle count, start bar index, and pacing mode.
2. WHEN the Replay_Engine finishes all bars, THE Replay_Engine SHALL emit a `REPLAY_COMPLETE` event containing the total bars processed, total signals generated, and total positions opened.
3. WHEN a simulated position is registered with `TradeStateManager`, THE Replay_Engine SHALL emit a `REPLAY_TRADE_OPEN` event containing the bar index, Sim_Clock, side, entry price, SL, TP, and volume.
4. WHEN `TradeStateManager` emits an `ON_STOP_LOSS_HIT`, `ON_TAKE_PROFIT_HIT`, or `ON_MANAGEMENT_EXIT` event via the Replay_Listener, THE Replay_Engine SHALL emit a corresponding `REPLAY_TRADE_CLOSE` event containing the position ID, close reason, and Sim_Clock.
5. WHEN `PRINT_MODE` is `"FULL_DEBUG"`, THE Replay_Engine SHALL emit a `REPLAY_BAR_TICK` event for every bar containing the bar index, Sim_Clock, bid, ask, open positions count, and decision reason.
6. THE Replay_Engine SHALL use the existing `_emit_event` helper for all Observability_Output, ensuring output respects the `PRINT_MODE` silent/event-only/full-debug hierarchy.

---

### Requirement 7: Broker-Style Realism

**User Story:** As a developer, I want the replay to simulate broker-style execution constraints so that the results are comparable to live trading outcomes.

#### Acceptance Criteria

1. THE Replay_Engine SHALL enforce `config.MAX_OPEN_POSITIONS` during replay: WHEN the number of open `Replay_Position` objects equals `config.MAX_OPEN_POSITIONS`, THE Replay_Engine SHALL not register a new position even if `process_bar` returns `should_trade=True`.
2. WHEN `process_bar` returns `should_trade=True` but the position limit is reached, THE Replay_Engine SHALL emit a `REPLAY_TRADE_SKIPPED` event with `reason=max_positions`.
3. THE Replay_Engine SHALL apply `config.COOLDOWN_SECONDS` between position opens: IF `state.last_successful_open_mono` is set and `(sim_clock - state.last_successful_open_mono) < config.COOLDOWN_SECONDS`, THEN THE Replay_Engine SHALL not register a new position. IF `(sim_clock - state.last_successful_open_mono) >= config.COOLDOWN_SECONDS`, THEN THE Replay_Engine SHALL permit the position open. This rule applies on every bar regardless of whether it is the first trade of the session.
4. WHEN a position open is suppressed by the cooldown, THE Replay_Engine SHALL emit a `REPLAY_TRADE_SKIPPED` event with `reason=cooldown`.
5. THE Replay_Engine SHALL assign each simulated position a unique synthetic deal ID using a monotonically incrementing integer counter, ensuring no two positions share the same ID within a single replay run.

---

### Requirement 8: No Strategy Drift

**User Story:** As a developer, I want a strict guarantee that the replay engine introduces no new alpha logic, signal changes, or behavioural modifications to the strategy pipeline, so that replay results are a faithful reproduction of what the live engine would have done.

#### Acceptance Criteria

1. THE Replay_Engine SHALL call `process_bar` with the same argument contract as `run_live`, passing `candles`, `closed_i`, `symbol`, `config`, `risk`, `state`, `bid`, `ask`, and `now_s`.
2. THE Replay_Engine SHALL mirror all `EngineState` field updates that `run_live` performs in the same conditional branch. The authoritative rule for `state.last_successful_open_mono` is defined in Requirement 2.2; no additional state update rules are defined here.
3. THE Replay_Engine SHALL not import or call any function from the `strategy` package directly; all strategy execution SHALL occur exclusively through `process_bar`.
4. THE Replay_Engine SHALL not alter `config` module attributes at runtime.
5. WHEN `on_intent` callback is provided, THE Replay_Engine SHALL invoke it with the `OrderIntent` from `decision.intent` only under the same conditions as `run_live`; WHEN those conditions are not met, THE Replay_Engine SHALL skip the callback even if `decision.intent` is non-`None`.
