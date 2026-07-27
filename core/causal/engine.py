"""
Causal Engine — Query executor that answers causal questions.

Combines graph store + query parser + traversal into a single interface.

Usage:
    from core.causal import get_causal_engine

    engine = get_causal_engine()
    result = engine.query("FORWARD IMPACT OF SIGNAL.CONFLUENCE_SCORE")
    result = engine.query("TRACE OUTCOME.PERSIST")
    result = engine.query("FIND NODES WHERE domain = RISK")
"""

from __future__ import annotations

from typing import Any

from core.causal.graph import CausalGraph, CausalNode, CausalEdge, EdgeType, Domain
from core.causal.query import CQ, QueryType, parse_query


class CausalEngine:
    """
    Query executor for causal reasoning over the producer graph.

    Accepts CQ-QL query strings or pre-parsed CQ objects.
    Returns structured results for each query type.
    """

    def __init__(self, graph: CausalGraph) -> None:
        self._graph = graph

    @property
    def graph(self) -> CausalGraph:
        return self._graph

    def query(self, query_str: str) -> dict[str, Any]:
        """
        Execute a CQ-QL query and return structured results.

        Args:
            query_str: CQ-QL query (e.g., "TRACE OUTCOME.PERSIST")

        Returns:
            Dict with 'query', 'result_type', and type-specific data.
        """
        cq = parse_query(query_str)
        return self.execute(cq)

    def execute(self, cq: CQ) -> dict[str, Any]:
        """Execute a parsed CQ query object."""
        if cq.query_type == QueryType.FIND_NODES:
            return self._find_nodes(cq)
        elif cq.query_type == QueryType.FORWARD_IMPACT:
            return self._forward_impact(cq)
        elif cq.query_type == QueryType.BACKWARD_IMPACT:
            return self._backward_impact(cq)
        elif cq.query_type == QueryType.TRACE:
            return self._trace(cq)
        elif cq.query_type == QueryType.RISK_SURFACE:
            return self._risk_surface(cq)
        elif cq.query_type in (QueryType.SIMULATE_CHANGE, QueryType.SIMULATE_REMOVAL):
            return self._simulate(cq)
        else:
            return {"error": f"Unsupported query type: {cq.query_type}"}

    # ─── QUERY EXECUTORS ──────────────────────────────────────────────

    def _find_nodes(self, cq: CQ) -> dict[str, Any]:
        nodes = self._graph.get_nodes(**cq.filters)
        return {
            "query": f"FIND NODES WHERE {cq.filters}",
            "result_type": "node_list",
            "count": len(nodes),
            "nodes": [{"id": n.id, "domain": n.domain.value, "component": n.component, "feature": n.feature, "stage": n.stage} for n in nodes],
        }

    def _forward_impact(self, cq: CQ) -> dict[str, Any]:
        affected = self._graph.forward_reachable(cq.target_node)
        return {
            "query": f"FORWARD IMPACT OF {cq.target_node}",
            "result_type": "impact_list",
            "source": cq.target_node,
            "affected_count": len(affected),
            "affected_nodes": affected,
            "by_domain": self._group_by_domain(affected),
        }

    def _backward_impact(self, cq: CQ) -> dict[str, Any]:
        causes = self._graph.backward_reachable(cq.target_node)
        return {
            "query": f"BACKWARD IMPACT OF {cq.target_node}",
            "result_type": "ancestry_list",
            "target": cq.target_node,
            "cause_count": len(causes),
            "causal_ancestors": causes,
            "by_domain": self._group_by_domain(causes),
        }

    def _trace(self, cq: CQ) -> dict[str, Any]:
        paths = self._graph.trace_paths(cq.target_node)
        return {
            "query": f"TRACE {cq.target_node}",
            "result_type": "trace_paths",
            "target": cq.target_node,
            "path_count": len(paths),
            "paths": paths,
            "shortest_path": min(paths, key=len) if paths else [],
            "longest_path": max(paths, key=len) if paths else [],
        }

    def _risk_surface(self, cq: CQ) -> dict[str, Any]:
        surface = self._graph.risk_surface(cq.target_node)
        return {
            "query": f"RISK SURFACE OF {cq.target_node}",
            "result_type": "risk_surface",
            "source": cq.target_node,
            "total_at_risk": len(surface),
            "surface": surface,
            "hard_failures": [s for s in surface if s["dependency_type"] == "HARD_DEPENDENCY"],
            "soft_degradation": [s for s in surface if s["dependency_type"] == "WEAK_MODIFIER"],
        }

    def _simulate(self, cq: CQ) -> dict[str, Any]:
        sim = self._graph.simulate_removal(cq.target_node)
        return {
            "query": f"SIMULATE REMOVAL {cq.target_node}",
            "result_type": "simulation",
            **sim,
        }

    # ─── HELPERS ──────────────────────────────────────────────────────

    def _group_by_domain(self, node_ids: list[str]) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = {}
        for nid in node_ids:
            node = self._graph.get_node(nid)
            domain = node.domain.value if node else "UNKNOWN"
            groups.setdefault(domain, []).append(nid)
        return groups


# ═══════════════════════════════════════════════════════════════════════════════
# SINGLETON + DEFAULT GRAPH CONSTRUCTION
# ═══════════════════════════════════════════════════════════════════════════════

_engine: CausalEngine | None = None


def get_causal_engine() -> CausalEngine:
    """Get or create the singleton causal engine with the system's producer graph."""
    global _engine
    if _engine is None:
        graph = _build_system_graph()
        _engine = CausalEngine(graph)
    return _engine


def _build_system_graph() -> CausalGraph:
    """Construct the canonical causal graph from the producer registry."""
    g = CausalGraph()

    # ─── NODES ────────────────────────────────────────────────────────

    _nodes = [
        CausalNode("FEED.MARKET_OBSERVATION", Domain.MARKET, "MARKET_FEED", "Market Observation", "MARKET_DATA_INGESTION"),
        CausalNode("FEATURE.STATE_CHANGE", Domain.SIGNAL, "FEATURE_ENGINE", "Feature State Change", "FEATURE_COMPUTATION"),
        CausalNode("SIGNAL.PATTERN_DETECTED", Domain.SIGNAL, "SIGNAL_EVALUATION", "Pattern Detection", "PATTERN_DETECTION"),
        CausalNode("SIGNAL.PATTERN_CONFIRMED", Domain.SIGNAL, "SIGNAL_EVALUATION", "Pattern Confirmation", "PATTERN_DETECTION"),
        CausalNode("SIGNAL.BIAS_TRANSITION", Domain.SIGNAL, "SIGNAL_EVALUATION", "Bias Transition", "BIAS_DETERMINATION"),
        CausalNode("SIGNAL.CONFLUENCE_SCORE", Domain.SIGNAL, "SIGNAL_EVALUATION", "Confluence Score", "CONFLUENCE_SCORING"),
        CausalNode("RISK.DRAWDOWN_GUARD", Domain.RISK, "RISK_GUARDS", "Drawdown Guard", "RISK_CHECK"),
        CausalNode("RISK.DAILY_LOSS_GUARD", Domain.RISK, "RISK_GUARDS", "Daily Loss Guard", "RISK_CHECK"),
        CausalNode("RISK.STALE_DATA", Domain.RISK, "RISK_GUARDS", "Stale Data Monitor", "RISK_CHECK"),
        CausalNode("RISK.DAILY_TRADE_LIMIT", Domain.RISK, "RISK_GUARDS", "Daily Trade Limit", "RISK_CHECK"),
        CausalNode("RISK.TRADE_COOLDOWN", Domain.RISK, "RISK_GUARDS", "Trade Cooldown", "RISK_CHECK"),
        CausalNode("RISK.CORRELATION", Domain.RISK, "RISK_GUARDS", "Correlation Guard", "RISK_CHECK"),
        CausalNode("RISK.PORTFOLIO_EXPOSURE", Domain.RISK, "RISK_GUARDS", "Portfolio Exposure", "RISK_CHECK"),
        CausalNode("RISK.REGIME", Domain.RISK, "RISK_GUARDS", "Regime Guard", "RISK_CHECK"),
        CausalNode("RISK.CHALLENGE", Domain.RISK, "RISK_GUARDS", "Challenge Protection", "RISK_CHECK"),
        CausalNode("RISK.CONSISTENCY", Domain.RISK, "RISK_GUARDS", "Consistency Rules", "RISK_CHECK"),
        CausalNode("RISK.PROP_FIRM", Domain.RISK, "RISK_GUARDS", "Prop Firm Rules", "RISK_CHECK"),
        CausalNode("RISK.WEEKEND", Domain.RISK, "RISK_GUARDS", "Weekend Protection", "RISK_CHECK"),
        CausalNode("RISK.CONTROL_GATE", Domain.RISK, "RISK_GUARDS", "Control Gate", "RISK_CHECK"),
        CausalNode("DECISION.SPINE_CREATION", Domain.DECISION, "DECISION_ORCHESTRATOR", "Correlation Spine", "DECISION"),
        CausalNode("DECISION.CONTEXT_SNAPSHOT", Domain.DECISION, "DECISION_ORCHESTRATOR", "Execution Context", "DECISION"),
        CausalNode("DECISION.SHADOW_TRADE", Domain.DECISION, "DECISION_ORCHESTRATOR", "Shadow Trade Open", "DECISION"),
        CausalNode("DECISION.RECORD", Domain.DECISION, "DECISION_ORCHESTRATOR", "Decision Record", "DECISION"),
        CausalNode("EXEC.BROKER_FILL", Domain.EXECUTION, "BROKER_EXECUTION", "Broker Fill", "EXECUTION"),
        CausalNode("SHADOW.LIFECYCLE", Domain.OUTCOME, "SHADOW_TRADE_ENGINE", "Lifecycle Progression", "TRADE_MANAGEMENT"),
        CausalNode("POSITION.BREAKEVEN", Domain.POSITION, "POSITION_MANAGEMENT", "Break Even", "TRADE_MANAGEMENT"),
        CausalNode("POSITION.TRAILING", Domain.POSITION, "POSITION_MANAGEMENT", "Trailing Stop", "TRADE_MANAGEMENT"),
        CausalNode("POSITION.SL_MOVE", Domain.POSITION, "POSITION_MANAGEMENT", "Stop Loss Move", "TRADE_MANAGEMENT"),
        CausalNode("OUTCOME.PERSIST", Domain.OUTCOME, "OUTCOME_RECORDER", "Outcome Persistence", "OUTCOME_RECORDING"),
        CausalNode("GRAPH.NODE_CREATION", Domain.LEARNING, "RELATIONSHIP_GRAPH", "Graph Node", "POST_TRADE_ANALYSIS"),
        CausalNode("ATTR.DECOMPOSITION", Domain.LEARNING, "CAUSAL_ATTRIBUTION", "Causal Decomposition", "POST_TRADE_ANALYSIS"),
        CausalNode("EDGE.AGGREGATION", Domain.LEARNING, "EDGE_DISCOVERY", "Edge Aggregation", "POST_TRADE_ANALYSIS"),
        CausalNode("COMPILER.COMPILE", Domain.LEARNING, "STRATEGY_COMPILER", "Strategy Compilation", "POST_TRADE_ANALYSIS"),
    ]

    for node in _nodes:
        g.add_node(node)

    # ─── EDGES ────────────────────────────────────────────────────────

    _edges = [
        # MARKET → SIGNAL
        CausalEdge("FEED.MARKET_OBSERVATION", "FEATURE.STATE_CHANGE", EdgeType.HARD_DEPENDENCY, 1.0, "events/"),
        CausalEdge("FEED.MARKET_OBSERVATION", "SIGNAL.PATTERN_DETECTED", EdgeType.HARD_DEPENDENCY, 1.0, "events/"),
        CausalEdge("FEED.MARKET_OBSERVATION", "SIGNAL.BIAS_TRANSITION", EdgeType.HARD_DEPENDENCY, 1.0, "events/"),
        CausalEdge("FEED.MARKET_OBSERVATION", "RISK.STALE_DATA", EdgeType.HARD_DEPENDENCY, 1.0, "events/"),
        CausalEdge("FEED.MARKET_OBSERVATION", "SHADOW.LIFECYCLE", EdgeType.HARD_DEPENDENCY, 1.0, "events/"),
        CausalEdge("FEED.MARKET_OBSERVATION", "EXEC.BROKER_FILL", EdgeType.HARD_DEPENDENCY, 0.9, "events/"),

        # SIGNAL internal
        CausalEdge("FEATURE.STATE_CHANGE", "SIGNAL.CONFLUENCE_SCORE", EdgeType.WEAK_MODIFIER, 0.6, "", "volatility penalty + sweep bonus"),
        CausalEdge("FEATURE.STATE_CHANGE", "RISK.REGIME", EdgeType.WEAK_MODIFIER, 0.5, "", "ATR ratio for classification"),
        CausalEdge("SIGNAL.PATTERN_DETECTED", "SIGNAL.PATTERN_CONFIRMED", EdgeType.HARD_DEPENDENCY, 1.0),
        CausalEdge("SIGNAL.PATTERN_DETECTED", "SIGNAL.BIAS_TRANSITION", EdgeType.WEAK_MODIFIER, 0.7, "", "triggering signals"),
        CausalEdge("SIGNAL.PATTERN_DETECTED", "SIGNAL.CONFLUENCE_SCORE", EdgeType.HARD_DEPENDENCY, 0.9, "", "pattern type → base score"),
        CausalEdge("SIGNAL.PATTERN_CONFIRMED", "SIGNAL.CONFLUENCE_SCORE", EdgeType.WEAK_MODIFIER, 0.6, "", "confirmation strength"),
        CausalEdge("SIGNAL.BIAS_TRANSITION", "SIGNAL.CONFLUENCE_SCORE", EdgeType.HARD_DEPENDENCY, 1.0, "", "bias alignment required"),
        CausalEdge("SIGNAL.BIAS_TRANSITION", "DECISION.SHADOW_TRADE", EdgeType.HARD_DEPENDENCY, 1.0, "", "direction from bias"),

        # SIGNAL → RISK (score pass gates all risk checks)
        CausalEdge("SIGNAL.CONFLUENCE_SCORE", "DECISION.SPINE_CREATION", EdgeType.HARD_DEPENDENCY, 1.0),
        CausalEdge("SIGNAL.CONFLUENCE_SCORE", "RISK.DAILY_TRADE_LIMIT", EdgeType.HARD_DEPENDENCY, 0.8),
        CausalEdge("SIGNAL.CONFLUENCE_SCORE", "RISK.TRADE_COOLDOWN", EdgeType.HARD_DEPENDENCY, 0.8),
        CausalEdge("SIGNAL.CONFLUENCE_SCORE", "RISK.CORRELATION", EdgeType.HARD_DEPENDENCY, 0.8),
        CausalEdge("SIGNAL.CONFLUENCE_SCORE", "RISK.PORTFOLIO_EXPOSURE", EdgeType.HARD_DEPENDENCY, 0.8),
        CausalEdge("SIGNAL.CONFLUENCE_SCORE", "RISK.REGIME", EdgeType.HARD_DEPENDENCY, 0.8),
        CausalEdge("SIGNAL.CONFLUENCE_SCORE", "RISK.CHALLENGE", EdgeType.HARD_DEPENDENCY, 0.8),
        CausalEdge("SIGNAL.CONFLUENCE_SCORE", "RISK.CONSISTENCY", EdgeType.HARD_DEPENDENCY, 0.8),
        CausalEdge("SIGNAL.CONFLUENCE_SCORE", "RISK.PROP_FIRM", EdgeType.HARD_DEPENDENCY, 0.8),
        CausalEdge("SIGNAL.CONFLUENCE_SCORE", "RISK.WEEKEND", EdgeType.HARD_DEPENDENCY, 0.8),
        CausalEdge("SIGNAL.CONFLUENCE_SCORE", "RISK.CONTROL_GATE", EdgeType.HARD_DEPENDENCY, 0.8),

        # DECISION
        CausalEdge("DECISION.SPINE_CREATION", "DECISION.CONTEXT_SNAPSHOT", EdgeType.HARD_DEPENDENCY, 1.0),
        CausalEdge("DECISION.SPINE_CREATION", "DECISION.SHADOW_TRADE", EdgeType.HARD_DEPENDENCY, 1.0),
        CausalEdge("DECISION.SPINE_CREATION", "DECISION.RECORD", EdgeType.HARD_DEPENDENCY, 1.0),
        CausalEdge("DECISION.SHADOW_TRADE", "SHADOW.LIFECYCLE", EdgeType.HARD_DEPENDENCY, 1.0),
        CausalEdge("DECISION.RECORD", "EXEC.BROKER_FILL", EdgeType.HARD_DEPENDENCY, 0.9),

        # EXECUTION → POSITION
        CausalEdge("EXEC.BROKER_FILL", "POSITION.BREAKEVEN", EdgeType.HARD_DEPENDENCY, 1.0),
        CausalEdge("EXEC.BROKER_FILL", "POSITION.TRAILING", EdgeType.HARD_DEPENDENCY, 1.0),
        CausalEdge("EXEC.BROKER_FILL", "POSITION.SL_MOVE", EdgeType.HARD_DEPENDENCY, 1.0),

        # POSITION + SHADOW → OUTCOME
        CausalEdge("POSITION.BREAKEVEN", "OUTCOME.PERSIST", EdgeType.WEAK_MODIFIER, 0.7),
        CausalEdge("POSITION.TRAILING", "OUTCOME.PERSIST", EdgeType.WEAK_MODIFIER, 0.7),
        CausalEdge("POSITION.SL_MOVE", "OUTCOME.PERSIST", EdgeType.WEAK_MODIFIER, 0.7),
        CausalEdge("SHADOW.LIFECYCLE", "OUTCOME.PERSIST", EdgeType.HARD_DEPENDENCY, 1.0),

        # OUTCOME → LEARNING
        CausalEdge("OUTCOME.PERSIST", "GRAPH.NODE_CREATION", EdgeType.HARD_DEPENDENCY, 1.0),
        CausalEdge("OUTCOME.PERSIST", "RISK.DRAWDOWN_GUARD", EdgeType.WEAK_MODIFIER, 0.5, "", "drawdown computed from outcomes"),
        CausalEdge("OUTCOME.PERSIST", "RISK.DAILY_LOSS_GUARD", EdgeType.WEAK_MODIFIER, 0.5, "", "daily PnL from outcomes"),
        CausalEdge("GRAPH.NODE_CREATION", "ATTR.DECOMPOSITION", EdgeType.HARD_DEPENDENCY, 1.0),
        CausalEdge("ATTR.DECOMPOSITION", "EDGE.AGGREGATION", EdgeType.HARD_DEPENDENCY, 1.0),
        CausalEdge("EDGE.AGGREGATION", "COMPILER.COMPILE", EdgeType.HARD_DEPENDENCY, 1.0),

        # OBSERVATIONAL edges
        CausalEdge("FEED.MARKET_OBSERVATION", "DECISION.CONTEXT_SNAPSHOT", EdgeType.OBSERVATIONAL, 0.3),
        CausalEdge("RISK.DRAWDOWN_GUARD", "DECISION.CONTEXT_SNAPSHOT", EdgeType.OBSERVATIONAL, 0.2),
        CausalEdge("RISK.DAILY_LOSS_GUARD", "DECISION.CONTEXT_SNAPSHOT", EdgeType.OBSERVATIONAL, 0.2),
        CausalEdge("DECISION.CONTEXT_SNAPSHOT", "ATTR.DECOMPOSITION", EdgeType.OBSERVATIONAL, 0.4, "", "environment context for attribution"),
    ]

    # Separate feedback edge (Arc 2 — bounded, declared)
    _feedback_edge = CausalEdge("COMPILER.COMPILE", "SIGNAL.CONFLUENCE_SCORE", EdgeType.WEAK_MODIFIER, 0.3, "", "Arc 2: config thresholds")

    for edge in _edges:
        g.add_edge(edge)

    # Add feedback loop with explicit bypass (documented Arc 2 bounded loop)
    g.add_edge(_feedback_edge, allow_feedback=True)

    return g
