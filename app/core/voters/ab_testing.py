"""
A/B Testing Layer — Phase 4.

Compares production decisions against shadow voter pipeline decisions.
Computes Shadow Superiority Index (SSI) and production readiness gate.

STRICTLY observational. NEVER modifies production pipeline.
NEVER switches execution authority. NEVER applies weight changes.

Ownership: core/voters/ab_testing.py
Mutability: Rolling SSI history (internal only)
Dependencies: ConfluenceDecision, WeightIntelligenceBlock (read-only artifacts)
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

_SSI_WINDOW = 50  # Rolling window for SSI computation


# ─── DIVERGENCE CLASSIFICATION ────────────────────────────────────────────────

def classify_ab_divergence(
    production_action: str,
    shadow_action: str,
) -> Literal["match", "conservative_shadow", "aggressive_shadow", "directional_conflict"]:
    """
    Classify divergence between production and shadow decisions.
    """
    if production_action == shadow_action:
        return "match"

    # Directional conflict: opposite trade directions
    if (production_action == "BUY" and shadow_action == "SELL") or \
       (production_action == "SELL" and shadow_action == "BUY"):
        return "directional_conflict"

    # Shadow takes fewer trades (more NO_TRADE)
    if shadow_action == "NO_TRADE" and production_action in ("BUY", "SELL"):
        return "conservative_shadow"

    # Shadow takes more trades (production is NO_TRADE)
    if production_action == "NO_TRADE" and shadow_action in ("BUY", "SELL"):
        return "aggressive_shadow"

    return "match"


# ─── SSI TRACKER ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ABTestResult:
    """Per-cycle A/B comparison output."""
    production_action: str
    shadow_action: str
    divergence_type: str
    ssi_score: float
    ssi_trend: Literal["improving", "degrading", "stable"]
    readiness_flag: bool
    readiness_confidence: float
    notes: str


class SSITracker:
    """
    Shadow Superiority Index — rolling metric tracking how often
    shadow decisions would have improved outcomes vs production.

    Scoring heuristic (without actual trade outcomes):
      - match: neutral (0.5)
      - conservative_shadow: slight positive (0.6) — reducing noise is usually good
      - aggressive_shadow: slight negative (0.4) — adding trades without proof is risky
      - directional_conflict: negative (0.2) — fundamental disagreement is concerning
    """

    def __init__(self) -> None:
        self._history: deque[float] = deque(maxlen=_SSI_WINDOW)

    def record(self, divergence_type: str) -> None:
        """Record a divergence observation."""
        scores = {
            "match": 0.6,
            "conservative_shadow": 0.7,
            "aggressive_shadow": 0.4,
            "directional_conflict": 0.2,
        }
        self._history.append(scores.get(divergence_type, 0.5))

    @property
    def score(self) -> float:
        """Current SSI score (0.0–1.0)."""
        if not self._history:
            return 0.5
        return round(sum(self._history) / len(self._history), 3)

    @property
    def trend(self) -> Literal["improving", "degrading", "stable"]:
        """SSI trend over recent history."""
        if len(self._history) < 10:
            return "stable"
        recent = list(self._history)
        first_half = recent[:len(recent) // 2]
        second_half = recent[len(recent) // 2:]
        avg_first = sum(first_half) / len(first_half)
        avg_second = sum(second_half) / len(second_half)
        delta = avg_second - avg_first
        if delta > 0.03:
            return "improving"
        elif delta < -0.03:
            return "degrading"
        return "stable"

    def reset(self) -> None:
        self._history.clear()


# ─── READINESS GATE ───────────────────────────────────────────────────────────

def _evaluate_readiness(
    ssi_score: float,
    ssi_trend: str,
    system_state: str,
    conflict_severity: str,
) -> tuple[bool, float]:
    """
    Evaluate production switch readiness (NON-ACTUATING).

    Conditions for ready=True:
      - SSI > 0.60
      - SSI trend not degrading
      - system_state is coherent or tensioned
      - conflict severity not high

    Returns (readiness_flag, readiness_confidence).
    """
    conditions_met = 0
    total_conditions = 4

    if ssi_score > 0.60:
        conditions_met += 1
    if ssi_trend != "degrading":
        conditions_met += 1
    if system_state in ("coherent", "tensioned"):
        conditions_met += 1
    if conflict_severity in ("none", "low"):
        conditions_met += 1

    confidence = conditions_met / total_conditions
    ready = conditions_met == total_conditions

    return ready, round(confidence, 2)


# ─── MODULE SINGLETON ─────────────────────────────────────────────────────────

ssi_tracker = SSITracker()


# ─── MAIN ENTRY POINT ─────────────────────────────────────────────────────────

def compute_ab_test(
    *,
    production_action: str,
    shadow_action: str,
    system_state: str = "coherent",
    conflict_severity: str = "none",
) -> ABTestResult:
    """
    Compute A/B test comparison for this decision cycle.

    Pure observational output. NEVER influences execution.
    """
    divergence = classify_ab_divergence(production_action, shadow_action)
    ssi_tracker.record(divergence)

    ssi_score = ssi_tracker.score
    ssi_trend = ssi_tracker.trend

    ready, readiness_conf = _evaluate_readiness(
        ssi_score, ssi_trend, system_state, conflict_severity,
    )

    # Notes
    if divergence == "match":
        notes = "systems agree"
    elif divergence == "conservative_shadow":
        notes = "shadow filtering low-quality entries"
    elif divergence == "aggressive_shadow":
        notes = "shadow taking trades production rejects"
    else:
        notes = "fundamental directional disagreement"

    return ABTestResult(
        production_action=production_action,
        shadow_action=shadow_action,
        divergence_type=divergence,
        ssi_score=ssi_score,
        ssi_trend=ssi_trend,
        readiness_flag=ready,
        readiness_confidence=readiness_conf,
        notes=notes,
    )


def emit_ab_test_log(symbol: str, result: ABTestResult) -> None:
    """Emit structured A/B test log. Never raises."""
    try:
        logger.debug(
            "[AB_TEST] symbol=%s production=%s shadow=%s divergence=%s "
            "SSI=%.3f %s readiness=%s(%.2f) notes=%s",
            symbol,
            result.production_action,
            result.shadow_action,
            result.divergence_type,
            result.ssi_score,
            result.ssi_trend,
            result.readiness_flag,
            result.readiness_confidence,
            result.notes,
        )
    except Exception:
        pass
