"""
Correlation Engine — Links related records across persistence layers.

Joins shadow trades with trade truth records using correlation_id
to produce enriched research records for experimentation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ResearchRecord:
    """A single correlated research record linking shadow prediction to live outcome."""
    correlation_id: str
    symbol: str

    # Canonical lineage root — the shared shadow↔live join key
    canonical_opportunity_id: str = ""

    # Shadow prediction
    shadow_r: float | None = None
    shadow_exit_reason: str = ""
    shadow_bars_held: int = 0
    shadow_direction: str = ""
    shadow_pattern: str = ""
    shadow_score: float = 0.0

    # Live outcome (from trade_truth)
    live_r: float | None = None
    live_exit_reason: str = ""
    live_pnl: float = 0.0

    # Computed
    prediction_error: float | None = None

    # Metadata
    has_shadow: bool = False
    has_live: bool = False

    def is_matched(self) -> bool:
        """True if both shadow and live outcomes are present."""
        return self.has_shadow and self.has_live and self.shadow_r is not None and self.live_r is not None


def _extract_correlation_id(record: dict[str, Any]) -> str | None:
    """Extract correlation_id from a record, handling nested schemas."""
    # Direct field
    cor_id = record.get("correlation_id")
    if cor_id:
        return str(cor_id)
    # Nested under identity (trade_truth schema)
    identity = record.get("identity", {})
    if isinstance(identity, dict):
        cor_id = identity.get("correlation_id")
        if cor_id:
            return str(cor_id)
    return None


def extract_canonical_opportunity_id(record: dict[str, Any]) -> str:
    """Extract the canonical lineage root from a record, handling nested schemas.

    The canonical_opportunity_id ("{SYMBOL}*{bar_time}*{PATTERN}") is the
    canonical lineage ROOT shared by the shadow runtime (nshadow_* events,
    copied verbatim onto every event) and trade_truth_v1 (identity domain).
    """
    canon = record.get("canonical_opportunity_id")
    if canon:
        return str(canon)
    identity = record.get("identity", {})
    if isinstance(identity, dict):
        canon = identity.get("canonical_opportunity_id")
        if canon:
            return str(canon)
    return ""



# ═══════════════════════════════════════════════════════════════════════════════
# CANONICAL SHADOW ↔ LIVE MATCHING CONTRACT (Q16/X4)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Join key: canonical_opportunity_id — the canonical lineage ROOT. It is the
# ONLY lineage field shared by BOTH authoritative populations:
#     - normalized shadow_runtime_v1 outcomes (identity.canonical_opportunity_id,
#       inherited verbatim from the nshadow_* runtime events), and
#     - trade_truth_v1 (identity.canonical_opportunity_id).
# correlation_id (COR-*) is minted ONLY on the live EXECUTE path and is never
# carried by the shadow runtime stream, so it cannot be the join key.
#
# Shadow population: canonical runtime shadows (nshadow_*). For the same
# canonical opportunity several horizon simulations may legitimately coexist;
# the like-for-like comparison against the executed live trade uses the
# PRIMARY_HORIZON_SIMULATION (the incumbent prediction surface). A canonical
# covered only by HORIZON_ALTERNATIVE shadows is excluded with explicit
# accounting — comparing a different horizon's R against the live R would
# contaminate the validation.
#
# One-to-one safety: duplicate replay rows (same IDs, same content) collapse;
# genuinely ambiguous populations (multiple distinct live trades for one
# canonical opportunity, or conflicting replay content) are EXCLUDED and
# counted — never fabricated into pairs. Symbols must agree. Never joins by
# symbol, timestamp proximity, or list order.


# shadow_runtime_v1 identity tag for the incumbent prediction surface
PRIMARY_HORIZON_TYPE = "PRIMARY_HORIZON_SIMULATION"


@dataclass
class MatchDiagnostics:
    """Explicit accounting for the shadow↔live join (no silent drops)."""

    total_shadow: int = 0
    total_live: int = 0
    # Eligible = has join key + outcome evidence + unambiguous
    eligible_shadow_canonicals: int = 0
    eligible_live_canonicals: int = 0
    matched_pairs: int = 0
    unmatched_shadow: int = 0          # eligible shadow canonicals with no live
    unmatched_live: int = 0            # eligible live canonicals with no shadow
    ambiguous_excluded: int = 0        # canonicals excluded for ambiguity
    excluded_by_reason: dict[str, int] = field(default_factory=dict)

    def note(self, reason: str, n: int = 1) -> None:
        self.excluded_by_reason[reason] = self.excluded_by_reason.get(reason, 0) + n

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_shadow": self.total_shadow,
            "total_live": self.total_live,
            "eligible_shadow_canonicals": self.eligible_shadow_canonicals,
            "eligible_live_canonicals": self.eligible_live_canonicals,
            "matched_pairs": self.matched_pairs,
            "unmatched_shadow": self.unmatched_shadow,
            "unmatched_live": self.unmatched_live,
            "ambiguous_excluded": self.ambiguous_excluded,
            "excluded_by_reason": dict(self.excluded_by_reason),
        }


def _live_outcome_signature(record: dict[str, Any]) -> tuple[str, float | None]:
    """Identity of a live outcome: trade_id + realised R (conflict detector)."""
    identity = record.get("identity", {}) if isinstance(record.get("identity", {}), dict) else {}
    trade_id = str(identity.get("trade_id", "") or record.get("trade_id", "") or "")
    return trade_id, _extract_r_multiple_live(record)


def _index_live_outcomes(
    trade_truths: list[dict[str, Any]],
    diag: MatchDiagnostics,
) -> dict[str, dict[str, Any]]:
    """Index trade_truth by canonical_opportunity_id with duplicate safety."""
    indexed: dict[str, dict[str, Any] | None] = {}
    for record in trade_truths:
        canon = extract_canonical_opportunity_id(record)
        if not canon:
            diag.note("live_missing_canonical_key")
            continue
        if _extract_r_multiple_live(record) is None:
            diag.note("live_missing_outcome")
            continue
        existing = indexed.get(canon)
        if existing is None and canon not in indexed:
            indexed[canon] = record
            continue
        if existing is not None and _live_outcome_signature(existing) == _live_outcome_signature(record):
            diag.note("live_duplicate_replay_collapsed")
            continue  # exact replay — deterministic collapse, no pair inflation
        # Distinct/conflicting live outcomes for ONE canonical opportunity.
        diag.note("live_ambiguous_multiple_outcomes")
        diag.ambiguous_excluded += 1
        indexed[canon] = None  # tombstone: canonical excluded — never fabricated
    return {k: v for k, v in indexed.items() if v is not None}


def _index_shadow_predictions(
    shadow_trades: list[dict[str, Any]],
    diag: MatchDiagnostics,
) -> dict[str, dict[str, Any]]:
    """Index shadow outcomes by canonical_opportunity_id with horizon semantics.

    Selects the PRIMARY_HORIZON_SIMULATION per canonical opportunity (explicit
    architectural semantics, never first/last). A canonical covered only by
    HORIZON_ALTERNATIVE shadows is excluded with explicit accounting.
    """
    by_canon: dict[str, list[dict[str, Any]]] = {}
    for record in shadow_trades:
        canon = extract_canonical_opportunity_id(record)
        if not canon:
            diag.note("shadow_missing_canonical_key")
            continue
        if _extract_r_multiple_shadow(record) is None:
            diag.note("shadow_missing_outcome")
            continue
        by_canon.setdefault(canon, []).append(record)

    indexed: dict[str, dict[str, Any]] = {}
    for canon, group in by_canon.items():
        primaries = [
            r for r in group
            if (r.get("identity", {}) or {}).get("shadow_type", "") == PRIMARY_HORIZON_TYPE
        ]
        if not primaries:
            diag.note("shadow_horizon_alternative_only")
            continue
        # Collapse replay duplicates of the same canonical primary.
        distinct: dict[str, dict[str, Any]] = {}
        conflicting = False
        for rec in primaries:
            identity = rec.get("identity", {}) or {}
            tid = str(identity.get("shadow_trade_id", "") or "")
            prior = distinct.get(tid)
            if prior is None:
                distinct[tid] = rec
            elif _extract_r_multiple_shadow(prior) != _extract_r_multiple_shadow(rec):
                conflicting = True
            else:
                diag.note("shadow_duplicate_replay_collapsed")
        if conflicting or len(distinct) > 1:
            diag.note("shadow_ambiguous_multiple_primaries")
            diag.ambiguous_excluded += 1
            continue
        indexed[canon] = next(iter(distinct.values()))
    return indexed


def _extract_r_multiple_shadow(record: dict[str, Any]) -> float | None:
    """Extract R-multiple from shadow trade record."""
    # Direct field (shadow_trades_v2)
    simulated = record.get("simulated_outcome", {})
    if isinstance(simulated, dict):
        r = simulated.get("pnl_r_multiple")
        if r is not None:
            return float(r)
    # Flat field
    r = record.get("pnl_r_multiple")
    if r is not None:
        return float(r)
    return None


def _extract_r_multiple_live(record: dict[str, Any]) -> float | None:
    """Extract R-multiple from trade truth record."""
    # Nested under outcome (trade_truth_v1)
    outcome = record.get("outcome", {})
    if isinstance(outcome, dict):
        r = outcome.get("r_multiple_realised")
        if r is not None:
            return float(r)
    # Flat field (legacy)
    r = record.get("r_multiple_realised")
    if r is not None:
        return float(r)
    return None


def match_shadow_to_live(
    shadow_trades: list[dict[str, Any]],
    trade_truths: list[dict[str, Any]],
) -> tuple[list[ResearchRecord], MatchDiagnostics]:
    """
    Canonical one-to-one shadow↔live matching for Q16/X4.

    A shadow result is compared to a live result ONLY when both carry the SAME
    canonical_opportunity_id (same underlying opportunity/decision). Deterministic;
    never joins by symbol, timestamp proximity, or list order.

    Returns (research_records, diagnostics). Records include matched pairs,
    shadow-only, and live-only research records; diagnostic counts carry the
    full unmatched/ambiguous/excluded accounting.
    """
    diag = MatchDiagnostics(total_shadow=len(shadow_trades), total_live=len(trade_truths))

    live_by_canon = _index_live_outcomes(trade_truths, diag)
    shadow_by_canon = _index_shadow_predictions(shadow_trades, diag)
    diag.eligible_shadow_canonicals = len(shadow_by_canon)
    diag.eligible_live_canonicals = len(live_by_canon)

    results: list[ResearchRecord] = []
    matched_canon: set[str] = set()

    for canon, shadow in sorted(shadow_by_canon.items()):
        identity = shadow.get("identity", {}) or {}
        decision = shadow.get("decision_snapshot", {}) or {}
        simulated = shadow.get("simulated_outcome", {}) or {}
        shadow_symbol = str(identity.get("symbol", "") or "")

        truth = live_by_canon.get(canon)
        if truth is None:
            results.append(ResearchRecord(
                correlation_id="",
                symbol=shadow_symbol,
                canonical_opportunity_id=canon,
                shadow_r=_extract_r_multiple_shadow(shadow),
                shadow_exit_reason=str(simulated.get("exit_reason", "") or ""),
                shadow_bars_held=int(simulated.get("bars_held", 0) or 0),
                shadow_direction=str(decision.get("direction", "") or ""),
                shadow_pattern=str(decision.get("pattern", "") or ""),
                shadow_score=float(decision.get("score", 0.0) or 0.0),
                has_shadow=True,
            ))
            diag.unmatched_shadow += 1
            continue

        truth_identity = truth.get("identity", {}) or {}
        live_symbol = str(truth_identity.get("symbol", "") or "")
        if shadow_symbol and live_symbol and shadow_symbol != live_symbol:
            # Symbol is embedded in the canonical root, so this should be
            # impossible for well-formed records — enforced explicitly anyway.
            diag.note("symbol_mismatch_excluded")
            continue

        outcome = truth.get("outcome", {}) or {}
        exit_info = truth.get("exit", {}) or {}
        live_r = _extract_r_multiple_live(truth)
        shadow_r = _extract_r_multiple_shadow(shadow)
        results.append(ResearchRecord(
            correlation_id=str(_extract_correlation_id(truth) or ""),
            symbol=shadow_symbol or live_symbol,
            canonical_opportunity_id=canon,
            shadow_r=shadow_r,
            shadow_exit_reason=str(simulated.get("exit_reason", "") or ""),
            shadow_bars_held=int(simulated.get("bars_held", 0) or 0),
            shadow_direction=str(decision.get("direction", "") or ""),
            shadow_pattern=str(decision.get("pattern", "") or ""),
            shadow_score=float(decision.get("score", 0.0) or 0.0),
            live_r=live_r,
            live_exit_reason=str(exit_info.get("exit_reason", "") or ""),
            live_pnl=float(outcome.get("pnl_realised", outcome.get("net_profit", 0.0)) or 0.0),
            has_shadow=True,
            has_live=True,
        ))
        if shadow_r is not None and live_r is not None:
            results[-1].prediction_error = shadow_r - live_r
        matched_canon.add(canon)
        diag.matched_pairs += 1

    # Live outcomes with no eligible shadow counterpart
    for canon, truth in sorted(live_by_canon.items()):
        if canon in matched_canon:
            continue
        truth_identity = truth.get("identity", {}) or {}
        outcome = truth.get("outcome", {}) or {}
        exit_info = truth.get("exit", {}) or {}
        results.append(ResearchRecord(
            correlation_id=str(_extract_correlation_id(truth) or ""),
            symbol=str(truth_identity.get("symbol", "") or ""),
            canonical_opportunity_id=canon,
            live_r=_extract_r_multiple_live(truth),
            live_exit_reason=str(exit_info.get("exit_reason", "") or ""),
            live_pnl=float(outcome.get("pnl_realised", outcome.get("net_profit", 0.0)) or 0.0),
            has_live=True,
        ))
        diag.unmatched_live += 1

    logger.info(
        "[RESEARCH_LINKER] canonical join: shadows=%d live=%d matched=%d "
        "shadow_only=%d live_only=%d ambiguous=%d excluded=%s",
        diag.total_shadow, diag.total_live, diag.matched_pairs,
        diag.unmatched_shadow, diag.unmatched_live, diag.ambiguous_excluded,
        diag.excluded_by_reason,
    )
    return results, diag

def build_research_records(
    shadow_trades: list[dict[str, Any]],
    trade_truths: list[dict[str, Any]],
) -> list[ResearchRecord]:
    """
    Join shadow trades with trade truth records via the canonical lineage root.

    Returns list of ResearchRecords. Records may have:
    - Both shadow and live (matched — usable for Q16)
    - Shadow only (signal produced but no live trade)
    - Live only (live trade without shadow record — unlikely but handled)
    """
    results, _diag = match_shadow_to_live(shadow_trades, trade_truths)
    return results
