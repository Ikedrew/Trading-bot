# V10 Runtime Context Audit

---

## Part 1 — AccountContext Audit

### MT5 `account_info()` Fields vs AccountContext

| MT5 Field | In AccountContext | Used By | Recommendation |
|---|---|---|---|
| `balance` | ✓ | Risk (sizing) | Keep |
| `equity` | ✓ | Risk (exposure) | Keep |
| `margin_free` | ✗ (in BrokerContext) | Execution (margin check) | Keep as-is — lives in BrokerContext |
| `margin` | ✗ | — | **Add** — used margin tells total exposure |
| `margin_level` | ✗ | — | **Add** — percentage margin health indicator |
| `profit` | ✗ | — | **Add** — floating P&L of open positions |
| `leverage` | ✗ | — | **Add** — needed for accurate position sizing |
| `currency` | ✗ | — | Add (informational — needed for cross-currency sizing) |
| `credit` | ✗ | — | Intentionally omit — not relevant to risk |
| `login` | ✗ | — | Intentionally omit — identity, not risk |
| `server` | ✗ | — | Intentionally omit — infrastructure, not risk |
| `company` | ✗ | — | Intentionally omit — metadata |
| `trade_mode` | ✗ | — | Intentionally omit — rarely changes |
| `limit_orders` | ✗ | — | Intentionally omit — rarely relevant |
| `name` | ✗ | — | Intentionally omit — metadata |

### Recommended Additions to AccountContext:

| Field | Type | Reason |
|---|---|---|
| `margin_used` | float | Total deployed margin — exposure indicator |
| `margin_level_pct` | float | Margin health (equity/margin × 100) — stopout proximity |
| `floating_profit` | float | Unrealized P&L — affects real equity |
| `leverage` | int | Account leverage — required for precise position sizing |
| `currency` | str | Account currency — needed when trading cross-currency pairs |

### Intentionally Excluded:

| Field | Reason |
|---|---|
| `login` | Identity — not relevant to risk calculations |
| `server` | Infrastructure metadata |
| `company` | Broker name — informational only |
| `credit` | Virtual balance — not real capital |
| `trade_mode` | Static account property, rarely changes |

---

## Part 2 — BrokerContext Audit

### MT5 `symbol_info()` Fields vs BrokerContext

| MT5 Field | In BrokerContext | Used By | Recommendation |
|---|---|---|---|
| `spread` | ✓ (from tick) | Execution (spread gate) | Keep |
| `trade_mode` | ✓ (as market_open) | Execution (session gate) | Keep |
| `bid` | ✗ | — | **Add** — current market price needed |
| `ask` | ✗ | — | **Add** — current market price needed |
| `digits` | ✗ | — | **Add** — decimal precision for order submission |
| `point` | ✗ | — | **Add** — minimum price increment (replaces pip_size inference) |
| `trade_tick_size` | ✗ | — | Add — order price rounding |
| `trade_tick_value` | ✗ | — | **Add** — monetary value per tick per lot (exact sizing) |
| `trade_contract_size` | ✗ | — | **Add** — contract size (exact sizing) |
| `volume_min` | ✗ | — | **Add** — minimum order size (validation) |
| `volume_max` | ✗ | — | **Add** — maximum order size (validation) |
| `volume_step` | ✗ | — | **Add** — lot step for rounding |
| `trade_freeze_level` | ✗ | — | Add — freeze zone distance from price |
| `trade_stops_level` | ✗ | — | **Add** — minimum SL/TP distance from price |
| `trade_exemode` | ✗ | — | Add — execution mode (instant/exchange/market) |
| `filling_mode` | ✗ | — | Add — order filling type |
| `swap_long` | ✗ | — | Intentionally omit — overnight only |
| `swap_short` | ✗ | — | Intentionally omit — overnight only |

### Recommended Additions to BrokerContext:

| Field | Type | Reason |
|---|---|---|
| `bid` | float | Current bid — needed for entry/stop validation |
| `ask` | float | Current ask — needed for entry/stop validation |
| `digits` | int | Price decimal places — order submission |
| `point` | float | Minimum price change — replaces inferred pip_size |
| `tick_value` | float | $/tick/lot — **exact position sizing** (eliminates pip_value_per_lot=10 approximation) |
| `contract_size` | float | Lots to units conversion |
| `volume_min` | float | Min order size — prevents invalid orders |
| `volume_max` | float | Max order size — prevents rejection |
| `volume_step` | float | Lot rounding — prevents invalid volume |
| `stops_level` | int | Min SL/TP distance in points — prevents order rejection |

### Intentionally Excluded:

| Field | Reason |
|---|---|
| `swap_long/short` | Only relevant for overnight positions (future) |
| `freeze_level` | Rarely constrains M5 trades |
| `margin_initial/maintenance` | Derived from leverage + contract_size |
| Session schedule fields | Market_open from trade_mode is sufficient |

---

## Part 3 — Runtime Provider Completeness

### Current Provider (`core/runtime/account_provider.py`):

| MT5 API | Called? | Purpose |
|---|---|---|
| `mt5.account_info()` | ✓ | Balance, equity |
| `mt5.terminal_info()` | ✓ | Connection status |
| `mt5.symbol_info(symbol)` | ✓ | Symbol availability, trade_mode |
| `mt5.symbol_info_tick(symbol)` | ✓ (fallback) | Spread when bid/ask not provided |
| `mt5.positions_total()` | ✓ | Open position count |
| `mt5.positions_get()` | ✗ | **Missing** — per-symbol position details |
| `mt5.orders_total()` | ✗ | Missing — pending orders count |

### Recommended Addition:

`mt5.positions_get(symbol=symbol)` — would provide:
- Exact per-symbol position count (vs total)
- Current profit per position
- Volume already deployed

---

## Part 4 — Hardcoded Numeric Value Audit

### In `core/v10/risk_engine.py`:

| Value | Location | Classification | Action |
|---|---|---|---|
| `0.0025` | `DEFAULT_RISK_PCT` | **Configuration** | Move to config.py |
| `1.5` | `MIN_RR` | **Business Rule** | Move to config.py |
| `0.04` | `MAX_DAILY_LOSS_PCT` | **Configuration** | Move to config.py |
| `3` | `MAX_OPEN_POSITIONS` | **Configuration** | Move to config.py |
| `0.03` | `MAX_TOTAL_RISK_PCT` | **Configuration** | Move to config.py |
| `2` | `MAX_SYMBOL_EXPOSURE` | **Configuration** | Move to config.py |
| `0.75` | Breakout size modifier | **Business Rule** | Keep in engine (strategy-specific) |
| `0.75` | Extended horizon modifier | **Business Rule** | Keep in engine (horizon-specific) |
| `2.0` | Breakout min R:R | **Business Rule** | Keep in engine |
| `10.0` | `pip_value_per_lot` (FX) | **Accidental approximation** | **Replace with `tick_value` from MT5** |
| `1.0` | `pip_value_per_lot` (Index) | **Accidental approximation** | **Replace with `tick_value` from MT5** |

### In `core/v10/execution_engine.py`:

| Value | Location | Classification | Action |
|---|---|---|---|
| `0.30` | `MAX_SPREAD_ATR_RATIO` | **Configuration** | Move to config.py |
| `2.0` | `DEFAULT_SLIPPAGE_PIPS` | **Configuration** | Move to config.py |
| `5.0` | `DEFAULT_TIMEOUT` | **Configuration** | Move to config.py |

### In `core/v10/horizon_engine.py`:

| Value | Location | Classification | Action |
|---|---|---|---|
| `5.0, 20.0` | SCALP FX pips | **Instrument Policy** | Keep (horizon definition) |
| `20.0, 50.0` | INTRADAY FX pips | **Instrument Policy** | Keep |
| `50.0, 150.0` | EXTENDED FX pips | **Instrument Policy** | Keep |
| `10.0, 50.0` | SCALP index points | **Instrument Policy** | Keep |
| `50.0, 150.0` | INTRADAY index points | **Instrument Policy** | Keep |
| `150.0, 500.0` | EXTENDED index points | **Instrument Policy** | Keep |
| `0.6` | HTF strong trend threshold | **Business Rule** | Keep |
| `0.2` | HTF weak threshold | **Business Rule** | Keep |
| `10, 30` | Liquidity distance thresholds (pips) | **Instrument Policy** | Keep |

### In `core/v10/opportunity_engine.py`:

| Value | Location | Classification | Action |
|---|---|---|---|
| `0.35, 0.30, 0.15, 0.20` | Quality weights | **Business Rule** | Keep (documented) |
| `0.60` | VALID threshold | **Business Rule** | Keep |
| `0.40` | WATCHING threshold | **Business Rule** | Keep |
| `0.4, 0.3` | Location/structure minimums | **Business Rule** | Keep |

### Accidental Defaults Found:

| Value | File | Issue |
|---|---|---|
| `pip_value_per_lot = 10.0` | risk_engine.py | **Should use MT5 tick_value** |
| `pip_value_per_lot = 1.0` | risk_engine.py | **Should use MT5 tick_value** |

These are the ONLY remaining approximations. They're in `_calculate_position_size()` and should be replaced with the actual `tick_value` from `symbol_info()`.

---

## Part 5 — Configuration Audit

### Values that should be in `config.py` (currently hardcoded in V10 engines):

| Value | Current Location | Should Be |
|---|---|---|
| `DEFAULT_RISK_PCT = 0.0025` | risk_engine.py | `config.V10_RISK_PER_TRADE = 0.0025` |
| `MIN_RR = 1.5` | risk_engine.py | `config.V10_MIN_RR = 1.5` |
| `MAX_DAILY_LOSS_PCT = 0.04` | risk_engine.py | Already exists: `config.DAILY_LOSS_LIMIT_PERCENT` |
| `MAX_OPEN_POSITIONS = 3` | risk_engine.py | Already exists: `config.MAX_TOTAL_OPEN_POSITIONS` |
| `MAX_TOTAL_RISK_PCT = 0.03` | risk_engine.py | `config.V10_MAX_TOTAL_RISK_PCT = 0.03` |
| `MAX_SYMBOL_EXPOSURE = 2` | risk_engine.py | Already exists: `config.MAX_TRADES_PER_DAY_PER_SYMBOL` |
| `MAX_SPREAD_ATR_RATIO = 0.30` | execution_engine.py | Already exists: `config.MAX_SPREAD_ATR_RATIO` |
| `DEFAULT_SLIPPAGE_PIPS = 2.0` | execution_engine.py | `config.V10_MAX_SLIPPAGE_PIPS = 2.0` |
| `DEFAULT_TIMEOUT = 5.0` | execution_engine.py | `config.V10_EXECUTION_TIMEOUT = 5.0` |

---

## Part 6 — Runtime Dependency Map

```
MT5 Terminal
   │
   ├── mt5.account_info()
   │       ├── balance ──────────────► AccountContext.balance
   │       ├── equity ───────────────► AccountContext.equity
   │       ├── margin_free ──────────► BrokerContext.available_margin
   │       └── (missing: leverage, margin, profit, currency)
   │
   ├── mt5.terminal_info()
   │       └── connected ────────────► BrokerContext.connected
   │
   ├── mt5.symbol_info(symbol)
   │       ├── trade_mode ───────────► BrokerContext.market_open
   │       └── (missing: point, tick_value, volume_min/max/step, stops_level, digits)
   │
   ├── mt5.symbol_info_tick(symbol)
   │       └── ask - bid ────────────► BrokerContext.spread
   │
   └── mt5.positions_total()
           └── count ────────────────► BrokerContext.existing_positions
                                       │
                                       ▼
                          ┌─────────────────────────────┐
                          │ V10 Pipeline                 │
                          │                             │
                          │ Risk Engine:                │
                          │   AccountContext.balance    │
                          │   AccountContext.equity     │
                          │   → position sizing        │
                          │   → exposure checks        │
                          │                             │
                          │ Execution Engine:           │
                          │   BrokerContext.connected   │
                          │   BrokerContext.spread      │
                          │   BrokerContext.market_open │
                          │   BrokerContext.margin      │
                          │   → execution gating       │
                          └─────────────────────────────┘
                                       │
                                       ▼
                              ExecutionDecision
```

### Missing Dependency (Critical):

```
mt5.symbol_info(symbol).trade_tick_value  ──►  Risk Engine._calculate_position_size()
                                                 (currently approximated as 10.0 / 1.0)
```

---

## Part 7 — Recommended Additions

### Priority 1 (Correctness):
1. **`tick_value` from `symbol_info()`** — replaces the `pip_value_per_lot = 10.0` approximation in position sizing
2. **`volume_min/max/step`** — prevents order rejection from invalid lot sizes
3. **`stops_level`** — prevents order rejection from SL/TP too close to price

### Priority 2 (Completeness):
4. `leverage` — more accurate sizing model
5. `margin_used` / `floating_profit` — real-time exposure picture
6. `bid/ask` in BrokerContext — eliminates passing them separately
7. `digits` / `point` — proper price rounding for order submission

### Priority 3 (Future):
8. `currency` — cross-currency pair sizing
9. `execution_mode` — adapt order type to broker's execution model

---

## Part 8 — Fields That Should Remain Excluded

| Field | Reason |
|---|---|
| `login` | Identity — no risk/execution use |
| `server` | Infrastructure — no trading use |
| `company` | Metadata — no trading use |
| `credit` | Virtual balance — not real risk capital |
| `swap_long/short` | Overnight positions not in scope (M5 intraday) |
| `margin_initial/maintenance` | Derivable from leverage + contract_size |
| `time`/`time_msc` (from tick) | Already tracked by scanner's bar provision |

---

## Part 9 — Final Verdict

### The runtime providers correctly read live MT5 data for the ESSENTIAL fields.

**No accidental hardcoded account values remain in the pipeline.** The `balance=10000` default has been removed. Account unavailability causes clean rejection.

### Remaining gaps:

| Gap | Impact | Severity |
|---|---|---|
| Position sizing uses `pip_value_per_lot = 10.0` approximation | Position size may be ~10-30% off for cross-currency pairs and indices | **MEDIUM** — must fix before live |
| No `volume_min/max/step` check | Orders may be rejected by broker for invalid lot size | **MEDIUM** — must fix before live |
| No `stops_level` check | Orders may be rejected if SL/TP within freeze zone | **LOW** — rare at structural stops |
| Risk constants hardcoded in engine (not config) | Less flexible but functionally correct | **LOW** — convenience improvement |

### Verdict:

**The providers are functionally correct for shadow trading.** The pipeline will reject cleanly when MT5 is unavailable. The two medium-severity gaps (`tick_value` for sizing, `volume_min/max/step` for validation) must be resolved before live capital deployment but do not block shadow observation or paper trading.
