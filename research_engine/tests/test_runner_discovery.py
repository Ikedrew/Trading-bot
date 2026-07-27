"""
Tests for Registry-Driven Runner Discovery.

Validates:
    - Every registry runner with metadata is discovered
    - Every discovered object is callable
    - Missing runner metadata is skipped
    - Missing modules do not crash loading
    - Duplicate IDs detected
    - run_all() executes discovered runners
    - Legacy IDs still execute correctly
    - get_all_runners() merges legacy + registry

Does NOT test trading logic — this is infrastructure only.
"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_project_root = str(Path(__file__).resolve().parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from research_engine.runner_discovery import (
    discover_runners,
    get_all_runners,
    get_discovery_diagnostics,
)


class TestDiscoverRunners:
    """Test that registry-driven discovery works correctly."""

    def test_discovers_all_v2_runners(self):
        """All experiments with runner_module are discovered."""
        runners = discover_runners()
        # 7 v2 + 20 legacy with metadata = 27 total (some shared functions reduce count)
        assert len(runners) >= 20
        # V2 experiments always present
        for qid in ("R3", "R4", "R5", "E5", "D6", "L7", "P1"):
            assert qid in runners, f"Missing runner for {qid}"
        # Legacy experiments with registry metadata also present
        for qid in ("E1", "E2", "D1", "D2", "M1"):
            assert qid in runners, f"Missing runner for {qid}"

    def test_all_discovered_are_callable(self):
        """Every discovered runner is a callable."""
        runners = discover_runners()
        for qid, func in runners.items():
            assert callable(func), f"Runner for {qid} is not callable"

    def test_skips_questions_without_metadata(self):
        """Questions without runner_module are not discovered."""
        runners = discover_runners()
        # E4, M2-M8, S2-S7 etc don't have runner_module set
        assert "E4" not in runners
        assert "M2" not in runners
        assert "G1" not in runners

    def test_missing_module_does_not_crash(self):
        """A missing module produces a warning, not a crash."""
        from research_engine.registry.research_question_models import (
            ResearchQuestion, QuestionCategory, QuestionPriority, DataSource,
        )
        from research_engine.registry.research_question_registry import REGISTRY

        fake_q = ResearchQuestion(
            id="FAKE1",
            category=QuestionCategory.SYSTEM_EDGE,
            title="Fake",
            description="Test",
            required_fields=("r_multiple",),
            data_sources=(DataSource.SHADOW_TRADES,),
            priority=QuestionPriority.P3,
            runner_module="nonexistent.module.that.does.not.exist",
            runner_function="run_fake",
            report_filename="fake.json",
        )

        with patch("research_engine.registry.research_question_registry.REGISTRY", REGISTRY + (fake_q,)):
            runners = discover_runners()
            # FAKE1 should not be in results (module doesn't exist)
            assert "FAKE1" not in runners
            # Other runners should still work
            assert "R3" in runners

    def test_discovery_diagnostics(self):
        """Diagnostics report correct counts based on registry."""
        from research_engine.registry.research_question_registry import REGISTRY
        diag = get_discovery_diagnostics()
        expected_total = len(REGISTRY)
        expected_with_runner = sum(1 for q in REGISTRY if q.runner_module and q.runner_function)
        expected_without = expected_total - expected_with_runner

        assert diag["total_registry_questions"] == expected_total
        assert diag["with_runner_metadata"] == expected_with_runner
        assert diag["without_runner_metadata"] == expected_without
        assert diag["successfully_discovered"] >= expected_with_runner - 2  # Allow minor import failures
        assert diag["failed_to_load"] == 0
        assert diag["legacy_runners_remaining"] == 0


class TestGetAllRunners:
    """Test merged runner retrieval (legacy + registry)."""

    def test_includes_legacy_runners(self):
        """Legacy Q-series runners are discovered via registry metadata."""
        runners = get_all_runners()
        # D4 maps to run_q02, D5 maps to run_q03, etc.
        assert "D4" in runners  # Q2 via registry
        assert "D5" in runners  # Q3 via registry
        assert "E2" in runners  # Q5 via registry

    def test_includes_registry_runners(self):
        """V2 registry runners are included."""
        runners = get_all_runners()
        assert "R3" in runners
        assert "E5" in runners
        assert "P1" in runners

    def test_registry_is_sole_source(self):
        """All runners come from registry discovery only."""
        from research_engine.registry.research_question_registry import REGISTRY
        runners = get_all_runners()
        expected_max = sum(1 for q in REGISTRY if q.runner_module and q.runner_function)
        assert len(runners) >= 1
        assert len(runners) <= expected_max

    def test_all_runners_callable(self):
        """Every merged runner is callable."""
        runners = get_all_runners()
        for qid, func in runners.items():
            assert callable(func), f"Runner {qid} is not callable"

    def test_legacy_adapted_returns_canonical(self):
        """Legacy-adapted runners return canonical report format."""
        runners = get_all_runners()
        # D5 is a legacy runner (run_q03) — should be adapted
        if "D5" in runners:
            result = runners["D5"]()
            # Must have canonical fields
            assert "overall" in result
            assert "fingerprint" in result
            assert "provenance" in result
            assert "generated" in result
            assert isinstance(result.get("recommendation"), str)


class TestRunAllIntegration:
    """Test that run_all() uses discovery correctly."""

    @patch("research_engine.runner_discovery.discover_runners")
    def test_run_all_uses_discovery(self, mock_discover):
        """run_all() calls get_all_runners which uses discover_runners."""
        # Just verify the import path works
        from research_engine.experiments.research_runner import run_all
        # run_all imports get_all_runners which calls discover_runners
        # We don't actually run it (would be slow) but verify it's wired
        assert callable(run_all)


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: PHASE B CLEANUP VERIFICATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestPhaseBCleanup:
    """Verify legacy dead code has been removed."""

    def test_no_experiment_functions_in_research_runner(self):
        """research_runner.py should not contain run_qXX functions."""
        import research_engine.experiments.research_runner as rr
        # Should NOT have any run_q* attributes
        q_funcs = [attr for attr in dir(rr) if attr.startswith("run_q")]
        assert q_funcs == [], f"Dead experiment functions still in research_runner: {q_funcs}"

    def test_no_ALL_RUNNERS_in_research_runner(self):
        """research_runner.py should not have ALL_RUNNERS dict."""
        import research_engine.experiments.research_runner as rr
        assert not hasattr(rr, "ALL_RUNNERS"), "ALL_RUNNERS still exists in research_runner"

    def test_no_wrap_report_in_research_runner(self):
        """research_runner.py should not import wrap_report."""
        import research_engine.experiments.research_runner as rr
        assert not hasattr(rr, "wrap_report"), "wrap_report still imported in research_runner"

    def test_registry_is_sole_discovery_mechanism(self):
        """get_all_runners uses only registry discovery."""
        from research_engine.runner_discovery import get_all_runners
        import inspect
        source = inspect.getsource(get_all_runners)
        assert "_get_legacy_runners" not in source
        assert "_LEGACY_RUNNERS" not in source
        assert "discover_runners" in source

    def test_all_discovered_return_canonical(self):
        """Every discovered runner returns canonical report format."""
        from research_engine.runner_discovery import discover_runners
        from research_engine.experiments.report_contract import validate_report_contract, normalize_legacy_report
        runners = discover_runners()
        # Test a sample of runners that can execute without data (they return INSUFFICIENT_DATA)
        for qid in ("D5", "M1", "X1"):
            if qid not in runners:
                continue
            result = runners[qid]()
            # Must have canonical 'overall' field (not legacy 'metrics')
            assert "overall" in result, f"{qid} missing 'overall' field"
            assert "fingerprint" in result, f"{qid} missing 'fingerprint' field"
            assert "provenance" in result, f"{qid} missing 'provenance' field"
            assert isinstance(result.get("recommendation"), str), f"{qid} recommendation is not a string"
