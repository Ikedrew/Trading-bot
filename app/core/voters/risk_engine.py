"""
Voter: RiskEngine
Domain: position_sizing
Layer: post-execution-gate (between ExecutionGate and MT5 Execution)
Input: ConfluenceDecision + ExecutionGateResult + StateSnapshot + equity
Mutability: NONE
Dependencies: NONE
Signal Type: risk-scaling-only

Converts an approved trade into position size and risk exposure.
Does NOT decide direction — only scales risk.
Does NOT re-run voters, confluence, or execution gate.
Does NOT access EngineState directly.
Deterministic: same inputs → same output.

HARD RULES:
  ❌ Cannot change BUY/SELL/NO_TRADE
  ❌ Cannot re-check execution gate
  ❌ Cannot access EngineState
  ❌ Cannot create direction
  ✅ Only scales risk based on market conditions
"""

from __future__ import annotations

from dataclasses import dataclass

from core.state.snapshot import StateSnapshot
from core.voters.confluence_engine import ConfluenceDecision
from core.voters.execution_gate import ExecutionGateResult


# ─── RISK LIMITS ──────────────────────────────────────────────────────────────

BASE_RISK_PERCENT = 1.0        # Starting risk per trade
MIN_RISK_PERCENT = 0.25        # Floor
MAX_RISK_PERCENT = 2.0         # Ceiling (single trade)
MAX_TOTAL_EXPOSURE = 5.0       # Maximum total account exposure %


# ─── OUTPUT TYPE ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RiskDecision:
    """
    Final risk sizing output.

    allowed: True if trade can proceed with this sizing
    position_size: lots to trade (0.0 if blocked)
    risk_amount: currency amount at risk
    risk_percent: final risk % of equity
    adjusted_confidence: confidence after all adjustments
    reason: human-readable breakdown of multipliers applied
    """

    allowed: bool
    position_size: float
    risk_amount: float
    risk_percent: float
    adjusted_confidence: float
    reason: str


# ─── RISK ENGINE ──────────────────────────────────────────────────────────────

def compute_risk(
    *,
    confluence: ConfluenceDecision,
    gate: ExecutionGateResult,
    snapshot: StateSnapshot,
    equity: float,
    stop_loss_distance: float,
    pip_value: float = 10.0,
    current_exposure_percent: float = 0.0,
    session_score: float = 0.0,
) -> RiskDecision:
    """
    Compute position size and risk exposure for an approved trade.

    Pure function: deterministic, no side effects, no state mutation.

    Args:
        confluence: ConfluenceDecision (for confidence + score strength)
        gate: ExecutionGateResult (for adjusted confidence)
        snapshot: StateSnapshot (for volatility + structure features)
        equity: Current account equity
        stop_loss_distance: Distance to stop loss in price units
        pip_value: Value per pip per lot (default 10.0 for standard FX)
        current_exposure_percent: Current total exposure % (for cap check)
        session_score: SessionVoter score (for session adjustment)

    Returns:
        RiskDecision with position size, risk amount, and reasoning.
    """
    multipliers: list[str] = []

    # BLOCK: No stop loss = no trade
    if stop_loss_distance <= 0:
        return RiskDecision(
            allowed=False,
            position_size=0.0,
            risk_amount=0.0,
            risk_percent=0.0,
            adjusted_confidence=gate.adjusted_confidence,
            reason="BLOCKED: no stop_loss_distance",
        )

    # BLOCK: Equity invalid
    if equity <= 0:
        return RiskDecision(
            allowed=False,
            position_size=0.0,
            risk_amount=0.0,
            risk_percent=0.0,
            adjusted_confidence=gate.adjusted_confidence,
            reason="BLOCKED: invalid equity",
        )

    # Start with base risk
    risk_multiplier = 1.0
    multipliers.append(f"base={BASE_RISK_PERCENT}%")

    # 1. Confidence scaling (0.5–1.2 range)
    conf = gate.adjusted_confidence
    conf_scale = max(0.5, min(1.2, conf))
    risk_multiplier *= conf_scale
    multipliers.append(f"conf({conf_scale:.2f})")

    # 2. Volatility scaling
    atr_ratio = snapshot.m5_atr_ratio
    if atr_ratio > 1.5:
        risk_multiplier *= 0.7
        multipliers.append("vol_high(0.7)")
    elif atr_ratio < 0.8:
        risk_multiplier *= 1.1
        multipliers.append("vol_low(1.1)")

    # 3. Structure quality scaling
    clarity = snapshot.m5_structure_clarity
    if clarity > 0.7:
        risk_multiplier *= 1.1
        multipliers.append("struct_high(1.1)")
    elif clarity < 0.3:
        risk_multiplier *= 0.6
        multipliers.append("struct_low(0.6)")

    # 4. Session adjustment
    if session_score < 0:
        risk_multiplier *= 0.8
        multipliers.append("session_neg(0.8)")

    # 5. Confluence direction strength
    if abs(confluence.score) > 1.2:
        risk_multiplier *= 1.2
        multipliers.append("strong_signal(1.2)")

    # Final risk percent (clamped)
    final_risk_percent = BASE_RISK_PERCENT * risk_multiplier
    final_risk_percent = max(MIN_RISK_PERCENT, min(MAX_RISK_PERCENT, final_risk_percent))
    multipliers.append(f"final={final_risk_percent:.2f}%")

    # BLOCK: Exposure cap
    if current_exposure_percent + final_risk_percent > MAX_TOTAL_EXPOSURE:
        return RiskDecision(
            allowed=False,
            position_size=0.0,
            risk_amount=0.0,
            risk_percent=final_risk_percent,
            adjusted_confidence=conf,
            reason=f"BLOCKED: exposure_cap ({current_exposure_percent:.1f}% + {final_risk_percent:.2f}% > {MAX_TOTAL_EXPOSURE}%)",
        )

    # Position sizing
    risk_amount = equity * (final_risk_percent / 100.0)
    position_size = risk_amount / (stop_loss_distance * pip_value)

    # Clamp position size to reasonable bounds
    position_size = max(0.01, round(position_size, 2))

    return RiskDecision(
        allowed=True,
        position_size=position_size,
        risk_amount=round(risk_amount, 2),
        risk_percent=round(final_risk_percent, 4),
        adjusted_confidence=round(conf, 4),
        reason=" → ".join(multipliers),
    )
