"""
V2 Outcome Linker — Connects V2Opportunity observations to shadow trade outcomes.

This is RESEARCH INFRASTRUCTURE only. It does NOT:
    - Modify trading behaviour
    - Alter shadow trade creation or lifecycle
    - Change execution logic
    - Import pipeline or decision modules

Purpose:
    After shadow trades close, this module matches them back to the
    V2Opportunity observation that captured the pre-trade market state.
    This enables research to answer: "Given this market context, what was the outcome?"

Match priority:
    1. entity_id (deterministic: {symbol}_{bar_time})
    2. correlation_id (decision spine ID)
    3. symbol + timestamp tolerance (±300s fallback)

Usage:
    from core.research.v2_outcome_linker import link_outcomes
    results = link_outcomes(symbol="EURUSD")
    results = link_outcomes()  # all symbols
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default directories (overridable for testing)
_V2_OPP_DIR = "logs/v2_opportunities"
_SHADOW_DIR = "logs/shadow_trades"

# Timestamp tolerance for fallback matching (seconds)
_TIMESTAMP_TOLERANCE = 300  # 5 minutes (60 M5 bars)


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════


def link_outcomes(
    *,
    symbol: str | None = None,
    v2_dir: str | None = None,
    shadow_dir: str | None = None,
    persist: bool = True,
) -> LinkageReport:
    """
    Link V2Opportunity records to shadow trade outcomes.

    Args:
        symbol: Filter to specific symbol (None = all symbols)
        v2_dir: Override V2 opportunity directory (for testing)
        shadow_dir: Override shadow trade directory (for testing)
        persist: Whether to write linked records back to disk

    Returns:
        LinkageReport with match statistics and linked records.
    """
    opp_dir = Path(v2_dir or _V2_OPP_DIR)
    trade_dir = Path(shadow_dir or _SHADOW_DIR)

    # Load opportunities
    opportunities = _load_opportunities(opp_dir, symbol)
    if not opportunities:
        return LinkageReport(
            total_opportunities=0,
            matched=0,
            unmatched=0,
            match_by_entity_id=0,
            match_by_correlation_id=0,
            match_by_timestamp=0,
            linked_records=[],
        )

    # Load shadow trades
    shadow_trades = _load_shadow_trades(trade_dir, symbol)

    # Build indexes for matching
    entity_index: dict[str, dict] = {}
    correlation_index: dict[str, dict] = {}
    symbol_time_index: dict[str, list[dict]] = {}

    for trade in shadow_trades:
        identity = trade.get("identity", {})
        outcome = trade.get("simulated_outcome", {})
        eid = identity.get("entity_id") or ""
        cid = identity.get("correlation_id") or ""
        sym = identity.get("symbol") or ""
        entry_time = trade.get("decision_snapshot", {}).get("timestamp_decision_utc", 0)

        trade_summary = {
            "entity_id": eid,
            "correlation_id": cid,
            "symbol": sym,
            "entry_time": entry_time,
            "result_r": outcome.get("pnl_r_multiple"),
            "mfe_r": outcome.get("mfe_r"),
            "mae_r": outcome.get("mae_r"),
            "exit_reason": outcome.get("exit_reason", ""),
            "bars_held": outcome.get("bars_held", 0),
        }

        if eid:
            entity_index[eid] = trade_summary
        if cid:
            correlation_index[cid] = trade_summary
        key = f"{sym}"
        if key not in symbol_time_index:
            symbol_time_index[key] = []
        symbol_time_index[key].append(trade_summary)

    # Match opportunities to trades
    linked: list[dict] = []
    match_entity = 0
    match_corr = 0
    match_time = 0
    unmatched_count = 0

    for opp in opportunities:
        opp_corr = opp.get("correlation_id", "")
        opp_symbol = opp.get("symbol", "")
        opp_time = float(opp.get("timestamp_utc", 0))

        # Already linked — skip
        if opp.get("outcome_recorded"):
            linked.append(opp)
            continue

        # Match priority 1: entity_id
        # entity_id format is typically {symbol}_{bar_time}
        entity_key = opp_corr  # correlation_id often IS the entity_id
        match = entity_index.get(entity_key)
        match_method = ""

        if match:
            match_method = "entity_id"
            match_entity += 1
        else:
            # Priority 2: correlation_id
            match = correlation_index.get(opp_corr)
            if match:
                match_method = "correlation_id"
                match_corr += 1
            else:
                # Priority 3: symbol + timestamp tolerance
                candidates = symbol_time_index.get(opp_symbol, [])
                best = _find_nearest_trade(candidates, opp_time)
                if best:
                    match = best
                    match_method = "timestamp"
                    match_time += 1

        if match:
            # Attach outcome fields
            result_r = match["result_r"]
            opp["outcome_recorded"] = True
            opp["outcome_raw_r"] = result_r
            opp["mfe"] = match["mfe_r"]
            opp["mae"] = match["mae_r"]
            opp["bars_to_outcome"] = match["bars_held"]
            opp["reached_positive_target"] = (result_r is not None and result_r >= 1.0)
            opp["reached_negative_target"] = (result_r is not None and result_r <= -1.0)
            # Extended outcome fields
            opp["_linkage"] = {
                "linked": True,
                "result_r": result_r,
                "win": result_r is not None and result_r > 0,
                "mfe_r": match["mfe_r"],
                "mae_r": match["mae_r"],
                "hold_minutes": (match["bars_held"] or 0) * 5,  # M5 bars
                "exit_reason": match["exit_reason"],
                "match_method": match_method,
            }
            linked.append(opp)
        else:
            # No match — keep original with empty outcome
            opp["_linkage"] = {
                "linked": False,
                "result_r": None,
                "win": None,
                "mfe_r": None,
                "mae_r": None,
                "hold_minutes": None,
                "exit_reason": None,
                "match_method": None,
            }
            linked.append(opp)
            unmatched_count += 1

    # Persist linked records
    if persist and linked:
        _persist_linked(linked, opp_dir)

    return LinkageReport(
        total_opportunities=len(opportunities),
        matched=match_entity + match_corr + match_time,
        unmatched=unmatched_count,
        match_by_entity_id=match_entity,
        match_by_correlation_id=match_corr,
        match_by_timestamp=match_time,
        linked_records=linked,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# REPORT DATACLASS
# ═══════════════════════════════════════════════════════════════════════════════


class LinkageReport:
    """Summary of outcome linkage run."""

    __slots__ = (
        "total_opportunities", "matched", "unmatched",
        "match_by_entity_id", "match_by_correlation_id",
        "match_by_timestamp", "linked_records",
    )

    def __init__(
        self,
        total_opportunities: int,
        matched: int,
        unmatched: int,
        match_by_entity_id: int,
        match_by_correlation_id: int,
        match_by_timestamp: int,
        linked_records: list[dict],
    ):
        self.total_opportunities = total_opportunities
        self.matched = matched
        self.unmatched = unmatched
        self.match_by_entity_id = match_by_entity_id
        self.match_by_correlation_id = match_by_correlation_id
        self.match_by_timestamp = match_by_timestamp
        self.linked_records = linked_records

    @property
    def match_rate(self) -> float:
        """Fraction of opportunities matched to outcomes."""
        if self.total_opportunities == 0:
            return 0.0
        return self.matched / self.total_opportunities

    def summary(self) -> dict[str, Any]:
        """Return summary dict for logging/reporting."""
        return {
            "total_opportunities": self.total_opportunities,
            "matched": self.matched,
            "unmatched": self.unmatched,
            "match_rate": round(self.match_rate, 4),
            "by_entity_id": self.match_by_entity_id,
            "by_correlation_id": self.match_by_correlation_id,
            "by_timestamp": self.match_by_timestamp,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ═══════════════════════════════════════════════════════════════════════════════


def _load_opportunities(base: Path, symbol: str | None) -> list[dict]:
    """Load V2Opportunity records from JSONL."""
    if not base.exists():
        return []

    results: list[dict] = []
    dirs = [base / symbol] if symbol else [d for d in base.iterdir() if d.is_dir()]

    for dir_path in dirs:
        if not dir_path.exists():
            continue
        for filepath in sorted(dir_path.glob("*.jsonl")):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                results.append(json.loads(line))
                            except json.JSONDecodeError:
                                continue
            except OSError:
                continue

    return results


def _load_shadow_trades(base: Path, symbol: str | None) -> list[dict]:
    """Load shadow trade records from JSONL."""
    if not base.exists():
        return []

    results: list[dict] = []
    dirs = [base / symbol] if symbol else [d for d in base.iterdir() if d.is_dir()]

    for dir_path in dirs:
        if not dir_path.exists():
            continue
        for filepath in sorted(dir_path.glob("*.jsonl")):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                record = json.loads(line)
                                # Only include v2 schema records with identity
                                if "identity" in record:
                                    results.append(record)
                            except json.JSONDecodeError:
                                continue
            except OSError:
                continue

    return results


def _find_nearest_trade(
    candidates: list[dict], target_time: float
) -> dict | None:
    """Find nearest trade by entry_time within tolerance."""
    if not candidates or target_time <= 0:
        return None

    best: dict | None = None
    best_delta = float("inf")

    for trade in candidates:
        entry_time = float(trade.get("entry_time", 0))
        if entry_time <= 0:
            continue
        delta = abs(entry_time - target_time)
        if delta <= _TIMESTAMP_TOLERANCE and delta < best_delta:
            best = trade
            best_delta = delta

    return best


def _persist_linked(records: list[dict], opp_dir: Path) -> None:
    """
    Write linked records back to V2 opportunity files.

    Groups by symbol/date and overwrites the JSONL file with updated records.
    Original observation fields are preserved; only outcome/_linkage fields added.
    """
    # Group by symbol and date
    grouped: dict[str, list[dict]] = {}
    for rec in records:
        sym = rec.get("symbol", "UNKNOWN")
        ts = float(rec.get("timestamp_utc", 0))
        if ts > 1_000_000_000:
            date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        else:
            date_str = "unknown"
        key = f"{sym}/{date_str}"
        if key not in grouped:
            grouped[key] = []
        grouped[key].append(rec)

    # Write each group
    for key, recs in grouped.items():
        path = opp_dir / f"{key}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(path, "w", encoding="utf-8") as f:
                for rec in recs:
                    f.write(json.dumps(rec, separators=(",", ":"), default=str) + "\n")
        except OSError as exc:
            logger.debug("[V2_LINKAGE_PERSIST] failed for %s: %s", key, exc)
