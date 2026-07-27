# Final Deployment Gate

## Date: 2026-07-23
## Status: NOT YET CLEARED

---

## Gate Definition

This document defines the exact conditions required before the trading bot is trusted with real capital beyond micro-lot experimentation.

---

## PHASE 1 GATE — Data Collection Mode (Current)

**Purpose:** Collect evidence with minimum risk (0.01 lot / $0.20 per trade max risk).

### Prerequisites (must ALL be true):

- [x] End-to-end execution pipeline working
- [x] Broker fills confirmed (retcode 10009)
- [x] Trade lifecycle completes (open → close → journal → truth)
- [x] Position recovery after restart
- [x] Adaptive minimum SL guard active
- [x] S3 persistence isolated from tests
- [x] EV experiment markers deployed
- [ ] **Exit reason bug fixed** (currently all show "margin_call")
- [x] FIXED sizing at 0.01 lots
- [x] All 7 symbols active

### Operating Configuration:
```
POSITION_SIZING_MODE = "FIXED"
FIXED_LOT = 0.01
ENABLE_EV_GATE = False (experiment — collecting comparison data)
SESSION_GUARD_ENABLED = False (collecting all-session data)
ADAPTIVE_MIN_SL_ENABLED = True
MIN_SL_ABSOLUTE_FLOOR_PIPS = 3.0
```

### Exit Criteria (move to Phase 2):
- 50 completed trades with corrected architecture
- Exit reason classification verified correct
- No unexplained system failures
- Continuous operation > 48 hours without crash

---

## PHASE 2 GATE — Edge Assessment

**Purpose:** Determine whether a tradeable edge exists.

### Analysis Required:

| Metric | Minimum Threshold | Action if Failed |
|--------|-------------------|-----------------|
| Expectancy (R) | > -0.3 | Investigate; do not increase size |
| Win rate | > 15% | Investigate pattern/regime selection |
| Profit factor | > 0.5 | Investigate; strategy may need adjustment |
| Max single loss | < -3.0R | Investigate outlier; verify SL worked |
| Max consecutive losses | < 15 | Statistical check — may be normal at low win rate |

### EV Experiment Analysis:

| Comparison | Required Result |
|-----------|----------------|
| EV-positive trades avg R > EV-negative trades avg R | Yes — EV has predictive power |
| If EV-positive trades lose: | Investigate calibration — probability model may be wrong |
| If EV-negative trades win: | Normal — EV is probabilistic not deterministic |

### Decision After Phase 2:

| Outcome | Action |
|---------|--------|
| Expectancy > 0 | Proceed to Phase 3 (increase size) |
| Expectancy -0.3 to 0 | Continue collecting data (100 more trades) |
| Expectancy < -0.3 | STOP — investigate strategy fundamentals |

---

## PHASE 3 GATE — Capital Deployment (Retail)

**Purpose:** Scale from micro-lots to risk-appropriate sizing.

### Prerequisites:

- [ ] Phase 2 cleared (expectancy ≥ 0 over 50+ trades)
- [ ] EV gate decision made (enable or keep disabled based on evidence)
- [ ] Session guard decision made (based on session performance data)
- [ ] PolicyProfile extracted and versioned
- [ ] DYNAMIC sizing validated with known balance
- [ ] Drawdown guard enabled and tested
- [ ] 48+ hours continuous operation without intervention

### Recommended Configuration:
```
POSITION_SIZING_MODE = "DYNAMIC"
RISK_PER_TRADE_PERCENT = 0.5  (conservative start)
ENABLE_EV_GATE = True (if Phase 2 showed EV works)
SESSION_GUARD_ENABLED = True (if off-session data is worse)
ENABLE_DRAWDOWN_GUARD = True
MAX_DRAWDOWN_PERCENT = 10.0
```

### Capital Allocation:
- Start with account size where max expected drawdown represents < 5% of total capital
- Example: if max drawdown = 10%, and you can tolerate losing $500, start with $5000

---

## PHASE 4 GATE — Prop Firm Evaluation

**Purpose:** Determine readiness to purchase a prop firm challenge.

### Prerequisites:

- [ ] Phase 3 running profitably for 30+ days
- [ ] Expectancy > 0.2R over 100+ trades
- [ ] Maximum drawdown < 6% (within most prop limits)
- [ ] Prop firm profile versioned and tested with shadow trades
- [ ] All prop rules encoded and verified against official documentation
- [ ] News event behaviour observed (at least 2 high-impact news events)
- [ ] Weekend behaviour observed (at least 2 Friday→Monday transitions)

### Risk Assessment:
- Challenge cost vs expected profit
- Probability of passing (derived from 100+ trade statistics)
- Expected time to target (profit target / daily expected profit)

---

## ABSOLUTE DEPLOYMENT BLOCKERS

These conditions MUST NEVER be true during any deployment phase:

| Blocker | Reason |
|---------|--------|
| Exit reason classification broken | Cannot analyse trade outcomes |
| P&L calculation incorrect | Cannot trust performance metrics |
| Position lifecycle incomplete | Trades may go unrecorded |
| No kill switch available | Cannot emergency-stop the system |
| Test data in production S3 | Corrupts analytics |
| Broker connection unstable (> 3 disconnects/day) | Execution unreliable |

---

## Summary

```
TODAY (July 2026):
  Phase 1 — Data Collection Mode
  Status: 1 RED item remaining (exit reason bug)
  Next: Fix exit reason → run 50 trades → assess

WEEKS 2-4:
  Phase 2 — Edge Assessment
  Status: Pending 50-trade dataset
  Next: Analyse expectancy → decide EV gate → decide session guard

MONTH 2+:
  Phase 3 — Capital Deployment (if edge confirmed)
  Status: Pending Phase 2 clearance

MONTH 3+:
  Phase 4 — Prop Firm (if profitable at scale)
  Status: Pending Phase 3 track record
```

---

## Final Statement

**The system is architecturally ready for deployment.** The intelligence engine, execution pipeline, lifecycle management, persistence, and observability are all functional and tested.

**The system is NOT yet evidence-ready for deployment.** There is no statistical evidence of a tradeable edge. The 13-trade sample was collected under structurally flawed conditions (no min SL, EV bypassed, all off-session) and is not representative of the corrected system's potential.

**The single remaining code fix** (exit reason mapping) takes 5 minutes. After that fix, the bot should run in micro-lot data collection mode until 50+ clean trades provide the first statistically meaningful performance assessment.

The architecture does not need further changes. The evidence does.
