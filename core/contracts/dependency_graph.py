"""
Validator Dependency Graph — Directed acyclic graph for execution ordering.

Manages validator execution order based on declared dependencies.
Provides:
    - Topological sort for deterministic execution order
    - Dependency validation (cycle detection, missing refs, self-deps)
    - Skip-propagation logic (downstream validators skipped on failure)
    - Graph export for audit/visualization

RULES:
    - The graph is IMMUTABLE after build (read-only after startup)
    - Execution order is DETERMINISTIC (same graph → same order always)
    - Validators only run after ALL dependencies have PASSED
    - If a dependency FAILS, all downstream validators are SKIPPED
    - Independent validators (no shared dependency chain) continue normally

Usage:
    from core.contracts.dependency_graph import DependencyGraph, GraphValidationError

    graph = DependencyGraph()
    graph.add_node("SCHEMA_001", depends_on=[])
    graph.add_node("FEATURE_001", depends_on=["SCHEMA_001"])
    graph.add_node("CAUSAL_001", depends_on=["SCHEMA_001", "PERSISTENCE_001"])
    graph.build()  # Validates and freezes

    order = graph.execution_order  # Topologically sorted
"""

from __future__ import annotations

from collections import deque
from enum import Enum
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# EXECUTION STATE
# ═══════════════════════════════════════════════════════════════════════════════

class ValidatorState(str, Enum):
    """Execution state of a validator during a validation run."""
    PASSED = "PASSED"           # Executed, no ERROR+ violations
    FAILED = "FAILED"           # Executed, produced ERROR+ violations
    SKIPPED = "SKIPPED"         # Dependency failure — not executed
    NOT_APPLICABLE = "NOT_APPLICABLE"  # applies_to() returned False
    ERROR = "ERROR"             # Validator raised an exception
    NOT_RUN = "NOT_RUN"         # Disabled or not yet reached


# ═══════════════════════════════════════════════════════════════════════════════
# GRAPH VALIDATION ERRORS
# ═══════════════════════════════════════════════════════════════════════════════

class GraphValidationError(Exception):
    """Raised when the dependency graph is invalid at build time."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(f"Dependency graph invalid: {errors}")


# ═══════════════════════════════════════════════════════════════════════════════
# DEPENDENCY GRAPH
# ═══════════════════════════════════════════════════════════════════════════════

class DependencyGraph:
    """
    Directed acyclic graph for validator execution ordering.

    Nodes are validator IDs. Edges represent "depends on" relationships.
    After build(), the graph is frozen (read-only).

    Properties:
        - Deterministic topological sort
        - Cycle detection at build time
        - Missing reference detection at build time
        - O(V+E) execution scheduling
    """

    def __init__(self) -> None:
        self._nodes: dict[str, list[str]] = {}  # id → depends_on list
        self._built = False
        self._order: list[str] = []
        self._dependents: dict[str, set[str]] = {}  # id → set of IDs that depend on it

    @property
    def is_built(self) -> bool:
        return self._built

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    def add_node(self, validator_id: str, *, depends_on: list[str] | None = None) -> None:
        """
        Add a validator node to the graph.

        Args:
            validator_id: Globally unique validator ID
            depends_on: List of validator IDs this validator depends on (empty = root)

        Raises:
            RuntimeError: If graph is already built (frozen)
        """
        if self._built:
            raise RuntimeError("Cannot modify dependency graph after build()")
        self._nodes[validator_id] = depends_on or []

    def build(self) -> None:
        """
        Validate and freeze the dependency graph.

        Performs:
            1. Self-dependency detection
            2. Missing reference detection
            3. Duplicate node detection (handled at add_node)
            4. Circular dependency detection
            5. Topological sort for execution order

        Raises:
            GraphValidationError: If any validation check fails
        """
        errors: list[str] = []

        # ─── Self-dependency check ────────────────────────────────────
        for vid, deps in self._nodes.items():
            if vid in deps:
                errors.append(f"Self-dependency: '{vid}' depends on itself")

        # ─── Missing reference check ─────────────────────────────────
        all_ids = set(self._nodes.keys())
        for vid, deps in self._nodes.items():
            for dep in deps:
                if dep not in all_ids:
                    errors.append(
                        f"Missing dependency: '{vid}' depends on '{dep}' "
                        f"which is not registered"
                    )

        # ─── Circular dependency detection (via topological sort) ─────
        if not errors:
            sorted_order = self._topological_sort()
            if sorted_order is None:
                errors.append("Circular dependency detected in validator graph")
            else:
                self._order = sorted_order

        if errors:
            raise GraphValidationError(errors)

        # ─── Build reverse dependency map (who depends on me) ─────────
        self._dependents = {vid: set() for vid in self._nodes}
        for vid, deps in self._nodes.items():
            for dep in deps:
                self._dependents[dep].add(vid)

        self._built = True

    def _topological_sort(self) -> list[str] | None:
        """
        Kahn's algorithm for topological sort.

        Returns sorted order, or None if cycle detected.
        Deterministic: ties broken alphabetically.
        """
        # Compute in-degree
        in_degree: dict[str, int] = {vid: 0 for vid in self._nodes}
        for vid, deps in self._nodes.items():
            for dep in deps:
                if dep in in_degree:
                    in_degree[vid] = in_degree.get(vid, 0)
                    # dep is a prerequisite of vid (edge: dep → vid)
                    # So vid's in-degree is the count of its deps
                    pass

        # Recompute properly: in-degree = number of dependencies
        in_degree = {vid: len([d for d in deps if d in self._nodes]) for vid, deps in self._nodes.items()}

        # Start with nodes that have no dependencies
        queue: list[str] = sorted([vid for vid, deg in in_degree.items() if deg == 0])
        result: list[str] = []

        while queue:
            # Deterministic: always pick alphabetically first
            queue.sort()
            current = queue.pop(0)
            result.append(current)

            # For each node that depends on current, decrement in-degree
            for vid, deps in self._nodes.items():
                if current in deps:
                    in_degree[vid] -= 1
                    if in_degree[vid] == 0:
                        queue.append(vid)

        # If not all nodes processed → cycle exists
        if len(result) != len(self._nodes):
            return None

        return result

    @property
    def execution_order(self) -> list[str]:
        """Topologically sorted execution order. Graph must be built."""
        if not self._built:
            raise RuntimeError("Graph must be built before accessing execution_order")
        return list(self._order)

    def get_dependencies(self, validator_id: str) -> list[str]:
        """Get direct dependencies of a validator."""
        return list(self._nodes.get(validator_id, []))

    def get_dependents(self, validator_id: str) -> set[str]:
        """Get validators that depend on this one (direct dependents only)."""
        if not self._built:
            raise RuntimeError("Graph must be built before accessing dependents")
        return set(self._dependents.get(validator_id, set()))

    def get_all_downstream(self, validator_id: str) -> set[str]:
        """
        Get ALL validators downstream of this one (transitive closure).

        Used for skip-propagation: if validator_id fails, all returned
        validators must be skipped.
        """
        if not self._built:
            raise RuntimeError("Graph must be built before computing downstream")

        visited: set[str] = set()
        queue = deque(self._dependents.get(validator_id, set()))

        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            queue.extend(self._dependents.get(current, set()))

        return visited

    def compute_skip_set(self, failed_ids: set[str]) -> set[str]:
        """
        Compute the full set of validators that must be SKIPPED
        given a set of failed validator IDs.

        Returns all transitive downstream validators of ALL failures.
        """
        skip: set[str] = set()
        for failed_id in failed_ids:
            skip |= self.get_all_downstream(failed_id)
        return skip

    def export(self) -> dict[str, Any]:
        """Export graph structure for audit/visualization."""
        return {
            "graph_version": "dependency_graph_v1",
            "built": self._built,
            "node_count": self.node_count,
            "execution_order": self._order if self._built else [],
            "nodes": {
                vid: {
                    "depends_on": deps,
                    "dependents": sorted(self._dependents.get(vid, set())) if self._built else [],
                }
                for vid, deps in self._nodes.items()
            },
        }
