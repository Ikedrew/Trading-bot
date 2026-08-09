"""
V10 Research Baseline Tracking.

Stores and compares research cycle baselines to track system improvement.

Storage: reports/research_cycles/baseline.json (latest)
         reports/research_cycles/baseline_history.jsonl (all)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_BASELINE_DIR = "reports/research_cycles"
_BASELINE_FILE = "baseline.json"
_HISTORY_FILE = "baseline_history.jsonl"


def load_baseline() -> dict[str, Any] | None:
    """Load the most recent baseline. Returns None if none exists."""
    path = Path(_BASELINE_DIR) / _BASELINE_FILE
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError):
        return None


def save_baseline(data: dict[str, Any]) -> None:
    """Save a new baseline (overwrites latest, appends to history)."""
    base_dir = Path(_BASELINE_DIR)
    base_dir.mkdir(parents=True, exist_ok=True)

    # Overwrite latest
    (base_dir / _BASELINE_FILE).write_text(
        json.dumps(data, indent=2, default=str), encoding="utf-8"
    )

    # Append to history
    history_path = base_dir / _HISTORY_FILE
    with open(history_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(data, default=str) + "\n")


def compare_baselines() -> dict[str, Any]:
    """
    Compare current baseline against previous.

    Returns comparison dict with changes.
    """
    history_path = Path(_BASELINE_DIR) / _HISTORY_FILE
    if not history_path.exists():
        return {"status": "NO_PREVIOUS", "message": "No previous cycle to compare"}

    lines = [l for l in history_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if len(lines) < 2:
        return {"status": "NO_PREVIOUS", "message": "Only one cycle recorded"}

    try:
        current = json.loads(lines[-1])
        previous = json.loads(lines[-2])
    except json.JSONDecodeError:
        return {"status": "ERROR", "message": "Failed to parse baseline history"}

    changes = {}

    # Trade count
    prev_n = previous.get("trade_count", 0)
    curr_n = current.get("trade_count", 0)
    if curr_n != prev_n:
        changes["trade_count"] = f"{prev_n} → {curr_n} ({curr_n - prev_n:+d})"

    # Expectancy
    prev_exp = previous.get("expectancy_r", 0)
    curr_exp = current.get("expectancy_r", 0)
    diff_exp = curr_exp - prev_exp
    direction = "IMPROVED" if diff_exp > 0.05 else ("REGRESSED" if diff_exp < -0.05 else "UNCHANGED")
    changes["expectancy"] = f"{prev_exp:+.4f}R → {curr_exp:+.4f}R ({diff_exp:+.4f}R) [{direction}]"

    # Win rate
    prev_wr = previous.get("win_rate", 0)
    curr_wr = current.get("win_rate", 0)
    changes["win_rate"] = f"{prev_wr:.0%} → {curr_wr:.0%}"

    # Profit factor
    prev_pf = previous.get("profit_factor", 0)
    curr_pf = current.get("profit_factor", 0)
    changes["profit_factor"] = f"{prev_pf:.2f} → {curr_pf:.2f}"

    # Experiment conclusion changes
    prev_exps = previous.get("experiments", {})
    curr_exps = current.get("experiments", {})
    exp_changes = {}
    for exp_id in set(list(prev_exps.keys()) + list(curr_exps.keys())):
        p = prev_exps.get(exp_id, "N/A")
        c = curr_exps.get(exp_id, "N/A")
        if p != c:
            exp_changes[exp_id] = f"{p} → {c}"
    if exp_changes:
        changes["experiment_changes"] = exp_changes

    overall = "IMPROVED" if diff_exp > 0.05 else ("REGRESSED" if diff_exp < -0.05 else "STABLE")

    return {
        "status": overall,
        "previous_cycle": previous.get("cycle_id", "?"),
        "current_cycle": current.get("cycle_id", "?"),
        "changes": changes,
    }
