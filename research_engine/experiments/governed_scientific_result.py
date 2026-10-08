"""Governed evaluator-side scientific-result contract (Repair Block 1).

The canonical question cycle can only turn a *real* evaluator result into a
governed finding when that evaluator says, in its own governed vocabulary, that
the result is scientifically meaningful.  This module defines that vocabulary
and the exact shape it must take.

Authority rules enforced here:

* Scientific authority originates in the evaluator.  Nothing in this module
  computes, infers or defaults a p-value, a threshold, an effect size, a
  population or an intervention.
* Every field is optional for an evaluator that cannot support it.  Absence is
  always valid and is always safer than a fabricated claim.
* ``scientifically_meaningful=True`` is a *claim*: it is accepted only when the
  claim is fully evidenced (estimand, estimate, test method, sample, population,
  null definition and limitations are all present).
* ``scientifically_meaningful=False`` must carry a machine-readable reason from
  the closed vocabulary below.  It exists so a structurally ``COMPLETE``
  evaluator can state, mechanically, that it is not a scientific finding.
* A candidate design is accepted only when the evaluator names a policy that
  already exists in the governed policy catalogue and supplies the exact
  governed treatment parameters plus complete validation criteria.

The canonical normalizer transports the resulting metrics; it never creates
them.  See ``governed_scientific_metrics``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from research_engine.registry.exit_policy_adjudication import CANDIDATE_POLICIES_V1


SCIENTIFIC_RESULT_SCHEMA = "governed_evaluator_scientific_result_v1"
SCIENTIFIC_RESULT_REPORT_KEY = "scientific_result"

# â”€â”€ Closed "not scientifically meaningful" vocabulary â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# A structurally COMPLETE result that is not a scientific finding must say which
# of these applies.  The list is closed so downstream consumers can rely on it.
DESCRIPTIVE_ONLY = "DESCRIPTIVE_ONLY"
GOVERNANCE_STATE_ONLY = "GOVERNANCE_STATE_ONLY"
NO_STATISTICAL_TEST_DEFINED = "NO_STATISTICAL_TEST_DEFINED"
INSUFFICIENT_CAUSAL_SUPPORT = "INSUFFICIENT_CAUSAL_SUPPORT"
NO_INTERVENTION_MAPPING = "NO_INTERVENTION_MAPPING"
INSUFFICIENT_GOVERNED_EVIDENCE = "INSUFFICIENT_GOVERNED_EVIDENCE"
TEST_NOT_ESTIMABLE = "TEST_NOT_ESTIMABLE"
AMBIGUOUS_GOVERNED_INTERVENTION = "AMBIGUOUS_GOVERNED_INTERVENTION"
POPULATION_NOT_DEFINED = "POPULATION_NOT_DEFINED"

NOT_MEANINGFUL_REASONS = frozenset({
    DESCRIPTIVE_ONLY,
    GOVERNANCE_STATE_ONLY,
    NO_STATISTICAL_TEST_DEFINED,
    INSUFFICIENT_CAUSAL_SUPPORT,
    NO_INTERVENTION_MAPPING,
    INSUFFICIENT_GOVERNED_EVIDENCE,
    TEST_NOT_ESTIMABLE,
    AMBIGUOUS_GOVERNED_INTERVENTION,
    POPULATION_NOT_DEFINED,
})

EFFECT_DIRECTIONS = frozenset({"POSITIVE", "NEGATIVE", "NULL", "UNKNOWN"})

# Governed policy catalogue: the only policy identities an evaluator may name.
GOVERNED_POLICY_IDS = frozenset(
    str(item["policy_id"]) for item in CANDIDATE_POLICIES_V1
)
GOVERNED_POLICIES = {
    str(item["policy_id"]): dict(item) for item in CANDIDATE_POLICIES_V1
}

# The treatment component each governed policy family is representable as in
# governed treatment memory.  This mirrors the single authoritative mapping in
# ``scientific_state_bridge._treatment_signature``; it is declared here so an
# evaluator that names a policy must also name the matching component.
POLICY_TREATMENT_COMPONENTS = {
    "TRAILING": "STOP_GEOMETRY",
    "REDUCED_TP": "TARGET_GEOMETRY",
    "TIME_CAP": "TIMING",
}


class ScientificResultError(ValueError):
    """The evaluator's governed scientific result is invalid; fail closed."""


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ScientificResultError("SCIENTIFIC_RESULT_INVALID_TEXT:" + label)
    return value.strip()


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScientificResultError("SCIENTIFIC_RESULT_INVALID_NUMBER:" + label)
    return float(value)


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not value:
        raise ScientificResultError("SCIENTIFIC_RESULT_INVALID_MAPPING:" + label)
    return dict(value)


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ScientificResultError("SCIENTIFIC_RESULT_INVALID_POSITIVE_INT:" + label)
    return int(value)


@dataclass(frozen=True)
class ScientificSignal:
    """The evaluator's own scientific claim about one governed population.

    Every field here must already exist in the evaluator's computation.  This
    object performs no inference and derives no threshold.
    """

    signal_type: str
    classification: str
    primary_metric: str
    estimate: float
    significance_method: str
    significance_value: float
    sample_size: int
    population: Mapping[str, Any]
    null_definition: str
    limitations: tuple[str, ...]
    effect_direction: str = "UNKNOWN"
    effect_size: float | None = None
    confidence_interval: tuple[float, float] | None = None

    def validate(self) -> None:
        _text(self.signal_type, "signal_type")
        _text(self.classification, "classification")
        _text(self.primary_metric, "primary_metric")
        _number(self.estimate, "estimate")
        _text(self.significance_method, "significance_method")
        _number(self.significance_value, "significance_value")
        _positive_int(self.sample_size, "sample_size")
        _mapping(self.population, "population")
        _text(self.null_definition, "null_definition")
        if not self.limitations or not all(
            isinstance(item, str) and item.strip() for item in self.limitations
        ):
            raise ScientificResultError("SCIENTIFIC_RESULT_LIMITATIONS_REQUIRED")
        if self.effect_direction not in EFFECT_DIRECTIONS:
            raise ScientificResultError(
                "SCIENTIFIC_RESULT_INVALID_EFFECT_DIRECTION:" + str(self.effect_direction))
        if self.effect_size is not None:
            _number(self.effect_size, "effect_size")
        if self.confidence_interval is not None:
            if (
                not isinstance(self.confidence_interval, (list, tuple))
                or len(self.confidence_interval) != 2
            ):
                raise ScientificResultError("SCIENTIFIC_RESULT_INVALID_CONFIDENCE_INTERVAL")
            low = _number(self.confidence_interval[0], "confidence_interval_low")
            high = _number(self.confidence_interval[1], "confidence_interval_high")
            if low > high:
                raise ScientificResultError("SCIENTIFIC_RESULT_INVERTED_CONFIDENCE_INTERVAL")

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_type": self.signal_type,
            "classification": self.classification,
            "primary_metric": self.primary_metric,
            "estimate": self.estimate,
            "significance_method": self.significance_method,
            "significance_value": self.significance_value,
            "sample_size": self.sample_size,
            "population": dict(self.population),
            "null_definition": self.null_definition,
            "limitations": list(self.limitations),
            "effect_direction": self.effect_direction,
            "effect_size": self.effect_size,
            "confidence_interval": (
                None if self.confidence_interval is None
                else [self.confidence_interval[0], self.confidence_interval[1]]),
        }



@dataclass(frozen=True)
class FalsificationContract:
    """Measurable criteria whose satisfaction would falsify the claim.

    ``criteria`` must be human-auditable measurable statements.  They are the
    evaluator's own operational failure conditions, never invented thresholds.
    """

    criteria: tuple[str, ...]
    failure_conditions: Mapping[str, Any] = field(default_factory=dict)
    minimum_evidence_requirements: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.criteria or not all(
            isinstance(item, str) and item.strip() for item in self.criteria
        ):
            raise ScientificResultError("SCIENTIFIC_RESULT_FALSIFICATION_CRITERIA_REQUIRED")

    def to_dict(self) -> dict[str, Any]:
        return {
            "criteria": [item.strip() for item in self.criteria],
            "failure_conditions": dict(self.failure_conditions),
            "minimum_evidence_requirements": dict(self.minimum_evidence_requirements),
        }


def validate_validation_criteria(criteria: Any) -> dict[str, Any]:
    """Require a complete, non-invented validation plan or fail closed."""
    value = _mapping(criteria, "validation_criteria")
    _positive_int(value.get("required_sample"), "validation_criteria.required_sample")
    metrics = value.get("primary_metrics")
    if (
        not isinstance(metrics, (list, tuple))
        or not metrics
        or not all(isinstance(item, str) and item.strip() for item in metrics)
    ):
        raise ScientificResultError("SCIENTIFIC_RESULT_INVALID_PRIMARY_METRICS")
    for name in ("success_conditions", "failure_conditions"):
        _mapping(value.get(name), "validation_criteria." + name)
    return value



@dataclass(frozen=True)
class CandidateDesignContract:
    """A governed intervention the evaluator itself identified.

    A candidate design is a *proposal*, never an approval.  It is accepted only
    for a policy that already exists in the governed catalogue and only with the
    catalogue's exact parameters.
    """

    governed_policy_id: str
    treatment_component: str
    treatment_parameters: Mapping[str, Any]
    success_conditions: Mapping[str, Any]
    failure_conditions: Mapping[str, Any]
    validation_criteria: Mapping[str, Any]
    applicable_population: Mapping[str, Any]
    intervention_rationale: str

    def validate(self) -> None:
        policy_id = _text(self.governed_policy_id, "governed_policy_id")
        policy = GOVERNED_POLICIES.get(policy_id)
        if policy is None:
            raise ScientificResultError("SCIENTIFIC_RESULT_UNGOVERNED_POLICY:" + policy_id)
        expected_component = POLICY_TREATMENT_COMPONENTS.get(str(policy.get("policy_type")))
        if self.treatment_component != expected_component:
            raise ScientificResultError(
                "SCIENTIFIC_RESULT_TREATMENT_COMPONENT_MISMATCH:" + policy_id)
        parameters = _mapping(self.treatment_parameters, "treatment_parameters")
        if parameters != policy:
            raise ScientificResultError(
                "SCIENTIFIC_RESULT_TREATMENT_PARAMETERS_MISMATCH:" + policy_id)
        _mapping(self.success_conditions, "success_conditions")
        _mapping(self.failure_conditions, "failure_conditions")
        _mapping(self.applicable_population, "applicable_population")
        _text(self.intervention_rationale, "intervention_rationale")
        validate_validation_criteria(self.validation_criteria)

    def to_dict(self) -> dict[str, Any]:
        return {
            "governed_policy_id": self.governed_policy_id,
            "treatment_component": self.treatment_component,
            "treatment_parameters": dict(self.treatment_parameters),
            "success_conditions": dict(self.success_conditions),
            "failure_conditions": dict(self.failure_conditions),
            "validation_criteria": dict(self.validation_criteria),
            "applicable_population": dict(self.applicable_population),
            "intervention_rationale": self.intervention_rationale,
        }



@dataclass(frozen=True)
class GovernedScientificResult:
    """The complete governed scientific result an evaluator may emit."""

    question_id: str
    scientifically_meaningful: bool
    not_meaningful_reason: str | None = None
    signal: ScientificSignal | None = None
    falsification: FalsificationContract | None = None
    candidate_design: CandidateDesignContract | None = None
    no_intervention_reason: str | None = None
    detail: str | None = None

    def validate(self) -> None:
        _text(self.question_id, "question_id")
        if self.scientifically_meaningful:
            if self.not_meaningful_reason is not None:
                raise ScientificResultError("SCIENTIFIC_RESULT_CONTRADICTORY_REASON")
            if self.signal is None:
                raise ScientificResultError("SCIENTIFIC_RESULT_SIGNAL_REQUIRED")
            self.signal.validate()
            if self.falsification is not None:
                self.falsification.validate()
            if self.candidate_design is not None:
                self.candidate_design.validate()
                if self.falsification is None:
                    # A governed intervention is unverifiable without an explicit
                    # falsification contract; refuse rather than invent one.
                    raise ScientificResultError(
                        "SCIENTIFIC_RESULT_CANDIDATE_REQUIRES_FALSIFICATION")
                population = dict(self.signal.population)
                applicable = dict(self.candidate_design.applicable_population)
                if any(
                    key not in applicable or applicable[key] != value
                    for key, value in population.items()
                ):
                    raise ScientificResultError(
                        "SCIENTIFIC_RESULT_CANDIDATE_POPULATION_MISMATCH")
            elif not self.no_intervention_reason:
                raise ScientificResultError(
                    "SCIENTIFIC_RESULT_INTERVENTION_MAPPING_REQUIRED")
        else:
            if self.signal is not None or self.falsification is not None \
                    or self.candidate_design is not None:
                raise ScientificResultError("SCIENTIFIC_RESULT_CONTRADICTORY_NOT_MEANINGFUL")
            if self.not_meaningful_reason not in NOT_MEANINGFUL_REASONS:
                raise ScientificResultError(
                    "SCIENTIFIC_RESULT_REASON_NOT_IN_VOCABULARY:"
                    + str(self.not_meaningful_reason))

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema": SCIENTIFIC_RESULT_SCHEMA,
            "question_id": self.question_id,
            "scientifically_meaningful": self.scientifically_meaningful,
            "not_meaningful_reason": self.not_meaningful_reason,
            "detail": self.detail,
            "scientific_signal": None if self.signal is None else self.signal.to_dict(),
            "falsification": (
                None if self.falsification is None else self.falsification.to_dict()),
            "candidate_design": (
                None if self.candidate_design is None else self.candidate_design.to_dict()),
            "no_intervention_reason": self.no_intervention_reason,
        }


def not_meaningful(
    question_id: str, reason: str, *, detail: str | None = None,
) -> GovernedScientificResult:
    """Declare explicitly that this result is not a scientific finding."""
    return GovernedScientificResult(
        question_id=str(question_id),
        scientifically_meaningful=False,
        not_meaningful_reason=str(reason),
        detail=None if detail is None else str(detail),
    )


def meaningful(
    question_id: str,
    *,
    signal: ScientificSignal,
    falsification: FalsificationContract | None = None,
    candidate_design: CandidateDesignContract | None = None,
    no_intervention_reason: str | None = None,
) -> GovernedScientificResult:
    """Declare a fully evidenced scientific claim."""
    return GovernedScientificResult(
        question_id=str(question_id),
        scientifically_meaningful=True,
        signal=signal,
        falsification=falsification,
        candidate_design=candidate_design,
        no_intervention_reason=no_intervention_reason,
    )



def attach(report: dict[str, Any], result: GovernedScientificResult) -> dict[str, Any]:
    """Attach a governed scientific result to an evaluator report.

    Returns the same mapping so callers can write ``return attach(report, r)``.
    """
    result.validate()
    report[SCIENTIFIC_RESULT_REPORT_KEY] = result.to_dict()
    return report


def result_to_metrics(result: GovernedScientificResult) -> dict[str, Any]:
    """Project a governed result onto the flat keys the canonical pipeline reads.

    This is a pure transport projection.  It adds no meaning: every value comes
    from the evaluator's own declaration.
    """
    result.validate()
    metrics: dict[str, Any] = {
        "scientific_result_schema": SCIENTIFIC_RESULT_SCHEMA,
        "scientifically_meaningful": result.scientifically_meaningful,
    }
    if not result.scientifically_meaningful:
        metrics["scientific_not_meaningful_reason"] = result.not_meaningful_reason
        if result.detail:
            metrics["scientific_not_meaningful_detail"] = result.detail
        return metrics
    signal = result.signal
    assert signal is not None  # guaranteed by validate()
    metrics.update({
        "scientific_signal_type": signal.signal_type,
        "scientific_classification": signal.classification,
        "primary_metric": signal.primary_metric,
        "estimate": signal.estimate,
        "sample_size": signal.sample_size,
        "population": dict(signal.population),
        "null_definition": signal.null_definition,
        "significance_method": signal.significance_method,
        "significance_value": signal.significance_value,
        "effect_direction": signal.effect_direction,
    })
    if signal.effect_size is not None:
        metrics["effect_size"] = signal.effect_size
    if signal.confidence_interval is not None:
        metrics["confidence_interval"] = [
            signal.confidence_interval[0], signal.confidence_interval[1]]
    if result.falsification is not None:
        metrics["falsification_criteria"] = list(result.falsification.criteria)
        if result.falsification.failure_conditions:
            metrics["falsification_failure_conditions"] = dict(
                result.falsification.failure_conditions)
        if result.falsification.minimum_evidence_requirements:
            metrics["falsification_minimum_evidence"] = dict(
                result.falsification.minimum_evidence_requirements)
    if result.no_intervention_reason:
        metrics["no_governed_intervention_reason"] = result.no_intervention_reason
    if result.candidate_design is not None:
        design = result.candidate_design
        metrics.update({
            "governed_policy_id": design.governed_policy_id,
            "governed_policy_parameters": dict(design.treatment_parameters),
            "treatment_component": design.treatment_component,
            "target_component": design.treatment_component,
            "validation_criteria": dict(design.validation_criteria),
            "applicable_population": dict(design.applicable_population),
            "intervention_rationale": design.intervention_rationale,
        })
    return metrics



def _parse_signal(value: Any) -> ScientificSignal:
    if not isinstance(value, Mapping):
        raise ScientificResultError("SCIENTIFIC_RESULT_SIGNAL_MALFORMED")
    interval = value.get("confidence_interval")
    return ScientificSignal(
        signal_type=value.get("signal_type"),
        classification=value.get("classification"),
        primary_metric=value.get("primary_metric"),
        estimate=value.get("estimate"),
        significance_method=value.get("significance_method"),
        significance_value=value.get("significance_value"),
        sample_size=value.get("sample_size"),
        population=value.get("population"),
        null_definition=value.get("null_definition"),
        limitations=tuple(value.get("limitations") or ()),
        effect_direction=str(value.get("effect_direction") or "UNKNOWN"),
        effect_size=value.get("effect_size"),
        confidence_interval=(
            None if interval is None else (interval[0], interval[1])),
    )


def _parse_falsification(value: Any) -> FalsificationContract:
    if not isinstance(value, Mapping):
        raise ScientificResultError("SCIENTIFIC_RESULT_FALSIFICATION_MALFORMED")
    return FalsificationContract(
        criteria=tuple(value.get("criteria") or ()),
        failure_conditions=dict(value.get("failure_conditions") or {}),
        minimum_evidence_requirements=dict(
            value.get("minimum_evidence_requirements") or {}),
    )


def _parse_candidate_design(value: Any) -> CandidateDesignContract:
    if not isinstance(value, Mapping):
        raise ScientificResultError("SCIENTIFIC_RESULT_CANDIDATE_DESIGN_MALFORMED")
    return CandidateDesignContract(
        governed_policy_id=value.get("governed_policy_id"),
        treatment_component=value.get("treatment_component"),
        treatment_parameters=value.get("treatment_parameters"),
        success_conditions=value.get("success_conditions"),
        failure_conditions=value.get("failure_conditions"),
        validation_criteria=value.get("validation_criteria"),
        applicable_population=value.get("applicable_population"),
        intervention_rationale=value.get("intervention_rationale"),
    )


def result_from_dict(value: Any) -> GovernedScientificResult:
    """Strictly parse a governed scientific result; malformed input fails closed."""
    if not isinstance(value, Mapping):
        raise ScientificResultError("SCIENTIFIC_RESULT_MALFORMED")
    if value.get("schema") != SCIENTIFIC_RESULT_SCHEMA:
        raise ScientificResultError("SCIENTIFIC_RESULT_SCHEMA_MISMATCH")
    meaningful_flag = value.get("scientifically_meaningful")
    if not isinstance(meaningful_flag, bool):
        raise ScientificResultError("SCIENTIFIC_RESULT_MEANINGFUL_FLAG_INVALID")
    signal = value.get("scientific_signal")
    falsification = value.get("falsification")
    design = value.get("candidate_design")
    result = GovernedScientificResult(
        question_id=str(value.get("question_id") or ""),
        scientifically_meaningful=meaningful_flag,
        not_meaningful_reason=value.get("not_meaningful_reason"),
        signal=None if signal is None else _parse_signal(signal),
        falsification=None if falsification is None else _parse_falsification(falsification),
        candidate_design=None if design is None else _parse_candidate_design(design),
        no_intervention_reason=value.get("no_intervention_reason"),
        detail=value.get("detail"),
    )
    result.validate()
    return result


def governed_scientific_metrics(report: Any) -> dict[str, Any]:
    """Canonical transport hook: extract and validate a report's governed result.

    Returns the flat metric projection, or ``{}`` when the evaluator emitted no
    governed scientific result.  Raises :class:`ScientificResultError` when a
    declared result is malformed or self-contradictory, so the canonical cycle
    fails the question closed instead of publishing unsupported science.
    """
    if not isinstance(report, Mapping):
        return {}
    block = report.get(SCIENTIFIC_RESULT_REPORT_KEY)
    if block is None:
        return {}
    return result_to_metrics(result_from_dict(block))


def governed_scientific_result(report: Any) -> GovernedScientificResult | None:
    """Return the validated governed result declared by a report, if any."""
    if not isinstance(report, Mapping):
        return None
    block = report.get(SCIENTIFIC_RESULT_REPORT_KEY)
    if block is None:
        return None
    return result_from_dict(block)



__all__ = [
    "AMBIGUOUS_GOVERNED_INTERVENTION",
    "CandidateDesignContract",
    "DESCRIPTIVE_ONLY",
    "EFFECT_DIRECTIONS",
    "FalsificationContract",
    "GOVERNANCE_STATE_ONLY",
    "GOVERNED_POLICIES",
    "GOVERNED_POLICY_IDS",
    "GovernedScientificResult",
    "INSUFFICIENT_CAUSAL_SUPPORT",
    "INSUFFICIENT_GOVERNED_EVIDENCE",
    "NOT_MEANINGFUL_REASONS",
    "NO_INTERVENTION_MAPPING",
    "NO_STATISTICAL_TEST_DEFINED",
    "POLICY_TREATMENT_COMPONENTS",
    "POPULATION_NOT_DEFINED",
    "SCIENTIFIC_RESULT_REPORT_KEY",
    "SCIENTIFIC_RESULT_SCHEMA",
    "ScientificResultError",
    "ScientificSignal",
    "TEST_NOT_ESTIMABLE",
    "attach",
    "governed_scientific_metrics",
    "governed_scientific_result",
    "meaningful",
    "not_meaningful",
    "result_from_dict",
    "result_to_metrics",
    "validate_validation_criteria",
]
