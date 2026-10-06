"""Immutable, run-scoped CURRENT snapshots for the HD13/HD14 audits."""
from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from research_engine.control_plane.evidence_resolver import (
    authoritative_evidence_schema,
    classify_authoritative_evidence_record,
)
from research_engine.data_quality.classifier import DataEpoch

SNAPSHOT_VERSION = "data_governance_current_snapshot_v1"


def _canonical(value: Any) -> str:
    return json.dumps(_thaw(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(item) for item in value]
    return value


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CurrentSnapshot:
    """A deep-frozen snapshot; records can only be obtained as fresh copies."""

    snapshot_id: str
    as_of_utc: str
    registry_digest: str
    definition_digest: str
    contract_digests: Mapping[str, str]
    components: tuple[Mapping[str, Any], ...]
    _payloads: tuple[tuple[str, tuple[str, ...]], ...]
    _input_payloads: tuple[tuple[str, tuple[str, ...]], ...]
    _borrowed_datasets: Mapping[str, Iterable[Mapping[str, Any]]] | None = field(
        default=None, compare=False, repr=False,
    )

    def records(self, source: str) -> list[dict[str, Any]]:
        payload = dict(self._payloads).get(source, ())
        if payload or self._borrowed_datasets is None:
            return [json.loads(item) for item in payload]
        schema = authoritative_evidence_schema(source)
        return [
            deepcopy(dict(record))
            for record in self._borrowed_datasets.get(source, ())
            if classify_authoritative_evidence_record(
                record, source, schema=schema,
            ) == DataEpoch.CURRENT
        ]

    def input_records(
        self, source: str, *, trusted_immutable: bool = False,
    ) -> Iterable[Mapping[str, Any]]:
        """Return copies of all frozen input rows for fail-closed orphan audits."""
        payload = dict(self._input_payloads).get(source, ())
        if payload or self._borrowed_datasets is None:
            return [json.loads(item) for item in payload]
        if trusted_immutable:
            return self._borrowed_datasets.get(source, ())
        return [
            deepcopy(dict(record))
            for record in self._borrowed_datasets.get(source, ())
        ]

    def component(self, source: str) -> Mapping[str, Any] | None:
        return next((item for item in self.components if item["source"] == source), None)

    @property
    def sources(self) -> tuple[str, ...]:
        if self._payloads:
            return tuple(source for source, _ in self._payloads)
        return tuple(str(component["source"]) for component in self.components)

    def manifest(self) -> dict[str, Any]:
        return {
            "version": SNAPSHOT_VERSION,
            "snapshot_id": self.snapshot_id,
            "epoch": "CURRENT",
            "as_of_utc": self.as_of_utc,
            "registry_digest": self.registry_digest,
            "definition_digest": self.definition_digest,
            "contract_digests": _thaw(self.contract_digests),
            "components": [_thaw(item) for item in self.components],
            "input_records": sum(int(item["input_records"]) for item in self.components),
            "records_used": sum(int(item["records_used"]) for item in self.components),
            "records_excluded": sum(int(item["records_excluded"]) for item in self.components),
        }


def freeze_current_snapshot(
    datasets: Mapping[str, Iterable[Mapping[str, Any]]],
    *,
    registry_material: Any,
    definition_material: Any,
    contract_material: Mapping[str, Any],
    as_of_utc: str | None = None,
    retain_record_payloads: bool = True,
) -> CurrentSnapshot:
    """Freeze supplied persisted evidence once, accounting for every input row."""
    as_of = as_of_utc or datetime.now(timezone.utc).isoformat()
    components: list[Mapping[str, Any]] = []
    payloads: list[tuple[str, tuple[str, ...]]] = []
    input_payloads: list[tuple[str, tuple[str, ...]]] = []
    for source in sorted(datasets):
        schema = authoritative_evidence_schema(source)
        if schema is None:
            raise ValueError(f"No persisted canonical authority exists for source {source!r}")
        supplied = [
            dict(record) if retain_record_payloads or not isinstance(record, dict)
            else record
            for record in datasets[source]
        ]
        current: list[dict[str, Any]] = []
        counts = {"CURRENT": 0, "TRANSITIONAL": 0, "LEGACY": 0, "INCOMPATIBLE": 0}
        for record in supplied:
            epoch = classify_authoritative_evidence_record(record, source, schema=schema)
            label = epoch.value if isinstance(epoch, DataEpoch) else "INCOMPATIBLE"
            counts[label] += 1
            if label == "CURRENT":
                current.append(record)
        # A metadata-only canonical-cycle snapshot must not retain a second
        # serialized copy of every record merely to obtain an order-insensitive
        # digest.  Hash each canonical row, sort the fixed-width hashes, and
        # retain full payloads only for callers that explicitly request them.
        current_hashes = sorted(
            hashlib.sha256(_canonical(record).encode("utf-8")).hexdigest()
            for record in current
        )
        if retain_record_payloads:
            encoded = tuple(sorted(_canonical(record) for record in current))
            input_encoded = tuple(sorted(_canonical(record) for record in supplied))
        else:
            encoded = ()
            input_encoded = ()
        component = {
            "source": source,
            "schema": schema,
            "state": "CURRENT",
            "selection": "CURRENT_ONLY",
            "input_records": len(supplied),
            "records_used": len(current),
            "records_excluded": len(supplied) - len(current),
            "included_counts": {"CURRENT": len(current)},
            "excluded_counts": {key: value for key, value in counts.items() if key != "CURRENT"},
            "digest_algorithm": "sha256",
            "digest": hashlib.sha256(
                "\n".join(current_hashes).encode("utf-8")).hexdigest(),
        }
        components.append(_freeze(component))
        if retain_record_payloads:
            payloads.append((source, encoded))
            input_payloads.append((source, input_encoded))
    registry_digest = _digest(registry_material)
    definition_digest = _digest(definition_material)
    contract_digests = {key: _digest(value) for key, value in sorted(contract_material.items())}
    identity = {
        "version": SNAPSHOT_VERSION,
        "as_of_utc": as_of,
        "registry_digest": registry_digest,
        "definition_digest": definition_digest,
        "contract_digests": contract_digests,
        "components": [_thaw(item) for item in components],
    }
    return CurrentSnapshot(
        snapshot_id=_digest(identity), as_of_utc=as_of,
        registry_digest=registry_digest, definition_digest=definition_digest,
        contract_digests=MappingProxyType(contract_digests),
        components=tuple(components), _payloads=tuple(payloads),
        _input_payloads=tuple(input_payloads),
        _borrowed_datasets=(None if retain_record_payloads else datasets),
    )


def validate_snapshot_manifest(value: Any) -> tuple[bool, str]:
    if not isinstance(value, dict) or value.get("version") != SNAPSHOT_VERSION:
        return False, "Missing or unsupported data-governance snapshot manifest"
    if value.get("epoch") != "CURRENT" or not value.get("snapshot_id"):
        return False, "Snapshot is not identified as CURRENT"
    components = value.get("components")
    if not isinstance(components, list) or not components:
        return False, "Snapshot has no persisted evidence components"
    sources: set[str] = set()
    for component in components:
        if not isinstance(component, dict):
            return False, "Snapshot component is malformed"
        source, schema = str(component.get("source", "")), str(component.get("schema", ""))
        if not source or source in sources or authoritative_evidence_schema(source) != schema:
            return False, "Snapshot source/schema identity is missing, duplicated, or incompatible"
        sources.add(source)
        if component.get("state") != "CURRENT" or component.get("digest_algorithm") != "sha256":
            return False, "Snapshot component is not CURRENT SHA-256 evidence"
        if len(str(component.get("digest", ""))) != 64:
            return False, "Snapshot component digest is invalid"
        numbers = [component.get(key) for key in ("input_records", "records_used", "records_excluded")]
        if any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in numbers):
            return False, "Snapshot component accounting is invalid"
        if numbers[1] + numbers[2] != numbers[0]:
            return False, "Snapshot component accounting is inconsistent"
    for key in ("input_records", "records_used", "records_excluded"):
        if value.get(key) != sum(item[key] for item in components):
            return False, "Snapshot aggregate accounting is inconsistent"
    identity = {
        "version": SNAPSHOT_VERSION,
        "as_of_utc": value.get("as_of_utc"),
        "registry_digest": value.get("registry_digest"),
        "definition_digest": value.get("definition_digest"),
        "contract_digests": value.get("contract_digests"),
        "components": components,
    }
    if value.get("snapshot_id") != _digest(identity):
        return False, "Snapshot identity does not match its immutable manifest"
    return True, "Immutable CURRENT snapshot manifest is valid"
