# CE1: Cost-Adjusted Expected Value Experiment

**Date:** 2026-07-27
**Data:** CURRENT epoch only (n=867)
**Method:** Estimated spread per symbol applied to risk_price_distance
**Spread source:** Conservative typical spreads (EURUSD=1.0pip, GBPUSD=1.3pip, etc.)
**Validity:** CURRENT epoch, all trades, no cherry-picking, no look-ahead

---

## HEADLINE RESULT

| Metric | Value | 95% CI |
|--------|-------|--------|
| **Raw EV (before costs)** | **-0.219R** | [-0.256, -0.183] |
| **Average spread cost** | **0.478R per trade** | — |
| **Cost-adjusted EV** | **-0.697R** | [-0.770, -0.625] |
| Significance | t = -18.84, p < 0.0000001 | Certainly negative |

**The system loses approximately 0.70R per trade after transaction costs.** This is a catastrophic negative expected value that no exit optimisation, strategy filtering, or position sizing can overcome.

---

## WHY COSTS ARE SO HIGH

### The Spread-to-Risk Ratio Problem

| Risk Distance | Trades | % of Total | Spread as % of Risk | Raw EV |
|---|---|---|---|---|
| < 2 pips | 36 | 4.2% | **50-100%** | -0.31R |
| 2-4 pips | 441 | **50.9%** | **25-50%** | -0.07R |
| 4-6 pips | 99 | 11.4% | 20-25% | -0.06R |
| 6-10 pips | 86 | 9.9% | 10-17% | -0.02R |
| 10+ pips | 205 | 23.6% | 7-10% | -0.68R |

**The core problem:** 55% of trades use SL distances of 2-4 pips. With a 1-pip spread, the spread consumes 25-50% of the entire risk budget before the trade even moves.

### The Paradox

- Trades with SMALL risk (2-4 pips): Best raw EV (-0.07R) but spread = 25-50% of risk (execution impossible)
- Trades with LARGE risk (10+ pips): Best cost ratio (7-10%) but worst raw EV (-0.68R)
- There is NO risk distance where BOTH raw EV is positive AND costs are manageable

---

## PER-SYMBOL ANALYSIS

| Symbol | n | Raw EV | Cost/Trade | Adjusted EV | Viable? |
|---|---|---|---|---|---|
| EURUSD | 209 | -0.448R | 0.261R | -0.709R | ❌ NO |
| NZDUSD | 156 | -0.066R | 0.476R | -0.542R | ❌ NO |
| AUDUSD | 137 | -0.362R | 0.259R | -0.621R | ❌ NO |
| USDCAD | 125 | -0.076R | 0.419R | -0.494R | ❌ NO |
| GBPUSD | 106 | -0.144R | 0.368R | -0.512R | ❌ NO |
| USDCHF | 88 | -0.060R | 0.442R | -0.502R | ❌ NO |
| USDJPY | 46 | -0.146R | 2.604R | -2.750R | ❌ NO |

**No symbol achieves positive cost-adjusted EV.** USDJPY has the worst cost ratio because JPY pairs use pip sizes 100× larger (0.01 vs 0.0001) while spreads are proportionally larger.

---

## SUBSET ANALYSIS

### Trades where spread < 20% of risk (n=282, 32.5% of total)

| Metric | Value | CI |
|--------|-------|-----|
| Raw EV | -0.502R | — |
| Cost | 0.121R | — |
| **Adjusted EV** | **-0.623R** | [-0.681, -0.566] |

Even the most cost-efficient subset (wide risk distances) has deeply negative EV. The signal quality is WORSE for wide-SL trades, not better.

### Trades with risk ≥ 5 pips (n=335, 38.6%)

| Raw EV | -0.433R |
| Cost | 0.140R |
| **Adjusted EV** | **-0.573R** |

### Trades with risk ≥ 10 pips (n=205, 23.6%)

| Raw EV | -0.677R |
| Cost | 0.105R |
| **Adjusted EV** | **-0.783R** |

---

## VALIDITY VERIFICATION

| Check | Status |
|-------|--------|
| CURRENT epoch only | ✅ n=867 via load_shadow_trades(epoch='CURRENT') |
| No legacy contamination | ✅ Epoch safety enforced |
| Transaction cost applied per trade | ✅ Based on symbol-specific spreads |
| Conservative spread estimates | ✅ Using typical, not minimum, spreads |
| No cherry-picking | ✅ All CURRENT trades included |
| Statistical testing | ✅ t-test, CI, significance reported |
| No look-ahead | ✅ Spread is a known constant, not outcome-derived |

---

## RESEARCH VALIDITY GATE

```
Epoch: CURRENT ✅
Sample: n=867 ≥ 100 ✅
CI reported: ✅
Significance: p < 0.0001 ✅
Architecture: new_pipeline_v1.2 ✅
```

**Gate status: PASSED (result is trustworthy — the system definitively loses money after costs)**

---

## PROMOTION DECISION

### 🔴 NO EDGE EXISTS AFTER TRANSACTION COSTS

**Classification:** REJECT — no promotable finding.

The system cannot achieve positive EV at any:
- Stop distance (tested 0.25R through 5.0R)
- Take profit level (tested 0.25R through 3.0R)
- Trailing configuration (30 configs tested)
- Symbol
- Risk distance bucket
- Strategy family
- Market phase

---

## ROOT CAUSE ANALYSIS

The system has TWO compounding problems:

### Problem 1: Signal has zero or negative predictive value (Raw EV = -0.22R)

Even before costs, the entry signal loses money. The directional prediction (BUY/SELL) is not better than random — it's actually WORSE than random. A random entry would produce EV ≈ -spread (about -0.48R), and the system produces -0.22R raw (slightly better than pure spread loss), meaning the directional signal recovers only part of the spread cost.

### Problem 2: SL geometry is too tight for the spread environment (avg cost = 0.48R)

The median SL is 3.46 pips. With 1-pip spreads, the cost alone consumes 29% of risk. Even if the signal were perfectly directional (50% WR with 1:1 RR), the system would need EV > 0.48R just to BREAK EVEN.

### Combined: The entry signal would need EV > +0.48R (before costs) to be viable

The actual raw EV is -0.22R. The gap between required (+0.48R) and actual (-0.22R) is **0.70R per trade** — exactly what the cost-adjusted EV shows (-0.70R).

---

## WHAT THIS MEANS FOR THE TRADING SYSTEM

1. **No optimisation of the current system can create profitability.** Exits, stops, trailing, horizons, strategy selection — none can overcome a 0.70R deficit per trade.

2. **The system needs EITHER:**
   - (a) Entries with significantly stronger directional signal (raw EV > +0.5R), OR
   - (b) Dramatically wider SL distances (10+ pips minimum) to reduce spread-to-risk ratio, combined with entries that work at that timescale, OR
   - (c) A fundamentally different approach (higher timeframe, wider risk, fewer but higher-quality entries)

3. **The research engine correctly identifies this.** The CE1 experiment produces a trustworthy, validated conclusion: the current system architecture cannot be profitable.

---

## NEXT RESEARCH QUESTION

> "Under what conditions (if any) does the entry signal produce raw EV > +0.5R — enough to overcome realistic transaction costs?"

This requires investigating:
- Specific pattern × phase × regime combinations
- Only within the 6-10 pip risk distance bucket (EV=-0.015R, best raw performance)
- Whether those specific subsets can be identified at entry time
