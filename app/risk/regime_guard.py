"""
I2: Market Regime Guard — Hard execution gate based on market regime.

Blocks trade execution when the market is classified in an adverse regime
(e.g. VOLATILE, CHOPPY). This is a capital protection gate that prevents
trading in structurally unfavorable conditions.

Uses multi-signal regime classification:
- H4 regime from HTF system (primary, when available)
- M5 regime state from pipeline (fallback)
- ATR-based volatility expansion detection (supplementary)

This is a HARD BLOCK — cannot be overridden by strategy logic.
Controlled exclusively by config.BLOCKED_REGIMES.
"""

from __future__ import annotations

import logging
import time as _time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# ─── CONFIGURATION ───────────────────────────────────────────────────────────


def _is_enabled() -> bool:
    try:
        from core import config
        return bool(getattr(config, "REGIME_GUARD_ENABLED", True))
    except ImportError:
        return True


def _get_blocked_regimes() -> list[str]:
    try:
        from core import config
        regimes = getattr(config, "BLOCKED_REGIMES", ["VOLATILE", "CHOPPY"])
        if isinstance(regimes, (list, tuple)):
            return [str(r).upper() for r in regimes]
        return ["VOLATILE", "CHOPPY"]
    except ImportError:
        return ["VOLATILE", "CHOPPY"]


# ─── RESULT TYPE ──────────────────────────────────────────────────────────────

REJECT_REGIME_BLOCKED = "REGIME_BLOCKED"


@dataclass(frozen=True)
class RegimeGuardResult:
    """Result of regime guard evaluation."""
    allowed: bool
    reason: str = ""
    regime: str = ""
    confidence: float = 0.0
    source: str = ""  # "HTF" or "M5" — where classification came from


# ─── REGIME CLASSIFICATION ────────────────────────────────────────────────────

@dataclass(frozen=True)
class RegimeAssessment:
    """
    Multi-signal regime assessment combining HTF and M5 data.

    Represents the final regime classification used for the gate decision.
    """
    regime: str  # TRENDING, RANGING, VOLATILE, CHOPPY, TRANSITIONAL
    confidence: float  # 0.0–1.0
    source: str  # "HTF_H4", "M5_PIPELINE", "COMPOSITE"
    signals: dict  # debug info


def classify_regime(
    *,
    htf_context: Any | None = None,
    m5_regime_state: str = "",
    atr_ratio: float = 0.0,
    structure_score: float = 0.0,
) -> RegimeAssessment:
    """
    Multi-signal regime classification.

    Priority:
    1. H4 HTF regime (if available and populated) — most reliable
    2. Composite from M5 signals + ATR ratio (fallback)

    Args:
        htf_context: HTFContext from timeframe cache (may be None)
        m5_regime_state: Pipeline regime state ("TREND_UP", "TREND_DOWN", "RANGING")
        atr_ratio: Current ATR / average ATR (volatility expansion metric)
        structure_score: M15 structure quality score (0-1)

    Returns:
        RegimeAssessment with classification, confidence, and debug signals.
    """
    signals = {
        "m5_regime_state": m5_regime_state,
        "atr_ratio": round(atr_ratio, 3),
        "structure_score": round(structure_score, 3),
    }

    # ─── PRIMARY: HTF H4 Regime (most reliable) ───────────────────────
    if htf_context is not None:
        regime_snap = getattr(htf_context, "regime", None)
        if regime_snap is not None:
            h4_class = regime_snap.classification.value  # e.g. "VOLATILE", "RANGING"
            h4_confidence = regime_snap.confidence
            h4_atr_ratio = getattr(regime_snap, "atr_ratio", 0.0)

            signals["h4_classification"] = h4_class
            signals["h4_confidence"] = round(h4_confidence, 3)
            signals["h4_atr_ratio"] = round(h4_atr_ratio, 3)

            # Normalize H4 classifications to our gate vocabulary
            regime = _normalize_h4_regime(h4_class, h4_atr_ratio, structure_score)

            return RegimeAssessment(
                regime=regime,
                confidence=h4_confidence,
                source="HTF_H4",
                signals=signals,
            )

    # ─── FALLBACK: M5 + ATR composite ─────────────────────────────────
    regime = _classify_from_m5_signals(m5_regime_state, atr_ratio, structure_score)
    confidence = _compute_confidence(regime, atr_ratio, structure_score)

    signals["classification_source"] = "M5_COMPOSITE"

    return RegimeAssessment(
        regime=regime,
        confidence=confidence,
        source="M5_COMPOSITE",
        signals=signals,
    )


def _normalize_h4_regime(h4_class: str, atr_ratio: float, structure_score: float) -> str:
    """
    Normalize H4 classification to gate vocabulary.

    Maps: TRENDING_BULLISH/BEARISH → TRENDING
           RANGING → RANGING
           VOLATILE → VOLATILE
           TRANSITIONAL → check for CHOPPY conditions
    """
    if h4_class in ("TRENDING_BULLISH", "TRENDING_BEARISH"):
        return "TRENDING"
    if h4_class == "VOLATILE":
        return "VOLATILE"
    if h4_class == "RANGING":
        # Check for CHOPPY: ranging + low structure quality
        if structure_score > 0 and structure_score < 0.3:
            return "CHOPPY"
        return "RANGING"
    if h4_class == "TRANSITIONAL":
        # Transitional with high ATR = volatile
        if atr_ratio > 1.3:
            return "VOLATILE"
        # Transitional with low structure = choppy
        if structure_score > 0 and structure_score < 0.3:
            return "CHOPPY"
        return "TRANSITIONAL"
    return h4_class


def _classify_from_m5_signals(m5_regime: str, atr_ratio: float, structure_score: float) -> str:
    """
    Classify regime from M5 pipeline signals when HTF is unavailable.

    Uses ATR ratio (volatility expansion) and structure score as primary inputs.
    """
    # Volatile: ATR expansion > 1.5x average
    if atr_ratio > 1.5:
        return "VOLATILE"

    # Choppy: ranging market + poor structure quality
    if m5_regime == "RANGING" and structure_score > 0 and structure_score < 0.3:
        return "CHOPPY"

    # Choppy: moderate ATR but very poor structure
    if atr_ratio > 1.0 and structure_score > 0 and structure_score < 0.25:
        return "CHOPPY"

    # Trending: clear directional movement
    if m5_regime in ("TREND_UP", "TREND_DOWN"):
        return "TRENDING"

    # Default: ranging (not blocked by default)
    if m5_regime == "RANGING":
        return "RANGING"

    return "TRANSITIONAL"


def _compute_confidence(regime: str, atr_ratio: float, structure_score: float) -> float:
    """Compute confidence for M5-based classification."""
    if regime == "VOLATILE":
        return min(1.0, (atr_ratio - 1.0) / 1.5)
    if regime == "CHOPPY":
        return min(1.0, (1.0 - structure_score) * 0.8)
    if regime == "TRENDING":
        return 0.6  # Moderate confidence without H4 confirmation
    return 0.4  # Low confidence for RANGING/TRANSITIONAL


# ─── MAIN GUARD FUNCTION ──────────────────────────────────────────────────────

def check_regime(
    *,
    htf_context: Any | None = None,
    m5_regime_state: str = "",
    atr_ratio: float = 0.0,
    structure_score: float = 0.0,
    symbol: str = "",
) -> RegimeGuardResult:
    """
    Hard execution gate — blocks trades in adverse market regimes.

    Must be called BEFORE execution.place_market().
    Cannot be overridden by strategy logic.

    Args:
        htf_context: HTFContext from timeframe cache (may be None)
        m5_regime_state: Pipeline regime state from EngineState
        atr_ratio: Current ATR / rolling average ATR
        structure_score: M15 structure quality score (0-1)
        symbol: Trading symbol (for logging)

    Returns:
        RegimeGuardResult with allowed=False if regime is blocked.
    """
    if not _is_enabled():
        return RegimeGuardResult(allowed=True, reason="REGIME_GUARD_DISABLED")

    blocked_regimes = _get_blocked_regimes()
    if not blocked_regimes:
        return RegimeGuardResult(allowed=True, reason="NO_BLOCKED_REGIMES")

    # Classify current regime
    assessment = classify_regime(
        htf_context=htf_context,
        m5_regime_state=m5_regime_state,
        atr_ratio=atr_ratio,
        structure_score=structure_score,
    )

    # Check against blocked list
    if assessment.regime in blocked_regimes:
        logger.warning(
            "[REGIME_GUARD] Trade blocked — %s regime detected "
            "(confidence: %.2f) symbol=%s source=%s signals=%s",
            assessment.regime, assessment.confidence,
            symbol, assessment.source, assessment.signals,
        )
        return RegimeGuardResult(
            allowed=False,
            reason=REJECT_REGIME_BLOCKED,
            regime=assessment.regime,
            confidence=assessment.confidence,
            source=assessment.source,
        )

    # Allowed
    return RegimeGuardResult(
        allowed=True,
        reason="",
        regime=assessment.regime,
        confidence=assessment.confidence,
        source=assessment.source,
    )
