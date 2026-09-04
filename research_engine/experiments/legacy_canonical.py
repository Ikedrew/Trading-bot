"""
Legacy Experiments — Canonical Implementation.

All 21 legacy inline experiments (Q2-Q25) migrated to use experiment_base
directly. Each function follows the canonical contract:

    def run_qXX() -> dict:
        1. Load data
        2. Check readiness
        3. Compute metrics
        4. Return build_report(...)

No wrap_report() usage. Every function returns the canonical schema.

This module is PURELY RESEARCH. It does NOT modify trading logic.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from research_engine.data_access.s3_source import get_default_source
from research_engine.experiments.experiment_base import (
    ReadinessStatus,
    build_fingerprint,
    build_report,
    compute_confidence,
    load_shadow_trades,
    extract_r_multiples,
)

# Production-contract dataset names read via the shared S3 data-access layer.
_TRACE_DATASET = "decision_trace"
_SHADOW_DATASET = "research_shadow_trades"
_TRUTH_DATASET = "trade_truth"
_LEDGER_DATASET = "decision_ledger"
_EXEC_DATASET = "execution_context"


# ═══════════════════════════════════════════════════════════════════════════════
# SHARED DATA LOADING
# ═══════════════════════════════════════════════════════════════════════════════


def _load_jsonl(dataset: str) -> list[dict]:
    """Read a production dataset from S3 via the shared data-access layer."""
    return get_default_source().read_dataset(dataset)


def _shadow_outcomes() -> list[dict]:
    from research_engine.data_access.shadow_runtime_ingestion import (
        ingest_completed_shadow_trades,
    )

    outcomes = []
    # Canonical production shadow source (S3 shadow_runtime_v1 event stream,
    # reconstructed into completed shadow outcomes), then the separate
    # live-written research_shadow_trades dataset. Order preserved.
    for rec in [*ingest_completed_shadow_trades(), *_load_jsonl(_SHADOW_DATASET)]:
        o = rec.get("simulated_outcome", {})
        ds = rec.get("decision_snapshot", {})
        ident = rec.get("identity", {})
        if o:
            outcomes.append({
                "r": o.get("pnl_r_multiple", 0),
                "win": o.get("pnl_r_multiple", 0) > 0,
                "score": ds.get("score", 0),
                "pattern": ds.get("pattern", ""),
                "direction": ds.get("direction", ""),
                "symbol": ident.get("symbol", ""),
                "exit_reason": o.get("exit_reason", ""),
                "bars_held": o.get("bars_held", 0),
            })
    return outcomes


def _provenance(qid: str, func: str) -> dict:
    return {
        "experiment_module": "research_engine.experiments.legacy_canonical",
        "registry_id": qid,
        "function": func,
        "pipeline": "Question -> Experiment -> Dataset -> Output -> Knowledge -> Command Centre",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# BATCH 1: SIMPLE EXPERIMENTS
# ═══════════════════════════════════════════════════════════════════════════════


def run_q03() -> dict[str, Any]:
    """Q3/D5: Missed opportunity cost."""
    traces = _load_jsonl(_TRACE_DATASET)
    rejected = [t for t in traces if t.get("action") == "NO_TRADE" and t.get("terminal_stage")]
    n = len(rejected)

    if n == 0:
        return build_report(question_id="Q3", status=ReadinessStatus.INSUFFICIENT_DATA,
            overall={"reason": "No rejected decisions found"}, confidence="INSUFFICIENT_DATA",
            dataset={"source": "decision_trace", "sample_size": 0},
            fingerprint=build_fingerprint(0, len(traces), "decision_trace"),
            recommendation="WAIT", provenance=_provenance("Q3", "run_q03"))

    stages = Counter(t.get("terminal_stage") for t in rejected)
    reasons = Counter(t.get("terminal_reason", "")[:50] for t in rejected)

    return build_report(question_id="Q3", status=ReadinessStatus.COMPLETE,
        overall={"rejection_stages": dict(stages.most_common(10)), "top_reasons": dict(reasons.most_common(10)), "total_rejected": n,
                 "finding": f"{n} trades rejected. Top stage: {stages.most_common(1)[0][0] if stages else 'none'}"},
        confidence=compute_confidence(n), dataset={"source": "decision_trace", "sample_size": n},
        fingerprint=build_fingerprint(n, len(traces) - n, "decision_trace"),
        recommendation="COMPLETE", provenance=_provenance("Q3", "run_q03"))


def run_q06() -> dict[str, Any]:
    """Q6/M1: Regime accuracy."""
    traces = _load_jsonl(_TRACE_DATASET)
    shadows = _shadow_outcomes()
    regimes = Counter(t.get("regime") for t in traces if t.get("regime"))
    n = len(shadows)

    return build_report(question_id="Q6", status=ReadinessStatus.COMPLETE if n > 0 else ReadinessStatus.INSUFFICIENT_DATA,
        overall={"regime_distribution": dict(regimes.most_common()), "total_traces": len(traces),
                 "finding": f"Regime distribution: {dict(regimes.most_common(3))}"},
        confidence=compute_confidence(n), dataset={"source": "decision_trace+shadow", "sample_size": n},
        fingerprint=build_fingerprint(n, 0, "decision_trace+shadow"),
        recommendation="COMPLETE", provenance=_provenance("Q6", "run_q06"))


def run_q07() -> dict[str, Any]:
    """Q7: Session/symbol edge."""
    shadows = _shadow_outcomes()
    by_symbol = defaultdict(list)
    for s in shadows:
        by_symbol[s["symbol"]].append(s)
    n = len(shadows)

    symbol_stats = {}
    for sym, trades in by_symbol.items():
        if len(trades) >= 5:
            wr = sum(1 for t in trades if t["win"]) / len(trades)
            symbol_stats[sym] = {"n": len(trades), "wr": round(wr, 4), "avg_r": round(statistics.mean([t["r"] for t in trades]), 4)}

    return build_report(question_id="Q7", status=ReadinessStatus.COMPLETE if n > 0 else ReadinessStatus.INSUFFICIENT_DATA,
        overall={"symbol_performance": symbol_stats, "finding": f"Per-symbol analysis from {n} trades"},
        confidence=compute_confidence(n), dataset={"source": "shadow_trades", "sample_size": n},
        fingerprint=build_fingerprint(n, 0, "shadow_trades"),
        recommendation="COMPLETE", provenance=_provenance("Q7", "run_q07"))


def run_q08() -> dict[str, Any]:
    """Q8: HTF alignment value."""
    traces = _load_jsonl(_TRACE_DATASET)
    htf_vals = [t.get("htf_alignment", 0.5) for t in traces if t.get("htf_alignment") is not None]
    h4_vals = [t.get("h4_alignment", 0) for t in traces if t.get("h4_alignment") is not None]
    n = len(htf_vals)

    finding = f"HTF alignment mean={statistics.mean(htf_vals):.4f}, H4 mean={statistics.mean(h4_vals):.4f}" if htf_vals else "No data"
    return build_report(question_id="Q8", status=ReadinessStatus.COMPLETE if n > 0 else ReadinessStatus.INSUFFICIENT_DATA,
        overall={"htf_mean": round(statistics.mean(htf_vals), 4) if htf_vals else 0, "h4_mean": round(statistics.mean(h4_vals), 4) if h4_vals else 0, "n_traces": len(traces), "finding": finding},
        confidence=compute_confidence(n), dataset={"source": "decision_trace", "sample_size": n},
        fingerprint=build_fingerprint(n, len(traces) - n, "decision_trace"),
        recommendation="COMPLETE", provenance=_provenance("Q8", "run_q08"))


def run_q09() -> dict[str, Any]:
    """Q9/X3: Spread/fill quality."""
    exec_data = _load_jsonl(_EXEC_DATASET)
    n = len(exec_data)
    if not exec_data:
        return build_report(question_id="Q9", status=ReadinessStatus.BLOCKED,
            overall={"finding": "No execution context data available"}, confidence="INSUFFICIENT_DATA",
            dataset={"source": "execution_context", "sample_size": 0},
            fingerprint=build_fingerprint(0, 0, "execution_context"),
            recommendation="BLOCKED", provenance=_provenance("Q9", "run_q09"))
    return build_report(question_id="Q9", status=ReadinessStatus.COMPLETE,
        overall={"records_available": n, "finding": f"{n} execution context records available"},
        confidence=compute_confidence(n), dataset={"source": "execution_context", "sample_size": n},
        fingerprint=build_fingerprint(n, 0, "execution_context"),
        recommendation="COMPLETE", provenance=_provenance("Q9", "run_q09"))


def run_q10() -> dict[str, Any]:
    """Q10/R1/R2: Guard efficacy."""
    traces = _load_jsonl(_TRACE_DATASET)
    ledger = _load_jsonl(_LEDGER_DATASET)
    risk_blocks = [r for r in ledger if r.get("decision") == "RISK_BLOCK" or "RISK_BLOCK" in str(r.get("decision", ""))]
    n = len(risk_blocks)

    return build_report(question_id="Q10", status=ReadinessStatus.COMPLETE if n > 0 else ReadinessStatus.INSUFFICIENT_DATA,
        overall={"risk_blocks": n, "total_ledger": len(ledger), "finding": f"{n} risk blocks in {len(ledger)} ledger entries"},
        confidence=compute_confidence(n), dataset={"source": "decision_ledger", "sample_size": n},
        fingerprint=build_fingerprint(n, len(ledger) - n, "decision_ledger"),
        recommendation="COMPLETE" if n > 0 else "INSUFFICIENT_DATA", provenance=_provenance("Q10", "run_q10"))


def run_q11() -> dict[str, Any]:
    """Q11/X1: Slippage model."""
    truth = _load_jsonl(_TRUTH_DATASET)
    n = len(truth)
    if not truth:
        return build_report(question_id="Q11", status=ReadinessStatus.BLOCKED,
            overall={"finding": "No trade truth records. Requires live execution."}, confidence="INSUFFICIENT_DATA",
            dataset={"source": "trade_truth", "sample_size": 0},
            fingerprint=build_fingerprint(0, 0, "trade_truth"),
            recommendation="BLOCKED", provenance=_provenance("Q11", "run_q11"))
    return build_report(question_id="Q11", status=ReadinessStatus.COMPLETE,
        overall={"records": n, "finding": f"{n} trade truth records available"},
        confidence=compute_confidence(n), dataset={"source": "trade_truth", "sample_size": n},
        fingerprint=build_fingerprint(n, 0, "trade_truth"),
        recommendation="COMPLETE", provenance=_provenance("Q11", "run_q11"))


def run_q12() -> dict[str, Any]:
    """Q12/X2: Broker reliability."""
    truth = _load_jsonl(_TRUTH_DATASET)
    n = len(truth)
    if not truth:
        return build_report(question_id="Q12", status=ReadinessStatus.BLOCKED,
            overall={"finding": "No execution data available"}, confidence="INSUFFICIENT_DATA",
            dataset={"source": "trade_truth", "sample_size": 0},
            fingerprint=build_fingerprint(0, 0, "trade_truth"),
            recommendation="BLOCKED", provenance=_provenance("Q12", "run_q12"))
    return build_report(question_id="Q12", status=ReadinessStatus.COMPLETE,
        overall={"records": n, "finding": f"{n} execution records"},
        confidence=compute_confidence(n), dataset={"source": "trade_truth", "sample_size": n},
        fingerprint=build_fingerprint(n, 0, "trade_truth"),
        recommendation="COMPLETE", provenance=_provenance("Q12", "run_q12"))


def run_q14() -> dict[str, Any]:
    """Q14: Causal chains."""
    traces = _load_jsonl(_TRACE_DATASET)
    executes = [t for t in traces if t.get("action") == "EXECUTE"]
    n = len(executes)

    return build_report(question_id="Q14", status=ReadinessStatus.COMPLETE if n > 0 else ReadinessStatus.INSUFFICIENT_DATA,
        overall={"executed_decisions": n, "total_traces": len(traces), "finding": f"{n} executed decisions available for causal analysis"},
        confidence=compute_confidence(n), dataset={"source": "decision_trace", "sample_size": n},
        fingerprint=build_fingerprint(n, len(traces) - n, "decision_trace"),
        recommendation="COMPLETE" if n > 0 else "INSUFFICIENT_DATA", provenance=_provenance("Q14", "run_q14"))


def run_q15() -> dict[str, Any]:
    """Q15/L2: Learning velocity."""
    reports_dir = Path("analysis/reports")
    reports = list(reports_dir.glob("*.json")) if reports_dir.exists() else []
    n = len(reports)

    return build_report(question_id="Q15", status=ReadinessStatus.COMPLETE,
        overall={"reports_generated": n, "finding": f"{n} research reports exist. System learning infrastructure active."},
        confidence=compute_confidence(n), dataset={"source": "research_reports", "sample_size": n},
        fingerprint=build_fingerprint(n, 0, "research_reports"),
        recommendation="COMPLETE", provenance=_provenance("Q15", "run_q15"))


def run_q17() -> dict[str, Any]:
    """Q17/L4: Drawdown precursors."""
    truth = _load_jsonl(_TRUTH_DATASET)
    n = len(truth)
    if not truth:
        return build_report(question_id="Q17", status=ReadinessStatus.BLOCKED,
            overall={"finding": "No trade truth. Requires live execution history."}, confidence="INSUFFICIENT_DATA",
            dataset={"source": "trade_truth", "sample_size": 0},
            fingerprint=build_fingerprint(0, 0, "trade_truth"),
            recommendation="BLOCKED", provenance=_provenance("Q17", "run_q17"))
    return build_report(question_id="Q17", status=ReadinessStatus.COMPLETE,
        overall={"records": n, "finding": f"{n} records for drawdown analysis"},
        confidence=compute_confidence(n), dataset={"source": "trade_truth", "sample_size": n},
        fingerprint=build_fingerprint(n, 0, "trade_truth"),
        recommendation="COMPLETE", provenance=_provenance("Q17", "run_q17"))


# ═══════════════════════════════════════════════════════════════════════════════
# BATCH 2: ANALYTICAL EXPERIMENTS
# ═══════════════════════════════════════════════════════════════════════════════


def run_q02() -> dict[str, Any]:
    """Q2/D4: Regime-adaptive threshold."""
    traces = _load_jsonl(_TRACE_DATASET)
    shadows = _shadow_outcomes()
    n = len(shadows)

    if n < 20:
        return build_report(question_id="Q2", status=ReadinessStatus.INSUFFICIENT_DATA,
            overall={"finding": "Insufficient shadow data"}, confidence="INSUFFICIENT_DATA",
            dataset={"source": "decision_trace+shadow", "sample_size": n},
            fingerprint=build_fingerprint(n, 0), recommendation="WAIT", provenance=_provenance("Q2", "run_q02"))

    scored = [t for t in traces if t.get("score_neutral", 0) > 0 and t.get("regime")]
    results = {}
    for threshold in [0.30, 0.35, 0.40, 0.45, 0.50]:
        above = [s for s in shadows if s["score"] >= threshold]
        if above:
            wr = sum(1 for s in above if s["win"]) / len(above)
            avg_r = statistics.mean([s["r"] for s in above])
            results[str(threshold)] = {"n": len(above), "wr": round(wr, 4), "avg_r": round(avg_r, 4)}

    return build_report(question_id="Q2", status=ReadinessStatus.COMPLETE,
        overall={"threshold_analysis": results, "regimes_in_traces": dict(Counter(t.get("regime") for t in scored)),
                 "finding": f"Threshold analysis across {n} shadow trades"},
        confidence=compute_confidence(n), dataset={"source": "decision_trace+shadow_trades", "sample_size": n},
        fingerprint=build_fingerprint(n, 0, "decision_trace+shadow_trades"),
        recommendation="COMPLETE", provenance=_provenance("Q2", "run_q02"))


def run_q04() -> dict[str, Any]:
    """Q4/D2: Confidence calibration."""
    traces = _load_jsonl(_TRACE_DATASET)
    shadows = _shadow_outcomes()
    n = len(shadows)
    with_p = [t for t in traces if t.get("p_success") is not None]

    if not with_p or n < 20:
        return build_report(question_id="Q4", status=ReadinessStatus.INSUFFICIENT_DATA,
            overall={"finding": "Insufficient data for calibration analysis"}, confidence="INSUFFICIENT_DATA",
            dataset={"source": "decision_trace+shadow", "sample_size": n},
            fingerprint=build_fingerprint(n, 0), recommendation="WAIT", provenance=_provenance("Q4", "run_q04"))

    predicted = statistics.mean([t["p_success"] for t in with_p])
    actual = sum(1 for s in shadows if s["win"]) / n
    error = abs(actual - predicted)
    rec = "PROMOTE_CALIBRATION" if error > 0.10 else "KEEP_CURRENT"

    return build_report(question_id="Q4", status=ReadinessStatus.COMPLETE,
        overall={"predicted_p": round(predicted, 4), "actual_wr": round(actual, 4), "calibration_error": round(error, 4), "n_traces": len(with_p),
                 "finding": f"Predicted p={predicted:.3f} vs actual WR={actual:.3f}. Error={error:.3f}"},
        confidence=compute_confidence(n, error > 0.10), dataset={"source": "decision_trace+shadow_trades", "sample_size": n},
        fingerprint=build_fingerprint(n, 0, "decision_trace+shadow_trades"),
        recommendation=rec, provenance=_provenance("Q4", "run_q04"))


def run_q05() -> dict[str, Any]:
    """Q5/E2/L1: Pattern degradation/expectancy."""
    shadows = _shadow_outcomes()
    n = len(shadows)
    by_pattern = defaultdict(list)
    for s in shadows:
        if s["pattern"]:
            by_pattern[s["pattern"]].append(s)

    degradation = {}
    for pat, trades in by_pattern.items():
        if len(trades) >= 5:
            wr = sum(1 for t in trades if t["win"]) / len(trades)
            avg_r = statistics.mean([t["r"] for t in trades])
            degradation[pat] = {"n": len(trades), "wr": round(wr, 4), "avg_r": round(avg_r, 4)}

    return build_report(question_id="Q5", status=ReadinessStatus.COMPLETE if n > 0 else ReadinessStatus.INSUFFICIENT_DATA,
        overall={"pattern_performance": degradation, "finding": f"{len(degradation)} patterns analysed from {n} trades"},
        confidence=compute_confidence(n), dataset={"source": "shadow_trades", "sample_size": n},
        fingerprint=build_fingerprint(n, 0, "shadow_trades"),
        recommendation="COMPLETE", provenance=_provenance("Q5", "run_q05"))


def run_q13() -> dict[str, Any]:
    """Q13: Optimal duration."""
    shadows = _shadow_outcomes()
    durations = [s["bars_held"] for s in shadows if s["bars_held"] > 0]
    n = len(durations)

    if n < 10:
        return build_report(question_id="Q13", status=ReadinessStatus.INSUFFICIENT_DATA,
            overall={"finding": "Insufficient duration data"}, confidence="INSUFFICIENT_DATA",
            dataset={"source": "shadow_trades", "sample_size": n},
            fingerprint=build_fingerprint(n, len(shadows) - n, "shadow_trades"),
            recommendation="WAIT", provenance=_provenance("Q13", "run_q13"))

    winners = [s["bars_held"] for s in shadows if s["win"] and s["bars_held"] > 0]
    losers = [s["bars_held"] for s in shadows if not s["win"] and s["bars_held"] > 0]
    finding = f"Avg duration: {statistics.mean(durations):.0f} bars. Winners: {statistics.mean(winners):.0f}, Losers: {statistics.mean(losers):.0f}" if winners and losers else "Partial data"

    return build_report(question_id="Q13", status=ReadinessStatus.COMPLETE,
        overall={"avg_bars_all": round(statistics.mean(durations), 1), "avg_bars_winners": round(statistics.mean(winners), 1) if winners else 0,
                 "avg_bars_losers": round(statistics.mean(losers), 1) if losers else 0, "median_bars": round(statistics.median(durations), 1), "finding": finding},
        confidence=compute_confidence(n), dataset={"source": "shadow_trades", "sample_size": n},
        fingerprint=build_fingerprint(n, len(shadows) - n, "shadow_trades"),
        recommendation="COMPLETE", provenance=_provenance("Q13", "run_q13"))


def run_q18() -> dict[str, Any]:
    """Q18: Symbol universe."""
    shadows = _shadow_outcomes()
    n = len(shadows)
    by_sym = defaultdict(list)
    for s in shadows:
        by_sym[s["symbol"]].append(s["r"])

    symbol_ev = {}
    for sym, rs in by_sym.items():
        if len(rs) >= 5:
            symbol_ev[sym] = {"n": len(rs), "avg_r": round(statistics.mean(rs), 4), "wr": round(sum(1 for r in rs if r > 0) / len(rs), 4)}

    return build_report(question_id="Q18", status=ReadinessStatus.COMPLETE if n > 0 else ReadinessStatus.INSUFFICIENT_DATA,
        overall={"symbol_performance": symbol_ev, "finding": f"{len(symbol_ev)} symbols with sufficient data"},
        confidence=compute_confidence(n), dataset={"source": "shadow_trades", "sample_size": n},
        fingerprint=build_fingerprint(n, 0, "shadow_trades"),
        recommendation="COMPLETE", provenance=_provenance("Q18", "run_q18"))


def run_q21() -> dict[str, Any]:
    """Q21/D3: Calibration impact on EV."""
    shadows = _shadow_outcomes()
    n = len(shadows)
    if n < 20:
        return build_report(question_id="Q21", status=ReadinessStatus.INSUFFICIENT_DATA,
            overall={"finding": "Insufficient data for calibration impact analysis"}, confidence="INSUFFICIENT_DATA",
            dataset={"source": "shadow_trades", "sample_size": n},
            fingerprint=build_fingerprint(n, 0), recommendation="WAIT", provenance=_provenance("Q21", "run_q21"))

    above_threshold = sum(1 for s in shadows if s["score"] >= 0.45)
    current_wr = sum(1 for s in shadows if s["win"]) / n

    return build_report(question_id="Q21", status=ReadinessStatus.COMPLETE,
        overall={"trades_above_045": above_threshold, "pct_above": round(above_threshold / max(n, 1), 4), "current_wr": round(current_wr, 4),
                 "finding": f"{above_threshold}/{n} trades would pass score>=0.45. Overall WR={current_wr:.1%}"},
        confidence=compute_confidence(n), dataset={"source": "shadow_trades", "sample_size": n},
        fingerprint=build_fingerprint(n, 0, "shadow_trades"),
        recommendation="COMPLETE", provenance=_provenance("Q21", "run_q21"))


def run_q22() -> dict[str, Any]:
    """Q22: EV threshold optimisation."""
    shadows = _shadow_outcomes()
    n = len(shadows)
    if n < 20:
        return build_report(question_id="Q22", status=ReadinessStatus.INSUFFICIENT_DATA,
            overall={"finding": "Insufficient data"}, confidence="INSUFFICIENT_DATA",
            dataset={"source": "shadow_trades", "sample_size": n},
            fingerprint=build_fingerprint(n, 0), recommendation="WAIT", provenance=_provenance("Q22", "run_q22"))

    results = {}
    for threshold in [0.35, 0.40, 0.45, 0.50, 0.55]:
        above = [s for s in shadows if s["score"] >= threshold]
        if above:
            wr = sum(1 for s in above if s["win"]) / len(above)
            total_r = sum(s["r"] for s in above)
            results[str(threshold)] = {"n": len(above), "wr": round(wr, 4), "total_r": round(total_r, 2)}

    return build_report(question_id="Q22", status=ReadinessStatus.COMPLETE,
        overall={"threshold_analysis": results, "finding": f"Threshold optimisation across {n} shadow trades"},
        confidence=compute_confidence(n), dataset={"source": "shadow_trades", "sample_size": n},
        fingerprint=build_fingerprint(n, 0, "shadow_trades"),
        recommendation="COMPLETE", provenance=_provenance("Q22", "run_q22"))


def run_q23() -> dict[str, Any]:
    """Q23: Regime edge."""
    traces = _load_jsonl(_TRACE_DATASET)
    post = [t for t in traces if t.get("regime_source") == "H4_MARKET_CONTEXT"]
    regimes = Counter(t.get("regime") for t in post)
    n = len(post)

    return build_report(question_id="Q23", status=ReadinessStatus.COMPLETE if n > 0 else ReadinessStatus.INSUFFICIENT_DATA,
        overall={"regime_distribution": dict(regimes.most_common()), "total_post_migration": n,
                 "finding": f"Post-migration regime distribution: {dict(regimes.most_common())}"},
        confidence=compute_confidence(n), dataset={"source": "decision_trace", "sample_size": n},
        fingerprint=build_fingerprint(n, len(traces) - n, "decision_trace"),
        recommendation="COMPLETE", provenance=_provenance("Q23", "run_q23"))


def run_q24() -> dict[str, Any]:
    """Q24/E3/S1: Strategy edge."""
    traces = _load_jsonl(_TRACE_DATASET)
    strategies = Counter(t.get("selected_strategy") or "None" for t in traces if t.get("score_neutral", 0) > 0)
    n = sum(strategies.values())

    return build_report(question_id="Q24", status=ReadinessStatus.COMPLETE if n > 0 else ReadinessStatus.INSUFFICIENT_DATA,
        overall={"strategy_distribution": dict(strategies.most_common()), "finding": f"Strategy activation: {dict(strategies.most_common(3))}"},
        confidence=compute_confidence(n), dataset={"source": "decision_trace", "sample_size": n},
        fingerprint=build_fingerprint(n, len(traces) - n, "decision_trace"),
        recommendation="COMPLETE", provenance=_provenance("Q24", "run_q24"))


def run_q25() -> dict[str, Any]:
    """Q25: Symbol/session edge."""
    shadows = _shadow_outcomes()
    n = len(shadows)
    by_sym = defaultdict(list)
    for s in shadows:
        by_sym[s["symbol"]].append(s)

    results = {}
    for sym, trades in by_sym.items():
        if len(trades) >= 5:
            wr = sum(1 for t in trades if t["win"]) / len(trades)
            results[sym] = {"n": len(trades), "wr": round(wr, 4), "avg_r": round(statistics.mean([t["r"] for t in trades]), 4), "total_r": round(sum(t["r"] for t in trades), 2)}

    return build_report(question_id="Q25", status=ReadinessStatus.COMPLETE if n > 0 else ReadinessStatus.INSUFFICIENT_DATA,
        overall={"symbol_edge": results, "finding": f"{len(results)} symbols with edge data"},
        confidence=compute_confidence(n), dataset={"source": "shadow_trades", "sample_size": n},
        fingerprint=build_fingerprint(n, 0, "shadow_trades"),
        recommendation="COMPLETE", provenance=_provenance("Q25", "run_q25"))
