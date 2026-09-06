"""
Q16: Shadow Validation Experiment

Question: "How well do shadow R-multiples predict live R-multiples?"

Computes:
- Number of matched trades (shadow + live outcome)
- Average predicted shadow R
- Average realised live R
- Average prediction error (shadow - live)
- Pearson correlation between shadow and live R
- Mean absolute error
- Shadow accuracy classification
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

from research_engine.correlation.linker import ResearchRecord

logger = logging.getLogger(__name__)


@dataclass
class ShadowValidationResult:
    """Result of Q16 shadow validation experiment."""
    # Dataset
    total_shadow_trades: int = 0
    total_live_trades: int = 0
    matched_trades: int = 0

    # Metrics
    avg_shadow_r: float = 0.0
    avg_live_r: float = 0.0
    avg_prediction_error: float = 0.0
    mean_absolute_error: float = 0.0
    correlation: float | None = None

    # Distribution
    shadow_win_rate: float = 0.0
    live_win_rate: float = 0.0
    directional_accuracy: float = 0.0  # % of trades where shadow and live agree on win/loss

    # Classification
    conclusion: str = ""
    confidence: str = ""  # HIGH / MEDIUM / LOW / INSUFFICIENT_DATA

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_shadow_trades": self.total_shadow_trades,
            "total_live_trades": self.total_live_trades,
            "matched_trades": self.matched_trades,
            "avg_shadow_r": round(self.avg_shadow_r, 4),
            "avg_live_r": round(self.avg_live_r, 4),
            "avg_prediction_error": round(self.avg_prediction_error, 4),
            "mean_absolute_error": round(self.mean_absolute_error, 4),
            "correlation": round(self.correlation, 4) if self.correlation is not None else None,
            "shadow_win_rate": round(self.shadow_win_rate, 4),
            "live_win_rate": round(self.live_win_rate, 4),
            "directional_accuracy": round(self.directional_accuracy, 4),
            "conclusion": self.conclusion,
            "confidence": self.confidence,
        }


def _pearson_correlation(xs: list[float], ys: list[float]) -> float | None:
    """Compute Pearson correlation coefficient. Returns None if insufficient data."""
    n = len(xs)
    if n < 3:
        return None

    mean_x = sum(xs) / n
    mean_y = sum(ys) / n

    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))

    if den_x == 0 or den_y == 0:
        return None

    return num / (den_x * den_y)


def run_shadow_validation(records: list[ResearchRecord]) -> ShadowValidationResult:
    """
    Run Q16: Shadow Validation Experiment.

    Analyses matched records (shadow + live) to determine how well
    shadow R-multiples predict actual live R-multiples.
    """
    result = ShadowValidationResult()

    # Count totals
    result.total_shadow_trades = sum(1 for r in records if r.has_shadow)
    result.total_live_trades = sum(1 for r in records if r.has_live)

    # Filter to matched records only
    matched = [r for r in records if r.is_matched()]
    result.matched_trades = len(matched)

    if result.matched_trades == 0:
        result.conclusion = "No matched trades found. Cannot validate shadow model."
        result.confidence = "INSUFFICIENT_DATA"
        return result

    # Extract R-multiple pairs
    shadow_rs = [r.shadow_r for r in matched]
    live_rs = [r.live_r for r in matched]
    errors = [r.prediction_error for r in matched]

    # Basic metrics
    result.avg_shadow_r = sum(shadow_rs) / len(shadow_rs)
    result.avg_live_r = sum(live_rs) / len(live_rs)
    result.avg_prediction_error = sum(errors) / len(errors)
    result.mean_absolute_error = sum(abs(e) for e in errors) / len(errors)

    # Correlation
    result.correlation = _pearson_correlation(shadow_rs, live_rs)

    # Win rates
    result.shadow_win_rate = sum(1 for r in shadow_rs if r > 0) / len(shadow_rs)
    result.live_win_rate = sum(1 for r in live_rs if r > 0) / len(live_rs)

    # Directional accuracy (shadow and live agree on win/loss)
    directional_matches = sum(
        1 for s, l in zip(shadow_rs, live_rs)
        if (s > 0 and l > 0) or (s <= 0 and l <= 0)
    )
    result.directional_accuracy = directional_matches / len(matched)

    # Classification (thresholds unchanged; correlation may be None for n<3)
    corr_str = f"{result.correlation:.2f}" if result.correlation is not None else "N/A"
    if result.matched_trades < 10:
        result.confidence = "LOW"
        result.conclusion = (
            f"Only {result.matched_trades} matched trades. "
            f"Preliminary correlation: {corr_str}. "
            f"Insufficient data for confident validation."
        )
    elif result.matched_trades < 30:
        result.confidence = "MEDIUM"
        if result.correlation is not None and result.correlation >= 0.5:
            result.conclusion = (
                f"Shadow model shows moderate predictive power "
                f"(r={corr_str}, n={result.matched_trades}). "
                f"Directional accuracy: {result.directional_accuracy:.0%}. "
                f"More data needed for high confidence."
            )
        else:
            result.conclusion = (
                f"Shadow model shows weak predictive power "
                f"(r={corr_str}, n={result.matched_trades}). "
                f"Shadow may not reliably predict live outcomes."
            )
    else:
        result.confidence = "HIGH"
        if result.correlation is not None and result.correlation >= 0.7:
            result.conclusion = (
                f"Shadow model is strongly predictive "
                f"(r={corr_str}, n={result.matched_trades}). "
                f"Directional accuracy: {result.directional_accuracy:.0%}. "
                f"Shadow-based research findings are trustworthy."
            )
        elif result.correlation is not None and result.correlation >= 0.4:
            result.conclusion = (
                f"Shadow model is moderately predictive "
                f"(r={corr_str}, n={result.matched_trades}). "
                f"MAE={result.mean_absolute_error:.2f}R. "
                f"Shadow research usable with caveats."
            )
        else:
            result.conclusion = (
                f"Shadow model has weak correlation with live outcomes "
                f"(r={corr_str}, n={result.matched_trades}). "
                f"Shadow-based research may be unreliable."
            )

    logger.info(
        "[Q16] matched=%d correlation=%s avg_error=%.3f conclusion=%s",
        result.matched_trades,
        f"{result.correlation:.3f}" if result.correlation else "N/A",
        result.avg_prediction_error,
        result.confidence,
    )

    return result


# ─── STANDARD REPORT PERSISTENCE ──────────────────────────────────────────────


def run() -> dict:
    """
    Run Q16/X4 and persist the result using the standard research report framework.

    Populations (authoritative):
        shadow  — canonical shadow_runtime_v1 ingestion (nshadow_* completed
                  outcomes, internal research shape), restricted by the matcher
                  to PRIMARY_HORIZON_SIMULATION per canonical opportunity.
        live    — trade_truth_v1 (realised broker outcomes).

    Matching contract: one-to-one on canonical_opportunity_id (see
    research_engine.correlation.linker.match_shadow_to_live). correlation_id
    (COR-*) exists only on the live side and is carried onto the matched
    record as the decision spine, never used as the join key.
    """
    from research_engine.correlation.linker import (
        extract_canonical_opportunity_id,
        match_shadow_to_live,
    )
    from research_engine.data_access.s3_source import get_default_source
    from research_engine.data_access.shadow_runtime_ingestion import (
        ingest_completed_shadow_trades,
    )

    _source = get_default_source()

    # Canonical production shadow source: S3 shadow_runtime_v1 event stream,
    # reconstructed into completed shadow outcomes (internal research shape).
    shadows = ingest_completed_shadow_trades()
    truths = _source.read_dataset("trade_truth")

    # ─── structural gates (source availability / schema sanity) ──────────────
    if not shadows:
        return _blocked_report(
            "Canonical shadow source (shadow_runtime_v1 ingestion) returned NO "
            "completed outcomes — source unavailable/collection gap."
        )
    if not truths:
        return _blocked_report(
            "Live source (trade_truth_v1) returned NO records — source "
            "unavailable/collection gap."
        )
    if not any(extract_canonical_opportunity_id(s) for s in shadows):
        return _blocked_report(
            "No normalized shadow record carries canonical_opportunity_id — "
            "schema mismatch between ingestion and matcher."
        )
    if not any(extract_canonical_opportunity_id(t) for t in truths):
        return _blocked_report(
            "No trade_truth record carries identity.canonical_opportunity_id — "
            "schema mismatch between live outcomes and matcher."
        )

    # ─── canonical one-to-one matching ────────────────────────────────────────
    records, diagnostics = match_shadow_to_live(shadows, truths)
    result = run_shadow_validation(records)


    # ─── status semantics ─────────────────────────────────────────────────────
    if result.matched_trades > 0:
        status = "COMPLETE"
        finding = result.conclusion
        if result.correlation and result.correlation >= 0.6:
            recommendation = "SHADOW_TRUSTED"
        else:
            recommendation = "SHADOW_UNRELIABLE"
    else:
        # Both populations exist and carry join keys, but genuinely no overlap.
        status = "INSUFFICIENT_DATA"
        finding = (
            "No shadow↔live overlap: "
            f"{diagnostics.total_shadow} shadow outcomes and "
            f"{diagnostics.total_live} live outcomes share no "
            "canonical_opportunity_id. Q16/X4 waits for live trades executed "
            "on shadow-observed opportunities."
        )
        recommendation = "WAIT"

    from research_engine.experiments.experiment_base import build_report, build_fingerprint

    # Up to 3 real matched-pair lineage examples (join-key proof, not metrics).
    examples = [
        {
            "canonical_opportunity_id": r.canonical_opportunity_id,
            "live_correlation_id": r.correlation_id,
            "shadow_r_multiple": r.shadow_r,
            "live_r_multiple": r.live_r,
        }
        for r in records if r.is_matched()
    ][:3]

    report = build_report(
        question_id="Q16",
        status=status,
        overall={
            "matched_trades": result.matched_trades,
            "correlation": round(result.correlation, 4) if result.correlation else None,
            "mean_absolute_error": round(result.mean_absolute_error, 4),
            "directional_accuracy": round(result.directional_accuracy, 4) if result.directional_accuracy else None,
            "finding": finding,
            "join_contract": {
                "join_key": "canonical_opportunity_id",
                "shadow_population": "shadow_runtime_v1 ingestion (nshadow_*), PRIMARY_HORIZON_SIMULATION",
                "live_population": "trade_truth_v1 (identity.canonical_opportunity_id)",
            },
            "match_diagnostics": diagnostics.to_dict(),
            "matched_examples": examples,
            **result.to_dict(),
        },
        confidence=result.confidence,
        dataset={
            "source": "shadow_runtime_v1(ingested, nshadow_*) + trade_truth_v1 "
                      "(matched 1:1 by canonical_opportunity_id)",
            "sample_size": result.matched_trades,
        },
        fingerprint=build_fingerprint(result.matched_trades, diagnostics.unmatched_shadow, "shadow_runtime_v1+trade_truth_v1"),
        recommendation=recommendation,
        provenance={"experiment_module": "research_engine.experiments.shadow_validation", "registry_id": "Q16", "function": "run", "pipeline": "Question -> Experiment -> Dataset -> Output -> Knowledge -> Command Centre"},
    )

    # Persist
    try:
        from research_engine.experiments.experiment_base import persist_report as eb_persist
        eb_persist(report, "q16_shadow_validation.json")
    except Exception:
        pass

    return report


def _blocked_report(reason: str) -> dict:
    """Structural BLOCKED report — a wiring/schema/source problem, never 'no evidence'."""
    from research_engine.experiments.experiment_base import build_report, build_fingerprint

    report = build_report(
        question_id="Q16",
        status="BLOCKED",
        overall={
            "matched_trades": 0,
            "correlation": None,
            "mean_absolute_error": 0.0,
            "directional_accuracy": None,
            "finding": reason,
            "match_diagnostics": {
                "total_shadow": 0,
                "total_live": 0,
                "matched_pairs": 0,
            },
            "join_contract": {
                "join_key": "canonical_opportunity_id",
                "shadow_population": "shadow_runtime_v1 ingestion (nshadow_*), PRIMARY_HORIZON_SIMULATION",
                "live_population": "trade_truth_v1 (identity.canonical_opportunity_id)",
            },
        },
        confidence="INSUFFICIENT_DATA",
        dataset={"source": "shadow_runtime_v1(ingested) + trade_truth_v1", "sample_size": 0},
        fingerprint=build_fingerprint(0, 0, "shadow_runtime_v1+trade_truth_v1"),
        recommendation="BLOCKED",
        provenance={"experiment_module": "research_engine.experiments.shadow_validation", "registry_id": "Q16", "function": "run", "pipeline": "Question -> Experiment -> Dataset -> Output -> Knowledge -> Command Centre"},
    )
    try:
        from research_engine.experiments.experiment_base import persist_report as eb_persist
        eb_persist(report, "q16_shadow_validation.json")
    except Exception:
        pass
    return report


if __name__ == "__main__":
    r = run()
    print(f"Q16: matched={r.matched_trades} corr={r.correlation} | {r.confidence}")
