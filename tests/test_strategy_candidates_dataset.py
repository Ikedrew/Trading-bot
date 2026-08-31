"""Tests for the strategy_candidates persistence dataset.

Verifies that select_strategy() persists every evaluated strategy candidate
(not just the winner) with correct rank, selection, lineage, and that
persistence failure never affects strategy selection.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.v10.market_state import (
    V10MarketState, H4State, H1State, M15State, M5State,
    RegimeState, LocationState, HTFAlignment,
)
from core.v10.opportunity_assessment import OpportunityAssessment, OpportunityQuality
from core.v10.strategy_engine import select_strategy
from core.identity.canonical import make_canonical_opportunity_id, mint_observation_id
import core.persistence.strategy_candidates_writer as scw


# ═══════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════


def _opp(state="VALID", bias="NEUTRAL", obs_id="test123"):
    return OpportunityAssessment(
        observation_id=obs_id, symbol="TEST", timestamp_utc=1000.0,
        opportunity_state=state, directional_bias=bias,
        opportunity_type="ZONE_REACTION",
        quality=OpportunityQuality(overall_quality=0.7),
    )


def _dual_candidate_state():
    """State qualifying for BOTH MEAN_REVERSION and RANGE_REACTION."""
    return V10MarketState(
        symbol="TEST", timestamp_utc=1000.0,
        h4=H4State(trend="NEUTRAL", trend_strength=0.15),
        h1=H1State(dominant_trend="NEUTRAL", structural_clarity=0.75,
                   swing_high=1.0920, swing_low=1.0850),
        m15=M15State(pullback_active=True),
        m5=M5State(rejection_present=True, rejection_strength_atr=0.8,
                   rejection_direction="BEARISH"),
        regime=RegimeState(regime="RANGING", momentum_strength=0.2),
        location=LocationState(range_position=0.80, premium_discount="PREMIUM"),
        htf_alignment=HTFAlignment(macro_bias="NEUTRAL", structure_alignment=0.3),
    )


def _single_candidate_state():
    """State qualifying for only TREND_CONTINUATION."""
    return V10MarketState(
        symbol="TEST", timestamp_utc=1000.0,
        h4=H4State(trend="BULLISH", trend_strength=0.7, market_phase="IMPULSE"),
        h1=H1State(dominant_trend="BULLISH", bos_confirmed=True,
                   bos_direction="BULLISH", structural_clarity=0.8),
        m15=M15State(pullback_active=True, pullback_depth_atr=1.2,
                    internal_bos=True, internal_bos_direction="BULLISH"),
        m5=M5State(rejection_present=True, rejection_direction="BULLISH"),
        regime=RegimeState(regime="TRENDING"),
        location=LocationState(inside_institutional_zone=True,
                               location_type="DEMAND_OB"),
    )


@pytest.fixture(autouse=True)
def _redirect_writer(tmp_path):
    """Redirect the writer's local dir to a temp path for every test."""
    original = scw._LOCAL_DIR
    scw._LOCAL_DIR = str(tmp_path)
    yield
    scw._LOCAL_DIR = original


def _read_records(tmp_path):
    """Read all persisted candidate records from the redirected writer."""
    records = []
    for f in Path(scw._LOCAL_DIR).rglob("*.jsonl"):
        for line in f.read_text(encoding="utf-8").strip().split("\n"):
            if line.strip():
                records.append(json.loads(line))
    return records


# ═══════════════════════════════════════════════════════════════
# TEST 1 — MULTIPLE CANDIDATES
# ═══════════════════════════════════════════════════════════════


class TestMultipleCandidates:
    def test_dual_state_produces_two_records(self):
        state = _dual_candidate_state()
        opp = _opp()
        result = select_strategy(state, opp)
        records = _read_records(None)
        assert len(records) == 2

    def test_each_record_has_correct_family(self):
        state = _dual_candidate_state()
        select_strategy(state, _opp())
        records = _read_records(None)
        families = {r["strategy_family"] for r in records}
        assert families == {"MEAN_REVERSION", "RANGE_REACTION"}

    def test_confidence_preserved(self):
        state = _dual_candidate_state()
        result = select_strategy(state, _opp())
        records = _read_records(None)
        by_family = {r["strategy_family"]: r for r in records}
        assert by_family["MEAN_REVERSION"]["confidence"] == pytest.approx(
            result.strategy_confidence
        )
        assert by_family["RANGE_REACTION"]["confidence"] > 0

    def test_reasoning_preserved(self):
        state = _dual_candidate_state()
        select_strategy(state, _opp())
        records = _read_records(None)
        for rec in records:
            assert isinstance(rec["reasoning"], list)
            assert len(rec["reasoning"]) > 0

    def test_supporting_conditions_preserved(self):
        state = _dual_candidate_state()
        select_strategy(state, _opp())
        records = _read_records(None)
        for rec in records:
            assert isinstance(rec["supporting_conditions"], dict)
            assert len(rec["supporting_conditions"]) > 0

    def test_single_candidate_state_produces_one_record(self):
        state = _single_candidate_state()
        select_strategy(state, _opp(bias="BULLISH"))
        records = _read_records(None)
        assert len(records) == 1
        assert records[0]["strategy_family"] == "TREND_CONTINUATION"


# ═══════════════════════════════════════════════════════════════
# TEST 2 — RANK
# ═══════════════════════════════════════════════════════════════


class TestRanking:
    def test_rank_matches_post_sort_order(self):
        """MEAN_REVERSION has higher priority than RANGE_REACTION
        in STRATEGY_PRIORITY, so it must be rank 1."""
        state = _dual_candidate_state()
        select_strategy(state, _opp())
        records = _read_records(None)
        by_rank = sorted(records, key=lambda r: r["rank"])
        assert by_rank[0]["rank"] == 1
        assert by_rank[0]["strategy_family"] == "MEAN_REVERSION"
        assert by_rank[1]["rank"] == 2
        assert by_rank[1]["strategy_family"] == "RANGE_REACTION"

    def test_ranks_are_sequential_from_one(self):
        state = _dual_candidate_state()
        select_strategy(state, _opp())
        records = _read_records(None)
        ranks = sorted(r["rank"] for r in records)
        assert ranks == list(range(1, len(records) + 1))


# ═══════════════════════════════════════════════════════════════
# TEST 3 — WINNER
# ═══════════════════════════════════════════════════════════════


class TestWinner:
    def test_exactly_one_selected(self):
        state = _dual_candidate_state()
        select_strategy(state, _opp())
        records = _read_records(None)
        selected = [r for r in records if r["selected"]]
        assert len(selected) == 1

    def test_selected_is_the_engine_winner(self):
        state = _dual_candidate_state()
        result = select_strategy(state, _opp())
        records = _read_records(None)
        selected = [r for r in records if r["selected"]][0]
        assert selected["strategy_family"] == result.strategy_family

    def test_selected_is_rank_one(self):
        state = _dual_candidate_state()
        select_strategy(state, _opp())
        records = _read_records(None)
        selected = [r for r in records if r["selected"]][0]
        assert selected["rank"] == 1


# ═══════════════════════════════════════════════════════════════
# TEST 4 — LINEAGE
# ═══════════════════════════════════════════════════════════════


class TestLineage:
    def test_observation_id_present(self):
        state = _dual_candidate_state()
        select_strategy(state, _opp())
        records = _read_records(None)
        for rec in records:
            assert rec["observation_id"]
            assert rec["observation_id"] == "TEST.M5.1000"

    def test_candidates_receive_parent_opportunity_canonical_id(self):
        state = _dual_candidate_state()
        parent_canonical_id = make_canonical_opportunity_id(
            symbol=state.symbol,
            bar_time=state.timestamp_utc,
            pattern="ZONE_REACTION",
        )
        lineage = {
            "canonical_opportunity_id": parent_canonical_id,
            "observation_id": mint_observation_id(
                symbol=state.symbol,
                bar_time=state.timestamp_utc,
                timeframe="M5",
            ),
        }

        select_strategy(state, _opp(), lineage=lineage)

        records = _read_records(None)
        assert records
        for rec in records:
            assert rec["canonical_opportunity_id"] == parent_canonical_id

    def test_canonical_id_passed_via_lineage(self):
        state = _dual_candidate_state()
        lineage = {
            "canonical_opportunity_id": "TEST*1000*ZONE_REACTION",
            "cycle_id": 42,
        }
        select_strategy(state, _opp(), lineage=lineage)
        records = _read_records(None)
        for rec in records:
            assert rec["canonical_opportunity_id"] == "TEST*1000*ZONE_REACTION"
            assert rec["cycle_id"] == 42

    def test_two_opportunities_on_same_observation_remain_distinguishable(self):
        state = _dual_candidate_state()
        observation_id = mint_observation_id(
            symbol=state.symbol,
            bar_time=state.timestamp_utc,
            timeframe="M5",
        )
        zone_root = make_canonical_opportunity_id(
            symbol=state.symbol,
            bar_time=state.timestamp_utc,
            pattern="ZONE_REACTION",
        )
        breakout_root = make_canonical_opportunity_id(
            symbol=state.symbol,
            bar_time=state.timestamp_utc,
            pattern="BREAKOUT",
        )

        select_strategy(
            state,
            _opp(),
            lineage={
                "canonical_opportunity_id": zone_root,
                "observation_id": observation_id,
            },
        )
        select_strategy(
            state,
            _opp(),
            lineage={
                "canonical_opportunity_id": breakout_root,
                "observation_id": observation_id,
            },
        )

        records = _read_records(None)
        assert {r["observation_id"] for r in records} == {observation_id}
        assert {r["canonical_opportunity_id"] for r in records} == {
            zone_root,
            breakout_root,
        }

    def test_observation_id_is_parent_reference_not_opportunity_identity(self):
        state = _dual_candidate_state()
        observation_id = mint_observation_id(
            symbol=state.symbol,
            bar_time=state.timestamp_utc,
            timeframe="M5",
        )
        canonical_id = make_canonical_opportunity_id(
            symbol=state.symbol,
            bar_time=state.timestamp_utc,
            pattern="ZONE_REACTION",
        )

        select_strategy(
            state,
            _opp(),
            lineage={
                "canonical_opportunity_id": canonical_id,
                "observation_id": observation_id,
            },
        )

        records = _read_records(None)
        for rec in records:
            assert rec["observation_id"] == observation_id
            assert rec["canonical_opportunity_id"] == canonical_id
            assert rec["canonical_opportunity_id"] != rec["observation_id"]

    def test_missing_canonical_does_not_fallback_to_observation_id(self):
        state = _dual_candidate_state()

        select_strategy(state, _opp())

        records = _read_records(None)
        for rec in records:
            assert rec["observation_id"] == "TEST.M5.1000"
            assert rec["canonical_opportunity_id"] is None

    def test_decision_id_defaults_empty(self):
        """decision_id is not yet minted at strategy-selection time — empty is correct."""
        state = _dual_candidate_state()
        select_strategy(state, _opp())
        records = _read_records(None)
        for rec in records:
            assert rec["decision_id"] == ""

    def test_candidate_id_deterministic(self):
        state = _dual_candidate_state()
        select_strategy(state, _opp())
        records = _read_records(None)
        ids = [r["candidate_id"] for r in records]
        assert len(ids) == len(set(ids))  # No duplicates
        for rec in records:
            assert rec["strategy_family"] in rec["candidate_id"]

    def test_symbol_present(self):
        state = _dual_candidate_state()
        select_strategy(state, _opp())
        records = _read_records(None)
        for rec in records:
            assert rec["symbol"] == "TEST"

    def test_strategy_selection_unchanged_by_lineage(self):
        state = _dual_candidate_state()
        opp = _opp()
        baseline = select_strategy(state, opp)
        lineaged = select_strategy(
            state,
            opp,
            lineage={
                "canonical_opportunity_id": make_canonical_opportunity_id(
                    symbol=state.symbol,
                    bar_time=state.timestamp_utc,
                    pattern=opp.opportunity_type,
                ),
                "observation_id": mint_observation_id(
                    symbol=state.symbol,
                    bar_time=state.timestamp_utc,
                    timeframe="M5",
                ),
            },
        )

        assert lineaged.strategy_family == baseline.strategy_family
        assert lineaged.strategy_confidence == baseline.strategy_confidence
        assert lineaged.reasoning == baseline.reasoning
        assert lineaged.supporting_conditions == baseline.supporting_conditions


# ═══════════════════════════════════════════════════════════════
# TEST 5 — PERSISTENCE FAILURE ISOLATION
# ═══════════════════════════════════════════════════════════════


class TestFailureIsolation:
    def test_selection_unchanged_when_writer_fails(self):
        state = _dual_candidate_state()
        opp = _opp()
        expected = select_strategy(state, opp)

        original = scw.persist_strategy_candidates

        def _failing_persist(**kwargs):
            raise RuntimeError("simulated disk failure")

        scw.persist_strategy_candidates = _failing_persist
        try:
            actual = select_strategy(state, opp)
        finally:
            scw.persist_strategy_candidates = original

        assert actual.strategy_family == expected.strategy_family
        assert actual.strategy_confidence == expected.strategy_confidence
        assert actual.reasoning == expected.reasoning
        assert actual.supporting_conditions == expected.supporting_conditions

    def test_no_exception_propagates(self):
        state = _dual_candidate_state()

        def _failing_persist(**kwargs):
            raise RuntimeError("boom")

        original = scw.persist_strategy_candidates
        scw.persist_strategy_candidates = _failing_persist
        try:
            result = select_strategy(state, _opp())  # Must not raise
            assert result.strategy_family == "MEAN_REVERSION"
        finally:
            scw.persist_strategy_candidates = original


# ═══════════════════════════════════════════════════════════════
# TEST 6 — NO BEHAVIOURAL REGRESSION / EDGE CASES
# ═══════════════════════════════════════════════════════════════


class TestNoRegression:
    def test_invalid_opportunity_no_records(self):
        """INVALID opportunities return early — no candidates to persist."""
        state = _dual_candidate_state()
        result = select_strategy(state, _opp(state="INVALID"))
        assert result.strategy_family == "NONE"
        assert _read_records(None) == []

    def test_no_candidates_no_records(self):
        """Empty market state produces no candidates — nothing persisted."""
        state = V10MarketState(symbol="TEST", timestamp_utc=1000.0)
        result = select_strategy(state, _opp())
        assert result.strategy_family == "NONE"
        assert _read_records(None) == []

    def test_backward_compatible_two_arg_call(self):
        """Existing 2-arg callers (no lineage) must continue to work."""
        state = _dual_candidate_state()
        result = select_strategy(state, _opp())
        assert result.strategy_family == "MEAN_REVERSION"
        records = _read_records(None)
        assert len(records) == 2
        for rec in records:
            assert rec["observation_id"] == "TEST.M5.1000"
            assert rec["canonical_opportunity_id"] is None

    def test_schema_version_present(self):
        state = _dual_candidate_state()
        select_strategy(state, _opp())
        records = _read_records(None)
        for rec in records:
            assert rec["schema_version"] == "strategy_candidates_v1"

    def test_written_to_symbol_date_partition(self):
        """Records must be written to {SYMBOL}/{YYYY-MM-DD}.jsonl structure."""
        state = _dual_candidate_state()
        select_strategy(state, _opp())
        # timestamp 1000.0 = 1970-01-01 in UTC
        expected_dir = Path(scw._LOCAL_DIR) / "TEST"
        assert expected_dir.exists()
        jsonl_files = list(expected_dir.glob("*.jsonl"))
        assert len(jsonl_files) == 1
        assert jsonl_files[0].stem == "1970-01-01"

    def test_all_json_serialisable(self):
        state = _dual_candidate_state()
        select_strategy(state, _opp())
        records = _read_records(None)
        for rec in records:
            # Re-serialising must succeed without special encoders
            json.dumps(rec, separators=(",", ":"))
