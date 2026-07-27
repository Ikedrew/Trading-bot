"""Uncertainty Engine — measures ambiguity without making decisions."""

from core.uncertainty.model import UncertaintyAssessment
from core.uncertainty.engine import compute_uncertainty

__all__ = ["UncertaintyAssessment", "compute_uncertainty"]
