# V10 Persistence Migration Plan

## Objective

Redirect ALL S3 persistence from `trading-bot-data-mk1` (now legacy/archive) to `v10-engine`. Single-write only. Same dataset names, schemas, lifecycle, local mirror.

## Architecture (After Migration)

```
Writer → Local JSONL (unchanged) + v10-engine S3 (ONLY active target)
trading-bot-data-mk1 → ARCHIVE (no new writes)
```

---

## The Change

Every module has a `_S3_BUCKET` constant. Migration = change the string:

```python
_S3_BUCKET = "trading-bot-data-mk1"  →  _S3_BUCKET = "v10-engine"
```

---

## Every Location Requiring Change

### Functional Constants (29 locations — controls where data writes)

| # | File | Line | Variable |
|---|---|---|---|
| 1 | `core/assessment/persistence.py` | 34 | `_S3_BUCKET` |
| 2 | `core/audit_persistence.py` | 34 | `_S3_BUCKET` |
| 3 | `core/contracts/quarantine.py` | 32 | `_S3_BUCKET` |
| 4 | `core/decision_audit.py` | 31 | `_S3_BUCKET` |
| 5 | `core/decision_ledger.py` | 67 | `_S3_BUCKET` |
| 6 | `core/decision_trace.py` | 44 | `_S3_BUCKET` |
| 7 | `core/edge_attribution.py` | 57 | `_S3_BUCKET` |
| 8 | `core/edge_optimisation.py` | 62 | `_S3_BUCKET` |
| 9 | `core/event_stream.py` | 394 | `_S3_BUCKET` |
| 10 | `core/execution_context.py` | 60 | `_S3_BUCKET` |
| 11 | `core/learning/store.py` | 30 | `_S3_BUCKET` |
| 12 | `core/market_context/persistence.py` | 21 | `_S3_BUCKET` |
| 13 | `core/opportunity/persistence.py` | 33 | `_S3_BUCKET` |
| 14 | `core/persistence/execution_result_writer.py` | 29 | `_S3_BUCKET` |
| 15 | `core/persistence/opportunity_assessment_writer.py` | 33 | `_S3_BUCKET` |
| 16 | `core/portfolio_ranking/persistence.py` | 36 | `_S3_BUCKET` |
| 17 | `core/portfolio_ranking/shadow_comparison.py` | 31 | `_S3_BUCKET` |
| 18 | `core/protection_verification.py` | 45 | `_S3_BUCKET` |
| 19 | `core/research_assessment/research_shadow_engine.py` | 25 | `_S3_BUCKET` |
| 20 | `core/risk_deviation.py` | 48 | `_S3_BUCKET` |
| 21 | `core/shadow_trades.py` | 38 | `_S3_BUCKET` |
| 22 | `core/storage/s3_batch_writer.py` | 48 | constructor default |
| 23 | `core/storage/s3_batch_writer.py` | 56 | parameter default |
| 24 | `core/storage/s3_batch_writer.py` | 249 | hardcoded bucket |
| 25 | `core/strategies/observation_persistence.py` | 34 | `_S3_BUCKET` |
| 26 | `core/strategy_compiler.py` | 56 | `_S3_BUCKET` |
| 27 | `core/trade_journal.py` | 36 | `_S3_BUCKET` |
| 28 | `core/trade_truth.py` | 54 | `_S3_BUCKET` |
| 29 | `core/trade_truth_graph.py` | 54 | `_S3_BUCKET` |

### Event Stream Validation Guard (4 lines — prevents writes to wrong bucket)

| # | File | Line | Change |
|---|---|---|---|
| 30 | `core/event_stream.py` | 399 | `os.getenv("AWS_S3_BUCKET", "trading-bot-data-mk1")` → `"v10-engine"` |
| 31 | `core/event_stream.py` | 400 | `if bucket != "trading-bot-data-mk1":` → `"v10-engine"` |
| 32 | `core/event_stream.py` | 402 | Error message bucket name |
| 33 | `core/event_stream.py` | 403 | Error message bucket name |

### Docstrings (20 locations — documentation only, no runtime effect)

All `S3: s3://trading-bot-data-mk1/...` docstrings in the files above. Update to `s3://v10-engine/...`.

---

## Migration Table

| # | Dataset | Writer File | Migration | Status |
|---|---|---|---|---|
| 1 | Events | `core/event_stream.py` + `core/storage/s3_batch_writer.py` | Change bucket constant + guard | Redirect bucket |
| 2 | Market Context | `core/market_context/persistence.py` | Change `_S3_BUCKET` | Redirect bucket |
| 3 | Opportunities | `core/opportunity/persistence.py` | Change `_S3_BUCKET` | Redirect bucket |
| 4 | Opportunity Assessment | `core/persistence/opportunity_assessment_writer.py` | Change `_S3_BUCKET` | Redirect bucket |
| 5 | Assessments | `core/assessment/persistence.py` | Change `_S3_BUCKET` | Redirect bucket |
| 6 | Decision Trace | `core/decision_trace.py` | Change `_S3_BUCKET` | Redirect bucket |
| 7 | Decision Ledger | `core/decision_ledger.py` | Change `_S3_BUCKET` | Redirect bucket |
| 8 | Decision Audit | `core/decision_audit.py` | Change `_S3_BUCKET` | Redirect bucket |
| 9 | Execution Context | `core/execution_context.py` | Change `_S3_BUCKET` | Redirect bucket |
| 10 | Execution Results | `core/persistence/execution_result_writer.py` | Change `_S3_BUCKET` | Redirect bucket |
| 11 | Protection Audit | `core/protection_verification.py` | Change `_S3_BUCKET` | Redirect bucket |
| 12 | Risk Deviation | `core/risk_deviation.py` | Change `_S3_BUCKET` | Redirect bucket |
| 13 | Trade Truth | `core/trade_truth.py` | Change `_S3_BUCKET` | Redirect bucket |
| 14 | Trade Journal | `core/trade_journal.py` | Change `_S3_BUCKET` | Redirect bucket |
| 15 | Shadow Trades | `core/shadow_trades.py` | Change `_S3_BUCKET` | Redirect bucket |
| 16 | Strategy Observations | `core/strategies/observation_persistence.py` | Change `_S3_BUCKET` | Redirect bucket |
| 17 | Portfolio Rankings | `core/portfolio_ranking/persistence.py` | Change `_S3_BUCKET` | Redirect bucket |
| 18 | Portfolio Shadow | `core/portfolio_ranking/shadow_comparison.py` | Change `_S3_BUCKET` | Redirect bucket |
| 19 | Trade Truth Graph | `core/trade_truth_graph.py` | Change `_S3_BUCKET` | Redirect bucket |
| 20 | Edge Attribution | `core/edge_attribution.py` | Change `_S3_BUCKET` | Redirect bucket |
| 21 | Edge Optimisation | `core/edge_optimisation.py` | Change `_S3_BUCKET` | Redirect bucket |
| 22 | Strategy Compiler | `core/strategy_compiler.py` | Change `_S3_BUCKET` | Redirect bucket |
| 23 | Quarantine | `core/contracts/quarantine.py` | Change `_S3_BUCKET` | Redirect bucket |

---

## Post-Migration: Deprecate `core/v10/s3_writer.py`

After migration, the custom `v10/decisions/`, `v10/events/`, `v10/executions/`, `v10/outcomes/` prefixes are redundant. The standard dataset names in v10-engine provide the same data. Deprecate `upload_decision()`, `upload_events()`, `upload_execution()`, `upload_outcome()`.

---

## Rollback

Set all 29 constants back to `"trading-bot-data-mk1"`. No data loss — archive bucket untouched.

---

## Effort: ~55 minutes total

| Task | Time |
|---|---|
| Change 29 constants + 4 guard lines | 20 min |
| Update docstrings | 15 min |
| Verify writes succeed to v10-engine | 10 min |
| Deprecate v10/s3_writer.py | 10 min |
