"""
Lifecycle Trace Persistence.

Persists lifecycle traces as immutable research artifacts.

Rules:
    - A persisted trace is never overwritten (immutable history)
    - Traces are stored by entity_id and trace_hash
    - The same trace_hash means the same evidence (deduplication)
    - A new trace for the same entity_id with different evidence
      creates a new history entry rather than overwriting
    - Traces can be reconstructed from persisted JSON
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from research_engine.v10.cross_universe.tracer import (
    LifecycleTrace,
    UniverseObservation,
    UniversePresence,
)

logger = logging.getLogger(__name__)

_DEFAULT_DIR = Path("reports/research/lifecycle_traces")


class LifecycleTraceStore:
    """
    Persists and retrieves lifecycle traces as immutable artifacts.

    Storage structure:
        reports/research/lifecycle_traces/
            {entity_id}/
                latest.json          — most recent trace (overwritten)
                history/
                    {trace_hash}.json — immutable historical trace

    This follows the same pattern as QuestionProductManager:
    latest.json for current state, history/ for immutable records.
    """

    def __init__(self, base_dir: Path | str | None = None):
        self._base = Path(base_dir) if base_dir else _DEFAULT_DIR

    def save(self, trace: LifecycleTrace) -> Path:
        """
        Persist a lifecycle trace.

        - Writes latest.json (overwritten with newest)
        - Writes history/{trace_hash}.json (immutable — never overwrites)

        Returns:
            Path to the saved latest.json.
        """
        if not trace.entity_id:
            raise ValueError("Cannot persist trace without entity_id")

        # Sanitise entity_id for filesystem (replace problematic chars)
        safe_id = self._safe_filename(trace.entity_id)
        trace_dir = self._base / safe_id
        trace_dir.mkdir(parents=True, exist_ok=True)
        (trace_dir / "history").mkdir(exist_ok=True)

        trace_dict = trace.to_dict()

        # Write latest.json
        latest_path = trace_dir / "latest.json"
        latest_path.write_text(
            json.dumps(trace_dict, indent=2, default=str),
            encoding="utf-8",
        )

        # Write history (immutable — skip if this exact hash already exists)
        trace_hash = trace.trace_hash
        history_path = trace_dir / "history" / f"{trace_hash}.json"
        if not history_path.exists():
            history_path.write_text(
                json.dumps(trace_dict, indent=2, default=str),
                encoding="utf-8",
            )

        return latest_path

    def load_latest(self, entity_id: str) -> LifecycleTrace | None:
        """Load the most recent trace for an entity_id."""
        safe_id = self._safe_filename(entity_id)
        latest_path = self._base / safe_id / "latest.json"
        if not latest_path.exists():
            return None
        try:
            data = json.loads(latest_path.read_text(encoding="utf-8"))
            return self._reconstruct(data)
        except Exception as e:
            logger.warning(f"[LIFECYCLE] Failed to load trace for {entity_id}: {e}")
            return None

    def load_history(self, entity_id: str) -> list[LifecycleTrace]:
        """Load all historical traces for an entity_id."""
        safe_id = self._safe_filename(entity_id)
        history_dir = self._base / safe_id / "history"
        if not history_dir.exists():
            return []
        traces = []
        for f in sorted(history_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                trace = self._reconstruct(data)
                if trace:
                    traces.append(trace)
            except Exception:
                continue
        return traces

    def has_trace(self, entity_id: str) -> bool:
        """Check if any trace exists for an entity_id."""
        safe_id = self._safe_filename(entity_id)
        return (self._base / safe_id / "latest.json").exists()

    def list_entities(self) -> list[str]:
        """List all entity_ids with persisted traces."""
        if not self._base.exists():
            return []
        return sorted(
            d.name for d in self._base.iterdir()
            if d.is_dir() and (d / "latest.json").exists()
        )

    def _reconstruct(self, data: dict[str, Any]) -> LifecycleTrace | None:
        """Reconstruct a LifecycleTrace from persisted JSON."""
        entity_id = data.get("entity_id", "")
        if not entity_id:
            return None

        universes: dict[str, UniverseObservation] = {}
        for key, obs_data in data.get("universes", {}).items():
            universes[key] = UniverseObservation(
                universe=obs_data.get("universe", key.upper()),
                presence=obs_data.get("presence", UniversePresence.MISSING),
                record=obs_data.get("record"),
            )

        return LifecycleTrace(
            entity_id=entity_id,
            trace_status=data.get("trace_status", ""),
            universes=universes,
            present_count=data.get("present_count", 0),
            missing_count=data.get("missing_count", 0),
            universe_versions=data.get("universe_versions", {}),
        )

    def _safe_filename(self, entity_id: str) -> str:
        """Convert entity_id to a filesystem-safe directory name."""
        # Replace characters that are problematic on Windows
        safe = entity_id.replace("/", "_").replace("\\", "_").replace(":", "_")
        # Truncate if excessively long
        if len(safe) > 200:
            safe = safe[:200]
        return safe
