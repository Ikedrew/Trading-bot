# V3 Outcome Linkage Audit

**Date:** 2026-07-28
**Status:** RESOLVED — 92.4% linkage achieved

---

## 1. Linkage Architecture

```
V3 Observer (#9)
    │
    │ correlation_id = "{SYMBOL}_{bar_time}"
    │ (e.g., "EURUSD_1785255900")
    │
    ▼
V3Opportunity (persisted)
    │
    │ JOIN KEY: correlation_id
    │
    ▼
Shadow Trade Engine
    │
    │ identity.entity_id = "{SYMBOL}_{bar_time}"
    │ (same format as V3.correlation_id)
    │
    ▼
simulated_outcome.pnl_r_multiple → outcome_raw_r
```

**Join key:** `V3Opportunity.correlation_id` == `shadow_trade.identity.entity_id`

Both use the format `{SYMBOL}_{unix_bar_time}` — they are naturally compatible.

---

## 2. The Break Point

**There was no format mismatch.** The V3 `correlation_id` and shadow trade `entity_id` use the identical `{SYMBOL}_{bar_time}` format.

**The actual problem:** The outcome linker had never been executed on V3 data. The V3 schema has `outcome_linked: bool = False` as default — it stays False until explicitly linked.

**Fix:** Created `core/research/v3_outcome_linker.py` and ran it.

---

## 3. Files Changed

| File | Purpose |
|---|---|
| `core/research/v3_outcome_linker.py` | V3-specific outcome linker (entity_id → corr_id → timestamp) |
| `analysis/run_v3_linkage.py` | Executable linkage script |
| `tests/test_v3_outcome_linker.py` | 14 tests covering all match paths |

---

## 4. Fields Added/Modified

No fields were added to V3Opportunity. The existing outcome fields are now populated:

| Field | Before | After |
|---|---|---|
| `outcome_linked` | `False` | `True` (for matched records) |
| `outcome_raw_r` | `None` | R-multiple from shadow trade |
| `outcome_win` | `None` | `True`/`False` |
| `outcome_mfe_r` | `None` | Maximum favourable excursion |
| `outcome_mae_r` | `None` | Maximum adverse excursion |
| `outcome_exit_reason` | `None` | "TP" / "SL" / "max_bars_timeout" |
| `outcome_bars_held` | `None` | Number of M5 bars |
| `_linkage` | absent | Match metadata dict |

---

## 5. Before/After Linkage Percentages

| Metric | Before | After |
|---|---|---|
| Total V3 observations | 132 | 132 |
| Linked to outcomes | **0 (0%)** | **122 (92.4%)** |
| Match by entity_id | 0 | 66 (50%) |
| Match by correlation_id | 0 | 0 |
| Match by timestamp (±300s) | 0 | 56 (42.4%) |
| Unmatched (NO_TRADE) | 132 | 10 (7.6%) |

---

## 6. Linked Data Summary

| Metric | Value |
|---|---|
| Records with outcomes | 122 |
| Win rate | 63.1% |
| Mean R | +1.03R |
| Exit reasons | max_bars_timeout (majority), TP, SL |

**Note:** The +1.03R mean is from raw shadow trade outcomes which include multiple horizon variants. This is NOT cost-adjusted and should not be interpreted as positive EV until the V3 Discovery Engine runs proper analysis with spread deduction.

---

## 7. NO_TRADE Handling

10 V3 observations (7.6%) have no matching shadow trade. These are marked:

```json
{
    "_linkage": {
        "linked": false,
        "reason": "NO_TRADE_MATCH"
    }
}
```

These represent cycles where the V3 observer fired but no pattern triggered a shadow trade. They remain in the dataset with full V3 context features for future "missed opportunity" research.

---

## 8. Research Dataset Now Available

The V3 research engine can now query:

```python
from core.research.v3_outcome_linker import link_v3_outcomes

report = link_v3_outcomes()
linked = [r for r in report.linked_records if r["_linkage"]["linked"]]

# Example: EV when equal_highs detected vs not
with_eq_highs = [r for r in linked if r.get("equal_highs_above")]
without = [r for r in linked if not r.get("equal_highs_above")]
```

Every linked record contains both:
- **V3 features:** range_position, equal_highs, FVG, OB, displacement, rejection
- **Outcome:** result_r, win/loss, MFE, MAE, exit_reason, hold_time

---

## 9. Tests

| Suite | Result |
|---|---|
| `test_v3_outcome_linker.py` | **14 passed** |
| Full regression | **3457 passed**, 1 pre-existing failure (unchanged) |
| New regressions | **0** |

---

## 10. Completion Checklist

| Criterion | Status |
|---|---|
| ✅ Every V3 observation has a traceable identity | `correlation_id` = `{SYMBOL}_{bar_time}` |
| ✅ Executed trades link to V3 features | 92.4% match rate |
| ✅ Shadow trades link to outcomes | Via `simulated_outcome.pnl_r_multiple` |
| ✅ NO_TRADE decisions remain explainable | Marked with `reason: "NO_TRADE_MATCH"` |
| ✅ Research engine can calculate EV from V3 features | All outcome fields populated on linked records |
| ✅ No strategy behaviour changed | Zero production code modified |
