"""
Confidence Model — Validator certainty classification for contract violations.

Confidence is INDEPENDENT of Severity:
    Severity:   "How serious would this problem be if it is real?"
    Confidence: "How certain is the validator that this is actually a real problem?"

These are orthogonal axes. A violation can be:
    - CRITICAL severity + 100% confidence (deterministic schema failure)
    - WARNING severity + 45% confidence (suspicious but possibly valid behaviour)
    - ERROR severity + 90% confidence (likely genuine, edge cases possible)

CONFIDENCE SCALE:
    VERY_LOW   =  0–20%  (speculative, may be noise)
    LOW        = 21–40%  (uncertain, needs human review)
    MEDIUM     = 41–60%  (plausible, some ambiguity)
    HIGH       = 61–80%  (likely genuine violation)
    VERY_HIGH  = 81–100% (near-certain or deterministic)

FUTURE COMPATIBILITY:
    Supports both deterministic validators (always 100%) and future
    probabilistic validators (anomaly detection, drift scoring, ML-based).

Usage:
    from core.contracts.confidence import Confidence, classify_confidence

    # Numeric confidence
    violation = ContractViolation(..., confidence=100)

    # Classification
    level = classify_confidence(65)  # → Confidence.HIGH
"""

from __future__ import annotations

from enum import Enum


class Confidence(str, Enum):
    """
    Confidence level classification for contract violations.

    Represents the validator's certainty that a detected violation
    is genuinely a real problem (not a false positive).
    """

    VERY_LOW = "VERY_LOW"       #  0–20%: Speculative, may be noise
    LOW = "LOW"                 # 21–40%: Uncertain, needs review
    MEDIUM = "MEDIUM"           # 41–60%: Plausible, some ambiguity
    HIGH = "HIGH"               # 61–80%: Likely genuine
    VERY_HIGH = "VERY_HIGH"     # 81–100%: Near-certain or deterministic


def classify_confidence(value: int | float) -> Confidence:
    """
    Classify a numeric confidence value (0–100) into a confidence level.

    Args:
        value: Confidence percentage (0–100). Clamped to bounds.

    Returns:
        Confidence enum level.
    """
    value = max(0, min(100, value))

    if value <= 20:
        return Confidence.VERY_LOW
    elif value <= 40:
        return Confidence.LOW
    elif value <= 60:
        return Confidence.MEDIUM
    elif value <= 80:
        return Confidence.HIGH
    else:
        return Confidence.VERY_HIGH


def confidence_to_numeric(level: Confidence) -> int:
    """
    Convert a confidence level to its midpoint numeric value.

    Useful for aggregation and comparison.
    """
    midpoints = {
        Confidence.VERY_LOW: 10,
        Confidence.LOW: 30,
        Confidence.MEDIUM: 50,
        Confidence.HIGH: 70,
        Confidence.VERY_HIGH: 90,
    }
    return midpoints.get(level, 50)
