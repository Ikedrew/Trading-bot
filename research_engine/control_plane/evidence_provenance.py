"""Fail-closed, deterministic provenance for evidence actually analysed.

The selection functions return both the exact selected records and the
component metadata derived from those records.  Runners must analyse
``EvidenceSelection.records`` and build report provenance from that same
selection; callers cannot ask this module to stamp an arbitrary epoch.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
import hashlib
import json
from typing import Any, Iterable, Mapping
from collections import Counter

from research_engine.control_plane.evidence_resolver import (
    authoritative_evidence_schema,
    classify_authoritative_evidence_record,
)
from research_engine.data_quality.classifier import DataEpoch

PROVENANCE_VERSION = "evidence_provenance_v1"
CURRENT = "CURRENT"
STALE = "STALE"
MIXED = "MIXED"
UNVERIFIED = "UNVERIFIED"
INCOMPATIBLE = "INCOMPATIBLE"
_COUNT_KEYS = (CURRENT, "TRANSITIONAL", "LEGACY", INCOMPATIBLE)


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return _json_value(value.value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        items = [_json_value(item) for item in value]
        return sorted(items, key=_canonical_json)
    raise TypeError(f"Unsupported evidence value for deterministic digest: {type(value).__name__}")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _json_value(value), sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    )


def evidence_digest(records: Iterable[Mapping[str, Any]]) -> str:
    """Return an order-independent SHA-256 digest preserving duplicates."""
    encoded = sorted(_canonical_json(record) for record in records)
    payload = "\n".join(encoded).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _empty_counts() -> dict[str, int]:
    return {key: 0 for key in _COUNT_KEYS}


def _state_from_used_counts(counts: Mapping[str, int]) -> str:
    total = sum(int(counts.get(key, 0)) for key in _COUNT_KEYS)
    if total == 0:
        return UNVERIFIED
    if int(counts.get(INCOMPATIBLE, 0)):
        return INCOMPATIBLE
    current = int(counts.get(CURRENT, 0))
    stale = int(counts.get("TRANSITIONAL", 0)) + int(counts.get("LEGACY", 0))
    if current and stale:
        return MIXED
    if stale:
        return STALE
    return CURRENT


def _subset_state(
    input_counts: Mapping[str, int], used_counts: Mapping[str, int],
) -> str:
    """Classify a CURRENT subset, including a proven empty analytical subset."""
    state = _state_from_used_counts(used_counts)
    if state == UNVERIFIED and int(input_counts.get(CURRENT, 0)) > 0:
        # An empty subset selected from a non-empty authoritative CURRENT
        # population is still a valid CURRENT scientific result (for example,
        # no sufficient inferential grid).  It must not be relabelled stale or
        # fabricated merely because the exact analysed population is empty.
        return CURRENT
    return state


def _combined_digest(components: list[dict[str, Any]]) -> str:
    material = [
        {
            "source": component["source"],
            "schema": component["schema"],
            "selection": component["selection"],
            "input_records": component["input_records"],
            "records_used": component["records_used"],
            "records_excluded": component["records_excluded"],
            "epoch_counts": component["epoch_counts"],
            "used_epoch_counts": component["used_epoch_counts"],
            "state": component["state"],
            "digest": component["digest"],
        }
        for component in sorted(
            components, key=lambda item: (item["source"], item["schema"])
        )
    ]
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EvidenceSelection:
    """Exact records selected for analysis plus their derived provenance."""

    records: tuple[dict[str, Any], ...]
    component: dict[str, Any]

    def records_for_analysis(self) -> list[dict[str, Any]]:
        """Return an isolated copy of the exact population represented here."""
        return deepcopy(list(self.records))


def _select(
    source: str,
    records: Iterable[Mapping[str, Any]],
    *,
    schema: str | None,
    current_only: bool,
) -> EvidenceSelection:
    supplied = [deepcopy(dict(record)) for record in records]
    expected_schema = authoritative_evidence_schema(source)
    declared_schema = str(schema or expected_schema or "")
    classified: list[tuple[dict[str, Any], str]] = []
    counts = _empty_counts()
    for record in supplied:
        epoch = classify_authoritative_evidence_record(
            record, source, schema=declared_schema or None,
        )
        label = epoch.value if isinstance(epoch, DataEpoch) else INCOMPATIBLE
        counts[label] += 1
        classified.append((record, label))

    if current_only:
        used_pairs = [pair for pair in classified if pair[1] == CURRENT]
        selection_mode = "CURRENT_ONLY"
    else:
        used_pairs = classified
        selection_mode = "AS_SUPPLIED"

    selected = tuple(record for record, _ in used_pairs)
    used_counts = _empty_counts()
    for _, label in used_pairs:
        used_counts[label] += 1
    state = _state_from_used_counts(used_counts)
    component = {
        "source": str(source),
        "schema": declared_schema,
        "selection": selection_mode,
        "input_records": len(supplied),
        "records_used": len(selected),
        "records_excluded": len(supplied) - len(selected),
        "epoch_counts": counts,
        "used_epoch_counts": used_counts,
        "state": state,
        "digest_algorithm": "sha256",
        "digest": evidence_digest(selected),
    }
    return EvidenceSelection(records=selected, component=component)


def select_current_evidence(
    source: str,
    records: Iterable[Mapping[str, Any]],
    *,
    schema: str | None = None,
) -> EvidenceSelection:
    """Select only proven CURRENT records and account for every exclusion."""
    return _select(source, records, schema=schema, current_only=True)


def use_evidence_as_supplied(
    source: str,
    records: Iterable[Mapping[str, Any]],
    *,
    schema: str | None = None,
) -> EvidenceSelection:
    """Represent every supplied record as used; mixed/stale input fails closed."""
    return _select(source, records, schema=schema, current_only=False)


def attest_current_subset(
    source: str,
    input_records: Iterable[Mapping[str, Any]],
    selected_records: Iterable[Mapping[str, Any]],
    *,
    schema: str | None = None,
) -> EvidenceSelection:
    """Attest an exact analytical subset of an authoritative CURRENT input.

    ``selected_records`` must be a duplicate-preserving subset of the CURRENT
    records in ``input_records``.  This permits scientific filters and
    conflict rejection to happen after epoch classification while preventing
    callers from attaching CURRENT provenance to fabricated or stale rows.
    """
    supplied = [deepcopy(dict(record)) for record in input_records]
    selected = [deepcopy(dict(record)) for record in selected_records]
    expected_schema = authoritative_evidence_schema(source)
    declared_schema = str(schema or expected_schema or "")
    counts = _empty_counts()
    current_material: Counter[str] = Counter()
    for record in supplied:
        epoch = classify_authoritative_evidence_record(
            record, source, schema=declared_schema or None,
        )
        label = epoch.value if isinstance(epoch, DataEpoch) else INCOMPATIBLE
        counts[label] += 1
        if label == CURRENT:
            current_material[_canonical_json(record)] += 1

    selected_material = Counter(_canonical_json(record) for record in selected)
    if selected_material - current_material:
        raise ValueError(
            "Selected evidence must be a duplicate-preserving subset of the "
            "authoritative CURRENT input population"
        )

    used_counts = _empty_counts()
    used_counts[CURRENT] = len(selected)
    component = {
        "source": str(source),
        "schema": declared_schema,
        "selection": "CURRENT_SUBSET",
        "input_records": len(supplied),
        "records_used": len(selected),
        "records_excluded": len(supplied) - len(selected),
        "epoch_counts": counts,
        "used_epoch_counts": used_counts,
        "state": _subset_state(counts, used_counts),
        "digest_algorithm": "sha256",
        "digest": evidence_digest(selected),
    }
    return EvidenceSelection(records=tuple(selected), component=component)


def build_evidence_provenance(
    *selections: EvidenceSelection,
) -> dict[str, Any]:
    """Compose one or more independently identified evidence components."""
    components = [deepcopy(selection.component) for selection in selections]
    identities = [(item["source"], item["schema"]) for item in components]
    if len(identities) != len(set(identities)):
        raise ValueError("Evidence components must have unique source/schema identities")
    states = [component["state"] for component in components]
    state = CURRENT if components and all(item == CURRENT for item in states) else (
        INCOMPATIBLE if INCOMPATIBLE in states else
        MIXED if MIXED in states or (CURRENT in states and STALE in states) else
        STALE if STALE in states else UNVERIFIED
    )
    result = {
        "version": PROVENANCE_VERSION,
        "state": state,
        "epoch": CURRENT if state == CURRENT else state,
        "components": sorted(
            components, key=lambda item: (item["source"], item["schema"])
        ),
        "input_records": sum(item["input_records"] for item in components),
        "records_used": sum(item["records_used"] for item in components),
        "records_excluded": sum(item["records_excluded"] for item in components),
        "digest_algorithm": "sha256",
    }
    result["digest"] = _combined_digest(result["components"])
    return result


def validate_evidence_provenance(value: Any) -> tuple[bool, str, str]:
    """Validate structured provenance without trusting its top-level state."""
    if not isinstance(value, dict) or value.get("version") != PROVENANCE_VERSION:
        return False, UNVERIFIED, "Missing or unsupported structured evidence provenance"
    components = value.get("components")
    if not isinstance(components, list) or not components:
        return False, UNVERIFIED, "Structured provenance has no evidence components"
    identities: set[tuple[str, str]] = set()
    for component in components:
        if not isinstance(component, dict):
            return False, UNVERIFIED, "Evidence component is not an object"
        identity = (str(component.get("source", "")), str(component.get("schema", "")))
        if not all(identity) or identity in identities:
            return False, UNVERIFIED, "Evidence component identity is missing or duplicated"
        identities.add(identity)
        if authoritative_evidence_schema(identity[0]) != identity[1]:
            return False, INCOMPATIBLE, f"Incompatible source/schema identity {identity!r}"
        counts = component.get("epoch_counts")
        used_counts = component.get("used_epoch_counts")
        if not isinstance(counts, dict) or not isinstance(used_counts, dict):
            return False, UNVERIFIED, "Evidence epoch counts are missing"
        if any(
            isinstance(item.get(key), bool) or not isinstance(item.get(key), int)
            or item.get(key) < 0
            for item in (counts, used_counts) for key in _COUNT_KEYS
        ):
            return False, UNVERIFIED, "Evidence epoch counts are invalid"
        input_count = component.get("input_records")
        used_count = component.get("records_used")
        excluded_count = component.get("records_excluded")
        selection = component.get("selection")
        if (
            any(isinstance(number, bool) or not isinstance(number, int) or number < 0
                for number in (input_count, used_count, excluded_count))
            or sum(counts[key] for key in _COUNT_KEYS) != input_count
            or sum(used_counts[key] for key in _COUNT_KEYS) != used_count
            or used_count + excluded_count != input_count
        ):
            return False, UNVERIFIED, "Evidence component counts are inconsistent"
        if selection == "AS_SUPPLIED":
            if excluded_count != 0 or used_counts != counts:
                return False, UNVERIFIED, "AS_SUPPLIED evidence cannot exclude records"
        elif selection == "CURRENT_ONLY":
            if (
                used_counts[CURRENT] != counts[CURRENT]
                or any(used_counts[key] for key in _COUNT_KEYS if key != CURRENT)
                or excluded_count != input_count - counts[CURRENT]
            ):
                return False, UNVERIFIED, "CURRENT_ONLY selection counts are inconsistent"
        elif selection == "CURRENT_SUBSET":
            if (
                used_counts[CURRENT] > counts[CURRENT]
                or any(used_counts[key] for key in _COUNT_KEYS if key != CURRENT)
                or excluded_count != input_count - used_count
            ):
                return False, UNVERIFIED, "CURRENT_SUBSET selection counts are inconsistent"
        else:
            return False, UNVERIFIED, "Unknown evidence selection mode"
        derived_state = (
            _subset_state(counts, used_counts)
            if selection == "CURRENT_SUBSET"
            else _state_from_used_counts(used_counts)
        )
        if component.get("state") != derived_state:
            return False, UNVERIFIED, "Evidence component state contradicts its used population"
        digest = component.get("digest")
        if component.get("digest_algorithm") != "sha256" or not _valid_digest(digest):
            return False, UNVERIFIED, "Evidence component digest is invalid"

    expected_state = CURRENT if all(item["state"] == CURRENT for item in components) else (
        INCOMPATIBLE if any(item["state"] == INCOMPATIBLE for item in components) else
        MIXED if any(item["state"] == MIXED for item in components)
        or ({item["state"] for item in components} >= {CURRENT, STALE}) else
        STALE if any(item["state"] == STALE for item in components) else UNVERIFIED
    )
    if value.get("state") != expected_state or value.get("epoch") != expected_state:
        return False, UNVERIFIED, "Combined provenance state is inconsistent"
    if any(
        value.get(key) != sum(item[key] for item in components)
        for key in ("input_records", "records_used", "records_excluded")
    ):
        return False, UNVERIFIED, "Combined provenance counts are inconsistent"
    if value.get("digest_algorithm") != "sha256" or value.get("digest") != _combined_digest(components):
        return False, UNVERIFIED, "Combined provenance digest is inconsistent"
    return True, expected_state, f"Structured evidence provenance is {expected_state}"


def _valid_digest(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True
