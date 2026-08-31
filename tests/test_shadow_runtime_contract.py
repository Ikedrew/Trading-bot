"""
NEW Shadow Runtime — contract / integration tests.

Covers the authorised contract:
    PLAN → OPEN → PROGRESS* → CLOSE → RECOVERY
plus identity, horizon completeness, construction provenance, live/shadow
isolation, simulation semantics (exact-fill, SL_FIRST, horizon timeouts,
MFE/MAE/R), DATA_GAP honesty, durable watermark, timestamp integrity and
three-dimensional versioning.

Runs fully isolated: writes ONLY to tmp_path; never touches legacy
logs/shadow_trades/ or any live component.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.shadow.persistence import ShadowEventWriter, load_events
from core.shadow.runtime import ShadowRuntime
from core.identity.canonical import mint_observation_id

SYMBOL = "EURUSD"
ROOT_ID = "EURUSD*1784800000*TWEEZER_TOP"
OBSERVATION_ID = mint_observation_id(
    symbol=SYMBOL, bar_time=1_784_800_000, timeframe="M5"
)


def _ctx(
    *,
    cycle_id: int = 42,
    bar_time: int = 1_784_800_000,
    direction: str = "SELL",
    bid: float = 1.10000,
    ask: float = 1.10002,
    eligible=("INTRADAY",),
    m15_resistance: float | None = 1.10100,
    m15_support: float | None = None,
) -> dict:
    structure = {
        "m5_candle_high": 1.10050,
        "m5_candle_low": 1.09980,
        "m15_nearest_support": m15_support,
        "m15_nearest_resistance": m15_resistance,
        "h1_last_swing_high": None,
        "h1_last_swing_low": None,
    }
    return {
        "canonical_opportunity_id": ROOT_ID,
        "observation_id": OBSERVATION_ID,
        "entity_id": f"{SYMBOL}_1784800000",
        "symbol": SYMBOL,
        "cycle_id": cycle_id,
        "bar_time_raw": bar_time,
        "direction": direction,
        "pattern": "TWEEZER_TOP",
        "strategy": "REVERSAL",
        "score": 0.61,
        "regime": "RANGE",
        "h4_regime": "RANGE",
        "h1_bias": "BEARISH",
        "market_phase": "PULLBACK",
        "market_phase_confidence": 0.55,
        "bid": bid,
        "ask": ask,
        "structure": structure,
        "eligible_horizons": list(eligible),
        "horizon_assessments": [
            {"horizon": hz, "confidence": 0.4, "reasoning": "test-reason"}
            for hz in ("SCALP", "INTRADAY", "EXTENDED")
        ],
        "v10_action": "NO_TRADE",
        "v10_rejection_stage": "risk",
        "v10_selected_horizon": "",
    }


@pytest.fixture()
def env(tmp_path):
    writer = ShadowEventWriter(base_dir=str(tmp_path))
    rt = ShadowRuntime(writer=writer)

    class Env:
        pass

    e = Env()
    e.writer = writer
    e.rt = rt
    e.base = str(tmp_path)
    e.events = lambda: load_events(str(tmp_path))
    yield e


# ─── Identity & lineage ───────────────────────────────────────────────────────

def test_every_event_carries_canonical_root_and_unique_shadow_ids(env):
    env.rt.handle_opportunity(_ctx())
    events = env.events()
    assert events, "no events written"
    for ev in events:
        assert ev["canonical_opportunity_id"] == ROOT_ID
        assert ev["observation_id"] == OBSERVATION_ID
    opens = [e for e in events if e["event_type"] == "OPEN"]
    tids = {e["shadow_trade_id"] for e in opens}
    assert len(tids) == len(opens) and tids
    assert all(t.startswith("nshadow_") for t in tids)


def test_siblings_share_root_and_differ_by_horizon(env):
    # All three horizons constructible (H1 swings supplied for EXTENDED).
    ctx = _ctx(eligible=("SCALP", "INTRADAY", "EXTENDED"), m15_support=1.09900)
    ctx["structure"]["h1_last_swing_high"] = 1.10400
    ctx["structure"]["h1_last_swing_low"] = 1.09500
    env.rt.handle_opportunity(ctx)
    opens = [e for e in env.events() if e["event_type"] == "OPEN"]
    assert len(opens) == 3
    assert {e["canonical_opportunity_id"] for e in opens} == {ROOT_ID}
    assert {e["identity"]["trade_horizon"] for e in opens} == {
        "SCALP", "INTRADAY", "EXTENDED",
    }


def test_no_root_no_simulation(env):
    ctx = _ctx()
    ctx["canonical_opportunity_id"] = ""
    env.rt.handle_opportunity(ctx)
    assert env.events() == []


# ─── Horizon completeness (PLAN) ─────────────────────────────────────────────

def test_plan_lists_all_three_horizons_even_when_zero_constructible(env):
    env.rt.handle_opportunity(_ctx(eligible=()))
    plans = [e for e in env.events() if e["event_type"] == "PLAN"]
    assert len(plans) == 1
    plan = plans[0]
    assert plan["canonical_opportunity_id"] == ROOT_ID
    assert plan["observation_id"] == OBSERVATION_ID
    states = {h["horizon"]: h["state"] for h in plan["horizons"]}
    assert set(states) == {"SCALP", "INTRADAY", "EXTENDED"}
    assert all(s == "NOT_ELIGIBLE" for s in states.values())
    ne = plan["horizons"][0]
    assert ne["confidence"] == 0.4 and ne["reasoning"] == "test-reason"
    assert plan["constructed_count"] == 0
    assert not [e for e in env.events() if e["event_type"] == "OPEN"]


def test_unconstructible_horizon_recorded_with_missing_dependency(env):
    env.rt.handle_opportunity(_ctx(eligible=("INTRADAY",), m15_resistance=None))
    plan = [e for e in env.events() if e["event_type"] == "PLAN"][0]
    intraday = [h for h in plan["horizons"] if h["horizon"] == "INTRADAY"][0]
    assert intraday["state"] == "ELIGIBLE_BUT_UNCONSTRUCTIBLE"
    assert intraday["missing_structure"][0] == "m15_nearest_resistance"


# ─── Construction provenance ─────────────────────────────────────────────────

def test_open_preserves_full_construction_provenance(env):
    env.rt.handle_opportunity(_ctx())
    open_ev = [e for e in env.events() if e["event_type"] == "OPEN"][0]
    assert open_ev["canonical_opportunity_id"] == ROOT_ID
    assert open_ev["observation_id"] == OBSERVATION_ID
    c = open_ev["construction"]
    assert c["sl_source"] == "M15_STRUCTURE"
    assert isinstance(c["reasoning"], list) and len(c["reasoning"]) >= 2
    assert c["intended_rr"] == 3.0
    assert abs(c["entry_price"] - 1.10000) < 1e-9          # SELL → BID basis
    assert open_ev["market_entry_facts"]["entry_price_basis"] == "BID"
    assert c["structure_inputs"]["m15_nearest_resistance"] == 1.10100
    assert "TP:" in c["tp_construction_rule"]
    assert open_ev["simulation_assumptions"]["timeout_bars"] == 96
    lf = open_ev["live_facts"]
    assert lf["v10_action"] == "NO_TRADE"                  # inherited LIVE fact
    assert lf["horizon_selection_status"] == "ALTERNATIVE"


def test_scalp_uses_m5_candle_geometry_provenance(env):
    env.rt.handle_opportunity(_ctx(eligible=("SCALP",)))
    open_ev = [e for e in env.events() if e["event_type"] == "OPEN"][0]
    assert open_ev["construction"]["sl_source"] == "M5_CANDLE_GEOMETRY"
    assert open_ev["construction"]["intended_rr"] == 2.0


# ─── Simulation semantics ─────────────────────────────────────────────────────

def _bars_after(bar_time, n, high, low, close):
    return [(bar_time + 300 * (i + 1), high, low, close) for i in range(n)]


def test_exact_fill_sl_first_when_both_touched(env):
    env.rt.handle_opportunity(_ctx())
    cons = [e for e in env.events() if e["event_type"] == "OPEN"][0]["construction"]
    sl, tp = cons["stop_loss"], cons["take_profit"]
    assert sl > tp  # SELL geometry sanity
    t0 = _ctx()["bar_time_raw"]
    # Single closed bar touches BOTH levels → SL_FIRST wins, exact fill at SL.
    env.rt.evaluate_bar(symbol=SYMBOL, bar_time=t0 + 300,
                        bar_high=sl + 0.001, bar_low=tp - 0.001,
                        bar_close=(sl + tp) / 2)
    closes = [e for e in env.events() if e["event_type"] == "CLOSE"]
    assert len(closes) == 1
    assert closes[0]["exit_reason"] == "stop_loss"
    assert closes[0]["exit_price"] == pytest.approx(sl)


def test_exact_fill_tp(env):
    env.rt.handle_opportunity(_ctx())
    opens = [e for e in env.events() if e["event_type"] == "OPEN"][0]["construction"]
    sl, tp = opens["stop_loss"], opens["take_profit"]
    t0 = _ctx()["bar_time_raw"]
    env.rt.evaluate_bar(symbol=SYMBOL, bar_time=t0 + 300,
                        bar_high=sl - 0.0005, bar_low=tp - 0.001, bar_close=tp)
    close = [e for e in env.events() if e["event_type"] == "CLOSE"][0]
    assert close["exit_reason"] == "take_profit"
    assert close["exit_price"] == pytest.approx(tp)


def test_horizon_specific_timeout_scalp_9_bars(env):
    env.rt.handle_opportunity(_ctx(eligible=("SCALP",)))
    tid = f"nshadow_42_{SYMBOL}_SCALP"
    t0 = _ctx()["bar_time_raw"]
    for bt, hi, lo, cl in _bars_after(t0, 9, 1.10040, 1.09990, 1.10000):
        env.rt.evaluate_bar(symbol=SYMBOL, bar_time=bt,
                            bar_high=hi, bar_low=lo, bar_close=cl)
        snap = env.rt.snapshot(tid)
        assert snap is None or snap["lifecycle"]["bars_elapsed"] <= 9
    closes = [e for e in env.events() if e["event_type"] == "CLOSE"]
    assert len(closes) == 1
    assert closes[0]["exit_reason"] == "timeout"
    assert closes[0]["bars_held"] == 9


def test_mfe_mae_and_progression_in_close(env):
    env.rt.handle_opportunity(_ctx())
    cons = [e for e in env.events() if e["event_type"] == "OPEN"][0]["construction"]
    tp = cons["take_profit"]
    t0 = _ctx()["bar_time_raw"]
    for bt, hi, lo, cl in [
        (t0 + 300, 1.10020, 1.09985, 1.10000),
        (t0 + 600, 1.10010, 1.09600, 1.09750),   # low pierces TP → exact fill at TP
    ]:
        env.rt.evaluate_bar(symbol=SYMBOL, bar_time=bt, bar_high=hi, bar_low=lo, bar_close=cl)
    close = [e for e in env.events() if e["event_type"] == "CLOSE"][0]
    assert close["exit_reason"] == "take_profit"
    oc = close["outcome"]
    assert oc["mfe_r"] > 0 and oc["mae_r"] > 0
    assert oc["pnl_r_multiple"] == pytest.approx(3.0, abs=0.01)
    assert len(close["trade_state_progression"]) == 2


def test_data_gap_recorded_never_fabricated(env):
    env.rt.handle_opportunity(_ctx())
    t0 = _ctx()["bar_time_raw"]
    env.rt.evaluate_bar(symbol=SYMBOL, bar_time=t0 + 300,
                        bar_high=1.10010, bar_low=1.09990, bar_close=1.10000)
    env.rt.evaluate_bar(symbol=SYMBOL, bar_time=t0 + 1200,   # two bars missing
                        bar_high=1.10010, bar_low=1.09990, bar_close=1.10000)
    progresses = [e for e in env.events() if e["event_type"] == "PROGRESS"]
    assert progresses and progresses[-1]["lifecycle"]["data_gaps"], "gap not recorded"


def test_watermark_prevents_duplicate_evaluation(env):
    env.rt.handle_opportunity(_ctx())
    tid = f"nshadow_42_{SYMBOL}_INTRADAY"
    t0 = _ctx()["bar_time_raw"]
    for _ in range(2):  # same closed bar delivered twice
        env.rt.evaluate_bar(symbol=SYMBOL, bar_time=t0 + 300,
                            bar_high=1.10010, bar_low=1.09990, bar_close=1.10000)
    snap = env.rt.snapshot(tid)
    assert snap["lifecycle"]["bars_elapsed"] == 1


# ─── Durability / recovery / legacy isolation ────────────────────────────────

def test_recovery_reopens_active_and_continues_without_duplicate(env):
    # Cross ONE checkpoint boundary so durable mid-life state exists
    # (checkpoint_interval = 12 bars → PROGRESS at bar 12).
    env.rt.handle_opportunity(_ctx())
    tid = f"nshadow_42_{SYMBOL}_INTRADAY"
    t0 = _ctx()["bar_time_raw"]
    for bt, hi, lo, cl in _bars_after(t0, 12, 1.10010, 1.09990, 1.10000):
        env.rt.evaluate_bar(symbol=SYMBOL, bar_time=bt,
                            bar_high=hi, bar_low=lo, bar_close=cl)

    rt2 = ShadowRuntime(writer=env.writer)   # "restart": recover from stream only
    assert tid in rt2.active_ids()
    snap = rt2.snapshot(tid)
    assert snap["canonical_opportunity_id"] == ROOT_ID
    assert snap["observation_id"] == OBSERVATION_ID
    assert snap["lifecycle"]["bars_elapsed"] == 12
    assert snap["lifecycle"]["last_evaluated_bar_time"] == t0 + 3600

    # Re-deliver the last evaluated bar → watermark must suppress it.
    rt2.evaluate_bar(symbol=SYMBOL, bar_time=t0 + 3600,
                     bar_high=1.10010, bar_low=1.09990, bar_close=1.10000)
    assert rt2.snapshot(tid)["lifecycle"]["bars_elapsed"] == 12

    # Next new bar proceeds normally: low pierces TP → exact-fill CLOSE.
    rt2.evaluate_bar(symbol=SYMBOL, bar_time=t0 + 3900,
                     bar_high=1.10010, bar_low=1.09600, bar_close=1.09750)
    closes = [e for e in env.events() if e["event_type"] == "CLOSE"]
    assert len(closes) == 1
    assert closes[0]["bars_held"] == 13
    rt3 = ShadowRuntime(writer=env.writer)
    assert rt3.active_ids() == []            # CLOSE terminates recovery


def test_recovered_open_progress_and_close_preserve_original_observation_id(env):
    env.rt.handle_opportunity(_ctx())
    tid = f"nshadow_42_{SYMBOL}_INTRADAY"
    t0 = _ctx()["bar_time_raw"]

    rt2 = ShadowRuntime(writer=env.writer)
    assert rt2.snapshot(tid)["observation_id"] == OBSERVATION_ID

    for bt, hi, lo, cl in _bars_after(t0, 12, 1.10010, 1.09990, 1.10000):
        rt2.evaluate_bar(
            symbol=SYMBOL,
            bar_time=bt,
            bar_high=hi,
            bar_low=lo,
            bar_close=cl,
        )
    progress = [e for e in env.events() if e["event_type"] == "PROGRESS"][-1]
    assert progress["observation_id"] == OBSERVATION_ID
    assert progress["canonical_opportunity_id"] == ROOT_ID

    rt2.evaluate_bar(
        symbol=SYMBOL,
        bar_time=t0 + 3900,
        bar_high=1.10010,
        bar_low=1.09600,
        bar_close=1.09750,
    )
    close = [e for e in env.events() if e["event_type"] == "CLOSE"][-1]
    assert close["observation_id"] == OBSERVATION_ID
    assert close["canonical_opportunity_id"] == ROOT_ID


def test_legacy_shadow_data_is_never_read(tmp_path):
    foreign = tmp_path / "foreign_tree"
    foreign.mkdir()
    (foreign / "x.jsonl").write_text('{"event_type":"OPEN"}\n')
    domain = tmp_path / "domain"
    writer = ShadowEventWriter(base_dir=str(domain))
    rt = ShadowRuntime(writer=writer)
    rt.recover()                             # reads ONLY the domain base_dir
    assert rt.active_ids() == []
    assert load_events(str(domain)) == []


# ─── Timestamp integrity & versioning ────────────────────────────────────────

def test_market_time_raw_verbatim_utc_derived_wall_separate(env, monkeypatch):
    import core.shadow.runtime as rtmod

    monkeypatch.setattr(rtmod, "get_broker_offset_seconds", lambda: 10800)
    ctx = _ctx()
    ctx["bar_time_raw"] = 1_784_800_500                 # raw broker seconds
    env.rt.handle_opportunity(ctx)
    open_ev = [e for e in env.events() if e["event_type"] == "OPEN"][0]
    assert open_ev["opportunity_market_time"] == 1_784_800_500          # untouched
    assert open_ev["opportunity_market_time_utc_epoch_s"] == 1_784_800_500 - 10_800
    assert open_ev["opportunity_market_time_utc_iso8601"].endswith("Z")
    assert open_ev["broker_offset_seconds"] == 10800
    assert isinstance(open_ev["recorded_at_utc_ms"], int)
    assert open_ev["recorded_at_utc_ms"] > 1_700_000_000_000            # wall clock
    assert open_ev["entry_market_time"] == 1_784_800_500


def test_every_event_carries_three_version_dimensions(env):
    env.rt.handle_opportunity(_ctx())
    env.rt.evaluate_bar(symbol=SYMBOL, bar_time=_ctx()["bar_time_raw"] + 300,
                        bar_high=1.10010, bar_low=1.09990, bar_close=1.10000)
    for ev in env.events():
        assert ev["schema_version"] == "shadow_runtime_v1"
        assert ev["construction_model_version"] == "construction_v1"
        assert ev["simulation_model_version"] == "simulation_v1"


def test_full_lifecycle_plan_open_progress_close(env):
    env.rt.handle_opportunity(_ctx())                    # PLAN + OPEN
    t0 = _ctx()["bar_time_raw"]
    for bt, hi, lo, cl in _bars_after(t0, 12, 1.10010, 1.09990, 1.10000):
        env.rt.evaluate_bar(symbol=SYMBOL, bar_time=bt,
                            bar_high=hi, bar_low=lo, bar_close=cl)
    kinds = [e["event_type"] for e in env.events()]
    assert kinds.count("PLAN") == 1
    assert kinds.count("OPEN") == 1
    assert kinds.count("PROGRESS") >= 1                  # checkpoint at bar 12
    prog = [e for e in env.events() if e["event_type"] == "PROGRESS"][-1]
    assert prog["canonical_opportunity_id"] == ROOT_ID
    assert prog["observation_id"] == OBSERVATION_ID
    assert prog["lifecycle"]["bars_elapsed"] == 12
    assert prog["lifecycle"]["last_evaluated_bar_time"] == t0 + 3600


def test_close_preserves_observation_id(env):
    env.rt.handle_opportunity(_ctx())
    cons = [e for e in env.events() if e["event_type"] == "OPEN"][0]["construction"]
    env.rt.evaluate_bar(
        symbol=SYMBOL,
        bar_time=_ctx()["bar_time_raw"] + 300,
        bar_high=cons["stop_loss"] + 0.001,
        bar_low=cons["take_profit"] - 0.001,
        bar_close=cons["stop_loss"],
    )
    close = [e for e in env.events() if e["event_type"] == "CLOSE"][0]
    assert close["canonical_opportunity_id"] == ROOT_ID
    assert close["observation_id"] == OBSERVATION_ID


def test_missing_direction_records_plan_without_open(env):
    ctx = _ctx(direction="", eligible=("SCALP",))
    env.rt.handle_opportunity(ctx)
    events = env.events()
    plan = [e for e in events if e["event_type"] == "PLAN"][0]
    assert plan["direction"] == ""
    assert plan["entry_price_basis"] == ""
    assert plan["constructed_count"] == 0
    assert not [e for e in events if e["event_type"] == "OPEN"]
    scalp = [h for h in plan["horizons"] if h["horizon"] == "SCALP"][0]
    assert scalp["state"] == "ELIGIBLE_BUT_UNCONSTRUCTIBLE"
    assert scalp["missing_structure"] == ["direction"]


def test_duplicate_plan_suppressed_per_root(env):
    env.rt.handle_opportunity(_ctx())
    env.rt.handle_opportunity(_ctx())                    # same opportunity-cycle
    assert len([e for e in env.events() if e["event_type"] == "PLAN"]) == 1
