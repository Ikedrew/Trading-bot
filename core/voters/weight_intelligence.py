"""
Weight Intelligence Layer — Phase 3.5.

Produces observational weight recommendations based on Phase 3 intelligence outputs.
STRICTLY read-only. NEVER modifies runtime weights, confluence, or execution.

Architecture:
  Phase 3 Intelligence → WeightSignalExtractor → WeightStabilityController → WeightRecommendationEngine
  → WEIGHT_INTELLIGENCE_BLOCK (frozen output artifact for Phase 4 consumption)

Ownership: core/voters/weight_intelligence.py
Mutability: Rolling smoothing window (internal only, never affects decisions)
Dependencies: Phase 3 outputs (influence, reliability, conflict, agreement) only
"""

from __future__ import annotations

import logging
import time as _time
from collections import deque
from dataclasses import dataclass, field
from typing import Literal

from core.voters.confluence_engine import DEFAULT_WEIGHTS
from core.voters.influence_tracker import VoterInfluenceSnapshot, VoterReliabilitySnapshot, VOTER_NAMES
from core.voters.conflict_classification import ConflictAnalysis
from core.voters.agreement_analysis import AgreementAnalysis

logger = logging.getLogger(__name__)

# ─── CONSTANTS ────────────────────────────────────────────────────────────────

_MAX_DELTA = 0.05          # Maximum weight change per cycle
_SMOOTHING_WINDOW = 20     # Rolling window for delta smoothing
_MIN_WEIGHT = 0.05         # No voter weight below this
_MAX_WEIGHT = 0.50         # No voter weight above this


# ─── OUTPUT CONTRACT ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class WeightIntelligenceBlock:
    """
    Fixed-schema output artifact consumed by Phase 4.
    Phase 4 NEVER needs to know how this was computed — only reads it.
    """
    timestamp: float
    voter_weights_current: dict[str, float]
    voter_weights_recommended: dict[str, float]
    weight_deltas: dict[str, float]
    weight_health_score: float          # 0.0–1.0
    confidence: float                   # 0.0–1.0
    stability_guard_applied: bool
    reasoning_tags: list[str]


# ─── A. WEIGHT SIGNAL EXTRACTOR ───────────────────────────────────────────────

def _extract_raw_signals(
    influence: VoterInfluenceSnapshot,
    reliability: VoterReliabilitySnapshot,
    conflict: ConflictAnalysis,
    agreement: AgreementAnalysis,
) -> dict[str, float]:
    """
    Extract raw weight adjustment signals per voter from Phase 3 metrics.

    Rules:
      - High reliability → positive signal
      - High conflict participation → negative signal
      - High influence + low reliability → dampened (negative)
      - Agreement alignment → slight positive
    """
    signals: dict[str, float] = {}

    for name in VOTER_NAMES:
        signal = 0.0

        # Reliability contribution (+)
        rel = reliability.reliability_scores.get(name, 0.5)
        signal += (rel - 0.5) * 0.1  # ±0.05 max from reliability

        # Conflict participation (-)
        if name in conflict.conflict_map:
            conflict_count = len(conflict.conflict_map[name])
            signal -= conflict_count * 0.02  # -0.02 per conflict involvement

        # Influence + reliability mismatch (dampening)
        inf_mag = abs(influence.influence_map.get(name, 0.0))
        if inf_mag > 0.5 and rel < 0.5:
            signal -= 0.03  # High influence but unreliable → dampen

        # Agreement alignment bonus
        if name in agreement.dominant_voters:
            signal += 0.01

        signals[name] = round(signal, 4)

    return signals


# ─── B. WEIGHT STABILITY CONTROLLER ──────────────────────────────────────────

class WeightStabilityController:
    """
    Prevents noisy/volatile weight recommendations.
    Applies smoothing and max-delta constraints.
    """

    def __init__(self) -> None:
        self._history: dict[str, deque[float]] = {
            name: deque(maxlen=_SMOOTHING_WINDOW) for name in VOTER_NAMES
        }

    def stabilise(self, raw_signals: dict[str, float]) -> tuple[dict[str, float], bool]:
        """
        Apply stability constraints to raw signals.
        Returns (stabilised_deltas, guard_applied).
        """
        guard_applied = False
        stabilised: dict[str, float] = {}

        for name in VOTER_NAMES:
            raw = raw_signals.get(name, 0.0)
            self._history[name].append(raw)

            # Smoothing: average over window
            history = self._history[name]
            if len(history) >= 3:
                smoothed = sum(history) / len(history)
            else:
                smoothed = raw

            # Clamp to max delta
            clamped = max(-_MAX_DELTA, min(_MAX_DELTA, smoothed))
            if clamped != smoothed:
                guard_applied = True

            stabilised[name] = round(clamped, 4)

        return stabilised, guard_applied

    def reset(self) -> None:
        for name in VOTER_NAMES:
            self._history[name].clear()


# ─── C. WEIGHT RECOMMENDATION ENGINE ─────────────────────────────────────────

def _compute_recommendation(
    current_weights: dict[str, float],
    stabilised_deltas: dict[str, float],
) -> dict[str, float]:
    """Compute recommended weights by applying deltas with bounds enforcement."""
    recommended: dict[str, float] = {}
    for name in VOTER_NAMES:
        current = current_weights.get(name, 0.2)
        delta = stabilised_deltas.get(name, 0.0)
        new_weight = max(_MIN_WEIGHT, min(_MAX_WEIGHT, current + delta))
        recommended[name] = round(new_weight, 4)

    # Normalize to sum = 1.0
    total = sum(recommended.values())
    if total > 0:
        recommended = {k: round(v / total, 4) for k, v in recommended.items()}

    return recommended


def _compute_health_score(
    reliability: VoterReliabilitySnapshot,
    conflict: ConflictAnalysis,
) -> float:
    """
    Weight health: how balanced and stable is the current weighting?
    High = balanced, low conflict, high reliability across voters.
    """
    avg_reliability = sum(reliability.reliability_scores.values()) / max(len(reliability.reliability_scores), 1)
    conflict_penalty = {"none": 0.0, "low": 0.05, "medium": 0.15, "high": 0.3}.get(conflict.severity, 0.0)

    # Reliability variance (lower = healthier)
    rel_values = list(reliability.reliability_scores.values())
    if rel_values:
        rel_mean = sum(rel_values) / len(rel_values)
        rel_variance = sum((v - rel_mean) ** 2 for v in rel_values) / len(rel_values)
        variance_penalty = min(0.2, rel_variance * 2)
    else:
        variance_penalty = 0.1

    health = avg_reliability - conflict_penalty - variance_penalty
    return round(max(0.0, min(1.0, health)), 3)


# ─── MODULE-LEVEL SINGLETON ───────────────────────────────────────────────────

_stability_controller = WeightStabilityController()


# ─── MAIN ENTRY POINT ─────────────────────────────────────────────────────────

def compute_weight_intelligence(
    *,
    influence: VoterInfluenceSnapshot,
    reliability: VoterReliabilitySnapshot,
    conflict: ConflictAnalysis,
    agreement: AgreementAnalysis,
    current_weights: dict[str, float] | None = None,
) -> WeightIntelligenceBlock:
    """
    Compute full weight intelligence block for this decision cycle.

    Pure observational output. NEVER modifies runtime weights.
    Deterministic for identical inputs (except smoothing history).
    """
    weights = current_weights or dict(DEFAULT_WEIGHTS)

    # A. Extract raw signals
    raw_signals = _extract_raw_signals(influence, reliability, conflict, agreement)

    # B. Stabilise
    stabilised_deltas, guard_applied = _stability_controller.stabilise(raw_signals)

    # C. Compute recommendation
    recommended = _compute_recommendation(weights, stabilised_deltas)

    # Health + confidence
    health = _compute_health_score(reliability, conflict)
    confidence = min(1.0, health * 0.8 + (0.2 if not guard_applied else 0.0))

    # Reasoning tags
    tags: list[str] = []
    for name, delta in stabilised_deltas.items():
        if delta > 0.02:
            tags.append(f"{name}_increase")
        elif delta < -0.02:
            tags.append(f"{name}_decrease")
    if guard_applied:
        tags.append("stability_guard_active")
    if health < 0.5:
        tags.append("low_weight_health")

    return WeightIntelligenceBlock(
        timestamp=_time.time(),
        voter_weights_current=weights,
        voter_weights_recommended=recommended,
        weight_deltas=stabilised_deltas,
        weight_health_score=health,
        confidence=round(confidence, 3),
        stability_guard_applied=guard_applied,
        reasoning_tags=tags,
    )


def emit_weight_intelligence_log(symbol: str, block: WeightIntelligenceBlock) -> None:
    """Emit structured weight intelligence log. Never raises."""
    try:
        delta_parts = " ".join(
            f"{n}={block.voter_weights_current.get(n, 0):.2f}→{block.voter_weights_recommended.get(n, 0):.2f}({block.weight_deltas.get(n, 0):+.3f})"
            for n in VOTER_NAMES
        )
        logger.debug(
            "[WEIGHT_INTELLIGENCE] symbol=%s health=%.2f conf=%.2f guard=%s %s tags=%s",
            symbol, block.weight_health_score, block.confidence,
            block.stability_guard_applied, delta_parts,
            ",".join(block.reasoning_tags) or "none",
        )
    except Exception:
        pass
