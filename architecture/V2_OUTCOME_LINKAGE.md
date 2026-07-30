# V2 Outcome Linkage

## Summary

The V2 Outcome Linker connects V2Opportunity observations (pre-trade market state) to shadow trade outcomes (post-trade results). This enables the research engine to answer: "Given this market context, what was the outcome?"

This is research infrastructure only. It does not modify trading behaviour.

---

## Linkage Flow

```
V2Opportunity (observation)          ShadowTrade (outcome)
logs/v2_opportunities/               logs/shadow_trades/
{SYMBOL}/{DATE}.jsonl                {SYMBOL}/{DATE}.jsonl
         │                                    │
         │                                    │
         └──────────┐          ┌──────────────┘
                    │          │
                    ▼          ▼
              ┌─────────────────────┐
              │  V2 Outcome Linker  │
              │                     │
              │  Match Priority:    │
              │  1. entity_id       │
              │  2. correlation_id  │
              │  3. symbol+time     │
              └─────────┬───────────┘
                        │
                        ▼
              V2Opportunity + Outcome
              (persisted back to v2 JSONL)
```

---

## Identifiers Used

| Identifier | Source (V2Opportunity) | Source (ShadowTrade) | Notes |
|---|---|---|---|
| entity_id | `correlation_id` field | `identity.entity_id` | Format: `{symbol}_{bar_time}`. Deterministic, highest confidence. |
| correlation_id | `correlation_id` field | `identity.correlation_id` | Decision spine ID. Links all artefacts from one decision. |
| symbol + timestamp | `symbol` + `timestamp_utc` | `identity.symbol` + `decision_snapshot.timestamp_decision_utc` | Fallback with ±300s tolerance. |

### Match Priority

1. **entity_id** — Exact match between V2Opportunity's `correlation_id` and ShadowTrade's `identity.entity_id`. Deterministic join.
2. **correlation_id** — Exact match on the decision spine correlation ID.
3. **symbol + timestamp** — Same symbol, entry time within 300 seconds (5 minutes). Nearest match wins.

---

## Fields Attached on Linkage

### Before (unlinked)

```json
{
    "outcome_recorded": false,
    "outcome_raw_r": null,
    "mfe": null,
    "mae": null,
    "reached_positive_target": null,
    "reached_negative_target": null,
    "bars_to_outcome": null
}
```

### After (linked)

```json
{
    "outcome_recorded": true,
    "outcome_raw_r": 1.8,
    "mfe": 2.4,
    "mae": -0.3,
    "reached_positive_target": true,
    "reached_negative_target": false,
    "bars_to_outcome": 9,
    "_linkage": {
        "linked": true,
        "result_r": 1.8,
        "win": true,
        "mfe_r": 2.4,
        "mae_r": -0.3,
        "hold_minutes": 45,
        "exit_reason": "TIMEOUT",
        "match_method": "entity_id"
    }
}
```

### Field Definitions

| Field | Type | Description |
|---|---|---|
| `outcome_recorded` | bool | Whether a matching trade was found |
| `outcome_raw_r` | float | P&L in R-multiples |
| `mfe` | float | Maximum favourable excursion in R |
| `mae` | float | Maximum adverse excursion in R |
| `reached_positive_target` | bool | result_r >= 1.0 |
| `reached_negative_target` | bool | result_r <= -1.0 |
| `bars_to_outcome` | int | Number of M5 bars held |
| `_linkage.win` | bool | result_r > 0 |
| `_linkage.hold_minutes` | int | bars_held * 5 |
| `_linkage.exit_reason` | str | SL / TP / TIMEOUT / MANUAL |
| `_linkage.match_method` | str | entity_id / correlation_id / timestamp |

---

## Examples

### Usage

```python
from core.research.v2_outcome_linker import link_outcomes

# Link all symbols
report = link_outcomes()

# Link specific symbol
report = link_outcomes(symbol="EURUSD")

# Dry run (don't persist)
report = link_outcomes(symbol="EURUSD", persist=False)

# Check results
print(report.summary())
# {'total_opportunities': 150, 'matched': 120, 'unmatched': 30,
#  'match_rate': 0.8, 'by_entity_id': 100, 'by_correlation_id': 15,
#  'by_timestamp': 5}
```

### Research Query

```python
# After linkage, V2 records can be filtered for analysis:
from core.research.v2_outcome_linker import link_outcomes

report = link_outcomes(symbol="EURUSD", persist=False)
linked = [r for r in report.linked_records if r.get("_linkage", {}).get("linked")]

# Example: What is the EV when H1 BOS is confirmed?
bos_trades = [r for r in linked if r.get("h1_bos_confirmed")]
bos_ev = sum(r["outcome_raw_r"] for r in bos_trades) / len(bos_trades) if bos_trades else 0
```

---

## Persistence

- Linked records are written back to `logs/v2_opportunities/{SYMBOL}/{DATE}.jsonl`
- Original observation fields are preserved unchanged
- Only outcome and `_linkage` fields are added/updated
- Shadow trade files are never modified

---

## Test Results

| Suite | Result |
|---|---|
| `test_v2_outcome_linker.py` | **15 passed** |
| Full regression | **3303 passed**, 1 pre-existing failure (unchanged) |
| New regressions | **0** |

---

## Design Decisions

1. **Read-only on shadow trades** — Linker never writes to shadow trade files. Only V2 opportunity files are updated.
2. **Immutable observations** — Original market context fields (H4, H1, M15, pattern, execution) are never modified. Only outcome fields change.
3. **Tolerance-based fallback** — 300s (5 min) window for timestamp matching. Prevents false joins from different trading sessions.
4. **Fire-and-forget** — Linkage can be re-run idempotently. Already-linked records (outcome_recorded=True) are skipped.
5. **No pipeline imports** — The linker imports only from `core.v2_opportunity` schema. Zero dependency on decision, execution, or risk modules.
