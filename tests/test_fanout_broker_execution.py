"""Focused broker-boundary tests for the fan-out execution worker.

REPAIR 1 (filling-mode negotiation) + REPAIR 2 (destination-broker stop
validation) + disabled-instrument gate. Mocks only — never submits orders,
never touches a terminal. ONE canonical interpretation of MT5 broker
constraints is shared via core.mt5_symbol_spec.
"""

from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from core.accounts.config import AccountConfig
from core.accounts.execution_worker import execute_pinned
from core.mt5_symbol_spec import (
    FILLING_FOK,
    FILLING_IOC,
    FILLING_RETURN,
    select_filling_mode,
    validate_trade_mode,
)


# ─── ORDER-CAPABLE MT5 DOUBLE ─────────────────────────────────────────────────

class FakeExecMT5:
    """Order-capable MT5 double with one configurable symbol spec."""

    ACCOUNT_TRADE_MODE_DEMO = 0
    TRADE_ACTION_DEAL = 1
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    # Real MQL5 ORDER_FILLING_* values as the MT5 python module exposes them.
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_RETURN = 2
    TRADE_RETCODE_DONE = 10009

    def __init__(self, cfg, *, spec=None, tick=None):
        self.cfg = cfg
        self.sent = []
        self.spec = dict(
            name="EURUSD.mq", digits=5, point=0.00001,
            trade_contract_size=100000.0, volume_min=0.01, volume_step=0.01,
            volume_max=100.0, trade_stops_level=10, trade_freeze_level=5,
            trade_tick_size=0.00001, trade_tick_value=1.0,
            trade_mode=4, filling_mode=FILLING_IOC,
        )
        self.spec.update(spec or {})
        self.tick = tick or NS(bid=1.1, ask=1.10002)
        self.account = NS(login=cfg.login, server=cfg.server, trade_mode=0,
                          balance=10000.0, equity=10000.0, margin=1.0,
                          margin_free=9999.0, margin_level=100.0, leverage=100,
                          currency="USD", trade_allowed=True, trade_expert=True)
        self.terminal = NS(path=str(Path(cfg.terminal_path).parent),
                           data_path=str(Path(cfg.terminal_path).parent / "data"),
                           connected=True, trade_allowed=True, tradeapi_disabled=False)

    def account_info(self):
        return self.account

    def terminal_info(self):
        return self.terminal

    def symbol_info(self, name):
        return NS(**self.spec)

    def symbol_info_tick(self, name):
        return self.tick

    def positions_get(self, *args, **kwargs):
        return []

    def history_deals_get(self, *args, **kwargs):
        return []

    def order_send(self, request):
        self.sent.append(request)
        return NS(retcode=10009, deal=7, order=8, comment="ok", price=1.10002)


def _config(tmp_path, account_id="METAQUOTES"):
    return AccountConfig(
        account_id=account_id, broker="MetaQuotes",
        server=account_id + "-Demo", login=1001,
        terminal_path=str(tmp_path / account_id / "terminal64.exe"),
        enabled=True, role="baseline",
    )


def _request(cfg, *, side="BUY", sl=1.09902, tp=1.10102,
             broker_symbol="EURUSD.mq", volume=0.05):
    return {
        "account": asdict(cfg),
        "target": {"account_id": cfg.account_id, "broker": cfg.broker,
                   "broker_server": cfg.server, "canonical_symbol": "EURUSD",
                   "broker_symbol": broker_symbol,
                   "canonical_opportunity_id": "o-1", "correlation_id": "c-1",
                   "decision_id": "d-1", "account_execution_id": "e-1",
                   "trade_id": "t-1", "requested_volume": volume,
                   "entry": 1.10002, "sl": sl, "tp": tp},
        "order": {"broker_symbol": broker_symbol, "requested_volume": volume,
                  "sl": sl, "tp": tp, "side": side, "deviation": 20,
                  "magic": 713001, "comment": "fanout-test"},
    }


# ─── REPAIR 1: filling-mode negotiation (shared canonical helper) ─────────────

def test_select_filling_mode_semantics():
    assert select_filling_mode(1) == "FOK"        # FOK-only (MetaQuotes FX)
    assert select_filling_mode(2) == "IOC"
    assert select_filling_mode(3) == "IOC"        # multi-mode: deterministic IOC preference
    assert select_filling_mode(4) == "RETURN"
    assert select_filling_mode(7) == "IOC"        # FOK|IOC|RETURN → IOC preferred
    assert select_filling_mode(0) is None         # no supported mode → fail closed
    assert select_filling_mode(None) is None
    assert select_filling_mode("bogus") is None


def test_fok_only_symbol_produces_fok_request(tmp_path):
    cfg = _config(tmp_path)
    fake = FakeExecMT5(cfg, spec={"filling_mode": FILLING_FOK})
    out = execute_pinned(_request(cfg), fake)
    assert out["executed"] is True and out["status"] == "FILLED"
    assert out["filling_mode"] == "FOK"
    assert fake.sent[0]["type_filling"] == fake.ORDER_FILLING_FOK
    assert fake.sent[0]["type_filling"] != fake.ORDER_FILLING_IOC


@pytest.mark.parametrize("mask", [FILLING_FOK, FILLING_FOK | FILLING_RETURN])
def test_metaquotes_fok_symbols_never_receive_ioc(tmp_path, mask):
    cfg = _config(tmp_path)
    fake = FakeExecMT5(cfg, spec={"filling_mode": mask})
    out = execute_pinned(_request(cfg), fake)
    assert out["status"] == "FILLED"
    assert fake.sent[0]["type_filling"] == fake.ORDER_FILLING_FOK
    assert fake.sent[0]["type_filling"] != fake.ORDER_FILLING_IOC


def test_ioc_capable_symbol_keeps_ioc_request(tmp_path):
    """Vantage reports IOC support → IOC requests remain valid (no regression)."""
    cfg = _config(tmp_path, "VANTAGE")
    fake = FakeExecMT5(cfg, spec={"filling_mode": FILLING_IOC})
    out = execute_pinned(_request(cfg), fake)
    assert out["status"] == "FILLED"
    assert fake.sent[0]["type_filling"] == fake.ORDER_FILLING_IOC
    assert out["filling_mode"] == "IOC"


def test_multi_mode_symbol_deterministic_supported_choice(tmp_path):
    cfg = _config(tmp_path)
    fake = FakeExecMT5(cfg, spec={"filling_mode": FILLING_FOK | FILLING_IOC})
    out = execute_pinned(_request(cfg), fake)
    assert out["status"] == "FILLED"
    assert fake.sent[0]["type_filling"] == fake.ORDER_FILLING_IOC


def test_return_only_symbol_gets_return_request(tmp_path):
    cfg = _config(tmp_path)
    fake = FakeExecMT5(cfg, spec={"filling_mode": FILLING_RETURN})
    out = execute_pinned(_request(cfg), fake)
    assert out["status"] == "FILLED"
    assert fake.sent[0]["type_filling"] == fake.ORDER_FILLING_RETURN


def test_unsupported_filling_mode_fails_closed(tmp_path):
    cfg = _config(tmp_path)
    fake = FakeExecMT5(cfg, spec={"filling_mode": 0})
    out = execute_pinned(_request(cfg), fake)
    assert out["executed"] is False
    assert out["status"] == "BLOCKED"
    assert out["comment"] == "NO_SUPPORTED_FILLING_MODE"
    assert out["broker_filling_mode"] == 0
    assert fake.sent == []


def test_legacy_filling_mode_delegates_to_shared_negotiation():
    """The legacy boundary must reuse the SAME negotiation, not a second one."""
    import MetaTrader5 as mt5
    import execution.mt5_execution as mex
    with patch("execution.mt5_execution.mt5_call", return_value=NS(filling_mode=1)):
        assert mex._filling_mode("EURUSD") == mt5.ORDER_FILLING_FOK
    with patch("execution.mt5_execution.mt5_call", return_value=NS(filling_mode=3)):
        assert mex._filling_mode("EURUSD") == mt5.ORDER_FILLING_IOC
    # Legacy keeps its historical permissive fallback on an empty mask.
    with patch("execution.mt5_execution.mt5_call", return_value=NS(filling_mode=0)):
        assert mex._filling_mode("EURUSD") == mt5.ORDER_FILLING_IOC
    with patch("execution.mt5_execution.mt5_call", return_value=None):
        assert mex._filling_mode("EURUSD") == mt5.ORDER_FILLING_IOC


# ─── REPAIR 2 / disabled instruments: trade-mode gate ─────────────────────────

def test_validate_trade_mode_unit():
    spec = NS(trade_mode=0)
    assert validate_trade_mode(spec, "BUY") == "SYMBOL_TRADE_MODE_DISABLED"
    spec = NS(trade_mode=1)
    assert validate_trade_mode(spec, "SELL") == "SYMBOL_TRADE_MODE_BLOCKED"
    assert validate_trade_mode(spec, "BUY") is None
    spec = NS(trade_mode=2)
    assert validate_trade_mode(spec, "BUY") == "SYMBOL_TRADE_MODE_BLOCKED"
    assert validate_trade_mode(spec, "SELL") is None
    spec = NS(trade_mode=3)
    assert validate_trade_mode(spec, "BUY") == "SYMBOL_TRADE_MODE_BLOCKED"
    assert validate_trade_mode(spec, "BOGUS") == "INVALID_SIDE"


def test_disabled_trade_mode_never_reaches_order_send(tmp_path):
    """MetaQuotes USTEC/US500 report trade_mode=0 — must clean-skip."""
    cfg = _config(tmp_path)
    fake = FakeExecMT5(cfg, spec={"trade_mode": 0})
    out = execute_pinned(_request(cfg), fake)
    assert out["executed"] is False
    assert out["status"] == "BLOCKED"
    assert out["comment"] == "SYMBOL_TRADE_MODE_DISABLED"
    assert out["broker_trade_mode"] == 0
    assert fake.sent == []


def test_side_restricted_trade_mode_blocked(tmp_path):
    cfg = _config(tmp_path)
    long_only = FakeExecMT5(cfg, spec={"trade_mode": 1})
    out = execute_pinned(_request(cfg, side="SELL"), long_only)
    assert out["status"] == "BLOCKED" and out["comment"] == "SYMBOL_TRADE_MODE_BLOCKED"
    assert long_only.sent == []
    out = execute_pinned(_request(cfg, side="BUY"), long_only)
    assert out["status"] == "FILLED"


# ─── REPAIR 2: destination-broker stop-distance validation ────────────────────

def test_buy_minimum_stop_distance_blocked(tmp_path):
    cfg = _config(tmp_path)
    fake = FakeExecMT5(cfg)  # stops_level=10 → 0.0001 minimum
    out = execute_pinned(_request(cfg, sl=1.09997, tp=1.10102), fake)  # 5 points away
    assert out["executed"] is False
    assert out["status"] == "BLOCKED"
    assert out["comment"] == "SL_TOO_CLOSE"
    assert fake.sent == []


def test_sell_minimum_stop_distance_blocked(tmp_path):
    cfg = _config(tmp_path)
    fake = FakeExecMT5(cfg)
    out = execute_pinned(_request(cfg, side="SELL", sl=1.10003, tp=1.09998), fake)
    assert out["executed"] is False
    assert out["status"] == "BLOCKED"
    assert fake.sent == []


def test_valid_existing_stops_submitted_unchanged(tmp_path):
    cfg = _config(tmp_path)
    fake = FakeExecMT5(cfg)
    out = execute_pinned(_request(cfg, sl=1.09902, tp=1.10102), fake)
    assert out["status"] == "FILLED"
    assert fake.sent[0]["sl"] == 1.09902
    assert fake.sent[0]["tp"] == 1.10102
    # Canonical SL/TP preserved alongside broker-submitted values.
    assert out["canonical_sl"] == 1.09902
    assert out["broker_sl"] == 1.09902


def test_canonical_sl_tp_normalised_to_broker_digits(tmp_path):
    """Canonical prices off the destination grid are normalised BEFORE send."""
    cfg = _config(tmp_path)
    fake = FakeExecMT5(cfg)
    out = execute_pinned(_request(cfg, sl=1.099005, tp=1.101015), fake)
    assert out["status"] == "FILLED"
    assert fake.sent[0]["sl"] == 1.09901       # 5-digit HALF_UP normalisation
    assert fake.sent[0]["tp"] == 1.10102
    assert out["canonical_sl"] == 1.099005     # original canonical values intact
    assert out["canonical_tp"] == 1.101015
    assert out["broker_sl"] == 1.09901


def test_zero_stops_level_invents_no_restriction(tmp_path):
    """A broker with stops_level=0 accepts tighter stops — no invented block."""
    cfg = _config(tmp_path)
    fake = FakeExecMT5(cfg, spec={"trade_stops_level": 0, "trade_freeze_level": 0})
    out = execute_pinned(_request(cfg, sl=1.09999, tp=1.10006), fake)
    assert out["status"] == "FILLED"
    assert fake.sent[0]["sl"] == 1.09999


def test_invalid_stops_fail_closed_without_mechanical_adjustment(tmp_path):
    """No canonical policy allows minimum-distance adjustment → fail closed."""
    cfg = _config(tmp_path)
    fake = FakeExecMT5(cfg, spec={"trade_stops_level": 50})
    out = execute_pinned(_request(cfg, sl=1.09997, tp=1.10102), fake)
    assert out["status"] == "BLOCKED" and out["comment"] == "SL_TOO_CLOSE"
    assert fake.sent == []                      # nothing invalid was sent
    assert out["canonical_sl"] == 1.09997       # canonical values preserved
    assert out["broker_sl"] == 1.09997          # normalised-but-not-adjusted


# ─── fan-out isolation: one account's failure never stops another ─────────────

def test_one_account_failure_does_not_stop_another():
    from core.accounts.account_router import route_executions

    def _route(account_id):
        return {
            "target": NS(account_id=account_id, broker="b", broker_server="s",
                         login=1, canonical_symbol="EURUSD",
                         broker_symbol="EURUSD", canonical_opportunity_id="o",
                         correlation_id="c", decision_id="d",
                         account_execution_id="e-" + account_id,
                         trade_id="t-" + account_id, requested_volume=0.01,
                         entry=1.0, sl=0.9, tp=1.1, pattern="", observation_id=""),
            "eligibility": {"eligible": True, "reasons": []},
            "execution_enabled": True,
        }

    def execute_one(item):
        if item["target"].account_id == "FAILING":
            raise RuntimeError("worker exploded")
        return {"executed": True, "status": "FILLED", "ok": True,
                "retcode": 10009, "lifecycle_side": "BUY"}

    outcomes = route_executions(
        routed=[_route("FAILING"), _route("HEALTHY")],
        execute_one=execute_one,
    )
    by_id = {o["target"].account_id: o for o in outcomes}
    assert by_id["FAILING"]["executed"] is False
    assert by_id["FAILING"]["status"] == "WORKER_FAILED"
    assert by_id["HEALTHY"]["executed"] is True
    assert by_id["HEALTHY"]["status"] == "FILLED"
