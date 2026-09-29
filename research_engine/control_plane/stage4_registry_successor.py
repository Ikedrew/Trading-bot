"""Governed Stage 4 registry/contract successor for implementation re-entry.

The canonical ``research_question_registry`` remains the immutable V1 authority.
This module is an additive V2 overlay and is only selected by a pinned re-entry
request.  Questions not named here retain their byte-for-byte V1 definition.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from typing import Any

from research_engine.registry.research_question_registry import REGISTRY, REGISTRY_BY_ID

REGISTRY_VERSION = "stage4_implementation_registry_v2"
PREDECESSOR_REGISTRY_VERSION = "research_question_registry_v1"
GOVERNANCE_REF = "STAGE4-IMPLEMENTATION-GAP-RESOLUTION-20260929"


SUCCESSOR_OVERRIDES: dict[str, dict[str, Any]] = {
    "L3": {
        "runner_module": "research_engine.experiments.architecture_assumption_validity",
        "runner_function": "run_l3",
        "report_filename": "l3_architecture_assumption_validity.json",
    },
    "L6": {
        "runner_module": "research_engine.experiments.learning_cycle_validation",
        "runner_function": "run_l6",
        "report_filename": "l6_learning_cycle_validation.json",
    },
    "L7": {
        "runner_module": "research_engine.experiments.adaptation_evidence",
        "runner_function": "run_l7",
        "report_filename": "l7_adaptation_evidence.json",
        # The arm is the producer-issued, versioned experiment-arm block.  It
        # lives in `experiment_arm` / `arm_schema_version`, NEVER in
        # `schema_version`: the latter is the record-structure identity
        # (shadow_runtime_v1) and would be read as an arm by any runner that
        # treats it as one.  See ROOT-05 / L7_REQUIRED_CONTRACT_FIELDS.
        "required_fields": (
            "r_multiple", "strategy", "experiment_arm", "arm_schema_version",
        ),
    },
    "EX2": {
        "runner_module": "research_engine.experiments.exit_policy_governed",
        "runner_function": "run_ex2",
        "report_filename": "ex2_profit_retention.json",
        # EX2 is bound to the exact frozen governed lifecycle population.  The
        # 9,045 HD09 reconstruction denominator is permanently forbidden: it
        # may never be substituted for the governed roster.
        "required_fields": ("mfe_r", "pnl_r_multiple", "trade_state_progression"),
    },
}

QUESTION_CONTRACT_VERSION = {qid: 2 for qid in SUCCESSOR_OVERRIDES}
EVIDENCE_CONTRACT_VERSION = {
    "L3": "v2", "L6": "v2", "L7": "v2", "EX2": "v2",
}
EVIDENCE_CONTRACT_EXTENSIONS = {
    "L3": {"scientific_report_owner": "L3", "forbidden_report": "q1_component_reward.json"},
    "L6": {"analytical_population": 1852, "runner_input": "records"},
    "L7": {
        # The assignment field is `experiment_arm`, NOT `schema_version`.
        # `schema_version` is the record-structure identity (shadow_runtime_v1)
        # and is never an arm; that exact collision is what ROOT-05 removes.
        "assignment_field": "experiment_arm",
        "assignment_block": "arm_pre_outcome_attestation + experiment_arm",
        "assignment_schema_version_field": "arm_schema_version",
        "assignment_vocabulary": ["CONTROL", "CANDIDATE"],
        "assignment_authority": "producer-issued pre-outcome arm block",
        "assignment_mechanism": "DETERMINISTIC_IDENTITY_HASH",
        "assignment_policy_version": "experiment_arm_policy_v2",
        "assignment_timestamp_field": "arm_assigned_at_market_time_utc",
        "assignment_issued_at_event": "OPEN",
        "unknown_label_behavior": "FAIL_CLOSED",
        "unassigned_behavior": "FAIL_CLOSED_NO_DEFAULT_TO_CONTROL",
        "chronology_substitution": "FORBIDDEN",
        "dataset_schema_identity_is_not_an_arm": True,
        "schema_version_is_not_an_arm": True,
        "threshold_min": {"control": 100, "candidate": 100, "cell": 30},
    },
    "EX2": {
        "analytical_population": 8760,
        "runner_input": "governed_records",
        "forbidden_runner_population": 9045,
        "path_authority": "events_v1:CANDLE:mt5_data:M5 ordered OHLC",
        "inadequate_reconstruction": ["bar", "close", "r"],
    },
}
REPORT_OWNERS = {
    "l3_architecture_assumption_validity.json": "L3",
    "l6_learning_cycle_validation.json": "L6",
    "l7_adaptation_evidence.json": "L7",
}


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        default=str).encode("utf-8")).hexdigest()


def question(question_id: str):
    """Return the V2 definition without mutating the frozen V1 object."""
    qid = str(question_id).strip().upper()
    base = REGISTRY_BY_ID[qid]
    override = SUCCESSOR_OVERRIDES.get(qid)
    return replace(base, **override) if override else base


REGISTRY_V2 = tuple(question(item.id) for item in REGISTRY)
REGISTRY_V2_BY_ID = {item.id: item for item in REGISTRY_V2}


def definition_material(question_id: str) -> dict[str, Any]:
    item = question(question_id)
    return {
        "question_id": item.id,
        "title": item.title,
        "description": item.description,
        "category": item.category,
        "runner_module": item.runner_module,
        "runner_function": item.runner_function,
        "report_filename": item.report_filename,
        "required_fields": item.required_fields,
        "validation_rules": item.validation_rules,
        "data_sources": [str(getattr(source, "value", source)) for source in item.data_sources],
        "depends_on": list(item.depends_on),
        "scientific_owner_id": item.scientific_owner_id,
        "legacy_ids": list(item.legacy_ids),
        "evidence_contract_extension": EVIDENCE_CONTRACT_EXTENSIONS.get(item.id),
    }


def registry_fingerprint() -> str:
    return _fingerprint({item.id: definition_material(item.id) for item in REGISTRY_V2})


def contract_evolution(question_id: str) -> dict[str, Any] | None:
    qid = str(question_id).strip().upper()
    if qid not in SUCCESSOR_OVERRIDES:
        return None
    return {
        "from_version": 1,
        "to_version": 2,
        "semantic_change": True,
        "governance_ref": GOVERNANCE_REF,
        "predecessor_registry_version": PREDECESSOR_REGISTRY_VERSION,
        "successor_registry_version": REGISTRY_VERSION,
    }


def canonical_report_owner(report_filename: str) -> str | None:
    key = str(report_filename or "").replace("\\", "/").rsplit("/", 1)[-1].lower()
    return REPORT_OWNERS.get(key)
