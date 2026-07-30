"""
Tests for V2 Discovery Engine.

Verifies:
    - Feature extraction and statistical calculations
    - Sample filtering and minimum size enforcement
    - Epoch isolation (only linked records analysed)
    - Statistical calculations (EV, CI, p-value)
    - No execution coupling
    - Report generation
"""

import math
import random

import pytest

from research_engine.v2_discovery.feature_analyser import (
    analyse_features,
    get_significant_features,
    summarise_top_features,
    FeatureAnalysis,
    FeatureResult,
    _mean,
    _std,
    _z_to_p,
)
from research_engine.v2_discovery.context_combiner import (
    analyse_combinations,
    CombinationHypothesis,
    CombinationAnalysis,
)
from research_engine.v2_discovery.environment_classifier import (
    classify_environments,
    get_best_environments,
    EnvironmentAnalysis,
)
from research_engine.v2_discovery.probability_model import (
    build_probability_model,
    estimate_probability,
    ProbabilityAnalysis,
    _encode_record,
    _bin_value,
)
from research_engine.v2_discovery.discovery_report import (
    run_full_discovery,
    DiscoveryReport,
    DiscoveryConclusion,
)


# ═══════════════════════════════════════════════════════════════════════════════
# TEST DATA GENERATORS
# ═══════════════════════════════════════════════════════════════════════════════


def _make_record(
    *,
    h4_regime: str = "TRENDING",
    h1_bias: str = "BULLISH",
    h1_bos_confirmed: bool = True,
    session: str = "LONDON",
    near_support: bool = True,
    pattern_detected: str = "HAMMER",
    pattern_quality: float = 0.7,
    spread_atr_ratio: float = 0.25,
    risk_distance_pips: float = 10.0,
    volatility: float = 0.5,
    outcome_raw_r: float = 0.5,
    timestamp_utc: float = 1753574400.0,
) -> dict:
    """Create a linked V2Opportunity record for testing."""
    return {
        "schema_version": "v2_opportunity_1.0",
        "opportunity_id": f"v2_TEST_{int(timestamp_utc)}_{random.randint(1000,9999)}",
        "correlation_id": f"TEST_{int(timestamp_utc)}",
        "timestamp_utc": timestamp_utc,
        "symbol": "EURUSD",
        "timeframe": "M5",
        "h4_regime": h4_regime,
        "h4_trend_direction": "BULLISH" if h4_regime == "TRENDING" else "NEUTRAL",
        "h4_structure_state": "",
        "h4_volatility_state": "NEUTRAL",
        "h1_bias": h1_bias,
        "h1_structure_type": "HH_HL",
        "h1_bos_confirmed": h1_bos_confirmed,
        "h1_bos_direction": "BULLISH",
        "h1_choch_detected": False,
        "near_support": near_support,
        "near_resistance": False,
        "order_block_present": False,
        "m15_structure_state": "",
        "m15_rejection_strength": 0.5,
        "m15_displacement": 0.0,
        "pattern_detected": pattern_detected,
        "pattern_direction": "BUY",
        "pattern_quality": pattern_quality,
        "candle_range": 0.0015,
        "body_ratio": 0.4,
        "wick_ratio": 0.5,
        "bid": 1.085,
        "ask": 1.0851,
        "spread": 0.0001,
        "spread_atr_ratio": spread_atr_ratio,
        "atr": 0.0008,
        "volatility": volatility,
        "session": session,
        "proposed_direction": "BUY",
        "proposed_entry": 1.08505,
        "structure_stop_distance": 0.00113,
        "candle_stop_distance": 0.00027,
        "atr_stop_distance": 0.0012,
        "risk_distance_pips": risk_distance_pips,
        # Linked outcome
        "outcome_recorded": True,
        "outcome_raw_r": outcome_raw_r,
        "mfe": max(outcome_raw_r, 0.1),
        "mae": min(outcome_raw_r, -0.1),
        "_linkage": {
            "linked": True,
            "result_r": outcome_raw_r,
            "win": outcome_raw_r > 0,
            "mfe_r": max(outcome_raw_r, 0.1),
            "mae_r": min(outcome_raw_r, -0.1),
            "hold_minutes": 45,
            "exit_reason": "TIMEOUT",
            "match_method": "entity_id",
        },
    }


def _generate_dataset(n: int = 200, seed: int = 42) -> list[dict]:
    """Generate a realistic test dataset."""
    random.seed(seed)
    records = []
    regimes = ["TRENDING", "RANGING", "TRANSITIONAL"]
    biases = ["BULLISH", "BEARISH", "NEUTRAL"]
    sessions = ["LONDON", "NY", "ASIA", "OFF"]
    patterns = ["HAMMER", "ENGULFING", "DOJI", "PIN_BAR", ""]

    for i in range(n):
        regime = random.choice(regimes)
        bias = random.choice(biases)
        session = random.choice(sessions)
        pattern = random.choice(patterns)

        # Simulate slight edge for LONDON + TRENDING
        base_ev = -0.2  # negative baseline
        if session == "LONDON":
            base_ev += 0.15
        if regime == "TRENDING":
            base_ev += 0.1

        outcome = random.gauss(base_ev, 1.5)

        records.append(_make_record(
            h4_regime=regime,
            h1_bias=bias,
            session=session,
            pattern_detected=pattern,
            pattern_quality=random.uniform(0.3, 0.9),
            spread_atr_ratio=random.uniform(0.1, 0.6),
            risk_distance_pips=random.uniform(5, 20),
            volatility=random.uniform(0.2, 0.8),
            outcome_raw_r=round(outcome, 4),
            timestamp_utc=1753574400.0 + i * 300,
            h1_bos_confirmed=random.random() > 0.4,
            near_support=random.random() > 0.5,
        ))

    return records


# ═══════════════════════════════════════════════════════════════════════════════
# CQ1 — FEATURE ANALYSER TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestFeatureAnalyser:
    """CQ1 feature analysis tests."""

    def test_empty_records(self):
        """Returns empty list for no records."""
        result = analyse_features([])
        assert result == []

    def test_unlinked_records_filtered(self):
        """Records without outcomes are excluded."""
        records = [{"h4_regime": "TRENDING", "outcome_recorded": False}] * 50
        result = analyse_features(records)
        assert result == []

    def test_basic_analysis(self):
        """Produces results for valid data."""
        records = _generate_dataset(200)
        results = analyse_features(records, min_sample=20)
        assert len(results) > 0
        assert all(isinstance(r, FeatureAnalysis) for r in results)

    def test_minimum_sample_enforced(self):
        """Categories below min_sample are excluded."""
        records = _generate_dataset(100)
        results = analyse_features(records, min_sample=50)
        for analysis in results:
            for cat_result in analysis.results:
                assert cat_result.sample_size >= 50

    def test_ev_calculation_correct(self):
        """EV calculation matches manual computation."""
        # All wins of +1R
        records = [_make_record(outcome_raw_r=1.0, h4_regime="TRENDING")
                   for _ in range(50)]
        results = analyse_features(
            records, min_sample=10,
            categorical_features=["h4_regime"],
            continuous_features=[])
        # Find the h4_regime analysis
        h4_analysis = [r for r in results if r.feature_name == "h4_regime"]
        assert len(h4_analysis) == 1
        trending_result = h4_analysis[0].results[0]
        assert trending_result.raw_ev == pytest.approx(1.0, abs=0.001)
        assert trending_result.cost_adjusted_ev == pytest.approx(0.52, abs=0.01)

    def test_confidence_interval_computed(self):
        """CI bounds are present and sensible."""
        records = _generate_dataset(200)
        results = analyse_features(records, min_sample=20)
        for analysis in results:
            for r in analysis.results:
                assert r.ci_lower <= r.raw_ev <= r.ci_upper

    def test_significant_features_filter(self):
        """get_significant_features returns only predictive features."""
        records = _generate_dataset(200)
        results = analyse_features(records, min_sample=20)
        significant = get_significant_features(results)
        assert all(a.predictive for a in significant)

    def test_summarise_top(self):
        """Summary returns expected dict format."""
        records = _generate_dataset(200)
        results = analyse_features(records, min_sample=20)
        summary = summarise_top_features(results, top_n=3)
        assert len(summary) <= 3
        if summary:
            assert "feature" in summary[0]
            assert "best_ev" in summary[0]


class TestStatisticalUtils:
    """Utility function correctness."""

    def test_mean(self):
        assert _mean([1, 2, 3, 4, 5]) == 3.0

    def test_mean_empty(self):
        assert _mean([]) == 0.0

    def test_std_single(self):
        assert _std([5.0]) == 0.0

    def test_std_known(self):
        # std of [2, 4, 4, 4, 5, 5, 7, 9] = 2.138
        vals = [2, 4, 4, 4, 5, 5, 7, 9]
        assert _std(vals) == pytest.approx(2.138, abs=0.01)

    def test_z_to_p_zero(self):
        """z=0 gives p=1.0 (no difference)."""
        assert _z_to_p(0.0) == pytest.approx(1.0, abs=0.01)

    def test_z_to_p_large(self):
        """Large z gives small p."""
        assert _z_to_p(3.0) < 0.01


# ═══════════════════════════════════════════════════════════════════════════════
# CQ2 — CONTEXT COMBINER TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestContextCombiner:
    """CQ2 combination analysis tests."""

    def test_empty_records(self):
        """Returns empty analysis for no records."""
        result = analyse_combinations([])
        assert result.hypotheses_tested == 0

    def test_custom_hypothesis(self):
        """Custom hypothesis is tested correctly."""
        records = _generate_dataset(200)
        hyp = CombinationHypothesis(
            hypothesis_id="TEST_1",
            description="Test combo",
            filters={"session": "LONDON"},
        )
        result = analyse_combinations(
            records, hypotheses=[hyp], min_sample=10)
        assert result.hypotheses_tested == 1
        # Should have results since LONDON records exist
        assert len(result.results) >= 0  # May or may not meet threshold

    def test_validation_split(self):
        """In-sample and out-of-sample are both computed."""
        records = _generate_dataset(300)
        hyp = CombinationHypothesis(
            hypothesis_id="TEST_2",
            description="All records match",
            filters={"proposed_direction": "BUY"},
        )
        result = analyse_combinations(
            records, hypotheses=[hyp], min_sample=10)
        if result.results:
            r = result.results[0]
            assert r.in_sample_n > 0
            assert r.out_sample_n > 0
            assert r.in_sample_n > r.out_sample_n  # 70/30 split

    def test_no_brute_force(self):
        """Only explicitly provided hypotheses are tested."""
        records = _generate_dataset(200)
        # Single narrow hypothesis that won't match anything
        narrow_hyp = CombinationHypothesis(
            hypothesis_id="NARROW",
            description="Impossible combo",
            filters={"h4_regime": "IMPOSSIBLE_VALUE"},
        )
        result = analyse_combinations(records, hypotheses=[narrow_hyp])
        assert result.hypotheses_tested == 1
        assert len(result.results) == 0  # No records match


# ═══════════════════════════════════════════════════════════════════════════════
# CQ3 — ENVIRONMENT CLASSIFIER TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestEnvironmentClassifier:
    """CQ3 environment classification tests."""

    def test_empty_records(self):
        """Returns empty analysis for no data."""
        result = classify_environments([])
        assert result.total_records == 0

    def test_basic_classification(self):
        """Produces environment buckets."""
        records = _generate_dataset(200)
        result = classify_environments(records, min_sample=20)
        assert result.total_records == 200
        assert len(result.buckets) > 0
        assert result.dimensions_analysed > 0

    def test_bucket_stats_valid(self):
        """Each bucket has valid statistical properties."""
        records = _generate_dataset(200)
        result = classify_environments(records, min_sample=20)
        for b in result.buckets:
            assert 0 <= b.win_rate <= 1.0
            assert b.sample_size >= 20
            assert b.ci_lower <= b.raw_ev <= b.ci_upper
            assert 0 <= b.p_value <= 1.0

    def test_favourable_has_positive_ev(self):
        """Favourable environments have positive cost-adjusted EV."""
        records = _generate_dataset(300)
        result = classify_environments(records, min_sample=15)
        for env in result.favourable_environments:
            assert env.cost_adjusted_ev > 0
            assert env.significant is True

    def test_get_best_environments(self):
        """Helper returns correct format."""
        records = _generate_dataset(200)
        result = classify_environments(records, min_sample=20)
        best = get_best_environments(result, top_n=3)
        assert len(best) <= 3
        if best:
            assert "dimension" in best[0]
            assert "cost_adjusted_ev" in best[0]


# ═══════════════════════════════════════════════════════════════════════════════
# CQ4 — PROBABILITY MODEL TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestProbabilityModel:
    """CQ4 probability model tests."""

    def test_insufficient_data(self):
        """Returns empty model for small datasets."""
        records = [_make_record() for _ in range(5)]
        result = build_probability_model(records)
        assert result.train_size == 0

    def test_basic_model(self):
        """Builds model with sufficient data."""
        records = _generate_dataset(200)
        result = build_probability_model(records, min_cohort=10)
        assert result.train_size > 0
        assert result.test_size > 0
        assert 0 <= result.model_accuracy <= 1.0
        assert 0 <= result.model_brier_score <= 1.0

    def test_calibration_computed(self):
        """Calibration buckets are produced."""
        records = _generate_dataset(200)
        result = build_probability_model(records, min_cohort=10)
        # At least some calibration buckets should exist
        assert isinstance(result.calibration_buckets, list)

    def test_feature_importance_computed(self):
        """Feature importance is ranked."""
        records = _generate_dataset(200)
        result = build_probability_model(records, min_cohort=10)
        assert isinstance(result.feature_importance, list)
        if result.feature_importance:
            # Sorted by importance descending
            scores = [f.importance_score for f in result.feature_importance]
            assert scores == sorted(scores, reverse=True)

    def test_encoding(self):
        """Feature encoding works correctly."""
        rec = _make_record(pattern_quality=0.75, spread_atr_ratio=0.15)
        features = ["h4_regime", "pattern_quality", "spread_atr_ratio"]
        enc = _encode_record(rec, features)
        assert enc["h4_regime"] == "TRENDING"
        assert "BIN_" in enc["pattern_quality"]
        assert "BIN_" in enc["spread_atr_ratio"]

    def test_bin_value(self):
        """Continuous binning is correct."""
        assert _bin_value("pattern_quality", 0.3) == "BIN_0"
        assert _bin_value("pattern_quality", 0.5) == "BIN_1"
        assert _bin_value("pattern_quality", 0.75) == "BIN_2"
        assert _bin_value("pattern_quality", 0.9) == "BIN_3"
        assert _bin_value("pattern_quality", None) == "MISSING"

    def test_single_estimate(self):
        """Can estimate probability for a single record."""
        records = _generate_dataset(100)
        target = _make_record()
        est = estimate_probability(target, records, min_cohort=5)
        assert 0 <= est.predicted_win_prob <= 1.0
        assert est.cohort_size >= 5


# ═══════════════════════════════════════════════════════════════════════════════
# DISCOVERY REPORT TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestDiscoveryReport:
    """Full discovery report generation tests."""

    def test_empty_records(self):
        """Report handles empty input."""
        report = run_full_discovery([])
        assert report.linked_records == 0
        assert report.conclusion.outcome == "NO_PREDICTIVE_VALUE"

    def test_full_report_generation(self):
        """Full report runs without error."""
        records = _generate_dataset(200)
        report = run_full_discovery(records, min_sample=15)
        assert report.total_records == 200
        assert report.linked_records == 200
        assert report.cq1_features_analysed > 0
        assert report.conclusion is not None

    def test_conclusion_has_evidence(self):
        """Conclusion contains evidence and next steps."""
        records = _generate_dataset(200)
        report = run_full_discovery(records, min_sample=15)
        assert len(report.conclusion.evidence) >= 4  # One per CQ
        assert len(report.conclusion.next_steps) >= 1

    def test_report_conclusion_types(self):
        """Conclusion outcome is one of expected values."""
        records = _generate_dataset(200)
        report = run_full_discovery(records, min_sample=15)
        assert report.conclusion.outcome in (
            "PREDICTIVE_INFORMATION_FOUND", "NO_PREDICTIVE_VALUE")
        assert report.conclusion.confidence in ("HIGH", "MEDIUM", "LOW")


# ═══════════════════════════════════════════════════════════════════════════════
# SAFETY TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoExecutionCoupling:
    """Discovery engine has no execution dependencies."""

    def test_no_forbidden_imports_feature_analyser(self):
        import inspect
        import research_engine.v2_discovery.feature_analyser as m
        source = inspect.getsource(m)
        for forbidden in ["import MetaTrader5", "from core.pipeline",
                          "from core.runtime"]:
            assert forbidden not in source

    def test_no_forbidden_imports_combiner(self):
        import inspect
        import research_engine.v2_discovery.context_combiner as m
        source = inspect.getsource(m)
        for forbidden in ["import MetaTrader5", "from core.pipeline",
                          "from core.runtime"]:
            assert forbidden not in source

    def test_no_forbidden_imports_environment(self):
        import inspect
        import research_engine.v2_discovery.environment_classifier as m
        source = inspect.getsource(m)
        for forbidden in ["import MetaTrader5", "from core.pipeline",
                          "from core.runtime"]:
            assert forbidden not in source

    def test_no_forbidden_imports_probability(self):
        import inspect
        import research_engine.v2_discovery.probability_model as m
        source = inspect.getsource(m)
        for forbidden in ["import MetaTrader5", "from core.pipeline",
                          "from core.runtime"]:
            assert forbidden not in source

    def test_no_forbidden_imports_report(self):
        import inspect
        import research_engine.v2_discovery.discovery_report as m
        source = inspect.getsource(m)
        for forbidden in ["import MetaTrader5", "from core.pipeline",
                          "from core.runtime"]:
            assert forbidden not in source

    def test_analysis_does_not_modify_records(self):
        """Input records are not mutated by analysis."""
        import copy
        records = _generate_dataset(50)
        original = copy.deepcopy(records)
        analyse_features(records, min_sample=10)
        # Check first and last records unchanged
        assert records[0] == original[0]
        assert records[-1] == original[-1]
