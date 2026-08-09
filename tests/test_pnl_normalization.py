"""Tests for V10 PnL Normalisation Layer."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from research_engine.v10.pnl_normalization import (
    normalize_trade_pnl,
    normalize_dataset,
    get_canonical_pnl_totals,
    reconcile_pnl,
)


class TestGrossProfitOnly:
    def test_trade_with_no_costs(self):
        """Gross profit only — no commission, swap, or fees."""
        trade = {
            "position_ticket": 100,
            "symbol": "EURUSD",
            "broker_pnl": 50.0,
            "commission": 0.0,
            "swap": 0.0,
            "final_pnl": 50.0,
        }
        result = normalize_trade_pnl(trade, source="research")
        assert result["gross_profit"] == 50.0
        assert result["commission"] == 0.0
        assert result["swap"] == 0.0
        assert result["fees"] == 0.0
        assert result["net_realised_pnl"] == 50.0
        assert result["normalisation_status"] == "PASS"


class TestCommissionAdjustment:
    def test_commission_reduces_net(self):
        """Commission should reduce net_realised_pnl."""
        trade = {
            "position_ticket": 200,
            "symbol": "GBPUSD",
            "broker_pnl": 10.0,
            "commission": -2.0,
            "swap": 0.0,
            "final_pnl": 8.0,
        }
        result = normalize_trade_pnl(trade, source="research")
        assert result["gross_profit"] == 10.0
        assert result["commission"] == -2.0
        assert result["net_realised_pnl"] == 8.0
        assert result["normalisation_status"] == "PASS"


class TestSwapAdjustment:
    def test_swap_reduces_net(self):
        """Swap charges should reduce net_realised_pnl."""
        trade = {
            "position_ticket": 300,
            "symbol": "USDCHF",
            "broker_pnl": 5.0,
            "commission": -1.0,
            "swap": -0.5,
            "final_pnl": 3.5,
        }
        result = normalize_trade_pnl(trade, source="research")
        assert result["gross_profit"] == 5.0
        assert result["swap"] == -0.5
        assert result["net_realised_pnl"] == 3.5
        assert result["normalisation_status"] == "PASS"


class TestFullNetPnLCalculation:
    def test_all_components(self):
        """Full calculation: gross + commission + swap + fees."""
        trade = {
            "position_ticket": 400,
            "symbol": "US500",
            "broker_profit": 100.0,
            "broker_commission": -5.0,
            "broker_swap": -2.0,
            "broker_fee": -1.0,
            "broker_net_profit": 92.0,
        }
        result = normalize_trade_pnl(trade, source="recon")
        assert result["gross_profit"] == 100.0
        assert result["commission"] == -5.0
        assert result["swap"] == -2.0
        assert result["fees"] == -1.0
        assert result["net_realised_pnl"] == 92.0
        assert result["normalisation_status"] == "PASS"

    def test_losing_trade(self):
        """Losing trade with costs amplifies the loss."""
        trade = {
            "position_ticket": 500,
            "symbol": "XAUUSD",
            "broker_pnl": -50.0,
            "commission": -3.0,
            "swap": -1.0,
            "final_pnl": -54.0,
        }
        result = normalize_trade_pnl(trade, source="research")
        assert result["net_realised_pnl"] == -54.0
        assert result["normalisation_status"] == "PASS"


class TestMT5AndResearchReconciliation:
    def test_matching_totals_pass(self):
        """Same population, same net = PASS."""
        research = [
            {"position_ticket": 1, "broker_pnl": 10, "commission": -1, "swap": 0, "final_pnl": 9, "symbol": "X"},
            {"position_ticket": 2, "broker_pnl": -5, "commission": -1, "swap": -0.5, "final_pnl": -6.5, "symbol": "X"},
        ]
        recon = [
            {"position_ticket": 1, "broker_net_profit": 9.0},
            {"position_ticket": 2, "broker_net_profit": -6.5},
        ]
        totals = get_canonical_pnl_totals(research, recon)
        assert totals["mt5_net"] == 2.5
        assert totals["research_net"] == 2.5
        assert totals["diff_abs"] == 0.0
        assert totals["diff_pct"] == 0.0

    def test_mismatched_totals(self):
        """Different nets should show difference."""
        research = [
            {"position_ticket": 1, "broker_pnl": 10, "commission": 0, "swap": 0, "final_pnl": 10, "symbol": "X"},
        ]
        recon = [
            {"position_ticket": 1, "broker_net_profit": 8.0},  # Mismatch
        ]
        totals = get_canonical_pnl_totals(research, recon)
        assert totals["mt5_net"] == 8.0
        assert totals["research_net"] == 10.0
        assert totals["diff_abs"] == 2.0
        assert totals["diff_pct"] == 25.0


class TestMissingPnLFields:
    def test_all_zero_warns(self):
        """Trade with all PnL components zero should warn."""
        trade = {
            "position_ticket": 600,
            "symbol": "NZDUSD",
            "broker_pnl": 0,
            "commission": 0,
            "swap": 0,
            "final_pnl": 0,
        }
        result = normalize_trade_pnl(trade, source="research")
        assert result["normalisation_status"] == "WARNING"
        assert any("zero" in i.lower() for i in result["issues"])

    def test_missing_final_pnl_still_calculates(self):
        """If final_pnl is missing, net is still computed from components."""
        trade = {
            "position_ticket": 700,
            "symbol": "USDCAD",
            "broker_pnl": 5.0,
            "commission": -0.5,
            "swap": 0.0,
        }
        result = normalize_trade_pnl(trade, source="research")
        assert result["net_realised_pnl"] == 4.5
        # No expected_net to compare against, so PASS
        assert result["normalisation_status"] == "PASS"


class TestBrokerSignConvention:
    def test_negative_commission_is_cost(self):
        """Broker reports commission as negative = cost to trader."""
        trade = {
            "position_ticket": 800,
            "symbol": "EURUSD",
            "broker_profit": 20.0,
            "broker_commission": -4.0,
            "broker_swap": 0.0,
            "broker_fee": 0.0,
            "broker_net_profit": 16.0,
        }
        result = normalize_trade_pnl(trade, source="recon")
        assert result["net_realised_pnl"] == 16.0
        assert result["commission"] == -4.0

    def test_component_mismatch_detected(self):
        """If components don't add up to expected net, flag it."""
        trade = {
            "position_ticket": 900,
            "symbol": "USDJPY",
            "broker_profit": 10.0,
            "broker_commission": -2.0,
            "broker_swap": 0.0,
            "broker_fee": 0.0,
            "broker_net_profit": 5.0,  # Should be 8.0, not 5.0
        }
        result = normalize_trade_pnl(trade, source="recon")
        assert result["net_realised_pnl"] == 8.0  # Computed correctly
        assert result["normalisation_status"] in ("WARNING", "FAIL")
        assert any("Component sum" in i for i in result["issues"])


class TestLiveReconciliation:
    def test_live_reconciliation_passes(self):
        """Run against actual project data — should PASS now."""
        src = Path("logs/research_ready_trade_dataset/research_ready_trades.jsonl")
        recon = Path("reports/research/mt5_reconciliation_report.json")
        if not src.exists() or not recon.exists():
            pytest.skip("Live data not available")
        result = reconcile_pnl(
            research_file=str(src),
            recon_file=str(recon),
            reports_dir=str(Path("reports/research")),
        )
        assert result["status"] == "PASS"
        assert result["after"]["difference_pct"] < 5.0
