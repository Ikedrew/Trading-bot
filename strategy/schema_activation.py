"""
Strategy Activation Schema — Output contracts for the 1.2/1.3 activation pipeline.

All modules in the activation system use these frozen data structures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RegimeOutput:
    """Output of regime classification (non-decisional)."""
    regime: str                     # TRENDING / RANGE / TRANSITIONAL
    regime_confidence: float        # 0.0–1.0
    volatility_state: str           # LOW / MEDIUM / HIGH
    structure_state: str            # EXPANDING / CONTRACTING / BROKEN / ORDERLY
    trend_strength: float           # 0.0–1.0
    range_quality: float            # 0.0–1.0
    noise_index: float              # 0.0–1.0
    liquidity_condition: str        # CLEAN / CHOPPY / MANIPULATED
    session_context: str            # ASIAN / LONDON / NY / OVERLAP / OFF_SESSION
    notes: str = ""


@dataclass(frozen=True)
class StrategyCandidate:
    """One evaluated strategy with activation status."""
    strategy: str                   # REVERSAL / FALSE_BREAK / CONTINUATION
    allowed: bool
    activation_weight: float        # 0.0–1.0
    confidence: float               # 0.0–1.0
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class RejectedStrategy:
    """Record of why a strategy was disallowed."""
    strategy: str
    reason: str
    stage: str                      # ELIGIBILITY / MAPPING / GATING / SELECTION


@dataclass(frozen=True)
class ActivationResult:
    """Complete output of the strategy activation pipeline (1.3)."""
    regime: str
    regime_confidence: float
    eligible_strategies: tuple[str, ...]         # Passed eligibility matrix
    mapped_strategies: tuple[str, ...]           # Pattern produced candidates
    gated_strategies: tuple[str, ...]            # Passed hard gate validation
    strategy_candidates: tuple[StrategyCandidate, ...]
    selected_strategy: str | None
    selected_weight: float
    rejected_strategies: tuple[RejectedStrategy, ...]
    raw_pressure: dict[str, float]               # Before eligibility (pure pattern intent)
    final_pressure: dict[str, float]             # After eligibility + gating + modulation
    context_state: dict[str, Any]
