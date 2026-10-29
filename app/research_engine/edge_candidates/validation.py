"""
Candidate Walk-Forward Validation — Tests edge candidates out-of-sample.

For each candidate:
    - Split data chronologically (expanding training window)
    - Match decisions to candidate conditions
    - Measure performance only on test period
    - No future information leakage

This is RESEARCH ONLY. No production code is modified.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from research_engine.edge_attribution.models import EdgeAttributionRecord
from research_engine.edge_candidates.models import EdgeCandidate

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# RESULT STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class CandidateSplitResult:
    """One candidate's performance in one test split."""
    split: int
    train_size: int = 0
    test_size: int = 0
    trades_taken: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    avg_r: float = 0.0
    total_r: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "split": self.split, "train_size": self.train_size, "test_size": self.test_size,
            "trades_taken": self.trades_taken, "wins": self.wins, "losses": self.losses,
            "win_rate": round(self.win_rate, 4), "avg_r": round(self.avg_r, 4),
            "total_r": round(self.total_r, 4), "profit_factor": round(self.profit_factor, 2),
            "max_drawdown": round(self.max_drawdown, 4),
        }


@dataclass
class CandidateValidationResult:
    """Complete validation result for one candidate."""
    candidate_id: str = ""
    conditions: dict[str, str] = field(default_factory=dict)

    # Splits
    splits: list[CandidateSplitResult] = field(default_factory=list)
    splits_positive: int = 0
    splits_total: int = 0
    positive_rate: float = 0.0

    # Aggregate
    total_trades: int = 0
    total_r: float = 0.0
    avg_ev: float = 0.0
    avg_win_rate: float = 0.0
    worst_split_r: float = 0.0
    best_split_r: float = 0.0
    max_drawdown: float = 0.0

    # Dependency
    symbol_concentration: float = 0.0  # Fraction from top symbol
    session_concentration: float = 0.0

    # Pass/Fail
    passes: bool = False
    fail_reasons: list[str] = field(default_factory=list)
    confidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id, "conditions": self.conditions,
            "splits": [s.to_dict() for s in self.splits],
            "splits_positive": self.splits_positive, "splits_total": self.splits_total,
            "positive_rate": round(self.positive_rate, 4),
            "total_trades": self.total_trades, "total_r": round(self.total_r, 4),
            "avg_ev": round(self.avg_ev, 4), "avg_win_rate": round(self.avg_win_rate, 4),
            "worst_split_r": round(self.worst_split_r, 4), "best_split_r": round(self.best_split_r, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "symbol_concentration": round(self.symbol_concentration, 4),
            "session_concentration": round(self.session_concentration, 4),
            "passes": self.passes, "fail_reasons": self.fail_reasons,
            "confidence": self.confidence,
        }


@dataclass
class ValidationReport:
    """Full validation report across all candidates."""
    total_candidates: int = 0
    candidates_validated: int = 0
    candidates_passed: int = 0
    candidates_failed: int = 0

    survivors: list[CandidateValidationResult] = field(default_factory=list)
    failures: list[CandidateValidationResult] = field(default_factory=list)

    conclusion: str = ""
    confidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_candidates": self.total_candidates,
            "candidates_validated": self.candidates_validated,
            "candidates_passed": self.candidates_passed,
            "candidates_failed": self.candidates_failed,
            "survivors": [s.to_dict() for s in self.survivors],
            "failures": [f.to_dict() for f in self.failures[:20]],
            "conclusion": self.conclusion,
            "confidence": self.confidence,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# CONDITION MATCHING
# ═══════════════════════════════════════════════════════════════════════════════

def _matches_conditions(record: EdgeAttributionRecord, conditions: dict[str, str]) -> bool:
    """Check if a record matches all candidate conditions."""
    for field_name, required_value in conditions.items():
        actual = getattr(record, field_name, None)
        if actual is None or str(actual) != str(required_value):
            return False
    return True


# ═══════════════════════════════════════════════════════════════════════════════
# SPLIT EVALUATION
# ═══════════════════════════════════════════════════════════════════════════════

def _evaluate_candidate_split(
    candidate: EdgeCandidate,
    test_records: list[EdgeAttributionRecord],
    split_idx: int,
    train_size: int,
) -> CandidateSplitResult:
    """Evaluate one candidate on one test split."""
    sr = CandidateSplitResult(split=split_idx, train_size=train_size, test_size=len(test_records))

    matching = [r for r in test_records if _matches_conditions(r, candidate.conditions)]
    rs = [r.result_r for r in matching]

    sr.trades_taken = len(rs)
    if not rs:
        return sr

    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    sr.wins = len(wins)
    sr.losses = len(losses)
    sr.win_rate = len(wins) / len(rs)
    sr.avg_r = sum(rs) / len(rs)
    sr.total_r = sum(rs)

    gw = sum(wins)
    gl = abs(sum(losses))
    sr.profit_factor = gw / gl if gl > 0 else (float("inf") if gw > 0 else 0.0)

    # Drawdown
    cum = 0.0
    peak = 0.0
    worst = 0.0
    for r in rs:
        cum += r
        peak = max(peak, cum)
        worst = max(worst, peak - cum)
    sr.max_drawdown = worst

    return sr


# ═══════════════════════════════════════════════════════════════════════════════
# SURVIVAL CRITERIA
# ═══════════════════════════════════════════════════════════════════════════════

_MIN_POSITIVE_RATE = 0.5       # Majority of splits must be positive
_MIN_TOTAL_TRADES = 15         # Across all splits
_MAX_SINGLE_SPLIT_SHARE = 0.7  # No single split can contribute >70% of total R
_MAX_SYMBOL_CONCENTRATION = 0.8
_MAX_SESSION_CONCENTRATION = 0.8


def _assess_survival(
    vr: CandidateValidationResult,
    records_matched: list[EdgeAttributionRecord],
) -> None:
    """Apply pass/fail criteria to a validated candidate."""
    fails = []

    if vr.positive_rate < _MIN_POSITIVE_RATE:
        fails.append(f"positive_rate={vr.positive_rate:.0%} < {_MIN_POSITIVE_RATE:.0%}")

    if vr.total_trades < _MIN_TOTAL_TRADES:
        fails.append(f"total_trades={vr.total_trades} < {_MIN_TOTAL_TRADES}")

    if vr.total_r <= 0:
        fails.append(f"total_r={vr.total_r:.2f} <= 0")

    # Single split dominance
    if vr.splits and vr.total_r > 0:
        max_split_r = max(s.total_r for s in vr.splits)
        if max_split_r / vr.total_r > _MAX_SINGLE_SPLIT_SHARE:
            fails.append(f"single_split_dominance={max_split_r/vr.total_r:.0%}")

    # Symbol concentration
    if records_matched:
        sym_counts = defaultdict(int)
        for r in records_matched:
            sym_counts[r.symbol] += 1
        top_sym_frac = max(sym_counts.values()) / len(records_matched)
        vr.symbol_concentration = top_sym_frac
        if top_sym_frac > _MAX_SYMBOL_CONCENTRATION and "symbol" not in vr.conditions:
            fails.append(f"symbol_concentration={top_sym_frac:.0%}")

    vr.fail_reasons = fails
    vr.passes = len(fails) == 0

    # Confidence
    if vr.total_trades >= 50 and vr.splits_total >= 4:
        vr.confidence = "HIGH"
    elif vr.total_trades >= 20 and vr.splits_total >= 3:
        vr.confidence = "MEDIUM"
    else:
        vr.confidence = "LOW"


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

def validate_candidates(
    candidates: list[EdgeCandidate],
    records: list[EdgeAttributionRecord],
    n_splits: int = 5,
    min_train_pct: float = 0.3,
) -> ValidationReport:
    """
    Run walk-forward validation for all edge candidates.

    Args:
        candidates: Edge candidates to validate.
        records: Full chronological attribution records (sorted by timestamp).
        n_splits: Number of test periods.
        min_train_pct: Minimum fraction of data for first training period.
    """
    report = ValidationReport(total_candidates=len(candidates))

    if not records or not candidates:
        report.conclusion = "No data or candidates to validate."
        report.confidence = "INSUFFICIENT"
        return report

    # Sort records chronologically
    records_sorted = sorted(records, key=lambda r: r.timestamp_utc)
    n = len(records_sorted)
    min_train = int(n * min_train_pct)
    test_size = (n - min_train) // n_splits if n_splits > 0 else n

    if test_size < 5:
        report.conclusion = "Insufficient data for walk-forward splits."
        report.confidence = "INSUFFICIENT"
        return report

    # Validate each candidate
    for candidate in candidates:
        vr = CandidateValidationResult(candidate_id=candidate.candidate_id, conditions=candidate.conditions)
        all_matched: list[EdgeAttributionRecord] = []

        for i in range(n_splits):
            train_end = min_train + i * test_size
            test_end = min(train_end + test_size, n)
            test_records = records_sorted[train_end:test_end]

            if not test_records:
                continue

            sr = _evaluate_candidate_split(candidate, test_records, i + 1, train_end)
            vr.splits.append(sr)

            # Collect matched records for dependency analysis
            matched_in_split = [r for r in test_records if _matches_conditions(r, candidate.conditions)]
            all_matched.extend(matched_in_split)

        # Aggregate
        vr.splits_total = len(vr.splits)
        vr.splits_positive = sum(1 for s in vr.splits if s.total_r > 0)
        vr.positive_rate = vr.splits_positive / vr.splits_total if vr.splits_total > 0 else 0.0
        vr.total_trades = sum(s.trades_taken for s in vr.splits)
        vr.total_r = sum(s.total_r for s in vr.splits)

        trade_splits = [s for s in vr.splits if s.trades_taken > 0]
        if trade_splits:
            vr.avg_ev = sum(s.avg_r for s in trade_splits) / len(trade_splits)
            vr.avg_win_rate = sum(s.win_rate for s in trade_splits) / len(trade_splits)
            vr.worst_split_r = min(s.total_r for s in vr.splits)
            vr.best_split_r = max(s.total_r for s in vr.splits)
            vr.max_drawdown = max((s.max_drawdown for s in vr.splits), default=0.0)

        # Assess survival
        _assess_survival(vr, all_matched)
        report.candidates_validated += 1

        if vr.passes:
            report.survivors.append(vr)
        else:
            report.failures.append(vr)

    # Sort survivors by total_r
    report.survivors.sort(key=lambda v: v.total_r, reverse=True)
    report.failures.sort(key=lambda v: v.total_r, reverse=True)
    report.candidates_passed = len(report.survivors)
    report.candidates_failed = len(report.failures)

    # Confidence
    if len(records) >= 500 and n_splits >= 4:
        report.confidence = "HIGH"
    elif len(records) >= 100:
        report.confidence = "MEDIUM"
    else:
        report.confidence = "LOW"

    # Conclusion
    if report.survivors:
        top = report.survivors[0]
        report.conclusion = (
            f"{report.candidates_passed}/{report.candidates_validated} candidates survived walk-forward. "
            f"Top: {top.candidate_id} ({top.splits_positive}/{top.splits_total} positive, "
            f"total={top.total_r:+.1f}R, trades={top.total_trades}, confidence={top.confidence})"
        )
    else:
        report.conclusion = (
            f"0/{report.candidates_validated} candidates survived walk-forward validation. "
            f"No edge hypothesis generalises to unseen data in this dataset."
        )

    logger.info("[CANDIDATE_VALIDATION] validated=%d passed=%d failed=%d",
                report.candidates_validated, report.candidates_passed, report.candidates_failed)
    return report
