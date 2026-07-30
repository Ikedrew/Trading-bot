# V6.4 — Index Market Transfer Validation Results

**Date:** 2026-07-27
**Dataset:** 214 index shadow trades (NAS100: 62, US500: 72, XAUUSD: 80) vs 368 FX execution assessments
**Verdict:** C) Index markets improve movement conditions but current signal produces NEGATIVE EV

---

## Key Finding: Movement Improves Dramatically, But Direction Accuracy Drops

| Metric | FX (baseline) | INDEX | Change |
|---|---|---|---|
| **n** | 368 | 214 | — |
| **Win Rate** | 46.2% | **40.2%** | **-6.0%** |
| **EV** | +0.093R | **-0.112R** | **-0.206R** |
| Avg MFE | 0.504R | **1.178R** | +0.675R |
| Avg MAE | 0.409R | **1.221R** | +0.812R |
| **Timeout Rate** | 82.1% | **55.1%** | **-27%** |
| **P(>0.5R)** | 23.4% | **47.7%** | **+24.3%** |
| **P(>1R)** | 8.2% | **31.3%** | **+23.2%** |
| P(>2R) | 7.1% | 12.6% | +5.6% |

---

## The Paradox Explained

**Index markets produce FAR more movement** — the timeout problem is largely solved:
- Timeout rate drops from 82% → 55%
- P(>0.5R) jumps from 23% → 48%
- P(>1R) jumps from 8% → 31%

**But the system gets direction WRONG more often:**
- WR drops from 46.2% → 40.2%
- EV flips from +0.093R to -0.112R
- MAE (adverse movement) increases more than MFE (favourable movement)

**The architecture's directional signal does NOT transfer directly.** The same logic that produces ~50% accuracy on FX ranging markets produces ~40% on index markets. Indices move MORE, but they move MORE against the system's predictions too.

---

## Per-Symbol Breakdown

| Symbol | n | WR | EV | MFE | P(>0.5R) | Timeout |
|---|---|---|---|---|---|---|
| NAS100 | 62 | 41.9% | -0.164R | 2.211R | 56.5% | 45.2% |
| US500 | 72 | 40.3% | -0.092R | 0.822R | 50.0% | 56.9% |
| XAUUSD | 80 | 38.8% | -0.091R | 0.699R | 38.8% | 61.3% |

All three instruments show:
- Negative EV (the system loses money)
- Excellent movement (45-57% reach 0.5R)
- Low timeout rates (45-61% vs 82% for FX)
- Win rates below 42%

**NAS100 shows the highest MFE (2.211R)** but also the worst EV (-0.164R) — the market moves violently but the system picks the wrong direction.

---

## What This Means

### V6.1 Hypothesis: "Was the limitation the market or the architecture?"

**ANSWER: Both.**

1. **The market WAS limiting movement** — confirmed. Indices produce 2-4x more usable movement.
2. **The architecture's directional signal is FX-specific** — it produces ~40% accuracy on indices (worse than random).

### Why The System Fails On Indices

The V3/V5 research showed the system is **contrarian/mean-reverting**:
- It trades AGAINST momentum
- It works in NEUTRAL/RANGING conditions
- It enters on WEAK confirmation (early, before consensus)

Indices are **momentum-driven/trending**:
- 55-60% of time in directional moves
- Structure (BOS) leads to continuation, not reversal
- "Weak" entries against the trend get steamrolled

**A mean-reversion system applied to a trending market predicts the wrong direction.** This explains the 40% WR and negative EV — the system is systematically fading the trend.

---

## Critical Caveat: Data Source Limitation

This analysis uses **shadow_trades only** (no V3 pipeline context). This means:
- No entry_state classification (WEAK/VALID)
- No momentum/regime data per-trade
- No opportunity_state filtering
- The shadow trades represent ALL signals, not V3-filtered signals

When the V3 pipeline fully processes index data (execution_assessment stage), the results may differ because:
- V3 filtering may reject the worst trades
- Entry timing classification may change behaviour
- Regime detection may produce useful variance (trending vs ranging)

---

## V6.4 Verdict

### C) Index markets improve movement conditions but current signal produces NEGATIVE EV

**The V6.1 hypothesis is partially confirmed:**
- ✓ Movement limitation IS market-specific (solved by indices)
- ✓ Cost pressure IS reduced (less timeout = more opportunity)
- ✗ Directional signal does NOT transfer (40% WR = worse than random)
- ✗ The architecture IS partially market-specific (contrarian ≠ indices)

---

## Implications and Recommended Direction

### Option A: Adapt Architecture for Indices
The V3 system could work on indices IF the contrarian bias is REVERSED:
- Trade WITH momentum instead of against
- Enter on VALID confirmation (trend continuation) not WEAK (reversal)
- Use structure alignment positively (aligned = good) not inverted

This would be a fundamental change to the decision logic — essentially building a TREND-FOLLOWING system using the same observation infrastructure.

### Option B: Use Indices for Movement, FX for Signal
Hybrid approach:
- FX V3 identifies WHEN conditions are favourable (neutral, ranging)
- Index observations identify WHERE movement is available
- Cross-market signal: "FX says calm → index may break out"

### Option C: Accept Null Result
The research program has exhaustively tested:
- V1: Pattern strategy → REJECTED
- V2: Context-as-signal → REJECTED  
- V3/V5: Architecture on FX → signal too weak after costs
- V4: Currency strength → context-dependent, not additive
- V6: Market transfer → movement improves but direction fails

**No reliable, stable, cost-covering edge has been found** across any configuration tested.

### Option D: Rebuild for Trending Markets
Use the observation infrastructure (structure detection, zone ID, multi-TF) but redesign the DECISION logic for trend-following:
- Enter pullbacks in established trends (not reversals in ranging)
- Use BOS as CONTINUATION signal (not exhaustion)
- VALID confirmation = good (trend resumed after pullback)
- Higher timeframe alignment = good (not inverted)

This would require new research (AR-series equivalent for index trend-following) but keeps the proven observation pipeline.
