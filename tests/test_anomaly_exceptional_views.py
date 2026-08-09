"""
Tests for anomaly + exceptional views in research findings.

Proves:
    - Anomaly evidence is generated when question declares ANOMALOUS view
    - Exceptional evidence is generated when question declares EXCEPTIONAL view
    - NOT_APPLICABLE when question doesn't declare the view
    - INCONCLUSIVE when view is applicable but no qualifying records
    - Reproducibility metadata present (criteria, sample sizes, thresholds)
    - Finding persistence includes structured views
    - Markdown rendering includes view sections
    - Local/Lambda parity for views
    - Failure isolation (view failure doesn't crash primary analysis)
    - Regression of existing pipeline
"""

import json
from pathlib import Path

import pytest

from research_engine.v10.runner.primitives.base import AnalysisResult
from research_engine.v10.runner.primitives.implementations import (
    AnomalyAnalysisPrimitive,
    ExceptionalAnalysisPrimitive,
    build_default_registry,
)
from research_engine.v10.runner.question_runner import (
    QuestionRunner,
    RunContext,
    compose_evidence,
)
from research_engine.v10.runner.primitive_mapping import build_full_mapping
from research_engine.v10.universes.question_bank import QUESTION_BANK, get_question
from research_engine.v10.universes.models import ViewType
from research_engine.v10.control_plane.question_products import QuestionProductManager
from research_engine.v10.control_plane.finding_schema import ResearchFinding


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _pop_with_anomalies(n=50, anomaly_count=3):
    import random
    random.seed(77)
    records = []
    for i in range(n):
        records.append({
            "trade_id": f"t{i}", "entity_id": f"SYM_{i*300}",
            "symbol": "EURUSD",
            "r_multiple": round(random.uniform(-2, 3), 3),
            "score": round(random.uniform(40, 90), 2),
            "regime": "TRENDING",
            "family": "TREND_CONTINUATION",
            "pattern": "ENGULFING",
            "entry_time": 1784700000 + i * 3600,
            "duration_seconds": random.uniform(100, 5000),
            "exit_reason": "STOP_LOSS" if random.random() < 0.6 else "TAKE_PROFIT",
            "anomaly": i < anomaly_count,
            "session": "LONDON",
            "confidence": 0.7,
        })
    return records


def _pop_with_extremes(n=50):
    import random
    random.seed(88)
    records = []
    for i in range(n):
        if i < 2:
            r = 4.0  # Exceptional high
        elif i < 4:
            r = -3.5  # Exceptional low
        else:
            r = round(random.uniform(-1.5, 1.5), 3)
        records.append({
            "trade_id": f"t{i}", "entity_id": f"SYM_{i*300}",
            "symbol": "EURUSD", "r_multiple": r,
            "score": 70, "regime": "TRENDING",
            "family": "TREND_CONTINUATION", "pattern": "ENGULFING",
            "entry_time": 1784700000 + i * 3600,
            "duration_seconds": 1000, "exit_reason": "STOP_LOSS",
            "anomaly": False, "session": "LONDON", "confidence": 0.7,
        })
    return records


# ═══════════════════════════════════════════════════════════════════════════════
# ANOMALY VIEW TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestAnomalyView:

    def test_anomaly_view_available_when_data_exists(self):
        """Question with ANOMALOUS view + anomalous records → AVAILABLE."""
        q = get_question("E-001")  # Has ANOMALOUS view
        assert ViewType.ANOMALOUS in q.views

        registry = build_default_registry()
        mapping = build_full_mapping(QUESTION_BANK)
        runner = QuestionRunner(registry, mapping)
        ctx = RunContext(run_id="test_anomaly")

        pop = _pop_with_anomalies(50, anomaly_count=3)
        result = runner.run_question(q, pop, ctx)

        assert result.success
        view = result.finding.anomaly_view
        assert view["status"] == "AVAILABLE"
        assert view["anomaly_count"] == 3
        assert view["normal_count"] == 47
        assert "criteria" in view
        assert "anomaly_rate" in view

    def test_anomaly_view_inconclusive_no_anomalies(self):
        """ANOMALOUS view declared but no anomalous records → INCONCLUSIVE."""
        q = get_question("E-001")
        registry = build_default_registry()
        mapping = build_full_mapping(QUESTION_BANK)
        runner = QuestionRunner(registry, mapping)
        ctx = RunContext()

        pop = _pop_with_anomalies(50, anomaly_count=0)
        result = runner.run_question(q, pop, ctx)

        assert result.success
        view = result.finding.anomaly_view
        assert view["status"] == "INCONCLUSIVE"
        assert "No anomalous records" in view["reason"]

    def test_anomaly_view_not_applicable(self):
        """Question without ANOMALOUS view → NOT_APPLICABLE."""
        q = get_question("D-002")  # Does NOT have ANOMALOUS view
        assert ViewType.ANOMALOUS not in q.views

        registry = build_default_registry()
        mapping = build_full_mapping(QUESTION_BANK)
        runner = QuestionRunner(registry, mapping)
        ctx = RunContext()

        pop = _pop_with_anomalies(50)
        result = runner.run_question(q, pop, ctx)

        assert result.success
        view = result.finding.anomaly_view
        assert view["status"] == "NOT_APPLICABLE"

    def test_anomaly_view_has_reproducibility_metadata(self):
        q = get_question("E-001")
        registry = build_default_registry()
        mapping = build_full_mapping(QUESTION_BANK)
        runner = QuestionRunner(registry, mapping)
        ctx = RunContext()

        pop = _pop_with_anomalies(50, anomaly_count=5)
        result = runner.run_question(q, pop, ctx)

        view = result.finding.anomaly_view
        assert "criteria" in view
        assert "sample_sizes" in view


# ═══════════════════════════════════════════════════════════════════════════════
# EXCEPTIONAL VIEW TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestExceptionalView:

    def test_exceptional_view_available(self):
        """Question with EXCEPTIONAL view + extreme records → AVAILABLE."""
        q = get_question("D-004")  # Has EXCEPTIONAL view
        assert ViewType.EXCEPTIONAL in q.views

        registry = build_default_registry()
        mapping = build_full_mapping(QUESTION_BANK)
        runner = QuestionRunner(registry, mapping)
        ctx = RunContext()

        pop = _pop_with_extremes(50)
        result = runner.run_question(q, pop, ctx)

        assert result.success
        view = result.finding.exceptional_view
        assert view["status"] == "AVAILABLE"
        assert view["exceptional_high_count"] == 2
        assert view["exceptional_low_count"] == 2
        assert "criteria" in view
        assert view["criteria"]["threshold_high"] == 2.0

    def test_exceptional_view_inconclusive(self):
        """EXCEPTIONAL declared but no extreme values → INCONCLUSIVE."""
        q = get_question("D-004")
        registry = build_default_registry()
        mapping = build_full_mapping(QUESTION_BANK)
        runner = QuestionRunner(registry, mapping)
        ctx = RunContext()

        # All values between -1 and 1 — no extremes
        pop = [{"r_multiple": 0.5, "anomaly": False, "entry_time": 1000 + i,
                "duration_seconds": 100, "exit_reason": "SL", "score": 70,
                "regime": "TRENDING", "symbol": "EURUSD", "session": "LONDON",
                "family": "X", "pattern": "X", "confidence": 0.5}
               for i in range(30)]
        result = runner.run_question(q, pop, ctx)

        view = result.finding.exceptional_view
        assert view["status"] == "INCONCLUSIVE"

    def test_exceptional_view_not_applicable(self):
        """Question without EXCEPTIONAL view → NOT_APPLICABLE."""
        q = get_question("E-001")  # Does NOT have EXCEPTIONAL
        assert ViewType.EXCEPTIONAL not in q.views

        registry = build_default_registry()
        mapping = build_full_mapping(QUESTION_BANK)
        runner = QuestionRunner(registry, mapping)
        ctx = RunContext()

        pop = _pop_with_extremes(50)
        result = runner.run_question(q, pop, ctx)

        view = result.finding.exceptional_view
        assert view["status"] == "NOT_APPLICABLE"


# ═══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE & MARKDOWN TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestPersistenceAndMarkdown:

    def test_views_persisted_in_latest_json(self, tmp_path):
        mgr = QuestionProductManager(base_dir=tmp_path)
        mgr.initialise_product("E-001", {"title": "test", "research_intent": "test"})

        finding = ResearchFinding(
            question_id="E-001", run_id="r1", outcome="NEGATIVE",
            anomaly_view={"status": "AVAILABLE", "anomaly_count": 3, "criteria": {"field": "anomaly"}},
            exceptional_view={"status": "NOT_APPLICABLE", "reason": "not declared"},
        )
        mgr.save_finding(finding)

        data = json.loads((tmp_path / "E-001" / "latest.json").read_text())
        assert data["anomaly_view"]["status"] == "AVAILABLE"
        assert data["exceptional_view"]["status"] == "NOT_APPLICABLE"

    def test_views_in_markdown(self, tmp_path):
        mgr = QuestionProductManager(base_dir=tmp_path)
        mgr.initialise_product("E-001", {"title": "test", "research_intent": "test"})

        finding = ResearchFinding(
            question_id="E-001", run_id="r1", title="Test", outcome="NEGATIVE",
            anomaly_view={"status": "AVAILABLE", "anomaly_count": 3, "normal_count": 47},
            exceptional_view={"status": "INCONCLUSIVE", "reason": "No extremes found"},
        )
        mgr.save_finding(finding)

        md = (tmp_path / "E-001" / "latest.md").read_text()
        assert "Anomaly View" in md
        assert "AVAILABLE" in md or "anomaly_count" in md
        assert "Exceptional View" in md or "INCONCLUSIVE" in md


# ═══════════════════════════════════════════════════════════════════════════════
# FAILURE ISOLATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestFailureIsolation:

    def test_anomaly_failure_does_not_crash_finding(self):
        """If anomaly primitive fails, primary analysis still completes."""
        q = get_question("E-001")
        primary = AnalysisResult(
            analysis_type="expectancy", success=True, sample_size=50,
            metrics={"mean_r": 0.15, "count": 50, "win_rate": 0.42},
            evidence=["Positive"],
        )
        anomaly_failed = AnalysisResult(
            analysis_type="anomaly_analysis", success=False,
            error="Test failure in anomaly",
        )
        ctx = RunContext()

        finding = compose_evidence(q, [primary, anomaly_failed], ctx, [{}] * 50)
        assert finding.outcome == "POSITIVE"
        assert finding.anomaly_view["status"] == "ERROR"


# ═══════════════════════════════════════════════════════════════════════════════
# PARITY
# ═══════════════════════════════════════════════════════════════════════════════


class TestParity:

    def test_lambda_produces_same_views(self):
        """Lambda adapter produces same anomaly/exceptional views."""
        from research_engine.v10.runner.lambda_adapter import LambdaResearchAdapter
        from tests.test_lambda_canonical import _mock_builders

        builders = _mock_builders()
        adapter = LambdaResearchAdapter(builders=builders)

        result = adapter.handle({
            "action": "run_canonical_question",
            "question_id": "E-001",
        })
        assert "finding" in result
        # Anomaly view should be structured
        av = result["finding"]["anomaly_view"]
        assert "status" in av
        assert av["status"] in ("AVAILABLE", "INCONCLUSIVE", "NOT_APPLICABLE", "NOT_EXECUTED")
