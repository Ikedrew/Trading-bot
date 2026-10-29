"""
Opportunity — Market observation object.

Represents: "The market presented something potentially interesting."
Does NOT represent: "Execute a trade."

An Opportunity captures:
    - What was observed (pattern, direction, symbol)
    - Supporting evidence (HTF context, regime, volatility)
    - Confidence level (pattern quality, evidence alignment)
    - Lifecycle state (what happened to this opportunity)

An Opportunity NEVER contains:
    - Stop loss / take profit
    - Lot size / volume
    - Order type
    - Final trade decision
    - Execution parameters

Those belong to the TradeIntent layer (created by RiskManager if approved).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# SCHEMA AND DATASET VERSION
# ═══════════════════════════════════════════════════════════════════════════════

from core.production_data_contract import (
    current_schema as _current_schema,
    current_generation as _current_generation,
)

SCHEMA_VERSION = _current_schema("opportunities")
"""Record-level schema version — sourced from the central production contract."""

DATASET_VERSION = _current_generation("opportunities")
"""Dataset-level generation. Clean V1 baseline == 1 (was "2026.1")."""


# ═══════════════════════════════════════════════════════════════════════════════
# LIFECYCLE STATES
# ═══════════════════════════════════════════════════════════════════════════════

class OpportunityState(str, Enum):
    """Lifecycle state of an Opportunity."""
    DETECTED = "DETECTED"        # Pattern fired, opportunity created
    ASSESSED = "ASSESSED"        # Engine scored and classified
    EXECUTED = "EXECUTED"        # Approved and filled by broker
    REJECTED = "REJECTED"        # Decision system rejected (with reason)
    EXPIRED = "EXPIRED"          # Not processed within cycle TTL


# ═══════════════════════════════════════════════════════════════════════════════
# OPPORTUNITY OBJECT
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Opportunity:
    """
    A market observation that may warrant a trading decision.

    Created at pattern detection time. Enriched by assessment.
    Persisted regardless of outcome (executed, rejected, or expired).
    """

    # ─── A) IDENTITY ──────────────────────────────────────────────────
    opportunity_id: str              # Unique: f"{symbol}_{bar_time}_{pattern}"
    symbol: str                      # Trading pair (e.g., "EURUSD")
    cycle_id: int                    # Scan cycle that produced this

    # ─── B) MARKET OBSERVATION ────────────────────────────────────────
    direction: str                   # "BUY" or "SELL" (implied by pattern)
    pattern: str                     # Pattern name (e.g., "TWEEZER_TOP")
    detection_timeframe: str         # Source timeframe ("M5")
    detected_at_bar_time: int        # Unix seconds of trigger bar
    detected_at_utc: str             # ISO timestamp of detection moment

    # Trigger candle context
    trigger_candle: dict[str, float] = field(default_factory=dict)
    # Expected: {open, high, low, close}

    # ─── B2) MARKET SNAPSHOT (at detection time) ──────────────────────
    # Required for hypothetical outcome research on rejected opportunities.
    bid_at_detection: float = 0.0    # Live bid price at detection moment
    ask_at_detection: float = 0.0    # Live ask price at detection moment
    spread_at_detection: float = 0.0 # ask - bid (execution cost context)
    session_at_detection: str = ""   # "LONDON" | "NY" | "ASIA" | "OFF" | ""

    # ─── SCHEMA/DATASET VERSION ───────────────────────────────────────
    schema_version: str = SCHEMA_VERSION    # Record schema version
    dataset_version: str = DATASET_VERSION  # Dataset semantic version

    # ─── C) EVIDENCE ─────────────────────────────────────────────────
    # Structure alignment (from HTF context at detection time)
    h4_regime: str = ""              # "TRENDING" | "RANGE" | "TRANSITIONAL" | ""
    h4_regime_confidence: float = 0.0
    h1_direction: str = ""           # "BULLISH" | "BEARISH" | "NEUTRAL" | ""
    h1_bos_confirmed: bool = False
    h1_swing_structure: str = ""     # "HH_HL" | "LH_LL" | "MIXED" | ""

    # Bias state
    bias_direction: str = ""         # Current M5 bias FSM direction
    bias_phase: str = ""             # "BUILDING" | "CONFIRMED" | "EXPIRED" | ""

    # ─── D) CONFIDENCE ────────────────────────────────────────────────
    pattern_confidence: float = 0.0  # From pattern detector (0.0–1.0)
    evidence_scores: dict[str, float] = field(default_factory=dict)
    # The 10-factor scoring breakdown (populated at ASSESSED stage)
    overall_score: float = 0.0       # Weighted composite (populated at ASSESSED)
    strategy_classification: str = ""  # "CONTINUATION"|"REVERSAL"|"FALSE_BREAK"|""
    strategy_confidence: float = 0.0

    # ─── E) LIFECYCLE ─────────────────────────────────────────────────
    state: str = OpportunityState.DETECTED.value
    rejection_reason: str = ""       # Why rejected (if state == REJECTED)
    rejection_stage: str = ""        # Which pipeline stage rejected
    outcome_trade_id: str = ""       # Link to trade (if state == EXECUTED)

    # Sibling awareness
    sibling_patterns: list[str] = field(default_factory=list)
    # Other patterns detected on same bar (for multi-pattern context)

    # ─── F) METADATA ─────────────────────────────────────────────────
    canonical_opportunity_id: str = ""
    # THE authoritative lineage root (Phase 3 data capture). Equals the minted
    # opportunity_id for every pattern-qualified opportunity (both derive from
    # the single approved authority core.identity.canonical); carried as an
    # explicit named join key so consumers never parse opportunity_id format.
    # Never repurposed; legacy opportunity_id preserved verbatim.
    entity_id: str = ""              # For joining to decision_audit, trade_truth
    correlation_id: str = ""         # Execution chain link (if executed)
    decision_id: str = ""            # Links to decision_audit (populated at ASSESSED)
    runtime_session_id: str = ""     # Distinguishes bot restart sessions

    # Additive Data/Shadow derivation (FIXED DECISION §5.2). Observational only.
    # passed_identification_condition =
    #   (identification_verdict == VALID) AND (len(eligible_horizons) > 0)
    # Default False; populated at the primary-opportunity enrichment point in
    # live_scanner.py from already-produced verdict + horizon eligibility.
    # Not an identifier; not a new stage; additive field on the existing
    # opportunities_v1 Data record (asdict auto-serializes).
    passed_identification_condition: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for JSONL persistence."""
        return asdict(self)

    def transition(self, new_state: OpportunityState, **kwargs: Any) -> None:
        """
        Transition lifecycle state. Accepts optional metadata updates.

        Example:
            opp.transition(OpportunityState.REJECTED,
                          rejection_reason="ev_policy_blocked",
                          rejection_stage="execution_policy")
        """
        self.state = new_state.value
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
