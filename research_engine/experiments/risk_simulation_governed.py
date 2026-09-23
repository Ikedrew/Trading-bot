"""Shared HD10 calculations for the distinct R3, R4 and R5 runners."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import math
import random
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from research_engine.control_plane.evidence_provenance import (
    CURRENT,
    evidence_digest,
    validate_evidence_provenance,
)
from research_engine.control_plane.risk_policy_evidence import (
    BASELINE_AUTHORITY_CURRENT,
    SCHEMA_VERSION as EVIDENCE_SCHEMA_VERSION,
    ChronologicalRiskWindows,
    RiskPolicyEvidence,
    build_risk_policy_evidence,
    build_chronological_risk_population,
    split_chronological_windows,
)
from research_engine.experiments.experiment_base import build_fingerprint_from_provenance
from research_engine.registry.risk_policy_adjudication import (
    BASELINE_RISK_POLICY_V1,
    HALT_THRESHOLD_GRID_V1,
    HD10_ADJUDICATION_VERSION,
    R3_CONTRACT,
    R4_CONTRACT,
    R5_CONTRACT,
    RUIN_MODEL_V1,
    SAMPLE_AND_READINESS_CONTRACT,
    SIZING_MODELS_V1,
    VALIDATION_CHRONOLOGY,
    Z_95,
)

R3_SCHEMA_VERSION = "r3_probability_of_ruin_v1"
R4_SCHEMA_VERSION = "r4_drawdown_threshold_v1"
R5_SCHEMA_VERSION = "r5_position_sizing_v1"
_ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR = _ROOT / "analysis" / "reports"


class RiskSimulationError(ValueError):
    """Structural HD10 evidence, dependency or model failure."""


def load_governed_risk_evidence() -> RiskPolicyEvidence:
    """Load CURRENT sources and delegate linkage to the RW9.1 foundation."""
    from research_engine.data_access.s3_source import get_default_source
    from research_engine.data_access.shadow_runtime_ingestion import ingest_completed_shadow_trades

    source = get_default_source()
    return build_risk_policy_evidence(
        source.read_dataset("decision_trace"),
        [
            *ingest_completed_shadow_trades(),
            *source.read_dataset("research_shadow_trades"),
        ],
        baseline_authority=deepcopy(BASELINE_RISK_POLICY_V1),
    )


def load_upstream_report(filename: str) -> dict[str, Any]:
    path = REPORTS_DIR / filename
    if not path.is_file():
        raise RiskSimulationError(f"UPSTREAM_REPORT_MISSING:{filename}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RiskSimulationError(f"UPSTREAM_REPORT_INVALID:{filename}")
    return value


def persist_report(report: Mapping[str, Any], filename: str) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / filename).write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8",
    )


def _finite_values(values: Sequence[float]) -> tuple[float, ...]:
    result: list[float] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RiskSimulationError("non-numeric governed R-multiple")
        number = float(value)
        if not math.isfinite(number):
            raise RiskSimulationError("non-finite governed R-multiple")
        result.append(number)
    return tuple(result)


def analytical_ruin_probability(values: Sequence[float]) -> float:
    outcomes = _finite_values(values)
    if not outcomes:
        raise RiskSimulationError("ruin model requires governed calibration outcomes")
    mean_r = math.fsum(outcomes) / len(outcomes)
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
    avg_win = math.fsum(wins) / len(wins)
    avg_loss = math.fsum(losses) / len(losses)
    ratio = (q * avg_loss) / (p * avg_win)
    if ratio >= 1.0:
        return 1.0
    return min(1.0, max(0.0, ratio ** (1.0 / float(RUIN_MODEL_V1["ruin_threshold_pct"]))))


def wilson_interval(successes: int, trials: int) -> list[float]:
    if trials <= 0 or successes < 0 or successes > trials:
        raise RiskSimulationError("invalid Wilson interval inputs")
    p_hat = successes / trials
    z2 = Z_95 * Z_95
    denominator = 1.0 + z2 / trials
    centre = (p_hat + z2 / (2.0 * trials)) / denominator
    half = (
        Z_95 * math.sqrt((p_hat * (1.0 - p_hat) + z2 / (4.0 * trials)) / trials)
        / denominator
    )
    return [max(0.0, centre - half), min(1.0, centre + half)]


def _simulate_ruin_paths(
    values: Sequence[float],
    *,
    n_simulations: int,
    trade_horizon: int,
    seed_base: int,
    risk_per_trade: float,
    ruin_threshold: float,
) -> dict[str, Any]:
    """Resample with replacement using one isolated generator per simulation."""
    outcomes = _finite_values(values)
    if not outcomes:
        raise RiskSimulationError("ruin simulation requires governed outcomes")
    ruin_count = 0
    survival_total = 0
    constant = outcomes[0] if len(set(outcomes)) == 1 else None
    for simulation_index in range(1, n_simulations + 1):
        rng = random.Random(seed_base + simulation_index)
        equity = peak = 1.0
        survived = trade_horizon
        if constant is not None and constant >= 0:
            survival_total += survived
            continue
        for trade_number in range(1, trade_horizon + 1):
            outcome = constant if constant is not None else outcomes[rng.randrange(len(outcomes))]
            equity += outcome * risk_per_trade
            peak = max(peak, equity)
            if peak > 0 and (peak - equity) / peak >= ruin_threshold:
                ruin_count += 1
                survived = trade_number
                break
        survival_total += survived
    probability = ruin_count / n_simulations
    return {
        "simulations": n_simulations,
        "trade_horizon": trade_horizon,
        "risk_per_trade_pct": risk_per_trade,
        "ruin_threshold_pct": ruin_threshold,
        "seed_base": seed_base,
        "seed_first": seed_base + 1,
        "seed_last": seed_base + n_simulations,
        "seed_policy": "random.Random(seed_base + simulation_index), simulation_index=1..n",
        "sampling": "i.i.d. resampling with replacement from calibration R-multiples",
        "ruin_count": ruin_count,
        "ruin_probability": probability,
        "survival_probability": 1.0 - probability,
        "expected_survival_trades": survival_total / n_simulations,
        "wilson_interval_95": wilson_interval(ruin_count, n_simulations),
    }


def monte_carlo_ruin(values: Sequence[float]) -> dict[str, Any]:
    simulation = RUIN_MODEL_V1["monte_carlo"]
    return _simulate_ruin_paths(
        values,
        n_simulations=int(simulation["n_simulations"]),
        trade_horizon=int(simulation["trade_horizon"]),
        seed_base=int(RUIN_MODEL_V1["seed_contract"]["seed_base"]),
        risk_per_trade=float(RUIN_MODEL_V1["risk_per_trade_pct"]),
        ruin_threshold=float(RUIN_MODEL_V1["ruin_threshold_pct"]),
    )


def validate_evidence(evidence: RiskPolicyEvidence) -> None:
    if evidence.schema_version != EVIDENCE_SCHEMA_VERSION:
        raise RiskSimulationError("invalid RW9.1 evidence schema")
    if evidence.hd10_adjudication_version != HD10_ADJUDICATION_VERSION:
        raise RiskSimulationError("HD10 authority mismatch")
    if evidence.baseline_authority_state != BASELINE_AUTHORITY_CURRENT:
        raise RiskSimulationError(evidence.baseline_authority_state)
    if (
        evidence.summary.baseline_policy_id != BASELINE_RISK_POLICY_V1["policy_id"]
        or evidence.summary.baseline_policy_version != BASELINE_RISK_POLICY_V1["policy_version"]
        or evidence.summary.baseline_identity_hash != BASELINE_RISK_POLICY_V1["identity_hash"]
    ):
        raise RiskSimulationError("BASELINE_RISK_POLICY_IDENTITY_DRIFT")
    material = dict(evidence.provenance)
    supplied = material.pop("digest", None)
    if supplied != evidence_digest((material,)):
        raise RiskSimulationError("invalid RW9.1 provenance digest")
    if evidence.provenance.get("eligible_population_digest") != evidence_digest(
        item.digest_material() for item in evidence.records
    ):
        raise RiskSimulationError("RW9.1 population digest mismatch")
    identities = [item.canonical_opportunity_id for item in evidence.records]
    if len(identities) != len(set(identities)):
        raise RiskSimulationError("ACCOUNT_FANOUT_PSEUDOREPLICATION")
    components = evidence.evidence_provenance.get("components", [])
    if any(
        isinstance(component, Mapping)
        and any(int(component.get("epoch_counts", {}).get(key, 0)) > 0
                for key in ("TRANSITIONAL", "LEGACY", "INCOMPATIBLE"))
        for component in components
    ):
        raise RiskSimulationError("NON_CURRENT_OR_INCOMPATIBLE_EVIDENCE")


def governed_windows(evidence: RiskPolicyEvidence) -> ChronologicalRiskWindows:
    validate_evidence(evidence)
    return split_chronological_windows(build_chronological_risk_population(evidence))


def readiness(evidence: RiskPolicyEvidence, windows: ChronologicalRiskWindows, question_id: str) -> dict[str, Any]:
    contract = SAMPLE_AND_READINESS_CONTRACT[question_id]
    total = evidence.summary.total_candidate_observations
    eligible = evidence.summary.eligible_observations
    coverage = eligible / total if total else 0.0
    required_coverage = float(contract["outcome_coverage"])
    minimum = int(contract["minimum_distinct_opportunities"])
    blockers: list[str] = []
    if coverage < required_coverage:
        blockers.append("OUTCOME_COVERAGE_BELOW_REQUIRED")
    if len(windows.calibration_records) < minimum:
        blockers.append("CALIBRATION_OPPORTUNITIES_BELOW_REQUIRED")
    if len(windows.validation_records) < int(windows.minimum_validation_opportunities):
        blockers.append("VALIDATION_OPPORTUNITIES_BELOW_REQUIRED")
    structural: list[str] = []
    if windows.missing_close_chronology_records:
        structural.append("MISSING_OUTCOME_CLOSE_CHRONOLOGY")
    return {
        "state": "BLOCKED" if structural else ("WAITING_DATA" if blockers else "READY"),
        "blockers": structural if structural else blockers,
        "outcome_coverage": coverage,
        "required_outcome_coverage": required_coverage,
        "calibration_opportunities": len(windows.calibration_records),
        "required_calibration_opportunities": minimum,
        "validation_opportunities": len(windows.validation_records),
        "required_validation_opportunities": int(windows.minimum_validation_opportunities),
        "purged_calibration_opportunities": len(windows.purged_records),
    }


def chronology_provenance(windows: ChronologicalRiskWindows) -> dict[str, Any]:
    return {
        "split_schema_version": windows.schema_version,
        "split_digest": windows.provenance["digest"],
        "source_population_digest": windows.provenance["source_population_digest"],
        "calibration_digest": windows.provenance["calibration_digest"],
        "validation_digest": windows.provenance["validation_digest"],
        "calibration_fraction": windows.calibration_fraction,
        "validation_fraction": 1.0 - windows.calibration_fraction,
        "order_key": list(windows.provenance["order_key"]),
        "first_validation_entry_utc": windows.first_validation_entry_utc,
        "purge_rule": VALIDATION_CHRONOLOGY["purge"],
        "freeze_rule": VALIDATION_CHRONOLOGY["freeze_rule"],
        "no_retrofit_rule": VALIDATION_CHRONOLOGY["no_retrofit"],
        "validation_outcomes_used_for_fitting": False,
    }


def _report(
    *,
    question_id: str,
    schema_version: str,
    status: str,
    evidence: RiskPolicyEvidence,
    windows: ChronologicalRiskWindows | None,
    overall: Mapping[str, Any],
    readiness_result: Mapping[str, Any],
    contract: Mapping[str, Any],
    upstream: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    chronology = chronology_provenance(windows) if windows else {}
    analytical = {
        "question_id": question_id,
        "hd10_adjudication_version": HD10_ADJUDICATION_VERSION,
        "contract": deepcopy(dict(contract)),
        "baseline_policy_id": evidence.summary.baseline_policy_id,
        "baseline_policy_version": evidence.summary.baseline_policy_version,
        "baseline_identity_hash": evidence.summary.baseline_identity_hash,
        "rw91_evidence_provenance_digest": evidence.provenance.get("digest"),
        "chronology": chronology,
        "readiness": deepcopy(dict(readiness_result)),
        "upstream_authority": deepcopy(dict(upstream or {})),
        "runtime_effect": "NONE",
    }
    analytical_digest = evidence_digest((analytical, overall))
    fingerprint = build_fingerprint_from_provenance(
        evidence.evidence_provenance, validation_score=f"HD10_{question_id}_GOVERNED",
    )
    fingerprint.update({
        "architecture_version": "new_pipeline_v1.2",
        "analytical_observations": len(windows.calibration_records) if windows else 0,
        "analytical_digest": analytical_digest,
    })
    report = {
        "report_schema_version": schema_version,
        "question_id": question_id,
        "status": status,
        "epoch": CURRENT if evidence.evidence_provenance.get("state") == CURRENT else "UNVERIFIED",
        "overall": deepcopy(dict(overall)),
        "confidence": "HIGH" if status == "COMPLETE" else "INSUFFICIENT_DATA",
        "dataset": {
            "source": "RW9.1 governed chronological risk-policy evidence",
            "eligible_opportunities": evidence.summary.eligible_observations,
            "calibration_opportunities": len(windows.calibration_records) if windows else 0,
            "validation_opportunities": len(windows.validation_records) if windows else 0,
            "purged_opportunities": len(windows.purged_records) if windows else 0,
        },
        "fingerprint": fingerprint,
        "recommendation": "RESEARCH_RESULT" if status == "COMPLETE" else status,
        "assumptions": [contract["claim_boundary"]],
        "warnings": ["Research-only result; runtime effect is NONE."],
        "provenance": {**analytical, "analytical_digest": analytical_digest},
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    material = deepcopy(report)
    material.pop("generated", None)
    report["provenance"]["report_digest"] = evidence_digest((material,))
    return report


def analyse_r3(evidence: RiskPolicyEvidence) -> dict[str, Any]:
    try:
        windows = governed_windows(evidence)
        ready = readiness(evidence, windows, "R3")
    except (RiskSimulationError, ValueError) as exc:
        windows = None
        ready = {"state": "BLOCKED", "blockers": [str(exc)]}
    overall: dict[str, Any] = {
        "experiment": "HD10 ruin_model_v1",
        "finding": f"R3 {ready['state']}: {', '.join(ready['blockers']) or 'ready for estimation'}.",
    }
    status = "BLOCKED" if ready["state"] == "BLOCKED" else "WAITING_DATA"
    if ready["state"] == "READY" and windows is not None:
        values = [item.outcome_r_multiple for item in windows.calibration_records]
        analytical = analytical_ruin_probability(values)
        simulation = monte_carlo_ruin(values)
        disagreement = abs(analytical - simulation["ruin_probability"])
        overall.update({
            "model_version": RUIN_MODEL_V1["model_version"],
            "analytical_ruin_probability": analytical,
            "monte_carlo": simulation,
            "survival_probability": simulation["survival_probability"],
            "expected_survival_trades": simulation["expected_survival_trades"],
            "required_sample_size": RUIN_MODEL_V1["minimum_samples"],
            "attained_calibration_sample_size": len(values),
            "agreement_absolute_difference": disagreement,
            "agreement_tolerance": RUIN_MODEL_V1["agreement_tolerance"],
            "parameters": deepcopy(RUIN_MODEL_V1),
        })
        if disagreement > float(RUIN_MODEL_V1["agreement_tolerance"]):
            status = "BLOCKED"
            ready = {**ready, "state": "BLOCKED", "blockers": ["ANALYTICAL_MONTE_CARLO_DISAGREEMENT"]}
            overall["finding"] = "R3 BLOCKED: analytical and Monte Carlo estimates disagree."
        else:
            status = "COMPLETE"
            overall["finding"] = "R3 COMPLETE: frozen ruin_model_v1 estimated from calibration evidence."
    return _report(
        question_id="R3", schema_version=R3_SCHEMA_VERSION, status=status,
        evidence=evidence, windows=windows, overall=overall, readiness_result=ready,
        contract=R3_CONTRACT,
    )


def drawdown_recovery_path(values: Sequence[float]) -> dict[str, Any]:
    outcomes = _finite_values(values)
    risk = float(R4_CONTRACT["simulation"]["risk_per_trade_pct"])
    states = {threshold: {"open": False, "peak": 0.0, "start": 0, "breaches": 0, "durations": []}
              for threshold in HALT_THRESHOLD_GRID_V1}
    equity = peak = 1.0
    max_drawdown = 0.0
    for index, outcome in enumerate(outcomes, start=1):
        factor = 1.0 + risk * outcome
        equity = equity * factor if factor > 0 else 0.0
        if equity > peak:
            peak = equity
        drawdown = (peak - equity) / peak if peak > 0 else 0.0
        max_drawdown = max(max_drawdown, drawdown)
        for threshold in HALT_THRESHOLD_GRID_V1:
            state = states[threshold]
            if not state["open"] and drawdown >= threshold:
                state.update({"open": True, "peak": peak, "start": index, "breaches": state["breaches"] + 1})
            elif state["open"] and equity >= state["peak"]:
                state["durations"].append(index - state["start"])
                state["open"] = False
    grid: list[dict[str, Any]] = []
    for threshold in HALT_THRESHOLD_GRID_V1:
        state = states[threshold]
        recoveries = len(state["durations"])
        breaches = int(state["breaches"])
        grid.append({
            "threshold": threshold,
            "breach_episodes": breaches,
            "recovery_episodes": recoveries,
            "unrecovered_episodes": breaches - recoveries,
            "recovery_probability": recoveries / breaches if breaches else 1.0,
            "mean_recovery_duration_lifecycles": (
                math.fsum(state["durations"]) / recoveries if recoveries else 0.0
            ),
            "maximum_recovery_duration_lifecycles": max(state["durations"], default=0),
        })
    selected = next(
        (item["threshold"] for item in grid[:-1]
         if item["breach_episodes"] and item["recovery_probability"] < 0.50),
        float(HALT_THRESHOLD_GRID_V1[-1]),
    )
    return {
        "equity_model": R4_CONTRACT["simulation"]["equity_model"],
        "maximum_drawdown": max_drawdown,
        "threshold_grid": grid,
        "selected_halt_threshold": selected,
        "resume_threshold": selected / 2.0,
        "selection_rule": R4_CONTRACT["halt_rule"],
        "fallback_rule": R4_CONTRACT["no_halt_fallback"],
        "definitions": deepcopy(R4_CONTRACT["definitions"]),
        "runtime_effect": "NONE",
    }


def _upstream_identity(report: Mapping[str, Any], question_id: str, windows: ChronologicalRiskWindows) -> dict[str, Any]:
    schemas = {"R3": R3_SCHEMA_VERSION, "R4": R4_SCHEMA_VERSION}
    contracts = {"R3": R3_CONTRACT, "R4": R4_CONTRACT}
    valid, reason = validate_report(
        report,
        question_id=question_id,
        schema_version=schemas[question_id],
        contract=contracts[question_id],
    )
    if not valid:
        raise RiskSimulationError(f"{question_id}_UPSTREAM_INVALID:{reason}")
    if report.get("question_id") != question_id or report.get("status") != "COMPLETE":
        raise RiskSimulationError(f"{question_id}_UPSTREAM_AUTHORITY_NOT_COMPLETE")
    provenance = report.get("provenance")
    if not isinstance(provenance, Mapping):
        raise RiskSimulationError(f"{question_id}_UPSTREAM_PROVENANCE_MISSING")
    if provenance.get("baseline_identity_hash") != BASELINE_RISK_POLICY_V1["identity_hash"]:
        raise RiskSimulationError(f"{question_id}_UPSTREAM_BASELINE_DRIFT")
    chronology = provenance.get("chronology")
    if not isinstance(chronology, Mapping) or chronology.get("split_digest") != windows.provenance["digest"]:
        raise RiskSimulationError(f"{question_id}_NO_RETROFIT_SPLIT_MISMATCH")
    return {
        "question_id": question_id,
        "report_schema_version": report.get("report_schema_version"),
        "analytical_digest": provenance.get("analytical_digest"),
        "report_digest": provenance.get("report_digest"),
        "split_digest": chronology.get("split_digest"),
    }


def analyse_r4(evidence: RiskPolicyEvidence, r3_report: Mapping[str, Any]) -> dict[str, Any]:
    upstream: dict[str, Any] = {}
    try:
        windows = governed_windows(evidence)
        ready = readiness(evidence, windows, "R4")
        upstream = _upstream_identity(r3_report, "R3", windows)
    except (RiskSimulationError, ValueError) as exc:
        windows = None
        ready = {"state": "BLOCKED", "blockers": [str(exc)]}
    overall: dict[str, Any] = {
        "experiment": "HD10 chronological drawdown recovery",
        "finding": f"R4 {ready['state']}: {', '.join(ready['blockers']) or 'ready for estimation'}.",
        "runtime_effect": "NONE",
    }
    status = "BLOCKED" if ready["state"] == "BLOCKED" else "WAITING_DATA"
    if ready["state"] == "READY" and windows is not None:
        result = drawdown_recovery_path(
            [item.outcome_r_multiple for item in windows.calibration_records]
        )
        breached = sum(item["breach_episodes"] > 0 for item in result["threshold_grid"])
        required_breached = int(SAMPLE_AND_READINESS_CONTRACT["R4"]["minimum_breached_grid_thresholds"])
        ready = {**ready, "breached_grid_thresholds": breached,
                 "required_breached_grid_thresholds": required_breached}
        overall.update(result)
        if breached < required_breached:
            ready = {**ready, "state": "WAITING_DATA", "blockers": ["NO_OBSERVED_THRESHOLD_BREACH"]}
            status = "WAITING_DATA"
            overall["finding"] = "R4 WAITING_DATA: no frozen-grid threshold was breached."
        else:
            status = "COMPLETE"
            overall["finding"] = "R4 COMPLETE: frozen chronological recovery grid evaluated."
    return _report(
        question_id="R4", schema_version=R4_SCHEMA_VERSION, status=status,
        evidence=evidence, windows=windows, overall=overall, readiness_result=ready,
        contract=R4_CONTRACT, upstream=upstream,
    )


def kelly_fraction(values: Sequence[float]) -> float:
    outcomes = _finite_values(values)
    if not outcomes:
        raise RiskSimulationError("Kelly calculation requires outcomes")
    wins = [value for value in outcomes if value > 0]
    losses = [abs(value) for value in outcomes if value < 0]
    if not wins or not losses:
        return 1.0 if wins else -1.0
    p = len(wins) / len(outcomes)
    q = 1.0 - p
    b = (math.fsum(wins) / len(wins)) / (math.fsum(losses) / len(losses))
    return (p * b - q) / b


def evaluate_sizing_model(
    model: Mapping[str, Any],
    values: Sequence[float],
    *,
    kelly: float,
    upstream_ruin_probability: float,
) -> dict[str, Any]:
    model_id = str(model["model_id"])
    family = str(model["family"])
    risk_fraction: float | None
    if "risk_fraction" in model:
        risk_fraction = float(model["risk_fraction"])
    elif "kelly_multiplier" in model:
        risk_fraction = kelly * float(model["kelly_multiplier"])
    else:
        risk_fraction = None
    result: dict[str, Any] = {
        "model_id": model_id,
        "family": family,
        "frozen_definition": deepcopy(dict(model)),
        "risk_fraction": risk_fraction,
        "ruin_probability": upstream_ruin_probability,
        "objective": R5_CONTRACT["objective"],
    }
    reasons: list[str] = []
    if family in {"KELLY", "FRACTIONAL_KELLY"} and kelly <= 0:
        reasons.append("NON_POSITIVE_KELLY_FAMILY_INELIGIBLE")
    if risk_fraction is None:
        reasons.append("NO_NUMERIC_RISK_FRACTION_IN_FROZEN_HD10_MODEL")
    if reasons:
        result.update({
            "geometric_mean_growth_factor": None,
            "maximum_drawdown": None,
            "eligible": False,
            "ineligibility_reasons": reasons,
        })
        return result
    outcomes = _finite_values(values)
    equity = peak = 1.0
    log_factors: list[float] = []
    max_drawdown = 0.0
    for outcome in outcomes:
        factor = 1.0 + risk_fraction * outcome
        if factor <= 0:
            reasons.append("NON_POSITIVE_GROWTH_FACTOR")
            break
        log_factors.append(math.log(factor))
        equity *= factor
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak)
    geometric = math.exp(math.fsum(log_factors) / len(outcomes)) if not reasons else None
    if max_drawdown > float(R5_CONTRACT["max_acceptable_drawdown"]):
        reasons.append("MAXIMUM_DRAWDOWN_EXCEEDS_0_30")
    if upstream_ruin_probability > float(RUIN_MODEL_V1["acceptable_ruin_threshold"]):
        reasons.append("RUIN_PROBABILITY_EXCEEDS_FROZEN_ACCEPTABLE_THRESHOLD")
    result.update({
        "geometric_mean_growth_factor": geometric,
        "maximum_drawdown": max_drawdown,
        "eligible": not reasons,
        "ineligibility_reasons": reasons,
    })
    return result


def analyse_r5(
    evidence: RiskPolicyEvidence,
    r3_report: Mapping[str, Any],
    r4_report: Mapping[str, Any],
) -> dict[str, Any]:
    upstream: dict[str, Any] = {}
    try:
        windows = governed_windows(evidence)
        ready = readiness(evidence, windows, "R5")
        upstream = {
            "R3": _upstream_identity(r3_report, "R3", windows),
            "R4": _upstream_identity(r4_report, "R4", windows),
        }
        r3_overall = r3_report.get("overall")
        if not isinstance(r3_overall, Mapping):
            raise RiskSimulationError("R3_UPSTREAM_OUTPUT_MISSING")
        mc = r3_overall.get("monte_carlo")
        if not isinstance(mc, Mapping):
            raise RiskSimulationError("R3_UPSTREAM_RUIN_OUTPUT_MISSING")
        upstream_ruin = float(mc["ruin_probability"])
    except (RiskSimulationError, ValueError, TypeError, KeyError) as exc:
        windows = None
        upstream_ruin = 1.0
        ready = {"state": "BLOCKED", "blockers": [str(exc)]}
    overall: dict[str, Any] = {
        "experiment": "HD10 closed-vocabulary position sizing",
        "finding": f"R5 {ready['state']}: {', '.join(ready['blockers']) or 'ready for estimation'}.",
        "runtime_effect": "NONE",
    }
    status = "BLOCKED" if ready["state"] == "BLOCKED" else "WAITING_DATA"
    if ready["state"] == "READY" and windows is not None:
        values = [item.outcome_r_multiple for item in windows.calibration_records]
        kelly = kelly_fraction(values)
        models = [
            evaluate_sizing_model(
                model, values, kelly=kelly, upstream_ruin_probability=upstream_ruin,
            )
            for model in SIZING_MODELS_V1
        ]
        eligible = [model for model in models if model["eligible"]]
        order = {model["model_id"]: index for index, model in enumerate(SIZING_MODELS_V1)}
        selected = max(
            eligible,
            key=lambda model: (model["geometric_mean_growth_factor"], -order[model["model_id"]]),
        ) if eligible else None
        overall.update({
            "model_vocabulary": [model["model_id"] for model in SIZING_MODELS_V1],
            "declared_families": list(R5_CONTRACT["declared_families"]),
            "kelly_fraction": kelly,
            "kelly_formula": R5_CONTRACT["kelly_formula"],
            "models": models,
            "selected_model_id": selected["model_id"] if selected else None,
            "selection_objective": R5_CONTRACT["objective"],
            "max_acceptable_drawdown": R5_CONTRACT["max_acceptable_drawdown"],
            "acceptable_ruin_threshold": RUIN_MODEL_V1["acceptable_ruin_threshold"],
            "forbidden_objectives": list(R5_CONTRACT["forbidden_objectives"]),
        })
        status = "COMPLETE"
        overall["finding"] = (
            f"R5 COMPLETE: selected {selected['model_id']} by geometric growth."
            if selected else "R5 COMPLETE valid null: no sizing model is eligible."
        )
    return _report(
        question_id="R5", schema_version=R5_SCHEMA_VERSION, status=status,
        evidence=evidence, windows=windows, overall=overall, readiness_result=ready,
        contract=R5_CONTRACT, upstream=upstream,
    )


def validate_report(
    report: Mapping[str, Any], *, question_id: str, schema_version: str,
    contract: Mapping[str, Any],
) -> tuple[bool, str]:
    if report.get("question_id") != question_id or report.get("report_schema_version") != schema_version:
        return False, f"{question_id} report identity/schema mismatch"
    provenance = report.get("provenance")
    if not isinstance(provenance, Mapping):
        return False, f"{question_id} provenance missing"
    if provenance.get("hd10_adjudication_version") != HD10_ADJUDICATION_VERSION:
        return False, f"{question_id} HD10 authority mismatch"
    if provenance.get("contract") != contract:
        return False, f"{question_id} frozen contract mismatch"
    if (
        provenance.get("baseline_policy_id") != BASELINE_RISK_POLICY_V1["policy_id"]
        or provenance.get("baseline_policy_version") != BASELINE_RISK_POLICY_V1["policy_version"]
        or provenance.get("baseline_identity_hash") != BASELINE_RISK_POLICY_V1["identity_hash"]
    ):
        return False, f"{question_id} baseline identity mismatch"
    readiness_result = provenance.get("readiness")
    if not isinstance(readiness_result, Mapping):
        return False, f"{question_id} readiness missing"
    expected = "COMPLETE" if readiness_result.get("state") == "READY" else readiness_result.get("state")
    if report.get("status") != expected:
        return False, f"{question_id} status contradicts readiness"
    if provenance.get("runtime_effect") != "NONE":
        return False, f"{question_id} runtime boundary mismatch"
    analytical = dict(provenance)
    report_digest = analytical.pop("report_digest", None)
    analytical_digest = analytical.pop("analytical_digest", None)
    if analytical_digest != evidence_digest((analytical, report.get("overall"))):
        return False, f"{question_id} analytical digest mismatch"
    report_copy = deepcopy(dict(report))
    report_copy.pop("generated", None)
    report_copy["provenance"].pop("report_digest", None)
    if report_digest != evidence_digest((report_copy,)):
        return False, f"{question_id} report digest mismatch"
    fingerprint = report.get("fingerprint")
    if not isinstance(fingerprint, Mapping) or fingerprint.get("analytical_digest") != analytical_digest:
        return False, f"{question_id} fingerprint mismatch"
    valid, state, _ = validate_evidence_provenance(fingerprint.get("evidence_provenance"))
    if not valid or (report.get("status") == "COMPLETE" and state != CURRENT):
        return False, f"{question_id} evidence provenance is not valid CURRENT authority"
    return True, f"valid governed HD10 {question_id} report"
