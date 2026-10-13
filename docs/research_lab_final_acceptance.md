# Research Lab final acceptance — manual verification pending

Status: IMPLEMENTED FOR MANUAL VERIFICATION; NOT ACCEPTED.
No Python imports, tests, server or browser session were executed by the agent.
Existing Phase 2A–2D implementation and benchmark evidence are preserved.

## Delivered files

- research_lab_archive.py: streaming verified importer, original artifact copies.
- tools/research_lab_browser.py: import, full verification and loopback server.
- research_engine/v10/continuous/lab_web/: question/science/blocker/lineage UI.
- tests/test_research_lab_archive.py: essential integrity tests, not executed.

This is a derived archive view using existing authoritative models, not a new
production projection or scientific schema. Existing publication logic is unchanged.

## Source and verification

Read-only source: C:\Users\Administrator\Desktop\stage_a_research_transfer.

The question projection is approximately 871 MB. Import streams individual
questions, verifies CSV-listed SHA-256/sizes, preserves copied originals and CSV,
validates snapshot/result identities with existing models, checks all 70 result
identities, scientific dependencies, current hypothesis/finding relationships
and the separate bridge receipt. Actual blocked/incomplete states are preserved.

Transferred content includes two findings, two proposed hypotheses, no candidates
and no plans. No candidates are invented. Raw evidence bodies and a validation
queue are absent; the Lab explicitly reports these verification gaps.
Operator-provided checksums protect integrity, not cryptographic authenticity.
Object references are checked against snapshot membership when an identifier is
present; aggregate descriptors are retained without claiming body verification.

## Retention, safety and recovery

Only a separate Stage A archive is writable. Production source paths are rejected.
Paths reject traversal, drive paths, ADS, reserved names and reparse points.
Known synthetic fixture buckets are rejected; source authenticity still requires
an operator-trusted transfer. Historical originals are never edited.

Import validates staged files, publishes a content-addressed directory, then
atomically replaces the display index. Older runs remain. Same-cycle conflicting
transfers fail closed. A newer run must identify the latest imported cycle and
snapshot as its predecessor; import intermediate completed packages in order.
Ambiguous equal completion times require review. Failure leaves the old display
index intact.

Interrupted imports may leave .pending-* directories, an unindexed completed
directory or import.lock. These are never automatically displayed. Confirm no
importer remains and review diagnostics before recovery; do not bypass collisions.
An interruption between directory publication and index replacement intentionally
requires operator review. Runtime recovery acceptance is still pending.

The server binds 127.0.0.1, handles one request at a time, checks Host/Origin,
sets CSP, exposes no mutation endpoint and verifies requested fields/downloads.
Full verification also hashes original artifacts. Nested items may remain large
despite pagination; use verified downloads if response limits are exceeded.
This is intended for a trusted local workstation user.

## Acceptance evidence

| Criterion | Prepared behavior | Pending manual evidence |
|---|---|---|
| Browser accessible | Loopback server and UI | Launch and browser inspection |
| 70 identities | Frozen baseline and cycle/result checks | Import/verify reports 70 |
| Actual science | Persisted findings/hypotheses and full fields | Inspect two of each |
| Evidence/blockers | Evidence, gaps, bridge reviews | Open supported/incomplete questions |
| Freshness | Historical banner, completion/import dates | Browser confirmation |
| Lineage/integrity | Snapshot/cycle/results/separate receipt | Verify; reject altered copied artifact |
| Read-only refresh | CLI import, newest imported view | Reimport and retention/lineage checks |
| No synthetic science | Historical source; fixture rejection | Confirm source and archive identities |
| No production mutation | Separate Stage A archive | Review output paths/source hashes |
| No critical failure | Atomic promotion and fail-closed reads | Focused tests and actual package acceptance |

No row is marked passed before operator results.

## Manual commands

Run from Stage A using its interpreter:

    Set-Location 'C:\Users\Administrator\Desktop\Trading-bot-stage-a'
    .\.venv\Scripts\python.exe -m pip install 'ijson>=3,<4'
    .\.venv\Scripts\python.exe -m pytest tests\test_research_lab_archive.py -q
    .\.venv\Scripts\python.exe tools\research_lab_browser.py import --package 'C:\Users\Administrator\Desktop\stage_a_research_transfer'
    .\.venv\Scripts\python.exe tools\research_lab_browser.py verify
    .\.venv\Scripts\python.exe tools\research_lab_browser.py serve --port 8765

Open http://127.0.0.1:8765/ locally. Stop with Ctrl+C.
Allow several GB spare disk space for originals and derived fields.
Dependency installation uses a package repository; importer/server do not contact
AWS, a coordinator or other network research data sources.

Default output: reports/research_lab_archive/ under Stage A. CLI prints archive
identity, latest identity, path and question count. Expected validation/file
failures exit nonzero with RESEARCH_LAB_FAIL_CLOSED; preserve stderr and staging
diagnostics. The large import and verify each read substantial local data.

For newer transferred completed research, repeat import with its new package
path, verify, then click Check imported updates. No newer source is connected
automatically. Until supplied, the displayed run remains honestly historical.

Prior evidence: operator reported 18/18 Phase 2D and a passing benchmark regression.
No new test results claimed; successful combined suites were not repeated.

## Original Lab restoration — 2026-10-11

This section supersedes the parallel viewer launch instructions above.
Final interface: research_engine/v10/lab/server.py and its existing static/index.html.
No new website or persisted scientific/projection store was created for this repair.
The previous browser CLI now delegates serve to this original application.

Root cause: transferred optimisation_registry/registry.json has candidates={};
the transferred scientific current.candidates is also empty. Import correctly
retained that emptiness. The earlier viewer only displayed that transferred
cohort and omitted the separate locally persisted candidate registry.

Located records:
- data/research/candidates/candidates.jsonl: OPT-fcf078b8, OPT-441a8783,
  OPT-3292f718, all PROPOSED.
- data/research/optimisation/registry.json: OPT-DP1-001 PROPOSED,
  OPT-DP1-002 SHADOW_VALIDATION_ACTIVE, with two persisted hypotheses.
- Latest locally imported completed cycle:
  QCYCLE-5B5C406636540AE99AB46705B538F405,
  completed 2026-10-10T04:26:34.553975+00:00, separate bridge
  SBRIDGE-F2ED9B896902E3D9DAF259E593DF1177: 70 question outcomes,
  two findings and two hypotheses, zero candidates within this cohort.

Combined display inventories are therefore 70 questions, 2 actual findings,
4 actual hypotheses and 5 actual candidates (4 PROPOSED, 1 shadow-active).
These are separate source cohorts, not a fabricated unified completed run.
Every local record is labeled with file path, SHA-256, separate-registry scope,
and unverified live freshness. No status is upgraded.

Search of local analysis/data/log JSON artifacts found the older hypothesis and
finding IDs only as references in the two registries. Missing full records:
H-fcf078b8, H-441a8783, H-3292f718; strong_MEAN_REVERSION_416,
score_mono_770_2, strong_MEAN_REVERSION_421; F-DP1-002, F-DP1-006.
They are shown as lineage gaps; no finding/hypothesis placeholders are made.

The original Lab's unified_research_projection_v1 production contract remains
unchanged. With a local native projection present, the original projection path
is used. With no such projection, explicit read-only historical display adapts
the existing verified imported artifacts to the original Lab API without
fabricating or persisting a unified projection. Missing/corrupt archive data
fails closed. Original statuses and identities remain intact.

Read-only sessions cannot launch research, acquire a run lock or compute current
evaluator identities. Run controls are hidden and POST run is rejected.
Question/finding evidence collections show counts; complete hash-verified fields
are available on explicit downloads. Auto-refresh defaults off.

Files changed for restoration:
- research_engine/v10/lab/server.py
- research_engine/v10/lab/static/index.html
- tools/research_lab_browser.py (serve delegates to original)
- tests/test_research_lab_restore.py
- docs/research_lab_final_acceptance.md

Manual targeted regression and original application launch:

    Set-Location 'C:\Users\Administrator\Desktop\Trading-bot-stage-a'
    .\.venv\Scripts\python.exe -m pytest tests\test_research_lab_restore.py -q
    .\.venv\Scripts\python.exe -m research_engine.v10.lab.server --read-only --port 8766 --open

Use port 8766 to avoid the earlier archive viewer on 8765. Do not terminate any
production process. Browse http://127.0.0.1:8766/; confirm questions, findings,
hypotheses, all five candidate IDs, evidence counts, blockers and provenance.
Ctrl+C stops only the manually started server.

Current production freshness remains unavailable: Stage A has no completed
data/research/continuous/projection/latest.json or matching completed continuous
cycle/scientific/bridge artifacts newer than the imported 2026-10-10 cycle.
Supply a separately verified completed production transfer, including matching
lineage and any missing candidate-related historical records. No live fetch is
performed. Raw evidence bodies remain unavailable. Existing candidate notes or
shadow-active status do not establish live authorization or profitability.

Runtime acceptance is pending these manual results. No Python/tests/server were
executed by the agent for this repair.
