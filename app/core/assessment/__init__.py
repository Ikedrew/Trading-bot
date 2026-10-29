"""
Assessment Dataset — First-class persistence layer for opportunity evaluation.

Answers: "How good is this Opportunity?"
Does NOT answer: "Should we trade it?" (that belongs to the Decision layer).

Assessment captures:
    - Scoring (10-factor breakdown, composite score)
    - Probability estimation (p_success, model version)
    - Expected value (EV, reward, risk)
    - Strategy classification (type, confidence)
    - Uncertainty quantification
    - Reasoning narrative
    - Evidence attribution

Assessment NEVER contains:
    - Trade approval decision (should_trade, block_reason)
    - Position sizing (volume, lot)
    - Risk levels (SL, TP)
    - Broker execution parameters
    - Account state

Dataset Ownership:
    Producer: core/assessment/builder.py (called from live_scanner after engine evaluation)
    Persistence: core/assessment/persistence.py (local JSONL + S3 mirror)
    Consumer: Research Engine, Portfolio Intelligence, Strategy Compiler
"""

from core.assessment.assessment import (
    Assessment,
    SCHEMA_VERSION,
    DATASET_VERSION,
)
from core.assessment.builder import build_assessment
from core.assessment.persistence import persist_assessment

__all__ = [
    "Assessment",
    "SCHEMA_VERSION",
    "DATASET_VERSION",
    "build_assessment",
    "persist_assessment",
]
