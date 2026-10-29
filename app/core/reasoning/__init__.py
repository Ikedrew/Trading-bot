"""Reasoning Engine — explains WHY opportunities exist. Never decides."""

from core.reasoning.model import DecisionReasoning
from core.reasoning.engine import generate_reasoning

__all__ = ["DecisionReasoning", "generate_reasoning"]
