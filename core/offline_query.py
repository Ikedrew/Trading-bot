"""
Offline Query Engine — Read-only analytics over persisted S3 truth data.

Operates ONLY on immutable persisted datasets. Never accesses live feeds,
MT5, execution engine, or modifies strategies.

Data Source (only):
    logs/trade_journal/  (realised-outcome nodes; the canonical V1 owner)

Standard Queries:
    1. Expectancy (by strategy/pattern/HTF)
    2. Regime Performance
    3. Edge Decay (week-over-week)
    4. HTF Contribution

Usage:
    from core.offline_query import OfflineQueryEngine

    q = OfflineQueryEngine()
    print(q.expectancy(group_by="strategy"))
    print(q.edge_decay(weeks=3))
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_GRAPH_DIR = "logs/trade_journal"  # migrated from retired trade_truth_graph dataset


# ═══════════════════════════════════════════════════════════════════════════════
# DATA LOADING + COMPLETENESS FILTER
# ═══════════════════════════════════════════════════════════════════════════════

def _load_jsonl_dir(base_dir: str, symbol: str | None = None, date_from: str | None = None, date_to: str | None = None) -> list[dict[str, Any]]:
    """
    Load JSONL records from a directory tree, optionally filtered.

    RE-HYDRATION IMMUTABILITY: Snapshot fields are re-frozen on load to
    ensure disk-loaded data has identical immutability guarantees as runtime data.
    """
    records: list[dict[str, Any]] = []
    path = Path(base_dir)
    if not path.exists():
        return records

    for f in sorted(path.rglob("*.jsonl")):
        # Symbol filter (directory name)
        if symbol and symbol not in str(f):
            continue
        # Date filter (filename)
        fname = f.stem
        if date_from and fname < date_from:
            continue
        if date_to and fname > date_to:
            continue

        with open(f, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    # Re-freeze snapshot fields on deserialization
                    _refreeze_record(record)
                    records.append(record)
                except json.JSONDecodeError:
                    continue
    return records


def _refreeze_record(record: dict[str, Any]) -> None:
    """Re-freeze mutable snapshot fields after JSON deserialization."""
    from types import MappingProxyType

    for key in ("htf_snapshot", "strategy_meta"):
        val = record.get(key)
        if isinstance(val, dict):
            record[key] = _freeze_nested(val)


def _freeze_nested(d: dict) -> "MappingProxyType":
    """Recursively freeze a dict."""
    from types import MappingProxyType
    frozen = {}
    for k, v in d.items():
        if isinstance(v, dict):
            frozen[k] = _freeze_nested(v)
        elif isinstance(v, list):
            frozen[k] = tuple(_freeze_nested(i) if isinstance(i, dict) else i for i in v)
        else:
            frozen[k] = v
    return MappingProxyType(frozen)


def _completeness_filter(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Only include records with complete lifecycle (exit + R-multiple present)."""
    valid = []
    for r in records:
        ts = r.get("timestamps", {})
        outcome = r.get("outcome", {})
        if ts.get("exit_time") and outcome.get("r_multiple") is not None:
            valid.append(r)
    return valid


def _adapt_trade_journal(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Adapt trade_journal records into the graph-node shape this engine expects.

    Migrated from the retired trade_truth_graph dataset (Production V1
    consolidation). trade_journal is the correct owner of realised outcome +
    management fields (the old graph nodes never carried r_multiple).
    """
    adapted: list[dict[str, Any]] = []
    for rec in records:
        entry = rec.get("entry_price")
        exit_p = rec.get("exit_price")
        sl = rec.get("initial_sl")
        direction = rec.get("direction", "")
        if entry is None or exit_p is None or not sl or not direction:
            continue
        risk = abs(float(entry) - float(sl))
        if risk <= 0:
            continue
        if direction.upper() == "BUY":
            r_mult = (float(exit_p) - float(entry)) / risk
        else:
            r_mult = (float(entry) - float(exit_p)) / risk
        adapted.append({
            "trade_id": rec.get("trade_id", ""),
            "symbol": rec.get("symbol", ""),
            "timestamps": {
                "entry_time": rec.get("entry_time"),
                "exit_time": rec.get("exit_time"),
            },
            "outcome": {"r_multiple": round(r_mult, 4), "bars_held": rec.get("bars_held", 0)},
            "strategy_meta": {
                "strategy": rec.get("strategy", rec.get("pattern_name", "UNKNOWN")),
                "pattern": rec.get("pattern_name", "UNKNOWN"),
            },
            "htf_snapshot": rec.get("htf_snapshot", {}),
            "edges": {"regime": rec.get("regime", "UNKNOWN")},
        })
    return adapted


# ═══════════════════════════════════════════════════════════════════════════════
# QUERY ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class OfflineQueryEngine:
    """Read-only analytics engine over persisted truth data."""

    def __init__(
        self,
        *,
        graph_dir: str = _GRAPH_DIR,
        symbol: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ):
        self._graph_dir = graph_dir
        self._symbol = symbol
        self._date_from = date_from
        self._date_to = date_to
        self._nodes: list[dict] | None = None

    def _load(self) -> list[dict[str, Any]]:
        """Lazy-load and cache filtered dataset."""
        if self._nodes is None:
            raw = _load_jsonl_dir(self._graph_dir, self._symbol, self._date_from, self._date_to)
            # Adapt trade_journal records into the graph-node shape (consolidation).
            adapted = _adapt_trade_journal(raw)
            self._nodes = _completeness_filter(adapted)
        return self._nodes

    @property
    def trade_count(self) -> int:
        return len(self._load())

    # ─── QUERY 1: EXPECTANCY ──────────────────────────────────────────

    def expectancy(self, *, group_by: str = "strategy") -> dict[str, Any]:
        """
        Average R by grouping dimension.

        group_by: "strategy" | "pattern" | "htf_alignment" | "session" | "regime"
        """
        nodes = self._load()
        if not nodes:
            return {"metric": "expectancy", "value": 0, "sample_size": 0, "confidence": "NONE"}

        groups: dict[str, list[float]] = defaultdict(list)
        for n in nodes:
            key = self._extract_group_key(n, group_by)
            groups[key].append(n.get("outcome", {}).get("r_multiple", 0))

        result = {}
        for key, values in sorted(groups.items()):
            wins = sum(1 for v in values if v > 0)
            result[key] = {
                "avg_r": round(sum(values) / len(values), 4),
                "win_rate": round(wins / len(values), 4),
                "sample_size": len(values),
                "total_r": round(sum(values), 4),
                "confidence": "HIGH" if len(values) >= 30 else "MEDIUM" if len(values) >= 10 else "LOW",
            }

        return {"metric": "expectancy", "group_by": group_by, "results": result, "total_trades": len(nodes)}

    # ─── QUERY 2: REGIME PERFORMANCE ──────────────────────────────────

    def regime_performance(self) -> dict[str, Any]:
        """Group performance by regime (trending/ranging/chop)."""
        nodes = self._load()
        groups: dict[str, list[float]] = defaultdict(list)

        for n in nodes:
            regime = n.get("edges", {}).get("regime", n.get("htf_snapshot", {}).get("H4", {}).get("regime", "UNKNOWN"))
            groups[regime].append(n.get("outcome", {}).get("r_multiple", 0))

        result = {}
        for regime, values in sorted(groups.items()):
            wins = sum(1 for v in values if v > 0)
            # Drawdown proxy: worst consecutive R sum
            worst_run = 0.0
            current_run = 0.0
            for v in values:
                if v < 0:
                    current_run += v
                    worst_run = min(worst_run, current_run)
                else:
                    current_run = 0
            result[regime] = {
                "avg_r": round(sum(values) / len(values), 4),
                "win_rate": round(wins / len(values), 4),
                "drawdown_proxy": round(worst_run, 4),
                "sample_size": len(values),
            }

        return {"metric": "regime_performance", "results": result}

    # ─── QUERY 3: EDGE DECAY ─────────────────────────────────────────

    def edge_decay(self, *, weeks: int = 3) -> dict[str, Any]:
        """Compare feature EV week-over-week to detect decay."""
        nodes = self._load()
        if not nodes:
            return {"metric": "edge_decay", "weeks_compared": 0, "results": []}

        # Bucket by week
        by_week: dict[int, list[dict]] = defaultdict(list)
        now = datetime.now(timezone.utc)

        for n in nodes:
            exit_time = n.get("timestamps", {}).get("exit_time", 0)
            if not exit_time:
                continue
            try:
                trade_dt = datetime.fromtimestamp(exit_time, tz=timezone.utc)
                weeks_ago = (now - trade_dt).days // 7
                if weeks_ago < weeks:
                    by_week[weeks_ago].append(n)
            except (OSError, ValueError):
                continue

        weekly_ev: list[dict[str, Any]] = []
        for week_num in range(weeks):
            week_nodes = by_week.get(week_num, [])
            if not week_nodes:
                weekly_ev.append({"week": f"W-{week_num}", "avg_r": 0, "trades": 0})
                continue
            r_vals = [n.get("outcome", {}).get("r_multiple", 0) for n in week_nodes]
            wins = sum(1 for r in r_vals if r > 0)
            weekly_ev.append({
                "week": f"W-{week_num}",
                "avg_r": round(sum(r_vals) / len(r_vals), 4),
                "win_rate": round(wins / len(r_vals), 4),
                "trades": len(r_vals),
            })

        # Detect decay
        decay_detected = False
        if len(weekly_ev) >= 2:
            evs = [w["avg_r"] for w in weekly_ev if w["trades"] > 0]
            if len(evs) >= 2 and evs[0] < evs[-1] * 0.7:
                decay_detected = True

        return {"metric": "edge_decay", "weeks_compared": weeks, "weekly": weekly_ev, "decay_detected": decay_detected}


    # ─── QUERY 4: HTF CONTRIBUTION ───────────────────────────────────

    def htf_contribution(self) -> dict[str, Any]:
        """Compare R when HTF aligned vs not aligned."""
        nodes = self._load()
        aligned_r: list[float] = []
        not_aligned_r: list[float] = []

        for n in nodes:
            htf = n.get("htf_snapshot", {})
            alignment = htf.get("alignment_score", 0.5)
            r = n.get("outcome", {}).get("r_multiple", 0)

            if alignment >= 0.7:
                aligned_r.append(r)
            else:
                not_aligned_r.append(r)

        def _stats(values: list[float]) -> dict:
            if not values:
                return {"avg_r": 0, "win_rate": 0, "trades": 0}
            wins = sum(1 for v in values if v > 0)
            return {
                "avg_r": round(sum(values) / len(values), 4),
                "win_rate": round(wins / len(values), 4),
                "trades": len(values),
            }

        aligned_stats = _stats(aligned_r)
        not_aligned_stats = _stats(not_aligned_r)
        delta_r = round(aligned_stats["avg_r"] - not_aligned_stats["avg_r"], 4)
        delta_wr = round(aligned_stats["win_rate"] - not_aligned_stats["win_rate"], 4)

        return {
            "metric": "htf_contribution",
            "aligned": aligned_stats,
            "not_aligned": not_aligned_stats,
            "delta_r": delta_r,
            "delta_win_rate": delta_wr,
            "htf_matters": delta_r > 0.2,
        }

    # ─── HELPERS ──────────────────────────────────────────────────────

    def _extract_group_key(self, node: dict[str, Any], group_by: str) -> str:
        """Extract grouping key from a trade node."""
        if group_by == "strategy":
            return node.get("strategy_meta", {}).get("strategy", node.get("edges", {}).get("strategy", "UNKNOWN"))
        elif group_by == "pattern":
            return node.get("strategy_meta", {}).get("pattern", node.get("edges", {}).get("pattern", "UNKNOWN"))
        elif group_by == "htf_alignment":
            score = node.get("htf_snapshot", {}).get("alignment_score", 0)
            if score >= 0.8:
                return "HIGH"
            elif score >= 0.5:
                return "MEDIUM"
            return "LOW"
        elif group_by == "session":
            return node.get("edges", {}).get("session", "UNKNOWN")
        elif group_by == "regime":
            return node.get("edges", {}).get("regime", "UNKNOWN")
        return "ALL"

    # ─── FULL REPORT ──────────────────────────────────────────────────

    def full_report(self) -> dict[str, Any]:
        """Run all 5 standard queries and return unified report."""
        return {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "total_trades": self.trade_count,
            "expectancy_by_strategy": self.expectancy(group_by="strategy"),
            "expectancy_by_pattern": self.expectancy(group_by="pattern"),
            "expectancy_by_session": self.expectancy(group_by="session"),
            "regime_performance": self.regime_performance(),
            "edge_decay": self.edge_decay(weeks=3),
            "htf_contribution": self.htf_contribution(),
        }


def export_report(report: dict[str, Any], path: str = "analysis/reports/offline_query.json") -> str:
    """Export offline query report."""
    filepath = Path(path)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    return str(filepath)
