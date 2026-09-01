"""
Tests for recovered trade identity restoration and MT5 timestamp normalization.

Covers:
1. Identity restoration from execution_results during D3 recovery
2. MT5 timestamp normalization (UTC+3 → UTC)
3. Recovered position produces valid trade_truth
4. No negative durations
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.mt5_timestamp import normalize_mt5_timestamp, get_server_offset_seconds
from core.trade_identity import TradeIdentity


# ─── PART 2 TESTS: TIMESTAMP NORMALIZATION ────────────────────────────────────

class TestMT5TimestampNormalization:
    def test_basic_conversion(self):
        """MT5 server time (UTC+3) converts to UTC correctly."""
        broker_time = 1784752774  # 2026-07-22 20:39:34 server (UTC+3)
        utc_time = normalize_mt5_timestamp(broker_time)
        expected = 1784752774 - 3 * 3600  # 17:39:34 UTC
        assert utc_time == expected

    def test_zero_returns_zero(self):
        """Zero input returns zero (no conversion)."""
        assert normalize_mt5_timestamp(0) == 0.0

    def test_negative_returns_zero(self):
        """Negative input returns zero."""
        assert normalize_mt5_timestamp(-100) == 0.0

    def test_offset_is_three_hours(self):
        """Server offset is 3 hours (10800 seconds)."""
        assert get_server_offset_seconds() == 10800

    def test_normalized_entry_before_exit(self):
        """After normalization, entry < exit (no negative duration)."""
        # Broker entry: 20:39:34 server time
        # Broker exit:  20:47:15 server time
        entry_broker = 1784752774
        exit_broker = 1784753235

        entry_utc = normalize_mt5_timestamp(entry_broker)
        exit_utc = normalize_mt5_timestamp(exit_broker)

        assert exit_utc > entry_utc
        assert (exit_utc - entry_utc) == (exit_broker - entry_broker)


# ─── PART 1 TESTS: IDENTITY RESTORATION ──────────────────────────────────────

class TestIdentityRestoration:
    @pytest.fixture
    def exec_results_dir(self, tmp_path):
        """Create a temp execution_results directory with a test record."""
        nzdusd_dir = tmp_path / "logs" / "execution_results" / "NZDUSD"
        nzdusd_dir.mkdir(parents=True)
        record = {
            "timestamp_utc": "2026-07-22T17:39:35.314Z",
            "symbol": "NZDUSD",
            "cycle_id": 1,
            "result_ok": True,
            "retcode": 10009,
            "deal": 53297071,
            "order_ticket": 80513550,
            "fill_price": 0.58151,
            "side": "SELL",
            "volume": 0.01,
            "pattern": "THREE_BLACK_CROWS",
            "correlation_id": "COR-20260722-1-NZDUSD-D2C3",
            "decision_id": "93eab925eec8",
            "canonical_opportunity_id": "NZDUSD*1784741700*THREE_BLACK_CROWS",
            "observation_id": "obs_NZDUSD_1784741700_M5",
            "decision_ts_utc_ms": 1784741966636,
        }
        with open(nzdusd_dir / "2026-07-22.jsonl", "w") as f:
            f.write(json.dumps(record) + "\n")
        return tmp_path

    def test_restore_identity_from_execution_results(self, exec_results_dir):
        """Identity is restored from execution_results by order_ticket."""
        from core.runtime.startup_recovery import _restore_identity_from_logs

        # Test the function directly by temporarily changing CWD
        import os
        old_cwd = os.getcwd()
        os.chdir(str(exec_results_dir))
        try:
            result = _restore_identity_from_logs(
                symbol="NZDUSD",
                ticket=80513550,
                entry_price=0.58151,
            )
        finally:
            os.chdir(old_cwd)

        assert result["correlation_id"] == "COR-20260722-1-NZDUSD-D2C3"
        assert result["pattern"] == "THREE_BLACK_CROWS"
        assert result["cycle_id"] == 1
        assert result["decision_id"] == "93eab925eec8"
        assert result["canonical_opportunity_id"] == "NZDUSD*1784741700*THREE_BLACK_CROWS"
        assert result["observation_id"] == "obs_NZDUSD_1784741700_M5"

    def test_restore_identity_not_found(self):
        """Returns empty dict when no matching execution result exists."""
        from core.runtime.startup_recovery import _restore_identity_from_logs

        with tempfile.TemporaryDirectory() as td:
            import os
            old_cwd = os.getcwd()
            os.chdir(td)
            try:
                result = _restore_identity_from_logs(
                    symbol="NZDUSD",
                    ticket=99999999,
                    entry_price=1.0,
                )
            finally:
                os.chdir(old_cwd)

        assert result == {}

    def test_recovered_position_has_identity(self, exec_results_dir):
        """Full D3 recovery assigns TradeIdentity from execution_results."""
        from core.runtime.startup_recovery import recover_positions_on_startup
        from core.trade_management.manager import TradeStateManager
        from core.trade_management.config import TradeManagementConfig

        cfg = TradeManagementConfig(
            break_even_trigger_rr=0, break_even_buffer_rr=0,
            trailing_step=0, trailing_start_rr=0,
            partial_tp_fraction=0, partial_tp_path_fraction=0,
            max_time_in_trade_seconds=0,
        )
        tm = TradeStateManager(cfg)

        # Mock broker position
        mock_pos = MagicMock()
        mock_pos.ticket = 80513550
        mock_pos.symbol = "NZDUSD"
        mock_pos.type = 1  # SELL
        mock_pos.magic = 713001
        mock_pos.price_open = 0.58151
        mock_pos.sl = 0.58169
        mock_pos.tp = 0.58105
        mock_pos.volume = 0.01
        mock_pos.time = 1784752774  # Broker server time (UTC+3)
        mock_pos.price_current = 0.58155

        import os
        old_cwd = os.getcwd()
        os.chdir(str(exec_results_dir))
        try:
            with patch("core.runtime.startup_recovery.mt5_call", return_value=[mock_pos]), \
                 patch("core.runtime.startup_recovery.mt5") as mock_mt5:
                mock_mt5.ORDER_TYPE_BUY = 0
                count = recover_positions_on_startup(
                    trade_manager=tm,
                    symbol="NZDUSD",
                    magic=713001,
                )
        finally:
            os.chdir(old_cwd)

        assert count == 1
        pos = tm.positions_open()[0]
        assert pos.pattern_tag == "THREE_BLACK_CROWS"
        assert pos.trade_identity is not None
        assert pos.trade_identity.correlation_id == "COR-20260722-1-NZDUSD-D2C3"
        assert pos.trade_identity.pattern == "THREE_BLACK_CROWS"
        assert pos.trade_identity.canonical_opportunity_id == (
            "NZDUSD*1784741700*THREE_BLACK_CROWS"
        )
        assert pos.trade_identity.observation_id == "obs_NZDUSD_1784741700_M5"

    def test_recovered_position_timestamp_is_utc(self, exec_results_dir):
        """Recovered position open_time is normalized to UTC."""
        from core.runtime.startup_recovery import recover_positions_on_startup
        from core.trade_management.manager import TradeStateManager
        from core.trade_management.config import TradeManagementConfig

        cfg = TradeManagementConfig(
            break_even_trigger_rr=0, break_even_buffer_rr=0,
            trailing_step=0, trailing_start_rr=0,
            partial_tp_fraction=0, partial_tp_path_fraction=0,
            max_time_in_trade_seconds=0,
        )
        tm = TradeStateManager(cfg)

        mock_pos = MagicMock()
        mock_pos.ticket = 80513550
        mock_pos.symbol = "NZDUSD"
        mock_pos.type = 1
        mock_pos.magic = 713001
        mock_pos.price_open = 0.58151
        mock_pos.sl = 0.58169
        mock_pos.tp = 0.58105
        mock_pos.volume = 0.01
        mock_pos.time = 1784752774  # Broker UTC+3
        mock_pos.price_current = 0.58155

        import os
        old_cwd = os.getcwd()
        os.chdir(str(exec_results_dir))
        try:
            with patch("core.runtime.startup_recovery.mt5_call", return_value=[mock_pos]), \
                 patch("core.runtime.startup_recovery.mt5") as mock_mt5:
                mock_mt5.ORDER_TYPE_BUY = 0
                recover_positions_on_startup(trade_manager=tm, symbol="NZDUSD", magic=713001)
        finally:
            os.chdir(old_cwd)

        pos = tm.positions_open()[0]
        expected_utc = 1784752774 - 3 * 3600
        assert pos.open_time == expected_utc


# ─── PART 3 TESTS: TRADE_TRUTH WITH RECOVERED IDENTITY ───────────────────────

class TestRecoveredTradeTruth:
    def test_recovered_trade_produces_valid_trade_truth(self):
        """A recovered position with restored identity produces valid trade_truth."""
        from core.trade_truth import build_trade_truth, validate_trade_truth

        # Simulate what trade_journal would build after identity restoration
        entry_utc = normalize_mt5_timestamp(1784752774)  # ~17:39:34 UTC
        exit_utc = entry_utc + 461  # ~7.7 minutes later

        record = build_trade_truth(
            trade_id="pos_80513550",
            correlation_id="COR-20260722-1-NZDUSD-D2C3",
            symbol="NZDUSD",
            entry_fill_price=0.58151,
            exit_fill_price=0.58169,
            volume_executed=0.01,
            entry_timestamp_broker=entry_utc,
            exit_timestamp_broker=exit_utc,
            pnl_realised=-0.13,
            r_multiple_realised=-1.0,
            exit_reason="stop_loss_hit",
        )

        valid, reason = validate_trade_truth(record)
        assert valid is True, f"Validation failed: {reason}"
        assert record["timestamps"]["duration_seconds"] > 0

    def test_synthetic_correlation_id_passes_validation(self):
        """Positions without restored identity use RECOVERED-{id} which passes validation."""
        from core.trade_truth import build_trade_truth, validate_trade_truth

        record = build_trade_truth(
            trade_id="pos_99999",
            correlation_id="RECOVERED-pos_99999",  # Synthetic fallback
            symbol="NZDUSD",
            entry_fill_price=0.58151,
            exit_fill_price=0.58169,
            volume_executed=0.01,
            entry_timestamp_broker=1784741974.0,
            exit_timestamp_broker=1784742435.0,
            pnl_realised=-0.13,
            r_multiple_realised=-1.0,
            exit_reason="stop_loss_hit",
        )

        valid, reason = validate_trade_truth(record)
        assert valid is True, f"Validation failed: {reason}"

    def test_no_negative_duration_after_normalization(self):
        """Duration is always positive when both timestamps are properly normalized."""
        from core.trade_truth import build_trade_truth

        entry_utc = normalize_mt5_timestamp(1784752774)
        exit_utc = normalize_mt5_timestamp(1784753235)

        record = build_trade_truth(
            trade_id="pos_test",
            correlation_id="COR-TEST",
            symbol="NZDUSD",
            entry_fill_price=0.58151,
            exit_fill_price=0.58169,
            volume_executed=0.01,
            entry_timestamp_broker=entry_utc,
            exit_timestamp_broker=exit_utc,
            pnl_realised=-0.13,
            r_multiple_realised=-1.0,
            exit_reason="stop_loss_hit",
        )

        assert record["timestamps"]["duration_seconds"] > 0
        assert record["timestamps"]["duration_seconds"] == 461.0
