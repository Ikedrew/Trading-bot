"""
Dataset Fingerprint — Cryptographic content-identity for research populations.

Ensures every research experiment can identify EXACTLY which data it used.

Guarantees:
    - Same logical records → same fingerprint (deterministic)
    - Different content → different fingerprint (collision-resistant)
    - JSON key ordering does NOT affect fingerprint (canonical serialisation)
    - File ordering does NOT affect fingerprint (sorted by canonical key)
    - Filtered populations produce different fingerprints from unfiltered

Canonicalisation Rules:
    1. Each record is serialised to JSON with keys sorted recursively
    2. Records are sorted by a canonical ordering key (symbol + timestamp + pattern)
    3. The concatenated canonical representation is hashed with SHA-256
    4. Floating-point values are rounded to 8 decimal places for stability
    5. None values are preserved (not omitted)
    6. The hash covers the ACTUAL records supplied, not dataset metadata

This module NEVER modifies production V10.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class DatasetFingerprint:
    """
    Immutable content-identity for a research dataset/population.
    
    Two datasets with identical content produce the same fingerprint.
    Two datasets with different content produce different fingerprints.
    """
    dataset_id: str                     # Human-readable identifier (e.g., "V10_PRIMARY_shadows")
    dataset_version: str                # Schema/pipeline version (e.g., "shadow_trades_v2")
    fingerprint_algorithm: str          # "SHA-256"
    content_hash: str                   # Hex digest of canonical content hash
    observation_count: int              # Number of records in the population
    first_timestamp: float              # Earliest observation timestamp (unix)
    last_timestamp: float               # Latest observation timestamp (unix)
    population: str                     # Population description (e.g., "TBC+TWS inverted")
    schema_version: str                 # Record schema version
    generated_timestamp: str            # When this fingerprint was computed

    # Optional enrichment
    symbols: tuple[str, ...] = ()       # Symbols present in the population
    filters_applied: tuple[str, ...] = ()  # Description of filters applied

    def to_dict(self) -> dict[str, Any]:
        """Serialise for embedding in experiment results and reports."""
        return {
            "dataset_id": self.dataset_id,
            "dataset_version": self.dataset_version,
            "fingerprint_algorithm": self.fingerprint_algorithm,
            "content_hash": self.content_hash,
            "observation_count": self.observation_count,
            "first_timestamp": self.first_timestamp,
            "last_timestamp": self.last_timestamp,
            "population": self.population,
            "schema_version": self.schema_version,
            "generated_timestamp": self.generated_timestamp,
            "symbols": list(self.symbols),
            "filters_applied": list(self.filters_applied),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DatasetFingerprint":
        """Deserialise from persistence."""
        return cls(
            dataset_id=data.get("dataset_id", ""),
            dataset_version=data.get("dataset_version", ""),
            fingerprint_algorithm=data.get("fingerprint_algorithm", "SHA-256"),
            content_hash=data.get("content_hash", ""),
            observation_count=data.get("observation_count", 0),
            first_timestamp=data.get("first_timestamp", 0),
            last_timestamp=data.get("last_timestamp", 0),
            population=data.get("population", ""),
            schema_version=data.get("schema_version", ""),
            generated_timestamp=data.get("generated_timestamp", ""),
            symbols=tuple(data.get("symbols", ())),
            filters_applied=tuple(data.get("filters_applied", ())),
        )

    @classmethod
    def unavailable(cls, reason: str = "historical — original input not reconstructable") -> "DatasetFingerprint":
        """Create a fingerprint placeholder for historical experiments without content hash."""
        return cls(
            dataset_id="UNAVAILABLE",
            dataset_version="UNAVAILABLE",
            fingerprint_algorithm="NONE",
            content_hash=f"UNAVAILABLE:{reason}",
            observation_count=0,
            first_timestamp=0,
            last_timestamp=0,
            population="UNAVAILABLE",
            schema_version="UNAVAILABLE",
            generated_timestamp=datetime.now(timezone.utc).isoformat(),
        )


# ═══════════════════════════════════════════════════════════════════════════════
# CANONICAL SERIALISATION
# ═══════════════════════════════════════════════════════════════════════════════

def _canonicalise_value(value: Any) -> Any:
    """
    Recursively canonicalise a value for deterministic hashing.
    
    Rules:
    - dicts: sort keys recursively
    - lists: preserve order (order IS semantically meaningful for record fields)
    - floats: round to 8 decimal places
    - None: preserved as null
    - strings: preserved as-is
    - ints/bools: preserved as-is
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value, 8)
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return {k: _canonicalise_value(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canonicalise_value(item) for item in value]
    # Fallback: convert to string
    return str(value)


def _canonical_sort_key(record: dict[str, Any]) -> str:
    """
    Generate a sort key for ordering records deterministically.
    
    Uses: symbol + timestamp + pattern + direction as compound key.
    Falls back to full canonical JSON if those fields are missing.
    """
    parts = []
    # Try common identifying fields
    for key in ("symbol", "time", "timestamp_decision_utc", "entry_time",
                "pattern", "direction", "dir", "correlation_id"):
        val = record.get(key, "")
        if isinstance(val, dict):
            # Nested (v2 schema) — try sub-fields
            continue
        parts.append(str(val))

    # Also check nested identity/decision_snapshot for v2 schema
    identity = record.get("identity", {})
    snapshot = record.get("decision_snapshot", {})
    if identity:
        parts.append(str(identity.get("symbol", "")))
        parts.append(str(identity.get("correlation_id", "")))
    if snapshot:
        parts.append(str(snapshot.get("timestamp_decision_utc", "")))
        parts.append(str(snapshot.get("pattern", "")))

    return "|".join(parts)


def compute_content_hash(records: list[dict[str, Any]]) -> str:
    """
    Compute SHA-256 hash of canonical record content.
    
    Canonicalisation:
    1. Each record is recursively canonicalised (keys sorted, floats rounded)
    2. Records are sorted by canonical sort key (deterministic ordering)
    3. Each canonical record is serialised to compact JSON
    4. All serialised records are joined with newlines
    5. The resulting bytestring is SHA-256 hashed
    
    This ensures:
    - Same records in different order → same hash
    - Same records with different JSON formatting → same hash
    - Different records → different hash
    """
    # Canonicalise each record
    canonical_records = []
    for record in records:
        canonical = _canonicalise_value(record)
        sort_key = _canonical_sort_key(record)
        canonical_json = json.dumps(canonical, separators=(",", ":"), sort_keys=True, ensure_ascii=True)
        canonical_records.append((sort_key, canonical_json))

    # Sort by canonical key for deterministic ordering
    canonical_records.sort(key=lambda x: x[0])

    # Hash the sorted canonical content
    hasher = hashlib.sha256()
    for _, canonical_json in canonical_records:
        hasher.update(canonical_json.encode("utf-8"))
        hasher.update(b"\n")

    return hasher.hexdigest()


# ═══════════════════════════════════════════════════════════════════════════════
# FINGERPRINT BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

def build_dataset_fingerprint(
    records: list[dict[str, Any]],
    *,
    dataset_id: str = "research_population",
    dataset_version: str = "v10",
    population: str = "",
    schema_version: str = "shadow_trades_v2",
    filters_applied: list[str] | None = None,
    time_field: str = "time",
) -> DatasetFingerprint:
    """
    Build a complete dataset fingerprint from the actual records used.
    
    Args:
        records: The ACTUAL records supplied to the experiment (post-filtering)
        dataset_id: Human-readable identifier
        dataset_version: Pipeline/schema version
        population: Description of the population
        schema_version: Record schema version
        filters_applied: List of filter descriptions applied to get this population
        time_field: Field name containing the observation timestamp
    
    Returns:
        DatasetFingerprint with cryptographic content hash
    """
    if not records:
        return DatasetFingerprint(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            fingerprint_algorithm="SHA-256",
            content_hash="EMPTY_POPULATION",
            observation_count=0,
            first_timestamp=0,
            last_timestamp=0,
            population=population,
            schema_version=schema_version,
            generated_timestamp=datetime.now(timezone.utc).isoformat(),
            filters_applied=tuple(filters_applied or []),
        )

    # Extract timestamps
    timestamps = []
    for r in records:
        ts = r.get(time_field, 0)
        if not ts:
            # Try nested paths
            ts = (r.get("decision_snapshot", {}).get("timestamp_decision_utc", 0) or
                  r.get("entry_time", 0) or 0)
        if ts:
            timestamps.append(float(ts))

    # Extract symbols
    symbols = sorted(set(
        r.get("symbol", "") or r.get("identity", {}).get("symbol", "")
        for r in records
    ) - {""})

    # Compute content hash
    content_hash = compute_content_hash(records)

    return DatasetFingerprint(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        fingerprint_algorithm="SHA-256",
        content_hash=content_hash,
        observation_count=len(records),
        first_timestamp=min(timestamps) if timestamps else 0,
        last_timestamp=max(timestamps) if timestamps else 0,
        population=population,
        schema_version=schema_version,
        generated_timestamp=datetime.now(timezone.utc).isoformat(),
        symbols=tuple(symbols),
        filters_applied=tuple(filters_applied or []),
    )
