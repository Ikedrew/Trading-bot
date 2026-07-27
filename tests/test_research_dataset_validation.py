"""
Tests for Research Dataset Validation Layer.

Validates that the pre-experiment validator correctly identifies:
    1. Fully populated datasets → validation passes
    2. Replay datasets with missing HTF fields → warnings generated
    3. Missing market_phase → phase research marked invalid
    4. Missing required experiment fields → validation failure
    5. Live datasets with complete MarketContext → HTF research allowed

No trading behaviour is tested or modified.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from research_engine.validation import (
    validate_dataset,
    DataSource,
    ValidationThresholds,
    ResearchValidationResult,
)


# ═══════════════════════════════════════════════════════════════════════════════
# TEST DATA BUILDERS
# ═══════════════════════════════════════════════════════════════════════════════


def _make_full_shadow_trade(
    *,
    pattern: str = "HAMMER",
    h4_regime: str = "TRENDING",
    h1_bias: str = "BULLISH",
    market_phase: str = "IMPULSE",
    r_multiple: float = 1.5,
) -> dict:
    """Create a shadow trade record with ALL fields populated."""
    return {
        "schema_version": "shadow_trades_v2",
        "identity": {"trade_id": "test_1", "symbol": "EURUSD", "cycle_id": "100", "entity_id": "EURUSD_1000"},
        "decision_snapshot": {
            "pattern": pattern,
            "score": 0.7,
            "market_phase": market_phase,
            "market_phase_confidence": 0.8,
            "regime": "TRENDING",
            "h4_regime": h4_regime,
            "h1_bias": h1_bias,
            "strategy": "REVERSAL",
            "trade_horizon": "SCALP",
        },
        "simulation_environment": {
            "htf_snapshot": {
                "timeframe_bias": {
                    "H4": {"regime": h4_regime, "bias": "BULLISH", "strength": 0.8},
                    "H1": {"bias": h1_bias, "regime": "TRENDING", "strength": 0.6},
                }
            }
        },
        "simulated_outcome": {
            "pnl_r_multiple": r_multiple,
            "exit_reason": "take_profit" if r_multiple > 0 else "stop_loss",
            "bars_held": 10,
        },
    }


def _make_replay_shadow_trade(*, pattern: str = "TWEEZER_TOP", r_multiple: float = 0.5) -> dict:
    """Shadow trade from replay — H4 regime UNKNOWN, no phase."""
    return {
        "schema_version": "shadow_trades_v2",
        "identity": {"trade_id": "replay_1", "symbol": "EURUSD", "cycle_id": "200"},
        "decision_snapshot": {
            "pattern": pattern,
            "score": 0.6,
        },
        "simulation_environment": {
            "htf_snapshot": {
                "timeframe_bias": {
                    "H4": {"regime": "UNKNOWN", "bias": "NEUTRAL", "strength": 0.0},
                    "H1": {"bias": "NEUTRAL", "regime": "RANGING", "strength": 0.0},
                }
            }
        },
        "simulated_outcome": {
            "pnl_r_multiple": r_multiple,
            "exit_reason": "max_bars_timeout",
            "bars_held": 60,
        },
    }


def _make_decision_trace(
    *,
    regime: str = "TRENDING",
    regime_source: str = "H4_MARKET_CONTEXT",
    market_phase: str | None = "IMPULSE",
    pattern: str = "HAMMER",
) -> dict:
    """Decision trace record."""
    d = {
        "entity_id": "EURUSD_1000",
        "symbol": "EURUSD",
        "cycle_id": 1,
        "action": "NO_TRADE",
        "regime": regime,
        "regime_source": regime_source,
        "pattern_name": pattern,
        "score_neutral": 0.5,
    }
    if market_phase is not None:
        d["market_phase"] = market_phase
    return d


# ═══════════════════════════════════════════════════════════════════════════════
# 1. FULLY POPULATED DATASET → VALIDATION PASSES
# ═══════════════════════════════════════════════════════════════════════════════


class TestFullyPopulatedDataset:
    """Complete dataset with all fields passes all suitability checks."""

    def test_all_suitability_flags_true(self):
        records = [_make_full_shadow_trade() for _ in range(25)]
        result = validate_dataset(records, dataset_name="shadow_full_test")

        assert result.validation_passed is True
        assert result.suitable_for_htf_research is True
        assert result.suitable_for_phase_research is True
        assert result.suitable_for_pattern_research is True
        assert result.total_records == 25

    def test_high_coverage_metrics(self):
        records = [_make_full_shadow_trade() for _ in range(30)]
        result = validate_dataset(records, dataset_name="shadow_full")

        assert result.h4_regime_coverage.coverage_pct == 1.0
        assert result.h1_bias_coverage.coverage_pct == 1.0
        assert result.market_phase_coverage.coverage_pct == 1.0
        assert result.pattern_coverage.coverage_pct == 1.0
        assert result.outcome_coverage.coverage_pct == 1.0

    def test_no_warnings_generated(self):
        records = [_make_full_shadow_trade() for _ in range(25)]
        result = validate_dataset(records, dataset_name="shadow_full")

        # Should have no coverage warnings (may have source warning for SHADOW)
        coverage_warnings = [w for w in result.warnings if "coverage" in w.lower() or "below" in w.lower()]
        assert len(coverage_warnings) == 0

    def test_source_detected_as_shadow(self):
        records = [_make_full_shadow_trade() for _ in range(25)]
        result = validate_dataset(records, dataset_name="shadow_trades_2026")

        assert result.source == DataSource.SHADOW


# ═══════════════════════════════════════════════════════════════════════════════
# 2. REPLAY DATASET WITH MISSING HTF → WARNINGS GENERATED
# ═══════════════════════════════════════════════════════════════════════════════


class TestReplayDatasetMissingHTF:
    """Replay data with UNKNOWN H4 regime generates appropriate warnings."""

    def test_htf_research_unsuitable(self):
        records = [_make_replay_shadow_trade() for _ in range(30)]
        result = validate_dataset(records, dataset_name="shadow_replay")

        assert result.suitable_for_htf_research is False

    def test_h4_regime_coverage_low(self):
        records = [_make_replay_shadow_trade() for _ in range(30)]
        result = validate_dataset(records, dataset_name="shadow_replay")

        # UNKNOWN counts as "not useful" — coverage should be 0%
        assert result.h4_regime_coverage.coverage_pct == 0.0

    def test_warning_includes_h4_regime(self):
        records = [_make_replay_shadow_trade() for _ in range(30)]
        result = validate_dataset(records, dataset_name="shadow_replay")

        h4_warnings = [w for w in result.warnings if "H4 regime" in w]
        assert len(h4_warnings) >= 1

    def test_pattern_research_still_suitable(self):
        """Pattern + outcome are present even in replay — pattern research ok."""
        records = [_make_replay_shadow_trade() for _ in range(30)]
        result = validate_dataset(records, dataset_name="shadow_replay")

        assert result.suitable_for_pattern_research is True


# ═══════════════════════════════════════════════════════════════════════════════
# 3. MISSING MARKET PHASE → PHASE RESEARCH INVALID
# ═══════════════════════════════════════════════════════════════════════════════


class TestMissingMarketPhase:
    """Dataset without market_phase blocks phase-based research."""

    def test_phase_research_unsuitable(self):
        records = [_make_replay_shadow_trade() for _ in range(30)]  # No market_phase field
        result = validate_dataset(records, dataset_name="shadow_no_phase")

        assert result.suitable_for_phase_research is False

    def test_phase_coverage_zero(self):
        records = [_make_replay_shadow_trade() for _ in range(30)]
        result = validate_dataset(records, dataset_name="shadow_no_phase")

        assert result.market_phase_coverage.coverage_pct == 0.0

    def test_phase_warning_generated(self):
        records = [_make_replay_shadow_trade() for _ in range(30)]
        result = validate_dataset(records, dataset_name="shadow_no_phase")

        phase_warnings = [w for w in result.warnings if "phase" in w.lower()]
        assert len(phase_warnings) >= 1

    def test_mixed_dataset_partial_phase(self):
        """50% phase coverage should still fail the 80% threshold."""
        with_phase = [_make_full_shadow_trade() for _ in range(15)]
        without_phase = [_make_replay_shadow_trade() for _ in range(15)]
        records = with_phase + without_phase
        result = validate_dataset(records, dataset_name="shadow_mixed")

        assert result.market_phase_coverage.coverage_pct == 0.5
        assert result.suitable_for_phase_research is False


# ═══════════════════════════════════════════════════════════════════════════════
# 4. MISSING REQUIRED FIELDS → VALIDATION FAILURE
# ═══════════════════════════════════════════════════════════════════════════════


class TestMissingRequiredFields:
    """Experiments can specify required fields — missing fields fail validation."""

    def test_missing_required_field_detected(self):
        records = [_make_replay_shadow_trade() for _ in range(25)]
        result = validate_dataset(
            records,
            dataset_name="shadow_test",
            required_fields=["nonexistent_field"],
        )

        assert "nonexistent_field" in result.missing_fields
        assert result.validation_passed is False

    def test_present_required_field_passes(self):
        records = [_make_full_shadow_trade() for _ in range(25)]
        result = validate_dataset(
            records,
            dataset_name="shadow_test",
            required_fields=["pattern"],  # pattern is in decision_snapshot
        )

        assert "pattern" not in result.missing_fields
        assert result.validation_passed is True

    def test_multiple_missing_fields(self):
        records = [_make_replay_shadow_trade() for _ in range(25)]
        result = validate_dataset(
            records,
            dataset_name="shadow_test",
            required_fields=["market_phase", "nonexistent"],
        )

        assert "market_phase" in result.missing_fields or "nonexistent" in result.missing_fields

    def test_warning_message_includes_field_names(self):
        records = [_make_replay_shadow_trade() for _ in range(25)]
        result = validate_dataset(
            records,
            dataset_name="shadow_test",
            required_fields=["magic_field"],
        )

        field_warnings = [w for w in result.warnings if "magic_field" in w]
        assert len(field_warnings) >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# 5. LIVE DATASET WITH COMPLETE CONTEXT → HTF RESEARCH ALLOWED
# ═══════════════════════════════════════════════════════════════════════════════


class TestLiveDatasetComplete:
    """Live data with full MarketContext enables all research types."""

    def _make_live_trade_truth(self) -> dict:
        return {
            "schema_version": "trade_truth_v3",
            "identity": {"trade_id": "live_1", "symbol": "EURUSD"},
            "execution": {
                "entry_fill_price": 1.10000,
                "exit_fill_price": 1.10200,
                "slippage_entry": 0.00001,
            },
            "outcome": {"r_multiple_realised": 2.0, "pnl_realised": 20.0},
            "regime": "TRENDING",
            "regime_source": "H4_MARKET_CONTEXT",
            "h1_bias": "BULLISH",
            "market_phase": "IMPULSE",
            "pattern": "HAMMER",
        }

    def test_live_source_detected(self):
        records = [self._make_live_trade_truth() for _ in range(25)]
        result = validate_dataset(records, dataset_name="trade_truth_live")

        assert result.source == DataSource.TRADE_TRUTH

    def test_execution_research_suitable(self):
        records = [self._make_live_trade_truth() for _ in range(25)]
        result = validate_dataset(records, dataset_name="trade_truth_live")

        assert result.suitable_for_execution_research is True

    def test_htf_research_suitable(self):
        records = [self._make_live_trade_truth() for _ in range(25)]
        result = validate_dataset(records, dataset_name="trade_truth_live")

        assert result.suitable_for_htf_research is True


# ═══════════════════════════════════════════════════════════════════════════════
# EDGE CASES
# ═══════════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Boundary conditions and empty datasets."""

    def test_empty_dataset(self):
        result = validate_dataset([], dataset_name="empty")

        assert result.validation_passed is False
        assert result.total_records == 0
        assert result.source == DataSource.UNKNOWN
        assert "empty" in result.warnings[0].lower()

    def test_below_minimum_sample_size(self):
        records = [_make_full_shadow_trade() for _ in range(5)]
        result = validate_dataset(records, dataset_name="small_shadow")

        sample_warnings = [w for w in result.warnings if "Sample size" in w]
        assert len(sample_warnings) >= 1

    def test_to_dict_serializable(self):
        records = [_make_full_shadow_trade() for _ in range(25)]
        result = validate_dataset(records, dataset_name="test_serial")
        d = result.to_dict()

        assert isinstance(d, dict)
        assert "coverage" in d
        assert "suitability" in d
        assert "warnings" in d
        assert isinstance(d["coverage"]["h4_regime"], float)

    def test_custom_thresholds(self):
        """Custom thresholds override defaults."""
        records = [_make_full_shadow_trade() for _ in range(10)]
        # Lower min sample to 5 so 10 records pass
        thresholds = ValidationThresholds(min_sample_size=5)
        result = validate_dataset(records, dataset_name="custom", thresholds=thresholds)

        assert result.validation_passed is True

    def test_test_source_detected(self):
        """Records with synthetic timestamps (< 2020) detected as TEST."""
        records = [{"timestamp_utc": 1000.0, "bar_time": 500} for _ in range(25)]
        result = validate_dataset(records, dataset_name="unit_test_data")

        assert result.source == DataSource.TEST
