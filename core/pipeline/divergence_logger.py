"""
Phase 4 Divergence Logger — Compares legacy vs new shadow system outputs.

Strictly observational. Does NOT influence trading decisions.
Runs after both systems compute, before control layer / execution.

Produces:
- Structured JSON log (for analysis)
- Human-readable compact line (for console)
- Divergence score (quantified drift metric)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class OldSystemOutput:
    """Captured output from legacy run_strategy_detection() + process_bar()."""
    bias: str | None          # e.g. "BUY", "SELL", None
    bias_phase: str           # "BUILDING", "CONFIRMED", "EXPIRED"
    bias_strength: float
    threshold: float          # dynamic confluence threshold
    score: float              # final score (0 if not reached)
    pattern_count: int        # number of patterns detected
    signal_valid: bool        # did pipeline produce should_trade=True?


@dataclass
class NewSystemOutput:
    """Captured output from new shadow system (bias_context + scoring_inputs)."""
    bias: str | None
    bias_phase: str
    bias_strength: float
    threshold: float
    bias_age_seconds: float
    bias_confirmation_count: int


@dataclass
class DivergenceResult:
    """Comparison result between old and new systems."""
    bias_match: bool
    phase_match: bool
    score_diff: float
    threshold_diff: float
    direction_conflict: bool
    divergence_score: float
    classification: str       # "aligned", "mild_drift", "structural_drift", "critical"


def compare_systems(
    symbol: str,
    cycle_id: int,
    old: OldSystemOutput,
    new: NewSystemOutput,
) -> DivergenceResult:
    """
    Compare legacy vs new system outputs and quantify divergence.

    Returns DivergenceResult with classification.
    """
    # Bias comparison
    old_bias_str = old.bias if old.bias else "NONE"
    new_bias_str = new.bias if new.bias else "NONE"
    bias_match = (old_bias_str == new_bias_str)

    # Phase comparison
    phase_match = (old.bias_phase == new.bias_phase)

    # Threshold comparison
    threshold_diff = abs(old.threshold - new.threshold)

    # Score diff (new system doesn't produce score yet — use 0)
    score_diff = 0.0

    # Direction conflict
    direction_conflict = (
        old.bias is not None
        and new.bias is not None
        and old.bias != new.bias
    )

    # Compute divergence score
    div_score = 0.0
    if not bias_match:
        div_score += 5.0
    if not phase_match:
        div_score += 3.0
    div_score += threshold_diff * 2.0
    if direction_conflict:
        div_score += 5.0

    # Classify
    if div_score <= 2.0:
        classification = "aligned"
    elif div_score <= 5.0:
        classification = "mild_drift"
    elif div_score <= 10.0:
        classification = "structural_drift"
    else:
        classification = "critical"

    result = DivergenceResult(
        bias_match=bias_match,
        phase_match=phase_match,
        score_diff=score_diff,
        threshold_diff=round(threshold_diff, 4),
        direction_conflict=direction_conflict,
        divergence_score=round(div_score, 2),
        classification=classification,
    )

    # Emit logs
    _log_compact(symbol, cycle_id, result)
    _log_structured(symbol, cycle_id, old, new, result)

    return result


def _log_compact(symbol: str, cycle_id: int, result: DivergenceResult) -> None:
    """Emit human-readable compact log line + Discord for non-aligned cases."""
    bias_ok = "BIAS OK" if result.bias_match else "BIAS ⚠"
    phase_ok = "PHASE OK" if result.phase_match else "PHASE ⚠"
    align = "true" if result.classification == "aligned" else "false"

    line = (
        f"[PH4] {symbol} | {bias_ok} | {phase_ok} | "
        f"ΔTHRESH={result.threshold_diff:.3f} | "
        f"DIV={result.divergence_score:.1f} | ALIGN={align}"
    )

    if result.classification in ("structural_drift", "critical"):
        print(f"[PH4] ⚠ DIVERGENCE DETECTED | {symbol} | score={result.divergence_score:.1f} | class={result.classification}")
        # Discord: divergence alert (only for significant drift)
        try:
            from core.discord_notifier import send_discord
            send_discord("decision-log",
                f"🔀 **PH4 DIVERGENCE** | {symbol} | "
                f"div_score={result.divergence_score:.1f} | class={result.classification} | "
                f"bias={'OK' if result.bias_match else 'MISMATCH'} | "
                f"phase={'OK' if result.phase_match else 'MISMATCH'} | "
                f"Δthresh={result.threshold_diff:.3f}"
            )
        except Exception:
            pass
    else:
        print(line)


def _log_structured(
    symbol: str,
    cycle_id: int,
    old: OldSystemOutput,
    new: NewSystemOutput,
    result: DivergenceResult,
) -> None:
    """Emit structured JSON log for analysis."""
    record = {
        "meta": {
            "symbol": symbol,
            "cycle_id": cycle_id,
        },
        "old": {
            "bias": old.bias,
            "bias_phase": old.bias_phase,
            "bias_strength": round(old.bias_strength, 3),
            "threshold": round(old.threshold, 4),
            "score": round(old.score, 3),
            "pattern_count": old.pattern_count,
            "signal_valid": old.signal_valid,
        },
        "new": {
            "bias": new.bias,
            "bias_phase": new.bias_phase,
            "bias_strength": round(new.bias_strength, 3),
            "threshold": round(new.threshold, 4),
            "bias_age_seconds": round(new.bias_age_seconds, 1),
            "bias_confirmation_count": new.bias_confirmation_count,
        },
        "delta": {
            "bias_match": result.bias_match,
            "phase_match": result.phase_match,
            "score_diff": result.score_diff,
            "threshold_diff": result.threshold_diff,
            "direction_conflict": result.direction_conflict,
            "divergence_score": result.divergence_score,
            "classification": result.classification,
        },
    }

    logger.info("[PH4_STRUCTURED] %s", json.dumps(record, default=str))
