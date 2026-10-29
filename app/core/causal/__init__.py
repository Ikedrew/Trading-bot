"""
Causal Graph Query Engine — Lightweight causal reasoning for the trading system.

Answers:
    - Why did this trade happen? (TRACE)
    - What caused this signal? (BACKWARD IMPACT)
    - What breaks if I change this rule? (RISK SURFACE)
    - What does this decision influence? (FORWARD IMPACT)

Usage:
    from core.causal import get_causal_engine, CQ

    engine = get_causal_engine()
    result = engine.query("FORWARD IMPACT OF SIGNAL.CONFLUENCE_SCORE")
    result = engine.query("TRACE OUTCOME.PERSIST")
    result = engine.query("RISK SURFACE OF FEED.MARKET_OBSERVATION")
"""

from core.causal.graph import CausalGraph, CausalNode, CausalEdge, EdgeType, Domain
from core.causal.engine import CausalEngine, get_causal_engine
from core.causal.query import CQ
from core.causal.api import CausalAPI, get_causal_api

__all__ = [
    "CausalGraph",
    "CausalNode",
    "CausalEdge",
    "EdgeType",
    "Domain",
    "CausalEngine",
    "get_causal_engine",
    "CQ",
    "CausalAPI",
    "get_causal_api",
]
