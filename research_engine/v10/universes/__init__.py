"""
New-Engine Research Universes.

This package contains:
    - models.py: Data models for questions, angles, populations, and joins
    - question_bank.py: The single canonical question registry for the new engine
    - base.py: Abstract base class for universe builders
    - execution_universe.py: Execution Universe builder (wraps existing data)
    - decision_universe.py: Decision Universe builder (from decision_trace logs)
    - market_universe.py: Market Universe builder (from v10_market_state + market_context)
    - strategy_universe.py: Strategy Universe builder (from v10_strategy + strategy_observations)
    - risk_universe.py: Risk Universe builder (from v10_risk in decision traces)
    - outcome_universe.py: Outcome Universe builder (realised results from completed executions)
"""

from research_engine.v10.universes.base import UniverseBuilder, UniverseMetadata
from research_engine.v10.universes.execution_universe import ExecutionUniverseBuilder
from research_engine.v10.universes.decision_universe import DecisionUniverseBuilder
from research_engine.v10.universes.market_universe import MarketUniverseBuilder
from research_engine.v10.universes.strategy_universe import StrategyUniverseBuilder
from research_engine.v10.universes.risk_universe import RiskUniverseBuilder
from research_engine.v10.universes.outcome_universe import OutcomeUniverseBuilder

__all__ = [
    "UniverseBuilder",
    "UniverseMetadata",
    "ExecutionUniverseBuilder",
    "DecisionUniverseBuilder",
    "MarketUniverseBuilder",
    "StrategyUniverseBuilder",
    "RiskUniverseBuilder",
    "OutcomeUniverseBuilder",
]
