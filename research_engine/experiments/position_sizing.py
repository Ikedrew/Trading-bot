"""
R5 — Position Sizing Optimisation Experiment.

Question:
    What position sizing model maximises long-term growth while
    respecting acceptable drawdown?

Compares: Fixed Risk, Fixed Lot, Kelly, Half Kelly, Fractional Kelly, Dynamic.

This module is PURELY RESEARCH. It does NOT modify trading logic.
"""

from __future__ import annotations

import math
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

_MIN_SAMPLES = 50
_STARTING_EQUITY = 10000.0
_MAX_ACCEPTABLE_DD = 0.30


def _kelly_fraction(win_rate: float, avg_win_r: float, avg_loss_r: float) -> float:
    """Compute Kelly criterion fraction: f* = (p*b - q) / b where b = avg_win/avg_loss."""
    if avg_loss_r <= 0 or win_rate <= 0:
        return 0.0
    b = avg_win_r / avg_loss_r
    q = 1.0 - win_rate
    kelly = (win_rate * b - q) / b
    return max(0.0, kelly)


def _simulate_sizing(r_values: list[float], risk_fraction: float, label: str) -> dict[str, Any]:
    """Simulate equity curve with a given risk fraction per trade."""
    equity = _STARTING_EQUITY
    peak = _STARTING_EQUITY
    max_dd = 0.0
    min_equity = _STARTING_EQUITY

    for r in r_values:
        pnl = equity * risk_fraction * r
        equity += pnl
        equity = max(equity, 0.01)  # Prevent negative equity
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)
        min_equity = min(min_equity, equity)

    n = len(r_values)
    total_return = (equity - _STARTING_EQUITY) / _STARTING_EQUITY
    cagr = ((equity / _STARTING_EQUITY) ** (252 / max(n, 1))) - 1 if equity > 0 else -1
    volatility = math.sqrt(sum((r * risk_fraction) ** 2 for r in r_values) / max(n - 1, 1)) if n > 1 else 0
    sharpe = (total_return / n * 252) / volatility if volatility > 0 else 0

    return {
        "model": label,
        "risk_fraction": round(risk_fraction, 4),
        "final_equity": round(equity, 2),
        "total_return_pct": round(total_return * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "volatility": round(volatility, 4),
        "sharpe_approx": round(sharpe, 2),
        "acceptable_dd": max_dd <= _MAX_ACCEPTABLE_DD,
    }


def run_position_sizing(shadow_trades: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Run R5: Position Sizing Optimisation experiment."""
    if shadow_trades is None:
        shadow_trades = load_shadow_trades()

    status, reason, coverage = check_readiness(shadow_trades, min_samples=_MIN_SAMPLES, require_outcome=True)
    if status != ReadinessStatus.READY:
        return build_report(
            question_id="R5", status=status, overall={"reason": reason},
            confidence="INSUFFICIENT_DATA",
            dataset={"records_available": len(shadow_trades), "coverage": coverage},
            fingerprint=build_fingerprint(0, len(shadow_trades)), recommendation="WAIT", warnings=[reason],
        )

    r_values = extract_r_multiples(shadow_trades)
    n = len(r_values)
    if n < _MIN_SAMPLES:
        return build_report(
            question_id="R5", status=ReadinessStatus.INSUFFICIENT_DATA,
            overall={"reason": f"Only {n} R-multiples"}, confidence="INSUFFICIENT_DATA",
            dataset={"r_multiples": n}, fingerprint=build_fingerprint(n, len(shadow_trades) - n),
            recommendation="WAIT",
        )

    wins = [r for r in r_values if r > 0]
    losses = [r for r in r_values if r < 0]
    win_rate = len(wins) / n
    avg_win_r = sum(wins) / len(wins) if wins else 0
    avg_loss_r = abs(sum(losses) / len(losses)) if losses else 1.0

    kelly = _kelly_fraction(win_rate, avg_win_r, avg_loss_r)

    # Test all models
    models = [
        _simulate_sizing(r_values, 0.005, "Fixed 0.5%"),
        _simulate_sizing(r_values, 0.01, "Fixed 1%"),
        _simulate_sizing(r_values, 0.02, "Fixed 2%"),
        _simulate_sizing(r_values, kelly, f"Full Kelly ({kelly:.2%})") if kelly > 0 else {"model": "Full Kelly", "risk_fraction": 0, "acceptable_dd": False, "max_drawdown_pct": 100, "total_return_pct": 0, "cagr_pct": 0, "final_equity": _STARTING_EQUITY, "volatility": 0, "sharpe_approx": 0},
        _simulate_sizing(r_values, kelly * 0.5, f"Half Kelly ({kelly*0.5:.2%})") if kelly > 0 else {"model": "Half Kelly", "risk_fraction": 0, "acceptable_dd": True, "max_drawdown_pct": 0, "total_return_pct": 0, "cagr_pct": 0, "final_equity": _STARTING_EQUITY, "volatility": 0, "sharpe_approx": 0},
        _simulate_sizing(r_values, kelly * 0.25, f"Quarter Kelly ({kelly*0.25:.2%})") if kelly > 0 else {"model": "Quarter Kelly", "risk_fraction": 0, "acceptable_dd": True, "max_drawdown_pct": 0, "total_return_pct": 0, "cagr_pct": 0, "final_equity": _STARTING_EQUITY, "volatility": 0, "sharpe_approx": 0},
    ]

    # Best model: highest return with acceptable DD
    acceptable = [m for m in models if m.get("acceptable_dd", False)]
    best = max(acceptable, key=lambda m: m.get("total_return_pct", 0)) if acceptable else models[0]

    confidence = compute_confidence(n, kelly > 0)
    recommendation = "PROMOTE" if confidence in ("HIGH", "MEDIUM") and best.get("total_return_pct", 0) > 0 else "MONITOR"
    finding = f"Best sizing: {best['model']} (return {best.get('total_return_pct', 0):.1f}%, DD {best.get('max_drawdown_pct', 0):.1f}%). Kelly={kelly:.2%}."

    report = build_report(
        question_id="R5", status=ReadinessStatus.COMPLETE,
        overall={"kelly_fraction": round(kelly, 4), "best_model": best["model"], "best_risk_fraction": best.get("risk_fraction", 0), "models": models},
        confidence=confidence,
        dataset={"total_records": len(shadow_trades), "r_multiples_used": n, "win_rate": round(win_rate, 4), "avg_win_r": round(avg_win_r, 4), "avg_loss_r": round(avg_loss_r, 4), "coverage": coverage},
        fingerprint=build_fingerprint(n, len(shadow_trades) - n),
        recommendation=recommendation,
        assumptions=[f"Starting equity: ${_STARTING_EQUITY:.0f}", f"Max acceptable DD: {_MAX_ACCEPTABLE_DD:.0%}", "Sequential replay of observed R distribution"],
        provenance={"experiment_module": "research_engine.experiments.position_sizing", "registry_id": "R5", "function": "run_position_sizing", "pipeline": "Question → Experiment → Dataset → Output → Knowledge → Command Centre"},
    )

    persist_report(report, "r5_position_sizing.json")
    update_knowledge_map("R5", finding, recommendation)
    return report


if __name__ == "__main__":
    result = run_position_sizing()
    o = result.get("overall", {})
    print(f"R5: best={o.get('best_model', '?')} | kelly={o.get('kelly_fraction', '?')} | rec={result.get('recommendation')}")
