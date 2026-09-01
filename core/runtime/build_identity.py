"""
V10 Build Identity — Git revision retrieval for startup logging.

Provides the current Git commit hash and branch name so every live bot
startup can be tied to an exact code revision.

Ownership: core/runtime/build_identity.py
Dependencies: subprocess (stdlib only)
Must NOT import from: any trading logic, strategy, pipeline, or persistence

Failure handling:
  - Returns "UNKNOWN" if .git is missing, Git is not installed, or command fails.
  - NEVER raises exceptions — the bot must start regardless of Git availability.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class BuildIdentity:
    """Immutable snapshot of the code revision at startup."""

    git_commit: str = "UNKNOWN"
    branch: str = "UNKNOWN"
    started_at: str = ""

    @property
    def is_available(self) -> bool:
        """True if Git metadata was successfully retrieved."""
        return self.git_commit != "UNKNOWN"


def get_build_identity() -> BuildIdentity:
    """
    Retrieve current Git commit and branch.

    Returns BuildIdentity with UNKNOWN fields if Git is unavailable.
    Never raises.
    """
    started_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    commit = _run_git("rev-parse", "--short", "HEAD")
    branch = _run_git("rev-parse", "--abbrev-ref", "HEAD")

    return BuildIdentity(
        git_commit=commit or "UNKNOWN",
        branch=branch or "UNKNOWN",
        started_at=started_at,
    )


def _run_git(*args: str) -> str | None:
    """
    Run a git command and return stripped stdout.

    Returns None on any failure (missing git, not a repo, command error).
    """
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        return None
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError, Exception):
        return None
