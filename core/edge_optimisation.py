"""
Edge Optimisation — Statistical Edge Discovery Layer.

A purely aggregated learning system that identifies persistent causal
relationships between features and performance using outputs from
edge_attribution/ and trade_truth/.

CONTAINS ONLY:
    1. Aggregated edge statistics (rolling windows, sample sizes)
    2. Feature edge performance (mean attribution, stability, win rates)
    3. Regime-level performance breakdowns
    4. Causal edge weights (derived, aggregated only)
    5. Portfolio edge metrics (expectancy, decay, consistency)

NEVER CONTAINS:
    - Individual trades or trade IDs
    - Entry/exit prices, pnl per trade, r_multiple per trade
    - Strategy definitions or decision logic
    - Indicator snapshots, HTF data, shadow trade references
    - Event-level data or execution_context references

DATA SOURCES (read-only):
    - edge_attribution/ (aggregated causal scores)
    - trade_truth/ (aggregated outcome stats only — never row-level)

KEY PRINCIPLE:
    Every record is: "A summary of how a feature behaves across many outcomes"
    NOT: "What happened in a trade"

GUARANTEE:
    You can delete all raw trades — edge_optimisation still works.
    System still knows what edges are strong but cannot reconstruct
    individual trades.

S3: s3://trading-bot-data-mk1/edge_optimisation/{YYYY-MM-DD}.jsonl
Local: logs/edge_optimisation/{YYYY-MM-DD}.jsonl

Usage:
    from core.edge_optimisation import build_edge_report, persist_edge_report

    report = build_edge_report(
        symbol="EURUSD",
        attributions=load_attributions(symbol="EURUSD"),
        ...
    )
    persist_edge_report(report)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_S3_BUCKET = "trading-bot-data-mk1"
_S3_PREFIX = "edge_optimisation"
_LOCAL_DIR = "logs/edge_optimisation"
_SCHEMA_VERSION = "edge_optimisation_v2"

# Minimum sample for a feature to be statistically meaningful
MIN_SAMPLE_SIZE = 5


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
    # Decision data
    "strategy_id", "strategy", "pattern", "confluence_score", "score",
    "decision_context", "decision_snapshot",
    "htf_context", "htf_snapshot", "H4_bias", "H1_bias", "M15_bias",
    "alignment_score", "bias", "indicators",
    # Simulation data
    "simulated_outcome", "shadow_trade_ref", "simulation_environment",
    # Raw market data
    "candles", "ohlcv", "atr", "rsi", "feature_state",
    # Execution context
    "execution_context_ref", "execution_context",
    # Legacy
    "legacy", "final_r", "derived_metrics", "risk_model",
})


# ═══════════════════════════════════════════════════════════════════════════════
# EDGE REPORT BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

def build_edge_report(
    *,
    symbol: str,
    attributions: list[dict[str, Any]],
    window_label: str = "",
) -> dict[str, Any]:
    """
    Build an aggregated edge discovery report from attribution records.

    Compresses thousands of causal attributions into stable probabilistic
    edge weights. Individual trades are NOT identifiable from the output.

    Args:
        symbol: Trading instrument
        attributions: List of edge_attribution_v2 records (read-only input)
        window_label: Optional label for this rolling window

    Returns:
        Aggregated edge report conforming to edge_optimisation_v2 schema.
    """
    if not attributions:
        return _empty_report(symbol, window_label)

    now = datetime.now(timezone.utc)
    window_id = f"WIN-{hashlib.md5(f'{symbol}:{now.isoformat()}:{len(attributions)}'.encode()).hexdigest()[:8].upper()}"

    # Extract time range from attributions
    timestamps = [a.get("timestamp_reference", 0) for a in attributions if a.get("timestamp_reference", 0) > 0]
    time_start = min(timestamps) if timestamps else 0
    time_end = max(timestamps) if timestamps else 0

    # ─── FEATURE EDGE PERFORMANCE (aggregated) ────────────────────────
    feature_edges = _compute_feature_edges(attributions)

    # ─── REGIME-LEVEL BREAKDOWNS ──────────────────────────────────────
    regime_breakdowns = _compute_regime_breakdowns(attributions)

    # ─── CAUSAL EDGE WEIGHTS ──────────────────────────────────────────
    causal_weights = _compute_causal_weights(feature_edges)

    # ─── PORTFOLIO EDGE METRICS ───────────────────────────────────────
    portfolio_metrics = _compute_portfolio_metrics(attributions, feature_edges)

    return {
        "schema_version": _SCHEMA_VERSION,

        # Domain 1: Aggregated edge statistics
        "statistics": {
            "rolling_window_id": window_id,
            "window_label": window_label or f"{symbol}_latest",
            "sample_size": len(attributions),
            "symbol": symbol,
            "time_range": {
                "start_ts": time_start,
                "end_ts": time_end,
            },
            "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },

        # Domain 2: Feature edge performance
        "feature_edges": feature_edges,

        # Domain 3: Regime-level breakdowns
        "regime_breakdowns": regime_breakdowns,

        # Domain 4: Causal edge weights
        "causal_weights": causal_weights,

        # Domain 5: Portfolio edge metrics
        "portfolio_metrics": portfolio_metrics,
    }


def _empty_report(symbol: str, window_label: str) -> dict[str, Any]:
    """Return empty report when no data available."""
    return {
        "schema_version": _SCHEMA_VERSION,
        "statistics": {
            "rolling_window_id": "WIN-EMPTY",
            "window_label": window_label or f"{symbol}_empty",
            "sample_size": 0,
            "symbol": symbol,
            "time_range": {"start_ts": 0, "end_ts": 0},
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "feature_edges": {},
        "regime_breakdowns": [],
        "causal_weights": {},
        "portfolio_metrics": {
            "overall_expectancy": 0.0,
            "edge_decay_rate": 0.0,
            "edge_consistency_index": 0.0,
            "signal_to_noise_ratio": 0.0,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE EDGE COMPUTATION (aggregated only)
# ═══════════════════════════════════════════════════════════════════════════════

_ATTRIBUTION_FEATURES = [
    "structure_strength", "breakout_quality", "trend_alignment_score",
    "session_effect", "liquidity_window_strength",
    "regime_fit_score", "regime_stability_impact",
    "volatility_regime_score", "volatility_misfit_penalty",
    "event_alignment_score", "pre_event_drift_effect",
]


def _compute_feature_edges(attributions: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute per-feature aggregated statistics from attributions."""
    feature_data: dict[str, list[float]] = defaultdict(list)

    for attr in attributions:
        attr_section = attr.get("attribution", {})
        for feature in _ATTRIBUTION_FEATURES:
            val = attr_section.get(feature, 0.0)
            if isinstance(val, (int, float)):
                feature_data[feature].append(val)

    edges: dict[str, Any] = {}
    for feature, values in feature_data.items():
        if len(values) < MIN_SAMPLE_SIZE:
            continue

        mean_val = sum(values) / len(values)
        variance = sum((v - mean_val) ** 2 for v in values) / len(values)
        volatility = variance ** 0.5
        positive_count = sum(1 for v in values if v > 0)
        negative_count = sum(1 for v in values if v < 0)

        # Win rate when feature is positive/negative
        win_rate_positive = positive_count / len(values) if values else 0
        win_rate_negative = negative_count / len(values) if values else 0

        # Stability: how consistent is the feature across samples
        if volatility > 0:
            stability = min(1.0, abs(mean_val) / volatility)
        else:
            stability = 1.0 if mean_val != 0 else 0.0

        edges[feature] = {
            "mean_attribution_score": round(mean_val, 4),
            "attribution_volatility": round(volatility, 4),
            "win_rate_when_feature_positive": round(win_rate_positive, 4),
            "win_rate_when_feature_negative": round(win_rate_negative, 4),
            "expectancy_delta": round(mean_val, 4),
            "stability_score": round(stability, 4),
            "sample_count": len(values),
        }

    return edges


# ═══════════════════════════════════════════════════════════════════════════════
# REGIME BREAKDOWNS (aggregated)
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_regime_breakdowns(attributions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compute regime-level performance from attribution classification data."""
    # Group by attribution quality as a proxy for regime conditions
    by_quality: dict[str, list[float]] = defaultdict(list)

    for attr in attributions:
        quality = attr.get("classification", {}).get("attribution_quality", "UNKNOWN")
        net_score = attr.get("aggregate", {}).get("net_attribution_score", 0)
        by_quality[quality].append(net_score)

    breakdowns = []
    for regime_type, scores in sorted(by_quality.items()):
        if len(scores) < MIN_SAMPLE_SIZE:
            continue

        mean_score = sum(scores) / len(scores)
        variance = sum((s - mean_score) ** 2 for s in scores) / len(scores)
        stability = min(1.0, abs(mean_score) / (variance ** 0.5)) if variance > 0 else 1.0

        breakdowns.append({
            "regime_type": regime_type,
            "regime_edge_strength": round(mean_score, 4),
            "regime_expectancy": round(mean_score, 4),
            "regime_stability": round(stability, 4),
            "transition_penalty": 0.0,  # Computed from sequential data when available
            "sample_count": len(scores),
        })

    return breakdowns


# ═══════════════════════════════════════════════════════════════════════════════
# CAUSAL EDGE WEIGHTS (derived aggregates only)
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_causal_weights(feature_edges: dict[str, Any]) -> dict[str, Any]:
    """Compute normalised causal weights from feature edge statistics."""
    if not feature_edges:
        return {}

    # Normalise by absolute mean score
    total_abs = sum(abs(f.get("mean_attribution_score", 0)) for f in feature_edges.values())
    if total_abs == 0:
        return {f: {"outcome_strength": 0.0} for f in feature_edges}

    weights: dict[str, Any] = {}
    for feature, stats in feature_edges.items():
        mean = stats.get("mean_attribution_score", 0)
        weights[feature] = {
            "outcome_strength": round(mean / total_abs, 4) if total_abs > 0 else 0.0,
            "stability_weight": stats.get("stability_score", 0.0),
        }

    return weights


# ═══════════════════════════════════════════════════════════════════════════════
# PORTFOLIO EDGE METRICS
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_portfolio_metrics(
    attributions: list[dict[str, Any]],
    feature_edges: dict[str, Any],
) -> dict[str, Any]:
    """Compute portfolio-level edge metrics."""
    if not attributions:
        return {
            "overall_expectancy": 0.0,
            "edge_decay_rate": 0.0,
            "edge_consistency_index": 0.0,
            "signal_to_noise_ratio": 0.0,
        }

    # Overall expectancy: mean net attribution across all records
    net_scores = [a.get("aggregate", {}).get("net_attribution_score", 0) for a in attributions]
    overall_exp = sum(net_scores) / len(net_scores) if net_scores else 0

    # Edge consistency: fraction of attributions with HIGH quality
    high_quality = sum(
        1 for a in attributions
        if a.get("classification", {}).get("attribution_quality") == "HIGH"
    )
    consistency = high_quality / len(attributions) if attributions else 0

    # Signal-to-noise: ratio of explained vs unexplained variance
    confidences = [a.get("aggregate", {}).get("attribution_confidence", 0) for a in attributions]
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0
    snr = avg_confidence / (1 - avg_confidence) if avg_confidence < 1 else 10.0

    # Edge decay: compare first half vs second half expectancy
    half = len(net_scores) // 2
    if half > 0:
        first_half = sum(net_scores[:half]) / half
        second_half = sum(net_scores[half:]) / (len(net_scores) - half)
        decay = round(first_half - second_half, 4) if first_half != 0 else 0.0
    else:
        decay = 0.0

    return {
        "overall_expectancy": round(overall_exp, 4),
        "edge_decay_rate": decay,
        "edge_consistency_index": round(consistency, 4),
        "signal_to_noise_ratio": round(snr, 4),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

def validate_edge_report(record: dict[str, Any]) -> tuple[bool, str]:
    """
    Validate an edge report before persistence.

    Rejects if:
        - Any forbidden field present (individual trades, prices, etc.)
        - Required sections missing
        - Schema version wrong
    """
    if record.get("schema_version") != _SCHEMA_VERSION:
        return False, f"invalid_schema_version: expected {_SCHEMA_VERSION}"

    # Required sections
    for section in ("statistics", "feature_edges", "portfolio_metrics"):
        if section not in record:
            return False, f"missing_section:{section}"

    # Statistics must have sample_size
    stats = record.get("statistics", {})
    if not isinstance(stats, dict) or "sample_size" not in stats:
        return False, "missing_sample_size"
    if not stats.get("symbol"):
        return False, "missing_symbol"

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

def persist_edge_report(record: dict[str, Any]) -> bool:
    """
    Persist a validated edge optimisation report.

    Validates before write. Append-only. Immutable.
    """
    valid, reason = validate_edge_report(record)
    if not valid:
        logger.warning("[EDGE_OPTIMISATION] rejected: %s", reason)
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

        return True
    except Exception as exc:
        logger.debug("[EDGE_OPTIMISATION] persist_failed: %s", exc)
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# READER
# ═══════════════════════════════════════════════════════════════════════════════

def load_edge_reports(*, local_dir: str = _LOCAL_DIR) -> list[dict[str, Any]]:
    """Load edge optimisation reports from local JSONL. Read-only."""
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
