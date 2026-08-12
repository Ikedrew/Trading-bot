"""
Knowledge Store.

Persists knowledge items with immutable history.

Storage:
    reports/research/knowledge/
        {knowledge_id}/
            latest.json
            history/
                v{knowledge_version}.json
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from research_engine.v10.knowledge.model import KnowledgeItem, KnowledgeStatus, EvidenceRef

logger = logging.getLogger(__name__)

_DEFAULT_DIR = Path("reports/research/knowledge")


class KnowledgeStore:
    """
    Persists and retrieves knowledge items.

    Immutable history: every version is preserved.
    Latest: overwritten with newest version.
    """

    def __init__(self, base_dir: Path | str | None = None):
        self._base = Path(base_dir) if base_dir else _DEFAULT_DIR

    def save(self, item: KnowledgeItem) -> Path:
        """Save a knowledge item. Returns path to latest.json."""
        kid = item.knowledge_id or "unknown"
        kdir = self._base / kid
        kdir.mkdir(parents=True, exist_ok=True)
        (kdir / "history").mkdir(exist_ok=True)

        item_dict = item.to_dict()

        # Write latest
        latest_path = kdir / "latest.json"
        latest_path.write_text(json.dumps(item_dict, indent=2, default=str), encoding="utf-8")

        # Write immutable history
        history_path = kdir / "history" / f"v{item.knowledge_version}.json"
        if not history_path.exists():
            history_path.write_text(json.dumps(item_dict, indent=2, default=str), encoding="utf-8")

        return latest_path

    def save_batch(self, items: list[KnowledgeItem]) -> int:
        """Save multiple items. Returns count."""
        for item in items:
            self.save(item)
        return len(items)

    def load(self, knowledge_id: str) -> KnowledgeItem | None:
        """Load latest knowledge item."""
        path = self._base / knowledge_id / "latest.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return self._reconstruct(data)
        except Exception:
            return None

    def load_all(self) -> list[KnowledgeItem]:
        """Load all current knowledge items."""
        if not self._base.exists():
            return []
        items = []
        for kdir in sorted(self._base.iterdir()):
            if kdir.is_dir():
                item = self.load(kdir.name)
                if item:
                    items.append(item)
        return items

    def query_by_area(self, system_area: str) -> list[KnowledgeItem]:
        """Query knowledge by system area."""
        return [k for k in self.load_all() if k.system_area == system_area]

    def query_by_status(self, status: str) -> list[KnowledgeItem]:
        """Query knowledge by status."""
        return [k for k in self.load_all() if k.status == status]

    def query_weaknesses(self) -> list[KnowledgeItem]:
        """Return knowledge items that represent identified weaknesses."""
        return [
            k for k in self.load_all()
            if k.status in (KnowledgeStatus.CONTRADICTED.value,)
            or (k.status == KnowledgeStatus.SUPPORTED.value and "weakness" in k.statement.lower())
        ]

    def load_history(self, knowledge_id: str) -> list[KnowledgeItem]:
        """Load all historical versions of a knowledge item."""
        history_dir = self._base / knowledge_id / "history"
        if not history_dir.exists():
            return []
        items = []
        for f in sorted(history_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                item = self._reconstruct(data)
                if item:
                    items.append(item)
            except Exception:
                continue
        return items

    def _reconstruct(self, data: dict[str, Any]) -> KnowledgeItem | None:
        """Reconstruct a KnowledgeItem from persisted JSON."""
        if not data.get("knowledge_id"):
            return None

        supporting = [
            EvidenceRef(**e) for e in data.get("supporting_evidence", [])
        ]
        contradicting = [
            EvidenceRef(**e) for e in data.get("contradicting_evidence", [])
        ]

        return KnowledgeItem(
            knowledge_id=data.get("knowledge_id", ""),
            subject=data.get("subject", ""),
            system_area=data.get("system_area", ""),
            statement=data.get("statement", ""),
            status=data.get("status", KnowledgeStatus.UNRESOLVED.value),
            confidence=data.get("confidence", ""),
            supporting_evidence=supporting,
            contradicting_evidence=contradicting,
            evidence_count=data.get("evidence_count", 0),
            knowledge_version=data.get("knowledge_version", 1),
            first_observed_at=data.get("first_observed_at", ""),
            last_updated_at=data.get("last_updated_at", ""),
            source_universes=data.get("source_universes", []),
            universe_versions=data.get("universe_versions", {}),
            population_versions=data.get("population_versions", {}),
        )
