"""
DecisionTrace — Structured diagnostic record of one new-engine evaluation.

Produced once per entity_id per cycle. Records the complete decision journey
without making or influencing any trading decisions.

This module is PURELY OBSERVATIONAL. It does NOT:
    - Make trading decisions
    - Modify scores or thresholds
    - Influence execution
    - Gate or block trades
    - Replace existing persistence layers

It ONLY:
    - Records what decision the engine already made
    - Captures the reasoning data available at that point
    - Identifies which stage terminated the pipeline
    - Computes diagnostic metrics (drag, proximity) from existing data

Persistence:
    Local: logs/decision_trace/{SYMBOL}/{YYYY-MM-DD}.jsonl
    S3:    s3://trading-bot-data-mk1/decision_trace/symbol={SYMBOL}/date={YYYY-MM-DD}/part-000.jsonl

Usage:
    from core.decision_trace import build_decision_trace, persist_decision_trace

    trace = build_decision_trace(engine_result=_new_result, runtime_session_id=_session_id)
    persist_decision_trace(trace)
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOCAL_DIR = "logs/decision_trace"
_S3_BUCKET = "trading-bot-data-mk1"
_S3_PREFIX = "decision_trace"
_SCHEMA_VERSION = "decision_trace_v1"

# ─── SCORE THRESHOLD (must match new_engine.py — read-only reference) ─────────
_MIN_SCORE_THRESHOLD = 0.35


# ═══════════════════════════════════════════════════════════════════════════════
# STAGE CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════════════════

_STAGE_ORDER = (
    "pattern_detection",
    "strategy_classification",
    "scoring",
    "policy_pre",
    "swing",
    "data_validation",
    "risk",
    "ev_policy",
    "execute",
)


def _classify_terminal_stage(reason: str, action: str) -> str:
    """Classify which pipeline stage terminated this evaluation."""
    if action == "EXECUTE":
        return "execute"
    if not reason:
        return "unknown"
    if "no_viable_pattern" in reason:
        return "pattern_detection"
    if "strategy_activation_failed" in reason:
        return "strategy_classification"
    if "score_below_threshold" in reason:
        return "scoring"
    if "ev_policy_blocked" in reason:
        return "ev_policy"
    if "policy_blocked" in reason:
        return "policy_pre"
    if "swing_blocked" in reason:
        return "swing"
    if "data_invalid" in reason:
        return "data_validation"
    if "risk_rejected" in reason:
        return "risk"
    return "unknown"


def _compute_stages_reached(terminal_stage: str, action: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Determine which stages were reached and which passed."""
    if terminal_stage == "unknown":
        return (), ()

    reached: list[str] = []
    passed: list[str] = []

    for stage in _STAGE_ORDER:
        reached.append(stage)
        if stage == terminal_stage:
            if action == "EXECUTE":
                passed.append(stage)
            break
        else:
            passed.append(stage)

    return tuple(reached), tuple(passed)


# ═══════════════════════════════════════════════════════════════════════════════
# COMPONENT DIAGNOSTICS
# ═══════════════════════════════════════════════════════════════════════════════

def _resolve_weights(assessment: Any) -> dict[str, float]:
    """Resolve the weight profile used for scoring."""
    try:
        weights_used = getattr(assessment, "weights_used", "global_fallback")
        if weights_used == "strategy_specific":
            from core.pipeline.strategy_weights import get_weights_for_strategy
            from core.pipeline.strategy_classifier import StrategyType
            strategy_map = {
                "CONTINUATION": StrategyType.CONTINUATION,
                "REVERSAL": StrategyType.REVERSAL,
                "FALSE_BREAK": StrategyType.FALSE_BREAK,
            }
            selected = getattr(assessment, "selected_strategy", None)
            stype = strategy_map.get(selected) if selected else None
            if stype:
                return get_weights_for_strategy(stype)
    except Exception:
        pass

    # Global fallback
    return {
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


def _compute_component_diagnostics(
    components: dict[str, float],
    weights: dict[str, float],
    score: float,
    threshold: float,
) -> dict[str, Any]:
    """
    Compute diagnostic metrics from scoring components.

    Returns:
        weakest_component: lowest raw value
        largest_drag_component: max(weight × (1 - raw)) — biggest score cost
        closest_flip_component: smallest delta needed to cross threshold
        closest_flip_delta: the delta amount
        threshold_gap: score - threshold (negative = failed)
    """
    result: dict[str, Any] = {
        "weakest_component": None,
        "weakest_value": 0.0,
        "largest_drag_component": None,
        "largest_drag_value": 0.0,
        "closest_flip_component": None,
        "closest_flip_delta": None,
        "closest_flip_target": None,
        "flip_feasible": False,
        "threshold_gap": round(score - threshold, 6),
    }

    if not components or not weights:
        return result

    # Weakest raw signal
    weakest_name = min(components, key=lambda k: components[k])
    result["weakest_component"] = weakest_name
    result["weakest_value"] = round(components[weakest_name], 4)

    # Largest weighted drag: weight × (1 - raw)
    drags = {k: weights.get(k, 0) * (1.0 - v) for k, v in components.items() if weights.get(k, 0) > 0}
    if drags:
        drag_name = max(drags, key=lambda k: drags[k])
        result["largest_drag_component"] = drag_name
        result["largest_drag_value"] = round(drags[drag_name], 6)

    # Closest flip: minimum single-component improvement to cross threshold
    gap = threshold - score
    if gap > 0:
        candidates: list[tuple[str, float, float]] = []
        for name, raw in components.items():
            w = weights.get(name, 0)
            if w <= 0:
                continue
            delta = gap / w
            target = raw + delta
            if target <= 1.0:
                candidates.append((name, delta, target))

        if candidates:
            best = min(candidates, key=lambda x: x[1])
            result["closest_flip_component"] = best[0]
            result["closest_flip_delta"] = round(best[1], 4)
            result["closest_flip_target"] = round(best[2], 4)
            result["flip_feasible"] = True

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# DECISION TRACE MODEL
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class DecisionTrace:
    """
    Diagnostic record of one new-engine evaluation.

    This does not make decisions.
    It only records what decision the engine already made
    and the reasoning data available at that point.
    """

    # ─── IDENTITY ─────────────────────────────────────────────────────
    entity_id: str
    symbol: str
    cycle_id: int
    timestamp_utc: str
    runtime_session_id: str = ""

    # ─── OUTCOME ──────────────────────────────────────────────────────
    action: str = "NO_TRADE"          # "EXECUTE" | "NO_TRADE"
    terminal_stage: str = "unknown"
    terminal_reason: str = ""

    # ─── PIPELINE PROGRESSION ─────────────────────────────────────────
    stages_reached: tuple[str, ...] = ()
    stages_passed: tuple[str, ...] = ()

    # ─── STAGE 1: PATTERN DETECTION ───────────────────────────────────
    pattern_detected: bool = False
    pattern_name: str | None = None
    pattern_quality: float = 0.0
    pattern_count: int = 0

    # ─── STAGE 2: MARKET CONTEXT ──────────────────────────────────────
    regime: str | None = None
    regime_confidence: float = 0.0
    regime_source: str = ""               # H4_MARKET_CONTEXT | M5_CLASSIFIER
    regime_timeframe: str = ""            # H4 | M5
    market_state: str | None = None
    market_state_confidence: float = 0.0
    market_phase: str | None = None           # IMPULSE | PULLBACK | CONSOLIDATION | EXHAUSTION | REVERSAL
    market_phase_confidence: float = 0.0
    selected_strategy: str | None = None
    strategy_confidence: float = 0.0
    trade_horizon: str | None = None          # SCALP | INTRADAY | EXTENDED (independent of strategy)
    htf_alignment: float = 0.0
    h4_alignment: float = 0.0
    trend_alignment_source: str = ""      # H1_PHASE | M5_EMA50
    trend_alignment_timeframe: str = ""   # H1 | M5
    trend_alignment_confidence: float = 0.0

    # ─── STAGE 3: SCORING ─────────────────────────────────────────────
    score_neutral: float = 0.0
    score_strategy: float = 0.0
    score_delta: float = 0.0
    components: dict[str, float] = field(default_factory=dict)
    weights_used: str = ""

    # Component diagnostics
    weakest_component: str | None = None
    weakest_value: float = 0.0
    largest_drag_component: str | None = None
    largest_drag_value: float = 0.0

    # Threshold proximity
    threshold_gap: float = 0.0
    closest_flip_component: str | None = None
    closest_flip_delta: float | None = None
    flip_feasible: bool = False

    # ─── STAGE 4: EV / POLICY ─────────────────────────────────────────
    ev: float | None = None
    ev_positive: bool | None = None
    p_success: float | None = None
    rr_effective: float | None = None
    confirmation_score: float | None = None
    policy_reasoning: str = ""

    # ─── METADATA ─────────────────────────────────────────────────────
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSONL persistence."""
        return {
            "entity_id": self.entity_id,
            "symbol": self.symbol,
            "cycle_id": self.cycle_id,
            "timestamp_utc": self.timestamp_utc,
            "runtime_session_id": self.runtime_session_id,
            "action": self.action,
            "terminal_stage": self.terminal_stage,
            "terminal_reason": self.terminal_reason,
            "stages_reached": list(self.stages_reached),
            "stages_passed": list(self.stages_passed),
            "pattern_detected": self.pattern_detected,
            "pattern_name": self.pattern_name,
            "pattern_quality": self.pattern_quality,
            "pattern_count": self.pattern_count,
            "regime": self.regime,
            "regime_confidence": round(self.regime_confidence, 4),
            "regime_source": self.regime_source,
            "regime_timeframe": self.regime_timeframe,
            "market_state": self.market_state,
            "market_state_confidence": round(self.market_state_confidence, 4),
            "market_phase": self.market_phase,
            "market_phase_confidence": round(self.market_phase_confidence, 4),
            "selected_strategy": self.selected_strategy,
            "strategy_confidence": round(self.strategy_confidence, 4),
            "trade_horizon": self.trade_horizon,
            "htf_alignment": round(self.htf_alignment, 4),
            "h4_alignment": round(self.h4_alignment, 4),
            "trend_alignment_source": self.trend_alignment_source,
            "trend_alignment_timeframe": self.trend_alignment_timeframe,
            "trend_alignment_confidence": round(self.trend_alignment_confidence, 4),
            "score_neutral": round(self.score_neutral, 4),
            "score_strategy": round(self.score_strategy, 4),
            "score_delta": round(self.score_delta, 4),
            "components": {k: round(v, 4) for k, v in self.components.items()} if self.components else {},
            "weights_used": self.weights_used,
            "weakest_component": self.weakest_component,
            "weakest_value": round(self.weakest_value, 4),
            "largest_drag_component": self.largest_drag_component,
            "largest_drag_value": round(self.largest_drag_value, 6),
            "threshold_gap": round(self.threshold_gap, 6),
            "closest_flip_component": self.closest_flip_component,
            "closest_flip_delta": round(self.closest_flip_delta, 4) if self.closest_flip_delta is not None else None,
            "flip_feasible": self.flip_feasible,
            "ev": round(self.ev, 6) if self.ev is not None else None,
            "ev_positive": self.ev_positive,
            "p_success": round(self.p_success, 4) if self.p_success is not None else None,
            "rr_effective": round(self.rr_effective, 3) if self.rr_effective is not None else None,
            "confirmation_score": round(self.confirmation_score, 4) if self.confirmation_score is not None else None,
            "policy_reasoning": self.policy_reasoning,
            "metadata": self.metadata,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# TRACE BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

def build_decision_trace(
    *,
    engine_result: dict[str, Any],
    runtime_session_id: str = "",
    pattern_count: int = 0,
) -> DecisionTrace:
    """
    Build a DecisionTrace from a run_new_engine() result dict.

    Reads only — never modifies engine_result.
    Never raises — returns minimal trace on error.
    """
    try:
        return _build_trace(engine_result, runtime_session_id, pattern_count)
    except Exception:
        return DecisionTrace(
            entity_id=engine_result.get("entity_id", ""),
            symbol=engine_result.get("symbol", "unknown"),
            cycle_id=engine_result.get("cycle_id", 0),
            timestamp_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            action=engine_result.get("action", "NO_TRADE"),
            terminal_stage="error",
            terminal_reason="trace_build_failed",
            metadata={"error": True},
        )


def _build_trace(
    engine_result: dict[str, Any],
    runtime_session_id: str,
    pattern_count: int,
) -> DecisionTrace:
    """Internal trace construction — may raise."""
    action = engine_result.get("action", "NO_TRADE")
    reason = engine_result.get("reason", "")
    assessment = engine_result.get("assessment")

    # Classify terminal stage
    terminal_stage = _classify_terminal_stage(reason, action)
    stages_reached, stages_passed = _compute_stages_reached(terminal_stage, action)

    # Extract fields (None-safe for early exits)
    entity_id = engine_result.get("entity_id", "")
    symbol = engine_result.get("symbol", "") or (getattr(assessment, "symbol", "") if assessment else "")
    cycle_id = engine_result.get("cycle_id", 0)
    components = engine_result.get("components", {})
    score_strategy = engine_result.get("score_strategy", engine_result.get("score", 0.0))
    score_neutral = engine_result.get("score_neutral", 0.0)

    # Pattern detection
    pattern_name = engine_result.get("pattern")
    pattern_detected = pattern_name is not None

    # Market context (from assessment or result)
    regime = None
    regime_confidence = 0.0
    market_state = None
    market_state_confidence = 0.0
    selected_strategy = None
    strategy_confidence = 0.0
    htf_alignment = 0.0
    h4_alignment = 0.0
    pattern_quality = 0.0
    weights_used = ""

    if assessment:
        regime = getattr(assessment, "regime", None)
        regime_confidence = getattr(assessment, "regime_confidence", 0.0)
        market_state = getattr(assessment, "market_state", None)
        market_state_confidence = getattr(assessment, "market_state_confidence", 0.0)
        selected_strategy = getattr(assessment, "selected_strategy", None)
        strategy_confidence = getattr(assessment, "strategy_confidence", 0.0)
        htf_alignment = getattr(assessment, "htf_alignment", 0.0)
        h4_alignment = getattr(assessment, "h4_alignment", 0.0)
        pattern_quality = getattr(assessment, "pattern_quality", 0.0)
        weights_used = getattr(assessment, "weights_used", "")
    else:
        # Fallback to result dict fields
        regime = engine_result.get("activation_regime")
        regime_confidence = engine_result.get("activation_regime_confidence", 0.0)
        market_state = engine_result.get("market_state")
        market_state_confidence = engine_result.get("market_state_confidence", 0.0)
        selected_strategy = engine_result.get("strategy")
        strategy_confidence = engine_result.get("strategy_confidence", 0.0)
        weights_used = engine_result.get("weights_used", "")

    # Regime source metadata (from Migration 1 — observability)
    regime_source = engine_result.get("regime_source", "") or ""
    regime_timeframe = "H4" if regime_source == "H4_MARKET_CONTEXT" else "M5" if regime_source == "M5_CLASSIFIER" else ""

    # Market phase (from MarketContext.phase — observability only)
    market_phase = engine_result.get("market_phase") or None
    market_phase_confidence = float(engine_result.get("market_phase_confidence", 0.0) or 0.0)

    # Trade horizon (observability — independent of strategy)
    trade_horizon = engine_result.get("trade_horizon") or None

    # Trend alignment source metadata (from Migration 2 — observability)
    trend_alignment_source = engine_result.get("trend_alignment_source", "") or ""
    trend_alignment_timeframe = engine_result.get("trend_alignment_timeframe", "") or ""
    trend_alignment_confidence = engine_result.get("trend_alignment_confidence", 0.0) or 0.0

    # Component diagnostics
    diag: dict[str, Any] = {}
    if components:
        weights = _resolve_weights(assessment) if assessment else {
            "pattern_quality": 0.14, "bias_alignment": 0.18, "market_quality": 0.08,
            "trend_alignment": 0.10, "chop_clarity": 0.06, "volatility_quality": 0.07,
            "bias_stability": 0.07, "confirmation_pre": 0.06, "htf_alignment": 0.14,
            "h4_alignment": 0.10,
        }
        diag = _compute_component_diagnostics(components, weights, score_strategy, _MIN_SCORE_THRESHOLD)

    # EV / Policy (only available on later exits)
    ev = engine_result.get("ev")
    ev_positive = engine_result.get("ev_positive")
    p_success = engine_result.get("p_success")
    rr_effective = engine_result.get("rr_effective")
    confirmation_score = engine_result.get("confirmation_score")
    policy_reasoning = engine_result.get("policy_reasoning", "")

    return DecisionTrace(
        entity_id=entity_id,
        symbol=symbol,
        cycle_id=cycle_id,
        timestamp_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        runtime_session_id=runtime_session_id,
        action=action,
        terminal_stage=terminal_stage,
        terminal_reason=reason,
        stages_reached=stages_reached,
        stages_passed=stages_passed,
        pattern_detected=pattern_detected,
        pattern_name=pattern_name,
        pattern_quality=pattern_quality,
        pattern_count=pattern_count,
        regime=regime,
        regime_confidence=regime_confidence,
        regime_source=regime_source,
        regime_timeframe=regime_timeframe,
        market_state=market_state,
        market_state_confidence=market_state_confidence,
        market_phase=market_phase,
        market_phase_confidence=market_phase_confidence,
        selected_strategy=selected_strategy,
        strategy_confidence=strategy_confidence,
        trade_horizon=trade_horizon,
        htf_alignment=htf_alignment,
        h4_alignment=h4_alignment,
        trend_alignment_source=trend_alignment_source,
        trend_alignment_timeframe=trend_alignment_timeframe,
        trend_alignment_confidence=trend_alignment_confidence,
        score_neutral=score_neutral,
        score_strategy=score_strategy,
        score_delta=round(score_strategy - score_neutral, 4),
        components=dict(components) if components else {},
        weights_used=weights_used,
        weakest_component=diag.get("weakest_component"),
        weakest_value=diag.get("weakest_value", 0.0),
        largest_drag_component=diag.get("largest_drag_component"),
        largest_drag_value=diag.get("largest_drag_value", 0.0),
        threshold_gap=diag.get("threshold_gap", 0.0),
        closest_flip_component=diag.get("closest_flip_component"),
        closest_flip_delta=diag.get("closest_flip_delta"),
        flip_feasible=diag.get("flip_feasible", False),
        ev=ev,
        ev_positive=ev_positive,
        p_success=p_success,
        rr_effective=rr_effective,
        confirmation_score=confirmation_score,
        policy_reasoning=policy_reasoning,
        metadata={},
    )


# ═══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE
# ═══════════════════════════════════════════════════════════════════════════════

def _write_s3(symbol: str, date_str: str, line: str) -> None:
    """
    Mirror trace record to S3. Fire-and-forget. Never raises.
    Follows the same pattern as decision_ledger.py and opportunity_assessment_writer.py.
    """
    try:
        from core import config as _cfg
        if not getattr(_cfg, "EVENT_STREAM_S3_MIRROR", False):
            return

        import boto3
        from botocore.config import Config as BotoConfig
        s3 = boto3.client(
            "s3",
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=os.getenv("AWS_REGION", "eu-west-2"),
            config=BotoConfig(
                connect_timeout=3,
                read_timeout=5,
                retries={"max_attempts": 0},
            ),
        )
        key = f"{_S3_PREFIX}/symbol={symbol}/date={date_str}/part-000.jsonl"
        body = line + "\n"

        # Read-append-write (acceptable for trace volume)
        try:
            existing = s3.get_object(Bucket=_S3_BUCKET, Key=key)
            body = existing["Body"].read().decode("utf-8") + body
        except Exception:
            pass  # New file

        s3.put_object(
            Bucket=_S3_BUCKET, Key=key,
            Body=body.encode("utf-8"),
            ContentType="application/x-ndjson",
        )
    except Exception:
        pass  # S3 failure must never affect runtime


def persist_decision_trace(trace: DecisionTrace) -> None:
    """
    Append trace to local JSONL + S3 mirror. Fire-and-forget. Never raises.
    Never affects trading behaviour.
    """
    try:
        symbol = trace.symbol or "UNKNOWN"
        ts = trace.timestamp_utc[:10] if len(trace.timestamp_utc) >= 10 else datetime.now(timezone.utc).strftime("%Y-%m-%d")

        path = Path(_LOCAL_DIR) / symbol / f"{ts}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)

        _trace_dict = trace.to_dict()
        _trace_dict["schema_version"] = _SCHEMA_VERSION
        line = json.dumps(_trace_dict, separators=(",", ":"), default=str)
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

        # S3 mirror (fire-and-forget durability)
        _write_s3(symbol, ts, line)

    except Exception:
        pass  # Trace persistence must NEVER affect trading


# ═══════════════════════════════════════════════════════════════════════════════
# DECISION FUNNEL (cumulative aggregator derived from traces)
# ═══════════════════════════════════════════════════════════════════════════════

# Stage mapping: terminal_stage → 4-stage model
_STAGE_TO_FUNNEL = {
    "pattern_detection": 1,
    "strategy_classification": 2,
    "scoring": 3,
    "policy_pre": 3,
    "swing": 4,
    "data_validation": 4,
    "risk": 4,
    "ev_policy": 4,
    "execute": 4,
}


class DecisionFunnel:
    """
    Cumulative aggregator for DecisionTrace data.

    Derives the hierarchical decision funnel display from trace objects.
    Replaces ad-hoc _filter_hits counting with structured trace-based aggregation.

    This is PURELY OBSERVATIONAL. It does not affect trading.
    """

    def __init__(self) -> None:
        self._total_evaluations: int = 0
        self._executes: int = 0

        # Stage 1: Pattern Detection
        self._s1_failures: int = 0

        # Stage 2: Market Context / Strategy
        self._s2_failures: int = 0
        self._s2_reasons: dict[str, int] = {}

        # Stage 3: Scoring
        self._s3_failures: int = 0
        self._s3_reasons: dict[str, int] = {}
        self._s3_drags: dict[str, int] = {}      # largest_drag_component tallies
        self._s3_weakest: dict[str, int] = {}    # weakest_component tallies
        self._s3_flips: dict[str, int] = {}      # closest_flip_component tallies

        # Stage 4: Execution Policy
        self._s4_failures: int = 0
        self._s4_reasons: dict[str, int] = {}

        # Runtime guards (post-engine, not from trace)
        self._guard_hits: dict[str, int] = {
            "daily_trade_limit": 0,
            "cooldown": 0,
            "correlation": 0,
            "exposure": 0,
            "regime_guard": 0,
            "spread_guard": 0,
        }

    def record_trace(self, trace: DecisionTrace) -> None:
        """Ingest one DecisionTrace into cumulative tallies. Never raises."""
        try:
            self._total_evaluations += 1

            if trace.action == "EXECUTE":
                self._executes += 1
                return

            stage = _STAGE_TO_FUNNEL.get(trace.terminal_stage, 0)

            if stage == 1:
                self._s1_failures += 1

            elif stage == 2:
                self._s2_failures += 1
                reason = trace.terminal_reason.split(":")[-1].strip() if ":" in trace.terminal_reason else trace.terminal_stage
                self._s2_reasons[reason] = self._s2_reasons.get(reason, 0) + 1

            elif stage == 3:
                self._s3_failures += 1
                reason = trace.terminal_reason.split(":")[-1].strip() if ":" in trace.terminal_reason else trace.terminal_stage
                self._s3_reasons[reason] = self._s3_reasons.get(reason, 0) + 1
                # Component diagnostics
                if trace.largest_drag_component:
                    self._s3_drags[trace.largest_drag_component] = self._s3_drags.get(trace.largest_drag_component, 0) + 1
                if trace.weakest_component:
                    self._s3_weakest[trace.weakest_component] = self._s3_weakest.get(trace.weakest_component, 0) + 1
                if trace.closest_flip_component:
                    self._s3_flips[trace.closest_flip_component] = self._s3_flips.get(trace.closest_flip_component, 0) + 1

            elif stage == 4:
                self._s4_failures += 1
                # Sub-classify execution policy failures
                reason = trace.terminal_reason
                if "NEGATIVE_EXPECTED_VALUE" in reason:
                    sub = "EV_NEGATIVE"
                elif "RR_BELOW" in reason:
                    sub = "RR_INSUFFICIENT"
                elif "risk_rejected" in reason:
                    sub = "RISK_REJECTED"
                elif "swing_blocked" in reason:
                    sub = "SWING_BLOCKED"
                elif "data_invalid" in reason:
                    sub = "DATA_INVALID"
                else:
                    sub = trace.terminal_stage
                self._s4_reasons[sub] = self._s4_reasons.get(sub, 0) + 1

        except Exception:
            pass  # Funnel recording must never affect trading

    def record_guard_block(self, guard_name: str) -> None:
        """Record a runtime guard block (post-engine). Never raises."""
        try:
            if guard_name in self._guard_hits:
                self._guard_hits[guard_name] += 1
        except Exception:
            pass

    def format_console(self, cycle_id: int) -> str:
        """Format the hierarchical decision funnel for console display."""
        total = self._total_evaluations
        if total == 0:
            return f"===== DECISION FUNNEL (cycle {cycle_id}) =====\n  No evaluations yet.\n"

        # Compute flow-through counts
        reached_s2 = total - self._s1_failures
        reached_s3 = reached_s2 - self._s2_failures
        reached_s4 = reached_s3 - self._s3_failures
        passed_all = reached_s4 - self._s4_failures
        guard_total = sum(self._guard_hits.values())
        executed = self._executes

        def _pct(n: int) -> str:
            return f"{n/total*100:.0f}%" if total > 0 else "0%"

        def _top3(d: dict[str, int]) -> str:
            if not d:
                return "none"
            items = sorted(d.items(), key=lambda x: -x[1])[:3]
            return ", ".join(f"{k}({v})" for k, v in items)

        lines = [
            f"",
            f"===== DECISION FUNNEL (cumulative @ cycle {cycle_id}) =====",
            f"",
            f"  Total evaluations: {total}",
            f"",
            f"  ┌─ STAGE 1: PATTERN DETECTION",
            f"  │  Reached:  {total}",
            f"  │  Failed:   {self._s1_failures} ({_pct(self._s1_failures)}) [no_viable_pattern]",
            f"  │  Passed:   {reached_s2}",
            f"  │",
            f"  ├─ STAGE 2: MARKET CONTEXT / STRATEGY",
            f"  │  Reached:  {reached_s2}",
            f"  │  Failed:   {self._s2_failures} ({_pct(self._s2_failures)})",
        ]
        if self._s2_reasons:
            lines.append(f"  │  Reasons:  {_top3(self._s2_reasons)}")
        lines += [
            f"  │  Passed:   {reached_s3}",
            f"  │",
            f"  ├─ STAGE 3: SCORING ENGINE",
            f"  │  Reached:  {reached_s3}",
            f"  │  Failed:   {self._s3_failures} ({_pct(self._s3_failures)})",
        ]
        if self._s3_reasons:
            lines.append(f"  │  Reasons:  {_top3(self._s3_reasons)}")
        if self._s3_drags:
            lines.append(f"  │  Drag:     {_top3(self._s3_drags)}")
        if self._s3_weakest:
            lines.append(f"  │  Weakest:  {_top3(self._s3_weakest)}")
        if self._s3_flips:
            lines.append(f"  │  Flip via: {_top3(self._s3_flips)}")
        lines += [
            f"  │  Passed:   {reached_s4}",
            f"  │",
            f"  ├─ STAGE 4: EXECUTION POLICY",
            f"  │  Reached:  {reached_s4}",
            f"  │  Failed:   {self._s4_failures} ({_pct(self._s4_failures)})",
        ]
        if self._s4_reasons:
            lines.append(f"  │  Reasons:  {_top3(self._s4_reasons)}")
        lines += [
            f"  │  Passed:   {passed_all}",
            f"  │",
            f"  ├─ RUNTIME GUARDS (post-engine)",
        ]
        if guard_total > 0:
            for g, c in sorted(self._guard_hits.items(), key=lambda x: -x[1]):
                if c > 0:
                    lines.append(f"  │  {g}: {c}")
        else:
            lines.append(f"  │  (none blocked)")
        lines += [
            f"  │",
            f"  └─ EXECUTED ═══════════════════► {executed} trades",
            f"",
        ]

        # Top blockers summary
        all_stages = {
            "S1:Pattern": self._s1_failures,
            "S2:Context": self._s2_failures,
            "S3:Scoring": self._s3_failures,
            "S4:Policy": self._s4_failures,
        }
        if guard_total > 0:
            all_stages["Guards"] = guard_total
        top = sorted(all_stages.items(), key=lambda x: -x[1])[:3]
        top_str = "  ".join(f"{k}={v}({v*100//total}%)" for k, v in top if v > 0)
        if top_str:
            lines.append(f"  Top blockers: {top_str}")
            lines.append(f"")

        return "\n".join(lines)
