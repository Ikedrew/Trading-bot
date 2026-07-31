# Macro Context Persistence Contract

## Research Question This Must Answer

> "Did macro context improve or reduce strategy performance?"

To answer this, every persisted decision must contain:
1. What macro said (raw observations)
2. How it was interpreted (alignment state relative to trade direction)
3. What it did to confidence (modifier applied)
4. What the trade's outcome was (linked via observation_id)

With these four pieces, a researcher can GROUP BY alignment_state and compare average R-multiples.

---

## 1. Fields Required in DecisionTrace (v10_decisions JSONL)

### New Section: `macro_context`

```json
{
  "macro_context": {
    "monthly_trend": "BULLISH",
    "monthly_trend_strength": 0.65,
    "monthly_phase": "IMPULSE",
    "weekly_trend": "BEARISH",
    "weekly_trend_strength": 0.42,
    "weekly_swing_high": 1.0920,
    "weekly_swing_low": 1.0850,
    "weekly_range_position": 0.78,
    "daily_bias": "NEUTRAL",
    "daily_bias_strength": 0.15,
    "daily_range_position": 0.62,
    "daily_atr_ratio": 1.1,
    "data_quality": "COMPLETE"
  },
  "macro_alignment": {
    "trade_direction": "BUY",
    "monthly_alignment": "ALIGNED",
    "weekly_alignment": "OPPOSING",
    "daily_alignment": "NEUTRAL",
    "alignment_state": "CONFLICTED",
    "confidence_modifier": -0.03,
    "primary_influence": "WEEKLY",
    "is_conflicted": true,
    "raw_score": -0.075,
    "narrative": "Monthly supports but weekly structure opposes"
  }
}
```

### Field Definitions

| Field | Type | Null? | Purpose |
|---|---|---|---|
| `macro_context.monthly_trend` | str | Yes (if unavailable) | Raw monthly direction observation |
| `macro_context.monthly_trend_strength` | float | No (default 0.0) | How strong the monthly signal is |
| `macro_context.monthly_phase` | str | Yes | Current monthly candle character |
| `macro_context.weekly_trend` | str | Yes | Raw weekly direction |
| `macro_context.weekly_trend_strength` | float | No (default 0.0) | Weekly signal strength |
| `macro_context.weekly_swing_high` | float | No (default 0.0) | Key weekly structural boundary |
| `macro_context.weekly_swing_low` | float | No (default 0.0) | Key weekly structural boundary |
| `macro_context.weekly_range_position` | float | No (default 0.0) | Where price is in weekly range |
| `macro_context.daily_bias` | str | Yes | Today's directional lean |
| `macro_context.daily_bias_strength` | float | No (default 0.0) | Today's conviction |
| `macro_context.daily_range_position` | float | No (default 0.0) | Position in today's range |
| `macro_context.daily_atr_ratio` | float | No (default 1.0) | Today's volatility vs average |
| `macro_context.data_quality` | str | No | COMPLETE / PARTIAL / STALE / UNAVAILABLE |
| `macro_alignment.trade_direction` | str | No | Which direction alignment was computed against |
| `macro_alignment.monthly_alignment` | str | No | ALIGNED / OPPOSING / NEUTRAL |
| `macro_alignment.weekly_alignment` | str | No | ALIGNED / OPPOSING / NEUTRAL |
| `macro_alignment.daily_alignment` | str | No | ALIGNED / OPPOSING / NEUTRAL |
| `macro_alignment.alignment_state` | str | No | FA / SA / PA / N / CONFLICTED / PO / SO / FO |
| `macro_alignment.confidence_modifier` | float | No | Actual modifier applied (±0.20 max) |
| `macro_alignment.primary_influence` | str | No | MONTHLY / WEEKLY / DAILY / NONE |
| `macro_alignment.is_conflicted` | bool | No | Layers actively disagree |
| `macro_alignment.raw_score` | float | No | Weighted score before scaling |
| `macro_alignment.narrative` | str | Yes | Human-readable summary |

### For NO_TRADE Decisions

`macro_alignment` section is **still populated** (computed against `opportunity.directional_bias`). This enables the research question: "For opportunities that were rejected, would macro alignment have made them better or worse if they had traded?"

---

## 2. Fields Required in Decision Ledger Records

The decision ledger is a lighter-weight record. Include only the computed result, not the raw data.

```json
{
  "macro_alignment_state": "CONFLICTED",
  "macro_confidence_modifier": -0.03,
  "macro_data_quality": "COMPLETE"
}
```

Three fields only. The full detail is in the decision trace — the ledger just needs enough for basic filtering.

---

## 3. Fields Required for Research Queries

### Primary Research Table (joins decision + outcome)

| Column | Source | Enables |
|---|---|---|
| `observation_id` | Decision record | JOIN key to outcomes |
| `alignment_state` | `macro_alignment.alignment_state` | GROUP BY for performance comparison |
| `confidence_modifier` | `macro_alignment.confidence_modifier` | Correlation with R-multiple |
| `is_conflicted` | `macro_alignment.is_conflicted` | Filter conflicted vs clear signals |
| `primary_influence` | `macro_alignment.primary_influence` | "Which timeframe is most predictive?" |
| `data_quality` | `macro_context.data_quality` | Filter only high-quality observations |
| `strategy_family` | Existing field | Per-strategy macro analysis |
| `final_action` | Existing field | EXECUTE vs NO_TRADE splits |

### Research Queries Enabled

**Q1: Does macro alignment predict trade outcome?**
```sql
SELECT alignment_state, AVG(r_multiple), COUNT(*)
FROM decisions d JOIN outcomes o USING (observation_id)
WHERE data_quality = 'COMPLETE'
GROUP BY alignment_state
ORDER BY AVG(r_multiple) DESC
```

**Q2: Which timeframe is most predictive?**
```sql
SELECT primary_influence, AVG(r_multiple), COUNT(*)
FROM decisions d JOIN outcomes o USING (observation_id)
WHERE confidence_modifier != 0
GROUP BY primary_influence
```

**Q3: Does the confidence modifier correlate with outcome?**
```sql
SELECT
    CASE WHEN confidence_modifier > 0 THEN 'POSITIVE'
         WHEN confidence_modifier < 0 THEN 'NEGATIVE'
         ELSE 'NEUTRAL' END as modifier_direction,
    AVG(r_multiple), STDDEV(r_multiple), COUNT(*)
FROM decisions d JOIN outcomes o USING (observation_id)
GROUP BY modifier_direction
```

**Q4: Is conflicted macro worse than neutral macro?**
```sql
SELECT is_conflicted, AVG(r_multiple), COUNT(*)
FROM decisions d JOIN outcomes o USING (observation_id)
WHERE alignment_state IN ('NEUTRAL', 'CONFLICTED')
GROUP BY is_conflicted
```

**Q5: Per-strategy macro sensitivity**
```sql
SELECT strategy_family, alignment_state, AVG(r_multiple), COUNT(*)
FROM decisions d JOIN outcomes o USING (observation_id)
WHERE data_quality = 'COMPLETE'
GROUP BY strategy_family, alignment_state
```

**Q6: Should macro modifiers be larger or smaller?**
```sql
SELECT
    confidence_modifier,
    r_multiple
FROM decisions d JOIN outcomes o USING (observation_id)
WHERE final_action = 'EXECUTE'
-- Scatter plot: modifier vs outcome → shows if modifier direction predicts R
```

---

## 4. Schema Version Impact

### Current Schema: `v10_decision_v1`

### Decision: NO schema version bump required

| Reasoning | Detail |
|---|---|
| Schema evolution rules | Additive only — new fields can be added without version change |
| `macro_context` is a new top-level section | Does not modify or remove existing fields |
| `macro_alignment` is a new top-level section | Same |
| Existing consumers | Will not break — they never read `macro_context` |
| Existing validation | `validate_decision_record()` checks CRITICAL_FIELDS only — macro fields are not critical |

The schema version remains `v10_decision_v1`. The schema contract allows additive evolution.

### Optional: Add to schema registry

If desired, register the new fields in `schema_contract.py` under a non-critical section:

```python
# Non-critical enrichment fields (may be null, never required for record validity)
ENRICHMENT_FIELDS = frozenset({
    "macro_context",
    "macro_alignment",
})
```

---

## 5. Backwards Compatibility Requirements

### Reading Old Records (pre-macro)

| Scenario | Handling |
|---|---|
| Decision record has no `macro_context` field | Reader treats as `null` — macro was not available for this decision |
| Research query filters on `data_quality` | Records without macro_context are excluded by `WHERE data_quality = 'COMPLETE'` naturally (field doesn't exist → not complete) |
| Ledger record has no `macro_alignment_state` | Default to "UNAVAILABLE" — pre-macro decision |

### Writing New Records

| Scenario | Handling |
|---|---|
| MacroSnapshot is None (cold start) | Write `macro_context: null` and `macro_alignment: {"alignment_state": "UNAVAILABLE", "confidence_modifier": 0.0, "data_quality": "UNAVAILABLE"}` |
| MacroSnapshot partial (some layers missing) | Write available fields, set `data_quality: "PARTIAL"` |
| MacroSnapshot complete | Write all fields, `data_quality: "COMPLETE"` |

### S3 Compatibility

No changes to S3 bucket structure. Macro fields are embedded in existing decision records — same key path, same JSONL format, slightly larger record size (~200 bytes added per record).

---

## 6. What This Enables (and When)

| Capability | Available When | Prerequisite |
|---|---|---|
| "See macro context in terminal" | After implementation | Terminal report section added |
| "Filter decisions by macro alignment" | Immediately after persistence | Any query tool on JSONL |
| "Compare R-multiples by alignment" | After n≥30 EXECUTE + closed trades | Outcome linker operational |
| "Determine optimal confidence modifier weights" | After n≥100 trades with outcomes | Sufficient sample size |
| "Prove macro improves or reduces performance" | After n≥100 with statistical test | p-value < 0.05 comparison |

### Minimum Sample Sizes for Research

| Question | Min n | Estimated Days at 2-5 trades/day |
|---|---|---|
| "Is FULL_ALIGNMENT better than NEUTRAL?" | 30 per group | ~15-30 days |
| "Which timeframe is most predictive?" | 50 per group | ~25-50 days |
| "Should modifier weights change?" | 100 total | ~20-50 days |
| "Statistically significant (p<0.05)?" | 100+ per group | ~50-100 days |

---

## Summary

The persistence contract ensures:
1. Every decision records WHAT macro said (raw)
2. Every decision records HOW it was interpreted (alignment)
3. Every decision records WHAT it did (modifier)
4. Research can JOIN to outcomes via observation_id
5. Old records remain valid (additive, no breaks)
6. Null/missing is handled gracefully (UNAVAILABLE state)
7. Quality filtering is built in (`data_quality` field)
