"""Candidate persistence: separate append-only shadow_candidate_v1 stream."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_BASE_DIR = "logs/shadow_candidate_v1"


def get_candidate_base_dir() -> str:
    try:
        from core import config as _cfg
        return str(getattr(_cfg, "SHADOW_CANDIDATE_DIR", _DEFAULT_BASE_DIR))
    except Exception:
        return _DEFAULT_BASE_DIR


class CandidateEventWriter:
    """Append-only writer for shadow_candidate_v1. Never raises."""

    def __init__(self, base_dir: str | None = None) -> None:
        self._base_dir = base_dir or get_candidate_base_dir()

    @property
    def base_dir(self) -> str:
        return self._base_dir

    def partition_path(self, symbol: str, market_time_utc: int) -> Path:
        utc_date = datetime.fromtimestamp(
            int(market_time_utc), tz=timezone.utc).strftime("%Y-%m-%d")
        return Path(self._base_dir) / (symbol or "UNKNOWN") / f"{utc_date}.jsonl"

    def append(self, *, event: dict, symbol: str,
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
            logger.debug("[SHADOW_CANDIDATE_PERSIST_FAIL] %s", exc)
            return False


def load_candidate_events(base_dir: str | None = None) -> list:
    root = Path(base_dir or get_candidate_base_dir())
    events: list[dict[str, Any]] = []
    if not root.exists():
        return events
    for f in sorted(root.rglob("*.jsonl")):
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except OSError:
            continue
    return events
