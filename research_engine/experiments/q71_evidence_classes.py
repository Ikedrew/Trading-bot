"""Governed production evidence-class catalogue for generated (Q71+) research.

Repair Block 2 completed the generated-question execution architecture but left
the production evaluator registry empty, so every generated question correctly
resolved to ``MISSING_EVALUATOR``.  This module declares the *closed* vocabulary
of governed evidence classes a production observation space may admit, and binds
each class to:

* the physical governed dataset(s) that carry it,
* the canonical evaluator whose population, endpoint and statistical method the
  class is scientifically equivalent to,
* the scientific capability that canonical evaluator can actually support.

Authority rules enforced here:

* Nothing is invented.  Every declaration is derived from an authority that
  already exists in the repository: a canonical evaluator's own governed
  ``population_id``, its declared evidence authority and its own statistical
  method.  No population, endpoint, dataset binding or method is manufactured.
* The vocabulary is closed.  An evidence class that is not declared here has no
  production evaluator and a generated question about it stays
  ``MISSING_EVALUATOR``.  That is valid fail-closed behaviour, not a defect.
* A declaration states its own capability boundary.  It never claims a finding,
  a hypothesis or a candidate design that the canonical evaluator does not
  already compute.
* ``governed_intervention_policy_id`` is ``None`` unless a governed policy in
  the governed policy catalogue can be legitimately named by that evaluator on
  the evidence the Q71 worker can actually supply.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from typing import Any, Mapping

from research_engine.control_plane.stage4_dataset_snapshot import canonical_json


EVIDENCE_CLASS_CATALOGUE_SCHEMA = "production_evidence_class_catalogue_v1"

#: Every declaration must name physical datasets drawn from the common
#: investigation snapshot.  A declaration that names a dataset outside this
#: boundary can never be satisfied by the Q71 worker.
SUPPORTED_GOVERNED_DATASETS = frozenset({
    "trade_truth", "execution_results", "decision_trace", "shadow_runtime",
    "execution_attempts", "execution_context", "market_context",
    "strategy_observations", "protection_audit", "risk_deviation",
})


class ProductionEvidenceClassError(RuntimeError):
    """A production evidence-class declaration is invalid or unsupported."""


@dataclass(frozen=True)
class ProductionEvidenceClass:
    """One governed evidence class the production system can legitimately research.

    The declaration is descriptive metadata for evaluator scoping and capability
    reporting.  It computes nothing and grants nothing: the scientific semantics
    of a result are always produced by the evaluator that runs.
    """

    evidence_class: str
    canonical_question_id: str
    canonical_evaluator_module: str
    governed_datasets: tuple[str, ...]
    observation_grain: str
    estimand: str
    statistical_method: str
    minimum_evidence: Mapping[str, Any]
    scientific_finding_capable: bool
    hypothesis_capable: bool
    candidate_capable: bool
    governed_intervention_policy_id: str | None
    capability_class: str
    declared_scope_note: str
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "evidence_class", "canonical_question_id", "canonical_evaluator_module",
            "observation_grain", "estimand", "statistical_method",
            "capability_class", "declared_scope_note",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ProductionEvidenceClassError(
                    "EVIDENCE_CLASS_FIELD_REQUIRED:" + name)
        datasets = tuple(str(item).strip() for item in self.governed_datasets)
        if not datasets or any(not item for item in datasets):
            raise ProductionEvidenceClassError(
                "EVIDENCE_CLASS_DATASETS_REQUIRED:" + self.evidence_class)
        unsupported = sorted(set(datasets) - SUPPORTED_GOVERNED_DATASETS)
        if unsupported:
            raise ProductionEvidenceClassError(
                "EVIDENCE_CLASS_DATASET_NOT_IN_SNAPSHOT:"
                + self.evidence_class + ":" + ",".join(unsupported))
        object.__setattr__(self, "governed_datasets", datasets)
        for name in (
            "scientific_finding_capable", "hypothesis_capable",
            "candidate_capable",
        ):
            if type(getattr(self, name)) is not bool:
                raise ProductionEvidenceClassError(
                    "EVIDENCE_CLASS_FLAG_NOT_BOOLEAN:" + name)
        policy_id = self.governed_intervention_policy_id
        if policy_id is not None:
            policy_id = str(policy_id).strip() or None
            object.__setattr__(self, "governed_intervention_policy_id", policy_id)
        # A candidate is only ever claimable when a governed intervention exists.
        if self.candidate_capable and not self.governed_intervention_policy_id:
            raise ProductionEvidenceClassError(
                "EVIDENCE_CLASS_CANDIDATE_REQUIRES_GOVERNED_POLICY:"
                + self.evidence_class)
        if not isinstance(self.minimum_evidence, Mapping) or not self.minimum_evidence:
            raise ProductionEvidenceClassError(
                "EVIDENCE_CLASS_MINIMUM_EVIDENCE_REQUIRED:" + self.evidence_class)
        object.__setattr__(self, "minimum_evidence", dict(self.minimum_evidence))
        object.__setattr__(self, "provenance", dict(self.provenance or {}))

    def identity_material(self) -> dict[str, Any]:
        return {
            "catalogue_schema": EVIDENCE_CLASS_CATALOGUE_SCHEMA,
            "evidence_class": self.evidence_class,
            "canonical_question_id": self.canonical_question_id,
            "canonical_evaluator_module": self.canonical_evaluator_module,
            "governed_datasets": list(self.governed_datasets),
            "observation_grain": self.observation_grain,
            "estimand": self.estimand,
            "statistical_method": self.statistical_method,
            "minimum_evidence": dict(self.minimum_evidence),
            "scientific_finding_capable": self.scientific_finding_capable,
            "hypothesis_capable": self.hypothesis_capable,
            "candidate_capable": self.candidate_capable,
            "governed_intervention_policy_id": self.governed_intervention_policy_id,
            "capability_class": self.capability_class,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.identity_material(),
            "declared_scope_note": self.declared_scope_note,
            "provenance": dict(self.provenance),
            "evidence_class_identity_digest": self.identity_digest,
        }

    @property
    def identity_digest(self) -> str:
        return hashlib.sha256(
            canonical_json(self.identity_material()).encode("utf-8")
        ).hexdigest()

# ── Production declarations ───────────────────────────────────────────────────
#
# Ordered explicitly so the catalogue has a deterministic, reviewable
# registration order.  Each entry names the canonical authority it is derived
# from in ``provenance``.

CURRENT_COMPLETED_SHADOW_LIFECYCLES = "CURRENT_COMPLETED_SHADOW_LIFECYCLES"
SESSION_CONDITIONED_ABSOLUTE_MEASURED_EXECUTION_SLIPPAGE = (
    "SESSION_CONDITIONED_ABSOLUTE_MEASURED_EXECUTION_SLIPPAGE")
CURRENT_COMPLETED_SHADOW_LIFECYCLE_EXIT_PATH = (
    "CURRENT_COMPLETED_SHADOW_LIFECYCLE_EXIT_PATH")


PRODUCTION_EVIDENCE_CLASSES: tuple[ProductionEvidenceClass, ...] = (
    ProductionEvidenceClass(
        evidence_class=CURRENT_COMPLETED_SHADOW_LIFECYCLES,
        canonical_question_id="E1",
        canonical_evaluator_module="research_engine.experiments.expected_value",
        governed_datasets=("shadow_runtime",),
        observation_grain=(
            "one completed governed shadow lifecycle with a realised R-multiple"),
        estimand="mean R-multiple per completed governed shadow lifecycle",
        statistical_method=(
            "one-sample t-test of the mean R-multiple versus zero, normal "
            "approximation, alpha=0.05, minimum 5 estimable lifecycles"),
        minimum_evidence={
            "minimum_estimable_completed_lifecycles": 5,
            "significance_alpha": 0.05,
        },
        scientific_finding_capable=True,
        hypothesis_capable=True,
        candidate_capable=False,
        governed_intervention_policy_id=None,
        capability_class="GENERATED_QUESTION_OUTCOME_EXPECTANCY_EVALUATOR",
        declared_scope_note=(
            "Scope: questions whose governed evidence class is the canonical "
            "completed-shadow-lifecycle outcome population (E1's own "
            "population_id).  The evaluator measures that population's mean "
            "R-multiple against a zero null.  It identifies no governed "
            "intervention, so it can never produce a candidate."),
        provenance={
            "authority": "research_engine.experiments.expected_value",
            "population_id": "CURRENT_COMPLETED_SHADOW_LIFECYCLES",
            "evidence_authority": (
                "shadow_runtime_v1 reconstructed completed lifecycles"),
            "derivation": (
                "E1's own governed POPULATION declaration and its own "
                "one-sample t-test versus zero"),
        },
    ),
    ProductionEvidenceClass(
        evidence_class=SESSION_CONDITIONED_ABSOLUTE_MEASURED_EXECUTION_SLIPPAGE,
        canonical_question_id="X3",
        canonical_evaluator_module="research_engine.experiments.x3_session_quality",
        governed_datasets=("execution_results", "execution_context"),
        observation_grain="one distinct account/broker execution result",
        estimand=(
            "session-conditioned absolute producer-measured execution slippage"),
        statistical_method=(
            "correlation_id-clustered quadratic Wald joint session test with "
            "Holm step-down pairwise contrasts, alpha=0.05"),
        minimum_evidence={
            "minimum_matched_account_results": 30,
            "minimum_results_per_session": 30,
            "minimum_decisions_per_session": 10,
            "significance_alpha": 0.05,
        },
        scientific_finding_capable=True,
        hypothesis_capable=True,
        candidate_capable=False,
        governed_intervention_policy_id=None,
        capability_class="GENERATED_QUESTION_EXECUTION_QUALITY_EVALUATOR",
        declared_scope_note=(
            "Scope: questions whose governed evidence class is the canonical "
            "session-conditioned execution-slippage population (X3's own "
            "population_id).  X3 identifies an execution-quality difference, "
            "never a governed trading intervention."),
        provenance={
            "authority": "research_engine.experiments.x3_session_quality",
            "population_id": (
                "SESSION_CONDITIONED_ABSOLUTE_MEASURED_EXECUTION_SLIPPAGE"),
            "evidence_authority": "execution_results_v1 + execution_context",
            "derivation": (
                "X3's own governed population and its own cluster-robust "
                "quadratic Wald / Holm-adjusted contrast inference"),
        },
    ),
    ProductionEvidenceClass(
        evidence_class=CURRENT_COMPLETED_SHADOW_LIFECYCLE_EXIT_PATH,
        canonical_question_id="EX1-EX4",
        canonical_evaluator_module="research_engine.experiments.exit_management",
        governed_datasets=("shadow_runtime",),
        observation_grain=(
            "one completed shadow lifecycle carrying realised MFE, MAE, "
            "exit reason and realised R-multiple"),
        estimand=(
            "descriptive distribution of the governed exit path "
            "(MFE/MAE/exit reason/realised R)"),
        statistical_method=(
            "descriptive distribution only; this evaluator defines no "
            "statistical test"),
        minimum_evidence={"minimum_eligible_lifecycles": 1},
        scientific_finding_capable=False,
        hypothesis_capable=False,
        candidate_capable=False,
        governed_intervention_policy_id=None,
        capability_class="GENERATED_QUESTION_DESCRIPTIVE_EVALUATOR",
        declared_scope_note=(
            "Scope: questions whose governed evidence class is the completed "
            "shadow lifecycle exit-path population.  exit_management's own "
            "scientific boundary declares EX1-EX4 observational and "
            "descriptive; this evaluator therefore declares DESCRIPTIVE_ONLY "
            "and can never create a finding."),
        provenance={
            "authority": "research_engine.experiments.exit_management",
            "population_id": (
                "exit_management._flatten_exit_population eligible population"),
            "evidence_authority": "shadow_runtime_v1 completed lifecycles",
            "derivation": (
                "exit_management's own declared scientific boundary "
                "(DESCRIPTIVE_ONLY) and its own exit-path population "
                "definition"),
        },
    ),
)


def evidence_class_catalogue() -> dict[str, ProductionEvidenceClass]:
    """Return the closed evidence-class vocabulary keyed by evidence class."""
    return {item.evidence_class: item for item in PRODUCTION_EVIDENCE_CLASSES}


def evidence_class_declaration(evidence_class: Any) -> ProductionEvidenceClass | None:
    """Return the declaration for one evidence class, or ``None`` when unknown."""
    return evidence_class_catalogue().get(str(evidence_class or "").strip())


def evidence_class_vocabulary() -> tuple[str, ...]:
    """The closed, deterministically ordered evidence-class vocabulary."""
    return tuple(item.evidence_class for item in PRODUCTION_EVIDENCE_CLASSES)


def evidence_class_identity() -> str:
    """Deterministic identity of the whole production evidence-class catalogue."""
    return hashlib.sha256(canonical_json({
        "catalogue_schema": EVIDENCE_CLASS_CATALOGUE_SCHEMA,
        "declarations": [
            item.identity_material() for item in PRODUCTION_EVIDENCE_CLASSES],
    }).encode("utf-8")).hexdigest()


# ── Structural generated-question families that are deliberately unsupported ──
#
# These evidence-class tokens already exist in the repository.  They are
# *structurally* part of the generated-question space but have no honest
# production evaluator, so a generated question about them must stay
# MISSING_EVALUATOR.  They are listed here - rather than omitted - so the
# capability denominator is the real structural family set and cannot be
# narrowed to make coverage look better.

UNSUPPORTED_EVIDENCE_CLASSES: tuple[Mapping[str, Any], ...] = (
    {
        "evidence_class": "EVENTS",
        "reason": "EVIDENCE_CLASS_NOT_BOUND_TO_GOVERNED_DATASET",
        "provenance": (
            "declared as the governed cell evidence class by "
            "research_engine.lifecycle.research_observation_space in the "
            "coverage and Q71 execution suites"),
        "detail": (
            "The token names no physical governed dataset, so no evaluator can "
            "declare an honest analytical population or evidence authority "
            "from it."),
    },
    {
        "evidence_class": "COUNTERFACTUAL_SHADOW_SIMULATED_OUTCOME",
        "reason": "NO_GOVERNED_SCIENTIFIC_RESULT_FROM_CANONICAL_AUTHORITY",
        "provenance": (
            "research_engine.experiments.d5_rejected_opportunity_validation "
            "(D5 EVIDENCE_CLASS)"),
        "detail": (
            "D5 computes a real counterfactual rejection analysis but declares "
            "no governed scientific result.  Manufacturing one here would "
            "fabricate the scientific semantics instead of reusing them."),
    },
    {
        "evidence_class": "SHADOW_CANDIDATE_PROSPECTIVE",
        "reason": "EVIDENCE_CLASS_NOT_A_GENERATED_QUESTION_POPULATION",
        "provenance": (
            "research_engine.control_plane.shadow_candidate_governance "
            "(candidate_status_citation evidence_class)"),
        "detail": (
            "This class describes prospective evidence about an existing "
            "governed candidate, not a governed population a generated "
            "coverage question can be researched against."),
    },
)


def unsupported_evidence_class_catalogue() -> dict[str, Mapping[str, Any]]:
    """Return the deliberately unsupported structural families keyed by token."""
    return {
        str(item["evidence_class"]): dict(item)
        for item in UNSUPPORTED_EVIDENCE_CLASSES
    }


def structural_generated_question_families() -> tuple[str, ...]:
    """The real structural evidence-class families of the generated-question space.

    Supported and unsupported families are both included; the denominator of any
    coverage claim is this tuple and is never narrowed.
    """
    return (
        evidence_class_vocabulary()
        + tuple(str(item["evidence_class"])
                for item in UNSUPPORTED_EVIDENCE_CLASSES)
    )


__all__ = [
    "CURRENT_COMPLETED_SHADOW_LIFECYCLE_EXIT_PATH",
    "CURRENT_COMPLETED_SHADOW_LIFECYCLES",
    "EVIDENCE_CLASS_CATALOGUE_SCHEMA",
    "PRODUCTION_EVIDENCE_CLASSES",
    "ProductionEvidenceClass",
    "ProductionEvidenceClassError",
    "SESSION_CONDITIONED_ABSOLUTE_MEASURED_EXECUTION_SLIPPAGE",
    "SUPPORTED_GOVERNED_DATASETS",
    "UNSUPPORTED_EVIDENCE_CLASSES",
    "evidence_class_catalogue",
    "evidence_class_declaration",
    "evidence_class_identity",
    "evidence_class_vocabulary",
    "structural_generated_question_families",
    "unsupported_evidence_class_catalogue",
]
