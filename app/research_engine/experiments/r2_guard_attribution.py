"""Canonical HD10 R2 guard-exclusive expectancy attribution.

The runner consumes RW9.1 governed evidence.  It does not reconstruct raw
lineage, use guard counts as outcomes, allocate multi-guard outcomes, or alter
production risk behaviour.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from research_engine.control_plane.evidence_provenance import (
    CURRENT,
    evidence_digest,
    validate_evidence_provenance,
)
from research_engine.control_plane.evidence_readiness import (
    EvidenceRequirement,
    evaluate_evidence_readiness,
)
from research_engine.control_plane.risk_policy_evidence import (
    BASELINE_AUTHORITY_CURRENT,
    GUARD_IDS,
    GUARD_TAXONOMY_DIGEST,
    SCHEMA_VERSION as EVIDENCE_SCHEMA_VERSION,
    RiskPolicyEvidence,
    build_risk_policy_evidence,
)
from research_engine.experiments.experiment_base import build_fingerprint_from_provenance
from research_engine.registry.risk_policy_adjudication import (
    BASELINE_RISK_POLICY_V1,
    CLUSTERED_INFERENCE,
    GUARD_TAXONOMY_V1,
    HD10_ADJUDICATED_CONTRACT,
    HD10_ADJUDICATION_VERSION,
    R2_CONTRACT,
    REPORT_OWNERSHIP,
    SAMPLE_AND_READINESS_CONTRACT,
    Z_95,
)

REPORT_SCHEMA_VERSION = "r2_guard_attribution_v1"
INFERENCE_SCHEMA_VERSION = "hd10_r2_guard_exclusive_cr0_normal_v1"
REPORT_FILENAME = REPORT_OWNERSHIP["R2"]
GUARDS = tuple(guard["guard_id"] for guard in GUARD_TAXONOMY_V1)
HOLM_FAMILY = tuple(f"{guard}_blocked_minus_allowed_mean_r" for guard in GUARDS)
PRE_DECISION_STRATUM_FIELDS: tuple[str, ...] = ()

_ROOT = Path(__file__).resolve().parents[2]
_REPORTS_DIR = _ROOT / "analysis" / "reports"
_STRUCTURAL_EXCLUSIONS = frozenset({
    "AMBIGUOUS_CANONICAL_IDENTITY",
    "AMBIGUOUS_DECISION_AUTHORITY",
    "AMBIGUOUS_OUTCOME_AUTHORITY",
    "ACCOUNT_FANOUT_PSEUDOREPLICATION",
    "BASELINE_RISK_POLICY_AUTHORITY_MISSING",
    "BASELINE_RISK_POLICY_IDENTITY_DRIFT",
    "CONFLICTING_GOVERNED_OUTCOME",
    "DECISION_OUTCOME_LINEAGE_MISMATCH",
    "MISSING_DECISION_AUTHORITY",
    "NON_CURRENT_DECISION_AUTHORITY_PRESENT",
    "NON_CURRENT_OUTCOME_AUTHORITY_PRESENT",
})


class R2AnalysisError(ValueError):
    """A structural R2 authority or inference failure."""


def _finite(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise R2AnalysisError("required analytical value is not numeric")
    result = float(value)
    if not math.isfinite(result):
        raise R2AnalysisError("required analytical value is not finite")
    return result


def _mean(values: Sequence[float]) -> float:
    if not values:
        raise R2AnalysisError("an R2 contrast arm has no governed outcomes")
    return math.fsum(values) / len(values)


def _normal_two_sided_p(estimate: float, standard_error: float) -> float:
    if standard_error < 0:
        raise R2AnalysisError("negative standard error")
    if standard_error == 0:
        return 1.0 if estimate == 0 else 0.0
    return math.erfc(abs(estimate / standard_error) / math.sqrt(2.0))


def _cr0_mean_variance(values: Sequence[float], estimate: float) -> float:
    n = len(values)
    return math.fsum((value - estimate) ** 2 for value in values) / (n * n)


def holm_adjust(tests: Sequence[tuple[str, float]]) -> dict[str, float]:
    """Deterministic Holm adjustment over exactly the frozen four guards."""
    if tuple(test_id for test_id, _ in tests) != HOLM_FAMILY:
        raise R2AnalysisError("R2 Holm family is incomplete, duplicated, or reordered")
    checked = [(test_id, _finite(p_value)) for test_id, p_value in tests]
    if any(not 0.0 <= p_value <= 1.0 for _, p_value in checked):
        raise R2AnalysisError("R2 Holm family contains an invalid p-value")
    tie_order = {test_id: index for index, test_id in enumerate(HOLM_FAMILY)}
    ranked = sorted(checked, key=lambda item: (item[1], tie_order[item[0]]))
    adjusted: dict[str, float] = {}
    running = 0.0
    for rank, (test_id, p_value) in enumerate(ranked):
        running = max(running, min(1.0, (len(ranked) - rank) * p_value))
        adjusted[test_id] = running
    return {test_id: adjusted[test_id] for test_id in HOLM_FAMILY}


def estimate_guard_contrast(
    guard_id: str,
    candidate_values: Sequence[float],
    comparator_values: Sequence[float],
) -> dict[str, Any]:
    """Estimate guard-exclusive blocked minus governed allowed mean R."""
    if guard_id not in GUARDS:
        raise R2AnalysisError(f"unknown HD10 guard id: {guard_id!r}")
    candidate = tuple(_finite(value) for value in candidate_values)
    comparator = tuple(_finite(value) for value in comparator_values)
    candidate_mean = _mean(candidate)
    comparator_mean = _mean(comparator)
    estimate = candidate_mean - comparator_mean
    variance = (
        _cr0_mean_variance(candidate, candidate_mean)
        + _cr0_mean_variance(comparator, comparator_mean)
    )
    standard_error = math.sqrt(variance)
    return {
        "test_identity": f"{guard_id}_blocked_minus_allowed_mean_r",
        "guard_id": guard_id,
        "estimand": R2_CONTRACT["per_guard_estimand"],
        "candidate_definition": (
            f"{guard_id}-exclusive blocked canonical opportunities with governed "
            "shadow-counterfactual outcomes"
        ),
        "comparator_definition": (
            "governed allowed canonical opportunities in the frozen empty pre-decision stratum"
        ),
        "candidate_opportunities": len(candidate),
        "comparator_opportunities": len(comparator),
        "candidate_mean_r": candidate_mean,
        "comparator_mean_r": comparator_mean,
        "estimate": estimate,
        "estimate_order": "guard-exclusive blocked counterfactual mean R minus allowed mean R",
        "standard_error": standard_error,
        "confidence_interval_95": [
            estimate - Z_95 * standard_error,
            estimate + Z_95 * standard_error,
        ],
        "raw_two_sided_p_value": _normal_two_sided_p(estimate, standard_error),
        "cluster_identity": CLUSTERED_INFERENCE["cluster_identity"],
        "weighting": "one total unit of weight per canonical opportunity within each arm",
        "covariance": CLUSTERED_INFERENCE["finite_sample_correction"],
    }


def _validate_evidence(evidence: RiskPolicyEvidence) -> None:
    if HD10_ADJUDICATED_CONTRACT.get("version") != HD10_ADJUDICATION_VERSION:
        raise R2AnalysisError("invalid HD10 authority")
    if GUARDS != GUARD_IDS or len(GUARDS) != 4:
        raise R2AnalysisError("HD10 guard taxonomy is not the frozen four-guard family")
    if evidence.schema_version != EVIDENCE_SCHEMA_VERSION:
        raise R2AnalysisError("invalid RW9.1 evidence schema")
    material = dict(evidence.provenance)
    supplied = material.pop("digest", None)
    if supplied != evidence_digest((material,)):
        raise R2AnalysisError("invalid RW9.1 evidence provenance digest")
    if evidence.provenance.get("guard_taxonomy_digest") != GUARD_TAXONOMY_DIGEST:
        raise R2AnalysisError("RW9.1 guard taxonomy authority drift")
    if evidence.provenance.get("eligible_population_digest") != evidence_digest(
        item.digest_material() for item in evidence.records
    ):
        raise R2AnalysisError("RW9.1 analytical population digest mismatch")
    if evidence.provenance.get("exclusion_digest") != evidence_digest(
        item.record() for item in evidence.exclusions
    ):
        raise R2AnalysisError("RW9.1 exclusion digest mismatch")
    if any(
        item.record_digest != evidence_digest((item.digest_material(),))
        for item in evidence.records
    ):
        raise R2AnalysisError("RW9.1 record integrity failure")
    identities = [item.canonical_opportunity_id for item in evidence.records]
    if len(identities) != len(set(identities)):
        raise R2AnalysisError("canonical opportunity pseudoreplication")


def _readiness(evidence: RiskPolicyEvidence) -> dict[str, Any]:
    contract = SAMPLE_AND_READINESS_CONTRACT["R2"]
    summary = evidence.summary
    linked = evaluate_evidence_readiness(
        total_count=summary.total_candidate_observations,
        valid_count=summary.eligible_observations,
        distinct_valid_count=summary.distinct_canonical_opportunities,
        requirement=EvidenceRequirement(
            0,
            int(CLUSTERED_INFERENCE["minimum_clusters"]),
            float(contract["lineage_coverage"]),
        ),
    )
    outcome = evaluate_evidence_readiness(
        total_count=summary.total_candidate_observations,
        valid_count=summary.eligible_observations,
        distinct_valid_count=summary.distinct_canonical_opportunities,
        requirement=EvidenceRequirement(0, 0, float(contract["outcome_coverage"])),
    )
    blockers = [*linked.blockers, *outcome.blockers]
    minimum = int(contract["minimum_guard_exclusive_opportunities_per_guard"])
    allowed_count = len(evidence.allowed_records())
    guard_counts = {
        guard: len(evidence.guard_exclusive_records(guard)) for guard in GUARDS
    }
    guard_readiness: dict[str, dict[str, Any]] = {}
    for guard in GUARDS:
        contrast_clusters = guard_counts[guard] + allowed_count
        guard_blockers: list[str] = []
        if guard_counts[guard] < minimum:
            guard_blockers.append("GUARD_EXCLUSIVE_ARM_BELOW_REQUIRED")
        if allowed_count == 0:
            guard_blockers.append("ALLOWED_COMPARATOR_EMPTY")
        if contrast_clusters < int(CLUSTERED_INFERENCE["minimum_clusters"]):
            guard_blockers.append("CONTRAST_CLUSTERS_BELOW_REQUIRED")
        if guard_blockers:
            blockers.extend(f"{guard}:{item}" for item in guard_blockers)
        guard_readiness[guard] = {
            "state": "READY" if not guard_blockers else "WAITING_DATA",
            "guard_exclusive_opportunities": guard_counts[guard],
            "required_guard_exclusive_opportunities": minimum,
            "allowed_comparator_opportunities": allowed_count,
            "contrast_clusters": contrast_clusters,
            "required_contrast_clusters": int(CLUSTERED_INFERENCE["minimum_clusters"]),
            "blockers": guard_blockers,
        }

    structural = sorted(
        reason for reason in summary.exclusions_by_reason if reason in _STRUCTURAL_EXCLUSIONS
    )
    if evidence.baseline_authority_state != BASELINE_AUTHORITY_CURRENT:
        structural.append(evidence.baseline_authority_state)
    if summary.unattributable_blocked_count:
        structural.append("UNKNOWN_OR_MISSING_GUARD_AUTHORITY")
    components = evidence.evidence_provenance.get("components", [])
    if any(
        isinstance(component, Mapping)
        and any(int(component.get("epoch_counts", {}).get(key, 0)) > 0
                for key in ("TRANSITIONAL", "LEGACY", "INCOMPATIBLE"))
        for component in components
    ):
        structural.append("NON_CURRENT_OR_INCOMPATIBLE_EVIDENCE")
    state = "BLOCKED" if structural else ("WAITING_DATA" if blockers else "READY")
    return {
        "state": state,
        "blockers": sorted(set(structural if structural else blockers)),
        "lineage_coverage": linked.current_coverage,
        "required_lineage_coverage": linked.required_coverage,
        "outcome_coverage": outcome.current_coverage,
        "required_outcome_coverage": outcome.required_coverage,
        "distinct_canonical_opportunities": summary.distinct_canonical_opportunities,
        "required_distinct_canonical_opportunities": linked.required_distinct_count,
        "guards": guard_readiness,
    }


def _residual_stratum(evidence: RiskPolicyEvidence) -> dict[str, Any]:
    records = evidence.multi_guard_records()
    return {
        "name": "MULTI_GUARD_RESIDUAL",
        "opportunities": len(records),
        "canonical_opportunity_ids": [item.canonical_opportunity_id for item in records],
        "population_digest": evidence_digest(item.digest_material() for item in records),
        "attribution_rule": (
            "reported once; excluded from every per-guard contrast; no interaction claim"
        ),
    }


def _exclusions(evidence: RiskPolicyEvidence) -> dict[str, Any]:
    return {
        "by_reason": dict(evidence.summary.exclusions_by_reason),
        "unattributable_blocked_opportunities": evidence.summary.unattributable_blocked_count,
        "digest": evidence.provenance.get("exclusion_digest"),
    }


def analyse(evidence: RiskPolicyEvidence) -> dict[str, Any]:
    """Build the dedicated canonical R2 report from governed RW9.1 evidence."""
    try:
        _validate_evidence(evidence)
    except R2AnalysisError as exc:
        readiness = {"state": "BLOCKED", "blockers": [str(exc)], "guards": {}}
    else:
        readiness = _readiness(evidence)

    comparator = tuple(item.outcome_r_multiple for item in evidence.allowed_records())
    candidates = {
        guard: tuple(
            item.outcome_r_multiple for item in evidence.guard_exclusive_records(guard)
        )
        for guard in GUARDS
    }
    tests: list[dict[str, Any]] = []
    if len(comparator) >= 2 and all(len(candidates[guard]) >= 2 for guard in GUARDS):
        tests = [
            estimate_guard_contrast(guard, candidates[guard], comparator)
            for guard in GUARDS
        ]
        adjusted = holm_adjust([
            (test["test_identity"], test["raw_two_sided_p_value"]) for test in tests
        ])
        alpha = float(CLUSTERED_INFERENCE["alpha"])
        for test in tests:
            test["holm_adjusted_p_value"] = adjusted[test["test_identity"]]
            supported = test["estimate"] < 0 and adjusted[test["test_identity"]] <= alpha
            test["interpretation"] = "SUPPORTED_BENEFIT" if supported else "NOT_SUPPORTED"

    status = (
        "BLOCKED" if readiness["state"] == "BLOCKED" else
        "WAITING_DATA" if readiness["state"] != "READY" else
        "COMPLETE"
    )
    supported = [
        test["guard_id"] for test in tests if test["interpretation"] == "SUPPORTED_BENEFIT"
    ]
    classification = (
        "SUPPORTED_GUARD_BENEFIT" if supported else
        "SUFFICIENT_NULL_NO_SUPPORTED_GUARD_BENEFIT" if status == "COMPLETE" else
        "NO_AUTHORITATIVE_FINDING"
    )
    finding = (
        f"R2 COMPLETE: supported benefit for {', '.join(supported)}."
        if status == "COMPLETE" and supported else
        "R2 COMPLETE valid null: sufficient governed evidence, with no supported guard benefit."
        if status == "COMPLETE" else
        f"R2 {status}: {', '.join(readiness['blockers']) or 'evidence gates unmet'}."
    )
    analytical_material = {
        "question_id": "R2",
        "hd10_adjudication_version": HD10_ADJUDICATION_VERSION,
        "r2_contract": deepcopy(R2_CONTRACT),
        "baseline_policy_id": evidence.summary.baseline_policy_id,
        "baseline_policy_version": evidence.summary.baseline_policy_version,
        "baseline_identity_hash": evidence.summary.baseline_identity_hash,
        "guard_taxonomy": list(GUARDS),
        "guard_taxonomy_digest": GUARD_TAXONOMY_DIGEST,
        "rw91_evidence_provenance_digest": evidence.provenance.get("digest"),
        "analytical_population_digest": evidence.provenance.get("eligible_population_digest"),
        "exclusion_digest": evidence.provenance.get("exclusion_digest"),
        "runner_analytical_population": evidence.summary.total_candidate_observations,
        "pre_decision_stratum_fields": list(PRE_DECISION_STRATUM_FIELDS),
        "comparator_rule": (
            "all governed allowed opportunities in the single frozen empty pre-decision stratum; "
            "RW9.1 exposes no HD10-adjudicated matching covariates and outcome-driven matching is forbidden"
        ),
        "inference_configuration": {
            "schema_version": INFERENCE_SCHEMA_VERSION,
            **dict(CLUSTERED_INFERENCE),
            "critical_value": Z_95,
        },
        "holm_family_order": list(HOLM_FAMILY),
        "tests": tests,
        "readiness": readiness,
        "multi_guard_residual": _residual_stratum(evidence),
        "exclusions": _exclusions(evidence),
    }
    analytical_digest = evidence_digest((analytical_material,))
    fingerprint = build_fingerprint_from_provenance(
        evidence.evidence_provenance,
        validation_score="HD10_R2_GOVERNED",
    )
    fingerprint.update({
        "architecture_version": "new_pipeline_v1.2",
        "analytical_observations": evidence.summary.eligible_observations,
        "analytical_digest": analytical_digest,
    })
    report = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "question_id": "R2",
        "status": status,
        "epoch": CURRENT if evidence.evidence_provenance.get("state") == CURRENT else "UNVERIFIED",
        "overall": {
            "experiment": "HD10 canonical guard-exclusive attribution",
            "baseline": evidence.summary.baseline_policy_id,
            "variants": list(GUARDS),
            "finding": finding,
            "finding_classification": classification,
            "supported_guards": supported,
            "tests": tests,
        },
        "confidence": "HIGH" if status == "COMPLETE" else "INSUFFICIENT_DATA",
        "dataset": {
            "source": "RW9.1 governed risk-policy evidence",
            "analysed_row_count": evidence.summary.total_candidate_observations,
            "sample_size": evidence.summary.eligible_observations,
            "independent_observations": evidence.summary.distinct_canonical_opportunities,
            "allowed_comparator_opportunities": len(comparator),
            "guard_exclusive_opportunities": {
                guard: len(candidates[guard]) for guard in GUARDS
            },
            "multi_guard_residual_opportunities": evidence.summary.multi_guard_count,
        },
        "fingerprint": fingerprint,
        "recommendation": (
            classification if status == "COMPLETE" else
            "WAIT" if status == "WAITING_DATA" else "BLOCKED"
        ),
        "assumptions": [R2_CONTRACT["claim_boundary"]],
        "warnings": [
            "Research-only observational result; no production risk, sizing, halt, or guard directive."
        ],
        "provenance": {**analytical_material, "analytical_digest": analytical_digest},
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    report_material = deepcopy(report)
    report_material.pop("generated", None)
    report["provenance"]["report_digest"] = evidence_digest((report_material,))
    return report


def load_governed_r2_evidence() -> RiskPolicyEvidence:
    """Load CURRENT sources and delegate all linkage to the RW9.1 foundation."""
    from research_engine.data_access.s3_source import get_default_source
    from research_engine.data_access.shadow_runtime_ingestion import ingest_completed_shadow_trades

    source = get_default_source()
    decisions = source.read_dataset("decision_trace")
    outcomes = [
        *ingest_completed_shadow_trades(),
        *source.read_dataset("research_shadow_trades"),
    ]
    return build_risk_policy_evidence(
        decisions,
        outcomes,
        baseline_authority=deepcopy(BASELINE_RISK_POLICY_V1),
    )


def run_r2(*, decision_records=None, outcome_records=None,
           governed_records=None, persist: bool = True) -> dict[str, Any]:
    if governed_records is None:
        if decision_records is not None or outcome_records is not None:
            raise R2AnalysisError("R2_GOVERNED_POPULATION_REQUIRED")
        evidence = load_governed_r2_evidence()
    else:
        from research_engine.control_plane.stage4_impl_population2 import (
            enforce_exact_population, filter_sources_to_governed_population,
        )
        governed = enforce_exact_population("R2", governed_records)
        decisions, outcomes = filter_sources_to_governed_population(
            "R2", governed, decision_records or (), outcome_records or ())
        evidence = build_risk_policy_evidence(
            decisions, outcomes, baseline_authority=deepcopy(BASELINE_RISK_POLICY_V1))
        if evidence.summary.total_candidate_observations != len(governed):
            raise R2AnalysisError(
                "R2_RUNNER_INPUT_POPULATION_MISMATCH:"
                f"{evidence.summary.total_candidate_observations}!={len(governed)}")
    report = analyse(evidence)
    if persist:
        _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        (_REPORTS_DIR / REPORT_FILENAME).write_text(
            json.dumps(report, indent=2, sort_keys=True, allow_nan=False),
            encoding="utf-8",
        )
    return report


def validate_r2_report(report: Mapping[str, Any]) -> tuple[bool, str]:
    """Validate persisted R2 authority without trusting its declared status."""
    if report.get("question_id") != "R2":
        return False, "R2 report identity mismatch"
    if report.get("report_schema_version") != REPORT_SCHEMA_VERSION:
        return False, "R2 report schema mismatch"
    provenance = report.get("provenance")
    if not isinstance(provenance, Mapping):
        return False, "R2 report provenance missing"
    if provenance.get("hd10_adjudication_version") != HD10_ADJUDICATION_VERSION:
        return False, "R2 HD10 authority mismatch"
    if json.loads(json.dumps(provenance.get("r2_contract"))) != json.loads(json.dumps(R2_CONTRACT)):
        return False, "R2 frozen estimand contract mismatch"
    if (
        provenance.get("baseline_policy_id") != BASELINE_RISK_POLICY_V1["policy_id"]
        or provenance.get("baseline_policy_version") != BASELINE_RISK_POLICY_V1["policy_version"]
        or provenance.get("baseline_identity_hash") != BASELINE_RISK_POLICY_V1["identity_hash"]
    ):
        return False, "R2 baseline-policy identity mismatch"
    if tuple(provenance.get("guard_taxonomy", ())) != GUARDS:
        return False, "R2 guard taxonomy mismatch"
    if provenance.get("guard_taxonomy_digest") != GUARD_TAXONOMY_DIGEST:
        return False, "R2 guard taxonomy digest mismatch"
    if tuple(provenance.get("holm_family_order", ())) != HOLM_FAMILY:
        return False, "R2 Holm family is incomplete or reordered"
    readiness = provenance.get("readiness")
    if not isinstance(readiness, Mapping):
        return False, "R2 readiness missing"
    expected_status = "COMPLETE" if readiness.get("state") == "READY" else str(readiness.get("state"))
    if report.get("status") != expected_status:
        return False, "R2 status contradicts readiness"
    overall = report.get("overall")
    tests = overall.get("tests", []) if isinstance(overall, Mapping) else []
    if report.get("status") == "COMPLETE":
        if tuple(test.get("test_identity") for test in tests if isinstance(test, Mapping)) != HOLM_FAMILY:
            return False, "R2 endpoints are incomplete"
        required = {
            "estimate", "standard_error", "confidence_interval_95",
            "raw_two_sided_p_value", "holm_adjusted_p_value", "interpretation",
        }
        if any(not required.issubset(test) for test in tests):
            return False, "R2 inference fields are incomplete"
        try:
            adjusted = holm_adjust([
                (test["test_identity"], test["raw_two_sided_p_value"]) for test in tests
            ])
        except (R2AnalysisError, KeyError, TypeError):
            return False, "R2 Holm inputs are invalid"
        if any(
            test.get("holm_adjusted_p_value") != adjusted[test["test_identity"]]
            for test in tests
        ):
            return False, "R2 Holm adjustment mismatch"
    residual = provenance.get("multi_guard_residual")
    if not isinstance(residual, Mapping) or residual.get("name") != "MULTI_GUARD_RESIDUAL":
        return False, "R2 multi-guard residual missing"
    analytical = dict(provenance)
    stored_report_digest = analytical.pop("report_digest", None)
    stored_analytical_digest = analytical.pop("analytical_digest", None)
    if stored_analytical_digest != evidence_digest((analytical,)):
        return False, "R2 analytical digest mismatch"
    report_copy = deepcopy(dict(report))
    report_copy.pop("generated", None)
    report_copy["provenance"].pop("report_digest", None)
    if stored_report_digest != evidence_digest((report_copy,)):
        return False, "R2 report digest mismatch"
    fingerprint = report.get("fingerprint")
    if not isinstance(fingerprint, Mapping):
        return False, "R2 fingerprint missing"
    if fingerprint.get("analytical_digest") != stored_analytical_digest:
        return False, "R2 fingerprint/provenance mismatch"
    valid, state, _ = validate_evidence_provenance(fingerprint.get("evidence_provenance"))
    if not valid or (report.get("status") == "COMPLETE" and state != CURRENT):
        return False, "R2 evidence provenance is not valid CURRENT authority"
    return True, "valid governed HD10 R2 report"
