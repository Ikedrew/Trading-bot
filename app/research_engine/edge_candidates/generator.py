"""
Edge Candidate Generator — Converts attribution findings into structured hypotheses.

Reads edge attribution results and produces ranked, scored candidates
suitable for walk-forward validation.
"""

from __future__ import annotations

import hashlib
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from research_engine.edge_attribution.models import (
    EdgeAttributionRecord,
    _compute_stats,
    _MIN_SAMPLES_LOW,
)
from research_engine.edge_candidates.models import EdgeCandidate
from research_engine.edge_candidates.scoring import score_candidate

logger = logging.getLogger(__name__)

_MIN_SAMPLE_GENERATE = 20  # Minimum n to generate a candidate
_MIN_EV_GENERATE = 0.01    # Minimum EV to consider

# Combinations to test (2D and 3D)
_COMBINATIONS_2D = [
    ("pattern", "session"),
    ("pattern", "regime"),
    ("pattern", "htf_alignment_bin"),
    ("pattern", "bias_alignment_bin"),
    ("pattern", "trend_alignment_bin"),
    ("pattern", "symbol"),
    ("session", "regime"),
    ("session", "bias_alignment_bin"),
]

_COMBINATIONS_3D = [
    ("pattern", "regime", "session"),
    ("pattern", "regime", "htf_alignment_bin"),
    ("pattern", "session", "bias_alignment_bin"),
]


@dataclass
class CandidateGenerationResult:
    """Output of candidate generation."""
    total_records: int = 0
    combinations_tested: int = 0
    candidates_generated: int = 0
    candidates_accepted: int = 0
    candidates_rejected: int = 0

    accepted: list[EdgeCandidate] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)

    conclusion: str = ""
    confidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_records": self.total_records,
            "combinations_tested": self.combinations_tested,
            "candidates_generated": self.candidates_generated,
            "candidates_accepted": self.candidates_accepted,
            "candidates_rejected": self.candidates_rejected,
            "accepted": [c.to_dict() for c in self.accepted],
            "rejected": self.rejected[:20],
            "conclusion": self.conclusion,
            "confidence": self.confidence,
        }


def _make_candidate_id(conditions: dict[str, str]) -> str:
    """Generate a deterministic candidate ID from conditions."""
    parts = sorted(f"{k}={v}" for k, v in conditions.items())
    raw = "|".join(parts)
    short_hash = hashlib.md5(raw.encode()).hexdigest()[:6].upper()
    label = "_".join(v for _, v in sorted(conditions.items()))[:40]
    return f"EC-{label}-{short_hash}"


def _build_hypothesis(conditions: dict[str, str]) -> str:
    """Generate a natural-language hypothesis from conditions."""
    parts = [f"{k}={v}" for k, v in sorted(conditions.items())]
    return f"Positive expectancy when: {', '.join(parts)}"


def _detect_dependencies(
    records: list[EdgeAttributionRecord],
    matching: list[EdgeAttributionRecord],
    conditions: dict[str, str],
) -> tuple[bool, bool, bool]:
    """Detect if the candidate is dependent on a single pattern/symbol/regime."""
    if not matching:
        return False, False, False

    patterns = set(r.pattern for r in matching)
    symbols = set(r.symbol for r in matching)
    regimes = set(r.regime for r in matching)

    single_pattern = len(patterns) == 1 and "pattern" not in conditions
    single_symbol = len(symbols) == 1 and "symbol" not in conditions
    single_regime = len(regimes) == 1 and "regime" not in conditions

    return single_pattern, single_symbol, single_regime


def generate_candidates(records: list[EdgeAttributionRecord]) -> CandidateGenerationResult:
    """
    Generate edge candidates from attribution records.

    Tests 2D and 3D feature combinations, scores them, and produces
    ranked candidates suitable for walk-forward validation.
    """
    result = CandidateGenerationResult(total_records=len(records))

    if len(records) < _MIN_SAMPLE_GENERATE:
        result.conclusion = "Insufficient data for candidate generation."
        result.confidence = "INSUFFICIENT"
        return result

    seen_ids: set[str] = set()
    all_candidates: list[EdgeCandidate] = []
    rejected: list[dict[str, Any]] = []
    combos_tested = 0

    # ─── SINGLE FEATURE CANDIDATES ────────────────────────────────────
    single_features = ["pattern", "session", "regime", "htf_alignment_bin", "bias_alignment_bin"]
    for feature in single_features:
        groups: dict[str, list[EdgeAttributionRecord]] = defaultdict(list)
        for r in records:
            groups[getattr(r, feature, "?")].append(r)

        for val, recs in groups.items():
            combos_tested += 1
            if len(recs) < _MIN_SAMPLE_GENERATE:
                continue
            rs = [r.result_r for r in recs]
            stats = _compute_stats(rs)
            if stats["ev"] <= _MIN_EV_GENERATE:
                rejected.append({"conditions": {feature: val}, "reason": "negative_ev", "ev": stats["ev"], "n": stats["n"]})
                continue

            conditions = {feature: val}
            cid = _make_candidate_id(conditions)
            if cid in seen_ids:
                continue
            seen_ids.add(cid)

            sp, ss, sr = _detect_dependencies(records, recs, conditions)
            c = EdgeCandidate(
                candidate_id=cid,
                hypothesis=_build_hypothesis(conditions),
                conditions=conditions,
                sample_size=stats["n"],
                win_rate=stats["wr"],
                expectancy=stats["ev"],
                profit_factor=stats["pf"],
                total_r=stats["total_r"],
                single_pattern_dependent=sp,
                single_symbol_dependent=ss,
                single_regime_dependent=sr,
            )
            all_candidates.append(score_candidate(c))

    # ─── 2D COMBINATIONS ──────────────────────────────────────────────
    for feat_a, feat_b in _COMBINATIONS_2D:
        groups: dict[tuple[str, str], list[EdgeAttributionRecord]] = defaultdict(list)
        for r in records:
            key = (getattr(r, feat_a, "?"), getattr(r, feat_b, "?"))
            groups[key].append(r)

        for (val_a, val_b), recs in groups.items():
            combos_tested += 1
            if len(recs) < _MIN_SAMPLE_GENERATE:
                continue
            rs = [r.result_r for r in recs]
            stats = _compute_stats(rs)
            if stats["ev"] <= _MIN_EV_GENERATE:
                continue

            conditions = {feat_a: val_a, feat_b: val_b}
            cid = _make_candidate_id(conditions)
            if cid in seen_ids:
                continue
            seen_ids.add(cid)

            sp, ss, sr = _detect_dependencies(records, recs, conditions)
            c = EdgeCandidate(
                candidate_id=cid,
                hypothesis=_build_hypothesis(conditions),
                conditions=conditions,
                sample_size=stats["n"],
                win_rate=stats["wr"],
                expectancy=stats["ev"],
                profit_factor=stats["pf"],
                total_r=stats["total_r"],
                single_pattern_dependent=sp,
                single_symbol_dependent=ss,
                single_regime_dependent=sr,
            )
            all_candidates.append(score_candidate(c))

    # ─── 3D COMBINATIONS ──────────────────────────────────────────────
    for feat_a, feat_b, feat_c in _COMBINATIONS_3D:
        groups: dict[tuple[str, str, str], list[EdgeAttributionRecord]] = defaultdict(list)
        for r in records:
            key = (getattr(r, feat_a, "?"), getattr(r, feat_b, "?"), getattr(r, feat_c, "?"))
            groups[key].append(r)

        for (va, vb, vc), recs in groups.items():
            combos_tested += 1
            if len(recs) < _MIN_SAMPLE_GENERATE:
                continue
            rs = [r.result_r for r in recs]
            stats = _compute_stats(rs)
            if stats["ev"] <= _MIN_EV_GENERATE:
                continue

            conditions = {feat_a: va, feat_b: vb, feat_c: vc}
            cid = _make_candidate_id(conditions)
            if cid in seen_ids:
                continue
            seen_ids.add(cid)

            sp, ss, sr = _detect_dependencies(records, recs, conditions)
            c = EdgeCandidate(
                candidate_id=cid,
                hypothesis=_build_hypothesis(conditions),
                conditions=conditions,
                sample_size=stats["n"],
                win_rate=stats["wr"],
                expectancy=stats["ev"],
                profit_factor=stats["pf"],
                total_r=stats["total_r"],
                single_pattern_dependent=sp,
                single_symbol_dependent=ss,
                single_regime_dependent=sr,
            )
            all_candidates.append(score_candidate(c))

    # ─── RANK AND FILTER ──────────────────────────────────────────────
    result.combinations_tested = combos_tested
    result.candidates_generated = len(all_candidates)

    # Accept: sample >= 30 AND positive expectancy AND positive total_r
    accepted = [c for c in all_candidates if c.sample_size >= 30 and c.expectancy > 0 and c.total_r > 0]
    rejected_candidates = [c for c in all_candidates if c not in accepted]

    for c in rejected_candidates:
        reasons = []
        if c.sample_size < 30:
            reasons.append("sample_below_30")
        if c.expectancy <= 0:
            reasons.append("non_positive_ev")
        if c.total_r <= 0:
            reasons.append("non_positive_total_r")
        rejected.append({"candidate_id": c.candidate_id, "conditions": c.conditions, "reasons": reasons, "ev": c.expectancy, "n": c.sample_size})

    # Sort accepted by confidence score
    accepted.sort(key=lambda c: c.confidence_score, reverse=True)

    result.accepted = accepted
    result.rejected = rejected[:30]
    result.candidates_accepted = len(accepted)
    result.candidates_rejected = len(rejected)

    # Confidence
    if result.total_records >= 500 and result.candidates_accepted >= 3:
        result.confidence = "HIGH"
    elif result.total_records >= 100:
        result.confidence = "MEDIUM"
    else:
        result.confidence = "LOW"

    # Conclusion
    if accepted:
        top = accepted[0]
        result.conclusion = (
            f"Generated {result.candidates_accepted} candidates from {combos_tested} combinations. "
            f"Top: {top.candidate_id} (EV={top.expectancy:+.3f}R, n={top.sample_size}, score={top.confidence_score:.0f}/100, overfit={top.overfit_risk})"
        )
    else:
        result.conclusion = f"No candidates met acceptance criteria (tested {combos_tested} combinations)."

    logger.info("[EDGE_CANDIDATES] tested=%d generated=%d accepted=%d", combos_tested, len(all_candidates), len(accepted))
    return result
