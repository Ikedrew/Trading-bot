"""Focused Repair 4B.2 tests for governed X3 session execution quality."""
from __future__ import annotations

from copy import deepcopy
import json
import math

import pytest

from research_engine.control_plane.evidence_provenance import CURRENT
from research_engine.control_plane.models import ReadinessStatus, ReportValidity, RunnerStatus
from research_engine.control_plane.report_ownership import (
    canonical_report_owner,
    resolve_report_ownership,
)
from research_engine.control_plane.report_resolver import resolve_report_validity
from research_engine.control_plane.state_builder import build_question_state
from research_engine.experiments.execution_protection_research import run_x3
from research_engine.experiments.x3_session_quality import (
    ALPHA,
    REPORT_FILENAME,
    _holm_adjust,
    build_x3_report,
    unique_supported_best,
)
from research_engine.registry.baseline_manifest import BASELINE_QUESTION_IDS
from research_engine.registry.definition_validator import build_definitions_from_registry
from research_engine.registry.master_repair_ledger import (
    MASTER_REPAIR_LEDGER,
    STRUCTURALLY_OPERATIONAL_IDS,
    operational_baseline,
)
from research_engine.registry.research_question_registry import REGISTRY, REGISTRY_BY_ID
from research_engine.registry.wave_a_no_runner_definitions import (
    WAVE_A_NO_RUNNER_TARGETS,
    X6_HD08_ADJUDICATED_CONTRACT,
)


class _EmptyCandidates:
    def list_all(self):
        return []


def _result(
    correlation: str,
    account: str,
    slippage: float | None,
    *,
    result_ok=True,
    semantic: str = "measured_execution_slippage",
    epoch: str = "CURRENT",
) -> dict:
    index = abs(hash((correlation, account))) % 1_000_000 + 1
    return {
        "schema_version": "execution_results_v1",
        "data_epoch": epoch,
        "correlation_id": correlation,
        "canonical_opportunity_id": f"OPP-{correlation}",
        "decision_id": f"DEC-{correlation}",
        "entity_id": f"ENTITY-{correlation}",
        "symbol": "EURUSD",
        "account_id": account,
        "broker": "Broker",
        "broker_server": "Server",
        "broker_symbol": "EURUSD.raw",
        "order_ticket": index,
        "position_ticket": index,
        "result_ok": result_ok,
        "retcode": 10009 if result_ok is True else 10030,
        "comment": "filled" if result_ok is True else "rejected",
        "slippage": slippage,
        "slippage_semantic": semantic,
        "entry_reference": 1.1,
        "fill_price": 1.2,
    }


def _context(correlation: str, session: str, *, epoch: str = "CURRENT") -> dict:
    return {
        "schema_version": "execution_context_v1",
        "data_epoch": epoch,
        "correlation_id": correlation,
        "canonical_opportunity_id": f"OPP-{correlation}",
        "decision_id": f"DEC-{correlation}",
        "entity_id": f"ENTITY-{correlation}",
        "symbol": "EURUSD",
        "timestamp_utc": "2026-09-22T10:00:00Z",
        "market_access": {
            "session_state": session,
            "spread": 0.0001,
            "spread_atr_ratio": 0.2,
        },
    }


def _population(
    session_bases: dict[str, float], *, clusters_per_session: int = 15,
    accounts_per_cluster: int = 2,
) -> tuple[list[dict], list[dict]]:
    results: list[dict] = []
    contexts: list[dict] = []
    for session_index, (session, base) in enumerate(session_bases.items()):
        for cluster_index in range(clusters_per_session):
            correlation = f"COR-{session_index}-{cluster_index}"
            contexts.append(_context(correlation, session))
            cluster_noise = ((cluster_index % 5) - 2) * 0.001
            for account_index in range(accounts_per_cluster):
                account_noise = (account_index - (accounts_per_cluster - 1) / 2) * 0.0002
                results.append(_result(
                    correlation,
                    f"ACCOUNT-{account_index}",
                    base + cluster_noise + account_noise,
                    result_ok=False if cluster_index % 5 == 0 else True,
                ))
    return results, contexts


def _state(tmp_path, report: dict | None, results: list[dict], contexts: list[dict]):
    if report is not None:
        (tmp_path / REPORT_FILENAME).write_text(json.dumps(report), encoding="utf-8")
    return build_question_state(
        "X3",
        reports_dir=tmp_path,
        evidence_source={"execution_results_v1": results, "execution_context": contexts},
        candidate_registry=_EmptyCandidates(),
    )


def test_current_evidence_strict_join_and_stale_exclusion():
    results, contexts = _population({"LONDON": 0.01, "NY": 0.04})
    results.append({**_result("STALE", "A", 999.0), "data_epoch": "LEGACY"})
    contexts.append({**_context("STALE", "ASIA"), "data_epoch": "LEGACY"})
    unmatched = _result("NO-CONTEXT", "A", 999.0)
    results.append(unmatched)

    report = run_x3(results, contexts)

    assert report["status"] == "COMPLETE"
    accounting = report["overall"]["evidence_accounting"]
    assert accounting["components"]["execution_results_v1"]["stale_records_excluded"] == 1
    assert accounting["components"]["execution_context"]["stale_records_excluded"] == 1
    assert accounting["join_and_identity_exclusions"]["missing_context"] == 1
    assert report["fingerprint"]["epoch"] == CURRENT
    assert report["fingerprint"]["sources"] == ["execution_context", "execution_results_v1"]


def test_fanout_rows_remain_observations_but_each_cluster_has_total_weight_one():
    results, contexts = _population({"LONDON": 0.01, "NY": 0.04})
    report = build_x3_report(results, contexts)
    population = report["overall"]["analytical_population"]
    assert population["analytical_account_results"] == 60
    assert population["analytical_distinct_decisions"] == 30
    weights = report["overall"]["estimator"]["cluster_total_weights"]
    assert len(weights) == 30
    assert all(math.isclose(value, 1.0) for value in weights.values())


def test_slippage_authority_absolute_value_and_no_reconstruction():
    results, contexts = _population({"LONDON": 0.01, "NY": 0.04})
    results[0]["slippage"] = -0.02
    results[1]["slippage_semantic"] = "unknown"
    results[1]["slippage"] = 0.9
    results[2]["slippage"] = float("nan")
    results[3]["slippage"] = float("inf")
    results[4]["slippage"] = None
    results[4]["entry_reference"] = 1.0
    results[4]["fill_price"] = 9.0
    report = build_x3_report(results, contexts)
    london = report["overall"]["sessions"]["LONDON"]
    assert london["measured_slippage_eligible_n"] == 26
    assert london["mean_absolute_measured_slippage"] < 0.1


def test_session_comes_only_from_context_and_unknown_is_counted():
    results, contexts = _population({"LONDON": 0.01, "NY": 0.04})
    contexts[0]["market_access"]["session_state"] = "UNKNOWN"
    results[0]["session_state"] = "ASIA"
    results[0]["timestamp_utc"] = "2026-09-22T01:00:00Z"
    report = build_x3_report(results, contexts)
    assert "ASIA" not in report["overall"]["sessions"]
    assert report["overall"]["analytical_population"]["unknown_or_missing_session_results"] == 2


@pytest.mark.parametrize(
    ("session_bases", "clusters", "accounts", "expected_reason"),
    [
        ({"LONDON": 0.01}, 14, 2, "overall"),
        ({"LONDON": 0.01}, 15, 2, "two_sessions"),
        ({"LONDON": 0.01, "NY": 0.04}, 9, 4, "decisions"),
    ],
)
def test_sufficiency_gates_fail_closed(session_bases, clusters, accounts, expected_reason):
    results, contexts = _population(
        session_bases, clusters_per_session=clusters, accounts_per_cluster=accounts,
    )
    report = build_x3_report(results, contexts)
    assert report["status"] == "INSUFFICIENT_DATA"
    assert report["overall"]["classification"] == "INSUFFICIENT_EVIDENCE"
    sufficiency = report["overall"]["sufficiency"]
    if expected_reason == "overall":
        assert not sufficiency["overall_sufficient"]
    elif expected_reason == "two_sessions":
        assert not sufficiency["at_least_two_primary_sufficient_sessions"]
    else:
        assert sufficiency["primary_sufficient_session_count"] == 0


def test_exact_30_result_10_decision_boundaries_are_sufficient():
    results, contexts = _population(
        {"LONDON": 0.01, "NY": 0.04}, clusters_per_session=10, accounts_per_cluster=3,
    )
    report = build_x3_report(results, contexts)
    assert report["status"] == "COMPLETE"
    assert all(item["primary_sufficient"] for item in report["overall"]["sessions"].values())


def test_equal_session_population_is_valid_completed_null_and_reorder_invariant():
    results, contexts = _population({"LONDON": 0.02, "NY": 0.02})
    forward = build_x3_report(results, contexts)
    reverse = build_x3_report(list(reversed(results)), list(reversed(contexts)))
    assert forward["status"] == "COMPLETE"
    assert forward["overall"]["classification"] == "NO_RELIABLE_SESSION_SLIPPAGE_DIFFERENCE"
    assert forward["overall"]["global_primary_test"]["degrees_of_freedom"] == 1
    assert forward["overall"]["global_primary_test"]["alpha"] == ALPHA
    assert forward["overall"] == reverse["overall"]
    assert forward["fingerprint"]["digest"] if "digest" in forward["fingerprint"] else True
    assert forward["fingerprint"]["dataset_id"] == reverse["fingerprint"]["dataset_id"]


def test_differing_population_rejects_and_supports_unique_lower_session():
    results, contexts = _population({"LONDON": 0.01, "NY": 0.08})
    report = build_x3_report(results, contexts)
    overall = report["overall"]
    assert report["status"] == "COMPLETE"
    assert overall["classification"] == "RELIABLE_SESSION_SLIPPAGE_DIFFERENCE"
    assert overall["global_primary_test"]["p_value"] <= ALPHA
    assert overall["unique_best_session"] == "LONDON"
    pair = overall["pairwise_primary_contrasts"]["results"][0]
    assert pair["inferential_claim_permitted"]
    assert pair["holm_adjusted_p_value"] <= ALPHA
    assert pair["interval_95"]["method"] == "correlation_id_clustered_sandwich"


def test_three_session_pairwise_family_holm_and_omnibus_gating():
    results, contexts = _population({"ASIA": 0.01, "LONDON": 0.02, "NY": 0.03})
    report = build_x3_report(results, contexts)
    family = report["overall"]["pairwise_primary_contrasts"]
    assert family["declared_count"] == 3
    assert len(family["results"]) == 3
    assert family["multiplicity"] == "one global Holm step-down family"
    assert family["omnibus_in_holm_family"] is False
    adjusted = _holm_adjust([0.01, 0.04, 0.03, 0.01])
    assert adjusted == pytest.approx([0.04, 0.06, 0.06, 0.04])

    equal_results, equal_contexts = _population({"ASIA": 0.02, "LONDON": 0.02, "NY": 0.02})
    null_family = build_x3_report(equal_results, equal_contexts)["overall"]["pairwise_primary_contrasts"]
    assert all(item["holm_adjusted_p_value"] is None for item in null_family["results"])
    assert all(not item["inferential_claim_permitted"] for item in null_family["results"])


def test_unique_best_requires_supported_win_over_every_session():
    sessions = ("ASIA", "LONDON", "NY")
    partial = [
        {"left_session": "ASIA", "right_session": "LONDON", "supported_lower_session": "ASIA", "inferential_claim_permitted": True},
        {"left_session": "ASIA", "right_session": "NY", "supported_lower_session": None, "inferential_claim_permitted": False},
        {"left_session": "LONDON", "right_session": "NY", "supported_lower_session": None, "inferential_claim_permitted": False},
    ]
    assert unique_supported_best(sessions, partial) == "NO_UNIQUE_SUPPORTED_BEST"
    partial[1].update({"supported_lower_session": "ASIA", "inferential_claim_permitted": True})
    assert unique_supported_best(sessions, partial) == "ASIA"


def test_singular_cluster_covariance_is_insufficient_not_a_null_result():
    results, contexts = _population({"LONDON": 0.01, "NY": 0.04})
    for result in results:
        result["slippage"] = 0.01 if result["correlation_id"].startswith("COR-0") else 0.04
    report = build_x3_report(results, contexts)
    assert report["status"] == "INSUFFICIENT_DATA"
    assert report["overall"]["classification"] == "INSUFFICIENT_EVIDENCE"


def test_rejection_tri_state_sufficiency_and_clustered_interval_are_descriptive():
    results, contexts = _population({"LONDON": 0.01, "NY": 0.04})
    results[0]["result_ok"] = None
    results[1]["result_ok"] = "false"
    report = build_x3_report(results, contexts)
    london = report["overall"]["sessions"]["LONDON"]
    assert london["unknown_status_n"] == 2
    assert london["known_status_n"] == 28
    assert london["failures"] == 4
    assert not london["rejection_sufficient"]
    assert london["endpoint_state"] == "PRIMARY_SUFFICIENT"
    assert "clustered_sandwich" in london["rejection_interval_95"]["method"]
    secondary = report["overall"]["secondary_rejection_endpoint"]
    assert not secondary["pairwise_hypothesis_tests_performed"]
    assert not secondary["composite_score_used"]
    assert not secondary["can_override_primary_classification"]


def test_exact_primary_subset_provenance_is_current_change_sensitive_and_stale_safe():
    results, contexts = _population({"LONDON": 0.01, "NY": 0.04})
    original = build_x3_report(results, contexts)
    changed = deepcopy(results)
    changed[0]["slippage"] += 0.005
    altered = build_x3_report(changed, contexts)
    assert original["fingerprint"]["epoch"] == CURRENT
    assert original["fingerprint"]["dataset_id"] != altered["fingerprint"]["dataset_id"]
    assert original["fingerprint"]["records_used"] == 90  # 60 results + 30 contexts

    stale = [{**row, "data_epoch": "LEGACY"} for row in results]
    stale_report = build_x3_report(stale, contexts)
    assert stale_report["status"] == "INSUFFICIENT_DATA"
    assert stale_report["fingerprint"]["epoch"] != CURRENT


def test_report_ownership_is_x3_only_and_relabelling_fails_closed():
    assert canonical_report_owner(REPORT_FILENAME) == "X3"
    assert resolve_report_ownership(REPORT_FILENAME, "X3").allowed
    assert not resolve_report_ownership(REPORT_FILENAME, "Q9").allowed
    report = build_x3_report(*_population({"LONDON": 0.01, "NY": 0.04}))
    relabelled = {**report, "question_id": "EXEC1"}
    validity, _ = resolve_report_validity(
        REPORT_FILENAME, relabelled, expected_question_id="X3", accepted_question_ids=("Q9",),
    )
    assert validity == ReportValidity.INVALIDATED


def test_readiness_missing_insufficient_reliable_and_null(tmp_path):
    results, contexts = _population({"LONDON": 0.01, "NY": 0.08})
    missing = _state(tmp_path, None, results, contexts)
    assert missing.runner_status == RunnerStatus.READY
    assert missing.report_validity == ReportValidity.MISSING
    assert missing.readiness_status == ReadinessStatus.BLOCKED

    one_result, one_context = _population({"LONDON": 0.01}, clusters_per_session=1)
    insufficient_report = build_x3_report(one_result, one_context)
    insufficient = _state(tmp_path, insufficient_report, one_result, one_context)
    assert insufficient.report_validity == ReportValidity.VALID_CURRENT
    assert insufficient.readiness_status == ReadinessStatus.WAITING_DATA
    assert insufficient.latest_finding == ""

    reliable_report = build_x3_report(results, contexts)
    reliable = _state(tmp_path, reliable_report, results, contexts)
    assert reliable.readiness_status == ReadinessStatus.COMPLETE

    null_results, null_contexts = _population({"LONDON": 0.02, "NY": 0.02})
    null_state = _state(tmp_path, build_x3_report(null_results, null_contexts), null_results, null_contexts)
    assert null_state.readiness_status == ReadinessStatus.COMPLETE
    assert null_state.latest_result["unique_best_session"] == "NO_UNIQUE_SUPPORTED_BEST"


def test_registry_ledger_and_non_targets_remain_bounded():
    assert REGISTRY_BY_ID["X3"].description == (
        "Which trading sessions produce the best execution quality (lowest slippage, fewest rejects)?"
    )
    assert REGISTRY_BY_ID["X3"].runner_function == "run_x3"
    assert REGISTRY_BY_ID["X3"].report_filename == REPORT_FILENAME
    assert MASTER_REPAIR_LEDGER["X3"].structurally_operational
    assert "X3" in STRUCTURALLY_OPERATIONAL_IDS
    assert not MASTER_REPAIR_LEDGER["EXEC1"].structurally_operational
    assert not MASTER_REPAIR_LEDGER["X6"].structurally_operational
    assert not REGISTRY_BY_ID["X6"].runner_module
    assert not REGISTRY_BY_ID["X6"].runner_function
    assert X6_HD08_ADJUDICATED_CONTRACT["status"] == "ADJUDICATED"
    assert operational_baseline() == (46, 24)
    assert WAVE_A_NO_RUNNER_TARGETS == {"X6", "L6", "G1", "G2", "G3"}
    assert len(REGISTRY) == len({question.id for question in REGISTRY}) == 70
    assert tuple(question.id for question in REGISTRY) == BASELINE_QUESTION_IDS
    assert {definition.definition_version for definition in build_definitions_from_registry(REGISTRY).values()} == {1}

