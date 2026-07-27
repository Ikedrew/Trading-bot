"""
Pipeline Diagnostics — Throttled diagnostic reporting for the live scanner.

Emits score pressure reports, component pressure analysis, calibration reports,
paper outcome summaries, and dashboard metrics. Purely observational.

This module OWNS:
    - Score pressure reporting (every 50 cycles)
    - Component pressure reporting (every 100 cycles)
    - Calibration report (at cycle 100)
    - Paper outcome report (every 100 cycles)
    - Dashboard metrics + Discord emission (every 50 cycles)
    - Decision funnel console output

This module does NOT own:
    - When diagnostics run (caller decides timing)
    - Trading decisions
    - Execution authority
    - Runtime loop
    - Score calculation
    - Filter hit tracking

Design: fire-and-forget reporting — never raises, never controls flow.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def emit_pipeline_diagnostics(
    *,
    cycle_id: int,
    decision_funnel: Any,
    score_tracker: dict[str, list],
    filter_hits: dict[str, int],
) -> None:
    """
    Emit throttled pipeline diagnostics. Never raises.

    Called every cycle — internal throttling determines what runs.

    Args:
        cycle_id: Current cycle number.
        decision_funnel: DecisionFunnel instance (has format_console method).
        score_tracker: Dict with scored_signals, rejected_scores, passed_scores.
        filter_hits: Cumulative filter hit counters (read-only).
    """
    try:
        if not (cycle_id % 50 == 0 and cycle_id > 0):
            return

        # Decision Funnel console output
        try:
            print(decision_funnel.format_console(cycle_id))
        except Exception:
            pass

        # Score pressure report
        _emit_score_pressure(cycle_id, score_tracker)

        # Component pressure report (every 100 cycles)
        if cycle_id % 100 == 0:
            _emit_component_pressure(cycle_id, score_tracker)

        # Calibration report (cycle 100 only)
        if cycle_id == 100:
            _emit_calibration_report(cycle_id, score_tracker, filter_hits)

        # Paper outcome report (every 100 cycles)
        if cycle_id % 100 == 0:
            try:
                from core.pipeline.paper_outcome_engine import get_paper_engine
                get_paper_engine().print_report()
            except Exception:
                pass

        # Dashboard metrics + Discord
        _emit_dashboard_discord(cycle_id)

    except Exception:
        pass  # Diagnostics failure must never crash runtime


def _emit_score_pressure(cycle_id: int, score_tracker: dict[str, list]) -> None:
    """Score pressure report — prints to console."""
    _rej = score_tracker["rejected_scores"]
    _all_scored = score_tracker["scored_signals"]
    _passed = score_tracker["passed_scores"]

    if _rej:
        _rej_scores = [s for _, s, _ in _rej]
        _rej_thresholds = [t for _, _, t in _rej]
        _avg_th = sum(_rej_thresholds) / len(_rej_thresholds)
        _near_misses = [(sym, sc, th) for sym, sc, th in _rej if sc >= (th - 0.5)]
        _closest = max(_rej_scores)
        print(f"""[SCORE PRESSURE REPORT @ cycle {cycle_id}]
  Total scored signals: {len(_all_scored)}
  Rejected by score:    {len(_rej)}
  Passed scoring:       {len(_passed)}
  Threshold (avg):      {_avg_th:.2f}
  Avg rejected score:   {sum(_rej_scores) / len(_rej_scores):.2f}
  Min rejected score:   {min(_rej_scores):.2f}
  Max rejected score:   {_closest:.2f}
  Near misses (<0.5):   {len(_near_misses)}
  Closest rejection:    {_closest:.2f} (gap={_closest - _avg_th:.2f})
  DIAGNOSIS: {"CASE B: Threshold too strict" if len(_near_misses) > len(_rej) * 0.5 else "CASE A: Signal quality weak" if _closest < _avg_th - 2.0 else "CASE C: Check component weights"}
""")
    elif _all_scored:
        print(f"\n[SCORE PRESSURE REPORT @ cycle {cycle_id}]\n  Scored: {len(_all_scored)} | Rejected: 0 | All passed scoring\n")


def _emit_component_pressure(cycle_id: int, score_tracker: dict[str, list]) -> None:
    """Component pressure report — deeper analysis every 100 cycles."""
    if not score_tracker["scored_signals"]:
        return

    _comp_data: dict[str, list[float]] = {}
    _expected = {
        "base_score": 4.0,
        "bias_age_weight": 1.0,
        "time_decay_multiplier": 1.0,
        "volatility_penalty": 0.0,
        "regime_bonus": 0.5,
        "sweep_bonus": 0.5,
    }
    for _sym, _sc, _th, _bd in score_tracker["scored_signals"]:
        if isinstance(_bd, dict):
            for _ck, _cv in _bd.items():
                if isinstance(_cv, (int, float)):
                    _comp_data.setdefault(_ck, []).append(float(_cv))
    if not _comp_data:
        return

    print(f"\n[COMPONENT PRESSURE REPORT @ cycle {cycle_id}]")
    print(f"  {'Component':<28s} {'Avg':>6s} {'Min':>6s} {'Max':>6s} {'Weak#':>6s} {'Weak%':>6s}")
    print(f"  {'-'*28} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*6}")
    _suppressors = []
    for _ck in sorted(_comp_data.keys()):
        _vals = _comp_data[_ck]
        _avg = sum(_vals) / len(_vals)
        _mn = min(_vals)
        _mx = max(_vals)
        _exp = _expected.get(_ck, _avg)
        _weak_threshold = _exp * 0.8 if _exp > 0 else -0.5
        _weak_count = sum(1 for v in _vals if v < _weak_threshold)
        _weak_pct = (_weak_count / len(_vals) * 100) if _vals else 0
        print(f"  {_ck:<28s} {_avg:>6.2f} {_mn:>6.2f} {_mx:>6.2f} {_weak_count:>6d} {_weak_pct:>5.1f}%")
        if _weak_pct > 30:
            _suppressors.append((_ck, _weak_pct, _avg))
    _suppressors.sort(key=lambda x: -x[1])
    if _suppressors:
        print(f"\n  Top suppressors (>30% weak-hit rate):")
        for _i, (_name, _pct, _av) in enumerate(_suppressors[:3], 1):
            print(f"    {_i}. {_name} — {_pct:.0f}% weak hits, avg={_av:.2f}")
        _penalty_dominated = any("penalty" in s[0] for s in _suppressors[:2])
        _base_weak = any("base_score" in s[0] for s in _suppressors[:2])
        if _base_weak:
            print(f"\n  DIAGNOSIS: CASE C — Base score low before modifiers (upstream signal quality weak)")
        elif _penalty_dominated:
            print(f"\n  DIAGNOSIS: CASE B — Penalties dominate (filters may be double-counting risk)")
        else:
            print(f"\n  DIAGNOSIS: CASE A — Component suppression (weight imbalance in {_suppressors[0][0]})")
    else:
        print(f"\n  No dominant suppressors detected (all components within normal range)")
    print()


def _emit_calibration_report(cycle_id: int, score_tracker: dict[str, list], filter_hits: dict[str, int]) -> None:
    """Calibration report — emitted at cycle 100."""
    _cal_rej = score_tracker["rejected_scores"]
    _cal_all = score_tracker["scored_signals"]
    _cal_pass = score_tracker["passed_scores"]
    _cal_trades = filter_hits["trades_executed"]
    _cal_rej_scores = [s for _, s, _ in _cal_rej] if _cal_rej else []
    _cal_pass_scores = [s for _, s, _ in _cal_pass] if _cal_pass else []
    _cal_near = [(sym, sc, th) for sym, sc, th in _cal_rej if sc >= (th - 0.5)] if _cal_rej else []
    _avg_rej_str = f"{sum(_cal_rej_scores) / len(_cal_rej_scores):.2f}" if _cal_rej_scores else "N/A"
    _avg_pass_str = f"{sum(_cal_pass_scores) / len(_cal_pass_scores):.2f}" if _cal_pass_scores else "N/A"
    print(f"""
{'='*60}
[CALIBRATION REPORT — cycle 100]
  Volatility penalty: REDUCED 50% (-3→-1.5, -2→-1.0, -1→-0.5)
  Confluence threshold: LOWERED 5.0 → 4.6
{'='*60}
  Total scored:       {len(_cal_all)}
  Rejected:           {len(_cal_rej)}
  Passed:             {len(_cal_pass)}
  Trades executed:    {_cal_trades}
  Avg rejected score: {_avg_rej_str}
  Avg passed score:   {_avg_pass_str}
  Near misses (<0.5): {len(_cal_near)}
{'='*60}
  RESULT: {"TRADE FLOW INCREASED ✅" if _cal_trades > 0 or len(_cal_pass) > 0 else "NO CHANGE — still no trades ❌"}
  ACTION: {"Monitor quality of executed trades" if _cal_trades > 0 else "Consider further adjustments or wait for market conditions"}
{'='*60}
""")
    try:
        from core.discord_notifier import send_discord
        send_discord("decision-log", f"🧪 CALIBRATION REPORT @ 100 cycles: scored={len(_cal_all)} rejected={len(_cal_rej)} passed={len(_cal_pass)} trades={_cal_trades}")
    except Exception:
        pass


def _emit_dashboard_discord(cycle_id: int) -> None:
    """Dashboard metrics + Discord emission."""
    try:
        from core.pipeline.dashboard import get_dashboard_metrics
        from core.discord_notifier import send_discord
        _dm = get_dashboard_metrics()
        _diag_lines = [
            f"📊 **Pipeline Diagnostic** | cycle {cycle_id}",
            f"```",
            f"Cycles:        {_dm.get('cycles', 0)}",
            f"Bias reject:   {_dm.get('rejected_bias', 0)}",
            f"Pattern reject:{_dm.get('rejected_pattern', 0)}",
            f"Confirm reject:{_dm.get('rejected_confirmation', 0)}",
            f"Chop filter:   {_dm.get('rejected_chop', 0)}",
            f"Trend filter:  {_dm.get('rejected_trend', 0)}",
            f"Score reject:  {_dm.get('rejected_score', 0)}",
            f"Cooldown:      {_dm.get('rejected_cooldown', 0)}",
            f"Risk reject:   {_dm.get('rejected_risk', 0)}",
            f"Trades exec:   {_dm.get('trades_executed', 0)}",
            f"```",
        ]
        send_discord("decision-log", "\n".join(_diag_lines))
    except Exception:
        pass
