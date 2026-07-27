"""
Tests for M3B — H1 Structural Phase Ownership.

Validates:
1. Phase classification uses ONLY H1 fields (no M5 dependency)
2. All phase states are reachable from H1 inputs
3. Downstream MarketContext receives correct phase
4. M5 bias_phase does NOT influence structural phase
"""

from __future__ import annotations

import pytest

from core.market_context.models import (
    Direction, H1Summary, H4Summary, M15Summary, M5Summary,
    MarketContext, Phase, Regime,
)
from core.market_context.builder import MarketContextBuilder


# ─── HELPERS ──────────────────────────────────────────────────────────────────


def _build_with_h1(h1: H1Summary, m5: M5Summary | None = None) -> MarketContext:
    """Build a MarketContext with specific H1 and optional M5 settings."""
    builder = MarketContextBuilder(symbol="TEST")
    # We call the internal _classify_phase directly for isolated testing
    # but also verify through the full build path
    return builder.build(cycle_id=1, current_time_s=1000.0)


# ─── TEST: PHASE FROM H1 ONLY ────────────────────────────────────────────────


class TestPhaseFromH1Only:
    """Phase classification depends ONLY on H1 structural fields."""

    def setup_method(self):
        self.builder = MarketContextBuilder(symbol="TEST")

    def test_impulse_bullish(self):
        """HH_HL + bullish BOS + good confidence → IMPULSE."""
        h1 = H1Summary(
            direction="BULLISH", confidence=0.7,
            swing_structure="HH_HL", bos_confirmed=True, bos_direction="BULLISH",
        )
        phase, conf = self.builder._classify_phase(h1, M5Summary())
        assert phase == Phase.IMPULSE
        assert conf > 0.5

    def test_impulse_bearish(self):
        """LH_LL + bearish BOS + good confidence → IMPULSE."""
        h1 = H1Summary(
            direction="BEARISH", confidence=0.8,
            swing_structure="LH_LL", bos_confirmed=True, bos_direction="BEARISH",
        )
        phase, conf = self.builder._classify_phase(h1, M5Summary())
        assert phase == Phase.IMPULSE
        assert conf > 0.5

    def test_pullback_bullish(self):
        """HH_HL structure but no BOS → PULLBACK."""
        h1 = H1Summary(
            direction="BULLISH", confidence=0.6,
            swing_structure="HH_HL", bos_confirmed=False, bos_direction="",
        )
        phase, conf = self.builder._classify_phase(h1, M5Summary())
        assert phase == Phase.PULLBACK

    def test_pullback_bearish(self):
        """LH_LL structure but no BOS → PULLBACK."""
        h1 = H1Summary(
            direction="BEARISH", confidence=0.5,
            swing_structure="LH_LL", bos_confirmed=False, bos_direction="",
        )
        phase, conf = self.builder._classify_phase(h1, M5Summary())
        assert phase == Phase.PULLBACK

    def test_consolidation_mixed(self):
        """MIXED structure → CONSOLIDATION."""
        h1 = H1Summary(
            direction="NEUTRAL", confidence=0.3,
            swing_structure="MIXED", bos_confirmed=False, bos_direction="",
        )
        phase, conf = self.builder._classify_phase(h1, M5Summary())
        assert phase == Phase.CONSOLIDATION

    def test_exhaustion_low_confidence(self):
        """Directional structure but very low confidence → EXHAUSTION."""
        h1 = H1Summary(
            direction="BULLISH", confidence=0.2,
            swing_structure="HH_HL", bos_confirmed=False, bos_direction="",
        )
        phase, conf = self.builder._classify_phase(h1, M5Summary())
        assert phase == Phase.EXHAUSTION

    def test_reversal_bos_against_structure(self):
        """HH_HL structure + bearish BOS → REVERSAL."""
        h1 = H1Summary(
            direction="BULLISH", confidence=0.6,
            swing_structure="HH_HL", bos_confirmed=True, bos_direction="BEARISH",
        )
        phase, conf = self.builder._classify_phase(h1, M5Summary())
        assert phase == Phase.REVERSAL

    def test_reversal_bearish_with_bullish_bos(self):
        """LH_LL structure + bullish BOS → REVERSAL."""
        h1 = H1Summary(
            direction="BEARISH", confidence=0.6,
            swing_structure="LH_LL", bos_confirmed=True, bos_direction="BULLISH",
        )
        phase, conf = self.builder._classify_phase(h1, M5Summary())
        assert phase == Phase.REVERSAL


# ─── TEST: M5 DOES NOT INFLUENCE PHASE ───────────────────────────────────────


class TestM5DoesNotInfluencePhase:
    """Changing M5 bias_phase MUST NOT change the structural phase."""

    def setup_method(self):
        self.builder = MarketContextBuilder(symbol="TEST")

    def test_m5_confirmed_does_not_change_phase(self):
        """M5 bias_phase=CONFIRMED must NOT upgrade PULLBACK to IMPULSE."""
        h1 = H1Summary(
            direction="BULLISH", confidence=0.6,
            swing_structure="HH_HL", bos_confirmed=False, bos_direction="",
        )
        # With M5 CONFIRMED
        phase_a, _ = self.builder._classify_phase(h1, M5Summary(bias_phase="CONFIRMED"))
        # With M5 EXPIRED
        phase_b, _ = self.builder._classify_phase(h1, M5Summary(bias_phase="EXPIRED"))
        # Both should be the same — M5 has no influence
        assert phase_a == phase_b == Phase.PULLBACK

    def test_m5_weakening_does_not_create_exhaustion(self):
        """M5 bias_phase=WEAKENING must NOT force EXHAUSTION."""
        h1 = H1Summary(
            direction="BULLISH", confidence=0.7,
            swing_structure="HH_HL", bos_confirmed=True, bos_direction="BULLISH",
        )
        # With M5 WEAKENING — should still be IMPULSE (H1 says so)
        phase, _ = self.builder._classify_phase(h1, M5Summary(bias_phase="WEAKENING"))
        assert phase == Phase.IMPULSE

    def test_m5_expired_does_not_force_consolidation(self):
        """M5 bias_phase=EXPIRED must NOT force CONSOLIDATION when H1 is directional."""
        h1 = H1Summary(
            direction="BEARISH", confidence=0.6,
            swing_structure="LH_LL", bos_confirmed=False, bos_direction="",
        )
        phase, _ = self.builder._classify_phase(h1, M5Summary(bias_phase="EXPIRED"))
        assert phase == Phase.PULLBACK  # H1 decides, not M5


# ─── TEST: FULL BUILD PATH PROPAGATION ────────────────────────────────────────


class TestFullBuildPropagation:
    """Phase propagates correctly through the full MarketContext build."""

    def test_phase_in_market_context(self):
        """MarketContext.phase should reflect H1 structural phase."""
        from dataclasses import dataclass

        @dataclass
        class MockBias:
            direction: object = None
            confidence: float = 0.75
            bar_time: int = 0
            ema_position: float = 0.5
            swing_structure: str = "HH_HL"
            bos_confirmed: bool = True
            bos_direction: str = "BULLISH"

        class MockDir:
            value = "BULLISH"

        @dataclass
        class MockHTF:
            regime: object = None
            bias: object = None
            structure: object = None

        htf = MockHTF(bias=MockBias(direction=MockDir()))
        builder = MarketContextBuilder(symbol="EURUSD")
        ctx = builder.build(htf_context=htf, cycle_id=1, current_time_s=1000.0)

        assert ctx.phase == Phase.IMPULSE
        assert ctx.phase_confidence > 0.5

    def test_phase_serialized_in_to_dict(self):
        """Phase appears in serialized output."""
        ctx = MarketContext(
            symbol="TEST", cycle_id=1, timestamp_utc=1000.0,
            phase=Phase.REVERSAL, phase_confidence=0.6,
        )
        d = ctx.to_dict()
        assert d["phase"] == "REVERSAL"
        assert d["phase_confidence"] == 0.6
