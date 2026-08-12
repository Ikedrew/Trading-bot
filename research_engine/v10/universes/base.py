"""
Universe Base Interface.

Every universe builder implements this contract:
    - load() → loads raw data from source files
    - build() → transforms raw data into a normalised population
    - get_population(name) → returns a filtered subset of the population
    - metadata → generation metadata (timestamp, record count, hash)

The universe builder is the ONLY place where raw data schema knowledge lives.
Questions interact with normalised populations, never raw data.
"""

from __future__ import annotations

import hashlib
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_engine.v10.universes.models import Population, Universe

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UniverseMetadata:
    """Metadata about a generated universe population."""
    universe: str
    record_count: int
    generation_timestamp: str
    content_hash: str
    source_files: tuple[str, ...] = ()
    populations_available: tuple[str, ...] = ()
    exclusions: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "universe": self.universe,
            "record_count": self.record_count,
            "generation_timestamp": self.generation_timestamp,
            "content_hash": self.content_hash,
            "source_files": list(self.source_files),
            "populations_available": list(self.populations_available),
            "exclusions": self.exclusions,
        }


class UniverseBuilder(ABC):
    """
    Abstract base class for all universe builders.

    Each builder:
    1. Knows how to locate and load its raw data sources
    2. Normalises raw data into flat records with semantic field names
    3. Produces named populations (filtered subsets)
    """

    def __init__(self):
        self._records: list[dict[str, Any]] = []
        self._metadata: UniverseMetadata | None = None
        self._built = False

    @property
    @abstractmethod
    def universe_type(self) -> Universe:
        """Which universe this builder serves."""
        ...

    @abstractmethod
    def load(self) -> int:
        """
        Load raw data from source files.

        Returns:
            Number of raw records loaded.
        """
        ...

    @abstractmethod
    def build(self) -> list[dict[str, Any]]:
        """
        Transform raw data into normalised population records.

        Each record is a flat dict with semantic field names matching
        what the question bank declares in required_fields.

        Returns:
            List of normalised records.
        """
        ...

    @abstractmethod
    def get_population(self, population: Population) -> list[dict[str, Any]]:
        """
        Return a filtered subset of the built population.

        Args:
            population: The named population to retrieve.

        Returns:
            Filtered list of records matching the population criteria.
        """
        ...

    @property
    def records(self) -> list[dict[str, Any]]:
        """All normalised records after build()."""
        if not self._built:
            raise RuntimeError(
                f"{self.universe_type.value} universe not built. Call build() first."
            )
        return self._records

    @property
    def metadata(self) -> UniverseMetadata:
        """Generation metadata. Available after build()."""
        if self._metadata is None:
            raise RuntimeError(
                f"{self.universe_type.value} universe not built. Call build() first."
            )
        return self._metadata

    @property
    def is_built(self) -> bool:
        return self._built

    def _compute_hash(self, records: list[dict[str, Any]]) -> str:
        """Compute a stable content hash for the population."""
        content = json.dumps(records, sort_keys=True, default=str)
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def _generate_metadata(
        self,
        records: list[dict[str, Any]],
        source_files: tuple[str, ...],
        populations: tuple[str, ...],
        exclusions: dict[str, Any] | None = None,
    ) -> UniverseMetadata:
        """Generate metadata for the built universe."""
        return UniverseMetadata(
            universe=self.universe_type.value,
            record_count=len(records),
            generation_timestamp=datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            content_hash=self._compute_hash(records),
            source_files=source_files,
            populations_available=populations,
            exclusions=exclusions or {},
        )

    def _load_jsonl(self, path: Path) -> list[dict[str, Any]]:
        """Load a JSONL file into a list of dicts."""
        records = []
        if not path.exists():
            logger.warning(f"[{self.universe_type.value}] File not found: {path}")
            return records
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return records

    def _load_jsonl_directory(self, directory: Path) -> list[dict[str, Any]]:
        """Load all JSONL files from a directory (recursively)."""
        records = []
        if not directory.exists():
            logger.warning(
                f"[{self.universe_type.value}] Directory not found: {directory}"
            )
            return records
        for jsonl_file in sorted(directory.rglob("*.jsonl")):
            records.extend(self._load_jsonl(jsonl_file))
        return records
