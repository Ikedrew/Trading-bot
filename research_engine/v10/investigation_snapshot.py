"""Common immutable snapshots for the six manual investigation views.

This is an integration layer over the Stage 4 dataset-population authority,
not a competing snapshot identity system. Each non-empty dataset receives the
existing ``DatasetSnapshot`` identity; this manifest content-addresses their
exact common S3 object population and pins the source boundaries used by every
view.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from core.production_data_contract import (
    DATA_CONTRACT_VERSION,
    PRODUCTION_SCHEMA_REGISTRY,
    current_schema,
)
from research_engine.control_plane import stage4_dataset_snapshot as D
from research_engine.data_access.s3_source import (
    ResearchDataSourceError,
    S3ResearchDataSource,
    get_default_source,
)


MANIFEST_SCHEMA = 1
MANIFEST_DIRECTORY = D.ROOT / "analysis" / "assurance" / "investigation_snapshots"
REQUIRED_DATASETS = (
    "trade_truth", "execution_results", "decision_trace", "shadow_runtime",
    "execution_attempts", "execution_context",
)
OPTIONAL_DATASETS = (
    "market_context", "strategy_observations", "protection_audit",
    "risk_deviation",
)
BOUND_DATASETS = REQUIRED_DATASETS + OPTIONAL_DATASETS
EXCLUDED_BY_DESIGN = (
    "research_universe", "shadow_trades", "research_shadow_trades",
    "trade_journal", "portfolio_rankings", "portfolio_shadow",
    "decision_ledger", "local_logs", "research_artifacts",
)
_HEX64 = frozenset("0123456789abcdef")


class InvestigationSnapshotError(RuntimeError):
    """A common investigation snapshot could not be frozen or verified."""


def _is_sha256(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 64 and set(text.lower()) <= _HEX64


def _validate_date(value: str, name: str) -> str:
    try:
        parsed = date.fromisoformat(str(value))
    except ValueError as exc:
        raise InvestigationSnapshotError(f"INVALID_{name.upper()}") from exc
    return parsed.isoformat()


def _listing_material(objects: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "identifier": str(item.get("identifier") or ""),
            "etag": str(item.get("etag") or ""),
            "size": int(item.get("size") or 0),
            "last_modified": str(item.get("last_modified") or ""),
        }
        for item in objects
    ]


@dataclass(frozen=True)
class BoundObject:
    identifier: str
    etag: str
    size: int
    last_modified: str
    version_id: str | None
    content_sha256: str
    byte_size: int
    row_count: int

    def __post_init__(self) -> None:
        if not self.identifier or not _is_sha256(self.content_sha256):
            raise InvestigationSnapshotError("INVALID_BOUND_OBJECT_IDENTITY")
        if self.size < 0 or self.byte_size < 0 or self.row_count < 0:
            raise InvestigationSnapshotError("NEGATIVE_BOUND_OBJECT_COUNT")

    @classmethod
    def from_metadata(cls, value: Mapping[str, Any]) -> "BoundObject":
        return cls(
            identifier=str(value.get("identifier") or ""),
            etag=str(value.get("etag") or ""),
            size=int(value.get("size") or 0),
            last_modified=str(value.get("last_modified") or ""),
            version_id=(None if value.get("version_id") is None
                        else str(value.get("version_id"))),
            content_sha256=str(value.get("content_sha256") or ""),
            byte_size=int(value.get("byte_size") or 0),
            row_count=int(value.get("row_count") or 0),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DatasetBinding:
    dataset: str
    requirement: str
    presence: str
    schema_version: str
    schema_generation: int | None
    dataset_snapshot_id: str | None
    dataset_snapshot_json: str | None
    source_object_count: int
    source_row_count: int
    content_digest: str
    objects: tuple[BoundObject, ...]
    stage4_evidence_epochs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.requirement not in {"REQUIRED", "OPTIONAL"}:
            raise InvestigationSnapshotError("INVALID_DATASET_REQUIREMENT")
        if self.presence not in {"PRESENT", "ABSENT"}:
            raise InvestigationSnapshotError("INVALID_DATASET_PRESENCE")
        if self.presence == "PRESENT" and self.source_object_count == 0:
            raise InvestigationSnapshotError("PRESENT_DATASET_WITHOUT_OBJECTS")
        if self.presence == "ABSENT" and (self.source_object_count or self.source_row_count):
            raise InvestigationSnapshotError("ABSENT_DATASET_WITH_CONTENT")
        if self.source_object_count != len(self.objects):
            raise InvestigationSnapshotError("DATASET_OBJECT_COUNT_MISMATCH")
        if self.source_row_count != sum(item.row_count for item in self.objects):
            raise InvestigationSnapshotError("DATASET_ROW_COUNT_MISMATCH")
        if not _is_sha256(self.content_digest):
            raise InvestigationSnapshotError("INVALID_DATASET_CONTENT_DIGEST")
        keys = [item.identifier for item in self.objects]
        if len(keys) != len(set(keys)):
            raise InvestigationSnapshotError("DUPLICATE_DATASET_OBJECT_KEY")
        if self.dataset_snapshot_id is None:
            if self.dataset_snapshot_json is not None:
                raise InvestigationSnapshotError("UNBOUND_DATASET_HAS_SNAPSHOT_RECORD")
        else:
            try:
                snapshot = D.DatasetSnapshot.from_dict(
                    json.loads(str(self.dataset_snapshot_json)))
            except (TypeError, ValueError) as exc:
                raise InvestigationSnapshotError(
                    "INVALID_STAGE4_DATASET_SNAPSHOT") from exc
            if snapshot.dataset_snapshot_id != self.dataset_snapshot_id \
                    or snapshot.dataset_name != self.dataset:
                raise InvestigationSnapshotError("STAGE4_DATASET_SNAPSHOT_MISMATCH")

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "requirement": self.requirement,
            "presence": self.presence,
            "schema_version": self.schema_version,
            "schema_generation": self.schema_generation,
            "dataset_snapshot_id": self.dataset_snapshot_id,
            "dataset_snapshot": (
                None if self.dataset_snapshot_json is None
                else json.loads(self.dataset_snapshot_json)),
            "source_object_count": self.source_object_count,
            "source_row_count": self.source_row_count,
            "content_digest": self.content_digest,
            "objects": [item.to_dict() for item in self.objects],
            "stage4_evidence_epochs": list(self.stage4_evidence_epochs),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DatasetBinding":
        child = value.get("dataset_snapshot")
        return cls(
            dataset=str(value.get("dataset") or ""),
            requirement=str(value.get("requirement") or ""),
            presence=str(value.get("presence") or ""),
            schema_version=str(value.get("schema_version") or ""),
            schema_generation=(None if value.get("schema_generation") is None
                               else int(value["schema_generation"])),
            dataset_snapshot_id=(None if value.get("dataset_snapshot_id") is None
                                 else str(value["dataset_snapshot_id"])),
            dataset_snapshot_json=(
                None if child is None else D.canonical_json(child)),
            source_object_count=int(value.get("source_object_count", -1)),
            source_row_count=int(value.get("source_row_count", -1)),
            content_digest=str(value.get("content_digest") or ""),
            objects=tuple(BoundObject(**dict(item))
                          for item in value.get("objects", ())),
            stage4_evidence_epochs=tuple(
                str(item) for item in value.get("stage4_evidence_epochs", ())),
        )


def _identity_material(
    *, start_date: str, end_date: str, source_authority: Mapping[str, Any],
    bindings: Sequence[DatasetBinding], excluded_by_design: Sequence[str],
) -> dict[str, Any]:
    dataset_material: list[dict[str, Any]] = []
    for binding in bindings:
        item = binding.to_dict()
        child = item.get("dataset_snapshot")
        if isinstance(child, dict):
            child.pop("created_at", None)
            child.pop("frozen_at", None)
            child.pop("population_descriptor_digest", None)
        dataset_material.append(item)
    return {
        "manifest_schema": MANIFEST_SCHEMA,
        "start_date": start_date,
        "end_date": end_date,
        "source_authority": dict(source_authority),
        "datasets": dataset_material,
        "excluded_by_design": list(excluded_by_design),
    }


@dataclass(frozen=True)
class InvestigationSnapshot:
    snapshot_id: str
    evidence_epoch: str
    snapshot_fingerprint: str
    created_at: str
    start_date: str
    end_date: str
    source_authority_json: str
    datasets: tuple[DatasetBinding, ...]
    excluded_by_design: tuple[str, ...]
    immutable: bool = True
    source_state: str = "FROZEN"
    status: str = "FROZEN"

    def __post_init__(self) -> None:
        if not self.immutable or self.source_state != "FROZEN" or self.status != "FROZEN":
            raise InvestigationSnapshotError("COMMON_SNAPSHOT_NOT_FROZEN")
        if self.start_date > self.end_date:
            raise InvestigationSnapshotError("INVERTED_SNAPSHOT_BOUNDS")
        names = [item.dataset for item in self.datasets]
        if names != list(BOUND_DATASETS):
            raise InvestigationSnapshotError("COMMON_SNAPSHOT_DATASET_SET_MISMATCH")
        if any(item.requirement != ("REQUIRED" if item.dataset in REQUIRED_DATASETS else "OPTIONAL")
               for item in self.datasets):
            raise InvestigationSnapshotError("COMMON_SNAPSHOT_DATASET_POLICY_MISMATCH")
        if self.excluded_by_design != EXCLUDED_BY_DESIGN:
            raise InvestigationSnapshotError("EXCLUDED_DATASET_POLICY_MISMATCH")
        for item in self.datasets:
            if item.presence == "ABSENT" and item.requirement == "REQUIRED":
                raise InvestigationSnapshotError(
                    "REQUIRED_DATASET_ABSENT:" + item.dataset)
            if item.schema_version != current_schema(item.dataset):
                raise InvestigationSnapshotError(
                    "SCHEMA_AUTHORITY_MISMATCH:" + item.dataset)
        try:
            source_authority = json.loads(self.source_authority_json)
        except (TypeError, ValueError) as exc:
            raise InvestigationSnapshotError("INVALID_SOURCE_AUTHORITY") from exc
        material = _identity_material(
            start_date=self.start_date, end_date=self.end_date,
            source_authority=source_authority, bindings=self.datasets,
            excluded_by_design=self.excluded_by_design,
        )
        digest = D.fingerprint(material)
        expected_id = "ISNAP-" + digest[:24].upper()
        expected_epoch = "INVESTIGATION-EPOCH-" + digest[:24].upper()
        if self.snapshot_fingerprint != digest or self.snapshot_id != expected_id:
            raise InvestigationSnapshotError("COMMON_SNAPSHOT_FINGERPRINT_MISMATCH")
        if self.evidence_epoch != expected_epoch:
            raise InvestigationSnapshotError("COMMON_EVIDENCE_EPOCH_MISMATCH")
        snapshots = [D.DatasetSnapshot.from_dict(item) for item in self.dataset_snapshots]
        D.DatasetSnapshotRegistry(snapshots)

    @property
    def source_authority(self) -> dict[str, Any]:
        return json.loads(self.source_authority_json)

    @property
    def dataset_fingerprints(self) -> dict[str, str]:
        return {item.dataset: item.content_digest for item in self.datasets}

    @property
    def dataset_coverage(self) -> tuple[dict[str, str], ...]:
        bound = tuple({
            "dataset": item.dataset,
            "requirement": item.requirement,
            "presence": item.presence,
        } for item in self.datasets)
        excluded = tuple({
            "dataset": name,
            "requirement": "EXCLUDED_BY_DESIGN",
            "presence": "EXCLUDED_BY_DESIGN",
        } for name in self.excluded_by_design)
        return bound + excluded

    @property
    def dataset_snapshots(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            json.loads(str(binding.dataset_snapshot_json))
            for binding in self.datasets
            if binding.dataset_snapshot_json is not None
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_schema": MANIFEST_SCHEMA,
            "snapshot_id": self.snapshot_id,
            "evidence_epoch": self.evidence_epoch,
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "created_at": self.created_at,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "source_authority": self.source_authority,
            "datasets": [item.to_dict() for item in self.datasets],
            "dataset_coverage": list(self.dataset_coverage),
            "excluded_by_design": list(self.excluded_by_design),
            "dataset_snapshots": list(self.dataset_snapshots),
            "immutable": self.immutable,
            "source_state": self.source_state,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "InvestigationSnapshot":
        if int(value.get("manifest_schema", -1)) != MANIFEST_SCHEMA:
            raise InvestigationSnapshotError("UNKNOWN_INVESTIGATION_MANIFEST_SCHEMA")
        snapshots = [D.DatasetSnapshot.from_dict(item)
                     for item in value.get("dataset_snapshots", ())]
        registry = D.DatasetSnapshotRegistry(snapshots)
        bindings = tuple(DatasetBinding.from_dict(item)
                         for item in value.get("datasets", ()))
        for binding in bindings:
            if binding.dataset_snapshot_id is not None:
                registry.require(binding.dataset_snapshot_id)
        authority_json = D.canonical_json(value.get("source_authority") or {})
        snapshot = cls(
            snapshot_id=str(value.get("snapshot_id") or ""),
            evidence_epoch=str(value.get("evidence_epoch") or ""),
            snapshot_fingerprint=str(value.get("snapshot_fingerprint") or ""),
            created_at=str(value.get("created_at") or ""),
            start_date=_validate_date(str(value.get("start_date") or ""), "start_date"),
            end_date=_validate_date(str(value.get("end_date") or ""), "end_date"),
            source_authority_json=authority_json,
            datasets=bindings,
            excluded_by_design=tuple(str(item) for item in
                                     value.get("excluded_by_design", ())),
            immutable=bool(value.get("immutable")),
            source_state=str(value.get("source_state") or ""),
            status=str(value.get("status") or ""),
        )
        if D.canonical_json(list(snapshot.dataset_snapshots)) != D.canonical_json(
                list(value.get("dataset_snapshots", ()))):
            raise InvestigationSnapshotError("STAGE4_SNAPSHOT_REGISTRY_MISMATCH")
        if D.canonical_json(list(snapshot.dataset_coverage)) != D.canonical_json(
                list(value.get("dataset_coverage", ()))):
            raise InvestigationSnapshotError("DATASET_COVERAGE_RECORD_MISMATCH")
        return snapshot


def _authority_material(source: S3ResearchDataSource) -> dict[str, Any]:
    schemas = {
        name: {
            "schema_version": current_schema(name),
            "schema_generation": PRODUCTION_SCHEMA_REGISTRY[name].generation,
            "s3_prefix": PRODUCTION_SCHEMA_REGISTRY[name].s3_base_prefix,
            "role": PRODUCTION_SCHEMA_REGISTRY[name].role.value,
            "partition_model": PRODUCTION_SCHEMA_REGISTRY[name].partition_model.value,
        }
        for name in BOUND_DATASETS
    }
    versioning_policy = json.loads(
        D.VERSIONING_POLICY_PATH.read_text(encoding="utf-8"))
    epoch_map: dict[str, tuple[str, ...]] = {name: () for name in BOUND_DATASETS}
    for epoch in (versioning_policy.get("version_registry") or {}).get(
            "evidence_epochs", ()):
        dataset = str(epoch.get("dataset") or "")
        if dataset in epoch_map:
            epoch_map[dataset] = tuple(sorted((*epoch_map[dataset], str(epoch.get("epoch_id")))))
    return {
        "data_contract": DATA_CONTRACT_VERSION,
        "canonical_authority": "core.production_data_contract.PRODUCTION_SCHEMA_REGISTRY",
        "schemas": schemas,
        "stage4_snapshot_policy_id": D.SNAPSHOT_POLICY_ID,
        "stage4_versioning_policy_id": versioning_policy.get("policy_id"),
        "stage4_versioning_policy_fingerprint": D.fingerprint(versioning_policy),
        "stage4_evidence_epochs_by_dataset": {
            name: list(epoch_map[name]) for name in BOUND_DATASETS
        },
        "source": "research_engine.data_access.s3_source.S3ResearchDataSource",
        "bucket": source.bucket,
        "region": str(getattr(source, "_region", "")),
        "research_profile": source.research_profile,
    }


def _object_digest(objects: Sequence[BoundObject]) -> str:
    return D.fingerprint([item.to_dict() for item in objects])


def _child_snapshot(
    dataset: str, requirement: str, start_date: str, end_date: str,
    rows: Sequence[Mapping[str, Any]], objects: Sequence[BoundObject],
    source_authority: Mapping[str, Any],
) -> DatasetBinding:
    presence = "PRESENT" if objects else "ABSENT"
    object_digest = _object_digest(objects)
    content_digest = D.fingerprint({
        "dataset": dataset,
        "schema_version": current_schema(dataset),
        "objects": [item.to_dict() for item in objects],
    })
    snapshot = None
    if rows:
        key_digest = D.fingerprint([item.identifier for item in objects])
        snapshot = D.freeze_population(
            dataset_name=dataset,
            schema_version=current_schema(dataset),
            schema_generation=None,
            generation_state=D.GENERATION_UNASSERTED,
            generation_evidence=(
                "Schema version is asserted by production_v1; per-population "
                "generation homogeneity is not independently attested."),
            identity_grain="one canonical Production V1 record per source row",
            identity_grain_evidence=(
                "Production V1 dataset identity and exact bound S3 objects; "
                "record-level identities remain governed by each dataset contract."),
            source_boundaries=(
                "s3_bucket=" + str(source_authority["bucket"]),
                "source_object_count=" + str(len(objects)),
                "source_keys_sha256=" + key_digest,
                "object_manifest_sha256=" + object_digest,
            ),
            population_filters=(
                "schema_version=" + current_schema(dataset),
                "start_date_inclusive=" + start_date,
                "end_date_inclusive=" + end_date,
                "source_object_population=EXACT_BOUND_KEYS",
            ),
            temporal_bounds=(start_date, end_date),
            population_class=D.AUDIT_BOUNDARY_POPULATION,
            records=rows,
            producer_version=None,
            producer_fingerprint=None,
            frozen_at=datetime.now(timezone.utc).isoformat(),
            created_at=datetime.now(timezone.utc).isoformat(),
            evidence_citations=(
                "canonical production_v1 S3 object manifest",
                "raw-object SHA-256 verified on two exact-key reads",
            ),
            audit_authority_fingerprint=str(
                source_authority["stage4_versioning_policy_fingerprint"]),
        )
    binding = DatasetBinding(
        dataset=dataset,
        requirement=requirement,
        presence=presence,
        schema_version=current_schema(dataset),
        schema_generation=None,
        dataset_snapshot_id=(snapshot.dataset_snapshot_id if snapshot else None),
        dataset_snapshot_json=(
            None if snapshot is None else D.canonical_json(snapshot.to_dict())),
        source_object_count=len(objects),
        source_row_count=len(rows),
        content_digest=content_digest,
        objects=tuple(objects),
        stage4_evidence_epochs=tuple(source_authority[
            "stage4_evidence_epochs_by_dataset"][dataset]),
    )
    return binding


def _save_manifest(snapshot: InvestigationSnapshot, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = InvestigationSnapshot.from_dict(
            json.loads(path.read_text(encoding="utf-8")))
        if existing.snapshot_fingerprint != snapshot.snapshot_fingerprint:
            raise InvestigationSnapshotError("SNAPSHOT_ID_FILE_CONFLICT")
        return
    temporary = path.with_name(path.name + ".tmp")
    with open(temporary, "x", encoding="utf-8", newline="\n") as handle:
        json.dump(snapshot.to_dict(), handle, indent=2, sort_keys=True)
        handle.write("\n")
    try:
        os.replace(temporary, path)
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        finally:
            raise


def save_investigation_snapshot(
    snapshot: InvestigationSnapshot, path: Path,
) -> None:
    """Persist a finalized manifest atomically without allowing ID reuse."""
    _save_manifest(snapshot, Path(path))


def freeze_investigation_snapshot(
    *, start_date: str, end_date: str,
    source: S3ResearchDataSource | None = None,
    manifest_path: Path | None = None,
) -> InvestigationSnapshot:
    """Capture and verify all six views' exact canonical source objects.

    Listing, exact-key reads, a second listing, a second exact-key read and a
    final listing must all agree. No manifest is finalized before those checks
    pass. S3 provides no multi-object transaction; this procedure detects
    observed changes and fails closed rather than claiming an atomic S3 write.
    """
    start = _validate_date(start_date, "start_date")
    end = _validate_date(end_date, "end_date")
    if start > end:
        raise InvestigationSnapshotError("INVERTED_SNAPSHOT_BOUNDS")
    resolved = source or get_default_source()
    authority = _authority_material(resolved)
    schema_by_dataset = {name: current_schema(name) for name in BOUND_DATASETS}

    before = {
        name: resolved.discover_dataset_objects(
            name, start_date=start, end_date=end)
        for name in BOUND_DATASETS
    }
    for name in REQUIRED_DATASETS:
        if not before[name]:
            raise InvestigationSnapshotError("REQUIRED_DATASET_ABSENT:" + name)

    first_objects: dict[str, tuple[BoundObject, ...]] = {}
    first_row_counts: dict[str, int] = {}
    bindings: list[DatasetBinding] = []
    for name in BOUND_DATASETS:
        rows = resolved.read_objects_for_freeze(
            name, before[name], expected_schema_version=schema_by_dataset[name],
            start_date=start, end_date=end)
        malformed = resolved.malformed_report(name)
        if malformed and malformed.malformed_lines:
            raise InvestigationSnapshotError(
                f"MALFORMED_BOUND_OBJECT_ROWS:{name}:{malformed.malformed_lines}")
        first_objects[name] = tuple(
            BoundObject.from_metadata(item)
            for item in resolved.object_metadata(name))
        if name in REQUIRED_DATASETS and not rows:
            raise InvestigationSnapshotError("REQUIRED_DATASET_EMPTY:" + name)
        first_row_counts[name] = len(rows)
        requirement = "REQUIRED" if name in REQUIRED_DATASETS else "OPTIONAL"
        bindings.append(_child_snapshot(
            name, requirement, start, end, rows, first_objects[name], authority))
        del rows

    between = {
        name: resolved.discover_dataset_objects(
            name, start_date=start, end_date=end)
        for name in BOUND_DATASETS
    }
    if any(_listing_material(before[name]) != _listing_material(between[name])
           for name in BOUND_DATASETS):
        raise InvestigationSnapshotError("OBJECT_POPULATION_CHANGED_DURING_FREEZE")

    for name in BOUND_DATASETS:
        second_rows = resolved.read_bound_objects(
            name, [item.to_dict() for item in first_objects[name]],
            expected_schema_version=schema_by_dataset[name],
            start_date=start, end_date=end)
        second_objects = tuple(
            BoundObject.from_metadata(item)
            for item in resolved.object_metadata(name))
        if second_objects != first_objects[name] or len(second_rows) != first_row_counts[name]:
            raise InvestigationSnapshotError(
                "OBJECT_CONTENT_CHANGED_DURING_FREEZE:" + name)
        del second_rows

    after = {
        name: resolved.discover_dataset_objects(
            name, start_date=start, end_date=end)
        for name in BOUND_DATASETS
    }
    if any(_listing_material(before[name]) != _listing_material(after[name])
           for name in BOUND_DATASETS):
        raise InvestigationSnapshotError("OBJECT_POPULATION_CHANGED_DURING_FREEZE")

    material = _identity_material(
        start_date=start, end_date=end, source_authority=authority,
        bindings=bindings, excluded_by_design=EXCLUDED_BY_DESIGN)
    digest = D.fingerprint(material)
    snapshot_id = "ISNAP-" + digest[:24].upper()
    epoch_id = "INVESTIGATION-EPOCH-" + digest[:24].upper()
    snapshot = InvestigationSnapshot(
        snapshot_id=snapshot_id,
        evidence_epoch=epoch_id,
        snapshot_fingerprint=digest,
        created_at=datetime.now(timezone.utc).isoformat(),
        start_date=start,
        end_date=end,
        source_authority_json=D.canonical_json(authority),
        datasets=tuple(bindings),
        excluded_by_design=EXCLUDED_BY_DESIGN,
    )
    snapshot = InvestigationSnapshot.from_dict(snapshot.to_dict())
    if manifest_path is not None:
        _save_manifest(snapshot, Path(manifest_path))
    return snapshot


def load_investigation_snapshot(path: Path) -> InvestigationSnapshot:
    """Load an immutable manifest and rederive all identity/digest invariants."""
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise InvestigationSnapshotError("INVESTIGATION_MANIFEST_UNREADABLE") from exc
    snapshot = InvestigationSnapshot.from_dict(value)
    return snapshot


def load_investigation_snapshot_id(
    snapshot_id: str, *, manifest_directory: Path = MANIFEST_DIRECTORY,
) -> InvestigationSnapshot:
    """Resolve a persisted ISNAP identity without scanning for newer sources."""
    resolved_id = str(snapshot_id or "")
    if not resolved_id.startswith("ISNAP-"):
        raise InvestigationSnapshotError("INVALID_INVESTIGATION_SNAPSHOT_ID")
    snapshot = load_investigation_snapshot(
        Path(manifest_directory) / f"{resolved_id}.json")
    if snapshot.snapshot_id != resolved_id:
        raise InvestigationSnapshotError("INVESTIGATION_SNAPSHOT_ID_MISMATCH")
    return snapshot


class SnapshotBoundDatasetReader:
    """DatasetReader that can only fetch the objects recorded in one manifest."""

    def __init__(
        self, snapshot: InvestigationSnapshot,
        source: S3ResearchDataSource | None = None,
    ) -> None:
        self.snapshot = snapshot
        self._source = source or get_default_source()
        self._bindings = {item.dataset: item for item in snapshot.datasets}
        for name, binding in self._bindings.items():
            if current_schema(name) != binding.schema_version:
                raise InvestigationSnapshotError("SCHEMA_AUTHORITY_CHANGED:" + name)
        current_authority = _authority_material(self._source)
        if D.fingerprint(current_authority) != D.fingerprint(snapshot.source_authority):
            raise InvestigationSnapshotError("SOURCE_AUTHORITY_CHANGED")
        self._cache: dict[str, list[dict[str, Any]]] = {}
        for name, binding in self._bindings.items():
            if binding.presence == "ABSENT":
                self._cache[name] = []
                continue
            rows = self._source.read_bound_objects(
                name, [item.to_dict() for item in binding.objects],
                expected_schema_version=binding.schema_version,
                start_date=snapshot.start_date, end_date=snapshot.end_date)
            malformed = self._source.malformed_report(name)
            if malformed and malformed.malformed_lines:
                raise InvestigationSnapshotError(
                    "MALFORMED_BOUND_OBJECT_ROWS:" + name)
            if len(rows) != binding.source_row_count:
                raise InvestigationSnapshotError("SNAPSHOT_DATASET_ROW_COUNT_CHANGED:" + name)
            actual_metadata = tuple(
                BoundObject.from_metadata(item)
                for item in self._source.object_metadata(name))
            if actual_metadata != binding.objects:
                raise InvestigationSnapshotError("SNAPSHOT_DATASET_OBJECTS_CHANGED:" + name)
            if D.fingerprint({
                    "dataset": name,
                    "schema_version": binding.schema_version,
                    "objects": [item.to_dict() for item in actual_metadata],
            }) != binding.content_digest:
                raise InvestigationSnapshotError("SNAPSHOT_DATASET_DIGEST_CHANGED:" + name)
            self._cache[name] = rows

    @property
    def snapshot_provenance(self) -> dict[str, Any]:
        return self.snapshot.to_dict()

    @property
    def reads_by_dataset(self) -> dict[str, int]:
        return {name: len(rows) for name, rows in self._cache.items()}

    def cycle_cached_dataset(self, dataset: str) -> list[dict[str, Any]]:
        """Borrow the verified cycle-scoped population without copying records.

        This is intentionally narrower than ``read_dataset`` and is used only
        while constructing one immutable-snapshot question context.  External
        consumers continue to receive defensive copies from ``read_dataset``.
        """
        if dataset not in self._bindings:
            raise InvestigationSnapshotError("DATASET_NOT_BOUND:" + str(dataset))
        return self._cache[dataset]

    def read_dataset(self, dataset: str, **kwargs: Any) -> list[dict[str, Any]]:
        if dataset not in self._bindings:
            raise InvestigationSnapshotError("DATASET_NOT_BOUND:" + str(dataset))
        for name in ("start_date", "end_date"):
            requested = kwargs.get(name)
            frozen = getattr(self.snapshot, name)
            if requested is not None and str(requested) != frozen:
                raise InvestigationSnapshotError("SNAPSHOT_DATE_SCOPE_MISMATCH:" + name)
        if kwargs.get("all_schemas"):
            raise InvestigationSnapshotError("SNAPSHOT_LEGACY_SCHEMA_READ_FORBIDDEN")
        return [dict(row) for row in self._cache[dataset]]


def open_investigation_snapshot(
    snapshot_id: str, *, source: S3ResearchDataSource | None = None,
    manifest_directory: Path = MANIFEST_DIRECTORY,
) -> SnapshotBoundDatasetReader:
    """Open a snapshot by ID, reading only its verified object keys."""
    snapshot = load_investigation_snapshot_id(
        snapshot_id, manifest_directory=manifest_directory)
    return SnapshotBoundDatasetReader(snapshot, source=source)
