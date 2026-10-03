"""Generic monitoring and governed readiness for paired candidate evidence."""

from __future__ import annotations

import statistics
from typing import Any

from core.shadow.candidate_evaluation import CandidateEvaluation, get_candidate_evaluation
from core.shadow.candidate_persistence import load_candidate_events
from core.shadow.candidate_runtime import get_candidate_runtime


def _profit_factor(values: list[float]) -> tuple[float | None, str]:
    gross_profit = sum(value for value in values if value > 0)
    gross_loss = abs(sum(value for value in values if value < 0))
    if gross_loss == 0:
        return (
            None,
            "NO_GROSS_LOSSES" if gross_profit > 0 else "NO_GAINS_OR_LOSSES",
        )
    return gross_profit / gross_loss, "FINITE"


def _max_drawdown(values: list[float]) -> float:
    cumulative = 0.0
    peak = 0.0
    maximum = 0.0
    for value in values:
        cumulative += value
        peak = max(peak, cumulative)
        maximum = max(maximum, peak - cumulative)
    return round(maximum, 4)


def _collapse_by_canonical(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_canonical: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        canonical_id = str(row.get("canonical_opportunity_id") or "")
        if canonical_id:
            by_canonical.setdefault(canonical_id, []).append(row)
    collapsed = []
    for canonical_id, group in by_canonical.items():
        baseline = statistics.fmean(float(row["baseline_r"]) for row in group)
        candidate = statistics.fmean(float(row["candidate_r"]) for row in group)
        delta = candidate - baseline
        collapsed.append({
            "canonical_opportunity_id": canonical_id,
            "baseline_r": baseline,
            "candidate_r": candidate,
            "paired_delta_r": delta,
            "baseline_exit_time": min(int(row.get("baseline_exit_time") or 0)
                                      for row in group),
            "outcome_classification": (
                "IMPROVED" if delta > 0
                else "WORSENED" if delta < 0
                else "UNCHANGED"
            ),
        })
    return sorted(
        collapsed,
        key=lambda row: (int(row["baseline_exit_time"]), row["canonical_opportunity_id"]),
    )


def _readiness(*, criteria: dict[str, Any], metrics: dict[str, Any],
               integrity_violations: int,
               minimum_sample_requirement: int | None,
               promotion_review_prerequisites_satisfied: bool) -> dict[str, Any]:
    criteria = dict(criteria or {})
    if not criteria and minimum_sample_requirement is None:
        return {
            "state": "SHADOW_VALIDATION_ACTIVE",
            "satisfied": False,
            "unsatisfied_criteria": ["GOVERNED_CRITERIA_NOT_CONFIGURED"],
        }

    min_paired = criteria.get(
        "minimum_paired_sample", minimum_sample_requirement
    )
    min_candidate = criteria.get(
        "minimum_candidate_sample", minimum_sample_requirement
    )
    known_criteria = {
        "minimum_paired_sample",
        "minimum_candidate_sample",
        "candidate_pf_at_least_baseline",
        "max_drawdown_multiple",
        "minimum_mean_paired_delta_r",
        "minimum_observation_window_seconds",
        "require_zero_integrity_violations",
        "require_complete_pairing",
        "confidence_interval",
    }
    unknown = sorted(set(criteria) - known_criteria)
    if unknown:
        return {
            "state": "SHADOW_VALIDATION_ACTIVE",
            "satisfied": False,
            "unsatisfied_criteria": [
                f"UNSUPPORTED_CRITERIA:{name}" for name in unknown
            ],
        }
    if any(value is not None and int(value) < 1
           for value in (min_paired, min_candidate)):
        return {
            "state": "SHADOW_VALIDATION_ACTIVE",
            "satisfied": False,
            "unsatisfied_criteria": ["MINIMUM_SAMPLE_MUST_BE_POSITIVE"],
        }
    sample_checks = []
    if min_paired is not None:
        sample_checks.append(metrics["paired_n"] >= int(min_paired))
    if min_candidate is not None:
        sample_checks.append(metrics["completed_candidate_outcomes"]
                             >= int(min_candidate))
    if sample_checks and not all(sample_checks):
        return {
            "state": "SHADOW_VALIDATION_INSUFFICIENT_DATA",
            "satisfied": False,
            "unsatisfied_criteria": ["MINIMUM_SAMPLE"],
        }

    failures: list[str] = []
    if criteria.get("candidate_pf_at_least_baseline"):
        c_pf = metrics["candidate_profit_factor"]
        b_pf = metrics["baseline_profit_factor"]
        if c_pf is None or b_pf is None or c_pf < b_pf:
            failures.append("CANDIDATE_PF_AT_LEAST_BASELINE")
    if "max_drawdown_multiple" in criteria:
        baseline_dd = metrics["baseline_max_drawdown_r"]
        candidate_dd = metrics["candidate_max_drawdown_r"]
        if candidate_dd > float(criteria["max_drawdown_multiple"]) * baseline_dd:
            failures.append("MAX_DRAWDOWN_MULTIPLE")
    if "minimum_mean_paired_delta_r" in criteria:
        mean_delta = metrics["mean_paired_delta_r"]
        if mean_delta is None or mean_delta < float(
            criteria["minimum_mean_paired_delta_r"]
        ):
            failures.append("MINIMUM_MEAN_PAIRED_DELTA_R")
    if "minimum_observation_window_seconds" in criteria:
        observed = metrics["observation_window_seconds"]
        if observed is None or observed < int(
            criteria["minimum_observation_window_seconds"]
        ):
            failures.append("MINIMUM_OBSERVATION_WINDOW")
    if criteria.get("require_zero_integrity_violations") and integrity_violations:
        failures.append("ZERO_INTEGRITY_VIOLATIONS")
    if criteria.get("require_complete_pairing") and (
        metrics["completed_candidate_outcomes"] != metrics["paired_n"]
    ):
        failures.append("COMPLETE_PAIRING")
    if criteria.get("confidence_interval"):
        failures.append("CONFIDENCE_INTERVAL_UNAVAILABLE")

    if failures:
        return {
            "state": "SHADOW_VALIDATION_ACTIVE",
            "satisfied": False,
            "unsatisfied_criteria": failures,
        }
    state = (
        "READY_FOR_PROMOTION_REVIEW"
        if promotion_review_prerequisites_satisfied
        else "SHADOW_VALIDATED"
    )
    return {"state": state, "satisfied": True, "unsatisfied_criteria": []}


class CandidateMonitor:
    def __init__(self, evaluation: CandidateEvaluation | None = None,
                 candidate_runtime=None, candidate_dir: str | None = None) -> None:
        self._evaluation = evaluation or get_candidate_evaluation()
        self._candidate_runtime = candidate_runtime or get_candidate_runtime()
        self._candidate_dir = candidate_dir

    def report(self, *, promotion_review_prerequisites_satisfied: bool = False
               ) -> dict[str, Any]:
        self._candidate_runtime.ensure_recovered()
        self._evaluation.reconcile()
        pairs = self._evaluation.records()
        raw_events = load_candidate_events(self._candidate_dir)
        registrations = self._candidate_runtime.registrations()
        latest: dict[str, dict[str, Any]] = {}
        opens: dict[str, dict[str, Any]] = {}
        closes: dict[str, dict[str, Any]] = {}
        for event in raw_events:
            rid = str(event.get("candidate_runtime_id") or "")
            if not rid:
                continue
            latest[rid] = event
            if event.get("event_type") == "CANDIDATE_OPEN":
                opens[rid] = event
            elif event.get("event_type") == "CANDIDATE_CLOSE":
                closes[rid] = event

        errors = self._evaluation.integrity_errors()
        reports: list[dict[str, Any]] = []
        for reg in registrations:
            cid, pid, treatment_hash = (
                str(reg.candidate_id), str(reg.policy_id), str(reg.treatment_hash)
            )
            group_pairs = [
                pair for pair in pairs
                if pair.get("candidate_id") == cid
                and pair.get("policy_id") == pid
                and pair.get("treatment_hash") == treatment_hash
            ]
            unit_rows = _collapse_by_canonical(group_pairs)
            baseline_values = [float(row["baseline_r"]) for row in unit_rows]
            candidate_values = [float(row["candidate_r"]) for row in unit_rows]
            deltas = [float(row["paired_delta_r"]) for row in unit_rows]
            ordered_pairs = sorted(
                group_pairs,
                key=lambda row: (
                    int(row.get("baseline_exit_time") or 0),
                    str(row.get("canonical_opportunity_id") or ""),
                    str(row.get("trade_horizon") or ""),
                ),
            )
            relevant_runtime_ids = {
                rid for rid, event in latest.items()
                if event.get("candidate_id") == cid and event.get("policy_id") == pid
            }
            relevant_shadow_ids = {
                str(event.get("shadow_trade_id") or "")
                for rid, event in latest.items() if rid in relevant_runtime_ids
            }
            integrity_errors = [
                error for error in errors
                if error.get("identity") in relevant_runtime_ids
                or error.get("identity") in relevant_shadow_ids
                or not error.get("identity")
                or str(error.get("reason") or "").startswith("EVALUATION_")
            ]
            integrity_errors.extend(
                failure
                for failure in self._candidate_runtime.integrity_failures()
                if failure.get("candidate_id") == cid
                and failure.get("policy_id") == pid
            )
            for rid in relevant_runtime_ids:
                if str(latest[rid].get("treatment_hash") or "") != treatment_hash:
                    integrity_errors.append({
                        "reason": "TREATMENT_HASH_MISMATCH",
                        "identity": rid,
                    })
            active_ids = {
                rid for rid in relevant_runtime_ids
                if str(dict(latest[rid].get("state") or {}).get("state") or "")
                == "ACTIVE"
                and latest[rid].get("event_type") != "CANDIDATE_INVALID"
            }
            observed_canonical = {
                str(opens[rid].get("canonical_opportunity_id") or "")
                for rid in relevant_runtime_ids if rid in opens
            } - {""}
            active_canonical = {
                str(latest[rid].get("canonical_opportunity_id") or "")
                for rid in active_ids
            } - {""}
            completed_canonical = {
                str(closes[rid].get("canonical_opportunity_id") or "")
                for rid in relevant_runtime_ids if rid in closes
            } - {""}
            baseline_pf, baseline_pf_status = _profit_factor(baseline_values)
            candidate_pf, candidate_pf_status = _profit_factor(candidate_values)
            times = [
                int(row.get("baseline_exit_time") or 0)
                for row in unit_rows if int(row.get("baseline_exit_time") or 0) > 0
            ]
            metrics = {
                "observed_candidate_lifecycles": len(observed_canonical),
                "active_candidate_lifecycles": len(active_canonical),
                "completed_candidate_outcomes": len(completed_canonical),
                "paired_n": len(unit_rows),
                "improved_n": sum(
                    row["outcome_classification"] == "IMPROVED"
                    for row in unit_rows
                ),
                "worsened_n": sum(
                    row["outcome_classification"] == "WORSENED"
                    for row in unit_rows
                ),
                "unchanged_n": sum(
                    row["outcome_classification"] == "UNCHANGED"
                    for row in unit_rows
                ),
                "baseline_total_r": sum(baseline_values),
                "candidate_total_r": sum(candidate_values),
                "baseline_expectancy_r": (
                    statistics.fmean(baseline_values) if baseline_values else None
                ),
                "candidate_expectancy_r": (
                    statistics.fmean(candidate_values) if candidate_values else None
                ),
                "mean_paired_delta_r": statistics.fmean(deltas) if deltas else None,
                "median_paired_delta_r": statistics.median(deltas) if deltas else None,
                "baseline_win_rate": (
                    sum(value > 0 for value in baseline_values) / len(baseline_values)
                    if baseline_values else None
                ),
                "candidate_win_rate": (
                    sum(value > 0 for value in candidate_values) / len(candidate_values)
                    if candidate_values else None
                ),
                "baseline_profit_factor": baseline_pf,
                "candidate_profit_factor": candidate_pf,
                "baseline_profit_factor_status": baseline_pf_status,
                "candidate_profit_factor_status": candidate_pf_status,
                "baseline_max_drawdown_r": _max_drawdown(
                    [float(row["baseline_r"]) for row in _collapse_by_canonical(ordered_pairs)]
                ),
                "candidate_max_drawdown_r": _max_drawdown(
                    [float(row["candidate_r"]) for row in _collapse_by_canonical(ordered_pairs)]
                ),
                "observation_window_seconds": max(times) - min(times) if len(times) > 1 else (
                    0 if times else None
                ),
                "integrity_violations": len(integrity_errors),
            }
            criteria = dict(reg.readiness_criteria or {})
            minimum_sample = reg.minimum_sample_requirement
            if not treatment_hash or integrity_errors:
                readiness = {
                    "state": "SHADOW_VALIDATION_INVALID",
                    "satisfied": False,
                    "unsatisfied_criteria": (
                        ["VALID_TREATMENT_HASH"] if not treatment_hash
                        else ["INTEGRITY_VIOLATIONS"]
                    ),
                }
            else:
                readiness = _readiness(
                    criteria=criteria,
                    metrics=metrics,
                    integrity_violations=len(integrity_errors),
                    minimum_sample_requirement=minimum_sample,
                    promotion_review_prerequisites_satisfied=(
                        promotion_review_prerequisites_satisfied
                    ),
                )
            reports.append({
                "candidate_id": cid,
                "policy_id": pid,
                "treatment_hash": treatment_hash,
                "independent_unit": "canonical_opportunity_id",
                "metrics": metrics,
                "readiness": readiness,
                "readiness_criteria": criteria,
            })
        return {"schema_version": "shadow_candidate_monitoring_v1", "reports": reports}


def monitor_candidates(*, promotion_review_prerequisites_satisfied: bool = False,
                       evaluation: CandidateEvaluation | None = None) -> dict[str, Any]:
    return CandidateMonitor(evaluation=evaluation).report(
        promotion_review_prerequisites_satisfied=(
            promotion_review_prerequisites_satisfied
        )
    )


__all__ = ["CandidateMonitor", "monitor_candidates"]
