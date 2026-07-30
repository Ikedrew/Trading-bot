# V8.3 — Trade Reconstruction Audit Results

**Date:** 2026-07-27
**Dataset:** 142 reconstructed trades (NAS100: 75, US500: 67)
**Verdict:** THE PROXY IS INVALID — Naive inversion overstates edge by +0.517R. Reconstructed trades show NEGATIVE EV.

---

## THE CRITICAL FINDING

| Method | WR | EV | Status |
|---|---|---|---|
| **Naive proxy (result_r × -1)** | 58% | +0.088R | **INVALID** |
| **RECONSTRUCTED (structural stops)** | **19%** | **-0.430R** | **ACTUAL** |

**The entire V7.1-V8.2 research finding is based on an invalid proxy.**

When actual structural stop/target placement is used for the inverted trade, the EV flips from **positive +0.088R to deeply negative -0.430R**.

---

## What Happened

### The Proxy Assumption:
"If the original SELL lost 1R, then a BUY would have gained 1R."

### The Reality:
When you BUY at a point where V3 identified a SELL opportunity:
- **The nearest structural support (stop placement) is far away** — avg 105 points on NAS100, 23 points on US500
- **The risk (stop distance) is much LARGER** than the original trade's stop
- **The 2:1 target is proportionally further** — requiring more movement
- **83% of trades hit the wider stop** before reaching the distant target

### Why It Fails:

V3 identifies SELL opportunities at **supply zones / resistance levels**. These are places where:
- Nearby resistance above (tight stop for SHORT)
- Open space below (room for target)

When you INVERT to BUY at these same points:
- Resistance above = your TARGET is blocked
- Open space below = your STOP is far away (wide risk)
- You're buying into resistance with a massive stop — terrible geometry

---

## Per-Symbol Results

### NAS100 (n=75)

| Metric | Proxy | Reconstructed |
|---|---|---|
| Win Rate | 56.0% | **17.3%** |
| EV | +0.090R | **-0.480R** |
| Take Profit | — | 13 trades (17%) |
| Stop Loss | — | 62 trades (83%) |
| Avg Stop Distance | — | **104.9 points** |

The original NAS100 trades had ~15-point stops (SHORT below resistance). The inverted BUY stop is 105 points below — **7x wider**. At 2:1 R:R, the target is 210 points away. The market rarely moves 210 points from a supply zone entry point.

### US500 (n=67)

| Metric | Proxy | Reconstructed |
|---|---|---|
| Win Rate | 61.2% | **20.9%** |
| EV | +0.090R | **-0.373R** |
| Take Profit | — | 14 trades (21%) |
| Stop Loss | — | 53 trades (79%) |
| Avg Stop Distance | — | **22.7 points** |

Same pattern. ~80% hit stop. The structural geometry doesn't support the inverted direction.

---

## Why The Proxy Appeared To Work

The naive `result_r × -1` worked as a number because:
1. Original SELL trades that hit STOP (result = -1.0R) become "+1.0R" for the BUY proxy
2. But a REAL BUY at that point would have a DIFFERENT stop distance
3. The market may have moved 15 points up (enough to stop the SHORT) but NOT 210 points up (needed for the BUY target with structural stop)

**The proxy measures "did the market move against the original?" — not "could an inverted trade with proper geometry have captured that movement."**

---

## What This Means For The Research Program

### V7.1-V8.2 Findings: INVALIDATED

Every conclusion based on `result_r × -1` for index trend-following is wrong:
- V7.1 (+0.129R inverted EV) — **INVALID**
- V7.2 (router validation) — **INVALID**
- V7.3 (dynamic router) — **INVALID**
- V7.5 (+0.191R equity index) — **INVALID**
- V8.1 (universe expansion) — **INVALID**
- V8.2 (forward validation) — **INVALID**

### What Remains True:
1. The V3 observation layer correctly identifies supply/demand zones
2. The original FX mean-reversion signal (V3 exec assessments, +0.093R) was measured directly (not inverted) — that remains valid but fragile
3. The architecture is sound — only the INVERSION INTERPRETATION was wrong

### The Fundamental Problem:
A mean-reversion system identifies optimal SELL points (near resistance). Inverting to BUY at resistance is geometrically unsound — you'd need to identify optimal BUY points (near support) instead.

---

## Correct Path Forward

The system cannot simply invert its signals on indices. To trade indices with trend-following:

1. **Identify BUY opportunities directly** — detect demand zones, support levels, pullbacks INTO support
2. **Place stops below the identified support** (tight, structural)
3. **Target the next resistance above** (natural R:R)

This is NOT an inversion — it's a different observation entirely. The V3 pipeline would need to identify BULLISH setups (demand OB entries, bullish FVG, pullback into demand) rather than repurposing BEARISH setups.

---

## V8.3 Verdict

```
THE NAIVE INVERSION PROXY IS INVALID.

The V7-V8 "index trend-following" finding does not survive
reconstruction with actual trade geometry.

Real structural trades at inverted entry points produce:
  WR = 19% | EV = -0.43R | 80% hit stop

The research program must either:
A) Build a NATIVE index trend-following observation (identify BUY setups directly)
B) Return to the validated-but-marginal FX mean-reversion finding
C) Accept the null hypothesis — no exploitable edge exists with current architecture
```
