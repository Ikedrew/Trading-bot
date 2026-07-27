"""Tests for Shadow EV Models — isolation, correctness, edge cases."""
from __future__ import annotations
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_engine.shadow_ev.models import (
    compute_shadow_ev, ShadowEVAssessment,
    _existing_model, _model_a, _model_b, _model_c,
)


def _trace(pattern="TWEEZER_BOTTOM", score=0.55, regime="TRANSITIONAL", strat_conf=0.0, confirmation=1.0):
    return {
        "entity_id": "EURUSD_1000000",
        "symbol": "EURUSD",
        "timestamp_utc": "2026-07-17T01:00:00Z",
        "pattern_detected": True,
        "pattern_name": pattern,
        "score_neutral": score,
        "score_strategy": score,
        "strategy_confidence": strat_conf,
        "confirmation_score": confirmation,
        "regime": regime,
        "market_state": regime,
        "components": {"pattern_quality": 0.5},
    }


class TestExistingModelReplication:
    def test_matches_known_output(self):
        """Production model: score=0.55, strat_conf=0 → p≈0.264, EV negative at RR=2."""
        t = _trace(score=0.55, pattern="TWEEZER_BOTTOM")
        p, ev = _existing_model(t)
        assert 0.20 < p < 0.35  # Suppressed by strat_conf=0
        assert ev < 0  # Negative at RR=2.0

    def test_rr3_pattern_can_pass(self):
        """THREE_WHITE_SOLDIERS (RR=3) can produce positive EV."""
        t = _trace(pattern="THREE_WHITE_SOLDIERS", score=0.65)
        p, ev = _existing_model(t)
        # With RR=3 and score=0.65: p_base=0.39, dampened → p≈0.31, EV=(0.31*3)-(0.69*1)=0.24
        # Actually: p_base=0.65*0.6=0.39, conf_mod=1.0, damp=0.20 → p=0.39*1.0*0.80=0.312
        assert ev > 0  # Should be positive with RR=3


class TestModelA:
    def test_uses_pattern_win_rate(self):
        """Model A uses empirical pattern win rate."""
        t = _trace(pattern="THREE_INSIDE_DOWN")
        rates = {"THREE_INSIDE_DOWN": 0.46}
        p, ev = _model_a(t, rates)
        # p = 0.46 * (1-0.10) = 0.414, EV = 0.414*2 - 0.586*1 = 0.242
        assert p > 0.35
        assert ev > 0

    def test_unknown_pattern_uses_prior(self):
        """Unknown pattern falls back to 30% prior."""
        t = _trace(pattern="UNKNOWN_XYZ")
        rates = {}
        p, ev = _model_a(t, rates)
        # p = 0.30 * 0.90 = 0.27
        assert 0.20 < p < 0.35
        assert ev < 0  # 0.27*2 - 0.73*1 = -0.19


class TestModelB:
    def test_small_sample_pulled_toward_prior(self):
        """With few samples, Bayesian pulls toward 30% prior."""
        t = _trace(pattern="RARE_PATTERN")
        rates = {"RARE_PATTERN": 0.80}  # High empirical but few samples
        counts = {"RARE_PATTERN": 5}
        p, ev = _model_b(t, rates, counts)
        # posterior = (0.80*5 + 0.30*10) / (5+10) = 7.0/15 = 0.467
        # dampened: 0.467*0.90 = 0.42
        assert p < 0.50  # Pulled down from 0.80
        assert p > 0.30  # But above prior

    def test_large_sample_trusts_empirical(self):
        """With many samples, Bayesian trusts empirical rate."""
        t = _trace(pattern="COMMON")
        rates = {"COMMON": 0.50}
        counts = {"COMMON": 200}
        p, ev = _model_b(t, rates, counts)
        # posterior = (0.50*200 + 0.30*10) / 210 = 103/210 = 0.49
        # dampened: 0.49*0.90 = 0.44
        assert 0.40 < p < 0.50

    def test_no_samples_returns_prior(self):
        """Zero samples → pure prior."""
        t = _trace(pattern="NEVER_SEEN")
        rates = {}
        counts = {}
        p, ev = _model_b(t, rates, counts)
        # prior = 0.30, dampened = 0.27
        assert 0.20 < p < 0.35


class TestModelC:
    def test_conditional_used_when_available(self):
        """Uses regime|pattern conditional when enough samples."""
        t = _trace(pattern="THREE_INSIDE_DOWN", regime="TRANSITIONAL")
        cond_rates = {"TRANSITIONAL|THREE_INSIDE_DOWN": 0.50}
        cond_counts = {"TRANSITIONAL|THREE_INSIDE_DOWN": 30}
        pat_rates = {"THREE_INSIDE_DOWN": 0.46}
        p, ev = _model_c(t, cond_rates, cond_counts, pat_rates)
        # Uses conditional 0.50, dampened to 0.45
        assert p > 0.40
        assert ev > 0

    def test_falls_back_to_pattern_when_conditional_sparse(self):
        """Falls back to pattern rate when conditional has few samples."""
        t = _trace(pattern="THREE_INSIDE_DOWN", regime="TRENDING")
        cond_rates = {"TRENDING|THREE_INSIDE_DOWN": 0.80}
        cond_counts = {"TRENDING|THREE_INSIDE_DOWN": 3}  # Below threshold
        pat_rates = {"THREE_INSIDE_DOWN": 0.46}
        p, ev = _model_c(t, cond_rates, cond_counts, pat_rates)
        # Falls back to pattern rate 0.46, dampened to 0.41
        assert 0.35 < p < 0.50


class TestComputeShadowEV:
    def test_produces_all_fields(self):
        """Full assessment has all model outputs."""
        t = _trace()
        a = compute_shadow_ev(t, {"TWEEZER_BOTTOM": 0.34}, {"TWEEZER_BOTTOM": 50}, {}, {})
        assert a.existing_action in ("EXECUTE", "NO_TRADE")
        assert a.model_a_action in ("EXECUTE", "NO_TRADE")
        assert a.model_b_action in ("EXECUTE", "NO_TRADE")
        assert a.model_c_action in ("EXECUTE", "NO_TRADE")

    def test_identical_inputs_identical_output(self):
        """Deterministic: same input → same output."""
        t = _trace()
        rates = {"TWEEZER_BOTTOM": 0.34}
        counts = {"TWEEZER_BOTTOM": 50}
        a1 = compute_shadow_ev(t, rates, counts, {}, {})
        a2 = compute_shadow_ev(t, rates, counts, {}, {})
        assert a1.to_dict() == a2.to_dict()

    def test_disagreement_detected(self):
        """Disagreement flagged when models disagree."""
        # THREE_INSIDE_DOWN with 46% win rate: Model A approves, EXISTING rejects
        t = _trace(pattern="THREE_INSIDE_DOWN", score=0.55)
        a = compute_shadow_ev(
            t,
            {"THREE_INSIDE_DOWN": 0.46},
            {"THREE_INSIDE_DOWN": 67},
            {"TRANSITIONAL|THREE_INSIDE_DOWN": 0.46},
            {"TRANSITIONAL|THREE_INSIDE_DOWN": 67},
        )
        # EXISTING: p=0.55*0.6*1.0*0.8=0.264, EV=0.264*2-0.736=-0.208 → NO_TRADE
        # MODEL_A: p=0.46*0.9=0.414, EV=0.414*2-0.586=0.242 → EXECUTE
        assert a.existing_action == "NO_TRADE"
        assert a.model_a_action == "EXECUTE"
        assert a.disagreement is True

    def test_no_production_imports(self):
        """Shadow EV module does not import from production execution."""
        import research_engine.shadow_ev.models as m
        source = Path(m.__file__).read_text(encoding="utf-8")
        # Must not import from core.pipeline, execution, risk
        assert "from core.pipeline" not in source
        assert "from execution" not in source
        assert "from risk" not in source

    def test_to_dict_serializable(self):
        """Assessment serializes to JSON-safe dict."""
        import json
        t = _trace()
        a = compute_shadow_ev(t, {}, {}, {}, {})
        d = a.to_dict()
        json_str = json.dumps(d)
        assert len(json_str) > 0
