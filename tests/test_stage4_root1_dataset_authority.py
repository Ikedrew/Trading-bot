"""ROOT CHANGE 1 -- shadow_runtime_v1 dataset-authority focused tests.

Scope: governance / authority / contract / validation only.  These tests prove
the correction is truthful and that NO real observability gap was erased.  They
assert, in particular, that no producer, schema, S3, backfill, research
re-entry or Q71+ action occurred.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from research_engine.control_plane import gap_governance as G
from research_engine.control_plane import stage4_dataset_authority as A

CANON = A.CANONICAL_STATE_PATH
ARTIFACT_JSON = A.ARTIFACT_JSON_PATH
ARTIFACT_MD = A.ARTIFACT_MD_PATH
PRODUCER_MODULES = (
    "core/shadow/runtime.py", "core/shadow/persistence.py",
    "core/shadow/models.py", "core/shadow_trades.py",
)


@pytest.fixture(scope="module")
def store():
    return A.build_store()


@pytest.fixture(scope="module")
def canonical():
    return A.load_canonical_state()


# ---------------------------------------------------------------------------
# Dataset authority
# ---------------------------------------------------------------------------

def test_01_shadow_trades_zero_objects_cannot_be_authoritative(store):
    prior = store["dataset_authority"]["supersedes_observation_reference"]
    assert prior["dataset"] == "shadow_trades"
    assert prior["persisted_objects"] == 0
    assert prior["persisted_rows"] == 0
    assert prior["authoritative_for_current_contracts"] is False
    assert A.is_authoritative_current_evidence("shadow_trades") is False
    assert "shadow_trades" not in G.authoritative_evidence_datasets()


def test_02_shadow_runtime_is_the_selected_authority(store):
    authority = store["dataset_authority"]
    assert authority["canonical_observation_source"] == "shadow_runtime"
    assert authority["dataset_version"] == "shadow_runtime_v1"
    assert authority["persisted_rows"] > 0
    assert authority["producer"] == (
        "core/shadow/persistence.py + core/shadow/runtime.py")
    assert A.is_authoritative_current_evidence("shadow_runtime") is True
    assert G.authoritative_evidence_datasets() == ("shadow_runtime",)


def test_03_shadow_runtime_selected_only_for_audited_requirements(store):
    audited = set(store["affected_observation_requirements"])
    for transition in store["observation_requirement_transitions"]:
        assert transition["observation_requirement_id"] in audited
        # A requirement is re-pointed ONLY where the audit proved it.
        assert transition["audited_authoritative_dataset"] == "shadow_runtime"
    # Requirements the audit did not place under this root change keep their
    # own audited authority (e.g. decision_trace for OR-08/OR-09).
    assert A.authoritative_dataset_for("OR-08", store) == "decision_trace"
    assert A.authoritative_dataset_for("OR-10", store) == "horizon_candidates"
    for rid in audited:
        assert A.authoritative_dataset_for(rid, store) == "shadow_runtime"


def test_04_shadow_trades_history_is_preserved(store):
    history = A.dataset_history("shadow_trades")
    assert history["dataset"] == "shadow_trades"
    assert history["declared_in_production_contract"] is True
    assert history["writer_module_exists"] == "core/shadow_trades.py"
    assert history["persistence_state"] == "DECLARED_NEVER_EMITTED"
    assert history["history_preserved"] is True
    # History is preserved, not erased: the production contract still declares
    # it and the register still recorded it.
    from core.production_data_contract import PRODUCTION_SCHEMA_REGISTRY
    assert "shadow_trades" in PRODUCTION_SCHEMA_REGISTRY
    assert PRODUCTION_SCHEMA_REGISTRY["shadow_trades"].current == (
        "shadow_trades_v1")


def test_05_no_dataset_history_mutation(canonical, store):
    assert canonical["store_fingerprint"] == store["store_fingerprint"]
    # The frozen gap-governance store is untouched by Root 1.
    gap_state = G.load_dataset_authority()
    assert gap_state["dataset_authority"]["dataset_version"] == (
        "shadow_runtime_v1")
    prior = gap_state["dataset_authority"][
        "supersedes_observation_reference"]
    assert prior["dataset_version"] == "shadow_trades_v1"
    frozen = json.loads(
        Path("research_engine/control_plane/gap_governance_state.json")
        .read_text(encoding="utf-8"))
    G.validate_store(frozen)
    assert frozen["unique_work_items"] == 37


# ---------------------------------------------------------------------------
# Gap preservation -- source correction alone satisfies NOTHING.
# ---------------------------------------------------------------------------

def _transition(store, rid):
    return A.transition_for(rid, store)


def test_06_correction_alone_cannot_satisfy_missing_field(store):
    t = _transition(store, "OR-15")
    assert t["satisfaction_gates"]["field_exists"] is False
    assert t["satisfies_current_contract"] is False
    assert t["shadow_runtime_classification"] == A.FIELD_GAP_REMAINS
    assert t["gap_disposition_after_correction"] == A.DISPOSITION_UNRESOLVED
    assert t["blocked_by_root_change"] == A.ROOT_CHANGE_05
    assert "FIELD" in t["remaining_gap_kinds"]


def test_07_correction_alone_cannot_satisfy_partial_coverage(store):
    for rid in ("OR-03", "OR-04", "OR-05"):
        t = _transition(store, rid)
        assert t["satisfaction_gates"]["coverage_completeness_met"] is False
        assert t["satisfies_current_contract"] is False
        assert t["shadow_runtime_classification"] == A.COVERAGE_GAP_REMAINS
        assert t["gap_disposition_after_correction"] == A.DISPOSITION_UNRESOLVED
        assert t["blocked_by_root_change"] == A.ROOT_CHANGE_02
        assert "COVERAGE" in t["remaining_gap_kinds"]


def test_08_correction_alone_cannot_satisfy_wrong_semantics(store):
    t = _transition(store, "OR-02")
    gates = t["satisfaction_gates"]
    assert gates["semantics_match"] is False
    assert gates["grain_match"] is False
    assert t["satisfies_current_contract"] is False
    assert t["shadow_runtime_classification"] == A.SEMANTIC_GAP_REMAINS
    assert t["blocked_by_root_change"] == A.ROOT_CHANGE_02
    assert "SEMANTIC" in t["remaining_gap_kinds"]


def test_09_correction_alone_cannot_satisfy_missing_lineage(store):
    t = _transition(store, "OR-15")
    assert t["satisfaction_gates"]["lineage_requirement_matches"] is False
    assert t["satisfies_current_contract"] is False
    assert "LINEAGE" in t["remaining_gap_kinds"]
    t2 = _transition(store, "OR-02")
    assert t2["satisfaction_gates"]["lineage_requirement_matches"] is False


def test_10_wrong_timestamp_semantics_is_preserved(store):
    t = _transition(store, "OR-07")
    assert t["satisfaction_gates"]["timestamp_semantics_match"] is False
    assert t["satisfies_current_contract"] is False
    assert t["blocked_by_root_change"] == A.ROOT_CHANGE_03
    assert t["gap_disposition_after_correction"] == A.DISPOSITION_UNRESOLVED


def test_11_valid_present_observations_may_become_satisfied(store):
    for rid in ("OR-01", "OR-06"):
        t = _transition(store, rid)
        assert all(t["satisfaction_gates"][g]
                   for g in A.SATISFACTION_GATES)
        assert t["failing_gates"] == []
        assert t["satisfies_current_contract"] is True
        assert t["shadow_runtime_classification"] == A.FULLY_SATISFIED
        assert t["gap_disposition_after_correction"] == A.DISPOSITION_SATISFIED
        assert t["blocked_by_root_change"] is None


def test_12_every_failing_gate_carries_an_evidence_reason(store):
    for t in store["observation_requirement_transitions"]:
        assert sorted(t["gate_failure_reasons"]) == sorted(t["failing_gates"])
        for reason in t["gate_failure_reasons"].values():
            assert reason.strip()


def test_13_satisfaction_flag_must_be_gate_derived(store):
    bad = copy.deepcopy(store)
    target = next(t for t in bad["observation_requirement_transitions"]
                  if t["observation_requirement_id"] == "OR-15")
    target["satisfies_current_contract"] = True
    with pytest.raises(A.DatasetAuthorityError,
                       match="SATISFACTION_NOT_GATE_DERIVED"):
        A.validate_store(bad)


def test_14_satisfied_requirement_must_not_keep_a_blocker(store):
    bad = copy.deepcopy(store)
    target = next(t for t in bad["observation_requirement_transitions"]
                  if t["observation_requirement_id"] == "OR-01")
    target["blocked_by_root_change"] = A.ROOT_CHANGE_02
    with pytest.raises(A.DatasetAuthorityError,
                       match="SATISFIED_MUST_NOT_BLOCK"):
        A.validate_store(bad)


def test_15_incomplete_gate_set_fails_closed(store):
    bad = copy.deepcopy(store)
    bad["observation_requirement_transitions"][0][
        "satisfaction_gates"].pop("lineage_requirement_matches")
    with pytest.raises(A.DatasetAuthorityError,
                       match="GATE_SET_INCOMPLETE"):
        A.validate_store(bad)


# ---------------------------------------------------------------------------
# Dependency mapping
# ---------------------------------------------------------------------------

def test_16_every_affected_gap_is_satisfied_or_root_blocked(store):
    for gap in store["affected_gap_dispositions"]:
        satisfied = gap["disposition"] == A.DISPOSITION_SATISFIED
        if satisfied:
            assert gap["blocked_by_root_changes"] == []
            assert gap["blocking_requirements"] == []
        else:
            assert gap["blocking_requirements"]
            assert gap["blocked_by_root_changes"]
            assert set(gap["blocked_by_root_changes"]).issubset(
                set(A.ROOT_CHANGES))


def test_17_no_gap_becomes_unowned(store):
    for gap in store["affected_gap_dispositions"]:
        assert gap["owner_identifier"].startswith(("producer:", "runner:"))
        assert gap["owning_module"]
    assert store["conservation"]["gaps_without_owner"] == 0


def test_18_unresolved_gap_without_blocker_fails_closed(store):
    bad = copy.deepcopy(store)
    target = next(g for g in bad["affected_gap_dispositions"]
                  if g["disposition"] == A.DISPOSITION_UNRESOLVED)
    target["blocked_by_root_changes"] = []
    with pytest.raises(A.DatasetAuthorityError,
                       match="UNOWNED_UNRESOLVED_GAP"):
        A.validate_store(bad)


def test_19_no_observation_requirement_disappears(store):
    conservation = store["conservation"]
    assert conservation["observation_requirements_lost"] == 0
    assert conservation["observation_requirement_ids_lost"] == []
    repointed = set(conservation["repointed_observation_requirements"])
    retained = set(conservation["retained_untouched_observation_requirements"])
    assert not (repointed & retained)
    assert repointed | retained == set(
        store["audited_observation_requirements"])


def test_20_dropping_a_requirement_fails_closed(store):
    bad = copy.deepcopy(store)
    bad["observation_requirement_transitions"].pop()
    with pytest.raises(A.DatasetAuthorityError):
        A.validate_store(bad)


def test_21_affected_set_is_derived_and_governed(store):
    loaded = A.load_audit()
    requirements = A.root1_requirements(loaded)
    assert requirements == store["affected_observation_requirements"]
    assert A.affected_gap_ids(loaded, requirements) == store["affected_gap_ids"]
    assert store["conservation"]["unaccounted_gaps"] == 0
    # Headline reconciliation: 27 register gaps + 1 implementation observation
    # gap (L7) = 28 affected governed items.
    conservation = store["conservation"]
    assert conservation["affected_data_schema_gap_count"] == 27
    assert conservation["affected_implementation_observation_gap_count"] == 1
    assert (conservation["affected_data_schema_gap_count"]
            + conservation["affected_implementation_observation_gap_count"]
            == store["affected_gap_count"])


def test_22_remaining_root_changes_are_the_named_roots(store):
    by_root = store["gaps_by_root_change"]
    assert by_root[A.ROOT_CHANGE_01] == []
    assert set(by_root[A.ROOT_CHANGE_02])
    assert set(by_root[A.ROOT_CHANGE_03])
    assert by_root[A.ROOT_CHANGE_04] == []  # EX2 is not a Root-1 requirement
    assert by_root[A.ROOT_CHANGE_05] == ["L7 (OG-L7-4db003c3ab76)"]
    assert store["next_root_change"] == A.ROOT_CHANGE_02


def test_23_gap_authority_is_reachable_through_gap_governance(store):
    resolved = G.current_evidence_authority("STAGE4-DATA-M3", store)
    assert resolved["previous_dataset_reference"] == "shadow_trades"
    assert resolved["current_dataset"] == "shadow_runtime"
    assert resolved["current_dataset_version"] == "shadow_runtime_v1"
    assert resolved["disposition"] == A.DISPOSITION_UNRESOLVED
    with pytest.raises(G.GapGovernanceError,
                       match="NO_DATASET_AUTHORITY_FOR_GAP"):
        G.current_evidence_authority("STAGE4-DATA-NOT-REAL", store)


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------

def test_24_no_producer_change(store):
    ledger = store["mutation_ledger"]
    assert ledger["producer_mutations"] == 0
    assert ledger["new_producers_deployed"] == 0
    assert ledger["new_observables_added"] == 0
    assert store["producer_files_modified"] == []
    authority = store["dataset_authority"]
    assert authority["producer_changed_by_this_pass"] is False
    assert authority["schema_changed_by_this_pass"] is False
    assert authority["dataset_version_changed_by_this_pass"] is False


def test_25_no_s3_write_no_backfill_no_reentry(store):
    ledger = store["mutation_ledger"]
    assert ledger["s3_writes"] == 0
    assert ledger["backfills_performed"] == 0
    assert ledger["research_reentry_events"] == 0
    assert ledger["q71_started"] is False
    assert ledger["q71_started_count"] == 0
    for t in store["observation_requirement_transitions"]:
        assert t["research_reentry_triggered"] is False
        assert t["scientific_finding_version_changed"] is False
        assert t["scientific_result_version_changed"] is False
    for g in store["affected_gap_dispositions"]:
        assert g["research_reentry_triggered"] is False
        assert g["old_data_remains_valid"] is True


def test_26_no_q71_started(store):
    assert store["mutation_ledger"]["q71_started"] is False
    with pytest.raises(G.GapGovernanceError, match="Q71_NOT_STARTED"):
        G.create_q71_gap(["anything"], ["shadow_trades"])


def test_27_no_scientific_version_churn(store):
    # shadow_runtime_v1 is preserved: this pass never mints a v2.
    assert store["dataset_authority"]["dataset_version"] == "shadow_runtime_v1"
    for t in store["observation_requirement_transitions"]:
        assert t["dataset_version_changed"] is False
        assert t["producer_version_changed"] is False
        assert t["version_consequence"] == "CONTRACT_ONLY_CHANGE"
        assert t["dataset_schema_version_before"] == "shadow_trades_v1"
        assert t["dataset_schema_version_after"] == "shadow_runtime_v1"
    from core.shadow.models import SCHEMA_VERSION
    assert SCHEMA_VERSION == "shadow_runtime_v1"


def test_28_non_zero_mutation_ledger_fails_closed(store):
    bad = copy.deepcopy(store)
    bad["mutation_ledger"]["s3_writes"] = 1
    with pytest.raises(A.DatasetAuthorityError,
                       match="MUTATION_LEDGER_NONZERO"):
        A.validate_store(bad)
    bad2 = copy.deepcopy(store)
    bad2["mutation_ledger"]["q71_started"] = True
    with pytest.raises(A.DatasetAuthorityError, match="Q71_STARTED"):
        A.validate_store(bad2)


def test_29_authority_module_touches_no_producer(store):
    """This module must not import, write or introspect any producer path."""
    text = Path("research_engine/control_plane/"
                "stage4_dataset_authority.py").read_text(encoding="utf-8")
    for forbidden in ("put_object", "boto3", "s3_client", "jsonl",
                      "import core.shadow", "from core.shadow"):
        assert forbidden not in text


def test_30_artifacts_persisted_and_consistent(canonical):
    assert CANON.exists() and ARTIFACT_JSON.exists() and ARTIFACT_MD.exists()
    artifact = json.loads(ARTIFACT_JSON.read_text(encoding="utf-8"))
    assert artifact["store_fingerprint"] == canonical["store_fingerprint"]
    assert artifact["dataset_authority"] == canonical["dataset_authority"]
    A.validate_store(artifact)
    md = ARTIFACT_MD.read_text(encoding="utf-8")
    assert "Root Change 1" in md and "shadow_runtime_v1" in md
    # The original observation audit artifacts are never overwritten.
    assert A.AUDIT_PATH.exists()
    assert A.REQUIREMENT_MATRIX_PATH.exists()
