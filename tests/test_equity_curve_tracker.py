"""
Tests for I4: Equity Curve Tracking / Performance Monitoring.

Covers:
- Snapshot writes JSONL correctly
- Append-only behaviour (no overwrite)
- Rolling window selection (30 days)
- Sharpe calculation correctness
- Drawdown calculation correctness
- Edge decay trigger works
- System handles empty history
- Disabled mode skips
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.equity_curve_tracker import (
    EquitySnapshot,
    PerformanceMetrics,
    record_daily_equity_snapshot,
    load_equity_curve,
    compute_performance_metrics,
    check_edge_decay,
    _compute_max_drawdown,
    _append_snapshot,
    validate_equity_curve_config,
)


# ─── FIXTURES ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def default_config(tmp_path):
    """Set known config and redirect curve file."""
    curve_file = tmp_path / "equity_curve.jsonl"
    with patch("core.equity_curve_tracker._is_enabled", return_value=True), \
         patch("core.equity_curve_tracker._get_curve_path", return_value=curve_file), \
         patch("core.equity_curve_tracker._get_sharpe_threshold", return_value=0.5):
        yield curve_file


# ─── TEST: SNAPSHOT WRITES JSONL ───────────────────────────────────────────────

class TestSnapshotWrite:
    def test_writes_valid_jsonl(self, default_config):
        """Snapshot is written as valid JSON line."""
        result = record_daily_equity_snapshot(equity=103000.0, balance=101000.0)

        assert result is not None
        assert default_config.exists()

        lines = default_config.read_text().strip().split("\n")
        assert len(lines) == 1

        entry = json.loads(lines[0])
        assert entry["equity"] == 103000.0
        assert entry["balance"] == 101000.0
        assert entry["unrealized_pnl"] == 2000.0
        assert "timestamp" in entry
        assert "timestamp_iso" in entry

    def test_snapshot_fields_complete(self, default_config):
        """All required fields are present."""
        record_daily_equity_snapshot(equity=50000.0, balance=49000.0)

        entry = json.loads(default_config.read_text().strip())
        required = {"timestamp", "timestamp_iso", "equity", "balance", "unrealized_pnl", "realized_pnl"}
        assert required.issubset(set(entry.keys()))


# ─── TEST: APPEND-ONLY ────────────────────────────────────────────────────────

class TestAppendOnly:
    def test_multiple_writes_append(self, default_config):
        """Multiple snapshots are appended, not overwritten."""
        record_daily_equity_snapshot(equity=100000.0, balance=100000.0)
        record_daily_equity_snapshot(equity=101000.0, balance=100500.0)
        record_daily_equity_snapshot(equity=102000.0, balance=101000.0)

        lines = default_config.read_text().strip().split("\n")
        assert len(lines) == 3

        # Verify ordering
        e1 = json.loads(lines[0])["equity"]
        e2 = json.loads(lines[1])["equity"]
        e3 = json.loads(lines[2])["equity"]
        assert e1 == 100000.0
        assert e2 == 101000.0
        assert e3 == 102000.0


# ─── TEST: ROLLING WINDOW ─────────────────────────────────────────────────────

class TestRollingWindow:
    def test_loads_last_n_days(self, default_config):
        """load_equity_curve returns only entries within window."""
        now = time.time()

        # Write entries: 1 recent, 1 old (40 days ago)
        entries = [
            {"timestamp": now - 86400 * 40, "equity": 90000, "balance": 90000,
             "unrealized_pnl": 0, "realized_pnl": 0, "timestamp_iso": "old"},
            {"timestamp": now - 86400 * 5, "equity": 100000, "balance": 100000,
             "unrealized_pnl": 0, "realized_pnl": 0, "timestamp_iso": "recent"},
        ]
        with open(default_config, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

        result = load_equity_curve(days=30, path=default_config)
        assert len(result) == 1  # Only the recent one
        assert result[0]["equity"] == 100000

    def test_empty_file_returns_empty(self, default_config):
        """Empty or missing file returns empty list."""
        result = load_equity_curve(days=30, path=default_config)
        assert result == []


# ─── TEST: SHARPE CALCULATION ──────────────────────────────────────────────────

class TestSharpeCalculation:
    def test_positive_sharpe(self, default_config):
        """Consistently positive returns → positive Sharpe."""
        now = time.time()
        entries = []
        for i in range(10):
            entries.append({
                "timestamp": now - 86400 * (10 - i),
                "equity": 100000 + i * 500,  # Steady growth
                "balance": 100000, "unrealized_pnl": 0, "realized_pnl": 0,
                "timestamp_iso": "",
            })
        with open(default_config, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

        metrics = compute_performance_metrics(days=30, path=default_config)
        assert metrics.sharpe_30d > 0

    def test_negative_sharpe(self, default_config):
        """Consistently negative returns → negative Sharpe."""
        now = time.time()
        entries = []
        for i in range(10):
            entries.append({
                "timestamp": now - 86400 * (10 - i),
                "equity": 100000 - i * 500,  # Steady decline
                "balance": 100000, "unrealized_pnl": 0, "realized_pnl": 0,
                "timestamp_iso": "",
            })
        with open(default_config, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

        metrics = compute_performance_metrics(days=30, path=default_config)
        assert metrics.sharpe_30d < 0

    def test_insufficient_data(self, default_config):
        """Less than 2 snapshots → zero Sharpe."""
        now = time.time()
        with open(default_config, "w") as f:
            f.write(json.dumps({"timestamp": now, "equity": 100000, "balance": 100000,
                                "unrealized_pnl": 0, "realized_pnl": 0, "timestamp_iso": ""}) + "\n")

        metrics = compute_performance_metrics(days=30, path=default_config)
        assert metrics.sharpe_30d == 0.0


# ─── TEST: DRAWDOWN CALCULATION ────────────────────────────────────────────────

class TestDrawdownCalculation:
    def test_no_drawdown(self):
        """Monotonically increasing equity → 0% drawdown."""
        equities = [100, 101, 102, 103, 104]
        dd = _compute_max_drawdown(equities)
        assert dd == 0.0

    def test_known_drawdown(self):
        """Peak 110, trough 100 → ~9.09% drawdown."""
        equities = [100, 105, 110, 100, 108]
        dd = _compute_max_drawdown(equities)
        assert dd == pytest.approx(9.09, abs=0.1)

    def test_recovery_after_drawdown(self):
        """Drawdown followed by recovery — max dd still tracked."""
        equities = [100, 120, 90, 130]  # Peak 120, trough 90 = 25% DD
        dd = _compute_max_drawdown(equities)
        assert dd == pytest.approx(25.0, abs=0.1)

    def test_empty_equities(self):
        """Empty list → 0%."""
        assert _compute_max_drawdown([]) == 0.0


# ─── TEST: EDGE DECAY DETECTION ───────────────────────────────────────────────

class TestEdgeDecay:
    def test_decay_triggers_alert(self, default_config, caplog):
        """Low Sharpe triggers edge decay warning."""
        import logging
        now = time.time()
        entries = []
        for i in range(10):
            entries.append({
                "timestamp": now - 86400 * (10 - i),
                "equity": 100000 - i * 300,  # Declining
                "balance": 100000, "unrealized_pnl": 0, "realized_pnl": 0,
                "timestamp_iso": "",
            })
        with open(default_config, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

        with caplog.at_level(logging.WARNING):
            triggered = check_edge_decay(days=30, path=default_config)

        assert triggered is True
        assert "EDGE_DECAY_WARNING" in caplog.text

    def test_healthy_no_alert(self, default_config, caplog):
        """High Sharpe → no decay alert."""
        import logging
        now = time.time()
        entries = []
        for i in range(10):
            entries.append({
                "timestamp": now - 86400 * (10 - i),
                "equity": 100000 + i * 2000,  # Strong growth
                "balance": 100000, "unrealized_pnl": 0, "realized_pnl": 0,
                "timestamp_iso": "",
            })
        with open(default_config, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

        with caplog.at_level(logging.WARNING):
            triggered = check_edge_decay(days=30, path=default_config)

        assert triggered is False
        assert "EDGE_DECAY_WARNING" not in caplog.text


# ─── TEST: DISABLED MODE ──────────────────────────────────────────────────────

class TestDisabled:
    def test_disabled_no_write(self, default_config):
        """Disabled → no snapshot written."""
        with patch("core.equity_curve_tracker._is_enabled", return_value=False):
            result = record_daily_equity_snapshot(equity=100000.0, balance=100000.0)
        assert result is None
        assert not default_config.exists()


# ─── TEST: CONFIG VALIDATION ──────────────────────────────────────────────────

class TestConfigValidation:
    def test_valid_config(self, default_config):
        """Valid config passes."""
        errors = validate_equity_curve_config()
        assert errors == []

    def test_negative_threshold_errors(self, default_config):
        """Negative threshold generates error."""
        with patch("core.equity_curve_tracker._get_sharpe_threshold", return_value=-1.0):
            errors = validate_equity_curve_config()
        assert any("SHARPE" in e for e in errors)
