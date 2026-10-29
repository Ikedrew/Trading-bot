"""
WAVE 9 — Exit-Management Depth Research Runners (EX5–EX10).

Implements the remaining six exit-management research questions using
canonical shadow_runtime_v1 evidence.

Foundation EX1–EX4 (exit_management.py) already own:
    EX1: Overall exit efficiency / MFE capture
    EX2: Trailing-stop concept analysis
    EX3: Optimal TP distance (MFE reachability)
    EX4: Optimal SL distance (MAE adverse-excursion profile)

EX5–EX10 extend this by asking whether exit policy should vary by:
    EX5: Trade horizon (SCALP/INTRADAY/EXTENDED)
    EX6: Strategy family (REVERSAL/CONTINUATION/FALSE_BREAK)
    EX7: Market regime (TRENDING/RANGING/TRANSITIONAL)
    EX8: Pattern type
    EX9: Whether a different exit reduces timeout losses
    EX10: Whether exit improvement survives walk-forward validation

EVIDENCE CLASSIFICATION:
    These are OBSERVATIONAL / DERIVED-FROM-OBSERVED-PATH analyses.
    MFE and MAE are OBSERVED PATH FACTS from completed shadow lifecycles.
    Normalised records preserve trade_state_progression when the raw
    shadow_runtime_v1 CLOSE event carries it, but these runners do not yet
    simulate alternative exit policies from that path.

    Without a dedicated path-aware exit simulator, EX5–EX8 cannot perform true
    counterfactual trailing-stop simulation. They instead answer whether
    exit efficiency (MFE capture, MAE exposure, exit_reason distribution)
    differs materially across the dimension being tested.

    This is a valid research question: if, e.g., SCALP trades have
    significantly worse MFE capture than INTRADAY trades, it suggests
    horizon-specific exit policy is worth investigating — even though
    the exact alternative exit mechanism cannot be simulated here.

Temporal ordering:
    Only EX10 requires chronological ordering (walk-forward split).
    EX5–EX9 are cross-sectional.

Clean-evidence boundary:
    All evidence comes from shadow_runtime_v1 ingested completed lifecycles.
    The shadow runtime simulates outcomes using closed-bar candle data.
    Forming-bar decision contamination does not affect closed-bar
    shadow outcome facts (MFE/MAE/PNL are path facts).
"""

from __future__ import annotations

import logging
import math
import statistics
from collections import defaultdict
from typing import Any

from research_engine.experiments.exit_management import (
    _confidence,
    _make_report,
    _stats,
    _pct_reaching,
    _pct_below,
    _insufficient,
)

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# SHARED HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

_MIN_CELL = 10  # minimum per-segment sample for reporting


def _load_exit_depth_population() -> list[dict[str, Any]]:
    """
    Load exit depth population — like _load_exit_population but adds
    strategy, entry_time, epoch classification, and other segmentation
    fields needed for EX5–EX10.

    Uses canonical shadow_runtime_v1 ingestion directly.

    CURRENT-epoch filtering: EX5–EX10 condition exit metrics on
    system-created context fields (strategy, horizon, regime, pattern).
    A pre-fix shadow lifecycle may contain factual MFE/MAE/PNL, but
    the shadow position may have originated from a forming-bar decision
    that the corrected system would not have produced. Therefore only
    CURRENT-epoch records are eligible for corrected strategy-conditioned
    exit research.

    LEGACY/TRANSITIONAL records are counted but excluded.
    """
    from research_engine.data_access.shadow_runtime_ingestion import (
        ingest_completed_shadow_trades,
    )
    from research_engine.data_quality.classifier import classify_record, DataEpoch

    raw = ingest_completed_shadow_trades()
    population: list[dict[str, Any]] = []
    excluded_no_mfe = 0
    excluded_no_mae = 0
    excluded_not_current = 0

    for rec in raw:
        # Epoch classification — only CURRENT records for corrected strategy research
        epoch = classify_record(rec)
        if epoch != DataEpoch.CURRENT:
            excluded_not_current += 1
            continue

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

        # Strategy from identity or decision_snapshot
        strategy = str(
            ident.get("strategy_id", "")
            or snap.get("strategy", "")
            or ""
        )

        # Entry time from simulated_outcome exit_timestamp or decision_snapshot
        entry_time = (
            sim.get("exit_timestamp")
            or snap.get("timestamp_decision_utc")
            or 0
        )

        population.append({
            "shadow_trade_id": ident.get("shadow_trade_id", ""),
            "canonical_opportunity_id": ident.get("canonical_opportunity_id", ""),
            "symbol": ident.get("symbol", ""),
            "pattern": snap.get("pattern", ""),
            "direction": snap.get("direction", ""),
            "strategy": strategy,
            "trade_horizon": ident.get("evaluated_horizon", ""),
            "pnl_r": float(pnl) if pnl is not None else None,
            "mfe_r": float(mfe),
            "mae_r": float(mae),
            "exit_reason": str(sim.get("exit_reason", "")),
            "bars_held": sim.get("bars_held"),
            "trade_state_progression": list(sim.get("trade_state_progression") or []),
            "trade_state_progression_status": sim.get(
                "trade_state_progression_status", "MISSING"
            ),
            "h4_regime": snap.get("h4_regime", ""),
            "market_phase": snap.get("market_phase", ""),
            "entry_time": float(entry_time) if entry_time else 0.0,
            "data_epoch": epoch.value,
        })

    logger.info(
        "[EXIT_DEPTH_POPULATION] raw=%d current=%d "
        "excluded_no_mfe=%d excluded_no_mae=%d excluded_not_current=%d",
        len(raw), len(population),
        excluded_no_mfe, excluded_no_mae, excluded_not_current,
    )
    return population


def _segment_exit_population(
    population: list[dict[str, Any]],
    dimension_fn,
) -> dict[str, Any]:
    """
    Segment the exit population by a dimension extractor function.

    For each segment, computes:
        - count
        - pnl stats (mean, win_rate)
        - MFE stats (mean, capture ratio)
        - MAE stats (mean, deep MAE rate)
        - exit_reason distribution
        - MFE capture ratio (realised_r / mfe_r for trades with mfe_r > 0.05)
        - Giveback (mfe_r - realised_r)
    """
    segment_mfes: dict[str, list[float]] = defaultdict(list)
    segment_maes: dict[str, list[float]] = defaultdict(list)
    segment_pnls: dict[str, list[float]] = defaultdict(list)
    segment_exit_reasons: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    segment_capture: dict[str, list[float]] = defaultdict(list)
    segment_giveback: dict[str, list[float]] = defaultdict(list)

    for rec in population:
        seg = dimension_fn(rec)
        if not seg:
            continue

        pnl = rec.get("pnl_r")
        mfe = rec["mfe_r"]
        mae = rec["mae_r"]
        reason = rec.get("exit_reason", "unknown")

        if pnl is not None:
            segment_pnls[seg].append(pnl)
        segment_mfes[seg].append(mfe)
        segment_maes[seg].append(mae)
        segment_exit_reasons[seg][reason] += 1

        # Capture ratio (only for trades with meaningful MFE)
        if mfe > 0.05 and pnl is not None:
            segment_capture[seg].append(pnl / mfe)
            segment_giveback[seg].append(mfe - pnl)

    # Build result
    result_segments = {}
    for seg in sorted(segment_mfes):
        n = len(segment_mfes[seg])
        if n < _MIN_CELL:
            continue
        pnls = segment_pnls.get(seg, [])
        mfes = segment_mfes.get(seg, [])
        maes = segment_maes.get(seg, [])
        reasons = dict(segment_exit_reasons.get(seg, {}))
        captures = segment_capture.get(seg, [])
        givebacks = segment_giveback.get(seg, [])

        win_rate = sum(1 for p in pnls if p > 0) / len(pnls) if pnls else 0

        segment_info = {
            "count": n,
            "pnl": _stats(pnls) if pnls else {"n": 0, "mean": None},
            "mfe": _stats(mfes),
            "mae": _stats(maes),
            "win_rate": round(win_rate, 4),
            "exit_reasons": reasons,
        }

        if captures:
            segment_info["capture_ratio"] = _stats(captures)
            segment_info["giveback"] = _stats(givebacks)

        result_segments[seg] = segment_info

    return result_segments


def _extract_dimension(rec: dict[str, Any], field: str, valid_values: set) -> str:
    """Extract a dimension value, returning empty if not valid."""
    val = str(rec.get(field, "") or "")
    return val if val in valid_values else ""


# ═══════════════════════════════════════════════════════════════════════════════
# EX5 — Horizon changes optimal exit
# ═══════════════════════════════════════════════════════════════════════════════

CANONICAL_INTENT_EX5 = (
    "Does trade horizon (SCALP/INTRADAY/EXTENDED) require a different "
    "exit policy? Tests whether MFE capture, MAE exposure, and exit reason "
    "distribution differ materially by horizon."
)

_OWNERSHIP_EX5 = (
    "EX5 uniquely owns: horizon-conditioned exit efficiency analysis.\n"
    "EX1 owns overall exit efficiency (all horizons combined).\n"
    "EX3 owns TP optimisation using MFE reachability.\n"
    "EX5 segments EX1's metrics by trade_horizon.\n"
    "S2/S3 own horizon expectancy (not exit efficiency)."
)


def _horizon_fn(rec: dict[str, Any]) -> str:
    return _extract_dimension(
        rec, "trade_horizon",
        {"SCALP", "INTRADAY", "EXTENDED"},
    )


def run_ex5() -> dict[str, Any]:
    """EX5: Horizon-specific exit efficiency."""
    question_id = "EX5"
    population = _load_exit_depth_population()
    n = len(population)
    if n < 30:
        return _insufficient(question_id, n)

    segments = _segment_exit_population(population, _horizon_fn)

    if not segments:
        return _insufficient(question_id, n)

    total_analysed = sum(s["count"] for s in segments.values())

    # Determine if horizons differ materially in capture
    capture_means = {
        seg: s.get("capture_ratio", {}).get("mean")
        for seg, s in segments.items()
        if s.get("capture_ratio")
    }
    capture_available = len([v for v in capture_means.values() if v is not None])
    horizons_differ = False
    if capture_available >= 2:
        values = [v for v in capture_means.values() if v is not None]
        if max(values) - min(values) > 0.15:
            horizons_differ = True

    status = "COMPLETE"
    recommendation = "FINDING: horizon-specific exit metrics"

    overall = {
        "finding": f"Horizon-specific exit efficiency for {len(segments)} horizons",
        "question": CANONICAL_INTENT_EX5,
        "ownership_note": _OWNERSHIP_EX5,
        "segmentation": "trade_horizon",
        "segments": segments,
        "horizons_differ_in_capture": horizons_differ,
        "methodology": (
            "OBSERVATIONAL: segmented MFE capture, MAE, and exit reason "
            "by trade horizon. Counterfactual trailing simulation requires "
            "a dedicated path-aware simulator over normalised "
            "trade_state_progression."
        ),
    }

    return _make_report(
        question_id=question_id,
        status=status,
        overall=overall,
        confidence=_confidence(total_analysed),
        dataset={"source": "shadow_runtime_v1(ingested)", "sample_size": total_analysed},
        recommendation=recommendation,
        assumptions=[
            "MFE/MAE are observed path facts — ordering within the trade is unknown",
            "Capture ratio assumes mfe_r > 0.05 for meaningful division",
            "Horizon label comes from evaluated_horizon in shadow_runtime_v1",
        ],
        warnings=[
            "Normalised trade_state_progression is not consumed by this runner — "
            "true counterfactual trailing analysis requires a dedicated simulator",
        ],
    )


# ═══════════════════════════════════════════════════════════════════════════════
# EX6 — Exit depends on strategy family
# ═══════════════════════════════════════════════════════════════════════════════

CANONICAL_INTENT_EX6 = (
    "Does each strategy family (REVERSAL/MOMENTUM/CONTINUATION) require "
    "a different exit policy? Tests whether exit efficiency metrics differ "
    "materially by strategy."
)

_OWNERSHIP_EX6 = (
    "EX6 uniquely owns: strategy-conditioned exit efficiency analysis.\n"
    "E3/E4 own strategy overall expectancy (not exit efficiency).\n"
    "EX1 owns overall exit efficiency (all strategies combined).\n"
    "EX6 segments EX1's metrics by strategy family."
)

_VALID_STRATEGIES = {"REVERSAL", "CONTINUATION", "FALSE_BREAK"}


def _strategy_fn(rec: dict[str, Any]) -> str:
    # Check strategy field from decision_snapshot (pattern in the population)
    # The exit population stores it in the original record
    return _extract_dimension(rec, "strategy", _VALID_STRATEGIES)


def run_ex6() -> dict[str, Any]:
    """EX6: Strategy-specific exit efficiency."""
    question_id = "EX6"
    population = _load_exit_depth_population()
    n = len(population)
    if n < 30:
        return _insufficient(question_id, n)

    segments = _segment_exit_population(population, _strategy_fn)

    if not segments:
        return _insufficient(question_id, n)

    total_analysed = sum(s["count"] for s in segments.values())

    # Check if strategies differ
    means = {s: seg.get("pnl", {}).get("mean") for s, seg in segments.items()}
    available = [v for v in means.values() if v is not None]
    strategies_differ = False
    if len(available) >= 2:
        if max(available) - min(available) > 0.15:
            strategies_differ = True

    overall = {
        "finding": f"Strategy-specific exit efficiency for {len(segments)} strategies",
        "question": CANONICAL_INTENT_EX6,
        "ownership_note": _OWNERSHIP_EX6,
        "segmentation": "strategy",
        "segments": segments,
        "strategies_differ_in_exit_efficiency": strategies_differ,
        "methodology": "OBSERVATIONAL",
    }

    return _make_report(
        question_id=question_id,
        status="COMPLETE",
        overall=overall,
        confidence=_confidence(total_analysed),
        dataset={"source": "shadow_runtime_v1(ingested)", "sample_size": total_analysed},
        recommendation="FINDING: strategy-specific exit metrics",
        assumptions=[
            "Strategy label from decision_snapshot.strategy or identity.strategy_id",
        ],
        warnings=[],
    )


# ═══════════════════════════════════════════════════════════════════════════════
# EX7 — Exit depends on market regime
# ═══════════════════════════════════════════════════════════════════════════════

CANONICAL_INTENT_EX7 = (
    "Does market regime (TRENDING/RANGING/TRANSITIONAL) require a different "
    "exit policy? Tests whether exit efficiency differs materially by regime."
)

_OWNERSHIP_EX7 = (
    "EX7 uniquely owns: regime-conditioned exit efficiency analysis.\n"
    "M1 owns regime overall expectancy (not exit efficiency).\n"
    "EX1 owns overall exit efficiency (all regimes combined).\n"
    "EX7 segments EX1's metrics by h4_regime."
)

_VALID_REGIMES = {"TRENDING", "RANGING", "TRANSITIONAL"}


def _regime_fn(rec: dict[str, Any]) -> str:
    return _extract_dimension(rec, "h4_regime", _VALID_REGIMES)


def run_ex7() -> dict[str, Any]:
    """EX7: Regime-specific exit efficiency."""
    question_id = "EX7"
    population = _load_exit_depth_population()
    n = len(population)
    if n < 30:
        return _insufficient(question_id, n)

    segments = _segment_exit_population(population, _regime_fn)

    if not segments:
        return _insufficient(question_id, n)

    total_analysed = sum(s["count"] for s in segments.values())

    overall = {
        "finding": f"Regime-specific exit efficiency for {len(segments)} regimes",
        "question": CANONICAL_INTENT_EX7,
        "ownership_note": _OWNERSHIP_EX7,
        "segmentation": "h4_regime",
        "segments": segments,
        "methodology": "OBSERVATIONAL",
    }

    return _make_report(
        question_id=question_id,
        status="COMPLETE",
        overall=overall,
        confidence=_confidence(total_analysed),
        dataset={"source": "shadow_runtime_v1(ingested)", "sample_size": total_analysed},
        recommendation="FINDING: regime-specific exit metrics",
        assumptions=[
            "H4 regime from decision_snapshot.h4_regime",
        ],
        warnings=[],
    )


# ═══════════════════════════════════════════════════════════════════════════════
# EX8 — Exit depends on pattern type
# ═══════════════════════════════════════════════════════════════════════════════

CANONICAL_INTENT_EX8 = (
    "Do different candlestick patterns require different exit policies "
    "based on their MFE/MAE profiles?"
)

_OWNERSHIP_EX8 = (
    "EX8 uniquely owns: pattern-conditioned exit efficiency analysis.\n"
    "E2 owns pattern overall expectancy (not exit efficiency).\n"
    "EX1 owns overall exit efficiency (all patterns combined).\n"
    "EX8 segments EX1's metrics by pattern identity."
)


def _pattern_fn(rec: dict[str, Any]) -> str:
    val = str(rec.get("pattern", "") or "")
    if val and len(val) >= 2:
        return val
    return ""


def run_ex8() -> dict[str, Any]:
    """EX8: Pattern-specific exit efficiency."""
    question_id = "EX8"
    population = _load_exit_depth_population()
    n = len(population)
    if n < 30:
        return _insufficient(question_id, n)

    segments = _segment_exit_population(population, _pattern_fn)

    if not segments:
        return _insufficient(question_id, n)

    total_analysed = sum(s["count"] for s in segments.values())

    # Top 5 patterns by count
    top_patterns = sorted(
        segments.items(), key=lambda x: x[1]["count"], reverse=True
    )[:5]
    top_analysis = {p[0]: p[1] for p in top_patterns}

    overall = {
        "finding": f"Pattern-specific exit efficiency for {len(segments)} patterns",
        "question": CANONICAL_INTENT_EX8,
        "ownership_note": _OWNERSHIP_EX8,
        "segmentation": "pattern",
        "total_patterns": len(segments),
        "top_patterns_by_count": top_analysis,
        "segments": segments,
        "methodology": "OBSERVATIONAL",
        "note": (
            "Pattern-level exit analysis is naturally data-sparse — many "
            "patterns may have insufficient cell sizes for reliable conclusions."
        ),
    }

    return _make_report(
        question_id=question_id,
        status="COMPLETE",
        overall=overall,
        confidence=_confidence(total_analysed),
        dataset={"source": "shadow_runtime_v1(ingested)", "sample_size": total_analysed},
        recommendation="FINDING: pattern-specific exit metrics",
        assumptions=[
            "Pattern from decision_snapshot.pattern",
        ],
        warnings=[
            "Pattern sparsity: only patterns with >= 10 records are reported separately",
        ],
    )


# ═══════════════════════════════════════════════════════════════════════════════
# EX9 — Exit reduces timeout losses
# ═══════════════════════════════════════════════════════════════════════════════

CANONICAL_INTENT_EX9 = (
    "Does the proposed exit policy reduce the timeout exit rate and convert "
    "timeout losses into captured profits?"
)

_OWNERSHIP_EX9 = (
    "EX9 uniquely owns: timeout exit analysis.\n"
    "EX1 owns overall exit efficiency (all exit reasons combined).\n"
    "EX9 specifically analyses max_bars_timeout exits — their frequency, "
    "MFE at timeout, and whether an earlier exit would have captured more value."
)


def run_ex9() -> dict[str, Any]:
    """EX9: Timeout loss analysis."""
    question_id = "EX9"
    population = _load_exit_depth_population()
    n = len(population)
    if n < 30:
        return _insufficient(question_id, n)

    # Identify timeout exits (max_bars_timeout)
    timeout_trades = [
        r for r in population
        if r.get("exit_reason", "") == "max_bars_timeout"
    ]
    non_timeout_trades = [
        r for r in population
        if r.get("exit_reason", "") != "max_bars_timeout"
    ]

    n_timeout = len(timeout_trades)
    n_non_timeout = len(non_timeout_trades)
    timeout_rate = round(n_timeout / n, 4) if n else 0

    # Timeout MFE analysis — how much was available at timeout?
    timeout_mfes = [r["mfe_r"] for r in timeout_trades]
    timeout_pnls = [r["pnl_r"] for r in timeout_trades if r["pnl_r"] is not None]
    timeout_win_rate = sum(1 for p in timeout_pnls if p > 0) / len(timeout_pnls) if timeout_pnls else 0

    # How many timeout exits had positive MFE but ended negative?
    timeout_reversals = sum(
        1 for r in timeout_trades
        if r["mfe_r"] > 0.05 and r.get("pnl_r") is not None and r["pnl_r"] <= 0
    )
    timeout_reversal_count = timeout_reversals
    timeout_reversal_rate = round(
        timeout_reversals / len(timeout_trades), 4
    ) if timeout_trades else 0

    # Comparison with non-timeout exits
    non_timeout_pnls = [r["pnl_r"] for r in non_timeout_trades if r["pnl_r"] is not None]
    non_timeout_mfes = [r["mfe_r"] for r in non_timeout_trades]
    non_timeout_win_rate = sum(1 for p in non_timeout_pnls if p > 0) / len(non_timeout_pnls) if non_timeout_pnls else 0

    # Bars held at timeout
    timeout_bars = [r.get("bars_held") for r in timeout_trades if r.get("bars_held") is not None]

    status = "COMPLETE" if n_timeout >= _MIN_CELL else "INSUFFICIENT_DATA"
    if status == "COMPLETE":
        recommendation = "FINDING: timeout exit profile computed"
    else:
        recommendation = "WAIT"

    overall = {
        "finding": (
            f"Timeout exits: {n_timeout}/{n} ({timeout_rate:.1%}). "
            f"Timeout win rate: {timeout_win_rate:.1%} "
            f"(non-timeout: {non_timeout_win_rate:.1%}). "
            f"Timeout reversals (pos MFE → neg outcome): {timeout_reversal_count}/{n_timeout} "
            f"({timeout_reversal_rate:.1%})."
        ),
        "question": CANONICAL_INTENT_EX9,
        "ownership_note": _OWNERSHIP_EX9,
        "semantic_honesty": {
            "observational_timeout_analysis": "EXECUTABLE",
            "counterfactual_proposed_exit_policy_reduces_timeouts": "BLOCKED_BY_DATA",
            "explanation": (
                "This analysis characterises observed timeout exits — their frequency, "
                "MFE at exit, and reversal rate. It does NOT simulate what would happen "
                "under a proposed alternative exit policy (e.g., time-based exit at bar N, "
                "or trailing stop to prevent reversals). A proposed-policy counterfactual "
                "requires a dedicated simulator over ordered bar-by-bar path data "
                "(trade_state_progression). Until such a simulator is implemented, "
                "only the observational component is executable."
            ),
        },
        "timeout_analysis": {
            "total_trades": n,
            "timeout_exits": n_timeout,
            "timeout_rate": timeout_rate,
            "non_timeout_exits": n_non_timeout,
            "timeout_exit_metrics": {
                "pnl_stats": _stats(timeout_pnls) if timeout_pnls else {"n": 0},
                "mfe_stats": _stats(timeout_mfes),
                "win_rate": timeout_win_rate,
                "bars_held": _stats([float(b) for b in timeout_bars]) if timeout_bars else {"n": 0},
                "reversals": timeout_reversal_count,
                "reversal_rate": timeout_reversal_rate,
            },
            "non_timeout_exit_metrics": {
                "pnl_stats": _stats(non_timeout_pnls) if non_timeout_pnls else {"n": 0},
                "mfe_stats": _stats(non_timeout_mfes),
                "win_rate": non_timeout_win_rate,
            },
        },
        "methodology": (
            "OBSERVATIONAL: compares timeout exits with non-timeout exits. "
            "Does not simulate what would have happened under a different "
            "time-exit policy — that requires bar-by-bar path data."
        ),
    }

    return _make_report(
        question_id=question_id,
        status=status,
        overall=overall,
        confidence=_confidence(n_timeout),
        dataset={
            "source": "shadow_runtime_v1(ingested)",
            "sample_size": n,
            "timeout_trades": n_timeout,
        },
        recommendation=recommendation,
        assumptions=[
            "max_bars_timeout is the canonical timeout exit_reason",
            "Reversal = positive MFE but negative/zero pnl at exit",
            "Timeouts are identified, not simulated",
        ],
        warnings=[
            "Timeout MFE analysis is descriptive, not counterfactual — "
            "MFE may have occurred at any point during the trade, not necessarily "
            "at the timeout moment",
        ],
    )


# ═══════════════════════════════════════════════════════════════════════════════
# EX10 — Exit survives walk-forward
# ═══════════════════════════════════════════════════════════════════════════════

CANONICAL_INTENT_EX10 = (
    "Does the exit policy improvement hold on out-of-sample data using "
    "time-ordered walk-forward validation?"
)

_OWNERSHIP_EX10 = (
    "EX10 uniquely owns: temporal robustness validation of exit findings.\n"
    "E5 owns overall walk-forward validation for system edge.\n"
    "EX10 is EXIT-SPECIFIC walk-forward: does EX1's observed MFE capture "
    "efficiency persist across independent time periods?\n"
    "This is NOT a full walk-forward optimisation — it checks descriptive "
    "stability of exit metrics over time."
)


def run_ex10() -> dict[str, Any]:
    """EX10: Walk-forward exit validation."""
    question_id = "EX10"
    population = _load_exit_depth_population()
    n = len(population)
    if n < 50:  # need sufficient records for split
        return _insufficient(question_id, n)

    # Sort by canonical entry_time for temporal ordering
    # Uses exit_timestamp from simulated_outcome (epoch seconds)
    # This is a canonical temporal field, NOT shadow_trade_id lexicographic
    sorted_pop = sorted(
        population,
        key=lambda r: r.get("entry_time", 0.0),
    )

    # Verify ordering is deterministic (non-decreasing entry_time)
    # If ordering fails, the split would be meaningless — warn and continue
    timestamps = [r["entry_time"] for r in sorted_pop]
    ordering_ok = all(timestamps[i] <= timestamps[i+1]
                      for i in range(len(timestamps)-1))
    if not ordering_ok:
        logger.warning(
            "[EX10] entry_time ordering is NOT monotonic — "
            "split may not reflect true chronology"
        )

    # Split into early and late halves
    mid = len(sorted_pop) // 2
    early = sorted_pop[:mid]
    late = sorted_pop[-mid:] if mid > 0 else sorted_pop

    def _compute_exit_metrics(subset: list[dict]) -> dict[str, Any]:
        pnls = [r["pnl_r"] for r in subset if r["pnl_r"] is not None]
        mfes = [r["mfe_r"] for r in subset]
        win_rate = sum(1 for p in pnls if p > 0) / len(pnls) if pnls else 0

        # Capture ratio
        pos_mfe = [r for r in subset if r["mfe_r"] > 0.05 and r["pnl_r"] is not None]
        captures = []
        for r in pos_mfe:
            captures.append(r["pnl_r"] / r["mfe_r"])

        return {
            "n": len(subset),
            "pnl": _stats(pnls) if pnls else {"n": 0, "mean": None},
            "mfe": _stats(mfes),
            "win_rate": round(win_rate, 4),
            "mean_capture_ratio": round(
                statistics.mean(captures), 4
            ) if captures else None,
        }

    early_metrics = _compute_exit_metrics(early)
    late_metrics = _compute_exit_metrics(late)

    # Compare: did metrics stay stable?
    early_capture = early_metrics.get("mean_capture_ratio")
    late_capture = late_metrics.get("mean_capture_ratio")
    capture_stable = True
    if early_capture is not None and late_capture is not None:
        if abs(late_capture - early_capture) > 0.15:
            capture_stable = False

    early_mean = early_metrics.get("pnl", {}).get("mean", 0) or 0
    late_mean = late_metrics.get("pnl", {}).get("mean", 0) or 0
    pnl_stable = abs(late_mean - early_mean) < 0.15

    overall = {
        "finding": (
            f"Early period: n={early_metrics['n']}, mean_pnl={early_mean:.4f}R, "
            f"capture_ratio={early_capture or 'N/A'}. "
            f"Late period: n={late_metrics['n']}, mean_pnl={late_mean:.4f}R, "
            f"capture_ratio={late_capture or 'N/A'}. "
            f"Exit metrics {'STABLE' if capture_stable else 'CHANGED'} "
            f"across periods."
        ),
        "question": CANONICAL_INTENT_EX10,
        "ownership_note": _OWNERSHIP_EX10,
        "semantic_honesty": {
            "observed_exit_stability_analysis": "EXECUTABLE",
            "walk_forward_improvement_validation": "BLOCKED_BY_DATA",
            "explanation": (
                "This analysis is a DESCRIPTIVE temporal stability check — it compares "
                "observed exit metrics between early and late chronological periods. "
                "It does NOT define, train, or validate any candidate exit policy "
                "improvement. Genuine walk-forward validation requires: "
                "(1) a proposed alternative exit policy, "
                "(2) ordered bar-by-bar path data to simulate that policy on the early "
                "period, (3) evaluation on the late period. "
                "Until a candidate policy and path-aware simulator are defined, only "
                "the temporal-stability component is executable."
            ),
        },
        "walk_forward_analysis": {
            "method": "chronological_50_50_split_by_entry_time",
            "ordering_field": "entry_time (exit_timestamp from simulated_outcome)",
            "ordering_verified": ordering_ok,
            "early_period": early_metrics,
            "late_period": late_metrics,
            "capture_stable_across_periods": capture_stable,
            "pnl_stable_across_periods": pnl_stable,
        },
        "methodology": (
            "50/50 chronological split by entry_time (exit_timestamp epoch seconds). "
            "This is a descriptive stability check, not a true walk-forward "
            "optimisation. It reports whether exit metrics changed between "
            "the first and second chronological halves of available data."
        ),
        "limitation": (
            "A proper walk-forward test requires (a) defining an exit policy "
            "improvement on the early period, then (b) testing it on the late "
            "period. EX10 only checks whether aggregate exit metrics are stable — "
            "this is a necessary but not sufficient condition for deployability."
        ),
    }

    if not capture_stable or not pnl_stable:
        status = "COMPLETE"
        recommendation = "FINDING: exit metrics changed across periods — further investigation needed"
    else:
        status = "COMPLETE"
        recommendation = "FINDING: exit metrics stable across periods"

    return _make_report(
        question_id=question_id,
        status=status,
        overall=overall,
        confidence=_confidence(n),
        dataset={"source": "shadow_runtime_v1(ingested)", "sample_size": n},
        recommendation=recommendation,
        assumptions=[
            "Records are sorted by entry_time before the early/late split",
            "50/50 split is arbitrary — results may vary with different split points",
            "Descriptive stability ≠ proven out-of-sample exit improvement",
        ],
        warnings=[
            "Walk-forward validation here is DESCRIPTIVE STABILITY CHECK, not "
            "a true optimisation walk-forward with train/test evaluation",
        ],
    )
