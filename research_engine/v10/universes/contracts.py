"""
═══════════════════════════════════════════════════════════════════════════════
UNIVERSE & POPULATION CONTRACTS
═══════════════════════════════════════════════════════════════════════════════

Machine-readable contracts defining:
    - What each universe IS (grain, identity, sources)
    - What each population MEANS (filters, required fields)
    - How universes JOIN (keys, cardinality, validation)
    - How semantic fields MAP to source paths

These contracts are the single source of truth for population semantics.
No experiment may interpret a population differently from its contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from research_engine.v10.universes.models import Population, Universe


# ═══════════════════════════════════════════════════════════════════════════════
# CONTRACT DATA MODELS
# ═══════════════════════════════════════════════════════════════════════════════


class Cardinality(str, Enum):
    ONE_TO_ONE = "1:1"
    ONE_TO_MANY = "1:N"
    MANY_TO_ONE = "N:1"
    MANY_TO_MANY = "N:N"
    TEMPORAL_AS_OF = "temporal/as-of"


class FieldType(str, Enum):
    FLOAT = "float"
    INT = "int"
    STRING = "string"
    BOOL = "bool"
    LIST = "list"
    DICT = "dict"
    TIMESTAMP = "timestamp"
    NULLABLE_FLOAT = "nullable_float"
    NULLABLE_STRING = "nullable_string"


class PopulationStatus(str, Enum):
    VALID = "VALID"
    EMPTY = "EMPTY"
    DEGRADED = "DEGRADED"
    INVALID = "INVALID"


@dataclass(frozen=True)
class UniverseContract:
    """Formal contract for a universe."""
    universe_id: Universe
    name: str
    description: str
    grain: str  # What one record represents
    identity_field: str  # Primary identity key
    source_datasets: tuple[str, ...]
    source_schema_versions: tuple[str, ...]
    join_keys: tuple[str, ...]  # Fields available for cross-universe joins
    coverage_fields: tuple[str, ...]  # Fields that define data coverage
    lineage_fields: tuple[str, ...]  # Fields that track provenance

    def to_dict(self) -> dict[str, Any]:
        return {
            "universe_id": self.universe_id.value,
            "name": self.name,
            "description": self.description,
            "grain": self.grain,
            "identity_field": self.identity_field,
            "source_datasets": list(self.source_datasets),
            "source_schema_versions": list(self.source_schema_versions),
            "join_keys": list(self.join_keys),
            "coverage_fields": list(self.coverage_fields),
            "lineage_fields": list(self.lineage_fields),
        }


@dataclass(frozen=True)
class PopulationContract:
    """Formal contract for a named population."""
    population_id: Population
    universe_id: Universe
    name: str
    description: str
    definition: str  # Human-readable filter definition
    filter_field: str  # Which field is filtered
    filter_values: tuple[str, ...]  # Accepted values (empty = all)
    record_grain: str  # Same as parent universe grain
    required_fields: tuple[str, ...]  # Must be non-null
    optional_fields: tuple[str, ...]  # May be null
    join_keys: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "population_id": self.population_id.value,
            "universe_id": self.universe_id.value,
            "name": self.name,
            "description": self.description,
            "definition": self.definition,
            "filter_field": self.filter_field,
            "filter_values": list(self.filter_values),
            "record_grain": self.record_grain,
            "required_fields": list(self.required_fields),
            "optional_fields": list(self.optional_fields),
            "join_keys": list(self.join_keys),
        }


@dataclass(frozen=True)
class JoinContract:
    """Formal contract for a cross-universe join."""
    join_id: str
    left_universe: Universe
    right_universe: Universe
    left_key: str
    right_key: str
    cardinality: Cardinality
    description: str
    temporal_constraint: str  # e.g., "same entity_id implies same event"
    expected_match_rate: float  # 0.0-1.0, expected fraction of left that match

    def to_dict(self) -> dict[str, Any]:
        return {
            "join_id": self.join_id,
            "left_universe": self.left_universe.value,
            "right_universe": self.right_universe.value,
            "left_key": self.left_key,
            "right_key": self.right_key,
            "cardinality": self.cardinality.value,
            "description": self.description,
            "temporal_constraint": self.temporal_constraint,
            "expected_match_rate": self.expected_match_rate,
        }


@dataclass(frozen=True)
class SemanticFieldMapping:
    """Maps a semantic field name to its source path in a universe."""
    semantic_name: str
    universe_id: Universe
    source_path: str  # e.g., "v10_opportunity.overall_quality"
    field_type: FieldType
    nullable: bool
    validation: str  # e.g., "0 <= value <= 1"
    description: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "semantic_name": self.semantic_name,
            "universe_id": self.universe_id.value,
            "source_path": self.source_path,
            "field_type": self.field_type.value,
            "nullable": self.nullable,
            "validation": self.validation,
            "description": self.description,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# UNIVERSE CONTRACTS
# ═══════════════════════════════════════════════════════════════════════════════

EXECUTION_CONTRACT = UniverseContract(
    universe_id=Universe.EXECUTION,
    name="Execution Universe",
    description="Validated trade outcomes. One record = one completed trade with realised P&L.",
    grain="One validated trade (entry → exit → realised outcome)",
    identity_field="trade_id",
    source_datasets=("data/research/research_universe.jsonl",),
    source_schema_versions=("1.0",),
    join_keys=("trade_id", "entity_id", "symbol"),
    coverage_fields=("entry_time", "exit_time", "symbol"),
    lineage_fields=("trade_id", "entity_id"),
)

DECISION_CONTRACT = UniverseContract(
    universe_id=Universe.DECISION,
    name="Decision Universe",
    description="Decision events. One record = one pipeline decision (EXECUTE or NO_TRADE).",
    grain="One decision event at the moment the pipeline decided action",
    identity_field="entity_id",
    source_datasets=("logs/decision_trace/<SYMBOL>/*.jsonl",),
    source_schema_versions=("2.0",),
    join_keys=("entity_id", "correlation_id", "decision_id", "symbol", "cycle_id"),
    coverage_fields=("timestamp_utc", "symbol"),
    lineage_fields=("entity_id", "decision_id", "correlation_id"),
)

MARKET_CONTRACT = UniverseContract(
    universe_id=Universe.MARKET,
    name="Market Universe",
    description="Market state observations at decision time. One record = one market snapshot.",
    grain="One market-state observation tied to a decision/opportunity event",
    identity_field="entity_id",
    source_datasets=(
        "logs/decision_trace/<SYMBOL>/*.jsonl (v10_market_state)",
        "logs/market_context/<SYMBOL>/*.jsonl",
    ),
    source_schema_versions=("2.0", "1.0"),
    join_keys=("entity_id", "symbol", "cycle_id"),
    coverage_fields=("timestamp_utc", "symbol"),
    lineage_fields=("entity_id", "source"),
)

STRATEGY_CONTRACT = UniverseContract(
    universe_id=Universe.STRATEGY,
    name="Strategy Universe",
    description="Strategy evaluations. One record = one strategy assessment for an opportunity.",
    grain="One strategy evaluation/selection event for an opportunity",
    identity_field="entity_id",
    source_datasets=(
        "logs/decision_trace/<SYMBOL>/*.jsonl (v10_strategy)",
        "logs/strategy_observations/<SYMBOL>/*.jsonl",
    ),
    source_schema_versions=("2.0", "1.0"),
    join_keys=("entity_id", "correlation_id", "symbol", "cycle_id"),
    coverage_fields=("timestamp_utc", "symbol"),
    lineage_fields=("entity_id", "source"),
)

RISK_CONTRACT = UniverseContract(
    universe_id=Universe.RISK,
    name="Risk Universe",
    description="Risk evaluations. One record = one risk-control assessment for a proposed trade.",
    grain="One risk evaluation event (risk mechanism assessed whether trade satisfies constraints)",
    identity_field="entity_id",
    source_datasets=(
        "logs/decision_trace/<SYMBOL>/*.jsonl (v10_risk)",
    ),
    source_schema_versions=("2.0",),
    join_keys=("entity_id", "correlation_id", "symbol", "cycle_id"),
    coverage_fields=("timestamp_utc", "symbol"),
    lineage_fields=("entity_id",),
)

OUTCOME_CONTRACT = UniverseContract(
    universe_id=Universe.OUTCOME,
    name="Outcome Universe",
    description="Realised economic results. One record = one completed trade with realised R-multiple.",
    grain="One completed trade with realised economic outcome",
    identity_field="entity_id",
    source_datasets=(
        "data/research/research_universe.jsonl (via ExecutionUniverseBuilder)",
    ),
    source_schema_versions=("1.0",),
    join_keys=("entity_id", "trade_id", "symbol"),
    coverage_fields=("entry_time", "exit_time", "symbol"),
    lineage_fields=("entity_id", "trade_id"),
)

UNIVERSE_CONTRACTS: dict[Universe, UniverseContract] = {
    Universe.EXECUTION: EXECUTION_CONTRACT,
    Universe.DECISION: DECISION_CONTRACT,
    Universe.MARKET: MARKET_CONTRACT,
    Universe.STRATEGY: STRATEGY_CONTRACT,
    Universe.RISK: RISK_CONTRACT,
    Universe.OUTCOME: OUTCOME_CONTRACT,
}


# ═══════════════════════════════════════════════════════════════════════════════
# POPULATION CONTRACTS
# ═══════════════════════════════════════════════════════════════════════════════

_EXEC_GRAIN = "One validated trade"
_DEC_GRAIN = "One decision event"
_MKT_GRAIN = "One market-state observation"
_STRAT_GRAIN = "One strategy evaluation"

POPULATION_CONTRACTS: dict[Population, PopulationContract] = {
    # ─── Execution Populations ────────────────────────────────────────────────
    Population.ALL_TRADES: PopulationContract(
        population_id=Population.ALL_TRADES,
        universe_id=Universe.EXECUTION,
        name="All Trades",
        description="Every validated trade in the execution universe",
        definition="No filter — all records",
        filter_field="",
        filter_values=(),
        record_grain=_EXEC_GRAIN,
        required_fields=("trade_id", "r_multiple", "symbol", "entry_time"),
        optional_fields=("ev", "score", "regime"),
        join_keys=("trade_id", "entity_id"),
    ),
    Population.WINNING_TRADES: PopulationContract(
        population_id=Population.WINNING_TRADES,
        universe_id=Universe.EXECUTION,
        name="Winning Trades",
        description="Trades with positive R-multiple (r_multiple > 0)",
        definition="r_multiple > 0",
        filter_field="r_multiple",
        filter_values=(),
        record_grain=_EXEC_GRAIN,
        required_fields=("trade_id", "r_multiple"),
        optional_fields=(),
        join_keys=("trade_id", "entity_id"),
    ),
    Population.LOSING_TRADES: PopulationContract(
        population_id=Population.LOSING_TRADES,
        universe_id=Universe.EXECUTION,
        name="Losing Trades",
        description="Trades with non-positive R-multiple (r_multiple <= 0)",
        definition="r_multiple <= 0",
        filter_field="r_multiple",
        filter_values=(),
        record_grain=_EXEC_GRAIN,
        required_fields=("trade_id", "r_multiple"),
        optional_fields=(),
        join_keys=("trade_id", "entity_id"),
    ),
    Population.ANOMALOUS_TRADES: PopulationContract(
        population_id=Population.ANOMALOUS_TRADES,
        universe_id=Universe.EXECUTION,
        name="Anomalous Trades",
        description="Trades flagged as anomalous by the governance layer",
        definition="anomaly == True",
        filter_field="anomaly",
        filter_values=("True",),
        record_grain=_EXEC_GRAIN,
        required_fields=("trade_id", "r_multiple", "anomaly"),
        optional_fields=("anomaly_reasons",),
        join_keys=("trade_id", "entity_id"),
    ),
    # ─── Decision Populations ─────────────────────────────────────────────────
    Population.ALL_DECISIONS: PopulationContract(
        population_id=Population.ALL_DECISIONS,
        universe_id=Universe.DECISION,
        name="All Decisions",
        description="Every decision event (EXECUTE + NO_TRADE)",
        definition="No filter — all normalised records with entity_id",
        filter_field="",
        filter_values=(),
        record_grain=_DEC_GRAIN,
        required_fields=("entity_id", "action", "timestamp_utc"),
        optional_fields=("score", "ev"),
        join_keys=("entity_id", "correlation_id"),
    ),
    Population.EXECUTE_DECISIONS: PopulationContract(
        population_id=Population.EXECUTE_DECISIONS,
        universe_id=Universe.DECISION,
        name="Execute Decisions",
        description="Decisions where the pipeline approved execution",
        definition="action == 'EXECUTE'",
        filter_field="action",
        filter_values=("EXECUTE",),
        record_grain=_DEC_GRAIN,
        required_fields=("entity_id", "action", "score"),
        optional_fields=("ev", "r_multiple"),
        join_keys=("entity_id", "correlation_id"),
    ),
    Population.NO_TRADE_DECISIONS: PopulationContract(
        population_id=Population.NO_TRADE_DECISIONS,
        universe_id=Universe.DECISION,
        name="No-Trade Decisions",
        description="Decisions where the pipeline rejected the opportunity",
        definition="action == 'NO_TRADE'",
        filter_field="action",
        filter_values=("NO_TRADE",),
        record_grain=_DEC_GRAIN,
        required_fields=("entity_id", "action", "terminal_reason"),
        optional_fields=("score",),
        join_keys=("entity_id",),
    ),
    Population.REJECTED_AT_OPPORTUNITY: PopulationContract(
        population_id=Population.REJECTED_AT_OPPORTUNITY,
        universe_id=Universe.DECISION,
        name="Rejected at Opportunity Stage",
        description="NO_TRADE decisions where terminal_reason contains 'opportunity'",
        definition="action=='NO_TRADE' AND 'opportunity' in terminal_reason.lower()",
        filter_field="terminal_reason",
        filter_values=("opportunity",),
        record_grain=_DEC_GRAIN,
        required_fields=("entity_id", "terminal_reason"),
        optional_fields=(),
        join_keys=("entity_id",),
    ),
    Population.REJECTED_AT_STRATEGY: PopulationContract(
        population_id=Population.REJECTED_AT_STRATEGY,
        universe_id=Universe.DECISION,
        name="Rejected at Strategy Stage",
        description="NO_TRADE decisions where terminal_reason contains 'strategy'",
        definition="action=='NO_TRADE' AND 'strategy' in terminal_reason.lower()",
        filter_field="terminal_reason",
        filter_values=("strategy",),
        record_grain=_DEC_GRAIN,
        required_fields=("entity_id", "terminal_reason"),
        optional_fields=(),
        join_keys=("entity_id",),
    ),
    Population.REJECTED_AT_ENTRY: PopulationContract(
        population_id=Population.REJECTED_AT_ENTRY,
        universe_id=Universe.DECISION,
        name="Rejected at Entry Stage",
        description="NO_TRADE decisions where terminal_reason contains 'entry'",
        definition="action=='NO_TRADE' AND 'entry' in terminal_reason.lower()",
        filter_field="terminal_reason",
        filter_values=("entry",),
        record_grain=_DEC_GRAIN,
        required_fields=("entity_id", "terminal_reason"),
        optional_fields=(),
        join_keys=("entity_id",),
    ),
    Population.REJECTED_AT_RISK: PopulationContract(
        population_id=Population.REJECTED_AT_RISK,
        universe_id=Universe.DECISION,
        name="Rejected at Risk Stage",
        description="NO_TRADE decisions where terminal_reason contains 'risk'",
        definition="action=='NO_TRADE' AND 'risk' in terminal_reason.lower()",
        filter_field="terminal_reason",
        filter_values=("risk",),
        record_grain=_DEC_GRAIN,
        required_fields=("entity_id", "terminal_reason"),
        optional_fields=("ev",),
        join_keys=("entity_id",),
    ),
    Population.REJECTED_AT_EXECUTION: PopulationContract(
        population_id=Population.REJECTED_AT_EXECUTION,
        universe_id=Universe.DECISION,
        name="Rejected at Execution Stage",
        description="NO_TRADE decisions rejected at execution layer (not entry)",
        definition="action=='NO_TRADE' AND 'exec' in reason AND 'entry' not in reason",
        filter_field="terminal_reason",
        filter_values=("exec",),
        record_grain=_DEC_GRAIN,
        required_fields=("entity_id", "terminal_reason"),
        optional_fields=(),
        join_keys=("entity_id",),
    ),
    Population.HIGH_SCORE_DECISIONS: PopulationContract(
        population_id=Population.HIGH_SCORE_DECISIONS,
        universe_id=Universe.DECISION,
        name="High Score Decisions",
        description="Decisions with score >= 70",
        definition="score >= 70",
        filter_field="score",
        filter_values=(),
        record_grain=_DEC_GRAIN,
        required_fields=("entity_id", "score"),
        optional_fields=(),
        join_keys=("entity_id",),
    ),
    Population.LOW_SCORE_DECISIONS: PopulationContract(
        population_id=Population.LOW_SCORE_DECISIONS,
        universe_id=Universe.DECISION,
        name="Low Score Decisions",
        description="Decisions with score < 50 (and score is not null)",
        definition="score < 50 AND score IS NOT NULL",
        filter_field="score",
        filter_values=(),
        record_grain=_DEC_GRAIN,
        required_fields=("entity_id", "score"),
        optional_fields=(),
        join_keys=("entity_id",),
    ),
    # ─── Market Populations ───────────────────────────────────────────────────
    Population.ALL_MARKET_STATES: PopulationContract(
        population_id=Population.ALL_MARKET_STATES,
        universe_id=Universe.MARKET,
        name="All Market States",
        description="Every market-state observation",
        definition="No filter — all normalised market records",
        filter_field="",
        filter_values=(),
        record_grain=_MKT_GRAIN,
        required_fields=("entity_id", "regime", "symbol"),
        optional_fields=("volatility_state", "h1_structural_clarity"),
        join_keys=("entity_id", "symbol", "cycle_id"),
    ),
    Population.TRENDING_REGIME: PopulationContract(
        population_id=Population.TRENDING_REGIME,
        universe_id=Universe.MARKET,
        name="Trending Regime",
        description="Market states classified as TRENDING",
        definition="regime == 'TRENDING'",
        filter_field="regime",
        filter_values=("TRENDING",),
        record_grain=_MKT_GRAIN,
        required_fields=("entity_id", "regime"),
        optional_fields=(),
        join_keys=("entity_id",),
    ),
    Population.RANGING_REGIME: PopulationContract(
        population_id=Population.RANGING_REGIME,
        universe_id=Universe.MARKET,
        name="Ranging Regime",
        description="Market states classified as RANGING",
        definition="regime == 'RANGING'",
        filter_field="regime",
        filter_values=("RANGING",),
        record_grain=_MKT_GRAIN,
        required_fields=("entity_id", "regime"),
        optional_fields=(),
        join_keys=("entity_id",),
    ),
    Population.TRANSITIONAL_REGIME: PopulationContract(
        population_id=Population.TRANSITIONAL_REGIME,
        universe_id=Universe.MARKET,
        name="Transitional Regime",
        description="Market states classified as TRANSITIONAL",
        definition="regime == 'TRANSITIONAL'",
        filter_field="regime",
        filter_values=("TRANSITIONAL",),
        record_grain=_MKT_GRAIN,
        required_fields=("entity_id", "regime"),
        optional_fields=(),
        join_keys=("entity_id",),
    ),
    Population.HIGH_VOLATILITY: PopulationContract(
        population_id=Population.HIGH_VOLATILITY,
        universe_id=Universe.MARKET,
        name="High Volatility",
        description="Market states with volatility_state in (HIGH, EXPANDING, EXPANSION)",
        definition="volatility_state IN ('HIGH', 'EXPANDING', 'EXPANSION')",
        filter_field="volatility_state",
        filter_values=("HIGH", "EXPANDING", "EXPANSION"),
        record_grain=_MKT_GRAIN,
        required_fields=("entity_id", "volatility_state"),
        optional_fields=(),
        join_keys=("entity_id",),
    ),
    Population.LOW_VOLATILITY: PopulationContract(
        population_id=Population.LOW_VOLATILITY,
        universe_id=Universe.MARKET,
        name="Low Volatility",
        description="Market states with volatility_state in (LOW, CONTRACTING, CONTRACTION)",
        definition="volatility_state IN ('LOW', 'CONTRACTING', 'CONTRACTION')",
        filter_field="volatility_state",
        filter_values=("LOW", "CONTRACTING", "CONTRACTION"),
        record_grain=_MKT_GRAIN,
        required_fields=("entity_id", "volatility_state"),
        optional_fields=(),
        join_keys=("entity_id",),
    ),
    Population.SESSION_LONDON: PopulationContract(
        population_id=Population.SESSION_LONDON,
        universe_id=Universe.MARKET,
        name="London Session",
        description="Market states during London trading session (07-16 UTC)",
        definition="session == 'LONDON'",
        filter_field="session",
        filter_values=("LONDON",),
        record_grain=_MKT_GRAIN,
        required_fields=("entity_id",),
        optional_fields=("session",),
        join_keys=("entity_id",),
    ),
    Population.SESSION_NY: PopulationContract(
        population_id=Population.SESSION_NY,
        universe_id=Universe.MARKET,
        name="New York Session",
        description="Market states during New York trading session (12-21 UTC)",
        definition="session == 'NEW_YORK'",
        filter_field="session",
        filter_values=("NEW_YORK",),
        record_grain=_MKT_GRAIN,
        required_fields=("entity_id",),
        optional_fields=("session",),
        join_keys=("entity_id",),
    ),
    Population.SESSION_ASIA: PopulationContract(
        population_id=Population.SESSION_ASIA,
        universe_id=Universe.MARKET,
        name="Asia Session",
        description="Market states during Asia trading session (21-07 UTC)",
        definition="session == 'ASIA'",
        filter_field="session",
        filter_values=("ASIA",),
        record_grain=_MKT_GRAIN,
        required_fields=("entity_id",),
        optional_fields=("session",),
        join_keys=("entity_id",),
    ),
    # ─── Strategy Populations ─────────────────────────────────────────────────
    Population.ALL_STRATEGIES: PopulationContract(
        population_id=Population.ALL_STRATEGIES,
        universe_id=Universe.STRATEGY,
        name="All Strategies",
        description="Every strategy evaluation record",
        definition="No filter — all normalised strategy records",
        filter_field="",
        filter_values=(),
        record_grain=_STRAT_GRAIN,
        required_fields=("entity_id", "family", "symbol"),
        optional_fields=("confidence", "conditions_met"),
        join_keys=("entity_id", "symbol", "cycle_id"),
    ),
    Population.TREND_CONTINUATION: PopulationContract(
        population_id=Population.TREND_CONTINUATION,
        universe_id=Universe.STRATEGY,
        name="Trend Continuation",
        description="Strategy evaluations where family is TREND_CONTINUATION or CONTINUATION",
        definition="family.upper() IN ('TREND_CONTINUATION', 'CONTINUATION')",
        filter_field="family",
        filter_values=("TREND_CONTINUATION", "CONTINUATION"),
        record_grain=_STRAT_GRAIN,
        required_fields=("entity_id", "family"),
        optional_fields=(),
        join_keys=("entity_id",),
    ),
    Population.MEAN_REVERSION: PopulationContract(
        population_id=Population.MEAN_REVERSION,
        universe_id=Universe.STRATEGY,
        name="Mean Reversion",
        description="Strategy evaluations where family is MEAN_REVERSION or REVERSAL",
        definition="family.upper() IN ('MEAN_REVERSION', 'REVERSAL')",
        filter_field="family",
        filter_values=("MEAN_REVERSION", "REVERSAL"),
        record_grain=_STRAT_GRAIN,
        required_fields=("entity_id", "family"),
        optional_fields=(),
        join_keys=("entity_id",),
    ),
    Population.BREAKOUT: PopulationContract(
        population_id=Population.BREAKOUT,
        universe_id=Universe.STRATEGY,
        name="Breakout",
        description="Strategy evaluations where family is BREAKOUT or FALSE_BREAK",
        definition="family.upper() IN ('BREAKOUT', 'FALSE_BREAK')",
        filter_field="family",
        filter_values=("BREAKOUT", "FALSE_BREAK"),
        record_grain=_STRAT_GRAIN,
        required_fields=("entity_id", "family"),
        optional_fields=(),
        join_keys=("entity_id",),
    ),
    Population.MOMENTUM: PopulationContract(
        population_id=Population.MOMENTUM,
        universe_id=Universe.STRATEGY,
        name="Momentum",
        description="Strategy evaluations where family is MOMENTUM",
        definition="family.upper() == 'MOMENTUM'",
        filter_field="family",
        filter_values=("MOMENTUM",),
        record_grain=_STRAT_GRAIN,
        required_fields=("entity_id", "family"),
        optional_fields=(),
        join_keys=("entity_id",),
    ),
    Population.STRATEGY_ELIGIBLE: PopulationContract(
        population_id=Population.STRATEGY_ELIGIBLE,
        universe_id=Universe.STRATEGY,
        name="Strategy Eligible",
        description="Records where a strategy was eligible (family is not empty/NONE)",
        definition="family NOT IN ('', 'NONE') OR evaluation_status IN ('ELIGIBLE','SELECTED','EXECUTED')",
        filter_field="family",
        filter_values=(),
        record_grain=_STRAT_GRAIN,
        required_fields=("entity_id",),
        optional_fields=("family", "evaluation_status"),
        join_keys=("entity_id",),
    ),
    Population.STRATEGY_SELECTED: PopulationContract(
        population_id=Population.STRATEGY_SELECTED,
        universe_id=Universe.STRATEGY,
        name="Strategy Selected",
        description="Records where the strategy was selected for execution",
        definition="action == 'EXECUTE' OR evaluation_status == 'SELECTED'",
        filter_field="action",
        filter_values=("EXECUTE",),
        record_grain=_STRAT_GRAIN,
        required_fields=("entity_id",),
        optional_fields=("action", "evaluation_status"),
        join_keys=("entity_id",),
    ),
    Population.STRATEGY_REJECTED: PopulationContract(
        population_id=Population.STRATEGY_REJECTED,
        universe_id=Universe.STRATEGY,
        name="Strategy Rejected",
        description="Records where no strategy matched or strategy was rejected",
        definition="family IN ('','NONE') OR evaluation_status IN ('REJECTED','NOT_MET')",
        filter_field="family",
        filter_values=(),
        record_grain=_STRAT_GRAIN,
        required_fields=("entity_id",),
        optional_fields=("family", "evaluation_status"),
        join_keys=("entity_id",),
    ),
}


# ═══════════════════════════════════════════════════════════════════════════════
# JOIN CONTRACTS
# ═══════════════════════════════════════════════════════════════════════════════

JOIN_CONTRACTS: tuple[JoinContract, ...] = (
    JoinContract(
        join_id="EXEC_DECISION",
        left_universe=Universe.EXECUTION,
        right_universe=Universe.DECISION,
        left_key="entity_id",
        right_key="entity_id",
        cardinality=Cardinality.ONE_TO_ONE,
        description="Each executed trade maps to exactly one EXECUTE decision",
        temporal_constraint="Same entity_id implies same event lifecycle",
        expected_match_rate=0.95,
    ),
    JoinContract(
        join_id="DECISION_EXECUTION",
        left_universe=Universe.DECISION,
        right_universe=Universe.EXECUTION,
        left_key="entity_id",
        right_key="entity_id",
        cardinality=Cardinality.MANY_TO_ONE,
        description="Many decisions exist per entity; only EXECUTE decisions have a trade",
        temporal_constraint="Only EXECUTE decisions have matching execution records",
        expected_match_rate=0.05,  # ~351 EXECUTE out of 7841 decisions
    ),
    JoinContract(
        join_id="DECISION_MARKET",
        left_universe=Universe.DECISION,
        right_universe=Universe.MARKET,
        left_key="entity_id",
        right_key="entity_id",
        cardinality=Cardinality.ONE_TO_ONE,
        description="Each decision has exactly one market-state observation at decision time",
        temporal_constraint="Same entity_id and timestamp — market state at decision moment",
        expected_match_rate=0.75,  # Some decisions may lack v10_market_state
    ),
    JoinContract(
        join_id="DECISION_STRATEGY",
        left_universe=Universe.DECISION,
        right_universe=Universe.STRATEGY,
        left_key="entity_id",
        right_key="entity_id",
        cardinality=Cardinality.ONE_TO_ONE,
        description="Each decision has one strategy evaluation",
        temporal_constraint="Same entity_id — strategy assessed at same decision event",
        expected_match_rate=0.90,
    ),
    JoinContract(
        join_id="MARKET_STRATEGY",
        left_universe=Universe.MARKET,
        right_universe=Universe.STRATEGY,
        left_key="entity_id",
        right_key="entity_id",
        cardinality=Cardinality.ONE_TO_ONE,
        description="Market state and strategy evaluation for the same opportunity",
        temporal_constraint="Same entity_id — same decision cycle",
        expected_match_rate=0.80,
    ),
    JoinContract(
        join_id="EXEC_MARKET",
        left_universe=Universe.EXECUTION,
        right_universe=Universe.MARKET,
        left_key="entity_id",
        right_key="entity_id",
        cardinality=Cardinality.ONE_TO_ONE,
        description="Each trade has one market state at entry decision time",
        temporal_constraint="Market state captured at the decision that led to execution",
        expected_match_rate=0.90,
    ),
    JoinContract(
        join_id="EXEC_STRATEGY",
        left_universe=Universe.EXECUTION,
        right_universe=Universe.STRATEGY,
        left_key="entity_id",
        right_key="entity_id",
        cardinality=Cardinality.ONE_TO_ONE,
        description="Each trade has one strategy evaluation that led to execution",
        temporal_constraint="Strategy selected at the decision that led to this trade",
        expected_match_rate=0.85,
    ),
)

JOIN_CONTRACTS_BY_ID: dict[str, JoinContract] = {j.join_id: j for j in JOIN_CONTRACTS}


# ═══════════════════════════════════════════════════════════════════════════════
# SEMANTIC FIELD MAPPINGS
# ═══════════════════════════════════════════════════════════════════════════════
# Maps every semantic field used by the 45 questions to its source path.

SEMANTIC_FIELD_MAPPINGS: tuple[SemanticFieldMapping, ...] = (
    # ─── Execution Universe Fields ────────────────────────────────────────────
    SemanticFieldMapping("r_multiple", Universe.EXECUTION, "execution.r_multiple", FieldType.FLOAT, False, "-5 <= value <= 10", "Realised R-multiple of the trade"),
    SemanticFieldMapping("net_realised_pnl", Universe.EXECUTION, "execution.net_realised_pnl", FieldType.FLOAT, False, "numeric", "Net P&L after commission and swap"),
    SemanticFieldMapping("direction", Universe.EXECUTION, "execution.direction", FieldType.STRING, False, "BUY|SELL", "Trade direction"),
    SemanticFieldMapping("symbol", Universe.EXECUTION, "execution.symbol", FieldType.STRING, False, "non-empty", "Trading instrument"),
    SemanticFieldMapping("entry_price", Universe.EXECUTION, "execution.entry_price", FieldType.FLOAT, False, "> 0", "Entry price"),
    SemanticFieldMapping("exit_price", Universe.EXECUTION, "execution.exit_price", FieldType.FLOAT, False, "> 0", "Exit price"),
    SemanticFieldMapping("entry_time", Universe.EXECUTION, "execution.entry_time", FieldType.FLOAT, False, "> 0", "Entry timestamp (unix)"),
    SemanticFieldMapping("stop_loss", Universe.EXECUTION, "execution.stop_loss", FieldType.FLOAT, False, "> 0", "Stop loss price"),
    SemanticFieldMapping("take_profit", Universe.EXECUTION, "execution.take_profit", FieldType.FLOAT, True, ">= 0", "Take profit price"),
    SemanticFieldMapping("volume", Universe.EXECUTION, "execution.volume", FieldType.FLOAT, False, "> 0", "Position volume in lots"),
    SemanticFieldMapping("duration_seconds", Universe.EXECUTION, "execution.duration_seconds", FieldType.FLOAT, False, ">= 0", "Trade duration"),
    SemanticFieldMapping("exit_reason", Universe.EXECUTION, "execution.exit_reason", FieldType.STRING, False, "non-empty", "Why the trade was closed"),

    # ─── Decision Universe Fields ─────────────────────────────────────────────
    SemanticFieldMapping("score", Universe.DECISION, "score_strategy || score_neutral", FieldType.NULLABLE_FLOAT, True, "0-100", "Decision score (strategy-adjusted preferred)"),
    SemanticFieldMapping("ev", Universe.DECISION, "ev", FieldType.NULLABLE_FLOAT, True, "-1 <= value <= 5", "Expected value estimate"),
    SemanticFieldMapping("p_success", Universe.DECISION, "p_success", FieldType.NULLABLE_FLOAT, True, "0 <= value <= 1", "Predicted success probability"),
    SemanticFieldMapping("components", Universe.DECISION, "components", FieldType.DICT, True, "dict with string keys", "Scoring component breakdown"),
    SemanticFieldMapping("opportunity_quality", Universe.DECISION, "v10_opportunity.overall_quality", FieldType.NULLABLE_FLOAT, True, "0 <= value <= 1", "4-dimension opportunity quality score"),
    SemanticFieldMapping("location_score", Universe.DECISION, "v10_opportunity.location_score", FieldType.NULLABLE_FLOAT, True, "0 <= value <= 1", "Location quality dimension"),
    SemanticFieldMapping("structure_score", Universe.DECISION, "v10_opportunity.structure_score", FieldType.NULLABLE_FLOAT, True, "0 <= value <= 1", "Structure quality dimension"),
    SemanticFieldMapping("behaviour_score", Universe.DECISION, "v10_opportunity.behaviour_score", FieldType.NULLABLE_FLOAT, True, "0 <= value <= 1", "Behaviour quality dimension"),
    SemanticFieldMapping("formation_score", Universe.DECISION, "v10_opportunity.formation_score", FieldType.NULLABLE_FLOAT, True, "0 <= value <= 1", "Formation quality dimension"),
    SemanticFieldMapping("terminal_stage", Universe.DECISION, "terminal_stage", FieldType.STRING, False, "non-empty for NO_TRADE", "Pipeline stage where decision terminated"),
    SemanticFieldMapping("terminal_reason", Universe.DECISION, "terminal_reason", FieldType.STRING, False, "non-empty for NO_TRADE", "Reason for rejection/termination"),
    SemanticFieldMapping("action", Universe.DECISION, "action", FieldType.STRING, False, "EXECUTE|NO_TRADE", "Decision outcome"),

    # ─── Market Universe Fields ───────────────────────────────────────────────
    SemanticFieldMapping("regime", Universe.MARKET, "v10_market_state.regime.regime", FieldType.STRING, False, "TRENDING|RANGING|TRANSITIONAL", "H4 regime classification"),
    SemanticFieldMapping("regime_confidence", Universe.MARKET, "v10_market_state.regime.regime_confidence", FieldType.NULLABLE_FLOAT, True, "0 <= value <= 1", "Confidence in regime classification"),
    SemanticFieldMapping("volatility_state", Universe.MARKET, "v10_market_state.regime.volatility_state", FieldType.STRING, True, "NEUTRAL|EXPANSION|CONTRACTION", "Current volatility state"),
    SemanticFieldMapping("h4_trend", Universe.MARKET, "v10_market_state.h4.trend", FieldType.STRING, True, "BULLISH|BEARISH|NEUTRAL", "H4 trend direction"),
    SemanticFieldMapping("h4_market_phase", Universe.MARKET, "v10_market_state.h4.market_phase", FieldType.STRING, True, "IMPULSE|PULLBACK|CONSOLIDATION|EXHAUSTION", "H4 market phase"),
    SemanticFieldMapping("h4_atr", Universe.MARKET, "v10_market_state.h4.atr", FieldType.NULLABLE_FLOAT, True, ">= 0", "H4 Average True Range"),
    SemanticFieldMapping("h1_dominant_trend", Universe.MARKET, "v10_market_state.h1.dominant_trend", FieldType.STRING, True, "BULLISH|BEARISH|NEUTRAL", "H1 dominant trend"),
    SemanticFieldMapping("h1_structural_clarity", Universe.MARKET, "v10_market_state.h1.structural_clarity", FieldType.NULLABLE_FLOAT, True, "0 <= value <= 1", "H1 structural clarity score"),
    SemanticFieldMapping("h1_bos_confirmed", Universe.MARKET, "v10_market_state.h1.bos_confirmed", FieldType.BOOL, False, "true|false", "Whether Break of Structure is confirmed on H1"),
    SemanticFieldMapping("location_type", Universe.MARKET, "v10_market_state.location.location_type", FieldType.STRING, True, "DEMAND_ZONE|SUPPLY_ZONE|OPEN_SPACE|etc", "Price location type"),
    SemanticFieldMapping("inside_institutional_zone", Universe.MARKET, "v10_market_state.location.inside_institutional_zone", FieldType.BOOL, False, "true|false", "Whether price is inside an institutional zone"),
    SemanticFieldMapping("zone_quality", Universe.MARKET, "v10_market_state.location.zone_quality", FieldType.NULLABLE_FLOAT, True, "0 <= value <= 1", "Quality of the institutional zone"),
    SemanticFieldMapping("premium_discount", Universe.MARKET, "v10_market_state.location.premium_discount", FieldType.STRING, True, "PREMIUM|DISCOUNT|EQUILIBRIUM", "Premium/discount classification"),
    SemanticFieldMapping("htf_alignment_macro_bias", Universe.MARKET, "v10_market_state.htf_alignment.macro_bias", FieldType.STRING, True, "BULLISH|BEARISH|NEUTRAL", "Higher timeframe macro bias"),
    SemanticFieldMapping("htf_alignment_strength", Universe.MARKET, "v10_market_state.htf_alignment.macro_bias_strength", FieldType.NULLABLE_FLOAT, True, "0 <= value <= 1", "Strength of HTF alignment"),
    SemanticFieldMapping("structure_alignment", Universe.MARKET, "v10_market_state.htf_alignment.structure_alignment", FieldType.NULLABLE_FLOAT, True, "0 <= value <= 1", "Multi-timeframe structure alignment"),
    SemanticFieldMapping("session", Universe.MARKET, "derived from timestamp_utc", FieldType.STRING, True, "LONDON|NEW_YORK|ASIA", "Trading session (derived from hour)"),

    # ─── Strategy Universe Fields ─────────────────────────────────────────────
    SemanticFieldMapping("family", Universe.STRATEGY, "v10_strategy.family || strategy_family", FieldType.STRING, True, "TREND_CONTINUATION|MEAN_REVERSION|BREAKOUT|MOMENTUM|NONE", "Strategy family"),
    SemanticFieldMapping("confidence", Universe.STRATEGY, "v10_strategy.confidence || confidence", FieldType.NULLABLE_FLOAT, True, "0 <= value <= 1", "Strategy confidence score"),
    SemanticFieldMapping("pattern", Universe.STRATEGY, "pattern_name || detected_pattern", FieldType.STRING, True, "candlestick pattern name", "Detected candlestick pattern"),
    SemanticFieldMapping("conditions_met", Universe.STRATEGY, "conditions_passed", FieldType.NULLABLE_FLOAT, True, ">= 0", "Number of strategy conditions met"),
    SemanticFieldMapping("reasoning", Universe.STRATEGY, "v10_strategy.reasoning", FieldType.LIST, True, "list of strings", "Strategy selection reasoning"),
)

SEMANTIC_FIELDS_BY_NAME: dict[str, list[SemanticFieldMapping]] = {}
for _m in SEMANTIC_FIELD_MAPPINGS:
    SEMANTIC_FIELDS_BY_NAME.setdefault(_m.semantic_name, []).append(_m)


def get_field_mapping(semantic_name: str, universe: Universe) -> SemanticFieldMapping | None:
    """Look up the mapping for a semantic field in a specific universe."""
    for m in SEMANTIC_FIELD_MAPPINGS:
        if m.semantic_name == semantic_name and m.universe_id == universe:
            return m
    return None


def get_universe_contract(universe: Universe) -> UniverseContract:
    """Get the contract for a universe."""
    return UNIVERSE_CONTRACTS[universe]


def get_population_contract(population: Population) -> PopulationContract | None:
    """Get the contract for a population."""
    return POPULATION_CONTRACTS.get(population)


def get_join_contract(left: Universe, right: Universe) -> JoinContract | None:
    """Get the join contract between two universes."""
    for j in JOIN_CONTRACTS:
        if j.left_universe == left and j.right_universe == right:
            return j
    return None
