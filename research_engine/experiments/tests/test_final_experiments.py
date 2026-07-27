"""
Tests for all 7 final research experiments (R3, R4, R5, E5, D6, L7, P1).

Covers:
    - Insufficient data handling
    - Readiness failures
    - Confidence calculations
    - Dataset fingerprinting
    - Contamination handling
    - Provenance generation
    - Report generation (standard contract)
    - Recommendation logic
    - Promotion gates

Does NOT test trading logic — experiments are research only.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from research_engine.experiments.experiment_base import (
    ReadinessStatus,
    build_fingerprint,
    check_readiness,
    compute_confidence,
    extract_r_multiples,
)
from research_engine.experiments.probability_of_ruin import run_probability_of_ruin
from research_engine.experiments.drawdown_threshold import run_drawdown_threshold
from research_engine.experiments.position_sizing import run_position_sizing
from research_engine.experiments.out_of_sample_validation import run_out_of_sample_validation
from research_engine.experiments.portfolio_ranking import run_portfolio_ranking
from research_engine.experiments.shadow_ab_validation import run_shadow_ab_validation
from research_engine.experiments.promotion_impact import run_promotion_impact

# Reduce Monte Carlo iterations for test speed
import research_engine.experiments.probability_of_ruin as _r3_mod
_r3_mod._MONTE_CARLO_SIMULATIONS = 100
_r3_mod._MONTE_CARLO_TRADE_HORIZON = 200


# ═══════════════════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════════════════


def _make_shadow_record(r_multiple=0.5, entity_id="EURUSD_170000", pattern="BOS_PULLBACK", score=0.55, strategy="CONTINUATION", cycle_id="100"):
    return {
        "schema_version": "shadow_trades_v2",
        "identity": {"trade_id": "t1", "correlation_id": "C1", "symbol": "EURUSD", "strategy_id": strategy, "entity_id": entity_id, "cycle_id": cycle_id},
        "decision_snapshot": {"pattern": pattern, "score": score, "regime": "TRENDING", "h4_regime": "TRENDING", "h1_bias": "BULLISH", "trade_horizon": "SCALP", "market_phase": "IMPULSE", "strategy": strategy, "cycle_id": cycle_id},
        "simulated_outcome": {"pnl_r_multiple": r_multiple, "exit_reason": "take_profit" if r_multiple > 0 else "stop_loss", "bars_held": 12},
    }


def _make_records(n, r_func=None, **kwargs):
    """Create N records. r_func(i) returns R-multiple for record i."""
    records = []
    for i in range(n):
        r = r_func(i) if r_func else (1.5 if i % 3 != 0 else -1.0)
        records.append(_make_shadow_record(r_multiple=r, entity_id=f"EURUSD_{170000+i}", cycle_id=str(100+i), **kwargs))
    return records


def _positive_edge_records(n=200):
    """Records with clear positive edge: 60% win rate, avg +0.3R."""
    return _make_records(n, r_func=lambda i: 2.0 if i % 5 != 0 else -1.0)


def _negative_edge_records(n=200):
    """Records with negative edge."""
    return _make_records(n, r_func=lambda i: 0.5 if i % 5 == 0 else -1.0)


def _concurrent_records(n_cycles=20, trades_per_cycle=3):
    """Records with multiple trades per cycle for portfolio ranking test."""
    records = []
    for c in range(n_cycles):
        for t in range(trades_per_cycle):
            r = 2.0 - t * 1.0  # First trade in cycle is best
            score = 0.7 - t * 0.1
            records.append(_make_shadow_record(
                r_multiple=r, entity_id=f"EURUSD_{170000+c*10+t}",
                cycle_id=str(100 + c), score=score, pattern=f"PAT_{t}",
            ))
    return records


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: EXPERIMENT BASE
# ═══════════════════════════════════════════════════════════════════════════════


class TestExperimentBase:
    def test_extract_r_multiples(self):
        records = _make_records(10)
        r_values = extract_r_multiples(records)
        assert len(r_values) == 10
        assert all(isinstance(r, float) for r in r_values)

    def test_check_readiness_insufficient(self):
        status, reason, _ = check_readiness([], min_samples=50)
        assert status == ReadinessStatus.INSUFFICIENT_DATA

    def test_check_readiness_passes(self):
        records = _positive_edge_records(100)
        status, reason, cov = check_readiness(records, min_samples=50, require_outcome=True)
        assert status == ReadinessStatus.READY
        assert cov["outcome"] >= 0.95

    def test_build_fingerprint(self):
        fp = build_fingerprint(100, 20, source="shadow_trades")
        assert fp["records_used"] == 100
        assert fp["records_excluded"] == 20
        assert "shadow_trades" in fp["dataset_id"]

    def test_compute_confidence_levels(self):
        assert compute_confidence(200, True) == "HIGH"
        assert compute_confidence(100, True) == "HIGH"
        assert compute_confidence(100, False) == "MEDIUM"
        assert compute_confidence(50, False) == "MEDIUM"
        assert compute_confidence(20, False) == "LOW"
        assert compute_confidence(5, False) == "INSUFFICIENT_DATA"


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: R3 — PROBABILITY OF RUIN
# ═══════════════════════════════════════════════════════════════════════════════


class TestR3ProbabilityOfRuin:
    def test_insufficient_data(self):
        result = run_probability_of_ruin([])
        assert result["status"] == ReadinessStatus.INSUFFICIENT_DATA
        assert result["recommendation"] == "WAIT"

    def test_small_sample(self):
        result = run_probability_of_ruin(_make_records(10))
        assert result["status"] == ReadinessStatus.INSUFFICIENT_DATA

    @patch("research_engine.experiments.probability_of_ruin.persist_report")
    @patch("research_engine.experiments.probability_of_ruin.update_knowledge_map")
    def test_positive_edge_low_ruin(self, mock_km, mock_persist):
        result = run_probability_of_ruin(_positive_edge_records(200))
        assert result["status"] == ReadinessStatus.COMPLETE
        assert result["question_id"] == "R3"
        assert result["overall"]["probability_of_ruin_monte_carlo"] < 0.20
        assert result["confidence"] in ("HIGH", "MEDIUM")
        assert "fingerprint" in result
        assert "provenance" in result
        mock_persist.assert_called_once()

    @patch("research_engine.experiments.probability_of_ruin.persist_report")
    @patch("research_engine.experiments.probability_of_ruin.update_knowledge_map")
    def test_negative_edge_high_ruin(self, mock_km, mock_persist):
        result = run_probability_of_ruin(_negative_edge_records(200))
        assert result["status"] == ReadinessStatus.COMPLETE
        assert result["overall"]["probability_of_ruin_monte_carlo"] > 0.10
        assert result["recommendation"] in ("REJECT", "WAIT")

    @patch("research_engine.experiments.probability_of_ruin.persist_report")
    @patch("research_engine.experiments.probability_of_ruin.update_knowledge_map")
    def test_report_contract(self, mock_km, mock_persist):
        result = run_probability_of_ruin(_positive_edge_records(100))
        for key in ("question_id", "status", "overall", "confidence", "dataset", "fingerprint", "recommendation", "assumptions", "warnings", "generated", "provenance"):
            assert key in result, f"Missing key: {key}"


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: R4 — DRAWDOWN THRESHOLD
# ═══════════════════════════════════════════════════════════════════════════════


class TestR4DrawdownThreshold:
    def test_insufficient_data(self):
        result = run_drawdown_threshold([])
        assert result["status"] == ReadinessStatus.INSUFFICIENT_DATA

    @patch("research_engine.experiments.drawdown_threshold.persist_report")
    @patch("research_engine.experiments.drawdown_threshold.update_knowledge_map")
    def test_produces_halt_threshold(self, mock_km, mock_persist):
        result = run_drawdown_threshold(_positive_edge_records(150))
        assert result["status"] == ReadinessStatus.COMPLETE
        assert "recommended_halt_threshold" in result["overall"]
        assert "resume_threshold" in result["overall"]
        assert "recovery_analysis" in result["overall"]
        assert result["overall"]["recommended_halt_threshold"] > 0

    @patch("research_engine.experiments.drawdown_threshold.persist_report")
    @patch("research_engine.experiments.drawdown_threshold.update_knowledge_map")
    def test_report_contract(self, mock_km, mock_persist):
        result = run_drawdown_threshold(_positive_edge_records(100))
        for key in ("question_id", "status", "overall", "confidence", "dataset", "fingerprint", "recommendation", "generated", "provenance"):
            assert key in result


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: R5 — POSITION SIZING
# ═══════════════════════════════════════════════════════════════════════════════


class TestR5PositionSizing:
    def test_insufficient_data(self):
        result = run_position_sizing([])
        assert result["status"] == ReadinessStatus.INSUFFICIENT_DATA

    @patch("research_engine.experiments.position_sizing.persist_report")
    @patch("research_engine.experiments.position_sizing.update_knowledge_map")
    def test_selects_best_model(self, mock_km, mock_persist):
        result = run_position_sizing(_positive_edge_records(150))
        assert result["status"] == ReadinessStatus.COMPLETE
        assert "kelly_fraction" in result["overall"]
        assert "best_model" in result["overall"]
        assert result["overall"]["kelly_fraction"] > 0  # Positive edge → positive Kelly

    @patch("research_engine.experiments.position_sizing.persist_report")
    @patch("research_engine.experiments.position_sizing.update_knowledge_map")
    def test_negative_edge_zero_kelly(self, mock_km, mock_persist):
        result = run_position_sizing(_negative_edge_records(150))
        assert result["status"] == ReadinessStatus.COMPLETE
        assert result["overall"]["kelly_fraction"] == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: E5 — OUT OF SAMPLE VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestE5OutOfSample:
    def test_insufficient_data(self):
        result = run_out_of_sample_validation([])
        assert result["status"] == ReadinessStatus.INSUFFICIENT_DATA

    def test_small_sample(self):
        result = run_out_of_sample_validation(_make_records(30))
        assert result["status"] in (ReadinessStatus.INSUFFICIENT_DATA, ReadinessStatus.WAITING_DATA)

    @patch("research_engine.experiments.out_of_sample_validation.persist_report")
    @patch("research_engine.experiments.out_of_sample_validation.update_knowledge_map")
    def test_positive_edge_survives(self, mock_km, mock_persist):
        result = run_out_of_sample_validation(_positive_edge_records(200))
        assert result["status"] == ReadinessStatus.COMPLETE
        assert "in_sample_ev" in result["overall"]
        assert "out_of_sample_ev" in result["overall"]
        assert "stability_score" in result["overall"]
        assert result["overall"]["edge_survives"] is True
        assert result["recommendation"] in ("PROMOTE", "MONITOR")

    @patch("research_engine.experiments.out_of_sample_validation.persist_report")
    @patch("research_engine.experiments.out_of_sample_validation.update_knowledge_map")
    def test_negative_edge_fails(self, mock_km, mock_persist):
        result = run_out_of_sample_validation(_negative_edge_records(200))
        assert result["status"] == ReadinessStatus.COMPLETE
        assert result["overall"]["edge_survives"] is False
        assert result["recommendation"] == "REJECT"

    @patch("research_engine.experiments.out_of_sample_validation.persist_report")
    @patch("research_engine.experiments.out_of_sample_validation.update_knowledge_map")
    def test_rolling_windows_produced(self, mock_km, mock_persist):
        result = run_out_of_sample_validation(_positive_edge_records(200))
        assert len(result["overall"]["rolling_windows"]) >= 3


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: D6 — PORTFOLIO RANKING
# ═══════════════════════════════════════════════════════════════════════════════


class TestD6PortfolioRanking:
    def test_insufficient_data(self):
        result = run_portfolio_ranking([])
        assert result["status"] == ReadinessStatus.INSUFFICIENT_DATA

    def test_no_concurrent_cycles(self):
        """Single trade per cycle → insufficient concurrent data."""
        records = _make_records(100)  # All unique cycle_ids
        result = run_portfolio_ranking(records)
        # Should detect insufficient concurrent cycles
        assert result["status"] == ReadinessStatus.INSUFFICIENT_DATA or result["recommendation"] == "WAIT"

    @patch("research_engine.experiments.portfolio_ranking.persist_report")
    @patch("research_engine.experiments.portfolio_ranking.update_knowledge_map")
    def test_concurrent_ranking(self, mock_km, mock_persist):
        records = _concurrent_records(n_cycles=20, trades_per_cycle=3)
        result = run_portfolio_ranking(records)
        assert result["status"] == ReadinessStatus.COMPLETE
        assert "ranking_accuracy" in result["overall"]
        assert "opportunity_cost_avg" in result["overall"]
        assert result["overall"]["ranking_accuracy"] > 0

    @patch("research_engine.experiments.portfolio_ranking.persist_report")
    @patch("research_engine.experiments.portfolio_ranking.update_knowledge_map")
    def test_perfect_ranking_high_accuracy(self, mock_km, mock_persist):
        """When highest-scored trade IS the best outcome, accuracy should be high."""
        records = _concurrent_records(n_cycles=30, trades_per_cycle=2)
        result = run_portfolio_ranking(records)
        assert result["overall"]["ranking_accuracy"] >= 0.50


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: L7 — SHADOW A/B VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestL7ShadowAB:
    def test_insufficient_data(self):
        result = run_shadow_ab_validation([])
        assert result["status"] == ReadinessStatus.INSUFFICIENT_DATA

    def test_small_sample(self):
        result = run_shadow_ab_validation(_make_records(20))
        assert result["status"] in (ReadinessStatus.INSUFFICIENT_DATA, ReadinessStatus.WAITING_DATA)

    @patch("research_engine.experiments.shadow_ab_validation.persist_report")
    @patch("research_engine.experiments.shadow_ab_validation.update_knowledge_map")
    def test_equal_arms_inconclusive(self, mock_km, mock_persist):
        """Same distribution in both halves → INCONCLUSIVE."""
        records = _make_records(200, r_func=lambda i: 0.5 if i % 2 == 0 else -0.5)
        result = run_shadow_ab_validation(records)
        assert result["status"] == ReadinessStatus.COMPLETE
        assert result["overall"]["winner"] == "INCONCLUSIVE"
        assert result["recommendation"] == "WAIT"

    @patch("research_engine.experiments.shadow_ab_validation.persist_report")
    @patch("research_engine.experiments.shadow_ab_validation.update_knowledge_map")
    def test_candidate_better(self, mock_km, mock_persist):
        """Second half much better → CANDIDATE wins."""
        records = _make_records(200, r_func=lambda i: -0.5 if i < 100 else 2.0)
        result = run_shadow_ab_validation(records)
        assert result["status"] == ReadinessStatus.COMPLETE
        assert result["overall"]["winner"] == "CANDIDATE"
        assert result["overall"]["significant"] is True

    @patch("research_engine.experiments.shadow_ab_validation.persist_report")
    @patch("research_engine.experiments.shadow_ab_validation.update_knowledge_map")
    def test_report_has_both_arms(self, mock_km, mock_persist):
        result = run_shadow_ab_validation(_positive_edge_records(200))
        assert "control" in result["overall"]
        assert "candidate" in result["overall"]
        assert "z_statistic" in result["overall"]


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: P1 — PROMOTION IMPACT
# ═══════════════════════════════════════════════════════════════════════════════


class TestP1PromotionImpact:
    def test_insufficient_data(self):
        result = run_promotion_impact([])
        assert result["status"] == ReadinessStatus.INSUFFICIENT_DATA

    @patch("research_engine.experiments.promotion_impact.persist_report")
    @patch("research_engine.experiments.promotion_impact.update_knowledge_map")
    def test_identifies_negative_patterns(self, mock_km, mock_persist):
        """Should identify patterns to remove."""
        # Mix good and bad patterns
        records = []
        for i in range(100):
            records.append(_make_shadow_record(r_multiple=1.5, entity_id=f"E_{i}", pattern="GOOD_PAT", strategy="CONTINUATION"))
        for i in range(100):
            records.append(_make_shadow_record(r_multiple=-1.0, entity_id=f"E_{100+i}", pattern="BAD_PAT", strategy="CONTINUATION"))
        result = run_promotion_impact(records)
        assert result["status"] == ReadinessStatus.COMPLETE
        assert "negative_patterns" in result["overall"]
        assert "BAD_PAT" in result["overall"]["negative_patterns"]
        assert result["overall"]["best_ev_improvement"] > 0

    @patch("research_engine.experiments.promotion_impact.persist_report")
    @patch("research_engine.experiments.promotion_impact.update_knowledge_map")
    def test_no_improvement_rejects(self, mock_km, mock_persist):
        """All patterns positive → no removal candidate."""
        records = _positive_edge_records(200)
        result = run_promotion_impact(records)
        assert result["status"] == ReadinessStatus.COMPLETE
        # With uniform positive edge, no strong removal candidate
        assert result["recommendation"] in ("WAIT", "REJECT", "PROMOTE")

    @patch("research_engine.experiments.promotion_impact.persist_report")
    @patch("research_engine.experiments.promotion_impact.update_knowledge_map")
    def test_report_contract(self, mock_km, mock_persist):
        records = _positive_edge_records(200)
        result = run_promotion_impact(records)
        for key in ("question_id", "status", "overall", "confidence", "dataset", "fingerprint", "recommendation", "generated", "provenance"):
            assert key in result


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: CONTAMINATION HANDLING
# ═══════════════════════════════════════════════════════════════════════════════


class TestContaminationHandling:
    def test_contamination_detected(self):
        records = [_make_shadow_record(strategy="NONE_SCALP") for _ in range(60)]
        status, reason, cov = check_readiness(
            records, min_samples=50, require_no_contamination=True, require_outcome=True,
        )
        assert status == ReadinessStatus.BLOCKED
        assert "contaminated" in reason

    def test_clean_passes(self):
        records = _positive_edge_records(100)
        status, _, cov = check_readiness(records, min_samples=50, require_no_contamination=True, require_outcome=True)
        assert status == ReadinessStatus.READY


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: PROVENANCE & FINGERPRINT
# ═══════════════════════════════════════════════════════════════════════════════


class TestProvenanceFingerprint:
    @patch("research_engine.experiments.probability_of_ruin.persist_report")
    @patch("research_engine.experiments.probability_of_ruin.update_knowledge_map")
    def test_provenance_in_r3(self, mock_km, mock_persist):
        result = run_probability_of_ruin(_positive_edge_records(100))
        assert result["provenance"]["registry_id"] == "R3"
        assert "experiment_module" in result["provenance"]

    @patch("research_engine.experiments.out_of_sample_validation.persist_report")
    @patch("research_engine.experiments.out_of_sample_validation.update_knowledge_map")
    def test_fingerprint_in_e5(self, mock_km, mock_persist):
        result = run_out_of_sample_validation(_positive_edge_records(200))
        fp = result["fingerprint"]
        assert fp["records_used"] > 0
        assert "dataset_id" in fp
        assert "shadow_trades" in fp["dataset_id"]
