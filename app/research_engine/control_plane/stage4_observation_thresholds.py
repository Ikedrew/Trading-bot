"""STAGE 4 OBSERVATION THRESHOLD GOVERNANCE.

Every governed observation gap needs a machine-readable threshold rule, or an
explicit ``METHOD_THRESHOLD_DEFINITION_REQUIRED`` classification naming exactly
which methodological decision is missing.

This module NEVER invents a universal numeric sample size.  Thresholds are
derived, in strict priority order, from the project's own authorities:

  1. ``research_engine.registry.research_question_registry`` per-question
     ``validation_rules`` -- the question's own scientific method already
     states the coverage/lineage/sample floors it requires.
  2. The adjudication ``*_MIN`` constants (e.g. ``L2_MIN``) where a question's
     registry entry carries no rules but an authoritative adjudication minimum
     exists.
  3. The OR-14 / OR-15 contract thresholds, which are already governed.

If none of those yields a defensible rule, the question is classified
``METHOD_THRESHOLD_DEFINITION_REQUIRED`` and the exact missing methodological
decision is recorded.  No arbitrary ``n=30`` / ``n=100`` fallback exists.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

#: Threshold classification vocabulary.
THRESHOLD_GOVERNED = "THRESHOLD_GOVERNED"
METHOD_THRESHOLD_DEFINITION_REQUIRED = "METHOD_THRESHOLD_DEFINITION_REQUIRED"

#: Where a governed threshold came from.  Recorded so a reviewer can audit the
#: derivation rather than take the number on trust.
SOURCE_QUESTION_VALIDATION_RULES = "QUESTION_REGISTRY_VALIDATION_RULES"
SOURCE_ADJUDICATION_MIN = "ADJUDICATION_MIN_CONSTANT"
SOURCE_OR_CONTRACT = "OBSERVATION_REQUIREMENT_CONTRACT"

#: The rule fields a threshold may carry.  Mirrors the vocabulary the task
#: allows: eligible sample count, COMPLETE observations, non-null ratio,
#: per-class/cell count, symbols, regimes/phases, time coverage, event coverage
#: and lineage-valid coverage.
THRESHOLD_RULE_KINDS = (
    "minimum_eligible_sample_count",
    "minimum_complete_observations",
    "minimum_non_null_ratio",
    "minimum_per_class_or_cell_count",
    "minimum_symbols",
    "minimum_regimes_or_phases",
    "minimum_time_coverage",
    "minimum_event_coverage",
    "minimum_lineage_valid_coverage",
)

#: Map a registry validation-rule field to its governed threshold kind.
_RULE_FIELD_TO_KIND: dict[str, str] = {
    "sample_size": "minimum_eligible_sample_count",
    "assessed_opportunities": "minimum_eligible_sample_count",
    "ranking_cycles": "minimum_eligible_sample_count",
    "outcome_coverage": "minimum_event_coverage",
    "lineage_coverage": "minimum_lineage_valid_coverage",
    "market_phase_coverage": "minimum_non_null_ratio",
    "h4_regime_coverage": "minimum_non_null_ratio",
    "strategy_coverage": "minimum_non_null_ratio",
    "pattern_coverage": "minimum_non_null_ratio",
}


class ThresholdGovernanceError(RuntimeError):
    """A threshold-governance invariant was violated (fail closed)."""


#: Adjudication minimums that govern a question whose registry entry declares no
#: validation rules.  These are the project's OWN authoritative constants, not
#: numbers invented here.  ``L2`` is the live case: its registry entry is empty
#: but ``learning_adaptation_adjudication.L2_MIN`` governs its pre/post design.
ADJUDICATION_MINIMUMS: dict[str, dict[str, Any]] = {
    "L2": {
        "constant": "L2_MIN",
        "authority": (
            "research_engine.registry.learning_adaptation_adjudication.L2_MIN"
        ),
        "design": "pre-vs-post expectancy change across a versioned boundary",
        "minimums": {"pre": 100, "post": 100, "cell": 30},
        "rules": [
            {"kind": "minimum_eligible_sample_count", "field": "pre",
             "operator": ">=", "value": 100,
             "description": "Pre-change governed observations required."},
            {"kind": "minimum_eligible_sample_count", "field": "post",
             "operator": ">=", "value": 100,
             "description": "Post-change governed observations required."},
            {"kind": "minimum_per_class_or_cell_count", "field": "cell",
             "operator": ">=", "value": 30,
             "description": "Minimum observations per comparison cell."},
        ],
    },
}

#: Contract thresholds for the two observation requirements that already carry
#: an explicit, governed, evidence-contract threshold.  These are preserved
#: verbatim and are NOT weakened here.
OR_CONTRACT_THRESHOLDS: dict[str, dict[str, Any]] = {
    "OR-14": {
        "constant": "EX2_LIFECYCLE_PATH_COMPLETENESS",
        "authority": (
            "stage4_ex2_l7_blocker_adjudication (governed EX2 roster) + "
            "core.shadow.observability.EX2_REQUIRED_CONTRACT_FIELDS"
        ),
        "rules": [
            {"kind": "minimum_complete_observations", "field": "path_state",
             "operator": "==", "value": "COMPLETE",
             "description": (
                 "100% of governed lifecycles must carry a COMPLETE ordered "
                 "M5 OHLC path bound to the lifecycle identity."
             )},
            {"kind": "minimum_event_coverage", "field": "lifecycle_coverage",
             "operator": ">=", "value": 1.0,
             "description": "Every governed lifecycle, no exclusions."},
        ],
        "forbidden": (
            "The 9,045-row widened reconstruction and any (symbol, ts) "
            "range-join reconstruction are permanently forbidden."
        ),
    },
    "OR-15": {
        "constant": "L7_MIN",
        "authority": (
            "research_engine.registry.learning_adaptation_adjudication.L7_MIN"
        ),
        "rules": [
            {"kind": "minimum_eligible_sample_count", "field": "control",
             "operator": ">=", "value": 100,
             "description": "Minimum CONTROL-arm assigned lifecycles."},
            {"kind": "minimum_eligible_sample_count", "field": "candidate",
             "operator": ">=", "value": 100,
             "description": "Minimum CANDIDATE-arm assigned lifecycles."},
            {"kind": "minimum_per_class_or_cell_count", "field": "cell",
             "operator": ">=", "value": 30,
             "description": "Minimum observations per comparison cell."},
            {"kind": "minimum_event_coverage", "field": "assignment_coverage",
             "operator": ">=", "value": 1.0,
             "description": "100% of the governed population must be "
                            "producer-assigned; unassigned rows fail closed."},
        ],
    },
}


def _question_rules(question_id: str) -> list[dict[str, Any]]:
    """The question's OWN governed validation rules, normalised to threshold form."""
    from research_engine.registry.research_question_registry import REGISTRY_BY_ID

    entry = REGISTRY_BY_ID.get(str(question_id).strip().upper())
    if entry is None:
        return []
    rules: list[dict[str, Any]] = []
    for rule in (getattr(entry, "validation_rules", None) or ()):
        field = str(getattr(rule, "field", "") or "")
        if not field:
            continue
        rules.append({
            "kind": _RULE_FIELD_TO_KIND.get(field, "minimum_non_null_ratio"),
            "field": field,
            "operator": str(getattr(rule, "operator", ">=") or ">="),
            "value": getattr(rule, "threshold", None),
            "description": str(getattr(rule, "description", "") or ""),
            "registry_field": field,
        })
    return rules


def resolve_threshold(question_id: str) -> dict[str, Any]:
    """Resolve the governed threshold for one question, or fail closed.

    Priority: question registry validation rules, then an authoritative
    adjudication ``*_MIN`` constant, then METHOD_THRESHOLD_DEFINITION_REQUIRED.
    """
    qid = str(question_id or "").strip().upper()

    rules = _question_rules(qid)
    if rules:
        return {
            "question_id": qid,
            "classification": THRESHOLD_GOVERNED,
            "threshold_source": SOURCE_QUESTION_VALIDATION_RULES,
            "authority": "research_engine.registry.research_question_registry",
            "rules": rules,
            "has_sample_size_rule": any(
                r["kind"] == "minimum_eligible_sample_count" for r in rules),
            "derivation": (
                "Derived from the question's own governed validation_rules, "
                "which state the coverage/lineage/sample floors its scientific "
                "method already requires. No number was invented here."
            ),
        }

    minimum = ADJUDICATION_MINIMUMS.get(qid)
    if minimum is not None:
        return {
            "question_id": qid,
            "classification": THRESHOLD_GOVERNED,
            "threshold_source": SOURCE_ADJUDICATION_MIN,
            "authority": minimum["authority"],
            "constant": minimum["constant"],
            "rules": list(minimum["rules"]),
            "has_sample_size_rule": True,
            "derivation": (
                f"Derived from the authoritative adjudication constant "
                f"{minimum['constant']} governing the {minimum['design']} "
                "design. No number was invented here."
            ),
        }

    return {
        "question_id": qid,
        "classification": METHOD_THRESHOLD_DEFINITION_REQUIRED,
        "threshold_source": None,
        "authority": None,
        "rules": [],
        "has_sample_size_rule": False,
        "missing_methodological_decision": (
            f"No governed minimum sample size, coverage floor or per-cell "
            f"minimum is declared for {qid} by the question registry or by an "
            "adjudication constant. The missing decision is: what effect size "
            "must {qid} be able to detect, and therefore what minimum eligible "
            "sample, coverage ratio and per-cell count its scientific method "
            "requires."
        ),
        "derivation": "No defensible threshold exists; none was invented.",
    }


def resolve_observation_requirement_threshold(
        requirement_id: str) -> dict[str, Any]:
    """Contract threshold for an OR that already carries one (EX2 / L7)."""
    from research_engine.control_plane.stage4_identity import (
        Stage4IdentityError, validate_requirement_id,
    )
    try:
        rid = validate_requirement_id(requirement_id)
    except Stage4IdentityError as exc:
        raise ThresholdGovernanceError(str(exc)) from exc
    contract = OR_CONTRACT_THRESHOLDS.get(rid)
    if contract is None:
        raise ThresholdGovernanceError("NO_OR_CONTRACT_THRESHOLD:" + rid)
    return {
        "observation_requirement_id": rid,
        "classification": THRESHOLD_GOVERNED,
        "threshold_source": SOURCE_OR_CONTRACT,
        "authority": contract["authority"],
        "constant": contract["constant"],
        "rules": list(contract["rules"]),
        "forbidden": contract.get("forbidden"),
        "preserved_verbatim": True,
    }


__all__ = [
    "ADJUDICATION_MINIMUMS", "METHOD_THRESHOLD_DEFINITION_REQUIRED",
    "OR_CONTRACT_THRESHOLDS", "SOURCE_ADJUDICATION_MIN", "SOURCE_OR_CONTRACT",
    "SOURCE_QUESTION_VALIDATION_RULES", "THRESHOLD_GOVERNED",
    "THRESHOLD_RULE_KINDS", "ThresholdGovernanceError",
    "resolve_observation_requirement_threshold", "resolve_threshold",
]
