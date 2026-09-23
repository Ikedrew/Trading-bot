"""Canonical HD10 R1 global risk-layer effectiveness experiment.

This runner consumes the governed RW9.1 population.  It does not reconstruct
lineage, count guard events as outcomes, attribute individual guards, or change
production risk behaviour.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
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
    ALLOWED,
    BASELINE_AUTHORITY_CURRENT,
    BLOCKED,
    SCHEMA_VERSION as EVIDENCE_SCHEMA_VERSION,
    RiskPolicyEvidence,
    build_risk_policy_evidence,
)
from research_engine.experiments.experiment_base import build_fingerprint_from_provenance
from research_engine.registry.risk_policy_adjudication import (
    BASELINE_RISK_POLICY_V1,
    CLUSTERED_INFERENCE,
    HD10_ADJUDICATED_CONTRACT,
    HD10_ADJUDICATION_VERSION,
    R1_CONTRACT,
    REPORT_OWNERSHIP,
    RUIN_MODEL_V1,
    SAMPLE_AND_READINESS_CONTRACT,
    Z_95,
)

REPORT_SCHEMA_VERSION = "r1_risk_layer_effectiveness_v1"
INFERENCE_SCHEMA_VERSION = "hd10_r1_clustered_cr0_normal_v1"
REPORT_FILENAME = REPORT_OWNERSHIP["R1"]
EXPECTANCY_ENDPOINT = "allowed_mean_r_minus_blocked_counterfactual_mean_r"
SURVIVAL_ENDPOINT = "allowed_ruin_probability_minus_blocked_ruin_probability"
HOLM_FAMILY = (EXPECTANCY_ENDPOINT, SURVIVAL_ENDPOINT)

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


class R1AnalysisError(ValueError):
    """A structural R1 authority or inference failure."""


def _finite(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise R1AnalysisError("required analytical value is not numeric")
    result = float(value)
    if not math.isfinite(result):
        raise R1AnalysisError("required analytical value is not finite")
    return result


def _normal_two_sided_p(estimate: float, standard_error: float) -> float:
    estimate, standard_error = _finite(estimate), _finite(standard_error)
    if standard_error < 0:
        raise R1AnalysisError("negative standard error")
    if standard_error == 0:
        return 1.0 if estimate == 0 else 0.0
    return math.erfc(abs(estimate / standard_error) / math.sqrt(2.0))


def holm_adjust(
    tests: Sequence[tuple[str, float]],
    required_order: Sequence[str] = HOLM_FAMILY,
) -> dict[str, float]:
    """Deterministic Holm adjustment with frozen endpoint-order tie breaking."""
    required = tuple(required_order)
    if tuple(test_id for test_id, _ in tests) != required or len(set(required)) != len(required):
        raise R1AnalysisError("R1 Holm family is incomplete, duplicated, or reordered")
    order = {test_id: index for index, test_id in enumerate(required)}
    checked = [(test_id, _finite(p_value)) for test_id, p_value in tests]
    if any(not 0.0 <= p_value <= 1.0 for _, p_value in checked):
        raise R1AnalysisError("R1 Holm family contains an invalid p-value")
    ranked = sorted(checked, key=lambda item: (item[1], order[item[0]]))
    adjusted: dict[str, float] = {}
    running = 0.0
    for rank, (test_id, p_value) in enumerate(ranked):
        running = max(running, min(1.0, (len(ranked) - rank) * p_value))
        adjusted[test_id] = running
    return {test_id: adjusted[test_id] for test_id in required}


def _mean(values: Sequence[float]) -> float:
    if not values:
        raise R1AnalysisError("an R1 arm has no governed outcomes")
    return math.fsum(values) / len(values)


def _cr0_mean_variance(values: Sequence[float], estimate: float) -> float:
    n = len(values)
    return math.fsum((value - estimate) ** 2 for value in values) / (n * n)


def estimate_expectancy(
    allowed_values: Sequence[float], blocked_values: Sequence[float],
) -> dict[str, Any]:
    """HD10 primary estimand at one-weight-per-opportunity grain."""
    allowed = tuple(_finite(value) for value in allowed_values)
    blocked = tuple(_finite(value) for value in blocked_values)
    allowed_mean, blocked_mean = _mean(allowed), _mean(blocked)
    estimate = allowed_mean - blocked_mean
    variance = (
        _cr0_mean_variance(allowed, allowed_mean)
        + _cr0_mean_variance(blocked, blocked_mean)
    )
    standard_error = math.sqrt(variance)
    return {
        "test_identity": EXPECTANCY_ENDPOINT,
        "estimand": R1_CONTRACT["primary_estimand"],
        "allowed_opportunities": len(allowed),
        "blocked_counterfactual_opportunities": len(blocked),
        "allowed_mean_r": allowed_mean,
        "blocked_counterfactual_mean_r": blocked_mean,
        "estimate": estimate,
        "standard_error": standard_error,
        "confidence_interval_95": [
            estimate - Z_95 * standard_error,
            estimate + Z_95 * standard_error,
        ],
        "raw_two_sided_p_value": _normal_two_sided_p(estimate, standard_error),
        "cluster_identity": CLUSTERED_INFERENCE["cluster_identity"],
        "weighting": "one total unit of weight per canonical opportunity",
        "covariance": CLUSTERED_INFERENCE["finite_sample_correction"],
    }


def analytical_ruin_probability(values: Sequence[float]) -> float:
    """Frozen RUIN_MODEL_V1 analytical odds-ratio approximation."""
    outcomes = tuple(_finite(value) for value in values)
    if not outcomes:
        raise R1AnalysisError("ruin model requires at least one governed outcome")
    mean_r = _mean(outcomes)
    if mean_r <= 0:
        return 1.0
    wins = [value for value in outcomes if value > 0]
    losses = [abs(value) for value in outcomes if value < 0]
    if not wins:
        return 1.0
    if not losses:
        return 0.0
    p = len(wins) / len(outcomes)
    q = 1.0 - p
    average_win = _mean(wins)
    average_loss = _mean(losses)
    ratio = (q * average_loss) / (p * average_win)
    if ratio >= 1.0:
        return 1.0
    exponent = 1.0 / float(RUIN_MODEL_V1["ruin_threshold_pct"])
    return min(1.0, max(0.0, ratio ** exponent))


def _wilson_interval(successes: int, trials: int) -> list[float]:
    if trials <= 0:
        raise R1AnalysisError("Wilson interval requires simulations")
    p_hat = successes / trials
    z2 = Z_95 * Z_95
    denominator = 1.0 + z2 / trials
    centre = (p_hat + z2 / (2.0 * trials)) / denominator
    half_width = (
        Z_95 * math.sqrt((p_hat * (1.0 - p_hat) + z2 / (4.0 * trials)) / trials)
        / denominator
    )
    return [max(0.0, centre - half_width), min(1.0, centre + half_width)]


def monte_carlo_ruin_probability(values: Sequence[float]) -> dict[str, Any]:
    """Frozen, process-local RUIN_MODEL_V1 simulation and Wilson interval."""
    outcomes = tuple(_finite(value) for value in values)
    if not outcomes:
        raise R1AnalysisError("ruin simulation requires governed outcomes")
    simulation = RUIN_MODEL_V1["monte_carlo"]
    n_simulations = int(simulation["n_simulations"])
    horizon = int(simulation["trade_horizon"])
    seed_base = int(RUIN_MODEL_V1["seed_contract"]["seed_base"])
    risk = float(RUIN_MODEL_V1["risk_per_trade_pct"])
    threshold = float(RUIN_MODEL_V1["ruin_threshold_pct"])

    constant = outcomes[0] if len(set(outcomes)) == 1 else None
    if constant is not None and constant >= 0:
        return {
            "model_version": RUIN_MODEL_V1["model_version"],
            "simulations": n_simulations,
            "trade_horizon": horizon,
            "risk_per_trade_pct": risk,
            "ruin_threshold_pct": threshold,
            "seed_base": seed_base,
            "seed_order": "seed_base + s for s=1..n_simulations",
            "ruin_probability": 0.0,
            "survival_probability": 1.0,
            "wilson_interval_95": _wilson_interval(0, n_simulations),
        }

    ruin_count = 0
    for simulation_index in range(1, n_simulations + 1):
        rng = random.Random(seed_base + simulation_index)
        equity = peak = 1.0
        ruined_at: int | None = None
        for trade_number in range(1, horizon + 1):
            outcome = constant if constant is not None else outcomes[rng.randrange(len(outcomes))]
            equity += outcome * risk
            peak = max(peak, equity)
            if peak > 0 and (peak - equity) / peak >= threshold:
                ruined_at = trade_number
                break
        if ruined_at is not None:
            ruin_count += 1
    probability = ruin_count / n_simulations
    return {
        "model_version": RUIN_MODEL_V1["model_version"],
        "simulations": n_simulations,
        "trade_horizon": horizon,
        "risk_per_trade_pct": risk,
        "ruin_threshold_pct": threshold,
        "seed_base": seed_base,
        "seed_order": "seed_base + s for s=1..n_simulations",
        "ruin_probability": probability,
        "survival_probability": 1.0 - probability,
        "wilson_interval_95": _wilson_interval(ruin_count, n_simulations),
    }


def _ruin_cr0_variance(values: Sequence[float], estimate: float) -> float:
    """Delete-one canonical-cluster influence CR0 for the nonlinear estimator."""
    n = len(values)
    if n < 2:
        raise R1AnalysisError("ruin contrast needs at least two opportunities per arm")
    scores = [
        (n - 1) * (estimate - analytical_ruin_probability(values[:index] + values[index + 1:]))
        for index in range(n)
    ]
    return math.fsum(score * score for score in scores) / (n * n)


def estimate_survival(
    allowed_values: Sequence[float], blocked_values: Sequence[float],
) -> tuple[dict[str, Any], bool]:
    """HD10 secondary ruin-probability contrast plus frozen-model verification."""
    allowed = tuple(_finite(value) for value in allowed_values)
    blocked = tuple(_finite(value) for value in blocked_values)
    allowed_analytical = analytical_ruin_probability(allowed)
    blocked_analytical = analytical_ruin_probability(blocked)
    allowed_mc = monte_carlo_ruin_probability(allowed)
    blocked_mc = monte_carlo_ruin_probability(blocked)
    tolerance = float(RUIN_MODEL_V1["agreement_tolerance"])
    allowed_agrees = abs(allowed_analytical - allowed_mc["ruin_probability"]) <= tolerance
    blocked_agrees = abs(blocked_analytical - blocked_mc["ruin_probability"]) <= tolerance
    estimate = allowed_analytical - blocked_analytical
    standard_error = math.sqrt(
        _ruin_cr0_variance(allowed, allowed_analytical)
        + _ruin_cr0_variance(blocked, blocked_analytical)
    )
    return ({
        "test_identity": SURVIVAL_ENDPOINT,
        "estimand": R1_CONTRACT["secondary_estimand"],
        "estimate": estimate,
        "estimate_order": "allowed ruin probability minus blocked-counterfactual ruin probability",
        "standard_error": standard_error,
        "confidence_interval_95": [
            estimate - Z_95 * standard_error,
            estimate + Z_95 * standard_error,
        ],
        "raw_two_sided_p_value": _normal_two_sided_p(estimate, standard_error),
        "allowed_arm": {
            "analytical_ruin_probability": allowed_analytical,
            "monte_carlo": allowed_mc,
            "agreement_within_tolerance": allowed_agrees,
        },
        "blocked_counterfactual_arm": {
            "analytical_ruin_probability": blocked_analytical,
            "monte_carlo": blocked_mc,
            "agreement_within_tolerance": blocked_agrees,
        },
        "agreement_tolerance": tolerance,
        "cluster_identity": CLUSTERED_INFERENCE["cluster_identity"],
        "weighting": "one total unit of weight per canonical opportunity",
        "covariance": CLUSTERED_INFERENCE["finite_sample_correction"],
    }, allowed_agrees and blocked_agrees)


def _validate_evidence(evidence: RiskPolicyEvidence) -> None:
    if HD10_ADJUDICATED_CONTRACT.get("version") != HD10_ADJUDICATION_VERSION:
        raise R1AnalysisError("invalid HD10 authority")
    if evidence.schema_version != EVIDENCE_SCHEMA_VERSION:
        raise R1AnalysisError("invalid RW9.1 evidence schema")
    material = dict(evidence.provenance)
    supplied = material.pop("digest", None)
    if supplied != evidence_digest((material,)):
        raise R1AnalysisError("invalid RW9.1 evidence provenance digest")
    if evidence.provenance.get("eligible_population_digest") != evidence_digest(
        item.digest_material() for item in evidence.records
    ):
        raise R1AnalysisError("RW9.1 analytical population digest mismatch")
    if evidence.provenance.get("exclusion_digest") != evidence_digest(
        item.record() for item in evidence.exclusions
    ):
        raise R1AnalysisError("RW9.1 exclusion digest mismatch")
    if any(
        item.record_digest != evidence_digest((item.digest_material(),))
        for item in evidence.records
    ):
        raise R1AnalysisError("RW9.1 record integrity failure")
    identities = [item.canonical_opportunity_id for item in evidence.records]
    if len(identities) != len(set(identities)):
        raise R1AnalysisError("canonical opportunity pseudoreplication")


def _readiness(evidence: RiskPolicyEvidence) -> dict[str, Any]:
    contract = SAMPLE_AND_READINESS_CONTRACT["R1"]
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
    if summary.allowed_count < int(contract["minimum_allowed_arm_opportunities"]):
        blockers.append("ALLOWED_ARM_BELOW_REQUIRED")
    if summary.blocked_with_valid_counterfactual_count < int(
        contract["minimum_blocked_counterfactual_opportunities"]
    ):
        blockers.append("BLOCKED_COUNTERFACTUAL_ARM_BELOW_REQUIRED")

    structural = sorted(
        reason for reason in summary.exclusions_by_reason if reason in _STRUCTURAL_EXCLUSIONS
    )
    if evidence.baseline_authority_state != BASELINE_AUTHORITY_CURRENT:
        structural.append(evidence.baseline_authority_state)
    components = evidence.evidence_provenance.get("components", [])
    incompatible = any(
        isinstance(component, Mapping)
        and any(int(component.get("epoch_counts", {}).get(key, 0)) > 0
                for key in ("TRANSITIONAL", "LEGACY", "INCOMPATIBLE"))
        for component in components
    )
    if incompatible:
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
        "allowed_opportunities": summary.allowed_count,
        "required_allowed_opportunities": int(contract["minimum_allowed_arm_opportunities"]),
        "blocked_counterfactual_opportunities": summary.blocked_with_valid_counterfactual_count,
        "required_blocked_counterfactual_opportunities": int(
            contract["minimum_blocked_counterfactual_opportunities"]
        ),
    }


def _inference_configuration() -> dict[str, Any]:
    return {
        "schema_version": INFERENCE_SCHEMA_VERSION,
        **dict(CLUSTERED_INFERENCE),
        "critical_value": Z_95,
        "nonlinear_survival_covariance": (
            "delete-one canonical-opportunity influence scores with CR0 outer product"
        ),
    }


def _exclusion_diagnostics(evidence: RiskPolicyEvidence) -> dict[str, Any]:
    allowed_reasons = {"MISSING_GOVERNED_OUTCOME"}
    blocked_reasons = {"MISSING_BLOCKED_COUNTERFACTUAL_OUTCOME"}
    allowed = sum(
        1 for item in evidence.exclusions if item.reason in allowed_reasons
    )
    blocked = sum(
        1 for item in evidence.exclusions if item.reason in blocked_reasons
    )
    return {
        "allowed_arm": allowed,
        "blocked_counterfactual_arm": blocked,
        "unassigned_authority_or_scope": len(evidence.exclusions) - allowed - blocked,
        "by_reason": dict(evidence.summary.exclusions_by_reason),
        "digest": evidence.provenance.get("exclusion_digest"),
    }


def analyse(evidence: RiskPolicyEvidence) -> dict[str, Any]:
    """Build the dedicated canonical R1 report from governed RW9.1 evidence."""
    try:
        _validate_evidence(evidence)
    except R1AnalysisError as exc:
        readiness = {"state": "BLOCKED", "blockers": [str(exc)]}
    else:
        readiness = _readiness(evidence)

    allowed = tuple(item.outcome_r_multiple for item in evidence.allowed_records())
    blocked = tuple(
        item.outcome_r_multiple for item in evidence.blocked_with_counterfactual_records()
    )
    tests: list[dict[str, Any]] = []
    model_agreement = True
    if len(allowed) >= 2 and len(blocked) >= 2:
        expectancy = estimate_expectancy(allowed, blocked)
        survival, model_agreement = estimate_survival(allowed, blocked)
        tests = [expectancy, survival]
        adjusted = holm_adjust([
            (test["test_identity"], test["raw_two_sided_p_value"]) for test in tests
        ])
        alpha = float(CLUSTERED_INFERENCE["alpha"])
        for test in tests:
            test["holm_adjusted_p_value"] = adjusted[test["test_identity"]]
            if test["test_identity"] == EXPECTANCY_ENDPOINT:
                supported = test["estimate"] > 0 and adjusted[test["test_identity"]] <= alpha
            else:
                supported = test["estimate"] < 0 and adjusted[test["test_identity"]] <= alpha
            test["interpretation"] = "SUPPORTED_BENEFIT" if supported else "NOT_SUPPORTED"

    if readiness["state"] == "BLOCKED":
        status = "BLOCKED"
    elif readiness["state"] != "READY":
        status = "WAITING_DATA"
    elif not model_agreement:
        status = "BLOCKED"
        readiness = {
            **readiness,
            "state": "BLOCKED",
            "blockers": ["RUIN_MODEL_ANALYTICAL_MONTE_CARLO_DISAGREEMENT"],
        }
    else:
        status = "COMPLETE"

    supported = [test["test_identity"] for test in tests if test["interpretation"] == "SUPPORTED_BENEFIT"]
    classification = (
        "SUPPORTED_BENEFIT" if supported else
        "SUFFICIENT_NULL_NO_SUPPORTED_BENEFIT" if status == "COMPLETE" else
        "NO_AUTHORITATIVE_FINDING"
    )
    finding = (
        f"R1 COMPLETE: benefit supported on {len(supported)}/2 frozen endpoints."
        if status == "COMPLETE" and supported else
        "R1 COMPLETE valid null: sufficient governed evidence, with no supported benefit."
        if status == "COMPLETE" else
        f"R1 {status}: {', '.join(readiness['blockers']) or 'evidence gates unmet'}."
    )
    analytical_material = {
        "question_id": "R1",
        "hd10_adjudication_version": HD10_ADJUDICATION_VERSION,
        "r1_contract": deepcopy(R1_CONTRACT),
        "baseline_policy_id": evidence.summary.baseline_policy_id,
        "baseline_policy_version": evidence.summary.baseline_policy_version,
        "baseline_identity_hash": evidence.summary.baseline_identity_hash,
        "rw91_evidence_provenance_digest": evidence.provenance.get("digest"),
        "analytical_population_digest": evidence.provenance.get("eligible_population_digest"),
        "exclusion_digest": evidence.provenance.get("exclusion_digest"),
        "pre_decision_adjustment_set": [],
        "adjustment_set_rule": (
            "empty predeclared set; RW9.1 exposes no HD10-adjudicated adjustment covariates, "
            "and outcome-driven selection is forbidden"
        ),
        "inference_configuration": _inference_configuration(),
        "holm_family_order": list(HOLM_FAMILY),
        "tests": tests,
        "readiness": readiness,
        "exclusions": _exclusion_diagnostics(evidence),
    }
    analytical_digest = evidence_digest((analytical_material,))
    fingerprint = build_fingerprint_from_provenance(
        evidence.evidence_provenance,
        validation_score="HD10_R1_GOVERNED",
    )
    fingerprint.update({
        "architecture_version": "new_pipeline_v1.2",
        "analytical_observations": evidence.summary.eligible_observations,
        "analytical_digest": analytical_digest,
    })
    report = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "question_id": "R1",
        "status": status,
        "epoch": CURRENT if evidence.evidence_provenance.get("state") == CURRENT else "UNVERIFIED",
        "overall": {
            "experiment": "HD10 canonical global risk-layer effectiveness",
            "baseline": evidence.summary.baseline_policy_id,
            "variants": [ALLOWED, BLOCKED],
            "finding": finding,
            "finding_classification": classification,
            "supported_endpoints": supported,
            "tests": tests,
        },
        "confidence": "HIGH" if status == "COMPLETE" else "INSUFFICIENT_DATA",
        "dataset": {
            "source": "RW9.1 governed risk-policy evidence",
            "sample_size": evidence.summary.eligible_observations,
            "independent_observations": evidence.summary.distinct_canonical_opportunities,
            "allowed_opportunities": evidence.summary.allowed_count,
            "blocked_counterfactual_opportunities": (
                evidence.summary.blocked_with_valid_counterfactual_count
            ),
        },
        "fingerprint": fingerprint,
        "recommendation": (
            classification if status == "COMPLETE" else "WAIT" if status == "WAITING_DATA" else "BLOCKED"
        ),
        "assumptions": [R1_CONTRACT["claim_boundary"]],
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


def load_governed_r1_evidence() -> RiskPolicyEvidence:
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


def run_r1(*, persist: bool = True) -> dict[str, Any]:
    report = analyse(load_governed_r1_evidence())
    if persist:
        _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        (_REPORTS_DIR / REPORT_FILENAME).write_text(
            json.dumps(report, indent=2, sort_keys=True, allow_nan=False),
            encoding="utf-8",
        )
    return report


def validate_r1_report(report: Mapping[str, Any]) -> tuple[bool, str]:
    """Validate persisted R1 authority without trusting its declared status."""
    if report.get("question_id") != "R1":
        return False, "R1 report identity mismatch"
    if report.get("report_schema_version") != REPORT_SCHEMA_VERSION:
        return False, "R1 report schema mismatch"
    provenance = report.get("provenance")
    if not isinstance(provenance, Mapping):
        return False, "R1 report provenance missing"
    if provenance.get("hd10_adjudication_version") != HD10_ADJUDICATION_VERSION:
        return False, "R1 HD10 authority mismatch"
    if provenance.get("r1_contract") != R1_CONTRACT:
        return False, "R1 frozen estimand contract mismatch"
    if (
        provenance.get("baseline_policy_id") != BASELINE_RISK_POLICY_V1["policy_id"]
        or provenance.get("baseline_policy_version") != BASELINE_RISK_POLICY_V1["policy_version"]
        or provenance.get("baseline_identity_hash") != BASELINE_RISK_POLICY_V1["identity_hash"]
    ):
        return False, "R1 baseline-policy identity mismatch"
    if tuple(provenance.get("holm_family_order", ())) != HOLM_FAMILY:
        return False, "R1 Holm family is incomplete or reordered"
    readiness = provenance.get("readiness")
    if not isinstance(readiness, Mapping):
        return False, "R1 readiness missing"
    expected_status = (
        "COMPLETE" if readiness.get("state") == "READY" else str(readiness.get("state"))
    )
    if report.get("status") != expected_status:
        return False, "R1 status contradicts readiness"
    tests = report.get("overall", {}).get("tests", []) if isinstance(report.get("overall"), Mapping) else []
    if report.get("status") == "COMPLETE":
        if tuple(test.get("test_identity") for test in tests if isinstance(test, Mapping)) != HOLM_FAMILY:
            return False, "R1 endpoints are incomplete"
        required = {
            "estimate", "standard_error", "confidence_interval_95",
            "raw_two_sided_p_value", "holm_adjusted_p_value", "interpretation",
        }
        if any(not required.issubset(test) for test in tests):
            return False, "R1 inference fields are incomplete"
        try:
            adjusted = holm_adjust([
                (test["test_identity"], test["raw_two_sided_p_value"])
                for test in tests
            ])
        except (R1AnalysisError, KeyError, TypeError):
            return False, "R1 Holm inputs are invalid"
        if any(
            test.get("holm_adjusted_p_value") != adjusted[test["test_identity"]]
            for test in tests
        ):
            return False, "R1 Holm adjustment mismatch"
    analytical = dict(provenance)
    stored_report_digest = analytical.pop("report_digest", None)
    stored_analytical_digest = analytical.pop("analytical_digest", None)
    if stored_analytical_digest != evidence_digest((analytical,)):
        return False, "R1 analytical digest mismatch"
    report_copy = deepcopy(dict(report))
    report_copy.pop("generated", None)
    report_copy["provenance"].pop("report_digest", None)
    if stored_report_digest != evidence_digest((report_copy,)):
        return False, "R1 report digest mismatch"
    if report.get("fingerprint", {}).get("analytical_digest") != stored_analytical_digest:
        return False, "R1 fingerprint/provenance mismatch"
    valid, state, _ = validate_evidence_provenance(
        report.get("fingerprint", {}).get("evidence_provenance")
    )
    if not valid or (report.get("status") == "COMPLETE" and state != CURRENT):
        return False, "R1 evidence provenance is not valid CURRENT authority"
    return True, "valid governed HD10 R1 report"
