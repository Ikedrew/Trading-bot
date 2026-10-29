"""Implemented PORT-1 contract within the still-in-progress RW4 foundation.

PORT-1 evaluates whether the candidate actually selected by the portfolio
process (pre-outcome selection_status == "SELECTED") was competitive with the
best pre-outcome available candidate (rank_position == 1) in the same selection
cycle, and whether that competitiveness persists on later unseen cycles.

PORT-1 is DISTINCT from D6 (rank-ordering across positions). Per Wave A5 they
may share pure calculation helpers but never share question ownership or
completion. PORT-1 retains run_port_1 and port1_portfolio_selection.json
ownership. Research only — production selection is never modified.
"""
from __future__ import annotations

from dataclasses import replace

from research_engine.registry.research_question_models import (
    CompletionRule, EvidenceAuthority, EvidenceProducer, JoinContract,
    ResearchQuestionDefinition,
)

PORT1_IMPLEMENTED_TARGETS = frozenset({"PORT-1"})

PORT1_OVERRIDE = {
    "hypothesis": "The actually selected candidate (selection_status==SELECTED) is competitive with the best pre-outcome available candidate (rank_position==1) in the same cycle — its subsequent realised R is within a fixed versioned regret tolerance — and that competitiveness persists from earlier discovery cycles into later unseen validation cycles.",
    "null_hypothesis": "The selected candidate is not reliably competitive with the best pre-outcome available candidate, or any discovery competitiveness does not persist in later unseen cycles.",
    "population_definition": "Valid CURRENT portfolio_rankings selection cycles in which the pre-outcome selected candidate (selection_status==SELECTED) and the pre-outcome best-available comparator (rank_position==1) both have a joined CURRENT shadow outcome, collapsed to one independent observation per selection cycle; account fanout and repeated horizons cannot increase n. Cycles missing either outcome are excluded.",
    "metric_definition": "For each valid cycle: selection_regret_r = realised_R(rank_position==1 comparator) - realised_R(selected). Positive => selected underperformed the best pre-outcome comparator; 0 => matched; negative => selected outperformed despite lower rank. Both identities are pre-outcome producer fields, never derived from realised outcome. Reports mean/median/dispersion regret, competitive_rate (regret <= fixed versioned tolerance), selected/comparator realised-R stats and their difference, and selected==best-ranked rate, evaluated on an earlier discovery partition and a later unseen validation partition. Findings: SELECTION_COMPETITIVE, DISCOVERY_ONLY, SELECTION_REGRET_SIGNAL, NO_RELIABLE_SELECTION_ADVANTAGE, INSUFFICIENT_EVIDENCE. Distinct from D6; shadow outcomes are counterfactual/simulated and no causal claim is made.",
    "evidence_authorities": (
        EvidenceAuthority(dataset="portfolio_rankings", schema_version="CURRENT", producer=EvidenceProducer.SHADOW_TRADES, field_path="candidates.selection_status", semantic_meaning="pre-outcome identity of the candidate the portfolio process actually selected (SELECTED)"),
        EvidenceAuthority(dataset="portfolio_rankings", schema_version="CURRENT", producer=EvidenceProducer.SHADOW_TRADES, field_path="candidates.rank_position", semantic_meaning="pre-outcome best-available comparator is rank_position==1 (producer highest final_rank_score, sorted descending)"),
        EvidenceAuthority(dataset="shadow_trades", schema_version="CURRENT", producer=EvidenceProducer.SHADOW_TRADES, field_path="simulated_outcome.pnl_r_multiple", semantic_meaning="subsequent counterfactual realised R for each candidate's canonical opportunity; used only to evaluate the prior pre-outcome selection"),
    ),
    "join_contract": JoinContract(join_keys=("canonical_opportunity_id",), cardinality="one_to_many", conflict_policy="reject", description="Within one selection cycle the selected candidate and the rank_position==1 comparator each join exactly one canonical opportunity and one subsequent shadow outcome (bridged via decision_ledger cycle_id+symbol). Repeated shadow horizons/accounts collapse; conflicting selected or comparator identity fails closed; cycles missing either outcome are excluded."),
    "epoch_requirement": "CURRENT",
    "minimum_sample": 100,
    "completion_rule": CompletionRule(rule_type="paired_chronological_selection_competitiveness", threshold=100, description="COMPLETE only after >=100 valid paired selection cycles, >=60 earlier discovery and >=40 later unseen validation cycles. The competitiveness tolerance is a fixed versioned contract (port1_competitiveness_v1), never tuned on validation evidence. COMPLETE means the chronological selected-vs-best-available evaluation ran validly (a regret/no-advantage result is still COMPLETE); it does not mean selection was good and changes no production logic. A PORT-1 report never completes D6."),
}


def apply_port1_definition(definitions: dict[str, ResearchQuestionDefinition]) -> dict[str, ResearchQuestionDefinition]:
    result = dict(definitions)
    result["PORT-1"] = replace(result["PORT-1"], **PORT1_OVERRIDE)
    return result
