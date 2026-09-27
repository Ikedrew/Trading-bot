"""Stage IV Wave 2 evidence integrity assurance.

This module is deliberately outside every live producer.  It describes and
reconciles evidence supplied by callers; source evidence remains authoritative.
Universe meaning is obtained exclusively from :mod:`.assurance`, while schema
and partition meaning is obtained from :mod:`core.production_data_contract`.

The public objects are immutable, JSON-compatible assurance artifacts.  Their
fingerprints exclude wall-clock generation time, making repeated audits over
unchanged evidence stable across process restarts.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from core.production_data_contract import (
    PRODUCTION_SCHEMA_REGISTRY,
    current_schema,
    partition_model,
    s3_base_prefix,
    supported_schemas,
)
from research_engine.v10.universes.assurance import (
    DatasetRole,
    PresenceContext,
    PresenceStatus,
    classify_presence,
    expected_active_universes,
    get_universe_contract,
)
from research_engine.v10.universes.models import Universe


INTEGRITY_SCHEMA_VERSION = 1
DEFAULT_ASSURANCE_DIRECTORY = Path("reports/research/evidence_integrity")


class IntegrityStatus(str, Enum):
    INTACT = "INTACT"
    MISSING_EXPECTED = "MISSING_EXPECTED"
    ABSENT_LEGITIMATE = "ABSENT_LEGITIMATE"
    DELAYED = "DELAYED"
    DUPLICATE = "DUPLICATE"
    BROKEN_CONTINUITY = "BROKEN_CONTINUITY"
    STALE = "STALE"
    ORDERING_VIOLATION = "ORDERING_VIOLATION"
    IDENTITY_CONFLICT = "IDENTITY_CONFLICT"
    COUNTERPART_MISSING = "COUNTERPART_MISSING"
    RECONSTRUCTABLE = "RECONSTRUCTABLE"
    RECONSTRUCTED = "RECONSTRUCTED"
    UNRECONSTRUCTABLE = "UNRECONSTRUCTABLE"
    AMBIGUOUS = "AMBIGUOUS"
    HISTORICAL_LIMITATION = "HISTORICAL_LIMITATION"
    NEW_UNMANIFESTED = "NEW_UNMANIFESTED"
    MANIFEST_SOURCE_MISSING = "MANIFEST_SOURCE_MISSING"
    SOURCE_MODIFIED = "SOURCE_MODIFIED"


class DuplicateKind(str, Enum):
    EXACT_RECORD = "EXACT_RECORD"
    IDENTITY_SAME_PAYLOAD = "IDENTITY_SAME_PAYLOAD"
    IDENTITY_CONFLICTING_PAYLOAD = "IDENTITY_CONFLICTING_PAYLOAD"


class CounterpartStatus(str, Enum):
    PRESENT = "PRESENT"
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    MISSING = "MISSING"
    AMBIGUOUS = "AMBIGUOUS"


class ReconstructionState(str, Enum):
    OBSERVED_ORIGINAL = "OBSERVED_ORIGINAL"
    RECONSTRUCTABLE = "RECONSTRUCTABLE"
    RECONSTRUCTED = "RECONSTRUCTED"
    INFERRED_EXPECTED_MISSING = "INFERRED_EXPECTED_MISSING"
    UNRECONSTRUCTABLE = "UNRECONSTRUCTABLE"
    AMBIGUOUS = "AMBIGUOUS"


class GapClass(str, Enum):
    LEGITIMATE_ABSENCE = "LEGITIMATE_ABSENCE"
    EXPECTED_MISSING = "EXPECTED_MISSING"
    LATE_ARRIVING = "LATE_ARRIVING"
    HISTORICAL_BOUNDARY = "HISTORICAL_BOUNDARY"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class EvidenceObject:
    """One source object/file participating in a scope."""

    identifier: str
    checksum: str = ""
    etag: str = ""
    persistence_timestamp: str = ""
    partition: str = ""


@dataclass(frozen=True)
class EvidenceBatch:
    """Observed records and their physical provenance for one semantic scope."""

    universe: Universe
    dataset: str
    records: tuple[Mapping[str, Any], ...] = ()
    source_objects: tuple[EvidenceObject, ...] = ()
    scope: Mapping[str, Any] = field(default_factory=dict)
    observation_start: str = ""
    observation_end: str = ""
    schema_version: str = ""
    source_available: bool | None = True
    qualifying_activity_expected: bool | None = None
    stale: bool = False
    historical_boundary: str = ""
    expected_arrival_lag_seconds: int | None = None


@dataclass(frozen=True)
class ExpectedEvidence:
    universe: Universe
    dataset: str
    identity: Mapping[str, Any]
    reason: str
    upstream_proof: tuple[str, ...] = ()
    gap_class: GapClass = GapClass.EXPECTED_MISSING
    reconstruction_hint: str = ""
    confidence: str = "PROVEN"


@dataclass(frozen=True)
class EvidenceManifest:
    universe: str
    dataset: str
    schema_version: str
    storage_prefix: str
    partition_model: str
    scope: Mapping[str, Any]
    observation_start: str
    observation_end: str
    first_event_timestamp: str
    last_event_timestamp: str
    record_count: int
    unique_identity_count: int
    duplicate_count: int
    conflicting_identity_count: int
    source_objects: tuple[Mapping[str, Any], ...]
    producer: str
    lineage_coverage: Mapping[str, int]
    lifecycle_coverage: Mapping[str, int]
    provenance: str
    reconstruction_state: str
    content_hash: str
    fingerprint: str
    schema: int = INTEGRITY_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _json_native(asdict(self))


@dataclass(frozen=True)
class IntegrityFinding:
    finding_id: str
    universe: str
    dataset: str
    category: str
    status: str
    severity: str
    identity: Mapping[str, Any]
    scope: Mapping[str, Any]
    first_affected_timestamp: str
    last_affected_timestamp: str
    expected_condition: str
    observed_condition: str
    evidence: tuple[str, ...]
    legitimate_absence_evaluation: str
    reconstruction_status: str
    provenance: str
    fingerprint: str
    schema: int = INTEGRITY_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _json_native(asdict(self))


@dataclass(frozen=True)
class ReconstructedArtifact:
    """Envelope that can never be confused with original source evidence."""

    artifact: Mapping[str, Any]
    target_universe: str
    target_dataset: str
    source_datasets: tuple[str, ...]
    source_identities: tuple[str, ...]
    reconstruction_rule: str
    rule_version: str
    source_complete: bool
    exact: bool
    information_loss: tuple[str, ...]
    reconstruction_timestamp: str
    state: str = ReconstructionState.RECONSTRUCTED.value

    def to_dict(self) -> dict[str, Any]:
        return _json_native(asdict(self))


@dataclass(frozen=True)
class ReconstructionLimitation:
    """A lifecycle that was deliberately not reconstructed.

    Limitations are kept separate from reconstructed artifacts so incomplete or
    ambiguous identities can never accidentally become target-universe rows.
    """

    identity: Mapping[str, Any]
    reason: str
    source_identities: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return _json_native(asdict(self))


@dataclass(frozen=True)
class ShadowReconstructionResult:
    artifacts: tuple[ReconstructedArtifact, ...]
    limitations: tuple[ReconstructionLimitation, ...]
    fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifacts": [item.to_dict() for item in self.artifacts],
            "limitations": [item.to_dict() for item in self.limitations],
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True)
class IntegrityReport:
    manifests: tuple[EvidenceManifest, ...]
    findings: tuple[IntegrityFinding, ...]
    reconstructions: tuple[ReconstructedArtifact, ...] = ()
    schema: int = INTEGRITY_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "active_universe_authority": (
                "research_engine.v10.universes.models.ACTIVE_UNIVERSES"
            ),
            "active_universes": [u.value for u in expected_active_universes()],
            "manifests": [m.to_dict() for m in self.manifests],
            "findings": [f.to_dict() for f in self.findings],
            "reconstructions": [r.to_dict() for r in self.reconstructions],
        }


def _json_native(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(k): _json_native(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_native(v) for v in value]
    return value


def _canonical(value: Any) -> str:
    return json.dumps(_json_native(value), sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _record_without_source(record: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in record.items() if k not in {"_source_object", "_persistence_timestamp"}}


def _field(record: Mapping[str, Any], name: str) -> Any:
    """Resolve flat fields plus known production nested envelopes."""
    if name in record:
        return record.get(name)
    aliases = {
        "trade_id": (("identity", "trade_id"),),
        "shadow_trade_id": (("identity", "shadow_trade_id"), ("identity", "trade_id")),
        "entity_id": (("identity", "entity_id"),),
        "correlation_id": (("identity", "correlation_id"),),
        "symbol": (("identity", "symbol"),),
        "entry_time": (("timestamps", "entry_timestamp_broker"),),
        "exit_time": (("timestamps", "exit_timestamp_broker"),),
        "timestamp_decision_utc": (("decision_snapshot", "timestamp_decision_utc"),),
        "exit_timestamp": (("simulated_outcome", "exit_timestamp"),),
    }
    for path in aliases.get(name, ()):
        value: Any = record
        for part in path:
            value = value.get(part) if isinstance(value, Mapping) else None
        if value not in (None, ""):
            return value
    return None


def _identity(
    record: Mapping[str, Any], universe: Universe, dataset: str = "",
) -> tuple[Any, ...]:
    if universe is Universe.SHADOW_OUTCOME and dataset == "shadow_runtime":
        if record.get("event_type") == "PLAN":
            return ("PLAN", record.get("plan_id"))
        return (
            record.get("event_type"), record.get("shadow_trade_id"),
            record.get("canonical_opportunity_id"), record.get("horizon"),
        )
    contract = get_universe_contract(universe)
    return tuple(_field(record, name) for name in contract.identity.primary_fields)


def _identity_dict(
    record: Mapping[str, Any], universe: Universe, dataset: str = "",
) -> dict[str, Any]:
    if universe is Universe.SHADOW_OUTCOME and dataset == "shadow_runtime":
        if record.get("event_type") == "PLAN":
            return {"event_type": "PLAN", "plan_id": record.get("plan_id")}
        return {
            "event_type": record.get("event_type"),
            "shadow_trade_id": record.get("shadow_trade_id"),
            "canonical_opportunity_id": record.get("canonical_opportunity_id"),
            "horizon": record.get("horizon"),
        }
    contract = get_universe_contract(universe)
    return {name: _field(record, name) for name in contract.identity.primary_fields}


def _timestamp_value(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            # Epoch milliseconds are common in runtime envelopes.
            number = float(value)
            if number > 100_000_000_000:
                number /= 1000.0
            return datetime.fromtimestamp(number, tz=timezone.utc)
        text = str(value).strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def _event_timestamps(batch: EvidenceBatch) -> list[str]:
    fields = get_universe_contract(batch.universe).timestamps.event_fields
    values: list[tuple[datetime, str]] = []
    for record in batch.records:
        for name in fields:
            raw = _field(record, name)
            parsed = _timestamp_value(raw)
            if parsed is not None:
                values.append((parsed, str(raw)))
    values.sort(key=lambda item: item[0])
    return [value for _, value in values]


def _duplicate_summary(
    batch: EvidenceBatch,
) -> tuple[
    int,
    int,
    list[tuple[DuplicateKind, tuple[Any, ...], tuple[Mapping[str, Any], ...]]],
]:
    by_identity: dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for record in batch.records:
        by_identity[_identity(record, batch.universe, batch.dataset)].append(record)
    duplicate_count = 0
    conflicts = 0
    detail: list[
        tuple[DuplicateKind, tuple[Any, ...], tuple[Mapping[str, Any], ...]]
    ] = []
    for identity, records in by_identity.items():
        if len(records) < 2:
            continue
        duplicate_count += len(records) - 1
        payloads = {_canonical(_record_without_source(r)) for r in records}
        sources = {str(r.get("_source_object", "")) for r in records}
        if len(payloads) > 1:
            kind = DuplicateKind.IDENTITY_CONFLICTING_PAYLOAD
            conflicts += 1
        elif len(sources) <= 1:
            kind = DuplicateKind.EXACT_RECORD
        else:
            kind = DuplicateKind.IDENTITY_SAME_PAYLOAD
        # Preserve the already-grouped records for finding construction.  The
        # previous count-only detail forced _identity_findings to rescan the
        # complete production batch once per duplicate identity (O(n*d)).
        detail.append((kind, identity, tuple(records)))
    return duplicate_count, conflicts, detail


def build_manifest(batch: EvidenceBatch) -> EvidenceManifest:
    """Build a deterministic semantic index over one supplied evidence scope."""
    if batch.universe not in expected_active_universes():
        raise ValueError(f"{batch.universe.value} is not an active analytical universe")
    contract = get_universe_contract(batch.universe)
    schema = batch.schema_version
    storage_prefix = "derived-from-universe"
    partition = "DERIVED"
    if batch.dataset in PRODUCTION_SCHEMA_REGISTRY:
        schema = schema or current_schema(batch.dataset)
        storage_prefix = s3_base_prefix(batch.dataset)
        partition = partition_model(batch.dataset).value
    identities = {_canonical(_identity(r, batch.universe, batch.dataset)) for r in batch.records}
    duplicate_count, conflicting_count, _ = _duplicate_summary(batch)
    timestamps = _event_timestamps(batch)
    source_objects = tuple(
        _json_native(asdict(item)) for item in sorted(batch.source_objects, key=lambda x: x.identifier)
    )
    lineage_fields = contract.identity.relationship_fields
    lineage = {
        name: sum(_field(record, name) not in (None, "") for record in batch.records)
        for name in lineage_fields
    }
    lifecycle: dict[str, int] = {}
    if batch.universe is Universe.SHADOW_OUTCOME and batch.dataset == "shadow_runtime":
        lifecycle = {
            event: sum(r.get("event_type") == event for r in batch.records)
            for event in ("PLAN", "OPEN", "PROGRESS", "CLOSE")
        }
    content_material = {
        "records": sorted(_canonical(_record_without_source(r)) for r in batch.records),
        "source_objects": source_objects,
    }
    stable = {
        "universe": batch.universe.value,
        "dataset": batch.dataset,
        "schema_version": schema,
        "scope": _json_native(batch.scope),
        "observation_start": batch.observation_start,
        "observation_end": batch.observation_end,
        "content_hash": _hash(content_material),
        "record_count": len(batch.records),
        "unique_identity_count": len(identities),
        "duplicate_count": duplicate_count,
        "conflicting_identity_count": conflicting_count,
    }
    return EvidenceManifest(
        universe=batch.universe.value,
        dataset=batch.dataset,
        schema_version=schema,
        storage_prefix=storage_prefix,
        partition_model=partition,
        scope=_json_native(batch.scope),
        observation_start=batch.observation_start,
        observation_end=batch.observation_end,
        first_event_timestamp=timestamps[0] if timestamps else "",
        last_event_timestamp=timestamps[-1] if timestamps else "",
        record_count=len(batch.records),
        unique_identity_count=len(identities),
        duplicate_count=duplicate_count,
        conflicting_identity_count=conflicting_count,
        source_objects=source_objects,
        producer=contract.builder,
        lineage_coverage=lineage,
        lifecycle_coverage=lifecycle,
        provenance="OBSERVED_SOURCE_INDEX",
        reconstruction_state=ReconstructionState.OBSERVED_ORIGINAL.value,
        content_hash=stable["content_hash"],
        fingerprint=_hash(stable),
    )


def _finding(
    batch: EvidenceBatch,
    *,
    category: str,
    status: IntegrityStatus,
    severity: str,
    identity: Mapping[str, Any] | None = None,
    expected: str,
    observed: str,
    evidence: Iterable[str] = (),
    absence: str = "NOT_APPLICABLE",
    reconstruction: ReconstructionState = ReconstructionState.AMBIGUOUS,
    first_timestamp: str = "",
    last_timestamp: str = "",
) -> IntegrityFinding:
    stable = {
        "universe": batch.universe.value,
        "dataset": batch.dataset,
        "category": category,
        "identity": _json_native(identity or {}),
        "scope": _json_native(batch.scope),
        "first": first_timestamp,
        "last": last_timestamp,
        "evidence": sorted(evidence),
    }
    fingerprint = _hash(stable)
    return IntegrityFinding(
        finding_id=f"EIF-{fingerprint[:16]}",
        universe=batch.universe.value,
        dataset=batch.dataset,
        category=category,
        status=status.value,
        severity=severity,
        identity=_json_native(identity or {}),
        scope=_json_native(batch.scope),
        first_affected_timestamp=first_timestamp,
        last_affected_timestamp=last_timestamp,
        expected_condition=expected,
        observed_condition=observed,
        evidence=tuple(sorted(evidence)),
        legitimate_absence_evaluation=absence,
        reconstruction_status=reconstruction.value,
        provenance="DETERMINISTIC_ASSURANCE_EVALUATION",
        fingerprint=fingerprint,
    )


def _presence_findings(batch: EvidenceBatch) -> list[IntegrityFinding]:
    status = classify_presence(
        batch.universe,
        PresenceContext(
            record_count=len(batch.records),
            source_available=batch.source_available,
            qualifying_activity_expected=batch.qualifying_activity_expected,
            stale=batch.stale,
        ),
    )
    if status is PresenceStatus.PRESENT:
        return []
    mapping = {
        PresenceStatus.ABSENT_LEGITIMATE: (IntegrityStatus.ABSENT_LEGITIMATE, "INFO", ReconstructionState.OBSERVED_ORIGINAL),
        PresenceStatus.ABSENT_UNEXPECTED: (IntegrityStatus.MISSING_EXPECTED, "ERROR", ReconstructionState.INFERRED_EXPECTED_MISSING),
        PresenceStatus.STALE: (IntegrityStatus.STALE, "WARNING", ReconstructionState.AMBIGUOUS),
        PresenceStatus.UNKNOWN_EXPECTATION: (IntegrityStatus.AMBIGUOUS, "WARNING", ReconstructionState.AMBIGUOUS),
        PresenceStatus.AMBIGUOUS_AUTHORITY: (IntegrityStatus.AMBIGUOUS, "ERROR", ReconstructionState.AMBIGUOUS),
    }
    if status not in mapping:
        return []
    integrity, severity, reconstruction = mapping[status]
    contract = get_universe_contract(batch.universe)
    return [_finding(
        batch, category="PRESENCE", status=integrity, severity=severity,
        expected=contract.expected_presence, observed=f"record_count={len(batch.records)}",
        evidence=(f"source_available={batch.source_available}", f"qualifying_activity_expected={batch.qualifying_activity_expected}"),
        absence=("AUTHORIZED_BY_CONTRACT" if status is PresenceStatus.ABSENT_LEGITIMATE else "NOT_PROVEN_LEGITIMATE"),
        reconstruction=reconstruction,
    )]


def _identity_findings(batch: EvidenceBatch) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    fields = get_universe_contract(batch.universe).identity.primary_fields
    for record in batch.records:
        identity = _identity_dict(record, batch.universe, batch.dataset)
        required_fields = tuple(identity) if batch.dataset == "shadow_runtime" else fields
        missing = [name for name in required_fields if identity[name] in (None, "")]
        if missing:
            findings.append(_finding(
                batch, category="IDENTITY_NULLABILITY", status=IntegrityStatus.IDENTITY_CONFLICT,
                severity="ERROR", identity=identity,
                expected=f"non-null canonical identity fields: {', '.join(fields)}",
                observed=f"missing: {', '.join(missing)}", evidence=(_hash(record),),
                reconstruction=ReconstructionState.UNRECONSTRUCTABLE,
            ))
    _, _, detail = _duplicate_summary(batch)
    for kind, values, duplicate_records in detail:
        count = len(duplicate_records)
        duplicate_fields = fields
        if batch.universe is Universe.SHADOW_OUTCOME and batch.dataset == "shadow_runtime":
            duplicate_fields = ("event_type", "shadow_trade_id", "canonical_opportunity_id", "horizon")
            if values and values[0] == "PLAN":
                duplicate_fields = ("event_type", "plan_id")
        identity = dict(zip(duplicate_fields, values))
        conflict = kind is DuplicateKind.IDENTITY_CONFLICTING_PAYLOAD
        findings.append(_finding(
            batch, category=kind.value,
            status=IntegrityStatus.IDENTITY_CONFLICT if conflict else IntegrityStatus.DUPLICATE,
            severity="ERROR" if conflict else "WARNING", identity=identity,
            expected="one canonical payload per canonical identity",
            observed=f"{count} records share identity ({kind.value})",
            evidence=tuple(sorted(
                _hash(_record_without_source(record))
                for record in duplicate_records
            )),
            reconstruction=ReconstructionState.UNRECONSTRUCTABLE if conflict else ReconstructionState.OBSERVED_ORIGINAL,
        ))
    return findings


def _timestamp_findings(batch: EvidenceBatch, now: datetime | None) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    contract = get_universe_contract(batch.universe)
    # A caller-supplied reference makes future-time checks reproducible.  With
    # no reference, wall-clock time is deliberately excluded from the audit.
    reference = now.astimezone(timezone.utc) if now is not None else None
    for record in batch.records:
        identity = _identity_dict(record, batch.universe, batch.dataset)
        parsed: dict[str, datetime] = {}
        for name in contract.timestamps.event_fields:
            raw = _field(record, name)
            if raw in (None, ""):
                continue
            value = _timestamp_value(raw)
            if value is None:
                findings.append(_finding(
                    batch, category="TIMESTAMP_REPRESENTATION", status=IntegrityStatus.ORDERING_VIOLATION,
                    severity="ERROR", identity=identity, expected=f"valid timezone-aware {name}",
                    observed=repr(raw), evidence=(_hash(record),), reconstruction=ReconstructionState.UNRECONSTRUCTABLE,
                ))
                continue
            parsed[name] = value
            if reference is not None and value > reference:
                findings.append(_finding(
                    batch, category="FUTURE_TIMESTAMP", status=IntegrityStatus.ORDERING_VIOLATION,
                    severity="ERROR", identity=identity, expected=f"{name} <= assurance reference time",
                    observed=str(raw), evidence=(_hash(record),), reconstruction=ReconstructionState.UNRECONSTRUCTABLE,
                    first_timestamp=str(raw), last_timestamp=str(raw),
                ))
        pairs: tuple[tuple[str, str], ...] = ()
        if batch.universe in {Universe.EXECUTION, Universe.OUTCOME}:
            pairs = (("entry_time", "exit_time"),)
        elif batch.universe is Universe.SHADOW_OUTCOME and batch.dataset != "shadow_runtime":
            pairs = (("timestamp_decision_utc", "exit_timestamp"),)
        for before, after in pairs:
            if before in parsed and after in parsed and parsed[after] < parsed[before]:
                findings.append(_finding(
                    batch, category="EVENT_ORDERING", status=IntegrityStatus.ORDERING_VIOLATION,
                    severity="ERROR", identity=identity, expected=f"{before} <= {after}",
                    observed=f"{_field(record, before)} > {_field(record, after)}",
                    evidence=(_hash(record),), reconstruction=ReconstructionState.UNRECONSTRUCTABLE,
                    first_timestamp=str(_field(record, after)), last_timestamp=str(_field(record, before)),
                ))
        event_values = sorted(parsed.values())
        canonical_event = event_values[0] if event_values else None
        partition_date = str(record.get("_partition_date", "") or "")
        if canonical_event is not None and partition_date and canonical_event.date().isoformat() != partition_date:
            findings.append(_finding(
                batch, category="PARTITION_TIMESTAMP_MISMATCH", status=IntegrityStatus.ORDERING_VIOLATION,
                severity="ERROR", identity=identity,
                expected=f"partition date {canonical_event.date().isoformat()} from canonical event timestamp",
                observed=f"partition date {partition_date}", evidence=(_hash(record),),
                reconstruction=ReconstructionState.UNRECONSTRUCTABLE,
            ))
        persisted_raw = record.get("_persistence_timestamp")
        if persisted_raw not in (None, "") and canonical_event is not None:
            persisted = _timestamp_value(persisted_raw)
            if persisted is None:
                findings.append(_finding(
                    batch, category="PERSISTENCE_TIMESTAMP_REPRESENTATION",
                    status=IntegrityStatus.ORDERING_VIOLATION, severity="ERROR", identity=identity,
                    expected="valid timezone-aware persistence timestamp", observed=repr(persisted_raw),
                    evidence=(_hash(record),), reconstruction=ReconstructionState.UNRECONSTRUCTABLE,
                ))
            elif persisted < canonical_event:
                findings.append(_finding(
                    batch, category="PERSISTENCE_BEFORE_EVENT", status=IntegrityStatus.ORDERING_VIOLATION,
                    severity="ERROR", identity=identity, expected="persistence at or after event time",
                    observed=str(persisted_raw), evidence=(_hash(record),),
                    reconstruction=ReconstructionState.UNRECONSTRUCTABLE,
                ))
            elif (
                batch.expected_arrival_lag_seconds is not None
                and (persisted - canonical_event).total_seconds() > batch.expected_arrival_lag_seconds
            ):
                findings.append(_finding(
                    batch, category="LATE_ARRIVAL", status=IntegrityStatus.DELAYED,
                    severity="WARNING", identity=identity,
                    expected=f"arrival within {batch.expected_arrival_lag_seconds}s",
                    observed=f"arrival lag={(persisted - canonical_event).total_seconds():.0f}s",
                    evidence=(_hash(record),), reconstruction=ReconstructionState.OBSERVED_ORIGINAL,
                ))
    return findings


def _shadow_lifecycle_findings(batch: EvidenceBatch) -> list[IntegrityFinding]:
    if batch.universe is not Universe.SHADOW_OUTCOME or batch.dataset != "shadow_runtime":
        return []
    findings: list[IntegrityFinding] = []
    groups: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for record in batch.records:
        key = (
            str(record.get("shadow_trade_id", "") or ""),
            str(record.get("canonical_opportunity_id", "") or ""),
            str(record.get("horizon", "") or ""),
        )
        groups[key].append(record)
    for key, events in groups.items():
        identity = {"shadow_trade_id": key[0], "canonical_opportunity_id": key[1], "horizon": key[2]}
        types = [str(e.get("event_type", "")) for e in events]
        opens, closes = types.count("OPEN"), types.count("CLOSE")
        if closes and not opens:
            findings.append(_finding(
                batch, category="CLOSE_WITHOUT_OPEN", status=IntegrityStatus.BROKEN_CONTINUITY,
                severity="ERROR", identity=identity, expected="OPEN before CLOSE",
                observed=f"events={types}", evidence=tuple(_hash(e) for e in events),
                reconstruction=ReconstructionState.UNRECONSTRUCTABLE,
            ))
        elif opens > 1:
            payloads = {_canonical(e.get("construction", {})) for e in events if e.get("event_type") == "OPEN"}
            findings.append(_finding(
                batch, category="MULTIPLE_OPEN", status=IntegrityStatus.IDENTITY_CONFLICT if len(payloads) > 1 else IntegrityStatus.DUPLICATE,
                severity="ERROR" if len(payloads) > 1 else "WARNING", identity=identity,
                expected="one compatible OPEN per lifecycle", observed=f"open_count={opens}",
                evidence=tuple(_hash(e) for e in events if e.get("event_type") == "OPEN"),
                reconstruction=ReconstructionState.UNRECONSTRUCTABLE if len(payloads) > 1 else ReconstructionState.RECONSTRUCTABLE,
            ))
        if closes > 1:
            payloads = {_canonical(e.get("outcome", {})) for e in events if e.get("event_type") == "CLOSE"}
            findings.append(_finding(
                batch, category="DUPLICATE_TERMINAL", status=IntegrityStatus.IDENTITY_CONFLICT if len(payloads) > 1 else IntegrityStatus.DUPLICATE,
                severity="ERROR", identity=identity, expected="at most one compatible CLOSE per lifecycle",
                observed=f"close_count={closes}", evidence=tuple(_hash(e) for e in events if e.get("event_type") == "CLOSE"),
                reconstruction=ReconstructionState.UNRECONSTRUCTABLE,
            ))
        if opens == 1 and closes == 0:
            # An open lifecycle is valid and pending, not corruption.
            continue
        order = {"PLAN": 0, "OPEN": 1, "PROGRESS": 2, "CLOSE": 3}
        timed: list[tuple[datetime, int]] = []
        for event in events:
            event_type = str(event.get("event_type", ""))
            raw_time = next((
                event.get(name) for name in (
                    "event_market_time_utc_epoch_s", "entry_market_time_utc_epoch_s",
                    "exit_market_time_utc_epoch_s", "timestamp_utc", "ts_utc_ms",
                ) if event.get(name) not in (None, "")
            ), None)
            parsed_time = _timestamp_value(raw_time)
            if parsed_time is not None and event_type in order:
                timed.append((parsed_time, order[event_type]))
        timed.sort(key=lambda item: (item[0], item[1]))
        sequence = [rank for _, rank in timed]
        if opens and closes and len(timed) >= 2 and sequence != sorted(sequence):
            findings.append(_finding(
                batch, category="LIFECYCLE_ORDERING", status=IntegrityStatus.ORDERING_VIOLATION,
                severity="ERROR", identity=identity, expected="PLAN -> OPEN -> PROGRESS -> CLOSE",
                observed=f"timestamp-ordered events violate lifecycle order; events={types}",
                evidence=tuple(sorted(_hash(e) for e in events)),
                reconstruction=ReconstructionState.UNRECONSTRUCTABLE,
            ))
    return findings


def _historical_findings(batch: EvidenceBatch) -> list[IntegrityFinding]:
    schema = batch.schema_version
    if batch.historical_boundary:
        return [_finding(
            batch, category="HISTORICAL_BOUNDARY", status=IntegrityStatus.HISTORICAL_LIMITATION,
            severity="INFO", expected="evaluate evidence only under guarantees available in its era",
            observed=batch.historical_boundary, reconstruction=ReconstructionState.UNRECONSTRUCTABLE,
        )]
    if batch.dataset in PRODUCTION_SCHEMA_REGISTRY and schema and schema != current_schema(batch.dataset):
        supported = schema in supported_schemas(batch.dataset)
        return [_finding(
            batch, category="HISTORICAL_SCHEMA", status=IntegrityStatus.HISTORICAL_LIMITATION if supported else IntegrityStatus.AMBIGUOUS,
            severity="INFO" if supported else "ERROR", expected=current_schema(batch.dataset), observed=schema,
            reconstruction=ReconstructionState.UNRECONSTRUCTABLE,
        )]
    contract = get_universe_contract(batch.universe)
    role = next((d.role for d in contract.datasets if d.dataset == batch.dataset), None)
    if role is DatasetRole.LEGACY_COMPATIBILITY:
        return [_finding(
            batch, category="LEGACY_COMPATIBILITY_SOURCE", status=IntegrityStatus.HISTORICAL_LIMITATION,
            severity="INFO", expected="legacy evidence remains non-authoritative",
            observed=f"{batch.dataset} is present only for compatibility",
            reconstruction=ReconstructionState.UNRECONSTRUCTABLE,
        )]
    return []


def detect_expected_gaps(batch: EvidenceBatch, expected: Iterable[ExpectedEvidence]) -> list[IntegrityFinding]:
    observed = {_canonical(_identity_dict(record, batch.universe, batch.dataset)) for record in batch.records}
    findings: list[IntegrityFinding] = []
    for item in expected:
        if item.universe is not batch.universe or item.dataset != batch.dataset:
            continue
        if _canonical(item.identity) in observed:
            continue
        status_by_class = {
            GapClass.LEGITIMATE_ABSENCE: IntegrityStatus.ABSENT_LEGITIMATE,
            GapClass.EXPECTED_MISSING: IntegrityStatus.MISSING_EXPECTED,
            GapClass.LATE_ARRIVING: IntegrityStatus.DELAYED,
            GapClass.HISTORICAL_BOUNDARY: IntegrityStatus.HISTORICAL_LIMITATION,
            GapClass.UNKNOWN: IntegrityStatus.AMBIGUOUS,
        }
        reconstruction = (
            ReconstructionState.RECONSTRUCTABLE if item.reconstruction_hint
            else ReconstructionState.UNRECONSTRUCTABLE if item.gap_class is GapClass.EXPECTED_MISSING
            else ReconstructionState.AMBIGUOUS
        )
        findings.append(_finding(
            batch, category="EVIDENCE_GAP", status=status_by_class[item.gap_class],
            severity="ERROR" if item.gap_class is GapClass.EXPECTED_MISSING else "INFO",
            identity=item.identity, expected=item.reason, observed="identity absent from supplied source evidence",
            evidence=item.upstream_proof, absence=("AUTHORIZED_BY_CONTRACT" if item.gap_class is GapClass.LEGITIMATE_ABSENCE else "NOT_PROVEN_LEGITIMATE"),
            reconstruction=reconstruction,
        ))
    return findings


def evaluate_counterparts(
    source: EvidenceBatch,
    available: Mapping[Universe, Sequence[Mapping[str, Any]]],
    *,
    pending_identities: Iterable[Mapping[str, Any]] = (),
) -> tuple[dict[str, Any], ...]:
    """Evaluate Wave 1 counterpart declarations without inventing conditions.

    Conditional relationships are required only when the source row itself
    supplies decisive facts (currently EXECUTE decisions and terminal
    execution/outcome projections). Otherwise the result is AMBIGUOUS rather
    than an asserted failure.
    """
    contract = get_universe_contract(source.universe)
    pending = {_canonical(value) for value in pending_identities}
    results: list[dict[str, Any]] = []
    for record in source.records:
        for counterpart in contract.counterparts:
            join = {name: _field(record, name) for name in counterpart.join_fields}
            usable = {name: value for name, value in join.items() if value not in (None, "")}
            required: bool | None
            if counterpart.expectation == "ALWAYS_EXPECTED":
                required = True
            elif source.universe is Universe.DECISION and counterpart.universe is Universe.EXECUTION:
                required = record.get("action") == "EXECUTE"
            elif source.universe is Universe.EXECUTION and counterpart.universe is Universe.DECISION:
                required = True
            elif source.universe is Universe.SHADOW_OUTCOME and counterpart.universe is Universe.DECISION:
                required = bool(_field(record, "entity_id"))
            else:
                required = None
            candidates = available.get(counterpart.universe, ())
            matches = [candidate for candidate in candidates if usable and all(_field(candidate, k) == v for k, v in usable.items())]
            if required is False:
                status = CounterpartStatus.NOT_REQUIRED
            elif matches:
                status = CounterpartStatus.PRESENT
            elif _canonical(usable) in pending:
                status = CounterpartStatus.PENDING
            elif required is True and usable:
                status = CounterpartStatus.MISSING
            else:
                status = CounterpartStatus.AMBIGUOUS
            results.append({
                "source_universe": source.universe.value,
                "counterpart_universe": counterpart.universe.value,
                "identity": usable,
                "condition": counterpart.condition,
                "status": status.value,
            })
    return tuple(results)


def counterpart_findings(source: EvidenceBatch, statuses: Iterable[Mapping[str, Any]]) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    for item in statuses:
        status = item.get("status")
        if status not in {CounterpartStatus.MISSING.value, CounterpartStatus.AMBIGUOUS.value}:
            continue
        findings.append(_finding(
            source, category="COUNTERPART", status=(IntegrityStatus.COUNTERPART_MISSING if status == CounterpartStatus.MISSING.value else IntegrityStatus.AMBIGUOUS),
            severity="ERROR" if status == CounterpartStatus.MISSING.value else "WARNING",
            identity=item.get("identity", {}), expected=str(item.get("condition", "")),
            observed=f"counterpart status={status}; universe={item.get('counterpart_universe')}",
            reconstruction=ReconstructionState.UNRECONSTRUCTABLE if status == CounterpartStatus.MISSING.value else ReconstructionState.AMBIGUOUS,
        ))
    return findings


def audit_batch(
    batch: EvidenceBatch,
    *,
    expected: Iterable[ExpectedEvidence] = (),
    now: datetime | None = None,
) -> tuple[EvidenceManifest, tuple[IntegrityFinding, ...]]:
    """Run bounded checks over one supplied scope; never scans history itself."""
    manifest = build_manifest(batch)
    findings = [
        *_presence_findings(batch),
        *_identity_findings(batch),
        *_timestamp_findings(batch, now),
        *_shadow_lifecycle_findings(batch),
        *_historical_findings(batch),
        *detect_expected_gaps(batch, expected),
    ]
    findings.sort(key=lambda item: item.fingerprint)
    return manifest, tuple(findings)


def reconcile_manifest(current: EvidenceManifest | None, recorded: EvidenceManifest | None) -> tuple[dict[str, Any], ...]:
    if recorded is None and current is not None:
        return ({"status": IntegrityStatus.NEW_UNMANIFESTED.value, "current": current.fingerprint},)
    if current is None and recorded is not None:
        return ({"status": IntegrityStatus.MANIFEST_SOURCE_MISSING.value, "recorded": recorded.fingerprint},)
    if current is None or recorded is None:
        return ({"status": IntegrityStatus.AMBIGUOUS.value},)
    if current.fingerprint == recorded.fingerprint:
        return ({"status": IntegrityStatus.INTACT.value, "fingerprint": current.fingerprint},)
    differences: list[dict[str, Any]] = []
    if current.record_count != recorded.record_count:
        differences.append({"status": IntegrityStatus.SOURCE_MODIFIED.value, "field": "record_count", "recorded": recorded.record_count, "current": current.record_count})
    if current.content_hash != recorded.content_hash:
        differences.append({"status": IntegrityStatus.SOURCE_MODIFIED.value, "field": "content_hash", "recorded": recorded.content_hash, "current": current.content_hash})
    if current.source_objects != recorded.source_objects:
        differences.append({"status": IntegrityStatus.SOURCE_MODIFIED.value, "field": "source_objects"})
    return tuple(differences or ({"status": IntegrityStatus.SOURCE_MODIFIED.value},))


def _shadow_lifecycle_identity(record: Mapping[str, Any]) -> tuple[str, str, str]:
    identity = record.get("identity")
    nested = identity if isinstance(identity, Mapping) else {}
    return (
        str(record.get("shadow_trade_id") or nested.get("shadow_trade_id") or nested.get("trade_id") or ""),
        str(record.get("canonical_opportunity_id") or nested.get("canonical_opportunity_id") or ""),
        str(record.get("horizon") or nested.get("evaluated_horizon") or nested.get("trade_horizon") or ""),
    )


def reconstruct_shadow_outcomes_report(
    events: Sequence[Mapping[str, Any]], *, reconstruction_timestamp: str,
) -> ShadowReconstructionResult:
    """Govern the existing canonical shadow reconstruction without rewriting it."""
    from research_engine.data_access.shadow_runtime_ingestion import reconstruct_completed_shadow_trades

    if _timestamp_value(reconstruction_timestamp) is None:
        raise ValueError("reconstruction_timestamp must be timezone-aware")
    source = [dict(event) for event in events]
    records = reconstruct_completed_shadow_trades(source)
    lifecycle_events: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for event in source:
        key = _shadow_lifecycle_identity(event)
        if all(key):
            lifecycle_events[key].append(event)
    artifacts: list[ReconstructedArtifact] = []
    limitations: list[ReconstructionLimitation] = []
    for record in records:
        key = _shadow_lifecycle_identity(record)
        identity = {
            "shadow_trade_id": key[0],
            "canonical_opportunity_id": key[1],
            "horizon": key[2],
        }
        if not all(key):
            limitations.append(ReconstructionLimitation(
                identity=identity,
                reason="INCOMPLETE_CANONICAL_LIFECYCLE_IDENTITY",
            ))
            continue
        matching = lifecycle_events.get(key, ())
        source_ids = tuple(sorted(
            f"{event.get('event_type', 'UNKNOWN')}:{key[0]}:"
            f"{event.get('canonical_opportunity_id', '')}:{event.get('horizon', '')}:"
            f"{_hash(event)[:16]}"
            for event in matching
        ))
        open_count = sum(event.get("event_type") == "OPEN" for event in matching)
        close_count = sum(event.get("event_type") == "CLOSE" for event in matching)
        if open_count != 1 or close_count != 1:
            limitations.append(ReconstructionLimitation(
                identity=identity,
                reason=(
                    "AMBIGUOUS_CANONICAL_LIFECYCLE_CARDINALITY:"
                    f"open={open_count},close={close_count}"
                ),
                source_identities=source_ids,
            ))
            continue
        artifacts.append(ReconstructedArtifact(
            artifact=record,
            target_universe=Universe.SHADOW_OUTCOME.value,
            target_dataset="shadow_trades",
            source_datasets=("shadow_runtime",),
            source_identities=source_ids,
            reconstruction_rule="shadow_runtime_open_close_to_terminal_outcome",
            rule_version="1",
            source_complete=True,
            exact=True,
            information_loss=("normalised universe rows omit some lifecycle-level lineage",),
            reconstruction_timestamp=reconstruction_timestamp,
        ))
    artifacts.sort(key=lambda item: _canonical(item.artifact))
    limitations.sort(key=lambda item: _canonical(item.to_dict()))
    payload = {
        "artifacts": [item.to_dict() for item in artifacts],
        "limitations": [item.to_dict() for item in limitations],
    }
    return ShadowReconstructionResult(
        artifacts=tuple(artifacts),
        limitations=tuple(limitations),
        fingerprint=_hash(payload),
    )


def reconstruct_shadow_outcomes(
    events: Sequence[Mapping[str, Any]], *, reconstruction_timestamp: str,
) -> tuple[ReconstructedArtifact, ...]:
    """Compatibility view containing only safely reconstructed artifacts."""
    return reconstruct_shadow_outcomes_report(
        events, reconstruction_timestamp=reconstruction_timestamp,
    ).artifacts


def reconstruct_outcomes(
    execution_records: Sequence[Mapping[str, Any]], *, reconstruction_timestamp: str,
) -> tuple[ReconstructedArtifact, ...]:
    """Project terminal Execution rows into governed Outcome artifacts."""
    if _timestamp_value(reconstruction_timestamp) is None:
        raise ValueError("reconstruction_timestamp must be timezone-aware")
    artifacts = []
    for record in execution_records:
        trade_id = _field(record, "trade_id")
        if trade_id in (None, "") or record.get("r_multiple") is None:
            continue
        artifacts.append(ReconstructedArtifact(
            artifact=dict(record), target_universe=Universe.OUTCOME.value,
            target_dataset="derived:outcome", source_datasets=("trade_truth", "execution_results"),
            source_identities=(str(trade_id),), reconstruction_rule="execution_to_outcome_identity_projection",
            rule_version="1", source_complete=True, exact=True, information_loss=(),
            reconstruction_timestamp=reconstruction_timestamp,
        ))
    artifacts.sort(key=lambda item: item.source_identities)
    return tuple(artifacts)


def build_report(
    batches: Iterable[EvidenceBatch],
    *,
    expectations: Iterable[ExpectedEvidence] = (),
    reconstructions: Iterable[ReconstructedArtifact] = (),
    now: datetime | None = None,
) -> IntegrityReport:
    expected = tuple(expectations)
    manifests: list[EvidenceManifest] = []
    findings: list[IntegrityFinding] = []
    for batch in batches:
        manifest, batch_findings = audit_batch(batch, expected=expected, now=now)
        manifests.append(manifest)
        findings.extend(batch_findings)
    manifests.sort(key=lambda item: (item.universe, item.dataset, item.fingerprint))
    findings.sort(key=lambda item: item.fingerprint)
    reconstructed = tuple(sorted(reconstructions, key=lambda item: _canonical(item.to_dict())))
    return IntegrityReport(tuple(manifests), tuple(findings), reconstructed)


def write_report(report: IntegrityReport, path: Path | str) -> Path:
    """Persist a reproducible assurance artifact, never production evidence."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = _canonical(report.to_dict()) + "\n"
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(target)
    return target


def describe_evidence_integrity() -> dict[str, Any]:
    """Machine-readable Wave 2 capabilities and explicit safety boundaries."""
    universes = []
    for universe in expected_active_universes():
        contract = get_universe_contract(universe)
        universes.append({
            "universe": universe.value,
            "datasets": [d.dataset for d in contract.datasets],
            "identity_fields": list(contract.identity.primary_fields),
            "timestamp_fields": list(contract.timestamps.event_fields),
            "legitimate_absence": list(contract.legitimate_absence),
            "dependencies": [asdict(d) for d in contract.dependencies],
            "counterparts": [_json_native(asdict(c)) for c in contract.counterparts],
        })
    return {
        "schema": INTEGRITY_SCHEMA_VERSION,
        "authority": "research_engine.v10.universes.assurance",
        "active_universe_authority": "research_engine.v10.universes.models.ACTIVE_UNIVERSES",
        "active_universes": [u.value for u in expected_active_universes()],
        "universes": universes,
        "reconstructable": {
            "SHADOW_OUTCOME": "shadow_runtime OPEN+CLOSE lifecycle",
            "OUTCOME": "validated terminal EXECUTION record",
            "MANIFEST": "supplied source records and object metadata",
        },
        "unreconstructable": (
            "missing original market or strategy observations",
            "missing broker execution facts not preserved elsewhere",
            "discarded historical lineage identifiers",
            "conflicting canonical identity payloads",
        ),
        "hot_path": False,
        "mutates_source_evidence": False,
    }


__all__ = [
    "DEFAULT_ASSURANCE_DIRECTORY", "INTEGRITY_SCHEMA_VERSION", "CounterpartStatus",
    "DuplicateKind", "EvidenceBatch", "EvidenceManifest", "EvidenceObject",
    "ExpectedEvidence", "GapClass", "IntegrityFinding", "IntegrityReport",
    "IntegrityStatus", "ReconstructedArtifact", "ReconstructionLimitation",
    "ReconstructionState", "ShadowReconstructionResult",
    "audit_batch", "build_manifest", "build_report", "counterpart_findings",
    "describe_evidence_integrity", "detect_expected_gaps", "evaluate_counterparts",
    "reconcile_manifest", "reconstruct_outcomes", "reconstruct_shadow_outcomes",
    "reconstruct_shadow_outcomes_report",
    "write_report",
]
