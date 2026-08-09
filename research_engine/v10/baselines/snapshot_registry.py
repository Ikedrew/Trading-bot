"""
Baseline Snapshot — Registry (storage + retrieval).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from research_engine.v10.baselines.models import BaselineSnapshot

logger = logging.getLogger(__name__)

_BASELINES_DIR = "data/baselines"


class SnapshotRegistry:
    """
    Stores and retrieves versioned baseline snapshots.

    Storage: data/baselines/{snapshot_id}.json
    """

    def __init__(self, baselines_dir: str | None = None):
        self._dir = Path(baselines_dir or _BASELINES_DIR)

    def save(self, snapshot: BaselineSnapshot) -> str:
        """Save a snapshot. Returns the file path."""
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{snapshot.snapshot_id}.json"
        path.write_text(json.dumps(snapshot.to_dict(), indent=2, default=str), encoding="utf-8")
        logger.info(f"[BASELINE] Saved: {path}")
        return str(path)

    def load(self, snapshot_id: str) -> BaselineSnapshot | None:
        """Load a snapshot by ID."""
        path = self._dir / f"{snapshot_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return BaselineSnapshot.from_dict(data)
        except (json.JSONDecodeError, IOError):
            return None

    def list_snapshots(self) -> list[str]:
        """List all snapshot IDs (sorted by name, newest last)."""
        if not self._dir.exists():
            return []
        return sorted(f.stem for f in self._dir.glob("*.json"))

    def latest(self) -> BaselineSnapshot | None:
        """Get the most recent snapshot."""
        ids = self.list_snapshots()
        if not ids:
            return None
        return self.load(ids[-1])

    def exists(self, snapshot_id: str) -> bool:
        return (self._dir / f"{snapshot_id}.json").exists()
