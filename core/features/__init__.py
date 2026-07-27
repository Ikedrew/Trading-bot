"""
Feature Engineering Layer — Pure market-derived signal computation.

This layer sits between State Preparation and Snapshot creation.
It computes ONLY market-derived features from candle data and tick prices.
It NEVER reads FSM state, EngineState counters, or scoring outputs.

Architecture:
  State Prep (FSM) → Feature Engine → StateSnapshot → Voters → Decision → Delta
"""

from core.features.bundle import FeatureBundle
from core.features.engine import compute_features

__all__ = ["FeatureBundle", "compute_features"]
