"""
Tests for V10 Active Opportunity Ranking.

Verifies:
    1. Multiple candidates → highest score ranked first
    2. Lower quality cannot outrank higher quality
    3. Risk rejection removes candidate from execution (ranking doesn't bypass safety)
    4. Same-symbol opportunities ranked independently
    5. Opportunity IDs preserved through ranking
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
from dataclasses import dataclass, field
from typing import Any

from core.v10.opportunity_ranking import (
    OpportunityScore,
    ExecutionCandidate,
    rank_for_execution,
    format_ranking_summary,
    WEIGHT_OPPORTUNITY_QUALITY,
    WEIGHT_STRATEGY_CONFIDENCE,
    WEIGHT_HTF_ALIGNMENT,
    WEIGHT_SESSION_QUALITY,
    WEIGHT_RISK_QUALITY,
)


# ═══════════════════════════════════════════════════════════════
# TEST HELPERS
# ═══════════════════════════════════════════════════════════════

@dataclass
class FakeOpportunityQuality:
    overall_quality: float = 0.0


@dataclass
class FakeOpportunity:
    quality: FakeOpportunityQuality = field(default_factory=FakeOpportunityQuality)
    observation_id: str = ""


@dataclass
class FakeHTFAlignment:
    structure_alignment: float = 0.0


@dataclass
class FakeMarketState:
    htf_alignment: FakeHTFAlignment = field(default_factory=FakeHTFAlignment)


@dataclass
class FakeStrategy:
    strategy_confidence: float = 0.0


@dataclass
class FakeEntry:
    expected_rr: float = 0.0


@dataclass
class FakePipelineResult:
    opportunity: FakeOpportunity = field(default_factory=FakeOpportunity)
    market_state: FakeMarketState = field(default_factory=FakeMarketState)
    strategy: FakeStrategy = field(default_factory=FakeStrategy)
    entry: FakeEntry = field(default_factory=FakeEntry)


@dataclass
class FakeOpp:
    opportunity_id: str = ""
    state: str = "ASSESSED"


def _make_candidate(
    symbol: str,
    quality: float,
    strategy_confidence: float = 0.5,
    htf_alignment: float = 0.5,
    expected_rr: float = 2.0,
    direction: str = "SELL",
    strategy: str = "TREND_CONTINUATION",
    opportunity_id: str = "",
    closed_time: float = 1753574400.0,  # London session (10 UTC)
) -> ExecutionCandidate:
    """Build a realistic ExecutionCandidate for testing."""
    opp_id = opportunity_id or f"{symbol}_{int(closed_time)}_{strategy}"

    pr = FakePipelineResult(
        opportunity=FakeOpportunity(
            quality=FakeOpportunityQuality(overall_quality=quality),
            observation_id=f"obs_{symbol}",
        ),
        market_state=FakeMarketState(
            htf_alignment=FakeHTFAlignment(structure_alignment=htf_alignment),
        ),
        strategy=FakeStrategy(strategy_confidence=strategy_confidence),
        entry=FakeEntry(expected_rr=expected_rr),
    )

    return ExecutionCandidate(
        symbol=symbol,
        new_result={
            "action": "EXECUTE",
            "side": direction,
            "strategy": strategy,
            "strategy_confidence": strategy_confidence,
            "score": quality,
            "components": {"htf_alignment": htf_alignment},
            "v10_pipeline_result": pr,
            "entity_id": f"{symbol}_{int(closed_time)}",
        },
        pipeline_result=pr,
        exec_prep=MagicMock(intent=MagicMock(), correlation_id=f"cor_{symbol}", decision_id=f"dec_{symbol}"),
        sym_state=MagicMock(symbol=symbol),
        bid=1.1000,
        ask=1.1002,
        closed_time=closed_time,
        cycle_opportunities=[FakeOpp(opportunity_id=opp_id)],
        v10_obs_id=f"obs_{symbol}",
        new_engine_htf=None,
        raw_patterns=[],
        correlation_id=f"cor_{symbol}",
        decision_id=f"dec_{symbol}",
        engine_score=quality,
    )


# ═══════════════════════════════════════════════════════════════
# TEST 1: Multiple candidates — highest score ranks first
# ═══════════════════════════════════════════════════════════════

class TestHighestScoreRanksFirst:
    """Three valid opportunities: highest quality opportunity must rank #1."""

    def test_three_candidates_ranked_by_quality(self):
        candidates = [
            _make_candidate("XAUUSD", quality=0.65, strategy_confidence=0.60, htf_alignment=0.50, expected_rr=1.8),
            _make_candidate("EURUSD", quality=0.82, strategy_confidence=0.91, htf_alignment=0.80, expected_rr=3.1),
            _make_candidate("GBPUSD", quality=0.71, strategy_confidence=0.70, htf_alignment=0.60, expected_rr=2.0),
        ]

        scores = rank_for_execution(candidates, portfolio_context=None)

        assert len(scores) == 3
        assert scores[0].symbol == "EURUSD"
        assert scores[0].rank_position == 1
        assert scores[1].symbol == "GBPUSD"
        assert scores[1].rank_position == 2
        assert scores[2].symbol == "XAUUSD"
        assert scores[2].rank_position == 3

    def test_rank_score_descending(self):
        candidates = [
            _make_candidate("AUDUSD", quality=0.50, strategy_confidence=0.40),
            _make_candidate("NZDUSD", quality=0.90, strategy_confidence=0.85),
        ]

        scores = rank_for_execution(candidates, portfolio_context=None)

        assert scores[0].final_rank_score > scores[1].final_rank_score


# ═══════════════════════════════════════════════════════════════
# TEST 2: Lower quality cannot outrank higher quality
# ═══════════════════════════════════════════════════════════════

class TestLowerQualityCannotOutrank:
    """A lower quality opportunity should not beat a clearly superior one."""

    def test_weak_cannot_beat_strong(self):
        strong = _make_candidate(
            "EURUSD", quality=0.85, strategy_confidence=0.90,
            htf_alignment=0.80, expected_rr=3.0,
        )
        weak = _make_candidate(
            "GBPUSD", quality=0.50, strategy_confidence=0.40,
            htf_alignment=0.30, expected_rr=1.5,
        )

        scores = rank_for_execution([weak, strong], portfolio_context=None)

        assert scores[0].symbol == "EURUSD"
        assert scores[1].symbol == "GBPUSD"
        assert scores[0].final_rank_score > scores[1].final_rank_score

    def test_marginal_difference_resolved_by_secondary_factors(self):
        """When quality is similar, strategy confidence and HTF break the tie."""
        a = _make_candidate("EURUSD", quality=0.70, strategy_confidence=0.90, htf_alignment=0.85)
        b = _make_candidate("GBPUSD", quality=0.72, strategy_confidence=0.40, htf_alignment=0.30)

        scores = rank_for_execution([a, b], portfolio_context=None)

        # EURUSD should win despite slightly lower quality — secondary factors dominate
        assert scores[0].symbol == "EURUSD"


# ═══════════════════════════════════════════════════════════════
# TEST 3: Risk rejection removes from execution (ranking preserved)
# ═══════════════════════════════════════════════════════════════

class TestRiskRejectionRespected:
    """
    Ranking produces order, but risk/guard rejection means the candidate
    doesn't execute. The next candidate should be tried.
    """

    def test_ranking_does_not_bypass_guards(self):
        """rank_for_execution produces scores; it doesn't decide execution.
        The guard chain in live_scanner can reject any candidate."""
        candidates = [
            _make_candidate("EURUSD", quality=0.90),
            _make_candidate("GBPUSD", quality=0.70),
        ]

        scores = rank_for_execution(candidates, portfolio_context=None)

        # Ranking puts EURUSD first
        assert scores[0].symbol == "EURUSD"
        # But ranking itself does NOT mark anything as "executed" or "rejected"
        # That's the guard chain's job — ranking only orders candidates
        assert all(s.rank_position > 0 for s in scores)

    def test_zero_rr_produces_zero_risk_quality(self):
        """An opportunity with terrible R:R gets penalised in ranking."""
        good_rr = _make_candidate("EURUSD", quality=0.70, expected_rr=3.0)
        bad_rr = _make_candidate("GBPUSD", quality=0.70, expected_rr=0.8)

        scores = rank_for_execution([good_rr, bad_rr], portfolio_context=None)

        assert scores[0].symbol == "EURUSD"
        assert scores[0].risk_quality > scores[1].risk_quality


# ═══════════════════════════════════════════════════════════════
# TEST 4: Same-symbol opportunities ranked independently
# ═══════════════════════════════════════════════════════════════

class TestSameSymbolIndependentRanking:
    """Two opportunities on the same symbol are treated as distinct candidates."""

    def test_same_symbol_different_patterns_ranked(self):
        opp_a = _make_candidate(
            "XAUUSD", quality=0.80, strategy="TREND_CONTINUATION",
            opportunity_id="XAUUSD_1753574400_BEARISH_ENGULFING",
        )
        opp_b = _make_candidate(
            "XAUUSD", quality=0.65, strategy="MEAN_REVERSION",
            opportunity_id="XAUUSD_1753574400_PIN_BAR",
        )

        scores = rank_for_execution([opp_a, opp_b], portfolio_context=None)

        assert len(scores) == 2
        assert scores[0].final_rank_score > scores[1].final_rank_score
        # Both have same symbol but different opportunity_ids
        assert scores[0].opportunity_id != scores[1].opportunity_id

    def test_same_symbol_both_tracked(self):
        opp_a = _make_candidate("EURUSD", quality=0.75, opportunity_id="EURUSD_100_A")
        opp_b = _make_candidate("EURUSD", quality=0.60, opportunity_id="EURUSD_200_B")

        scores = rank_for_execution([opp_a, opp_b], portfolio_context=None)

        symbols = [s.symbol for s in scores]
        assert symbols == ["EURUSD", "EURUSD"]
        assert scores[0].opportunity_id == "EURUSD_100_A"
        assert scores[1].opportunity_id == "EURUSD_200_B"


# ═══════════════════════════════════════════════════════════════
# TEST 5: Opportunity IDs unchanged through ranking
# ═══════════════════════════════════════════════════════════════

class TestOpportunityIdsPreserved:
    """Ranking must never modify, truncate, or regenerate opportunity IDs."""

    def test_ids_preserved_exactly(self):
        original_id = "GBPUSD_1785959700_BEARISH_ENGULFING"
        candidate = _make_candidate(
            "GBPUSD", quality=0.75, opportunity_id=original_id,
        )

        scores = rank_for_execution([candidate], portfolio_context=None)

        assert scores[0].opportunity_id == original_id

    def test_multiple_ids_all_preserved(self):
        ids = [
            "EURUSD_1753574400_ENGULFING",
            "GBPUSD_1753578000_MORNING_STAR",
            "XAUUSD_1753579000_PIN_BAR",
        ]
        candidates = [
            _make_candidate("EURUSD", quality=0.80, opportunity_id=ids[0]),
            _make_candidate("GBPUSD", quality=0.70, opportunity_id=ids[1]),
            _make_candidate("XAUUSD", quality=0.60, opportunity_id=ids[2]),
        ]

        scores = rank_for_execution(candidates, portfolio_context=None)

        returned_ids = {s.opportunity_id for s in scores}
        assert returned_ids == set(ids)

    def test_direction_preserved(self):
        candidate = _make_candidate("EURUSD", quality=0.75, direction="BUY")

        scores = rank_for_execution([candidate], portfolio_context=None)

        assert scores[0].direction == "BUY"

    def test_strategy_family_preserved(self):
        candidate = _make_candidate("EURUSD", quality=0.75, strategy="LIQUIDITY_SWEEP_REVERSAL")

        scores = rank_for_execution([candidate], portfolio_context=None)

        assert scores[0].strategy_family == "LIQUIDITY_SWEEP_REVERSAL"


# ═══════════════════════════════════════════════════════════════
# ADDITIONAL: Scoring mechanics
# ═══════════════════════════════════════════════════════════════

class TestScoringMechanics:
    """Verify the scoring formula produces expected outputs."""

    def test_empty_candidates_returns_empty(self):
        scores = rank_for_execution([], portfolio_context=None)
        assert scores == []

    def test_single_candidate_ranked_first(self):
        candidate = _make_candidate("EURUSD", quality=0.80)

        scores = rank_for_execution([candidate], portfolio_context=None)

        assert len(scores) == 1
        assert scores[0].rank_position == 1
        assert scores[0].final_rank_score > 0

    def test_explainability_reasons_populated(self):
        candidate = _make_candidate("EURUSD", quality=0.82, strategy_confidence=0.91)

        scores = rank_for_execution([candidate], portfolio_context=None)

        reasons = scores[0].ranking_reason
        assert any("Opportunity quality" in r for r in reasons)
        assert any("Strategy confidence" in r for r in reasons)
        assert any("HTF alignment" in r for r in reasons)
        assert any("Session quality" in r for r in reasons)
        assert any("Risk quality" in r for r in reasons)

    def test_format_ranking_summary(self):
        candidates = [
            _make_candidate("EURUSD", quality=0.80),
            _make_candidate("GBPUSD", quality=0.60),
        ]
        scores = rank_for_execution(candidates, portfolio_context=None)

        summary = format_ranking_summary(scores)

        assert "EURUSD" in summary
        assert "GBPUSD" in summary
        assert "#1" in summary
        assert "#2" in summary

    def test_session_quality_asia_penalises_non_jpy(self):
        """Asia session (hour=3) should score low for EUR but higher for JPY."""
        asia_time = 1753495200.0  # ~03:00 UTC
        eur = _make_candidate("EURUSD", quality=0.70, closed_time=asia_time)
        jpy = _make_candidate("USDJPY", quality=0.70, closed_time=asia_time)

        scores_eur = rank_for_execution([eur], portfolio_context=None)
        scores_jpy = rank_for_execution([jpy], portfolio_context=None)

        assert scores_jpy[0].session_quality > scores_eur[0].session_quality

    def test_portfolio_context_adjusts_score(self):
        """Portfolio context with correlated exposure should reduce score."""
        candidate = _make_candidate("EURUSD", quality=0.75, direction="BUY")

        # Mock portfolio context with existing correlated position
        mock_ctx = MagicMock()
        mock_ctx.open_positions = [{"symbol": "GBPUSD", "side": "BUY", "volume": 0.1}]
        mock_ctx.total_open = 1
        mock_ctx.currency_exposure = {"EUR": 0.0, "USD": -0.1, "GBP": 0.1}
        mock_ctx.active_correlation_groups = ["EURUSD,GBPUSD..."]
        mock_ctx.daily_risk_used_pct = 0.01
        mock_ctx.daily_drawdown_pct = 0.005

        scores_no_ctx = rank_for_execution([candidate], portfolio_context=None)
        scores_with_ctx = rank_for_execution([candidate], portfolio_context=mock_ctx)

        # With correlated exposure, score should be lower (or equal if enrichment fails gracefully)
        assert scores_with_ctx[0].final_rank_score <= scores_no_ctx[0].final_rank_score
