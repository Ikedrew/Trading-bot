"""Governed HD09 expanding walk-forward exit-policy research for EX10."""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from research_engine.control_plane.evidence_provenance import evidence_digest
from research_engine.control_plane.evidence_readiness import EvidenceRequirement, evaluate_evidence_readiness
from research_engine.control_plane.exit_bar_path import GovernedExitBarPathEvidence
from research_engine.control_plane.exit_baseline_replay import GovernedBaselineReproductionPopulation
from research_engine.control_plane.exit_candidate_replay import CANDIDATE_POLICY_IDS, CandidateReplayPopulation
from research_engine.experiments.exit_policy_governed import (
    _REPORTS_DIR,
    _validate_foundations,
    clustered_cr0_cell_means,
    holm_adjust,
    load_governed_foundations,
)
from research_engine.registry.exit_policy_adjudication import (
    BASELINE_POLICY_ID,
    CANDIDATE_POLICIES_V1,
    CLUSTERED_INFERENCE,
    COMMON_ANALYTICAL_CONTRACT,
    EX10_CONTRACT,
    HD09_ADJUDICATED_CONTRACT,
    HD09_ADJUDICATION_VERSION,
    REPORT_OWNERSHIP,
    SAMPLE_AND_READINESS_CONTRACT,
)

REPORT_SCHEMA_VERSION = "hd09_governed_ex10_walk_forward_v1"
FOLD_COUNT = 5
MIN_TOTAL = int(EX10_CONTRACT["minimum_total_distinct_opportunities"])
MIN_TRAIN = int(EX10_CONTRACT["minimum_training_opportunities"])
MIN_VALIDATION = int(EX10_CONTRACT["minimum_validation_opportunities_per_fold"])
EMBARGO_SECONDS = 300
ALPHA = float(CLUSTERED_INFERENCE["alpha"])
_FOLD_IDENTITY_FIELDS = (
    "fold_identity",
    "fold_number",
    "validation_start_utc_epoch_s",
    "purge_boundary",
    "embargo_boundary_utc_epoch_s",
    "embargo_semantics",
    "pre_purge_training_opportunity_ids",
    "purged_opportunity_ids",
    "embargo_excluded_opportunity_ids",
    "training_opportunity_ids",
    "validation_opportunity_ids",
)


class WalkForwardAnalysisError(ValueError):
    pass


def _fold_identity_material(fold: Mapping[str, Any]) -> dict[str, Any]:
    return {field: fold.get(field) for field in _FOLD_IDENTITY_FIELDS}


def _validate_contract() -> None:
    if HD09_ADJUDICATED_CONTRACT.get("ex10") != EX10_CONTRACT:
        raise WalkForwardAnalysisError("invalid HD09 EX10 authority")
    if EX10_CONTRACT["fold_type"] != "five-fold expanding-window validation":
        raise WalkForwardAnalysisError("EX10 fold authority drift")
    if EX10_CONTRACT["embargo"] != (
        "one M5 bar: retained training exit must be <= validation start UTC - 300 seconds"
    ):
        raise WalkForwardAnalysisError("EX10 embargo authority drift")
    if FOLD_COUNT != int(EX10_CONTRACT["minimum_valid_folds"]):
        raise WalkForwardAnalysisError("EX10 required fold count drift")
    if len(CANDIDATE_POLICY_IDS) != 9:
        raise WalkForwardAnalysisError("EX10 candidate vocabulary drift")


_validate_contract()


def build_opportunity_timeline(path: GovernedExitBarPathEvidence) -> list[dict[str, Any]]:
    """Group path lifecycles into deterministic governed information intervals."""
    grouped: dict[str, list[Any]] = defaultdict(list)
    identities: set[tuple[str, str, str]] = set()
    for record in path.records:
        if record.lifecycle_identity in identities:
            raise WalkForwardAnalysisError("duplicate lifecycle/account-fanout path identity")
        identities.add(record.lifecycle_identity)
        if record.lifecycle_identity[1] != record.canonical_opportunity_id:
            raise WalkForwardAnalysisError("path cluster identity is not canonical opportunity")
        if (
            isinstance(record.entry_utc_epoch_s, bool)
            or not isinstance(record.entry_utc_epoch_s, int)
            or isinstance(record.exit_utc_epoch_s, bool)
            or not isinstance(record.exit_utc_epoch_s, int)
            or record.exit_utc_epoch_s <= record.entry_utc_epoch_s
        ):
            raise WalkForwardAnalysisError("missing or ambiguous governed lifecycle timestamp")
        grouped[record.canonical_opportunity_id].append(record)
    timeline = []
    for opportunity_id, records in grouped.items():
        entries = {item.entry_utc_epoch_s for item in records}
        if len(entries) != 1:
            raise WalkForwardAnalysisError("canonical opportunity has ambiguous entry timestamp")
        timeline.append({
            "canonical_opportunity_id": opportunity_id,
            "entry_utc_epoch_s": next(iter(entries)),
            "information_end_utc_epoch_s": max(item.exit_utc_epoch_s for item in records),
            "lifecycle_identities": sorted(item.lifecycle_identity for item in records),
            "path_record_digests": sorted(item.analytical_digest for item in records),
        })
    timeline.sort(key=lambda item: (item["entry_utc_epoch_s"], item["canonical_opportunity_id"]))
    return timeline


def construct_five_expanding_folds(
    timeline: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Reserve the latest 5×50 opportunities for ordered validation."""
    ordered = list(timeline)
    if ordered != sorted(
        ordered, key=lambda item: (item["entry_utc_epoch_s"], item["canonical_opportunity_id"]),
    ):
        raise WalkForwardAnalysisError("opportunity timeline is not canonically ordered")
    if len({item["canonical_opportunity_id"] for item in ordered}) != len(ordered):
        raise WalkForwardAnalysisError("duplicate canonical opportunity in timeline")
    validation_total = FOLD_COUNT * MIN_VALIDATION
    initial_training_end = max(0, len(ordered) - validation_total)
    folds = []
    for fold_index in range(FOLD_COUNT):
        validation_start_index = initial_training_end + fold_index * MIN_VALIDATION
        validation_end_index = min(len(ordered), validation_start_index + MIN_VALIDATION)
        pre_purge = ordered[:validation_start_index]
        validation = ordered[validation_start_index:validation_end_index]
        validation_start = (
            int(validation[0]["entry_utc_epoch_s"]) if validation else None
        )
        if validation_start is None:
            purged, embargoed, training = [], [], list(pre_purge)
        else:
            purged = [
                item for item in pre_purge
                if int(item["information_end_utc_epoch_s"]) >= validation_start
            ]
            after_purge = [
                item for item in pre_purge
                if int(item["information_end_utc_epoch_s"]) < validation_start
            ]
            embargo_boundary = validation_start - EMBARGO_SECONDS
            embargoed = [
                item for item in after_purge
                if int(item["information_end_utc_epoch_s"]) > embargo_boundary
            ]
            training = [
                item for item in after_purge
                if int(item["information_end_utc_epoch_s"]) <= embargo_boundary
            ]
        fold_material = {
            "fold_identity": f"EX10_FOLD_{fold_index + 1}",
            "fold_number": fold_index + 1,
            "validation_start_utc_epoch_s": validation_start,
            "purge_boundary": "training information_end_utc_epoch_s < validation_start_utc_epoch_s",
            "embargo_boundary_utc_epoch_s": (
                validation_start - EMBARGO_SECONDS if validation_start is not None else None
            ),
            "embargo_semantics": EX10_CONTRACT["embargo"],
            "pre_purge_training_opportunity_ids": [item["canonical_opportunity_id"] for item in pre_purge],
            "purged_opportunity_ids": [item["canonical_opportunity_id"] for item in purged],
            "embargo_excluded_opportunity_ids": [item["canonical_opportunity_id"] for item in embargoed],
            "training_opportunity_ids": [item["canonical_opportunity_id"] for item in training],
            "validation_opportunity_ids": [item["canonical_opportunity_id"] for item in validation],
        }
        fold_material["fold_digest"] = evidence_digest((_fold_identity_material(fold_material),))
        folds.append(fold_material)
    return folds


def _assert_fold_integrity(folds: Sequence[Mapping[str, Any]]) -> None:
    if len(folds) != FOLD_COUNT:
        raise WalkForwardAnalysisError("incomplete five-fold family")
    previous_training: set[str] = set()
    validation_seen: set[str] = set()
    for expected_number, fold in enumerate(folds, 1):
        if fold.get("fold_number") != expected_number:
            raise WalkForwardAnalysisError("fold identity/order mismatch")
        training = list(fold.get("training_opportunity_ids", ()))
        validation = list(fold.get("validation_opportunity_ids", ()))
        if len(training) != len(set(training)):
            raise WalkForwardAnalysisError("duplicate training opportunity")
        if len(validation) != len(set(validation)):
            raise WalkForwardAnalysisError("duplicate validation opportunity")
        training_set, validation_set = set(training), set(validation)
        if training_set & validation_set:
            raise WalkForwardAnalysisError("training/validation opportunity overlap")
        if validation_seen & validation_set:
            raise WalkForwardAnalysisError("validation opportunity reused across folds")
        if not previous_training <= training_set:
            raise WalkForwardAnalysisError("training history is not expanding")
        validation_seen |= validation_set
        previous_training = training_set
        supplied = fold.get("fold_digest")
        if supplied != evidence_digest((_fold_identity_material(fold),)):
            raise WalkForwardAnalysisError("fold provenance mismatch")


def _effect_rows(
    opportunity_ids: set[str],
    candidate: CandidateReplayPopulation,
    reproduction: GovernedBaselineReproductionPopulation,
    *,
    policy_ids: Sequence[str] = CANDIDATE_POLICY_IDS,
    prefix: str,
) -> list[dict[str, Any]]:
    baseline = {item.lifecycle_identity: item.replay for item in reproduction.records}
    if len(baseline) != len(reproduction.records):
        raise WalkForwardAnalysisError("duplicate baseline lifecycle identity")
    permitted = set(policy_ids)
    rows = []
    candidate_pairs: set[tuple[tuple[str, str, str], str]] = set()
    for item in candidate.records:
        if item.canonical_opportunity_id not in opportunity_ids or item.candidate_policy_id not in permitted:
            continue
        candidate_pair = (item.lifecycle_identity, item.candidate_policy_id)
        if candidate_pair in candidate_pairs:
            raise WalkForwardAnalysisError("duplicate candidate lifecycle/policy account-fanout identity")
        candidate_pairs.add(candidate_pair)
        replay = baseline.get(item.lifecycle_identity)
        if replay is None:
            raise WalkForwardAnalysisError("candidate lacks reproduced baseline pair")
        rows.append({
            "test_id": f"{prefix}:{item.candidate_policy_id}",
            "candidate_policy_id": item.candidate_policy_id,
            "lifecycle_identity": item.lifecycle_identity,
            "canonical_opportunity_id": item.canonical_opportunity_id,
            "candidate_r": item.candidate_r,
            "baseline_r": replay.pnl_r_multiple,
            "value": item.candidate_r - replay.pnl_r_multiple,
        })
    return rows


def select_training_policy(
    rows: Sequence[Mapping[str, Any]], fold_identity: str,
) -> dict[str, Any]:
    family = tuple(f"{fold_identity}:TRAIN:{policy_id}" for policy_id in CANDIDATE_POLICY_IDS)
    normalized = [dict(row, test_id=f"{fold_identity}:TRAIN:{row['candidate_policy_id']}") for row in rows]
    tests = clustered_cr0_cell_means(normalized, family)
    adjusted = holm_adjust(
        [(item["test_identity"], item["raw_two_sided_p_value"]) for item in tests], family,
    )
    policy_order = {value: index for index, value in enumerate(CANDIDATE_POLICY_IDS)}
    eligible = []
    for test, policy_id in zip(tests, CANDIDATE_POLICY_IDS):
        test["candidate_policy_id"] = policy_id
        test["holm_adjusted_p_value"] = adjusted[test["test_identity"]]
        test["selection_eligible"] = (
            test["weighted_effect_estimate"] > 0
            and test["holm_adjusted_p_value"] <= ALPHA
        )
        if test["selection_eligible"]:
            eligible.append(test)
    if eligible:
        selected = min(
            eligible,
            key=lambda item: (-item["weighted_effect_estimate"], policy_order[item["candidate_policy_id"]]),
        )["candidate_policy_id"]
        reason = "highest opportunity-weighted positive Holm-significant training effect"
    else:
        selected = BASELINE_POLICY_ID
        reason = "no frozen candidate had positive nine-policy Holm-significant training effect"
    material = {
        "fold_identity": fold_identity,
        "training_objective": EX10_CONTRACT["selection"],
        "tie_break": EX10_CONTRACT["tie_break"],
        "candidate_order": list(CANDIDATE_POLICY_IDS),
        "training_policy_statistics": tests,
        "selected_policy_id": selected,
        "selection_reason": reason,
    }
    return {**material, "selection_digest": evidence_digest((material,))}


def evaluate_frozen_validation(
    rows: Sequence[Mapping[str, Any]],
    selected_policy_id: str,
    fold_identity: str,
) -> dict[str, Any]:
    if selected_policy_id != BASELINE_POLICY_ID and selected_policy_id not in CANDIDATE_POLICY_IDS:
        raise WalkForwardAnalysisError("selected policy is outside frozen authority")
    selected_rows = [row for row in rows if row["candidate_policy_id"] == selected_policy_id]
    if selected_policy_id == BASELINE_POLICY_ID:
        # One deterministic baseline row per lifecycle, projected from the first
        # frozen candidate row without inspecting any candidate outcome.
        by_identity = {}
        for row in rows:
            by_identity.setdefault(tuple(row["lifecycle_identity"]), row)
        selected_rows = [dict(row, value=0.0, candidate_r=row["baseline_r"]) for row in by_identity.values()]
    test_id = f"{fold_identity}:FROZEN_VALIDATION:{selected_policy_id}"
    inference_rows = [dict(row, test_id=test_id) for row in selected_rows]
    result = clustered_cr0_cell_means(inference_rows, (test_id,))[0]
    horizon_counts = Counter(row["canonical_opportunity_id"] for row in selected_rows)
    bread = math.fsum(1.0 / horizon_counts[row["canonical_opportunity_id"]] for row in selected_rows)
    baseline_mean = math.fsum(
        row["baseline_r"] / horizon_counts[row["canonical_opportunity_id"]]
        for row in selected_rows
    ) / bread
    candidate_mean = math.fsum(
        row["candidate_r"] / horizon_counts[row["canonical_opportunity_id"]]
        for row in selected_rows
    ) / bread
    return {
        "fold_identity": fold_identity,
        "selected_policy_id": selected_policy_id,
        "validation_opportunity_count": result["distinct_canonical_opportunity_count"],
        "validation_lifecycle_count": result["eligible_paired_lifecycle_count"],
        "weighted_baseline_r": baseline_mean,
        "weighted_selected_policy_r": candidate_mean,
        "paired_validation_effect": result["weighted_effect_estimate"],
        "standard_error": result["standard_error"],
        "confidence_interval_95": result["confidence_interval_95"],
        "raw_two_sided_p_value": result["raw_two_sided_p_value"],
        "validation_endpoint": EX10_CONTRACT["validation_endpoint"],
        "fold_status": "VALID",
    }


def _blocked_report(reason: str) -> dict[str, Any]:
    report = {
        "report_schema_version": REPORT_SCHEMA_VERSION, "question_id": "EX10",
        "status": "BLOCKED", "epoch": "CURRENT",
        "overall": {"finding": f"Governed EX10 blocked: {reason}", "folds": []},
        "dataset": {"source": "exit_candidate_replay_v1", "sample_size": 0},
        "fingerprint": {"epoch": "CURRENT", "source": "exit_candidate_replay_v1", "records_used": 0},
        "confidence": "INSUFFICIENT_DATA", "recommendation": "BLOCKED",
        "warnings": [reason], "assumptions": [],
        "provenance": {"hd09_adjudication_version": HD09_ADJUDICATION_VERSION,
                       "readiness": {"state": "BLOCKED", "blockers": [reason]}},
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    material = dict(report); material.pop("generated", None)
    report["provenance"]["report_digest"] = evidence_digest((material,))
    return report


def analyse_ex10(
    candidate: CandidateReplayPopulation,
    reproduction: GovernedBaselineReproductionPopulation,
    path: GovernedExitBarPathEvidence,
    *, foundations_validated: bool = False,
) -> dict[str, Any]:
    try:
        if not foundations_validated:
            _validate_foundations(candidate, reproduction, path)
        timeline = build_opportunity_timeline(path)
        candidate_opportunities = {item.canonical_opportunity_id for item in candidate.records}
        timeline = [item for item in timeline if item["canonical_opportunity_id"] in candidate_opportunities]
        folds = construct_five_expanding_folds(timeline)
        _assert_fold_integrity(folds)
        total_gate = evaluate_evidence_readiness(
            total_count=max(1, len(timeline)), valid_count=len(timeline),
            distinct_valid_count=len(timeline),
            requirement=EvidenceRequirement(MIN_TOTAL, MIN_TOTAL, 0.0),
        )
        governed_coverage = candidate.summary.candidate_eligible_lifecycles / path.summary.total_completed_lifecycles
        blockers = list(total_gate.blockers)
        if governed_coverage < SAMPLE_AND_READINESS_CONTRACT["common_path_coverage"]:
            blockers.append("GOVERNED_COVERAGE_BELOW_REQUIRED")
        timeline_index = {item["canonical_opportunity_id"]: item for item in timeline}
        fold_reports = []
        pooled_rows = []
        for fold in folds:
            training_ids = set(fold["training_opportunity_ids"])
            validation_ids = set(fold["validation_opportunity_ids"])
            if len(training_ids) < MIN_TRAIN:
                blockers.append(f"{fold['fold_identity']}:TRAINING_OPPORTUNITIES_BELOW_REQUIRED")
            if len(validation_ids) < MIN_VALIDATION:
                blockers.append(f"{fold['fold_identity']}:VALIDATION_OPPORTUNITIES_BELOW_REQUIRED")
            validation_start = fold["validation_start_utc_epoch_s"]
            if validation_start is not None:
                for opportunity_id in training_ids:
                    if timeline_index[opportunity_id]["information_end_utc_epoch_s"] > validation_start - EMBARGO_SECONDS:
                        raise WalkForwardAnalysisError("invalid purge/embargo retained future information")
            record = {
                **fold,
                "pre_purge_training_opportunity_count": len(fold["pre_purge_training_opportunity_ids"]),
                "purged_opportunity_count": len(fold["purged_opportunity_ids"]),
                "embargo_excluded_opportunity_count": len(fold["embargo_excluded_opportunity_ids"]),
                "post_embargo_training_opportunity_count": len(training_ids),
                "validation_opportunity_count": len(validation_ids),
                "required_training_opportunities": MIN_TRAIN,
                "required_validation_opportunities": MIN_VALIDATION,
                "remaining_training_opportunities": max(0, MIN_TRAIN - len(training_ids)),
                "remaining_validation_opportunities": max(0, MIN_VALIDATION - len(validation_ids)),
            }
            if len(training_ids) >= MIN_TRAIN and len(validation_ids) >= MIN_VALIDATION:
                training_rows = _effect_rows(training_ids, candidate, reproduction, prefix="unused")
                selection = select_training_policy(training_rows, fold["fold_identity"])
                if selection["selected_policy_id"] != BASELINE_POLICY_ID and selection["selected_policy_id"] not in CANDIDATE_POLICY_IDS:
                    raise WalkForwardAnalysisError("invalid selected policy identity")
                validation_rows = _effect_rows(validation_ids, candidate, reproduction, prefix="unused")
                frozen = evaluate_frozen_validation(
                    validation_rows, selection["selected_policy_id"], fold["fold_identity"],
                )
                if frozen["selected_policy_id"] != selection["selected_policy_id"]:
                    raise WalkForwardAnalysisError("policy mutation after selection")
                record["selection"] = selection
                record["frozen_validation"] = frozen
                for row in validation_rows:
                    if row["candidate_policy_id"] == selection["selected_policy_id"]:
                        pooled_rows.append(dict(row, fold_identity=fold["fold_identity"]))
                if selection["selected_policy_id"] == BASELINE_POLICY_ID:
                    by_identity = {}
                    for row in validation_rows:
                        by_identity.setdefault(tuple(row["lifecycle_identity"]), row)
                    pooled_rows.extend(
                        dict(row, value=0.0, candidate_r=row["baseline_r"], fold_identity=fold["fold_identity"])
                        for row in by_identity.values()
                    )
                record["fold_status"] = "VALID"
            else:
                record["selection"] = None
                record["frozen_validation"] = None
                record["fold_status"] = "WAITING_DATA"
            fold_reports.append(record)
        readiness_state = "READY" if not blockers else "WAITING_DATA"
        aggregate = None
        if readiness_state == "READY":
            if len(fold_reports) != FOLD_COUNT or any(item["fold_status"] != "VALID" for item in fold_reports):
                raise WalkForwardAnalysisError("incomplete five-fold validation family")
            pooled_opportunities = [row["canonical_opportunity_id"] for row in pooled_rows]
            if len(set(pooled_opportunities)) != sum(
                item["validation_opportunity_count"] for item in fold_reports
            ):
                # Repeated lifecycle horizons are allowed, repeated opportunities across
                # folds are not; compare unique pooled clusters to declared validation.
                declared = set().union(*(set(item["validation_opportunity_ids"]) for item in fold_reports))
                if set(pooled_opportunities) != declared:
                    raise WalkForwardAnalysisError("duplicate or missing pooled validation opportunity")
            aggregate_id = "EX10:POOLED_FROZEN_VALIDATION"
            aggregate_result = clustered_cr0_cell_means(
                [dict(row, test_id=aggregate_id) for row in pooled_rows], (aggregate_id,),
            )[0]
            aggregate = {
                **aggregate_result,
                "aggregation": EX10_CONTRACT["aggregation"],
                "selected_policy_is_frozen_per_fold": True,
                "supported_oos_improvement": (
                    aggregate_result["weighted_effect_estimate"] > 0
                    and aggregate_result["raw_two_sided_p_value"] <= ALPHA
                    and aggregate_result["confidence_interval_95"][0] > 0
                ),
                "interpretation": "SUPPORTED_OOS_IMPROVEMENT" if (
                    aggregate_result["weighted_effect_estimate"] > 0
                    and aggregate_result["raw_two_sided_p_value"] <= ALPHA
                    and aggregate_result["confidence_interval_95"][0] > 0
                ) else "NOT_SUPPORTED",
            }
        readiness = {
            "state": readiness_state, "blockers": blockers,
            "total_eligible_distinct_opportunities": len(timeline),
            "required_total_distinct_opportunities": MIN_TOTAL,
            "remaining_total_distinct_opportunities": max(0, MIN_TOTAL - len(timeline)),
            "governed_coverage": governed_coverage,
            "required_governed_coverage": SAMPLE_AND_READINESS_CONTRACT["common_path_coverage"],
            "required_fold_count": FOLD_COUNT,
            "valid_fold_count": sum(item["fold_status"] == "VALID" for item in fold_reports),
            "fold_readiness": [{
                "fold_identity": item["fold_identity"],
                "training_count": item["post_embargo_training_opportunity_count"],
                "required_training_count": MIN_TRAIN,
                "validation_count": item["validation_opportunity_count"],
                "required_validation_count": MIN_VALIDATION,
                "purge_count": item["purged_opportunity_count"],
                "embargo_exclusion_count": item["embargo_excluded_opportunity_count"],
                "status": item["fold_status"],
            } for item in fold_reports],
        }
        analytical_material = {
            "question_id": "EX10", "scientific_contract": EX10_CONTRACT,
            "hd09_adjudication_version": HD09_ADJUDICATION_VERSION,
            "path_population_digest": path.provenance["digest"],
            "baseline_reproduction_population_digest": reproduction.provenance["digest"],
            "candidate_replay_population_digest": candidate.provenance["digest"],
            "candidate_policy_identities": [dict(item) for item in CANDIDATE_POLICIES_V1],
            "canonical_opportunity_grouping": True,
            "temporal_authority": "exit_bar_path_v1.entry_utc_epoch_s and exit_utc_epoch_s",
            "fold_construction": "latest 5 x 50 canonical opportunities are consecutive validation folds; all earlier opportunities initialize expanding training",
            "folds": fold_reports,
            "training_objective": EX10_CONTRACT["selection"],
            "tie_break": EX10_CONTRACT["tie_break"],
            "purge": EX10_CONTRACT["purge"],
            "embargo": EX10_CONTRACT["embargo"],
            "aggregation": EX10_CONTRACT["aggregation"],
            "inference_configuration": EX10_CONTRACT["inference"],
            "aggregate": aggregate, "readiness": readiness,
        }
        analytical_digest = evidence_digest((analytical_material,))
        status = "COMPLETE" if readiness_state == "READY" else "WAITING_DATA"
        finding = (
            f"Valid five-fold EX10 evaluation: {aggregate['interpretation']}."
            if aggregate else f"Valid EX10 machinery; evidence gates unmet: {', '.join(blockers)}."
        )
        report = {
            "report_schema_version": REPORT_SCHEMA_VERSION, "question_id": "EX10",
            "status": status, "epoch": "CURRENT",
            "overall": {"finding": finding, "folds": fold_reports, "aggregate": aggregate,
                        "sample_size": len(timeline), "distinct_canonical_opportunities": len(timeline)},
            "dataset": {"source": "exit_candidate_replay_v1", "sample_size": len(timeline),
                        "independent_observations": len(timeline)},
            "fingerprint": {"epoch": "CURRENT", "source": "exit_candidate_replay_v1",
                            "records_used": len(timeline), "analytical_digest": analytical_digest},
            "confidence": "HIGH" if status == "COMPLETE" else "INSUFFICIENT_DATA",
            "recommendation": "FINDING: governed walk-forward evaluation" if status == "COMPLETE" else "WAIT",
            "assumptions": [HD09_ADJUDICATED_CONTRACT["claim_boundary"]],
            "warnings": ["Research-only historical walk-forward; no production application authority."],
            "provenance": {**analytical_material, "analytical_digest": analytical_digest},
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        report_material = dict(report); report_material.pop("generated", None)
        report["provenance"]["report_digest"] = evidence_digest((report_material,))
        return report
    except (ValueError, TypeError, KeyError, ArithmeticError) as error:
        return _blocked_report(str(error))


def run_ex10(*, persist: bool = False) -> dict[str, Any]:
    path, reproduction, candidate = load_governed_foundations()
    report = analyse_ex10(candidate, reproduction, path)
    if persist:
        _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        (_REPORTS_DIR / REPORT_OWNERSHIP["EX10"]).write_text(
            json.dumps(report, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8",
        )
    return report


def validate_governed_ex10_report(report: Mapping[str, Any]) -> tuple[bool, str]:
    if report.get("question_id") != "EX10" or report.get("report_schema_version") != REPORT_SCHEMA_VERSION:
        return False, "governed EX10 report identity/schema mismatch"
    provenance = report.get("provenance")
    if not isinstance(provenance, Mapping):
        return False, "governed EX10 provenance missing"
    report_copy = dict(report); report_copy.pop("generated", None)
    provenance_copy = dict(provenance); stored_report = provenance_copy.pop("report_digest", None)
    report_copy["provenance"] = provenance_copy
    if stored_report != evidence_digest((report_copy,)):
        return False, "governed EX10 report digest mismatch"
    analytical = dict(provenance_copy); stored_analytical = analytical.pop("analytical_digest", None)
    if stored_analytical != evidence_digest((analytical,)):
        return False, "governed EX10 analytical digest mismatch"
    if report.get("fingerprint", {}).get("analytical_digest") != stored_analytical:
        return False, "governed EX10 fingerprint/provenance mismatch"
    folds = provenance.get("folds")
    if not isinstance(folds, list) or len(folds) != FOLD_COUNT:
        return False, "governed EX10 five-fold family incomplete"
    try:
        _assert_fold_integrity(folds)
    except WalkForwardAnalysisError as error:
        return False, str(error)
    readiness = provenance.get("readiness", {})
    expected = {"READY": "COMPLETE", "WAITING_DATA": "WAITING_DATA", "BLOCKED": "BLOCKED"}.get(readiness.get("state"))
    if expected is None or report.get("status") != expected:
        return False, "governed EX10 status contradicts readiness"
    if expected == "COMPLETE":
        if readiness.get("valid_fold_count") != FOLD_COUNT or provenance.get("aggregate") is None:
            return False, "governed EX10 complete result lacks five valid folds/aggregate"
        for fold in folds:
            if fold.get("post_embargo_training_opportunity_count") != len(fold["training_opportunity_ids"]):
                return False, "governed EX10 training count contradicts fold population"
            if fold.get("validation_opportunity_count") != len(fold["validation_opportunity_ids"]):
                return False, "governed EX10 validation count contradicts fold population"
            if len(fold["training_opportunity_ids"]) < MIN_TRAIN or len(fold["validation_opportunity_ids"]) < MIN_VALIDATION:
                return False, "governed EX10 complete fold violates sample gates"
            selection = fold.get("selection")
            validation = fold.get("frozen_validation")
            if not isinstance(selection, Mapping) or not isinstance(validation, Mapping):
                return False, "governed EX10 fold selection/validation missing"
            if selection.get("selected_policy_id") != validation.get("selected_policy_id"):
                return False, "governed EX10 selected policy mutated in validation"
            if len(selection.get("training_policy_statistics", ())) != 9:
                return False, "governed EX10 training policy family incomplete"
            selection_copy = dict(selection)
            stored_selection = selection_copy.pop("selection_digest", None)
            if stored_selection != evidence_digest((selection_copy,)):
                return False, "governed EX10 training selection digest mismatch"
            if selection.get("candidate_order") != list(CANDIDATE_POLICY_IDS):
                return False, "governed EX10 training candidate order mismatch"
            expected_tests = [f"{fold['fold_identity']}:TRAIN:{policy_id}" for policy_id in CANDIDATE_POLICY_IDS]
            if [item.get("test_identity") for item in selection["training_policy_statistics"]] != expected_tests:
                return False, "governed EX10 training policy tests are incomplete or reordered"
            selected = selection.get("selected_policy_id")
            if selected != BASELINE_POLICY_ID and selected not in CANDIDATE_POLICY_IDS:
                return False, "governed EX10 selected policy is outside frozen authority"
            if validation.get("validation_opportunity_count") != len(fold["validation_opportunity_ids"]):
                return False, "governed EX10 frozen validation population mismatch"
    return True, "valid governed HD09 EX10 report"
