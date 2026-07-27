"""
StrictnessBase — Decision Stability Diagnostic Engine.

Forensic diagnostic layer that maps decision failure across the trading pipeline.
Consumes ONLY completed TradeEvent records. NEVER influences decisions.

Answers:
  1. Where did decisions die?
  2. Why did they die?
  3. Is failure consistent or chaotic?
  4. Is the system over-filtering or under-filtering?

Ownership: phase5/strictness_base.py
Mutability: NONE (pure functions on immutable events)
Dependencies: TradeEvent only (no trading runtime imports)
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

from phase5.event_reconstructor import TradeEvent


# ─── LIFECYCLE STAGES ─────────────────────────────────────────────────────────

LIFECYCLE_STAGES = ("PRE_SETUP", "STRUCTURE", "CONFIRMATION", "SCORING", "RISK", "EXECUTION")


# ─── OUTPUT CONTRACT ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class StrictnessReport:
    """
    Frozen diagnostic artifact. Explains why the system is not trading.
    NEVER influences decisions. Purely observational.
    """
    timestamp: float
    symbol: str

    # Block classification
    structural_blocks: int
    timing_blocks: int
    disagreement_blocks: int
    scoring_blocks: int
    risk_blocks: int

    # Derived metrics
    total_attempts: int
    total_blocks: int
    block_rate: float

    # Behavioural interpretation
    regime: Literal["UNDER_FILTERING", "BALANCED", "OVER_FILTERING", "UNSTABLE"]

    # Diagnostic signals
    dominant_block_reason: str
    secondary_block_reason: str

    # Stability metrics
    consistency_score: float  # 0–1 (high = same stage always fails)
    entropy_of_blocks: float  # low = stable failure point, high = chaotic

    # Funnel efficiency (stage-to-stage survival rates)
    funnel_efficiency: dict[str, float]

    # Recommendation tags (NON-ACTING)
    suggestion_tags: list[str] = field(default_factory=list)


# ─── LIFECYCLE CLASSIFICATION ─────────────────────────────────────────────────

def _classify_block_stage(event: TradeEvent) -> str:
    """
    Classify which lifecycle stage blocked this event.
    Uses conflict_types, system_state, and voter_snapshot as signals.
    """
    # If production traded → not a block
    if event.production_decision in ("BUY", "SELL"):
        return "EXECUTION"  # Reached execution (may have succeeded or failed)

    # Classify NO_TRADE events by their characteristics
    severity = event.conflict_severity
    state = event.system_state
    agreement = event.agreement_score
    voter = event.voter_snapshot

    # PRE_SETUP: no meaningful signal from any voter
    if abs(voter.bias) < 0.1 and abs(voter.structure) < 0.1:
        return "PRE_SETUP"

    # STRUCTURE: bias voter active but structure/agreement low
    if voter.bias != 0 and voter.structure < 0 and state in ("unstable", "degenerate"):
        return "STRUCTURE"

    # DISAGREEMENT: high conflict, voters opposing
    if severity in ("medium", "high") or len(event.conflict_types) >= 2:
        return "CONFIRMATION"  # Conflict prevented confirmation

    # SCORING: voters somewhat aligned but score below threshold
    if agreement >= 0.5 and abs(voter.bias) > 0.3:
        return "SCORING"

    # RISK: everything aligned but execution blocked
    if agreement >= 0.7 and event.ssi > 0.5:
        return "RISK"

    # Default: structural block (most common for over-filtering)
    return "STRUCTURE"


# ─── ENTROPY CALCULATION ──────────────────────────────────────────────────────

def _compute_entropy(counts: dict[str, int]) -> float:
    """Shannon entropy of block distribution. 0 = all same stage, high = chaotic."""
    total = sum(counts.values())
    if total == 0:
        return 0.0
    entropy = 0.0
    for count in counts.values():
        if count > 0:
            p = count / total
            entropy -= p * math.log2(p)
    return round(entropy, 3)


# ─── REGIME CLASSIFICATION ────────────────────────────────────────────────────

def _classify_regime(block_rate: float, entropy: float, dominant_stage: str) -> str:
    """Classify system filtering regime."""
    if block_rate < 0.30:
        return "UNDER_FILTERING"
    elif block_rate > 0.70:
        if entropy < 1.5:
            return "OVER_FILTERING"  # Concentrated failure = systematic over-strictness
        else:
            return "UNSTABLE"  # High blocks + high entropy = chaotic
    else:
        if entropy > 2.0:
            return "UNSTABLE"
        return "BALANCED"


# ─── FUNNEL EFFICIENCY ────────────────────────────────────────────────────────

def _compute_funnel(stage_counts: dict[str, int], total: int) -> dict[str, float]:
    """Compute stage-to-stage survival rates."""
    if total == 0:
        return {}

    # Cumulative: how many survived past each stage
    survived = total
    funnel: dict[str, float] = {}
    stage_order = ["PRE_SETUP", "STRUCTURE", "CONFIRMATION", "SCORING", "RISK", "EXECUTION"]

    for i in range(len(stage_order) - 1):
        blocked_here = stage_counts.get(stage_order[i], 0)
        survived -= blocked_here
        key = f"{stage_order[i].lower()}_to_{stage_order[i+1].lower()}"
        funnel[key] = round(survived / total, 3) if total > 0 else 0.0

    return funnel


# ─── SUGGESTION TAGS ──────────────────────────────────────────────────────────

def _generate_suggestions(
    regime: str,
    dominant: str,
    block_rate: float,
    entropy: float,
) -> list[str]:
    """Generate descriptive suggestion tags (NEVER applied automatically)."""
    tags: list[str] = []

    if regime == "OVER_FILTERING":
        tags.append("over_filtering_detected")
        if dominant == "STRUCTURE":
            tags.append("fsm_too_strict")
        elif dominant == "SCORING":
            tags.append("scoring_threshold_high")
        elif dominant == "PRE_SETUP":
            tags.append("early_stage_collapse")

    elif regime == "UNSTABLE":
        tags.append("execution_instability")
        if entropy > 2.0:
            tags.append("chaotic_rejection_pattern")

    elif regime == "UNDER_FILTERING":
        tags.append("insufficient_filtering")

    elif regime == "BALANCED":
        tags.append("healthy_filtering")

    return tags


# ─── MAIN API ─────────────────────────────────────────────────────────────────

def compute_strictness_report(
    events: list[TradeEvent],
    symbol: str = "ALL",
) -> StrictnessReport:
    """
    Compute strictness diagnostic report from completed trade events.

    Pure function. No side effects. No runtime dependencies.
    Consumes only TradeEvent records.

    Args:
        events: List of reconstructed trade events
        symbol: Symbol filter (or "ALL" for aggregate)

    Returns:
        Frozen StrictnessReport with full diagnostic breakdown.
    """
    if symbol != "ALL":
        events = [e for e in events if e.symbol == symbol]

    total = len(events)
    if total == 0:
        return StrictnessReport(
            timestamp=0.0, symbol=symbol,
            structural_blocks=0, timing_blocks=0, disagreement_blocks=0,
            scoring_blocks=0, risk_blocks=0,
            total_attempts=0, total_blocks=0, block_rate=0.0,
            regime="BALANCED",
            dominant_block_reason="none", secondary_block_reason="none",
            consistency_score=1.0, entropy_of_blocks=0.0,
            funnel_efficiency={}, suggestion_tags=[],
        )

    # Classify each event
    stage_assignments: list[str] = []
    for event in events:
        stage = _classify_block_stage(event)
        stage_assignments.append(stage)

    stage_counts = Counter(stage_assignments)

    # Block counts (EXECUTION = not blocked)
    blocks = total - stage_counts.get("EXECUTION", 0)
    block_rate = blocks / total if total > 0 else 0.0

    # Map to categories
    structural = stage_counts.get("STRUCTURE", 0) + stage_counts.get("PRE_SETUP", 0)
    timing = 0  # Would need explicit timing data; approximate from system_state
    disagreement = stage_counts.get("CONFIRMATION", 0)
    scoring = stage_counts.get("SCORING", 0)
    risk = stage_counts.get("RISK", 0)

    # Timing approximation: events where system_state indicates timing issues
    timing = sum(1 for e in events if e.system_state == "degenerate")

    # Dominant block reasons
    block_counter = Counter({
        "structural": structural,
        "timing": timing,
        "disagreement": disagreement,
        "scoring": scoring,
        "risk": risk,
    })
    most_common = block_counter.most_common(2)
    dominant = most_common[0][0] if most_common else "none"
    secondary = most_common[1][0] if len(most_common) > 1 else "none"

    # Entropy
    entropy = _compute_entropy(stage_counts)

    # Consistency: inverse of entropy (normalized)
    max_entropy = math.log2(len(LIFECYCLE_STAGES)) if len(LIFECYCLE_STAGES) > 1 else 1.0
    consistency = round(1.0 - (entropy / max_entropy), 3) if max_entropy > 0 else 1.0

    # Regime
    regime = _classify_regime(block_rate, entropy, dominant)

    # Funnel
    funnel = _compute_funnel(stage_counts, total)

    # Suggestions
    suggestions = _generate_suggestions(regime, dominant.upper(), block_rate, entropy)

    # Timestamp from latest event
    latest_ts = max(e.timestamp for e in events) if events else 0.0

    return StrictnessReport(
        timestamp=latest_ts,
        symbol=symbol,
        structural_blocks=structural,
        timing_blocks=timing,
        disagreement_blocks=disagreement,
        scoring_blocks=scoring,
        risk_blocks=risk,
        total_attempts=total,
        total_blocks=blocks,
        block_rate=round(block_rate, 3),
        regime=regime,
        dominant_block_reason=dominant,
        secondary_block_reason=secondary,
        consistency_score=consistency,
        entropy_of_blocks=entropy,
        funnel_efficiency=funnel,
        suggestion_tags=suggestions,
    )
