"""Implemented RW2 scientific contracts for M1, M3, M7, M8 and M11.

This layer follows the frozen Wave-A diagnosis.  It does not weaken the
canonical predictive questions; it records the authority now implemented by
the RW2 runners.
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

RW2_TARGETS = frozenset({"M1", "M3", "M7", "M8", "M11"})


def _shadow(field: str, meaning: str) -> EvidenceAuthority:
    return EvidenceAuthority(
        dataset="shadow_trades",
        schema_version="CURRENT",
        producer=EvidenceProducer.SHADOW_TRADES,
        field_path=field,
        semantic_meaning=meaning,
    )


_OPPORTUNITY_JOIN = JoinContract(
    join_keys=("canonical_opportunity_id",),
    cardinality="one_to_many",
    conflict_policy="reject",
    description=(
        "Group all CURRENT shadow horizon/account rows by canonical_opportunity_id; "
        "pre-decision facts must agree and repeated outcomes are aggregated to one "
        "opportunity label. Missing labels remain missing."
    ),
)


def _completion(total: int, discovery: int, validation: int, cell: int) -> CompletionRule:
    return CompletionRule(
        rule_type="predictive_chronological_validation",
        threshold=total,
        description=(
            f"COMPLETE only after at least {total} distinct outcome-bearing canonical "
            f"opportunities split chronologically into at least {discovery} earlier "
            f"discovery and {validation} later unseen validation observations, with at "
            f"least two candidate cells having {cell} observations in each partition. "
            "Completion records either supported or unsupported predictive evidence; "
            "descriptive association alone never completes the question."
        ),
    )


RW2_OVERRIDES: dict[str, dict] = {
    "M1": {
        "hypothesis": "Pre-decision H4 regime improves prediction of subsequent opportunity-level R on later unseen CURRENT opportunities.",
        "null_hypothesis": "Pre-decision H4 regime does not improve later unseen opportunity-level R prediction over the discovery-set mean.",
        "population_definition": "CURRENT completed shadow evidence collapsed to one independent observation per canonical_opportunity_id; account fanout and repeated horizons are not independent samples.",
        "metric_definition": "Later-validation reduction in mean squared R prediction error for discovery-fitted H4-regime means versus the discovery intercept, with a paired 95% interval; descriptive per-regime mean R is supporting only.",
        "evidence_authorities": (_shadow("decision_snapshot.h4_regime", "H4 regime frozen at decision time"), _shadow("simulated_outcome.pnl_r_multiple", "subsequent shadow R outcome label")),
        "join_contract": _OPPORTUNITY_JOIN,
        "minimum_sample": 60,
        "completion_rule": _completion(60, 30, 20, 5),
    },
    "M3": {
        "hypothesis": "Pre-decision market phase adds later-validation R predictive value beyond H4 regime alone.",
        "null_hypothesis": "Regime-plus-phase does not reduce later-validation R prediction error beyond regime alone.",
        "population_definition": "CURRENT completed shadow evidence collapsed to one independent observation per canonical_opportunity_id with decision-time H4 regime and market phase.",
        "metric_definition": "Paired later-validation MSE improvement of discovery-fitted regime-plus-phase cell means over discovery-fitted regime means; descriptive cell dispersion is not predictive evidence.",
        "evidence_authorities": (_shadow("decision_snapshot.h4_regime", "pre-decision H4 regime"), _shadow("decision_snapshot.market_phase", "pre-decision market phase"), _shadow("simulated_outcome.pnl_r_multiple", "subsequent R label")),
        "join_contract": _OPPORTUNITY_JOIN,
        "minimum_sample": 90,
        "completion_rule": _completion(90, 50, 30, 8),
    },
    "M7": {
        "hypothesis": "The pre-decision H4-regime by market-phase interaction improves later-validation R prediction beyond either context alone.",
        "null_hypothesis": "The interaction does not improve later-validation prediction beyond both single-context models.",
        "population_definition": "CURRENT completed shadow evidence collapsed to one independent canonical opportunity; all horizons and accounts for that opportunity remain in one temporal partition.",
        "metric_definition": "Paired later-validation MSE improvement of discovery-fitted regime-phase cells against each single-context model; support requires improvement beyond both, not in-sample group differences.",
        "evidence_authorities": (_shadow("decision_snapshot.h4_regime", "pre-decision H4 regime"), _shadow("decision_snapshot.market_phase", "pre-decision phase"), _shadow("simulated_outcome.pnl_r_multiple", "subsequent R label")),
        "join_contract": _OPPORTUNITY_JOIN,
        "minimum_sample": 90,
        "completion_rule": _completion(90, 50, 30, 8),
    },
    "M8": {
        "hypothesis": "Canonical pre-decision market phase transitions improve prediction of subsequent opportunity-level R on later unseen CURRENT opportunities.",
        "null_hypothesis": "Canonical market phase transitions do not improve later unseen R prediction over the discovery-set mean.",
        "population_definition": "CURRENT canonical market_context observations deterministically linked to CURRENT shadow outcomes, with one independent observation per canonical_opportunity_id.",
        "metric_definition": "Later-validation MSE improvement of discovery-fitted transition-category means over the discovery intercept. Embedded shadow phase is consistency evidence only and cannot replace market_context.",
        "evidence_authorities": (
            EvidenceAuthority(dataset="market_context", schema_version="CURRENT", field_path="market_phase + timestamp", semantic_meaning="authoritative ordered pre-decision phase history"),
            _shadow("simulated_outcome.pnl_r_multiple", "subsequent R outcome label"),
        ),
        "join_contract": JoinContract(join_keys=("canonical_opportunity_id", "entity_id"), cardinality="many_to_one", conflict_policy="reject", description="Prefer the explicit canonical root; entity_id may resolve it only when unique. Context must precede the opportunity and conflicts with snapshot provenance reject the join."),
        "minimum_sample": 60,
        "completion_rule": _completion(60, 30, 20, 5),
    },
    "M11": {
        "hypothesis": "Decision-time regime, phase and bias jointly improve later-validation R prediction beyond pattern identity.",
        "null_hypothesis": "Decision-time context does not reduce later-validation R prediction error beyond pattern identity.",
        "population_definition": "CURRENT decision traces joined one-to-one to CURRENT shadow evidence and collapsed to one independent observation per canonical_opportunity_id.",
        "metric_definition": "Paired later-validation MSE improvement of discovery-fitted regime-phase-bias cells over discovery-fitted pattern means; cells are weighted through opportunity-level paired errors, never unweighted cell-mean dispersion.",
        "evidence_authorities": (
            EvidenceAuthority(dataset="decision_trace", schema_version="CURRENT", producer=EvidenceProducer.DECISION_TRACE, field_path="v10_market_state.regime + v10_market_state.h4.market_phase + v10_market_state.h1.dominant_trend", semantic_meaning="authoritative decision-time context"),
            _shadow("decision_snapshot.pattern", "pattern identity frozen at decision time"),
            _shadow("simulated_outcome.pnl_r_multiple", "subsequent R outcome label"),
        ),
        "join_contract": JoinContract(join_keys=("canonical_opportunity_id",), cardinality="one_to_many", conflict_policy="reject", description="Exactly one authoritative decision context joins to each canonical opportunity; shadow horizons/accounts are collapsed within root and context disagreements fail closed."),
        "minimum_sample": 120,
        "completion_rule": _completion(120, 60, 40, 10),
    },
}


def apply_rw2_definitions(definitions: dict[str, ResearchQuestionDefinition]) -> dict[str, ResearchQuestionDefinition]:
    result = dict(definitions)
    for question_id, values in RW2_OVERRIDES.items():
        result[question_id] = replace(result[question_id], epoch_requirement="CURRENT", **values)
    return result

