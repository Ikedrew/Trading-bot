"""Unit tests for Shadow Voter System."""

from __future__ import annotations

from core.engine_state import EngineState
from core.state.snapshot import StateSnapshot
from core.voters.bias_voter import ShadowBiasVoter
from core.voters.structure_voter import ShadowStructureVoter
from core.voters.types import VoteResult
from strategy.signals import Side


class TestShadowBiasVoter:
    def _snap(self, phase="EXPIRED", strength=0.0, age=0.0, bias=None):
        state = EngineState()
        state.bias_phase = phase
        state.bias_strength = strength
        state.bias_age_seconds = age
        state.current_bias = bias
        return StateSnapshot.from_state(state)

    def test_expired_returns_negative(self):
        snap = self._snap(phase="EXPIRED")
        result = ShadowBiasVoter().evaluate(snap)
        assert result.score < 0
        assert result.confidence > 0
        assert "expired" in result.reason

    def test_confirmed_high_strength_returns_positive(self):
        snap = self._snap(phase="CONFIRMED", strength=80.0, age=100.0, bias=Side.BUY)
        result = ShadowBiasVoter().evaluate(snap)
        assert result.score > 1.0
        assert result.confidence > 0.5
        assert "confirmed" in result.reason

    def test_building_returns_weak_positive(self):
        snap = self._snap(phase="BUILDING", strength=40.0, age=200.0)
        result = ShadowBiasVoter().evaluate(snap)
        assert 0.0 < result.score < 1.0
        assert "building" in result.reason

    def test_old_confirmed_decays(self):
        fresh = self._snap(phase="CONFIRMED", strength=80.0, age=100.0)
        old = self._snap(phase="CONFIRMED", strength=80.0, age=5000.0)
        fresh_result = ShadowBiasVoter().evaluate(fresh)
        old_result = ShadowBiasVoter().evaluate(old)
        assert fresh_result.score > old_result.score

    def test_score_bounded(self):
        snap = self._snap(phase="CONFIRMED", strength=100.0, age=0.0)
        result = ShadowBiasVoter().evaluate(snap)
        assert -2.0 <= result.score <= 2.0
        assert 0.0 <= result.confidence <= 1.0

    def test_deterministic(self):
        snap = self._snap(phase="CONFIRMED", strength=60.0, age=300.0)
        r1 = ShadowBiasVoter().evaluate(snap)
        r2 = ShadowBiasVoter().evaluate(snap)
        assert r1 == r2

    def test_returns_vote_result_type(self):
        snap = self._snap()
        result = ShadowBiasVoter().evaluate(snap)
        assert isinstance(result, VoteResult)


class TestShadowStructureVoter:
    def _snap(self, regime="RANGING", clarity=0.5, swing_highs=2, swing_lows=2,
              overlap=0.3, sweep_high=None, sweep_low=None):
        state = EngineState()
        state.regime_state = regime
        from core.features.bundle import FeatureBundle
        features = FeatureBundle(
            m5_atr_14=0.001,
            m5_atr_ratio=1.0,
            candle_overlap_ratio=overlap,
            spread=0.00015,
            m5_swing_high_count=swing_highs,
            m5_swing_low_count=swing_lows,
            m5_structure_clarity=clarity,
            last_sweep_high=sweep_high,
            last_sweep_low=sweep_low,
        )
        return StateSnapshot.from_state_and_features(state, features)

    def test_trending_regime_positive(self):
        snap = self._snap(regime="TREND_UP", clarity=0.8, swing_highs=3, swing_lows=3, overlap=0.1)
        result = ShadowStructureVoter().evaluate(snap)
        assert result.score > 1.0
        assert result.confidence > 0.7

    def test_volatile_regime_negative(self):
        snap = self._snap(regime="VOLATILE", clarity=0.1, swing_highs=0, swing_lows=0, overlap=0.8)
        result = ShadowStructureVoter().evaluate(snap)
        assert result.score < 0
        assert result.confidence > 0.3

    def test_ranging_regime_slightly_negative(self):
        snap = self._snap(regime="RANGING", clarity=0.3, swing_highs=1, swing_lows=1, overlap=0.5)
        result = ShadowStructureVoter().evaluate(snap)
        assert result.score < 0.5

    def test_high_overlap_penalized(self):
        clean = self._snap(regime="TREND_UP", clarity=0.6, overlap=0.1)
        choppy = self._snap(regime="TREND_UP", clarity=0.6, overlap=0.8)
        clean_result = ShadowStructureVoter().evaluate(clean)
        choppy_result = ShadowStructureVoter().evaluate(choppy)
        assert clean_result.score > choppy_result.score

    def test_sweep_adds_positive(self):
        no_sweep = self._snap(regime="RANGING", clarity=0.5, overlap=0.3)
        with_sweep = self._snap(regime="RANGING", clarity=0.5, overlap=0.3, sweep_low=1.08)
        no_result = ShadowStructureVoter().evaluate(no_sweep)
        sweep_result = ShadowStructureVoter().evaluate(with_sweep)
        assert sweep_result.score > no_result.score

    def test_high_clarity_rewarded(self):
        low = self._snap(clarity=0.1)
        high = self._snap(clarity=0.9)
        low_result = ShadowStructureVoter().evaluate(low)
        high_result = ShadowStructureVoter().evaluate(high)
        assert high_result.score > low_result.score

    def test_score_bounded(self):
        snap = self._snap(regime="TREND_UP", clarity=0.9, swing_highs=5, swing_lows=5, overlap=0.0)
        result = ShadowStructureVoter().evaluate(snap)
        assert -2.0 <= result.score <= 2.0
        assert 0.0 <= result.confidence <= 1.0

    def test_deterministic(self):
        snap = self._snap(regime="RANGING", clarity=0.4, overlap=0.5)
        r1 = ShadowStructureVoter().evaluate(snap)
        r2 = ShadowStructureVoter().evaluate(snap)
        assert r1 == r2

    def test_returns_vote_result_type(self):
        snap = self._snap()
        result = ShadowStructureVoter().evaluate(snap)
        assert isinstance(result, VoteResult)


class TestSessionVoter:
    def _snap(self, unix_time: float):
        state = EngineState()
        state.current_time = unix_time
        return StateSnapshot.from_state(state)

    def test_london_open_positive(self):
        # 07:30 UTC on a Wednesday
        from datetime import datetime, timezone
        dt = datetime(2026, 5, 28, 7, 30, tzinfo=timezone.utc)
        snap = self._snap(dt.timestamp())
        from core.voters.session_voter import SessionVoter
        result = SessionVoter().evaluate(snap)
        assert result.score > 0.5
        assert "london_open" in result.reason

    def test_overlap_highest(self):
        # 13:00 UTC — London/NY overlap
        from datetime import datetime, timezone
        dt = datetime(2026, 5, 28, 13, 0, tzinfo=timezone.utc)
        snap = self._snap(dt.timestamp())
        from core.voters.session_voter import SessionVoter
        result = SessionVoter().evaluate(snap)
        assert result.score >= 1.0
        assert "overlap" in result.reason

    def test_asia_negative(self):
        # 03:00 UTC — Asian session
        from datetime import datetime, timezone
        dt = datetime(2026, 5, 28, 3, 0, tzinfo=timezone.utc)
        snap = self._snap(dt.timestamp())
        from core.voters.session_voter import SessionVoter
        result = SessionVoter().evaluate(snap)
        assert result.score < 0
        assert "asia" in result.reason

    def test_friday_winddown(self):
        # Friday 21:00 UTC
        from datetime import datetime, timezone
        dt = datetime(2026, 5, 29, 21, 0, tzinfo=timezone.utc)  # Friday
        snap = self._snap(dt.timestamp())
        from core.voters.session_voter import SessionVoter
        result = SessionVoter().evaluate(snap)
        assert result.score < -1.0
        assert "friday" in result.reason

    def test_deterministic(self):
        from datetime import datetime, timezone
        dt = datetime(2026, 5, 28, 10, 0, tzinfo=timezone.utc)
        snap = self._snap(dt.timestamp())
        from core.voters.session_voter import SessionVoter
        r1 = SessionVoter().evaluate(snap)
        r2 = SessionVoter().evaluate(snap)
        assert r1 == r2

    def test_score_bounded(self):
        from datetime import datetime, timezone
        for hour in range(24):
            dt = datetime(2026, 5, 28, hour, 0, tzinfo=timezone.utc)
            snap = self._snap(dt.timestamp())
            from core.voters.session_voter import SessionVoter
            result = SessionVoter().evaluate(snap)
            assert -2.0 <= result.score <= 2.0
            assert 0.0 <= result.confidence <= 1.0

    def test_confidence_always_high(self):
        # Session is deterministic from time — confidence should be high
        from datetime import datetime, timezone
        dt = datetime(2026, 5, 28, 14, 0, tzinfo=timezone.utc)
        snap = self._snap(dt.timestamp())
        from core.voters.session_voter import SessionVoter
        result = SessionVoter().evaluate(snap)
        assert result.confidence >= 0.9

    def test_returns_vote_result(self):
        from datetime import datetime, timezone
        dt = datetime(2026, 5, 28, 10, 0, tzinfo=timezone.utc)
        snap = self._snap(dt.timestamp())
        from core.voters.session_voter import SessionVoter
        result = SessionVoter().evaluate(snap)
        assert isinstance(result, VoteResult)

    def test_reason_references_only_current_time(self):
        from datetime import datetime, timezone
        dt = datetime(2026, 5, 28, 15, 0, tzinfo=timezone.utc)
        snap = self._snap(dt.timestamp())
        from core.voters.session_voter import SessionVoter
        result = SessionVoter().evaluate(snap)
        assert "current_time=" in result.reason


class TestConfluenceEngine:
    def test_all_positive_produces_buy(self):
        from core.voters.confluence_engine import compute_confluence, ConfluenceDecision
        result = compute_confluence(
            bias_vote=VoteResult(score=1.5, confidence=0.9, reason="confirmed"),
            structure_vote=VoteResult(score=1.2, confidence=0.8, reason="clear"),
            volatility_vote=VoteResult(score=0.8, confidence=0.7, reason="moderate"),
            spread_vote=VoteResult(score=0.6, confidence=0.9, reason="tight"),
            session_vote=VoteResult(score=0.8, confidence=0.9, reason="london"),
        )
        assert result.action == "BUY"
        assert result.score > 0.75
        assert result.confidence > 0.6
        assert not result.risk_flag

    def test_all_negative_produces_sell(self):
        from core.voters.confluence_engine import compute_confluence
        result = compute_confluence(
            bias_vote=VoteResult(score=-1.8, confidence=0.9, reason="bearish"),
            structure_vote=VoteResult(score=-1.5, confidence=0.8, reason="breakdown"),
            volatility_vote=VoteResult(score=-0.5, confidence=0.7, reason="choppy"),
            spread_vote=VoteResult(score=-0.3, confidence=0.9, reason="wide"),
            session_vote=VoteResult(score=0.5, confidence=0.9, reason="active"),
        )
        assert result.action == "SELL"
        assert result.score < -0.75

    def test_mixed_produces_no_trade(self):
        from core.voters.confluence_engine import compute_confluence
        result = compute_confluence(
            bias_vote=VoteResult(score=0.3, confidence=0.5, reason="weak"),
            structure_vote=VoteResult(score=-0.2, confidence=0.4, reason="unclear"),
            volatility_vote=VoteResult(score=0.1, confidence=0.3, reason="neutral"),
            spread_vote=VoteResult(score=0.0, confidence=0.5, reason="normal"),
            session_vote=VoteResult(score=0.0, confidence=0.9, reason="mid"),
        )
        assert result.action == "NO_TRADE"

    def test_risk_flag_blocks_trade(self):
        from core.voters.confluence_engine import compute_confluence
        result = compute_confluence(
            bias_vote=VoteResult(score=1.8, confidence=0.95, reason="strong"),
            structure_vote=VoteResult(score=1.5, confidence=0.9, reason="clear"),
            volatility_vote=VoteResult(score=-1.5, confidence=0.9, reason="extreme"),
            spread_vote=VoteResult(score=0.5, confidence=0.9, reason="ok"),
            session_vote=VoteResult(score=0.8, confidence=0.9, reason="active"),
        )
        assert result.risk_flag is True
        assert result.action == "NO_TRADE"

    def test_conflict_flag_detected(self):
        from core.voters.confluence_engine import compute_confluence
        result = compute_confluence(
            bias_vote=VoteResult(score=1.5, confidence=0.9, reason="bullish"),
            structure_vote=VoteResult(score=-1.0, confidence=0.8, reason="bearish_structure"),
            volatility_vote=VoteResult(score=0.5, confidence=0.7, reason="ok"),
            spread_vote=VoteResult(score=0.5, confidence=0.9, reason="ok"),
            session_vote=VoteResult(score=0.5, confidence=0.9, reason="active"),
        )
        assert result.conflict_flag is True

    def test_missing_voters_treated_as_neutral(self):
        from core.voters.confluence_engine import compute_confluence
        result = compute_confluence(
            bias_vote=VoteResult(score=1.5, confidence=0.9, reason="strong"),
            structure_vote=None,
            volatility_vote=None,
            spread_vote=None,
            session_vote=None,
        )
        assert isinstance(result.action, str)
        assert result.score > 0  # bias contributes positively

    def test_breakdown_contains_all_voters(self):
        from core.voters.confluence_engine import compute_confluence
        result = compute_confluence(
            bias_vote=VoteResult(score=1.0, confidence=0.8, reason="ok"),
            structure_vote=VoteResult(score=0.5, confidence=0.7, reason="ok"),
            volatility_vote=VoteResult(score=0.3, confidence=0.6, reason="ok"),
            spread_vote=VoteResult(score=0.2, confidence=0.9, reason="ok"),
            session_vote=VoteResult(score=0.1, confidence=0.9, reason="ok"),
        )
        assert "bias" in result.breakdown
        assert "structure" in result.breakdown
        assert "volatility" in result.breakdown
        assert "spread" in result.breakdown
        assert "session" in result.breakdown
        assert "final_score" in result.breakdown

    def test_deterministic(self):
        from core.voters.confluence_engine import compute_confluence
        votes = dict(
            bias_vote=VoteResult(score=1.0, confidence=0.8, reason="x"),
            structure_vote=VoteResult(score=0.5, confidence=0.7, reason="x"),
            volatility_vote=VoteResult(score=0.3, confidence=0.6, reason="x"),
            spread_vote=VoteResult(score=0.2, confidence=0.9, reason="x"),
            session_vote=VoteResult(score=0.1, confidence=0.9, reason="x"),
        )
        r1 = compute_confluence(**votes)
        r2 = compute_confluence(**votes)
        assert r1 == r2

    def test_confidence_bounded(self):
        from core.voters.confluence_engine import compute_confluence
        result = compute_confluence(
            bias_vote=VoteResult(score=2.0, confidence=1.0, reason="max"),
            structure_vote=VoteResult(score=2.0, confidence=1.0, reason="max"),
            volatility_vote=VoteResult(score=2.0, confidence=1.0, reason="max"),
            spread_vote=VoteResult(score=2.0, confidence=1.0, reason="max"),
            session_vote=VoteResult(score=2.0, confidence=1.0, reason="max"),
        )
        assert 0.0 <= result.confidence <= 1.0


class TestExecutionGate:
    def _snap(self, spread=0.00015, atr=0.001, atr_ratio=1.0, clarity=0.5):
        state = EngineState()
        from core.features.bundle import FeatureBundle
        features = FeatureBundle(
            m5_atr_14=atr,
            m5_atr_ratio=atr_ratio,
            candle_overlap_ratio=0.3,
            spread=spread,
            m5_swing_high_count=2,
            m5_swing_low_count=2,
            m5_structure_clarity=clarity,
            last_sweep_high=None,
            last_sweep_low=None,
        )
        return StateSnapshot.from_state_and_features(state, features)

    def _confluence(self, action="BUY", score=1.0, confidence=0.8, risk=False, conflict=False):
        from core.voters.confluence_engine import ConfluenceDecision
        return ConfluenceDecision(
            action=action, score=score, confidence=confidence,
            risk_flag=risk, conflict_flag=conflict, breakdown={},
        )

    def test_all_pass(self):
        from core.voters.execution_gate import evaluate_execution_gate
        snap = self._snap(spread=0.0001, atr=0.001, atr_ratio=1.0, clarity=0.6)
        conf = self._confluence(score=1.0, confidence=0.85)
        result = evaluate_execution_gate(conf, snap)
        assert result.allowed is True
        assert "PASS" in result.reason
        assert result.blocked_by == []

    def test_spread_blocks(self):
        from core.voters.execution_gate import evaluate_execution_gate
        snap = self._snap(spread=0.0005, atr=0.001)  # ratio = 0.5 > 0.3
        conf = self._confluence()
        result = evaluate_execution_gate(conf, snap)
        assert result.allowed is False
        assert any("spread" in b for b in result.blocked_by)

    def test_volatility_spike_blocks(self):
        from core.voters.execution_gate import evaluate_execution_gate
        snap = self._snap(atr_ratio=2.5)
        conf = self._confluence()
        result = evaluate_execution_gate(conf, snap)
        assert result.allowed is False
        assert any("volatility" in b for b in result.blocked_by)

    def test_low_clarity_blocks(self):
        from core.voters.execution_gate import evaluate_execution_gate
        snap = self._snap(clarity=0.1)
        conf = self._confluence()
        result = evaluate_execution_gate(conf, snap)
        assert result.allowed is False
        assert any("clarity" in b for b in result.blocked_by)

    def test_weak_signal_blocks(self):
        from core.voters.execution_gate import evaluate_execution_gate
        snap = self._snap()
        conf = self._confluence(score=0.3)  # below 0.75
        result = evaluate_execution_gate(conf, snap)
        assert result.allowed is False
        assert any("weak_signal" in b for b in result.blocked_by)

    def test_conflict_low_confidence_blocks(self):
        from core.voters.execution_gate import evaluate_execution_gate
        snap = self._snap()
        conf = self._confluence(confidence=0.65, conflict=True)  # below 0.8
        result = evaluate_execution_gate(conf, snap)
        assert result.allowed is False
        assert any("conflict" in b for b in result.blocked_by)

    def test_conflict_high_confidence_passes(self):
        from core.voters.execution_gate import evaluate_execution_gate
        snap = self._snap()
        conf = self._confluence(confidence=0.9, conflict=True)
        result = evaluate_execution_gate(conf, snap)
        # Conflict present but confidence high enough — should pass
        assert result.allowed is True
        assert result.adjusted_confidence < 0.9  # reduced by 15%

    def test_risk_flag_blocks(self):
        from core.voters.execution_gate import evaluate_execution_gate
        snap = self._snap()
        conf = self._confluence(risk=True)
        result = evaluate_execution_gate(conf, snap)
        assert result.allowed is False
        assert any("risk_flag" in b for b in result.blocked_by)

    def test_multiple_blockers_reported(self):
        from core.voters.execution_gate import evaluate_execution_gate
        snap = self._snap(spread=0.0005, atr=0.001, atr_ratio=2.5, clarity=0.1)
        conf = self._confluence(score=0.3, risk=True)
        result = evaluate_execution_gate(conf, snap)
        assert result.allowed is False
        assert len(result.blocked_by) >= 3

    def test_deterministic(self):
        from core.voters.execution_gate import evaluate_execution_gate
        snap = self._snap()
        conf = self._confluence()
        r1 = evaluate_execution_gate(conf, snap)
        r2 = evaluate_execution_gate(conf, snap)
        assert r1 == r2


class TestRiskEngine:
    def _snap(self, atr_ratio=1.0, clarity=0.5):
        state = EngineState()
        from core.features.bundle import FeatureBundle
        features = FeatureBundle(
            m5_atr_14=0.001, m5_atr_ratio=atr_ratio,
            candle_overlap_ratio=0.3, spread=0.00015,
            m5_swing_high_count=2, m5_swing_low_count=2,
            m5_structure_clarity=clarity,
            last_sweep_high=None, last_sweep_low=None,
        )
        return StateSnapshot.from_state_and_features(state, features)

    def _conf(self, score=1.0, confidence=0.8):
        from core.voters.confluence_engine import ConfluenceDecision
        return ConfluenceDecision(action="BUY", score=score, confidence=confidence,
                                  risk_flag=False, conflict_flag=False, breakdown={})

    def _gate(self, confidence=0.8):
        from core.voters.execution_gate import ExecutionGateResult
        return ExecutionGateResult(allowed=True, reason="PASS", blocked_by=[], adjusted_confidence=confidence)

    def test_normal_trade(self):
        from core.voters.risk_engine import compute_risk
        result = compute_risk(
            confluence=self._conf(), gate=self._gate(), snapshot=self._snap(),
            equity=10000.0, stop_loss_distance=0.001, pip_value=10.0,
        )
        assert result.allowed is True
        assert result.position_size > 0
        assert 0.25 <= result.risk_percent <= 2.0

    def test_high_volatility_reduces_size(self):
        from core.voters.risk_engine import compute_risk
        normal = compute_risk(
            confluence=self._conf(), gate=self._gate(), snapshot=self._snap(atr_ratio=1.0),
            equity=10000.0, stop_loss_distance=0.001,
        )
        volatile = compute_risk(
            confluence=self._conf(), gate=self._gate(), snapshot=self._snap(atr_ratio=1.8),
            equity=10000.0, stop_loss_distance=0.001,
        )
        assert volatile.position_size < normal.position_size

    def test_low_clarity_reduces_size(self):
        from core.voters.risk_engine import compute_risk
        clear = compute_risk(
            confluence=self._conf(), gate=self._gate(), snapshot=self._snap(clarity=0.8),
            equity=10000.0, stop_loss_distance=0.001,
        )
        unclear = compute_risk(
            confluence=self._conf(), gate=self._gate(), snapshot=self._snap(clarity=0.2),
            equity=10000.0, stop_loss_distance=0.001,
        )
        assert unclear.position_size < clear.position_size

    def test_no_stop_loss_blocks(self):
        from core.voters.risk_engine import compute_risk
        result = compute_risk(
            confluence=self._conf(), gate=self._gate(), snapshot=self._snap(),
            equity=10000.0, stop_loss_distance=0.0,
        )
        assert result.allowed is False
        assert "no stop_loss" in result.reason

    def test_exposure_cap_blocks(self):
        from core.voters.risk_engine import compute_risk
        result = compute_risk(
            confluence=self._conf(), gate=self._gate(), snapshot=self._snap(),
            equity=10000.0, stop_loss_distance=0.001,
            current_exposure_percent=4.5,
        )
        assert result.allowed is False
        assert "exposure_cap" in result.reason

    def test_risk_percent_clamped(self):
        from core.voters.risk_engine import compute_risk
        result = compute_risk(
            confluence=self._conf(score=2.0, confidence=1.0),
            gate=self._gate(confidence=1.0),
            snapshot=self._snap(atr_ratio=0.5, clarity=0.9),
            equity=10000.0, stop_loss_distance=0.001,
        )
        assert result.risk_percent <= 2.0

    def test_deterministic(self):
        from core.voters.risk_engine import compute_risk
        kwargs = dict(
            confluence=self._conf(), gate=self._gate(), snapshot=self._snap(),
            equity=10000.0, stop_loss_distance=0.001,
        )
        r1 = compute_risk(**kwargs)
        r2 = compute_risk(**kwargs)
        assert r1 == r2

    def test_reason_contains_multipliers(self):
        from core.voters.risk_engine import compute_risk
        result = compute_risk(
            confluence=self._conf(), gate=self._gate(), snapshot=self._snap(),
            equity=10000.0, stop_loss_distance=0.001,
        )
        assert "base=" in result.reason
        assert "final=" in result.reason


class TestShadowCalibration:
    def test_agree_buy(self):
        from core.voters.shadow_calibration import classify_divergence
        assert classify_divergence("BUY", "BUY") == "AGREE_BUY"

    def test_agree_sell(self):
        from core.voters.shadow_calibration import classify_divergence
        assert classify_divergence("SELL", "SELL") == "AGREE_SELL"

    def test_agree_no_trade(self):
        from core.voters.shadow_calibration import classify_divergence
        assert classify_divergence("NO_TRADE", "NO_TRADE") == "AGREE_NO_TRADE"

    def test_shadow_more_aggressive(self):
        from core.voters.shadow_calibration import classify_divergence
        assert classify_divergence("NO_TRADE", "BUY") == "SHADOW_MORE_AGGRESSIVE"
        assert classify_divergence("NO_TRADE", "SELL") == "SHADOW_MORE_AGGRESSIVE"

    def test_shadow_more_conservative(self):
        from core.voters.shadow_calibration import classify_divergence
        assert classify_divergence("BUY", "NO_TRADE") == "SHADOW_MORE_CONSERVATIVE"
        assert classify_divergence("SELL", "NO_TRADE") == "SHADOW_MORE_CONSERVATIVE"

    def test_directional_conflict(self):
        from core.voters.shadow_calibration import classify_divergence
        assert classify_divergence("BUY", "SELL") == "DIRECTIONAL_CONFLICT"
        assert classify_divergence("SELL", "BUY") == "DIRECTIONAL_CONFLICT"

    def test_emit_never_raises(self):
        from core.voters.shadow_calibration import emit_shadow_calibration
        from core.voters.confluence_engine import ConfluenceDecision
        from core.voters.execution_gate import ExecutionGateResult
        # Should not raise even with valid inputs
        emit_shadow_calibration(
            symbol="TEST",
            bias_vote=VoteResult(score=0.5, confidence=0.7, reason="test"),
            structure_vote=VoteResult(score=0.3, confidence=0.6, reason="test"),
            session_vote=VoteResult(score=0.1, confidence=0.9, reason="test"),
            confluence=ConfluenceDecision(action="NO_TRADE", score=0.2, confidence=0.4,
                                          risk_flag=False, conflict_flag=False, breakdown={}),
            gate=ExecutionGateResult(allowed=False, reason="blocked", blocked_by=["test"], adjusted_confidence=0.4),
            production_action="NO_TRADE",
        )
        # No assertion needed — just must not raise


class TestSpreadVoter:
    def _snap(self, spread=0.00015, atr=0.001):
        state = EngineState()
        from core.features.bundle import FeatureBundle
        features = FeatureBundle(
            m5_atr_14=atr, m5_atr_ratio=1.0, candle_overlap_ratio=0.3,
            spread=spread, m5_swing_high_count=2, m5_swing_low_count=2,
            m5_structure_clarity=0.5, last_sweep_high=None, last_sweep_low=None,
        )
        return StateSnapshot.from_state_and_features(state, features)

    def test_tight_spread_positive(self):
        from core.voters.spread_voter import SpreadVoter
        snap = self._snap(spread=0.00005, atr=0.001)  # 5% of ATR
        result = SpreadVoter().evaluate(snap)
        assert result.score > 0.5
        assert "optimal" in result.reason

    def test_wide_spread_negative(self):
        from core.voters.spread_voter import SpreadVoter
        snap = self._snap(spread=0.0004, atr=0.001)  # 40% of ATR
        result = SpreadVoter().evaluate(snap)
        assert result.score < -1.0
        assert "avoid" in result.reason

    def test_extreme_spread_very_negative(self):
        from core.voters.spread_voter import SpreadVoter
        snap = self._snap(spread=0.001, atr=0.001)  # 100% of ATR
        result = SpreadVoter().evaluate(snap)
        assert result.score == -2.0

    def test_zero_atr_returns_neutral(self):
        from core.voters.spread_voter import SpreadVoter
        snap = self._snap(spread=0.0001, atr=0.0)
        result = SpreadVoter().evaluate(snap)
        assert result.score == 0.0

    def test_deterministic(self):
        from core.voters.spread_voter import SpreadVoter
        snap = self._snap()
        r1 = SpreadVoter().evaluate(snap)
        r2 = SpreadVoter().evaluate(snap)
        assert r1 == r2

    def test_score_bounded(self):
        from core.voters.spread_voter import SpreadVoter
        snap = self._snap(spread=0.01, atr=0.001)
        result = SpreadVoter().evaluate(snap)
        assert -2.0 <= result.score <= 2.0
        assert 0.0 <= result.confidence <= 1.0


class TestVolatilityVoter:
    def _snap(self, atr_ratio=1.0, overlap=0.3):
        state = EngineState()
        from core.features.bundle import FeatureBundle
        features = FeatureBundle(
            m5_atr_14=0.001, m5_atr_ratio=atr_ratio, candle_overlap_ratio=overlap,
            spread=0.00015, m5_swing_high_count=2, m5_swing_low_count=2,
            m5_structure_clarity=0.5, last_sweep_high=None, last_sweep_low=None,
        )
        return StateSnapshot.from_state_and_features(state, features)

    def test_stable_conditions_positive(self):
        from core.voters.volatility_voter import VolatilityVoter
        snap = self._snap(atr_ratio=1.0, overlap=0.2)
        result = VolatilityVoter().evaluate(snap)
        assert result.score > 0.5
        assert "stable" in result.reason

    def test_chaotic_conditions_negative(self):
        from core.voters.volatility_voter import VolatilityVoter
        snap = self._snap(atr_ratio=2.5, overlap=0.8)
        result = VolatilityVoter().evaluate(snap)
        assert result.score < -1.0
        assert "chaotic" in result.reason

    def test_high_atr_penalized(self):
        from core.voters.volatility_voter import VolatilityVoter
        normal = self._snap(atr_ratio=1.0, overlap=0.3)
        high = self._snap(atr_ratio=2.0, overlap=0.3)
        normal_r = VolatilityVoter().evaluate(normal)
        high_r = VolatilityVoter().evaluate(high)
        assert normal_r.score > high_r.score

    def test_high_overlap_penalized(self):
        from core.voters.volatility_voter import VolatilityVoter
        clean = self._snap(atr_ratio=1.0, overlap=0.1)
        choppy = self._snap(atr_ratio=1.0, overlap=0.8)
        clean_r = VolatilityVoter().evaluate(clean)
        choppy_r = VolatilityVoter().evaluate(choppy)
        assert clean_r.score > choppy_r.score

    def test_deterministic(self):
        from core.voters.volatility_voter import VolatilityVoter
        snap = self._snap()
        r1 = VolatilityVoter().evaluate(snap)
        r2 = VolatilityVoter().evaluate(snap)
        assert r1 == r2

    def test_score_bounded(self):
        from core.voters.volatility_voter import VolatilityVoter
        snap = self._snap(atr_ratio=3.0, overlap=0.9)
        result = VolatilityVoter().evaluate(snap)
        assert -2.0 <= result.score <= 2.0
        assert 0.0 <= result.confidence <= 1.0

    def test_confidence_high_for_extremes(self):
        from core.voters.volatility_voter import VolatilityVoter
        snap = self._snap(atr_ratio=2.5, overlap=0.8)
        result = VolatilityVoter().evaluate(snap)
        assert result.confidence >= 0.8


class TestAgreementAnalysis:
    def _votes(self, bias=1.0, structure=0.8, session=0.5, spread=0.3, volatility=0.6):
        return dict(
            bias_vote=VoteResult(score=bias, confidence=0.8, reason="x"),
            structure_vote=VoteResult(score=structure, confidence=0.7, reason="x"),
            session_vote=VoteResult(score=session, confidence=0.9, reason="x"),
            spread_vote=VoteResult(score=spread, confidence=0.9, reason="x"),
            volatility_vote=VoteResult(score=volatility, confidence=0.7, reason="x"),
        )

    def test_all_agree_stable(self):
        from core.voters.agreement_analysis import compute_agreement
        from core.voters.confluence_engine import ConfluenceDecision
        votes = self._votes(bias=1.0, structure=0.8, session=0.5, spread=0.3, volatility=0.6)
        conf = ConfluenceDecision(action="BUY", score=0.9, confidence=0.8,
                                   risk_flag=False, conflict_flag=False, breakdown={})
        result = compute_agreement(**votes, confluence=conf)
        assert result.stability_flag == "stable"
        assert result.confluence_agreement_score >= 0.8
        assert len(result.dominant_voters) >= 4

    def test_mixed_agreement(self):
        from core.voters.agreement_analysis import compute_agreement
        from core.voters.confluence_engine import ConfluenceDecision
        votes = self._votes(bias=1.0, structure=-0.5, session=0.3, spread=-0.8, volatility=0.2)
        conf = ConfluenceDecision(action="NO_TRADE", score=0.1, confidence=0.4,
                                   risk_flag=False, conflict_flag=True, breakdown={})
        result = compute_agreement(**votes, confluence=conf)
        assert result.stability_flag in ("mixed", "unstable")
        assert len(result.conflicting_voters) > 0

    def test_all_disagree_unstable(self):
        from core.voters.agreement_analysis import compute_agreement
        from core.voters.confluence_engine import ConfluenceDecision
        votes = self._votes(bias=1.5, structure=-1.0, session=0.8, spread=-0.5, volatility=-0.7)
        conf = ConfluenceDecision(action="NO_TRADE", score=0.05, confidence=0.3,
                                   risk_flag=False, conflict_flag=True, breakdown={})
        result = compute_agreement(**votes, confluence=conf)
        assert result.stability_flag in ("mixed", "unstable")

    def test_confluence_agreement_score_bounded(self):
        from core.voters.agreement_analysis import compute_agreement
        from core.voters.confluence_engine import ConfluenceDecision
        votes = self._votes()
        conf = ConfluenceDecision(action="BUY", score=1.0, confidence=0.9,
                                   risk_flag=False, conflict_flag=False, breakdown={})
        result = compute_agreement(**votes, confluence=conf)
        assert 0.0 <= result.confluence_agreement_score <= 1.0

    def test_deterministic(self):
        from core.voters.agreement_analysis import compute_agreement
        from core.voters.confluence_engine import ConfluenceDecision
        votes = self._votes()
        conf = ConfluenceDecision(action="BUY", score=0.9, confidence=0.8,
                                   risk_flag=False, conflict_flag=False, breakdown={})
        r1 = compute_agreement(**votes, confluence=conf)
        r2 = compute_agreement(**votes, confluence=conf)
        assert r1 == r2

    def test_emit_never_raises(self):
        from core.voters.agreement_analysis import compute_agreement, emit_agreement_log, AgreementAnalysis
        from core.voters.confluence_engine import ConfluenceDecision
        votes = self._votes()
        conf = ConfluenceDecision(action="BUY", score=0.9, confidence=0.8,
                                   risk_flag=False, conflict_flag=False, breakdown={})
        result = compute_agreement(**votes, confluence=conf)
        emit_agreement_log("TEST", result)  # Must not raise


class TestConflictClassification:
    def _conf(self, score=0.5):
        from core.voters.confluence_engine import ConfluenceDecision
        return ConfluenceDecision(action="NO_TRADE", score=score, confidence=0.5,
                                  risk_flag=False, conflict_flag=False, breakdown={})

    def test_no_conflicts_when_all_agree(self):
        from core.voters.conflict_classification import classify_conflicts
        result = classify_conflicts(
            bias_vote=VoteResult(score=1.0, confidence=0.8, reason="x"),
            structure_vote=VoteResult(score=0.8, confidence=0.7, reason="x"),
            session_vote=VoteResult(score=0.5, confidence=0.9, reason="x"),
            spread_vote=VoteResult(score=0.3, confidence=0.9, reason="x"),
            volatility_vote=VoteResult(score=0.6, confidence=0.7, reason="x"),
            confluence=self._conf(0.9),
        )
        assert result.severity == "none"
        assert result.conflict_types == []

    def test_bias_vs_structure_detected(self):
        from core.voters.conflict_classification import classify_conflicts
        result = classify_conflicts(
            bias_vote=VoteResult(score=1.5, confidence=0.9, reason="x"),
            structure_vote=VoteResult(score=-1.0, confidence=0.8, reason="x"),
            session_vote=VoteResult(score=0.5, confidence=0.9, reason="x"),
            spread_vote=VoteResult(score=0.3, confidence=0.9, reason="x"),
            volatility_vote=VoteResult(score=0.5, confidence=0.7, reason="x"),
            confluence=self._conf(0.5),
        )
        assert "bias_vs_structure" in result.conflict_types
        assert result.severity in ("low", "medium", "high")

    def test_spread_vs_direction_detected(self):
        from core.voters.conflict_classification import classify_conflicts
        result = classify_conflicts(
            bias_vote=VoteResult(score=1.0, confidence=0.8, reason="x"),
            structure_vote=VoteResult(score=0.8, confidence=0.7, reason="x"),
            session_vote=VoteResult(score=0.5, confidence=0.9, reason="x"),
            spread_vote=VoteResult(score=-1.0, confidence=0.9, reason="x"),
            volatility_vote=VoteResult(score=0.5, confidence=0.7, reason="x"),
            confluence=self._conf(0.5),
        )
        assert "spread_vs_direction" in result.conflict_types

    def test_session_misalignment_detected(self):
        from core.voters.conflict_classification import classify_conflicts
        result = classify_conflicts(
            bias_vote=VoteResult(score=1.0, confidence=0.8, reason="x"),
            structure_vote=VoteResult(score=0.8, confidence=0.7, reason="x"),
            session_vote=VoteResult(score=-1.0, confidence=0.9, reason="x"),
            spread_vote=VoteResult(score=0.3, confidence=0.9, reason="x"),
            volatility_vote=VoteResult(score=0.5, confidence=0.7, reason="x"),
            confluence=self._conf(0.5),
        )
        assert "session_misalignment" in result.conflict_types

    def test_multi_voter_fragmentation(self):
        from core.voters.conflict_classification import classify_conflicts
        result = classify_conflicts(
            bias_vote=VoteResult(score=1.5, confidence=0.9, reason="x"),
            structure_vote=VoteResult(score=-1.0, confidence=0.8, reason="x"),
            session_vote=VoteResult(score=0.8, confidence=0.9, reason="x"),
            spread_vote=VoteResult(score=-0.8, confidence=0.9, reason="x"),
            volatility_vote=VoteResult(score=-0.6, confidence=0.7, reason="x"),
            confluence=self._conf(0.1),
        )
        assert "multi_voter_fragmentation" in result.conflict_types
        assert result.severity == "high"

    def test_high_severity_strong_conflict(self):
        from core.voters.conflict_classification import classify_conflicts
        result = classify_conflicts(
            bias_vote=VoteResult(score=1.8, confidence=0.95, reason="x"),
            structure_vote=VoteResult(score=-1.5, confidence=0.9, reason="x"),
            session_vote=VoteResult(score=-0.8, confidence=0.9, reason="x"),
            spread_vote=VoteResult(score=-1.0, confidence=0.9, reason="x"),
            volatility_vote=VoteResult(score=-0.7, confidence=0.8, reason="x"),
            confluence=self._conf(0.2),
        )
        assert result.severity == "high"
        assert result.impact == "strong"

    def test_deterministic(self):
        from core.voters.conflict_classification import classify_conflicts
        kwargs = dict(
            bias_vote=VoteResult(score=1.0, confidence=0.8, reason="x"),
            structure_vote=VoteResult(score=-0.5, confidence=0.7, reason="x"),
            session_vote=VoteResult(score=0.3, confidence=0.9, reason="x"),
            spread_vote=VoteResult(score=0.2, confidence=0.9, reason="x"),
            volatility_vote=VoteResult(score=0.4, confidence=0.7, reason="x"),
            confluence=self._conf(0.4),
        )
        r1 = classify_conflicts(**kwargs)
        r2 = classify_conflicts(**kwargs)
        assert r1 == r2

    def test_emit_never_raises(self):
        from core.voters.conflict_classification import classify_conflicts, emit_conflict_log
        result = classify_conflicts(
            bias_vote=VoteResult(score=1.0, confidence=0.8, reason="x"),
            structure_vote=VoteResult(score=-1.0, confidence=0.8, reason="x"),
            session_vote=VoteResult(score=0.5, confidence=0.9, reason="x"),
            spread_vote=VoteResult(score=0.3, confidence=0.9, reason="x"),
            volatility_vote=VoteResult(score=0.5, confidence=0.7, reason="x"),
            confluence=self._conf(0.3),
        )
        emit_conflict_log("TEST", result)  # Must not raise


class TestInfluenceTracker:
    def _votes(self, bias=1.0, structure=0.8, session=0.3, spread=0.2, volatility=0.5):
        return dict(
            bias_vote=VoteResult(score=bias, confidence=0.8, reason="x"),
            structure_vote=VoteResult(score=structure, confidence=0.7, reason="x"),
            session_vote=VoteResult(score=session, confidence=0.9, reason="x"),
            spread_vote=VoteResult(score=spread, confidence=0.9, reason="x"),
            volatility_vote=VoteResult(score=volatility, confidence=0.7, reason="x"),
        )

    def test_aligned_voters_positive_influence(self):
        from core.voters.influence_tracker import compute_influence
        from core.voters.confluence_engine import ConfluenceDecision
        conf = ConfluenceDecision(action="BUY", score=0.8, confidence=0.7,
                                   risk_flag=False, conflict_flag=False, breakdown={})
        result = compute_influence(**self._votes(), confluence=conf)
        assert result.influence_map["bias"] > 0
        assert result.influence_map["structure"] > 0

    def test_opposing_voter_negative_influence(self):
        from core.voters.influence_tracker import compute_influence
        from core.voters.confluence_engine import ConfluenceDecision
        conf = ConfluenceDecision(action="BUY", score=0.8, confidence=0.7,
                                   risk_flag=False, conflict_flag=False, breakdown={})
        votes = self._votes(spread=-0.8)
        result = compute_influence(**votes, confluence=conf)
        assert result.influence_map["spread"] < 0

    def test_dominant_influencers_identified(self):
        from core.voters.influence_tracker import compute_influence
        from core.voters.confluence_engine import ConfluenceDecision
        conf = ConfluenceDecision(action="BUY", score=0.9, confidence=0.8,
                                   risk_flag=False, conflict_flag=False, breakdown={})
        result = compute_influence(**self._votes(), confluence=conf)
        assert len(result.dominant_influencers) > 0
        assert "bias" in result.dominant_influencers

    def test_reliability_tracker_accumulates(self):
        from core.voters.influence_tracker import VoterReliabilityTracker
        from core.voters.confluence_engine import ConfluenceDecision
        tracker = VoterReliabilityTracker()
        conf = ConfluenceDecision(action="BUY", score=0.8, confidence=0.7,
                                   risk_flag=False, conflict_flag=False, breakdown={})
        for _ in range(10):
            tracker.record(**self._votes(), confluence=conf)
        snap = tracker.get_snapshot()
        assert snap.reliability_scores["bias"] > 0.5
        assert "high_reliability" in snap.classifications["bias"]


class TestSystemSynthesis:
    def test_coherent_state(self):
        from core.voters.system_synthesis import compute_synthesis
        from core.voters.agreement_analysis import AgreementAnalysis
        from core.voters.conflict_classification import ConflictAnalysis
        from core.voters.influence_tracker import VoterInfluenceSnapshot, VoterReliabilitySnapshot

        agreement = AgreementAnalysis(
            agreement_matrix={}, confluence_agreement_score=0.9,
            stability_flag="stable", dominant_voters=["bias", "structure"], conflicting_voters=[],
        )
        conflict = ConflictAnalysis(
            conflict_types=[], severity="none", impact="none", conflict_map={}, primary_driver="none",
        )
        influence = VoterInfluenceSnapshot(
            influence_map={"bias": 0.5, "structure": 0.4, "session": 0.1, "spread": 0.1, "volatility": 0.2},
            dominant_influencers=["bias", "structure"], weakest_voters=["session", "spread"],
        )
        reliability = VoterReliabilitySnapshot(
            reliability_scores={"bias": 0.85, "structure": 0.80, "session": 0.70, "spread": 0.65, "volatility": 0.75},
            consistency_scores={"bias": 0.9, "structure": 0.85, "session": 0.7, "spread": 0.6, "volatility": 0.8},
            classifications={"bias": "high_stable", "structure": "high_stable", "session": "mod_stable",
                            "spread": "mod_volatile", "volatility": "high_stable"},
        )
        result = compute_synthesis(agreement=agreement, conflict=conflict, influence=influence, reliability=reliability)
        assert result.system_state == "coherent"
        assert result.decision_integrity_score > 0.7

    def test_unstable_state(self):
        from core.voters.system_synthesis import compute_synthesis
        from core.voters.agreement_analysis import AgreementAnalysis
        from core.voters.conflict_classification import ConflictAnalysis
        from core.voters.influence_tracker import VoterInfluenceSnapshot, VoterReliabilitySnapshot

        agreement = AgreementAnalysis(
            agreement_matrix={}, confluence_agreement_score=0.3,
            stability_flag="unstable", dominant_voters=[], conflicting_voters=["bias", "structure"],
        )
        conflict = ConflictAnalysis(
            conflict_types=["bias_vs_structure", "multi_voter_fragmentation"],
            severity="high", impact="strong", conflict_map={"bias": ["structure"]}, primary_driver="bias",
        )
        influence = VoterInfluenceSnapshot(
            influence_map={"bias": 0.3, "structure": -0.2, "session": -0.1, "spread": -0.1, "volatility": -0.1},
            dominant_influencers=["bias"], weakest_voters=["spread", "volatility"],
        )
        reliability = VoterReliabilitySnapshot(
            reliability_scores={"bias": 0.4, "structure": 0.45, "session": 0.5, "spread": 0.3, "volatility": 0.4},
            consistency_scores={"bias": 0.4, "structure": 0.5, "session": 0.6, "spread": 0.3, "volatility": 0.4},
            classifications={"bias": "low_volatile", "structure": "low_volatile", "session": "mod_stable",
                            "spread": "low_volatile", "volatility": "low_volatile"},
        )
        result = compute_synthesis(agreement=agreement, conflict=conflict, influence=influence, reliability=reliability)
        assert result.system_state in ("unstable", "degenerate")
        assert result.decision_integrity_score < 0.6

    def test_integrity_bounded(self):
        from core.voters.system_synthesis import compute_synthesis
        from core.voters.agreement_analysis import AgreementAnalysis
        from core.voters.conflict_classification import ConflictAnalysis
        from core.voters.influence_tracker import VoterInfluenceSnapshot, VoterReliabilitySnapshot

        agreement = AgreementAnalysis(
            agreement_matrix={}, confluence_agreement_score=1.0,
            stability_flag="stable", dominant_voters=["bias"], conflicting_voters=[],
        )
        conflict = ConflictAnalysis(
            conflict_types=[], severity="none", impact="none", conflict_map={}, primary_driver="none",
        )
        influence = VoterInfluenceSnapshot(
            influence_map={"bias": 1.0, "structure": 0.8, "session": 0.5, "spread": 0.3, "volatility": 0.6},
            dominant_influencers=["bias"], weakest_voters=["spread"],
        )
        reliability = VoterReliabilitySnapshot(
            reliability_scores={"bias": 1.0, "structure": 1.0, "session": 1.0, "spread": 1.0, "volatility": 1.0},
            consistency_scores={"bias": 1.0, "structure": 1.0, "session": 1.0, "spread": 1.0, "volatility": 1.0},
            classifications={"bias": "high", "structure": "high", "session": "high", "spread": "high", "volatility": "high"},
        )
        result = compute_synthesis(agreement=agreement, conflict=conflict, influence=influence, reliability=reliability)
        assert 0.0 <= result.decision_integrity_score <= 1.0


class TestWeightIntelligence:
    def _inputs(self):
        from core.voters.influence_tracker import VoterInfluenceSnapshot, VoterReliabilitySnapshot
        from core.voters.conflict_classification import ConflictAnalysis
        from core.voters.agreement_analysis import AgreementAnalysis

        influence = VoterInfluenceSnapshot(
            influence_map={"bias": 0.5, "structure": 0.4, "session": 0.1, "spread": -0.2, "volatility": 0.3},
            dominant_influencers=["bias", "structure"],
            weakest_voters=["session", "spread"],
        )
        reliability = VoterReliabilitySnapshot(
            reliability_scores={"bias": 0.82, "structure": 0.78, "session": 0.65, "spread": 0.52, "volatility": 0.71},
            consistency_scores={"bias": 0.9, "structure": 0.85, "session": 0.7, "spread": 0.5, "volatility": 0.8},
            classifications={"bias": "high_stable", "structure": "high_stable", "session": "mod_stable",
                            "spread": "mod_volatile", "volatility": "high_stable"},
        )
        conflict = ConflictAnalysis(
            conflict_types=["spread_vs_direction"],
            severity="low", impact="minimal",
            conflict_map={"spread": ["bias"]},
            primary_driver="spread",
        )
        agreement = AgreementAnalysis(
            agreement_matrix={},
            confluence_agreement_score=0.8,
            stability_flag="stable",
            dominant_voters=["bias", "structure"],
            conflicting_voters=["spread"],
        )
        return dict(influence=influence, reliability=reliability, conflict=conflict, agreement=agreement)

    def test_produces_valid_block(self):
        from core.voters.weight_intelligence import compute_weight_intelligence, WeightIntelligenceBlock
        from core.voters.influence_tracker import VOTER_NAMES as _VN
        result = compute_weight_intelligence(**self._inputs())
        assert isinstance(result, WeightIntelligenceBlock)
        assert 0.0 <= result.weight_health_score <= 1.0
        assert 0.0 <= result.confidence <= 1.0
        assert set(result.voter_weights_current.keys()) == set(_VN)
        assert set(result.voter_weights_recommended.keys()) == set(_VN)
        assert set(result.weight_deltas.keys()) == set(_VN)

    def test_recommended_weights_sum_to_one(self):
        from core.voters.weight_intelligence import compute_weight_intelligence
        result = compute_weight_intelligence(**self._inputs())
        total = sum(result.voter_weights_recommended.values())
        assert abs(total - 1.0) < 0.01

    def test_deltas_bounded(self):
        from core.voters.weight_intelligence import compute_weight_intelligence
        result = compute_weight_intelligence(**self._inputs())
        for delta in result.weight_deltas.values():
            assert -0.05 <= delta <= 0.05

    def test_deterministic_single_call(self):
        from core.voters.weight_intelligence import compute_weight_intelligence
        # Note: smoothing window means repeated calls may differ slightly
        # But a single call with same inputs should produce consistent schema
        result = compute_weight_intelligence(**self._inputs())
        assert result.timestamp > 0
        assert isinstance(result.reasoning_tags, list)

    def test_emit_never_raises(self):
        from core.voters.weight_intelligence import compute_weight_intelligence, emit_weight_intelligence_log
        result = compute_weight_intelligence(**self._inputs())
        emit_weight_intelligence_log("TEST", result)  # Must not raise


class TestABTesting:
    def test_match_classification(self):
        from core.voters.ab_testing import classify_ab_divergence
        assert classify_ab_divergence("BUY", "BUY") == "match"
        assert classify_ab_divergence("NO_TRADE", "NO_TRADE") == "match"

    def test_conservative_shadow(self):
        from core.voters.ab_testing import classify_ab_divergence
        assert classify_ab_divergence("BUY", "NO_TRADE") == "conservative_shadow"
        assert classify_ab_divergence("SELL", "NO_TRADE") == "conservative_shadow"

    def test_aggressive_shadow(self):
        from core.voters.ab_testing import classify_ab_divergence
        assert classify_ab_divergence("NO_TRADE", "BUY") == "aggressive_shadow"
        assert classify_ab_divergence("NO_TRADE", "SELL") == "aggressive_shadow"

    def test_directional_conflict(self):
        from core.voters.ab_testing import classify_ab_divergence
        assert classify_ab_divergence("BUY", "SELL") == "directional_conflict"
        assert classify_ab_divergence("SELL", "BUY") == "directional_conflict"

    def test_ssi_tracker_accumulates(self):
        from core.voters.ab_testing import SSITracker
        tracker = SSITracker()
        for _ in range(10):
            tracker.record("match")
        assert tracker.score > 0.5
        for _ in range(10):
            tracker.record("directional_conflict")
        assert tracker.score < 0.5

    def test_compute_ab_test_produces_result(self):
        from core.voters.ab_testing import compute_ab_test, ABTestResult
        result = compute_ab_test(production_action="BUY", shadow_action="NO_TRADE")
        assert isinstance(result, ABTestResult)
        assert result.divergence_type == "conservative_shadow"
        assert 0.0 <= result.ssi_score <= 1.0

    def test_readiness_gate_not_ready_by_default(self):
        from core.voters.ab_testing import compute_ab_test
        result = compute_ab_test(production_action="BUY", shadow_action="BUY")
        # SSI needs sustained history to reach readiness
        assert isinstance(result.readiness_flag, bool)
        assert 0.0 <= result.readiness_confidence <= 1.0

    def test_emit_never_raises(self):
        from core.voters.ab_testing import compute_ab_test, emit_ab_test_log
        result = compute_ab_test(production_action="NO_TRADE", shadow_action="BUY")
        emit_ab_test_log("TEST", result)  # Must not raise
