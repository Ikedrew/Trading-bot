"""
Probability Model — CQ4: Can probability be estimated from context?

Estimates outcome probability from V2Opportunity features using
historical example matching (k-nearest-neighbor style) and
frequency-based calibration.

Approach:
    1. Encode V2Opportunity features into a comparable vector
    2. Find historical records with similar feature profiles
    3. Compute empirical probability from matched cohort
    4. Calibrate predictions against actual outcomes
    5. Report feature importance by leave-one-out degradation

Does NOT use black-box ML. Uses interpretable, auditable methods:
    - Exact categorical matching with fallback relaxation
    - Binned continuous features
    - Frequency-based probability (not regression weights)

Safety:
    - Never modifies trades or execution
    - Never promotes findings automatically
    - Minimum cohort size enforced
    - Train/test calibration required
    - Pure statistical analysis on linked observations
"""

from __future__ import annotations

import math
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

MIN_COHORT_SIZE = 20
VALIDATION_SPLIT = 0.3
_Z_95 = 1.96
DEFAULT_SPREAD_COST_R = 0.48

# Features used for probability estimation (ordered by expected importance)
PROBABILITY_FEATURES = [
    # Categorical
    "h4_regime",
    "h1_bias",
    "h1_bos_confirmed",
    "session",
    "near_support",
    "near_resistance",
    "order_block_present",
    "proposed_direction",
    # Continuous (will be binned)
    "pattern_quality",
    "spread_atr_ratio",
    "risk_distance_pips",
    "volatility",
]

# Bin edges for continuous features
CONTINUOUS_BINS: dict[str, list[float]] = {
    "pattern_quality": [0.0, 0.4, 0.6, 0.8, 1.0],
    "spread_atr_ratio": [0.0, 0.2, 0.35, 0.5, 1.0],
    "risk_distance_pips": [0.0, 7.0, 11.0, 16.0, 50.0],
    "volatility": [0.0, 0.3, 0.5, 0.7, 1.0],
}


# ═══════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class ProbabilityEstimate:
    """Probability estimate for a single record."""
    opportunity_id: str
    predicted_win_prob: float
    cohort_size: int
    cohort_ev: float
    actual_outcome: float | None = None
    actual_win: bool | None = None
    features_matched: int = 0
    confidence: str = ""  # HIGH / MEDIUM / LOW


@dataclass
class CalibrationBucket:
    """One calibration bucket (predicted vs actual)."""
    predicted_range: str  # e.g. "0.4-0.5"
    predicted_mean: float
    actual_win_rate: float
    sample_size: int
    calibration_error: float  # |predicted - actual|


@dataclass
class FeatureImportance:
    """Importance of one feature measured by degradation."""
    feature_name: str
    baseline_accuracy: float
    without_feature_accuracy: float
    importance_score: float  # degradation when removed


@dataclass
class ProbabilityAnalysis:
    """Full probability model analysis."""
    total_records: int
    train_size: int
    test_size: int
    # Baseline
    baseline_win_rate: float
    # Model performance
    model_accuracy: float  # % correct predictions on test set
    model_brier_score: float  # calibration metric (lower = better)
    # Calibration
    calibration_buckets: list[CalibrationBucket] = field(default_factory=list)
    mean_calibration_error: float = 0.0
    # Feature importance
    feature_importance: list[FeatureImportance] = field(default_factory=list)
    # Sample predictions
    sample_estimates: list[ProbabilityEstimate] = field(default_factory=list)
    # Conclusion
    model_useful: bool = False  # better than random baseline?


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════


def build_probability_model(
    records: list[dict[str, Any]],
    *,
    min_cohort: int = MIN_COHORT_SIZE,
    spread_cost_r: float = DEFAULT_SPREAD_COST_R,
    validation_split: float = VALIDATION_SPLIT,
    features: list[str] | None = None,
) -> ProbabilityAnalysis:
    """
    Build and evaluate a probability estimation model.

    Uses frequency-based matching: for each test record, find training
    records with matching features and compute empirical win probability.

    Args:
        records: Linked V2Opportunity dicts
        min_cohort: Minimum matching records for a prediction
        spread_cost_r: Spread cost in R
        validation_split: Test set fraction
        features: Override feature list

    Returns:
        ProbabilityAnalysis with calibration and importance metrics.
    """
    linked = [r for r in records if _get_outcome(r) is not None]
    if len(linked) < min_cohort * 3:
        return ProbabilityAnalysis(
            total_records=len(linked), train_size=0, test_size=0,
            baseline_win_rate=0.0, model_accuracy=0.0, model_brier_score=1.0)

    # Split train/test (chronological by timestamp)
    linked.sort(key=lambda r: float(r.get("timestamp_utc", 0)))
    split_idx = int(len(linked) * (1 - validation_split))
    train = linked[:split_idx]
    test = linked[split_idx:]

    feat_list = features or PROBABILITY_FEATURES

    # Baseline
    train_outcomes = [_get_outcome(r) for r in train]
    baseline_wr = sum(1 for o in train_outcomes if o > 0) / len(train_outcomes)

    # Encode training set
    train_encoded = [_encode_record(r, feat_list) for r in train]
    train_wins = [_get_outcome(r) > 0 for r in train]

    # Predict on test set
    predictions: list[ProbabilityEstimate] = []
    correct = 0
    brier_sum = 0.0

    for rec in test:
        enc = _encode_record(rec, feat_list)
        actual = _get_outcome(rec)
        actual_win = actual > 0

        # Find matching cohort with progressive relaxation
        pred = _predict_probability(
            enc, train_encoded, train_wins, train, feat_list, min_cohort, rec
        )
        pred.actual_outcome = actual
        pred.actual_win = actual_win

        predictions.append(pred)

        # Accuracy (threshold at 0.5)
        predicted_win = pred.predicted_win_prob > 0.5
        if predicted_win == actual_win:
            correct += 1

        # Brier score
        target = 1.0 if actual_win else 0.0
        brier_sum += (pred.predicted_win_prob - target) ** 2

    test_size = len(test)
    accuracy = correct / test_size if test_size > 0 else 0.0
    brier = brier_sum / test_size if test_size > 0 else 1.0

    # Calibration
    calibration = _compute_calibration(predictions)
    mean_cal_error = (
        sum(b.calibration_error for b in calibration) / len(calibration)
        if calibration else 0.0
    )

    # Feature importance (leave-one-out)
    importance = _compute_feature_importance(
        train_encoded, train_wins, test, feat_list, min_cohort, accuracy
    )

    # Is model useful?
    baseline_accuracy = max(baseline_wr, 1 - baseline_wr)  # always-predict-majority
    model_useful = accuracy > baseline_accuracy + 0.02  # must beat baseline by 2%

    analysis = ProbabilityAnalysis(
        total_records=len(linked),
        train_size=len(train),
        test_size=test_size,
        baseline_win_rate=round(baseline_wr, 4),
        model_accuracy=round(accuracy, 4),
        model_brier_score=round(brier, 4),
        calibration_buckets=calibration,
        mean_calibration_error=round(mean_cal_error, 4),
        feature_importance=importance,
        sample_estimates=predictions[:20],  # Cap at 20 samples for reporting
        model_useful=model_useful,
    )

    return analysis


def estimate_probability(
    target: dict[str, Any],
    training_records: list[dict[str, Any]],
    *,
    min_cohort: int = MIN_COHORT_SIZE,
    features: list[str] | None = None,
) -> ProbabilityEstimate:
    """
    Estimate win probability for a single new opportunity.

    Args:
        target: V2Opportunity dict to estimate
        training_records: Historical linked records
        min_cohort: Minimum matching records
        features: Override feature list

    Returns:
        ProbabilityEstimate with predicted probability and cohort info.
    """
    feat_list = features or PROBABILITY_FEATURES
    linked = [r for r in training_records if _get_outcome(r) is not None]

    train_encoded = [_encode_record(r, feat_list) for r in linked]
    train_wins = [_get_outcome(r) > 0 for r in linked]

    enc = _encode_record(target, feat_list)
    return _predict_probability(
        enc, train_encoded, train_wins, linked, feat_list, min_cohort, target
    )


# ═══════════════════════════════════════════════════════════════════════════════
# INTERNAL
# ═══════════════════════════════════════════════════════════════════════════════


def _encode_record(rec: dict, features: list[str]) -> dict[str, str]:
    """Encode a record's features into comparable categorical values."""
    encoded: dict[str, str] = {}
    for feat in features:
        val = rec.get(feat)
        if feat in CONTINUOUS_BINS:
            encoded[feat] = _bin_value(feat, val)
        else:
            encoded[feat] = str(val) if val is not None else ""
    return encoded


def _bin_value(feature: str, value: Any) -> str:
    """Bin a continuous value into a category."""
    if value is None:
        return "MISSING"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "MISSING"

    edges = CONTINUOUS_BINS.get(feature, [])
    for i in range(len(edges) - 1):
        if v < edges[i + 1]:
            return f"BIN_{i}"
    return f"BIN_{len(edges) - 1}"


def _predict_probability(
    target_enc: dict[str, str],
    train_encoded: list[dict[str, str]],
    train_wins: list[bool],
    train_records: list[dict],
    features: list[str],
    min_cohort: int,
    target_rec: dict,
) -> ProbabilityEstimate:
    """
    Predict probability using progressive feature relaxation.

    Starts with all features, removes least-important features
    until cohort size is sufficient.
    """
    opp_id = target_rec.get("opportunity_id", "unknown")

    # Try full match first, then progressively relax
    for n_features in range(len(features), 0, -1):
        active_features = features[:n_features]
        matches = _find_matches(target_enc, train_encoded, active_features)

        if len(matches) >= min_cohort:
            # Compute probability from matching cohort
            cohort_wins = [train_wins[i] for i in matches]
            cohort_outcomes = [_get_outcome(train_records[i]) for i in matches]

            win_prob = sum(cohort_wins) / len(cohort_wins)
            cohort_ev = sum(o for o in cohort_outcomes if o is not None) / len(cohort_outcomes)

            confidence = (
                "HIGH" if n_features >= len(features) - 2 and len(matches) >= min_cohort * 2
                else "MEDIUM" if n_features >= len(features) // 2
                else "LOW"
            )

            return ProbabilityEstimate(
                opportunity_id=opp_id,
                predicted_win_prob=round(win_prob, 4),
                cohort_size=len(matches),
                cohort_ev=round(cohort_ev, 4),
                features_matched=n_features,
                confidence=confidence,
            )

    # Fallback: use entire training set (baseline)
    overall_wr = sum(train_wins) / len(train_wins) if train_wins else 0.5
    return ProbabilityEstimate(
        opportunity_id=opp_id,
        predicted_win_prob=round(overall_wr, 4),
        cohort_size=len(train_wins),
        cohort_ev=0.0,
        features_matched=0,
        confidence="LOW",
    )


def _find_matches(
    target: dict[str, str],
    candidates: list[dict[str, str]],
    features: list[str],
) -> list[int]:
    """Find indices of candidates matching target on specified features."""
    matches = []
    for i, cand in enumerate(candidates):
        match = True
        for feat in features:
            if target.get(feat, "") != cand.get(feat, ""):
                match = False
                break
        if match:
            matches.append(i)
    return matches


def _compute_calibration(
    predictions: list[ProbabilityEstimate],
) -> list[CalibrationBucket]:
    """Compute calibration buckets (predicted vs actual)."""
    # Bucket into 5 bins: 0-0.2, 0.2-0.4, 0.4-0.6, 0.6-0.8, 0.8-1.0
    bins = [
        ("0.0-0.2", 0.0, 0.2),
        ("0.2-0.4", 0.2, 0.4),
        ("0.4-0.6", 0.4, 0.6),
        ("0.6-0.8", 0.6, 0.8),
        ("0.8-1.0", 0.8, 1.01),
    ]

    buckets: list[CalibrationBucket] = []
    for label, lo, hi in bins:
        in_bin = [p for p in predictions if lo <= p.predicted_win_prob < hi]
        if not in_bin:
            continue

        pred_mean = sum(p.predicted_win_prob for p in in_bin) / len(in_bin)
        actual_wins = sum(1 for p in in_bin if p.actual_win)
        actual_wr = actual_wins / len(in_bin)
        cal_error = abs(pred_mean - actual_wr)

        buckets.append(CalibrationBucket(
            predicted_range=label,
            predicted_mean=round(pred_mean, 4),
            actual_win_rate=round(actual_wr, 4),
            sample_size=len(in_bin),
            calibration_error=round(cal_error, 4),
        ))

    return buckets


def _compute_feature_importance(
    train_encoded: list[dict[str, str]],
    train_wins: list[bool],
    test: list[dict],
    features: list[str],
    min_cohort: int,
    baseline_accuracy: float,
) -> list[FeatureImportance]:
    """Compute feature importance via leave-one-out degradation."""
    importance: list[FeatureImportance] = []

    for feat_to_remove in features:
        reduced_features = [f for f in features if f != feat_to_remove]

        # Re-predict test set without this feature
        correct = 0
        for rec in test:
            enc = _encode_record(rec, reduced_features)
            # Reduced encoding for training
            reduced_train = [
                {k: v for k, v in te.items() if k != feat_to_remove}
                for te in train_encoded
            ]
            matches = _find_matches(enc, reduced_train, reduced_features)

            if len(matches) >= min_cohort:
                cohort_wins = [train_wins[i] for i in matches]
                pred_win = sum(cohort_wins) / len(cohort_wins) > 0.5
            else:
                # Fallback to baseline
                pred_win = sum(train_wins) / len(train_wins) > 0.5

            actual_win = _get_outcome(rec) > 0
            if pred_win == actual_win:
                correct += 1

        reduced_accuracy = correct / len(test) if test else 0.0
        degradation = baseline_accuracy - reduced_accuracy

        importance.append(FeatureImportance(
            feature_name=feat_to_remove,
            baseline_accuracy=round(baseline_accuracy, 4),
            without_feature_accuracy=round(reduced_accuracy, 4),
            importance_score=round(degradation, 4),
        ))

    # Sort by importance descending
    importance.sort(key=lambda f: f.importance_score, reverse=True)
    return importance


def _get_outcome(rec: dict) -> float | None:
    """Get outcome R from record."""
    raw_r = rec.get("outcome_raw_r")
    if raw_r is not None:
        try:
            return float(raw_r)
        except (TypeError, ValueError):
            pass
    linkage = rec.get("_linkage", {})
    result_r = linkage.get("result_r")
    if result_r is not None:
        try:
            return float(result_r)
        except (TypeError, ValueError):
            pass
    return None
