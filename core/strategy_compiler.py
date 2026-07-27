"""
Strategy Compiler — Strategy Generation and Evolution Layer.

A constrained synthesis engine that converts stable statistical edges
from edge_optimisation/ into parameterised, testable strategy definitions.

CONTAINS ONLY:
    1. Strategy identity (version, source window)
    2. Edge-selection basis (which features were chosen and why)
    3. Parameterised ruleset (boolean logic conditions — no raw data)
    4. Feature weight map (derived from edge_optimisation causal_weights)
    5. Strategy expectation model (abstract model-level summaries)
    6. Version evolution history (mutation tracking)

NEVER CONTAINS:
    - Individual trades or trade IDs
    - PnL, execution data, shadow trades, trade_truth references
    - Entry/exit prices, indicator snapshots, raw market data
    - Causal attribution values per trade
    - Event windows, HTF data, regression outputs per trade

DATA SOURCES (read-only):
    - edge_optimisation/ (stable edge statistics only)

KEY PRINCIPLE:
    Strategies are compressed rule systems derived from stable edges.
    NOT fitted models on historical trades.

GUARANTEE:
    Strategies are portable across time, independent of individual trade
    history. System cannot overfit to single trade sequences. Evolution
    is driven only by edge stability.

S3: s3://trading-bot-data-mk1/strategy_compiler/{YYYY-MM-DD}.jsonl
Local: logs/strategy_compiler/{YYYY-MM-DD}.jsonl

Usage:
    from core.strategy_compiler import compile_strategy, persist_strategy

    strategy = compile_strategy(edge_report=report)
    persist_strategy(strategy)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_S3_BUCKET = "trading-bot-data-mk1"
_S3_PREFIX = "strategy_compiler"
_LOCAL_DIR = "logs/strategy_compiler"
_SCHEMA_VERSION = "strategy_compiler_v2"

# Thresholds for feature inclusion
_MIN_STABILITY = 0.3
_MIN_MEAN_SCORE = 0.1
_MIN_SAMPLE = 5


# ═══════════════════════════════════════════════════════════════════════════════
# FORBIDDEN FIELDS (reject at write time)
# ═══════════════════════════════════════════════════════════════════════════════

_FORBIDDEN_FIELDS = frozenset({
    # Individual trade data
    "trade_id", "entry_price", "exit_price", "entry_fill_price", "exit_fill_price",
    "slippage", "slippage_entry", "slippage_exit",
    "spread", "spread_at_entry", "spread_at_exit",
    "volume", "volume_executed", "lot_size", "position_size",
    "pnl", "pnl_realised", "pnl_price", "net_profit",
    "r_multiple", "r_multiple_realised", "pnl_r_multiple",
    "commission", "swap",
    # Decision/execution context
    "decision_context", "execution_context_ref", "execution_context",
    "htf_context", "htf_snapshot", "H4_bias", "H1_bias", "M15_bias",
    "indicators", "candles", "ohlcv",
    # Trade references
    "shadow_trade_ref", "trade_truth_ref", "correlation_id",
    "simulation_environment", "simulated_outcome",
    # Per-trade attribution
    "attribution_id", "actual_r", "outcome_r",
    # Legacy
    "legacy", "final_r",
})


# ═══════════════════════════════════════════════════════════════════════════════
# STRATEGY COMPILER
# ═══════════════════════════════════════════════════════════════════════════════

def compile_strategy(
    *,
    edge_report: dict[str, Any],
    parent_strategy_id: str = "",
    mutation_type: str = "initial_compilation",
    reason_code: str = "first_generation",
) -> dict[str, Any]:
    """
    Compile a parameterised strategy from an edge_optimisation report.

    Converts stable statistical edges into executable boolean rule systems.
    Does NOT analyse trades. Does NOT store outcomes.

    Args:
        edge_report: An edge_optimisation_v2 report (read-only input)
        parent_strategy_id: Previous strategy version this derives from
        mutation_type: How this strategy differs from parent
        reason_code: Why the mutation was triggered

    Returns:
        Strategy definition conforming to strategy_compiler_v2 schema.
    """
    now = datetime.now(timezone.utc)
    feature_edges = edge_report.get("feature_edges", {})
    causal_weights = edge_report.get("causal_weights", {})
    portfolio = edge_report.get("portfolio_metrics", {})
    stats = edge_report.get("statistics", {})

    # Generate strategy ID from content hash
    content_hash = hashlib.sha256(
        json.dumps(feature_edges, sort_keys=True, default=str).encode()
    ).hexdigest()[:8].upper()
    strategy_id = f"STRAT-{now.strftime('%Y%m%d')}-{content_hash}"

    # ─── EDGE SELECTION ───────────────────────────────────────────────
    selected, excluded = _select_features(feature_edges)

    # ─── PARAMETERISED RULESET ────────────────────────────────────────
    entry_conditions = _build_entry_conditions(selected, feature_edges)
    exit_conditions = _build_exit_conditions(selected)
    regime_filters = _build_regime_filters(edge_report.get("regime_breakdowns", []))

    # ─── FEATURE WEIGHT MAP ───────────────────────────────────────────
    weight_map = _build_weight_map(selected, causal_weights)

    # ─── EXPECTATION MODEL (abstract only) ────────────────────────────
    expectation = _build_expectation_model(portfolio, feature_edges, selected)

    return {
        "schema_version": _SCHEMA_VERSION,

        # Domain 1: Strategy identity
        "identity": {
            "strategy_id": strategy_id,
            "version": 1,
            "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source_edge_window_id": stats.get("rolling_window_id", ""),
        },

        # Domain 2: Edge-selection basis
        "edge_selection": {
            "selected_features": selected,
            "excluded_features": excluded,
            "selection_criteria": {
                "min_stability_score": _MIN_STABILITY,
                "min_mean_attribution": _MIN_MEAN_SCORE,
                "min_sample_count": _MIN_SAMPLE,
            },
        },

        # Domain 3: Parameterised ruleset
        "ruleset": {
            "entry_conditions": entry_conditions,
            "exit_conditions": exit_conditions,
            "risk_parameters": {
                "max_concurrent_positions": 1,
                "base_reward_risk": 2.0,
            },
            "regime_filters": regime_filters,
        },

        # Domain 4: Feature weight map
        "feature_weights": weight_map,

        # Domain 5: Strategy expectation model
        "expectation_model": expectation,

        # Domain 6: Version evolution history
        "evolution": {
            "parent_strategy_id": parent_strategy_id,
            "mutation_type": mutation_type,
            "reason_code": reason_code,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE SELECTION
# ═══════════════════════════════════════════════════════════════════════════════

def _select_features(feature_edges: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Select features meeting stability + strength thresholds."""
    selected = []
    excluded = []

    for feature, stats in feature_edges.items():
        if not isinstance(stats, dict):
            excluded.append(feature)
            continue

        stability = stats.get("stability_score", 0)
        mean_score = abs(stats.get("mean_attribution_score", 0))
        sample = stats.get("sample_count", 0)

        if stability >= _MIN_STABILITY and mean_score >= _MIN_MEAN_SCORE and sample >= _MIN_SAMPLE:
            selected.append(feature)
        else:
            excluded.append(feature)

    # Sort by absolute mean score (strongest first)
    selected.sort(key=lambda f: abs(feature_edges[f].get("mean_attribution_score", 0)), reverse=True)
    return selected[:7], excluded  # Cap at 7 features to prevent overfitting


# ═══════════════════════════════════════════════════════════════════════════════
# RULESET BUILDERS
# ═══════════════════════════════════════════════════════════════════════════════

def _build_entry_conditions(selected: list[str], feature_edges: dict[str, Any]) -> list[dict[str, Any]]:
    """Build boolean entry conditions from selected features."""
    conditions = []
    for feature in selected:
        stats = feature_edges.get(feature, {})
        mean = stats.get("mean_attribution_score", 0)

        # Direction-aware threshold
        if mean >= 0:
            conditions.append({
                "feature": feature,
                "operator": ">",
                "threshold": round(_MIN_MEAN_SCORE, 4),
                "direction": "positive",
            })
        else:
            conditions.append({
                "feature": feature,
                "operator": "<",
                "threshold": round(-_MIN_MEAN_SCORE, 4),
                "direction": "negative",
            })

    return conditions


def _build_exit_conditions(selected: list[str]) -> list[dict[str, str]]:
    """Build abstract exit conditions."""
    return [
        {"condition": "stop_loss_hit", "type": "risk"},
        {"condition": "take_profit_hit", "type": "target"},
        {"condition": "max_bars_exceeded", "type": "time"},
        {"condition": "regime_shift_detected", "type": "environment"},
    ]


def _build_regime_filters(regime_breakdowns: list[dict[str, Any]]) -> dict[str, Any]:
    """Build regime filter rules from breakdown data."""
    allowed_regimes = []
    blocked_regimes = []

    for breakdown in regime_breakdowns:
        regime = breakdown.get("regime_type", "")
        stability = breakdown.get("regime_stability", 0)
        strength = breakdown.get("regime_edge_strength", 0)

        if stability >= 0.5 and strength > 0:
            allowed_regimes.append(regime)
        elif strength < 0:
            blocked_regimes.append(regime)

    return {
        "allowed_regimes": allowed_regimes,
        "blocked_regimes": blocked_regimes,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# WEIGHT MAP
# ═══════════════════════════════════════════════════════════════════════════════

def _build_weight_map(selected: list[str], causal_weights: dict[str, Any]) -> dict[str, Any]:
    """Build normalised weight map from causal weights."""
    weights = {}
    for feature in selected:
        cw = causal_weights.get(feature, {})
        weights[feature] = {
            "weight": cw.get("outcome_strength", 0.0),
            "directionality": "positive" if cw.get("outcome_strength", 0) >= 0 else "negative",
            "stability_confidence": cw.get("stability_weight", 0.0),
        }
    return weights


# ═══════════════════════════════════════════════════════════════════════════════
# EXPECTATION MODEL (abstract — no trade predictions)
# ═══════════════════════════════════════════════════════════════════════════════

def _build_expectation_model(
    portfolio: dict[str, Any],
    feature_edges: dict[str, Any],
    selected: list[str],
) -> dict[str, Any]:
    """Build abstract strategy-level expectation model."""
    # Average stability of selected features
    stabilities = [
        feature_edges.get(f, {}).get("stability_score", 0)
        for f in selected
    ]
    avg_stability = sum(stabilities) / len(stabilities) if stabilities else 0

    return {
        "expected_edge_score": portfolio.get("overall_expectancy", 0.0),
        "expected_stability": round(avg_stability, 4),
        "expected_drawdown_band": "MODERATE",  # Abstract classification
        "expected_regime_dependency": "MEDIUM" if len(selected) > 3 else "LOW",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

def validate_strategy(record: dict[str, Any]) -> tuple[bool, str]:
    """
    Validate a strategy before persistence.

    Rejects if:
        - Any forbidden field present
        - Required sections missing
        - Schema version wrong
    """
    if record.get("schema_version") != _SCHEMA_VERSION:
        return False, f"invalid_schema_version: expected {_SCHEMA_VERSION}"

    # Required sections
    for section in ("identity", "edge_selection", "ruleset", "feature_weights", "expectation_model", "evolution"):
        if section not in record or not isinstance(record[section], dict):
            return False, f"missing_section:{section}"

    # Identity must have strategy_id
    if not record["identity"].get("strategy_id"):
        return False, "missing_strategy_id"

    # Forbidden field scan
    forbidden = _scan_forbidden(record)
    if forbidden:
        return False, forbidden

    return True, "valid"


def _scan_forbidden(d: dict[str, Any], path: str = "") -> str | None:
    """Recursively scan for forbidden fields."""
    for k, v in d.items():
        if k in _FORBIDDEN_FIELDS:
            return f"forbidden_field:{path}{k}"
        if isinstance(v, dict):
            result = _scan_forbidden(v, f"{path}{k}.")
            if result:
                return result
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE (local + S3)
# ═══════════════════════════════════════════════════════════════════════════════

def persist_strategy(record: dict[str, Any]) -> bool:
    """
    Persist a validated strategy definition.

    Validates before write. Append-only. Immutable.
    """
    valid, reason = validate_strategy(record)
    if not valid:
        logger.warning("[STRATEGY_COMPILER] rejected: %s", reason)
        return False

    try:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        local_path = Path(_LOCAL_DIR) / f"{date_str}.jsonl"
        local_path.parent.mkdir(parents=True, exist_ok=True)

        line = json.dumps(record, separators=(",", ":"), default=str) + "\n"
        with open(local_path, "a", encoding="utf-8") as f:
            f.write(line)

        # S3 mirror
        try:
            from core import config as _cfg
            if getattr(_cfg, "EVENT_STREAM_S3_MIRROR", False):
                import boto3
                s3 = boto3.client("s3", region_name=os.getenv("AWS_REGION", "eu-west-2"))
                key = f"{_S3_PREFIX}/schema_version={_SCHEMA_VERSION}/date={date_str}/part-000.jsonl"
                try:
                    existing = s3.get_object(Bucket=_S3_BUCKET, Key=key)
                    body = existing["Body"].read().decode("utf-8") + line
                except Exception:
                    body = line
                s3.put_object(Bucket=_S3_BUCKET, Key=key, Body=body.encode("utf-8"), ContentType="application/x-ndjson")
        except Exception:
            pass

        logger.info(
            "[STRATEGY_COMPILER] persisted strategy_id=%s features=%d",
            record["identity"]["strategy_id"],
            len(record["edge_selection"]["selected_features"]),
        )
        return True
    except Exception as exc:
        logger.debug("[STRATEGY_COMPILER] persist_failed: %s", exc)
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# READER
# ═══════════════════════════════════════════════════════════════════════════════

def load_strategies(*, local_dir: str = _LOCAL_DIR) -> list[dict[str, Any]]:
    """Load strategy definitions from local JSONL. Read-only."""
    records: list[dict[str, Any]] = []
    path = Path(local_dir)
    if not path.exists():
        return records

    for f in sorted(path.glob("*.jsonl")):
        with open(f, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if rec.get("schema_version") == _SCHEMA_VERSION:
                        records.append(rec)
                except json.JSONDecodeError:
                    continue

    return records
