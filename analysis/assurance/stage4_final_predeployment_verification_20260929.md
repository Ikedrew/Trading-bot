# Stage 4 — Final Pre-Deployment Verification (2026-09-29)

Verdict: **DEPLOYMENT READY** — ready to commit. Nothing committed or deployed by this pass.

## Critical finding

`core/observability_contract.py` is **untracked**. HEAD has no such file, so a fresh
checkout of HEAD alone cannot import the runtime guard at all. It and
`tests/test_shadow_runtime_serialization_guard.py` must be in the next commit.

`core/shadow/observability.py` is only usable together with
`core/observability_contract.py` — the guard imports `SHADOW_RUNTIME_GEN2_FIELDS` from it.

## Fresh-checkout simulation

`git archive HEAD` → isolated tree → overlay only the intended commit files →
`ShadowRuntime()` + full lifecycle. All 24 smoke steps passed, 87 tests passed,
0 suspect imports, 0 hidden local dependencies.

Observed gen-2 metadata: `schema_generation=2`,
`producer_version=shadow_runtime_producer_v2`,
`evidence_epoch=STAGE4-EPOCH-SHADOW-RUNTIME-G2`, `schema_version=shadow_runtime_v1`.

`FRESH_CHECKOUT_DEPENDENCY_ON_UNCOMMITTED_LOCAL_FILE = 0`

## Test adjudication

- `summary` → governed key is **`completeness`**. Test stale; producer correct.
- `assignment_decision_record` → not in `L7_REQUIRED_CONTRACT_FIELDS` and not in any
  governed audit artifact. The governed structure is `arm_pre_outcome_attestation`.
  Test stale; producer correct.
- `EXPERIMENT_ARM_NOT_ISSUED_AT_ASSIGNMENT` does not exist in production code.
- `ARM_ISSUER` in `core/shadow/observability.py` is dead code (left unchanged).

No producer code was modified. 60/60 observability tests now pass.

## Baseline exclusions (proven at clean HEAD)

- `test_shadow_counterfactual_population::test_watermark_exactly_once_preserved`
- `test_phase3_capture_contract::TestNegativeCases::test_case_f_shadow_gate_off_in_source_and_config`
  (caused by `core/config.py:98`, `SHADOW_RUNTIME_V2_ENABLED = True`)

## VM requirements

All **NO**: new env vars, config change, IAM change, dependency install, S3 path
change, migration, new directories, state reset.

Full detail: `stage4_final_predeployment_verification_20260929.json`
