"""
Tests for F2: Quiet Period Diagnostics.

Covers:
- Gate rejection tracking increments correctly
- Top 3 reasons sorted correctly
- Alert includes breakdown
- Telegram payload structure correct
- Reset behaviour (daily/session)
- Time-windowed counts
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.quiet_period_diagnostics import (
    RejectionReasonTracker,
    DiagnosticSummary,
    record_rejection,
    get_top_rejection_reasons,
    build_rejection_summary,
    emit_quiet_period_alert,
    build_telegram_payload,
)


# ─── FIXTURES ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the module singleton before each test."""
    import core.quiet_period_diagnostics as mod
    mod._tracker = None
    yield
    mod._tracker = None


# ─── TEST: REJECTION TRACKING ─────────────────────────────────────────────────

class TestRejectionTracking:
    def test_single_gate_increments(self):
        """Recording a gate rejection increments its count."""
        tracker = RejectionReasonTracker()
        tracker.record("A4_daily_trade_limit")
        tracker.record("A4_daily_trade_limit")
        tracker.record("A4_daily_trade_limit")

        counts = tracker.get_counts_by_gate()
        assert counts["A4_daily_trade_limit"] == 3

    def test_multiple_gates_tracked(self):
        """Multiple different gates tracked independently."""
        tracker = RejectionReasonTracker()
        tracker.record("I2_regime_guard")
        tracker.record("I2_regime_guard")
        tracker.record("H2_consistency_rules")
        tracker.record("A5_portfolio_exposure")

        counts = tracker.get_counts_by_gate()
        assert counts["I2_regime_guard"] == 2
        assert counts["H2_consistency_rules"] == 1
        assert counts["A5_portfolio_exposure"] == 1

    def test_session_total(self):
        """Session total counts all rejections."""
        tracker = RejectionReasonTracker()
        tracker.record("A4_daily_trade_limit")
        tracker.record("I2_regime_guard")
        tracker.record("H1_challenge_protect")

        assert tracker.get_session_total() == 3


# ─── TEST: TOP REASONS SORTING ────────────────────────────────────────────────

class TestTopReasons:
    def test_sorted_descending(self):
        """Top reasons sorted by count descending."""
        tracker = RejectionReasonTracker()
        tracker.record("I2_regime_guard")
        tracker.record("I2_regime_guard")
        tracker.record("I2_regime_guard")
        tracker.record("H2_consistency_rules")
        tracker.record("H2_consistency_rules")
        tracker.record("A5_portfolio_exposure")

        top = tracker.get_top_reasons(3)
        assert top[0] == ("I2_regime_guard", 3)
        assert top[1] == ("H2_consistency_rules", 2)
        assert top[2] == ("A5_portfolio_exposure", 1)

    def test_top_n_limits(self):
        """Only returns top N results."""
        tracker = RejectionReasonTracker()
        for i in range(10):
            tracker.record(f"gate_{i}")

        top = tracker.get_top_reasons(3)
        assert len(top) == 3

    def test_empty_returns_empty(self):
        """No rejections → empty list."""
        tracker = RejectionReasonTracker()
        top = tracker.get_top_reasons(3)
        assert top == []


# ─── TEST: DIAGNOSTIC SUMMARY ─────────────────────────────────────────────────

class TestDiagnosticSummary:
    def test_summary_includes_all_fields(self):
        """build_rejection_summary returns complete DiagnosticSummary."""
        record_rejection("I2_regime_guard")
        record_rejection("I2_regime_guard")
        record_rejection("H2_consistency_rules")

        summary = build_rejection_summary(no_trade_cycles=50)

        assert summary.no_trade_cycles == 50
        assert summary.session_total_rejections == 3
        assert len(summary.top_reasons) <= 3
        assert summary.top_reasons[0] == ("I2_regime_guard", 2)

    def test_alert_emits_summary(self, caplog):
        """emit_quiet_period_alert logs ranked breakdown."""
        import logging
        record_rejection("I2_regime_guard")
        record_rejection("I2_regime_guard")
        record_rejection("H4_weekend_protection")

        with caplog.at_level(logging.WARNING):
            summary = emit_quiet_period_alert(100)

        assert "QUIET_PERIOD_DIAGNOSTIC" in caplog.text
        assert "I2_regime_guard" in caplog.text
        assert summary.no_trade_cycles == 100


# ─── TEST: TELEGRAM PAYLOAD ───────────────────────────────────────────────────

class TestTelegramPayload:
    def test_payload_structure(self):
        """Telegram payload has correct structure."""
        record_rejection("A4_daily_trade_limit")
        record_rejection("A4_daily_trade_limit")
        record_rejection("I2_regime_guard")

        summary = build_rejection_summary(no_trade_cycles=75)
        payload = build_telegram_payload(summary)

        assert payload["alert"] == "QUIET_PERIOD"
        assert payload["cycles"] == 75
        assert isinstance(payload["top_rejections"], list)
        assert payload["top_rejections"][0]["gate"] == "A4_daily_trade_limit"
        assert payload["top_rejections"][0]["count"] == 2
        assert "session_total_rejections" in payload

    def test_empty_payload(self):
        """Payload with no rejections is still valid."""
        summary = build_rejection_summary(no_trade_cycles=10)
        payload = build_telegram_payload(summary)

        assert payload["cycles"] == 10
        assert payload["top_rejections"] == []


# ─── TEST: RESET BEHAVIOUR ────────────────────────────────────────────────────

class TestReset:
    def test_reset_clears_all(self):
        """reset() clears all counters and history."""
        tracker = RejectionReasonTracker()
        tracker.record("I2_regime_guard")
        tracker.record("A4_daily_trade_limit")

        tracker.reset()

        assert tracker.get_session_total() == 0
        assert tracker.get_top_reasons(3) == []
        assert tracker.get_last_n_minutes(60) == 0


# ─── TEST: TIME-WINDOWED COUNTS ───────────────────────────────────────────────

class TestTimeWindows:
    def test_last_30_min_count(self):
        """Only counts rejections in the last 30 minutes."""
        tracker = RejectionReasonTracker()

        # These are all "now" → within 30 minutes
        tracker.record("I2_regime_guard")
        tracker.record("A4_daily_trade_limit")

        count = tracker.get_last_n_minutes(30)
        assert count == 2

    def test_old_entries_excluded(self):
        """Entries older than window not counted (via timestamp check)."""
        tracker = RejectionReasonTracker()

        # Manually insert old entries
        from core.quiet_period_diagnostics import RejectionEntry
        old_time = time.time() - 7200  # 2 hours ago
        tracker._history.append(RejectionEntry("old_gate", old_time))
        tracker._history.append(RejectionEntry("old_gate", old_time))

        # Add fresh one
        tracker.record("fresh_gate")

        assert tracker.get_last_n_minutes(60) == 1  # Only the fresh one


# ─── TEST: PRODUCTION INTEGRATION ─────────────────────────────────────────────

class TestProductionIntegration:
    def test_record_rejection_in_pipeline(self):
        """record_rejection is called in pipeline (source verification)."""
        import inspect
        from core.runtime import live_scanner
        from risk import runtime_guard_chain

        # Verify record_rejection is called in live_scanner's consolidated handler
        scanner_source = inspect.getsource(live_scanner.run_live_scanner)
        assert "record_rejection(_gcr.rejection_code)" in scanner_source

        # Verify rejection codes exist in the guard chain
        chain_source = inspect.getsource(runtime_guard_chain.evaluate_runtime_guards)
        assert '"A4_daily_trade_limit"' in chain_source
        assert '"A5_portfolio_exposure"' in chain_source
        assert '"I2_regime_guard"' in chain_source
        assert '"H1_challenge_protect"' in chain_source
        assert '"H2_consistency_rules"' in chain_source
        assert '"H3_prop_firm_rules"' in chain_source
        assert '"H4_weekend_protection"' in chain_source

    def test_alert_integrated(self):
        """emit_quiet_period_alert is called in health_monitor (extracted from live_scanner)."""
        import inspect
        from core.runtime import health_monitor
        source = inspect.getsource(health_monitor.HealthMonitor.tick)
        assert "emit_quiet_period_alert" in source
