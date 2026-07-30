# V9 — Observation-to-Decision Translation Research Results

**Date:** 2026-07-27
**Dataset:** 368 V3 execution assessments (real trades, real geometry, real outcomes)
**Finding:** The observation layer is valid but the market produces insufficient movement for cost-viable trading at M5 FX

---

## The Definitive Picture

### What Actually Happens After V3 Observations (n=368):

| Behaviour | Count | % | Avg R | Description |
|---|---|---|---|---|
| **RANGE_FAILURE** | **239** | **65%** | -0.007R | Market didn't move |
| REVERSAL | 41 | 11% | -1.000R | Signal wrong, hit stop |
| CONTINUATION | 25 | 7% | +2.920R | Signal right, hit TP |
| PARTIAL_CONTINUATION | 24 | 7% | +0.468R | Moved right but timed out |
| PARTIAL_REVERSAL | 21 | 6% | -0.496R | Moved wrong but not SL |
| CHOP | 18 | 5% | +0.170R | Moved both ways |

**65% of the time, the market simply DOES NOT MOVE.** This is the core problem — not direction accuracy, not signal quality, not architecture design.

---

## Section 3: What Predicts Continuation vs Reversal?

### CONTINUATION (signal was correct, TP hit — 25 trades):

| Feature | % of Winners | Baseline | Lift |
|---|---|---|---|
| **Low struct alignment (<0.5)** | **92%** | 36% | **2.58x** |
| **HTF NEUTRAL** | **92%** | 45% | **2.06x** |
| **Momentum NEUTRAL** | **96%** | 55% | **1.74x** |
| Open space | 96% | 73% | 1.31x |
| WEAK entry | 12% | 47% | 0.25x (UNDERREPRESENTED) |

### REVERSAL (signal was wrong, hit SL — 41 trades):

| Feature | % of Losers | Baseline | Lift |
|---|---|---|---|
| **Low struct alignment (<0.5)** | **73%** | 36% | **2.06x** |
| **HTF NEUTRAL** | **73%** | 45% | **1.64x** |
| Open space | 88% | 73% | 1.20x |

### The Paradox:

**Low structure alignment and HTF NEUTRAL predict BOTH winners AND losers with high lift.** These contexts produce MORE MOVEMENT (both directions) — which means more TPs AND more SLs. They don't predict DIRECTION, they predict MOVEMENT.

This explains the V5.1/V5.2 findings: low alignment showed positive EV because it has more TPs (at 2:1 R:R, you need fewer TPs than SLs to be positive). But it's not a directional signal — it's a volatility/movement signal.

---

## Section 6: Exploitable Subsets

| Configuration | n | WR | EV | Net @12% | Viable? |
|---|---|---|---|---|---|
| ALL (baseline) | 368 | 46.2% | +0.093R | -0.027R | NO |
| WEAK entry only | 174 | 49.4% | +0.020R | -0.100R | NO |
| WEAK + NEUTRAL momentum | 73 | 42.5% | -0.010R | -0.130R | NO |
| WEAK + INTERESTING | 146 | 50.7% | +0.035R | -0.085R | NO |
| **Inside zone + WEAK** | **40** | **62.5%** | **+0.183R** | **+0.063R** | **MARGINAL** |
| Non-RANGE (moved meaningfully) | 129 | 43.4% | +0.278R | +0.158R | YES (but unpredictable) |

**Only ONE subset survives costs: WEAK + Inside zone (n=40, net +0.063R).** But n=40 is underpowered and its CI includes zero.

The "Non-RANGE" subset (+0.158R net) is profitable but you can't KNOW in advance which trades will produce movement and which will timeout.

---

## The Core Truth (Post V1-V9)

```
The system's EV comes from a TINY number of trades:
  - 25 trades (7%) produce +2.92R avg → contributes +73R total
  - 41 trades (11%) lose -1.00R avg → costs -41R total
  - 302 trades (82%) contribute approximately zero

NET: +73R - 41R + (302 × ~0) ≈ +32R → +0.093R per trade average

COST at 12%: 368 × 0.12 = -44R

NET AFTER COST: +32R - 44R = -12R → NEGATIVE

The edge does NOT survive transaction costs.
```

---

## Strategy Family Assessment

| Strategy Family | Applicable? | Evidence |
|---|---|---|
| Trend continuation | NO | Only 7% of trades continue — can't predict which |
| Mean reversion | PARTIALLY | 46% WR suggests mild reversion from zones works |
| Breakout continuation | NO | Low struct alignment predicts movement, not direction |
| Liquidity sweep reversal | INSUFFICIENT DATA | No sweep classification in current data |
| Range trading | NO | 65% of trades ARE range-bound (can't exploit) |
| Momentum expansion | NO | Momentum WITH trade is WORST performer |

---

## V9 Final Verdict

```
OBSERVATION LAYER: VALID
  - Correctly identifies structural context
  - Detects zones, structure, momentum, regimes
  - Produces slightly-above-random directional signal (46% vs 50%)

DECISION LAYER: FUNCTIONAL BUT MARGINAL
  - CI barely excludes zero [+0.004, +0.183]
  - Best subset (inside zone + WEAK): n=40, net +0.063R
  - Not robust enough for production

COST BARRIER: UNSOLVED
  - 12-20% per-trade cost consumes the +0.093R edge
  - Net EV is negative or approximately zero after costs
  - No context filter reliably produces net-positive EV with adequate sample

MOVEMENT PROBLEM: UNSOLVED
  - 65% of trades produce no meaningful movement
  - Cannot predict WHICH trades will move before entry
  - The 7% that reach TP are indistinguishable from the 65% that timeout

STATUS: INSUFFICIENT
  - The observation layer works but the M5 FX market does not produce
    enough exploitable movement relative to transaction costs
  - No decision policy transformation solves this — the limitation
    is market microstructure, not architecture
```

---

## Recommended Direction

The research program (V1-V9) has exhaustively determined:

1. **The observation architecture is sound** — it detects real market structure
2. **The directional signal is real but thin** — 46% WR, +0.09R EV before costs
3. **Transaction costs consume the edge** — no subset reliably nets positive
4. **The movement problem is the fundamental barrier** — 65% of trades don't move
5. **The index inversion was invalid** (V8.3) — structural geometry doesn't support it

**Options:**
- A) Accept null result for M5 FX with current information sources
- B) Investigate higher timeframes (H1/H4) where cost ratio is lower
- C) Investigate the "inside zone + WEAK" subset (n=40, net +0.063R) with more data
- D) Build native index/equity observation (detect BUY opportunities at support, not invert SELL opportunities at resistance)
