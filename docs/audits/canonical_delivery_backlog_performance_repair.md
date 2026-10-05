# Canonical-delivery backlog: performance acceptance report

Scope: eliminate historical canonical-delivery backlog cost from the live
scanner cycle and from the canonical reconciliation worker.

## 1. Production evidence (reported)

- Cycle 1 of a full 10-symbol run took **107793 ms** and tripped
  `[LIVENESS_STALL] threshold_seconds=10.0`.
- During that run "historical canonical anomaly spam" was active.

The saved runtime logs in `logs/` predate the canonical-delivery integration
(the 2026-09-09/09-10 runs contain no `CANONICAL_*` lines), so the durable
evidence used here is the live backlog itself:

`logs/canonical_delivery_outbox.sqlite3` (146 MB) and
`logs/lifecycle_evidence_obligations.jsonl` (64 MB / 45,533 lines).

Outbox state at diagnosis:

| delivery_state | reconciliation_state | rows |
|---|---|---|
| ACKNOWLEDGED | RECONCILED | 13,165 |
| ACKNOWLEDGED | NOT_APPLICABLE | 2,630 |
| ACKNOWLEDGED | **ANOMALY** (`LIFECYCLE_OBLIGATION_NOT_LINKED`) | **330** |

All 330 `ANOMALY` rows have `lifecycle_obligation_id IS NULL`. Because
`acknowledged_needing_reconciliation()` selects
`reconciliation_state IN ('PENDING','ANOMALY','NOT_LINKED')`, those 330 rows
were re-selected and re-logged `[CANONICAL_RECONCILIATION_ANOMALY]` on **every**
worker pass (10 rows / 2 s, forever) — the "historical per-row anomaly spam".

## 2. Root cause (measured)

| Cost centre | Before | Frequency |
|---|---|---|
| `LifecycleEvidenceLedger.find_exact()` — O(N) whole-ledger scan over 18,658 obligations, called once per enqueue with no linked obligation (`_existing_obligation_id`) and from `decision_ledger` / `decision_recorder` | **87–97 ms / call** | every cycle, per record |
| `CanonicalDeliveryOutbox._validate_all_rows()` on open (`PRAGMA quick_check` + reconstruct all 16,125 rows) | **8–37 s** | once per open |
| `LifecycleEvidenceLedger._load()` (64 MB) | **13.0 s** | once per process |
| Historical `ANOMALY` reconciliation | 330 rows re-logged every pass | continuous |

The recurring killer is `find_exact`: on a busy first cycle (~600 enqueues in
the first minute, ~18 % with no linked obligation) the whole-ledger scan alone
costs **tens of seconds**, and it grows linearly with the obligation history.

## 3. The repair

| File | Change |
|---|---|
| `core/lifecycle_evidence_obligations.py` | Index obligations by `(expected_dataset, expected_identity_key)`; maintained on `_load`, `create` and `update`. `find_exact()` is now a bucket lookup instead of a whole-ledger scan. |
| `core/canonical_delivery_outbox.py` | New `TERMINAL_HISTORICAL` reconciliation state (added to `record_reconciliation` and `_row_to_record` validation, deliberately **absent** from the reconciliation scan whitelist). New `mark_terminal_historical()`, `historical_acknowledged_needing_reconciliation(horizon)`, `reconciliation_state_counts()`. |
| `core/canonical_delivery_worker.py` | `_evaluate_lifecycle(record, *, mutate=True)` split out of `_reconcile_lifecycle` (behaviour unchanged for live rows). New `triage_historical_backlog(horizon, max_items)` parks only **unreconcilable** historical rows as `TERMINAL_HISTORICAL` and defers everything else to the normal active pass. New `HistoricalTriageResult`. `reconcile_acknowledged()` measures its own pass duration. |
| `core/canonical_delivery_service.py` | Captures a reconciliation `horizon` at start, runs the bounded triage **once, off the per-pass path**, logs one aggregate line `[CANONICAL_HISTORICAL_RECONCILIATION_TRIAGE]`, and exposes `historical_triage` and `last_pass_seconds` in its status. |

Design notes:
- "Historical" = ACK rows created **before the service start instant**. Rows the
  process creates (including recovered local handoffs) stay "live".
- Triage is **non-mutating for reconcilable rows** (`mutate=False`), so the
  certified "a restarted service repairs an ACK row once its obligation
  appears" contract is preserved.
- Parked rows are excluded from `acknowledged_needing_reconciliation()` by the
  existing whitelist, so they are never reprocessed as active work.

## 4. Before / after measurements

### 4.1 Per-enqueue obligation lookup (recurring per-cycle cost)

| Metric | Before | After |
|---|---|---|
| `find_exact`, 1 call | 46.9 ms | 1.2 ms |
| `find_exact`, 50 calls | 4.865 s | 0.049 s |
| per-lookup (200-call loop) | **87.2 ms** | **0.67 ms** |
| representative 200-lookup cycle | **17.43 s** | **0.135 s** |
| speedup | — | **~129x** |

### 4.2 Canonical worker pass (reconciliation)

| Metric | Before | After |
|---|---|---|
| active pass, 10 rows | 0.162 s, **10 per-row anomaly logs** | 0.117 s, **0 rows, 0 logs** |
| active pass, all rows | 2.249 s, **330 per-row anomaly logs** | (no rows remain) |
| `worker.last_reconciliation_pass_seconds` | n/a | 0.117 s |

### 4.3 Historical triage (one-shot, off the per-pass path)

```
TRIAGE scanned=330  skipped_terminal_historical=330  deferred_to_active_pass=0
       per_row_anomaly_logs=0
reconciliation_state_counts = {NOT_APPLICABLE: 2630, RECONCILED: 13165,
                               TERMINAL_HISTORICAL: 330}
```

- **Rows skipped as terminal historical: 330.**
- **Historical per-row anomaly logging: 0** (one aggregate line instead).
- Historical terminal rows are no longer returned by
  `acknowledged_needing_reconciliation()`.

## 5. Acceptance assessment

| Criterion | Result |
|---|---|
| Historical per-row anomaly logging = zero | **Met** (330 -> 0; one aggregate line) |
| Historical terminal rows not reprocessed as active reconciliation work | **Met** (parked `TERMINAL_HISTORICAL`, excluded from the scan) |
| First cycle no longer ~108 s *because of historical canonical backlog* | **Met for the canonical contribution** (200-lookup cycle 17.43 s -> 0.135 s) |
| Measure cycle latency before/after | **Done** (section 4.1) |
| Measure canonical worker pass duration before/after | **Done** (section 4.2) |
| Report rows skipped as terminal historical | **Done** (330) |
| Normal cycles below the 10 s liveness threshold | **Not provable from here — see below** |

## 6. Remaining blockers (honest reporting)

Cycle latency was **not** measured end-to-end here: doing so requires a live
MT5 terminal and the full scanner. The canonical-delivery contribution is now
negligible (0.135 s for a 200-lookup cycle), but two other cost centres were
observed and are **independent of canonical delivery**:

1. **One-time startup costs (not per-cycle):** `LifecycleEvidenceLedger._load()`
   = 13.0 s and `CanonicalDeliveryOutbox._validate_all_rows()` = 8–37 s. Both
   run once per process, before the scanner loop, and grow with the ledger/outbox
   size. They should be bounded/lazy next (e.g. skip `quick_check` on every open,
   or validate incrementally) — but they do not inflate steady-state cycles.
2. **Scanner per-symbol work:** in the archived live logs the first 10-symbol
   cycle spent 12–25 s **per symbol** inside `[V10 CONTEXT]` / MT5 data
   collection (`[DATA_TICK] ... stale_tick_detected=true`), which is the dominant
   cycle cost and is unrelated to canonical delivery.

Therefore this change removes the historical-canonical-backlog bottleneck and
its anomaly spam, but it does **not** claim that scanner cycles are below 10 s.
The remaining blocker is the per-symbol scanner/MT5 data path (plus the one-time
ledger/outbox load), which must be addressed separately.

## 7. Test evidence

- `tests/test_canonical_delivery_worker.py`, `test_canonical_delivery_outbox.py`,
  `test_canonical_delivery_service.py`, `test_canonical_delivery_writer_migration.py`,
  `test_lifecycle_evidence_obligations.py`: **all pass**.
- New regression tests:
  - `test_historical_backlog_triage_parks_unreconcilable_rows_without_anomaly_spam`
  - `test_historical_backlog_triage_defers_active_work_unchanged`
  - `test_startup_triage_parks_historical_backlog_and_reports_skipped`
  - `test_find_exact_index_matches_full_scan_and_tracks_revisions`
- Pre-existing (unrelated, fail on the pristine checkout too):
  `test_account_snapshot_contract.py::test_repeated_identical_observation_is_idempotent_in_the_outbox`
  and the same test in `test_position_snapshot_contract.py`
  (`OBLIGATION_IDENTITY_CONFLICT`, caused by a repeated identical observation).


