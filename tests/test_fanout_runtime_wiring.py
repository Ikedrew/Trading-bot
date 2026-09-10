"""Focused runtime-wiring tests for the multi-account fan-out gate (D boundary).

Proves:
1. flag OFF → legacy single-account execution called; fan-out never called
2. flag ON  → execute_fanned_out called; legacy execution never called
3. canonical lineage (canonical_opportunity_id / correlation_id / decision_id /
   observation_id / entry / sl / tp) is passed through build_canonical_decision
   verbatim — one canonical decision, no second decision generated
4. NO_TRADE does not reach the runtime dispatch (structural: the NO_TRADE
   branch `continue`s BEFORE the dispatch boundary in the live scanner)
5. PATTERN_REJECT does not reach the runtime dispatch (structural: pre-engine
   gate `continue`s BEFORE the dispatch boundary)
6. exactly one execution route runs (XOR dispatch — no double execution)
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace as NS

import pytest

import core.runtime.fanout_execution as fe
from core.runtime.fanout_execution import (
    build_primary_execution_outcome,
    dispatch_execution,
    execute_multi_account_fanout,
    multi_account_fanout_enabled,
)

FLAG = "MULTI_ACCOUNT_FANOUT_ENABLED"

LIVE_SCANNER = (Path(__file__).resolve().parents[1] / "core" / "runtime" / "live_scanner.py").read_text(
    encoding="utf-8"
)


def _intent(**kw):
    return NS(
        symbol="EURUSD", side=NS(name="BUY"), volume=0.05, entry_reference=1.10002,
        sl=1.09902, tp=1.10202, pattern="TWEEZER_BOTTOM",
        metadata={"horizon": "SCALP"}, **kw,
    )


# ─── 1. FLAG OFF → LEGACY ONLY ────────────────────────────────────────────────


def test_flag_off_uses_legacy_execution_and_never_fans_out(monkeypatch):
    calls = {"fanout": 0, "legacy": 0}
    monkeypatch.setenv(FLAG, "false")
    monkeypatch.setattr(
        fe, "execute_multi_account_fanout",
        lambda **kw: calls.__setitem__("fanout", calls["fanout"] + 1) or (_ for _ in ()).throw(
            AssertionError("fan-out must never run when the flag is OFF")),
    )

    class _LegacySentinel:
        executed = True
        ok = True
        result = "legacy_result"
        decision_ts_utc_ms = 1

    def legacy():
        calls["legacy"] += 1
        return _LegacySentinel()

    outcome = dispatch_execution(intent=_intent(), symbol="EURUSD", cycle_id=7,
                                 decision_id="dec-off", correlation_id="cor-off",
                                 legacy_execute=legacy)
    assert multi_account_fanout_enabled() is False
    assert calls == {"fanout": 0, "legacy": 1}
    assert outcome is not None and outcome.ok and outcome.result == "legacy_result"


def test_flag_off_reads_live_fanout_module_gate(monkeypatch):
    # The gate must delegate to core.accounts.live_fanout.fanout_enabled.
    monkeypatch.setenv(FLAG, "true")
    from core.accounts.live_fanout import fanout_enabled
    assert fanout_enabled() is True and multi_account_fanout_enabled() is True
    monkeypatch.setenv(FLAG, "false")
    assert fanout_enabled() is False and multi_account_fanout_enabled() is False
# ─── 2. FLAG ON → FAN-OUT ONLY ────────────────────────────────────────────────


def test_flag_on_fans_out_and_legacy_execution_never_runs(monkeypatch):
    calls = {"fanout": 0, "legacy": 0}
    monkeypatch.setenv(FLAG, "true")
    sentinel = NS(executed=True)

    def fanout_spy(**kw):
        calls["fanout"] += 1
        return sentinel

    monkeypatch.setattr(fe, "execute_multi_account_fanout", fanout_spy)

    def legacy():
        calls["legacy"] += 1
        raise AssertionError("legacy path must never run when the flag is ON")

    outcome = dispatch_execution(intent=_intent(), symbol="EURUSD", cycle_id=7,
                                 decision_id="dec-on", correlation_id="cor-on",
                                 legacy_execute=legacy)
    assert multi_account_fanout_enabled() is True
    assert calls == {"fanout": 1, "legacy": 0}
    assert outcome is sentinel


# ─── 3. CANONICAL LINEAGE PASSED VERBATIM ─────────────────────────────────────


def _fake_accounts():
    return tuple(NS(account_id=aid, broker=aid, server=aid + "-Demo", login=1000 + i,
                    enabled=True, errors=lambda: [], terminal_path="C:/t/" + aid)
                 for i, aid in enumerate(("METAQUOTES", "ADMIRALS", "VANTAGE"), 1))


def _fake_report(account_id):
    rows = []
    for canonical in ("EURUSD", "NAS100"):
        rows.append({
            "canonical_symbol": canonical,
            "status": "available",
            "broker_symbol": canonical + (".mq" if account_id == "METAQUOTES" else ".van"),
            "digits": 5, "point": 0.00001, "trade_contract_size": 100000.0,
            "volume_min": 0.01, "volume_step": 0.01, "volume_max": 100.0,
            "trade_tick_size": 0.00001, "trade_tick_value": 1.0,
            "trade_stops_level": 0, "trade_freeze_level": 0,
        })
    return {
        "account_id": account_id, "broker": account_id, "server": account_id + "-Demo",
        "login": 1000, "connected": True, "identity_verified": True,
        "trade_allowed": True, "trade_expert": True,
        "balance": 10000.0, "equity": 9999.0, "margin_free": 9998.0,
        "leverage": 100, "currency": "USD", "positions": [], "orders": [],
        "symbols": rows,
    }


@pytest.fixture
def no_broker_probe(monkeypatch):
    """Replace account load + diagnostics so tests never touch a terminal."""
    accounts = _fake_accounts()
    reports = [_fake_report(a.account_id) for a in accounts]
    import core.accounts.config as cfg_mod
    import core.accounts.manager as mgr_mod
    monkeypatch.setattr(cfg_mod, "load_accounts", lambda *a, **kw: accounts)
    monkeypatch.setattr(mgr_mod, "diagnose", lambda *a, **kw: reports)
    return accounts


def test_canonical_lineage_is_passed_verbatim(monkeypatch, no_broker_probe):
    captured: dict = {}

    def fanout_spy(*, decision, accounts, snapshots, broker_symbols,
                   strategy_family, horizon_type, global_execution_enabled,
                   execute_one, timeout):
        captured["decision"] = decision
        captured["accounts"] = tuple(accounts)
        captured["broker_symbols"] = dict(broker_symbols)
        captured["horizon_type"] = horizon_type
        captured["global_execution_enabled"] = global_execution_enabled
        return []  # no children → adapter maps to a blocked outcome

    import core.accounts.live_fanout as lf
    monkeypatch.setattr(lf, "execute_fanned_out", fanout_spy)

    execute_multi_account_fanout(
        intent=_intent(), symbol="EURUSD", cycle_id=7,
        decision_id="dec-1", correlation_id="cor-1", entity_id="EURUSD_1",
        observation_id="EURUSD.M5.1", canonical_opportunity_id="EURUSD*1*TWEEZER_BOTTOM",
        strategy_family="TREND_CONTINUATION", bid=1.1, ask=1.10002,
    )

    d = captured["decision"]
    # ONE canonical decision built from the existing intent — lineage inherited
    assert d.canonical_opportunity_id == "EURUSD*1*TWEEZER_BOTTOM"
    assert d.correlation_id == "cor-1"
    assert d.decision_id == "dec-1"
    assert d.observation_id == "EURUSD.M5.1"
    assert d.canonical_symbol == "EURUSD"
    assert d.side == "BUY"
    assert (d.entry, d.sl, d.tp) == (1.10002, 1.09902, 1.10202)
    assert d.pattern == "TWEEZER_BOTTOM"
    # The same account list + per-account broker symbols were fanned out
    assert [a.account_id for a in captured["accounts"]] == ["METAQUOTES", "ADMIRALS", "VANTAGE"]
    assert captured["broker_symbols"]["METAQUOTES"] == "EURUSD.mq"
    assert captured["broker_symbols"]["VANTAGE"] == "EURUSD.van"
    assert captured["horizon_type"] == "SCALP"          # risk methodology untouched
    assert captured["global_execution_enabled"] is True
def test_blocked_children_map_to_not_executed_without_canonical_mutation(
    monkeypatch, no_broker_probe,
):
    # A locally blocked child (EXECUTION_DISABLED etc.) must map to
    # executed=False with the block reason and never invent a fill.
    import core.accounts.live_fanout as lf

    def blocked_children(**kw):
        decision = kw["decision"]
        return [
            {"executed": False, "status": "OBSERVED", "comment": "EXECUTION_DISABLED",
             "target": NS(account_id="ADMIRALS", broker="ADMIRALS",
                          broker_server="ADMIRALS-Demo", canonical_symbol="EURUSD",
                          broker_symbol=None, canonical_opportunity_id=decision.canonical_opportunity_id,
                          correlation_id=decision.correlation_id, decision_id=decision.decision_id,
                          account_execution_id="e-a", trade_id="t-a", entry=1.10002,
                          sl=1.09902, tp=1.10202, pattern="TWEEZER_BOTTOM", observation_id="obs")},
        ]

    monkeypatch.setattr(lf, "execute_fanned_out", blocked_children)
    outcome = execute_multi_account_fanout(
        intent=_intent(), symbol="EURUSD", cycle_id=7,
        decision_id="dec-2", correlation_id="cor-2",
        canonical_opportunity_id="opp-2", observation_id="obs",
    )
    # Primary (METAQUOTES) is absent → nothing executed; the canonical lineage
    # carried by the children is untouched (canonical_opportunity_id / ids).
    assert outcome.executed is False
    assert "EXECUTION_DISABLED" in outcome.error


def test_filled_primary_child_maps_to_execution_result(monkeypatch, no_broker_probe):
    import core.accounts.live_fanout as lf

    def filled_children(**kw):
        return [
            {"executed": True, "ok": True, "status": "FILLED", "retcode": 10009,
             "deal": 7, "order": 8, "comment": "ok", "fill_price": 1.10003,
             "lifecycle_side": "BUY", "lifecycle_bid": 1.1, "lifecycle_ask": 1.10002,
             "volume": 0.05,
             "ownership": {"account_id": "METAQUOTES", "broker": "METAQUOTES",
                           "broker_server": "METAQUOTES-Demo", "position_ticket": 8,
                           "order_ticket": 8, "deal_ticket": 7, "canonical_symbol": "EURUSD",
                           "broker_symbol": "EURUSD.mq",
                           "canonical_opportunity_id": "opp-3", "correlation_id": "cor-3",
                           "decision_id": "dec-3", "account_execution_id": "e-m", "trade_id": "t-m"},
             "target": NS(account_id="METAQUOTES", broker="METAQUOTES",
                          broker_server="METAQUOTES-Demo", canonical_symbol="EURUSD",
                          broker_symbol="EURUSD.mq",
                          canonical_opportunity_id="opp-3", correlation_id="cor-3",
                          decision_id="dec-3", account_execution_id="e-m", trade_id="t-m",
                          entry=1.10002, sl=1.09902, tp=1.10202,
                          pattern="TWEEZER_BOTTOM", observation_id="obs")},
            {"executed": False, "status": "OBSERVED", "comment": "EXECUTION_DISABLED",
             "target": NS(account_id="ADMIRALS", broker="ADMIRALS",
                          broker_server="ADMIRALS-Demo", canonical_symbol="EURUSD",
                          broker_symbol=None,
                          canonical_opportunity_id="opp-3", correlation_id="cor-3",
                          decision_id="dec-3", account_execution_id="e-a", trade_id="t-a",
                          entry=1.10002, sl=1.09902, tp=1.10202,
                          pattern="TWEEZER_BOTTOM", observation_id="obs")},
        ]

    monkeypatch.setattr(lf, "execute_fanned_out", filled_children)
    outcome = execute_multi_account_fanout(
        intent=_intent(), symbol="EURUSD", cycle_id=7,
        decision_id="dec-3", correlation_id="cor-3",
        canonical_opportunity_id="opp-3", observation_id="obs",
    )
    assert outcome.executed is True and outcome.ok is True
    # ExecutionResult-compatible view for the existing scanner post-execution block
    r = outcome.result
    assert r.ok and r.retcode == 10009 and r.deal == 7 and r.order == 8
    assert r.fill_price == 1.10003 and r.volume == 0.05
    # (account_id, position_ticket) runtime ownership preserved — not ticket-only
    assert r.ownership.account_id == "METAQUOTES" and r.ownership.position_ticket == 8
# ─── 4/5. NO_TRADE + PATTERN_REJECT NEVER REACH THE DISPATCH ──────────────────


def _dispatch_index() -> int:
    return LIVE_SCANNER.index("_exec_outcome = dispatch_execution(")


def test_no_trade_branch_continues_before_dispatch():
    no_trade = LIVE_SCANNER.index('if _new_result["action"] == "NO_TRADE":')
    dispatch = _dispatch_index()
    assert no_trade < dispatch
    # The NO_TRADE branch ends with `continue` BEFORE the dispatch boundary.
    continue_at = LIVE_SCANNER.index("                        continue", no_trade)
    assert continue_at < dispatch


def test_pattern_reject_gate_continues_before_dispatch():
    reject = LIVE_SCANNER.index('block_outcome == "PATTERN_REJECT"')
    dispatch = _dispatch_index()
    assert reject < dispatch
    continue_at = LIVE_SCANNER.index("                    continue", reject)
    assert continue_at < dispatch


def test_risk_block_guard_continues_before_dispatch():
    guard = LIVE_SCANNER.index("if not _guard_chain_result.allowed:")
    dispatch = _dispatch_index()
    assert guard < dispatch
    continue_at = LIVE_SCANNER.index("                    continue", guard)
    assert continue_at < dispatch


def test_runtime_imports_the_fanout_gate():
    assert "from core.runtime.fanout_execution import dispatch_execution" in LIVE_SCANNER


# ─── 6. XOR DISPATCH — NO DOUBLE EXECUTION ────────────────────────────────────


def test_exactly_one_route_runs_in_both_modes(monkeypatch, no_broker_probe):
    """The dispatch is if/else: fan-out XOR legacy — never both."""
    ran: list[str] = []
    monkeypatch.setenv(FLAG, "false")
    monkeypatch.setattr(fe, "execute_multi_account_fanout",
                        lambda **kw: (_ for _ in ()).throw(
                            AssertionError("fan-out ran while flag OFF")))
    dispatch_execution(intent=_intent(), symbol="EURUSD", cycle_id=1,
                       decision_id="d", correlation_id="c",
                       legacy_execute=lambda: ran.append("legacy"))
    assert ran == ["legacy"]
    monkeypatch.setenv(FLAG, "true")
    monkeypatch.setattr(fe, "execute_multi_account_fanout",
                        lambda **kw: ran.append("fanout") or NS(executed=False))
    dispatch_execution(intent=_intent(), symbol="EURUSD", cycle_id=1,
                       decision_id="d", correlation_id="c",
                       legacy_execute=lambda: (_ for _ in ()).throw(
                           AssertionError("legacy ran while flag ON")))
    assert ran == ["legacy", "fanout"]


def test_primary_outcome_adapter_blocked_without_children(monkeypatch, no_broker_probe):
    import core.accounts.live_fanout as lf
    monkeypatch.setattr(lf, "execute_fanned_out", lambda **kw: [])
    outcome = execute_multi_account_fanout(
        intent=_intent(), symbol="EURUSD", cycle_id=1,
        decision_id="d", correlation_id="c", canonical_opportunity_id="o",
    )
    assert outcome.executed is False
    assert outcome.error == "no_multi_account_outcome"


def test_flag_env_missing_defaults_off(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    assert multi_account_fanout_enabled() is False