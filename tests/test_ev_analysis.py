"""
Tests for Q19 / E1 — True System EV Analysis.

Validates:
    1. Dataset filtering (contract enforcement)
    2. Invalid/test records excluded
    3. Contaminated strategy handling
    4. Replay duplicate removal
    5. EV calculation correctness
    6. Dimensional breakdowns
    7. Confidence scoring
    8. Dashboard execution gate
    9. Report structure and fingerprint

No trading logic is tested or modified.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from research_engine.experiments.ev_analysis import (
    run_ev_analysis,
    filter_dataset,
    _compute_ev,
    _is_contaminated,
    _is_test_data,
    _confidence_level,
    _breakdown_by,
)


# ═══════════════════════════════════════════════════════════════════════════════
# TEST DATA BUILDERS
# ═══════════════════════════════════════════════════════════════════════════════


def _shadow(
    *,
    pattern="HAMMER",
    r=1.0,
    entity_id="EURUSD_1000",
    strategy="REVERSAL",
    horizon="SCALP",
    h4_regime="TRENDING",
    market_phase="IMPULSE",
    entry_time=1784000000.0,
    trade_id="t1",
) -> dict:
    """Build a valid shadow trade record."""
    return {
        "schema_version": "shadow_trades_v2",
        "identity": {"entity_id": entity_id, "trade_id": trade_id, "strategy_id": strategy},
        "decision_snapshot": {
            "pattern": pattern,
            "score": 0.7,
            "strategy": strategy,
            "trade_horizon": horizon,
            "regime": h4_regime,
            "h4_regime": h4_regime,
            "h1_bias": "BULLISH",
            "market_phase": market_phase,
            "timestamp_decision_utc": entry_time,
        },
        "simulation_environment": {
            "htf_snapshot": {"timeframe_bias": {"H4": {"regime": h4_regime}, "H1": {"bias": "BULLISH"}}}
        },
        "simulated_outcome": {"pnl_r_multiple": r, "exit_reason": "take_profit" if r > 0 else "stop_loss", "bars_held": 10},
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 1. DATASET FILTERING
# ═══════════════════════════════════════════════════════════════════════════════


class TestDatasetFiltering:
    """Contract enforces inclusion/exclusion rules."""

    def test_valid_records_included(self):
        records = [_shadow(trade_id=f"t{i}", entity_id=f"EUR_{i}") for i in range(10)]
        trades, exclusions = filter_dataset(records)
        assert len(trades) == 10
        assert sum(exclusions.values()) == 0

    def test_no_outcome_excluded(self):
        records = [{"identity": {"trade_id": "t1"}, "decision_snapshot": {"pattern": "X"}, "simulated_outcome": {}}]
        trades, exclusions = filter_dataset(records)
        assert len(trades) == 0
        assert exclusions.get("no_outcome", 0) == 1

    def test_no_pattern_excluded(self):
        record = _shadow(pattern="")
        trades, exclusions = filter_dataset([record])
        assert len(trades) == 0
        assert exclusions.get("no_pattern", 0) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 2. TEST DATA EXCLUSION
# ═══════════════════════════════════════════════════════════════════════════════


class TestTestDataExclusion:
    """Synthetic/test records are excluded."""

    def test_old_timestamp_excluded(self):
        record = _shadow(entry_time=1000.0, trade_id="old")  # Year 1970
        trades, exclusions = filter_dataset([record])
        assert len(trades) == 0
        assert exclusions.get("test_data", 0) == 1

    def test_test_trade_id_excluded(self):
        record = _shadow(trade_id="test_crash_1")
        trades, exclusions = filter_dataset([record])
        assert len(trades) == 0
        assert exclusions.get("test_data", 0) == 1

    def test_is_test_data_detection(self):
        assert _is_test_data({"entry_time": 500.0, "trade_id": "x"}) is True
        assert _is_test_data({"entry_time": 1784000000.0, "trade_id": "real"}) is False
        assert _is_test_data({"entry_time": 1784000000.0, "trade_id": "mock_trade"}) is True


# ═══════════════════════════════════════════════════════════════════════════════
# 3. CONTAMINATED STRATEGY HANDLING
# ═══════════════════════════════════════════════════════════════════════════════


class TestContaminatedStrategy:
    """Combined strategy_horizon format is detected."""

    def test_contamination_detected(self):
        assert _is_contaminated("NONE_SCALP") is True
        assert _is_contaminated("REVERSAL_INTRADAY") is True
        assert _is_contaminated("CONTINUATION_EXTENDED") is True

    def test_clean_strategy_not_contaminated(self):
        assert _is_contaminated("REVERSAL") is False
        assert _is_contaminated("CONTINUATION") is False
        assert _is_contaminated("FALSE_BREAK") is False
        assert _is_contaminated("") is False

    def test_contaminated_records_flagged_not_removed(self):
        """Contaminated records are kept (have outcome) but flagged."""
        record = _shadow(strategy="NONE_SCALP")
        trades, exclusions = filter_dataset([record])
        assert len(trades) == 1  # Kept — still has pattern + outcome
        assert trades[0].get("_strategy_contaminated") is True


# ═══════════════════════════════════════════════════════════════════════════════
# 4. REPLAY DUPLICATE REMOVAL
# ═══════════════════════════════════════════════════════════════════════════════


class TestReplayDeduplication:
    """Same entity_id + horizon only counted once."""

    def test_duplicate_entity_horizon_removed(self):
        records = [
            _shadow(entity_id="EUR_1000", horizon="SCALP", trade_id="t1", r=1.0),
            _shadow(entity_id="EUR_1000", horizon="SCALP", trade_id="t2", r=2.0),  # Duplicate
        ]
        trades, exclusions = filter_dataset(records)
        assert len(trades) == 1
        assert exclusions.get("replay_duplicate", 0) == 1

    def test_different_horizons_not_deduplicated(self):
        """Same entity_id but different horizons = different trades."""
        records = [
            _shadow(entity_id="EUR_1000", horizon="SCALP", trade_id="t1"),
            _shadow(entity_id="EUR_1000", horizon="INTRADAY", trade_id="t2"),
        ]
        trades, exclusions = filter_dataset(records)
        assert len(trades) == 2
        assert exclusions.get("replay_duplicate", 0) == 0

    def test_empty_entity_id_not_deduplicated(self):
        """Records without entity_id cannot be deduplicated."""
        records = [
            _shadow(entity_id="", trade_id="t1", r=1.0),
            _shadow(entity_id="", trade_id="t2", r=2.0),
        ]
        trades, exclusions = filter_dataset(records)
        assert len(trades) == 2  # Both kept — no dedup key


# ═══════════════════════════════════════════════════════════════════════════════
# 5. EV CALCULATION CORRECTNESS
# ═══════════════════════════════════════════════════════════════════════════════


class TestEVCalculation:
    """EV calculation produces correct results."""

    def test_positive_ev(self):
        trades = [{"r": 2.0}, {"r": 2.0}, {"r": -1.0}]
        result = _compute_ev(trades)
        assert result["ev"] == 1.0
        assert result["n"] == 3
        assert result["win_rate"] == pytest.approx(2/3, rel=0.01)

    def test_negative_ev(self):
        trades = [{"r": -1.0}, {"r": -1.0}, {"r": 0.5}]
        result = _compute_ev(trades)
        assert result["ev"] == pytest.approx(-0.5, rel=0.01)

    def test_zero_trades(self):
        result = _compute_ev([])
        assert result["ev"] == 0.0
        assert result["n"] == 0
        assert result["confidence"] == "INSUFFICIENT"

    def test_total_r_correct(self):
        trades = [{"r": 1.5}, {"r": -0.5}, {"r": 2.0}]
        result = _compute_ev(trades)
        assert result["total_r"] == 3.0


# ═══════════════════════════════════════════════════════════════════════════════
# 6. DIMENSIONAL BREAKDOWNS
# ═══════════════════════════════════════════════════════════════════════════════


class TestBreakdowns:
    """Breakdowns correctly segment by dimension."""

    def test_breakdown_by_pattern(self):
        trades = [
            {"pattern": "HAMMER", "r": 2.0},
            {"pattern": "HAMMER", "r": 1.0},
            {"pattern": "HAMMER", "r": -0.5},
            {"pattern": "HAMMER", "r": 1.5},
            {"pattern": "HAMMER", "r": -1.0},
            {"pattern": "TWEEZER_TOP", "r": -1.0},
            {"pattern": "TWEEZER_TOP", "r": -1.0},
            {"pattern": "TWEEZER_TOP", "r": -1.0},
            {"pattern": "TWEEZER_TOP", "r": -1.0},
            {"pattern": "TWEEZER_TOP", "r": -1.0},
        ]
        result = _breakdown_by(trades, "pattern", min_n=5)
        assert "HAMMER" in result
        assert "TWEEZER_TOP" in result
        assert result["HAMMER"]["ev"] > 0
        assert result["TWEEZER_TOP"]["ev"] < 0

    def test_breakdown_excludes_small_groups(self):
        trades = [
            {"pattern": "HAMMER", "r": 1.0},
            {"pattern": "RARE", "r": 5.0},  # Only 1 — excluded
        ]
        result = _breakdown_by(trades, "pattern", min_n=2)
        assert "RARE" not in result

    def test_breakdown_excludes_unknown(self):
        trades = [
            {"h4_regime": "UNKNOWN", "r": 1.0},
        ] * 10
        result = _breakdown_by(trades, "h4_regime", min_n=5)
        assert "UNKNOWN" not in result


# ═══════════════════════════════════════════════════════════════════════════════
# 7. CONFIDENCE SCORING
# ═══════════════════════════════════════════════════════════════════════════════


class TestConfidence:
    """Confidence levels correctly assigned by sample size."""

    def test_high_confidence(self):
        assert _confidence_level(200) == "HIGH"
        assert _confidence_level(500) == "HIGH"

    def test_medium_confidence(self):
        assert _confidence_level(50) == "MEDIUM"
        assert _confidence_level(199) == "MEDIUM"

    def test_low_confidence(self):
        assert _confidence_level(20) == "LOW"
        assert _confidence_level(49) == "LOW"

    def test_insufficient(self):
        assert _confidence_level(19) == "INSUFFICIENT"
        assert _confidence_level(0) == "INSUFFICIENT"


# ═══════════════════════════════════════════════════════════════════════════════
# 8. DASHBOARD EXECUTION GATE
# ═══════════════════════════════════════════════════════════════════════════════


class TestDashboardGate:
    """EV analysis respects dashboard gate."""

    def test_blocked_with_no_data(self):
        result = run_ev_analysis(records=[], check_dashboard=True)
        assert result["status"] == "BLOCKED"

    def test_runs_without_gate_check(self):
        records = [_shadow(trade_id=f"t{i}", entity_id=f"EUR_{i}") for i in range(25)]
        result = run_ev_analysis(records=records, check_dashboard=False)
        assert result["status"] == "COMPLETE"


# ═══════════════════════════════════════════════════════════════════════════════
# 9. REPORT STRUCTURE
# ═══════════════════════════════════════════════════════════════════════════════


class TestReportStructure:
    """Complete report has required sections."""

    def test_complete_report_structure(self):
        records = [_shadow(trade_id=f"t{i}", entity_id=f"EUR_{i}") for i in range(25)]
        report = run_ev_analysis(records=records, check_dashboard=False)

        assert report["status"] == "COMPLETE"
        assert "fingerprint" in report
        assert "overall" in report
        assert "breakdowns" in report
        assert "cross_dimensional" in report
        assert "history" in report
        assert "conclusion" in report

    def test_fingerprint_has_identity(self):
        records = [_shadow(trade_id=f"t{i}", entity_id=f"EUR_{i}") for i in range(25)]
        report = run_ev_analysis(records=records, check_dashboard=False)
        fp = report["fingerprint"]

        assert "dataset_id" in fp
        assert fp["total_raw_records"] == 25
        assert fp["eligible_records"] == 25
        assert fp["source"] == "SHADOW"

    def test_overall_has_ev_fields(self):
        records = [_shadow(trade_id=f"t{i}", entity_id=f"EUR_{i}", r=1.5) for i in range(25)]
        report = run_ev_analysis(records=records, check_dashboard=False)
        ov = report["overall"]

        assert ov["ev"] == 1.5
        assert ov["n"] == 25
        assert ov["win_rate"] == 1.0
        assert ov["confidence"] == "LOW"  # 25 < 50

    def test_history_includes_all_versions(self):
        records = [_shadow(trade_id=f"t{i}", entity_id=f"EUR_{i}") for i in range(25)]
        report = run_ev_analysis(records=records, check_dashboard=False)

        assert len(report["history"]) == 3
        versions = [h["version"] for h in report["history"]]
        assert "v1_original" in versions
        assert "v2_research_monitor" in versions
        assert "v3_lineage_validated" in versions
