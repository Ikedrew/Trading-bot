"""
Candidate Shadow Hook — Opens paired candidate shadow observations.

Called from engine_execution_handler.py after shadow observations are opened.
For each candidate in SHADOW_TESTING status, opens an additional shadow trade
with the candidate's modified geometry.

CONTRACT:
    - Observation-only: never modifies production configuration
    - Never calls MT5Execution or broker
    - Never alters the V10 decision or existing shadow
    - Never prevents trade execution
    - Failure is silent (log + continue)
    - Uses existing ShadowTradeEngine.open_trade()
    - Preserves correlation_id and entity_id for pairing

PAIRED OBSERVATION MODEL:
    Same opportunity (correlation_id, entity_id):
        CANDIDATE_{id}   → candidate counterfactual outcome

    RETIRED (Phase 1I-C): the legacy V10_PRIMARY baseline shadow type is
    removed from the architecture and is no longer created at runtime
    (live_scanner emits only the canonical HORIZON_ALTERNATIVE lineage).
    Candidate shadows are paired with the DEPLOYED logic's realised outcome
    (trade_truth) on the same opportunity via the exact execution
    correlation_id — see research_engine.lifecycle.candidate_pairing for the
    honest pairing contract used by evaluation.

This module NEVER modifies production V10.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def open_candidate_shadows(
    *,
    symbol: str,
    cycle_id: int,
    direction: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    entry_time: float,
    entry_bar_index: int,
    correlation_id: str,
    entity_id: str,
    pattern: str,
    score: float,
    bid: float,
    ask: float,
    strategy: str = "",
) -> int:
    """
    Open candidate shadow observations for all active SHADOW_TESTING candidates.
    
    Called after the canonical shadow observations are created. Uses the same
    opportunity context but applies candidate-specific geometry modifications.
    
    Returns number of candidate shadows opened.
    Never raises — all errors are logged and suppressed.
    """
    count = 0
    try:
        from research_engine.v10.candidates.candidate_registry import CandidateRegistry
        from research_engine.v10.candidates.models import CandidateStatus
        from core.shadow_trades import get_shadow_engine

        registry = CandidateRegistry()
        candidates = registry.list_by_status(CandidateStatus.SHADOW_TESTING)

        if not candidates:
            return 0

        risk_distance = abs(entry_price - stop_loss)
        if risk_distance <= 0:
            return 0

        engine = get_shadow_engine()

        for candidate in candidates:
            try:
                params = _translate_change_definition(
                    change_definition=candidate.change_definition,
                    direction=direction,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    risk_distance=risk_distance,
                    symbol=symbol,
                    pattern=pattern,
                )

                if params is None:
                    # Unsupported candidate type — skip silently
                    continue

                # Check if candidate applies to this opportunity
                if not _candidate_applies(candidate, symbol=symbol, pattern=pattern):
                    continue

                # Open candidate shadow with modified geometry
                trade_id = f"candidate_{candidate.candidate_id}_{cycle_id}_{symbol}"

                engine.open_trade(
                    trade_id=trade_id,
                    cycle_id=cycle_id,
                    symbol=symbol,
                    direction=params["direction"],
                    entry_price=entry_price,
                    stop_loss=params["stop_loss"],
                    take_profit=params["take_profit"],
                    entry_time=entry_time,
                    strategy=strategy,
                    pattern=pattern,
                    score=score,
                    lot_size=0.01,
                    entry_bar_index=entry_bar_index,
                    correlation_id=correlation_id,
                    entity_id=entity_id,
                    spread_at_entry=abs(ask - bid) if (bid > 0 and ask > 0) else 0.0,
                    bid_at_entry=bid,
                    ask_at_entry=ask,
                    # Candidate shadow lineage
                    shadow_type=f"CANDIDATE_{candidate.candidate_id}",
                    v10_action="CANDIDATE_SHADOW",
                )

                count += 1
                logger.debug(
                    "[CANDIDATE_SHADOW] opened trade_id=%s candidate=%s symbol=%s dir=%s",
                    trade_id, candidate.candidate_id, symbol, params["direction"],
                )

            except Exception as e:
                logger.debug("[CANDIDATE_SHADOW_ERROR] candidate=%s error=%s",
                             candidate.candidate_id, str(e)[:100])
                continue  # Individual candidate failure must never block

    except Exception as e:
        logger.debug("[CANDIDATE_SHADOW_HOOK_ERROR] %s", str(e)[:100])

    return count


def _translate_change_definition(
    *,
    change_definition: dict[str, Any],
    direction: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    risk_distance: float,
    symbol: str,
    pattern: str,
) -> dict[str, Any] | None:
    """
    Translate a candidate's change_definition into shadow trade parameters.
    
    Returns dict with {direction, stop_loss, take_profit} or None if unsupported.
    """
    change_type = change_definition.get("type", "")

    if change_type == "direction_inversion":
        # Invert direction, compute new SL/TP from original risk distance
        inv_dir = "BUY" if direction == "SELL" else "SELL"
        if inv_dir == "BUY":
            new_sl = entry_price - risk_distance
            new_tp = entry_price + risk_distance * 3.0
        else:
            new_sl = entry_price + risk_distance
            new_tp = entry_price - risk_distance * 3.0
        return {"direction": inv_dir, "stop_loss": new_sl, "take_profit": new_tp}

    elif change_type == "geometry_modification":
        # Modify stop width using multiplier
        multiplier = change_definition.get("stop_multiplier", 1.5)
        if direction == "BUY":
            new_sl = entry_price - risk_distance * multiplier
            new_tp = take_profit  # Keep original TP
        else:
            new_sl = entry_price + risk_distance * multiplier
            new_tp = take_profit
        return {"direction": direction, "stop_loss": new_sl, "take_profit": new_tp}

    elif change_type == "symbol_exclusion":
        # Candidate proposes NOT trading this symbol
        excluded = change_definition.get("symbol", "")
        if symbol == excluded:
            return None  # Don't open shadow — exclusion means "don't trade"

    elif change_type == "regime_conditioning":
        # Would need regime context to decide — for now, use original geometry
        # (the shadow will be collected; evaluation will filter by regime)
        return {"direction": direction, "stop_loss": stop_loss, "take_profit": take_profit}

    # Unsupported types
    return None


def _candidate_applies(candidate: Any, *, symbol: str, pattern: str) -> bool:
    """Determine if this candidate should shadow this specific opportunity."""
    defn = candidate.change_definition
    change_type = defn.get("type", "")

    # Pattern-specific candidates only apply to their pattern
    target_patterns = defn.get("patterns", [])
    if target_patterns and pattern not in target_patterns:
        return False

    # Symbol-specific candidates only apply to their symbol
    target_symbol = defn.get("symbol", "")
    if target_symbol and symbol != target_symbol:
        return False

    # Symbol exclusion applies only to the excluded symbol
    if change_type == "symbol_exclusion":
        return symbol == defn.get("symbol", "")

    return True
