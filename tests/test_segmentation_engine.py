"""Tests for V10 Research Segmentation Engine."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _s3_fake import install_fake_s3, reset_fake_s3

from research_engine.v10.segmentation_engine import ResearchSegmenter


# ═══════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════

def _event(trade_id="pos_1", symbol="EURUSD", session="LONDON", regime="TRENDING",
           volatility="NEUTRAL", confidence=0.7, score=0.55, pnl=-0.5, r=-1.0):
    return {
        "trade_id": trade_id,
        "execution": {
            "ticket": int(trade_id.split("_")[1]),
            "symbol": symbol,
            "direction": "BUY",
            "entry_price": 1.1,
            "exit_price": 1.099,
            "entry_time": 1784808000.0,
            "exit_time": 1784809000.0,
            "stop_loss": 1.098,
            "take_profit": 1.103,
            "gross_profit": pnl,
            "commission": -0.04,
            "swap": 0.0,
            "net_realised_pnl": pnl - 0.04,
            "r_multiple": r,
            "volume": 0.01,
            "duration_seconds": 1000,
            "exit_reason": "STOP_LOSS",
        },
        "decision": {
            "strategy": "REVERSAL",
            "score": score,
            "confidence": confidence,
            "decision_type": "sym_cycle",
            "decision_timestamp": 1784808000.0,
            "components": {"location": 0.6, "structure": 0.5},
            "weakest_component": "structure",
            "ev": None,
            "p_success": None,
        },
        "market": {
            "regime": regime,
            "session": session,
            "volatility": volatility,
            "trend_state": "BULLISH",
            "higher_timeframe_bias": "BULLISH",
            "h4_phase": "IMPULSE",
            "h1_clarity": 0.6,
        },
        "strategy": {
            "family": "REVERSAL",
            "pattern": "HAMMER",
            "conditions_met": 2,
            "strategy_confidence": confidence,
            "opportunity_quality": 0.55,
            "opportunity_type": "ZONE_REACTION",
        },
        "quality": {
            "anomaly": False,
            "anomaly_reasons": [],
            "governance_status": "WARNING",
            "data_completeness": "COMPLETE",
            "missing": [],
            "join_method": "sym_cycle",
            "pnl_source": "MT5_BROKER",
        },
    }


@pytest.fixture
def segmenter():
    """Create segmenter with a synthetic research_universe artifact seeded in S3."""
    events = [
        _event("pos_1", "EURUSD", "LONDON", "TRENDING", "NEUTRAL", 0.7, 0.6),
        _event("pos_2", "EURUSD", "NEW_YORK", "RANGING", "HIGH", 0.8, 0.75),
        _event("pos_3", "GBPUSD", "LONDON", "TRENDING", "NEUTRAL", 0.5, 0.55),
        _event("pos_4", "US500", "NEW_YORK", "TRENDING", "HIGH", 0.9, 0.8),
        _event("pos_5", "XAUUSD", "ASIAN", "TRANSITIONAL", "LOW", 0.3, 0.4),
        _event("pos_6", "NZDUSD", "LONDON", "RANGING", "NEUTRAL", 0.6, 0.5),
        _event("pos_7", "US500", "LONDON_NY_OVERLAP", "TRENDING", "HIGH", 0.85, 0.72),
    ]
    fake = install_fake_s3()
    fake.add_artifact("research_universe", events)
    try:
        yield ResearchSegmenter()
    finally:
        reset_fake_s3()


# ═══════════════════════════════════════════════════════════════
# TESTS
# ═══════════════════════════════════════════════════════════════

class TestInstrumentSegmentation:
    def test_filter_by_symbol(self, segmenter):
        result = segmenter.filter(instrument="EURUSD")
        assert len(result) == 2
        assert all(e["execution"]["symbol"] == "EURUSD" for e in result)

    def test_filter_by_fx(self, segmenter):
        result = segmenter.filter(instrument="FX")
        symbols = {e["execution"]["symbol"] for e in result}
        assert symbols <= {"EURUSD", "GBPUSD", "NZDUSD"}
        assert len(result) == 4

    def test_filter_by_index(self, segmenter):
        result = segmenter.filter(instrument="INDEX")
        assert len(result) == 2
        assert all(e["execution"]["symbol"] == "US500" for e in result)

    def test_filter_by_commodity(self, segmenter):
        result = segmenter.filter(instrument="COMMODITY")
        assert len(result) == 1
        assert result[0]["execution"]["symbol"] == "XAUUSD"


class TestSessionSegmentation:
    def test_london(self, segmenter):
        result = segmenter.filter(session="LONDON")
        assert len(result) == 3
        assert all(e["market"]["session"] == "LONDON" for e in result)

    def test_new_york(self, segmenter):
        result = segmenter.filter(session="NEW_YORK")
        assert len(result) == 2

    def test_asian(self, segmenter):
        result = segmenter.filter(session="ASIAN")
        assert len(result) == 1


class TestRegimeSegmentation:
    def test_trending(self, segmenter):
        result = segmenter.filter(regime="TRENDING")
        assert len(result) == 4

    def test_ranging(self, segmenter):
        result = segmenter.filter(regime="RANGING")
        assert len(result) == 2

    def test_transitional(self, segmenter):
        result = segmenter.filter(regime="TRANSITIONAL")
        assert len(result) == 1


class TestVolatilitySegmentation:
    def test_high(self, segmenter):
        result = segmenter.filter(volatility="HIGH")
        assert len(result) == 3

    def test_low(self, segmenter):
        result = segmenter.filter(volatility="LOW")
        assert len(result) == 1

    def test_neutral(self, segmenter):
        result = segmenter.filter(volatility="NEUTRAL")
        assert len(result) == 3


class TestConfidenceSegmentation:
    def test_high_confidence(self, segmenter):
        # Threshold >= 0.6
        result = segmenter.filter(confidence="HIGH")
        assert all(e["decision"]["confidence"] >= 0.6 for e in result)

    def test_low_confidence(self, segmenter):
        # Threshold < 0.4
        result = segmenter.filter(confidence="LOW")
        assert all(e["decision"]["confidence"] < 0.4 for e in result)
        assert len(result) == 1


class TestScoreBuckets:
    def test_high_score(self, segmenter):
        # >= 0.7
        result = segmenter.filter(score_bucket="HIGH")
        assert all(e["decision"]["score"] >= 0.7 for e in result)

    def test_medium_score(self, segmenter):
        # 0.5 <= score < 0.7
        result = segmenter.filter(score_bucket="MEDIUM")
        assert all(0.5 <= e["decision"]["score"] < 0.7 for e in result)

    def test_low_score(self, segmenter):
        # < 0.5
        result = segmenter.filter(score_bucket="LOW")
        assert all(e["decision"]["score"] < 0.5 for e in result)


class TestCombinedFilters:
    def test_multi_dimension(self, segmenter):
        # US500 + NEW_YORK + TRENDING
        result = segmenter.filter(instrument="US500", session="NEW_YORK", regime="TRENDING")
        assert len(result) == 1
        e = result[0]
        assert e["execution"]["symbol"] == "US500"
        assert e["market"]["session"] == "NEW_YORK"
        assert e["market"]["regime"] == "TRENDING"

    def test_no_matches(self, segmenter):
        result = segmenter.filter(instrument="XAUUSD", session="LONDON")
        assert result == []


class TestEmptySegment:
    def test_nonexistent_symbol(self, segmenter):
        result = segmenter.filter(instrument="BTCUSD")
        assert result == []

    def test_nonexistent_regime(self, segmenter):
        result = segmenter.filter(regime="EXPLOSIVE")
        assert result == []


class TestPreservesCanonicalPnL:
    def test_pnl_preserved(self, segmenter):
        all_events = segmenter.events
        filtered = segmenter.filter(instrument="EURUSD")
        for e in filtered:
            original = next(o for o in all_events if o["trade_id"] == e["trade_id"])
            assert e["execution"]["net_realised_pnl"] == original["execution"]["net_realised_pnl"]
            assert e["execution"]["gross_profit"] == original["execution"]["gross_profit"]


class TestNoDuplicates:
    def test_no_duplicates_in_segment(self, segmenter):
        result = segmenter.filter(regime="TRENDING")
        ids = [e["trade_id"] for e in result]
        assert len(ids) == len(set(ids))


class TestBuildAllSegments:
    def test_reports_generated(self, segmenter, tmp_path):
        result = segmenter.build_all_segments(
            segments_dir=str(tmp_path / "segments"),
            reports_dir=str(tmp_path / "reports"),
        )
        assert "error" not in result
        assert (tmp_path / "reports" / "segmentation_engine_report.json").exists()
        assert (tmp_path / "reports" / "segmentation_engine_report.md").exists()

    def test_segment_files_created(self, segmenter, tmp_path):
        segmenter.build_all_segments(
            segments_dir=str(tmp_path / "segments"),
            reports_dir=str(tmp_path / "reports"),
        )
        assert (tmp_path / "segments" / "instruments" / "EURUSD.jsonl").exists()
        assert (tmp_path / "segments" / "sessions" / "LONDON.jsonl").exists()
        assert (tmp_path / "segments" / "regimes" / "TRENDING.jsonl").exists()


class TestLiveData:
    def test_live_segmentation(self):
        # Live/integration test: the research universe is now authoritative in S3
        # (not the local file). This exercises the REAL S3 source, so it is gated
        # behind an explicit opt-in to avoid network access in normal runs.
        import os
        if os.environ.get("RESEARCH_LIVE_S3_TESTS") != "1":
            pytest.skip("live S3 test — set RESEARCH_LIVE_S3_TESTS=1 to run")
        from research_engine.data_access.s3_source import get_default_source
        events = get_default_source().read_artifact("research_universe")
        if not events:
            pytest.skip("Research universe not available in S3")
        seg = ResearchSegmenter()
        result = seg.build_all_segments(
            segments_dir="data/research/segments",
            reports_dir="reports/research",
        )
        assert "error" not in result
        assert result["total_events"] > 0
