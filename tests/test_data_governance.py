"""Tests for V10 Layer 0 — Data Governance."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from research_engine.v10.data_governance import DataGovernanceValidator, GovernanceStatus


# ═══════════════════════════════════════════════════════════════
# FIXTURES — Synthetic test data
# ═══════════════════════════════════════════════════════════════

def _make_trade(ticket: int, symbol: str = "EURUSD", pnl: float = -0.5) -> dict:
    return {
        "trade_id": f"pos_{ticket}",
        "position_ticket": ticket,
        "symbol": symbol,
        "direction": "BUY",
        "entry_time": 1784742000.0 + ticket,
        "exit_time": 1784742100.0 + ticket,
        "entry_price": 1.1000,
        "exit_price": 1.0995,
        "stop_loss": 1.0990,
        "take_profit": 1.1020,
        "broker_pnl": pnl,
        "final_pnl": pnl,
        "realised_r": -1.0 if pnl < 0 else 1.0,
        "exit_reason_validated": "STOP_LOSS",
        "correlation_id": f"COR-20260722-{ticket}-{symbol}-ABCD",
        "pattern": "THREE_BLACK_CROWS",
        "strategy": "REVERSAL",
        "score": 0.55,
        "regime": "TRENDING",
        "instrument_class": "FX_MAJOR",
    }


def _make_recon_entry(ticket: int, symbol: str = "EURUSD", broker_net: float = -0.5) -> dict:
    return {
        "trade_id": f"pos_{ticket}",
        "symbol": symbol,
        "position_ticket": ticket,
        "mt5_matched": True,
        "broker_net_profit": broker_net,
    }


@pytest.fixture
def perfect_dataset(tmp_path):
    """A perfectly consistent dataset that should return PASS."""
    tickets = [100, 200, 300, 400, 500]

    # Journal
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir()
    journal_trades = [_make_trade(t) for t in tickets]
    (journal_dir / "2026-07-22.jsonl").write_text(
        "\n".join(json.dumps(t) for t in journal_trades), encoding="utf-8"
    )

    # Research (same trades)
    research_file = tmp_path / "research.jsonl"
    research_file.write_text(
        "\n".join(json.dumps(t) for t in journal_trades), encoding="utf-8"
    )

    # Excluded (empty)
    excluded_file = tmp_path / "excluded.jsonl"
    excluded_file.write_text("", encoding="utf-8")

    # Reconciliation
    recon_file = tmp_path / "recon.json"
    recon_data = {
        "matched": 5,
        "unmatched": 0,
        "entries": [_make_recon_entry(t) for t in tickets],
    }
    recon_file.write_text(json.dumps(recon_data), encoding="utf-8")

    # Decision traces (EXECUTE for each trade)
    dt_dir = tmp_path / "dt" / "EURUSD"
    dt_dir.mkdir(parents=True)
    dt_entries = []
    for t in tickets:
        dt_entries.append(json.dumps({
            "action": "EXECUTE",
            "symbol": "EURUSD",
            "cycle_id": t,
            "entity_id": f"EURUSD_{int(1784742000 + t) // 300 * 300}",
            "correlation_id": f"COR-20260722-{t}-EURUSD-ABCD",
        }))
    (dt_dir / "2026-07-22.jsonl").write_text("\n".join(dt_entries), encoding="utf-8")

    return {
        "journal_dir": str(journal_dir),
        "research_file": str(research_file),
        "excluded_file": str(excluded_file),
        "recon_file": str(recon_file),
        "decision_trace_dir": str(tmp_path / "dt"),
        "reports_dir": str(tmp_path / "reports"),
    }


# ═══════════════════════════════════════════════════════════════
# TEST: PERFECT DATASET RETURNS PASS
# ═══════════════════════════════════════════════════════════════

class TestPerfectDataset:
    def test_overall_pass(self, perfect_dataset):
        v = DataGovernanceValidator(**perfect_dataset)
        result = v.validate()
        assert result["data_trust"] == GovernanceStatus.PASS

    def test_trade_counts_pass(self, perfect_dataset):
        v = DataGovernanceValidator(**perfect_dataset)
        result = v.validate()
        assert result["checks"]["trade_counts"]["status"] == GovernanceStatus.PASS

    def test_identity_pass(self, perfect_dataset):
        v = DataGovernanceValidator(**perfect_dataset)
        result = v.validate()
        assert result["checks"]["identity_validation"]["status"] == GovernanceStatus.PASS

    def test_decision_coverage_pass(self, perfect_dataset):
        v = DataGovernanceValidator(**perfect_dataset)
        result = v.validate()
        assert result["checks"]["decision_coverage"]["status"] == GovernanceStatus.PASS
        assert result["checks"]["decision_coverage"]["coverage"] == "100%"


# ═══════════════════════════════════════════════════════════════
# TEST: MISSING TICKET RETURNS FAIL
# ═══════════════════════════════════════════════════════════════

class TestMissingTicket:
    def test_missing_ticket_fails_identity(self, perfect_dataset, tmp_path):
        # Modify research file to have a trade with missing ticket
        research_file = Path(perfect_dataset["research_file"])
        trades = [json.loads(l) for l in research_file.read_text(encoding="utf-8").splitlines()]
        trades[0]["position_ticket"] = None  # Remove ticket
        research_file.write_text("\n".join(json.dumps(t) for t in trades), encoding="utf-8")

        v = DataGovernanceValidator(**perfect_dataset)
        result = v.validate()
        assert result["checks"]["identity_validation"]["status"] == GovernanceStatus.FAIL
        assert result["checks"]["identity_validation"]["missing_ticket_count"] == 1


# ═══════════════════════════════════════════════════════════════
# TEST: PNL MISMATCH RETURNS WARNING
# ═══════════════════════════════════════════════════════════════

class TestPnlMismatch:
    def test_small_pnl_mismatch_warns(self, perfect_dataset, tmp_path):
        # Modify recon to have different PnL (>5% but <20%)
        recon_file = Path(perfect_dataset["recon_file"])
        recon = json.loads(recon_file.read_text(encoding="utf-8"))
        # Total research PnL = 5 * -0.5 = -2.5
        # Make MT5 PnL = -2.8 (12% difference)
        recon["entries"][0]["broker_net_profit"] = -0.8
        recon_file.write_text(json.dumps(recon), encoding="utf-8")

        v = DataGovernanceValidator(**perfect_dataset)
        result = v.validate()
        assert result["checks"]["pnl_reconciliation"]["status"] == GovernanceStatus.WARNING


# ═══════════════════════════════════════════════════════════════
# TEST: MISSING STRATEGY METADATA RETURNS WARNING
# ═══════════════════════════════════════════════════════════════

class TestMissingMetadata:
    def test_missing_desired_fields_warns(self, perfect_dataset):
        # Clear strategy/score/regime/pattern on all trades to trigger desired field warning
        research_file = Path(perfect_dataset["research_file"])
        trades = [json.loads(l) for l in research_file.read_text(encoding="utf-8").splitlines()]
        for t in trades:
            t["strategy"] = ""
            t["score"] = 0
            t["regime"] = ""
            t["pattern"] = ""
        research_file.write_text("\n".join(json.dumps(t) for t in trades), encoding="utf-8")

        v = DataGovernanceValidator(**perfect_dataset)
        result = v.validate()
        fields_check = result["checks"]["field_completeness"]
        assert fields_check["status"] == GovernanceStatus.WARNING
        assert "strategy" in fields_check["missing_desired"]

    def test_missing_critical_field_fails(self, perfect_dataset):
        # Remove symbol from a trade
        research_file = Path(perfect_dataset["research_file"])
        trades = [json.loads(l) for l in research_file.read_text(encoding="utf-8").splitlines()]
        trades[0]["symbol"] = ""
        research_file.write_text("\n".join(json.dumps(t) for t in trades), encoding="utf-8")

        v = DataGovernanceValidator(**perfect_dataset)
        result = v.validate()
        assert result["checks"]["field_completeness"]["status"] == GovernanceStatus.FAIL


# ═══════════════════════════════════════════════════════════════
# TEST: MISSING DECISION TRACES REDUCES COVERAGE
# ═══════════════════════════════════════════════════════════════

class TestDecisionCoverage:
    def test_no_traces_fails(self, perfect_dataset, tmp_path):
        # Remove all decision trace files
        dt_dir = Path(perfect_dataset["decision_trace_dir"])
        import shutil
        shutil.rmtree(dt_dir)
        dt_dir.mkdir()

        v = DataGovernanceValidator(**perfect_dataset)
        result = v.validate()
        assert result["checks"]["decision_coverage"]["status"] == GovernanceStatus.FAIL
        assert result["checks"]["decision_coverage"]["coverage_pct"] == 0.0

    def test_partial_traces_warns(self, perfect_dataset):
        # Keep 3 of 5 traces (60% coverage: below 80% warn, above 50% fail)
        dt_file = Path(perfect_dataset["decision_trace_dir"]) / "EURUSD" / "2026-07-22.jsonl"
        lines = dt_file.read_text(encoding="utf-8").splitlines()
        dt_file.write_text("\n".join(lines[:3]), encoding="utf-8")

        # Shift unmatched trades far away so entity_id won't match
        research_file = Path(perfect_dataset["research_file"])
        trades = [json.loads(l) for l in research_file.read_text(encoding="utf-8").splitlines()]
        for i in range(3, 5):
            trades[i]["entry_time"] = 1800000000.0 + i * 1000
        research_file.write_text("\n".join(json.dumps(t) for t in trades), encoding="utf-8")

        v = DataGovernanceValidator(**perfect_dataset)
        result = v.validate()
        assert result["checks"]["decision_coverage"]["status"] == GovernanceStatus.WARNING


# ═══════════════════════════════════════════════════════════════
# TEST: REPORT GENERATION
# ═══════════════════════════════════════════════════════════════

class TestReportGeneration:
    def test_reports_created(self, perfect_dataset):
        v = DataGovernanceValidator(**perfect_dataset)
        v.validate()
        reports_dir = Path(perfect_dataset["reports_dir"])
        assert (reports_dir / "database_health_report.json").exists()
        assert (reports_dir / "database_health_report.md").exists()

    def test_json_report_valid(self, perfect_dataset):
        v = DataGovernanceValidator(**perfect_dataset)
        v.validate()
        reports_dir = Path(perfect_dataset["reports_dir"])
        data = json.loads((reports_dir / "database_health_report.json").read_text(encoding="utf-8"))
        assert "data_trust" in data
        assert "checks" in data
        assert "timestamp" in data


# ═══════════════════════════════════════════════════════════════
# TEST: LIVE DATA (integration — skipped if data missing)
# ═══════════════════════════════════════════════════════════════

class TestLiveData:
    def test_live_validation_does_not_crash(self):
        """Run governance on actual project data — should not crash."""
        src = Path("logs/research_ready_trade_dataset/research_ready_trades.jsonl")
        if not src.exists():
            pytest.skip("Live research dataset not available")
        v = DataGovernanceValidator()
        result = v.validate()
        assert result["data_trust"] in (GovernanceStatus.PASS, GovernanceStatus.WARNING, GovernanceStatus.FAIL)
        assert "checks" in result
        assert len(result["checks"]) == 5
