"""
Research Operations — Storage abstraction.

Supports both local filesystem and S3 backends.
Lambda uses S3; local development uses filesystem.

Configuration via environment variables:
    RESEARCH_STORAGE=s3          (or "local", default)
    RESEARCH_BUCKET=v10-engine
    RESEARCH_UNIVERSE_KEY=data/research/research_universe.jsonl
    RESEARCH_REPORT_PREFIX=reports/research/
    RESEARCH_STATE_KEY=research/operations/state.json
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Environment configuration
_STORAGE_BACKEND = os.environ.get("RESEARCH_STORAGE", "local")
_BUCKET = os.environ.get("RESEARCH_BUCKET", "v10-engine")
_UNIVERSE_KEY = os.environ.get("RESEARCH_UNIVERSE_KEY", "data/research/research_universe.jsonl")
_REPORT_PREFIX = os.environ.get("RESEARCH_REPORT_PREFIX", "reports/research/")
_STATE_KEY = os.environ.get("RESEARCH_STATE_KEY", "research/operations/state.json")


class ResearchStorage:
    """
    Unified storage interface for research data.

    Automatically selects S3 or local filesystem based on RESEARCH_STORAGE env var.
    """

    def __init__(self, backend: str | None = None, bucket: str | None = None):
        self._backend = backend or _STORAGE_BACKEND
        self._bucket = bucket or _BUCKET
        self._s3_client = None

    @property
    def is_s3(self) -> bool:
        return self._backend == "s3"

    def load_universe(self, key: str | None = None) -> str:
        """Load research universe content (JSONL string)."""
        path = key or _UNIVERSE_KEY
        return self._read(path)

    def save_report(self, content: str, filename: str) -> str:
        """Save a report file. Returns the path/key."""
        path = f"{_REPORT_PREFIX}{filename}"
        self._write(path, content)
        return path

    def load_state(self, key: str | None = None) -> dict[str, Any]:
        """Load research state JSON."""
        path = key or _STATE_KEY
        content = self._read(path)
        if not content:
            return {}
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {}

    def save_state(self, state: dict[str, Any], key: str | None = None) -> None:
        """Save research state JSON."""
        path = key or _STATE_KEY
        self._write(path, json.dumps(state, indent=2, default=str))

    # ─── BACKEND DISPATCH ─────────────────────────────────────

    def _read(self, path: str) -> str:
        if self.is_s3:
            return self._s3_read(path)
        return self._local_read(path)

    def _write(self, path: str, content: str) -> None:
        if self.is_s3:
            self._s3_write(path, content)
        else:
            self._local_write(path, content)

    # ─── LOCAL FILESYSTEM ─────────────────────────────────────

    def _local_read(self, path: str) -> str:
        p = Path(path)
        if not p.exists():
            return ""
        return p.read_text(encoding="utf-8")

    def _local_write(self, path: str, content: str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    # ─── S3 ───────────────────────────────────────────────────

    def _get_s3(self):
        if self._s3_client is None:
            import boto3
            self._s3_client = boto3.client("s3")
        return self._s3_client

    def _s3_read(self, key: str) -> str:
        try:
            s3 = self._get_s3()
            response = s3.get_object(Bucket=self._bucket, Key=key)
            return response["Body"].read().decode("utf-8")
        except Exception as exc:
            logger.warning(f"[STORAGE] S3 read failed: {key} — {exc}")
            return ""

    def _s3_write(self, key: str, content: str) -> None:
        try:
            s3 = self._get_s3()
            s3.put_object(Bucket=self._bucket, Key=key, Body=content.encode("utf-8"))
        except Exception as exc:
            logger.warning(f"[STORAGE] S3 write failed: {key} — {exc}")
