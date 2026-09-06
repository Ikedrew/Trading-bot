"""
Candidate Evaluator — Statistical evaluation of paired baseline/candidate shadow observations.

Transforms accumulated prospective observations into an evidence-backed verdict:
    VALIDATED / REJECTED / INCONCLUSIVE

Uses exclusively PROSPECTIVE observations (after candidate activation).
Reuses existing validation_harness infrastructure for all statistical tests.

BASELINE (Phase 1I-C repair): the retired ``V10_PRIMARY`` population has been
replaced by the honest pairing contract in
``research_engine.lifecycle.candidate_pairing``: each candidate shadow
(``shadow_type=CANDIDATE_<id>``) is paired with the DEPLOYED logic's realised
outcome (``trade_truth``) on the SAME opportunity via exact correlation_id.
This module delegates ALL pairing to that shared contract so the counted
population and the evaluated population cannot drift.

This module NEVER modifies production V10 or promotes candidates automatically.
"""

from __future__ import annotations

import statistics
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from research_engine.lifecycle.candidate_pairing import (
    build_prospective_pairs,
)
from research_engine.lifecycle.validation_harness import (
    bootstrap_ci,
    permutation_test_paired,
    temporal_stability,
    outlier_influence,
)


# ═══════════════════════════════════════════════════════════════════════════════
# EVALUATION RESULT
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class CandidateEvaluation:
    """Complete result of evaluating a candidate against its baseline."""
    evaluation_id: str = ""
    candidate_id: str = ""
    timestamp: str = ""

    # Dataset
    prospective_boundary: str = ""      # Observations after this timestamp count
    total_observations_raw: int = 0
    eligible_pairs: int = 0
    excluded_unpaired: int = 0
    excluded_pre_boundary: int = 0

    # Primary metrics
    n: int = 0
    mean_baseline_r: float = 0.0
    mean_candidate_r: float = 0.0
    mean_delta_r: float = 0.0
    median_delta_r: float = 0.0
    total_baseline_r: float = 0.0
    total_candidate_r: float = 0.0
    candidate_wins: int = 0             # Pairs where candidate > baseline
    candidate_win_rate: float = 0.0

    # Statistical significance
    ci_lower: float | None = None
    ci_upper: float | None = None
    permutation_p: float | None = None

    # OOS (chronological split of prospective data)
    oos_n: int = 0
    oos_delta_r: float = 0.0

    # Robustness
    symbols_positive: int = 0
    symbols_total: int = 0
    periods_positive: int = 0
    periods_total: int = 0
    survives_outlier_removal: bool = False

    # Risk
    worst_delta_r: float = 0.0
    risk_level: str = ""

    # Decision
    decision: str = ""                  # VALIDATED / REJECTED / INCONCLUSIVE
    decision_reason: str = ""
    confidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


# ═══════════════════════════════════════════════════════════════════════════════
# EVALUATION CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class EvaluationConfig:
    """Thresholds for candidate evaluation."""
    minimum_sample: int = 30
    significance_ci_excludes_zero: bool = True   # CI lower bound > 0
    significance_p_threshold: float = 0.05       # Permutation p < this
    oos_split: float = 0.6                       # First 60% = train, last 40% = OOS
    min_symbols_positive: int = 2
    min_periods_positive: int = 2


# ═══════════════════════════════════════════════════════════════════════════════
# EVALUATOR
# ═══════════════════════════════════════════════════════════════════════════════

class CandidateEvaluator:
    """
    Evaluates a candidate by comparing paired prospective shadow observations.
    
    Usage:
        evaluator = CandidateEvaluator()
        result = evaluator.evaluate(
            candidate_id="OPT-xxx",
            candidate_activated_at="2026-08-13T...",
            shadow_observations=all_shadow_trades,
        )
    """

    def __init__(self, config: EvaluationConfig | None = None):
        self._config = config or EvaluationConfig()

    def evaluate(
        self,
        *,
        candidate_id: str,
        candidate_activated_at: str,
        pairs: list[dict[str, Any]] | None = None,
        candidate_records: list[dict[str, Any]] | None = None,
        incumbent_records: list[dict[str, Any]] | None = None,
    ) -> CandidateEvaluation:
        """
        Evaluate a candidate from matched prospective pairs.

        Args:
            candidate_id: The candidate being evaluated
            candidate_activated_at: ISO timestamp — the prospective boundary
            pairs: Pre-built matched pairs (from candidate_pairing). When None,
                pairs are built via the shared pairing contract using the
                injected populations, or the sanctioned S3 loaders when no
                populations are injected.
            candidate_records: Optional injected candidate-shadow CLOSE records
                (dataset ``shadow_trades`` shape) — used instead of an S3 load.
            incumbent_records: Optional injected trade_truth records.

        Returns:
            CandidateEvaluation with decision and full metrics
        """
        eval_id = f"EVAL-{uuid.uuid4().hex[:8]}"
        result = CandidateEvaluation(
            evaluation_id=eval_id,
            candidate_id=candidate_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            prospective_boundary=candidate_activated_at,
        )

        # ─── STEP 1+2: MATCHED PROSPECTIVE PAIRS (shared contract) ────
        # All pairing — completeness, lineage, prospectivity, one-to-one,
        # duplicate/ambiguity handling — is owned by candidate_pairing.
        if pairs is None:
            pairing = build_prospective_pairs(
                candidate_id=candidate_id,
                candidate_activated_at=candidate_activated_at,
                candidate_records=candidate_records,
                incumbent_records=incumbent_records,
            )
            pairs = pairing.pairs
            diag = pairing.diagnostics
            result.total_observations_raw = (
                diag.candidate_records_total + diag.incumbent_records_total
            )
            result.excluded_unpaired = (
                diag.unmatched_no_incumbent + diag.symbol_mismatch
                + diag.horizon_mismatch + diag.candidate_ambiguous
                + diag.incumbent_ambiguous
            )
            result.excluded_pre_boundary = (
                diag.candidate_before_boundary + diag.incumbent_before_boundary
            )

        result.eligible_pairs = len(pairs)

        # ─── STEP 3: MINIMUM SAMPLE GATE ─────────────────────────────
        result.n = len(pairs)
        if result.n < self._config.minimum_sample:
            result.decision = "INCONCLUSIVE"
            result.decision_reason = (f"Insufficient prospective evidence: "
                                       f"N={result.n} < minimum={self._config.minimum_sample}")
            result.confidence = "INSUFFICIENT"
            return result

        # ─── STEP 4: COMPUTE PAIRED DELTAS ───────────────────────────
        baseline_r = [p["baseline_r"] for p in pairs]
        candidate_r = [p["candidate_r"] for p in pairs]
        delta_r = [p["candidate_r"] - p["baseline_r"] for p in pairs]

        result.mean_baseline_r = round(statistics.mean(baseline_r), 4)
        result.mean_candidate_r = round(statistics.mean(candidate_r), 4)
        result.mean_delta_r = round(statistics.mean(delta_r), 4)
        result.median_delta_r = round(statistics.median(delta_r), 4)
        result.total_baseline_r = round(sum(baseline_r), 2)
        result.total_candidate_r = round(sum(candidate_r), 2)
        result.candidate_wins = sum(1 for d in delta_r if d > 0)
        result.candidate_win_rate = round(result.candidate_wins / result.n, 3)
        result.worst_delta_r = round(min(delta_r), 4)

        # ─── STEP 5: STATISTICAL TESTS ───────────────────────────────
        ci_lo, ci_hi = bootstrap_ci(delta_r, seed=42)
        result.ci_lower = round(ci_lo, 4) if ci_lo is not None else None
        result.ci_upper = round(ci_hi, 4) if ci_hi is not None else None

        try:
            p_val = permutation_test_paired(candidate_r, baseline_r, n_perms=5000, seed=42)
            result.permutation_p = round(p_val, 4)
        except (ValueError, ZeroDivisionError):
            result.permutation_p = None

        # ─── STEP 6: OOS ─────────────────────────────────────────────
        split_idx = int(result.n * self._config.oos_split)
        if split_idx > 0 and split_idx < result.n:
            oos_deltas = delta_r[split_idx:]
            result.oos_n = len(oos_deltas)
            result.oos_delta_r = round(statistics.mean(oos_deltas), 4) if oos_deltas else 0

        # ─── STEP 7: ROBUSTNESS ──────────────────────────────────────
        # Symbol robustness (from paired records)
        by_symbol = defaultdict(list)
        for p in pairs:
            by_symbol[p.get("symbol", "")].append(p["candidate_r"] - p["baseline_r"])
        sym_positive = sum(1 for vals in by_symbol.values() if len(vals) >= 3 and statistics.mean(vals) > 0)
        result.symbols_positive = sym_positive
        result.symbols_total = sum(1 for vals in by_symbol.values() if len(vals) >= 3)

        # Temporal stability
        n_periods = min(5, max(2, result.n // 10))
        period_size = result.n // n_periods if n_periods > 0 else result.n
        periods_pos = 0
        for i in range(n_periods):
            chunk = delta_r[i * period_size:(i + 1) * period_size]
            if chunk and statistics.mean(chunk) > 0:
                periods_pos += 1
        result.periods_positive = periods_pos
        result.periods_total = n_periods

        # Outlier robustness
        sorted_deltas = sorted(delta_r, reverse=True)
        trimmed = sorted_deltas[5:]  # Remove top 5
        result.survives_outlier_removal = (statistics.mean(trimmed) > 0) if trimmed else False

        # ─── STEP 8: RISK ASSESSMENT ─────────────────────────────────
        if result.n < 50:
            result.risk_level = "HIGH"
        elif not result.survives_outlier_removal or result.symbols_positive < 2:
            result.risk_level = "HIGH"
        elif result.n < 100:
            result.risk_level = "MEDIUM"
        else:
            result.risk_level = "LOW"

        # ─── STEP 9: DECISION ─────────────────────────────────────────
        result.decision, result.decision_reason, result.confidence = self._make_decision(result)

        return result

    # ─── INTERNAL ─────────────────────────────────────────────────────

    def _make_decision(self, result: CandidateEvaluation) -> tuple[str, str, str]:
        """Determine VALIDATED / REJECTED / INCONCLUSIVE."""
        cfg = self._config

        # Gate 1: Statistical significance
        passes_ci = (result.ci_lower is not None and result.ci_lower > 0) if cfg.significance_ci_excludes_zero else True
        passes_p = (result.permutation_p is not None and result.permutation_p < cfg.significance_p_threshold)
        passes_significance = passes_ci or passes_p

        if not passes_significance:
            if result.mean_delta_r < 0:
                return ("REJECTED",
                        f"Candidate underperforms baseline (delta={result.mean_delta_r:+.4f}, "
                        f"CI=[{result.ci_lower}, {result.ci_upper}])",
                        "HIGH")
            return ("INCONCLUSIVE",
                    f"Effect not statistically significant "
                    f"(CI=[{result.ci_lower}, {result.ci_upper}], p={result.permutation_p})",
                    "LOW")

        # Gate 2: Robustness
        robust = (result.symbols_positive >= cfg.min_symbols_positive and
                  result.periods_positive >= cfg.min_periods_positive and
                  result.survives_outlier_removal)

        if not robust:
            reasons = []
            if result.symbols_positive < cfg.min_symbols_positive:
                reasons.append(f"symbols_positive={result.symbols_positive}<{cfg.min_symbols_positive}")
            if result.periods_positive < cfg.min_periods_positive:
                reasons.append(f"periods_positive={result.periods_positive}<{cfg.min_periods_positive}")
            if not result.survives_outlier_removal:
                reasons.append("fails_outlier_removal")
            return ("INCONCLUSIVE",
                    f"Significant but fragile: {'; '.join(reasons)}",
                    "MEDIUM")

        # Gate 3: OOS consistency
        if result.oos_n >= 10 and result.oos_delta_r <= 0:
            return ("INCONCLUSIVE",
                    f"OOS shows no improvement (OOS delta={result.oos_delta_r:+.4f}, N={result.oos_n})",
                    "MEDIUM")

        # All gates passed
        confidence = "HIGH" if result.n >= 100 else "MEDIUM"
        return ("VALIDATED",
                f"Candidate outperforms baseline: delta={result.mean_delta_r:+.4f}, "
                f"CI=[{result.ci_lower:+.4f},{result.ci_upper:+.4f}], "
                f"p={result.permutation_p}, OOS={result.oos_delta_r:+.4f}, "
                f"symbols={result.symbols_positive}/{result.symbols_total}, "
                f"periods={result.periods_positive}/{result.periods_total}",
                confidence)


