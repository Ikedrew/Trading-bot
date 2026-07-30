# V8.2 — Forward Validation Audit Results

**Date:** 2026-07-27
**Method:** 70/30 in-sample/out-of-sample split on existing shadow data
**Finding:** 5/6 instruments remain forward-positive, but EURUSD DEGRADES. Significant EV decay across all FX trend pairs.

---

## CRITICAL: This Is NOT True Forward Validation

No new data has been generated since discovery. This analysis uses the LAST 30% of existing data as a proxy. True forward validation requires the bot to RUN and COLLECT new observations.

---

## Section 2: Instrument Performance (70/30 Split)

| Symbol | Policy | Discovery EV | **Forward EV** | Forward WR | Forward n | Forward CI |
|---|---|---|---|---|---|---|
| **US500** | Trend | +0.095R | **+0.339R** | **71.4%** | 28 | **[+0.060, +0.619]** |
| USDJPY | Trend | +0.498R | +0.125R | 66.7% | 114 | [-0.066, +0.315] |
| NZDUSD | Trend | +0.553R | +0.091R | 56.8% | 243 | **[+0.040, +0.143]** |
| AUDUSD | Trend | +0.481R | +0.069R | 54.4% | 193 | [-0.003, +0.141] |
| NAS100 | Trend | +0.192R | +0.036R | 53.8% | 26 | [-0.339, +0.411] |
| **EURUSD** | Reversion | +0.877R | **-0.033R** | 44.7% | 365 | [-0.087, +0.021] |

---

## Section 3: Stability Assessment

| Symbol | Discovery → Forward | Delta | Assessment |
|---|---|---|---|
| **US500** | +0.095 → +0.339 | **+0.245** | **IMPROVED** |
| NAS100 | +0.192 → +0.036 | -0.156 | WEAKENED |
| USDJPY | +0.498 → +0.125 | -0.373 | WEAKENED |
| AUDUSD | +0.481 → +0.069 | -0.412 | WEAKENED |
| NZDUSD | +0.553 → +0.091 | -0.462 | WEAKENED |
| **EURUSD** | +0.877 → -0.033 | **-0.910** | **DEGRADED** |

### Pattern:
- **US500 is the ONLY instrument that improves in forward data**
- All FX trend pairs (USDJPY, AUDUSD, NZDUSD) show massive discovery→forward decay
- EURUSD goes NEGATIVE in forward period
- NAS100 weakens but remains positive

---

## Section 4: What The Data Actually Shows

### EURUSD — DEGRADED (Policy No Longer Valid)
```
Discovery: n=850 | WR=50.9% | EV=+0.877R | Max losing streak: 370!
Forward:   n=365 | WR=44.7% | EV=-0.033R | Max losing streak: 17
```
The discovery period shows an anomalous +0.877R with a **370-bar max losing streak** — this indicates the discovery EV is driven by a small number of massive outlier wins surrounded by hundreds of losses. The forward period shows the TRUE behaviour: flat/negative.

### FX Trend Pairs — WEAKENED (Edge Decaying)
```
USDJPY:  Discovery +0.498R → Forward +0.125R (still positive, n=114)
AUDUSD:  Discovery +0.481R → Forward +0.069R (barely positive, n=193)
NZDUSD:  Discovery +0.553R → Forward +0.091R (positive, n=243, CI>0)
```
All three remain forward-positive but at 15-25% of discovery EV. The "trend-following" edge exists but is MUCH weaker in recent data.

### Indices — STRONGEST IN FORWARD
```
US500:   Discovery +0.095R → Forward +0.339R (IMPROVING, n=28, CI>0)
NAS100:  Discovery +0.192R → Forward +0.036R (weakened but positive, n=26)
```
US500 is the standout — forward performance EXCEEDS discovery. But n=26-28 is very small.

---

## Section 5: Cost-Adjusted Viability

| Symbol | Gross EV (all) | Cost | Net EV | Viable? |
|---|---|---|---|---|
| EURUSD | +0.603R | 0.20R | +0.403R | YES (but FORWARD is -0.03R → **NO**) |
| NZDUSD | +0.414R | 0.20R | +0.214R | YES (forward +0.091 - 0.20 = **NO**) |
| USDJPY | +0.386R | 0.20R | +0.186R | YES (forward +0.125 - 0.20 = **NO**) |
| AUDUSD | +0.357R | 0.20R | +0.157R | YES (forward +0.069 - 0.20 = **NO**) |
| US500 | +0.168R | 0.08R | +0.088R | YES (forward +0.339 - 0.08 = **YES**) |
| NAS100 | +0.144R | 0.10R | +0.044R | YES (forward +0.036 - 0.10 = **NO**) |

### The uncomfortable truth:

**When using FORWARD data (recent 30%), only US500 survives after costs.**

All FX trend pairs show forward EV below their 20% cost threshold. The combined "all data" numbers look good because discovery-period outliers inflate the average.

---

## Section 6: Policy Validation

| Symbol | Policy Still Correct? | Evidence |
|---|---|---|
| US500 | **YES** | Forward EV=+0.339R, WR=71.4%, CI excludes zero |
| USDJPY | YES (marginal) | Forward EV=+0.125R, WR=66.7%, but net<0 after costs |
| NZDUSD | YES (marginal) | Forward EV=+0.091R, CI>0, but net<0 after costs |
| AUDUSD | YES (marginal) | Forward EV=+0.069R, borderline |
| NAS100 | INSUFFICIENT DATA | Forward EV=+0.036R, n=26 too small |
| **EURUSD** | **NO** | Forward EV=-0.033R — policy has FAILED |

---

## Section 7: Invalidation Thresholds

```
SUSPEND instrument if ANY of:
  1. Rolling-50 EV < 0 for 2 consecutive measurements
  2. Rolling-50 WR < 40% (trend) or < 35% (reversion)
  3. 15+ consecutive losses
  4. Drawdown exceeds 12R from equity peak

HALT ALL trading if:
  1. Portfolio rolling-100 EV < 0
  2. Combined max DD exceeds 15R
  3. 3+ instruments simultaneously suspended
```

**EURUSD would currently trigger Rule 1 (forward EV < 0).**

---

## Section 8: Confidence Assessment

| Symbol | Policy | Forward EV | Stable? | Net+? | **Confidence** |
|---|---|---|---|---|---|
| **US500** | Trend | +0.339R | IMPROVED | YES | **MEDIUM** |
| NZDUSD | Trend | +0.091R | WEAKENED | NO (net) | LOW |
| USDJPY | Trend | +0.125R | WEAKENED | NO (net) | LOW |
| AUDUSD | Trend | +0.069R | WEAKENED | NO (net) | VERY LOW |
| NAS100 | Trend | +0.036R | WEAKENED | NO (net) | VERY LOW |
| EURUSD | Reversion | -0.033R | DEGRADED | NO | **NONE** |

---

## FINAL REPORT

### A) Instruments That Remain Validated:
- **US500** — only instrument where forward exceeds discovery, net positive after costs

### B) Instruments Requiring More Forward Data:
- NAS100 (n=26 forward, too small, positive but inconclusive)
- USDJPY (forward positive but below cost threshold)
- NZDUSD (forward positive, CI>0, but net<0 at 20% cost)
- AUDUSD (forward barely positive, near zero)

### C) Instruments Showing Degradation:
- **EURUSD** — forward EV negative, policy FAILED
- AUDUSD — forward EV approaches zero

### D) Implementation Recommendation:

```
→ CONTINUE SHADOW VALIDATION

Evidence:
- Only 1/6 instruments (US500) fully survives forward + costs
- EURUSD reversion policy has FAILED in forward period
- FX trend pairs remain gross-positive but net-negative after costs
- Discovery-period EV was inflated by outliers (EURUSD: 370 losing streak!)
- No true forward data exists (this is all in-sample split)

BEFORE PAPER TRADING:
1. Collect 200+ NEW observations (post-discovery)
2. US500 remains primary candidate (IMPROVING forward)
3. EURUSD reversion should be SUSPENDED pending new data
4. FX trend pairs viable only at wider stops (>15 pip) or lower-cost execution
5. NAS100 needs more data (n=26 forward is inconclusive)
```

---

## The Honest Assessment

The V8.1 universe expansion looked promising (+0.20-0.40R net across many pairs). But the forward split reveals **most of that EV was concentrated in the FIRST 70% of data** (discovery period). The recent 30% shows:

- FX trend pairs: +0.07-0.13R gross (below 20% cost threshold)
- EURUSD: negative (reversion has stopped working)
- US500: the sole bright spot (improving)

**The V7.5 finding (NAS100+US500 trend-following) remains the most defensible position.** The FX expansion was premature — gross edge exists but doesn't survive M5 costs in recent data.
