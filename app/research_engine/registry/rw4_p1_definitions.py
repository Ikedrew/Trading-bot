"""Implemented P1 contract completing the RW4 selection/ranking/promotion wave.

P1 evaluates a CANDIDATE PROMOTION POLICY (remove opportunities whose pre-outcome
pattern group is a discovery removal candidate) as a leakage-safe counterfactual:
treatment membership is pre-outcome, the policy is learned on earlier discovery,
frozen, and evaluated on later unseen validation opportunities.

The counterfactual-design defect is repaired: the promotion policy is no longer
chosen in-sample on the same data it is scored against, and outcomes never
define treatment membership. P1 retains run_promotion_impact and
p1_promotion_impact.json ownership. Observational only — no causal claim, no
production change.
"""
from __future__ import annotations

from dataclasses import replace

from research_engine.registry.research_question_models import (
    CompletionRule, EvidenceAuthority, EvidenceProducer, JoinContract,
    ResearchQuestionDefinition,
)

P1_IMPLEMENTED_TARGETS = frozenset({"P1"})

P1_OVERRIDE = {
    "hypothesis": "A candidate promotion policy that removes pre-outcome-defined unfavourable pattern groups improves realised-R expectancy (with reported win-rate, trade-frequency and drawdown effects), and that improvement persists from earlier discovery opportunities into later unseen validation opportunities.",
    "null_hypothesis": "The candidate promotion policy does not improve realised-R expectancy on later unseen opportunities, or any discovery improvement does not persist.",
    "population_definition": "CURRENT shadow opportunities with a pre-outcome pattern group and a realised R outcome, collapsed to one independent observation per canonical_opportunity_id; account fanout and repeated horizons cannot increase n. Opportunities without a valid outcome are excluded and MISSING outcomes are never imputed.",
    "metric_definition": "Treatment = candidate removal policy (drop opportunities whose pre-outcome pattern group has discovery n>=10 and discovery mean R < -0.2R), learned on the earlier discovery partition and frozen. The effect metric is policy_minus_status_quo_ev_r = mean realised R of policy-kept validation opportunities minus mean realised R of all validation opportunities (R units), plus reported win-rate delta, trade-frequency delta and a deterministic cumulative-R drawdown delta. Contrast is WITHIN the later-unseen validation opportunities (status-quo vs frozen policy); membership is pre-outcome and outcomes only evaluate the policy. Findings: COUNTERFACTUAL_SUPPORT, DISCOVERY_ONLY, COUNTERFACTUAL_HARM_SIGNAL, NO_RELIABLE_COUNTERFACTUAL_SIGNAL, NO_CANDIDATE_POLICY, INSUFFICIENT_EVIDENCE. Observational counterfactual on shadow evidence; no causal identification is claimed.",
    "evidence_authorities": (
        EvidenceAuthority(dataset="shadow_trades", schema_version="CURRENT", producer=EvidenceProducer.SHADOW_TRADES, field_path="decision_snapshot.pattern", semantic_meaning="pre-outcome pattern group that defines candidate-policy treatment membership"),
        EvidenceAuthority(dataset="shadow_trades", schema_version="CURRENT", producer=EvidenceProducer.SHADOW_TRADES, field_path="simulated_outcome.pnl_r_multiple", semantic_meaning="realised R used only to evaluate the frozen policy; missing is excluded, explicit 0.0 is valid"),
    ),
    "join_contract": JoinContract(join_keys=("canonical_opportunity_id",), cardinality="one_to_many", conflict_policy="reject", description="Pattern group and outcome collapse to one observation per canonical_opportunity_id; repeated shadow horizons/accounts collapse; conflicting outcomes fail closed; missing outcomes are excluded. Treatment membership is derived from pre-outcome pattern only and never from realised R."),
    "epoch_requirement": "CURRENT",
    "minimum_sample": 100,
    "completion_rule": CompletionRule(rule_type="paired_chronological_counterfactual_promotion_policy", threshold=100, description="COMPLETE only after >=100 valid paired canonical opportunities, >=60 earlier discovery and >=40 later unseen validation observations, with >=15 validation opportunities on each of the removed and kept sides when a candidate policy exists (an empty candidate policy is also validly COMPLETE with no impact). The policy is learned on discovery and frozen before validation. COMPLETE means the counterfactual evaluation ran validly (a null/harm result is still COMPLETE); it does not mean the promotion is beneficial and changes no production logic. A P1 report never completes another question.",),
}


def apply_p1_definition(definitions: dict[str, ResearchQuestionDefinition]) -> dict[str, ResearchQuestionDefinition]:
    result = dict(definitions)
    result["P1"] = replace(result["P1"], **P1_OVERRIDE)
    return result
