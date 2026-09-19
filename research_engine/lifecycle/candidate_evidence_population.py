"""Wave 6.2C -- baseline-transition evidence population safety."""
from __future__ import annotations
import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable
from research_engine.lifecycle.candidate_evidence_continuity import (
    CandidateEvidenceContinuityState, EvidenceContinuityState,)
from research_engine.lifecycle.candidate_evidence_eligibility import (
    EvidenceEligibilityStatus, HistoricalObservation,)
from research_engine.lifecycle.candidate_impact_history import (
    CandidateBaselineImpactRecord,)
__all__ = ["PopulationSource", "FreshTargetObservation",
    "HistoricalPopulationMember", "FreshPopulationMember",
    "CandidateEvidencePopulation", "PopulationBindingError",
    "PopulationConflictError", "UnknownHistoricalObservationError",
    "compute_population_id", "build_candidate_evidence_population"]

class PopulationBindingError(ValueError):
    """Context or member provenance cannot be proven exactly."""

class PopulationConflictError(ValueError):
    """Conflicting duplicate identity/provenance."""

class UnknownHistoricalObservationError(PopulationBindingError):
    """Historical observation absent from / unbindable to the snapshot."""

class PopulationSource(str, Enum):
    HISTORICAL_CONTINUITY = "HISTORICAL_CONTINUITY"
    FRESH_TARGET_BASELINE = "FRESH_TARGET_BASELINE"

@dataclass(frozen=True)
class FreshTargetObservation:
    """ONE observation genuinely generated against Baseline N+1. Propagates
    already-existing canonical fields verbatim; invents nothing."""
    candidate_id: str
    source_baseline_id: str
    correlation_id: str | None = None
    source_config_hash: str | None = None
    treatment_id: str | None = None
    symbol: str | None = None
    def to_dict(self) -> dict[str, Any]:
        return {"candidate_id": self.candidate_id,
            "source_baseline_id": self.source_baseline_id,
            "source_config_hash": self.source_config_hash,
            "treatment_id": self.treatment_id,
            "correlation_id": self.correlation_id, "symbol": self.symbol}

@dataclass(frozen=True)
class HistoricalPopulationMember:
    """One admitted historical N observation. ALWAYS retains source N."""
    correlation_id: str
    candidate_id: str
    source_baseline_id: str
    source_config_hash: str | None
    treatment_id: str | None
    population_source: PopulationSource = PopulationSource.HISTORICAL_CONTINUITY
    def to_dict(self) -> dict[str, Any]:
        return {"correlation_id": self.correlation_id,
            "candidate_id": self.candidate_id,
            "source_baseline_id": self.source_baseline_id,
            "source_config_hash": self.source_config_hash,
            "treatment_id": self.treatment_id,
            "population_source": self.population_source.value}

@dataclass(frozen=True)
class FreshPopulationMember:
    """One admitted fresh N+1 observation. ALWAYS retains source N+1."""
    correlation_id: str
    candidate_id: str
    source_baseline_id: str
    source_config_hash: str | None
    treatment_id: str | None
    population_source: PopulationSource = PopulationSource.FRESH_TARGET_BASELINE
    def to_dict(self) -> dict[str, Any]:
        return {"correlation_id": self.correlation_id,
            "candidate_id": self.candidate_id,
            "source_baseline_id": self.source_baseline_id,
            "source_config_hash": self.source_config_hash,
            "treatment_id": self.treatment_id,
            "population_source": self.population_source.value}

_POP_FIELDS = ("candidate_id", "target_baseline_id",
    "target_baseline_config_hash", "impact_id", "continuity_id",
    "candidate_treatment_id", "historical_members", "fresh_members")

def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)

def _digest(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()

def _non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())

def compute_population_id(**identity: Any) -> str:
    """Deterministic identity: canonical JSON + sha256. Binds candidate +
    target baseline/config + impact + continuity + treatment + EXACT admitted
    member identities + source provenance. Order-independent. No clocks."""
    missing = [f for f in _POP_FIELDS if f not in identity]
    if missing:
        raise ValueError(f"Population identity missing {missing}")
    extra = [k for k in identity if k not in _POP_FIELDS]
    if extra:
        raise ValueError(f"Population identity unexpected {extra}")
    for field in _POP_FIELDS[:6]:
        if not _non_empty_str(identity[field]):
            raise ValueError(f"Population identity {field!r} non-empty")
    for key in ("historical_members", "fresh_members"):
        members = identity[key]
        if not isinstance(members, (list, tuple)):
            raise ValueError(f"Population identity {key} must be sequence")
        for entry in members:
            if (not isinstance(entry, dict) or set(entry) != {
                    "correlation_id", "source_baseline_id",
                    "source_config_hash", "treatment_id",
                    "population_source"}):
                raise ValueError(f"Malformed member: {entry!r}")
            if not _non_empty_str(entry["correlation_id"]):
                raise PopulationBindingError("Member lacks identity")
            if not _non_empty_str(entry["source_baseline_id"]):
                raise PopulationBindingError("Member lacks source baseline")
    return "ECP-" + _digest({
        f: (sorted(identity[f], key=lambda m: m["correlation_id"])
            if f in ("historical_members", "fresh_members") else identity[f])
        for f in _POP_FIELDS})

@dataclass(frozen=True)
class CandidateEvidencePopulation:
    """Canonical combined VIEW for Candidate X under Baseline N+1. Two
    separate populations; every member retains source population + source
    baseline. Descriptive flags only; NO evaluation, NO revalidation claim."""
    population_id: str
    candidate_id: str
    target_baseline_id: str
    target_baseline_config_hash: str
    impact_id: str
    continuity_id: str
    candidate_treatment_id: str
    historical_members: tuple = ()
    fresh_members: tuple = ()
    historical_count: int = 0
    fresh_count: int = 0
    total_count: int = 0
    historical_continuity_used: bool = False
    blocked_historical_continuity: bool = False
    fresh_evidence_required: bool = False
    fresh_evidence_present: bool = False
    def to_dict(self) -> dict[str, Any]:
        return {"population_id": self.population_id,
            "candidate_id": self.candidate_id,
            "target_baseline_id": self.target_baseline_id,
            "target_baseline_config_hash": self.target_baseline_config_hash,
            "impact_id": self.impact_id, "continuity_id": self.continuity_id,
            "candidate_treatment_id": self.candidate_treatment_id,
            "historical_members": [m.to_dict() for m in self.historical_members],
            "fresh_members": [m.to_dict() for m in self.fresh_members],
            "historical_count": self.historical_count,
            "fresh_count": self.fresh_count, "total_count": self.total_count,
            "historical_continuity_used": self.historical_continuity_used,
            "blocked_historical_continuity": self.blocked_historical_continuity,
            "fresh_evidence_required": self.fresh_evidence_required,
            "fresh_evidence_present": self.fresh_evidence_present}
    def canonical_json(self) -> str:
        return _canonical(self.to_dict())

def _require_context(impact_record, continuity) -> None:
    if not isinstance(impact_record, CandidateBaselineImpactRecord):
        raise PopulationBindingError("Not a 6.1B impact record")
    if not isinstance(continuity, CandidateEvidenceContinuityState):
        raise PopulationBindingError("Not a 6.2B continuity snapshot")
    for label, value in (("impact candidate", impact_record.candidate_id),
            ("impact_id", impact_record.impact_id),
            ("continuity_id", continuity.continuity_id),
            ("continuity impact", continuity.impact_id),
            ("continuity candidate", continuity.candidate_id),
            ("historical baseline", continuity.historical_baseline_id),
            ("historical config", continuity.historical_baseline_config_hash),
            ("target baseline", continuity.target_baseline_id),
            ("target config", continuity.target_baseline_config_hash),
            ("treatment", continuity.candidate_treatment_id)):
        if not _non_empty_str(value):
            raise PopulationBindingError(f"Context lacks {label}")
    if continuity.impact_id != impact_record.impact_id:
        raise PopulationBindingError("Continuity impact mismatch")
    if (continuity.candidate_id != impact_record.candidate_id
            or continuity.historical_baseline_id
            != impact_record.candidate_baseline_id
            or continuity.historical_baseline_config_hash
            != impact_record.candidate_baseline_config_hash
            or continuity.target_baseline_id != impact_record.to_baseline_id
            or continuity.target_baseline_config_hash
            != impact_record.to_baseline_config_hash
            or continuity.application_id != impact_record.application_id
            or continuity.candidate_treatment_id
            != impact_record.candidate_treatment_id):
        raise PopulationBindingError("Snapshot does not bind impact chain")

def _snapshot_index(continuity) -> dict:
    index: dict = {}
    for summary in continuity.observation_states:
        if not _non_empty_str(summary.correlation_id):
            raise PopulationBindingError("Snapshot entry lacks identity")
        existing = index.get(summary.correlation_id)
        if existing is not None:
            if (existing.status != summary.status
                    or tuple(existing.reason_codes)
                    != tuple(summary.reason_codes)):
                raise PopulationConflictError(
                    f"Snapshot conflict for {summary.correlation_id!r}")
            continue
        index[summary.correlation_id] = summary
    return index

def _check_provenance(*, correlation_id, candidate_id, source_baseline_id,
        source_config_hash, treatment_id, expected_candidate,
        expected_baseline, expected_config, expected_treatment,
        kind) -> str:
    if not _non_empty_str(correlation_id):
        raise PopulationBindingError(f"{kind} lacks correlation_id")
    if candidate_id != expected_candidate:
        raise PopulationBindingError(
            f"{kind} {correlation_id!r} candidate {candidate_id!r} "
            f"!= {expected_candidate!r}")
    if source_baseline_id != expected_baseline:
        raise PopulationBindingError(
            f"{kind} {correlation_id!r} baseline {source_baseline_id!r} "
            f"!= {expected_baseline!r}")
    if source_config_hash is not None and source_config_hash != expected_config:
        raise PopulationBindingError(
            f"{kind} {correlation_id!r} config mismatch")
    if (treatment_id is not None and _non_empty_str(expected_treatment)
            and treatment_id != expected_treatment):
        raise PopulationBindingError(
            f"{kind} {correlation_id!r} treatment mismatch")
    return str(correlation_id)

def _admit_historical(observations, *, continuity, impact_record):
    index = _snapshot_index(continuity)
    blocked = continuity.state in (
        EvidenceContinuityState.BLOCKED_INDETERMINATE,
        EvidenceContinuityState.NO_HISTORICAL_EVIDENCE)
    supplied = list(observations or ())
    if blocked:
        for obs in supplied:
            if not isinstance(obs, HistoricalObservation):
                raise PopulationBindingError("Historical type only")
            if (not _non_empty_str(getattr(obs, "correlation_id", None))
                    or obs.correlation_id not in index):
                raise UnknownHistoricalObservationError(
                    f"Historical {getattr(obs, 'correlation_id', None)!r} "
                    f"absent from snapshot {continuity.continuity_id!r}")
        return ()
    admitted: dict = {}
    seen: dict = {}
    for obs in supplied:
        if not isinstance(obs, HistoricalObservation):
            raise PopulationBindingError("Historical type only")
        cid = _check_provenance(correlation_id=obs.correlation_id,
            candidate_id=obs.candidate_id, source_baseline_id=obs.baseline_id,
            source_config_hash=obs.config_hash, treatment_id=obs.treatment_id,
            expected_candidate=continuity.candidate_id,
            expected_baseline=continuity.historical_baseline_id,
            expected_config=continuity.historical_baseline_config_hash,
            expected_treatment=continuity.candidate_treatment_id,
            kind="Historical")
        summary = index.get(cid)
        if summary is None:
            raise UnknownHistoricalObservationError(
                f"Historical {cid!r} absent from {continuity.continuity_id!r}")
        if summary.status != EvidenceEligibilityStatus.DIRECTLY_ELIGIBLE.value:
            continue
        if cid not in continuity.direct_contribution_correlation_ids:
            raise PopulationBindingError(
                f"Historical {cid!r} not permitted direct contribution")
        key = (obs.candidate_id, obs.baseline_id, obs.config_hash,
               obs.symbol, obs.pattern, obs.treatment_id)
        if cid in admitted:
            if seen[cid] != key:
                raise PopulationConflictError(
                    f"Conflicting provenance for historical {cid!r}")
            continue
        seen[cid] = key
        admitted[cid] = HistoricalPopulationMember(
            correlation_id=cid, candidate_id=str(obs.candidate_id),
            source_baseline_id=str(obs.baseline_id),
            source_config_hash=obs.config_hash,
            treatment_id=obs.treatment_id)
    return tuple(sorted(admitted.values(), key=lambda m: m.correlation_id))

def _admit_fresh(observations, *, continuity):
    admitted: dict = {}
    seen: dict = {}
    for obs in (observations or ()):
        if not isinstance(obs, FreshTargetObservation):
            raise PopulationBindingError("Fresh type only")
        cid = _check_provenance(correlation_id=obs.correlation_id,
            candidate_id=obs.candidate_id,
            source_baseline_id=obs.source_baseline_id,
            source_config_hash=obs.source_config_hash,
            treatment_id=obs.treatment_id,
            expected_candidate=continuity.candidate_id,
            expected_baseline=continuity.target_baseline_id,
            expected_config=continuity.target_baseline_config_hash,
            expected_treatment=continuity.candidate_treatment_id,
            kind="Fresh")
        key = (obs.candidate_id, obs.source_baseline_id,
               obs.source_config_hash, obs.treatment_id)
        if cid in admitted:
            if seen[cid] != key:
                raise PopulationConflictError(
                    f"Conflicting provenance for fresh {cid!r}")
            continue
        seen[cid] = key
        admitted[cid] = FreshPopulationMember(
            correlation_id=cid, candidate_id=str(obs.candidate_id),
            source_baseline_id=str(obs.source_baseline_id),
            source_config_hash=obs.source_config_hash,
            treatment_id=obs.treatment_id)
    return tuple(sorted(admitted.values(), key=lambda m: m.correlation_id))

def build_candidate_evidence_population(*, impact_record, continuity,
        historical_observations=None, fresh_observations=None):
    """Pure VIEW constructor. Validates provenance, never rewrites it."""
    _require_context(impact_record, continuity)
    historical = _admit_historical(historical_observations or (),
        continuity=continuity, impact_record=impact_record)
    fresh = _admit_fresh(fresh_observations or (), continuity=continuity)
    hist_ids = {m.correlation_id for m in historical}
    fresh_ids = {m.correlation_id for m in fresh}
    clash = hist_ids & fresh_ids
    if clash:
        raise PopulationConflictError(
            f"Observation {sorted(clash)[0]!r} claims N and N+1")
    for member in historical:
        if member.source_baseline_id != continuity.historical_baseline_id:
            raise PopulationBindingError("Historical lost source N")
        if member.population_source is not PopulationSource.HISTORICAL_CONTINUITY:
            raise PopulationBindingError("Historical lost source tag")
    for member in fresh:
        if member.source_baseline_id != continuity.target_baseline_id:
            raise PopulationBindingError("Fresh lost source N+1")
        if member.population_source is not PopulationSource.FRESH_TARGET_BASELINE:
            raise PopulationBindingError("Fresh lost source tag")
    permitted = set(continuity.direct_contribution_correlation_ids)
    if not hist_ids <= permitted:
        raise PopulationBindingError("Historical exceeds permitted subset")
    blocked = continuity.state is EvidenceContinuityState.BLOCKED_INDETERMINATE
    population_id = compute_population_id(
        candidate_id=continuity.candidate_id,
        target_baseline_id=continuity.target_baseline_id,
        target_baseline_config_hash=continuity.target_baseline_config_hash,
        impact_id=continuity.impact_id, continuity_id=continuity.continuity_id,
        candidate_treatment_id=continuity.candidate_treatment_id,
        historical_members=[{"correlation_id": m.correlation_id,
            "source_baseline_id": m.source_baseline_id,
            "source_config_hash": m.source_config_hash,
            "treatment_id": m.treatment_id,
            "population_source": m.population_source.value}
            for m in historical],
        fresh_members=[{"correlation_id": m.correlation_id,
            "source_baseline_id": m.source_baseline_id,
            "source_config_hash": m.source_config_hash,
            "treatment_id": m.treatment_id,
            "population_source": m.population_source.value}
            for m in fresh])
    return CandidateEvidencePopulation(
        population_id=population_id, candidate_id=continuity.candidate_id,
        target_baseline_id=continuity.target_baseline_id,
        target_baseline_config_hash=continuity.target_baseline_config_hash,
        impact_id=continuity.impact_id,
        continuity_id=continuity.continuity_id,
        candidate_treatment_id=continuity.candidate_treatment_id,
        historical_members=historical, fresh_members=fresh,
        historical_count=len(historical), fresh_count=len(fresh),
        total_count=len(historical) + len(fresh),
        historical_continuity_used=bool(historical),
        blocked_historical_continuity=blocked,
        fresh_evidence_required=continuity.fresh_evidence_required,
        fresh_evidence_present=bool(fresh))
