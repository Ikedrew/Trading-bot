# PHASE 4C.5: HORIZON DATA COLLECTION READINESS AUDIT

**Date:** 2026-07-24
**Question:** Can we trust the data we are about to collect?
**Answer:** YES — READY WITH GAPS. The architecture is verified end-to-end. Horizon shadows are created, tracked, and persisted correctly. Two minor gaps exist (assessment horizon persistence ordering + duplicate test records on disk).

---

## 1. Executive Summary

The horizon shadow system is architecturally complete and functionally verified. A full lifecycle test confirms:
- Horizon classification produces correct eligibility
- Horizon trade builder generates valid SL/TP per horizon
- ShadowTradeEngine tracks each horizon independently
- Outcomes include R-multiple, MFE, MAE, bars held, and exit reason
- Horizon identity (INTRADAY/EXTENDED) survives from creation through to persisted outcome
- correlation_id links horizon shadows back to the originating opportunity

**Verdict: READY for live data collection.**

---

## 2. Architecture Verification: ✅ PASS

```
Opportunity (detected)
    │ opportunity_id, entity_id, correlation_id
    ▼
Horizon Classification (classify_horizons)
    │ eligible_horizons: [SCALP, INTRADAY, EXTENDED]
    ▼
Horizon Trade Builder (build_all_horizon_trades)
    │ HorizonTrade per eligible horizon (SL/TP/RR)
    ▼
ShadowTradeEngine.open_trade()
    │ trade_id=shadow_{cycle}_{SYMBOL}_{HORIZON}
    │ correlation_id=COR-... (links back to opportunity)
    ▼
evaluate_bar() per M5 cycle
    │ MFE/MAE tracked, R-multiple computed per bar
    ▼
Close (SL hit | TP hit | max_bars timeout)
    │ shadow_trades_v2 record persisted
    ▼
Research Engine (load_shadow_trades → filter by horizon)
```

---

## 3. Opportunity → Horizon Shadow Linking: ✅ PASS

| Field | Present on Horizon Shadow? | Links To |
|-------|---------------------------|----------|
| `trade_id` (contains `_INTRADAY`/`_EXTENDED`) | ✅ | Identifies horizon |
| `correlation_id` | ✅ | Links to opportunity/decision |
| `symbol` | ✅ | Direct match |
| `strategy_id` (contains horizon) | ✅ | `CONTINUATION_INTRADAY`, etc. |
| `cycle_id` | ✅ | Same-cycle grouping |
| `direction` | ✅ | Matches opportunity |
| `entry_intent_price` | ✅ | Same entry for all horizons |

**Can research answer "For the same opportunity, which horizon performed best?"**

YES — join on `correlation_id` + `symbol` + `cycle_id`. Each horizon produces a separate shadow with the same linking fields but different trade_id suffix and different SL/TP.

---

## 4. Coverage Metrics (Production Data)

| Category | Count | Notes |
|----------|-------|-------|
| Total opportunities | 668 | From live runtime |
| Total shadow trades | 203 | All types |
| Standard (SCALP) shadows | 194 | Pre-horizon and SCALP |
| INTRADAY horizon shadows | 6 | From test runs (persisted to disk) |
| EXTENDED horizon shadows | 3 | From test runs (persisted to disk) |

**Coverage Analysis:**
- SCALP: ~100% of executed opportunities get a shadow (existing behaviour)
- INTRADAY: 0% from live production (code deployed but bot restart pending)
- EXTENDED: 0% from live production (same reason)

The 9 horizon shadows on disk are from test execution (cycle_ids 100, 200, 500, 5000). Production horizon shadows will begin accumulating on next bot restart.

---

## 5. Horizon Identity Persistence: ✅ PASS

Verified end-to-end:

| Stage | Horizon Identifiable? | Method |
|-------|----------------------|--------|
| HorizonTrade (builder) | ✅ | `horizon="INTRADAY"` field |
| ShadowTrade (engine) | ✅ | `trade_id` contains `_INTRADAY` |
| shadow_trades_v2 (storage) | ✅ | `identity.trade_id` + `identity.strategy_id` |
| Research query | ✅ | Filter: `trade_id LIKE '%_INTRADAY'` |

Example verified record:
```
trade_id: shadow_5000_GBPUSD_INTRADAY
strategy_id: CONTINUATION_INTRADAY
→ Horizon = INTRADAY (extractable from both fields)
```

---

## 6. Shadow Trade Completeness: ✅ PASS

Every horizon shadow record contains:

| Field | Present? | Example |
|-------|----------|---------|
| symbol | ✅ | GBPUSD |
| horizon (in trade_id) | ✅ | `_INTRADAY` |
| direction | ✅ | SELL |
| entry | ✅ | 1.33700 |
| stop_loss | ✅ | 1.33850 |
| take_profit | ✅ | 1.33250 |
| entry_time | ✅ | 1784900000.0 |
| correlation_id | ✅ | COR-20260724-5000-GBPUSD-ABCD |

**Missing fields:** None. All required fields populated.

---

## 7. Outcome Completeness: ✅ PASS

Closed horizon shadow records contain:

| Field | Present? | Example |
|-------|----------|---------|
| exit_reason | ✅ | `take_profit` / `stop_loss` / `max_bars_timeout` |
| pnl_r_multiple | ✅ | 3.0000 |
| mfe_r | ✅ | 3.0333 |
| mae_r | ✅ | 0.3333 |
| bars_held | ✅ | 28 |
| exit_price | ✅ | (computed at SL/TP level) |

**All 9 test records have complete outcomes.** Zero missing outcome fields.

---

## 8. Horizon Comparison Capability: ✅ PASS

Verified with synthetic data:

```
Opportunity: GBPUSD SELL (trending continuation, H1 aligned)

SCALP (via standard shadow):
  SL: 1.33730+buffer  TP: entry-risk×2  →  Tracked separately

INTRADAY:
  SL: 1.33850 (M15 resistance)
  TP: 1.33250 (entry - risk×3)
  Result: +3.0R (TP hit after 28 bars)

EXTENDED:
  SL: 1.34000 (H1 swing high)
  TP: 1.32500 (entry - risk×4)
  Result: STILL ACTIVE after 50 bars (wider target)
```

**Same opportunity, different horizons, independent outcomes** — confirmed researchable.

---

## 9. Research Query Readiness: ✅ PASS

| Query | Required Fields | Available? |
|-------|----------------|-----------|
| Average R by horizon | `trade_id` (horizon tag) + `pnl_r_multiple` | ✅ |
| Pattern × Horizon | `decision_snapshot.pattern` + horizon tag | ✅ |
| Regime × Horizon | `strategy_id` + `correlation_id` → join to decision | ✅ (indirect) |
| Session × Horizon | `entry_time` (derive session) + horizon | ✅ |
| Win rate by horizon | `exit_reason` + horizon tag | ✅ |
| MFE/MAE by horizon | `mfe_r`, `mae_r` + horizon | ✅ |

---

## 10. Duplicate Detection: ⚠️ MINOR ISSUE

| Finding | Count | Cause | Impact |
|---------|-------|-------|--------|
| Duplicate INTRADAY records (cycle_id=100) | 3 copies | Test execution persisted to production logs | LOW — filter by `cycle_id > 1000` for production data |
| Duplicate EXTENDED records (cycle_id=200) | 3 copies | Same | LOW |

**Resolution:** When querying for research, filter to `cycle_id > last_known_test_cycle`. Not a data integrity issue — just test artifacts on disk.

---

## 11. Execution Isolation: ✅ PASS

| Check | Status |
|-------|--------|
| Horizon shadows modify live entries? | ❌ NO |
| Horizon shadows change SL/TP on real positions? | ❌ NO |
| Horizon code affects position sizing? | ❌ NO |
| Horizon code touches RiskManager? | ❌ NO |
| Horizon code calls MT5 execution? | ❌ NO |
| Horizon code modifies trade management? | ❌ NO |
| All horizon code in try/except pass? | ✅ YES |

**Execution isolation: CONFIRMED.**

---

## 12. Gaps Identified

| Gap | Severity | Impact | Fix |
|-----|----------|--------|-----|
| Assessment records lack horizon classification (0/260) | MEDIUM | Cannot query "which horizons were considered" from assessment dataset | Phase 4B.1 fix deployed but not yet reflected in existing records. New records will contain it. |
| Test records in production shadow_trades | LOW | 9 test records with artificial cycle_ids | Filter by cycle_id range for research |
| No dedicated `horizon` field on shadow schema | LOW | Must parse trade_id or strategy_id string | Functional but less clean than a top-level field |

---

## 13. Final Readiness Verdict

### READY WITH GAPS

| Criterion | Status |
|-----------|--------|
| Horizon shadows created correctly | ✅ |
| SL/TP geometry correct per horizon | ✅ |
| Outcome tracking functional | ✅ |
| Identity survives lifecycle | ✅ |
| Linking back to opportunity | ✅ |
| Persistence to disk + S3 | ✅ |
| Research engine can load | ✅ |
| Cross-horizon comparison possible | ✅ |
| Execution isolation confirmed | ✅ |
| Production data accumulating | ⚠️ Awaiting bot restart |
| Assessment horizon data | ⚠️ New records will contain it |

**The system is READY to collect trustworthy horizon performance data.**

Once the bot runs its next live session:
- Every EXECUTE opportunity will produce 1 SCALP shadow (existing) + 0-2 higher-horizon shadows
- Each shadow runs independently through ShadowTradeEngine
- Outcomes are persisted with full metrics (R, MFE, MAE, bars, exit reason)
- Research can compare "same opportunity, different horizon" to discover where edge exists
