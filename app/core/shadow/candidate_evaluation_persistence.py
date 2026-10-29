"""Append-only persistence for paired shadow-candidate evaluation evidence."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
_DEFAULT_BASE_DIR = "logs/shadow_candidate_evaluation_v1"


def get_evaluation_base_dir() -> str:
    try:
        from core import config
        return str(getattr(config, "SHADOW_CANDIDATE_EVALUATION_DIR", _DEFAULT_BASE_DIR))
    except Exception:
        return _DEFAULT_BASE_DIR


class CandidateEvaluationWriter:
    """Durable writer for the independent shadow_candidate_evaluation_v1 stream."""

    def __init__(self, base_dir: str | None = None) -> None:
        self._base_dir = base_dir or get_evaluation_base_dir()

    @property
    def base_dir(self) -> str:
        return self._base_dir

    def partition_path(self, symbol: str, market_time_utc: int) -> Path:
        day = datetime.fromtimestamp(
            int(market_time_utc), tz=timezone.utc
        ).strftime("%Y-%m-%d")
        return Path(self._base_dir) / (symbol or "UNKNOWN") / f"{day}.jsonl"

    def append(self, *, event: dict[str, Any], symbol: str,
               market_time_utc: int) -> bool:
        try:
            path = self.partition_path(symbol, market_time_utc)
            path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(event, separators=(",", ":"), default=str) + "\n"
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
            try:
                os.write(fd, line.encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
            return True
        except Exception as exc:
            logger.error("[SHADOW_CANDIDATE_EVALUATION_PERSIST_FAIL] %s", exc)
            return False


def load_evaluation_events(base_dir: str | None = None) -> list[dict[str, Any]]:
    root = Path(base_dir or get_evaluation_base_dir())
    events: list[dict[str, Any]] = []
    if not root.exists():
        return events
    for path in sorted(root.rglob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            logger.error("[SHADOW_CANDIDATE_EVALUATION_READ_FAIL] %s: %s", path, exc)
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.error("[SHADOW_CANDIDATE_EVALUATION_CORRUPT_LINE] %s: %s", path, exc)
                continue
            if isinstance(value, dict):
                events.append(value)
    return events
