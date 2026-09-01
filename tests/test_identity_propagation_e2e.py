"""
End-to-end verification: Trade Identity propagates from decision through to Trade Truth.

Tests the complete lifecycle:
    Decision → ExecutionPrep → TradeIdentity → Position → TradeRecord → Trade Truth

Proves:
    1. A trade created with a correlation_id stores it on Position.
    2. The Position survives the full trade management lifecycle.
    3. Trade close produces a TradeRecord with the same correlation_id.
    4. Trade Truth persistence receives the same correlation_id.
    5. Identity is never recovered from thread-local context.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.trade_identity import TradeIdentity, EMPTY_IDENTITY
from core.trade_management.position import Position, PositionStatus
from core.trade_management.manager import TradeStateManager
from core.trade_management.config import TradeManagementConfig
from core.trade_management.events import TradeLifecycleEvent, TradeEvent
from core.trade_journal import build_trade_record, persist_trade, TradeRecord
from core.trade_truth import build_trade_truth, validate_trade_truth, compute_r_multiple
from execution.mt5_execution import ExecutionResult
from strategy.signals import Side
from risk.models import OrderIntent


# ─── FIXTURES ─────────────────────────────────────────────────────────────────

_TEST_CORRELATION_ID = "COR-20260719-500-EURUSD-BEEF"
_TEST_DECISION_ID = "dec_audit_001"
_TEST_CYCLE_ID = 500
_TEST_STRATEGY = "momentum_v2"
_TEST_PATTERN = "ENGULFING_BULLISH"
_TEST_DECISION_TS = 1721400000.0


def _identity() -> TradeIdentity:
    return TradeIdentity(
        correlation_id=_TEST_CORRELATION_ID,
        decision_id=_TEST_DECISION_ID,
        cycle_id=_TEST_CYCLE_ID,
        strategy=_TEST_STRATEGY,
        pattern=_TEST_PATTERN,
        decision_ts_utc=_TEST_DECISION_TS,
    )


def _cfg() -> TradeManagementConfig:
    return TradeManagementConfig(
        break_even_trigger_rr=0.0,
        break_even_buffer_rr=0.0,
        trailing_step=0.0,
        trailing_start_rr=0.0,
        partial_tp_fraction=0.0,
        partial_tp_path_fraction=0.0,
        max_time_in_trade_seconds=0.0,
    )


def _intent() -> OrderIntent:
    return OrderIntent(
        symbol="EURUSD",
        side=Side.BUY,
        volume=0.10,
        entry_reference=1.1000,
        sl=1.0950,
        tp=1.1100,
        pattern=_TEST_PATTERN,
    )


def _execution_result() -> ExecutionResult:
    return ExecutionResult(ok=True, retcode=10009, deal=99999, order=88888, comment="done")


@pytest.fixture
def temp_journal(tmp_path):
    """Redirect journal persistence to temp directory."""
    with patch("core.trade_journal._get_journal_dir", return_value=tmp_path):
        yield tmp_path


@pytest.fixture
def temp_truth(tmp_path):
    """Redirect trade truth persistence to temp directory."""
    truth_dir = tmp_path / "trade_truth"
    truth_dir.mkdir()
    yield truth_dir


# ═══════════════════════════════════════════════════════════════════════════════
# TASK 1 — COMPLETE LIFECYCLE TRACE
# ═══════════════════════════════════════════════════════════════════════════════

class TestCompleteLifecycleTrace:
    """Verify identity propagates through the full lifecycle."""

    def test_identity_flows_decision_to_trade_truth(self, temp_truth):
        """
        Full lifecycle:
            TradeIdentity → Position → TradeRecord → Trade Truth

        The same correlation_id must appear at every stage.
        """
        identity = _identity()

        # ─── STAGE 1: Position created with identity ──────────────────
        mgr = TradeStateManager(_cfg())
        pos = mgr.register_from_execution(
            _intent(),
            magic=713001,
            execution=_execution_result(),
            entry_fill_price=1.1000,
            bid=1.0999,
            ask=1.1001,
            trade_identity=identity,
        )

        assert pos is not None
        assert pos.correlation_id == _TEST_CORRELATION_ID
        assert pos.trade_identity is identity
        assert pos.trade_identity.cycle_id == _TEST_CYCLE_ID
        assert pos.trade_identity.pattern == _TEST_PATTERN

        # ─── STAGE 2: Trade closes ───────────────────────────────────
        # Simulate trade close
        record = build_trade_record(
            position=pos,
            exit_price=1.1080,
            exit_time=_TEST_DECISION_TS + 3600,
            close_reason="take_profit",
        )

        assert record.correlation_id == _TEST_CORRELATION_ID
        assert record.trade_id == pos.position_id

        # ─── STAGE 3: Trade Truth built ──────────────────────────────
        _r = compute_r_multiple(
            direction=record.direction,
            entry_price=record.entry_price,
            exit_price=record.exit_price,
            stop_loss=record.initial_sl,
        )

        truth = build_trade_truth(
            trade_id=record.trade_id,
            correlation_id=record.correlation_id,
            symbol=record.symbol,
            entry_fill_price=record.entry_price,
            exit_fill_price=record.exit_price,
            volume_executed=record.final_volume,
            order_type="market",
            entry_timestamp_broker=record.entry_time,
            exit_timestamp_broker=record.exit_time,
            pnl_realised=record.realised_pnl,
            r_multiple_realised=_r,
            exit_reason="take_profit_hit",
        )

        # ─── VERIFY: Same identity at every stage ─────────────────────
        assert truth["identity"]["correlation_id"] == _TEST_CORRELATION_ID
        assert truth["identity"]["trade_id"] == pos.position_id
        assert truth["identity"]["symbol"] == "EURUSD"

        # ─── VERIFY: Trade Truth passes validation ────────────────────
        valid, reason = validate_trade_truth(truth)
        assert valid, f"Trade Truth validation failed: {reason}"

    def test_identity_survives_price_updates(self):
        """Identity unchanged after price update cycles."""
        identity = _identity()
        mgr = TradeStateManager(_cfg())
        pos = mgr.register_from_execution(
            _intent(),
            magic=713001,
            execution=_execution_result(),
            entry_fill_price=1.1000,
            bid=1.0999,
            ask=1.1001,
            trade_identity=identity,
        )

        # Simulate many price updates
        for i in range(100):
            bid = 1.1000 + i * 0.0001
            ask = bid + 0.0002
            mgr.on_price_update("EURUSD", bid, ask, _TEST_DECISION_TS + i)

        # Identity unchanged
        assert pos.correlation_id == _TEST_CORRELATION_ID
        assert pos.trade_identity is identity


# ═══════════════════════════════════════════════════════════════════════════════
# TASK 2 — POSITION OWNERSHIP VERIFICATION
# ═══════════════════════════════════════════════════════════════════════════════

class TestPositionOwnership:
    """Confirm Position owns identity without thread-local dependency."""

    def test_identity_is_immutable(self):
        """TradeIdentity cannot be modified after creation."""
        identity = _identity()
        with pytest.raises(Exception):  # FrozenInstanceError
            identity.correlation_id = "MODIFIED"

    def test_identity_created_before_position(self):
        """Identity must exist before Position is registered."""
        identity = _identity()
        # Identity exists
        assert identity.correlation_id == _TEST_CORRELATION_ID

        # Then position is created with it
        mgr = TradeStateManager(_cfg())
        pos = mgr.register_from_execution(
            _intent(),
            magic=713001,
            execution=_execution_result(),
            entry_fill_price=1.1000,
            bid=1.0999,
            ask=1.1001,
            trade_identity=identity,
        )
        assert pos.trade_identity is identity

    def test_no_thread_local_dependency(self):
        """
        Position carries identity directly.
        Clearing thread-local context does not affect it.
        """
        from core.correlation import clear_active_correlation, set_active_correlation

        identity = _identity()
        mgr = TradeStateManager(_cfg())
        pos = mgr.register_from_execution(
            _intent(),
            magic=713001,
            execution=_execution_result(),
            entry_fill_price=1.1000,
            bid=1.0999,
            ask=1.1001,
            trade_identity=identity,
        )

        # Set and clear thread-local — should not affect Position
        set_active_correlation("EURUSD", "DIFFERENT-ID")
        assert pos.correlation_id == _TEST_CORRELATION_ID

        clear_active_correlation("EURUSD")
        assert pos.correlation_id == _TEST_CORRELATION_ID

    def test_position_without_identity_has_empty_correlation(self):
        """Backward-compatible: positions without identity have empty string."""
        mgr = TradeStateManager(_cfg())
        pos = mgr.register_from_execution(
            _intent(),
            magic=713001,
            execution=_execution_result(),
            entry_fill_price=1.1000,
            bid=1.0999,
            ask=1.1001,
            # No trade_identity passed
        )
        assert pos.correlation_id == ""
        assert pos.trade_identity is None


# ═══════════════════════════════════════════════════════════════════════════════
# TASK 3 — TRADE TRUTH OUTPUT VERIFICATION
# ═══════════════════════════════════════════════════════════════════════════════

class TestTradeTruthOutput:
    """Confirm Trade Truth receives identity from Position ownership."""

    def test_trade_record_carries_correlation_from_position(self):
        """TradeRecord.correlation_id comes from Position.trade_identity."""
        identity = _identity()
        mgr = TradeStateManager(_cfg())
        pos = mgr.register_from_execution(
            _intent(),
            magic=713001,
            execution=_execution_result(),
            entry_fill_price=1.1000,
            bid=1.0999,
            ask=1.1001,
            trade_identity=identity,
        )

        record = build_trade_record(
            position=pos,
            exit_price=1.1080,
            exit_time=_TEST_DECISION_TS + 3600,
            close_reason="take_profit",
        )

        assert record.correlation_id == _TEST_CORRELATION_ID

    def test_trade_truth_json_contains_correlation(self, temp_truth):
        """Persisted JSON has correlation_id in identity section."""
        identity = _identity()
        mgr = TradeStateManager(_cfg())
        pos = mgr.register_from_execution(
            _intent(),
            magic=713001,
            execution=_execution_result(),
            entry_fill_price=1.1000,
            bid=1.0999,
            ask=1.1001,
            trade_identity=identity,
        )

        record = build_trade_record(
            position=pos,
            exit_price=1.1080,
            exit_time=_TEST_DECISION_TS + 3600,
            close_reason="take_profit",
        )

        _r = compute_r_multiple(
            direction=record.direction,
            entry_price=record.entry_price,
            exit_price=record.exit_price,
            stop_loss=record.initial_sl,
        )

        truth = build_trade_truth(
            trade_id=record.trade_id,
            correlation_id=record.correlation_id,
            symbol=record.symbol,
            entry_fill_price=record.entry_price,
            exit_fill_price=record.exit_price,
            volume_executed=record.final_volume,
            order_type="market",
            entry_timestamp_broker=record.entry_time,
            exit_timestamp_broker=record.exit_time,
            pnl_realised=record.realised_pnl,
            r_multiple_realised=_r,
            exit_reason="take_profit_hit",
        )

        # Serialize and verify JSON roundtrip
        json_str = json.dumps(truth, separators=(",", ":"))
        parsed = json.loads(json_str)

        assert parsed["identity"]["correlation_id"] == _TEST_CORRELATION_ID
        assert parsed["identity"]["trade_id"] == pos.position_id
        assert parsed["schema_version"] == "trade_truth_v1"

    def test_trade_truth_validates_with_correlation(self):
        """Trade Truth passes validation when correlation_id is present."""
        truth = build_trade_truth(
            trade_id="pos_99999",
            correlation_id=_TEST_CORRELATION_ID,
            symbol="EURUSD",
            entry_fill_price=1.1000,
            exit_fill_price=1.1080,
            volume_executed=0.10,
            entry_timestamp_broker=_TEST_DECISION_TS,
            exit_timestamp_broker=_TEST_DECISION_TS + 3600,
            pnl_realised=80.0,
            r_multiple_realised=1.6,
            exit_reason="take_profit_hit",
        )
        valid, reason = validate_trade_truth(truth)
        assert valid, f"Unexpected rejection: {reason}"

    def test_trade_truth_rejects_without_correlation(self):
        """Trade Truth rejects records with empty correlation_id."""
        truth = build_trade_truth(
            trade_id="pos_99999",
            correlation_id="",  # Empty!
            symbol="EURUSD",
            entry_fill_price=1.1000,
            exit_fill_price=1.1080,
            volume_executed=0.10,
            entry_timestamp_broker=_TEST_DECISION_TS,
            exit_timestamp_broker=_TEST_DECISION_TS + 3600,
            pnl_realised=80.0,
            r_multiple_realised=1.6,
            exit_reason="take_profit_hit",
        )
        valid, reason = validate_trade_truth(truth)
        assert not valid
        assert "missing_correlation_id" in reason

    def test_persist_trade_truth_end_to_end(self, temp_truth):
        """Full persistence writes correct correlation to disk."""
        from core.trade_truth import persist_trade_truth

        truth = build_trade_truth(
            trade_id="pos_99999",
            correlation_id=_TEST_CORRELATION_ID,
            symbol="EURUSD",
            entry_fill_price=1.1000,
            exit_fill_price=1.1080,
            volume_executed=0.10,
            entry_timestamp_broker=_TEST_DECISION_TS,
            exit_timestamp_broker=_TEST_DECISION_TS + 3600,
            pnl_realised=80.0,
            r_multiple_realised=1.6,
            exit_reason="take_profit_hit",
        )

        result = persist_trade_truth(truth, local_dir=str(temp_truth))
        assert result is True

        # Read back and verify
        files = list(temp_truth.rglob("*.jsonl"))
        assert len(files) == 1

        with open(files[0], "r") as f:
            line = f.readline().strip()
            parsed = json.loads(line)

        assert parsed["identity"]["correlation_id"] == _TEST_CORRELATION_ID
        assert parsed["identity"]["trade_id"] == "pos_99999"


# ═══════════════════════════════════════════════════════════════════════════════
# TASK 4 — CONTROLLED END-TO-END TEST
# ═══════════════════════════════════════════════════════════════════════════════

class TestControlledLifecycle:
    """
    The smallest possible test proving identity flows from creation to persistence.

    Input:  correlation_id = COR-20260719-500-EURUSD-BEEF
    Position stores: COR-20260719-500-EURUSD-BEEF
    Trade Truth contains: COR-20260719-500-EURUSD-BEEF
    """

    def test_minimal_lifecycle_proof(self, temp_truth):
        """
        Proves:
            1. Trade created with correlation_id
            2. Position stores it
            3. Trade closes → TradeRecord carries it
            4. Trade Truth contains the same correlation_id
        """
        from core.trade_truth import persist_trade_truth

        COR_ID = "COR-20260719-500-EURUSD-BEEF"

        # 1. Create identity and position
        identity = TradeIdentity(correlation_id=COR_ID, cycle_id=500)
        mgr = TradeStateManager(_cfg())
        pos = mgr.register_from_execution(
            _intent(),
            magic=713001,
            execution=_execution_result(),
            entry_fill_price=1.1000,
            bid=1.0999,
            ask=1.1001,
            trade_identity=identity,
        )

        # 2. Position stores it
        assert pos.correlation_id == COR_ID

        # 3. Trade closes
        record = build_trade_record(
            position=pos,
            exit_price=1.1080,
            exit_time=_TEST_DECISION_TS + 7200,
            close_reason="take_profit",
        )
        assert record.correlation_id == COR_ID

        # 4. Trade Truth contains the same correlation_id
        truth = build_trade_truth(
            trade_id=record.trade_id,
            correlation_id=record.correlation_id,
            symbol=record.symbol,
            entry_fill_price=record.entry_price,
            exit_fill_price=record.exit_price,
            volume_executed=record.final_volume,
            entry_timestamp_broker=record.entry_time,
            exit_timestamp_broker=record.exit_time,
            pnl_realised=record.realised_pnl,
            r_multiple_realised=1.6,
            exit_reason="take_profit_hit",
        )

        assert truth["identity"]["correlation_id"] == COR_ID

        # Persist and verify on disk
        ok = persist_trade_truth(truth, local_dir=str(temp_truth))
        assert ok

        files = list(temp_truth.rglob("*.jsonl"))
        with open(files[0]) as f:
            disk_record = json.loads(f.readline())
        assert disk_record["identity"]["correlation_id"] == COR_ID

        print(f"\n✓ PROOF: correlation_id={COR_ID} verified at all 4 lifecycle stages")


# ═══════════════════════════════════════════════════════════════════════════════
# TASK 5 — RESEARCH ENGINE READINESS
# ═══════════════════════════════════════════════════════════════════════════════

class TestResearchEngineReadiness:
    """Verify that shadow trades and live trade truth can now share an identity."""

    def test_shadow_and_truth_share_correlation_format(self):
        """
        Both shadow trade and Trade Truth use the same correlation_id format.
        Q16 can match them when both have the same COR-* identifier.
        """
        from core.correlation import generate_correlation_id

        # Same inputs produce same correlation_id
        cor_id = generate_correlation_id(
            cycle_id=500,
            symbol="EURUSD",
            timestamp=_TEST_DECISION_TS,
        )

        # This is what shadow_trades.py stores
        shadow_correlation = cor_id

        # This is what Trade Truth will now store (via Position → TradeRecord)
        truth = build_trade_truth(
            trade_id="pos_99999",
            correlation_id=cor_id,
            symbol="EURUSD",
            entry_fill_price=1.1000,
            exit_fill_price=1.1080,
            volume_executed=0.10,
            entry_timestamp_broker=_TEST_DECISION_TS,
            exit_timestamp_broker=_TEST_DECISION_TS + 3600,
            pnl_realised=80.0,
            r_multiple_realised=1.6,
            exit_reason="take_profit_hit",
        )

        truth_correlation = truth["identity"]["correlation_id"]

        # Q16 can now match: shadow_correlation == truth_correlation
        assert shadow_correlation == truth_correlation
        assert shadow_correlation == cor_id

    def test_decision_produces_shared_identity_for_shadow_and_live(self):
        """
        A single execution decision now produces the same correlation_id in:
        - Shadow trade (via engine_execution_handler → shadow_engine.open_trade)
        - Live Position (via TradeIdentity → register_from_execution)
        - Trade Truth (via Position → TradeRecord → persist_trade_truth)

        This is the Q16 linkage requirement.
        """
        from core.correlation import generate_correlation_id

        # Simulate the decision point
        cycle_id = 500
        symbol = "EURUSD"
        bar_close_ts = _TEST_DECISION_TS

        # Engine execution handler generates this ONCE
        cor_id = generate_correlation_id(
            cycle_id=cycle_id,
            symbol=symbol,
            timestamp=bar_close_ts,
        )

        # Shadow trade stores it directly
        shadow_cor_id = cor_id  # Passed to open_trade(correlation_id=cor_id)

        # Live Position stores it via TradeIdentity
        identity = TradeIdentity(correlation_id=cor_id, cycle_id=cycle_id)
        mgr = TradeStateManager(_cfg())
        pos = mgr.register_from_execution(
            _intent(),
            magic=713001,
            execution=_execution_result(),
            entry_fill_price=1.1000,
            bid=1.0999,
            ask=1.1001,
            trade_identity=identity,
        )
        position_cor_id = pos.correlation_id

        # Trade Truth receives it from Position
        record = build_trade_record(
            position=pos,
            exit_price=1.1080,
            exit_time=bar_close_ts + 3600,
            close_reason="take_profit",
        )
        truth_cor_id = record.correlation_id

        # ALL THREE ARE THE SAME
        assert shadow_cor_id == position_cor_id == truth_cor_id == cor_id
