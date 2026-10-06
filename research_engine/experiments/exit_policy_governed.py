"""Governed HD09 counterfactual research for EX1, EX2, and EX9.

This module consumes, but never changes, the governed exit-bar-path, baseline
reproduction, and candidate replay populations.  All scientific choices are
read directly from ``HD09_ADJUDICATED_CONTRACT``.
"""
from __future__ import annotations

import json
import math
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from research_engine.control_plane.evidence_provenance import evidence_digest
from research_engine.control_plane.evidence_readiness import (
    EvidenceRequirement,
    evaluate_evidence_readiness,
)
from research_engine.control_plane.exit_bar_path import (
    SCHEMA_VERSION as PATH_SCHEMA_VERSION,
    GovernedExitBarPathEvidence,
    LifecyclePathSource,
    build_exit_bar_path_v1,
)
from research_engine.control_plane.exit_baseline_replay import (
    REPRODUCTION_SCHEMA_VERSION,
    GovernedBaselineReproductionPopulation,
    build_baseline_reproduction_population,
)
from research_engine.control_plane.exit_candidate_replay import (
    CANDIDATE_POLICY_IDS,
    CANDIDATE_REPLAY_SCHEMA_VERSION,
    CandidateReplayPopulation,
    candidate_replay_digest,
    build_candidate_replay_population,
)
from research_engine.registry.exit_policy_adjudication import (
    CANDIDATE_POLICIES_V1,
    CLUSTERED_INFERENCE,
    COMMON_ANALYTICAL_CONTRACT,
    HD09_ADJUDICATED_CONTRACT,
    HD09_ADJUDICATION_VERSION,
    SAMPLE_AND_READINESS_CONTRACT,
)

# Governed evaluator semantic identity; see component_reward for the contract.
REPORT_SCHEMA_VERSION = "hd09_governed_exit_research_v1"
INFERENCE_SCHEMA_VERSION = "hd09_clustered_cr0_normal_v1"
EVALUATOR_SEMANTIC_VERSIONS = {
    "run_ex2": "ex2_snapshot_scoped_population_closure_v2",
}
EVALUATOR_REPORT_SCHEMA_VERSIONS = {
    "run_ex2": {
        "REPORT_SCHEMA_VERSION": REPORT_SCHEMA_VERSION,
        "INFERENCE_SCHEMA_VERSION": INFERENCE_SCHEMA_VERSION,
    },
}
EVALUATOR_GOVERNANCE_CONTRACT_VERSIONS = {
    "run_ex2": {"HD09_ADJUDICATION_VERSION": HD09_ADJUDICATION_VERSION},
}
TARGETS = ("EX1", "EX2", "EX9")
TRAILING_POLICY_IDS = tuple(
    item["policy_id"] for item in CANDIDATE_POLICIES_V1
    if item["policy_type"] == "TRAILING"
)
ENDPOINT_A = "paired_timeout_indicator_change"
ENDPOINT_B = "baseline_timeout_loss_converted_positive"
_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_SHADOW_DIR = _ROOT / "_exit_path_audit" / "shadow_runtime_v1"
_DEFAULT_EVENT_DIR = _ROOT / "_exit_path_audit" / "events_v1"
_REPORTS_DIR = _ROOT / "analysis" / "reports"


class GovernedAnalysisError(ValueError):
    """An authority, integrity, family, or estimability failure."""


def _finite(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GovernedAnalysisError("required analytical value is not numeric")
    result = float(value)
    if not math.isfinite(result):
        raise GovernedAnalysisError("required analytical value is not finite")
    return result


def _normal_two_sided_p(estimate: float, standard_error: float) -> float:
    estimate, standard_error = _finite(estimate), _finite(standard_error)
    if standard_error < 0:
        raise GovernedAnalysisError("negative standard error")
    if standard_error == 0:
        return 1.0 if estimate == 0 else 0.0
    return math.erfc(abs(estimate / standard_error) / math.sqrt(2.0))


def holm_adjust(
    tests: Sequence[tuple[str, float]], required_order: Sequence[str],
) -> dict[str, float]:
    """Deterministic Holm adjustment with frozen-order tie breaking."""
    required = tuple(required_order)
    if len(tests) != len(required) or tuple(item[0] for item in tests) != required:
        raise GovernedAnalysisError("Holm family is incomplete or not in frozen order")
    if len(set(required)) != len(required):
        raise GovernedAnalysisError("Holm family contains duplicate test identities")
    order = {test_id: index for index, test_id in enumerate(required)}
    checked = [(test_id, _finite(p)) for test_id, p in tests]
    if any(p < 0 or p > 1 for _, p in checked):
        raise GovernedAnalysisError("Holm family contains an invalid p-value")
    ranked = sorted(checked, key=lambda item: (item[1], order[item[0]]))
    adjusted: dict[str, float] = {}
    running = 0.0
    family_size = len(ranked)
    for rank, (test_id, p_value) in enumerate(ranked):
        running = max(running, min(1.0, (family_size - rank) * p_value))
        adjusted[test_id] = running
    return {test_id: adjusted[test_id] for test_id in required}


def clustered_cr0_cell_means(
    rows: Iterable[Mapping[str, Any]], required_test_ids: Sequence[str],
) -> list[dict[str, Any]]:
    """Fit HD09's weighted cell means and canonical-opportunity CR0 covariance."""
    required = tuple(required_test_ids)
    grouped: dict[str, list[Mapping[str, Any]]] = {test_id: [] for test_id in required}
    seen: set[tuple[str, tuple[str, str, str]]] = set()
    for row in rows:
        test_id = str(row.get("test_id", ""))
        if test_id not in grouped:
            raise GovernedAnalysisError(f"unknown required test identity: {test_id!r}")
        identity_raw = row.get("lifecycle_identity")
        identity = tuple(identity_raw) if isinstance(identity_raw, (tuple, list)) else ()
        if len(identity) != 3 or not all(isinstance(value, str) and value for value in identity):
            raise GovernedAnalysisError("invalid lifecycle pairing identity")
        cluster = str(row.get("canonical_opportunity_id", ""))
        if not cluster or identity[1] != cluster:
            raise GovernedAnalysisError("cluster identity is not canonical_opportunity_id")
        key = (test_id, identity)  # account fanout/duplicate grain cannot multiply rows
        if key in seen:
            raise GovernedAnalysisError("duplicate lifecycle x candidate analytical row")
        seen.add(key)
        _finite(row.get("value"))
        grouped[test_id].append(row)

    results: list[dict[str, Any]] = []
    for test_id in required:
        cell = sorted(
            grouped[test_id],
            key=lambda row: (
                str(row["canonical_opportunity_id"]),
                tuple(row["lifecycle_identity"]),
            ),
        )
        if not cell:
            raise GovernedAnalysisError(f"required test {test_id} has no eligible rows")
        horizons_by_cluster = Counter(str(row["canonical_opportunity_id"]) for row in cell)
        if len(horizons_by_cluster) < 2:
            raise GovernedAnalysisError(f"required test {test_id} has fewer than two clusters")
        weighted = [
            (row, 1.0 / horizons_by_cluster[str(row["canonical_opportunity_id"])])
            for row in cell
        ]
        bread = math.fsum(weight for _, weight in weighted)
        if not math.isfinite(bread) or bread <= 0:
            raise GovernedAnalysisError(f"required test {test_id} has singular bread")
        estimate = math.fsum(
            weight * _finite(row["value"]) for row, weight in weighted
        ) / bread
        scores: dict[str, float] = defaultdict(float)
        for row, weight in weighted:
            scores[str(row["canonical_opportunity_id"])] += (
                weight * (_finite(row["value"]) - estimate)
            )
        variance = math.fsum(score * score for score in scores.values()) / (bread * bread)
        if not math.isfinite(variance) or variance < 0:
            raise GovernedAnalysisError(f"required test {test_id} has invalid CR0 covariance")
        standard_error = math.sqrt(variance)
        p_value = _normal_two_sided_p(estimate, standard_error)
        critical = float(CLUSTERED_INFERENCE["critical_value"])
        results.append({
            "test_identity": test_id,
            "eligible_paired_lifecycle_count": len(cell),
            "distinct_canonical_opportunity_count": len(horizons_by_cluster),
            "weighted_effect_estimate": estimate,
            "standard_error": standard_error,
            "confidence_interval_95": [
                estimate - critical * standard_error,
                estimate + critical * standard_error,
            ],
            "raw_two_sided_p_value": p_value,
            "cluster_identity": "canonical_opportunity_id",
            "horizon_weighting": "w_oh = 1 / k_o",
        })
    return results


def _validate_foundations(
    candidate: CandidateReplayPopulation,
    reproduction: GovernedBaselineReproductionPopulation,
    path: GovernedExitBarPathEvidence,
) -> None:
    if HD09_ADJUDICATED_CONTRACT.get("version") != HD09_ADJUDICATION_VERSION:
        raise GovernedAnalysisError("invalid HD09 authority")
    if path.schema_version != PATH_SCHEMA_VERSION:
        raise GovernedAnalysisError("invalid exit-bar-path schema")
    if reproduction.schema_version != REPRODUCTION_SCHEMA_VERSION:
        raise GovernedAnalysisError("invalid baseline reproduction schema")
    if candidate.schema_version != CANDIDATE_REPLAY_SCHEMA_VERSION:
        raise GovernedAnalysisError("invalid candidate replay schema")
    for provenance, label in (
        (path.provenance, "exit-bar-path"),
        (reproduction.provenance, "baseline reproduction"),
        (candidate.provenance, "candidate replay"),
    ):
        material = dict(provenance)
        supplied = material.pop("digest", None)
        if supplied != evidence_digest((material,)):
            raise GovernedAnalysisError(f"invalid {label} provenance digest")
    if candidate.provenance.get("source_exit_bar_path_provenance_digest") != path.provenance["digest"]:
        raise GovernedAnalysisError("candidate/path provenance mismatch")
    if candidate.provenance.get("source_baseline_reproduction_provenance_digest") != reproduction.provenance["digest"]:
        raise GovernedAnalysisError("candidate/baseline provenance mismatch")
    if candidate.provenance.get("candidate_rows_digest") != evidence_digest(
        item.analytical_record() for item in candidate.records
    ):
        raise GovernedAnalysisError("candidate analytical population digest mismatch")
    if candidate.provenance.get("candidate_exclusion_digest") != evidence_digest(
        item.record() for item in candidate.exclusions
    ):
        raise GovernedAnalysisError("candidate exclusion digest mismatch")
    if any(candidate_replay_digest(item) != item.candidate_replay_digest for item in candidate.records):
        raise GovernedAnalysisError("candidate row integrity failure")
    expected = set(CANDIDATE_POLICY_IDS)
    if set(candidate.summary.rows_by_policy) != expected:
        raise GovernedAnalysisError("candidate policy population is incomplete")
    by_identity = Counter((item.lifecycle_identity, item.candidate_policy_id) for item in candidate.records)
    if any(count != 1 for count in by_identity.values()):
        raise GovernedAnalysisError("candidate population violates row grain")


def _baseline_index(
    reproduction: GovernedBaselineReproductionPopulation,
) -> dict[tuple[str, str, str], Any]:
    counts = Counter(item.lifecycle_identity for item in reproduction.records)
    if any(count != 1 for count in counts.values()):
        raise GovernedAnalysisError("baseline reproduction pairing identity is not unique")
    return {item.lifecycle_identity: item.replay for item in reproduction.records}


def _readiness(
    question_id: str,
    path: GovernedExitBarPathEvidence,
    candidate: CandidateReplayPopulation,
    tests: Sequence[Mapping[str, Any]],
    *,
    endpoint_b_opportunities: int | None = None,
) -> dict[str, Any]:
    contract = SAMPLE_AND_READINESS_CONTRACT[question_id]
    coverage = evaluate_evidence_readiness(
        total_count=path.summary.total_completed_lifecycles,
        valid_count=candidate.summary.candidate_eligible_lifecycles,
        distinct_valid_count=candidate.summary.distinct_eligible_opportunities,
        requirement=EvidenceRequirement(0, 0, SAMPLE_AND_READINESS_CONTRACT["common_path_coverage"]),
    )
    minimum_rows = min(int(test["eligible_paired_lifecycle_count"]) for test in tests)
    minimum_clusters = min(int(test["distinct_canonical_opportunity_count"]) for test in tests)
    sample = evaluate_evidence_readiness(
        total_count=max(1, minimum_rows),
        valid_count=minimum_rows,
        distinct_valid_count=minimum_clusters,
        requirement=EvidenceRequirement(
            contract["minimum_paired_lifecycles"],
            contract["minimum_distinct_opportunities"],
            0.0,
        ),
    )
    blockers = [*coverage.blockers, *sample.blockers]
    endpoint_b_ok = True
    if endpoint_b_opportunities is not None:
        required = int(contract["endpoint_b_minimum_distinct_opportunities"])
        endpoint_b_ok = endpoint_b_opportunities >= required
        if not endpoint_b_ok:
            blockers.append("ENDPOINT_B_DISTINCT_COUNT_BELOW_REQUIRED")
    return {
        "state": "READY" if not blockers else "WAITING_DATA",
        "blockers": blockers,
        "governed_coverage": coverage.current_coverage,
        "required_governed_coverage": coverage.required_coverage,
        "minimum_eligible_paired_lifecycles": minimum_rows,
        "required_eligible_paired_lifecycles": sample.required_valid_count,
        "minimum_distinct_canonical_opportunities": minimum_clusters,
        "required_distinct_canonical_opportunities": sample.required_distinct_count,
        "endpoint_b_distinct_opportunities": endpoint_b_opportunities,
        "endpoint_b_gate_satisfied": endpoint_b_ok,
    }


def _inference_configuration() -> dict[str, Any]:
    return {
        "schema_version": INFERENCE_SCHEMA_VERSION,
        "model": CLUSTERED_INFERENCE["model"],
        "covariance": CLUSTERED_INFERENCE["covariance"],
        "finite_sample_correction": CLUSTERED_INFERENCE["finite_sample_correction"],
        "reference_distribution": CLUSTERED_INFERENCE["reference_distribution"],
        "confidence_level": CLUSTERED_INFERENCE["confidence_level"],
        "critical_value": CLUSTERED_INFERENCE["critical_value"],
        "alpha": CLUSTERED_INFERENCE["alpha"],
        "test_sidedness": CLUSTERED_INFERENCE["test_sidedness"],
        "degenerate_se_rule": CLUSTERED_INFERENCE["degenerate_se_rule"],
    }


def _make_report(
    question_id: str,
    tests: list[dict[str, Any]],
    family_order: Sequence[str],
    readiness: dict[str, Any],
    candidate: CandidateReplayPopulation,
    reproduction: GovernedBaselineReproductionPopulation,
    path: GovernedExitBarPathEvidence,
) -> dict[str, Any]:
    analytical_material = {
        "question_id": question_id,
        "hd09_adjudication_version": HD09_ADJUDICATION_VERSION,
        "path_population_digest": path.provenance["digest"],
        "baseline_reproduction_population_digest": reproduction.provenance["digest"],
        "candidate_replay_population_digest": candidate.provenance["digest"],
        "candidate_policy_identities": [dict(item) for item in CANDIDATE_POLICIES_V1],
        "analytical_population": tests,
        "horizon_weighting_contract": COMMON_ANALYTICAL_CONTRACT["horizon_weight_definition"],
        "cluster_identity": COMMON_ANALYTICAL_CONTRACT["cluster_identity"],
        "inference_configuration": _inference_configuration(),
        "holm_family_order": list(family_order),
        "exclusions": {
            "path": path.summary.exclusions_by_reason,
            "baseline_reproduction": {
                "count": len(reproduction.exclusions),
                "digest": reproduction.provenance.get("reproduction_exclusion_digest"),
            },
            "candidate": {
                "count": len(candidate.exclusions),
                "by_reason": candidate.summary.exclusions_by_reason,
                "digest": candidate.provenance["candidate_exclusion_digest"],
            },
        },
        "readiness": readiness,
    }
    analytical_digest = evidence_digest((analytical_material,))
    status = "COMPLETE" if readiness["state"] == "READY" else "WAITING_DATA"
    supported = sum(1 for test in tests if test["interpretation"].startswith("SUPPORTED"))
    overall = {
        "finding": (
            f"Governed HD09 evaluation complete: {supported}/{len(tests)} required tests supported."
            if status == "COMPLETE" else
            f"Governed machinery valid; evidence gates unmet: {', '.join(readiness['blockers'])}."
        ),
        "experiment": "HD09 governed exit-policy counterfactual",
        "baseline": "SHADOW_BASELINE_V1",
        "variants": list(family_order),
        "sample_size": min(test["eligible_paired_lifecycle_count"] for test in tests),
        "distinct_canonical_opportunities": min(
            test["distinct_canonical_opportunity_count"] for test in tests
        ),
        "tests": tests,
    }
    report = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "question_id": question_id,
        "status": status,
        "epoch": "CURRENT",
        "overall": overall,
        "dataset": {
            "source": "exit_candidate_replay_v1",
            "sample_size": overall["sample_size"],
            "independent_observations": overall["distinct_canonical_opportunities"],
        },
        "fingerprint": {
            "epoch": "CURRENT",
            "source": "exit_candidate_replay_v1",
            "records_used": sum(test["eligible_paired_lifecycle_count"] for test in tests),
            "records_excluded": len(candidate.exclusions),
            "analytical_digest": analytical_digest,
        },
        "confidence": "HIGH" if status == "COMPLETE" else "INSUFFICIENT_DATA",
        "recommendation": "FINDING: governed simulated-policy evaluation" if status == "COMPLETE" else "WAIT",
        "assumptions": [HD09_ADJUDICATED_CONTRACT["claim_boundary"]],
        "warnings": ["Research-only simulated policy outcomes; no production application authority."],
        "provenance": {**analytical_material, "analytical_digest": analytical_digest},
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    report_material = dict(report)
    report_material.pop("generated", None)
    report["provenance"]["report_digest"] = evidence_digest((report_material,))
    return report


def _apply_holm_and_interpret(
    tests: list[dict[str, Any]], family_order: Sequence[str], question_id: str,
) -> None:
    adjusted = holm_adjust(
        [(test["test_identity"], test["raw_two_sided_p_value"]) for test in tests],
        family_order,
    )
    alpha = float(CLUSTERED_INFERENCE["alpha"])
    for test in tests:
        test["holm_adjusted_p_value"] = adjusted[test["test_identity"]]
        estimate = test["weighted_effect_estimate"]
        lower = test["confidence_interval_95"][0]
        if question_id in {"EX1", "EX2"}:
            supported = estimate > 0 and adjusted[test["test_identity"]] <= alpha and lower > 0
            test["interpretation"] = "SUPPORTED_IMPROVEMENT" if supported else "NOT_SUPPORTED"
        elif test["endpoint"] == ENDPOINT_A:
            supported = estimate < 0 and adjusted[test["test_identity"]] <= alpha
            test["interpretation"] = "SUPPORTED_REDUCTION" if supported else "NOT_SUPPORTED"
        else:
            supported = estimate > 0 and adjusted[test["test_identity"]] <= alpha
            test["interpretation"] = "SUPPORTED_CONVERSION" if supported else "NOT_SUPPORTED"


def analyse_ex1(candidate, reproduction, path, *, _foundations_validated: bool = False) -> dict[str, Any]:
    if not _foundations_validated:
        _validate_foundations(candidate, reproduction, path)
    baseline = _baseline_index(reproduction)
    family = tuple(f"EX1:{policy_id}" for policy_id in CANDIDATE_POLICY_IDS)
    rows = [{
        "test_id": f"EX1:{item.candidate_policy_id}",
        "lifecycle_identity": item.lifecycle_identity,
        "canonical_opportunity_id": item.canonical_opportunity_id,
        "value": item.candidate_r - baseline[item.lifecycle_identity].pnl_r_multiple,
    } for item in candidate.records]
    tests = clustered_cr0_cell_means(rows, family)
    for test, policy_id in zip(tests, CANDIDATE_POLICY_IDS):
        test["policy_identity"] = policy_id
        test["endpoint"] = "candidate_r_minus_reproduced_baseline_r"
    _apply_holm_and_interpret(tests, family, "EX1")
    ready = _readiness("EX1", path, candidate, tests)
    return _make_report("EX1", tests, family, ready, candidate, reproduction, path)


def analyse_ex2(candidate, reproduction, path, *, _foundations_validated: bool = False) -> dict[str, Any]:
    if not _foundations_validated:
        _validate_foundations(candidate, reproduction, path)
    baseline = _baseline_index(reproduction)
    family = tuple(f"EX2:{policy_id}" for policy_id in TRAILING_POLICY_IDS)
    rows = []
    for item in candidate.records:
        if item.candidate_policy_id not in TRAILING_POLICY_IDS:
            continue
        mfe = _finite(item.baseline_window_mfe_r)
        if mfe <= 0:
            continue
        rows.append({
            "test_id": f"EX2:{item.candidate_policy_id}",
            "lifecycle_identity": item.lifecycle_identity,
            "canonical_opportunity_id": item.canonical_opportunity_id,
            "value": (item.candidate_r / mfe) - (
                baseline[item.lifecycle_identity].pnl_r_multiple / mfe
            ),
        })
    tests = clustered_cr0_cell_means(rows, family)
    for test, policy_id in zip(tests, TRAILING_POLICY_IDS):
        test["policy_identity"] = policy_id
        test["endpoint"] = "candidate_retention_minus_baseline_retention"
        test["eligibility"] = "finite baseline_window_mfe_r > 0"
    _apply_holm_and_interpret(tests, family, "EX2")
    ready = _readiness("EX2", path, candidate, tests)
    return _make_report("EX2", tests, family, ready, candidate, reproduction, path)


def analyse_ex9(candidate, reproduction, path, *, _foundations_validated: bool = False) -> dict[str, Any]:
    if not _foundations_validated:
        _validate_foundations(candidate, reproduction, path)
    baseline = _baseline_index(reproduction)
    family_a = tuple(f"EX9:{ENDPOINT_A}:{policy_id}" for policy_id in CANDIDATE_POLICY_IDS)
    family_b = tuple(f"EX9:{ENDPOINT_B}:{policy_id}" for policy_id in CANDIDATE_POLICY_IDS)
    family = family_a + family_b
    rows = []
    endpoint_b_opportunities: set[str] = set()
    for item in candidate.records:
        base = baseline[item.lifecycle_identity]
        rows.append({
            "test_id": f"EX9:{ENDPOINT_A}:{item.candidate_policy_id}",
            "lifecycle_identity": item.lifecycle_identity,
            "canonical_opportunity_id": item.canonical_opportunity_id,
            "value": int(item.exit_reason in {"timeout", "time_cap"}) - int(base.exit_reason == "timeout"),
        })
        if base.exit_reason == "timeout" and base.pnl_r_multiple < 0:
            endpoint_b_opportunities.add(item.canonical_opportunity_id)
            rows.append({
                "test_id": f"EX9:{ENDPOINT_B}:{item.candidate_policy_id}",
                "lifecycle_identity": item.lifecycle_identity,
                "canonical_opportunity_id": item.canonical_opportunity_id,
                "value": int(item.candidate_r > 0),
            })
    tests = clustered_cr0_cell_means(rows, family)
    for index, test in enumerate(tests):
        test["endpoint"] = ENDPOINT_A if index < len(CANDIDATE_POLICY_IDS) else ENDPOINT_B
        test["policy_identity"] = CANDIDATE_POLICY_IDS[index % len(CANDIDATE_POLICY_IDS)]
    _apply_holm_and_interpret(tests, family, "EX9")
    ready = _readiness(
        "EX9", path, candidate, tests[:len(CANDIDATE_POLICY_IDS)],
        endpoint_b_opportunities=len(endpoint_b_opportunities),
    )
    return _make_report("EX9", tests, family, ready, candidate, reproduction, path)


def _records(path: Path) -> Iterable[dict[str, Any]]:
    for source_file in sorted(path.rglob("*.jsonl")):
        with source_file.open("r", encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    yield json.loads(line)


def load_governed_foundations(
    shadow_dir: str | Path = _DEFAULT_SHADOW_DIR,
    event_dir: str | Path = _DEFAULT_EVENT_DIR,
    governed_records: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[GovernedExitBarPathEvidence, GovernedBaselineReproductionPopulation, CandidateReplayPopulation]:
    """Rebuild the three governed foundations from immutable source evidence."""
    from research_engine.data_access.shadow_runtime_ingestion import reconstruct_completed_shadow_trades

    shadow_records = list(_records(Path(shadow_dir)))
    open_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    close_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for record in shadow_records:
        key = (
            str(record.get("shadow_trade_id", "")),
            str(record.get("canonical_opportunity_id", "")),
            str(record.get("horizon", "")),
        )
        if record.get("event_type") == "OPEN":
            open_by_key.setdefault(key, record)
        elif record.get("event_type") == "CLOSE":
            close_by_key.setdefault(key, record)
    completed = reconstruct_completed_shadow_trades(shadow_records)
    if governed_records is not None:
        from research_engine.control_plane.stage4_impl_population2 import enforce_exact_population
        governed = enforce_exact_population("EX2", governed_records)
        roster = set()
        for row in governed:
            identity = row.get("identity") if isinstance(row.get("identity"), Mapping) else {}
            roster.add((
                str(row.get("shadow_trade_id") or identity.get("shadow_trade_id") or ""),
                str(row.get("canonical_opportunity_id") or identity.get("canonical_opportunity_id") or ""),
                str(row.get("trade_horizon") or row.get("horizon") or identity.get("trade_horizon") or ""),
            ))
        completed = [item for item in completed if (
            str((item.get("identity") or {}).get("shadow_trade_id", "")),
            str((item.get("identity") or {}).get("canonical_opportunity_id", "")),
            str((item.get("identity") or {}).get("trade_horizon", "")),
        ) in roster]
        if len(completed) != len(governed):
            raise GovernedAnalysisError(
                f"EX2_RUNNER_POPULATION_MISMATCH:{len(completed)}!={len(governed)}")
    candles_by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in _records(Path(event_dir)):
        if (
            record.get("type") == "CANDLE" and record.get("source") == "mt5_data"
            and record.get("schema_version") == "events_v1" and record.get("timeframe") == "M5"
        ):
            candles_by_symbol[str(record.get("symbol", "")).upper()].append(record)
    for values in candles_by_symbol.values():
        values.sort(key=lambda item: int(item["payload"]["ts"]))
    times = {
        symbol: [int(item["payload"]["ts"]) for item in values]
        for symbol, values in candles_by_symbol.items()
    }
    sources = []
    for lifecycle in completed:
        identity = lifecycle.get("identity") or {}
        key = (
            str(identity.get("shadow_trade_id", "")),
            str(identity.get("canonical_opportunity_id", "")),
            str(identity.get("trade_horizon", "")),
        )
        opened, closed = open_by_key.get(key), close_by_key.get(key)
        if opened is None or closed is None:
            raise GovernedAnalysisError(f"completed lifecycle lacks canonical source events: {key!r}")
        symbol = str(opened["symbol"]).upper()
        values, stamps = candles_by_symbol.get(symbol, []), times.get(symbol, [])
        entry_ms, exit_ms = int(opened["entry_market_time"]) * 1000, int(closed["exit_market_time"]) * 1000
        subset = values[bisect_right(stamps, entry_ms):bisect_right(stamps, exit_ms)]
        sources.append(LifecyclePathSource(opened, closed, tuple(subset)))
    path = build_exit_bar_path_v1(sources)
    reproduction = build_baseline_reproduction_population(path)
    candidate = build_candidate_replay_population(path, reproduction)
    return path, reproduction, candidate


def run_governed_exit_research(*, persist: bool = False) -> dict[str, dict[str, Any]]:
    path, reproduction, candidate = load_governed_foundations()
    _validate_foundations(candidate, reproduction, path)
    reports = {
        "EX1": analyse_ex1(candidate, reproduction, path, _foundations_validated=True),
        "EX2": analyse_ex2(candidate, reproduction, path, _foundations_validated=True),
        "EX9": analyse_ex9(candidate, reproduction, path, _foundations_validated=True),
    }
    if persist:
        _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        for question_id, report in reports.items():
            filename = HD09_ADJUDICATED_CONTRACT[question_id.lower()]["report_filename"]
            (_REPORTS_DIR / filename).write_text(
                json.dumps(report, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8",
            )
    return reports


def _governed_missing_evidence_report(question_id: str, evidence: Any) -> dict[str, Any]:
    """Fail closed when the governed snapshot cannot bind the M5 exit path.

    The HD09 exit evaluator requires the ordered ``events_v1`` M5 OHLC candle
    path from entry to exit.  The common investigation snapshot does not bind
    that dataset, so the rebuilt foundations carry an explicit observation gap.
    Surfacing it here keeps the runner snapshot-bound instead of reopening the
    filesystem to find the historical candles.
    """
    missing = list(evidence.missing_evidence)
    provenance = {
        "question_id": question_id,
        "completed_lifecycles": evidence.completed_lifecycles,
        "eligible_path_lifecycles": evidence.eligible_path_lifecycles,
        "missing_evidence": missing,
    }
    report = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "question_id": question_id,
        "status": "INSUFFICIENT_DATA",
        "epoch": "CURRENT",
        "scientific_state": "INSUFFICIENT_GOVERNED_EVIDENCE",
        "overall": {
            "finding": (
                "Governed snapshot does not bind the ordered M5 OHLC exit path "
                "required by the HD09 contract; exit policy cannot be evaluated"),
            "sample_size": evidence.completed_lifecycles,
            "observation_gap": "; ".join(missing),
        },
        "failure_reason": "GOVERNED_EXIT_EVIDENCE_INCOMPLETE",
        "missing_evidence": missing,
        "confidence": "NOT_ESTIMABLE",
        "dataset": {
            "source": "governed_exit_evidence",
            "sample_size": evidence.completed_lifecycles,
        },
        "fingerprint": {"epoch": "CURRENT", "source": "governed_exit_evidence"},
        "provenance": provenance,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    material = dict(report)
    material.pop("generated", None)
    report["provenance"]["report_digest"] = evidence_digest((material,))
    return report


def _governed_exit_result(question_id: str, evidence: Any) -> dict[str, Any]:
    """Run an HD09 exit evaluator from snapshot-bound governed foundations."""
    if evidence.missing_evidence:
        return _governed_missing_evidence_report(question_id, evidence)
    if question_id == "EX1":
        return analyse_ex1(evidence.candidate, evidence.reproduction, evidence.path)
    if question_id == "EX2":
        return analyse_ex2(evidence.candidate, evidence.reproduction, evidence.path)
    if question_id == "EX9":
        return analyse_ex9(evidence.candidate, evidence.reproduction, evidence.path)
    raise GovernedAnalysisError("unknown governed exit question")


def run_ex1(*, governed_exit_evidence=None) -> dict[str, Any]:
    if governed_exit_evidence is not None:
        return _governed_exit_result("EX1", governed_exit_evidence)
    path, reproduction, candidate = load_governed_foundations()
    return analyse_ex1(candidate, reproduction, path)


def run_ex2(*, governed_records=None, governed_exit_evidence=None) -> dict[str, Any]:
    # EX2 is a permanently closed historical question. CURRENT snapshot rows
    # are not a replacement roster and must not be forced to equal the frozen
    # historical denominator.
    if governed_exit_evidence is not None:
        return _governed_exit_result("EX2", governed_exit_evidence)
    from research_engine.control_plane.stage4_ex2_l7_blocker_adjudication import (
        EX2_POPULATION, adjudicate_ex2,
    )
    audit = adjudicate_ex2()
    if audit["historically_unobserved"]:
        provenance = {
            "question_id": "EX2",
            "runner_analytical_population": EX2_POPULATION,
            "population_authority": "HISTORICAL_HD09_EX2_ROSTER",
            "population_version": "stage4_impl_repairs_v2",
            "current_snapshot_rows_not_substituted": len(governed_records or ()),
            "observation_gap_adjudication": audit,
            "readiness": {
                "state": "HISTORICALLY_UNANSWERABLE",
                "required_governed_coverage": 1.0,
                "historical_governed_coverage": audit["exact_authoritative_match"] / EX2_POPULATION,
                "remaining_path_observations": audit["historically_unobserved"],
            },
        }
        provenance["analytical_digest"] = evidence_digest((provenance,))
        report = {
            "question_id": "EX2", "report_schema_version": REPORT_SCHEMA_VERSION,
            "status": "BLOCKED", "epoch": "HISTORICAL",
            "scientific_state": "HISTORICALLY_UNANSWERABLE",
            "overall": {
                "finding": "Contract-required ordered M5 OHLC exit paths were not historically retained for every governed lifecycle",
                "sample_size": EX2_POPULATION,
                "observation_gap": "ordered events_v1 M5 OHLC path from entry through exit",
            },
            "failure_reason": "EX2_HISTORICAL_M5_PATHS_UNRECOVERABLE",
            "missing_evidence": ["2844 governed lifecycle M5 OHLC paths were not retained"],
            "confidence": "NOT_ESTIMABLE",
            "dataset": {"source": "frozen historical governed EX2 lifecycle roster", "sample_size": EX2_POPULATION},
            "fingerprint": {"analytical_digest": provenance["analytical_digest"], "epoch": "HISTORICAL"},
            "provenance": provenance,
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        material = dict(report)
        material.pop("generated", None)
        report["provenance"]["report_digest"] = evidence_digest((material,))
        return report
    if governed_records is None:
        raise GovernedAnalysisError("EX2_GOVERNED_POPULATION_REQUIRED")
    from research_engine.control_plane.stage4_impl_population2 import enforce_exact_population
    governed = enforce_exact_population("EX2", governed_records)
    path, reproduction, candidate = load_governed_foundations(governed_records=governed)
    report = analyse_ex2(candidate, reproduction, path)
    report.setdefault("provenance", {})["runner_analytical_population"] = len(governed_records)
    return report


def run_ex9(*, governed_exit_evidence=None) -> dict[str, Any]:
    if governed_exit_evidence is not None:
        return _governed_exit_result("EX9", governed_exit_evidence)
    path, reproduction, candidate = load_governed_foundations()
    return analyse_ex9(candidate, reproduction, path)


def validate_governed_exit_report(report: Mapping[str, Any], question_id: str) -> tuple[bool, str]:
    """Validate a persisted CURRENT HD09 report without trusting its status."""
    if question_id not in TARGETS or report.get("question_id") != question_id:
        return False, "governed exit report identity mismatch"
    if report.get("report_schema_version") != REPORT_SCHEMA_VERSION:
        return False, "governed exit report schema mismatch"
    provenance = report.get("provenance")
    if not isinstance(provenance, Mapping):
        return False, "governed exit report provenance missing"
    audit = provenance.get("observation_gap_adjudication")
    if question_id == "EX2" and audit is not None:
        if report.get("status") != "BLOCKED" or report.get("scientific_state") != "HISTORICALLY_UNANSWERABLE":
            return False, "EX2 observation-gap state invalid"
        if not isinstance(audit, Mapping) or audit.get("classification") != "HISTORICAL_OBSERVATION_GAP":
            return False, "EX2 observation-gap adjudication invalid"
        keys = ("exact_authoritative_match", "historically_unobserved", "ambiguous", "unexplained")
        if int(audit.get("governed_population", -1)) != sum(int(audit.get(key, -1)) for key in keys):
            return False, "EX2 observation-gap accounting does not conserve"
        if audit.get("implementation_resolvable_missing") != 0:
            return False, "EX2 still contains an implementation-resolvable population"
        return True, "valid governed EX2 historical observation-gap report"
    required_family = (
        tuple(f"EX1:{item}" for item in CANDIDATE_POLICY_IDS) if question_id == "EX1" else
        tuple(f"EX2:{item}" for item in TRAILING_POLICY_IDS) if question_id == "EX2" else
        tuple(f"EX9:{ENDPOINT_A}:{item}" for item in CANDIDATE_POLICY_IDS)
        + tuple(f"EX9:{ENDPOINT_B}:{item}" for item in CANDIDATE_POLICY_IDS)
    )
    if tuple(provenance.get("holm_family_order", ())) != required_family:
        return False, "governed exit Holm family is incomplete or reordered"
    tests = report.get("overall", {}).get("tests", []) if isinstance(report.get("overall"), Mapping) else []
    if tuple(test.get("test_identity") for test in tests if isinstance(test, Mapping)) != required_family:
        return False, "governed exit required tests are incomplete"
    if any(not all(key in test for key in (
        "weighted_effect_estimate", "standard_error", "confidence_interval_95",
        "raw_two_sided_p_value", "holm_adjusted_p_value", "interpretation",
    )) for test in tests):
        return False, "governed exit inference fields are incomplete"
    stored_report_digest = provenance.get("report_digest")
    report_copy = dict(report)
    report_copy.pop("generated", None)
    provenance_copy = dict(provenance)
    provenance_copy.pop("report_digest", None)
    report_copy["provenance"] = provenance_copy
    if stored_report_digest != evidence_digest((report_copy,)):
        return False, "governed exit report digest mismatch"
    analytical = dict(provenance_copy)
    stored_analytical = analytical.pop("analytical_digest", None)
    if stored_analytical != evidence_digest((analytical,)):
        return False, "governed exit analytical digest mismatch"
    if report.get("fingerprint", {}).get("analytical_digest") != stored_analytical:
        return False, "governed exit fingerprint/provenance mismatch"
    readiness = provenance.get("readiness", {})
    expected_status = "COMPLETE" if readiness.get("state") == "READY" else "WAITING_DATA"
    if report.get("status") != expected_status:
        return False, "governed exit status contradicts readiness"
    return True, "valid governed HD09 report"
