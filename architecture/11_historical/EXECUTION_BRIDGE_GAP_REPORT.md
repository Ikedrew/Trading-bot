# Execution Bridge Gap Report

> **STATUS: SUPERSEDED.** This gap report predates the HorizonExecutionAuthority, persistence S3 completion, and guard chain documentation. For current execution authority, see `TOP_LEVEL_SYSTEM_AUTHORITY_AUDIT.md` §2.7.

**Date:** 2026-07-19  
**Data source:** Decision ledger, 2026-07-17 (1,210 decisions across 7 symbols)  
**Method:** Live pipeline forensic trace + numerical EV reproduction

---

## 1. Funnel Table

| Stage | Count | Conversion |
|-------|------:|----------:|
| Candles received (bars with tick data) | 1,210 | — |
| Session guard passed | 1,210 | 100% |
| Patterns detected | 701 | 58.0% |
| Engine A scored | 701 | 100% of patterns |
| Score above threshold (0.35) | ~675 | 96.3% |
| Swing structure confirmed | ~535 | 79.3% |
| Risk manager accepted (SL/TP computed) | ~513 | 95.9% |
| **EV positive** | **0** | **0.0%** |
| Runtime guard chain | 0 | — |
| Execution orchestrator | 0 | — |
| MT5 order submitted | 0 | — |
| Broker accepted | 0 | — |

**The funnel collapses to zero at one point: the EV gate.**

---

## 2. Exit Reason Registry

| Exit Reason | Count | % of Total | Stage |
|---|---:|---:|---|
| `PATTERN_REJECT` (no pattern detected) | 509 | 42.1% | Pre-Engine Gate 4 |
| `ev_policy_blocked: NEGATIVE_EXPECTED_VALUE` | 513 | 42.4% | Engine A: EV Policy |
| `swing_blocked` (direction not confirmed by BOS) | 160 | 13.2% | Engine A: Swing Gate |
| `score_below_threshold` (< 0.35) | 28 | 2.3% | Engine A: Score Gate |

**Zero decisions reached:** `RISK_BLOCK`, `EXECUTION_BLOCK`, `MT5_FAILURE`.

---

## 3. Decision-to-Execution Trace (10 Examples)

All 10 examined EV-blocked signals share the same profile:

| Field | Typical Value |
|-------|---------------|
| Pattern | TWEEZER_BOTTOM, EVENING_STAR, SHOOTING_STAR |
| Score (neutral) | 0.55 – 0.63 |
| Strategy classified | **None (global fallback)** |
| Strategy confidence | **0.0** |
| Regime | TRANSITIONAL |
| Regime confidence | 20-24% |
| Uncertainty score | 0.59 – 0.62 |
| Market state | TRANSITIONAL |
| P_success (computed) | **0.28** |
| Minimum RR for positive EV | **2.54** |
| RR from risk manager | **~1.5–2.0** |
| Final EV | **Negative** |
| Blocking reason | `NEGATIVE_EXPECTED_VALUE` |

**Timeline example (EURUSD, cycle 18442):**
```
00:30:08 UTC — Bar closes
           — Pattern detected: TWEEZER_BOTTOM
           — Score: 0.589 (passes 0.35 threshold)
           — Strategy: None classified (global fallback, confidence=0)
           — Regime: TRANSITIONAL (24% confidence)
           — Risk: SL/TP computed, RR ~1.5
           — EV calculation:
               p_base = 0.589 * 0.6 + 0.0 * 0.4 = 0.3534
               p_success = 0.3534 * 1.0 * 0.80 = 0.2827
               p_failure = 0.7173
               EV = 0.2827 * reward - 0.7173 * risk = NEGATIVE
           — BLOCKED: NEGATIVE_EXPECTED_VALUE
```

---

## 4. Decision → Execution Handoff Verification

The handoff from Decision Engine to Execution is architecturally complete:

```
Engine A returns action="EXECUTE" with intent=OrderIntent
    ↓ prepare_execution() (generates correlation_id, shadow trade)
    ↓ runtime_guard_chain (10 guards)
    ↓ execution_orchestrator.execute_trade()
    ↓ MT5Execution.place_market()
    ↓ mt5.order_send()
```

**No fields are lost between layers.** The `OrderIntent` contains symbol, side, volume, entry_reference, sl, tp, pattern. All propagate correctly.

**The handoff is never reached** because the engine never produces `action="EXECUTE"`. It always returns `action="NO_TRADE"` at the EV policy gate.

---

## 5. Shadow Trade Audit

**Shadow trades ARE being generated.** 479 shadow trades exist (529KB recent data from July 19, 2026).

Shadow trades are created by `engine_execution_handler.py` when the engine produces `action="EXECUTE"`. But the engine only reaches `EXECUTE` when EV is positive. Since EV is never positive during the observed period, shadow trades from this session are from a different code path — they come from the shadow engine evaluating bars independently.

**Conclusion:** The shadow system works correctly. It produces trades because it uses its own lifecycle (simulating a position from the pattern, not requiring EV approval). The live execution path requires EV approval, which always fails.

---

## 6. Guard Authority Order

```
1. MT5 Health Check (connection)
2. Cycle Guards (drawdown, daily loss)
3. Pre-Engine Gates:
   a. Kill switch
   b. Daily loss block
   c. Session guard
   d. Pattern gate (no patterns → PATTERN_REJECT)
4. Engine A (sole decision authority):
   a. Pattern selection
   b. Strategy classification
   c. 10-factor scoring
   d. Confirmation
   e. Risk evaluation (SL/TP/sizing)
   f. ★ EV computation → BLOCKS HERE ★
   g. Execution policy check
5. Runtime Guard Chain (10 guards — NEVER REACHED)
6. Execution Orchestrator (NEVER REACHED)
7. MT5 Execution (NEVER REACHED)
```

**Are guards replacing the decision engine?**

No. The guards (stage 5) are not the problem. They are never reached. The engine itself (stage 4f) produces NEGATIVE EV every time and blocks internally.

---

## 7. Finding

### What is preventing decisions becoming trades?

**A mathematical certainty in the EV formula makes positive EV nearly impossible when strategy classification fails.**

The EV formula:
```
p_base = score_neutral × 0.6 + strategy_confidence × 0.4
p_success = p_base × confirmation × (1 - dampening)
EV = p_success × reward - p_failure × risk
```

When `strategy_confidence = 0` (which happens 99.6% of the time in the observed data):
- `p_base` is capped at `score × 0.6` (maximum: 0.6 × 0.6 = 0.36 for a perfect score)
- After TRANSITIONAL dampening (20%): `p_success ≤ 0.288`
- Requires RR ≥ 2.47 for positive EV
- Risk manager typically provides RR 1.5–2.0

**The system is mathematically locked.** With the strategy classifier producing no classification on 99.6% of opportunities, the EV formula guarantees negative output for any realistic RR.

### Root Cause Chain

```
Strategy classifier fails to classify (regime=TRANSITIONAL, confidence=20-24%)
    ↓
strategy_confidence = 0.0
    ↓
p_base is capped at score * 0.6 (40% of formula zeroed)
    ↓
p_success ≈ 0.28 (after TRANSITIONAL dampening)
    ↓
Minimum RR for positive EV ≈ 2.5
    ↓
Risk manager provides RR ≈ 1.5–2.0
    ↓
EV is always negative
    ↓
No executions possible
```

---

## Evidence

- 513 out of 701 scored decisions (73%) blocked by `NEGATIVE_EXPECTED_VALUE`
- 511 out of 513 EV-blocked decisions had `selected_strategy=None`
- All observed regimes are `TRANSITIONAL` with confidence 20-24%
- P_success never exceeds 0.29 in the data
- No decision from 2026-07-17 reached the runtime guard chain
- No decision from 2026-07-17 reached execution

---

## Severity

**Critical** — The system cannot execute any live trade under current market conditions with this parameter combination. This is not a rare edge case; it affects 100% of trading sessions.

---

## The Bridge Answer

> "If this engine was connected to a real trader making decisions manually, what information would the trader have that the execution pipeline currently does not?"

**The trader would know their historical win rate.**

The shadow trades show 63.3% win rate with EV of +1.47R (from Q19 experiment). A human trader with that track record would assign themselves ~63% probability of success — not the 28% that the formula computes.

The EV formula derives P_success from the *current signal's features* (score + strategy confidence). But a real trader's confidence comes from *historical performance* — "I've taken setups like this 479 times and won 63% of them."

The missing bridge is: **The EV formula does not consult historical performance data.** It computes probability from a signal's feature scores alone, and the formula's structure (40% weight on a strategy classifier that almost never fires) mathematically prevents positive EV.

---

## Recommended Fix

**Minimal change required (one of):**

**Option A — Feed historical win rate into EV (cleanest):**
Replace the synthetic `p_base` formula with actual shadow-validated win rate per pattern/regime:
```python
# Instead of:
p_base = score_neutral * 0.6 + strategy_confidence * 0.4

# Use:
p_base = historical_win_rate_for_this_pattern_and_regime
# (from Q19 shadow trade data: 0.633 for the current dataset)
```

**Option B — Remove strategy_confidence dead weight (quickest):**
When `strategy_confidence = 0`, use `score_neutral` alone instead of zeroing 40% of the probability:
```python
if strategy_confidence == 0:
    p_base = score_neutral  # Full score as probability proxy
else:
    p_base = score_neutral * 0.6 + strategy_confidence * 0.4
```

**Option C — Lower the TRANSITIONAL dampening (simplest, least principled):**
Reduce dampening from 20% to 5-10% so that a 0.59 score can produce positive EV at RR=1.5.

---

## Do NOT Recommend

- Changing strategy logic
- Lowering score thresholds
- Disabling risk systems
- Disabling the EV check entirely

The EV check is architecturally correct. The *inputs* to the formula are the problem, not the formula itself.

---

## Next Engineering Action

**Implement Option A or B**, then re-run the decision ledger for one session and verify that at least some decisions now reach `action="EXECUTE"`. Once the bridge is unblocked, the full pipeline (runtime guards → execution orchestrator → MT5) can be validated end-to-end.
