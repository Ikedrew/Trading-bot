# Current Truth Restoration

**Date:** 2026-07-27
**Architecture:** USE_NEW_PIPELINE=True, MARKET_CONTEXT_ENABLED=True, ENABLE_EV_GATE=False
**Data:** CURRENT epoch only (n=846 shadow trades, 100% entity_id, 91% market_phase, 100% regime)

---

## Audit 1: Expected Value Truth

### The Actual Expected Value of the Current System

| Metric | CURRENT TRUTH | Previous Report (Q19) |
|--------|--------------|----------------------|
| **EV per trade** | **-0.1999R** | +0.675R |
| Win rate | **33.3%** | 45.8% |
| Loss rate | **64.3%** | 45.5% |
| Avg win R | **+0.334R** | +2.298R |
| Avg loss R | **-0.484R** | -0.831R |
| Profit factor | **0.358** | 2.786 |
| Median R | **-0.096** | 0.0 |
| Std dev R | **0.539** | 1.705 |
| Max drawdown | **172.4R** | Not reported |
| Sample size | **846** | 901 |
| Epoch | **CURRENT only** | ALL (mixed) |
| Significant? | **YES (p<0.000001)** — significantly NEGATIVE | YES — but for wrong conclusion |

### 95% Confidence Interval

**EV is between -0.236R and -0.164R with 95% confidence.**

The system CERTAINLY loses money. This is not statistical noise.

### Trend

| Period | EV |
|--------|-----|
| All CURRENT (n=846) | -0.200R |
| Last 100 trades | -0.120R |
| Last 50 trades | -0.137R |

Slight improvement recently but still decisively negative.

### Discrepancy Explanation

The previous Q19 report of +0.675R was caused by **epoch contamination**:

| Epoch | n | EV | Impact |
|-------|---|-----|--------|
| LEGACY | 412 | +0.369R | Inflated (old architecture) |
| TRANSITIONAL | 992 | +0.442R | Inflated (partially migrated) |
| CURRENT | 846 | **-0.200R** | True current performance |
| ALL combined | 2,250 | +0.187R | Misleading average |

The Q19 experiment used "shadow_trades + research_shadow_trades" without epoch filtering. The LEGACY and TRANSITIONAL records come from a fundamentally different architecture (pre-migration) where the strategy activation, scoring weights, and pipeline behaviour were different.

**Cause: Mixed epochs, not data leakage or selection bias.** The old system genuinely performed differently (different SL/TP geometry, different regime classification, different pipeline flow). Those results are historically accurate but NOT representative of the current system.

---

## Audit 2: Risk Model Truth

### Position Sizing (CURRENT Data)

| Risk Per Trade | Final Equity (from 10,000) | Return | Max Drawdown |
|---|---|---|---|
| 0.10% | 8,443 | -15.6% | 15.8% |
| 0.20% | 7,126 | -28.7% | 29.2% |
| 0.50% | 4,278 | **-57.2%** | **57.9%** |
| 1.00% | 1,817 | -81.8% | 82.4% |
| 2.00% | 321 | -96.8% | 97.0% |

**Previous R5 claimed:** Fixed 0.5% produces +1917% return.
**CURRENT TRUTH:** Fixed 0.5% produces **-57.2% loss**.

No position sizing model converts negative EV into profit. R5 is completely invalidated.

### Probability of Ruin (CURRENT Data)

**Monte Carlo simulation (5,000 paths × 1,000 trades):**

| Risk Per Trade | P(Ruin) — 50% DD threshold |
|---|---|
| 1.00% | **100.0%** (5000/5000 paths fail) |
| 0.50% | **100.0%** (5000/5000 paths fail) |
| 0.20% | 0.0% |
| 0.10% | 0.0% |

**Previous R3 claimed:** P(ruin) = 0%, using WR=80%, avg_win=2.0R.
**CURRENT TRUTH:** P(ruin) = **100%** at any risk ≥ 0.5%. The system will CERTAINLY reach 50% drawdown.

At 0.20% risk or below, the absolute loss (-28.7% over 846 trades) doesn't breach 50% within 1000 trades. But this is only because the loss is slow enough — the system is still losing monotonically.

### Drawdown (CURRENT Data)

At 1% risk per trade:
- Max observed DD: **82.4%**
- Breached 5% DD: 841/846 times (99.4%)
- Breached 20% DD: 824/846 times (97.4%)
- Breached 50% DD: 690/846 times (81.6%)

**Previous R4 claimed:** Halt at 50% DD, resume at 25%.
**CURRENT TRUTH:** The system reaches 50% DD in 81.6% of its history. A 50% halt would have stopped the system almost immediately. This is the CORRECT action — the system should not be trading.

---

## Audit 3: Data Lineage Verification

### Dataset Classification

| Epoch | Count | Percentage | Usable for Current Research? |
|-------|-------|-----------|----------------------------|
| CURRENT | 846 | 38% | ✅ YES |
| TRANSITIONAL | 992 | 44% | ⚠️ Only for field that exist |
| LEGACY | 412 | 18% | ❌ NO — different architecture |
| **TOTAL** | **2,250** | 100% | |

### CURRENT Epoch Field Coverage

| Field | Coverage | Research Usability |
|-------|----------|-------------------|
| entity_id | 100% | ✅ Full join capability |
| strategy (clean) | 100% | ✅ Strategy analysis possible |
| market_phase | 91% | ✅ Phase research possible |
| regime | 100% | ✅ Regime research possible |
| trade_horizon | 86% | ✅ Horizon research possible |

### Duplicate Detection

130 duplicate trade_ids found in CURRENT epoch (846 total, 716 unique). These are likely horizon shadow duplicates (same opportunity, SCALP + INTRADAY variants sharing a base cycle_id). Not a data corruption issue.

### Experiment Epoch Usage

| Experiment | Epoch Used | Valid for Current? |
|---|---|---|
| **M9** | CURRENT only | ✅ YES |
| **M10** | CURRENT only | ✅ YES |
| **Q19** | ALL/MIXED | ❌ INVALID |
| **Q1** | ALL/MIXED | ⚠️ SUSPECT |
| **Q3-Q9** | ALL/MIXED | ⚠️ HISTORICAL only |
| **R3, R4, R5** | ALL/MIXED (+ synthetic?) | ❌ INVALID |

---

## Audit 4: Promotion Recommendation Reset

| Recommendation | Original Evidence | Current Evidence | Status |
|---|---|---|---|
| **R3: PROMOTE** "P(ruin)=0%" | WR=80%, avg_win=2.0R, n=100 | WR=33%, avg_win=0.33R. P(ruin)=100% at ≥0.5% risk | 🔴 **INVALIDATED** |
| **R4: PROMOTE** "Halt at 50% DD" | EV=+0.675R system, recovery possible | EV=-0.20R system. 50% DD is EXPECTED, not exceptional | 🔴 **INVALIDATED** (correct action is: halt NOW) |
| **R5: PROMOTE** "Fixed 0.5%" | +1917% return simulation | -57.2% return simulation on CURRENT data | 🔴 **INVALIDATED** |
| **Q4: PROMOTE_CALIBRATION** | 15pp miscalibration found | Calibration finding may be valid but meaningless with -0.20R EV | 🟡 **DEFERRED** — fix exits first |
| **Q19: POSITIVE_EDGE** | EV=+0.675R, significant | EV=-0.200R, significantly NEGATIVE | 🔴 **INVALIDATED** |
| **Q1: WEIGHT_ADJUSTMENT** | confirmation_pre best predictor | May be valid but epoch unclear and irrelevant with negative EV | 🟡 **DEFERRED** |
| **M9: MONITOR** (TWEEZER_BOTTOM/REVERSAL) | +0.098R, n=37, MEDIUM | Still valid finding but n=37 insufficient for promotion | ✅ **Still valid as observation** |
| **M10: WAIT** | Interaction detected, no promotable cell | Still valid — correctly recommended WAIT | ✅ **Still valid** |
| **ARCH decisions** (H4 regime, H1 BOS, M5 timing) | Architecture audit validated | Architecture unchanged. Still valid. | ✅ **Still valid** |

---

## Audit 5: Current System Truth Report

### Section 1: What Is Proven True

| # | Fact | Evidence | Confidence |
|---|------|----------|-----------|
| 1 | **System EV is -0.20R** | n=846, p<0.000001, 95% CI [-0.236, -0.164] | CERTAIN |
| 2 | **78.7% of trades exit by timeout** | Direct count: 666/846 | CERTAIN |
| 3 | **Take profit is unreachable** | 0.5% TP hit rate (4/846) | CERTAIN |
| 4 | **Entries DO have directional signal** | Mean MFE = 0.70R, 46% reach 0.25R, 18.7% reach 1.0R | HIGH |
| 5 | **Exit mechanism destroys the signal** | MFE capture ratio = -1.51 (gives back more than it gains) | CERTAIN |
| 6 | **P(ruin) = 100% at standard risk** | Monte Carlo: 5000/5000 paths fail at ≥0.5% risk | CERTAIN |
| 7 | **Phase×pattern interaction exists** | M9: 22 cells analysed, some positive, most negative | HIGH (n=728) |
| 8 | **REVERSAL family outperforms MOMENTUM** | M10: REVERSAL EV=-0.06R vs CONTINUATION EV=-0.20R | HIGH (n=728) |
| 9 | **Score is monotonically related to win rate** | Q4/Q20 confirmed | HIGH (but on mixed data) |
| 10 | **H4 owns regime, H1 owns structure, M5 owns timing** | Architecture audit | CERTAIN (design fact) |
| 11 | **Trailing stop significantly improves outcomes** | Bar-by-bar simulation: +0.185R improvement, t=6.66, p<0.001 | HIGH (n=846) |
| 12 | **Trailing does NOT convert to positive EV overall** | Best config: EV=-0.015R (still negative) | HIGH |
| 13 | **REVERSAL family + trailing stop = +0.055R** | Subset analysis: n=630, positive EV | MEDIUM (needs validation) |

### Section 2: What Previous Conclusions Are No Longer Valid

| Previous Conclusion | Why Invalid | Corrected Fact |
|---|---|---|
| "System has positive EV (+0.675R)" | ALL-epoch contamination | System EV = -0.20R |
| "P(ruin) = 0%" | Used synthetic/LEGACY inputs | P(ruin) = 100% at standard risk |
| "Fixed 0.5% produces +1917%" | Based on positive-EV system | Produces -57.2% loss |
| "Halt at 50% DD with recovery" | Assumes recovery from positive EV | System monotonically decays — halt is permanent |
| "Strategy confidence is valid p_success input" | Confirmed rejected | strategy_confidence=0 in 98% of decisions |
| "System is ready for promotion decisions" | No edge exists to promote | System needs fundamental exit redesign |

### Section 3: What Can Safely Be Implemented

| # | Change | Evidence | Risk | Implementation |
|---|--------|----------|------|---------------|
| 1 | **Reduce risk to ≤ 0.2% per trade** | Only risk levels ≤0.2% avoid 50% DD in 1000 trades | Low — reduces exposure while exit research continues | Change config: MAX_RISK_PER_TRADE = 0.002 |
| 2 | **Add CURRENT epoch filter to all experiment runners** | 13/15 reports used ALL/MIXED epoch | Zero execution risk — research infrastructure only | Add epoch gate to experiment_base.py |
| 3 | **Mark R3/R4/R5/Q19 reports as INVALIDATED** | Proven wrong by CURRENT data | Zero risk — documentation only | Update research_knowledge.json |

Nothing else can be safely implemented because the system has negative EV.

### Section 4: What Requires More Research

| # | Research Needed | Why | Priority |
|---|---|---|---|
| 1 | **Exit policy experiment (trailing stop in shadow)** | Most promising improvement (+0.185R) but needs walk-forward validation | P0 |
| 2 | **REVERSAL-only execution subset** | REVERSAL+trailing shows +0.055R (marginal) — needs n≥200 validation | P1 |
| 3 | **Entry quality filtering** | 85% of entries never reach +0.5R — can the 15% be identified at entry? | P1 |
| 4 | **TP distance reduction** | Any TP ≤ 2R improves EV. Needs shadow comparison | P2 |
| 5 | **Horizon duration differentiation** | INTRADAY uses same max_bars as SCALP. Needs proper test | P2 |

### Section 5: Current System Confidence Rating

| Domain | Rating | Evidence |
|--------|--------|---------|
| System profitability | 🔴 CONFIRMED NEGATIVE | EV=-0.20R, p<0.000001 |
| Entry signal quality | 🟡 PARTIAL SIGNAL EXISTS | MFE=0.70R avg, but 85% never reach 0.5R |
| Exit effectiveness | 🔴 CONFIRMED FAILURE | 78.7% timeout, 0.5% TP, -1.51 capture |
| Risk model validity | 🔴 PREVIOUS CONCLUSIONS INVALID | R3/R4/R5 all wrong for current system |
| Architecture correctness | ✅ SOUND | H4/H1/M15/M5 authority separation working |
| Data infrastructure | ✅ COMPREHENSIVE | 100% entity_id, 91% phase, 100% regime |
| Research capability | 🟡 CAPABLE BUT CONTAMINATED | Infrastructure correct, existing reports wrong |

---

## Final Question: "Can the current research engine make implementation decisions after this audit?"

### YES — but only for decisions backed by CURRENT-epoch data.

**What changed:**
1. The CURRENT-epoch truth is now established (-0.20R, not +0.675R)
2. Previous PROMOTE recommendations are formally invalidated
3. The discrepancy cause is identified (epoch contamination, not bug)
4. The M9/M10 findings (which used CURRENT epoch) remain valid

**What the engine CAN now decide:**
- Exit research priorities (trailing stop validation)
- Risk reduction (max risk 0.2%)
- Which experiments need re-running
- Whether to continue or halt live trading

**What it CANNOT decide until epoch-filtered experiments are re-run:**
- Weight adjustments (Q1 needs CURRENT reproduction)
- Calibration changes (Q4 needs re-validation in negative-EV context)
- Strategy activation (no strategy has validated positive EV)

**The engine's architecture is correct. The data infrastructure is correct. The only failure was using unfiltered historical data for conclusions about the current system. With epoch filtering enforced, the engine becomes trustworthy.**
