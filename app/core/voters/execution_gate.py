"""
Voter: ExecutionGate
Domain: execution_safety
Layer: post-confluence (between ConfluenceEngine and Risk/MT5 execution)
Input: ConfluenceDecision + StateSnapshot (read-only)
Mutability: NONE
Dependencies: NONE
Signal Type: safety-checkpoint-only

Deterministic pre-trade safety filter.
Does NOT generate decisions — only blocks or allows.
Does NOT modify ConfluenceDecision.
Does NOT recompute any voter logic.
Does NOT access EngineState directly.

HARD RULES:
  ❌ Cannot override BUY/SELL — only block or allow
  ❌ Cannot modify confluence score
  ❌ Cannot access EngineState
  ❌ Cannot recompute features
  ✅ Only evaluates execution safety conditions
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.state.snapshot import StateSnapshot
from core.voters.confluence_engine import ConfluenceDecision


# ─── SAFETY THRESHOLDS ────────────────────────────────────────────────────────

SPREAD_ATR_MAX = 0.30          # spread / ATR must be below this
VOLATILITY_SPIKE_MAX = 2.0     # ATR ratio must be below this
STRUCTURE_CLARITY_MIN = 0.2    # structure clarity must exceed this
SESSION_SCORE_MIN = -1.0       # session voter score must exceed this
CONFLUENCE_MIN = 0.75          # abs(confluence score) must exceed this
CONFLICT_MIN_CONFIDENCE = 0.8  # confidence required when conflict_flag is True


# ─── OUTPUT TYPE ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ExecutionGateResult:
    """
    Result of execution safety evaluation.

    allowed: True if trade may proceed to risk/execution layer
    reason: human-readable explanation
    blocked_by: list of check names that failed
    adjusted_confidence: confidence after conflict adjustment
    """

    allowed: bool
    reason: str
    blocked_by: list[str] = field(default_factory=list)
    adjusted_confidence: float = 0.0


# ─── GATE EVALUATION ──────────────────────────────────────────────────────────

def evaluate_execution_gate(
    confluence: ConfluenceDecision,
    snapshot: StateSnapshot,
) -> ExecutionGateResult:
    """
    Evaluate whether a trade decision is safe to execute.

    Pure function: deterministic, no side effects, no state mutation.

    Args:
        confluence: ConfluenceDecision from voter aggregation
        snapshot: Frozen StateSnapshot (for feature-derived safety checks)

    Returns:
        ExecutionGateResult with allowed/blocked status and reasons.
    """
    blockers: list[str] = []

    # 1. SPREAD SAFETY CHECK
    if snapshot.m5_atr_14 > 0:
        spread_ratio = snapshot.spread / snapshot.m5_atr_14
        if spread_ratio > SPREAD_ATR_MAX:
            blockers.append(f"spread_too_high ({spread_ratio:.2f} > {SPREAD_ATR_MAX})")

    # 2. VOLATILITY SPIKE CHECK
    if snapshot.m5_atr_ratio > VOLATILITY_SPIKE_MAX:
        blockers.append(f"volatility_spike ({snapshot.m5_atr_ratio:.2f} > {VOLATILITY_SPIKE_MAX})")

    # 3. STRUCTURE QUALITY CHECK
    if snapshot.m5_structure_clarity < STRUCTURE_CLARITY_MIN:
        blockers.append(f"low_structure_clarity ({snapshot.m5_structure_clarity:.2f} < {STRUCTURE_CLARITY_MIN})")

    # 4. CONFLUENCE MINIMUM CHECK
    if abs(confluence.score) < CONFLUENCE_MIN:
        blockers.append(f"weak_signal ({abs(confluence.score):.3f} < {CONFLUENCE_MIN})")

    # 5. CONFLICT OVERRIDE CHECK
    adjusted_confidence = confluence.confidence
    if confluence.conflict_flag:
        if confluence.confidence < CONFLICT_MIN_CONFIDENCE:
            blockers.append(f"conflict_low_confidence ({confluence.confidence:.2f} < {CONFLICT_MIN_CONFIDENCE})")
        else:
            # Conflict present but confidence high enough — reduce confidence slightly
            adjusted_confidence = confluence.confidence * 0.85

    # 6. RISK FLAG HARD STOP
    if confluence.risk_flag:
        blockers.append("risk_flag_active")

    # Decision
    if blockers:
        return ExecutionGateResult(
            allowed=False,
            reason=f"BLOCKED: {' | '.join(blockers)}",
            blocked_by=blockers,
            adjusted_confidence=adjusted_confidence,
        )

    return ExecutionGateResult(
        allowed=True,
        reason="PASS: all execution conditions satisfied",
        blocked_by=[],
        adjusted_confidence=adjusted_confidence,
    )
