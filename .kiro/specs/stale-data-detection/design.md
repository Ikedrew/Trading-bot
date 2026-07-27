# Design Document: Stale Data Detection

## Overview

This design adds a lightweight `StaleDataMonitor` class that tracks tick and candle freshness per symbol within `run_live`. It uses timestamp comparisons and wall-clock elapsed time to detect frozen feeds, escalates through three severity tiers, and integrates with the existing reconnect state machine by setting `mt5_state = _MT5_DISCONNECTED` when critical thresholds are breached.

## Architecture

### Component: StaleDataMonitor

A single class instantiated per symbol inside `run_live`. It holds O(1) state and exposes two methods called each iteration:

```
StaleDataMonitor
├── on_tick(tick_time: int, wall_clock: float) -> StaleCheckResult
├── on_candle(candle_time: int, wall_clock: float) -> StaleCheckResult
└── check_heartbeat(wall_clock: float) -> StaleCheckResult
```

### Data Flow (per iteration in run_live)

```
feed.last_tick() ──→ monitor.on_tick(tick_time, now)
                         │
                         ▼
              [tick stale? → log warning]
                         │
feed.copy_rates_closed() ──→ monitor.on_candle(candle_time, now)
                                  │
                                  ▼
                       [candle stale? → escalate]
                                  │
              monitor.check_heartbeat(now)
                         │
                         ▼
              [heartbeat lost? → mt5_state = DISCONNECTED]
```

### Integration Points

1. **run_live loop** — StaleDataMonitor instantiated after symbol resolution, checked each iteration
2. **MT5DataFeed.last_tick()** — Extended to return `(bid, ask, tick_time)` tuple
3. **Reconnect state machine** — Monitor sets `mt5_state` variable directly (same scope)
4. **Config module** — New constants added with `getattr` fallback pattern
5. **Exception containment** — All monitor calls wrapped in try/except within run_live

### StaleCheckResult

```python
@dataclass
class StaleCheckResult:
    is_stale: bool
    escalation_level: int  # 0=normal, 1=warning, 2=escalation, 3=critical
    stale_duration_seconds: float
    action: str  # "none", "log_warning", "log_escalation", "force_disconnect"
```

## Detailed Design

### StaleDataMonitor Class

```python
class StaleDataMonitor:
    def __init__(self, symbol: str, config_module):
        self.symbol = symbol
        self.last_tick_time: int | None = None
        self.last_candle_time: int | None = None
        self.last_data_update_time: float | None = None
        self.stale_state: bool = False
        self._tick_stale_since: float | None = None
        self._candle_stale_since: float | None = None
        self._escalation_level: int = 0

        # Config with defaults
        self.stale_tick_timeout = getattr(config_module, "STALE_TICK_TIMEOUT_SECONDS", 30.0)
        self.stale_candle_timeout = getattr(config_module, "STALE_CANDLE_TIMEOUT_SECONDS", 600.0)
        self.heartbeat_timeout = getattr(config_module, "MARKET_HEARTBEAT_TIMEOUT_SECONDS", 120.0)
        self.escalation_warning = getattr(config_module, "STALE_ESCALATION_WARNING_SECONDS", 60.0)
        self.escalation_critical = getattr(config_module, "STALE_ESCALATION_CRITICAL_SECONDS", 300.0)
```

### MT5DataFeed.last_tick() Change

Current signature: `def last_tick(self, symbol: str) -> tuple[float, float]`

New signature: `def last_tick(self, symbol: str) -> tuple[float, float, int]`

Returns `(bid, ask, tick_time)` where `tick_time` is `t.time` from `symbol_info_tick`.

### Escalation Logic

```python
def _compute_escalation(self, stale_duration: float) -> int:
    if stale_duration >= self.escalation_critical:
        return 3
    elif stale_duration >= self.escalation_warning:
        return 2
    elif stale_duration > 0:
        return 1
    return 0
```

### Config Additions (core/config.py)

```python
# --- Stale data detection ---
STALE_TICK_TIMEOUT_SECONDS = 30.0
STALE_CANDLE_TIMEOUT_SECONDS = 600.0
MARKET_HEARTBEAT_TIMEOUT_SECONDS = 120.0
STALE_ESCALATION_WARNING_SECONDS = 60.0
STALE_ESCALATION_CRITICAL_SECONDS = 300.0
```

## File Changes

| File | Change |
|------|--------|
| `core/stale_monitor.py` | New file — StaleDataMonitor class |
| `core/config.py` | Add 5 new config constants |
| `core/loop.py` | Instantiate monitor, call on_tick/on_candle/check_heartbeat, exception wrap |
| `data/mt5_data.py` | Extend `last_tick()` to return tick timestamp |

## Correctness Properties

### Property 1: Fresh tick always updates state
For all tick timestamps `t2 > t1` (where `t1` is the current `last_tick_time`), after calling `on_tick(t2, wall_clock)`, `monitor.last_tick_time == t2` and `monitor.stale_state == False`.

### Property 2: Stale tick never updates last_tick_time
For all tick timestamps `t2 <= t1` (where `t1` is the current `last_tick_time`), after calling `on_tick(t2, wall_clock)`, `monitor.last_tick_time == t1` (unchanged).

### Property 3: Escalation level monotonically maps to duration
For all stale durations `d >= 0`:
- `d < escalation_warning` → level 1
- `escalation_warning <= d < escalation_critical` → level 2
- `d >= escalation_critical` → level 3

The escalation level is a pure function of duration and thresholds.

### Property 4: Fresh data always resets escalation
For all states where `escalation_level > 0`, receiving fresh data (tick or candle with newer timestamp) resets `escalation_level` to 0 and `stale_state` to False.

### Property 5: Heartbeat loss triggers disconnect action
For all wall clock values `now` where `now - last_data_update_time > heartbeat_timeout`, `check_heartbeat(now)` returns `action == "force_disconnect"`.

### Property 6: Exception containment preserves loop continuation
For all exceptions raised within StaleDataMonitor methods, the run_live loop catches the exception and continues to the next iteration without termination.

### Property 7: Candle staleness respects timeout threshold
For all elapsed durations `d` since last candle progression:
- `d <= stale_candle_timeout` → no candle stale escalation
- `d > stale_candle_timeout` → candle stale event triggered

### Property 8: Round-trip — last_tick returns valid tick_time
For all successful `symbol_info_tick` calls returning a tick object with `time` field, `last_tick()` returns a 3-tuple where the third element equals `tick.time`.

## Testing Strategy

- **Property-based tests**: Properties 1–5, 7 using Hypothesis to generate arbitrary timestamps, durations, and state sequences
- **Unit tests**: Config defaults, initialization, escalation level computation
- **Integration tests**: Exception containment (Property 6), last_tick API change (Property 8), reconnect state machine interaction
