"""
Q19: Expected Value Experiment

Question: "What is the system's true edge expressed as expected value?"

Formula:
    EV = (win_rate × avg_win_R) - (loss_rate × avg_loss_R)

Computes:
    - Win rate and loss rate
    - Average winning R-multiple
    - Average losing R-multiple
    - Expected value per trade (in R-multiples)
    - Rolling EV trend (is edge growing or decaying?)
    - Statistical significance (is EV distinguishable from zero?)
    - Profit factor (gross_wins / gross_losses)

Data source: shadow_trades (469+ records available — no live trades required)

This is the fundamental "is this working?" metric. If EV ≤ 0, the system
has no edge and should reduce exposure.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ExpectedValueResult:
    """Result of Q19 Expected Value experiment."""

    # Dataset
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    breakeven_trades: int = 0

    # Core metrics
    win_rate: float = 0.0
    loss_rate: float = 0.0
    avg_win_r: float = 0.0
    avg_loss_r: float = 0.0
    expected_value: float = 0.0  # EV per trade in R-multiples

    # Extended metrics
    profit_factor: float = 0.0  # gross_wins / gross_losses
    avg_r: float = 0.0          # Simple average R across all trades
    median_r: float = 0.0
    max_win_r: float = 0.0
    max_loss_r: float = 0.0
    std_dev_r: float = 0.0

    # Trend (rolling window)
    ev_last_50: float | None = None   # EV over last 50 trades
    ev_last_100: float | None = None  # EV over last 100 trades
    ev_trend: str = ""                # "improving", "stable", "decaying", "insufficient_data"

    # Statistical significance
    t_statistic: float | None = None
    p_value_approx: str = ""   # "< 0.01", "< 0.05", "< 0.10", "> 0.10"
    significant: bool = False  # True if EV significantly different from 0

    # Classification
    conclusion: str = ""
    confidence: str = ""  # HIGH / MEDIUM / LOW / INSUFFICIENT_DATA
    edge_classification: str = ""  # STRONG_EDGE / MARGINAL_EDGE / NO_EDGE / NEGATIVE_EDGE

    # Per-pattern breakdown (top 5)
    pattern_breakdown: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "breakeven_trades": self.breakeven_trades,
            "win_rate": round(self.win_rate, 4),
            "loss_rate": round(self.loss_rate, 4),
            "avg_win_r": round(self.avg_win_r, 4),
            "avg_loss_r": round(self.avg_loss_r, 4),
            "expected_value": round(self.expected_value, 4),
            "profit_factor": round(self.profit_factor, 4),
            "avg_r": round(self.avg_r, 4),
            "median_r": round(self.median_r, 4),
            "max_win_r": round(self.max_win_r, 4),
            "max_loss_r": round(self.max_loss_r, 4),
            "std_dev_r": round(self.std_dev_r, 4),
            "ev_last_50": round(self.ev_last_50, 4) if self.ev_last_50 is not None else None,
            "ev_last_100": round(self.ev_last_100, 4) if self.ev_last_100 is not None else None,
            "ev_trend": self.ev_trend,
            "t_statistic": round(self.t_statistic, 4) if self.t_statistic is not None else None,
            "p_value_approx": self.p_value_approx,
            "significant": self.significant,
            "conclusion": self.conclusion,
            "confidence": self.confidence,
            "edge_classification": self.edge_classification,
            "pattern_breakdown": self.pattern_breakdown,
        }


def _extract_r_multiple(record: dict[str, Any]) -> float | None:
    """Extract R-multiple from a shadow trade record."""
    # shadow_trades_v2 schema
    simulated = record.get("simulated_outcome", {})
    if isinstance(simulated, dict):
        r = simulated.get("pnl_r_multiple")
        if r is not None:
            return float(r)
    # Flat field fallback
    r = record.get("pnl_r_multiple")
    if r is not None:
        return float(r)
    # Legacy schema
    outcome = record.get("outcome", {})
    if isinstance(outcome, dict):
        r = outcome.get("r_multiple")
        if r is not None:
            return float(r)
    return None


def _extract_pattern(record: dict[str, Any]) -> str:
    """Extract pattern name from a shadow trade record."""
    decision = record.get("decision_snapshot", {})
    if isinstance(decision, dict):
        p = decision.get("pattern")
        if p:
            return str(p)
    return record.get("pattern", "UNKNOWN")


def _compute_ev(r_values: list[float]) -> tuple[float, float, float, float, float]:
    """
    Compute expected value metrics from a list of R-multiples.

    Returns: (win_rate, avg_win_r, avg_loss_r, expected_value, profit_factor)
    """
    if not r_values:
        return 0.0, 0.0, 0.0, 0.0, 0.0

    wins = [r for r in r_values if r > 0]
    losses = [r for r in r_values if r < 0]
    n = len(r_values)

    win_rate = len(wins) / n if n > 0 else 0.0
    loss_rate = len(losses) / n if n > 0 else 0.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0

    ev = (win_rate * avg_win) - (loss_rate * avg_loss)

    gross_wins = sum(wins) if wins else 0.0
    gross_losses = abs(sum(losses)) if losses else 0.0
    profit_factor = gross_wins / gross_losses if gross_losses > 0 else float("inf") if gross_wins > 0 else 0.0

    return win_rate, avg_win, avg_loss, ev, profit_factor


def _t_test_vs_zero(values: list[float]) -> tuple[float | None, str, bool]:
    """
    One-sample t-test: is the mean significantly different from 0?

    Returns: (t_statistic, p_value_category, is_significant_at_0.05)
    """
    n = len(values)
    if n < 5:
        return None, "> 0.10", False

    mean = sum(values) / n
    variance = sum((x - mean) ** 2 for x in values) / (n - 1)
    if variance <= 0:
        return None, "> 0.10", False

    std_err = math.sqrt(variance / n)
    t_stat = mean / std_err

    # Approximate p-value using t-distribution
    # For large n (>30), t ≈ z (normal)
    abs_t = abs(t_stat)
    if abs_t > 3.29:
        p_cat = "< 0.001"
        sig = True
    elif abs_t > 2.58:
        p_cat = "< 0.01"
        sig = True
    elif abs_t > 1.96:
        p_cat = "< 0.05"
        sig = True
    elif abs_t > 1.645:
        p_cat = "< 0.10"
        sig = False
    else:
        p_cat = "> 0.10"
        sig = False

    return t_stat, p_cat, sig


def run_expected_value(shadow_trades: list[dict[str, Any]]) -> ExpectedValueResult:
    """
    Run Q19: Expected Value Experiment.

    Computes the system's true edge from shadow trade R-multiples.
    Answers: "Is this system making money in expectation?"
    """
    result = ExpectedValueResult()

    # Extract R-multiples
    r_values: list[float] = []
    pattern_r: dict[str, list[float]] = {}

    for record in shadow_trades:
        r = _extract_r_multiple(record)
        if r is None:
            continue
        r_values.append(r)
        pattern = _extract_pattern(record)
        pattern_r.setdefault(pattern, []).append(r)

    result.total_trades = len(r_values)

    if result.total_trades == 0:
        result.conclusion = "No shadow trade R-multiples found. Cannot compute expected value."
        result.confidence = "INSUFFICIENT_DATA"
        result.edge_classification = "NO_EDGE"
        return result

    # Core metrics
    result.winning_trades = sum(1 for r in r_values if r > 0)
    result.losing_trades = sum(1 for r in r_values if r < 0)
    result.breakeven_trades = sum(1 for r in r_values if r == 0)

    win_rate, avg_win, avg_loss, ev, pf = _compute_ev(r_values)
    result.win_rate = win_rate
    result.loss_rate = 1.0 - win_rate - (result.breakeven_trades / result.total_trades)
    result.avg_win_r = avg_win
    result.avg_loss_r = avg_loss
    result.expected_value = ev
    result.profit_factor = pf

    # Distribution metrics
    sorted_r = sorted(r_values)
    result.avg_r = sum(r_values) / len(r_values)
    result.median_r = sorted_r[len(sorted_r) // 2]
    result.max_win_r = max(r_values)
    result.max_loss_r = min(r_values)

    variance = sum((r - result.avg_r) ** 2 for r in r_values) / (len(r_values) - 1) if len(r_values) > 1 else 0.0
    result.std_dev_r = math.sqrt(variance)

    # Rolling EV trend
    if len(r_values) >= 50:
        _, _, _, ev_50, _ = _compute_ev(r_values[-50:])
        result.ev_last_50 = ev_50
    if len(r_values) >= 100:
        _, _, _, ev_100, _ = _compute_ev(r_values[-100:])
        result.ev_last_100 = ev_100

    # Trend classification
    if result.ev_last_50 is not None and result.ev_last_100 is not None:
        if result.ev_last_50 > result.ev_last_100 * 1.2:
            result.ev_trend = "improving"
        elif result.ev_last_50 < result.ev_last_100 * 0.8:
            result.ev_trend = "decaying"
        else:
            result.ev_trend = "stable"
    elif result.ev_last_50 is not None:
        result.ev_trend = "insufficient_history"
    else:
        result.ev_trend = "insufficient_data"

    # Statistical significance
    t_stat, p_cat, sig = _t_test_vs_zero(r_values)
    result.t_statistic = t_stat
    result.p_value_approx = p_cat
    result.significant = sig

    # Edge classification
    if ev > 0.15 and sig:
        result.edge_classification = "STRONG_EDGE"
    elif ev > 0.05 and sig:
        result.edge_classification = "MARGINAL_EDGE"
    elif ev > 0:
        result.edge_classification = "NO_EDGE"  # Positive but not significant
    else:
        result.edge_classification = "NEGATIVE_EDGE"

    # Per-pattern breakdown (top 5 by trade count)
    pattern_stats = []
    for pattern, rs in sorted(pattern_r.items(), key=lambda x: -len(x[1])):
        if len(rs) < 3:
            continue
        _, p_avg_win, p_avg_loss, p_ev, p_pf = _compute_ev(rs)
        p_wr = sum(1 for r in rs if r > 0) / len(rs)
        pattern_stats.append({
            "pattern": pattern,
            "trades": len(rs),
            "win_rate": round(p_wr, 4),
            "avg_win_r": round(p_avg_win, 4),
            "avg_loss_r": round(p_avg_loss, 4),
            "expected_value": round(p_ev, 4),
            "profit_factor": round(p_pf, 4),
        })
    result.pattern_breakdown = pattern_stats[:5]

    # Confidence level
    if result.total_trades >= 100:
        result.confidence = "HIGH"
    elif result.total_trades >= 30:
        result.confidence = "MEDIUM"
    elif result.total_trades >= 10:
        result.confidence = "LOW"
    else:
        result.confidence = "INSUFFICIENT_DATA"

    # Conclusion
    if result.edge_classification == "STRONG_EDGE":
        result.conclusion = (
            f"System demonstrates a statistically significant positive edge. "
            f"EV = {ev:+.3f}R per trade (n={result.total_trades}, p {p_cat}). "
            f"Win rate: {win_rate:.1%}, Avg win: {avg_win:.2f}R, Avg loss: {avg_loss:.2f}R. "
            f"Profit factor: {pf:.2f}. "
            f"Trend: {result.ev_trend}."
        )
    elif result.edge_classification == "MARGINAL_EDGE":
        result.conclusion = (
            f"System shows a marginal positive edge. "
            f"EV = {ev:+.3f}R per trade (n={result.total_trades}, p {p_cat}). "
            f"Win rate: {win_rate:.1%}. Profit factor: {pf:.2f}. "
            f"Edge is small — monitor for decay. Trend: {result.ev_trend}."
        )
    elif result.edge_classification == "NEGATIVE_EDGE":
        result.conclusion = (
            f"System has NEGATIVE expected value. "
            f"EV = {ev:+.3f}R per trade (n={result.total_trades}). "
            f"Win rate: {win_rate:.1%}. Profit factor: {pf:.2f}. "
            f"System should reduce exposure or investigate causes."
        )
    else:
        result.conclusion = (
            f"System EV is not statistically distinguishable from zero. "
            f"EV = {ev:+.3f}R per trade (n={result.total_trades}, p {p_cat}). "
            f"Win rate: {win_rate:.1%}. Profit factor: {pf:.2f}. "
            f"More data needed to confirm edge existence."
        )

    logger.info(
        "[Q19] trades=%d ev=%.4f win_rate=%.3f pf=%.2f edge=%s confidence=%s",
        result.total_trades, result.expected_value, result.win_rate,
        result.profit_factor, result.edge_classification, result.confidence,
    )

    return result


# ─── STANDARD REPORT PERSISTENCE ──────────────────────────────────────────────


def run(shadow_trades: list[dict[str, Any]] | None = None) -> dict:
    """
    Run Q19 and persist result using standard research report framework.

    If shadow_trades not provided, loads from default location.
    """
    import json
    from pathlib import Path

    if shadow_trades is None:
        shadow_trades = []
        shadow_dir = Path("logs/research_shadow_trades")
        if shadow_dir.exists():
            for f in shadow_dir.rglob("*.jsonl"):
                for line in f.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        try:
                            shadow_trades.append(json.loads(line))
                        except Exception:
                            pass
        # Also load regular shadow trades
        shadow_dir2 = Path("logs/shadow_trades")
        if shadow_dir2.exists():
            for f in shadow_dir2.rglob("*.jsonl"):
                for line in f.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        try:
                            shadow_trades.append(json.loads(line))
                        except Exception:
                            pass

    result = run_expected_value(shadow_trades)

    # Build canonical report
    from research_engine.experiments.experiment_base import build_report, build_fingerprint

    recommendation = "POSITIVE_EDGE" if result.expected_value > 0 else "NEGATIVE_EDGE"

    report = build_report(
        question_id="Q19",
        status="COMPLETE" if result.total_trades > 0 else "INSUFFICIENT_DATA",
        overall={
            "expected_value": round(result.expected_value, 4),
            "win_rate": round(result.win_rate, 4),
            "profit_factor": round(result.profit_factor, 4),
            "avg_r": round(result.avg_r, 4),
            "edge_classification": result.edge_classification,
            "significant": result.significant,
            "finding": result.conclusion,
            **result.to_dict(),
        },
        confidence=result.confidence,
        dataset={"source": "shadow_trades + research_shadow_trades", "sample_size": result.total_trades},
        fingerprint=build_fingerprint(result.total_trades, 0, "shadow_trades"),
        recommendation=recommendation,
        provenance={"experiment_module": "research_engine.experiments.expected_value", "registry_id": "Q19", "function": "run", "pipeline": "Question -> Experiment -> Dataset -> Output -> Knowledge -> Command Centre"},
    )

    # Persist
    try:
        from research_engine.experiments.experiment_base import persist_report as eb_persist
        eb_persist(report, "q19_expected_value.json")
    except Exception:
        pass

    return report


if __name__ == "__main__":
    r = run()
    print(f"Q19: EV={r.expected_value:+.4f}R | WR={r.win_rate:.1%} | n={r.total_trades} | {r.edge_classification}")
