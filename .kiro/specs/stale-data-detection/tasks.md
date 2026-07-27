# Tasks: Stale Data Detection

## Task 1: Add Configuration Constants

### Description
Add the 5 stale data detection configuration parameters to `core/config.py`.

### Files to Modify
- `core/config.py`

### Steps
- [x] 1.1 Add STALE_TICK_TIMEOUT_SECONDS = 30.0 to config.py
- [x] 1.2 Add STALE_CANDLE_TIMEOUT_SECONDS = 600.0 to config.py
- [x] 1.3 Add MARKET_HEARTBEAT_TIMEOUT_SECONDS = 120.0 to config.py
- [x] 1.4 Add STALE_ESCALATION_WARNING_SECONDS = 60.0 to config.py
- [x] 1.5 Add STALE_ESCALATION_CRITICAL_SECONDS = 300.0 to config.py

---

## Task 2: Extend MT5DataFeed.last_tick() to Return Tick Timestamp

### Description
Modify `last_tick()` to return `(bid, ask, tick_time)` instead of `(bid, ask)`. Update all callers in `run_live` to unpack the new tuple.

### Files to Modify
- `data/mt5_data.py`
- `core/loop.py`

### Steps
- [x] 2.1 Modify `MT5DataFeed.last_tick()` to return `tuple[float, float, int]` with `t.time` as third element
- [x] 2.2 Update the `bid, ask = feed.last_tick(symbol)` call in `run_live` to `bid, ask, tick_time = feed.last_tick(symbol)`
- [x] 2.3 Verify no other callers of `last_tick()` exist in the codebase that would break

---

## Task 3: Create StaleDataMonitor Class

### Description
Create `core/stale_monitor.py` with the `StaleDataMonitor` class and `StaleCheckResult` dataclass.

### Files to Create
- `core/stale_monitor.py`

### Steps
- [x] 3.1 Create `StaleCheckResult` dataclass with fields: `is_stale`, `escalation_level`, `stale_duration_seconds`, `action`
- [x] 3.2 Create `StaleDataMonitor.__init__()` with symbol, config reading via getattr, and initialization of all tracking fields
- [x] 3.3 Implement `on_tick(tick_time: int, wall_clock: float) -> StaleCheckResult` — compare tick_time to last_tick_time, update state, detect staleness
- [x] 3.4 Implement `on_candle(candle_time: int, wall_clock: float) -> StaleCheckResult` — compare candle_time to last_candle_time, apply timeout threshold
- [x] 3.5 Implement `check_heartbeat(wall_clock: float) -> StaleCheckResult` — check elapsed since last_data_update_time against heartbeat timeout
- [x] 3.6 Implement `_compute_escalation(stale_duration: float) -> int` — map duration to escalation level (0/1/2/3)
- [x] 3.7 Implement escalation reset logic — fresh data clears stale_state, resets escalation_level and stale_since trackers

---

## Task 4: Integrate StaleDataMonitor into run_live

### Description
Instantiate the monitor in `run_live`, call it each iteration after tick/candle retrieval, and act on results (logging + state transitions).

### Files to Modify
- `core/loop.py`

### Steps
- [x] 4.1 Import StaleDataMonitor and StaleCheckResult at top of loop.py
- [x] 4.2 Instantiate `stale_monitor = StaleDataMonitor(symbol, config)` after symbol resolution in run_live
- [x] 4.3 After `feed.last_tick()` succeeds, call `stale_monitor.on_tick(tick_time, time.time())` and log based on result
- [x] 4.4 After `feed.copy_rates_closed()` succeeds, call `stale_monitor.on_candle(candles[closed_i].time, time.time())` and log based on result
- [x] 4.5 Call `stale_monitor.check_heartbeat(time.time())` each iteration and set `mt5_state = _MT5_DISCONNECTED` if action is "force_disconnect"
- [x] 4.6 Wrap all stale_monitor calls in try/except to ensure exceptions never crash the loop — log as `[STALE_MONITOR_ERROR]`
- [x] 4.7 On escalation level 3 or heartbeat loss, log `[STALE_DATA_CRITICAL]` before transitioning state

---

## Task 5: Write Property-Based Tests

### Description
Write property-based tests using Hypothesis for the core StaleDataMonitor logic.

### Files to Create
- `tests/test_stale_monitor_properties.py`

### Steps
- [x] 5.1 Write property test: fresh tick always updates last_tick_time and clears stale state `[PBT]`
- [x] 5.2 Write property test: stale tick (timestamp <= previous) never updates last_tick_time `[PBT]`
- [x] 5.3 Write property test: escalation level is a pure function of duration — monotonically maps to correct tier `[PBT]`
- [x] 5.4 Write property test: fresh data always resets escalation to level 0 `[PBT]`
- [x] 5.5 Write property test: heartbeat loss triggers force_disconnect when elapsed > timeout `[PBT]`
- [x] 5.6 Write property test: candle staleness only escalates after timeout threshold exceeded `[PBT]`

---

## Task 6: Write Unit and Integration Tests

### Description
Write unit tests for config defaults, initialization, and integration tests for exception containment and API compatibility.

### Files to Create
- `tests/test_stale_monitor_unit.py`

### Steps
- [x] 6.1 Test StaleDataMonitor initialization sets all fields to expected defaults
- [x] 6.2 Test config parameter reading with custom values overrides defaults
- [x] 6.3 Test last_tick() returns 3-tuple with correct tick_time
- [x] 6.4 Test exception within monitor methods is caught and loop continues (mock monitor to raise)
- [x] 6.5 Test escalation level 3 sets mt5_state to DISCONNECTED (integration with loop logic)
- [x] 6.6 Test recovery: fresh data after stale period resets all stale tracking and logs recovery
