"""Operational definition closure for frozen HD13/G1 and HD14/G2 only."""
from __future__ import annotations

from dataclasses import replace

from research_engine.registry.research_question_models import (
    CompletionRule, EvidenceAuthority, JoinContract, ResearchQuestionDefinition,
)


def apply_data_governance_definitions(
    definitions: dict[str, ResearchQuestionDefinition],
) -> dict[str, ResearchQuestionDefinition]:
    result = dict(definitions)
    result["G1"] = replace(
        result["G1"],
        hypothesis="All 70 canonical questions have suitable CURRENT evidence under HD13.",
        null_hypothesis="At least one canonical question is waiting, unsuitable, blocked, or unknown.",
        population_definition="Exactly all 70 canonical question identities once each.",
        metric_definition="Exhaustive six-category HD13 requirement assessment per canonical question.",
        evidence_authorities=(
            EvidenceAuthority("canonical_registry_definitions", semantic_meaning="Frozen intended-question and requirement inventory"),
            EvidenceAuthority("canonical_current_evidence_snapshot", semantic_meaning="Immutable CURRENT source, field, join, and sample evidence"),
        ),
        join_contract=JoinContract(
            ("canonical_question_id", "requirement_id"), "one_to_many", "reject",
            "Every declared requirement belongs to exactly one canonical question assessment.",
        ),
        minimum_sample=70,
        completion_rule=CompletionRule("report_exists", 70, "Owned VALID_CURRENT exhaustive G1 report with zero UNKNOWN"),
    )
    result["G2"] = replace(
        result["G2"],
        hypothesis="Point-estimate valid lineage coverage is at least 50%.",
        null_hypothesis="Point-estimate valid lineage coverage is below 50%.",
        population_definition="Exhaustive union of eligible CURRENT decision and outcome canonical opportunities.",
        metric_definition="VALID / D after exactly-once VALID/MISSING/AMBIGUOUS/CONFLICTING classification.",
        evidence_authorities=(
            EvidenceAuthority("decision_trace", semantic_meaning="Persisted decision-side canonical opportunities"),
            EvidenceAuthority("shadow_runtime", semantic_meaning="Persisted completed shadow/outcome lifecycles"),
        ),
        join_contract=JoinContract(
            ("entity_id", "canonical_opportunity_id"), "one_to_one", "reject",
            "Exact composite identity; no correlation, entity-only, account, or horizon fallback.",
        ),
        minimum_sample=100,
        completion_rule=CompletionRule("sample_reached", 100, "Owned VALID_CURRENT exhaustive G2 report"),
    )
    return result
