# Stage A — FRONTIER Recovery Integration and Qualification Report

**Repository:** `C:\Users\ikues\Trading-bot-stage-a-restored`
**Branch:** `stage-a-completion` · **HEAD:** `4d2ba9491b4ba391e193003bb9a89b89acb41d02`
**Status:** integrated and qualified with two consecutive **offline** diagnostic
attempts (in-memory S3 double) plus focused suites. **No** real S3 request, **no**
research cycle, **no** EC2/MT5 change, **no** commit or push. No existing research
artifact (checkpoint, cache body, manifest, frontier state) was deleted or
rewritten.

## 1. Verdict

**Diagnostic recovery is correctly integrated.** The newly added
resumable-acquisition checkpoint is reachable end-to-end from the supervised
diagnostic launcher through the cadence service, the continuous research loop and
the frontier coordinator; a timed-out attempt preserves completed-dataset
checkpoints; the next attempt (fresh isolated state root) loads them, revalidates
every reused object against fresh S3 authority, avoids the corresponding body
downloads, and produces a snapshot **fingerprint-identical** to an uninterrupted
run. The 45-minute watchdog, idle-sleep protection and exact-process termination
are unchanged.

## 2. Checkpoint-directory trace (launcher → cadence → loop → coordinator)

| Layer | File | What it does with the checkpoint |
|---|---|---|
| Launcher | `scripts/run_frontier_diagnostic.py` | child `--state-root` = `{DIAGNOSTIC_STATE_ROOT}`; `--diagnostic-cache-directory` = `<watchdog --state-root>/verified_object_bodies` |
| Watchdog | `scripts/run_frontier_diagnostic.py` (`run_watchdog`) | `diagnostic_state=True` → `monitored_state = log_dir/"state"` (isolated per attempt); substitutes the placeholder in `--state-root` only |
| Cadence | `research_engine/v10/continuous/research_cadence.py` (`run_cadence_tick`) | forwards `frontier_diagnostic=True` and `frontier_diagnostic_cache_directory` into the cycle |
| Loop | `research_engine/v10/continuous/research_loop.py` (`run_continuous_research_cycle`) | `cache_directory = frontier_diagnostic_cache_directory or <state_root>/verified_object_bodies`; sets `frontier_options.setdefault("checkpoint_directory", Path(cache_directory).parent / "frontier_acquisition_checkpoint")`; for a diagnostic also `state_directory=<root>/frontier_state`, `manifest_directory=<root>/manifests` |
| Coordinator | `research_engine/v10/continuous/frontier_coordinator.py` (`run_frontier_snapshot_cycle`) | `FrontierAcquisitionCheckpointStore(checkpoint_directory)` — default `<state_directory>/acquisition`; loads/saves/clears the roster |

The key split: the **shared, persistent** locations are derived from the
*diagnostic cache directory* (which is rooted at the watchdog `--state-root`),
while the **authoritative scientific state** (`frontier_state/`, `manifests/`,
`cycles/`, `cycle_progress.json`) is rooted at the **isolated per-attempt state
root** (`log_dir/state`). Separate attempts therefore discover the same checkpoint
without ever sharing authoritative state.

## 3. Exact persistent checkpoint location

```
<watchdog --state-root>/frontier_acquisition_checkpoint/acquisition_checkpoint.json
```

With the launcher defaults (`--state-root data/research/continuous`) the absolute
path is:

```
C:\Users\ikues\Trading-bot-stage-a-restored\data\research\continuous\frontier_acquisition_checkpoint\acquisition_checkpoint.json
```

This directory does **not** exist yet, which is expected: no real attempt has run
with the new code. It is created (atomically) the first time a dataset completes
and is removed only after a successful snapshot is persisted. The verified-body
cache (`data/research/continuous/verified_object_bodies/`, 27,100 existing bodies)
sits beside it and is untouched.


## 4. Guarantees confirmed (tasks 2, 3, 4, 6, 7, 8)

**Task 2 — separate attempts discover the same persistent checkpoint without
sharing authoritative state.** Confirmed by construction (§2, §3) and by tests:
the checkpoint + body cache live under the shared `--state-root`; `frontier_state`,
`manifests` and `cycles` live under the isolated `log_dir/state`. Existing
watchdog test `test_diagnostic_state_is_isolated_from_existing_progress` and new
`test_checkpoint_isolation_keeps_authoritative_state_per_attempt`.

**Task 3 — a timed-out attempt preserves completed dataset checkpoints.** The
coordinator fires `on_dataset_verified` (→ `checkpoint_store.save`, atomic
temp-file + `os.replace`) once per completed dataset, **before** the
`FRONTIER_DATASET_VERIFIED` substage is emitted. A kill between datasets therefore
leaves datasets `1..N` checkpointed. New
`test_timed_out_attempt_preserves_checkpoint_then_next_attempt_resumes` asserts the
interrupted attempt leaves exactly datasets `1..2` in the checkpoint and **no**
`latest_success.json` / history / `ISNAP-*.json`.

**Task 4 — the next attempt loads them and resumes after fresh source-authority
verification.** `checkpoint_store.load(boundary, not_before=last_success_ts)` feeds
`_discover_verified(resume=…)`. Each resumed object is still matched against fresh
S3 authority (ListObjectsV2 identity + one fresh `HeadObject` for versioned
objects) and re-read when changed; the checkpoint is a *reuse prior*, never an
authority. New test asserts **0 body GETs** for recovered datasets and fingerprint
parity with a cold acquisition.

**Task 6 — a successful completion clears the checkpoint only after the
authoritative snapshot is safely persisted.** Order in
`run_frontier_snapshot_cycle`: snapshot frozen → verified → `store.save_success`
(atomic `latest_success.json`) → `store.save_candidate_promotion` →
`checkpoint_store.clear()`. New test asserts the shared checkpoint is gone **and**
`frontier_state/latest_success.json` exists.

**Task 7 — corrupt / stale / changed-boundary / incompatible checkpoints fail
closed or safely fall back.** `FrontierAcquisitionCheckpointStore.load` returns
`None` (→ full cold acquisition, never a partial authority) for every doubt:

| Failure | Guard | Result |
|---|---|---|
| Unreadable / malformed JSON | `json.loads` in `try` | ignored → cold acquisition |
| Wrong schema | `checkpoint_schema != CHECKPOINT_SCHEMA` | ignored |
| Tampered roster | `checkpoint_fingerprint` recompute mismatch | ignored |
| Changed boundary (bucket/region/profile/schemas) | `canonical_json(boundary)` mismatch | ignored |
| Stale (older than last success) | `not_before` + `_is_newer` | ignored |
| Over disk budget | `save` returns `False`, file not written | behaves like cold acquisition |

A **changed object inside a recovered dataset** is detected per object (listing
identity / fresh HEAD) and re-downloaded exactly once; an **invalid** changed
object fails closed (`FRONTIER_INVALID`, no success state).

**Task 8 — watchdog preserved.** No change to `_prevent_idle_sleep`,
`_stop_owned_child` (CTRL_BREAK → `Popen.kill()` on the *owned* handle),
`_windows_kill_on_close_job`, deadline/stall logic or `--max-seconds`. Only
**additive, read-only** extraction of the new recovery fields into the watchdog
samples/receipt. `tests/test_frontier_diagnostic_watchdog.py` (27 tests, incl. the
forced-stop, descendant-job and runtime-limit tests) is green.

## 5. Added progress and timing measurements (task 5)

All are **diagnostic only** (best-effort substage events; never consulted by the
governed result). New/updated fields:

- **`FRONTIER_DISCOVERY_STARTED`** (checkpoint load & validation):
  `checkpoint_loaded`, `checkpoint_path`, `checkpoint_load_seconds`,
  `resumed_datasets`, `resumed_objects`.
- **`FRONTIER_DATASET_VERIFIED`** (per dataset): `objects_reused`,
  `revalidation_seconds` (fresh-authority HEAD time for the reused objects),
  `elapsed_seconds` (unchanged), `source_io_cumulative` (unchanged).
- **`FRONTIER_DISCOVERY_COMPLETED`** (aggregate): `datasets_recovered`,
  `objects_reused`, `revalidation_seconds`, `recovered_datasets_seconds`
  (wall time for recovered datasets), `remaining_datasets_seconds` (wall time for
  the not-yet-complete datasets).
- **Fresh HEAD / GET / cache-hit** counts remain in `source_io_cumulative`
  (`head_calls`, `get_calls`, `verified_cache_hits`, `verified_cache_misses`,
  `verified_cache_writes`) and in the watchdog `counters`.
- **Watchdog**: `_sample` now carries a `recovery` block with the fields above;
  `run_watchdog` records the last observed `recovery` block in `outcome.json`.

## 6. Offline two-attempt qualification (verification)

Reproduce with `python _stage_a_recovery_harness.py` (in-memory S3 double; no
network). Population: 10 datasets / 400 objects. Attempt 1 is interrupted after
dataset 4 (a timed-out attempt); attempt 2 runs on a fresh isolated state root
with the **same** shared checkpoint. "Cold before" is an uninterrupted run of the
same objects.

### Scenario A — checkpoint only (no body cache)

| Phase | Wall clock | GET | HEAD | cache hits |
|---|---|---|---|---|
| Cold before (uninterrupted) | 0.754 s | 400 | 0 | 0 |
| Attempt 1 (interrupted @ dataset 4) | — | 160 | 0 | 0 |
| Attempt 2 (resumed, fresh state root) | 0.744 s | **240** | **160** | 0 |

Recovery measurements from attempt 2: `checkpoint_loaded=True`,
`checkpoint_load_seconds≈0.004`, `resumed_datasets=4`, `resumed_objects=160`,
`datasets_recovered=4`, `objects_reused=160`, `revalidation_seconds≈0.041`,
`recovered_datasets_seconds≈0.041`, `remaining_datasets_seconds≈0.058`.
Checkpoint after attempt 1: 4 datasets / 160 objects (atomic, preserved).
`fingerprint_parity = true`.

### Scenario B — checkpoint **and** warm verified-body cache (mirrors the real diagnostic)

| Phase | Wall clock | GET | HEAD | cache hits |
|---|---|---|---|---|
| Cold before (populates cache) | 4.814 s | 400 | 400 | 0 |
| Attempt 1 (interrupted @ dataset 4) | — | 0 | 160 | 160 |
| Attempt 2 (resumed, fresh state root) | **1.664 s** | **0** | 400 | **240** |

Recovery measurements from attempt 2: `datasets_recovered=4`,
`objects_reused=160`, `revalidation_seconds≈0.044`,
`recovered_datasets_seconds≈0.252`, `remaining_datasets_seconds≈0.709`.
`fingerprint_parity = true`.

**Reading of the numbers.** The checkpoint removes the *re-acquisition* of the
recovered datasets (no re-list/re-read/re-decode of their bodies: 160 GETs
avoided in scenario A). With the persistent body cache (the real diagnostic
configuration) the recovered datasets need only one fresh HEAD each, the remaining
datasets are served from the cache (240 hits), and **no body GET is issued at
all** — the wall-clock drops ~65 % versus the cold run. The residual cost is the
mandated fresh authority HEAD per versioned object (160 + 240 = 400), which is
exactly the required "never trust an expired HEAD" revalidation. Because the fake
client is in-memory, the wall-clock gap understates the production saving (real
S3 GET/decode latency dominates); the GET/cache-hit counts are the
latency-independent evidence.

## 7. Focused test results

New file `tests/test_frontier_diagnostic_recovery.py` (7 tests):

```
python -m pytest tests/test_frontier_diagnostic_recovery.py -q -p no:cacheprovider   # 7 passed
```

- `test_timed_out_attempt_preserves_checkpoint_then_next_attempt_resumes` — the
  two-consecutive-attempt proof (checkpoint preserved, 0 GET for recovered
  datasets, measurement fields present, checkpoint cleared after success,
  fingerprint parity with a cold run).
- `test_checkpoint_isolation_keeps_authoritative_state_per_attempt`.
- `test_corrupt_checkpoint_falls_back_to_cold_acquisition`.
- `test_watchdog_sample_reports_recovery_measurements`.
- `test_watchdog_sample_reports_no_recovery_for_a_cold_attempt`.
- `test_checkpoint_schema_constant_is_stable`.

Regression suites re-run green after the change:

| Suite | Result |
|---|---|
| `test_frontier_resumable_acquisition.py` + `test_frontier_substage_progress.py` + `test_frontier_coordinator.py` + `test_frontier_diagnostic_recovery.py` + `test_cold_start_cache_recovery.py` + `test_incremental_acquisition_replay.py` + `test_cycle_snapshot_inputs.py` | 122 passed |
| `test_investigation_snapshot.py` + `test_incremental_snapshot_materialization.py` + `test_continuous_research_block4.py` + `test_stage_a_completion_repairs.py` + `test_frontier_freeze_schema_integrity.py` | 79 passed |
| `test_frontier_authority_head_concurrency.py` | 6 passed |
| `test_frontier_diagnostic_watchdog.py` | 27 passed |

`git diff --check` clean; all edited modules compile.

## 8. Exact command for the next bounded real S3 qualification run

```powershell
python scripts/run_frontier_diagnostic.py --state-root data/research/continuous --log-root logs/frontier_diagnostics --max-seconds 2700
```

The launcher injects `AWS_PROFILE=trading-bot-new` and
`RESEARCH_AWS_PROFILE=trading-bot-new`, isolates the per-attempt state root under
`logs/frontier_diagnostics/<UTC>-<uuid>/state`, and points the persistent
checkpoint at
`data/research/continuous/frontier_acquisition_checkpoint/acquisition_checkpoint.json`.
Exit code `0` means `frontier_completed` with a validated snapshot; a non-zero
exit is a bounded failure whose `outcome.json` (now including the `recovery`
block) shows how far acquisition got. This command was **not** executed.

## 9. Limitations (stated explicitly)

- Resumption still performs **one fresh HEAD per versioned object** in a recovered
  dataset (authority must be re-proven). The saving is the body GET/decode/recompute
  and the restart at the wrong dataset, not the authority HEAD itself.
- Versionless (`null`-version) objects are never cached and always re-fetched;
  such a population gains no resume benefit.
- The checkpoint only helps when the diagnostic wiring points it at a persistent
  directory; the coordinator default (`<state_directory>/acquisition`) is
  per-attempt when the state root is per-attempt.
- Only focused offline suites and the in-memory harness were run. No
  production-scale timing, memory, concurrency, or S3 throttle behaviour was
  measured, and no 45-minute test was launched (per the task instruction).
