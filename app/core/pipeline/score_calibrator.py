"""
Score Calibrator — Transforms raw composite score into calibrated probability.

PROMOTED (Q20): Empirical calibration from shadow trade outcomes.
Loads: analysis/artifacts/calibration/score_calibration_curve.json
Fallback: identity_v1 (calibrated = raw) if artifact unavailable.

Design: deterministic, stateless, safe fallback. Never crashes runtime.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_CALIBRATOR_VERSION = "empirical_v1"
_FALLBACK_VERSION = "identity_v1"
_ARTIFACT_PATH = Path("analysis/artifacts/calibration/score_calibration_curve.json")


@dataclass(frozen=True)
class CalibrationResult:
    """Output of score calibration."""
    raw_score: float
    calibrated_probability: float
    calibration_source: str = ""
    calibration_version: str = ""


def _load_curve() -> list[dict] | None:
    """Load empirical curve. Returns None on any failure. Never raises."""
    try:
        if not _ARTIFACT_PATH.exists():
            return None
        data = json.loads(_ARTIFACT_PATH.read_text(encoding="utf-8"))
        if data.get("version") != "calibration_v1":
            return None
        mapping = data.get("mapping")
        if not mapping or not isinstance(mapping, list):
            return None
        for e in mapping:
            if not all(k in e for k in ("score_min", "score_max", "probability")):
                return None
        return mapping
    except Exception:
        return None


class ScoreCalibrator:
    """
    Transforms raw score into calibrated probability.

    Promoted (empirical_v1): Uses Q20 calibration curve from research.
    Fallback (identity_v1): calibrated = raw_score if artifact unavailable.
    """

    def __init__(self) -> None:
        self._curve = _load_curve()
        self._version = _CALIBRATOR_VERSION if self._curve else _FALLBACK_VERSION

    def calibrate(self, raw_score: float) -> CalibrationResult:
        """Transform raw score into calibrated probability."""
        raw_clamped = max(0.0, min(1.0, raw_score))

        if self._curve:
            calibrated = self._lookup(raw_clamped)
        else:
            calibrated = raw_clamped

        return CalibrationResult(
            raw_score=round(raw_score, 4),
            calibrated_probability=round(calibrated, 4),
            calibration_source="ScoreCalibrator",
            calibration_version=self._version,
        )

    def _lookup(self, score: float) -> float:
        """Look up calibrated probability from empirical curve."""
        for e in self._curve:
            if e["score_min"] <= score < e["score_max"]:
                return e["probability"]
        # Below all buckets
        if score < self._curve[0]["score_min"]:
            return max(0.10, self._curve[0]["probability"] * 0.7)
        # Above all buckets
        if score >= self._curve[-1]["score_max"]:
            highest = self._curve[-1]["probability"]
            return min(0.85, highest + (score - self._curve[-1]["score_max"]) * 0.5)
        return score  # gap fallback

    @property
    def version(self) -> str:
        return self._version

    @property
    def is_empirical(self) -> bool:
        return self._curve is not None


_calibrator: ScoreCalibrator | None = None


def get_score_calibrator() -> ScoreCalibrator:
    """Get or create singleton score calibrator."""
    global _calibrator
    if _calibrator is None:
        _calibrator = ScoreCalibrator()
    return _calibrator
