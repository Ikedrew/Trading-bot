# Implementation Plan: Trade Management Event-Driven Refactor

## Overview

Three files change, all additively. `events.py` gains two enum values and a new dataclass. `manager.py` gains `ON_SL_MODIFIED` emission and the `on_position_update` reconciliation method. `loop.py` gains `_LoopTradeListener` and wires it into the `TradeStateManager` constructor. No existing logic is removed or rewritten. Behavioural parity is a hard constraint throughout.

## Tasks

- [ ] 1. Extend `events.py` with two new event types and `PositionEvent` dataclass
  - [ ] 1.1 Add `ON_SL_MODIFIED` and `ON_POSITION_UPDATE` to `TradeLifecycleEvent`
    - Open `core/trade_management/events.py`
    - Append `ON_SL_MODIFIED = "on_sl_modified"` and `ON_POSITION_UPDATE = "on_position_update"` to the `TradeLifecycleEvent` enum after the existing `ON_MANAGEMENT_EXIT` entry
    - Do not alter any existing enum values or their string representations
    - _Requirements: 2.1_

  - [ ] 1.2 Add `PositionEvent` frozen dataclass to `events.py`
    - In `core/trade_management/events.py`, import `PositionStatus` from `core.trade_management.position`
    - Add a `@dataclass(frozen=True)` class `PositionEvent` with fields: `position_id: str`, `symbol: str`, `status: PositionStatus`, `stop_loss: float`, `take_profit: float`, `volume: float`, `time_s: float`
    - Place it after `TradeEvent` and before `TradeLifecycleListener`
    - _Requirements: 3.5_

  - [ ]* 1.3 Write unit tests for `events.py` additions
    - Verify `TradeLifecycleEvent.ON_SL_MODIFIED` and `ON_POSITION_UPDATE` are present in the enum
    - Verify `PositionEvent` is frozen (attempting mutation raises `FrozenInstanceError`)
    - Verify all seven `PositionEvent` fields are present with correct types
    - _Requirements: 2.1, 3.5_

- [ ] 2. Emit `ON_SL_MODIFIED` in `manager.py` on every stop-loss change
  - [ ] 2.1 Capture old SL and emit `ON_SL_MODIFIED` after break-even update
    - In `core/trade_management/manager.py`, inside `_process_one_position`, locate the break-even block: `if new_sl is not None and new_sl != pos.stop_loss:`
    - Before mutating `pos.stop_loss`, capture `old_sl = pos.stop_loss`
    - After `self._push_stops_to_server_if_possible(pos)`, call `self._emit(TradeLifecycleEvent.ON_SL_MODIFIED, pos, (bid, ask), ts, {"previous_sl": old_sl, "new_sl": new_sl, "source": "break_even"})`
    - The mutation of `pos.stop_loss` and the broker push are unchanged; only the emit call is added
    - _Requirements: 1.4, 2.2_

  - [ ] 2.2 Capture old SL and emit `ON_SL_MODIFIED` after trailing update
    - In the same method, locate the trailing block: `if trail is not None and trail != pos.stop_loss:`
    - Before mutating `pos.stop_loss`, capture `old_sl = pos.stop_loss`
    - After `self._push_stops_to_server_if_possible(pos)`, call `self._emit(TradeLifecycleEvent.ON_SL_MODIFIED, pos, (bid, ask), ts, {"previous_sl": old_sl, "new_sl": trail, "source": "trailing"})`
    - _Requirements: 1.4, 2.2_

  - [ ]* 2.3 Write property test — SL monotonicity for BUY positions
    - **Property: SL monotonicity (BUY)** — for a BUY position, `stop_loss` must never decrease after any sequence of break-even or trailing updates
    - Use Hypothesis to generate sequences of ascending bid/ask prices and assert `pos.stop_loss` is non-decreasing across all `ON_SL_MODIFIED` events
    - **Validates: Requirements 8.2**

  - [ ]* 2.4 Write property test — SL monotonicity for SELL positions
    - **Property: SL monotonicity (SELL)** — for a SELL position, `stop_loss` must never increase after any sequence of break-even or trailing updates
    - Use Hypothesis to generate sequences of descending bid/ask prices and assert `pos.stop_loss` is non-increasing across all `ON_SL_MODIFIED` events
    - **Validates: Requirements 8.2**

  - [ ]* 2.5 Write property test — `ON_SL_MODIFIED` detail consistency
    - **Property: ON_SL_MODIFIED detail consistency** — every emitted `ON_SL_MODIFIED` event must satisfy `detail["previous_sl"] != detail["new_sl"]`
    - Collect all events emitted during a Hypothesis-driven price sequence and assert no `ON_SL_MODIFIED` event has equal `previous_sl` and `new_sl`
    - **Validates: Requirements 2.2**

  - [ ]* 2.6 Write property test — listener receives exactly one `ON_SL_MODIFIED` per SL change
    - **Property: Listener receives ON_SL_MODIFIED** — every SL change in `_process_one_position` must produce exactly one `ON_SL_MODIFIED` event with correct `previous_sl` and `new_sl`
    - Drive `on_price_update` with a price that triggers break-even; assert listener received exactly one `ON_SL_MODIFIED` with matching values
    - **Validates: Requirements 2.2, 1.4**

- [ ] 3. Checkpoint — verify existing tests pass and SL emission is correct
  - Ensure all existing tests pass, ensure `ON_SL_MODIFIED` is emitted for both break-even and trailing paths, ask the user if questions arise.

- [ ] 4. Add `on_position_update` reconciliation method to `manager.py`
  - [ ] 4.1 Import `PositionEvent` in `manager.py`
    - In `core/trade_management/manager.py`, add `PositionEvent` to the import from `core.trade_management.events`
    - _Requirements: 3.1_

  - [ ] 4.2 Implement `on_position_update` method
    - Add the method `on_position_update(self, position_event: PositionEvent) -> None` to `TradeStateManager`
    - Lookup `pos = self._by_id.get(position_event.position_id)`; if `None`, log a warning and return (Requirement 3.4)
    - Emit `ON_POSITION_UPDATE` with `pos`, `(0.0, 0.0)`, `position_event.time_s`, `{"broker_event": position_event}`
    - If `position_event.status == PositionStatus.CLOSED`, call `self._close_local(pos, TradeLifecycleEvent.ON_TRADE_CLOSE, (0.0, 0.0), position_event.time_s, {"reason": "broker_closed"})` and return (Requirement 3.2)
    - If `position_event.stop_loss != pos.stop_loss` or `position_event.take_profit != pos.take_profit`, update `pos.stop_loss` and `pos.take_profit` to broker values (Requirement 3.3)
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

  - [ ]* 4.3 Write property test — unknown position ID is a no-op
    - **Property: on_position_update unknown ID** — calling `on_position_update` with an unknown `position_id` must not raise and must not modify any tracked position
    - Use Hypothesis to generate arbitrary `position_id` strings not in `_by_id`; assert no exception and no state change
    - **Validates: Requirements 3.4**

  - [ ]* 4.4 Write property test — CLOSED status removes position from `positions_open()`
    - **Property: on_position_update CLOSED** — after `on_position_update` with `status=CLOSED`, the position must have `status=PositionStatus.CLOSED` and must not appear in `positions_open()`
    - Register a position, call `on_position_update` with `status=CLOSED`, assert `positions_open()` is empty and `pos.status == CLOSED`
    - **Validates: Requirements 3.2**

  - [ ]* 4.5 Write property test — SL/TP sync from broker
    - **Property: on_position_update SL sync** — after `on_position_update` with a different SL, `pos.stop_loss` must equal `position_event.stop_loss`
    - Use Hypothesis to generate SL/TP values differing from the local position; assert sync after the call
    - **Validates: Requirements 3.3**

- [ ] 5. Wire `_LoopTradeListener` into `loop.py`
  - [ ] 5.1 Add `_LoopTradeListener` class to `loop.py`
    - In `core/loop.py`, add the necessary imports: `TradeEvent`, `TradeLifecycleEvent` from `core.trade_management.events`
    - Define `class _LoopTradeListener:` with method `on_trade_event(self, event: TradeEvent) -> None`
    - Inside the method, define `_NOTIFY_KINDS` as the set: `{ON_TRADE_OPEN, ON_STOP_LOSS_HIT, ON_TAKE_PROFIT_HIT, ON_MANAGEMENT_EXIT, ON_SL_MODIFIED, ON_POSITION_UPDATE}`
    - If `event.kind not in _NOTIFY_KINDS`, return immediately
    - Otherwise call `logger.info("TRADE_EVENT | kind=%s | position_id=%s | symbol=%s | time=%.0f", event.kind.value, event.position.position_id, event.position.symbol, event.time_s)`
    - Place the class definition before `run_replay`
    - _Requirements: 4.1, 4.2_

  - [ ] 5.2 Pass `_LoopTradeListener` to `TradeStateManager` in `run_live`
    - In `run_live`, locate the `TradeStateManager(...)` constructor call
    - Add `listener=_LoopTradeListener()` as a keyword argument
    - The `execution=execution` argument and all other arguments remain unchanged
    - _Requirements: 4.1_

  - [ ]* 5.3 Write unit tests for `_LoopTradeListener` filtering
    - Verify that `ON_TRADE_OPEN`, `ON_STOP_LOSS_HIT`, `ON_TAKE_PROFIT_HIT`, `ON_MANAGEMENT_EXIT`, `ON_SL_MODIFIED`, `ON_POSITION_UPDATE` all produce a `logger.info` call
    - Verify that `ON_PRICE_UPDATE`, `ON_PARTIAL_CLOSE`, `ON_TRADE_CLOSE` are silently ignored (no log call)
    - _Requirements: 4.2_

  - [ ]* 5.4 Write integration test — `on_price_update` call order preserved
    - Construct a `TradeStateManager` with a recording listener and drive `run_live` for one iteration using a mock feed
    - Assert `trade_manager.on_price_update` is called before `process_bar` on each poll iteration
    - _Requirements: 4.3_

- [ ] 6. Final checkpoint — behavioural parity verification
  - Ensure all tests pass. Run the replay loop against a fixed candle sequence and assert the sequence of `ON_STOP_LOSS_HIT` / `ON_TAKE_PROFIT_HIT` events is identical to the pre-refactor baseline. Ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- All changes are strictly additive — no existing function signatures, logic, or call sites are altered
- Behavioural parity (Requirement 8) is enforced by the constraint that `_process_one_position` evaluation order is unchanged; only emit calls are inserted
- `PositionStatus` is already defined in `position.py`; `events.py` must import it to type `PositionEvent.status`
- The `_close_local` helper in `manager.py` already emits `ON_TRADE_CLOSE` internally — `on_position_update` should call it directly rather than duplicating the close sequence
- Property tests should use `pytest` + `hypothesis`; no new test framework is required

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["1.3", "2.1", "2.2"] },
    { "id": 2, "tasks": ["2.3", "2.4", "2.5", "2.6", "4.1"] },
    { "id": 3, "tasks": ["4.2"] },
    { "id": 4, "tasks": ["4.3", "4.4", "4.5", "5.1"] },
    { "id": 5, "tasks": ["5.2"] },
    { "id": 6, "tasks": ["5.3", "5.4"] }
  ]
}
```
