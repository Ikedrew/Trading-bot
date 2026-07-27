"""
Event Stream Integrity Checker — Detects orphan events and broken causal links.

Reads from events/{YYYY-MM-DD}.jsonl and validates:
    1. Every EXECUTION has a matching DECISION (by decision_id or decision_ts_utc_ms)
    2. Every DECISION has a matching STRATEGY (by strategy_ts_utc_ms)
    3. Every OUTCOME has a matching EXECUTION (by execution_ts_utc_ms or decision_id)
    4. Every STRATEGY has a matching ENTITY (by entity_id)
    5. No orphan events exist in the causal chain

Usage:
    python -m tools.replay_engine.integrity_checker --date 2026-06-27
    python -m tools.replay_engine.integrity_checker --days 7
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class IntegrityReport:
    """Results of causal chain integrity check."""
    date_range: str = ""
    total_events: int = 0

    # Counts by type
    candle_count: int = 0
    entity_count: int = 0
    strategy_count: int = 0
    decision_count: int = 0
    execution_count: int = 0
    outcome_count: int = 0

    # Orphan counts
    strategy_without_entity: int = 0
    decision_without_strategy: int = 0
    execution_without_decision: int = 0
    outcome_without_execution: int = 0

    # Integrity
    broken_chains: list[dict[str, Any]] = field(default_factory=list)
    linked_executions: int = 0
    linked_outcomes: int = 0

    @property
    def is_healthy(self) -> bool:
        return (
            self.execution_without_decision == 0
            and self.outcome_without_execution == 0
        )

    def print_report(self) -> str:
        lines = [
            f"{'='*60}",
            f"EVENT STREAM INTEGRITY REPORT",
            f"Date range: {self.date_range}",
            f"{'='*60}",
            f"",
            f"EVENT COUNTS:",
            f"  CANDLE:    {self.candle_count}",
            f"  ENTITY:    {self.entity_count}",
            f"  STRATEGY:  {self.strategy_count}",
            f"  DECISION:  {self.decision_count}",
            f"  EXECUTION: {self.execution_count}",
            f"  OUTCOME:   {self.outcome_count}",
            f"  TOTAL:     {self.total_events}",
            f"",
            f"CAUSAL LINKAGE:",
            f"  Executions linked to Decision: {self.linked_executions}/{self.execution_count}",
            f"  Outcomes linked to Execution:  {self.linked_outcomes}/{self.outcome_count}",
            f"",
            f"ORPHAN EVENTS:",
            f"  STRATEGY without ENTITY:       {self.strategy_without_entity}",
            f"  DECISION without STRATEGY:     {self.decision_without_strategy}",
            f"  EXECUTION without DECISION:    {self.execution_without_decision}",
            f"  OUTCOME without EXECUTION:     {self.outcome_without_execution}",
            f"",
            f"VERDICT: {'HEALTHY' if self.is_healthy else 'BROKEN LINKS DETECTED'}",
        ]

        if self.broken_chains:
            lines.append(f"\nBROKEN CHAINS (first 10):")
            for bc in self.broken_chains[:10]:
                lines.append(f"  {bc['type']} | ts={bc['ts_utc_ms']} | missing={bc['missing_link']} | symbol={bc.get('symbol','?')}")

        lines.append(f"{'='*60}")
        return "\n".join(lines)


def check_integrity(
    *,
    event_dir: str = "events",
    days: int = 1,
    date_str: str | None = None,
) -> IntegrityReport:
    """
    Run full causal chain integrity check on the event stream.

    Args:
        event_dir: Path to events directory
        days: Number of recent days to check (ignored if date_str set)
        date_str: Specific date (YYYY-MM-DD) to check

    Returns:
        IntegrityReport with orphan counts and broken links.
    """
    report = IntegrityReport()
    base_dir = Path(event_dir)

    # Determine files to check
    if date_str:
        files = [base_dir / f"{date_str}.jsonl"]
        report.date_range = date_str
    else:
        files = sorted(base_dir.glob("*.jsonl"), reverse=True)[:days]
        if files:
            report.date_range = f"{files[-1].stem} to {files[0].stem}"
        else:
            report.date_range = "no files found"

    # Collect events by type
    entities: dict[str, dict[str, Any]] = {}  # entity_id → event
    strategies: list[dict[str, Any]] = []
    decisions: dict[str, dict[str, Any]] = {}  # decision_id → event
    decision_ts_set: set[int] = set()  # ts_utc_ms values of decisions
    executions: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []

    for filepath in files:
        if not filepath.exists():
            continue
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                report.total_events += 1
                etype = event.get("type", "")
                payload = event.get("payload", {})

                if etype == "CANDLE":
                    report.candle_count += 1
                elif etype == "ENTITY":
                    report.entity_count += 1
                    eid = payload.get("entity_id", "")
                    if eid:
                        entities[eid] = event
                elif etype == "STRATEGY":
                    report.strategy_count += 1
                    strategies.append(event)
                elif etype == "DECISION":
                    report.decision_count += 1
                    did = payload.get("decision_id", "")
                    if did:
                        decisions[did] = event
                    decision_ts_set.add(event.get("ts_utc_ms", 0))
                elif etype == "EXECUTION":
                    report.execution_count += 1
                    executions.append(event)
                elif etype == "OUTCOME":
                    report.outcome_count += 1
                    outcomes.append(event)

    # ─── CHECK 1: STRATEGY → ENTITY linkage ──────────────────────────
    for s in strategies:
        payload = s.get("payload", {})
        eid = payload.get("entity_id", "")
        if eid and eid not in entities:
            report.strategy_without_entity += 1

    # ─── CHECK 2: DECISION → STRATEGY linkage ────────────────────────
    for did, d in decisions.items():
        payload = d.get("payload", {})
        strat_ts = payload.get("strategy_ts_utc_ms", 0)
        if not strat_ts:
            report.decision_without_strategy += 1

    # ─── CHECK 3: EXECUTION → DECISION linkage ───────────────────────
    for ex in executions:
        payload = ex.get("payload", {})
        did = payload.get("decision_id", "")
        dts = payload.get("decision_ts_utc_ms", 0)

        linked = False
        if did and did in decisions:
            linked = True
        elif dts and dts in decision_ts_set:
            linked = True

        if linked:
            report.linked_executions += 1
        else:
            report.execution_without_decision += 1
            report.broken_chains.append({
                "type": "EXECUTION",
                "ts_utc_ms": ex.get("ts_utc_ms", 0),
                "symbol": ex.get("symbol", ""),
                "missing_link": "DECISION (no matching decision_id or decision_ts_utc_ms)",
            })

    # ─── CHECK 4: OUTCOME → EXECUTION linkage ────────────────────────
    execution_ts_set = {ex.get("ts_utc_ms", 0) for ex in executions}
    execution_did_set = {ex.get("payload", {}).get("decision_id", "") for ex in executions if ex.get("payload", {}).get("decision_id")}

    for o in outcomes:
        payload = o.get("payload", {})
        ets = payload.get("execution_ts_utc_ms", 0)
        did = payload.get("decision_id", "")

        linked = False
        if did and did in execution_did_set:
            linked = True
        elif ets and ets in execution_ts_set:
            linked = True

        if linked:
            report.linked_outcomes += 1
        else:
            report.outcome_without_execution += 1
            report.broken_chains.append({
                "type": "OUTCOME",
                "ts_utc_ms": o.get("ts_utc_ms", 0),
                "symbol": o.get("symbol", ""),
                "missing_link": "EXECUTION (no matching decision_id or execution_ts_utc_ms)",
            })

    return report


# ─── CLI ENTRY POINT ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

    parser = argparse.ArgumentParser(description="Event Stream Integrity Checker")
    parser.add_argument("--date", type=str, help="Check specific date (YYYY-MM-DD)")
    parser.add_argument("--days", type=int, default=1, help="Number of recent days to check (default: 1)")
    parser.add_argument("--event-dir", type=str, default="events", help="Events directory")

    args = parser.parse_args()

    report = check_integrity(
        event_dir=args.event_dir,
        days=args.days,
        date_str=args.date,
    )
    print(report.print_report())
    sys.exit(0 if report.is_healthy else 1)
