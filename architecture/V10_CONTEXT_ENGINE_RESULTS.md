# V10 — Context-Driven Strategy Execution Engine Results

**Date:** 2026-07-27
**Dataset:** 368 V3 execution assessments with full H4/H1/M15/M5 context
**Verdict:** B) Strategy routing adds value but reveals an unexpected truth — the "movement" comes from HTF NEUTRAL states, not trends

---

## THE SURPRISING FINDING

The V10 hypothesis was: "H4/H1 trend alignment + M15 pullback + zone = better trades."

**Reality is OPPOSITE:**

| Configuration | n | WR | EV | Net @12% | Timeout |
|---|---|---|---|---|---|
| HTF CLEAR + aligned with trade | 63 | 38.1% | **-0.067R** | -0.187R | **95%** |
| **HTF NEUTRAL (no bias)** | **171** | **46.2%** | **+0.241R** | **+0.121R** | **69%** |
| M15 pullback active | 302 | 46.4% | -0.021R | -0.141R | **94%** |
| **NO pullback** | **66** | **45.5%** | **+0.615R** | **+0.495R** | **26%** |

**Everything the V10 architecture proposed is WRONG:**
- HTF trend alignment = WORST (EV -0.067R, 95% timeout)
- HTF neutral = BEST (EV +0.241R, CI excludes zero)
- M15 pullback = WORST (94% timeout)
- NO pullback = BEST (+0.615R, only 26% timeout!)

---

## Strategy Family Results

| Strategy | n | WR | EV | Net | Timeout | P(>0.5R) |
|---|---|---|---|---|---|---|
| **MEAN_REVERSION** | **107** | 41.1% | **+0.341R** | **+0.221R** | **53%** | **50%** |
| ZONE_REACTION | 35 | 65.7% | +0.189R | +0.069R | 91% | 11% |
| TREND_CONTINUATION | 44 | 50.0% | -0.030R | -0.150R | 95% | 20% |
| UNCLASSIFIED | 182 | 44.5% | -0.041R | -0.161R | 94% | 11% |

**MEAN_REVERSION is the strongest strategy (CI excludes zero: [+0.070, +0.611]).**
It has:
- Lowest timeout (53% vs 82-95% for others)
- Highest movement probability (50% reach 0.5R)
- Highest net EV (+0.221R)

But wait — mean reversion was classified as: "HTF neutral + price at extremes." This is the SAME finding as V5.1/V5.2: **the system makes money in neutral conditions at range extremes.**

---

## The "No Pullback" Discovery

The most startling finding:

| Context | n | EV | Timeout | P(>0.5R) |
|---|---|---|---|---|
| M15 pullback active | 302 | -0.021R | **94%** | 11% |
| **No pullback** | **66** | **+0.615R** | **26%** | **80%** |

**When NO pullback is active, 80% of trades produce >0.5R movement and only 26% timeout.** This completely contradicts the "pullback into zone" narrative.

What "no pullback" means: the M15 structure is NOT pulling back — it's in **impulse/displacement/fresh expansion**. These are the trades where the market is ALREADY MOVING.

The implication: the system performs best when it enters DURING movement, not when it waits for a pullback to complete.

---

## Zone Reaction: Still Positive But Doesn't Solve Timeout

| Metric | Zone Reaction (n=35) |
|---|---|
| WR | 65.7% |
| EV | +0.189R |
| Net | +0.069R |
| Timeout | **91%** |
| P(>0.5R) | 11% |
| Time stable | YES (H1=+0.25, H2=+0.13) |
| USDJPY contribution | +1.018R (5 trades) — dominates |

Zone reaction is net-positive and time-stable, but it does NOT solve the movement problem (91% timeout). Its edge comes from the few trades that DO move being strongly positive — same runner-dependency pattern as V5.2.

---

## Best Overall Configuration

**HTF NEUTRAL + ZONE_REACTION (n=18):**
- WR = 77.8%
- EV = +0.376R
- Net = +0.256R
- BUT: n=18 (far too small), CI includes zero [-0.058, +0.808]

This is the theoretical optimum but completely underpowered.

---

## V10 Verdict

### B) Strategy routing shows promise but reveals the architecture needs a different orientation

**What V10 proves:**
1. HTF trend alignment HURTS (not helps) — the system is contrarian
2. HTF NEUTRAL is the optimal environment (CI excludes zero, +0.241R)
3. NO pullback = massive movement (+0.615R, 80% reach 0.5R)
4. MEAN_REVERSION is the strongest strategy family (+0.341R, 50% reach 0.5R)
5. Pullback-based entries produce 94% timeouts
6. Classified trades (+0.225R) beat unclassified (-0.041R)

**What V10 contradicts:**
- "H4/H1 should give trend permission" — NO, neutral is better
- "M15 pullback should time the entry" — NO, impulse/no-pullback is better
- "Trend continuation is the natural strategy" — NO, it's net-negative

---

## Revised Understanding

The system's profitable trades occur when:
1. **HTF is NEUTRAL** (no strong directional bias — market hasn't committed)
2. **M15 is in fresh impulse** (no pullback — market is STARTING to move)
3. **Price is at an institutional zone** (demand/supply OB, FVG)
4. **M5 gives WEAK entry** (first sign of rejection/structure, before consensus)

This is a **BREAKOUT FROM NEUTRAL** strategy — not trend continuation, not mean reversion at extremes. It catches the FIRST move from a neutral/undecided state when structure starts to form.

---

## Architecture Recommendation (Revised)

```
ACTUAL V10 (data-driven, not assumption-driven):
┌──────────────────────────────────────────────────────────────────┐
│ H4/H1: Gate = NEUTRAL only (no strong bias required)              │
│ M15:   Gate = NOT in pullback (fresh impulse/displacement)        │
│ M5:    Entry = WEAK confirmation at institutional zone            │
│                                                                  │
│ TRADE WHEN:                                                       │
│   - HTF is neutral/undecided                                      │
│   - Market is NOT pulling back (fresh directional move forming)   │
│   - Price is at institutional zone                                │
│   - WEAK confirmation on M5                                       │
│                                                                  │
│ DO NOT TRADE WHEN:                                                │
│   - HTF has strong established trend (95% timeout)                │
│   - M15 is in pullback (94% timeout)                              │
│   - Open space with no zone context                               │
└──────────────────────────────────────────────────────────────────┘
```

**Key sample sizes:**
- HTF neutral: n=171, CI excludes zero ← strongest finding
- No pullback: n=66, +0.615R ← powerful but small
- Zone + WEAK: n=35, +0.189R ← consistent but underpowered
- Mean reversion family: n=107, CI excludes zero ← second strongest

The research continues to converge on the same answer: **neutral environments with fresh movement produce positive EV.** The challenge remains sample size and the 65-95% timeout rate for most configurations.
