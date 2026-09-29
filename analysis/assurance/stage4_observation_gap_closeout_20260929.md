# Stage 4 Observation Gap Closeout

Stamp: 20260929  |  policy: stage4_data_versioning_policy_v1

## Data versioning

| item | value |
|---|---|
| dataset versions changed | 0 |
| schema generations added | 1 (shadow_runtime_v1 gen 1 -> 2) |
| producer versions changed | 2 (shadow_runtime, decision_trace) |
| evidence epochs created | 3 |
| compatibility class | ADDITIVE_SCHEMA_EVOLUTION |
| collection start | 2026-09-29 |

Dataset meaning, grain and canonical identity are unchanged, so the
Stage 4 observability work is an ADDITIVE_SCHEMA_EVOLUTION: a schema
generation bump, not a dataset version bump.

## Shadow runtime

| item | value |
|---|---|
| dataset version | shadow_runtime_v1 |
| previous schema generation | 1 |
| current schema generation | 2 |
| producer version | shadow_runtime_producer_v1 -> shadow_runtime_producer_v2 |
| fields added | decision_snapshot, market_time_attestation, experiment_arm, lifecycle_m5_path |

## EX2

- contract complete: True
- dataset/schema: shadow_runtime_v1 generation 2, path lifecycle_m5_path_v2
- future-only: True
- threshold preserved verbatim: True
- synthetic historical paths: 0

## L7

- contract complete: True
- experimental design valid: True
- dataset/schema: shadow_runtime_v1 generation 2, arm experiment_arm_schema_v2
- future-only: True
- threshold preserved verbatim: True
- schema_version overload removed: True

Design review: deterministic identity hashing is a valid random
assignment mechanism (unbiased, reproducible, pre-outcome, outcome
blind). It does NOT provide stratification or guaranteed finite-sample
balance, which is recorded explicitly as an accepted, named deficiency.

## p_success

- producer implemented: True
- historical backfill possible: False
- future collection: True
- D3 state: COLLECTING
- X5 state: COLLECTING

Root cause: PRODUCER OMISSION, not an upstream data gap: the V10 pipeline never invoked the probability estimator, so the V10 branch of decision_trace read p_success from a dict that never carried it. 25360/25516 rows held the key, 0 held a usable value.

## OPP-1

| item | value |
|---|---|
| coverage before | 0.500952 (50521/100850) |
| coverage after | 0.500952 |
| records repaired | 0 |
| records still missing | 50329 |
| remaining unexplained | 0 |

Cause: PRODUCER_DIVERSION_DISJOINT_VOCABULARY. Two producers write one
dataset version with disjoint closed state vocabularies, so a backfill
would be a reinterpretation (inference) and is refused. The residual
gap is preserved truthfully.

## 31 gap reconciliation

| state | gaps |
|---|---|
| SATISFIED_OBSERVATION | 0 |
| COLLECTING | 0 |
| WAITING_THRESHOLD | 27 |
| BACKFILL_PENDING | 0 |
| FUTURE_ONLY | 4 |
| UPSTREAM_BLOCKED | 0 |
| CONTRACT_BLOCKED | 0 |

| conservation | value |
|---|---|
| total governed gaps | 31 |
| accounted gaps | 31 |
| unaccounted | 0 |
| ownerless | 0 |
| unexplained | 0 |
| observation requirements | 15/15 |

## Re-entry

- REENTRY_ELIGIBLE: 0
- REENTRY_EXECUTED: 0

## Mutation ledger

- backfills_performed: 0
- historical_mutations: 0
- q71_started: False
- q71_started_count: 0
- research_reentry_events: 0
- s3_writes: 0
