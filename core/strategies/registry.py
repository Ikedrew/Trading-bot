"""
Strategy Registry — Single source of truth for all registered strategies.

Contains strategy definitions as research hypotheses. No strategy is active.
All strategies start as HYPOTHESIS and require validated evidence to progress.

IMPORTANT:
    These are NOT trading strategies yet.
    They are research candidates that describe HOW a market behaviour
    might be exploited. Activation requires passing through research
    promotion gates with validated statistical evidence.
"""

from __future__ import annotations

from core.strategy_family.models import StrategyFamily
from core.strategies.models import (
    EvidenceStatus,
    ExitModel,
    RiskModel,
    StrategyDefinition,
    StrategyStatus,
)


# ═══════════════════════════════════════════════════════════════════════════════
# STRATEGY DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════════════

STRATEGY_REGISTRY: dict[str, StrategyDefinition] = {}


def _register(strategy: StrategyDefinition) -> None:
    """Register a strategy definition."""
    STRATEGY_REGISTRY[strategy.strategy_id] = strategy


# ─── REVERSAL FAMILY ──────────────────────────────────────────────────────────

_register(StrategyDefinition(
    strategy_id="range_reversal_v1",
    name="Range Reversal V1",
    description=(
        "Reversal strategy attempting to exploit failed continuation at range extremes. "
        "Hypothesis: when price reaches range boundaries in a RANGE regime, reversal "
        "patterns have higher probability of success because the range is acting as "
        "a mean-reverting structure."
    ),
    strategy_family=StrategyFamily.REVERSAL,
    valid_market_phases=("REVERSAL", "EXHAUSTION"),
    required_context=("h4_regime", "market_phase", "structure_level"),
    trigger_patterns=(
        "TWEEZER_TOP", "TWEEZER_BOTTOM", "HAMMER", "HANGING_MAN",
        "INVERTED_HAMMER", "SHOOTING_STAR", "MORNING_STAR", "EVENING_STAR",
        "BULLISH_ENGULFING", "BEARISH_ENGULFING",
    ),
    entry_conditions=(
        "Price at range extreme (upper/lower 20%)",
        "H4 regime is RANGE",
        "Market phase is REVERSAL or EXHAUSTION",
        "No strong momentum against reversal direction",
    ),
    invalidation_conditions=(
        "Price breaks range with conviction (strong close beyond)",
        "Regime transitions to TRENDING",
        "Momentum indicator shows strong continuation",
    ),
    risk_model=RiskModel(
        stop_loss_method="STRUCTURE",
        risk_reward_minimum=1.5,
        max_risk_per_trade=0.01,
        trailing_stop=False,
        invalidation_type="PRICE_BASED",
        notes="Stop beyond range boundary + buffer",
    ),
    exit_model=ExitModel(
        take_profit_method="STRUCTURE",
        partial_exit=False,
        time_based_exit=True,
        max_hold_candles=20,
        trailing_exit=False,
        notes="Target opposite range boundary or midpoint",
    ),
    status=StrategyStatus.HYPOTHESIS,
    evidence_status=EvidenceStatus(
        notes="Awaiting M9/M10 research results for REVERSAL phase validation",
    ),
    version="1.0",
    author="research_engine",
    created_date="2026-07-27",
))

_register(StrategyDefinition(
    strategy_id="liquidity_sweep_reversal_v1",
    name="Liquidity Sweep Reversal V1",
    description=(
        "Reversal strategy attempting to exploit liquidity grabs and rejection. "
        "Hypothesis: when price sweeps beyond a known liquidity level and immediately "
        "reverses, the move was a stop hunt rather than genuine breakout."
    ),
    strategy_family=StrategyFamily.REVERSAL,
    valid_market_phases=("REVERSAL", "EXHAUSTION", "CONSOLIDATION"),
    required_context=("h4_regime", "market_phase", "liquidity_levels", "volume"),
    trigger_patterns=(
        "HAMMER", "SHOOTING_STAR", "BULLISH_ENGULFING", "BEARISH_ENGULFING",
        "TWEEZER_TOP", "TWEEZER_BOTTOM",
    ),
    entry_conditions=(
        "Price swept beyond known liquidity level",
        "Immediate rejection (wick-dominant candle)",
        "Volume spike on sweep",
        "Reversal pattern confirms rejection",
    ),
    invalidation_conditions=(
        "Price accepts beyond liquidity level (closes beyond)",
        "Follow-through in sweep direction",
        "No rejection within 2 candles",
    ),
    risk_model=RiskModel(
        stop_loss_method="STRUCTURE",
        risk_reward_minimum=2.0,
        max_risk_per_trade=0.01,
        trailing_stop=False,
        invalidation_type="PRICE_BASED",
        notes="Stop beyond sweep extreme",
    ),
    exit_model=ExitModel(
        take_profit_method="STRUCTURE",
        partial_exit=True,
        time_based_exit=True,
        max_hold_candles=15,
        trailing_exit=False,
        notes="Target previous structure level or midpoint",
    ),
    status=StrategyStatus.HYPOTHESIS,
    evidence_status=EvidenceStatus(
        notes="Requires liquidity level detection (not yet implemented)",
    ),
    version="1.0",
    author="research_engine",
    created_date="2026-07-27",
))


# ─── MOMENTUM FAMILY ─────────────────────────────────────────────────────────

_register(StrategyDefinition(
    strategy_id="momentum_expansion_v1",
    name="Momentum Expansion V1",
    description=(
        "Momentum strategy attempting to exploit directional expansion. "
        "Hypothesis: when multiple strong directional candles appear in an "
        "IMPULSE phase within a TRENDING regime, the move has institutional "
        "commitment and will continue."
    ),
    strategy_family=StrategyFamily.MOMENTUM,
    valid_market_phases=("IMPULSE",),
    required_context=("h4_regime", "market_phase", "h1_bias"),
    trigger_patterns=(
        "THREE_WHITE_SOLDIERS", "THREE_BLACK_CROWS",
    ),
    entry_conditions=(
        "H4 regime is TRENDING",
        "Market phase is IMPULSE",
        "H1 bias aligns with momentum direction",
        "Candle bodies are expanding (not compressing)",
    ),
    invalidation_conditions=(
        "Reversal candle appears",
        "Phase transitions away from IMPULSE",
        "Momentum deceleration (smaller bodies)",
    ),
    risk_model=RiskModel(
        stop_loss_method="ATR_BASED",
        risk_reward_minimum=1.5,
        max_risk_per_trade=0.01,
        trailing_stop=True,
        invalidation_type="STRUCTURE_BREAK",
        notes="Trailing stop at 1.5 ATR behind",
    ),
    exit_model=ExitModel(
        take_profit_method="EXTENSION",
        partial_exit=True,
        time_based_exit=True,
        max_hold_candles=12,
        trailing_exit=True,
        notes="Trail with momentum, partial at 1R and 2R",
    ),
    status=StrategyStatus.HYPOTHESIS,
    evidence_status=EvidenceStatus(
        notes="M9 shows IMPULSE phase has limited data. Requires more evidence.",
    ),
    version="1.0",
    author="research_engine",
    created_date="2026-07-27",
))


# ─── CONTINUATION FAMILY ─────────────────────────────────────────────────────

_register(StrategyDefinition(
    strategy_id="trend_pullback_continuation_v1",
    name="Trend Pullback Continuation V1",
    description=(
        "Continuation strategy attempting to exploit trend resumption after pullback. "
        "Hypothesis: in a TRENDING regime during PULLBACK phase, price will resume "
        "the dominant trend direction after a measured retracement."
    ),
    strategy_family=StrategyFamily.CONTINUATION,
    valid_market_phases=("PULLBACK",),
    required_context=("h4_regime", "market_phase", "h1_bias", "trend_direction"),
    trigger_patterns=(),  # No continuation patterns in library yet
    entry_conditions=(
        "H4 regime is TRENDING",
        "Market phase is PULLBACK",
        "Pullback depth is 38-62% of prior impulse",
        "Trend resumption signal appears",
    ),
    invalidation_conditions=(
        "Pullback exceeds 78% retracement",
        "Regime transitions to RANGE",
        "Phase moves to REVERSAL",
    ),
    risk_model=RiskModel(
        stop_loss_method="STRUCTURE",
        risk_reward_minimum=2.0,
        max_risk_per_trade=0.01,
        trailing_stop=True,
        invalidation_type="PRICE_BASED",
        notes="Stop below pullback low/high",
    ),
    exit_model=ExitModel(
        take_profit_method="EXTENSION",
        partial_exit=True,
        time_based_exit=True,
        max_hold_candles=25,
        trailing_exit=True,
        notes="Target prior impulse extension (1.0-1.618)",
    ),
    status=StrategyStatus.HYPOTHESIS,
    evidence_status=EvidenceStatus(
        notes=(
            "No continuation patterns in library. Cannot test until pattern "
            "detectors are built (Rising Three Methods, etc.)."
        ),
    ),
    version="1.0",
    author="research_engine",
    created_date="2026-07-27",
))


# ─── BREAKOUT FAMILY ─────────────────────────────────────────────────────────

_register(StrategyDefinition(
    strategy_id="range_breakout_v1",
    name="Range Breakout V1",
    description=(
        "Breakout strategy attempting to exploit compression resolution. "
        "Hypothesis: after extended consolidation (RANGE regime), the eventual "
        "breakout produces a strong directional move as trapped participants exit."
    ),
    strategy_family=StrategyFamily.BREAKOUT,
    valid_market_phases=("CONSOLIDATION", "IMPULSE"),
    required_context=("h4_regime", "market_phase", "range_duration", "volume"),
    trigger_patterns=(),  # No breakout patterns in library yet
    entry_conditions=(
        "Extended consolidation period (> 20 candles)",
        "Range compression (narrowing bodies)",
        "Breakout candle closes beyond range with conviction",
        "Volume confirms breakout",
    ),
    invalidation_conditions=(
        "Price re-enters range within 2 candles",
        "False breakout pattern (sweep and return)",
        "Volume dies immediately after breakout",
    ),
    risk_model=RiskModel(
        stop_loss_method="STRUCTURE",
        risk_reward_minimum=2.0,
        max_risk_per_trade=0.01,
        trailing_stop=True,
        invalidation_type="PRICE_BASED",
        notes="Stop inside range (midpoint or opposite boundary)",
    ),
    exit_model=ExitModel(
        take_profit_method="EXTENSION",
        partial_exit=True,
        time_based_exit=True,
        max_hold_candles=15,
        trailing_exit=True,
        notes="Target range height projected from breakout point",
    ),
    status=StrategyStatus.HYPOTHESIS,
    evidence_status=EvidenceStatus(
        notes=(
            "No breakout patterns in library. Requires either new pattern detectors "
            "or price-structure-based triggers rather than candlestick patterns."
        ),
    ),
    version="1.0",
    author="research_engine",
    created_date="2026-07-27",
))


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def get_strategy(strategy_id: str) -> StrategyDefinition | None:
    """Retrieve a strategy by ID. Returns None if not found."""
    return STRATEGY_REGISTRY.get(strategy_id)


def get_all_strategies() -> list[StrategyDefinition]:
    """Return all registered strategies."""
    return list(STRATEGY_REGISTRY.values())


def get_strategies_by_family(family: StrategyFamily) -> list[StrategyDefinition]:
    """Return all strategies belonging to a given family."""
    return [s for s in STRATEGY_REGISTRY.values() if s.strategy_family == family]


def get_strategies_by_status(status: StrategyStatus) -> list[StrategyDefinition]:
    """Return all strategies with a given status."""
    return [s for s in STRATEGY_REGISTRY.values() if s.status == status]


def get_active_strategies() -> list[StrategyDefinition]:
    """Return all ACTIVE strategies. Currently expected to be empty."""
    return get_strategies_by_status(StrategyStatus.ACTIVE)


def get_strategy_ids() -> list[str]:
    """Return all registered strategy IDs."""
    return list(STRATEGY_REGISTRY.keys())


def get_status_distribution() -> dict[str, int]:
    """Return count of strategies per status."""
    from collections import Counter
    counts = Counter(s.status.value for s in STRATEGY_REGISTRY.values())
    # Include all statuses even if zero
    for status in StrategyStatus:
        if status.value not in counts:
            counts[status.value] = 0
    return dict(counts)
