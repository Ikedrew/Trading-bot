"""
Causal Graph — In-memory DAG store for producer relationships.

Stores nodes (producers) and edges (causal relationships) as a
directed acyclic graph. Provides basic traversal primitives.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════════════════════════════════

class Domain(str, Enum):
    MARKET = "MARKET"
    SIGNAL = "SIGNAL"
    RISK = "RISK"
    DECISION = "DECISION"
    EXECUTION = "EXECUTION"
    POSITION = "POSITION"
    OUTCOME = "OUTCOME"
    LEARNING = "LEARNING"


class EdgeType(str, Enum):
    HARD_DEPENDENCY = "HARD_DEPENDENCY"
    WEAK_MODIFIER = "WEAK_MODIFIER"
    OBSERVATIONAL = "OBSERVATIONAL"


# ═══════════════════════════════════════════════════════════════════════════════
# DATA MODEL
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class CausalNode:
    """A producer in the causal graph."""
    id: str                 # e.g., "SIGNAL.CONFLUENCE_SCORE"
    domain: Domain          # MARKET | SIGNAL | RISK | ...
    component: str          # owning subsystem
    feature: str            # capability name
    stage: str              # lifecycle position
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CausalEdge:
    """A directed causal relationship between producers."""
    source: str             # source node id
    target: str             # target node id
    edge_type: EdgeType     # HARD_DEPENDENCY | WEAK_MODIFIER | OBSERVATIONAL
    strength: float = 1.0   # 0.0–1.0 (1.0 = absolute dependency)
    layer: str = ""         # which persistence layer this edge relates to
    description: str = ""   # human-readable explanation


# ═══════════════════════════════════════════════════════════════════════════════
# GRAPH STORE
# ═══════════════════════════════════════════════════════════════════════════════

class CausalGraph:
    """
    In-memory directed acyclic graph of causal relationships.

    Supports:
        - Node and edge storage
        - Forward traversal (effects)
        - Backward traversal (causes)
        - Structural queries (filter by domain/component)
        - Cycle detection at insertion time
    """

    def __init__(self) -> None:
        self._nodes: dict[str, CausalNode] = {}
        self._forward: dict[str, list[CausalEdge]] = {}   # node → outgoing edges
        self._backward: dict[str, list[CausalEdge]] = {}  # node → incoming edges

    # ─── CONSTRUCTION ─────────────────────────────────────────────────

    def add_node(self, node: CausalNode) -> None:
        """Add a producer node to the graph."""
        self._nodes[node.id] = node
        if node.id not in self._forward:
            self._forward[node.id] = []
        if node.id not in self._backward:
            self._backward[node.id] = []

    def add_edge(self, edge: CausalEdge, *, allow_feedback: bool = False) -> None:
        """
        Add a directed causal edge. Rejects if it would create a cycle
        (unless allow_feedback=True for declared Arc 2 feedback loops).
        """
        if edge.source not in self._nodes:
            raise KeyError(f"Source node '{edge.source}' not in graph")
        if edge.target not in self._nodes:
            raise KeyError(f"Target node '{edge.target}' not in graph")
        if not allow_feedback and self._would_create_cycle(edge.source, edge.target):
            raise ValueError(
                f"Edge {edge.source} → {edge.target} would create a cycle"
            )
        self._forward.setdefault(edge.source, []).append(edge)
        self._backward.setdefault(edge.target, []).append(edge)

    def _would_create_cycle(self, source: str, target: str) -> bool:
        """Check if adding source→target creates a cycle (target reaches source)."""
        visited: set[str] = set()
        stack = [target]
        while stack:
            current = stack.pop()
            if current == source:
                return True
            if current in visited:
                continue
            visited.add(current)
            for edge in self._forward.get(current, []):
                stack.append(edge.target)
        return False

    # ─── QUERIES ──────────────────────────────────────────────────────

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    @property
    def edge_count(self) -> int:
        return sum(len(edges) for edges in self._forward.values())

    def get_node(self, node_id: str) -> CausalNode | None:
        return self._nodes.get(node_id)

    def get_nodes(self, **filters: Any) -> list[CausalNode]:
        """Filter nodes by attribute (domain, component, stage, etc.)."""
        results = list(self._nodes.values())
        for key, value in filters.items():
            results = [n for n in results if getattr(n, key, None) == value]
        return results

    def get_forward_edges(self, node_id: str) -> list[CausalEdge]:
        """Get all outgoing edges from a node."""
        return list(self._forward.get(node_id, []))

    def get_backward_edges(self, node_id: str) -> list[CausalEdge]:
        """Get all incoming edges to a node."""
        return list(self._backward.get(node_id, []))

    # ─── TRAVERSAL ────────────────────────────────────────────────────

    def forward_reachable(
        self,
        node_id: str,
        *,
        edge_types: set[EdgeType] | None = None,
        max_depth: int = 50,
    ) -> list[str]:
        """
        All nodes reachable forward from node_id (what does it affect?).
        DFS traversal. Respects edge type filter.
        """
        visited: list[str] = []
        stack: list[tuple[str, int]] = [(node_id, 0)]
        seen: set[str] = {node_id}

        while stack:
            current, depth = stack.pop()
            if depth > max_depth:
                continue
            for edge in self._forward.get(current, []):
                if edge_types and edge.edge_type not in edge_types:
                    continue
                if edge.target not in seen:
                    seen.add(edge.target)
                    visited.append(edge.target)
                    stack.append((edge.target, depth + 1))

        return visited

    def backward_reachable(
        self,
        node_id: str,
        *,
        edge_types: set[EdgeType] | None = None,
        max_depth: int = 50,
    ) -> list[str]:
        """
        All nodes that causally precede node_id (what causes it?).
        DFS backward traversal.
        """
        visited: list[str] = []
        stack: list[tuple[str, int]] = [(node_id, 0)]
        seen: set[str] = {node_id}

        while stack:
            current, depth = stack.pop()
            if depth > max_depth:
                continue
            for edge in self._backward.get(current, []):
                if edge_types and edge.edge_type not in edge_types:
                    continue
                if edge.source not in seen:
                    seen.add(edge.source)
                    visited.append(edge.source)
                    stack.append((edge.source, depth + 1))

        return visited

    def trace_paths(
        self,
        target_id: str,
        *,
        max_depth: int = 20,
    ) -> list[list[str]]:
        """
        Find all paths from roots to target_id. Returns list of paths.
        Each path is ordered root → ... → target.
        """
        paths: list[list[str]] = []

        def _dfs(current: str, path: list[str], depth: int) -> None:
            if depth > max_depth:
                return
            incoming = self._backward.get(current, [])
            if not incoming:
                # This is a root — path complete
                paths.append(list(reversed(path)))
                return
            for edge in incoming:
                if edge.source not in path:  # avoid cycles
                    _dfs(edge.source, path + [edge.source], depth + 1)

        _dfs(target_id, [target_id], 0)
        return paths

    def risk_surface(self, node_id: str) -> list[dict[str, Any]]:
        """
        Identify all nodes downstream that are at risk if node_id fails.
        Returns nodes + their edge types (how dependent they are).
        """
        surface: list[dict[str, Any]] = []
        for target_id in self.forward_reachable(node_id):
            node = self._nodes.get(target_id)
            # Find the edges connecting to this target from the risk source
            incoming = [e for e in self._backward.get(target_id, [])
                        if e.source in self.forward_reachable(node_id) or e.source == node_id]
            max_severity = max(
                (e.edge_type for e in incoming),
                key=lambda t: {"HARD_DEPENDENCY": 3, "WEAK_MODIFIER": 2, "OBSERVATIONAL": 1}.get(t.value, 0),
                default=EdgeType.OBSERVATIONAL,
            )
            surface.append({
                "node_id": target_id,
                "domain": node.domain.value if node else "?",
                "dependency_type": max_severity.value,
                "distance": self._shortest_distance(node_id, target_id),
            })
        return sorted(surface, key=lambda x: x["distance"])

    def _shortest_distance(self, source: str, target: str) -> int:
        """BFS shortest path length."""
        from collections import deque
        queue: deque[tuple[str, int]] = deque([(source, 0)])
        visited: set[str] = {source}
        while queue:
            current, dist = queue.popleft()
            for edge in self._forward.get(current, []):
                if edge.target == target:
                    return dist + 1
                if edge.target not in visited:
                    visited.add(edge.target)
                    queue.append((edge.target, dist + 1))
        return -1

    def simulate_removal(self, node_id: str) -> dict[str, Any]:
        """
        Simulate: what happens if node_id is removed?
        Returns affected nodes and severity classification.
        """
        affected = self.forward_reachable(node_id, edge_types={EdgeType.HARD_DEPENDENCY})
        weakly_affected = self.forward_reachable(node_id, edge_types={EdgeType.WEAK_MODIFIER})
        observational = self.forward_reachable(node_id, edge_types={EdgeType.OBSERVATIONAL})

        return {
            "removed_node": node_id,
            "hard_failures": affected,
            "degraded": [n for n in weakly_affected if n not in affected],
            "observation_loss": [n for n in observational if n not in affected and n not in weakly_affected],
            "total_impact": len(set(affected + weakly_affected + observational)),
        }
