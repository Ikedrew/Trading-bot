"""ROOT CHANGE 1 -- shadow_runtime_v1 canonical observation-source authority.

This module is a GOVERNANCE / CONTROL-PLANE correction only.  It removes the
false ``shadow_trades`` evidence-source reference from current Stage 4
observation governance and re-points the affected observation requirements at
the real persisted producer authority ``shadow_runtime_v1``.

It is emphatically NOT:

  * a producer change (no file under ``core/shadow/`` is touched),
  * a schema change (no dataset generation is created or bumped),
  * an S3 write / backfill / historical migration,
  * a scientific re-entry or a Q71+ start.

Design principles enforced here (fail closed):

  1. A requirement may only be re-pointed at ``shadow_runtime_v1`` when the
     persisted evidence actually satisfies its current observation contract.
  2. Correcting the dataset mapping may NEVER by itself satisfy a requirement.
     Seven independent gates must ALL pass before a requirement -- and hence a
     governed gap -- may become ``SATISFIED_CURRENTLY``:
     field, semantics, grain, canonical identity, timestamp semantics, lineage,
     coverage/completeness.  Any failing gate leaves the gap unresolved and
     attaches it to a named remaining root change.
  3. ``shadow_trades`` history is preserved verbatim.  It remains a declared
     dataset; it is simply recorded as ZERO-OBJECT / NOT-AUTHORITATIVE for the
     affected requirements.  No silent history rewrite.

The dataset identity/version authority itself is NOT duplicated here: canonical
dataset facts are read from ``core.production_data_contract``
(``PRODUCTION_SCHEMA_REGISTRY``), the existing authority.  This module only
supplies the *observation authority overlay* and the root-change dependency map.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from core.production_data_contract import PRODUCTION_SCHEMA_REGISTRY
from research_engine.control_plane.assured_epistemic_findings import (
    EXPECTED_CERTIFICATION_FINGERPRINT,
)
from research_engine.control_plane import stage4_dataset_snapshot as D
from research_engine.control_plane import stage4_identity as I
from research_engine.control_plane import stage4_satisfaction as SD

# ---------------------------------------------------------------------------
# Authorities consumed (read-only).  The completed Stage 4 observation/dataset
# audit is the upstream authority; nothing here regenerates it.
# ---------------------------------------------------------------------------

AUDIT_PATH = Path(
    "analysis/assurance/stage4_observation_dataset_audit_20260929.json")
REQUIREMENT_MATRIX_PATH = Path(
    "analysis/assurance/stage4_observation_requirement_matrix_20260929.json")
GAP_GOVERNANCE_PATH = Path(
    "analysis/assurance/stage4_gap_governance_20260929.json")
DATASET_GAP_REGISTER_PATH = Path(
    "analysis/assurance/stage4_dataset_schema_gap_register_20260928.json")

#: Persisted (read-only) output of this control-plane pass.
ARTIFACT_JSON_PATH = Path(
    "analysis/assurance/stage4_root1_shadow_runtime_authority_20260929.json")
ARTIFACT_MD_PATH = Path(
    "analysis/assurance/stage4_root1_shadow_runtime_authority_20260929.md")

#: Additive persistent authority overlay.
CANONICAL_STATE_PATH = Path(
    "research_engine/control_plane/stage4_dataset_authority_state.json")

STAMP = "20260929"
STORE_SCHEMA = 1

#: Governance stamp recorded in the overlay; frozen at the audit stamp.
GOVERNANCE_REF = "STAGE4-ROOT1-SHADOW-RUNTIME-AUTHORITY-20260929"


class DatasetAuthorityError(RuntimeError):
    """Root-1 dataset-authority invariant violated (fail closed)."""


# ---------------------------------------------------------------------------
# The five shared root changes, by identity only (no subjective priority).
# ---------------------------------------------------------------------------

ROOT_CHANGE_01 = "ROOT-01-DATASET-AUTHORITY"
ROOT_CHANGE_02 = "ROOT-02-LIFECYCLE-DECISION-SNAPSHOT"
ROOT_CHANGE_03 = "ROOT-03-MARKET-TIMESTAMP-SEMANTICS"
ROOT_CHANGE_04 = "ROOT-04-EX2-LIFECYCLE-M5-PATH"
ROOT_CHANGE_05 = "ROOT-05-L7-EXPERIMENT-ARM"

ROOT_CHANGES: tuple[str, ...] = (
    ROOT_CHANGE_01, ROOT_CHANGE_02, ROOT_CHANGE_03,
    ROOT_CHANGE_04, ROOT_CHANGE_05,
)

#: The dataset whose authority this root change re-points.
SHADOW_TRADES = "shadow_trades"
SHADOW_RUNTIME = "shadow_runtime"
SHADOW_RUNTIME_SCHEMA = "shadow_runtime_v1"
SHADOW_TRADES_SCHEMA = "shadow_trades_v1"

#: The producer that actually persists ``shadow_runtime_v1``.
SHADOW_RUNTIME_PRODUCER = "core/shadow/persistence.py + core/shadow/runtime.py"

# ---------------------------------------------------------------------------
# Requirement classification vocabulary (task-2 controlled set).
# ---------------------------------------------------------------------------

FULLY_SATISFIED = "FULLY_SATISFIED_BY_SHADOW_RUNTIME"
FIELD_GAP_REMAINS = "SHADOW_RUNTIME_CORRECT_DATASET_BUT_FIELD_GAP_REMAINS"
COVERAGE_GAP_REMAINS = (
    "SHADOW_RUNTIME_CORRECT_DATASET_BUT_COVERAGE_GAP_REMAINS")
SEMANTIC_GAP_REMAINS = (
    "SHADOW_RUNTIME_CORRECT_DATASET_BUT_SEMANTIC_GAP_REMAINS")
NOT_SUFFICIENT = "SHADOW_RUNTIME_NOT_ACTUALLY_SUFFICIENT"

ALLOWED_CLASSIFICATIONS = frozenset({
    FULLY_SATISFIED, FIELD_GAP_REMAINS, COVERAGE_GAP_REMAINS,
    SEMANTIC_GAP_REMAINS, NOT_SUFFICIENT,
})

#: The seven fail-closed satisfaction gates (task 7).  ALL must be True.
SATISFACTION_GATES: tuple[str, ...] = (
    "field_exists",
    "semantics_match",
    "grain_match",
    "canonical_identity_match",
    "timestamp_semantics_match",
    "lineage_requirement_matches",
    "coverage_completeness_met",
)

#: Gap dispositions.
DISPOSITION_SATISFIED = "SATISFIED_CURRENTLY"
DISPOSITION_UNRESOLVED = "REMAINS_UNRESOLVED"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _fp(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, default=str).encode("utf-8")).hexdigest()



# ---------------------------------------------------------------------------
# Audited per-requirement evidence (TASK 2).
#
# Every value below is transcribed from the PERSISTED Stage 4 audit
# (stage4_observation_dataset_audit_20260929.json and
#  stage4_observation_requirement_matrix_20260929.json) plus the persisted
# read-only S3 field census (_stage4_s3_fields_20260929.json).  No new S3
# discovery, no producer introspection, no re-audit.
#
# Each entry records, for the seven fail-closed gates, whether the CURRENTLY
# PERSISTED shadow_runtime_v1 rows satisfy them.  A gate the audit evidences as
# failed stays False -- correcting the dataset name cannot repair it.
# ---------------------------------------------------------------------------

#: Coverage facts transcribed from the audit's ``observed_coverage``.
AUDITED_COVERAGE: dict[str, str] = {
    "OR-01": "outcome.pnl_r_multiple 20421/20421 (all 20421 CLOSE lifecycles)",
    "OR-02": ("live_facts.market_phase present on 20460 shadow_runtime rows but "
              "20460 NULLISH -> 0 usable at lifecycle grain; market_context_v1 "
              "1754/1754 non-null; decision_trace_v1 25360/25516 non-null; "
              "strategy_observation_v1 25575/25575 non-null"),
    "OR-03": ("live_facts.h4_regime 20460 present, 4003 nullish -> 16457 "
              "usable; market_context_v1 h4.regime 1754/1754; decision_trace_v1 "
              "25360/25516"),
    "OR-04": ("live_facts.pattern 20460 present, 18047 nullish -> 2413 usable "
              "(11.8% of OPEN lifecycles)"),
    "OR-05": ("live_facts.strategy 20460 present, 3841 nullish -> 16619 usable "
              "(81.2% of OPEN lifecycles)"),
    "OR-06": ("identity.canonical_opportunity_id 0/66258; identity.shadow_type "
              "20460/66258; identity.evaluated_horizon present in the identity "
              "block; identity.entity_id present; top-level "
              "canonical_opportunity_id 66258/66258; top-level symbol "
              "66258/66258; top-level shadow_trade_id 66258/66258"),
    "OR-07": ("OPEN.entry_market_time 20460/66258; "
              "entry_market_time_utc_epoch_s 20460/66258; "
              "market_timestamp_semantics 7473/66258 (11.3%); "
              "market_timestamp_normalization_version 7473/66258 (11.3%)"),
    "OR-15": ("experiment_arm 0/66258; arm_assigned_at 0/66258; "
              "arm_schema_version 0/66258; no occurrence of 'experiment_arm' "
              "anywhere in core/shadow/*.py"),
}

#: Actual persisted field path(s) carrying the required observable.
AUDITED_FIELD: dict[str, str] = {
    "OR-01": "outcome.pnl_r_multiple",
    "OR-02": "live_facts.market_phase",
    "OR-03": "live_facts.h4_regime",
    "OR-04": "live_facts.pattern",
    "OR-05": "live_facts.strategy",
    "OR-06": ("identity.{shadow_type,trade_horizon,evaluated_horizon,"
              "entity_id,cycle_id} plus top-level canonical_opportunity_id/"
              "symbol/shadow_trade_id"),
    "OR-07": ("entry_market_time, entry_market_time_utc_epoch_s, "
              "market_timestamp_semantics, "
              "market_timestamp_normalization_version"),
    "OR-15": "ABSENT -- experiment_arm / arm_assigned_at / arm_schema_version",
}

#: Gate verdicts derived from the audit evidence above.
#:
#: OR-01 / OR-06 are FULLY satisfied: pnl_r_multiple is non-null on 100% of
#: the 20421 CLOSE lifecycles at exactly the required grain, and the canonical
#: identity triple + symbol are persisted on 100% of rows (the OR-06 defect is
#: only WHICH PATH carries them -- a contract/path correction, not a missing
#: observation).
#:
#: OR-02/03/04/05: field present but NULL or under-covered => a REAL remaining
#: gap.  Dataset authority is corrected; the gap is PRESERVED.
#: OR-07: 11.3% timestamp-semantics attestation => real SCHEMA gap (ROOT-03).
#: OR-15: field entirely absent => FIELD gap (ROOT-05).
GATE_VERDICTS: dict[str, dict[str, bool]] = {
    "OR-01": dict.fromkeys(SATISFACTION_GATES, True),
    "OR-02": {
        "field_exists": True,
        "semantics_match": False,
        "grain_match": False,
        "canonical_identity_match": True,
        "timestamp_semantics_match": True,
        "lineage_requirement_matches": False,
        "coverage_completeness_met": False,
    },
    "OR-03": {
        "field_exists": True,
        "semantics_match": True,
        "grain_match": True,
        "canonical_identity_match": True,
        "timestamp_semantics_match": True,
        "lineage_requirement_matches": True,
        "coverage_completeness_met": False,
    },
    "OR-04": {
        "field_exists": True,
        "semantics_match": True,
        "grain_match": True,
        "canonical_identity_match": True,
        "timestamp_semantics_match": True,
        "lineage_requirement_matches": True,
        "coverage_completeness_met": False,
    },
    "OR-05": {
        "field_exists": True,
        "semantics_match": True,
        "grain_match": True,
        "canonical_identity_match": True,
        "timestamp_semantics_match": True,
        "lineage_requirement_matches": True,
        "coverage_completeness_met": False,
    },
    "OR-06": dict.fromkeys(SATISFACTION_GATES, True),
    "OR-07": {
        "field_exists": True,
        "semantics_match": True,
        "grain_match": True,
        "canonical_identity_match": True,
        "timestamp_semantics_match": False,
        "lineage_requirement_matches": True,
        "coverage_completeness_met": False,
    },
    "OR-15": {
        "field_exists": False,
        "semantics_match": False,
        "grain_match": True,
        "canonical_identity_match": True,
        "timestamp_semantics_match": False,
        "lineage_requirement_matches": False,
        "coverage_completeness_met": False,
    },
}


#: Human-readable, evidence-bound reason for every failing gate.
GATE_FAILURE_REASONS: dict[str, dict[str, str]] = {
    "OR-02": {
        "semantics_match": ("live_facts.market_phase is emitted but 100% NULL "
                            "inside the lifecycle record; the non-null values "
                            "live at MARKET grain in market_context_v1 / "
                            "decision_trace_v1 / strategy_observation_v1 and "
                            "are not lifecycle-bound."),
        "grain_match": ("No lifecycle-bound phase survives: 20460 rows carry a "
                        "NULL, so 0 usable values at lifecycle grain."),
        "lineage_requirement_matches": ("A NULL value carries no producer "
                                        "lineage back to the causal decision "
                                        "instant."),
        "coverage_completeness_met": "0 usable values at lifecycle grain.",
    },
    "OR-03": {
        "coverage_completeness_met": ("4003/20460 lifecycle rows are NULL -> "
                                      "only 16457 usable; not 100%."),
    },
    "OR-04": {
        "coverage_completeness_met": ("18047/20460 lifecycle rows are NULL -> "
                                      "only 2413 usable (11.8%); not 100%."),
    },
    "OR-05": {
        "coverage_completeness_met": ("3841/20460 lifecycle rows are NULL -> "
                                      "only 16619 usable (81.2%); not 100%."),
    },
    "OR-07": {
        "timestamp_semantics_match": ("market_timestamp_semantics is present "
                                      "on only 7473/66258 rows (11.3%); "
                                      "shadow_timestamp_normalization.reject() "
                                      "fails closed without it."),
        "coverage_completeness_met": ("11.3% coverage is not the required "
                                      "100% of OPEN rows."),
    },
    "OR-15": {
        "field_exists": ("experiment_arm / arm_assigned_at / arm_schema_version "
                         "are 0/66258 and absent from core/shadow/*.py."),
        "semantics_match": ("No arm is issued; shadow_runtime_v1 carries no "
                            "CONTROL/CANDIDATE assignment at all."),
        "timestamp_semantics_match": ("No arm_assigned_at exists, so the "
                                      "pre-outcome ordering cannot be proven."),
        "lineage_requirement_matches": ("No assignment decision record (issuer, "
                                        "request id, policy version) exists."),
        "coverage_completeness_met": "0/66258.",
    },
}

#: Which remaining root change owns each requirement that is NOT fully
#: satisfied after the authority correction.  Identity only -- no ranking.
REQUIREMENT_BLOCKED_BY_ROOT: dict[str, str] = {
    "OR-02": ROOT_CHANGE_02,
    "OR-03": ROOT_CHANGE_02,
    "OR-04": ROOT_CHANGE_02,
    "OR-05": ROOT_CHANGE_02,
    "OR-07": ROOT_CHANGE_03,
    "OR-15": ROOT_CHANGE_05,
}


# ---------------------------------------------------------------------------
# TASK 1 -- identify the affected gaps EXACTLY (derived, never hard-coded).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# SEPARATED EVIDENCE IDENTITY (Stage 4 Refinement 2)
#
# The corrected authority answers four DIFFERENT questions and each has its own
# field.  In particular the historical ``dataset_version`` field carries a
# SCHEMA string (``shadow_runtime_v1``): it is not a population identity, and a
# population identity is now named explicitly by ``dataset_snapshot_id``.
# ---------------------------------------------------------------------------

def snapshot_registry() -> D.DatasetSnapshotRegistry:
    """The governed population registry (single population-identity source)."""
    return D.bootstrap_registry()


def requirement_evidence_identity(requirement_id: str) -> dict[str, Any]:
    """Separated evidence identity for ONE governed observation requirement."""
    registry = snapshot_registry()
    rid = I.validate_requirement_id(requirement_id)
    snapshots = registry.snapshots_for_requirement(rid)
    if not snapshots:
        raise DatasetAuthorityError(
            "REQUIREMENT_WITHOUT_DATASET_SNAPSHOT:" + rid)
    evidence_set_ids = sorted(
        item.evidence_set_id for item in D.governed_evidence_sets(registry)
        if rid in item.observation_requirement_ids)
    if not evidence_set_ids:
        raise DatasetAuthorityError(
            "REQUIREMENT_WITHOUT_EVIDENCE_SET:" + rid)
    schema_versions = sorted({item.schema_version for item in snapshots})
    if len(schema_versions) != 1:
        raise DatasetAuthorityError("REQUIREMENT_SCHEMA_AMBIGUOUS:" + rid)
    dataset_names = sorted({item.dataset_name for item in snapshots})
    if dataset_names != [SHADOW_RUNTIME]:
        raise DatasetAuthorityError(
            "REQUIREMENT_SNAPSHOT_DATASET_MISMATCH:" + rid)
    generations = sorted({item.schema_generation for item in snapshots
                          if item.schema_generation is not None})
    return {
        "dataset_name": SHADOW_RUNTIME,
        "schema_version": schema_versions[0],
        "schema_generation": generations[-1] if generations else None,
        "schema_generation_state": (D.GENERATION_CONFIRMED if generations
                                    else D.GENERATION_UNASSERTED),
        "dataset_snapshot_ids": [item.dataset_snapshot_id for item in snapshots],
        "producer_versions": sorted({item.producer_version for item in snapshots
                                     if item.producer_version}),
        "producer_lineage_state": (
            D.PRODUCER_BOUND if all(item.producer_version for item in snapshots)
            else D.PRODUCER_UNASSERTED),
        "evidence_set_ids": evidence_set_ids,
        "snapshot_identity_state": I.SNAPSHOT_IDENTITY_BOUND,
        "legacy_dataset_version_field_meaning": (
            "corrected_dataset_version holds the SCHEMA identifier, not a "
            "population identity; the population is named by "
            "dataset_snapshot_ids."),
    }


def dataset_identity_block() -> dict[str, Any]:
    """Separated dataset identity for the corrected authority."""
    registry = snapshot_registry()
    snapshots = registry.snapshots_for_dataset(SHADOW_RUNTIME)
    if not snapshots:
        raise DatasetAuthorityError("AUTHORITY_WITHOUT_DATASET_SNAPSHOT")
    epochs = _versioning_epochs_for(SHADOW_RUNTIME)
    generations = sorted({
        int(row["schema_generation"]) for row in _versioning_generations(
            SHADOW_RUNTIME)})
    return {
        "dataset_name": SHADOW_RUNTIME,
        "schema_version": SHADOW_RUNTIME_SCHEMA,
        "schema_generations": generations,
        "current_schema_generation": generations[-1] if generations else None,
        "dataset_snapshot_ids": [item.dataset_snapshot_id for item in snapshots],
        "producer_versions": sorted({item.producer_version for item in snapshots
                                     if item.producer_version}),
        "evidence_set_ids": sorted(
            epoch["evidence_set_id"] for epoch in epochs),
        "epoch_snapshot_identity_states": {
            epoch["epoch_id"]: epoch.get("snapshot_identity_state")
            for epoch in epochs},
        "legacy_dataset_version_field": I.normalize_legacy_dataset_reference(
            SHADOW_RUNTIME_SCHEMA),
    }


def _versioning_policy() -> dict[str, Any]:
    return _read_json(Path(
        "analysis/assurance/stage4_data_versioning_policy_20260929.json"))


def _versioning_generations(dataset: str) -> list[dict[str, Any]]:
    rows = (list(_versioning_policy().get("version_registry", {})
                 .get("schema_generations", ()))
            + list(_versioning_policy().get("version_registry", {})
                   .get("evidence_epochs", ())))
    return [row for row in rows if str(row.get("dataset")) == str(dataset)
            and row.get("schema_generation") is not None]


def _versioning_epochs_for(dataset: str) -> list[dict[str, Any]]:
    rows = list(_versioning_policy().get("version_registry", {})
                .get("evidence_epochs", ()))
    return [row for row in rows if str(row.get("dataset")) == str(dataset)]


def load_audit() -> dict[str, Any]:
    """Load the persisted audit authorities (read-only)."""
    audit = _read_json(AUDIT_PATH)
    matrix = _read_json(REQUIREMENT_MATRIX_PATH)
    governance = _read_json(GAP_GOVERNANCE_PATH)
    register = _read_json(DATASET_GAP_REGISTER_PATH)
    if audit.get("implementation_performed") not in (False, "NO", None):
        raise DatasetAuthorityError("AUDIT_MUST_REMAIN_DISCOVERY_ONLY")
    baseline = matrix.get("baseline_certification_fingerprint")
    if baseline != EXPECTED_CERTIFICATION_FINGERPRINT:
        raise DatasetAuthorityError("AUDIT_BASELINE_FINGERPRINT_CHANGED")
    if governance.get("certification_fingerprint") != (
            EXPECTED_CERTIFICATION_FINGERPRINT):
        raise DatasetAuthorityError("GOVERNANCE_FINGERPRINT_CHANGED")
    return {"audit": audit, "matrix": matrix,
            "governance": governance, "register": register}


def root1_requirements(loaded: Mapping[str, Any]) -> list[str]:
    """Derive the Root-1 requirement set from the audit's own root causes.

    The audit names the phantom-dataset root cause explicitly; we read its
    requirement list rather than restating it, so the count is derived.
    """
    causes = list(loaded["audit"].get("shared_root_causes", ()))
    if not causes:
        raise DatasetAuthorityError("AUDIT_ROOT_CAUSES_MISSING")
    dataset_cause = None
    for cause in causes:
        joined = str(cause.get("root_cause", "")).lower()
        if "re-point" in joined and "shadow_runtime" in joined:
            dataset_cause = cause
            break
    if dataset_cause is None:
        raise DatasetAuthorityError("DATASET_AUTHORITY_ROOT_CAUSE_MISSING")
    return sorted(str(r) for r in dataset_cause.get("requirements", ()))


def classify_requirement(requirement_id: str) -> str:
    """Classify a requirement against persisted shadow_runtime_v1 evidence."""
    rid = str(requirement_id)
    if rid not in GATE_VERDICTS:
        raise DatasetAuthorityError("UNAUDITED_REQUIREMENT:" + rid)
    if not all(gate in GATE_VERDICTS[rid] for gate in SATISFACTION_GATES):
        raise DatasetAuthorityError("GATE_SET_INCOMPLETE:" + rid)
    if all(GATE_VERDICTS[rid][gate] for gate in SATISFACTION_GATES):
        return FULLY_SATISFIED
    if not GATE_VERDICTS[rid]["field_exists"]:
        return FIELD_GAP_REMAINS
    if not (GATE_VERDICTS[rid]["semantics_match"]
            and GATE_VERDICTS[rid]["grain_match"]
            and GATE_VERDICTS[rid]["timestamp_semantics_match"]
            and GATE_VERDICTS[rid]["lineage_requirement_matches"]):
        return SEMANTIC_GAP_REMAINS
    return COVERAGE_GAP_REMAINS


def requirement_is_satisfied(requirement_id: str) -> bool:
    """Fail-closed: satisfied ONLY when all seven gates pass."""
    return classify_requirement(requirement_id) == FULLY_SATISFIED


# ---------------------------------------------------------------------------
# TASK 3 -- canonical dataset authority.
#
# Dataset identity/version facts are READ from the existing authority
# (core.production_data_contract).  This module does not create a parallel
# registry and does not mutate the production contract.
# ---------------------------------------------------------------------------

def canonical_dataset_authority(loaded: Mapping[str, Any]) -> dict[str, Any]:
    """Build the canonical observation-source authority for Root 1."""
    universe = dict(loaded["audit"].get("persisted_dataset_universe", {}))
    runtime = universe.get(SHADOW_RUNTIME, {})
    phantom = universe.get(SHADOW_TRADES, {})
    runtime_schema = PRODUCTION_SCHEMA_REGISTRY[SHADOW_RUNTIME]
    phantom_schema = PRODUCTION_SCHEMA_REGISTRY[SHADOW_TRADES]
    if int(runtime.get("objects", 0)) <= 0:
        raise DatasetAuthorityError("SHADOW_RUNTIME_NOT_PERSISTED")
    if int(phantom.get("objects", 0)) != 0:
        raise DatasetAuthorityError("SHADOW_TRADES_PERSISTENCE_CHANGED")
    if str(runtime.get("schema")) != SHADOW_RUNTIME_SCHEMA:
        raise DatasetAuthorityError("SHADOW_RUNTIME_SCHEMA_MISMATCH")
    if runtime_schema.current != SHADOW_RUNTIME_SCHEMA:
        raise DatasetAuthorityError("PRODUCTION_CONTRACT_SCHEMA_MISMATCH")
    return {
        "governance_ref": GOVERNANCE_REF,
        "stamp": STAMP,
        "canonical_observation_source": SHADOW_RUNTIME,
        "dataset": SHADOW_RUNTIME,
        "dataset_version": SHADOW_RUNTIME_SCHEMA,
        "dataset_identity": dataset_identity_block(),
        "producer": SHADOW_RUNTIME_PRODUCER,
        "semantic_owner": runtime_schema.semantic_owner,
        "status": "PERSISTED_CURRENT",
        "s3_prefix": str(runtime.get("prefix", "")),
        "persisted_objects": int(runtime.get("objects", 0)),
        "persisted_rows": int(runtime.get("rows", 0)),
        "role": runtime_schema.role.value,
        "partition_model": runtime_schema.partition_model.value,
        "date_span": [str(runtime.get("date_min", "")),
                      str(runtime.get("date_max", ""))],
        "supersedes_observation_reference": {
            "dataset": SHADOW_TRADES,
            "dataset_version": phantom_schema.current,
            "reason_invalid": ("no persisted evidence; list_objects_v2 "
                               "KeyCount=0 for supporting/shadow_trades/"),
            "declared_in_production_contract": True,
            "writer_module_exists": "core/shadow_trades.py",
            "persisted_objects": int(phantom.get("objects", 0)),
            "persisted_rows": int(phantom.get("rows", 0)),
            "persistence_state": "DECLARED_NEVER_EMITTED",
            "authoritative_for_current_contracts": False,
            "history_preserved": True,
            "history_note": ("shadow_trades remains a DECLARED dataset and a "
                             "historical governance reference. It is recorded "
                             "as zero-object and NOT authoritative for current "
                             "observation contracts. No history is rewritten."),
        },
        "dataset_version_changed_by_this_pass": False,
        "producer_changed_by_this_pass": False,
        "schema_changed_by_this_pass": False,
    }


# ---------------------------------------------------------------------------
# TASK 4 -- re-point the observation requirements (authority correction only).
# ---------------------------------------------------------------------------

def requirement_transition(
        loaded: Mapping[str, Any], requirement: Mapping[str, Any],
        authority: Mapping[str, Any],
        satisfaction_registry: SD.SatisfactionDecisionRegistry | None = None,
        ) -> dict[str, Any]:
    """Record the source-authority transition for one requirement.

    Requirement identity, requesting question identity, scientific history,
    evidence-contract history and original gap provenance are all PRESERVED.
    This is an authority correction, not a new scientific result: no finding or
    scientific-result version is incremented.
    """
    rid = str(requirement.get("id"))
    decisions = satisfaction_registry or SD.build_registry()
    decision = decisions.current_for_requirement(rid)
    if decision is None:
        raise DatasetAuthorityError("MISSING_SATISFACTION_DECISION:" + rid)
    classification = classify_requirement(rid)
    satisfied = classification == FULLY_SATISFIED
    blocked_by = REQUIREMENT_BLOCKED_BY_ROOT.get(rid)
    if not satisfied and blocked_by is None:
        raise DatasetAuthorityError("UNOWNED_UNSATISFIED_REQUIREMENT:" + rid)
    if satisfied and blocked_by is not None:
        raise DatasetAuthorityError("SATISFIED_REQUIREMENT_MUST_NOT_BLOCK:" + rid)
    gates = dict(GATE_VERDICTS[rid])
    failing = [g for g in SATISFACTION_GATES if not gates[g]]
    reasons = dict(GATE_FAILURE_REASONS.get(rid, {}))
    if sorted(reasons) != sorted(failing):
        raise DatasetAuthorityError("GATE_REASON_MISMATCH:" + rid)
    return {
        "observation_requirement_id": rid,
        "title": str(requirement.get("title", "")),
        "semantic": str(requirement.get("semantic", "")),
        "question_ids": list(requirement.get("questions", ())),
        "gap_ids": list(requirement.get("gaps", ())),
        "previous_dataset_reference": SHADOW_TRADES,
        "previous_dataset_version": SHADOW_TRADES_SCHEMA,
        "previous_producer_reference": "producer:shadow_trades",
        "previous_authority_status": (
            "NOT_PERSISTED_ZERO_OBJECT; declared in "
            "core/production_data_contract.py with a writer in "
            "core/shadow_trades.py but never emitted an object"),
        "reason_previous_reference_invalid": (
            "no persisted evidence; list_objects_v2 KeyCount=0 for "
            "supporting/shadow_trades/"),
        "corrected_dataset": str(authority["dataset"]),
        "corrected_dataset_version": str(authority["dataset_version"]),
        "corrected_producer": str(authority["producer"]),
        "evidence_identity": requirement_evidence_identity(rid),
        "reason_corrected": ("persisted producer-authoritative source "
                             "confirmed by the Stage 4 observation/dataset "
                             "audit (66258 rows, 200 objects)"),
        "audited_authoritative_dataset": str(
            requirement.get("authoritative_dataset", "")),
        "audited_auditor_classification": str(
            requirement.get("primary_classification", "")),
        "audited_disposition": str(requirement.get("disposition", "")),
        "required_observable": str(requirement.get("required_observable", "")),
        "audited_persisted_field": AUDITED_FIELD[rid],
        "audited_persisted_coverage": AUDITED_COVERAGE[rid],
        "required_grain": str(requirement.get("required_grain", "")),
        "required_identity": list(requirement.get("required_identity", ())),
        "required_timestamp_semantics": str(
            requirement.get("required_timestamp", "")),
        "required_lineage": list(requirement.get("required_lineage", ())),
        "required_type": str(requirement.get("required_type", "")),
        "satisfaction_gates": gates,
        "failing_gates": failing,
        "gate_failure_reasons": reasons,
        "shadow_runtime_classification": classification,
        # This legacy field is the seven-gate DATASET-CONTRACT verdict.  It is
        # retained for compatibility, but is explicitly not final scientific
        # satisfaction authority.
        "satisfies_current_contract": satisfied,
        "dataset_contract_sufficient": satisfied,
        "final_satisfaction_authority":
            "GOVERNED_SATISFACTION_DECISION_ONLY",
        "satisfaction_decision_id": decision.satisfaction_decision_id,
        "satisfaction_decision_state": decision.decision,
        "satisfaction_decision_fingerprint": decision.decision_fingerprint,
        "governed_satisfied": decision.decision == SD.SATISFIED,
        "gap_disposition_after_correction": (
            DISPOSITION_SATISFIED if satisfied else DISPOSITION_UNRESOLVED),
        "blocked_by_root_change": blocked_by,
        "remaining_gap_kinds": sorted({
            _gate_to_gap_kind(gate) for gate in failing}),
        # -- version consequences (TASK 9) --
        "dataset_schema_version_before": SHADOW_TRADES_SCHEMA,
        "dataset_schema_version_after": SHADOW_RUNTIME_SCHEMA,
        "dataset_version_changed": False,
        "producer_version_before": "core/shadow_trades.py writer (never emitted)",
        "producer_version_after": authority["producer"],
        "producer_version_changed": False,
        "contract_version_before": "v1",
        "contract_version_after": "v1",
        "contract_governance_linkage_updated": True,
        "version_consequence": "CONTRACT_ONLY_CHANGE",
        "version_consequence_reason": (
            "The persisted shadow_runtime_v1 semantics are unchanged; only the "
            "governance pointer moved. shadow_runtime_v1 is preserved and no "
            "shadow_runtime_v2 is created by this pass."),
        "scientific_finding_version_changed": False,
        "scientific_result_version_changed": False,
        "research_reentry_triggered": False,
    }


def _gate_to_gap_kind(gate: str) -> str:
    if gate == "field_exists":
        return "FIELD"
    if gate == "coverage_completeness_met":
        return "COVERAGE"
    if gate == "lineage_requirement_matches":
        return "LINEAGE"
    return "SEMANTIC"


# ---------------------------------------------------------------------------
# TASKS 1 / 5 / 6 -- affected gaps, recomputed dispositions, root-change map.
# ---------------------------------------------------------------------------

#: Governed items whose CURRENT declared dataset domain is the phantom
#: ``shadow_trades``.  Read from the register/governance, not hard-coded.
def shadow_trades_declared_gaps(
        loaded: Mapping[str, Any]) -> list[str]:
    """Every governed gap that currently declares ``shadow_trades``."""
    found: set[str] = set()
    for gap in loaded["register"].get("gaps", ()):
        if SHADOW_TRADES in list(gap.get("current_datasets", ())):
            found.add(str(gap.get("gap_id")))
    for item in loaded["governance"].get("work_items", ()):
        if SHADOW_TRADES in list(item.get("current_datasets", ())):
            found.add(str(item.get("gap_id")))
    return sorted(found)


def affected_gap_ids(
        loaded: Mapping[str, Any], requirements: Sequence[str]) -> list[str]:
    """Derive the exact affected set: gaps depending on a Root-1 requirement."""
    matrix_gaps = set(str(g) for g in loaded["matrix"]["governed_gaps"])
    root1 = set(requirements)
    affected: set[str] = set()
    for req in loaded["matrix"]["observation_requirements"]:
        if str(req.get("id")) not in root1:
            continue
        for gap in req.get("gaps", ()):
            affected.add(str(gap))
    ungoverned = sorted(affected - matrix_gaps)
    if ungoverned:
        raise DatasetAuthorityError(
            "AFFECTED_GAP_NOT_GOVERNED:" + ",".join(ungoverned))
    return sorted(affected)


def _gap_governance_item(
        loaded: Mapping[str, Any], gap_id: str) -> Mapping[str, Any] | None:
    """Resolve the governed work item behind a matrix gap id.

    DATA gaps carry ``STAGE4-DATA-<QID>`` in both artifacts.  The two
    implementation-derived observation gaps (EX2, L7) are recorded in the
    matrix under their adjudication label ``<QID> (OG-<QID>-<fp>)`` while the
    governance store keys them as ``STAGE4-IMPL-<QID>``.  Both forms resolve to
    the same owned work item; neither is silently dropped.
    """
    for item in loaded["governance"].get("work_items", ()):
        if str(item.get("gap_id")) == gap_id:
            return item
    label = gap_id.split(" ", 1)[0].strip().upper()
    if not label:
        return None
    for item in loaded["governance"].get("work_items", ()):
        if label in [str(q).upper() for q in
                     item.get("affected_question_ids", ())]:
            return item
    return None


def gap_dispositions(
        loaded: Mapping[str, Any],
        transitions: Sequence[Mapping[str, Any]],
        affected: Sequence[str]) -> list[dict[str, Any]]:
    """Recompute each affected gap's disposition after the correction.

    A gap becomes SATISFIED_CURRENTLY only when EVERY Root-1 requirement it
    depends on is fully satisfied.  Otherwise it REMAINS_UNRESOLVED and is
    attached to the root change(s) that own its failing requirements.
    """
    by_gap: dict[str, list[Mapping[str, Any]]] = {}
    for transition in transitions:
        for gap in transition["gap_ids"]:
            by_gap.setdefault(gap, []).append(transition)
    rows: list[dict[str, Any]] = []
    for gap in sorted(affected):
        linked = by_gap.get(gap, [])
        if not linked:
            raise DatasetAuthorityError("AFFECTED_GAP_WITHOUT_REQUIREMENT:" + gap)
        failing = [t for t in linked if not t["satisfies_current_contract"]]
        satisfied = not failing
        roots = sorted({t["blocked_by_root_change"] for t in failing})
        item = _gap_governance_item(loaded, gap)
        owner = dict((item or {}).get("owner", {}))
        if not owner.get("owner_identifier"):
            raise DatasetAuthorityError("GAP_WITHOUT_OWNER:" + gap)
        rows.append({
            "gap_id": gap,
            "gap_work_item_id": str((item or {}).get("gap_work_item_id", "")),
            "gap_type": str((item or {}).get("gap_type", "")),
            "question_ids": sorted({
                q for t in linked for q in t["question_ids"]}),
            "observation_requirement_ids": sorted(
                t["observation_requirement_id"] for t in linked),
            "previous_dataset_reference": SHADOW_TRADES,
            "corrected_dataset": str(linked[0]["corrected_dataset"]),
            "corrected_dataset_version": str(
                linked[0]["corrected_dataset_version"]),
            "owner_identifier": str(owner["owner_identifier"]),
            "owning_module": str(owner.get("owning_module", "")),
            "owner_reassigned_to_persisted_producer": True,
            "dataset_authority_corrected": True,
            "disposition": (DISPOSITION_SATISFIED if satisfied
                            else DISPOSITION_UNRESOLVED),
            "blocked_by_root_changes": roots,
            "blocking_requirements": sorted(
                t["observation_requirement_id"] for t in failing),
            "remaining_gap_kinds": sorted({
                kind for t in failing for kind in t["remaining_gap_kinds"]}),
            "scientific_finding_version_changed": False,
            "research_reentry_triggered": False,
            "old_data_remains_valid": True,
        })
    return rows


# ---------------------------------------------------------------------------
# TASK 7 -- fail-closed validation: no real gap may be erased.
# ---------------------------------------------------------------------------

def _validate_authority(authority: Mapping[str, Any]) -> None:
    """The phantom may never be current authority; history stays intact."""
    if authority.get("canonical_observation_source") != SHADOW_RUNTIME:
        raise DatasetAuthorityError("AUTHORITY_NOT_SHADOW_RUNTIME")
    if authority.get("dataset_version") != SHADOW_RUNTIME_SCHEMA:
        raise DatasetAuthorityError("AUTHORITY_VERSION_CHANGED")
    prior = authority.get("supersedes_observation_reference", {})
    if prior.get("authoritative_for_current_contracts") is not False:
        raise DatasetAuthorityError("SHADOW_TRADES_STILL_CLAIMED_AUTHORITY")
    if prior.get("history_preserved") is not True:
        raise DatasetAuthorityError("DATASET_HISTORY_NOT_PRESERVED")
    if str(prior.get("dataset")) != SHADOW_TRADES:
        raise DatasetAuthorityError("DATASET_HISTORY_REFERENCE_LOST")
    if int(authority.get("persisted_rows", 0)) <= 0:
        raise DatasetAuthorityError("AUTHORITY_ROWS_MISSING")
    for flag in ("dataset_version_changed_by_this_pass",
                 "producer_changed_by_this_pass",
                 "schema_changed_by_this_pass"):
        if authority.get(flag) is not False:
            raise DatasetAuthorityError("PRODUCER_OR_SCHEMA_MUTATION:" + flag)
    identity = authority.get("dataset_identity")
    if not isinstance(identity, Mapping):
        raise DatasetAuthorityError("DATASET_IDENTITY_BLOCK_MISSING")
    if str(identity.get("dataset_name")) != SHADOW_RUNTIME:
        raise DatasetAuthorityError("DATASET_IDENTITY_NAME_MISMATCH")
    if str(identity.get("schema_version")) != SHADOW_RUNTIME_SCHEMA:
        raise DatasetAuthorityError("DATASET_IDENTITY_SCHEMA_MISMATCH")
    legacy = identity.get("legacy_dataset_version_field") or {}
    if legacy.get("is_dataset_snapshot_id") is not False:
        raise DatasetAuthorityError("SCHEMA_STRING_PRESENTED_AS_POPULATION")
    if legacy.get("dataset_snapshot_id") is not None:
        raise DatasetAuthorityError("LEGACY_SNAPSHOT_IDENTITY_SYNTHESIZED")
    resolved = snapshot_registry()
    for snapshot_id in identity.get("dataset_snapshot_ids", ()):
        if resolved.require(str(snapshot_id)).dataset_name != SHADOW_RUNTIME:
            raise DatasetAuthorityError(
                "DATASET_IDENTITY_SNAPSHOT_MISMATCH:" + str(snapshot_id))


def _validate_ledger(ledger: Mapping[str, Any]) -> None:
    for key in ("s3_writes", "schema_mutations", "producer_mutations",
                "research_reentry_events", "backfills_performed",
                "q71_started_count"):
        if int(ledger.get(key, -1)) != 0:
            raise DatasetAuthorityError("MUTATION_LEDGER_NONZERO:" + key)
    if ledger.get("q71_started") is not False:
        raise DatasetAuthorityError("Q71_STARTED")


def _validate_transition(
        transition: Mapping[str, Any], *,
        satisfaction_registry: SD.SatisfactionDecisionRegistry | None = None,
        satisfaction_evidence: Mapping[str, I.EvidenceSet] | None = None,
        satisfaction_snapshots: D.DatasetSnapshotRegistry | None = None,
        ) -> None:
    rid = str(transition.get("observation_requirement_id", ""))
    gates = transition.get("satisfaction_gates", {})
    missing = sorted(set(SATISFACTION_GATES) - set(gates))
    if missing:
        raise DatasetAuthorityError(
            "GATE_SET_INCOMPLETE:" + rid + ":" + ",".join(missing))
    failing = [g for g in SATISFACTION_GATES if not gates[g]]
    if sorted(transition.get("failing_gates", ())) != sorted(failing):
        raise DatasetAuthorityError("FAILING_GATE_MISMATCH:" + rid)
    if sorted(transition.get("gate_failure_reasons", {})) != sorted(failing):
        raise DatasetAuthorityError("GATE_REASON_MISMATCH:" + rid)
    # TASK 7 core rule: satisfied ONLY when EVERY gate passes.
    satisfied = all(gates[g] for g in SATISFACTION_GATES)
    if bool(transition.get("satisfies_current_contract")) != satisfied:
        raise DatasetAuthorityError("SATISFACTION_NOT_GATE_DERIVED:" + rid)
    if transition.get("final_satisfaction_authority") != (
            "GOVERNED_SATISFACTION_DECISION_ONLY"):
        raise DatasetAuthorityError("FINAL_SATISFACTION_AUTHORITY_MISSING:" + rid)
    if not str(transition.get("satisfaction_decision_id") or "").startswith(
            "SDEC-"):
        raise DatasetAuthorityError("SATISFACTION_DECISION_ID_MISSING:" + rid)
    governed_state = str(transition.get("satisfaction_decision_state") or "")
    if governed_state not in SD.DECISION_STATES:
        raise DatasetAuthorityError("SATISFACTION_DECISION_STATE_INVALID:" + rid)
    if bool(transition.get("governed_satisfied")) != (
            governed_state == SD.SATISFIED):
        raise DatasetAuthorityError("GOVERNED_SATISFACTION_NOT_DERIVED:" + rid)
    if satisfaction_registry is not None:
        decision_id = str(transition.get("satisfaction_decision_id"))
        try:
            verified = satisfaction_registry.verify(
                decision_id,
                evidence_sets=dict(satisfaction_evidence or {}),
                snapshot_registry=(satisfaction_snapshots
                                   or D.bootstrap_registry()))
        except SD.SatisfactionDecisionError as exc:
            raise DatasetAuthorityError(
                "SATISFACTION_DECISION_VERIFICATION_FAILED:" + rid) from exc
        if (verified.observation_requirement_id != rid
                or verified.decision != governed_state
                or verified.decision_fingerprint != transition.get(
                    "satisfaction_decision_fingerprint")):
            raise DatasetAuthorityError(
                "SATISFACTION_DECISION_REFERENCE_MISMATCH:" + rid)
    if satisfied and failing:
        raise DatasetAuthorityError("SATISFIED_WITH_FAILING_GATES:" + rid)
    if not satisfied and not failing:
        raise DatasetAuthorityError(
            "UNSATISFIED_WITHOUT_FAILING_GATES:" + rid)
    blocked = transition.get("blocked_by_root_change")
    if not satisfied and blocked not in set(ROOT_CHANGES):
        raise DatasetAuthorityError("UNOWNED_UNSATISFIED_REQUIREMENT:" + rid)
    if satisfied and blocked is not None:
        raise DatasetAuthorityError("SATISFIED_MUST_NOT_BLOCK:" + rid)
    if transition.get("dataset_version_changed") is not False:
        raise DatasetAuthorityError("UNEXPECTED_DATASET_VERSION_CHANGE:" + rid)
    if transition.get("producer_version_changed") is not False:
        raise DatasetAuthorityError("UNEXPECTED_PRODUCER_CHANGE:" + rid)
    for churn in ("scientific_finding_version_changed",
                  "scientific_result_version_changed",
                  "research_reentry_triggered"):
        if transition.get(churn) is not False:
            raise DatasetAuthorityError("SCIENTIFIC_CHURN:" + rid + ":" + churn)
    for key in ("question_ids", "gap_ids", "required_identity",
                "required_lineage", "required_grain",
                "required_timestamp_semantics", "required_observable"):
        if not transition.get(key):
            raise DatasetAuthorityError(
                "PRESERVED_PROVENANCE_MISSING:" + rid + ":" + key)
    if str(transition.get("previous_dataset_reference")) != SHADOW_TRADES:
        raise DatasetAuthorityError("HISTORY_REFERENCE_LOST:" + rid)
    if str(transition.get("corrected_dataset")) != SHADOW_RUNTIME:
        raise DatasetAuthorityError("AUTHORITY_NOT_CORRECTED:" + rid)
    if str(transition.get("corrected_dataset_version")) != (
            SHADOW_RUNTIME_SCHEMA):
        raise DatasetAuthorityError("AUTHORITY_VERSION_NOT_CORRECTED:" + rid)
    _validate_evidence_identity(transition.get("evidence_identity"), rid)


def _validate_evidence_identity(
        identity: Any, rid: str,
        registry: D.DatasetSnapshotRegistry | None = None) -> None:
    """Every governed transition names population, schema and evidence set."""
    if not isinstance(identity, Mapping):
        raise DatasetAuthorityError(
            "EVIDENCE_IDENTITY_MISSING:" + rid)
    resolved = registry if registry is not None else snapshot_registry()
    snapshot_ids = list(identity.get("dataset_snapshot_ids") or ())
    if not snapshot_ids:
        raise DatasetAuthorityError(
            "DATASET_SNAPSHOT_IDENTITY_MISSING:" + rid)
    if not list(identity.get("evidence_set_ids") or ()):
        raise DatasetAuthorityError("EVIDENCE_SET_IDENTITY_MISSING:" + rid)
    if str(identity.get("dataset_name")) != SHADOW_RUNTIME:
        raise DatasetAuthorityError("EVIDENCE_DATASET_NAME_MISMATCH:" + rid)
    if str(identity.get("schema_version")) != SHADOW_RUNTIME_SCHEMA:
        raise DatasetAuthorityError("EVIDENCE_SCHEMA_IDENTITY_MISMATCH:" + rid)
    if identity.get("snapshot_identity_state") != I.SNAPSHOT_IDENTITY_BOUND:
        raise DatasetAuthorityError("SNAPSHOT_IDENTITY_NOT_BOUND:" + rid)
    for snapshot_id in snapshot_ids:
        # Population identity must be a registered DSNAP- identity, never the
        # schema string that the historical dataset_version field carries.
        I.validate_dataset_snapshot_id(snapshot_id, allow_none=False)
        snapshot = resolved.require(snapshot_id)
        if snapshot.dataset_name != SHADOW_RUNTIME:
            raise DatasetAuthorityError("SNAPSHOT_DATASET_MISMATCH:" + rid)
        if snapshot.schema_version != str(identity.get("schema_version")):
            raise DatasetAuthorityError("SNAPSHOT_SCHEMA_MISMATCH:" + rid)
    if not list(identity.get("producer_versions") or ()):
        raise DatasetAuthorityError("PRODUCER_LINEAGE_MISSING:" + rid)


def _validate_gap(gap: Mapping[str, Any]) -> None:
    gid = str(gap.get("gap_id", ""))
    if not str(gap.get("owner_identifier", "")):
        raise DatasetAuthorityError("GAP_WITHOUT_OWNER:" + gid)
    if gap.get("dataset_authority_corrected") is not True:
        raise DatasetAuthorityError("GAP_AUTHORITY_NOT_CORRECTED:" + gid)
    satisfied = gap.get("disposition") == DISPOSITION_SATISFIED
    blocking = list(gap.get("blocking_requirements", ()))
    roots = list(gap.get("blocked_by_root_changes", ()))
    if satisfied and (blocking or roots):
        raise DatasetAuthorityError("SATISFIED_GAP_MUST_NOT_BLOCK:" + gid)
    if not satisfied:
        if not blocking:
            raise DatasetAuthorityError("UNRESOLVED_GAP_WITHOUT_BLOCKER:" + gid)
        if not roots or not set(roots).issubset(set(ROOT_CHANGES)):
            raise DatasetAuthorityError("UNOWNED_UNRESOLVED_GAP:" + gid)
    if gap.get("old_data_remains_valid") is not True:
        raise DatasetAuthorityError("HISTORICAL_GAP_DATA_MUTATED:" + gid)
    for churn in ("scientific_finding_version_changed",
                  "research_reentry_triggered"):
        if gap.get(churn) is not False:
            raise DatasetAuthorityError("SCIENTIFIC_CHURN:" + gid + ":" + churn)
    if str(gap.get("previous_dataset_reference")) != SHADOW_TRADES:
        raise DatasetAuthorityError("GAP_HISTORY_REFERENCE_LOST:" + gid)
    if str(gap.get("corrected_dataset")) != SHADOW_RUNTIME:
        raise DatasetAuthorityError("GAP_AUTHORITY_NOT_CORRECTED:" + gid)


def validate_store(store: Mapping[str, Any]) -> None:
    """Fail closed on any conservation or satisfaction violation."""
    if store.get("schema") != STORE_SCHEMA:
        raise DatasetAuthorityError("STORE_SCHEMA_CHANGED")
    if store.get("stamp") != STAMP:
        raise DatasetAuthorityError("STORE_STAMP_CHANGED")
    if store.get("certification_fingerprint") != (
            EXPECTED_CERTIFICATION_FINGERPRINT):
        raise DatasetAuthorityError("CERTIFICATION_FINGERPRINT_CHANGED")
    _validate_authority(store.get("dataset_authority", {}))
    _validate_ledger(store.get("mutation_ledger", {}))

    transitions = list(store.get("observation_requirement_transitions", ()))
    gaps = list(store.get("affected_gap_dispositions", ()))
    if not transitions or not gaps:
        raise DatasetAuthorityError("STORE_EMPTY")

    seen_requirements: set[str] = set()
    satisfaction_snapshots = D.bootstrap_registry()
    satisfaction_evidence = SD.governed_evidence_map(satisfaction_snapshots)
    try:
        satisfaction_registry = SD.SatisfactionDecisionRegistry.load(
            SD.STATE_PATH)
    except SD.SatisfactionDecisionError as exc:
        raise DatasetAuthorityError(
            "SATISFACTION_AUTHORITY_UNREADABLE") from exc
    for transition in transitions:
        rid = str(transition.get("observation_requirement_id", ""))
        try:
            I.validate_requirement_id(rid)
        except I.Stage4IdentityError as exc:
            raise DatasetAuthorityError(str(exc)) from exc
        if not rid or rid in seen_requirements:
            raise DatasetAuthorityError("DUPLICATE_REQUIREMENT_TRANSITION")
        seen_requirements.add(rid)
        _validate_transition(
            transition, satisfaction_registry=satisfaction_registry,
            satisfaction_evidence=satisfaction_evidence,
            satisfaction_snapshots=satisfaction_snapshots)
    if set(store.get("affected_observation_requirements", ())) != (
            seen_requirements):
        raise DatasetAuthorityError("REQUIREMENT_SET_MISMATCH")

    # No observation requirement may disappear.  Every audited requirement must
    # be accounted for as either REPOINTED or RETAINED_UNTOUCHED.
    matrix_requirements = {str(rid) for rid in store.get(
        "audited_observation_requirements", ())}
    try:
        for rid in matrix_requirements:
            I.validate_requirement_id(rid)
    except I.Stage4IdentityError as exc:
        raise DatasetAuthorityError(str(exc)) from exc
    conservation_pre = store.get("conservation", {})
    retained = {str(rid) for rid in conservation_pre.get(
        "retained_untouched_observation_requirements", ())}
    if seen_requirements & retained:
        raise DatasetAuthorityError("REQUIREMENT_STATE_CONFLICT")
    if matrix_requirements != (seen_requirements | retained):
        raise DatasetAuthorityError("OBSERVATION_REQUIREMENTS_LOST")

    if len(gaps) != int(store.get("affected_gap_count", -1)):
        raise DatasetAuthorityError("AFFECTED_GAP_COUNT_MISMATCH")
    seen_gaps: set[str] = set()
    for gap in gaps:
        gid = str(gap.get("gap_id", ""))
        if not gid or gid in seen_gaps:
            raise DatasetAuthorityError("DUPLICATE_AFFECTED_GAP")
        seen_gaps.add(gid)
        _validate_gap(gap)
    if seen_gaps != set(store.get("affected_gap_ids", ())):
        raise DatasetAuthorityError("AFFECTED_GAP_SET_MISMATCH")

    conservation = store.get("conservation", {})
    if int(conservation.get("unaccounted_gaps", -1)) != 0:
        raise DatasetAuthorityError("UNACCOUNTED_GAPS_PRESENT")
    if int(conservation.get("gaps_without_owner", -1)) != 0:
        raise DatasetAuthorityError("OWNERLESS_GAPS_PRESENT")
    if int(conservation.get("observation_requirements_lost", -1)) != 0:
        raise DatasetAuthorityError("OBSERVATION_REQUIREMENTS_LOST")


# ---------------------------------------------------------------------------
# Store construction (pure, read-only, no I/O beyond the audit artifacts).
# ---------------------------------------------------------------------------

def _conservation(
        loaded: Mapping[str, Any], affected: Sequence[str],
        gaps: Sequence[Mapping[str, Any]],
        requirements: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    governed = set(str(g) for g in loaded["matrix"]["governed_gaps"])
    audited = {str(r.get("id")) for r in
               loaded["matrix"]["observation_requirements"]}
    repointed = {str(r.get("observation_requirement_id"))
                 for r in requirements}
    unaccounted = sorted(set(affected) - governed)
    ownerless = sorted(str(g.get("gap_id")) for g in gaps
                       if not str(g.get("owner_identifier", "")))
    # Reconciliation against the audit's headline figure.  The audit reports 27
    # governed GAPS for the dataset-authority root cause: the 27 DATA_SCHEMA_GAP
    # register entries that named shadow_trades.  The 28th affected governed item
    # is L7, an IMPLEMENTATION_GAP-derived observation gap (GWI-L7-IMPL) whose
    # required_dataset_domain is also shadow_trades.  Both are affected; the two
    # populations are reported separately so the audit's 27 is neither lost nor
    # inflated.
    register_affected = sorted(
        str(g.get("gap_id")) for g in gaps
        if str(g.get("gap_type", "")).startswith("DATA"))
    observation_impl_affected = sorted(
        str(g.get("gap_id")) for g in gaps
        if str(g.get("gap_type", "")) == "IMPLEMENTATION_GAP")
    # "Lost" means the authority record no longer accounts for an audited
    # requirement at all.  Every audited requirement is accounted for under
    # exactly one of two explicit states: REPOINTED (Root 1 corrected it) or
    # RETAINED_UNTOUCHED (Root 1 did not touch it).  Neither state is a loss.
    retained = sorted(audited - repointed)
    accounted = repointed | set(retained)
    lost = sorted(audited - accounted)
    return {
        "total_governed_gaps": len(governed),
        "affected_governed_gaps": len(affected),
        "affected_data_schema_gap_register_entries": register_affected,
        "affected_data_schema_gap_count": len(register_affected),
        "affected_implementation_observation_gaps": observation_impl_affected,
        "affected_implementation_observation_gap_count": len(
            observation_impl_affected),
        "audit_headline_gap_count": 27,
        "headline_reconciliation": (
            "27 DATA_SCHEMA_GAP register entries (the audit's headline count) "
            "plus 1 IMPLEMENTATION_GAP-derived observation gap (L7) = 28 "
            "affected governed items; the L7 item is governed through "
            "GWI-L7-IMPL and the adjudicated observation_gap_transition."),
        "unaffected_governed_gaps": sorted(governed - set(affected)),
        "audited_observation_requirements": len(audited),
        "affected_observation_requirements": len(repointed),
        "repointed_observation_requirements": sorted(repointed),
        "retained_untouched_observation_requirements": retained,
        "unaccounted_gaps": len(unaccounted),
        "unaccounted_gap_ids": unaccounted,
        "gaps_without_owner": len(ownerless),
        "gaps_without_owner_ids": ownerless,
        "observation_requirements_lost": len(lost),
        "observation_requirement_ids_lost": lost,
    }


def build_store() -> dict[str, Any]:
    """Build the Root-1 dataset-authority overlay store (pure derivation)."""
    loaded = load_audit()
    requirements = root1_requirements(loaded)
    authority = canonical_dataset_authority(loaded)
    matrix_by_id = {str(r.get("id")): r
                    for r in loaded["matrix"]["observation_requirements"]}
    unknown = sorted(set(requirements) - set(matrix_by_id))
    if unknown:
        raise DatasetAuthorityError("ROOT1_REQUIREMENT_UNKNOWN:"
                                    + ",".join(unknown))

    satisfaction_registry = SD.build_registry()
    transitions = [requirement_transition(
                       loaded, matrix_by_id[rid], authority,
                       satisfaction_registry)
                   for rid in requirements]
    affected = affected_gap_ids(loaded, requirements)
    gaps = gap_dispositions(loaded, transitions, affected)

    declared = shadow_trades_declared_gaps(loaded)
    satisfied = [g for g in gaps if g["disposition"] == DISPOSITION_SATISFIED]
    unresolved = [g for g in gaps if g["disposition"] != DISPOSITION_SATISFIED]
    by_root = {root: sorted(
        g["gap_id"] for g in unresolved if root in
        g["blocked_by_root_changes"]) for root in ROOT_CHANGES}
    by_class = {name: sorted(
        t["observation_requirement_id"] for t in transitions
        if t["shadow_runtime_classification"] == name)
        for name in sorted(ALLOWED_CLASSIFICATIONS)}
    by_kind = {kind: sorted(
        g["gap_id"] for g in unresolved if kind in g["remaining_gap_kinds"])
        for kind in ("FIELD", "COVERAGE", "SEMANTIC", "LINEAGE")}

    store: dict[str, Any] = {
        "schema": STORE_SCHEMA,
        "stamp": STAMP,
        "governance_ref": GOVERNANCE_REF,
        "root_change_id": ROOT_CHANGE_01,
        "mode": "CONTRACT_GOVERNANCE_ONLY",
        "certification_fingerprint": EXPECTED_CERTIFICATION_FINGERPRINT,
        "upstream_audit_fingerprint": str(
            loaded["audit"].get("audit_fingerprint", "")),
        "dataset_authority": authority,
        "satisfaction_authority": {
            "state_path": str(SD.STATE_PATH),
            "role": "FINAL_GOVERNED_SATISFACTION_ONLY",
            "dataset_authority_role": "STRUCTURAL_EVIDENCE_AUTHORITY_ONLY",
        },
        "affected_observation_requirements": list(requirements),
        "observation_requirement_transitions": transitions,
        "audited_observation_requirements": sorted(matrix_by_id),
        "audited_requirement_authority": {
            rid: str(matrix_by_id[rid].get("authoritative_dataset", ""))
            for rid in sorted(matrix_by_id)},
        "affected_gap_ids": list(affected),
        "affected_gap_count": len(affected),
        "affected_gap_dispositions": gaps,
        "shadow_trades_declared_gap_count": len(declared),
        "shadow_trades_declared_gap_ids": list(declared),
        "classification_summary": by_class,
        "remaining_gap_kind_summary": by_kind,
        "gaps_satisfied_after_authority_correction": [
            g["gap_id"] for g in satisfied],
        "gaps_remaining_unresolved": [g["gap_id"] for g in unresolved],
        "gaps_by_root_change": by_root,
        "conservation": _conservation(loaded, affected, gaps, transitions),
        "mutation_ledger": {
            "s3_writes": 0,
            "schema_mutations": 0,
            "producer_mutations": 0,
            "research_reentry_events": 0,
            "backfills_performed": 0,
            "q71_started": False,
            "q71_started_count": 0,
            "new_producers_deployed": 0,
            "new_observables_added": 0,
            "scientific_finding_version_changes": 0,
            "scientific_result_version_changes": 0,
        },
        "producer_files_modified": [],
        "next_root_change": ROOT_CHANGE_02,
    }
    validate_store(store)
    store["store_fingerprint"] = _fp(
        {k: v for k, v in store.items() if k != "store_fingerprint"})
    return store


def persist(store: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Write the canonical authority state and both assurance artifacts.

    Read-only with respect to every producer, schema, dataset and scientific
    artifact: this only writes control-plane/assurance files.
    """
    resolved = dict(store) if store is not None else build_store()
    save_store(resolved)
    write_artifacts(resolved)
    return resolved

# ---------------------------------------------------------------------------
# Public governance accessors (TASK 4): these are what current governance
# consults.  They make the corrected authority the answerable question, so the
# phantom shadow_trades reference can no longer be selected as current evidence
# for an affected requirement.
# ---------------------------------------------------------------------------

def authoritative_dataset_for(requirement_id: str,
                              store: Mapping[str, Any] | None = None
                              ) -> str:
    """The dataset that currently holds authority for a requirement."""
    resolved = store if store is not None else build_store()
    rid = str(requirement_id)
    for transition in resolved["observation_requirement_transitions"]:
        if str(transition["observation_requirement_id"]) == rid:
            return str(transition["corrected_dataset"])
    # A requirement Root 1 did not re-point keeps its audited authority.
    return str(resolved["audited_requirement_authority"].get(rid, ""))


def is_authoritative_current_evidence(dataset: str,
                                      store: Mapping[str, Any] | None = None
                                      ) -> bool:
    """``shadow_trades`` can never be selected as current evidence."""
    resolved = store if store is not None else build_store()
    name = str(dataset)
    if name == SHADOW_TRADES:
        return bool(
            resolved["dataset_authority"][
                "supersedes_observation_reference"].get(
                    "authoritative_for_current_contracts", False))
    return name == SHADOW_RUNTIME


def dataset_history(dataset: str) -> dict[str, Any]:
    """Non-destructive dataset history: declarations are never erased."""
    authority = build_store()["dataset_authority"]
    prior = authority["supersedes_observation_reference"]
    if str(dataset) == SHADOW_TRADES:
        return dict(prior)
    if str(dataset) == SHADOW_RUNTIME:
        return {
            "dataset": SHADOW_RUNTIME,
            "dataset_version": authority["dataset_version"],
            "persistence_state": authority["status"],
            "persisted_objects": authority["persisted_objects"],
            "persisted_rows": authority["persisted_rows"],
            "producer": authority["producer"],
            "authoritative_for_current_contracts": True,
            "history_preserved": True,
        }
    raise DatasetAuthorityError("UNKNOWN_DATASET_HISTORY:" + str(dataset))


def transition_for(requirement_id: str,
                   store: Mapping[str, Any] | None = None
                   ) -> Mapping[str, Any]:
    resolved = store if store is not None else build_store()
    rid = str(requirement_id)
    for transition in resolved["observation_requirement_transitions"]:
        if str(transition["observation_requirement_id"]) == rid:
            return transition
    raise DatasetAuthorityError("NO_AUTHORITY_TRANSITION:" + rid)


def gap_is_satisfied(gap_id: str,
                     store: Mapping[str, Any] | None = None) -> bool:
    resolved = store if store is not None else build_store()
    for gap in resolved["affected_gap_dispositions"]:
        if str(gap["gap_id"]) == str(gap_id):
            return gap["disposition"] == DISPOSITION_SATISFIED
    raise DatasetAuthorityError("GAP_NOT_IN_ROOT1_AUTHORITY:" + str(gap_id))


def root_changes_for_gap(gap_id: str,
                         store: Mapping[str, Any] | None = None
                         ) -> tuple[str, ...]:
    resolved = store if store is not None else build_store()
    for gap in resolved["affected_gap_dispositions"]:
        if str(gap["gap_id"]) == str(gap_id):
            return tuple(gap["blocked_by_root_changes"])
    raise DatasetAuthorityError("GAP_NOT_IN_ROOT1_AUTHORITY:" + str(gap_id))




# ---------------------------------------------------------------------------
# Persistence + report rendering.
# ---------------------------------------------------------------------------

def save_store(store: Mapping[str, Any],
               path: Path = CANONICAL_STATE_PATH) -> None:
    path.write_text(json.dumps(store, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def load_canonical_state(
        path: Path = CANONICAL_STATE_PATH) -> dict[str, Any]:
    state = _read_json(path)
    validate_store(state)
    return state


def _table(rows: Sequence[Sequence[str]],
           header: Sequence[str]) -> list[str]:
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join("---" for _ in header) + "|"]
    for row in rows:
        out.append("| " + " | ".join(str(c) for c in row) + " |")
    return out


def render_markdown(store: Mapping[str, Any]) -> str:
    authority = store["dataset_authority"]
    prior = authority["supersedes_observation_reference"]
    lines: list[str] = [
        "# Stage 4 Root Change 1 — Shadow Runtime Authority",
        "",
        f"Stamp: {store['stamp']}  |  Root change: `{store['root_change_id']}`"
        f"  |  Mode: {store['mode']}",
        "",
        f"Upstream audit fingerprint: `{store['upstream_audit_fingerprint']}`",
        "",
        "## Previous authority",
        "",
        f"- dataset: `{prior['dataset']}` ({prior['dataset_version']})",
        f"- persisted objects: {prior['persisted_objects']}",
        f"- persisted rows: {prior['persisted_rows']}",
        f"- state: {prior['persistence_state']}",
        f"- authoritative for current observation contracts: "
        f"{prior['authoritative_for_current_contracts']}",
        f"- history preserved: {prior['history_preserved']}",
        "",
        "## Corrected authority",
        "",
        f"- dataset: `{authority['dataset']}`",
        f"- dataset version: `{authority['dataset_version']}`",
        f"- persisted objects: {authority['persisted_objects']}",
        f"- persisted rows: {authority['persisted_rows']}",
        f"- producer: `{authority['producer']}`",
        f"- status: {authority['status']}",
        "",
        "## Conservation",
        "",
    ]
    lines += _table(
        [[k, v] for k, v in sorted(store["conservation"].items())
         if isinstance(v, int)],
        ["metric", "value"])
    lines += ["", "## Observation requirement transitions", ""]
    lines += _table(
        [[t["observation_requirement_id"],
          t["previous_dataset_reference"],
          t["corrected_dataset"],
          t["shadow_runtime_classification"],
          ",".join(t["failing_gates"]) or "-",
          t["blocked_by_root_change"] or "-"]
         for t in store["observation_requirement_transitions"]],
        ["req", "was", "now", "classification", "failing gates",
         "blocked by"])
    lines += ["", "## Affected governed gaps", ""]
    lines += _table(
        [[g["gap_id"], ",".join(g["observation_requirement_ids"]),
          g["disposition"],
          ",".join(g["blocked_by_root_changes"]) or "-"]
         for g in store["affected_gap_dispositions"]],
        ["gap_id", "requirements", "disposition", "blocked by"])
    lines += ["", "## Remaining root changes", ""]
    lines += _table(
        [[root, len(store["gaps_by_root_change"][root]),
          ", ".join(store["gaps_by_root_change"][root]) or "-"]
         for root in ROOT_CHANGES],
        ["root change", "gaps", "gap ids"])
    lines += ["", "## Mutation ledger", ""]
    lines += _table(
        [[k, v] for k, v in sorted(store["mutation_ledger"].items())],
        ["ledger", "value"])
    lines += [
        "",
        "This is a CONTRACT/GOVERNANCE-ONLY correction. No producer, schema, "
        "S3, backfill, research re-entry or Q71+ action occurred.",
        "",
    ]
    return "\n".join(lines)


def write_artifacts(store: Mapping[str, Any]) -> None:
    ARTIFACT_JSON_PATH.write_text(
        json.dumps(store, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    ARTIFACT_MD_PATH.write_text(render_markdown(store), encoding="utf-8")

