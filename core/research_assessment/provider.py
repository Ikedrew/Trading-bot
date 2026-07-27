"""
Research Assessment Provider — Candidate lookup and assessment generation.

Given a production decision context (pattern, regime, session, components),
determines whether it matches any walk-forward-validated edge candidate
and returns the empirical evidence.

This module:
    - Loads validated candidates from research reports (once, cached)
    - Matches decision context against candidate conditions
    - Returns ResearchAssessment (informational only)
    - NEVER imports from execution, risk, or order placement modules

Thread-safe. Lazy-loaded. Failure-safe (returns neutral on any error).
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from core.research_assessment.models import ResearchAssessment, NEUTRAL_ASSESSMENT

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# VALIDATED CANDIDATE STORE (lazy-loaded, cached)
# ═══════════════════════════════════════════════════════════════════════════════

_lock = threading.Lock()
_candidates_loaded = False
_validated_candidates: list[dict[str, Any]] = []


def _bin_value(val: float, thresholds: tuple[float, float] = (0.33, 0.66)) -> str:
    """Bin a 0-1 value into HIGH/MEDIUM/LOW."""
    if val >= thresholds[1]:
        return "HIGH"
    elif val >= thresholds[0]:
        return "MEDIUM"
    return "LOW"


def _infer_session(timestamp_utc: str) -> str:
    """Infer session from timestamp."""
    if not timestamp_utc or len(timestamp_utc) < 13:
        return "UNKNOWN"
    try:
        hour = int(timestamp_utc[11:13])
        if 0 <= hour < 7:
            return "ASIAN"
        elif 7 <= hour < 12:
            return "LONDON"
        elif 12 <= hour < 14:
            return "OVERLAP"
        elif 14 <= hour < 21:
            return "NY"
        else:
            return "OFF_SESSION"
    except ValueError:
        return "UNKNOWN"


def _load_candidates() -> None:
    """Load validated candidates from the latest validation report."""
    global _candidates_loaded, _validated_candidates

    with _lock:
        if _candidates_loaded:
            return

        _candidates_loaded = True

        # Look for validation reports
        reports_dir = Path("research_reports")
        if not reports_dir.exists():
            return

        # Find latest candidate validation report
        validation_files = sorted(reports_dir.glob("edge_validation_*.json"), reverse=True)
        if not validation_files:
            return

        try:
            with open(validation_files[0], "r", encoding="utf-8") as f:
                report = json.load(f)

            survivors = report.get("metrics", {}).get("survivors", [])
            for s in survivors:
                if s.get("passes"):
                    _validated_candidates.append(s)

            if _validated_candidates:
                logger.info(
                    "[RESEARCH_ASSESSMENT] loaded %d validated candidates from %s",
                    len(_validated_candidates), validation_files[0].name,
                )
        except Exception as exc:
            logger.debug("[RESEARCH_ASSESSMENT] load_failed: %s", exc)


def _match_conditions(
    conditions: dict[str, str],
    context: dict[str, str],
) -> bool:
    """Check if all candidate conditions are satisfied by the decision context."""
    for field_name, required_value in conditions.items():
        actual = context.get(field_name, "")
        if str(actual) != str(required_value):
            return False
    return True


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def get_research_assessment(
    *,
    pattern_name: str = "",
    regime: str = "",
    market_state: str = "",
    symbol: str = "",
    timestamp_utc: str = "",
    components: dict[str, float] | None = None,
) -> ResearchAssessment:
    """
    Look up whether the current decision matches a validated research candidate.

    Args:
        pattern_name: Pattern detected (e.g., "TWEEZER_TOP")
        regime: Current regime classification (e.g., "TRANSITIONAL")
        market_state: Market state (e.g., "TRANSITIONAL")
        symbol: Trading symbol (e.g., "EURUSD")
        timestamp_utc: Decision timestamp (for session inference)
        components: Component scores dict (for binning)

    Returns:
        ResearchAssessment with empirical data if match found,
        NEUTRAL_ASSESSMENT otherwise.

    Never raises. Returns NEUTRAL_ASSESSMENT on any error.
    """
    try:
        _load_candidates()

        if not _validated_candidates:
            return NEUTRAL_ASSESSMENT

        # Build decision context for matching
        session = _infer_session(timestamp_utc)
        comps = components or {}

        context = {
            "pattern": pattern_name,
            "regime": regime,
            "market_state": market_state,
            "symbol": symbol,
            "session": session,
            "htf_alignment_bin": _bin_value(comps.get("htf_alignment", 0.0)),
            "trend_alignment_bin": _bin_value(comps.get("trend_alignment", 0.0)),
            "bias_alignment_bin": _bin_value(comps.get("bias_alignment", 0.0)),
        }

        # Find best matching candidate (most specific first)
        best_match: dict[str, Any] | None = None
        best_specificity = 0

        for candidate in _validated_candidates:
            conditions = candidate.get("conditions", {})
            if not conditions:
                continue
            if _match_conditions(conditions, context):
                specificity = len(conditions)
                if specificity > best_specificity:
                    best_specificity = specificity
                    best_match = candidate

        if best_match is None:
            return NEUTRAL_ASSESSMENT

        # Build assessment from match
        splits_pos = best_match.get("splits_positive", 0)
        splits_tot = best_match.get("splits_total", 0)
        total_trades = best_match.get("total_trades", 0)

        # Confidence from candidate data
        if total_trades >= 50 and splits_pos >= 3:
            confidence = "HIGH"
        elif total_trades >= 20:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        return ResearchAssessment(
            candidate_match=True,
            candidate_id=best_match.get("candidate_id", ""),
            historical_win_rate=best_match.get("avg_win_rate", 0.0),
            empirical_ev=best_match.get("avg_ev", 0.0),
            sample_size=total_trades,
            walk_forward_survivor=True,
            walk_forward_positive_splits=splits_pos,
            walk_forward_total_splits=splits_tot,
            research_confidence=confidence,
            matched_conditions=best_match.get("conditions", {}),
            reasoning=f"Matches validated candidate {best_match.get('candidate_id', '')} ({splits_pos}/{splits_tot} positive splits, n={total_trades})",
        )

    except Exception as exc:
        logger.debug("[RESEARCH_ASSESSMENT] lookup_error: %s", exc)
        return NEUTRAL_ASSESSMENT


def reload_candidates() -> int:
    """Force reload of validated candidates (e.g., after new research run). Returns count loaded."""
    global _candidates_loaded, _validated_candidates
    with _lock:
        _candidates_loaded = False
        _validated_candidates = []
    _load_candidates()
    return len(_validated_candidates)
