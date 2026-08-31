"""
NEW Shadow Runtime — Live integration adapters.

The ONLY modules the live path touches. Every function is fire-and-forget:
exceptions are contained here (or by the caller's existing isolation) and can
never alter a live decision, risk outcome, execution, or broker state.

Integration points (both gated by config.SHADOW_RUNTIME_V2_ENABLED):
    - core/runtime/live_scanner.py  → handle_live_opportunity_shadow()
      at the pre-verdict horizon-shadow branch.
    - core/runtime/bar_provider.py  → evaluate_closed_bar()
      on each authoritative closed M5 bar.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class ShadowV2Handled(Exception):
    """
    Internal sentinel: raised by live_scanner's gated branch after the NEW
    runtime has handled an opportunity, so the legacy open_trade block is
    skipped while remaining inside the block's existing `except Exception`
    isolation. Never escapes the shadow branch.
    """


def _normalise_direction(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "value"):
        value = value.value
    text = str(value or "").upper()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    if text in ("BUY", "BULLISH", "LONG"):
        return "BUY"
    if text in ("SELL", "BEARISH", "SHORT"):
        return "SELL"
    return ""


def _extract_direction(new_result: dict[str, Any], assessment_obj: Any) -> str:
    for raw in (
        getattr(assessment_obj, "side", "") if assessment_obj else "",
        new_result.get("side", ""),
    ):
        direction = _normalise_direction(raw)
        if direction:
            return direction

    pipeline_result = new_result.get("v10_pipeline_result")
    opportunity = getattr(pipeline_result, "opportunity", None)
    return _normalise_direction(getattr(opportunity, "directional_bias", ""))


def _h1_bias_str(htf_context: Any) -> str:
    try:
        bias = getattr(htf_context, "bias", None)
        d = getattr(bias, "direction", None)
        if d is not None and hasattr(d, "value"):
            return str(d.value)
        if d is not None:
            return str(d)
    except Exception:
        pass
    return ""


def handle_live_opportunity_shadow(
    *,
    symbol: str,
    cycle_id: int,
    closed_time: int,
    candles: Any,
    closed_i: int,
    bid: float,
    ask: float,
    htf_context: Any,
    new_result: dict[str, Any],
    horizon_result: Any,
    canonical_opportunity_id: str,
    entity_id: str,
    observation_id: str = "",
    regime: str = "",
    h4_regime: str = "",
    market_phase: str = "",
    market_phase_confidence: float = 0.0,
) -> None:
    """
    Branch-point adapter: PLAN + OPEN for all constructible horizons of one
    canonical opportunity. Pre-verdict; independent of the live decision.

    Unexpected failures propagate to the caller's existing fire-and-forget
    isolation (the shadow branch's `except Exception: pass`).
    """
    from core.shadow.runtime import get_shadow_runtime

    if not canonical_opportunity_id:
        return  # rule 17: every simulation must have a canonical root

    direction = _extract_direction(new_result, new_result.get("assessment"))

    # Structure inputs actually consumed by construction (mirrors legacy
    # extraction; values frozen verbatim into the OPEN definition).
    m5_high = m5_low = m15_sup = m15_res = h1_hi = h1_lo = None
    if htf_context is not None:
        m15 = getattr(htf_context, "structure", None)
        if m15 is not None:
            m15_sup = getattr(m15, "nearest_support", None)
            m15_res = getattr(m15, "nearest_resistance", None)
        h1 = getattr(htf_context, "bias", None)
        if h1 is not None:
            h1_hi = getattr(h1, "last_swing_high", None)
            h1_lo = getattr(h1, "last_swing_low", None)
    if candles and 0 <= closed_i < len(candles):
        m5_high = getattr(candles[closed_i], "high", None)
        m5_low = getattr(candles[closed_i], "low", None)

    eligible: list[str] = []
    assessments: list[dict[str, Any]] = []
    if horizon_result is not None:
        to_dict = getattr(horizon_result, "to_dict", None)
        raw = to_dict() if callable(to_dict) else {}
        for a in raw.get("assessments", []) or []:
            hz = str(a.get("horizon", "") or "").upper()
            assessments.append(
                {
                    "horizon": hz,
                    "confidence": a.get("confidence"),
                    "reasoning": a.get("reasoning", ""),
                }
            )
            if a.get("eligible"):
                eligible.append(hz)

    pr = new_result.get("v10_pipeline_result")
    v10_selected = ""
    v10_rejection = ""
    if pr is not None:
        hz_obj = getattr(pr, "horizon", None)
        if hz_obj is not None:
            v10_selected = str(getattr(hz_obj, "horizon_type", "") or "")
        v10_rejection = str(getattr(pr, "rejection_stage", "") or "")

    ctx = {
        "canonical_opportunity_id": canonical_opportunity_id,
        "observation_id": observation_id,
        "entity_id": entity_id,
        "symbol": symbol,
        "cycle_id": cycle_id,
        "bar_time_raw": int(closed_time),
        "direction": direction,
        "pattern": new_result.get("pattern", "") or "",
        "strategy": new_result.get("strategy", "") or "",
        "score": float(new_result.get("score", 0.0) or 0.0),
        # Phase 3 Step 10-B: regime/phase facts are supplied by the caller from
        # the already-produced assessment/engine context (passed explicitly).
        # Values are forwarded verbatim; never invented here ("" when absent).
        "regime": regime or new_result.get("activation_regime", "") or "",
        "h4_regime": h4_regime or new_result.get("activation_regime", "") or "",
        "h1_bias": _h1_bias_str(htf_context),
        "market_phase": market_phase or new_result.get("market_phase", "") or "",
        "market_phase_confidence": float(
            new_result.get("market_phase_confidence", 0.0) or 0.0
        )
        if not market_phase_confidence
        else float(market_phase_confidence),
        "bid": bid,
        "ask": ask,
        "structure": {
            "m5_candle_high": m5_high,
            "m5_candle_low": m5_low,
            "m15_nearest_support": m15_sup,
            "m15_nearest_resistance": m15_res,
            "h1_last_swing_high": h1_hi,
            "h1_last_swing_low": h1_lo,
        },
        "eligible_horizons": eligible,
        "horizon_assessments": assessments,
        "v10_action": new_result.get("action", "") or "",
        "v10_rejection_stage": v10_rejection,
        "v10_selected_horizon": v10_selected,
    }
    get_shadow_runtime().handle_opportunity(ctx)


def evaluate_closed_bar(
    *,
    symbol: str,
    bar_time: int,
    bar_high: float,
    bar_low: float,
    bar_close: float,
    bar_index: int = 0,
) -> None:
    """Closed-bar adapter: authoritative lifecycle transition for active shadows."""
    from core.shadow.runtime import get_shadow_runtime

    get_shadow_runtime().evaluate_bar(
        symbol=symbol,
        bar_time=int(bar_time),
        bar_high=bar_high,
        bar_low=bar_low,
        bar_close=bar_close,
        bar_index=bar_index,
    )
