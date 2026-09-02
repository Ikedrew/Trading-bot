"""
Q1: Component → Reward Correlation Experiment

Question: "Which decision components are correlated with positive trade outcomes?"

Joins decision_trace records (containing 10-factor component scores) with
shadow trade outcomes (R-multiples) via correlation_id to determine which
scoring components have genuine predictive value.

Produces:
    Q1.1 — Per-component win rate, avg R, and sample size
    Q1.2 — Negative contributors (components active in losing trades)
    Q1.3 — Component interaction analysis (aligned vs isolated)

Data sources:
    - logs/decision_trace/{SYMBOL}/{DATE}.jsonl (component scores)
    - logs/shadow_trades/{SYMBOL}/{DATE}.jsonl (R-multiple outcomes)
    Join key: correlation_id (primary), entity_id + cycle_id (fallback)

This module ONLY reads data and produces analysis.
It does NOT modify trading behaviour.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# ATTRIBUTION RECORD
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class AttributionRecord:
    """A single decision linked to its outcome for component analysis."""

    # Identity
    correlation_id: str
    symbol: str
    cycle_id: int
    entity_id: str = ""
    timestamp_utc: str = ""

    # Decision context
    action: str = "NO_TRADE"
    pattern_name: str = ""
    regime: str = ""
    market_state: str = ""
    selected_strategy: str = ""
    strategy_confidence: float = 0.0

    # Component scores (the 10-factor model)
    components: dict[str, float] = field(default_factory=dict)
    score_neutral: float = 0.0
    score_strategy: float = 0.0

    # Outcome (from shadow trade)
    has_outcome: bool = False
    r_multiple: float = 0.0
    exit_reason: str = ""
    bars_held: int = 0
    mfe_r: float = 0.0
    mae_r: float = 0.0
    win: bool = False


# ═══════════════════════════════════════════════════════════════════════════════
# COMPONENT STATISTICS
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ComponentStats:
    """Statistics for one scoring component."""
    name: str
    sample_size: int = 0
    win_rate: float = 0.0
    avg_r: float = 0.0
    avg_r_when_high: float = 0.0   # R when component > median
    avg_r_when_low: float = 0.0    # R when component <= median
    correlation: float | None = None  # Pearson correlation with R
    predictive_value: float = 0.0  # avg_r_when_high - avg_r_when_low


@dataclass
class InteractionStats:
    """Statistics for a component combination."""
    components: tuple[str, ...]
    label: str
    sample_size: int = 0
    win_rate: float = 0.0
    avg_r: float = 0.0


@dataclass
class ComponentRewardResult:
    """Result of Q1 Component → Reward Correlation experiment."""

    # Dataset
    total_decisions: int = 0
    decisions_with_outcome: int = 0
    join_rate: float = 0.0

    # Q1.1 — Per-component analysis
    component_stats: list[ComponentStats] = field(default_factory=list)

    # Q1.2 — Negative contributors
    negative_contributors: list[ComponentStats] = field(default_factory=list)

    # Q1.3 — Interactions
    interactions: list[InteractionStats] = field(default_factory=list)

    # Summary
    best_predictor: str = ""
    worst_predictor: str = ""
    conclusion: str = ""
    confidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_decisions": self.total_decisions,
            "decisions_with_outcome": self.decisions_with_outcome,
            "join_rate": round(self.join_rate, 4),
            "component_stats": [
                {
                    "name": s.name,
                    "sample_size": s.sample_size,
                    "win_rate": round(s.win_rate, 4),
                    "avg_r": round(s.avg_r, 4),
                    "avg_r_when_high": round(s.avg_r_when_high, 4),
                    "avg_r_when_low": round(s.avg_r_when_low, 4),
                    "correlation": round(s.correlation, 4) if s.correlation is not None else None,
                    "predictive_value": round(s.predictive_value, 4),
                }
                for s in self.component_stats
            ],
            "negative_contributors": [
                {
                    "name": s.name,
                    "avg_r_when_high": round(s.avg_r_when_high, 4),
                    "avg_r_when_low": round(s.avg_r_when_low, 4),
                    "predictive_value": round(s.predictive_value, 4),
                    "sample_size": s.sample_size,
                }
                for s in self.negative_contributors
            ],
            "interactions": [
                {
                    "label": i.label,
                    "components": list(i.components),
                    "sample_size": i.sample_size,
                    "win_rate": round(i.win_rate, 4),
                    "avg_r": round(i.avg_r, 4),
                }
                for i in self.interactions
            ],
            "best_predictor": self.best_predictor,
            "worst_predictor": self.worst_predictor,
            "conclusion": self.conclusion,
            "confidence": self.confidence,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# ATTRIBUTION LINKER
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_correlation_id(record: dict[str, Any]) -> str:
    """Extract correlation_id from a record (handles nested schemas)."""
    cor = record.get("correlation_id", "")
    if cor:
        return str(cor)
    identity = record.get("identity", {})
    if isinstance(identity, dict):
        cor = identity.get("correlation_id", "")
        if cor:
            return str(cor)
    return ""


def _extract_r_multiple(record: dict[str, Any]) -> float | None:
    """Extract R-multiple from a shadow trade or trade_truth record."""
    # shadow_trades_v1 (simulated_outcome block)
    simulated = record.get("simulated_outcome", {})
    if isinstance(simulated, dict):
        r = simulated.get("pnl_r_multiple")
        if r is not None:
            return float(r)
    # trade_truth_v1 (outcome block)
    outcome = record.get("outcome", {})
    if isinstance(outcome, dict):
        r = outcome.get("r_multiple") or outcome.get("pnl_r_multiple")
        if r is not None:
            return float(r)
    # Flat field
    r = record.get("pnl_r_multiple")
    if r is not None:
        return float(r)
    return None


def _extract_outcome_fields(record: dict[str, Any]) -> dict[str, Any]:
    """Extract outcome fields from a shadow trade record."""
    simulated = record.get("simulated_outcome", {})
    outcome = record.get("outcome", {})
    src = simulated if simulated else outcome

    return {
        "exit_reason": src.get("exit_reason", record.get("exit_reason", "")),
        "bars_held": int(src.get("bars_held", record.get("bars_held", 0))),
        "mfe_r": float(src.get("mfe_r", 0.0)),
        "mae_r": float(src.get("mae_r", 0.0)),
    }


def build_attribution_records(
    decision_traces: list[dict[str, Any]],
    shadow_trades: list[dict[str, Any]],
) -> list[AttributionRecord]:
    """
    Join decision_trace records with shadow trade outcomes via correlation_id.

    Primary join key: correlation_id
    Fallback: entity_id match (if correlation_id missing on older records)

    Returns AttributionRecords with component scores + outcomes.
    """
    # Index shadow trades by correlation_id
    shadow_by_cor: dict[str, dict[str, Any]] = {}
    shadow_by_entity: dict[str, dict[str, Any]] = {}

    for shadow in shadow_trades:
        cor_id = _extract_correlation_id(shadow)
        if cor_id:
            shadow_by_cor[cor_id] = shadow
        # Also index by cycle_id + symbol as fallback
        identity = shadow.get("identity", shadow)
        cycle = str(identity.get("cycle_id", shadow.get("cycle_id", "")))
        sym = identity.get("symbol", shadow.get("symbol", ""))
        if cycle and sym:
            shadow_by_entity[f"{sym}_{cycle}"] = shadow

    # Build attribution records from decision traces
    records: list[AttributionRecord] = []

    for trace in decision_traces:
        # Only include traces where pattern was detected (non-trivial decisions)
        if not trace.get("pattern_detected", False):
            continue

        components = trace.get("components", {})
        if not components:
            continue

        cor_id = _extract_correlation_id(trace)
        entity_id = trace.get("entity_id", "")
        cycle_id = int(trace.get("cycle_id", 0))
        symbol = trace.get("symbol", "")

        # Try to join with shadow trade
        shadow = None
        if cor_id:
            shadow = shadow_by_cor.get(cor_id)
        if shadow is None and entity_id:
            shadow = shadow_by_entity.get(f"{symbol}_{cycle_id}")

        # Build the attribution record
        rec = AttributionRecord(
            correlation_id=cor_id,
            symbol=symbol,
            cycle_id=cycle_id,
            entity_id=entity_id,
            timestamp_utc=trace.get("timestamp_utc", ""),
            action=trace.get("action", "NO_TRADE"),
            pattern_name=trace.get("pattern_name", ""),
            regime=trace.get("regime", ""),
            market_state=trace.get("market_state", ""),
            selected_strategy=trace.get("selected_strategy", "") or "",
            strategy_confidence=float(trace.get("strategy_confidence", 0.0)),
            components=dict(components),
            score_neutral=float(trace.get("score_neutral", 0.0)),
            score_strategy=float(trace.get("score_strategy", 0.0)),
        )

        if shadow is not None:
            r = _extract_r_multiple(shadow)
            if r is not None:
                outcome_fields = _extract_outcome_fields(shadow)
                rec.has_outcome = True
                rec.r_multiple = r
                rec.win = r > 0
                rec.exit_reason = outcome_fields["exit_reason"]
                rec.bars_held = outcome_fields["bars_held"]
                rec.mfe_r = outcome_fields["mfe_r"]
                rec.mae_r = outcome_fields["mae_r"]

        records.append(rec)

    logger.info(
        "[Q1_LINKER] traces=%d attributed=%d with_outcome=%d join_rate=%.1f%%",
        len(decision_traces),
        len(records),
        sum(1 for r in records if r.has_outcome),
        (sum(1 for r in records if r.has_outcome) / len(records) * 100) if records else 0,
    )

    return records


# ═══════════════════════════════════════════════════════════════════════════════
# ANALYSIS ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def _pearson(xs: list[float], ys: list[float]) -> float | None:
    """Pearson correlation. Returns None if insufficient data."""
    n = len(xs)
    if n < 5:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    if den_x == 0 or den_y == 0:
        return None
    return num / (den_x * den_y)


def _analyse_component(
    name: str,
    records: list[AttributionRecord],
) -> ComponentStats:
    """Compute statistics for one scoring component."""
    # Filter to records that have this component AND an outcome
    valid = [r for r in records if name in r.components and r.has_outcome]
    stats = ComponentStats(name=name, sample_size=len(valid))

    if not valid:
        return stats

    # Basic stats
    wins = [r for r in valid if r.win]
    stats.win_rate = len(wins) / len(valid) if valid else 0.0
    stats.avg_r = sum(r.r_multiple for r in valid) / len(valid)

    # Split by median component value
    values = sorted(r.components[name] for r in valid)
    median_val = values[len(values) // 2]

    # Use >= median for high, < median for low to handle discrete value distributions
    high = [r for r in valid if r.components[name] > median_val]
    low = [r for r in valid if r.components[name] < median_val]

    # If one group is empty (all values identical), try strict split at median
    if not high and not low:
        # All values are the same — cannot differentiate
        stats.avg_r_when_high = stats.avg_r
        stats.avg_r_when_low = stats.avg_r
    elif not high:
        # All values <= median (equal to median) — use top/bottom half by index
        half = len(valid) // 2
        sorted_by_comp = sorted(valid, key=lambda r: r.components[name])
        high = sorted_by_comp[half:]
        low = sorted_by_comp[:half]
        if high:
            stats.avg_r_when_high = sum(r.r_multiple for r in high) / len(high)
        if low:
            stats.avg_r_when_low = sum(r.r_multiple for r in low) / len(low)
    else:
        if high:
            stats.avg_r_when_high = sum(r.r_multiple for r in high) / len(high)
        if low:
            stats.avg_r_when_low = sum(r.r_multiple for r in low) / len(low)

    stats.predictive_value = stats.avg_r_when_high - stats.avg_r_when_low

    # Correlation between component score and R-multiple
    comp_values = [r.components[name] for r in valid]
    r_values = [r.r_multiple for r in valid]
    stats.correlation = _pearson(comp_values, r_values)

    return stats


def _analyse_interaction(
    component_names: tuple[str, ...],
    label: str,
    records: list[AttributionRecord],
    threshold: float = 0.5,
) -> InteractionStats:
    """Analyse component combination (all above threshold)."""
    valid = [
        r for r in records
        if r.has_outcome and all(r.components.get(c, 0) > threshold for c in component_names)
    ]
    stats = InteractionStats(components=component_names, label=label, sample_size=len(valid))

    if valid:
        stats.win_rate = sum(1 for r in valid if r.win) / len(valid)
        stats.avg_r = sum(r.r_multiple for r in valid) / len(valid)

    return stats


def run_component_reward(
    decision_traces: list[dict[str, Any]],
    shadow_trades: list[dict[str, Any]],
) -> ComponentRewardResult:
    """
    Run Q1: Component → Reward Correlation Experiment.

    Joins decision component scores with shadow trade outcomes to determine
    which components have genuine predictive value.
    """
    result = ComponentRewardResult()

    # Build attributed records
    records = build_attribution_records(decision_traces, shadow_trades)
    result.total_decisions = len(records)
    result.decisions_with_outcome = sum(1 for r in records if r.has_outcome)
    result.join_rate = result.decisions_with_outcome / result.total_decisions if result.total_decisions > 0 else 0.0

    if result.decisions_with_outcome < 5:
        result.conclusion = "Insufficient matched data for component analysis."
        result.confidence = "INSUFFICIENT_DATA"
        return result

    # Identify available components
    all_components: set[str] = set()
    for r in records:
        if r.has_outcome:
            all_components.update(r.components.keys())

    # ─── Q1.1: Per-component analysis ─────────────────────────────────
    for comp_name in sorted(all_components):
        stats = _analyse_component(comp_name, records)
        if stats.sample_size >= 5:
            result.component_stats.append(stats)

    # Sort by predictive value (best first)
    result.component_stats.sort(key=lambda s: s.predictive_value, reverse=True)

    # ─── Q1.2: Negative contributors ─────────────────────────────────
    result.negative_contributors = [
        s for s in result.component_stats
        if s.predictive_value < 0
    ]

    # ─── Q1.3: Component interactions ─────────────────────────────────
    # Test key combinations
    interactions_to_test = [
        (("bias_alignment", "trend_alignment"), "Bias + Trend aligned"),
        (("bias_alignment", "htf_alignment"), "Bias + HTF aligned"),
        (("pattern_quality", "confirmation_pre"), "Pattern + Confirmation"),
        (("htf_alignment", "h4_alignment"), "HTF + H4 aligned"),
        (("volatility_quality", "trend_alignment"), "Volatility + Trend"),
        (("bias_alignment",), "Bias only (high)"),
        (("htf_alignment",), "HTF only (high)"),
        (("trend_alignment",), "Trend only (high)"),
    ]

    for components, label in interactions_to_test:
        istat = _analyse_interaction(components, label, records)
        if istat.sample_size >= 3:
            result.interactions.append(istat)

    result.interactions.sort(key=lambda i: i.avg_r, reverse=True)

    # ─── Summary ──────────────────────────────────────────────────────
    if result.component_stats:
        result.best_predictor = result.component_stats[0].name
        result.worst_predictor = result.component_stats[-1].name

    # Confidence
    if result.decisions_with_outcome >= 100:
        result.confidence = "HIGH"
    elif result.decisions_with_outcome >= 30:
        result.confidence = "MEDIUM"
    else:
        result.confidence = "LOW"

    # Conclusion
    positive = [s for s in result.component_stats if s.predictive_value > 0.1]
    negative = [s for s in result.component_stats if s.predictive_value < -0.1]

    parts = []
    if positive:
        parts.append(f"Strong predictors: {', '.join(s.name for s in positive[:3])}")
    if negative:
        parts.append(f"Negative contributors: {', '.join(s.name for s in negative[:3])}")
    if not positive and not negative:
        parts.append("No component shows strong predictive differentiation")

    result.conclusion = ". ".join(parts) + f". (n={result.decisions_with_outcome}, confidence={result.confidence})"

    logger.info(
        "[Q1] decisions=%d matched=%d best=%s worst=%s confidence=%s",
        result.total_decisions, result.decisions_with_outcome,
        result.best_predictor, result.worst_predictor, result.confidence,
    )

    return result


# ─── STANDARD REPORT PERSISTENCE ──────────────────────────────────────────────


def run() -> dict:
    """
    Run Q1 and persist result using standard research report framework.

    Loads decision traces and shadow trades from S3 via the shared data-access layer.
    """
    from research_engine.data_access.s3_source import get_default_source

    _source = get_default_source()

    decision_traces = list(_source.read_dataset("decision_trace"))

    shadow_trades = []
    for dataset in ["shadow_trades", "research_shadow_trades"]:
        shadow_trades.extend(_source.read_dataset(dataset))

    result = run_component_reward(decision_traces, shadow_trades)

    # Build canonical report
    from research_engine.experiments.experiment_base import build_report, build_fingerprint, compute_confidence

    recommendation = "WEIGHT_ADJUSTMENT" if result.best_predictor else "INSUFFICIENT_DATA"
    confidence = result.confidence if result.confidence else compute_confidence(result.decisions_with_outcome)

    report = build_report(
        question_id="Q1",
        status="COMPLETE" if result.decisions_with_outcome > 0 else "INSUFFICIENT_DATA",
        overall={
            "total_decisions": result.total_decisions,
            "matched_outcomes": result.decisions_with_outcome,
            "best_predictor": result.best_predictor or "none",
            "worst_predictor": result.worst_predictor or "none",
            "finding": result.conclusion,
            **result.to_dict(),
        },
        confidence=confidence,
        dataset={"source": "decision_trace + shadow_trades", "sample_size": result.decisions_with_outcome},
        fingerprint=build_fingerprint(result.decisions_with_outcome, result.total_decisions - result.decisions_with_outcome, "decision_trace+shadow_trades"),
        recommendation=recommendation,
        provenance={"experiment_module": "research_engine.experiments.component_reward", "registry_id": "Q1", "function": "run", "pipeline": "Question -> Experiment -> Dataset -> Output -> Knowledge -> Command Centre"},
    )

    # Persist
    try:
        from research_engine.experiments.experiment_base import persist_report as eb_persist
        eb_persist(report, "q1_component_reward.json")
    except Exception:
        pass

    return report


if __name__ == "__main__":
    r = run()
    print(f"Q1: best={r.best_predictor} worst={r.worst_predictor} n={r.decisions_with_outcome} | {r.confidence}")
