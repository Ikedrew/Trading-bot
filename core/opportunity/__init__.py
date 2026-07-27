"""
Opportunity Layer — Shadow market intelligence capture.

Records every market candidate the system detects, regardless of whether
the decision system approves or rejects it. Purely observational.

This module does NOT influence:
    - Trade execution
    - Risk decisions
    - Score thresholds
    - Position sizing
    - Entry/exit rules
"""

from core.opportunity.opportunity import (
    Opportunity,
    OpportunityState,
    SCHEMA_VERSION,
    DATASET_VERSION,
)
from core.opportunity.factory import create_opportunity
from core.opportunity.persistence import persist_opportunity

__all__ = [
    "Opportunity",
    "OpportunityState",
    "SCHEMA_VERSION",
    "DATASET_VERSION",
    "create_opportunity",
    "persist_opportunity",
]
