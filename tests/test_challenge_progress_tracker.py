"""
Tests for H1: Challenge Progress Tracker.

Covers:
- Progress at 0%, 50%, 80%, 100%+
- Conservative mode activates at threshold
- Conservative mode reduces effective risk
- Protect mode activates at target
- Protect mode blocks new trades
- Date logic (elapsed/remaining days)
- Expired challenge
- Persistence JSON output valid
- Disabled mode always allows
- Config validation
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.challenge_progress_tracker import (
    ChallengeProgress,
    ChallengeGuardResult,
    REJECT_CHALLENGE_TARGET_ACHIEVED,
    evaluate_challenge_progress,
    check_challenge_gate,
    get_effective_risk_percent,
    persist_progress,
    validate_challenge_config,
    compute_challenge_profit_percent,
    _compute_time,
)


# ─── FIXTURES ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def default_config():
    """Set known challenge config defaults."""
    with patch("core.challenge_progress_tracker._is_enabled", return_value=True), \
         patch("core.challenge_progress_tracker._get_profit_target", return_value=8.0), \
         patch("core.challenge_progress_tracker._get_start_date", return_value="2026-06-01"), \
         patch("core.challenge_progress_tracker._get_end_date", return_value="2026-07-01"), \
         patch("core.challenge_progress_tracker._get_conservative_threshold", return_value=80.0), \
         patch("core.challenge_progress_tracker._get_size_reduction_factor", return_value=0.50), \
         patch("core.challenge_progress_tracker._get_protect_enabled", return_value=True):
        yield


# ─── TEST: PROGRESS CALCULATIONS ──────────────────────────────────────────────

class TestProgressCalculation:
    def test_zero_percent(self, default_config):
        """No profit = 0% progress."""
        p = evaluate_challenge_progress(
            current_profit_percent=0.0,
            reference_date=date(2026, 6, 10),
        )
        assert p.progress_percent == 0.0
        assert p.conservative_mode is False
        assert p.protect_mode is False

    def test_fifty_percent(self, default_config):
        """4% of 8% target = 50% progress."""
        p = evaluate_challenge_progress(
            current_profit_percent=4.0,
            reference_date=date(2026, 6, 15),
        )
        assert p.progress_percent == 50.0
        assert p.conservative_mode is False
        assert p.protect_mode is False

    def test_eighty_percent(self, default_config):
        """6.4% of 8% target = 80% → conservative mode activates."""
        p = evaluate_challenge_progress(
            current_profit_percent=6.4,
            reference_date=date(2026, 6, 20),
        )
        assert p.progress_percent == 80.0
        assert p.conservative_mode is True
        assert p.protect_mode is False

    def test_hundred_percent(self, default_config):
        """8% of 8% target = 100% → protect mode activates."""
        p = evaluate_challenge_progress(
            current_profit_percent=8.0,
            reference_date=date(2026, 6, 25),
        )
        assert p.progress_percent == 100.0
        assert p.conservative_mode is True  # Also true (>80%)
        assert p.protect_mode is True

    def test_over_hundred_percent(self, default_config):
        """Above target = protect mode."""
        p = evaluate_challenge_progress(
            current_profit_percent=10.0,
            reference_date=date(2026, 6, 25),
        )
        assert p.progress_percent == 125.0
        assert p.protect_mode is True

    def test_negative_profit(self, default_config):
        """Negative profit = 0% progress (clamped)."""
        p = evaluate_challenge_progress(
            current_profit_percent=-2.0,
            reference_date=date(2026, 6, 10),
        )
        assert p.progress_percent == 0.0
        assert p.conservative_mode is False


# ─── TEST: CONSERVATIVE MODE ──────────────────────────────────────────────────

class TestConservativeMode:
    def test_activates_at_threshold(self, default_config):
        """Conservative mode activates at exactly 80% progress."""
        p = evaluate_challenge_progress(
            current_profit_percent=6.4,  # 6.4/8.0 = 80%
            reference_date=date(2026, 6, 20),
        )
        assert p.conservative_mode is True

    def test_not_active_below_threshold(self, default_config):
        """Below 80% progress → not conservative."""
        p = evaluate_challenge_progress(
            current_profit_percent=6.0,  # 6.0/8.0 = 75%
            reference_date=date(2026, 6, 20),
        )
        assert p.conservative_mode is False

    def test_reduces_effective_risk(self, default_config):
        """Conservative mode reduces risk by factor."""
        # At 85% progress → conservative
        with patch("core.challenge_progress_tracker.evaluate_challenge_progress") as mock_eval:
            mock_eval.return_value = ChallengeProgress(
                current_profit_percent=6.8,
                target_percent=8.0,
                progress_percent=85.0,
                days_elapsed=20,
                days_remaining=10,
                conservative_mode=True,
                protect_mode=False,
            )
            effective = get_effective_risk_percent(1.0)

        assert effective == 0.5  # 1.0 * 0.50

    def test_normal_risk_below_threshold(self, default_config):
        """Below threshold → full risk."""
        with patch("core.challenge_progress_tracker.evaluate_challenge_progress") as mock_eval:
            mock_eval.return_value = ChallengeProgress(
                current_profit_percent=3.0,
                target_percent=8.0,
                progress_percent=37.5,
                days_elapsed=10,
                days_remaining=20,
                conservative_mode=False,
                protect_mode=False,
            )
            effective = get_effective_risk_percent(1.0)

        assert effective == 1.0  # Unchanged


# ─── TEST: PROTECT MODE ───────────────────────────────────────────────────────

class TestProtectMode:
    def test_activates_at_target(self, default_config):
        """Protect mode activates when profit >= target."""
        p = evaluate_challenge_progress(
            current_profit_percent=8.1,
            reference_date=date(2026, 6, 25),
        )
        assert p.protect_mode is True

    def test_blocks_new_trades(self, default_config):
        """Protect mode blocks new entries via check_challenge_gate."""
        with patch("core.challenge_progress_tracker.evaluate_challenge_progress") as mock_eval:
            mock_eval.return_value = ChallengeProgress(
                current_profit_percent=8.1,
                target_percent=8.0,
                progress_percent=101.25,
                days_elapsed=25,
                days_remaining=5,
                conservative_mode=True,
                protect_mode=True,
            )
            result = check_challenge_gate()

        assert result.allowed is False
        assert result.reason == REJECT_CHALLENGE_TARGET_ACHIEVED
        assert result.current_profit_percent == 8.1

    def test_allows_below_target(self, default_config):
        """Below target → allowed."""
        with patch("core.challenge_progress_tracker.evaluate_challenge_progress") as mock_eval:
            mock_eval.return_value = ChallengeProgress(
                current_profit_percent=5.0,
                target_percent=8.0,
                progress_percent=62.5,
                days_elapsed=15,
                days_remaining=15,
                conservative_mode=False,
                protect_mode=False,
            )
            result = check_challenge_gate()

        assert result.allowed is True

    def test_protect_disabled_allows(self, default_config):
        """When protect mode disabled, target reached still allows."""
        with patch("core.challenge_progress_tracker._get_protect_enabled", return_value=False):
            p = evaluate_challenge_progress(
                current_profit_percent=9.0,
                reference_date=date(2026, 6, 25),
            )
        assert p.protect_mode is False


# ─── TEST: DATE LOGIC ──────────────────────────────────────────────────────────

class TestDateLogic:
    def test_days_elapsed(self, default_config):
        """Days elapsed calculated correctly."""
        p = evaluate_challenge_progress(
            current_profit_percent=4.0,
            reference_date=date(2026, 6, 21),
        )
        assert p.days_elapsed == 20  # June 1 → June 21

    def test_days_remaining(self, default_config):
        """Days remaining calculated correctly."""
        p = evaluate_challenge_progress(
            current_profit_percent=4.0,
            reference_date=date(2026, 6, 21),
        )
        assert p.days_remaining == 10  # June 21 → July 1

    def test_expired_challenge(self, default_config):
        """Past end date → 0 days remaining."""
        p = evaluate_challenge_progress(
            current_profit_percent=4.0,
            reference_date=date(2026, 7, 15),
        )
        assert p.days_remaining == 0
        assert p.days_elapsed == 44  # June 1 → July 15

    def test_before_start(self, default_config):
        """Before start date → 0 days elapsed, full remaining."""
        p = evaluate_challenge_progress(
            current_profit_percent=0.0,
            reference_date=date(2026, 5, 25),
        )
        assert p.days_elapsed == 0
        assert p.days_remaining == 37  # May 25 → July 1

    def test_invalid_dates(self, default_config):
        """Invalid dates → (0, 0)."""
        with patch("core.challenge_progress_tracker._get_start_date", return_value=""), \
             patch("core.challenge_progress_tracker._get_end_date", return_value=""):
            elapsed, remaining = _compute_time(date(2026, 6, 15))
        assert elapsed == 0
        assert remaining == 0


# ─── TEST: DISABLED MODE ──────────────────────────────────────────────────────

class TestDisabledMode:
    def test_disabled_always_allows(self, default_config):
        """When disabled, check always allows."""
        with patch("core.challenge_progress_tracker._is_enabled", return_value=False):
            result = check_challenge_gate(current_profit_percent=99.0)

        assert result.allowed is True
        assert result.reason == "CHALLENGE_MODE_DISABLED"

    def test_disabled_risk_unchanged(self, default_config):
        """When disabled, risk is not modified."""
        with patch("core.challenge_progress_tracker._is_enabled", return_value=False):
            effective = get_effective_risk_percent(1.5)

        assert effective == 1.5


# ─── TEST: PERSISTENCE ────────────────────────────────────────────────────────

class TestPersistence:
    def test_json_output_valid(self, tmp_path, default_config):
        """Persistence writes valid JSON."""
        with patch("core.challenge_progress_tracker._get_persistence_path",
                   return_value=tmp_path / "progress.json"):
            progress = ChallengeProgress(
                current_profit_percent=6.8,
                target_percent=8.0,
                progress_percent=85.0,
                days_elapsed=20,
                days_remaining=10,
                conservative_mode=True,
                protect_mode=False,
            )
            result = persist_progress(progress)

        assert result is True

        data = json.loads((tmp_path / "progress.json").read_text())
        assert data["current_profit_percent"] == 6.8
        assert data["progress_percent"] == 85.0
        assert data["mode"] == "CONSERVATIVE"

    def test_protect_mode_persisted(self, tmp_path, default_config):
        """Protect mode reflected in persistence."""
        with patch("core.challenge_progress_tracker._get_persistence_path",
                   return_value=tmp_path / "progress.json"):
            progress = ChallengeProgress(
                current_profit_percent=8.1,
                target_percent=8.0,
                progress_percent=101.25,
                days_elapsed=25,
                days_remaining=5,
                conservative_mode=True,
                protect_mode=True,
            )
            persist_progress(progress)

        data = json.loads((tmp_path / "progress.json").read_text())
        assert data["mode"] == "PROTECT"


# ─── TEST: CONFIG VALIDATION ──────────────────────────────────────────────────

class TestConfigValidation:
    def test_valid_config_no_errors(self, default_config):
        """Valid challenge config has no errors."""
        errors = validate_challenge_config()
        assert errors == []

    def test_zero_target_errors(self, default_config):
        """Zero target generates error."""
        with patch("core.challenge_progress_tracker._get_profit_target", return_value=0):
            errors = validate_challenge_config()
        assert any("PROFIT_TARGET" in e for e in errors)

    def test_end_before_start_errors(self, default_config):
        """End date before start date generates error."""
        with patch("core.challenge_progress_tracker._get_start_date", return_value="2026-07-01"), \
             patch("core.challenge_progress_tracker._get_end_date", return_value="2026-06-01"):
            errors = validate_challenge_config()
        assert any("after" in e for e in errors)

    def test_invalid_factor_errors(self, default_config):
        """Invalid size reduction factor generates error."""
        with patch("core.challenge_progress_tracker._get_size_reduction_factor", return_value=0):
            errors = validate_challenge_config()
        assert any("REDUCTION_FACTOR" in e for e in errors)


# ─── TEST: PRODUCTION INTEGRATION ─────────────────────────────────────────────

class TestProductionIntegration:
    def test_gate_in_pipeline_before_execution(self):
        """Challenge gate appears in runtime guard chain."""
        import inspect
        from risk import runtime_guard_chain

        source = inspect.getsource(runtime_guard_chain.evaluate_runtime_guards)

        challenge_pos = source.find("check_challenge_gate")

        assert challenge_pos > 0, "Challenge gate not found in runtime guard chain"


# ─── TEST: EQUITY-BASED PROFIT CALCULATION ─────────────────────────────────────

class TestEquityProfit:
    def test_compute_profit_from_equity(self, default_config):
        """Profit computed from (current_equity - start) / start * 100."""
        from core.challenge_progress_tracker import compute_challenge_profit_percent, _cached_start_equity
        import core.challenge_progress_tracker as mod

        # Set cached start equity
        mod._cached_start_equity = 100000.0

        mock_acct = MagicMock(equity=104000.0)
        with patch("core.challenge_progress_tracker._fetch_current_equity", return_value=104000.0):
            profit = compute_challenge_profit_percent()

        # (104000 - 100000) / 100000 * 100 = 4.0%
        assert profit == pytest.approx(4.0, abs=0.01)

        # Cleanup
        mod._cached_start_equity = None

    def test_profit_includes_floating_pnl(self, default_config):
        """Equity includes floating P&L — so challenge progress reflects open trades."""
        import core.challenge_progress_tracker as mod
        mod._cached_start_equity = 100000.0

        # Equity of 107000 = 7% profit (includes open position floating P&L)
        with patch("core.challenge_progress_tracker._fetch_current_equity", return_value=107000.0):
            profit = compute_challenge_profit_percent()

        assert profit == pytest.approx(7.0, abs=0.01)
        mod._cached_start_equity = None

    def test_negative_profit(self, default_config):
        """Negative equity change = negative profit %."""
        import core.challenge_progress_tracker as mod
        mod._cached_start_equity = 100000.0

        with patch("core.challenge_progress_tracker._fetch_current_equity", return_value=97000.0):
            profit = compute_challenge_profit_percent()

        assert profit == pytest.approx(-3.0, abs=0.01)
        mod._cached_start_equity = None

    def test_no_start_equity_returns_none(self, default_config):
        """If start equity can't be determined, returns None."""
        import core.challenge_progress_tracker as mod
        mod._cached_start_equity = None

        with patch("core.challenge_progress_tracker._get_start_equity", return_value=0.0), \
             patch("core.challenge_progress_tracker._get_baseline_path") as mock_path:
            mock_path.return_value = Path("/nonexistent/path.json")
            with patch("core.challenge_progress_tracker._fetch_current_equity", return_value=None):
                profit = compute_challenge_profit_percent()

        assert profit is None
        mod._cached_start_equity = None

    def test_evaluate_uses_equity_when_no_arg(self, default_config):
        """evaluate_challenge_progress() uses equity-based calc when no arg."""
        import core.challenge_progress_tracker as mod
        mod._cached_start_equity = 100000.0

        with patch("core.challenge_progress_tracker._fetch_current_equity", return_value=106400.0):
            p = evaluate_challenge_progress(reference_date=date(2026, 6, 20))

        # 6.4% profit → 80% of 8% target → conservative mode
        assert p.current_profit_percent == pytest.approx(6.4, abs=0.01)
        assert p.progress_percent == pytest.approx(80.0, abs=0.1)
        assert p.conservative_mode is True
        assert p.protect_mode is False

        mod._cached_start_equity = None

    def test_protect_mode_from_equity(self, default_config):
        """Protect mode activates from live equity measurement."""
        import core.challenge_progress_tracker as mod
        mod._cached_start_equity = 100000.0

        with patch("core.challenge_progress_tracker._fetch_current_equity", return_value=108500.0):
            p = evaluate_challenge_progress(reference_date=date(2026, 6, 25))

        # 8.5% profit → target=8% → protect mode
        assert p.current_profit_percent == pytest.approx(8.5, abs=0.01)
        assert p.protect_mode is True

        mod._cached_start_equity = None


# ─── TEST: BASELINE PERSISTENCE ────────────────────────────────────────────────

class TestBaselinePersistence:
    def test_config_equity_takes_priority(self, default_config):
        """Explicit CHALLENGE_START_EQUITY config takes priority."""
        import core.challenge_progress_tracker as mod
        mod._cached_start_equity = None

        with patch("core.challenge_progress_tracker._get_start_equity", return_value=50000.0):
            from core.challenge_progress_tracker import _resolve_start_equity
            result = _resolve_start_equity()

        assert result == 50000.0
        mod._cached_start_equity = None

    def test_persisted_baseline_survives_restart(self, tmp_path, default_config):
        """Persisted baseline file is loaded on restart."""
        import core.challenge_progress_tracker as mod
        mod._cached_start_equity = None

        # Write a baseline file
        baseline_file = tmp_path / "challenge_baseline.json"
        baseline_file.write_text(json.dumps({"start_equity": 75000.0, "captured_at": "2026-06-01"}))

        with patch("core.challenge_progress_tracker._get_start_equity", return_value=0.0), \
             patch("core.challenge_progress_tracker._get_baseline_path", return_value=baseline_file):
            from core.challenge_progress_tracker import _resolve_start_equity
            result = _resolve_start_equity()

        assert result == 75000.0
        mod._cached_start_equity = None

    def test_auto_capture_on_first_run(self, tmp_path, default_config):
        """First run auto-captures equity and persists it."""
        import core.challenge_progress_tracker as mod
        mod._cached_start_equity = None

        baseline_file = tmp_path / "challenge_baseline.json"

        with patch("core.challenge_progress_tracker._get_start_equity", return_value=0.0), \
             patch("core.challenge_progress_tracker._get_baseline_path", return_value=baseline_file), \
             patch("core.challenge_progress_tracker._fetch_current_equity", return_value=100000.0):
            from core.challenge_progress_tracker import _resolve_start_equity
            result = _resolve_start_equity()

        assert result == 100000.0
        # Verify persisted
        assert baseline_file.exists()
        data = json.loads(baseline_file.read_text())
        assert data["start_equity"] == 100000.0

        mod._cached_start_equity = None
