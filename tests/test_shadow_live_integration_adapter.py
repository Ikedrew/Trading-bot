"""
NEW Shadow Runtime — live integration adapter unit tests.

Covers the ONLY bridge between the production scanner and the NEW Shadow
Runtime: ``core.shadow.integration.handle_live_opportunity_shadow()``, plus
the ``ShadowV2Handled`` sentinel contract consumed by the gated branch in
``core/runtime/live_scanner.py`` (Phase 4C.3, lines ~752-896).

Contract proven here (statically, gate stays OFF):

    handle_live_opportunity_shadow()
        ↓ canonical root preserved verbatim, full context assembled
NEW Shadow Runtime handles opportunity
        ↓ (scanner raises sentinel after successful handling)
ShadowV2Handled raised
        ↓
caught by the branch's generic `except Exception` isolation
        ↓
legacy shadow writer skipped, LIVE execution flow continues

Failure isolation follows the EXISTING implementation: the adapter does NOT
swallow runtime failures — they propagate to the caller's containment, which
prevents any impact on LIVE decision/execution.

Fully deterministic and offline: the runtime singleton is stubbed — no MT5,
S3, Discord, broker credentials, or real persistence involved.
"""

from __future__ import annotations

import sys
from enum import Enum
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.shadow.integration import (
    ShadowV2Handled,
    handle_live_opportunity_shadow,
)
from core.identity.canonical import mint_observation_id

SYMBOL = "EURUSD"
ROOT_ID = "EURUSD*1784800000*TWEEZER_TOP"
OBSERVATION_ID = mint_observation_id(
    symbol="EURUSD", bar_time=1_784_800_000, timeframe="M5"
)
ENTITY_ID = "EURUSD_1784800000"
CYCLE_ID = 42
BAR_TIME_RAW = 1_784_800_000
BID = 1.10000
ASK = 1.10002


# ─── Realistic stand-ins for live-scanner objects ─────────────────────────────

class _H1Direction(Enum):
    BEARISH = "BEARISH"


def _htf_context() -> SimpleNamespace:
    """Mimics the HTF context object built by the live scanner pipeline."""
    structure = SimpleNamespace(
        nearest_support=1.09980,
        nearest_resistance=1.10100,
    )
    bias = SimpleNamespace(
        direction=_H1Direction.BEARISH,
        last_swing_high=1.10450,
        last_swing_low=1.09750,
    )
    return SimpleNamespace(structure=structure, bias=bias)


def _candles() -> list:
    """Three M5 candles; index 2 is the authoritative closed bar."""
    return [
        SimpleNamespace(high=1.10030, low=1.09970),
        SimpleNamespace(high=1.10060, low=1.09990),
        SimpleNamespace(high=1.10050, low=1.09980),  # closed_i = 2
    ]


def _horizon_result():
    """Mimics HorizonResult: SCALP ineligible, INTRADAY+EXTENDED eligible."""

    def to_dict():
        return {
            "assessments": [
                {
                    "horizon": "SCALP",
                    "eligible": False,
                    "confidence": 0.40,
                    "reasoning": "below scalp threshold",
                },
                {
                    "horizon": "INTRADAY",
                    "eligible": True,
                    "confidence": 0.61,
                    "reasoning": "intraday structure aligned",
                },
                {
                    "horizon": "EXTENDED",
                    "eligible": True,
                    "confidence": 0.55,
                    "reasoning": "h1 swing room available",
                },
            ]
        }

    return SimpleNamespace(to_dict=to_dict)


def _new_result(*, action: str = "NO_TRADE", side: str = "SELL") -> dict:
    """Mimics the engine result dict assembled by live_scanner (V10 mode)."""
    return {
        "entity_id": ENTITY_ID,
        "action": action,
        "side": side,
        "pattern": "TWEEZER_TOP",
        "strategy": "REVERSAL",
        "score": 0.61,
        "activation_regime": "RANGE",
        "market_phase": "PULLBACK",
        "market_phase_confidence": 0.55,
        "assessment": SimpleNamespace(side=side),
        "v10_pipeline_result": SimpleNamespace(
            horizon=SimpleNamespace(horizon_type="INTRADAY"),
            rejection_stage="risk",
        ),
    }


class _RecordingRuntime:
    """Stub singleton: records every handle_opportunity() context."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def handle_opportunity(self, ctx: dict) -> None:
        self.calls.append(ctx)


class _ExplodingRuntime:
    """Stub singleton simulating an internal Shadow Runtime failure."""

    def handle_opportunity(self, ctx: dict) -> None:
        raise RuntimeError("simulated runtime failure")


@pytest.fixture()
def recorded(monkeypatch) -> _RecordingRuntime:
    """Intercept the process-wide runtime lookup used by the adapter."""
    rt = _RecordingRuntime()
    monkeypatch.setattr(
        "core.shadow.runtime.get_shadow_runtime", lambda: rt
    )
    return rt


def _invoke(runtime_calls: _RecordingRuntime, **overrides) -> None:
    del runtime_calls  # documentation: recording fixture is active
    kwargs = dict(
        symbol=SYMBOL,
        cycle_id=CYCLE_ID,
        closed_time=BAR_TIME_RAW,
        candles=_candles(),
        closed_i=2,
        bid=BID,
        ask=ASK,
        htf_context=_htf_context(),
        new_result=_new_result(),
        horizon_result=_horizon_result(),
        canonical_opportunity_id=ROOT_ID,
        entity_id=ENTITY_ID,
        observation_id=OBSERVATION_ID,
    )
    kwargs.update(overrides)
    handle_live_opportunity_shadow(**kwargs)


def _gated_branch_harness(shadow_invocation) -> list[str]:
    """
    Faithful structural mirror of live_scanner.py Phase 4C.3 (lines 752-896):

        try:
            <shadow invocation>          # adapter call
            raise ShadowV2Handled()      # scanner:777 — skips legacy writer
            <legacy shadow writer>       # unreachable when gate ON
        except Exception:
            pass                         # scanner:895-896 — containment
        <LIVE flow continues>            # Phase 2A opportunity lifecycle etc.

    Returns the execution trace so tests can assert exactly which stages ran.
    """
    trace: list[str] = []
    try:
        shadow_invocation()
        trace.append("sentinel_raised")
        raise ShadowV2Handled()
        trace.append("legacy_writer_ran")
    except Exception:
        trace.append("contained")
    trace.append("live_flow_continues")
    return trace


# ─── 1. CONTEXT ASSEMBLY ──────────────────────────────────────────────────────

def test_full_context_assembly_mapped_to_runtime(recorded):
    _invoke(recorded)

    assert len(recorded.calls) == 1
    ctx = recorded.calls[0]

    # Identity / timing
    assert ctx["symbol"] == SYMBOL
    assert ctx["canonical_opportunity_id"] == ROOT_ID
    assert ctx["observation_id"] == OBSERVATION_ID
    assert ctx["entity_id"] == ENTITY_ID
    assert ctx["cycle_id"] == CYCLE_ID
    assert ctx["bar_time_raw"] == BAR_TIME_RAW

    # Direction + engine observation facts
    assert ctx["direction"] == "SELL"
    assert ctx["pattern"] == "TWEEZER_TOP"
    assert ctx["strategy"] == "REVERSAL"
    assert ctx["score"] == pytest.approx(0.61)
    assert ctx["regime"] == "RANGE"
    assert ctx["h4_regime"] == "RANGE"
    assert ctx["market_phase"] == "PULLBACK"
    assert ctx["market_phase_confidence"] == pytest.approx(0.55)

    # H1 bias resolved from the enum-like broker object (.direction.value)
    assert ctx["h1_bias"] == "BEARISH"

    # Entry market facts
    assert ctx["bid"] == pytest.approx(BID)
    assert ctx["ask"] == pytest.approx(ASK)

    # Structure snapshot frozen verbatim from live objects
    st = ctx["structure"]
    assert st["m5_candle_high"] == pytest.approx(1.10050)
    assert st["m5_candle_low"] == pytest.approx(1.09980)
    assert st["m15_nearest_support"] == pytest.approx(1.09980)
    assert st["m15_nearest_resistance"] == pytest.approx(1.10100)
    assert st["h1_last_swing_high"] == pytest.approx(1.10450)
    assert st["h1_last_swing_low"] == pytest.approx(1.09750)

    # Horizon plan inputs: eligibility list + per-horizon assessments
    assert ctx["eligible_horizons"] == ["INTRADAY", "EXTENDED"]
    by_hz = {a["horizon"]: a for a in ctx["horizon_assessments"]}
    assert set(by_hz) == {"SCALP", "INTRADAY", "EXTENDED"}
    # Adapter maps ONLY horizon/confidence/reasoning per contract
    # (integration.py) — eligibility itself travels via eligible_horizons.
    assert by_hz["SCALP"] == {
        "horizon": "SCALP",
        "confidence": 0.40,
        "reasoning": "below scalp threshold",
    }
    assert by_hz["INTRADAY"] == {
        "horizon": "INTRADAY",
        "confidence": 0.61,
        "reasoning": "intraday structure aligned",
    }

    # LIVE verdict facts carried as observations only
    assert ctx["v10_action"] == "NO_TRADE"
    assert ctx["v10_rejection_stage"] == "risk"
    assert ctx["v10_selected_horizon"] == "INTRADAY"


def test_direction_taken_from_assessment_object_first(recorded):
    # Assessment object wins over the raw "side" key (extraction precedence).
    nr = _new_result(side="")
    nr["assessment"] = SimpleNamespace(side="BUY")
    _invoke(recorded, new_result=nr)
    assert recorded.calls[0]["direction"] == "BUY"


def test_rejected_direction_taken_from_v10_opportunity_bias(recorded):
    nr = _new_result(action="NO_TRADE", side="")
    nr["assessment"] = None
    nr["v10_pipeline_result"] = SimpleNamespace(
        opportunity=SimpleNamespace(directional_bias="BEARISH"),
        horizon=SimpleNamespace(horizon_type=""),
        rejection_stage="risk",
    )
    _invoke(recorded, new_result=nr)
    assert recorded.calls[0]["direction"] == "SELL"
    assert recorded.calls[0]["v10_action"] == "NO_TRADE"


# ─── 2. CANONICAL IDENTITY PRESERVATION ───────────────────────────────────────

def test_canonical_identity_passed_verbatim(recorded):
    odd_root = "EURUSD*1784800000*Tweezer_Top"  # unusual casing must survive
    _invoke(recorded, canonical_opportunity_id=odd_root)
    # Char-for-char equality: not regenerated, hashed, normalised or re-cased.
    assert recorded.calls[0]["canonical_opportunity_id"] == odd_root
    assert recorded.calls[0]["canonical_opportunity_id"] != odd_root.upper()
    assert recorded.calls[0]["canonical_opportunity_id"] != odd_root.lower()


def test_canonical_identity_matches_input_exactly_on_standard_path(recorded):
    _invoke(recorded)
    assert recorded.calls[0]["canonical_opportunity_id"] == ROOT_ID
    assert type(recorded.calls[0]["canonical_opportunity_id"]) is str


# ─── 3. PRE-VERDICT BEHAVIOUR ─────────────────────────────────────────────────

def test_rejected_no_trade_opportunity_still_enters_shadow(recorded):
    """Pre-verdict branch: NO_TRADE/rejected opportunities still construct."""
    nr = _new_result(action="NO_TRADE")
    _invoke(recorded, new_result=nr)

    assert len(recorded.calls) == 1
    ctx = recorded.calls[0]
    assert ctx["v10_action"] == "NO_TRADE"
    assert ctx["v10_rejection_stage"] == "risk"
    # Same opportunity identity regardless of the eventual LIVE outcome
    assert ctx["canonical_opportunity_id"] == ROOT_ID


def test_rejected_at_different_stage_still_enters_shadow(recorded):
    nr = _new_result(action="NO_TRADE")
    nr["v10_pipeline_result"] = SimpleNamespace(
        horizon=SimpleNamespace(horizon_type=""),
        rejection_stage="session",
    )
    _invoke(recorded, new_result=nr)
    ctx = recorded.calls[0]
    assert ctx["v10_action"] == "NO_TRADE"
    assert ctx["v10_rejection_stage"] == "session"
    assert ctx["v10_selected_horizon"] == ""


# ─── 4. EARLY-RETURN CONDITIONS ───────────────────────────────────────────────

def test_missing_canonical_root_creates_no_record(recorded):
    _invoke(recorded, canonical_opportunity_id="")
    assert recorded.calls == []


def test_none_like_empty_canonical_root_creates_no_record(recorded):
    # Whitespace-only root is truthy in Python — contract only guards ""/None.
    _invoke(recorded, canonical_opportunity_id=None)
    assert recorded.calls == []


def test_missing_direction_is_not_fabricated(recorded):
    _invoke(recorded, new_result=_new_result(side=""))
    assert len(recorded.calls) == 1
    assert recorded.calls[0]["direction"] == ""


def test_unknown_direction_value_is_not_fabricated(recorded):
    _invoke(recorded, new_result=_new_result(side="FLAT"))
    assert len(recorded.calls) == 1
    assert recorded.calls[0]["direction"] == ""


# ─── 5. SHADOW_V2_HANDLED SENTINEL CONTRACT ───────────────────────────────────

def test_sentinel_is_plain_exception_containable_by_scanner_isolation():
    # live_scanner contains the branch with a bare `except Exception`;
    # the sentinel MUST be an Exception subclass (never BaseException-only),
    # otherwise the containment at live_scanner.py:895 would miss it.
    assert issubclass(ShadowV2Handled, Exception)


def test_sentinel_flow_skips_legacy_writer_and_continues_live(recorded):
    """Happy path: handled → sentinel → contained → legacy skipped → continue."""
    trace = _gated_branch_harness(lambda: _invoke(recorded))
    assert trace == ["sentinel_raised", "contained", "live_flow_continues"]
    # The NEW runtime actually received the opportunity before the sentinel.
    assert len(recorded.calls) == 1


# ─── 6. FAILURE ISOLATION (existing implementation contract) ──────────────────

def test_runtime_failure_propagates_out_of_adapter_to_caller(monkeypatch):
    """The adapter does NOT swallow failures (documented contract:
    integration.py 'Unexpected failures propagate to the caller's existing
    fire-and-forget isolation'). Containment is the CALLER's job."""
    monkeypatch.setattr(
        "core.shadow.runtime.get_shadow_runtime", lambda: _ExplodingRuntime()
    )
    with pytest.raises(RuntimeError):
        _invoke(_ExplodingRuntime())


def test_runtime_failure_contained_and_never_reaches_legacy_or_live(monkeypatch):
    """Caller containment: runtime failure → generic except → LIVE continues.
    Crucially the sentinel stage is NEVER reached, proving ShadowV2Handled is
    raised only after the NEW runtime successfully handled the opportunity."""
    monkeypatch.setattr(
        "core.shadow.runtime.get_shadow_runtime", lambda: _ExplodingRuntime()
    )

    trace = _gated_branch_harness(lambda: _invoke(_ExplodingRuntime()))
    assert "sentinel_raised" not in trace      # never claimed success
    assert "legacy_writer_ran" not in trace    # legacy writer also skipped
    assert trace[-1] == "live_flow_continues"  # LIVE unaffected


def test_persistence_style_failure_does_not_break_subsequent_cycles(monkeypatch):
    """A transient runtime failure on cycle N must not prevent cycle N+1."""
    rt = _RecordingRuntime()
    state = {"fail": True}

    def flaky_handle(ctx):
        if state["fail"]:
            raise RuntimeError("transient persistence failure")
        rt.calls.append(ctx)

    monkeypatch.setattr(
        "core.shadow.runtime.get_shadow_runtime",
        lambda: SimpleNamespace(handle_opportunity=flaky_handle),
    )

    with pytest.raises(RuntimeError):
        _invoke(rt)  # cycle N fails inside the runtime
    assert rt.calls == []

    state["fail"] = False
    _invoke(rt)  # cycle N+1 succeeds once the runtime recovers
    assert len(rt.calls) == 1
