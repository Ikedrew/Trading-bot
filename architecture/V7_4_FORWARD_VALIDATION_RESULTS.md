# V7.4 — Index Trend Policy Forward Validation Results

**Date:** 2026-07-27
**Dataset:** 238 index shadow trades (70/30 pseudo-forward split: 166 discovery + 72 forward)
**Verdict:** B) Promising — forward EV positive but marginally net-zero after costs

---

## Forward Performance Summary

| Dataset | n | WR | EV | Avg Win | Avg Loss | CI |
|---|---|---|---|---|---|---|
| Discovery (70%) | 166 | 65.1% | +0.149R | +0.665R | -0.812R | [+0.012, +0.285] |
| **Forward (30%)** | **72** | **52.8%** | **+0.099R** | +0.880R | -0.774R | [-0.140, +0.338] |
| All combined | 238 | 61.3% | +0.134R | +0.721R | -0.798R | [+0.014, +0.253] |

**Forward EV remains POSITIVE (+0.099R)** but with reduced WR (52.8% vs 65.1%) and wider CI that includes zero.

---

## Symbol Validation (Forward Set)

| Symbol | n | WR | EV | Positive? |
|---|---|---|---|---|
| **NAS100** | 23 | **65.2%** | **+0.381R** | **YES** |
| US500 | 23 | 56.5% | +0.068R | YES |
| XAUUSD | 26 | 38.5% | -0.122R | **NO** |

**2/3 symbols positive.** NAS100 is the strongest (+0.381R). XAUUSD turns NEGATIVE in forward period — the commodity behaves differently from indices in recent data.

---

## Cost-Adjusted Performance

| Metric | Value |
|---|---|
| Gross EV (forward) | +0.099R |
| Average cost per trade | 0.086R |
| **Net EV** | **+0.013R** |

**Barely positive after costs.** The edge is razor-thin at the portfolio level.

Per-symbol net:
- NAS100: **+0.281R net** (strongly positive)
- US500: -0.012R net (breakeven)
- XAUUSD: -0.202R net (negative — should be excluded)

---

## Risk Behaviour

| Metric | Forward Set |
|---|---|
| Max drawdown | 8.44R |
| Max consecutive losses | 10 |
| Win/Loss ratio | 1.14 |
| Worst single loss | -3.000R |
| P90 loss | -2.000R |

**Risk is substantial.** 8.44R max drawdown on 72 trades means significant variance. The 10-consecutive-loss streak would be psychologically challenging in live trading.

---

## Stability

### Forward set halves (both positive):
| Half | n | WR | EV |
|---|---|---|---|
| First | 36 | 52.8% | +0.092R |
| Second | 36 | 52.8% | +0.106R |

**Both halves positive and nearly identical** — good sign for stability within the forward period.

### Full dataset thirds (all positive):
| Period | n | WR | EV | CI |
|---|---|---|---|---|
| Period 1 | 79 | 64.6% | +0.110R | [-0.071, +0.291] |
| Period 2 | 79 | 65.8% | +0.157R | [-0.059, +0.373] |
| Period 3 | 80 | 53.8% | +0.134R | [-0.088, +0.357] |

**All three periods positive.** WR degrades from 65% → 54% but EV remains above +0.10R throughout.

---

## V7.4 Verdict

### B) Promising — forward EV positive but costs consume most of the edge

**What survived forward validation:**
- ✓ Forward EV positive (+0.099R gross)
- ✓ NAS100 strongly positive (+0.381R)
- ✓ Both forward halves positive
- ✓ All three dataset thirds positive
- ✓ WR above 50% (52.8%)

**What degraded:**
- ✗ WR dropped from 65% → 53% (still above 50%)
- ✗ CI includes zero [-0.140, +0.338]
- ✗ XAUUSD turned negative (-0.122R)
- ✗ Net EV after costs is only +0.013R (razor-thin)
- ✗ Max drawdown is 8.44R (high variance)

**Critical insight:** The signal is positive but the **variance is high relative to the edge**. This means:
- Statistical confidence is low (n=72 is underpowered)
- A few bad trades can wipe the edge for extended periods
- NAS100 is the clear best instrument; XAUUSD may not belong

---

## Recommended Actions

1. **Continue collecting** — n=72 forward is insufficient. Target n=200+ forward.
2. **Focus on NAS100** — strongest and most consistent signal (+0.381R forward)
3. **Reconsider XAUUSD** — negative in forward period, may not be a true "index" in behaviour
4. **Implement shadow execution** — track inverted signal in real-time without capital
5. **Re-validate at n=500** with proper walk-forward (train on first 300, test on last 200)

### Production Readiness Criteria (not yet met):
- Forward n ≥ 200
- Forward CI excludes zero
- Net EV after costs > +0.03R
- Max drawdown < 5R per 100 trades
- WR remains > 55%
- At least 2/3 symbols independently positive
