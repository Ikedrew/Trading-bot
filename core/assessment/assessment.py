"""
Assessment — Opportunity evaluation record.

Represents: "How good is this Opportunity?"
Does NOT represent: "Should we trade it?"

An Assessment captures the full analytical evaluation of a detected opportunity:
    - Scoring output (10-factor component breakdown + weighted composites)
    - Probability estimation (p_success from the probability model)
    - Expected value computation
    - Strategy classification and confidence
    - Uncertainty quantification
    - Evidence attribution breakdown
    - Reasoning narrative

An Assessment NEVER contains:
    - Trade approval decision
    - Position sizing / volume
    - Stop loss / take profit
    - Broker execution details
    - Account equity or drawdown

Those belong to the Decision and Execution layers.

Persistence:
    Local:  logs/assessments/{SYMBOL}/{YYYY-MM-DD}.jsonl
    S3:     s3://trading-bot-data-mk1/assessments/symbol={SYMBOL}/date={YYYY-MM-DD}/part-000.jsonl
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# SCHEMA AND DATASET VERSION
# ═══════════════════════════════════════════════════════════════════════════════

SCHEMA_VERSION = "assessment_v1"
"""Record-level schema version. Increment on breaking field changes."""

DATASET_VERSION = "2026.1"
"""Dataset-level version. Increment on semantic changes to what the dataset represents."""


# ═══════════════════════════════════════════════════════════════════════════════
# ASSESSMENT RECORD
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Assessment:
    """
    Complete evaluation of a detected opportunity.

    One record per opportunity that reaches the engine scoring stage.
    Persisted regardless of whether the trade is ultimately approved or rejected.
    """

    # ─── IDENTITY ─────────────────────────────────────────────────────
    assessment_id: str               # Unique: f"{symbol}_{bar_time}_{pattern}_assessment"
    opportunity_id: str              # Links to Opportunity record
    symbol: str
    cycle_id: int
    bar_time: int                    # Unix seconds of evaluated bar

    # ─── VERSION ──────────────────────────────────────────────────────
    schema_version: str = SCHEMA_VERSION
    dataset_version: str = DATASET_VERSION

    # ─── SCORING ──────────────────────────────────────────────────────
    # 10-factor component breakdown (each 0.0–1.0)
    components: dict[str, float] = field(default_factory=dict)
    score_neutral: float = 0.0       # Global-weighted composite
    score_strategy: float = 0.0      # Strategy-weighted composite
    score_delta: float = 0.0         # score_strategy - score_neutral

    # ─── STRATEGY CLASSIFICATION ──────────────────────────────────────
    pattern: str = ""                # Authoritative pattern name
    direction: str = ""              # "BUY" or "SELL"
    selected_strategy: str = ""      # "CONTINUATION" | "REVERSAL" | "FALSE_BREAK" | ""
    strategy_confidence: float = 0.0 # Activation weight (0.0–1.0)
    regime: str = ""                 # "TRENDING" | "RANGE" | "TRANSITIONAL"
    regime_confidence: float = 0.0
    weights_used: str = ""           # "strategy_specific" | "global_fallback"

    # ─── PROBABILITY ──────────────────────────────────────────────────
    p_success: float = 0.0           # Probability estimate (0.0–1.0)
    probability_source: str = ""     # Model identifier
    probability_model_version: str = ""

    # ─── EXPECTED VALUE ───────────────────────────────────────────────
    ev: float = 0.0                  # Expected value
    ev_positive: bool = False        # Whether EV > 0
    ev_reward: float = 0.0           # Reward component
    ev_risk: float = 0.0             # Risk component
    rr_effective: float = 0.0        # Effective risk/reward ratio

    # ─── MARKET STATE ─────────────────────────────────────────────────
    market_state: str = ""           # "STRUCTURED" | "TRANSITIONAL" | "CHOP"
    market_state_confidence: float = 0.0

    # ─── UNCERTAINTY ──────────────────────────────────────────────────
    uncertainty_score: float = 0.0   # 0.0 = very clear, 1.0 = highly ambiguous
    confidence_modifier: float = 0.0 # Applied confidence adjustment

    # ─── CONFIRMATION ─────────────────────────────────────────────────
    confirmation_score: float = 0.0  # Candle quality score (0.0–1.0)
    confirmation_strength: str = ""  # "STRONG" | "WEAK" | "INVALID"

    # ─── REASONING (narrative) ────────────────────────────────────────
    reasoning_narrative: str = ""    # Human-readable explanation
    policy_reasoning: str = ""       # Execution policy reasoning

    # ─── EVIDENCE ATTRIBUTION ─────────────────────────────────────────
    evidence_contributions: list[dict[str, Any]] = field(default_factory=list)
    # Per-factor attribution breakdown

    # ─── CONTEXT (market at assessment time) ──────────────────────────
    bid_at_assessment: float = 0.0
    ask_at_assessment: float = 0.0

    # ─── JOIN KEYS ────────────────────────────────────────────────────
    entity_id: str = ""              # Joins to decision_audit, trade_truth, opportunity
    canonical_opportunity_id: str = ""  # THE authoritative lineage root (remediation)
    correlation_id: str = ""         # Technical tracing only (never a join requirement)
    decision_id: str = ""            # Joins to decision_audit
    runtime_session_id: str = ""     # Distinguishes bot sessions

    # ─── TIMESTAMP ────────────────────────────────────────────────────
    assessed_at_utc: str = ""        # ISO timestamp of assessment moment

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for JSONL persistence."""
        return asdict(self)
