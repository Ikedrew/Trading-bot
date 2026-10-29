"""V10 Decision Context — Immutable reasoning chain carried through the pipeline.

Each pipeline stage produces a frozen snapshot that is accumulated
into V10DecisionContext. No stage can modify or delete information
from a previous stage.

Usage:
    ctx = V10DecisionContext.empty(symbol, timestamp)
    ctx = ctx.with_market_state(state)
    ctx = ctx.with_opportunity(opp)
    ctx = ctx.with_strategy(strat)
    ...

Each .with_*() returns a NEW immutable instance — the original is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.v10.market_state import V10MarketState
from core.v10.opportunity_assessment import OpportunityAssessment
from core.v10.strategy_family import StrategyDecision, StrategyFamily
from core.v10.horizon_assessment import HorizonDecision
from core.v10.entry_model import EntryDecision, EntryStatus, TradeDirection
from core.v10.risk_model import RiskDecision
from core.v10.execution_model import ExecutionDecision


@dataclass(frozen=True)
class V10DecisionContext:
    """
    Complete immutable reasoning chain for a single V10 pipeline evaluation.

    Every stage adds information. No stage removes or overwrites prior data.
    Terminal reporting, persistence, and research all consume this object.

    No legacy score fields. No composite score. No pattern gate.
    """

    # ─── Identity ─────────────────────────────────────────────
    symbol: str = ""
    timestamp_utc: float = 0.0

    # ─── Stage 1: Market Understanding ────────────────────────
    market_state: V10MarketState | None = None

    # ─── Stage 2: Opportunity Assessment ──────────────────────
    opportunity: OpportunityAssessment | None = None

    # ─── Stage 3: Strategy Assessment ─────────────────────────
    strategy: StrategyDecision | None = None

    # ─── Stage 4: Horizon ─────────────────────────────────────
    horizon: HorizonDecision | None = None

    # ─── Stage 5: Entry ───────────────────────────────────────
    entry: EntryDecision | None = None

    # ─── Stage 6: Risk ────────────────────────────────────────
    risk: RiskDecision | None = None

    # ─── Stage 7: Execution ───────────────────────────────────
    execution: ExecutionDecision | None = None

    # ─── Pipeline metadata ────────────────────────────────────
    completed_stages: tuple[str, ...] = ()
    terminal_stage: str = ""          # Stage where pipeline stopped (if not approved)

    # ─── Builder methods (return NEW instance) ────────────────

    @classmethod
    def empty(cls, symbol: str, timestamp_utc: float) -> V10DecisionContext:
        """Create an empty context at the start of pipeline evaluation."""
        return cls(symbol=symbol, timestamp_utc=timestamp_utc)

    def with_market_state(self, state: V10MarketState) -> V10DecisionContext:
        """Add market understanding — returns new immutable instance."""
        return _replace(self, market_state=state, completed_stages=self.completed_stages + ("market_state",))

    def with_opportunity(self, opp: OpportunityAssessment) -> V10DecisionContext:
        """Add opportunity assessment."""
        ctx = _replace(self, opportunity=opp, completed_stages=self.completed_stages + ("opportunity",))
        if opp.opportunity_state == "INVALID" and not self.terminal_stage:
            ctx = _replace(ctx, terminal_stage="opportunity")
        return ctx

    def with_strategy(self, strat: StrategyDecision) -> V10DecisionContext:
        """Add strategy selection."""
        ctx = _replace(self, strategy=strat, completed_stages=self.completed_stages + ("strategy",))
        if strat.strategy_family == StrategyFamily.NONE.value and not self.terminal_stage:
            ctx = _replace(ctx, terminal_stage="strategy")
        return ctx

    def with_horizon(self, hz: HorizonDecision) -> V10DecisionContext:
        """Add horizon assessment."""
        return _replace(self, horizon=hz, completed_stages=self.completed_stages + ("horizon",))

    def with_entry(self, entry: EntryDecision) -> V10DecisionContext:
        """Add entry decision."""
        ctx = _replace(self, entry=entry, completed_stages=self.completed_stages + ("entry",))
        if entry.entry_status == EntryStatus.INVALID.value and not self.terminal_stage:
            ctx = _replace(ctx, terminal_stage="entry")
        return ctx

    def with_risk(self, risk: RiskDecision) -> V10DecisionContext:
        """Add risk decision."""
        ctx = _replace(self, risk=risk, completed_stages=self.completed_stages + ("risk",))
        if not risk.approved and not self.terminal_stage:
            ctx = _replace(ctx, terminal_stage="risk")
        return ctx

    def with_execution(self, exe: ExecutionDecision) -> V10DecisionContext:
        """Add execution decision."""
        ctx = _replace(self, execution=exe, completed_stages=self.completed_stages + ("execution",))
        if not exe.approved and not self.terminal_stage:
            ctx = _replace(ctx, terminal_stage="execution")
        return ctx

    # ─── Query properties ─────────────────────────────────────

    @property
    def approved(self) -> bool:
        """True if the full pipeline approved execution."""
        return self.execution is not None and self.execution.approved

    @property
    def final_action(self) -> str:
        return "EXECUTE" if self.approved else "NO_TRADE"

    @property
    def rejection_stage(self) -> str:
        """Which stage stopped the pipeline (empty if approved)."""
        return self.terminal_stage

    @property
    def direction(self) -> str:
        """Trade direction (from opportunity/entry or NONE)."""
        if self.entry and self.entry.trade_direction != TradeDirection.NONE.value:
            return self.entry.trade_direction
        if self.opportunity and self.opportunity.directional_bias:
            return self.opportunity.directional_bias
        return "NONE"

    @property
    def strategy_family(self) -> str:
        """Selected strategy family or NONE."""
        if self.strategy:
            return self.strategy.strategy_family
        return StrategyFamily.NONE.value

    @property
    def horizon_type(self) -> str:
        """Selected horizon or empty."""
        if self.horizon:
            return self.horizon.horizon_type
        return ""

    # ─── Serialisation ────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Full serialisation for persistence/research."""
        return {
            "symbol": self.symbol,
            "timestamp_utc": self.timestamp_utc,
            "final_action": self.final_action,
            "rejection_stage": self.rejection_stage,
            "completed_stages": list(self.completed_stages),
            "market_state": self.market_state.to_dict() if self.market_state else None,
            "opportunity": self.opportunity.to_dict() if self.opportunity else None,
            "strategy": self.strategy.to_dict() if self.strategy else None,
            "horizon": self.horizon.to_dict() if self.horizon else None,
            "entry": self.entry.to_dict() if self.entry else None,
            "risk": self.risk.to_dict() if self.risk else None,
            "execution": self.execution.to_dict() if self.execution else None,
        }


def _replace(ctx: V10DecisionContext, **kwargs) -> V10DecisionContext:
    """Create a new frozen instance with specified fields replaced."""
    from dataclasses import fields as dc_fields
    current = {f.name: getattr(ctx, f.name) for f in dc_fields(ctx)}
    current.update(kwargs)
    return V10DecisionContext(**current)
