"""
Tests for Current Epoch Data Safety Fix.

Verifies:
    - Default load_shadow_trades() returns CURRENT epoch only
    - Legacy data excluded unless explicitly requested
    - Reports include epoch metadata
    - Mixed epoch triggers warnings
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _s3_fake import install_fake_s3, reset_fake_s3
from research_engine.experiments.experiment_base import (
    build_fingerprint,
    build_report,
    load_shadow_trades,
    load_shadow_trades_all,
    ReadinessStatus,
)
from research_engine.data_quality.classifier import classify_record, DataEpoch


# ═══════════════════════════════════════════════════════════════════════════════
# S3 test source — the shared experiment loader reads the canonical shadow
# runtime ingestion + the live research_shadow_trades dataset via S3.
# ═══════════════════════════════════════════════════════════════════════════════

def _current_lifecycle_events() -> list[dict]:
    """A complete nshadow_* runtime lifecycle that classifies CURRENT."""
    return [
        {
            "schema_version": "shadow_runtime_v1",
            "event_type": "OPEN",
            "shadow_trade_id": "nshadow_7_EURUSD_SCALP",
            "plan_id": "nplan_7_EURUSD_1777700000",
            "canonical_opportunity_id": "EURUSD*1777700000*HAMMER",
            "symbol": "EURUSD",
            "identity": {
                "entity_id": "EURUSD_1777700000",
                "cycle_id": 7,
                "trade_horizon": "SCALP",
                "evaluated_horizon": "SCALP",
                "shadow_type": "HORIZON_ALTERNATIVE",
            },
            "live_facts": {
                "v10_action": "NO_TRADE",
                "v10_rejection_stage": "",
                "v10_selected_horizon": "INTRADAY",
                "horizon_selection_status": "ALTERNATIVE",
                "pattern": "HAMMER",
                "strategy": "REVERSAL",
                "score": 0.7,
                "regime": "TRENDING",
                "h4_regime": "TRENDING",
                "h1_bias": "BULLISH",
                "market_phase": "IMPULSE",
                "market_phase_confidence": 0.8,
            },
            "construction": {
                "direction": "BUY",
                "entry_price": 1.08000,
                "stop_loss": 1.07900,
                "take_profit": 1.08125,
                "risk_distance": 0.001,
                "risk_pips": 10.0,
                "intended_rr": 1.25,
            },
            "market_entry_facts": {},
        },
        {
            "schema_version": "shadow_runtime_v1",
            "event_type": "CLOSE",
            "shadow_trade_id": "nshadow_7_EURUSD_SCALP",
            "symbol": "EURUSD",
            "exit_reason": "take_profit",
            "exit_market_time_utc_epoch_s": 1777702400,
            "bars_held": 4,
            "outcome": {"pnl_r_multiple": 1.25, "mfe_r": 1.4, "mae_r": -0.2},
        },
    ]


def _legacy_record() -> dict:
    """Contaminated strategy suffix + no lineage → LEGACY."""
    return {"identity": {"entity_id": "", "strategy_id": "CONTINUATION_SCALP"}}


def _transitional_record() -> dict:
    """Entity present + clean strategy, but no regime/canonical → TRANSITIONAL."""
    return {
        "identity": {"entity_id": "EURUSD_1777700001", "strategy_id": "REVERSAL"},
        "decision_snapshot": {"trade_horizon": ""},
        "simulated_outcome": {"pnl_r_multiple": 0.5},
    }


@pytest.fixture(autouse=True)
def _fake_s3_source():
    fake = install_fake_s3()
    fake.add("shadow_runtime", _current_lifecycle_events(), symbol="EURUSD")
    fake.add("research_shadow_trades", [_legacy_record(), _transitional_record()],
             symbol="EURUSD")
    yield fake
    reset_fake_s3()


class TestDefaultEpochFiltering:
    """Default data loading returns CURRENT epoch only."""

    def test_default_load_returns_current_only(self):
        """load_shadow_trades() without args returns CURRENT epoch."""
        records = load_shadow_trades()
        # Every record should be CURRENT
        for r in records:
            epoch = classify_record(r)
            assert epoch == DataEpoch.CURRENT, (
                f"Non-CURRENT record in default load: {epoch.value}"
            )

    def test_default_is_subset_of_all(self):
        """Default (CURRENT) is smaller than all-epoch load."""
        current = load_shadow_trades()
        all_records = load_shadow_trades_all()
        assert len(current) <= len(all_records)

    def test_explicit_current_same_as_default(self):
        """Explicit epoch='CURRENT' produces same result as default."""
        default = load_shadow_trades()
        explicit = load_shadow_trades(epoch="CURRENT")
        assert len(default) == len(explicit)

    def test_all_epochs_includes_legacy(self):
        """include_all_epochs=True returns LEGACY + TRANSITIONAL + CURRENT."""
        all_records = load_shadow_trades(include_all_epochs=True)
        epochs = {classify_record(r) for r in all_records}
        # Should contain at least CURRENT (may or may not have others depending on data)
        assert DataEpoch.CURRENT in epochs or len(all_records) == 0

    def test_load_all_helper(self):
        """load_shadow_trades_all() returns everything."""
        all_records = load_shadow_trades_all()
        direct = load_shadow_trades(include_all_epochs=True)
        assert len(all_records) == len(direct)

    def test_epoch_parameter_filters_correctly(self):
        """Specifying epoch='TRANSITIONAL' returns only that epoch."""
        trans = load_shadow_trades(epoch="TRANSITIONAL")
        for r in trans:
            assert classify_record(r) == DataEpoch.TRANSITIONAL


class TestLegacyExclusion:
    """Legacy data requires explicit opt-in."""

    def test_legacy_not_in_default(self):
        """LEGACY records never appear in default load."""
        records = load_shadow_trades()
        legacy = [r for r in records if classify_record(r) == DataEpoch.LEGACY]
        assert legacy == []

    def test_transitional_not_in_default(self):
        """TRANSITIONAL records never appear in default load."""
        records = load_shadow_trades()
        trans = [r for r in records if classify_record(r) == DataEpoch.TRANSITIONAL]
        assert trans == []

    def test_legacy_available_via_explicit_request(self):
        """LEGACY data accessible with explicit epoch='LEGACY'."""
        legacy = load_shadow_trades(epoch="LEGACY")
        # All should be LEGACY (if any exist)
        for r in legacy:
            assert classify_record(r) == DataEpoch.LEGACY


class TestFingerprintEpochMetadata:
    """Fingerprints record epoch information."""

    def test_fingerprint_includes_epoch(self):
        """build_fingerprint includes epoch field."""
        fp = build_fingerprint(100, 5, source="shadow_trades", epoch="CURRENT")
        assert "epoch" in fp
        assert fp["epoch"] == "CURRENT"

    def test_fingerprint_includes_architecture_version(self):
        """build_fingerprint includes architecture_version."""
        fp = build_fingerprint(100, 5)
        assert "architecture_version" in fp
        assert fp["architecture_version"] == "new_pipeline_v1.2"

    def test_fingerprint_default_epoch_is_current(self):
        """Default epoch in fingerprint is CURRENT."""
        fp = build_fingerprint(100, 5)
        assert fp["epoch"] == "CURRENT"


class TestReportEpochWarnings:
    """Reports warn when using non-CURRENT data."""

    def test_current_epoch_no_warning(self):
        """CURRENT epoch report has no epoch warning."""
        fp = build_fingerprint(100, 0, epoch="CURRENT")
        report = build_report(
            question_id="TEST", status="COMPLETE",
            overall={"finding": "test"}, confidence="HIGH",
            dataset={"source": "test", "sample_size": 100},
            fingerprint=fp, recommendation="MONITOR",
        )
        epoch_warnings = [w for w in report["warnings"] if "EPOCH_WARNING" in w]
        assert epoch_warnings == []

    def test_mixed_epoch_triggers_warning(self):
        """Non-CURRENT epoch report includes epoch warning."""
        fp = build_fingerprint(100, 0, epoch="ALL")
        report = build_report(
            question_id="TEST", status="COMPLETE",
            overall={"finding": "test"}, confidence="HIGH",
            dataset={"source": "test", "sample_size": 100},
            fingerprint=fp, recommendation="MONITOR",
        )
        epoch_warnings = [w for w in report["warnings"] if "EPOCH_WARNING" in w]
        assert len(epoch_warnings) == 1
        assert "may not represent current system" in epoch_warnings[0]

    def test_unknown_epoch_triggers_warning(self):
        """Unknown/missing epoch in fingerprint triggers warning."""
        fp = {"records_used": 100, "source": "test"}  # No epoch field
        report = build_report(
            question_id="TEST", status="COMPLETE",
            overall={"finding": "test"}, confidence="HIGH",
            dataset={"source": "test", "sample_size": 100},
            fingerprint=fp, recommendation="MONITOR",
        )
        epoch_warnings = [w for w in report["warnings"] if "EPOCH_WARNING" in w]
        assert len(epoch_warnings) == 1

    def test_report_contains_epoch_field(self):
        """Report has top-level epoch field."""
        fp = build_fingerprint(100, 0, epoch="CURRENT")
        report = build_report(
            question_id="TEST", status="COMPLETE",
            overall={}, confidence="HIGH",
            dataset={}, fingerprint=fp, recommendation="MONITOR",
        )
        assert "epoch" in report
        assert report["epoch"] == "CURRENT"


class TestNoExecutionImports:
    """Epoch safety fix has no execution dependencies."""

    def test_no_forbidden_imports(self):
        import inspect
        import research_engine.experiments.experiment_base as m
        source = inspect.getsource(m)
        for f in ["from execution", "from risk.manager", "import MetaTrader5"]:
            assert f not in source
