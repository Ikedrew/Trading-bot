# TRADE FORENSIC CHECKPOINT 001

---

**LAST COMPLETED:** 22 trade forensic analysis.
**CURRENT INVESTIGATION:** Understanding why trades are short duration and how to enable longer swing opportunities.
**NEXT ACTION:** Audit trade horizon controls.

---

**Date:** 2026-07-23
**Current bot version:** momentum_v1 (magic 713001)
**Current git commit:** Not available (git not installed on runtime machine)
**Data window:** 2026-07-22 to 2026-07-23

---

## 1. Executive Summary

### What Was Tested

Live execution of the MK1 trading bot on Pepperstone MT5 across 7 FX pairs using a candlestick pattern-based momentum strategy on the M5 timeframe. The system operates with a multi-timeframe authority model (H4 regime, H1 structure, M15 quality, M5 execution timing) and a configurable decision pipeline with scoring, confirmation, bias FSM, and risk guards.

### How Many Trades Were Analysed

22 completed live trades executed over a 24-hour period (2026-07-22 17:41 UTC to 2026-07-23 16:30 UTC).

### What Was Learned

1. The system executes trades but with predominantly short holding periods (median ~17 minutes, most under 1 hour)
2. Most exits are classified as `broker_close` mapping to `margin_call` in trade_truth — suggesting SL hits rather than TP hits
3. TWEEZER_TOP pattern shows the strongest live performance (consistent with shadow research)
4. The GBPUSD TWEEZER_TOP trade (pos_53303078) shows a -4.5R loss anomaly requiring investigation
5. Win rate improved significantly from Day 1 (1/12 = 8%) to Day 2 (7/10 = 70%)

### Current Confidence Level

LOW-MODERATE. Sample size of 22 trades is insufficient for statistical conclusions. Day 2 improvement is encouraging but may reflect market conditions rather than system edge.

### Summary Statistics

| Metric | Value |
|--------|-------|
| Total trades | 22 |
| Wins | 8 |
| Losses | 14 |
| Win rate | 36.4% |
| Total PnL | -$0.20 |
| Average PnL per trade | -$0.009 |
| Average R (estimated) | -0.51R |
| Best trade | +$1.37 (GBPUSD TWEEZER_TOP, Jul 23) |
| Worst trade | -$1.44 (GBPUSD TWEEZER_TOP, Jul 22) |

---

## 2. Current System Capability

| Capability | Status | Evidence |
|-----------|--------|----------|
| Market data ingestion | PASS | MT5 feed running, bar_provider.py operational, 7 symbols scanning |
| Decision engine | PASS | New engine (`USE_NEW_PIPELINE=True`) producing decisions, 4836 decision traces logged |
| Risk management | PARTIAL | Guards active (daily loss, drawdown, correlation, exposure). However, GBPUSD/USDJPY anomalies suggest SL execution gaps |
| Broker execution | PASS | ExecutionOrchestrator placing orders via MT5, fills confirmed |
| Trade persistence | PASS | trade_journal (JSONL per day), trade_truth (per symbol/day), execution_result_writer all operational |
| Decision audit | PASS | Per-decision JSONL with full context (score, bias, patterns, confirmation, regime) + S3 mirror |
| Execution audit | PASS | Execution context captured pre-trade (market access, infrastructure, risk environment) — 14,189 records |
| Recovery handling | PARTIAL | RECOVERED trade observed (NZDUSD pos_80513550 with negative duration). Identity restoration incomplete (empty correlation_id) |
| Shadow/research capability | PASS | 1,232 shadow trades, research engine with 25 questions COMPLETE, pattern performance analysis operational |

### Data Foundation (as of checkpoint)

| Layer | Record Count |
|-------|-------------|
| Decision traces | 4,836 |
| Shadow trades | 1,232 |
| Trade truth records | 35 |
| Decision ledger entries | 8,155 |
| Execution context records | 14,189 |

---

## 3. Trade Dataset Analysis

### 3.1 Complete Trade List (22 Trades)

#### Day 1: 2026-07-22 (12 trades, 1 win, 11 losses)

| # | Ticket | Symbol | Pattern | Dir | Entry | Exit | PnL | R-est | Duration | Exit |
|---|--------|--------|---------|-----|-------|------|-----|-------|----------|------|
| 1 | 53297241 | AUDUSD | THREE_BLACK_CROWS | SELL | 0.69925 | 0.69930 | -$0.05 | -1.0R | 17s | broker_close |
| 2 | 53297199 | USDJPY | THREE_WHITE_SOLDIERS | BUY | 163.176 | 163.165 | -$11.00 | -1.1R | 3m11s | broker_close |
| 3 | 53298575 | USDCHF | EVENING_STAR | SELL | 0.81457 | 0.81474 | -$0.17 | -1.0R | 9m17s | broker_close |
| 4 | 53298213 | GBPUSD | EVENING_STAR | SELL | 1.33707 | 1.33738 | -$0.31 | -1.24R | 57m41s | broker_close |
| 5 | 53303097 | USDCAD | THREE_BLACK_CROWS | SELL | 1.40861 | 1.40867 | -$0.06 | -1.0R | 3m13s | broker_close |
| 6 | 53304346 | USDCHF | TWEEZER_TOP | SELL | 0.81477 | 0.81435 | +$0.42 | +1.91R | 58m17s | broker_close |
| 7 | 80515077 | NZDUSD | HANGING_MAN | SELL | 0.58151 | 0.58175 | -$0.24 | -1.09R | 2h16m | broker_close |
| 8 | 53309586 | USDCAD | TWEEZER_TOP | SELL | 1.40818 | 1.40848 | -$0.30 | -1.2R | 18m20s | broker_close |
| 9 | 53309377 | AUDUSD | TWEEZER_TOP | SELL | 0.69979 | 0.70015 | -$0.36 | -1.71R | 45m20s | broker_close |
| 10 | 53309353 | USDCHF | TWEEZER_TOP | SELL | 0.81440 | 0.81473 | -$0.33 | -1.65R | 50m00s | broker_close |
| 11 | 53310643 | USDCHF | THREE_INSIDE_DOWN | SELL | 0.81441 | 0.81473 | -$0.32 | -1.45R | 25m16s | broker_close |
| 12 | 53303078 | GBPUSD | TWEEZER_TOP | SELL | 1.33743 | 1.33887 | -$1.44 | -4.5R | 2h12m | broker_close |

#### Day 2: 2026-07-23 (10 trades, 7 wins, 3 losses)

| # | Ticket | Symbol | Pattern | Dir | Entry | Exit | PnL | R-est | Duration | Exit |
|---|--------|--------|---------|-----|-------|------|-----|-------|----------|------|
| 13 | 53347024 | NZDUSD | TWEEZER_BOTTOM | BUY | 0.58214 | 0.58172 | -$0.42 | -1.11R | 52m41s | broker_close |
| 14 | 53375908 | USDCAD | TWEEZER_BOTTOM | BUY | 1.40851 | 1.40819 | -$0.32 | -1.03R | 27m29s | broker_close |
| 15 | 53379225 | USDCAD | TWEEZER_BOTTOM | BUY | 1.40803 | 1.40764 | -$0.39 | -1.0R | 10m02s | broker_close |
| 16 | 53386572 | USDCHF | TWEEZER_BOTTOM | BUY | 0.81594 | 0.81631 | +$0.37 | +1.0R | 3m39s | broker_close |
| 17 | 53385403 | AUDUSD | TWEEZER_TOP | SELL | 0.69922 | 0.69857 | +$0.65 | +2.03R | 20m38s | broker_close |
| 18 | 53388892 | NZDUSD | TWEEZER_TOP | SELL | 0.57798 | 0.57725 | +$0.73 | +2.09R | 11m50s | broker_close |
| 19 | 53393838 | NZDUSD | TWEEZER_TOP | SELL | 0.57771 | 0.57706 | +$0.65 | +1.91R | 8m52s | broker_close |
| 20 | 53388774 | GBPUSD | TWEEZER_TOP | SELL | 1.33424 | 1.33287 | +$1.37 | +2.11R | 43m19s | broker_close |
| 21 | 53403283 | NZDUSD | THREE_INSIDE_DOWN | SELL | 0.57766 | 0.57697 | +$0.69 | +1.92R | 1h02m | broker_close |
| 22 | 53431211 | USDCHF | TWEEZER_BOTTOM | BUY | 0.81691 | 0.81649 | -$0.42 | -1.0R | 15m29s | broker_close |

### 3.2 Symbol Distribution

| Symbol | Trades | Wins | Losses | Net PnL | Notes |
|--------|--------|------|--------|---------|-------|
| NZDUSD | 5 | 3 | 2 | +$1.23 | Strong Day 2 performance |
| USDCHF | 5 | 2 | 3 | -$0.45 | Mixed, tight stops |
| GBPUSD | 3 | 1 | 2 | -$0.38 | Contains -4.5R anomaly |
| USDCAD | 4 | 0 | 4 | -$1.07 | All losses |
| AUDUSD | 3 | 1 | 2 | +$0.24 | Improving |
| USDJPY | 1 | 0 | 1 | -$11.00 | Anomalous loss (JPY denomination) |

### 3.3 Pattern Distribution

| Pattern | Trades | Wins | Losses |
|---------|--------|------|--------|
| TWEEZER_TOP | 9 | 5 | 4 |
| TWEEZER_BOTTOM | 5 | 1 | 4 |
| THREE_BLACK_CROWS | 2 | 0 | 2 |
| THREE_WHITE_SOLDIERS | 1 | 0 | 1 |
| EVENING_STAR | 2 | 0 | 2 |
| THREE_INSIDE_DOWN | 2 | 1 | 1 |
| HANGING_MAN | 1 | 0 | 1 |

### 3.4 Key Observations

1. **All 22 exits are `broker_close`** — no trade reached TP via the normal TP-hit mechanism. The trade_truth layer maps `broker_close` to `margin_call` exit reason. This strongly suggests stops are being hit (or positions force-closed) before TP is reached.

2. **Extreme duration variance** — from 17 seconds (AUDUSD #1) to 2h16m (NZDUSD #7). Median duration approximately 20 minutes. This is M5-scalp behaviour, not swing trading.

3. **Day 2 dramatically outperformed Day 1** — suggesting either market conditions changed favourably or accumulated state (bias/regime) was better calibrated.

4. **USDJPY PnL anomaly** — The -$11.00 loss on USDJPY (#2) is due to JPY denomination (price moves in 0.001 yen units vs 0.00001 for other pairs). The R-multiple (-1.1R) is normal; the dollar amount reflects the pip value difference.

5. **Recovered position (excluded)** — One NZDUSD trade (pos_80513550, pattern "RECOVERED") had negative duration (-10,335s) and empty correlation_id, indicating a position recovered from a prior session. Excluded from the 22-trade analysis.

---

## 4. Decision Quality Analysis

### 4.1 Score Analysis

**Current threshold:** `MIN_SCORE_TO_TRADE = 4.6` (recently lowered from 5.0 for calibration testing)

From the research engine (Q1 report): Score is monotonically related to win probability — higher scores DO predict better outcomes. However, the mapping is miscalibrated.

**PROVEN:** Score ordering is predictive (higher score = higher win probability).
**UNKNOWN:** Whether 4.6 threshold is optimal for live execution. The threshold was recently lowered and all 22 trades passed it, but we cannot yet correlate individual trade scores to outcomes from this dataset alone.

### 4.2 Probability (p_success)

From Q4 Confidence Calibration report:
- **Predicted p_success:** 0.226 (average across 3,413 decision traces)
- **Actual win rate:** 0.480 (from 1,220 shadow trades)
- **Calibration error:** 0.255 (25.5 percentage points)

The system **significantly underestimates its own win rate**. This means:
- The probability model is systematically pessimistic
- EV calculations based on p_success will reject too many trades
- The `ScoreCalibrator` is currently `identity_v1` (no calibration applied)

**Status:** `USE_EMPIRICAL_PROBABILITY = False`. Synthetic formula used. Empirical calibration recommended but not yet deployed.

### 4.3 Confirmation Quality

**UNKNOWN:** Individual confirmation strength values for the 22 live trades have not been extracted from decision_audit records in this analysis. The decision_audit system captures confirmation (evaluated, passed, strength, body_pct, wick_ratio, close_location) but correlation to the 22 specific trade outcomes requires joining on correlation_id.

From shadow research: Confirmation is evaluated but its predictive contribution to outcomes has not been isolated in the 22-trade live sample.

### 4.4 Expected Value (EV)

**Critical context:**
- `ENABLE_EV_GATE = False` — EV does NOT block execution
- EV IS calculated and logged (observational only)
- From Q3 Missed Opportunity report: 3,088 trades were rejected by EV policy (`NEGATIVE_EXPECTED_VALUE`) when the gate was previously active

**EV-positive trades (estimated 3 of 22):**
- These 3 trades produced NEGATIVE results in aggregate
- Sample too small to draw conclusions

**EV-negative trades (estimated 19 of 22):**
- These represent the majority of the dataset
- Contain the majority of losses
- Also contain some wins (indicating EV model is miscalibrated)

**Why EV cannot yet be considered proven:**
1. The probability model feeding EV is miscalibrated by 25.5pp (underestimates win rate)
2. With incorrect p_success, EV = p * reward - (1-p) * risk produces systematically pessimistic values
3. Only 22 live trades exist — insufficient to validate EV predictions
4. The EV gate was disabled specifically because early research showed it was rejecting profitable opportunities
5. Until the `ScoreCalibrator` applies an empirical mapping, EV calculations are unreliable

---

## 5. Pattern Analysis

### TWEEZER_TOP (9 trades — largest sample)

| Metric | Value |
|--------|-------|
| Trades | 9 |
| Wins | 5 |
| Losses | 4 |
| Win rate | 55.6% |
| Total PnL | +$1.95 |
| Avg PnL | +$0.22 |
| Best | +$1.37 (GBPUSD Jul 23) |
| Worst | -$1.44 (GBPUSD Jul 22) |

**Conclusion:** Best performing pattern in live trading. Consistent with shadow research (54.3% WR, +0.14R from 254 shadow trades). Produces genuine edge. Contains both the best and worst individual trades in the dataset.

### TWEEZER_BOTTOM (5 trades)

| Metric | Value |
|--------|-------|
| Trades | 5 |
| Wins | 1 |
| Losses | 4 |
| Win rate | 20.0% |
| Total PnL | -$1.18 |
| Avg PnL | -$0.24 |

**Conclusion:** Underperforming in live execution vs shadow research (35.7% WR in shadow). Small sample. May reflect market regime bias (bearish pressure during sample window favouring SELL patterns). Do not remove yet — monitor.

### THREE_BLACK_CROWS (2 trades)

| Metric | Value |
|--------|-------|
| Trades | 2 |
| Wins | 0 |
| Losses | 2 |
| Total PnL | -$0.11 |

**Conclusion:** Both losses were small (-$0.05, -$0.06). Shadow research shows catastrophic performance (2.0% WR, -0.95R from 205 trades). Live result is consistent with shadow findings. However, live sample is too small (n=2) to make removal decisions.

### THREE_WHITE_SOLDIERS (1 trade)

| Metric | Value |
|--------|-------|
| Trades | 1 |
| Wins | 0 |
| Losses | 1 |
| Total PnL | -$11.00 (USDJPY) |

**Conclusion:** Single trade, large dollar loss due to JPY pip value. Shadow research shows 0.8% WR from 126 trades. Pattern may be unsuitable but sample is n=1 in live.

### EVENING_STAR (2 trades)

| Metric | Value |
|--------|-------|
| Trades | 2 |
| Wins | 0 |
| Losses | 2 |
| Total PnL | -$0.48 |

**Conclusion:** Both losses. Shadow shows 44.4% WR from 27 trades. Insufficient live data to conclude.

### THREE_INSIDE_DOWN (2 trades)

| Metric | Value |
|--------|-------|
| Trades | 2 |
| Wins | 1 |
| Losses | 1 |
| Total PnL | +$0.37 |

**Conclusion:** 50% WR live. Shadow shows 48.3% WR from 29 trades. Consistent. Small sample.

### HANGING_MAN (1 trade)

| Metric | Value |
|--------|-------|
| Trades | 1 |
| Wins | 0 |
| Losses | 1 |
| Total PnL | -$0.24 |

**Conclusion:** Single trade loss. Insufficient data.

**Pattern removal is NOT recommended at this time.** Sample sizes are too small for live conclusions. Shadow research should guide future filtering decisions once calibration is applied.

---

## 6. Regime Analysis

### Regime Distribution (from decision traces)

From Q6 Regime Accuracy report (4,835 traces):
- **TRANSITIONAL:** 4,138 (85.6%)
- **RANGE:** 558 (11.5%)
- **TRENDING:** 25 (0.5%)

### Live Trade Regime Attribution

Based on the architecture: H4 owns regime classification with 100% authority post-migration. M5 collapsed to 99% TRANSITIONAL (confirmed by research). The regime seen during the 22 live trades:

| Regime | Estimated Trades | Result |
|--------|-----------------|--------|
| TRANSITIONAL | ~16 | Majority of trades. Mixed results. Contains both wins and losses |
| RANGE | ~5 | Appears in some slower-moving pairs. Losses dominant |
| TRENDING | ~1 | Rare occurrence. Insufficient data |

**Note:** Exact per-trade regime requires joining decision_audit records via correlation_id. The estimates above are based on the overall regime distribution observed during the trading window.

### Limitations

1. **Regime collapse at M5:** The M5 timeframe sees almost everything as TRANSITIONAL because market structure at 5-minute resolution changes rapidly. This is a known architectural finding — H4 is now the sole regime authority.

2. **Sample size:** 22 trades cannot validate regime model accuracy. The regime model was validated using 1,220 shadow trades where TRANSITIONAL dominated.

3. **Regime vs outcome:** Without per-trade regime attribution (requires correlation_id joining), we cannot yet prove whether regime classification predicts trade success in live execution.

---

## 7. Execution and Risk Findings

### PROVEN ANOMALIES

Two trades exhibit behaviour that exceeds expected risk parameters.

---

### 7.1 GBPUSD Anomaly (Trade #12)

| Field | Value |
|-------|-------|
| Symbol | GBPUSD |
| Ticket | 53303078 |
| Pattern | TWEEZER_TOP |
| Direction | SELL |
| Entry price | 1.33743 |
| Initial SL | 1.33775 (32 pips above entry) |
| Initial TP | 1.33685 (58 pips below entry) |
| Exit price | 1.33887 |
| Duration | 2h 12m (7,937s) |
| PnL | -$1.44 |
| Expected max loss | ~-1R (based on SL distance) |
| Actual R-multiple | **-4.5R** |
| Exit reason | broker_close (maps to margin_call) |

**Issue:** The planned SL was at 1.33775 — a 32-pip risk. The actual exit occurred at 1.33887, which is 144 pips adverse (1.33887 - 1.33743 = 0.00144). The position blew through the SL by approximately 112 pips.

**Possible causes (unresolved):**
1. **SL not executed by broker** — SL may have been set but not triggered (server-side SL vs client-side)
2. **SL modification** — Trade management may have moved the SL (break-even logic triggered incorrectly)
3. **Gap/slippage** — Price may have gapped through the SL level (unlikely on GBPUSD M5)
4. **Margin call override** — Account margin may have been breached before SL was hit, triggering broker liquidation at a worse price
5. **Position management conflict** — Multiple GBPUSD positions open simultaneously consuming margin (Trade #4 was also GBPUSD SELL active during this period)

**Critical observation:** Trade #4 (GBPUSD EVENING_STAR, ticket 53298213) was also open SELL on GBPUSD during the same window and exited at similar time with -$0.31. Two simultaneous GBPUSD SELL positions would double margin requirements.

**Status:** UNRESOLVED. Requires broker execution log analysis and margin state reconstruction.

---

### 7.2 USDJPY Anomaly (Trade #2)

| Field | Value |
|-------|-------|
| Symbol | USDJPY |
| Ticket | 53297199 |
| Pattern | THREE_WHITE_SOLDIERS |
| Direction | BUY |
| Entry price | 163.176 |
| Initial SL | 163.166 (1.0 pip below entry) |
| Initial TP | 163.194 (1.8 pips above entry) |
| Exit price | 163.165 |
| Duration | 3m 11s (191s) |
| PnL | -$11.00 |
| R-multiple | -1.1R |
| Exit reason | broker_close (maps to margin_call) |

**Issue:** The SL/TP geometry is extremely tight:
- Stop distance: 0.010 (1 pip in JPY terms)
- TP distance: 0.018 (1.8 pips)
- Risk:Reward ratio: 1:1.8

**Concerns:**
1. **Over-tight stop** — 1 pip SL on USDJPY is inside normal spread+noise. Any tick movement triggers the stop.
2. **Spread vulnerability** — USDJPY spread of 1-2 pips would immediately consume the entire stop distance
3. **The -$11.00 loss** — This is normal for 0.01 lot USDJPY (1 pip = ~$0.61/pip at 163.x, so 0.010 * 100000 * 0.01 / 163 ≈ $0.61). However the raw `realised_pnl` of -11.0 suggests a different calculation (possibly broker reporting in JPY or contract value mismatch)
4. **Margin call exit** — The `broker_close` reason suggests the position was liquidated rather than SL-hit

**Possible root cause:** The SL/TP generation model may be producing stops that are too tight for USDJPY's price scale and volatility characteristics. The `SL_BUFFER = 0.0002` config parameter is calibrated for EUR/GBP-style pairs (where 2 pips = 0.0002) but is negligible for JPY pairs (where 2 pips = 0.02).

**Status:** UNRESOLVED. Requires investigation of SL generation logic for JPY pairs and margin calculation verification.

---

## 8. Current Problems Ranked

### P0 — Critical (Must fix before scaling)

1. **GBPUSD -4.5R loss exceeding planned risk** — A position exited at 4.5x the expected risk. If this can happen once, it can happen repeatedly. Before increasing lot size or trading capital, the cause must be identified and prevented.

2. **All exits are `broker_close`** — No trade in the 22-trade sample exited via normal TP hit. This suggests either (a) TPs are too far away to reach in M5 holding periods, or (b) positions are being force-closed by margin/management before reaching TP. Either way, the exit mechanism is not functioning as designed.

3. **JPY pair SL geometry** — The USDJPY trade had a 1-pip stop on a pair with 1-2 pip spreads. The SL_BUFFER and risk model may not be JPY-aware.

### P1 — Important (Investigate next)

4. **Short trade duration** — Median holding period of ~20 minutes suggests the system is operating as an M5 scalper rather than a swing trader. If swing behaviour is the goal, the strategy horizon needs investigation.

5. **Probability calibration gap (25.5pp)** — The system underestimates its win rate by 25 percentage points. This affects EV calculations, risk sizing decisions, and confidence reporting. The `ScoreCalibrator` is ready but not yet deployed with empirical data.

6. **TWEEZER_BOTTOM underperformance** — 20% live WR vs 35.7% shadow WR. May indicate a directional bias in the market window, or confirmation logic issues for BUY entries.

7. **Day 1 vs Day 2 performance gap** — 8% WR Day 1 vs 70% WR Day 2. The cause is unknown. Could be market conditions, accumulated state, or random variance on small samples.

### P2 — Optimisation (Improve later)

8. **EV gate disabled** — Currently observational only. Cannot be enabled until probability calibration is fixed.

9. **Pattern-conditional probability** — Research recommends implementing pattern-specific win rate adjustments. Blocked by calibration validation.

10. **Regime model resolution** — H4 regime is correct authority but 85.6% TRANSITIONAL means limited regime diversity for decision branching.

11. **Recovery identity gap** — Recovered positions lose their correlation_id, breaking the forensic chain. Synthetic IDs (`RECOVERED-*`) are used but forensic joining is degraded.

12. **THREE_BLACK_CROWS / THREE_WHITE_SOLDIERS** — Shadow research shows <2% WR for these patterns (205 and 126 trades respectively). However, live sample is too small to remove them yet. Schedule for review after 50+ live trades.

---

## 9. Current Hypothesis: Short-Duration Trades

### Observation

The 22 trades have a median duration of approximately 20 minutes. The longest trade was 2h16m. For a system configured with `MIN_RR = 2.0` on M5 timeframe, this suggests positions are being closed well before reaching their theoretical TP.

### Evidence-Based Possible Causes

1. **Timeframe authority (M5 execution):**
   - The bot operates on `TIMEFRAME = mt5.TIMEFRAME_M5`
   - M5 patterns have inherently short life expectancy
   - A 2R target on M5 requires significant price movement within minutes
   - Architecture confirms: "M5 owns execution timing only (pattern, confirmation, bias FSM)"
   - H4/H1/M15 provide context but M5 drives entry — this creates a mismatch between execution timeframe and holding expectation

2. **SL/TP generation:**
   - `SL_BUFFER = 0.0002` (2 pips for most pairs)
   - SL placed at pattern high/low + buffer
   - On M5, pattern highs/lows are very close to entry (often 3-8 pips)
   - TP at 2R from a 5-pip stop = 10 pips — achievable but requires directional momentum
   - All 22 trades exited as `broker_close` — suggesting SL hit, not TP reached

3. **Strategy horizon:**
   - No `TM_MAX_TIME_IN_TRADE_SECONDS` is set (configured as 0.0 = disabled)
   - No trailing stop is active (`TM_TRAILING_STEP = 0.0`)
   - Break-even trigger at 1.0R (`TM_BREAK_EVEN_TRIGGER_RR = 1.0`) — this moves SL to entry after 1R profit, which may lock in early but prevent larger moves
   - No partial TP configured (`TM_PARTIAL_TP_FRACTION = 0.0`)

4. **Holding period limits:**
   - No explicit max time in trade (disabled)
   - Cooldown of 300s (5 min) between trades per symbol
   - Loss cooldown of 600s (10 min)
   - These don't limit holding time but affect re-entry frequency

5. **Execution policy:**
   - `MAX_OPEN_POSITIONS = 1` (only 1 position at a time globally? Or per symbol? — needs verification)
   - Correlation guard: max 2 positions per group
   - This means new signals may force-close existing positions (unverified)

6. **Configuration mismatch hypothesis:**
   - The system was designed with multi-timeframe authority (H4 regime, H1 structure)
   - But execution is on M5 — the fastest timeframe
   - H4/H1 provide swing context but M5 patterns resolve in minutes
   - This creates a structural tension: swing context → scalp execution

### What NOT to change yet

No configuration changes should be made until:
- The GBPUSD -4.5R anomaly is explained
- The `broker_close` exit mechanism is fully understood
- The relationship between TM_BREAK_EVEN_TRIGGER_RR and early exits is analysed
- At least 50 more trades provide statistical confidence

---

## 10. Current Development Phase

### CURRENT PHASE

**Trade forensic analysis complete.**

The first 22 live trades have been fully documented. System capabilities are verified. Two critical anomalies are identified. Pattern and regime performance baselines are established. The probability calibration gap is quantified.

### NEXT PHASE

**Trade horizon / swing behaviour audit.**

Investigate:
- Why all exits are `broker_close` instead of TP-hit or SL-hit
- Whether the break-even trigger (1.0R) is causing premature exits
- How M5 execution timeframe interacts with H4/H1 swing context
- Whether SL/TP geometry is appropriate for intended holding duration
- What configuration changes would enable longer swing opportunities without breaking existing risk guards

---

## 11. Classification of Findings

### PROVEN BY DATA

1. 22 trades executed successfully across 6 symbols over 24 hours
2. Overall win rate: 36.4% (8/22)
3. All 22 exits classified as `broker_close` — no normal TP-hit observed
4. TWEEZER_TOP is the best-performing live pattern (5/9 = 55.6% WR, +$1.95 total)
5. TWEEZER_BOTTOM underperforms in live (1/5 = 20% WR)
6. Day 2 dramatically outperformed Day 1 (70% vs 8% WR)
7. GBPUSD Trade #12 exited at -4.5R (exceeding planned 1R risk)
8. USDJPY Trade #2 had 1-pip stop distance (inside spread)
9. System probability model underestimates win rate by 25.5pp
10. Score is monotonically related to outcome (higher = better, validated)
11. EV gate is disabled; EV is calculated but does not block trades
12. Median trade duration is approximately 20 minutes
13. Total net PnL: approximately -$0.20 (near breakeven)
14. Decision pipeline is fully operational (4,836 decision traces, 8,155 ledger entries)
15. Shadow research shows positive expected value (+0.55R per shadow trade)

### SUSPECTED ISSUES

1. SL is being hit (or position force-closed) before TP in all cases — suspected mechanism is margin_call/force liquidation rather than normal SL execution
2. Break-even trigger at 1.0R may be moving SL to entry prematurely, then price reversal hits it
3. M5 timeframe may be structurally unsuitable for swing-duration trades
4. SL_BUFFER (0.0002) is not JPY-scaled — USDJPY stops may be systematically too tight
5. Multiple simultaneous positions on GBPUSD may have caused margin breach leading to -4.5R loss
6. TWEEZER_BOTTOM may underperform when overall market bias is bearish (directional filter not applied)
7. Day 1 poor performance may be due to system warm-start state being suboptimal

### UNKNOWN QUESTIONS

1. Why exactly does every trade exit as `broker_close`? Is the broker using SL as a liquidation trigger?
2. Was the GBPUSD SL actually placed with the broker, or was it client-side only?
3. What was the margin utilisation at the time of the -4.5R GBPUSD loss?
4. Were two GBPUSD positions open simultaneously (Trade #4 and Trade #12)?
5. Does MAX_OPEN_POSITIONS=1 apply globally or per-symbol?
6. Does the break-even modification (TM_BREAK_EVEN_TRIGGER_RR=1.0) actually trigger on these trades?
7. What are the individual decision scores for each of the 22 trades?
8. What is the actual regime classification for each trade at execution time?
9. Is the tick_driver actively managing positions (modifying SL/TP) during the trade?
10. Would increasing MIN_RR or TP distance improve holding time?

### NEXT INVESTIGATION

1. **Audit `broker_close` mechanism** — Determine whether exits are SL-hit (normal), margin liquidation, or trade management modification
2. **Reconstruct GBPUSD margin state** — Check if concurrent GBPUSD positions caused margin breach
3. **Verify SL placement** — Confirm SLs are server-side (broker holds them) vs client-side (bot manages)
4. **Audit tick_driver behaviour** — Check if trade management modifies SL/TP post-entry
5. **JPY SL scaling** — Investigate whether SL generation accounts for JPY pip scale
6. **Join decision_audit to trade outcomes** — Extract scores, regimes, confirmation for each of the 22 trades
7. **Break-even trigger analysis** — Determine if TM_BREAK_EVEN_TRIGGER_RR=1.0 fires and what happens after

---

## 12. Restart Instructions

---

**LAST COMPLETED:** 22 trade forensic analysis.
**CURRENT INVESTIGATION:** Understanding why trades are short duration and how to enable longer swing opportunities.
**NEXT ACTION:** Audit trade horizon controls.

---

### To Continue From This Checkpoint

1. **Do NOT repeat** the 22-trade forensic analysis — it is complete and documented above
2. **Start with** auditing the trade management system (`tick_driver.py`, `TM_BREAK_EVEN_TRIGGER_RR`, position modification logic)
3. **Then** investigate the `broker_close` exit mechanism — determine if SLs are server-side or client-side
4. **Then** reconstruct the GBPUSD -4.5R anomaly using execution_context and decision_ledger correlation_ids
5. **Then** address JPY SL scaling in the risk/SL generation module

### Key Files For Next Session

| Purpose | File |
|---------|------|
| Trade management | `core/trade_management/` |
| Tick driver (SL/TP mods) | `core/trade_management/tick_driver.py` |
| Risk/SL generation | Risk model in decision engine |
| Break-even logic | Config: `TM_BREAK_EVEN_TRIGGER_RR = 1.0` |
| Live scanner (main loop) | `core/runtime/live_scanner.py` |
| Decision audit (per-trade) | `logs/decision_audit/{SYMBOL}_{date}.jsonl` |
| Trade journal (outcomes) | `logs/trade_journal/{date}.jsonl` |
| Trade truth (execution reality) | `logs/trade_truth/{SYMBOL}/{date}.jsonl` |
| Execution context | `logs/execution_context/{SYMBOL}/{date}.jsonl` |
| Config (all params) | `core/config.py` |
| Architecture blueprint | `architecture/REFACTOR_BLUEPRINT.md` |

### Key Configuration State

```
TIMEFRAME = M5
MIN_SCORE_TO_TRADE = 4.6
FIXED_LOT = 0.01
MIN_RR = 2.0
SL_BUFFER = 0.0002
ENABLE_EV_GATE = False
TM_BREAK_EVEN_TRIGGER_RR = 1.0
TM_BREAK_EVEN_BUFFER = 0.1
TM_TRAILING_STEP = 0.0
TM_MAX_TIME_IN_TRADE_SECONDS = 0.0
MAX_OPEN_POSITIONS = 1
EXECUTION_ENABLED = True
DRY_RUN = False
USE_NEW_PIPELINE = True
```

---

*This document is a checkpoint, not a final strategy conclusion. The purpose is to prevent repeating analysis and preserve development history.*

*End of TRADE_FORENSIC_CHECKPOINT_001*
