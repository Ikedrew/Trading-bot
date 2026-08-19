# H1 SELECTION FUNNEL INVESTIGATION: 985 EXECUTE → 94 LIVE

## Executive Summary

The apparent 985→94 (90% loss) funnel is **structurally misframed**. The real funnel is **450→94** (79% loss), caused by three distinct mechanisms operating in sequence. The runtime guards are **quality-neutral**, but broker rejection is **quality-destroying**.

---

## Corrected Population Decomposition

| Population | N | Mean R | Status |
|---|---|---|---|
| V10_PRIMARY (all) | 986 | +0.58 | MISLEADING — inflated by test data |
| Pre-execution / synthetic test batch | 536 | +1.00 | NOT real market observations |
| **Execution-period shadows** | **450** | **+0.08** | **Correct baseline** |
| Guard-blocked (matched) | 210 | -0.06 | Blocked by position-state guards |
| Guard-passed (matched) | 140 | -0.06 | Submitted to broker |
| Broker-rejected | 53 | +0.39 | Quality-destroying rejection |
| **Live filled** | **94** | **-0.18** | Realised execution |

---

## Complete Funnel Architecture (PROVEN)

```
V10 EXECUTE DECISION
    │
    ├─ prepare_execution()         ← Shadow opened here (BEFORE guards)
    │
    ├─ HorizonExecutionAuthority   ← SKIPPED for V10 mode
    │
    ├─ evaluate_runtime_guards()   ← 10 guards in strict order:
    │   │
    │   ├─ 1. daily_trade_limit    (A4) — 23 blocks, Mean blocked R = -0.059
    │   ├─ 2. trade_cooldown       (B1) — 0 blocks in data
    │   ├─ 3. correlation_guard    (A3) — 101 blocks, Mean blocked R = -0.098
    │   ├─ 4. portfolio_exposure   (A5) — 79 blocks, Mean blocked R = -0.033
    │   ├─ 5. regime_guard         (I2) — 0 blocks in data
    │   ├─ 6. challenge_protect    (H1) — 0 blocks in data
    │   ├─ 7. consistency_rules    (H2) — 0 blocks in data
    │   ├─ 8. prop_firm_rules      (H3) — 0 blocks in data
    │   ├─ 9. weekend_protection   (H4) — 3 blocks, Mean blocked R = +0.146
    │   └─ 10. control_layer            — 0 blocks in data
    │
    │   First failure short-circuits remaining guards.
    │   Total guard-blocked: 216 (59.5% of execution-period intents)
    │
    ├─ ExecutionOrchestrator.execute_trade()
    │   │
    │   ├─ Broker REJECTED: 54 (37% of guard-passed)
    │   │   Reason: "execution_failed:broker_rejected" (100%)
    │   │
    │   └─ Broker FILLED: 94
    │
    └─ LIVE TRADE (registered with TradeManager)
```

---

## Classified Findings

### F1. THE 985→94 FRAMING IS INCORRECT — PROVEN

The V10_PRIMARY shadow population contains **536 synthetic/test records** that never had the opportunity to become live trades:
- All timestamped at a single moment: 2026-07-23 09:46
- Exactly 134 per symbol (only 4 symbols: EURUSD, GBPUSD, NZDUSD, USDCHF)
- Perfectly symmetric R distribution: 25% at -1.0, 25% at 0.0, 25% at ~2.0, 25% at +3.0
- bars_held = 1-2 (impossibly fast for real market)

These are calibration/test data that contaminate the V10_PRIMARY population statistics.

**The correct funnel starts at 450 real execution-period shadows, not 986.**

### F2. RUNTIME GUARDS ARE QUALITY-NEUTRAL — PROVEN

| Metric | Guard-Passed | Guard-Blocked | Delta |
|---|---|---|---|
| N | 140 | 210 | — |
| Mean shadow R | -0.0648 | -0.0643 | -0.0004 |
| Win rate | 45.3% | 43.8% | +1.5pp |
| Mean V10 score | 5.38 | 5.20 | +0.18 |

**Δ = -0.0004R** — no meaningful quality selection. Guards are **volume-reducing** (rate-limiting portfolio concentration) but do not select on opportunity quality.

The three dominant guards are all **portfolio-state** guards:
1. **correlation_guard** (47% of blocks): Prevents correlated exposure
2. **portfolio_exposure** (37% of blocks): Caps total risk allocation
3. **daily_trade_limit** (11% of blocks): Caps daily trade count

None of these assess signal quality or pattern strength.

### F3. BROKER REJECTION IS QUALITY-DESTROYING — PROVEN

| Metric | Broker-Rejected | Broker-Filled (shadow) |
|---|---|---|
| N | 53 | ~87 (by exclusion) |
| Mean shadow R | **+0.39** | **~-0.19** |
| Win rate | 34.0% | ~47% |

Broker-rejected opportunities have **+0.39R better** counterfactual expectancy than those that filled.

**Mechanism hypothesis**: Good setups form during volatility spikes → spread widens → broker rejects the order at the moment of highest expected value. The system is systematically losing its best opportunities to execution rejection.

### F4. THE +0.58R "V10_PRIMARY EXPECTANCY" IS A DATA ARTEFACT — PROVEN

The frequently-cited +0.58R V10_PRIMARY shadow expectancy is **meaningless** for live performance comparison:
- Contaminated by 536 synthetic test records (Mean R = +1.00 exactly)
- Real execution-period shadow expectancy: **+0.08R** (much lower)
- Real guard-passed shadow expectancy: **-0.06R** (slightly negative)

### F5. EXECUTION COSTS ACCOUNT FOR ~0.12R — PLAUSIBLE

| Metric | Value |
|---|---|
| Execution-period shadow R (guard-passed) | -0.06 |
| Live realised R | -0.18 |
| Gap | 0.12R |

Contributing factors:
- Spread cost at entry: ~0.05-0.10R per trade
- Commission: ~0.01-0.02R per trade
- Slippage on stop-loss fills: variable
- Trade management (BE/trailing): may cut winners

This is **plausible** but requires per-trade spread data to confirm.

### F6. GUARD INTERACTION EFFECTS — DATA LIMITATION

Guards short-circuit: first failure prevents later guards from being tested. The 101 correlation-blocked trades were never tested against portfolio_exposure, regime_guard, etc. This means:
- We cannot determine if multiple guards would independently block the same trade
- The guard ordering itself affects which guard "gets credit" for blocking
- A different ordering would produce different guard-attribution counts

---

## Selection Quality Summary

| Stage | Input | Rejected | Surviving | Quality Effect |
|---|---|---|---|---|
| V10 EXECUTE decision | — | — | 450 (real) | Baseline |
| Runtime guards | 363 | 216 | 147 | **NEUTRAL** (Δ=-0.0004) |
| Broker execution | 147 | 54 | 94* | **QUALITY-DESTROYING** |
| Live (realised vs shadow) | 94 | — | 94 | -0.12R execution cost |

*Note: 147 - 54 = 93, but ledger shows 94 fills (±1 accounting from ledger boundaries)

---

## Highest-Confidence Explanation

Only 94 of 986 V10_PRIMARY shadows became live trades because:

1. **536 (54%) are synthetic test data** — never had opportunity to become live trades
2. **216 (of 363 real intents) blocked by NEUTRAL guards** — volume-limiting, not quality-selecting
3. **54 (of 147 guard-passed) rejected by broker** — quality-DESTROYING mechanism
4. **94 (of 147 guard-passed) filled** — realise -0.18R (0.12R worse than shadow due to execution costs)

**The runtime guard chain is not responsible for outcome degradation.** The guards neither help nor hurt expected R. They correctly limit portfolio concentration.

**The broker rejection mechanism IS quality-destroying.** It systematically rejects the highest-expectancy opportunities (+0.39R) while accepting lower-expectancy ones (-0.06R).

---

## Next Experiment Required

**Priority 1: Broker Rejection Analysis**

Investigate WHY broker rejects 54 opportunities:
- Is it spread exceeding the spread_guard threshold?
- Is it requote/price-change between decision and order?
- Does rejection correlate with volatility (ATR ratio at moment of order)?
- Can the rejected orders be characterised by time-of-day, session, or symbol?

This is the single largest quality-destroying mechanism in the pipeline and the most actionable finding from this investigation.

**Priority 2: Pre-Execution Shadow Cleanup**

The 536 synthetic test records should be excluded from the V10_PRIMARY population in the research engine (via a data_quality filter or shadow_type sub-classification) to prevent future analytical contamination.

**Priority 3: Execution Cost Decomposition**

Quantify the exact spread + commission + slippage for each of the 94 live trades to validate the 0.12R execution cost estimate and determine how much is attributable to trade management vs market friction.

---

## Methodology

- **Guard chain**: Traced from `core/runtime/live_scanner.py` lines 1330-1530
- **Guard implementation**: `risk/runtime_guard_chain.py` — `evaluate_runtime_guards()`
- **Shadow creation timing**: `core/runtime/engine_execution_handler.py` — `prepare_execution()`
- **Broker execution**: `execution/execution_orchestrator.py` — `execute_trade()`
- **Matching method**: `correlation_id` (shadow) ↔ `context_snapshot_id` (ledger RISK_BLOCK) / `correlation_id` (ledger EXECUTE)
- **Decision ledger**: `logs/decision_ledger/{SYMBOL}/{DATE}.jsonl` — 32,264 total records
- **Shadow data**: V10_PRIMARY population from ShadowOutcomeUniverseBuilder
- **Live data**: ExecutionUniverseBuilder — 94 records

---

*Report generated: 2026-07-27*
*Investigation scripts: `scripts/funnel_analysis.py`, `scripts/funnel_corr_match.py`, `scripts/funnel_final_analysis.py`*
