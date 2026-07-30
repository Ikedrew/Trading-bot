# V3 Research Status Audit — Full Pipeline Review

**Date:** 2026-07-28
**Readiness:** READY FOR PRELIMINARY ANALYSIS

---

## Executive Summary

The V3 research pipeline is operational and producing meaningful data. After re-linkage, the analysable dataset has grown to **158 post-Phase-2 records with outcomes** — sufficient for individual feature testing but below thresholds for combination testing and rare event analysis.

**Key finding:** Price inside Order Block remains the only statistically positive signal (+0.071R, CI [+0.001, +0.141]). A consistent location gradient exists (discount > mid > premium). But no feature overcomes the 0.48R spread cost alone.

---

## 1. Data Collection Status

| Metric | Current | Previous Audit | Change |
|---|---|---|---|
| V3 total observations | **315** | 187 | +128 (+68%) |
| V3 linked outcomes | **266** | 122 | +144 (+118%) |
| V3 post-Phase-2 total | **199** | 168 | +31 |
| V3 post-P2 linked | **158** | 25 | +133 (+532%) |
| V2 observations | 332 | ~97 | +235 |
| Shadow trades | 3,152 | 2,812 | +340 |
| Executed (live) trades | 0 | 0 | — |
| Symbols | 7 | 7 | Same |

### Lineage Completeness

| Stage | Count | % |
|---|---|---|
| V3 observation created | 315 | 100% |
| Has correlation_id | 315 | 100% |
| Linked to shadow trade | 266 | 84% |
| Has complete outcome | 266 | 84% |
| Post-P2 with full features + outcome | 158 | 50% |
| Unresolved (no match) | 49 | 16% |

### Data Quality

- **New records collecting correctly:** YES (315 total, growing)
- **Statistical confidence improving:** YES (158 linked post-P2, up from 25)
- **Composition contamination:** RESOLVED (Pass 2 filters to post-P2 only)
- **Session bias:** PRESENT — 100% "OFF" session in post-P2 linked set. No LONDON/NY/ASIA data yet.

---

## 2. Research Engine Health

| Component | Status |
|---|---|
| V3 Observer (#9) | ✅ Active, collecting every cycle |
| Liquidity Detector | ✅ Firing (87-94% population) |
| FVG Detector | ✅ Firing (72-82% population) |
| Order Block Detector | ✅ Firing (77-78% population) |
| V3 Outcome Linker | ✅ Working (92.4% match rate) |
| V2 Discovery Engine (CQ1-CQ4) | ✅ Available for V3 fields |
| Report Generation | ✅ JSON + Markdown |

### Research Runner Status

| Runner | Status | Last Run |
|---|---|---|
| `run_v3_linkage.py` | ✅ Complete | Today (linked 144 new) |
| `run_v3_discovery_pass2.py` | ✅ Complete | Today (n=158) |
| `run_v3_discovery_pass.py` | ✅ Complete (Pass 1) | Earlier today |
| V2 Discovery Engine | ✅ Available | Used on V2 data |

**All experiments executing. Reports generating. Results reproducible.**

---

## 3. V3 Market Context Research

### Market Location

| Feature | Population | Predictive? | Evidence |
|---|---|---|---|
| M15 range_position | 93% | **YELLOW** | Discount WR=62.7% vs Premium WR=38.9% |
| H1 range_position | 67% | YELLOW | Discount +0.08R vs Premium -0.05R (small n) |
| Swing highs/lows | 29-34% | Enabled | Provides distances for location |
| Nearest support/resistance | 100% | Tested | Near support (-0.04R) vs mid (-0.11R) |

### Liquidity

| Feature | Population | Predictive? | Evidence |
|---|---|---|---|
| Equal highs | 87% | YELLOW | Present -0.05R vs Absent -0.12R (+0.07R effect) |
| Equal lows | 94% | YELLOW | Present -0.04R vs Absent -0.22R (+0.18R effect) |
| Session extremes | 100% | Neutral | High swept -0.03R vs not swept -0.09R |
| Liquidity sweep | 7% | GRAY | n=11, shows -0.17R (insufficient) |

### Fair Value Gaps

| Feature | Population | Predictive? | Evidence |
|---|---|---|---|
| FVG above price | 72% | Neutral | -0.07R |
| FVG below price | 82% | YELLOW | -0.02R (least negative) |
| Price inside FVG | 22% | YELLOW | WR=64.7%, EV=-0.01R |
| No FVG control | 0% | — | Cannot test (FVG always present) |

### Order Blocks

| Feature | Population | Predictive? | Evidence |
|---|---|---|---|
| Demand OB | 77% | YELLOW | -0.03R vs -0.13R absent (+0.10R) |
| Supply OB | 78% | YELLOW | -0.02R vs -0.17R absent (+0.14R) |
| **Price inside OB** | **15%** | **GREEN** | **+0.071R, WR=65.2%, CI > 0** |

### Displacement/Rejection

| Feature | Population | Predictive? | Evidence |
|---|---|---|---|
| Rejection candle | 21% | NEUTRAL | -0.09R (slightly worse than baseline) |
| Displacement | 1% | GRAY | n=2 (cannot test) |

---

## 4. Pattern Discovery Audit

### By Direction (n=158)

| Direction | n | WR | EV |
|---|---|---|---|
| BUY | 76 | — | — |
| SELL | 82 | — | — |

### By Exit Reason

| Exit | n | % | Interpretation |
|---|---|---|---|
| max_bars_timeout | 151 | 95.6% | Majority of trades expire |
| stop_loss | 7 | 4.4% | Very few SL hits |
| take_profit | 0 | 0% | No TP hits in post-P2 set |

**Critical observation:** 95.6% of outcomes are timeouts. This means the risk:reward structure is not reaching targets. Trades drift sideways and expire — they don't decisively move to TP or SL. This is consistent with a non-directional signal in a ranging market.

### Pattern Classification

| Pattern | Status | Evidence |
|---|---|---|
| M5 candlestick patterns (all types) | **REJECTED** (V2 research) | CE1: -0.70R after costs, n=867 |
| Pattern + context combination | **REJECTED** (V2 research) | CQ2: 0/8 validate OOS |
| Pattern at institutional zone | **YELLOW** (V3 finding) | Inside OB: +0.07R (n=23) |

---

## 5. Regime Analysis

### Current Distribution (post-P2 period)

The regime data from shadow trades shows:
- RANGE: ~92% of the CURRENT epoch
- TRANSITIONAL: ~7%
- TRENDING: <1%

### Does Regime Separate Outcomes?

From V2 research (n=437): **NO.** H4 regime showed zero predictive separation. All regimes produced negative cost-adjusted EV.

From V3 research: Cannot meaningfully test (92% RANGE = no variation to compare against).

**Verdict:** Regime classification is NOT currently informative because the market has been almost exclusively in RANGE for the entire observation period.

---

## 6. Hypothesis Testing

### Core Hypothesis: Market Behaviour + Context + Risk + Execution = Positive EV

| Component | Evidence Found | Evidence Missing | Confidence |
|---|---|---|---|
| **Market Behaviour (regime)** | Regime classification works | No trend data to validate | LOW — 92% RANGE |
| **Context (location/liquidity)** | Inside OB = +0.07R; discount > premium gradient | Effect too small for spread cost | MEDIUM — real signal exists |
| **Risk Model** | Spread = 48% of risk; 95% timeout exits | No TP hits — RR structure broken | LOW — risk geometry inadequate |
| **Execution Policy** | Shadow trades execute correctly | No live validation | N/A — never tested live |

### Per-Component Assessment

**Market Behaviour:** Cannot validate — insufficient regime diversity.

**Context:** Location signal exists (+0.07R for OB, +0.18R for equal lows presence). But signal < cost. The V3 hypothesis is **partially supported** — location DOES separate outcomes but the magnitude is insufficient for current execution parameters.

**Risk Model:** BROKEN. 95.6% timeout rate means the RR structure (TP/SL distances) is inappropriate for this market. Trades don't reach targets. This is the most critical finding.

**Execution Policy:** Working mechanically but economically non-viable.

---

## 7. EV Model Validation

### Current State

| Metric | Value | Interpretation |
|---|---|---|
| Baseline EV (raw) | -0.056R | Slightly negative |
| Baseline WR | 52.5% | Near coin-flip |
| Best feature EV | +0.071R (inside OB) | Only positive raw signal |
| Spread cost | 0.48R | Overwhelms all signals |
| Cost-adjusted best | -0.41R | Still negative |
| TP hit rate | 0% | Targets never reached |
| SL hit rate | 4.4% | Rarely stopped out |
| Timeout rate | 95.6% | Almost always expires |

### Calibration Assessment

- **Overconfidence:** N/A — no probability model deployed
- **False positives:** The system generates many opportunities (315 observations) but none reach TP
- **False negatives:** Unknown — NO_TRADE observations not tracked for missed moves
- **EV gate:** No EV gate exists yet — all patterns trade if score passes

### Critical Risk/Reward Problem

The 95.6% timeout rate reveals that the TP/SL structure is mismatched to market behaviour:
- TP is set too far (never reached)
- SL is too tight (4.4% hit) OR just right
- Market moves sideways within the range, not to targets

---

## 8. Decision Funnel Analysis

```
Market Data                           315 observations captured
    ↓
Candidate Detection (V3 observer)     315 (100% — observes every cycle)
    ↓
Pattern Recognition                   ~1,500 shadow trades created
    ↓
Context Validation                    N/A (no context filter active)
    ↓
Strategy Selection                    N/A (all patterns trade)
    ↓
Risk Validation                       N/A (no EV gate)
    ↓
Execution (shadow only)               3,152 shadow trades
    ↓
Outcome                               95.6% timeout, 4.4% SL, 0% TP
```

### Where Information Is Lost

1. **No context filter:** V3 features are observed but NOT used for filtering. Everything trades.
2. **No location gate:** The +0.07R OB signal is not applied as a filter.
3. **No RR adjustment:** Risk geometry doesn't adapt to market state.

---

## 9. Comparison Against V2

| Dimension | V2 | V3 | Improvement |
|---|---|---|---|
| Data quality | 27% field population | **89% field population** | +230% |
| Context awareness | Regime + bias + session | **+ Location + liquidity + FVG + OB** | Major expansion |
| Pattern understanding | Patterns don't predict | Confirmed + **location matters** | New finding |
| Decision traceability | Full lineage | Full lineage + V3 features | Same quality |
| Research confidence | HIGH (null result proven) | MEDIUM (positive signal found) | Progressing |
| Strongest finding | None (all negative) | **Inside OB: +0.071R** | First positive |

### V2 Problems Solved

- ✅ Feature population (89% vs 27%)
- ✅ Composition artefact identified and filtered
- ✅ Linkage pipeline working (92.4%)
- ✅ Market intelligence detectors producing data

### Problems Remaining

- ❌ Spread cost still dominates (0.48R)
- ❌ No LONDON/ASIA session data
- ❌ 95% timeout rate (RR structure broken)
- ❌ H4 range_position always empty
- ❌ prev_day_high/low never populated
- ❌ Only 23 "inside OB" events (need 50+)

---

## 10. Research Readiness Verdict

### **READY FOR PRELIMINARY ANALYSIS**

Justification:
- 158 linked post-Phase-2 records (above 100 minimum for single-feature testing)
- Feature population at 89% (15 of 17 key fields populated above 20%)
- One GREEN finding exists (inside OB: +0.071R)
- Multiple YELLOW findings with consistent direction (location gradient, equal levels, FVG)
- Research pipeline fully operational
- No data contamination (post-P2 filter applied)

**NOT ready for:** Hypothesis validation (need n=50 per feature group), strategy development (no cost-adjusted positive signal), or live validation (no proven edge).

---

## Key Metrics Table

| Metric | Value |
|---|---|
| Total V3 observations | 315 |
| Linked outcomes | 266 (84%) |
| Post-Phase-2 analysable | 158 |
| Feature population | 89% (63/71 fields) |
| Baseline EV (post-P2) | -0.056R |
| Baseline WR | 52.5% |
| Best feature (raw EV) | Inside OB: **+0.071R** |
| Best feature (cost-adj) | Inside OB: **-0.409R** |
| Strongest effect size | Equal lows present: **+0.18R** vs absent |
| Tests passing | 3,457 |
| New regressions | 0 |

---

## Major Discoveries

1. **Price inside Order Block = positive raw EV** (+0.071R, n=23, CI barely > 0). First positive finding in all research.
2. **Location gradient is real:** Discount WR=62.7% > Mid WR=51.9% > Premium WR=38.9%.
3. **Institutional zone proximity helps:** OB/FVG presence = +0.10 to +0.18R vs absence.
4. **95.6% timeout rate** reveals fundamental RR structure problem — targets are unreachable.

---

## Failed Hypotheses

| Hypothesis | Status | Evidence |
|---|---|---|
| M5 patterns predict direction | **REJECTED** (V2) | -0.70R after costs, n=867 |
| H4/H1 regime filters create edge | **REJECTED** (V2) | 0/21 features significant |
| Session timing creates edge | **REJECTED** (V2) | 0 favourable environments |
| Liquidity sweep predicts reversal | **INSUFFICIENT** | n=11, shows -0.17R |
| Rejection candle predicts bounce | **NEUTRAL** | -0.09R (slightly worse) |

---

## Current Strongest Evidence

1. **Inside OB: +0.071R** (GREEN, n=23, needs confirmation at n=50)
2. **Equal lows presence: +0.18R effect** (YELLOW, large effect but vs small absent group)
3. **Supply OB presence: +0.14R effect** (YELLOW, robust sample n=123)
4. **M15 discount zone: WR 62.7%** (YELLOW, 10pp above premium, n=59)

---

## Biggest Remaining Uncertainty

**Can the +0.07R inside-OB signal survive transaction costs if risk distance is increased?**

Currently: spread/risk = 0.48 (48% of risk consumed by spread).
If risk distance doubled: spread/risk = 0.24.
Required raw EV at 0.24 spread: +0.24R to break even.
Current inside-OB raw EV: +0.071R.

**Gap: need +0.17R more signal.** This might come from combining OB + FVG + discount. Or it might require a fundamentally different entry timeframe (H1/H4).

---

## Recommended Next Research Questions (Priority Order)

1. **Can OB + FVG + discount combination exceed +0.25R?** (minimum for breakeven at reduced spread/risk)
2. **Does wider stop distance preserve the OB signal while reducing spread burden?**
3. **Does the inside-OB finding hold at n=50?** (need 27 more events at 15% rate ≈ 180 more records)
4. **What happens during LONDON/NY sessions?** (currently 100% OFF session data)
5. **Can timeout rate be reduced by adjusting TP targets?** (95.6% timeout suggests targets too ambitious)
