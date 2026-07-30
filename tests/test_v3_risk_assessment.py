"""
Tests for V3 Risk Assessment Engine.

Verifies:
    - SCALP risk evaluation (tight stops, high spread impact)
    - INTRADAY structure-based risk (wider stops, lower spread impact)
    - EXTENDED higher timeframe risk
    - Poor spread/risk geometry detected
    - Missing data handled
    - Observer integration
"""

import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from core.v3_shadow.context_models import (
    HTFStructureContext, LocationContext, BehaviourContext, V3MarketContext,
)
from core.v3_shadow.opportunity_models import (
    OpportunityAssessment, AlignmentResult, HIGH_QUALITY_CONTEXT, INTERESTING_CONTEXT,
)
from core.v3_shadow.horizon_models import (
    HorizonAssessment, HorizonCandidate, SCALP, INTRADAY, EXTENDED, NO_HORIZON,
)
from core.v3_shadow.risk_models import (
    RiskAssessment, ACCEPTABLE_RISK, MARGINAL_RISK, POOR_RISK, INSUFFICIENT_RISK_DATA,
)
from core.v3_shadow.risk_builder import build_risk_assessment


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════


def _ctx(volatility="NEUTRAL", liquidity_dist=0.0) -> V3MarketContext:
    return V3MarketContext(
        symbol="EURUSD", timestamp_utc=1753574400.0, overall_confidence=0.7,
        behaviour=BehaviourContext(volatility_state=volatility, confidence=0.6),
        location=LocationContext(
            nearest_liquidity_distance_pips=liquidity_dist, confidence=0.5),
    )


def _horizon(
    selected: str = INTRADAY,
    move_min: float = 20.0, move_max: float = 50.0,
    confidence: float = 0.7,
) -> HorizonAssessment:
    return HorizonAssessment(
        symbol="EURUSD", timestamp_utc=1753574400.0,
        selected_horizon=selected,
        expected_move_min_pips=move_min, expected_move_max_pips=move_max,
        stop_framework="M15_STRUCTURE",
        confidence=confidence,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# SCALP RISK TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestScalpRisk:
    """SCALP risk geometry evaluation."""

    def test_scalp_high_spread_impact(self):
        """SCALP with 1 pip spread on 3.5 pip stop = ~29% spread/risk."""
        ctx = _ctx()
        h = _horizon(SCALP, move_min=5.0, move_max=20.0)
        risk = build_risk_assessment(ctx, h, spread_pips=1.0)
        # Stop estimate: midpoint of SCALP profile (2+5)/2 = 3.5 pips
        # Spread/risk: 1.0/3.5 ≈ 0.29
        assert risk.spread_to_risk_ratio > 0.20
        assert risk.horizon == SCALP
        # High spread/risk noted in factors
        assert any("spread" in f.lower() for f in
                   risk.supporting_factors + risk.conflicting_factors)

    def test_scalp_tight_spread(self):
        """SCALP with 0.5 pip spread on 3.5 pip stop = ~14% → could be ACCEPTABLE."""
        ctx = _ctx()
        h = _horizon(SCALP, move_min=5.0, move_max=20.0)
        risk = build_risk_assessment(ctx, h, spread_pips=0.5)
        assert risk.spread_to_risk_ratio < 0.20
        assert risk.risk_state in (ACCEPTABLE_RISK, MARGINAL_RISK)


# ═══════════════════════════════════════════════════════════════════════════════
# INTRADAY RISK TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestIntradayRisk:
    """INTRADAY structure-based risk geometry."""

    def test_intraday_acceptable(self):
        """INTRADAY with 1 pip spread on 10 pip stop = 10% → ACCEPTABLE."""
        ctx = _ctx()
        h = _horizon(INTRADAY, move_min=20.0, move_max=50.0)
        risk = build_risk_assessment(ctx, h, spread_pips=1.0)
        # Stop estimate: midpoint of INTRADAY (5+15)/2 = 10 pips
        # Spread/risk: 1.0/10 = 0.10 → ACCEPTABLE
        assert risk.spread_to_risk_ratio <= 0.20
        assert risk.risk_state == ACCEPTABLE_RISK
        assert risk.risk_reward_ratio >= 2.0

    def test_intraday_with_liquidity_target(self):
        """INTRADAY with nearby liquidity target uses it for target."""
        ctx = _ctx(liquidity_dist=35.0)
        h = _horizon(INTRADAY, move_min=20.0, move_max=50.0)
        risk = build_risk_assessment(ctx, h, spread_pips=1.0)
        assert risk.target_distance_pips == pytest.approx(35.0, abs=1.0)
        assert risk.target_source == "LIQUIDITY_TARGET"

    def test_intraday_expansion_widens_stop(self):
        """Expanding volatility widens stop estimate."""
        ctx_normal = _ctx(volatility="NEUTRAL")
        ctx_expand = _ctx(volatility="EXPANSION")
        h = _horizon(INTRADAY)

        risk_normal = build_risk_assessment(ctx_normal, h, spread_pips=1.0)
        risk_expand = build_risk_assessment(ctx_expand, h, spread_pips=1.0)

        assert risk_expand.stop_distance_pips > risk_normal.stop_distance_pips


# ═══════════════════════════════════════════════════════════════════════════════
# EXTENDED RISK TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestExtendedRisk:
    """EXTENDED higher timeframe risk geometry."""

    def test_extended_low_spread_impact(self):
        """EXTENDED with 1 pip spread on 32 pip stop = ~3% → ACCEPTABLE."""
        ctx = _ctx()
        h = _horizon(EXTENDED, move_min=50.0, move_max=150.0)
        risk = build_risk_assessment(ctx, h, spread_pips=1.0)
        # Stop: (15+50)/2 = 32.5 pips
        # Spread/risk: 1.0/32.5 ≈ 0.03 → very low
        assert risk.spread_to_risk_ratio < 0.10
        assert risk.risk_state == ACCEPTABLE_RISK

    def test_extended_high_rr(self):
        """EXTENDED produces high RR due to large expected move."""
        ctx = _ctx()
        h = _horizon(EXTENDED, move_min=50.0, move_max=150.0)
        risk = build_risk_assessment(ctx, h, spread_pips=1.0)
        assert risk.risk_reward_ratio >= 2.5


# ═══════════════════════════════════════════════════════════════════════════════
# POOR GEOMETRY TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestPoorGeometry:
    """Poor spread/risk geometry detection."""

    def test_extreme_spread(self):
        """3 pip spread on scalp 3.5 pip stop = ~86% → POOR."""
        ctx = _ctx()
        h = _horizon(SCALP, move_min=5.0, move_max=20.0)
        risk = build_risk_assessment(ctx, h, spread_pips=3.0)
        assert risk.risk_state == POOR_RISK
        assert any("spread" in c.lower() or "High" in c for c in risk.conflicting_factors)

    def test_cost_adjusted_negative(self):
        """Very high spread on very tight stop makes spread/risk extreme → POOR."""
        ctx = _ctx()
        # Use a custom horizon with very small expected move
        h = _horizon(SCALP, move_min=2.0, move_max=4.0)
        risk = build_risk_assessment(ctx, h, spread_pips=3.0)
        # 3 pip spread on 3.5 pip stop = 86% → POOR_RISK (hard gate at 35%)
        assert risk.risk_state == POOR_RISK


# ═══════════════════════════════════════════════════════════════════════════════
# MISSING DATA TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestMissingData:
    """Handles missing/insufficient data."""

    def test_no_horizon(self):
        """NO_HORIZON → INSUFFICIENT_RISK_DATA."""
        ctx = _ctx()
        h = HorizonAssessment(symbol="EURUSD", selected_horizon=NO_HORIZON)
        risk = build_risk_assessment(ctx, h)
        assert risk.risk_state == INSUFFICIENT_RISK_DATA

    def test_zero_spread_uses_default(self):
        """Zero spread uses 1 pip default."""
        ctx = _ctx()
        h = _horizon(INTRADAY)
        risk = build_risk_assessment(ctx, h, spread_pips=0.0)
        assert risk.spread_cost_pips == 1.0


# ═══════════════════════════════════════════════════════════════════════════════
# MODEL TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestModels:
    """Model correctness."""

    def test_frozen(self):
        r = RiskAssessment(symbol="EURUSD")
        with pytest.raises(Exception):
            r.symbol = "X"

    def test_to_dict(self):
        ctx = _ctx()
        h = _horizon(INTRADAY)
        r = build_risk_assessment(ctx, h, spread_pips=1.0)
        d = r.to_dict()
        assert d["horizon"] == INTRADAY
        assert "spread_to_risk_ratio" in d
        assert "risk_reward_ratio" in d
        assert json.loads(json.dumps(d, default=str))


# ═══════════════════════════════════════════════════════════════════════════════
# OBSERVER INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestObserverIntegration:
    """Observer #10 persists RiskAssessment."""

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        import core.v3_shadow.observer as mod
        self._orig_mu = mod._LOCAL_DIR
        self._orig_ctx = mod._CONTEXT_DIR
        self._orig_opp = mod._ASSESSMENT_DIR
        self._orig_hor = mod._HORIZON_DIR
        self._orig_risk = mod._RISK_DIR
        mod._LOCAL_DIR = self.temp_dir + "/mu"
        mod._CONTEXT_DIR = self.temp_dir + "/ctx"
        mod._ASSESSMENT_DIR = self.temp_dir + "/opp"
        mod._HORIZON_DIR = self.temp_dir + "/hor"
        mod._RISK_DIR = self.temp_dir + "/risk"

    def teardown_method(self):
        import core.v3_shadow.observer as mod
        mod._LOCAL_DIR = self._orig_mu
        mod._CONTEXT_DIR = self._orig_ctx
        mod._ASSESSMENT_DIR = self._orig_opp
        mod._HORIZON_DIR = self._orig_hor
        mod._RISK_DIR = self._orig_risk
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_persists_risk(self):
        """Observer writes RiskAssessment to JSONL."""
        from core.v3_shadow.observer import observe_market_understanding

        @dataclass
        class MockCandle:
            high: float = 1.086
            low: float = 1.084
            open: float = 1.085
            close: float = 1.0855
            time: int = 1753574400

        @dataclass
        class Ctx:
            symbol: str = "EURUSD"
            cycle_id: int = 1
            bar_time: float = 1753574400.0
            engine_result: dict = None
            engine_state: Any = None
            candles: list = None
            closed_i: int = 60
            bid: float = 1.08500
            ask: float = 1.08510
            htf_context: Any = None
            market_context: Any = None
            runtime_session_id: str = "t"
            decision_funnel: Any = None
            config: Any = None
            detected_patterns: list = None
            risk_manager: Any = None

        ctx = Ctx(
            engine_result={"entity_id": "TEST"},
            candles=[MockCandle(
                high=1.085 + (i % 5) * 0.0003,
                low=1.083 + (i % 3) * 0.0002,
                open=1.084, close=1.0845,
                time=1753574400 + i * 300,
            ) for i in range(65)],
        )

        observe_market_understanding(ctx)

        risk_files = list(Path(self.temp_dir + "/risk").rglob("*.jsonl"))
        assert len(risk_files) == 1
        record = json.loads(open(risk_files[0]).readline())
        assert "risk_state" in record
        assert "spread_to_risk_ratio" in record


# ═══════════════════════════════════════════════════════════════════════════════
# SAFETY TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestSafety:
    """No execution coupling."""

    def test_no_forbidden_imports(self):
        import inspect
        import core.v3_shadow.risk_builder as m
        source = inspect.getsource(m)
        for f in ["import MetaTrader5", "from core.runtime"]:
            assert f not in source

    def test_no_trade_actions(self):
        from dataclasses import fields
        for f in fields(RiskAssessment):
            assert "execute" not in f.name.lower()
            assert "order" not in f.name.lower()
            assert "position" not in f.name.lower()
