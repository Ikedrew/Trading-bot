# V8 — Research Gap Audit: Implementation Readiness

---

## 1. Edge Validation Gaps

### 1a. Forward Sample Size

```
Unknown: Whether +0.191R EV survives on truly unseen data
Why it matters: All 150 trades were used in discovery — no pure out-of-sample exists
Minimum evidence required: 200+ trades generated AFTER V7.5 analysis date
Can implementation begin without this? NO
```

### 1b. Market Regime Shift Sensitivity

```
Unknown: Whether the edge persists through bear markets, corrections, and low-volatility regimes
Why it matters: The 150 trades may represent a single macro regime (bull/recovery)
Minimum evidence required: Observations spanning at least one market correction or VIX spike >25
Can implementation begin without this? NO — but paper trading can begin
```

### 1c. Invalidation Threshold

```
Unknown: At what point is the hypothesis rejected?
Why it matters: Without defined stop criteria, losses may continue indefinitely
Minimum evidence required: Define: "If forward n≥100 shows EV<0 or WR<50%, halt"
Can implementation begin without this? NO
```

---

## 2. Strategy Behaviour Gaps

### 2a. Trend Strength Dependency

```
Unknown: Does the edge require strong trends, or does it work in weak trends too?
Risk if unknown: System may trade in choppy-but-classified-as-trending conditions and lose
Research priority: HIGH
```

### 2b. Volatility Regime Dependency

```
Unknown: Does high volatility (VIX>25) help or hurt the signal?
Risk if unknown: Could suffer catastrophic losses during market panic
Research priority: HIGH
```

### 2c. Session Dependency

```
Unknown: Does the edge concentrate in specific hours (US open, core, close)?
Risk if unknown: Trading during low-edge sessions dilutes expectancy
Research priority: MEDIUM
```

### 2d. Edge Decay

```
Unknown: Is the signal degrading over time or stable/improving?
Risk if unknown: Could be implementing a dying signal
Research priority: HIGH (partially answered — V7.5 showed improvement over time, but n is small)
```

### 2e. News/Event Sensitivity

```
Unknown: How does the signal perform around FOMC, NFP, CPI, earnings?
Risk if unknown: Large adverse moves on event days could dominate losses
Research priority: HIGH
```

---

## 3. Regime Dependency Audit

### 3a. Is It Truly Trend-Following?

```
Unknown: The signal was discovered by INVERTING a mean-reversion system on indices.
         It is ASSUMED to be trend-following because inversion on trending markets = follow.
         But it has NOT been validated that:
         - it only works in trends
         - it fails in ranges
         - regime classification would improve it
Risk: The signal may work for reasons other than "following trends"
Classification: Can be learned after deployment (observe regime labels on new trades)
```

### 3b. Does It Survive Ranging Index Markets?

```
Unknown: Indices occasionally range (consolidation, pre-FOMC). Signal behaviour unknown.
Risk: May generate losses during consolidation if traded blindly
Classification: Required before implementation — at minimum, define "don't trade" conditions
```

### 3c. Is Regime Filtering Required?

```
Unknown: Whether adding "only trade when trending" improves or degrades net performance
Risk: Adding a filter reduces sample size; NOT filtering risks ranging-market losses
Classification: Can be learned after deployment — but MONITOR regime distribution
```

---

## 4. Entry Quality Research

### 4a. Entry Timing

```
Unknown: Whether the V3 entry point is optimal or could be improved with limit orders
Risk if unknown: Low — current entry produces positive EV
Research priority: LOW (future optimisation)
```

### 4b. Signal Quality Distribution

```
Unknown: Are all 150 trades equal, or do some subsets carry all the EV?
         (e.g., do "WEAK" vs "VALID" entries matter for indices?)
Risk if unknown: May be trading low-quality signals that dilute edge
Research priority: MEDIUM — but requires V3 pipeline to label index trades (not yet happening)
```

### 4c. Need Optimisation Before Implementation?

```
NO — Current entry produces +0.191R EV. Optimisation is a post-deployment activity.
Entry quality research is useful but not blocking.
```

---

## 5. Exit Model Research

### 5a. Fixed R:R Assumption

```
Unknown: Shadow trades use a fixed 2:1 R:R with timeout. Is this the right exit for indices?
         The data shows:
         - 49% timeout (no resolution within bar limit)
         - Avg winner: +0.764R (less than 2R — most don't reach full TP)
         - Profit factor: 1.66
Risk if unknown: May be leaving money on table OR cutting winners too early
Classification: Future optimisation — current model is net positive
```

### 5b. Trailing Stop Benefit

```
Unknown: Whether trailing stops on index trend trades capture more of the available movement
Risk if unknown: MFE data shows moves of 1.4R average — current captures 0.76R avg win
Classification: Future optimisation — potential +30% improvement but not blocking
```

### 5c. Time-Based Exit

```
Unknown: Whether reducing timeout duration improves or hurts (fewer losers vs missed winners)
Risk if unknown: Low — timeout trades show slight positive EV currently
Classification: Future optimisation
```

### 5d. Critical Before Implementation?

```
NO — Exit model produces positive EV. Optimisation improves but doesn't enable.
```

---

## 6. Risk Model Research

### 6a. Maximum Expected Drawdown

```
Unknown: True max drawdown probability (only observed 8R on 150 trades)
Potential damage: At 0.5% risk/trade, 8R DD = 4% account. Could be worse with bad luck.
Required before live trading: YES — Monte Carlo simulation on observed distribution
```

### 6b. Losing Streak Probability

```
Unknown: Probability of 10+ consecutive losses (observed max = 9)
Potential damage: Psychological failure, risk of increasing size, abandoning system
Required before live trading: YES — calculate from binomial at WR=62.7%
```

### 6c. Position Sizing

```
Unknown: Optimal risk per trade for this specific edge/variance profile
Potential damage: Over-sizing → ruin; under-sizing → insufficient growth
Required before live trading: YES — Kelly criterion calculation + conservative fractional
```

### 6d. Risk of Ruin

```
Unknown: Probability of losing X% of account given observed EV and variance
Potential damage: Total account loss
Required before live trading: YES — calculate from EV=+0.19R, std=~1.1R, WR=63%
```

### 6e. Correlation Between NAS100 and US500

```
Unknown: How correlated are simultaneous positions?
Potential damage: Two positions = effectively one 2x position if correlated
Required before live trading: YES — measure win/loss correlation on same-timestamp trades
```

---

## 7. Execution Reality Research

### 7a. Actual Spread Variability

```
Unknown: Real spread distribution during trading hours for NAS100/US500 on Pepperstone
Must validate before real money? YES
Method: Record spreads during shadow period
```

### 7b. Slippage at Market Entry

```
Unknown: Actual fill quality on NAS100/US500 market orders
Must validate before real money? YES (paper trading phase)
Method: Compare requested vs filled price during paper trading
```

### 7c. Session-Specific Costs

```
Unknown: Whether spread widens materially at US open / during news
Must validate before real money? YES — at least observe during shadow collection
Method: Log spread-at-entry on every shadow trade
```

### 7d. Broker Symbol Availability

```
Unknown: Whether Pepperstone offers NAS100/US500 under these exact names
Must validate before real money? YES — this blocks ALL data collection
Method: Check MT5 Market Watch immediately
```

### 7e. Execution Delay Impact

```
Unknown: Latency between signal and fill on M5 indices
Must validate before real money? PARTIALLY — paper trading reveals this
Method: Timestamp comparison during paper phase
```

---

## 8. Data Collection Requirements

### Required Observations (not yet recorded for index trades):

```
Required observation: regime_at_entry (TRENDING/RANGING/TRANSITIONAL)
Why: Determine if edge is regime-dependent

Required observation: volatility_state (LOW/NEUTRAL/HIGH)
Why: Identify conditions that damage edge

Required observation: session_label (PRE_MARKET/OPEN/CORE/CLOSE)
Why: Identify session concentration

Required observation: spread_at_entry (actual pips)
Why: Validate cost model with real data

Required observation: vix_level (or ATR percentile proxy)
Why: Identify volatility sensitivity

Required observation: htf_trend_strength (0-1)
Why: Determine if strong trends produce better results

Required observation: news_proximity (minutes to next major event)
Why: Identify event risk exposure
```

### Currently NOT recorded for index trades:

The V3 pipeline does NOT produce `execution_assessment` records for NAS100/US500 (V6.3 confirmed this). This means:
- No entry_state labels
- No opportunity_state labels
- No regime/momentum context
- No spread-at-entry data

**This is a blocking gap for understanding WHY the signal works.**

---

## 9. Minimum Implementation Threshold

| Research Item | Mandatory? | Reason |
|---|---|---|
| **Forward validation (n≥200 new trades)** | **YES** | Confirms survival on unseen data |
| **Invalidation criteria defined** | **YES** | Stops indefinite losses if edge dies |
| **Risk of ruin calculation** | **YES** | Prevents account destruction |
| **Position sizing model** | **YES** | Prevents over/under-sizing |
| **Correlation between NAS/US500** | **YES** | Prevents hidden 2x exposure |
| **Broker symbol verification** | **YES** | Blocks all data collection |
| **Spread measurement (real data)** | **YES** | Validates cost assumptions |
| Regime dependency analysis | Determine | Could invalidate if edge is regime-specific |
| Session analysis | No | Can learn during paper trading |
| Exit optimisation | No | Can improve later |
| Entry quality segmentation | No | Requires V3 pipeline on indices (future) |
| News/event sensitivity | No | Can observe during paper phase |
| Trailing stop research | No | Future optimisation |
| Trend strength segmentation | No | Can learn after deployment |

---

## A) Remaining Research Questions

1. Does the edge survive on 200+ truly unseen forward observations?
2. What is the maximum probable drawdown (Monte Carlo)?
3. What is the optimal position size (Kelly/fractional Kelly)?
4. Are NAS100 and US500 trades correlated (concurrent position risk)?
5. Does the edge disappear during ranging/consolidation periods?
6. What are actual spreads/slippage on Pepperstone for these symbols?
7. What invalidation threshold triggers strategy halt?

---

## B) Minimum Evidence Required Before Implementation

1. **200+ new equity-index shadow trades** with positive EV (forward validation)
2. **Defined invalidation rule** (e.g., "halt if rolling-50 EV < 0 for 2 consecutive checks")
3. **Monte Carlo drawdown estimate** from observed distribution
4. **Position sizing rule** (Kelly fraction based on measured EV/std)
5. **Symbol correlation measurement** (from available concurrent trades)
6. **Broker confirmation** that NAS100/US500 exist on Pepperstone MT5
7. **Spread data** from at least 50 observations during shadow collection

---

## C) Implementation Blockers

| Blocker | Status | Resolution |
|---|---|---|
| No forward data exists | **BLOCKING** | Run bot with indices enabled |
| Broker symbol availability unverified | **BLOCKING** | Check MT5 Market Watch |
| No invalidation criteria defined | **BLOCKING** | Define before any execution |
| No position sizing model | **BLOCKING** | Calculate from observed statistics |
| No risk-of-ruin calculation | **BLOCKING** | Compute from EV=+0.19R, std≈1.1R |

---

## D) Can Begin Implementation?

```
SHADOW EXECUTION: YES
  - No real money at risk
  - Collects required forward data
  - Measures actual spreads
  - Tests broker connectivity
  - Builds execution confidence

PAPER TRADING: NO — not yet
  - Requires 200+ forward shadow trades with positive EV
  - Requires invalidation criteria
  - Requires position sizing model

LIVE TRADING: NO — not yet
  - Requires paper trading phase completion (100+ paper trades)
  - Requires all mandatory items resolved
  - Requires net EV confirmation after real costs
```

**Immediate action: Begin shadow execution to unblock all downstream validation.**
