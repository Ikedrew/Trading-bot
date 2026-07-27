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
    # Nested under outcome (trade_truth_v3)
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


def build_research_records(
    shadow_trades: list[dict[str, Any]],
    trade_truths: list[dict[str, Any]],
) -> list[ResearchRecord]:
    """
    Join shadow trades with trade truth records via correlation_id.

    Returns list of ResearchRecords. Records may have:
    - Both shadow and live (matched — usable for Q16)
    - Shadow only (signal produced but no live trade)
    - Live only (live trade without shadow record — unlikely but handled)
    """
    # Index trade truths by correlation_id
    truth_by_cor: dict[str, dict[str, Any]] = {}
    for record in trade_truths:
        cor_id = _extract_correlation_id(record)
        if cor_id:
            truth_by_cor[cor_id] = record

    # Build research records from shadow trades
    results: list[ResearchRecord] = []
    matched_cor_ids: set[str] = set()

    for shadow in shadow_trades:
        cor_id = _extract_correlation_id(shadow)
        if not cor_id:
            continue

        # Extract shadow fields
        shadow_r = _extract_r_multiple_shadow(shadow)
        identity = shadow.get("identity", shadow)
        decision = shadow.get("decision_snapshot", shadow)
        simulated = shadow.get("simulated_outcome", shadow)

        rr = ResearchRecord(
            correlation_id=cor_id,
            symbol=identity.get("symbol", shadow.get("symbol", "")),
            shadow_r=shadow_r,
            shadow_exit_reason=simulated.get("exit_reason", shadow.get("exit_reason", "")),
            shadow_bars_held=int(simulated.get("bars_held", shadow.get("bars_held", 0))),
            shadow_direction=decision.get("direction", shadow.get("direction", "")),
            shadow_pattern=decision.get("pattern", shadow.get("pattern", "")),
            shadow_score=float(decision.get("score", shadow.get("score", 0.0))),
            has_shadow=True,
        )

        # Try to match with live outcome
        truth = truth_by_cor.get(cor_id)
        if truth:
            live_r = _extract_r_multiple_live(truth)
            outcome = truth.get("outcome", truth)
            exit_info = truth.get("exit", truth)

            rr.live_r = live_r
            rr.live_exit_reason = exit_info.get("exit_reason", truth.get("exit_reason", ""))
            rr.live_pnl = float(outcome.get("pnl_realised", outcome.get("net_profit", 0.0)))
            rr.has_live = True
            matched_cor_ids.add(cor_id)

            # Compute prediction error
            if rr.shadow_r is not None and rr.live_r is not None:
                rr.prediction_error = rr.shadow_r - rr.live_r

        results.append(rr)

    # Add any live trades without shadow records
    for cor_id, truth in truth_by_cor.items():
        if cor_id in matched_cor_ids:
            continue
        live_r = _extract_r_multiple_live(truth)
        identity = truth.get("identity", truth)
        rr = ResearchRecord(
            correlation_id=cor_id,
            symbol=identity.get("symbol", truth.get("symbol", "")),
            live_r=live_r,
            live_exit_reason=truth.get("exit", {}).get("exit_reason", ""),
            live_pnl=float(truth.get("outcome", {}).get("pnl_realised", 0.0)),
            has_live=True,
        )
        results.append(rr)

    # Summary
    total = len(results)
    matched = sum(1 for r in results if r.is_matched())
    shadow_only = sum(1 for r in results if r.has_shadow and not r.has_live)
    live_only = sum(1 for r in results if r.has_live and not r.has_shadow)

    logger.info(
        "[RESEARCH_LINKER] records=%d matched=%d shadow_only=%d live_only=%d",
        total, matched, shadow_only, live_only,
    )

    return results
