"""
Layered evaluation artifacts (MK1 pipeline scaffolding).

Stage-1 goals: explicit structured outputs per concern without changing scoring rules
or reordering gates. Behaviour remains driven by core.engine procedural parity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from data.mt5_data import Candle
from risk.models import OrderIntent
from strategy.signals import Signal, Side

# -----------------------------------------------------------------------------
# Inputs (immutable snapshot for one closed-bar evaluation)
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class BarEvaluationContext:
    """Everything read-only needed to interpret this bar evaluation."""

    candles: list[Candle]
    closed_i: int
    symbol: str
    bid: float
    ask: float
    current_time_s: float
    config_module: Any


@dataclass
class Decision:
    """Legacy wire format — unchanged contract for logging and execution."""

    should_trade: bool
    reason: str
    signal: Signal | None
    intent: OrderIntent | None
    bias: Side | None = None
    patterns: list[str] | None = None
    score: int = 0
    bias_phase: str = "EXPIRED"
    bias_validation_score: int = 0
    structure_ok: bool = False
    bias_strength: float = 0.0
    bias_age_seconds: float = 0.0
    bias_window_phase: str = "early"
    confluence_threshold_dynamic: float = 0.0
    regime_state: str = "RANGING"
    confluence_breakdown: dict[str, float | str] | None = None


# -----------------------------------------------------------------------------
# Layer results (extend with fields in later refactor stages as logic moves out)
# -----------------------------------------------------------------------------


@dataclass
class ContextResult:
    """Market / bar environment after time sync, bias decay, memory update, optional pre-bias gate."""

    evaluated: bool = False

    #: Wall-clock elapsed since previous processed bar (used for decay); 0 when no prior time.
    elapsed_s: float = 0.0

    #: When CHOP_FILTER_ENABLED: outcome of passes_market_filter before bias/pattern work.
    market_filter_checked: bool = False
    market_filter_passed: bool | None = None
    market_filter_reason: str = ""

    #: Snapshots taken after _update_market_memory at this point in the pipeline.
    regime_state: str = "RANGING"
    last_sweep_high: float | None = None
    last_sweep_low: float | None = None
    last_strong_impulse_direction: Side | None = None


@dataclass
class PatternResult:
    """Raw pattern detection output for the closed bar (no bias-based filtering yet)."""

    evaluated: bool = False

    #: Direction hint from MA setup (same bar, runs with detection today).
    raw_bias_from_setup: Side | None = None

    signals: list[Signal] = field(default_factory=list)
    pattern_names: list[str] = field(default_factory=list)


@dataclass
class ConfirmationResult:
    """Bar-level confirmation for the candidate signal picked for this bar."""

    evaluated: bool = False
    signal: Signal | None = None
    passed: bool = False
    reason: str = ""
    strength: str = ""           # "INVALID", "WEAK", or "STRONG"
    body_pct: float = 0.0       # Body as percentage of candle range (0.0–1.0)
    wick_ratio: float = 0.0     # Combined wick / total range (0.0–1.0)
    close_location: float = 0.0  # Close position within range (0.0=low, 1.0=high)


@dataclass
class StructureResult:
    """Bias continuity, structural metrics, stability, failure-zone guards up to aligned-pattern choice."""

    evaluated: bool = False

    #: Config-derived knobs applied this bar (mirrors mutable EngineState writes).
    bias_confluence_threshold: float = 0.0

    #: Phase / directional evaluation used for downstream alignment + Decision fields.
    bias_phase: str = "EXPIRED"
    raw_bias_from_setup: Side | None = None
    current_bias: Side | None = None
    evaluation_bias: Side | None = None
    bias_validation_score: int = 0
    structure_ok: bool = False
    #: Second output of _bias_metrics_for_side internal confluence (bias building gate).
    bias_structure_confluence: float = 0.0

    bias_window_phase: str = "early"
    confluence_threshold_dynamic: float = 0.0
    bias_strength: float = 0.0
    bias_age_seconds: float = 0.0

    stability_score: float = 0.0
    can_trade_bias: bool = False
    #: True iff _in_recent_failure_zone veto fired.
    failure_zone_blocked: bool = False

    #: Signals aligned to evaluation_bias when that stage runs; empty otherwise.
    bias_aligned_signals: list[Signal] = field(default_factory=list)
    chosen_signal: Signal | None = None


@dataclass
class ScoreResult:
    """Weighted confluence ledger for the bar (runs only after pre-score quality gates succeed)."""

    evaluated: bool = False

    base_score: float = 0.0
    volatility_penalty: float = 0.0
    bias_age_weight: float = 0.0
    time_decay_multiplier: float = 0.0
    regime_bonus: float = 0.0
    sweep_bonus: float = 0.0
    final_score: float = 0.0
    score_int: int = 0

    min_score_threshold: float = 0.0
    soft_floor: float = 4.5
    in_soft_zone: bool = False
    allow_soft_entry: bool = False
    stability_at_score: float = 0.0
    #: True iff score satisfied min threshold or soft-zone escape (same semantics as legacy).
    passed_threshold: bool = False

    #: Same keys as Decision.confluence_breakdown when populated (mixed types mirror legacy dumps).
    breakdown: dict[str, float | str] | None = None


@dataclass
class QualityResult:
    """Tradability constraints after confirmation and around execution (MK1 gates)."""

    evaluated: bool = False

    post_confirm_chop_filter_checked: bool = False
    post_confirm_chop_filter_passed: bool | None = None

    direction_cooldown_veto: bool = False

    trend_filter_checked: bool = False
    trend_aligned: bool | None = None

    max_positions_blocked: bool = False
    cooldown_blocked: bool = False
    can_trade_bias_blocked: bool = False

    intent_attempted: bool = False
    intent_built_ok: bool | None = None


@dataclass
class UnifiedDecision:
    """Single bundle: layer outputs feeding one legacy Decision."""

    bar_context: BarEvaluationContext

    context: ContextResult = field(default_factory=ContextResult)
    pattern: PatternResult = field(default_factory=PatternResult)
    confirmation: ConfirmationResult = field(default_factory=ConfirmationResult)
    structure: StructureResult = field(default_factory=StructureResult)
    score: ScoreResult = field(default_factory=ScoreResult)
    quality: QualityResult = field(default_factory=QualityResult)

    #: Which pipeline stage last completed before exit (including successful completion).
    last_completed_stage: str = ""

    #: Authoritative params passed through DecisionEngine (same information as `decision`).
    decision_authority: Any | None = None

    decision: Decision = field(default_factory=lambda: Decision(False, "", None, None))
