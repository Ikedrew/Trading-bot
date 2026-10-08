"""Repair Block 3 — governed candidate lifecycle operations.

This module is the ONLY place that turns a research candidate into governed
production authority.  It composes the existing authorities (Track-A candidate
registry, human-decision store, application ledger, baseline authority) with the
new canonical lifecycle ledger and the governed production-authority ledger.

Autonomy boundary enforced here:

    ENGINE (autonomous)  : validation, shadow evidence, readiness derivation,
                           failure/rejection where criteria fail, adverse
                           evidence detection, revoke/rollback RECOMMENDATION.
    HUMAN (explicit)     : live ACCEPT, and (unless an explicit governed policy
                           grants it) rollback/revocation execution.

There is NO automatic live promotion in this module and no code path that can
create runtime authority without an explicit human ACCEPT.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from research_engine.control_plane.candidate_lifecycle_authority import (
    CANONICAL_STATES, CanonicalState, CandidateLifecycleLedger, REVIEW_STATE_NOTICE,
    STORE_CLASSES, TransitionAuthority, derive_canonical_lifecycle,
    record_lifecycle_event,
)
from research_engine.control_plane.evidence_freshness_gate import (
    candidate_requires_shadow_evidence, evaluate_promotion_freshness,
    proposal_treatment_signature,
)
from research_engine.control_plane.production_authority import (
    ProductionAuthorityLedger, activate_authority, disable_authority,
    issue_authority, mark_rolled_back, revoke_authority, supersede_authority,
)


class CandidateLifecycleServiceError(RuntimeError):
    """A governed lifecycle operation was refused (fail closed)."""


def _ledger(path: str | Path | None) -> CandidateLifecycleLedger:
    return CandidateLifecycleLedger(path)


def _authority(path: str | Path | None) -> ProductionAuthorityLedger:
    return ProductionAuthorityLedger(path)


def _load_candidate(candidate_id: str, registry_dir: str | None):
    from research_engine.v10.candidates.candidate_registry import CandidateRegistry

    registry = (CandidateRegistry(storage_dir=registry_dir) if registry_dir
                else CandidateRegistry())
    return registry, registry.get(candidate_id)


def governed_human_decision(
    candidate_id: str,
    decision: str,
    recommendation_id: str,
    *,
    actor: str,
    reason: str,
    registry_dir: str | None = None,
    decisions_dir: str | None = None,
    recommendations_dir: str | None = None,
    evaluations_dir: str | None = None,
    lifecycle_ledger_path: str | Path | None = None,
    current_frontier: str = "",
    superseded_frontiers: Sequence[str] = (),
    candidate_frontier: str = "",
    shadow_frontier: str = "",
    require_shadow_evidence: bool | None = None,
    superseded_by_candidate: str = "",
    invalidated_upstream: Sequence[str] = (),
) -> Any:
    """Record an explicit human ACCEPT / REJECT behind the final freshness gate.

    ACCEPT is valid ONLY from a reviewable state AND only when the governed
    freshness/frontier gate passes.  A refusal is durable and auditable and
    never fabricates the opposite verdict.

    REJECT is not gated on freshness (rejecting a stale candidate must stay
    possible) but still requires an explicit human actor/reason and the
    existing recommendation binding.
    """
    from research_engine.v10.candidates.candidate_decision import record_human_decision

    verdict = str(decision or "").strip().upper()
    ledger = _ledger(lifecycle_ledger_path)
    _registry, candidate = _load_candidate(candidate_id, registry_dir)
    if candidate is None:
        raise CandidateLifecycleServiceError(f"CANDIDATE_NOT_FOUND:{candidate_id}")

    # Duplicate/conflict detection runs FIRST: an identical replay of an
    # already-recorded decision must stay a no-op, and a CONFLICTING decision
    # must fail closed without touching the governed record.
    from research_engine.v10.candidates.candidate_decision import (
        CandidateDecisionStore,
    )

    existing = CandidateDecisionStore(decisions_dir=decisions_dir).get_decision(candidate_id)
    if existing is not None:
        identical = (
            existing.decision == verdict
            and existing.actor == (actor or "").strip()
            and existing.reason == (reason or "").strip()
            and existing.recommendation_id == (recommendation_id or "").strip()
        )
        if not identical:
            raise CandidateLifecycleServiceError(
                f"HUMAN_DECISION_CONFLICT:{candidate_id}:{existing.decision}->{verdict}")
        from research_engine.v10.candidates.candidate_decision import (
            record_human_decision,
        )
        return record_human_decision(
            candidate_id, verdict, recommendation_id, actor=actor, reason=reason,
            registry_dir=registry_dir, decisions_dir=decisions_dir,
            recommendations_dir=recommendations_dir, evaluations_dir=evaluations_dir,
        )

    # Derive the current canonical state from every store (read-only).
    current = derive_canonical_lifecycle(
        candidate_id=candidate_id, registry_dir=registry_dir,
        decisions_dir=decisions_dir,
        lifecycle_ledger_path=lifecycle_ledger_path,
        upstream_invalidated=bool(invalidated_upstream),
    )

    if verdict == "ACCEPT":
        needs_shadow = (candidate_requires_shadow_evidence(candidate)
                        if require_shadow_evidence is None else require_shadow_evidence)
        gate = evaluate_promotion_freshness(
            candidate_id=candidate_id,
            registry_dir=registry_dir,
            current_frontier=current_frontier,
            superseded_frontiers=superseded_frontiers,
            candidate_frontier=candidate_frontier,
            shadow_frontier=shadow_frontier,
            require_shadow_evidence=needs_shadow,
            superseded_by_candidate=superseded_by_candidate,
            invalidated_upstream=invalidated_upstream,
        )
        if not gate.ok:
            record_lifecycle_event(
                candidate_id=candidate_id, event="PROMOTION_BLOCKED_STALE",
                from_state=current.state, to_state=current.state,
                authority=TransitionAuthority.ENGINE, actor="engine:freshness_gate",
                reason=";".join(gate.reasons),
                evidence_frontier=gate.evidence_frontier,
                ledger=ledger,
            )
            raise CandidateLifecycleServiceError(
                "PROMOTION_BLOCKED_STALE:" + ",".join(gate.reasons))

    result = record_human_decision(
        candidate_id, verdict, recommendation_id, actor=actor, reason=reason,
        registry_dir=registry_dir, decisions_dir=decisions_dir,
        recommendations_dir=recommendations_dir, evaluations_dir=evaluations_dir,
    )
    if result.ok and not result.duplicate:
        record_lifecycle_event(
            candidate_id=candidate_id,
            event=("HUMAN_ACCEPT" if verdict == "ACCEPT" else "HUMAN_REJECT"),
            from_state=current.state,
            to_state=(CanonicalState.ACCEPTED if verdict == "ACCEPT"
                      else CanonicalState.REJECTED),
            authority=TransitionAuthority.HUMAN,
            actor=actor, reason=reason,
            decision_id=(getattr(result.decision, "recommendation_id", "")
                         or recommendation_id),
            baseline_id=getattr(result.decision, "baseline_id", ""),
            baseline_config_hash=getattr(result.decision, "baseline_config_hash", ""),
            ledger=ledger,
        )
    return result


def create_governed_approval(
    candidate_id: str,
    recommendation_id: str,
    *,
    registry_dir: str | None = None,
    decisions_dir: str | None = None,
    recommendations_dir: str | None = None,
    evaluations_dir: str | None = None,
    application_path: str | Path | None = None,
    authority_path: str | Path | None = None,
    lifecycle_ledger_path: str | Path | None = None,
    actor: str = "human",
    evidence_frontier: str = "",
) -> Any:
    """ACCEPTED -> approval record (APPROVED_NOT_DEPLOYED) -> governed authority.

    The authority is ISSUED, not runtime-effective.  Runtime authority requires
    an explicit, verified ``ApplicationService.execute()``.
    """
    from research_engine.control_plane.application_ledger import ApplicationLedger

    _registry, candidate = _load_candidate(candidate_id, registry_dir)
    if candidate is None:
        raise CandidateLifecycleServiceError(f"CANDIDATE_NOT_FOUND:{candidate_id}")
    if candidate.status != CanonicalState.ACCEPTED:
        raise CandidateLifecycleServiceError(
            f"CANDIDATE_NOT_ACCEPTED:{candidate.status}")

    ledger = (ApplicationLedger(application_path) if application_path is not None
              else ApplicationLedger())
    application = ledger.create_application_from_approval(
        candidate_id, recommendation_id, registry_dir=registry_dir,
        decisions_dir=decisions_dir, recommendations_dir=recommendations_dir,
        evaluations_dir=evaluations_dir,
    )
    row = application.to_dict()

    from core.optimisation_policy import intended_state_from_approval, policy_scope

    policy_state = intended_state_from_approval(application=row)
    try:
        scope = policy_scope(policy_state)
    except Exception:
        scope = None

    decision = {}
    if decisions_dir is not None:
        from research_engine.control_plane.candidate_lifecycle_authority import (
            _latest_decision, _read_jsonl,
        )
        decision = _latest_decision([
            r for r in _read_jsonl(Path(decisions_dir) / "decisions.jsonl")
            if r.get("candidate_id") == candidate_id])

    authority = issue_authority(
        candidate_id=candidate_id,
        decision_id=str(decision.get("recommendation_id") or recommendation_id),
        application_id=row["application_id"],
        treatment_id=row.get("treatment_id", ""),
        treatment_spec=row.get("treatment_spec"),
        baseline_id=row.get("baseline_id", ""),
        baseline_config_hash=row.get("baseline_config_hash", ""),
        policy_state=policy_state,
        actor=actor,
        reason=f"approval:{row['application_id']}",
        evidence_frontier=evidence_frontier,
        scope=scope,
        ledger=_authority(authority_path),
    )

    lifecycle = _ledger(lifecycle_ledger_path)
    record_lifecycle_event(
        candidate_id=candidate_id, event="APPROVED_NOT_DEPLOYED",
        from_state=CanonicalState.ACCEPTED, to_state=CanonicalState.APPROVED_NOT_DEPLOYED,
        authority=TransitionAuthority.APPLICATION_SERVICE, actor=actor,
        reason="governed approval record created",
        application_id=row["application_id"], authority_id=authority.authority_id,
        decision_id=authority.decision_id, evidence_frontier=evidence_frontier,
        baseline_id=authority.baseline_id,
        baseline_config_hash=authority.baseline_config_hash,
        ledger=lifecycle,
    )
    record_lifecycle_event(
        candidate_id=candidate_id, event="AUTHORITY_ISSUED",
        from_state=CanonicalState.APPROVED_NOT_DEPLOYED,
        to_state=CanonicalState.APPROVED_NOT_DEPLOYED,
        authority=TransitionAuthority.APPLICATION_SERVICE, actor=actor,
        reason="production authority issued (NOT runtime-effective)",
        application_id=row["application_id"], authority_id=authority.authority_id,
        ledger=lifecycle,
    )
    return application


# ═══════════════════════════════════════════════════════════════════════════════
# TERMINAL / RESTRICTIVE GOVERNED OPERATIONS
# ═══════════════════════════════════════════════════════════════════════════════

def _current_state(candidate_id: str, *, registry_dir: str | None,
                   decisions_dir: str | None, application_path: str | Path | None,
                   authority_path: str | Path | None,
                   lifecycle_ledger_path: str | Path | None) -> str:
    return derive_canonical_lifecycle(
        candidate_id=candidate_id, registry_dir=registry_dir,
        decisions_dir=decisions_dir, application_path=application_path,
        authority_path=authority_path, lifecycle_ledger_path=lifecycle_ledger_path,
    ).state


def disable_candidate(
    candidate_id: str, *, actor: str, reason: str,
    registry_dir: str | None = None, decisions_dir: str | None = None,
    application_path: str | Path | None = None,
    authority_path: str | Path | None = None,
    lifecycle_ledger_path: str | Path | None = None,
    transition_authority: str = TransitionAuthority.HUMAN,
) -> Any:
    """DISABLED: temporarily stop a deployed candidate from affecting runtime."""
    from research_engine.control_plane.production_authority import (
        effective_authority_for_candidate,
    )

    authority = effective_authority_for_candidate(
        candidate_id, ledger=_authority(authority_path))
    if authority is None:
        raise CandidateLifecycleServiceError("NO_GOVERNED_AUTHORITY_TO_DISABLE")
    current = _current_state(
        candidate_id, registry_dir=registry_dir, decisions_dir=decisions_dir,
        application_path=application_path, authority_path=authority_path,
        lifecycle_ledger_path=lifecycle_ledger_path)
    record = disable_authority(application_id=authority.application_id, actor=actor,
                               reason=reason, ledger=_authority(authority_path))
    record_lifecycle_event(
        candidate_id=candidate_id, event="DISABLED", from_state=current,
        to_state=CanonicalState.DISABLED, authority=transition_authority,
        actor=actor, reason=reason, application_id=authority.application_id,
        authority_id=authority.authority_id, ledger=_ledger(lifecycle_ledger_path),
    )
    return record


def revoke_candidate(
    candidate_id: str, *, actor: str, reason: str,
    registry_dir: str | None = None, decisions_dir: str | None = None,
    application_path: str | Path | None = None,
    authority_path: str | Path | None = None,
    lifecycle_ledger_path: str | Path | None = None,
    transition_authority: str = TransitionAuthority.HUMAN,
) -> Any:
    """REVOKED: permanently withdraw production authority."""
    from research_engine.control_plane.production_authority import (
        effective_authority_for_candidate,
    )

    authority = effective_authority_for_candidate(
        candidate_id, ledger=_authority(authority_path))
    if authority is None:
        raise CandidateLifecycleServiceError("NO_GOVERNED_AUTHORITY_TO_REVOKE")
    current = _current_state(
        candidate_id, registry_dir=registry_dir, decisions_dir=decisions_dir,
        application_path=application_path, authority_path=authority_path,
        lifecycle_ledger_path=lifecycle_ledger_path)
    record = revoke_authority(application_id=authority.application_id, actor=actor,
                              reason=reason, ledger=_authority(authority_path))
    record_lifecycle_event(
        candidate_id=candidate_id, event="REVOKED", from_state=current,
        to_state=CanonicalState.REVOKED, authority=transition_authority,
        actor=actor, reason=reason, application_id=authority.application_id,
        authority_id=authority.authority_id, ledger=_ledger(lifecycle_ledger_path),
    )
    return record


def supersede_candidate(
    candidate_id: str, successor_application_id: str, *, actor: str, reason: str,
    registry_dir: str | None = None, decisions_dir: str | None = None,
    application_path: str | Path | None = None,
    authority_path: str | Path | None = None,
    lifecycle_ledger_path: str | Path | None = None,
    transition_authority: str = TransitionAuthority.HUMAN,
) -> Any:
    """SUPERSEDED: an explicit successor replaces this candidate's authority."""
    from research_engine.control_plane.production_authority import (
        effective_authority_for_candidate,
    )

    authority = effective_authority_for_candidate(
        candidate_id, ledger=_authority(authority_path))
    if authority is None:
        raise CandidateLifecycleServiceError("NO_GOVERNED_AUTHORITY_TO_SUPERSEDE")
    current = _current_state(
        candidate_id, registry_dir=registry_dir, decisions_dir=decisions_dir,
        application_path=application_path, authority_path=authority_path,
        lifecycle_ledger_path=lifecycle_ledger_path)
    record = supersede_authority(
        predecessor_application_id=authority.application_id,
        successor_application_id=successor_application_id,
        actor=actor, reason=reason, ledger=_authority(authority_path))
    record_lifecycle_event(
        candidate_id=candidate_id, event="SUPERSEDED", from_state=current,
        to_state=CanonicalState.SUPERSEDED, authority=transition_authority,
        actor=actor, reason=reason, application_id=authority.application_id,
        authority_id=authority.authority_id, superseded_by=record.superseded_by,
        ledger=_ledger(lifecycle_ledger_path),
    )
    return record


def record_rollback(
    candidate_id: str, *, actor: str, reason: str,
    registry_dir: str | None = None, decisions_dir: str | None = None,
    application_path: str | Path | None = None,
    authority_path: str | Path | None = None,
    lifecycle_ledger_path: str | Path | None = None,
    predecessor_application_id: str = "",
    transition_authority: str = TransitionAuthority.APPLICATION_SERVICE,
) -> Any:
    """ROLLED_BACK: a verified governed deployment was reverted to its predecessor.

    ``rollback`` never means REJECTED.  The candidate's historical deployment is
    retained; only its production authority ceases to be effective.
    """
    from research_engine.control_plane.production_authority import (
        effective_authority_for_candidate,
    )

    authority = effective_authority_for_candidate(
        candidate_id, ledger=_authority(authority_path))
    if authority is None:
        raise CandidateLifecycleServiceError("NO_GOVERNED_AUTHORITY_TO_ROLL_BACK")
    current = _current_state(
        candidate_id, registry_dir=registry_dir, decisions_dir=decisions_dir,
        application_path=application_path, authority_path=authority_path,
        lifecycle_ledger_path=lifecycle_ledger_path)
    record = mark_rolled_back(application_id=authority.application_id, actor=actor,
                              reason=reason, ledger=_authority(authority_path))
    lifecycle = _ledger(lifecycle_ledger_path)
    record_lifecycle_event(
        candidate_id=candidate_id, event="ROLLBACK_REQUESTED", from_state=current,
        to_state=current, authority=transition_authority, actor=actor, reason=reason,
        application_id=authority.application_id, authority_id=authority.authority_id,
        ledger=lifecycle,
    )
    record_lifecycle_event(
        candidate_id=candidate_id, event="ROLLED_BACK", from_state=current,
        to_state=CanonicalState.ROLLED_BACK, authority=transition_authority,
        actor=actor, reason=reason, application_id=authority.application_id,
        authority_id=authority.authority_id, ledger=lifecycle,
    )
    if predecessor_application_id:
        record_lifecycle_event(
            candidate_id=candidate_id, event="AUTHORITY_ACTIVATED",
            from_state=CanonicalState.ROLLED_BACK,
            to_state=CanonicalState.ROLLED_BACK,
            authority=TransitionAuthority.APPLICATION_SERVICE, actor=actor,
            reason=f"predecessor restored:{predecessor_application_id}",
            application_id=predecessor_application_id, ledger=lifecycle,
        )
    return record


def invalidate_upstream(
    candidate_id: str, *, actor: str, reason: str, upstream_id: str = "",
    registry_dir: str | None = None, decisions_dir: str | None = None,
    application_path: str | Path | None = None,
    authority_path: str | Path | None = None,
    lifecycle_ledger_path: str | Path | None = None,
) -> dict[str, Any]:
    """INVALIDATED_UPSTREAM / BLOCKED_UPSTREAM_INVALIDATED.

    The candidate can no longer progress.  If it already holds runtime
    authority, the required action escalates to a revoke/rollback path — it is
    never silently left live.
    """
    from research_engine.control_plane.production_authority import (
        effective_authority_for_candidate,
    )

    authority = effective_authority_for_candidate(
        candidate_id, ledger=_authority(authority_path))
    live = bool(authority and authority.status == "ACTIVE" and not authority.superseded_by)
    current = _current_state(
        candidate_id, registry_dir=registry_dir, decisions_dir=decisions_dir,
        application_path=application_path, authority_path=authority_path,
        lifecycle_ledger_path=lifecycle_ledger_path)
    to_state = (CanonicalState.INVALIDATED_UPSTREAM if live
                else CanonicalState.BLOCKED_UPSTREAM_INVALIDATED)
    record_lifecycle_event(
        candidate_id=candidate_id,
        event=("UPSTREAM_INVALIDATED" if live else "BLOCKED_UPSTREAM_INVALIDATED"),
        from_state=current, to_state=to_state, authority=TransitionAuthority.UPSTREAM,
        actor=actor, reason=reason, evidence=upstream_id,
        application_id=(authority.application_id if authority else ""),
        ledger=_ledger(lifecycle_ledger_path),
    )
    return {
        "candidate_id": candidate_id,
        "state": to_state,
        "was_runtime_effective": live,
        "required_action": ("REVOKE_OR_ROLLBACK" if live else "BLOCK_CANDIDATE"),
        "authority_id": (authority.authority_id if authority else ""),
        "application_id": (authority.application_id if authority else ""),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# ADVERSE-EVIDENCE MONITORING -> GOVERNED ACTION
# ═══════════════════════════════════════════════════════════════════════════════

def monitor_post_deployment(
    candidate_id: str,
    *,
    monitoring_report: Mapping[str, Any] | None = None,
    post_deployment_state: str = "",
    explicit_signals: Sequence[str] = (),
    policy_path: str | Path | None = None,
    registry_dir: str | None = None,
    decisions_dir: str | None = None,
    application_path: str | Path | None = None,
    authority_path: str | Path | None = None,
    lifecycle_ledger_path: str | Path | None = None,
    execute_if_authorized: bool = False,
    rollback_executor: Any = None,
    actor: str = "runtime_monitor",
) -> dict[str, Any]:
    """Detect adverse evidence and derive the required governed action.

    The engine AUTONOMOUSLY detects and records the requirement.  Execution is
    automatic ONLY when an explicit governed policy grants it
    (``AUTO_ROLLBACK`` with human attribution).  The default is
    ``HUMAN_CONFIRMATION_REQUIRED``: recommendation recorded, no execution.
    """
    from research_engine.control_plane.adverse_evidence_monitor import (
        automatic_execution_permitted, evaluate_adverse_evidence,
        load_rollback_authority_policy,
    )
    from research_engine.control_plane.production_authority import (
        effective_authority_for_candidate,
    )

    authority = effective_authority_for_candidate(
        candidate_id, ledger=_authority(authority_path))
    governed = load_rollback_authority_policy(policy_path)
    verdict = evaluate_adverse_evidence(
        candidate_id=candidate_id,
        post_deployment_state=post_deployment_state,
        monitoring_report=monitoring_report,
        explicit_signals=explicit_signals,
        authority_status=(authority.status if authority else ""),
        policy=governed,
    )
    out: dict[str, Any] = {"verdict": verdict.to_dict(), "executed": False}
    if not verdict.action_required:
        return out

    current = _current_state(
        candidate_id, registry_dir=registry_dir, decisions_dir=decisions_dir,
        application_path=application_path, authority_path=authority_path,
        lifecycle_ledger_path=lifecycle_ledger_path)
    record_lifecycle_event(
        candidate_id=candidate_id, event="ADVERSE_EVIDENCE", from_state=current,
        to_state=current, authority=TransitionAuthority.RUNTIME_MONITOR, actor=actor,
        reason=f"{verdict.recommended_action}:" + ";".join(verdict.reasons),
        application_id=(authority.application_id if authority else ""),
        ledger=_ledger(lifecycle_ledger_path),
    )
    out["action_required"] = True
    out["recommended_action"] = verdict.recommended_action

    if not automatic_execution_permitted(verdict) or not execute_if_authorized:
        out["execution"] = "HUMAN_CONFIRMATION_REQUIRED"
        return out

    if verdict.recommended_action == "ROLLBACK":
        if rollback_executor is None:
            out["execution"] = "NO_GOVERNED_ROLLBACK_EXECUTOR"
            return out
        rollback_executor(candidate_id)
        out["execution"] = "ROLLBACK_EXECUTED"
    elif verdict.recommended_action == "REVOKE":
        revoke_candidate(
            candidate_id, actor=actor, reason="adverse_evidence:" + ";".join(verdict.reasons),
            registry_dir=registry_dir, decisions_dir=decisions_dir,
            application_path=application_path, authority_path=authority_path,
            lifecycle_ledger_path=lifecycle_ledger_path,
            transition_authority=TransitionAuthority.RUNTIME_MONITOR,
        )
        out["execution"] = "REVOKE_EXECUTED"
    else:
        out["execution"] = "REVIEW_REQUIRED"
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# RESEARCH LAB TRUTH
# ═══════════════════════════════════════════════════════════════════════════════

LAB_LIFECYCLE_SCHEMA = "research_lab_candidate_lifecycle_v1"


def build_candidate_lifecycle_projection(
    *,
    registry_dir: str | None = None,
    optimisation_registry_dir: str | None = None,
    application_path: str | Path | None = None,
    authority_path: str | Path | None = None,
    decisions_dir: str | None = None,
    validation_queue_path: str | Path | None = None,
    lifecycle_ledger_path: str | Path | None = None,
    shadow_summaries: Mapping[str, Mapping[str, Any]] | None = None,
    invalidated_upstream: Sequence[str] = (),
) -> dict[str, Any]:
    """Exact authoritative lifecycle state for every candidate, for the Lab.

    Every candidate is projected onto the ONE canonical vocabulary; the Lab
    never has to infer meaning from a legacy status.  Compatibility/legacy
    registries are labelled, never presented as authority.
    """
    candidate_ids: set[str] = set()
    try:
        from research_engine.v10.candidates.candidate_registry import CandidateRegistry
        registry = (CandidateRegistry(storage_dir=registry_dir) if registry_dir
                    else CandidateRegistry())
        candidate_ids.update(record.candidate_id for record in registry.list_all())
    except Exception:
        pass
    try:
        from research_engine.v10.optimisation.optimisation_registry import (
            OptimisationRegistry,
        )
        opt = OptimisationRegistry(optimisation_registry_dir)
        opt.load()
        candidate_ids.update(row.candidate_id for row in opt.list_candidates())
    except Exception:
        pass
    # A governed authority is itself a candidate identity source: a candidate
    # that exists only as governed production authority must still be visible.
    try:
        from research_engine.control_plane.production_authority import (
            ProductionAuthorityLedger,
        )
        candidate_ids.update(
            row.candidate_id
            for row in ProductionAuthorityLedger(authority_path).effective())
    except Exception:
        pass

    invalidated = {str(item) for item in invalidated_upstream if str(item)}
    rows: list[dict[str, Any]] = []
    for candidate_id in sorted(candidate_ids):
        view = derive_canonical_lifecycle(
            candidate_id=candidate_id, registry_dir=registry_dir,
            optimisation_registry_dir=optimisation_registry_dir,
            application_path=application_path, authority_path=authority_path,
            decisions_dir=decisions_dir, validation_queue_path=validation_queue_path,
            lifecycle_ledger_path=lifecycle_ledger_path,
            shadow_summary=(shadow_summaries or {}).get(candidate_id),
            upstream_invalidated=candidate_id in invalidated,
        )
        row = view.to_dict()
        row["registry_classes"] = dict(STORE_CLASSES)
        row["compatibility_sources"] = sorted(
            store for store, state in view.store_states.items()
            if STORE_CLASSES.get(store) in {"COMPATIBILITY", "LEGACY"}
            and state
        )
        row["authoritative_sources"] = sorted(
            store for store, state in view.store_states.items()
            if STORE_CLASSES.get(store) == "AUTHORITATIVE" and state
        )
        rows.append(row)

    counts: dict[str, int] = {}
    for row in rows:
        counts[row["canonical_state"]] = counts.get(row["canonical_state"], 0) + 1

    return {
        "schema_version": LAB_LIFECYCLE_SCHEMA,
        "candidates": rows,
        "state_counts": dict(sorted(counts.items())),
        "review_ready": [
            {"candidate_id": row["candidate_id"], "notice": REVIEW_STATE_NOTICE}
            for row in rows
            if row["canonical_state"] == CanonicalState.READY_FOR_PROMOTION_REVIEW
        ],
        "runtime_effective": [
            row["candidate_id"] for row in rows if row["runtime_effective"]
        ],
        "action_required": [
            {"candidate_id": row["candidate_id"], "action": row["required_action"]}
            for row in rows if row["required_action"]
        ],
        "disagreements": [
            {"candidate_id": row["candidate_id"], "disagreements": row["disagreements"]}
            for row in rows if row["disagreements"]
        ],
        "store_classes": dict(STORE_CLASSES),
        "canonical_states": list(CANONICAL_STATES),
    }


__all__ = [
    "CandidateLifecycleServiceError", "LAB_LIFECYCLE_SCHEMA",
    "build_candidate_lifecycle_projection", "create_governed_approval",
    "disable_candidate", "governed_human_decision", "invalidate_upstream",
    "monitor_post_deployment", "record_rollback", "revoke_candidate",
    "supersede_candidate",
]
