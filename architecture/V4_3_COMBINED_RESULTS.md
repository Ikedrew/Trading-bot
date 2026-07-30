# V4.3 — Currency Strength + V3 Timing + Geometry Results

**Date:** 2026-07-29
**Dataset:** 291 V3 execution assessments with cross-pair currency context
**Verdict:** B) Currency strength improves filtering — the strongest combined signal yet, but still net negative

---

## Executive Summary

The combination of V3 timing (WEAK) + quality (INTERESTING) + currency strength (3+ agree) produces the **highest raw EV found in any combined configuration: +0.105R (n=25)**.

At INTRADAY geometry (12% cost), net EV is **-0.015R** — the closest to breakeven the research has achieved.

| Configuration | n | WR | Raw EV | Net @INTRA |
|---|---|---|---|---|
| All V3 baseline | 291 | 47.4% | -0.016R | -0.136R |
| WEAK only | 166 | 49.4% | +0.022R | -0.098R |
| WEAK + ALIGNED | 62 | 48.4% | +0.045R | -0.075R |
| WEAK + INTERESTING + ALIGNED | 55 | 49.1% | +0.062R | -0.058R |
| **WEAK + INTERESTING + 3+ AGREE** | **25** | **44.0%** | **+0.105R** | **-0.015R** |

---

## Key Findings

### 1. Currency Strength IS Additive to V3

Each layer adds measurable value:
- V3 baseline: -0.016R
- + WEAK timing: +0.022R (improvement: +0.038R)
- + INTERESTING quality: +0.039R (+0.017R)
- + USD aligned: +0.045R (+0.006R)
- + 3+ agreement: +0.105R (+0.060R)

**Total improvement from baseline to best: +0.121R.** The layers are partially additive.

### 2. The "3+ Agree" Filter Is The Strongest Single Addition

From WEAK+INTERESTING:
- Without strength filter: +0.039R (n=140)
- With 3+ agreement: +0.105R (n=25)
- Improvement: **+0.066R** from a single filter

### 3. Best Configuration Almost Breaks Even

```
WEAK + INTERESTING + 3+ AGREE:
    Raw EV: +0.105R
    Cost @INTRA (10p): -0.120R
    Net: -0.015R
    
    Cost @WIDE (15p): -0.080R
    Net: +0.025R ← POSITIVE at 15-pip stops
    
    Cost @STRUCTURE (20p): -0.060R
    Net: +0.045R ← POSITIVE at 20-pip stops
```

**At 15+ pip stops, this configuration is NET POSITIVE.** The first viable combination in the research program.

### 4. Filter Behaviour — Primarily Removes Losers

Opposing trades removed: 177 (61% of total)
- 47% of removed were winners
- 53% of removed were losers
- Average R of removed: -0.037R

The filter removes more losers than winners — it's doing useful work.

### 5. Time Stability — VARIABLE

| Period | WR | EV |
|---|---|---|
| First half (aligned) | 42.1% | +0.049R |
| Second half (aligned) | 54.4% | -0.016R |

The effect is NOT stable across time periods. This is a significant concern.

---

## Analysis 2: Timing Interaction — Surprising Finding

| Entry State | Alignment Effect |
|---|---|
| WEAK | +0.036R (aligned better) |
| VALID | -0.018R (aligned WORSE — inverted!) |
| NO_ENTRY | **+0.118R** (strongest alignment effect) |

**The NO_ENTRY group shows the STRONGEST currency alignment effect (+0.118R).** This suggests that when V3 entry confirmation is absent but USD direction agrees, the opportunity still has value. This challenges the V3 assumption that entry confirmation is needed.

---

## V4.3 Verdict

### B) Currency strength improves filtering — the closest to viability yet

**Evidence:**
- +0.121R total improvement (baseline to best combo)
- +0.066R from 3+ agreement filter alone
- Net positive at 15-20 pip stops (+0.025 to +0.045R)
- Filter removes more losers than winners
- Effect is additive to V3 timing/quality

**Concerns:**
- n=25 for best configuration (statistically underpowered)
- Time stability is VARIABLE (first half ≠ second half)
- WR of best config (44%) is LOWER than baseline (47.4%) — edge from rare large wins
- 4+ agreement (n=16) shows +0.105R but tiny sample

---

## Critical Threshold Analysis

| Stop Distance | Cost/R | Net EV (WEAK+INT+3agree) | Viable? |
|---|---|---|---|
| 10 pips | 12.0% | -0.015R | NO (barely) |
| **15 pips** | 8.0% | **+0.025R** | **YES** |
| **20 pips** | 6.0% | **+0.045R** | **YES** |
| 30 pips | 4.0% | +0.065R | YES |

**At 15+ pip stops, the combined V3 + currency strength signal appears viable.** But:
- n=25 is too small for confidence
- Need 50+ at this configuration for statistical validation
- The AR9 finding showed raw EV collapses at wider stops (from R-scaling)

---

## Recommended V4.4

**"Is the +0.105R raw EV at WEAK + INTERESTING + 3+ AGREE stable at 15-20 pip stop geometries when re-simulated on progression data?"**

This combines:
- The V4.3 best filter (3+ agree)
- The AR4/AR9 re-simulation methodology
- Explicit 15-20 pip stop distances

If the +0.105R survives re-simulation at those distances → first validated viable configuration.
If it collapses (as AR9 showed for unfiltered trades) → the currency filter didn't change the fundamental problem.
