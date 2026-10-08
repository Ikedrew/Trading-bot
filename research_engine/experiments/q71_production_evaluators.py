"""Production Q71+ evaluators (Repair Block 2A).

Repair Block 2 delivered a complete generated-question execution architecture
with an empty production evaluator registry, so every real generated question
correctly resolved to ``MISSING_EVALUATOR``.  This module supplies the real
production evaluator families for the structural generated-question classes the
system can legitimately research from its existing governed evidence.

Every evaluator here:

* receives only governed/frozen evidence supplied by ``q71_worker`` and reads
  nothing else (no filesystem, no live S3, no current-state ingestion);
* reuses an existing canonical evaluator's own population construction and
  statistical computation rather than re-implementing it, so the scientific
  method is the tested one;
* re-identifies the canonical evaluator's own governed scientific result to the
  generated question identity without altering any scientific semantic;
* declares its own RB1 governed scientific result via ``meaningful`` /
  ``not_meaningful`` and derives its execution status from that declaration;
* fails closed with the RB1 not-meaningful vocabulary when the governed evidence
  cannot support the declared test.

Nothing here fabricates a p-value, a population, an intervention or a
threshold.  An evidence class that is not implemented here has no production
evaluator and stays ``MISSING_EVALUATOR``.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, Sequence

from research_engine.data_access.shadow_runtime_ingestion import (
    reconstruct_completed_shadow_trades,
)
from research_engine.data_quality.classifier import DataEpoch, classify_record
from research_engine.experiments.expected_value import (
    _governed_scientific_result as _e1_governed_result,
    run_expected_value,
)
from research_engine.experiments.exit_management import _flatten_exit_population
from research_engine.experiments.governed_scientific_result import (
    DESCRIPTIVE_ONLY,
    INSUFFICIENT_GOVERNED_EVIDENCE,
    SCIENTIFIC_RESULT_REPORT_KEY,
    TEST_NOT_ESTIMABLE,
    GovernedScientificResult,
    attach,
    governed_scientific_result,
    not_meaningful,
)
from research_engine.control_plane.governed_counterfactual_evidence import (
    COUNTERFACTUAL_EVIDENCE_CLASS,
    INCOMPLETE_REPLAY,
    INSUFFICIENT_SAMPLE,
    INVALID_COUNTERFACTUAL_SCHEMA,
    LEAKAGE_GUARD_FAILED,
    MISSING_BASELINE,
    MISSING_COUNTERFACTUAL_EVIDENCE,
    MISSING_M5_CANDLE_AUTHORITY,
    MISSING_SHADOW_LIFECYCLE_POPULATION,
    STALE_FRONTIER,
    SUPERSEDED_EVIDENCE,
    TREATMENT_SIGNATURE_MISMATCH,
    UNKNOWN_GOVERNED_POLICY,
    CounterfactualEvidenceError,
    rebuild_governed_exit_evidence,
    validate_governed_counterfactual_evidence,
    verify_counterfactual_rows,
    verify_governed_counterfactual_binding,
)
from research_engine.experiments import exit_policy_governed as governed_exit
from research_engine.experiments.x3_session_quality import build_x3_report


#: Report statuses this module may declare.  Each is a legitimate member of the
#: closed lifecycle vocabulary consumed by ``generated_question_lifecycle``.
STATUS_COMPLETE = "COMPLETE"
STATUS_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

#: Not-meaningful reasons that mean "the declared test cannot be estimated from
#: the governed evidence supplied", as opposed to "the evaluator answered but the
#: answer is not a scientific finding".
_NOT_ESTIMABLE_REASONS = frozenset({
    INSUFFICIENT_GOVERNED_EVIDENCE, TEST_NOT_ESTIMABLE,
})

#: The most specific governed fail-closed reasons, in reporting preference order.
#: A frozen artifact may carry several reason codes; the evaluator reports the
#: concrete evidence defect rather than the generic "no admissible evidence".
_SPECIFIC_FAIL_CLOSED_CODES = (
    MISSING_M5_CANDLE_AUTHORITY,
    MISSING_SHADOW_LIFECYCLE_POPULATION,
    INVALID_COUNTERFACTUAL_SCHEMA,
    INCOMPLETE_REPLAY,
    LEAKAGE_GUARD_FAILED,
    MISSING_BASELINE,
    TREATMENT_SIGNATURE_MISMATCH,
    UNKNOWN_GOVERNED_POLICY,
    STALE_FRONTIER,
    SUPERSEDED_EVIDENCE,
)


def _rows(datasets: Mapping[str, Any] | None, name: str) -> list[dict[str, Any]]:
    """Read one governed dataset exactly as the worker supplied it."""
    if not isinstance(datasets, Mapping):
        return []
    value = datasets.get(name)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [dict(row) for row in value if isinstance(row, Mapping)]


def _completed_lifecycles(
    datasets: Mapping[str, Any] | None,
    shadow_trades: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    """The governed completed-shadow-lifecycle population for this snapshot.

    The worker supplies the snapshot's reconstructed completed lifecycles as
    ``shadow_trades``; when only raw ``shadow_runtime`` rows are available the
    same canonical reconstruction is applied.  Nothing is loaded from current
    state.
    """
    if isinstance(shadow_trades, Sequence) and not isinstance(
            shadow_trades, (str, bytes)):
        supplied = [
            dict(row) for row in shadow_trades if isinstance(row, Mapping)]
        if supplied:
            return supplied
    reconstructed = _rows(datasets, "shadow_trades")
    if reconstructed:
        return reconstructed
    raw = _rows(datasets, "shadow_runtime")
    if raw:
        return [
            dict(row) for row in reconstruct_completed_shadow_trades(raw)]
    return []


def _reidentify(
    result: GovernedScientificResult, generated_question_id: str,
) -> GovernedScientificResult:
    """Re-identify a canonical evaluator's governed result to this question.

    Only the *question identity* is replaced.  The signal, estimate, sample
    size, significance method and value, population definition, falsification
    contract, intervention semantics and limitations remain exactly the ones the
    canonical evaluator computed.  The canonical authority question identity is
    preserved inside the population so the lineage is never lost.
    """
    if result.question_id == generated_question_id:
        return result
    signal = result.signal
    if signal is None:
        return replace(result, question_id=generated_question_id)
    population = dict(signal.population)
    canonical_authority = str(population.get("question_id") or "")
    population["question_id"] = generated_question_id
    if canonical_authority:
        population["canonical_authority_question_id"] = canonical_authority
    design = result.candidate_design
    if design is not None:
        # The governed result's own contract requires the candidate design's
        # applicable population to agree with the signal population on every
        # key.  The canonical authority question identity is preserved inside
        # both, so no lineage is lost and no scientific semantic is changed.
        applicable = dict(design.applicable_population)
        applicable["question_id"] = generated_question_id
        if canonical_authority:
            applicable["canonical_authority_question_id"] = canonical_authority
        design = replace(design, applicable_population=applicable)
    return replace(
        result, question_id=generated_question_id,
        signal=replace(signal, population=population),
        candidate_design=design)


def _status_for(governed: GovernedScientificResult) -> str:
    """Derive the execution status from the evaluator's own declaration.

    A declaration that the test is not estimable from the governed evidence is
    reported as INSUFFICIENT_DATA rather than a misleading COMPLETE.  A
    deliberate, estimable declaration that is simply not a scientific finding
    stays COMPLETE: the evaluator answered.
    """
    if governed.scientifically_meaningful:
        return STATUS_COMPLETE
    if governed.not_meaningful_reason in _NOT_ESTIMABLE_REASONS:
        return STATUS_INSUFFICIENT_DATA
    return STATUS_COMPLETE


def _report_with(
    report: Mapping[str, Any], governed: GovernedScientificResult,
) -> dict[str, Any]:
    """Replace a report's governed block with this evaluator's own declaration."""
    rebuilt = {key: value for key, value in dict(report).items()
               if key != SCIENTIFIC_RESULT_REPORT_KEY}
    return attach(rebuilt, governed)


# ── Family 1: completed shadow lifecycle outcome expectancy ───────────────────
# Reuses E1 (research_engine.experiments.expected_value) verbatim: its own
# CURRENT-epoch boundary, its own population construction, its own one-sample
# t-test versus zero and its own governed scientific result.

def completed_shadow_lifecycle_expectancy(
    *, generated_question_id: str,
    datasets: Mapping[str, Any] | None = None,
    shadow_trades: Sequence[Mapping[str, Any]] | None = None,
    evidence_class: str = "UNKNOWN",
    population_identity: str = "UNKNOWN",
    horizon: str = "UNKNOWN",
    **_ignored: Any,
) -> dict[str, Any]:
    """Governed outcome expectancy of the completed shadow lifecycle population."""
    population = _completed_lifecycles(datasets, shadow_trades)
    current = [
        record for record in population
        if classify_record(record) == DataEpoch.CURRENT
    ]
    excluded = len(population) - len(current)
    result = run_expected_value(current)
    governed = _reidentify(
        _e1_governed_result(result), generated_question_id)
    report = {
        "status": _status_for(governed),
        "conclusion": result.conclusion or (
            "the governed completed-lifecycle population could not be estimated"),
        "sample_size": int(result.total_trades),
        "confidence": result.confidence or "INSUFFICIENT_DATA",
        "statistical_output": {
            "method": "one_sample_mean_vs_zero",
            "t_statistic": result.t_statistic,
            "p_value_approximation": result.p_value_approx,
            "significant": bool(result.significant),
        },
        "key_metrics": {
            "mean_r_multiple": float(result.avg_r),
            "expected_value_r": float(result.expected_value),
            "win_rate": float(result.win_rate),
            "profit_factor": float(result.profit_factor),
            "governed_rows": int(result.total_trades),
            "records_excluded_non_current": int(excluded),
        },
        "recommendation": (
            "WAIT_FOR_EVIDENCE"
            if _status_for(governed) == STATUS_INSUFFICIENT_DATA
            else str(result.edge_classification or "NO_EDGE")),
        "warnings": [
            "Governed shadow outcomes are simulated research evidence, not "
            "realised live P&L.",
        ],
        "provenance": {
            "production_evaluator_family": (
                "completed_shadow_lifecycle_expectancy"),
            "canonical_authority": (
                "research_engine.experiments.expected_value"),
            "canonical_question_id": "E1",
            "reused_computation": (
                "run_expected_value + E1's own governed scientific result, "
                "re-identified to this generated question"),
        },
    }
    return _report_with(report, governed)


# ── Family 2: session-conditioned execution quality ──────────────────────────
# Reuses X3 (research_engine.experiments.x3_session_quality) verbatim: its own
# governed execution-evidence attestation, its own cluster-robust quadratic
# Wald joint session test, its own Holm step-down contrasts and its own
# governed scientific result.

def session_conditioned_execution_slippage(
    *, generated_question_id: str,
    datasets: Mapping[str, Any] | None = None,
    execution_results: Sequence[Mapping[str, Any]] | None = None,
    execution_contexts: Sequence[Mapping[str, Any]] | None = None,
    evidence_class: str = "UNKNOWN",
    population_identity: str = "UNKNOWN",
    horizon: str = "UNKNOWN",
    **_ignored: Any,
) -> dict[str, Any]:
    """Governed session-conditioned execution-quality inference from X3's evidence."""
    results = [
        dict(row) for row in (execution_results or [])
        if isinstance(row, Mapping)
    ] or _rows(datasets, "execution_results")
    contexts = [
        dict(row) for row in (execution_contexts or [])
        if isinstance(row, Mapping)
    ] or _rows(datasets, "execution_context")
    canonical_report = build_x3_report(results, contexts)
    declared = governed_scientific_result(canonical_report)
    if declared is None:  # pragma: no cover - X3 always declares a result
        governed = not_meaningful(
            generated_question_id, INSUFFICIENT_GOVERNED_EVIDENCE,
            detail="the canonical X3 evaluator declared no governed result")
    else:
        governed = _reidentify(declared, generated_question_id)
    report = dict(canonical_report)
    report["status"] = _status_for(governed)
    report["provenance"] = {
        **dict(report.get("provenance") or {}),
        "production_evaluator_family": (
            "session_conditioned_execution_slippage"),
        "canonical_authority": (
            "research_engine.experiments.x3_session_quality"),
        "canonical_question_id": "X3",
        "reused_computation": (
            "build_x3_report + X3's own governed scientific result, "
            "re-identified to this generated question"),
    }
    return _report_with(report, governed)


# ── Family 3: descriptive governed exit-path distribution ────────────────────
# Reuses exit_management's own exit-path population definition.  That module's
# own scientific boundary declares EX1-EX4 observational and descriptive, so
# this evaluator answers, and declares DESCRIPTIVE_ONLY: it can never create a
# scientific finding.

def governed_exit_path_distribution(
    *, generated_question_id: str,
    datasets: Mapping[str, Any] | None = None,
    shadow_trades: Sequence[Mapping[str, Any]] | None = None,
    evidence_class: str = "UNKNOWN",
    population_identity: str = "UNKNOWN",
    horizon: str = "UNKNOWN",
    **_ignored: Any,
) -> dict[str, Any]:
    """Descriptive profile of the governed exit path; explicitly not a finding."""
    lifecycles = _completed_lifecycles(datasets, shadow_trades)
    population = _flatten_exit_population(lifecycles)
    realised = _numbers(population, "pnl_r")
    mfe = _numbers(population, "mfe_r")
    mae = _numbers(population, "mae_r")
    exit_reasons: dict[str, int] = {}
    for row in population:
        reason = str(row.get("exit_reason") or "UNKNOWN")
        exit_reasons[reason] = exit_reasons.get(reason, 0) + 1
    if not population:
        governed = not_meaningful(
            generated_question_id, INSUFFICIENT_GOVERNED_EVIDENCE,
            detail=(
                "completed_lifecycles=" + str(len(lifecycles))
                + ":eligible_exit_rows=0"))
    else:
        governed = not_meaningful(
            generated_question_id, DESCRIPTIVE_ONLY,
            detail=(
                "eligible_exit_rows=" + str(len(population))
                + ":this evaluator defines no statistical test and names no "
                  "governed intervention"))
    report = {
        "status": _status_for(governed),
        "conclusion": (
            f"descriptive governed exit-path profile over {len(population)} "
            "eligible completed lifecycles"),
        "sample_size": len(population),
        "confidence": (
            "HIGH" if len(population) >= 200
            else "MEDIUM" if len(population) >= 30
            else "LOW" if population else "INSUFFICIENT_DATA"),
        "key_metrics": {
            "governed_rows": len(population),
            "mean_mfe_r": _mean(mfe),
            "mean_mae_r": _mean(mae),
            "mean_realised_r": _mean(realised),
            "exit_reason_counts": dict(sorted(exit_reasons.items())),


            "exit_reason_counts": dict(sorted(exit_reasons.items())),
        },
        "recommendation": "DESCRIPTIVE_ONLY",
        "warnings": [
            "This evaluator describes a governed distribution; it performs no "
            "test and supports no scientific finding.",
        ],
        "provenance": {
            "production_evaluator_family": "governed_exit_path_distribution",
            "canonical_authority": (
                "research_engine.experiments.exit_management"),
            "canonical_question_id": "EX1-EX4",
            "reused_computation": (
                "exit_management._flatten_exit_population eligible population "
                "and its own declared DESCRIPTIVE_ONLY scientific boundary"),
        },
    }
    return _report_with(report, governed)


# ── Family 4: governed counterfactual exit-policy intervention ───────────────
# Reuses the canonical HD09 authority (research_engine.experiments.
# exit_policy_governed) verbatim: its own exit-bar-path, baseline-reproduction
# and nine-policy candidate-replay populations, its own opportunity-clustered
# CR0 Holm family, and its own governed scientific result — including the
# governed intervention mapping it declares when exactly one governed policy is
# supported.  The evaluator reads ONLY the frozen governed counterfactual
# evidence artifact admitted to this question's snapshot; it never opens a
# filesystem path, a replay directory or live S3.

def _counterfactual_fail_closed(
    generated_question_id: str, reason_code: str, detail: str,
) -> dict[str, Any]:
    """Declare, machine-readably, that no governed counterfactual analysis ran."""
    governed = not_meaningful(
        generated_question_id, INSUFFICIENT_GOVERNED_EVIDENCE,
        detail=reason_code + (":" + detail if detail else ""))
    report = {
        "status": _status_for(governed),
        "conclusion": (
            "governed counterfactual exit-policy evidence is not available for "
            "this generated question"),
        "sample_size": 0,
        "confidence": "INSUFFICIENT_DATA",
        "key_metrics": {"governed_counterfactual_rows": 0},
        "recommendation": "WAIT_FOR_EVIDENCE",
        "warnings": [
            "No governed counterfactual evidence was admitted to this "
            "snapshot; nothing was analysed and nothing was inferred.",
        ],
        "failure_reason": reason_code,
        "missing_evidence": [detail or reason_code],
        "provenance": {
            "production_evaluator_family": "governed_exit_policy_counterfactual",
            "canonical_authority": "research_engine.experiments.exit_policy_governed",
            "canonical_question_id": "EX1",
            "fail_closed_reason": reason_code,
            "reused_computation": (
                "none: the governed counterfactual evidence was not admissible"),
        },
    }
    return _report_with(report, governed)
def governed_exit_policy_counterfactual(
    *, generated_question_id: str,
    datasets: Mapping[str, Any] | None = None,
    shadow_runtime: Sequence[Mapping[str, Any]] | None = None,
    governed_counterfactual_evidence: Any = None,
    governed_counterfactual_binding: Any = None,
    evidence_class: str = "UNKNOWN",
    population_identity: str = "UNKNOWN",
    horizon: str = "UNKNOWN",
    **_ignored: Any,
) -> dict[str, Any]:
    """Governed counterfactual exit-policy evaluation of one generated question.

    The only admissible evidence is the frozen governed counterfactual artifact
    the worker admitted for this snapshot.  Everything the canonical HD09
    authority needs is reconstructed from that artifact plus the snapshot's own
    ``shadow_runtime`` population, and the artifact is refused unless it was
    produced from exactly that population.
    """
    shadow = [
        dict(row) for row in (shadow_runtime or [])
        if isinstance(row, Mapping)
    ] or _rows(datasets, "shadow_runtime")
    if governed_counterfactual_evidence is None:
        return _counterfactual_fail_closed(
            generated_question_id, MISSING_COUNTERFACTUAL_EVIDENCE,
            "no frozen governed counterfactual evidence was admitted to this "
            "snapshot")
    try:
        artifact = validate_governed_counterfactual_evidence(
            governed_counterfactual_evidence)
        if governed_counterfactual_binding is not None:
            verify_governed_counterfactual_binding(
                governed_counterfactual_binding,
                snapshot_id=artifact.snapshot_id,
                snapshot_fingerprint=artifact.snapshot_fingerprint,
                investigation_epoch=artifact.investigation_epoch,
                evidence=artifact)
        if not artifact.scientifically_analysable:
            codes = list(artifact.reason_codes)
            specific = [
                code for code in codes if code in _SPECIFIC_FAIL_CLOSED_CODES]
            reason_code = (specific or codes or [MISSING_COUNTERFACTUAL_EVIDENCE])[0]
            return _counterfactual_fail_closed(
                generated_question_id, reason_code,
                ",".join(str(code) for code in codes))
        rebuilt = rebuild_governed_exit_evidence(artifact, shadow)
        if rebuilt.missing_evidence:
            return _counterfactual_fail_closed(
                generated_question_id, MISSING_M5_CANDLE_AUTHORITY,
                "; ".join(str(item) for item in rebuilt.missing_evidence))
        verify_counterfactual_rows(artifact, rebuilt)
    except CounterfactualEvidenceError as exc:
        return _counterfactual_fail_closed(
            generated_question_id, str(exc).split(":", 1)[0], str(exc))
    except Exception as exc:  # noqa: BLE001 - any failure must fail closed
        return _counterfactual_fail_closed(
            generated_question_id, INCOMPLETE_REPLAY,
            f"{type(exc).__name__}:{exc}")
    canonical_report = governed_exit.analyse_ex1(
        rebuilt.candidate, rebuilt.reproduction, rebuilt.path)
    declared = governed_scientific_result(canonical_report)
    if declared is None:  # pragma: no cover - the HD09 authority always declares
        governed = not_meaningful(
            generated_question_id, INSUFFICIENT_GOVERNED_EVIDENCE,
            detail="the canonical HD09 evaluator declared no governed result")
    else:
        governed = _reidentify(declared, generated_question_id)
    report = dict(canonical_report)
    report["status"] = _status_for(governed)
    report["provenance"] = {
        **dict(report.get("provenance") or {}),
        "production_evaluator_family": "governed_exit_policy_counterfactual",
        "canonical_authority": "research_engine.experiments.exit_policy_governed",
        "canonical_question_id": "EX1",
        "governed_counterfactual_evidence": {
            "dataset_id": artifact.dataset_id,
            "content_digest": artifact.content_digest,
            "evidence_class": artifact.evidence_class,
            "producer_identity": artifact.producer_identity,
            "producer_version": artifact.producer_version,
            "snapshot_id": artifact.snapshot_id,
            "snapshot_fingerprint": artifact.snapshot_fingerprint,
            "investigation_epoch": artifact.investigation_epoch,
            "source_dataset_identities": [
                list(item) for item in artifact.source_dataset_identities],
            "replay_method": artifact.replay_method,
            "replay_version": artifact.replay_version,
            "m5_authority": artifact.m5_authority,
            "admissible_rows": len(artifact.rows),
            "excluded_rows": len(artifact.exclusions),
            "reason_codes": list(artifact.reason_codes),
        },
        "reused_computation": (
            "exit_policy_governed.analyse_ex1 over the governed exit-bar-path, "
            "baseline-reproduction and nine-policy candidate-replay populations "
            "rebuilt from the frozen governed evidence, re-identified to this "
            "generated question"),
    }
    return _report_with(report, governed)






def _numbers(population: Sequence[Mapping[str, Any]], name: str) -> list[float]:
    values: list[float] = []
    for row in population:
        value = row.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            values.append(float(value))
    return values


def _mean(values: Sequence[float]) -> float | None:
    return (sum(values) / len(values)) if values else None


__all__ = [
    "STATUS_COMPLETE",
    "STATUS_INSUFFICIENT_DATA",
    "completed_shadow_lifecycle_expectancy",
    "governed_exit_path_distribution",
    "governed_exit_policy_counterfactual",
    "session_conditioned_execution_slippage",
]
