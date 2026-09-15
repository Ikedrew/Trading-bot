"""Implemented D2 contract within the still-in-progress RW3 foundation."""
from __future__ import annotations

from dataclasses import replace

from research_engine.registry.research_question_models import (
    CompletionRule, EvidenceAuthority, EvidenceProducer, JoinContract,
    ResearchQuestionDefinition,
)

D2_IMPLEMENTED_TARGETS = frozenset({"D2"})

D2_OVERRIDE = {
    "hypothesis": "Authoritative pre-decision p_success is calibrated to subsequent binary opportunity success on later unseen CURRENT opportunities.",
    "null_hypothesis": "Pre-decision p_success is not calibrated to subsequent binary opportunity success on later unseen CURRENT opportunities.",
    "population_definition": "CURRENT decision traces with authoritative pre-decision p_success paired to CURRENT shadow outcomes, collapsed to one independent observation per canonical_opportunity_id; account and horizon fanout cannot increase n.",
    "metric_definition": "Discovery and later-validation Brier score, reliability-bin predicted probability versus observed R>0 success rate, and expected calibration error. Missing R is excluded rather than classified. heuristic_score_v1 remains UNCALIBRATED unless later validation meets the declared calibration criterion.",
    "evidence_authorities": (
        EvidenceAuthority(dataset="decision_trace", schema_version="CURRENT", producer=EvidenceProducer.DECISION_TRACE, field_path="p_success", semantic_meaning="authoritative probability frozen before the decision outcome"),
        EvidenceAuthority(dataset="shadow_trades", schema_version="CURRENT", producer=EvidenceProducer.SHADOW_TRADES, field_path="simulated_outcome.pnl_r_multiple", semantic_meaning="subsequent R outcome; R>0 is the binary success label"),
    ),
    "join_contract": JoinContract(join_keys=("canonical_opportunity_id",), cardinality="one_to_many", conflict_policy="reject", description="Exactly one prediction/version/timestamp joins each canonical opportunity. Repeated shadow horizons/accounts collapse within opportunity; conflicting predictions, duplicate-horizon outcomes, or post-decision timestamps fail closed."),
    "epoch_requirement": "CURRENT",
    "minimum_sample": 100,
    "completion_rule": CompletionRule(rule_type="paired_chronological_calibration", threshold=100, description="COMPLETE only after >=100 paired canonical opportunities, >=60 earlier discovery and >=40 later unseen validation observations, with >=2 later reliability bins containing >=10 observations. COMPLETE means calibration was evaluated, not necessarily supported."),
}


def apply_d2_definition(definitions: dict[str, ResearchQuestionDefinition]) -> dict[str, ResearchQuestionDefinition]:
    result = dict(definitions)
    result["D2"] = replace(result["D2"], **D2_OVERRIDE)
    return result

