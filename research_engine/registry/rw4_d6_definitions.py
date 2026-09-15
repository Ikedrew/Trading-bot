"""Implemented D6 contract opening the RW4 selection/ranking foundation.

D6 evaluates whether candidate RANK ORDERING (by rank_position) predicts
subsequent realised/shadow R across rank positions — do higher-ranked
candidates genuinely outperform lower-ranked ones, and does that persist on
later unseen cycles?

D6 is DISTINCT from PORT-1 (selected-vs-best). Per Wave A5 they may share a pure
calculation helper but never share question ownership or completion authority.
D6 retains run_portfolio_ranking and d6_portfolio_ranking.json ownership.
Research only — production ranking/selection is never modified.
"""
from __future__ import annotations

from dataclasses import replace

from research_engine.registry.research_question_models import (
    CompletionRule, EvidenceAuthority, EvidenceProducer, JoinContract,
    ResearchQuestionDefinition,
)

D6_IMPLEMENTED_TARGETS = frozenset({"D6"})

D6_OVERRIDE = {
    "hypothesis": "Candidate rank ordering (rank_position) predicts subsequent realised/shadow R: better-ranked candidates outperform lower-ranked ones, and the relationship persists from earlier discovery cycles into later unseen validation cycles.",
    "null_hypothesis": "Candidate rank ordering does not reliably predict subsequent realised/shadow R, or any discovery relationship does not persist in later unseen cycles.",
    "population_definition": "Candidates within CURRENT portfolio_rankings cycles whose canonical opportunity joins a CURRENT shadow outcome, collapsed to one independent observation per canonical_opportunity_id; account fanout and repeated horizons cannot increase n. Candidates without a valid joined outcome are excluded.",
    "metric_definition": "For each canonical opportunity: its pre-outcome rank_position and its subsequent shadow realised R. Reports the Spearman rank relationship between better rank (-rank_position) and realised R (positive => better rank predicts higher R), per-rank-bucket mean/median R and win rate, evaluated on an earlier discovery partition and a later unseen validation partition. Findings: RANK_ORDERING_PREDICTS_OUTCOME, DISCOVERY_ONLY, NO_RELIABLE_RANK_SIGNAL, INVERSE_RANK_SIGNAL, INSUFFICIENT_EVIDENCE. Distinct from PORT-1's selected-vs-best question; shadow outcomes are counterfactual/simulated and no causal claim is made.",
    "evidence_authorities": (
        EvidenceAuthority(dataset="portfolio_rankings", schema_version="CURRENT", producer=EvidenceProducer.SHADOW_TRADES, field_path="candidates.rank_position", semantic_meaning="pre-outcome candidate rank ordering assigned by the portfolio ranker"),
        EvidenceAuthority(dataset="shadow_trades", schema_version="CURRENT", producer=EvidenceProducer.SHADOW_TRADES, field_path="simulated_outcome.pnl_r_multiple", semantic_meaning="subsequent counterfactual realised R for the same canonical opportunity; R>0 is the binary win label"),
    ),
    "join_contract": JoinContract(join_keys=("canonical_opportunity_id",), cardinality="one_to_many", conflict_policy="reject", description="Each canonical opportunity joins exactly one rank_position and one subsequent shadow outcome (bridged via decision_ledger cycle_id+symbol). Repeated shadow horizons/accounts collapse within opportunity; conflicting rank membership or conflicting outcomes fail closed; missing outcomes are excluded."),
    "epoch_requirement": "CURRENT",
    "minimum_sample": 100,
    "completion_rule": CompletionRule(rule_type="paired_chronological_rank_ordering_vs_outcome", threshold=100, description="COMPLETE only after >=100 paired canonical opportunities, >=60 earlier discovery and >=40 later unseen validation observations. Directional per-rank-bucket claims require >=15 in the bucket. COMPLETE means the chronological rank-ordering evaluation ran validly (a negative/no-signal or inverse result is still COMPLETE); it does not mean the ranker is good and changes no production logic. A D6 report never completes PORT-1."),
}


def apply_d6_definition(definitions: dict[str, ResearchQuestionDefinition]) -> dict[str, ResearchQuestionDefinition]:
    result = dict(definitions)
    result["D6"] = replace(result["D6"], **D6_OVERRIDE)
    return result
