"""Governed-contract evaluator fixtures for generated (Q71+) questions.

These modules are *test doubles registered through the production evaluator
registry*.  They exist because production currently has no registered evaluator
for generated questions - which is exactly why a generated question must remain
``MISSING_EVALUATOR`` in production.  Registering one of these proves the
mechanism works; it does not claim production capability.

Each evaluator computes its statistic from the governed evidence rows it is
handed.  None of them hard-codes a scientific verdict, a threshold or an
intervention: the governed scientific result is assembled from the evaluator's
own computation, exactly as Repair Block 1 requires.
"""
from __future__ import annotations

from statistics import fmean
from typing import Any, Mapping

from research_engine.experiments.governed_scientific_result import (
    DESCRIPTIVE_ONLY,
    INSUFFICIENT_GOVERNED_EVIDENCE,
    NO_INTERVENTION_MAPPING,
    CandidateDesignContract,
    FalsificationContract,
    ScientificSignal,
    attach,
    meaningful,
    not_meaningful,
)


#: The exact governed policy the intervention-declaring fixture names.  It is
#: read from the governed catalogue rather than invented.
GOVERNED_POLICY_ID = "TRAIL_ACT_0_25R_DIST_0_10R_V1"

#: Minimum governed rows this evaluator will treat as an estimable sample.
MINIMUM_ROWS = 1


def _rows(datasets: Mapping[str, Any], name: str) -> list[dict[str, Any]]:
    value = datasets.get(name)
    return [dict(row) for row in value] if isinstance(value, list) else []


def _r_multiples(rows: list[dict[str, Any]]) -> list[float]:
    values: list[float] = []
    for row in rows:
        for key in ("pnl_r_multiple", "r_multiple"):
            value = row.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values.append(float(value))
                break
    return values


def _population(generated_question_id: str, evidence_class: str,
                population_identity: str) -> dict[str, Any]:
    """The population the evaluator actually observed, named explicitly."""
    return {
        "population_id": population_identity,
        "evidence_class": evidence_class,
        "question_id": generated_question_id,
    }


def descriptive_r_multiple_profile(
    *, generated_question_id: str, datasets: Mapping[str, Any] | None = None,
    evidence_class: str = "UNKNOWN", population_identity: str = "UNKNOWN",
    **_ignored: Any,
) -> dict[str, Any]:
    """A structurally COMPLETE evaluator that is deliberately descriptive."""
    values = _r_multiples(_rows(datasets or {}, "trade_truth"))
    return attach({
        "status": "COMPLETE",
        "conclusion": (
            f"descriptive r-multiple profile over {len(values)} governed rows"),
        "sample_size": len(values),
        "key_metrics": {
            "mean_r_multiple": fmean(values) if values else None,
            "governed_rows": len(values),
        },
        "recommendation": "DESCRIPTIVE_ONLY",
    }, not_meaningful(generated_question_id, DESCRIPTIVE_ONLY, detail=(
        "this evaluator profiles an outcome distribution; it defines no test "
        "and names no intervention")))


def insufficient_evidence_declaration(
    *, generated_question_id: str, datasets: Mapping[str, Any] | None = None,
    **_ignored: Any,
) -> dict[str, Any]:
    """An evaluator that ran but declares its governed evidence insufficient."""
    values = _r_multiples(_rows(datasets or {}, "trade_truth"))
    return attach({
        "status": "INSUFFICIENT_DATA",
        "conclusion": (
            f"only {len(values)} governed rows were available for estimation"),
        "sample_size": len(values),
        "reason_code": "GOVERNED_ROWS_BELOW_ESTIMATION_MINIMUM",
        "reason": "the governed sample cannot support an estimate",
        "key_metrics": {"governed_rows": len(values)},
    }, not_meaningful(
        generated_question_id, INSUFFICIENT_GOVERNED_EVIDENCE,
        detail=f"governed rows={len(values)}"))



def meaningful_mean_r_multiple(
    *, generated_question_id: str, datasets: Mapping[str, Any] | None = None,
    evidence_class: str = "UNKNOWN", population_identity: str = "UNKNOWN",
    **_ignored: Any,
) -> dict[str, Any]:
    """A meaningful evaluator that declares its own signal and falsification.

    It names no intervention: the governed pipeline must therefore stop at the
    hypothesis and say why.
    """
    values = _r_multiples(_rows(datasets or {}, "trade_truth"))
    estimate = fmean(values) if values else 0.0
    return attach({
        "status": "COMPLETE",
        "conclusion": (
            f"the governed population mean r-multiple is {estimate:.4f}"),
        "sample_size": len(values),
        "confidence": "MEDIUM",
        "statistical_output": {"method": "one_sample_mean_vs_zero"},
        "key_metrics": {"mean_r_multiple": estimate, "governed_rows": len(values)},
    }, meaningful(
        generated_question_id,
        signal=ScientificSignal(
            signal_type="GENERATED_QUESTION_MEAN_R_MULTIPLE",
            classification="SUPPORTED",
            primary_metric="mean_r_multiple",
            estimate=estimate,
            significance_method="one_sample_mean_vs_zero",
            significance_value=0.0,
            sample_size=max(len(values), MINIMUM_ROWS),
            population=_population(
                generated_question_id, evidence_class, population_identity),
            null_definition="the governed population mean r-multiple is zero",
            limitations=("observational: no randomised assignment",),
            effect_direction=(
                "POSITIVE" if estimate > 0 else
                "NEGATIVE" if estimate < 0 else "NULL"),
            effect_size=estimate,
        ),
        falsification=FalsificationContract(
            criteria=(
                "a governed replication measures a non-positive mean r-multiple",),
            failure_conditions={"mean_r_multiple": {"lte": 0.0}},
        ),
        no_intervention_reason=NO_INTERVENTION_MAPPING,
    ))


def meaningful_mean_r_multiple_with_governed_intervention(
    *, generated_question_id: str, datasets: Mapping[str, Any] | None = None,
    evidence_class: str = "UNKNOWN", population_identity: str = "UNKNOWN",
    **_ignored: Any,
) -> dict[str, Any]:
    """A meaningful evaluator that also names an existing governed policy."""
    values = _r_multiples(_rows(datasets or {}, "trade_truth"))
    estimate = fmean(values) if values else 0.0
    population = _population(
        generated_question_id, evidence_class, population_identity)
    return attach({
        "status": "COMPLETE",
        "conclusion": (
            f"the governed population mean r-multiple is {estimate:.4f} and a "
            "governed exit-geometry change is applicable"),
        "sample_size": len(values),
        "confidence": "HIGH",
        "statistical_output": {"method": "one_sample_mean_vs_zero"},
        "key_metrics": {"mean_r_multiple": estimate, "governed_rows": len(values)},
    }, meaningful(
        generated_question_id,
        signal=ScientificSignal(
            signal_type="GENERATED_QUESTION_MEAN_R_MULTIPLE",
            classification="SUPPORTED",
            primary_metric="mean_r_multiple",
            estimate=estimate,
            significance_method="one_sample_mean_vs_zero",
            significance_value=0.0,
            sample_size=max(len(values), MINIMUM_ROWS),
            population=population,
            null_definition="the governed population mean r-multiple is zero",
            limitations=("observational: no randomised assignment",),
            effect_direction=(
                "POSITIVE" if estimate > 0 else
                "NEGATIVE" if estimate < 0 else "NULL"),
            effect_size=estimate,
        ),
        falsification=FalsificationContract(
            criteria=(
                "a governed replication measures a non-positive mean r-multiple",),
            failure_conditions={"mean_r_multiple": {"lte": 0.0}},
        ),
        candidate_design=CandidateDesignContract(
            governed_policy_id=GOVERNED_POLICY_ID,
            treatment_component="STOP_GEOMETRY",
            treatment_parameters={
                "policy_id": GOVERNED_POLICY_ID,
                "policy_type": "TRAILING",
                "activation_r": 0.25,
                "distance_r": 0.10,
            },
            success_conditions={"mean_r_multiple": {"gt": 0.0}},
            failure_conditions={"mean_r_multiple": {"lte": 0.0}},
            validation_criteria={
                "required_sample": 100,
                "primary_metrics": ["mean_r_multiple"],
                "success_conditions": {"mean_r_multiple": {"gt": 0.0}},
                "failure_conditions": {"mean_r_multiple": {"lte": 0.0}},
            },
            applicable_population=population,
            intervention_rationale=(
                "the observed excursion distribution supports testing an "
                "existing governed trailing-stop geometry"),
        ),
    ))


def malformed_governed_result(
    *, generated_question_id: str, **_ignored: Any,
) -> dict[str, Any]:
    """An evaluator whose declared governed result contradicts itself."""
    return {
        "status": "COMPLETE",
        "conclusion": "malformed declaration",
        "sample_size": 10,
        "scientific_result": {
            "schema": "governed_evaluator_scientific_result_v1",
            "question_id": generated_question_id,
            "scientifically_meaningful": "yes",
        },
    }


def non_mapping_report(**kwargs: Any) -> Any:
    """An evaluator that returns something other than a report mapping."""
    return ["not", "a", "report"]


def required_evidence_evaluator(
    *, trade_truth: list[dict[str, Any]] | None = None, **kwargs: Any,
) -> dict[str, Any]:
    """An evaluator that requires a dataset the frozen snapshot may not hold."""
    rows = trade_truth or []
    return {
        "status": "COMPLETE",
        "conclusion": f"governed rows={len(rows)}",
        "sample_size": len(rows),
    }


def raising_evaluator(**kwargs: Any) -> Any:
    """An evaluator that fails while running."""
    raise RuntimeError("evaluator exploded")


def waiting_for_data_declaration(
    *, generated_question_id: str, datasets: Mapping[str, Any] | None = None,
    **_ignored: Any,
) -> dict[str, Any]:
    """An evaluator that ran but declares it is still waiting for evidence."""
    return attach({
        "status": "WAITING_FOR_DATA",
        "conclusion": "the governed evidence required by this test is absent",
        "reason_code": "GOVERNED_EVIDENCE_ABSENT",
        "missing_evidence": ["trade_truth"],
        "sample_size": 0,
    }, not_meaningful(
        generated_question_id, INSUFFICIENT_GOVERNED_EVIDENCE,
        detail="no governed rows were readable for the declared population"))


def negative_result_mean_r_multiple(
    *, generated_question_id: str, datasets: Mapping[str, Any] | None = None,
    evidence_class: str = "UNKNOWN", population_identity: str = "UNKNOWN",
    **_ignored: Any,
) -> dict[str, Any]:
    """A legitimate negative scientific result declared by the evaluator."""
    values = _r_multiples(_rows(datasets or {}, "trade_truth"))
    estimate = fmean(values) if values else 0.0
    return attach({
        "status": "NEGATIVE_RESULT",
        "conclusion": (
            f"the governed population mean r-multiple is {estimate:.4f}, which "
            "does not exceed the declared null"),
        "sample_size": len(values),
        "confidence": "MEDIUM",
        "statistical_output": {"method": "one_sample_mean_vs_zero"},
        "key_metrics": {"mean_r_multiple": estimate, "governed_rows": len(values)},
    }, meaningful(
        generated_question_id,
        signal=ScientificSignal(
            signal_type="GENERATED_QUESTION_MEAN_R_MULTIPLE",
            classification="NEGATIVE_RESULT",
            primary_metric="mean_r_multiple",
            estimate=estimate,
            significance_method="one_sample_mean_vs_zero",
            significance_value=0.0,
            sample_size=max(len(values), MINIMUM_ROWS),
            population=_population(
                generated_question_id, evidence_class, population_identity),
            null_definition="the governed population mean r-multiple is zero",
            limitations=("observational: no randomised assignment",),
            effect_direction=(
                "POSITIVE" if estimate > 0 else
                "NEGATIVE" if estimate < 0 else "NULL"),
            effect_size=estimate,
        ),
        falsification=FalsificationContract(
            criteria=(
                "a governed replication measures a non-positive mean r-multiple",),
            failure_conditions={"mean_r_multiple": {"lte": 0.0}},
        ),
        no_intervention_reason=NO_INTERVENTION_MAPPING,
    ))


def meaningful_without_falsification(
    *, generated_question_id: str, datasets: Mapping[str, Any] | None = None,
    evidence_class: str = "UNKNOWN", population_identity: str = "UNKNOWN",
    **_ignored: Any,
) -> dict[str, Any]:
    """A meaningful evaluator that supplies no falsification semantics."""
    values = _r_multiples(_rows(datasets or {}, "trade_truth"))
    estimate = fmean(values) if values else 0.0
    return attach({
        "status": "COMPLETE",
        "conclusion": (
            f"the governed population mean r-multiple is {estimate:.4f}"),
        "sample_size": len(values),
        "confidence": "MEDIUM",
        "key_metrics": {"mean_r_multiple": estimate},
    }, meaningful(
        generated_question_id,
        signal=ScientificSignal(
            signal_type="GENERATED_QUESTION_MEAN_R_MULTIPLE",
            classification="SUPPORTED",
            primary_metric="mean_r_multiple",
            estimate=estimate,
            significance_method="one_sample_mean_vs_zero",
            significance_value=0.0,
            sample_size=max(len(values), MINIMUM_ROWS),
            population=_population(
                generated_question_id, evidence_class, population_identity),
            null_definition="the governed population mean r-multiple is zero",
            limitations=("observational: no randomised assignment",),
            effect_direction="POSITIVE",
            effect_size=estimate,
        ),
        no_intervention_reason=NO_INTERVENTION_MAPPING,
    ))


__all__ = [
    "GOVERNED_POLICY_ID",
    "MINIMUM_ROWS",
    "descriptive_r_multiple_profile",
    "insufficient_evidence_declaration",
    "malformed_governed_result",
    "meaningful_mean_r_multiple",
    "meaningful_mean_r_multiple_with_governed_intervention",
    "meaningful_without_falsification",
    "negative_result_mean_r_multiple",
    "non_mapping_report",
    "raising_evaluator",
    "required_evidence_evaluator",
    "waiting_for_data_declaration",
]
