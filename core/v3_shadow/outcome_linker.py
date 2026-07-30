"""
V3 Shadow Outcome Linker — Connects V3 shadow pipeline outputs to trade outcomes.

Links ExecutionAssessment records (and all upstream pipeline layers) to
observed shadow trade results via correlation_id / timestamp matching.

Answers: "Given a V3 shadow decision, what happened afterwards?"

This is RESEARCH INFRASTRUCTURE only. Does NOT:
    - Modify trading behaviour
    - Alter shadow trade creation
    - Change execution logic

Join key: {SYMBOL}_{bar_time} — same format used by V3 observer and shadow trades.
Match priority:
    1. correlation_id == shadow.identity.entity_id (exact)
    2. symbol + timestamp ±300s (fallback)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default directories
_SHADOW_DIR = "logs/shadow_trades"
_V3_SHADOW_BASE = "logs/v3_shadow"
_TIMESTAMP_TOLERANCE = 300  # 5 minutes


# ═══════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════


class V3ShadowLinkageReport:
    """Summary of V3 shadow outcome linkage run."""

    __slots__ = (
        "total_assessments", "matched", "unmatched",
        "match_by_correlation", "match_by_timestamp",
        "linked_records", "ready_executions", "not_executable",
    )

    def __init__(
        self,
        total_assessments: int = 0,
        matched: int = 0,
        unmatched: int = 0,
        match_by_correlation: int = 0,
        match_by_timestamp: int = 0,
        linked_records: list[dict] | None = None,
        ready_executions: int = 0,
        not_executable: int = 0,
    ):
        self.total_assessments = total_assessments
        self.matched = matched
        self.unmatched = unmatched
        self.match_by_correlation = match_by_correlation
        self.match_by_timestamp = match_by_timestamp
        self.linked_records = linked_records or []
        self.ready_executions = ready_executions
        self.not_executable = not_executable

    @property
    def match_rate(self) -> float:
        if self.total_assessments == 0:
            return 0.0
        return self.matched / self.total_assessments

    def summary(self) -> dict[str, Any]:
        return {
            "total_assessments": self.total_assessments,
            "matched": self.matched,
            "unmatched": self.unmatched,
            "match_rate": round(self.match_rate, 4),
            "by_correlation": self.match_by_correlation,
            "by_timestamp": self.match_by_timestamp,
            "ready_executions": self.ready_executions,
            "not_executable": self.not_executable,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════


def link_v3_shadow_outcomes(
    *,
    symbol: str | None = None,
    shadow_dir: str | None = None,
    v3_base_dir: str | None = None,
    persist: bool = True,
) -> V3ShadowLinkageReport:
    """
    Link V3 shadow pipeline ExecutionAssessments to shadow trade outcomes.

    Loads execution assessments, finds matching shadow trades, attaches
    outcome data (result_r, MFE, MAE, exit_reason, bars_held).

    Args:
        symbol: Filter to specific symbol (None = all)
        shadow_dir: Override shadow trade directory
        v3_base_dir: Override V3 shadow base directory
        persist: Write linked records back

    Returns:
        V3ShadowLinkageReport with match statistics and linked records.
    """
    s_dir = Path(shadow_dir or _SHADOW_DIR)
    v3_base = Path(v3_base_dir or _V3_SHADOW_BASE)
    exec_dir = v3_base / "execution_assessment"

    # Load execution assessments
    assessments = _load_jsonl_dir(exec_dir, symbol)
    if not assessments:
        return V3ShadowLinkageReport()

    # Load shadow trades
    shadow_trades = _load_shadow_trades(s_dir, symbol)

    # Build index
    entity_index: dict[str, dict] = {}
    symbol_time_index: dict[str, list[dict]] = {}

    for trade in shadow_trades:
        identity = trade.get("identity", {})
        outcome = trade.get("simulated_outcome", {})
        snap = trade.get("decision_snapshot", {})

        eid = identity.get("entity_id") or ""
        sym = identity.get("symbol") or ""
        entry_time = snap.get("timestamp_decision_utc", 0)

        trade_summary = {
            "entity_id": eid,
            "symbol": sym,
            "entry_time": entry_time,
            "direction": snap.get("direction", ""),
            "result_r": outcome.get("pnl_r_multiple"),
            "mfe_r": outcome.get("mfe_r"),
            "mae_r": outcome.get("mae_r"),
            "exit_reason": outcome.get("exit_reason", ""),
            "bars_held": outcome.get("bars_held", 0),
        }

        if eid:
            entity_index[eid] = trade_summary
        if sym not in symbol_time_index:
            symbol_time_index[sym] = []
        symbol_time_index[sym].append(trade_summary)

    # Match assessments to outcomes
    linked: list[dict] = []
    match_corr = 0
    match_time = 0
    unmatched_count = 0
    ready_count = 0
    not_exec_count = 0

    for assess in assessments:
        sym = assess.get("symbol", "")
        ts = float(assess.get("timestamp_utc", 0))
        exec_state = assess.get("execution_state", "")

        if exec_state == "READY_FOR_EXECUTION":
            ready_count += 1
        elif exec_state == "NOT_EXECUTABLE":
            not_exec_count += 1

        # Already linked — skip
        if assess.get("_outcome_linked"):
            linked.append(assess)
            continue

        # Build correlation key (same format as V3 observer)
        corr_key = f"{sym}_{int(ts)}" if sym and ts > 0 else ""

        # Match priority 1: correlation_id / entity_id
        match = entity_index.get(corr_key) if corr_key else None
        match_method = ""

        if match:
            match_method = "correlation"
            match_corr += 1
        else:
            # Priority 2: symbol + timestamp tolerance
            candidates = symbol_time_index.get(sym, [])
            best = _find_nearest(candidates, ts)
            if best:
                match = best
                match_method = "timestamp"
                match_time += 1

        if match:
            assess["_outcome_linked"] = True
            assess["_outcome"] = {
                "result_r": match["result_r"],
                "win": match["result_r"] > 0 if match["result_r"] is not None else None,
                "mfe_r": match["mfe_r"],
                "mae_r": match["mae_r"],
                "exit_reason": match["exit_reason"],
                "bars_held": match["bars_held"],
                "hold_minutes": (match["bars_held"] or 0) * 5,
                "match_method": match_method,
                "trade_direction": match["direction"],
            }
            linked.append(assess)
        else:
            assess["_outcome_linked"] = False
            assess["_outcome"] = {"result_r": None, "reason": "NO_MATCH"}
            linked.append(assess)
            unmatched_count += 1

    # Persist
    if persist and linked:
        _persist_linked(linked, exec_dir)

    return V3ShadowLinkageReport(
        total_assessments=len(assessments),
        matched=match_corr + match_time,
        unmatched=unmatched_count,
        match_by_correlation=match_corr,
        match_by_timestamp=match_time,
        linked_records=linked,
        ready_executions=ready_count,
        not_executable=not_exec_count,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# LOADING
# ═══════════════════════════════════════════════════════════════════════════════


def _load_jsonl_dir(base: Path, symbol: str | None) -> list[dict]:
    """Load JSONL records from a directory."""
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


def _persist_linked(records: list[dict], exec_dir: Path) -> None:
    """Write linked records back to execution assessment files."""
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
        path = exec_dir / f"{key}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(path, "w", encoding="utf-8") as f:
                for rec in recs:
                    f.write(json.dumps(rec, separators=(",", ":"), default=str) + "\n")
        except OSError as exc:
            logger.debug("[V3_SHADOW_LINKAGE_PERSIST] failed: %s", exc)
