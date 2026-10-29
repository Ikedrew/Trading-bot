"""Implemented OPP-1 contract within the still-in-progress RW4 foundation.

OPP-1 evaluates whether opportunities promoted into the decision pipeline
(pre-outcome horizon_candidates selection_status PROMOTED) have higher
subsequent realised R than opportunities rejected/filtered out, and whether that
advantage persists on later unseen opportunities.

The P0 defect (missing outcome -> 0.0 in assessment score buckets) is
eliminated: MISSING outcomes are excluded (never imputed), while an explicit
realised 0.0R remains a valid observation. The independent unit is one
canonical_opportunity_id (no legacy opportunity_id fallback); account fanout and
repeated horizons collapse; conflicts fail closed. OPP-1 retains run_opp_1 and
opp1_opportunity_selection.json ownership. Research only.
"""
from __future__ import annotations

from dataclasses import replace

from research_engine.registry.research_question_models import (
    CompletionRule, EvidenceAuthority, EvidenceProducer, JoinContract,
    ResearchQuestionDefinition,
)

OPP1_IMPLEMENTED_TARGETS = frozenset({"OPP-1"})

OPP1_OVERRIDE = {
    "hypothesis": "Opportunities promoted into the decision pipeline (pre-outcome SELECTED horizon candidate) have higher subsequent realised R than opportunities rejected/filtered out, and that advantage persists from earlier discovery opportunities into later unseen validation opportunities.",
    "null_hypothesis": "Promoted opportunities do not have higher subsequent realised R than rejected opportunities, or any discovery advantage does not persist in later unseen opportunities.",
    "population_definition": "CURRENT canonical opportunities with a deterministic pre-outcome promoted/rejected membership from horizon_candidates that also join a CURRENT shadow outcome, collapsed to one independent observation per canonical_opportunity_id; account fanout and repeated horizons cannot increase n. Opportunities without a valid outcome are excluded (frequency diagnostics only) and MISSING outcomes are never imputed.",
    "metric_definition": "Compare mean/median realised R, win rate and dispersion between PROMOTED and REJECTED canonical opportunities, using ONLY valid paired outcomes; promoted_minus_rejected_mean_r is the effect, evaluated on an earlier discovery partition and a later unseen validation partition. Missing outcomes are excluded (never 0.0/loss/success); an explicit realised 0.0R is valid and included. Findings: PROMOTED_OUTPERFORMS_REJECTED, DISCOVERY_ONLY, PROMOTED_UNDERPERFORMS_REJECTED, NO_RELIABLE_SELECTION_SIGNAL, INSUFFICIENT_EVIDENCE. Distinct from D6 and PORT-1; shadow outcomes are counterfactual/simulated and no causal claim is made.",
    "evidence_authorities": (
        EvidenceAuthority(dataset="horizon_candidates", schema_version="CURRENT", producer=EvidenceProducer.SHADOW_TRADES, field_path="selection_status", semantic_meaning="pre-outcome promoted/rejected membership keyed by canonical_opportunity_id"),
        EvidenceAuthority(dataset="shadow_trades", schema_version="CURRENT", producer=EvidenceProducer.SHADOW_TRADES, field_path="simulated_outcome.pnl_r_multiple", semantic_meaning="subsequent realised R for the same canonical opportunity; missing is excluded, explicit 0.0 is valid"),
    ),
    "join_contract": JoinContract(join_keys=("canonical_opportunity_id",), cardinality="one_to_many", conflict_policy="reject", description="Membership and outcome join on canonical_opportunity_id only (no legacy opportunity_id fallback, no positional/symbol-only join). Each opportunity resolves to at most one independent outcome; repeated shadow horizons/accounts collapse; opportunities with both promoted and rejected statuses, or conflicting outcomes, fail closed; missing outcomes are excluded."),
    "epoch_requirement": "CURRENT",
    "minimum_sample": 100,
    "completion_rule": CompletionRule(rule_type="paired_chronological_promoted_vs_rejected_expectancy", threshold=100, description="COMPLETE only after >=100 valid paired canonical opportunities, >=60 earlier discovery and >=40 later unseen validation observations, with >=15 in each of the promoted and rejected groups. COMPLETE means the chronological promoted-vs-rejected evaluation ran validly (a negative/no-signal or adverse result is still COMPLETE); it does not mean the pipeline is profitable and changes no production logic. An OPP-1 report never completes D6, PORT-1 or P1."),
}


def apply_opp1_definition(definitions: dict[str, ResearchQuestionDefinition]) -> dict[str, ResearchQuestionDefinition]:
    result = dict(definitions)
    result["OPP-1"] = replace(result["OPP-1"], **OPP1_OVERRIDE)
    return result
