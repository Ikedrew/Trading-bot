# Stage 4 Root Change 1 — Shadow Runtime Authority

Stamp: 20260929  |  Root change: `ROOT-01-DATASET-AUTHORITY`  |  Mode: CONTRACT_GOVERNANCE_ONLY

Upstream audit fingerprint: `aeb8dc9b6d9a9c92b61cc5847ab6b814f9f4d90a2d051e24519270d7b362bd04`

## Previous authority

- dataset: `shadow_trades` (shadow_trades_v1)
- persisted objects: 0
- persisted rows: 0
- state: DECLARED_NEVER_EMITTED
- authoritative for current observation contracts: False
- history preserved: True

## Corrected authority

- dataset: `shadow_runtime`
- dataset version: `shadow_runtime_v1`
- persisted objects: 200
- persisted rows: 66258
- producer: `core/shadow/persistence.py + core/shadow/runtime.py`
- status: PERSISTED_CURRENT

## Conservation

| metric | value |
|---|---|
| affected_data_schema_gap_count | 27 |
| affected_governed_gaps | 28 |
| affected_implementation_observation_gap_count | 1 |
| affected_observation_requirements | 8 |
| audit_headline_gap_count | 27 |
| audited_observation_requirements | 15 |
| gaps_without_owner | 0 |
| observation_requirements_lost | 0 |
| total_governed_gaps | 31 |
| unaccounted_gaps | 0 |

## Observation requirement transitions

| req | was | now | classification | failing gates | blocked by |
|---|---|---|---|---|---|
| OR-01 | shadow_trades | shadow_runtime | FULLY_SATISFIED_BY_SHADOW_RUNTIME | - | - |
| OR-02 | shadow_trades | shadow_runtime | SHADOW_RUNTIME_CORRECT_DATASET_BUT_SEMANTIC_GAP_REMAINS | semantics_match,grain_match,lineage_requirement_matches,coverage_completeness_met | ROOT-02-LIFECYCLE-DECISION-SNAPSHOT |
| OR-03 | shadow_trades | shadow_runtime | SHADOW_RUNTIME_CORRECT_DATASET_BUT_COVERAGE_GAP_REMAINS | coverage_completeness_met | ROOT-02-LIFECYCLE-DECISION-SNAPSHOT |
| OR-04 | shadow_trades | shadow_runtime | SHADOW_RUNTIME_CORRECT_DATASET_BUT_COVERAGE_GAP_REMAINS | coverage_completeness_met | ROOT-02-LIFECYCLE-DECISION-SNAPSHOT |
| OR-05 | shadow_trades | shadow_runtime | SHADOW_RUNTIME_CORRECT_DATASET_BUT_COVERAGE_GAP_REMAINS | coverage_completeness_met | ROOT-02-LIFECYCLE-DECISION-SNAPSHOT |
| OR-06 | shadow_trades | shadow_runtime | FULLY_SATISFIED_BY_SHADOW_RUNTIME | - | - |
| OR-07 | shadow_trades | shadow_runtime | SHADOW_RUNTIME_CORRECT_DATASET_BUT_SEMANTIC_GAP_REMAINS | timestamp_semantics_match,coverage_completeness_met | ROOT-03-MARKET-TIMESTAMP-SEMANTICS |
| OR-15 | shadow_trades | shadow_runtime | SHADOW_RUNTIME_CORRECT_DATASET_BUT_FIELD_GAP_REMAINS | field_exists,semantics_match,timestamp_semantics_match,lineage_requirement_matches,coverage_completeness_met | ROOT-05-L7-EXPERIMENT-ARM |

## Affected governed gaps

| gap_id | requirements | disposition | blocked by |
|---|---|---|---|
| L7 (OG-L7-4db003c3ab76) | OR-15 | REMAINS_UNRESOLVED | ROOT-05-L7-EXPERIMENT-ARM |
| STAGE4-DATA-D2 | OR-01 | SATISFIED_CURRENTLY | - |
| STAGE4-DATA-D4 | OR-01 | SATISFIED_CURRENTLY | - |
| STAGE4-DATA-D5 | OR-01 | SATISFIED_CURRENTLY | - |
| STAGE4-DATA-E5 | OR-01 | SATISFIED_CURRENTLY | - |
| STAGE4-DATA-EX10 | OR-06,OR-07 | REMAINS_UNRESOLVED | ROOT-03-MARKET-TIMESTAMP-SEMANTICS |
| STAGE4-DATA-HORIZON-1 | OR-01,OR-06 | SATISFIED_CURRENTLY | - |
| STAGE4-DATA-L1 | OR-01,OR-02,OR-05,OR-06,OR-07 | REMAINS_UNRESOLVED | ROOT-02-LIFECYCLE-DECISION-SNAPSHOT,ROOT-03-MARKET-TIMESTAMP-SEMANTICS |
| STAGE4-DATA-L2 | OR-06,OR-07 | REMAINS_UNRESOLVED | ROOT-03-MARKET-TIMESTAMP-SEMANTICS |
| STAGE4-DATA-L4 | OR-01,OR-02,OR-06,OR-07 | REMAINS_UNRESOLVED | ROOT-02-LIFECYCLE-DECISION-SNAPSHOT,ROOT-03-MARKET-TIMESTAMP-SEMANTICS |
| STAGE4-DATA-L5 | OR-01,OR-05,OR-06,OR-07 | REMAINS_UNRESOLVED | ROOT-02-LIFECYCLE-DECISION-SNAPSHOT,ROOT-03-MARKET-TIMESTAMP-SEMANTICS |
| STAGE4-DATA-M10 | OR-01,OR-02,OR-04 | REMAINS_UNRESOLVED | ROOT-02-LIFECYCLE-DECISION-SNAPSHOT |
| STAGE4-DATA-M11 | OR-01,OR-03,OR-04 | REMAINS_UNRESOLVED | ROOT-02-LIFECYCLE-DECISION-SNAPSHOT |
| STAGE4-DATA-M3 | OR-01,OR-02,OR-03 | REMAINS_UNRESOLVED | ROOT-02-LIFECYCLE-DECISION-SNAPSHOT |
| STAGE4-DATA-M4 | OR-01,OR-02,OR-05 | REMAINS_UNRESOLVED | ROOT-02-LIFECYCLE-DECISION-SNAPSHOT |
| STAGE4-DATA-M5 | OR-01,OR-02 | REMAINS_UNRESOLVED | ROOT-02-LIFECYCLE-DECISION-SNAPSHOT |
| STAGE4-DATA-M6 | OR-01,OR-02 | REMAINS_UNRESOLVED | ROOT-02-LIFECYCLE-DECISION-SNAPSHOT |
| STAGE4-DATA-M7 | OR-01,OR-02,OR-03 | REMAINS_UNRESOLVED | ROOT-02-LIFECYCLE-DECISION-SNAPSHOT |
| STAGE4-DATA-M8 | OR-01,OR-02 | REMAINS_UNRESOLVED | ROOT-02-LIFECYCLE-DECISION-SNAPSHOT |
| STAGE4-DATA-M9 | OR-01,OR-02,OR-04 | REMAINS_UNRESOLVED | ROOT-02-LIFECYCLE-DECISION-SNAPSHOT |
| STAGE4-DATA-P1 | OR-01,OR-04 | REMAINS_UNRESOLVED | ROOT-02-LIFECYCLE-DECISION-SNAPSHOT |
| STAGE4-DATA-PORT-1 | OR-01 | SATISFIED_CURRENTLY | - |
| STAGE4-DATA-R3 | OR-06 | SATISFIED_CURRENTLY | - |
| STAGE4-DATA-R4 | OR-06,OR-07 | REMAINS_UNRESOLVED | ROOT-03-MARKET-TIMESTAMP-SEMANTICS |
| STAGE4-DATA-R5 | OR-06,OR-07 | REMAINS_UNRESOLVED | ROOT-03-MARKET-TIMESTAMP-SEMANTICS |
| STAGE4-DATA-S4 | OR-01,OR-04,OR-06 | REMAINS_UNRESOLVED | ROOT-02-LIFECYCLE-DECISION-SNAPSHOT |
| STAGE4-DATA-STRAT-1 | OR-01 | SATISFIED_CURRENTLY | - |
| STAGE4-DATA-X5 | OR-01 | SATISFIED_CURRENTLY | - |

## Remaining root changes

| root change | gaps | gap ids |
|---|---|---|
| ROOT-01-DATASET-AUTHORITY | 0 | - |
| ROOT-02-LIFECYCLE-DECISION-SNAPSHOT | 14 | STAGE4-DATA-L1, STAGE4-DATA-L4, STAGE4-DATA-L5, STAGE4-DATA-M10, STAGE4-DATA-M11, STAGE4-DATA-M3, STAGE4-DATA-M4, STAGE4-DATA-M5, STAGE4-DATA-M6, STAGE4-DATA-M7, STAGE4-DATA-M8, STAGE4-DATA-M9, STAGE4-DATA-P1, STAGE4-DATA-S4 |
| ROOT-03-MARKET-TIMESTAMP-SEMANTICS | 7 | STAGE4-DATA-EX10, STAGE4-DATA-L1, STAGE4-DATA-L2, STAGE4-DATA-L4, STAGE4-DATA-L5, STAGE4-DATA-R4, STAGE4-DATA-R5 |
| ROOT-04-EX2-LIFECYCLE-M5-PATH | 0 | - |
| ROOT-05-L7-EXPERIMENT-ARM | 1 | L7 (OG-L7-4db003c3ab76) |

## Mutation ledger

| ledger | value |
|---|---|
| backfills_performed | 0 |
| new_observables_added | 0 |
| new_producers_deployed | 0 |
| producer_mutations | 0 |
| q71_started | False |
| q71_started_count | 0 |
| research_reentry_events | 0 |
| s3_writes | 0 |
| schema_mutations | 0 |
| scientific_finding_version_changes | 0 |
| scientific_result_version_changes | 0 |

This is a CONTRACT/GOVERNANCE-ONLY correction. No producer, schema, S3, backfill, research re-entry or Q71+ action occurred.
