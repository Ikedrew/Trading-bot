"""Risk recheck A."""
from dataclasses import asdict
from types import SimpleNamespace as NS
from core.accounts.config import AccountConfig
from core.accounts.execution_worker import execute_pinned
def make_cfg(**over):
    b = dict(account_id="METAQUOTES", broker="MetaQuotes", server="METAQUOTES-Demo", login=1001, terminal_path="C:/MT5Accounts/METAQUOTES/terminal64.exe", enabled=True, role="baseline")
    b.update(over)
    return AccountConfig(**b)
class FakeExecMT5:
    ACCOUNT_TRADE_MODE_DEMO = 0
    TRADE_ACTION_DEAL = 1
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_RETURN = 2
    TRADE_RETCODE_DONE = 10009
    def __init__(self, account, *, bid=1.1, ask=1.10002, calc="ok", point=0.00001, tick_value=1.0):
        self.account = account
        self.sent = []
        self._bid = bid
        self._ask = ask
        self._calc = calc
        self._point = point
        self._tick_value = tick_value
        self.cfg = None
    def account_info(self):
        return self.account
    def terminal_info(self):
        import pathlib
        p = str(pathlib.Path(self.cfg.terminal_path).parent) if self.cfg else "C:/MT5Accounts/METAQUOTES"
        return NS(path=p, data_path=p + "/data", connected=True, trade_allowed=True, tradeapi_disabled=False)
    def symbol_info(self, name):
        return NS(name=name, digits=5, point=self._point, trade_contract_size=100000.0, volume_min=0.01, volume_step=0.01, volume_max=100.0, trade_stops_level=10, trade_freeze_level=5, trade_tick_size=self._point, trade_tick_value=self._tick_value, trade_mode=4, filling_mode=1)
    def symbol_info_tick(self, name):
        return NS(bid=self._bid, ask=self._ask)
    def order_calc_profit(self, otype, sym, vol, po, pc):
        if self._calc == "raise":
            raise RuntimeError("boom")
        if self._calc == "none":
            return None
        if self._calc == "nan":
            return "xx"
        ticks = (pc - po) / self._point
        s = 1.0 if otype == self.ORDER_TYPE_BUY else -1.0
        return ticks * self._tick_value * float(vol) * s
    def order_send(self, req):
        self.sent.append(req)
        return NS(retcode=10009, deal=7, order=8, comment="ok", price=1.1)
    def last_error(self):
        return (0, "ok")
def make_acct(login=1001, server="METAQUOTES-Demo"):
    return NS(login=login, server=server, trade_mode=0, balance=10000.0, equity=10000.0, margin=1.0, margin_free=9999.0, margin_level=100.0, leverage=100, currency="USD", trade_allowed=True, trade_expert=True)
def make_mt5(acct=None, **kw):
    c = make_cfg()
    m = FakeExecMT5(acct or make_acct(), **kw)
    m.cfg = c
    return c, m
def make_payload(cfg, *, side="BUY", volume=0.05, sl=1.09902, tp=1.10202, risk_amount=25.0, broker_symbol="EURUSD.mq"):
    return {"account": asdict(cfg), "target": {"account_id": cfg.account_id, "broker": cfg.broker, "broker_server": cfg.server, "canonical_symbol": "EURUSD", "broker_symbol": broker_symbol, "canonical_opportunity_id": "o", "correlation_id": "c", "decision_id": "d", "account_execution_id": "e-1", "trade_id": "t-1"}, "order": {"broker_symbol": broker_symbol, "requested_volume": volume, "sl": sl, "tp": tp, "side": side, "deviation": 20, "magic": 713001, "comment": "risk-test", "risk_amount": risk_amount}}





def test_1_market_buy_within_budget_allowed():
    cfg, mt5 = make_mt5()
    out = execute_pinned(make_payload(cfg), mt5)
    assert out["executed"] is True and out["status"] == "FILLED"
    assert len(mt5.sent) == 1
    assert out["execution_risk_amount"] == 5.0
    assert out["risk_budget"] == 25.0


def test_2_market_buy_drift_pushes_risk_above_budget_rejected():
    cfg, mt5 = make_mt5(ask=1.10102, bid=1.1010)
    out = execute_pinned(make_payload(cfg, risk_amount=5.0), mt5)
    assert out["executed"] is False
    assert out["comment"] == "EXECUTION_RISK_EXCEEDED"
    assert mt5.sent == []
    assert out["execution_price"] == 1.10102


def test_3_market_sell_equivalent():
    cfg, mt5 = make_mt5()
    out = execute_pinned(make_payload(cfg, side="SELL", sl=1.101, tp=1.098), mt5)
    assert out["executed"] is True and out["status"] == "FILLED"
    assert len(mt5.sent) == 1
    assert out["execution_price"] == 1.1
    cfg2 = make_cfg()
    mt5b = FakeExecMT5(make_acct(), bid=1.099, ask=1.09902)
    mt5b.cfg = cfg2
    out2 = execute_pinned(make_payload(cfg2, side="SELL", sl=1.101, tp=1.098,
                                       risk_amount=5.0), mt5b)
    assert out2["executed"] is False
    assert out2["comment"] == "EXECUTION_RISK_EXCEEDED"
    assert mt5b.sent == []


def test_4_missing_budget_rejected():
    for missing in (None, 0.0, -5.0):
        cfg, mt5 = make_mt5()
        payload = make_payload(cfg, risk_amount=missing)
        if missing is None:
            del payload["order"]["risk_amount"]
        out = execute_pinned(payload, mt5)
        assert out["executed"] is False
        assert out["comment"] == "EXECUTION_RISK_BUDGET_UNAVAILABLE"
        assert mt5.sent == []


def test_5_calc_failure_rejected():
    for mode in ("raise", "none", "nan"):
        cfg, mt5 = make_mt5(calc=mode)
        out = execute_pinned(make_payload(cfg), mt5)
        assert out["executed"] is False, mode
        assert out["comment"] == "EXECUTION_RISK_CALC_FAILED", mode
        assert mt5.sent == []



def test_6_oversized_historical_style_order_rejected():
    cfg, mt5 = make_mt5()
    out = execute_pinned(make_payload(cfg, volume=8.0, risk_amount=25.0), mt5)
    assert out["executed"] is False
    assert out["comment"] == "EXECUTION_RISK_EXCEEDED"
    assert mt5.sent == []
    assert out["execution_risk_amount"] == 800.0
    assert out["excess_ratio"] == round(800.0 / 25.0, 4)


def test_7_conservative_step_flooring_allowed():
    cfg, mt5 = make_mt5()
    out = execute_pinned(make_payload(cfg, volume=0.05, risk_amount=25.0), mt5)
    assert out["executed"] is True and out["status"] == "FILLED"
    assert len(mt5.sent) == 1
    assert out["execution_risk_amount"] <= out["risk_budget"]


def test_8_independent_fanout_accounts_pass_reject_independently():
    from core.accounts.account_router import build_account_order_request
    from core.accounts.account_sizing import AccountVolumeResult
    from core.accounts.fanout import CanonicalDecision, fan_out_canonical_decision

    cfg_a = make_cfg()
    cfg_b = make_cfg(account_id="VANTAGE", broker="Vantage",
                     server="VANTAGE-Demo", login=1003,
                     terminal_path="C:/MT5Accounts/VANTAGE/terminal64.exe")
    dec = CanonicalDecision("o", "c", "d", "EURUSD", "BUY",
                            1.10002, 1.09902, 1.10202, 0.25)
    targets = fan_out_canonical_decision(dec, [cfg_a, cfg_b])
    by_id = {t.account_id: t for t in targets}
    vol_a = AccountVolumeResult("METAQUOTES", "EURUSD.mq", 0.05, "", 0.0025, 25.0, 10000.0)
    vol_b = AccountVolumeResult("VANTAGE", "EURUSD.van", 0.05, "", 0.0025, 1.0, 10000.0)
    req_a = build_account_order_request(
        item={"target": by_id["METAQUOTES"], "volume": vol_a,
              "broker_symbol": "EURUSD.mq"})
    req_b = build_account_order_request(
        item={"target": by_id["VANTAGE"], "volume": vol_b,
              "broker_symbol": "EURUSD.van"})
    assert req_a["risk_amount"] == 25.0 and req_b["risk_amount"] == 1.0
    mt5_a = FakeExecMT5(make_acct(), ask=1.10002, bid=1.1)
    mt5_a.cfg = cfg_a
    acct_b = make_acct(login=1003, server="VANTAGE-Demo")
    mt5_b = FakeExecMT5(acct_b, ask=1.10002, bid=1.1)
    mt5_b.cfg = cfg_b

    def payload_for(cfg, target, req):
        return {"account": asdict(cfg),
                "target": {"account_id": target.account_id, "broker": target.broker,
                           "broker_server": target.broker_server,
                           "canonical_symbol": target.canonical_symbol,
                           "broker_symbol": req["broker_symbol"],
                           "canonical_opportunity_id": target.canonical_opportunity_id,
                           "correlation_id": target.correlation_id,
                           "decision_id": target.decision_id,
                           "account_execution_id": target.account_execution_id,
                           "trade_id": target.trade_id},
                "order": {**req, "sl": 1.09902, "tp": 1.10202, "side": "BUY",
                          "deviation": 20, "magic": 713001, "comment": "fanout"}}

    out_a = execute_pinned(payload_for(cfg_a, by_id["METAQUOTES"], req_a), mt5_a)
    out_b = execute_pinned(payload_for(cfg_b, by_id["VANTAGE"], req_b), mt5_b)
    assert out_a["executed"] is True and len(mt5_a.sent) == 1
    assert out_b["executed"] is False
    assert out_b["comment"] == "EXECUTION_RISK_EXCEEDED"
    assert mt5_b.sent == []

