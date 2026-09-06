"""
Cockpit Refresh — Single canonical operation to synchronize all cockpit state.

This is the ONE entry point for cockpit regeneration. It:
    1. Locates the latest persisted research run manifest (canonical source).
    2. Synchronizes control_plane_state.json with that canonical run.
    3. Generates the local cockpit HTML.

RETIREMENT NOTE (Gap 9 final architecture cleanup): the former step 4
(publish to the legacy V10 research bucket) has been RETIRED. The cockpit
HTML is a derived LOCAL artifact only; the scheduled cycle calls this with
skip_s3=True and no active caller requests an S3 publish.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_RUNS_DIR = Path("reports/research/runs")
_STATE_PATH = Path("reports/research/control_plane_state.json")
_LOCAL_COCKPIT = Path("reports/research/cockpit.html")


@dataclass
class CockpitRefreshResult:
    """Result of a cockpit refresh operation."""
    success: bool = False
    error: str = ""
    latest_run_id: str = ""
    latest_run_timestamp: str = ""
    latest_run_duration: float = 0.0
    local_path: str = ""
    s3_path: str = ""
    s3_published: bool = False
    s3_error: str = ""


def refresh_cockpit(skip_s3: bool = False) -> CockpitRefreshResult:
    """
    Single canonical cockpit refresh operation.

    1. Locate latest persisted run manifest.
    2. Synchronize control_plane_state.json.
    3. Generate local cockpit HTML.
    4. Publish to S3 (unless skip_s3=True).

    Returns:
        CockpitRefreshResult with status of each step.
    """
    result = CockpitRefreshResult()

    # Step 1: Locate latest run manifest
    manifest = _get_latest_run_manifest()
    if manifest is None:
        result.error = "No persisted research run found in reports/research/runs/"
        return result

    result.latest_run_id = manifest.get("run_id", "")
    result.latest_run_timestamp = manifest.get("timestamp", "")
    result.latest_run_duration = manifest.get("duration_seconds", 0.0)

    # Step 2: Synchronize control_plane_state.json
    _sync_control_plane_state(manifest)

    # Step 3: Generate local cockpit HTML
    try:
        from research_engine.v10.cockpit.generator import generate_cockpit
        path = generate_cockpit()
        result.local_path = str(path)
    except Exception as e:
        result.error = f"Cockpit generation failed: {e}"
        return result

    # Step 4: S3 publish RETIRED (old v10-engine bucket) — cockpit is
    # local-only. skip_s3 is retained for call-site compatibility.
    result.s3_path = "(retired - local only)"

    result.success = True
    return result


def _get_latest_run_manifest() -> dict[str, Any] | None:
    """Load the most recent persisted run manifest (canonical source of truth)."""
    if not _RUNS_DIR.exists():
        return None
    runs = sorted(_RUNS_DIR.glob("*.json"), reverse=True)
    if not runs:
        return None
    try:
        return json.loads(runs[0].read_text(encoding="utf-8"))
    except Exception:
        return None


def _sync_control_plane_state(manifest: dict[str, Any]) -> None:
    """
    Synchronize control_plane_state.json with the canonical latest run.

    Preserves all existing non-run fields (universe health, population counts, etc.).
    Only updates run-identity fields to match the canonical manifest.
    """
    if _STATE_PATH.exists():
        try:
            state = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            state = {}
    else:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        state = {}

    # Update only run-related fields
    state["last_run_id"] = manifest.get("run_id", "")
    state["last_run_timestamp"] = manifest.get("timestamp", "")
    state["last_updated"] = manifest.get("timestamp", "")
    state["latest_run"] = manifest

    # Preserve engine_version if not already set
    if "engine_version" not in state:
        state["engine_version"] = manifest.get("engine_version", "1.0.0")

    _STATE_PATH.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")

