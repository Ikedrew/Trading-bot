"""Bias age window label and dynamic confluence threshold (unchanged formulae)."""

from __future__ import annotations


def bias_window_phase(bias_age_seconds: float, expiry_seconds: float) -> str:
    if expiry_seconds <= 0:
        return "early"
    progress = max(0.0, min(1.0, bias_age_seconds / expiry_seconds))
    if progress < 0.25:
        return "early"
    if progress < 0.75:
        return "optimal"
    return "late"


def dynamic_confluence_threshold(
    base_threshold: float,
    bias_age_seconds: float,
    expiry_seconds: float,
) -> float:
    if expiry_seconds <= 0:
        return base_threshold
    progress = max(0.0, min(1.0, bias_age_seconds / expiry_seconds))
    strictness_offset = abs(progress - 0.5) * 1.6
    return base_threshold + strictness_offset
