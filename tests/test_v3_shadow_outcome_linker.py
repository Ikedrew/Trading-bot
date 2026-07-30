"""
Tests for V3 Shadow Outcome Linker.

Verifies:
    - Correlation-based matching works
    - Timestamp fallback works
    - Missing outcomes handled (NO_MATCH)
    - Outcome fields attached correctly
    - Persistence works
    - Shadow trades unmodified
    - Report statistics correct
"""

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from core.v3_shadow.outcome_linker import link_v3_shadow_outcomes, V3ShadowLinkageReport


def _make_execution(symbol="EURUSD", timestamp=1785255900.0, state="READY_FOR_EXECUTION"):
    return {
        "schema_version": "v3_execution_assessment_v1",
        "symbol": symbol,
        "timestamp_utc": timestamp,
        "direction": "BULLISH",
        "execution_state": state,
        "entry_price": 1.085,
        "stop_price": 1.084,
        "target_price": 1.088,
        "horizon": "INTRADAY",
        "risk_state": "ACCEPTABLE_RISK",
    }


def _make_shadow_trade(symbol="EURUSD", entity_id="EURUSD_1785255900",
                       entry_time=1785255900.0, result_r=1.5, mfe_r=2.0, mae_r=-0.3,
                       exit_reason="TP", bars_held=12):
    return {
        "schema_version": "shadow_trades_v2",
        "identity": {
            "trade_id": f"SH_{symbol}_{int(entry_time)}",
            "correlation_id": "",
            "symbol": symbol,
            "entity_id": entity_id,
        },
        "decision_snapshot": {
            "timestamp_decision_utc": entry_time,
            "entry_intent_price": 1.085,
            "direction": "BUY",
        },
        "simulated_outcome": {
            "pnl_r_multiple": result_r,
            "mfe_r": mfe_r,
            "mae_r": mae_r,
            "exit_reason": exit_reason,
            "bars_held": bars_held,
        },
    }


class _Base:
    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.exec_dir = Path(self.temp_dir) / "v3_shadow" / "execution_assessment"
        self.shadow_dir = Path(self.temp_dir) / "shadow"

    def teardown_method(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_exec(self, rec):
        sym = rec["symbol"]
        path = self.exec_dir / sym / "2025-07-28.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(rec) + "\n")

    def _write_shadow(self, trade):
        sym = trade["identity"]["symbol"]
        path = self.shadow_dir / sym / "2025-07-28.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(trade) + "\n")


class TestCorrelationMatch(_Base):
    """Match by correlation key (symbol_timestamp = entity_id)."""

    def test_exact_match(self):
        ex = _make_execution(timestamp=1785255900.0)
        trade = _make_shadow_trade(entity_id="EURUSD_1785255900", result_r=1.5)
        self._write_exec(ex)
        self._write_shadow(trade)

        report = link_v3_shadow_outcomes(
            v3_base_dir=str(Path(self.temp_dir) / "v3_shadow"),
            shadow_dir=str(self.shadow_dir), persist=False)

        assert report.matched == 1
        assert report.match_by_correlation == 1
        rec = report.linked_records[0]
        assert rec["_outcome_linked"] is True
        assert rec["_outcome"]["result_r"] == 1.5
        assert rec["_outcome"]["win"] is True
        assert rec["_outcome"]["exit_reason"] == "TP"

    def test_mfe_mae_attached(self):
        ex = _make_execution(timestamp=1785255900.0)
        trade = _make_shadow_trade(entity_id="EURUSD_1785255900", mfe_r=2.5, mae_r=-0.4)
        self._write_exec(ex)
        self._write_shadow(trade)

        report = link_v3_shadow_outcomes(
            v3_base_dir=str(Path(self.temp_dir) / "v3_shadow"),
            shadow_dir=str(self.shadow_dir), persist=False)

        rec = report.linked_records[0]
        assert rec["_outcome"]["mfe_r"] == 2.5
        assert rec["_outcome"]["mae_r"] == -0.4
        assert rec["_outcome"]["hold_minutes"] == 60  # 12 bars * 5


class TestTimestampFallback(_Base):
    """Fallback to timestamp matching."""

    def test_within_tolerance(self):
        ex = _make_execution(timestamp=1785255900.0)
        trade = _make_shadow_trade(
            entity_id="DIFFERENT", entry_time=1785256000.0, result_r=0.8)
        self._write_exec(ex)
        self._write_shadow(trade)

        report = link_v3_shadow_outcomes(
            v3_base_dir=str(Path(self.temp_dir) / "v3_shadow"),
            shadow_dir=str(self.shadow_dir), persist=False)

        assert report.matched == 1
        assert report.match_by_timestamp == 1

    def test_beyond_tolerance(self):
        ex = _make_execution(timestamp=1785255900.0)
        trade = _make_shadow_trade(
            entity_id="DIFFERENT", entry_time=1785256500.0, result_r=0.5)
        self._write_exec(ex)
        self._write_shadow(trade)

        report = link_v3_shadow_outcomes(
            v3_base_dir=str(Path(self.temp_dir) / "v3_shadow"),
            shadow_dir=str(self.shadow_dir), persist=False)

        assert report.matched == 0
        assert report.unmatched == 1


class TestNoMatch(_Base):
    """Unmatched assessments handled gracefully."""

    def test_no_shadow_trades(self):
        ex = _make_execution()
        self._write_exec(ex)

        report = link_v3_shadow_outcomes(
            v3_base_dir=str(Path(self.temp_dir) / "v3_shadow"),
            shadow_dir=str(self.shadow_dir), persist=False)

        assert report.unmatched == 1
        assert report.linked_records[0]["_outcome_linked"] is False
        assert report.linked_records[0]["_outcome"]["reason"] == "NO_MATCH"

    def test_empty_directories(self):
        report = link_v3_shadow_outcomes(
            v3_base_dir=str(Path(self.temp_dir) / "v3_shadow"),
            shadow_dir=str(self.shadow_dir), persist=False)
        assert report.total_assessments == 0


class TestPersistence(_Base):
    """Linked records persist back to disk."""

    def test_persist_writes(self):
        ex = _make_execution(timestamp=1785255900.0)
        trade = _make_shadow_trade(entity_id="EURUSD_1785255900")
        self._write_exec(ex)
        self._write_shadow(trade)

        link_v3_shadow_outcomes(
            v3_base_dir=str(Path(self.temp_dir) / "v3_shadow"),
            shadow_dir=str(self.shadow_dir), persist=True)

        files = list(self.exec_dir.rglob("*.jsonl"))
        found_linked = False
        for f in files:
            with open(f) as fh:
                for line in fh:
                    if line.strip():
                        rec = json.loads(line)
                        if rec.get("_outcome_linked"):
                            found_linked = True
        assert found_linked


class TestShadowIntegrity(_Base):
    """Shadow trade files are never modified."""

    def test_shadow_unchanged(self):
        ex = _make_execution(timestamp=1785255900.0)
        trade = _make_shadow_trade(entity_id="EURUSD_1785255900")
        self._write_exec(ex)
        self._write_shadow(trade)

        shadow_files = list(self.shadow_dir.rglob("*.jsonl"))
        before = {str(f): f.read_text() for f in shadow_files}

        link_v3_shadow_outcomes(
            v3_base_dir=str(Path(self.temp_dir) / "v3_shadow"),
            shadow_dir=str(self.shadow_dir), persist=True)

        for f in shadow_files:
            assert f.read_text() == before[str(f)]


class TestExecutionStateTracking(_Base):
    """Report tracks execution states."""

    def test_counts_ready_and_not_executable(self):
        self._write_exec(_make_execution(timestamp=100.0, state="READY_FOR_EXECUTION"))
        self._write_exec(_make_execution(timestamp=200.0, state="NOT_EXECUTABLE"))
        self._write_exec(_make_execution(timestamp=300.0, state="SIMULATED_ONLY"))

        report = link_v3_shadow_outcomes(
            v3_base_dir=str(Path(self.temp_dir) / "v3_shadow"),
            shadow_dir=str(self.shadow_dir), persist=False)

        assert report.total_assessments == 3
        assert report.ready_executions == 1
        assert report.not_executable == 1


class TestReport:
    """Report summary."""

    def test_match_rate(self):
        r = V3ShadowLinkageReport(total_assessments=10, matched=7, unmatched=3)
        assert r.match_rate == 0.7

    def test_summary_keys(self):
        r = V3ShadowLinkageReport(
            total_assessments=50, matched=40, unmatched=10,
            match_by_correlation=30, match_by_timestamp=10,
            ready_executions=20, not_executable=15)
        s = r.summary()
        assert s["total_assessments"] == 50
        assert s["match_rate"] == 0.8
        assert s["ready_executions"] == 20
