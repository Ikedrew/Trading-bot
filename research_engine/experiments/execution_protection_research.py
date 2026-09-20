"""
Execution + Protection Research — X1/X2/X3/X5/EXEC-1/PROT-1 (Wave 4)

Canonical execution-side research over four V1 datasets:

    execution_attempts_v1   (one record per broker call -- writer exists but
                             currently has NO production caller; see LIMITS)
    execution_results_v1    (one record per successful fill, written
                             post-fill by the live scanner, with protection
                             verification fields embedded)
    execution_context_v1    (PRE-EXECUTION snapshot per decision cycle:
                             session, spread, latency, feed/risk state)
    protection_audit_v1     (post-fill SL/TP verification per position)

EVIDENCE OWNERSHIP (fixed in this wave):

    X1 slippage model    -> execution_results measured slippage joined to
                            execution_context session by correlation_id
    X2 broker failures   -> execution_results retcode/result_ok;
                            execution_attempts when populated
    X3 session quality   -> execution_context joined to results slippage
    X5 execution leakage -> decision_trace EV joined to trade_truth realised
                            R by canonical_opportunity_id (distinct from X4
                            shadow-vs-live)

HONEST LIMITS (documented in affected reports):

    1. Broker rejections/failed attempts are currently emitted only to the
       runtime log (execution_trace.log_order_failed) -- NOT persisted to
       any canonical dataset. The execution_attempts_v1 writer exists but
       has no production caller. Failure-rate analysis reports its
       denominator honestly and never fabricates rejection counts.

    2. A failed execution does NOT reveal what the trade would have earned.
       No counterfactual PnL is ever claimed for non-executed opportunities.

    3. All condition fields used from execution_context are PRE-EXECUTION.
       Outcome R is joined only for realised-outcome association and never
       becomes a pre-execution feature.

TEMPORAL CLASSES:
    execution_context fields           -> PRE-EXECUTION
    execution_attempts request fields  -> EXECUTION-TIME
    execution_results slippage/retcode -> EXECUTION-TIME
    protection_audit verification      -> POST-FILL
    trade_truth r_multiple_realised    -> POST-OUTCOME (research only)
"""
from __future__ import annotations

import logging
import math
import statistics
from collections import defaultdict
from typing import Any

from research_engine.control_plane.evidence_provenance import (
    build_evidence_provenance,
    select_current_evidence,
)
from research_engine.control_plane.evidence_resolver import (
    join_execution_context_results,
    normalise_evidence_record,
)

logger = logging.getLogger(__name__)

_MIN_SAMPLE = 30   # overall status gate (engine convention)
_MIN_CELL = 10     # subgroup/cell gate
_MEASURED = "measured_execution_slippage"


def _load_results() -> list[dict[str, Any]]:
    from research_engine.data_access.loaders import load_execution_results
    return load_execution_results()


def _load_context() -> list[dict[str, Any]]:
    from research_engine.data_access.loaders import load_execution_context
    return load_execution_context()


def _load_attempts() -> list[dict[str, Any]]:
    from research_engine.data_access.loaders import load_execution_attempts
    return load_execution_attempts()


def _load_protection() -> list[dict[str, Any]]:
    from research_engine.data_access.loaders import load_protection_audit
    return load_protection_audit()


def _load_trade_truth() -> list[dict[str, Any]]:
    from research_engine.data_access.loaders import load_trade_truth
    return load_trade_truth()


def _load_decision_trace() -> list[dict[str, Any]]:
    from research_engine.data_access.loaders import load_decision_trace
    return load_decision_trace()


def _confidence(n: int) -> str:
    if n >= 200:
        return "HIGH"
    if n >= _MIN_SAMPLE:
        return "MEDIUM"
    if n > 0:
        return "LOW"
    return "INSUFFICIENT_DATA"


def _report(
    question_id: str,
    status: str,
    overall: dict[str, Any],
    confidence: str,
    dataset: dict[str, Any],
    recommendation: str,
    evidence_provenance: dict[str, Any] | None = None,
    assumptions: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    from research_engine.experiments.experiment_base import (
        build_report, build_fingerprint, build_fingerprint_from_provenance,
    )
    sample = dataset.get("sample_size", 0)
    return build_report(
        question_id=question_id,
        status=status,
        overall=overall,
        confidence=confidence,
        dataset=dataset,
        fingerprint=(
            build_fingerprint_from_provenance(evidence_provenance)
            if evidence_provenance is not None
            else build_fingerprint(sample, 0, "execution_results")
        ),
        recommendation=recommendation,
        assumptions=assumptions or [],
        warnings=warnings or [],
        provenance={
            "experiment_module":
                "research_engine.experiments.execution_protection_research",
            "registry_id": question_id,
            "pipeline": "Question -> Experiment -> Dataset -> Output -> "
                        "Knowledge -> Command Centre",
        },
    )


def _extract_result(rec: dict[str, Any]) -> dict[str, Any] | None:
    """Flatten one execution_results_v1 record. EXECUTION-TIME facts."""
    normalised = normalise_evidence_record(rec, "execution_results_v1")
    slippage = normalised.get("slippage")
    if slippage is not None and normalised.get("slippage_semantic") != _MEASURED:
        slippage = None
    return {
        "correlation_id": str(normalised.get("correlation_id", "") or ""),
        "decision_id": str(normalised.get("decision_id", "") or ""),
        "canonical_opportunity_id": str(
            normalised.get("canonical_opportunity_id", "") or ""),
        "entity_id": str(normalised.get("entity_id", "") or ""),
        "account_id": str(normalised.get("account_id", "") or ""),
        "symbol": str(normalised.get("symbol", "") or ""),
        "result_ok": bool(normalised.get("result_ok", False)),
        "retcode": normalised.get("retcode"),
        "comment": str(normalised.get("comment", "") or ""),
        "fill_price": normalised.get("fill_price"),
        "slippage": (
            float(slippage)
            if slippage is not None and math.isfinite(float(slippage))
            else None
        ),
        "slippage_semantic": str(normalised.get("slippage_semantic", "unknown") or "unknown"),
        "slippage_provenance": normalised.get("slippage_provenance", "unknown"),
        "derived_compatibility_slippage": normalised.get("derived_compatibility_slippage"),
        "protection_status": str(normalised.get("protection_status", "") or ""),
        "protection_failure_reason": str(
            normalised.get("protection_failure_reason", "") or ""),
        "requested_sl": normalised.get("requested_sl"),
        "requested_tp": normalised.get("requested_tp"),
        "broker_confirmed_sl": normalised.get("broker_confirmed_sl"),
        "broker_confirmed_tp": normalised.get("broker_confirmed_tp"),
    }


def _extract_context(rec: dict[str, Any]) -> dict[str, Any] | None:
    """Flatten one execution_context_v1 record. ALL fields PRE-EXECUTION."""
    corr = str(rec.get("correlation_id", "") or "")
    if not corr:
        return None
    ma = rec.get("market_access") or {}
    infra = rec.get("infrastructure") or {}
    risk = rec.get("risk_environment") or {}
    return {
        "correlation_id": corr,
        "canonical_opportunity_id": str(
            rec.get("canonical_opportunity_id", "") or ""),
        "symbol": str(rec.get("symbol", "") or ""),
        "session_state": str(ma.get("session_state", "") or ""),
        "spread": ma.get("spread"),
        "spread_atr_ratio": ma.get("spread_atr_ratio"),
        "bid": ma.get("bid"),
        "ask": ma.get("ask"),
        "latency_ms": infra.get("latency_ms"),
        "feed_state": str(infra.get("feed_state", "") or ""),
        "tick_age_ms": infra.get("tick_age_ms"),
        "drawdown_pct": risk.get("drawdown_pct"),
        "open_positions": risk.get("open_positions"),
    }


def _extract_attempt(rec: dict[str, Any]) -> dict[str, Any] | None:
    """Flatten one execution_attempts_v1 record. EXECUTION-TIME facts."""
    aid = str(rec.get("attempt_id", "") or "")
    if not aid:
        return None
    br = rec.get("broker_result") or {}
    return {
        "attempt_id": aid,
        "correlation_id": str(rec.get("correlation_id", "") or ""),
        "decision_id": str(rec.get("decision_id", "") or ""),
        "canonical_opportunity_id": str(
            rec.get("canonical_opportunity_id", "") or ""),
        "trade_id": str(rec.get("trade_id", "") or ""),
        "symbol": str(rec.get("symbol", "") or ""),
        "action_type": str(rec.get("action_type", "") or ""),
        "attempt_number": rec.get("attempt_number"),
        "retry_reason": rec.get("retry_reason"),
        "broker_ok": bool(br.get("ok", False)),
        "retcode": br.get("retcode"),
        "comment": str(br.get("comment", "") or ""),
        "spread_at_attempt": rec.get("spread_at_attempt"),
        "slippage": rec.get("slippage"),
    }


def _extract_protection(rec: dict[str, Any]) -> dict[str, Any] | None:
    """Flatten one protection_audit_v1 record. POST-FILL verification."""
    corr = str(rec.get("correlation_id", "") or "")
    if not corr:
        return None
    return {
        "correlation_id": corr,
        "symbol": str(rec.get("symbol", "") or ""),
        "position_ticket": rec.get("position_ticket"),
        "requested_sl": rec.get("requested_sl"),
        "requested_tp": rec.get("requested_tp"),
        "broker_confirmed_sl": rec.get("broker_confirmed_sl"),
        "broker_confirmed_tp": rec.get("broker_confirmed_tp"),
        "protection_status": str(rec.get("protection_status", "") or ""),
        "protection_failure_reason": str(
            rec.get("protection_failure_reason", "") or ""),
        "verification_latency_ms": rec.get("verification_latency_ms"),
        "attempts": rec.get("attempts"),
        "correction_attempted": bool(rec.get("correction_attempted", False)),
        "correction_success": bool(rec.get("correction_success", False)),
    }


def _group_stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "mean": None, "median": None,
                "p75": None, "min": None, "max": None}
    vals = sorted(v for v in values if math.isfinite(v))
    n = len(vals)
    if n == 0:
        return {"n": 0, "mean": None, "median": None,
                "p75": None, "min": None, "max": None}
    return {
        "n": n,
        "mean": round(statistics.mean(vals), 6),
        "median": round(statistics.median(vals), 6),
        "p75": round(vals[min(int(n * 0.75), n - 1)], 6),
        "min": round(vals[0], 6),
        "max": round(vals[-1], 6),
    }


def join_by_correlation_id(
    records: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Deterministic join index on correlation_id."""
    idx: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in records:
        key = rec.get("correlation_id", "")
        if key:
            idx[key].append(rec)
    return idx


def join_context_to_results(
    results: list[dict[str, Any]],
    contexts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Join one pre-execution context to account-grained execution results."""
    joined = join_execution_context_results(results, contexts)
    matched: list[dict[str, Any]] = []
    for pair in joined["matched"]:
        result = _extract_result(pair["result"])
        context = _extract_context(pair["context"])
        if result is not None and context is not None:
            matched.append({"result": result, "context": context})
    return {**joined, "matched": matched}

# ============================================================
# X1 — Slippage model
# ============================================================

_REJECTION_EVIDENCE_LIMITATION = (
    "Broker rejections/failed attempts are currently emitted only to the "
    "runtime log (execution_trace.log_order_failed) — NOT persisted to any "
    "canonical dataset. The execution_attempts_v1 writer exists but has no "
    "production caller. Failure-rate analysis reports its denominator "
    "honestly and never fabricates rejection counts."
)


def run_x1() -> dict[str, Any]:
    result_selection = select_current_evidence(
        "execution_results_v1", _load_results(),
    )
    context_selection = select_current_evidence(
        "execution_context", _load_context(),
    )
    res = result_selection.records_for_analysis()
    ctx = context_selection.records_for_analysis()
    evidence_provenance = build_evidence_provenance(
        result_selection, context_selection,
    )
    if not res:
        return _report(
            question_id="X1", status="INSUFFICIENT_DATA",
            overall={"n": 0, "detail": "No execution_results_v1 records."},
            confidence="INSUFFICIENT_DATA",
            dataset={
                "sample_size": 0,
                "source": "execution_results_v1 + execution_context_v1",
            },
            recommendation="INSUFFICIENT_DATA",
            evidence_provenance=evidence_provenance,
            assumptions=[
                "X1 requires execution_results_v1 measured slippage joined "
                "to execution_context_v1 by correlation_id.",
            ],
        )
    n = len(res)

    # Identify measured-slippage records (flagged by the producer)
    measured = [r for r in res
                if r.get("slippage_semantic") == _MEASURED
                and r.get("slippage") is not None
                and math.isfinite(float(r["slippage"]))]
    m = len(measured)

    overall: dict[str, Any] = {
        "n": n,
        "measured_slippage_count": m,
        "slippage_semantic": _MEASURED,
        "evidence_limitation": (
            "Slippage reported only when fill price differs measurably from "
            "requested price. Orders filled at requested price produce no "
            "slippage record."),
    }

    if not ctx or m < _MIN_SAMPLE:
        conf = "LOW" if m else "INSUFFICIENT_DATA"
        if m:
            overall["slippage_basic"] = _group_stats(
                [r["slippage"] for r in measured])
        return _report(
            question_id="X1",
            status="INSUFFICIENT_DATA" if m < _MIN_SAMPLE else "COMPLETE",
            overall=overall,
            confidence=conf,
            dataset={"sample_size": n,
                     "measured_slippage": m,
                     "source": "execution_results_v1 (+ execution_context_v1)"},
            recommendation="INSUFFICIENT_DATA",
            evidence_provenance=evidence_provenance,
            assumptions=[
                "QUESTION -> PRIMARY EVIDENCE: X1 -> execution_results "
                "measured slippage; execution_context session joined by "
                "correlation_id (deterministic, no proximity fallback).",
            ],
            warnings=[
                "Requires >=30 measured-slippage records with matched "
                "execution_context for session analysis.",
            ],
        )

    # Join context to results for session-level analysis
    joined = join_context_to_results(res, ctx)
    matched = joined["matched"]
    measured_with_context = [
        pair for pair in matched
        if pair["result"].get("slippage_semantic") == _MEASURED
        and pair["result"].get("slippage") is not None
        and math.isfinite(float(pair["result"]["slippage"]))
    ]
    overall["match_stats"] = {
        "matched": len(matched),
        "results_without_context": joined["results_without_context"],
        "contexts_without_results": joined["contexts_without_results"],
        "ambiguous": joined["ambiguous"],
    }

    # Per-session slippage
    if len(measured_with_context) < _MIN_SAMPLE:
        return _report(
            question_id="X1", status="INSUFFICIENT_DATA",
            overall=overall, confidence="LOW" if measured_with_context else "INSUFFICIENT_DATA",
            dataset={"sample_size": len(measured_with_context),
                     "measured_slippage": m,
                     "source": "execution_results_v1 + execution_context_v1"},
            recommendation="INSUFFICIENT_DATA",
            evidence_provenance=evidence_provenance,
            assumptions=["Measured execution slippage joined to context by correlation_id."],
            warnings=["Requires >=30 measured-slippage records with matched execution_context."],
        )

    if len(measured_with_context) >= _MIN_SAMPLE:
        by_session: dict[str, list[float]] = defaultdict(list)
        for pair in measured_with_context:
            slp = pair["result"].get("slippage")
            if slp is not None and math.isfinite(float(slp)):
                sess = pair["context"].get("session_state", "UNKNOWN") or "UNKNOWN"
                by_session[sess].append(float(slp))
        overall["per_session_slippage"] = {
            sess: _group_stats(vals)
            for sess, vals in sorted(by_session.items())
            if len(vals) >= _MIN_CELL
        }

    return _report(
        question_id="X1", status="COMPLETE",
        overall=overall,
        confidence=_confidence(n),
        dataset={"sample_size": n,
                 "measured_slippage": m,
                 "source": "execution_results_v1 + execution_context_v1"},
        recommendation="SLIPPAGE_PROFILE_REPORTED",
        evidence_provenance=evidence_provenance,
        assumptions=[
            "QUESTION -> PRIMARY EVIDENCE: X1 -> execution_results "
            "measured slippage; execution_context session joined by "
            "correlation_id (deterministic, no proximity fallback).",
        ],
        warnings=[
            "Slippage model is OBSERVATIONAL and execution-specific — "
            "it describes broker behaviour, not strategy profitability.",
            "Session cells with N < %d excluded." % _MIN_CELL,
        ],
    )


# ============================================================
# X2 — Broker failure patterns
# ============================================================

def run_x2() -> dict[str, Any]:
    result_selection = select_current_evidence(
        "execution_results_v1", _load_results(),
    )
    attempt_selection = select_current_evidence(
        "execution_attempts_v1", _load_attempts(),
    )
    res = result_selection.records_for_analysis()
    att = attempt_selection.records_for_analysis()
    evidence_provenance = build_evidence_provenance(
        result_selection, attempt_selection,
    )
    n = len(res)

    overall: dict[str, Any] = {
        "results_total": n,
        "attempts_total": len(att),
        "evidence_limitation": _REJECTION_EVIDENCE_LIMITATION,
    }

    if n < _MIN_SAMPLE and len(att) < _MIN_SAMPLE:
        conf = "INSUFFICIENT_DATA" if (n + len(att)) < 10 else "LOW"
        return _report(
            question_id="X2",
            status="INSUFFICIENT_DATA",
            overall=overall,
            confidence=conf,
            dataset={"sample_size": n + len(att),
                     "source": "execution_results_v1 (+ execution_attempts_v1 "
                               "when populated)"},
            recommendation="INSUFFICIENT_DATA",
            evidence_provenance=evidence_provenance,
            assumptions=[
                "Requires >=30 persisted execution records.",
            ],
            warnings=[_REJECTION_EVIDENCE_LIMITATION],
        )

    # Retcode / result_ok distribution from persisted executions
    retcode_counts: dict[str, int] = defaultdict(int)
    ok_count = 0
    for r in res:
        retcode_counts[str(r["retcode"])] += 1
        if r["result_ok"]:
            ok_count += 1
    overall["result_ok_rate"] = round(ok_count / n, 4) if n else None
    overall["retcode_distribution"] = dict(sorted(retcode_counts.items()))

    # Per-symbol failure mix
    by_symbol: dict[str, dict[str, int]] = defaultdict(
        lambda: {"n": 0, "not_ok": 0})
    for r in res:
        by_symbol[r["symbol"]]["n"] += 1
        if not r["result_ok"]:
            by_symbol[r["symbol"]]["not_ok"] += 1
    overall["per_symbol"] = {
        sym: {
            "n": s["n"],
            "not_ok_rate": round(s["not_ok"] / s["n"], 4),
        }
        for sym, s in sorted(by_symbol.items()) if s["n"] >= _MIN_CELL
    }

    # Attempts layer (when populated): retries + per-attempt failures
    if att:
        n_att = len(att)
        retries = [a for a in att
                   if (a["attempt_number"] or 1) > 1]
        unique_trades = {a["trade_id"] or a["correlation_id"] or a["attempt_id"]
                         for a in att}
        broker_fail = [a for a in att
                       if not (a.get("broker_result") or {}).get("ok", False)]
        retry_reasons: dict[str, int] = defaultdict(int)
        for a in retries:
            retry_reasons[str(a["retry_reason"])] += 1
        overall["attempts_layer"] = {
            "attempts_total": n_att,
            "unique_trades": len(unique_trades),
            "retry_rate": round(len(retries) / n_att, 4) if n_att else None,
            "attempt_failure_rate": round(
                len(broker_fail) / n_att, 4) if n_att else None,
            "retry_reason_distribution": dict(sorted(retry_reasons.items())),
        }

    return _report(
        question_id="X2",
        status="COMPLETE",
        overall=overall,
        confidence=_confidence(n + len(att)),
        dataset={"sample_size": n + len(att),
                 "source": "execution_results_v1 (+ execution_attempts_v1 "
                           "when populated)"},
        recommendation="BROKER_EXECUTION_PROFILE_REPORTED",
        evidence_provenance=evidence_provenance,
        assumptions=[
            "QUESTION -> PRIMARY EVIDENCE: X2 -> execution_results "
            "retcode/result_ok; execution_attempts secondary (per-attempt "
            "retcodes/retries) — no proximity joins.",
        ],
        warnings=[
            _REJECTION_EVIDENCE_LIMITATION,
            "Execution reliability is NOT strategy profitability — these "
            "profiles describe broker behaviour, not edge.",
            "Per-symbol cells with N < "
            f"{_MIN_CELL} are excluded from the per-symbol view.",
        ],
    )


# ============================================================
# X3 — Session execution quality
# ============================================================

def run_x3() -> dict[str, Any]:
    res = _load_results()
    ctx = _load_context()
    if not ctx:
        return _report(
            question_id="X3", status="INSUFFICIENT_DATA",
            overall={"n": 0, "detail": "No execution_context records found."},
            confidence="INSUFFICIENT_DATA",
            dataset={"sample_size": 0, "source": "execution_context_v1"},
            recommendation="INSUFFICIENT_DATA",
            assumptions=["ExecutionContext is PRE-EXECUTION."],
        )

    joined = join_context_to_results(res, ctx)
    matched = [
        pair for pair in joined["matched"]
        if pair["result"].get("slippage_semantic") == _MEASURED
        and pair["result"].get("slippage") is not None
        and math.isfinite(float(pair["result"]["slippage"]))
    ]
    n = len(matched)
    if n < _MIN_SAMPLE:
        return _report(
            question_id="X3", status="INSUFFICIENT_DATA",
            overall={"n": n, "matched": n,
                     "results_without_context": joined["results_without_context"],
                     "contexts_without_results": joined["contexts_without_results"],
                     "ambiguous": joined["ambiguous"]},
            confidence="LOW" if n else "INSUFFICIENT_DATA",
            dataset={"sample_size": n,
                     "source": "execution_context_v1 + execution_results_v1"},
            recommendation="INSUFFICIENT_DATA",
            assumptions=["Matched on correlation_id (deterministic)."],
        )

    by_session: dict[str, list[float]] = defaultdict(list)
    for pair in matched:
        sess = pair["context"].get("session_state", "UNKNOWN") or "UNKNOWN"
        slp = pair["result"].get("slippage")
        if slp is not None and math.isfinite(float(slp)):
            by_session[sess].append(float(slp))

    overall: dict[str, Any] = {
        "n": n, "matched": n,
        "results_without_context": joined["results_without_context"],
        "contexts_without_results": joined["contexts_without_results"],
        "ambiguous": joined["ambiguous"],
    }
    session_metrics: dict[str, dict[str, Any]] = {}
    for sess, vals in sorted(by_session.items()):
        if len(vals) >= _MIN_CELL:
            session_metrics[sess] = _group_stats(vals)
    overall["per_session_slippage"] = session_metrics
    overall["sessions_analysed"] = len(session_metrics)
    overall["sessions_excluded_tiny_n"] = sum(
        1 for v in by_session.values() if len(v) < _MIN_CELL)

    return _report(
        question_id="X3", status="COMPLETE",
        overall=overall, confidence=_confidence(n),
        dataset={"sample_size": n,
                 "source": "execution_context_v1 + execution_results_v1"},
        recommendation="SESSION_QUALITY_PROFILED",
        assumptions=[
            "X3 -> PRIMARY EVIDENCE: execution_context session_state; "
            "execution_results slippage matched by correlation_id.",
            "All condition fields are PRE-EXECUTION.",
        ],
        warnings=[
            "Execution quality is NOT trade quality — low-slippage sessions "
            "may still produce unprofitable trades.",
            "Session cells with N < %d excluded." % _MIN_CELL,
        ],
    )


# ============================================================
# X5 — Execution leakage (decision_trace EV vs realised R)
# ============================================================

def _leakage_rec(leakage: float, ev_mean: float, r_mean: float) -> str:
    if ev_mean <= 0:
        return "NO_POSITIVE_EV_BASELINE"
    if leakage <= 0.05 * abs(ev_mean):
        return "EXECUTION_PRESERVES_EDGE"
    if r_mean > 0:
        return "PARTIAL_EXECUTION_LOSS"
    return "MEANINGFUL_EXECUTION_LOSS"


def run_x5() -> dict[str, Any]:
    dt = _load_decision_trace()
    tt = _load_trade_truth()
    tt_by_coid: dict[str, list[dict]] = defaultdict(list)
    for raw in tt:
        t = normalise_evidence_record(raw, "trade_truth")
        coid = t.get("canonical_opportunity_id", "") or ""
        if coid:
            tt_by_coid[coid].append(t)

    pairs: list[dict[str, Any]] = []
    unmatched_dt = 0
    ambiguous = 0
    for rec in dt:
        coid = rec.get("canonical_opportunity_id", "") or ""
        if not coid:
            continue
        ev = rec.get("ev")
        if ev is None or not math.isfinite(float(ev)):
            continue
        outcomes = tt_by_coid.get(coid, [])
        if not outcomes:
            unmatched_dt += 1
            continue
        if len(outcomes) > 1:
            ambiguous += 1
            continue
        r = outcomes[0].get("r_multiple_realised")
        if r is not None and math.isfinite(float(r)):
            pairs.append({"ev": float(ev), "r": float(r),
                          "symbol": rec.get("symbol", ""), "coid": coid})

    n = len(pairs)
    if n < _MIN_SAMPLE:
        return _report(
            question_id="X5", status="INSUFFICIENT_DATA",
            overall={"n": n, "unmatched_dt": unmatched_dt, "ambiguous": ambiguous},
            confidence="LOW" if n else "INSUFFICIENT_DATA",
            dataset={"sample_size": n, "source": "decision_trace_v1 + trade_truth_v1"},
            recommendation="INSUFFICIENT_DATA",
            assumptions=[
                "EV is PRE-DECISION (STAGE 4 of decision_trace).",
                "r_multiple_realised is POST-OUTCOME, research only.",
                "Joined by canonical_opportunity_id (deterministic).",
            ],
            warnings=["Requires >=30 matched EV <-> R pairs."],
        )

    evs = [p["ev"] for p in pairs]
    rs = [p["r"] for p in pairs]
    ev_mean = statistics.mean(evs)
    r_mean = statistics.mean(rs)
    leakage = ev_mean - r_mean

    overall: dict[str, Any] = {
        "n": n, "unmatched_dt": unmatched_dt, "ambiguous": ambiguous,
        "mean_ev": round(ev_mean, 6),
        "mean_realised_r": round(r_mean, 6),
        "leakage_ev_minus_r": round(leakage, 6),
        "ev_distribution": _group_stats(evs),
        "realised_r_distribution": _group_stats(rs),
    }

    return _report(
        question_id="X5", status="COMPLETE", overall=overall,
        confidence=_confidence(n),
        dataset={"sample_size": n, "source": "decision_trace_v1 + trade_truth_v1"},
        recommendation=_leakage_rec(leakage, ev_mean, r_mean),
        assumptions=[
            "EV is PRE-DECISION; realised R is POST-OUTCOME.",
            "Leakage = mean(EV) - mean(realised_R); positive = edge lost.",
            "Cannot attribute leakage to specific layers from aggregate alone.",
        ],
        warnings=[
            "N = %d — confidence is %s; conclusions are preliminary." % (n, _confidence(n)),
        ],
    )


# ============================================================
# EXEC-1 — Execution failures & adverse conditions
# ============================================================

def run_exec1() -> dict[str, Any]:
    res = _load_results()
    ctx = _load_context()
    if not res:
        return _report(
            question_id="EXEC1", status="INSUFFICIENT_DATA",
            overall={"n": 0, "detail": "No execution_results_v1 records."},
            confidence="INSUFFICIENT_DATA",
            dataset={"sample_size": 0, "source": "execution_results_v1"},
            recommendation="INSUFFICIENT_DATA",
            assumptions=["EXEC-1 reads execution_results_v1 only."],
        )

    n = len(res)
    ok_count = sum(1 for r in res if r.get("result_ok", False))
    fail_count = n - ok_count

    retcodes: dict[str, int] = defaultdict(int)
    for r in res:
        retcodes[str(r.get("retcode", "?"))] += 1

    by_symbol: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"n": 0, "fail": 0})
    for r in res:
        sym = str(r.get("symbol", "UNKNOWN") or "UNKNOWN")
        by_symbol[sym]["n"] += 1
        if not r.get("result_ok", False):
            by_symbol[sym]["fail"] += 1

    overall: dict[str, Any] = {
        "n": n, "success": ok_count, "failures": fail_count,
        "success_rate": round(ok_count / n, 4) if n else None,
        "retcode_distribution": dict(sorted(retcodes.items())),
        "per_symbol": {
            sym: {"n": s["n"],
                  "fail_rate": round(s["fail"] / s["n"], 4) if s["n"] else None}
            for sym, s in sorted(by_symbol.items()) if s["n"] >= _MIN_CELL
        },
    }

    if ctx:
        joined = join_context_to_results(res, ctx)
        matched = joined["matched"]
        spread_slippage: list[float] = []
        for pair in matched:
            sp = pair["context"].get("spread")
            if sp is not None and math.isfinite(float(sp)):
                slp = pair["result"].get("slippage")
                if slp is not None and math.isfinite(float(slp)):
                    spread_slippage.append(float(sp))
        overall["spread_slippage_pairs"] = len(spread_slippage)
        if spread_slippage:
            overall["spread_at_execution"] = _group_stats(spread_slippage)
        overall["context_join"] = {
            "matched": len(matched),
            "results_without_context": joined["results_without_context"],
            "ambiguous": joined["ambiguous"],
        }

    if n < _MIN_SAMPLE:
        conf = "LOW" if n >= 10 else "INSUFFICIENT_DATA"
        return _report(
            question_id="EXEC1", status="INSUFFICIENT_DATA",
            overall=overall, confidence=conf,
            dataset={"sample_size": n, "source": "execution_results_v1 + execution_context_v1"},
            recommendation="INSUFFICIENT_DATA",
            assumptions=[
                "EXEC-1 is OBSERVATIONAL association; failures may reflect "
                "intended rejections of invalid orders, not broker defects.",
            ],
            warnings=["Requires >=30 records for COMPLETE."],
        )

    return _report(
        question_id="EXEC1", status="COMPLETE", overall=overall,
        confidence=_confidence(n),
        dataset={"sample_size": n, "source": "execution_results_v1 + execution_context_v1"},
        recommendation="EXECUTION_SUCCESS_RATE_REPORTED",
        assumptions=[
            "EXEC-1 is OBSERVATIONAL. Failed execution does NOT reveal "
            "what the trade would have earned — no counterfactual PnL claimed.",
            "Execution reliability is NOT strategy profitability.",
        ],
        warnings=[
            "Per-symbol cells with N < %d excluded." % _MIN_CELL,
            "Rejections may include valid order rejections "
            "(e.g. price outside market) — not necessarily broker defects.",
        ],
    )


# ============================================================
# PROT-1 — Protection integrity
# ============================================================

def run_prot1() -> dict[str, Any]:
    protection_selection = select_current_evidence(
        "protection_audit_v1", _load_protection(),
    )
    prot = protection_selection.records_for_analysis()
    evidence_provenance = build_evidence_provenance(protection_selection)
    if not prot:
        return _report(
            question_id="PROT1", status="INSUFFICIENT_DATA",
            overall={"n": 0, "detail": "No protection_audit_v1 records."},
            confidence="INSUFFICIENT_DATA",
            dataset={"sample_size": 0, "source": "protection_audit_v1"},
            recommendation="INSUFFICIENT_DATA",
            evidence_provenance=evidence_provenance,
            assumptions=["PROT-1 reads protection_audit_v1 only."],
        )

    n = len(prot)
    status_counts: dict[str, int] = defaultdict(int)
    sl_match = sl_present = 0
    tp_match = tp_present = 0
    failures = corrections = correction_ok = 0

    for rec in prot:
        status_counts[str(rec.get("protection_status", "UNKNOWN") or "UNKNOWN")] += 1
        rsl = rec.get("requested_sl"); bsl = rec.get("broker_confirmed_sl")
        rtp = rec.get("requested_tp"); btp = rec.get("broker_confirmed_tp")
        if bsl is not None:
            sl_present += 1
            if rsl is not None and abs(float(bsl) - float(rsl)) < 0.0001:
                sl_match += 1
        if btp is not None:
            tp_present += 1
            if rtp is not None and abs(float(btp) - float(rtp)) < 0.0001:
                tp_match += 1
        if rec.get("correction_attempted", False):
            corrections += 1
            if rec.get("correction_success", False):
                correction_ok += 1
        if rec.get("protection_failure_reason", ""):
            failures += 1

    failure_reasons: dict[str, int] = defaultdict(int)
    for rec in prot:
        if not rec.get("protection_failure_reason", ""):
            continue
        for r in str(rec.get("protection_failure_reason", "")).split(";"):
            r = r.strip()
            if r:
                failure_reasons[r] += 1

    overall: dict[str, Any] = {
        "n": n,
        "protection_status_distribution": dict(sorted(status_counts.items())),
        "sl_present": sl_present, "sl_match_count": sl_match,
        "tp_present": tp_present, "tp_match_count": tp_match,
        "failures": failures,
        "correction_attempted": corrections,
        "correction_success_count": correction_ok,
        "failure_reasons": dict(sorted(failure_reasons.items())),
    }

    if n < _MIN_SAMPLE:
        conf = "LOW" if n >= 10 else "INSUFFICIENT_DATA"
        return _report(
            question_id="PROT1", status="INSUFFICIENT_DATA",
            overall=overall, confidence=conf,
            dataset={"sample_size": n, "source": "protection_audit_v1"},
            recommendation="INSUFFICIENT_DATA",
            evidence_provenance=evidence_provenance,
            assumptions=["protection_audit_v1 is POST-FILL verification."],
            warnings=["Requires >=30 records for COMPLETE."],
        )

    return _report(
        question_id="PROT1", status="COMPLETE", overall=overall,
        confidence=_confidence(n),
        dataset={"sample_size": n, "source": "protection_audit_v1"},
        recommendation="PROTECTION_INTEGRITY_REPORTED",
        evidence_provenance=evidence_provenance,
        assumptions=[
            "PROT-1 is audit/research only — never places/modifies SL/TP.",
            "SL/TP match uses tolerance of 0.0001 price units.",
            "Broker-confirmed SL/TP differing from requested values "
            "may indicate broker rounding, not necessarily a defect.",
        ],
        warnings=[
            "Protection integrity is NOT trade quality.",
        ],
    )
