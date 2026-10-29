"""Evidence Attribution — decomposes scores into contributing evidence."""

from core.attribution.model import EvidenceContribution, ScoreAttribution
from core.attribution.engine import compute_attribution

__all__ = ["EvidenceContribution", "ScoreAttribution", "compute_attribution"]
