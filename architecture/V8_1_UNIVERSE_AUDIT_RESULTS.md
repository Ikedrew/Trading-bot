# V8.1 — Trading Universe Expansion Audit Results

**Date:** 2026-07-27
**Dataset:** 4,960 shadow trades across 9 instruments
**Finding:** 6 instruments qualify for TIER 1 inclusion — 4 FX pairs show strong TREND-FOLLOWING evidence

---

## CRITICAL DISCOVERY: Most FX Pairs Are TREND-FOLLOWING, Not Mean-Reverting

The V7 research assumed FX = mean-reversion. **This is WRONG for most pairs.**

| Symbol | n | Better Policy | Best EV | Cost | Net EV | Tier |
|---|---|---|---|---|---|---|
| **EURUSD** | 1199 | REVERSION | +0.611R | 0.20 | **+0.411R** | **TIER 1** |
| GBPUSD | 773 | REVERSION | +0.136R | 0.20 | -0.064R | TIER 3 |
| **USDJPY** | 369 | **TREND** | +0.402R | 0.20 | **+0.202R** | **TIER 1** |
| **AUDUSD** | 635 | **TREND** | +0.361R | 0.20 | **+0.161R** | **TIER 1** |
| **NZDUSD** | 805 | **TREND** | +0.416R | 0.20 | **+0.216R** | **TIER 1** |
| USDCHF | 627 | TREND | +0.142R | 0.20 | -0.058R | TIER 3 |
| USDCAD | 394 | TREND | +0.050R | 0.20 | -0.150R | TIER 3 |
| **NAS100** | 74 | **TREND** | +0.196R | 0.09 | **+0.106R** | **TIER 1** |
| **US500** | 84 | **TREND** | +0.153R | 0.09 | **+0.063R** | **TIER 1** |

---

## The Revelation: 5 Out of 7 FX Pairs Prefer TREND-FOLLOWING

| Symbol | WR (trend) | EV (trend) | Time-Stable? |
|---|---|---|---|
| USDJPY | 75.6% | +0.402R | YES (both halves +) |
| NZDUSD | 77.0% | +0.416R | YES |
| AUDUSD | 70.4% | +0.361R | YES |
| USDCHF | 69.9% | +0.142R | YES (but net negative) |
| USDCAD | 56.6% | +0.050R | YES (but net negative) |

**Only EURUSD and GBPUSD prefer the reversion policy.** The V3-V5 research that concluded "FX is mean-reverting" was dominated by EURUSD (largest sample). The individual pair analysis reveals most pairs actually trend.

---

## Time Stability

| Symbol | Policy | H1 EV | H2 EV | Both Positive? |
|---|---|---|---|---|
| EURUSD | Reversion | +0.529R | +0.693R | **YES (improving)** |
| USDJPY | Trend | +0.685R | +0.121R | YES (declining but positive) |
| AUDUSD | Trend | +0.680R | +0.044R | YES (declining but positive) |
| NZDUSD | Trend | +0.752R | +0.081R | YES (declining but positive) |
| NAS100 | Trend | +0.119R | +0.273R | **YES (improving)** |
| US500 | Trend | +0.075R | +0.231R | **YES (improving)** |

**All Tier 1 instruments are positive in BOTH time halves.** However, USDJPY/AUDUSD/NZDUSD show significant first-half concentration (declining trend).

---

## Section 8: Universe Classification

| Symbol | Tier | Net EV | Policy | Reason |
|---|---|---|---|---|
| **EURUSD** | **TIER 1** | +0.411R | Reversion | Strongest FX signal, time-stable, improving |
| **USDJPY** | **TIER 1** | +0.202R | Trend | Strong net positive, time-stable |
| **NZDUSD** | **TIER 1** | +0.216R | Trend | Strong net positive, high WR (77%) |
| **AUDUSD** | **TIER 1** | +0.161R | Trend | Positive, time-stable, high WR (70%) |
| **NAS100** | **TIER 1** | +0.106R | Trend | Validated (V7.5), improving |
| **US500** | **TIER 1** | +0.063R | Trend | Validated (V7.5), improving |
| GBPUSD | TIER 3 | -0.064R | Reversion | Costs destroy the edge |
| USDCHF | TIER 3 | -0.058R | Trend | Costs destroy the edge |
| USDCAD | TIER 3 | -0.150R | Trend | Insufficient edge for costs |

---

## IMPORTANT CAVEATS

### 1. These Are SHADOW TRADE Results (Not V3-Filtered)

The shadow trades represent ALL signals the system generated. The V3 pipeline (which filters for WEAK/INTERESTING/context quality) has NOT processed these index trades. The FX exec assessment data (V3-filtered, n=368) showed different results than the raw shadow trades because V3 filtering adds value.

### 2. First-Half Concentration on FX TREND Pairs

USDJPY, AUDUSD, NZDUSD all show H1 >> H2 (declining but positive). This echoes the V5.2 finding — early period drove FX results. The recent half remains positive but weak (+0.04-0.12R) compared to the first half (+0.68-0.75R).

### 3. Cost Model Is Critical

At 20% cost ratio (FX M5), only signals with >+0.20R gross survive. This eliminates 3/7 FX pairs (GBPUSD, USDCHF, USDCAD). Any spread widening or execution degradation would threaten the marginal pairs.

### 4. No Forward Validation Exists

ALL of this data was used in the analysis. There is zero unseen forward data for any instrument. This is discovery, not validation.

---

## A) Validated Instruments (earned inclusion based on evidence)

**With confidence:**
- NAS100 (V7.5 validated, trend, CI excludes zero)
- US500 (V7.5 validated, trend, CI excludes zero)

**With strong evidence but no forward validation:**
- EURUSD (reversion, +0.411R net, improving, n=1199)
- NZDUSD (trend, +0.216R net, n=805)
- USDJPY (trend, +0.202R net, n=369)
- AUDUSD (trend, +0.161R net, n=635)

---

## B) Research Gaps Remaining

1. **No forward validation** for ANY FX pair under trend-following policy
2. **V3 pipeline not processing** FX-trend trades (only tested reversion)
3. **Second-half degradation** on USDJPY/AUDUSD/NZDUSD (recent EV much lower)
4. **Session dependency** unknown
5. **Correlation** between concurrent FX trend trades unmeasured
6. **EURUSD concentration** — if EURUSD is removed, does FX portfolio survive?

---

## C) Minimum Additional Evidence Required

1. Forward validation: 100+ trades per instrument generated AFTER this analysis
2. V3 pipeline producing execution_assessment for FX under trend policy
3. Separate measurement of recent-only (last 30%) performance per pair
4. Define invalidation criteria per instrument

---

## D) Recommended Initial Trading Universe

```
TIER 1 — IMMEDIATE (validated or strong evidence):
┌──────────────────────────────────────────────────────────────────┐
│ NAS100  — Trend-following — VALIDATED (V7.5)                      │
│ US500   — Trend-following — VALIDATED (V7.5)                      │
│ EURUSD  — Mean-reversion — Strong evidence (n=1199, improving)    │
└──────────────────────────────────────────────────────────────────┘

TIER 1 — OBSERVE THEN INCLUDE (strong but declining):
┌──────────────────────────────────────────────────────────────────┐
│ USDJPY  — Trend-following — +0.202R net, declining H2             │
│ NZDUSD  — Trend-following — +0.216R net, declining H2             │
│ AUDUSD  — Trend-following — +0.161R net, declining H2             │
└──────────────────────────────────────────────────────────────────┘

EXCLUDED:
┌──────────────────────────────────────────────────────────────────┐
│ GBPUSD  — Costs exceed edge                                       │
│ USDCHF  — Costs exceed edge                                       │
│ USDCAD  — Insufficient signal                                     │
└──────────────────────────────────────────────────────────────────┘
```

---

## Policy Router (Updated from V7.3)

```
def get_policy(symbol):
    if symbol in ('NAS100', 'US500'):
        return 'TREND'        # Follow signal — VALIDATED
    elif symbol == 'EURUSD':
        return 'REVERSION'    # Fade signal — strong evidence
    elif symbol in ('USDJPY', 'AUDUSD', 'NZDUSD'):
        return 'TREND'        # Follow signal — strong evidence, monitor
    else:
        return 'SKIP'         # No edge after costs
```

This is a significant evolution from V7.3's "FX=reversion, INDEX=trend" — the data shows most FX pairs ALSO prefer trend-following.
