"""
Research Operations — Persistent state.

Tracks operational research metadata across Lambda invocations and restarts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research_engine.v10.base import timestamp_now

_STATE_FILE = "data/research/research_state.json"


def get_research_state(state_file: str | None = None) -> dict[str, Any]:
    """Load current research state."""
    path = Path(state_file or _STATE_FILE)
    if not path.exists():
        return _default_state()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError):
        return _default_state()


def save_research_state(state: dict[str, Any], state_file: str | None = None) -> None:
    """Persist research state."""
    path = Path(state_file or _STATE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    state["last_updated"] = timestamp_now()
    path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def update_state_field(key: str, value: Any, state_file: str | None = None) -> None:
    """Update a single field in state."""
    state = get_research_state(state_file)
    state[key] = value
    save_research_state(state, state_file)


def _default_state() -> dict[str, Any]:
    return {
        "last_research_run": "",
        "last_campaign_run": "",
        "last_universe_update": "",
        "active_campaigns": [],
        "active_candidates": [],
        "active_shadow_tests": [],
        "latest_findings": [],
        "latest_recommendations": [],
        "data_version": "",
        "last_updated": "",
    }
