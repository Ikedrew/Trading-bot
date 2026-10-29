"""
Opportunity Factory — Creates Opportunity objects from detected Signals.

Called at pattern detection time to capture every market candidate
before the decision system filters them.

This module is PURELY OBSERVATIONAL. It does NOT:
    - Affect trading decisions
    - Modify the pipeline
    - Block or gate execution
    - Change scoring or risk behaviour
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.opportunity.opportunity import Opportunity, OpportunityState
from strategy.signals import Signal


def create_opportunity(
    *,
    signal: Signal,
    symbol: str,
    cycle_id: int,
    candles: Any = None,
    htf_context: Any = None,
    engine_state: Any = None,
    sibling_patterns: list[str] | None = None,
    bid: float = 0.0,
    ask: float = 0.0,
    session_state: str = "",
    runtime_session_id: str = "",
    canonical_opportunity_id: str = "",
) -> Opportunity:
    """
    Create an Opportunity from a detected Signal and available context.

    Called once per detected pattern per symbol per cycle.
    Captures all available evidence at detection time.

    Args:
        signal: Detected pattern Signal (pattern, side, bar_index, bar_time, confidence)
        symbol: Trading pair
        cycle_id: Current scan cycle number
        candles: Candle list (for trigger candle extraction)
        htf_context: HTFContext (H4 regime, H1 bias, M15 structure)
        engine_state: EngineState (for bias FSM state)
        sibling_patterns: Other patterns detected on same bar
        bid: Live bid price at detection time (for hypothetical research)
        ask: Live ask price at detection time (for hypothetical research)
        session_state: Trading session classification at detection time
        runtime_session_id: Bot runtime session identifier

    Returns:
        Opportunity in DETECTED state with all available evidence.
    """
    now = datetime.now(timezone.utc)
    detected_at_utc = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    # Build THE canonical opportunity lineage ID (single authoritative mint point)
    from core.identity.canonical import make_canonical_opportunity_id

    opportunity_id = make_canonical_opportunity_id(
        symbol=symbol,
        bar_time=signal.bar_time,
        pattern=signal.pattern,
    )

    # Extract trigger candle OHLC
    trigger_candle: dict[str, float] = {}
    if candles is not None and 0 <= signal.bar_index < len(candles):
        c = candles[signal.bar_index]
        trigger_candle = {
            "open": float(getattr(c, "open", 0.0)),
            "high": float(getattr(c, "high", 0.0)),
            "low": float(getattr(c, "low", 0.0)),
            "close": float(getattr(c, "close", 0.0)),
        }

    # Extract HTF evidence (safe extraction — never fails)
    h4_regime = ""
    h4_regime_confidence = 0.0
    h1_direction = ""
    h1_bos_confirmed = False
    h1_swing_structure = ""

    if htf_context is not None:
        # H4 regime
        h4_snap = getattr(htf_context, "regime", None)
        if h4_snap is not None:
            h4_class = getattr(h4_snap, "classification", None)
            if h4_class is not None:
                h4_regime = h4_class.value if hasattr(h4_class, "value") else str(h4_class)
            h4_regime_confidence = float(getattr(h4_snap, "confidence", 0.0))

        # H1 bias
        h1_snap = getattr(htf_context, "bias", None)
        if h1_snap is not None:
            h1_dir = getattr(h1_snap, "direction", None)
            if h1_dir is not None:
                h1_direction = h1_dir.value if hasattr(h1_dir, "value") else str(h1_dir)
            h1_bos_confirmed = bool(getattr(h1_snap, "bos_confirmed", False))
            h1_swing_structure = str(getattr(h1_snap, "swing_structure", "")) or ""

    # Extract bias FSM state
    bias_direction = ""
    bias_phase = ""
    if engine_state is not None:
        _bias = getattr(engine_state, "current_bias", None)
        if _bias is not None:
            bias_direction = _bias.name if hasattr(_bias, "name") else str(_bias)
        bias_phase = str(getattr(engine_state, "bias_phase", "")) or ""

    return Opportunity(
        # Identity
        opportunity_id=opportunity_id,
        symbol=symbol,
        cycle_id=cycle_id,
        # Market observation
        direction=signal.side.value if hasattr(signal.side, "value") else str(signal.side),
        pattern=signal.pattern,
        detection_timeframe="M5",
        detected_at_bar_time=signal.bar_time,
        detected_at_utc=detected_at_utc,
        trigger_candle=trigger_candle,
        # Market snapshot
        bid_at_detection=bid,
        ask_at_detection=ask,
        spread_at_detection=round(ask - bid, 8) if ask > 0 and bid > 0 else 0.0,
        session_at_detection=session_state,
        # Evidence
        h4_regime=h4_regime,
        h4_regime_confidence=h4_regime_confidence,
        h1_direction=h1_direction,
        h1_bos_confirmed=h1_bos_confirmed,
        h1_swing_structure=h1_swing_structure,
        bias_direction=bias_direction,
        bias_phase=bias_phase,
        # Confidence
        pattern_confidence=signal.confidence,
        # Lifecycle
        state=OpportunityState.DETECTED.value,
        sibling_patterns=sibling_patterns or [],
        # Metadata
        # Explicit canonical lineage root (Phase 3). Single-authority rule: if a
        # caller already established the root it is passed through VERBATIM;
        # otherwise the approved mint (same function live_scanner uses) fills it,
        # which by determinism equals that downstream mint for identical inputs.
        canonical_opportunity_id=(
            canonical_opportunity_id or opportunity_id
        ),
        entity_id=f"{symbol}_{signal.bar_time}",
        runtime_session_id=runtime_session_id,
    )
