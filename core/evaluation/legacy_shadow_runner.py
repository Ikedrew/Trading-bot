"""
Legacy Shadow Runner — Runs old pipeline in shadow mode for comparison.

Executes the legacy pipeline (process_bar) as a shadow when
ENABLE_LEGACY_SHADOW_PIPELINE is True. Compares legacy output against
Engine A output. Never affects production execution.

This module OWNS:
    - Legacy engine execution (process_bar)
    - Shadow mode dual-pipeline comparison
    - MTF shadow divergence + calibration
    - Legacy result comparison with new engine

This module does NOT own:
    - Production decisions
    - Execution
    - Risk management
    - Engine A logic
    - Runtime loop

Design: fire-and-forget shadow — never raises, never affects production.
"""

from __future__ import annotations

import copy
import logging
from typing import Any

logger = logging.getLogger(__name__)


def run_legacy_shadow(
    *,
    candles: Any,
    closed_i: int,
    symbol: str,
    config: Any,
    risk: Any,
    engine_state: Any,
    bid: float,
    ask: float,
    closed_time: int,
    htf_context: Any,
    new_engine_score: float,
) -> Any:
    """
    Run legacy pipeline in shadow mode and compare with Engine A.

    Only called when ENABLE_LEGACY_SHADOW_PIPELINE=True.
    Returns the unified result (for shadow comparison downstream) or None.

    Never raises. Never affects production execution.
    """
    try:
        from core.engine import process_bar

        # Shadow mode: determine if MTF dual comparison should run
        _shadow_mode = getattr(config, "MTF_SHADOW_MODE", False) and htf_context is not None
        _baseline_unified = None

        # Use a COPY of state (read-only — never contaminate production state)
        _old_pipeline_state = copy.deepcopy(engine_state)

        if _shadow_mode:
            # Run baseline (M5-only, no HTF influence)
            try:
                _baseline_unified = process_bar(
                    candles=candles,
                    closed_i=closed_i,
                    symbol=symbol,
                    config=config,
                    risk=risk,
                    state=_old_pipeline_state,
                    bid=bid,
                    ask=ask,
                    now_s=float(closed_time),
                    htf_context=None,
                )
            except Exception:
                _shadow_mode = False

        if _shadow_mode:
            # Run MTF pipeline on a COPY to prevent contamination
            _shadow_state = copy.copy(_old_pipeline_state)
            _shadow_state.bias_flip_bars = copy.copy(_old_pipeline_state.bias_flip_bars)
            _shadow_state.last_failed_setups = copy.copy(_old_pipeline_state.last_failed_setups)
            try:
                unified = process_bar(
                    candles=candles,
                    closed_i=closed_i,
                    symbol=symbol,
                    config=config,
                    risk=risk,
                    state=_shadow_state,
                    bid=bid,
                    ask=ask,
                    now_s=float(closed_time),
                    htf_context=htf_context,
                )
            except Exception:
                unified = _baseline_unified
        else:
            # Normal mode: single pipeline run
            try:
                unified = process_bar(
                    candles=candles,
                    closed_i=closed_i,
                    symbol=symbol,
                    config=config,
                    risk=risk,
                    state=_old_pipeline_state,
                    bid=bid,
                    ask=ask,
                    now_s=float(closed_time),
                    htf_context=htf_context,
                )
            except Exception:
                unified = None

        # Shadow mode divergence comparison
        if _shadow_mode and _baseline_unified is not None and unified is not None:
            _compare_shadow_divergence(symbol, _baseline_unified, unified, htf_context)
            # In shadow mode: use baseline for any downstream comparison
            unified = _baseline_unified

        return unified

    except Exception:
        return None  # Legacy shadow failure must never affect production


def _compare_shadow_divergence(symbol: str, baseline: Any, mtf_result: Any, htf_context: Any) -> None:
    """Compare baseline vs MTF pipeline decisions and log divergence."""
    try:
        _bl_dec = baseline.decision
        _mtf_dec = mtf_result.decision
        _bl_action = "TRADE" if _bl_dec.should_trade else "NO_TRADE"
        _mtf_action = "TRADE" if _mtf_dec.should_trade else "NO_TRADE"
        _block_reason = _mtf_dec.reason if "htf_block" in _mtf_dec.reason else ""
        _score_delta = int(_mtf_dec.score) - int(_bl_dec.score)

        logger.info(
            "[SHADOW_BASELINE] symbol=%s decision=%s score=%d reason=%s",
            symbol, _bl_action, int(_bl_dec.score), _bl_dec.reason[:50],
        )
        logger.info(
            "[SHADOW_MTF] symbol=%s decision=%s score=%d delta=%d block=%s",
            symbol, _mtf_action, int(_mtf_dec.score), _score_delta, _block_reason or "none",
        )

        if _bl_dec.should_trade != _mtf_dec.should_trade:
            logger.info(
                "[SHADOW_DIVERGENCE] symbol=%s baseline=%s mtf=%s reason=%s",
                symbol, _bl_action, _mtf_action, _block_reason or _mtf_dec.reason[:60],
            )

        # Calibration metrics
        try:
            from core.timeframes.calibration import mtf_calibration
            _h4_regime = ""
            _h1_bias = ""
            _m15_quality = 0.0
            if htf_context is not None:
                if htf_context.regime is not None:
                    _h4_regime = htf_context.regime.classification.value
                if htf_context.bias is not None:
                    _h1_bias = htf_context.bias.direction.value
                if htf_context.structure is not None:
                    _m15_quality = htf_context.structure.quality_score
            mtf_calibration.record(
                symbol=symbol,
                baseline_should_trade=_bl_dec.should_trade,
                baseline_score=int(_bl_dec.score),
                baseline_reason=_bl_dec.reason[:60],
                mtf_should_trade=_mtf_dec.should_trade,
                mtf_score=int(_mtf_dec.score),
                mtf_reason=_mtf_dec.reason[:60],
                htf_blocked=bool(_block_reason),
                block_reason=_block_reason,
                h4_regime=_h4_regime,
                h1_bias=_h1_bias,
                m15_quality=_m15_quality,
            )
        except Exception:
            pass
    except Exception:
        pass
