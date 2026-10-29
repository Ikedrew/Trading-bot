"""Shared canonical evidence layer for Portfolio / Opportunity / Ranking research.

WAVE 6 — PORTFOLIO + OPPORTUNITY SELECTION (PORT-1, OPP-1) and Portfolio
Ranking (D6). This module is the single shared analysis layer for those three
research questions. It owns:

    - the forming-bar defect quarantine boundary (defensive guard; a research
      row whose decision/ranking timestamp falls inside the defect window is
      NOT treated as clean closed-bar strategy evidence);
    - canonical V1 outcome extraction and join helpers (portfolio_rankings ->
      decision_ledger -> trade_truth / shadow_runtime);
    - descriptive statistics + the canonical report builder.

It reads NO evidence from ``quarantine/.../logs/`` -- only the defect *manifest*
(metadata defining the boundary). All persistent evidence enters through the
canonical Research Engine data-access layer (research_engine.data_access).

This module is PURELY RESEARCH. It never writes production data and never
imports live trading / execution modules.
"""

from __future__ import annotations

import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from research_engine.experiments.experiment_base import (
    build_fingerprint,
    build_report,
    compute_confidence,
)

# ─────────────────────────────────────────────────────────────────────────────
# FORMING-BAR DEFECT QUARANTINE BOUNDARY
# ─────────────────────────────────────────────────────────────────────────────
# The defect is documented in quarantine/forming_bar_decision_defect/. The
# manifest is METADATA (the boundary definition), never active evidence.
# Research rows (ranking/decision) created within [first_affected, fix_committed]
# are forming-bar-contaminated and MUST NOT be counted as clean closed-bar
# strategy evidence. A fallback constant is kept so the guard still works if the
# manifest file is absent in a deployment.
_DEFECT_DIRNAME = "forming_bar_decision_defect"
_DEFECT_FIRST_AFFECTED = "2026-09-01T00:22:27Z"
_DEFECT_FIX_COMMITTED = "2026-09-07T15:28:07Z"

# Primary horizon simulation tag (shadow_runtime_v1 ingestion convention).
PRIMARY_HORIZON_TAG = "PRIMARY_HORIZON_SIMULATION"

# Minimum outcome-bearing rows for a selection question to report a real metric.
MIN_SAMPLE = 30
MIN_SELECTION_DELTA = 20

# Selection-status vocabulary persisted by the V1 portfolio ranker.
SELECTED = "SELECTED"
OUTRANKED = "OUTRANKED"
BLOCKED = "BLOCKED"
class QuarantineBoundary:
    """First-affected .. fix-committed boundary for the forming-bar defect."""

    __slots__ = ("first_affected", "fix_committed")

    def __init__(self, first_affected: str, fix_committed: str) -> None:
        self.first_affected = first_affected
        self.fix_committed = fix_committed

    def contains(self, iso_ts: str | float | int | None) -> bool:
        """True when *iso_ts* (or epoch seconds) falls inside the defect window."""
        if not iso_ts and iso_ts != 0:
            return False
        try:
            ts = _parse_ts(iso_ts)
        except (ValueError, TypeError):
            return False
        return _parse_ts(self.first_affected) <= ts <= _parse_ts(self.fix_committed)

    def describe(self) -> dict[str, str]:
        return {
            "defect": "forming_bar_decision_defect",
            "first_affected": self.first_affected,
            "fix_committed": self.fix_committed,
        }


def load_quarantine_boundary() -> QuarantineBoundary:
    """Build the defect boundary from the manifest (fallback to constants)."""
    try:
        manifest = (
            Path(__file__).resolve().parents[2]
            / "quarantine" / _DEFECT_DIRNAME / "manifest.json"
        )
        if manifest.exists():
            data = json.loads(manifest.read_text(encoding="utf-8"))
            b = data.get("defect_boundary", {})
            first = b.get("first_affected") or _DEFECT_FIRST_AFFECTED
            fix = b.get("fix_committed") or _DEFECT_FIX_COMMITTED
            return QuarantineBoundary(str(first), str(fix))
    except (json.JSONDecodeError, OSError, ValueError):
        pass
    return QuarantineBoundary(_DEFECT_FIRST_AFFECTED, _DEFECT_FIX_COMMITTED)


def _parse_ts(ts: str | float | int) -> float:
    """Parse an ISO-8601 / epoch-seconds value into an epoch float."""
    if isinstance(ts, (int, float)):
        return float(ts)
    text = str(ts).strip()
    if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
        return float(text)
    try:
        return float(text)
    except ValueError:
        pass
    normalized = text.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def deep_get(record: dict[str, Any], *keys: str) -> Any:
    """Resolve a nested key path; None if absent at any level."""
    cur: Any = record
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur
# ─────────────────────────────────────────────────────────────────────────────
# STATISTICS + REPORT
# ─────────────────────────────────────────────────────────────────────────────


def group_stats(rs: Iterable[Any]) -> dict[str, Any]:
    """Descriptive statistics for one group of outcome R values (None-aware)."""
    values = [r for r in rs if r is not None and math.isfinite(float(r))]
    if not values:
        return {"n": 0, "mean_r": None, "median_r": None, "win_rate": None,
                "total_r": None, "std_r": None}
    wins = [v for v in values if v > 0]
    out = {
        "n": len(values),
        "mean_r": round(statistics.mean(values), 4),
        "median_r": round(statistics.median(values), 4),
        "win_rate": round(len(wins) / len(values), 4),
        "total_r": round(sum(values), 3),
    }
    out["std_r"] = round(statistics.pstdev(values), 4) if len(values) > 1 else None
    return out


def numeric(record: dict[str, Any], *keys: str) -> float | None:
    """Extract a finite float for a nested key; None if missing/non-finite."""
    v = deep_get(record, *keys)
    if v is None:
        return None
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return None
    return fv if math.isfinite(fv) else None


def build_selection_report(
    *,
    question_id: str,
    status: str,
    overall: dict[str, Any],
    confidence: str,
    dataset: dict[str, Any],
    recommendation: str,
    source: str,
    records_used: int,
    records_excluded: int,
    assumptions: list[str] | None = None,
    warnings: list[str] | None = None,
    module: str,
) -> dict[str, Any]:
    """Canonical report for selection questions (Gap-4 contract)."""
    return build_report(
        question_id=question_id,
        status=status,
        overall=overall,
        confidence=confidence,
        dataset=dataset,
        fingerprint=build_fingerprint(
            records_used, records_excluded, source,
            validation_score=confidence, epoch="CURRENT",
        ),
        recommendation=recommendation,
        assumptions=assumptions or [],
        warnings=warnings or [],
        provenance={
            "experiment_module": module,
            "registry_id": question_id,
            "pipeline": "Question -> Experiment -> Dataset -> Output -> "
                        "Knowledge -> Command Centre",
        },
    )
# ─────────────────────────────────────────────────────────────────────────────
# CANONICAL INDEX + OUTCOME JOIN HELPERS
# ─────────────────────────────────────────────────────────────────────────────


def index_by(records: Iterable[dict[str, Any]],
             keyfn: Callable[[dict[str, Any]], Any]) -> dict[Any, list[dict[str, Any]]]:
    """Index records by a key function; records without a usable key are skipped."""
    idx: dict[Any, list[dict[str, Any]]] = {}
    for rec in records:
        key = keyfn(rec)
        if key is not None and key != "":
            idx.setdefault(key, []).append(rec)
    return idx


def decision_key(cycle_id: Any, symbol: str) -> tuple[int, str]:
    """Canonical (cycle_id, symbol) key used to bridge ranking -> decisions."""
    try:
        cid = int(cycle_id or 0)
    except (TypeError, ValueError):
        cid = 0
    return (cid, str(symbol or ""))


def build_decision_index(decisions: list[dict[str, Any]]) -> dict[tuple[int, str], dict[str, Any]]:
    """Index decision_ledger by (cycle_id, symbol). One entry per cycle per symbol."""
    out: dict[tuple[int, str], dict[str, Any]] = {}
    for d in decisions:
        key = decision_key(d.get("cycle_id"), d.get("symbol"))
        if key[1]:
            out[key] = d
    return out


def shadow_outcome_for_opportunity(shadows: list[dict[str, Any]],
                                   canonical_opp: str) -> dict[str, Any] | None:
    """Return the PRIMARY simulated outcome dict for one opportunity, if present."""
    if not canonical_opp:
        return None
    for s in shadows:
        if s.get("canonical_opportunity_id") != canonical_opp:
            continue
        if deep_get(s, "identity", "shadow_type") in (PRIMARY_HORIZON_TAG, ""):
            return s
    # Fallback: the first shadow belonging to this opportunity.
    for s in shadows:
        if s.get("canonical_opportunity_id") == canonical_opp:
            return s
    return None


def join_candidate_outcome(
    candidate: dict[str, Any],
    ranking_row: dict[str, Any],
    decision_idx: dict[tuple[int, str], dict[str, Any]],
    truth_idx: dict[str, list[dict[str, Any]]],
    shadows: list[dict[str, Any]],
    boundary: QuarantineBoundary,
) -> dict[str, Any]:
    """Attach decision + realised/shadow outcome evidence to a ranked candidate.

    Canonical join (the strongest the data supports):
        portfolio_rankings candidate (cycle_id, symbol)
            -> decision_ledger (cycle_id, symbol)           [bridge, HIGH conf]
            -> trade_truth     (correlation_id)             [realised outcome]
            -> shadow_runtime  (canonical_opportunity_id)   [primary shadow]

    NOTE: portfolio_rankings candidates do NOT carry a canonical_opportunity_id,
    observation_id, or correlation_id of their own (only a synthetic
    opportunity_id='{symbol}_{cycle}_{pattern}' which is NOT guaranteed to match
    canonical IDs). Hence the cycle+symbol bridge through decision_ledger is the
    canonical join; the synthetic key is never used for joins.
    """
    outcome: dict[str, Any] = {
        "matched_decision": False,
        "decision": "",
        "decision_clean": None,
        "correlation_id": "",
        "canonical_opportunity_id": "",
        "has_live": False,
        "live_r": None,
        "has_shadow": False,
        "shadow_r": None,
    }
    key = decision_key(ranking_row.get("cycle_id"), candidate.get("symbol"))
    ledger = decision_idx.get(key) if key[1] else None
    if not ledger:
        return outcome

    outcome["matched_decision"] = True
    outcome["decision"] = ledger.get("decision", "")
    outcome["correlation_id"] = ledger.get("correlation_id", "")
    outcome["canonical_opportunity_id"] = ledger.get("canonical_opportunity_id", "")
    outcome["decision_clean"] = not boundary.contains(
        ledger.get("timestamp") or ledger.get("timestamp_unix")
    )

    corr = outcome["correlation_id"]
    if corr:
        for truth in truth_idx.get(corr, []):
            r = numeric(truth, "outcome", "r_multiple_realised")
            if r is not None:
                outcome["has_live"] = True
                outcome["live_r"] = r
                break

    shadow = shadow_outcome_for_opportunity(shadows, outcome["canonical_opportunity_id"])
    if shadow is not None:
        r = numeric(shadow, "simulated_outcome", "pnl_r_multiple")
        if r is not None:
            outcome["has_shadow"] = True
            outcome["shadow_r"] = r

    return outcome


def outcome_value(candidate: dict[str, Any],
                  precedence: str = "shadow") -> float | None:
    """Preferenced outcome R for a candidate that has been joined.

    ``precedence`` chooses between 'shadow' (simulated; available for promoted
    AND rejected alike -- used for selection comparisons) and 'live' (realised
    broker outcome; only for executed trades). LABELLED by the caller.
    """
    joined = candidate.get("_outcome", {}) or {}
    if precedence == "live" and joined.get("has_live"):
        return joined.get("live_r")
    if joined.get("has_shadow"):
        return joined.get("shadow_r")
    if joined.get("has_live"):
        return joined.get("live_r")
    return None