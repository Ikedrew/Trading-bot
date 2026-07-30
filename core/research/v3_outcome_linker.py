"""
V3 Outcome Linker — Connects V3Opportunity observations to shadow trade outcomes.

This is RESEARCH INFRASTRUCTURE only. It does NOT:
    - Modify trading behaviour
    - Alter shadow trade creation or lifecycle
    - Change execution logic

Purpose:
    After shadow trades close, this module matches them back to the
    V3Opportunity observation that captured location/liquidity context.
    Enables: "Given this market location, what was the outcome?"

Match priority:
    1. entity_id (V3.correlation_id == shadow.identity.entity_id)
    2. correlation_id (V3.correlation_id == shadow.identity.correlation_id)
    3. symbol + timestamp tolerance (±300s fallback)

Key format: {SYMBOL}_{bar_time} — identical in both V3 and shadow trades.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default directories
_V3_OPP_DIR = "logs/v3_opportunities"
_SHADOW_DIR = "logs/shadow_trades"

# Timestamp tolerance for fallback matching
_TIMESTAMP_TOLERANCE = 300  # 5 minutes


# ═══════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════


class V3LinkageReport:
    """Summary of V3 outcome linkage run."""

    __slots__ = (
        "total_observations", "matched", "unmatched",
        "match_by_entity_id", "match_by_correlation_id",
        "match_by_timestamp", "linked_records",
        "no_trade_observations",
    )

    def __init__(
        self,
        total_observations: int = 0,
        matched: int = 0,
        unmatched: int = 0,
        match_by_entity_id: int = 0,
        match_by_correlation_id: int = 0,
        match_by_timestamp: int = 0,
        linked_records: list[dict] | None = None,
        no_trade_observations: int = 0,
    ):
        self.total_observations = total_observations
        self.matched = matched
        self.unmatched = unmatched
        self.match_by_entity_id = match_by_entity_id
        self.match_by_correlation_id = match_by_correlation_id
        self.match_by_timestamp = match_by_timestamp
        self.linked_records = linked_records or []
        self.no_trade_observations = no_trade_observations

    @property
    def match_rate(self) -> float:
        if self.total_observations == 0:
            return 0.0
        return self.matched / self.total_observations

    def summary(self) -> dict[str, Any]:
        return {
            "total_observations": self.total_observations,
            "matched": self.matched,
            "unmatched": self.unmatched,
            "match_rate": round(self.match_rate, 4),
            "by_entity_id": self.match_by_entity_id,
            "by_correlation_id": self.match_by_correlation_id,
            "by_timestamp": self.match_by_timestamp,
            "no_trade_observations": self.no_trade_observations,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════


def link_v3_outcomes(
    *,
    symbol: str | None = None,
    v3_dir: str | None = None,
    shadow_dir: str | None = None,
    persist: bool = True,
) -> V3LinkageReport:
    """
    Link V3Opportunity records to shadow trade outcomes.

    Args:
        symbol: Filter to specific symbol (None = all)
        v3_dir: Override V3 opportunity directory
        shadow_dir: Override shadow trade directory
        persist: Write linked records back to disk

    Returns:
        V3LinkageReport with match statistics and linked records.
    """
    opp_dir = Path(v3_dir or _V3_OPP_DIR)
    trade_dir = Path(shadow_dir or _SHADOW_DIR)

    # Load V3 observations
    observations = _load_v3_observations(opp_dir, symbol)
    if not observations:
        return V3LinkageReport()

    # Load shadow trades
    shadow_trades = _load_shadow_trades(trade_dir, symbol)

    # Build indexes
    entity_index: dict[str, dict] = {}
    correlation_index: dict[str, dict] = {}
    symbol_time_entries: dict[str, list[dict]] = {}

    for trade in shadow_trades:
        identity = trade.get("identity", {})
        outcome = trade.get("simulated_outcome", {})
        snap = trade.get("decision_snapshot", {})

        eid = identity.get("entity_id") or ""
        cid = identity.get("correlation_id") or ""
        sym = identity.get("symbol") or ""
        entry_time = snap.get("timestamp_decision_utc", 0)

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
            "direction": snap.get("direction", ""),
        }

        if eid:
            entity_index[eid] = trade_summary
        if cid:
            correlation_index[cid] = trade_summary
        if sym not in symbol_time_entries:
            symbol_time_entries[sym] = []
        symbol_time_entries[sym].append(trade_summary)

    # Match observations to trades
    linked: list[dict] = []
    match_entity = 0
    match_corr = 0
    match_time = 0
    unmatched_count = 0
    no_trade_count = 0

    for obs in observations:
        obs_corr = obs.get("correlation_id", "")
        obs_symbol = obs.get("symbol", "")
        obs_time = float(obs.get("timestamp_utc", 0))

        # Already linked — preserve
        if obs.get("outcome_linked"):
            linked.append(obs)
            continue

        # Match priority 1: entity_id
        match = entity_index.get(obs_corr)
        match_method = ""

        if match:
            match_method = "entity_id"
            match_entity += 1
        else:
            # Priority 2: correlation_id
            match = correlation_index.get(obs_corr)
            if match:
                match_method = "correlation_id"
                match_corr += 1
            else:
                # Priority 3: symbol + timestamp
                candidates = symbol_time_entries.get(obs_symbol, [])
                best = _find_nearest(candidates, obs_time)
                if best:
                    match = best
                    match_method = "timestamp"
                    match_time += 1

        if match:
            result_r = match["result_r"]
            obs["outcome_linked"] = True
            obs["outcome_raw_r"] = result_r
            obs["outcome_win"] = result_r > 0 if result_r is not None else None
            obs["outcome_mfe_r"] = match["mfe_r"]
            obs["outcome_mae_r"] = match["mae_r"]
            obs["outcome_exit_reason"] = match["exit_reason"]
            obs["outcome_bars_held"] = match["bars_held"]
            obs["_linkage"] = {
                "linked": True,
                "result_r": result_r,
                "win": result_r > 0 if result_r is not None else None,
                "mfe_r": match["mfe_r"],
                "mae_r": match["mae_r"],
                "hold_minutes": (match["bars_held"] or 0) * 5,
                "exit_reason": match["exit_reason"],
                "match_method": match_method,
                "trade_direction": match["direction"],
            }
            linked.append(obs)
        else:
            # No match — mark as NO_TRADE observation
            obs["_linkage"] = {
                "linked": False,
                "result_r": None,
                "reason": "NO_TRADE_MATCH",
            }
            linked.append(obs)
            unmatched_count += 1
            no_trade_count += 1

    # Persist
    if persist and linked:
        _persist_linked(linked, opp_dir)

    return V3LinkageReport(
        total_observations=len(observations),
        matched=match_entity + match_corr + match_time,
        unmatched=unmatched_count,
        match_by_entity_id=match_entity,
        match_by_correlation_id=match_corr,
        match_by_timestamp=match_time,
        linked_records=linked,
        no_trade_observations=no_trade_count,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# INTERNAL
# ═══════════════════════════════════════════════════════════════════════════════


def _load_v3_observations(base: Path, symbol: str | None) -> list[dict]:
    """Load V3Opportunity records."""
    if not base.exists():
        return []
    results: list[dict] = []
    dirs = [base / symbol] if symbol else [d for d in base.iterdir() if d.is_dir()]
    for dir_path in dirs:
        if not dir_path.exists():
            continue
        for fp in sorted(dir_path.glob("*.jsonl")):
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip():
                            try:
                                results.append(json.loads(line))
                            except json.JSONDecodeError:
                                continue
            except OSError:
                continue
    return results


def _load_shadow_trades(base: Path, symbol: str | None) -> list[dict]:
    """Load shadow trade records."""
    if not base.exists():
        return []
    results: list[dict] = []
    dirs = [base / symbol] if symbol else [d for d in base.iterdir() if d.is_dir()]
    for dir_path in dirs:
        if not dir_path.exists():
            continue
        for fp in sorted(dir_path.glob("*.jsonl")):
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip():
                            try:
                                rec = json.loads(line)
                                if "identity" in rec:
                                    results.append(rec)
                            except json.JSONDecodeError:
                                continue
            except OSError:
                continue
    return results


def _find_nearest(candidates: list[dict], target_time: float) -> dict | None:
    """Find nearest trade by timestamp within tolerance."""
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
    """Write linked records back to V3 opportunity files."""
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

    for key, recs in grouped.items():
        path = opp_dir / f"{key}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(path, "w", encoding="utf-8") as f:
                for rec in recs:
                    f.write(json.dumps(rec, separators=(",", ":"), default=str) + "\n")
        except OSError as exc:
            logger.debug("[V3_LINKAGE_PERSIST] failed for %s: %s", key, exc)
