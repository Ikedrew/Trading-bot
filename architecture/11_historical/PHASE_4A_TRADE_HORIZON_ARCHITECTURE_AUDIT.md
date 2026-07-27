# PHASE 4A: TRADE HORIZON ARCHITECTURE AUDIT

**Date:** 2026-07-24
**Objective:** Identify all existing assumptions that constrain the system to a single-timeframe, single-behaviour model — and map the precise insertion points for multi-horizon intelligence.
**Status:** Audit only. No implementation.

---

## 1. SL/TP Generation Assumptions

### Current Architecture

```
run_new_engine()
  → risk_manager.evaluate(assessment, candles, bid, ask)
    → _execute_risk(symbol, signal, candles, bid, ask)
      → build_sl_tp(signal, candles, base_rr, rr3_patterns, sl_buffer, min_rr)
        → SLTP_RULES[pattern_name](signal, candle, LevelConfig)
```

### Single-Timeframe Assumptions Found

| Assumption | Location | Evidence |
|-----------|----------|----------|
| SL is always from M5 candle geometry | `risk/levels.py` `_buy_low_buffer()` | `sl = candle.low - cfg.sl_buffer` — candle is M5 |
| TP is always `entry ± risk × fixed_RR` | `risk/levels.py` | `return sl, entry + risk * rr` |
| RR is fixed per pattern (2.0 or 3.0) | `risk/levels.py` `_compute_rr()` | `pattern_rr = 3.0 if pattern in cfg.rr3_patterns else cfg.base_rr` |
| SL_BUFFER is a single constant (0.0002) | `core/config.py` | `SL_BUFFER = 0.0002` — not symbol-aware or horizon-aware |
| LevelConfig is constructed with global constants | `risk/levels.py` `build_sl_tp()` | `cfg = LevelConfig(base_rr=base_rr, ...)` |
| `candle` is always `candles[signal.bar_index]` (M5 bar) | `risk/levels.py` | No option to use a different timeframe's candle |

### Can Different Horizons Produce Different Stops?

**NOT CURRENTLY.** The `build_sl_tp()` function takes a `Signal` with a `bar_index` pointing into M5 candles. There is no mechanism to:
- Use H1 swing high/low for EXTENDED SL
- Use M15 candle range for INTRADAY SL
- Scale SL by ATR for different holding periods

### Insertion Point For Multi-Horizon SL/TP

```python
# In risk/levels.py — add a new builder:
def build_sl_tp_for_horizon(
    signal: Signal,
    candles: list[Candle],  # M5 candles (current)
    htf_context: Any,       # H4/H1/M15 structure (NEW)
    horizon: str,           # "SCALP" | "INTRADAY" | "EXTENDED" (NEW)
    *,
    base_rr: float,
    sl_buffer: float,
    min_rr: float,
) -> tuple[float, float] | None:
    if horizon == "SCALP":
        return build_sl_tp(signal, candles, ...)  # Existing behaviour
    elif horizon == "INTRADAY":
        # SL from M15 structure or ATR-based
        ...
    elif horizon == "EXTENDED":
        # SL from H1 swing structure
        ...
```

### Can Different Horizons Have Different Targets?

**NOT CURRENTLY.** TP is always `entry ± risk × RR` where RR is 2.0 or 3.0. To support horizons:
- SCALP: TP = entry ± risk × 2.0 (current)
- INTRADAY: TP = entry ± risk × 3.0-4.0 (wider stop → further target)
- EXTENDED: TP = H1 swing target level or entry ± risk × 4.0-5.0

This requires `LevelConfig` to accept horizon-specific RR values, or a new `HorizonLevelConfig` dataclass.

---

## 2. Holding Time Assumptions

### Current Architecture

```
TradeStateManager._process_one_position(pos, bid, ask, ts):
  1. Time exit: if max_time_in_trade_seconds > 0 and age >= max → close
  2. Break-even: if profit >= trigger_rr × R → move SL to entry
  3. Trailing: if trail_step > 0 and profit >= start_rr × R → trail
  4. Exit trigger: if price crosses SL or TP → close
```

### Single-Behaviour Assumptions Found

| Assumption | Location | Evidence |
|-----------|----------|----------|
| ONE TradeManagementConfig for ALL trades | `core/runtime/runtime_utils.py` line 37 | `_build_trade_management_config()` called once, shared globally |
| Break-even trigger is global (1.0R) | `core/config.py` | `TM_BREAK_EVEN_TRIGGER_RR = 1.0` |
| No trailing (step=0) | `core/config.py` | `TM_TRAILING_STEP = 0.0` |
| No max time (disabled) | `core/config.py` | `TM_MAX_TIME_IN_TRADE_SECONDS = 0.0` |
| No partial TP | `core/config.py` | `TM_PARTIAL_TP_FRACTION = 0.0` |

### Does The System Assume All Trades Are Scalps?

**YES, implicitly.** The break-even at 1.0R is scalp behaviour — it locks in quickly and prevents larger moves. With no trailing and no max time, the system either:
- Hits TP quickly (scalp win)
- Gets stopped at break-even (scalp scratch)
- Gets stopped at initial SL (scalp loss)

### Are Extended Trades Currently Impossible?

**NOT impossible, but structurally disadvantaged.** The break-even trigger at 1.0R moves SL to entry after any +1R move. If an EXTENDED trade needs to breathe through a pullback:
- Price moves +1R → BE triggers → price pulls back to entry → stopped at BE → trade loses potential +3R move

### Insertion Point For Multi-Horizon Management

The `TradeManagementConfig` is already parameterized correctly. What's needed:

```python
# In runtime_utils.py — build per-horizon configs:
_HORIZON_CONFIGS = {
    "SCALP": TradeManagementConfig(
        break_even_trigger_rr=1.0,
        break_even_buffer=0.1,
        trailing_step=0.0,
        max_time_in_trade_seconds=2700.0,  # 45 min
    ),
    "INTRADAY": TradeManagementConfig(
        break_even_trigger_rr=1.5,
        break_even_buffer=0.1,
        trailing_step=0.0003,  # ATR-derived
        trailing_start_rr=2.0,
        max_time_in_trade_seconds=28800.0,  # 8 hours
    ),
    "EXTENDED": TradeManagementConfig(
        break_even_trigger_rr=2.0,
        break_even_buffer=0.1,
        trailing_step=0.0005,
        trailing_start_rr=3.0,
        max_time_in_trade_seconds=259200.0,  # 3 days
    ),
}
```

The `Position` dataclass needs a `trade_horizon` field so `_process_one_position` can select the correct config.

---

## 3. Risk Manager Assumptions

### Current Architecture

```python
# core/runtime/runtime_utils.py
RiskManager(
    fixed_lot=config.FIXED_LOT,      # 0.01
    base_rr=config.BASE_RR,          # 2.0
    sl_buffer=config.SL_BUFFER,      # 0.0002
    min_rr=config.MIN_RR,            # 2.0
    rr3_patterns=config.RR3_PATTERNS,
)
```

### Single-Horizon Assumptions Found

| Assumption | Location | Evidence |
|-----------|----------|----------|
| Fixed lot for all trades (0.01) | `core/config.py` | `FIXED_LOT = 0.01` |
| Same RR regardless of opportunity quality | `risk/levels.py` | `base_rr` is a constant |
| POSITION_SIZING_MODE = "FIXED" | `core/config.py` | Dynamic mode exists but is disabled |
| Risk per trade constant (0.25%) | `core/config.py` | `RISK_PER_TRADE_PERCENT = 0.25` |

### Can Wider Stops Automatically Reduce Lot Size?

**YES — the infrastructure exists.** `POSITION_SIZING_MODE = "DYNAMIC"` + `volume_for_risk()` already computes: volume = (account_risk × equity) / (stop_distance × tick_value). This naturally reduces lot size for wider stops.

Currently disabled (`POSITION_SIZING_MODE = "FIXED"`), but activating it would make horizon-aware sizing work automatically:
- SCALP: 5 pip stop → 0.01 lot
- INTRADAY: 15 pip stop → 0.0033 lot
- EXTENDED: 30 pip stop → 0.0017 lot

All at the same dollar risk.

### Does Risk Remain Constant Across Horizons?

**It should.** Risk per trade (in account currency) should be identical regardless of horizon. Only the expression changes: wider stop = smaller lot = same $ at risk. The `volume_for_risk()` function handles this.

---

## 4. Execution Engine Assumptions

### Current Architecture

```
OrderIntent (frozen) → execution_orchestrator.execute_trade() → MT5 order_send
  │
  └── Fields: symbol, side, volume, entry_reference, sl, tp, pattern, metadata{}
```

### Single-Horizon Assumptions Found

| Assumption | Location | Evidence |
|-----------|----------|----------|
| OrderIntent has no horizon field | `risk/models.py` | Not in the frozen dataclass |
| OrderIntent.metadata is a dict (extensible) | `risk/models.py` | `metadata: dict[str, Any] = field(default_factory=dict)` |
| Position has no horizon field | `core/trade_management/position.py` | No `trade_horizon` attribute |
| TradeStateManager uses global config | `core/runtime/runtime_utils.py` | One `_build_trade_management_config()` for all |

### Can Execution Receive Horizon Context?

**YES — immediately.** `OrderIntent.metadata` is already a dict. Adding `metadata={"horizon": "INTRADAY"}` requires zero schema changes.

### Can Position Management Behave Differently Per Horizon?

**NOT CURRENTLY** — but the fix is straightforward:

1. Add `trade_horizon: str = "SCALP"` to `Position` dataclass
2. In `_process_one_position()`, look up management parameters from a horizon-keyed config dict instead of the single global config
3. The `TradeManagementConfig` dataclass already has all the right fields (BE, trail, time, partial)

---

## 5. Summary: Where The System Assumes "One Opportunity = One Behaviour"

| Layer | Assumption | Difficulty to Change |
|-------|-----------|---------------------|
| SL Generation | M5 candle geometry only | MEDIUM — needs HTF structure price levels |
| TP Generation | Fixed RR multiplier | LOW — parameterize per horizon |
| RR Assignment | 2.0 or 3.0 (pattern-based) | LOW — add horizon factor |
| Position Sizing | Fixed lot (0.01) | LOW — dynamic mode exists (just enable) |
| Break-Even Trigger | Global 1.0R | LOW — per-Position config lookup |
| Trailing Stop | Disabled globally | LOW — enable per horizon |
| Max Hold Time | Disabled globally | LOW — set per horizon |
| Trade Management Config | Single global instance | MEDIUM — need per-trade config selection |
| Position Object | No horizon field | LOW — add field |
| OrderIntent | No horizon in frozen fields | LOW — use existing metadata dict |
| RiskManager | No horizon awareness | MEDIUM — needs to branch on horizon |

---

## 6. Recommended Implementation Sequence

### Phase 4B: Horizon Classification (observation only)

1. Create `core/horizon/horizon_classifier.py` — classifies plausible horizons
2. Create `core/horizon/horizon_profiles.py` — defines behaviour per horizon
3. Add `trade_horizon` field to `OpportunityAssessment`
4. Add `evaluated_horizons` to Opportunity persistence
5. Classify every opportunity but DO NOT change execution

### Phase 4C: Shadow Horizon Evaluation

1. Create `core/horizon/horizon_evaluator.py` — computes EV per horizon
2. For each opportunity, compute SCALP + INTRADAY + EXTENDED evaluations
3. Persist all evaluations (shadow dataset)
4. Config: `PERMITTED_HORIZONS = ["SCALP"]` — only scalp executes
5. Research: accumulate data showing where edge exists per horizon

### Phase 4D: Horizon-Aware Execution (structural change)

1. Branch `build_sl_tp()` by horizon (use HTF structure for wider stops)
2. Enable `POSITION_SIZING_MODE = "DYNAMIC"` (auto-scales lot for wider stops)
3. Add `trade_horizon` to `Position` dataclass
4. Branch `_process_one_position()` by position.trade_horizon
5. Config: `PERMITTED_HORIZONS = ["SCALP", "INTRADAY"]` — activate INTRADAY

### Phase 4E: Trade Management Per Horizon

1. Create per-horizon `TradeManagementConfig` instances
2. SCALP: BE at 1R, no trail, max 45 min
3. INTRADAY: BE at 1.5R, trail at 2R, max 8 hours
4. EXTENDED: BE at 2R, trail at 3R, max 3 days

---

## 7. Missing Infrastructure (Must Be Built Before Phase 4C)

| Component | Why Needed | Current State |
|-----------|-----------|---------------|
| H1 swing price levels | INTRADAY/EXTENDED SL needs specific price (not just direction) | H1 BiasSnapshot has `direction` + `bos_confirmed` but NOT swing high/low prices |
| M15 ATR value | INTRADAY SL sizing needs volatility reference | Available via `volatility_filter` on engine_state (may need extraction) |
| H4 ATR value | EXTENDED target sizing | H4 RegimeSnapshot has `atr_ratio` but not raw ATR value |
| Per-trade config selection | Management must branch per position | TradeManagementConfig is global — needs horizon-keyed lookup |

---

## 8. Design Validation Against Core Principle

**Core principle:** "The same opportunity can have multiple possible expressions. Discover which expression contains edge."

| Requirement | Satisfiable? | How |
|-------------|-------------|-----|
| Evaluate all horizons for every opportunity | ✅ | horizon_evaluator computes SCALP + INTRADAY + EXTENDED |
| Persist all evaluations (including non-executable) | ✅ | Shadow dataset captures all horizon assessments |
| Execute only permitted horizons | ✅ | Config `PERMITTED_HORIZONS` gates execution |
| Discover edge per horizon via research | ✅ | Lifecycle join: horizon_evaluation → trade_truth (for executed) + market_movement (for shadow) |
| Smoothly activate new horizons | ✅ | Add to PERMITTED_HORIZONS list when data proves edge |
| Risk constant across horizons (wider stop = smaller lot) | ✅ | `volume_for_risk()` handles automatically in DYNAMIC mode |

---

## 9. Final Assessment

The current system is **structurally capable of multi-horizon support** with moderate changes:
- 3 LOW-difficulty changes (add fields, parameterize RR, enable dynamic sizing)
- 3 MEDIUM-difficulty changes (HTF price levels, per-trade config, risk manager branching)
- 0 HIGH-difficulty or architectural redesign required

The biggest gap is **H1 swing price levels** — the H1 bias snapshot knows direction ("BULLISH") but not WHERE the last swing high/low occurred. This price data is needed for structure-based stops.

Everything else is either already built (dynamic sizing, trailing, time exit) or requires adding a field + branching on it.
