"""Canonical HD15 global research-validity assessment (G3).

G3 consumes frozen canonical control-plane state.  It never loads raw research
evidence and never invokes another question's runner.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from research_engine.control_plane.models import ReportValidity
from research_engine.control_plane.report_ownership import (
    canonical_report_owner,
    resolve_report_ownership,
)
from research_engine.control_plane.report_resolver import resolve_report_validity
from research_engine.experiments.experiment_base import build_report
from research_engine.registry import research_validity_adjudication as A
from research_engine.registry.definition_validator import build_definitions_from_registry
from research_engine.registry.learning_adaptation_adjudication import (
    FUTURE_L6,
    HD12_VERSION,
)
from research_engine.registry.master_repair_ledger import MASTER_REPAIR_LEDGER
from research_engine.registry.research_question_registry import REGISTRY, REGISTRY_BY_ID


REPORT_FILENAME = "g3_research_validity.json"
SNAPSHOT_VERSION = "g3_research_state_snapshot_v1"
CURRENT_EPOCH = "CURRENT"
MISSING_REQUIRED_DEPENDENCY = "MISSING_REQUIRED_DEPENDENCY"
INVALID_REQUIRED_REPORT_AUTHORITY = "INVALID_REQUIRED_REPORT_AUTHORITY"
AVAILABLE_VALID_CURRENT = "AVAILABLE_VALID_CURRENT"

_TRUST_STATES = frozenset(A.QUESTION_TRUST_STATES)
_RESULTS = frozenset(A.RESULT_VOCABULARY)
_POSITIVE = "RESEARCH_TRUST_DEMONSTRATED"
_NEGATIVE = "RESEARCH_TRUST_NOT_YET_DEMONSTRATED"
_BLOCKED = "EVALUATION_BLOCKED"
_UNKNOWN = "EVALUATION_UNKNOWN"


class SnapshotAuthorityError(ValueError):
    """The supplied G3 population/provenance cannot form a valid snapshot."""


def _primitive(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _primitive(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _primitive(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_primitive(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _canonical(value: Any) -> str:
    return json.dumps(
        _primitive(value), sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _value(record: Any, name: str, default: Any = None) -> Any:
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def _label(value: Any, default: str = "") -> str:
    if isinstance(value, Enum):
        value = value.value
    return str(value if value is not None else default).strip().upper()


def _canonical_population_ids() -> tuple[str, ...]:
    return tuple(question.id for question in REGISTRY if question.id != "G3")


def _registry_digest() -> str:
    return _digest([question.to_dict() for question in REGISTRY])


def _definition_digest() -> str:
    definitions = build_definitions_from_registry(REGISTRY)
    return _digest({qid: definitions[qid].to_dict() for qid in sorted(definitions)})


def _declared_owner(question_id: str) -> str | None:
    question = REGISTRY_BY_ID[question_id]
    filename = str(question.report_filename or "").strip()
    if not filename:
        return None
    adjudicated = canonical_report_owner(filename)
    if adjudicated is not None:
        return adjudicated
    claimants = [
        item.id for item in REGISTRY
        if str(item.report_filename or "").strip().lower() == filename.lower()
    ]
    return claimants[0] if claimants == [question_id] else None


def classify_question_state(state: Any) -> tuple[str, dict[str, Any]]:
    """Apply the exact HD15 precedence to one canonical QuestionState."""
    qid = str(_value(state, "question_id", "")).strip().upper()
    if not qid or qid == "G3" or qid not in REGISTRY_BY_ID:
        raise SnapshotAuthorityError(f"Invalid G3 population identity {qid or '<missing>'}")
    readiness = _label(_value(state, "readiness_status", "UNKNOWN"), "UNKNOWN")
    state_status = _label(_value(state, "state_status", readiness), readiness)
    runner_status = _label(_value(state, "runner_status", "NO_RUNNER"), "NO_RUNNER")
    report_validity = _label(
        _value(state, "report_validity", ReportValidity.MISSING),
        ReportValidity.MISSING.value,
    )
    report_status = _label(
        _value(state, "latest_report_status", _value(state, "report_status", "NOT_RUN")),
        "NOT_RUN",
    )
    evidence_epoch = _label(_value(state, "evidence_epoch", ""))
    finding = str(_value(state, "latest_finding", "") or "").strip()
    latest_result = _value(state, "latest_result", None)
    explicit_determinable = _value(state, "has_determinable_conclusion", None)
    determinable_conclusion = (
        bool(explicit_determinable)
        if explicit_determinable is not None
        else bool(finding or latest_result is not None)
    )
    structural = MASTER_REPAIR_LEDGER[qid].structurally_operational
    owner = _declared_owner(qid)
    declared_report = str(REGISTRY_BY_ID[qid].report_filename or "")
    authoritative = _value(state, "authoritative_report", None)
    authoritative_material = _primitive(authoritative) if authoritative is not None else None
    report_digest = str(_value(state, "report_digest", "") or "")
    if authoritative_material is not None:
        report_digest = _digest(authoritative_material)

    if readiness in {"UNKNOWN", "ERROR"} or state_status in {"UNKNOWN", "ERROR"}:
        trust_state = "UNKNOWN"
    elif report_validity == ReportValidity.UNKNOWN.value:
        trust_state = "UNKNOWN"
    elif readiness in {"WAITING_DATA", "INSUFFICIENT_DATA"} or state_status in {
        "WAITING_DATA", "INSUFFICIENT_DATA",
    }:
        trust_state = "WAITING_DATA"
    elif readiness == "BLOCKED" or state_status in {"BLOCKED", "INVALIDATED"}:
        trust_state = "BLOCKED"
    elif not structural or runner_status == "NO_RUNNER":
        trust_state = "NON_OPERATIONAL"
    elif (
        owner == qid
        and report_validity == ReportValidity.VALID_CURRENT.value
        and report_status == "COMPLETE"
        and readiness == "COMPLETE"
        and determinable_conclusion
        and len(report_digest) == 64
    ):
        trust_state = "TRUSTED_CURRENT_CONCLUSION"
    else:
        trust_state = "REPORT_NOT_VALID_CURRENT"

    row = {
        "question_id": qid,
        "trust_state": trust_state,
        "structurally_operational": structural,
        "readiness_status": readiness,
        "runner_status": runner_status,
        "report_filename": declared_report,
        "report_owner": owner or "",
        "report_validity": report_validity,
        "report_status": report_status,
        "evidence_epoch": evidence_epoch,
        "has_determinable_conclusion": determinable_conclusion,
        "report_digest": report_digest,
    }
    return trust_state, row


def _dependency(
    question_id: str,
    filename: str,
    report: Mapping[str, Any] | None,
    *,
    epoch: str,
) -> dict[str, Any]:
    if report is None:
        return {
            "question_id": question_id,
            "report_filename": filename,
            "owner": question_id,
            "availability": MISSING_REQUIRED_DEPENDENCY,
            "report_validity": ReportValidity.MISSING.value,
            "report_status": "MISSING",
            "result": "",
            "positive": False,
            "report_digest": "",
            "epoch": epoch,
            "authority_defect": False,
        }
    material = _primitive(report)
    ownership = resolve_report_ownership(filename, question_id, report_metadata=material)
    validity, _ = resolve_report_validity(
        filename, material, expected_question_id=question_id,
    )
    report_epoch = _label(material.get("epoch") or material.get("fingerprint", {}).get("epoch"))
    overall = material.get("overall", {})
    result = ""
    if isinstance(overall, Mapping):
        if question_id == "G1":
            result = str(overall.get("finding", ""))
        elif question_id == "G2":
            result = str(overall.get("threshold_result", ""))
    report_status = _label(material.get("status"))
    unknown = validity == ReportValidity.UNKNOWN
    authority_defect = (
        not ownership.allowed
        or validity not in {ReportValidity.VALID_CURRENT, ReportValidity.UNKNOWN}
        or report_epoch != epoch
    )
    positive = (
        validity == ReportValidity.VALID_CURRENT
        and report_status == "COMPLETE"
        and ((question_id == "G1" and result == "SUITABLE")
             or (question_id == "G2" and result == "LINEAGE_THRESHOLD_MET"))
    )
    return {
        "question_id": question_id,
        "report_filename": filename,
        "owner": ownership.canonical_owner or "",
        "availability": (
            "UNKNOWN" if unknown else
            AVAILABLE_VALID_CURRENT if validity == ReportValidity.VALID_CURRENT and not authority_defect else
            INVALID_REQUIRED_REPORT_AUTHORITY
        ),
        "report_validity": validity.value,
        "report_status": report_status,
        "result": result,
        "positive": positive,
        "report_digest": _digest(material),
        "epoch": report_epoch,
        "authority_defect": authority_defect,
    }


def _l6_dependency(report: Mapping[str, Any] | None, *, epoch: str) -> dict[str, Any]:
    if report is None:
        return {
            "question_id": "L6",
            "report_filename": FUTURE_L6["report"],
            "owner": "L6",
            "availability": MISSING_REQUIRED_DEPENDENCY,
            "report_validity": ReportValidity.MISSING.value,
            "report_status": "MISSING",
            "result": "",
            "positive": False,
            "report_digest": "",
            "epoch": epoch,
            "authority_defect": False,
            "contract_version": HD12_VERSION,
        }
    material = _primitive(report)
    question = REGISTRY_BY_ID["L6"]
    declared_filename = str(question.report_filename or "")
    wired = (
        MASTER_REPAIR_LEDGER["L6"].structurally_operational
        and declared_filename == FUTURE_L6["report"]
        and canonical_report_owner(declared_filename) == "L6"
    )
    validity = ReportValidity.INVALIDATED
    ownership_allowed = False
    if wired:
        ownership = resolve_report_ownership(
            declared_filename, "L6", report_metadata=material,
        )
        ownership_allowed = ownership.allowed
        validity, _ = resolve_report_validity(
            declared_filename, material, expected_question_id="L6",
        )
    report_epoch = _label(material.get("epoch") or material.get("fingerprint", {}).get("epoch"))
    report_status = _label(material.get("status"))
    valid_current = (
        wired and ownership_allowed and validity == ReportValidity.VALID_CURRENT
        and report_epoch == epoch
    )
    return {
        "question_id": "L6",
        "report_filename": FUTURE_L6["report"],
        "owner": "L6" if valid_current else "",
        "availability": AVAILABLE_VALID_CURRENT if valid_current else INVALID_REQUIRED_REPORT_AUTHORITY,
        "report_validity": validity.value,
        "report_status": report_status,
        "result": "HD12_COMPLETE" if valid_current and report_status == "COMPLETE" else "",
        "positive": valid_current and report_status == "COMPLETE",
        "report_digest": _digest(material),
        "epoch": report_epoch,
        "authority_defect": not valid_current,
        "contract_version": HD12_VERSION,
    }


@dataclass(frozen=True)
class ResearchStateSnapshot:
    """Deeply immutable G3 snapshot represented internally as canonical JSON."""

    snapshot_id: str
    _encoded_manifest: str

    def manifest(self) -> dict[str, Any]:
        return json.loads(self._encoded_manifest)


def freeze_research_state_snapshot(
    question_states: Sequence[Any],
    *,
    g1_report: Mapping[str, Any] | None,
    g2_report: Mapping[str, Any] | None,
    l6_report: Mapping[str, Any] | None = None,
    as_of_utc: str | None = None,
    epoch: str = CURRENT_EPOCH,
) -> ResearchStateSnapshot:
    """Freeze exactly 69 canonical states and three dependency records."""
    epoch = _label(epoch)
    if epoch != CURRENT_EPOCH:
        raise SnapshotAuthorityError("G3 requires the compatible CURRENT epoch")
    expected = _canonical_population_ids()
    supplied_ids = [str(_value(state, "question_id", "")).strip().upper() for state in question_states]
    if "G3" in supplied_ids:
        raise SnapshotAuthorityError("G3 self-reference is forbidden")
    if len(supplied_ids) != len(set(supplied_ids)):
        raise SnapshotAuthorityError("G3 population contains duplicate question identities")
    if set(supplied_ids) != set(expected) or len(supplied_ids) != 69:
        missing = sorted(set(expected) - set(supplied_ids))
        extra = sorted(set(supplied_ids) - set(expected))
        raise SnapshotAuthorityError(f"G3 population is incomplete: missing={missing}, extra={extra}")
    by_id = {qid: state for qid, state in zip(supplied_ids, question_states)}
    rows: list[dict[str, Any]] = []
    for qid in expected:
        _, row = classify_question_state(by_id[qid])
        if row["evidence_epoch"] and row["evidence_epoch"] != epoch:
            raise SnapshotAuthorityError(f"Mixed/incompatible epoch for {qid}")
        row["state_digest"] = _digest(row)
        rows.append(row)
    dependencies = [
        _dependency("G1", "g1_dataset_suitability.json", g1_report, epoch=epoch),
        _dependency("G2", "g2_lineage_coverage.json", g2_report, epoch=epoch),
        _l6_dependency(l6_report, epoch=epoch),
    ]
    for dependency in dependencies:
        dependency["dependency_digest"] = _digest(dependency)
    identity = {
        "version": SNAPSHOT_VERSION,
        "epoch": epoch,
        "as_of_utc": as_of_utc or datetime.now(timezone.utc).isoformat(),
        "registry_digest": _registry_digest(),
        "definition_digest": _definition_digest(),
        "contract_version": A.HD15_VERSION,
        "contract_digest": _digest(A.SNAPSHOT_CONTRACT),
        "population_order": list(expected),
        "question_states": rows,
        "dependencies": dependencies,
    }
    manifest = {**identity, "snapshot_id": _digest(identity)}
    encoded = _canonical(manifest)
    return ResearchStateSnapshot(manifest["snapshot_id"], encoded)


def validate_research_state_snapshot(value: Any) -> tuple[bool, str]:
    """Validate identity, population, components, authority and mutation safety."""
    if not isinstance(value, Mapping) or value.get("version") != SNAPSHOT_VERSION:
        return False, "Missing or unsupported G3 research-state snapshot"
    if value.get("epoch") != CURRENT_EPOCH or not value.get("as_of_utc"):
        return False, "G3 snapshot is not identified as one CURRENT epoch"
    expected = _canonical_population_ids()
    order = value.get("population_order")
    rows = value.get("question_states")
    if order != list(expected) or not isinstance(rows, list) or len(rows) != 69:
        return False, "G3 snapshot population/order is not exactly the 69 non-self identities"
    ids = [row.get("question_id") for row in rows if isinstance(row, Mapping)]
    if ids != list(expected) or len(ids) != len(set(ids)) or "G3" in ids:
        return False, "G3 snapshot population is missing, duplicated, reordered, or self-referential"
    for row in rows:
        material = dict(row)
        digest = material.pop("state_digest", "")
        if row.get("trust_state") not in _TRUST_STATES or digest != _digest(material):
            return False, "G3 question-state digest/classification is invalid"
        if row.get("evidence_epoch") not in {"", CURRENT_EPOCH}:
            return False, "G3 question state has a mixed/incompatible epoch"
        expected_state, _ = classify_question_state(row)
        if row.get("trust_state") != expected_state:
            return False, "G3 question trust state contradicts canonical authority"
    dependencies = value.get("dependencies")
    if not isinstance(dependencies, list) or [item.get("question_id") for item in dependencies] != ["G1", "G2", "L6"]:
        return False, "G3 mandatory dependency identities are incomplete or reordered"
    expected_dependency_identity = (
        ("G1", "g1_dataset_suitability.json", "G1"),
        ("G2", "g2_lineage_coverage.json", "G2"),
        ("L6", FUTURE_L6["report"], "L6"),
    )
    for dependency, (qid, filename, missing_owner) in zip(dependencies, expected_dependency_identity):
        if dependency.get("question_id") != qid or dependency.get("report_filename") != filename:
            return False, "G3 dependency report identity is invalid"
        if dependency.get("availability") == MISSING_REQUIRED_DEPENDENCY and dependency.get("owner") != missing_owner:
            return False, "G3 missing dependency owner identity is invalid"
    for dependency in dependencies:
        material = dict(dependency)
        digest = material.pop("dependency_digest", "")
        if digest != _digest(material):
            return False, "G3 dependency digest is invalid"
        availability = dependency.get("availability")
        if availability == MISSING_REQUIRED_DEPENDENCY:
            if (
                dependency.get("report_digest") or dependency.get("positive") is not False
                or dependency.get("authority_defect") is not False
            ):
                return False, "G3 missing dependency representation is contradictory"
        elif availability == AVAILABLE_VALID_CURRENT:
            if (
                dependency.get("report_validity") != ReportValidity.VALID_CURRENT.value
                or dependency.get("epoch") != CURRENT_EPOCH
                or len(str(dependency.get("report_digest", ""))) != 64
                or dependency.get("authority_defect") is not False
            ):
                return False, "G3 available dependency lacks VALID_CURRENT authority"
        elif availability == "UNKNOWN":
            if (
                dependency.get("report_validity") != ReportValidity.UNKNOWN.value
                or dependency.get("positive") is not False
            ):
                return False, "G3 UNKNOWN dependency representation is contradictory"
        elif availability == INVALID_REQUIRED_REPORT_AUTHORITY:
            if dependency.get("authority_defect") is not True or dependency.get("positive") is not False:
                return False, "G3 invalid dependency authority is contradictory"
        else:
            return False, "G3 dependency availability vocabulary is invalid"
    g1, g2, l6 = dependencies
    if g1.get("positive") is not (
        g1.get("availability") == AVAILABLE_VALID_CURRENT
        and g1.get("report_status") == "COMPLETE" and g1.get("result") == "SUITABLE"
    ):
        return False, "G3 G1 dependency aggregation is invalid"
    if g2.get("positive") is not (
        g2.get("availability") == AVAILABLE_VALID_CURRENT
        and g2.get("report_status") == "COMPLETE"
        and g2.get("result") == "LINEAGE_THRESHOLD_MET"
    ):
        return False, "G3 G2 dependency aggregation is invalid"
    l6_can_be_positive = (
        MASTER_REPAIR_LEDGER["L6"].structurally_operational
        and REGISTRY_BY_ID["L6"].report_filename == FUTURE_L6["report"]
        and canonical_report_owner(FUTURE_L6["report"]) == "L6"
    )
    if l6.get("positive") is not (
        l6_can_be_positive and l6.get("availability") == AVAILABLE_VALID_CURRENT
        and l6.get("report_status") == "COMPLETE"
    ):
        return False, "G3 L6 dependency aggregation is invalid"
    if value.get("registry_digest") != _registry_digest() or value.get("definition_digest") != _definition_digest():
        return False, "G3 registry/definition provenance no longer matches canonical authority"
    if value.get("contract_version") != A.HD15_VERSION or value.get("contract_digest") != _digest(A.SNAPSHOT_CONTRACT):
        return False, "G3 HD15 contract provenance is invalid"
    identity = {key: _primitive(item) for key, item in value.items() if key != "snapshot_id"}
    if value.get("snapshot_id") != _digest(identity):
        return False, "G3 snapshot identity does not match immutable provenance"
    return True, "Immutable HD15 G3 research-state snapshot is valid"


def aggregate_snapshot(manifest: Mapping[str, Any]) -> str:
    valid, _ = validate_research_state_snapshot(manifest)
    if not valid:
        return _BLOCKED
    states = [row["trust_state"] for row in manifest["question_states"]]
    dependencies = manifest["dependencies"]
    if "UNKNOWN" in states or any(item["availability"] == "UNKNOWN" for item in dependencies):
        return _UNKNOWN
    if any(item.get("authority_defect") for item in dependencies):
        return _BLOCKED
    if all(state == "TRUSTED_CURRENT_CONCLUSION" for state in states) and all(
        item.get("positive") is True for item in dependencies
    ):
        return _POSITIVE
    return _NEGATIVE


def _blocked_report(reason: str) -> dict[str, Any]:
    return build_report(
        question_id="G3", status="BLOCKED",
        overall={"finding": _BLOCKED, "authority_error": reason},
        confidence="INSUFFICIENT_DATA",
        dataset={"source": "canonical control-plane research state", "sample_size": 0},
        fingerprint={
            "dataset_id": "G3_EVALUATION_BLOCKED", "records_used": 0,
            "records_excluded": 0, "source": "canonical_control_plane",
            "epoch": CURRENT_EPOCH, "validation_score": "BLOCKED",
        },
        recommendation=_BLOCKED,
        assumptions=["No raw evidence or other research runner was invoked."],
        warnings=[reason],
        provenance={
            "experiment_module": __name__, "registry_id": "G3",
            "scientific_owner": "G3", "contract_version": A.HD15_VERSION,
            "report_identity": REPORT_FILENAME, "snapshot_error": reason,
        },
    )


def run_g3(
    *,
    question_states: Sequence[Any] | None = None,
    g1_report: Mapping[str, Any] | None = None,
    g2_report: Mapping[str, Any] | None = None,
    l6_report: Mapping[str, Any] | None = None,
    snapshot: ResearchStateSnapshot | None = None,
    as_of_utc: str | None = None,
) -> dict[str, Any]:
    """Run G3 from supplied canonical state only; never reconstruct science."""
    try:
        if snapshot is not None and question_states is not None:
            raise SnapshotAuthorityError("Supply either a frozen G3 snapshot or canonical states")
        if snapshot is None:
            if question_states is None:
                raise SnapshotAuthorityError("Canonical QuestionState population was not supplied")
            snapshot = freeze_research_state_snapshot(
                question_states, g1_report=g1_report, g2_report=g2_report,
                l6_report=l6_report, as_of_utc=as_of_utc,
            )
        manifest = snapshot.manifest()
        valid, reason = validate_research_state_snapshot(manifest)
        if not valid:
            raise SnapshotAuthorityError(reason)
    except (SnapshotAuthorityError, TypeError, ValueError) as exc:
        return _blocked_report(str(exc))

    result = aggregate_snapshot(manifest)
    status = "COMPLETE" if result in {_POSITIVE, _NEGATIVE} else "BLOCKED"
    states = [row["trust_state"] for row in manifest["question_states"]]
    return build_report(
        question_id="G3", status=status,
        overall={
            "finding": result,
            "question_count": len(states),
            "trust_state_counts": dict(sorted(Counter(states).items())),
            "dependencies": manifest["dependencies"],
            "unknown_prevents_completion": result == _UNKNOWN,
            "authority_defect_prevents_completion": result == _BLOCKED,
        },
        confidence="HIGH" if status == "COMPLETE" else "INSUFFICIENT_DATA",
        dataset={"source": "immutable canonical control-plane research state", "sample_size": 69},
        fingerprint={
            "dataset_id": manifest["snapshot_id"], "records_used": 69,
            "records_excluded": 0, "source": "canonical_control_plane",
            "epoch": CURRENT_EPOCH, "validation_score": CURRENT_EPOCH,
        },
        recommendation=result,
        assumptions=[
            "G3 did not invoke G1, G2, L6, or any underlying scientific runner.",
            "Finding direction and favourability were not trust gates.",
        ],
        provenance={
            "experiment_module": __name__, "registry_id": "G3",
            "scientific_owner": "G3", "contract_version": A.HD15_VERSION,
            "report_identity": REPORT_FILENAME, "snapshot": manifest,
        },
    )


def validate_g3_report(report: dict[str, Any]) -> tuple[bool, str]:
    """Validate G3 content; self-declared COMPLETE is never sufficient."""
    if report.get("question_id") != "G3":
        return False, "G3 report has wrong canonical identity"
    provenance = report.get("provenance", {})
    if (
        provenance.get("scientific_owner") != "G3"
        or provenance.get("report_identity") != REPORT_FILENAME
        or provenance.get("contract_version") != A.HD15_VERSION
    ):
        return False, "G3 ownership or HD15 contract identity is invalid"
    overall = report.get("overall", {})
    finding = overall.get("finding") if isinstance(overall, Mapping) else None
    if finding not in _RESULTS:
        return False, "G3 result vocabulary is invalid"
    if finding == _BLOCKED and "snapshot" not in provenance:
        if report.get("status") != "BLOCKED" or not provenance.get("snapshot_error"):
            return False, "G3-level blocked report lacks its authority failure"
        return True, "Owned HD15 G3 evaluation-blocked report is valid but non-completing"
    manifest = provenance.get("snapshot")
    valid, reason = validate_research_state_snapshot(manifest)
    if not valid:
        return False, reason
    expected = aggregate_snapshot(manifest)
    if finding != expected:
        return False, "G3 result contradicts deterministic HD15 aggregation"
    expected_status = "COMPLETE" if expected in {_POSITIVE, _NEGATIVE} else "BLOCKED"
    if report.get("status") != expected_status:
        return False, "G3 completion status contradicts the HD15 result"
    if overall.get("question_count") != 69:
        return False, "G3 report does not account for exactly 69 questions"
    expected_counts = dict(sorted(Counter(
        row["trust_state"] for row in manifest["question_states"]
    ).items()))
    if overall.get("trust_state_counts") != expected_counts:
        return False, "G3 trust-state accounting is inconsistent"
    forbidden = {"trust_score", "weighted_score", "ranking", "rank"}
    if forbidden & set(overall):
        return False, "HD15 forbids numeric trust scores and rankings"
    return True, "Owned deterministic HD15 G3 report is valid"


__all__ = [
    "AVAILABLE_VALID_CURRENT", "CURRENT_EPOCH", "INVALID_REQUIRED_REPORT_AUTHORITY",
    "MISSING_REQUIRED_DEPENDENCY", "REPORT_FILENAME", "ResearchStateSnapshot",
    "SNAPSHOT_VERSION", "SnapshotAuthorityError", "aggregate_snapshot",
    "classify_question_state", "freeze_research_state_snapshot", "run_g3",
    "validate_g3_report", "validate_research_state_snapshot",
]
