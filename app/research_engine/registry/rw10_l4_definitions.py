"""Implemented L4 definition within the still-in-progress RW10 wave."""
from __future__ import annotations

from dataclasses import replace

from research_engine.registry.learning_adaptation_adjudication import (
    HD11_VERSION, L4_MIN,
)
from research_engine.registry.research_question_models import (
    CompletionRule, EvidenceAuthority, EvidenceProducer, JoinContract,
    ResearchQuestionDefinition,
)

L4_IMPLEMENTED_TARGETS = frozenset({"L4"})

L4_OVERRIDE = {
    "hypothesis": (
        "At least one Holm-significant market-context mix or regime-conditional "
        "realized-R endpoint crosses its frozen architecture-threat threshold."
    ),
    "null_hypothesis": (
        "No Holm-significant endpoint crosses its frozen architecture-threat threshold."
    ),
    "population_definition": (
        "CURRENT completed PRIMARY_HORIZON_SIMULATION shadow outcomes collapsed to "
        "one observation per canonical opportunity, joined to the unique latest "
        "preceding same-symbol CURRENT market_context_v1 record at governed OPEN time."
    ),
    "metric_definition": (
        "Early/late regime-distribution total variation and three regime-specific "
        "late-minus-early opportunity-weighted mean-R contrasts; Pearson 2x3 and "
        "Welch tests form exactly one four-test Holm family at alpha 0.05."
    ),
    "evidence_authorities": (
        EvidenceAuthority(
            dataset="shadow_trades", schema_version="shadow_trades_v1",
            producer=EvidenceProducer.SHADOW_TRADES,
            field_path="identity.canonical_opportunity_id | identity.symbol | identity.shadow_type | simulated_outcome.pnl_r_multiple",
            semantic_meaning="Canonical primary-horizon opportunity outcome",
        ),
        EvidenceAuthority(
            dataset="shadow_runtime", schema_version="shadow_runtime_v1",
            producer=EvidenceProducer.SHADOW_TRADES,
            field_path="OPEN.entry_market_time canonical UTC authority",
            semantic_meaning="Governed shadow OPEN chronology authority",
        ),
        EvidenceAuthority(
            dataset="market_context", schema_version="market_context_v1",
            producer=None,
            field_path="symbol | bar_time | regime",
            semantic_meaning="Persisted canonical MarketContextBuilder regime history",
        ),
    ),
    "join_contract": JoinContract(
        join_keys=("symbol", "bar_time<=entry_time"), cardinality="many_to_one",
        conflict_policy="reject",
        description=(
            "For each canonical opportunity choose the unique latest preceding "
            "same-symbol market context; identical selected-time duplicates collapse "
            "and conflicting ties fail closed."
        ),
    ),
    "epoch_requirement": "CURRENT",
    "minimum_sample": L4_MIN["per_window"] * 2,
    "completion_rule": CompletionRule(
        rule_type="market_behaviour_stability",
        threshold=L4_MIN["per_window"] * 2,
        description=(
            f"{HD11_VERSION}: COMPLETE requires 100 opportunities per window, 30 "
            "per canonical regime per window, all four estimable endpoints, the "
            "frozen Holm family, and STABLE or MATERIAL_INSTABILITY."
        ),
    ),
}


def apply_l4_definition(
    definitions: dict[str, ResearchQuestionDefinition],
) -> dict[str, ResearchQuestionDefinition]:
    result = dict(definitions)
    result["L4"] = replace(result["L4"], **L4_OVERRIDE)
    return result
