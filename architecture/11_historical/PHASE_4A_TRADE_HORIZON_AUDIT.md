# PHASE 2: TRADE HORIZON ARCHITECTURE AUDIT

**Date:** 2026-07-23
**Objective:** Understand how the system currently decides trade duration, target size, and expected movement. Identify the correct implementation path for multi-horizon support.
**Scope:** Architecture audit only. No code changes.

---

## 1. Current Horizon Behaviour

### Summary

The system currently operates as a **single-horizon M5 scalper** regardless of what the higher timeframes indicate. Every trade has:

- SL derived from M5 candle geometry (typically 3-10 pips)
- TP fixed at entry ± (SL_distance × 2.0)
- No holding duration limit
- Break-even trigger at 1R (moves SL to entry)
- No trailing stop
- No partial take-profit

### Why Trades Are Short Duration

The M5 candle high/low (used for SL) is typically very small (3-10 pips). With a 2:1 RR, the TP target is only 6-20 pips from entry. At M5 cadence:

- Price can reach TP within 1-5 bars (5-25 minutes) on favourable moves
- Break-even triggers at 1R (3-10 pips profit), then any retrace stops the trade at entry
- The effective holding pattern is: rapid TP hit OR break-even stop → very short trades

### Evidence from Checkpoint 001

22 live trades showed:
- Median duration: ~20 minutes
- All exits: `broker_close` (no normal TP hit recorded)
- This is consistent with M5 geometry producing scalp-size targets

---

## 2. Current SL/TP Authority

### SL Calculation Chain

```
run_new_engine()
  → risk_manager.evaluate(assessment, candles, bid, ask)
    → RiskManager._execute_risk(symbol, signal, candles, bid, ask)
      → build_sl_tp(signal, candles, base_rr, rr3_patterns, sl_buffer, min_rr)
        → SLTP_RULES[pattern_name](signal, candle, LevelConfig)
          → For BUY: sl = candle.low - SL_BUFFER
          → For SELL: sl = candle.high + SL_BUFFER
```

**File:** `risk/levels.py`

**Key insight:** SL is placed at the **M5 trigger candle's extreme** (high for SELL, low for BUY) plus a fixed 2-pip buffer (`SL_BUFFER = 0.0002`). This is purely geometric — no volatility, no ATR, no timeframe awareness.

### TP Calculation Chain

```
_buy_low_buffer(signal, candle, cfg):
  entry = candle.close
  sl = candle.low - cfg.sl_buffer
  rr = _compute_rr(signal.pattern, cfg)  → 2.0 or 3.0
  risk = entry - sl
  tp = entry + risk * rr
```

**Key insight:** TP is a fixed multiple of risk distance. The system has no concept of:
- Swing structure targets (next H1 swing high/low)
- Key level targets (support/resistance)
- ATR-based targets (1.5× daily ATR)
- HTF structure-based targets

### Where The Authority Lives

| Authority | File | Function |
|-----------|------|----------|
| SL distance | `risk/levels.py` | `_buy_low_buffer()`, `_sell_high_buffer()`, etc. |
| TP distance | `risk/levels.py` | Same functions (TP = entry ± risk × RR) |
| RR multiplier | `risk/levels.py` | `_compute_rr()` |
| Buffer size | `core/config.py` | `SL_BUFFER = 0.0002` |
| Min SL guard | `risk/manager.py` | `_compute_adaptive_min_sl()` (rejects too-tight stops) |
| Intent construction | `risk/manager.py` | `_execute_risk()` → `OrderIntent(sl=sl, tp=tp)` |

---

## 3. Current RR Logic

### Fixed RR Assignment

```python
# risk/levels.py
def _compute_rr(pattern: str, cfg: LevelConfig) -> float:
    pattern_rr = 3.0 if pattern in cfg.rr3_patterns else cfg.base_rr
    return max(float(cfg.min_rr), float(pattern_rr))
```

| Config Parameter | Value | Effect |
|-----------------|-------|--------|
| `BASE_RR` | 2.0 | Default RR for most patterns |
| `MIN_RR` | 2.0 | Minimum acceptable RR (enforced floor) |
| `RR3_PATTERNS` | THREE_WHITE_SOLDIERS, THREE_BLACK_CROWS | Get 3.0 RR |

### RR Is Never Adaptive

The RR does NOT change based on:
- Market regime (trending vs range)
- HTF alignment strength
- Volatility conditions
- Strategy classification (continuation vs reversal)
- Pattern quality score

### Execution Policy RR (Gating Only)

`execution_policy.py` computes a `required_rr` per market state:
- STRUCTURED: 1.5
- TRANSITIONAL: 1.8-2.5
- CHOP: 999.0 (blocks)

**But this only GATES** — it does not modify the OrderIntent's SL/TP. If the computed RR from candle geometry meets the threshold, the trade passes. The actual target size remains fixed.

---

## 4. Current Holding Time Controls

### TradeManagementConfig (current state)

```python
break_even_trigger_rr: 1.0    # ACTIVE — moves SL to entry at 1R profit
break_even_buffer: 0.1        # ACTIVE — 0.1 price units past entry
trailing_step: 0.0            # DISABLED
trailing_start_rr: 0.0        # DISABLED
partial_tp_fraction: 0.0      # DISABLED
partial_tp_path_fraction: 0.0 # DISABLED
max_time_in_trade_seconds: 0.0  # DISABLED (no time limit)
```

### Exit Sequence (per tick)

```
_process_one_position(pos, bid, ask, ts):
  1. Update unrealised PnL
  2. Update MFE extreme
  3. Check time exit → TM_MAX_TIME (disabled: 0)
  4. Maybe break-even SL → triggers at 1.0R, moves SL to entry + 0.1
  5. Maybe trailing SL → (disabled: step=0)
  6. check_exit_trigger(sl, tp) → if bid/ask crosses SL or TP → close
```

### Consequence

With only break-even active:
- Trade runs until TP hit OR price returns to entry (BE stop)
- No mechanism to hold through pullbacks after BE triggers
- No mechanism to extend targets if momentum continues
- No trailing to capture extended moves

### Missing: Horizon-Aware Management

The `TradeManagementConfig` is **per-strategy-session** (single config for all trades). There is no per-trade or per-horizon config. An EXTENDED horizon trade would need different BE trigger, trailing parameters, and potentially different TP management than a SHORT horizon trade.

---

## 5. Existing Multi-Timeframe Inputs

### What HTF Context Provides

| Timeframe | Snapshot | Used For |
|-----------|----------|----------|
| H4 | `RegimeSnapshot` (classification, confidence) | Strategy activation regime, h4_alignment score |
| H1 | `BiasSnapshot` (direction, bos_confirmed, swing_structure) | Swing permission gate, htf_alignment score, trend source |
| M15 | `StructureSnapshot` (quality metrics) | market_quality score component |

### How HTF Feeds The Pipeline

```
HTFContext (H4 + H1 + M15)
  │
  ├─→ _compute_all_scores() → 10 component scores → weighted score
  │     ├── htf_alignment: H1 bias + M15 structure combined
  │     ├── h4_alignment: H4 regime directional alignment
  │     ├── trend_alignment: H1 phase direction
  │     └── market_quality: M15 structure quality
  │
  ├─→ H4 regime → strategy_activation (CONTINUATION/REVERSAL/FALSE_BREAK)
  │
  └─→ H1 BOS → swing permission gate (blocks opposing-structure trades)
```

### What HTF Does NOT Influence

- SL price level (always M5 candle geometry)
- TP price level (always entry ± risk × fixed RR)
- RR multiplier (always 2.0 or 3.0)
- Holding duration (no time controls connected to HTF)
- Trade management parameters (static global config)
- Position sizing beyond the execution_policy max_position_fraction

### Available But Unused HTF Data

The H1 bias snapshot contains `swing_structure` (HH_HL, LH_LL, MIXED) which could identify:
- Next swing high/low level (potential structure-based TP target)
- Swing amplitude (how far price typically moves in current structure)

The H4 regime could inform:
- Whether extended moves are probable (TRENDING → larger targets)
- Whether mean-reversion is expected (RANGING → shorter targets)

---

## 6. Missing Components

### For SHORT Horizon (current behaviour, formalized)

| Component | Status | Notes |
|-----------|--------|-------|
| M5 SL geometry | EXISTS | Pattern candle high/low ± buffer |
| Fixed RR TP | EXISTS | entry ± risk × 2.0 |
| Break-even management | EXISTS | Trigger at 1R |
| Horizon classification | MISSING | No explicit "this is a short trade" label |
| Per-trade management config | MISSING | All trades share one config |

### For EXTENDED Horizon (new capability)

| Component | Status | Notes |
|-----------|--------|-------|
| Wider SL (H1/M15 structure) | MISSING | Need SL from higher-timeframe structure |
| Structure-based TP | MISSING | Need TP from H1 swing targets or ATR multiples |
| Adaptive RR | MISSING | RR should vary by horizon (e.g., 3-5R for extended) |
| Trailing stop | EXISTS (disabled) | `trailing_step` + `trailing_start_rr` ready but = 0.0 |
| Partial TP | EXISTS (disabled) | `partial_tp_fraction` ready but = 0.0 |
| Max hold time | EXISTS (disabled) | `max_time_in_trade_seconds` ready but = 0.0 |
| Horizon metadata | MISSING | No field on OpportunityAssessment or OrderIntent |
| Horizon-conditional management | MISSING | TradeManagementConfig is global, not per-trade |

---

## 7. Recommended Architecture

### Design Principle

One engine, two horizon profiles. The `OpportunityAssessment` determines which horizon applies based on HTF context. The `RiskManager` uses horizon to select SL/TP rules. `TradeManagement` uses horizon to select exit parameters.

### Data Flow

```
run_new_engine()
  │
  ├── Pattern detected (M5)
  ├── Strategy classified (CONTINUATION/REVERSAL/FALSE_BREAK)
  ├── HTF context evaluated (H4 regime, H1 structure, M15 quality)
  │
  ├── NEW: Horizon Selection
  │     Input: strategy_type, regime, htf_alignment, h1_swing_structure
  │     Output: TradeHorizon = SHORT | EXTENDED
  │     Logic:
  │       - TRENDING regime + CONTINUATION + strong H1 alignment → EXTENDED
  │       - RANGE regime + REVERSAL → SHORT
  │       - TRANSITIONAL + high uncertainty → SHORT (conservative)
  │       - Strong H4+H1+M15 alignment → EXTENDED candidate
  │
  ├── OpportunityAssessment (with trade_horizon field)
  │
  ├── RiskManager.evaluate()
  │     NEW: reads assessment.trade_horizon
  │     SHORT: use existing M5 SL (candle geometry), fixed RR (2.0)
  │     EXTENDED: use H1/M15 structure SL (wider), adaptive RR (3.0-5.0)
  │
  └── OrderIntent (sl, tp now horizon-appropriate)
        │
        └── TradeStateManager.register_from_execution()
              NEW: Position carries trade_horizon tag
              SHORT config: BE at 1R, no trail, no partial
              EXTENDED config: BE at 1.5R, trail at 2R, partial at 1R
```

### Horizon Selection Logic (recommended placement)

**Where:** Between `OpportunityAssessment` construction and `risk_manager.evaluate()` call in `run_new_engine()`.

**Why here:** All HTF data is available. Strategy classification is complete. The horizon becomes metadata on the assessment, feeding downstream.

### SL/TP Rules for EXTENDED Horizon

**SL source options (choose one per implementation):**

1. **H1 swing structure** — SL at recent H1 swing low (BUY) or swing high (SELL)
2. **M15 candle range** — SL at M15 trigger candle extreme (wider than M5)
3. **ATR-based** — SL at entry ± (N × M15_ATR) where N = 1.0-1.5
4. **Key level** — SL below/above nearest structure level from H1 analysis

**TP source options:**

1. **H1 swing target** — TP at next H1 swing high (BUY) or low (SELL)
2. **ATR multiple** — TP at entry ± (N × H4_ATR) where N = 1.5-2.0
3. **Structure level** — TP at nearest significant S/R from H1/H4
4. **Adaptive RR** — TP = entry ± (extended_risk × 3.0-5.0)

### Per-Trade Management Config

**Current problem:** `TradeManagementConfig` is one global instance. All positions get the same BE/trail/time parameters.

**Solution:** Add `trade_horizon` field to `Position` dataclass. In `_process_one_position()`, select management parameters based on position's horizon:

```python
# Concept (not code change):
if pos.trade_horizon == "EXTENDED":
    be_trigger = 1.5  # Later BE trigger
    trail_step = calculated_from_atr
    trail_start = 2.0  # Trail after 2R profit
else:  # SHORT
    be_trigger = 1.0   # Current behaviour
    trail_step = 0.0   # No trail
    trail_start = 0.0
```

---

## 8. Implementation Plan

### Phase 2A: Horizon Classification (metadata only, no behaviour change)

**Goal:** Add `trade_horizon` field to the pipeline. Classify every trade. Log it. Do not change SL/TP/RR.

1. Add `trade_horizon: str` field to `OpportunityAssessment` (default "SHORT")
2. Create `core/pipeline/horizon_classifier.py` — pure function that returns "SHORT" or "EXTENDED" based on regime, strategy, HTF alignment
3. Populate horizon in `run_new_engine()` after OpportunityAssessment construction
4. Add `trade_horizon` to `OrderIntent.metadata` (already has a dict field)
5. Propagate to `Position` via `trade_identity` or new field
6. Log horizon in decision_audit and decision_ledger
7. Add to protection_audit and risk_deviation for forensic analysis

**Risk:** Zero. Observational only. No behaviour change.

### Phase 2B: EXTENDED SL/TP Rules (new geometry, same engine)

**Goal:** When horizon = EXTENDED, use wider SL and larger TP from HTF data.

1. Add `build_sl_tp_extended()` in `risk/levels.py` — takes HTF context, returns (sl, tp) from H1 structure
2. In `RiskManager._execute_risk()`, check `assessment.trade_horizon`:
   - SHORT → existing `build_sl_tp()` (unchanged)
   - EXTENDED → `build_sl_tp_extended()` with HTF data
3. Add HTF context parameter to `RiskManager.evaluate()` (or pass via assessment)
4. EXTENDED RR: 3.0-5.0 (configurable)
5. EXTENDED SL source: H1 swing extreme or M15 ATR-based

**Risk:** Moderate. New geometry needs validation via shadow trading before live.

### Phase 2C: Horizon-Aware Position Management

**Goal:** EXTENDED trades get different BE/trail/time parameters.

1. Add `trade_horizon` field to `Position` dataclass
2. In `_process_one_position()`, branch management logic by horizon:
   - SHORT: BE at 1R, no trail (current)
   - EXTENDED: BE at 1.5R, trail at `M15_ATR × 0.5`, partial TP at 1R
3. Make `TradeManagementConfig` horizon-aware (dual config or dynamic lookup)
4. Enable trailing for EXTENDED only (existing mechanism, just set step > 0)

**Risk:** Low-moderate. Trailing logic already exists and is tested; just needs activation per-horizon.

### Dependency Order

```
Phase 2A (classification + logging) → no behaviour change
  ↓
Phase 2B (SL/TP geometry) → new risk rules, shadow-validate first
  ↓
Phase 2C (management params) → enable trailing/partial for EXTENDED
```

Each phase is independently deployable. Phase 2A can run in production immediately for data collection.

---

## 9. Key Files For Implementation

| Purpose | File | Change Type |
|---------|------|-------------|
| Horizon classification | `core/pipeline/horizon_classifier.py` | NEW |
| Assessment metadata | `core/models/opportunity_assessment.py` | ADD field |
| Engine integration | `core/pipeline/new_engine.py` | CALL classifier |
| Extended SL/TP rules | `risk/levels.py` | ADD new builders |
| Risk manager branching | `risk/manager.py` | BRANCH on horizon |
| Order intent metadata | `risk/models.py` | Already has metadata dict |
| Position horizon tag | `core/trade_management/position.py` | ADD field |
| Management branching | `core/trade_management/manager.py` | BRANCH on horizon |
| Config extension | `core/trade_management/config.py` | ADD extended params |
| HTF context to risk | `core/timeframes/cache.py` | Already provides data |

---

## 10. Constraints

### Do NOT Change

- Pattern detection logic
- Scoring weights or threshold
- Strategy classification rules
- EV/probability model
- H1 BOS permission gate
- Existing SHORT horizon behaviour (must remain default)

### Must Preserve

- All existing tests pass without modification
- SHORT horizon produces identical SL/TP to current system
- Backwards compatibility of all persistence schemas
- Fire-and-forget safety (horizon failure must not block trading)

### Open Questions (resolve before implementation)

1. What H1 data is needed for EXTENDED SL? Current `BiasSnapshot` has direction + BOS but not explicit swing high/low price levels.
2. Should EXTENDED be shadow-only initially (like the EV gate experiment)?
3. What minimum HTF alignment score should qualify for EXTENDED?
4. Should EXTENDED trades have a max hold time (e.g., 4 hours) as a safety net?
5. How does position sizing change for EXTENDED (wider stop = smaller lot for same dollar risk)?
