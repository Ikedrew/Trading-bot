# V4.1 — Cross-Market Intelligence Value Analysis

**Date:** 2026-07-29
**Status:** CANNOT EXECUTE — required data does not exist in the current system

---

## Pre-Experiment Assessment

### Data Availability Audit

| Data Source | Available in MT5? | Currently Collected? | Required For |
|---|---|---|---|
| DXY (USD Index) | Sometimes (broker-dependent) | **NO** | USD pair direction |
| US 10Y Yield | No | **NO** | Yield-FX relationship |
| S&P 500 / NASDAQ | Sometimes (as CFD) | **NO** | Risk sentiment |
| VIX | Rarely | **NO** | Volatility regime |
| Gold (XAUUSD) | Usually | **NO** | Safe-haven flows |
| EUR strength index | Not directly | **NO** | Currency decomposition |
| Cross-pair rates | **YES** | **YES** (7 pairs) | Currency strength derivation |

### What IS Available

The system currently trades 7 FX pairs: EURUSD, GBPUSD, USDJPY, USDCHF, USDCAD, AUDUSD, NZDUSD.

These 7 pairs can be used to DERIVE some cross-market information:

| Derivable Feature | Method | Quality |
|---|---|---|
| **USD strength** | Average performance of USD in all USD pairs | GOOD — 6 USD pairs |
| **EUR strength** | EURUSD vs basket | LIMITED — only 1 EUR pair |
| **GBP strength** | GBPUSD vs basket | LIMITED — only 1 GBP pair |
| **JPY strength** | USDJPY vs basket | LIMITED — only 1 JPY pair |
| **AUD/NZD correlation** | AUDUSD vs NZDUSD | GOOD — highly correlated |
| **Risk-on/off proxy** | AUDUSD + NZDUSD vs USDJPY + USDCHF | MODERATE |
| **Pair divergence** | Individual pair vs USD average | GOOD |

### What IS NOT Available

- Real DXY data (not currently fetched)
- Yield data (not accessible via MT5 standard symbols)
- Equity index data (not in current symbol list)
- VIX (not available)
- Order flow / depth of market (MT5 has limited DOM)
- News events (no feed connected)

---

## What CAN Be Tested With Current Data

### Currency Strength from Cross-Pairs

Using the 7 pairs already collected, we can derive:

```
USD strength = average R-change across all USD pairs
    (EURUSD inverted + GBPUSD inverted + USDJPY + USDCHF + USDCAD + AUDUSD inverted + NZDUSD inverted)
```

**This is the ONLY genuinely new information source available without adding infrastructure.**

### The Research Question Becomes

> "Does the relative strength of the USD (derived from cross-pair performance) improve direction prediction on individual pairs beyond 50.7%?"

### Why This Might Work

If EURUSD is going up AND all other USD pairs confirm USD weakness:
- The move has fundamental support (USD selling broadly)
- vs. EURUSD going up but USD is strong elsewhere (EUR-specific move, may reverse)

This is **genuinely different information** from single-pair candlestick structure because it measures the CAUSE of movement (currency demand/supply) rather than the EFFECT (price pattern).

---

## Feasibility Assessment

### Can We Build This?

| Requirement | Feasible? | Effort |
|---|---|---|
| Fetch M5 candles for all 7 pairs simultaneously | **YES** — already doing this | Zero |
| Compute USD strength per bar | YES — simple derivation | Low |
| Compare individual pair direction vs USD direction | YES — comparison logic | Low |
| Link to existing V3 outcomes | YES — same timestamps | Low |
| Statistical analysis | YES — existing framework | Low |

### Sample Size

All 7 pairs × same time period = potentially 7× more data points for currency strength analysis. But the key question is whether ALIGNMENT between pair direction and USD strength predicts outcomes.

---

## Recommended Approach

### Phase 1: Currency Strength Derivation (implementable NOW)

1. For each M5 bar, compute USD strength from all 7 pairs
2. Classify: USD STRENGTHENING / WEAKENING / NEUTRAL
3. For each V3 shadow assessment, record whether the trade direction ALIGNS with USD trend

### Phase 2: Alignment Test

Compare:
- Trades aligned with broad USD trend → WR and EV
- Trades opposing broad USD trend → WR and EV
- Trades during neutral USD → WR and EV

### Phase 3: Validation

If alignment improves WR above 53%+ → investigate further
If alignment shows no improvement → conclude cross-pair data doesn't help

---

## Critical Limitation

Even if currency strength improves direction from 50.7% to 53%, the AR9 finding still applies:

| WR | Needed for profitability at 20p stop |
|---|---|
| 50.7% (current) | NOT VIABLE |
| 53% | Still NOT VIABLE (need ~55%+ with tight stops) |
| 55% | MARGINAL at 20p, might work at 30p |
| 60% | VIABLE at most geometries |

**The improvement would need to be DRAMATIC (50.7% → 55%+) to change the fundamental conclusion.** Small improvements in direction accuracy don't overcome the cost structure.

---

## V4.1 Verdict

### CANNOT EXECUTE as specified (DXY, yields, equities not available)

### CAN execute a LIMITED version using cross-pair currency strength

### Honest assessment of value

| If currency strength improves WR to... | Implication |
|---|---|
| 51-52% | Confirms cross-market info exists but insufficient |
| 53-54% | Interesting but still below profitability threshold |
| **55%+** | **First potentially viable improvement** |
| No improvement | Cross-pair information also insufficient |

### Recommendation

**Build the currency strength derivation as a V4 research feature** — it's low effort, uses existing data, and tests a genuinely new information source. But set expectations clearly: even a positive finding may not be sufficient for profitability given the AR series conclusions.

The system needs to move from 50.7% to approximately 55-60% direction accuracy (or find much lower cost markets) before ANY configuration becomes reliably tradeable. Whether currency strength can provide that 5-10% improvement is the right next question to answer.

---

## Implementation Path (if proceeding)

1. **Create `core/market_intelligence/currency_strength.py`** — derives USD/EUR/GBP/JPY/AUD/NZD/CAD strength from the 7 existing pairs
2. **Add to V3 shadow observer** — record alignment per assessment
3. **Collect 200+ observations** with alignment data
4. **Run AR-style analysis** — does alignment improve direction beyond 50.7%?

Estimated effort: 1-2 hours implementation + days of data collection.
