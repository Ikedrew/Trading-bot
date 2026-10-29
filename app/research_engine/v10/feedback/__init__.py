"""
Research → System Feedback Loop.

Transforms completed research findings into governed feedback artifacts
that identify system strengths, weaknesses, opportunities, and uncertainties.

This is observational infrastructure. It NEVER modifies the trading bot.

Components:
    - model: ResearchFeedback data structure
    - generator: Deterministic feedback generation from findings
    - persistence: Feedback artifact storage
"""

from research_engine.v10.feedback.model import (
    FeedbackType,
    SystemArea,
    ResearchFeedback,
)
from research_engine.v10.feedback.generator import FeedbackGenerator

__all__ = [
    "FeedbackType",
    "SystemArea",
    "ResearchFeedback",
    "FeedbackGenerator",
]
