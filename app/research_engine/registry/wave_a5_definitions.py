"""Wave A5 canonical question-ownership classifications.

This module records declarative ownership boundaries for the five target
relationships and applies the adjudicated E3-owner/S1-superseded-alias V1
definition clarification. It does not alter trading/runtime behaviour.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from research_engine.registry.research_question_models import (
    QuestionLifecycle,
    ResearchQuestionDefinition,
)


@dataclass(frozen=True)
class OwnershipRelationship:
    """Deterministic, non-operational ownership assessment for one ID pair."""

    question_ids: tuple[str, str]
    relationship_types: tuple[str, ...]
    scientific_intents: tuple[tuple[str, str], tuple[str, str]]
    scientifically_equivalent: bool
    highest_safe_sharing_level: str
    canonical_owners: tuple[tuple[str, str], ...]
    shared_helper_status: str
    runner_ownership_status: str
    report_ownership_status: str
    legacy_identity_risk: str
    false_completion_risk: str
    unit_of_analysis_boundary: str
    required_separation: str
    unresolved_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_ids": list(self.question_ids),
            "relationship_types": list(self.relationship_types),
            "scientific_intents": dict(self.scientific_intents),
            "scientifically_equivalent": self.scientifically_equivalent,
            "highest_safe_sharing_level": self.highest_safe_sharing_level,
            "canonical_owners": dict(self.canonical_owners),
            "shared_helper_status": self.shared_helper_status,
            "runner_ownership_status": self.runner_ownership_status,
            "report_ownership_status": self.report_ownership_status,
            "legacy_identity_risk": self.legacy_identity_risk,
            "false_completion_risk": self.false_completion_risk,
            "unit_of_analysis_boundary": self.unit_of_analysis_boundary,
            "required_separation": self.required_separation,
            "unresolved_reason": self.unresolved_reason,
        }


WAVE_A5_TARGET_RELATIONSHIPS = (
    ("E3", "S1"),
    ("D6", "PORT-1"),
    ("R1", "R2"),
    ("D1", "L3"),
    ("E2", "L1"),
)
WAVE_A5_TARGETS = frozenset(
    question_id
    for relationship in WAVE_A5_TARGET_RELATIONSHIPS
    for question_id in relationship
)

# HD01 scientific-definition clarification (V1; canonical IDs unchanged).
E3_ACTIVE_V10_STRATEGY_FAMILIES = (
    "LIQUIDITY_SWEEP_REVERSAL",
    "FALSE_BREAK",
    "TREND_CONTINUATION",
    "BREAKOUT_EXPANSION",
    "MEAN_REVERSION",
    "RANGE_REACTION",
)
E3_EXCLUDED_STRATEGY_FAMILY = "NONE"
E3_LEGACY_COMPATIBILITY_TAXONOMY = (
    "REVERSAL", "CONTINUATION", "FALSE_BREAK",
)
E3_SUFFICIENCY_CONTRACT = {
    "minimum_distinct_canonical_opportunities_per_family": 30,
    "minimum_total_observations": 50,
    "minimum_strategy_coverage": 0.50,
    "minimum_outcome_coverage": 0.95,
    "uncertainty": "95% mean interval using 1.96 sample-standard-errors",
    "below_family_minimum": "INSUFFICIENT_EVIDENCE",
}


WAVE_A5_OWNERSHIP = {
    ("E3", "S1"): OwnershipRelationship(
        question_ids=("E3", "S1"),
        relationship_types=("TRUE_ALIAS", "SUPERSEDED_IDENTITY", "ADJUDICATED"),
        scientific_intents=(
            ("E3", "Identify expectancy independently for each active non-NONE V10 StrategyFamily."),
            ("S1", "Superseded alias of the E3 V10 StrategyFamily expectancy result."),
        ),
        scientifically_equivalent=True,
        highest_safe_sharing_level="full alias after explicit canonical-owner governance and semantic runner repair",
        canonical_owners=(("research_engine.experiments.strategy_expectancy.run_e3", "E3"),
                          ("e3_strategy_family_expectancy.json", "E3")),
        shared_helper_status=(
            "A strategy-to-outcome grouping and per-strategy expectancy calculation may be fully shared."
        ),
        runner_ownership_status=(
            "E3 maps to strategy_expectancy.run_e3. S1 owns no runner and projects E3 explicitly. "
            "Legacy run_q24 remains compatibility/history-only activation-frequency machinery."
        ),
        report_ownership_status=(
            "E3 solely owns e3_strategy_family_expectancy.json. S1 owns no report. "
            "q24_strategy_edge.json cannot satisfy either canonical identity."
        ),
        legacy_identity_risk=(
            "Q24 is not a declared canonical alias for E3 or S1 and remains historical compatibility only."
        ),
        false_completion_risk=(
            "Closed: report ownership and alias projection prevent Q24 from attaching completion to E3/S1."
        ),
        unit_of_analysis_boundary=(
            "One completed PRIMARY_HORIZON_SIMULATION outcome per unique canonical_opportunity_id."
        ),
        required_separation=(
            "E3 is the scientific owner; S1 is an explicit superseded alias. No separate S1 finding or artifact exists."
        ),
        unresolved_reason="",
    ),
    ("D6", "PORT-1"): OwnershipRelationship(
        question_ids=("D6", "PORT-1"),
        relationship_types=("SHARED_CALCULATION_DISTINCT_OWNERSHIP",),
        scientific_intents=(
            ("D6", "Test whether candidate rank ordering predicts realised/shadow outcomes across rank positions."),
            ("PORT-1", "Test whether the selected candidate was competitive with the best available candidate in its ranking cycle."),
        ),
        scientifically_equivalent=False,
        highest_safe_sharing_level="calculation",
        canonical_owners=(("D6", "D6"), ("PORT-1", "PORT-1")),
        shared_helper_status=(
            "Portfolio loading, decision/outcome indexing, candidate outcome joining, shadow-first outcome selection, "
            "and within-cycle regret calculation are safe shared calculations."
        ),
        runner_ownership_status=(
            "Ownership is already separate: run_portfolio_ranking owns D6 rank-position analysis; run_port_1 owns "
            "PORT-1 selected-versus-best analysis."
        ),
        report_ownership_status=(
            "Ownership is already separate: d6_portfolio_ranking.json and port1_portfolio_selection.json carry their "
            "respective canonical IDs."
        ),
        legacy_identity_risk="Neither question declares a legacy ID; no legacy collision exists.",
        false_completion_risk=(
            "No cross-completion path is established by current filenames/IDs. D6 still reports overlapping selection "
            "metrics, but its canonical finding remains rank-order quality."
        ),
        unit_of_analysis_boundary=(
            "D6 uses candidate outcomes by rank position across ranking cycles; PORT-1 uses one outcome-bearing selected "
            "candidate comparison per cycle. Account executions are not independent candidate opportunities."
        ),
        required_separation=(
            "Retain the existing distinct runner, report, completion, and finding ownership while allowing shared "
            "candidate/outcome and regret helpers."
        ),
    ),
    ("R1", "R2"): OwnershipRelationship(
        question_ids=("R1", "R2"),
        relationship_types=(
            "SHARED_HELPER_ONLY",
            "DISTINCT_RUNNER_REQUIRED",
            "DISTINCT_REPORT_REQUIRED",
            "LEGACY_IDENTITY_COLLISION",
        ),
        scientific_intents=(
            ("R1", "Evaluate whether the overall risk layer improves expectancy and survival."),
            ("R2", "Attribute final-expectancy improvement to each individual spread/correlation/regime/daily-loss guard."),
        ),
        scientifically_equivalent=False,
        highest_safe_sharing_level="helper",
        canonical_owners=(),
        shared_helper_status=(
            "Risk-event loading, canonical decision/outcome linkage, and raw guard taxonomy helpers may be shared; "
            "global effectiveness and per-guard attribution calculations may not be conflated."
        ),
        runner_ownership_status=(
            "Both map to legacy_canonical.run_q10. It ignores loaded decision traces, reads decision_ledger, and emits "
            "only total RISK_BLOCK and ledger counts; it measures neither overall risk benefit nor per-guard value."
        ),
        report_ownership_status=(
            "Both claim q10_guard_efficacy.json. R1 needs an overall risk-effect report and R2 needs a separately "
            "identified per-guard attribution report."
        ),
        legacy_identity_risk=(
            "Both accept Q10, making direct legacy lookup ambiguous and allowing one Q10 artifact to be accepted by both."
        ),
        false_completion_risk=(
            "Any nonempty risk-block count makes the shared Q10 report COMPLETE and can falsely complete both distinct claims."
        ),
        unit_of_analysis_boundary=(
            "Both intend canonical decision/opportunity-to-outcome research. Broker account fanout must not multiply "
            "strategy decisions; R2 additionally needs one declared guard exposure/treatment grain."
        ),
        required_separation=(
            "Define separate R1 global-effectiveness and R2 per-guard-attribution runners, reports, canonical IDs, "
            "completion rules, and non-colliding legacy/report routing; retain only shared raw-data helpers."
        ),
    ),
    ("D1", "L3"): OwnershipRelationship(
        question_ids=("D1", "L3"),
        relationship_types=(
            "SHARED_CALCULATION_DISTINCT_OWNERSHIP",
            "DISTINCT_RUNNER_REQUIRED",
            "DISTINCT_REPORT_REQUIRED",
            "LEGACY_IDENTITY_COLLISION",
        ),
        scientific_intents=(
            ("D1", "Attribute matched R-multiple outcomes to the ten scoring components."),
            ("L3", "Validate scoring-weight assumptions, regime classifications, and strategy mappings."),
        ),
        scientifically_equivalent=False,
        highest_safe_sharing_level="calculation",
        canonical_owners=(("component_reward.run", "D1"), ("q1_component_reward.json", "D1")),
        shared_helper_status=(
            "D1 component statistics may be reused as one input to a future L3 assessment, but cannot establish regime "
            "classification or strategy-mapping validity."
        ),
        runner_ownership_status=(
            "component_reward.run computes component attribution and is canonically aligned to D1; L3 requires an "
            "independent architecture-validity runner."
        ),
        report_ownership_status=(
            "q1_component_reward.json belongs to D1. L3 requires a distinct canonical report and completion contract."
        ),
        legacy_identity_risk=(
            "Both accept Q1, so the Q1 artifact is validatable for both and direct legacy lookup is ambiguous."
        ),
        false_completion_risk=(
            "A COMPLETE D1 component-attribution artifact can currently satisfy L3 report validity/readiness and expose "
            "a D1 finding as if it answered architecture validity."
        ),
        unit_of_analysis_boundary=(
            "D1 uses one matched canonical decision-trace/shadow outcome. A future L3 design may reuse those decision-level "
            "rows but must not add account fanout or treat repeated horizon outcomes as independent without a rule."
        ),
        required_separation=(
            "Keep D1 runner/report ownership; create an L3-specific runner, report filename, canonical report ID, legacy "
            "routing, evidence contract, and completion rule."
        ),
    ),
    ("E2", "L1"): OwnershipRelationship(
        question_ids=("E2", "L1"),
        relationship_types=(
            "SHARED_HELPER_ONLY",
            "DISTINCT_RUNNER_REQUIRED",
            "DISTINCT_REPORT_REQUIRED",
            "LEGACY_IDENTITY_COLLISION",
        ),
        scientific_intents=(
            ("E2", "Identify candlestick patterns with positive pooled expectancy across conditions."),
            ("L1", "Test whether pattern performance degrades over chronological time."),
        ),
        scientifically_equivalent=False,
        highest_safe_sharing_level="helper",
        canonical_owners=(("legacy_canonical.run_q05", "E2"), ("q5_pattern_degradation.json", "E2")),
        shared_helper_status=(
            "Pattern/outcome extraction and per-window pattern summary helpers may be shared; the pooled E2 calculation "
            "cannot substitute for L1 chronology."
        ),
        runner_ownership_status=(
            "legacy_canonical.run_q05 performs pooled pattern R/win-rate summaries and is aligned to E2-style analysis. "
            "It does not use timestamps; L1 requires a separate temporal runner."
        ),
        report_ownership_status=(
            "q5_pattern_degradation.json currently contains pooled pattern performance and belongs to E2. L1 requires "
            "a distinct temporal-degradation report and completion contract."
        ),
        legacy_identity_risk=(
            "Both accept Q5, so one Q5 artifact is accepted for both and direct legacy lookup is ambiguous. E2 also "
            "declares Q24, which collides with E3/S1 and broadens legacy ambiguity."
        ),
        false_completion_risk=(
            "A COMPLETE pooled E2 Q5 report can currently satisfy L1 report validity/readiness and attach a non-temporal "
            "finding to L1."
        ),
        unit_of_analysis_boundary=(
            "E2 uses completed shadow lifecycle outcomes grouped by pattern. L1 needs chronologically ordered pattern "
            "outcomes; account fanout remains excluded and repeated horizons need explicit treatment."
        ),
        required_separation=(
            "Keep E2 pooled-pattern runner/report ownership; create an L1 timestamped temporal runner, unique report and "
            "canonical ID, non-colliding legacy routing, windows/trend test, and completion rule."
        ),
    ),
}


def apply_wave_a5_definitions(
    definitions: dict[str, ResearchQuestionDefinition],
) -> dict[str, ResearchQuestionDefinition]:
    """Apply the adjudicated E3/S1 owner/alias scientific contract."""
    e3 = definitions.get("E3")
    if e3 is not None:
        e3.question_wording = (
            "Which active non-NONE V10 StrategyFamily values contain positive "
            "expectancy on completed primary shadow outcomes?"
        )
        e3.research_intent = "Estimate opportunity-level simulated R expectancy by V10 StrategyFamily."
        e3.hypothesis = "At least one active V10 StrategyFamily has positive expectancy with 95% uncertainty."
        e3.null_hypothesis = "No active V10 StrategyFamily has established positive expectancy."
        e3.population_definition = (
            "CURRENT normalized shadow_trades_v1 completed PRIMARY_HORIZON_SIMULATION outcomes; "
            "one unique canonical_opportunity_id observation; StrategyFamily.NONE excluded."
        )
        e3.metric_definition = (
            "Per family N, mean simulated_outcome.pnl_r_multiple, win rate, and mean +/- 1.96 "
            "sample-standard-errors; N<30 is INSUFFICIENT_EVIDENCE."
        )
        e3.minimum_sample = 50
    s1 = definitions.get("S1")
    if s1 is not None:
        s1.lifecycle_status = QuestionLifecycle.SUPERSEDED
        s1.scientific_owner_id = "E3"
        s1.runner_module = ""
        s1.runner_function = ""
        s1.report_filename = ""
        s1.legacy_ids = ()
    return definitions
