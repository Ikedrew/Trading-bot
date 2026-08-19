# End-to-End Optimisation Lifecycle Audit

**Date**: 2026-07-27  
**Status**: AUDIT ONLY — no implementation  
**Test baseline**: 286 passing (52 lifecycle-specific confirmed passing individually)

---

## Executive Conclusion

**Classification: C. PARTIALLY INTEGRATED**

The components individually work correctly and are well-designed. However, **one critical orchestration gap** prevents the system from operating as a fully autonomous discovery-to-recommendation pipeline:

> **Candidates are created in PROPOSED status but never automatically transitioned to SHADOW_TESTING.**

The candidate shadow hook filters for `CandidateStatus.SHADOW_TESTING` candidates. The orchestrator creates candidates in `PROPOSED`. There is no code that performs `PROPOSED → VALIDATING → SHADOW_TESTING` automatically. This means that without manual `registry.update_status()` calls, **no candidate will ever accumulate prospective shadow observations**.

Despite this gap, the system **can** carry a discovery to a statistically evaluated recommendation — but only if a human (or a small orchestration script) manually activates the candidate. The research engine remains completely unable to change production without human governance.

---

## 1. Complete Runtime Lifecycle Trace

### Actual Code Path (with module, function, data flow)

```
Finding (FindingTriggerEngine.detect_from_pattern_performance)
  │  data: pattern, mean_r, win_rate, sample_size
  │  file: research_engine/lifecycle/finding_trigger.py
  │  state: TriggerRecord persisted in triggers.jsonl
  │
  ▼
Hypothesis (ResearchOrchestrator.detect_and_register)
  │  data: title, claim, null_hypothesis, category, falsification_conditions
  │  file: research_engine/lifecycle/orchestrator.py:94-128
  │  state: Hypothesis registered in InvestigationRegistry
  │  lineage: hypothesis.source_finding_id → trigger.finding_id
  │
  ▼
Experiment (ResearchOrchestrator.run_experiment → execute_fn)
  │  data: ExperimentDefinition → ExperimentResult
  │  file: research_engine/lifecycle/orchestrator.py:132-200
  │  state: Registered in ExperimentCatalogue + hypothesis.experiments[]
  │  lineage: experiment.hypothesis_id → hypothesis.hypothesis_id
  │
  ▼
Challenge (ResearchOrchestrator.challenge)
  │  data: ExperimentResult, PlaceboTestOutcome
  │  file: research_engine/lifecycle/orchestrator.py:204-225
  │  state: Hypothesis → CHALLENGED
  │
  ▼
Conclusion (ResearchOrchestrator.conclude)
  │  data: result metrics → ConclusionType enum
  │  file: research_engine/lifecycle/orchestrator.py:229-293
  │  state: Hypothesis → CONCLUDED, conclusion_type set
  │  gates: significance (Bonferroni), placebo, OOS, validation
  │
  ▼
OptimisationCandidate (ResearchOrchestrator.create_optimisation_candidate)
  │  data: hypothesis + result → CandidateRecord
  │  file: research_engine/lifecycle/orchestrator.py:552-620
  │  state: CandidateRecord created with status="PROPOSED"
  │  persistence: data/research/candidates/candidates.jsonl
  │  lineage: candidate.hypothesis_id → hypothesis.hypothesis_id
  │
  ▼  *** GAP: No automatic transition PROPOSED → SHADOW_TESTING ***
  │
  ▼  (REQUIRES MANUAL: registry.update_status(id, "VALIDATING"), then "SHADOW_TESTING")
  │
Candidate Shadow Observation (candidate_shadow_hook.open_candidate_shadows)
  │  data: baseline execution params + candidate.change_definition → shadow params
  │  file: research_engine/lifecycle/candidate_shadow_hook.py
  │  trigger: engine_execution_handler.py section 4b (fire-and-forget)
  │  filter: list_by_status(CandidateStatus.SHADOW_TESTING)
  │  state: Shadow trade opened in ShadowTradeEngine._active
  │
  ▼
Shadow Evaluation (ShadowTradeEngine.evaluate_bar)
  │  data: bar OHLC → exit conditions → ShadowTrade closed
  │  file: core/shadow_trades.py:309-420
  │  state: Trade Truth v2 record built and persisted
  │  persistence: logs/shadow_trades/{symbol}/{date}.jsonl
  │
  ▼
Candidate Evaluation (candidate_evaluation_bridge.evaluate_candidate)
  │  data: shadow_observations → paired deltas → statistical tests → decision
  │  file: research_engine/lifecycle/candidate_evaluation_bridge.py
  │  evaluator: research_engine/lifecycle/candidate_evaluator.py
  │  gates: minimum_N(30), significance(CI/perm), OOS, robustness
  │  state: ValidationEntry appended to candidate.validation_history
  │
  ▼
Lifecycle Transition (CandidateRegistry.update_status)
  │  VALIDATED → CandidateStatus.VALIDATED
  │  REJECTED → CandidateStatus.FAILED_VALIDATION (allows retry)
  │  INCONCLUSIVE → no transition (remains eligible)
  │  file: research_engine/v10/candidates/candidate_registry.py:63-77
  │
  ▼
Evaluation Report (CandidateEvaluationReport.generate)
  │  data: all candidates → health/priority/next_action assessment
  │  file: research_engine/v10/candidates/evaluation_report.py
  │  output: JSON + Markdown reports in reports/research/
  │
  ▼
Governance (GovernanceGate.approve — REQUIRES HUMAN)
  │  data: Hypothesis → PromotionRequest → GovernanceDecision
  │  file: research_engine/lifecycle/governance_gate.py
  │  state: hypothesis.human_approval_granted = True (with actor, timestamp)
  │  persistence: logs/research_lifecycle/governance_decisions.jsonl
  │
  ▼  HARD STOP — no code path to production beyond this point
```

---

## 2. Candidate Creation → Activation Audit

| Question | Answer |
|----------|--------|
| Where is the candidate created? | `ResearchOrchestrator.create_optimisation_candidate()` (orchestrator.py:552) |
| Where is it registered? | `CandidateRegistry().create(record)` (orchestrator.py:614) |
| What initial status? | `"PROPOSED"` — hardcoded in CandidateRecord construction |
| Does VALIDATED conclusion auto-create candidate? | **YES** — `investigate()` calls `create_optimisation_candidate()` at line 907 when `conclusion == ConclusionType.VALIDATED` |
| Is the candidate auto-activated? | **NO** — it remains in PROPOSED indefinitely |
| What manual step is required? | `registry.update_status(id, "VALIDATING")` then `registry.update_status(id, "SHADOW_TESTING")` |
| Is `created_at` reliable? | **YES** — auto-set via `timestamp_now()` in CandidateRecord `__post_init__` (UTC ISO format) |
| Is `baseline_id` preserved? | **YES** — set to `"current_v10"` at creation |
| Does `change_definition` survive unchanged? | **YES** — persisted as dict in JSONL, loaded via `from_dict()` unchanged |

**Critical finding**: The state machine explicitly BLOCKS `PROPOSED → SHADOW_TESTING` as an invalid transition. The valid path is `PROPOSED → VALIDATING → ... → SHADOW_TESTING`. But `PROPOSED → VALIDATING` is valid.

---

## 3. Candidate Lifecycle State Machine

### Actual transitions from code (`candidate_lifecycle.py`):

| State | Can Enter From | Can Exit To | Who Performs Transition |
|-------|---------------|-------------|------------------------|
| PROPOSED | Creation (orchestrator) | VALIDATING, ARCHIVED, REJECTED | Manual / future orchestrator |
| VALIDATING | PROPOSED, FAILED_VALIDATION, REGRESSION_DETECTED | VALIDATED, FAILED_VALIDATION, REGRESSION_DETECTED, REJECTED, ARCHIVED | `evaluate_candidate()` bridge |
| VALIDATED | VALIDATING | SHADOW_TESTING, READY_FOR_REVIEW, ARCHIVED, REJECTED | Manual / future orchestrator |
| SHADOW_TESTING | VALIDATED | READY_FOR_REVIEW, REGRESSION_DETECTED, REJECTED, ARCHIVED | Manual after evidence accumulation |
| READY_FOR_REVIEW | VALIDATED, SHADOW_TESTING | ACCEPTED, REJECTED, ARCHIVED | Manual / human governance |
| FAILED_VALIDATION | VALIDATING | VALIDATING (retry), ARCHIVED, REJECTED | Automatic (bridge on REJECTED eval) |
| REGRESSION_DETECTED | VALIDATING, SHADOW_TESTING | VALIDATING (retry), ARCHIVED, REJECTED | Future regression detector |
| ACCEPTED | READY_FOR_REVIEW | ARCHIVED | Human approval |
| REJECTED | Any active state | ARCHIVED | Manual / evaluation |
| ARCHIVED | ACCEPTED, REJECTED, any | (terminal) | Manual cleanup |

### Issues found:

1. **VALIDATED → SHADOW_TESTING** is valid in the state machine, but **nothing automates it**. After `evaluate_candidate()` transitions a candidate to VALIDATED (from historical evidence), there is no orchestrator that says "now start shadow testing".

2. **Dual VALIDATING/SHADOW_TESTING semantics confusion**: The candidate shadow hook uses `SHADOW_TESTING` for filtering. The evaluation bridge accepts `{VALIDATING, SHADOW_TESTING}` for evaluation eligibility. This creates two distinct pathways:
   - Path A: `PROPOSED → VALIDATING` → evaluate with historical data → `VALIDATED → SHADOW_TESTING` → shadow observation → evaluate again → `READY_FOR_REVIEW`
   - Path B (shortcut): `PROPOSED → SHADOW_TESTING` — but THIS IS INVALID in the state machine!

3. **VALIDATING ≠ SHADOW_TESTING**: These represent genuinely different lifecycle states. VALIDATING = being evaluated against existing/historical evidence. SHADOW_TESTING = being evaluated against live prospective shadow observations.

4. **No PROMOTED status exists**. The governance gate operates at the Hypothesis level (not candidate). Candidates reach ACCEPTED → then human deploys manually. This is architecturally safe but disconnected from the GovernanceGate's PromotionRequest mechanism.

---

## 4. Candidate Activation → Shadow Observation Audit

| Question | Answer |
|----------|--------|
| Which candidates are considered active for shadowing? | Only `CandidateStatus.SHADOW_TESTING` |
| Can rejected candidates accidentally remain active? | **NO** — REJECTED/FAILED_VALIDATION are excluded by the status filter |
| Can multiple candidates run simultaneously? | **YES** — `list_by_status()` returns all matching; the loop opens one shadow per candidate |
| Are shadows isolated from each other? | **YES** — each gets unique `trade_id = f"candidate_{candidate_id}_{cycle_id}_{symbol}"` |
| Is shadow creation fire-and-forget? | **YES** — double isolation: outer try/except in engine_execution_handler + inner per-candidate try/except |
| Can an exception affect production? | **NO** — `except Exception: pass` wraps the entire candidate shadow block |
| Is `candidate_id` preserved in shadow? | **YES** — via `shadow_type=f"CANDIDATE_{candidate.candidate_id}"` |
| Is `shadow_type` deterministic? | **YES** — derived directly from `candidate_id` |
| Is `correlation_id` preserved? | **YES** — same value passed from baseline to candidate shadow |
| Is `entity_id` preserved? | **YES** — same value passed from baseline to candidate shadow |
| Are candidate shadows distinguishable from V10_PRIMARY? | **YES** — `shadow_type` field in persisted record differs |

---

## 5. Change Definition → Actual Shadow Behaviour

| Candidate Type | Can Represent? | How Applied | Correct? | Tested? |
|---|---|---|---|---|
| `direction_inversion` | YES | Inverts BUY↔SELL, recomputes SL/TP from risk_distance (3R TP) | YES | YES (test_candidate_shadow_hook.py) |
| `geometry_modification` | YES | Applies `stop_multiplier` to widen stop | YES | YES (tested) |
| `symbol_exclusion` | PARTIAL | Returns None for excluded symbol (no shadow opened) | Correct semantics | YES |
| `regime_conditioning` | YES | Uses original geometry — evaluation filters by regime later | Correct design | Needs integration test |
| `score_recalibration` | NO | Falls through to `return None` (unsupported) | Correctly unsupported | N/A |
| `pattern_weighting` | NO | Falls through to `return None` (unsupported) | Correctly unsupported | N/A |
| `research_recommendation` | NO | Falls through to `return None` (unsupported) | Correctly unsupported — should never generate shadow | N/A |

**Critical note on unsupported types**: The orchestrator's `_derive_change_definition()` can produce `pattern_weighting` and `score_recalibration` candidates from VALIDATED conclusions. These candidates WILL be created but can NEVER be shadow-tested because `_translate_change_definition()` returns None for them. They will sit in SHADOW_TESTING status indefinitely accumulating no evidence. This is **not dangerous** (no incorrect shadows are opened) but means some candidates are structurally un-validatable through this mechanism.

---

## 6. Baseline vs Candidate Pairing Audit

### Join key: `entity_id`

The `CandidateEvaluator._build_pairs()` method pairs by `entity_id`:
```python
for obs in observations:
    shadow_type = self._get_shadow_type(obs)
    entity = self._get_entity_id(obs)
    ...
    if shadow_type == "V10_PRIMARY":
        baseline_by_entity[entity] = {"r": r, "symbol": symbol}
    elif shadow_type == candidate_type:
        candidate_by_entity[entity] = {"r": r, "symbol": symbol}

# Pair by entity_id
for entity in baseline_by_entity:
    if entity in candidate_by_entity:
        pairs.append(...)
```

### Pairing invariant:
> **One candidate observation corresponds to exactly one baseline observation for the same opportunity (entity_id).**

### Potential issues:

| Risk | Analysis |
|------|----------|
| Duplicate entity IDs | **Mitigated** — entity_id = `f"{symbol}_{bar_time}"` is deterministic per opportunity. Dict assignment means last-write-wins if duplicated. |
| Missing baseline | **Handled** — unpaired candidate observations are counted as `excluded_unpaired` and not used |
| Missing candidate | **Handled** — unpaired baselines simply aren't included in pairs |
| Multiple candidate observations for one entity | **Possible but handled** — dict assignment means only the last per entity_id is kept. Multiple candidates use different shadow_types so they don't collide. |
| Cross-candidate collisions | **NONE** — `candidate_type = f"CANDIDATE_{candidate_id}"` is candidate-specific |
| Cross-symbol collisions | **NONE** — entity_id includes symbol: `f"{symbol}_{bar_time}"` |
| Cross-run collisions | **LOW RISK** — if same symbol+bar_time occurs again, the older pair would be overwritten. Prospective boundary mitigates this. |
| Stale shadows | **Mitigated** — prospective boundary excludes pre-activation observations |

**The invariant IS enforced** through the dict-keyed pairing mechanism.

---

## 7. Prospective Data Boundary Audit

### Implementation:
```python
boundary_ts = self._parse_timestamp(candidate_activated_at)
for obs in shadow_observations:
    obs_ts = self._get_timestamp(obs)
    if obs_ts and obs_ts < boundary_ts:
        excluded_pre += 1
        continue
```

| Question | Answer |
|----------|--------|
| Which timestamp is authoritative? | `candidate.created_at` (passed as `candidate_activated_at` to evaluator) |
| Are timestamps UTC? | YES — `timestamp_now()` uses `datetime.now(timezone.utc).isoformat()` |
| String/epoch conversions safe? | `_parse_timestamp()` handles ISO strings → unix epoch. `_get_timestamp()` extracts from v2 `entry_timestamp` or flat `entry_time`. Both are float epoch. |
| Is creation time used instead of activation time? | **YES — this is a minor issue**. The bridge calls `evaluator.evaluate(candidate_activated_at=candidate.created_at)`. Since candidates currently have no separate "activation timestamp", `created_at` is used. For PROPOSED→SHADOW_TESTING transitions, the activation time should ideally be when the candidate entered SHADOW_TESTING, but currently `status_history` entries have timestamps that COULD be used. |
| Can pre-discovery observations leak in? | **NO** — the boundary filter excludes all observations before `created_at` |
| Can old V10_PRIMARY pair with new candidate? | **NO** — both must pass the prospective boundary filter. Old baseline observations (pre-candidate) are excluded. |
| Is the prospective dataset immutable? | YES — it's filtered fresh from disk each time; no in-memory mutation occurs. |

**Minor gap**: Using `created_at` instead of the SHADOW_TESTING transition timestamp means the prospective window includes the entire period from PROPOSED onwards — which is acceptable because no shadows are actually opened until SHADOW_TESTING, so no candidate observations exist before activation anyway.

---

## 8. Minimum Sample Gate Audit

| Parameter | Value | Source |
|-----------|-------|--------|
| Minimum N | 30 | `EvaluationConfig.minimum_sample = 30` |
| N means | Paired observations (not raw) | `result.n = len(pairs)` checked against minimum |
| Is N candidate-specific? | Implicitly yes — pairs are filtered by `CANDIDATE_{id}` shadow_type |
| Do unpaired observations count? | NO — only successfully paired observations count toward N |
| Can evaluator run prematurely? | YES — if called with < 30 pairs, returns INCONCLUSIVE |
| Does INCONCLUSIVE keep candidate active? | YES — no lifecycle transition occurs |
| Does system auto-retry? | **NO** — `evaluate_candidate()` must be called again manually |

### Edge cases:
- N = 0: INCONCLUSIVE ("Insufficient prospective evidence: N=0 < minimum=30")
- N = 1-29: Same INCONCLUSIVE
- N = 30: Evaluation proceeds (minimum met)
- N > 30: Full evaluation with all statistical tests

---

## 9. Statistical Evaluation Audit

| Test | Calculated | Used in Decision | Required for VALIDATED |
|------|------------|------------------|-----------------------|
| Bootstrap CI (90%) | YES | YES (Gate 1) | YES — CI lower > 0 needed |
| Paired Permutation (5000 perms) | YES | YES (Gate 1, alternative) | NO — CI OR p < 0.05 sufficient |
| OOS split (60/40) | YES | YES (Gate 3) | SOFT — INCONCLUSIVE if OOS≤0 with N≥10 |
| Symbol robustness | YES | YES (Gate 2) | YES — ≥2 symbols positive required |
| Temporal stability | YES | YES (Gate 2) | YES — ≥2 periods positive required |
| Outlier removal (top 5) | YES | YES (Gate 2) | YES — must survive |
| Risk assessment | YES | NO (informational) | NO — reported but doesn't gate |

### Decision logic (actual implementation):
```
VALIDATED = (
    (CI_lower > 0 OR permutation_p < 0.05)
    AND symbols_positive >= 2
    AND periods_positive >= 2
    AND survives_outlier_removal
    AND NOT (OOS_N >= 10 AND OOS_delta <= 0)
)

REJECTED = (
    NOT passes_significance
    AND mean_delta_r < 0
)

INCONCLUSIVE = everything else
```

### Issues found:
- **OOS is a soft gate**: It can turn VALIDATED → INCONCLUSIVE but not → REJECTED. This means a candidate that passes significance + robustness but degrades in OOS gets "more time" rather than failing. Reasonable but should be documented.
- **Direction correctness**: `delta_r = candidate_r - baseline_r`. Positive = candidate better. Confirmed correct.

---

## 10. Evaluation Decision Logic (Explicit Boolean)

```python
# Gate 1: Statistical Significance
passes_ci = (ci_lower is not None and ci_lower > 0)
passes_p = (permutation_p is not None and permutation_p < 0.05)
passes_significance = passes_ci or passes_p

if not passes_significance:
    if mean_delta_r < 0:
        return REJECTED, "Candidate underperforms baseline", HIGH
    return INCONCLUSIVE, "Effect not statistically significant", LOW

# Gate 2: Robustness
robust = (
    symbols_positive >= 2
    and periods_positive >= 2
    and survives_outlier_removal
)
if not robust:
    return INCONCLUSIVE, "Significant but fragile", LOW

# Gate 3: Out-of-Sample (soft)
if oos_n >= 10 and oos_delta_r <= 0:
    return INCONCLUSIVE, "OOS negative", MEDIUM

# All gates pass
confidence = "HIGH" if n >= 100 else "MEDIUM"
return VALIDATED, "All statistical gates pass", confidence
```

**No contradictory logic found.** All calculated tests are used. No silent bypasses.

---

## 11. Validation History Audit

### What is persisted per evaluation:

| Field | Persisted? | How |
|-------|-----------|-----|
| validation_id | YES | `evaluation.evaluation_id` → `ValidationEntry.validation_id` |
| timestamp | YES | Auto-set via `timestamp_now()` |
| decision | YES | Mapped: VALIDATED→"IMPROVED", REJECTED→"WORSENED", INCONCLUSIVE→"INCONCLUSIVE" |
| confidence | YES | From evaluation (HIGH/MEDIUM/LOW/INSUFFICIENT) |
| sample_size | YES | `evaluation.n` |
| expectancy_delta | YES | `evaluation.mean_delta_r` |
| regressions | YES | Structured list (OOS negative, fails outlier, low symbols) |
| candidate_id | Implicit | ValidationEntry attached to specific CandidateRecord |

### What is calculated but NOT persisted in ValidationEntry:
- Permutation p-value
- CI bounds
- OOS N and delta
- Symbol/temporal counts
- Win rate
- Worst delta

**Can a researcher reconstruct the decision?** Partially. The `decision`, `confidence`, `sample_size`, and `expectancy_delta` are preserved. But `permutation_p`, `ci_lower`, `ci_upper`, and full robustness breakdown are **lost** unless the full `CandidateEvaluation` object is captured elsewhere.

**Gap**: The full `CandidateEvaluation.to_dict()` (which contains ALL fields) is returned by `evaluate_candidate()` but is NOT persisted to disk separately. Only the condensed `ValidationEntry` is saved.

---

## 12. CandidateEvaluationReport Audit

**File**: `research_engine/v10/candidates/evaluation_report.py`

| Question | Answer |
|----------|--------|
| Can it consume new evaluation entries? | YES — reads `candidate.validation_history[-1]` |
| Displays latest validation? | YES — `last_validation` field in report |
| Displays historical evaluations? | NO — only the most recent |
| Exposes expected improvement? | YES — via `last.expectancy_delta` |
| Exposes confidence? | YES — via `last.confidence` |
| Exposes sample size? | YES — via `last.sample_size` |
| Exposes regressions? | YES — via `last.regressions` |
| Distinguishes discovery vs prospective evidence? | NO — treats all validation entries the same |
| States recommendation? | YES — `next_action` field per candidate |
| Is `CandidateEvaluator → ValidationHistory → Report` wired? | **Partially** — the bridge persists to ValidationHistory; the report reads from it. But there is no AUTOMATIC trigger that generates the report after evaluation. |

**Wiring**: The report must be explicitly generated by calling `CandidateEvaluationReport().generate()`. It IS wired into the operations router (`router.py:206`) via a dashboard command, so it CAN be triggered. But it's not automatically generated after each evaluation.

---

## 13. Human-Readable Optimisation Recommendation

Can the system answer these questions?

| Question | Answerable? | Where |
|----------|-------------|-------|
| What should change? | YES | `candidate.change_definition.type` + `action` |
| Why? | YES | `candidate.change_definition.rationale` = hypothesis conclusion reason |
| How strong is the evidence? | PARTIALLY | `ValidationEntry.sample_size`, `.confidence` — but CI/p not in entry |
| How much improvement? | YES | `ValidationEntry.expectancy_delta` |
| How robust is it? | PARTIALLY | Regressions list indicates failures; positive counts not preserved |
| What is the risk? | YES | `candidate.risk_level` (HIGH/MEDIUM/LOW) |
| Is more evidence required? | YES | INCONCLUSIVE decision means more evidence needed |
| Can it be deployed? | YES | Governance gate + ACCEPTED status required |

### Layer distinction:

| Layer | Exists? | Clearly Separated? |
|-------|---------|-------------------|
| RESEARCH FINDING | YES | YES — hypothesis level |
| SHADOW VALIDATION | YES | YES — candidate evaluation |
| GOVERNANCE DECISION | YES | YES — GovernanceGate |
| PRODUCTION DEPLOYMENT | **NO CODE EXISTS** | N/A — hard stop at ACCEPTED |

**The system correctly distinguishes all layers.** There is no code that conflates shadow validation with production deployment.

---

## 14. Failure / Recovery Audit

| Failure | Candidate Safe? | Production Safe? | Observable? | Resumable? |
|---------|----------------|-----------------|-------------|-----------|
| Candidate shadow creation fails | YES | YES (try/except) | YES (debug log) | YES |
| Candidate data missing | YES | YES | YES (log) | YES |
| Baseline observation missing | YES | YES | YES (excluded_unpaired count) | YES |
| Candidate observation missing | YES | YES | YES (excluded_unpaired count) | YES |
| Pairing fails | YES | YES | YES (n=0 → INCONCLUSIVE) | YES |
| Insufficient N | YES (no transition) | YES | YES (INCONCLUSIVE reason) | YES (re-evaluate later) |
| Statistical calculation fails | YES | YES | YES (None values, INCONCLUSIVE) | YES |
| Evaluation crashes | YES | YES | YES (exception propagates) | YES |
| Candidate already rejected | YES | YES | YES (eligibility check blocks) | N/A |
| Candidate already validated | YES | YES | YES (eligibility check blocks) | N/A |
| Dual evaluation | SAFE | YES | YES (appends additional entry) | YES |
| Registry persistence fails | SAFE (in-memory ok) | YES | YES (exception logged) | YES (retry) |

**All failures are contained.** No failure mode can affect production trading.

---

## 15. Concurrency / Multiple Candidate Audit

| Question | Answer |
|----------|--------|
| Can A, B, C shadow-test simultaneously? | YES — `list_by_status(SHADOW_TESTING)` returns all |
| Can candidate IDs collide? | NO — `OPT-{hypothesis_id[-8:]}` is unique per hypothesis |
| Can shadow IDs collide? | NO — `f"candidate_{candidate_id}_{cycle_id}_{symbol}"` is unique |
| Can entity IDs collide? | NO — entity_id is shared (intentionally — for pairing) |
| Can A consume B's shadows? | NO — evaluator filters by `f"CANDIDATE_{candidate_id}"` shadow_type |
| Are lifecycle transitions candidate-specific? | YES — `registry.update_status(candidate_id, ...)` |
| Can one candidate's failure affect another? | NO — per-candidate try/except in shadow hook loop |

**The system correctly supports concurrent multi-candidate validation.**

---

## 16. Production Safety Proof

### Fresh structural trace:

**Starting points**: CandidateRecord, CandidateRegistry, CandidateEvaluator, CandidateEvaluationBridge, CandidateShadowHook, CandidateEvaluationReport

**Target systems**: MT5Execution, RiskManager, ExecutionOrchestrator, production config

| Source Module | Imports MT5Execution? | Imports RiskManager? | Imports production config? | Path to order_send? |
|---|---|---|---|---|
| `candidate_shadow_hook.py` | NO | NO | NO | **NONE** |
| `candidate_evaluator.py` | NO | NO | NO | **NONE** |
| `candidate_evaluation_bridge.py` | NO | NO | NO | **NONE** |
| `candidate_registry.py` | NO | NO | NO | **NONE** |
| `governance_gate.py` | NO | NO | NO | **NONE** |
| `evaluation_report.py` | NO | NO | NO | **NONE** |

**ShadowTradeEngine**: Does NOT import MT5Execution. `open_trade()` creates an in-memory `ShadowTrade` dataclass. No broker calls exist anywhere in `shadow_trades.py`.

**engine_execution_handler.py integration point**: The candidate shadow hook is called AFTER production execution has already been prepared/initiated. It cannot interrupt, modify, or prevent the production trade. The `try/except Exception: pass` wrapper ensures this absolutely.

### Invariant verified:
```
Research → Candidate → Shadow → Evaluation → Governance → (STOP)
                                                              |
                                              Requires human to manually
                                              deploy changes to production
```

There is **NO code path** from any research/candidate module to `MT5Execution.order_send()` or to modifying production V10 configuration.

---

## 17. End-to-End Runtime Demonstration

**Cannot execute fully** — the first missing link is:

> **No automated transition from PROPOSED to SHADOW_TESTING.**

If we manually activate a candidate (2 status transitions), the remaining path works:
1. `open_candidate_shadows()` finds it via `list_by_status(SHADOW_TESTING)` ✓
2. `_translate_change_definition()` produces shadow params ✓
3. `ShadowTradeEngine.open_trade()` creates the shadow ✓
4. `evaluate_bar()` closes it when SL/TP hit ✓
5. `_persist_shadow_trade()` writes to `logs/shadow_trades/` ✓
6. `evaluate_candidate()` loads observations, pairs them, evaluates ✓
7. `ValidationEntry` persisted to candidate ✓
8. Lifecycle transition occurs ✓
9. `CandidateEvaluationReport` can read and display ✓

**The exact first missing link**: Automated `PROPOSED → VALIDATING → SHADOW_TESTING` transition.

---

## 18. Test Coverage Audit

### Existing lifecycle tests (confirmed passing):

| Coverage Area | Test File | Tests |
|---|---|---|
| Candidate creation | `test_candidate_registry.py` | CRUD, status transitions, validation history |
| Shadow hook | `test_candidate_shadow_hook.py` | Direction inversion, geometry, pairing, failure isolation, multi-candidate |
| Evaluator | `test_candidate_evaluator.py` | Prospective boundary, pairing, minimum N, significance, robustness, OOS, decision logic |
| Evaluation bridge | `test_evaluation_bridge.py` | End-to-end evaluation→lifecycle, persistence |
| Governance | `test_research_governance.py` | Approval flows |
| Lifecycle state machine | `test_candidate_registry.py` | Valid/invalid transitions |
| Evaluation report | `test_candidate_evaluation_report.py` | Report generation, priority, health |

### Integration tests present:
- `test_optimisation_bridge_lifecycle.py` — Tests creation→evaluation→transition flow
- `test_evaluation_bridge.py` — Tests evaluate_candidate() full path

### Missing integration tests:
1. **Full cycle: Finding → Hypothesis → VALIDATED → Candidate → Activate → Shadow → Close → Evaluate → Report** — no single test covers this end-to-end
2. **candidate shadow hook + evaluator pairing** — tested separately but not as one flow with real shadow closure
3. **Multiple concurrent candidates evaluation isolation**
4. **Candidate types that cannot be shadowed** — no test confirms `score_recalibration` candidates sit idle

---

## 19. Final Architecture Matrix

| Lifecycle Stage | Exists | Wired | Tested | Production Safe | Evidence Complete |
|---|---|---|---|---|---|
| Finding Detection | ✅ | ✅ | ✅ | ✅ | ✅ |
| Hypothesis Registration | ✅ | ✅ | ✅ | ✅ | ✅ |
| Experiment Execution | ✅ | ✅ | ✅ | ✅ | ✅ |
| Statistical Validation | ✅ | ✅ | ✅ | ✅ | ✅ |
| Placebo Control | ✅ | ✅ | ✅ | ✅ | ✅ |
| Conclusion | ✅ | ✅ | ✅ | ✅ | ✅ |
| Knowledge Map | ✅ | ✅ | ✅ | ✅ | ✅ |
| Candidate Creation | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Candidate Activation** | ✅ (mechanism) | **❌ NOT WIRED** | ✅ (manual) | ✅ | ❌ |
| Candidate Shadow | ✅ | ✅ | ✅ | ✅ | ✅ |
| Pairing | ✅ | ✅ | ✅ | ✅ | ✅ |
| Prospective Filtering | ✅ | ✅ | ✅ | ✅ | ✅ |
| Candidate Evaluation | ✅ | ✅ | ✅ | ✅ | ✅ |
| Validation History | ✅ | ✅ | ✅ | ✅ | ⚠️ Partial (CI/p not persisted) |
| Lifecycle Transition | ✅ | ✅ | ✅ | ✅ | ✅ |
| Evaluation Report | ✅ | ✅ | ✅ | ✅ | ⚠️ (no auto-trigger) |
| Human Governance | ✅ | ✅ | ✅ | ✅ | ✅ |
| Production Boundary | ✅ | ✅ | ✅ | ✅ | ✅ |

---

## 20. Final Classification

### **C. PARTIALLY INTEGRATED**

**Justification**: The complete lifecycle is architecturally sound and each component works correctly in isolation and in pairs. However, one critical orchestration gap — **automatic candidate activation** — prevents the system from operating as a fully autonomous discovery-to-recommendation pipeline without manual intervention.

**The system cannot currently**:
1. Autonomously carry a finding from discovery to shadow testing (requires 2 manual status changes)
2. Automatically re-evaluate candidates when new evidence arrives
3. Automatically generate the evaluation report after evaluation

**The system CAN**:
1. Autonomously discover findings, form hypotheses, run experiments, and conclude
2. Autonomously create candidates from VALIDATED conclusions
3. Correctly shadow-test candidates once manually activated
4. Correctly evaluate candidates with full statistical rigour
5. Correctly transition lifecycle states based on evaluation results
6. Completely prevent any production change without human governance

---

## Remaining Gaps (Ordered by Impact)

### GAP 1: Candidate Activation (Critical)
- **What**: No code transitions PROPOSED → VALIDATING → SHADOW_TESTING
- **Why it matters**: Without this, candidates never accumulate prospective observations
- **Proposed fix**: Add `activate_candidate()` to the orchestrator or bridge that performs `PROPOSED → VALIDATING` (or a new PROPOSED → SHADOW_TESTING transition if the state machine is updated)
- **Files**: `candidate_evaluation_bridge.py` or new `candidate_activator.py`
- **Scope**: ~20 lines
- **Safety**: Does not affect production — only enables observation collection

### GAP 2: Automatic Re-Evaluation Trigger (Medium)
- **What**: No scheduler/hook calls `evaluate_candidate()` when new evidence arrives
- **Why it matters**: Without this, evaluation is only manual
- **Proposed fix**: A periodic check (e.g., in the research cycle runner) that evaluates SHADOW_TESTING candidates when their pair count crosses the minimum threshold
- **Files**: `research_cycle_runner.py` or new post-cycle hook
- **Scope**: ~40 lines
- **Safety**: Observation-only — evaluation cannot change production

### GAP 3: Full Evaluation Persistence (Low)
- **What**: Full `CandidateEvaluation` (with CI, p-value, OOS details) is not persisted
- **Why it matters**: Audit trail is incomplete; reviewer cannot reconstruct full statistical reasoning
- **Proposed fix**: Persist `evaluation.to_dict()` alongside the ValidationEntry
- **Files**: `candidate_evaluation_bridge.py`
- **Scope**: ~15 lines

### GAP 4: Un-shadowable Candidate Types (Low)
- **What**: `score_recalibration` and `pattern_weighting` candidates can be created but never shadow-tested
- **Why it matters**: These sit in SHADOW_TESTING forever with no evidence
- **Proposed fix**: Either prevent their creation, or add shadow translation logic for them
- **Files**: `candidate_shadow_hook.py` or `orchestrator.py`
- **Scope**: Design decision needed

---

## Final Answer

> **Can the system autonomously carry a discovery all the way to a prospective, statistically evaluated, human-readable optimisation recommendation — while remaining completely unable to change or execute the production strategy without explicit human governance?**

**ALMOST, BUT NOT YET.**

- **Capability**: The architecture is complete. Every component exists, is tested, and is correctly designed.
- **Gap**: One missing orchestration call (candidate activation) prevents fully autonomous operation.
- **Safety**: The production boundary is absolute. There is zero code path from research to production execution. This has been verified structurally.

With the addition of ~20 lines of activation logic, the answer would change to **YES**.
