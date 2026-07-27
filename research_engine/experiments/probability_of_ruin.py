"""
R3 — Probability of Ruin Experiment.

Question:
    Given the measured edge, variance, win rate and position sizing,
    what is the probability that the account eventually reaches
    catastrophic drawdown?

Outputs:
    - probability_of_ruin (analytical)
    - survival_probability
    - expected_survival_trades
    - monte_carlo estimate with confidence interval
    - required_sample_size for reliable estimate
    - confidence level
    - promotion recommendation

This module is PURELY RESEARCH. It does NOT modify trading logic.
"""

from __future__ import annotations

import math
import random
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from research_engine.experiments.experiment_base import (
    ReadinessStatus,
    build_fingerprint,
    build_report,
    check_readiness,
    compute_confidence,
    extract_r_multiples,
    load_shadow_trades,
    persist_report,
    update_knowledge_map,
)


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

_MIN_SAMPLES = 50
_MONTE_CARLO_SIMULATIONS = 10_000
_MONTE_CARLO_TRADE_HORIZON = 5_000  # Simulate 5000 trades forward
_RUIN_THRESHOLD_PCT = 0.50  # 50% drawdown = ruin
_RISK_PER_TRADE_PCT = 0.01  # Default 1% risk per trade (for Kelly context)
_ACCEPTABLE_RUIN_THRESHOLD = 0.05  # Promotion requires < 5% ruin probability


# ═══════════════════════════════════════════════════════════════════════════════
# ANALYTICAL RUIN PROBABILITY
# ═══════════════════════════════════════════════════════════════════════════════


def _analytical_ruin_probability(win_rate: float, avg_win_r: float, avg_loss_r: float) -> float:
    """
    Compute probability of ruin using the classical formula.

    For a system with win probability p, average win W, average loss L:
        If edge exists (p*W > (1-p)*L):
            P(ruin) ≈ ((1-p)/p)^(bankroll/unit_risk) for even-money bets
        Generalised: P(ruin) ≈ (q/p)^n where q=1-p, n=units of capital

    For non-even bets, uses the ratio approximation.
    """
    if win_rate <= 0 or win_rate >= 1:
        return 1.0 if win_rate <= 0 else 0.0

    p = win_rate
    q = 1.0 - p

    # Expected value per trade
    ev = p * avg_win_r - q * avg_loss_r

    if ev <= 0:
        # Negative or zero edge → eventual ruin is certain
        return 1.0

    # Approximation using the odds ratio raised to capital/risk
    # For the generalised case with unequal payoffs:
    # Use the formula: P(ruin) = ((q * avg_loss_r) / (p * avg_win_r)) ^ (capital_units)
    # Where capital_units = 1 / risk_fraction (how many units before ruin)
    capital_units = 1.0 / _RUIN_THRESHOLD_PCT  # e.g. 50% DD = 2 units

    odds_ratio = (q * avg_loss_r) / (p * avg_win_r) if (p * avg_win_r) > 0 else 1.0

    if odds_ratio >= 1.0:
        return 1.0  # No edge in payoff structure

    ruin_prob = odds_ratio ** capital_units
    return min(1.0, max(0.0, ruin_prob))


# ═══════════════════════════════════════════════════════════════════════════════
# MONTE CARLO SIMULATION
# ═══════════════════════════════════════════════════════════════════════════════


def _monte_carlo_ruin(
    r_values: list[float],
    n_simulations: int = _MONTE_CARLO_SIMULATIONS,
    trade_horizon: int = _MONTE_CARLO_TRADE_HORIZON,
    ruin_threshold: float = _RUIN_THRESHOLD_PCT,
    seed: int = 42,
) -> dict[str, Any]:
    """
    Monte Carlo simulation of account survival.

    Randomly samples from observed R-multiples to simulate future paths.
    Counts how many paths reach ruin (drawdown >= threshold).
    """
    import array
    rng = random.Random(seed)
    n_values = len(r_values)
    ruin_count = 0
    survival_trades: list[int] = []
    max_drawdowns: list[float] = []

    for _ in range(n_simulations):
        equity = 1.0
        peak = 1.0
        ruined = False

        for trade_num in range(1, trade_horizon + 1):
            r = r_values[rng.randrange(n_values)]
            equity += r * _RISK_PER_TRADE_PCT

            if equity > peak:
                peak = equity

            if peak > 0 and (peak - equity) / peak >= ruin_threshold:
                ruined = True
                survival_trades.append(trade_num)
                break

        if ruined:
            ruin_count += 1
        max_drawdowns.append((peak - equity) / peak if peak > 0 else 0)

    ruin_probability = ruin_count / n_simulations if n_simulations > 0 else 0
    avg_survival = sum(survival_trades) / len(survival_trades) if survival_trades else trade_horizon
    avg_max_dd = sum(max_drawdowns) / len(max_drawdowns) if max_drawdowns else 0

    se = math.sqrt(ruin_probability * (1 - ruin_probability) / n_simulations) if n_simulations > 0 else 0
    ci_lower = max(0, ruin_probability - 1.96 * se)
    ci_upper = min(1, ruin_probability + 1.96 * se)

    return {
        "simulations": n_simulations,
        "trade_horizon": trade_horizon,
        "ruin_threshold": ruin_threshold,
        "ruin_count": ruin_count,
        "ruin_probability": round(ruin_probability, 6),
        "survival_probability": round(1 - ruin_probability, 6),
        "avg_survival_trades": round(avg_survival, 0),
        "avg_max_drawdown": round(avg_max_dd, 4),
        "confidence_interval_95": [round(ci_lower, 6), round(ci_upper, 6)],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN EXPERIMENT
# ═══════════════════════════════════════════════════════════════════════════════


def run_probability_of_ruin(shadow_trades: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """
    Run R3: Probability of Ruin experiment.

    Validates readiness, computes analytical and Monte Carlo ruin probability,
    and produces a standard research report.
    """
    if shadow_trades is None:
        shadow_trades = load_shadow_trades()

    # Readiness check
    status, reason, coverage = check_readiness(
        shadow_trades,
        min_samples=_MIN_SAMPLES,
        require_lineage=True,
        require_outcome=True,
    )

    if status != ReadinessStatus.READY:
        return build_report(
            question_id="R3",
            status=status,
            overall={"reason": reason},
            confidence="INSUFFICIENT_DATA",
            dataset={"records_available": len(shadow_trades), "coverage": coverage},
            fingerprint=build_fingerprint(0, len(shadow_trades)),
            recommendation="WAIT",
            warnings=[reason],
        )

    # Extract R-multiples
    r_values = extract_r_multiples(shadow_trades)
    if len(r_values) < _MIN_SAMPLES:
        return build_report(
            question_id="R3",
            status=ReadinessStatus.INSUFFICIENT_DATA,
            overall={"reason": f"Only {len(r_values)} R-multiples extracted"},
            confidence="INSUFFICIENT_DATA",
            dataset={"r_multiples": len(r_values)},
            fingerprint=build_fingerprint(len(r_values), len(shadow_trades) - len(r_values)),
            recommendation="WAIT",
        )

    # Core metrics
    n = len(r_values)
    wins = [r for r in r_values if r > 0]
    losses = [r for r in r_values if r < 0]
    win_rate = len(wins) / n
    avg_win_r = sum(wins) / len(wins) if wins else 0.0
    avg_loss_r = abs(sum(losses) / len(losses)) if losses else 0.0
    ev_per_trade = sum(r_values) / n
    variance = sum((r - ev_per_trade) ** 2 for r in r_values) / (n - 1) if n > 1 else 0
    std_dev = math.sqrt(variance)

    # Analytical ruin
    analytical_ruin = _analytical_ruin_probability(win_rate, avg_win_r, avg_loss_r)

    # Monte Carlo
    mc_result = _monte_carlo_ruin(
        r_values,
        n_simulations=_MONTE_CARLO_SIMULATIONS,
        trade_horizon=_MONTE_CARLO_TRADE_HORIZON,
    )

    # Confidence
    significant = ev_per_trade > 0 and n >= 100
    confidence = compute_confidence(n, significant)

    # Required sample size for reliable estimate (targeting SE < 0.01)
    target_se = 0.01
    p_hat = mc_result["ruin_probability"]
    required_n = int(math.ceil(p_hat * (1 - p_hat) / (target_se ** 2))) if p_hat > 0 and p_hat < 1 else 1000

    # Recommendation
    if mc_result["ruin_probability"] < _ACCEPTABLE_RUIN_THRESHOLD and confidence in ("HIGH", "MEDIUM"):
        recommendation = "PROMOTE"
        finding = f"Probability of ruin {mc_result['ruin_probability']:.2%} is below {_ACCEPTABLE_RUIN_THRESHOLD:.0%} threshold. System survival acceptable."
    elif mc_result["ruin_probability"] < _ACCEPTABLE_RUIN_THRESHOLD:
        recommendation = "MONITOR"
        finding = f"Ruin probability {mc_result['ruin_probability']:.2%} is acceptable but confidence is {confidence}. Need more data."
    elif mc_result["ruin_probability"] >= 0.20:
        recommendation = "REJECT"
        finding = f"Ruin probability {mc_result['ruin_probability']:.2%} is dangerously high. Do NOT deploy."
    else:
        recommendation = "WAIT"
        finding = f"Ruin probability {mc_result['ruin_probability']:.2%}. Reduce risk or collect more evidence."

    # Build report
    report = build_report(
        question_id="R3",
        status=ReadinessStatus.COMPLETE,
        overall={
            "probability_of_ruin_analytical": round(analytical_ruin, 6),
            "probability_of_ruin_monte_carlo": mc_result["ruin_probability"],
            "survival_probability": mc_result["survival_probability"],
            "expected_survival_trades": mc_result["avg_survival_trades"],
            "ev_per_trade": round(ev_per_trade, 4),
            "win_rate": round(win_rate, 4),
            "avg_win_r": round(avg_win_r, 4),
            "avg_loss_r": round(avg_loss_r, 4),
            "std_dev_r": round(std_dev, 4),
            "required_sample_size": required_n,
        },
        confidence=confidence,
        dataset={
            "total_records": len(shadow_trades),
            "r_multiples_used": n,
            "wins": len(wins),
            "losses": len(losses),
            "coverage": coverage,
        },
        fingerprint=build_fingerprint(n, len(shadow_trades) - n),
        recommendation=recommendation,
        assumptions=[
            f"Risk per trade: {_RISK_PER_TRADE_PCT:.1%}",
            f"Ruin threshold: {_RUIN_THRESHOLD_PCT:.0%} drawdown",
            f"Monte Carlo: {_MONTE_CARLO_SIMULATIONS} simulations, {_MONTE_CARLO_TRADE_HORIZON} trades forward",
            "R-multiples sampled with replacement from observed distribution",
        ],
        warnings=[
            w for w in [
                f"High ruin probability: {mc_result['ruin_probability']:.2%}" if mc_result["ruin_probability"] >= 0.10 else "",
                f"Low sample size: {n} trades" if n < 100 else "",
                "Negative EV detected" if ev_per_trade < 0 else "",
            ] if w
        ],
        provenance={
            "experiment_module": "research_engine.experiments.probability_of_ruin",
            "registry_id": "R3",
            "function": "run_probability_of_ruin",
            "pipeline": "Question → Experiment → Dataset → Output → Knowledge → Command Centre",
        },
    )

    # Add Monte Carlo details
    report["monte_carlo"] = mc_result

    # Persist
    persist_report(report, "r3_probability_of_ruin.json")

    # Update knowledge map
    update_knowledge_map("R3", finding, recommendation)

    return report


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════


if __name__ == "__main__":
    result = run_probability_of_ruin()
    status = result.get("status", "?")
    overall = result.get("overall", {})
    print(f"R3: status={status} | ruin={overall.get('probability_of_ruin_monte_carlo', '?')} | confidence={result.get('confidence')} | rec={result.get('recommendation')}")
