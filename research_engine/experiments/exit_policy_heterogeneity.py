"""Governed HD09 candidate-effect heterogeneity research for EX5-EX8."""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from core.v10.strategy_family import StrategyFamily
from research_engine.control_plane.evidence_provenance import evidence_digest
from research_engine.control_plane.evidence_readiness import EvidenceRequirement, evaluate_evidence_readiness
from research_engine.control_plane.exit_bar_path import GovernedExitBarPathEvidence, LifecyclePathSource
from research_engine.control_plane.exit_baseline_replay import GovernedBaselineReproductionPopulation
from research_engine.control_plane.exit_candidate_replay import CANDIDATE_POLICY_IDS, CandidateReplayPopulation
from research_engine.control_plane.exit_dimension_evidence import (
    DIMENSION_EVIDENCE_SCHEMA_VERSION,
    PATTERN_SOURCE,
    REGIME_SOURCE,
    STRATEGY_SOURCE,
    ExitDimensionEvidence,
    build_exit_dimension_evidence_v1,
    validate_exit_dimension_evidence,
)
from research_engine.experiments.exit_policy_governed import (
    _DEFAULT_SHADOW_DIR,
    _REPORTS_DIR,
    _normal_two_sided_p,
    _records,
    _validate_foundations,
    holm_adjust,
    load_governed_foundations,
)
from research_engine.experiments.strategy_horizon_interaction import _chi_square_survival
from research_engine.registry.exit_policy_adjudication import (
    CANDIDATE_POLICIES_V1,
    CLUSTERED_INFERENCE,
    COMMON_ANALYTICAL_CONTRACT,
    HD09_ADJUDICATED_CONTRACT,
    HD09_ADJUDICATION_VERSION,
    HETEROGENEITY_CONTRACT,
    REPORT_OWNERSHIP,
    SAMPLE_AND_READINESS_CONTRACT,
)

REPORT_SCHEMA_VERSION = "hd09_governed_exit_heterogeneity_v1"
TARGETS = ("EX5", "EX6", "EX7", "EX8")
CANONICAL_HORIZONS = ("SCALP", "INTRADAY", "EXTENDED")
CANONICAL_STRATEGIES = tuple(item.value for item in StrategyFamily if item is not StrategyFamily.NONE)
CANONICAL_REGIMES = ("TRENDING", "RANGING", "TRANSITIONAL")
MIN_CELL = int(HETEROGENEITY_CONTRACT["minimum_distinct_opportunities_per_candidate_dimension_cell"])
MIN_LEVELS = int(HETEROGENEITY_CONTRACT["minimum_levels"])
ALPHA = float(HETEROGENEITY_CONTRACT["global_alpha"])


class HeterogeneityAnalysisError(ValueError):
    pass


def _validate_contract() -> None:
    if HD09_ADJUDICATED_CONTRACT.get("ex5_ex8") != HETEROGENEITY_CONTRACT:
        raise HeterogeneityAnalysisError("invalid HD09 heterogeneity authority")
    if tuple(HETEROGENEITY_CONTRACT["questions"]) != TARGETS:
        raise HeterogeneityAnalysisError("HD09 heterogeneity target drift")
    if tuple(HETEROGENEITY_CONTRACT["dimensions"]["EX5"]["levels"]) != CANONICAL_HORIZONS:
        raise HeterogeneityAnalysisError("canonical horizon authority drift")
    if tuple(HETEROGENEITY_CONTRACT["dimensions"]["EX6"]["levels"]) != CANONICAL_STRATEGIES:
        raise HeterogeneityAnalysisError("StrategyFamily authority drift")
    if HETEROGENEITY_CONTRACT["dimensions"]["EX6"].get("excluded_level") != "NONE":
        raise HeterogeneityAnalysisError("StrategyFamily NONE exclusion drift")
    if not HETEROGENEITY_CONTRACT["dimensions"]["EX7"].get("h4_fallback_forbidden"):
        raise HeterogeneityAnalysisError("EX7 H4 fallback prohibition drift")


_validate_contract()


def load_governed_dimensions(
    path: GovernedExitBarPathEvidence,
    shadow_dir: str | Path = _DEFAULT_SHADOW_DIR,
) -> ExitDimensionEvidence:
    sources = [
        LifecyclePathSource(record, {}, ())
        for record in _records(Path(shadow_dir))
        if record.get("event_type") == "OPEN"
    ]
    return build_exit_dimension_evidence_v1(sources, path)


def _dimension_spec(question_id: str, dimensions: ExitDimensionEvidence) -> dict[str, Any]:
    contract = HETEROGENEITY_CONTRACT["dimensions"][question_id]
    if question_id == "EX5":
        return {
            "field": "trade_horizon", "name": "trade_horizon",
            "source": contract["source"], "levels": CANONICAL_HORIZONS,
            "coverage": float(contract["coverage"]),
        }
    if question_id == "EX6":
        return {
            "field": "strategy_family", "name": "strategy_family",
            "source": STRATEGY_SOURCE, "levels": CANONICAL_STRATEGIES,
            "coverage": float(contract["coverage"]),
        }
    if question_id == "EX7":
        return {
            "field": "market_regime", "name": "market_regime",
            "source": REGIME_SOURCE, "levels": CANONICAL_REGIMES,
            "coverage": float(contract["coverage"]),
        }
    observed = sorted({
        item.candlestick_pattern for item in dimensions.records
        if isinstance(item.candlestick_pattern, str) and item.candlestick_pattern
    })
    return {
        "field": "candlestick_pattern", "name": "candlestick_pattern",
        "source": PATTERN_SOURCE, "levels": tuple(observed),
        "coverage": float(contract["coverage"]),
    }


def _level_value(record: Any, field: str) -> str | None:
    value = getattr(record, field)
    return value if isinstance(value, str) and value else None


def _quadratic_wald(
    estimates: np.ndarray, covariance: np.ndarray,
) -> dict[str, Any]:
    if not np.all(np.isfinite(estimates)) or not np.all(np.isfinite(covariance)):
        raise HeterogeneityAnalysisError("non-finite omnibus inputs")
    covariance = (covariance + covariance.T) / 2.0
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    scale = max(1.0, float(np.max(np.abs(eigenvalues))) if eigenvalues.size else 1.0)
    tolerance = max(covariance.shape or (1,)) * np.finfo(float).eps * scale * 100.0
    if np.any(eigenvalues < -tolerance):
        raise HeterogeneityAnalysisError("invalid clustered omnibus covariance")
    positive = eigenvalues > tolerance
    rank = int(np.count_nonzero(positive))
    if rank:
        basis = eigenvectors[:, positive]
        coordinates = basis.T @ estimates
        statistic = float(np.sum((coordinates * coordinates) / eigenvalues[positive]))
        residual = estimates - basis @ coordinates
    else:
        statistic = 0.0
        residual = estimates.copy()
    residual_tolerance = 1e-10 * max(1.0, float(np.linalg.norm(estimates)))
    deterministic_nonzero = float(np.linalg.norm(residual)) > residual_tolerance
    if deterministic_nonzero:
        return {
            "statistic": "Infinity", "degrees_of_freedom": rank,
            "p_value": 0.0, "covariance_rank": rank,
            "degenerate_nonzero_direction": True,
        }
    p_value = 1.0 if rank == 0 else _chi_square_survival(statistic, rank)
    if p_value is None or not math.isfinite(p_value):
        raise HeterogeneityAnalysisError("omnibus Wald inference is non-estimable")
    return {
        "statistic": statistic, "degrees_of_freedom": rank,
        "p_value": p_value, "covariance_rank": rank,
        "degenerate_nonzero_direction": False,
    }


def fit_candidate_dimension_interaction(
    rows: Iterable[Mapping[str, Any]],
    levels: Sequence[str],
) -> dict[str, Any]:
    """Fit saturated candidate×level means with opportunity-clustered CR0."""
    levels = tuple(levels)
    if len(levels) < 2:
        raise HeterogeneityAnalysisError("fewer than two sufficient dimension levels")
    candidates = tuple(CANDIDATE_POLICY_IDS)
    candidate_order = {value: index for index, value in enumerate(candidates)}
    level_order = {value: index for index, value in enumerate(levels)}
    prepared = []
    seen: set[tuple[str, tuple[str, str, str]]] = set()
    for row in rows:
        candidate = str(row.get("candidate_policy_id", ""))
        level = str(row.get("dimension_level", ""))
        identity_raw = row.get("lifecycle_identity")
        identity = tuple(identity_raw) if isinstance(identity_raw, (tuple, list)) else ()
        opportunity = str(row.get("canonical_opportunity_id", ""))
        if candidate not in candidate_order or level not in level_order:
            raise HeterogeneityAnalysisError("unknown candidate or dimension level")
        if len(identity) != 3 or identity[1] != opportunity or not opportunity:
            raise HeterogeneityAnalysisError("invalid canonical-opportunity cluster identity")
        key = (candidate, identity)
        if key in seen:
            raise HeterogeneityAnalysisError("account fanout or duplicate analytical row")
        seen.add(key)
        value = float(row["effect"])
        if not math.isfinite(value):
            raise HeterogeneityAnalysisError("non-finite paired effect")
        prepared.append((candidate, level, identity, opportunity, value))
    prepared.sort(key=lambda item: (item[3], item[2], candidate_order[item[0]], level_order[item[1]]))
    horizon_counts = Counter((candidate, opportunity) for candidate, _, _, opportunity, _ in prepared)
    parameter_count = len(candidates) * len(levels)
    breads = np.zeros(parameter_count)
    rhs = np.zeros(parameter_count)
    indexed_rows = []
    for candidate, level, identity, opportunity, value in prepared:
        parameter = candidate_order[candidate] * len(levels) + level_order[level]
        weight = 1.0 / horizon_counts[(candidate, opportunity)]
        breads[parameter] += weight
        rhs[parameter] += weight * value
        indexed_rows.append((opportunity, parameter, value, weight))
    if np.any(breads <= 0) or not np.all(np.isfinite(breads)):
        raise HeterogeneityAnalysisError("candidate×dimension design is singular")
    beta = rhs / breads
    scores: dict[str, np.ndarray] = defaultdict(lambda: np.zeros(parameter_count))
    for opportunity, parameter, value, weight in indexed_rows:
        scores[opportunity][parameter] += weight * (value - beta[parameter])
    meat = np.zeros((parameter_count, parameter_count))
    for opportunity in sorted(scores):
        score = scores[opportunity]
        meat += np.outer(score, score)
    covariance = meat / np.outer(breads, breads)
    contrasts = []
    contrast_ids = []
    for candidate_index, candidate in enumerate(candidates):
        reference = candidate_index * len(levels)
        for level_index in range(1, len(levels)):
            contrast = np.zeros(parameter_count)
            contrast[reference + level_index] = 1.0
            contrast[reference] = -1.0
            contrasts.append(contrast)
            contrast_ids.append(f"{candidate}:{levels[level_index]}-vs-{levels[0]}")
    matrix = np.vstack(contrasts)
    omnibus = _quadratic_wald(matrix @ beta, matrix @ covariance @ matrix.T)
    omnibus.update({
        "method": "canonical-opportunity clustered CR0 Wald chi-square",
        "null": "all within-candidate dimension-level effects are equal",
        "contrast_identities": contrast_ids,
        "reject_at_alpha_0_05": omnibus["p_value"] <= ALPHA,
    })
    cell_results = []
    for candidate_index, candidate in enumerate(candidates):
        for level_index, level in enumerate(levels):
            parameter = candidate_index * len(levels) + level_index
            cell_results.append({
                "candidate_policy_id": candidate,
                "dimension_level": level,
                "weighted_effect_estimate": float(beta[parameter]),
                "standard_error": math.sqrt(max(0.0, float(covariance[parameter, parameter]))),
            })
    followups = []
    if omnibus["reject_at_alpha_0_05"]:
        family_order = []
        raw = []
        pending = []
        for candidate_index, candidate in enumerate(candidates):
            for first_index, second_index in combinations(range(len(levels)), 2):
                first = candidate_index * len(levels) + first_index
                second = candidate_index * len(levels) + second_index
                identity = f"{candidate}:{levels[first_index]}-vs-{levels[second_index]}"
                estimate = float(beta[first] - beta[second])
                variance = float(
                    covariance[first, first] + covariance[second, second]
                    - 2.0 * covariance[first, second]
                )
                if variance < -1e-12 or not math.isfinite(variance):
                    raise HeterogeneityAnalysisError("invalid follow-up contrast covariance")
                se = math.sqrt(max(0.0, variance))
                p_value = _normal_two_sided_p(estimate, se)
                family_order.append(identity)
                raw.append((identity, p_value))
                pending.append((identity, candidate, levels[first_index], levels[second_index], estimate, se, p_value))
        adjusted = holm_adjust(raw, family_order)
        critical = float(CLUSTERED_INFERENCE["critical_value"])
        for identity, candidate, first_level, second_level, estimate, se, p_value in pending:
            followups.append({
                "test_identity": identity,
                "candidate_policy_id": candidate,
                "first_level": first_level,
                "second_level": second_level,
                "effect_contrast": estimate,
                "standard_error": se,
                "confidence_interval_95": [estimate - critical * se, estimate + critical * se],
                "raw_two_sided_p_value": p_value,
                "holm_adjusted_p_value": adjusted[identity],
                "interpretation": "SUPPORTED_HETEROGENEITY" if adjusted[identity] <= ALPHA else "NOT_SUPPORTED",
            })
    return {
        "cell_effects": cell_results,
        "omnibus": omnibus,
        "followup_family_order": [item["test_identity"] for item in followups],
        "followups": followups,
        "horizon_weighting": COMMON_ANALYTICAL_CONTRACT["horizon_weight_definition"],
        "cluster_identity": "canonical_opportunity_id",
    }


def _blocked_report(question_id: str, reason: str) -> dict[str, Any]:
    report = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "question_id": question_id, "status": "BLOCKED", "epoch": "CURRENT",
        "overall": {"finding": f"Governed HD09 analysis blocked: {reason}", "tests": []},
        "dataset": {"source": "exit_candidate_replay_v1", "sample_size": 0},
        "fingerprint": {"epoch": "CURRENT", "source": "exit_candidate_replay_v1", "records_used": 0},
        "confidence": "INSUFFICIENT_DATA", "recommendation": "BLOCKED",
        "warnings": [reason], "assumptions": [],
        "provenance": {
            "hd09_adjudication_version": HD09_ADJUDICATION_VERSION,
            "readiness": {"state": "BLOCKED", "blockers": [reason]},
        },
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    material = dict(report); material.pop("generated", None)
    report["provenance"]["report_digest"] = evidence_digest((material,))
    return report


def _analyse(
    question_id: str,
    candidate: CandidateReplayPopulation,
    reproduction: GovernedBaselineReproductionPopulation,
    path: GovernedExitBarPathEvidence,
    dimensions: ExitDimensionEvidence,
    *,
    foundations_validated: bool = False,
) -> dict[str, Any]:
    try:
        if not foundations_validated:
            _validate_foundations(candidate, reproduction, path)
        validate_exit_dimension_evidence(dimensions, path)
        spec = _dimension_spec(question_id, dimensions)
        baseline = {item.lifecycle_identity: item.replay for item in reproduction.records}
        dimension_index = {item.lifecycle_identity: item for item in dimensions.records}
        if len(baseline) != len(reproduction.records) or len(dimension_index) != len(dimensions.records):
            raise HeterogeneityAnalysisError("non-unique governed identity population")
        allowed = set(spec["levels"])
        analytical_rows = []
        invalid_lifecycles: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
        valid_lifecycles: set[tuple[str, str, str]] = set()
        observed_levels: set[str] = set()
        for item in candidate.records:
            dimension_record = dimension_index.get(item.lifecycle_identity)
            if dimension_record is None:
                raise HeterogeneityAnalysisError("candidate lacks dimension evidence binding")
            level = _level_value(dimension_record, spec["field"])
            if question_id == "EX6" and level == "NONE":
                invalid_lifecycles["EXCLUDED_STRATEGY_FAMILY_NONE"].add(item.lifecycle_identity)
                continue
            if level is None:
                invalid_lifecycles["MISSING_ENTRY_DIMENSION_AUTHORITY"].add(item.lifecycle_identity)
                continue
            if level not in allowed:
                invalid_lifecycles["INVALID_ENTRY_DIMENSION_AUTHORITY"].add(item.lifecycle_identity)
                continue
            observed_levels.add(level)
            valid_lifecycles.add(item.lifecycle_identity)
            analytical_rows.append({
                "candidate_policy_id": item.candidate_policy_id,
                "dimension_level": level,
                "lifecycle_identity": item.lifecycle_identity,
                "canonical_opportunity_id": item.canonical_opportunity_id,
                "effect": item.candidate_r - baseline[item.lifecycle_identity].pnl_r_multiple,
            })
        level_lifecycles: dict[str, set[tuple[str, str, str]]] = {level: set() for level in spec["levels"]}
        level_opportunities: dict[str, set[str]] = {level: set() for level in spec["levels"]}
        cell_opportunities: dict[tuple[str, str], set[str]] = {
            (candidate_id, level): set()
            for candidate_id in CANDIDATE_POLICY_IDS for level in spec["levels"]
        }
        for row in analytical_rows:
            level = row["dimension_level"]
            level_lifecycles[level].add(tuple(row["lifecycle_identity"]))
            level_opportunities[level].add(row["canonical_opportunity_id"])
            cell_opportunities[(row["candidate_policy_id"], level)].add(row["canonical_opportunity_id"])
        sufficient_levels = tuple(
            level for level in spec["levels"]
            if all(len(cell_opportunities[(candidate_id, level)]) >= MIN_CELL for candidate_id in CANDIDATE_POLICY_IDS)
        )
        insufficient_levels = tuple(level for level in spec["levels"] if level not in sufficient_levels)
        sufficient_rows = [row for row in analytical_rows if row["dimension_level"] in sufficient_levels]
        eligible_opportunities = {identity[1] for identity in valid_lifecycles}
        common = SAMPLE_AND_READINESS_CONTRACT["EX5_EX8"]
        overall_gate = evaluate_evidence_readiness(
            total_count=max(1, len(valid_lifecycles)), valid_count=len(valid_lifecycles),
            distinct_valid_count=len(eligible_opportunities),
            requirement=EvidenceRequirement(
                int(common["minimum_paired_lifecycles"]),
                int(common["minimum_distinct_opportunities"]), 0.0,
            ),
        )
        governed_coverage = candidate.summary.candidate_eligible_lifecycles / path.summary.total_completed_lifecycles
        dimension_coverage = len(valid_lifecycles) / candidate.summary.candidate_eligible_lifecycles if candidate.summary.candidate_eligible_lifecycles else 0.0
        required_dimension_lifecycles = math.ceil(
            spec["coverage"] * candidate.summary.candidate_eligible_lifecycles
        )
        blockers = list(overall_gate.blockers)
        if governed_coverage < float(SAMPLE_AND_READINESS_CONTRACT["common_path_coverage"]):
            blockers.append("GOVERNED_COVERAGE_BELOW_REQUIRED")
        if dimension_coverage < spec["coverage"]:
            blockers.append("DIMENSION_COVERAGE_BELOW_REQUIRED")
        if len(sufficient_levels) < MIN_LEVELS:
            blockers.append("SUFFICIENT_DIMENSION_LEVELS_BELOW_REQUIRED")
        readiness_state = "READY" if not blockers else "WAITING_DATA"
        inference = None
        if readiness_state == "READY":
            inference = fit_candidate_dimension_interaction(sufficient_rows, sufficient_levels)
        readiness = {
            "state": readiness_state, "blockers": blockers,
            "overall_eligible_lifecycle_count": len(valid_lifecycles),
            "distinct_canonical_opportunity_count": len(eligible_opportunities),
            "governed_coverage": governed_coverage,
            "required_governed_coverage": SAMPLE_AND_READINESS_CONTRACT["common_path_coverage"],
            "dimension_coverage": dimension_coverage,
            "required_dimension_coverage": spec["coverage"],
            "required_dimension_valid_lifecycles": required_dimension_lifecycles,
            "remaining_dimension_valid_lifecycles": max(
                0, required_dimension_lifecycles - len(valid_lifecycles),
            ),
            "required_paired_lifecycles": common["minimum_paired_lifecycles"],
            "remaining_paired_lifecycles": max(0, int(common["minimum_paired_lifecycles"]) - len(valid_lifecycles)),
            "required_distinct_opportunities": common["minimum_distinct_opportunities"],
            "remaining_distinct_opportunities": max(0, int(common["minimum_distinct_opportunities"]) - len(eligible_opportunities)),
            "required_opportunities_per_candidate_level_cell": MIN_CELL,
            "required_sufficient_levels": MIN_LEVELS,
            "remaining_sufficient_levels": max(0, MIN_LEVELS - len(sufficient_levels)),
        }
        cell_counts = {
            f"{candidate_id}|{level}": len(cell_opportunities[(candidate_id, level)])
            for candidate_id in CANDIDATE_POLICY_IDS for level in spec["levels"]
        }
        cell_remaining = {
            key: max(0, MIN_CELL - count) for key, count in cell_counts.items()
        }
        population = {
            "dimension_name": spec["name"], "dimension_source": spec["source"],
            "observed_levels": [level for level in spec["levels"] if level in observed_levels],
            "level_lifecycle_counts": {level: len(level_lifecycles[level]) for level in spec["levels"]},
            "level_opportunity_counts": {level: len(level_opportunities[level]) for level in spec["levels"]},
            "candidate_level_opportunity_counts": cell_counts,
            "candidate_level_remaining_opportunities": cell_remaining,
            "sufficient_levels": list(sufficient_levels),
            "insufficient_levels": list(insufficient_levels),
            "dimension_excluded_lifecycles": {
                reason: len(identities)
                for reason, identities in sorted(invalid_lifecycles.items())
            },
        }
        analytical_material = {
            "question_id": question_id,
            "scientific_contract": HETEROGENEITY_CONTRACT,
            "hd09_adjudication_version": HD09_ADJUDICATION_VERSION,
            "path_population_digest": path.provenance["digest"],
            "baseline_reproduction_population_digest": reproduction.provenance["digest"],
            "candidate_replay_population_digest": candidate.provenance["digest"],
            "dimension_evidence_schema": DIMENSION_EVIDENCE_SCHEMA_VERSION,
            "dimension_evidence_digest": dimensions.provenance["digest"],
            "candidate_policy_identities": [dict(item) for item in CANDIDATE_POLICIES_V1],
            "population": population,
            "analytical_population_digest": evidence_digest(sufficient_rows),
            "horizon_weighting_contract": COMMON_ANALYTICAL_CONTRACT["horizon_weight_definition"],
            "cluster_identity": "canonical_opportunity_id",
            "interaction_model": HETEROGENEITY_CONTRACT["model"],
            "covariance": HETEROGENEITY_CONTRACT["covariance"],
            "finite_sample_correction": HETEROGENEITY_CONTRACT["finite_sample_correction"],
            "omnibus": inference["omnibus"] if inference else None,
            "followup_family_order": inference["followup_family_order"] if inference else [],
            "readiness": readiness,
        }
        analytical_digest = evidence_digest((analytical_material,))
        status = "COMPLETE" if readiness_state == "READY" else "WAITING_DATA"
        finding = (
            "Valid sufficient HD09 heterogeneity evaluation; omnibus "
            + ("rejected." if inference and inference["omnibus"]["reject_at_alpha_0_05"] else "did not reject.")
            if status == "COMPLETE" else
            f"Governed machinery valid; evidence gates unmet: {', '.join(blockers)}."
        )
        report = {
            "report_schema_version": REPORT_SCHEMA_VERSION,
            "question_id": question_id, "status": status, "epoch": "CURRENT",
            "overall": {
                "finding": finding, "experiment": "HD09 governed exit-effect heterogeneity",
                "baseline": "SHADOW_BASELINE_V1", "population": population,
                "cell_effects": inference["cell_effects"] if inference else [],
                "omnibus_interaction_test": inference["omnibus"] if inference else None,
                "followups": inference["followups"] if inference else [],
                "sample_size": len(valid_lifecycles),
                "distinct_canonical_opportunities": len(eligible_opportunities),
            },
            "dataset": {"source": "exit_candidate_replay_v1+exit_dimension_evidence_v1", "sample_size": len(valid_lifecycles)},
            "fingerprint": {"epoch": "CURRENT", "source": "MULTI_SOURCE", "records_used": len(sufficient_rows), "analytical_digest": analytical_digest},
            "confidence": "HIGH" if status == "COMPLETE" else "INSUFFICIENT_DATA",
            "recommendation": "FINDING: governed heterogeneity evaluation" if status == "COMPLETE" else "WAIT",
            "assumptions": [HD09_ADJUDICATED_CONTRACT["claim_boundary"]],
            "warnings": ["Research-only simulated policy effects; no production application authority."],
            "provenance": {**analytical_material, "analytical_digest": analytical_digest},
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        report_material = dict(report); report_material.pop("generated", None)
        report["provenance"]["report_digest"] = evidence_digest((report_material,))
        return report
    except (ValueError, TypeError, KeyError, ArithmeticError) as error:
        return _blocked_report(question_id, str(error))


def analyse_ex5(candidate, reproduction, path, dimensions, **kwargs):
    return _analyse("EX5", candidate, reproduction, path, dimensions, **kwargs)


def analyse_ex6(candidate, reproduction, path, dimensions, **kwargs):
    return _analyse("EX6", candidate, reproduction, path, dimensions, **kwargs)


def analyse_ex7(candidate, reproduction, path, dimensions, **kwargs):
    return _analyse("EX7", candidate, reproduction, path, dimensions, **kwargs)


def analyse_ex8(candidate, reproduction, path, dimensions, **kwargs):
    return _analyse("EX8", candidate, reproduction, path, dimensions, **kwargs)


def run_governed_exit_heterogeneity(*, persist: bool = False) -> dict[str, dict[str, Any]]:
    path, reproduction, candidate = load_governed_foundations()
    _validate_foundations(candidate, reproduction, path)
    dimensions = load_governed_dimensions(path)
    validate_exit_dimension_evidence(dimensions, path)
    reports = {
        question_id: _analyse(
            question_id, candidate, reproduction, path, dimensions,
            foundations_validated=True,
        )
        for question_id in TARGETS
    }
    if persist:
        _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        for question_id, report in reports.items():
            (_REPORTS_DIR / REPORT_OWNERSHIP[question_id]).write_text(
                json.dumps(report, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8",
            )
    return reports


def _run(question_id: str, governed_exit_evidence=None) -> dict[str, Any]:
    if governed_exit_evidence is not None:
        return _governed_heterogeneity_result(question_id, governed_exit_evidence)
    path, reproduction, candidate = load_governed_foundations()
    dimensions = load_governed_dimensions(path)
    return _analyse(question_id, candidate, reproduction, path, dimensions)


def _governed_heterogeneity_result(question_id: str, evidence: Any) -> dict[str, Any]:
    """Run an HD09 heterogeneity evaluator from snapshot-bound foundations."""
    if evidence.missing_evidence:
        missing = list(evidence.missing_evidence)
        report = {
            "report_schema_version": REPORT_SCHEMA_VERSION,
            "question_id": question_id, "status": "INSUFFICIENT_DATA",
            "epoch": "CURRENT",
            "overall": {
                "finding": (
                    "Governed snapshot does not bind the ordered M5 OHLC exit "
                    "path required by the HD09 contract; heterogeneity cannot be "
                    "evaluated"),
                "tests": [],
                "observation_gap": "; ".join(missing),
            },
            "missing_evidence": missing,
            "failure_reason": "GOVERNED_EXIT_EVIDENCE_INCOMPLETE",
            "dataset": {"source": "governed_exit_evidence", "sample_size": evidence.completed_lifecycles},
            "fingerprint": {"epoch": "CURRENT", "source": "governed_exit_evidence"},
            "confidence": "NOT_ESTIMABLE",
            "recommendation": "WAIT",
            "warnings": list(missing),
            "assumptions": [],
            "provenance": {
                "question_id": question_id,
                "hd09_adjudication_version": HD09_ADJUDICATION_VERSION,
                "completed_lifecycles": evidence.completed_lifecycles,
                "eligible_path_lifecycles": evidence.eligible_path_lifecycles,
                "missing_evidence": missing,
            },
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        material = dict(report)
        material.pop("generated", None)
        report["provenance"]["report_digest"] = evidence_digest((material,))
        return report
    return _analyse(
        question_id, evidence.candidate, evidence.reproduction,
        evidence.path, evidence.dimensions,
    )


def run_ex5(*, governed_exit_evidence=None): return _run("EX5", governed_exit_evidence)
def run_ex6(*, governed_exit_evidence=None): return _run("EX6", governed_exit_evidence)
def run_ex7(*, governed_exit_evidence=None): return _run("EX7", governed_exit_evidence)
def run_ex8(): return _run("EX8")


def validate_governed_heterogeneity_report(report: Mapping[str, Any], question_id: str) -> tuple[bool, str]:
    if question_id not in TARGETS or report.get("question_id") != question_id:
        return False, "governed heterogeneity report identity mismatch"
    if report.get("report_schema_version") != REPORT_SCHEMA_VERSION:
        return False, "governed heterogeneity report schema mismatch"
    provenance = report.get("provenance")
    if not isinstance(provenance, Mapping):
        return False, "governed heterogeneity provenance missing"
    report_copy = dict(report); report_copy.pop("generated", None)
    provenance_copy = dict(provenance); stored_report = provenance_copy.pop("report_digest", None)
    report_copy["provenance"] = provenance_copy
    if stored_report != evidence_digest((report_copy,)):
        return False, "governed heterogeneity report digest mismatch"
    analytical = dict(provenance_copy); stored_analytical = analytical.pop("analytical_digest", None)
    if stored_analytical != evidence_digest((analytical,)):
        return False, "governed heterogeneity analytical digest mismatch"
    if report.get("fingerprint", {}).get("analytical_digest") != stored_analytical:
        return False, "governed heterogeneity fingerprint/provenance mismatch"
    readiness = provenance.get("readiness", {})
    expected = {"READY": "COMPLETE", "WAITING_DATA": "WAITING_DATA", "BLOCKED": "BLOCKED"}.get(readiness.get("state"))
    if expected is None or report.get("status") != expected:
        return False, "governed heterogeneity status contradicts readiness"
    if expected == "COMPLETE":
        omnibus = provenance.get("omnibus")
        if not isinstance(omnibus, Mapping) or "p_value" not in omnibus:
            return False, "governed heterogeneity omnibus result missing"
        followups = report.get("overall", {}).get("followups", [])
        if not omnibus.get("reject_at_alpha_0_05") and followups:
            return False, "follow-ups reported without omnibus rejection"
        if tuple(provenance.get("followup_family_order", ())) != tuple(
            item.get("test_identity") for item in followups
        ):
            return False, "follow-up multiplicity family mismatch"
    return True, "valid governed HD09 heterogeneity report"
