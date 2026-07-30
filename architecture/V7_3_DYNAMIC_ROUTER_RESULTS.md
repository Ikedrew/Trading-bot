# V7.3 — Dynamic Policy Router Validation Results

**Date:** 2026-07-27
**Dataset:** 368 FX exec assessments (with context) + 233 INDEX shadow trades
**Verdict:** B) Symbol router sufficient — INDEX trend-following is the primary value; dynamic FX routing adds marginal value but is time-unstable

---

## Key Results

### Analysis 1: Which Features Predict Policy Preference?

| Feature | Reversion EV | Trend EV | Better Policy |
|---|---|---|---|
| **Momentum: NEUTRAL** | **+0.280R** | -0.280R | **REVERSION** |
| Momentum: WITH trade | -0.067R | **+0.067R** | **TREND** |
| Momentum: AGAINST trade | +0.100R | -0.100R | REVERSION |
| Struct align: LOW (<0.5) | **+0.321R** | -0.321R | **REVERSION** |
| Struct align: HIGH (>0.8) | -0.072R | **+0.072R** | **TREND** |
| Entry: WEAK | +0.020R | -0.020R | REVERSION |
| Entry: VALID | -0.112R | **+0.112R** | **TREND** |
| Macro: NEUTRAL | **+0.247R** | -0.247R | **REVERSION** |
| Macro: BEARISH | -0.047R | +0.047R | TREND |

**Clear pattern:** REVERSION works when signals are NEUTRAL/LOW/WEAK. TREND works when signals are HIGH/VALID/DIRECTIONAL. This confirms V5.1: the system is contrarian — but only works in neutral conditions.

---

### Analysis 2: Router Comparison

| Router | n | WR | EV | CI |
|---|---|---|---|---|
| A: All reversion (baseline) | 368 | 46.2% | +0.093R | [+0.004, +0.183] |
| B: Invert WITH-momentum | 368 | 47.8% | +0.118R | [+0.029, +0.207] |
| C: Skip WITH-momentum | 299 | 46.5% | +0.130R | [+0.022, +0.239] |
| **D: Only NEUTRAL momentum** | **203** | **47.3%** | **+0.280R** | **[+0.139, +0.420]** |
| E: WEAK + NEUTRAL | 73 | 42.5% | -0.010R | [-0.122, +0.102] |

**Router D (NEUTRAL momentum only) is the clear winner on FX** — CI excludes zero, EV = +0.280R. But it reduces n from 368 to 203.

### Combined Portfolio (FX + INDEX):

| Configuration | Combined EV | n |
|---|---|---|
| Symbol-static (all FX rev + IDX trend) | +0.109R | 601 |
| Dynamic B (FX flip momentum + IDX trend) | +0.124R | 601 |
| **Dynamic D (FX neutral-only + IDX trend)** | **+0.201R** | **436** |
| Dynamic E (FX WEAK+neutral + IDX trend) | +0.099R | 306 |

---

### Analysis 3: The INDEX Signal Is Robust

| Period | n | Inverted EV | WR |
|---|---|---|---|
| Early | 77 | +0.109R | 64.9% |
| Middle | 77 | +0.157R | 66.2% |
| Recent | 79 | +0.133R | 53.2% |

**All three periods positive, all above +0.10R.** This is the most time-stable finding in the program.

### The FX Signal Is NOT Robust

| Period | n | Reversion EV | NEUTRAL-only EV |
|---|---|---|---|
| Early | 122 | +0.339R | +0.939R |
| Middle | 122 | -0.044R | -0.052R |
| Recent | 124 | -0.013R | -0.053R |

**FX reversion only works in the EARLY period.** Middle and Recent show zero/negative EV — including the NEUTRAL-momentum filter. The +0.280R aggregate for Router D is driven entirely by early data.

---

## V7.3 Verdict

### B) Symbol router sufficient — dynamic routing adds complexity without time-stable benefit

**Evidence:**

1. **INDEX trend-following is the only TIME-STABLE positive signal:**
   - All periods positive (+0.109 to +0.157R)
   - All symbols positive (NAS100, US500, XAUUSD)
   - Simple implementation (invert signal direction)
   - No per-trade context needed

2. **FX dynamic routing (NEUTRAL momentum) looks good in aggregate (+0.280R) but:**
   - Early period drives the result (+0.939R)
   - Middle and Recent are both negative (-0.05R)
   - The improvement disappears in recent data
   - This is the same time-instability found in V5.2

3. **The simple instrument-class router IS the correct answer:**
   - INDEX → follow signal (consistently +0.13R across time)
   - FX → the signal is marginal and time-dependent regardless of filtering

---

## Architecture Recommendation

```
PRODUCTION ROUTER (validated):
┌──────────────────────────────────────────────────────────────────┐
│ PRIMARY TRACK: INDEX TREND-FOLLOWING                              │
│   Instruments: NAS100, US500, XAUUSD                             │
│   Policy: FOLLOW V3 signal direction                             │
│   Expected EV: +0.13R | WR: 60% | Time-stable: YES              │
│   Status: VALIDATED for shadow execution                         │
│                                                                  │
│ SECONDARY TRACK: FX MEAN-REVERSION (observation only)            │
│   Instruments: EURUSD, GBPUSD                                    │
│   Policy: FADE V3 signal, only in NEUTRAL momentum               │
│   Expected EV: uncertain (time-unstable)                         │
│   Status: CONTINUE OBSERVING — do not allocate capital           │
└──────────────────────────────────────────────────────────────────┘
```

---

## What The Research Program Has Concluded

After V1 → V7.3 (pattern strategy → context signals → architecture validation → market transfer → policy routing):

| Finding | Confidence | Action |
|---|---|---|
| FX M5 candlestick edge | LOW (time-unstable, symbol-concentrated) | Observe only |
| INDEX trend-following (inverted V3) | **HIGH** (time-stable, symbol-diverse) | **Develop** |
| Dynamic per-trade routing | LOW (no improvement over symbol-static) | Not needed |
| Observation architecture | **VALIDATED** (detects direction in both markets) | Keep |
| V3 pipeline integrity | **VALIDATED** (93.4% outcome linking) | Keep |
