"""Governed, read-only research view of prospective shadow candidates.

This module adapts the two runtime streams into the existing Stage 4 immutable
snapshot/evidence-set discipline.  It never registers a runtime candidate,
changes a policy, or imports execution/broker code.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import statistics
from typing import Any, Iterable, Mapping, Sequence

from core.shadow.candidate_models import candidate_runtime_id
from core.shadow.candidate_monitoring import (
    _collapse_by_canonical,
    _max_drawdown,
    _profit_factor,
    _readiness,
)
from research_engine.control_plane import stage4_dataset_snapshot as D
from research_engine.control_plane import stage4_identity as I


CANDIDATE_SCHEMA = "shadow_candidate_v1"
EVALUATION_SCHEMA = "shadow_candidate_evaluation_v1"
BASELINE_SCHEMA = "shadow_runtime_v1"
GOVERNANCE_SCHEMA = "shadow_candidate_governance_v1"
OBSERVATION_REQUIREMENT = "OR-15"

NOT_OBSERVED = "NOT_OBSERVED"
INVALID = "SHADOW_VALIDATION_INVALID"


class CandidateGovernanceError(RuntimeError):
    """A frozen candidate evidence invariant was violated."""


@dataclass(frozen=True)
class DatasetAuthority:
    dataset_name: str
    schema_version: str
    producer: str
    identity_fields: tuple[str, ...]
    timestamp_fields: tuple[str, ...]
    persistence_location: str
    evidence_class: str


DATASET_AUTHORITIES: dict[str, DatasetAuthority] = {
    "shadow_candidate": DatasetAuthority(
        "shadow_candidate", CANDIDATE_SCHEMA, "shadow_candidate_runtime",
        ("shadow_trade_id", "canonical_opportunity_id", "trade_horizon",
         "candidate_runtime_id", "candidate_id", "policy_id", "treatment_hash",
         "event_id"),
        ("bar_time_utc", "watermark"), "logs/shadow_candidate_v1",
        "CANDIDATE_LIFECYCLE",
    ),
    "shadow_candidate_evaluation": DatasetAuthority(
        "shadow_candidate_evaluation", EVALUATION_SCHEMA,
        "shadow_candidate_evaluation",
        ("pair_id", "shadow_trade_id", "canonical_opportunity_id",
         "trade_horizon", "candidate_runtime_id", "candidate_id", "policy_id",
         "treatment_hash"),
        ("baseline_exit_time", "candidate_exit_time", "evaluation_time_utc"),
        "logs/shadow_candidate_evaluation_v1", "PAIRED_EVALUATION",
    ),
}


def governed_dataset_authority(schema_version: str) -> DatasetAuthority | None:
    """Return the distinct governed authority for a candidate schema."""
    return next((item for item in DATASET_AUTHORITIES.values()
                 if item.schema_version == str(schema_version)), None)


def _producer_fingerprint(producer: str) -> str:
    return hashlib.sha256(
        f"{GOVERNANCE_SCHEMA}|{producer}".encode("utf-8")
    ).hexdigest()


def _pair_id(record: Mapping[str, Any]) -> str:
    fields = (
        str(record.get("shadow_trade_id") or ""),
        str(record.get("canonical_opportunity_id") or ""),
        str(record.get("trade_horizon") or "").upper(),
        str(record.get("candidate_id") or ""),
        str(record.get("policy_id") or ""),
        str(record.get("treatment_hash") or ""),
    )
    return "pair_" + hashlib.sha256("|".join(fields).encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class SourceObject:
    """One exact persisted object and its immutable record population."""

    object_id: str
    records: tuple[dict[str, Any], ...]
    content_digest: str

    @classmethod
    def freeze(cls, object_id: str, records: Iterable[Mapping[str, Any]],
               *, content_digest: str | None = None) -> "SourceObject":
        rows = tuple(dict(row) for row in records)
        if not str(object_id or "").strip():
            raise CandidateGovernanceError("SOURCE_OBJECT_ID_MISSING")
        if not rows:
            raise CandidateGovernanceError("SOURCE_OBJECT_EMPTY:" + str(object_id))
        digest = D.record_content_digest(rows)
        if content_digest is not None and str(content_digest) != digest:
            raise CandidateGovernanceError(
                "SOURCE_OBJECT_DIGEST_MISMATCH:" + str(object_id))
        return cls(str(object_id), rows, digest)


def _objects(values: Iterable[SourceObject]) -> tuple[SourceObject, ...]:
    by_id: dict[str, SourceObject] = {}
    for item in values:
        existing = by_id.get(item.object_id)
        if existing is not None and existing.content_digest != item.content_digest:
            raise CandidateGovernanceError(
                "CONFLICTING_SOURCE_OBJECT:" + item.object_id)
        by_id[item.object_id] = item
    return tuple(by_id[key] for key in sorted(by_id))


def _rows(objects: Sequence[SourceObject]) -> tuple[dict[str, Any], ...]:
    return tuple(row for obj in objects for row in obj.records)


def _snapshot(authority: DatasetAuthority, objects: Sequence[SourceObject],
              frozen_at: str) -> D.DatasetSnapshot:
    rows = _rows(objects)
    return D.freeze_population(
        dataset_name=authority.dataset_name,
        schema_version=authority.schema_version,
        schema_generation=1,
        generation_state=D.GENERATION_CONFIRMED,
        generation_evidence="canonical production schema registry generation 1",
        identity_grain=" + ".join(authority.identity_fields),
        identity_grain_evidence=GOVERNANCE_SCHEMA,
        source_boundaries=tuple(
            f"object={obj.object_id};sha256={obj.content_digest}" for obj in objects
        ),
        population_filters=("all_records_in_exact_source_objects=true",),
        producer_version=authority.producer,
        producer_fingerprint=_producer_fingerprint(authority.producer),
        observation_requirements=(OBSERVATION_REQUIREMENT,),
        records=rows,
        frozen_at=frozen_at,
        evidence_citations=(authority.persistence_location,),
    )


@dataclass(frozen=True)
class FrozenCandidateEvidence:
    """Exact records plus their Stage 4 population identities."""

    baseline_records: tuple[dict[str, Any], ...]
    candidate_records: tuple[dict[str, Any], ...]
    evaluation_records: tuple[dict[str, Any], ...]
    snapshots: tuple[D.DatasetSnapshot, ...]
    evidence_set: I.EvidenceSet | None

    @property
    def snapshot_id(self) -> str | None:
        return (None if self.evidence_set is None
                else self.evidence_set.content_fingerprint)

    @property
    def evidence_frontier(self) -> tuple[str, ...]:
        return (() if self.evidence_set is None
                else self.evidence_set.dataset_snapshot_ids)

    def verify(self) -> D.EvidenceIdentityValidation | None:
        if self.evidence_set is None:
            return None
        registry = D.DatasetSnapshotRegistry(self.snapshots)
        return D.validate_evidence_identity(
            self.evidence_set, registry=registry, require_producer=True,
            require_uniform_schema=False, require_uniform_generation=True,
        )


def freeze_candidate_evidence(
    *,
    baseline_objects: Iterable[SourceObject] = (),
    candidate_objects: Iterable[SourceObject] = (),
    evaluation_objects: Iterable[SourceObject] = (),
    frozen_at: str = "1970-01-01T00:00:00Z",
) -> FrozenCandidateEvidence:
    """Freeze exact source objects; repeated identical objects count once."""
    baseline = _objects(baseline_objects)
    candidate = _objects(candidate_objects)
    evaluation = _objects(evaluation_objects)
    snapshots: list[D.DatasetSnapshot] = []
    if baseline:
        baseline_authority = DatasetAuthority(
            "shadow_runtime", BASELINE_SCHEMA, "shadow_runtime",
            ("shadow_trade_id", "event_id"),
            ("market_time_utc", "exit_market_time_utc_epoch_s"),
            "logs/shadow_runtime_v1", "BASELINE_LIFECYCLE",
        )
        snapshots.append(_snapshot(baseline_authority, baseline, frozen_at))
    if candidate:
        snapshots.append(_snapshot(
            DATASET_AUTHORITIES["shadow_candidate"], candidate, frozen_at))
    if evaluation:
        snapshots.append(_snapshot(
            DATASET_AUTHORITIES["shadow_candidate_evaluation"], evaluation,
            frozen_at))
    evidence_set = None
    if snapshots:
        evidence_set = I.EvidenceSet.deterministic(
            observation_requirement_ids=(OBSERVATION_REQUIREMENT,),
            members=tuple(D.snapshot_member(item) for item in snapshots),
            dataset_snapshot_ids=tuple(item.dataset_snapshot_id for item in snapshots),
        )
    result = FrozenCandidateEvidence(
        _rows(baseline), _rows(candidate), _rows(evaluation),
        tuple(snapshots), evidence_set,
    )
    verdict = result.verify()
    if verdict is not None and not verdict.valid:
        raise CandidateGovernanceError(verdict.state + ":" + verdict.reason)
    return result


@dataclass(frozen=True)
class CandidateRegistrationView:
    candidate_id: str
    policy_id: str
    treatment_hash: str
    minimum_sample_requirement: int | None = None
    readiness_criteria: Mapping[str, Any] | None = None
    activation_frontier_epoch_s: int = 0

    @classmethod
    def from_value(cls, value: Any) -> "CandidateRegistrationView":
        get = value.get if isinstance(value, Mapping) else lambda key, default=None: getattr(value, key, default)
        return cls(
            str(get("candidate_id", "") or ""),
            str(get("policy_id", "") or ""),
            str(get("treatment_hash", "") or ""),
            get("minimum_sample_requirement"),
            dict(get("readiness_criteria", {}) or {}),
            int(get("activation_frontier_epoch_s", 0) or 0),
        )


def _deduplicate(records: Sequence[dict[str, Any]], identity: str,
                 errors: list[str]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for record in records:
        key = str(record.get(identity) or "")
        if not key:
            errors.append("AMBIGUOUS_IDENTITY_MISSING:" + identity)
            continue
        previous = unique.get(key)
        if previous is not None and D.canonical_json(previous) != D.canonical_json(record):
            errors.append("DUPLICATE_CONFLICTING_RECORD:" + key)
            continue
        unique[key] = record
    return list(unique.values())


def _validate_candidate_records(records: Sequence[dict[str, Any]],
                                registration: CandidateRegistrationView,
                                errors: list[str]) -> list[dict[str, Any]]:
    relevant = [row for row in records
                if row.get("candidate_id") == registration.candidate_id
                and row.get("policy_id") == registration.policy_id
                and int(dict(row.get("state") or {}).get("entry_time")
                        or row.get("bar_time_utc") or 0)
                >= registration.activation_frontier_epoch_s]
    relevant = _deduplicate(relevant, "event_id", errors)
    seen_runtime_events: dict[tuple[str, str, int], dict[str, Any]] = {}
    for row in relevant:
        if row.get("schema_version") != CANDIDATE_SCHEMA:
            errors.append("CANDIDATE_SCHEMA_MISMATCH")
        if row.get("producer") != DATASET_AUTHORITIES["shadow_candidate"].producer:
            errors.append("CANDIDATE_PRODUCER_MISMATCH")
        required = DATASET_AUTHORITIES["shadow_candidate"].identity_fields
        if any(not str(row.get(field) or "") for field in required):
            errors.append("CANDIDATE_LIFECYCLE_IDENTITY_INCOMPLETE")
            continue
        expected = candidate_runtime_id(
            shadow_trade_id=str(row["shadow_trade_id"]),
            canonical_opportunity_id=str(row["canonical_opportunity_id"]),
            trade_horizon=str(row["trade_horizon"]),
            candidate_id=str(row["candidate_id"]), policy_id=str(row["policy_id"]),
        )
        if row.get("candidate_runtime_id") != expected:
            errors.append("CANDIDATE_RUNTIME_ID_MISMATCH")
        if row.get("treatment_hash") != registration.treatment_hash:
            errors.append("TREATMENT_HASH_MISMATCH")
        try:
            stamp = int(row.get("bar_time_utc"))
        except (TypeError, ValueError):
            errors.append("CANDIDATE_TIMESTAMP_INVALID")
            continue
        key = (str(row.get("candidate_runtime_id")),
               str(row.get("event_type")), stamp)
        prior = seen_runtime_events.get(key)
        if prior is not None and prior.get("event_id") != row.get("event_id"):
            errors.append("CANDIDATE_LIFECYCLE_IDENTITY_AMBIGUOUS")
        seen_runtime_events[key] = row
    return relevant


def _horizon(row: Mapping[str, Any]) -> str:
    identity = dict(row.get("identity") or {})
    return str(row.get("horizon") or identity.get("trade_horizon")
               or identity.get("evaluated_horizon") or "").upper()


def _validate_evaluations(
    records: Sequence[dict[str, Any]], candidate_records: Sequence[dict[str, Any]],
    baseline_records: Sequence[dict[str, Any]], registration: CandidateRegistrationView,
    errors: list[str],
) -> list[dict[str, Any]]:
    relevant = [row for row in records
                if row.get("candidate_id") == registration.candidate_id
                and row.get("policy_id") == registration.policy_id
                and int(row.get("candidate_entry_time") or 0)
                >= registration.activation_frontier_epoch_s]
    relevant = _deduplicate(relevant, "pair_id", errors)
    candidate_by_event = {str(row.get("event_id")): row for row in candidate_records
                          if row.get("event_id")}
    baseline_by_event = {str(row.get("event_id")): row for row in baseline_records
                         if row.get("event_id")}
    valid: list[dict[str, Any]] = []
    canonical_pairs: dict[tuple[str, str], str] = {}
    for row in relevant:
        if row.get("schema_version") != EVALUATION_SCHEMA:
            errors.append("EVALUATION_SCHEMA_MISMATCH")
            continue
        required = DATASET_AUTHORITIES["shadow_candidate_evaluation"].identity_fields
        if any(not str(row.get(field) or "") for field in required):
            errors.append("EVALUATION_PAIR_IDENTITY_INCOMPLETE")
            continue
        if row.get("pair_id") != _pair_id(row):
            errors.append("EVALUATION_PAIR_ID_MISMATCH")
            continue
        if row.get("treatment_hash") != registration.treatment_hash:
            errors.append("TREATMENT_HASH_MISMATCH")
            continue
        lineage = row.get("lineage")
        if not isinstance(lineage, Mapping):
            errors.append("EVALUATION_LINEAGE_MISSING")
            continue
        lineage_keys = ("baseline_event_id", "baseline_open_event_id",
                        "candidate_open_event_id", "candidate_close_event_id")
        if any(not str(lineage.get(key) or "") for key in lineage_keys):
            errors.append("EVALUATION_LINEAGE_INCOMPLETE")
            continue
        b_close = baseline_by_event.get(str(lineage["baseline_event_id"]))
        b_open = baseline_by_event.get(str(lineage["baseline_open_event_id"]))
        c_open = candidate_by_event.get(str(lineage["candidate_open_event_id"]))
        c_close = candidate_by_event.get(str(lineage["candidate_close_event_id"]))
        if any(item is None for item in (b_close, b_open, c_open, c_close)):
            errors.append("EVALUATION_LINEAGE_UNRESOLVED")
            continue
        common = (str(row["shadow_trade_id"]),
                  str(row["canonical_opportunity_id"]),
                  str(row["trade_horizon"]).upper())
        if any((str(item.get("shadow_trade_id") or ""),
                str(item.get("canonical_opportunity_id") or ""),
                (_horizon(item) if item in (b_open, b_close)
                 else str(item.get("trade_horizon") or "").upper())) != common
               for item in (b_open, b_close, c_open, c_close)):
            errors.append("EVALUATION_LINEAGE_IDENTITY_MISMATCH")
            continue
        if (c_open.get("candidate_runtime_id") != row.get("candidate_runtime_id")
                or c_close.get("candidate_runtime_id") != row.get("candidate_runtime_id")
                or c_open.get("treatment_hash") != row.get("treatment_hash")
                or c_close.get("treatment_hash") != row.get("treatment_hash")):
            errors.append("EVALUATION_CANDIDATE_LINEAGE_MISMATCH")
            continue
        key = (str(row["canonical_opportunity_id"]),
               str(row["trade_horizon"]).upper())
        previous = canonical_pairs.get(key)
        if previous is not None and previous != row["pair_id"]:
            errors.append("DUPLICATE_CONFLICTING_EVALUATION")
            continue
        canonical_pairs[key] = str(row["pair_id"])
        for field in ("baseline_r", "candidate_r", "paired_delta_r"):
            try:
                if not math.isfinite(float(row[field])):
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                errors.append("EVALUATION_OUTCOME_INVALID:" + field)
                break
        else:
            if not math.isclose(float(row["candidate_r"]) - float(row["baseline_r"]),
                                float(row["paired_delta_r"]), abs_tol=1e-12):
                errors.append("EVALUATION_DELTA_MISMATCH")
                continue
            valid.append(row)
    return valid


def _minimum_required(registration: CandidateRegistrationView) -> int | None:
    criteria = dict(registration.readiness_criteria or {})
    values = [criteria.get("minimum_paired_sample"),
              criteria.get("minimum_candidate_sample"),
              registration.minimum_sample_requirement]
    present = [int(item) for item in values if item is not None]
    return max(present) if present else None


def candidate_research_summaries(
    frozen: FrozenCandidateEvidence,
    registrations: Iterable[Any],
    *, promotion_review_prerequisites_satisfied: bool = False,
) -> list[dict[str, Any]]:
    """Build deterministic research summaries from one frozen snapshot."""
    if frozen.evidence_set is None:
        return []
    verdict = frozen.verify()
    global_errors = [] if verdict and verdict.valid else [
        "SNAPSHOT_MEMBERSHIP_UNVERIFIED"
    ]
    reports: list[dict[str, Any]] = []
    for value in registrations:
        reg = CandidateRegistrationView.from_value(value)
        errors = list(global_errors)
        if not all((reg.candidate_id, reg.policy_id, reg.treatment_hash)):
            errors.append("CANDIDATE_REGISTRATION_IDENTITY_INCOMPLETE")
        candidate_rows = _validate_candidate_records(
            frozen.candidate_records, reg, errors)
        evaluation_rows = _validate_evaluations(
            frozen.evaluation_records, candidate_rows, frozen.baseline_records,
            reg, errors)
        opens = [row for row in candidate_rows
                 if row.get("event_type") == "CANDIDATE_OPEN"]
        closes = [row for row in candidate_rows
                  if row.get("event_type") == "CANDIDATE_CLOSE"]
        observed = {str(row.get("canonical_opportunity_id")) for row in opens
                    if row.get("canonical_opportunity_id")}
        completed = {str(row.get("canonical_opportunity_id")) for row in closes
                     if row.get("canonical_opportunity_id")}
        units = _collapse_by_canonical(evaluation_rows)
        baseline = [float(row["baseline_r"]) for row in units]
        candidate = [float(row["candidate_r"]) for row in units]
        deltas = [float(row["paired_delta_r"]) for row in units]
        baseline_pf, baseline_pf_status = _profit_factor(baseline)
        candidate_pf, candidate_pf_status = _profit_factor(candidate)
        times = sorted(int(row.get("baseline_exit_time") or 0)
                       for row in units if int(row.get("baseline_exit_time") or 0) > 0)
        metrics = {
            "observed_candidate_lifecycles": len(observed),
            "active_candidate_lifecycles": len(observed - completed),
            "completed_candidate_outcomes": len(completed),
            "paired_n": len(units),
            "improved_n": sum(row["outcome_classification"] == "IMPROVED" for row in units),
            "worsened_n": sum(row["outcome_classification"] == "WORSENED" for row in units),
            "unchanged_n": sum(row["outcome_classification"] == "UNCHANGED" for row in units),
            "baseline_total_r": sum(baseline),
            "candidate_total_r": sum(candidate),
            "baseline_expectancy_r": statistics.fmean(baseline) if baseline else None,
            "candidate_expectancy_r": statistics.fmean(candidate) if candidate else None,
            "mean_paired_delta_r": statistics.fmean(deltas) if deltas else None,
            "median_paired_delta_r": statistics.median(deltas) if deltas else None,
            "baseline_win_rate": (sum(item > 0 for item in baseline) / len(baseline)
                                  if baseline else None),
            "candidate_win_rate": (sum(item > 0 for item in candidate) / len(candidate)
                                   if candidate else None),
            "baseline_profit_factor": baseline_pf,
            "candidate_profit_factor": candidate_pf,
            "baseline_profit_factor_status": baseline_pf_status,
            "candidate_profit_factor_status": candidate_pf_status,
            "baseline_max_drawdown_r": _max_drawdown(baseline),
            "candidate_max_drawdown_r": _max_drawdown(candidate),
            "observation_window_seconds": (times[-1] - times[0]
                                           if len(times) > 1 else (0 if times else None)),
            "integrity_violations": len(set(errors)),
        }
        if not candidate_rows and not errors:
            readiness = {"state": NOT_OBSERVED, "satisfied": False,
                         "unsatisfied_criteria": ["CANDIDATE_EVIDENCE_UNAVAILABLE"]}
        elif errors:
            readiness = {"state": INVALID, "satisfied": False,
                         "unsatisfied_criteria": sorted(set(errors))}
        else:
            readiness = _readiness(
                criteria=dict(reg.readiness_criteria or {}), metrics=metrics,
                integrity_violations=0,
                minimum_sample_requirement=reg.minimum_sample_requirement,
                promotion_review_prerequisites_satisfied=(
                    promotion_review_prerequisites_satisfied),
            )
        last_time_values = [int(row.get("bar_time_utc") or 0)
                            for row in candidate_rows]
        last_time_values.extend(int(row.get("candidate_exit_time") or 0)
                                for row in evaluation_rows)
        reports.append({
            "schema_version": GOVERNANCE_SCHEMA,
            "candidate_id": reg.candidate_id,
            "policy_id": reg.policy_id,
            "treatment_hash": reg.treatment_hash,
            "status": readiness["state"],
            "observed_n": metrics["observed_candidate_lifecycles"],
            "paired_n": metrics["paired_n"],
            "minimum_required_n": _minimum_required(reg),
            "baseline_expectancy": metrics["baseline_expectancy_r"],
            "candidate_expectancy": metrics["candidate_expectancy_r"],
            "mean_paired_delta_r": metrics["mean_paired_delta_r"],
            "baseline_pf": metrics["baseline_profit_factor"],
            "candidate_pf": metrics["candidate_profit_factor"],
            "baseline_max_dd_r": metrics["baseline_max_drawdown_r"],
            "candidate_max_dd_r": metrics["candidate_max_drawdown_r"],
            "improved_n": metrics["improved_n"],
            "worsened_n": metrics["worsened_n"],
            "unchanged_n": metrics["unchanged_n"],
            "integrity_status": "VERIFIED" if not errors else "INVALID",
            "integrity_reasons": sorted(set(errors)),
            "snapshot_id": frozen.snapshot_id,
            "evidence_frontier": list(frozen.evidence_frontier),
            "last_evidence_time": max(last_time_values, default=None),
            "readiness": readiness,
            "metrics": metrics,
            "review_eligible": readiness["state"] == "READY_FOR_PROMOTION_REVIEW",
            "live_approved": False,
        })
    return reports


def candidate_status_citation(summary: Mapping[str, Any]) -> dict[str, Any]:
    """Generic question/finding citation for governed candidate evidence."""
    return {
        "evidence_class": "SHADOW_CANDIDATE_PROSPECTIVE",
        "candidate_id": summary.get("candidate_id"),
        "policy_id": summary.get("policy_id"),
        "treatment_hash": summary.get("treatment_hash"),
        "status": summary.get("status"),
        "snapshot_id": summary.get("snapshot_id"),
        "evidence_frontier": list(summary.get("evidence_frontier") or ()),
        "observed_n": summary.get("observed_n"),
        "paired_n": summary.get("paired_n"),
        "mean_paired_delta_r": summary.get("mean_paired_delta_r"),
        "integrity_status": summary.get("integrity_status"),
    }


def rolling_candidate_frontier(previous: Mapping[str, Any] | None,
                               current: Mapping[str, Any]) -> dict[str, Any]:
    """Compare two frozen read models without using wall-clock state."""
    old = dict(previous or {})
    old_frontier = tuple(old.get("evidence_frontier") or ())
    new_frontier = tuple(current.get("evidence_frontier") or ())
    return {
        "candidate_id": current.get("candidate_id"),
        "new_candidate_evidence": new_frontier != old_frontier,
        "observed_added": int(current.get("observed_n") or 0) - int(old.get("observed_n") or 0),
        "paired_added": int(current.get("paired_n") or 0) - int(old.get("paired_n") or 0),
        "current_paired_performance": current.get("mean_paired_delta_r"),
        "readiness_changed": old.get("status") != current.get("status"),
        "previous_status": old.get("status"),
        "current_status": current.get("status"),
        "evidence_frontier": list(new_frontier),
    }


def optimisation_registry_shadow_view(summary: Mapping[str, Any]) -> dict[str, Any]:
    """Read-only bridge; applying the returned transition remains explicit."""
    return {
        "candidate_id": summary.get("candidate_id"),
        "candidate_status": summary.get("status"),
        "policy_id": summary.get("policy_id"),
        "treatment_hash": summary.get("treatment_hash"),
        "evidence_frontier": list(summary.get("evidence_frontier") or ()),
        "evidence_snapshot_id": summary.get("snapshot_id"),
        "integrity_status": summary.get("integrity_status"),
        "review_eligible": bool(summary.get("review_eligible")),
        "live_approved": False,
    }


__all__ = [
    "BASELINE_SCHEMA", "CANDIDATE_SCHEMA", "DATASET_AUTHORITIES",
    "EVALUATION_SCHEMA", "GOVERNANCE_SCHEMA", "CandidateGovernanceError",
    "CandidateRegistrationView", "DatasetAuthority", "FrozenCandidateEvidence",
    "SourceObject", "candidate_research_summaries", "candidate_status_citation",
    "freeze_candidate_evidence", "governed_dataset_authority",
    "optimisation_registry_shadow_view", "rolling_candidate_frontier",
]
