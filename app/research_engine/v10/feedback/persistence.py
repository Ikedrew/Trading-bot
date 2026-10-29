"""
Research Feedback Persistence.

Stores feedback artifacts following the same pattern as QuestionProductManager:
    latest.json (overwritten with most recent)
    history/{feedback_id}.json (immutable)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from research_engine.v10.feedback.model import ResearchFeedback

logger = logging.getLogger(__name__)

_DEFAULT_DIR = Path("reports/research/feedback")


class FeedbackStore:
    """
    Persists research feedback artifacts.

    Storage:
        reports/research/feedback/
            {question_id}/
                latest.json
                history/
                    {feedback_id}.json
    """

    def __init__(self, base_dir: Path | str | None = None):
        self._base = Path(base_dir) if base_dir else _DEFAULT_DIR

    def save(self, feedback: ResearchFeedback) -> Path:
        """Persist a feedback artifact. Returns path to latest.json."""
        qid = feedback.question_id or "unknown"
        fb_dir = self._base / qid
        fb_dir.mkdir(parents=True, exist_ok=True)
        (fb_dir / "history").mkdir(exist_ok=True)

        fb_dict = feedback.to_dict()

        # Write latest
        latest_path = fb_dir / "latest.json"
        latest_path.write_text(json.dumps(fb_dict, indent=2, default=str), encoding="utf-8")

        # Write immutable history
        history_path = fb_dir / "history" / f"{feedback.feedback_id}.json"
        if not history_path.exists():
            history_path.write_text(json.dumps(fb_dict, indent=2, default=str), encoding="utf-8")

        return latest_path

    def save_batch(self, feedbacks: list[ResearchFeedback]) -> int:
        """Save multiple feedback artifacts. Returns count saved."""
        for fb in feedbacks:
            self.save(fb)
        return len(feedbacks)

    def load_latest(self, question_id: str) -> dict[str, Any] | None:
        """Load latest feedback for a question."""
        path = self._base / question_id / "latest.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def load_all_latest(self) -> list[dict[str, Any]]:
        """Load latest feedback for all questions."""
        if not self._base.exists():
            return []
        results = []
        for qdir in sorted(self._base.iterdir()):
            if qdir.is_dir():
                latest = qdir / "latest.json"
                if latest.exists():
                    try:
                        results.append(json.loads(latest.read_text(encoding="utf-8")))
                    except Exception:
                        continue
        return results

    def list_questions(self) -> list[str]:
        """List all question IDs with feedback."""
        if not self._base.exists():
            return []
        return sorted(d.name for d in self._base.iterdir() if d.is_dir() and (d / "latest.json").exists())
