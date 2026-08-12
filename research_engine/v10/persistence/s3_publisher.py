"""
V10 Research S3 Publisher.

Publishes V10 research products to a dedicated S3 area:
    s3://v10-engine/reports/v10-research/

Structure mirrors local:
    reports/v10-research/
    +-- runs/{run_id}.json
    +-- questions/{question_id}/question.json
    +-- questions/{question_id}/latest.json
    +-- questions/{question_id}/latest.md
    +-- questions/{question_id}/history/{run_id}.json
    +-- questions/{question_id}/history/{run_id}.md
    +-- control_plane/control_plane_state.json
    +-- cockpit/cockpit.html

This module does NOT:
    - Run analysis
    - Modify question contracts
    - Modify populations/universes
    - Touch the existing reports/research/ S3 prefix
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_S3_BUCKET = "v10-engine"
_S3_PREFIX = "reports/v10-research"
_LOCAL_ROOT = Path("reports/research")


class V10ResearchS3Publisher:
    """
    Publishes local V10 research products to S3.

    Usage:
        publisher = V10ResearchS3Publisher()
        result = publisher.publish_all()
    """

    def __init__(
        self,
        bucket: str = _S3_BUCKET,
        prefix: str = _S3_PREFIX,
        local_root: Path | str = _LOCAL_ROOT,
        dry_run: bool = False,
    ):
        self._bucket = bucket
        self._prefix = prefix
        self._local = Path(local_root)
        self._dry_run = dry_run
        self._published: list[str] = []
        self._failed: list[str] = []

    def publish_all(self) -> dict[str, Any]:
        """
        Publish all V10 research products to S3.

        Returns summary of what was published.
        """
        self._published = []
        self._failed = []

        # Publish run manifests
        self._publish_directory("runs")

        # Publish question products
        self._publish_questions()

        # Publish control plane state
        self._publish_file("control_plane_state.json", "control_plane/control_plane_state.json")
        self._publish_file("control_centre_status.txt", "control_plane/control_centre_status.txt")

        # Publish cockpit
        self._publish_file("cockpit.html", "cockpit/cockpit.html")

        # Publish audits
        self._publish_file("universe_contract_audit.json", "audits/universe_contract_audit.json")
        self._publish_file("universe_contract_audit.md", "audits/universe_contract_audit.md")
        self._publish_file(
            "execution_decision_correlation_audit.json",
            "audits/execution_decision_correlation_audit.json",
        )
        self._publish_file(
            "execution_decision_correlation_audit.md",
            "audits/execution_decision_correlation_audit.md",
        )

        return {
            "bucket": self._bucket,
            "prefix": self._prefix,
            "published": len(self._published),
            "failed": len(self._failed),
            "dry_run": self._dry_run,
            "files": self._published[:20],  # First 20 for summary
        }

    def publish_run(self, run_id: str) -> dict[str, Any]:
        """Publish products for a specific run only."""
        self._published = []
        self._failed = []

        # Run manifest
        self._publish_file(f"runs/{run_id}.json", f"runs/{run_id}.json")

        # Question products that have history for this run
        questions_dir = self._local / "questions"
        if questions_dir.exists():
            for q_dir in sorted(questions_dir.iterdir()):
                if not q_dir.is_dir():
                    continue
                qid = q_dir.name
                # Always publish latest + definition
                self._publish_question_files(qid)
                # Publish specific run history
                history_json = q_dir / "history" / f"{run_id}.json"
                history_md = q_dir / "history" / f"{run_id}.md"
                if history_json.exists():
                    self._publish_file(
                        f"questions/{qid}/history/{run_id}.json",
                        f"questions/{qid}/history/{run_id}.json",
                    )
                if history_md.exists():
                    self._publish_file(
                        f"questions/{qid}/history/{run_id}.md",
                        f"questions/{qid}/history/{run_id}.md",
                    )

        return {
            "bucket": self._bucket,
            "prefix": self._prefix,
            "run_id": run_id,
            "published": len(self._published),
            "failed": len(self._failed),
            "dry_run": self._dry_run,
        }

    def _publish_questions(self) -> None:
        """Publish all question products."""
        questions_dir = self._local / "questions"
        if not questions_dir.exists():
            return

        for q_dir in sorted(questions_dir.iterdir()):
            if not q_dir.is_dir():
                continue
            qid = q_dir.name
            self._publish_question_files(qid)

            # Publish history
            history_dir = q_dir / "history"
            if history_dir.exists():
                for hfile in sorted(history_dir.iterdir()):
                    if hfile.is_file():
                        rel = f"questions/{qid}/history/{hfile.name}"
                        self._publish_file(f"questions/{qid}/history/{hfile.name}", rel)

    def _publish_question_files(self, qid: str) -> None:
        """Publish core question files (definition + latest)."""
        for fname in ("question.json", "latest.json", "latest.md"):
            local_rel = f"questions/{qid}/{fname}"
            s3_rel = f"questions/{qid}/{fname}"
            self._publish_file(local_rel, s3_rel)

    def _publish_directory(self, subdir: str) -> None:
        """Publish all files in a subdirectory."""
        local_dir = self._local / subdir
        if not local_dir.exists():
            return
        for fpath in sorted(local_dir.rglob("*")):
            if fpath.is_file():
                rel = str(fpath.relative_to(self._local)).replace("\\", "/")
                self._publish_file(rel, rel)

    def _publish_file(self, local_rel: str, s3_rel: str) -> None:
        """Publish one file to S3."""
        local_path = self._local / local_rel
        if not local_path.exists():
            return  # Silently skip missing files

        s3_key = f"{self._prefix}/{s3_rel}"

        if self._dry_run:
            self._published.append(s3_key)
            return

        try:
            content = local_path.read_bytes()
            content_type = self._content_type(local_path.name)

            import boto3
            from botocore.config import Config as BotoConfig

            s3 = boto3.client(
                "s3",
                aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
                region_name=os.getenv("AWS_REGION", "eu-west-2"),
                config=BotoConfig(connect_timeout=5, read_timeout=10, retries={"max_attempts": 1}),
            )
            s3.put_object(
                Bucket=self._bucket,
                Key=s3_key,
                Body=content,
                ContentType=content_type,
            )
            self._published.append(s3_key)

        except Exception as exc:
            self._failed.append(f"{s3_key}: {exc}")
            logger.warning(f"[S3_PUBLISH] Failed: {s3_key} — {exc}")

    @staticmethod
    def _content_type(filename: str) -> str:
        if filename.endswith(".json"):
            return "application/json"
        elif filename.endswith(".md"):
            return "text/markdown"
        elif filename.endswith(".html"):
            return "text/html"
        elif filename.endswith(".txt"):
            return "text/plain"
        return "application/octet-stream"


# ═══════════════════════════════════════════════════════════════════════════════
# CONVENIENCE FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════


def publish_v10_research(dry_run: bool = False) -> dict[str, Any]:
    """Publish all V10 research products to S3."""
    publisher = V10ResearchS3Publisher(dry_run=dry_run)
    return publisher.publish_all()


def publish_v10_run(run_id: str, dry_run: bool = False) -> dict[str, Any]:
    """Publish products for a specific run."""
    publisher = V10ResearchS3Publisher(dry_run=dry_run)
    return publisher.publish_run(run_id)


def get_s3_key(local_rel_path: str) -> str:
    """Get the S3 key for a local relative path."""
    return f"{_S3_PREFIX}/{local_rel_path}"


def get_expected_s3_structure(run_id: str, question_ids: list[str]) -> list[str]:
    """Get the expected S3 keys for a run + questions."""
    keys = [f"{_S3_PREFIX}/runs/{run_id}.json"]
    for qid in question_ids:
        keys.append(f"{_S3_PREFIX}/questions/{qid}/question.json")
        keys.append(f"{_S3_PREFIX}/questions/{qid}/latest.json")
        keys.append(f"{_S3_PREFIX}/questions/{qid}/latest.md")
        keys.append(f"{_S3_PREFIX}/questions/{qid}/history/{run_id}.json")
        keys.append(f"{_S3_PREFIX}/questions/{qid}/history/{run_id}.md")
    keys.append(f"{_S3_PREFIX}/control_plane/control_plane_state.json")
    return keys
