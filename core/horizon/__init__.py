"""
Trade Horizon Intelligence — Observation-mode horizon classification.

Evaluates which trade horizons are plausible for each detected opportunity.
Does NOT change execution behaviour. Purely observational.

Answers: "What horizon characteristics does this opportunity contain?"
Does NOT answer: "Should we trade a different horizon?" (that belongs to execution policy).
"""

from core.horizon.horizon_models import TradeHorizon, HorizonAssessment
from core.horizon.horizon_classifier import classify_horizons
from core.horizon.horizon_execution_profile import HorizonExecutionProfile
from core.horizon.horizon_manager import get_horizon_manager, HorizonManager
from core.horizon.execution_authority import HorizonExecutionAuthority, HorizonPermission

__all__ = [
    "TradeHorizon",
    "HorizonAssessment",
    "classify_horizons",
    "HorizonExecutionProfile",
    "HorizonManager",
    "get_horizon_manager",
    "HorizonExecutionAuthority",
    "HorizonPermission",
]
