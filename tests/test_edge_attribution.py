"""Tests for Edge Attribution Engine."""
from __future__ import annotations
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_engine.edge_attribution.models import (
    EdgeAttributionRecord, build_attribution_record, _compute_stats, _bin_value,
)
from research_engine.edge_attribution.analyser import run_edge_analysis


def _make_record(pattern="TWEEZER_BOTTOM", regime="TRANSITIONAL", result_r=1.0, session="LONDON"):
    return EdgeAttributionRecord(
        entity_id="TEST_1", pattern=pattern, regime=regime,
        session=session, result_r=result_r, win=result_r > 0,
        direction="BUY", htf_alignment_bin="MEDIUM",
        trend_alignment_bin="MEDIUM", bias_alignment_bin="MEDIUM",
        score_bin="MEDIUM", confirmation_bin="STRONG",
    )


class TestSchema:
    def test_build_attribution_record(self):
        trace = {
            "entity_id": "EUR_1000", "symbol": "EURUSD",
            "timestamp_utc": "2026-07-17T09:30:00Z",
            "pattern_name": "THREE_INSIDE_DOWN", "regime": "TRANSITIONAL",
            "market_state": "TRANSITIONAL", "score_neutral": 0.55,
            "selected_strategy": None,
            "components": {"htf_alignment": 0.7, "trend_alignment": 0.3, "bias_alignment": 0.5, "confirmation_pre": 0.8},
        }
        rec = build_attribution_record(trace, outcome_r=1.5)
        assert rec.pattern == "THREE_INSIDE_DOWN"
        assert rec.result_r == 1.5
        assert rec.win is True
        assert rec.session == "LONDON"
        assert rec.htf_alignment_bin == "HIGH"
        assert rec.trend_alignment_bin == "LOW"

    def test_bin_value(self):
        assert _bin_value(0.8) == "HIGH"
        assert _bin_value(0.5) == "MEDIUM"
        assert _bin_value(0.1) == "LOW"

    def test_compute_stats_empty(self):
        s = _compute_stats([])
        assert s["n"] == 0
        assert s["confidence"] == "INSUFFICIENT"

    def test_compute_stats_correct(self):
        s = _compute_stats([1.0, 1.0, -1.0, -1.0, -1.0])
        assert s["n"] == 5
        assert s["wr"] == 0.4
        assert s["confidence"] == "LOW"


class TestAnalyser:
    def _make_dataset(self, n=100):
        records = []
        for i in range(n):
            pattern = "THREE_INSIDE_DOWN" if i % 5 == 0 else "TWEEZER_BOTTOM" if i % 3 == 0 else "THREE_BLACK_CROWS"
            regime = "TRENDING" if i % 10 == 0 else "TRANSITIONAL"
            # THREE_INSIDE_DOWN wins, others lose
            r = 2.0 if pattern == "THREE_INSIDE_DOWN" else -1.0 if pattern == "THREE_BLACK_CROWS" else 0.1
            records.append(_make_record(pattern=pattern, regime=regime, result_r=r))
        return records

    def test_produces_result(self):
        records = self._make_dataset()
        result = run_edge_analysis(records)
        assert result.total_records == 100
        assert len(result.single_features) > 0
        assert len(result.importance) > 0

    def test_identifies_positive_pattern(self):
        records = self._make_dataset()
        result = run_edge_analysis(records)
        pattern_conditions = result.single_features.get("pattern", [])
        tid = next((c for c in pattern_conditions if c.value == "THREE_INSIDE_DOWN"), None)
        assert tid is not None
        assert tid.stats["ev"] > 0

    def test_empty_data(self):
        result = run_edge_analysis([])
        assert result.confidence == "INSUFFICIENT"

    def test_deterministic(self):
        records = self._make_dataset(50)
        r1 = run_edge_analysis(records)
        r2 = run_edge_analysis(records)
        assert r1.to_dict() == r2.to_dict()

    def test_no_production_imports(self):
        import research_engine.edge_attribution.analyser as m
        source = Path(m.__file__).read_text(encoding="utf-8")
        assert "from core.pipeline" not in source
        assert "from execution" not in source
        assert "from risk." not in source

    def test_small_sample_flagged(self):
        # Few records but one has high EV
        records = [_make_record(pattern="RARE", result_r=5.0) for _ in range(6)]
        result = run_edge_analysis(records)
        # Should not produce HIGH confidence candidates
        high_conf = [c for c in result.edge_candidates if c.get("confidence") == "HIGH"]
        assert len(high_conf) == 0
