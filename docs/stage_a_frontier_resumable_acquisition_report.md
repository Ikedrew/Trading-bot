# Stage A — FRONTIER Resumable Acquisition Report

**Repository:** `C:\Users\ikues\Trading-bot-stage-a-restored`
**Branch:** `stage-a-completion` · **HEAD:** `4d2ba9491b4ba391e193003bb9a89b89acb41d02`
**Status:** implemented and qualified with focused offline tests only.
**No** production run, **no** real S3 request, **no** EC2/MT5 or trading change,
**no** commit or push. No existing evidence, manifest, or cache file was deleted
or rewritten.

## 1. Objective

Stop repeated FRONTIER attempts from unnecessarily restarting expensive evidence
acquisition, while preserving scientific and snapshot integrity. Measured
problem: a 45-minute attempt stopped at `execution_context` (dataset 6 of 10),
2,163 / 10,637 objects processed, 30,070 HEAD calls, 2,970 GET calls, 27,100
verified cache hits, progress still changing at timeout, and no completed
snapshot. The next attempt reached the same dataset, i.e. it repeated the work.

## 2. Phase 1 — What recovery already existed

Recovery across **successful** snapshots already existed and works:

- `FrontierStateStore.load_latest_success()` supplies `previous`; `_discover_verified`
  reuses the predecessor's verified roster per object (identical `ListObjectsV2`
  identity, plus a fresh `HeadObject` for versioned objects) and only reads added
  or replaced bodies.
- `freeze_investigation_snapshot_incremental` reuses a predecessor's exact
  `DatasetBinding` authority for unchanged datasets.
- `S3ResearchDataSource` persists verified bodies (one
  `<sha256(identity)>.cache` file per object, header = full immutable identity)
  and reuses them after a fresh authority HEAD.

Recovery across **interrupted** attempts did **not** exist, and was not merely
unused:

- The diagnostic isolates the FRONTIER state root per attempt
  (`scripts/run_frontier_diagnostic.py`: `monitored_state = log_dir/"state"`,
  substituted for `--state-root`). `latest_success.json` is written only on
  success. So an interrupted attempt left **no** `previous`.
- With `previous is None`, `_discover_verified` re-listed and re-verified **every
  object of every dataset from dataset 1**. The persistent body cache removed the
  GETs (27,100 hits) but each cached body still required one fresh authority HEAD
  — hence 30,070 HEADs (27,100 cached + 2,970 uncached) with no reusable
  per-dataset progress.

**Conclusion:** the pre-existing recovery is sufficient *only* between completed
snapshots; it is ineffective across interrupted attempts. A resumable acquisition
checkpoint was missing.

## 3. Phase 2 — Smallest justified correction

Added one bounded, resumable acquisition checkpoint to the existing coordinator.
It is **not** a new research engine and **not** a snapshot authority.

### Files changed

| File | Change |
|---|---|
| `research_engine/v10/continuous/frontier_coordinator.py` | `CHECKPOINT_SCHEMA`, `DEFAULT_CHECKPOINT_MAX_BYTES`, `_checkpoint_boundary`, `FrontierAcquisitionCheckpointStore`, `_discover_verified(resume=…, on_dataset_verified=…)`, `run_frontier_snapshot_cycle(checkpoint_directory=…)` wiring, `__all__` |
| `research_engine/v10/continuous/research_loop.py` | one `frontier_options.setdefault("checkpoint_directory", …)` beside the verified-body cache |
| `tests/test_frontier_resumable_acquisition.py` | new focused offline acceptance (11 tests) |

### Mechanism

`FrontierAcquisitionCheckpointStore` writes
`<checkpoint_directory>/acquisition_checkpoint.json`, atomically (temp file +
`os.replace`), with:

```
{checkpoint_schema, boundary, updated_at, dataset_count, object_count,
 datasets: {name: {objects: [...], event_time_coverage: {...}}},
 checkpoint_fingerprint}
```

Each per-dataset roster entry is the exact `_full_object_record` a completed run
would produce: `identifier`, `version_id`, `etag`, `content_sha256`, `byte_size`,
`row_count`, `historical_guard_rows`, `partition_date` and event-time coverage.

`_discover_verified` fires `on_dataset_verified` after **each** dataset completes,
so `run_frontier_snapshot_cycle` persists progress incrementally. On the next
attempt the checkpoint is loaded and passed as `resume`; it is used **only as a
reuse prior**. Every resumed object is still matched against fresh S3 authority
(the fresh `ListObjectsV2` plus a fresh `HeadObject` for versioned objects) and
re-read when it changed. `_compute_delta` still compares against the real
`previous` success, so delta semantics are unchanged. A full success clears the
checkpoint; an interruption leaves it for the next attempt.

### Requirement mapping

| # | Requirement | How it is met |
|---|---|---|
| 1 | Preserve completed per-dataset work | checkpoint written per completed dataset |
| 2 | Preserve immutable object refs + verified content identities | roster stores identity, version, `content_sha256`, byte size, row count, coverage |
| 3 | Revalidate required S3 authority; never trust expired HEAD | fresh `ListObjectsV2` every attempt; versioned objects always re-HEADed (`head_bound_objects` + `_same_frontier_object_identity`) |
| 4 | Reuse verified local bodies without unnecessary GETs | resumed objects reuse the stored record (no GET at all); the persistent body cache still serves the rest |
| 5 | Do not restart completed datasets unless listing/identity/boundary changed | per-object listing identity + HEAD; `boundary` = schema/bucket/region/profile |
| 6 | Detect changed / missing / invalid and fail closed | changed -> re-read; invalid canonical content -> `FRONTIER_INVALID`; missing -> re-listing; corrupt checkpoint -> rejected |
| 7 | Coherent membership + deterministic fingerprints | proven by fingerprint-equality tests |
| 8 | Atomic, integrity-validated persistence | temp file + `os.replace`; `checkpoint_fingerprint` re-derived and compared on load |
| 9 | Bound disk and memory | `DEFAULT_CHECKPOINT_MAX_BYTES` (64 MiB) skips rather than truncates; memory bounded by the roster the frontier already holds |
| 10 | Incomplete snapshots non-authoritative / unavailable downstream | checkpoint is separate from `latest_success`/manifests and cannot be loaded as a snapshot |
| 11 | Compatible with coordinator + governance | purely additive optional parameters; no change to snapshot or delta authority |

### Where the checkpoint lives

`research_loop.py` points it beside the persistent verified-body cache:
`Path(cache_directory).parent / "frontier_acquisition_checkpoint"`. In the
diagnostic that resolves to the persistent
`data/research/continuous/frontier_acquisition_checkpoint` (survives across
attempts), even though the frontier state root is per-attempt. In production it
resolves to `<state_root>/frontier_acquisition_checkpoint`. When the coordinator
is called directly the default is `<state_directory>/acquisition`.

## 4. Phase 3 — Offline verification

All tests use immutable fixtures and in-memory S3 doubles only: no network, no
real S3, no research cycle, no EC2.

    python -m pytest tests/test_frontier_resumable_acquisition.py -q -p no:cacheprovider

Eleven tests, all green:

- **Store contracts:** round-trip; boundary mismatch rejected; tampered
  `content_sha256` rejected (integrity fingerprint); unreadable JSON rejected; a
  checkpoint superseded by a newer success rejected (`not_before`); a checkpoint
  over `max_bytes` is not written (bounded disk).
- **Resume without repeating completed work:** interrupt after dataset 2, resume,
  assert datasets 1-2 require **zero** GETs, datasets 3-10 are read, and the
  `FRONTIER_DISCOVERY_STARTED` event reports `resumed_datasets == 2`.
- **Fingerprint parity:** the resumed run's snapshot id/fingerprint equal an
  uninterrupted acquisition of the same objects.
- **Changed object revalidation:** an object changed inside a checkpointed dataset
  is re-downloaded exactly once; its unchanged sibling is not.
- **Fail closed:** a checkpointed object replaced with an invalid canonical record
  yields `FRONTIER_INVALID` and no success state.
- **Corrupt checkpoint rejected safely:** a garbage checkpoint degrades to a cold
  acquisition that re-downloads every object once; the run still succeeds.
- **Boundary change invalidates:** a different bucket authority ignores the
  checkpoint and re-downloads everything.
- **Unfinished snapshot non-authoritative:** after an interrupt there is no
  `latest_success.json`, no history directory, no `ISNAP-*.json` manifest, and the
  checkpoint cannot be loaded as an investigation snapshot.
- **Cleared on success:** a successful acquisition removes the checkpoint.

Regression suites re-run green: `test_frontier_coordinator.py` (32),
`test_frontier_substage_progress.py` (8), `test_cold_start_cache_recovery.py` (12),
`test_incremental_snapshot_materialization.py`, `test_investigation_snapshot.py`,
`test_frontier_freeze_schema_integrity.py`, `test_frontier_diagnostic_watchdog.py`
(27), `test_continuous_research_block4.py` (14),
`test_stage_a_completion_repairs.py` (13), plus sampled
`test_incremental_acquisition_replay.py` and `test_cycle_snapshot_inputs.py`.
`git diff --check` is clean; all edited modules compile.

## 5. Can the next bounded run reuse completed work?

**Yes.** On the next real attempt the diagnostic checkpoint persists across
attempts and is loaded at the start of `_discover_verified`. Datasets already
verified in a prior attempt are reused after a fresh authority revalidation, so:

- completed datasets are **not** re-downloaded or re-decoded;
- their bodies are not re-fetched (the persistent cache is still reused for the
  remaining datasets);
- the run resumes at the first dataset that is not yet verified;
- a changed/missing/invalid object or a changed source authority correctly forces
  re-acquisition or fails closed.

The 27,100 existing cached bodies are untouched and remain reusable. The one
condition to confirm before relying on reuse: the same bucket / region / profile
and dataset schemas as the run that produced the checkpoint (the boundary binds
these); if any change, the checkpoint is correctly ignored.

## 6. Limitations (stated explicitly)

- Resumption still performs **one fresh HEAD per versioned object** in a reused
  dataset (the task requires revalidating S3 authority and never trusting expired
  HEAD results). The saving is the body GET/decode/recompute and the restart at
  the wrong dataset, not the authority HEAD itself.
- Versionless (`null` version) objects are never cached and always re-fetched;
  such a population gains no resume benefit.
- The checkpoint is only as useful as the diagnostic wiring that points it at a
  persistent directory; the default (`<state_directory>/acquisition`) is
  per-attempt when the state root itself is per-attempt.
- Only the focused offline suite was run; no production-scale timing, memory,
  concurrency, or S3 throttle behaviour was measured. No 45-minute test was
  launched, per the task instruction.
