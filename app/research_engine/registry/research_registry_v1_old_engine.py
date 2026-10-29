"""
═══════════════════════════════════════════════════════════════════════════════
FROZEN REGISTRY — OLD ENGINE (v1)
═══════════════════════════════════════════════════════════════════════════════

STATUS: ARCHIVED / FROZEN

This registry belongs to the previous engine architecture and is frozen.
New research questions should be added to the V10 registry:
    research_engine/registry/v10_research_registry.py

DO NOT:
    - Add new questions here
    - Modify existing questions
    - Rename IDs
    - Delete entries
    - Use this for new V10 research

This file preserves:
    - 55 original research questions (E1-E5, M1-M11, D1-D6, S1-S7, X1-X6, R1-R5, L1-L7, G1-G3, P1, EX1-EX10)
    - Historical experiment definitions
    - Legacy Q1-Q25 ID mappings
    - Original validation rules and data source requirements

The canonical source for these definitions remains:
    research_engine/registry/research_question_registry.py

This freeze file exists purely as a named reference for the migration report.

Migration to V10 registry: 2026-08-05
Frozen by: Data Trustworthiness Audit Phase 1
═══════════════════════════════════════════════════════════════════════════════
"""

# Re-export the full original registry for reference
from research_engine.registry.research_question_registry import (
    REGISTRY as OLD_ENGINE_REGISTRY,
    REGISTRY_BY_ID as OLD_ENGINE_REGISTRY_BY_ID,
)

__all__ = ["OLD_ENGINE_REGISTRY", "OLD_ENGINE_REGISTRY_BY_ID"]

# Total questions in frozen registry: 55
FROZEN_QUESTION_COUNT = 55
FROZEN_DATE = "2026-08-05"
FROZEN_REASON = "V10 architecture migration — old engine questions preserved for reference"
