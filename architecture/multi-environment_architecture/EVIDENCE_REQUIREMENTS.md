# Evidence Requirements

## Date: 2026-07-23

For each unknown question, this document specifies the exact evidence needed.

---

## Tier 1 — Required Before ANY Real Capital

### Q1-Q2: Strategy Edge & Win Rate
- **Evidence:** 50 completed trades with adaptive min SL guard active
- **Conditions:** EV gate disabled (experiment mode) to collect maximum data
- **Success criteria:** Expectancy ≥ -0.3R (not catastrophically negative)
- **Minimum confidence:** If 50 trades show expectancy < -0.5R, stop and investigate
- **Timeline:** ~1-2 weeks of continuous operation (estimated 3-7 trades/day)

### Q8-Q9: EV Gate Effectiveness
- **Evidence:** 50 trades with `ev_experiment_mode=True`; compare EV-positive vs EV-negative outcomes
- **Analysis:** Split trades into `ev_positive=True` and `ev_positive=False` groups; compare avg R
- **Success criteria:** EV-positive group outperforms EV-negative group by ≥ 0.3R average
- **If inconclusive:** Need 100+ trades per group

### Exit Reason Bug Fix (PREREQUISITE)
- **Evidence:** After fix, verify next 10+ trades show correct exit_reason (stop_loss_hit / take_profit_hit)
- **Test:** Manually check MT5 history comment vs trade_truth exit_reason for 5 trades
- **Success criteria:** 100% match between MT5 `[sl ...]` / `[tp ...]` and trade_truth classification

---

## Tier 2 — Required Before Increasing Size

### Q3: Regime Performance
- **Evidence:** At least 10 trades each in STRUCTURED, TRANSITIONAL, and RANGING regimes
- **How to collect:** Run during London session (more regime variety) + use session guard
- **Analysis:** Expectancy per regime bucket
- **Timeline:** May take 4-6 weeks to accumulate naturally

### Q4: Session Performance
- **Evidence:** At least 15 trades during London, 15 during NY, 10 off-session
- **How to collect:** Enable SESSION_GUARD=False initially; tag all trades with derived session
- **Analysis:** Win rate and expectancy per session

### Q5: Calibration Accuracy
- **Evidence:** 30+ trades per score bucket (0.4-0.5, 0.5-0.6, 0.6-0.7)
- **Analysis:** Compare predicted p_success vs actual win rate per bucket
- **Success criteria:** Brier score < 0.25; no bucket off by > 15% from predicted
- **Timeline:** 100+ total trades needed (8-12 weeks)

---

## Tier 3 — Required Before Prop Firm

### Q19: Max Drawdown Modelling
- **Evidence:** 100+ trades → compute Monte Carlo drawdown distribution
- **Analysis:** 95th percentile drawdown at N-trade horizon
- **Success criteria:** 95th percentile drawdown < prop firm limit (e.g., < 8% for FTMO)

### Q21: Drawdown Guard Testing
- **Evidence:** Either natural trigger or simulated scenario
- **Test:** Confirm guard blocks new trades when drawdown exceeds threshold
- **Success criteria:** No trade placed after limit reached

### Prop Rule Encoding Validation
- **Evidence:** Compare encoded rules against official prop firm documentation
- **Test:** Run 50+ shadow trades through prop profile; verify no violations
- **Success criteria:** Zero false positives (blocking valid trades) or false negatives (allowing violations)

---

## Tier 4 — Continuous Improvement (Post-Deployment)

### Q12-Q13: MAE and Entry Timing
- **Implementation:** Add `max_adverse_price` tracking to Position dataclass
- **Evidence:** 50+ trades with MAE data
- **Analysis:** "Do trades that survive initial adverse move end up winning?"

### Q15-Q16: Exit Optimisation
- **Evidence:** 100+ trades with MFE, MAE, and actual R-multiple
- **Analysis:** "Would wider stops have converted losers to winners?"
- **Experiment:** Shadow comparison with 2x stop distance

### Q18: Trailing Stop Analysis
- **Evidence:** Run parallel shadow trades: one with trailing, one without
- **Analysis:** Compare net R per group over 100+ trades
- **Decision:** Enable trailing only if evidence shows improvement

---

## Data Collection Timeline

| Week | Expected Trades | Cumulative | Milestones |
|------|----------------|-----------|-----------|
| 1 | 15-25 | 15-25 | Exit reason fix validated; basic metrics |
| 2 | 15-25 | 30-50 | **TIER 1 GATE** (50 trades) — edge assessment |
| 3-4 | 30-50 | 60-100 | EV experiment analysis; session data begins |
| 5-8 | 60-100 | 120-200 | **TIER 2 GATE** — regime/session/calibration |
| 9-12 | 60-100 | 180-300 | **TIER 3 GATE** — prop readiness assessment |

**Assumption:** 3-7 trades per day with current 7-pair M5 setup, adaptive min SL active, EV experiment mode.
