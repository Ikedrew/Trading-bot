"""Tests for Dual EV computation (synthetic + empirical)."""
from __future__ import annotations
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.pipeline.expected_value import (
    compute_expected_value,
    compute_dual_ev,
    ExpectedValueResult,
    DualEVComparison,
)
from core.pipeline.market_state_engine import MarketState, MarketStateResult
from core.research_assessment.models import ResearchAssessment, NEUTRAL_ASSESSMENT


def _market_state(state=MarketState.TRANSITIONAL):
    return MarketStateResult(state=state, confidence=0.7, delta_stability=0.5, reasoning="test", flip_rate=0.0, score_consistency=0.8)


def _synthetic_result(p=0.28, ev=-0.05, positive=False, reward=0.004, risk=0.002):
    return ExpectedValueResult(
        ev=ev, p_success=p, p_failure=1-p, reward=reward, risk=risk,
        rr_effective=reward/risk if risk > 0 else 0, uncertainty_dampening=0.20,
        ev_positive=positive, reasoning="test",
    )


class TestDualEVComputation:
    def test_no_match_returns_synthetic_only(self):
        """When no candidate matches, empirical equals synthetic."""
        import core.research_assessment.provider as prov
        prov._candidates_loaded = True
        prov._validated_candidates = []

        synth = _synthetic_result()
        dual = compute_dual_ev(
            synthetic_result=synth,
            pattern_name="UNKNOWN_PATTERN",
            reward=0.004, risk=0.002,
        )

        assert dual.synthetic_p == synth.p_success
        assert dual.synthetic_ev == synth.ev
        assert dual.empirical_p == synth.p_success  # No empirical data → mirrors synthetic
        assert dual.candidate_match is False
        assert dual.execution_difference == "AGREE"

        prov._candidates_loaded = False
        prov._validated_candidates = []

    def test_match_produces_different_empirical(self):
        """When candidate matches, empirical uses research win rate."""
        import core.research_assessment.provider as prov
        prov._candidates_loaded = True
        prov._validated_candidates = [{
            "candidate_id": "EC-TEST",
            "conditions": {"pattern": "TWEEZER_TOP", "bias_alignment_bin": "HIGH"},
            "passes": True,
            "splits_positive": 4, "splits_total": 5,
            "total_trades": 216, "avg_win_rate": 0.42, "avg_ev": 0.246,
        }]

        synth = _synthetic_result(p=0.28, ev=-0.05, positive=False, reward=0.004, risk=0.002)
        dual = compute_dual_ev(
            synthetic_result=synth,
            pattern_name="TWEEZER_TOP",
            regime="TRANSITIONAL",
            components={"bias_alignment": 0.8, "htf_alignment": 0.3},
            reward=0.004, risk=0.002,
        )

        assert dual.candidate_match is True
        assert dual.candidate_id == "EC-TEST"
        assert dual.empirical_p == 0.42  # From research
        assert dual.empirical_p > dual.synthetic_p  # Higher than synthetic
        assert dual.empirical_positive is True  # 0.42*0.004 - 0.58*0.002 = positive
        assert dual.synthetic_positive is False
        assert dual.execution_difference == "RESEARCH_WOULD_EXECUTE"
        assert dual.probability_difference > 0  # empirical > synthetic

        prov._candidates_loaded = False
        prov._validated_candidates = []

    def test_agrees_when_both_negative(self):
        """When both models reject, execution_difference is AGREE."""
        import core.research_assessment.provider as prov
        prov._candidates_loaded = True
        prov._validated_candidates = [{
            "candidate_id": "EC-WEAK",
            "conditions": {"pattern": "BAD_PATTERN"},
            "passes": True,
            "splits_positive": 3, "splits_total": 5,
            "total_trades": 50, "avg_win_rate": 0.20, "avg_ev": -0.1,
        }]

        synth = _synthetic_result(p=0.15, ev=-0.5, positive=False, reward=0.002, risk=0.002)
        dual = compute_dual_ev(
            synthetic_result=synth,
            pattern_name="BAD_PATTERN",
            reward=0.002, risk=0.002,
        )

        # 0.20 * 0.002 - 0.80 * 0.002 = -0.0012 (negative)
        assert dual.empirical_positive is False
        assert dual.execution_difference == "AGREE"

        prov._candidates_loaded = False
        prov._validated_candidates = []

    def test_to_dict_serializable(self):
        """DualEVComparison serializes to JSON-safe dict."""
        import json
        dual = DualEVComparison(
            synthetic_p=0.28, synthetic_ev=-0.05, synthetic_positive=False,
            empirical_p=0.42, empirical_ev=0.24, empirical_positive=True,
            candidate_match=True, candidate_id="EC-X",
            probability_difference=0.14, ev_difference=0.29,
            execution_difference="RESEARCH_WOULD_EXECUTE",
        )
        d = dual.to_dict()
        json_str = json.dumps(d)
        assert len(json_str) > 0
        assert d["execution_difference"] == "RESEARCH_WOULD_EXECUTE"


class TestProductionUnchanged:
    def test_synthetic_ev_unchanged(self):
        """compute_expected_value produces correct result with calibrated formula."""
        ms = _market_state()
        result = compute_expected_value(
            score_neutral=0.55,
            strategy_confidence=0.0,
            market_state_result=ms,
            entry_price=1.1000,
            stop_loss=1.0980,
            take_profit=1.1040,
            confirmation_score=1.0,
        )

        # Phase 1 calibration: p_base = score_neutral = 0.55
        # p_success = 0.55 * 1.0 * 0.80 = 0.44 (TRANSITIONAL dampening = 20%)
        assert 0.40 < result.p_success < 0.48
        assert result.ev > 0  # Positive at RR=2 with p=0.44

    def test_feature_flag_default_false(self):
        """USE_EMPIRICAL_PROBABILITY defaults to False."""
        from core import config
        assert config.USE_EMPIRICAL_PROBABILITY is False

    def test_dual_ev_does_not_affect_synthetic(self):
        """Computing dual EV has no side effect on the synthetic result."""
        import core.research_assessment.provider as prov
        prov._candidates_loaded = True
        prov._validated_candidates = [{
            "candidate_id": "EC-X", "conditions": {"pattern": "TEST"},
            "passes": True, "splits_positive": 4, "splits_total": 5,
            "total_trades": 100, "avg_win_rate": 0.50, "avg_ev": 0.5,
        }]

        synth = _synthetic_result(p=0.28, ev=-0.05, positive=False)

        # Compute dual — should NOT modify synth
        dual = compute_dual_ev(
            synthetic_result=synth,
            pattern_name="TEST",
            reward=0.004, risk=0.002,
        )

        # Synthetic unchanged
        assert dual.synthetic_p == 0.28
        assert dual.synthetic_ev == -0.05
        assert dual.synthetic_positive is False

        prov._candidates_loaded = False
        prov._validated_candidates = []


class TestErrorHandling:
    def test_provider_failure_returns_agree(self):
        """If research assessment throws, returns safe AGREE result."""
        synth = _synthetic_result()

        with patch("core.pipeline.expected_value.compute_dual_ev") as mock:
            # Actually test the real function with broken provider
            pass

        # Direct test: force exception in provider
        import core.research_assessment.provider as prov
        prov._candidates_loaded = True
        prov._validated_candidates = None  # This will cause TypeError in iteration

        dual = compute_dual_ev(
            synthetic_result=synth,
            pattern_name="X",
            reward=0.004, risk=0.002,
        )

        # Should gracefully return synthetic-only
        assert dual.synthetic_p == synth.p_success
        assert dual.execution_difference == "AGREE"

        prov._candidates_loaded = False
        prov._validated_candidates = []
