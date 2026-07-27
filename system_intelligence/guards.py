"""
Guard Statistics — Answers: "Which guards block the most trades?"

Reads from: logs/decision_ledger/ (RISK_BLOCK records)
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


_LEDGER_DIR = Path("logs/decision_ledger")


def get_guard_statistics(days: int = 7) -> dict[str, Any]:
    """
    Count guard blocks by guard name from decision_ledger.

    Returns:
        - total_blocks
        - by_guard: {guard_name: count}
        - by_symbol: {symbol: count}
        - most_blocking_guard
        - period_days
    """
    if not _LEDGER_DIR.exists():
        return {"total_blocks": 0, "by_guard": {}, "by_symbol": {}, "period_days": days}

    guard_counts: Counter = Counter()
    symbol_counts: Counter = Counter()
    total = 0

    # Read all ledger files (flatten across symbols)
    for f in sorted(_LEDGER_DIR.rglob("*.jsonl")):
        try:
            for line in open(f, encoding="utf-8"):
                line = line.strip()
                if not line:
                    continue
                if "RISK_BLOCK" not in line:
                    continue
                rec = json.loads(line)
                if rec.get("decision") == "RISK_BLOCK":
                    guard = rec.get("risk_flag", "unknown")
                    symbol = rec.get("symbol", "UNKNOWN")
                    guard_counts[guard] += 1
                    symbol_counts[symbol] += 1
                    total += 1
        except Exception:
            continue

    most_blocking = guard_counts.most_common(1)[0][0] if guard_counts else "none"

    return {
        "total_blocks": total,
        "by_guard": dict(guard_counts.most_common()),
        "by_symbol": dict(symbol_counts.most_common()),
        "most_blocking_guard": most_blocking,
        "period_days": days,
    }
