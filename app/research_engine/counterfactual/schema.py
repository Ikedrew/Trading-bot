"""
Counterfactual Truth Schema — Data model for blocked decision outcomes.

This module defines the schema for counterfactual simulation results:
what WOULD have happened if a blocked trade had been allowed to execute.

IMPORTANT DISTINCTIONS:
    - trade_truth = what ACTUALLY happened (real execution)
    - shadow_trade = what the shadow engine simulated (EXECUTE path only)
    - counterfactual_truth = what WOULD have happened (blocked decisions only)

These are three SEPARATE datasets. Never conflate them.

JOIN STRATEGY:
    Blocked decisions do NOT have correlation_id (they never reach ExecutionPrep).
    Join key: entity_id ({symbol}_{bar_time_unix}) or cycle_id + symbol.
    Bar time extracted from entity_id → used to load replay candles.

SIMULATION CONFIDENCE:
    Every result carries a confidence level indicating data quality:
    - HIGH: exact replay candle, direction confirmed, SL/TP from live rules
    - MEDIUM: partial reconstruction (e.g., estimated SL from rr_effective)
    - LOW: assumptions required (e.g., no replay data, inferred parameters)
    - UNKNOWN: insufficient data to simulate (e.g., blocked before risk eval)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SimulationConfidence(str, Enum):
    """Confidence level of the counterfactual simulation."""
    HIGH = "HIGH"         # Exact replay candle + direction + SL/TP from live rules
    MEDIUM = "MEDIUM"     # Partial reconstruction (e.g., SL estimated from rr_effective)
    LOW = "LOW"           # Assumptions required (e.g., no replay data for some bars)
    UNKNOWN = "UNKNOWN"   # Insufficient data to simulate (blocked before risk eval)


class OutcomeClass(str, Enum):
    """Classification of the counterfactual outcome."""
    LOSS_AVOIDED = "LOSS_AVOIDED"      # Blocker correctly prevented a losing trade
    WIN_AVOIDED = "WIN_AVOIDED"        # Blocker incorrectly prevented a winning trade (MISSED)
    BREAKEVEN = "BREAKEVEN"            # Trade would have roughly broken even
    TIMEOUT = "TIMEOUT"                # Trade would have hit max bars without SL/TP
    UNKNOWN = "UNKNOWN"                # Cannot determine outcome


@dataclass
class CounterfactualTruth:
    """
    Counterfactual outcome for one blocked decision.

    Answers: "If this trade had been allowed, what would the R-multiple have been?"
    """

    # ─── IDENTITY (from decision trace) ───────────────────────────────
    entity_id: str
    cycle_id: int
    symbol: str
    timestamp_utc: str = ""

    # ─── BLOCKING CONTEXT ─────────────────────────────────────────────
    terminal_stage: str = ""          # Which gate blocked (ev_policy, swing, scoring)
    terminal_reason: str = ""         # Full reason string
    blocking_component: str = ""      # Extracted gate name for aggregation

    # ─── DECISION CONTEXT ─────────────────────────────────────────────
    pattern_name: str = ""
    direction: str = ""               # "BUY" or "SELL"
    score_neutral: float = 0.0
    regime: str = ""
    market_state: str = ""

    # ─── HYPOTHETICAL TRADE PARAMETERS ────────────────────────────────
    entry_price: float = 0.0
    stop_price: float = 0.0
    target_price: float = 0.0
    risk_distance: float = 0.0       # abs(entry - stop)
    target_r: float = 0.0            # RR ratio used

    # ─── SIMULATION OUTCOME ───────────────────────────────────────────
    future_data_available: bool = False
    bars_evaluated: int = 0
    max_favourable_excursion_r: float = 0.0   # Best R reached during simulation
    max_adverse_excursion_r: float = 0.0      # Worst R reached during simulation
    hypothetical_exit_price: float = 0.0
    hypothetical_exit_reason: str = ""        # stop_loss, take_profit, max_bars_timeout
    hypothetical_r: float = 0.0               # Final R-multiple of the simulated trade

    # ─── CLASSIFICATION ───────────────────────────────────────────────
    outcome_class: OutcomeClass = OutcomeClass.UNKNOWN
    simulation_confidence: SimulationConfidence = SimulationConfidence.UNKNOWN

    # ─── CONFIDENCE FACTORS ───────────────────────────────────────────
    confidence_factors: dict[str, bool] = field(default_factory=dict)
    # Expected keys:
    #   replay_candle_available: True if exact M5 candle loaded from replay_data
    #   direction_confirmed: True if pattern → direction mapping succeeded
    #   sl_from_live_rules: True if SL computed using actual SLTP_RULES
    #   sl_from_rr_estimate: True if SL estimated from rr_effective field
    #   tp_from_live_rules: True if TP computed using actual RR config
    #   future_bars_complete: True if enough future bars available for simulation

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSONL persistence."""
        return {
            "entity_id": self.entity_id,
            "cycle_id": self.cycle_id,
            "symbol": self.symbol,
            "timestamp_utc": self.timestamp_utc,
            "terminal_stage": self.terminal_stage,
            "terminal_reason": self.terminal_reason,
            "blocking_component": self.blocking_component,
            "pattern_name": self.pattern_name,
            "direction": self.direction,
            "score_neutral": round(self.score_neutral, 4),
            "regime": self.regime,
            "market_state": self.market_state,
            "entry_price": self.entry_price,
            "stop_price": self.stop_price,
            "target_price": self.target_price,
            "risk_distance": round(self.risk_distance, 8),
            "target_r": round(self.target_r, 3),
            "future_data_available": self.future_data_available,
            "bars_evaluated": self.bars_evaluated,
            "max_favourable_excursion_r": round(self.max_favourable_excursion_r, 4),
            "max_adverse_excursion_r": round(self.max_adverse_excursion_r, 4),
            "hypothetical_exit_price": self.hypothetical_exit_price,
            "hypothetical_exit_reason": self.hypothetical_exit_reason,
            "hypothetical_r": round(self.hypothetical_r, 4),
            "outcome_class": self.outcome_class.value,
            "simulation_confidence": self.simulation_confidence.value,
            "confidence_factors": self.confidence_factors,
        }


@dataclass
class BlockerEconomicImpact:
    """
    Economic impact summary for one blocking gate.

    Aggregates counterfactual outcomes to rank blockers by economic value.
    """

    # ─── IDENTITY ─────────────────────────────────────────────────────
    terminal_stage: str              # Gate identifier (e.g., "ev_policy")
    terminal_reason: str = ""        # Optional sub-reason for granularity

    # ─── COUNTS ───────────────────────────────────────────────────────
    total_blocks: int = 0
    simulable_blocks: int = 0        # Had enough data to simulate
    high_confidence: int = 0         # Simulation confidence = HIGH
    medium_confidence: int = 0
    low_confidence: int = 0
    unknown: int = 0                 # Could not simulate

    # ─── OUTCOME DISTRIBUTION ─────────────────────────────────────────
    losses_avoided: int = 0          # Hypothetical R < 0 (correct block)
    wins_avoided: int = 0            # Hypothetical R > 0 (missed opportunity)
    breakeven: int = 0               # Hypothetical R ≈ 0
    timeout: int = 0                 # Hit max bars

    # ─── ECONOMIC METRICS (in R-multiples) ────────────────────────────
    avoided_loss_r: float = 0.0      # Sum of |negative R| for losses avoided (positive = good)
    missed_profit_r: float = 0.0     # Sum of positive R for wins avoided (positive = cost)
    net_counterfactual_r: float = 0.0  # avoided_loss_r - missed_profit_r (positive = net protective)

    # ─── RATES ────────────────────────────────────────────────────────
    protection_rate: float = 0.0     # losses_avoided / simulable_blocks
    miss_rate: float = 0.0           # wins_avoided / simulable_blocks

    # ─── AVERAGES ─────────────────────────────────────────────────────
    avg_avoided_loss_r: float = 0.0  # Average R of avoided losses
    avg_missed_profit_r: float = 0.0 # Average R of missed wins
    avg_mfe_r: float = 0.0           # Average max favourable excursion

    def to_dict(self) -> dict[str, Any]:
        """Serialize for reporting."""
        return {
            "terminal_stage": self.terminal_stage,
            "terminal_reason": self.terminal_reason,
            "total_blocks": self.total_blocks,
            "simulable_blocks": self.simulable_blocks,
            "high_confidence": self.high_confidence,
            "medium_confidence": self.medium_confidence,
            "low_confidence": self.low_confidence,
            "unknown": self.unknown,
            "losses_avoided": self.losses_avoided,
            "wins_avoided": self.wins_avoided,
            "breakeven": self.breakeven,
            "timeout": self.timeout,
            "avoided_loss_r": round(self.avoided_loss_r, 4),
            "missed_profit_r": round(self.missed_profit_r, 4),
            "net_counterfactual_r": round(self.net_counterfactual_r, 4),
            "protection_rate": round(self.protection_rate, 4),
            "miss_rate": round(self.miss_rate, 4),
            "avg_avoided_loss_r": round(self.avg_avoided_loss_r, 4),
            "avg_missed_profit_r": round(self.avg_missed_profit_r, 4),
            "avg_mfe_r": round(self.avg_mfe_r, 4),
        }
