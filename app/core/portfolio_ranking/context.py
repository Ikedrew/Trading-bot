"""
Portfolio Context — Enriches ranking candidates with portfolio-level awareness.

Computes adjustments based on:
    - Correlation with existing positions
    - Currency exposure concentration
    - Diversification benefit
    - Remaining risk budget

This module is PURELY OBSERVATIONAL. It does NOT:
    - Gate or block trades
    - Modify execution decisions
    - Replace the existing ranking score
    - Affect the guard chain

It ONLY computes additional context fields for research comparison:
    "What did the ranker think before and after portfolio context?"
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ─── CONFIGURATION ───────────────────────────────────────────────────────────

_CORRELATION_PENALTY_FACTOR = 0.30     # Reduce rank score by this per correlated position
_CONCENTRATION_PENALTY_FACTOR = 0.20   # Penalty when same-currency exposure is high
_DIVERSIFICATION_BONUS_FACTOR = 0.10   # Bonus for adding exposure to uncorrelated pair
_RISK_BUDGET_PENALTY_THRESHOLD = 0.75  # Penalise when >75% of risk budget used


# ─── CORRELATION GROUPS (mirrored from config for calculation) ─────────────

_DEFAULT_GROUPS = [
    ["EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"],  # USD-short cluster
    ["USDJPY", "USDCHF", "USDCAD"],              # USD-long cluster
]


def _get_correlation_groups() -> list[list[str]]:
    try:
        from core import config
        return list(getattr(config, "CORRELATION_GROUPS", _DEFAULT_GROUPS))
    except ImportError:
        return _DEFAULT_GROUPS


# ─── PORTFOLIO CONTEXT RESULT ─────────────────────────────────────────────────

@dataclass
class PortfolioContext:
    """Portfolio-level state at ranking time."""
    open_positions: list[dict[str, Any]] = field(default_factory=list)
    # Each: {symbol, side, volume}
    total_open: int = 0
    currency_exposure: dict[str, float] = field(default_factory=dict)
    # {USD: +0.02, EUR: -0.01, ...}
    active_correlation_groups: list[str] = field(default_factory=list)
    # Which groups have open positions
    daily_risk_used_pct: float = 0.0
    daily_drawdown_pct: float = 0.0


@dataclass
class CandidateContextEnrichment:
    """Portfolio-aware adjustments for one candidate."""
    symbol: str
    # Penalties/bonuses
    correlation_penalty: float = 0.0    # Negative adjustment for correlated exposure
    exposure_penalty: float = 0.0       # Negative adjustment for concentration
    diversification_bonus: float = 0.0  # Positive adjustment for uncorrelated entry
    risk_adjustment: float = 0.0        # Adjustment based on risk budget remaining
    # Composite
    portfolio_adjustment: float = 0.0   # Sum of all adjustments
    final_rank_score: float = 0.0       # original_rank_score + portfolio_adjustment
    original_rank_score: float = 0.0    # Preserved for comparison
    # Context
    correlated_positions_count: int = 0
    same_currency_exposure: float = 0.0
    is_diversifying: bool = False


# ─── PORTFOLIO CONTEXT BUILDER ─────────────────────────────────────────────────

def build_portfolio_context(
    open_positions: list[Any],
    daily_risk_used_pct: float = 0.0,
    daily_drawdown_pct: float = 0.0,
) -> PortfolioContext:
    """
    Build portfolio state snapshot from open positions.

    Args:
        open_positions: List of Position objects from TradeStateManager
        daily_risk_used_pct: Current daily loss percentage used
        daily_drawdown_pct: Current drawdown from equity high

    Returns:
        PortfolioContext with exposure, correlation, and risk state.
    """
    pos_dicts: list[dict[str, Any]] = []
    currency_exposure: dict[str, float] = {}

    for pos in open_positions:
        symbol = getattr(pos, "symbol", "")
        side = getattr(pos, "side", None)
        side_str = side.value if hasattr(side, "value") else str(side)
        volume = float(getattr(pos, "volume", 0.0))

        pos_dicts.append({"symbol": symbol, "side": side_str, "volume": volume})

        # Compute currency exposure
        _decompose_and_add(currency_exposure, symbol, side_str, volume)

    # Determine active correlation groups
    groups = _get_correlation_groups()
    active_groups: list[str] = []
    position_symbols = {p["symbol"] for p in pos_dicts}
    for group in groups:
        if any(s in position_symbols for s in group):
            active_groups.append(",".join(group[:2]) + "...")

    return PortfolioContext(
        open_positions=pos_dicts,
        total_open=len(pos_dicts),
        currency_exposure={k: round(v, 4) for k, v in currency_exposure.items()},
        active_correlation_groups=active_groups,
        daily_risk_used_pct=round(daily_risk_used_pct, 4),
        daily_drawdown_pct=round(daily_drawdown_pct, 4),
    )


def enrich_candidate(
    *,
    symbol: str,
    direction: str,
    rank_score: float,
    portfolio_ctx: PortfolioContext,
) -> CandidateContextEnrichment:
    """
    Compute portfolio-aware adjustments for one ranking candidate.

    Args:
        symbol: Candidate symbol
        direction: "BUY" or "SELL"
        rank_score: Original rank_score from EV × market_state
        portfolio_ctx: Current portfolio state

    Returns:
        CandidateContextEnrichment with penalties, bonuses, and final score.
    """
    # ─── CORRELATION PENALTY ──────────────────────────────────────────
    groups = _get_correlation_groups()
    correlated_count = 0
    candidate_group: list[str] = []

    for group in groups:
        if symbol in group:
            candidate_group = group
            break

    if candidate_group:
        for pos in portfolio_ctx.open_positions:
            if pos["symbol"] in candidate_group and pos["symbol"] != symbol:
                correlated_count += 1

    correlation_penalty = -(_CORRELATION_PENALTY_FACTOR * correlated_count * abs(rank_score))

    # ─── EXPOSURE PENALTY ─────────────────────────────────────────────
    same_currency_exposure = 0.0
    _pair = _decompose_symbol(symbol)
    if _pair:
        base, quote = _pair
        if direction == "BUY":
            same_currency_exposure = portfolio_ctx.currency_exposure.get(base, 0.0)
        else:
            same_currency_exposure = -portfolio_ctx.currency_exposure.get(base, 0.0)

    # Penalise if adding to already large same-direction exposure
    exposure_penalty = 0.0
    if same_currency_exposure > 0.01:  # Already have >0.01 lots in same direction
        exposure_penalty = -(_CONCENTRATION_PENALTY_FACTOR * abs(rank_score))

    # ─── DIVERSIFICATION BONUS ────────────────────────────────────────
    is_diversifying = correlated_count == 0 and portfolio_ctx.total_open > 0
    diversification_bonus = 0.0
    if is_diversifying:
        diversification_bonus = _DIVERSIFICATION_BONUS_FACTOR * abs(rank_score)

    # ─── RISK BUDGET ADJUSTMENT ───────────────────────────────────────
    risk_adjustment = 0.0
    if portfolio_ctx.daily_risk_used_pct > _RISK_BUDGET_PENALTY_THRESHOLD:
        # When risk budget is mostly consumed, penalise new entries
        overshoot = portfolio_ctx.daily_risk_used_pct - _RISK_BUDGET_PENALTY_THRESHOLD
        risk_adjustment = -(overshoot * abs(rank_score))

    # ─── COMPOSITE ────────────────────────────────────────────────────
    portfolio_adjustment = round(
        correlation_penalty + exposure_penalty + diversification_bonus + risk_adjustment, 8
    )
    final_rank_score = round(rank_score + portfolio_adjustment, 8)

    return CandidateContextEnrichment(
        symbol=symbol,
        correlation_penalty=round(correlation_penalty, 8),
        exposure_penalty=round(exposure_penalty, 8),
        diversification_bonus=round(diversification_bonus, 8),
        risk_adjustment=round(risk_adjustment, 8),
        portfolio_adjustment=portfolio_adjustment,
        final_rank_score=final_rank_score,
        original_rank_score=rank_score,
        correlated_positions_count=correlated_count,
        same_currency_exposure=round(same_currency_exposure, 4),
        is_diversifying=is_diversifying,
    )


# ─── HELPERS ──────────────────────────────────────────────────────────────────

_PAIR_CURRENCIES: dict[str, tuple[str, str]] = {
    "EURUSD": ("EUR", "USD"),
    "GBPUSD": ("GBP", "USD"),
    "AUDUSD": ("AUD", "USD"),
    "NZDUSD": ("NZD", "USD"),
    "USDJPY": ("USD", "JPY"),
    "USDCHF": ("USD", "CHF"),
    "USDCAD": ("USD", "CAD"),
}


def _decompose_symbol(symbol: str) -> tuple[str, str] | None:
    clean = symbol.replace("_SB", "").replace("_sb", "")
    if clean in _PAIR_CURRENCIES:
        return _PAIR_CURRENCIES[clean]
    if len(clean) >= 6:
        return clean[:3].upper(), clean[3:6].upper()
    return None


def _decompose_and_add(
    exposure: dict[str, float], symbol: str, side: str, volume: float
) -> None:
    pair = _decompose_symbol(symbol)
    if pair is None:
        return
    base, quote = pair
    if side == "BUY":
        exposure[base] = exposure.get(base, 0.0) + volume
        exposure[quote] = exposure.get(quote, 0.0) - volume
    else:
        exposure[base] = exposure.get(base, 0.0) - volume
        exposure[quote] = exposure.get(quote, 0.0) + volume
