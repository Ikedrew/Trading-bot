# Requirements Document

## Introduction

This feature refactors the trading bot's trade lifecycle management from a bar-driven model to a fully event-driven architecture. The goal is architectural hardening: `TradeStateManager` becomes the sole owner of all open position state and all SL/TP logic, responding only to well-defined events. The execution layer is reduced to a dumb executor that places and modifies orders when instructed and never calculates risk parameters. The `TradeLifecycleListener` protocol is formalised with all event types, and broker reconciliation is added via a new `on_position_update` handler. No trading outcomes change — this is a structural refactoring with behavioural parity as a hard constraint.

## Glossary

- **TradeStateManager**: The class in `core/trade_management/manager.py` that owns all open position state and drives the trade lifecycle.
- **TradeLifecycleListener**: The Protocol in `core/trade_management/events.py` that defines the observer interface for lifecycle events.
- **TradeLifecycleEvent**: The enum in `core/trade_management/events.py` enumerating all event types that `TradeStateManager` can emit.
- **TradeEvent**: The frozen dataclass carrying event kind, position snapshot, price snapshot, timestamp, and detail payload.
- **Position**: The dataclass in `core/trade_management/position.py` representing the authoritative local view of one managed position.
- **sl_tp_rules**: The module `core/trade_management/sl_tp_rules.py` containing all SL/TP calculation functions (break-even, trailing, exit trigger checks).
- **MT5Execution**: The class in `execution/mt5_execution.py` responsible for sending orders to MetaTrader 5.
- **process_bar**: The function in `core/engine.py` that evaluates a closed bar and produces an entry decision.
- **loop.py**: The module `core/loop.py` that drives the live and replay run loops.
- **PositionEvent**: A new data structure carrying broker-reported position state used for reconciliation via `on_position_update`.
- **ON_SL_MODIFIED**: A new `TradeLifecycleEvent` emitted when `TradeStateManager` modifies the stop-loss of a tracked position.
- **ON_POSITION_UPDATE**: A new `TradeLifecycleEvent` emitted when `TradeStateManager` processes a broker reconciliation update.
- **Behavioural Parity**: The constraint that all trading outcomes (entry prices, SL/TP levels, exit triggers, partial closes) remain identical before and after this refactoring.

---

## Requirements

### Requirement 1 — TradeStateManager as Sole Lifecycle Owner

**User Story:** As a developer, I want `TradeStateManager` to be the single authoritative owner of all open position state, so that trade lifecycle logic is not scattered across multiple modules.

#### Acceptance Criteria

1. THE `TradeStateManager` SHALL maintain the complete set of open and partial `Position` objects for a strategy session, with no other module holding a parallel authoritative copy.
2. WHEN `on_price_update(symbol, bid, ask, time_s)` is called, THE `TradeStateManager` SHALL evaluate all tracked positions for that symbol and apply SL/TP adjustments and exit checks in the order: unrealised PnL update → MFE update → break-even SL → trailing SL → exit trigger → partial TP.
3. THE `TradeStateManager` SHALL delegate all SL/TP calculations exclusively to functions defined in `sl_tp_rules.py`, with no SL/TP arithmetic duplicated inside `manager.py`.
4. WHEN a position's stop-loss is modified by `TradeStateManager`, THE `TradeStateManager` SHALL push the updated SL/TP to the broker via `MT5Execution.position_modify_sl_tp` before emitting any lifecycle event for that modification.

---

### Requirement 2 — Formalised TradeLifecycleListener Protocol

**User Story:** As a developer, I want the `TradeLifecycleListener` protocol to enumerate every event type that `TradeStateManager` can emit, so that listeners can handle all lifecycle transitions without inspecting internal state.

#### Acceptance Criteria

1. THE `TradeLifecycleEvent` enum SHALL contain the following event types: `ON_TRADE_OPEN`, `ON_PRICE_UPDATE`, `ON_PARTIAL_CLOSE`, `ON_TRADE_CLOSE`, `ON_STOP_LOSS_HIT`, `ON_TAKE_PROFIT_HIT`, `ON_MANAGEMENT_EXIT`, `ON_SL_MODIFIED`, `ON_POSITION_UPDATE`.
2. WHEN `TradeStateManager` modifies the stop-loss of a tracked position (break-even or trailing), THE `TradeStateManager` SHALL emit a `TradeEvent` with kind `ON_SL_MODIFIED` containing the previous SL value and the new SL value in the `detail` payload.
3. THE `TradeLifecycleListener` Protocol SHALL declare a single method `on_trade_event(event: TradeEvent) -> None` that covers all `TradeLifecycleEvent` kinds.
4. THE `TradeEvent` dataclass SHALL remain frozen and SHALL carry: `kind: TradeLifecycleEvent`, `position: Position`, `price_snapshot: tuple[float, float]`, `time_s: float`, `detail: dict[str, Any]`.

---

### Requirement 3 — Broker Reconciliation via on_position_update

**User Story:** As a developer, I want `TradeStateManager` to handle externally closed or modified positions reported by MT5, so that local state stays consistent with broker state without requiring a full restart.

#### Acceptance Criteria

1. THE `TradeStateManager` SHALL expose a method `on_position_update(position_event: PositionEvent) -> None` that accepts a broker-reported position state.
2. WHEN `on_position_update` is called with a `PositionEvent` whose `position_id` matches a tracked position and whose status is `CLOSED`, THE `TradeStateManager` SHALL mark the local `Position` as `PositionStatus.CLOSED` and emit a `TradeEvent` with kind `ON_POSITION_UPDATE` followed by `ON_TRADE_CLOSE`.
3. WHEN `on_position_update` is called with a `PositionEvent` whose `position_id` matches a tracked position and whose SL or TP differs from the local values, THE `TradeStateManager` SHALL update the local `Position.stop_loss` and `Position.take_profit` to match the broker values and emit a `TradeEvent` with kind `ON_POSITION_UPDATE`.
4. IF `on_position_update` is called with a `PositionEvent` whose `position_id` does not match any tracked position, THEN THE `TradeStateManager` SHALL log a warning and take no further action.
5. THE `PositionEvent` dataclass SHALL carry: `position_id: str`, `symbol: str`, `status: PositionStatus`, `stop_loss: float`, `take_profit: float`, `volume: float`, `time_s: float`.

---

### Requirement 4 — loop.py Subscription to TradeLifecycleListener

**User Story:** As a developer, I want `loop.py` to subscribe to `TradeStateManager` lifecycle events, so that the run loop can react to trade outcomes without polling internal state.

#### Acceptance Criteria

1. WHEN `TradeStateManager` is instantiated in `loop.py`, THE `loop.py` module SHALL pass a `TradeLifecycleListener` implementation as the `listener` argument.
2. WHEN `TradeStateManager` emits a `TradeEvent` with kind `ON_TRADE_OPEN`, `ON_STOP_LOSS_HIT`, `ON_TAKE_PROFIT_HIT`, `ON_MANAGEMENT_EXIT`, `ON_SL_MODIFIED`, or `ON_POSITION_UPDATE`, THE listener in `loop.py` SHALL log the event at INFO level including the event kind, position ID, symbol, and timestamp.
3. THE `loop.py` module SHALL call `trade_manager.on_price_update(symbol, bid, ask, time.time())` on every poll iteration before calling `process_bar`, preserving the existing call order.

---

### Requirement 5 — Execution Layer as Dumb Executor

**User Story:** As a developer, I want the execution layer to contain no SL/TP calculation or risk logic, so that all risk decisions are centralised in `TradeStateManager` and `sl_tp_rules.py`.

#### Acceptance Criteria

1. THE `MT5Execution` class SHALL expose only the following public methods: `place_market(intent: OrderIntent) -> ExecutionResult` and `position_modify_sl_tp(symbol, position_ticket, sl, tp) -> ExecutionResult`.
2. THE `MT5Execution.place_market` method SHALL use the `sl` and `tp` values from the `OrderIntent` directly, without recalculating or adjusting them.
3. THE `MT5Execution.position_modify_sl_tp` method SHALL send the provided `sl` and `tp` values to MT5 without modification.
4. IF `MT5Execution.DRY_RUN` is `True`, THEN THE `MT5Execution` SHALL return a successful `ExecutionResult` with comment `"dry_run"` for `place_market` and `"dry_run_modify"` for `position_modify_sl_tp` without sending any order to MT5.

---

### Requirement 6 — process_bar Restricted to Entry Decisions

**User Story:** As a developer, I want `process_bar` to produce only entry decisions and market context, so that trade management logic cannot leak into the bar evaluation pipeline.

#### Acceptance Criteria

1. THE `process_bar` function SHALL return a `UnifiedDecision` containing only entry signal data: `should_trade`, `intent`, `bias`, `patterns`, `score`, `reason`, and associated metadata.
2. THE `process_bar` function SHALL NOT read, write, or evaluate any `Position` object or any SL/TP value belonging to an open trade.
3. THE `process_bar` function SHALL NOT call any function from `sl_tp_rules.py`.
4. WHEN `process_bar` returns a `UnifiedDecision` with `should_trade=True`, THE `loop.py` module SHALL pass the resulting `OrderIntent` to `MT5Execution.place_market` and then call `TradeStateManager.register_from_execution` to register the new position.

---

### Requirement 7 — sl_tp_rules.py as Sole SL/TP Authority

**User Story:** As a developer, I want all SL/TP calculation logic to reside exclusively in `sl_tp_rules.py`, so that there is a single place to audit and modify risk rules.

#### Acceptance Criteria

1. THE `sl_tp_rules` module SHALL contain all functions for: exit trigger detection (`check_exit_trigger`), break-even SL calculation (`maybe_break_even_sl`), trailing SL calculation (`maybe_trailing_sl`), MFE extreme update (`update_mfe_extreme`), and risk unit calculation (`risk_unit_r`).
2. THE `sl_tp_rules` module SHALL NOT import from `core.trade_management.manager`, `core.engine`, `execution`, or `loop`.
3. WHEN `TradeStateManager` needs to evaluate a SL/TP rule, THE `TradeStateManager` SHALL call the corresponding function from `sl_tp_rules` and apply the returned value to the `Position`.
4. IF any module outside `core/trade_management/` calls a function from `sl_tp_rules.py` to modify an open position's SL or TP, THEN that call SHALL be removed as part of this refactoring.

---

### Requirement 8 — Behavioural Parity

**User Story:** As a developer, I want the refactored system to produce identical trading outcomes to the pre-refactoring system, so that the architectural change introduces no regression in live trading behaviour.

#### Acceptance Criteria

1. THE refactored system SHALL produce the same entry decisions (side, SL, TP, volume, pattern tag) as the pre-refactoring system for any given sequence of candles and tick data.
2. THE refactored system SHALL apply break-even SL, trailing SL, and exit triggers at the same price levels and in the same evaluation order as the pre-refactoring system.
3. THE refactored system SHALL emit `ON_STOP_LOSS_HIT` and `ON_TAKE_PROFIT_HIT` events at the same bid/ask thresholds as the pre-refactoring `check_exit_trigger` logic.
4. WHEN partial TP is configured, THE refactored system SHALL trigger partial close at the same path fraction and volume fraction as the pre-refactoring system.
5. THE refactored system SHALL preserve the existing `TradeManagementConfig` fields: `break_even_trigger_rr`, `break_even_buffer`, `trailing_step`, `trailing_start_rr`, `partial_tp_fraction`, `partial_tp_path_fraction`, `max_time_in_trade_seconds`.
