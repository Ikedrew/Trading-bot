# PHASE 2A PERSISTENCE AUDIT

**Date:** 2026-07-23
**Question:** Is the Opportunity Layer currently a true intelligence dataset, or only a logging feature?
**Answer:** It is currently a **local logging feature** — not yet a first-class research dataset.

---

## 1. Current Persistence Audit

### Data Flow

```
Pattern Detection (pre_engine_gates.py)
  → Signal objects (list[Signal])
    │
    ▼
Opportunity Factory (core/opportunity/factory.py)
  → Opportunity objects created for ALL detected patterns
  → State: DETECTED
  → Persisted immediately to local JSONL
    │
    ▼
Decision Engine (run_new_engine)
  → Enriches selected pattern's opportunity with scores
  → Transitions to ASSESSED or REJECTED
  → Persisted again (updated state)
    │
    ▼
Guard Chain / Execution
  → EXECUTED (if filled) or REJECTED (if blocked)
  → Persisted again (final state)
```

### Storage Location

```
logs/opportunities/{SYMBOL}/{YYYY-MM-DD}.jsonl
```

Example: `logs/opportunities/NZDUSD/2026-07-23.jsonl`

### What Survives

| Property | Status |
|----------|--------|
| Survives process restart | YES (local disk) |
| Survives VM termination | NO (no S3 mirror) |
| Queryable historically | NO (no Athena table) |
| Joinable to trade_truth | PARTIALLY (via entity_id, but no SQL join) |
| Schema versioned | NO |
| Deduplicated | NO (multiple writes per opportunity lifecycle) |

---

## 2. Data Architecture Assessment

### Existing S3 Data Landscape

```
s3://trading-bot-data-mk1/
  │
  ├── events/                        → "What did the system do?"
  │     symbol={SYMBOL}/date={YYYY-MM-DD}/
  │
  ├── trade_truth/                   → "What happened after capital committed?"
  │     symbol={SYMBOL}/date={YYYY-MM-DD}/  (trade_truth_v3)
  │
  ├── trade_truth_graph/             → "How do trades connect causally?"
  │     symbol={SYMBOL}/date={YYYY-MM-DD}/
  │
  ├── shadow_trades/                 → "What would have happened without execution?"
  │     {SYMBOL}/{YYYY-MM-DD}.jsonl
  │
  ├── research_shadow_trades/        → "Research engine shadow trade outcomes"
  │     {SYMBOL}/{YYYY-MM-DD}.jsonl
  │
  ├── decision_ledger/               → "What was decided each cycle?"
  │     symbol={SYMBOL}/date={YYYY-MM-DD}/
  │
  ├── decision_audit/                → "Full decision context per trade signal"
  │     symbol={SYMBOL}/date={YYYY-MM-DD}/
  │
  ├── execution_context/             → "Environment state at decision time"
  │     symbol={SYMBOL}/date={YYYY-MM-DD}/
  │
  ├── execution_results/             → "Broker response to order submission"
  │     {SYMBOL}/{YYYY-MM-DD}.jsonl
  │
  └── [MISSING] opportunities/       → "What did the market present?"
```

### Why Opportunity Data Is Different

| Dataset | Question Answered | When Written | Scope |
|---------|-------------------|--------------|-------|
| Events | What did the system do? | On action | Only actions taken |
| Trade Truth | What happened after capital? | On trade close | Only executed trades |
| Decision Ledger | What was decided? | Every cycle | All cycles (incl. no-trade) |
| Decision Audit | Full signal context? | On trade signal | Only triggered signals |
| **Opportunities** | **What did the market present?** | **On pattern detection** | **ALL candidates, incl. rejected** |

**The critical distinction:** Every other dataset records what the system *did* or *decided*. The Opportunity dataset records what the market *offered* — regardless of whether the system acted. This is the only dataset that captures the full opportunity universe.

---

## 3. Missing Components

### Gap 1: No S3 Mirror (CRITICAL)

Local disk only. If the VM is terminated, rebuilt, or the disk fills, all opportunity history is lost. Every other dataset (trade_truth, decision_ledger, events, shadow_trades) has an S3 mirror. Opportunity does not.

**Risk:** Complete data loss on infrastructure event.

### Gap 2: No Schema Version (HIGH)

The `Opportunity` dataclass has no `schema_version` field. When fields are added/removed, historical records become ambiguous. Every other dataset uses a version string (e.g., `trade_truth_v3`).

**Risk:** Future schema evolution breaks historical analysis without migration tooling.

### Gap 3: No Athena Table (MEDIUM)

No Glue crawler or table definition exists for opportunities. Cannot run SQL queries like:
```sql
SELECT pattern, COUNT(*) as n, 
       SUM(CASE WHEN state='EXECUTED' THEN 1 ELSE 0 END) as executed
FROM opportunities
WHERE symbol='NZDUSD' AND date='2026-07-23'
GROUP BY pattern
```

**Risk:** Research requires custom Python scripts instead of standard SQL.

### Gap 4: No Market Price Context (HIGH)

The Opportunity schema does NOT capture `bid` and `ask` at detection time. This is critical for:
- Hypothetical P&L reconstruction ("if we had traded this, what would entry be?")
- Spread analysis at opportunity time
- Slippage estimation for rejected opportunities

**Risk:** Cannot compute hypothetical outcomes for rejected opportunities.

### Gap 5: No EV/Probability Capture (MEDIUM)

EV is computed AFTER the OpportunityAssessment, inside the engine. The opportunity lifecycle update captures `overall_score` and `strategy_classification` but NOT:
- `ev` (expected value)
- `p_success` (probability estimate)
- `rr_effective` (reward/risk ratio from intent)

These are available in `_new_result` but not propagated to the Opportunity.

**Risk:** Cannot answer "which rejected opportunities had positive EV?"

### Gap 6: Duplicate Records Per Lifecycle (LOW)

Each state transition writes a new JSONL line (DETECTED, then ASSESSED/REJECTED, then EXECUTED). This means the same opportunity appears 2-3 times in the file. Not inherently wrong (append-only audit trail), but complicates queries.

**Risk:** Row count != opportunity count. Requires `WHERE state = 'REJECTED'` filter or deduplication logic.

### Gap 7: No Session/Runtime Identity (LOW)

No `runtime_session_id` or `batch_id` field. Cannot distinguish opportunities from different bot restarts on the same day.

**Risk:** Ambiguous attribution when bot restarts mid-day.

---

## 4. Recommended S3 Structure

### Target Layout

```
s3://trading-bot-data-mk1/
  opportunities/
    symbol=EURUSD/
      date=2026-07-23/
        part-000.jsonl
    symbol=GBPUSD/
      date=2026-07-23/
        part-000.jsonl
    symbol=NZDUSD/
      date=2026-07-23/
        part-000.jsonl
```

### Partitioning Strategy

| Partition Key | Type | Rationale |
|--------------|------|-----------|
| `symbol` | String | Primary filter for per-pair analysis |
| `date` | String (YYYY-MM-DD) | Time-based retention and query scoping |

This matches the existing convention used by `decision_ledger`, `trade_truth`, and `events`.

### Schema Versioning

Add to every record:
```json
{"schema_version": "opportunity_v1", ...}
```

### Athena Compatibility

- Content type: `application/x-ndjson` (JSONL)
- Partition style: Hive-compatible (`symbol=X/date=Y/`)
- Glue SerDe: `org.openx.data.jsonserde.JsonSerDe`
- Crawler: Add `opportunities/` to existing `trading_bot_curated_crawler` target paths

### Recommended Athena DDL

```sql
CREATE EXTERNAL TABLE IF NOT EXISTS trading_bot.opportunities (
  schema_version STRING,
  opportunity_id STRING,
  symbol STRING,
  cycle_id INT,
  direction STRING,
  pattern STRING,
  detection_timeframe STRING,
  detected_at_bar_time BIGINT,
  detected_at_utc STRING,
  h4_regime STRING,
  h4_regime_confidence DOUBLE,
  h1_direction STRING,
  h1_bos_confirmed BOOLEAN,
  market_state STRING,
  pattern_confidence DOUBLE,
  overall_score DOUBLE,
  strategy_classification STRING,
  state STRING,
  rejection_reason STRING,
  rejection_stage STRING,
  outcome_trade_id STRING,
  entity_id STRING
)
PARTITIONED BY (symbol STRING, date STRING)
ROW FORMAT SERDE 'org.openx.data.jsonserde.JsonSerDe'
LOCATION 's3://trading-bot-data-mk1/opportunities/'
```

---

## 5. Schema Recommendations

### Fields To Add (for research capability)

| Field | Type | Purpose |
|-------|------|---------|
| `schema_version` | str | Schema evolution tracking ("opportunity_v1") |
| `bid_at_detection` | float | Market price for hypothetical entry (BUY) |
| `ask_at_detection` | float | Market price for hypothetical entry (SELL) |
| `spread_at_detection` | float | Execution cost context |
| `ev_at_assessment` | float | Expected value (if computed) |
| `p_success_at_assessment` | float | Probability estimate |
| `rr_effective` | float | Risk/reward from risk model |
| `runtime_session_id` | str | Distinguish bot restart sessions |

### Persistence Model Recommendation

Instead of multiple writes per opportunity (DETECTED → ASSESSED → REJECTED), use a **final-state-only** model for S3:

- Local JSONL: Keep current append-per-transition (audit trail)
- S3 mirror: Write ONCE per opportunity after final state is determined (end of cycle)

This gives:
- Local: Full audit trail (every transition visible)
- S3/Athena: Clean analytical dataset (one row per opportunity, final state)

---

## 6. Research Questions: Current vs Target Capability

| Research Question | Current (Local JSONL) | Target (S3 + Athena) |
|-------------------|----------------------|---------------------|
| How many opportunities per session? | Manual script | `SELECT COUNT(*) FROM opportunities WHERE date='...' AND state='DETECTED'` |
| Which symbols produce most opportunities? | Manual script | `GROUP BY symbol` |
| Which patterns appear most frequently? | Manual script | `GROUP BY pattern` |
| Which opportunities are rejected? | Manual script (grep) | `WHERE state='REJECTED'` |
| Why are they rejected? | Manual script | `GROUP BY rejection_reason` |
| Are filters removing profitable setups? | **IMPOSSIBLE** (no price/EV data) | Join to price data + hypothetical outcome |
| Which patterns contain edge? | **IMPOSSIBLE** (no outcome linkage via SQL) | Join opportunities to trade_truth on entity_id |
| Were multiple opportunities competing? | Manual cycle_id correlation | `GROUP BY cycle_id HAVING COUNT(*)>1` |
| Was the chosen trade the best candidate? | **IMPOSSIBLE** | Compare EV/score across same-cycle opportunities |

---

## 7. Migration Plan

### Phase A: Schema Enrichment (immediate, no architecture change)

Add to `create_opportunity()` and `Opportunity` schema:
- `schema_version = "opportunity_v1"`
- `bid_at_detection`, `ask_at_detection` (passed from live_scanner)
- `runtime_session_id` (passed from live_scanner)

After engine result, also capture:
- `ev_at_assessment` from `_new_result.get("ev")`
- `p_success_at_assessment` from `_new_result.get("p_success")`
- `rr_effective` from `_new_result.get("rr_effective")`

**Risk:** Zero. Additive fields only.

### Phase B: S3 Mirror (shadow durability)

Add `_write_s3()` to `persistence.py` following the exact pattern from `decision_ledger.py`:
- Gated by `config.EVENT_STREAM_S3_MIRROR`
- Fire-and-forget (never blocks trading)
- Key: `opportunities/symbol={SYMBOL}/date={YYYY-MM-DD}/part-000.jsonl`
- Write final-state-only records to S3 (not per-transition)

**Risk:** Low. Same pattern as 8 other datasets. Boto3 already configured.

### Phase C: Athena Table (research capability)

1. Add `opportunities/` to Glue crawler target paths in `data_pipeline/aws_glue_setup.py`
2. Create table DDL (see Section 4)
3. Verify with `SELECT * FROM trading_bot.opportunities LIMIT 10`

**Risk:** Zero. Read-only query layer.

### Phase D: Research Engine Connection

Connect opportunity dataset to:
- Research Engine edge candidate analysis
- Strategy compiler feature selection
- Walk-forward validation
- Counterfactual simulation ("what if we had taken this rejected opportunity?")

**Risk:** Low-medium. Research modules already consume JSONL from S3.

---

## 8. Risks If Ignored

| Risk | Severity | Consequence |
|------|----------|-------------|
| VM termination loses all opportunity history | HIGH | Cannot perform historical analysis. Research value = 0. |
| Schema drift without version | MEDIUM | Future schema changes break existing analysis scripts |
| No hypothetical outcome analysis | HIGH | Cannot answer "are we rejecting profitable opportunities?" — the PRIMARY question this dataset was designed to answer |
| No SQL access | MEDIUM | Research requires bespoke Python scripts per question. Slows iteration. |
| No bid/ask at detection | HIGH | Cannot reconstruct what entry price would have been. Cannot compute hypothetical P&L. |

---

## 9. Final Assessment

### Current State: LOCAL LOGGING FEATURE

The Opportunity Layer currently provides:
- Visibility into what patterns fire (useful for debugging)
- Rejection reason capture (useful for understanding filter behaviour)
- Lifecycle tracking (useful for pipeline auditing)

It does NOT yet provide:
- Durable storage (S3)
- Queryable analytics (Athena)
- Hypothetical outcome analysis (no price data)
- Research engine integration
- Portfolio intelligence input

### To Become A True Intelligence Dataset

The Opportunity Layer needs:

1. **Durability** — S3 mirror (same pattern as 8 other datasets)
2. **Schema version** — `opportunity_v1` field on every record
3. **Price context** — `bid_at_detection`, `ask_at_detection`
4. **Assessment enrichment** — `ev_at_assessment`, `p_success`, `rr_effective`
5. **Query capability** — Athena table via Glue crawler
6. **Research connection** — Feed into counterfactual simulator and strategy compiler

### Effort Estimate

| Phase | Effort | Value Unlocked |
|-------|--------|----------------|
| A: Schema enrichment | 1 hour | Price context enables hypothetical analysis |
| B: S3 mirror | 1 hour | Durability — data survives infrastructure events |
| C: Athena table | 30 min | SQL research capability |
| D: Research connection | 2-4 hours | Full portfolio intelligence feedback loop |

**Total to reach "true intelligence dataset": ~5-6 hours of implementation.**

The foundation (Opportunity object, factory, lifecycle, integration) is solid. What's missing is the standard data engineering layer that every other dataset in the system already has.
