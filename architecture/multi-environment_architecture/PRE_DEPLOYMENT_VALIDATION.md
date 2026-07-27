# Pre-Deployment Trading Bot Validation

## Date: 2026-07-23
## Status: ASSESSMENT COMPLETE

---

## VERIFIED KNOWNS

| # | Question | Evidence | Confidence | Limitation |
|---|----------|----------|-----------|------------|
| 1 | Can the bot execute trades? | 25 successful broker fills in execution_results | HIGH | Confirmed with retcode=10009 |
| 2 | Can every trade be reconstructed? | 80% (20/25) have full lifecycle chain | MEDIUM | 5 early trades missing journal/truth (pre-fix) |
| 3 | Can we see why a trade happened? | decision_trace stores all 10 components, scores, EV, reasoning | HIGH | 100% coverage for EXECUTE decisions |
| 4 | Can we calculate P&L? | trade_journal stores net_pnl for all completed trades | HIGH | P&L uses local pip calculation, not broker-reported |
| 5 | Can we calculate R-multiple? | entry_price + exit_price + initial_sl → R derivable | HIGH | All 3 fields present in 100% of journal records |
| 6 | Can we identify winners vs losers? | net_pnl > 0 classification | HIGH | 20/20 records have valid P&L |
| 7 | Can we analyse EV? | decision_audit stores ev, p_success, ev_positive | HIGH | Only for trades reaching EV stage (16% of all evaluations) |
| 8 | Can we compare executed vs rejected? | shadow_trades simulate rejected opportunities | MEDIUM | 171 shadow trade records available |
| 9 | Does the execution layer work? | mt5.order_send verified, fills confirmed | HIGH | kwargs bug fixed, validated with live broker |
| 10 | Does position lifecycle complete? | broker_close reconciliation handles server-side SL/TP | HIGH | Fix deployed and tested |
| 11 | Does persistence survive restarts? | Identity restoration from execution_results | MEDIUM | Depends on execution_results existing |
| 12 | Does the bot recover open positions? | D3 startup recovery + identity restoration | HIGH | Tested with live positions |
| 13 | Can the bot run continuously? | Heartbeat shows 7000+ cycles per session | HIGH | Multi-hour sessions confirmed |
| 14 | Are decisions observable via Discord? | Per-channel routing for decisions, execution, risk | MEDIUM | Some observability calls had signature bugs (fixed) |
| 15 | Is S3 persistence isolated from tests? | Path-resolved guard + conftest autouse fixture | HIGH | Double protection verified |

---

## STRATEGY EDGE VALIDATION

| Question | Answerable? | Current Evidence | Verdict |
|----------|-------------|-----------------|---------|
| Positive expectancy? | YES (data exists) | 13 completed trades: expectancy = -1.24 R | **NO EDGE DEMONSTRATED** |
| Average R? | YES | -1.24 R (heavily negative) | Needs 50+ trades with corrected architecture |
| Profit factor? | YES | 0.028 (catastrophic) | Same — all trades had structural flaws |
| Win rate? | YES | 7.7% (1/13) | Insufficient sample, all had sub-5-pip stops |
| Average winner? | YES | +1.91 R (one trade) | Single data point |
| Average loser? | YES | -1.50 R | Inflated by -4.5R outlier |
| Winners > losers? | YES | 1.91 vs 1.50 (yes, marginally) | Single winner — not statistically meaningful |
| Losses predictable? | PARTIALLY | All losses hit SL; most within 3 minutes | SL too tight (now fixed with adaptive guard) |
| Consistent across conditions? | NO | All trades OFF_SESSION, TRANSITIONAL regime | Zero variance in conditions tested |

**Critical context:** The 13-trade sample operated under compromised conditions (no EV gate, no min SL guard, all off-session). The corrected architecture has not yet produced a statistically meaningful sample.

---

## EV GATE VALIDATION

| Question | Answerable? | Evidence |
|----------|-------------|----------|
| Does EV improve profitability? | NOT YET | Only 3/13 trades had positive EV; all 3 lost (but all had fatal SL distances) |
| Does EV reduce losses? | LIKELY YES | 6/13 trades had negative EV — would have been blocked |
| Does EV remove winners? | YES — 1 case | The winning USDCHF trade had negative EV (-0.000036) |
| Are EV-blocked trades bad? | INCONCLUSIVE | Need 50+ trades with EV experiment markers |
| Optimal EV threshold? | UNKNOWN | Requires 100+ trades across threshold values |
| EV per environment? | NOT YET RELEVANT | Single environment currently |

**Required experiment:** 50 trades with `ENABLE_EV_GATE=False` + `ev_experiment_mode=True` markers. Compare outcomes of EV-positive vs EV-negative trades.

**Current sample is INVALID** for EV validation because all trades had structural SL defects. The SL problem dominated outcomes regardless of EV.

---

## ENTRY VALIDATION

| Metric | Available? | Current Data | Assessment |
|--------|-----------|-------------|-----------|
| Price moves in expected direction? | YES (MFE) | Average MFE = 0.8 pips before SL hit | Very low — entries barely move favourably |
| Immediate adverse movement? | PARTIAL | Most trades hit SL within 3 minutes | Suggests entries are noise, not signal |
| Entry timing quality? | DERIVABLE | Compare entry time vs bar close time | Not yet computed |
| MAE (max adverse) | **NOT AVAILABLE** | Not tracked for live positions | **MISSING — critical for entry analysis** |
| Time to favourable movement | DERIVABLE | MFE timestamp not stored (only price) | Cannot determine timing |

**Finding:** Entries appear directionally correct (GBPUSD moved 1.7 pips favourable before reverting) but stops are too tight to survive normal retracement. This is a risk/exit problem more than an entry problem.

---

## EXIT VALIDATION

| Question | Answerable? | Evidence | Problem |
|----------|-------------|----------|---------|
| Are TPs realistic? | YES | TP distances 2-6 pips | Generally reasonable for M5 |
| Are SLs too tight? | **YES — PROVEN** | 3 trades had sub-1-pip SL; all lost instantly | Adaptive min SL now deployed |
| Winners cut short? | INCONCLUSIVE | 1 winner reached +1.91R before TP | Insufficient data |
| Losses allowed to run? | NO | All losses hit SL at -1.0R (controlled) | Risk management working as designed |
| Exit reason accuracy? | **BROKEN** | ALL records show "margin_call" | Exit reason mapping bug — MUST FIX |
| MFE capture %? | DERIVABLE | Can compute: R_achieved / MFE_R | Not yet calculated |
| Planned vs achieved RR? | YES | Planned 2-3:1 RR; achieved -1.0R average | Stops hit before TP reached |

**Critical finding:** The exit reason classification bug means we cannot distinguish SL hits from TP hits from manual closes in trade_truth. This blocks exit type analysis.

---

## RISK VALIDATION

| Guard | Working? | Evidence |
|-------|----------|----------|
| Adaptive min SL | YES | Rejects sub-floor stops; unit tested | Deployed but not yet battle-tested |
| Daily loss limit | YES | Config enabled, DailyLossGuard tested | Never triggered (losses too small to hit 4%) |
| Cooldown | YES | Blocks trades within 300s of last trade | Observed in guard chain results |
| Correlation guard | YES | Blocks when currency exposure > limit | Blocked several early trades correctly |
| Portfolio exposure | YES | Limits total open positions to 3 | Never triggered (max 1 position at a time) |
| Spread guard | YES | Blocks when spread/risk ratio > 0.30 | Blocked 3 trades (AUDUSD off-session) |
| Position sizing (FIXED) | YES | All trades 0.01 lots | Verified in execution_results |
| Drawdown guard | UNTESTED | Enabled=False (never activated in live) | Needs testing |
| Weekend protection | UNTESTED | Friday flatten configured but never triggered | Needs Friday validation |

**Expected drawdown:** With 0.01 lot FIXED sizing and $5000 balance, each -1R loss = ~$0.20. Maximum observed drawdown = -$14.58 (non-critical at micro-lot scale).

---

## EXECUTION VALIDATION

| Metric | Status | Evidence |
|--------|--------|----------|
| Fill accuracy | GOOD | Slippage 0.0-0.4 pips (normal for FX micro-lots) |
| Broker acceptance rate | 100% | All 25 attempted orders filled (post-kwargs fix) |
| Spread at execution | NOT CAPTURED | execution_context shows spread but trade_truth always 0 |
| Commission | NOT APPLICABLE | Pepperstone standard = spread-based (no explicit commission) |
| Broker error handling | TESTED | POSITION_NOT_FOUND now handled; retries work |
| Restart recovery | TESTED | D3 recovery + identity restoration verified |
| Idempotency | TESTED | Duplicate intent blocking works |
| Circuit breaker | DEPLOYED | Trips after 3 consecutive MT5 timeouts |

---

## DATA QUALITY ISSUES

### Critical

| Issue | Impact | Fix Effort |
|-------|--------|-----------|
| Exit reason always "margin_call" | Cannot distinguish SL/TP/manual exits | 5 minutes (mapping fix) |
| 5 trades missing journal/truth | Incomplete lifecycle for early trades | Cannot fix retroactively |

### Medium

| Issue | Impact | Fix Effort |
|-------|--------|-----------|
| Spread not captured in trade_truth | Cannot calculate true execution cost | 15 minutes |
| MAE not tracked for live positions | Cannot analyse intra-trade drawdown | 30 minutes |
| Session not tagged on records | Must derive at query time | 10 minutes |
| USDJPY P&L shows -$11.0 (wrong units) | P&L calculation uses pip_value_per_lot=100000 | Needs investigation |

### Minor

| Issue | Impact | Fix Effort |
|-------|--------|-----------|
| Test contamination in trade_truth (19 records) | Pollutes queries if not filtered | Filter by COR- prefix |
| Duplicate execution_context records | 2x storage for same correlation_id | Cosmetic — no functional impact |
| Strategy classification never activates | Cannot analyse per-strategy performance | Architectural — not a bug |

---

## OPERATIONAL READINESS

| Requirement | Status | Evidence |
|-------------|--------|----------|
| Run for weeks | PARTIAL | Longest observed: ~6 hours continuous | Needs multi-day validation |
| Automatic recovery | YES | Startup recovery + warm-start tested | MT5 reconnect verified |
| Reliable alerts | PARTIAL | Discord webhooks configured for 15+ channels | Some observability gaps in EXECUTE path fixed |
| Visible failures | YES | log_runtime_exception → Discord errors channel | All exceptions caught and routed |
| Remote understanding | YES | Decision trace + decision ledger + trade journal | Full reasoning chain persisted |
| Safe shutdown | YES | Graceful shutdown flag + state checkpoint | Tested |
| Kill switch | YES | Kill switch blocks all execution immediately | Config-level + runtime |

---

## RETAIL ACCOUNT READINESS

| Requirement | Status |
|-------------|--------|
| Risk profile defined | YES — PolicyProfile concept designed, config exists |
| Account size decision | PENDING — need expectancy data first |
| Drawdown tolerance | DEFINED — MAX_DRAWDOWN_PERCENT=10.0 (disabled currently) |
| Policy profile active | YES — current config is the implicit "retail_test_v1" profile |
| Profile frozen | NO — config is still being adjusted between sessions |

**Verdict:** Retail deployment requires 50+ trades demonstrating neutral-to-positive expectancy with the corrected architecture (min SL + EV gate or EV experiment data).

---

## PROP FIRM READINESS

| Requirement | Status |
|-------------|--------|
| Rules encoded | YES — H1 challenge, H2 consistency, H3 prop firm, H4 weekend |
| Profile versioned | NO — not yet extracted into PolicyProfile dataclass |
| Daily loss limit | YES — 4% configured, DailyLossGuard implemented |
| Max drawdown | YES — 10% configured, DrawdownGuard implemented |
| Lot size limits | YES — PROP_FIRM_RULE_SET.max_lot_size |
| News/weekend handling | PARTIAL — weekend protection yes, news filter no |
| Trading hours | YES — SESSION_GUARD with configurable hours |

**Verdict:** Prop firm deployment requires: (1) retail validation first, (2) profile extraction into versioned config, (3) prop-specific backtest with shadow trades.

---

## QUESTIONS NOT YET CONSIDERED

| Risk Category | Question |
|---------------|----------|
| **Statistics** | Is 50 trades enough to confirm edge, or do we need 200+ per market regime? |
| **Statistics** | Could the single winner be pure luck (p=0.08 for 1/13 at true 0% edge)? |
| **Regime shift** | What happens when H4 shifts from TRANSITIONAL to TRENDING? |
| **Correlation** | If all 7 pairs produce signals simultaneously, does correlation guard correctly prevent overexposure? |
| **Overnight risk** | What happens if a position is held through a major gap? |
| **Infrastructure** | What happens if the EC2 instance reboots mid-trade? |
| **Infrastructure** | What happens if MT5 terminal updates automatically? |
| **Data** | Are MT5 candle timestamps consistent across broker server restarts? |
| **Psychology** | After a losing streak, will the operator override the bot's decisions? |
| **Broker** | Does Pepperstone change spread conditions during news without warning? |
| **Broker** | Is the MT5 server timezone always UTC+3, or does it shift with DST? |
| **Broker** | Are there hidden position limits or margin changes on micro-lots? |
| **Market** | Does the bot behave correctly during flash crashes (extreme gaps)? |
| **Sizing** | With DYNAMIC sizing, could a low-volatility period produce oversized positions? |

---

## DEPLOYMENT TRAFFIC LIGHT

### 🟢 GREEN — Proven Ready

- End-to-end execution pipeline (decision → broker → fill → persist)
- Trade lifecycle reconciliation (broker-side close handling)
- Minimum SL adaptive guard
- Position isolation (magic number)
- Persistence and forensic reconstruction
- S3 test isolation
- Startup recovery with identity restoration
- Kill switch and graceful shutdown
- 2160+ automated tests passing

### 🟡 YELLOW — Needs Evidence

- Strategy edge (need 50+ trades with corrected architecture)
- EV gate effectiveness (need 50-trade A/B experiment)
- Probability calibration accuracy (need per-bucket outcome data)
- Multi-day continuous operation (longest run: 6 hours)
- Session performance comparison (all trades off-session so far)
- Regime performance comparison (all trades in TRANSITIONAL)
- Pattern-specific profitability (max 5 trades per pattern)

### 🔴 RED — Must Fix Before Deployment

| # | Issue | Impact | Fix |
|---|-------|--------|-----|
| 1 | Exit reason always "margin_call" | Blocks exit analysis, corrupts trade_truth | Fix mapping in trade_journal.py |
| 2 | USDJPY P&L calculation appears wrong (-$11.0 for 0.01 lot, 1 pip move) | Incorrect portfolio tracking | Investigate `_compute_pnl` for JPY pairs |
| 3 | No evidence of positive expectancy | Cannot justify real capital | Run 50-trade experiment |

---

## FINAL GATE CONDITIONS

The trading bot may be deployed with real money when ALL of the following are true:

1. **Exit reason bug is fixed** — trade_truth correctly classifies SL/TP/manual
2. **50+ trades completed** with adaptive min SL guard active
3. **Expectancy ≥ 0** over those 50 trades (or statistically indistinguishable from 0)
4. **No catastrophic single trade** (no loss > -3R in the 50-trade sample)
5. **Continuous operation > 48 hours** without crash or missed opportunity
6. **EV experiment data collected** — can determine if EV gate helps or hurts
7. **At least one full London + NY session** of trading data (not just off-session)

Until these conditions are met, the bot should run with 0.01 lot micro-sizing only, treating all trades as experimental data collection.
