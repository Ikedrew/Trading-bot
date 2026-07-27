"""Tests for Data Quality Classifier."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from research_engine.data_quality.classifier import (
    DataEpoch,
    DatasetClassification,
    classify_dataset,
    classify_record,
    filter_current,
    filter_usable,
)


def _record(entity_id="", strategy="", h4_regime="", correlation_id="", r_multiple=0.5, horizon=""):
    return {
        "identity": {"entity_id": entity_id, "strategy_id": strategy, "correlation_id": correlation_id},
        "decision_snapshot": {"h4_regime": h4_regime, "trade_horizon": horizon, "pattern": "X"},
        "simulated_outcome": {"pnl_r_multiple": r_multiple},
    }


class TestClassifyRecord:
    def test_current_full_lineage(self):
        r = _record(entity_id="EURUSD_123", strategy="CONTINUATION", h4_regime="TRENDING")
        assert classify_record(r) == DataEpoch.CURRENT

    def test_current_empty_strategy_with_entity_and_regime(self):
        r = _record(entity_id="EURUSD_123", strategy="", h4_regime="RANGING")
        assert classify_record(r) == DataEpoch.CURRENT

    def test_transitional_entity_no_regime(self):
        r = _record(entity_id="EURUSD_123", strategy="REVERSAL", h4_regime="")
        assert classify_record(r) == DataEpoch.TRANSITIONAL

    def test_transitional_correlation_clean_strategy(self):
        r = _record(entity_id="", strategy="CONTINUATION", correlation_id="COR-123")
        assert classify_record(r) == DataEpoch.TRANSITIONAL

    def test_legacy_contaminated_strategy(self):
        r = _record(entity_id="X", strategy="CONTINUATION_SCALP", h4_regime="TRENDING")
        assert classify_record(r) == DataEpoch.LEGACY

    def test_legacy_none_scalp(self):
        r = _record(strategy="NONE_SCALP")
        assert classify_record(r) == DataEpoch.LEGACY

    def test_legacy_reversal_intraday(self):
        r = _record(strategy="REVERSAL_INTRADAY")
        assert classify_record(r) == DataEpoch.LEGACY

    def test_legacy_horizon_prefix_no_entity(self):
        r = _record(correlation_id="HORIZON-123-EURUSD-SCALP", strategy="")
        assert classify_record(r) == DataEpoch.LEGACY

    def test_legacy_no_fields(self):
        """Record with no identity fields and no outcome is LEGACY."""
        r = {"identity": {}, "decision_snapshot": {}, "simulated_outcome": {}}
        assert classify_record(r) == DataEpoch.LEGACY

    def test_false_break_is_valid(self):
        r = _record(entity_id="X", strategy="FALSE_BREAK", h4_regime="TRENDING")
        assert classify_record(r) == DataEpoch.CURRENT


class TestClassifyDataset:
    def test_empty_dataset(self):
        result = classify_dataset([])
        assert result.total_records == 0
        assert result.current_count == 0

    def test_mixed_dataset(self):
        records = [
            _record(entity_id="X", strategy="CONTINUATION", h4_regime="TRENDING"),
            _record(entity_id="X", strategy="REVERSAL", h4_regime=""),
            _record(strategy="NONE_SCALP"),
        ]
        result = classify_dataset(records)
        assert result.total_records == 3
        assert result.current_count == 1
        assert result.transitional_count == 1
        assert result.legacy_count == 1
        assert result.usable_count == 2

    def test_summary_format(self):
        records = [_record(entity_id="X", strategy="", h4_regime="T")] * 5
        result = classify_dataset(records)
        assert "CURRENT: 5" in result.summary
        assert "100%" in result.summary

    def test_to_dict(self):
        records = [_record(strategy="NONE_SCALP")] * 3
        result = classify_dataset(records)
        d = result.to_dict()
        assert d["legacy"]["count"] == 3
        assert d["current"]["count"] == 0


class TestFilters:
    def test_filter_current(self):
        records = [
            _record(entity_id="X", strategy="CONTINUATION", h4_regime="T"),
            _record(strategy="NONE_SCALP"),
            _record(entity_id="Y", strategy="", h4_regime="R"),
        ]
        current = filter_current(records)
        assert len(current) == 2  # Both with entity+regime

    def test_filter_usable(self):
        records = [
            _record(entity_id="X", strategy="CONTINUATION", h4_regime="T"),
            _record(entity_id="X", strategy="REVERSAL", h4_regime=""),
            _record(strategy="NONE_SCALP"),
        ]
        usable = filter_usable(records)
        assert len(usable) == 2  # CURRENT + TRANSITIONAL

    def test_filters_do_not_modify_source(self):
        records = [_record(strategy="NONE_SCALP"), _record(entity_id="X", strategy="", h4_regime="T")]
        original_len = len(records)
        _ = filter_current(records)
        assert len(records) == original_len  # Source unchanged
