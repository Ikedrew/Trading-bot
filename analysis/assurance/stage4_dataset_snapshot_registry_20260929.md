# Stage 4 dataset snapshot registry (immutable population identity)

Stamp: 20260929
Policy: `stage4_dataset_snapshot_policy_v1`
Snapshots: 5

Dataset **population** identity (`DSNAP-...`) is separate from schema
identity (`shadow_runtime_v1`) and from producer identity. A schema
identity never identifies a population, and a growing dataset family is
never claimed as an immutable evidence snapshot.

| dataset_snapshot_id | dataset_name | schema_version | schema_generation | record_count | producer_version | population_class |
|---|---|---|---|---|---|---|
| DSNAP-5815AEDBC8947B483BC609F7 | shadow_runtime | shadow_runtime_v1 | 1 | 20421 | shadow_runtime_producer_v1 | GOVERNED_REQUIREMENT_POPULATION |
| DSNAP-A3C4ED868DDBABFCCF6AF586 | decision_trace | decision_trace_v1 | 1 | 25516 | decision_trace_producer_v1 | AUDIT_BOUNDARY_PERSISTED_POPULATION |
| DSNAP-B40B23A919D48CD2B12555A0 | opportunities | opportunities_v1 | 1 | 100850 | opportunities_producer_v1 | AUDIT_BOUNDARY_PERSISTED_POPULATION |
| DSNAP-BD89F20DB3073C46B861A97C | market_context | market_context_v1 | None | 1754 | UNASSERTED | AUDIT_BOUNDARY_PERSISTED_POPULATION |
| DSNAP-E2FCEE7D922C0AE59114C917 | shadow_runtime | shadow_runtime_v1 | 1 | 66258 | shadow_runtime_producer_v1 | AUDIT_BOUNDARY_PERSISTED_POPULATION |

## Evidence epoch -> population bindings

* `STAGE4-EPOCH-DECISION-TRACE-G1` -> `DSNAP-A3C4ED868DDBABFCCF6AF586` [BOUND_TO_DATASET_SNAPSHOT]
* `STAGE4-EPOCH-DECISION-TRACE-G2` -> (unresolved - population not frozen) [UNRESOLVED_POPULATION_NOT_FROZEN]
* `STAGE4-EPOCH-OPPORTUNITIES-G1` -> `DSNAP-B40B23A919D48CD2B12555A0` [BOUND_TO_DATASET_SNAPSHOT]
* `STAGE4-EPOCH-SHADOW-RUNTIME-G1` -> `DSNAP-E2FCEE7D922C0AE59114C917` [BOUND_TO_DATASET_SNAPSHOT]
* `STAGE4-EPOCH-SHADOW-RUNTIME-G2` -> (unresolved - population not frozen) [UNRESOLVED_POPULATION_NOT_FROZEN]

## Governed evidence sets

* `ESET-OR-01-POPULATION` -> `DSNAP-5815AEDBC8947B483BC609F7` (requirements: OR-01)
* `ESET-SHADOW-RUNTIME-AUDIT-BOUNDARY` -> `DSNAP-E2FCEE7D922C0AE59114C917` (requirements: OR-01, OR-02, OR-03, OR-04, OR-05, OR-06, OR-07, OR-14, OR-15)
* `ESET-STAGE4-AUDIT-BOUNDARY-POPULATIONS` -> `DSNAP-A3C4ED868DDBABFCCF6AF586`, `DSNAP-B40B23A919D48CD2B12555A0`, `DSNAP-BD89F20DB3073C46B861A97C`, `DSNAP-E2FCEE7D922C0AE59114C917` (requirements: OR-01, OR-02, OR-03, OR-04, OR-05, OR-06, OR-07, OR-08, OR-09, OR-13, OR-14, OR-15)

## Known limitations

* market_context carries no producer version in the Stage 4 producer overlay, so its producer lineage is recorded as UNASSERTED rather than invented.
* Epochs whose population is still collecting (COLLECTING/OPEN) have no dataset snapshot identity: a growing population is not an immutable evidence snapshot.
* Descriptor digests mark GOVERNED_POPULATION_DESCRIPTOR scope for populations whose exact rows cannot be re-read; they are never presented as record-byte digests.
