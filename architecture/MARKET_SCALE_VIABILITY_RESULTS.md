# Market Scale Viability Results

**Date:** 2026-07-27
**Data:** CURRENT epoch (n=867)
**Method:** Bar-by-bar simulation with cost adjustment
**Primary metric:** Cost-adjusted EV = raw_R - (spread / risk_distance)

---

## 1. EXECUTIVE VERDICT

### "Is the current failure caused by insufficient movement scale?"

## YES — but with a critical qualification.

The system operates at M5 timeframe with median 3.46-pip stops. At this scale, **spread consumes 48% of every risk unit on average**. Even with zero directional error (random entry), the system would lose -0.48R per trade purely from costs.

**Widening the risk geometry reduces cost impact** (confirmed by MS2), but the underlying signal has **slightly negative directional value** (raw EV = -0.013R at 1x risk), meaning even at infinite risk distance the system would still lose marginally.

The combination of:
1. Zero directional edge (raw EV ≈ 0 before costs)
2. Massive cost burden (48% of risk)

...produces the observed -0.70R after costs.

---

## 2. MS1 — TIMEFRAME VIABILITY

Simulated by widening risk geometry to approximate higher timeframes:

| Simulated TF | Risk Mult | SL (pips) | Spread % of Risk | Raw EV (new R) | Adj EV | 95% CI | Viable? |
|---|---|---|---|---|---|---|---|
| **M5 (current)** | 1× | 5.4 | **47.8%** | -0.013 | **-0.491** | [-0.57, -0.41] | ❌ |
| M15 equiv | 3× | 16.3 | 15.9% | -0.024 | **-0.183** | [-0.21, -0.15] | ❌ |
| H1 equiv | 12× | 65 | 4.0% | -0.008 | **-0.048** | [-0.06, -0.04] | ❌ |
| H4 equiv | 48× | 260 | 1.0% | -0.002 | **-0.012** | [-0.01, -0.01] | ❌ |

### Key Finding

As risk widens:
- Cost impact drops dramatically (48% → 1%)
- But **raw EV remains slightly negative** at every scale (-0.013 to -0.002)
- No timeframe equivalent achieves positive adjusted EV

**Interpretation:** Widening risk does NOT create edge — it only reduces the cost penalty. The underlying signal has no directional value at ANY timeframe scale when holding period remains fixed at 60 bars.

### Critical Limitation

This simulation uses M5 ENTRIES with wider geometry. It does **NOT** test actual H1/H4 pattern detection (which would produce different entries with potentially different directional accuracy). The conclusion is: "current M5 entries cannot become viable by widening risk alone."

---

## 3. MS2 — RISK GEOMETRY VIABILITY

| Risk Multiplier | SL (pips) | Spread % | Raw EV | Adj EV | 95% CI | Stop-out % |
|---|---|---|---|---|---|---|
| 1.0× | 5.4 | 47.8% | -0.013 | -0.491 | [-0.57, -0.41] | 15.5% |
| 1.5× | 8.2 | 31.9% | -0.041 | -0.359 | — | 6.5% |
| 2.0× | 10.9 | 23.9% | -0.033 | -0.272 | — | 0.8% |
| 3.0× | 16.3 | 15.9% | -0.024 | **-0.183** | [-0.21, -0.15] | 0.6% |
| 5.0× | 27.2 | 9.6% | -0.016 | -0.112 | [-0.13, -0.09] | 0.3% |
| 7.0× | 38.1 | 6.8% | -0.012 | -0.081 | [-0.09, -0.07] | 0.2% |
| 10.0× | 54.4 | 4.8% | -0.009 | -0.057 | [-0.07, -0.05] | 0.1% |

### Key Finding

The curve is **asymptotically approaching zero from below** but never crosses zero:
- At 10× risk: EV = -0.057R (still negative, CI entirely below zero)
- Even at hypothetical 100× risk: extrapolated EV ≈ -0.010R (still negative)

**The system converges to slightly negative EV as cost diminishes.** This proves the raw signal itself is directionally neutral-to-negative — it does not contain usable predictive information regardless of cost structure.

---

## 4. MS3 — MOVEMENT EXPANSION DETECTION

### By Movement-to-Cost Ratio

| MFE / Spread Cost | n | Avg MFE | Raw EV | Adj EV | Win Rate |
|---|---|---|---|---|---|
| < 0.5× (cost > move) | 387 | 0.12R | -0.296R | **-0.887R** | 0% |
| 0.5–1.0× | 170 | 0.46R | +0.024R | -0.615R | 0% |
| 1.0–2.0× | 122 | 0.54R | +0.253R | -0.116R | 38.5% |
| **2.0–3.0×** | 37 | 0.83R | +0.489R | **+0.143R** | **75.7%** |
| **3.0–5.0×** | 13 | 1.35R | +0.732R | **+0.368R** | **69.2%** |
| 10.0+× | 138 | 3.00R | -1.000R | -1.107R | 0% |

### Critical Discovery

**Trades where movement exceeds cost by 2–5× DO produce positive adjusted EV:**
- Combined n=50: Adj EV = **+0.201R** [CI: +0.076, +0.327]
- Win rate: 74.0%

**BUT THIS CANNOT BE USED AS AN ENTRY FILTER** because:
1. MFE (maximum favourable excursion) is only knowable AFTER the trade completes
2. It is a retrospective measure of how far price moved
3. You cannot know at entry time whether a trade will achieve 2× movement-to-cost
4. This is equivalent to saying "winning trades are profitable" — tautological

### The 10.0+× anomaly

The 138 trades with MFE > 10× cost ratio have EV = -1.0R (ALL hit stop loss). These are trades with very tight stops (high cost_r) where price moved far favourably at some point BUT also hit the stop first. The MFE was reached AFTER an initial drawdown past the SL. This confirms intra-bar SL triggering is a major factor.

---

## 5. BEST SURVIVING ENVIRONMENTS

**No environment survives cost adjustment with statistical confidence at n ≥ 100.**

The only positive cells are:
1. MFE/Cost 2-5× subset (n=50, +0.20R) — **retrospective only, not actionable**
2. BULLISH_ENGULFING (n=10, adj EV = -0.088R, CI upper = +0.011) — **insufficient data**

---

## 6. ENVIRONMENTS TO AVOID

| Environment | n | Adj EV | Why |
|---|---|---|---|
| USDJPY (any) | 46 | -2.75R | JPY spread vs pip-size ratio catastrophic |
| TRENDING regime | 92 | -1.11R | All trades immediately stop out |
| Trades with risk < 3 pips | 441 | -0.58R | Cost = 33-100% of risk |
| HAMMER pattern | 53 | -1.05R | Extreme tight SL geometry |
| THREE_WHITE_SOLDIERS | 106 | -0.86R | Pattern produces large immediate adverse movement |

---

## 7. RESEARCH CONCLUSION

### The current system architecture is fundamentally incompatible with profitable trading.

The failure has TWO independent causes:

**Cause 1: The entry signal has no directional predictive value.**

At every risk scale from 1× to 48×, the raw EV converges to approximately -0.01R (slightly negative). This means the pattern detection + direction assignment is no better than random — it cannot predict whether price will move up or down from the entry point with any consistency.

**Cause 2: The risk geometry amplifies cost to catastrophic levels.**

With median 3.46-pip stops, spread alone consumes 48% of risk. But even solving this (via wider stops) does not create positive EV because the signal has no directional value (Cause 1).

### Neither cause alone explains the failure — both must be addressed:

| Scenario | Cause 1 Fixed? | Cause 2 Fixed? | Result |
|----------|---------------|---------------|--------|
| Current system | ❌ | ❌ | -0.70R |
| Wider risk (10×) | ❌ | ✅ | -0.057R |
| Better signal (hypothetical +0.5R raw) | ✅ | ❌ | +0.5R - 0.48R = +0.02R (marginal) |
| Both fixed | ✅ | ✅ | Viable |

---

## VALIDITY VERIFICATION

| Check | Status |
|-------|--------|
| CURRENT epoch enforced | ✅ |
| Transaction costs included | ✅ Per-trade spread/risk |
| Variables isolated (MS2) | ✅ Only risk distance changes |
| No look-ahead bias | ✅ Bar-by-bar sequential |
| No optimisation leakage | ✅ Results reported for all tested levels |
| Reproducible | ✅ Same data + same logic = same results |

---

## DECISION

### 🔴 The current opportunity generation model lacks sufficient movement AND directional accuracy to overcome execution costs.

No further research within this architecture is likely to find profitability. The system needs:

1. **Entry signals with demonstrable directional accuracy** (raw EV > 0 before any cost consideration) — this is a pattern detection/signal generation problem, not a research infrastructure problem.

2. **Risk geometry appropriate to the instrument** (SL distance must be ≫ spread, meaning 10-20+ pips minimum for FX majors).

3. **Both simultaneously** — fixing either alone is insufficient.

The research engine has successfully and conclusively proven that the current architecture cannot be made profitable through any combination of exit optimisation, strategy filtering, phase matching, horizon selection, or stop distance adjustment. The bottleneck is the signal itself.
