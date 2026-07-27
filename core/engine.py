"""Bar evaluation: pipeline describes; DecisionEngine renders the sole Decision (Stage 3)."""

from __future__ import annotations

import logging

from core.config import CHOP_FILTER_ENABLED, TREND_FILTER_ENABLED
from core.engine_state import EngineState
from core.pipeline.confirmations import run_confirmations
from core.pipeline.dashboard import record_cycle
from core.pipeline.decision_engine import DecisionEngine
from core.pipeline.finish_params import FinishParams
from core.pipeline.intent_builder import run_build_intent
from core.pipeline.market_context import run_market_context
from core.pipeline.pipeline_authority import PipelineAuthority
from core.pipeline.trace_record import TraceCollector
from core.pipeline.scoring_engine import calculate_confluence, run_scoring_engine, volatility_penalty
from core.pipeline.strategy_detection import run_strategy_detection
from core.pipeline.structure_analysis import run_structure_analysis
from core.state.snapshot import StateSnapshot
from core.state.delta import StateDelta, apply_delta
from core.features.engine import compute_features
from core.stability.stability_gate import evaluate_stability_policy
from core.stability.cohort_key import build_cohort_key
from core.stability.policy_registry import POLICY_REGISTRY

_logger = logging.getLogger(__name__)
from core.pipeline.trade_quality import run_trade_quality_after_confirmation, run_trade_quality_after_scoring
from core.pipeline_types import (
    BarEvaluationContext,
    ConfirmationResult,
    ContextResult,
    PatternResult,
    QualityResult,
    ScoreResult,
    StructureResult,
    UnifiedDecision,
)
from data.mt5_data import Candle
from risk.manager import RiskManager

__all__ = [
    "EngineState",
    "process_bar",
    "calculate_confluence",
    "volatility_penalty",
]


def trace(trace_log: list[str], message: str) -> None:
    trace_log.append(message)


def process_bar(
    *,
    candles: list[Candle],
    closed_i: int,
    symbol: str,
    config,
    risk: RiskManager,
    state: EngineState,
    bid: float,
    ask: float,
    now_s: float | None = None,
    htf_context=None,
) -> UnifiedDecision:
    """
    Collect subsystem facts; terminate early by delegating FinishParams proposals
    to `DecisionEngine.finalize`. No trading rules live here beyond call order parity.
    """
    current_time_s = now_s if now_s is not None else float(candles[closed_i].time)
    record_cycle()

    # ─── PIPELINE AUTHORITY (centralised decision ledger) ─────────────
    authority = PipelineAuthority(symbol=symbol)
    _trace = TraceCollector(symbol=symbol, timeframe="M5")

    _logger.debug("[PIPELINE_STAGE] symbol=%s stage=market_context", symbol)

    bar_ev = BarEvaluationContext(
        candles=candles,
        closed_i=closed_i,
        symbol=symbol,
        bid=bid,
        ask=ask,
        current_time_s=current_time_s,
        config_module=config,
    )
    layer_context = ContextResult()
    layer_pattern = PatternResult()
    layer_confirmation = ConfirmationResult()
    layer_structure = StructureResult()
    layer_score = ScoreResult()
    layer_quality = QualityResult()

    engine = DecisionEngine()

    mc_veto = run_market_context(
        candles=candles,
        closed_i=closed_i,
        state=state,
        config=config,
        current_time_s=current_time_s,
        chop_filter_enabled_fallback=CHOP_FILTER_ENABLED,
        layer_context=layer_context,
        symbol=symbol,
    )
    if mc_veto is not None:
        authority.reject("market_context", mc_veto, {"filter": mc_veto})
        _trace.trace("market_context", "REJECT", mc_veto)
        return engine.finalize(
            bar_context=bar_ev,
            last_completed_stage="market_context",
            ctx=layer_context,
            pattern=layer_pattern,
            confirmation=layer_confirmation,
            structure=layer_structure,
            score=layer_score,
            quality=layer_quality,
            params=engine.market_environment_halt(mc_veto),
        )

    _logger.debug("[PIPELINE_STAGE] symbol=%s stage=strategy_detection", symbol)
    detection = run_strategy_detection(
        candles=candles,
        closed_i=closed_i,
        config=config,
        state=state,
        layer_pattern=layer_pattern,
        symbol=symbol,
    )

    def regime_state_fn() -> str:
        return state.regime_state

    # ─── Pre-state capture for BIAS_CHANGE detection ──────────────────
    _pre_bias_phase = state.bias_phase
    _pre_bias_direction = state.current_bias.value if state.current_bias else None
    # ─── END pre-state ────────────────────────────────────────────────

    _logger.debug("[PIPELINE_STAGE] symbol=%s stage=structure_analysis", symbol)
    struct_step = run_structure_analysis(
        candles=candles,
        closed_i=closed_i,
        current_time_s=current_time_s,
        state=state,
        detection=detection,
        layer_structure=layer_structure,
        regime_state_for_finish=regime_state_fn,
        symbol=symbol,
    )

    # ─── UNIFIED EVENT STREAM: BIAS_CHANGE (Layer 4) ──────────────────
    # Emit only on meaningful state transitions (phase change or bias flip).
    # Includes triggering signals for reasoning chain reconstruction.
    _post_bias_phase = state.bias_phase
    _post_bias = state.current_bias.value if state.current_bias else None
    if _post_bias_phase != _pre_bias_phase or _post_bias != _pre_bias_direction:
        try:
            from core.event_stream import emit_bias_change
            # Determine transition reason from FSM logic
            _transition_reason = "unknown"
            if _pre_bias_phase == "EXPIRED" and _post_bias_phase == "BUILDING":
                _transition_reason = "new_bias_detected"
            elif _pre_bias_phase == "BUILDING" and _post_bias_phase == "CONFIRMED":
                _transition_reason = "confirmation_threshold_met"
            elif _pre_bias_phase == "CONFIRMED" and _post_bias_phase == "EXPIRED":
                _transition_reason = "bias_invalidated"
            elif _post_bias_phase == "EXPIRED" and _pre_bias_phase == "BUILDING":
                _transition_reason = "building_failed"
            elif _post_bias != _pre_bias_direction and _post_bias_phase == _pre_bias_phase:
                _transition_reason = "direction_flip"

            emit_bias_change(symbol, {
                "previous_bias": _pre_bias_direction,
                "new_bias": _post_bias,
                "previous_phase": _pre_bias_phase,
                "new_phase": _post_bias_phase,
                "reason": _transition_reason,
                "triggering_signals": detection.pattern_names[:5] if detection.pattern_names else [],
                "triggering_raw_bias": detection.raw_bias_from_setup.value if detection.raw_bias_from_setup else None,
                "bias_strength": state.bias_strength,
                "bias_age_seconds": state.bias_age_seconds,
                "bias_confirmation_count": state.bias_confirmation_count,
                "bias_contradiction_count": state.bias_contradiction_count,
                "structure_ok": layer_structure.structure_ok if layer_structure.evaluated else None,
                "bias_validation_score": layer_structure.bias_validation_score if layer_structure.evaluated else None,
                "stability_score": layer_structure.stability_score if hasattr(layer_structure, 'stability_score') else None,
            })
        except Exception:
            pass  # Event emission must never affect pipeline
    # ─── END UNIFIED EVENT STREAM ─────────────────────────────────────

    if struct_step.halt is not None:
        authority.reject("structure_analysis", "structure_halt", {"halt_reason": str(struct_step.halt.reason) if hasattr(struct_step.halt, 'reason') else "unknown"})
        _trace.trace("structure_analysis", "REJECT", "structure_halt")
        return engine.finalize(
            bar_context=bar_ev,
            last_completed_stage="structure_analysis",
            ctx=layer_context,
            pattern=layer_pattern,
            confirmation=layer_confirmation,
            structure=layer_structure,
            score=layer_score,
            quality=layer_quality,
            params=struct_step.halt,
        )

    assert struct_step.continuity is not None
    cont = struct_step.continuity

    # ─── STRUCTURE COHESION SCORING (parallel to FSM) ─────────────────
    try:
        from core.pipeline.structure_scoring import update_structure_state
        state.structure_score, state.structure_regime = update_structure_state(
            buffer=state.structure_buffer,
            candles=candles,
            closed_i=closed_i,
            bias_direction=state.current_bias,
        )
    except Exception:
        pass  # Structure scoring failure must never affect pipeline
    # ─── END STRUCTURE COHESION ───────────────────────────────────────

    # ─── PHASE B: FEATURE ENGINEERING + SNAPSHOT FREEZE ──────────────
    # Compute pure market features from candles + tick data (no FSM input).
    _features = compute_features(candles=candles, closed_i=closed_i, bid=bid, ask=ask, symbol=symbol)
    # Create immutable snapshot combining FSM state + market features.
    _state_snapshot = StateSnapshot.from_state_and_features(state, _features)
    # ─── END PHASE B ──────────────────────────────────────────────────

    # ─── SHADOW VOTER PIPELINE (observational — no decision impact) ──
    # Runs full voter → confluence → gate pipeline in shadow mode.
    # Emits structured calibration log. NEVER affects execution.
    _shadow_bias_vote = None
    _shadow_structure_vote = None
    _shadow_session_vote = None
    _shadow_spread_vote = None
    _shadow_volatility_vote = None
    _shadow_confluence = None
    _shadow_gate = None
    try:
        from core.voters.bias_voter import ShadowBiasVoter
        from core.voters.structure_voter import ShadowStructureVoter
        from core.voters.session_voter import SessionVoter
        from core.voters.spread_voter import SpreadVoter
        from core.voters.volatility_voter import VolatilityVoter
        from core.voters.confluence_engine import compute_confluence as _shadow_compute
        from core.voters.execution_gate import evaluate_execution_gate as _shadow_gate_fn

        _shadow_bias_vote = ShadowBiasVoter().evaluate(_state_snapshot)
        _shadow_structure_vote = ShadowStructureVoter().evaluate(_state_snapshot)
        _shadow_session_vote = SessionVoter().evaluate(_state_snapshot)
        _shadow_spread_vote = SpreadVoter().evaluate(_state_snapshot)
        _shadow_volatility_vote = VolatilityVoter().evaluate(_state_snapshot)

        _shadow_confluence = _shadow_compute(
            bias_vote=_shadow_bias_vote,
            structure_vote=_shadow_structure_vote,
            session_vote=_shadow_session_vote,
            spread_vote=_shadow_spread_vote,
            volatility_vote=_shadow_volatility_vote,
            structure_score=_state_snapshot.structure_score,
            structure_regime=_state_snapshot.structure_regime,
        )

        _shadow_gate = _shadow_gate_fn(_shadow_confluence, _state_snapshot)

        # Agreement analysis (observational)
        from core.voters.agreement_analysis import compute_agreement, emit_agreement_log
        _agreement = compute_agreement(
            bias_vote=_shadow_bias_vote,
            structure_vote=_shadow_structure_vote,
            session_vote=_shadow_session_vote,
            spread_vote=_shadow_spread_vote,
            volatility_vote=_shadow_volatility_vote,
            confluence=_shadow_confluence,
        )
        emit_agreement_log(symbol, _agreement)

        # Conflict classification (observational)
        from core.voters.conflict_classification import classify_conflicts, emit_conflict_log
        _conflicts = classify_conflicts(
            bias_vote=_shadow_bias_vote,
            structure_vote=_shadow_structure_vote,
            session_vote=_shadow_session_vote,
            spread_vote=_shadow_spread_vote,
            volatility_vote=_shadow_volatility_vote,
            confluence=_shadow_confluence,
        )
        emit_conflict_log(symbol, _conflicts)

        # Voter influence tracking (observational)
        from core.voters.influence_tracker import (
            compute_influence, voter_reliability_tracker, emit_influence_log,
        )
        _influence = compute_influence(
            bias_vote=_shadow_bias_vote,
            structure_vote=_shadow_structure_vote,
            session_vote=_shadow_session_vote,
            spread_vote=_shadow_spread_vote,
            volatility_vote=_shadow_volatility_vote,
            confluence=_shadow_confluence,
        )
        voter_reliability_tracker.record(
            bias_vote=_shadow_bias_vote,
            structure_vote=_shadow_structure_vote,
            session_vote=_shadow_session_vote,
            spread_vote=_shadow_spread_vote,
            volatility_vote=_shadow_volatility_vote,
            confluence=_shadow_confluence,
        )
        _reliability = voter_reliability_tracker.get_snapshot()
        emit_influence_log(symbol, _influence, _reliability)

        # System synthesis (observational)
        from core.voters.system_synthesis import compute_synthesis, emit_synthesis_log
        _synthesis = compute_synthesis(
            agreement=_agreement,
            conflict=_conflicts,
            influence=_influence,
            reliability=_reliability,
        )
        emit_synthesis_log(symbol, _synthesis)

        # Weight intelligence (observational — Phase 3.5)
        from core.voters.weight_intelligence import compute_weight_intelligence, emit_weight_intelligence_log
        _weight_intel = compute_weight_intelligence(
            influence=_influence,
            reliability=_reliability,
            conflict=_conflicts,
            agreement=_agreement,
        )
        emit_weight_intelligence_log(symbol, _weight_intel)
    except Exception:
        pass  # Shadow pipeline failure must never affect execution
    # ─── END SHADOW VOTER PIPELINE ────────────────────────────────────

    _logger.debug("[PIPELINE_STAGE] symbol=%s stage=confirmation", symbol)
    confirmed, cf_reason = run_confirmations(
        signal=cont.signal,
        candles=candles,
        layer_confirmation=layer_confirmation,
        symbol=symbol,
    )
    if not confirmed:
        authority.reject("confirmations", f"failed_confirmation:{cf_reason}", {"reason": cf_reason})
        _trace.trace("confirmations", "REJECT", cf_reason)
        return engine.finalize(
            bar_context=bar_ev,
            last_completed_stage="confirmations",
            ctx=layer_context,
            pattern=layer_pattern,
            confirmation=layer_confirmation,
            structure=layer_structure,
            score=layer_score,
            quality=layer_quality,
            params=FinishParams(
                should_trade=False,
                reason=f"failed_confirmation:{cf_reason}",
                signal=cont.signal,
                intent=None,
                bias=cont.evaluation_bias,
                patterns=detection.pattern_names,
                score=0,
                bias_phase=state.bias_phase,
                bias_validation_score=cont.bias_validation_score,
                structure_ok=cont.structure_ok,
                bias_strength=state.bias_strength,
                bias_age_seconds=state.bias_age_seconds,
                bias_window_phase=detection.bias_window_phase,
                confluence_threshold_dynamic=detection.confluence_threshold_dynamic,
                regime_state=state.regime_state,
            ),
        )

    halt_tqf, trend_aligned = run_trade_quality_after_confirmation(
        candles=candles,
        closed_i=closed_i,
        config=config,
        snapshot=_state_snapshot,
        signal=cont.signal,
        evaluation_bias=cont.evaluation_bias,
        pattern_names=detection.pattern_names,
        bias_validation_score=cont.bias_validation_score,
        structure_ok=cont.structure_ok,
        bias_window_phase=detection.bias_window_phase,
        confluence_threshold_dynamic=detection.confluence_threshold_dynamic,
        layer_quality=layer_quality,
        chop_filter_enabled_fallback=CHOP_FILTER_ENABLED,
        trend_filter_enabled_fallback=TREND_FILTER_ENABLED,
        regime_state=state.regime_state,
    )
    if halt_tqf is not None:
        authority.reject("trade_quality_pre", "quality_filter_pre_scoring", {"halt": True})
        _trace.trace("trade_quality_pre", "REJECT", "quality_filter_pre_scoring")
        return engine.finalize(
            bar_context=bar_ev,
            last_completed_stage="trade_quality",
            ctx=layer_context,
            pattern=layer_pattern,
            confirmation=layer_confirmation,
            structure=layer_structure,
            score=layer_score,
            quality=layer_quality,
            params=halt_tqf,
        )

    _logger.debug("[PIPELINE_STAGE] symbol=%s stage=scoring", symbol)

    # ─── MTF: Apply higher-timeframe constraints (if enabled) ─────────
    _htf_score_adj = 0.0
    _htf_min_score_adj = 0.0
    if htf_context is not None:
        try:
            from core.timeframes.types import HTFContext as _HTFCtx
            if isinstance(htf_context, _HTFCtx) and htf_context.is_populated:
                from core.timeframes.integration import apply_htf_constraints
                _htf_influence = apply_htf_constraints(
                    htf_context=htf_context,
                    signal_side=cont.signal.side,
                    evaluation_bias=cont.evaluation_bias,
                    config=config,
                )
                if _htf_influence.is_blocking:
                    _logger.info(
                        "[MTF_BLOCK] symbol=%s reason=%s",
                        symbol, _htf_influence.block_reason,
                    )
                    authority.reject("htf_constraint", f"htf_block:{_htf_influence.block_reason}", {"block_reason": _htf_influence.block_reason})
                    _trace.trace("htf_constraint", "REJECT", _htf_influence.block_reason)
                    # ─── RISK_CHECK: HTF constraint block ─────────────
                    try:
                        from core.event_stream import emit_risk_check
                        emit_risk_check(symbol, {
                            "result": "REJECTED",
                            "guard": "htf_constraint",
                            "reason": _htf_influence.block_reason,
                            "pattern": cont.signal.pattern,
                            "layer": "HTF",
                            "score_adjustment": _htf_influence.score_adjustment,
                            "min_score_adjustment": _htf_influence.min_score_adjustment,
                            "breakdown": getattr(_htf_influence, "breakdown", None),
                        }, source="htf_integration")
                    except Exception:
                        pass
                    # ─── END RISK_CHECK ────────────────────────────────
                    return engine.finalize(
                        bar_context=bar_ev,
                        last_completed_stage="htf_constraint",
                        ctx=layer_context,
                        pattern=layer_pattern,
                        confirmation=layer_confirmation,
                        structure=layer_structure,
                        score=layer_score,
                        quality=layer_quality,
                        params=FinishParams(
                            should_trade=False,
                            reason=f"htf_block:{_htf_influence.block_reason}",
                            signal=cont.signal,
                            intent=None,
                            bias=cont.evaluation_bias,
                            patterns=detection.pattern_names,
                            score=0,
                            bias_phase=state.bias_phase,
                            bias_validation_score=cont.bias_validation_score,
                            structure_ok=cont.structure_ok,
                            bias_strength=state.bias_strength,
                            bias_age_seconds=state.bias_age_seconds,
                            bias_window_phase=detection.bias_window_phase,
                            confluence_threshold_dynamic=detection.confluence_threshold_dynamic,
                            regime_state=state.regime_state,
                        ),
                    )
                _htf_score_adj = _htf_influence.score_adjustment
                _htf_min_score_adj = _htf_influence.min_score_adjustment
                if _htf_influence.has_influence:
                    _logger.debug(
                        "[MTF_SCORE] symbol=%s score_adj=%.2f min_score_adj=%.2f breakdown=%s",
                        symbol, _htf_score_adj, _htf_min_score_adj, _htf_influence.breakdown,
                    )
        except Exception:
            pass  # Graceful degradation — proceed without HTF influence
    # ─── END MTF ──────────────────────────────────────────────────────

    halt_score = run_scoring_engine(
        signal=cont.signal,
        evaluation_bias=cont.evaluation_bias,
        trend_aligned=trend_aligned,
        candles=candles,
        closed_i=closed_i,
        snapshot=_state_snapshot,
        config=config,
        stability_score=cont.stability_score,
        bias_window_phase=detection.bias_window_phase,
        confluence_threshold_dynamic=detection.confluence_threshold_dynamic,
        pattern_names=detection.pattern_names,
        bias_validation_score=cont.bias_validation_score,
        structure_ok=cont.structure_ok,
        layer_score=layer_score,
        regime_state=state.regime_state,
        htf_score_adjustment=_htf_score_adj,
        htf_min_score_adjustment=_htf_min_score_adj,
        confirmation_strength=layer_confirmation.strength or "STRONG",
        symbol=symbol,
    )
    # Collect volatility_filter into delta (deferred from scoring_engine)
    _delta = StateDelta()
    if layer_score.evaluated:
        _delta.volatility_filter = layer_score.volatility_penalty
    if halt_score is not None:
        apply_delta(state, _delta)
        authority.reject("scoring_engine", "confluence_below_threshold", {"score": layer_score.final_score if layer_score.evaluated else 0})
        _trace.trace("scoring_engine", "REJECT", "confluence_below_threshold")
        return engine.finalize(
            bar_context=bar_ev,
            last_completed_stage="scoring_engine",
            ctx=layer_context,
            pattern=layer_pattern,
            confirmation=layer_confirmation,
            structure=layer_structure,
            score=layer_score,
            quality=layer_quality,
            params=halt_score,
        )

    halt_tqp = run_trade_quality_after_scoring(
        symbol=symbol,
        config=config,
        current_time_s=current_time_s,
        snapshot=_state_snapshot,
        signal=cont.signal,
        evaluation_bias=cont.evaluation_bias,
        pattern_names=detection.pattern_names,
        bias_validation_score=cont.bias_validation_score,
        structure_ok=cont.structure_ok,
        bias_window_phase=detection.bias_window_phase,
        confluence_threshold_dynamic=detection.confluence_threshold_dynamic,
        score_int=layer_score.score_int,
        breakdown=layer_score.breakdown,
        can_trade_bias=cont.can_trade_bias,
        layer_quality=layer_quality,
        regime_state=state.regime_state,
    )
    if halt_tqp is not None:
        apply_delta(state, _delta)
        authority.reject("trade_quality_post", "quality_filter_post_scoring", {"score": layer_score.score_int})
        _trace.trace("trade_quality_post", "REJECT", "quality_filter_post_scoring")
        return engine.finalize(
            bar_context=bar_ev,
            last_completed_stage="trade_quality",
            ctx=layer_context,
            pattern=layer_pattern,
            confirmation=layer_confirmation,
            structure=layer_structure,
            score=layer_score,
            quality=layer_quality,
            params=halt_tqp,
        )

    _logger.debug("[PIPELINE_STAGE] symbol=%s stage=risk_intent", symbol)

    # ─── STABILITY GATE (final admission check before intent) ─────────
    _cohort_key = build_cohort_key(layer_confirmation)
    _stability_decision = evaluate_stability_policy(
        snapshot=_state_snapshot,
        policy_registry=POLICY_REGISTRY,
    )
    if not _stability_decision.allow_trade:
        apply_delta(state, _delta)
        authority.reject("stability_gate", f"stability_block:{_stability_decision.reason}", {"mode": _stability_decision.mode, "cohort_key": _cohort_key})
        _trace.trace("stability_gate", "REJECT", _stability_decision.reason)
        return engine.finalize(
            bar_context=bar_ev,
            last_completed_stage="stability_gate",
            ctx=layer_context,
            pattern=layer_pattern,
            confirmation=layer_confirmation,
            structure=layer_structure,
            score=layer_score,
            quality=layer_quality,
            params=FinishParams(
                should_trade=False,
                reason=f"stability_block:{_stability_decision.reason}",
                signal=cont.signal,
                intent=None,
                bias=cont.evaluation_bias,
                patterns=detection.pattern_names,
                score=layer_score.score_int if layer_score.evaluated else 0,
                bias_phase=state.bias_phase,
                bias_validation_score=cont.bias_validation_score,
                structure_ok=cont.structure_ok,
                bias_strength=state.bias_strength,
                bias_age_seconds=state.bias_age_seconds,
                bias_window_phase=detection.bias_window_phase,
                confluence_threshold_dynamic=detection.confluence_threshold_dynamic,
                regime_state=state.regime_state,
            ),
        )
    # ─── END STABILITY GATE ───────────────────────────────────────────

    params_final = run_build_intent(
        risk=risk,
        symbol=symbol,
        signal=cont.signal,
        candles=candles,
        closed_i=closed_i,
        bid=bid,
        ask=ask,
        current_time_s=current_time_s,
        snapshot=_state_snapshot,
        delta=_delta,
        evaluation_bias=cont.evaluation_bias,
        pattern_names=detection.pattern_names,
        score_int=layer_score.score_int,
        bias_validation_score=cont.bias_validation_score,
        structure_ok=cont.structure_ok,
        bias_window_phase=detection.bias_window_phase,
        confluence_threshold_dynamic=detection.confluence_threshold_dynamic,
        breakdown=layer_score.breakdown,
        regime_state=state.regime_state,
        layer_quality=layer_quality,
        confirmation_strength=layer_confirmation.strength or None,
        confirmation_body_pct=layer_confirmation.body_pct or None,
        confirmation_wick_ratio=layer_confirmation.wick_ratio or None,
        confirmation_close_location=layer_confirmation.close_location or None,
    )
    apply_delta(state, _delta)

    # ─── PIPELINE AUTHORITY: Record final decision ────────────────────
    if params_final.should_trade:
        authority.allow("complete", {"score": layer_score.score_int, "signal": str(cont.signal.side.value) if cont.signal else None})
        _trace.trace("complete", "ALLOW", "trade_approved")
    else:
        authority.reject("intent_builder", params_final.reason or "risk_reject", {"score": layer_score.score_int})
        _trace.trace("intent_builder", "REJECT", params_final.reason or "risk_reject")
    # ─── END PIPELINE AUTHORITY ───────────────────────────────────────

    # ─── SHADOW CALIBRATION LOG (after production decision known) ─────
    if _shadow_confluence is not None and _shadow_gate is not None and _shadow_bias_vote is not None:
        try:
            from core.voters.shadow_calibration import emit_shadow_calibration
            _prod_action = "BUY" if params_final.should_trade and cont.evaluation_bias and cont.evaluation_bias.value == "BUY" else \
                           "SELL" if params_final.should_trade and cont.evaluation_bias and cont.evaluation_bias.value == "SELL" else \
                           "NO_TRADE"
            emit_shadow_calibration(
                symbol=symbol,
                bias_vote=_shadow_bias_vote,
                structure_vote=_shadow_structure_vote,
                session_vote=_shadow_session_vote,
                confluence=_shadow_confluence,
                gate=_shadow_gate,
                production_action=_prod_action,
            )

            # A/B Test comparison (Phase 4)
            from core.voters.ab_testing import compute_ab_test, emit_ab_test_log
            _shadow_final_action = _shadow_confluence.action if _shadow_gate.allowed else "NO_TRADE"
            _ab_result = compute_ab_test(
                production_action=_prod_action,
                shadow_action=_shadow_final_action,
            )
            emit_ab_test_log(symbol, _ab_result)
        except Exception:
            pass
    # ─── END SHADOW CALIBRATION ───────────────────────────────────────

    # ─── STABILITY POLICY ATTACHMENT (post-decision metadata only) ────
    _final_cohort_key = build_cohort_key(layer_confirmation)
    _final_policy = POLICY_REGISTRY.get(_final_cohort_key, "NORMAL_MODE")
    # ─── END STABILITY POLICY ATTACHMENT ──────────────────────────────

    _unified = engine.finalize(
        bar_context=bar_ev,
        last_completed_stage="complete" if params_final.should_trade else "build_intent",
        ctx=layer_context,
        pattern=layer_pattern,
        confirmation=layer_confirmation,
        structure=layer_structure,
        score=layer_score,
        quality=layer_quality,
        params=params_final,
    )
    _unified.stability_policy = _final_policy
    return _unified
