"""Wave A1 Safe Definition Closure — authoritative scientific definitions.

This module closes the canonical Wave A scientific-definition contract ONLY for
the subset of research questions whose semantics are already established by the
current canonical registry, dedicated runners, evidence resolver, readiness
rules, and existing tests.

SOURCE PRIORITY (used for every target):
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
    * No Data Collection, S3, trading, execution, risk, multi-account,
      readiness-gating, runner, resolver-mapping or non-target definition is
      modified.
    * Targets whose sources do NOT agree strongly enough are left UNRESOLVED
      (fail-closed): their base definitions stay blank and are never forced
      VALID.

RESOLVED (16):  E1 E4 M2 M4 M6 M9 M10 D1 X1 X4 EX3 EX4 MGMT-1 MGMT-2 STRAT-1 PROT1
UNRESOLVED (4): EX1 EX2 EX10 L7   (see WAVE_A1_UNRESOLVED_REASONS)
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

# The full Wave A1 target scope (20 questions).
WAVE_A1_TARGETS = frozenset({
    "E1", "E4", "M2", "M4", "M6", "M9", "M10", "D1", "X1", "X4",
    "L7", "EX1", "EX2", "EX3", "EX4", "EX10",
    "MGMT-1", "MGMT-2", "STRAT-1", "PROT1",
})

# Targets closed in this module (all remaining targets).
WAVE_A1_RESOLVED = frozenset({
    "E1", "E4", "M2", "M4", "M6", "M9", "M10", "D1", "X1", "X4",
    "EX3", "EX4", "MGMT-1", "MGMT-2", "STRAT-1", "PROT1",
})

# Targets intentionally left unresolved because authoritative sources conflict.
# Their base definitions remain blank so the validator stays fail-closed.
WAVE_A1_UNRESOLVED = frozenset({
    "EX1", "EX2", "EX10", "L7",
})

# Exact conflicts that prevent authoritative closure. Kept machine-readable so
# a fail-closed target can never be mistaken for an oversight.
WAVE_A1_UNRESOLVED_REASONS = {
    "EX1": (
        "Registry intent asks whether MODIFYING exit policy (trailing stop, "
        "reduced TP, time-based) improves expected value versus the current "
        "max_bars timeout, which requires a counterfactual policy comparison. "
        "The dedicated runner (exit_management.run_ex1) is explicitly "
        "observational (MFE capture ratio / giveback / reversal rate) and "
        "documents that it does NOT prove an alternative exit policy would "
        "improve outcomes. Hypothesis, metric and population therefore cannot "
        "be stated without inventing an unexecuted counterfactual."
    ),
    "EX2": (
        "Registry intent asks whether a trailing stop mechanism captures MORE "
        "of the available MFE than the current exit using bar-by-bar "
        "sequential simulation. The dedicated runner (exit_management.run_ex2) "
        "is explicitly an observational retention analysis (realised/MFE among "
        "MFE>=0.5R trades) and states NO trailing-stop simulation is performed. "
        "The arm-comparison semantics (current vs trailing stop) do not agree "
        "across sources."
    ),
    "EX10": (
        "Registry intent asks whether an exit-policy IMPROVEMENT holds on "
        "out-of-sample data via time-ordered walk-forward validation. The "
        "dedicated runner (exit_depth.run_ex10) and its tests document that "
        "only a DESCRIPTIVE 50/50 chronological stability check is executable "
        "and that walk_forward_improvement_validation is BLOCKED_BY_DATA. "
        "Because no exit policy improvement is defined or validated, the "
        "question's core claim cannot be populated without changing its "
        "meaning."
    ),
    "L7": (
        "Registry intent and the required 'schema_version' field define A/B "
        "arms as currently-promoted (control) vs proposed strategy change "
        "(candidate). The dedicated runner (shadow_ab_validation."
        "run_shadow_ab_validation) instead assigns arms by a chronological "
        "first-half/second-half temporal split and never reads schema_version, "
        "so arm-assignment semantics do not agree across sources."
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

WAVE_A1_OVERRIDES: dict[str, dict] = {
    # ── E1 — True system expectancy (expected_value.run) ──────────────────────
    "E1": _override(
        hypothesis=(
            "The production decision pipeline's per-trade expected R-multiple is "
            "positive (a true edge exists) in CURRENT shadow evidence."
        ),
        null_hypothesis=(
            "The pipeline's per-trade expected R-multiple is not distinguishable "
            "from zero in CURRENT shadow evidence."
        ),
        population_definition=(
            "All CURRENT-epoch completed shadow lifecycles drawn from canonical "
            "shadow_runtime_v1 ingestion plus research_shadow_trades that carry an "
            "R-multiple outcome and lineage identity (entity_id). Readiness also "
            "requires the decision score, pattern and exit_reason fields to be "
            "present for a row to be analysable."
        ),
        metric_definition=(
            "Per-trade R-multiple. Primary metric is the mean R-multiple "
            "(expected value per trade); supporting metrics are win rate, profit "
            "factor, and one-sample t-test significance of the mean against zero."
        ),
        evidence_authorities=(
            _auth(
                "shadow_trades",
                producer=EvidenceProducer.SHADOW_TRADES,
                field_path="simulated_outcome.pnl_r_multiple | r_multiple",
                semantic_meaning=(
                    "Realised R-multiple of a completed shadow lifecycle "
                    "(observed path fact)"
                ),
            ),
        ),
        completion_rule=CompletionRule(
            rule_type="report_exists",
            description=(
                "Runner reports COMPLETE when at least one CURRENT shadow "
                "R-multiple is analysed; INSUFFICIENT_DATA when none."
            ),
        ),
    ),

    # ── E4 — Strategy × pattern combinations (edge_depth.run_e4) ─────────────
    "E4": _override(
        hypothesis=(
            "Specific strategy × pattern combinations produce positive edge "
            "(mean R-multiple above the runner's edge threshold) in CURRENT "
            "shadow evidence."
        ),
        null_hypothesis=(
            "No strategy × pattern combination shows mean R-multiple above the "
            "edge threshold in CURRENT shadow evidence."
        ),
        population_definition=(
            "CURRENT-epoch completed shadow lifecycles carrying a canonical "
            "strategy (REVERSAL/CONTINUATION/FALSE_BREAK), a detected pattern, "
            "and an R-multiple outcome."
        ),
        metric_definition=(
            "Per (strategy × pattern) cell mean R-multiple and win rate over the "
            "CURRENT shadow population; edge is assessed from the resulting "
            "segment edge statistics."
        ),
        evidence_authorities=(
            _auth(
                "shadow_trades",
                producer=EvidenceProducer.SHADOW_TRADES,
                field_path=(
                    "decision_snapshot.strategy | decision_snapshot.pattern; "
                    "simulated_outcome.pnl_r_multiple"
                ),
                semantic_meaning=(
                    "Strategy and pattern decision context joined to the realised "
                    "R-multiple of the same completed shadow lifecycle"
                ),
            ),
        ),
        completion_rule=CompletionRule(
            rule_type="report_exists",
            description=(
                "Runner reports COMPLETE when at least one CURRENT shadow "
                "R-multiple is analysable by the runner; INSUFFICIENT_DATA when "
                "none."
            ),
        ),
    ),

    # ── M2 — H4 regime edge by strategy (market_research.run_m2) ─────────────
    "M2": _override(
        hypothesis=(
            "H4 regime × strategy combinations differ in mean R-multiple, with "
            "at least one combination exhibiting positive edge in CURRENT shadow "
            "evidence."
        ),
        null_hypothesis=(
            "Mean R-multiple does not differ materially across H4 regime × "
            "strategy combinations in CURRENT shadow evidence."
        ),
        population_definition=(
            "CURRENT-epoch completed shadow lifecycles with an H4 regime label, "
            "a canonical strategy, and an R-multiple outcome."
        ),
        metric_definition=(
            "Per (h4_regime × strategy) cell mean R-multiple and win rate; "
            "overall edge assessed across populated cells."
        ),
        evidence_authorities=(
            _auth(
                "shadow_trades",
                producer=EvidenceProducer.SHADOW_TRADES,
                field_path=(
                    "decision_snapshot.h4_regime | decision_snapshot.strategy; "
                    "simulated_outcome.pnl_r_multiple"
                ),
                semantic_meaning=(
                    "Regime and strategy context joined to the realised "
                    "R-multiple of the same completed shadow lifecycle"
                ),
            ),
        ),
        completion_rule=CompletionRule(
            rule_type="report_exists",
            description=(
                "Runner reports COMPLETE when at least one CURRENT shadow "
                "R-multiple is analysable; INSUFFICIENT_DATA when none."
            ),
        ),
    ),

    # ── M4 — Regime × phase × strategy edge (market_research.run_m4) ─────────
    "M4": _override(
        hypothesis=(
            "The three-way interaction of H4 regime, market phase and strategy "
            "contains cells with positive mean R-multiple in CURRENT shadow "
            "evidence."
        ),
        null_hypothesis=(
            "No (regime × phase × strategy) cell exhibits mean R-multiple above "
            "the edge threshold in CURRENT shadow evidence."
        ),
        population_definition=(
            "CURRENT-epoch completed shadow lifecycles with an H4 regime label, "
            "a market phase label, a canonical strategy, and an R-multiple "
            "outcome."
        ),
        metric_definition=(
            "Per (h4_regime × market_phase × strategy) cell mean R-multiple and "
            "win rate; edge assessed across populated cells."
        ),
        evidence_authorities=(
            _auth(
                "shadow_trades",
                producer=EvidenceProducer.SHADOW_TRADES,
                field_path=(
                    "decision_snapshot.h4_regime | decision_snapshot.market_phase "
                    "| decision_snapshot.strategy; simulated_outcome."
                    "pnl_r_multiple"
                ),
                semantic_meaning=(
                    "Regime/phase/strategy decision context joined to the "
                    "realised R-multiple of the same completed shadow lifecycle"
                ),
            ),
        ),
        completion_rule=CompletionRule(
            rule_type="report_exists",
            description=(
                "Runner reports COMPLETE when at least one CURRENT shadow "
                "R-multiple is analysable; INSUFFICIENT_DATA when none."
            ),
        ),
    ),


    # ── M6 — Market phase expectancy (market_research.run_m6) ────────────────
    "M6": _override(
        hypothesis=(
            "At least one market phase "
            "(IMPULSE/PULLBACK/CONSOLIDATION/EXHAUSTION/REVERSAL) contains real "
            "positive edge (positive mean R-multiple) in CURRENT shadow evidence."
        ),
        null_hypothesis=(
            "No market phase shows positive mean R-multiple in CURRENT shadow "
            "evidence."
        ),
        population_definition=(
            "CURRENT-epoch completed shadow lifecycles with a market phase label "
            "in the canonical vocabulary and an R-multiple outcome."
        ),
        metric_definition=(
            "Per-market-phase mean R-multiple and win rate."
        ),
        evidence_authorities=(
            _auth(
                "shadow_trades",
                producer=EvidenceProducer.SHADOW_TRADES,
                field_path=(
                    "decision_snapshot.market_phase; "
                    "simulated_outcome.pnl_r_multiple"
                ),
                semantic_meaning=(
                    "Market phase context joined to the realised R-multiple of "
                    "the same completed shadow lifecycle"
                ),
            ),
        ),
        completion_rule=CompletionRule(
            rule_type="report_exists",
            description=(
                "Runner reports COMPLETE when at least one CURRENT shadow "
                "R-multiple is analysable; INSUFFICIENT_DATA when none."
            ),
        ),
    ),

    # ── M9 — Phase-appropriate pattern classification (m9_phase_pattern) ─────
    "M9": _override(
        hypothesis=(
            "For at least one market phase, a specific pattern cell exhibits "
            "positive expectancy (positive mean R) in CURRENT shadow evidence, "
            "indicating phase-appropriate pattern behaviour."
        ),
        null_hypothesis=(
            "No (market_phase × pattern) cell exhibits positive mean R in "
            "CURRENT shadow evidence."
        ),
        population_definition=(
            "CURRENT-epoch completed shadow lifecycles with a valid market "
            "phase, a detected pattern, and an R-multiple outcome. Runner gates "
            "on >=100 phase-labelled trades overall and >=10 per phase×pattern "
            "cell before reporting a cell."
        ),
        metric_definition=(
            "Per (market_phase × pattern) cell mean R (EV) and win rate; "
            "positive-EV cells are identified as phase-appropriate pattern "
            "candidates."
        ),
        evidence_authorities=(
            _auth(
                "shadow_trades",
                producer=EvidenceProducer.SHADOW_TRADES,
                field_path=(
                    "decision_snapshot.market_phase | decision_snapshot.pattern; "
                    "simulated_outcome.pnl_r_multiple"
                ),
                semantic_meaning=(
                    "Phase and pattern decision context joined to the realised "
                    "R-multiple of the same completed shadow lifecycle"
                ),
            ),
        ),
        minimum_sample=100,
        completion_rule=CompletionRule(
            rule_type="sample_reached",
            threshold=100,
            description=(
                "Runner declares INSUFFICIENT_DATA below 100 phase-labelled "
                "CURRENT shadow trades (module _MIN_TOTAL_SAMPLES)."
            ),
        ),
    ),

    # ── M10 — Strategy family required per phase (m10_strategy_family) ───────
    "M10": _override(
        hypothesis=(
            "At least one market phase is best served by a different strategy "
            "family (REVERSAL/CONTINUATION/BREAKOUT) than others, i.e. family "
            "selection is phase-dependent in CURRENT shadow evidence."
        ),
        null_hypothesis=(
            "Strategy-family performance does not differ meaningfully across "
            "market phases in CURRENT shadow evidence."
        ),
        population_definition=(
            "CURRENT-epoch completed shadow lifecycles with a valid market "
            "phase, a detected pattern classifiable to a strategy family, and an "
            "R-multiple outcome. Runner gates on >=100 phase-labelled trades "
            "overall and >=10 per phase×family cell before reporting a cell."
        ),
        metric_definition=(
            "Per (market_phase × strategy_family) cell mean R and win rate; "
            "family dominance per phase is assessed from the populated cells."
        ),
        evidence_authorities=(
            _auth(
                "shadow_trades",
                producer=EvidenceProducer.SHADOW_TRADES,
                field_path=(
                    "decision_snapshot.market_phase | decision_snapshot.pattern "
                    "(family derived via STRATEGY_FAMILIES mapping); "
                    "simulated_outcome.pnl_r_multiple"
                ),
                semantic_meaning=(
                    "Phase and pattern decision context (pattern mapped to a "
                    "strategy family) joined to the realised R-multiple of the "
                    "same completed shadow lifecycle"
                ),
            ),
        ),
        minimum_sample=100,
        completion_rule=CompletionRule(
            rule_type="sample_reached",
            threshold=100,
            description=(
                "Runner declares INSUFFICIENT_DATA below 100 phase-labelled "
                "CURRENT shadow trades (module _MIN_TOTAL_SAMPLES)."
            ),
        ),
    ),

    # ── D1 — Scoring components predict R (component_reward.run) ─────────────
    "D1": _override(
        hypothesis=(
            "One or more of the 10 decision scoring components are correlated "
            "with R-multiple outcome (predictive value distinct from zero) when "
            "joined to matched shadow outcomes."
        ),
        null_hypothesis=(
            "No decision scoring component shows predictive value (mean R when "
            "high vs when low) distinct from zero."
        ),
        population_definition=(
            "CURRENT-epoch decision traces with component scores joined to "
            "matched shadow outcome records by lineage key (correlation_id "
            "primary; entity_id + cycle_id fallback); ambiguous or conflicting "
            "lineage is rejected."
        ),
        metric_definition=(
            "Per-component predictive value = mean R when the component score is "
            "above median minus mean R when at/below median; supporting metrics "
            "are Pearson correlation of the component score with R and win rate "
            "when the component is active."
        ),
        evidence_authorities=(
            _auth(
                "decision_trace",
                producer=EvidenceProducer.DECISION_TRACE,
                field_path="components.* | score_strategy | score_neutral",
                semantic_meaning=(
                    "Pre-decision component scores of the decision that produced "
                    "the opportunity"
                ),
            ),
            _auth(
                "shadow_trades",
                producer=EvidenceProducer.SHADOW_TRADES,
                field_path="simulated_outcome.pnl_r_multiple | r_multiple",
                semantic_meaning=(
                    "Realised R-multiple of the matched completed shadow "
                    "lifecycle"
                ),
            ),
        ),
        join_contract=JoinContract(
            join_keys=("correlation_id", "entity_id", "cycle_id"),
            cardinality="many_to_one",
            conflict_policy="reject",
            description=(
                "Decision-trace component scores joined to the shadow outcome by "
                "correlation_id (primary) with entity_id + cycle_id fallback; "
                "ambiguous/conflicting lineage rejected."
            ),
        ),
        completion_rule=CompletionRule(
            rule_type="report_exists",
            description=(
                "Runner reports COMPLETE when at least one decision has a "
                "matched shadow outcome; INSUFFICIENT_DATA when none."
            ),
        ),
    ),

    # ── X1 — Slippage model per symbol per session (execution_protection) ────
    "X1": _override(
        hypothesis=(
            "Measured execution slippage varies by symbol and session (a "
            "non-constant model) in CURRENT execution evidence."
        ),
        null_hypothesis=(
            "Measured execution slippage is constant/negligible across symbols "
            "and sessions in CURRENT execution evidence."
        ),
        population_definition=(
            "CURRENT-epoch execution_results_v1 fills carrying "
            "producer-measured execution slippage, each joined to its "
            "execution_context_v1 session by correlation_id (one context per "
            "correlation; ambiguous lineage or missing context excluded). "
            "Readiness requires >=30 measured-slippage records."
        ),
        metric_definition=(
            "Measured slippage distribution (mean/median/p75) overall and per "
            "(symbol × session) cell with cell n >= 10; only producer-measured "
            "execution slippage counts (derived/unknown provenance excluded)."
        ),
        evidence_authorities=(
            _auth(
                "execution_results_v1",
                producer=EvidenceProducer.EXECUTION_RESULTS,
                field_path="slippage (slippage_semantic=measured_execution_slippage)",
                semantic_meaning=(
                    "Execution-time measured slippage recorded by the producer "
                    "for a successful fill"
                ),
            ),
            _auth(
                "execution_context",
                producer=None,
                field_path="market_access.session_state",
                semantic_meaning=(
                    "Pre-execution session state snapshot of the same decision "
                    "cycle"
                ),
            ),
        ),
        join_contract=JoinContract(
            join_keys=("correlation_id",),
            cardinality="many_to_one",
            conflict_policy="reject",
            description=(
                "Each execution_context_v1 session maps to account-grained "
                "execution_results_v1 fills by correlation_id; one context per "
                "correlation and consistent lineage required, ambiguous matches "
                "rejected."
            ),
        ),
        minimum_sample=30,
        completion_rule=CompletionRule(
            rule_type="sample_reached",
            threshold=30,
            description=(
                "Runner declares COMPLETE only when >=30 measured-slippage "
                "records with matched execution context exist (module "
                "_MIN_SAMPLE); below that INSUFFICIENT_DATA."
            ),
        ),
    ),

    # ── X4 — Edge lost in execution (shadow_validation.run) ──────────────────
    "X4": _override(
        hypothesis=(
            "Shadow R-multiples are positively correlated with realised live "
            "R-multiples for matched trades (the shadow model predicts live "
            "outcomes)."
        ),
        null_hypothesis=(
            "Shadow R-multiples show no correlation with live R-multiples for "
            "matched trades."
        ),
        population_definition=(
            "Matched pairs of CURRENT-epoch shadow lifecycles (canonical "
            "shadow_runtime_v1 ingestion, primary-horizon simulation) and "
            "trade_truth_v1 realised outcomes, joined one-to-one by "
            "canonical_opportunity_id; unmatched or ambiguous pairs excluded."
        ),
        metric_definition=(
            "Pearson correlation between shadow and live R-multiples, mean "
            "absolute error, average prediction error, and directional accuracy "
            "over the matched-pair population."
        ),
        evidence_authorities=(
            _auth(
                "shadow_trades",
                producer=EvidenceProducer.SHADOW_TRADES,
                field_path="simulated_outcome.pnl_r_multiple | r_multiple",
                semantic_meaning=(
                    "Shadow-predicted R-multiple of the completed shadow "
                    "lifecycle"
                ),
            ),
            _auth(
                "trade_truth",
                producer=EvidenceProducer.TRADE_TRUTH,
                field_path="outcome.r_multiple_realised | live_r_multiple",
                semantic_meaning=(
                    "Realised live R-multiple recorded in trade_truth_v1"
                ),
            ),
        ),
        join_contract=JoinContract(
            join_keys=("canonical_opportunity_id",),
            cardinality="one_to_one",
            conflict_policy="reject",
            description=(
                "One-to-one match of a completed shadow lifecycle to its "
                "trade_truth_v1 realised outcome by canonical_opportunity_id; "
                "unmatched/ambiguous pairs rejected."
            ),
        ),
        completion_rule=CompletionRule(
            rule_type="report_exists",
            description=(
                "Runner reports COMPLETE when at least one matched "
                "shadow/live pair exists; INSUFFICIENT_DATA (or a BLOCKED "
                "structural-gate report) otherwise."
            ),
        ),
    ),

    # ── EX3 — Optimal TP distance via MFE reachability (exit_management) ─────
    "EX3": _override(
        hypothesis=(
            "The MFE reachability profile is informative for take-profit "
            "distance selection: the fraction of trades reaching >= T R declines "
            "with distance and reaches economically meaningful levels at one or "
            "more thresholds in [0.25R, 3.0R]."
        ),
        null_hypothesis=(
            "MFE reachability is negligible at all tested take-profit distance "
            "thresholds in [0.25R, 3.0R]."
        ),
        population_definition=(
            "CURRENT-epoch completed shadow lifecycles from canonical "
            "shadow_runtime_v1 ingestion carrying both MFE and MAE path facts "
            "(records missing MFE or MAE are excluded); R-multiple used where "
            "present."
        ),
        metric_definition=(
            "Reachability = fraction of trades whose MFE >= threshold T for T in "
            "{0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0}R; MFE distribution "
            "(mean/median/p75) and current TP hit rate (exit_reason == "
            "take_profit)."
        ),
        evidence_authorities=(
            _auth(
                "shadow_trades",
                producer=EvidenceProducer.SHADOW_TRADES,
                field_path="simulated_outcome.mfe_r",
                semantic_meaning=(
                    "Maximum favourable excursion in R over the completed shadow "
                    "lifecycle (observed path fact)"
                ),
            ),
        ),
        minimum_sample=200,
        completion_rule=CompletionRule(
            rule_type="sample_reached",
            threshold=200,
            description=(
                "Registry readiness and runner both require >=200 CURRENT "
                "shadow exit records for a COMPLETE report (validation rule "
                "sample_size >= 200 / module _MIN_SAMPLE_STRICT)."
            ),
        ),
    ),

    # ── EX4 — Optimal SL distance / signal quality (exit_management) ─────────
    "EX4": _override(
        hypothesis=(
            "The current SL distance preserves signal quality: eventual winners "
            "survive materially deeper adverse excursions than eventual losers "
            "in CURRENT shadow evidence."
        ),
        null_hypothesis=(
            "Winner and loser adverse-excursion (MAE) profiles are "
            "indistinguishable, indicating the SL distance does not discriminate "
            "signal from noise."
        ),
        population_definition=(
            "CURRENT-epoch completed shadow lifecycles from canonical "
            "shadow_runtime_v1 ingestion carrying both MFE and MAE path facts "
            "(records missing MFE or MAE are excluded); R-multiple is used to "
            "classify each trade as winner or loser."
        ),
        metric_definition=(
            "Adverse-excursion profile = fraction of trades whose MAE <= "
            "threshold T for T in {-0.25, -0.5, -0.75, -1.0}R; winner vs loser "
            "MAE distributions; deep-MAE rate among winners and shallow-MAE rate "
            "among losers."
        ),
        evidence_authorities=(
            _auth(
                "shadow_trades",
                producer=EvidenceProducer.SHADOW_TRADES,
                field_path="simulated_outcome.mae_r",
                semantic_meaning=(
                    "Maximum adverse excursion in R over the completed shadow "
                    "lifecycle (observed path fact)"
                ),
            ),
        ),
        minimum_sample=200,
        completion_rule=CompletionRule(
            rule_type="sample_reached",
            threshold=200,
            description=(
                "Registry readiness and runner both require >=200 CURRENT "
                "shadow exit records for a COMPLETE report (validation rule "
                "sample_size >= 200 / module _MIN_SAMPLE_STRICT)."
            ),
        ),
    ),

    # ── MGMT-1 — Management help/harm (management_research.run_mgmt1) ────────
    "MGMT-1": _override(
        hypothesis=(
            "Managed trades (>=1 management action) and unmanaged trades differ "
            "in realised R-multiple outcomes in CURRENT evidence "
            "(observational association, not causal)."
        ),
        null_hypothesis=(
            "Managed and unmanaged trades show no difference in mean realised "
            "R-multiple in CURRENT evidence."
        ),
        population_definition=(
            "CURRENT-epoch trade_truth_v1 realised outcomes split into managed "
            "(matching at least one management_actions_v1 record by lifecycle "
            "identity) and unmanaged; actions link to outcomes by trade_id "
            "(primary) with canonical_opportunity_id/correlation_id fallbacks "
            "and conflict rejection."
        ),
        metric_definition=(
            "Mean realised R-multiple and win rate for managed vs unmanaged "
            "trades; difference in mean R between the two groups."
        ),
        evidence_authorities=(
            _auth(
                "management_actions",
                producer=None,
                field_path="action_type | trade_id",
                semantic_meaning=(
                    "Recorded discretionary management actions on a lifecycle "
                    "(SLTP_MODIFY / PARTIAL_CLOSE / CLOSE)"
                ),
            ),
            _auth(
                "trade_truth",
                producer=EvidenceProducer.TRADE_TRUTH,
                field_path="outcome.r_multiple_realised",
                semantic_meaning=(
                    "Realised live R-multiple recorded in trade_truth_v1"
                ),
            ),
        ),
        join_contract=JoinContract(
            join_keys=("trade_id", "canonical_opportunity_id", "correlation_id"),
            cardinality="many_to_one",
            conflict_policy="reject",
            description=(
                "Management actions linked to trade_truth_v1 outcomes by the "
                "strongest available lifecycle identity with conflict rejection; "
                "an action marks its trade managed."
            ),
        ),
        minimum_sample=30,
        completion_rule=CompletionRule(
            rule_type="sample_reached",
            threshold=30,
            description=(
                "Runner declares COMPLETE only when >=30 CURRENT managed+"
                "unmanaged outcome records exist (module _MIN_SAMPLE_MGMT1)."
            ),
        ),
    ),

    # ── MGMT-2 — Which action types appear helpful/harmful/neutral ───────────
    "MGMT-2": _override(
        hypothesis=(
            "Different management action types (SLTP_MODIFY, PARTIAL_CLOSE, "
            "CLOSE) are associated with different realised R-multiple outcomes "
            "in CURRENT evidence (observational)."
        ),
        null_hypothesis=(
            "No management action type is associated with a materially different "
            "mean realised R-multiple in CURRENT evidence."
        ),
        population_definition=(
            "CURRENT-epoch management_actions_v1 records joined one-to-one to "
            "trade_truth_v1 realised outcomes by lifecycle identity (trade_id "
            "primary), deduplicated per (trade_id, action_type); each action is "
            "classified discretionary vs lifecycle-bookkeeping by action_reason."
        ),
        metric_definition=(
            "Per-action-type mean realised R-multiple, win rate and sample size; "
            "discretionary vs bookkeeping comparison."
        ),
        evidence_authorities=(
            _auth(
                "management_actions",
                producer=None,
                field_path="action_type | action_reason | trade_id",
                semantic_meaning=(
                    "Recorded management action type and reason on a lifecycle"
                ),
            ),
            _auth(
                "trade_truth",
                producer=EvidenceProducer.TRADE_TRUTH,
                field_path="outcome.r_multiple_realised",
                semantic_meaning=(
                    "Realised live R-multiple recorded in trade_truth_v1"
                ),
            ),
        ),
        join_contract=JoinContract(
            join_keys=("trade_id", "canonical_opportunity_id", "correlation_id"),
            cardinality="many_to_one",
            conflict_policy="reject",
            description=(
                "Management actions joined to trade_truth_v1 outcomes by the "
                "strongest available lifecycle identity with conflict rejection, "
                "deduplicated per (trade_id, action_type)."
            ),
        ),
        minimum_sample=15,
        completion_rule=CompletionRule(
            rule_type="sample_reached",
            threshold=15,
            description=(
                "Runner declares COMPLETE when at least one action-type group "
                "has n >= 15 matched action-outcome records (registry rule "
                "max_action_type_sample_size >= 15; module _MIN_SAMPLE_MGMT2)."
            ),
        ),
    ),

    # ── STRAT-1 — Confidence predicts outcome quality (selection_research) ───
    "STRAT-1": _override(
        hypothesis=(
            "Pre-decision strategy-ranking confidence is associated with "
            "subsequent primary-horizon shadow outcome quality (positive "
            "monotonic relationship) in CURRENT evidence."
        ),
        null_hypothesis=(
            "Pre-decision strategy-ranking confidence and primary-horizon shadow "
            "outcome R show no monotonic association in CURRENT evidence."
        ),
        population_definition=(
            "Selected strategy_candidates_v1 records (selected == true, "
            "deduplicated by candidate_id) joined to the PRIMARY_HORIZON_"
            "SIMULATION shadow outcome of the same canonical_opportunity_id; "
            "opportunities with zero or multiple outcomes are excluded."
        ),
        metric_definition=(
            "Spearman rank correlation between confidence and R-multiple; "
            "per-confidence-bucket mean R (bucket n >= 15) monotonicity "
            "direction."
        ),
        evidence_authorities=(
            _auth(
                "strategy_candidates",
                producer=None,
                field_path="confidence | rank | selected | canonical_opportunity_id",
                semantic_meaning=(
                    "Pre-decision strategy-selection confidence/rank persisted "
                    "before any outcome exists"
                ),
            ),
            _auth(
                "shadow_trades",
                producer=EvidenceProducer.SHADOW_TRADES,
                field_path="simulated_outcome.pnl_r_multiple (PRIMARY_HORIZON_SIMULATION)",
                semantic_meaning=(
                    "Primary-horizon shadow R-multiple of the same canonical "
                    "opportunity (post-decision research evidence)"
                ),
            ),
        ),
        join_contract=JoinContract(
            join_keys=("canonical_opportunity_id",),
            cardinality="one_to_one",
            conflict_policy="reject",
            description=(
                "Each selected strategy candidate joins to the primary-horizon "
                "shadow outcome of the same canonical opportunity; only "
                "candidates with exactly one outcome are retained."
            ),
        ),
        minimum_sample=30,
        completion_rule=CompletionRule(
            rule_type="sample_reached",
            threshold=30,
            description=(
                "Runner declares COMPLETE only when >=30 selected-candidate/"
                "opportunity matches with confidence and a primary shadow "
                "outcome exist (module _MIN_SAMPLE)."
            ),
        ),
    ),

    # ── PROT1 — Protection integrity (execution_protection.run_prot1) ────────
    "PROT1": _override(
        hypothesis=(
            "Positions retain the protection the system intended: "
            "broker-confirmed SL/TP match the requested SL/TP for the large "
            "majority of protection audits in CURRENT evidence."
        ),
        null_hypothesis=(
            "Broker-confirmed protection levels diverge materially from "
            "requested SL/TP across protection audits in CURRENT evidence."
        ),
        population_definition=(
            "CURRENT-epoch protection_audit_v1 post-fill verification records "
            "(one per audited position)."
        ),
        metric_definition=(
            "SL present/match rate and TP present/match rate against requested "
            "levels (0.0001 price-unit tolerance), protection_status "
            "distribution, protection failure reasons, and correction success "
            "count."
        ),
        evidence_authorities=(
            _auth(
                "protection_audit_v1",
                producer=EvidenceProducer.PROTECTION_AUDIT,
                field_path=(
                    "requested_sl | broker_confirmed_sl | requested_tp | "
                    "broker_confirmed_tp | protection_status"
                ),
                semantic_meaning=(
                    "Post-fill verification of broker-confirmed SL/TP against "
                    "requested protection levels"
                ),
            ),
        ),
        minimum_sample=30,
        completion_rule=CompletionRule(
            rule_type="sample_reached",
            threshold=30,
            description=(
                "Runner declares COMPLETE only when >=30 protection_audit_v1 "
                "records exist (module _MIN_SAMPLE); below that "
                "INSUFFICIENT_DATA."
            ),
        ),
    ),
}


def apply_wave_a1_definitions(
    definitions: dict[str, ResearchQuestionDefinition],
) -> dict[str, ResearchQuestionDefinition]:
    """Return a new definitions dict with Wave A1 target metadata applied.

    Only the successfully-closed Wave A1 targets are enriched. Unresolved
    targets (EX1/EX2/EX10/L7) and all non-target definitions are returned
    unchanged so the validator remains fail-closed for them.
    """
    result = dict(definitions)
    for qid, overrides in WAVE_A1_OVERRIDES.items():
        base = result[qid]
        result[qid] = replace(base, **overrides)
    return result









