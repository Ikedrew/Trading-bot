# Phase 2A: shared snapshot inputs and instrumentation

Scope: Stage A only. No production access, research runs, MT5 operations, new
S3 snapshots, transferred artifact modifications, commits, pushes or deployments.
No Phase 2B-2D work was implemented. Pytest was installed in the Stage A virtualenv.

## Implementation

- `research_engine/v10/investigation_snapshot.py`: `CycleSnapshotInputs` owns one
  eagerly verified `SnapshotBoundDatasetReader` for one cycle lease. Admission
  rederives manifest identity; reuse checks the exact manifest, source instance,
  current source authority and schema. The existing reader still verifies pinned
  versions, SHA-256, bytes, raw/canonical counts, membership and guard cohorts.
  Recursively frozen dict/list-compatible evidence blocks ordinary nested writes;
  explicit reader reads return independent mutable deep copies. Freezing releases
  each mutable row as it replaces it, avoiding two retained decoded populations.
  Closing the cycle discards the pool and rejects further admission. A new cycle
  must verify bodies again. No persistent or global evidence cache was added.
- `research_engine/v10/continuous/research_loop.py`: the existing exclusive lease
  owns and closes the pool in `finally`. The real frontier and canonical paths
  share a cycle source; candle and counterfactual helpers and the generated worker
  receive the same admitted reader. Existing injected custom question adapters
  retain their independent behavior. Frontier acquisition and metadata verification
  remain outside reuse. Existing incremental freezing and deep scheduling remain.
- `research_engine/v10/continuous/canonical_question_cycle.py`: optional shared
  admission; isolated context timing; separately labeled cumulative readiness time;
  normalization/provenance timings nested within existing resolution/runner times.
  Failed runners retain measured evaluation/provenance duration.
- `research_engine/v10/continuous/q71_worker.py`: optional shared admission across
  generated questions; input datasets still respect evaluator declarations and
  governed counterfactual binding checks. Diagnostic runner/provenance durations
  do not enter immutable scientific result or batch identity.
- `research_engine/data_access/s3_source.py`: aggregated LIST/HEAD/GET invocation
  counts, bytes, SHA-256 update time, JSON decoding time and inclusive body-load
  duration. Stream bodies are closed. SDK-internal wire retries are not counted.
- `research_engine/control_plane/evidence_provenance.py` and
  `research_engine/control_plane/evidence_resolver.py`: scoped diagnostic timers,
  carrying no evidence or results. Existing selection, hashing and normalization
  algorithms are unchanged. Measurements flow through existing progress details;
  existing stage duration and RSS reporting remain in use.
- `tests/test_cycle_snapshot_inputs.py`: 19 offline contract tests plus a bounded
  benchmark helper. Tests use fake S3 and temporary directories.

## Offline measurements

440 canonical rows in 10 objects; four sequential consumers; separate processes;
tracemalloc enabled. Freeze/setup is excluded. Measurements are individual runs,
not an EC2 qualification or an end-to-end pipeline benchmark.

| Fake GET delay | Independent total | Shared total | Independent GETs | Shared GETs |
|---|---:|---:|---:|---:|
| None | 0.147989s | 0.162916s | 40 | 10 |
| Explicit 10ms | 0.589792s | 0.285728s | 40 | 10 |

For the simulated-latency run, first-consumer time was 0.145695s independent versus
0.175558s shared. The following three consumers totaled 0.444041s versus 0.110150s.
The total reduction was about 52%, specific to this synthetic latency model.
Without simulated latency, total shared admission was about 10% slower.

Process peak RSS, including imports/setup, was 65,462,272 bytes independent versus
66,379,776 bytes shared for the delayed fixture. Tracemalloc peak was 961,950 versus
1,222,578 bytes. Immutable admission has measurable allocation/CPU overhead;
production-scale RSS has not been qualified. After first admission, subsequent
consumers issued zero snapshot-body GETs. The real producer-to-worker fixture also
proved each bound object was downloaded once, with no generated-worker reload.

## Verification

- Final new regression suite: **19 passed**.
- Offline snapshot/incremental/producer/provenance/S3/profile/fast-deep scheduling
  verification set: **114 passed**. The new real-E3 COMPLETE equivalence check
  also passed separately and is included in the final 19-test suite.
- Canonical question suite was included in an earlier broader run: **133 passed,
  3 failed** overall. Those three failures belong to legacy orchestrator tests
  without offline data injection; they stopped at missing boto3 before S3 access.
  Boto3 was deliberately not installed to enable those remote paths.
- Required frontier and Q71 suites, final patch: **27 passed, 37 failed**.
  All 37 identical failures were independently reproduced using unchanged HEAD
  module sources loaded in memory, without replacing working files. There are
  31 frontier fixture/profile failures and six generated scientific-bridge
  expectation failures. They were left unchanged rather than weakening contracts.
- `git diff --check` passed. No historical research artifacts were modified.

Coverage includes scientific result equality, real E3 COMPLETE output equality,
snapshot/digest/version/membership/schema/source rejection, duplicate guard-row
accounting, required object loss, optional absence, nested mutation isolation,
closed/wrong-cycle rejection, fresh body verification in a new cycle, scoped
normalization/provenance timings and real generated-worker reuse.

## Remaining independent reads and limits

Standalone canonical/generated invocations and producer helper fallbacks retain
independent readers when no cycle pool is supplied. Manual investigation views
(`investigation_views.py`) and separately leased deep jobs remain independent;
cross-job reuse would violate this phase's lifetime boundary. Fresh frontier
LIST/HEAD/content checks remain authoritative. The separately governed M5 candle
stream is not a bound investigation dataset and still requires its own reads.
Immutable manifests are still loaded and authenticated at consumer boundaries.
Custom question-runner adapters are not automatically rewritten or trusted.

Verdict: ready for further isolated Phase 2A qualification, not production
integration. Resolve the baseline test failures and qualify representative EC2
memory and end-to-end latency before integration. No production speedup is claimed.
