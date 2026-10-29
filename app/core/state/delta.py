"""
StateDelta — Collected post-decision mutations.

Mutations that currently happen during evaluation (scoring, intent building)
are collected here and applied AFTER the decision is finalized.

This ensures evaluation stages remain pure readers of StateSnapshot.

Ownership: core/state/delta.py
Created by: evaluation stages (scoring_engine, intent_builder)
Applied by: apply_delta() after decision completes
Must NOT contain: decision logic, FSM logic, market analysis
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StateDelta:
    """
    Collected state mutations to apply after evaluation completes.

    Only fields that are written DURING evaluation (not state preparation)
    belong here. FSM mutations happen in state preparation and are NOT
    part of the delta.
    """

    # From scoring_engine (volatility penalty cache)
    volatility_filter: float | None = None

    # From intent_builder (on successful trade)
    last_trade_side: str | None = None
    last_trade_bar: int | None = None
    bias_age_increment: int = 0

    # From intent_builder (on risk rejection)
    failed_setup: tuple[float, float, float, str] | None = None
    last_rejection_zone: tuple[float, float] | None = None


def apply_delta(state, delta: StateDelta) -> None:
    """
    Apply collected mutations to EngineState after decision completes.

    Args:
        state: Live EngineState instance to mutate
        delta: Collected mutations from evaluation phase

    This function is the ONLY place where post-evaluation state
    changes are applied. It runs after the decision is finalized.
    """
    if delta.volatility_filter is not None:
        state.volatility_filter = delta.volatility_filter

    if delta.last_trade_side is not None:
        state.last_trade_side = delta.last_trade_side

    if delta.last_trade_bar is not None:
        state.last_trade_bar = delta.last_trade_bar

    if delta.bias_age_increment > 0:
        state.bias_age += delta.bias_age_increment

    if delta.failed_setup is not None:
        if state.last_failed_setups is not None:
            state.last_failed_setups.append(delta.failed_setup)

    if delta.last_rejection_zone is not None:
        state.last_rejection_zone = delta.last_rejection_zone
