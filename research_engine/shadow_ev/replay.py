"""
Shadow EV Replay — Process historical decisions through all EV models.

Produces comparison metrics: which model would have produced the best outcomes.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from research_engine.shadow_ev.models import compute_shadow_ev, ShadowEVAssessment
from research_engine.counterfactual.simulator import simulate_blocked_decision
from research_engine.counterfactual.schema import SimulationConfidence

logger = logging.getLogger(__name__)


@dataclass
class ModelPerformance:
    """Performance metrics for one EV model."""
    name: str
    approved: int = 0
    blocked: int = 0
    approval_rate: float = 0.0
    # Of approved (with outcome)
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    avg_r: float = 0.0
    total_r: float = 0.0
    profit_factor: float = 0.0
    # Of blocked (counterfactual)
    blocked_wins: int = 0
    blocked_wr: float = 0.0
    # Net
    max_drawdown_r: float = 0.0  # Worst cumulative dip

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "approved": self.approved,
            "blocked": self.blocked,
            "approval_rate": round(self.approval_rate, 4),
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(self.win_rate, 4),
            "avg_r": round(self.avg_r, 4),
            "total_r": round(self.total_r, 4),
            "profit_factor": round(self.profit_factor, 4),
            "blocked_wins": self.blocked_wins,
            "blocked_wr": round(self.blocked_wr, 4),
            "max_drawdown_r": round(self.max_drawdown_r, 4),
        }


@dataclass
class ShadowEVReplayResult:
    """Result of shadow EV historical replay."""
    total_decisions: int = 0
    decisions_with_outcome: int = 0

    models: list[ModelPerformance] = field(default_factory=list)
    assessments_sample: list[dict[str, Any]] = field(default_factory=list)

    opportunity_recovery: dict[str, int] = field(default_factory=dict)
    false_positive_analysis: dict[str, dict[str, Any]] = field(default_factory=dict)

    best_model: str = ""
    conclusion: str = ""
    confidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_decisions": self.total_decisions,
            "decisions_with_outcome": self.decisions_with_outcome,
            "models": [m.to_dict() for m in self.models],
            "opportunity_recovery": self.opportunity_recovery,
            "false_positive_analysis": self.false_positive_analysis,
            "best_model": self.best_model,
            "conclusion": self.conclusion,
            "confidence": self.confidence,
        }


def _compute_model_perf(name: str, assessments: list[ShadowEVAssessment], outcomes: dict[str, float], action_field: str) -> ModelPerformance:
    """Compute performance metrics for one model."""
    mp = ModelPerformance(name=name)

    approved_rs: list[float] = []
    blocked_rs: list[float] = []

    for a in assessments:
        action = getattr(a, action_field)
        r = outcomes.get(a.entity_id)
        if r is None:
            continue
        if action == "EXECUTE":
            mp.approved += 1
            approved_rs.append(r)
        else:
            mp.blocked += 1
            blocked_rs.append(r)

    total = mp.approved + mp.blocked
    mp.approval_rate = mp.approved / total if total > 0 else 0.0

    if approved_rs:
        wins = [r for r in approved_rs if r > 0]
        losses = [r for r in approved_rs if r < 0]
        mp.wins = len(wins)
        mp.losses = len(losses)
        mp.win_rate = len(wins) / len(approved_rs)
        mp.avg_r = sum(approved_rs) / len(approved_rs)
        mp.total_r = sum(approved_rs)
        gw = sum(wins)
        gl = abs(sum(losses))
        mp.profit_factor = gw / gl if gl > 0 else (float("inf") if gw > 0 else 0.0)

        # Max drawdown (cumulative)
        cumulative = 0.0
        peak = 0.0
        worst_dd = 0.0
        for r in approved_rs:
            cumulative += r
            peak = max(peak, cumulative)
            dd = peak - cumulative
            worst_dd = max(worst_dd, dd)
        mp.max_drawdown_r = worst_dd

    if blocked_rs:
        mp.blocked_wins = sum(1 for r in blocked_rs if r > 0)
        mp.blocked_wr = mp.blocked_wins / len(blocked_rs)

    return mp


def run_shadow_ev_replay(
    decision_traces: list[dict[str, Any]],
    replay_dir: str = "replay_data",
) -> ShadowEVReplayResult:
    """
    Replay all historical decisions through all EV models.
    Compare outcomes using counterfactual simulation.
    """
    import json
    from pathlib import Path

    result = ShadowEVReplayResult()

    decisions = [t for t in decision_traces if t.get("pattern_detected") and t.get("components")]
    result.total_decisions = len(decisions)

    if not decisions:
        result.conclusion = "No decisions."
        result.confidence = "INSUFFICIENT_DATA"
        return result

    # Load candles
    candle_cache: dict[str, list[dict]] = {}
    base = Path(replay_dir)
    for sym_dir in base.iterdir():
        if not sym_dir.is_dir():
            continue
        tf_dir = sym_dir / "5"
        if not tf_dir.exists():
            continue
        candles: list[dict] = []
        for f in sorted(tf_dir.glob("*.jsonl")):
            with open(f) as fh:
                for line in fh:
                    if line.strip():
                        try:
                            candles.append(json.loads(line.strip()))
                        except json.JSONDecodeError:
                            pass
        if candles:
            candle_cache[sym_dir.name] = candles

    # Build outcomes
    outcomes: dict[str, float] = {}
    for t in decisions:
        eid = t.get("entity_id", "")
        if not eid:
            continue
        symbol = t.get("symbol", "")
        candles = candle_cache.get(symbol) or candle_cache.get(symbol + "_SB") or candle_cache.get(symbol.replace("_SB", "")) or []
        if candles:
            cf = simulate_blocked_decision(t, candles)
            if cf.simulation_confidence in (SimulationConfidence.HIGH, SimulationConfidence.MEDIUM):
                outcomes[eid] = cf.hypothetical_r

    result.decisions_with_outcome = len(outcomes)
    if result.decisions_with_outcome < 30:
        result.conclusion = "Insufficient outcomes."
        result.confidence = "INSUFFICIENT_DATA"
        return result

    # Build historical win rates from outcomes
    pattern_outcomes: dict[str, list[bool]] = defaultdict(list)
    conditional_outcomes: dict[str, list[bool]] = defaultdict(list)

    for t in decisions:
        eid = t.get("entity_id", "")
        if eid not in outcomes:
            continue
        win = outcomes[eid] > 0
        pattern = t.get("pattern_name", "")
        regime = t.get("regime", "UNKNOWN")
        if pattern:
            pattern_outcomes[pattern].append(win)
            conditional_outcomes[f"{regime}|{pattern}"].append(win)

    pattern_win_rates = {p: sum(ws) / len(ws) for p, ws in pattern_outcomes.items() if len(ws) >= 5}
    pattern_counts = {p: len(ws) for p, ws in pattern_outcomes.items()}
    conditional_win_rates = {k: sum(ws) / len(ws) for k, ws in conditional_outcomes.items() if len(ws) >= 8}
    conditional_counts = {k: len(ws) for k, ws in conditional_outcomes.items()}

    # Run all models
    assessments: list[ShadowEVAssessment] = []
    for t in decisions:
        eid = t.get("entity_id", "")
        if eid not in outcomes:
            continue
        a = compute_shadow_ev(t, pattern_win_rates, pattern_counts, conditional_win_rates, conditional_counts)
        assessments.append(a)

    # Compute performance per model
    result.models = [
        _compute_model_perf("EXISTING", assessments, outcomes, "existing_action"),
        _compute_model_perf("MODEL_A", assessments, outcomes, "model_a_action"),
        _compute_model_perf("MODEL_B", assessments, outcomes, "model_b_action"),
        _compute_model_perf("MODEL_C", assessments, outcomes, "model_c_action"),
    ]

    # Opportunity recovery
    existing_approvals = set(a.entity_id for a in assessments if a.existing_action == "EXECUTE")
    for m in result.models:
        model_field = {"EXISTING": "existing_action", "MODEL_A": "model_a_action", "MODEL_B": "model_b_action", "MODEL_C": "model_c_action"}[m.name]
        model_approvals = set(a.entity_id for a in assessments if getattr(a, model_field) == "EXECUTE")
        recovered = model_approvals - existing_approvals
        result.opportunity_recovery[m.name] = len(recovered)

    # False positive analysis
    for m in result.models:
        model_field = {"EXISTING": "existing_action", "MODEL_A": "model_a_action", "MODEL_B": "model_b_action", "MODEL_C": "model_c_action"}[m.name]
        newly_approved = [a for a in assessments if getattr(a, model_field) == "EXECUTE" and a.existing_action == "NO_TRADE"]
        if newly_approved:
            new_rs = [outcomes[a.entity_id] for a in newly_approved if a.entity_id in outcomes]
            good = sum(1 for r in new_rs if r > 0)
            bad = sum(1 for r in new_rs if r < 0)
            net_r = sum(new_rs)
            result.false_positive_analysis[m.name] = {
                "recovered": len(newly_approved),
                "good_trades": good,
                "bad_trades": bad,
                "net_r": round(net_r, 2),
                "recovery_wr": round(good / len(new_rs), 4) if new_rs else 0.0,
            }

    # Sample assessments (first 5 disagreements)
    disagree = [a for a in assessments if a.disagreement][:5
    ]
    result.assessments_sample = [a.to_dict() for a in disagree]

    # Best model
    best = max(result.models, key=lambda m: m.total_r)
    result.best_model = best.name

    # Confidence
    result.confidence = "HIGH" if result.decisions_with_outcome >= 500 else "MEDIUM" if result.decisions_with_outcome >= 100 else "LOW"

    # Conclusion
    parts = []
    for m in result.models:
        parts.append(f"{m.name}: {m.approved} approved, EV={m.avg_r:+.3f}R, WR={m.win_rate:.0%}, total={m.total_r:+.1f}R")
    parts.append(f"Best: {result.best_model}")
    result.conclusion = ". ".join(parts)

    logger.info("[SHADOW_EV_REPLAY] decisions=%d outcomes=%d best=%s total_r=%.1f",
                result.total_decisions, result.decisions_with_outcome, best.name, best.total_r)
    return result
