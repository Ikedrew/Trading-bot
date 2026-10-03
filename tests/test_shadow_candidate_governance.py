from __future__ import annotations

import copy

import pytest

from core.production_data_contract import current_schema
from core.shadow.candidate_models import candidate_runtime_id
from research_engine.command_center.decision_gates import (
    evaluate_candidate_promotion_review,
)
from research_engine.control_plane.evidence_provenance import (
    CURRENT,
    select_current_evidence,
)
from research_engine.control_plane.shadow_candidate_governance import (
    CandidateGovernanceError,
    SourceObject,
    candidate_research_summaries,
    candidate_status_citation,
    freeze_candidate_evidence,
    governed_dataset_authority,
    optimisation_registry_shadow_view,
    rolling_candidate_frontier,
)


CID = "candidate-generic"
PID = "policy-generic"
THASH = "treatment-sha256"


def _records(index: int, baseline_r: float, candidate_r: float):
    shadow_id = f"shadow-{index}"
    canonical = f"opp-{index}"
    horizon = "H1"
    runtime_id = candidate_runtime_id(
        shadow_trade_id=shadow_id,
        canonical_opportunity_id=canonical,
        trade_horizon=horizon,
        candidate_id=CID,
        policy_id=PID,
    )
    b_open_id = f"baseline-open-{index}"
    b_close_id = f"baseline-close-{index}"
    c_open_id = f"{runtime_id}:CANDIDATE_OPEN:{index * 100}"
    c_close_id = f"{runtime_id}:CANDIDATE_CLOSE:{index * 100 + 10}"
    baseline = [
        {
            "schema_version": "shadow_runtime_v1", "event_type": "OPEN",
            "event_id": b_open_id, "shadow_trade_id": shadow_id,
            "canonical_opportunity_id": canonical, "horizon": horizon,
        },
        {
            "schema_version": "shadow_runtime_v1", "event_type": "CLOSE",
            "event_id": b_close_id, "shadow_trade_id": shadow_id,
            "canonical_opportunity_id": canonical, "horizon": horizon,
            "outcome": {"pnl_r_multiple": baseline_r},
            "exit_market_time_utc_epoch_s": index * 100 + 10,
        },
    ]
    common = {
        "schema_version": "shadow_candidate_v1",
        "producer": "shadow_candidate_runtime",
        "candidate_runtime_id": runtime_id,
        "candidate_id": CID,
        "policy_id": PID,
        "treatment_hash": THASH,
        "shadow_trade_id": shadow_id,
        "canonical_opportunity_id": canonical,
        "trade_horizon": horizon,
    }
    candidate = [
        {**common, "event_type": "CANDIDATE_OPEN", "event_id": c_open_id,
         "bar_time_utc": index * 100, "watermark": 0},
        {**common, "event_type": "CANDIDATE_CLOSE", "event_id": c_close_id,
         "bar_time_utc": index * 100 + 10, "watermark": index * 100 + 10,
         "outcome": {"candidate_r": candidate_r}},
    ]
    pair_material = "|".join((shadow_id, canonical, horizon, CID, PID, THASH))
    import hashlib
    pair_id = "pair_" + hashlib.sha256(pair_material.encode()).hexdigest()[:32]
    evaluation = [{
        "schema_version": "shadow_candidate_evaluation_v1",
        "event_type": "PAIRED_OUTCOME", "pair_id": pair_id,
        "candidate_runtime_id": runtime_id, "candidate_id": CID,
        "policy_id": PID, "treatment_hash": THASH,
        "shadow_trade_id": shadow_id, "canonical_opportunity_id": canonical,
        "trade_horizon": horizon, "baseline_r": baseline_r,
        "candidate_r": candidate_r, "paired_delta_r": candidate_r - baseline_r,
        "baseline_exit_time": index * 100 + 10,
        "candidate_exit_time": index * 100 + 10,
        "outcome_classification": ("IMPROVED" if candidate_r > baseline_r
                                   else "WORSENED" if candidate_r < baseline_r
                                   else "UNCHANGED"),
        "lineage": {
            "baseline_event_id": b_close_id,
            "baseline_open_event_id": b_open_id,
            "candidate_open_event_id": c_open_id,
            "candidate_close_event_id": c_close_id,
            "baseline_schema_version": "shadow_runtime_v1",
            "candidate_schema_version": "shadow_candidate_v1",
        },
    }]
    return baseline, candidate, evaluation


def _frozen(outcomes=((-1.0, 1.0), (2.0, 1.0), (0.0, 0.0))):
    baseline, candidate, evaluation = [], [], []
    for index, (base_r, cand_r) in enumerate(outcomes, 1):
        b_rows, c_rows, e_rows = _records(index, base_r, cand_r)
        baseline.extend(b_rows)
        candidate.extend(c_rows)
        evaluation.extend(e_rows)
    return freeze_candidate_evidence(
        baseline_objects=(SourceObject.freeze("baseline.jsonl", baseline),),
        candidate_objects=(SourceObject.freeze("candidate.jsonl", candidate),),
        evaluation_objects=(SourceObject.freeze("evaluation.jsonl", evaluation),),
        frozen_at="2026-10-03T00:00:00Z",
    )


def _registration(minimum=3):
    return {
        "candidate_id": CID, "policy_id": PID, "treatment_hash": THASH,
        "minimum_sample_requirement": minimum,
        "readiness_criteria": {
            "minimum_paired_sample": minimum,
            "minimum_mean_paired_delta_r": 0.0,
            "require_zero_integrity_violations": True,
            "require_complete_pairing": True,
        },
    }


def test_dataset_authorities_are_distinct_and_provenance_recognises_both():
    candidate = governed_dataset_authority("shadow_candidate_v1")
    evaluation = governed_dataset_authority("shadow_candidate_evaluation_v1")
    assert candidate and evaluation and candidate.evidence_class != evaluation.evidence_class
    assert current_schema("shadow_candidate") == "shadow_candidate_v1"
    assert current_schema("shadow_candidate_evaluation") == "shadow_candidate_evaluation_v1"
    _, rows, _ = _records(1, -1, 1)
    assert select_current_evidence("shadow_candidate_v1", rows).component["state"] == CURRENT


def test_snapshot_freezes_exact_objects_and_reproduces_deterministically():
    first = _frozen()
    replay = _frozen()
    assert first.snapshot_id == replay.snapshot_id
    assert first.evidence_frontier == replay.evidence_frontier
    names = {item.dataset_name for item in first.snapshots}
    assert names == {"shadow_runtime", "shadow_candidate", "shadow_candidate_evaluation"}
    assert all("object=" in item.source_boundaries[0] for item in first.snapshots)
    assert candidate_research_summaries(first, [_registration()]) == \
        candidate_research_summaries(replay, [_registration()])


def test_claimed_object_digest_mismatch_fails_closed():
    baseline, _, _ = _records(1, -1, 1)
    with pytest.raises(CandidateGovernanceError, match="SOURCE_OBJECT_DIGEST_MISMATCH"):
        SourceObject.freeze("baseline.jsonl", baseline, content_digest="0" * 64)


@pytest.mark.parametrize("target,reason", [
    ("schema_version", "CANDIDATE_SCHEMA_MISMATCH"),
    ("treatment_hash", "TREATMENT_HASH_MISMATCH"),
])
def test_schema_and_treatment_mismatch_invalidate_research(target, reason):
    baseline, candidate, evaluation = _records(1, -1, 1)
    candidate[0][target] = "wrong"
    frozen = freeze_candidate_evidence(
        baseline_objects=(SourceObject.freeze("b", baseline),),
        candidate_objects=(SourceObject.freeze("c", candidate),),
        evaluation_objects=(SourceObject.freeze("e", evaluation),),
    )
    report = candidate_research_summaries(frozen, [_registration(1)])[0]
    assert report["status"] == "SHADOW_VALIDATION_INVALID"
    assert reason in report["integrity_reasons"]


def test_lineage_resolves_and_missing_or_mismatched_lineage_blocks_readiness():
    valid = candidate_research_summaries(_frozen(((-1, 1),)), [_registration(1)],
                                         promotion_review_prerequisites_satisfied=True)[0]
    assert valid["status"] == "READY_FOR_PROMOTION_REVIEW"
    baseline, candidate, evaluation = _records(1, -1, 1)
    evaluation[0]["lineage"]["candidate_close_event_id"] = "not-present"
    frozen = freeze_candidate_evidence(
        baseline_objects=(SourceObject.freeze("b", baseline),),
        candidate_objects=(SourceObject.freeze("c", candidate),),
        evaluation_objects=(SourceObject.freeze("e", evaluation),),
    )
    invalid = candidate_research_summaries(frozen, [_registration(1)],
                                           promotion_review_prerequisites_satisfied=True)[0]
    assert invalid["status"] == "SHADOW_VALIDATION_INVALID"
    assert "EVALUATION_LINEAGE_UNRESOLVED" in invalid["integrity_reasons"]


def test_exact_metrics_and_block2_readiness_are_exposed():
    report = candidate_research_summaries(_frozen(), [_registration(3)])[0]
    assert report["observed_n"] == report["paired_n"] == 3
    assert report["baseline_expectancy"] == pytest.approx(1 / 3)
    assert report["candidate_expectancy"] == pytest.approx(2 / 3)
    assert report["mean_paired_delta_r"] == pytest.approx(1 / 3)
    assert report["baseline_pf"] == pytest.approx(2.0)
    assert report["candidate_pf"] is None
    assert report["baseline_max_dd_r"] == 1.0
    assert report["candidate_max_dd_r"] == 0.0
    assert (report["improved_n"], report["worsened_n"], report["unchanged_n"]) == (1, 1, 1)
    assert report["status"] == "SHADOW_VALIDATED"


def test_insufficient_active_validated_and_review_ready_states():
    insufficient = candidate_research_summaries(
        _frozen(((-1, 1),)), [_registration(2)])[0]
    assert insufficient["status"] == "SHADOW_VALIDATION_INSUFFICIENT_DATA"
    active_reg = _registration(1)
    active_reg["readiness_criteria"]["minimum_mean_paired_delta_r"] = 3.0
    active = candidate_research_summaries(_frozen(((-1, 1),)), [active_reg])[0]
    assert active["status"] == "SHADOW_VALIDATION_ACTIVE"
    validated = candidate_research_summaries(
        _frozen(((-1, 1),)), [_registration(1)])[0]
    ready = candidate_research_summaries(
        _frozen(((-1, 1),)), [_registration(1)],
        promotion_review_prerequisites_satisfied=True)[0]
    assert validated["status"] == "SHADOW_VALIDATED"
    assert ready["status"] == "READY_FOR_PROMOTION_REVIEW"


def test_review_gate_never_grants_live_approval():
    summary = candidate_research_summaries(
        _frozen(((-1, 1),)), [_registration(1)],
        promotion_review_prerequisites_satisfied=True)[0]
    gate = evaluate_candidate_promotion_review(summary).to_dict()
    assert gate["review_eligible"] is True
    assert gate["live_approved"] is False
    assert optimisation_registry_shadow_view(summary)["live_approved"] is False


def test_legacy_baseline_only_snapshot_is_not_observed_not_failed():
    baseline, _, _ = _records(1, -1, 1)
    frozen = freeze_candidate_evidence(
        baseline_objects=(SourceObject.freeze("old-shadow.jsonl", baseline),))
    report = candidate_research_summaries(frozen, [_registration(1)])[0]
    assert report["status"] == "NOT_OBSERVED"
    assert report["paired_n"] == 0
    assert report["baseline_expectancy"] is None


def test_duplicate_objects_and_horizons_do_not_inflate_independent_units():
    frozen = _frozen(((-1, 1),))
    baseline_obj = SourceObject.freeze("same-b", frozen.baseline_records)
    candidate_obj = SourceObject.freeze("same-c", frozen.candidate_records)
    evaluation_obj = SourceObject.freeze("same-e", frozen.evaluation_records)
    duplicate = freeze_candidate_evidence(
        baseline_objects=(baseline_obj, baseline_obj),
        candidate_objects=(candidate_obj, candidate_obj),
        evaluation_objects=(evaluation_obj, evaluation_obj),
    )
    assert candidate_research_summaries(duplicate, [_registration(1)])[0]["paired_n"] == 1

    baseline = list(frozen.baseline_records)
    candidate = list(frozen.candidate_records)
    evaluation = list(frozen.evaluation_records)
    second = copy.deepcopy(evaluation[0])
    second["trade_horizon"] = "H4"
    import hashlib
    material = "|".join((second["shadow_trade_id"], second["canonical_opportunity_id"],
                         "H4", CID, PID, THASH))
    second["pair_id"] = "pair_" + hashlib.sha256(material.encode()).hexdigest()[:32]
    # Repoint to an exact H4 lifecycle and baseline lineage.
    b2, c2, e2 = _records(1, -1, 1)
    for row in b2:
        row["horizon"] = "H4"
        row["event_id"] += "-h4"
    for row in c2:
        row["trade_horizon"] = "H4"
        row["event_id"] += "-h4"
        row["candidate_runtime_id"] = candidate_runtime_id(
            shadow_trade_id=row["shadow_trade_id"],
            canonical_opportunity_id=row["canonical_opportunity_id"],
            trade_horizon="H4", candidate_id=CID, policy_id=PID)
    second["candidate_runtime_id"] = c2[0]["candidate_runtime_id"]
    second["lineage"] = {
        "baseline_open_event_id": b2[0]["event_id"],
        "baseline_event_id": b2[1]["event_id"],
        "candidate_open_event_id": c2[0]["event_id"],
        "candidate_close_event_id": c2[1]["event_id"],
    }
    collapsed = freeze_candidate_evidence(
        baseline_objects=(SourceObject.freeze("b", baseline + b2),),
        candidate_objects=(SourceObject.freeze("c", candidate + c2),),
        evaluation_objects=(SourceObject.freeze("e", evaluation + [second]),),
    )
    assert candidate_research_summaries(collapsed, [_registration(1)])[0]["paired_n"] == 1


def test_rolling_frontier_and_citations_are_snapshot_bound():
    first = candidate_research_summaries(
        _frozen(((-1, 1),)), [_registration(1)])[0]
    same = candidate_research_summaries(
        _frozen(((-1, 1),)), [_registration(1)])[0]
    later = candidate_research_summaries(
        _frozen(((-1, 1), (1, 2))), [_registration(1)])[0]
    unchanged = rolling_candidate_frontier(first, same)
    changed = rolling_candidate_frontier(first, later)
    assert unchanged["new_candidate_evidence"] is False
    assert unchanged["paired_added"] == 0
    assert changed["new_candidate_evidence"] is True
    assert changed["paired_added"] == 1
    citation = candidate_status_citation(later)
    assert citation["snapshot_id"] == later["snapshot_id"]
    assert citation["paired_n"] == 2
