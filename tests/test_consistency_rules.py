"""
Tests for H2: Consistency Rules Compliance.

Covers:
- Daily cap triggers block
- Daily cap resets next day
- Concentration detection
- Min trading days tracked
- Persistence survives restart
- No duplicate day counting
- Disabled mode allows all
- Config validation
- Production integration
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.consistency_rules import (
    ConsistencyTracker,
    ConsistencyStatus,
    ConsistencyGuardResult,
    REJECT_DAILY_PROFIT_CAP,
    REJECT_CONCENTRATION,
    check_consistency_gate,
    validate_consistency_config,
)


# ─── FIXTURES ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def default_config(tmp_path):
    """Set known config defaults and redirect state file."""
    state_file = tmp_path / "consistency.json"
    with patch("core.consistency_rules._is_enabled", return_value=True), \
         patch("core.consistency_rules._get_max_daily_profit", return_value=2.0), \
         patch("core.consistency_rules._get_min_trading_days", return_value=5), \
         patch("core.consistency_rules._get_max_concentration", return_value=40.0), \
         patch("core.consistency_rules._get_lock_after_cap", return_value=True), \
         patch("core.consistency_rules._get_state_path", return_value=state_file), \
         patch("core.consistency_rules._current_day_key", return_value="2026-06-06"):
        # Reset singleton
        import core.consistency_rules as mod
        mod._tracker = None
        yield state_file
        mod._tracker = None


# ─── TEST: DAILY CAP ──────────────────────────────────────────────────────────

class TestDailyCap:
    def test_blocks_at_daily_cap(self, default_config):
        """Daily profit >= cap triggers block."""
        tracker = ConsistencyTracker()
        tracker.record_trade_result(1.5)
        tracker.record_trade_result(0.6)  # Total = 2.1% > 2.0% cap

        result = tracker.check_gate()
        assert result.allowed is False
        assert result.reason == REJECT_DAILY_PROFIT_CAP
        assert result.today_profit_percent >= 2.0

    def test_allows_below_cap(self, default_config):
        """Below daily cap → allowed."""
        tracker = ConsistencyTracker()
        tracker.record_trade_result(0.8)
        tracker.record_trade_result(0.5)  # Total = 1.3% < 2.0%

        result = tracker.check_gate()
        assert result.allowed is True

    def test_exactly_at_cap_blocks(self, default_config):
        """Exactly at cap → blocked (>= check)."""
        tracker = ConsistencyTracker()
        tracker.record_trade_result(2.0)  # Exactly 2.0%

        result = tracker.check_gate()
        assert result.allowed is False

    def test_resets_next_day(self, default_config):
        """New day starts fresh — previous day's profit doesn't block."""
        tracker = ConsistencyTracker()
        tracker.record_trade_result(2.5)  # Day 1 over cap

        # Simulate day change
        with patch("core.consistency_rules._current_day_key", return_value="2026-06-07"):
            tracker.reset_day()
            result = tracker.check_gate()

        assert result.allowed is True


# ─── TEST: CONCENTRATION RULE ──────────────────────────────────────────────────

class TestConcentration:
    def test_detects_skewed_distribution(self, default_config):
        """One big day dominating total → concentration violation."""
        tracker = ConsistencyTracker()

        # Day 1: 3% profit (will be 60% of total 5%)
        tracker._daily_history["2026-06-01"] = {"profit_pct": 3.0, "trade_count": 2}
        # Day 2: 1%
        tracker._daily_history["2026-06-02"] = {"profit_pct": 1.0, "trade_count": 1}
        # Day 3: 1%
        tracker._daily_history["2026-06-03"] = {"profit_pct": 1.0, "trade_count": 1}

        status = tracker.evaluate()

        # 3.0 / 5.0 * 100 = 60% > 40% limit
        assert status.violates_concentration_rule is True

    def test_balanced_no_violation(self, default_config):
        """Balanced distribution → no violation."""
        tracker = ConsistencyTracker()

        tracker._daily_history["2026-06-01"] = {"profit_pct": 1.0, "trade_count": 2}
        tracker._daily_history["2026-06-02"] = {"profit_pct": 1.2, "trade_count": 2}
        tracker._daily_history["2026-06-03"] = {"profit_pct": 0.8, "trade_count": 1}
        tracker._daily_history["2026-06-04"] = {"profit_pct": 1.0, "trade_count": 2}

        status = tracker.evaluate()

        # Max day 1.2 / total 4.0 = 30% < 40%
        assert status.violates_concentration_rule is False

    def test_negative_total_no_violation(self, default_config):
        """Total profit <= 0 → concentration rule not applicable."""
        tracker = ConsistencyTracker()
        tracker._daily_history["2026-06-01"] = {"profit_pct": -1.0, "trade_count": 1}

        status = tracker.evaluate()
        assert status.violates_concentration_rule is False


# ─── TEST: MINIMUM TRADING DAYS ────────────────────────────────────────────────

class TestMinTradingDays:
    def test_warns_below_minimum(self, default_config):
        """Fewer than MIN_TRADING_DAYS → violates_min_days."""
        tracker = ConsistencyTracker()
        tracker._daily_history["2026-06-01"] = {"profit_pct": 0.5, "trade_count": 1}
        tracker._daily_history["2026-06-02"] = {"profit_pct": 0.3, "trade_count": 1}

        status = tracker.evaluate()

        assert status.active_trading_days == 2
        assert status.violates_min_days is True  # 2 < 5

    def test_meets_minimum(self, default_config):
        """Meeting minimum → no violation."""
        tracker = ConsistencyTracker()
        for i in range(5):
            tracker._daily_history[f"2026-06-0{i+1}"] = {"profit_pct": 0.3, "trade_count": 1}

        status = tracker.evaluate()

        assert status.active_trading_days == 5
        assert status.violates_min_days is False

    def test_zero_trade_days_not_counted(self, default_config):
        """Days with 0 trades don't count as active."""
        tracker = ConsistencyTracker()
        tracker._daily_history["2026-06-01"] = {"profit_pct": 0.5, "trade_count": 2}
        tracker._daily_history["2026-06-02"] = {"profit_pct": 0.0, "trade_count": 0}  # Not active
        tracker._daily_history["2026-06-03"] = {"profit_pct": 0.3, "trade_count": 1}

        assert tracker.active_trading_days == 2


# ─── TEST: PERSISTENCE ─────────────────────────────────────────────────────────

class TestPersistence:
    def test_survives_restart(self, default_config):
        """State persists and loads on new instance."""
        tracker1 = ConsistencyTracker()
        tracker1.record_trade_result(1.2)
        tracker1.record_trade_result(0.3)

        # New instance (simulates restart)
        tracker2 = ConsistencyTracker()

        assert tracker2.today_profit_percent == pytest.approx(1.5, abs=0.01)
        assert tracker2.today_trade_count == 2

    def test_no_duplicate_days(self, default_config):
        """Multiple records on same day aggregate, not duplicate."""
        tracker = ConsistencyTracker()
        tracker.record_trade_result(0.5)
        tracker.record_trade_result(0.3)
        tracker.record_trade_result(0.2)

        assert tracker.today_trade_count == 3
        assert tracker.today_profit_percent == pytest.approx(1.0, abs=0.01)
        # Only 1 day key exists for today
        assert len([k for k in tracker._daily_history if k == "2026-06-06"]) == 1

    def test_state_file_format(self, default_config):
        """State file is valid JSON with expected structure."""
        tracker = ConsistencyTracker()
        tracker.record_trade_result(0.7)

        data = json.loads(default_config.read_text())
        assert "daily_history" in data
        assert "2026-06-06" in data["daily_history"]
        assert data["daily_history"]["2026-06-06"]["profit_pct"] == pytest.approx(0.7)
        assert data["daily_history"]["2026-06-06"]["trade_count"] == 1


# ─── TEST: DISABLED MODE ──────────────────────────────────────────────────────

class TestDisabledMode:
    def test_disabled_always_allows(self, default_config):
        """When disabled, all checks pass."""
        with patch("core.consistency_rules._is_enabled", return_value=False):
            tracker = ConsistencyTracker()
            tracker.record_trade_result(99.0)
            result = tracker.check_gate()

        assert result.allowed is True
        assert result.reason == "CONSISTENCY_RULES_DISABLED"

    def test_lock_disabled_allows(self, default_config):
        """When LOCK_AFTER_DAILY_PROFIT_CAP=False, no hard block."""
        with patch("core.consistency_rules._get_lock_after_cap", return_value=False):
            tracker = ConsistencyTracker()
            tracker.record_trade_result(5.0)  # Way over cap
            result = tracker.check_gate()

        assert result.allowed is True


# ─── TEST: CONFIG VALIDATION ──────────────────────────────────────────────────

class TestConfigValidation:
    def test_valid_config_no_errors(self, default_config):
        """Valid config has no errors."""
        errors = validate_consistency_config()
        assert errors == []

    def test_zero_cap_errors(self, default_config):
        """Zero daily profit cap generates error."""
        with patch("core.consistency_rules._get_max_daily_profit", return_value=0):
            errors = validate_consistency_config()
        assert any("MAX_DAILY_PROFIT" in e for e in errors)

    def test_zero_min_days_errors(self, default_config):
        """Zero min trading days generates error."""
        with patch("core.consistency_rules._get_min_trading_days", return_value=0):
            errors = validate_consistency_config()
        assert any("MIN_TRADING_DAYS" in e for e in errors)


# ─── TEST: PRODUCTION INTEGRATION ─────────────────────────────────────────────

class TestProductionIntegration:
    def test_gate_before_execution(self):
        """Consistency gate appears in runtime guard chain."""
        import inspect
        from risk import runtime_guard_chain

        source = inspect.getsource(runtime_guard_chain.evaluate_runtime_guards)

        consistency_pos = source.find("check_consistency_gate")

        assert consistency_pos > 0, "Consistency gate not found in runtime guard chain"

    def test_gate_after_challenge(self):
        """Consistency gate appears after challenge gate."""
        import inspect
        from risk import runtime_guard_chain

        source = inspect.getsource(runtime_guard_chain.evaluate_runtime_guards)

        challenge_pos = source.find("check_challenge_gate")
        consistency_pos = source.find("check_consistency_gate")

        assert challenge_pos > 0
        assert consistency_pos > 0
        assert challenge_pos < consistency_pos
