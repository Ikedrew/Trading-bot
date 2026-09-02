"""
Restart lineage-recovery regression test.

Proves that when the bot restarts and reconstructs an already-open broker
position, the ORIGINAL persisted V1 lineage is restored exactly and survives
end-to-end through close → trade journal → trade truth:

    OPEN → persistence(execution_results) → restart → recovery(Position)
         → CLOSE(build_trade_record) → journal(TradeRecord) → trade_truth

Governing rule: the canonical root (and the other lineage IDs where the schema
supports them) must be BYTE-FOR-BYTE identical before and after restart, with
no regenerated/synthetic identity (no RECOVERED-*) when the original lineage is
provable from persisted state.

This is a persistence/identity test only — no trading logic is exercised.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.runtime.startup_recovery import recover_positions_on_startup
from core.trade_management.manager import TradeStateManager
from core.trade_management.config import TradeManagementConfig
from core.trade_journal import build_trade_record
from core.trade_truth import build_trade_truth, validate_trade_truth, compute_r_multiple


# ─── Known original lineage (Stage A) ─────────────────────────────────────────
ORIG_CANONICAL = "NZDUSD*1784741700*THREE_BLACK_CROWS"
ORIG_OBSERVATION = "obs_NZDUSD_1784741700_M5"
ORIG_CORRELATION = "COR-20260722-1-NZDUSD-D2C3"
ORIG_DECISION = "93eab925eec8"
TICKET = 80513550
SYMBOL = "NZDUSD"
MAGIC = 713001


@pytest.fixture
def exec_results_dir(tmp_path):
    """Stage A: persist the execution_results record the runtime writes on open."""
    d = tmp_path / "logs" / "execution_results" / SYMBOL
    d.mkdir(parents=True)
    record = {
        "timestamp_utc": "2026-07-22T17:39:35.314Z",
        "symbol": SYMBOL,
        "cycle_id": 1,
        "result_ok": True,
        "retcode": 10009,
        "deal": 53297071,
        "order_ticket": TICKET,          # == broker position ticket (result.order)
        "fill_price": 0.58151,
        "side": "SELL",
        "volume": 0.01,
        "pattern": "THREE_BLACK_CROWS",
        "correlation_id": ORIG_CORRELATION,
        "decision_id": ORIG_DECISION,
        "canonical_opportunity_id": ORIG_CANONICAL,
        "observation_id": ORIG_OBSERVATION,
        "decision_ts_utc_ms": 1784741966636,
    }
    (d / "2026-07-22.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    return tmp_path


def _mock_broker_position():
    bp = MagicMock()
    bp.ticket = TICKET
    bp.symbol = SYMBOL
    bp.type = 1  # SELL
    bp.magic = MAGIC
    bp.price_open = 0.58151
    bp.sl = 0.58169
    bp.tp = 0.58105
    bp.volume = 0.01
    bp.time = 1784752774  # broker server time (UTC+3)
    bp.price_current = 0.58155
    return bp


def _recover_one_position(exec_results_dir) -> "TradeStateManager":
    """Stage B: fresh (restarted) manager runs the REAL recovery path."""
    cfg = TradeManagementConfig(
        break_even_trigger_rr=0, break_even_buffer_rr=0,
        trailing_step=0, trailing_start_rr=0,
        partial_tp_fraction=0, partial_tp_path_fraction=0,
        max_time_in_trade_seconds=0,
    )
    tm = TradeStateManager(cfg)  # brand-new in-memory state == post-restart

    old_cwd = os.getcwd()
    os.chdir(str(exec_results_dir))
    try:
        with patch("core.runtime.startup_recovery.mt5_call", return_value=[_mock_broker_position()]), \
             patch("core.runtime.startup_recovery.mt5") as mock_mt5:
            mock_mt5.ORDER_TYPE_BUY = 0
            count = recover_positions_on_startup(trade_manager=tm, symbol=SYMBOL, magic=MAGIC)
    finally:
        os.chdir(old_cwd)

    assert count == 1
    return tm


# ─── Stage C: reconstruction restores the exact original lineage ──────────────

def test_recovered_position_restores_exact_lineage(exec_results_dir):
    tm = _recover_one_position(exec_results_dir)
    pos = tm.positions_open()[0]

    assert pos.trade_identity is not None, "recovery must attach a TradeIdentity"
    assert pos.trade_identity.canonical_opportunity_id == ORIG_CANONICAL
    assert pos.trade_identity.observation_id == ORIG_OBSERVATION
    assert pos.trade_identity.correlation_id == ORIG_CORRELATION
    assert pos.trade_identity.decision_id == ORIG_DECISION


# ─── Stage D: close → journal → trade_truth preserve the lineage ──────────────

def test_recovered_lineage_survives_close_journal_and_trade_truth(exec_results_dir):
    tm = _recover_one_position(exec_results_dir)
    pos = tm.positions_open()[0]

    # Close via the REAL trade-journal projection path.
    record = build_trade_record(
        position=pos,
        exit_price=0.58169,
        exit_time=pos.open_time + 461,
        close_reason="stop_loss",
    )

    # Journal record carries all four lineage IDs unchanged.
    assert record.canonical_opportunity_id == ORIG_CANONICAL
    assert record.observation_id == ORIG_OBSERVATION
    assert record.correlation_id == ORIG_CORRELATION
    assert record.decision_id == ORIG_DECISION

    # Trade truth (schema carries correlation_id + canonical_opportunity_id).
    _r = compute_r_multiple(
        direction=record.direction,
        entry_price=record.entry_price,
        exit_price=record.exit_price,
        stop_loss=record.initial_sl,
    )
    truth = build_trade_truth(
        trade_id=record.trade_id,
        correlation_id=record.correlation_id,
        canonical_opportunity_id=record.canonical_opportunity_id,
        symbol=record.symbol,
        entry_fill_price=record.entry_price,
        exit_fill_price=record.exit_price,
        volume_executed=record.final_volume,
        entry_timestamp_broker=record.entry_time,
        exit_timestamp_broker=record.exit_time,
        pnl_realised=record.realised_pnl,
        r_multiple_realised=_r,
        exit_reason="stop_loss_hit",
    )
    valid, reason = validate_trade_truth(truth)
    assert valid, f"trade_truth invalid: {reason}"

    # THE core assertion: original == recovered == closed/journal == trade_truth.
    assert (
        ORIG_CANONICAL
        == pos.trade_identity.canonical_opportunity_id
        == record.canonical_opportunity_id
        == truth["identity"]["canonical_opportunity_id"]
    )
    assert record.correlation_id == truth["identity"]["correlation_id"] == ORIG_CORRELATION


# ─── Negative assertion: no synthetic identity when the original is provable ──

def test_no_synthetic_recovered_identity_when_lineage_available(exec_results_dir):
    tm = _recover_one_position(exec_results_dir)
    pos = tm.positions_open()[0]

    record = build_trade_record(
        position=pos,
        exit_price=0.58169,
        exit_time=pos.open_time + 461,
        close_reason="stop_loss",
    )

    # No RECOVERED-* fallback anywhere in the lineage.
    for value in (
        record.correlation_id,
        record.canonical_opportunity_id,
        record.observation_id,
        record.decision_id,
    ):
        assert not value.startswith("RECOVERED-"), f"synthetic id leaked: {value}"
        assert not value.startswith("RECOVERY-"), f"synthetic id leaked: {value}"

    # Canonical root was NOT regenerated — it is the byte-for-byte original.
    assert record.canonical_opportunity_id == ORIG_CANONICAL
    # pattern_tag came from the restored identity, not the "RECOVERED" default.
    assert pos.pattern_tag == "THREE_BLACK_CROWS"


# ─── Failure path: genuinely unrecoverable lineage stays explicit (no lie) ────

def test_unrecoverable_lineage_is_explicit_not_falsely_canonical(tmp_path):
    """When NO persisted execution_results row proves the lineage, recovery must
    NOT fabricate a canonical root; the synthetic correlation fallback is only a
    diagnosable last resort and must never masquerade as the original canonical."""
    # No execution_results directory at all → identity cannot be proven.
    cfg = TradeManagementConfig(
        break_even_trigger_rr=0, break_even_buffer_rr=0,
        trailing_step=0, trailing_start_rr=0,
        partial_tp_fraction=0, partial_tp_path_fraction=0,
        max_time_in_trade_seconds=0,
    )
    tm = TradeStateManager(cfg)
    old_cwd = os.getcwd()
    os.chdir(str(tmp_path))
    try:
        with patch("core.runtime.startup_recovery.mt5_call", return_value=[_mock_broker_position()]), \
             patch("core.runtime.startup_recovery.mt5") as mock_mt5:
            mock_mt5.ORDER_TYPE_BUY = 0
            recover_positions_on_startup(trade_manager=tm, symbol=SYMBOL, magic=MAGIC)
    finally:
        os.chdir(old_cwd)

    pos = tm.positions_open()[0]
    # No identity proven → no TradeIdentity attached (explicit absence).
    assert pos.trade_identity is None
    record = build_trade_record(
        position=pos, exit_price=0.58169, exit_time=pos.open_time + 461, close_reason="stop_loss",
    )
    # Canonical root is empty (honest) — never a fabricated/regenerated value.
    assert record.canonical_opportunity_id == ""
    assert record.observation_id == ""
    assert record.decision_id == ""
