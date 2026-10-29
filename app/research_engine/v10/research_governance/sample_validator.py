"""
Research Governance — Sample Validator.

Determines whether a research result has enough evidence to draw conclusions.
"""

from __future__ import annotations

from typing import Any


# Default thresholds (configurable)
_DEFAULT_THRESHOLDS = {
    "insufficient": 10,  # < 10 trades
    "limited": 30,       # 10-30 trades
    "valid": 30,         # > 30 trades
}


class SampleValidator:
    """
    Validates sample size for research conclusions.

    Statuses:
        VALID       — sufficient evidence (>30 trades)
        LIMITED     — evidence exists but limited (10-30)
        INSUFFICIENT — cannot draw conclusions (<10)
    """

    def __init__(self, thresholds: dict[str, int] | None = None):
        t = thresholds or _DEFAULT_THRESHOLDS
        self._insufficient_max = t.get("insufficient", 10)
        self._valid_min = t.get("valid", 30)

    def validate(self, sample_size: int) -> dict[str, Any]:
        """
        Validate a sample size.

        Returns:
            {"status": str, "sample_size": int, "minimum_required": int, "confidence": str}
        """
        if sample_size < self._insufficient_max:
            return {
                "status": "INSUFFICIENT",
                "sample_size": sample_size,
                "minimum_required": self._insufficient_max,
                "confidence": "LOW",
            }
        elif sample_size < self._valid_min:
            return {
                "status": "LIMITED",
                "sample_size": sample_size,
                "minimum_required": self._valid_min,
                "confidence": "MEDIUM",
            }
        else:
            return {
                "status": "VALID",
                "sample_size": sample_size,
                "minimum_required": self._valid_min,
                "confidence": "HIGH",
            }
