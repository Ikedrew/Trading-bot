# Stage 4 EX2/L7 blocker adjudication (2026-09-29)

Frozen-evidence, read-only adjudication of the two remaining Stage 4
implementation-gap blockers.  No live/S3 read, no Q71+, no new
scientific research run, and no post-hoc promotion of reconstructed
evidence into authoritative historical evidence.

- evidence fingerprint: `4db003c3ab767c72c55803bf01976f913a747c572c3efe62333e861f0b2084cc`
- baseline V1 certification: `b42b4bfcfa1eaf7df09cac9f88011c89362cf2ca907217e8466fff9fea3adc8b`
- V1 findings mutated: `False`
- global certification mutated: `False`

## Governed roster

- L7 CURRENT rows: 14,046
- EX2 governed predicate rows: 8,760
- 31 legacy rows were excluded before both questions.

## Per-question outcome

### EX2

- classification: `HISTORICAL_OBSERVATION_GAP`
- governed population: 8,760
- authoritative matched population: 5,916
- historically missing population: 2,844
- ambiguous: 0
- unexplained: 0
- accounting conserved: `True`
- implementation defect remains: NO
- historical observation gap: YES
- scientific state: IMPLEMENTATION_BLOCKED -> HISTORICALLY_UNANSWERABLE
- scientific result version: 1 -> 2
- certification version: 1 -> 2
- finding version: 1 -> 2
- gap work item transition: GWI-EX2-IMPL:OPEN/REPAIR_PENDING -> RESOLVED (implementation defect exhausted) -> GWI-EX2-IMPL observation gap OPEN
- observation requirement: `OG-EX2-4db003c3ab76`
- future collection required: YES
- historical backfill possible: NO
- expected re-entry trigger: `GOVERNED_EVIDENCE_CONTRACT_REEVALUATION`
- remaining blocker: 2,844 governed lifecycles have no authoritative ordered M5 OHLC exit path; {bar, close, r} cannot satisfy the EX2 contract and the 9,045 widened reconstruction path is permanently forbidden.

Evidence sources searched:

- `wave4_20260927/reconstruction.json governed lifecycle roster`
- `wave4_20260927/evidence/shadow_runtime.jsonl`
- `_exit_path_audit/shadow_runtime_v1`
- `_exit_path_audit/events_v1 events_v1:CANDLE:mt5_data:M5`

Identity/join chain:

1. `shadow_trade_id`
2. `canonical_opportunity_id`
3. `trade_horizon`
4. `OPEN/CLOSE entry/exit market time`
5. `canonical symbol`
6. `ordered events_v1 M5 OHLC bars after entry through exit`

Missing observable to be collected:

- events_v1:CANDLE:mt5_data:M5 ordered open/high/low/close between lifecycle entry_market_time and exit_market_time inclusive

### L7

- classification: `HISTORICAL_OBSERVATION_GAP`
- governed population: 14,046
- authoritative matched population: 0
- historically missing population: 14,046
- ambiguous: 0
- unexplained: 0
- accounting conserved: `True`
- implementation defect remains: NO
- historical observation gap: YES
- scientific state: IMPLEMENTATION_BLOCKED -> HISTORICALLY_UNANSWERABLE
- scientific result version: 1 -> 2
- certification version: 1 -> 2
- finding version: 1 -> 2
- gap work item transition: GWI-L7-IMPL:OPEN/REPAIR_PENDING -> RESOLVED (implementation defect exhausted) -> GWI-L7-IMPL observation gap OPEN
- observation requirement: `OG-L7-4db003c3ab76`
- future collection required: YES
- historical backfill possible: NO
- expected re-entry trigger: `GOVERNED_EVIDENCE_CONTRACT_REEVALUATION`
- remaining blocker: All 14,046 governed rows are historically unlabeled; no producer-authoritative CONTROL/CANDIDATE assignment exists and the L7 runner fails closed rather than inferring one.

Evidence sources searched:

- `wave4_20260927/evidence/decision_trace.jsonl`
- `wave4_20260927/evidence/execution_attempts.jsonl`
- `wave4_20260927/evidence/execution_context.jsonl`
- `wave4_20260927/evidence/execution_results.jsonl`
- `wave4_20260927/evidence/horizon_candidates.jsonl`
- `wave4_20260927/evidence/management_actions.jsonl`
- `wave4_20260927/evidence/market_context.jsonl`
- `wave4_20260927/evidence/portfolio_rankings.jsonl`
- `wave4_20260927/evidence/protection_audit.jsonl`
- `wave4_20260927/evidence/risk_deviation.jsonl`
- `wave4_20260927/evidence/shadow_runtime.jsonl`
- `wave4_20260927/evidence/shadow_trades.jsonl`
- `wave4_20260927/evidence/strategy_candidates.jsonl`
- `wave4_20260927/evidence/trade_truth.jsonl`
- `wave4_20260927/reconstruction.json`
- `frozen report/history identities (non-producer, non-authoritative for assignment)`

Identity/join chain:

1. `producer-issued assignment`
2. `shadow_trade_id`
3. `canonical_opportunity_id`
4. `trade_horizon`

Missing observable to be collected:

- producer-authoritative experiment arm assignment exactly CONTROL or CANDIDATE, issued before outcome knowledge can contaminate it

## Implementation-gap phase accounting

```text
ORIGINAL IMPLEMENTATION GAPS: 8
REPAIRED THROUGH GOVERNED REENTRY: 6
CONVERTED TO HISTORICAL OBSERVATION/DATA GAPS: 2
GENUINE IMPLEMENTATION DEFECTS REMAINING: 0
UNEXPLAINED EVIDENCE IDENTITIES: 0
```

The implementation defects are exhausted.  EX2 and L7 are NOT fully
resolved as scientific questions: both move into the next Stage 4
observation/data-governance phase as HISTORICALLY_UNANSWERABLE.
Future observation requirements are recorded only; no producer or
schema change was deployed by this adjudication.
