"""Repair Block 3 — adverse-evidence monitoring and governed rollback authority.

Audit 3 established that adverse evidence could *recommend* rollback but nothing
invoked governed revoke / disable / rollback authority.

This module connects post-deployment monitoring to the candidate lifecycle:

    deployed/verified authority
        -> new governed evidence
        -> monitoring evaluation
        -> action required (REVIEW / DISABLE / REVOKE / ROLLBACK)
        -> disable / revoke / rollback according to the configured authority policy

IMPORTANT — the repository had NO explicit governed policy deciding automatic
versus human-confirmed rollback.  Rather than invent autonomy, this module
introduces an explicit authority-policy artifact whose SAFE DEFAULT is
``HUMAN_CONFIRMATION_REQUIRED``.  The engine may autonomously DETECT and
RECOMMEND; automatic execution requires an explicit, auditable policy grant.

This module never writes a policy file, never trades, never mutates a registry
and never executes a rollback.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

POLICY_SCHEMA = "rollback_authority_policy_v1"
DEFAULT_POLICY_PATH = Path("data/research/governance/rollback_authority_policy.json")

HUMAN_CONFIRMATION_REQUIRED = "HUMAN_CONFIRMATION_REQUIRED"
AUTO_ROLLBACK = "AUTO_ROLLBACK"
VALID_MODES = frozenset({HUMAN_CONFIRMATION_REQUIRED, AUTO_ROLLBACK})

# Recommended actions, in escalating severity.
NONE = "NONE"
REVIEW = "REVIEW"
DISABLE = "DISABLE"
REVOKE = "REVOKE"
ROLLBACK = "ROLLBACK"

# Monitoring states that indicate degradation AFTER a successful deployment.
_ADVERSE_STATES = frozenset({
    "SHADOW_VALIDATION_INVALID", "REGRESSION_DETECTED", "SHADOW_VALIDATION_INSUFFICIENT_DATA",
})

# Criteria tokens that indicate a failed post-deployment criterion.
_ADVERSE_CRITERIA_TOKENS = (
    "INTEGRITY_VIOLATIONS",
    "MAX_DRAWDOWN",
    "PROFIT_FACTOR",
    "MEAN_PAIRED_DELTA",
    "REGRESSION",
    "COMPLETE_PAIRING",
    "INVALID",
)


class RollbackAuthorityPolicyError(RuntimeError):
    """The governed rollback-authority policy artifact is corrupt."""


@dataclass(frozen=True)
class RollbackAuthorityPolicy:
    """The explicit governed policy deciding who may execute a rollback."""

    mode: str = HUMAN_CONFIRMATION_REQUIRED
    authorized_by: str = ""
    authorized_at: str = ""
    source: str = "default_safe"

    @property
    def automatic_execution_authorized(self) -> bool:
        return (self.mode == AUTO_ROLLBACK
                and bool(self.authorized_by.strip())
                and bool(self.authorized_at.strip()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": POLICY_SCHEMA,
            "mode": self.mode,
            "authorized_by": self.authorized_by,
            "authorized_at": self.authorized_at,
            "automatic_execution_authorized": self.automatic_execution_authorized,
            "source": self.source,
        }


def load_rollback_authority_policy(
    path: str | Path | None = None,
) -> RollbackAuthorityPolicy:
    """Load the governed policy.  Absent/malformed/unauthorized => safe default."""
    target = Path(path) if path is not None else DEFAULT_POLICY_PATH
    if not target.exists():
        return RollbackAuthorityPolicy()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        raise RollbackAuthorityPolicyError(
            f"ROLLBACK_AUTHORITY_POLICY_UNREADABLE:{exc}") from exc
    if not isinstance(raw, Mapping):
        raise RollbackAuthorityPolicyError("ROLLBACK_AUTHORITY_POLICY_NOT_OBJECT")
    mode = str(raw.get("mode") or HUMAN_CONFIRMATION_REQUIRED).upper()
    if mode not in VALID_MODES:
        raise RollbackAuthorityPolicyError(f"ROLLBACK_AUTHORITY_MODE_INVALID:{mode}")
    policy = RollbackAuthorityPolicy(
        mode=mode,
        authorized_by=str(raw.get("authorized_by") or ""),
        authorized_at=str(raw.get("authorized_at") or ""),
        source=str(target),
    )
    # An AUTO_ROLLBACK grant without explicit human attribution is NOT a grant.
    if mode == AUTO_ROLLBACK and not policy.automatic_execution_authorized:
        return RollbackAuthorityPolicy(source=str(target))
    return policy


@dataclass(frozen=True)
class AdverseEvidenceVerdict:
    """Outcome of one adverse-evidence evaluation."""

    action_required: bool
    recommended_action: str
    reasons: tuple[str, ...] = field(default_factory=tuple)
    execution_authority: str = HUMAN_CONFIRMATION_REQUIRED
    automatic_execution_authorized: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_required": self.action_required,
            "recommended_action": self.recommended_action,
            "reasons": list(self.reasons),
            "execution_authority": self.execution_authority,
            "automatic_execution_authorized": self.automatic_execution_authorized,
        }


def _criterion_reasons(readiness: Mapping[str, Any]) -> list[str]:
    out: list[str] = []
    for token in readiness.get("unsatisfied_criteria") or ():
        text = str(token).upper()
        if any(part in text for part in _ADVERSE_CRITERIA_TOKENS):
            out.append(f"CRITERION_FAILED:{token}")
    return out


def evaluate_adverse_evidence(
    *,
    candidate_id: str,
    post_deployment_state: str = "",
    monitoring_report: Mapping[str, Any] | None = None,
    explicit_signals: Sequence[str] = (),
    authority_status: str = "",
    policy: RollbackAuthorityPolicy | None = None,
) -> AdverseEvidenceVerdict:
    """Evaluate post-deployment evidence and derive the required governed action.

    The engine may autonomously detect and recommend.  ``execution_authority``
    reports whether an automatic execution is explicitly governed.
    """
    governed = policy or load_rollback_authority_policy()
    reasons: list[str] = []

    for signal in explicit_signals or ():
        text = str(signal).strip()
        if text:
            reasons.append(f"ADVERSE_SIGNAL:{text}")

    report = monitoring_report or {}
    reports = report.get("reports") if isinstance(report, Mapping) else None
    rows: Iterable[Mapping[str, Any]] = (
        [row for row in reports if isinstance(row, Mapping)
         and row.get("candidate_id") == candidate_id]
        if isinstance(reports, (list, tuple)) else ()
    )
    for row in rows:
        readiness = row.get("readiness") or {}
        state = str(readiness.get("state") or "")
        if state in _ADVERSE_STATES:
            reasons.append(f"MONITORING_STATE:{state}")
        reasons.extend(_criterion_reasons(readiness))

    state_text = str(post_deployment_state or "").upper()
    if state_text in _ADVERSE_STATES:
        reasons.append(f"POST_DEPLOYMENT_STATE:{state_text}")

    if not reasons:
        return AdverseEvidenceVerdict(
            action_required=False, recommended_action=NONE, reasons=(),
            execution_authority=governed.mode,
            automatic_execution_authorized=governed.automatic_execution_authorized,
        )

    # Escalation: a live authority with failed criteria recommends rollback;
    # a non-live authority recommends review / revoke instead.
    live = str(authority_status or "").upper() in {"ACTIVE"}
    if live:
        recommended = ROLLBACK
    elif authority_status:
        recommended = REVOKE
    else:
        recommended = REVIEW

    return AdverseEvidenceVerdict(
        action_required=True,
        recommended_action=recommended,
        reasons=tuple(sorted(set(reasons))),
        execution_authority=governed.mode,
        automatic_execution_authorized=governed.automatic_execution_authorized,
    )


def automatic_execution_permitted(verdict: AdverseEvidenceVerdict) -> bool:
    """Automatic revoke/rollback requires an EXPLICIT governed authorization."""
    return bool(verdict.action_required and verdict.automatic_execution_authorized)


__all__ = [
    "AUTO_ROLLBACK", "AdverseEvidenceVerdict", "DEFAULT_POLICY_PATH", "DISABLE",
    "HUMAN_CONFIRMATION_REQUIRED", "NONE", "POLICY_SCHEMA", "REVIEW", "REVOKE",
    "ROLLBACK", "RollbackAuthorityPolicy", "RollbackAuthorityPolicyError",
    "VALID_MODES", "automatic_execution_permitted", "evaluate_adverse_evidence",
    "load_rollback_authority_policy",
]
