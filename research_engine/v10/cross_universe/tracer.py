"""
Cross-Universe Tracer.

Retrieves the corresponding observations across all six universes
for a given entity_id (analytical episode).

Does NOT perform analysis. Only retrieves and structures evidence.

Lifecycle traces are:
    - Deterministic (same data → same trace)
    - Reproducible (trace_hash identifies content)
    - Persistable (can be saved and reconstructed)
    - Immutable once persisted (historical traces are never overwritten)
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from research_engine.v10.universes.base import UniverseBuilder
from research_engine.v10.universes.models import Universe


# ═══════════════════════════════════════════════════════════════════════════════
# TRACE RESULT
# ═══════════════════════════════════════════════════════════════════════════════


class UniversePresence:
    """Whether a universe has an observation for a given entity."""
    PRESENT = "PRESENT"
    MISSING = "MISSING"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass
class UniverseObservation:
    """One universe's observation for an entity."""
    universe: str
    presence: str  # PRESENT, MISSING, NOT_APPLICABLE
    record: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "universe": self.universe,
            "presence": self.presence,
        }
        if self.record is not None:
            result["record"] = self.record
        return result


@dataclass
class LifecycleTrace:
    """Complete cross-universe trace for one entity_id."""
    entity_id: str
    trace_status: str = ""  # COMPLETE, PARTIAL, EMPTY
    universes: dict[str, UniverseObservation] = field(default_factory=dict)
    present_count: int = 0
    missing_count: int = 0
    universe_versions: dict[str, str] = field(default_factory=dict)

    @property
    def trace_hash(self) -> str:
        """
        Deterministic content hash for this lifecycle trace.

        Same entity_id + same universe evidence → same hash.
        Changed evidence → different hash.

        Uses canonicalised JSON of presence states and record content,
        sorted by universe key for ordering independence.
        """
        content = {
            "entity_id": self.entity_id,
            "universes": {
                k: {
                    "presence": v.presence,
                    "record": v.record,
                }
                for k, v in sorted(self.universes.items())
            },
        }
        canonical = json.dumps(content, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "trace_status": self.trace_status,
            "trace_hash": self.trace_hash,
            "present_count": self.present_count,
            "missing_count": self.missing_count,
            "universes": {k: v.to_dict() for k, v in self.universes.items()},
            "universe_versions": self.universe_versions,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# TRACER
# ═══════════════════════════════════════════════════════════════════════════════


class CrossUniverseTracer:
    """
    Retrieves lifecycle observations across all six universes by entity_id.

    Usage:
        tracer = CrossUniverseTracer(builders)
        trace = tracer.trace("EURUSD_cycle_20260801_100000")
    """

    def __init__(self, builders: dict[Universe, UniverseBuilder]):
        """
        Build entity_id lookup indexes from all universe builders.

        Args:
            builders: Dict of Universe → built UniverseBuilder instances.
        """
        self._builders = builders
        self._indexes: dict[Universe, dict[str, dict[str, Any]]] = {}
        self._build_indexes()

    def _build_indexes(self) -> None:
        """Build entity_id → record lookup for each universe."""
        for universe, builder in self._builders.items():
            if not builder.is_built:
                continue
            index: dict[str, dict[str, Any]] = {}
            for record in builder.records:
                eid = record.get("entity_id", "")
                if eid and eid not in index:
                    # First occurrence wins (consistent with OutcomeEnrichment)
                    index[eid] = record
            self._indexes[universe] = index

    def trace(self, entity_id: str) -> LifecycleTrace:
        """
        Retrieve the complete lifecycle trace for one entity_id.

        Returns observations from every universe where the entity exists.
        Missing universes are explicitly marked MISSING.
        """
        if not entity_id:
            return LifecycleTrace(entity_id="", trace_status="EMPTY")

        observations: dict[str, UniverseObservation] = {}
        present = 0
        missing = 0

        for universe in Universe:
            u_key = universe.value.lower()
            index = self._indexes.get(universe)

            if index is None:
                # Universe builder not available
                observations[u_key] = UniverseObservation(
                    universe=universe.value,
                    presence=UniversePresence.NOT_APPLICABLE,
                )
                continue

            record = index.get(entity_id)
            if record is not None:
                observations[u_key] = UniverseObservation(
                    universe=universe.value,
                    presence=UniversePresence.PRESENT,
                    record=record,
                )
                present += 1
            else:
                observations[u_key] = UniverseObservation(
                    universe=universe.value,
                    presence=UniversePresence.MISSING,
                )
                missing += 1

        # Determine trace status
        if present == 0:
            status = "EMPTY"
        elif missing == 0:
            status = "COMPLETE"
        else:
            status = "PARTIAL"

        # Capture universe versions for reproducibility
        versions = {}
        for universe, builder in self._builders.items():
            if builder.is_built:
                versions[universe.value] = builder.metadata.content_hash

        return LifecycleTrace(
            entity_id=entity_id,
            trace_status=status,
            universes=observations,
            present_count=present,
            missing_count=missing,
            universe_versions=versions,
        )

    def trace_batch(self, entity_ids: list[str]) -> list[LifecycleTrace]:
        """Trace multiple entities."""
        return [self.trace(eid) for eid in entity_ids]

    def all_entity_ids(self) -> set[str]:
        """Return all entity_ids observed across any universe."""
        all_ids: set[str] = set()
        for index in self._indexes.values():
            all_ids.update(index.keys())
        return all_ids

    @property
    def indexed_universes(self) -> list[str]:
        """Which universes are indexed."""
        return [u.value for u in self._indexes.keys()]
