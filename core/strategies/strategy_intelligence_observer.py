"""
Strategy Intelligence Observer — Pipeline integration adapter.

Bridges the ObserverRegistry dispatch to the StrategyObserver +
observation persistence layer. Called as observer #7 after decision
trace is built.

This module:
    - Extracts market context from ObserverContext
    - Builds a market snapshot for the condition evaluator
    - Runs StrategyObserver.observe()
    - Persists observations to local JSONL + S3
    - Never raises (fire-and-forget)
    - Never modifies engine_result or any mutable state
    - Never influences trading decisions

Design: read-only intelligence observer.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Module-level singleton (lazy init)
_observer_instance = None


def _get_observer():
    """Lazy-init the strategy observer singleton."""
    global _observer_instance
    if _observer_instance is None:
        from core.strategies.strategy_observer import StrategyObserver
        _observer_instance = StrategyObserver(max_observations=5000)
    return _observer_instance


def observe_strategy_intelligence(ctx: Any) -> None:
    """
    Strategy intelligence observation — called by ObserverRegistry.

    Extracts market context and pattern from the engine result,
    evaluates all strategies, creates observations, and persists.

    Args:
        ctx: ObserverContext from the observer dispatch.

    Never raises. Failure is logged and silently ignored.
    """
    try:
        _do_observe(ctx)
    except Exception as exc:
        logger.debug("[STRATEGY_OBSERVER] observation failed: %s", exc)


def _do_observe(ctx: Any) -> None:
    """Internal observation logic. May raise."""
    from core.strategies.condition_evaluator import build_market_snapshot
    from core.strategies.observation_persistence import (
        build_observation_record,
        persist_strategy_observation,
    )

    # ─── 1. EXTRACT CONTEXT ───────────────────────────────────────────
    engine_result = ctx.engine_result or {}
    htf_context = ctx.htf_context
    market_ctx = getattr(ctx, "market_context", None)

    # Market phase (from engine result or htf_context)
    market_phase = engine_result.get("market_phase", "") or ""
    regime = ""
    h1_direction = ""
    h4_regime = ""
    h4_trend_bias = ""
    h4_trend_strength = 0.0
    h1_bos_confirmed = False
    m15_at_key_level = False
    m15_order_block_present = False
    m15_quality_score = 0.0
    m5_bias_strength = 0.0
    m5_bias_direction = ""
    m5_trigger_ready = False
    tradability_score = 0.0

    # ─── PREFERRED SOURCE: MarketContext (if available) ────────────────
    if market_ctx is not None and hasattr(market_ctx, "regime") and hasattr(market_ctx, "phase"):
        _regime = getattr(market_ctx, "regime", None)
        regime = _regime.value if hasattr(_regime, "value") else str(_regime or "")
        _phase = getattr(market_ctx, "phase", None)
        if _phase:
            market_phase = _phase.value if hasattr(_phase, "value") else str(_phase)
        _dir = getattr(market_ctx, "direction", None)
        h1_direction = _dir.value if hasattr(_dir, "value") else str(_dir or "")
        tradability_score = float(getattr(market_ctx, "tradability_score", 0.0) or 0.0)

        h4 = getattr(market_ctx, "h4", None)
        if h4:
            h4_regime = getattr(h4, "regime", "") or ""
            h4_trend_bias = getattr(h4, "trend_bias", "") or ""
            h4_trend_strength = float(getattr(h4, "trend_strength", 0.0) or 0.0)

        h1 = getattr(market_ctx, "h1", None)
        if h1:
            h1_direction = getattr(h1, "direction", "") or ""
            h1_bos_confirmed = bool(getattr(h1, "bos_confirmed", False))

        m15 = getattr(market_ctx, "m15", None)
        if m15:
            m15_at_key_level = bool(getattr(m15, "at_key_level", False))
            m15_order_block_present = bool(getattr(m15, "order_block_present", False))
            m15_quality_score = float(getattr(m15, "quality_score", 0.0) or 0.0)

        m5 = getattr(market_ctx, "m5", None)
        if m5:
            m5_bias_strength = float(getattr(m5, "bias_strength", 0.0) or 0.0)
            m5_bias_direction = getattr(m5, "bias_direction", "") or ""
            m5_trigger_ready = bool(getattr(m5, "trigger_ready", False))

    # ─── FALLBACK: Legacy htf_context ─────────────────────────────────
    elif htf_context is not None:
        if hasattr(htf_context, "regime") and hasattr(htf_context, "phase"):
            _regime = getattr(htf_context, "regime", None)
            regime = _regime.value if hasattr(_regime, "value") else str(_regime or "")
            _phase = getattr(htf_context, "phase", None)
            if _phase and not market_phase:
                market_phase = _phase.value if hasattr(_phase, "value") else str(_phase)
            _dir = getattr(htf_context, "direction", None)
            h1_direction = _dir.value if hasattr(_dir, "value") else str(_dir or "")
            tradability_score = float(getattr(htf_context, "tradability_score", 0.0) or 0.0)

            h4 = getattr(htf_context, "h4", None)
            if h4:
                h4_regime = getattr(h4, "regime", "") or ""
                h4_trend_bias = getattr(h4, "trend_bias", "") or ""
                h4_trend_strength = float(getattr(h4, "trend_strength", 0.0) or 0.0)

            h1 = getattr(htf_context, "h1", None)
            if h1:
                h1_direction = getattr(h1, "direction", "") or ""
                h1_bos_confirmed = bool(getattr(h1, "bos_confirmed", False))

            m15 = getattr(htf_context, "m15", None)
            if m15:
                m15_at_key_level = bool(getattr(m15, "at_key_level", False))
                m15_order_block_present = bool(getattr(m15, "order_block_present", False))
                m15_quality_score = float(getattr(m15, "quality_score", 0.0) or 0.0)

            m5 = getattr(htf_context, "m5", None)
            if m5:
                m5_bias_strength = float(getattr(m5, "bias_strength", 0.0) or 0.0)
                m5_bias_direction = getattr(m5, "bias_direction", "") or ""
                m5_trigger_ready = bool(getattr(m5, "trigger_ready", False))
        else:
            # Legacy htf_context (bias/regime objects only)
            _bias = getattr(htf_context, "bias", None)
            if _bias:
                h1_direction = getattr(_bias, "direction", "") or ""
                if hasattr(h1_direction, "value"):
                    h1_direction = h1_direction.value
                h1_bos_confirmed = bool(getattr(_bias, "bos_confirmed", False))

    # Fallback regime from engine_result
    if not regime:
        regime = engine_result.get("activation_regime", "") or ""

    # ─── 2. EXTRACT PATTERN ───────────────────────────────────────────
    pattern_detected = engine_result.get("pattern", "") or ""

    # ─── 3. BUILD SNAPSHOT ────────────────────────────────────────────
    snapshot = build_market_snapshot(
        regime=regime,
        phase=market_phase,
        direction=h1_direction,
        h4_regime=h4_regime,
        h4_trend_bias=h4_trend_bias,
        h4_trend_strength=h4_trend_strength,
        h1_direction=h1_direction,
        h1_bos_confirmed=h1_bos_confirmed,
        m15_at_key_level=m15_at_key_level,
        m15_order_block_present=m15_order_block_present,
        m15_quality_score=m15_quality_score,
        m5_bias_strength=m5_bias_strength,
        m5_bias_direction=m5_bias_direction,
        m5_trigger_ready=m5_trigger_ready,
        tradability_score=tradability_score,
        pattern_detected=pattern_detected,
    )

    # ─── 4. OBSERVE ──────────────────────────────────────────────────
    observer = _get_observer()
    result = observer.observe(
        snapshot=snapshot,
        pattern_detected=pattern_detected,
        symbol=ctx.symbol,
        cycle_id=ctx.cycle_id,
        timestamp_utc=ctx.bar_time,
    )

    # ─── 5. PERSIST ──────────────────────────────────────────────────
    # Build decision context for enrichment
    action = engine_result.get("action", "NO_TRADE")
    score = engine_result.get("score", 0.0)
    reason = engine_result.get("reason", "")
    side = engine_result.get("side", "")

    # Build candidate strategy summaries
    candidate_summaries = []
    for obs in observer.get_observations()[-5:]:  # Last 5 = this cycle
        if obs.cycle_id == ctx.cycle_id and obs.symbol == ctx.symbol:
            candidate_summaries.append({
                "strategy_id": obs.strategy_id,
                "eligible": obs.eligible_by_phase,
                "status": obs.overall_status,
                "confidence": round(obs.confidence, 4),
            })

    # Persist one record per cycle (summary of all strategies)
    _strategy_family = _dominant_family(candidate_summaries)

    # Fallback: derive family from detected pattern if phase-matching failed
    if not _strategy_family and pattern_detected:
        try:
            from core.strategy_family import classify_pattern
            _fam = classify_pattern(pattern_detected)
            if _fam:
                _strategy_family = _fam.value
        except Exception:
            pass

    # Final fallback: mark as UNKNOWN rather than blank
    if not _strategy_family:
        _strategy_family = "UNKNOWN"

    record = build_observation_record(
        observation_id=f"{ctx.symbol}_{ctx.cycle_id}_{int(ctx.bar_time)}",
        timestamp_utc=ctx.bar_time,
        symbol=ctx.symbol,
        cycle_id=ctx.cycle_id,
        market_phase=market_phase,
        h4_regime=h4_regime or regime,
        h1_bias=h1_direction,
        direction=h1_direction,
        detected_pattern=pattern_detected,
        strategy_family=_strategy_family,
        candidate_strategies=candidate_summaries,
        strategy_conditions={
            "phase_eligible_count": result.phase_eligible_count,
            "fully_met_count": result.fully_met_count,
            "partially_met_count": result.partially_met_count,
            "not_met_count": result.not_met_count,
            "phase_eligible": list(result.phase_eligible_strategies),
            "fully_met": list(result.fully_met_strategies),
        },
        conditions_passed=result.fully_met_count,
        conditions_failed=result.not_met_count,
        conditions_missing=0,
        evaluation_status=_cycle_status(result),
        confidence=_cycle_confidence(candidate_summaries),
        tradability_score=tradability_score,
        eligible_by_phase=result.phase_eligible_count > 0,
        pattern_in_triggers=any(
            obs.pattern_in_strategy_triggers
            for obs in observer.get_observations()[-5:]
            if obs.cycle_id == ctx.cycle_id and obs.symbol == ctx.symbol
        ),
    )

    # Add decision context (enrichment, not used for strategy evaluation)
    record["decision_action"] = action
    record["decision_score"] = round(float(score or 0), 4)
    record["decision_side"] = side
    record["decision_reason"] = str(reason)[:200] if reason else ""

    # ─── Phase 3 Step 6 — observation-layer lineage enrichment ────────
    # Identity semantics: the existing {symbol}_{cycle}_{ts} observation_id is
    # PRESERVED as the dataset-local ID. We add the observation-level entity_id
    # plus the canonical root ONLY when the engine already established one for
    # this bar. Non-opportunity cycles keep "" — nothing is fabricated, so a
    # bare observation stays joinable via symbol+cycle+bar_time without ever
    # claiming an opportunity it did not produce.
    record["bar_time"] = int(ctx.bar_time)
    record["timeframe"] = "M5"
    record.setdefault("entity_id", f"{ctx.symbol}_{int(ctx.bar_time)}")
    record["entity_id"] = record.get("entity_id", "") or f"{ctx.symbol}_{int(ctx.bar_time)}"
    record["canonical_opportunity_id"] = str(
        engine_result.get("canonical_opportunity_id", "") or ""
    )

    # Add entity_id for deterministic joins to shadow_trades and decision_trace
    # entity_id format: f"{symbol}_{bar_time}" — same as decision pipeline
    record["entity_id"] = engine_result.get("entity_id", "") or f"{ctx.symbol}_{int(ctx.bar_time)}"

    persist_strategy_observation(record)


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════


def _dominant_family(candidates: list[dict]) -> str:
    """Return the family of the highest-confidence eligible strategy."""
    eligible = [c for c in candidates if c.get("eligible")]
    if not eligible:
        return ""
    best = max(eligible, key=lambda c: c.get("confidence", 0))
    # Look up family from strategy ID
    try:
        from core.strategies.registry import get_strategy
        s = get_strategy(best["strategy_id"])
        if s:
            return s.family_name
    except Exception:
        pass
    return ""


def _cycle_status(result: Any) -> str:
    """Determine overall cycle evaluation status."""
    if result.fully_met_count > 0:
        return "STRATEGIES_FULLY_MET"
    elif result.phase_eligible_count > 0:
        return "STRATEGIES_ELIGIBLE"
    else:
        return "NO_ELIGIBLE_STRATEGIES"


def _cycle_confidence(candidates: list[dict]) -> float:
    """Average confidence of eligible strategies."""
    eligible = [c for c in candidates if c.get("eligible")]
    if not eligible:
        return 0.0
    return round(sum(c.get("confidence", 0) for c in eligible) / len(eligible), 4)


# ═══════════════════════════════════════════════════════════════════════════════
# TESTING / DIAGNOSTICS
# ═══════════════════════════════════════════════════════════════════════════════


def get_observer_instance():
    """Access the singleton observer for testing/diagnostics."""
    return _get_observer()


def reset_observer():
    """Reset the observer singleton. For testing only."""
    global _observer_instance
    if _observer_instance is not None:
        _observer_instance.clear()
    _observer_instance = None
