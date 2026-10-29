"""Implemented L1 definition within the still-in-progress RW10 wave."""
from __future__ import annotations

from dataclasses import replace

from research_engine.registry.learning_adaptation_adjudication import (
    HD11_VERSION, L1_ESTIMAND, L1_MIN,
)
from research_engine.registry.research_question_models import (
    CompletionRule, EvidenceAuthority, EvidenceProducer, JoinContract,
    ResearchQuestionDefinition,
)

L1_IMPLEMENTED_TARGETS = frozenset({"L1"})

L1_OVERRIDE = {
    "hypothesis": (
        "At least one sufficiently evidenced pattern has a statistically reliable "
        "decline in late-window versus early-window mean simulated R."
    ),
    "null_hypothesis": (
        "No sufficiently evidenced pattern has a statistically reliable decline "
        "in late-window versus early-window mean simulated R."
    ),
    "population_definition": (
        "CURRENT completed PRIMARY_HORIZON_SIMULATION shadow outcomes, collapsed "
        "to one observation per identity.canonical_opportunity_id. Opportunities "
        "are ordered by authoritative entry_time UTC epoch seconds with canonical "
        "opportunity ID as the deterministic tie-break; account fanout never enlarges N."
    ),
    "metric_definition": (
        f"{L1_ESTIMAND}; deterministic non-overlapping equal chronological early/late "
        "windows, two-sided 95% normal-approximation inference, and one Holm step-down "
        "family over all sufficiently evidenced patterns at alpha 0.05."
    ),
    "evidence_authorities": (
        EvidenceAuthority(
            dataset="shadow_trades",
            schema_version="shadow_trades_v1",
            producer=EvidenceProducer.SHADOW_TRADES,
            field_path=(
                "identity.canonical_opportunity_id | identity.shadow_type | "
                "decision_snapshot.pattern | simulated_outcome.pnl_r_multiple"
            ),
            semantic_meaning=(
                "Canonical primary-horizon pattern outcome"
            ),
        ),
        EvidenceAuthority(
            dataset="shadow_runtime",
            schema_version="shadow_runtime_v1",
            producer=EvidenceProducer.SHADOW_TRADES,
            field_path=(
                "OPEN.entry_market_time | OPEN.entry_market_time_utc_epoch_s | "
                "OPEN.market_timestamp_semantics | "
                "OPEN.market_timestamp_normalization_version"
            ),
            semantic_meaning=(
                "Immutable shadow construction entry time; current canonical UTC "
                "or input to the governed historical timestamp normalization"
            ),
        ),
        EvidenceAuthority(
            dataset="events",
            schema_version="events_v1",
            producer=None,
            field_path="CANDLE:mt5_data:M5.payload.ts and OHLC",
            semantic_meaning=(
                "Existing governed historical timestamp-normalization corroboration only"
            ),
        ),
    ),
    "join_contract": JoinContract(
        join_keys=("canonical_opportunity_id", "shadow_trade_id", "horizon"),
        cardinality="one_to_many",
        conflict_policy="reject",
        description=(
            "Each completed primary shadow outcome joins exactly one OPEN and one "
            "CLOSE lifecycle by (shadow_trade_id, canonical_opportunity_id, horizon). "
            "Affected historical timestamps additionally use the paired governed M5 "
            "candle interval; missing, duplicate, or conflicting authority fails closed."
        ),
    ),
    "epoch_requirement": "CURRENT",
    "minimum_sample": L1_MIN["overall"],
    "completion_rule": CompletionRule(
        rule_type="chronological_pattern_degradation",
        threshold=L1_MIN["overall"],
        description=(
            f"{HD11_VERSION}: COMPLETE requires at least {L1_MIN['overall']} distinct "
            f"canonical opportunities and at least {L1_MIN['patterns']} patterns with "
            f"at least {L1_MIN['per_pattern_window']} observations in each window; a "
            "sufficient null completes as NO_RELIABLE_DEGRADATION."
        ),
    ),
}


def apply_l1_definition(
    definitions: dict[str, ResearchQuestionDefinition],
) -> dict[str, ResearchQuestionDefinition]:
    result = dict(definitions)
    result["L1"] = replace(result["L1"], **L1_OVERRIDE)
    return result
