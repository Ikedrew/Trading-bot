"""Wave A2 Safe Definition Closure — authoritative scientific definitions.

This module closes the canonical Wave A2 scientific-definition contract ONLY for
the subset of research questions whose semantics are already established by the
current canonical registry, dedicated runners, evidence resolver, readiness
rules, and existing tests.

SOURCE PRIORITY (used for every target) — same cascade as Wave A1:
    1. canonical question registry intent
    2. dedicated runner semantics
    3. canonical evidence resolver
    4. canonical readiness/sample rules
    5. canonical report contract
    6. existing tests documenting intended semantics

SCOPE GUARANTEES:
    * Nothing here invents new science. Every hypothesis, population,
      metric, evidence authority, join contract, epoch requirement and
      completion rule is derived from already-existing authoritative behavior.
    * No Data Collection, S3, AWS, trading logic, execution routing,
      multi-account code, evidence resolver behavior, readiness algorithm,
      runner algorithm, report algorithm, Wave A1 logic, or non-target
      definition is modified.
    * Targets whose sources do NOT agree strongly enough are left UNRESOLVED
      (fail-closed): their base definitions stay blank and are never forced
      VALID.

RESOLVED (8):  E2 E5 M5 S2 X2 S3 S4 RISK-1
UNRESOLVED (1): M8   (see WAVE_A2_UNRESOLVED_REASONS)
"""
from __future__ import annotations

from dataclasses import replace

from research_engine.registry.research_question_models import (
    CompletionRule,
    EvidenceAuthority,
    EvidenceProducer,
    JoinContract,
    ResearchQuestionDefinition,
)

# Frozen Wave A2 tranche scopes and their cumulative scope.
WAVE_A2_1_TARGETS = frozenset({
    "E2", "E5", "M5", "M8", "S2", "X2",
})
WAVE_A2_2A_TARGETS = frozenset({
    "S3", "S4", "RISK-1",
})
WAVE_A2_TARGETS = WAVE_A2_1_TARGETS | WAVE_A2_2A_TARGETS

# Targets closed in this module.
WAVE_A2_RESOLVED = frozenset({
    "E2", "E5", "M5", "S2", "X2", "S3", "S4", "RISK-1",
})

# Targets intentionally left unresolved because authoritative sources conflict.
# Their base definitions remain blank so the validator stays fail-closed.
WAVE_A2_UNRESOLVED = frozenset({
    "M8",
})

# Exact conflicts that prevent authoritative closure. Kept machine-readable so
# a fail-closed target can never be mistaken for an oversight.
WAVE_A2_UNRESOLVED_REASONS = {
    "M8": (
        "Registry declares data_sources=(DataSource.MARKET_CONTEXT, "
        "DataSource.SHADOW_TRADES), expecting a separate market_context "
        "dataset. The dedicated runner (market_temporal.run_m8) does NOT "
        "load market_context as a distinct source — it derives market_phase "
        "from decision_snapshot embedded in shadow_trades records "
        "(temporal_universe_used=False, TEMPORAL_UNIVERSE_OPTIONAL per "
        "runner provenance). Declaring both authorities with a join_contract "
        "would invent a join the runner never performs; declaring only "
        "shadow_trades contradicts the registry's declared data_sources. "
        "Left fail-closed."
    ),
}


def _auth(
    dataset: str,
    *,
    producer: EvidenceProducer | None = None,
    field_path: str | None = None,
    semantic_meaning: str = "",
    current_eligibility: bool = True,
) -> EvidenceAuthority:
    """Build one evidence authority (field-level semantic contract)."""
    return EvidenceAuthority(
        dataset=dataset,
        producer=producer,
        field_path=field_path,
        semantic_meaning=semantic_meaning,
        current_eligibility=current_eligibility,
    )


def _override(
    *,
    hypothesis: str,
    null_hypothesis: str,
    population_definition: str,
    metric_definition: str,
    evidence_authorities: tuple[EvidenceAuthority, ...],
    join_contract: JoinContract | None = None,
    epoch_requirement: str = "CURRENT",
    minimum_sample: int | None = None,
    completion_rule: CompletionRule | None = None,
) -> dict:
    """Assemble a partial-definition override applied on top of the registry base."""
    return {
        "hypothesis": hypothesis,
        "null_hypothesis": null_hypothesis,
        "population_definition": population_definition,
        "metric_definition": metric_definition,
        "evidence_authorities": evidence_authorities,
        "join_contract": join_contract,
        "epoch_requirement": epoch_requirement,
        "minimum_sample": minimum_sample,
        "completion_rule": completion_rule,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Per-question authoritative overrides (derived ONLY from existing behavior).
# ─────────────────────────────────────────────────────────────────────────────

WAVE_A2_OVERRIDES: dict[str, dict] = {
    # ── E2 — Pattern expectancy (legacy_canonical.run_q05) ──────────────────
    # Runner note: shares report_filename "q5_pattern_degradation.json" with
    # L1 (in SEMANTIC_MISMATCH_QIDS). E2 and L1 are NOT semantically
    # equivalent — they answer different questions on the same report. The
    # AMBIGUOUS_REPORT_MAPPING validator warning captures this honestly.
    "E2": _override(
        hypothesis=(
            "Candlestick patterns with sufficient observational support "
            "show positive expectancy: mean R-multiples per pattern are "
            "greater than zero in CURRENT shadow evidence."
        ),
        null_hypothesis=(
            "Candlestick patterns show no positive expectancy in CURRENT "
            "shadow evidence: mean R-multiples per pattern are not greater "
            "than zero."
        ),
        population_definition=(
            "Completed shadow lifecycles from canonical shadow_runtime_v1 "
            "ingestion carrying a pattern tag and realised R-multiple "
            "outcome (decision_snapshot.pattern + simulated_outcome."
            "pnl_r_multiple). Patterns with fewer than 5 observations are "
            "reported descriptively but excluded from the degradation "
            "comparison table."
        ),
        metric_definition=(
            "Per-pattern mean R-multiple and win rate for patterns with "
            ">=5 trades; aggregate expectancy across all patterns with "
            "outcomes. Confidence bands via experiment_base.compute_confidence "
            "(LOW >=20, MEDIUM >=50, HIGH >=100/MEDIUM >=100, "
            "HIGH >=200 when significant)."
        ),
        evidence_authorities=(
            _auth(
                "shadow_trades",
                producer=EvidenceProducer.SHADOW_TRADES,
                field_path=(
                    "simulated_outcome.pnl_r_multiple | "
                    "decision_snapshot.pattern"
                ),
                semantic_meaning=(
                    "Realised R-multiple and pattern tag from completed "
                    "shadow lifecycles"
                ),
            ),
        ),
        epoch_requirement="CURRENT",
        minimum_sample=None,
        completion_rule=CompletionRule(
            rule_type="report_exists",
            threshold=None,
            description=(
                "Runner (run_q05) declares COMPLETE when >=1 shadow outcome "
                "with a pattern exists. Per-pattern cells require >=5 trades "
                "for the degradation comparison table. "
                "AMBIGUOUS_REPORT_MAPPING: report_filename "
                "q5_pattern_degradation.json is shared with L1."
            ),
        ),
    ),

    # ── E5 — Out-of-sample validation
    # (out_of_sample_validation.run_out_of_sample_validation)
    # Hidden threshold: _MIN_SAMPLES = 80.
    "E5": _override(
        hypothesis=(
            "The measured edge survives on unseen market data: "
            "in-sample train EV is positive, out-of-sample test EV "
            "remains positive, and stability is >=70% across 5 rolling "
            "windows."
        ),
        null_hypothesis=(
            "The measured edge does not survive out-of-sample: test EV "
            "is non-positive or stability is below 70%."
        ),
        population_definition=(
            "CURRENT-epoch completed shadow lifecycles with R-multiple "
            "outcomes (simulated_outcome.pnl_r_multiple), split "
            "chronologically 60% train / 40% test, evaluated with "
            "_NUM_ROLLING_WINDOWS=5 rolling windows and an expanding "
            "window. Needs >=80 R-multiples for a meaningful split "
            "(_MIN_SAMPLES=80)."
        ),
        metric_definition=(
            "Train EV, out-of-sample test EV, drift (|train EV - "
            "test EV|), stability score (fraction of rolling windows "
            "with positive EV), overall EV, edge_survives flag, drift "
            "significance (drift > 50% of overall EV). Expanding window EV."
        ),
        evidence_authorities=(
            _auth(
                "shadow_trades",
                producer=EvidenceProducer.SHADOW_TRADES,
                field_path="simulated_outcome.pnl_r_multiple",
                semantic_meaning=(
                    "Realised R-multiple from shadow outcomes for "
                    "walk-forward validation"
                ),
            ),
        ),
        epoch_requirement="CURRENT",
        minimum_sample=80,
        completion_rule=CompletionRule(
            rule_type="sample_reached",
            threshold=80,
            description=(
                "Runner (run_out_of_sample_validation) declares COMPLETE "
                "only when >=80 R-multiples are available for walk-forward "
                "split (module constant _MIN_SAMPLES=80); below that "
                "INSUFFICIENT_DATA. Depends_on E1 for upstream edge."
            ),
        ),
    ),

    # ── M5 — Phase transitions predict drawdown (market_temporal.run_m5)
    # Hidden thresholds: _M5_MIN_RECORDS=30, _M5_MIN_TRANSITIONS=3.
    "M5": _override(
        hypothesis=(
            "Rapid phase transitions (e.g. IMPULSE to EXHAUSTION) are "
            "associated with increased realized strategy drawdown in "
            "cumulative R."
        ),
        null_hypothesis=(
            "Phase transitions are not associated with increased "
            "realized strategy drawdown in R."
        ),
        population_definition=(
            "CURRENT-epoch completed shadow lifecycles with market_phase "
            "(from decision_snapshot) and realised R-multiple "
            "(simulated_outcome.pnl_r_multiple), ordered chronologically "
            "per symbol. Needs >=30 usable records AND >=3 phase-transition "
            "observations (_M5_MIN_RECORDS=30, _M5_MIN_TRANSITIONS=3)."
        ),
        metric_definition=(
            "Realized strategy drawdown curve in R from chronologically "
            "closed shadow outcomes. Comparison of mean R after phase "
            "transitions vs no-transition trades, transition-type "
            "breakdown, drawdown increases attributed to transitions. "
            "NOT mark-to-market or account-equity drawdown."
        ),
        evidence_authorities=(
            _auth(
                "shadow_trades",
                producer=EvidenceProducer.SHADOW_TRADES,
                field_path=(
                    "simulated_outcome.pnl_r_multiple | "
                    "decision_snapshot.market_phase"
                ),
                semantic_meaning=(
                    "Realised R-multiple and market phase from completed "
                    "shadow lifecycles"
                ),
            ),
        ),
        epoch_requirement="CURRENT",
        minimum_sample=30,
        completion_rule=CompletionRule(
            rule_type="sample_reached",
            threshold=30,
            description=(
                "Runner (run_m5) declares COMPLETE only when >=30 usable "
                "CURRENT records AND >=3 phase-transition observations "
                "exist (_M5_MIN_RECORDS=30, _M5_MIN_TRANSITIONS=3); "
                "below that INSUFFICIENT_DATA."
            ),
        ),
    ),

    # ── S2 — Horizon affects expectancy (selection_research.run_s2)
    # Hidden thresholds: _MIN_SAMPLE=30 (overall), _MIN_CELL=10 (per cell).
    # The runner remains INSUFFICIENT_DATA when only SCALP is represented;
    # horizons are consumed only when actually present in shadow evidence.
    "S2": _override(
        hypothesis=(
            "Mean simulated R-multiple differs across at least two persisted "
            "shadow horizons, with a spread of at least 0.25R between the "
            "best and worst sufficiently populated horizon groups."
        ),
        null_hypothesis=(
            "Persisted shadow horizon groups do not differ materially in "
            "mean simulated R-multiple (spread below 0.25R), or the evidence "
            "does not contain enough populated horizon groups to compare."
        ),
        population_definition=(
            "CURRENT-epoch completed shadow simulation lifecycles with a "
            "finite simulated_outcome.pnl_r_multiple and an explicitly "
            "persisted identity.evaluated_horizon. The population includes "
            "primary and horizon-alternative simulations already present in "
            "the authoritative shadow evidence; no missing INTRADAY or "
            "EXTENDED outcomes are inferred. At least 30 outcome records "
            "across at least two observed horizons are required."
        ),
        metric_definition=(
            "Per-horizon sample size, mean simulated R-multiple and win rate; "
            "primary-horizon results are also reported separately. Horizon "
            "groups with at least 10 outcomes participate in the best-to-worst "
            "mean-R spread, and a spread >=0.25R is reported as differing "
            "horizon outcomes. Records from the same opportunity are explicitly "
            "non-independent observational simulations."
        ),
        evidence_authorities=(
            _auth(
                "shadow_trades",
                producer=EvidenceProducer.SHADOW_TRADES,
                field_path=(
                    "identity.evaluated_horizon | identity.shadow_type | "
                    "simulated_outcome.pnl_r_multiple"
                ),
                semantic_meaning=(
                    "Persisted evaluated horizon, simulation type, and finite "
                    "simulated R-multiple from a completed shadow lifecycle"
                ),
            ),
        ),
        epoch_requirement="CURRENT",
        minimum_sample=30,
        completion_rule=CompletionRule(
            rule_type="sample_reached",
            threshold=30,
            description=(
                "Runner (run_s2) declares COMPLETE only when >=30 finite "
                "shadow outcome records span >=2 explicitly observed horizons "
                "(_MIN_SAMPLE=30); per-horizon comparison cells require >=10 "
                "records (_MIN_CELL=10). SCALP-only evidence remains "
                "INSUFFICIENT_DATA."
            ),
        ),
    ),

    # -- X2 - Broker failure patterns (execution_protection_research.run_x2)
    # Hidden thresholds: _MIN_SAMPLE=30 for either evidence layer and
    # _MIN_CELL=10 for per-symbol output. The two layers are not joined.
    "X2": _override(
        hypothesis=(
            "Persisted execution results exhibit identifiable broker failure "
            "patterns in result_ok/retcode distributions and sufficiently "
            "populated per-symbol failure rates; execution attempts separately "
            "characterise retry and per-attempt failure behaviour."
        ),
        null_hypothesis=(
            "Persisted execution results and attempts do not exhibit "
            "differentiated broker failure, retcode, symbol, or retry patterns."
        ),
        population_definition=(
            "Two independent CURRENT evidence layers: execution_results_v1 "
            "records for broker result_ok, retcode and canonical symbol; and "
            "execution_attempts_v1 records for attempt number, broker result, "
            "retry reason and stable trade/correlation/attempt identity. The "
            "runner performs no proximity or record-level join between the "
            "layers. It requires at least 30 records in either layer."
        ),
        metric_definition=(
            "Overall result_ok rate and retcode distribution from execution "
            "results; per-symbol not-ok rate for symbol cells with >=10 "
            "results; and, when attempts exist, retry rate, per-attempt failure "
            "rate, unique-trade count and retry-reason distribution. This is a "
            "broker execution profile, not a profitability analysis."
        ),
        evidence_authorities=(
            _auth(
                "execution_results_v1",
                producer=EvidenceProducer.EXECUTION_RESULTS,
                field_path="result_ok | retcode | symbol",
                semantic_meaning=(
                    "Persisted broker execution outcome and canonical symbol"
                ),
            ),
            _auth(
                "execution_attempts_v1",
                producer=None,
                field_path=(
                    "attempt_number | broker_result | retry_reason | "
                    "trade_id | correlation_id | attempt_id"
                ),
                semantic_meaning=(
                    "Persisted per-attempt broker result and retry behaviour"
                ),
            ),
        ),
        join_contract=JoinContract(
            join_keys=(),
            cardinality="independent_populations",
            conflict_policy="reject",
            description=(
                "No record-level join: run_x2 analyses execution_results_v1 "
                "and execution_attempts_v1 as independent evidence layers and "
                "combines only their aggregate sample sufficiency."
            ),
        ),
        epoch_requirement="CURRENT",
        minimum_sample=30,
        completion_rule=CompletionRule(
            rule_type="sample_reached",
            threshold=30,
            description=(
                "Runner (run_x2) declares COMPLETE when execution_results_v1 "
                "has >=30 records OR execution_attempts_v1 has >=30 records "
                "(_MIN_SAMPLE=30); per-symbol views require >=10 result records "
                "(_MIN_CELL=10)."
            ),
        ),
    ),

    # -- S3 - Strategy x horizon combinations (selection_research.run_s3)
    # Full simulation population; _MIN_SAMPLE=30 and _MIN_CELL=10.
    "S3": _override(
        hypothesis=(
            "At least one sufficiently populated canonical strategy and "
            "persisted evaluated-horizon combination has positive mean "
            "simulated R-multiple in CURRENT shadow evidence."
        ),
        null_hypothesis=(
            "No sufficiently populated canonical strategy and evaluated-"
            "horizon combination has positive mean simulated R-multiple in "
            "CURRENT shadow evidence."
        ),
        population_definition=(
            "CURRENT-epoch completed shadow simulation lifecycles with finite "
            "simulated_outcome.pnl_r_multiple, canonical decision-time "
            "strategy, and explicitly persisted evaluated_horizon. Primary "
            "and horizon-alternative simulations are included. The independent "
            "strategy observation is the canonical opportunity; analytical "
            "cells contain one persisted simulation per "
            "(canonical_opportunity_id, evaluated_horizon). Account-grained "
            "execution fanout is not part of this population."
        ),
        metric_definition=(
            "Per (strategy, evaluated_horizon) cell sample size, mean and "
            "median simulated R-multiple, win rate, total R and R standard "
            "deviation. Cells with >=10 outcomes are eligible for conclusions; "
            "positive cells have mean R > 0. The number of combinations tested "
            "and undersized cells is reported for multiple-comparison context."
        ),
        evidence_authorities=(
            _auth(
                "shadow_trades",
                producer=EvidenceProducer.SHADOW_TRADES,
                field_path=(
                    "identity.canonical_opportunity_id | "
                    "identity.evaluated_horizon | decision_snapshot.strategy | "
                    "simulated_outcome.pnl_r_multiple"
                ),
                semantic_meaning=(
                    "Canonical opportunity lineage, persisted simulated "
                    "horizon, decision-time strategy, and completed simulated "
                    "R-multiple"
                ),
            ),
        ),
        epoch_requirement="CURRENT",
        minimum_sample=30,
        completion_rule=CompletionRule(
            rule_type="sample_reached",
            threshold=30,
            description=(
                "Runner (run_s3) declares COMPLETE when >=30 finite shadow "
                "outcome records form at least one strategy+horizon cell "
                "(_MIN_SAMPLE=30). Cells require >=10 outcomes (_MIN_CELL=10) "
                "to support a positive-combination conclusion. Dependencies "
                "S1 and S2 remain governed by the registry."
            ),
        ),
    ),

    # -- S4 - Strategy specialisation by phase (selection_research.run_s4)
    # Primary-horizon only to avoid duplicate opportunity observations.
    "S4": _override(
        hypothesis=(
            "At least one canonical strategy is phase-specialised: across at "
            "least two sufficiently populated market-phase cells, its best-to-"
            "worst mean simulated R-multiple spread is >=0.5R."
        ),
        null_hypothesis=(
            "No canonical strategy has a best-to-worst mean simulated "
            "R-multiple spread of >=0.5R across at least two sufficiently "
            "populated market-phase cells."
        ),
        population_definition=(
            "CURRENT-epoch PRIMARY_HORIZON_SIMULATION completed shadow "
            "lifecycles with finite simulated_outcome.pnl_r_multiple, canonical "
            "decision-time strategy, and decision-time market_phase. The unit "
            "of analysis is one primary-horizon shadow lifecycle per canonical "
            "opportunity; horizon alternatives and account-grained execution "
            "fanout are excluded."
        ),
        metric_definition=(
            "Per (strategy, market_phase) cell R-multiple summary, followed by "
            "a within-strategy best-phase minus worst-phase mean-R spread across "
            "phase cells with >=10 outcomes. Spread >=0.5R is "
            "PHASE_SPECIALISED, >=0.25R is MILD_PHASE_VARIATION, otherwise "
            "NO_MATERIAL_PHASE_SPECIALISATION; fewer than two sufficient phase "
            "cells yields INSUFFICIENT_PHASE_COVERAGE for that strategy."
        ),
        evidence_authorities=(
            _auth(
                "shadow_trades",
                producer=EvidenceProducer.SHADOW_TRADES,
                field_path=(
                    "identity.canonical_opportunity_id | identity.shadow_type "
                    "| decision_snapshot.strategy | "
                    "decision_snapshot.market_phase | "
                    "simulated_outcome.pnl_r_multiple"
                ),
                semantic_meaning=(
                    "Primary-horizon canonical opportunity, decision-time "
                    "strategy and phase, and completed simulated R-multiple"
                ),
            ),
        ),
        epoch_requirement="CURRENT",
        minimum_sample=30,
        completion_rule=CompletionRule(
            rule_type="sample_reached",
            threshold=30,
            description=(
                "Runner (run_s4) declares COMPLETE when >=30 finite primary-"
                "horizon outcomes form at least one strategy+phase cell "
                "(_MIN_SAMPLE=30). A specialisation conclusion for a strategy "
                "requires >=2 phase cells with >=10 outcomes each "
                "(_MIN_CELL=10). Dependencies E3 and M3 remain governed by "
                "the registry."
            ),
        ),
    ),

    # -- RISK-1 - Realised risk-control fidelity (risk_research.run_risk1)
    # Post-outcome diagnostic population; _MIN_SAMPLE=30, _MIN_CELL=10.
    "RISK-1": _override(
        hypothesis=(
            "Recorded realised losses respect the 1-R planned-risk definition: "
            "fewer than 5% of classified loss records are ELEVATED or CRITICAL "
            "and no CRITICAL loss is present."
        ),
        null_hypothesis=(
            "At least 5% of classified loss records are ELEVATED or CRITICAL, "
            "or at least one CRITICAL realised loss is present."
        ),
        population_definition=(
            "CURRENT risk_deviation_v1 post-outcome diagnostic records with a "
            "closed-trade trade_id and a valid canonical "
            "risk_classification (NORMAL, ELEVATED, CRITICAL, WIN, or "
            "NO_RISK_DATA). The unit of analysis is one recorded closed trade, "
            "not one canonical decision. Separate account executions may be "
            "valid separate observations only when they are persisted as "
            "distinct closed-trade trade_ids. No strategy-observation count is "
            "derived from account fanout."
        ),
        metric_definition=(
            "Classification distribution; ELEVATED-or-CRITICAL rate and "
            "CRITICAL rate using classified loss records "
            "(NORMAL/ELEVATED/CRITICAL) as denominator; realised loss-deviation "
            "and actual-R summaries; win and NO_RISK_DATA counts; and per-symbol "
            "classification summaries for cells with >=10 records. "
            "planned_risk_R is the definitional -1R reference, not a captured "
            "pre-trade intended-risk chain."
        ),
        evidence_authorities=(
            _auth(
                "risk_deviation_v1",
                producer=EvidenceProducer.RISK_DEVIATION,
                field_path=(
                    "trade_id | risk_classification | risk_deviation | "
                    "actual_risk_R | planned_risk_R | symbol | semantic_stage"
                ),
                semantic_meaning=(
                    "Post-outcome closed-trade diagnostic comparing realised "
                    "loss magnitude with the definitional 1-R reference"
                ),
            ),
        ),
        epoch_requirement="CURRENT",
        minimum_sample=30,
        completion_rule=CompletionRule(
            rule_type="sample_reached",
            threshold=30,
            description=(
                "Runner (run_risk1) declares COMPLETE with >=30 usable "
                "risk_deviation_v1 classification records (_MIN_SAMPLE=30); "
                "per-symbol summaries require >=10 records (_MIN_CELL=10)."
            ),
        ),
    ),
}


def apply_wave_a2_definitions(
    definitions: dict[str, ResearchQuestionDefinition],
) -> dict[str, ResearchQuestionDefinition]:
    """Return definitions with only resolved Wave A2.1 overrides applied.

    M8 and every non-target definition retain their existing objects and
    fields. This keeps unresolved authority conflicts fail-closed and makes
    the application order safe after Wave A1.
    """
    result = dict(definitions)
    for qid, overrides in WAVE_A2_OVERRIDES.items():
        base = result[qid]
        result[qid] = replace(base, **overrides)
    return result

