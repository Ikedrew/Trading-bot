"""
Horizon Research Engine Integration — Tests.

Validates:
    1. Research runner loads trades
    2. Trades are grouped by horizon
    3. SCALP reports generate correctly
    4. INTRADAY with zero trades returns insufficient data
    5. EXTENDED with zero trades returns insufficient data
    6. Contract versions are preserved
    7. Reports serialize correctly
    8. Execution paths remain untouched
    9. Pipeline handles empty data gracefully
"""

from __future__ import annotations

import sys
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest

from research_engine.horizon_research import (
    run_horizon_research,
    _load_trade_journal_records,
    _TradeRecordProxy,
)


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _sample_trade_record(
    horizon: str = "SCALP",
    direction: str = "BUY",
    entry: float = 1.1000,
    exit_p: float = 1.1020,
    sl: float = 1.0980,
    tp: float = 1.1040,
    duration: float = 2400.0,
    mfe: float = 1.1025,
    close_reason: str = "tp_hit",
    symbol: str = "EURUSD",
) -> dict:
    return {
        "trade_id": "pos_test",
        "position_ticket": 100,
        "symbol": symbol,
        "magic": 713001,
        "pattern_name": "HAMMER",
        "direction": direction,
        "entry_time": 1719000000.0,
        "exit_time": 1719000000.0 + duration,
        "duration_seconds": duration,
        "entry_price": entry,
        "exit_price": exit_p,
        "initial_volume": 0.01,
        "final_volume": 0.01,
        "realised_pnl": 2.0,
        "commission": 0.0,
        "swap": 0.0,
        "net_pnl": 2.0,
        "close_reason": close_reason,
        "initial_sl": sl,
        "initial_tp": tp,
        "max_favourable_price": mfe,
        "recorded_at_utc": "2026-07-23T00:00:00Z",
        "correlation_id": "COR-123",
        "trade_horizon": horizon,
    }


def _write_journal_file(tmpdir: Path, records: list[dict]) -> None:
    """Write records to a trade journal JSONL file in tmpdir."""
    journal_dir = tmpdir / "logs" / "trade_journal"
    journal_dir.mkdir(parents=True, exist_ok=True)
    filepath = journal_dir / "2026-07-23.jsonl"
    with open(filepath, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Research Runner Loads Trades
# ═══════════════════════════════════════════════════════════════════════════════

class TestRunnerLoadsTrades:
    def test_loads_from_trade_journal(self, tmp_path):
        records = [_sample_trade_record() for _ in range(5)]
        _write_journal_file(tmp_path, records)

        with patch("research_engine.horizon_research._get_project_root", return_value=tmp_path):
            loaded = _load_trade_journal_records()
            assert len(loaded) == 5

    def test_empty_directory_returns_empty(self, tmp_path):
        with patch("research_engine.horizon_research._get_project_root", return_value=tmp_path):
            loaded = _load_trade_journal_records()
            assert loaded == []

    def test_proxy_provides_attribute_access(self):
        data = _sample_trade_record()
        proxy = _TradeRecordProxy(data)
        assert proxy.symbol == "EURUSD"
        assert proxy.direction == "BUY"
        assert proxy.trade_horizon == "SCALP"
        assert proxy.entry_price == 1.1


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Trades Grouped by Horizon
# ═══════════════════════════════════════════════════════════════════════════════

class TestHorizonGrouping:
    def test_scalp_trades_grouped(self, tmp_path):
        records = [_sample_trade_record(horizon="SCALP") for _ in range(10)]
        _write_journal_file(tmp_path, records)

        with patch("research_engine.horizon_research._get_project_root", return_value=tmp_path):
            result = run_horizon_research(persist=False, min_sample_size=5)
            assert result["horizons"]["SCALP"]["sample_size"] == 10

    def test_mixed_horizons_separated(self, tmp_path):
        records = (
            [_sample_trade_record(horizon="SCALP") for _ in range(8)]
            + [_sample_trade_record(horizon="INTRADAY") for _ in range(3)]
        )
        _write_journal_file(tmp_path, records)

        with patch("research_engine.horizon_research._get_project_root", return_value=tmp_path):
            result = run_horizon_research(persist=False, min_sample_size=5)
            assert result["horizons"]["SCALP"]["sample_size"] == 8
            assert result["horizons"]["INTRADAY"]["sample_size"] == 3


# ═══════════════════════════════════════════════════════════════════════════════
# 3. SCALP Reports Generate Correctly
# ═══════════════════════════════════════════════════════════════════════════════

class TestScalpReports:
    def test_scalp_report_has_metrics(self, tmp_path):
        records = [_sample_trade_record(duration=2400.0) for _ in range(25)]
        _write_journal_file(tmp_path, records)

        with patch("research_engine.horizon_research._get_project_root", return_value=tmp_path):
            result = run_horizon_research(persist=False, min_sample_size=20)
            scalp = result["horizons"]["SCALP"]
            assert "metrics" in scalp
            assert "overall_status" in scalp
            assert scalp["contract_version"] == "SCALP_RESEARCH_V1"

    def test_scalp_report_overall_status(self, tmp_path):
        # Trades with 40 min hold, 1R profit → should validate
        records = [
            _sample_trade_record(
                entry=1.1, exit_p=1.102, sl=1.098,
                duration=2400.0, mfe=1.103,
            )
            for _ in range(25)
        ]
        _write_journal_file(tmp_path, records)

        with patch("research_engine.horizon_research._get_project_root", return_value=tmp_path):
            result = run_horizon_research(persist=False, min_sample_size=20)
            scalp = result["horizons"]["SCALP"]
            assert scalp["overall_status"] in ("VALIDATED", "PARTIALLY_VALIDATED")


# ═══════════════════════════════════════════════════════════════════════════════
# 4 & 5. Inactive Horizons Return Insufficient Data
# ═══════════════════════════════════════════════════════════════════════════════

class TestInactiveHorizons:
    def test_intraday_insufficient_data(self, tmp_path):
        records = [_sample_trade_record(horizon="SCALP") for _ in range(25)]
        _write_journal_file(tmp_path, records)

        with patch("research_engine.horizon_research._get_project_root", return_value=tmp_path):
            result = run_horizon_research(persist=False)
            intraday = result["horizons"]["INTRADAY"]
            assert intraday["overall_status"] == "INSUFFICIENT_DATA"

    def test_extended_insufficient_data(self, tmp_path):
        records = [_sample_trade_record(horizon="SCALP") for _ in range(25)]
        _write_journal_file(tmp_path, records)

        with patch("research_engine.horizon_research._get_project_root", return_value=tmp_path):
            result = run_horizon_research(persist=False)
            extended = result["horizons"]["EXTENDED"]
            assert extended["overall_status"] == "INSUFFICIENT_DATA"

    def test_all_horizons_present_even_with_zero_trades(self, tmp_path):
        _write_journal_file(tmp_path, [])

        with patch("research_engine.horizon_research._get_project_root", return_value=tmp_path):
            result = run_horizon_research(persist=False)
            assert "SCALP" in result["horizons"]
            assert "INTRADAY" in result["horizons"]
            assert "EXTENDED" in result["horizons"]


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Contract Versions Preserved
# ═══════════════════════════════════════════════════════════════════════════════

class TestVersionPreservation:
    def test_scalp_version_in_report(self, tmp_path):
        records = [_sample_trade_record() for _ in range(25)]
        _write_journal_file(tmp_path, records)

        with patch("research_engine.horizon_research._get_project_root", return_value=tmp_path):
            result = run_horizon_research(persist=False, min_sample_size=20)
            assert result["horizons"]["SCALP"]["contract_version"] == "SCALP_RESEARCH_V1"

    def test_intraday_version_in_report(self, tmp_path):
        records = [_sample_trade_record(horizon="INTRADAY") for _ in range(25)]
        _write_journal_file(tmp_path, records)

        with patch("research_engine.horizon_research._get_project_root", return_value=tmp_path):
            result = run_horizon_research(persist=False, min_sample_size=20)
            assert result["horizons"]["INTRADAY"]["contract_version"] == "INTRADAY_RESEARCH_V1"


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Reports Serialize Correctly
# ═══════════════════════════════════════════════════════════════════════════════

class TestSerialization:
    def test_result_is_json_serializable(self, tmp_path):
        records = [_sample_trade_record() for _ in range(5)]
        _write_journal_file(tmp_path, records)

        with patch("research_engine.horizon_research._get_project_root", return_value=tmp_path):
            result = run_horizon_research(persist=False, min_sample_size=3)
            serialized = json.dumps(result)
            assert isinstance(serialized, str)
            parsed = json.loads(serialized)
            assert parsed["experiment_name"] == "horizon_research"

    def test_report_has_metadata(self, tmp_path):
        records = [_sample_trade_record() for _ in range(5)]
        _write_journal_file(tmp_path, records)

        with patch("research_engine.horizon_research._get_project_root", return_value=tmp_path):
            result = run_horizon_research(persist=False, min_sample_size=3)
            assert "generated_at" in result
            assert "data_source" in result
            assert result["data_source"] == "trade_journal"
            assert "analysis_period" in result
            assert "total_trades_loaded" in result
            assert result["total_trades_loaded"] == 5

    def test_persist_creates_file(self, tmp_path):
        records = [_sample_trade_record() for _ in range(5)]
        _write_journal_file(tmp_path, records)

        with patch("research_engine.horizon_research._get_project_root", return_value=tmp_path):
            result = run_horizon_research(persist=True, min_sample_size=3)

        reports_dir = tmp_path / "research_reports"
        assert reports_dir.exists()
        report_files = list(reports_dir.glob("horizon_research_*.json"))
        assert len(report_files) == 1

        # Verify content
        content = json.loads(report_files[0].read_text(encoding="utf-8"))
        assert content["experiment_name"] == "horizon_research"


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Execution Paths Unchanged
# ═══════════════════════════════════════════════════════════════════════════════

class TestExecutionUnchanged:
    def test_permitted_horizons_match_current_runtime(self):
        from core import config
        assert config.PERMITTED_HORIZONS == ["SCALP", "INTRADAY", "EXTENDED"]

    def test_authority_allows_intraday(self):
        from core.horizon.execution_authority import HorizonExecutionAuthority
        auth = HorizonExecutionAuthority()
        result = auth.can_open(symbol="EURUSD", horizon="INTRADAY", current_positions=[])
        assert result.allowed is True


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Empty Data Graceful Handling
# ═══════════════════════════════════════════════════════════════════════════════

class TestEmptyData:
    def test_no_journal_directory(self, tmp_path):
        with patch("research_engine.horizon_research._get_project_root", return_value=tmp_path):
            result = run_horizon_research(persist=False)
            assert result["total_trades_loaded"] == 0
            assert result["analysis_period"] == "no_data"
            # All horizons should still report
            for h in ("SCALP", "INTRADAY", "EXTENDED"):
                assert h in result["horizons"]
