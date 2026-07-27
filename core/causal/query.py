"""
CQ-QL Query Parser — Causal Query Language for the trading system.

Supported queries:
    FIND NODES WHERE domain = SIGNAL
    FIND NODES WHERE component = RISK_GUARDS
    FORWARD IMPACT OF <node_id>
    BACKWARD IMPACT OF <node_id>
    TRACE <node_id>
    RISK SURFACE OF <node_id>
    SIMULATE CHANGE <node_id>
    SIMULATE REMOVAL <node_id>
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class QueryType(str, Enum):
    FIND_NODES = "FIND_NODES"
    FORWARD_IMPACT = "FORWARD_IMPACT"
    BACKWARD_IMPACT = "BACKWARD_IMPACT"
    TRACE = "TRACE"
    RISK_SURFACE = "RISK_SURFACE"
    SIMULATE_CHANGE = "SIMULATE_CHANGE"
    SIMULATE_REMOVAL = "SIMULATE_REMOVAL"


@dataclass
class CQ:
    """Parsed CQ-QL query."""
    query_type: QueryType
    target_node: str = ""
    filters: dict[str, str] = None

    def __post_init__(self):
        if self.filters is None:
            self.filters = {}


def parse_query(query_str: str) -> CQ:
    """
    Parse a CQ-QL query string into a structured query object.

    Examples:
        "FIND NODES WHERE domain = SIGNAL"
        "FORWARD IMPACT OF SIGNAL.CONFLUENCE_SCORE"
        "BACKWARD IMPACT OF OUTCOME.PERSIST"
        "TRACE DECISION.SHADOW_TRADE"
        "RISK SURFACE OF FEED.MARKET_OBSERVATION"
        "SIMULATE REMOVAL SIGNAL.BIAS_TRANSITION"
    """
    q = query_str.strip().upper()

    # FIND NODES WHERE field = value
    if q.startswith("FIND NODES WHERE"):
        rest = query_str[len("FIND NODES WHERE"):].strip()
        filters = {}
        for part in rest.split(" AND "):
            part = part.strip()
            if "=" in part:
                key, val = part.split("=", 1)
                filters[key.strip().lower()] = val.strip().strip("'\"")
        return CQ(query_type=QueryType.FIND_NODES, filters=filters)

    # FORWARD IMPACT OF <node>
    if q.startswith("FORWARD IMPACT OF"):
        target = query_str[len("FORWARD IMPACT OF"):].strip()
        return CQ(query_type=QueryType.FORWARD_IMPACT, target_node=target)

    # BACKWARD IMPACT OF <node>
    if q.startswith("BACKWARD IMPACT OF"):
        target = query_str[len("BACKWARD IMPACT OF"):].strip()
        return CQ(query_type=QueryType.BACKWARD_IMPACT, target_node=target)

    # TRACE <node>
    if q.startswith("TRACE"):
        target = query_str[len("TRACE"):].strip()
        return CQ(query_type=QueryType.TRACE, target_node=target)

    # RISK SURFACE OF <node>
    if q.startswith("RISK SURFACE OF"):
        target = query_str[len("RISK SURFACE OF"):].strip()
        return CQ(query_type=QueryType.RISK_SURFACE, target_node=target)

    # SIMULATE REMOVAL <node>
    if q.startswith("SIMULATE REMOVAL") or q.startswith("SIMULATE CHANGE"):
        keyword = "SIMULATE REMOVAL" if q.startswith("SIMULATE REMOVAL") else "SIMULATE CHANGE"
        target = query_str[len(keyword):].strip()
        qtype = QueryType.SIMULATE_REMOVAL if "REMOVAL" in keyword else QueryType.SIMULATE_CHANGE
        return CQ(query_type=qtype, target_node=target)

    raise ValueError(f"Unknown CQ-QL query: {query_str}")
