"""
Conflict Resolver — Resolves cross-timeframe directional disagreements.

Hierarchy: H4 > H1 > M15 > M5
Consensus: 3+ timeframes agree → highest confidence.

Never raises. Never affects trading decisions (Phase 1: observational only).
"""

from __future__ import annotations

from core.market_context.models import Direction, H4Summary, H1Summary, M15Summary, M5Summary


class ConflictResolver:
    """Resolves disagreements between timeframe directional signals."""

    def resolve(
        self,
        h4: H4Summary,
        h1: H1Summary,
        m15: M15Summary,
        m5: M5Summary,
    ) -> tuple[Direction, float, bool, str, str]:
        """
        Resolve unified direction from all timeframe summaries.

        Returns:
            (direction, confidence, conflict_detected, conflict_description, resolution_method)
        """
        # Map each TF to a Direction
        h4_dir = self._to_direction(h4.trend_bias)
        h1_dir = self._to_direction(h1.direction)
        m5_dir = self._bias_to_direction(m5.bias_direction)

        # M15 is structural (no directional bias) — skip in direction resolution
        directions = [h4_dir, h1_dir, m5_dir]
        confidences = [h4.trend_strength, h1.confidence, m5.bias_strength / 100.0]

        # Check for consensus (2+ agree on non-neutral direction)
        bullish_count = sum(1 for d in directions if d == Direction.BULLISH)
        bearish_count = sum(1 for d in directions if d == Direction.BEARISH)

        conflict_detected = False
        conflict_description = ""

        # Detect conflict: H4 and H1 disagree on direction (both non-neutral, opposing)
        if (h4_dir != Direction.NEUTRAL and h1_dir != Direction.NEUTRAL
                and h4_dir != h1_dir):
            conflict_detected = True
            conflict_description = f"H4={h4_dir.value} vs H1={h1_dir.value}"

        # Resolution: hierarchy (H4 wins) or consensus
        if bullish_count >= 2:
            # Consensus: multiple TFs agree bullish
            avg_conf = sum(c for d, c in zip(directions, confidences) if d == Direction.BULLISH) / max(bullish_count, 1)
            return Direction.BULLISH, min(1.0, avg_conf), conflict_detected, conflict_description, "CONSENSUS"

        if bearish_count >= 2:
            avg_conf = sum(c for d, c in zip(directions, confidences) if d == Direction.BEARISH) / max(bearish_count, 1)
            return Direction.BEARISH, min(1.0, avg_conf), conflict_detected, conflict_description, "CONSENSUS"

        # No consensus — use hierarchy (H4 > H1 > M5)
        if h4_dir != Direction.NEUTRAL:
            return h4_dir, h4.trend_strength * 0.8, conflict_detected, conflict_description, "HIERARCHY_H4"

        if h1_dir != Direction.NEUTRAL:
            return h1_dir, h1.confidence * 0.7, conflict_detected, conflict_description, "HIERARCHY_H1"

        if m5_dir != Direction.NEUTRAL:
            return m5_dir, (m5.bias_strength / 100.0) * 0.5, conflict_detected, conflict_description, "HIERARCHY_M5"

        return Direction.NEUTRAL, 0.0, False, "", "ALL_NEUTRAL"

    @staticmethod
    def _to_direction(value: str) -> Direction:
        """Convert string bias to Direction enum."""
        v = (value or "").upper()
        if v == "BULLISH":
            return Direction.BULLISH
        if v == "BEARISH":
            return Direction.BEARISH
        return Direction.NEUTRAL

    @staticmethod
    def _bias_to_direction(value: str) -> Direction:
        """Convert M5 bias_direction (BUY/SELL) to Direction."""
        v = (value or "").upper()
        if v == "BUY":
            return Direction.BULLISH
        if v == "SELL":
            return Direction.BEARISH
        return Direction.NEUTRAL
