"""Wire 4 — generic shadow-candidate registration from the optimisation registry.

Proves that the live shadow candidate runtime no longer hard-codes OPT-DP1-002:
every prospective, non-live, TRAILING shadow-bound candidate is registered
generically, unsupported treatment families are blocked explicitly, and a
candidate with live approval is never registered for shadow observation.
"""

from __future__ import annotations

import pytest

from core.shadow.candidate_runtime import CandidateRuntime, CandidateRegistration
from core.shadow.candidate_persistence import CandidateEventWriter
from research_engine.v10.optimisation.models import (
    OptimisationCandidate,
    ValidationPlan,
)
from research_engine.v10.optimisation.optimisation_registry import OptimisationRegistry

import core.shadow.opt_dp1_002 as binding


def _write_registry(tmp_path, candidates):
    registry = OptimisationRegistry(str(tmp_path / "optimisation"))
    for candidate, plan in candidates:
        registry.add_candidate(candidate)
        registry.add_plan(plan)
    registry.save()
    return registry


def _binding_candidate(cid, policy_id, *, live_approved=False, prospective=True):
    return OptimisationCandidate(
        candidate_id=cid, hypothesis_id="", baseline_id="BASE-1",
        status="FORWARD_VALIDATED", policy_id=policy_id,
        treatment_hash="h" * 64,
        shadow_binding={
            "candidate_id": cid, "policy_id": policy_id,
            "treatment_hash": "t" * 64,
            "live_approved": live_approved,
            "prospective_only": prospective,
            "activation_frontier_epoch_s": 1,
            "activated_at": "2026-01-01T00:00:00Z",
        },
    )


def _plan(cid):
    return ValidationPlan(candidate_id=cid, baseline_id="BASE-1",
                          minimum_sample=100, metrics=["m"],
                          success_conditions={"m": "> 0"},
                          failure_conditions={"m": "<= 0"})


def test_generic_shadow_registration_registers_trailing_candidate(tmp_path, monkeypatch):
    _write_registry(tmp_path, [
        (_binding_candidate("OPT-X-1", binding.POLICY_ID), _plan("OPT-X-1")),
    ])
    monkeypatch.setattr(binding, "REGISTRY_PATH",
                        tmp_path / "optimisation" / "registry.json")
    runtime = CandidateRuntime(CandidateEventWriter(str(tmp_path / "candidate")))
    result = binding.register_governed_shadow_candidates(runtime)
    assert result["registered"] == ["OPT-X-1"]
    assert ("OPT-X-1", binding.POLICY_ID) in runtime.registered_keys()


def test_unsupported_treatment_family_is_blocked(tmp_path, monkeypatch):
    _write_registry(tmp_path, [
        (_binding_candidate("OPT-X-2", "REDUCED_TP_0_50R_V1"), _plan("OPT-X-2")),
    ])
    monkeypatch.setattr(binding, "REGISTRY_PATH",
                        tmp_path / "optimisation" / "registry.json")
    runtime = CandidateRuntime(CandidateEventWriter(str(tmp_path / "candidate")))
    result = binding.register_governed_shadow_candidates(runtime)
    assert result["registered"] == []
    assert any("UNSUPPORTED_TREATMENT_FAMILY" in b["reason"]
               for b in result["blocked"])


def test_live_approved_candidate_not_registered(tmp_path, monkeypatch):
    _write_registry(tmp_path, [
        (_binding_candidate("OPT-X-3", binding.POLICY_ID, live_approved=True),
         _plan("OPT-X-3")),
    ])
    monkeypatch.setattr(binding, "REGISTRY_PATH",
                        tmp_path / "optimisation" / "registry.json")
    runtime = CandidateRuntime(CandidateEventWriter(str(tmp_path / "candidate")))
    result = binding.register_governed_shadow_candidates(runtime)
    assert result["registered"] == []
    assert any(b["reason"] == "LIVE_APPROVED_FORBIDDEN" for b in result["blocked"])
