"""V10 Pipeline Orchestrator — Single entry point for the V10 decision architecture.

Connects all V10 layers in sequence:
  MarketState → Opportunity → Strategy → Horizon → Entry → Risk → Execution

Exposes one public method:
  process(understanding, context, account, broker) → ExecutionDecision

Does not modify existing engine. Does not change persistence or MT5 execution.
Designed to be called by live_scanner as an alternative to the current engine.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.v3_shadow.models import MarketUnderstanding
from core.v3_shadow.context_models import V3MarketContext
from core.v10.market_state import V10MarketState
from core.v10.market_state_builder import build_v10_market_state
from core.v10.opportunity_assessment import OpportunityAssessment
from core.v10.opportunity_engine import assess_opportunity
from core.v10.strategy_family import StrategyDecision, StrategyFamily
from core.v10.strategy_engine import select_strategy
from core.v10.horizon_assessment import HorizonDecision
from core.v10.horizon_engine import assess_horizon
from core.v10.entry_model import EntryDecision, EntryStatus
from core.v10.entry_engine import build_entry_decision
from core.v10.risk_model import RiskDecision, AccountContext
from core.v10.risk_engine import assess_risk
from core.v10.execution_model import ExecutionDecision
from core.v10.execution_engine import build_execution_decision
from core.v10.broker_context import BrokerContext
from core.v10.decision_context import V10DecisionContext
from core.v10.pipeline_events import PipelineEventCollector


@dataclass
class PipelineResult:
    """Complete pipeline output — all intermediate decisions available for research."""
    market_state: V10MarketState
    opportunity: OpportunityAssessment
    strategy: StrategyDecision
    horizon: HorizonDecision
    entry: EntryDecision
    risk: RiskDecision
    execution: ExecutionDecision
    decision_context: V10DecisionContext | None = None
    account_snapshot: AccountContext | None = None
    broker_snapshot: BrokerContext | None = None
    events: PipelineEventCollector | None = None

    @property
    def approved(self) -> bool:
        return self.execution.approved

    @property
    def rejection_stage(self) -> str:
        """Which stage stopped the pipeline (empty if approved)."""
        if self.opportunity.opportunity_state == "INVALID":
            return "opportunity"
        if self.strategy.strategy_family == StrategyFamily.NONE.value:
            return "strategy"
        if self.entry.entry_status == EntryStatus.INVALID.value:
            return "entry"
        if not self.risk.approved:
            return "risk"
        if not self.execution.approved:
            return "execution"
        return ""


class V10Pipeline:
    """
    V10 Decision Pipeline — orchestrates the full decision flow.

    Usage:
        pipeline = V10Pipeline()
        result = pipeline.process(understanding, context, account, broker)
        if result.approved:
            # Send result.execution to broker adapter
    """

    def process(
        self,
        understanding: MarketUnderstanding,
        context: V3MarketContext | None = None,
        account: AccountContext | None = None,
        broker: BrokerContext | None = None,
    ) -> PipelineResult:
        """
        Run the full V10 decision pipeline.

        Args:
            understanding: V3 MarketUnderstanding (required)
            context: V3MarketContext (optional, enhances regime/location data)
            account: Account state for risk sizing (uses defaults if None)
            broker: Broker state for execution gating (uses defaults if None)

        Returns:
            PipelineResult with all intermediate decisions
        """
        # Defaults
        if account is None:
            account = AccountContext()
        if broker is None:
            broker = BrokerContext()

        # ─── LAYER 1: Market State ────────────────────────────
        market_state = build_v10_market_state(understanding, context)
        ctx = V10DecisionContext.empty(understanding.symbol, understanding.timestamp_utc)
        ctx = ctx.with_market_state(market_state)
        events = PipelineEventCollector(
            observation_id="",  # Set after opportunity generates it
            symbol=understanding.symbol,
            timestamp_utc=understanding.timestamp_utc,
        )
        events.emit("V10_MARKET_STATE_COMPLETE")

        # ─── LAYER 2: Opportunity Assessment ──────────────────
        opportunity = assess_opportunity(market_state)

        # Persist opportunity at detection boundary — BEFORE any downstream
        # strategy/horizon/entry/risk/execution decisions. Ensures the
        # opportunity record exists regardless of whether the pipeline
        # eventually results in EXECUTE or NO_TRADE.
        try:
            from core.persistence.opportunity_writer import persist_opportunity_from_v10
            persist_opportunity_from_v10(
                opportunity=opportunity,
                market_state=market_state,
                bid=0.0,   # bid/ask not available in pure V10 pipeline path;
                ask=0.0,   # they are only available when called from live_scanner
            )
        except Exception:
            pass  # Opportunity persistence must never block the pipeline

        ctx = ctx.with_opportunity(opportunity)
        events.observation_id = opportunity.observation_id
        events.emit("V10_OPPORTUNITY_COMPLETE", 
                    status="COMPLETE" if opportunity.opportunity_state != "INVALID" else "REJECTED",
                    payload={"state": opportunity.opportunity_state})

        # ─── LAYER 3: Strategy Selection ──────────────────────
        # Build lineage context for strategy candidate persistence.
        # The canonical_opportunity_id is computed consistently with the
        # opportunity dataset (opportunity_type as pattern). This is
        # observational only — never affects selection.
        _strategy_lineage: dict | None = None
        try:
            from core.identity.canonical import (
                make_canonical_opportunity_id,
                mint_observation_id,
            )
            _v10_pattern = opportunity.opportunity_type or "NONE"
            _strategy_lineage = {
                "canonical_opportunity_id": make_canonical_opportunity_id(
                    symbol=market_state.symbol,
                    bar_time=market_state.timestamp_utc,
                    pattern=_v10_pattern,
                ),
                "observation_id": mint_observation_id(
                    symbol=market_state.symbol,
                    bar_time=market_state.timestamp_utc,
                    timeframe="M5",
                ),
            }
        except Exception:
            _strategy_lineage = None

        strategy = select_strategy(market_state, opportunity, lineage=_strategy_lineage)
        ctx = ctx.with_strategy(strategy)
        events.emit("V10_STRATEGY_COMPLETE",
                    status="COMPLETE" if strategy.strategy_family != StrategyFamily.NONE.value else "REJECTED",
                    payload={"family": strategy.strategy_family})

        # ─── LAYER 4: Horizon Assessment ──────────────────────
        horizon = assess_horizon(market_state, opportunity, strategy)
        ctx = ctx.with_horizon(horizon)
        events.emit("V10_HORIZON_COMPLETE", payload={"type": horizon.horizon_type})

        # ─── LAYER 5: Entry Construction ──────────────────────
        entry = build_entry_decision(market_state, opportunity, strategy, horizon)
        ctx = ctx.with_entry(entry)
        events.emit("V10_ENTRY_COMPLETE",
                    status="COMPLETE" if entry.entry_status != EntryStatus.INVALID.value else "REJECTED",
                    payload={"status": entry.entry_status})

        # ─── LAYER 6: Risk Assessment ────────────────────────
        risk = assess_risk(market_state, opportunity, strategy, horizon, entry, account, broker)
        ctx = ctx.with_risk(risk)
        events.emit("V10_RISK_COMPLETE",
                    status="COMPLETE" if risk.approved else "REJECTED",
                    payload={"approved": risk.approved})

        # ─── LAYER 7: Execution Decision ─────────────────────
        execution = build_execution_decision(entry, risk, market_state, broker)
        ctx = ctx.with_execution(execution)
        events.emit("V10_EXECUTION_COMPLETE",
                    status="COMPLETE" if execution.approved else "REJECTED",
                    payload={"approved": execution.approved})

        # ─── FINAL: Decision Complete ─────────────────────────
        events.emit("V10_DECISION_COMPLETE",
                    status="EXECUTE" if execution.approved else "NO_TRADE")

        return PipelineResult(
            market_state=market_state,
            opportunity=opportunity,
            strategy=strategy,
            horizon=horizon,
            entry=entry,
            risk=risk,
            execution=execution,
            decision_context=ctx,
            account_snapshot=account,
            broker_snapshot=broker,
            events=events,
        )
