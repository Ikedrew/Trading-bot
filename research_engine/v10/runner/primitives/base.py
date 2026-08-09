"""
Analysis Primitive Base Interface and Registry.

Every analysis primitive implements a common contract:
    - Receives a resolved population + parameters
    - Produces structured AnalysisResult
    - Never writes files directly
    - Never modifies trading logic

The registry maps analysis_type → primitive implementation.
New questions reuse existing primitives without runner modification.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# ANALYSIS RESULT
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class AnalysisResult:
    """
    Structured output of an analysis primitive.

    The container is standardised; the content varies by primitive type.
    """
    analysis_type: str = ""
    success: bool = True
    error: str = ""

    # Core metrics (primitive-specific)
    metrics: dict[str, Any] = field(default_factory=dict)

    # Statistical results
    statistical_results: dict[str, Any] = field(default_factory=dict)

    # Comparisons between groups
    comparisons: dict[str, Any] = field(default_factory=dict)

    # Segmented results
    segments: dict[str, Any] = field(default_factory=dict)

    # Distribution data
    distributions: dict[str, Any] = field(default_factory=dict)

    # Effect sizes
    effect_sizes: dict[str, Any] = field(default_factory=dict)

    # Evidence items (narrative)
    evidence: list[str] = field(default_factory=list)

    # Warnings and limitations
    warnings: list[str] = field(default_factory=list)

    # Sample metadata
    sample_size: int = 0
    sub_sample_sizes: dict[str, int] = field(default_factory=dict)

    # Primitive version
    primitive_version: str = "1.0.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "analysis_type": self.analysis_type,
            "success": self.success,
            "error": self.error,
            "metrics": self.metrics,
            "statistical_results": self.statistical_results,
            "comparisons": self.comparisons,
            "segments": self.segments,
            "distributions": self.distributions,
            "effect_sizes": self.effect_sizes,
            "evidence": self.evidence,
            "warnings": self.warnings,
            "sample_size": self.sample_size,
            "sub_sample_sizes": self.sub_sample_sizes,
            "primitive_version": self.primitive_version,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# ANALYSIS PRIMITIVE BASE
# ═══════════════════════════════════════════════════════════════════════════════


class AnalysisPrimitive(ABC):
    """
    Abstract base for all analysis primitives.

    A primitive:
        - Receives data + parameters
        - Produces AnalysisResult
        - Never writes files
        - Never modifies state
        - Handles its own errors gracefully
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique primitive name (matches analysis_type enum)."""
        ...

    @property
    def version(self) -> str:
        """Primitive implementation version."""
        return "1.0.0"

    @abstractmethod
    def analyse(
        self,
        population: list[dict[str, Any]],
        parameters: dict[str, Any] | None = None,
    ) -> AnalysisResult:
        """
        Perform the analysis on a resolved population.

        Args:
            population: List of normalised records from a universe.
            parameters: Question-specific parameters (fields, thresholds, etc.)

        Returns:
            AnalysisResult with structured evidence.
        """
        ...

    def safe_analyse(
        self,
        population: list[dict[str, Any]],
        parameters: dict[str, Any] | None = None,
    ) -> AnalysisResult:
        """
        Execute analyse() with error isolation.

        A failed primitive produces an error result rather than crashing.
        """
        try:
            result = self.analyse(population, parameters)
            result.primitive_version = self.version
            return result
        except Exception as exc:
            return AnalysisResult(
                analysis_type=self.name,
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                primitive_version=self.version,
            )


# ═══════════════════════════════════════════════════════════════════════════════
# ANALYSIS REGISTRY
# ═══════════════════════════════════════════════════════════════════════════════


class AnalysisRegistry:
    """
    Registry of available analysis primitives.

    Maps analysis_type → primitive instance.
    Prevents duplicates. Supports version tracking.
    """

    def __init__(self):
        self._primitives: dict[str, AnalysisPrimitive] = {}

    def register(self, primitive: AnalysisPrimitive) -> None:
        """Register a primitive. Raises if duplicate name."""
        if primitive.name in self._primitives:
            raise ValueError(
                f"Primitive '{primitive.name}' already registered "
                f"(version {self._primitives[primitive.name].version})"
            )
        self._primitives[primitive.name] = primitive
        logger.debug(f"[REGISTRY] Registered primitive: {primitive.name} v{primitive.version}")

    def get(self, name: str) -> AnalysisPrimitive | None:
        """Look up a primitive by name."""
        return self._primitives.get(name)

    def has(self, name: str) -> bool:
        """Check if a primitive is registered."""
        return name in self._primitives

    @property
    def registered_names(self) -> list[str]:
        """List all registered primitive names."""
        return sorted(self._primitives.keys())

    @property
    def count(self) -> int:
        return len(self._primitives)

    def versions(self) -> dict[str, str]:
        """Get name → version mapping for all primitives."""
        return {name: p.version for name, p in self._primitives.items()}
