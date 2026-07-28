"""
Research Knowledge Integrity — Prevents invalidated findings from
triggering promotion decisions.

Status lifecycle:
    DISCOVERED → VALIDATED → PROMOTED
                          ↘ INVALIDATED
                          ↘ SUPERSEDED
                          ↘ REQUIRES_RERUN

Rules:
    - INVALIDATED findings CANNOT be promoted
    - SUPERSEDED findings CANNOT be promoted
    - REQUIRES_RERUN findings CANNOT be promoted until re-validated
    - Only VALIDATED findings can progress to PROMOTED

This module is PURELY RESEARCH INFRASTRUCTURE. No trading impact.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_KNOWLEDGE_PATH = Path("analysis/summaries/research_knowledge.json")

# Valid status values
VALID_STATUSES = frozenset({
    "DISCOVERED",
    "VALIDATED",
    "PROMOTED",
    "INVALIDATED",
    "SUPERSEDED",
    "REQUIRES_RERUN",
})

# Statuses that BLOCK promotion
PROMOTION_BLOCKED_STATUSES = frozenset({
    "INVALIDATED",
    "SUPERSEDED",
    "REQUIRES_RERUN",
    "DISCOVERED",
})

# Statuses that ALLOW promotion
PROMOTABLE_STATUSES = frozenset({
    "VALIDATED",
})


def load_knowledge() -> dict[str, Any]:
    """Load research knowledge from disk."""
    if not _KNOWLEDGE_PATH.exists():
        return {}
    try:
        return json.loads(_KNOWLEDGE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def get_finding_status(question_id: str) -> str:
    """
    Get the current status of a finding.

    Returns the status string or "NOT_FOUND" if no finding exists.
    """
    knowledge = load_knowledge()
    findings = knowledge.get("findings", {})
    finding = findings.get(question_id)
    if finding is None:
        return "NOT_FOUND"
    return finding.get("status", "DISCOVERED")


def is_promotable(question_id: str) -> bool:
    """
    Check whether a finding can be promoted to implementation.

    Returns True ONLY if the finding status is VALIDATED.
    All other statuses (including INVALIDATED, REQUIRES_RERUN) block promotion.
    """
    status = get_finding_status(question_id)
    return status in PROMOTABLE_STATUSES


def is_invalidated(question_id: str) -> bool:
    """Check whether a finding has been explicitly invalidated."""
    return get_finding_status(question_id) == "INVALIDATED"


def get_invalidated_findings() -> list[str]:
    """Return all question IDs with INVALIDATED status."""
    knowledge = load_knowledge()
    findings = knowledge.get("findings", {})
    return [qid for qid, f in findings.items() if f.get("status") == "INVALIDATED"]


def get_promotable_findings() -> list[str]:
    """Return all question IDs that CAN be promoted."""
    knowledge = load_knowledge()
    findings = knowledge.get("findings", {})
    return [qid for qid, f in findings.items() if f.get("status") in PROMOTABLE_STATUSES]


def get_promotion_blockers() -> list[str]:
    """Return all active promotion blockers from the knowledge base."""
    knowledge = load_knowledge()
    return knowledge.get("promotion_blockers", [])


def validate_promotion_attempt(question_id: str) -> tuple[bool, str]:
    """
    Validate whether a promotion attempt should proceed.

    Returns: (allowed, reason)
    """
    status = get_finding_status(question_id)

    if status == "NOT_FOUND":
        return False, f"No finding exists for {question_id}"

    if status == "INVALIDATED":
        knowledge = load_knowledge()
        finding = knowledge.get("findings", {}).get(question_id, {})
        reason = finding.get("reason", "Previously invalidated")
        return False, f"BLOCKED: {question_id} is INVALIDATED. Reason: {reason}"

    if status == "SUPERSEDED":
        return False, f"BLOCKED: {question_id} has been superseded by newer research"

    if status == "REQUIRES_RERUN":
        return False, f"BLOCKED: {question_id} requires re-run on CURRENT epoch data before promotion"

    if status == "DISCOVERED":
        return False, f"BLOCKED: {question_id} has not been validated yet"

    if status == "VALIDATED":
        # Check global blockers
        blockers = get_promotion_blockers()
        if blockers:
            return False, f"BLOCKED by system-level blocker: {blockers[0]}"
        return True, f"{question_id} is VALIDATED and eligible for promotion"

    if status == "PROMOTED":
        return False, f"{question_id} is already PROMOTED"

    return False, f"Unknown status: {status}"
