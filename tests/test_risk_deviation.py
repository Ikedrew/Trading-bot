"""
Tests for risk deviation tracking.

Covers:
    Case 1: Trade loses exactly planned risk → risk_deviation ≈ 1.0
    Case 2: Trade loses more than planned risk → deviation shows excessive loss
    Case 3: Winning trade → actual_risk_R positive, deviation calculated correctly
    Case 4: Missing SL/risk information → safe handling without breaking persistence
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.risk_deviation import (
    compute_risk_deviation,
    persist_risk_deviation,
    RiskDeviationResult,
    RiskClassification,
    NORMAL_DEVIATION_MAX,
    ELEVATED_DEVIATION_MAX,
)


# ═══════════════════════════════════════════════════════════════════════════════
# CASE 1: Trade loses exactly planned risk (deviation ≈ 1.0)
# ═══════════════════════════════════════════════════════════════════════════════

class TestNormalLoss:
    """Trade loses exactly the intended 1R → deviation is 1.0 (NORMAL)."""

    def test_buy_exact_sl_hit(self):
        """BUY trade exits exactly at SL → actual_risk_R = -1.0, deviation = 1.0."""
        result = compute_risk_deviation(
            trade_id="pos_001",
            symbol="EURUSD",
            correlation_id="COR-TEST-001",
            direction="BUY",
            entry_price=1.10000,
            exit_price=1.09950,    # Lost exactly 50 pips
            initial_sl=1.09950,    # SL was 50 pips below entry
        )

        assert result.planned_risk_R == -1.0
        assert result.actual_risk_R == -1.0
        assert result.risk_deviation == 1.0
        assert result.risk_classification == RiskClassification.NORMAL

    def test_sell_exact_sl_hit(self):
        """SELL trade exits exactly at SL → actual_risk_R = -1.0, deviation = 1.0."""
        result = compute_risk_deviation(
            trade_id="pos_002",
            symbol="GBPUSD",
            correlation_id="COR-TEST-002",
            direction="SELL",
            entry_price=1.33700,
            exit_price=1.33732,    # Lost exactly 32 pips (SL hit)
            initial_sl=1.33732,    # SL was 32 pips above entry
        )

        assert result.actual_risk_R == -1.0
        assert result.risk_deviation == 1.0
        assert result.risk_classification == RiskClassification.NORMAL

    def test_loss_with_minor_slippage(self):
        """Trade loses slightly more than 1R due to slippage → still NORMAL."""
        result = compute_risk_deviation(
            trade_id="pos_003",
            symbol="EURUSD",
            correlation_id="COR-TEST-003",
            direction="BUY",
            entry_price=1.10000,
            exit_price=1.09940,    # 60 pips lost (10 pips slippage past SL)
            initial_sl=1.09950,    # SL at 50 pips → risk = 50 pips
        )

        # 60/50 = 1.2R loss → deviation = 1.2 (within NORMAL threshold of 1.5)
        assert result.actual_risk_R == -1.2
        assert result.risk_deviation == 1.2
        assert result.risk_classification == RiskClassification.NORMAL

    def test_partial_loss_less_than_1r(self):
        """Trade exits before hitting SL → loss < 1R, deviation < 1.0."""
        result = compute_risk_deviation(
            trade_id="pos_004",
            symbol="USDCHF",
            correlation_id="COR-TEST-004",
            direction="SELL",
            entry_price=0.81500,
            exit_price=0.81515,    # Lost 15 pips
            initial_sl=0.81530,    # SL at 30 pips → risk = 30 pips
        )

        # 15/30 = 0.5R loss → deviation = 0.5
        assert result.actual_risk_R == -0.5
        assert result.risk_deviation == 0.5
        assert result.risk_classification == RiskClassification.NORMAL


# ═══════════════════════════════════════════════════════════════════════════════
# CASE 2: Trade loses more than planned risk (execution/protection failure)
# ═══════════════════════════════════════════════════════════════════════════════

class TestExcessiveLoss:
    """Trade loses significantly more than 1R → indicates protection failure."""

    def test_gbpusd_45r_loss_critical(self):
        """GBPUSD -4.5R anomaly from checkpoint → CRITICAL classification."""
        result = compute_risk_deviation(
            trade_id="pos_53303078",
            symbol="GBPUSD",
            correlation_id="COR-20260722-1-GBPUSD-F014",
            direction="SELL",
            entry_price=1.33743,
            exit_price=1.33887,    # 144 pips adverse
            initial_sl=1.33775,    # SL was 32 pips away
        )

        # 144/32 = 4.5R loss → CRITICAL
        assert result.actual_risk_R == pytest.approx(-4.5, abs=0.1)
        assert result.risk_deviation == pytest.approx(4.5, abs=0.1)
        assert result.risk_classification == RiskClassification.CRITICAL

    def test_elevated_2r_loss(self):
        """Trade loses 2R (SL slipped or moved) → ELEVATED classification."""
        result = compute_risk_deviation(
            trade_id="pos_005",
            symbol="AUDUSD",
            correlation_id="COR-TEST-005",
            direction="BUY",
            entry_price=0.70000,
            exit_price=0.69900,    # 100 pips lost
            initial_sl=0.69950,    # SL at 50 pips → risk = 50 pips
        )

        # 100/50 = 2.0R loss → ELEVATED (between 1.5 and 3.0)
        assert result.actual_risk_R == -2.0
        assert result.risk_deviation == 2.0
        assert result.risk_classification == RiskClassification.ELEVATED

    def test_exactly_at_elevated_boundary(self):
        """Loss at exactly 1.5R → still NORMAL (≤ threshold)."""
        result = compute_risk_deviation(
            trade_id="pos_006",
            symbol="EURUSD",
            correlation_id="COR-TEST-006",
            direction="BUY",
            entry_price=1.10000,
            exit_price=1.09925,    # 75 pips lost
            initial_sl=1.09950,    # SL at 50 pips
        )

        # 75/50 = 1.5R → at boundary → NORMAL (≤ 1.5)
        assert result.actual_risk_R == -1.5
        assert result.risk_deviation == 1.5
        assert result.risk_classification == RiskClassification.NORMAL

    def test_just_above_elevated_boundary(self):
        """Loss at 1.51R → ELEVATED."""
        result = compute_risk_deviation(
            trade_id="pos_007",
            symbol="EURUSD",
            correlation_id="COR-TEST-007",
            direction="SELL",
            entry_price=1.10000,
            exit_price=1.10151,    # 151 pips lost
            initial_sl=1.10100,    # SL at 100 pips
        )

        # 151/100 = 1.51R → ELEVATED
        assert result.risk_deviation == pytest.approx(1.51, abs=0.01)
        assert result.risk_classification == RiskClassification.ELEVATED

    def test_critical_boundary(self):
        """Loss at 3.01R → CRITICAL."""
        result = compute_risk_deviation(
            trade_id="pos_008",
            symbol="NZDUSD",
            correlation_id="COR-TEST-008",
            direction="BUY",
            entry_price=0.58000,
            exit_price=0.57699,    # 301 pip move
            initial_sl=0.57900,    # 100 pip risk
        )

        # 301/100 = 3.01R → CRITICAL
        assert result.risk_deviation == pytest.approx(3.01, abs=0.01)
        assert result.risk_classification == RiskClassification.CRITICAL


# ═══════════════════════════════════════════════════════════════════════════════
# CASE 3: Winning trade → actual_risk_R positive, deviation correct
# ═══════════════════════════════════════════════════════════════════════════════

class TestWinningTrade:
    """Winning trades have positive actual_risk_R and WIN classification."""

    def test_buy_win_2r(self):
        """BUY trade wins 2R → actual_risk_R = +2.0, classification = WIN."""
        result = compute_risk_deviation(
            trade_id="pos_009",
            symbol="EURUSD",
            correlation_id="COR-TEST-009",
            direction="BUY",
            entry_price=1.10000,
            exit_price=1.10100,    # Won 100 pips
            initial_sl=1.09950,    # Risk was 50 pips
        )

        assert result.actual_risk_R == 2.0
        assert result.risk_deviation == 2.0  # For wins, deviation = actual_risk_R
        assert result.risk_classification == RiskClassification.WIN

    def test_sell_win_1r(self):
        """SELL trade wins exactly 1R → actual_risk_R = +1.0."""
        result = compute_risk_deviation(
            trade_id="pos_010",
            symbol="USDCHF",
            correlation_id="COR-TEST-010",
            direction="SELL",
            entry_price=0.81477,
            exit_price=0.81455,    # Won 22 pips
            initial_sl=0.81499,    # Risk was 22 pips
        )

        assert result.actual_risk_R == 1.0
        assert result.risk_deviation == 1.0
        assert result.risk_classification == RiskClassification.WIN

    def test_breakeven_trade(self):
        """Trade exits at entry → actual_risk_R = 0.0, still WIN."""
        result = compute_risk_deviation(
            trade_id="pos_011",
            symbol="EURUSD",
            correlation_id="COR-TEST-011",
            direction="BUY",
            entry_price=1.10000,
            exit_price=1.10000,    # Breakeven
            initial_sl=1.09950,
        )

        assert result.actual_risk_R == 0.0
        assert result.risk_deviation == 0.0
        assert result.risk_classification == RiskClassification.WIN


# ═══════════════════════════════════════════════════════════════════════════════
# CASE 4: Missing SL/risk information → safe handling
# ═══════════════════════════════════════════════════════════════════════════════

class TestMissingData:
    """Safe handling when SL or price data is incomplete."""

    def test_zero_sl_returns_no_risk_data(self):
        """When initial_sl is 0.0, cannot compute risk → NO_RISK_DATA."""
        result = compute_risk_deviation(
            trade_id="pos_012",
            symbol="EURUSD",
            correlation_id="COR-TEST-012",
            direction="BUY",
            entry_price=1.10000,
            exit_price=1.09900,
            initial_sl=0.0,  # No SL information
        )

        assert result.risk_classification == RiskClassification.NO_RISK_DATA
        assert result.actual_risk_R == 0.0
        assert result.risk_deviation == 0.0
        assert result.risk_distance == 0.0

    def test_sl_equals_entry_returns_no_risk_data(self):
        """When SL equals entry price (zero risk distance) → NO_RISK_DATA."""
        result = compute_risk_deviation(
            trade_id="pos_013",
            symbol="GBPUSD",
            correlation_id="COR-TEST-013",
            direction="SELL",
            entry_price=1.33700,
            exit_price=1.33800,
            initial_sl=1.33700,  # SL at entry = zero risk
        )

        assert result.risk_classification == RiskClassification.NO_RISK_DATA
        assert result.risk_distance == 0.0

    def test_result_fields_populated_even_with_no_risk(self):
        """All identity/context fields are still populated when NO_RISK_DATA."""
        result = compute_risk_deviation(
            trade_id="pos_014",
            symbol="USDJPY",
            correlation_id="COR-RECOVERED-14",
            direction="BUY",
            entry_price=163.176,
            exit_price=163.165,
            initial_sl=0.0,
        )

        assert result.trade_id == "pos_014"
        assert result.symbol == "USDJPY"
        assert result.correlation_id == "COR-RECOVERED-14"
        assert result.direction == "BUY"
        assert result.entry_price == 163.176
        assert result.exit_price == 163.165
        assert result.planned_risk_R == -1.0
        assert result.timestamp_utc != ""


# ═══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE
# ═══════════════════════════════════════════════════════════════════════════════

class TestRiskDeviationPersistence:
    """Test JSONL persistence of risk deviation results."""

    def test_persist_writes_valid_jsonl(self, tmp_path):
        """Result is written to JSONL file with correct structure."""
        result = compute_risk_deviation(
            trade_id="pos_100",
            symbol="EURUSD",
            correlation_id="COR-TEST-100",
            direction="SELL",
            entry_price=1.10000,
            exit_price=1.10050,
            initial_sl=1.10050,  # Exact SL hit
        )

        with patch("core.risk_deviation._LOCAL_DIR", str(tmp_path / "risk_deviation")):
            persist_risk_deviation(result)

        # Verify file exists
        audit_dir = tmp_path / "risk_deviation" / "EURUSD"
        files = list(audit_dir.glob("*.jsonl"))
        assert len(files) == 1

        # Verify valid JSON with required fields
        with open(files[0]) as f:
            record = json.loads(f.read().strip())

        assert record["trade_id"] == "pos_100"
        assert record["symbol"] == "EURUSD"
        assert record["planned_risk_R"] == -1.0
        assert record["actual_risk_R"] == -1.0
        assert record["risk_deviation"] == 1.0
        assert record["risk_classification"] == "NORMAL"
        assert "timestamp_utc" in record

    def test_to_dict_serialization(self):
        """RiskDeviationResult.to_dict() returns all fields."""
        result = compute_risk_deviation(
            trade_id="pos_101",
            symbol="GBPUSD",
            correlation_id="COR-TEST-101",
            direction="BUY",
            entry_price=1.33000,
            exit_price=1.33100,
            initial_sl=1.32950,
        )

        d = result.to_dict()
        assert "trade_id" in d
        assert "planned_risk_R" in d
        assert "actual_risk_R" in d
        assert "risk_deviation" in d
        assert "risk_classification" in d
        assert "entry_price" in d
        assert "exit_price" in d
        assert "initial_sl" in d
        assert "risk_distance" in d
        assert "pnl_distance" in d
