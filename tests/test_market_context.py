"""Tests for the Market Context Layer (Phase 1)."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest

from core.market_context.models import (
    Direction,
    H1Summary,
    H4Summary,
    M15Summary,
    M5Summary,
    MarketContext,
    Phase,
    Regime,
)
from core.market_context.change_detector import ChangeDetector
from core.market_context.conflict_resolver import ConflictResolver
from core.market_context.persistence import MarketContextPersistence
from core.market_context.builder import MarketContextBuilder


# ─── MODEL TESTS ──────────────────────────────────────────────────────────────


class TestMarketContextModel:
    """Test MarketContext frozen dataclass."""

    def test_default_construction(self):
        ctx = MarketContext(symbol="EURUSD", cycle_id=1, timestamp_utc=1000.0)
        assert ctx.symbol == "EURUSD"
        assert ctx.direction == Direction.NEUTRAL
        assert ctx.regime == Regime.TRANSITIONAL
        assert ctx.phase == Phase.CONSOLIDATION
        assert ctx.is_material_change is False

    def test_frozen(self):
        ctx = MarketContext(symbol="EURUSD", cycle_id=1, timestamp_utc=1000.0)
        with pytest.raises(Exception):
            ctx.symbol = "GBPUSD"  # type: ignore

    def test_to_dict_serializable(self):
        ctx = MarketContext(
            symbol="EURUSD",
            cycle_id=42,
            timestamp_utc=1784562000.0,
            direction=Direction.BULLISH,
            direction_confidence=0.75,
            regime=Regime.TRENDING,
            regime_confidence=0.85,
        )
        d = ctx.to_dict()
        assert d["symbol"] == "EURUSD"
        assert d["direction"] == "BULLISH"
        assert d["regime"] == "TRENDING"
        # Must be JSON-serializable
        json_str = json.dumps(d)
        assert "EURUSD" in json_str

    def test_to_summary_compact(self):
        ctx = MarketContext(
            symbol="GBPUSD",
            cycle_id=1,
            timestamp_utc=1000.0,
            direction=Direction.BEARISH,
            regime=Regime.RANGING,
            phase=Phase.PULLBACK,
            tradability_score=0.6,
        )
        s = ctx.to_summary()
        assert s["direction"] == "BEARISH"
        assert s["regime"] == "RANGING"
        assert s["phase"] == "PULLBACK"
        assert "symbol" not in s  # Summary is compact


# ─── CHANGE DETECTOR TESTS ────────────────────────────────────────────────────


class TestChangeDetector:
    """Test material change detection."""

    def setup_method(self):
        self.detector = ChangeDetector()

    def test_first_context_is_material(self):
        ctx = MarketContext(symbol="EURUSD", cycle_id=1, timestamp_utc=1000.0)
        assert self.detector.is_material(ctx, None) is True

    def test_same_context_not_material(self):
        ctx = MarketContext(symbol="EURUSD", cycle_id=1, timestamp_utc=1000.0)
        assert self.detector.is_material(ctx, ctx) is False

    def test_direction_change_is_material(self):
        prev = MarketContext(symbol="EURUSD", cycle_id=1, timestamp_utc=1000.0, direction=Direction.NEUTRAL)
        curr = MarketContext(symbol="EURUSD", cycle_id=2, timestamp_utc=1005.0, direction=Direction.BULLISH)
        assert self.detector.is_material(curr, prev) is True

    def test_regime_change_is_material(self):
        prev = MarketContext(symbol="EURUSD", cycle_id=1, timestamp_utc=1000.0, regime=Regime.RANGING)
        curr = MarketContext(symbol="EURUSD", cycle_id=2, timestamp_utc=1005.0, regime=Regime.TRENDING)
        assert self.detector.is_material(curr, prev) is True

    def test_phase_change_is_material(self):
        prev = MarketContext(symbol="EURUSD", cycle_id=1, timestamp_utc=1000.0, phase=Phase.CONSOLIDATION)
        curr = MarketContext(symbol="EURUSD", cycle_id=2, timestamp_utc=1005.0, phase=Phase.IMPULSE)
        assert self.detector.is_material(curr, prev) is True

    def test_small_confidence_change_not_material(self):
        prev = MarketContext(symbol="EURUSD", cycle_id=1, timestamp_utc=1000.0, direction_confidence=0.5)
        curr = MarketContext(symbol="EURUSD", cycle_id=2, timestamp_utc=1005.0, direction_confidence=0.55)
        assert self.detector.is_material(curr, prev) is False

    def test_large_confidence_change_is_material(self):
        prev = MarketContext(symbol="EURUSD", cycle_id=1, timestamp_utc=1000.0, direction_confidence=0.3)
        curr = MarketContext(symbol="EURUSD", cycle_id=2, timestamp_utc=1005.0, direction_confidence=0.6)
        assert self.detector.is_material(curr, prev) is True

    def test_describe_change(self):
        prev = MarketContext(symbol="EURUSD", cycle_id=1, timestamp_utc=1000.0, direction=Direction.NEUTRAL)
        curr = MarketContext(symbol="EURUSD", cycle_id=2, timestamp_utc=1005.0, direction=Direction.BULLISH)
        desc = self.detector.describe_change(curr, prev)
        assert "direction" in desc
        assert "NEUTRAL" in desc
        assert "BULLISH" in desc


# ─── CONFLICT RESOLVER TESTS ──────────────────────────────────────────────────


class TestConflictResolver:
    """Test cross-timeframe conflict resolution."""

    def setup_method(self):
        self.resolver = ConflictResolver()

    def test_all_neutral(self):
        h4 = H4Summary()
        h1 = H1Summary()
        m15 = M15Summary()
        m5 = M5Summary()
        direction, conf, conflict, desc, method = self.resolver.resolve(h4, h1, m15, m5)
        assert direction == Direction.NEUTRAL
        assert conflict is False

    def test_consensus_bullish(self):
        h4 = H4Summary(trend_bias="BULLISH", trend_strength=0.7)
        h1 = H1Summary(direction="BULLISH", confidence=0.6)
        m15 = M15Summary()
        m5 = M5Summary(bias_direction="BUY", bias_strength=60.0)
        direction, conf, conflict, desc, method = self.resolver.resolve(h4, h1, m15, m5)
        assert direction == Direction.BULLISH
        assert method == "CONSENSUS"
        assert conf > 0.0

    def test_conflict_detected(self):
        h4 = H4Summary(trend_bias="BULLISH", trend_strength=0.7)
        h1 = H1Summary(direction="BEARISH", confidence=0.6)
        m15 = M15Summary()
        m5 = M5Summary(bias_direction="BUY", bias_strength=60.0)
        direction, conf, conflict, desc, method = self.resolver.resolve(h4, h1, m15, m5)
        assert conflict is True
        assert "H4=BULLISH" in desc
        assert "H1=BEARISH" in desc

    def test_hierarchy_h4_wins_no_consensus(self):
        h4 = H4Summary(trend_bias="BEARISH", trend_strength=0.8)
        h1 = H1Summary(direction="NEUTRAL", confidence=0.0)
        m15 = M15Summary()
        m5 = M5Summary(bias_direction="NEUTRAL", bias_strength=0.0)
        direction, conf, conflict, desc, method = self.resolver.resolve(h4, h1, m15, m5)
        assert direction == Direction.BEARISH
        assert "HIERARCHY" in method


# ─── PERSISTENCE TESTS ────────────────────────────────────────────────────────


class TestPersistence:
    """Test local JSONL persistence."""

    def test_persist_creates_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.market_context.persistence._LOCAL_DIR", str(tmp_path / "market_context"))
        p = MarketContextPersistence()
        ctx_dict = MarketContext(
            symbol="EURUSD", cycle_id=1, timestamp_utc=1000.0,
            direction=Direction.BULLISH, is_material_change=True,
        ).to_dict()
        p.persist(ctx_dict)

        # Find the written file
        files = list((tmp_path / "market_context" / "EURUSD").glob("*.jsonl"))
        assert len(files) == 1
        content = files[0].read_text()
        record = json.loads(content.strip())
        assert record["symbol"] == "EURUSD"
        assert record["direction"] == "BULLISH"


# ─── BUILDER TESTS ────────────────────────────────────────────────────────────


class TestBuilder:
    """Test MarketContextBuilder."""

    def test_build_with_no_inputs_returns_neutral(self):
        builder = MarketContextBuilder(symbol="EURUSD")
        ctx = builder.build(cycle_id=1, current_time_s=1000.0)
        assert ctx.symbol == "EURUSD"
        assert ctx.direction == Direction.NEUTRAL
        assert ctx.regime == Regime.TRANSITIONAL

    def test_build_with_htf_context(self):
        """Test that builder extracts HTFContext snapshots correctly."""
        # Create mock HTFContext-like object
        @dataclass(frozen=True)
        class MockRegime:
            classification: type = None
            confidence: float = 0.8
            atr_ratio: float = 1.1
            ema_slope: float = 0.2
            trend_bias: str = "BULLISH"
            trend_strength: float = 0.7
            bar_time: int = 0

        class MockClassification:
            value = "TRENDING_BULLISH"

        @dataclass(frozen=True)
        class MockBias:
            direction: type = None
            confidence: float = 0.65
            bar_time: int = 0
            ema_position: float = 1.2
            swing_structure: str = "HH_HL"

        class MockDirection:
            value = "BULLISH"

        @dataclass
        class MockHTF:
            regime: object = None
            bias: object = None
            structure: object = None

        regime = MockRegime(classification=MockClassification())
        bias = MockBias(direction=MockDirection())
        htf = MockHTF(regime=regime, bias=bias, structure=None)

        builder = MarketContextBuilder(symbol="EURUSD")
        ctx = builder.build(htf_context=htf, cycle_id=5, current_time_s=2000.0)

        assert ctx.h4.regime == "TRENDING_BULLISH"
        assert ctx.h4.trend_bias == "BULLISH"
        assert ctx.h1.direction == "BULLISH"
        assert ctx.h1.swing_structure == "HH_HL"
        assert ctx.regime == Regime.TRENDING
        assert ctx.direction == Direction.BULLISH

    def test_build_detects_material_change(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.market_context.persistence._LOCAL_DIR", str(tmp_path / "mc"))
        monkeypatch.setattr("core.config.MARKET_CONTEXT_PERSISTENCE_ENABLED", True)

        builder = MarketContextBuilder(symbol="EURUSD")

        # First build — always material
        ctx1 = builder.build(cycle_id=1, current_time_s=1000.0)
        assert ctx1.is_material_change is True

        # Second build — same inputs → not material
        ctx2 = builder.build(cycle_id=2, current_time_s=1005.0)
        assert ctx2.is_material_change is False

    def test_build_never_raises(self):
        """Builder must never raise even with garbage inputs."""
        builder = MarketContextBuilder(symbol="TEST")
        # Pass completely wrong types — should return neutral, not crash
        ctx = builder.build(
            htf_context="not_an_htf",  # type: ignore
            engine_state=42,  # type: ignore
            cycle_id=1,
            current_time_s=1000.0,
        )
        assert ctx.symbol == "TEST"
        assert ctx.direction == Direction.NEUTRAL

    def test_previous_context_tracked(self):
        builder = MarketContextBuilder(symbol="EURUSD")
        assert builder.previous_context is None
        ctx = builder.build(cycle_id=1, current_time_s=1000.0)
        assert builder.previous_context is ctx
