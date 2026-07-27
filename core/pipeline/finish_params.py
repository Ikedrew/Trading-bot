"""Authoritative outcome wiring for the single decision point (Stage 3).

Subsystems emit FinishParams; DecisionEngine converts to Decision unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.pipeline_types import Decision
from risk.models import OrderIntent
from strategy.signals import Signal, Side


@dataclass(frozen=True)
class FinishParams:
    """Exact payload that previously went to `finish(...)` / `Decision(...)`."""

    should_trade: bool
    reason: str
    signal: Signal | None = None
    intent: OrderIntent | None = None
    bias: Side | None = None
    patterns: list[str] | None = None
    score: int = 0
    bias_phase: str = "EXPIRED"
    bias_validation_score: int = 0
    structure_ok: bool = False
    bias_strength: float = 0.0
    bias_age_seconds: float = 0.0
    bias_window_phase: str = "early"
    confluence_threshold_dynamic: float = 0.0
    regime_state: str = "RANGING"
    confluence_breakdown: dict[str, float | str] | None = None

    # ─── CONFIRMATION METADATA (observability only) ───────────────────
    # These fields carry structured confirmation quality into the finish layer
    # for post-trade analysis. They NEVER influence scoring, execution, or risk.
    confirmation_strength: str | None = None     # "INVALID" / "WEAK" / "STRONG"
    confirmation_body_pct: float | None = None   # 0.0–1.0
    confirmation_wick_ratio: float | None = None  # 0.0–1.0
    confirmation_close_location: float | None = None  # 0.0–1.0


def finish_params_to_decision(p: FinishParams) -> Decision:
    return Decision(
        should_trade=p.should_trade,
        reason=p.reason,
        signal=p.signal,
        intent=p.intent,
        bias=p.bias,
        patterns=p.patterns,
        score=p.score,
        bias_phase=p.bias_phase,
        bias_validation_score=p.bias_validation_score,
        structure_ok=p.structure_ok,
        bias_strength=p.bias_strength,
        bias_age_seconds=p.bias_age_seconds,
        bias_window_phase=p.bias_window_phase,
        confluence_threshold_dynamic=p.confluence_threshold_dynamic,
        regime_state=p.regime_state,
        confluence_breakdown=p.confluence_breakdown,
    )
