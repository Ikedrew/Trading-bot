"""
Exit-Management Research — EX1–EX4 (Wave 1)

Implements the four foundation exit-management research questions using
canonical `shadow_runtime_v1` completed shadow outcomes and their observed
MFE/MAE/path evidence.

SCIENTIFIC BOUNDARY:
    MFE and MAE are OBSERVED PATH FACTS — they tell us what price excursion
    was recorded during each completed shadow lifecycle. They do NOT prove:
        - the ordering of MFE relative to MAE;
        - that an alternative TP or SL would have been hit before the
          original exit;
        - what the exact realised R would have been under another exit policy;
        - whether spread/slippage would alter an alternative exit.

    All EX1–EX4 conclusions are therefore OBSERVATIONAL, not simulated
    counterfactual results. The raw shadow_runtime_v1 CLOSE event carries
    `trade_state_progression` (ordered per-bar {bar, r, close}), but the
    current normalised research record does not preserve it. Until ordered
    path data is surfaced into the research population, no runner in this
    module may claim a simulated counterfactual result.

Populations come exclusively from the canonical shadow_runtime_v1 ingestion
(`ingest_completed_shadow_trades()`). No local fallback, no parallel path.
"""
from __future__ import annotations

import logging
import math
import statistics
from typing import Any

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# EXIT POPULATION
# ═══════════════════════════════════════════════════════════════════════════════

_MIN_SAMPLE = 30
_MIN_SAMPLE_STRICT = 200  # per EX1–EX4 validation rules


def _load_exit_population() -> list[dict[str, Any]]:
    """
    Load the exit-research population from canonical shadow_runtime_v1.

    Returns a list of flat dicts, one per completed shadow lifecycle, with:
        shadow_trade_id, canonical_opportunity_id, symbol, pattern, direction,
        trade_horizon, pnl_r, mfe_r, mae_r, exit_reason, bars_held

    Excludes records with missing/None MFE or MAE (exit research requires
    both). Records with None pnl_r are retained (exit analysis is valid
    even when realised R is absent, though some metrics require it).
    """
    from research_engine.data_access.shadow_runtime_ingestion import (
        ingest_completed_shadow_trades,
    )

    raw = ingest_completed_shadow_trades()
    population: list[dict[str, Any]] = []
    excluded_no_mfe = 0
    excluded_no_mae = 0

    for rec in raw:
        sim = rec.get("simulated_outcome") or {}
        ident = rec.get("identity") or {}
        snap = rec.get("decision_snapshot") or {}

        mfe = sim.get("mfe_r")
        mae = sim.get("mae_r")
        pnl = sim.get("pnl_r_multiple")

        if mfe is None:
            excluded_no_mfe += 1
            continue
        if mae is None:
            excluded_no_mae += 1
            continue

        population.append({
            "shadow_trade_id": ident.get("shadow_trade_id", ""),
            "canonical_opportunity_id": ident.get("canonical_opportunity_id", ""),
            "symbol": ident.get("symbol", ""),
            "pattern": snap.get("pattern", ""),
            "direction": snap.get("direction", ""),
            "trade_horizon": ident.get("evaluated_horizon", ""),
            "pnl_r": float(pnl) if pnl is not None else None,
            "mfe_r": float(mfe),
            "mae_r": float(mae),
            "exit_reason": str(sim.get("exit_reason", "")),
            "bars_held": sim.get("bars_held"),
            "h4_regime": snap.get("h4_regime", ""),
            "market_phase": snap.get("market_phase", ""),
        })

    logger.info(
        "[EXIT_POPULATION] raw=%d eligible=%d excluded_no_mfe=%d excluded_no_mae=%d",
        len(raw), len(population), excluded_no_mfe, excluded_no_mae,
    )
    return population


def _stats(values: list[float]) -> dict[str, Any]:
    """Descriptive statistics for a list of finite floats."""
    if not values:
        return {"n": 0, "mean": None, "median": None, "p25": None, "p75": None}
    vals = sorted(v for v in values if math.isfinite(v))
    n = len(vals)
    if n == 0:
        return {"n": 0, "mean": None, "median": None, "p25": None, "p75": None}

    def _pct(p: float) -> float:
        idx = int(n * p)
        return round(vals[min(idx, n - 1)], 4)

    return {
        "n": n,
        "mean": round(statistics.mean(vals), 4),
        "median": round(statistics.median(vals), 4),
        "p25": _pct(0.25),
        "p75": _pct(0.75),
        "min": round(vals[0], 4),
        "max": round(vals[-1], 4),
    }


def _pct_reaching(values: list[float], threshold: float) -> float:
    """Fraction of values >= threshold (for MFE) or <= threshold (for MAE)."""
    if not values:
        return 0.0
    return round(sum(1 for v in values if v >= threshold) / len(values), 4)


def _pct_below(values: list[float], threshold: float) -> float:
    """Fraction of values <= threshold (for MAE)."""
    if not values:
        return 0.0
    return round(sum(1 for v in values if v <= threshold) / len(values), 4)


def _confidence(n: int) -> str:
    if n >= _MIN_SAMPLE_STRICT:
        return "HIGH"
    if n >= _MIN_SAMPLE:
        return "MEDIUM"
    if n > 0:
        return "LOW"
    return "INSUFFICIENT_DATA"


def _make_report(
    question_id: str, status: str, overall: dict, confidence: str,
    dataset: dict, recommendation: str,
    assumptions: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Build a canonical report for exit-management questions."""
    from research_engine.experiments.experiment_base import build_report, build_fingerprint

    sample = dataset.get("sample_size", 0)
    return build_report(
        question_id=question_id,
        status=status,
        overall=overall,
        confidence=confidence,
        dataset=dataset,
        fingerprint=build_fingerprint(sample, 0, "shadow_runtime_v1"),
        recommendation=recommendation,
        assumptions=assumptions or [],
        warnings=warnings or [],
        provenance={
            "experiment_module": "research_engine.experiments.exit_management",
            "registry_id": question_id,
            "pipeline": "Question -> Experiment -> Dataset -> Output -> Knowledge -> Command Centre",
        },
    )


def _insufficient(question_id: str, population_n: int) -> dict[str, Any]:
    """Standard INSUFFICIENT_DATA report when population is too small."""
    conf = _confidence(population_n)
    return _make_report(
        question_id=question_id,
        status="INSUFFICIENT_DATA",
        overall={
            "finding": f"Insufficient exit evidence: N={population_n} < {_MIN_SAMPLE}",
            "sample_size": population_n,
        },
        confidence=conf,
        dataset={"source": "shadow_runtime_v1(ingested)", "sample_size": population_n},
        recommendation="WAIT",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# EX1 — EXIT EFFICIENCY / EXIT POLICY
# ═══════════════════════════════════════════════════════════════════════════════


def run_ex1() -> dict[str, Any]:
    """
    EX1: Does the current exit behaviour capture favourable excursion
    efficiently, or does it leave meaningful MFE uncaptured?

    Observational metrics (NOT counterfactual):
        - MFE capture ratio: realised_r / mfe_r for winning trades (mfe_r > 0)
        - Giveback: mfe_r - realised_r (excursion not captured)
        - Reversal frequency: trades with positive MFE that ended negative
        - Segmentation by exit_reason
    """
    population = _load_exit_population()
    n = len(population)
    if n < _MIN_SAMPLE:
        return _insufficient("EX1", n)

    pnls = [r["pnl_r"] for r in population if r["pnl_r"] is not None]
    mfes = [r["mfe_r"] for r in population]
    maes = [r["mae_r"] for r in population]

    # Favourable excursion capture (only for trades with positive MFE)
    pos_mfe = [r for r in population if r["mfe_r"] > 0 and r["pnl_r"] is not None]
    capture_ratios = []
    givebacks = []
    for r in pos_mfe:
        if r["mfe_r"] > 0.05:  # avoid division by near-zero MFE
            capture_ratios.append(r["pnl_r"] / r["mfe_r"])
            givebacks.append(r["mfe_r"] - r["pnl_r"])

    # Reversal: positive MFE but negative realised outcome
    reversals = [r for r in pos_mfe if r["pnl_r"] is not None and r["pnl_r"] < 0]
    reversal_rate = round(len(reversals) / len(pos_mfe), 4) if pos_mfe else 0.0

    # By exit reason
    by_reason: dict[str, dict] = {}
    for reason in sorted({r["exit_reason"] for r in population if r["exit_reason"]}):
        group = [r for r in population if r["exit_reason"] == reason]
        group_pnl = [r["pnl_r"] for r in group if r["pnl_r"] is not None]
        group_mfe = [r["mfe_r"] for r in group]
        if len(group) >= 10:  # minimum per-cell sample
            by_reason[reason] = {
                "n": len(group),
                "mean_pnl": round(statistics.mean(group_pnl), 4) if group_pnl else None,
                "mean_mfe": round(statistics.mean(group_mfe), 4) if group_mfe else None,
                "mean_mae": round(statistics.mean([r["mae_r"] for r in group]), 4),
                "win_rate": round(sum(1 for p in group_pnl if p > 0) / len(group_pnl), 4) if group_pnl else 0,
            }

    mean_capture = round(statistics.mean(capture_ratios), 4) if capture_ratios else None
    mean_giveback = round(statistics.mean(givebacks), 4) if givebacks else None

    # Determine status/conclusion
    if n < _MIN_SAMPLE_STRICT:
        status = "INSUFFICIENT_DATA"
        recommendation = "WAIT"
        finding = f"Insufficient exit evidence: N={n} < {_MIN_SAMPLE_STRICT}. Descriptive metrics below are preliminary."
    elif mean_capture is not None and mean_capture < 0.4:
        status = "COMPLETE"
        recommendation = "FINDING: significant favourable-excursion giveback observed"
        finding = (
            f"Mean MFE capture ratio {mean_capture:.2f} — trades retain only "
            f"{mean_capture:.0%} of their peak favourable excursion on average. "
            f"Mean giveback {mean_giveback:+.3f}R. Reversal rate (positive MFE -> negative exit): {reversal_rate:.0%}."
        )
    elif mean_capture is not None and mean_capture >= 0.7:
        status = "COMPLETE"
        recommendation = "FINDING: favourable excursion efficiently captured"
        finding = (
            f"Mean MFE capture ratio {mean_capture:.2f} — trades retain "
            f"{mean_capture:.0%} of their peak favourable excursion on average."
        )
    else:
        status = "COMPLETE"
        recommendation = "FINDING: mixed/inconclusive exit efficiency"
        finding = (
            f"Mean MFE capture ratio {mean_capture:.2f} — mixed capture efficiency. "
            f"Reversal rate: {reversal_rate:.0%}."
        )

    return _make_report(
        question_id="EX1",
        status=status,
        overall={
            "finding": finding,
            "sample_size": n,
            "mean_realised_r": round(statistics.mean(pnls), 4) if pnls else None,
            "mean_mfe_r": round(statistics.mean(mfes), 4) if mfes else None,
            "mean_mae_r": round(statistics.mean(maes), 4) if maes else None,
            "mfe_capture_ratio": mean_capture,
            "mean_giveback_r": mean_giveback,
            "reversal_rate": reversal_rate,
            "by_exit_reason": by_reason,
            "mfe_distribution": _stats(mfes),
            "mae_distribution": _stats(maes),
            "pnl_distribution": _stats(pnls),
            "methodology": "observational (MFE/MAE summary, no path-ordering assumption)",
        },
        confidence=_confidence(n),
        dataset={"source": "shadow_runtime_v1(ingested)", "sample_size": n},
        recommendation=recommendation,
        assumptions=[
            "MFE/MAE are observed path facts, not counterfactual proof",
            "MFE is the maximum favourable excursion in R over the lifecycle",
            "MAE is the maximum adverse excursion in R (negative or zero)",
            "Capture ratio = realised_r / mfe_r; only computed when mfe_r > 0.05",
            "No path ordering assumed — MFE and MAE ordering is unknown",
        ],
        warnings=[
            "Observational analysis only — does NOT prove an alternative exit policy would improve outcomes",
        ] if n < _MIN_SAMPLE_STRICT else [
            "Observational analysis — conclusions are about observed behaviour, not counterfactual policy simulation",
        ],
    )


# ═══════════════════════════════════════════════════════════════════════════════
# EX2 — TRAILING-STOP / PROFIT-RETENTION EVIDENCE
# ═══════════════════════════════════════════════════════════════════════════════


def run_ex2() -> dict[str, Any]:
    """
    EX2: Among trades that achieved meaningful favourable excursion,
    how much was retained at exit?

    Observational retention analysis (NOT a trailing-stop simulation).
    Segments trades by MFE bucket and reports retention/giveback per bucket.
    """
    population = _load_exit_population()
    n = len(population)
    if n < _MIN_SAMPLE:
        return _insufficient("EX2", n)

    # Only trades with meaningful favourable excursion
    favourable = [
        r for r in population
        if r["mfe_r"] >= 0.5 and r["pnl_r"] is not None
    ]
    n_favourable = len(favourable)

    # MFE buckets
    buckets = [
        ("0.5R-1R", 0.5, 1.0),
        ("1R-1.5R", 1.0, 1.5),
        ("1.5R-2R", 1.5, 2.0),
        ("2R-3R", 2.0, 3.0),
        ("3R+", 3.0, float("inf")),
    ]
    bucket_results = {}
    for label, lo, hi in buckets:
        group = [r for r in favourable if lo <= r["mfe_r"] < hi]
        if not group:
            continue
        pnls = [r["pnl_r"] for r in group]
        mfes = [r["mfe_r"] for r in group]
        retention = [
            r["pnl_r"] / r["mfe_r"] for r in group
            if r["mfe_r"] > 0 and r["pnl_r"] is not None
        ]
        surrendered = [
            r for r in group if r["pnl_r"] is not None and r["pnl_r"] < 0
        ]
        bucket_results[label] = {
            "n": len(group),
            "mean_mfe": round(statistics.mean(mfes), 4) if mfes else None,
            "mean_realised": round(statistics.mean(pnls), 4) if pnls else None,
            "mean_retention": round(statistics.mean(retention), 4) if retention else None,
            "surrendered_to_loss": len(surrendered),
            "surrender_rate": round(len(surrendered) / len(group), 4) if group else 0,
        }

    # Overall retention
    all_retention = [
        r["pnl_r"] / r["mfe_r"] for r in favourable
        if r["mfe_r"] > 0 and r["pnl_r"] is not None
    ]
    mean_retention = round(statistics.mean(all_retention), 4) if all_retention else None
    total_surrendered = sum(
        1 for r in favourable if r["pnl_r"] is not None and r["pnl_r"] < 0
    )
    surrender_rate = round(total_surrendered / n_favourable, 4) if n_favourable else 0

    # Exit-reason breakdown for surrendered trades
    surrendered_by_reason: dict[str, int] = {}
    for r in favourable:
        if r["pnl_r"] is not None and r["pnl_r"] < 0:
            reason = r["exit_reason"]
            surrendered_by_reason[reason] = surrendered_by_reason.get(reason, 0) + 1

    if n_favourable < _MIN_SAMPLE:
        status = "INSUFFICIENT_DATA"
        recommendation = "WAIT"
        finding = (
            f"Insufficient favourable-excursion evidence: N={n_favourable} "
            f"(MFE >= 0.5R) out of {n} total. "
        )
    elif mean_retention is not None and mean_retention < 0.3:
        status = "COMPLETE"
        recommendation = "FINDING: substantial favourable-excursion surrender observed"
        finding = (
            f"Mean retention {mean_retention:.2f} among {n_favourable} trades with "
            f"MFE >= 0.5R. {total_surrendered} ({surrender_rate:.0%}) reversed to loss."
        )
    elif mean_retention is not None and mean_retention >= 0.6:
        status = "COMPLETE"
        recommendation = "FINDING: favourable excursion substantially retained"
        finding = (
            f"Mean retention {mean_retention:.2f} among {n_favourable} trades "
            f"with MFE >= 0.5R. Only {total_surrendered} ({surrender_rate:.0%}) reversed to loss."
        )
    else:
        status = "COMPLETE"
        recommendation = "FINDING: mixed retention behaviour"
        finding = (
            f"Mean retention {mean_retention:.2f} among {n_favourable} trades. "
            f"{total_surrendered} ({surrender_rate:.0%}) surrendered to loss."
        )

    return _make_report(
        question_id="EX2",
        status=status,
        overall={
            "finding": finding,
            "sample_size": n,
            "favourable_n": n_favourable,
            "mean_retention_ratio": mean_retention,
            "surrender_rate": surrender_rate,
            "surrendered_by_reason": surrendered_by_reason,
            "by_mfe_bucket": bucket_results,
            "methodology": "observational retention analysis (no path-ordering assumption)",
        },
        confidence=_confidence(n),
        dataset={"source": "shadow_runtime_v1(ingested)", "sample_size": n},
        recommendation=recommendation,
        assumptions=[
            "MFE is the peak favourable excursion — trades that reached this level retained at least this much at some point",
            "Retention = realised_r / mfe_r (only for trades with mfe_r >= 0.5R)",
            "NO trailing-stop simulation performed — path ordering is unavailable in the normalised research record",
            "A low retention ratio indicates the trade gave back peak excursion but does NOT prove a trailing stop would have retained more",
        ],
        warnings=[
            "Observational analysis — the raw shadow_runtime_v1 CLOSE event carries "
            "trade_state_progression (ordered per-bar data) that could support true "
            "trailing-stop simulation, but it is not preserved in the normalised record",
        ],
    )


# ═══════════════════════════════════════════════════════════════════════════════
# EX3 — TP-DISTANCE EVIDENCE
# ═══════════════════════════════════════════════════════════════════════════════


def run_ex3() -> dict[str, Any]:
    """
    EX3: What favourable excursion distances do the opportunities reach?
    What fraction reach >= 0.5R, >= 1R, >= 1.5R, >= 2R, >= 3R?

    This is a REACHABILITY/DISTRIBUTION question, not an optimal-TP claim.
    """
    population = _load_exit_population()
    n = len(population)
    if n < _MIN_SAMPLE:
        return _insufficient("EX3", n)

    mfes = [r["mfe_r"] for r in population if math.isfinite(r["mfe_r"])]
    thresholds = [0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0]
    reachability = {}
    for t in thresholds:
        reachability[f">={t}R"] = _pct_reaching(mfes, t)

    dist = _stats(mfes)

    # TP hit rate if we know the intended TP
    intended_tps = [
        r["mfe_r"] for r in population
        if r.get("pnl_r") is not None and r["exit_reason"] == "take_profit"
    ]
    tp_hits = len(intended_tps)
    tp_rate = round(tp_hits / n, 4) if n else 0

    if n < _MIN_SAMPLE_STRICT:
        status = "INSUFFICIENT_DATA"
        recommendation = "WAIT"
        finding = f"Insufficient exit evidence: N={n} < {_MIN_SAMPLE_STRICT}."
    else:
        med = dist.get("median", 0) or 0
        p75 = dist.get("p75", 0) or 0
        status = "COMPLETE"
        recommendation = "FINDING: TP reachability profile computed"
        finding = (
            f"MFE distribution: median={med:.3f}R, p75={p75:.3f}R, max={dist.get('max', 0):.3f}R. "
            f"Reachability: " + ", ".join(f"{k}={v:.0%}" for k, v in reachability.items()) + ". "
            f"Current TP hit rate: {tp_rate:.0%}."
        )

    return _make_report(
        question_id="EX3",
        status=status,
        overall={
            "finding": finding,
            "sample_size": n,
            "mfe_distribution": dist,
            "reachability_profile": reachability,
            "current_tp_hit_rate": tp_rate,
            "tp_hits": tp_hits,
            "methodology": "TP_DISTANCE_EVIDENCE (observed favourable excursion reachability)",
        },
        confidence=_confidence(n),
        dataset={"source": "shadow_runtime_v1(ingested)", "sample_size": n},
        recommendation=recommendation,
        assumptions=[
            "MFE is the peak favourable excursion in R — the maximum distance the trade moved in favour",
            "Reachability = fraction of trades whose MFE >= threshold",
            "This is a REACHABILITY PROFILE, not an optimal-TP claim",
            "No path-ordering assumption — MFE may have occurred before or after MAE",
            "An MFE of 2R does NOT prove a 2R TP would have been hit before an adverse event",
        ],
        warnings=[
            "Multiple TP thresholds examined (0.25R through 3.0R) — this is a "
            "distribution analysis, not a single-threshold optimisation",
        ],
    )


# ═══════════════════════════════════════════════════════════════════════════════
# EX4 — SL-DISTANCE EVIDENCE
# ═══════════════════════════════════════════════════════════════════════════════


def run_ex4() -> dict[str, Any]:
    """
    EX4: What adverse-excursion distances do the opportunities experience?
    How much MAE do eventual winners survive vs eventual losers?

    This is an ADVERSE-EXCURSION PROFILE, not an optimal-SL claim.
    """
    population = _load_exit_population()
    n = len(population)
    if n < _MIN_SAMPLE:
        return _insufficient("EX4", n)

    maes = [r["mae_r"] for r in population if math.isfinite(r["mae_r"])]

    # MAE thresholds
    thresholds = [-0.25, -0.5, -0.75, -1.0]
    adverse_profile = {}
    for t in thresholds:
        adverse_profile[f"<={t}R"] = _pct_below(maes, t)

    dist = _stats(maes)

    # Winners vs losers MAE
    winners = [r["mae_r"] for r in population if r["pnl_r"] is not None and r["pnl_r"] > 0]
    losers = [r["mae_r"] for r in population if r["pnl_r"] is not None and r["pnl_r"] <= 0]
    winner_mae = _stats(winners)
    loser_mae = _stats(losers)

    # How many eventual winners experienced >= 0.5R adverse excursion?
    winner_deep_mae = sum(1 for m in winners if m <= -0.5)
    winner_deep_rate = round(winner_deep_mae / len(winners), 4) if winners else 0

    # How many eventual losers stayed shallow (< 0.25R adverse)?
    loser_shallow = sum(1 for m in losers if m > -0.25)
    loser_shallow_rate = round(loser_shallow / len(losers), 4) if losers else 0

    n_winners = len(winners)
    n_losers = len(losers)

    if n < _MIN_SAMPLE_STRICT:
        status = "INSUFFICIENT_DATA"
        recommendation = "WAIT"
        finding = f"Insufficient exit evidence: N={n} < {_MIN_SAMPLE_STRICT}."
    else:
        med = dist.get("median", 0) or 0
        status = "COMPLETE"
        recommendation = "FINDING: adverse-excursion profile computed"
        finding = (
            f"MAE distribution: median={med:.3f}R, p25={dist.get('p25', 0):.3f}R. "
            f"Winners (n={n_winners}): mean MAE={winner_mae.get('mean', 0):.3f}R, "
            f"deep MAE (<=-0.5R) rate={winner_deep_rate:.0%}. "
            f"Losers (n={n_losers}): mean MAE={loser_mae.get('mean', 0):.3f}R, "
            f"shallow MAE (>-0.25R) rate={loser_shallow_rate:.0%}."
        )

    return _make_report(
        question_id="EX4",
        status=status,
        overall={
            "finding": finding,
            "sample_size": n,
            "mae_distribution": dist,
            "adverse_excursion_profile": adverse_profile,
            "winners": {"n": n_winners, "mae_stats": winner_mae,
                        "deep_mae_rate": winner_deep_rate},
            "losers": {"n": n_losers, "mae_stats": loser_mae,
                       "shallow_mae_rate": loser_shallow_rate},
            "methodology": "SL_DISTANCE_EVIDENCE (observed adverse excursion profile)",
        },
        confidence=_confidence(n),
        dataset={"source": "shadow_runtime_v1(ingested)", "sample_size": n},
        recommendation=recommendation,
        assumptions=[
            "MAE is the peak adverse excursion in R (negative or zero) over the lifecycle",
            "Adverse profile = fraction of trades whose MAE <= threshold",
            "This is an ADVERSE-EXCURSION PROFILE, not an optimal-SL claim",
            "No path-ordering assumption — MAE may have occurred before or after MFE",
            "An MAE of -1R does NOT prove a 1R SL would have been hit (MFE may have occurred first)",
        ],
        warnings=[
            "Multiple SL thresholds examined (-0.25R through -1.0R) — this is a "
            "distribution analysis, not a single-threshold optimisation",
        ],
    )


# ═══════════════════════════════════════════════════════════════════════════════
# CONVENIENCE: run all four
# ═══════════════════════════════════════════════════════════════════════════════


def run_all_exit_questions() -> dict[str, dict[str, Any]]:
    """Run EX1-EX4 and return their reports keyed by question_id."""
    return {
        "EX1": run_ex1(),
        "EX2": run_ex2(),
        "EX3": run_ex3(),
        "EX4": run_ex4(),
    }
