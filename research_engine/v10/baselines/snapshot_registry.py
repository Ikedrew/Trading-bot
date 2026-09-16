"""
Baseline Snapshot — Registry (storage + retrieval).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from research_engine.v10.baselines.models import BaselineSnapshot

logger = logging.getLogger(__name__)

_BASELINES_DIR = "data/baselines"

# Reserved non-snapshot files inside the baseline store (Wave 4C.1 durable
# active-baseline pointer). list_snapshots()/latest() skip these so the
# pointer never masquerades as a snapshot.
RESERVED_POINTER_FILENAMES = {"active_baseline.json"}


class SnapshotRegistry:
    """
    Stores and retrieves versioned baseline snapshots.

    Storage: data/baselines/{snapshot_id}.json
    """

    def __init__(self, baselines_dir: str | None = None):
        self._dir = Path(baselines_dir or _BASELINES_DIR)

    def save(self, snapshot: BaselineSnapshot) -> str:
        """
        Save a snapshot. Returns the file path.

        Wave 4C.1: written atomically (temp file + os.replace). On Windows
        NTFS and POSIX filesystems os.replace is atomic within a volume, so a
        reader can never observe a partially-written snapshot. If the process
        dies between temp-write and replace, only a harmless `.tmp` residue
        remains (ignored by load/list, which match `*.json` only).
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{snapshot.snapshot_id}.json"
        tmp_path = self._dir / f".{snapshot.snapshot_id}.json.tmp"
        payload = json.dumps(snapshot.to_dict(), indent=2, default=str)
        with open(tmp_path, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
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
        """List all snapshot IDs (sorted by name, newest last).

        Skips reserved non-snapshot files (e.g. the Wave 4C.1 durable
        active-baseline pointer) so they never appear as snapshots.
        """
        if not self._dir.exists():
            return []
        return sorted(
            f.stem for f in self._dir.glob("*.json")
            if f.name not in RESERVED_POINTER_FILENAMES
        )

    def latest(self) -> BaselineSnapshot | None:
        """Get the most recent snapshot."""
        ids = self.list_snapshots()
        if not ids:
            return None
        return self.load(ids[-1])

    def exists(self, snapshot_id: str) -> bool:
        return (self._dir / f"{snapshot_id}.json").exists()
