# Research Readiness Audit — Strategy Intelligence System

## VERDICT: READY FOR COLLECTION

The system is architecturally complete for evidence collection.
One enrichment (adding `entity_id` to strategy observations) would
make joins deterministic rather than temporal-proximity-based, but
data collection can begin immediately without it.

---

## AUDIT AREA 1 — Profitability Questions

### Complete Research Question Catalogue

#### A) Strategy Performance

| # | Question | Required Evidence |
|---|----------|-------------------|
| S1 | Which strategies produce positive EV? | n≥100 resolved observations per strategy |
| S2 | Which strategies consistently fail? | Same — looking for EV < 0 with p < 0.05 |
| S3 | Which strategies only work in certain environments? | Phase × strategy × outcome analysis |
| S4 | Is there a strategy with Sharpe > 0.5? | R-multiple time series per strategy |
| S5 | Do strategies degrade over time? | Performance by month/quarter |

#### B) Environment Matching

| # | Question | Required Evidence |
|---|----------|-------------------|
| E1 | Does matching strategy to phase improve outcomes? | Compare: FULLY_MET outcome vs ALL_TRADES outcome |
| E2 | Does strategy family selection outperform "trade everything"? | A/B: filtered vs unfiltered EV |
| E3 | Is there a phase where NO strategy works? | Phase-level aggregate EV |
| E4 | Does regime × phase interaction matter? | 2-way breakdown |

#### C) Condition Quality

| # | Question | Required Evidence |
|---|----------|-------------------|
| C1 | Which conditions predict success? | Condition × outcome correlation |
| C2 | Which conditions are noise? | Conditions that don't change outcome distribution |
| C3 | Does confidence score correlate with outcome? | High-confidence vs low-confidence EV |
| C4 | Does condition count correlate with outcome? | More conditions met → better result? |
| C5 | Which conditions should be removed? | Conditions that HURT when present |

#### D) Decision Improvement (Before vs After)

| # | Question | Measurement |
|---|----------|-------------|
| D1 | Does the bot currently trade when strategy conditions are met? | Join observations to decisions |
| D2 | Does the bot currently avoid trading when conditions are NOT met? | Join observations to NO_TRADE |
| D3 | When conditions were met but bot didn't trade, what was the outcome? | Missed opportunity analysis |
| D4 | When conditions were NOT met but bot traded, what was the outcome? | False positive analysis |
| D5 | What's the EV difference between "traded with conditions met" vs "traded without"? | The fundamental comparison |

---

## AUDIT AREA 2 — Required Datasets

### Dataset → Field Mapping

| Research Question | Primary Dataset | Join Dataset | Join Key | Fields Needed |
|---|---|---|---|---|
| S1: Strategy EV | strategy_observations | shadow_trades_v2 | symbol + timestamp (±5min) | observation.strategy_family, shadow.pnl_r_multiple |
| E1: Phase matching | strategy_observations | shadow_trades_v2 | symbol + timestamp | observation.evaluation_status, observation.market_phase, shadow.pnl_r_multiple |
| C3: Confidence → outcome | strategy_observations | shadow_trades_v2 | symbol + timestamp | observation.confidence, shadow.pnl_r_multiple |
| D1: Did bot trade? | strategy_observations | decision_trace | symbol + cycle_id | observation.evaluation_status, trace.action |
| D5: Conditional EV | strategy_observations + shadow_trades_v2 | (self-join) | symbol + timestamp | observation.evaluation_status, shadow.pnl_r_multiple |

### Join Path Analysis

**Current join path:**
```
strategy_observations.symbol = shadow_trades_v2.identity.symbol
AND ABS(strategy_observations.timestamp_utc - shadow_trades_v2.decision_snapshot.timestamp_decision_utc) < 300
```

**Ideal join path (with entity_id enrichment):**
```
strategy_observations.entity_id = shadow_trades_v2.identity.entity_id
```

**Status:** Temporal proximity join is FUNCTIONAL but not ideal.
The observation_id format is `{symbol}_{cycle_id}_{bar_time}` which contains the same components as entity_id (`{symbol}_{bar_time}`). This means a deterministic join IS possible today by parsing the observation_id — no schema change required.

### Dataset Availability

| Dataset | Location | Fields Available | Status |
|---|---|---|---|
| strategy_observations | logs/strategy_observations/ | All observation fields | ✅ Persisted (from observer #7) |
| shadow_trades_v2 | logs/shadow_trades/ | identity, decision_snapshot, simulated_outcome | ✅ Already persisted |
| decision_trace | logs/decision_trace/ | action, entity_id, pattern, regime, score | ✅ Already persisted |
| trade_truth (real) | logs/trade_truth/ | correlation_id, entity_id, r_multiple | ✅ Already persisted |
| research_shadow_trades | logs/research_shadow_trades/ | Same as shadow_trades_v2 | ✅ Already persisted |

**All required datasets exist and are being persisted.** No new datasets need to be created.

---

## AUDIT AREA 3 — Athena Table Design

### Current Tables: SUFFICIENT for Phase 1 research

The existing Athena DDL in `observation_athena.sql` provides:
- `trading_bot.strategy_observations` — strategy condition occurrences
- `trading_bot.curated_events` — market observations (existing)
- Shadow trades accessible via S3 prefix queries

### Recommended Research Views (CATEGORY B — useful but can wait)

These are VIEWS over existing data, not new tables:

```sql
-- VIEW: strategy_performance_summary (materialised from joins)
CREATE OR REPLACE VIEW trading_bot.strategy_performance AS
SELECT
    so.strategy_family,
    so.market_phase,
    so.h4_regime,
    so.evaluation_status,
    so.confidence,
    st.simulated_outcome.pnl_r_multiple AS r_multiple,
    st.simulated_outcome.exit_reason,
    st.simulated_outcome.mfe_r,
    st.simulated_outcome.mae_r
FROM trading_bot.strategy_observations so
JOIN trading_bot.shadow_trades st
    ON so.symbol = st.identity.symbol
    AND ABS(so.timestamp_utc - st.decision_snapshot.timestamp_decision_utc) < 300;
```

```sql
-- VIEW: strategy_condition_effectiveness
SELECT
    evaluation_status,
    confidence,
    COUNT(*) AS n,
    AVG(r_multiple) AS avg_r,
    SUM(CASE WHEN r_multiple > 0 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS win_rate
FROM trading_bot.strategy_performance
GROUP BY evaluation_status, confidence;
```

**These views do NOT need to be created before data collection starts.**

---

## AUDIT AREA 4 — Tomorrow Workflow

### Exact Research Workflow (once n≥100 per cell):

**Step 1: Verify observations are accumulating**
```sql
SELECT date, COUNT(*) AS observations, 
       COUNT(DISTINCT strategy_family) AS families_seen
FROM trading_bot.strategy_observations
GROUP BY date ORDER BY date DESC LIMIT 7;
```
Purpose: Confirm observer #7 is writing data.

**Step 2: Check condition occurrence rates**
```sql
SELECT strategy_family, evaluation_status, COUNT(*) AS n
FROM trading_bot.strategy_observations
WHERE evaluation_status IN ('FULLY_MET', 'PARTIALLY_MET')
GROUP BY strategy_family, evaluation_status
ORDER BY n DESC;
```
Purpose: Find which strategies have enough FULLY_MET events to analyse.

**Step 3: Join to shadow trade outcomes**
```sql
SELECT
    so.strategy_family,
    so.evaluation_status,
    COUNT(*) AS n,
    AVG(st.simulated_outcome.pnl_r_multiple) AS avg_r,
    SUM(CASE WHEN st.simulated_outcome.pnl_r_multiple > 0 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS win_rate
FROM trading_bot.strategy_observations so
JOIN trading_bot.shadow_trades st
    ON so.symbol = st.identity.symbol
    AND ABS(so.timestamp_utc - st.decision_snapshot.timestamp_decision_utc) < 300
WHERE so.evaluation_status = 'FULLY_MET'
GROUP BY so.strategy_family, so.evaluation_status;
```
Purpose: "When conditions were fully met, what was the average outcome?"

**Step 4: Compare FULLY_MET vs NOT_MET**
```sql
-- Does strategy intelligence add value?
SELECT
    evaluation_status,
    COUNT(*) AS n,
    AVG(r_multiple) AS avg_r,
    SUM(CASE WHEN r_multiple > 0 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS win_rate
FROM (
    SELECT so.evaluation_status, st.simulated_outcome.pnl_r_multiple AS r_multiple
    FROM trading_bot.strategy_observations so
    JOIN trading_bot.shadow_trades st
        ON so.symbol = st.identity.symbol
        AND ABS(so.timestamp_utc - st.decision_snapshot.timestamp_decision_utc) < 300
)
GROUP BY evaluation_status;
```
Purpose: THE FUNDAMENTAL QUESTION — is FULLY_MET better than NOT_MET?

**Step 5: Decision**
- If FULLY_MET avg_r > 0 AND significantly different from NOT_MET (p<0.05) → Strategy intelligence has value → proceed to walk-forward
- If no difference → Strategy conditions are noise → revise definitions
- If insufficient n → Continue collecting data

---

## AUDIT AREA 5 — Data Quality Check

### Can the system currently tell me:

| Question | Answer | How |
|----------|--------|-----|
| "When strategy X was valid, did I trade?" | **YES** | Join strategy_observations (evaluation_status=FULLY_MET) to decision_trace (action=EXECUTE) by symbol + cycle_id |
| "When strategy X was valid, did I avoid trading?" | **YES** | Same join, filter action=NO_TRADE |
| "If I had taken every valid opportunity, what would the result have been?" | **YES** | Join FULLY_MET observations to shadow trades (which simulate ALL patterns regardless of decision) |
| "Which strategy would have been the best candidate at that moment?" | **YES** | strategy_observations.candidate_strategies contains all eligible strategies with confidence per cycle |
| "Was the market environment correctly classified?" | **PARTIALLY** | Observations record market_phase and h4_regime, but correctness requires manual review or structural validation |

### Key Insight:

**Shadow trades capture outcomes for EVERY detected pattern, not just executed ones.** This means strategy observations can be joined to shadow trade outcomes regardless of whether the bot actually traded. This is the research advantage of shadow trading — we have the counterfactual.

---

## AUDIT AREA 6 — Final Gap Analysis

### CATEGORY A: Required before collecting data

**None.** Data collection is already happening (observer #7 is integrated and live).

### CATEGORY B: Useful improvements (can wait)

| Item | Benefit | Complexity |
|------|---------|-----------|
| Add `entity_id` field to observation record | Deterministic join (vs temporal proximity) | Low — 1 line in strategy_intelligence_observer.py |
| Automated outcome linker (shadow trade close → observation link) | Removes manual linkage step | Medium — hook into shadow trade close event |
| Research experiment for strategy intelligence (e.g. "M12") | Formalises the workflow into the experiment framework | Low — follows M9/M10 pattern |
| Walk-forward split infrastructure | Required for validation, not discovery | Medium — time-based record splitting |

### CATEGORY C: Research enhancement only

| Item | Benefit |
|------|---------|
| Athena views for common strategy queries | Convenience (can write raw SQL instead) |
| Strategy performance dashboard | Visual monitoring |
| Condition-level outcome breakdown | Fine-grained condition analysis |
| Multiple-comparison correction (Bonferroni) | Required before promotion, not collection |
| Monte Carlo simulation for significance | Enhanced statistical testing |

---

## CONCLUSION

### READY FOR COLLECTION ✅

The system is architecturally complete. Every cycle:
1. StrategyObserver evaluates all strategies ✅
2. Observations are persisted (local + S3) ✅
3. Shadow trades capture outcomes for all patterns ✅
4. Datasets can be joined by symbol + temporal proximity ✅
5. Research engine can load and query data ✅

### What happens now:

1. **The bot runs.** Observer #7 writes strategy observations every cycle.
2. **Shadow trades accumulate.** Outcomes are recorded independently.
3. **After n≥100 per strategy×phase cell:** Run the Step 1-5 workflow above.
4. **After validation:** Either promote strategies or reject hypotheses.

### The single most valuable CATEGORY B improvement:

**Add `entity_id` to the strategy observation record** (1 line change in `strategy_intelligence_observer.py`). This converts the temporal-proximity join into an exact-match join via the entity_id that already flows through the entire pipeline. Without it, research still works but joins are fuzzy. With it, joins are deterministic.

This is recommended but NOT blocking. Research can begin today.
