"""
Tests for Phase 2C-Part3 Portfolio Ranking Shadow Comparison.

Covers:
    - Agreement detection (execution matches ranking)
    - Disagreement: WRONG_SYMBOL
    - Disagreement: EXTRA_EXECUTIONS
    - Disagreement: NO_EXECUTION_NEEDED
    - Persistence: only interesting cases written
    - Safety: never crashes
"""

from __future__ import annotations

import json
from pathlib import Path
from dataclasses import dataclass
from unittest.mock import patch

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.portfolio_ranking.shadow_comparison import (
    compute_shadow_comparison,
    persist_shadow_comparison,
    ShadowComparison,
)


# ═══════════════════════════════════════════════════════════════════════════════
# MOCK POOL
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class MockCandidate:
    symbol: str
    rank_score: float
    rank_position: int
    eligible: bool
    selection_status: str


@dataclass
class MockPool:
    cycle_id: int
    candidates: list
    selected: MockCandidate | None = None

    @property
    def total_candidates(self) -> int:
        return len(self.candidates)

    @property
    def eligible_count(self) -> int:
        return sum(1 for c in self.candidates if c.eligible)


def _pool_gbpusd_selected() -> MockPool:
    """Pool where GBPUSD is ranked #1."""
    c1 = MockCandidate("GBPUSD", 0.000142, 1, True, "SELECTED")
    c2 = MockCandidate("NZDUSD", 0.000098, 2, True, "OUTRANKED")
    c3 = MockCandidate("USDCAD", -0.00008, 3, False, "BLOCKED")
    return MockPool(cycle_id=100, candidates=[c1, c2, c3], selected=c1)


def _pool_no_selection() -> MockPool:
    """Pool where nothing is eligible."""
    c1 = MockCandidate("EURUSD", -0.0001, 1, False, "BLOCKED")
    return MockPool(cycle_id=200, candidates=[c1], selected=None)


def _pool_empty() -> MockPool:
    return MockPool(cycle_id=300, candidates=[], selected=None)


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: AGREEMENT
# ═══════════════════════════════════════════════════════════════════════════════

class TestAgreement:
    def test_single_execution_matches_ranking(self):
        """Executed GBPUSD, ranking says GBPUSD → agreement."""
        pool = _pool_gbpusd_selected()
        result = compute_shadow_comparison(
            pool=pool, executed_symbols=["GBPUSD"], cycle_id=100,
        )
        assert result.agreement is True
        assert result.disagreement_type == ""
        assert result.ranking_selected_symbol == "GBPUSD"

    def test_nothing_executed_nothing_selected(self):
        """Nothing executed, nothing eligible → agreement."""
        pool = _pool_no_selection()
        result = compute_shadow_comparison(
            pool=pool, executed_symbols=[], cycle_id=200,
        )
        assert result.agreement is True

    def test_empty_pool_empty_execution(self):
        """Empty pool, nothing executed → agreement."""
        pool = _pool_empty()
        result = compute_shadow_comparison(
            pool=pool, executed_symbols=[], cycle_id=300,
        )
        assert result.agreement is True


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: WRONG_SYMBOL
# ═══════════════════════════════════════════════════════════════════════════════

class TestWrongSymbol:
    def test_executed_different_symbol(self):
        """Executed NZDUSD but ranking says GBPUSD → WRONG_SYMBOL."""
        pool = _pool_gbpusd_selected()
        result = compute_shadow_comparison(
            pool=pool, executed_symbols=["NZDUSD"], cycle_id=100,
        )
        assert result.agreement is False
        assert result.disagreement_type == "WRONG_SYMBOL"
        assert "NZDUSD" in result.disagreement_detail
        assert "GBPUSD" in result.disagreement_detail

    def test_executed_blocked_symbol(self):
        """Executed USDCAD (blocked) but ranking says GBPUSD → WRONG_SYMBOL."""
        pool = _pool_gbpusd_selected()
        result = compute_shadow_comparison(
            pool=pool, executed_symbols=["USDCAD"], cycle_id=100,
        )
        assert result.agreement is False
        assert result.disagreement_type == "WRONG_SYMBOL"


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: EXTRA_EXECUTIONS
# ═══════════════════════════════════════════════════════════════════════════════

class TestExtraExecutions:
    def test_multiple_executions(self):
        """Executed 3 symbols but ranking would only select 1 → EXTRA_EXECUTIONS."""
        pool = _pool_gbpusd_selected()
        result = compute_shadow_comparison(
            pool=pool, executed_symbols=["GBPUSD", "NZDUSD", "USDCAD"], cycle_id=100,
        )
        assert result.agreement is False
        assert result.disagreement_type == "EXTRA_EXECUTIONS"
        assert result.actual_execution_count == 3
        assert "3" in result.disagreement_detail


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: NO_EXECUTION_NEEDED
# ═══════════════════════════════════════════════════════════════════════════════

class TestNoExecutionNeeded:
    def test_nothing_executed_but_ranking_wanted(self):
        """Nothing executed but ranking had a selection → NO_EXECUTION_NEEDED."""
        pool = _pool_gbpusd_selected()
        result = compute_shadow_comparison(
            pool=pool, executed_symbols=[], cycle_id=100,
        )
        assert result.agreement is False
        assert result.disagreement_type == "NO_EXECUTION_NEEDED"
        assert "GBPUSD" in result.disagreement_detail


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: OUTRANKED TRACKING
# ═══════════════════════════════════════════════════════════════════════════════

class TestOutranked:
    def test_outranked_symbols_captured(self):
        pool = _pool_gbpusd_selected()
        result = compute_shadow_comparison(
            pool=pool, executed_symbols=["GBPUSD"], cycle_id=100,
        )
        assert "NZDUSD" in result.outranked_symbols

    def test_context_fields_populated(self):
        pool = _pool_gbpusd_selected()
        result = compute_shadow_comparison(
            pool=pool, executed_symbols=["GBPUSD"], cycle_id=100,
            runtime_session_id="session_xyz",
        )
        assert result.total_candidates == 3
        assert result.eligible_candidates == 2
        assert result.runtime_session_id == "session_xyz"


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: PERSISTENCE
# ═══════════════════════════════════════════════════════════════════════════════

class TestPersistence:
    def test_disagreement_persisted(self, tmp_path):
        """Disagreements are written to JSONL."""
        pool = _pool_gbpusd_selected()
        comparison = compute_shadow_comparison(
            pool=pool, executed_symbols=["NZDUSD"], cycle_id=100,
        )
        with patch("core.portfolio_ranking.shadow_comparison._LOCAL_DIR", str(tmp_path / "shadow")):
            persist_shadow_comparison(comparison)

        files = list((tmp_path / "shadow").glob("*.jsonl"))
        assert len(files) == 1
        record = json.loads(files[0].read_text().strip())
        assert record["agreement"] is False
        assert record["disagreement_type"] == "WRONG_SYMBOL"

    def test_agreement_with_multiple_candidates_persisted(self, tmp_path):
        """Agreement with >1 candidate is persisted (interesting for research)."""
        pool = _pool_gbpusd_selected()
        comparison = compute_shadow_comparison(
            pool=pool, executed_symbols=["GBPUSD"], cycle_id=100,
        )
        with patch("core.portfolio_ranking.shadow_comparison._LOCAL_DIR", str(tmp_path / "shadow")):
            persist_shadow_comparison(comparison)

        # Persisted because total_candidates > 1 (even though agreement)
        files = list((tmp_path / "shadow").glob("*.jsonl"))
        assert len(files) == 1

    def test_boring_agreement_not_persisted(self, tmp_path):
        """Simple agreement with 1 candidate is NOT persisted (too noisy)."""
        pool = MockPool(
            cycle_id=400,
            candidates=[MockCandidate("EURUSD", 0.001, 1, True, "SELECTED")],
            selected=MockCandidate("EURUSD", 0.001, 1, True, "SELECTED"),
        )
        comparison = compute_shadow_comparison(
            pool=pool, executed_symbols=["EURUSD"], cycle_id=400,
        )
        with patch("core.portfolio_ranking.shadow_comparison._LOCAL_DIR", str(tmp_path / "shadow")):
            persist_shadow_comparison(comparison)

        # NOT persisted: agreement with only 1 candidate
        files = list((tmp_path / "shadow").glob("*.jsonl"))
        assert len(files) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: SAFETY
# ═══════════════════════════════════════════════════════════════════════════════

class TestSafety:
    def test_none_pool_does_not_crash(self):
        """None pool must not crash."""
        result = compute_shadow_comparison(
            pool=None, executed_symbols=[], cycle_id=0,  # type: ignore
        )
        assert result.agreement is True

    def test_persist_exception_does_not_propagate(self):
        """Persistence failure must never raise."""
        comparison = ShadowComparison(
            cycle_id=1, runtime_session_id="", compared_at_utc="",
            actual_executed_symbols=["X"], actual_execution_count=1,
            ranking_selected_symbol="Y", ranking_selected_rank_score=0.0,
            agreement=False, disagreement_type="WRONG_SYMBOL",
            disagreement_detail="test", total_candidates=2,
            eligible_candidates=1, outranked_symbols=[],
        )
        with patch("core.portfolio_ranking.shadow_comparison.Path.mkdir", side_effect=PermissionError):
            persist_shadow_comparison(comparison)  # Must not raise
