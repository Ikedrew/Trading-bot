"""Governed prospective-only binding for OPT-DP1-002."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Mapping

from core.shadow.candidate_models import TreatmentResult
from core.shadow.candidate_runtime import CandidateRegistration
from core.shadow.frozen_trailing_policy import (
    advance_frozen_trailing,
    initialise_frozen_trailing,
)
from research_engine.control_plane.exit_candidate_replay import CANDIDATE_POLICY_BY_ID

logger = logging.getLogger(__name__)


class OptDp1002AuthorityUnavailable(RuntimeError):
    """The governed binding-authority artifacts are not present on this host.

    The OPT-DP1-002 validation and forward-validation records live under
    ``reports/research/`` (git-ignored) and are produced by the governed
    research pipeline. When they are absent the candidate cannot be verified and
    MUST be treated as unavailable: fail closed by disabling it cleanly, never by
    fabricating, recreating, or weakening evidence. A present-but-inconsistent
    authority is a different condition and still raises.
    """


CANDIDATE_ID = "OPT-DP1-002"
POLICY_ID = "TRAIL_ACT_0_25R_DIST_0_10R_V1"
TREATMENT_HASH = "7b97e5116a6186917799c566b807075159a2cef5cf63921282cc50132b6b53c9"
ACTIVATED_AT = "2026-10-03T11:28:16.389128Z"
ACTIVATION_FRONTIER_EPOCH_S = 1791026896
ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "data" / "research" / "optimisation" / "registry.json"
VALIDATION_PATH = ROOT / "reports" / "research" / "validation" / \
    "VAL-OPT-DP1-002-ISNAP-2211B1EADCDDE8F6F02B59A7-V1.json"
FORWARD_PATH = ROOT / "reports" / "research" / "validation" / \
    "VAL-OPT-DP1-002-FORWARD-ISNAP-6C4F474E0999081BD11DE7C5-V1.json"

POLICY = dict(CANDIDATE_POLICY_BY_ID[POLICY_ID])
READINESS_CRITERIA = {
    "minimum_paired_sample": 300,
    "minimum_candidate_sample": 300,
    "candidate_pf_at_least_baseline": True,
    "max_drawdown_multiple": 1.10,
    "minimum_mean_paired_delta_r": 0.0,
    # Block 2 truthfully reports this unavailable criterion as unsatisfied.
    "confidence_interval": {"lower_bound_gt": 0.0, "cluster": "canonical_opportunity_id"},
    "require_zero_integrity_violations": True,
    "require_complete_pairing": True,
}


class OptDp1002TreatmentAdapter:
    """Thin CandidateTreatmentAdapter around the shared frozen HD09 transition."""

    def initialize(self, *, entry_geometry: dict) -> dict:
        return initialise_frozen_trailing(
            entry_price=entry_geometry["entry_price"],
            stop_loss=entry_geometry["stop_loss"],
            take_profit=entry_geometry["take_profit"],
            risk_distance=entry_geometry["risk_distance"],
            timeout_bars=int(entry_geometry["timeout_bars"]),
        )

    def on_bar(self, *, entry_geometry: dict, risk_distance: float,
               direction: str, bar, prior_state: dict) -> TreatmentResult:
        step = advance_frozen_trailing(
            policy=POLICY,
            entry_price=entry_geometry["entry_price"],
            stop_loss=entry_geometry["stop_loss"],
            take_profit=entry_geometry["take_profit"],
            risk_distance=risk_distance,
            direction=direction,
            bar_high=bar.bar_high,
            bar_low=bar.bar_low,
            bar_close=bar.bar_close,
            prior_state=prior_state,
        )
        return TreatmentResult(
            treatment_state=step.state,
            terminal=step.terminal,
            exit_reason=step.exit_reason,
            exit_price=step.exit_price,
        )


class GovernedTrailingTreatmentAdapter:
    """Policy-parameterised candidate treatment adapter for TRAILING policies.

    The generic counterpart of ``OptDp1002TreatmentAdapter``: identical frozen
    HD09 transition, but bound to any governed TRAILING policy rather than the
    single historical policy.
    """

    def __init__(self, policy: Mapping[str, Any]) -> None:
        if str(policy.get("policy_type") or "") != "TRAILING":
            raise ValueError("GOVERNED_TRAILING_ADAPTER_POLICY_TYPE_MISMATCH")
        self._policy = dict(policy)

    def initialize(self, *, entry_geometry: dict) -> dict:
        return initialise_frozen_trailing(
            entry_price=entry_geometry["entry_price"],
            stop_loss=entry_geometry["stop_loss"],
            take_profit=entry_geometry["take_profit"],
            risk_distance=entry_geometry["risk_distance"],
            timeout_bars=int(entry_geometry["timeout_bars"]),
        )

    def on_bar(self, *, entry_geometry: dict, risk_distance: float,
               direction: str, bar, prior_state: dict) -> TreatmentResult:
        step = advance_frozen_trailing(
            policy=self._policy,
            entry_price=entry_geometry["entry_price"],
            stop_loss=entry_geometry["stop_loss"],
            take_profit=entry_geometry["take_profit"],
            risk_distance=risk_distance,
            direction=direction,
            bar_high=bar.bar_high,
            bar_low=bar.bar_low,
            bar_close=bar.bar_close,
            prior_state=prior_state,
        )
        return TreatmentResult(
            treatment_state=step.state,
            terminal=step.terminal,
            exit_reason=step.exit_reason,
            exit_price=step.exit_price,
        )


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        # The governed authority artifact is legitimately absent on this host.
        raise OptDp1002AuthorityUnavailable(
            "OPT_DP1_002_AUTHORITY_UNAVAILABLE:" + str(path)) from exc
    except (OSError, ValueError) as exc:
        # Present but unreadable/corrupt: a governance violation, not absence.
        raise RuntimeError("OPT_DP1_002_AUTHORITY_UNREADABLE:" + str(path)) from exc
    if not isinstance(value, dict):
        raise RuntimeError("OPT_DP1_002_AUTHORITY_INVALID:" + str(path))
    return value


def verify_binding_authority() -> dict[str, Any]:
    """Fail closed unless registry, validation, and forward lineage agree."""
    registry = _read(REGISTRY_PATH)
    validation = _read(VALIDATION_PATH)
    forward = _read(FORWARD_PATH)
    candidate = dict((registry.get("candidates") or {}).get(CANDIDATE_ID) or {})
    plan = dict((registry.get("plans") or {}).get(CANDIDATE_ID) or {})
    binding = dict(candidate.get("shadow_binding") or {})
    errors: list[str] = []
    if candidate.get("candidate_id") != CANDIDATE_ID:
        errors.append("CANDIDATE_ID_MISMATCH")
    if dict(candidate.get("changes") or {}).get("treatment") != POLICY_ID:
        errors.append("POLICY_ID_MISMATCH")
    if POLICY != {"policy_id": POLICY_ID, "policy_type": "TRAILING",
                  "activation_r": 0.25, "distance_r": 0.10}:
        errors.append("FROZEN_POLICY_MISMATCH")
    historical = dict(dict(validation.get("comparison") or {}).get("treatment") or {})
    forward_treatment = dict(forward.get("treatment") or {})
    for label, treatment in (("HISTORICAL", historical), ("FORWARD", forward_treatment)):
        if treatment.get("policy_id") != POLICY_ID:
            errors.append(label + "_POLICY_ID_MISMATCH")
        if treatment.get("treatment_hash") != TREATMENT_HASH:
            errors.append(label + "_TREATMENT_HASH_MISMATCH")
        if treatment.get("activation_r") != 0.25 or treatment.get("distance_r") != 0.10:
            errors.append(label + "_PARAMETER_MISMATCH")
    if validation.get("candidate_id") != CANDIDATE_ID or \
            dict(validation.get("comparison") or {}).get("validation_status") != "VALIDATED":
        errors.append("HISTORICAL_VALIDATION_MISSING")
    if forward.get("candidate_id") != CANDIDATE_ID or \
            forward.get("forward_validation_status") != "OPT_DP1_002_FORWARD_VALIDATED":
        errors.append("FORWARD_VALIDATION_MISSING")
    governance = dict(forward.get("governance") or {})
    if governance.get("treatment_parameters_changed") is not False:
        errors.append("FORWARD_TREATMENT_CHANGED")
    if int(plan.get("minimum_sample") or 0) != 300:
        errors.append("MINIMUM_SAMPLE_MISMATCH")
    if candidate.get("status") in {"SUPERSEDED", "INVALIDATED", "REJECTED", "LIVE_APPROVED", "ACCEPTED"}:
        errors.append("CANDIDATE_NOT_ELIGIBLE:" + str(candidate.get("status")))
    if binding:
        expected = {
            "candidate_id": CANDIDATE_ID, "policy_id": POLICY_ID,
            "treatment_hash": TREATMENT_HASH,
            "activation_frontier_epoch_s": ACTIVATION_FRONTIER_EPOCH_S,
        }
        if any(binding.get(key) != value for key, value in expected.items()):
            errors.append("PERSISTED_BINDING_MISMATCH")
        if binding.get("live_approved") is not False:
            errors.append("PERSISTED_BINDING_LIVE_APPROVAL_INVALID")
    if errors:
        raise RuntimeError("OPT_DP1_002_BINDING_AUTHORITY_INVALID:" + ",".join(errors))
    return {"candidate": candidate, "plan": plan, "validation": validation,
            "forward": forward, "binding": binding}


_DISABLE_LOGGED = False


def register_opt_dp1_002(runtime) -> bool:
    """Register the candidate only when governed authority verifies.

    When the governed authority artifacts are legitimately unavailable on this
    host the candidate is disabled cleanly (returns ``False``) and the condition
    is logged exactly once — it is never retried per bar and never raises. A
    present-but-inconsistent authority still fails closed by raising, so a real
    governance violation is never silently suppressed.
    """
    global _DISABLE_LOGGED
    try:
        authority = verify_binding_authority()
    except OptDp1002AuthorityUnavailable as exc:
        if not _DISABLE_LOGGED:
            logger.warning(
                "[OPT_DP1_002_CANDIDATE_UNAVAILABLE] candidate=%s disabled: %s",
                CANDIDATE_ID, exc,
            )
            _DISABLE_LOGGED = True
        return False
    registration = CandidateRegistration(
        candidate_id=CANDIDATE_ID,
        policy_id=POLICY_ID,
        treatment_hash=TREATMENT_HASH,
        adapter=OptDp1002TreatmentAdapter(),
        provenance={
            "candidate_registry": str(REGISTRY_PATH.relative_to(ROOT)),
            "validation_record": str(VALIDATION_PATH.relative_to(ROOT)),
            "forward_validation_record": str(FORWARD_PATH.relative_to(ROOT)),
            "validation_id": authority["validation"]["validation_id"],
            "forward_validation_id": authority["forward"]["validation_id"],
            "policy_authority": "research_engine.registry.exit_policy_adjudication.CANDIDATE_POLICIES_V1",
            "activated_at": ACTIVATED_AT,
            "activation_frontier_epoch_s": ACTIVATION_FRONTIER_EPOCH_S,
        },
        required_experiment_arm="CANDIDATE",
        activation_frontier_epoch_s=ACTIVATION_FRONTIER_EPOCH_S,
        minimum_sample_requirement=300,
        readiness_criteria=READINESS_CRITERIA,
    )
    return runtime.register(registration)


def register_governed_shadow_candidates(runtime) -> dict[str, Any]:
    """Register every eligible prospective shadow-bound candidate generically.

    Reads the authoritative optimisation registry and registers any candidate
    carrying a prospective, non-live shadow binding whose governed policy is a
    TRAILING policy.  Non-TRAILING families are blocked explicitly rather than
    forced through an incompatible adapter.  When the registry is unavailable,
    falls back to the strict historical OPT-DP1-002 binding so live-runtime
    behaviour never silently drops the one verified candidate.
    """
    from research_engine.control_plane.exit_candidate_replay import (
        CANDIDATE_POLICY_BY_ID,
    )
    from research_engine.v10.optimisation.optimisation_registry import (
        OptimisationRegistry,
    )

    registry = OptimisationRegistry(str(REGISTRY_PATH.parent))
    try:
        registry.load()
    except Exception as exc:
        logger.debug("[SHADOW_CANDIDATE_REGISTRY_UNAVAILABLE] %s", exc)
        register_opt_dp1_002(runtime)
        return {"registered": [], "blocked": [
            {"candidate_id": "", "reason": "REGISTRY_UNAVAILABLE"}]}

    registered: list[str] = []
    blocked: list[dict[str, str]] = []
    for candidate in sorted(registry.list_candidates(), key=lambda c: c.candidate_id):
        binding = dict(candidate.shadow_binding or {})
        if not binding:
            continue
        cid = str(candidate.candidate_id or "")
        if binding.get("live_approved") is not False:
            blocked.append({"candidate_id": cid, "reason": "LIVE_APPROVED_FORBIDDEN"})
            continue
        if binding.get("prospective_only") is not True:
            blocked.append({"candidate_id": cid, "reason": "NOT_PROSPECTIVE_ONLY"})
            continue
        policy_id = str(binding.get("policy_id") or candidate.policy_id or "")
        policy = CANDIDATE_POLICY_BY_ID.get(policy_id)
        if policy is None:
            blocked.append({"candidate_id": cid,
                            "reason": "UNGOVERNED_POLICY:" + policy_id})
            continue
        if str(policy.get("policy_type") or "") != "TRAILING":
            blocked.append({"candidate_id": cid, "reason":
                            "UNSUPPORTED_TREATMENT_FAMILY:" + str(policy.get("policy_type"))})
            continue
        treatment_hash = str(binding.get("treatment_hash") or candidate.treatment_hash or "")
        if not treatment_hash:
            blocked.append({"candidate_id": cid, "reason": "MISSING_TREATMENT_HASH"})
            continue
        frontier = int(binding.get("activation_frontier_epoch_s") or 0)
        plan = registry.get_plan(cid)
        try:
            registry_provenance = str(REGISTRY_PATH.relative_to(ROOT))
        except ValueError:
            registry_provenance = str(REGISTRY_PATH)
        registration = CandidateRegistration(
            candidate_id=cid,
            policy_id=policy_id,
            treatment_hash=treatment_hash,
            adapter=GovernedTrailingTreatmentAdapter(policy),
            provenance={
                "candidate_registry": registry_provenance,
                "policy_authority":
                    "research_engine.registry.exit_policy_adjudication.CANDIDATE_POLICIES_V1",
                "activated_at": str(binding.get("activated_at") or ""),
                "activation_frontier_epoch_s": frontier,
            },
            required_experiment_arm="CANDIDATE",
            activation_frontier_epoch_s=frontier,
            minimum_sample_requirement=(int(plan.minimum_sample)
                                        if plan is not None and plan.minimum_sample else None),
        )
        if runtime.register(registration):
            registered.append(cid)
    if not registered and not blocked:
        register_opt_dp1_002(runtime)
    return {"registered": registered, "blocked": blocked}


__all__ = [
    "ACTIVATED_AT", "ACTIVATION_FRONTIER_EPOCH_S", "CANDIDATE_ID",
    "GovernedTrailingTreatmentAdapter",
    "OptDp1002AuthorityUnavailable", "OptDp1002TreatmentAdapter", "POLICY",
    "POLICY_ID", "READINESS_CRITERIA", "TREATMENT_HASH",
    "register_governed_shadow_candidates",
    "register_opt_dp1_002", "verify_binding_authority",
]
