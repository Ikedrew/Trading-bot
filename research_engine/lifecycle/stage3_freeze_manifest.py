"""Deterministic integrity manifest for the completed Stage III contract.

The manifest freezes architecture, not live data, research conclusions, or
production authority.  A semantic architecture change produces a new S3F ID;
notes and timestamps are provenance only.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from research_engine.lifecycle.generated_research_identity import canonical_json
from research_engine.lifecycle.generated_research_isolation import (
    CANONICAL_DEFINITION_VERSION, CANONICAL_QUESTION_COUNT, canonical_inventory,
)

STAGE3_FREEZE_SCHEMA_VERSION = 1
STAGE3_ARCHITECTURE_VERSION = 1
STAGE3_INTEGRATION_CONTRACT_VERSION = 1
STAGE3_FREEZE_ID_PREFIX = "S3F-"
_S3F_RE = re.compile(r"^S3F-[0-9A-F]{16}$")
_HEX_RE = re.compile(r"^[0-9a-f]{64}$")

AUTHORITATIVE_MODULES = (
    ("wave0", "generated_research_identity"), ("wave0", "generated_research_isolation"), ("wave0", "generated_research_store"),
    ("wave1", "governed_dimension"), ("wave1", "dimension_registry"), ("wave1", "research_interaction"),
    ("wave2", "interaction_feasibility"), ("wave2", "eligibility_evidence_freeze"), ("wave2", "progressive_depth_gate"), ("wave2", "dataset_fingerprint"),
    ("wave3", "curiosity_signal"), ("wave3", "curiosity_proposal"), ("wave3", "curiosity_generator"),
    ("wave4", "search_provenance"), ("wave4", "search_multiplicity"), ("wave4", "search_record_store"),
    ("wave5", "research_opportunity"), ("wave5", "research_priority"), ("wave5", "research_agenda"), ("wave5", "research_queue"), ("wave5", "research_agenda_store"),
    ("wave6", "research_protocol"), ("wave6", "research_protocol_scope"), ("wave6", "research_protocol_store"),
    ("wave7", "treatment_memory"), ("wave7", "treatment_equivalence"), ("wave7", "revisit_governance"), ("wave7", "treatment_memory_store"),
    ("wave8", "research_observation_space"), ("wave8", "research_coverage"), ("wave8", "research_blind_spots"), ("wave8", "research_coverage_store"),
)

IDENTITY_NAMESPACES = (
    ("GEN-", "wave0", "generated_research"),
    ("DIM-", "wave1", "dimension"), ("IXN-", "wave1", "interaction"), ("SLC-", "wave1", "slice"),
    ("FSP-", "wave2", "feasibility_policy"), ("EVD-", "wave2", "evidence_freeze"),
    ("DEC-", "wave2", "eligibility_decision"), ("EXP-", "wave2", "depth_justification"),
    ("CSN-", "wave3", "curiosity_signal"), ("PRP-", "wave3", "curiosity_proposal"),
    ("ALT-", "wave4", "search_alternative"), ("FAM-", "wave4", "search_family"),
    ("SRC-", "wave4", "search_record"), ("FRZ-", "wave4", "selection_freeze"), ("MUL-", "wave4", "multiplicity"),
    ("ROP-", "wave5", "research_opportunity"), ("POL-", "wave5", "priority_policy"),
    ("AGD-", "wave5", "research_agenda"), ("QUE-", "wave5", "research_queue"), ("AFR-", "wave5", "agenda_freeze"),
    ("RPL-", "wave6", "research_protocol"), ("PFR-", "wave6", "protocol_freeze"), ("OOD-", "wave6", "out_of_scope_discovery"),
    ("TRS-", "wave7", "treatment_signature"), ("TMR-", "wave7", "treatment_memory"),
    ("RVP-", "wave7", "revisit_policy"), ("RVD-", "wave7", "revisit_decision"),
    ("OCP-", "wave8", "observation_policy"), ("OCR-", "wave8", "compatibility_rule"),
    ("OBS-", "wave8", "observation_space"), ("OBC-", "wave8", "observation_cell"),
    ("INV-", "wave8", "inventory_boundary"), ("CVS-", "wave8", "coverage_snapshot"), ("BSP-", "wave8", "blind_spot"),
    ("S3F-", "stage3", "architecture_freeze"),
)

SCHEMA_VERSIONS = (
    ("generated_research", 1), ("dimension", 1), ("interaction", 1),
    ("feasibility", 1), ("evidence_freeze", 1), ("eligibility", 1),
    ("curiosity_signal", 1), ("curiosity_proposal", 1), ("search_provenance", 1),
    ("research_opportunity", 1), ("priority_policy", 1), ("research_agenda", 1), ("research_queue", 1),
    ("research_protocol", 1), ("treatment_memory", 1), ("revisit_policy", 1),
    ("observation_space", 1), ("research_coverage", 1), ("stage3_freeze", 1),
)

GOVERNANCE_POLICY_VERSIONS = (
    ("wave5_priority_policy", "W5-LEX-V1"),
    ("wave7_revisit_policy", "1"),
    ("wave8_observation_construction_policy", "1"),
)

DEFERRED_CAPABILITIES = (
    "autonomous_research_execution", "backtest_or_experiment_orchestration",
    "candidate_construction_or_application", "candidate_validation_execution",
    "candidate_promotion", "shadow_or_production_deployment", "broker_execution",
    "data_hub_ui", "retail_and_prop_sim", "stage4_runtime_integration",
)


class Stage3FreezeError(RuntimeError): pass
class Stage3FreezeValidationError(Stage3FreezeError): pass
class Stage3FreezeIdentityConflict(Stage3FreezeError): pass


def _digest(value: Any) -> str: return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
def stage3_freeze_identity_for(value: str) -> str:
    if not isinstance(value, str) or not _HEX_RE.fullmatch(value): raise Stage3FreezeValidationError("semantic identity must be sha256 hex")
    return STAGE3_FREEZE_ID_PREFIX + value[:16].upper()
def is_stage3_freeze_identity(value: Any) -> bool: return isinstance(value, str) and bool(_S3F_RE.fullmatch(value))


def _canonical_pairs(values: Any, name: str) -> tuple[tuple[Any, ...], ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, (tuple, list)): raise Stage3FreezeValidationError(f"{name} must be a sequence")
    rows = tuple(sorted(tuple(row) for row in values))
    if len(rows) != len(set(rows)): raise Stage3FreezeValidationError(f"{name} contains duplicates")
    return rows


@dataclass(frozen=True)
class Stage3FreezeManifest:
    stage_identifier: str
    architecture_version: int
    canonical_definition_version: int
    canonical_question_count: int
    canonical_question_fingerprint: str
    authoritative_modules: tuple[tuple[str, str], ...]
    identity_namespaces: tuple[tuple[str, str, str], ...]
    schema_versions: tuple[tuple[str, int], ...]
    governance_policy_versions: tuple[tuple[str, str], ...]
    integration_contract_version: int
    deferred_capabilities: tuple[str, ...]
    note: str = ""
    created_at: str = ""
    semantic_identity: str = field(init=False)
    freeze_identity: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "authoritative_modules", _canonical_pairs(self.authoritative_modules, "authoritative_modules"))
        object.__setattr__(self, "identity_namespaces", _canonical_pairs(self.identity_namespaces, "identity_namespaces"))
        object.__setattr__(self, "schema_versions", _canonical_pairs(self.schema_versions, "schema_versions"))
        object.__setattr__(self, "governance_policy_versions", _canonical_pairs(self.governance_policy_versions, "governance_policy_versions"))
        deferred = tuple(sorted(self.deferred_capabilities))
        object.__setattr__(self, "deferred_capabilities", deferred)
        if self.stage_identifier != "STAGE_3": raise Stage3FreezeValidationError("stage_identifier must be STAGE_3")
        if any(type(v) is not int or v < 1 for v in (self.architecture_version, self.canonical_definition_version, self.canonical_question_count, self.integration_contract_version)):
            raise Stage3FreezeValidationError("versions and canonical count must be positive integers")
        if not _HEX_RE.fullmatch(self.canonical_question_fingerprint): raise Stage3FreezeValidationError("canonical fingerprint must be sha256 hex")
        prefixes = [row[0] for row in self.identity_namespaces]
        if len(prefixes) != len(set(prefixes)) or any(not re.fullmatch(r"[A-Z0-9]+-", prefix) for prefix in prefixes):
            raise Stage3FreezeValidationError("identity namespace prefixes must be unique and exact")
        if not deferred or len(deferred) != len(set(deferred)): raise Stage3FreezeValidationError("deferred capabilities must be unique and non-empty")
        semantic = _digest(self.semantic_material()); object.__setattr__(self, "semantic_identity", semantic); object.__setattr__(self, "freeze_identity", stage3_freeze_identity_for(semantic))

    def semantic_material(self) -> dict[str, Any]:
        return {"kind": "stage3_architecture_freeze", "schema_version": STAGE3_FREEZE_SCHEMA_VERSION,
                "stage_identifier": self.stage_identifier, "architecture_version": self.architecture_version,
                "canonical_definition_version": self.canonical_definition_version,
                "canonical_question_count": self.canonical_question_count,
                "canonical_question_fingerprint": self.canonical_question_fingerprint,
                "authoritative_modules": [list(row) for row in self.authoritative_modules],
                "identity_namespaces": [list(row) for row in self.identity_namespaces],
                "schema_versions": [list(row) for row in self.schema_versions],
                "governance_policy_versions": [list(row) for row in self.governance_policy_versions],
                "integration_contract_version": self.integration_contract_version,
                "deferred_capabilities": list(self.deferred_capabilities)}

    def to_dict(self) -> dict[str, Any]:
        return {**self.semantic_material(), "note": self.note, "created_at": self.created_at,
                "semantic_identity": self.semantic_identity, "freeze_identity": self.freeze_identity}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Stage3FreezeManifest":
        expected = {"kind", "schema_version", "stage_identifier", "architecture_version", "canonical_definition_version",
                    "canonical_question_count", "canonical_question_fingerprint", "authoritative_modules", "identity_namespaces",
                    "schema_versions", "governance_policy_versions", "integration_contract_version", "deferred_capabilities", "note", "created_at",
                    "semantic_identity", "freeze_identity"}
        if not isinstance(data, Mapping) or set(data) != expected or data["kind"] != "stage3_architecture_freeze" or data["schema_version"] != STAGE3_FREEZE_SCHEMA_VERSION:
            raise Stage3FreezeValidationError("invalid freeze manifest payload")
        obj = cls(stage_identifier=data["stage_identifier"], architecture_version=data["architecture_version"],
                  canonical_definition_version=data["canonical_definition_version"], canonical_question_count=data["canonical_question_count"],
                  canonical_question_fingerprint=data["canonical_question_fingerprint"],
                  authoritative_modules=tuple(tuple(row) for row in data["authoritative_modules"]),
                  identity_namespaces=tuple(tuple(row) for row in data["identity_namespaces"]),
                  schema_versions=tuple(tuple(row) for row in data["schema_versions"]),
                  governance_policy_versions=tuple(tuple(row) for row in data["governance_policy_versions"]),
                  integration_contract_version=data["integration_contract_version"],
                  deferred_capabilities=tuple(data["deferred_capabilities"]), note=data["note"], created_at=data["created_at"])
        if data["semantic_identity"] != obj.semantic_identity or data["freeze_identity"] != obj.freeze_identity:
            raise Stage3FreezeIdentityConflict("freeze manifest identity mismatch")
        return obj


def current_stage3_freeze_manifest(*, note: str = "", created_at: str = "") -> Stage3FreezeManifest:
    canonical = canonical_inventory()
    fingerprint = _digest({"canonical_definition_version": CANONICAL_DEFINITION_VERSION, "canonical_questions": list(canonical)})
    return Stage3FreezeManifest(
        stage_identifier="STAGE_3", architecture_version=STAGE3_ARCHITECTURE_VERSION,
        canonical_definition_version=CANONICAL_DEFINITION_VERSION, canonical_question_count=CANONICAL_QUESTION_COUNT,
        canonical_question_fingerprint=fingerprint, authoritative_modules=AUTHORITATIVE_MODULES,
        identity_namespaces=IDENTITY_NAMESPACES, schema_versions=SCHEMA_VERSIONS,
        governance_policy_versions=GOVERNANCE_POLICY_VERSIONS,
        integration_contract_version=STAGE3_INTEGRATION_CONTRACT_VERSION,
        deferred_capabilities=DEFERRED_CAPABILITIES, note=note, created_at=created_at)
