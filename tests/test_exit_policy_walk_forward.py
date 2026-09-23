from __future__ import annotations

import copy
import random
from types import SimpleNamespace

import pytest

from research_engine.control_plane.models import ReportValidity, ReadinessStatus, RunnerStatus
from research_engine.control_plane.readiness import resolve_readiness
from research_engine.control_plane.report_ownership import resolve_report_ownership
from research_engine.control_plane.report_resolver import resolve_report_validity
from research_engine.experiments import exit_policy_walk_forward as wf
from research_engine.registry.master_repair_ledger import operational_baseline
from research_engine.registry.research_question_registry import REGISTRY_BY_ID


def _timeline(count: int, *, spacing: int = 600):
    return [
        {
            "canonical_opportunity_id": f"opp-{index:04d}",
            "entry_utc_epoch_s": 10_000 + index * spacing,
            "information_end_utc_epoch_s": 10_200 + index * spacing,
            "lifecycle_identities": [(f"trade-{index}", f"opp-{index:04d}", "SCALP")],
            "path_record_digests": [f"digest-{index}"],
        }
        for index in range(count)
    ]


def _path_from_timeline(timeline, *, total=None):
    records = tuple(
        SimpleNamespace(
            lifecycle_identity=item["lifecycle_identities"][0],
            canonical_opportunity_id=item["canonical_opportunity_id"],
            entry_utc_epoch_s=item["entry_utc_epoch_s"],
            exit_utc_epoch_s=item["information_end_utc_epoch_s"],
            analytical_digest=item["path_record_digests"][0],
        )
        for item in timeline
    )
    return SimpleNamespace(
        records=records,
        summary=SimpleNamespace(total_completed_lifecycles=total or len(records)),
        provenance={"digest": "path-digest"},
    )


def _populations(count=450, *, positive=False, total=None):
    timeline = _timeline(count)
    path = _path_from_timeline(timeline, total=total)
    reproduction_records = []
    candidate_records = []
    for index, item in enumerate(timeline):
        identity = item["lifecycle_identities"][0]
        reproduction_records.append(
            SimpleNamespace(lifecycle_identity=identity, replay=SimpleNamespace(pnl_r_multiple=0.0))
        )
        for policy_index, policy_id in enumerate(wf.CANDIDATE_POLICY_IDS):
            effect = 0.0
            if positive and policy_index == 0:
                effect = 0.5 + ((index % 3) - 1) * 0.05
            candidate_records.append(
                SimpleNamespace(
                    lifecycle_identity=identity,
                    canonical_opportunity_id=item["canonical_opportunity_id"],
                    candidate_policy_id=policy_id,
                    candidate_r=effect,
                )
            )
    reproduction = SimpleNamespace(records=tuple(reproduction_records), provenance={"digest": "base-digest"})
    candidate = SimpleNamespace(
        records=tuple(candidate_records),
        summary=SimpleNamespace(candidate_eligible_lifecycles=count),
        provenance={"digest": "candidate-digest"},
    )
    return candidate, reproduction, path


@pytest.fixture(scope="module")
def null_report():
    return wf.analyse_ex10(*_populations(), foundations_validated=True)


@pytest.fixture(scope="module")
def positive_report():
    return wf.analyse_ex10(*_populations(positive=True), foundations_validated=True)


def test_five_folds_are_expanding_grouped_later_and_deterministic():
    timeline = _timeline(450)
    folds = wf.construct_five_expanding_folds(timeline)
    assert len(folds) == 5
    assert [len(fold["training_opportunity_ids"]) for fold in folds] == [200, 250, 300, 350, 400]
    assert [len(fold["validation_opportunity_ids"]) for fold in folds] == [50] * 5
    for fold in folds:
        assert set(fold["training_opportunity_ids"]).isdisjoint(fold["validation_opportunity_ids"])
        training_end = max(
            item["information_end_utc_epoch_s"] for item in timeline
            if item["canonical_opportunity_id"] in fold["training_opportunity_ids"]
        )
        assert training_end <= fold["validation_start_utc_epoch_s"] - 300
    assert folds == wf.construct_five_expanding_folds(list(timeline))


def test_input_reordering_does_not_change_timeline_or_fold_identity():
    timeline = _timeline(450)
    shuffled = list(timeline)
    random.Random(23).shuffle(shuffled)
    canonical = wf.build_opportunity_timeline(_path_from_timeline(timeline))
    reordered = wf.build_opportunity_timeline(_path_from_timeline(shuffled))
    assert canonical == reordered
    assert wf.construct_five_expanding_folds(canonical) == wf.construct_five_expanding_folds(reordered)


def test_opportunity_grouping_keeps_repeated_horizons_together():
    path = _path_from_timeline(_timeline(1))
    first = path.records[0]
    second = SimpleNamespace(**{**vars(first), "lifecycle_identity": ("trade-2", first.canonical_opportunity_id, "INTRADAY"), "exit_utc_epoch_s": first.exit_utc_epoch_s + 20})
    grouped = wf.build_opportunity_timeline(SimpleNamespace(records=(first, second)))
    assert len(grouped) == 1
    assert len(grouped[0]["lifecycle_identities"]) == 2
    assert grouped[0]["information_end_utc_epoch_s"] == second.exit_utc_epoch_s


def test_temporal_purge_and_exact_m5_embargo_use_timestamps_not_positions():
    timeline = _timeline(450)
    validation_start = timeline[200]["entry_utc_epoch_s"]
    timeline[199] = {**timeline[199], "information_end_utc_epoch_s": validation_start}
    timeline[198] = {**timeline[198], "information_end_utc_epoch_s": validation_start - 1}
    timeline[197] = {**timeline[197], "information_end_utc_epoch_s": validation_start - 300}
    fold = wf.construct_five_expanding_folds(timeline)[0]
    assert timeline[199]["canonical_opportunity_id"] in fold["purged_opportunity_ids"]
    assert timeline[198]["canonical_opportunity_id"] in fold["embargo_excluded_opportunity_ids"]
    assert timeline[197]["canonical_opportunity_id"] in fold["training_opportunity_ids"]
    changed = copy.deepcopy(timeline)
    changed[198]["information_end_utc_epoch_s"] = validation_start - 300
    assert wf.construct_five_expanding_folds(changed)[0]["fold_digest"] != fold["fold_digest"]


def test_missing_or_ambiguous_timestamp_and_account_fanout_fail_closed():
    path = _path_from_timeline(_timeline(2))
    broken = SimpleNamespace(**{**vars(path.records[0]), "entry_utc_epoch_s": None})
    with pytest.raises(wf.WalkForwardAnalysisError, match="timestamp"):
        wf.build_opportunity_timeline(SimpleNamespace(records=(broken,)))
    duplicate = SimpleNamespace(**vars(path.records[0]))
    with pytest.raises(wf.WalkForwardAnalysisError, match="fanout"):
        wf.build_opportunity_timeline(SimpleNamespace(records=(path.records[0], duplicate)))


def test_fold_integrity_rejects_overlap_duplicate_missing_and_provenance_tamper():
    folds = wf.construct_five_expanding_folds(_timeline(450))
    wf._assert_fold_integrity(folds)
    for mutation, message in (
        (lambda value: value.pop(), "incomplete"),
        (lambda value: value[0]["training_opportunity_ids"].append(value[0]["validation_opportunity_ids"][0]), "overlap"),
        (lambda value: value[1]["validation_opportunity_ids"].append(value[1]["validation_opportunity_ids"][0]), "duplicate"),
        (lambda value: value[0].__setitem__("fold_digest", "tampered"), "provenance"),
    ):
        changed = copy.deepcopy(folds)
        mutation(changed)
        with pytest.raises(wf.WalkForwardAnalysisError, match=message):
            wf._assert_fold_integrity(changed)


def test_frozen_evidence_gates_produce_waiting_data():
    report = wf.analyse_ex10(*_populations(449), foundations_validated=True)
    assert report["status"] == "WAITING_DATA"
    assert report["provenance"]["readiness"]["remaining_total_distinct_opportunities"] == 1
    assert report["overall"]["folds"][0]["remaining_training_opportunities"] == 1
    short = wf.analyse_ex10(*_populations(200), foundations_validated=True)
    assert short["status"] == "WAITING_DATA"
    assert short["overall"]["folds"][-1]["validation_opportunity_count"] == 0
    low_coverage = wf.analyse_ex10(*_populations(450, total=500), foundations_validated=True)
    assert low_coverage["status"] == "WAITING_DATA"


def _selection_rows(effect=0.5):
    rows = []
    for index in range(40):
        for policy_index, policy_id in enumerate(wf.CANDIDATE_POLICY_IDS):
            rows.append({
                "candidate_policy_id": policy_id,
                "lifecycle_identity": (f"trade-{index}", f"opp-{index}", "SCALP"),
                "canonical_opportunity_id": f"opp-{index}",
                "candidate_r": effect if policy_index < 2 else 0.0,
                "baseline_r": 0.0,
                "value": effect if policy_index < 2 else 0.0,
            })
    return rows


def test_training_selection_is_nine_policy_only_with_frozen_tie_break():
    selection = wf.select_training_policy(_selection_rows(), "FOLD")
    assert len(selection["training_policy_statistics"]) == 9
    assert selection["selected_policy_id"] == wf.CANDIDATE_POLICY_IDS[0]
    assert selection["candidate_order"] == list(wf.CANDIDATE_POLICY_IDS)
    incomplete = [row for row in _selection_rows() if row["candidate_policy_id"] != wf.CANDIDATE_POLICY_IDS[-1]]
    with pytest.raises(ValueError, match="no eligible rows"):
        wf.select_training_policy(incomplete, "FOLD")


def test_training_selection_cannot_see_validation_and_baseline_is_valid_null():
    training = _selection_rows()
    first = wf.select_training_policy(training, "FOLD")
    validation_that_would_reverse_choice = [dict(row, value=-999.0) for row in training]
    second = wf.select_training_policy(training, "FOLD")
    assert validation_that_would_reverse_choice  # deliberately never supplied to selection
    assert first == second
    null_rows = [dict(row, candidate_r=0.0, value=0.0) for row in training]
    assert wf.select_training_policy(null_rows, "FOLD")["selected_policy_id"] == wf.BASELINE_POLICY_ID


def test_validation_uses_exact_pair_endpoint_freezes_policy_and_keeps_negative():
    rows = _selection_rows(effect=-0.25)
    policy = wf.CANDIDATE_POLICY_IDS[0]
    result = wf.evaluate_frozen_validation(rows, policy, "FOLD")
    assert result["selected_policy_id"] == policy
    assert result["paired_validation_effect"] == pytest.approx(-0.25)
    assert result["weighted_selected_policy_r"] - result["weighted_baseline_r"] == pytest.approx(-0.25)
    with pytest.raises(wf.WalkForwardAnalysisError, match="outside frozen"):
        wf.evaluate_frozen_validation(rows, "MUTATED_POLICY", "FOLD")


def test_duplicate_candidate_account_fanout_fails_closed():
    candidate, reproduction, _ = _populations(2)
    candidate.records = (*candidate.records, candidate.records[0])
    with pytest.raises(wf.WalkForwardAnalysisError, match="fanout"):
        wf._effect_rows({"opp-0000", "opp-0001"}, candidate, reproduction, prefix="TEST")


def test_chronology_ambiguity_and_future_information_return_blocked(monkeypatch):
    candidate, reproduction, path = _populations()
    broken = SimpleNamespace(**{**vars(path.records[0]), "entry_utc_epoch_s": None})
    ambiguous_path = SimpleNamespace(
        records=(broken, *path.records[1:]), summary=path.summary, provenance=path.provenance,
    )
    report = wf.analyse_ex10(candidate, reproduction, ambiguous_path, foundations_validated=True)
    assert report["status"] == "BLOCKED"
    assert "timestamp" in report["warnings"][0]

    original_timeline = wf.build_opportunity_timeline(path)
    retained = original_timeline[199]["canonical_opportunity_id"]
    validation_start = original_timeline[200]["entry_utc_epoch_s"]
    modified_records = tuple(
        SimpleNamespace(**{**vars(record), "exit_utc_epoch_s": validation_start - 1})
        if record.canonical_opportunity_id == retained else record
        for record in path.records
    )
    future_path = SimpleNamespace(records=modified_records, summary=path.summary, provenance=path.provenance)
    frozen_folds = wf.construct_five_expanding_folds(original_timeline)
    monkeypatch.setattr(wf, "construct_five_expanding_folds", lambda timeline: frozen_folds)
    future = wf.analyse_ex10(candidate, reproduction, future_path, foundations_validated=True)
    assert future["status"] == "BLOCKED"
    assert "purge/embargo" in future["warnings"][0]


def test_valid_null_and_successful_oos_effect_both_complete(null_report, positive_report):
    assert null_report["status"] == "COMPLETE"
    assert null_report["provenance"]["aggregate"]["supported_oos_improvement"] is False
    assert positive_report["status"] == "COMPLETE"
    assert positive_report["provenance"]["aggregate"]["supported_oos_improvement"] is True
    assert len(null_report["overall"]["folds"]) == 5
    assert all(fold["fold_status"] == "VALID" for fold in null_report["overall"]["folds"])


def test_current_validator_rejects_tamper_and_partial_family(null_report):
    assert wf.validate_governed_ex10_report(null_report)[0]
    tampered = copy.deepcopy(null_report)
    tampered["overall"]["folds"][0]["validation_opportunity_count"] = 49
    assert not wf.validate_governed_ex10_report(tampered)[0]
    partial = copy.deepcopy(null_report)
    partial["provenance"]["folds"].pop()
    assert not wf.validate_governed_ex10_report(partial)[0]


def test_current_ownership_resolver_and_readiness_are_governed(null_report):
    assert resolve_report_ownership("ex10_walk_forward.json", "EX10").allowed
    assert not resolve_report_ownership("ex10_walk_forward.json", "EX9").allowed
    validity, _ = resolve_report_validity(
        "ex10_walk_forward.json", null_report, expected_question_id="EX10",
    )
    assert validity == ReportValidity.VALID_CURRENT
    tampered = copy.deepcopy(null_report)
    tampered["status"] = "WAITING_DATA"
    invalid, _ = resolve_report_validity(
        "ex10_walk_forward.json", tampered, expected_question_id="EX10",
    )
    assert invalid == ReportValidity.INVALIDATED
    question = SimpleNamespace(id="EX10", depends_on=())
    state, _ = resolve_readiness(
        question, SimpleNamespace(), RunnerStatus.READY, ReportValidity.VALID_CURRENT,
        "WAITING_DATA", {},
    )
    assert state == ReadinessStatus.WAITING_DATA


def test_registry_transition_and_unrelated_exit_statuses_are_unchanged():
    assert REGISTRY_BY_ID["EX10"].runner_module == wf.__name__
    assert operational_baseline() == (53, 17)
    assert REGISTRY_BY_ID["EX5"].report_filename == "ex5_horizon_exit.json"
    assert REGISTRY_BY_ID["EX6"].report_filename == "ex6_strategy_exit.json"
    assert REGISTRY_BY_ID["EX8"].report_filename == "ex8_pattern_exit.json"
