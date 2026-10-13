# Phase 2C — Provenance Reuse Qualification Report (Reconciled)

**Authoritative workspace:** `C:\Users\Administrator\Desktop\Trading-bot-stage-a`
**Branch:** `stage-a-completion`
**HEAD:** `4d2ba9491b4ba391e193003bb9a89b89acb41d02`
**Report scope:** Stage A focused qualification only. Documentation only.
**Status:** **QUALIFIED (bounded, focused Stage A only — NOT production deployment ready)**

> **Reconciliation note:** An earlier Phase 2C report was generated using the
> wrong workspace/context and described lifecycle-related files that do not
> match this checkout. This document supersedes it. It was written after
> inspecting the actual working tree named above (`git diff --stat`,
> `git status --short`, `git diff --check`, plus direct reads of
> `research_engine/control_plane/evidence_provenance.py`,
> `research_engine/control_plane/evidence_resolver.py`,
> `tests/test_phase2c_provenance_reuse.py`,
> `docs/phase2a_shared_snapshot_inputs.md`, and
> `docs/phase2b_qualification_report.md`). No unrelated lifecycle changes
> are attributed to this implementation.

## 1. Working tree actually inspected

Tracked modifications in this checkout:

| File | Approx diff vs HEAD | Phase attribution |
|------|---------------------|-------------------|
| `research_engine/control_plane/evidence_provenance.py` | +82 / -5 | **Phase 2C — in scope** |
| `research_engine/control_plane/evidence_resolver.py` | +32 / -0 | **Phase 2C — diagnostic timing only** |
| `research_engine/data_access/s3_source.py` | modified | Pre-existing Phase 2A/2B work — **out of scope** |
| `research_engine/v10/continuous/canonical_question_cycle.py` | modified | Pre-existing Phase 2A/2B work — **out of scope** |
| `research_engine/v10/continuous/q71_worker.py` | modified | Pre-existing Phase 2A/2B work — **out of scope** |
| `research_engine/v10/continuous/question_cycle_state.py` | modified | Pre-existing Phase 2A/2B work — **out of scope** |
| `research_engine/v10/continuous/research_loop.py` | modified | Pre-existing Phase 2A/2B work — **out of scope** |
| `research_engine/v10/investigation_snapshot.py` | modified | Pre-existing Phase 2A/2B work — **out of scope** |

Untracked, present in this checkout:

- `tests/test_cycle_snapshot_inputs.py` — Phase 2A contracts.
- `tests/test_incremental_acquisition_replay.py` — Phase 2B contracts.
- `tests/test_phase2c_provenance_reuse.py` — Phase 2C contracts (7 tests).
- `docs/phase2a_shared_snapshot_inputs.md` — existing Phase 2A record.
- `docs/phase2b_qualification_report.md` — existing Phase 2B record.

Only the two `control_plane` files above constitute the Phase 2C
implementation. The other six tracked modifications are Phase 2A/2B
shared-snapshot / incremental-acquisition work and are explicitly **not**
claimed as Phase 2C provenance changes.

## 2. Implemented provenance changes (actual diff)

### 2.1 `research_engine/control_plane/evidence_provenance.py`

Additive, per-operation verified reuse with byte-identical digest encoding.
No caching across calls; no global or persistent evidence cache.

1. **Diagnostic timing scope only:** new imports
(`contextmanager`, `ContextVar`, `wraps`, `time`), new
`_PROVENANCE_TIMING` ContextVar, new `measure_provenance()` context manager
yielding `{"provenance_seconds": 0.0}`, and new `_timed_provenance`
decorator applied to `select_current_evidence`,
`use_evidence_as_supplied`, and `attest_current_subset`. When no measurement
scope is active the decorator calls through unchanged. Selection, hashing,
classification, and validation algorithms are otherwise unchanged.

2. **Reuse helpers (same encoding as `evidence_digest`):**
`_multiset_digest_from_canonicals()` hashes pre-computed canonical JSON
strings (sorted, joined by `"\n"`, UTF-8, SHA-256) without
re-canonicalizing; `_single_digests_from_canonicals()` derives per-record
digests from the same canonicals. Docstrings restrict this to per-operation
reuse from the exact private record objects attested in the same call.

3. **`_select()`:** records are still isolated first
(`deepcopy(dict(record))` unless `trusted_immutable_inputs` with an
already-`dict` record). Each isolated private record is canonicalized
exactly once per call and those canonicals feed the component `digest`,
removing the prior double canonicalization. Counts, selection modes
(`CURRENT_ONLY` / `AS_SUPPLIED`), state derivation, schema handling, and
`sha256` algorithm are unchanged.

4. **`attest_current_subset()`:** both input and selected populations are
still deep-copied before canonicalization. Each isolated record is
canonicalized exactly once; those canonicals feed both the
duplicate-sensitive subset check (`Counter` subtraction over single-record
digests, fail-closed `ValueError` on forgery or excess multiplicity) and
the final component digest. `CURRENT_SUBSET` accounting and `_subset_state`
are unchanged.

5. **Isolation preserved:** `records_for_analysis()` still returns isolated
copies (`deepcopy` by default). Post-selection caller mutation does not
change the attested digest.

### 2.2 `research_engine/control_plane/evidence_resolver.py`

Diagnostic timing only; no resolution-algorithm change. New
`_NORMALIZATION_TIMING` ContextVar, `measure_normalization()` scope, and
`_timed_normalization` decorator applied to `_normalise()` only. Source
records are never retained.

### 2.3 Not changed by Phase 2C

No persistent/cross-cycle cache, no lifecycle or state-machine semantics,
no production data or remote access, no commit/push/deploy, no Phase 2D.

## 3. Confirmed qualification results (Stage A checkout)

Supplied confirmed results, recorded verbatim. Benchmarks were **not**
rerun and implementation was **not** modified for this report.

- **Focused tests: 60/60 passed** — `test_cycle_snapshot_inputs.py` (Phase 2A)
  + `test_incremental_acquisition_replay.py` (Phase 2B) +
  `test_phase2c_provenance_reuse.py` (Phase 2C, 7 tests: reuse/digest
  equivalence, duplicate-multiplicity rejection, forged-row rejection,
  nested-field tamper rejection, stale-epoch handling, post-selection
  caller-mutation isolation, analysis-copy isolation). All three files are
  present in this checkout.
- **`git diff --check`: passed** (verified here — empty output, exit 0).
- **6,000-row benchmark (before → after):**
  - Wall time: **11.366s → 8.117s (28.6% faster)**.
  - Canonical JSON calls: **24,001 → 18,002** (about 25% fewer; one redundant
    canonicalization per analysed record eliminated plus fixed overhead).
  - Governed result digest: **identical** (correctness preserved).
  - Traced peak memory: **35.38 MB → 35.48 MB** (+0.10 MB, ~+0.3%;
    effectively flat).

## 4. Benchmark limitations

1. Single focused 6,000-row attestation workload — not end-to-end,
   EC2, or production-scale.
2. Single-run timing with no variance, warm-up, or host qualification.
3. Call-count reduction is the mechanism; wall-clock savings are host- and
   data-dependent.
4. Traced peak only — not process RSS, EC2 memory, or a large-population bound.
5. Digest equality is scoped to the benchmarked population and tested modes.
6. Reuse is strictly per-operation over private isolated copies; no
   cross-call/thread/cycle caching was introduced or qualified.
7. Dirty-tree context: numbers are attributed only to the §2 provenance
   reuse, not to unrelated Phase 2A/2B files.

## 5. Remaining risks

- Broader suite is not green (pre-existing frontier/Q71/legacy failures left
  unchanged rather than weakening contracts).
- `trusted_immutable_inputs=True` trusts caller-held `dict` identity; misuse
  with mutable/aliased objects would violate isolation (default `False`
  remains fail-closed).
- Subset checking inherits the standard SHA-256 collision assumption.
- Untracked local `tools/_phase2c_*` / `_p2c_*` helpers are unreviewed and
  outside the qualification.
- No production hardening (concurrency, soak, retry/large-object behavior).

## 6. Focused qualification vs production readiness

| Dimension | Focused Stage A (this report) | Production readiness (NOT claimed) |
|-----------|-------------------------------|------------------------------------|
| Tests | 60/60 focused offline contracts pass | Full suite green + integration/soak/rollback |
| Correctness | Identical digest on fixture; focused forgery cases fail closed | Production-scale corpora, all schemas/modes, adversarial review |
| Performance | 28.6% on one fixture | EC2-representative latency with variance bounds |
| Memory | Traced peak flat on one fixture | RSS qualified on representative hosts/concurrency |
| Hygiene | `git diff --check` clean | Lint/type/security/governance gates pass |
| Tree | Dirty tree + untracked helpers | Clean reviewed release candidate |

**Verdict:** Phase 2C provenance reuse is **qualified for Stage A focused
use** on this exact checkout. **Not** approved for production deployment,
production data, or Phase 2D.

## 7. Verification for this document

- `HEAD = 4d2ba949…`, `branch = stage-a-completion`, workspace above.
- Inspected actual diff stats/status and read the implementation, focused
  Phase 2C test, and prior phase docs.
- Verified `git diff --check` passes and all three focused test files exist.
- Recorded supplied results verbatim; no implementation change, benchmark
  rerun, reset, commit, push, deploy, or Phase 2D.

*Documentation only. No implementation modified. No benchmarks rerun.*
