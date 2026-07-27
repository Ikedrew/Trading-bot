"""
Change Detector — Determines material changes between MarketContext instances.

Material change = direction, regime, or phase changed.
Non-material = small confidence fluctuations within same classification.
"""

from __future__ import annotations

from core.market_context.models import MarketContext


class ChangeDetector:
    """Detects material changes between consecutive MarketContext objects."""

    def is_material(self, current: MarketContext, previous: MarketContext | None) -> bool:
        """Return True if current represents a meaningful state change from previous."""
        if previous is None:
            return True  # First context is always material

        if current.direction != previous.direction:
            return True
        if current.regime != previous.regime:
            return True
        if current.phase != previous.phase:
            return True

        # Significant confidence swing (±0.2) within same classification
        if abs(current.direction_confidence - previous.direction_confidence) >= 0.2:
            return True
        if abs(current.regime_confidence - previous.regime_confidence) >= 0.2:
            return True

        return False

    def describe_change(self, current: MarketContext, previous: MarketContext | None) -> str:
        """Produce a human-readable description of what changed."""
        if previous is None:
            return "initial_context"

        changes: list[str] = []

        if current.direction != previous.direction:
            changes.append(f"direction: {previous.direction.value} → {current.direction.value}")
        if current.regime != previous.regime:
            changes.append(f"regime: {previous.regime.value} → {current.regime.value}")
        if current.phase != previous.phase:
            changes.append(f"phase: {previous.phase.value} → {current.phase.value}")
        if abs(current.direction_confidence - previous.direction_confidence) >= 0.2:
            changes.append(f"direction_confidence: {previous.direction_confidence:.2f} → {current.direction_confidence:.2f}")
        if abs(current.regime_confidence - previous.regime_confidence) >= 0.2:
            changes.append(f"regime_confidence: {previous.regime_confidence:.2f} → {current.regime_confidence:.2f}")

        return "; ".join(changes) if changes else "no_change"
