"""
Cohort Outcome Mapper — Joins trade snapshots with cohort classification for analysis.

PURE transformation layer. Does NOT touch live systems, execution, scoring, or risk.
Uses build_cohort_from_trade as single source of CohortKey construction.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools.cohort_analysis.cohort_policy_types import CohortKey
from tools.cohort_analysis.cohort_builder import build_cohort_from_trade
from tools.trade_logging.live_trade_snapshot import TradeSnapshot

_SNAPSHOT_FILE = "data/trade_snapshots.jsonl"


def build_cohort(snapshot: TradeSnapshot | dict[str, Any]) -> CohortKey:
    """
    Reconstruct CohortKey from a trade snapshot.

    Delegates to build_cohort_from_trade (single source of truth).
    """
    if isinstance(snapshot, dict):
        # Remap flat canonical fields into the format build_cohort_from_trade expects
        decision_like = {
            "confirmation": {"strength": snapshot.get("confirmation_strength", "UNKNOWN")},
            "entry_timing": snapshot.get("entry_timing", "UNKNOWN"),
            "engine_state": {"regime_state": snapshot.get("market_regime", "UNKNOWN")},
        }
        return build_cohort_from_trade(decision_like)
    else:
        decision_like = {
            "confirmation": {"strength": snapshot.confirmation_strength or "UNKNOWN"},
            "entry_timing": snapshot.entry_timing or "UNKNOWN",
            "engine_state": {"regime_state": snapshot.market_regime or "UNKNOWN"},
        }
        return build_cohort_from_trade(decision_like)


def enrich_snapshot(snapshot: TradeSnapshot | dict[str, Any]) -> dict[str, Any]:
    """
    Enrich a trade snapshot with cohort classification and outcome flags.

    Args:
        snapshot: TradeSnapshot object or dict.

    Returns:
        Dict with all snapshot fields plus:
            - cohort_key: CohortKey
            - cohort_id: str (e.g. "STRONG+EARLY+TRENDING")
            - r_multiple: float | None
            - win: bool | None
    """
    if isinstance(snapshot, dict):
        data = dict(snapshot)
    else:
        data = {
            "trade_id": snapshot.trade_id,
            "symbol": snapshot.symbol,
            "entry_time": snapshot.entry_time,
            "exit_time": snapshot.exit_time,
            "entry_r": snapshot.entry_r,
            "final_r": snapshot.final_r,
            "mfe": snapshot.mfe,
            "mae": snapshot.mae,
            "breakeven_triggered": snapshot.breakeven_triggered,
            "trailing_triggered": snapshot.trailing_triggered,
            "partials_taken": snapshot.partials_taken,
            "confirmation_strength": snapshot.confirmation_strength,
            "entry_timing": snapshot.entry_timing,
            "market_regime": snapshot.market_regime,
        }

    cohort = build_cohort(data)
    r_multiple = data.get("final_r")
    win = r_multiple > 0 if r_multiple is not None else None

    data["cohort_key"] = cohort
    data["cohort_id"] = f"{cohort.confirmation_strength}+{cohort.entry_timing}+{cohort.market_regime}"
    data["r_multiple"] = r_multiple
    data["win"] = win

    return data


def enrich_all_snapshots(path: str | None = None) -> list[dict[str, Any]]:
    """
    Read all trade snapshots from JSONL and enrich with cohort classification.

    Args:
        path: Path to JSONL file. Defaults to data/trade_snapshots.jsonl.

    Returns:
        List of enriched trade dicts.
    """
    filepath = Path(path) if path else Path(_SNAPSHOT_FILE)
    results: list[dict[str, Any]] = []

    if not filepath.exists():
        return results

    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                enriched = enrich_snapshot(record)
                results.append(enriched)
            except (json.JSONDecodeError, KeyError):
                continue

    return results
