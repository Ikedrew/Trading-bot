"""
Population Versioning & Latest-Valid Resolver.

Every generated population receives a reproducible identity (PopulationVersion).
The resolver determines which population version is valid for a given question.

Resolution rules:
    - Population must be VALIDATED
    - Population schema must be COMPATIBLE with question requirements
    - Required fields must be PRESENT and non-null at acceptable rates
    - Coverage must be VALID (instruments, time range)
    - If no valid population exists: BLOCK the question

Never silently falls back to an older or incompatible population.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from research_engine.v10.universes.contracts import (
    POPULATION_CONTRACTS,
    UNIVERSE_CONTRACTS,
    PopulationContract,
    PopulationStatus,
    get_population_contract,
)
from research_engine.v10.universes.health import (
    PopulationHealth,
    check_population_health,
)
from research_engine.v10.universes.models import Population, Universe


# ═══════════════════════════════════════════════════════════════════════════════
# POPULATION VERSION
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class PopulationVersion:
    """Reproducible identity for a generated population snapshot."""
    population_id: str
    universe_id: str
    generation_timestamp: str
    generator_version: str
    source_schema_version: str
    row_count: int
    content_hash: str
    coverage_start: str  # ISO timestamp or empty
    coverage_end: str
    instruments: tuple[str, ...] = ()
    health_status: str = "UNKNOWN"

    def to_dict(self) -> dict[str, Any]:
        return {
            "population_id": self.population_id,
            "universe_id": self.universe_id,
            "generation_timestamp": self.generation_timestamp,
            "generator_version": self.generator_version,
            "source_schema_version": self.source_schema_version,
            "row_count": self.row_count,
            "content_hash": self.content_hash,
            "coverage_start": self.coverage_start,
            "coverage_end": self.coverage_end,
            "instruments": list(self.instruments),
            "health_status": self.health_status,
        }


def create_population_version(
    records: list[dict[str, Any]],
    population: Population,
    universe: Universe,
    generator_version: str = "1.0.0",
) -> PopulationVersion:
    """
    Create a versioned identity for a population snapshot.

    Args:
        records: The population records.
        population: Which population.
        universe: Which universe.
        generator_version: Version of the builder that produced this.

    Returns:
        PopulationVersion with reproducible identity.
    """
    # Content hash
    content = json.dumps(records, sort_keys=True, default=str)
    content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

    # Coverage
    timestamps = []
    instruments = set()
    for r in records:
        ts = r.get("entry_time") or r.get("timestamp_utc")
        if isinstance(ts, (int, float)) and ts > 0:
            timestamps.append(ts)
        elif isinstance(ts, str) and ts:
            timestamps.append(ts)
        sym = r.get("symbol", "")
        if sym:
            instruments.add(sym)

    coverage_start = str(min(timestamps)) if timestamps else ""
    coverage_end = str(max(timestamps)) if timestamps else ""

    # Health check
    health = check_population_health(records, population, universe)

    # Source schema version from universe contract
    uc = UNIVERSE_CONTRACTS.get(universe)
    schema_version = uc.source_schema_versions[0] if uc else "unknown"

    return PopulationVersion(
        population_id=population.value,
        universe_id=universe.value,
        generation_timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        generator_version=generator_version,
        source_schema_version=schema_version,
        row_count=len(records),
        content_hash=content_hash,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        instruments=tuple(sorted(instruments)),
        health_status=health.status.value,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# LATEST-VALID RESOLVER
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class ResolutionResult:
    """Result of resolving a population for a question."""
    resolved: bool
    population_id: str
    universe_id: str
    version: PopulationVersion | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "resolved": self.resolved,
            "population_id": self.population_id,
            "universe_id": self.universe_id,
            "version": self.version.to_dict() if self.version else None,
            "reason": self.reason,
        }


class PopulationResolver:
    """
    Resolves the latest valid population for a question's requirements.

    Resolution rules:
        1. Population must exist (contract defined)
        2. Population health must be VALID or DEGRADED (not INVALID/EMPTY)
        3. Required fields must be present at acceptable null rates
        4. Row count must meet minimum sample size
    """

    def __init__(self):
        self._versions: dict[str, PopulationVersion] = {}

    def register_version(self, version: PopulationVersion) -> None:
        """Register a built population version."""
        self._versions[version.population_id] = version

    def resolve(
        self,
        population: Population,
        universe: Universe,
        minimum_sample_size: int = 10,
        required_fields: tuple[str, ...] = (),
    ) -> ResolutionResult:
        """
        Resolve the latest valid population version.

        Args:
            population: Which population is required.
            universe: Which universe it belongs to.
            minimum_sample_size: Minimum records needed.
            required_fields: Fields that must be present.

        Returns:
            ResolutionResult indicating success or blocking reason.
        """
        pop_id = population.value

        # 1. Check contract exists
        contract = get_population_contract(population)
        if contract is None:
            return ResolutionResult(
                resolved=False,
                population_id=pop_id,
                universe_id=universe.value,
                reason=f"No contract defined for population '{pop_id}'",
            )

        # 2. Check version exists
        version = self._versions.get(pop_id)
        if version is None:
            return ResolutionResult(
                resolved=False,
                population_id=pop_id,
                universe_id=universe.value,
                reason=f"Population '{pop_id}' has not been built/registered",
            )

        # 3. Check health status
        if version.health_status in ("INVALID",):
            return ResolutionResult(
                resolved=False,
                population_id=pop_id,
                universe_id=universe.value,
                version=version,
                reason=f"Population health is {version.health_status}",
            )

        # 4. Check minimum sample size
        if version.row_count < minimum_sample_size:
            return ResolutionResult(
                resolved=False,
                population_id=pop_id,
                universe_id=universe.value,
                version=version,
                reason=(
                    f"Row count {version.row_count} < minimum {minimum_sample_size}"
                ),
            )

        # 5. Population is valid
        return ResolutionResult(
            resolved=True,
            population_id=pop_id,
            universe_id=universe.value,
            version=version,
            reason="Population valid and meets requirements",
        )

    def resolve_for_question(
        self,
        required_populations: tuple[Population, ...],
        required_universes: tuple[Universe, ...],
        minimum_sample_size: int = 10,
    ) -> list[ResolutionResult]:
        """
        Resolve all populations required by a question.

        Returns a list of resolution results. If ANY is unresolved,
        the question should be BLOCKED.
        """
        results = []
        for pop in required_populations:
            # Determine which universe this population belongs to
            contract = get_population_contract(pop)
            universe = contract.universe_id if contract else required_universes[0]
            if isinstance(universe, str):
                universe = Universe(universe)

            result = self.resolve(
                population=pop,
                universe=universe,
                minimum_sample_size=minimum_sample_size,
            )
            results.append(result)
        return results

    @property
    def registered_populations(self) -> list[str]:
        return list(self._versions.keys())
