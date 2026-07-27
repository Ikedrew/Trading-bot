# PHASE 2: EXECUTION EVIDENCE AUDIT

**Date:** 2026-07-23
**Question:** Does the current system have enough execution evidence to explain the path from decision to realised trade outcome?
**Answer:** **YES — Execution Evidence already exists as a first-class dataset.** It does NOT need promotion. It has local + S3 persistence, full broker response capture, slippage tracking, latency measurement, protection verification, and risk deviation analysis. Minor enrichment only (schema_version).

---

## 1. Execution Lifecycle Map

```
Decision approved (decision_id generated)
  │
  ├── [1] ExecutionContext captured (pre-trade environment snapshot)
  │       logs/execution_context/{SYMBOL}/{DATE}.jsonl
  │       S3: execution_context/symbol={S}/date={D}/
  │
  ▼
Order creation (OrderIntent → MT5 request dict)
  │
  ├── [2] Pre-submit log: [EXECUTION_SUBMITTED] with all request params
  │
  ▼
Broker submission (mt5.order_send)
  │
  ├── [3] Latency measured: t0 → mt5_result → latency_ms
  │
  ├── [4] Retry logic (if REQUOTE/TIMEOUT)
  │
  ▼
Broker response (mt5_result)
  │
  ├── [5] ExecutionResult persisted
  │       logs/execution_results/{SYMBOL}/{DATE}.jsonl
  │       S3: execution_results/symbol={S}/date={D}/
  │       Contains: result_ok, retcode, deal, order, fill_price, slippage,
  │                 decision_id, correlation_id, entity_id, volume, sl, tp
  │
  ├── [6] Event stream emission: FILLED or REJECTED status
  │
  ▼
Fill confirmation (result.ok = True)
  │
  ├── [7] Position registered (TradeStateManager)
  │
  ├── [8] Protection Verification (Phase 1)
  │       logs/protection_audit/{SYMBOL}/{DATE}.jsonl
  │       Confirms: broker-side SL/TP exist on position
  │
  ▼
Position open → managed by tick_driver (break-even, trailing)
  │
  ▼
Position closed (SL/TP hit, time exit, or broker close)
  │
  ├── [9] TradeRecord persisted (trade_journal)
  │       logs/trade_journal/{DATE}.jsonl
  │
  ├── [10] Trade Truth persisted (pure execution reality)
  │        logs/trade_truth/{SYMBOL}/{DATE}.jsonl
  │        S3: trades/{SYMBOL}/{DATE}.jsonl
  │
  ├── [11] Risk Deviation computed
  │        logs/risk_deviation/{SYMBOL}/{DATE}.jsonl
  │        Measures: planned_risk_R vs actual_risk_R
  │
  └── [12] Event stream outcome emission
```

---

## 2. Existing Execution Evidence Inventory

### Dataset: execution_results (PRIMARY EXECUTION EVIDENCE)

| Property | Value |
|----------|-------|
| Module | `core/persistence/execution_result_writer.py` |
| Local path | `logs/execution_results/{SYMBOL}/{YYYY-MM-DD}.jsonl` |
| S3 path | `execution_results/symbol={SYMBOL}/date={YYYY-MM-DD}/part-000.jsonl` |
| Record per | Every broker execution attempt (success AND failure) |
| Key fields | `result_ok`, `retcode`, `deal`, `order_ticket`, `fill_price`, `slippage`, `entry_reference`, `sl`, `tp`, `volume`, `side`, `pattern`, `decision_id`, `correlation_id`, `entity_id`, `decision_ts_utc_ms` |
| Phase 1 additions | `requested_sl`, `broker_confirmed_sl`, `requested_tp`, `broker_confirmed_tp`, `protection_status`, `protection_failure_reason` |
| Queryable | ✅ DuckDB/Athena on S3 |

### Dataset: execution_context (PRE-TRADE ENVIRONMENT)

| Property | Value |
|----------|-------|
| Module | `core/execution_context.py` |
| Local path | `logs/execution_context/{SYMBOL}/{YYYY-MM-DD}.jsonl` |
| S3 path | `execution_context/symbol={SYMBOL}/date={YYYY-MM-DD}/part-000.jsonl` |
| Record per | Every decision event (written BEFORE execution) |
| Key fields | `correlation_id`, `symbol`, `timestamp_utc`, `market_access` (session, spread, bid, ask), `infrastructure` (latency_ms, feed_state, tick_age_ms), `risk_environment` (drawdown, daily_loss, open_positions) |
| Records | 14,189+ |
| Queryable | ✅ DuckDB/Athena on S3 |

### Dataset: protection_audit (POST-FILL VERIFICATION)

| Property | Value |
|----------|-------|
| Module | `core/protection_verification.py` |
| Local path | `logs/protection_audit/{SYMBOL}/{YYYY-MM-DD}.jsonl` |
| S3 path | ❌ Local only |
| Record per | Every successful fill (verification attempt) |
| Key fields | `requested_sl`, `broker_confirmed_sl`, `requested_tp`, `broker_confirmed_tp`, `protection_status` (VERIFIED/CORRECTED/FAILED_UNPROTECTED), `verification_latency_ms`, `attempts`, `correction_attempted`, `correction_success` |
| Queryable | Local only (no S3) |

### Dataset: risk_deviation (POST-TRADE RISK MEASUREMENT)

| Property | Value |
|----------|-------|
| Module | `core/risk_deviation.py` |
| Local path | `logs/risk_deviation/{SYMBOL}/{YYYY-MM-DD}.jsonl` |
| S3 path | ❌ Local only |
| Record per | Every completed trade |
| Key fields | `planned_risk_R`, `actual_risk_R`, `risk_deviation`, `risk_classification` (NORMAL/ELEVATED/CRITICAL/WIN), `entry_price`, `exit_price`, `initial_sl`, `direction` |
| Queryable | Local only (no S3) |

### Dataset: trade_truth (OUTCOME TRUTH)

| Property | Value |
|----------|-------|
| Module | `core/trade_truth.py` |
| Local path | `logs/trade_truth/{SYMBOL}/{YYYY-MM-DD}.jsonl` |
| S3 path | `trades/{SYMBOL}/{YYYY-MM-DD}.jsonl` |
| Schema version | ✅ `trade_truth_v3` |
| Record per | Every closed trade |
| Key fields | `trade_id`, `correlation_id`, `entry_fill_price`, `exit_fill_price`, `pnl_realised`, `r_multiple_realised`, `duration_seconds`, `exit_reason` |
| Queryable | ✅ DuckDB/Athena on S3 |

### Dataset: trade_journal (OPERATIONAL TRADE LOG)

| Property | Value |
|----------|-------|
| Module | `core/trade_journal.py` |
| Local path | `logs/trade_journal/{YYYY-MM-DD}.jsonl` |
| S3 path | ❌ Local only |
| Record per | Every closed trade |
| Key fields | `trade_id`, `symbol`, `pattern_name`, `direction`, `entry_price`, `exit_price`, `initial_sl`, `initial_tp`, `net_pnl`, `duration_seconds`, `close_reason`, `correlation_id` |
| Queryable | Local (DuckDB read_json_auto) |

---

## 3. Execution Evidence Ownership

### Responsibility Separation Assessment

| Layer | Question | Owner | Mixing? |
|-------|----------|-------|---------|
| Decision | "Should we trade?" | `decision_audit.py`, `decision_ledger.py` | ✅ Clean |
| Execution | "Can we execute? Did it fill?" | `execution_result_writer.py`, `mt5_execution.py` | ✅ Clean |
| Protection | "Is the position protected?" | `protection_verification.py` | ✅ Clean |
| Trade Truth | "What was the final outcome?" | `trade_truth.py` | ✅ Clean |
| Risk Deviation | "Was risk respected?" | `risk_deviation.py` | ✅ Clean |

**No responsibility mixing found.** Each layer has clear ownership and a distinct question it answers:

- `execution_results` — "What happened when we sent the order?" (broker response)
- `execution_context` — "What was the environment when we decided?" (pre-trade snapshot)
- `protection_audit` — "Did the broker confirm our safety net?" (post-fill verification)
- `risk_deviation` — "Did actual risk match intended risk?" (post-trade analysis)
- `trade_truth` — "What was the final realised outcome?" (pure execution reality)

---

## 4. Execution Evidence Checklist

| Requirement | Status | Dataset | Fields |
|-------------|--------|---------|--------|
| Order intent (what was attempted) | ✅ | execution_results | `side`, `volume`, `entry_reference`, `sl`, `tp`, `pattern` |
| Broker request (what was sent) | ✅ | mt5_execution.py logs + execution_results | Full request dict logged pre-submit |
| Broker response (what was returned) | ✅ | execution_results | `result_ok`, `retcode`, `deal`, `order_ticket`, `comment` |
| Requested price | ✅ | execution_results | `entry_reference` |
| Fill price | ✅ | execution_results | `fill_price` |
| Slippage | ✅ | execution_results | `slippage` = abs(fill_price - entry_reference) |
| Latency | ⚠️ Partial | Event stream emission has `fill_latency_ms`; execution_results does NOT have latency field | Gap: latency not in persisted JSONL |
| Failure reason | ✅ | execution_results | `comment` (broker error text), `retcode` (numeric code) |
| Execution timestamp | ✅ | execution_results | `timestamp_utc`, `timestamp_unix`, `decision_ts_utc_ms` |
| Protection verification | ✅ | protection_audit + execution_results | `protection_status`, `broker_confirmed_sl/tp` |
| Risk deviation | ✅ | risk_deviation | `planned_risk_R`, `actual_risk_R`, `risk_classification` |
| Join keys | ✅ | execution_results | `decision_id`, `correlation_id`, `entity_id`, `cycle_id` |
| Persistence (local) | ✅ | All datasets | Local JSONL with fsync |
| Persistence (S3) | ⚠️ Partial | execution_results + execution_context have S3; protection_audit + risk_deviation do NOT | 2 datasets local-only |

---

## 5. Persistence Maturity Assessment

| Criterion | execution_results | execution_context | protection_audit | risk_deviation |
|-----------|------------------|-------------------|------------------|----------------|
| `schema_version` | ❌ | ❌ | ❌ | ❌ |
| `dataset_version` | ❌ | ❌ | ❌ | ❌ |
| Local persistence | ✅ | ✅ | ✅ | ✅ |
| S3 mirror | ✅ | ✅ | ❌ | ❌ |
| Hive partitioning | ✅ | ✅ | ✅ (local) | ✅ (local) |
| Athena compatible | ✅ | ✅ | ❌ (no S3) | ❌ (no S3) |
| Dataset ownership | ✅ | ✅ | ✅ | ✅ |
| Research use cases | ✅ | ✅ | ✅ | ✅ |

**The two primary execution datasets (execution_results + execution_context) are already first-class.** The two Phase 1 additions (protection_audit + risk_deviation) are local-only and need S3 mirrors.

---

## 6. Research Questions: Current Capability

| Research Question | Answerable? | Source |
|-------------------|-------------|--------|
| Are losses caused by bad decisions or bad execution? | ✅ | Join decision_audit (EV, score) to risk_deviation (planned vs actual) |
| Is slippage reducing expectancy? | ✅ | `execution_results.slippage` aggregated per symbol/session |
| Are broker failures affecting results? | ✅ | `execution_results WHERE result_ok=False` — count + retcode analysis |
| Is execution latency meaningful? | ⚠️ Partial | Latency captured in event stream emission but NOT in persisted execution_results JSONL |
| Are certain symbols harder to execute? | ✅ | `GROUP BY symbol` on execution_results (rejection rate, slippage) |
| Are certain sessions harder to execute? | ⚠️ Partial | execution_context has session_state but must be joined to execution_results via correlation_id |
| Did protection failure cause the GBPUSD -4.5R loss? | ✅ | protection_audit shows whether SL was confirmed post-fill |
| Was risk exceeded? | ✅ | risk_deviation.risk_classification = CRITICAL when deviation > 3.0 |

---

## 7. Gap Analysis

### Current State: COMPREHENSIVE

The system has **5 execution-related datasets** that collectively cover the entire lifecycle:

1. `execution_context` — pre-trade environment (14,189 records)
2. `execution_results` — broker response (local + S3)
3. `protection_audit` — post-fill SL/TP verification (local only)
4. `risk_deviation` — post-trade risk measurement (local only)
5. `trade_truth` — final outcome (local + S3, schema versioned)

### Missing (Minor)

| Gap | Severity | Impact |
|-----|----------|--------|
| No `fill_latency_ms` on `execution_results` JSONL | LOW | Latency is logged to event stream but not in the queryable JSONL. Easy to add. |
| No `schema_version` on any execution dataset except trade_truth | LOW | Schema evolution tracking. Same gap as Decision datasets. |
| protection_audit has no S3 mirror | MEDIUM | Protection failure evidence lost on VM termination |
| risk_deviation has no S3 mirror | MEDIUM | Risk anomaly evidence lost on VM termination |
| No `spread_at_execution` on execution_results | LOW | Spread is on execution_context (joinable) but not directly on execution_results |

### NOT Missing (Often Assumed To Be)

| Commonly Expected | Actually Present | Where |
|-------------------|------------------|-------|
| Fill price | ✅ | `execution_results.fill_price` |
| Slippage | ✅ | `execution_results.slippage` |
| Broker error codes | ✅ | `execution_results.retcode` + `comment` |
| SL/TP intent | ✅ | `execution_results.sl`, `execution_results.tp` |
| Broker SL/TP confirmation | ✅ | `execution_results.broker_confirmed_sl/tp` + `protection_audit` |
| Decision-to-execution link | ✅ | `decision_id`, `correlation_id` on execution_results |
| Pre-trade market state | ✅ | `execution_context` (session, spread, latency, drawdown) |

---

## 8. Recommendation

### Choice: **(B) Minor enrichment required**

A dedicated Execution Evidence dataset is **NOT needed**. The existing `execution_results` dataset already serves this role. It:
- Records every broker attempt (success and failure)
- Has local + S3 persistence
- Has Hive-compatible partitioning
- Has full join keys (decision_id, correlation_id, entity_id)
- Captures intent, response, fill, slippage, and protection verification

### Recommended Enrichment

| Action | Effort | Value |
|--------|--------|-------|
| Add `fill_latency_ms` field to `execution_results` | 15 min | Direct latency query without event stream join |
| Add `schema_version = "execution_results_v1"` | 15 min | Schema evolution tracking |
| Add S3 mirror to `protection_audit` | 30 min | Durability for protection failure evidence |
| Add S3 mirror to `risk_deviation` | 30 min | Durability for risk anomaly evidence |

**Total: ~90 minutes for full execution evidence compliance.**

---

## 9. Final Answer

**"Does the current system have enough execution evidence to explain the path from decision to realised trade outcome?"**

**YES.** The path is fully traceable:

```
Decision (decision_audit.decision_id)
    ↓ [decision_id]
Execution Intent (execution_results.sl, tp, volume, side)
    ↓ [same record]
Broker Response (execution_results.result_ok, retcode, fill_price, slippage)
    ↓ [correlation_id]
Protection Verified (protection_audit.protection_status)
    ↓ [correlation_id]
Position Managed (trade_management events)
    ↓ [trade_id]
Trade Closed (trade_truth.pnl_realised, r_multiple, exit_reason)
    ↓ [trade_id]
Risk Measured (risk_deviation.actual_risk_R, classification)
```

Every link in this chain exists. Every dataset has a defined owner. The primary datasets have S3 durability. The execution evidence layer is **already mature** — it just needs minor standardisation (schema_version) and S3 mirrors on the two Phase 1 additions.
