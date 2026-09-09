# Decision ledger immutable S3 persistence

Scope: decision_ledger S3 leaf naming and conditional creation only. No changes
to trading, CANDLE integrity, lineage, record construction, local persistence,
schema versions, other datasets, production processes, or existing S3 data.

## Writer trace and root cause

- `core/runtime/live_scanner.py:run_live_scanner` creates the ledger and
  `DecisionRecorder`, initializes/mutates the per-symbol decision, then invokes
  its nested `_finalize_decision`; end-of-cycle `tick()` and shutdown `flush()`
  drain buffered ledger records.
- `core/runtime/decision_recorder.py:DecisionRecorder.finalize` passes the
  completed decision to `self._ledger.record(...)`.
- `core/decision_ledger.py:DecisionLedgerWriter.record` calls
  `build_ledger_entry` and buffers the result. `write` accepts prebuilt rows.
  The additional adapter path is
  `core/v10/persistence_adapter.py:persist_v10_full` ->
  `build_v10_ledger_entry` -> `_write_to_ledger` -> `get_ledger().write`.
  The live scanner attaches its V10 payload to the recorder's authoritative row.
- `DecisionLedgerWriter._flush_locked` groups serialized rows by their existing
  symbol and `timestamp[:10]`, calls `_write_local`, then `_write_s3`.
- `_write_local` appends/fsyncs `logs/decision_ledger/{symbol}/{date}.jsonl`.
- Previously, `_write_s3` called
  `core/production_data_contract.py:canonical_s3_key` without `part`, obtaining
  the default `part-000.jsonl`. It downloaded the existing object, prepended its
  body to the new batch, and performed an unconditional `put_object`. Read
  failures were also treated as absence. Concurrent writers could lose updates;
  every successful append replaced the same S3 key.

The supplied production evidence identifies bucket `trading-bot-v10-data`,
writer session `20260909T102852773535Z-6f626150ee2a443b83540258e1111797`, and
15 versions of the reused leaf during the observed window. This local task
traced the corresponding repository path; it did not inspect the live process.

## Change

Each `_write_s3` call now uploads only its new batch to:

```text
core/decision_ledger/schema_version=decision_ledger_v1/symbol={SYMBOL}/date={YYYY-MM-DD}/part-{UTC_TIMESTAMP_MICROSECONDS}-{UUID4_HEX}.jsonl
```

The partition still comes from the record, independently of upload time.
`put_object(IfNoneMatch="*")` rejects unexpected collisions rather than
replacing an object. There is no get/list operation in the writer, no shared
sequence, and no timestamp-only uniqueness assumption. This uses the same
immutable/create-only approach as `core/storage/s3_batch_writer.py`, without
adopting its event-specific buffering, timestamp contract, or threading model.

Failure handling remains the existing local-primary, fire-and-forget behavior:
S3 errors reach `record_s3_failure`; the locally persisted batch remains. This
fix prevents replacement loss; it does not add durable S3 outage retries or
claim delivery through arbitrary network failures. A rejected collision is a
failed mirror operation, never an unconditional retry. Deployment acceptance
must reconcile records, not rely on `total_written` (which counts local flushes).

## Reader compatibility

- `research_engine/data_access/loaders.py:load_decision_ledger` -> `_read` ->
  `S3ResearchDataSource.read_dataset` in `research_engine/data_access/s3_source.py`.
  `_list_prefixes` selects canonical schema/symbol prefixes; `_iter_keys` follows
  `list_objects_v2` pagination and accepts every `.jsonl` leaf. `read_dataset`
  filters dates, reads each key once via `_read_object`, combines and sorts rows.
  It does not require `part-000.jsonl` and does not deduplicate by leaf name.
- `research_engine/experiments/portfolio_ranking.py` and
  `core/causal/replay.py:_load_decision_ledger_record` use this public loader.
- `research_engine/experiments/legacy_canonical.py:_load_jsonl` and
  `research_engine/audit/run_audit.py:_count_jsonl` use the same data source directly.
  Selection analysis consumes already-loaded decision rows.
- Local forensic scripts (`scripts/funnel_*.py`,
  `scripts/broker_rejection_analysis.py`, `audit_v10_ledger.py`) read local JSONL;
  that layout is unchanged. No active decision-ledger single-leaf S3 reader was
  found in the searched production/research code.

No reader changes were required. Old `part-000.jsonl` and new leaves coexist and
are read together. The reader caches results per run: use a new source instance
or `clear_cache()` when verifying newly arrived objects.

## Local verification

Run with the project's `.venv/Scripts/python.exe` (Python 3.12); the system
Python 3.14 lacks pytest. All persistence tests use fake S3 and temporary files.

Focused command:

```powershell
.venv/Scripts/python.exe -m pytest tests/test_decision_ledger_immutable_s3.py -p no:cacheprovider
```

Result: **7 passed**. Covers consecutive key uniqueness and unchanged first
object, 32 independent concurrent writers with a frozen clock, complete recovery
across paginated listings, mixed old/new leaves, full payload and canonical ID
equality, V1 schema and symbol/record-date partition stability, unchanged local
record semantics, forced collision rejection/failure reporting, and installed
SDK support for `IfNoneMatch`.

Existing regression command:

```powershell
.venv/Scripts/python.exe -m pytest tests/test_decision_ledger_integrity.py tests/test_decision_ledger_invariant.py tests/test_decision_recorder.py tests/test_dual_ev_ledger_persistence.py tests/test_v10_persistence_adapter.py tests/test_v10_persistence_truth.py tests/test_s3_event_object_key_collision.py tests/test_research_engine_s3_source.py tests/test_research_engine_s3_source_guard.py tests/test_research_loaders.py tests/test_s3_dataset_role_contract.py tests/test_s3_architecture_guard.py tests/test_replay_decision_audit_migration.py -p no:cacheprovider
```

Result: **132 passed, 3 failed**. Failures concern untouched source/artifacts:

1. `test_every_continue_after_init_has_finalize`: existing scanner `continue`
   at line 1702 lacks the guard's expected preceding finalizer.
2. `test_only_registered_modules_have_put_object`: flags pre-existing untracked
   `migration_audit/candle_timeframe_identity/final_cleanup_execution_v1.py`.
3. `test_only_registered_modules_import_boto3`: flags pre-existing untracked
   migration/verification scripts in that same directory.

The initial broader invocation also included `tests/test_v10_s3_persistence.py`;
collection failed because its unrelated research-report publisher import
`research_engine.v10.persistence.s3_publisher` is absent. It was omitted to run
the remaining regressions. These unrelated failures were not repaired or hidden.
`git diff --check` passed.

All three failing cases were rerun with the original HEAD ledger implementation
loaded into the test process in memory; all three failed again. This baseline
check did not replace the working file or touch any bot process. The architecture
checks still scanned the unchanged workspace artifacts described above.

## VM deployment and fresh acceptance (not executed)

1. Review/package the bounded patch. The only runtime file is
   `core/decision_ledger.py`; include this report and the new test for review.
   Do not deploy the workspace's untracked migration/cleanup artifacts.
2. On the VM, confirm the target checkout used by the bot and compare its ledger
   source to the expected pre-change file. Apply only this patch to that checkout.
   Check the VM interpreter's botocore S3 `PutObject` model contains
   `IfNoneMatch`; the project pins `boto3==1.43.84` in `requirements.txt`.
   Run the focused tests against fake S3 using the VM deployment environment.
   Do not remove the conditional header if an older SDK rejects it; resolve
   dependency compatibility before activating the change.
3. Activation requires an explicitly approved controlled restart using the VM's
   existing bot supervisor. Drain the old ledger using its normal graceful
   shutdown, confirm the old process has exited, then start the patched process.
   Ensure every decision-ledger writer uses the patched version. No restart,
   process inspection, deployment, or production write was performed here.
4. After old-process shutdown and before new-process start, capture a read-only
   baseline of the ledger prefix's keys and all paginated S3 versions, plus local
   ledger file byte offsets. Record the activation UTC boundary and new process
   identity. Do not use the old event writer's session string to select ledger
   leaves: ledger leaves use per-upload UTC timestamps and UUIDs.
5. Observe at least two successful partition flushes from the new process, then
   allow the normal timer to flush records through a fixed verification cutoff.
   Re-list objects and all versions under `core/decision_ledger/` in
   `trading-bot-v10-data`; retain key, LastModified, VersionId, ETag, size, and
   downloaded-body hashes. Define fresh keys as post-activation keys absent from
   the baseline. Check no baseline key acquired a post-activation content version
   and no fresh key has more than one distinct content VersionId. Paginate both
   object and version listings; inspect all affected symbols/dates, including a
   UTC date rollover if present.
6. Compare fresh S3 records against the locally appended, fully flushed records
   within that same bounded window. Compare multisets of decision IDs and full
   normalized JSON payloads so missing records and duplicate multiplicities are
   visible. Account for flush boundaries using actual local persisted rows, not
   only decision creation times. Reconcile across all writer instances if more
   than one is running. Any mirror failure or unmatched record prevents PASS.
7. Start a fresh `S3ResearchDataSource` (or clear its cache) and call the active
   `load_decision_ledger` for each affected symbol. Filter the returned rows to
   the verification manifest and compare their full record multiset to the fresh
   object contents and local expected manifest. Existing records remain readable.

Required acceptance:

```text
fresh decision_ledger objects > 1
reused keys = 0
fresh keys with multiple content VersionIds = 0
record loss = 0
reader successfully loads all fresh records
```

Do not delete, rewrite, restore, quarantine, or otherwise modify historical S3
objects. Fresh production acceptance remains pending deployment.
