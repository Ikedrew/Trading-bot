"""Operational definition closure for frozen HD15/G3 only."""
from __future__ import annotations

from dataclasses import replace

from research_engine.registry.research_question_models import (
    CompletionRule,
    EvidenceAuthority,
    JoinContract,
    ResearchQuestionDefinition,
)


def apply_g3_definition(
    definitions: dict[str, ResearchQuestionDefinition],
) -> dict[str, ResearchQuestionDefinition]:
    result = dict(definitions)
    result["G3"] = replace(
        result["G3"],
        hypothesis="Research trust is demonstrated under the categorical HD15 rule.",
        null_hypothesis="Research trust is not yet demonstrated under the categorical HD15 rule.",
        population_definition="Exactly the 69 canonical non-G3 question identities once each.",
        metric_definition="One HD15 categorical trust state per question and one categorical global result; no score.",
        evidence_authorities=(
            EvidenceAuthority(
                "canonical_control_plane_question_state",
                semantic_meaning="Immutable canonical QuestionState/readiness/report-validity authority",
            ),
            EvidenceAuthority(
                "canonical_governance_dependency_reports",
                semantic_meaning="Owned G1/G2 results and explicit frozen-HD12 L6 dependency state",
            ),
        ),
        join_contract=JoinContract(
            ("canonical_question_id",), "one_to_one", "reject",
            "Exactly one state per non-G3 identity; reject omission, duplication, or self-reference.",
        ),
        minimum_sample=69,
        completion_rule=CompletionRule(
            "report_exists", 69,
            "Owned VALID_CURRENT exhaustive G3 report with valid immutable provenance, zero UNKNOWN, and no G3-level authority defect.",
        ),
    )
    return result

