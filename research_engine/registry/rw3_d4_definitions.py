"""Implemented D4 contract within the still-in-progress RW3 foundation.

D4 evaluates whether the CANONICAL PRE-DECISION score
(decision_trace.score_strategy, the strategy-weighted confluence score the
production decision gate acts on) ranks/separates subsequent realised R, and
whether a threshold selected on earlier discovery opportunities is supported on
later unseen validation opportunities.

The score authority is a single field.  score_neutral, confidence, p_success
(D2's probability input) and predicted_ev_r (D3's EV measure) are DISTINCT and
cannot substitute for it.  Production score generation is never modified and the
live threshold is never changed.
"""
from __future__ import annotations

from dataclasses import replace

from research_engine.registry.research_question_models import (
    CompletionRule, EvidenceAuthority, EvidenceProducer, JoinContract,
    ResearchQuestionDefinition,
)

D4_IMPLEMENTED_TARGETS = frozenset({"D4"})

D4_OVERRIDE = {
    "hypothesis": "The canonical pre-decision score decision_trace.score_strategy separates later unseen CURRENT opportunities into groups with different realised R expectancy, and a threshold selected on earlier discovery opportunities is supported on later validation opportunities.",
    "null_hypothesis": "The canonical pre-decision score does not separate later unseen CURRENT opportunities by realised R expectancy, or any discovery-selected threshold effect does not persist on later validation opportunities.",
    "population_definition": "CURRENT decision traces carrying the authoritative pre-decision score decision_trace.score_strategy paired to CURRENT shadow outcomes, collapsed to one independent observation per canonical_opportunity_id; account and horizon fanout cannot increase n.",
    "metric_definition": "Predictor is exactly the canonical pre-decision score decision_trace.score_strategy (strategy-weighted confluence score the decision gate acts on); score_neutral/confidence/p_success/predicted_ev_r are rejected substitutes. Reports score distribution, realised R distribution, score-outcome rank relationship, and realised R expectancy/win rate above vs below a threshold selected on the earlier discovery partition and evaluated on the later unseen validation partition. Findings: NO_SIGNAL, DISCOVERY_ONLY, VALIDATED_DIRECTIONAL_SIGNAL, INSUFFICIENT_EVIDENCE. Observational predictive validation only; no causal or optimality claim, and the live production threshold is never changed.",
    "evidence_authorities": (
        EvidenceAuthority(dataset="decision_trace", schema_version="CURRENT", producer=EvidenceProducer.DECISION_TRACE, field_path="score_strategy", semantic_meaning="canonical pre-decision strategy-weighted confluence score the production decision gate acts on; frozen before the outcome"),
        EvidenceAuthority(dataset="shadow_trades", schema_version="CURRENT", producer=EvidenceProducer.SHADOW_TRADES, field_path="simulated_outcome.pnl_r_multiple", semantic_meaning="subsequent realised R outcome; R>0 is the binary win label"),
    ),
    "join_contract": JoinContract(join_keys=("canonical_opportunity_id",), cardinality="one_to_many", conflict_policy="reject", description="Exactly one pre-decision score/timestamp joins each canonical opportunity. Repeated shadow horizons/accounts collapse within opportunity; conflicting scores, duplicate-horizon outcomes, or post-decision timestamps fail closed."),
    "epoch_requirement": "CURRENT",
    "minimum_sample": 100,
    "completion_rule": CompletionRule(rule_type="paired_chronological_score_threshold_validation", threshold=100, description="COMPLETE only after >=100 paired canonical opportunities, >=60 earlier discovery and >=40 later unseen validation observations, with >=15 validation opportunities on each side of the discovery-selected threshold. The threshold is selected on discovery only. COMPLETE means the chronological predictive evaluation ran validly (a negative/no-signal result is still COMPLETE); it does not mean the threshold is profitable and does not change the live threshold."),
}


def apply_d4_definition(definitions: dict[str, ResearchQuestionDefinition]) -> dict[str, ResearchQuestionDefinition]:
    result = dict(definitions)
    result["D4"] = replace(result["D4"], **D4_OVERRIDE)
    return result
