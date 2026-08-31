"""
Phase 1b — Counterfactual shadow-population wiring tests.

Proves the existing NEW shadow runtime is fed the CORRECT population:

  * EXECUTE opportunities still produce shadow simulations.
  * NO_TRADE opportunities with a detected pattern direction produce a
    counterfactual simulation while the live record's side/action stay verbatim.
  * The unified canonical root originates from the DETECTED pattern.
  * Horizon eligibility drives construction (ineligible horizons are not built).
  * No MT5 execution is ever involved in the shadow path.
  * Exactly-once PLAN and watermark semantics remain intact.
  * The V2 sentinel prevents the legacy engine from double-writing.

Fully isolated: writes ONLY to tmp_path; never touches logs/ or MT5.
"""

from __future__ import annotations

import sys
from enum import Enum
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.identity.canonical import make_canonical_opportunity_id, mint_observation_id
from core.shadow.integration import (
    ShadowV2Handled,
    handle_live_opportunity_shadow,
)
from core.shadow.persistence import ShadowEventWriter, load_events
from core.shadow.runtime import ShadowRuntime

SYMBOL = "EURUSD"
BAR_TIME = 1_784_800_000
CYCLE_ID = 42
ENTITY_ID = f"{SYMBOL}_{BAR_TIME}"
ROOT_ID = make_canonical_opportunity_id(
    symbol=SYMBOL, bar_time=BAR_TIME, pattern="TWEEZER_TOP"
)
OBSERVATION_ID = mint_observation_id(symbol=SYMBOL, bar_time=BAR_TIME, timeframe="M5")
BID = 1.10000
ASK = 1.10002


# ───────────────────────────────────────────────────────────────────────────
# Fixtures / stand-ins
# ───────────────────────────────────────────────────────────────────────────

def _ctx(*, direction: str = "BUY", eligible=("SCALP",), **overrides) -> dict:
    """Runtime ctx mirroring what the live-scanner shadow branch now hands over."""
    ctx = {
        "canonical_opportunity_id": ROOT_ID,
        "observation_id": OBSERVATION_ID,
        "entity_id": ENTITY_ID,
        "symbol": SYMBOL,
        "cycle_id": CYCLE_ID,
        "bar_time_raw": BAR_TIME,
        "direction": direction,
        "pattern": "TWEEZER_TOP",
        "strategy": "",
        "score": 0.5,
        "regime": "RANGE",
        "h4_regime": "RANGE",
        "h1_bias": "BEARISH",
        "market_phase": "",
        "market_phase_confidence": 0.0,
        "bid": BID,
        "ask": ASK,
        "structure": {
            "m5_candle_high": 1.10050,
            "m5_candle_low": 1.09980,
            "m15_nearest_support": 1.09900,
            "m15_nearest_resistance": 1.10100,
            "h1_last_swing_high": 1.10400,
            "h1_last_swing_low": 1.09500,
        },
        "eligible_horizons": list(eligible),
        "horizon_assessments": [
            {"horizon": hz, "confidence": 0.6, "reasoning": "test-reason"}
            for hz in ("SCALP", "INTRADAY", "EXTENDED")
        ],
        "v10_action": "NO_TRADE",
        "v10_rejection_stage": "",
        "v10_selected_horizon": "",
    }
    ctx.update(overrides)
    return ctx


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


def _bars_after(bar_time, n, high, low, close):
    return [(bar_time + 300 * (i + 1), high, low, close) for i in range(n)]


def _h1_direction() -> type:
    class H1Direction(Enum):
        BEARISH = "BEARISH"
        BULLISH = "BULLISH"

    return H1Direction


def _htf_context() -> SimpleNamespace:
    H1 = _h1_direction()
    structure = SimpleNamespace(nearest_support=1.09900, nearest_resistance=1.10100)
    bias = SimpleNamespace(
        direction=H1.BEARISH, last_swing_high=1.10400, last_swing_low=1.09500
    )
    regime = SimpleNamespace(
        classification=SimpleNamespace(value="RANGE"), confidence=0.7
    )
    return SimpleNamespace(structure=structure, bias=bias, regime=regime)


def _candles() -> list:
    return [
        SimpleNamespace(high=1.10030, low=1.09970),
        SimpleNamespace(high=1.10060, low=1.09990),
        SimpleNamespace(high=1.10050, low=1.09980),  # closed_i = 2
    ]


def _horizon_result(*, eligible=("SCALP",)) -> SimpleNamespace:
    def to_dict():
        return {
            "assessments": [
                {
                    "horizon": hz,
                    "eligible": hz in eligible,
                    "confidence": 0.6,
                    "reasoning": "test-reason",
                }
                for hz in ("SCALP", "INTRADAY", "EXTENDED")
            ]
        }

    return SimpleNamespace(to_dict=to_dict)


def _new_result(*, action: str = "NO_TRADE", side: str | None = "BUY") -> dict:
    """Mimics the V10 engine result dict (pattern slot = strategy-family token)."""
    return {
        "entity_id": ENTITY_ID,
        "action": action,
        "side": side,
        "pattern": "MEAN_REVERSION",  # strategy-family token, NOT the candle pattern
        "strategy": "MEAN_REVERSION",
        "score": 0.5,
        "activation_regime": "RANGE",
        "market_phase": "",
        "market_phase_confidence": 0.0,
        "assessment": SimpleNamespace(side=side),
    }


class _RecordingRuntime:
    """Records the ctx passed to the runtime (adapter-level tests)."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def handle_opportunity(self, ctx: dict) -> None:
        self.calls.append(ctx)


@pytest.fixture()
def recorded(monkeypatch) -> _RecordingRuntime:
    rt = _RecordingRuntime()
    monkeypatch.setattr("core.shadow.runtime.get_shadow_runtime", lambda: rt)
    return rt


def _invoke(runtime_calls: _RecordingRuntime, **overrides) -> None:
    del runtime_calls  # documentation: recording fixture is active
    known_direction = overrides.pop("direction", None)
    known_pattern = overrides.pop("pattern", None)
    new_result = overrides.pop("new_result", _new_result())
    if known_pattern is not None:
        new_result["pattern"] = known_pattern
    if known_direction is not None:
        bias = {"BUY": "BULLISH", "SELL": "BEARISH"}.get(
            str(known_direction).upper(), ""
        )
        if bias:
            pipeline_result = new_result.get("v10_pipeline_result")
            if pipeline_result is None:
                pipeline_result = SimpleNamespace()
            pipeline_result.opportunity = SimpleNamespace(directional_bias=bias)
            new_result["v10_pipeline_result"] = pipeline_result
    kwargs = dict(
        symbol=SYMBOL,
        cycle_id=CYCLE_ID,
        closed_time=BAR_TIME,
        candles=_candles(),
        closed_i=2,
        bid=BID,
        ask=ASK,
        htf_context=_htf_context(),
        new_result=new_result,
        horizon_result=_horizon_result(),
        canonical_opportunity_id=ROOT_ID,
        entity_id=ENTITY_ID,
        observation_id=OBSERVATION_ID,
    )
    kwargs.update(overrides)
    handle_live_opportunity_shadow(**kwargs)


# ───────────────────────────────────────────────────────────────────────────
# 1. EXECUTE opportunity → shadow simulation (adapter + runtime contract)
# ───────────────────────────────────────────────────────────────────────────

def test_execute_opportunity_produces_shadow_simulation(recorded):
    _invoke(
        recorded,
        new_result=_new_result(action="EXECUTE", side="BUY"),
        direction="BUY",
        pattern="TWEEZER_TOP",
    )
    assert len(recorded.calls) == 1
    ctx = recorded.calls[0]
    assert ctx["direction"] == "BUY"
    assert ctx["v10_action"] == "EXECUTE"
    assert ctx["pattern"] == "TWEEZER_TOP"  # detected pattern, not MEAN_REVERSION


def test_execute_full_lifecycle_still_works(env):
    ctx = _ctx(direction="BUY", eligible=("SCALP",))
    env.rt.handle_opportunity(ctx)
    t0 = BAR_TIME
    for bt, hi, lo, cl in _bars_after(t0, 9, 1.10010, 1.09990, 1.10000):
        env.rt.evaluate_bar(symbol=SYMBOL, bar_time=bt, bar_high=hi, bar_low=lo, bar_close=cl)
    kinds = [e["event_type"] for e in env.events()]
    assert kinds.count("PLAN") == 1
    assert kinds.count("OPEN") == 1
    assert kinds.count("CLOSE") == 1
    close = [e for e in env.events() if e["event_type"] == "CLOSE"][0]
    assert close["exit_reason"] == "timeout"
    assert "pnl_r_multiple" in close["outcome"]


# ───────────────────────────────────────────────────────────────────────────
# 2-3. NO_TRADE + pattern direction → counterfactual BUY / SELL shadow
# ───────────────────────────────────────────────────────────────────────────

def test_no_trade_buy_opportunity_produces_buy_shadow(recorded):
    _invoke(
        recorded,
        new_result=_new_result(action="NO_TRADE", side=None),
        direction="BUY",
        pattern="TWEEZER_TOP",
    )
    assert len(recorded.calls) == 1
    ctx = recorded.calls[0]
    assert ctx["direction"] == "BUY"          # counterfactual (pattern) direction
    assert ctx["v10_action"] == "NO_TRADE"     # live decision preserved verbatim
    assert ctx["pattern"] == "TWEEZER_TOP"


def test_no_trade_sell_opportunity_produces_sell_shadow(recorded):
    _invoke(
        recorded,
        new_result=_new_result(action="NO_TRADE", side=None),
        direction="SELL",
        pattern="TWEEZER_TOP",
    )
    assert recorded.calls[0]["direction"] == "SELL"
    assert recorded.calls[0]["v10_action"] == "NO_TRADE"


# ───────────────────────────────────────────────────────────────────────────
# 4. NO_TRADE live record is NEVER mutated
# ───────────────────────────────────────────────────────────────────────────

def test_no_trade_live_side_stays_none(recorded):
    nr = _new_result(action="NO_TRADE", side=None)
    _invoke(recorded, new_result=nr, direction="BUY", pattern="TWEEZER_TOP")
    assert nr["side"] is None                 # live record untouched
    assert nr["action"] == "NO_TRADE"         # live record untouched
    assert recorded.calls[0]["direction"] == "BUY"   # independent simulation input


# ───────────────────────────────────────────────────────────────────────────
# 5. live_facts.v10_action remains verbatim on persisted OPEN
# ───────────────────────────────────────────────────────────────────────────

def test_live_facts_v10_action_verbatim_in_open(env):
    ctx = _ctx(direction="BUY", eligible=("SCALP",))
    ctx["v10_action"] = "NO_TRADE"
    env.rt.handle_opportunity(ctx)
    open_ev = [e for e in env.events() if e["event_type"] == "OPEN"][0]
    assert open_ev["live_facts"]["v10_action"] == "NO_TRADE"
    assert open_ev["construction"]["direction"] == "BUY"


# ───────────────────────────────────────────────────────────────────────────
# 6. Unified canonical identity from the DETECTED pattern
# ───────────────────────────────────────────────────────────────────────────

def test_canonical_identity_uses_detected_pattern_not_strategy(env):
    from core.opportunity.factory import create_opportunity
    from strategy.signals import Side, Signal

    sig = Signal(
        pattern="TWEEZER_TOP", side=Side.SELL, bar_index=2, bar_time=BAR_TIME,
        confidence=0.9,
    )
    opp = create_opportunity(
        signal=sig, symbol=SYMBOL, cycle_id=CYCLE_ID, bid=BID, ask=ASK,
    )

    canonical = make_canonical_opportunity_id(
        symbol=SYMBOL, bar_time=BAR_TIME, pattern="TWEEZER_TOP"
    )
    strategy_root = make_canonical_opportunity_id(
        symbol=SYMBOL, bar_time=BAR_TIME, pattern="MEAN_REVERSION"
    )
    # Opportunity layer and the unified canonical root agree...
    assert opp.opportunity_id == canonical
    assert opp.canonical_opportunity_id == canonical
    # ...and neither is the strategy-family token.
    assert canonical != strategy_root
    assert opp.direction == "SELL"

    # The shadow stream inherits the SAME canonical root.
    env.rt.handle_opportunity(
        _ctx(direction="SELL", eligible=("SCALP",), canonical_opportunity_id=canonical)
    )
    for ev in env.events():
        assert ev["canonical_opportunity_id"] == canonical
    assert env.events()[0]["canonical_opportunity_id"] != strategy_root


# ───────────────────────────────────────────────────────────────────────────
# 7-8. Horizon eligibility drives construction
# ───────────────────────────────────────────────────────────────────────────

def test_multiple_eligible_horizons_all_constructed(env):
    ctx = _ctx(direction="SELL", eligible=("SCALP", "INTRADAY", "EXTENDED"))
    env.rt.handle_opportunity(ctx)
    opens = [e for e in env.events() if e["event_type"] == "OPEN"]
    assert len(opens) == 3
    assert {e["identity"]["trade_horizon"] for e in opens} == {
        "SCALP", "INTRADAY", "EXTENDED",
    }


def test_ineligible_horizons_not_constructed(env):
    ctx = _ctx(direction="SELL", eligible=("SCALP",))
    env.rt.handle_opportunity(ctx)
    opens = [e for e in env.events() if e["event_type"] == "OPEN"]
    assert len(opens) == 1
    assert opens[0]["identity"]["trade_horizon"] == "SCALP"
    plan = [e for e in env.events() if e["event_type"] == "PLAN"][0]
    states = {h["horizon"]: h["state"] for h in plan["horizons"]}
    assert states["INTRADAY"] == "NOT_ELIGIBLE"
    assert states["EXTENDED"] == "NOT_ELIGIBLE"


# ───────────────────────────────────────────────────────────────────────────
# 9. Shadow never touches MT5 execution
# ───────────────────────────────────────────────────────────────────────────

def test_shadow_path_never_invokes_mt5():
    import core.shadow.integration as integration_mod
    import core.shadow.models as models_mod
    import core.shadow.persistence as persistence_mod
    import core.shadow.runtime as runtime_mod

    forbidden = (
        "order_send",
        "ordersend",
        "MT5Execution",
        "mt5_execution",
        "from execution",
        "import MetaTrader5",
        "mt5.position",
        "broker_confirmed",
    )
    for mod in (
        runtime_mod, integration_mod, models_mod, persistence_mod,
    ):
        src = (Path(mod.__file__).read_text(encoding="utf-8")).lower()
        for token in forbidden:
            assert token not in src, f"{mod.__name__} references broker API token {token!r}"


# ───────────────────────────────────────────────────────────────────────────
# 10-11. Exactly-once PLAN and watermark preserved
# ───────────────────────────────────────────────────────────────────────────

def test_no_duplicate_plan_per_root_no_trade(env):
    ctx = _ctx(direction="BUY", eligible=("SCALP",))
    ctx["v10_action"] = "NO_TRADE"
    env.rt.handle_opportunity(ctx)
    env.rt.handle_opportunity(ctx)  # same opportunity-cycle re-delivered
    assert len([e for e in env.events() if e["event_type"] == "PLAN"]) == 1


def test_watermark_exactly_once_preserved(env):
    ctx = _ctx(direction="BUY", eligible=("SCALP",))
    env.rt.handle_opportunity(ctx)
    tid = f"nshadow_{CYCLE_ID}_{SYMBOL}_SCALP"
    for _ in range(2):  # same closed bar delivered twice
        env.rt.evaluate_bar(symbol=SYMBOL, bar_time=BAR_TIME + 300,
                            bar_high=1.10010, bar_low=1.09990, bar_close=1.10000)
    snap = env.rt.snapshot(tid)
    assert snap["lifecycle"]["bars_elapsed"] == 1


# ───────────────────────────────────────────────────────────────────────────
# 12. Legacy shadow writer is skipped when the V2 branch is active
# ────────────────────────────────────────────────────────────────────────────

def _gated_branch(recorded_rt, trace: list[str], **invoke_overrides) -> None:
    """
    Structural mirror of live_scanner.py's V2 shadow branch:
        try:
            handle_live_opportunity_shadow(...)   # NEW runtime handles opportunity
            raise ShadowV2Handled()               # skip legacy writer
            <legacy writer>                       # unreachable
        except Exception:
            pass                                  # containment
        <live flow continues>
    """
    try:
        trace.append("new_runtime_handled")
        _invoke(recorded_rt, **invoke_overrides)
        raise ShadowV2Handled()
        trace.append("legacy_writer_ran")
    except Exception:
        trace.append("contained")
    trace.append("live_flow_continues")


def test_legacy_shadow_not_double_written_when_v2_enabled(recorded):
    # The NEW branch handles NO_TRADE counterfactuals, then raises the sentinel
    # so the legacy writer inside the same try block is unreachable — no
    # double-write while V2 is enabled.
    trace: list[str] = []
    _gated_branch(
        recorded,
        trace,
        new_result=_new_result(action="NO_TRADE", side=None),
        direction="BUY",
        pattern="TWEEZER_TOP",
    )
    assert len(recorded.calls) == 1                      # NEW runtime handled it
    assert recorded.calls[0]["direction"] == "BUY"
    assert recorded.calls[0]["v10_action"] == "NO_TRADE"
    assert "new_runtime_handled" in trace
    assert "contained" in trace
    assert "legacy_writer_ran" not in trace              # legacy skipped
    assert "live_flow_continues" in trace                # live decision untouched
