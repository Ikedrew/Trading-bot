"""
New Engine v1.2 — Execution-ready 10-factor decision engine with strategy classification.

Sole execution authority when USE_NEW_PIPELINE=True.
Produces real OrderIntent objects suitable for MT5 execution.

AUTHORITY BOUNDARIES:
    CAN:
        - Detect patterns via signal orchestrator
        - Classify strategies (CONTINUATION / REVERSAL / FALSE_BREAK)
        - Score opportunities (10-component weighted)
        - Apply execution policy (EV gate, swing block, risk check)
        - Produce EXECUTE or NO_TRADE decisions
        - Build OrderIntent (symbol, side, volume, SL, TP)

    CANNOT:
        - Execute broker orders (execution/ owns that)
        - Bypass runtime guard chain (risk/ owns that)
        - Modify configuration (config.py is static at runtime)
        - Manage open positions (trade_management/ owns that)
        - Write to persistence directly (persistence writers own that)

Flow: Pattern Gate → Strategy Classification (A/B/C) → Score (strategy-weighted) → Confirmation → OrderIntent

Version history:
    v1.0 — 8-factor model (pattern, bias, market, trend, chop, vol, stability, confirm)
    v1.1 — 10-factor model (+ htf_alignment, h4_alignment)
    v1.2 — Strategy-classified scoring (3 weight profiles: continuation/reversal/false_break)
"""

from __future__ import annotations

from typing import Any

from data.mt5_data import Candle
from risk.manager import RiskManager
from risk.models import OrderIntent
from strategy.signals import Signal, Side
from strategy.signal_orchestrator import confirm_signal_detailed, ConfirmationStrength


# ─── SCORING WEIGHTS (GLOBAL FALLBACK — used when classification confidence < threshold)
# Strategy-specific weights are in strategy_weights.py

_GLOBAL_WEIGHTS = {
    "pattern_quality": 0.14,
    "bias_alignment": 0.18,
    "market_quality": 0.08,
    "trend_alignment": 0.10,
    "chop_clarity": 0.06,
    "volatility_quality": 0.07,
    "bias_stability": 0.07,
    "confirmation_pre": 0.06,
    "htf_alignment": 0.14,
    "h4_alignment": 0.10,
}

_MIN_SCORE_THRESHOLD = 0.35  # Minimum weighted score to proceed (noise filter — EV is primary discriminator)
_CLASSIFIER_CONFIDENCE_THRESHOLD = 0.5  # Below this, use global weights instead of strategy-specific


# ─── MAIN ENGINE ──────────────────────────────────────────────────────────────

def run_new_engine(
    *,
    candles: list[Candle],
    closed_i: int,
    symbol: str,
    bid: float,
    ask: float,
    engine_state: Any,
    config: Any,
    detected_patterns: list[Signal],
    risk_manager: RiskManager,
    htf_context: Any = None,
    cycle_id: int = 0,
    market_phase: str | None = None,
    market_phase_confidence: float = 0.0,
) -> dict[str, Any]:
    """
    10-factor strategy-classified decision engine (v1.2).

    Pipeline: Pattern → Classify (A/B/C) → Score (strategy-weighted) → Confirm → Risk → Intent

    Args:
        candles: Full candle history
        closed_i: Index of last closed bar
        symbol: Trading symbol
        bid: Current bid price
        ask: Current ask price
        engine_state: Current EngineState
        config: Config module
        detected_patterns: Pre-detected patterns from Pattern Gate
        risk_manager: RiskManager instance for SL/TP/sizing
        htf_context: Optional HTFContext (regime, bias, structure snapshots)

    Returns:
        {"action": "EXECUTE"|"NO_TRADE", "score": float, "components": dict,
         "strategy": str, "strategy_confidence": float, ...}
    """

    # ─── ENTITY IDENTITY (stable across all exit paths) ───────────────
    # Constructed from bar identity — not pattern-dependent.
    # Same bar always produces the same entity_id regardless of outcome.
    _entity_id = f"{symbol}_{int(candles[closed_i].time)}"

    # ─── GATE 2: SCORE + RISK ─────────────────────────────────────────

    # Select best pattern (prefer strong patterns)
    best_pattern = _select_best_pattern(detected_patterns)
    if best_pattern is None:
        return {"action": "NO_TRADE", "reason": "no_viable_pattern", "score": 0.0, "components": {},
                "strategy": None, "strategy_confidence": 0.0, "_best_pattern": None, "pattern": None,
                "assessment": None, "entity_id": _entity_id,
                "market_phase": market_phase, "market_phase_confidence": market_phase_confidence}

    # ─── PATTERN AUTHORITY (immutable after this point) ────────────────
    # This is the SINGLE SOURCE OF TRUTH for pattern identity.
    # No downstream layer may re-read, redetect, or override this value.
    authoritative_pattern = best_pattern.pattern  # e.g. "THREE_WHITE_SOLDIERS"

    # ─── STRATEGY ACTIVATION v1.3 (FULL PIPELINE) ─────────────────────
    # Pipeline: Regime → Eligibility → Mapping → Gating → Weighting → Selection
    # A strategy must be POSSIBLE before it is evaluated.
    from strategy.selection_activation import run_strategy_activation
    from core.pipeline.strategy_weights import get_weights_for_strategy, GLOBAL_WEIGHTS
    from core.pipeline.strategy_classifier import StrategyType

    # Get swing context (already computed upstream, passed via fields)
    _swing_dir = "NEUTRAL"
    _swing_bos = False
    _bos_source = "M5_SWING_CONTEXT"  # default (legacy fallback)
    _trend_alignment_source = "M5_EMA50"
    _trend_alignment_timeframe = "M5"
    _trend_alignment_confidence = 0.0

    # ─── H1 BOS AUTHORITY ─────────────────────────────────────────────
    # H1 BiasSnapshot.bos_confirmed is the authoritative BOS source.
    # M5 compute_swing_context() BOS is retained for diagnostics only.
    try:
        from core import config as _bos_cfg
        if getattr(_bos_cfg, "MARKET_CONTEXT_ENABLED", False) and htf_context is not None:
            _h1_bias = getattr(htf_context, "bias", None)
            if _h1_bias is not None:
                _h1_bos = getattr(_h1_bias, "bos_confirmed", False)
                _h1_bos_dir = getattr(_h1_bias, "bos_direction", "") or ""
                _h1_swing = getattr(_h1_bias, "swing_structure", "MIXED") or "MIXED"
                if _h1_bos:
                    _swing_bos = True
                    _bos_source = "H1_MARKET_CONTEXT"
                # H1 swing_structure → swing direction
                if _h1_swing == "HH_HL":
                    _swing_dir = "BULLISH"
                elif _h1_swing == "LH_LL":
                    _swing_dir = "BEARISH"
                else:
                    _swing_dir = "NEUTRAL"
    except Exception:
        pass  # Fallback to M5 defaults on any failure

    # ─── H4 REGIME AUTHORITY (MarketContext → strategy activation) ─────
    # When MARKET_CONTEXT_ENABLED, regime comes from H4 via MarketContext.
    # This replaces the M5 classify_regime() as authoritative source.
    _mc_regime: str | None = None
    _mc_regime_conf: float | None = None
    _regime_source = "M5_CLASSIFIER"  # default (legacy)
    try:
        from core import config as _regime_cfg
        if getattr(_regime_cfg, "MARKET_CONTEXT_ENABLED", False) and htf_context is not None:
            _h4_snap = getattr(htf_context, "regime", None)
            if _h4_snap is not None:
                _h4_class = getattr(_h4_snap, "classification", None)
                _h4_class_val = _h4_class.value if _h4_class and hasattr(_h4_class, "value") else ""
                # Map H4 classification to strategy activation regime
                if "TRENDING" in _h4_class_val:
                    _mc_regime = "TRENDING"
                elif _h4_class_val == "RANGING":
                    _mc_regime = "RANGE"
                elif _h4_class_val == "VOLATILE":
                    _mc_regime = "TRANSITIONAL"
                else:
                    _mc_regime = "TRANSITIONAL"
                _mc_regime_conf = getattr(_h4_snap, "confidence", 0.5)
                _regime_source = "H4_MARKET_CONTEXT"
    except Exception:
        pass  # Fallback to M5 classifier if anything fails

    activation = run_strategy_activation(
        candles=candles,
        closed_i=closed_i,
        pattern=best_pattern,
        swing_direction=_swing_dir,
        swing_break_confirmed=_swing_bos,
        market_context_regime=_mc_regime,
        market_context_regime_confidence=_mc_regime_conf,
    )

    # ─── STRATEGY TRACE LOGGING (full replay trace) ───────────────────
    try:
        from strategy.trace_activation import build_strategy_trace, emit_strategy_trace, emit_strategy_trace_discord
        _strat_trace = build_strategy_trace(
            symbol=symbol,
            cycle_id=cycle_id,
            activation=activation,
            pattern=best_pattern,
            entity_id=_entity_id,
        )
        _strategy_ts_utc_ms = _strat_trace.get("ts_utc_ms", 0)
        emit_strategy_trace(_strat_trace)
        emit_strategy_trace_discord(_strat_trace)
    except Exception:
        _strategy_ts_utc_ms = 0  # Trace failure must never affect execution

    # ─── H1 BOS STRUCTURAL PERMISSION (before scoring) ────────────────
    # This check validates that H1 market structure supports the trade type.
    # It executes BEFORE scoring to avoid wasting computation on
    # structurally invalid opportunities.
    # Data source: _swing_bos and _swing_dir (extracted from htf_context at engine start)
    _h1_bos_allowed = True
    _h1_bos_block_reason: str | None = None
    _strategy_type_for_bos = activation.selected_strategy or "CONTINUATION"

    # REVERSAL requires BOS confirmation (H1 authority)
    if "REVERSAL" in _strategy_type_for_bos and not _swing_bos:
        _h1_bos_allowed = False
        _h1_bos_block_reason = "h1_bos_not_confirmed (reversal requires H1 BOS)"

    # DIRECTIONAL ALIGNMENT (H1 swing direction authority)
    if _h1_bos_allowed and _swing_dir != "NEUTRAL":
        if best_pattern.side == Side.BUY and _swing_dir == "BEARISH" and not _swing_bos:
            _h1_bos_allowed = False
            _h1_bos_block_reason = "h1_swing_bearish (BUY blocked without H1 BOS)"
        elif best_pattern.side == Side.SELL and _swing_dir == "BULLISH" and not _swing_bos:
            _h1_bos_allowed = False
            _h1_bos_block_reason = "h1_swing_bullish (SELL blocked without H1 BOS)"

    if not _h1_bos_allowed:
        # M5 swing diagnostic still computed for metadata
        from core.pipeline.swing_context import compute_swing_context
        _swing_diag = compute_swing_context(candles, closed_i)
        return {
            "action": "NO_TRADE",
            "reason": f"swing_blocked: {_h1_bos_block_reason}",
            "score": 0.0,
            "components": {},
            "strategy": activation.selected_strategy,
            "strategy_confidence": activation.selected_weight,
            "strategy_reasoning": f"regime={activation.regime} conf={activation.regime_confidence:.2f}",
            "weights_used": "global_fallback",
            "activation_regime": activation.regime,
            "activation_regime_confidence": activation.regime_confidence,
            "regime_source": _regime_source,
            "trend_alignment_source": _trend_alignment_source,
            "trend_alignment_timeframe": _trend_alignment_timeframe,
            "trend_alignment_confidence": _trend_alignment_confidence,
            "swing_direction": _swing_dir,
            "swing_break_confirmed": _swing_bos,
            "bos_source": _bos_source,
            "m5_swing_bos_diagnostic": _swing_diag.swing_break_confirmed,
            "swing_phase": _swing_diag.swing_phase.value,
            "swing_strength": _swing_diag.swing_strength,
            "swing_reasoning": _swing_diag.reasoning,
            "_best_pattern": best_pattern,
            "pattern": authoritative_pattern,
            "entity_id": _entity_id,
            "cycle_id": cycle_id,
            "assessment": None,
        }
    # ─── END H1 BOS STRUCTURAL PERMISSION ─────────────────────────────

    # ─── STRUCTURE/BIAS SCORING (probabilistic — never blocks) ───────
    try:
        from strategy.structure_bias_scoring import score_structure_and_bias
        _sb_result = score_structure_and_bias(candles, closed_i, engine_state)
    except Exception:
        _sb_result = None

    # If no strategy selected → use global weights with reduced confidence
    # Strategy activation is ADVISORY, not a hard gate.
    if activation.selected_strategy is None:
        # Log rejection reasons but DO NOT block pipeline
        _rejection_summary = "; ".join(
            f"{r.strategy}@{r.stage}:{r.reason}" for r in activation.rejected_strategies[:5]
        )
        print(f"[STRATEGY ADVISORY] {symbol} | no strategy selected — using global weights | reasons: {_rejection_summary[:100]}")
        _active_weights = GLOBAL_WEIGHTS
    else:
        # Map selected strategy to weight profile
        _strategy_map = {
            "CONTINUATION": StrategyType.CONTINUATION,
            "REVERSAL": StrategyType.REVERSAL,
            "FALSE_BREAK": StrategyType.FALSE_BREAK,
        }
        _selected_type = _strategy_map.get(activation.selected_strategy)

        # Select weight profile based on activation confidence
        if _selected_type and activation.selected_weight >= _CLASSIFIER_CONFIDENCE_THRESHOLD:
            _active_weights = get_weights_for_strategy(_selected_type)
        else:
            _active_weights = GLOBAL_WEIGHTS

    # Compute all 10 component scores (raw, strategy-independent)
    components = _compute_all_scores(
        candles=candles,
        closed_i=closed_i,
        best_pattern=best_pattern,
        engine_state=engine_state,
        config=config,
        htf_context=htf_context,
    )

    # ─── TREND ALIGNMENT SOURCE METADATA ──────────────────────────────
    _trend_alignment_source = "M5_EMA50"
    _trend_alignment_timeframe = "M5"
    _trend_alignment_confidence = 0.0
    try:
        from core import config as _ta_meta_cfg
        if getattr(_ta_meta_cfg, "MARKET_CONTEXT_ENABLED", False) and htf_context is not None:
            _ta_bias = getattr(htf_context, "bias", None)
            if _ta_bias is not None:
                _ta_dir = getattr(_ta_bias, "direction", None)
                if _ta_dir is not None and hasattr(_ta_dir, "value") and _ta_dir.value != "NEUTRAL":
                    _trend_alignment_source = "H1_PHASE"
                    _trend_alignment_timeframe = "H1"
                    _trend_alignment_confidence = getattr(_ta_bias, "confidence", 0.0)
    except Exception:
        pass  # Keep M5 defaults

    # ─── DUAL SCORING ─────────────────────────────────────────────────
    # Score 1: Neutral (global weights — baseline truth)
    score_neutral = sum(_GLOBAL_WEIGHTS.get(k, 0.0) * v for k, v in components.items())
    score_neutral = round(score_neutral, 4)

    # Score 2: Strategy (contextual weights — strategy-specific lens)
    score_strategy = sum(_active_weights.get(k, 0.0) * v for k, v in components.items())
    score_strategy = round(score_strategy, 4)

    delta = round(score_strategy - score_neutral, 4)

    # Primary score for threshold check is the STRATEGY score
    score = score_strategy

    # ─── MARKET STATE EVALUATION ──────────────────────────────────────
    from core.pipeline.market_state_engine import get_market_state_engine
    _mse = get_market_state_engine()
    market_state_result = _mse.evaluate(score_neutral, score_strategy, activation.selected_strategy or "CONTINUATION")

    # ═══════════════════════════════════════════════════════════════════
    # OPPORTUNITY ASSESSMENT (analysis-policy boundary)
    # Everything ABOVE = market analysis. Everything BELOW = policy.
    # This object is FROZEN — no downstream component may mutate it.
    # ═══════════════════════════════════════════════════════════════════
    try:
        from core.models.opportunity_assessment import OpportunityAssessment
        _opportunity = OpportunityAssessment(
            symbol=symbol,
            cycle_id=cycle_id,
            bar_time=candles[closed_i].time,
            entity_id=_entity_id,
            pattern=authoritative_pattern,
            side=best_pattern.side.value,
            pattern_quality=components.get("pattern_quality", 0.0),
            selected_strategy=activation.selected_strategy,
            strategy_confidence=activation.selected_weight,
            regime=activation.regime,
            regime_confidence=activation.regime_confidence,
            eligible_strategies=tuple(activation.eligible_strategies),
            weights_used="strategy_specific" if activation.selected_weight >= _CLASSIFIER_CONFIDENCE_THRESHOLD else "global_fallback",
            components=dict(components),
            score_neutral=score_neutral,
            score_strategy=score_strategy,
            score_delta=delta,
            market_state=market_state_result.state.value,
            market_state_confidence=market_state_result.confidence,
            delta_stability=market_state_result.delta_stability,
            bias_alignment=components.get("bias_alignment", 0.0),
            trend_alignment=components.get("trend_alignment", 0.0),
            chop_clarity=components.get("chop_clarity", 0.0),
            volatility_quality=components.get("volatility_quality", 0.0),
            confirmation_pre=components.get("confirmation_pre", 0.0),
            htf_alignment=components.get("htf_alignment", 0.0),
            h4_alignment=components.get("h4_alignment", 0.0),
        )
    except Exception:
        _opportunity = None  # Assessment failure must never block trading
    # ═══════════════════════════════════════════════════════════════════

    # ─── REASONING (observational — never affects execution) ──────────
    _reasoning = None
    if _opportunity is not None:
        try:
            from core.reasoning import generate_reasoning
            _reasoning = generate_reasoning(assessment=_opportunity)
        except Exception:
            pass  # Reasoning failure must never affect trading
    # ─── END REASONING ────────────────────────────────────────────────

    # ─── UNCERTAINTY (observational — measures ambiguity) ─────────────
    _uncertainty = None
    if _opportunity is not None:
        try:
            from core.uncertainty import compute_uncertainty
            _uncertainty = compute_uncertainty(assessment=_opportunity, reasoning=_reasoning)
            # Attach uncertainty scores to assessment (frozen replace)
            if _uncertainty is not None:
                from dataclasses import replace as _dc_replace
                _opportunity = _dc_replace(
                    _opportunity,
                    uncertainty_score=_uncertainty.uncertainty_score,
                    confidence_modifier=_uncertainty.confidence_modifier,
                )
        except Exception:
            pass  # Uncertainty failure must never affect trading
    # ─── END UNCERTAINTY ──────────────────────────────────────────────

    # ─── EVIDENCE ATTRIBUTION (observational — decomposes score) ──────
    _attribution = None
    if _opportunity is not None:
        try:
            from core.attribution import compute_attribution
            _attribution = compute_attribution(assessment=_opportunity)
            # Attach contribution summary to assessment (frozen replace)
            if _attribution is not None and _attribution.contributions:
                from dataclasses import replace as _dc_replace
                _opportunity = _dc_replace(
                    _opportunity,
                    evidence_contributions=tuple(
                        c.to_dict() for c in _attribution.contributions
                    ),
                )
        except Exception:
            pass  # Attribution failure must never affect trading
    # ─── END EVIDENCE ATTRIBUTION ─────────────────────────────────────

    # ─── PERSIST ASSESSMENT (after all enrichment is complete) ─────────
    # Persisted AFTER reasoning, uncertainty, and attribution enrichment
    # so that the S3 record contains the fully populated object.
    if _opportunity is not None:
        try:
            from core.persistence.opportunity_assessment_writer import persist_opportunity_assessment
            persist_opportunity_assessment(_opportunity)
        except Exception:
            pass  # Persistence failure must never block trading
    # ─── END PERSIST ──────────────────────────────────────────────────

    # ─── EXECUTION POLICY ─────────────────────────────────────────────
    from core.pipeline.execution_policy import compute_execution_policy
    policy = compute_execution_policy(
        market_state_result=market_state_result,
        assessment=_opportunity,
    )

    # Common output fields for all return paths
    _strategy_meta = {
        "_best_pattern": best_pattern,  # Signal object (used by Bias FSM, not serialized to S3)
        "assessment": _opportunity,  # Frozen analytical snapshot (analysis-policy boundary)
        "pattern": authoritative_pattern,  # AUTHORITATIVE — all downstream layers MUST use this
        "entity_id": _entity_id,  # Causal link: ENTITY ← this decision
        "cycle_id": cycle_id,  # Causal link: same-cycle grouping
        "strategy_ts_utc_ms": _strategy_ts_utc_ms,  # Causal link: STRATEGY ← this decision
        "strategy": activation.selected_strategy,
        "strategy_confidence": activation.selected_weight,
        "strategy_reasoning": f"regime={activation.regime} conf={activation.regime_confidence:.2f}",
        "weights_used": "strategy_specific" if activation.selected_weight >= _CLASSIFIER_CONFIDENCE_THRESHOLD else "global_fallback",
        "activation_regime": activation.regime,
        "activation_regime_confidence": activation.regime_confidence,
        "regime_source": _regime_source,  # H4_MARKET_CONTEXT or M5_CLASSIFIER
        "trend_alignment_source": _trend_alignment_source,  # H1_PHASE or M5_EMA50
        "trend_alignment_timeframe": _trend_alignment_timeframe,  # H1 or M5
        "trend_alignment_confidence": _trend_alignment_confidence,
        "eligible_strategies": list(activation.eligible_strategies),
        "gated_strategies": list(activation.gated_strategies),
        "rejected_strategies": [{"s": r.strategy, "stage": r.stage, "reason": r.reason} for r in activation.rejected_strategies],
        "score_neutral": score_neutral,
        "score_strategy": score_strategy,
        "delta": delta,
        "market_state": market_state_result.state.value,
        "market_state_confidence": market_state_result.confidence,
        "market_state_reasoning": market_state_result.reasoning,
        "market_phase": market_phase,
        "market_phase_confidence": market_phase_confidence,
        "policy_trade_allowed": policy.trade_allowed,
        "policy_required_rr": policy.required_rr,
        "policy_max_size_fraction": policy.max_position_fraction,
        "policy_reasoning": policy.policy_reasoning,
        "reasoning": _reasoning,  # DecisionReasoning (observational — never affects decisions)
        "uncertainty": _uncertainty,  # UncertaintyAssessment (observational — measures ambiguity)
        "attribution": _attribution,  # ScoreAttribution (observational — decomposes score)
    }

    # ─── EXECUTION POLICY GATE ────────────────────────────────────────
    if not policy.trade_allowed:
        return {
            "action": "NO_TRADE",
            "reason": f"policy_blocked: {policy.block_reason}",
            "score": score,
            "components": components,
            **_strategy_meta,
        }

    if score < _MIN_SCORE_THRESHOLD:
        return {
            "action": "NO_TRADE",
            "reason": f"score_below_threshold ({score:.3f} < {_MIN_SCORE_THRESHOLD})",
            "score": score,
            "components": components,
            **_strategy_meta,
        }

    # ─── SWING CONTEXT (diagnostic metadata only) ───────────────────
    # M5 compute_swing_context() runs for diagnostic metadata.
    # H1 BOS gate already executed earlier (before scoring).
    from core.pipeline.swing_context import compute_swing_context, check_swing_permission

    _swing = compute_swing_context(candles, closed_i)

    _strategy_meta.update({
        "swing_direction": _swing.current_swing_direction.value,
        "swing_phase": _swing.swing_phase.value,
        "swing_strength": _swing.swing_strength,
        "swing_break_confirmed": _swing_bos,  # H1 authority (not M5)
        "swing_reasoning": _swing.reasoning,
        "bos_source": _bos_source,
        "m5_swing_bos_diagnostic": _swing.swing_break_confirmed,  # M5 BOS for comparison only
    })

    # ─── CONFIRMATION SCORE (probabilistic — NOT a gate) ─────────────
    # Computes candle quality as a 0.0–1.0 score. Never blocks execution.
    # Only permitted hard block: candle_range < minimum tick (data quality failure).

    confirmation = confirm_signal_detailed(best_pattern, candles)
    confirmation_score = _compute_confirmation_score(best_pattern, candles, market_state_result.state.value)

    # Data quality hard block ONLY (not trade quality)
    _candle = candles[best_pattern.bar_index]
    _candle_range = _candle.high - _candle.low
    if _candle_range <= 0:
        return {
            "action": "NO_TRADE",
            "reason": "data_invalid: zero_candle_range",
            "score": score,
            "components": components,
            **_strategy_meta,
        }

    # ─── RISK EVALUATION ──────────────────────────────────────────────

    # Use existing RiskManager for SL/TP/sizing
    # Primary path: assessment-based. Fallback: legacy signal if assessment failed.
    if _opportunity is not None:
        risk_decision = risk_manager.evaluate(
            assessment=_opportunity,
            candles=candles,
            bid=bid,
            ask=ask,
        )
    else:
        risk_decision = risk_manager.evaluate_signal(symbol, best_pattern, candles, bid, ask)

    if not risk_decision.accepted:
        return {
            "action": "NO_TRADE",
            "reason": f"risk_rejected: {risk_decision.rejection.reason if risk_decision.rejection else 'unknown'}",
            "score": score,
            "components": components,
            "confirmation_score": confirmation_score,
            **_strategy_meta,
        }

    intent = risk_decision.intent

    # ─── EXPECTED VALUE (confirmation feeds P_success) ────────────────
    from core.pipeline.expected_value import compute_expected_value, compute_dual_ev

    # ─── PROBABILITY ESTIMATION (dedicated authority) ─────────────────
    from core.pipeline.probability_estimator import get_probability_estimator
    _prob_estimator = get_probability_estimator()
    _prob_estimate = _prob_estimator.estimate(
        assessment=_opportunity,
        market_state_result=market_state_result,
        confirmation_score=confirmation_score,
    )

    ev_result = compute_expected_value(
        assessment=_opportunity,
        market_state_result=market_state_result,
        entry_price=intent.entry_reference,
        stop_loss=intent.sl,
        take_profit=intent.tp,
        confirmation_score=confirmation_score,
        probability_estimate=_prob_estimate,
    )

    # ─── DUAL EV: RESEARCH COMPARISON (observability only) ────────────
    # Computes empirical EV alongside synthetic for comparison logging.
    # Never affects execution. Gated by RESEARCH_ASSESSMENT_LOGGING config.
    _dual_ev = None
    try:
        from core import config as _ev_cfg
        if getattr(_ev_cfg, "RESEARCH_ASSESSMENT_LOGGING", True):
            _dual_ev = compute_dual_ev(
                synthetic_result=ev_result,
                pattern_name=authoritative_pattern,
                regime=activation.regime,
                market_state=market_state_result.state.value,
                symbol=symbol,
                timestamp_utc="",  # Not available at engine level
                components=components,
                reward=ev_result.reward,
                risk=ev_result.risk,
            )
    except Exception:
        pass  # Research comparison must never affect trading
    # ─── END DUAL EV ──────────────────────────────────────────────────

    # Re-evaluate policy with EV information
    policy_final = compute_execution_policy(
        market_state_result=market_state_result,
        assessment=_opportunity,
        ev_result=ev_result,
    )

    # Update meta with EV + final policy + confirmation
    _strategy_meta.update({
        "confirmation_score": confirmation_score,
        "confirmation_detail": confirmation.reason if not confirmation.confirmed else "strong",
        "ev": ev_result.ev,
        "ev_positive": ev_result.ev_positive,
        "p_success": ev_result.p_success,
        "p_failure": ev_result.p_failure,
        "ev_reward": ev_result.reward,
        "ev_risk": ev_result.risk,
        "rr_effective": ev_result.rr_effective,
        "ev_uncertainty_dampening": ev_result.uncertainty_dampening,
        "ev_reasoning": ev_result.reasoning,
        "probability_source": _prob_estimate.source,
        "probability_model_version": _prob_estimate.model_version,
        "probability_raw_score": _prob_estimate.raw_score,
        "probability_evidence": list(_prob_estimate.evidence_used),
        "policy_trade_allowed": policy_final.trade_allowed,
        "policy_required_rr": policy_final.required_rr,
        "policy_max_size_fraction": policy_final.max_position_fraction,
        "policy_reasoning": policy_final.policy_reasoning,
    })

    # Attach dual EV comparison (observational — never affects decisions)
    if _dual_ev is not None:
        _strategy_meta["dual_ev"] = _dual_ev.to_dict()

    if not policy_final.trade_allowed:
        # ─── EV GATE BYPASS (controlled experiment) ───────────────────
        _ev_gate_enabled = True
        try:
            from core import config as _ev_gate_cfg
            _ev_gate_enabled = getattr(_ev_gate_cfg, "ENABLE_EV_GATE", True)
        except Exception:
            pass

        if _ev_gate_enabled:
            return {
                "action": "NO_TRADE",
                "reason": f"ev_policy_blocked: {policy_final.block_reason}",
                "score": score,
                "components": components,
                # Expose rejected trade parameters for research shadow trade creation
                "rejected_trade": {
                    "entry_reference": intent.entry_reference,
                    "sl": intent.sl,
                    "tp": intent.tp,
                    "side": intent.side.name if hasattr(intent.side, "name") else str(intent.side),
                    "volume": intent.volume,
                    "pattern": intent.pattern,
                },
                **_strategy_meta,
            }
        else:
            # EV gate bypassed — continue to execution with full observability
            _strategy_meta.update({
                "ev_gate_enabled": False,
                "ev_rejection_bypassed": True,
                "ev_would_have_blocked": policy_final.block_reason,
            })
            print(f"[EV GATE BYPASSED] symbol={symbol} ev={ev_result.ev:.6f} reason={policy_final.block_reason} — continuing to execution")

    return {
        "action": "EXECUTE",
        "intent": intent,
        "score": score,
        "components": components,
        "confirmation_score": confirmation_score,
        "confirmation_strength": confirmation.strength.value if confirmation.confirmed else "WEAK",
        "pattern": authoritative_pattern,
        "side": best_pattern.side.value,
        **_strategy_meta,
    }


# ─── SCORING COMPONENTS (10-FACTOR) ──────────────────────────────────────────

_STRONG_PATTERNS = frozenset({
    "BULLISH_ENGULFING", "BEARISH_ENGULFING",
    "EVENING_STAR", "MORNING_STAR",
})

_WEAK_PATTERNS = frozenset({
    "THREE_BLACK_CROWS", "THREE_WHITE_SOLDIERS",
    "TWEEZER_TOP", "TWEEZER_BOTTOM",
    "HAMMER", "HANGING_MAN",
    "INVERTED_HAMMER", "SHOOTING_STAR",
})


def _select_best_pattern(patterns: list[Signal]) -> Signal | None:
    """Select highest-quality pattern from detected list."""
    if not patterns:
        return None
    # Prefer strong patterns over weak
    strong = [s for s in patterns if s.pattern in _STRONG_PATTERNS]
    if strong:
        return strong[0]
    return patterns[0]


def _compute_all_scores(
    *,
    candles: list[Candle],
    closed_i: int,
    best_pattern: Signal,
    engine_state: Any,
    config: Any,
    htf_context: Any = None,
) -> dict[str, float]:
    """Compute all 10 weighted scoring components. Each returns 0.0–1.0."""

    return {
        "pattern_quality": _score_pattern_quality(best_pattern),
        "bias_alignment": _score_bias_alignment(best_pattern, engine_state),
        "market_quality": _score_market_quality(candles, closed_i, config, htf_context),
        "trend_alignment": _score_trend_alignment(best_pattern, candles, closed_i, config, htf_context),
        "chop_clarity": _score_chop_clarity(candles, closed_i, config, htf_context),
        "volatility_quality": _score_volatility_quality(candles, closed_i),
        "bias_stability": _score_bias_stability(engine_state),
        "confirmation_pre": _score_confirmation_pre(best_pattern, candles),
        "htf_alignment": _score_htf(best_pattern, htf_context),
        "h4_alignment": _score_h4(best_pattern, htf_context),
    }


# ─── ORIGINAL 8 FACTORS ──────────────────────────────────────────────────────

def _score_pattern_quality(pattern: Signal) -> float:
    """Strong pattern = 1.0, weak = 0.5, unknown = 0.3."""
    if pattern.pattern in _STRONG_PATTERNS:
        return 1.0
    if pattern.pattern in _WEAK_PATTERNS:
        return 0.5
    return 0.3


def _score_bias_alignment(pattern: Signal, state: Any) -> float:
    """How well does the pattern align with confirmed bias? Returns 0.5 when FSM is EXPIRED."""
    current_bias = getattr(state, "current_bias", None)
    bias_phase = getattr(state, "bias_phase", "EXPIRED")

    if current_bias is None or bias_phase == "EXPIRED":
        return 0.5  # Neutral — FSM not active, no penalty or bonus

    if bias_phase == "CONFIRMED" and current_bias == pattern.side:
        return 1.0  # Perfect alignment
    if bias_phase in ("FORMING", "CONFIRMING") and current_bias == pattern.side:
        return 0.7  # Building alignment
    if bias_phase == "CONFIRMED" and current_bias != pattern.side:
        return 0.0  # Counter-trend — zero
    if bias_phase == "WEAKENING" and current_bias == pattern.side:
        return 0.4  # Weakening but still aligned
    return 0.3  # Ambiguous


def _score_market_quality(candles: list[Candle], closed_i: int, config: Any, htf_context: Any = None) -> float:
    """
    Setup quality score.

    Authority: M15 StructureSnapshot.quality_score (when available).
    Fallback:  M5 net displacement ratio (original logic).

    M15 quality represents "is there a valid opportunity forming?" —
    a setup-timeframe concern, not an execution-timeframe concern.
    """
    # ─── M15 AUTHORITY (primary) ──────────────────────────────────────
    try:
        from core import config as _mq_cfg
        if getattr(_mq_cfg, "MARKET_CONTEXT_ENABLED", False) and htf_context is not None:
            struct_snap = getattr(htf_context, "structure", None)
            if struct_snap is not None:
                quality = getattr(struct_snap, "quality_score", None)
                if quality is not None:
                    return min(1.0, max(0.0, quality))
    except Exception:
        pass  # Fallback to M5

    # ─── M5 FALLBACK (diagnostic — original logic) ────────────────────
    lookback = int(getattr(config, "MARKET_FILTER_LOOKBACK", 5))
    if closed_i < lookback:
        return 0.5

    window = candles[closed_i - lookback: closed_i]
    total_range = sum(c.high - c.low for c in window)
    if total_range <= 0:
        return 0.0

    net_move = abs(window[-1].close - window[0].open)
    ratio = net_move / total_range

    if ratio >= 0.35:
        return 1.0
    if ratio >= 0.25:
        return 0.7
    if ratio >= 0.15:
        return 0.4
    return 0.1


def _score_trend_alignment(pattern: Signal, candles: list[Candle], closed_i: int, config: Any, htf_context: Any = None) -> float:
    """
    Trend alignment score.

    Authority: H1 Phase direction (when available from HTFContext).
    Fallback:  M5 EMA-50 position (original logic, used when H1 unavailable).

    Returns 0.0–1.0:
        1.0 = trade direction aligned with trend
        0.5 = neutral / no data
        0.2 = counter-trend
    """
    # ─── H1 AUTHORITY (primary) ───────────────────────────────────────
    try:
        from core import config as _ta_cfg
        if getattr(_ta_cfg, "MARKET_CONTEXT_ENABLED", False) and htf_context is not None:
            bias_snap = getattr(htf_context, "bias", None)
            if bias_snap is not None:
                h1_direction = getattr(bias_snap, "direction", None)
                h1_confidence = getattr(bias_snap, "confidence", 0.0)
                if h1_direction is not None and hasattr(h1_direction, "value"):
                    dir_val = h1_direction.value  # "BULLISH" / "BEARISH" / "NEUTRAL"
                    if dir_val == "NEUTRAL":
                        return 0.5  # H1 neutral — no directional bias
                    # H1 aligned with trade direction
                    if (dir_val == "BULLISH" and pattern.side == Side.BUY) or \
                       (dir_val == "BEARISH" and pattern.side == Side.SELL):
                        return min(1.0, 0.6 + (0.4 * h1_confidence))  # 0.6–1.0
                    # H1 contradicts trade direction
                    return max(0.0, 0.3 - (0.2 * h1_confidence))  # 0.1–0.3
    except Exception:
        pass  # Fallback to M5 on any failure

    # ─── M5 FALLBACK (original EMA-50 logic) ──────────────────────────
    period = int(getattr(config, "TREND_EMA_PERIOD", 50))
    if closed_i < period:
        return 0.5  # Insufficient data — neutral

    closes = [candles[i].close for i in range(closed_i - period, closed_i + 1)]
    ema = sum(closes) / len(closes)  # Simple approximation
    price = candles[closed_i].close

    if pattern.side == Side.BUY:
        if price > ema:
            return 1.0  # Above EMA, buying — aligned
        return 0.2  # Below EMA, buying — counter-trend
    else:
        if price < ema:
            return 1.0  # Below EMA, selling — aligned
        return 0.2  # Above EMA, selling — counter-trend


def _score_chop_clarity(candles: list[Candle], closed_i: int, config: Any, htf_context: Any = None) -> float:
    """
    Structure clarity score (inverse of chop).

    Authority: M15 StructureSnapshot (when available).
              High M15 quality + at_key_level = high clarity.
    Fallback:  M5 candle overlap ratio (original logic).

    Structure clarity represents "how clear is the setup?" —
    a setup-timeframe concern owned by M15.
    """
    # ─── M15 AUTHORITY (primary) ──────────────────────────────────────
    try:
        from core import config as _cc_cfg
        if getattr(_cc_cfg, "MARKET_CONTEXT_ENABLED", False) and htf_context is not None:
            struct_snap = getattr(htf_context, "structure", None)
            if struct_snap is not None:
                quality = getattr(struct_snap, "quality_score", None)
                at_level = getattr(struct_snap, "at_key_level", False)
                if quality is not None:
                    # M15 quality directly maps to clarity (higher quality = less chop)
                    clarity = quality
                    # Bonus for being at a key level (clear structural reference)
                    if at_level:
                        clarity = min(1.0, clarity + 0.15)
                    return min(1.0, max(0.0, clarity))
    except Exception:
        pass  # Fallback to M5

    # ─── M5 FALLBACK (diagnostic — original logic) ────────────────────
    lookback = int(getattr(config, "MARKET_FILTER_LOOKBACK", 5))
    if closed_i < lookback:
        return 0.5

    window = candles[closed_i - lookback + 1: closed_i + 1]
    if len(window) < 2:
        return 0.5

    overlap_hits = 0
    pairs = len(window) - 1
    for i in range(1, len(window)):
        prev = window[i - 1]
        cur = window[i]
        overlap = min(prev.high, cur.high) - max(prev.low, cur.low)
        if overlap <= 0:
            continue
        denom = min(prev.high - prev.low, cur.high - cur.low)
        if denom <= 0:
            continue
        if overlap / denom >= 0.5:
            overlap_hits += 1

    overlap_ratio = overlap_hits / pairs if pairs > 0 else 0.0
    # Invert: low overlap = high clarity
    return max(0.0, 1.0 - overlap_ratio)


def _score_volatility_quality(candles: list[Candle], closed_i: int) -> float:
    """ATR-based volatility quality. Normal vol = good, extreme = bad."""
    lookback = 5
    if closed_i < lookback:
        return 0.5

    recent = candles[closed_i - lookback: closed_i]
    total_range = sum(c.high - c.low for c in recent)
    if total_range <= 0:
        return 0.0

    net_move = abs(recent[-1].close - recent[0].open)
    ratio = net_move / total_range

    # Moderate directional movement is ideal
    if 0.25 <= ratio <= 0.60:
        return 1.0
    if 0.15 <= ratio < 0.25:
        return 0.6
    if ratio > 0.60:
        return 0.7  # Very directional — still good
    return 0.2  # Very choppy


def _score_bias_stability(state: Any) -> float:
    """Bias strength normalized to 0–1. Returns 0.5 (neutral) when bias is EXPIRED."""
    bias_phase = getattr(state, "bias_phase", "EXPIRED")
    if bias_phase == "EXPIRED":
        return 0.5  # Neutral — FSM not active, no penalty or bonus
    stability = getattr(state, "bias_strength", 0.0)
    return min(1.0, stability / 100.0)


def _score_confirmation_pre(pattern: Signal, candles: list[Candle]) -> float:
    """Lightweight pre-check of candle quality (body strength preview)."""
    c = candles[pattern.bar_index]
    candle_range = c.high - c.low
    if candle_range <= 0:
        return 0.0
    body = abs(c.close - c.open)
    body_pct = body / candle_range
    return min(1.0, body_pct / 0.60)  # Normalized: 60% body = 1.0


# ─── NEW FACTORS (v1.1) ──────────────────────────────────────────────────────

def _score_htf(pattern: Signal, htf_context: Any) -> float:
    """
    Higher-timeframe alignment score (H1 bias + M15 structure combined).

    Evaluates whether the H1 directional bias and M15 structural quality
    support the trade direction.

    Returns 0.0–1.0:
        1.0 = H1 bias aligned + M15 structure high quality
        0.5 = neutral / partial alignment
        0.0 = H1 bias contradicts trade direction
    """
    if htf_context is None:
        return 0.5  # No HTF data available — neutral (no penalty, no bonus)

    score = 0.5  # Start neutral

    # H1 bias alignment
    bias_snap = getattr(htf_context, "bias", None)
    if bias_snap is not None:
        h1_direction = getattr(bias_snap, "direction", None)
        h1_confidence = getattr(bias_snap, "confidence", 0.0)

        if h1_direction is not None and hasattr(h1_direction, "value"):
            direction_val = h1_direction.value  # "BULLISH" / "BEARISH" / "NEUTRAL"

            if direction_val == "NEUTRAL":
                score = 0.4  # Neutral H1 — slight penalty
            elif (direction_val == "BULLISH" and pattern.side == Side.BUY) or \
                 (direction_val == "BEARISH" and pattern.side == Side.SELL):
                # H1 aligned with trade direction
                score = 0.5 + (0.5 * h1_confidence)  # 0.5–1.0 based on confidence
            else:
                # H1 contradicts trade direction
                score = max(0.0, 0.3 - (0.3 * h1_confidence))  # 0.0–0.3 (stronger contradiction = lower)

    # M15 structure quality (additive modifier)
    structure_snap = getattr(htf_context, "structure", None)
    if structure_snap is not None:
        quality = getattr(structure_snap, "quality_score", 0.0)
        # M15 quality shifts score slightly (±0.15 max)
        if quality >= 0.7:
            score = min(1.0, score + 0.1)
        elif quality < 0.3:
            score = max(0.0, score - 0.1)

    return round(score, 4)


def _score_h4(pattern: Signal, htf_context: Any) -> float:
    """
    H4 regime alignment score.

    Evaluates whether the H4 macro regime supports taking a trade
    in the pattern's direction.

    Returns 0.0–1.0:
        1.0 = H4 trending in trade direction with high confidence
        0.5 = neutral / transitional regime
        0.0 = adverse regime (volatile/choppy) or strong counter-trend
    """
    if htf_context is None:
        return 0.5  # No H4 data available — neutral

    regime_snap = getattr(htf_context, "regime", None)
    if regime_snap is None:
        return 0.5

    classification = getattr(regime_snap, "classification", None)
    confidence = getattr(regime_snap, "confidence", 0.0)
    trend_bias = getattr(regime_snap, "trend_bias", "NEUTRAL")
    trend_strength = getattr(regime_snap, "trend_strength", 0.0)

    if classification is None:
        return 0.5

    class_val = classification.value if hasattr(classification, "value") else str(classification)

    # Adverse regimes — penalize
    if class_val == "VOLATILE":
        return max(0.0, 0.2 - (0.2 * confidence))  # 0.0–0.2
    if class_val == "RANGING":
        return max(0.1, 0.35 - (0.2 * confidence))  # 0.15–0.35

    # Transitional — neutral
    if class_val == "TRANSITIONAL":
        return 0.45

    # Trending — check alignment with trade direction
    if class_val in ("TRENDING_BULLISH", "TRENDING_BEARISH"):
        h4_is_bullish = (class_val == "TRENDING_BULLISH")
        trade_is_buy = (pattern.side == Side.BUY)

        if h4_is_bullish == trade_is_buy:
            # H4 trend aligned with trade direction
            return min(1.0, 0.6 + (0.4 * trend_strength))  # 0.6–1.0
        else:
            # Counter-trend trade against H4
            return max(0.0, 0.25 - (0.15 * trend_strength))  # 0.1–0.25

    return 0.5  # Unknown classification — neutral


# ─── CONFIRMATION SCORE (REGIME-ADAPTIVE, PROBABILISTIC) ─────────────────────

# Regime-adaptive baselines (scaling references, NOT rejection thresholds)
_CONFIRMATION_BASELINES = {
    "STRUCTURED": 0.25,
    "TRANSITIONAL": 0.35,
    "CHOP": 0.50,
}


def _compute_confirmation_score(
    pattern: Signal,
    candles: list[Candle],
    market_state: str,
) -> float:
    """
    Compute confirmation quality as a continuous 0.0–1.0 score.

    This is a PROBABILISTIC MODIFIER, not a gate.
    It measures candle strength relative to the current regime's volatility baseline.

    Regime-adaptive:
        STRUCTURED → lower body requirement (small candles are normal)
        TRANSITIONAL → moderate requirement
        CHOP → higher requirement (need strong conviction)

    Returns:
        0.0 = no directional conviction (doji)
        0.5 = adequate for regime
        1.0 = strong candle confirmation

    NEVER blocks execution. Only shapes P_success in EV.
    """
    c = candles[pattern.bar_index]
    candle_range = c.high - c.low

    if candle_range <= 0:
        return 0.0  # Zero range = no data (hard block handled upstream)

    body = abs(c.close - c.open)
    body_pct = body / candle_range

    # Wick rejection quality (lower wick for BUY, upper wick for SELL)
    if pattern.side == Side.BUY:
        rejection_wick = (min(c.open, c.close) - c.low) / candle_range
    else:
        rejection_wick = (c.high - max(c.open, c.close)) / candle_range

    # Directional conviction (close location relative to range)
    if pattern.side == Side.BUY:
        close_location = (c.close - c.low) / candle_range
    else:
        close_location = (c.high - c.close) / candle_range

    # Get regime baseline
    baseline = _CONFIRMATION_BASELINES.get(market_state, 0.35)

    # Score body relative to regime expectation
    # body_pct at baseline = 0.5, body_pct at 2x baseline = 1.0, body_pct at 0 = 0.0
    if baseline > 0:
        body_score = min(1.0, body_pct / (baseline * 2.0))
    else:
        body_score = min(1.0, body_pct / 0.70)

    # Composite: body (50%) + rejection wick (25%) + close location (25%)
    confirmation_score = (body_score * 0.50) + (rejection_wick * 0.25) + (close_location * 0.25)

    return round(max(0.0, min(1.0, confirmation_score)), 4)
