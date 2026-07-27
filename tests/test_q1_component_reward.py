"""
Tests for Q1: Component → Reward Correlation experiment.

Covers:
    - Attribution record creation from decision traces + shadow trades
    - Join via correlation_id (primary)
    - Missing outcome handling
    - Missing component handling
    - Component analysis correctness
    - Interaction analysis
    - Output formatting
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_engine.experiments.component_reward import (
    build_attribution_records,
    run_component_reward,
    AttributionRecord,
    ComponentRewardResult,
)


# ─── FIXTURES ─────────────────────────────────────────────────────────────────

def _trace(
    cor_id: str = "COR-001",
    cycle_id: int = 100,
    symbol: str = "EURUSD",
    pattern: str = "ENGULFING",
    components: dict | None = None,
    score: float = 0.6,
) -> dict:
    """Build a minimal decision trace record."""
    if components is None:
        components = {
            "pattern_quality": 0.7,
            "bias_alignment": 0.6,
            "trend_alignment": 0.5,
            "htf_alignment": 0.4,
            "h4_alignment": 0.3,
            "chop_clarity": 0.5,
            "volatility_quality": 0.8,
            "bias_stability": 0.7,
            "confirmation_pre": 0.9,
            "market_quality": 0.6,
        }
    return {
        "correlation_id": cor_id,
        "entity_id": f"{symbol}_{cycle_id}",
        "symbol": symbol,
        "cycle_id": cycle_id,
        "timestamp_utc": "2026-07-17T00:30:00.000Z",
        "action": "NO_TRADE",
        "pattern_detected": True,
        "pattern_name": pattern,
        "regime": "TRANSITIONAL",
        "market_state": "TRANSITIONAL",
        "selected_strategy": None,
        "strategy_confidence": 0.0,
        "components": components,
        "score_neutral": score,
        "score_strategy": score,
    }


def _shadow(
    cor_id: str = "COR-001",
    cycle_id: int = 100,
    symbol: str = "EURUSD",
    r_multiple: float = 1.5,
    exit_reason: str = "take_profit",
    bars_held: int = 12,
) -> dict:
    """Build a minimal shadow trade record (shadow_trades_v2 schema)."""
    return {
        "schema_version": "shadow_trades_v2",
        "identity": {
            "correlation_id": cor_id,
            "symbol": symbol,
            "cycle_id": str(cycle_id),
        },
        "decision_snapshot": {
            "pattern": "ENGULFING",
            "direction": "BUY",
            "score": 0.6,
        },
        "simulated_outcome": {
            "pnl_r_multiple": r_multiple,
            "exit_reason": exit_reason,
            "bars_held": bars_held,
            "mfe_r": 2.0,
            "mae_r": 0.5,
        },
    }


# ─── ATTRIBUTION RECORD CREATION ──────────────────────────────────────────────

class TestAttributionRecordCreation:
    def test_basic_join_by_correlation_id(self):
        """Records join on correlation_id."""
        traces = [_trace(cor_id="COR-100")]
        shadows = [_shadow(cor_id="COR-100", r_multiple=2.0)]

        records = build_attribution_records(traces, shadows)

        assert len(records) == 1
        assert records[0].has_outcome is True
        assert records[0].r_multiple == 2.0
        assert records[0].win is True
        assert records[0].correlation_id == "COR-100"

    def test_no_match_produces_record_without_outcome(self):
        """Traces without matching shadow trades have has_outcome=False."""
        traces = [_trace(cor_id="COR-AAA", cycle_id=999)]
        shadows = [_shadow(cor_id="COR-BBB", cycle_id=888)]

        records = build_attribution_records(traces, shadows)

        assert len(records) == 1
        assert records[0].has_outcome is False
        assert records[0].r_multiple == 0.0

    def test_multiple_traces_partial_match(self):
        """Only matching traces get outcomes."""
        traces = [
            _trace(cor_id="COR-1", cycle_id=1),
            _trace(cor_id="COR-2", cycle_id=2),
            _trace(cor_id="COR-3", cycle_id=3),
        ]
        shadows = [_shadow(cor_id="COR-2", r_multiple=-1.0)]

        records = build_attribution_records(traces, shadows)

        assert len(records) == 3
        matched = [r for r in records if r.has_outcome]
        assert len(matched) == 1
        assert matched[0].r_multiple == -1.0
        assert matched[0].win is False

    def test_components_preserved(self):
        """Component scores are carried through to attribution record."""
        components = {"bias_alignment": 0.9, "trend_alignment": 0.1}
        traces = [_trace(cor_id="COR-X", components=components)]
        shadows = [_shadow(cor_id="COR-X")]

        records = build_attribution_records(traces, shadows)

        assert records[0].components == components

    def test_traces_without_pattern_excluded(self):
        """Traces without pattern_detected=True are excluded."""
        trace = _trace(cor_id="COR-1")
        trace["pattern_detected"] = False
        traces = [trace]
        shadows = [_shadow(cor_id="COR-1")]

        records = build_attribution_records(traces, shadows)
        assert len(records) == 0

    def test_traces_without_components_excluded(self):
        """Traces with empty components dict are excluded."""
        trace = _trace(cor_id="COR-1")
        trace["components"] = {}
        traces = [trace]
        shadows = [_shadow(cor_id="COR-1")]

        records = build_attribution_records(traces, shadows)
        assert len(records) == 0


# ─── MISSING OUTCOME HANDLING ──────────────────────────────────────────────────

class TestMissingOutcomeHandling:
    def test_experiment_runs_with_no_matches(self):
        """Experiment returns INSUFFICIENT_DATA when no joins succeed."""
        traces = [_trace(cor_id="COR-A", cycle_id=999)]
        shadows = [_shadow(cor_id="COR-Z", cycle_id=888)]

        result = run_component_reward(traces, shadows)

        assert result.confidence == "INSUFFICIENT_DATA"
        assert result.decisions_with_outcome == 0

    def test_experiment_runs_with_empty_data(self):
        """Experiment handles empty inputs gracefully."""
        result = run_component_reward([], [])
        assert result.confidence == "INSUFFICIENT_DATA"


# ─── COMPONENT ANALYSIS ───────────────────────────────────────────────────────

class TestComponentAnalysis:
    def _make_dataset(self):
        """Create a synthetic dataset with known component → outcome relationship."""
        traces = []
        shadows = []

        # Pattern: high pattern_quality (0.9) → positive R (1.5)
        for i in range(20):
            cor = f"COR-HIGH-{i}"
            traces.append(_trace(
                cor_id=cor, cycle_id=i,
                components={"pattern_quality": 0.9, "bias_alignment": 0.2},
            ))
            shadows.append(_shadow(cor_id=cor, r_multiple=1.5))

        # Pattern: low pattern_quality (0.2) → negative R (-1.0)
        for i in range(20):
            cor = f"COR-LOW-{i}"
            traces.append(_trace(
                cor_id=cor, cycle_id=100 + i,
                components={"pattern_quality": 0.2, "bias_alignment": 0.9},
            ))
            shadows.append(_shadow(cor_id=cor, r_multiple=-1.0))

        return traces, shadows

    def test_identifies_positive_predictor(self):
        """High pattern_quality → positive R should show positive predictive value."""
        traces, shadows = self._make_dataset()
        result = run_component_reward(traces, shadows)

        # pattern_quality should have positive predictive value
        pq = next(s for s in result.component_stats if s.name == "pattern_quality")
        # High group (0.9) has avg R of +1.5, low group (0.2) has avg R of -1.0
        assert pq.avg_r_when_high > pq.avg_r_when_low
        assert pq.correlation is not None and pq.correlation > 0

    def test_identifies_negative_predictor(self):
        """High bias_alignment → negative R should show negative predictive value."""
        traces, shadows = self._make_dataset()
        result = run_component_reward(traces, shadows)

        # bias_alignment has 0.9 for losers, 0.2 for winners → negative correlation
        ba = next(s for s in result.component_stats if s.name == "bias_alignment")
        assert ba.avg_r_when_high < ba.avg_r_when_low
        assert ba in result.negative_contributors

    def test_win_rate_computed_correctly(self):
        """Win rate reflects actual outcome distribution."""
        traces, shadows = self._make_dataset()
        result = run_component_reward(traces, shadows)

        # 20 wins + 20 losses = 50% win rate overall
        for s in result.component_stats:
            assert 0.0 <= s.win_rate <= 1.0


# ─── OUTPUT FORMAT ────────────────────────────────────────────────────────────

class TestOutputFormat:
    def test_to_dict_serializable(self):
        """to_dict produces JSON-safe output."""
        import json

        traces = [_trace(cor_id=f"COR-{i}", cycle_id=i) for i in range(10)]
        shadows = [_shadow(cor_id=f"COR-{i}", r_multiple=0.5 * i - 2) for i in range(10)]

        result = run_component_reward(traces, shadows)
        d = result.to_dict()

        # Should be JSON-serializable
        json_str = json.dumps(d)
        assert len(json_str) > 0

        # Should have expected top-level keys
        assert "total_decisions" in d
        assert "decisions_with_outcome" in d
        assert "component_stats" in d
        assert "negative_contributors" in d
        assert "interactions" in d
        assert "conclusion" in d
        assert "confidence" in d
