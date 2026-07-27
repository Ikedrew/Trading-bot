# Technical Design — Multi-Symbol Scan Engine

## Overview

Refactors the trading bot from a per-symbol blocking architecture into a scan-based engine where a single loop iteration processes ALL symbols. Both replay and live modes use the same scanner pattern: one loop cycle = one pass across all symbols.

---

## Architecture

### Current (Per-Symbol Blocking)

```
main.py:
  for symbol in SYMBOLS:
    run_replay(symbol)    ← blocks until all 300 bars processed
    # or run_live(symbol) ← blocks indefinitely
```

### Target (Scan-Based)

```
main.py:
  run_replay(symbols=SYMBOLS)   ← single call, processes all symbols per cycle
  # or run_live(symbols=SYMBOLS)

run_replay:
  for each bar_index:
    for each symbol:
      process_bar(symbol)       ← all symbols advance together

run_live:
  while running:
    for each symbol:
      fetch_tick(symbol)
      fetch_candles(symbol)
      if new_bar(symbol):
        process_bar(symbol)
```

---

## Components and Interfaces

### SymbolState (per-symbol runtime context)

```python
@dataclass
class SymbolState:
    symbol: str
    feed: MT5DataFeed
    engine_state: EngineState
    event_state: EventState
    trade_manager: TradeStateManager | None
    stale_monitor: StaleDataMonitor
    risk: RiskManager
    last_closed_time: int | None = None
    # Replay-specific
    candles: list[Candle] | None = None
    start_i: int = 0
```

### run_replay (multi-symbol)

```python
def run_replay(*, symbols: list[str] | None = None, on_intent=None) -> None:
    symbol_list = symbols or getattr(config, "SYMBOLS", [config.SYMBOL])
    
    # Initialize per-symbol state
    states: list[SymbolState] = []
    for sym_hint in symbol_list:
        feed = MT5DataFeed(sym_hint)
        feed.connect()
        resolved = feed.resolve_symbol()
        candles = feed.copy_rates_closed(resolved, config.TIMEFRAME, config.CANDLE_COUNT)
        states.append(SymbolState(
            symbol=resolved, feed=feed,
            engine_state=EngineState(), event_state=EventState(),
            trade_manager=None, stale_monitor=StaleDataMonitor(resolved, config),
            risk=_build_risk_manager(), candles=candles,
            start_i=max(config.SETUP_MA_PERIOD + 3, 2),
        ))
    
    # Determine iteration range (all symbols have same bar count)
    max_bars = min(len(s.candles) for s in states)
    start_i = max(s.start_i for s in states)
    
    for closed_i in range(start_i, max_bars):
        for sym_state in states:
            _active_symbol = sym_state.symbol
            # Process one bar for this symbol
            process_bar(...)
```

### run_live (multi-symbol)

```python
def run_live(*, symbols: list[str] | None = None, ...) -> None:
    symbol_list = symbols or getattr(config, "SYMBOLS", [config.SYMBOL])
    
    # Initialize per-symbol state
    states: list[SymbolState] = []
    for sym_hint in symbol_list:
        ...  # Same initialization pattern
    
    while running:
        # System-level heartbeat
        cycle_id += 1
        
        for sym_state in states:
            _active_symbol = sym_state.symbol
            # Health check, tick fetch, candle fetch, process_bar
            # All per-symbol, isolated
```

---

## Data Models

### SymbolState fields

| Field | Type | Purpose |
|---|---|---|
| `symbol` | str | Resolved broker symbol name |
| `feed` | MT5DataFeed | Data access (shared MT5 connection) |
| `engine_state` | EngineState | Bias, cooldown, regime per symbol |
| `event_state` | EventState | Log deduplication per symbol |
| `trade_manager` | TradeStateManager | Position tracking per symbol |
| `stale_monitor` | StaleDataMonitor | Feed freshness per symbol |
| `risk` | RiskManager | Risk params (shared config but separate instance) |
| `last_closed_time` | int | Candle deduplication per symbol |
| `candles` | list[Candle] | Replay: pre-fetched candle array |
| `start_i` | int | Replay: first processable bar index |

---

## Correctness Properties

### Property 1: Symbol Isolation
Each symbol has independent EngineState, EventState, and TradeStateManager. No state leaks between symbols within a cycle.

### Property 2: No Blocking
One symbol's failure (exception, stale data) does not prevent other symbols from being processed in the same cycle.

### Property 3: Cycle Atomicity
System-level heartbeat fires once per cycle (not per symbol). Per-symbol logs are prefixed with symbol name.

### Property 4: Replay Parity
All symbols advance one bar per cycle in replay mode — they stay synchronized in simulated time.

### Property 5: Live Independence
In live mode, each symbol's candle deduplication is independent — one symbol getting a new bar doesn't force others to process.

---

## Error Handling

- Per-symbol processing wrapped in try/except
- One symbol's exception → log error, skip to next symbol, continue cycle
- MT5 health check is system-level (shared connection) — if MT5 is down, all symbols enter degraded mode together
- Reconnect is system-level (one reconnect cycle restores all symbols)

---

## Testing Strategy

1. Replay with 2 symbols: verify both produce output
2. Verify symbol isolation: different bias states per symbol
3. Verify error isolation: one symbol with bad data doesn't crash others
4. Verify cycle count matches expected bar count

---

## File Changes

| File | Change |
|---|---|
| `main.py` | Remove per-symbol loop, call `run_replay(symbols=...)` / `run_live(symbols=...)` |
| `core/loop.py` | Refactor `run_replay` and `run_live` to accept `symbols: list[str]`, add `SymbolState`, inner symbol loop |
| `core/config.py` | No changes |
| `data/mt5_data.py` | No changes |
