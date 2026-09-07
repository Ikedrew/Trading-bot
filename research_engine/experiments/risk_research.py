"""
Risk Research — RISK-1 (Wave 5)

Canonical risk-deviation research over risk_deviation_v1.

EVIDENCE CONTRACT (forensically proven from core/risk_deviation.py):

  risk_deviation_v1 is a POST-OUTCOME diagnostic projection:
      semantic_stage = "post_outcome_analysis"
      authority      = "diagnostic_projection"
      pre_trade_authority = False

  For every closing trade that flows through the trade journal, one record is
  written.  planned_risk_R is ALWAYS -1.0 by definition (one unit of risk).
  actual_risk_R is the realised R-multiple of the completed trade.
  risk_deviation for a loss = abs(actual_risk_R / planned_risk_R)  -> the
  magnitude of the realised loss expressed in units of planned risk.
  classification:  NORMAL (deviation <= 1.5), ELEVATED (1.5-3.0),
                   CRITICAL (> 3.0), WIN (profitable trade), NO_RISK_DATA.

DENOMINATOR SEMANTICS (proven):

  The dataset is written per closing trade through the trade journal and
  includes WIN records as well as loss records.  Deviation rates are therefore
  computed over the recorded classification population and the documented
  assumption states that the denominator is "closed-trade diagnostic records",
  NOT "all risk observations" and NOT "deviations only".

HONEST LIMITS:

  1. risk_deviation_v1 does NOT represent a pre-trade "intended risk ->
     requested risk -> broker-executed risk" chain.  It compares the realised
     loss to the 1-R definition AFTER the outcome is known.  No claim that a
     deviation "caused" the loss is ever made: the deviation IS a measure of
     the realised loss magnitude.

  2. A "WIN" record does not prove risk was control-perfect; it only records
     that the trade was profitable.  Conversely an ELEVATED/CRITICAL loss
     records that the realised loss exceeded the 1-R plan.

  3. NO_RISK_DATA records are counted but excluded from all deviation-rate
     calculations.  Their presence is reported explicitly.

TEMPORAL CLASSES:
    all fields -> POST-OUTCOME (research evaluation only)
"""

from __future__ import annotations

import logging
import math
import statistics
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)

_MIN_SAMPLE = 30   # overall status gate (engine convention)
_MIN_CELL = 10     # subgroup/cell gate

# Canonical production risk-deviation classification bands (from
# core/risk_deviation.py — NOT invented here).
_NORMAL_MAX = 1.5
_ELEVATED_MAX = 3.0

_NORMAL = "NORMAL"
_ELEVATED = "ELEVATED"
_CRITICAL = "CRITICAL"
_WIN = "WIN"
_NO_RISK_DATA = "NO_RISK_DATA"

_CLASSIFICATIONS = (_NORMAL, _ELEVATED, _CRITICAL, _WIN, _NO_RISK_DATA)


def _load_risk_deviation() -> list[dict[str, Any]]:
    from research_engine.data_access.loaders import load_risk_deviation
    return load_risk_deviation()


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
    assumptions: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    from research_engine.experiments.experiment_base import (
        build_report, build_fingerprint,
    )
    sample = dataset.get("sample_size", 0)
    return build_report(
        question_id=question_id,
        status=status,
        overall=overall,
        confidence=confidence,
        dataset=dataset,
        fingerprint=build_fingerprint(sample, 0, "risk_deviation"),
        recommendation=recommendation,
        assumptions=assumptions or [],
        warnings=warnings or [],
        provenance={
            "experiment_module": "research_engine.experiments.risk_research",
            "registry_id": question_id,
            "pipeline": "Question -> Experiment -> Dataset -> Output -> "
                        "Knowledge -> Command Centre",
        },
    )


def _group_stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "mean": None, "median": None,
                "stdev": None, "p75": None, "min": None, "max": None}
    vals = sorted(v for v in values if math.isfinite(v))
    n = len(vals)
    if n == 0:
        return {"n": 0, "mean": None, "median": None,
                "stdev": None, "p75": None, "min": None, "max": None}
    mean = statistics.mean(vals)
    return {
        "n": n,
        "mean": round(mean, 6),
        "median": round(statistics.median(vals), 6),
        "stdev": round(statistics.stdev(vals), 6) if n > 1 else None,
        "p75": round(vals[min(int(n * 0.75), n - 1)], 6),
        "min": round(vals[0], 6),
        "max": round(vals[-1], 6),
    }


def _flatten(rec: dict[str, Any]) -> dict[str, Any] | None:
    """Flatten one risk_deviation_v1 record. Returns None if unusable."""
    trade_id = str(rec.get("trade_id", "") or "")
    if not trade_id or trade_id == "None":
        return None
    classification = str(rec.get("risk_classification", "") or "") or None
    if classification is not None and classification not in _CLASSIFICATIONS:
        classification = None
    planned = rec.get("planned_risk_R")
    actual = rec.get("actual_risk_R")
    deviation = rec.get("risk_deviation")

    def _finite(v) -> float | None:
        if v is None:
            return None
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if math.isfinite(f) else None

    planned_f = _finite(planned)
    actual_f = _finite(actual)
    deviation_f = _finite(deviation)
    return {
        "trade_id": trade_id,
        "correlation_id": str(rec.get("correlation_id", "") or ""),
        "symbol": str(rec.get("symbol", "") or "UNKNOWN"),
        "planned_risk_R": planned_f,
        "actual_risk_R": actual_f,
        "risk_deviation": deviation_f,
        "classification": classification,
        "direction": str(rec.get("direction", "") or ""),
        "semantic_stage": str(rec.get("semantic_stage", "") or ""),
    }


def run_risk1() -> dict[str, Any]:
    """RISK-1 — Risk control fidelity.

    Question: When the bot realises a loss, does the realised loss magnitude
    respect the 1-R planned-risk definition?  How often does realised loss
    exceed the plan (ELEVATED / CRITICAL)?
    """
    raw = _load_risk_deviation()
    records: list[dict[str, Any]] = []
    excluded_no_trade_id = 0
    excluded_invalid_class = 0
    for rec in raw:
        flat = _flatten(rec)
        if flat is None:
            excluded_no_trade_id += 1
            continue
        if flat["classification"] is None:
            excluded_invalid_class += 1
            continue
        records.append(flat)

    n = len(records)
    if n < _MIN_SAMPLE:
        return _report(
            question_id="RISK-1",
            status="INSUFFICIENT_DATA",
            overall={
                "n": n,
                "excluded_no_trade_id": excluded_no_trade_id,
                "excluded_invalid_class": excluded_invalid_class,
            },
            confidence="LOW" if n else "INSUFFICIENT_DATA",
            dataset={"sample_size": n, "source": "risk_deviation_v1"},
            recommendation="INSUFFICIENT_DATA",
            assumptions=[
                "risk_deviation_v1 is a POST-OUTCOME diagnostic projection "
                "(semantic_stage=post_outcome_analysis).",
                "Requires >=30 recorded classification records for COMPLETE.",
            ],
        )

    # numerator/denominator accounting
    counts: dict[str, int] = defaultdict(int)
    for rec in records:
        counts[rec["classification"]] += 1

    loss_records = [r for r in records
                    if r["classification"] in (_NORMAL, _ELEVATED, _CRITICAL)]
    deviations_loss = [
        r["risk_deviation"] for r in loss_records
        if r["risk_deviation"] is not None
    ]
    actual_r_loss = [
        r["actual_risk_R"] for r in loss_records
        if r["actual_risk_R"] is not None
    ]
    wins = [r for r in records if r["classification"] == _WIN]

    # over/under risk — classification bands are authoritative (production
    # semantics from core/risk_deviation.py); deviation stats are reported
    # alongside for magnitude.
    over_risk = [r for r in loss_records
                 if r["classification"] in (_ELEVATED, _CRITICAL)]
    critical = [r for r in loss_records if r["classification"] == _CRITICAL]

    overall: dict[str, Any] = {
        "n": n,
        "excluded_no_trade_id": excluded_no_trade_id,
        "excluded_invalid_class": excluded_invalid_class,
        "classification_distribution": dict(sorted(counts.items())),
        "loss_records": len(loss_records),
        "win_records": len(wins),
        # --- loss / deviation profile (numerator + denominator) ---
        "deviation_rate_over_plan": {
            "numerator": len(over_risk),
            "denominator": len(loss_records),
            "rate": round(len(over_risk) / len(loss_records), 4) if loss_records else None,
        },
        "critical_loss_rate": {
            "numerator": len(critical),
            "denominator": len(loss_records),
            "rate": round(len(critical) / len(loss_records), 4) if loss_records else None,
        },
        "loss_deviation_stats": _group_stats(deviations_loss),
        "loss_actual_r_stats": _group_stats(actual_r_loss),
    }

    # per-symbol classification distribution (cell N >= _MIN_CELL)
    by_symbol: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"n": 0, "loss": 0, "elevated_or_critical": 0})
    for rec in records:
        sym = rec["symbol"]
        by_symbol[sym]["n"] += 1
        if rec["classification"] in (_NORMAL, _ELEVATED, _CRITICAL):
            by_symbol[sym]["loss"] += 1
        if rec["classification"] in (_ELEVATED, _CRITICAL):
            by_symbol[sym]["elevated_or_critical"] += 1

    overall["per_symbol"] = {
        sym: {
            "n": s["n"],
            "loss_count": s["loss"],
            "elevated_or_critical": s["elevated_or_critical"],
            "over_plan_rate": round(
                s["elevated_or_critical"] / s["n"], 4) if s["n"] else None,
        }
        for sym, s in sorted(by_symbol.items()) if s["n"] >= _MIN_CELL
    }

    return _report(
        question_id="RISK-1",
        status="COMPLETE",
        overall=overall,
        confidence=_confidence(n),
        dataset={"sample_size": n, "source": "risk_deviation_v1"},
        recommendation=_risk1_recommendation(
            len(loss_records), len(over_risk), len(critical), n),
        assumptions=[
            "risk_deviation_v1 is a POST-OUTCOME diagnostic projection: "
            "it compares the realised loss magnitude to the 1-R definition "
            "AFTER the outcome is known.",
            "Denominator = recorded classification population (closed-trade "
            "diagnostic records). It is NOT 'all risk observations' and NOT "
            "'deviations only'.",
            "planned_risk_R is always -1.0 by definition (one unit of risk); "
            "the dataset does NOT capture a pre-trade intended-risk plan.",
            "Deviation is a measure of realised loss magnitude — it does NOT "
            "prove that a deviation 'caused' a loss.",
            "NO_RISK_DATA records are counted but excluded from deviation "
            "rates.",
        ],
        warnings=[
            "A WIN record only records profitability — it does not prove "
            "risk-control perfection.",
            "ELEVATED/CRITICAL loss records indicate realised loss exceeded "
            "the 1-R plan, which may reflect market gap, slippage, or "
            "protection failure — this dataset alone cannot distinguish "
            "the cause.",
            "The true pre-trade chain (intended -> requested -> broker-"
            "executed risk) is NOT represented anywhere in current V1 "
            "canonical evidence; this question measures loss fidelity, not "
            "pre-trade risk planning.",
            "Per-symbol cells with N < %d are excluded." % _MIN_CELL,
        ],
    )


def _risk1_recommendation(loss_count: int, over_count: int,
                          critical_count: int, n: int) -> str:
    if loss_count == 0:
        return "NO_LOGGED_LOSSES"
    over_rate = over_count / loss_count
    if over_rate >= 0.20:
        return "ELEVATED_LOSS_DEVIATION_RATE"
    if critical_count > 0:
        return "CRITICAL_LOSSES_PRESENT"
    if over_rate >= 0.05:
        return "MINOR_LOSS_DEVIATION"
    return "LOSSES_WITHIN_PLANNED_RISK"