"""
Feature Registry — Defines versioned feature sets for trading logic evolution.

Each feature version represents a complete snapshot of the analytical logic
used to derive trading signals. This enables:
    - Safe upgrades (new logic tagged as v2, old data remains v1)
    - A/B comparison (Athena: GROUP BY feature_version)
    - Replay reproducibility (select feature_version for deterministic backtest)
    - Strategy evolution tracking (no silent drift)

Design Rules:
    - Feature versions are monotonically increasing integers
    - Each version is a FROZEN snapshot — never modified after deployment
    - feature_version is stamped at emit-time and is IMMUTABLE after write
    - Historical data is NEVER re-evaluated with a newer feature version
    - Comparisons across versions require explicit version-aware queries

Version History:
    v1 (baseline): Original production logic. Legacy pattern detector,
       basic regime rules, simple directional bias, baseline EV, v1 scoring.

    v2 (canonical): Enhanced logic deployed with canonical normalisation.
       Normalised pattern detector, multi-state regime, context-aware bias FSM,
       risk-adjusted EV model, weighted confluence scoring.

Usage:
    from core.feature_registry import CURRENT_FEATURE_VERSION, get_feature_set

    version = CURRENT_FEATURE_VERSION  # stamp on events
    feature_set = get_feature_set(version)  # introspect capabilities
"""

from __future__ import annotations

from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# VERSION CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

CURRENT_FEATURE_VERSION: int = 1

# When v2 logic is deployed, change to:
# CURRENT_FEATURE_VERSION: int = 2


# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE SET DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════════════

FEATURE_V1: dict[str, Any] = {
    "version": 1,
    "description": "Baseline production logic — original trading strategy",
    "deployed": "2024-01-01",
    "components": {
        "pattern_engine": {
            "name": "legacy_pattern_detector",
            "description": "Original candlestick pattern detection (engulfing, hammer, star, etc.)",
            "module": "strategy.signal_orchestrator",
        },
        "regime_classifier": {
            "name": "basic_regime_rules",
            "description": "Net-move ratio regime classification (TREND_UP, TREND_DOWN, RANGING)",
            "module": "core.pipeline.market_context",
        },
        "bias_model": {
            "name": "simple_directional_bias",
            "description": "FSM-based bias with building/confirmed/expired phases",
            "module": "core.pipeline.structure_analysis",
        },
        "ev_model": {
            "name": "baseline_ev",
            "description": "Basic expected value calculation (win_rate × avg_win - loss_rate × avg_loss)",
            "module": "core.pipeline.scoring_engine",
        },
        "score_model": {
            "name": "confluence_scoring_v1",
            "description": "Additive confluence scoring with static weights and thresholds",
            "module": "core.pipeline.scoring_engine",
        },
    },
    "guarantees": [
        "Pattern detection uses fixed candlestick rules",
        "Regime is classified from 5-bar net-move ratio only",
        "Bias FSM uses confirmation/contradiction counters",
        "EV is computed from historical win rates",
        "Confluence score is additive with static weights",
    ],
}

FEATURE_V2: dict[str, Any] = {
    "version": 2,
    "description": "Enhanced production logic — canonical normalisation + improved models",
    "deployed": None,  # Set when v2 is promoted to production
    "components": {
        "pattern_engine": {
            "name": "canonical_pattern_detector",
            "description": "Normalised pattern detection with confidence scoring and multi-timeframe context",
            "module": "strategy.signal_orchestrator",
        },
        "regime_classifier": {
            "name": "multi_state_regime_model",
            "description": "Enhanced regime with HTF influence, ATR-ratio, and structure scoring",
            "module": "core.pipeline.market_context",
        },
        "bias_model": {
            "name": "context_aware_bias_fsm",
            "description": "Context-aware bias FSM with voter influence and weight intelligence",
            "module": "core.pipeline.structure_analysis",
        },
        "ev_model": {
            "name": "risk_adjusted_ev",
            "description": "Risk-adjusted EV incorporating regime context, volatility, and position correlation",
            "module": "core.pipeline.scoring_engine",
        },
        "score_model": {
            "name": "weighted_confluence_v2",
            "description": "Weighted confluence scoring with dynamic thresholds and voter calibration",
            "module": "core.pipeline.scoring_engine",
        },
    },
    "guarantees": [
        "Pattern detection includes confidence and multi-timeframe validation",
        "Regime uses HTF context + ATR ratio + structure score",
        "Bias FSM incorporates voter agreement and weight intelligence",
        "EV is risk-adjusted with regime and correlation penalties",
        "Confluence uses dynamic weights from voter calibration",
    ],
}

# Registry of all known feature versions
FEATURE_REGISTRY: dict[int, dict[str, Any]] = {
    1: FEATURE_V1,
    2: FEATURE_V2,
}


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def get_feature_set(version: int) -> dict[str, Any] | None:
    """Get feature set definition by version number."""
    return FEATURE_REGISTRY.get(version)


def get_current_feature_set() -> dict[str, Any]:
    """Get the current active feature set definition."""
    return FEATURE_REGISTRY[CURRENT_FEATURE_VERSION]


def get_component(version: int, component_name: str) -> dict[str, Any] | None:
    """Get a specific component from a feature version."""
    feature_set = FEATURE_REGISTRY.get(version)
    if feature_set is None:
        return None
    return feature_set.get("components", {}).get(component_name)


def list_versions() -> list[int]:
    """List all registered feature versions."""
    return sorted(FEATURE_REGISTRY.keys())
