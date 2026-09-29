# Stage 4 Dataset/Schema Gap Register

Generated from frozen Stage 4 certification evidence; no schema migration or research execution was performed.

## STAGE4-DATA-E5

```json
{
  "affected_question_ids": [
    "E5"
  ],
  "compatibility_considerations": "Additive V2/V3 field/event evolution; legacy rows remain historical and must not be backfilled or silently substituted.",
  "current_datasets": [
    "shadow_trades"
  ],
  "current_schema_versions": {
    "shadow_trades": "shadow_trades_v1"
  },
  "earliest_future_answerable_epoch": "FIRST_POST_2026-09-27_FROZEN_EPOCH_WITH_COMPLETE_NEW_CONTRACT",
  "gap_id": "STAGE4-DATA-E5",
  "historical_recoverability": "UNRECOVERABLE",
  "missing_observable": [
    "simulated_outcome.pnl_r_multiple"
  ],
  "priority": "P0",
  "proposed_evidence_contract": "Persist the missing observable at its causal event time with canonical_opportunity_id/entity_id lineage and CURRENT epoch provenance.",
  "proposed_schema_change": {
    "add_required_observables": [
      "simulated_outcome.pnl_r_multiple"
    ],
    "epoch_attestation": "CURRENT",
    "preserve_lineage": true
  },
  "rationale": "CURRENT artifact with status INSUFFICIENT_DATA",
  "reason_unrecoverable": "Frozen Gate 1 accounting is EXHAUSTED and Wave 4 resolves the required observable/relationship as unavailable or irrecoverably lossy.",
  "required_future_producer": [
    "producer:shadow_trades"
  ],
  "schema_evolution": "V2_OR_V3_REQUIRED"
}
```

## STAGE4-DATA-M3

```json
{
  "affected_question_ids": [
    "M3"
  ],
  "compatibility_considerations": "Additive V2/V3 field/event evolution; legacy rows remain historical and must not be backfilled or silently substituted.",
  "current_datasets": [
    "shadow_trades"
  ],
  "current_schema_versions": {
    "shadow_trades": "shadow_trades_v1"
  },
  "earliest_future_answerable_epoch": "FIRST_POST_2026-09-27_FROZEN_EPOCH_WITH_COMPLETE_NEW_CONTRACT",
  "gap_id": "STAGE4-DATA-M3",
  "historical_recoverability": "UNRECOVERABLE",
  "missing_observable": [
    "decision_snapshot.h4_regime",
    "decision_snapshot.market_phase",
    "simulated_outcome.pnl_r_multiple"
  ],
  "priority": "P1",
  "proposed_evidence_contract": "Persist the missing observable at its causal event time with canonical_opportunity_id/entity_id lineage and CURRENT epoch provenance.",
  "proposed_schema_change": {
    "add_required_observables": [
      "decision_snapshot.h4_regime",
      "decision_snapshot.market_phase",
      "simulated_outcome.pnl_r_multiple"
    ],
    "epoch_attestation": "CURRENT",
    "preserve_lineage": true
  },
  "rationale": "CURRENT artifact with status INSUFFICIENT_DATA",
  "reason_unrecoverable": "Frozen Gate 1 accounting is EXHAUSTED and Wave 4 resolves the required observable/relationship as unavailable or irrecoverably lossy.",
  "required_future_producer": [
    "producer:shadow_trades"
  ],
  "schema_evolution": "V2_OR_V3_REQUIRED"
}
```

## STAGE4-DATA-M4

```json
{
  "affected_question_ids": [
    "M4"
  ],
  "compatibility_considerations": "Additive V2/V3 field/event evolution; legacy rows remain historical and must not be backfilled or silently substituted.",
  "current_datasets": [
    "shadow_trades"
  ],
  "current_schema_versions": {
    "shadow_trades": "shadow_trades_v1"
  },
  "earliest_future_answerable_epoch": "FIRST_POST_2026-09-27_FROZEN_EPOCH_WITH_COMPLETE_NEW_CONTRACT",
  "gap_id": "STAGE4-DATA-M4",
  "historical_recoverability": "UNRECOVERABLE",
  "missing_observable": [
    "decision_snapshot.h4_regime | decision_snapshot.market_phase | decision_snapshot.strategy; simulated_outcome.pnl_r_multiple"
  ],
  "priority": "P1",
  "proposed_evidence_contract": "Persist the missing observable at its causal event time with canonical_opportunity_id/entity_id lineage and CURRENT epoch provenance.",
  "proposed_schema_change": {
    "add_required_observables": [
      "decision_snapshot.h4_regime | decision_snapshot.market_phase | decision_snapshot.strategy; simulated_outcome.pnl_r_multiple"
    ],
    "epoch_attestation": "CURRENT",
    "preserve_lineage": true
  },
  "rationale": "CURRENT artifact with status INSUFFICIENT_DATA",
  "reason_unrecoverable": "Frozen Gate 1 accounting is EXHAUSTED and Wave 4 resolves the required observable/relationship as unavailable or irrecoverably lossy.",
  "required_future_producer": [
    "producer:shadow_trades"
  ],
  "schema_evolution": "V2_OR_V3_REQUIRED"
}
```

## STAGE4-DATA-M5

```json
{
  "affected_question_ids": [
    "M5"
  ],
  "compatibility_considerations": "Additive V2/V3 field/event evolution; legacy rows remain historical and must not be backfilled or silently substituted.",
  "current_datasets": [
    "shadow_trades"
  ],
  "current_schema_versions": {
    "shadow_trades": "shadow_trades_v1"
  },
  "earliest_future_answerable_epoch": "FIRST_POST_2026-09-27_FROZEN_EPOCH_WITH_COMPLETE_NEW_CONTRACT",
  "gap_id": "STAGE4-DATA-M5",
  "historical_recoverability": "UNRECOVERABLE",
  "missing_observable": [
    "simulated_outcome.pnl_r_multiple | decision_snapshot.market_phase"
  ],
  "priority": "P1",
  "proposed_evidence_contract": "Persist the missing observable at its causal event time with canonical_opportunity_id/entity_id lineage and CURRENT epoch provenance.",
  "proposed_schema_change": {
    "add_required_observables": [
      "simulated_outcome.pnl_r_multiple | decision_snapshot.market_phase"
    ],
    "epoch_attestation": "CURRENT",
    "preserve_lineage": true
  },
  "rationale": "CURRENT artifact with status INSUFFICIENT_DATA",
  "reason_unrecoverable": "Frozen Gate 1 accounting is EXHAUSTED and Wave 4 resolves the required observable/relationship as unavailable or irrecoverably lossy.",
  "required_future_producer": [
    "producer:shadow_trades"
  ],
  "schema_evolution": "V2_OR_V3_REQUIRED"
}
```

## STAGE4-DATA-M6

```json
{
  "affected_question_ids": [
    "M6"
  ],
  "compatibility_considerations": "Additive V2/V3 field/event evolution; legacy rows remain historical and must not be backfilled or silently substituted.",
  "current_datasets": [
    "shadow_trades"
  ],
  "current_schema_versions": {
    "shadow_trades": "shadow_trades_v1"
  },
  "earliest_future_answerable_epoch": "FIRST_POST_2026-09-27_FROZEN_EPOCH_WITH_COMPLETE_NEW_CONTRACT",
  "gap_id": "STAGE4-DATA-M6",
  "historical_recoverability": "UNRECOVERABLE",
  "missing_observable": [
    "decision_snapshot.market_phase; simulated_outcome.pnl_r_multiple"
  ],
  "priority": "P1",
  "proposed_evidence_contract": "Persist the missing observable at its causal event time with canonical_opportunity_id/entity_id lineage and CURRENT epoch provenance.",
  "proposed_schema_change": {
    "add_required_observables": [
      "decision_snapshot.market_phase; simulated_outcome.pnl_r_multiple"
    ],
    "epoch_attestation": "CURRENT",
    "preserve_lineage": true
  },
  "rationale": "CURRENT artifact with status INSUFFICIENT_DATA",
  "reason_unrecoverable": "Frozen Gate 1 accounting is EXHAUSTED and Wave 4 resolves the required observable/relationship as unavailable or irrecoverably lossy.",
  "required_future_producer": [
    "producer:shadow_trades"
  ],
  "schema_evolution": "V2_OR_V3_REQUIRED"
}
```

## STAGE4-DATA-M7

```json
{
  "affected_question_ids": [
    "M7"
  ],
  "compatibility_considerations": "Additive V2/V3 field/event evolution; legacy rows remain historical and must not be backfilled or silently substituted.",
  "current_datasets": [
    "shadow_trades"
  ],
  "current_schema_versions": {
    "shadow_trades": "shadow_trades_v1"
  },
  "earliest_future_answerable_epoch": "FIRST_POST_2026-09-27_FROZEN_EPOCH_WITH_COMPLETE_NEW_CONTRACT",
  "gap_id": "STAGE4-DATA-M7",
  "historical_recoverability": "UNRECOVERABLE",
  "missing_observable": [
    "decision_snapshot.h4_regime",
    "decision_snapshot.market_phase",
    "simulated_outcome.pnl_r_multiple"
  ],
  "priority": "P1",
  "proposed_evidence_contract": "Persist the missing observable at its causal event time with canonical_opportunity_id/entity_id lineage and CURRENT epoch provenance.",
  "proposed_schema_change": {
    "add_required_observables": [
      "decision_snapshot.h4_regime",
      "decision_snapshot.market_phase",
      "simulated_outcome.pnl_r_multiple"
    ],
    "epoch_attestation": "CURRENT",
    "preserve_lineage": true
  },
  "rationale": "CURRENT artifact with status INSUFFICIENT_DATA",
  "reason_unrecoverable": "Frozen Gate 1 accounting is EXHAUSTED and Wave 4 resolves the required observable/relationship as unavailable or irrecoverably lossy.",
  "required_future_producer": [
    "producer:shadow_trades"
  ],
  "schema_evolution": "V2_OR_V3_REQUIRED"
}
```

## STAGE4-DATA-M8

```json
{
  "affected_question_ids": [
    "M8"
  ],
  "compatibility_considerations": "Additive V2/V3 field/event evolution; legacy rows remain historical and must not be backfilled or silently substituted.",
  "current_datasets": [
    "market_context",
    "shadow_trades"
  ],
  "current_schema_versions": {
    "market_context": "market_context_v1",
    "shadow_trades": "shadow_trades_v1"
  },
  "earliest_future_answerable_epoch": "FIRST_POST_2026-09-27_FROZEN_EPOCH_WITH_COMPLETE_NEW_CONTRACT",
  "gap_id": "STAGE4-DATA-M8",
  "historical_recoverability": "UNRECOVERABLE",
  "missing_observable": [
    "market_phase + timestamp",
    "simulated_outcome.pnl_r_multiple"
  ],
  "priority": "P1",
  "proposed_evidence_contract": "Persist the missing observable at its causal event time with canonical_opportunity_id/entity_id lineage and CURRENT epoch provenance.",
  "proposed_schema_change": {
    "add_required_observables": [
      "market_phase + timestamp",
      "simulated_outcome.pnl_r_multiple"
    ],
    "epoch_attestation": "CURRENT",
    "preserve_lineage": true
  },
  "rationale": "CURRENT artifact with status INSUFFICIENT_DATA",
  "reason_unrecoverable": "Frozen Gate 1 accounting is EXHAUSTED and Wave 4 resolves the required observable/relationship as unavailable or irrecoverably lossy.",
  "required_future_producer": [
    "producer:market_context",
    "producer:shadow_trades"
  ],
  "schema_evolution": "V2_OR_V3_REQUIRED"
}
```

## STAGE4-DATA-M9

```json
{
  "affected_question_ids": [
    "M9"
  ],
  "compatibility_considerations": "Additive V2/V3 field/event evolution; legacy rows remain historical and must not be backfilled or silently substituted.",
  "current_datasets": [
    "shadow_trades"
  ],
  "current_schema_versions": {
    "shadow_trades": "shadow_trades_v1"
  },
  "earliest_future_answerable_epoch": "FIRST_POST_2026-09-27_FROZEN_EPOCH_WITH_COMPLETE_NEW_CONTRACT",
  "gap_id": "STAGE4-DATA-M9",
  "historical_recoverability": "UNRECOVERABLE",
  "missing_observable": [
    "decision_snapshot.market_phase | decision_snapshot.pattern; simulated_outcome.pnl_r_multiple"
  ],
  "priority": "P0",
  "proposed_evidence_contract": "Persist the missing observable at its causal event time with canonical_opportunity_id/entity_id lineage and CURRENT epoch provenance.",
  "proposed_schema_change": {
    "add_required_observables": [
      "decision_snapshot.market_phase | decision_snapshot.pattern; simulated_outcome.pnl_r_multiple"
    ],
    "epoch_attestation": "CURRENT",
    "preserve_lineage": true
  },
  "rationale": "CURRENT artifact with status INSUFFICIENT_DATA",
  "reason_unrecoverable": "Frozen Gate 1 accounting is EXHAUSTED and Wave 4 resolves the required observable/relationship as unavailable or irrecoverably lossy.",
  "required_future_producer": [
    "producer:shadow_trades"
  ],
  "schema_evolution": "V2_OR_V3_REQUIRED"
}
```

## STAGE4-DATA-M10

```json
{
  "affected_question_ids": [
    "M10"
  ],
  "compatibility_considerations": "Additive V2/V3 field/event evolution; legacy rows remain historical and must not be backfilled or silently substituted.",
  "current_datasets": [
    "shadow_trades"
  ],
  "current_schema_versions": {
    "shadow_trades": "shadow_trades_v1"
  },
  "earliest_future_answerable_epoch": "FIRST_POST_2026-09-27_FROZEN_EPOCH_WITH_COMPLETE_NEW_CONTRACT",
  "gap_id": "STAGE4-DATA-M10",
  "historical_recoverability": "UNRECOVERABLE",
  "missing_observable": [
    "decision_snapshot.market_phase | decision_snapshot.pattern (family derived via STRATEGY_FAMILIES mapping); simulated_outcome.pnl_r_multiple"
  ],
  "priority": "P0",
  "proposed_evidence_contract": "Persist the missing observable at its causal event time with canonical_opportunity_id/entity_id lineage and CURRENT epoch provenance.",
  "proposed_schema_change": {
    "add_required_observables": [
      "decision_snapshot.market_phase | decision_snapshot.pattern (family derived via STRATEGY_FAMILIES mapping); simulated_outcome.pnl_r_multiple"
    ],
    "epoch_attestation": "CURRENT",
    "preserve_lineage": true
  },
  "rationale": "CURRENT artifact with status INSUFFICIENT_DATA",
  "reason_unrecoverable": "Frozen Gate 1 accounting is EXHAUSTED and Wave 4 resolves the required observable/relationship as unavailable or irrecoverably lossy.",
  "required_future_producer": [
    "producer:shadow_trades"
  ],
  "schema_evolution": "V2_OR_V3_REQUIRED"
}
```

## STAGE4-DATA-M11

```json
{
  "affected_question_ids": [
    "M11"
  ],
  "compatibility_considerations": "Additive V2/V3 field/event evolution; legacy rows remain historical and must not be backfilled or silently substituted.",
  "current_datasets": [
    "shadow_trades",
    "decision_trace"
  ],
  "current_schema_versions": {
    "decision_trace": "decision_trace_v1",
    "shadow_trades": "shadow_trades_v1"
  },
  "earliest_future_answerable_epoch": "FIRST_POST_2026-09-27_FROZEN_EPOCH_WITH_COMPLETE_NEW_CONTRACT",
  "gap_id": "STAGE4-DATA-M11",
  "historical_recoverability": "UNRECOVERABLE",
  "missing_observable": [
    "v10_market_state.regime + v10_market_state.h4.market_phase + v10_market_state.h1.dominant_trend",
    "decision_snapshot.pattern",
    "simulated_outcome.pnl_r_multiple"
  ],
  "priority": "P0",
  "proposed_evidence_contract": "Persist the missing observable at its causal event time with canonical_opportunity_id/entity_id lineage and CURRENT epoch provenance.",
  "proposed_schema_change": {
    "add_required_observables": [
      "v10_market_state.regime + v10_market_state.h4.market_phase + v10_market_state.h1.dominant_trend",
      "decision_snapshot.pattern",
      "simulated_outcome.pnl_r_multiple"
    ],
    "epoch_attestation": "CURRENT",
    "preserve_lineage": true
  },
  "rationale": "CURRENT artifact with status INSUFFICIENT_DATA",
  "reason_unrecoverable": "Frozen Gate 1 accounting is EXHAUSTED and Wave 4 resolves the required observable/relationship as unavailable or irrecoverably lossy.",
  "required_future_producer": [
    "producer:shadow_trades",
    "producer:decision_trace"
  ],
  "schema_evolution": "V2_OR_V3_REQUIRED"
}
```

## STAGE4-DATA-D2

```json
{
  "affected_question_ids": [
    "D2"
  ],
  "compatibility_considerations": "Additive V2/V3 field/event evolution; legacy rows remain historical and must not be backfilled or silently substituted.",
  "current_datasets": [
    "decision_trace",
    "shadow_trades"
  ],
  "current_schema_versions": {
    "decision_trace": "decision_trace_v1",
    "shadow_trades": "shadow_trades_v1"
  },
  "earliest_future_answerable_epoch": "FIRST_POST_2026-09-27_FROZEN_EPOCH_WITH_COMPLETE_NEW_CONTRACT",
  "gap_id": "STAGE4-DATA-D2",
  "historical_recoverability": "UNRECOVERABLE",
  "missing_observable": [
    "simulated_outcome.pnl_r_multiple"
  ],
  "priority": "P0",
  "proposed_evidence_contract": "Persist the missing observable at its causal event time with canonical_opportunity_id/entity_id lineage and CURRENT epoch provenance.",
  "proposed_schema_change": {
    "add_required_observables": [
      "simulated_outcome.pnl_r_multiple"
    ],
    "epoch_attestation": "CURRENT",
    "preserve_lineage": true
  },
  "rationale": "CURRENT artifact with status INSUFFICIENT_DATA",
  "reason_unrecoverable": "Frozen Gate 1 accounting is EXHAUSTED and Wave 4 resolves the required observable/relationship as unavailable or irrecoverably lossy.",
  "required_future_producer": [
    "producer:decision_trace",
    "producer:shadow_trades"
  ],
  "schema_evolution": "V2_OR_V3_REQUIRED"
}
```

## STAGE4-DATA-D3

```json
{
  "affected_question_ids": [
    "D3"
  ],
  "compatibility_considerations": "Additive V2/V3 field/event evolution; legacy rows remain historical and must not be backfilled or silently substituted.",
  "current_datasets": [
    "decision_trace",
    "shadow_trades"
  ],
  "current_schema_versions": {
    "decision_trace": "decision_trace_v1",
    "shadow_trades": "shadow_trades_v1"
  },
  "earliest_future_answerable_epoch": "FIRST_POST_2026-09-27_FROZEN_EPOCH_WITH_COMPLETE_NEW_CONTRACT",
  "gap_id": "STAGE4-DATA-D3",
  "historical_recoverability": "UNRECOVERABLE",
  "missing_observable": [
    "p_success",
    "v10_entry",
    "simulated_outcome.pnl_r_multiple"
  ],
  "priority": "P1",
  "proposed_evidence_contract": "Persist the missing observable at its causal event time with canonical_opportunity_id/entity_id lineage and CURRENT epoch provenance.",
  "proposed_schema_change": {
    "add_required_observables": [
      "p_success",
      "v10_entry",
      "simulated_outcome.pnl_r_multiple"
    ],
    "epoch_attestation": "CURRENT",
    "preserve_lineage": true
  },
  "rationale": "CURRENT artifact with status INSUFFICIENT_DATA",
  "reason_unrecoverable": "Frozen Gate 1 accounting is EXHAUSTED and Wave 4 resolves the required observable/relationship as unavailable or irrecoverably lossy.",
  "required_future_producer": [
    "producer:decision_trace",
    "producer:shadow_trades"
  ],
  "schema_evolution": "V2_OR_V3_REQUIRED"
}
```

## STAGE4-DATA-D4

```json
{
  "affected_question_ids": [
    "D4"
  ],
  "compatibility_considerations": "Additive V2/V3 field/event evolution; legacy rows remain historical and must not be backfilled or silently substituted.",
  "current_datasets": [
    "decision_trace",
    "shadow_trades"
  ],
  "current_schema_versions": {
    "decision_trace": "decision_trace_v1",
    "shadow_trades": "shadow_trades_v1"
  },
  "earliest_future_answerable_epoch": "FIRST_POST_2026-09-27_FROZEN_EPOCH_WITH_COMPLETE_NEW_CONTRACT",
  "gap_id": "STAGE4-DATA-D4",
  "historical_recoverability": "UNRECOVERABLE",
  "missing_observable": [
    "simulated_outcome.pnl_r_multiple"
  ],
  "priority": "P1",
  "proposed_evidence_contract": "Persist the missing observable at its causal event time with canonical_opportunity_id/entity_id lineage and CURRENT epoch provenance.",
  "proposed_schema_change": {
    "add_required_observables": [
      "simulated_outcome.pnl_r_multiple"
    ],
    "epoch_attestation": "CURRENT",
    "preserve_lineage": true
  },
  "rationale": "CURRENT artifact with status BLOCKED",
  "reason_unrecoverable": "Frozen Gate 1 accounting is EXHAUSTED and Wave 4 resolves the required observable/relationship as unavailable or irrecoverably lossy.",
  "required_future_producer": [
    "producer:decision_trace",
    "producer:shadow_trades"
  ],
  "schema_evolution": "V2_OR_V3_REQUIRED"
}
```

## STAGE4-DATA-D5

```json
{
  "affected_question_ids": [
    "D5"
  ],
  "compatibility_considerations": "Additive V2/V3 field/event evolution; legacy rows remain historical and must not be backfilled or silently substituted.",
  "current_datasets": [
    "decision_trace",
    "shadow_trades"
  ],
  "current_schema_versions": {
    "decision_trace": "decision_trace_v1",
    "shadow_trades": "shadow_trades_v1"
  },
  "earliest_future_answerable_epoch": "FIRST_POST_2026-09-27_FROZEN_EPOCH_WITH_COMPLETE_NEW_CONTRACT",
  "gap_id": "STAGE4-DATA-D5",
  "historical_recoverability": "UNRECOVERABLE",
  "missing_observable": [
    "simulated_outcome.pnl_r_multiple"
  ],
  "priority": "P1",
  "proposed_evidence_contract": "Persist the missing observable at its causal event time with canonical_opportunity_id/entity_id lineage and CURRENT epoch provenance.",
  "proposed_schema_change": {
    "add_required_observables": [
      "simulated_outcome.pnl_r_multiple"
    ],
    "epoch_attestation": "CURRENT",
    "preserve_lineage": true
  },
  "rationale": "CURRENT artifact with status BLOCKED",
  "reason_unrecoverable": "Frozen Gate 1 accounting is EXHAUSTED and Wave 4 resolves the required observable/relationship as unavailable or irrecoverably lossy.",
  "required_future_producer": [
    "producer:decision_trace",
    "producer:shadow_trades"
  ],
  "schema_evolution": "V2_OR_V3_REQUIRED"
}
```

## STAGE4-DATA-S4

```json
{
  "affected_question_ids": [
    "S4"
  ],
  "compatibility_considerations": "Additive V2/V3 field/event evolution; legacy rows remain historical and must not be backfilled or silently substituted.",
  "current_datasets": [
    "shadow_trades"
  ],
  "current_schema_versions": {
    "shadow_trades": "shadow_trades_v1"
  },
  "earliest_future_answerable_epoch": "FIRST_POST_2026-09-27_FROZEN_EPOCH_WITH_COMPLETE_NEW_CONTRACT",
  "gap_id": "STAGE4-DATA-S4",
  "historical_recoverability": "UNRECOVERABLE",
  "missing_observable": [
    "identity.canonical_opportunity_id | identity.shadow_type | decision_snapshot.strategy | decision_snapshot.market_phase | simulated_outcome.pnl_r_multiple"
  ],
  "priority": "P1",
  "proposed_evidence_contract": "Persist the missing observable at its causal event time with canonical_opportunity_id/entity_id lineage and CURRENT epoch provenance.",
  "proposed_schema_change": {
    "add_required_observables": [
      "identity.canonical_opportunity_id | identity.shadow_type | decision_snapshot.strategy | decision_snapshot.market_phase | simulated_outcome.pnl_r_multiple"
    ],
    "epoch_attestation": "CURRENT",
    "preserve_lineage": true
  },
  "rationale": "CURRENT artifact with status INSUFFICIENT_DATA",
  "reason_unrecoverable": "Frozen Gate 1 accounting is EXHAUSTED and Wave 4 resolves the required observable/relationship as unavailable or irrecoverably lossy.",
  "required_future_producer": [
    "producer:shadow_trades"
  ],
  "schema_evolution": "V2_OR_V3_REQUIRED"
}
```

## STAGE4-DATA-R3

```json
{
  "affected_question_ids": [
    "R3"
  ],
  "compatibility_considerations": "Additive V2/V3 field/event evolution; legacy rows remain historical and must not be backfilled or silently substituted.",
  "current_datasets": [
    "shadow_trades"
  ],
  "current_schema_versions": {
    "shadow_trades": "shadow_trades_v1"
  },
  "earliest_future_answerable_epoch": "FIRST_POST_2026-09-27_FROZEN_EPOCH_WITH_COMPLETE_NEW_CONTRACT",
  "gap_id": "STAGE4-DATA-R3",
  "historical_recoverability": "UNRECOVERABLE",
  "missing_observable": [
    "canonical_opportunity_id"
  ],
  "priority": "P0",
  "proposed_evidence_contract": "Persist the missing observable at its causal event time with canonical_opportunity_id/entity_id lineage and CURRENT epoch provenance.",
  "proposed_schema_change": {
    "add_required_observables": [
      "canonical_opportunity_id"
    ],
    "epoch_attestation": "CURRENT",
    "preserve_lineage": true
  },
  "rationale": "Historical governed population has no analytically usable rows; absent/unresolved: win_rate, position_size",
  "reason_unrecoverable": "Frozen Gate 1 accounting is EXHAUSTED and Wave 4 resolves the required observable/relationship as unavailable or irrecoverably lossy.",
  "required_future_producer": [
    "producer:shadow_trades"
  ],
  "schema_evolution": "V2_OR_V3_REQUIRED"
}
```

## STAGE4-DATA-R4

```json
{
  "affected_question_ids": [
    "R4"
  ],
  "compatibility_considerations": "Additive V2/V3 field/event evolution; legacy rows remain historical and must not be backfilled or silently substituted.",
  "current_datasets": [
    "shadow_trades"
  ],
  "current_schema_versions": {
    "shadow_trades": "shadow_trades_v1"
  },
  "earliest_future_answerable_epoch": "FIRST_POST_2026-09-27_FROZEN_EPOCH_WITH_COMPLETE_NEW_CONTRACT",
  "gap_id": "STAGE4-DATA-R4",
  "historical_recoverability": "UNRECOVERABLE",
  "missing_observable": [
    "canonical_opportunity_id",
    "entry_time"
  ],
  "priority": "P0",
  "proposed_evidence_contract": "Persist the missing observable at its causal event time with canonical_opportunity_id/entity_id lineage and CURRENT epoch provenance.",
  "proposed_schema_change": {
    "add_required_observables": [
      "canonical_opportunity_id",
      "entry_time"
    ],
    "epoch_attestation": "CURRENT",
    "preserve_lineage": true
  },
  "rationale": "Historical governed population has no analytically usable rows; absent/unresolved: entry_time",
  "reason_unrecoverable": "Frozen Gate 1 accounting is EXHAUSTED and Wave 4 resolves the required observable/relationship as unavailable or irrecoverably lossy.",
  "required_future_producer": [
    "producer:shadow_trades"
  ],
  "schema_evolution": "V2_OR_V3_REQUIRED"
}
```

## STAGE4-DATA-R5

```json
{
  "affected_question_ids": [
    "R5"
  ],
  "compatibility_considerations": "Additive V2/V3 field/event evolution; legacy rows remain historical and must not be backfilled or silently substituted.",
  "current_datasets": [
    "shadow_trades"
  ],
  "current_schema_versions": {
    "shadow_trades": "shadow_trades_v1"
  },
  "earliest_future_answerable_epoch": "FIRST_POST_2026-09-27_FROZEN_EPOCH_WITH_COMPLETE_NEW_CONTRACT",
  "gap_id": "STAGE4-DATA-R5",
  "historical_recoverability": "UNRECOVERABLE",
  "missing_observable": [
    "canonical_opportunity_id"
  ],
  "priority": "P1",
  "proposed_evidence_contract": "Persist the missing observable at its causal event time with canonical_opportunity_id/entity_id lineage and CURRENT epoch provenance.",
  "proposed_schema_change": {
    "add_required_observables": [
      "canonical_opportunity_id"
    ],
    "epoch_attestation": "CURRENT",
    "preserve_lineage": true
  },
  "rationale": "Historical governed population has no analytically usable rows; absent/unresolved: win_rate",
  "reason_unrecoverable": "Frozen Gate 1 accounting is EXHAUSTED and Wave 4 resolves the required observable/relationship as unavailable or irrecoverably lossy.",
  "required_future_producer": [
    "producer:shadow_trades"
  ],
  "schema_evolution": "V2_OR_V3_REQUIRED"
}
```

## STAGE4-DATA-L1

```json
{
  "affected_question_ids": [
    "L1"
  ],
  "compatibility_considerations": "Additive V2/V3 field/event evolution; legacy rows remain historical and must not be backfilled or silently substituted.",
  "current_datasets": [
    "shadow_trades"
  ],
  "current_schema_versions": {
    "shadow_trades": "shadow_trades_v1"
  },
  "earliest_future_answerable_epoch": "FIRST_POST_2026-09-27_FROZEN_EPOCH_WITH_COMPLETE_NEW_CONTRACT",
  "gap_id": "STAGE4-DATA-L1",
  "historical_recoverability": "UNRECOVERABLE",
  "missing_observable": [
    "identity.canonical_opportunity_id | identity.shadow_type | decision_snapshot.pattern | simulated_outcome.pnl_r_multiple",
    "OPEN.entry_market_time | OPEN.entry_market_time_utc_epoch_s | OPEN.market_timestamp_semantics | OPEN.market_timestamp_normalization_version",
    "CANDLE:mt5_data:M5.payload.ts and OHLC"
  ],
  "priority": "P1",
  "proposed_evidence_contract": "Persist the missing observable at its causal event time with canonical_opportunity_id/entity_id lineage and CURRENT epoch provenance.",
  "proposed_schema_change": {
    "add_required_observables": [
      "identity.canonical_opportunity_id | identity.shadow_type | decision_snapshot.pattern | simulated_outcome.pnl_r_multiple",
      "OPEN.entry_market_time | OPEN.entry_market_time_utc_epoch_s | OPEN.market_timestamp_semantics | OPEN.market_timestamp_normalization_version",
      "CANDLE:mt5_data:M5.payload.ts and OHLC"
    ],
    "epoch_attestation": "CURRENT",
    "preserve_lineage": true
  },
  "rationale": "Historical governed population has no analytically usable rows; absent/unresolved: entry_time",
  "reason_unrecoverable": "Frozen Gate 1 accounting is EXHAUSTED and Wave 4 resolves the required observable/relationship as unavailable or irrecoverably lossy.",
  "required_future_producer": [
    "producer:shadow_trades"
  ],
  "schema_evolution": "V2_OR_V3_REQUIRED"
}
```

## STAGE4-DATA-L2

```json
{
  "affected_question_ids": [
    "L2"
  ],
  "compatibility_considerations": "Additive V2/V3 field/event evolution; legacy rows remain historical and must not be backfilled or silently substituted.",
  "current_datasets": [
    "shadow_trades"
  ],
  "current_schema_versions": {
    "shadow_trades": "shadow_trades_v1"
  },
  "earliest_future_answerable_epoch": "FIRST_POST_2026-09-27_FROZEN_EPOCH_WITH_COMPLETE_NEW_CONTRACT",
  "gap_id": "STAGE4-DATA-L2",
  "historical_recoverability": "UNRECOVERABLE",
  "missing_observable": [
    "canonical_opportunity_id",
    "entry_time"
  ],
  "priority": "P1",
  "proposed_evidence_contract": "Persist the missing observable at its causal event time with canonical_opportunity_id/entity_id lineage and CURRENT epoch provenance.",
  "proposed_schema_change": {
    "add_required_observables": [
      "canonical_opportunity_id",
      "entry_time"
    ],
    "epoch_attestation": "CURRENT",
    "preserve_lineage": true
  },
  "rationale": "CURRENT artifact with status INSUFFICIENT_DATA",
  "reason_unrecoverable": "Frozen Gate 1 accounting is EXHAUSTED and Wave 4 resolves the required observable/relationship as unavailable or irrecoverably lossy.",
  "required_future_producer": [
    "producer:shadow_trades"
  ],
  "schema_evolution": "V2_OR_V3_REQUIRED"
}
```

## STAGE4-DATA-L4

```json
{
  "affected_question_ids": [
    "L4"
  ],
  "compatibility_considerations": "Additive V2/V3 field/event evolution; legacy rows remain historical and must not be backfilled or silently substituted.",
  "current_datasets": [
    "shadow_trades",
    "market_context"
  ],
  "current_schema_versions": {
    "market_context": "market_context_v1",
    "shadow_trades": "shadow_trades_v1"
  },
  "earliest_future_answerable_epoch": "FIRST_POST_2026-09-27_FROZEN_EPOCH_WITH_COMPLETE_NEW_CONTRACT",
  "gap_id": "STAGE4-DATA-L4",
  "historical_recoverability": "UNRECOVERABLE",
  "missing_observable": [
    "identity.canonical_opportunity_id | identity.symbol | identity.shadow_type | simulated_outcome.pnl_r_multiple",
    "OPEN.entry_market_time canonical UTC authority",
    "symbol | bar_time | regime"
  ],
  "priority": "P1",
  "proposed_evidence_contract": "Persist the missing observable at its causal event time with canonical_opportunity_id/entity_id lineage and CURRENT epoch provenance.",
  "proposed_schema_change": {
    "add_required_observables": [
      "identity.canonical_opportunity_id | identity.symbol | identity.shadow_type | simulated_outcome.pnl_r_multiple",
      "OPEN.entry_market_time canonical UTC authority",
      "symbol | bar_time | regime"
    ],
    "epoch_attestation": "CURRENT",
    "preserve_lineage": true
  },
  "rationale": "Historical governed population has no analytically usable rows; absent/unresolved: entry_time",
  "reason_unrecoverable": "Frozen Gate 1 accounting is EXHAUSTED and Wave 4 resolves the required observable/relationship as unavailable or irrecoverably lossy.",
  "required_future_producer": [
    "producer:shadow_trades",
    "producer:market_context"
  ],
  "schema_evolution": "V2_OR_V3_REQUIRED"
}
```

## STAGE4-DATA-L5

```json
{
  "affected_question_ids": [
    "L5"
  ],
  "compatibility_considerations": "Additive V2/V3 field/event evolution; legacy rows remain historical and must not be backfilled or silently substituted.",
  "current_datasets": [
    "shadow_trades"
  ],
  "current_schema_versions": {
    "shadow_trades": "shadow_trades_v1"
  },
  "earliest_future_answerable_epoch": "FIRST_POST_2026-09-27_FROZEN_EPOCH_WITH_COMPLETE_NEW_CONTRACT",
  "gap_id": "STAGE4-DATA-L5",
  "historical_recoverability": "UNRECOVERABLE",
  "missing_observable": [
    "identity.canonical_opportunity_id | identity.evaluated_horizon | decision_snapshot.strategy | decision_snapshot.entry_time | simulated_outcome.pnl_r_multiple"
  ],
  "priority": "P1",
  "proposed_evidence_contract": "Persist the missing observable at its causal event time with canonical_opportunity_id/entity_id lineage and CURRENT epoch provenance.",
  "proposed_schema_change": {
    "add_required_observables": [
      "identity.canonical_opportunity_id | identity.evaluated_horizon | decision_snapshot.strategy | decision_snapshot.entry_time | simulated_outcome.pnl_r_multiple"
    ],
    "epoch_attestation": "CURRENT",
    "preserve_lineage": true
  },
  "rationale": "CURRENT artifact with status INSUFFICIENT_DATA",
  "reason_unrecoverable": "Frozen Gate 1 accounting is EXHAUSTED and Wave 4 resolves the required observable/relationship as unavailable or irrecoverably lossy.",
  "required_future_producer": [
    "producer:shadow_trades"
  ],
  "schema_evolution": "V2_OR_V3_REQUIRED"
}
```

## STAGE4-DATA-P1

```json
{
  "affected_question_ids": [
    "P1"
  ],
  "compatibility_considerations": "Additive V2/V3 field/event evolution; legacy rows remain historical and must not be backfilled or silently substituted.",
  "current_datasets": [
    "shadow_trades",
    "decision_trace"
  ],
  "current_schema_versions": {
    "decision_trace": "decision_trace_v1",
    "shadow_trades": "shadow_trades_v1"
  },
  "earliest_future_answerable_epoch": "FIRST_POST_2026-09-27_FROZEN_EPOCH_WITH_COMPLETE_NEW_CONTRACT",
  "gap_id": "STAGE4-DATA-P1",
  "historical_recoverability": "UNRECOVERABLE",
  "missing_observable": [
    "decision_snapshot.pattern",
    "simulated_outcome.pnl_r_multiple"
  ],
  "priority": "P0",
  "proposed_evidence_contract": "Persist the missing observable at its causal event time with canonical_opportunity_id/entity_id lineage and CURRENT epoch provenance.",
  "proposed_schema_change": {
    "add_required_observables": [
      "decision_snapshot.pattern",
      "simulated_outcome.pnl_r_multiple"
    ],
    "epoch_attestation": "CURRENT",
    "preserve_lineage": true
  },
  "rationale": "CURRENT artifact with status INSUFFICIENT_DATA",
  "reason_unrecoverable": "Frozen Gate 1 accounting is EXHAUSTED and Wave 4 resolves the required observable/relationship as unavailable or irrecoverably lossy.",
  "required_future_producer": [
    "producer:shadow_trades",
    "producer:decision_trace"
  ],
  "schema_evolution": "V2_OR_V3_REQUIRED"
}
```

## STAGE4-DATA-EX10

```json
{
  "affected_question_ids": [
    "EX10"
  ],
  "compatibility_considerations": "Additive V2/V3 field/event evolution; legacy rows remain historical and must not be backfilled or silently substituted.",
  "current_datasets": [
    "shadow_trades"
  ],
  "current_schema_versions": {
    "shadow_trades": "shadow_trades_v1"
  },
  "earliest_future_answerable_epoch": "FIRST_POST_2026-09-27_FROZEN_EPOCH_WITH_COMPLETE_NEW_CONTRACT",
  "gap_id": "STAGE4-DATA-EX10",
  "historical_recoverability": "UNRECOVERABLE",
  "missing_observable": [
    "canonical_opportunity_id",
    "entry_time"
  ],
  "priority": "P0",
  "proposed_evidence_contract": "Persist the missing observable at its causal event time with canonical_opportunity_id/entity_id lineage and CURRENT epoch provenance.",
  "proposed_schema_change": {
    "add_required_observables": [
      "canonical_opportunity_id",
      "entry_time"
    ],
    "epoch_attestation": "CURRENT",
    "preserve_lineage": true
  },
  "rationale": "Historical governed population has no analytically usable rows; absent/unresolved: entry_time",
  "reason_unrecoverable": "Frozen Gate 1 accounting is EXHAUSTED and Wave 4 resolves the required observable/relationship as unavailable or irrecoverably lossy.",
  "required_future_producer": [
    "producer:shadow_trades"
  ],
  "schema_evolution": "V2_OR_V3_REQUIRED"
}
```

## STAGE4-DATA-HORIZON-1

```json
{
  "affected_question_ids": [
    "HORIZON-1"
  ],
  "compatibility_considerations": "Additive V2/V3 field/event evolution; legacy rows remain historical and must not be backfilled or silently substituted.",
  "current_datasets": [
    "shadow_trades",
    "horizon_candidates"
  ],
  "current_schema_versions": {
    "horizon_candidates": "horizon_candidates_v1",
    "shadow_trades": "shadow_trades_v1"
  },
  "earliest_future_answerable_epoch": "FIRST_POST_2026-09-27_FROZEN_EPOCH_WITH_COMPLETE_NEW_CONTRACT",
  "gap_id": "STAGE4-DATA-HORIZON-1",
  "historical_recoverability": "UNRECOVERABLE",
  "missing_observable": [
    "identity.canonical_opportunity_id | identity.evaluated_horizon | identity.shadow_type | simulated_outcome.pnl_r_multiple",
    "canonical_opportunity_id | horizon | selection_status"
  ],
  "priority": "P1",
  "proposed_evidence_contract": "Persist the missing observable at its causal event time with canonical_opportunity_id/entity_id lineage and CURRENT epoch provenance.",
  "proposed_schema_change": {
    "add_required_observables": [
      "identity.canonical_opportunity_id | identity.evaluated_horizon | identity.shadow_type | simulated_outcome.pnl_r_multiple",
      "canonical_opportunity_id | horizon | selection_status"
    ],
    "epoch_attestation": "CURRENT",
    "preserve_lineage": true
  },
  "rationale": "CURRENT artifact with status INSUFFICIENT_DATA",
  "reason_unrecoverable": "Frozen Gate 1 accounting is EXHAUSTED and Wave 4 resolves the required observable/relationship as unavailable or irrecoverably lossy.",
  "required_future_producer": [
    "producer:shadow_trades",
    "producer:horizon_candidates"
  ],
  "schema_evolution": "V2_OR_V3_REQUIRED"
}
```

## STAGE4-DATA-STRAT-1

```json
{
  "affected_question_ids": [
    "STRAT-1"
  ],
  "compatibility_considerations": "Additive V2/V3 field/event evolution; legacy rows remain historical and must not be backfilled or silently substituted.",
  "current_datasets": [
    "strategy_candidates",
    "shadow_trades"
  ],
  "current_schema_versions": {
    "shadow_trades": "shadow_trades_v1",
    "strategy_candidates": "strategy_candidates_v1"
  },
  "earliest_future_answerable_epoch": "FIRST_POST_2026-09-27_FROZEN_EPOCH_WITH_COMPLETE_NEW_CONTRACT",
  "gap_id": "STAGE4-DATA-STRAT-1",
  "historical_recoverability": "UNRECOVERABLE",
  "missing_observable": [
    "confidence | rank | selected | canonical_opportunity_id",
    "simulated_outcome.pnl_r_multiple (PRIMARY_HORIZON_SIMULATION)"
  ],
  "priority": "P1",
  "proposed_evidence_contract": "Persist the missing observable at its causal event time with canonical_opportunity_id/entity_id lineage and CURRENT epoch provenance.",
  "proposed_schema_change": {
    "add_required_observables": [
      "confidence | rank | selected | canonical_opportunity_id",
      "simulated_outcome.pnl_r_multiple (PRIMARY_HORIZON_SIMULATION)"
    ],
    "epoch_attestation": "CURRENT",
    "preserve_lineage": true
  },
  "rationale": "CURRENT artifact with status INSUFFICIENT_DATA",
  "reason_unrecoverable": "Frozen Gate 1 accounting is EXHAUSTED and Wave 4 resolves the required observable/relationship as unavailable or irrecoverably lossy.",
  "required_future_producer": [
    "producer:strategy_candidates",
    "producer:shadow_trades"
  ],
  "schema_evolution": "V2_OR_V3_REQUIRED"
}
```

## STAGE4-DATA-X5

```json
{
  "affected_question_ids": [
    "X5"
  ],
  "compatibility_considerations": "Additive V2/V3 field/event evolution; legacy rows remain historical and must not be backfilled or silently substituted.",
  "current_datasets": [
    "decision_trace",
    "shadow_trades"
  ],
  "current_schema_versions": {
    "decision_trace": "decision_trace_v1",
    "shadow_trades": "shadow_trades_v1"
  },
  "earliest_future_answerable_epoch": "FIRST_POST_2026-09-27_FROZEN_EPOCH_WITH_COMPLETE_NEW_CONTRACT",
  "gap_id": "STAGE4-DATA-X5",
  "historical_recoverability": "UNRECOVERABLE",
  "missing_observable": [
    "p_success",
    "v10_entry",
    "simulated_outcome.pnl_r_multiple"
  ],
  "priority": "P1",
  "proposed_evidence_contract": "Persist the missing observable at its causal event time with canonical_opportunity_id/entity_id lineage and CURRENT epoch provenance.",
  "proposed_schema_change": {
    "add_required_observables": [
      "p_success",
      "v10_entry",
      "simulated_outcome.pnl_r_multiple"
    ],
    "epoch_attestation": "CURRENT",
    "preserve_lineage": true
  },
  "rationale": "CURRENT artifact with status INSUFFICIENT_DATA",
  "reason_unrecoverable": "Frozen Gate 1 accounting is EXHAUSTED and Wave 4 resolves the required observable/relationship as unavailable or irrecoverably lossy.",
  "required_future_producer": [
    "producer:decision_trace",
    "producer:shadow_trades"
  ],
  "schema_evolution": "V2_OR_V3_REQUIRED"
}
```

## STAGE4-DATA-PORT-1

```json
{
  "affected_question_ids": [
    "PORT-1"
  ],
  "compatibility_considerations": "Additive V2/V3 field/event evolution; legacy rows remain historical and must not be backfilled or silently substituted.",
  "current_datasets": [
    "portfolio_rankings",
    "shadow_trades"
  ],
  "current_schema_versions": {
    "portfolio_rankings": "portfolio_ranking_v1",
    "shadow_trades": "shadow_trades_v1"
  },
  "earliest_future_answerable_epoch": "FIRST_POST_2026-09-27_FROZEN_EPOCH_WITH_COMPLETE_NEW_CONTRACT",
  "gap_id": "STAGE4-DATA-PORT-1",
  "historical_recoverability": "UNRECOVERABLE",
  "missing_observable": [
    "candidates.selection_status",
    "candidates.rank_position",
    "simulated_outcome.pnl_r_multiple"
  ],
  "priority": "P1",
  "proposed_evidence_contract": "Persist the missing observable at its causal event time with canonical_opportunity_id/entity_id lineage and CURRENT epoch provenance.",
  "proposed_schema_change": {
    "add_required_observables": [
      "candidates.selection_status",
      "candidates.rank_position",
      "simulated_outcome.pnl_r_multiple"
    ],
    "epoch_attestation": "CURRENT",
    "preserve_lineage": true
  },
  "rationale": "CURRENT artifact with status INSUFFICIENT_DATA",
  "reason_unrecoverable": "Frozen Gate 1 accounting is EXHAUSTED and Wave 4 resolves the required observable/relationship as unavailable or irrecoverably lossy.",
  "required_future_producer": [
    "producer:portfolio_rankings",
    "producer:shadow_trades"
  ],
  "schema_evolution": "V2_OR_V3_REQUIRED"
}
```

## STAGE4-DATA-OPP-1

```json
{
  "affected_question_ids": [
    "OPP-1"
  ],
  "compatibility_considerations": "Additive V2/V3 field/event evolution; legacy rows remain historical and must not be backfilled or silently substituted.",
  "current_datasets": [
    "shadow_trades",
    "horizon_candidates"
  ],
  "current_schema_versions": {
    "horizon_candidates": "horizon_candidates_v1",
    "shadow_trades": "shadow_trades_v1"
  },
  "earliest_future_answerable_epoch": "FIRST_POST_2026-09-27_FROZEN_EPOCH_WITH_COMPLETE_NEW_CONTRACT",
  "gap_id": "STAGE4-DATA-OPP-1",
  "historical_recoverability": "UNRECOVERABLE",
  "missing_observable": [
    "opportunity_id",
    "state",
    "overall_score"
  ],
  "priority": "P1",
  "proposed_evidence_contract": "Persist the missing observable at its causal event time with canonical_opportunity_id/entity_id lineage and CURRENT epoch provenance.",
  "proposed_schema_change": {
    "add_required_observables": [
      "opportunity_id",
      "state",
      "overall_score"
    ],
    "epoch_attestation": "CURRENT",
    "preserve_lineage": true
  },
  "rationale": "CURRENT artifact with status BLOCKED",
  "reason_unrecoverable": "Frozen Gate 1 accounting is EXHAUSTED and Wave 4 resolves the required observable/relationship as unavailable or irrecoverably lossy.",
  "required_future_producer": [
    "producer:shadow_trades",
    "producer:horizon_candidates"
  ],
  "schema_evolution": "V2_OR_V3_REQUIRED"
}
```
