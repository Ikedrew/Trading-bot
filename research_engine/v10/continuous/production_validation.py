"""Production scientific executors for the continuous validation queue.

The executors consume only the immutable governed counterfactual-evidence
store.  They never load legacy reports or fixtures and never grant runtime
authority.  A forward verdict additionally requires a later, non-overlapping
governed population than the completed initial validation record.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
from pathlib import Path
from typing import Any, Mapping

from research_engine.control_plane.governed_counterfactual_evidence import (
    BASELINE_POLICY_ID,
    CounterfactualEvidenceStore,
    GovernedCounterfactualEvidence,
    VALIDITY_ELIGIBLE,
    treatment_signature,
    verify_governed_counterfactual_binding,
)
from research_engine.control_plane.stage4_dataset_snapshot import canonical_json
from research_engine.experiments.exit_policy_governed import (
    CANDIDATE_POLICY_IDS,
    clustered_cr0_cell_means,
    holm_adjust,
)
from research_engine.v10.continuous.validation_queue import (
    FORWARD_VALIDATION,
    VALIDATION,
    ValidationJob,
    ValidationQueueStore,
)
from research_engine.v10.continuous.candidate_observation import (
    CandidateObservationStore, observation_registration_problem,
)
from research_engine.v10.optimisation.models import OptimisationCandidate


EVALUATOR_IDENTITY = "production_governed_counterfactual_validation_v1"
SUPPORTED_METRICS = frozenset({
    "weighted_effect_estimate", "holm_adjusted_p_value",
    "confidence_interval_95_lower", "confidence_interval_95_upper",
    "baseline_sample_count", "treatment_sample_count",
    "comparable_observation_count", "distinct_canonical_opportunities",
    "baseline_expected_r", "treatment_expected_r", "expected_r_delta",
    "scope_fidelity",
})
_OPERATORS = frozenset({"gt", "gte", "lt", "lte", "eq"})


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(dict(value)).encode("utf-8")).hexdigest()


def _not_before(value: str, boundary: str) -> bool:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")) >= datetime.fromisoformat(
            boundary.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False


def _membership(rows: Any) -> list[dict[str, Any]]:
    """Unique observation roots across the complete tested policy family."""
    roots = {
        (row.canonical_opportunity_id, tuple(row.lifecycle_identity))
        for row in rows
    }
    return sorted(({
        "canonical_opportunity_id": opportunity,
        "lifecycle_identity": list(lifecycle),
    } for opportunity, lifecycle in roots), key=lambda item: (
        item["canonical_opportunity_id"], item["lifecycle_identity"]))


def _result(job: ValidationJob, status: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {
        "evaluation_schema": "scientific_validation_record_v1",
        "evaluation_version": EVALUATOR_IDENTITY,
        "evaluation_id": "VEVAL-" + _digest({
            "job_id": job.job_id,
            "status": status,
            "reason": reason,
            "evidence_dataset_id": extra.get("evidence_dataset_id"),
        })[:24].upper(),
        "job_id": job.job_id,
        "kind": job.kind,
        "candidate_id": job.candidate_id,
        "snapshot_id": job.source_snapshot_id,
        "source_snapshot_id": job.source_snapshot_id,
        "treatment_hash": job.treatment_hash,
        "status": status,
        "reason": reason,
        "live_approved": False,
        **extra,
    }


def _condition(metric: str, specification: Any, measured: Mapping[str, Any]) -> tuple[bool, str]:
    if metric == "scope_fidelity":
        text = str(specification).lower()
        return ("only" in text or "unchanged" in text or "scope" in text,
                "declared treatment-scope fidelity")
    if metric not in SUPPORTED_METRICS or metric not in measured:
        raise ValueError("UNKNOWN_OR_UNMEASURABLE_CRITERION:" + metric)
    if not isinstance(specification, Mapping) or len(specification) != 1:
        raise ValueError("CRITERION_FORMAT_UNSUPPORTED:" + metric)
    operator, threshold = next(iter(specification.items()))
    if operator not in _OPERATORS or isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise ValueError("CRITERION_OPERATOR_UNSUPPORTED:" + metric)
    actual = measured[metric]
    if isinstance(actual, bool) or not isinstance(actual, (int, float)):
        raise ValueError("CRITERION_VALUE_UNMEASURABLE:" + metric)
    expected = float(threshold)
    passed = {
        "gt": actual > expected, "gte": actual >= expected,
        "lt": actual < expected, "lte": actual <= expected,
        "eq": actual == expected,
    }[str(operator)]
    return passed, f"{actual} {operator} {expected}"


@dataclass
class ProductionValidationExecutor:
    """Queue-callable executor backed by immutable governed evidence."""

    evidence_store: CounterfactualEvidenceStore
    queue_path: Path
    observation_store_path: Path
    kind: str = VALIDATION

    def _artifacts(self) -> tuple[GovernedCounterfactualEvidence, ...]:
        artifacts = []
        for dataset_id in self.evidence_store.dataset_ids():
            artifact = self.evidence_store.load(dataset_id)
            if artifact is not None:
                binding = self.evidence_store.binding_for(dataset_id)
                verify_governed_counterfactual_binding(
                    binding, snapshot_id=artifact.snapshot_id,
                    snapshot_fingerprint=artifact.snapshot_fingerprint,
                    investigation_epoch=artifact.investigation_epoch,
                    evidence=artifact,
                )
                artifacts.append(artifact)
        return tuple(sorted(artifacts, key=lambda item: (
            item.frontier_end, item.investigation_epoch, item.dataset_id)))

    def _initial_record(self, candidate_id: str) -> Mapping[str, Any] | None:
        store = ValidationQueueStore(self.queue_path)
        records = [
            job.output_validation_record for job in store.ordered()
            if job.kind == VALIDATION and job.candidate_id == candidate_id
            and job.status == "COMPLETED" and job.output_validation_record
            and job.output_validation_record.get("status") == "VALIDATED"
        ]
        if len(records) != 1:
            return None
        return records[0]

    def __call__(
        self, candidate: OptimisationCandidate, plan: Mapping[str, Any], job: ValidationJob,
    ) -> Mapping[str, Any]:
        if job.kind != self.kind:
            return _result(job, "BLOCKED", "EXECUTOR_STAGE_MISMATCH")
        expected_status = "PROPOSED" if self.kind == VALIDATION else "VALIDATED"
        if candidate.status != expected_status:
            return _result(job, "BLOCKED", "CANDIDATE_STATE_INELIGIBLE:" + candidate.status)
        if (candidate.candidate_id != job.candidate_id
                or candidate.policy_id != job.policy_id
                or candidate.treatment_hash != job.treatment_hash):
            return _result(job, "BLOCKED", "CANDIDATE_JOB_IDENTITY_MISMATCH")
        if (self.kind == VALIDATION
                and str((candidate.provenance or {}).get("snapshot_id") or "")
                != job.source_snapshot_id):
            return _result(job, "BLOCKED", "CANDIDATE_SOURCE_SNAPSHOT_MISMATCH")
        if (plan.get("candidate_id") != candidate.candidate_id
                or plan.get("baseline_id") != candidate.baseline_id):
            return _result(job, "REVIEW_REQUIRED", "FROZEN_PLAN_IDENTITY_MISMATCH")
        try:
            registration = CandidateObservationStore(self.observation_store_path).load().get(
                candidate.candidate_id)
        except Exception as exc:
            return _result(job, "BLOCKED", "CANDIDATE_OBSERVATION_REGISTRATION_INVALID",
                           registration_error=f"{type(exc).__name__}:{exc}")
        registration_problem = observation_registration_problem(
            registration, candidate, plan)
        if registration_problem:
            return _result(job, "BLOCKED", registration_problem)
        if self.kind == VALIDATION and registration.source_snapshot_id != job.source_snapshot_id:
            return _result(job, "BLOCKED", "REGISTRATION_JOB_SNAPSHOT_MISMATCH")
        requirements = candidate.validation_requirements or {}
        expected_metrics = list(requirements.get("primary_metrics") or [])
        expected_metrics += [item for item in requirements.get("secondary_metrics", [])
                             if item not in expected_metrics]
        if requirements and (
                int(requirements.get("required_sample") or 0)
                != int(plan.get("minimum_sample") or 0)
                or expected_metrics != list(plan.get("metrics") or [])
                or dict(requirements.get("success_conditions") or {})
                != dict(plan.get("success_conditions") or {})
                or dict(requirements.get("failure_conditions") or {})
                != dict(plan.get("failure_conditions") or {})):
            return _result(job, "BLOCKED", "CANDIDATE_PLAN_FREEZE_MISMATCH")
        unknown_metrics = sorted(set(plan.get("metrics") or ()) - SUPPORTED_METRICS)
        if not plan.get("metrics") or unknown_metrics:
            return _result(job, "REVIEW_REQUIRED", "PLAN_METRIC_UNSUPPORTED",
                           unsupported_metrics=unknown_metrics)
        frozen = candidate.changes.get("frozen_policy") if candidate.changes else None
        if not isinstance(frozen, Mapping):
            return _result(job, "BLOCKED", "FROZEN_TREATMENT_MISSING")
        if _digest(dict(frozen)) != candidate.treatment_hash:
            return _result(job, "BLOCKED", "TREATMENT_HASH_MISMATCH")
        if str(frozen.get("policy_id") or "") != candidate.policy_id:
            return _result(job, "BLOCKED", "TREATMENT_POLICY_ID_MISMATCH")
        authority_question = str(
            (candidate.target_population or {}).get("canonical_authority_question_id")
            or (candidate.target_population or {}).get("question_id") or "")
        if authority_question not in {"EX1", "EX9"}:
            return _result(
                job, "REVIEW_REQUIRED", "MEASUREMENT_PROTOCOL_UNSUPPORTED",
                canonical_authority_question_id=authority_question or None,
                supported_protocols=["EX1", "EX9"],
            )

        try:
            artifacts = self._artifacts()
        except Exception as exc:
            return _result(job, "BLOCKED", "EVIDENCE_AUTHORITY_INVALID",
                           authority_error=f"{type(exc).__name__}:{exc}")

        initial = None
        if self.kind == FORWARD_VALIDATION:
            initial = self._initial_record(candidate.candidate_id)
            if initial is None:
                return _result(job, "BLOCKED", "INITIAL_VALIDATION_RECORD_MISSING")
            if (initial.get("candidate_id") != candidate.candidate_id
                    or initial.get("treatment_hash") != candidate.treatment_hash
                    or initial.get("baseline_id") != candidate.baseline_id
                    or initial.get("plan_digest") != _digest(dict(plan))
                    or initial.get("observation_registration_id") != registration.registration_id):
                return _result(job, "BLOCKED", "INITIAL_VALIDATION_IDENTITY_MISMATCH")
            manifest = initial.get("evidence_observation_membership")
            manifest_digest = initial.get("evidence_membership_digest")
            if (not isinstance(manifest, list) or not manifest
                    or not isinstance(manifest_digest, str)
                    or _digest({"members": manifest}) != manifest_digest):
                return _result(job, "BLOCKED", "INITIAL_MEMBERSHIP_INVALID_OR_MISSING")
            if any(
                    not isinstance(item, Mapping)
                    or not isinstance(item.get("canonical_opportunity_id"), str)
                    or not item["canonical_opportunity_id"]
                    or not isinstance(item.get("lifecycle_identity"), list)
                    or len(item["lifecycle_identity"]) != 3
                    or not all(isinstance(part, str) and part
                               for part in item["lifecycle_identity"])
                    for item in manifest):
                return _result(job, "BLOCKED", "INITIAL_MEMBERSHIP_INVALID_OR_MISSING")
            initial_artifact = next((item for item in artifacts
                                     if item.dataset_id == initial.get("evidence_dataset_id")), None)
            if (initial_artifact is None
                    or initial_artifact.content_digest != initial.get("evidence_content_digest")
                    or _membership(initial_artifact.rows) != manifest):
                return _result(job, "BLOCKED", "INITIAL_MEMBERSHIP_AUTHORITY_UNVERIFIABLE")
            initial_opportunities = {item.get("canonical_opportunity_id")
                                     for item in manifest if isinstance(item, Mapping)}
            initial_lifecycles = {tuple(item.get("lifecycle_identity") or ())
                                  for item in manifest if isinstance(item, Mapping)}
            if (None in initial_opportunities or "" in initial_opportunities
                    or len(initial_opportunities) != len(manifest)
                    or len(initial_lifecycles) != len(manifest)):
                return _result(job, "BLOCKED", "INITIAL_MEMBERSHIP_INVALID_OR_MISSING")
            initial_end = str(initial.get("evidence_frontier_end") or "")
            artifacts = tuple(item for item in artifacts
                              if item.dataset_id != initial.get("evidence_dataset_id")
                              and _not_before(item.frontier_start, initial_end))
        else:
            artifacts = tuple(item for item in artifacts if item.snapshot_id == job.source_snapshot_id)

        eligible = tuple(item for item in artifacts
                         if candidate.policy_id in item.governed_policy_ids)
        if not eligible:
            reason = ("FORWARD_EVIDENCE_NOT_YET_AVAILABLE" if self.kind == FORWARD_VALIDATION
                      else "VALIDATION_EVIDENCE_NOT_YET_AVAILABLE")
            return _result(job, "WAITING_FOR_DATA", reason,
                           initial_evaluation_id=None if initial is None else initial.get("evaluation_id"))
        if self.kind == VALIDATION and len(eligible) != 1:
            return _result(job, "BLOCKED", "AMBIGUOUS_EVIDENCE_AUTHORITY",
                           evidence_dataset_ids=[item.dataset_id for item in eligible])
        # Forward validation freezes the first qualifying post-initial
        # population.  Later artifacts cannot retroactively replace it.
        evidence = eligible[0]
        if not evidence.scientifically_analysable:
            return _result(job, "WAITING_FOR_DATA", "EVIDENCE_NOT_SCIENTIFICALLY_ANALYSABLE",
                           evidence_dataset_id=evidence.dataset_id)
        if evidence.baseline_policy_id != BASELINE_POLICY_ID:
            return _result(job, "BLOCKED", "FROZEN_BASELINE_POLICY_MISMATCH",
                           evidence_dataset_id=evidence.dataset_id)
        if initial is not None and evidence.baseline_policy_id != initial.get("baseline_policy_id"):
            return _result(job, "BLOCKED", "FORWARD_BASELINE_POLICY_MISMATCH",
                           evidence_dataset_id=evidence.dataset_id)
        if self.kind == VALIDATION and candidate.baseline_id != evidence.snapshot_id:
            return _result(job, "BLOCKED", "FROZEN_BASELINE_SNAPSHOT_MISMATCH",
                           evidence_dataset_id=evidence.dataset_id)
        if self.kind == VALIDATION and (
                evidence.snapshot_id != registration.source_snapshot_id
                or evidence.investigation_epoch != registration.investigation_epoch):
            return _result(job, "BLOCKED", "REGISTRATION_EVIDENCE_SOURCE_MISMATCH",
                           evidence_dataset_id=evidence.dataset_id)

        rows = evidence.rows_for_policy(candidate.policy_id)
        identities = [tuple(row.lifecycle_identity) for row in rows]
        if len(set(identities)) != len(identities):
            return _result(job, "BLOCKED", "DUPLICATE_EVIDENCE_GRAIN",
                           evidence_dataset_id=evidence.dataset_id)
        opportunities = [row.canonical_opportunity_id for row in rows]
        if (not all(opportunities) or len(set(opportunities)) != len(opportunities)):
            return _result(job, "BLOCKED", "DUPLICATE_OR_MISSING_OPPORTUNITY_ID",
                           evidence_dataset_id=evidence.dataset_id)
        family_grains = [(row.governed_policy_id, row.canonical_opportunity_id)
                         for row in evidence.rows]
        if (len(set(family_grains)) != len(family_grains)
                or any(row.governed_policy_id not in CANDIDATE_POLICY_IDS
                       or row.validity != VALIDITY_ELIGIBLE
                       or row.baseline_policy_id != evidence.baseline_policy_id
                       or not row.canonical_opportunity_id
                       or row.lifecycle_identity[1] != row.canonical_opportunity_id
                       or row.lifecycle_identity[2] != row.trade_horizon
                       for row in evidence.rows)):
            return _result(job, "BLOCKED", "FAMILY_EVIDENCE_MEMBERSHIP_INVALID",
                           evidence_dataset_id=evidence.dataset_id)
        family_membership = _membership(evidence.rows)
        if (not family_membership
                or len({item["canonical_opportunity_id"] for item in family_membership})
                != len(family_membership)):
            return _result(job, "BLOCKED", "AMBIGUOUS_FAMILY_OBSERVATION_MEMBERSHIP",
                           evidence_dataset_id=evidence.dataset_id)
        if initial is not None and (
                {item["canonical_opportunity_id"] for item in family_membership}
                .intersection(initial_opportunities)
                or {tuple(item["lifecycle_identity"]) for item in family_membership}
                .intersection(initial_lifecycles)):
            return _result(job, "BLOCKED", "FORWARD_OBSERVATION_POPULATION_OVERLAP",
                           evidence_dataset_id=evidence.dataset_id)
        for row in rows:
            if (row.validity != VALIDITY_ELIGIBLE
                    or row.baseline_policy_id != evidence.baseline_policy_id
                    or row.governed_policy_id != candidate.policy_id
                    or dict(row.treatment_parameters) != dict(frozen)
                    or row.treatment_signature != treatment_signature(dict(frozen))
                    or row.lifecycle_identity[1] != row.canonical_opportunity_id
                    or row.lifecycle_identity[2] != row.trade_horizon
                    or row.replay_method != evidence.replay_method
                    or row.replay_version != evidence.replay_version
                    or not all((row.leakage_guard.get("post_entry_only"),
                                row.leakage_guard.get("strictly_ascending"),
                                row.leakage_guard.get("exit_aligned")))):
                return _result(job, "BLOCKED", "EVIDENCE_PROVENANCE_INVALID",
                               evidence_dataset_id=evidence.dataset_id)

        minimum = plan.get("minimum_sample")
        if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum <= 0:
            return _result(job, "REVIEW_REQUIRED", "MINIMUM_SAMPLE_MISSING")
        if len(rows) < minimum:
            return _result(job, "WAITING_FOR_DATA", "MINIMUM_SAMPLE_UNMET",
                           evidence_dataset_id=evidence.dataset_id,
                           measured_sample=len(rows), required_sample=minimum,
                           evidence_row_digests=[row.row_digest for row in rows])

        family_policies = tuple(policy for policy in CANDIDATE_POLICY_IDS
                                if evidence.rows_for_policy(policy))
        if family_policies != tuple(CANDIDATE_POLICY_IDS):
            return _result(job, "WAITING_FOR_DATA", "HOLM_FAMILY_INCOMPLETE",
                           evidence_dataset_id=evidence.dataset_id,
                           observed_policy_ids=list(family_policies),
                           required_policy_ids=list(CANDIDATE_POLICY_IDS))
        if authority_question == "EX1":
            family = tuple("EX1:" + policy for policy in family_policies)
            analytical_rows = [{
                "test_id": "EX1:" + row.governed_policy_id,
                "lifecycle_identity": row.lifecycle_identity,
                "canonical_opportunity_id": row.canonical_opportunity_id,
                "value": row.delta_r,
            } for policy in family_policies for row in evidence.rows_for_policy(policy)]
            primary_test_id = "EX1:" + candidate.policy_id
        else:
            endpoint_a = "paired_timeout_indicator_change"
            endpoint_b = "baseline_timeout_loss_converted_positive"
            family_a = tuple(f"EX9:{endpoint_a}:{policy}" for policy in family_policies)
            family_b = tuple(f"EX9:{endpoint_b}:{policy}" for policy in family_policies)
            family = family_a + family_b
            analytical_rows = []
            for policy in family_policies:
                for row in evidence.rows_for_policy(policy):
                    analytical_rows.append({
                        "test_id": f"EX9:{endpoint_a}:{policy}",
                        "lifecycle_identity": row.lifecycle_identity,
                        "canonical_opportunity_id": row.canonical_opportunity_id,
                        "value": (int(row.counterfactual_exit.get("exit_reason")
                                      in {"timeout", "time_cap"})
                                  - int(row.baseline_exit.get("exit_reason") == "timeout")),
                    })
                    if (row.baseline_exit.get("exit_reason") == "timeout"
                            and row.baseline_r < 0):
                        analytical_rows.append({
                            "test_id": f"EX9:{endpoint_b}:{policy}",
                            "lifecycle_identity": row.lifecycle_identity,
                            "canonical_opportunity_id": row.canonical_opportunity_id,
                            "value": int(row.counterfactual_r > 0),
                        })
            primary_test_id = f"EX9:{endpoint_a}:{candidate.policy_id}"
        try:
            tests = clustered_cr0_cell_means(analytical_rows, family)
            adjusted = holm_adjust(
                [(test["test_identity"], test["raw_two_sided_p_value"])
                 for test in tests], family)
            test = next(item for item in tests
                        if item["test_identity"] == primary_test_id)
        except Exception as exc:
            return _result(job, "WAITING_FOR_DATA", "STATISTICAL_ESTIMATION_UNAVAILABLE",
                           evidence_dataset_id=evidence.dataset_id,
                           statistical_error=f"{type(exc).__name__}:{exc}")

        baseline_mean = sum(row.baseline_r for row in rows) / len(rows)
        treatment_mean = sum(row.counterfactual_r for row in rows) / len(rows)
        measured = {
            "weighted_effect_estimate": test["weighted_effect_estimate"],
            "holm_adjusted_p_value": adjusted[test["test_identity"]],
            "confidence_interval_95_lower": test["confidence_interval_95"][0],
            "confidence_interval_95_upper": test["confidence_interval_95"][1],
            "baseline_sample_count": len(rows),
            "treatment_sample_count": len(rows),
            "comparable_observation_count": len(rows),
            "distinct_canonical_opportunities": test["distinct_canonical_opportunity_count"],
            "baseline_expected_r": baseline_mean,
            "treatment_expected_r": treatment_mean,
            "expected_r_delta": treatment_mean - baseline_mean,
            "endpoint_b_distinct_opportunities": len({
                row.canonical_opportunity_id for row in rows
                if row.baseline_exit.get("exit_reason") == "timeout" and row.baseline_r < 0
            }),
            "scope_fidelity": True,
        }
        minimum_requirements = requirements.get("minimum_evidence_requirements") or {}
        if not isinstance(minimum_requirements, Mapping):
            return _result(job, "REVIEW_REQUIRED", "MINIMUM_EVIDENCE_REQUIREMENTS_INVALID",
                           evidence_dataset_id=evidence.dataset_id, measured=measured)
        supported_gates = {
            "minimum_paired_lifecycles": "comparable_observation_count",
            "minimum_distinct_opportunities": "distinct_canonical_opportunities",
            "endpoint_b_minimum_distinct_opportunities": (
                "endpoint_b_distinct_opportunities"),
        }
        unknown_gates = sorted(set(minimum_requirements) - set(supported_gates))
        if unknown_gates:
            return _result(job, "REVIEW_REQUIRED", "EVIDENCE_GATE_UNSUPPORTED",
                           evidence_dataset_id=evidence.dataset_id, measured=measured,
                           unsupported_evidence_gates=unknown_gates)
        unmet_gates = {
            gate: {"required": required, "measured": measured[supported_gates[gate]]}
            for gate, required in minimum_requirements.items()
            if (isinstance(required, bool) or not isinstance(required, (int, float))
                or measured[supported_gates[gate]] < required)
        }
        if unmet_gates:
            return _result(job, "WAITING_FOR_DATA", "MINIMUM_EVIDENCE_GATE_UNMET",
                           evidence_dataset_id=evidence.dataset_id, measured=measured,
                           unmet_evidence_gates=unmet_gates,
                           evidence_row_digests=[row.row_digest for row in rows])
        success = plan.get("success_conditions")
        failure = plan.get("failure_conditions")
        if not isinstance(success, Mapping) or not success or not isinstance(failure, Mapping) or not failure:
            return _result(job, "REVIEW_REQUIRED", "SUCCESS_OR_FAILURE_CRITERIA_MISSING",
                           evidence_dataset_id=evidence.dataset_id, measured=measured)
        try:
            success_results = {name: _condition(str(name), spec, measured)
                               for name, spec in success.items()}
            failure_results = {name: _condition(str(name), spec, measured)
                               for name, spec in failure.items()}
        except ValueError as exc:
            return _result(job, "REVIEW_REQUIRED", str(exc),
                           evidence_dataset_id=evidence.dataset_id, measured=measured)
        success_passed = all(item[0] for item in success_results.values())
        failure_triggered = any(item[0] for item in failure_results.values())
        verdict = ("FORWARD_VALIDATED" if self.kind == FORWARD_VALIDATION else "VALIDATED")
        if not success_passed:
            verdict = "REJECTED" if failure_triggered else "FAILED"
        return _result(
            job, verdict, "FROZEN_CRITERIA_SATISFIED" if success_passed else
            ("FROZEN_FAILURE_CONDITION_TRIGGERED" if failure_triggered
             else "FROZEN_SUCCESS_CONDITIONS_NOT_SATISFIED"),
            baseline_id=candidate.baseline_id,
            baseline_policy_id=evidence.baseline_policy_id,
            snapshot_id=evidence.snapshot_id,
            plan_digest=_digest(dict(plan)),
            observation_registration_id=registration.registration_id,
            evidence_dataset_id=evidence.dataset_id,
            evidence_content_digest=evidence.content_digest,
            evidence_snapshot_fingerprint=evidence.snapshot_fingerprint,
            evidence_frontier_start=evidence.frontier_start,
            evidence_frontier_end=evidence.frontier_end,
            evidence_row_digests=[row.row_digest for row in rows],
            evidence_observation_membership=family_membership,
            evidence_membership_digest=_digest({"members": family_membership}),
            measured=measured,
            evaluated_success_conditions={key: {"passed": value[0], "detail": value[1]}
                                          for key, value in success_results.items()},
            evaluated_failure_conditions={key: {"triggered": value[0], "detail": value[1]}
                                          for key, value in failure_results.items()},
            reproducibility={
                "method": "opportunity_clustered_cr0_two_sided_wald_holm_adjusted_family",
                "family": list(family),
                "row_order": "canonical_opportunity_id,lifecycle_identity",
                "evaluator": EVALUATOR_IDENTITY,
            },
            initial_evaluation_id=None if initial is None else initial.get("evaluation_id"),
            initial_evidence_dataset_id=None if initial is None else initial.get("evidence_dataset_id"),
        )


def production_executors(
    *, state_root: Path | str,
    evidence_directory: Path | str | None = None,
) -> tuple[ProductionValidationExecutor, ProductionValidationExecutor]:
    store = CounterfactualEvidenceStore(evidence_directory)
    queue_path = Path(state_root) / "validation_queue.json"
    observation_store_path = Path(state_root) / "candidate_observations.json"
    return (
        ProductionValidationExecutor(store, queue_path, observation_store_path, VALIDATION),
        ProductionValidationExecutor(store, queue_path, observation_store_path, FORWARD_VALIDATION),
    )


__all__ = ["EVALUATOR_IDENTITY", "ProductionValidationExecutor", "production_executors"]
