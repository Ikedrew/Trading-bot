"""
Walk-Forward Validation — Time-based robustness testing for strategy performance.

Evaluates whether observed trading performance is stable across sequential time
windows. Detects overfitting, regime-dependent breakdowns, and pattern
profitability decay.

This module ONLY evaluates. It does NOT:
    - Generate or modify trading rules
    - Run shadow execution
    - Perform rule interaction analysis

Core Logic:
    Split dataset into sequential chronological windows.
    For each window: compute TRAIN metrics, TEST metrics, DELTA metrics.
    Across all windows: compute per-pattern stability scores.

Usage:
    from analysis.walk_forward import run_walk_forward_validation

    result = run_walk_forward_validation(curated_dir="events/curated")
    print(result["overall_summary"])
    print(result["pattern_stability"])
"""

from __future__ import annotations

import json
import logging
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

# Default number of sequential windows (train→test pairs)
DEFAULT_N_WINDOWS = 4

# Train/test split ratio within each segment
DEFAULT_TRAIN_RATIO = 0.65

# Minimum trades required per window to produce valid statistics
MIN_TRADES_PER_WINDOW = 10

# Minimum trades per pattern per window to include in stability calculation
MIN_PATTERN_TRADES = 3

# Stability score thresholds
STABILITY_HIGH = 70
STABILITY_LOW = 35


# ═══════════════════════════════════════════════════════════════════════════════
# DATA LOADING
# ═══════════════════════════════════════════════════════════════════════════════

def _load_trades(curated_dir: str) -> list[dict[str, Any]]:
    """Load curated events and filter to trades (pnl != 0), sorted by time."""
    events: list[dict[str, Any]] = []
    curated_path = Path(curated_dir)

    if not curated_path.exists():
        logger.warning("[WF] Curated directory not found: %s", curated_dir)
        return events

    for jsonl_file in sorted(curated_path.glob("*.jsonl")):
        with open(jsonl_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                    if ev.get("pnl", 0) != 0:
                        events.append(ev)
                except json.JSONDecodeError:
                    continue

    # Sort chronologically
    events.sort(key=lambda e: e.get("timestamp", ""))
    logger.info("[WF] Loaded %d trades from %s", len(events), curated_dir)
    return events


# ═══════════════════════════════════════════════════════════════════════════════
# METRICS COMPUTATION
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_window_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute aggregate performance metrics for a time window."""
    if not trades:
        return {
            "total_trades": 0, "winrate": 0.0, "avg_pnl": 0.0,
            "total_pnl": 0.0, "max_drawdown": 0.0,
            "wins": 0, "losses": 0, "profit_factor": 0.0,
        }

    pnls = [t.get("pnl", 0) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    # Max drawdown from cumulative equity curve
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        cumulative += p
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)

    pf = abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else 999.0

    return {
        "total_trades": len(pnls),
        "wins": len(wins),
        "losses": len(losses),
        "winrate": round(len(wins) / len(pnls) * 100, 2),
        "avg_pnl": round(sum(pnls) / len(pnls), 4),
        "total_pnl": round(sum(pnls), 4),
        "max_drawdown": round(max_dd, 4),
        "profit_factor": round(min(pf, 999.0), 3),
    }


def _compute_pattern_metrics(trades: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Compute per-pattern expectancy metrics for a time window."""
    by_pattern: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        pattern = t.get("pattern", "UNKNOWN")
        by_pattern[pattern].append(t.get("pnl", 0))

    result: dict[str, dict[str, Any]] = {}
    for pattern, pnls in by_pattern.items():
        wins = [p for p in pnls if p > 0]
        result[pattern] = {
            "trades": len(pnls),
            "winrate": round(len(wins) / len(pnls) * 100, 2) if pnls else 0.0,
            "avg_pnl": round(sum(pnls) / len(pnls), 4) if pnls else 0.0,
            "total_pnl": round(sum(pnls), 4),
        }

    return result


def _compute_delta(train: dict[str, Any], test: dict[str, Any]) -> dict[str, Any]:
    """Compute delta metrics between train and test windows."""
    return {
        "winrate_change": round(test.get("winrate", 0) - train.get("winrate", 0), 2),
        "avg_pnl_change": round(test.get("avg_pnl", 0) - train.get("avg_pnl", 0), 4),
        "drawdown_change": round(test.get("max_drawdown", 0) - train.get("max_drawdown", 0), 4),
        "trade_count_change": test.get("total_trades", 0) - train.get("total_trades", 0),
        "pnl_degraded": test.get("avg_pnl", 0) < train.get("avg_pnl", 0),
        "winrate_degraded": test.get("winrate", 0) < train.get("winrate", 0),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# WINDOW SEGMENTATION
# ═══════════════════════════════════════════════════════════════════════════════

def _split_into_windows(
    trades: list[dict[str, Any]],
    n_windows: int,
    train_ratio: float,
) -> list[dict[str, Any]]:
    """
    Split trades into sequential train/test window pairs.

    Uses a sliding approach: each window advances through time,
    with train period preceding test period.
    """
    total = len(trades)
    if total < MIN_TRADES_PER_WINDOW * 2:
        return []

    # Each window covers a segment of the data
    # Total segments needed = n_windows + 1 (each window uses 2 consecutive segments)
    n_segments = n_windows + 1
    segment_size = total // n_segments

    if segment_size < MIN_TRADES_PER_WINDOW:
        # Reduce windows to fit data
        n_segments = total // MIN_TRADES_PER_WINDOW
        n_windows = max(1, n_segments - 1)
        segment_size = total // (n_windows + 1)

    windows: list[dict[str, Any]] = []

    for i in range(n_windows):
        train_start = i * segment_size
        train_end = train_start + segment_size
        test_start = train_end
        test_end = min(test_start + segment_size, total)

        train_set = trades[train_start:train_end]
        test_set = trades[test_start:test_end]

        if len(train_set) < MIN_TRADES_PER_WINDOW or len(test_set) < MIN_TRADES_PER_WINDOW:
            continue

        # Compute metrics
        train_metrics = _compute_window_metrics(train_set)
        test_metrics = _compute_window_metrics(test_set)
        delta_metrics = _compute_delta(train_metrics, test_metrics)

        # Per-pattern expectancy
        train_patterns = _compute_pattern_metrics(train_set)
        test_patterns = _compute_pattern_metrics(test_set)

        # Pattern-level deltas
        pattern_deltas: dict[str, dict[str, Any]] = {}
        all_patterns = set(list(train_patterns.keys()) + list(test_patterns.keys()))
        for pat in all_patterns:
            tr = train_patterns.get(pat, {"avg_pnl": 0, "winrate": 0, "trades": 0})
            te = test_patterns.get(pat, {"avg_pnl": 0, "winrate": 0, "trades": 0})
            pattern_deltas[pat] = {
                "train_avg_pnl": tr["avg_pnl"],
                "test_avg_pnl": te["avg_pnl"],
                "pnl_change": round(te["avg_pnl"] - tr["avg_pnl"], 4),
                "train_winrate": tr["winrate"],
                "test_winrate": te["winrate"],
                "winrate_change": round(te["winrate"] - tr["winrate"], 2),
                "train_trades": tr["trades"],
                "test_trades": te["trades"],
            }

        windows.append({
            "window_idx": i,
            "train_period": {
                "start": train_set[0].get("timestamp", ""),
                "end": train_set[-1].get("timestamp", ""),
                "trade_count": len(train_set),
            },
            "test_period": {
                "start": test_set[0].get("timestamp", ""),
                "end": test_set[-1].get("timestamp", ""),
                "trade_count": len(test_set),
            },
            "train_metrics": train_metrics,
            "test_metrics": test_metrics,
            "delta_metrics": delta_metrics,
            "train_pattern_expectancy": train_patterns,
            "test_pattern_expectancy": test_patterns,
            "pattern_deltas": pattern_deltas,
        })

    return windows


# ═══════════════════════════════════════════════════════════════════════════════
# PATTERN STABILITY ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_pattern_stability(
    windows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Compute per-pattern stability scores across all walk-forward windows.

    Stability score (0-100) measures how consistent a pattern's performance
    is across time. High stability = real edge. Low stability = overfit or
    regime-dependent.

    Components:
        - Direction consistency: % of windows where PnL is positive
        - Magnitude stability: low variance in avg_pnl across windows
        - Winrate consistency: low variance in winrate across windows
    """
    # Collect per-pattern metrics across all windows
    pattern_data: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for window in windows:
        # Use test metrics (out-of-sample) for stability measurement
        test_patterns = window.get("test_pattern_expectancy", {})
        for pattern, metrics in test_patterns.items():
            if metrics.get("trades", 0) >= MIN_PATTERN_TRADES:
                pattern_data[pattern].append(metrics)

    stability_results: list[dict[str, Any]] = []

    for pattern, window_metrics in pattern_data.items():
        if len(window_metrics) < 2:
            # Need at least 2 windows to measure stability
            stability_results.append({
                "pattern": pattern,
                "stability_score": 0,
                "windows_observed": len(window_metrics),
                "variance_pnl": 0.0,
                "variance_winrate": 0.0,
                "regime_sensitivity": "unknown",
                "note": "Insufficient windows for stability measurement",
            })
            continue

        avg_pnls = [m["avg_pnl"] for m in window_metrics]
        winrates = [m["winrate"] for m in window_metrics]
        trade_counts = [m["trades"] for m in window_metrics]

        # 1. Direction consistency (% windows with positive avg_pnl)
        positive_windows = sum(1 for p in avg_pnls if p > 0)
        direction_score = positive_windows / len(avg_pnls)

        # 2. PnL magnitude stability (coefficient of variation)
        mean_pnl = sum(avg_pnls) / len(avg_pnls)
        if mean_pnl != 0:
            var_pnl = sum((p - mean_pnl) ** 2 for p in avg_pnls) / len(avg_pnls)
            cv_pnl = (var_pnl ** 0.5) / abs(mean_pnl)
            pnl_stability = max(0, 1 - min(cv_pnl, 2.0) / 2.0)
        else:
            var_pnl = 0.0
            pnl_stability = 0.0

        # 3. Winrate consistency
        mean_wr = sum(winrates) / len(winrates)
        if mean_wr != 0:
            var_wr = sum((w - mean_wr) ** 2 for w in winrates) / len(winrates)
            cv_wr = (var_wr ** 0.5) / mean_wr
            wr_stability = max(0, 1 - min(cv_wr, 1.5) / 1.5)
        else:
            var_wr = 0.0
            wr_stability = 0.0

        # Combined stability score (0-100)
        score = int(
            direction_score * 40 +  # Most important: is it consistently profitable?
            pnl_stability * 35 +    # Is the magnitude stable?
            wr_stability * 25       # Is the winrate stable?
        )
        score = min(100, max(0, score))

        # Regime sensitivity classification
        pnl_range = max(avg_pnls) - min(avg_pnls) if avg_pnls else 0
        if abs(mean_pnl) > 0:
            relative_range = pnl_range / abs(mean_pnl)
        else:
            relative_range = 999

        if relative_range > 3.0:
            regime_sensitivity = "high"
        elif relative_range > 1.5:
            regime_sensitivity = "medium"
        else:
            regime_sensitivity = "low"

        stability_results.append({
            "pattern": pattern,
            "stability_score": score,
            "windows_observed": len(window_metrics),
            "direction_consistency": round(direction_score * 100, 1),
            "mean_avg_pnl": round(mean_pnl, 4),
            "variance_pnl": round(var_pnl, 6),
            "mean_winrate": round(mean_wr, 2),
            "variance_winrate": round(var_wr, 4),
            "regime_sensitivity": regime_sensitivity,
            "avg_trades_per_window": round(sum(trade_counts) / len(trade_counts), 1),
            "all_windows_profitable": all(p > 0 for p in avg_pnls),
            "worst_window_pnl": round(min(avg_pnls), 4),
            "best_window_pnl": round(max(avg_pnls), 4),
        })

    # Sort by stability score descending
    stability_results.sort(key=lambda s: s["stability_score"], reverse=True)
    return stability_results


# ═══════════════════════════════════════════════════════════════════════════════
# OVERALL SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════

def _build_overall_summary(
    windows: list[dict[str, Any]],
    stability: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build overall summary from walk-forward results."""
    if not windows:
        return {
            "most_stable_patterns": [],
            "least_stable_patterns": [],
            "overall_edge_decay": False,
            "summary_text": "Insufficient data for walk-forward analysis.",
        }

    # Edge decay detection: is test performance consistently below train?
    degradation_count = sum(
        1 for w in windows if w["delta_metrics"].get("pnl_degraded", False)
    )
    edge_decay = degradation_count > len(windows) * 0.6

    # Most/least stable patterns
    stable = [s for s in stability if s["stability_score"] >= STABILITY_HIGH]
    unstable = [s for s in stability if s["stability_score"] <= STABILITY_LOW]

    # Overall avg PnL trend across windows
    test_pnls = [w["test_metrics"]["avg_pnl"] for w in windows]
    pnl_trend = "declining" if len(test_pnls) >= 2 and test_pnls[-1] < test_pnls[0] else "stable_or_improving"

    # Summary text
    n_patterns = len(stability)
    n_stable = len(stable)
    lines = []
    if edge_decay:
        lines.append(f"WARNING: Edge decay detected — {degradation_count}/{len(windows)} windows show degradation.")
    else:
        lines.append(f"Edge appears stable — {len(windows) - degradation_count}/{len(windows)} windows maintain or improve performance.")

    lines.append(f"{n_stable}/{n_patterns} patterns show high stability (score >= {STABILITY_HIGH}).")
    if unstable:
        lines.append(f"{len(unstable)} pattern(s) show low stability — possible overfitting: {[s['pattern'] for s in unstable]}")

    return {
        "total_windows": len(windows),
        "degradation_windows": degradation_count,
        "overall_edge_decay": edge_decay,
        "pnl_trend": pnl_trend,
        "most_stable_patterns": [
            {"pattern": s["pattern"], "score": s["stability_score"], "regime_sensitivity": s["regime_sensitivity"]}
            for s in stable[:5]
        ],
        "least_stable_patterns": [
            {"pattern": s["pattern"], "score": s["stability_score"], "regime_sensitivity": s["regime_sensitivity"]}
            for s in unstable[:5]
        ],
        "summary_text": " ".join(lines),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def run_walk_forward_validation(
    *,
    curated_dir: str = "events/curated",
    n_windows: int = DEFAULT_N_WINDOWS,
    train_ratio: float = DEFAULT_TRAIN_RATIO,
) -> dict[str, Any]:
    """
    Run complete walk-forward validation on curated trade data.

    Args:
        curated_dir: Path to curated JSONL directory
        n_windows: Number of sequential train/test window pairs
        train_ratio: Not used in segment mode (kept for API compat)

    Returns:
        {
            "metadata": {...},
            "walk_forward_windows": [...],
            "pattern_stability": [...],
            "overall_summary": {...},
        }
    """
    trades = _load_trades(curated_dir)

    if len(trades) < MIN_TRADES_PER_WINDOW * 2:
        return {
            "metadata": {"error": "insufficient_data", "trades_found": len(trades)},
            "walk_forward_windows": [],
            "pattern_stability": [],
            "overall_summary": {
                "most_stable_patterns": [],
                "least_stable_patterns": [],
                "overall_edge_decay": False,
                "summary_text": f"Only {len(trades)} trades found. Need >= {MIN_TRADES_PER_WINDOW * 2}.",
            },
        }

    # Run window segmentation
    windows = _split_into_windows(trades, n_windows, train_ratio)

    # Compute pattern stability across windows
    pattern_stability = _compute_pattern_stability(windows)

    # Build summary
    overall_summary = _build_overall_summary(windows, pattern_stability)

    output = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "curated_dir": curated_dir,
            "total_trades": len(trades),
            "n_windows": len(windows),
            "requested_windows": n_windows,
            "date_range": {
                "earliest": trades[0].get("timestamp", ""),
                "latest": trades[-1].get("timestamp", ""),
            },
            "unique_patterns": len(set(t.get("pattern", "") for t in trades)),
            "unique_symbols": len(set(t.get("symbol", "") for t in trades)),
        },
        "walk_forward_windows": windows,
        "pattern_stability": pattern_stability,
        "overall_summary": overall_summary,
    }

    logger.info(
        "[WF] Validation complete — %d trades, %d windows, %d patterns scored",
        len(trades), len(windows), len(pattern_stability),
    )

    return output


# ═══════════════════════════════════════════════════════════════════════════════
# EXPORT & DISPLAY
# ═══════════════════════════════════════════════════════════════════════════════

def export_results(results: dict[str, Any], path: str = "analysis/reports/walk_forward.json") -> str:
    """Export walk-forward results to JSON file."""
    filepath = Path(path)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)
    logger.info("[WF] Results exported to %s", filepath)
    return str(filepath)


def print_results(results: dict[str, Any]) -> None:
    """Print human-readable walk-forward validation summary."""
    meta = results.get("metadata", {})
    windows = results.get("walk_forward_windows", [])
    stability = results.get("pattern_stability", [])
    summary = results.get("overall_summary", {})

    print()
    print("═══════════════════════════════════════════════════════════════")
    print("  WALK-FORWARD VALIDATION REPORT")
    print("═══════════════════════════════════════════════════════════════")
    print(f"  Generated: {meta.get('generated_at', '?')}")
    print(f"  Trades:    {meta.get('total_trades', 0)}")
    print(f"  Windows:   {meta.get('n_windows', 0)}")
    print(f"  Patterns:  {meta.get('unique_patterns', 0)}")
    print(f"  Period:    {meta.get('date_range', {}).get('earliest', '?')[:10]} → "
          f"{meta.get('date_range', {}).get('latest', '?')[:10]}")
    print()

    # Window results
    if windows:
        print("─── WINDOW RESULTS ─────────────────────────────────────────────")
        print(f"  {'Win':>4} {'Train':>7} {'Test':>7} │ {'Train WR':>9} {'Test WR':>8} {'Δ WR':>6} │ "
              f"{'Train PnL':>10} {'Test PnL':>9} {'Δ PnL':>8}")
        print(f"  {'─'*4} {'─'*7} {'─'*7} │ {'─'*9} {'─'*8} {'─'*6} │ {'─'*10} {'─'*9} {'─'*8}")

        for w in windows:
            tm = w["train_metrics"]
            te = w["test_metrics"]
            d = w["delta_metrics"]
            decay_marker = " ↓" if d["pnl_degraded"] else " ✓"
            print(
                f"  {w['window_idx']:>4} {tm['total_trades']:>7} {te['total_trades']:>7} │ "
                f"{tm['winrate']:>8.1f}% {te['winrate']:>7.1f}% {d['winrate_change']:>+5.1f} │ "
                f"{tm['avg_pnl']:>10.2f} {te['avg_pnl']:>9.2f} {d['avg_pnl_change']:>+7.2f}{decay_marker}"
            )
        print()

    # Pattern stability
    if stability:
        print("─── PATTERN STABILITY ──────────────────────────────────────────")
        print(f"  {'Pattern':<25} {'Score':>6} {'Windows':>8} {'Dir%':>6} {'Regime':>8} {'Profitable?':>12}")
        print(f"  {'─'*25} {'─'*6} {'─'*8} {'─'*6} {'─'*8} {'─'*12}")

        for s in stability:
            all_prof = "✓ all" if s.get("all_windows_profitable") else "✗ mixed"
            print(
                f"  {s['pattern']:<25} {s['stability_score']:>5}/100 "
                f"{s['windows_observed']:>7} {s.get('direction_consistency', 0):>5.0f}% "
                f"{s['regime_sensitivity']:>8} {all_prof:>12}"
            )
        print()

    # Summary
    print("─── OVERALL SUMMARY ────────────────────────────────────────────")
    print(f"  {summary.get('summary_text', 'No summary available.')}")
    print()

    if summary.get("most_stable_patterns"):
        print("  Most stable:")
        for p in summary["most_stable_patterns"][:3]:
            print(f"    ✓ {p['pattern']} (score={p['score']}, sensitivity={p['regime_sensitivity']})")

    if summary.get("least_stable_patterns"):
        print("  Least stable:")
        for p in summary["least_stable_patterns"][:3]:
            print(f"    ✗ {p['pattern']} (score={p['score']}, sensitivity={p['regime_sensitivity']})")

    print()
    edge_status = "⚠ EDGE DECAY DETECTED" if summary.get("overall_edge_decay") else "✓ Edge appears stable"
    print(f"  Final verdict: {edge_status}")
    print()
    print("═══════════════════════════════════════════════════════════════")


# ═══════════════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    curated = sys.argv[1] if len(sys.argv) > 1 else "events/curated"
    n_win = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_N_WINDOWS
    output = sys.argv[3] if len(sys.argv) > 3 else "analysis/reports/walk_forward.json"

    results = run_walk_forward_validation(curated_dir=curated, n_windows=n_win)

    if results.get("metadata", {}).get("error"):
        print(f"ERROR: {results['metadata']['error']}")
        sys.exit(1)

    print_results(results)
    export_results(results, output)
    print(f"  Report saved to: {output}")
