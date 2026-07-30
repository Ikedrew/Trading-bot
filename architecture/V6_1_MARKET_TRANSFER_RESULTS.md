# V6.1 — Market Transfer Assessment Results

**Date:** 2026-07-29
**Verdict:** B) Architecture transfers but requires market-specific adaptation

---

## Executive Summary

The V3/V5 research program has conclusively identified that the limitation is **the FX M5 market environment**, not the architecture. Index markets (NAS100, US500, XAUUSD) address every structural limitation discovered:

| Limitation | FX M5 | Index (NAS100) | Improvement |
|---|---|---|---|
| Cost/stop ratio | 20% | 10% | 2x better |
| Daily range | 0.5% | 1.8% | 3.6x better |
| Regime variance | 0% (always ranging) | 55% trending | Testable |
| Movement P(>0.5R) | 23% | ~44% (projected) | 2x better |
| Volume data | Unavailable | Available | New information source |
| Structure persistence | Low (choppy) | High (momentum) | Better signals |

---

## Market Fit Scoring

| Market | Score | Best For |
|---|---|---|
| **XAUUSD (Gold)** | **7** | 24h trading, lowest cost ratio (8%), good range |
| **NAS100 (Nasdaq)** | **6** | Highest range (1.8%), strongest momentum |
| US500 (S&P 500) | 5 | Most liquid, most predictable structure |
| FX Major (current) | 0 | Too high cost, too low movement |

---

## Projected Performance

Using conservative assumptions (20% adaptation discount, sqrt movement scaling):

| Market | Cost/Stop | Projected Raw EV | Net EV | Movement | Viability |
|---|---|---|---|---|---|
| NAS100 | 10% | +0.141R | +0.041R | 44% | MARGINAL → PROMISING |
| XAUUSD | 8% | +0.129R | +0.049R | 41% | MARGINAL → PROMISING |
| US500 | 10% | +0.115R | +0.015R | 36% | MARGINAL |
| FX Major | 20% | +0.074R | -0.126R | 23% | NOT VIABLE |

---

## Critical Insight: Architecture Expression Changes

In FX, the V3 system is **contrarian** (mean-reversion in a ranging market):
- NEUTRAL momentum = best
- Low structure alignment = best
- WEAK (early) entries = best

In indices, the same architecture may become **trend-following** (pullback entries in trending markets):
- WITH momentum = potentially best (inverted from FX)
- High structure alignment = potentially best (clear trend)
- WEAK timing = still valid (early pullback entry)

**The same building blocks — structure detection, zone identification, timing classification — express differently depending on market microstructure.**

---

## Transfer Plan

### Minimal changes required:
1. Add symbols to config (US500, NAS100, XAUUSD)
2. Adapt pip→point conversion
3. Adjust ATR scaling thresholds
4. Configure session timing (US market hours)
5. Run V3 shadow pipeline in observation-only mode

### What DOES NOT change:
- Market structure detection (BOS, CHoCH, swing)
- Multi-timeframe analysis (H4→H1→M15→M5)
- Institutional zone identification (OB, FVG)
- Entry timing classification
- Shadow outcome tracking
- Research pipeline

### New capabilities available:
- Real volume data (not available in FX)
- VIX/volatility context
- Opening gap analysis
- Session-specific behaviour (open, core, close)

---

## Risk Assessment

| Risk | Level | Mitigation |
|---|---|---|
| Architecture overfitted to FX | HIGH | Shadow-only first, no execution |
| False confidence from projections | HIGH | Actual data required before conclusions |
| Index spread variation | MEDIUM | Measure actual cost distribution |
| Session limitation (6.5h vs 24h) | MEDIUM | Fewer but potentially higher-quality opportunities |
| Gap risk | MEDIUM | No overnight positions initially |
| Different structure behaviour | MEDIUM | Let research pipeline discover, don't assume |

---

## Recommended V6.2

**"Add NAS100/US500 to shadow observation. Run V3 pipeline for 2-4 weeks. Measure directional accuracy, movement probability, and cost efficiency against the FX baseline."**

Success criteria:
- Direction accuracy > 53% (FX = 50.7%)
- P(>0.5R movement) > 35% (FX = 23%)
- Raw EV > +0.15R (FX = +0.09R)
- Net EV > +0.05R after costs

If these thresholds are met → the market IS the variable, and index-focused development is justified.
If not → the architecture has fundamental limits regardless of market.
