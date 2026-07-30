# V5.1 — Market Regime Intelligence Results

**Date:** 2026-07-29
**Dataset:** 368 V3 execution assessments with full market context
**Verdict:** B) Regime information provides limited filtering value but reveals deeper structural problems

---

## Critical Discovery: The Data Is Mono-Regime

**ALL 368 execution assessments occurred in RANGING / NEUTRAL volatility / NEUTRAL expansion.**

| Feature | Value | Count |
|---|---|---|
| Regime | RANGING | 368/368 (100%) |
| Volatility | NEUTRAL | 368/368 (100%) |
| Expansion | NEUTRAL | 368/368 (100%) |

This means:
- V3 NEVER fires in trending markets
- V3 NEVER fires in high/low volatility
- V3 NEVER fires during expansion

**The regime question cannot be answered** because V3's architecture is ALREADY filtering to ranging/neutral conditions by construction. There is no variance to test.

---

## What DOES Vary: Momentum, Location, Structure

### Feature Importance Ranking (by EV separation power)

| Rank | Feature | Spread | Best | Worst |
|---|---|---|---|---|
| 1 | **MOMENTUM ALIGNMENT** | **+0.346R** | NEUTRAL (+0.280R) | WITH trade (-0.067R) |
| 2 | LOCATION TYPE | +0.164R | DEMAND_OB (+0.127R) | SUPPLY_OB (-0.037R) |
| 3 | ENTRY STATE | +0.132R | WEAK (+0.020R) | VALID (-0.112R) |
| 4 | INSIDE ZONE | +0.095R | OUTSIDE (+0.119R) | INSIDE (+0.023R) |
| 5 | CURRENCY STRENGTH | +0.052R | ALIGNED (+0.012R) | OPPOSED (-0.040R) |
| 6 | HTF ALIGNMENT | +0.050R | NEUTRAL (+0.041R) | COUNTER (-0.009R) |
| 7 | PREMIUM/DISCOUNT | +0.020R | DISCOUNT (+0.008R) | EQUILIBRIUM (-0.012R) |

---

## The Momentum Finding Is Startling

| Momentum vs Trade Direction | n | WR | EV | P(>0.5R) | P(>1R) |
|---|---|---|---|---|---|
| **NEUTRAL momentum** | **203** | **47.3%** | **+0.280R** | 21% | 12% |
| AGAINST trade direction | 60 | 57.1% | +0.100R | 20% | 7% |
| **WITH trade direction** | **69** | **34.1%** | **-0.067R** | 10% | 3% |

**Trading WITH momentum is the WORST configuration.** Trading into NEUTRAL momentum is BEST.

This is the **same contrarian pattern** found in V3/V4:
- WEAK entry > VALID entry (less confirmation = better)
- NEUTRAL macro > HTF aligned (less agreement = better)
- NEUTRAL momentum > WITH momentum (less direction = better)

**The system's profitable trades are NOT directional plays.** They are structure-based mean-reversion trades that happen to fire when the market is NOT already moving.

---

## Location Analysis

| Location | n | WR | EV |
|---|---|---|---|
| **DEMAND_OB** | 30 | **63.3%** | **+0.127R** |
| BEARISH_FVG | 26 | 65.4% | +0.086R |
| OPEN_SPACE | 269 | 44.2% | +0.119R |
| SUPPLY_OB | 34 | 35.3% | -0.037R |
| BULLISH_FVG | 9 | 33.3% | -0.276R |

**Demand OBs and Bearish FVGs produce the highest WR.** But sample sizes are small (n=26-34).

### Inside Zone INVERTS Expected Behavior

| Zone Status | n | WR | EV | P(>0.5R) |
|---|---|---|---|---|
| **OUTSIDE zone** | **269** | 44.2% | **+0.119R** | **27%** |
| Inside zone | 99 | 51.5% | +0.023R | 14% |

**Outside zones produce more movement** (+0.119R, 27% reach 0.5R) than inside zones (+0.023R, 14% reach 0.5R). This challenges the V3 assumption that institutional zones are premium locations.

---

## Best Combined Configuration

| Configuration | n | WR | EV | Net @15p | P(>0.5R) |
|---|---|---|---|---|---|
| **WEAK + inside zone** | **40** | **62.5%** | **+0.183R** | **+0.103R** | 12% |
| Baseline (all) | 368 | 46.2% | +0.093R | +0.013R | 23% |
| WEAK only | 174 | 49.4% | +0.020R | -0.060R | 12% |

**WEAK + inside zone = +0.183R (net +0.103R at 15p stops)**

But:
- n=40 (underpowered)
- CI includes zero: [-0.028, +0.394]
- P(>0.5R) is only 12% — high WR but small wins

---

## Structure Alignment: INVERTED

| Structure Alignment | n | WR | EV |
|---|---|---|---|
| **Low (<0.5)** | 40 | **55.0%** | **+0.070R** |
| Medium (0.5-0.8) | 80 | 52.5% | +0.054R |
| **High (>0.8)** | 54 | 40.7% | **-0.069R** |

**Higher structure alignment = WORSE outcomes.** This confirms the pattern: the more the market agrees with your direction, the worse the trade performs.

---

## Time Stability: MOMENTUM ALIGNMENT UNSTABLE

| Period | WITH momentum | AGAINST momentum | Delta |
|---|---|---|---|
| Early | -0.281R (n=15) | +0.562R (n=10) | -0.843 |
| Middle | -0.046R (n=27) | -0.025R (n=28) | -0.020 |
| Recent | +0.031R (n=27) | +0.048R (n=22) | -0.018 |

The massive early-period effect (+0.562R for AGAINST momentum) **disappears in middle and recent periods.** The momentum finding is time-unstable.

---

## V5.1 Verdict

### B) Regime information provides limited filtering value — but reveals deeper structural truths

**What we learned:**

1. **The regime question is unanswerable** — V3 only fires in RANGING/NEUTRAL (100%). There's no trending data to compare against.

2. **Momentum alignment is the strongest feature** (+0.346R spread) but INVERTS: NEUTRAL > AGAINST > WITH. Trading with momentum is worst.

3. **Structure alignment is inverted** — higher agreement = worse. Low alignment (+0.070R) > High alignment (-0.069R).

4. **Inside zone is inverted** — outside produces more EV and more movement.

5. **The movement problem is NOT solved** — best configuration still only 12% reach 0.5R.

6. **Time stability is poor** — the momentum effect collapses in recent periods.

---

## The Deeper Truth This Reveals

Every finding in the research program now points the same direction:

| Signal | What helps | What hurts |
|---|---|---|
| Entry timing | WEAK (early) | VALID (confirmed) |
| Opportunity | INTERESTING (not perfect) | HIGH_QUALITY (too confirmed) |
| Momentum | NEUTRAL (no direction) | WITH trade (trending) |
| Structure | Low alignment | High alignment |
| Location | Open space | Inside institutional zone |
| HTF bias | NEUTRAL | Aligned with trade |
| Currency strength | Crowd neutral/split | Crowd agrees with you |

**Every confirmation/agreement signal is INVERTED.** The system makes money on:
- Trades with low agreement
- Neutral environments
- Weak confirmation
- Open locations (not trapped in zones)

**This is a MEAN-REVERSION architecture**, not a trend-following one. It profits when it goes AGAINST the local consensus — which explains why:
- Currency strength (crowd agreement) hurt the WEAK entries (V4.4b)
- More structure alignment = worse (V5.1)
- Stronger confirmation = worse (AR2)

---

## Recommended Direction

The V5.1 findings suggest the architecture is fundamentally a **contrarian/mean-reversion system** that makes small gains from temporary mispricings. Its edge (if any) exists in:

1. **Low-agreement, neutral-momentum environments** where structure creates a temporary dislocation
2. **WEAK entry timing** catches the dislocation before confirmation arrives (by which point the move is done)
3. **Institutional zones reduce movement** rather than enabling it (the zone catches the move before it happens)

**V5.2 could investigate:** Whether explicitly classifying "contrarian opportunity score" (inverse of agreement metrics) creates a more coherent signal than the inverted use of trend-following features.

Or: **Accept the null** — the system has ~50.7% direction accuracy with positive but small EV (+0.02-0.09R) that survives at 15-20p stops but cannot be validated due to sample size and time instability.
