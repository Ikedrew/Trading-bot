"""
Research Cockpit Tests.

Proves:
    - No research logic duplicated in UI
    - Correct data consumption from existing state
    - All 45 questions discoverable
    - Finding visibility
    - Cockpit can regenerate from state
"""

import json
from pathlib import Path

import pytest

from research_engine.v10.cockpit.aggregator import (
    CockpitData,
    CockpitDataAggregator,
    QuestionSummary,
)
from research_engine.v10.cockpit.generator import generate_cockpit
from research_engine.v10.universes.question_bank import QUESTION_BANK


class TestCockpitAggregator:

    def test_aggregates_from_real_data(self):
        """Aggregator reads real reports/research/ state."""
        agg = CockpitDataAggregator()
        data = agg.aggregate()
        # Should find all 45 questions
        assert data.total_questions == len(QUESTION_BANK)
        # Should have findings from the executed run
        assert data.complete >= 40
        assert data.blocked >= 1

    def test_all_questions_discoverable(self):
        agg = CockpitDataAggregator()
        data = agg.aggregate()
        ids = [q.question_id for q in data.all_questions]
        assert "E-001" in ids
        assert "D-001" in ids
        assert "M-001" in ids
        assert "S-001" in ids
        assert "EDMS-001" in ids

    def test_four_angle_classification(self):
        agg = CockpitDataAggregator()
        data = agg.aggregate()
        assert len(data.execution_questions) > 0
        assert len(data.decision_questions) > 0
        assert len(data.market_questions) > 0
        assert len(data.strategy_questions) > 0
        assert len(data.cross_angle_questions) > 0

    def test_findings_visible(self):
        agg = CockpitDataAggregator()
        data = agg.aggregate()
        # E-001 should have a finding
        e001 = next(q for q in data.all_questions if q.question_id == "E-001")
        assert e001.status == "COMPLETE"
        assert e001.outcome  # Non-empty
        assert e001.confidence  # Non-empty
        assert e001.sample_size > 0

    def test_anomaly_exceptional_status_visible(self):
        agg = CockpitDataAggregator()
        data = agg.aggregate()
        e001 = next(q for q in data.all_questions if q.question_id == "E-001")
        assert e001.anomaly_status in ("AVAILABLE", "INCONCLUSIVE", "NOT_APPLICABLE", "N/A")

    def test_run_history_loaded(self):
        agg = CockpitDataAggregator()
        data = agg.aggregate()
        assert len(data.run_history) >= 1
        assert data.run_history[0]["run_id"]

    def test_universe_health_loaded(self):
        agg = CockpitDataAggregator()
        data = agg.aggregate()
        assert len(data.universes) >= 4

    def test_correlation_loaded(self):
        agg = CockpitDataAggregator()
        data = agg.aggregate()
        assert data.correlation_summary.get("classification")

    def test_blocked_question_classified(self):
        agg = CockpitDataAggregator()
        data = agg.aggregate()
        e006 = next(q for q in data.all_questions if q.question_id == "E-006")
        assert e006.status == "BLOCKED"


class TestCockpitGenerator:

    def test_generates_html(self, tmp_path):
        agg = CockpitDataAggregator()
        data = agg.aggregate()
        path = generate_cockpit(output_path=tmp_path / "cockpit.html", data=data)
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "Research Cockpit" in content
        assert "E-001" in content
        assert "EXECUTION" in content

    def test_html_contains_all_sections(self, tmp_path):
        agg = CockpitDataAggregator()
        data = agg.aggregate()
        path = generate_cockpit(output_path=tmp_path / "c.html", data=data)
        content = path.read_text(encoding="utf-8")
        assert 'id="overview"' in content
        assert 'id="angles"' in content
        assert 'id="questions"' in content
        assert 'id="runs"' in content
        assert 'id="health"' in content
        assert 'id="development"' in content

    def test_html_has_search(self, tmp_path):
        agg = CockpitDataAggregator()
        data = agg.aggregate()
        path = generate_cockpit(output_path=tmp_path / "c.html", data=data)
        content = path.read_text(encoding="utf-8")
        assert 'id="search"' in content
        assert "filterQuestions" in content

    def test_no_research_logic_in_cockpit(self):
        """Cockpit must NOT import analysis primitives or runners."""
        import inspect
        from research_engine.v10.cockpit import aggregator, generator
        for mod in (aggregator, generator):
            source = inspect.getsource(mod)
            imports = [l for l in source.splitlines() if l.strip().startswith(("import", "from"))]
            for line in imports:
                assert "primitives" not in line
                assert "question_runner" not in line
                assert "orchestrator" not in line
                assert "context_resolver" not in line
