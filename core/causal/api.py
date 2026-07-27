"""
Causal API Layer — High-level deterministic interface over the causal query engine.

This is a THIN WRAPPER over engine.query(). It does NOT:
    - Modify graph logic
    - Redefine causal semantics
    - Store additional state
    - Infer causality or add heuristics

It ONLY translates method calls → CQ-QL queries.

Position in system:
    ONTOLOGY LAYER
        → CAUSAL GRAPH LAYER
            → QUERY ENGINE (CQ-QL)
                → API LAYER  ← THIS
                    → APPLICATIONS (trading bot, analytics, UI)

Usage:
    from core.causal.api import get_causal_api

    api = get_causal_api()

    api.why("DECISION.SHADOW_TRADE")
    api.forward("FEATURE.STATE_CHANGE")
    api.risk_surface("SIGNAL.CONFLUENCE_SCORE")
    api.what_if("SIGNAL.BIAS_TRANSITION")
    api.backward("OUTCOME.PERSIST")
"""

from __future__ import annotations

from typing import Any

from core.causal.engine import CausalEngine, get_causal_engine


class CausalAPI:
    """
    High-level deterministic interface over the causal query engine.

    Every method is a 1:1 mapping to a CQ-QL query.
    Stateless. Deterministic. Never mutates the graph.
    """

    def __init__(self, engine: CausalEngine) -> None:
        self.engine = engine

    def why(self, node_id: str) -> dict[str, Any]:
        """
        Explain why a node/event happened.
        Returns all causal paths from system roots to this node.
        """
        return self.engine.query(f"TRACE {node_id}")

    def forward(self, node_id: str) -> dict[str, Any]:
        """
        What this node influences downstream.
        Returns all nodes causally affected by this producer.
        """
        return self.engine.query(f"FORWARD IMPACT OF {node_id}")

    def backward(self, node_id: str) -> dict[str, Any]:
        """
        What caused this node.
        Returns all causal ancestors of this producer.
        """
        return self.engine.query(f"BACKWARD IMPACT OF {node_id}")

    def risk_surface(self, node_id: str) -> dict[str, Any]:
        """
        What breaks if this node fails.
        Returns all downstream nodes at risk, classified by dependency type.
        """
        return self.engine.query(f"RISK SURFACE OF {node_id}")

    def what_if(self, node_id: str) -> dict[str, Any]:
        """
        Simulate causal impact of removing a node.
        Returns hard failures, degraded nodes, and observation loss.
        """
        return self.engine.query(f"SIMULATE REMOVAL {node_id}")

    def lineage(self, node_id: str) -> dict[str, Any]:
        """
        Full causal history of a decision.
        Identical to why() — returns all paths from roots to this node.
        """
        return self.engine.query(f"TRACE {node_id}")

    def find(self, **filters: str) -> dict[str, Any]:
        """
        Find nodes matching structural criteria.
        Example: api.find(domain="RISK") or api.find(component="SIGNAL_EVALUATION")
        """
        where_clause = " AND ".join(f"{k} = {v}" for k, v in filters.items())
        return self.engine.query(f"FIND NODES WHERE {where_clause}")

    def query(self, raw: str) -> dict[str, Any]:
        """
        Direct pass-through to CQ-QL engine.
        For advanced queries not covered by convenience methods.
        """
        return self.engine.query(raw)


# ═══════════════════════════════════════════════════════════════════════════════
# SINGLETON ACCESS
# ═══════════════════════════════════════════════════════════════════════════════

_api: CausalAPI | None = None


def get_causal_api() -> CausalAPI:
    """Get or create the singleton CausalAPI instance."""
    global _api
    if _api is None:
        _api = CausalAPI(get_causal_engine())
    return _api
