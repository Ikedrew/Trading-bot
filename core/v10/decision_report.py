"""V10 Decision Report — Human-readable output of the V10 pipeline decision.

Replaces legacy Composite Score / Grade / Threshold display.
Produces structured text showing each V10 layer's contribution
from the V10DecisionContext reasoning chain.
"""

from __future__ import annotations

from core.v10.pipeline import PipelineResult
from core.v10.decision_context import V10DecisionContext


def format_v10_decision(result: PipelineResult) -> str:
    """Format a V10 PipelineResult into a human-readable decision report."""
    ctx = result.decision_context
    if ctx:
        return format_from_context(ctx)
    # Fallback: build from PipelineResult directly
    return _format_from_result(result)


def format_from_context(ctx: V10DecisionContext) -> str:
    """Format directly from V10DecisionContext — preferred path."""
    lines: list[str] = []

    lines.append("")
    lines.append(f"[V10 MARKET UNDERSTANDING]")
    lines.append(f"  Symbol: {ctx.symbol}")
    if ctx.market_state:
        s = ctx.market_state
        lines.append(f"  H4: Trend={s.h4.trend or '—'}  Phase={s.h4.market_phase or '—'}  Volatility={s.regime.volatility_state or '—'}")
        lines.append(f"  H1: Structure={s.h1.dominant_trend or '—'}  BOS={s.h1.bos_direction if s.h1.bos_confirmed else '—'}  CHoCH={s.h1.choch_direction if s.h1.choch_detected else '—'}")
        lines.append(f"  M15: Formation={'displacement' if s.m15.displacement_present else 'pullback' if s.m15.pullback_active else '—'}  Zone={s.location.location_type or '—'}")
        lines.append(f"  Location: {s.location.premium_discount or '—'}  Inside zone={s.location.inside_institutional_zone}")
        lines.append(f"  Liquidity: above={s.location.liquidity_above}  below={s.location.liquidity_below}")

    lines.append("")
    lines.append(f"[V10 OPPORTUNITY]")
    if ctx.opportunity:
        o = ctx.opportunity
        lines.append(f"  State: {o.opportunity_state}")
        lines.append(f"  Direction: {o.directional_bias or '—'}")
        lines.append(f"  Type: {o.opportunity_type or '—'}")
        lines.append(f"  Quality: {o.quality.overall_quality:.2f} (loc={o.quality.location_score:.2f} str={o.quality.structure_score:.2f} beh={o.quality.behaviour_score:.2f} form={o.quality.formation_score:.2f})")
        if o.reasoning:
            for r in o.reasoning[:3]:
                lines.append(f"    - {r}")
    else:
        lines.append(f"  Not evaluated")

    lines.append("")
    lines.append(f"[V10 STRATEGY]")
    if ctx.strategy:
        st = ctx.strategy
        lines.append(f"  Selected: {st.strategy_family}")
        lines.append(f"  Confidence: {st.strategy_confidence:.2f}")
        if st.reasoning:
            for r in st.reasoning[:3]:
                lines.append(f"    - {r}")
        if st.supporting_conditions:
            met = [k for k, v in st.supporting_conditions.items() if v]
            missed = [k for k, v in st.supporting_conditions.items() if not v]
            if met:
                lines.append(f"  Conditions met: {', '.join(met)}")
            if missed:
                lines.append(f"  Conditions missed: {', '.join(missed)}")
    else:
        lines.append(f"  Not evaluated")

    lines.append("")
    lines.append(f"[V10 HORIZON]")
    if ctx.horizon:
        h = ctx.horizon
        lines.append(f"  Type: {h.horizon_type}")
        lines.append(f"  Expected: {h.movement_expectation.minimum_expected_move:.0f}-{h.movement_expectation.maximum_expected_move:.0f} {h.movement_expectation.measurement_unit}")
    else:
        lines.append(f"  Not evaluated")

    lines.append("")
    lines.append(f"[V10 ENTRY]")
    if ctx.entry:
        e = ctx.entry
        lines.append(f"  Method: {e.entry_method}")
        lines.append(f"  Status: {e.entry_status}")
        lines.append(f"  Entry: {e.entry_price:.5f}")
        lines.append(f"  Stop: {e.stop_reference.price:.5f} ({e.stop_reference.reasoning})")
        lines.append(f"  Target: {e.target_reference.price:.5f} ({e.target_reference.reasoning})")
        lines.append(f"  R:R = {e.expected_rr:.2f}")
    else:
        lines.append(f"  Not evaluated")

    lines.append("")
    lines.append(f"[V10 RISK]")
    if ctx.risk:
        rk = ctx.risk
        lines.append(f"  Approved: {'YES' if rk.approved else 'NO'}")
        if rk.approved:
            lines.append(f"  Risk: {rk.risk_profile.risk_percentage:.2%}  Size: {rk.risk_profile.position_size:.4f}")
            lines.append(f"  R:R: {rk.trade_geometry.expected_rr:.2f}")
        else:
            lines.append(f"  Reason: {rk.rejection_reason}")
    else:
        lines.append(f"  Not evaluated")

    lines.append("")
    lines.append(f"[V10 EXECUTION]")
    if ctx.execution:
        ex = ctx.execution
        lines.append(f"  Approved: {'YES' if ex.approved else 'NO'}")
        if ex.approved:
            lines.append(f"  Order: {ex.order_details.order_type} {ex.order_details.direction} {ex.order_details.volume:.4f}")
            checks_passed = [k for k, v in ex.execution_checks.items() if v]
            lines.append(f"  Checks: {', '.join(checks_passed)}")
        else:
            lines.append(f"  Reason: {ex.rejection_reason}")
    else:
        lines.append(f"  Not evaluated")

    lines.append("")
    lines.append(f"[FINAL ACTION]")
    lines.append(f"  {ctx.final_action}")
    if ctx.rejection_stage:
        lines.append(f"  Stopped at: {ctx.rejection_stage}")
    lines.append("")

    return "\n".join(lines)


def _format_from_result(result: PipelineResult) -> str:
    """Fallback: format from PipelineResult when context not available."""
    # Build a minimal context and format from it
    ctx = V10DecisionContext.empty(result.market_state.symbol, result.market_state.timestamp_utc)
    ctx = ctx.with_market_state(result.market_state)
    ctx = ctx.with_opportunity(result.opportunity)
    ctx = ctx.with_strategy(result.strategy)
    ctx = ctx.with_horizon(result.horizon)
    ctx = ctx.with_entry(result.entry)
    ctx = ctx.with_risk(result.risk)
    ctx = ctx.with_execution(result.execution)
    return format_from_context(ctx)
