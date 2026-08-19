# V10 RESEARCH ENGINE — FIRST AUTHORITATIVE BASELINE

**Run ID:** baseline_20260813T014133
**Timestamp:** 2026-08-13T01:43:09Z
**Duration:** 95.6s
**Question Bank Hash:** 95d2652aed50e7ee

## UNIVERSE SIZES

- EXECUTION: 94 records
- DECISION: 12439 records
- MARKET: 10623 records
- STRATEGY: 17588 records
- RISK: 8819 records
- OUTCOME: 94 records
- SHADOW_OUTCOME: 4153 records

## EXECUTION SUMMARY

- **COMPLETE (meaningful finding):** 40
- **INCONCLUSIVE (insufficient):** 8
- **ERROR:** 0
- **BLOCKED (not executed):** 3

## EXECUTION FINDINGS

### E-001 — System Expectancy
- **Outcome:** NEGATIVE
- **Confidence:** MEDIUM
- **Evidence:** REALISED
- **Population:** 94
- **Metrics:** count=94, wins=34, losses=60, win_rate=+0.3617, mean_r=-0.1758, median_r=-1.0000, total_r=-16.5231, avg_win_r=+1.5395

### E-002 — Win/Loss Distribution Shape
- **Outcome:** NEGATIVE
- **Confidence:** MEDIUM
- **Evidence:** REALISED
- **Population:** 94
- **Metrics:** count=94, mean=-0.1758, median=-1.0000, std=+1.5628, min=-4.5000, max=+5.4490, expectancy_count=94, wins=34

### E-003 — Exit Reason Distribution
- **Outcome:** NEGATIVE
- **Confidence:** MEDIUM
- **Evidence:** REALISED
- **Population:** 94
- **Metrics:** segment_count=2, count=94, wins=34, losses=60, win_rate=+0.3617, mean_r=-0.1758, median_r=-1.0000

### E-004 — Execution Quality by Session
- **Outcome:** NEGATIVE
- **Confidence:** MEDIUM
- **Evidence:** REALISED
- **Population:** 94
- **Metrics:** segment_count=9, count=94, wins=34, losses=60, win_rate=+0.3617, mean_r=-0.1758, median_r=-1.0000

### E-005 — Probability of Ruin
- **Outcome:** NEGATIVE
- **Confidence:** MEDIUM
- **Evidence:** REALISED
- **Population:** 94
- **Metrics:** count=94, wins=34, losses=60, win_rate=+0.3617, mean_r=-0.1758, median_r=-1.0000, total_r=-16.5231, avg_win_r=+1.5395

### E-007 — Stop Placement Effectiveness
- **Outcome:** NEGATIVE
- **Confidence:** MEDIUM
- **Evidence:** REALISED
- **Population:** 94
- **Metrics:** count=94, wins=34, losses=60, win_rate=+0.3617, mean_r=-0.1758, median_r=-1.0000, total_r=-16.5231, avg_win_r=+1.5395

### E-008 — Pattern Degradation Over Time
- **Outcome:** STABLE
- **Confidence:** MEDIUM
- **Evidence:** REALISED
- **Population:** 94
- **Metrics:** periods=3

### E-009 — Trade Duration vs Outcome
- **Outcome:** NOT_PREDICTIVE
- **Confidence:** MEDIUM
- **Evidence:** REALISED
- **Population:** 94
- **Metrics:** monotonic=False, top_bottom_spread=+2.6489, bucket_count=5

### E-010 — Risk:Reward Ratio Effectiveness
- **Outcome:** NEGATIVE
- **Confidence:** MEDIUM
- **Evidence:** REALISED
- **Population:** 94
- **Metrics:** population_size=94, analytical_sample=94, groups_discovered=2, groups_sufficient=2, groups_insufficient=0, overall_mean=-0.1758, group_spread=+3.0121, mean_r=-0.1758

## DECISION FINDINGS

### D-001 — Score Predictive Power
- **Outcome:** NOT_PREDICTIVE
- **Confidence:** MEDIUM
- **Evidence:** REALISED
- **Population:** 12439
- **Metrics:** monotonic=False, top_bottom_spread=+0.3451, bucket_count=5

### D-002 — EV Calibration
- **Outcome:** POORLY_CALIBRATED
- **Confidence:** MEDIUM
- **Evidence:** REALISED
- **Population:** 394
- **Metrics:** mean_calibration_error=+0.2113, buckets=6

### D-003 — Decision Threshold Effectiveness
- **Outcome:** NOT_PREDICTIVE
- **Confidence:** MEDIUM
- **Evidence:** REALISED
- **Population:** 12439
- **Metrics:** monotonic=False, top_bottom_spread=+0.3451, bucket_count=5

### D-004 — Rejection Stage Distribution
- **Outcome:** NEGATIVE
- **Confidence:** HIGH
- **Evidence:** REALISED
- **Population:** 12045
- **Metrics:** segment_count=878, count=1, wins=0, losses=1, win_rate=+0.0000, mean_r=-1.0000, median_r=-1.0000
- **Warnings:** Small sample (1 trades) — low statistical confidence

### D-005 — Opportunity Quality Predictive Value
- **Outcome:** INCONCLUSIVE
- **Confidence:** INSUFFICIENT
- **Evidence:** REALISED
- **Population:** 394
- **Warnings:** Insufficient data (4 records)

### D-006 — Opportunity Failure Characterisation
- **Outcome:** NEGATIVE
- **Confidence:** HIGH
- **Evidence:** REALISED
- **Population:** 394
- **Metrics:** segment_count=1, count=80, wins=30, losses=50, win_rate=+0.3750, mean_r=-0.1432, median_r=-1.0000

## MARKET FINDINGS

### M-001 — Regime Predicts Outcomes
- **Outcome:** NEGATIVE
- **Confidence:** HIGH
- **Evidence:** REALISED
- **Population:** 10623
- **Metrics:** segment_count=2, count=4, wins=1, losses=3, win_rate=+0.2500, mean_r=-0.8245, median_r=-1.0375
- **Warnings:** Small sample (4 trades) — low statistical confidence

### M-002 — HTF Alignment Value
- **Outcome:** INCONCLUSIVE
- **Confidence:** INSUFFICIENT
- **Evidence:** REALISED
- **Population:** 10623
- **Warnings:** Insufficient data (4 records)

### M-003 — Volatility State Impact
- **Outcome:** NEGATIVE
- **Confidence:** HIGH
- **Evidence:** REALISED
- **Population:** 10623
- **Metrics:** segment_count=2, count=4, wins=1, losses=3, win_rate=+0.2500, mean_r=-0.8245, median_r=-1.0375
- **Warnings:** Small sample (4 trades) — low statistical confidence

### M-004 — Market Structure Clarity
- **Outcome:** INCONCLUSIVE
- **Confidence:** INSUFFICIENT
- **Evidence:** REALISED
- **Population:** 10623
- **Warnings:** Insufficient data (4 records)

### M-005 — Location Quality Impact
- **Outcome:** NEGATIVE
- **Confidence:** HIGH
- **Evidence:** REALISED
- **Population:** 10623
- **Metrics:** segment_count=1, count=4, wins=1, losses=3, win_rate=+0.2500, mean_r=-0.8245, median_r=-1.0375
- **Warnings:** Small sample (4 trades) — low statistical confidence

### M-006 — Session Edge Variation
- **Outcome:** NEGATIVE
- **Confidence:** HIGH
- **Evidence:** REALISED
- **Population:** 10623
- **Metrics:** segment_count=2, count=4, wins=1, losses=3, win_rate=+0.2500, mean_r=-0.8245, median_r=-1.0375
- **Warnings:** Small sample (4 trades) — low statistical confidence

## STRATEGY FINDINGS

### S-001 — Strategy Family Expectancy
- **Outcome:** NEGATIVE
- **Confidence:** HIGH
- **Evidence:** REALISED
- **Population:** 17588
- **Metrics:** segment_count=3, count=81, wins=30, losses=51, win_rate=+0.3704, mean_r=-0.1538, median_r=-1.0000

### S-002 — Pattern Expectancy
- **Outcome:** NEGATIVE
- **Confidence:** HIGH
- **Evidence:** REALISED
- **Population:** 17588
- **Metrics:** segment_count=11, count=81, wins=30, losses=51, win_rate=+0.3704, mean_r=-0.1538, median_r=-1.0000

### S-003 — Strategy Selection Accuracy
- **Outcome:** POORLY_CALIBRATED
- **Confidence:** LOW
- **Evidence:** REALISED
- **Population:** 433
- **Metrics:** mean_calibration_error=+0.4940, buckets=5

### S-004 — Strategy Gap Characterisation
- **Outcome:** INCONCLUSIVE
- **Confidence:** INSUFFICIENT
- **Evidence:** REALISED
- **Population:** 1946
- **Metrics:** count=0
- **Warnings:** No records with field 'r_multiple'; No records with R-multiple data

## SHADOW FINDINGS

### SD-001 — Shadow Counterfactual Expectancy
- **Outcome:** POSITIVE
- **Confidence:** HIGH
- **Evidence:** COUNTERFACTUAL
- **Population:** 4153
- **Metrics:** count=4153, wins=1759, losses=2394, win_rate=+0.4235, mean_r=+0.0737, median_r=-0.0278, total_r=306.1, avg_win_r=+0.7690

### SD-002 — Missed Opportunity Cost
- **Outcome:** NEGATIVE
- **Confidence:** HIGH
- **Evidence:** COUNTERFACTUAL
- **Population:** 3201
- **Metrics:** count=3201, wins=1309, losses=1892, win_rate=+0.4089, mean_r=-0.0720, median_r=-0.0476, total_r=-230.4, avg_win_r=+0.4467

### SD-004 — Rejection Stage Counterfactual Expectancy
- **Outcome:** NEGATIVE
- **Confidence:** HIGH
- **Evidence:** COUNTERFACTUAL
- **Population:** 3201
- **Metrics:** segment_count=0, count=3201, wins=1309, losses=1892, win_rate=+0.4089, mean_r=-0.0720, median_r=-0.0476

### SD-005 — Shadow Horizon Comparison
- **Outcome:** NEGATIVE
- **Confidence:** HIGH
- **Evidence:** COUNTERFACTUAL
- **Population:** 3201
- **Metrics:** population_size=3201, analytical_sample=3201, groups_discovered=3, groups_sufficient=3, groups_insufficient=0, overall_mean=-0.0720, group_spread=+0.1723, mean_r=-0.0720

### SD-006 — Shadow Strategy Expectancy
- **Outcome:** POSITIVE
- **Confidence:** HIGH
- **Evidence:** COUNTERFACTUAL
- **Population:** 4153
- **Metrics:** segment_count=13, count=4153, wins=1759, losses=2394, win_rate=+0.4235, mean_r=+0.0737, median_r=-0.0278

### SD-007 — Shadow Regime Expectancy
- **Outcome:** POSITIVE
- **Confidence:** HIGH
- **Evidence:** COUNTERFACTUAL
- **Population:** 4153
- **Metrics:** segment_count=3, count=4153, wins=1759, losses=2394, win_rate=+0.4235, mean_r=+0.0737, median_r=-0.0278

## CROSS-DOMAIN FINDINGS

### ED-001 — Decision-to-Execution Edge Leakage
- **Outcome:** NEGATIVE
- **Confidence:** MEDIUM
- **Evidence:** REALISED
- **Population:** 94
- **Metrics:** population_size=94, analytical_sample=94, groups_discovered=4, groups_sufficient=4, groups_insufficient=0, overall_mean=-0.1758, group_spread=+1.3213, mean_r=-0.1758

### ED-003 — Position Sizing Effectiveness
- **Outcome:** NEGATIVE
- **Confidence:** MEDIUM
- **Evidence:** REALISED
- **Population:** 94
- **Metrics:** count=94, wins=34, losses=60, win_rate=+0.3617, mean_r=-0.1758, median_r=-1.0000, total_r=-16.5231, avg_win_r=+1.5395

### EM-001 — Regime-Conditioned Expectancy
- **Outcome:** NEGATIVE
- **Confidence:** MEDIUM
- **Evidence:** REALISED
- **Population:** 94
- **Metrics:** segment_count=4, count=94, wins=34, losses=60, win_rate=+0.3617, mean_r=-0.1758, median_r=-1.0000

### EM-002 — Market Drift Detection
- **Outcome:** STABLE
- **Confidence:** MEDIUM
- **Evidence:** REALISED
- **Population:** 94
- **Metrics:** periods=3

### ES-001 — Execution Quality by Strategy
- **Outcome:** NEGATIVE
- **Confidence:** MEDIUM
- **Evidence:** REALISED
- **Population:** 94
- **Metrics:** segment_count=3, count=94, wins=34, losses=60, win_rate=+0.3617, mean_r=-0.1758, median_r=-1.0000

### DM-001 — Decision Quality Under Regime
- **Outcome:** NEGATIVE
- **Confidence:** HIGH
- **Evidence:** REALISED
- **Population:** 394
- **Metrics:** segment_count=2, count=80, wins=30, losses=50, win_rate=+0.3750, mean_r=-0.1432, median_r=-1.0000

### DM-002 — Opportunity Detection vs Market State
- **Outcome:** COMPLETED
- **Confidence:** INSUFFICIENT
- **Evidence:** REALISED
- **Population:** 12439
- **Metrics:** population_size=12439, analytical_sample=4, groups_discovered=2, groups_sufficient=0, groups_insufficient=2
- **Warnings:** All 2 groups have fewer than 3 observations each

### DM-003 — Rejection Rate by Market State
- **Outcome:** NEGATIVE
- **Confidence:** HIGH
- **Evidence:** REALISED
- **Population:** 12439
- **Metrics:** segment_count=5, count=81, wins=30, losses=51, win_rate=+0.3704, mean_r=-0.1538, median_r=-1.0000

### DS-001 — Strategy Confidence Calibration
- **Outcome:** INCONCLUSIVE
- **Confidence:** INSUFFICIENT
- **Evidence:** REALISED
- **Population:** 394
- **Warnings:** Insufficient calibration data (0 records)

### DS-002 — Strategy Conditions vs Outcome
- **Outcome:** INCONCLUSIVE
- **Confidence:** INSUFFICIENT
- **Evidence:** REALISED
- **Population:** 394
- **Warnings:** Insufficient data (0 records)

### MS-001 — Strategy × Regime Interaction
- **Outcome:** NEGATIVE
- **Confidence:** HIGH
- **Evidence:** REALISED
- **Population:** 10623
- **Metrics:** segment_count=0, count=4, wins=1, losses=3, win_rate=+0.2500, mean_r=-0.8245, median_r=-1.0375
- **Warnings:** Small sample (4 trades) — low statistical confidence

### MS-002 — Pattern × Market Context Interaction
- **Outcome:** NEGATIVE
- **Confidence:** HIGH
- **Evidence:** REALISED
- **Population:** 10623
- **Metrics:** segment_count=0, count=4, wins=1, losses=3, win_rate=+0.2500, mean_r=-0.8245, median_r=-1.0375
- **Warnings:** Small sample (4 trades) — low statistical confidence

### MS-003 — Strategy Availability by Market State
- **Outcome:** NEGATIVE
- **Confidence:** INSUFFICIENT
- **Evidence:** REALISED
- **Population:** 10623
- **Metrics:** count=4, mean=-0.8245, median=-1.0375, std=+0.6255, min=-1.3157, max=+0.0927, expectancy_count=4, wins=1
- **Warnings:** Small sample (4 trades) — low statistical confidence

### EDM-001 — Complete Trade Lifecycle Analysis
- **Outcome:** NEGATIVE
- **Confidence:** MEDIUM
- **Evidence:** REALISED
- **Population:** 94
- **Metrics:** population_size=94, analytical_sample=94, groups_discovered=4, groups_sufficient=4, groups_insufficient=0, overall_mean=-0.1758, group_spread=+1.3213, mean_r=-0.1758

### DMS-001 — Decision Quality Across Strategy × Market
- **Outcome:** NEGATIVE
- **Confidence:** HIGH
- **Evidence:** REALISED
- **Population:** 394
- **Metrics:** segment_count=0, count=80, wins=30, losses=50, win_rate=+0.3750, mean_r=-0.1432, median_r=-1.0000

### EDMS-001 — Full System Attribution
- **Outcome:** NOT_PREDICTIVE
- **Confidence:** MEDIUM
- **Evidence:** REALISED
- **Population:** 94
- **Metrics:** monotonic=False, top_bottom_spread=-0.1949, bucket_count=5

### EDMS-002 — Promotion Impact Analysis
- **Outcome:** NEGATIVE
- **Confidence:** MEDIUM
- **Evidence:** REALISED
- **Population:** 94
- **Metrics:** count=94, wins=34, losses=60, win_rate=+0.3617, mean_r=-0.1758, median_r=-1.0000, total_r=-16.5231, avg_win_r=+1.5395

## CONVERGENT EVIDENCE

### Regime-Related Convergence
- E-010: outcome=NEGATIVE, spread=3.0121
- M-001: outcome=NEGATIVE, spread=?
- M-003: outcome=NEGATIVE, spread=?
- ED-001: outcome=NEGATIVE, spread=1.3213
- EM-001: outcome=NEGATIVE, spread=?

## POTENTIAL OPTIMISATION LEADS

*(Observations only — NOT recommendations to implement)*

- **E-001** (System Expectancy): NEGATIVE [MEDIUM] — may indicate area for investigation
- **E-002** (Win/Loss Distribution Shape): NEGATIVE [MEDIUM] — may indicate area for investigation
- **E-003** (Exit Reason Distribution): NEGATIVE [MEDIUM] — may indicate area for investigation
- **E-004** (Execution Quality by Session): NEGATIVE [MEDIUM] — may indicate area for investigation
- **E-005** (Probability of Ruin): NEGATIVE [MEDIUM] — may indicate area for investigation
- **E-007** (Stop Placement Effectiveness): NEGATIVE [MEDIUM] — may indicate area for investigation
- **E-009** (Trade Duration vs Outcome): NOT_PREDICTIVE [MEDIUM] — may indicate area for investigation
- **E-010** (Risk:Reward Ratio Effectiveness): NEGATIVE [MEDIUM] — may indicate area for investigation
- **D-001** (Score Predictive Power): NOT_PREDICTIVE [MEDIUM] — may indicate area for investigation
- **D-003** (Decision Threshold Effectiveness): NOT_PREDICTIVE [MEDIUM] — may indicate area for investigation
- **D-004** (Rejection Stage Distribution): NEGATIVE [HIGH] — may indicate area for investigation
- **D-006** (Opportunity Failure Characterisation): NEGATIVE [HIGH] — may indicate area for investigation
- **M-001** (Regime Predicts Outcomes): NEGATIVE [HIGH] — may indicate area for investigation
- **M-003** (Volatility State Impact): NEGATIVE [HIGH] — may indicate area for investigation
- **M-005** (Location Quality Impact): NEGATIVE [HIGH] — may indicate area for investigation
- **M-006** (Session Edge Variation): NEGATIVE [HIGH] — may indicate area for investigation
- **S-001** (Strategy Family Expectancy): NEGATIVE [HIGH] — may indicate area for investigation
- **S-002** (Pattern Expectancy): NEGATIVE [HIGH] — may indicate area for investigation
- **ED-001** (Decision-to-Execution Edge Leakage): NEGATIVE [MEDIUM] — may indicate area for investigation
- **ED-003** (Position Sizing Effectiveness): NEGATIVE [MEDIUM] — may indicate area for investigation
- **EM-001** (Regime-Conditioned Expectancy): NEGATIVE [MEDIUM] — may indicate area for investigation
- **ES-001** (Execution Quality by Strategy): NEGATIVE [MEDIUM] — may indicate area for investigation
- **DM-001** (Decision Quality Under Regime): NEGATIVE [HIGH] — may indicate area for investigation
- **DM-003** (Rejection Rate by Market State): NEGATIVE [HIGH] — may indicate area for investigation
- **MS-001** (Strategy × Regime Interaction): NEGATIVE [HIGH] — may indicate area for investigation
- **MS-002** (Pattern × Market Context Interaction): NEGATIVE [HIGH] — may indicate area for investigation
- **EDM-001** (Complete Trade Lifecycle Analysis): NEGATIVE [MEDIUM] — may indicate area for investigation
- **DMS-001** (Decision Quality Across Strategy × Market): NEGATIVE [HIGH] — may indicate area for investigation
- **EDMS-001** (Full System Attribution): NOT_PREDICTIVE [MEDIUM] — may indicate area for investigation
- **EDMS-002** (Promotion Impact Analysis): NEGATIVE [MEDIUM] — may indicate area for investigation
- **SD-001** (Shadow Counterfactual Expectancy): POSITIVE [HIGH] — confirms current approach
- **SD-002** (Missed Opportunity Cost): NEGATIVE [HIGH] — may indicate area for investigation
- **SD-004** (Rejection Stage Counterfactual Expectancy): NEGATIVE [HIGH] — may indicate area for investigation
- **SD-005** (Shadow Horizon Comparison): NEGATIVE [HIGH] — may indicate area for investigation
- **SD-006** (Shadow Strategy Expectancy): POSITIVE [HIGH] — confirms current approach
- **SD-007** (Shadow Regime Expectancy): POSITIVE [HIGH] — confirms current approach

## WHAT DO WE NOW KNOW ABOUT V10?

### Realised Performance
- System expectancy (E-001): **-0.1758R** per trade
- Win rate: 36%
- Sample: 94 trades

### Counterfactual Opportunity Pool
- Shadow expectancy (SD-001): **+0.0737R** per opportunity
- Shadow win rate: 42%
- Opportunities observed: 4153

### Rejection Stage Analysis
- SD-004 outcome: NEGATIVE
- Rejection stages analysed: 0

---

*This is the authoritative V10 research baseline. No changes have been made to V10.*
*Next step: Review findings → Design experiments → Validate candidates → Human approval.*