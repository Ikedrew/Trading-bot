"""
V3 Execution Assessment Builder — Assembles final trade simulation record.

Consumes all upstream assessments and produces a simple execution record.
No intelligence. No filtering. No overriding upstream decisions.

The builder simply translates upstream outputs into execution parameters.
"""

from __future__ import annotations

import logging
from typing import Any

from core.v3_shadow.context_models import V3MarketContext
from core.v3_shadow.opportunity_models import OpportunityAssessment, INSUFFICIENT_CONTEXT, LOW_QUALITY_CONTEXT
from core.v3_shadow.horizon_models import HorizonAssessment, NO_HORIZON, SCALP, INTRADAY, EXTENDED
from core.v3_shadow.entry_models import (
    EntryAssessment, VALID_ENTRY_CONFIRMATION, WEAK_ENTRY_CONFIRMATION,
    NO_ENTRY_CONFIRMATION, INSUFFICIENT_ENTRY_DATA,
)
from core.v3_shadow.risk_models import (
    RiskAssessment, ACCEPTABLE_RISK, MARGINAL_RISK, POOR_RISK, INSUFFICIENT_RISK_DATA,
)
from core.v3_shadow.execution_models import (
    ExecutionAssessment,
    READY_FOR_EXECUTION,
    EXECUTION_CONSTRAINED,
    NOT_EXECUTABLE,
    SIMULATED_ONLY,
    ORDER_MARKET,
    ORDER_LIMIT,
    MGMT_FAST,
    MGMT_TRAIL_BREAKEVEN,
    MGMT_STRUCTURAL,
    MGMT_NONE,
    _EXECUTION_SCHEMA_VERSION,
)

logger = logging.getLogger(__name__)

# Horizon → management profile mapping (research defaults)
_HORIZON_MGMT = {
    SCALP: MGMT_FAST,
    INTRADAY: MGMT_TRAIL_BREAKEVEN,
    EXTENDED: MGMT_STRUCTURAL,
}


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════


def build_execution_assessment(
    market_context: V3MarketContext,
    opportunity: OpportunityAssessment,
    horizon: HorizonAssessment,
    entry: EntryAssessment,
    risk: RiskAssessment,
    *,
    bid: float = 0.0,
    ask: float = 0.0,
) -> ExecutionAssessment:
    """
    Assemble the final execution record from all upstream assessments.

    No filtering logic. Simply records how the decision would be expressed.
    """
    symbol = market_context.symbol
    timestamp = market_context.timestamp_utc
    direction = entry.direction
    pip_size = 0.01 if "JPY" in symbol.upper() else 0.0001

    # ─── Execution state (can we simulate this?) ──────────────────────
    exec_state = _determine_state(opportunity, horizon, entry, risk)

    # ─── Prices ───────────────────────────────────────────────────────
    if direction == "BULLISH" and ask > 0:
        entry_price = ask
    elif direction == "BEARISH" and bid > 0:
        entry_price = bid
    else:
        entry_price = (bid + ask) / 2 if bid > 0 else 0.0

    # Stop and target from risk assessment
    stop_price = 0.0
    target_price = 0.0
    if entry_price > 0 and risk.stop_distance_pips > 0:
        stop_dist = risk.stop_distance_pips * pip_size
        target_dist = risk.target_distance_pips * pip_size
        if direction == "BULLISH":
            stop_price = entry_price - stop_dist
            target_price = entry_price + target_dist
        elif direction == "BEARISH":
            stop_price = entry_price + stop_dist
            target_price = entry_price - target_dist

    # ─── Spread and cost ──────────────────────────────────────────────
    spread_pips = abs(ask - bid) / pip_size if bid > 0 and ask > 0 else 0.0
    slippage_estimate = 0.2  # Research default: 0.2 pip slippage assumption
    total_cost = spread_pips + slippage_estimate

    # ─── Management ───────────────────────────────────────────────────
    mgmt = _HORIZON_MGMT.get(horizon.selected_horizon, MGMT_NONE)

    # ─── Position size (research placeholder: 0.01 lots) ──────────────
    lot_size = 0.01  # Minimum research default

    # ─── Evidence ─────────────────────────────────────────────────────
    factors: list[str] = []
    conflicts: list[str] = []

    if exec_state == READY_FOR_EXECUTION:
        factors.append(f"Direction: {direction}")
        factors.append(f"Horizon: {horizon.selected_horizon}")
        factors.append(f"Risk: {risk.risk_state}")
        factors.append(f"Entry: {entry.entry_state}")
    if exec_state == EXECUTION_CONSTRAINED:
        if risk.risk_state != ACCEPTABLE_RISK:
            conflicts.append(f"Risk state: {risk.risk_state}")
        if entry.entry_state in (WEAK_ENTRY_CONFIRMATION, NO_ENTRY_CONFIRMATION):
            conflicts.append(f"Entry weak: {entry.entry_state}")
    if exec_state == NOT_EXECUTABLE:
        conflicts.append(f"Missing: opp={opportunity.assessment_state} hor={horizon.selected_horizon}")

    # ─── Observations ─────────────────────────────────────────────────
    observations = [
        f"Execution: {exec_state}",
        f"Direction: {direction}",
        f"Entry: {entry_price:.5f} Stop: {stop_price:.5f} Target: {target_price:.5f}",
        f"Spread: {spread_pips:.1f} pips, Total cost: {total_cost:.1f} pips",
        f"Management: {mgmt}",
    ]

    return ExecutionAssessment(
        symbol=symbol,
        timestamp_utc=timestamp,
        schema_version=_EXECUTION_SCHEMA_VERSION,
        direction=direction,
        execution_state=exec_state,
        order_type=ORDER_MARKET,
        entry_price=entry_price,
        stop_price=stop_price,
        target_price=target_price,
        position_size_lots=lot_size,
        horizon=horizon.selected_horizon,
        risk_state=risk.risk_state,
        entry_state=entry.entry_state,
        opportunity_state=opportunity.assessment_state,
        spread_at_entry=round(spread_pips, 4),
        estimated_slippage=slippage_estimate,
        total_entry_cost_pips=round(total_cost, 4),
        management_profile=mgmt,
        supporting_factors=factors,
        conflicting_factors=conflicts,
        observations=observations,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# INTERNAL
# ═══════════════════════════════════════════════════════════════════════════════


def _determine_state(
    opp: OpportunityAssessment,
    horizon: HorizonAssessment,
    entry: EntryAssessment,
    risk: RiskAssessment,
) -> str:
    """Determine execution state from upstream assessments."""
    # Not executable: missing critical upstream
    if horizon.selected_horizon == NO_HORIZON:
        return NOT_EXECUTABLE
    if opp.assessment_state in (INSUFFICIENT_CONTEXT, LOW_QUALITY_CONTEXT):
        return NOT_EXECUTABLE
    if entry.entry_state == INSUFFICIENT_ENTRY_DATA:
        return NOT_EXECUTABLE
    if risk.risk_state == INSUFFICIENT_RISK_DATA:
        return NOT_EXECUTABLE

    # Ready: all upstream positive
    if (entry.entry_state == VALID_ENTRY_CONFIRMATION and
            risk.risk_state == ACCEPTABLE_RISK):
        return READY_FOR_EXECUTION

    # Constrained: some issues but partially valid
    if entry.entry_state in (VALID_ENTRY_CONFIRMATION, WEAK_ENTRY_CONFIRMATION):
        if risk.risk_state in (ACCEPTABLE_RISK, MARGINAL_RISK):
            return EXECUTION_CONSTRAINED

    # Simulated: we can record it but wouldn't actually execute
    if risk.risk_state == POOR_RISK:
        return SIMULATED_ONLY
    if entry.entry_state == NO_ENTRY_CONFIRMATION:
        return SIMULATED_ONLY

    return SIMULATED_ONLY
