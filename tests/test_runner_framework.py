"""
Research Runner Framework Tests.

Tests: runner, registry, primitives, composition, failure isolation.
"""

import pytest

from research_engine.v10.runner.primitives.base import (
    AnalysisPrimitive,
    AnalysisRegistry,
    AnalysisResult,
)
from research_engine.v10.runner.primitives.implementations import (
    ExpectancyPrimitive,
    DistributionPrimitive,
    ComparisonPrimitive,
    ConditionalExpectancyPrimitive,
    CalibrationPrimitive,
    PredictivePowerPrimitive,
    SegmentationPrimitive,
    TransitionPrimitive,
    ExecutionQualityPrimitive,
    DegradationPrimitive,
    AnomalyAnalysisPrimitive,
    ExceptionalAnalysisPrimitive,
    build_default_registry,
)
from research_engine.v10.runner.question_runner import (
    QuestionRunner,
    RunContext,
    QuestionExecutionResult,
    compose_evidence,
)
from research_engine.v10.runner.primitive_mapping import (
    build_full_mapping,
    resolve_primitives_for_question,
)
from research_engine.v10.universes.question_bank import QUESTION_BANK
from research_engine.v10.universes.models import AnalysisType, ViewType


# ═══════════════════════════════════════════════════════════════════════════════
# SAMPLE DATA
# ═══════════════════════════════════════════════════════════════════════════════

def _trades(n=50, r_range=(-2, 3)):
    """Generate synthetic trade records."""
    import random
    random.seed(42)
    records = []
    for i in range(n):
        r = random.uniform(*r_range)
        records.append({
            "trade_id": f"t_{i}",
            "entity_id": f"SYM_{i * 300}",
            "symbol": random.choice(["EURUSD", "GBPUSD", "USDJPY"]),
            "r_multiple": round(r, 4),
            "score": round(random.uniform(40, 90), 2),
            "confidence": round(random.uniform(0.3, 0.9), 3),
            "p_success": round(random.uniform(0.3, 0.7), 3),
            "ev": round(random.uniform(-0.5, 1.0), 3),
            "regime": random.choice(["TRENDING", "RANGING", "TRANSITIONAL"]),
            "family": random.choice(["TREND_CONTINUATION", "MEAN_REVERSION", "BREAKOUT"]),
            "pattern": random.choice(["ENGULFING", "HAMMER", "STAR"]),
            "session": random.choice(["LONDON", "NEW_YORK", "ASIA"]),
            "entry_time": 1784700000 + i * 3600,
            "duration_seconds": random.uniform(60, 7200),
            "exit_reason": random.choice(["STOP_LOSS", "TAKE_PROFIT", "TIME_EXIT"]),
            "anomaly": i == 0,  # First record is anomalous
        })
    return records


# ═══════════════════════════════════════════════════════════════════════════════
# REGISTRY TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestRegistry:

    def test_build_default_registry(self):
        r = build_default_registry()
        assert r.count == 12

    def test_get_primitive(self):
        r = build_default_registry()
        p = r.get("expectancy")
        assert p is not None
        assert p.name == "expectancy"

    def test_unknown_returns_none(self):
        r = build_default_registry()
        assert r.get("nonexistent") is None

    def test_duplicate_raises(self):
        r = build_default_registry()
        with pytest.raises(ValueError):
            r.register(ExpectancyPrimitive())

    def test_versions_tracked(self):
        r = build_default_registry()
        v = r.versions()
        assert "expectancy" in v
        assert v["expectancy"] == "1.0.0"


# ═══════════════════════════════════════════════════════════════════════════════
# PRIMITIVE TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestExpectancy:

    def test_positive_expectancy(self):
        pop = [{"r_multiple": 1.5}, {"r_multiple": -1.0}, {"r_multiple": 0.5}]
        r = ExpectancyPrimitive().analyse(pop)
        assert r.success
        assert r.metrics["mean_r"] > 0
        assert r.metrics["win_rate"] == pytest.approx(2/3, abs=0.01)

    def test_empty_population(self):
        r = ExpectancyPrimitive().analyse([])
        assert r.success
        assert r.sample_size == 0

    def test_small_sample_warning(self):
        pop = [{"r_multiple": 1.0}] * 5
        r = ExpectancyPrimitive().analyse(pop)
        assert any("Small sample" in w for w in r.warnings)


class TestDistribution:

    def test_basic(self):
        pop = [{"r_multiple": float(i)} for i in range(10)]
        r = DistributionPrimitive().analyse(pop)
        assert r.success
        assert r.metrics["mean"] == pytest.approx(4.5, abs=0.1)


class TestComparison:

    def test_two_groups(self):
        pop = [
            {"regime": "TRENDING", "r_multiple": 1.0},
            {"regime": "TRENDING", "r_multiple": 0.5},
            {"regime": "RANGING", "r_multiple": -0.5},
            {"regime": "RANGING", "r_multiple": -1.0},
        ]
        r = ComparisonPrimitive().analyse(pop, {"group_field": "regime"})
        assert r.success
        assert "TRENDING" in r.comparisons
        assert "RANGING" in r.comparisons


class TestConditionalExpectancy:

    def test_segments(self):
        pop = _trades(30)
        r = ConditionalExpectancyPrimitive().analyse(pop, {"condition_fields": ["regime"]})
        assert r.success
        assert r.segments  # Should have TRENDING, RANGING, etc.


class TestCalibration:

    def test_basic(self):
        pop = [{"p_success": i/20, "r_multiple": 1 if i > 10 else -1} for i in range(20)]
        r = CalibrationPrimitive().analyse(pop)
        assert r.success
        assert "mean_calibration_error" in r.metrics


class TestPredictivePower:

    def test_monotonic(self):
        # Higher score → higher R (perfect prediction)
        pop = [{"score": float(i), "r_multiple": float(i) * 0.1} for i in range(20)]
        r = PredictivePowerPrimitive().analyse(pop)
        assert r.success
        assert r.metrics["monotonic"] is True


class TestSegmentation:

    def test_by_symbol(self):
        pop = _trades(30)
        r = SegmentationPrimitive().analyse(pop, {"dimensions": ["symbol"]})
        assert r.success
        assert r.segments


class TestTransition:

    def test_basic(self):
        pop = _trades(30)
        r = TransitionPrimitive().analyse(pop)
        assert r.success


class TestExecutionQuality:

    def test_basic(self):
        pop = _trades(30)
        r = ExecutionQualityPrimitive().analyse(pop)
        assert r.success
        assert "mean_duration_s" in r.metrics


class TestDegradation:

    def test_basic(self):
        pop = _trades(30)
        r = DegradationPrimitive().analyse(pop)
        assert r.success
        assert r.metrics["trend"] in ("DEGRADING", "IMPROVING", "STABLE")


class TestAnomalyAnalysis:

    def test_separates_anomalies(self):
        pop = [{"anomaly": True, "r_multiple": -3.0}] + [{"anomaly": False, "r_multiple": 0.5}] * 10
        r = AnomalyAnalysisPrimitive().analyse(pop)
        assert r.success
        assert r.metrics["anomaly_count"] == 1
        assert r.metrics["normal_count"] == 10


class TestExceptionalAnalysis:

    def test_identifies_extremes(self):
        pop = [{"r_multiple": v} for v in [-3, -1, 0, 0.5, 1, 1.5, 3, 5]]
        r = ExceptionalAnalysisPrimitive().analyse(pop)
        assert r.success
        assert r.metrics["exceptional_high_count"] == 2  # 3 and 5
        assert r.metrics["exceptional_low_count"] == 1  # -3


class TestSafeAnalyse:

    def test_error_isolation(self):
        class BrokenPrimitive(AnalysisPrimitive):
            @property
            def name(self): return "broken"
            def analyse(self, pop, params=None):
                raise RuntimeError("intentional failure")

        r = BrokenPrimitive().safe_analyse([{"r_multiple": 1.0}])
        assert not r.success
        assert "intentional failure" in r.error


# ═══════════════════════════════════════════════════════════════════════════════
# RUNNER TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestQuestionRunner:

    def test_run_single_question(self):
        from research_engine.v10.universes.question_bank import E_001
        registry = build_default_registry()
        mapping = build_full_mapping(QUESTION_BANK)
        runner = QuestionRunner(registry, mapping)
        ctx = RunContext(run_id="test_run_001")
        pop = _trades(50)

        result = runner.run_question(E_001, pop, ctx)
        assert result.success
        assert result.finding is not None
        assert result.finding.question_id == "E-001"
        assert result.finding.run_id == "test_run_001"
        assert result.finding.outcome in ("POSITIVE", "NEGATIVE", "INCONCLUSIVE")

    def test_run_batch_isolation(self):
        """One question failing doesn't crash others."""
        from research_engine.v10.universes.question_bank import E_001, M_001, S_001
        registry = build_default_registry()
        mapping = build_full_mapping(QUESTION_BANK)
        runner = QuestionRunner(registry, mapping)
        ctx = RunContext()
        pop = _trades(50)

        results = runner.run_batch(
            [E_001, M_001, S_001],
            {"E-001": pop, "M-001": pop, "S-001": pop},
            ctx,
        )
        assert len(results) == 3
        # All should succeed with synthetic data
        for r in results:
            assert r.success

    def test_missing_population_handled(self):
        """Question with empty population produces a finding (not crash)."""
        from research_engine.v10.universes.question_bank import E_001
        registry = build_default_registry()
        mapping = build_full_mapping(QUESTION_BANK)
        runner = QuestionRunner(registry, mapping)
        ctx = RunContext()

        result = runner.run_question(E_001, [], ctx)
        assert result.success
        assert result.finding is not None
        # Should have warnings about empty data
        assert result.finding.confidence == "INSUFFICIENT"

    def test_run_context_reproducibility(self):
        ctx = RunContext(
            run_id="repro_001",
            engine_version="1.0.0",
            universe_versions={"EXECUTION": "abc123"},
            population_versions={"all_trades": "def456"},
        )
        d = ctx.to_dict()
        assert d["run_id"] == "repro_001"
        assert d["universe_versions"]["EXECUTION"] == "abc123"


# ═══════════════════════════════════════════════════════════════════════════════
# MAPPING TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestPrimitiveMapping:

    def test_all_45_questions_mapped(self):
        mapping = build_full_mapping(QUESTION_BANK)
        assert len(mapping) == 45

    def test_every_mapped_primitive_exists_in_registry(self):
        registry = build_default_registry()
        mapping = build_full_mapping(QUESTION_BANK)
        for qid, primitives in mapping.items():
            for pname in primitives:
                assert registry.has(pname), (
                    f"{qid}: primitive '{pname}' not in registry"
                )

    def test_anomaly_view_adds_anomaly_primitive(self):
        primitives = resolve_primitives_for_question(
            AnalysisType.EXPECTANCY.value, (ViewType.NORMAL, ViewType.ANOMALOUS)
        )
        assert "anomaly_analysis" in primitives

    def test_exceptional_view_adds_exceptional_primitive(self):
        primitives = resolve_primitives_for_question(
            AnalysisType.COMPARISON.value, (ViewType.NORMAL, ViewType.EXCEPTIONAL)
        )
        assert "exceptional_analysis" in primitives


# ═══════════════════════════════════════════════════════════════════════════════
# COMPOSITION TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestEvidenceComposition:

    def test_compose_produces_finding(self):
        from research_engine.v10.universes.question_bank import E_001

        results = [
            AnalysisResult(
                analysis_type="expectancy", success=True, sample_size=50,
                metrics={"mean_r": 0.15, "win_rate": 0.42, "count": 50},
                evidence=["Positive expectancy: +0.15R"],
            ),
        ]
        ctx = RunContext(run_id="comp_test")
        pop = _trades(50)

        finding = compose_evidence(E_001, results, ctx, pop)
        assert finding.question_id == "E-001"
        assert finding.outcome == "POSITIVE"
        assert finding.primary_metrics["mean_r"] == 0.15

    def test_compose_handles_failed_primitive(self):
        from research_engine.v10.universes.question_bank import E_001

        results = [
            AnalysisResult(analysis_type="expectancy", success=False, error="Test error"),
        ]
        ctx = RunContext()

        finding = compose_evidence(E_001, results, ctx, [])
        assert finding.outcome == "ANALYSIS_FAILED"
        assert any("FAILED" in w for w in finding.limitations)

    def test_no_legacy_imports(self):
        import inspect
        from research_engine.v10.runner import question_runner, primitive_mapping
        for mod in (question_runner, primitive_mapping):
            source = inspect.getsource(mod)
            imports = [l for l in source.splitlines() if l.strip().startswith(("import", "from"))]
            for line in imports:
                assert "research_question_registry" not in line
                assert "v10_research_registry" not in line
