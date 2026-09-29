# Stage 4 Observation / Dataset Audit

Stamp: 20260929  |  Mode: AUDIT_DISCOVERY_ONLY  |  Implementation performed: NO

Baseline certification fingerprint: `b42b4bfcfa1eaf7df09cac9f88011c89362cf2ca907217e8466fff9fea3adc8b`

EX2/L7 adjudication fingerprint: `4db003c3ab767c72c55803bf01976f913a747c572c3efe62333e861f0b2084cc`

## Headline

The expected universe of **31 governed gaps is NOT 31 independent dataset changes**. The 31 governed gaps resolve to **15 unique observation requirements** touching **11 datasets**, of which only **2 are genuinely future-only** (EX2, L7).

### The single largest finding

`shadow_trades` / `shadow_trades_v1` is named as the current dataset by **27 of 31** governed gaps -- but `list_objects_v2` returns **KeyCount = 0** for `supporting/shadow_trades/`. The dataset is declared in `core/production_data_contract.py` and has a writer in `core/shadow_trades.py`, but it has **never emitted a single persisted object**. The real shadow lifecycle producer is `core/shadow/persistence.py` writing `shadow_runtime_v1`, which has persisted **66,258 rows**. Most registered 'missing' observables already exist there under a different dataset identity and/or path.

## Conservation

| metric | value |
|---|---|
| TOTAL_GOVERNED_GAPS | 31 |
| UNIQUE_OBSERVATION_REQUIREMENTS | 15 |
| SATISFIED_ALREADY | 4 |
| CONTRACT_GOVERNANCE_REQUIRED | 2 |
| PARTIAL_DATASET_CHANGE_REQUIRED | 1 |
| PRODUCER_CHANGE_REQUIRED | 4 |
| SCHEMA_CHANGE_REQUIRED | 2 |
| NEW_DATASET_REQUIRED | 2 |
| UNACCOUNTED_GAPS | 0 |
| GAPS_WITHOUT_DATASET_OR_PRODUCER_OWNER | 0 |
| UNEXPLAINED_FIELD_SCHEMA_MISMATCHES | 0 |
| conserved | True |

## Persisted S3 universe (read-only)

| dataset | schema | prefix | objects | rows | span |
|---|---|---|---|---|---|
| shadow_runtime | shadow_runtime_v1 | supporting/shadow_runtime/schema_version=shadow_runtime_v1/ | 200 | 66258 | 2026-09-07..2026-09-29 |
| events | events_v1 | core/events/schema_version=events_v1/ | 48737 | 15359 CANDLE rows over 249 partitions | 2026-09-03..2026-09-29 |
| shadow_trades | shadow_trades_v1 | supporting/shadow_trades/  <-- NO SUCH PREFIX | 0 | 0 | NOT PERSISTED |
| market_context | market_context_v1 | core/market_context/schema_version=market_context_v1/ | 230 | 1754 | 2026-09-03..2026-09-29 |
| decision_trace | decision_trace_v1 | supporting/decision_trace/schema_version=decision_trace_v1/ | 200 | 25516 | 2026-09-07..2026-09-29 |
| opportunities | opportunities_v1 | core/opportunities/schema_version=opportunities_v1/ | 200 | 100850 | 2026-09-07..2026-09-29 |
| horizon_candidates | horizon_candidates_v1 | supporting/horizon_candidates/schema_version=horizon_candidates_v1/ | 200 | 76710 | 2026-09-07..2026-09-29 |
| strategy_candidates | strategy_candidates_v1 | supporting/strategy_candidates/schema_version=strategy_candidates_v1/ | 125 | 3386 | 2026-09-07..2026-09-29 |
| portfolio_rankings | portfolio_ranking_v1 | supporting/portfolio_rankings/schema_version=portfolio_ranking_v1/ | 20 | 570 | 2026-09-07..2026-09-29 |
| strategy_observations | strategy_observation_v1 | supporting/strategy_observations/schema_version=strategy_observation_v1/ | 200 | 25575 | 2026-09-07..2026-09-29 |
| research_shadow_trades | research_shadow_trades_v1 | supporting/research_shadow_trades/  <-- NO SUCH PREFIX | 0 | 0 | NOT PERSISTED |

## Observation requirements

| id | observable | dataset | classification | disposition | gaps | questions |
|---|---|---|---|---|---|---|
| OR-01 | shadow_trades.simulated_outcome.pnl_r_multiple | shadow_runtime | FIELD_EXISTS_WRONG_IDENTITY | CONTRACT_GOVERNANCE_REQUIRED | 22 | 22 |
| OR-02 | shadow_trades.decision_snapshot.market_phase | shadow_runtime | FIELD_EXISTS_WRONG_GRAIN | SCHEMA_CHANGE_REQUIRED | 10 | 10 |
| OR-03 | shadow_trades.decision_snapshot.h4_regime | shadow_runtime | FIELD_EXISTS_PARTIAL_COVERAGE | PRODUCER_CHANGE_REQUIRED | 3 | 3 |
| OR-04 | shadow_trades.decision_snapshot.pattern | shadow_runtime | FIELD_EXISTS_PARTIAL_COVERAGE | PRODUCER_CHANGE_REQUIRED | 5 | 5 |
| OR-05 | shadow_trades.decision_snapshot.strategy | shadow_runtime | FIELD_EXISTS_PARTIAL_COVERAGE | PRODUCER_CHANGE_REQUIRED | 3 | 3 |
| OR-06 | shadow_trades.identity.canonical_opportunity_id | identity.shadow_type | identity.evaluated_horizon | identity.symbol | shadow_runtime | FIELD_EXISTS_WRONG_GRAIN | CONTRACT_GOVERNANCE_REQUIRED | 10 | 10 |
| OR-07 | OPEN.entry_market_time | OPEN.entry_market_time_utc_epoch_s | OPEN.market_timestamp_semantics | OPEN.market_timestamp_normalization_version | shadow_runtime | FIELD_EXISTS_PARTIAL_COVERAGE | SCHEMA_CHANGE_REQUIRED | 7 | 7 |
| OR-08 | decision_trace.p_success | decision_trace | FIELD_EXISTS_PARTIAL_COVERAGE | PRODUCER_CHANGE_REQUIRED | 2 | 2 |
| OR-09 | decision_trace.v10_entry | decision_trace | SATISFIED_CURRENTLY | SATISFIED_ALREADY | 2 | 2 |
| OR-10 | horizon_candidates.canonical_opportunity_id | horizon | selection_status | horizon_candidates | SATISFIED_CURRENTLY | SATISFIED_ALREADY | 1 | 1 |
| OR-11 | strategy_candidates.confidence | rank | selected | canonical_opportunity_id | strategy_candidates | SATISFIED_CURRENTLY | SATISFIED_ALREADY | 1 | 1 |
| OR-12 | portfolio_rankings.candidates.selection_status | candidates.rank_position | portfolio_rankings | SATISFIED_CURRENTLY | SATISFIED_ALREADY | 1 | 1 |
| OR-13 | opportunities.opportunity_id | state | overall_score | opportunities | FIELD_EXISTS_PARTIAL_COVERAGE | PARTIAL_DATASET_CHANGE_REQUIRED | 1 | 1 |
| OR-14 | events_v1:CANDLE:mt5_data:M5 ordered open/high/low/close between entry_market_time and exit_market_time inclusive | shadow_runtime (lifecycle side) + events (M5 bar side) | FIELD_EXISTS_NO_LINEAGE | NEW_DATASET_REQUIRED | 1 | 1 |
| OR-15 | shadow_trades.experiment_arm | arm_assigned_at | arm_schema_version, issued before any outcome field is populated | shadow_runtime | FIELD_MISSING | NEW_DATASET_REQUIRED | 1 | 1 |

## Shared root causes

### 1. re-point the dataset-schema gap register from the phantom `shadow_trades` to the real persisted producer `shadow_runtime_v1`

- Evidence: list_objects_v2 KeyCount=0 for supporting/shadow_trades/; 66258 rows persisted at supporting/shadow_runtime/schema_version=shadow_runtime_v1/
- Requirements satisfied: `OR-01, OR-02, OR-03, OR-04, OR-05, OR-06, OR-07, OR-15`
- Governed gaps affected: 27
- Questions affected: E5, D2, D3, D4, D5, M3, M4, M5, M6, M7, M8, M9, M10, M11, S4, X5, P1, L1, L2, L4, L5, R3, R4, R5, EX10, HORIZON-1, STRAT-1, PORT-1, L7
- Effect: removes 27 of 31 false dataset-level blockers without emitting a single new field

### 2. populate the lifecycle-bound decision snapshot (market_phase, h4_regime, pattern, strategy)

- Evidence: market_phase 100% NULL in shadow_runtime while 100% non-null in market_context_v1, decision_trace_v1 and strategy_observation_v1
- Requirements satisfied: `OR-02, OR-03, OR-04, OR-05`
- Governed gaps affected: 14
- Questions affected: M3, M4, M5, M6, M7, M8, M9, M10, M11, S4, P1, L1, L5
- Effect: one producer fix satisfies the market-state half of 14 registered gaps

### 3. emit market_timestamp_semantics + market_timestamp_normalization_version on 100% of OPEN rows

- Evidence: present on 7473/66258 shadow_runtime rows (11.3%); shadow_timestamp_normalization.reject() fails closed without them
- Requirements satisfied: `OR-07`
- Governed gaps affected: 7
- Questions affected: L1, L2, L4, L5, R4, R5, EX10
- Effect: unblocks canonical-UTC interpretation for every timestamp-dependent question

### 4. bind authoritative M5 OHLC bars to the shadow lifecycle at capture time

- Evidence: 12389/12389 M5 rows OHLC-complete in events_v1, but 0/15359 carry any lifecycle identity
- Requirements satisfied: `OR-14`
- Governed gaps affected: 1
- Questions affected: EX2
- Effect: the only true future-only data gap; NOT a market-data absence

### 5. issue a producer-authoritative experiment arm before outcome population

- Evidence: experiment_arm/arm_assigned_at/arm_schema_version = 0/66258 and absent from core/shadow/*.py
- Requirements satisfied: `OR-15`
- Governed gaps affected: 1
- Questions affected: L7
- Effect: the only true future-only labelling gap; cannot be inferred, only issued

## Dataset improvement matrix

| dataset | schema | rows | schema bump | producer bump | new dataset | backfill | order |
|---|---|---|---|---|---|---|---|
| shadow_trades | shadow_trades_v1 (DECLARED, NEVER EMITTED) | 0 | N/A | NO | NO -- re-point the register to shadow_runtime_v1 | NO | 0 |
| shadow_runtime | shadow_runtime_v1 | 66258 | YES -> shadow_runtime_v2 | YES | YES (exit_bar_path_m5_v2) | PARTIAL (OR-01..OR-07 yes; OR-14/OR-15 no) | 1 |
| events | events_v1 | 15359 CANDLE sampled / 249 partitions | YES -> events_v2 | YES | NO (if exit_bar_path_m5_v2 is owned separately) | NO | 2 |
| decision_trace | decision_trace_v1 | 25516 | NO | YES | NO | NO (p_success was never populated) | 3 |
| opportunities | opportunities_v1 | 100850 | NO | YES | NO | YES | 4 |

## EX2

```
EX2_OBSERVATION_REQUIREMENT
observation_requirement: OG-EX2-4db003c3ab76
missing_observable: events_v1:CANDLE:mt5_data:M5 ordered open/high/low/close between entry_market_time and exit_market_time inclusive
candidate_dataset: events (bar side) + shadow_runtime (lifecycle side)
authoritative_dataset: shadow_runtime (owns the bound path via a new exit_bar_path_m5_v2 dataset), with events as the authoritative bar source
producer: core/shadow/persistence.py + core/shadow/runtime.py (capture at lifecycle time)
required_fields: ['shadow_trade_id', 'canonical_opportunity_id', 'trade_horizon', 'symbol', 'bar_ts (strictly ascending)', 'open', 'high', 'low', 'close', 'bar_timestamp_semantics', 'bar_timestamp_normalization_version', 'schema_version']
required_identity: one record per (shadow_trade_id, canonical_opportunity_id, trade_horizon, M5 bar ts)
required_time_semantics: bar ts in producer market time at the causal bar close event plus CURRENT epoch attestation; interval (entry_market_time, exit_market_time] inclusive of exit bar
required_lineage: ['producer:shadow_trades', 'events_v1 producer instance', 'M5 aggregation lineage from the base timeframe feed', 'binding edge lifecycle -> bar proven at capture, not inferred post hoc']
existing_partial_source: events_v1 CANDLE M5 bars (12389/12389 OHLC-complete) and shadow_runtime trade_state_progression [{bar,close,r}] (20421 rows)
why_partial_source_insufficient: The M5 bars are complete and authoritative as BARS, but 0/15359 carry shadow_trade_id, canonical_opportunity_id or trade_horizon, so a (symbol, ts) range join is the only way to attach them, which the scientific contract does not accept as authoritative binding. trade_state_progression carries only {bar, close, r} -- no open, high or low -- and is permanently forbidden as a substitute.
schema_change_required: YES
producer_change_required: YES
new_dataset_required: YES
historical_backfill_possible: NO
future_collection_only: YES
m5_bars_exist: YES (events_v1, 100% OHLC-complete, 23 date partitions, 10 symbols)
defect_is: ABSENCE OF LIFECYCLE BINDING, NOT ABSENCE OF MARKET DATA
```

## L7

```
L7_OBSERVATION_REQUIREMENT
observation_requirement: OG-L7-4db003c3ab76
missing_observable: producer-authoritative experiment arm assignment exactly CONTROL or CANDIDATE, issued before outcome knowledge can contaminate it
authoritative_producer: core/shadow/persistence.py + core/shadow/runtime.py (assignment decision record)
authoritative_dataset: shadow_runtime (new arm dataset v2)
canonical_entity: (shadow_trade_id, canonical_opportunity_id, trade_horizon)
required_fields: ['experiment_arm', 'arm_assigned_at', 'arm_schema_version', 'arm_policy_version', 'arm_assignment_id', 'arm_issuer', 'arm_request_id', 'experiment_id', 'treatment_id']
allowed_values: ['CONTROL', 'CANDIDATE']
assignment_timestamp: arm_assigned_at, written at assignment time, strictly before any outcome field
assignment_policy_version: arm_policy_version + arm_schema_version required and persisted
experiment_treatment_identity: experiment_id / treatment_id required in addition to the arm
lineage: ['assignment decision record (issuer, request id, policy version)', 'immutability: the arm may never be rewritten or back-filled', 'back to the research candidate / promotion state that issued it']
existing_partial_source: NONE (experiment_arm/arm_assigned_at/arm_schema_version = 0/66258; no occurrence of 'experiment_arm' in core/shadow/*.py)
why_shadow_trades_v1_insufficient: shadow_trades_v1 is a dataset schema identity string, not an arm. The Stage 4 V2 successor contract currently overloads `schema_version` as the assignment field, which collides head-on with the persisted schema_version value 'shadow_runtime_v1' emitted on 66258/66258 rows. That collision must be corrected before any arm can bind.
schema_change_required: YES
producer_change_required: YES
new_dataset_required: YES
historical_backfill_possible: NO
future_collection_only: YES
governed_threshold: L7_MIN = {control: 100, candidate: 100, cell: 30}
```

## Mutation ledger

```
S3 WRITES: 0
SCHEMA MUTATIONS: 0
PRODUCER MUTATIONS: 0
RESEARCH RE-ENTRY EVENTS: 0
Q71+ STARTED: NO
BACKFILLS PERFORMED: 0
```

This audit is a BLUEPRINT ONLY. No dataset improvement has been implemented, no collection activated, no backfill performed and no research question re-entered.
