# Phase 2B — Incremental Acquisition & Replay: Qualification Report

Status: **QUALIFIED (bounded, offline, synthetic fixtures only)**
Scope: Stage A Phase 2B final qualification only. No Phase 2C. No commit/push/deploy.

## 1. Deliverable completed

Added the missing bounded offline benchmark to the existing untracked
`tests/test_incremental_acquisition_replay.py` (Phase 2B contracts). The
benchmark was previously absent (the file already imported `time`,
`tracemalloc`, `deepcopy`, and `process_memory_bytes` but used none of them —
dangling scaffolding for exactly this deliverable).

Nothing else was changed. All prior uncommitted source edits and all untracked
test files are preserved untouched.

## 2. How the benchmark stays out of normal test execution

- `pytest.ini` sets `python_functions = test_*`. The benchmark function is
  named `benchmark(...)` (no `test_` prefix), so pytest never collects it.
- Verified: `pytest --collect-only` on the file reports **34** tests (the 34
  contract tests), not 35. The benchmark is invisible to normal runs.
- The benchmark is invoked explicitly, one scenario per process:

      PYTHONPATH=<repo root> python tests/test_incremental_acquisition_replay.py <scenario>

  with `<scenario>` in `{full, unchanged, delta}`.

## 3. Workloads are synthetic and bounded

- In-memory fake S3 (`MemoryS3` from `test_incremental_snapshot_materialization`).
  **No remote client, no network, no production data.**
- One day (`2026-09-25`), one part per dataset, 10 bound datasets
  (6 REQUIRED + 4 OPTIONAL), single-row JSONL bodies.
- Every scenario runs in its own short-lived process with a disposable
  `tempfile.TemporaryDirectory` cache.

## 4. Memory measurement: process-wide high-water vs per-scenario

Two distinct measures are reported on purpose:

- `tracemalloc_peak_bytes` — the true **per-scenario** Python allocation peak
  (tracemalloc is started/stopped around the measured work only).
- `process_peak_rss_bytes` / `rss_bytes` — the **process-wide** OS high-water
  mark from `process_memory_bytes()`. On Windows `PeakWorkingSetSize` is
  monotonically non-decreasing, so it would be misleading if scenarios shared a
  process. This is avoided by running **one scenario per process**, making the
  process-wide figure attributable to that scenario alone. It is never used to
  infer per-scenario deltas; tracemalloc serves that role.

## 5. Measured results (synthetic fixture, not production-scale)

| scenario   | datasets | rows | seconds  | GET | HEAD | reused datasets | reread objs | tracemalloc peak (B) | process peak RSS (B) |
|------------|----------|------|----------|-----|------|-----------------|-------------|----------------------|----------------------|
| full       | 10       | 10   | 0.2482   | 10  | 10   | 0               | 10          | 1,270,171            | 63,631,360           |
| unchanged  | 10       | 10   | 0.1325   |  0  | 10   | 10              | 0           |   366,988            | 62,664,704           |
| delta      | 10       | 11   | 0.1399   |  1  | 10   | 9               | 1           | 1,354,046            | 62,881,792           |

Observations (bounded, synthetic):
- **unchanged**: zero body downloads (`GET=0`); all 10 datasets reuse
  predecessor authority. Versioned objects still require one HEAD each to
  confirm continued authority. Resulting snapshot fingerprint is identical to
  the full freeze (`ISNAP-5F7C7315FF035FD55520FA83`) — byte-for-byte equivalent.
- **delta**: adding one object to one dataset triggers exactly one body download
  (`GET=1`); the other 9 datasets are reused. The incremental snapshot equals a
  clean full freeze over the same membership (asserted per-dataset on objects +
  content digest).

## 6. Correctness / equivalence assertions inside the benchmark

- `unchanged`: `assert snapshot.snapshot_fingerprint == predecessor.snapshot_fingerprint`.
- `delta`: per-dataset `assert actual.objects == expected.objects` and
  `assert actual.content_digest == expected.content_digest` against a fresh full
  freeze.

## 7. Regression verification

- `python -m py_compile tests/test_incremental_acquisition_replay.py` → OK.
- `git diff --check` → exit 0 (no whitespace/conflict errors).
- Full Phase 2B suite:
  `pytest tests/test_cycle_snapshot_inputs.py tests/test_incremental_acquisition_replay.py`
  → **53 passed, 0 failed**.

## 8. Limitations / non-claims

- All numbers come from tiny synthetic in-memory fixtures. **No production-scale
  improvement is claimed**; timing and memory here illustrate mechanism
  (authority reuse, delta-only body reads), not real throughput.
- Absolute seconds are single-run, un-warmed, and machine/IO dependent; they are
  recorded for reproducibility, not as a performance target.
- Process-wide RSS is dominated by interpreter + import overhead at this fixture
  size; only tracemalloc deltas are meaningful for per-scenario memory.

Stop after Phase 2B. No Phase 2C, commit, push, or deploy performed.
