"""
S3-durable open-position excursion checkpoint — survives machine loss.

Extends the local durable-excursion contract (tests/test_excursion_durable_state.py)
with the S3 mirror + S3 recovery fallback:

    OPEN → new extreme → local checkpoint + S3 checkpoint → lose local state
    (VM/disk/instance loss) → restart → restore from S3 → continue → restart
    again from S3 → CLOSE → trade_truth_v1 keeps the FULL-lifetime MFE/MAE.

The excursion checkpoint is DURABLE RUNTIME STATE (not a research dataset) and is
OBSERVATIONAL only — these tests also prove S3 outage never touches SL/TP/close/
risk, S3 state can never attach to the wrong ticket, and missing state never
fabricates history.

Write cost: one S3 put_object per NEW extreme only (never per tick), reusing the
exact bytes written locally (overwrite-by-ticket, no append/read-modify-write).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.trade_management.position import Position, PositionStatus
from core.trade_management.manager import TradeStateManager
from core.trade_management.config import TradeManagementConfig
from core.trade_management import excursion_state as ex
from core.trade_management.excursion_state import (
    persist_excursion, load_excursion, _s3_key, _S3_SCHEMA_VERSION,
)
from core.trade_identity import TradeIdentity
from core.trade_journal import build_trade_record
from core.trade_truth import build_trade_truth, validate_trade_truth
from strategy.signals import Side
from risk.models import OrderIntent
from execution.mt5_execution import ExecutionResult


# ─── Fake in-memory S3 (put/get by exact key) ─────────────────────────────────

class FakeS3:
    """Minimal S3 double: overwrite-by-key put, key-exact get, call counting."""

    def __init__(self, *, fail=False):
        self.store: dict[tuple[str, str], bytes] = {}
        self.put_calls: list[tuple[str, str]] = []
        self.get_calls: list[tuple[str, str]] = []
        self.fail = fail

    def put_object(self, *, Bucket, Key, Body, ContentType=None):
        if self.fail:
            raise RuntimeError("simulated S3 outage")
        self.put_calls.append((Bucket, Key))
        self.store[(Bucket, Key)] = Body if isinstance(Body, bytes) else Body.encode()

    def get_object(self, *, Bucket, Key):
        self.get_calls.append((Bucket, Key))
        if (Bucket, Key) not in self.store:
            raise RuntimeError("NoSuchKey")
        body = MagicMock()
        body.read.return_value = self.store[(Bucket, Key)]
        return {"Body": body}


@pytest.fixture
def s3():
    return FakeS3()


@pytest.fixture
def wired(tmp_path, s3, monkeypatch):
    """Redirect local excursion dir to tmp, force the S3 mirror ON, and inject
    the fake S3 client into excursion_state."""
    d = tmp_path / "position_excursion"
    monkeypatch.setattr(ex.config, "POSITION_EXCURSION_DIR", str(d), raising=False)
    monkeypatch.setattr(ex.config, "POSITION_EXCURSION_S3_MIRROR", True, raising=False)
    monkeypatch.setattr(ex, "_s3_client", lambda: s3)
    return {"dir": d, "s3": s3}


def _cfg():
    return TradeManagementConfig(
        break_even_trigger_rr=0, break_even_buffer_rr=0,
        trailing_step=0, trailing_start_rr=0,
        partial_tp_fraction=0, partial_tp_path_fraction=0,
        max_time_in_trade_seconds=0,
    )


def _open_via_manager(tm, *, side, ticket, entry=1.1000, sl=1.0950, tp=1.1100,
                      bid=1.1000, ask=1.1001, canonical="C*1*P"):
    intent = OrderIntent(symbol="EURUSD", side=side, volume=0.1,
                         entry_reference=entry, sl=sl, tp=tp,
                         entry_type="MARKET", pattern="ENGULFING_BULLISH")
    execu = ExecutionResult(ok=True, retcode=10009, deal=ticket, order=ticket,
                            fill_price=entry, comment="ok")
    ident = TradeIdentity(correlation_id="COR-1", decision_id="d1",
                          canonical_opportunity_id=canonical, observation_id="obs1")
    return tm.register_from_execution(intent, magic=713001, execution=execu,
                                      entry_fill_price=entry, bid=bid, ask=ask,
                                      open_time_s=1717400000.0, trade_identity=ident)


def _recover(tm, *, ticket, side_type, price_current, sl=1.0950, tp=1.1100, entry=1.1000):
    bp = MagicMock()
    bp.ticket = ticket; bp.symbol = "EURUSD"; bp.type = side_type
    bp.magic = 713001; bp.price_open = entry; bp.sl = sl; bp.tp = tp
    bp.volume = 0.1; bp.time = 1717400000; bp.price_current = price_current
    from core.runtime.startup_recovery import recover_positions_on_startup
    with patch("core.runtime.startup_recovery.mt5_call", return_value=[bp]), \
         patch("core.runtime.startup_recovery.mt5") as m:
        m.ORDER_TYPE_BUY = 0
        recover_positions_on_startup(trade_manager=tm, symbol="EURUSD", magic=713001)
    return tm.positions_open()[0]


def _delete_local(wired, ticket):
    """Simulate machine loss: remove the local checkpoint file only."""
    fp = wired["dir"] / f"{int(ticket)}.json"
    if fp.exists():
        fp.unlink()


# ─── TEST A — S3 mirror on new extreme (local + S3 agree) ─────────────────────

def test_A_s3_mirror_on_new_extreme(wired):
    tm = TradeStateManager(_cfg())
    _open_via_manager(tm, side=Side.BUY, ticket=6001)
    tm.on_price_update("EURUSD", bid=1.1080, ask=1.1081, time_s=1.0)   # new MFE

    local = load_excursion(6001)  # local-first
    key = ("trading-bot-v10-data", _s3_key(6001))
    assert key in wired["s3"].store, "S3 checkpoint not written"
    s3_snapshot = json.loads(wired["s3"].store[key].decode())

    assert local["max_favourable_price"] == pytest.approx(1.1080)
    # Exact same excursion values in both copies.
    assert s3_snapshot["max_favourable_price"] == pytest.approx(1.1080)
    assert s3_snapshot["max_adverse_price"] == local["max_adverse_price"]
    assert s3_snapshot["position_ticket"] == 6001
    assert f"schema_version={_S3_SCHEMA_VERSION}" in key[1]


# ─── TEST B — no S3 write without a new extreme ───────────────────────────────

def test_B_no_s3_write_without_new_extreme(wired):
    tm = TradeStateManager(_cfg())
    _open_via_manager(tm, side=Side.BUY, ticket=6002)
    tm.on_price_update("EURUSD", bid=1.0970, ask=1.0971, time_s=1.0)   # new MAE → write
    puts_after_first = len(wired["s3"].put_calls)
    # Non-extreme updates must not write.
    tm.on_price_update("EURUSD", bid=1.0985, ask=1.0986, time_s=2.0)   # less adverse
    tm.on_price_update("EURUSD", bid=1.0990, ask=1.0991, time_s=3.0)   # still less adverse
    assert len(wired["s3"].put_calls) == puts_after_first


# ─── TEST C — local-first recovery (S3 not consulted when local valid) ────────

def test_C_local_first_recovery(wired):
    tm = TradeStateManager(_cfg())
    _open_via_manager(tm, side=Side.BUY, ticket=6003)
    tm.on_price_update("EURUSD", bid=1.1075, ask=1.1076, time_s=1.0)
    wired["s3"].get_calls.clear()
    # Local present + valid → load_excursion must not hit S3.
    snap = load_excursion(6003)
    assert snap["max_favourable_price"] == pytest.approx(1.1075)
    assert wired["s3"].get_calls == []


# ─── TEST D — S3 fallback when local missing ──────────────────────────────────

def test_D_s3_fallback_when_local_missing(wired):
    tm = TradeStateManager(_cfg())
    _open_via_manager(tm, side=Side.BUY, ticket=6004)
    tm.on_price_update("EURUSD", bid=1.1090, ask=1.1091, time_s=1.0)   # MFE
    tm.on_price_update("EURUSD", bid=1.0965, ask=1.0966, time_s=2.0)   # MAE
    _delete_local(wired, 6004)                                          # machine loss

    snap = load_excursion(6004)                                        # → S3 fallback
    assert snap is not None
    assert snap["max_favourable_price"] == pytest.approx(1.1090)
    assert snap["max_adverse_price"] == pytest.approx(1.0965)
    # Rehydrated locally for subsequent reads.
    assert (wired["dir"] / "6004.json").exists()


# ─── TEST E — S3 historical extreme not erased by restart ─────────────────────

def test_E_s3_historical_not_erased(wired):
    tm = TradeStateManager(_cfg())
    _open_via_manager(tm, side=Side.BUY, ticket=6005)
    tm.on_price_update("EURUSD", bid=1.1085, ask=1.1086, time_s=1.0)   # strong MFE
    tm.on_price_update("EURUSD", bid=1.0960, ask=1.0961, time_s=2.0)   # strong MAE
    _delete_local(wired, 6005)
    tm2 = TradeStateManager(_cfg())
    # Broker current price between the extremes — must not erase either.
    rec = _recover(tm2, ticket=6005, side_type=0, price_current=1.1005)
    assert rec.max_favourable_price == pytest.approx(1.1085)
    assert rec.max_adverse_price == pytest.approx(1.0960)
    assert rec.excursion_provenance == "full_lifecycle"


# ─── TEST F — current price can EXTEND restored S3 history ─────────────────────

def test_F_current_price_extends_s3_history(wired):
    tm = TradeStateManager(_cfg())
    _open_via_manager(tm, side=Side.BUY, ticket=6006)
    tm.on_price_update("EURUSD", bid=1.1040, ask=1.1041, time_s=1.0)   # MFE
    tm.on_price_update("EURUSD", bid=1.0975, ask=1.0976, time_s=2.0)   # MAE
    _delete_local(wired, 6006)
    tm2 = TradeStateManager(_cfg())
    # More favourable current price → extend MFE, keep MAE.
    rec = _recover(tm2, ticket=6006, side_type=0, price_current=1.1120)
    assert rec.max_favourable_price == pytest.approx(1.1120)   # extended
    assert rec.max_adverse_price == pytest.approx(1.0975)      # preserved


# ─── TEST G — S3 outage does not affect local persistence or trading ──────────

def test_G_s3_outage_is_non_blocking(tmp_path, monkeypatch):
    d = tmp_path / "position_excursion"
    failing = FakeS3(fail=True)
    monkeypatch.setattr(ex.config, "POSITION_EXCURSION_DIR", str(d), raising=False)
    monkeypatch.setattr(ex.config, "POSITION_EXCURSION_S3_MIRROR", True, raising=False)
    monkeypatch.setattr(ex, "_s3_client", lambda: failing)

    tm = TradeStateManager(_cfg())
    pos = _open_via_manager(tm, side=Side.BUY, ticket=6007)
    sl0, tp0, st0 = pos.stop_loss, pos.take_profit, pos.status
    # New extreme triggers persist → S3 put raises internally but is swallowed.
    tm.on_price_update("EURUSD", bid=1.0960, ask=1.0961, time_s=1.0)

    # Local persistence still succeeded.
    assert (d / "6007.json").exists()
    assert load_excursion(6007)["max_adverse_price"] == pytest.approx(1.0960)
    # Trade untouched: SL/TP/status/openness unchanged.
    assert pos.stop_loss == sl0 and pos.take_profit == tp0 and pos.status == st0
    assert pos in tm.positions_open()


# ─── TEST H — neither local nor S3 → legacy recovery_seeded ───────────────────

def test_H_neither_local_nor_s3(wired):
    # No persist for ticket 6008 (nothing local, nothing in S3).
    tm = TradeStateManager(_cfg())
    rec = _recover(tm, ticket=6008, side_type=0, price_current=1.0990)
    assert rec.max_favourable_price == pytest.approx(1.0990)   # seeded from current
    assert rec.max_adverse_price == pytest.approx(1.0990)      # not fabricated
    assert rec.excursion_provenance == "recovery_seeded"


# ─── TEST I — identity isolation across S3 (two tickets, same symbol) ─────────

def test_I_s3_identity_isolation(wired):
    tm = TradeStateManager(_cfg())
    _open_via_manager(tm, side=Side.BUY, ticket=7001, canonical="C*A")
    _open_via_manager(tm, side=Side.BUY, ticket=7002, canonical="C*B")
    p1 = [p for p in tm.positions_open() if p.mt5_ticket == 7001][0]
    p2 = [p for p in tm.positions_open() if p.mt5_ticket == 7002][0]
    p1.max_favourable_price, p1.max_adverse_price = 1.1090, 1.0980
    p2.max_favourable_price, p2.max_adverse_price = 1.1030, 1.0940
    persist_excursion(p1); persist_excursion(p2)

    # Two distinct S3 objects; each ticket loads only its own state.
    assert ("trading-bot-v10-data", _s3_key(7001)) in wired["s3"].store
    assert ("trading-bot-v10-data", _s3_key(7002)) in wired["s3"].store
    _delete_local(wired, 7001); _delete_local(wired, 7002)
    s1, s2 = load_excursion(7001), load_excursion(7002)
    assert (s1["max_favourable_price"], s1["max_adverse_price"]) == pytest.approx((1.1090, 1.0980))
    assert (s2["max_favourable_price"], s2["max_adverse_price"]) == pytest.approx((1.1030, 1.0940))


# ─── TEST I-neg — stale S3 checkpoint for a different ticket never attaches ────

def test_I_neg_wrong_ticket_never_attaches(wired):
    tm = TradeStateManager(_cfg())
    _open_via_manager(tm, side=Side.BUY, ticket=7003)
    tm.on_price_update("EURUSD", bid=1.1050, ask=1.1051, time_s=1.0)
    _delete_local(wired, 7003)
    # A DIFFERENT ticket has no S3 object → None (no symbol-only cross-attach).
    assert load_excursion(9999) is None


# ─── TEST J — full machine-loss lifecycle → trade_truth full lifetime ─────────

def test_J_machine_loss_lifecycle_to_trade_truth(wired):
    tm = TradeStateManager(_cfg())
    _open_via_manager(tm, side=Side.BUY, ticket=8001)
    tm.on_price_update("EURUSD", bid=1.0975, ask=1.0976, time_s=1.0)   # early MAE

    # ── lose local state, restart from S3 ──
    _delete_local(wired, 8001)
    tm2 = TradeStateManager(_cfg())
    rec = _recover(tm2, ticket=8001, side_type=0, price_current=1.1000)
    assert rec.max_adverse_price == pytest.approx(1.0975)   # restored from S3
    tm2.on_price_update("EURUSD", bid=1.1085, ask=1.1086, time_s=2.0)  # new MFE (mirrored)

    # ── lose local state again, restart from S3 again ──
    _delete_local(wired, 8001)
    tm3 = TradeStateManager(_cfg())
    rec = _recover(tm3, ticket=8001, side_type=0, price_current=1.1020)
    assert rec.max_favourable_price == pytest.approx(1.1085)   # from post-restart-1 period
    assert rec.max_adverse_price == pytest.approx(1.0975)      # from pre-restart-1 period
    assert rec.excursion_provenance == "full_lifecycle"

    # ── CLOSE → trade_truth_v1 reflects the FULL trade lifetime ──
    rec.status = PositionStatus.CLOSED
    trec = build_trade_record(position=rec, exit_price=1.1050,
                              exit_time=rec.open_time + 7200, close_reason="take_profit")
    truth = build_trade_truth(
        trade_id=trec.trade_id, correlation_id="COR-1",
        canonical_opportunity_id="C*1*P", symbol="EURUSD",
        entry_fill_price=trec.entry_price, exit_fill_price=trec.exit_price,
        volume_executed=trec.final_volume, entry_timestamp_broker=trec.entry_time,
        exit_timestamp_broker=trec.exit_time, pnl_realised=trec.realised_pnl,
        r_multiple_realised=1.0, commission=trec.commission, swap=trec.swap,
        net_profit=trec.net_pnl, exit_reason="take_profit_hit",
        max_favourable_price=trec.max_favourable_price, max_adverse_price=trec.max_adverse_price,
        mfe_r=trec.mfe_r, mae_r=trec.mae_r, excursion_provenance=trec.excursion_provenance,
    )
    valid, reason = validate_trade_truth(truth)
    assert valid, reason
    out = truth["outcome"]
    assert out["max_favourable_price"] == pytest.approx(1.1085)   # full lifetime, via S3
    assert out["max_adverse_price"] == pytest.approx(1.0975)
    # risk 0.0050 → MFE 0.0085/0.0050=1.7R ; MAE 0.0025/0.0050=0.5R
    assert out["mfe_r"] == pytest.approx(1.7)
    assert out["mae_r"] == pytest.approx(0.5)
    assert out["excursion_provenance"] == "full_lifecycle"


# ─── NEGATIVE — mirror disabled: no S3 traffic at all ─────────────────────────

def test_neg_mirror_disabled_no_s3(tmp_path, s3, monkeypatch):
    d = tmp_path / "position_excursion"
    monkeypatch.setattr(ex.config, "POSITION_EXCURSION_DIR", str(d), raising=False)
    monkeypatch.setattr(ex.config, "POSITION_EXCURSION_S3_MIRROR", False, raising=False)
    monkeypatch.setattr(ex, "_s3_client", lambda: s3)
    tm = TradeStateManager(_cfg())
    _open_via_manager(tm, side=Side.BUY, ticket=9100)
    tm.on_price_update("EURUSD", bid=1.0970, ask=1.0971, time_s=1.0)
    assert s3.put_calls == []            # no S3 write when disabled
    assert load_excursion(9100) is not None   # local still works
