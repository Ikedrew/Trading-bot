# V7.2 — Market Policy Router Validation Results

**Date:** 2026-07-27
**Dataset:** 4,895 total trades (4,665 FX + 230 INDEX)
**Verdict:** B) Policy separation exists for indices — but FX shadow trades are unreliable baseline

---

## Key Results

### The Router Works — But FX Shadow Trade Quality Is Low

| Policy | n | WR | EV | CI |
|---|---|---|---|---|
| Universal (current) | 4,895 | 35.7% | +0.002R | [-0.031, +0.036] |
| **ROUTED** (FX rev + IDX trend) | 4,895 | 36.8% | **+0.014R** | [-0.019, +0.047] |

Improvement: +0.012R — modest on the combined portfolio.

### INDEX Component Is Consistently Positive

| Symbol | Policy | n | WR | EV |
|---|---|---|---|---|
| **NAS100** | TREND | 66 | **59.1%** | **+0.199R** |
| **US500** | TREND | 77 | **62.3%** | **+0.134R** |
| **XAUUSD** | TREND | 87 | **60.9%** | **+0.062R** |

All 3 index symbols positive under trend-following. Time-stable across all three periods.

### FX Component Is Unstable

| Symbol | Policy | n | WR | EV |
|---|---|---|---|---|
| EURUSD | REVERSION | 1,163 | 49.4% | **+0.628R** |
| GBPUSD | REVERSION | 756 | 37.4% | +0.148R |
| USDCAD | REVERSION | 382 | 44.0% | -0.047R |
| USDCHF | REVERSION | 602 | 29.7% | -0.148R |
| AUDUSD | REVERSION | 619 | 29.7% | -0.362R |
| NZDUSD | REVERSION | 788 | 23.0% | -0.420R |
| USDJPY | REVERSION | 355 | 25.1% | -0.394R |

**Only 2/7 FX symbols positive** (EURUSD dominates massively). The FX shadow trade data has severe symbol concentration — EURUSD at +0.628R props up the entire FX portfolio while 5 other pairs are deeply negative.

---

## Critical Insights

### 1. INDEX Trend-Following Is The Strongest Signal Found

The inverted index signal shows:
- **All 3 symbols positive** (NAS100, US500, XAUUSD)
- **Time-stable** (Early +0.097, Middle +0.175, Recent +0.105)
- **60%+ WR** across all symbols
- **EV +0.125R** aggregate

This is MORE consistent than anything found in FX across the entire research program (AR1-V5.2).

### 2. FX Shadow Trades ≠ FX V3 Execution Assessments

The V3 exec assessments showed EV=+0.093R with 46.2% WR (n=368), but the full FX shadow trade population (n=4,665) shows only +0.009R with 35.7% WR. This means:
- V3 filtering (WEAK+INTERESTING+context) adds ~+0.08R of value
- The unfiltered FX signal is near-zero
- EURUSD concentration drives the FX positive result

### 3. Per-Trade Context Routing (within FX) Does NOT Help

When testing "should we follow momentum on FX?":
- NEUTRAL momentum: KEEP original (+0.280R) ← best
- AGAINST momentum: KEEP original (+0.100R)
- WITH momentum: INVERT (+0.067R vs -0.067R original) ← only case where inversion helps

But combining them makes things WORSE (routed FX EV=+0.061 vs original +0.093). The context router adds complexity without net benefit within FX.

### 4. The Simple Instrument-Class Router Is Correct

The routing rule is trivially simple:
```
IF index/commodity → FOLLOW the signal (trend)
IF FX → FADE the signal (reversion)
```

No per-trade context analysis needed. The instrument's structural behaviour (trending vs ranging) is the determinant, not the per-bar market state.

---

## Time Stability

### INDEX (inverted/trend policy):
| Period | n | EV | WR |
|---|---|---|---|
| Early | 76 | +0.097R | 64.5% |
| Middle | 76 | +0.175R | 67.1% |
| Recent | 78 | +0.105R | 51.3% |

**Consistently positive across all periods.** Recent WR drops slightly (51.3%) but EV remains positive.

### FX (original/reversion):
| Period | n | EV | WR |
|---|---|---|---|
| Early | 1,555 | -0.215R | 20.5% |
| Middle | 1,555 | +0.304R | 42.7% |
| Recent | 1,555 | -0.063R | 43.5% |

**Highly unstable.** Early period massively negative, middle massively positive, recent slightly negative. FX signal is not time-stable in the shadow trade population.

---

## V7.2 Verdict

### B) Policy separation EXISTS and is validated for indices — FX requires V3 filtering

**The index trend-following finding is the most robust result in the entire research program:**
- 3/3 symbols positive
- 3/3 time periods positive
- 60%+ win rate
- +0.125R aggregate EV
- Simple implementation (invert the signal)

**But the combined router verdict is D because:**
- FX shadow trades (unfiltered) are near-zero and unstable
- 5/7 FX symbols are NEGATIVE under reversion policy
- The FX positive result depends on EURUSD concentration + V3 filtering
- Overall portfolio CI still includes zero

---

## Recommended Next Steps

### IMMEDIATE (highest confidence):
1. **Focus development on INDEX trend-following** — most validated signal
2. **Implement inverted policy for NAS100/US500/XAUUSD** in shadow mode
3. **Continue collecting index data** to strengthen statistical power (n=230 → target 500+)

### MEDIUM-TERM:
4. **Separate FX assessment** — the FX signal only works WITH V3 filtering (exec assessments, not raw shadow trades)
5. **Don't combine into single portfolio** — treat as independent research tracks
6. **V7.3**: Walk-forward validation on index data with explicit cost modelling

### KEY INSIGHT:
The research has identified TWO separate findings:
- **FX**: V3-filtered signal is marginally positive (+0.09R) but fragile, symbol-concentrated, time-unstable
- **INDEX**: Inverted signal is robustly positive (+0.125R), symbol-diversified, time-stable

The index finding is STRONGER and should be the primary development direction.
