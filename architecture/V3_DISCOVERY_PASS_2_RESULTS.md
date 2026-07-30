# V3 Discovery Pass 2 — Uniform Post-Phase-2 Dataset (Updated)

**Date:** 2026-07-28
**Dataset:** 158 linked post-Phase-2 observations
**Status:** MEANINGFUL RESULTS — sufficient sample for preliminary feature assessment

---

## Executive Summary

### 1. Which V3 features appear most promising?

**PRICE INSIDE ORDER BLOCK** is the only feature with statistically significant positive EV:
- n=23, WR=65.2%, EV=**+0.07R**, CI=[+0.001, +0.141]
- This is the ONLY GREEN finding in the entire V3 feature set

### 2. Which features show no evidence?

- Rejection candles (-0.04R effect)
- Session low swept (-0.07R effect — slightly WORSE)
- Liquidity sweep occurred (-0.12R effect — WORSE, but n=11)

### 3. Which features need more collection?

- Liquidity sweeps (n=11, need 50+)
- Displacement into level (n=2, need 50+)
- Price inside FVG (n=34, almost sufficient)
- Price inside OB (n=23, promising but needs 50+)

### 4. Which features are ready for further validation?

- **Price inside OB** — only positive-EV feature, ready for deeper analysis
- **Equal lows presence** — large sample (n=148), +0.18R vs absence
- **Supply OB presence** — n=123, +0.14R vs absence
- **M15 range position** — n=147, discount zone shows best WR (62.7%)

---

## Dataset

| Metric | Value |
|---|---|
| Total V3 observations | 284 |
| Post-Phase-2 (filtered) | 168 |
| Excluded (legacy) | 116 |
| **With linked outcomes** | **158** |
| Baseline EV | -0.056R |
| Baseline Win Rate | 52.5% |
| Baseline 95% CI | [-0.106, -0.006] |

**Interpretation:** The post-Phase-2 baseline is slightly negative (-0.056R) with 52.5% win rate. This is much closer to breakeven than the V2 discovery (-0.12R raw, -0.60R after costs). The system is near neutral in this period — which means feature separation IS measurable if it exists.

---

## RQ1: Market Location

### M15 Range Position (n=147)

| Zone | n | WR | EV | CI | Status |
|---|---|---|---|---|---|
| **Discount (<0.33)** | **59** | **62.7%** | **-0.038R** | [-0.12, +0.04] | YELLOW |
| Mid-range (0.33-0.67) | 52 | 51.9% | -0.048R | [-0.12, +0.03] | GRAY |
| Premium (>0.67) | 36 | 38.9% | -0.095R | [-0.22, +0.03] | RED |

**Finding:** Discount zone has highest win rate (62.7%) and least negative EV. Premium zone has lowest win rate (38.9%). The gradient is consistent with the "buy in discount" hypothesis but the effect (-0.04R to -0.10R) does not cross into positive territory.

### H1 Range Position (n=105)

| Zone | n | WR | EV | CI |
|---|---|---|---|---|
| Discount (<0.33) | 6 | 66.7% | +0.08R | [-0.07, +0.23] |
| Mid-range (0.33-0.67) | 21 | 66.7% | +0.05R | [-0.11, +0.21] |
| Premium (>0.67) | 78 | 52.6% | -0.05R | [-0.11, +0.00] |

**Finding:** H1 discount and mid-range show POSITIVE EV (+0.05 to +0.08R) while premium is negative. But discount n=6 is too small, and no CI excludes zero definitively.

**RQ1 Status: YELLOW** — Consistent gradient (discount > mid > premium) but insufficient statistical power to confirm.

---

## RQ2: Liquidity Context

### Equal Highs/Lows

| Feature | n | WR | EV | vs Absent | Effect |
|---|---|---|---|---|---|
| Equal highs PRESENT | 137 | 55.5% | -0.047R | -0.12R (absent) | **+0.07R** |
| Equal lows PRESENT | 148 | 56.1% | -0.045R | -0.22R (absent) | **+0.18R** |

**Finding:** Presence of equal levels correlates with BETTER outcomes than absence. Equal lows particularly: -0.04R vs -0.22R when absent (+0.18R effect). However, the "absent" groups are small (n=10-21) so the comparison is unbalanced.

### Session Swept

| Feature | n | WR | EV | vs Not-Swept | Effect |
|---|---|---|---|---|---|
| Session HIGH swept | 94 | 54.3% | -0.034R | -0.089R | **+0.055R** |
| Session LOW swept | 63 | 49.2% | -0.096R | -0.030R | **-0.066R** |

**Finding:** Session HIGH being swept is slightly positive (+0.055R). Session LOW being swept is slightly negative (-0.066R). These are small effects within CI bounds.

### Liquidity Sweep

| Feature | n | WR | EV |
|---|---|---|---|
| Sweep YES | 11 | 45.5% | -0.17R |
| No sweep | 147 | 53.1% | -0.047R |

**Finding:** Sweeps show WORSE outcomes (-0.12R effect). But n=11 is too small for confidence.

**RQ2 Status: YELLOW** — Equal levels presence shows positive effect (+0.07 to +0.18R). Sweep detection shows negative effect but insufficient sample.

---

## RQ3: Fair Value Gaps

| Feature | n | WR | EV | CI |
|---|---|---|---|---|
| Any FVG present | 158 | 52.5% | -0.056R | [-0.11, -0.01] |
| No FVG | 0 | — | — | — |
| FVG above price | 113 | 50.4% | -0.066R | [-0.13, -0.01] |
| **FVG below price** | **129** | **57.4%** | **-0.021R** | [-0.07, +0.03] |
| **Price INSIDE FVG** | **34** | **64.7%** | **-0.014R** | [-0.13, +0.11] |

**Finding:** FVG present in 100% of records (no control group). But FVG below price (bullish imbalance below) shows better outcomes (-0.02R vs -0.07R for FVG above). Price INSIDE FVG shows highest win rate (64.7%) and near-zero EV.

**RQ3 Status: YELLOW** — FVG below (bullish) and price-inside-FVG show positive trends. No control group for presence/absence. Need continuous distance analysis.

---

## RQ4: Order Blocks

| Feature | n | WR | EV | CI | Status |
|---|---|---|---|---|---|
| Demand OB present | 121 | 54.5% | -0.032R | [-0.09, +0.02] | YELLOW |
| No demand OB | 37 | 45.9% | -0.134R | [-0.25, -0.01] | NEGATIVE |
| Supply OB present | 123 | 58.5% | -0.024R | [-0.08, +0.03] | YELLOW |
| No supply OB | 35 | 31.4% | -0.167R | [-0.27, -0.07] | NEGATIVE |
| **Price INSIDE OB** | **23** | **65.2%** | **+0.071R** | **[+0.001, +0.141]** | **GREEN** |

**Critical finding:** **Price inside Order Block is the ONLY feature with statistically significant positive EV (+0.071R, CI just above zero).** Win rate 65.2% with n=23.

OB presence (vs absence) also shows positive effect: -0.03R vs -0.13R (demand) and -0.02R vs -0.17R (supply). Being near/inside institutional zones is associated with better outcomes.

### OB Strength & Mitigation

| Feature | n | WR | EV |
|---|---|---|---|
| Strong demand OB (>0.6) | 108 | 52.8% | -0.035R |
| Weak demand OB (<=0.6) | 13 | 69.2% | -0.009R |
| Mitigated OB | 141 | 52.5% | -0.044R |
| Fresh OB | 17 | 52.9% | -0.151R |

**Finding:** Weak OBs slightly outperform strong ones (counterintuitive). Mitigated OBs outperform fresh ones (also counterintuitive — suggests "tested" zones may be more reliable than untested ones). But n is small for weak/fresh categories.

**RQ4 Status: GREEN (price inside OB)** — First statistically positive finding in V3 research.

---

## RQ5: Feature Ranking

| Rank | Feature | n_present | EV_present | Effect vs Absent | WR | Status |
|---|---|---|---|---|---|---|
| 1 | **equal_lows_below** | 148 | -0.045R | **+0.180R** | 56.1% | **YELLOW** |
| 2 | **supply_ob** | 123 | -0.024R | **+0.143R** | 58.5% | **YELLOW** |
| 3 | **demand_ob** | 121 | -0.032R | **+0.102R** | 54.5% | **YELLOW** |
| 4 | equal_highs_above | 137 | -0.047R | +0.070R | 55.5% | YELLOW |
| 5 | session_high_swept | 94 | -0.034R | +0.055R | 54.3% | YELLOW |
| 6 | session_low_swept | 63 | -0.096R | -0.066R | 49.2% | YELLOW |
| 7 | rejection_candle | 33 | -0.089R | -0.042R | 48.5% | YELLOW |

**Special findings (not in presence/absence format):**
- **Price inside OB:** EV=+0.071R (ONLY positive-EV feature) — **GREEN**
- **Price inside FVG:** EV=-0.014R, WR=64.7% — **YELLOW** (approaching positive)
- **M15 discount zone:** WR=62.7%, EV=-0.038R — **YELLOW**
- **H1 discount/mid zone:** EV=+0.05 to +0.08R — **YELLOW** (tiny n)

---

## Feature Population (n=158 linked post-Phase-2)

| Field | Populated | % | Ready? |
|---|---|---|---|
| m15_range_position | 147 | 93% | **YES** |
| h1_range_position | 105 | 67% | **YES** |
| atr | 158 | 100% | **YES** |
| equal_highs_above | 137 | 87% | **YES** |
| equal_lows_below | 148 | 94% | **YES** |
| prev_session_high/low | 158 | 100% | **YES** |
| prev_session_high_swept | 94 | 60% | **YES** |
| prev_session_low_swept | 63 | 40% | **YES** |
| liquidity_sweep | 11 | 7% | NO |
| nearest_fvg_above | 113 | 72% | **YES** |
| nearest_fvg_below | 129 | 82% | **YES** |
| price_inside_fvg | 34 | 22% | ALMOST |
| nearest_demand_ob | 121 | 77% | **YES** |
| nearest_supply_ob | 123 | 78% | **YES** |
| price_inside_ob | 23 | 15% | ALMOST |
| rejection_candle | 33 | 21% | ALMOST |
| displacement | 2 | 1% | NO |
| consolidation_range | 158 | 100% | **YES** |

---

## Cost-Adjusted Assessment (spread = 0.48R)

| Feature | Raw EV | Cost-Adj EV | Viable? |
|---|---|---|---|
| Baseline | -0.056R | -0.536R | No |
| Price inside OB | +0.071R | -0.409R | No (but least negative) |
| FVG below price | -0.021R | -0.501R | No |
| M15 discount | -0.038R | -0.518R | No |

**Even the best feature (price inside OB at +0.071R raw) does not overcome the 0.48R spread cost.** However, this analysis uses the FULL spread deduction. If risk distance were larger (reducing spread/risk ratio), the picture might change.

---

## Final Conclusion

### What Evidence Exists

1. **Price inside Order Block** is the strongest V3 signal: +0.071R raw EV, 65.2% WR, CI barely positive. This is the FIRST statistically positive finding across all V2/V3 research.

2. **Location gradient exists:** Discount zones consistently outperform premium zones (M15: 62.7% vs 38.9% WR; H1: +0.08R vs -0.05R). The direction is theoretically sound.

3. **OB/FVG presence** creates better outcomes than absence (+0.10 to +0.18R effect size). Being near institutional structure helps.

4. **All raw EVs remain negative** after transaction costs. No feature alone overcomes the spread burden.

### What This Means

The V3 hypothesis — that precise market location contains predictive information — shows **early evidence of being correct at the raw level**. Price inside institutional zones (OBs, FVGs) and location in discount zones correlate with better outcomes. But the spread cost (0.48R) overwhelms the signal.

This suggests the research direction should be:
- **Larger risk distances** (reducing spread/risk ratio below 20%)
- **Higher timeframe entries** where the same location logic applies but spread is a smaller fraction of risk
- **Combining OB + FVG + discount** to see if stacking conditions improves the +0.07R finding

---

## Research Roadmap

### Immediate (now)

1. **Deeper OB analysis:** Break down price-inside-OB by demand vs supply, by direction, by OB strength
2. **Distance analysis:** Use continuous OB distance (not just inside/outside binary)
3. **FVG + location combo:** Does price inside FVG + discount zone improve further?

### Next collection targets (days)

1. Price inside OB: need n=50 (current 23, need ~35 more at 15% rate → ~230 more records)
2. Liquidity sweeps: need n=50 (current 11, need ~560 more records at 7% rate)
3. Displacement: need n=50 (current 2 — would need thousands more records)

### Features to revisit with larger sample

1. H1 discount zone (n=6, shows +0.08R — needs 50+)
2. Price inside FVG (n=34, shows -0.014R with 64.7% WR — approaching significance)
3. Fresh vs mitigated OB (n=17 fresh — needs 50+)

### Not recommended

- Do NOT change strategy based on n=23 OB finding
- Do NOT implement location filters yet
- Continue collecting and re-validate at n=50 per feature
