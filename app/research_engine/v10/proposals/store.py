"""
Proposal / Validation Persistence.

Stores proposals, validation results, and promotion decisions
with immutable history.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_DIR = Path("reports/research/proposals")


class ProposalStore:
    """
    Persists proposals, validations, and promotion decisions.

    Storage:
        reports/research/proposals/
            {proposal_id}/
                proposal.json
                candidate.json
                validation.json
                promotion.json
                history/
                    {artifact_type}_{version}.json
    """

    def __init__(self, base_dir: Path | str | None = None):
        self._base = Path(base_dir) if base_dir else _DEFAULT_DIR

    def save_proposal(self, proposal_dict: dict[str, Any]) -> Path:
        pid = proposal_dict.get("proposal_id", "unknown")
        pdir = self._base / pid
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "history").mkdir(exist_ok=True)
        path = pdir / "proposal.json"
        path.write_text(json.dumps(proposal_dict, indent=2, default=str), encoding="utf-8")
        # Immutable history
        hist = pdir / "history" / f"proposal_{pid}.json"
        if not hist.exists():
            hist.write_text(json.dumps(proposal_dict, indent=2, default=str), encoding="utf-8")
        return path

    def save_validation(self, validation_dict: dict[str, Any]) -> Path:
        pid = validation_dict.get("proposal_id", "unknown")
        pdir = self._base / pid
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "history").mkdir(exist_ok=True)
        path = pdir / "validation.json"
        path.write_text(json.dumps(validation_dict, indent=2, default=str), encoding="utf-8")
        vid = validation_dict.get("validation_id", "unknown")
        hist = pdir / "history" / f"validation_{vid}.json"
        if not hist.exists():
            hist.write_text(json.dumps(validation_dict, indent=2, default=str), encoding="utf-8")
        return path

    def save_promotion(self, promotion_dict: dict[str, Any]) -> Path:
        pid = promotion_dict.get("proposal_id", "unknown")
        pdir = self._base / pid
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "history").mkdir(exist_ok=True)
        path = pdir / "promotion.json"
        path.write_text(json.dumps(promotion_dict, indent=2, default=str), encoding="utf-8")
        hist = pdir / "history" / f"promotion_{promotion_dict.get('candidate_id', '')}.json"
        if not hist.exists():
            hist.write_text(json.dumps(promotion_dict, indent=2, default=str), encoding="utf-8")
        return path

    def load_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        path = self._base / proposal_id / "proposal.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def load_validation(self, proposal_id: str) -> dict[str, Any] | None:
        path = self._base / proposal_id / "validation.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def load_promotion(self, proposal_id: str) -> dict[str, Any] | None:
        path = self._base / proposal_id / "promotion.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def list_proposals(self) -> list[str]:
        if not self._base.exists():
            return []
        return sorted(
            d.name for d in self._base.iterdir()
            if d.is_dir() and (d / "proposal.json").exists()
        )
