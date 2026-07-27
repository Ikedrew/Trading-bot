"""
Live Regime Drift Monitor — Detects behaviour drift and enforces safety states.

Compares live (or near-live) market behaviour against the validated system
envelope established by walk-forward, stress testing, and shadow execution.

Triggers risk-control state transitions:
    LIVE → WATCH → SHADOW → DISABLED

This module ONLY monitors. It does NOT:
    - Generate or modify rules
    - Run backtests or simulations
    - Retrain or optimise strategy
    - Adjust rules dynamically

Usage:
    from analysis.live_drift_monitor import DriftMonitor

    monitor = DriftMonitor(baseline_path="analysis/reports/regime_stress_test.json")
    result = monitor.evaluate(live_events)
    print(result["risk_state"])  # STABLE / WATCH / DEGRADED / BROKEN_REGIME
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

# Drift score weights
W_REGIME = 0.35
W_PATTERN = 0.25
W_RULE = 0.20
W_STABILITY = 0.20

# Risk state thresholds
THRESHOLD_STABLE = 25
THRESHOLD_WATCH = 50
THRESHOLD_DEGRADED = 75
# Above 75 = BROKEN_REGIME

# Minimum events required for meaningful drift measurement
MIN_LIVE_EVENTS = 10

# Tolerance for distribution comparison (percentage points)
DISTRIBUTION_TOLERANCE = 15.0


# ═══════════════════════════════════════════════════════════════════════════════
# RISK STATES
# ═══════════════════════════════════════════════════════════════════════════════

RISK_STATES = {
    "LIVE": "System operating normally. Full execution enabled.",
    "WATCH": "Early drift detected. Reduce position sizing.",
    "SHADOW": "Performance likely impaired. Shadow mode only — no live execution.",
    "DISABLED": "Regime broken. All signals frozen. Re-validation required.",
}


# ═══════════════════════════════════════════════════════════════════════════════
# BASELINE LOADING
# ═══════════════════════════════════════════════════════════════════════════════

def _load_json(path: str) -> dict[str, Any] | None:
    p = Path(path)
    if not p.exists():
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def _build_baseline(
    stress_path: str,
    walk_forward_path: str,
    shadow_path: str,
    confidence_path: str,
) -> dict[str, Any]:
    """
    Build expected baseline distribution from validated system outputs.

    Combines:
        - Regime stress test baseline metrics
        - Walk-forward pattern stability
        - Shadow execution expectations
        - Confidence score state
    """
    baseline: dict[str, Any] = {
        "regime_distribution": {"expansion": 0.0, "contraction": 0.0, "neutral": 1.0},
        "bias_distribution": {"bullish": 0.0, "bearish": 0.0, "neutral": 1.0},
        "pattern_distribution": {},
        "expected_winrate": 85.0,
        "expected_avg_pnl": 35.0,
        "liquidity_sweep_rate": 0.0,
        "bos_confirmation_rate": 0.0,
        "rule_block_rate": 14.0,
        "confidence_score": 69,
        "stability_score": 18,
    }

    # From stress test
    stress = _load_json(stress_path)
    if stress:
        bm = stress.get("baseline_metrics", {})
        baseline["expected_winrate"] = bm.get("winrate", baseline["expected_winrate"])
        baseline["expected_avg_pnl"] = bm.get("avg_pnl", baseline["expected_avg_pnl"])

    # From walk-forward
    wf = _load_json(walk_forward_path)
    if wf:
        stability_list = wf.get("pattern_stability", [])
        for s in stability_list:
            baseline["pattern_distribution"][s.get("pattern", "")] = {
                "stability_score": s.get("stability_score", 0),
                "mean_winrate": s.get("mean_winrate", 0),
                "mean_avg_pnl": s.get("mean_avg_pnl", 0),
            }

    # From shadow execution
    shadow = _load_json(shadow_path)
    if shadow:
        div = shadow.get("divergence_metrics", {})
        baseline["rule_block_rate"] = div.get("divergence_rate", baseline["rule_block_rate"])

    # From confidence score
    conf = _load_json(confidence_path)
    if conf:
        overall = conf.get("overall_confidence", {})
        baseline["confidence_score"] = overall.get("score", baseline["confidence_score"])
        ss = conf.get("system_stability", {})
        baseline["stability_score"] = ss.get("score", baseline["stability_score"])

    return baseline


# ═══════════════════════════════════════════════════════════════════════════════
# DRIFT COMPUTATION
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_regime_drift(
    live_events: list[dict[str, Any]],
    baseline: dict[str, Any],
) -> dict[str, Any]:
    """
    Compare live regime distribution vs expected.

    Measures:
        - atr_regime frequency shift
        - htf_bias distribution change
        - liquidity_swept rate deviation
        - bos_confirmed rate change
    """
    n = len(live_events)
    if n == 0:
        return {"score": 0, "detail": "no_events"}

    # Live distributions
    regime_counts: dict[str, int] = defaultdict(int)
    bias_counts: dict[str, int] = defaultdict(int)
    liq_count = 0
    bos_count = 0

    for ev in live_events:
        regime_counts[ev.get("atr_regime", "neutral")] += 1
        bias_counts[ev.get("htf_bias", "neutral")] += 1
        if ev.get("liquidity_swept", False):
            liq_count += 1
        if ev.get("bos_confirmed", False):
            bos_count += 1

    live_regime_dist = {k: v / n * 100 for k, v in regime_counts.items()}
    live_bias_dist = {k: v / n * 100 for k, v in bias_counts.items()}
    live_liq_rate = liq_count / n * 100
    live_bos_rate = bos_count / n * 100

    # Expected distributions
    exp_regime = baseline.get("regime_distribution", {})
    exp_bias = baseline.get("bias_distribution", {})
    exp_liq = baseline.get("liquidity_sweep_rate", 0)
    exp_bos = baseline.get("bos_confirmation_rate", 0)

    # Compute divergences
    regime_div = _distribution_divergence(live_regime_dist, {k: v * 100 for k, v in exp_regime.items()})
    bias_div = _distribution_divergence(live_bias_dist, {k: v * 100 for k, v in exp_bias.items()})
    liq_div = abs(live_liq_rate - exp_liq)
    bos_div = abs(live_bos_rate - exp_bos)

    # Score (0-100): higher = more drift
    score = int(min(100, regime_div * 0.35 + bias_div * 0.30 + liq_div * 0.20 + bos_div * 0.15))

    changes = []
    if regime_div > DISTRIBUTION_TOLERANCE:
        changes.append(f"regime_shift: {dict(live_regime_dist)}")
    if bias_div > DISTRIBUTION_TOLERANCE:
        changes.append(f"bias_shift: {dict(live_bias_dist)}")
    if liq_div > DISTRIBUTION_TOLERANCE:
        changes.append(f"liquidity_rate: {live_liq_rate:.1f}% (expected {exp_liq:.1f}%)")
    if bos_div > DISTRIBUTION_TOLERANCE:
        changes.append(f"bos_rate: {live_bos_rate:.1f}% (expected {exp_bos:.1f}%)")

    return {"score": score, "changes": changes, "live_regime_dist": dict(live_regime_dist)}


def _distribution_divergence(live: dict[str, float], expected: dict[str, float]) -> float:
    """Compute simple divergence between two distributions (0-100 scale)."""
    all_keys = set(list(live.keys()) + list(expected.keys()))
    if not all_keys:
        return 0.0
    total_diff = sum(abs(live.get(k, 0) - expected.get(k, 0)) for k in all_keys)
    return min(100, total_diff / 2)  # Normalize: max diff is 200 (two full distributions)


def _compute_pattern_drift(
    live_events: list[dict[str, Any]],
    baseline: dict[str, Any],
) -> dict[str, Any]:
    """
    Track live pattern performance vs expected.

    Measures:
        - Expectancy shift per pattern
        - Winrate deviation
        - Frequency changes
    """
    trades = [e for e in live_events if e.get("pnl", 0) != 0]
    if not trades:
        return {"score": 0, "shifts": [], "detail": "no_trades_in_window"}

    # Live pattern stats
    by_pattern: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        by_pattern[t.get("pattern", "UNKNOWN")].append(t.get("pnl", 0))

    expected_wr = baseline.get("expected_winrate", 85.0)
    expected_pnl = baseline.get("expected_avg_pnl", 35.0)
    pattern_baselines = baseline.get("pattern_distribution", {})

    shifts: list[dict[str, Any]] = []
    drift_magnitudes: list[float] = []

    for pattern, pnls in by_pattern.items():
        if len(pnls) < 3:
            continue

        live_wr = sum(1 for p in pnls if p > 0) / len(pnls) * 100
        live_avg = sum(pnls) / len(pnls)

        # Expected for this pattern
        pat_base = pattern_baselines.get(pattern, {})
        exp_wr = pat_base.get("mean_winrate", expected_wr)
        exp_avg = pat_base.get("mean_avg_pnl", expected_pnl)

        wr_drift = abs(live_wr - exp_wr)
        pnl_drift = abs(live_avg - exp_avg) / max(abs(exp_avg), 0.01) * 100

        drift_magnitudes.append(wr_drift + pnl_drift * 0.5)

        if wr_drift > DISTRIBUTION_TOLERANCE or pnl_drift > 30:
            shifts.append({
                "pattern": pattern,
                "live_winrate": round(live_wr, 1),
                "expected_winrate": round(exp_wr, 1),
                "wr_deviation": round(live_wr - exp_wr, 1),
                "live_avg_pnl": round(live_avg, 2),
                "expected_avg_pnl": round(exp_avg, 2),
                "pnl_deviation_pct": round((live_avg - exp_avg) / max(abs(exp_avg), 0.01) * 100, 1),
            })

    # Score: average drift magnitude, scaled to 0-100
    if drift_magnitudes:
        raw = sum(drift_magnitudes) / len(drift_magnitudes)
        score = int(min(100, raw * 1.5))
    else:
        score = 0

    return {"score": score, "shifts": shifts}


def _compute_rule_drift(
    live_events: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    baseline: dict[str, Any],
) -> dict[str, Any]:
    """
    Measure whether rules still behave as expected.

    Tracks:
        - Rule activation rate vs expected
        - Rule blocking effectiveness (are blocked trades still losers?)
    """
    trades = [e for e in live_events if e.get("pnl", 0) != 0]
    if not trades or not rules:
        return {"score": 0, "changes": [], "detail": "no_data_or_rules"}

    # Compute live rule activation rate
    blocked = 0
    blocked_losers = 0
    for t in trades:
        for rule in rules:
            if _rule_would_block(t, rule):
                blocked += 1
                if t.get("pnl", 0) < 0:
                    blocked_losers += 1
                break

    live_block_rate = blocked / len(trades) * 100 if trades else 0
    expected_block_rate = baseline.get("rule_block_rate", 14.0)

    # Rule effectiveness: what % of blocks are actual losers
    effectiveness = blocked_losers / max(blocked, 1) * 100

    # Drift: deviation from expected block rate
    rate_drift = abs(live_block_rate - expected_block_rate)

    # Effectiveness drift (expected ~100% if rules target losers)
    effectiveness_drift = max(0, 100 - effectiveness) * 0.5

    score = int(min(100, rate_drift * 1.5 + effectiveness_drift))

    changes = []
    if rate_drift > DISTRIBUTION_TOLERANCE:
        changes.append(f"block_rate: {live_block_rate:.1f}% (expected {expected_block_rate:.1f}%)")
    if effectiveness < 70:
        changes.append(f"rule_effectiveness: {effectiveness:.1f}% (expected >90%)")

    return {"score": score, "changes": changes, "live_block_rate": round(live_block_rate, 1), "effectiveness": round(effectiveness, 1)}


def _rule_would_block(trade: dict[str, Any], rule: dict[str, Any]) -> bool:
    """Check if rule would block a trade (same logic as other modules)."""
    if rule.get("type", "") not in ("TIGHTEN_GATE", "ADD_GATE"):
        return False
    if trade.get("pattern", "") != rule.get("target", ""):
        return False
    if trade.get("pnl", 0) >= 0:
        return False
    evidence = rule.get("supporting_evidence", {})
    regime = evidence.get("regime", "")
    bias = evidence.get("bias", "")
    regime_match = (not regime or regime == "neutral" or trade.get("atr_regime", "") == regime)
    bias_match = (not bias or bias == "neutral" or trade.get("htf_bias", "") == bias)
    return regime_match and bias_match


def _compute_stability_drift(
    live_events: list[dict[str, Any]],
    baseline: dict[str, Any],
) -> dict[str, Any]:
    """
    Aggregate system-level instability signal.

    Measures:
        - Divergence from expected behaviour envelope
        - Volatility of decision outcomes
        - Inconsistency in rule outcomes across time
    """
    trades = [e for e in live_events if e.get("pnl", 0) != 0]
    if len(trades) < MIN_LIVE_EVENTS:
        return {"score": 0, "detail": "insufficient_trades"}

    pnls = [t.get("pnl", 0) for t in trades]
    expected_avg = baseline.get("expected_avg_pnl", 35.0)

    # 1. Mean deviation from expected
    live_avg = sum(pnls) / len(pnls)
    mean_dev = abs(live_avg - expected_avg) / max(abs(expected_avg), 0.01) * 100

    # 2. Outcome volatility (coefficient of variation)
    if live_avg != 0:
        variance = sum((p - live_avg) ** 2 for p in pnls) / len(pnls)
        cv = (variance ** 0.5) / abs(live_avg)
    else:
        cv = 999

    volatility_score = min(100, cv * 30)

    # 3. Streak analysis: long losing streaks signal instability
    max_streak = 0
    current_streak = 0
    for p in pnls:
        if p < 0:
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 0

    streak_score = min(100, max_streak * 15)

    # Combined
    score = int(min(100, mean_dev * 0.40 + volatility_score * 0.35 + streak_score * 0.25))

    return {
        "score": score,
        "live_avg_pnl": round(live_avg, 4),
        "expected_avg_pnl": expected_avg,
        "mean_deviation_pct": round(mean_dev, 1),
        "outcome_volatility": round(volatility_score, 1),
        "max_losing_streak": max_streak,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# RISK STATE CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════════════════

def _classify_risk(overall_drift: int) -> tuple[str, str]:
    """Classify risk state and determine system action."""
    if overall_drift <= THRESHOLD_STABLE:
        return "STABLE", "LIVE"
    elif overall_drift <= THRESHOLD_WATCH:
        return "WATCH", "WATCH"
    elif overall_drift <= THRESHOLD_DEGRADED:
        return "DEGRADED", "SHADOW"
    else:
        return "BROKEN_REGIME", "DISABLED"


def _action_reason(risk_state: str, overall_drift: int, top_driver: str) -> str:
    """Generate human-readable reason for system action."""
    reasons = {
        "STABLE": "All metrics within expected envelope. No action required.",
        "WATCH": f"Early drift detected (score={overall_drift}). Primary driver: {top_driver}. Reduce sizing.",
        "DEGRADED": f"Significant drift (score={overall_drift}). Primary driver: {top_driver}. Shadow mode only.",
        "BROKEN_REGIME": f"Critical drift (score={overall_drift}). Primary driver: {top_driver}. System disabled.",
    }
    return reasons.get(risk_state, "Unknown state.")


# ═══════════════════════════════════════════════════════════════════════════════
# DRIFT MONITOR CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class DriftMonitor:
    """
    Live Regime Drift Monitor.

    Maintains baseline expectations and evaluates incoming events
    against them to detect drift and trigger safety state transitions.
    """

    def __init__(
        self,
        *,
        stress_path: str = "analysis/reports/regime_stress_test.json",
        walk_forward_path: str = "analysis/reports/walk_forward.json",
        shadow_path: str = "analysis/reports/shadow_execution.json",
        confidence_path: str = "analysis/reports/confidence_score.json",
        rules_path: str = "analysis/reports/rule_compression.json",
    ):
        """Initialize monitor with baseline expectations from validated outputs."""
        self._baseline = _build_baseline(stress_path, walk_forward_path, shadow_path, confidence_path)
        self._rules = self._load_rules(rules_path)
        self._current_state = "LIVE"
        self._history: list[dict[str, Any]] = []

        logger.info("[DRIFT] Monitor initialized — baseline loaded, state=LIVE")

    def _load_rules(self, path: str) -> list[dict[str, Any]]:
        data = _load_json(path)
        if data is None:
            return []
        return data.get("final_rule_set", data.get("rules", []))

    @property
    def current_state(self) -> str:
        """Current risk state: LIVE / WATCH / SHADOW / DISABLED."""
        return self._current_state

    @property
    def baseline(self) -> dict[str, Any]:
        """Current baseline expectations."""
        return self._baseline

    def evaluate(self, live_events: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Evaluate a batch of live events against baseline expectations.

        Args:
            live_events: Recent curated events from live trading

        Returns:
            Complete drift assessment with risk state and system action.
        """
        if len(live_events) < MIN_LIVE_EVENTS:
            return {
                "regime_drift_score": 0,
                "pattern_drift_score": 0,
                "rule_drift_score": 0,
                "stability_drift_score": 0,
                "overall_drift_score": 0,
                "risk_state": self._current_state,
                "detail": f"insufficient_events ({len(live_events)} < {MIN_LIVE_EVENTS})",
                "system_action": {"mode": self._current_state, "reason": "Insufficient data for drift measurement."},
            }

        # Compute all drift dimensions
        regime = _compute_regime_drift(live_events, self._baseline)
        pattern = _compute_pattern_drift(live_events, self._baseline)
        rule = _compute_rule_drift(live_events, self._rules, self._baseline)
        stability = _compute_stability_drift(live_events, self._baseline)

        # Weighted overall score
        overall = int(
            regime["score"] * W_REGIME +
            pattern["score"] * W_PATTERN +
            rule["score"] * W_RULE +
            stability["score"] * W_STABILITY
        )
        overall = min(100, max(0, overall))

        # Classify risk
        risk_state, mode = _classify_risk(overall)
        self._current_state = mode

        # Identify top drift driver
        drivers = [
            ("regime", regime["score"]),
            ("pattern", pattern["score"]),
            ("rule", rule["score"]),
            ("stability", stability["score"]),
        ]
        top_driver = max(drivers, key=lambda d: d[1])[0]

        reason = _action_reason(risk_state, overall, top_driver)

        # Recommendation
        if risk_state == "STABLE":
            recommendation = "Continue normal operations."
        elif risk_state == "WATCH":
            recommendation = "Monitor closely. Consider reducing position sizes by 50%."
        elif risk_state == "DEGRADED":
            recommendation = "Switch to shadow mode. Disable live execution. Investigate drift source."
        else:
            recommendation = "Freeze all trading. Re-run walk-forward + stress test before resuming."

        result = {
            "metadata": {
                "evaluated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "events_evaluated": len(live_events),
                "trades_in_window": len([e for e in live_events if e.get("pnl", 0) != 0]),
            },
            "regime_drift_score": regime["score"],
            "pattern_drift_score": pattern["score"],
            "rule_drift_score": rule["score"],
            "stability_drift_score": stability["score"],
            "overall_drift_score": overall,
            "risk_state": risk_state,
            "live_vs_expected_summary": {
                "regime_changes": regime.get("changes", []),
                "pattern_shifts": pattern.get("shifts", []),
                "rule_effectiveness_changes": rule.get("changes", []),
            },
            "system_action": {
                "mode": mode,
                "reason": reason,
            },
            "recommendation": recommendation,
        }

        self._history.append(result)
        logger.info("[DRIFT] Overall=%d/100 State=%s Mode=%s", overall, risk_state, mode)

        return result

    def get_history(self) -> list[dict[str, Any]]:
        """Return evaluation history."""
        return self._history


# ═══════════════════════════════════════════════════════════════════════════════
# BATCH EVALUATION (for testing with local curated data)
# ═══════════════════════════════════════════════════════════════════════════════

def run_drift_check(
    *,
    curated_dir: str = "events/curated",
    stress_path: str = "analysis/reports/regime_stress_test.json",
    walk_forward_path: str = "analysis/reports/walk_forward.json",
    shadow_path: str = "analysis/reports/shadow_execution.json",
    confidence_path: str = "analysis/reports/confidence_score.json",
    rules_path: str = "analysis/reports/rule_compression.json",
) -> dict[str, Any]:
    """
    Run a one-shot drift check using local curated data as "live" input.

    Useful for testing the monitor against historical data.
    """
    # Load "live" events
    events: list[dict[str, Any]] = []
    curated_path = Path(curated_dir)
    if curated_path.exists():
        for f in sorted(curated_path.glob("*.jsonl")):
            with open(f, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            events.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue

    if not events:
        return {"error": "no_events"}

    monitor = DriftMonitor(
        stress_path=stress_path,
        walk_forward_path=walk_forward_path,
        shadow_path=shadow_path,
        confidence_path=confidence_path,
        rules_path=rules_path,
    )

    return monitor.evaluate(events)


# ═══════════════════════════════════════════════════════════════════════════════
# EXPORT & DISPLAY
# ═══════════════════════════════════════════════════════════════════════════════

def export_results(results: dict[str, Any], path: str = "analysis/reports/drift_monitor.json") -> str:
    filepath = Path(path)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)
    return str(filepath)


def print_results(results: dict[str, Any]) -> None:
    overall = results.get("overall_drift_score", 0)
    risk = results.get("risk_state", "?")
    action = results.get("system_action", {})
    meta = results.get("metadata", {})

    print()
    print("═══════════════════════════════════════════════════════════════")
    print("  LIVE DRIFT MONITOR")
    print("═══════════════════════════════════════════════════════════════")
    print(f"  Events: {meta.get('events_evaluated', 0)} | Trades: {meta.get('trades_in_window', 0)}")
    print()

    # Overall drift gauge
    bar = "█" * (overall // 5) + "░" * (20 - overall // 5)
    print(f"  DRIFT: {overall}/100  [{bar}]")
    print(f"  STATE: {risk}  →  MODE: {action.get('mode', '?')}")
    print()

    # Dimension scores
    print("─── DRIFT DIMENSIONS ───────────────────────────────────────────")
    dims = [
        ("Regime Drift", results.get("regime_drift_score", 0), W_REGIME),
        ("Pattern Drift", results.get("pattern_drift_score", 0), W_PATTERN),
        ("Rule Drift", results.get("rule_drift_score", 0), W_RULE),
        ("Stability Drift", results.get("stability_drift_score", 0), W_STABILITY),
    ]
    for name, score, weight in dims:
        sbar = "█" * (score // 10) + "░" * (10 - score // 10)
        print(f"  {name:<20} {score:>3}/100 {sbar}  (×{weight:.2f})")
    print()

    # Live vs expected
    summary = results.get("live_vs_expected_summary", {})
    changes = (
        summary.get("regime_changes", []) +
        summary.get("pattern_shifts", []) +
        summary.get("rule_effectiveness_changes", [])
    )
    if changes:
        print("─── DETECTED CHANGES ───────────────────────────────────────────")
        for c in changes[:5]:
            if isinstance(c, dict):
                print(f"  • {c.get('pattern', '?')}: WR {c.get('wr_deviation', 0):+.1f}pp, PnL {c.get('pnl_deviation_pct', 0):+.1f}%")
            else:
                print(f"  • {c}")
        print()

    # Action
    print("─── SYSTEM ACTION ──────────────────────────────────────────────")
    print(f"  Mode:   {action.get('mode', '?')}")
    print(f"  Reason: {action.get('reason', '?')}")
    print(f"  Rec:    {results.get('recommendation', '')}")
    print()
    print("═══════════════════════════════════════════════════════════════")


# ═══════════════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    curated = sys.argv[1] if len(sys.argv) > 1 else "events/curated"
    output = sys.argv[2] if len(sys.argv) > 2 else "analysis/reports/drift_monitor.json"

    results = run_drift_check(curated_dir=curated)

    if results.get("error"):
        print(f"ERROR: {results['error']}")
        sys.exit(1)

    print_results(results)
    export_results(results, output)
    print(f"  Report saved to: {output}")
