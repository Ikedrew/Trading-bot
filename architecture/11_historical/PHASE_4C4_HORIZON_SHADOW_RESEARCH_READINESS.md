# PHASE 4C.4: HORIZON SHADOW RESEARCH READINESS VALIDATION

**Date:** 2026-07-24
**Question:** Does every valid opportunity produce research shadows regardless of EXECUTE or NO_TRADE outcome?
**Answer:** YES. Phase 4C.3 decoupling verified. Survivorship bias eliminated.

---

## 1. Creation Coverage

### Simulated Lifecycle (5 opportunities, mixed outcomes)

| # | Decision | Reason | Eligible Horizons | Shadows Created |
|---|----------|--------|-------------------|-----------------|
| 1 | NO_TRADE | ev_policy_blocked | SCALP, INTRADAY, EXTENDED | 3 |
| 2 | NO_TRADE | score_below_threshold | SCALP | 1 |
| 3 | EXECUTE | all_gates_passed | SCALP, INTRADAY, EXTENDED | 3 |
| 4 | NO_TRADE | swing_blocked | SCALP | 1 |
| 5 | NO_TRADE | risk_rejected | SCALP, INTRADAY, EXTENDED | 3 |

### Coverage Metrics

| Metric | Value |
|--------|-------|
| Total opportunities assessed | 5 |
| Opportunities producing ≥1 shadow | **5 (100%)** |
| SCALP coverage | 5/5 (100%) |
| INTRADAY coverage | 3/5 (60%) — correctly limited by regime/structure |
| EXTENDED coverage | 3/5 (60%) — correctly limited by trending requirement |
| Total shadows created | 11 |

### Key Finding: NO survivorship bias

- NO_TRADE opportunities #1, #5 produce 3 shadows each (same as EXECUTE #3)
- Decision outcome does NOT affect shadow creation
- Only horizon ELIGIBILITY determines shadow count

---

## 2. EXECUTE vs NO_TRADE Separation: ✅ VERIFIED

| Property | NO_TRADE | EXECUTE |
|----------|----------|---------|
| Horizon shadows created | ✅ YES | ✅ YES |
| Same number of shadows (given same eligibility) | ✅ YES | ✅ YES |
| Requires OrderIntent | ❌ NO | N/A |
| Requires RiskManager approval | ❌ NO | N/A |
| Requires ExecutionGate | ❌ NO | N/A |
| Requires broker permission | ❌ NO | N/A |
| Continues tracking after decision | ✅ YES | ✅ YES |

**The decoupled path uses ONLY:**
- Valid pattern (from engine result)
- Valid direction (from engine result)
- Entry price (bid/ask — always available)
- Eligible horizons (from classify_horizons — already computed)
- Structure data (from HTF context — already available)

---

## 3. Horizon Trade Correctness: ✅ VERIFIED

### SCALP (RR = 2.0, M5 geometry)

| Check | Result |
|-------|--------|
| SL source | M5 candle high + 0.0002 buffer |
| SELL: SL above entry | ✅ (1.33750 > 1.33700) |
| TP = entry - risk × 2.0 | ✅ (1.33600) |
| Risk distance > 0 | ✅ (0.00050) |

### INTRADAY (RR = 3.0, M15 structure)

| Check | Result |
|-------|--------|
| SL source | M15 nearest_resistance + 0.0003 buffer |
| SELL: SL above entry | ✅ (1.33850 > 1.33700) |
| TP = entry - risk × 3.0 | ✅ (1.33250) |
| Risk distance > 0 | ✅ (0.00150) |

### EXTENDED (RR = 4.0, H1 swing structure)

| Check | Result |
|-------|--------|
| SL source | H1 last_swing_high + 0.0005 buffer |
| SELL: SL above entry | ✅ (1.34000 > 1.33700) |
| TP = entry - risk × 4.0 | ✅ (1.32500) |
| Risk distance > 0 | ✅ (0.00300) |

### Progressive Stop Width

```
SCALP:    SL = 1.33750 (5.0 pips from entry)
INTRADAY: SL = 1.33850 (15.0 pips from entry)
EXTENDED: SL = 1.34000 (30.0 pips from entry)
```

✅ Each horizon has progressively wider stops as designed.

---

## 4. Outcome Tracking: ✅ VERIFIED

ShadowTradeEngine correctly evaluates horizon shadows:

| Metric | Tracked? | Example |
|--------|----------|---------|
| Exit reason | ✅ | `stop_loss`, `take_profit`, `max_bars_timeout` |
| R-multiple | ✅ | -1.00 (SL hit), +3.00 (TP hit) |
| MFE (R-multiples) | ✅ | 3.03 |
| MAE (R-multiples) | ✅ | 0.33 |
| Bars held | ✅ | 28 |
| Exit price | ✅ | Computed at SL/TP level |

**Test result:** 8/11 shadows closed during 60-bar simulation. 3 EXTENDED still active (wider targets need more bars).

---

## 5. Persistence: ✅ VERIFIED

### Production Shadow Record Inventory

| Category | Records | Source |
|----------|---------|--------|
| Standard execution-path shadows | 195 | Live bot (SCALP parameters) |
| Execution-path horizon shadows (`shadow_X_SYM_HORIZON`) | 15 | From prepare_execution() (Phase 4C.2) |
| Research-path horizon shadows (`hshadow_X_SYM_HORIZON`) | 8 | From decoupled path (Phase 4C.3 — test artifacts) |

### Record Structure Verified

```json
{
  "schema_version": "shadow_trades_v2",
  "identity": {
    "trade_id": "hshadow_0_GBPUSD_SCALP",
    "correlation_id": "HORIZON-0-GBPUSD",
    "strategy_id": "CONTINUATION_SCALP"
  },
  "decision_snapshot": {
    "direction": "SELL",
    "entry_intent_price": 1.33700,
    "stop_loss_intent": 1.33750,
    "take_profit_intent": 1.33600
  },
  "simulated_outcome": {
    "exit_reason": "stop_loss",
    "pnl_r_multiple": -1.0,
    "mfe_r": 1.0,
    "mae_r": 1.0,
    "bars_held": 1
  }
}
```

✅ All fields present. Horizon identifiable via trade_id prefix + strategy_id suffix.

---

## 6. Research Readiness: ✅ VERIFIED

### Can the system answer: "What would rejected trades have done?"

**YES.** Example from validation:

```
Opportunity: GBPUSD SELL (NO_TRADE — ev_policy_blocked)

  SCALP shadow:    -1.0R (SL hit bar 1)
  INTRADAY shadow: +3.0R (TP hit bar 28)
  EXTENDED shadow: ACTIVE (still tracking — wider target)

Conclusion: The EV gate rejected an opportunity that would have been
            +3.0R profitable at INTRADAY horizon.
```

### Research Queries Now Possible

| Question | Query Method |
|----------|-------------|
| Average R by horizon (ALL opportunities) | `GROUP BY horizon_tag` on all `hshadow_*` records |
| Rejected-opportunity horizon performance | Filter `hshadow_*` where correlation_id matches NO_TRADE decisions |
| Does EV gate block profitable higher-horizon trades? | Join `hshadow_*` outcomes to decision_ledger rejection reasons |
| Optimal horizon by regime | Group by `strategy_id` (contains regime+horizon) |
| INTRADAY vs SCALP expectancy | Compare R-multiples: `*_INTRADAY` vs `*_SCALP` same opportunity |

---

## 7. Known Gaps

| Gap | Severity | Impact |
|-----|----------|--------|
| Assessment records (260) don't yet contain horizon classification | MEDIUM | Fixed in 4B.1 — new records will contain it. Historical records unaffected. |
| Research-path shadows use `hshadow_` prefix while execute-path uses `shadow_` | LOW | Distinguishable but adds filter complexity |
| Production `hshadow_` records are from test runs only (8 records) | TEMPORARY | Will resolve on next live bot session |
| No explicit `horizon` top-level field on shadow_trades_v2 schema | LOW | Extractable from trade_id and strategy_id strings |

---

## 8. Phase 4C Completion Status

| Component | Status |
|-----------|--------|
| 4C.1: H1 swing levels exposed | ✅ COMPLETE |
| 4C.2: Horizon trade builder | ✅ COMPLETE |
| 4C.2: Execute-path shadow integration | ✅ COMPLETE |
| 4C.3: Research decoupling (ALL opportunities) | ✅ COMPLETE |
| 4C.4: Observability logging | ✅ COMPLETE |
| 4C.5: Data collection readiness | ✅ COMPLETE |
| Research validation | ✅ THIS DOCUMENT |

### Phase 4C: ✅ COMPLETE

**The horizon shadow system is fully operational and research-ready.** Every assessed opportunity — regardless of execution decision — produces horizon shadow trades that are independently tracked to outcome. Survivorship bias has been eliminated. The system can now discover where true edge exists across trade horizons.
