# V4.4b — Currency Strength Effect Decomposition Results

**Date:** 2026-07-29
**Verdict:** The currency strength effect is REAL but CONTEXT-DEPENDENT. It conflicts with V3's reversal-timing architecture.

---

## The Core Discovery

The V4.2 finding (+7.4% WR, +0.242R separation) is **confirmed on the full population** but **does NOT combine additively with V3 WEAK+INTERESTING timing**.

| Population | Aligned EV | Opposed EV | Delta | Direction |
|---|---|---|---|---|
| ALL shadow trades (n=1125) | -0.062R | -0.093R | **+0.032R** | Aligned better |
| V3 exec assessments (n=272) | +0.012R | -0.040R | **+0.052R** | Aligned better |
| WEAK+INTERESTING (n=138) | +0.060R | +0.023R | **+0.038R** | Aligned better (weak) |
| WEAK+INT+3agree (n=25) | +0.105R | — | — | V4.3 result |

**The alignment effect still exists** in the V3 exec population (+0.052R). But it's much weaker in WEAK+INTERESTING (+0.038R) and the 3+agree filter is severely sample-limited (n=25).

---

## Why V4.3's +0.105R Doesn't Generalise

The V4.3 result (n=25, +0.105R) was **not fabricated** — it reproduced exactly. But:

1. **n=25 is far too small** — the CI is [-0.135, +0.199], encompassing zero
2. **Required n for significance: 1,231** — we have 2% of what's needed
3. **Bootstrap profit probability: 61.7%** — barely better than coin flip
4. **The effect is driven by USDJPY** (+0.604R aligned, n=7) — one symbol dominates

---

## The Nuanced Finding

### Where alignment HELPS (V3 exec assessments):

| Entry State | Alignment Effect |
|---|---|
| **WEAK** | +0.035R (modest positive) |
| **VALID** | -0.018R (inverted — aligned worse) |
| **NO_ENTRY** | **+0.135R** (strongest effect) |

### Where alignment HELPS (opportunity state):

| Opportunity | Alignment Effect |
|---|---|
| **INTERESTING** | **+0.077R** (helps) |
| HIGH_QUALITY | -0.022R (neutral/inverted) |
| MIXED | -0.119R (inverted) |

**Key insight:** Alignment helps INTERESTING but not HIGH_QUALITY. This suggests:
- INTERESTING opportunities are less decisively timed → crowd signal adds value
- HIGH_QUALITY is already well-positioned → crowd signal is redundant

---

## Symbol Stability (V4.2 population)

| Symbol | Alignment Effect | Direction |
|---|---|---|
| **USDJPY** | **+0.277R** | Strong aligned |
| GBPUSD | +0.120R | Aligned |
| USDCHF | +0.095R | Aligned |
| AUDUSD | +0.005R | Neutral |
| EURUSD | -0.037R | Opposed |
| USDCAD | -0.054R | Opposed |
| NZDUSD | -0.139R | Strong opposed |

**4/7 symbols show positive alignment.** USDJPY dominates. NZDUSD and EURUSD are inverted.

---

## Time Stability (V4.2 population)

| Period | Alignment Delta |
|---|---|
| Early | -0.073R (aligned WORSE) |
| Middle | +0.067R (aligned better) |
| Recent | +0.102R (aligned better) |

**NOT stable across time.** Early period shows inverted effect.

---

## What Currency Strength Actually Is

The decomposition reveals that "currency strength alignment" measures **crowd agreement with your trade direction**:

- **It is a TREND-FOLLOWING signal** — when many pairs agree, you're trading with momentum
- **V3 WEAK entries are CONTRARIAN** — they identify reversal timing
- **Trend + Contrarian = conflict** — but the conflict is MILD, not destructive

The V4.2 finding was real because ALL shadow trades include both trend-following AND contrarian entries. Currency strength helps the trend-following subset while being neutral-to-mildly-negative for reversals.

---

## V4.4 Verdict

### The +0.105R Combined Finding Is:
- **Not validated** (CI includes zero, n too small)
- **Not generalizable** (USDJPY-dependent, time-variable)
- **Not additive** with V3 reversal timing in the way V4.3 suggested

### But Currency Strength IS:
- A **valid pre-filter** on the full population (saves ~0.03R on unfiltered trades)
- **Not a post-filter** for V3 timing-selected opportunities
- **Context-dependent** — helps INTERESTING, hurts HIGH_QUALITY

---

## Final Assessment

| Claim | Status |
|---|---|
| V4.2: Alignment helps on all trades | **CONFIRMED** (+0.032R, n=1125) |
| V4.2: 3+agree = WR 47% | **CONFIRMED** (47.9%, n=338) |
| V4.3: Combined = +0.105R | **NOT VALIDATED** (n=25, CI includes zero) |
| V4.3: Net positive at 15p | **NOT CONFIRMED** (collapses to +0.032R) |
| Additive with V3 timing | **PARTIALLY** (+0.035R on WEAK, not VALID) |
| Symbol-stable | **NO** (4/7 positive, USDJPY dominates) |
| Time-stable | **NO** (early period inverted) |

---

## Recommended Direction

Currency strength is **real but weak** (+0.03-0.05R) and **not the breakthrough V4.3 suggested**.

Options:
1. **Accept null result on combined signal** — V3 + currency strength doesn't produce viable edge
2. **Implement as PRE-filter only** — reject trades before V3 assessment when 3+ pairs strongly oppose (saves cost on clearly bad trades, small sample but large effect)
3. **Pivot to different information source** — volume, time-of-day, macro regime
4. **Accept overall null** — M5 FX with candlestick-only data does not contain exploitable edge regardless of architecture or filtering
