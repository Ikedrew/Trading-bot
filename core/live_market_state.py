"""
Live Market State — Current market belief per symbol (snapshot store).

Maintains exactly ONE record per symbol representing the latest V10 evaluation.
Overwritten on every evaluation cycle. NOT an append-only log.

Purpose:
    Answers: "What does the bot currently believe about this symbol?"
    Powers: Discord live market cards, dashboard queries, health checks.

Storage:
    logs/live_market_state/{SYMBOL}.json (one file per symbol, overwritten)

Update frequency:
    Every M5 bar where V10 pipeline evaluates the symbol (~every 5 minutes during session).

Relationship to other datasets:
    - decision_trace_v2: append-only history of ALL evaluations (research/audit)
    - live_market_state: LATEST evaluation only (real-time display)
    - opportunities: lifecycle tracking of detected patterns
    - live_market_state: current opportunity/strategy status (no lifecycle)

Design:
    - Overwrite (not append) — one file per symbol
    - Always contains the most recent V10 pipeline output
    - Read by Discord renderer for live cards
    - Never affects trading decisions
    - Failure never blocks execution
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOCAL_DIR = "logs/live_market_state"
_SCHEMA_VERSION = "live_market_state_v1"


def update_live_market_state(
    *,
    symbol: str,
    cycle_id: int,
    bar_time: int,
    v10_pipeline_result: Any = None,
    engine_result: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """
    Update the live market state snapshot for a symbol.

    Called once per V10 evaluation cycle, after the pipeline completes.
    Overwrites the previous snapshot entirely.

    Args:
        symbol: Trading symbol
        cycle_id: Current scan cycle number
        bar_time: Unix timestamp of the bar being evaluated
        v10_pipeline_result: PipelineResult object (if V10 mode)
        engine_result: Engine result dict (fallback fields)

    Returns:
        The state dict that was written, or None on failure.
    """
    if not symbol:
        return None

    try:
        now = datetime.now(timezone.utc)
        state = _build_state(
            symbol=symbol,
            cycle_id=cycle_id,
            bar_time=bar_time,
            v10_pipeline_result=v10_pipeline_result,
            engine_result=engine_result or {},
            timestamp=now,
        )
        _write_state(symbol, state)
        return state
    except Exception as exc:
        logger.debug("[LIVE_MARKET_STATE] update failed for %s: %s", symbol, exc)
        return None


def read_live_market_state(symbol: str) -> dict[str, Any] | None:
    """
    Read the current live market state for a symbol.

    Returns None if no state exists.
    """
    try:
        path = Path(_LOCAL_DIR) / f"{symbol}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def read_all_live_states() -> dict[str, dict[str, Any]]:
    """Read live state for all symbols. Returns {symbol: state_dict}."""
    result = {}
    state_dir = Path(_LOCAL_DIR)
    if not state_dir.exists():
        return result
    for f in state_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            sym = data.get("symbol", f.stem)
            result[sym] = data
        except Exception:
            pass
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# STATE BUILDER
# ═══════════════════════════════════════════════════════════════════════════════


def _build_state(
    *,
    symbol: str,
    cycle_id: int,
    bar_time: int,
    v10_pipeline_result: Any,
    engine_result: dict[str, Any],
    timestamp: datetime,
) -> dict[str, Any]:
    """Build complete live state from V10 pipeline result."""

    state: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "symbol": symbol,
        "cycle_id": cycle_id,
        "bar_time": bar_time,
        "updated_at": timestamp.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "updated_at_unix": round(timestamp.timestamp(), 3),
    }

    # Engine result fields (always available)
    state["action"] = engine_result.get("action", "NO_TRADE")
    state["score"] = engine_result.get("score", 0.0)
    state["reason"] = engine_result.get("reason", "")

    # Identity (for downstream consumers / Discord / joins)
    state["identity"] = {
        "entity_id": engine_result.get("entity_id", ""),
        "observation_id": "",
        "cycle_id": cycle_id,
        "correlation_id": engine_result.get("correlation_id", ""),
        "trade_id": "",  # Populated only after execution
    }

    # V10 pipeline extraction
    if v10_pipeline_result is not None:
        _extract_v10(state, v10_pipeline_result)
    else:
        # Fallback: populate from engine_result dict
        state["market"] = {}
        state["opportunity"] = {}
        state["strategy"] = {"family": engine_result.get("strategy") or ""}
        state["entry"] = {"status": engine_result.get("entry_status") or ""}
        state["risk"] = {}
        state["execution"] = {}

    return state


def _extract_v10(state: dict[str, Any], pr: Any) -> None:
    """Extract full V10 pipeline state into snapshot."""

    # Market state
    try:
        ms = pr.market_state
        state["market"] = {
            "regime": ms.regime.regime,
            "regime_confidence": ms.regime.regime_confidence,
            "volatility_state": ms.regime.volatility_state,
            "h4_trend": ms.h4.trend,
            "h4_trend_strength": ms.h4.trend_strength,
            "h4_phase": ms.h4.market_phase,
            "h1_bos_direction": ms.h1.bos_direction if ms.h1.bos_confirmed else "",
            "h1_structural_clarity": ms.h1.structural_clarity,
            "h1_dominant_trend": ms.h1.dominant_trend,
            "m15_pullback_active": ms.m15.pullback_active,
            "m15_displacement": ms.m15.displacement_present,
            "m15_range_position": ms.m15.range_position,
            "m5_momentum": ms.m5.momentum_direction,
            "m5_momentum_strength": ms.m5.momentum_strength,
            "m5_rejection": ms.m5.rejection_present,
            "m5_confirmation": ms.m5.confirmation_candle,
            "m5_local_bos": ms.m5.local_bos,
            "location_type": ms.location.location_type,
            "inside_zone": ms.location.inside_institutional_zone,
            "zone_quality": ms.location.zone_quality,
            "range_position": ms.location.range_position,
            "premium_discount": ms.location.premium_discount,
            "htf_macro_bias": ms.htf_alignment.macro_bias,
            "htf_structure_alignment": ms.htf_alignment.structure_alignment,
        }
    except Exception:
        state["market"] = {}

    # Opportunity
    try:
        opp = pr.opportunity
        state["opportunity"] = {
            "state": opp.opportunity_state,
            "directional_bias": opp.directional_bias,
            "opportunity_type": opp.opportunity_type,
            "overall_quality": opp.quality.overall_quality,
            "location_score": opp.quality.location_score,
            "structure_score": opp.quality.structure_score,
            "behaviour_score": opp.quality.behaviour_score,
            "formation_score": opp.quality.formation_score,
            "observation_id": opp.observation_id,
        }
        # Update identity with observation_id
        state["identity"]["observation_id"] = opp.observation_id or ""
    except Exception:
        state["opportunity"] = {}

    # Strategy
    try:
        strat = pr.strategy
        state["strategy"] = {
            "family": strat.strategy_family,
            "confidence": strat.strategy_confidence,
            "direction": strat.directional_context,
        }
    except Exception:
        state["strategy"] = {}

    # Horizon
    try:
        hz = pr.horizon
        state["horizon"] = {
            "type": hz.horizon_type,
            "min_move": hz.movement_expectation.minimum_expected_move,
            "max_move": hz.movement_expectation.maximum_expected_move,
            "duration_minutes": hz.trade_lifecycle.expected_duration_minutes,
        }
    except Exception:
        state["horizon"] = {}

    # Entry
    try:
        ent = pr.entry
        state["entry"] = {
            "status": ent.entry_status,
            "method": ent.entry_method,
            "direction": ent.trade_direction,
            "price": ent.entry_price,
            "stop": ent.stop_reference.price,
            "target": ent.target_reference.price,
            "risk_distance": ent.risk_distance,
            "reward_distance": ent.reward_distance,
            "expected_rr": ent.expected_rr,
        }
    except Exception:
        state["entry"] = {}

    # Risk
    try:
        rsk = pr.risk
        state["risk"] = {
            "approved": rsk.approved,
            "rejection_reason": rsk.rejection_reason or "",
            "risk_percentage": rsk.risk_profile.risk_percentage,
            "position_size": rsk.risk_profile.position_size,
            "max_loss": rsk.risk_profile.max_loss_amount,
        }
    except Exception:
        state["risk"] = {}

    # Execution decision
    try:
        exe = pr.execution
        state["execution"] = {
            "approved": exe.approved,
            "rejection_reason": exe.rejection_reason or "",
            "order_type": exe.order_details.order_type if exe.approved else "",
            "volume": exe.order_details.volume if exe.approved else 0.0,
        }
    except Exception:
        state["execution"] = {}

    # Pipeline progression
    state["rejection_stage"] = pr.rejection_stage or ""
    state["approved"] = pr.approved


# ═══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE (overwrite, not append)
# ═══════════════════════════════════════════════════════════════════════════════


def _write_state(symbol: str, state: dict[str, Any]) -> None:
    """Write state to local JSON file (overwrites previous)."""
    path = Path(_LOCAL_DIR) / f"{symbol}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state, indent=2, default=str),
        encoding="utf-8",
    )
