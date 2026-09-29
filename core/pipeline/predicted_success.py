"""AUTHORITATIVE p_success PRODUCER (Stage 4 observation requirement OR-08).

WHY THIS MODULE EXISTS
----------------------
``decision_trace_v1`` has carried a ``p_success`` key since it was declared, but
the value has been NULL on 100% of persisted rows.  The root cause is a
PRODUCER OMISSION, not an upstream data gap:

  * ``core/decision_trace.py`` reads ``p_success`` from the legacy
    ``engine_result`` dict, which only the legacy ``new_engine`` path populates
    (it calls :class:`ProbabilityEstimator` directly).
  * The V10 pipeline (``core/v10/pipeline.py``) never runs the probability
    estimator at all, so every V10 decision trace carried NULL.

The authoritative owner of the VALUE is
:class:`core.pipeline.probability_estimator.ProbabilityEstimator`.  This module
does not invent a second estimator and does not reimplement the model.  It
supplies the ONE missing link: a governed adapter that feeds the V10
pre-outcome decision context into the existing authoritative estimator and
records full model/version lineage.

HARD RULES (fail closed)
------------------------
  1. PRE-OUTCOME ONLY.  The estimate is computed from decision-instant context
     (opportunity quality, market state).  It never reads a realised outcome and
     is never computed retroactively.
  2. NEVER DEFAULT.  A missing or unusable input yields ``state=UNAVAILABLE``
     with ``p_success=None`` and a named reason.  It is never 0.0, never 0.5,
     and never silently omitted.
  3. NO HISTORICAL INFERENCE.  Rows already persisted keep their NULL.
  4. RANGE VALIDATION.  A computed value outside the governed range is a model
     fault reported as such, never clamped after the fact.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from core.pipeline.market_state_engine import MarketState

#: Governed identity of this block.  Deliberately NOT named ``schema_version``:
#: ``schema_version`` carries the RECORD-STRUCTURE identity of the dataset and
#: must never be overloaded as an observable value.
PREDICTED_SUCCESS_BLOCK_VERSION = "predicted_success_block_v1"

# ═══════════════════════════════════════════════════════════════════════════
# DECISION_TRACE EMISSION IDENTITY (governed, fail closed)
#
# ``decision_trace`` is dataset generation 2 from producer
# ``decision_trace_producer_v2``: generation 1 predates the OR-08
# ``predicted_success`` block and is never emitted again.  These constants are
# restated here (not only in the control-plane registry) so the startup guard
# in ``core.observability_contract`` can compare producer against contract and
# raise rather than let the two drift apart.
# ═══════════════════════════════════════════════════════════════════════════

DECISION_TRACE_DATASET = "decision_trace"
DECISION_TRACE_DATASET_VERSION = "decision_trace_v1"
DECISION_TRACE_SCHEMA_GENERATION = 2
DECISION_TRACE_PRODUCER_VERSION = "decision_trace_producer_v2"
PREVIOUS_DECISION_TRACE_SCHEMA_GENERATION = 1
PREVIOUS_DECISION_TRACE_PRODUCER_VERSION = "decision_trace_producer_v1"

#: The generation-2 field set this producer is governed to emit.
DECISION_TRACE_GEN2_FIELDS: tuple[str, ...] = ("predicted_success",)

#: The model that owns the value, so recorded lineage can never drift from the
#: model actually used.
ESTIMATOR_OWNER = "core.pipeline.probability_estimator.ProbabilityEstimator"

#: The governed outcome label a predicted success probability refers to.
OUTCOME_LABEL = "SIMULATED_TRADE_REACHED_TAKE_PROFIT"

#: Inclusive bounds used to VALIDATE a computed value, never to repair one.
P_SUCCESS_MIN = 0.0
P_SUCCESS_MAX = 1.0

#: Historical reconstruction is NOT supported for the governed population.
#:
#: Reconstructing ``p_success`` needs the exact estimator inputs AND the
#: calibration curve live at the time.  The governed decision_trace rows
#: persisted ``score_neutral`` and ``v10_opportunity`` but persisted
#: ``confirmation_score`` and ``market_state`` as NULL, and the calibration
#: artifact is a mutable file rather than a pinned per-row version.  The inputs
#: are therefore NOT available deterministically, so any historical value would
#: be an inference.  It is refused explicitly rather than approximated.
HISTORICAL_BACKFILL_SUPPORTED = False
HISTORICAL_BACKFILL_REFUSAL = (
    "UNAVAILABLE: authoritative p_success cannot be reconstructed for the "
    "governed historical population because required model inputs "
    "(confirmation_score, market_state) were not persisted and the calibration "
    "curve was not pinned per row. No value is inferred."
)

# ─── Missing-state vocabulary (explicit, never a default) ────────────────────

STATE_PREDICTED = "PREDICTED"
STATE_UNAVAILABLE = "UNAVAILABLE"

REASON_NO_OPPORTUNITY_QUALITY = "OPPORTUNITY_QUALITY_ABSENT_AT_DECISION_TIME"
REASON_NO_MARKET_STATE = "MARKET_STATE_ABSENT_AT_DECISION_TIME"
REASON_ESTIMATOR_FAILED = "PROBABILITY_ESTIMATOR_RAISED"
REASON_OUT_OF_RANGE = "COMPUTED_VALUE_OUTSIDE_GOVERNED_RANGE"

#: Explicit, closed V10 regime -> estimator market-state mapping.
#:
#: The estimator dampens uncertainty by market state.  The V10 pipeline exposes
#: a REGIME vocabulary (TRENDING / RANGING / TRANSITIONAL / VOLATILE) rather
#: than the estimator's (STRUCTURED / TRANSITIONAL / CHOP).  The mapping is
#: declared, versioned, and recorded on every emitted block so a consumer never
#: has to guess.  It is a declared translation, NOT a new model.
MARKET_STATE_MAPPING_VERSION = "v10_regime_to_estimator_state_v1"
V10_REGIME_TO_ESTIMATOR_STATE: dict[str, str] = {
    "TRENDING": MarketState.STRUCTURED.value,
    "RANGING": MarketState.CHOP.value,
    "TRANSITIONAL": MarketState.TRANSITIONAL.value,
    "VOLATILE": MarketState.CHOP.value,
}

#: Applied when the regime is present but outside the closed mapping.
UNMAPPED_REGIME_STATE = MarketState.TRANSITIONAL.value


def _digest(payload: Any) -> str:
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, default=str,
    ).encode("utf-8")).hexdigest()


def _unavailable(reason: str, **context: Any) -> dict[str, Any]:
    """An honest 'not predicted' block.  ``p_success`` stays None."""
    block: dict[str, Any] = {
        "block_version": PREDICTED_SUCCESS_BLOCK_VERSION,
        "p_success": None,
        "state": STATE_UNAVAILABLE,
        "unavailable_reason": reason,
        "outcome_label": OUTCOME_LABEL,
        # Pre-outcome attestation: produced at decision time, carrying no
        # knowledge of whether the trade later reached its target.
        "outcome_knowledge_at_prediction": "NONE",
        "predicted_pre_outcome": True,
        "estimator_owner": ESTIMATOR_OWNER,
        "market_state_mapping_version": MARKET_STATE_MAPPING_VERSION,
    }
    block.update(context)
    return block


def map_v10_regime_to_estimator_state(regime: Any) -> tuple[str, bool]:
    """Map a V10 regime to the estimator's market state.

    Returns ``(state, mapped)``.  An unmapped-but-present regime resolves to the
    governed default state and is reported as ``mapped=False`` rather than
    silently treated as a known regime.
    """
    text = str(regime or "").strip().upper()
    if not text:
        return UNMAPPED_REGIME_STATE, False
    resolved = V10_REGIME_TO_ESTIMATOR_STATE.get(text)
    if resolved is None:
        return UNMAPPED_REGIME_STATE, False
    return resolved, True


def predict_success_probability(
    *,
    opportunity_quality: Any,
    market_regime: Any,
    confirmation_score: Any = None,
) -> dict[str, Any]:
    """Produce the authoritative pre-outcome ``p_success`` block.

    ``opportunity_quality`` is the V10 opportunity quality composite (0-1),
    ``market_regime`` the V10 regime token, and ``confirmation_score`` the
    optional candle-quality modifier.  All three are DECISION-INPUTS known at
    the decision instant; no outcome field is read.

    The value itself is computed by the existing authoritative estimator; this
    function only adapts inputs and records lineage.
    """
    quality = _coerce_unit_float(opportunity_quality)
    if quality is None:
        return _unavailable(
            REASON_NO_OPPORTUNITY_QUALITY,
            opportunity_quality=opportunity_quality,
        )

    regime_state, regime_mapped = map_v10_regime_to_estimator_state(market_regime)
    if not str(market_regime or "").strip():
        return _unavailable(
            REASON_NO_MARKET_STATE,
            opportunity_quality=quality,
            market_regime=market_regime,
        )

    confirmation = _coerce_unit_float(confirmation_score)
    inputs: dict[str, Any] = {
        "opportunity_quality": quality,
        "estimator_market_state": regime_state,
        "v10_regime": str(market_regime),
        "v10_regime_mapped": regime_mapped,
        "confirmation_score": confirmation,
    }
    inputs_digest = _digest(inputs)

    try:
        from core.pipeline.probability_estimator import get_probability_estimator
        from core.pipeline.score_calibrator import get_score_calibrator

        estimator = get_probability_estimator()

        class _Assessment:
            """Minimal adapter exposing only what the estimator reads."""

            score_neutral = quality

        class _MarketStateResult:
            state = MarketState(regime_state)

        estimate = estimator.estimate(
            assessment=_Assessment(),
            market_state_result=_MarketStateResult(),
            confirmation_score=(1.0 if confirmation is None else confirmation),
        )
        calibrator = get_score_calibrator()
    except Exception:
        return _unavailable(
            REASON_ESTIMATOR_FAILED,
            inputs=inputs,
            inputs_digest=inputs_digest,
        )

    value = float(getattr(estimate, "p_success", 0.0))
    if not (P_SUCCESS_MIN <= value <= P_SUCCESS_MAX):
        return _unavailable(
            REASON_OUT_OF_RANGE,
            inputs=inputs,
            inputs_digest=inputs_digest,
            computed_value=value,
        )

    return {
        "block_version": PREDICTED_SUCCESS_BLOCK_VERSION,
        "p_success": round(value, 4),
        "state": STATE_PREDICTED,
        "unavailable_reason": None,
        "outcome_label": OUTCOME_LABEL,
        "outcome_knowledge_at_prediction": "NONE",
        "predicted_pre_outcome": True,
        "estimator_owner": ESTIMATOR_OWNER,
        "model_version": str(getattr(estimate, "model_version", "")),
        "calibration_version": str(getattr(estimate, "calibration_version", "")),
        "calibration_source": str(getattr(estimate, "calibration_source", "")),
        "calibrator_active_version": str(getattr(calibrator, "version", "")),
        "raw_score": float(getattr(estimate, "raw_score", 0.0)),
        "calibrated_probability": float(
            getattr(estimate, "calibrated_probability", 0.0)),
        "uncertainty_dampening": float(
            getattr(estimate, "uncertainty_dampening", 0.0)),
        "evidence_used": list(getattr(estimate, "evidence_used", ()) or ()),
        "market_state_mapping_version": MARKET_STATE_MAPPING_VERSION,
        "inputs": inputs,
        "inputs_digest": inputs_digest,
    }


def _coerce_unit_float(value: Any) -> float | None:
    """Coerce to a float in [0, 1]; return None for anything unusable.

    ``None`` means "not available", which is materially different from 0.0 and
    is never coerced to a default.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    if not (0.0 <= number <= 1.0):
        return None
    return number


__all__ = [
    "DECISION_TRACE_DATASET", "DECISION_TRACE_DATASET_VERSION",
    "DECISION_TRACE_GEN2_FIELDS", "DECISION_TRACE_PRODUCER_VERSION",
    "DECISION_TRACE_SCHEMA_GENERATION", "ESTIMATOR_OWNER",
    "HISTORICAL_BACKFILL_REFUSAL", "HISTORICAL_BACKFILL_SUPPORTED",
    "MARKET_STATE_MAPPING_VERSION", "OUTCOME_LABEL",
    "PREDICTED_SUCCESS_BLOCK_VERSION", "P_SUCCESS_MAX",
    "P_SUCCESS_MIN", "PREVIOUS_DECISION_TRACE_PRODUCER_VERSION",
    "PREVIOUS_DECISION_TRACE_SCHEMA_GENERATION", "REASON_ESTIMATOR_FAILED",
    "REASON_NO_MARKET_STATE", "REASON_NO_OPPORTUNITY_QUALITY",
    "REASON_OUT_OF_RANGE", "STATE_PREDICTED", "STATE_UNAVAILABLE",
    "V10_REGIME_TO_ESTIMATOR_STATE", "map_v10_regime_to_estimator_state",
    "predict_success_probability",
]

