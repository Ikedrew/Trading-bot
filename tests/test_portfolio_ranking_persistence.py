"""
Tests for Phase 2C Portfolio Ranking Persistence.

Covers:
    - Ranking record schema compliance
    - Persistence to JSONL
    - S3 mirror called correctly
    - Join keys present
    - No execution behaviour change
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock
from dataclasses import dataclass

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.portfolio_ranking.persistence import (
    persist_portfolio_ranking,
    SCHEMA_VERSION,
    DATASET_VERSION,
)
from core.lifecycle_evidence_obligations import (
    LifecycleEvidenceLedger, ObligationStatus,
)


# ═══════════════════════════════════════════════════════════════════════════════
# MOCK RANKER OUTPUT (matches core/pipeline/opportunity_ranker.py)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class MockRankedCandidate:
    symbol: str
    pattern: str
    strategy: str
    strategy_confidence: float
    score_neutral: float
    score_strategy: float
    ev: float
    rr_effective: float
    market_state: str
    rank_score: float
    rank_position: int
    eligible: bool
    block_reason: str | None
    selection_status: str


@dataclass
class MockOpportunityPool:
    cycle_id: int
    candidates: list
    selected: MockRankedCandidate | None = None

    @property
    def total_candidates(self) -> int:
        return len(self.candidates)

    @property
    def eligible_count(self) -> int:
        return sum(1 for c in self.candidates if c.eligible)


def _make_pool() -> MockOpportunityPool:
    """Create a realistic OpportunityPool with 3 candidates."""
    c1 = MockRankedCandidate(
        symbol="GBPUSD", pattern="TWEEZER_TOP", strategy="CONTINUATION",
        strategy_confidence=0.72, score_neutral=0.58, score_strategy=0.62,
        ev=0.000142, rr_effective=2.03, market_state="STRUCTURED",
        rank_score=0.000142, rank_position=1, eligible=True,
        block_reason=None, selection_status="SELECTED",
    )
    c2 = MockRankedCandidate(
        symbol="NZDUSD", pattern="TWEEZER_TOP", strategy="CONTINUATION",
        strategy_confidence=0.65, score_neutral=0.55, score_strategy=0.58,
        ev=0.000098, rr_effective=1.95, market_state="STRUCTURED",
        rank_score=0.000098, rank_position=2, eligible=True,
        block_reason=None, selection_status="OUTRANKED",
    )
    c3 = MockRankedCandidate(
        symbol="USDCAD", pattern="EVENING_STAR", strategy="REVERSAL",
        strategy_confidence=0.40, score_neutral=0.42, score_strategy=0.38,
        ev=-0.00012, rr_effective=1.5, market_state="TRANSITIONAL",
        rank_score=-0.000078, rank_position=3, eligible=False,
        block_reason="ev_policy_blocked: NEGATIVE_EXPECTED_VALUE",
        selection_status="BLOCKED",
    )
    return MockOpportunityPool(cycle_id=4578, candidates=[c1, c2, c3], selected=c1)


def _make_empty_pool() -> MockOpportunityPool:
    return MockOpportunityPool(cycle_id=100, candidates=[], selected=None)


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: SCHEMA COMPLIANCE
# ═══════════════════════════════════════════════════════════════════════════════

class TestSchemaCompliance:
    def test_schema_version_constant(self):
        assert SCHEMA_VERSION == "portfolio_ranking_v1"

    def test_dataset_version_constant(self):
        assert DATASET_VERSION == 1  # clean V1 baseline (was "2026.1")

    def test_record_contains_versions(self, tmp_path):
        pool = _make_pool()
        with patch("core.portfolio_ranking.persistence._LOCAL_DIR", str(tmp_path / "rankings")):
            with patch("core.portfolio_ranking.persistence._write_s3"):
                persist_portfolio_ranking(pool, runtime_session_id="test123")

        files = list((tmp_path / "rankings").glob("*.jsonl"))
        assert len(files) == 1
        record = json.loads(files[0].read_text().strip())
        assert record["schema_version"] == "portfolio_ranking_v1"
        assert record["dataset_version"] == 1

    def test_no_execution_fields(self, tmp_path):
        pool = _make_pool()
        with patch("core.portfolio_ranking.persistence._LOCAL_DIR", str(tmp_path / "rankings")):
            with patch("core.portfolio_ranking.persistence._write_s3"):
                persist_portfolio_ranking(pool)

        record = json.loads((tmp_path / "rankings").glob("*.jsonl").__next__().read_text().strip())
        forbidden = ["sl", "tp", "volume", "fill_price", "order_type"]
        for field in forbidden:
            assert field not in record


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: PERSISTENCE
# ═══════════════════════════════════════════════════════════════════════════════

class TestPersistence:
    def test_local_ranking_write_keeps_obligation_pending(self, tmp_path):
        pool = _make_pool()
        ledger = LifecycleEvidenceLedger(tmp_path / "obligation-ledger.data")
        with patch("core.portfolio_ranking.persistence._LOCAL_DIR", str(tmp_path / "rankings")), \
             patch("core.portfolio_ranking.persistence._write_s3"), \
             patch("core.lifecycle_evidence_obligations.obligation_ledger",
                   return_value=ledger):
            assert persist_portfolio_ranking(pool, runtime_session_id="session-test") is True

        obligation = ledger.obligations()[0]
        assert obligation.expected_dataset == "portfolio_rankings"
        assert obligation.current_status == ObligationStatus.NOT_YET_DUE.value
        assert obligation.provenance["producer_write"] == "LOCAL_FSYNC_SUCCEEDED"
        assert obligation.provenance["canonical_mirror_acknowledgement"] == "NOT_OBSERVED"

    def test_writes_jsonl_file(self, tmp_path):
        pool = _make_pool()
        with patch("core.portfolio_ranking.persistence._LOCAL_DIR", str(tmp_path / "rankings")):
            with patch("core.portfolio_ranking.persistence._write_s3"):
                persist_portfolio_ranking(pool)

        files = list((tmp_path / "rankings").glob("*.jsonl"))
        assert len(files) == 1

    def test_record_structure(self, tmp_path):
        pool = _make_pool()
        with patch("core.portfolio_ranking.persistence._LOCAL_DIR", str(tmp_path / "rankings")):
            with patch("core.portfolio_ranking.persistence._write_s3"):
                persist_portfolio_ranking(
                    pool,
                    runtime_session_id="session_abc",
                    open_positions_count=1,
                    max_open_positions=3,
                )

        record = json.loads((tmp_path / "rankings").glob("*.jsonl").__next__().read_text().strip())

        # Identity
        assert record["cycle_id"] == 4578
        assert record["runtime_session_id"] == "session_abc"
        assert "ranking_id" in record
        assert record["ranking_id"] == "ranking_session_abc_4578"
        assert "ranked_at_utc" in record

        # Pool summary
        assert record["total_candidates"] == 3
        assert record["eligible_count"] == 2
        assert record["selected_symbol"] == "GBPUSD"
        assert record["selected_rank_score"] == 0.000142
        assert record["ranking_method"] == "ev_x_market_state_multiplier"

        # Portfolio context
        assert record["open_positions_at_ranking"] == 1
        assert record["available_slots"] == 2
        assert record["max_open_positions"] == 3

    def test_candidates_persisted(self, tmp_path):
        pool = _make_pool()
        with patch("core.portfolio_ranking.persistence._LOCAL_DIR", str(tmp_path / "rankings")):
            with patch("core.portfolio_ranking.persistence._write_s3"):
                persist_portfolio_ranking(pool)

        record = json.loads((tmp_path / "rankings").glob("*.jsonl").__next__().read_text().strip())
        candidates = record["candidates"]
        assert len(candidates) == 3

        # First candidate (SELECTED)
        assert candidates[0]["symbol"] == "GBPUSD"
        assert candidates[0]["selection_status"] == "SELECTED"
        assert candidates[0]["rank_position"] == 1
        assert candidates[0]["ev"] == 0.000142
        assert candidates[0]["eligible"] is True
        assert candidates[0]["block_reason"] is None

        # Second candidate (OUTRANKED)
        assert candidates[1]["symbol"] == "NZDUSD"
        assert candidates[1]["selection_status"] == "OUTRANKED"
        assert candidates[1]["rank_position"] == 2

        # Third candidate (BLOCKED)
        assert candidates[2]["symbol"] == "USDCAD"
        assert candidates[2]["selection_status"] == "BLOCKED"
        assert candidates[2]["eligible"] is False
        assert "ev_policy" in candidates[2]["block_reason"]

    def test_empty_pool_persists(self, tmp_path):
        pool = _make_empty_pool()
        with patch("core.portfolio_ranking.persistence._LOCAL_DIR", str(tmp_path / "rankings")):
            with patch("core.portfolio_ranking.persistence._write_s3"):
                persist_portfolio_ranking(pool)

        record = json.loads((tmp_path / "rankings").glob("*.jsonl").__next__().read_text().strip())
        assert record["total_candidates"] == 0
        assert record["eligible_count"] == 0
        assert record["selected_symbol"] == ""
        assert record["candidates"] == []

    @patch("core.canonical_delivery.enqueue_canonical_delivery")
    def test_outbox_handoff_called(self, mock_enqueue, tmp_path):
        pool = _make_pool()
        with patch("core.portfolio_ranking.persistence._LOCAL_DIR", str(tmp_path / "rankings")):
            persist_portfolio_ranking(pool)

        mock_enqueue.assert_called_once()
        call_args = mock_enqueue.call_args.kwargs
        assert call_args["dataset"] == "portfolio_rankings"
        assert call_args["payload"]["schema_version"] == "portfolio_ranking_v1"


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: JOIN KEYS
# ═══════════════════════════════════════════════════════════════════════════════

class TestJoinKeys:
    def test_opportunity_id_on_candidates(self, tmp_path):
        pool = _make_pool()
        with patch("core.portfolio_ranking.persistence._LOCAL_DIR", str(tmp_path / "rankings")):
            with patch("core.portfolio_ranking.persistence._write_s3"):
                persist_portfolio_ranking(pool)

        record = json.loads((tmp_path / "rankings").glob("*.jsonl").__next__().read_text().strip())
        for c in record["candidates"]:
            assert "opportunity_id" in c
            assert c["opportunity_id"] != ""

    def test_assessment_id_on_candidates(self, tmp_path):
        pool = _make_pool()
        with patch("core.portfolio_ranking.persistence._LOCAL_DIR", str(tmp_path / "rankings")):
            with patch("core.portfolio_ranking.persistence._write_s3"):
                persist_portfolio_ranking(pool)

        record = json.loads((tmp_path / "rankings").glob("*.jsonl").__next__().read_text().strip())
        for c in record["candidates"]:
            assert "assessment_id" in c
            assert c["assessment_id"].endswith("_assessment")

    def test_cycle_id_present(self, tmp_path):
        pool = _make_pool()
        with patch("core.portfolio_ranking.persistence._LOCAL_DIR", str(tmp_path / "rankings")):
            with patch("core.portfolio_ranking.persistence._write_s3"):
                persist_portfolio_ranking(pool)

        record = json.loads((tmp_path / "rankings").glob("*.jsonl").__next__().read_text().strip())
        assert record["cycle_id"] == 4578


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: SAFETY
# ═══════════════════════════════════════════════════════════════════════════════

class TestSafety:
    def test_exception_does_not_propagate(self):
        """Persistence failure must never raise to caller."""
        with patch("core.portfolio_ranking.persistence.Path.mkdir", side_effect=PermissionError("denied")):
            # Must not raise
            persist_portfolio_ranking(_make_pool())

    def test_none_pool_does_not_crash(self):
        """Passing None or broken pool must not crash."""
        persist_portfolio_ranking(None)  # type: ignore

    def test_pool_with_no_candidates_attribute(self):
        """Pool without expected attributes must not crash."""
        persist_portfolio_ranking(MagicMock(spec=[]))  # type: ignore
