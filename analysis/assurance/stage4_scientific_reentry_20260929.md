# Stage 4 governed scientific re-entry (20260929)

- store: `6f6b74cbfb648ebcdf748368aad8a4402f95532973252ec758989f7e0dd2e120`
- effective state: `bc9770b1132397d28e0a68b1b72db7f86e7080b8788220fd7991a4e6fc58f6de`
- questions: 70
- version counts: {'1': 64, '2': 6}
- baseline certification: `b42b4bfcfa1eaf7df09cac9f88011c89362cf2ca907217e8466fff9fea3adc8b`

## Triggers

- IMPLEMENTATION_REPAIR
- DATA_THRESHOLD_REACHED
- SCHEMA_COLLECTION_RECOVERED
- DEPENDENCY_RESOLVED
- GOVERNED_METHOD_REPAIR
- NEW_EVIDENCE_EPOCH

## Dependencies

- L6 -> G3 (gap_governance:GWI-L6-IMPL->GWI-G3-IMPL)

## Pre-existing baseline drift

PRE_EXISTING_BASELINE_DRIFT: operational-baseline assertions expect (60,10)/(58,12) while the repository currently returns (63,7); reproduced on a clean checkout before this task and deliberately not repaired here.

