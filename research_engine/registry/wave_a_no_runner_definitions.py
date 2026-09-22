"""Proposed operational designs for the eight Wave-A NO_RUNNER questions.

This module is deliberately non-operational.  It records implementation-ready
scientific contracts without adding a runner, report mapping, readiness rule,
or evidence-resolver behaviour to the canonical registry.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from research_engine.registry.research_question_models import ResearchQuestionDefinition


ALREADY_AVAILABLE = "ALREADY_AVAILABLE"
DERIVABLE = "DERIVABLE"
NEW_RESEARCH_EVIDENCE = "NEW_RESEARCH_EVIDENCE"
EXISTING_V1_CONTRACT_VIOLATION = "EXISTING_V1_CONTRACT_VIOLATION"
EVIDENCE_GAP_CLASSIFICATIONS = frozenset({
    ALREADY_AVAILABLE,
    DERIVABLE,
    NEW_RESEARCH_EVIDENCE,
    EXISTING_V1_CONTRACT_VIOLATION,
})


@dataclass(frozen=True)
class ProposedEvidence:
    """One proposed authority, with its relationship to frozen V1 evidence."""

    dataset: str
    fields: tuple[str, ...]
    semantic_authority: str
    gap_classification: str


@dataclass(frozen=True)
class ProposedJoin:
    """A deterministic future join; ambiguity always fails closed."""

    left: str
    right: str
    keys: tuple[str, ...]
    cardinality: str
    conflict_policy: str
    purpose: str


@dataclass(frozen=True)
class RunnerSpecification:
    """Implementation specification only; module/function do not exist yet."""

    proposed_module: str
    proposed_function: str
    inputs: tuple[str, ...]
    filters: tuple[str, ...]
    unit_of_analysis: str
    joins: tuple[str, ...]
    grouping: tuple[str, ...]
    metrics: tuple[str, ...]
    sufficiency: str
    output: str
    completion_rule: str
    fail_closed_conditions: tuple[str, ...]


@dataclass(frozen=True)
class NoRunnerDesign:
    """A proposed contract that cannot be mistaken for active authority."""

    canonical_question_id: str
    canonical_intent: str
    hypothesis: str
    null_hypothesis: str
    research_classification: str
    population_definition: str
    unit_of_analysis: str
    metric_definition: str
    evidence_authority: tuple[ProposedEvidence, ...]
    join_contract: tuple[ProposedJoin, ...]
    epoch_requirement: str
    minimum_sample: str
    cell_sufficiency: str
    completion_criterion: str
    dependencies: tuple[str, ...]
    runner_specification: RunnerSpecification
    report_identity: str
    multi_account_rule: str
    repeated_measure_rule: str
    leakage_rule: str
    evidence_gap_classification: str
    existing_evidence_sufficient_in_principle: bool
    implementation_requirements: tuple[str, ...]
    human_semantic_decisions: tuple[str, ...]
    definition_version: int = 1
    lifecycle: str = "PROPOSED"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Repairs 2B.2/2B.3/2B.4 implemented S5/S6/S7 and Repair 4B.4 implemented X6.
# Their frozen design records below remain the authoritative HD06/HD08
# scientific contracts; L6/G1/G2/G3 remain unimplemented.
WAVE_A_NO_RUNNER_TARGETS = frozenset({"L6", "G1", "G2", "G3"})

# Human-adjudicated HD07 scientific contract.  This is definition/governance
# authority only: it does not declare a runner, active report, or operational
# completion for S7.
S7_HD07_ADJUDICATED_CONTRACT: dict[str, Any] = {
    "status": "ADJUDICATED",
    "scientific_question": (
        "Test whether StrategyFamily expectancy in simulated R depends on "
        "evaluated trade horizon (deviation from additivity)."
    ),
    "evidence_pipeline": (
        "shadow_runtime_v1",
        "canonical_lifecycle_reconstruction",
        "normalized_CURRENT_shadow_trades_v1",
        "eligible_StrategyFamily_x_horizon_rows",
        "canonical_opportunity_clustered_analysis",
    ),
    "outcome_field": "simulated_outcome.pnl_r_multiple",
    "strategy_field": "identity.strategy_id",
    "strategy_families": (
        "LIQUIDITY_SWEEP_REVERSAL",
        "FALSE_BREAK",
        "TREND_CONTINUATION",
        "BREAKOUT_EXPANSION",
        "MEAN_REVERSION",
        "RANGE_REACTION",
    ),
    "excluded_strategy_family": "NONE",
    "historical_strategy_mapping_inferred": False,
    "horizon_field": "identity.evaluated_horizon",
    "horizons": ("SCALP", "INTRADAY", "EXTENDED"),
    "horizon_authority": "core.horizon.horizon_models.TradeHorizon",
    "cluster_field": "identity.canonical_opportunity_id",
    "repeated_measure_rule": (
        "Horizon simulations within one canonical opportunity are repeated "
        "measurements, never independent trades."
    ),
    "opportunity_weight": "w_oh = 1 / k_o",
    "opportunity_total_weight": 1,
    "model": "R ~ StrategyFamily + Horizon + StrategyFamily:Horizon",
    "coding": "deterministic reference coding consistent with S5/S6",
    "full_grid_shape": (6, 3),
    "full_grid_interaction_df": 10,
    "subgrid_interaction_df": "(F - 1) * (H - 1)",
    "global_null": (
        "All StrategyFamily x horizon interaction terms for the evaluated "
        "sufficient grid are jointly zero."
    ),
    "alternative": "At least one interaction component differs from zero.",
    "covariance": "canonical-opportunity clustered sandwich",
    "bread": "A = X' W X",
    "cluster_score": "s_o = sum_rows_in_o(w_oh * x_oh * residual_oh)",
    "covariance_formula": "A^-1 (sum_o s_o s_o') A^-1",
    "omnibus_test": "deterministic cluster-robust Wald-type joint interaction-block test",
    "omnibus_alpha": 0.05,
    "followup_gate": "omnibus p <= 0.05",
    "followup_family": "within-strategy pairwise horizon contrasts only",
    "full_grid_followup_count": 18,
    "subgrid_followup_count": "F * C(H, 2)",
    "forbidden_followups": (
        "pairwise strategies within horizons",
        "all cells against each other",
        "best-cell rankings",
        "arbitrary post-hoc contrasts",
    ),
    "multiplicity_method": "Holm step-down family-wise error-rate control",
    "multiplicity_family": "one global family across all predeclared follow-up contrasts",
    "followup_alpha": 0.05,
    "omnibus_in_holm_family": False,
    "minimum_distinct_opportunities_overall": 150,
    "minimum_distinct_opportunities_per_included_cell": 30,
    "minimum_grid": (2, 2),
    "grid_objective": (
        "maximize included valid cells",
        "maximize included StrategyFamily values",
        "maximize included horizons",
        "tie-break by canonical StrategyFamily order then canonical horizon order",
    ),
    "grid_selection_inputs": "pre-outcome identity and sufficiency counts only",
    "grid_selection_forbidden_inputs": (
        "pnl_r_multiple",
        "estimated expectancy",
        "interaction strength",
        "p-values",
        "any outcome-derived quantity",
    ),
    "claim_boundary": "claims apply only to the selected sufficient evaluated grid",
    "estimability_rule": (
        "The full-factorial model and interaction covariance block must be "
        "estimable; interaction terms may not be silently dropped."
    ),
    "contrast_variance": "c' V_cluster c",
    "contrast_interval_95": "estimate +/- 1.96 * cluster-robust SE",
    "complete_results": ("RELIABLE_INTERACTION", "NO_RELIABLE_INTERACTION"),
    "insufficient_result": "INSUFFICIENT_EVIDENCE",
    "provenance_requirements": (
        "CURRENT selection before analysis",
        "explicit stale/incompatible exclusions",
        "exact analytical subset attestation",
        "deterministic order-invariant digest",
        "analytical change sensitivity",
        "invalid subset/provenance fails closed",
    ),
    "proposed_module": "research_engine.experiments.strategy_horizon_interaction",
    "proposed_function": "run_s7",
    "report_identity": "s7_strategy_horizon_interaction.json",
}


# HD08 authority for X6, implemented by Repair 4B.4 exactly from this contract:
# the registry now declares research_engine.experiments.execution_stability.run_x6
# as the canonical X6 runner and x6_execution_stability.json as its report.
X6_HD08_ADJUDICATED_CONTRACT: dict[str, Any] = {
    "definition_version": 1,
    "status": "ADJUDICATED",
    "implementation_blocked_until_decision": False,
    "canonical_question": (
        "Under what conditions (symbol, session, spread, volatility) does "
        "execution quality degrade?"
    ),
    "research_classification": "descriptive associative execution diagnostics",
    "evidence_components": (
        "CURRENT execution_results_v1",
        "CURRENT execution_context",
        "CURRENT decision_trace_v1",
    ),
    "unit_of_analysis": "one distinct account execution result",
    "cluster_field": "correlation_id",
    "primary_endpoint": "producer-measured absolute slippage",
    "primary_slippage_semantic": "measured_execution_slippage",
    "secondary_endpoints": ("explicit result_ok failure rate", "retcode mix"),
    "composite_endpoint_allowed": False,
    "spread_authority": "execution_context.market_access.spread_atr_ratio",
    "spread_bands": {
        "LOW": "spread_atr_ratio < 0.10",
        "NORMAL": "0.10 <= spread_atr_ratio < 0.25",
        "HIGH": "spread_atr_ratio >= 0.25",
        "MISSING_INVALID": "missing/non-finite/invalid spread_atr_ratio",
    },
    "raw_spread_substitution_allowed": False,
    "raw_spread_descriptive_only": True,
    "volatility_authority": "decision_trace_v1.v10_market_state.regime.volatility_state",
    "volatility_authority_rationale": (
        "The V10 RegimeState is the canonical classified regime/volatility layer "
        "consumed by the horizon engine; h4.volatility_state is its upstream observation."
    ),
    "future_or_outcome_derived_volatility_allowed": False,
    "condition_dimensions": (
        "canonical symbol",
        "session",
        "spread band",
        "pre-decision volatility band",
    ),
    "dimension_analysis": "individual dimensions; no unrestricted combinatorial interactions",
    "minimum_matched_account_results": 100,
    "minimum_distinct_decisions": 30,
    "cell_minimum_account_results": 30,
    "cell_minimum_distinct_decisions": 10,
    "minimum_comparable_sufficient_cells_per_claimed_dimension": 2,
    "sparse_dimension_policy": "report explicit INSUFFICIENT status; never omit silently",
    "completion_criterion": (
        "All three CURRENT components and strict joins are valid; overall result and "
        "decision thresholds pass; measured slippage is estimable; every declared "
        "dimension is sufficient or explicitly insufficient; and at least one dimension "
        "contains two comparable sufficient cells."
    ),
    "null_result_can_complete": True,
    "insufficient_overall_result": "WAITING_DATA / INSUFFICIENT_DATA",
    "proposed_module": "research_engine.experiments.execution_stability",
    "proposed_function": "run_x6",
    "report_identity": "x6_execution_stability.json",
    "structurally_operational": True,
}

_SHADOW_EVIDENCE = (
    ProposedEvidence(
        dataset="shadow_runtime_v1/research_shadow_trades",
        fields=(
            "identity.canonical_opportunity_id",
            "identity.strategy_id",
            "identity.evaluated_horizon",
            "simulated_outcome.pnl_r_multiple",
            "lifecycle.status",
        ),
        semantic_authority=(
            "Completed shadow lifecycle outcome at a declared strategy and horizon; "
            "it is not an actual broker execution."
        ),
        gap_classification=ALREADY_AVAILABLE,
    ),
)

_SHADOW_JOIN = ProposedJoin(
    left="shadow OPEN identity",
    right="shadow CLOSE simulated_outcome",
    keys=("shadow_trade_id", "canonical_opportunity_id", "evaluated_horizon"),
    cardinality="one_to_one lifecycle",
    conflict_policy="reject missing, duplicate, or contradictory lifecycle members",
    purpose="construct one completed horizon outcome without convenience aliases",
)


WAVE_A_NO_RUNNER_DESIGNS: dict[str, NoRunnerDesign] = {
    "S5": NoRunnerDesign(
        canonical_question_id="S5",
        canonical_intent=(
            "Determine which canonical REVERSAL, CONTINUATION, and FALSE_BREAK strategy identities "
            "have positive expectancy after accounting for evaluated horizon."
        ),
        hypothesis="At least one StrategyFamily has positive horizon-adjusted mean simulated R.",
        null_hypothesis="No StrategyFamily has positive horizon-adjusted mean simulated R.",
        research_classification="associative comparative",
        population_definition=(
            "Validity-approved CURRENT completed shadow lifecycles with an authoritative StrategyFamily, "
            "evaluated horizon, canonical opportunity root, and simulated R outcome."
        ),
        unit_of_analysis=(
            "One canonical opportunity, with its horizon rows treated as repeated simulated measurements "
            "and total opportunity weight fixed at one."
        ),
        metric_definition=(
            "Per-family mean and median simulated R, win rate, 95% cluster-aware interval, and a "
            "horizon-adjusted family effect; no causal strategy claim."
        ),
        evidence_authority=_SHADOW_EVIDENCE,
        join_contract=(_SHADOW_JOIN,),
        epoch_requirement="Validity-approved CURRENT shadow evidence only; reject mixed epochs.",
        minimum_sample="Proposed: 100 distinct canonical opportunities overall.",
        cell_sufficiency="Proposed: >=30 distinct opportunities per StrategyFamily and >=20 per reported family/horizon cell.",
        completion_criterion=(
            "All three registry StrategyFamily values are classified as supported, not supported, or insufficient; "
            "the adjusted model and intervals are emitted and every reported cell meets sufficiency."
        ),
        dependencies=("canonical StrategyFamily classification", "completed shadow lifecycle authority"),
        runner_specification=RunnerSpecification(
            proposed_module="research_engine.experiments.strategy_identity_expectancy",
            proposed_function="run_s5",
            inputs=("CURRENT completed shadow lifecycles",),
            filters=("authoritative StrategyFamily", "finite simulated R", "valid canonical opportunity root"),
            unit_of_analysis="canonical opportunity clustered across evaluated horizons",
            joins=("OPEN-to-CLOSE shadow lifecycle join",),
            grouping=("StrategyFamily", "evaluated_horizon adjustment stratum"),
            metrics=("mean/median R", "win rate", "cluster-aware interval", "horizon-adjusted family effect"),
            sufficiency=">=100 opportunities; >=30/family; >=20/family-horizon cell",
            output="one family result table plus diagnostics and exclusions",
            completion_rule="all canonical families deterministically classified with sufficient cells",
            fail_closed_conditions=("ambiguous lifecycle", "unknown strategy semantics", "missing opportunity root", "mixed epoch"),
        ),
        report_identity="s5_strategy_identity_expectancy.json",
        multi_account_rule="Broker/account fanout is excluded; it never multiplies strategy evidence.",
        repeated_measure_rule=(
            "Each horizon remains a repeated simulation cell, but horizon rows from one opportunity are "
            "clustered/weighted as one market opportunity and are never independent opportunities."
        ),
        leakage_rule="Only pre-decision strategy/horizon labels are predictors; simulated R is an outcome label only.",
        evidence_gap_classification=DERIVABLE,
        existing_evidence_sufficient_in_principle=True,
        implementation_requirements=(
            "Build the clustered horizon-adjusted runner and unique report.",
            "Add runner/report/readiness mappings without changing shadow evidence semantics.",
            "Publish excluded-row and distinct-opportunity counts.",
        ),
        human_semantic_decisions=(
            "HUMAN_SEMANTIC_DECISION_REQUIRED: approve the adjustment model and provisional sample/cell thresholds.",
        ),
    ),
    "S6": NoRunnerDesign(
        canonical_question_id="S6",
        canonical_intent=(
            "Determine which SCALP, INTRADAY, and EXTENDED evaluated horizons have positive expectancy "
            "after accounting for StrategyFamily."
        ),
        hypothesis="At least one evaluated horizon has positive strategy-adjusted mean simulated R.",
        null_hypothesis="No evaluated horizon has positive strategy-adjusted mean simulated R.",
        research_classification="associative comparative repeated-measures",
        population_definition=(
            "Validity-approved CURRENT canonical opportunities having completed shadow outcomes at declared "
            "evaluated horizons and authoritative StrategyFamily labels."
        ),
        unit_of_analysis="One canonical opportunity with within-opportunity horizon simulations as repeated measurements.",
        metric_definition=(
            "Per-horizon mean/median simulated R, win rate, 95% paired or cluster-aware interval, and a "
            "strategy-adjusted horizon effect."
        ),
        evidence_authority=_SHADOW_EVIDENCE,
        join_contract=(_SHADOW_JOIN,),
        epoch_requirement="Validity-approved CURRENT shadow evidence only; reject mixed epochs.",
        minimum_sample="Proposed: 100 distinct canonical opportunities overall.",
        cell_sufficiency="Proposed: >=30 opportunities per horizon and >=20 per reported strategy/horizon cell.",
        completion_criterion=(
            "All three canonical horizons are classified with cluster-aware uncertainty, all reported cells meet "
            "sufficiency, and strategy adjustment is present."
        ),
        dependencies=("canonical evaluated-horizon authority", "completed shadow lifecycle authority"),
        runner_specification=RunnerSpecification(
            proposed_module="research_engine.experiments.horizon_expectancy",
            proposed_function="run_s6",
            inputs=("CURRENT completed shadow lifecycles",),
            filters=("canonical horizon", "authoritative StrategyFamily", "finite simulated R", "valid opportunity root"),
            unit_of_analysis="canonical opportunity with paired/clustered horizon simulations",
            joins=("OPEN-to-CLOSE shadow lifecycle join",),
            grouping=("evaluated_horizon", "StrategyFamily adjustment stratum"),
            metrics=("mean/median R", "win rate", "paired/cluster-aware interval", "strategy-adjusted horizon effect"),
            sufficiency=">=100 opportunities; >=30/horizon; >=20/strategy-horizon cell",
            output="one horizon result table plus diagnostics and exclusions",
            completion_rule="all canonical horizons deterministically classified with sufficient cells",
            fail_closed_conditions=("unpaired/ambiguous lifecycle", "unknown horizon", "missing opportunity root", "mixed epoch"),
        ),
        report_identity="s6_horizon_expectancy.json",
        multi_account_rule="Account executions are irrelevant and excluded; one opportunity remains one research cluster.",
        repeated_measure_rule=(
            "Horizon rows are within-opportunity repeated simulations, never independent opportunities; paired "
            "comparisons use only opportunities with the required horizon pair."
        ),
        leakage_rule="Horizon and strategy are declared inputs; simulated R and later path facts are outcome labels only.",
        evidence_gap_classification=DERIVABLE,
        existing_evidence_sufficient_in_principle=True,
        implementation_requirements=(
            "Build a paired/clustered strategy-adjusted horizon runner and unique report.",
            "Add runner/report/readiness mappings and report pairwise availability/exclusions.",
        ),
        human_semantic_decisions=(
            "HUMAN_SEMANTIC_DECISION_REQUIRED: approve paired-effect estimator and provisional sample/cell thresholds.",
        ),
    ),
    "S7": NoRunnerDesign(
        canonical_question_id="S7",
        canonical_intent="Test whether strategy profitability depends on the evaluated trade horizon.",
        hypothesis="At least one StrategyFamily-by-horizon interaction in mean simulated R is non-zero.",
        null_hypothesis="StrategyFamily differences in mean simulated R do not vary by evaluated horizon.",
        research_classification="associative interaction repeated-measures",
        population_definition=(
            "Governed shadow_runtime_v1 canonical lifecycle reconstruction normalized to CURRENT "
            "shadow_trades_v1, restricted to eligible authoritative StrategyFamily x evaluated-horizon "
            "rows with canonical_opportunity_id and finite simulated_outcome.pnl_r_multiple."
        ),
        unit_of_analysis="One canonical opportunity clustered across its StrategyFamily-by-horizon simulation cells.",
        metric_definition=(
            "Descriptive cell mean/median R and win rate; a cluster-robust Wald joint test of the full-factorial "
            "interaction block; and, only after omnibus rejection, within-strategy pairwise horizon contrasts "
            "with 95% clustered intervals and one global Holm step-down family."
        ),
        evidence_authority=_SHADOW_EVIDENCE,
        join_contract=(_SHADOW_JOIN,),
        epoch_requirement="Validity-approved CURRENT shadow evidence only; reject mixed epochs.",
        minimum_sample="ADJUDICATED: >=150 distinct canonical opportunities overall.",
        cell_sufficiency=(
            "ADJUDICATED: >=30 distinct opportunities in every included StrategyFamily-by-horizon cell; "
            "deterministically select the maximal valid >=2x2 rectangular grid by cells, families, horizons, "
            "then canonical taxonomy order, using no outcome-derived quantity."
        ),
        completion_criterion=(
            "COMPLETE requires CURRENT provenance, >=150 opportunities, a deterministic estimable >=2x2 grid "
            "with >=30 per included cell, valid opportunity-clustered covariance, and a valid alpha=.05 Wald "
            "omnibus test. Both RELIABLE_INTERACTION and NO_RELIABLE_INTERACTION are complete outcomes; "
            "failed scientific gates are INSUFFICIENT_EVIDENCE. Claims are bounded to the selected grid."
        ),
        dependencies=("S5 operational contract", "S6 operational contract", "completed shadow lifecycle authority"),
        runner_specification=RunnerSpecification(
            proposed_module="research_engine.experiments.strategy_horizon_interaction",
            proposed_function="run_s7",
            inputs=("CURRENT completed shadow lifecycles",),
            filters=("canonical strategy and horizon", "finite simulated R", "valid opportunity root"),
            unit_of_analysis="canonical opportunity cluster",
            joins=("OPEN-to-CLOSE shadow lifecycle join",),
            grouping=("StrategyFamily", "evaluated_horizon", "interaction cell"),
            metrics=(
                "descriptive cell expectancy",
                "full-factorial interaction block",
                "cluster-robust Wald omnibus test at alpha .05",
                "omnibus-gated within-strategy horizon contrasts",
                "one global Holm-adjusted follow-up family",
            ),
            sufficiency=">=150 opportunities; >=30 per included interaction cell; deterministic maximal >=2x2 grid",
            output="interaction matrix, contrasts, uncertainty, insufficiency and exclusions",
            completion_rule=(
                "RELIABLE_INTERACTION or NO_RELIABLE_INTERACTION after valid evaluation; otherwise "
                "INSUFFICIENT_EVIDENCE. All claims are bounded to the selected sufficient grid."
            ),
            fail_closed_conditions=("ambiguous lifecycle", "sparse interaction grid", "unknown labels", "missing root", "mixed epoch"),
        ),
        report_identity="s7_strategy_horizon_interaction.json",
        multi_account_rule="Account fanout is excluded and cannot inflate any strategy-horizon cell.",
        repeated_measure_rule=(
            "All horizons for one opportunity form one repeated-measures canonical_opportunity_id cluster, "
            "not independent trades; each eligible row has weight 1/k_o so opportunity total weight is one."
        ),
        leakage_rule="Only declared strategy/horizon factors enter the model; simulated R and future path are outcomes only.",
        evidence_gap_classification=DERIVABLE,
        existing_evidence_sufficient_in_principle=True,
        implementation_requirements=(
            "Implement the adjudicated opportunity-normalized full-factorial interaction with the exact "
            "clustered sandwich covariance and joint Wald test recorded in S7_HD07_ADJUDICATED_CONTRACT.",
            "Implement the outcome-independent maximal-grid objective, omnibus-gated within-strategy horizon "
            "contrasts, one global Holm family, governed provenance, readiness, and completion contracts.",
        ),
        human_semantic_decisions=(
            "ADJUDICATED HD07: use S7_HD07_ADJUDICATED_CONTRACT; implementation is no longer decision-blocked.",
        ),
    ),
    "X6": NoRunnerDesign(
        canonical_question_id="X6",
        canonical_intent="Identify symbol, session, spread, and pre-execution volatility conditions associated with degraded execution quality.",
        hypothesis="Producer-measured execution quality differs across at least one declared pre-execution condition.",
        null_hypothesis="Producer-measured execution quality does not differ across the declared pre-execution conditions.",
        research_classification="descriptive associative execution diagnostics",
        population_definition=(
            "Validity-approved CURRENT account-grained execution results deterministically matched to their single "
            "pre-decision execution context and, for volatility, single decision trace market-state snapshot."
        ),
        unit_of_analysis="One account execution result; multiple account results from one decision are valid execution observations but remain clustered by correlation_id.",
        metric_definition=(
            "Primary: producer-measured absolute execution slippage. Secondary: explicit result_ok failure rate and "
            "retcode mix; no composite endpoint. Compare individual dimensions for canonical symbol, "
            "session, execution_context spread_atr_ratio bands LOW <0.10, NORMAL [0.10,0.25), HIGH >=0.25, and "
            "decision_trace_v1.v10_market_state.regime.volatility_state without causal claims or unrestricted interactions."
        ),
        evidence_authority=(
            ProposedEvidence(
                dataset="execution_results_v1",
                fields=("correlation_id", "account_id", "broker_symbol", "slippage", "slippage_semantic", "result_ok", "retcode"),
                semantic_authority="Account-specific broker execution result; only producer-measured slippage is authoritative.",
                gap_classification=ALREADY_AVAILABLE,
            ),
            ProposedEvidence(
                dataset="execution_context",
                fields=("correlation_id", "symbol", "timestamp_utc", "market_access.session_state", "market_access.spread", "market_access.spread_atr_ratio"),
                semantic_authority="Immutable pre-execution decision context for session and spread.",
                gap_classification=ALREADY_AVAILABLE,
            ),
            ProposedEvidence(
                dataset="decision_trace_v1",
                fields=("correlation_id", "canonical_opportunity_id", "v10_market_state.regime.volatility_state"),
                semantic_authority=(
                    "Canonical pre-decision classified regime volatility; h4.volatility_state is the upstream "
                    "observation and never takes precedence over the classified RegimeState field."
                ),
                gap_classification=ALREADY_AVAILABLE,
            ),
        ),
        join_contract=(
            ProposedJoin("execution_results_v1", "execution_context", ("correlation_id",), "many account results to one context", "reject ambiguous context or duplicate result grain", "attach immutable session/spread conditions"),
            ProposedJoin("execution_context", "decision_trace_v1", ("correlation_id",), "one_to_one", "reject missing, duplicate, or symbol/root conflict", "attach pre-decision volatility state"),
        ),
        epoch_requirement="All joined records must be validity-approved CURRENT and schema-compatible; reject mixed epochs.",
        minimum_sample="100 matched account execution results overall from at least 30 distinct correlation_ids.",
        cell_sufficiency=">=30 results and >=10 distinct decisions per reported categorical/bin cell; cluster by correlation_id.",
        completion_criterion=(
            "All declared dimensions have either a sufficient estimate or explicit INSUFFICIENT status, join/exclusion "
            "rates are reported, at least one dimension has two comparable sufficient cells, and a valid no-degradation "
            "result may complete without a positive finding."
        ),
        dependencies=("canonical execution-result/context join helper", "producer-measured slippage semantic", "pre-decision volatility authority"),
        runner_specification=RunnerSpecification(
            proposed_module="research_engine.experiments.execution_stability",
            proposed_function="run_x6",
            inputs=("CURRENT execution_results_v1", "CURRENT execution_context", "CURRENT decision_trace_v1"),
            filters=("valid producer-measured result fields", "unique result grain", "unambiguous correlation join", "symbol consistency", "spread_atr_ratio bands only; no raw-spread substitution"),
            unit_of_analysis="account execution result clustered by canonical decision correlation_id",
            joins=("results-to-context correlation join", "context-to-trace correlation join"),
            grouping=("canonical symbol", "session", "spread_atr_ratio band", "decision_trace_v1.v10_market_state.regime.volatility_state"),
            metrics=("producer-measured absolute slippage", "explicit result_ok failure rate", "retcode mix"),
            sufficiency=">=100 results, >=30 decisions; >=30 results and >=10 decisions/cell",
            output="condition tables, uncertainty, join quality, exclusions and cluster counts",
            completion_rule="at least one dimension with two comparable sufficient cells and exhaustive status for all declared dimensions; a valid null may complete",
            fail_closed_conditions=("derived/unknown slippage semantic", "ambiguous join", "symbol conflict", "post-outcome volatility", "mixed epoch", "missing spread_atr_ratio cannot fall back to raw spread"),
        ),
        report_identity="x6_execution_stability.json",
        multi_account_rule=(
            "Account executions are legitimate observations because X6 studies broker execution, but inference and "
            "counts also expose and cluster by the shared canonical decision."
        ),
        repeated_measure_rule="Shadow horizons are not inputs. Repeated account results share a decision cluster and are not independent strategy opportunities.",
        leakage_rule="Session, spread, and volatility must be frozen before execution; outcomes may label degradation but cannot define condition groups.",
        evidence_gap_classification=DERIVABLE,
        existing_evidence_sufficient_in_principle=True,
        implementation_requirements=(
            "Replace the future X6 authority design's non-V1 slippage_journal label with execution_results_v1; this is registry/runner design repair, not a V1 defect.",
            "Build the two-stage strict join, adjudicated spread_atr_ratio bins, individually analysed condition dimensions, clustered analysis, readiness rules, and unique report.",
            "Accept only producer-measured slippage and explicitly map broker symbol to canonical symbol with conflict rejection.",
        ),
        human_semantic_decisions=(
            "ADJUDICATED HD08: use X6_HD08_ADJUDICATED_CONTRACT; implementation is no longer decision-blocked.",
        ),
    ),
    "L6": NoRunnerDesign(
        canonical_question_id="L6",
        canonical_intent="Score the trustworthiness of each current research conclusion from dataset validity, coverage, and sample sufficiency.",
        hypothesis="At least one current conclusion meets a predeclared high-trust rule across validity, coverage, and sample sufficiency.",
        null_hypothesis="No current conclusion meets the predeclared high-trust rule.",
        research_classification="descriptive meta-research confidence assessment",
        population_definition="Validity-approved CURRENT canonical reports/findings with their definition, evidence-readiness, coverage, sample, and report-validity provenance.",
        unit_of_analysis="One canonical question's single validity-approved CURRENT report/finding; duplicate legacy reports do not add observations.",
        metric_definition=(
            "A transparent component vector (definition health, evidence validity, required-field coverage, sample/cell "
            "sufficiency, join quality, report validity) and approved ordinal trust tier; never a causal learning score."
        ),
        evidence_authority=(
            ProposedEvidence("canonical question definitions/states", ("question_id", "definition health", "requirements", "coverage", "sample", "readiness"), "Canonical control-plane metadata, not dashboard presentation state.", DERIVABLE),
            ProposedEvidence("canonical CURRENT reports/findings", ("question_id", "validity status", "sample/cell counts", "finding provenance"), "Only validity-approved CURRENT scientific conclusions.", DERIVABLE),
        ),
        join_contract=(
            ProposedJoin("canonical definition/state", "CURRENT report/finding", ("canonical_question_id",), "zero_or_one current report per canonical question", "reject duplicate/legacy ambiguity", "attach each conclusion to exactly one canonical identity"),
        ),
        epoch_requirement="Use one immutable CURRENT control-plane snapshot; historical reports are excluded unless explicitly displayed as history.",
        minimum_sample="At least one validity-approved CURRENT conclusion; each conclusion retains its own canonical minimum sample.",
        cell_sufficiency="A trust tier is emitted only when every required component is known; UNKNOWN is fail-closed, not zero-filled.",
        completion_criterion="Every eligible current conclusion in the snapshot receives a component vector and deterministic tier; zero eligible conclusions is INSUFFICIENT, not COMPLETE.",
        dependencies=("canonical definition validation", "canonical evidence readiness", "canonical report validity and ownership"),
        runner_specification=RunnerSpecification(
            proposed_module="research_engine.experiments.research_confidence",
            proposed_function="run_l6",
            inputs=("immutable canonical question-state snapshot", "validity-approved CURRENT reports/findings"),
            filters=("unique canonical ownership", "CURRENT", "valid report", "exclude L6 self-result from scoring"),
            unit_of_analysis="one current canonical conclusion",
            joins=("question_id-only state-to-report join",),
            grouping=("question", "family summary secondary only"),
            metrics=("validity/coverage/sample/join/report component vector", "approved trust tier"),
            sufficiency="all components known and source question's own sufficiency contract satisfied",
            output="per-conclusion trust table, exclusions, snapshot manifest and summary",
            completion_rule="all eligible conclusions scored; noneligible questions explicitly excluded",
            fail_closed_conditions=("ambiguous report owner", "invalid/superseded report", "unknown component", "self-reference", "mixed snapshot"),
        ),
        report_identity="l6_research_confidence.json",
        multi_account_rule="Inherit each source question's grain; never recount account rows inside a conclusion-level trust assessment.",
        repeated_measure_rule="Inherit and audit each source contract's horizon rule; repeated horizons never become extra confidence observations.",
        leakage_rule="Post-outcome fields may support the source conclusion but cannot be reused as predictive inputs; L6 only assesses declared provenance.",
        evidence_gap_classification=DERIVABLE,
        existing_evidence_sufficient_in_principle=True,
        implementation_requirements=(
            "Build an immutable control-plane snapshot adapter and per-conclusion scorer with unique report ownership.",
            "Correct L6's future evidence mapping from shadow-only to canonical definitions/states and valid CURRENT reports.",
            "Keep the result advisory and outside production-approval authority.",
        ),
        human_semantic_decisions=(
            "HUMAN_SEMANTIC_DECISION_REQUIRED: approve trust components, tier thresholds, weighting, and treatment of warnings.",
        ),
    ),
    "G1": NoRunnerDesign(
        canonical_question_id="G1",
        canonical_intent="Assess whether current evidence is suitable for every intended canonical research question by source, field coverage, joinability, and sample sufficiency.",
        hypothesis="At least one canonical question has CURRENT evidence satisfying all declared data-suitability requirements.",
        null_hypothesis="No canonical question has CURRENT evidence satisfying all declared data-suitability requirements.",
        research_classification="descriptive data-governance audit",
        population_definition="The frozen 70-question baseline and each question's declared evidence/readiness requirements evaluated against validity-approved CURRENT evidence metrics.",
        unit_of_analysis="One canonical question suitability assessment; the global summary is not a statistical independent sample.",
        metric_definition="Per-question source availability, required-field coverage, epoch eligibility, deterministic join coverage, distinct-grain sample/cell sufficiency, and suitability status.",
        evidence_authority=(
            ProposedEvidence("canonical registry/definitions", ("question_id", "sources", "required fields", "joins", "minimum sample", "cell rules"), "Intended-question and requirement authority.", ALREADY_AVAILABLE),
            ProposedEvidence("canonical evidence resolver/readiness metrics", ("source validity", "field coverage", "lineage coverage", "distinct sample/cell counts", "epoch"), "CURRENT evidence availability and sufficiency authority.", DERIVABLE),
        ),
        join_contract=(
            ProposedJoin("canonical question definition", "evidence/readiness metrics", ("canonical_question_id", "evidence requirement identity"), "one_to_many requirements", "reject unknown or duplicate requirement metrics", "evaluate every declared evidence requirement"),
        ),
        epoch_requirement="One validity-approved CURRENT evidence snapshot; LEGACY/INVALIDATED/SUPERSEDED evidence cannot establish suitability.",
        minimum_sample="Exactly all 70 frozen baseline question contracts must be assessed; per-question samples come from their own contracts.",
        cell_sufficiency="Every declared requirement receives PASS, FAIL, or INSUFFICIENT with numerator/denominator; UNKNOWN blocks COMPLETE.",
        completion_criterion="All 70 questions and all declared evidence requirements are assessed with provenance and no UNKNOWN requirement; suitability may still be false.",
        dependencies=("canonical definition contracts", "canonical evidence validation and readiness metrics"),
        runner_specification=RunnerSpecification(
            proposed_module="research_engine.experiments.dataset_suitability",
            proposed_function="run_g1",
            inputs=("frozen canonical registry/definitions", "CURRENT evidence/readiness snapshot"),
            filters=("baseline canonical IDs", "valid CURRENT evidence only"),
            unit_of_analysis="canonical question suitability assessment",
            joins=("question-and-requirement identity join",),
            grouping=("canonical question", "question family summary secondary only"),
            metrics=("source/field/join/sample/cell suitability vector",),
            sufficiency="all 70 questions and every declared requirement assessed",
            output="per-question suitability matrix, gaps, provenance and global summary",
            completion_rule="no missing question or UNKNOWN requirement",
            fail_closed_conditions=("missing definition", "unmapped requirement", "mixed epoch", "dashboard-only state"),
        ),
        report_identity="g1_dataset_suitability.json",
        multi_account_rule="Each source question's declared grain controls counts; G1 must flag, not conceal, strategy-sample fanout inflation.",
        repeated_measure_rule="Each source contract's horizon rule controls; G1 audits repeated-measure handling rather than counting horizon rows itself.",
        leakage_rule="G1 assesses data fitness only and makes no predictive, causal, promotion, or production decision.",
        evidence_gap_classification=DERIVABLE,
        existing_evidence_sufficient_in_principle=True,
        implementation_requirements=(
            "Build a read-only requirement evaluator and unique report over the frozen baseline.",
            "Correct G1's future mapping from shadow-only to canonical requirement and evidence/readiness authorities.",
            "Represent unimplemented questions as assessed gaps, not silently omit them.",
        ),
        human_semantic_decisions=(
            "HUMAN_SEMANTIC_DECISION_REQUIRED: approve whether global suitability requires every P0 only or all 70; proposed contract reports all 70 and does not collapse failures into a single approval.",
        ),
    ),
    "G2": NoRunnerDesign(
        canonical_question_id="G2",
        canonical_intent="Measure the percentage of eligible trade opportunities with valid one-to-one decision-to-outcome lineage through entity identity.",
        hypothesis="The true valid-lineage proportion meets or exceeds the registry threshold of 50%.",
        null_hypothesis="The true valid-lineage proportion is below 50%.",
        research_classification="descriptive lineage-quality audit",
        population_definition="Validity-approved CURRENT canonical opportunities represented in completed shadow outcomes and eligible decision traces.",
        unit_of_analysis="One canonical opportunity; account executions and repeated shadow horizons never enlarge the denominator.",
        metric_definition="Valid one-to-one lineage opportunities divided by all eligible distinct canonical opportunities, with exact confidence interval and missing/ambiguous/conflicting reason counts.",
        evidence_authority=(
            ProposedEvidence("shadow_runtime_v1/research_shadow_trades", ("entity_id", "canonical_opportunity_id", "shadow_trade_id", "evaluated_horizon", "simulated_outcome.pnl_r_multiple"), "Completed outcome-side lineage evidence.", ALREADY_AVAILABLE),
            ProposedEvidence("decision_trace_v1", ("entity_id", "canonical_opportunity_id", "correlation_id", "symbol", "timestamp_utc"), "Decision-side lineage evidence.", ALREADY_AVAILABLE),
        ),
        join_contract=(
            ProposedJoin("distinct shadow opportunity roots", "decision_trace_v1", ("entity_id", "canonical_opportunity_id"), "one_to_one opportunity to decision", "reject duplicates, partial keys, and root/symbol conflicts", "validate decision-to-outcome lineage without permissive fallback keys"),
        ),
        epoch_requirement="Both sides validity-approved CURRENT in the same compatible epoch; reject legacy fallback and mixed epochs.",
        minimum_sample="Proposed: >=100 eligible distinct canonical opportunities for an inferential coverage claim.",
        cell_sufficiency="Global denominator is mandatory; proposed diagnostic symbol/session cells require >=30 distinct opportunities and never determine global completion.",
        completion_criterion="Every eligible opportunity is exactly classified valid, missing, ambiguous, or conflicting; denominator, numerator, exclusions, interval, and 50% threshold result are emitted.",
        dependencies=("canonical shadow lifecycle identity", "canonical decision-trace identity"),
        runner_specification=RunnerSpecification(
            proposed_module="research_engine.experiments.lineage_coverage",
            proposed_function="run_g2",
            inputs=("CURRENT completed shadow lifecycles", "CURRENT decision_trace_v1"),
            filters=("distinct canonical opportunities", "complete outcomes", "nonempty authoritative identity"),
            unit_of_analysis="canonical opportunity",
            joins=("strict entity_id plus canonical_opportunity_id join",),
            grouping=("global", "diagnostic symbol/session cells secondary"),
            metrics=("valid-lineage proportion", "exact interval", "failure-reason counts"),
            sufficiency=">=100 distinct opportunities for inferential claim; every denominator row classified",
            output="coverage numerator/denominator, interval, threshold result and lineage-failure table",
            completion_rule="exhaustive deterministic denominator classification with no silent drops",
            fail_closed_conditions=("denominator ambiguity", "duplicate trace", "partial/fallback join", "root conflict", "mixed epoch"),
        ),
        report_identity="g2_lineage_coverage.json",
        multi_account_rule="Account fanout is excluded because G2 measures decision-to-outcome lineage, not broker executions.",
        repeated_measure_rule="Collapse all completed horizon lifecycles to one canonical opportunity before forming the denominator; report horizon inconsistencies as conflicts.",
        leakage_rule="Outcome R is used only to establish an outcome record exists; its value never determines whether lineage is valid.",
        evidence_gap_classification=DERIVABLE,
        existing_evidence_sufficient_in_principle=True,
        implementation_requirements=(
            "Build a strict opportunity-deduplicated lineage audit and unique report.",
            "Replace generic resolver fallback joins with the question-specific composite identity contract inside the future runner.",
            "Add readiness and completion rules for exhaustive denominator classification.",
        ),
        human_semantic_decisions=(
            "HUMAN_SEMANTIC_DECISION_REQUIRED: approve the eligible denominator and whether entity_id alone or the proposed entity_id-plus-root identity is canonical; ambiguity must remain fail-closed.",
            "HUMAN_SEMANTIC_DECISION_REQUIRED: approve the provisional >=100 opportunity threshold; the registry's 50% remains the coverage decision threshold.",
        ),
    ),
    "G3": NoRunnerDesign(
        canonical_question_id="G3",
        canonical_intent="Assess whether current research conclusions are trustworthy from validation status, coverage, lineage, and sample sufficiency.",
        hypothesis="At least one current conclusion satisfies the approved validity rule, and the system-wide trust profile can be computed without unknown inputs.",
        null_hypothesis="No current conclusion satisfies the approved validity rule or the system-wide trust profile contains unknown inputs.",
        research_classification="descriptive global research-governance assessment",
        population_definition="All 69 non-G3 canonical question states in one immutable snapshot, with current conclusions where present and explicit no-result states otherwise.",
        unit_of_analysis="One canonical question trust assessment; the aggregate is one system snapshot, not 69 statistically independent experiments.",
        metric_definition="Counts/rates by definition health, evidence suitability, lineage, sample/cell sufficiency, report validity, and L6 trust tier, plus a preapproved global status that cannot authorize production.",
        evidence_authority=(
            ProposedEvidence("canonical question-state snapshot", ("question_id", "definition health", "readiness", "sample", "coverage", "report validity"), "Canonical control-plane state rather than dashboard state.", DERIVABLE),
            ProposedEvidence("G1/G2/L6 canonical reports", ("dataset suitability", "lineage coverage", "per-conclusion trust"), "Validity-approved CURRENT governance inputs once their runners exist.", NEW_RESEARCH_EVIDENCE),
        ),
        join_contract=(
            ProposedJoin("69 non-G3 canonical states", "G1/G2/L6 CURRENT reports", ("canonical_question_id",), "one governance assessment per canonical identity", "reject missing provenance, duplicate owner, or self-reference", "construct a nonrecursive system trust snapshot"),
        ),
        epoch_requirement="All inputs from the same immutable validity-approved CURRENT snapshot; historical trends are separate output context only.",
        minimum_sample="All 69 non-G3 canonical question states must be represented; absent conclusions remain explicit no-result states.",
        cell_sufficiency="Every question has a known governance classification; UNKNOWN blocks COMPLETE while UNTRUSTED is a valid result.",
        completion_criterion="All 69 non-self states and required G1/G2/L6 inputs are classified with provenance and no recursion; the report may conclude NOT TRUSTWORTHY.",
        dependencies=("G1 operational report", "G2 operational report", "L6 operational report", "canonical question-state snapshot"),
        runner_specification=RunnerSpecification(
            proposed_module="research_engine.experiments.research_validity",
            proposed_function="run_g3",
            inputs=("immutable 69-question state snapshot", "CURRENT valid G1/G2/L6 reports"),
            filters=("exclude G3 self-result", "unique canonical ownership", "same snapshot/epoch"),
            unit_of_analysis="canonical question trust assessment within one global snapshot",
            joins=("canonical_question_id governance join",),
            grouping=("global", "question family diagnostic summary"),
            metrics=("health/readiness/report/trust distributions", "approved global governance status"),
            sufficiency="all 69 non-self question states plus valid G1/G2/L6 inputs; zero UNKNOWN classifications",
            output="global trust profile, per-question reasons, provenance manifest and advisory status",
            completion_rule="complete nonrecursive assessment; a negative trust conclusion is still COMPLETE",
            fail_closed_conditions=("self-reference", "missing governance dependency", "ambiguous report owner", "mixed snapshot", "unknown classification"),
        ),
        report_identity="g3_research_validity.json",
        multi_account_rule="Inherit audited source-question grains; global aggregation never recounts raw account observations.",
        repeated_measure_rule="Inherit and verify source contracts; repeated horizons cannot inflate question-level trust counts.",
        leakage_rule="G3 is advisory governance, not prediction or causal learning, and cannot approve or apply production changes.",
        evidence_gap_classification=NEW_RESEARCH_EVIDENCE,
        existing_evidence_sufficient_in_principle=False,
        implementation_requirements=(
            "First operationalize G1, G2, and L6 as versioned research reports; their absence is a new research-layer requirement, not a frozen V1 violation.",
            "Build a nonrecursive global aggregator and unique report over an immutable snapshot.",
            "Enforce finding-to-candidate-to-shadow-to-recommendation-to-HUMAN APPROVAL governance outside this runner.",
        ),
        human_semantic_decisions=(
            "HUMAN_SEMANTIC_DECISION_REQUIRED: approve the global trust rule and weighting; the runner must publish components even if no scalar is approved.",
        ),
    ),
}


def apply_wave_a_no_runner_designs(
    definitions: dict[str, ResearchQuestionDefinition],
) -> dict[str, ResearchQuestionDefinition]:
    """Record designs separately; preserve all active definitions and mappings."""
    return definitions
