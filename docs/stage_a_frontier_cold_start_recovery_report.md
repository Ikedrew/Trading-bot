# Stage A — FRONTIER Cold-Start Recovery and Acquisition Repair

**Repository:** `C:\Users\ikues\Trading-bot-stage-a-restored`
**Branch:** `stage-a-completion` · **HEAD:** `4d2ba9491b4ba391e193003bb9a89b89acb41d02`
**Status:** implemented and qualified with focused offline tests only.
**No** production run, **no** new real S3 downloads, **no** EC2/MT5 change,
**no** commit or push. The existing 27,100 cached objects were not modified.

Scope: Priority A (safe cold-start cache reuse), Priority B (performance
instrumentation and acquisition), Priority C (focused acceptance). Scientific
question evaluators and candidate governance were not altered.

## 1. Exact changes and files modified

Tracked modifications in this checkout (`git diff --stat`):

| File | Change |
|---|---|
| `research_engine/data_access/s3_source.py` | Verified-body cache + authority checks + instrumentation + **cold-start discovery** |
| `research_engine/v10/investigation_snapshot.py` | `CycleSnapshotInputs` shared admission, incremental freeze |
| `research_engine/v10/continuous/frontier_coordinator.py` | **FRONTIER substage progress callback** |
| `research_engine/v10/continuous/research_loop.py` | Cycle source wiring, **throttled FRONTIER substage persistence** |
| `research_engine/v10/continuous/canonical_question_cycle.py` | Cycle replay, shared admission, timings |
| `research_engine/v10/continuous/q71_worker.py` | Shared admission for generated questions |
| `research_engine/v10/continuous/question_cycle_state.py` | Immutable replay/checkpoint receipts |
| `research_engine/v10/continuous/cycle_state.py` | Durable cycle progress store |
| `research_engine/v10/continuous/research_cadence.py` | Cadence service lease + progress |
| `research_engine/v10/continuous/research_projection.py` | Immutable history verification on save |
| `research_engine/v10/continuous/research_lab.py` | Lab view derivation/caching |
| `research_engine/control_plane/evidence_provenance.py` | Per-operation verified reuse + diagnostic timing |
| `research_engine/control_plane/evidence_resolver.py` | Diagnostic timing only |
| `tests/test_frontier_coordinator.py` | Canonical required fields added to synthetic fixtures (test-only) |

New focused test files (isolated, immutable fixtures, recording S3 double):

- `tests/test_cold_start_cache_recovery.py` — Priority A acceptance.
- `tests/test_frontier_substage_progress.py` — Priority B.2 acceptance.
- `tests/test_incremental_acquisition_replay.py`, `tests/test_cycle_snapshot_inputs.py`,
  `tests/test_benchmark_research_pipeline.py` — Phase 2A/2B contracts (prior work).

`git diff --check`: clean (exit 0).

## 2. Cache-recovery mechanism and authority checks

**Cache entry format.** Each verified body is one file
`<sha256(full identity)>.cache` under
`data/research/continuous/verified_object_bodies/`. The file is
`<one-line JSON identity>\n<cached raw body bytes>`, written atomically
(temp file + `fsync` + `os.replace`). The identity binds:

`cache_schema, bucket, region, profile, dataset, schema` and, per object,
`identifier, version_id, etag, size, last_modified, content_sha256,
byte_size, row_count`.

**Two reuse paths, both fail closed on doubt:**

1. *Exact-identity reuse* (`_open_verified_body`) — used when the caller already
   holds a pinned object identity (manifest/BoundObject with a digest). The file
   name is the identity hash; the header must equal the identity and the body
   length must equal `byte_size`; then a **fresh authorized HEAD** must still
   match `identifier, version_id, etag, size, last_modified`.
2. *Bounded cold-start discovery* (`_open_discovered_verified_body`) — used when
   the caller has fresh S3 authority but **no digest yet** (the frontier freeze
   path). It:
   - resolves immutable version authority with **one HEAD** (no body) when the
     listing item lacks a version;
   - builds an **authority-only key** (`identifier + version_id + etag + size +
     last_modified`) — never the digest — and looks it up in a **bounded,
     one-time index** built by scanning existing `.cache` headers (headers only,
     never whole bodies, never deleting or rewriting anything);
   - re-reads the cached body and **re-verifies `content_sha256`** and size;
   - performs a **fresh authorized HEAD** and requires the cached
     `identifier, version_id, etag, size, last_modified` to still match;
   - only then reuses the bytes and adopts the entry's verified
     `content_sha256 / byte_size / row_count` as the object identity.
   Any located-but-invalid entry raises `VERIFIED_BODY_CACHE_INVALID` (fail
   closed). When no entry exists the reader falls back to a normal S3 GET.

**Governance preserved.** Only immutable versions are reusable (`version_id`
must be present and not `null`). Cached bytes never grant authority — a current
authorized HEAD is always required. Versionless objects are always re-fetched
and never cached. Writes are skipped (never evicted) when the cache budget is
reached, so reusable evidence is never destroyed during recovery; interrupted
`.partial` files are left untouched. Snapshot membership and immutable evidence
identities are unchanged: a reused body produces the identical digest, row
count and snapshot fingerprint.

## 3. Before/after GET counts on the same small deterministic fixture

Fixture: 10 bound datasets × 1 object each, one day, in-memory S3 double
(`tests/test_incremental_acquisition_replay.py`, scenario `benchmark()`),
one process per scenario. "Before" is the original implementation behaviour for
that scenario (a full re-read); "after" is the verified-cache behaviour.

| Scenario | Before GET | After GET | HEAD (after) | Reused datasets |
|---|---:|---:|---:|---:|
| Cold freeze (`full`) | 10 | 10 | 10 | 0 |
| Unchanged re-read (`unchanged`) | 10 | **0** | 10 | 10 |
| One object replaced (`delta`) | 10 | **1** | 10 | 9 |

Cold-start reuse (`tests/test_cold_start_cache_recovery.py`): a first freeze
downloads 10 bodies (10 GETs, empty cache); a **brand-new source with no prior
manifest or digest** then re-freezes and issues **0 repeat GETs** with
`remote_body_bytes == 0`, producing a byte-for-byte identical snapshot
fingerprint (`ISNAP-5F7C7315FF035FD55520FA83`). A single changed object is
re-fetched exactly once (1 GET) and the other 9 datasets reuse the cache.

## 4. Measured performance changes (real measurements vs estimates)

**Real measurements (this checkout, single run, synthetic, not production):**

| Scenario | Wall s | GET | HEAD | Peak Python bytes |
|---|---:|---:|---:|---:|
| full | 0.1620 | 10 | 10 | 1,158,673 |
| unchanged | 0.1237 | 0 | 10 | 380,188 |
| delta | 0.1368 | 1 | 10 | 1,114,239 |

Connected synthetic benchmark (`tools/benchmark_research_pipeline.py --rows 180`,
E3 only, 69 evaluators not exercised): COLD 11.58 s / GET 10 / HEAD 20;
UNCHANGED 5.65 s / GET 0 / HEAD 20; INCREMENTAL 13.58 s / GET 1 / HEAD 21.

The mechanism is a **real call-count reduction** (10 → 0 GETs unchanged;
10 → 1 GET on a one-object delta), measured on the fixture. On the production
shape (27,100 objects) a true cold start would replace ~27,100 body GETs with
~27,100 cheap HEADs plus local cache reads; that production-scale saving is an
**estimate**, not a measured result, and no production speedup is claimed.
Absolute seconds are host-, IO- and warmup-dependent.

**Estimates / non-claims:** no EC2, no production population, no soak, no
concurrency-at-scale. Process RSS here is dominated by interpreter + import
overhead at this fixture size.

## 5. Focused test results

Focused offline acceptance set (isolated temp dirs, fake S3, no network):

- `tests/test_cold_start_cache_recovery.py` — **3 passed** (cold-start zero
  repeat GETs + identical fingerprint; changed object reacquired once;
  corrupted entry fails closed).
- `tests/test_frontier_substage_progress.py` — **6 passed** (ordered meaningful
  substage events; failing callback cannot change the result; failed frontier
  never reports completion; throttling bounds writes and always keeps phase
  transitions; the continuous loop persists substage progress and never writes a
  false completed-cycle record).
- Combined focused set (cold-start + incremental acquisition + snapshot
  materialization + cycle snapshot inputs + investigation snapshot + S3 source +
  frontier coordinator + substage progress + frontier freeze schema) — **161
  passed, 0 failed** (`pytest ... -q`, no `F`/`E`).
- Continuous-loop suites (`test_continuous_research_block4.py`,
  `test_research_work_scheduling.py`) — 25/26 passed; the single failure was a
  wall-clock assertion (`elapsed < 10.0`, observed 10.7 s) under concurrent test
  load. It **passes in isolation (exit 0)** and is not a correctness regression.
- `git diff --check` — clean (exit 0).
- Change total: 16 tracked files changed, 1688 insertions(+), 148 deletions(-).

## 6. Remaining cold-start limitations

- Reuse still requires **immutable version authority**. Objects stored with a
  `null` version are always re-fetched and never cached; such a population gets
  no cold-start benefit.
- Discovery costs **one HEAD per not-yet-known object**. That is far cheaper than
  a body GET, but it is not free, and a bucket/credential that forbids
  `HeadObject` degrades to full GETs.
- The discovery index is built **once per source instance** from a bounded scan
  of existing headers (27,100 header reads at production scale). Entries written
  after that scan are visible to the exact-identity path but not to discovery
  until the next instance/run.
- The cache is at its configured 512 MiB budget, so **new bodies are not cached**
  (writes are skipped, never evicting) until the budget or population changes.
- Unchanged-dataset delta reuse at the FRONTIER level still depends on the
  persisted frontier state (`latest_success`); discovery closes the
  no-prior-state case but does not change delta semantics.
- Only the focused offline suite was run. No production-scale timing, memory,
  concurrency-at-scale, throttle behaviour against real S3, or soak was measured.
- The pre-existing `test_frontier_coordinator.py` fixtures were incomplete
  relative to the canonical profile (a HEAD-era mismatch, unrelated to this
  task); they were corrected with the canonical required fields so the
  acceptance suite is valid. No production contract was weakened.

## 7. Can the existing 27,100 cached objects be reused on the next real run?

**Yes — safely, subject to unchanged immutable authority.** The existing entries
were written by this implementation and carry the full identity (including
`version_id`, `etag`, `size`, `last_modified`, `content_sha256`, `byte_size`,
`row_count`). On the next run, bounded discovery can therefore match each entry
from fresh S3 authority alone (fresh HEAD) without a prior manifest:

- unchanged, versioned object → cache reused, **no body GET**;
- changed object → discovery key no longer matches → normal GET + revalidate;
- versionless object → always re-fetched;
- corrupted entry → fails closed (`VERIFIED_BODY_CACHE_INVALID`);
- existing files are **never deleted, rewritten or invalidated** (headers only
  are read; writes are skipped at budget).

The one condition the operator must confirm before relying on it: the next run
must use the **same bucket / region / profile / dataset schemas** as the run
that produced the cache (the identity binds these). If any of those change, the
entries correctly will not match and bodies are re-fetched.

## 8. Bounded verification command for the next real attempt (NOT executed)

Run this **offline focused acceptance command** (no production access, no real
S3 downloads, no research cycle). It is the single bounded check to run before
any real attempt:

    python -m pytest tests/test_cold_start_cache_recovery.py tests/test_frontier_substage_progress.py tests/test_incremental_acquisition_replay.py tests/test_cycle_snapshot_inputs.py tests/test_frontier_coordinator.py -q -p no:cacheprovider

Expected: all green, exit 0, no network. This command was **not** executed as
part of this report's final step; the equivalent set was run above during
qualification.
