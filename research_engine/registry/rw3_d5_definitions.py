"""Implemented D5 contract within the still-in-progress RW3 foundation.

D5 evaluates whether rejected / no-trade canonical opportunities subsequently
show counterfactual shadow-outcome characteristics indicating the rejection
logic is filtering harmful opportunities, discarding useful opportunities, or
showing no reliable difference.

The rejection authority is the producer-derived decision_trace triple
action + terminal_stage + terminal_reason, mapped to one deterministic class
(d5_rejection_taxonomy_v1) by canonical pipeline-stage precedence.  PATTERN_REJECT
/ NO_TRADE / RISK_BLOCK are not treated as interchangeable.  The subsequent
outcome is the CURRENT shadow realised R (a counterfactual/simulated research
outcome, never a broker fill).  Production rejection logic is never modified.
"""
from __future__ import annotations

from dataclasses import replace

from research_engine.registry.research_question_models import (
    CompletionRule, EvidenceAuthority, EvidenceProducer, JoinContract,
    ResearchQuestionDefinition,
)

D5_IMPLEMENTED_TARGETS = frozenset({"D5"})

D5_OVERRIDE = {
    "hypothesis": "Rejected / no-trade canonical opportunities have subsequent counterfactual shadow-R outcomes whose direction (adverse = good filtering, favourable = missed opportunity) persists from earlier discovery into later unseen validation.",
    "null_hypothesis": "Rejected / no-trade canonical opportunities show no reliable directional difference in subsequent counterfactual shadow-R outcomes, or any discovery direction does not persist in later validation.",
    "population_definition": "CURRENT decision traces with a genuine pre-outcome NO_TRADE rejection (resolved terminal_stage/reason) paired to a CURRENT shadow (counterfactual) outcome, collapsed to one independent observation per canonical_opportunity_id; account and horizon fanout cannot increase n. Rejections without a valid subsequent outcome are frequency diagnostics only.",
    "metric_definition": "Rejection class is producer-derived (action+terminal_stage+terminal_reason) under d5_rejection_taxonomy_v1: STRATEGY_SIGNAL_REJECTION, RISK_EXECUTION_BLOCK, or UNKNOWN, with the earliest canonical pipeline stage as the deterministic primary classification (same-rank contradictions fail closed). Reports counterfactual shadow realised-R mean/median, win rate, positive/negative counts overall and by sufficiently sampled class/reason/context, plus discovery-vs-later-validation persistence. Findings: GOOD_FILTERING, MISSED_OPPORTUNITY_SIGNAL, DISCOVERY_ONLY, MIXED_OR_NO_SIGNAL, INSUFFICIENT_EVIDENCE. Shadow outcomes are counterfactual/simulated, never broker truth; missing outcomes are excluded and never imputed; comparative policy-effectiveness is claimed only when a valid accepted comparator exists on both validation sides.",
    "evidence_authorities": (
        EvidenceAuthority(dataset="decision_trace", schema_version="CURRENT", producer=EvidenceProducer.DECISION_TRACE, field_path="terminal_stage", semantic_meaning="producer-derived pre-outcome rejection stage (with action=NO_TRADE and terminal_reason) establishing the canonical rejection taxonomy"),
        EvidenceAuthority(dataset="shadow_trades", schema_version="CURRENT", producer=EvidenceProducer.SHADOW_TRADES, field_path="simulated_outcome.pnl_r_multiple", semantic_meaning="subsequent counterfactual realised R for the rejected opportunity; R>0 is the binary win label"),
    ),
    "join_contract": JoinContract(join_keys=("canonical_opportunity_id",), cardinality="one_to_many", conflict_policy="reject", description="Exactly one primary rejection classification joins each canonical opportunity to exactly one subsequent shadow outcome. Repeated shadow horizons/accounts collapse within opportunity; multiple rejection reasons collapse to the earliest canonical stage; contradictory same-rank rejection authorities, conflicting outcomes, duplicate-horizon outcomes, or post-outcome rejection timestamps fail closed."),
    "epoch_requirement": "CURRENT",
    "minimum_sample": 100,
    "completion_rule": CompletionRule(rule_type="paired_chronological_rejected_opportunity_validation", threshold=100, description="COMPLETE only after >=100 rejected canonical opportunities with paired counterfactual outcomes, >=60 earlier discovery and >=40 later unseen validation observations. Directional class/reason/context claims require >=15 in the subgroup. COMPLETE means the chronological counterfactual evaluation ran validly (a negative/no-signal result is still COMPLETE); it does not mean the rejection logic is good and does not change production."),
}


def apply_d5_definition(definitions: dict[str, ResearchQuestionDefinition]) -> dict[str, ResearchQuestionDefinition]:
    result = dict(definitions)
    result["D5"] = replace(result["D5"], **D5_OVERRIDE)
    return result
