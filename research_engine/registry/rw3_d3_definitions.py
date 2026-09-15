"""Implemented D3 contract within the still-in-progress RW3 foundation.

D3 evaluates whether the PRE-DECISION predicted-EV signal (R-multiple units)
has a useful relationship to subsequent realised R.  The predicted-EV measure
is research-derived and versioned (predicted_ev_r_v1); the production Stage-4
EV implementation is never modified.
"""
from __future__ import annotations

from dataclasses import replace

from research_engine.registry.research_question_models import (
    CompletionRule, EvidenceAuthority, EvidenceProducer, JoinContract,
    ResearchQuestionDefinition,
)

D3_IMPLEMENTED_TARGETS = frozenset({"D3"})

D3_OVERRIDE = {
    "hypothesis": "Pre-decision predicted_ev_r_v1 (p_success and pre-decision TP/SL geometry in R units) separates later unseen CURRENT opportunities into groups with different realised R expectancy.",
    "null_hypothesis": "Pre-decision predicted_ev_r_v1 does not separate later unseen CURRENT opportunities by realised R expectancy.",
    "population_definition": "CURRENT decision traces with authoritative pre-decision p_success and valid pre-decision entry/stop/target geometry paired to CURRENT shadow outcomes, collapsed to one independent observation per canonical_opportunity_id; account and horizon fanout cannot increase n.",
    "metric_definition": "predicted_ev_r_v1 = p_success * reward_r - (1 - p_success) * risk_r, with risk_r=1.0 and reward_r = pre-decision TP distance / pre-decision SL distance (R units). Compares realised mean/median R, win rate, and expectancy difference between EV-eligible (predicted_ev_r >= research threshold) and EV-ineligible validation opportunities. Observational gate evaluation only; policy_trade_allowed is NOT an isolated EV-gate treatment and no causal improvement is claimed. Probability calibration state is reported from D2; heuristic_score_v1 remains UNCALIBRATED and is never presented as validated EV truth.",
    "evidence_authorities": (
        EvidenceAuthority(dataset="decision_trace", schema_version="CURRENT", producer=EvidenceProducer.DECISION_TRACE, field_path="p_success", semantic_meaning="authoritative probability frozen before the decision outcome"),
        EvidenceAuthority(dataset="decision_trace", schema_version="CURRENT", producer=EvidenceProducer.DECISION_TRACE, field_path="v10_entry", semantic_meaning="pre-decision entry/stop/target geometry used to derive reward_r (R units)"),
        EvidenceAuthority(dataset="shadow_trades", schema_version="CURRENT", producer=EvidenceProducer.SHADOW_TRADES, field_path="simulated_outcome.pnl_r_multiple", semantic_meaning="subsequent realised R outcome; R>0 is the binary win label"),
    ),
    "join_contract": JoinContract(join_keys=("canonical_opportunity_id",), cardinality="one_to_many", conflict_policy="reject", description="Exactly one predicted_ev_r prediction/version/timestamp joins each canonical opportunity. Repeated shadow horizons/accounts collapse within opportunity; conflicting predictions, invalid geometry, duplicate-horizon outcomes, or post-decision timestamps fail closed."),
    "epoch_requirement": "CURRENT",
    "minimum_sample": 100,
    "completion_rule": CompletionRule(rule_type="paired_chronological_predicted_ev_gate", threshold=100, description="COMPLETE only after >=100 paired canonical opportunities, >=60 earlier discovery and >=40 later unseen validation observations, with >=10 validation opportunities in each of the EV-eligible and EV-ineligible groups. The research EV threshold is selected on discovery only. COMPLETE means the gate was evaluated, not that a causal improvement was proven."),
}


def apply_d3_definition(definitions: dict[str, ResearchQuestionDefinition]) -> dict[str, ResearchQuestionDefinition]:
    result = dict(definitions)
    result["D3"] = replace(result["D3"], **D3_OVERRIDE)
    return result
