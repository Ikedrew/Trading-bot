"""
Structure Confidence Modifier — Soft execution amplifier/suppressor.

Converts structure_score and structure_regime into a confidence multiplier
that adjusts execution probability WITHOUT modifying FSM gating logic.

FSM remains the hard gate authority.
This layer is a downstream confidence modifier only.

Ownership: core/pipeline/structure_confidence.py
Mutability: NONE (pure function)
Dependencies: structure_score + structure_regime from EngineState only

RULES (locked logic):
  - Pure function
  - No EngineState access
  - No side effects
  - modifier = score_band_multiplier × regime_multiplier
  - Clamp: min 0.50, max 1.25
"""

from __future__ import annotations


def compute_structure_modifier(structure_score: float, structure_regime: str) -> float:
    """
    Compute execution confidence modifier from structure scoring system.

    Returns a multiplier (0.50–1.25 range) that adjusts downstream confidence.
    Does NOT gate or block — only amplifies or suppresses.

    Args:
        structure_score: Rolling weighted structure score (from structure_scoring.py)
        structure_regime: Derived regime (WEAK/BUILDING/CONFIRMED/INVALID)

    Returns:
        Confidence multiplier (float). Values:
          < 1.0 = suppression (reduce execution likelihood)
          = 1.0 = neutral
          > 1.0 = amplification (increase execution likelihood)
    """
    # Score-based modifier (monotonic bands)
    if structure_score < 1.5:
        score_factor = 0.70
    elif structure_score < 3.0:
        score_factor = 0.90
    elif structure_score < 4.5:
        score_factor = 1.05
    else:
        score_factor = 1.15

    # Regime-based secondary adjustment
    regime_factors = {
        "WEAK": 0.85,
        "BUILDING": 1.00,
        "CONFIRMED": 1.10,
        "INVALID": 0.60,
    }
    regime_factor = regime_factors.get(structure_regime, 1.00)

    # Combined modifier (multiplicative)
    modifier = score_factor * regime_factor

    # Clamp to safe bounds (never below 0.50, never above 1.25)
    return round(max(0.50, min(1.25, modifier)), 3)
