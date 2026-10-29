"""
Lifecycle Join Framework — Reconstructs complete trade lifecycle from disparate datasets.

Joins: Opportunity → Assessment → Ranking → Decision → Execution → Trade Truth

This module is PURELY RESEARCH. It does NOT:
    - Affect trading decisions
    - Modify runtime behaviour
    - Write to any persistence layer
"""

from research_engine.joins.lifecycle_join import (
    join_lifecycle,
    LifecycleRecord,
    LifecycleQuality,
)

__all__ = [
    "join_lifecycle",
    "LifecycleRecord",
    "LifecycleQuality",
]
