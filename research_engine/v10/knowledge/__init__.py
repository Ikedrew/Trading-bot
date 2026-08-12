"""
Persistent Evidence / Knowledge State.

Maintains the system's accumulated research understanding across runs.

Distinguishes:
    RAW EVIDENCE → FINDING → FEEDBACK → KNOWLEDGE

Knowledge is:
    - Persistent (survives restarts)
    - Evidence-backed (traceable to findings)
    - Versioned (historical states recoverable)
    - Contradiction-aware (supporting + contradicting evidence)
    - Reconstructable (from persisted findings/feedback)
    - Governed (cannot modify trading behaviour)
"""

from research_engine.v10.knowledge.model import (
    KnowledgeStatus,
    KnowledgeItem,
)
from research_engine.v10.knowledge.engine import KnowledgeEngine
from research_engine.v10.knowledge.store import KnowledgeStore

__all__ = [
    "KnowledgeStatus",
    "KnowledgeItem",
    "KnowledgeEngine",
    "KnowledgeStore",
]
