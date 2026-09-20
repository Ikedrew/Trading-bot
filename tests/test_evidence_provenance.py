"""Focused contract tests for fail-closed evidence provenance."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

from research_engine.control_plane.evidence_provenance import (
    CURRENT,
    INCOMPATIBLE,
    MIXED,
    STALE,
    UNVERIFIED,
    build_evidence_provenance,
    select_current_evidence,
    use_evidence_as_supplied,
    validate_evidence_provenance,
)
from research_engine.control_plane.evidence_resolver import EvidenceResolution
from research_engine.control_plane.models import (
    ReadinessStatus,
    ReportValidity,
    RunnerStatus,
)
from research_engine.control_plane.readiness import resolve_readiness
from research_engine.control_plane.report_resolver import resolve_report_validity
from research_engine.experiments.experiment_base import (
    build_fingerprint,
    build_fingerprint_from_provenance,
    build_report,
)


def _action(record_id: str, *, epoch: str = "CURRENT") -> dict:
    return {
        "schema_version": "management_actions_v1",
        "epoch": epoch,
        "management_action_id": record_id,
        "trade_id": f"trade-{record_id}",
        "action_type": "SLTP_MODIFY",
    }


def _truth(record_id: str, *, epoch: str = "CURRENT") -> dict:
    return {
        "schema_version": "trade_truth_v1",
        "epoch": epoch,
        "identity": {
            "trade_id": f"trade-{record_id}",
            "correlation_id": f"corr-{record_id}",
        },
        "outcome": {"r_multiple_realised": 1.0},
    }


def _structured_report(provenance: dict, *, status: str = "COMPLETE") -> dict:
    return build_report(
        question_id="Q19",
        status=status,
        overall={"finding": "Synthetic deterministic finding"},
        confidence="HIGH",
        dataset={"source": "structured evidence", "sample_size": 125},
        fingerprint=build_fingerprint_from_provenance(provenance),
        recommendation="MONITOR",
    )


def _legacy_current_report(question_id: str, source: str) -> dict:
    return {
        "question_id": question_id,
        "status": "COMPLETE",
        "overall": {"finding": "Existing modern CURRENT report"},
        "confidence": "HIGH",
        "dataset": {"source": source, "sample_size": 125},
        "fingerprint": {
            "epoch": "CURRENT",
            "architecture_version": "new_pipeline_v1.2",
            "records_used": 125,
            "records_excluded": 0,
            "source": source,
        },
        "epoch": "CURRENT",
        "recommendation": "MONITOR",
        "warnings": [],
    }


def test_current_authoritative_evidence_produces_current_provenance():
    selection = use_evidence_as_supplied(
        "management_actions_v1", [_action("a"), _action("b")]
    )
    provenance = build_evidence_provenance(selection)

    assert selection.records_for_analysis() == [_action("a"), _action("b")]
    assert provenance["state"] == CURRENT
    assert provenance["records_used"] == 2
    assert validate_evidence_provenance(provenance)[:2] == (True, CURRENT)


def test_historical_used_evidence_is_stale_and_missing_is_unverified():
    stale = build_evidence_provenance(
        use_evidence_as_supplied("management_actions_v1", [_action("old", epoch="LEGACY")])
    )
    missing = build_evidence_provenance(
        use_evidence_as_supplied("management_actions_v1", [])
    )

    assert stale["state"] == STALE
    assert missing["state"] == UNVERIFIED


def test_mixed_evidence_used_together_fails_closed():
    selection = use_evidence_as_supplied(
        "management_actions_v1",
        [_action("new"), _action("old", epoch="LEGACY")],
    )
    provenance = build_evidence_provenance(selection)

    assert selection.component["records_used"] == 2
    assert provenance["state"] == MIXED
    assert build_fingerprint_from_provenance(provenance)["epoch"] == UNVERIFIED


def test_authoritative_current_selection_excludes_and_counts_history():
    selection = select_current_evidence(
        "management_actions_v1",
        [_action("new"), _action("old", epoch="LEGACY")],
    )
    provenance = build_evidence_provenance(selection)

    assert selection.records_for_analysis() == [_action("new")]
    assert selection.component["input_records"] == 2
    assert selection.component["records_used"] == 1
    assert selection.component["records_excluded"] == 1
    assert selection.component["epoch_counts"]["LEGACY"] == 1
    assert provenance["state"] == CURRENT


def test_provenance_digest_is_deterministic_and_population_sensitive():
    first = build_evidence_provenance(
        use_evidence_as_supplied("management_actions_v1", [_action("a"), _action("b")])
    )
    reordered = build_evidence_provenance(
        use_evidence_as_supplied("management_actions_v1", [_action("b"), _action("a")])
    )
    changed = build_evidence_provenance(
        use_evidence_as_supplied("management_actions_v1", [_action("a"), _action("c")])
    )

    assert first == reordered
    assert first["digest"] != changed["digest"]
    assert first["components"][0]["digest"] != changed["components"][0]["digest"]


def test_multi_source_requires_every_used_component_to_be_current():
    actions = use_evidence_as_supplied("management_actions_v1", [_action("a")])
    current_truth = use_evidence_as_supplied("trade_truth_v1", [_truth("a")])
    stale_truth = use_evidence_as_supplied(
        "trade_truth_v1", [_truth("a", epoch="LEGACY")]
    )

    assert build_evidence_provenance(actions, current_truth)["state"] == CURRENT
    assert build_evidence_provenance(actions, stale_truth)["state"] == MIXED
    assert [
        item["source"] for item in build_evidence_provenance(actions, current_truth)["components"]
    ] == ["management_actions_v1", "trade_truth_v1"]


def test_false_source_label_cannot_convert_stale_or_incompatible_evidence():
    stale = use_evidence_as_supplied(
        "management_actions_v1", [_action("old", epoch="LEGACY")]
    )
    falsely_labelled = use_evidence_as_supplied(
        "management_actions_v1",
        [{"schema_version": "shadow_trades_v1", "epoch": "CURRENT"}],
    )

    assert stale.component["state"] == STALE
    assert falsely_labelled.component["state"] == INCOMPATIBLE


def test_legacy_fingerprint_omission_remains_unverified():
    assert build_fingerprint(10, 0, "shadow_trades")["epoch"] == UNVERIFIED


def test_structured_current_resolves_and_non_current_fails_closed():
    current = build_evidence_provenance(
        use_evidence_as_supplied("management_actions_v1", [_action("a")])
    )
    stale = build_evidence_provenance(
        use_evidence_as_supplied(
            "management_actions_v1", [_action("old", epoch="LEGACY")]
        )
    )

    validity, _ = resolve_report_validity(
        "q19_expected_value.json",
        _structured_report(current),
        expected_question_id="E1",
        accepted_question_ids=("Q19",),
    )
    stale_validity, _ = resolve_report_validity(
        "q19_expected_value.json",
        _structured_report(stale),
        expected_question_id="E1",
        accepted_question_ids=("Q19",),
    )

    assert validity == ReportValidity.VALID_CURRENT
    assert stale_validity == ReportValidity.STALE


def test_tampered_structured_provenance_is_invalidated():
    provenance = build_evidence_provenance(
        use_evidence_as_supplied("management_actions_v1", [_action("a")])
    )
    report = _structured_report(provenance)
    report["fingerprint"]["evidence_provenance"]["records_used"] = 999

    validity, _ = resolve_report_validity(
        "q19_expected_value.json", report,
        expected_question_id="E1", accepted_question_ids=("Q19",),
    )
    assert validity == ReportValidity.INVALIDATED


def test_tampered_fingerprint_summary_is_invalidated():
    provenance = build_evidence_provenance(
        use_evidence_as_supplied("management_actions_v1", [_action("a")])
    )
    report = _structured_report(provenance)
    report["fingerprint"]["records_used"] = 999

    validity, _ = resolve_report_validity(
        "q19_expected_value.json", report,
        expected_question_id="E1", accepted_question_ids=("Q19",),
    )
    assert validity == ReportValidity.INVALIDATED


def test_existing_modern_current_reports_remain_compatible():
    e1_validity, _ = resolve_report_validity(
        "q19_expected_value.json",
        _legacy_current_report("Q19", "shadow_trades"),
        expected_question_id="E1",
        accepted_question_ids=("Q19",),
    )
    risk_validity, _ = resolve_report_validity(
        "w5_risk1_control_fidelity.json",
        _legacy_current_report("RISK-1", "risk_deviation_v1"),
        expected_question_id="RISK-1",
    )

    assert e1_validity == ReportValidity.VALID_CURRENT
    assert risk_validity == ReportValidity.VALID_CURRENT


def test_valid_current_complete_readiness_is_unchanged():
    question = SimpleNamespace(id="TEST", depends_on=())
    evidence = EvidenceResolution(
        question_id="TEST", sources=[], usable_count=0, excluded_count=0,
        metrics={}, requirements=[],
    )

    readiness, _ = resolve_readiness(
        question, evidence, RunnerStatus.READY, ReportValidity.VALID_CURRENT,
        "COMPLETE", {},
    )
    assert readiness == ReadinessStatus.COMPLETE


def test_structured_current_cannot_override_invalid_scientific_gates():
    provenance = build_evidence_provenance(
        use_evidence_as_supplied("management_actions_v1", [_action("a")])
    )
    report = _structured_report(provenance)
    report["warnings"].append("EPOCH_WARNING: deliberately invalid population")

    validity, _ = resolve_report_validity(
        "q19_expected_value.json", report,
        expected_question_id="E1", accepted_question_ids=("Q19",),
    )
    assert validity == ReportValidity.INVALIDATED


def test_mutating_returned_analysis_copy_does_not_change_provenance():
    selection = use_evidence_as_supplied("management_actions_v1", [_action("a")])
    before = deepcopy(build_evidence_provenance(selection))
    analysis_records = selection.records_for_analysis()
    analysis_records[0]["trade_id"] = "changed"

    assert build_evidence_provenance(selection) == before
