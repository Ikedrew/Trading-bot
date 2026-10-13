# Targeted closeout repair — verification pending

Date: 2026-10-11 UTC. **UNQUALIFIED REPAIR — awaiting user-run regression results.** The original benchmark/report below is preserved as historical evidence. Its timings describe the pre-repair implementation and must not be attributed to this repair. Phase 2D remains open.

## Targeted changes

`ResearchProjectionStore.save` now reads and compares the complete canonical content of immutable history on every save, including same-instance duplicate saves. Unreadable history raises `HISTORY_PROJECTION_UNREADABLE`; parseable altered or incomplete history raises the existing `PROJECTION_IDENTITY_COLLISION`. It never overwrites conflicting immutable history. Missing history is recreated from the supplied projection through the existing atomic writer. Latest content is independently read and compared with the full submitted payload: missing, malformed, incomplete or changed latest content is atomically replaced only after history matches or has been created. Identical artifacts skip writes after content verification, including after restart. The incoming schema/version identity check, scientific result construction, version calculation and `_atomic` implementation are unchanged. Recall alone no longer authorizes skipping persistence.

`tests/test_phase2d_publication_reuse.py` adds eight parameterized artifact-integrity cases (history/latest × missing/malformed/incomplete/changed), one failed latest-repair/retry case, one non-empty validation-blocker case, and one candidate shadow-transition case. The governance fixtures use real registry and queue models, the existing queue processor's unavailable-executor blocker, and `bind_shadow_candidate`. They assert new projection versions, persisted/restarted publication, Lab invalidation, unchanged canonical scientific results/findings/hypotheses/frontier, dependency-linked candidate references, separate bridge receipt identity, and continued lack of live authority. These are publication tests; they do not qualify executor science or a real bridge weakening cycle. The shadow fixture begins with a registered FORWARD_VALIDATED candidate; it does not claim to run forward validation.

No Research Lab implementation change was made. Existing evidence isolates read/build counts but not individual copy costs. Deep-copy overhead is a plausible explanation for the regression, not an independently measured attribution. A minimal replacement cannot be qualified under the instruction to execute no benchmarks. The Lab regression is documented and deferred; existing Lab implementation and benchmark evidence remain intact.

## Verification requirements and remaining risks

No Python tests, compilation commands or benchmarks were executed during this repair. Earlier 7/7 and combined 67/67 results apply to the pre-repair source only. Expected collection after these additions is 18 Phase 2D cases and 78 combined cases, pending actual collection and execution. Do not treat these expected counts as passed results.

The user must run the commands below and return the full summaries, failures and exit codes. Qualification is pending review of those results. No successful benchmarks were rerun. Integrity checks add two artifact reads and canonical comparisons on duplicate saves, so the earlier 83.70%/93.75% improvements are historical; post-repair performance is NOT MEASURED. No end-to-end acceleration is claimed.

Unresolved: Lab same-metadata/same-version stale-view risk and read/stat race; general valid-schema incomplete-artifact read validation; concurrent writers and changes between verification and return; two-file publication is not a transaction; production-scale memory and process RSS; real bridge weakening/invalidation and executor-governance transitions. The targeted content checks close the demonstrated same-instance corrupted-artifact acceptance path under sequential use. They do not establish a concurrency protocol. Atomic flush/fsync/replace behavior remains unchanged. Missing immutable history is rebuilt only from the explicitly supplied payload, consistent with the existing save contract; existing conflicting history fails closed.

PowerShell commands (run from Stage A; stop and report failures if the targeted command fails):

```powershell
Set-Location -LiteralPath 'C:\Users\Administrator\Desktop\Trading-bot-stage-a'
.\.venv\Scripts\python.exe -m pytest tests\test_phase2d_publication_reuse.py -q -o addopts=
Write-Host "Targeted pytest exit code: $LASTEXITCODE"
```

After the targeted suite passes:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_cycle_snapshot_inputs.py tests\test_incremental_acquisition_replay.py tests\test_phase2c_provenance_reuse.py tests\test_phase2d_publication_reuse.py -q -o addopts=
Write-Host "Combined pytest exit code: $LASTEXITCODE"
git diff --check
Write-Host "Diff check exit code: $LASTEXITCODE"
```

Diff check during repair: **passed**, exit 0. Python verification remains pending user execution. No production processes, S3, MT5, historical artifacts, commits, pushes, merges, deployment or integration were accessed or started.

---

# Preserved pre-repair qualification evidence

# Phase 2D qualification report

Date: 2026-10-11 (UTC). Repository: `C:\Users\Administrator\Desktop\Trading-bot-stage-a`; branch: `stage-a-completion`.

**Verdict: PARTIALLY QUALIFIED. Phase 2D cannot be closed as fully qualified.** Unchanged publication is faster, but bounded Lab latency regressed, cache integrity limitations remain, and non-empty governance transitions have not been independently exercised in this qualification.

## Implementation

- `research_engine/v10/continuous/research_projection.py`: `ResearchProjectionStore.__init__`, `save`, and new `_remember` retain the last canonical payload/version on one store instance. An identical payload/version with both files present returns without history read, file serialization, fsync or replacement. Changed payloads follow collision checking and atomic publication. A restarted instance reads existing history and republishes latest. Initial save now adds canonical serialization. There is no projection-construction optimization.
- `research_engine/v10/continuous/research_lab.py`: `_LAB_VIEW_CACHE`, `_lab_cache_key`, `clear_lab_view_cache`, and `load_lab_view` cache one derived view. Key: path, mtime_ns and size. A hit still reads/parses the artifact and checks schema/version, then deep-copies the view. Metadata or version changes rebuild it. New views are also copied into the cache. There is no reduction in authoritative file reads on hits.
- `tests/test_phase2d_publication_reuse.py`: existing seven offline tests, including corrected canonical result/status, full registry/results/links, and separate bridge receipt assertions. No production schema change.
- This closeout adds only `tools/_phase2d_qualification.py`, `docs/phase2d_benchmark_results.json`, and this report. No tracked implementation was changed during qualification. Earlier Phase 2A/B/C changes were preserved.

## Methodology and baseline

Baseline publication/Lab modules: git HEAD `4d2ba9491b4ba391e193003bb9a89b89acb41d02`, compiled in memory; working files never replaced. Other dependencies and fixture inputs are shared with the working tree, isolating the Phase 2D module changes.

Interpreter: `3.14.7 (tags/v3.14.7:823f032, Aug  5 2026, 10:51:32) [MSC v.1944 64 bit (AMD64)]`; Stage A `.venv/Scripts/python.exe`. Existing Phase 2D fixture reused: 70 canonical questions, one COMPLETE and 69 WAITING_FOR_DATA, small findings/hypotheses/dependencies. Canonical payload: 70,203 bytes. All writes confined to temporary directories; no production services, datasets or artifacts accessed.

Three timing trials, median milliseconds per operation. Repeated paths: ten operations per trial (30 total); initial construction/persistence: three per trial (nine total); changed saves and Lab refreshes: three individual samples. Same process/interpreter, baseline then optimized, identical logical payloads; setup excluded. Timing includes lightweight call-count wrappers on applicable rows. OS caches are warm/uncontrolled; no statistical confidence or cold-disk claim. No cProfile or tracing during latency measurement. Raw samples and counters are retained in `phase2d_benchmark_results.json`.

Same-snapshot scientific change is a synthetic in-memory finding status amendment through `ScientificStateStore.replace_document`, with canonical results unchanged. It measures publication invalidation only, not a real bridge weakening/governance transition. Changed saves use fresh stores preseeded with the original artifact; reported latency excludes seeding and projection build. Changed build-plus-save totals: **NOT MEASURED**. Disk serialization versus fsync/replace costs independently: **NOT MEASURED**; initial persistence includes them together.

## Timings

Positive improvement means lower latency; negative means slower. Small differences on unchanged code are measurement variability, not demonstrated effects.

| Operation | Baseline ms | Optimized ms | Improvement |
|---|---:|---:|---:|
| Initial projection construction | 1.5785 | 1.6593 | -5.12% |
| Unchanged construction + publication | 16.0154 | 2.6107 | +83.70% |
| Unchanged save only | 13.9743 | 0.8728 | +93.75% |
| Changed snapshot save only | 22.2042 | 22.1631 | +0.19% |
| Same-snapshot scientific change save only | 21.2785 | 22.1781 | -4.23% |
| Pretty JSON serialization in memory | 0.7585 | 0.7802 | -2.86% |
| Initial serialization + atomic persistence | 22.3979 | 23.6274 | -5.49% |
| Warm repeated Lab read | 1.1056 | 2.9151 | -163.68% |
| Lab refresh after publication change | 11.3223 | 14.6309 | -29.22% |

The Lab cache avoids derivation but read/parse plus deep-copy costs exceeded the original derivation on this bounded fixture. No Lab acceleration is qualified. Initial Lab cold-read latency separately: **NOT MEASURED**. Standalone JSON serialization ran 30 times per implementation; its counter is explicit operation count, not the file `json.dump` counter.

## Repeated work counts

Counts below are measured totals across 30 operations per implementation. Canonical serialization counters apply to projection-module calls; file serialization means `json.dump`; reads mean `Path.read_text` (not OS syscalls).

| Path | Impl | Projection builds | Canonical serializations | File serializations / atomic writes | Reads | Lab builds |
|---|---|---:|---:|---:|---:|---:|
| unchanged_build_save | baseline | 30 | 90 | 30 / 30 | 30 | 0 |
| unchanged_build_save | optimized | 30 | 60 | 0 / 0 | 0 | 0 |
| unchanged_save_only | baseline | 0 | 60 | 30 / 30 | 30 | 0 |
| unchanged_save_only | optimized | 0 | 30 | 0 / 0 | 0 | 0 |
| lab_repeated_read | baseline | 0 | 0 | 0 / 0 | 30 | 30 |
| lab_repeated_read | optimized | 0 | 0 | 0 / 0 | 30 | 0 |

Initial persistence: nine saves each, 18 file serializations and 18 atomic writes each; baseline zero versus optimized nine canonical serializations. New-version changed saves require two atomic writes each in both implementations; source-derived canonical serialization counts are zero baseline, one optimized. No new-version I/O suppression is claimed.

## Memory

Separate tracemalloc phase: three build/save/Lab operations, setup and imports excluded, no timing inferred from tracing. This is peak Python traced allocation, not process peak RSS or total native memory.

| Metric | Baseline bytes | Optimized bytes |
|---|---:|---:|
| python_traced_current_bytes | 58747 | 319225 |
| python_traced_peak_bytes | 398717 | 700101 |

Peak traced allocation increased 75.59%; retained canonical content and copied Lab view contribute. Process peak RSS: **NOT MEASURED**. No production-scale memory qualification.

## Scientific and governance evidence

- Benchmark asserts complete canonical JSON equality between baseline and optimized projections for initial, changed-snapshot, and same-snapshot scientific-state cases. This covers all emitted fields, including result identities/statuses, snapshot identities, findings/hypotheses and link references. All three versions differ as required. Unchanged output equality and publication mtime stability are covered by the prior focused test.
- Prior corrected equivalence test explicitly checks 70 distinct registry identities, all result payloads/IDs/snapshots, changed flags, findings/hypothesis/candidate dependency links, and the complete separately persisted bridge receipt. It does not invent canonical lifecycle or top-level bridge identity fields.
- Benchmark Lab refresh asserts the new projection version after each change. Existing focused tests cover version-triggered Lab invalidation, restart history reuse, malformed JSON, wrong schema and identity-invalid save rejection.
- Atomic `_atomic` implementation is unchanged: temp file, flush, fsync and `os.replace`. Crash injection and concurrent writers: **NOT MEASURED**. Separate history/latest replacement is not a two-file transaction.
- Governed blocker/validation construction code is unchanged; complete fixture outputs compare equally. Non-empty blocker/validation transitions and real same-snapshot bridge transitions: **NOT VERIFIED by this benchmark**. Empty queues and reference equality cannot prove transition behavior.

## Integrity limitations and unresolved risks

- Confirmed bounded corruption probe: after a store saves a valid projection, corrupting latest.json and saving the same unchanged payload on that same instance returns without repairing or rejecting the corrupted artifact. The fast path trusts recalled content plus file existence. Lab reads still reject malformed JSON; this probe concerns save behavior. History replacement/corruption is likewise not revalidated on this path (source inspection, not separately probed).
- Lab cache validates schema/version but not full content on a metadata-key hit. Same-size/content edits retaining mtime and version can return stale derived state; a concurrent replacement between read and final stat can also associate a view with later metadata. These are source-inspection risks, not demonstrated race failures.
- Incomplete-artifact coverage is limited: the seven-test suite checks wrong schema and missing save identity, not every required scientific field under a valid schema/version. Stronger fail-closed claims are not supported.
- Cache deep copies impose measurable cost on this fixture; larger view performance is unknown. Ordering, filesystem variance and three trials limit generalization.

## Regression evidence and closeout

Reused verified results from the immediately preceding Stage A work: Phase 2D **7/7 passed**; combined Phase 2A/2B/2C/2D **67 passed in 90.15 seconds**, exit 0. No relevant implementation changed, so no redundant pytest run was launched. Qualification helper assertions passed, exit 0; these are benchmark checks, not additional pytest tests. An initial helper attempt stopped on immutable same-snapshot question-history collision before any timed workload; the corrected fixture uses a synthetic scientific-store amendment instead and preserves immutable history.

`git diff --check`: **passed**, exit 0, after qualification additions. New helper compiled and benchmark assertions passed.

**PARTIALLY QUALIFIED**: duplicate publication savings are demonstrated, scientific payload equivalence holds for bounded fixtures, but Research Lab improvement is not demonstrated and integrity/governance gaps remain. Do not close Phase 2D as fully qualified. Next work, if separately authorized: resolve or explicitly constrain cache integrity assumptions, exercise non-empty governed transitions and valid-schema incomplete artifacts, and address the measured Lab regression. No implementation rewrite, production integration, end-to-end qualification, commit, push, merge or deployment was performed. No end-to-end pipeline acceleration is claimed.
