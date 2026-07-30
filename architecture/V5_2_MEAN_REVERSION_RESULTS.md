# V5.2 — Mean Reversion Expression Validation Results

**Date:** 2026-07-29
**Dataset:** 368 V3 execution assessments with full context
**Verdict:** C) Signal is real but driven by outliers and time-unstable — not reliably exploitable

---

## Headline Numbers (before decomposition)

| Configuration | n | WR | EV | CI | Net @15p |
|---|---|---|---|---|---|
| All records | 368 | 46.2% | +0.093R | [+0.004, +0.183] | +0.013R |
| MR score >= 3 | 263 | 49.4% | **+0.236R** | **[+0.121, +0.350]** | **+0.156R** |
| MR score >= 5 | 39 | 64.1% | +0.146R | [-0.082, +0.374] | +0.066R |

**On the surface: CI excludes zero, net positive at all geometries.** This would appear to be Verdict A.

---

## CRITICAL: Why This Is NOT Verdict A

### Time Stability — The Effect Is GONE In Recent Data

| Period | n | WR | EV | CI |
|---|---|---|---|---|
| **Early** | 87 | 55.2% | **+0.753R** | [+0.451, +1.054] |
| Middle | 87 | 43.7% | -0.025R | [-0.107, +0.057] |
| Recent | 89 | 49.4% | -0.014R | [-0.086, +0.057] |

**The entire +0.236R average comes from the EARLY period.** Middle and Recent periods show zero EV. This is the classic signature of a regime-specific effect that has disappeared.

### Symbol Concentration — EURUSD Drives The Result

| Symbol | n | WR | EV |
|---|---|---|---|
| **EURUSD** | 50 | **68.0%** | **+1.301R** |
| USDJPY | 14 | 35.7% | +0.156R |
| AUDUSD | 43 | 51.2% | +0.030R |
| USDCHF | 37 | 54.1% | +0.023R |
| NZDUSD | 40 | 52.5% | +0.021R |
| GBPUSD | 45 | 40.0% | -0.049R |
| USDCAD | 34 | 29.4% | -0.177R |

**EURUSD at +1.301R is a massive outlier** that inflates the aggregate. Without EURUSD, the remaining 213 trades average approximately +0.02R (near zero).

### The Movement Problem Persists

- **70.7% of high-MR trades never reach 0.3R MFE**
- Median MFE is only 0.130R
- Only 19% reach 0.5R, only 10% reach 1.0R
- **Fixed TP at any level is net negative** (Analysis 7 shows all TPs produce negative EV)

This means the positive EV comes from **rare large moves** (runners) not from consistent mean-reversion captures. The simulated TP analysis proves this: no fixed target produces positive EV.

---

## What The Analysis DID Confirm

### 1. The Contrarian Pattern Is Real

| Signal | n | WR | EV |
|---|---|---|---|
| Neutral momentum | 203 | 47.3% | +0.280R |
| Against momentum (contrarian) | 60 | 61.7% | +0.100R |
| WITH momentum | 69 | 44.9% | -0.067R |

The ranking: **NEUTRAL > AGAINST > WITH** confirms V5.1. Trading into neutral momentum is best; trading with momentum is worst.

### 2. Structure Alignment Is Inverted (confirmed)

| Alignment | n | WR | EV |
|---|---|---|---|
| Low structure alignment | 184 | 52.2% | +0.236R |
| High structure alignment | 184 | 40.2% | -0.050R |

### 3. H1 Neutral > With H1 Trend

| H1 Context | n | WR | EV |
|---|---|---|---|
| H1 NEUTRAL | 164 | 45.7% | +0.247R |
| WITH H1 trend | 186 | 45.7% | -0.035R |
| AGAINST H1 trend | 18 | 55.6% | +0.015R |

### 4. No-Pullback Massively Outperforms Pullback

| Context | n | WR | EV |
|---|---|---|---|
| **No pullback** | **66** | **45.5%** | **+0.615R** |
| Pullback active | 302 | 46.4% | -0.021R |

This is counter-intuitive for mean-reversion (you'd expect pullbacks to be the setup). But it suggests the profitable trades are NOT mean-reversion in the classical sense — they're **range expansion** from quiet periods.

### 5. Exit Reason Distribution Tells The Story

| Exit | n | % | WR | EV |
|---|---|---|---|---|
| **max_bars_timeout** | 273 | **74%** | 50.2% | +0.013R |
| stop_loss | 67 | 18% | 9.0% | -0.647R |
| take_profit | 28 | **8%** | 96.4% | **+2.645R** |

**74% of trades time out** — the market simply doesn't move enough. The 8% that hit TP produce massive R (+2.645R) and drive all the EV. This is NOT a consistent mean-reversion edge. It's a rare-event distribution.

---

## The Real Truth Revealed

The V3 system is not a mean-reversion system OR a trend-following system. It is a:

**"Wait in neutral markets, occasionally catch a large move" system.**

- 74% of trades: nothing happens (timeout at ~0R)
- 18% of trades: wrong direction (stop loss at -1R)
- 8% of trades: large move caught (+2.6R)

The expected value is positive because: 0.74 × 0.013 + 0.18 × (-0.647) + 0.08 × 2.645 = +0.096R

But:
- The 8% runner rate is UNPREDICTABLE (AR5, AR6 confirmed this)
- The +0.753R in early period means those runners were concentrated in time
- Middle + Recent = 0R means the runner frequency has dropped
- EURUSD dominated the runners

---

## V5.2 Verdict

### C) Signal is real but too weak/unstable after decomposition

**The system has positive raw EV** — this is confirmed with CI excluding zero. But:

1. **Time-unstable:** Effect lives entirely in early period
2. **Symbol-concentrated:** EURUSD drives the aggregate
3. **Runner-dependent:** 8% of trades create all value
4. **Movement-deficient:** 71% never reach 0.3R MFE
5. **No fixed exit works:** All simulated TPs produce negative EV

The "mean-reversion" label is misleading. The system makes money from **rare expansion events that happen to start from neutral/ranging conditions** — not from consistent price snapping back to a mean.

---

## Implications

| Question | Answer |
|---|---|
| Is the architecture wrong? | No — it correctly identifies neutral/ranging/weak-confirmation conditions |
| Is there a tradeable edge? | Yes, +0.09R raw, but runners are unpredictable |
| Can we filter for runners? | No (AR5, AR6 proved runners are unpredictable from available features) |
| Can regime fix this? | No (all data is already RANGING/NEUTRAL) |
| Can currency strength fix this? | No (V4.4b: conflicts with contrarian timing) |
| What would fix this? | Either: predict WHEN the 8% expansion occurs, OR trade a market with more consistent movement |

---

## Recommended Next Steps

**Option 1: Accept the null hypothesis**
- M5 FX candlestick data does not contain a reliably exploitable edge
- The system has positive raw EV but it cannot be distinguished from zero after costs in recent data
- The research program has been thorough and conclusive

**Option 2: Change market/timeframe**
- Daily timeframe (larger moves, lower cost ratio)
- Indices (higher volatility, trend-following viable)
- Crypto (24h, larger moves, lower cost relative to movement)

**Option 3: Explicit runner targeting**
- Accept 74% timeout rate
- Focus on maximizing runner capture when it occurs
- This requires WIDE stops (20-30p) to survive noise
- Net EV at 20p: +0.175R but only in early period

**Option 4: Hybrid — use the system as a "ready state" detector**
- V3 identifies WHEN the market is capable of producing movement
- Don't trade M5 — use it as a signal for HIGHER timeframe positioning
- When V3 says "neutral + weak + low-alignment": H1/H4 breakout may be imminent
