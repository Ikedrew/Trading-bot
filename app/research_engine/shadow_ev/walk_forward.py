"""
Walk-Forward Validation for Shadow EV Models.

Splits historical decisions chronologically, trains EV parameters on past data,
evaluates on unseen future data. No future information leakage.

Determines whether shadow models maintain positive expectancy out-of-sample
and whether the edge is concentrated in a single pattern.

This is RESEARCH ONLY. No production code is modified.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from research_engine.shadow_ev.models import (
    _existing_model, _model_a, _model_b, _model_c,
    _get_rr, _PRIOR_WIN_RATE, _PRIOR_WEIGHT, _MIN_SAMPLES_EMPIRICAL,
    _MIN_SAMPLES_CONDITIONAL,
)
from research_engine.counterfactual.simulator import simulate_blocked_decision
from research_engine.counterfactual.schema import SimulationConfidence

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# RESULT STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class SplitPerformance:
    """Performance for one model in one split."""
    model: str
    split: int
    train_size: int = 0
    test_size: int = 0
    approved: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    avg_r: float = 0.0
    total_r: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0
    # Concentration
    patterns_traded: int = 0
    top_pattern: str = ""
    top_pattern_contribution: float = 0.0  # fraction of total_r from top pattern

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model, "split": self.split,
            "train_size": self.train_size, "test_size": self.test_size,
            "approved": self.approved, "wins": self.wins, "losses": self.losses,
            "win_rate": round(self.win_rate, 4),
            "avg_r": round(self.avg_r, 4), "total_r": round(self.total_r, 4),
            "profit_factor": round(self.profit_factor, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "patterns_traded": self.patterns_traded,
            "top_pattern": self.top_pattern,
            "top_pattern_contribution": round(self.top_pattern_contribution, 4),
        }


@dataclass
class PatternDependency:
    """Measures how dependent a model is on a single pattern."""
    model: str
    total_r_all: float = 0.0
    total_r_without_top: float = 0.0
    top_pattern: str = ""
    top_pattern_r: float = 0.0
    top_pattern_fraction: float = 0.0
    edge_survives_without_top: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "total_r_all": round(self.total_r_all, 4),
            "total_r_without_top": round(self.total_r_without_top, 4),
            "top_pattern": self.top_pattern,
            "top_pattern_r": round(self.top_pattern_r, 4),
            "top_pattern_fraction": round(self.top_pattern_fraction, 4),
            "edge_survives_without_top": self.edge_survives_without_top,
        }


@dataclass
class WalkForwardResult:
    """Complete walk-forward validation result."""
    total_decisions: int = 0
    decisions_with_outcome: int = 0
    n_splits: int = 0

    # Per-split per-model results
    splits: list[SplitPerformance] = field(default_factory=list)

    # Aggregated per model
    model_summary: dict[str, dict[str, Any]] = field(default_factory=dict)

    # Pattern dependency
    pattern_dependency: list[PatternDependency] = field(default_factory=list)

    # Acceptance criteria
    acceptance: dict[str, dict[str, Any]] = field(default_factory=dict)

    recommendation: str = ""
    conclusion: str = ""
    confidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_decisions": self.total_decisions,
            "decisions_with_outcome": self.decisions_with_outcome,
            "n_splits": self.n_splits,
            "splits": [s.to_dict() for s in self.splits],
            "model_summary": self.model_summary,
            "pattern_dependency": [p.to_dict() for p in self.pattern_dependency],
            "acceptance": self.acceptance,
            "recommendation": self.recommendation,
            "conclusion": self.conclusion,
            "confidence": self.confidence,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# WALK-FORWARD ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def _train_model_params(
    train_decisions: list[dict[str, Any]],
    outcomes: dict[str, float],
) -> tuple[dict[str, float], dict[str, int], dict[str, float], dict[str, int]]:
    """
    Compute model parameters from training data ONLY.
    Returns: (pattern_win_rates, pattern_counts, conditional_win_rates, conditional_counts)
    """
    pattern_wins: dict[str, list[bool]] = defaultdict(list)
    conditional_wins: dict[str, list[bool]] = defaultdict(list)

    for t in train_decisions:
        eid = t.get("entity_id", "")
        if eid not in outcomes:
            continue
        win = outcomes[eid] > 0
        pattern = t.get("pattern_name", "")
        regime = t.get("regime", "UNKNOWN")
        if pattern:
            pattern_wins[pattern].append(win)
            conditional_wins[f"{regime}|{pattern}"].append(win)

    pattern_win_rates = {p: sum(ws) / len(ws) for p, ws in pattern_wins.items() if len(ws) >= _MIN_SAMPLES_EMPIRICAL}
    pattern_counts = {p: len(ws) for p, ws in pattern_wins.items()}
    conditional_win_rates = {k: sum(ws) / len(ws) for k, ws in conditional_wins.items() if len(ws) >= _MIN_SAMPLES_CONDITIONAL}
    conditional_counts = {k: len(ws) for k, ws in conditional_wins.items()}

    return pattern_win_rates, pattern_counts, conditional_win_rates, conditional_counts


def _evaluate_model_on_test(
    model_name: str,
    model_func,
    test_decisions: list[dict[str, Any]],
    outcomes: dict[str, float],
    split_idx: int,
    train_size: int,
    **model_kwargs: Any,
) -> SplitPerformance:
    """Evaluate one model on one test split."""
    sp = SplitPerformance(model=model_name, split=split_idx, train_size=train_size, test_size=len(test_decisions))

    approved_rs: list[float] = []
    pattern_rs: dict[str, list[float]] = defaultdict(list)

    for t in test_decisions:
        eid = t.get("entity_id", "")
        if eid not in outcomes:
            continue
        pattern = t.get("pattern_name", "")
        rr = _get_rr(pattern)

        if model_name == "EXISTING":
            p, ev = model_func(t)
        else:
            p, ev = model_func(t, **model_kwargs)

        if ev > 0:
            r = outcomes[eid]
            approved_rs.append(r)
            pattern_rs[pattern].append(r)

    sp.approved = len(approved_rs)
    if approved_rs:
        wins = [r for r in approved_rs if r > 0]
        losses = [r for r in approved_rs if r < 0]
        sp.wins = len(wins)
        sp.losses = len(losses)
        sp.win_rate = len(wins) / len(approved_rs)
        sp.avg_r = sum(approved_rs) / len(approved_rs)
        sp.total_r = sum(approved_rs)
        gw = sum(wins)
        gl = abs(sum(losses))
        sp.profit_factor = gw / gl if gl > 0 else (float("inf") if gw > 0 else 0.0)

        # Max drawdown
        cum = 0.0
        peak = 0.0
        worst = 0.0
        for r in approved_rs:
            cum += r
            peak = max(peak, cum)
            worst = max(worst, peak - cum)
        sp.max_drawdown = worst

        # Pattern concentration
        sp.patterns_traded = len(pattern_rs)
        if pattern_rs:
            top_pat = max(pattern_rs, key=lambda p: sum(pattern_rs[p]))
            sp.top_pattern = top_pat
            top_r = sum(pattern_rs[top_pat])
            sp.top_pattern_contribution = top_r / sp.total_r if sp.total_r != 0 else 0.0

    return sp


def run_walk_forward(
    decision_traces: list[dict[str, Any]],
    replay_dir: str = "replay_data",
    n_splits: int = 5,
    min_train_pct: float = 0.3,
) -> WalkForwardResult:
    """
    Run walk-forward validation for all shadow EV models.

    Expanding window: each split trains on all prior data, tests on next chunk.
    """
    import json
    from pathlib import Path

    result = WalkForwardResult()

    decisions = [t for t in decision_traces if t.get("pattern_detected") and t.get("components") and t.get("timestamp_utc")]
    decisions.sort(key=lambda t: t.get("timestamp_utc", ""))
    result.total_decisions = len(decisions)

    if len(decisions) < 50:
        result.conclusion = "Insufficient decisions for walk-forward."
        result.confidence = "INSUFFICIENT_DATA"
        return result

    # Load candles + build outcomes
    candle_cache: dict[str, list[dict]] = {}
    base = Path(replay_dir)
    if base.exists():
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
    if result.decisions_with_outcome < 50:
        result.conclusion = "Insufficient outcomes."
        result.confidence = "INSUFFICIENT_DATA"
        return result

    # Walk-forward splits
    min_train = int(len(decisions) * min_train_pct)
    test_size = (len(decisions) - min_train) // n_splits
    if test_size < 10:
        n_splits = max(1, (len(decisions) - min_train) // 10)
        test_size = (len(decisions) - min_train) // n_splits if n_splits > 0 else len(decisions)

    result.n_splits = n_splits

    # Model accumulators
    model_all_approved: dict[str, list[tuple[float, str]]] = defaultdict(list)  # (R, pattern)

    for i in range(n_splits):
        train_end = min_train + i * test_size
        test_end = min(train_end + test_size, len(decisions))
        train = decisions[:train_end]
        test = decisions[train_end:test_end]

        if len(test) < 5:
            continue

        # Train parameters from training data only
        pat_wr, pat_counts, cond_wr, cond_counts = _train_model_params(train, outcomes)

        # Evaluate each model
        sp_existing = _evaluate_model_on_test(
            "EXISTING", _existing_model, test, outcomes, i + 1, len(train))
        result.splits.append(sp_existing)

        sp_a = _evaluate_model_on_test(
            "MODEL_A", _model_a, test, outcomes, i + 1, len(train),
            pattern_win_rates=pat_wr)
        result.splits.append(sp_a)

        sp_b = _evaluate_model_on_test(
            "MODEL_B", _model_b, test, outcomes, i + 1, len(train),
            pattern_win_rates=pat_wr, pattern_counts=pat_counts)
        result.splits.append(sp_b)

        sp_c = _evaluate_model_on_test(
            "MODEL_C", _model_c, test, outcomes, i + 1, len(train),
            conditional_win_rates=cond_wr, conditional_counts=cond_counts,
            pattern_win_rates=pat_wr)
        result.splits.append(sp_c)

        # Accumulate for pattern dependency
        for sp in [sp_existing, sp_a, sp_b, sp_c]:
            # Re-run to get per-pattern detail
            pass  # Pattern info already in sp

    # Aggregate per model
    for model_name in ["EXISTING", "MODEL_A", "MODEL_B", "MODEL_C"]:
        model_splits = [s for s in result.splits if s.model == model_name]
        positive_splits = sum(1 for s in model_splits if s.total_r > 0)
        total_r = sum(s.total_r for s in model_splits)
        total_approved = sum(s.approved for s in model_splits)
        total_wins = sum(s.wins for s in model_splits)
        all_wrs = [s.win_rate for s in model_splits if s.approved > 0]
        avg_wr = sum(all_wrs) / len(all_wrs) if all_wrs else 0.0
        max_dd = max((s.max_drawdown for s in model_splits), default=0.0)

        result.model_summary[model_name] = {
            "splits_positive": positive_splits,
            "splits_total": len(model_splits),
            "positive_rate": round(positive_splits / len(model_splits), 4) if model_splits else 0.0,
            "total_r": round(total_r, 4),
            "total_approved": total_approved,
            "avg_win_rate": round(avg_wr, 4),
            "max_drawdown": round(max_dd, 4),
        }

    # Pattern dependency analysis (full dataset for each model)
    full_pat_wr, full_pat_counts, full_cond_wr, full_cond_counts = _train_model_params(decisions, outcomes)

    for model_name, model_func, kwargs in [
        ("EXISTING", _existing_model, {}),
        ("MODEL_A", _model_a, {"pattern_win_rates": full_pat_wr}),
        ("MODEL_B", _model_b, {"pattern_win_rates": full_pat_wr, "pattern_counts": full_pat_counts}),
        ("MODEL_C", _model_c, {"conditional_win_rates": full_cond_wr, "conditional_counts": full_cond_counts, "pattern_win_rates": full_pat_wr}),
    ]:
        pattern_totals: dict[str, float] = defaultdict(float)
        for t in decisions:
            eid = t.get("entity_id", "")
            if eid not in outcomes:
                continue
            pattern = t.get("pattern_name", "")
            rr = _get_rr(pattern)
            if model_name == "EXISTING":
                _, ev = model_func(t)
            else:
                _, ev = model_func(t, **kwargs)
            if ev > 0:
                pattern_totals[pattern] += outcomes[eid]

        total_all = sum(pattern_totals.values())
        if pattern_totals:
            top = max(pattern_totals, key=lambda p: pattern_totals[p])
            top_r = pattern_totals[top]
            without_top = total_all - top_r
        else:
            top = ""
            top_r = 0.0
            without_top = 0.0

        pd = PatternDependency(
            model=model_name,
            total_r_all=total_all,
            total_r_without_top=without_top,
            top_pattern=top,
            top_pattern_r=top_r,
            top_pattern_fraction=top_r / total_all if total_all != 0 else 0.0,
            edge_survives_without_top=without_top > 0,
        )
        result.pattern_dependency.append(pd)

    # Acceptance criteria
    for model_name in ["EXISTING", "MODEL_A", "MODEL_B", "MODEL_C"]:
        ms = result.model_summary[model_name]
        pd = next(p for p in result.pattern_dependency if p.model == model_name)
        passes_majority = ms["positive_rate"] > 0.5
        passes_concentration = pd.top_pattern_fraction < 0.5 if pd.total_r_all > 0 else False
        passes_sample = ms["total_approved"] >= 30
        passes_total = ms["total_r"] > 0

        result.acceptance[model_name] = {
            "majority_positive": passes_majority,
            "no_single_pattern_dominance": passes_concentration,
            "sufficient_samples": passes_sample,
            "positive_total_r": passes_total,
            "PASSES_ALL": passes_majority and passes_concentration and passes_sample and passes_total,
        }

    # Recommendation
    passing = [m for m, a in result.acceptance.items() if a["PASSES_ALL"]]
    if passing:
        best = max(passing, key=lambda m: result.model_summary[m]["total_r"])
        result.recommendation = f"REPLACE_CANDIDATE: {best} passes all acceptance criteria."
    else:
        failing_reasons = []
        for m in ["MODEL_A", "MODEL_B", "MODEL_C"]:
            fails = [k for k, v in result.acceptance[m].items() if k != "PASSES_ALL" and not v]
            if fails:
                failing_reasons.append(f"{m} fails: {', '.join(fails)}")
        result.recommendation = f"DO_NOT_REPLACE. {'; '.join(failing_reasons)}"

    # Confidence
    result.confidence = "HIGH" if result.decisions_with_outcome >= 500 and n_splits >= 4 else "MEDIUM" if result.decisions_with_outcome >= 100 else "LOW"

    # Conclusion
    parts = []
    for m in ["EXISTING", "MODEL_A", "MODEL_B", "MODEL_C"]:
        ms = result.model_summary[m]
        parts.append(f"{m}: {ms['splits_positive']}/{ms['splits_total']} positive, total={ms['total_r']:+.1f}R")
    parts.append(f"Recommendation: {result.recommendation}")
    result.conclusion = ". ".join(parts)

    logger.info("[WALK_FORWARD] splits=%d recommendation=%s", n_splits, result.recommendation[:40])
    return result
