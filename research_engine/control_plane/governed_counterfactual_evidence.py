"""Governed counterfactual exit-policy evidence (candidate-capability activation).

The governed HD09 exit-policy research authority
(``research_engine.experiments.exit_policy_governed``) can already name a real
governed intervention and therefore a real candidate.  It cannot run inside the
snapshot-only generated-question worker, because the frozen ``exit_bar_path_v1``
authority requires the ordered ``events_v1`` M5 OHLC candle stream, which the
common investigation snapshot deliberately does not bind, and the worker is
forbidden from reopening storage.

This module is the *upstream* governed evidence-production path that closes that
gap without weakening either guarantee:

    raw governed source (snapshot ``shadow_runtime`` + governed M5 candles)
        -> deterministic evidence producer (this module)
        -> governed counterfactual dataset artifact (schema validated)
        -> immutable, content-addressed frozen artifact
        -> snapshot-pinned governed evidence membership
        -> Q71 production evaluator (frozen artifact only)

Authority rules enforced here:

* Nothing is invented.  Every row is the governed HD09 authority's own
  analytical record, read from the governed exit-bar-path, baseline-reproduction
  and candidate-replay populations.  No field the underlying evidence cannot
  support is computed.
* The artifact is *frozen*: write-once, content-addressed, digest-verified on
  read, and pinned to the exact InvestigationSnapshot it was produced from.
* Invalid or incomplete replay rows fail closed.  They are recorded as explicit
  exclusions with a closed reason code and never enter the admissible population.
* A dataset with no admissible rows is still a legitimate artifact: it declares
  ``scientifically_analysable = False`` with reason codes, so a consumer fails
  closed instead of analysing an empty population.
* The producer is the only component that may read the governed raw source.  A
  consumer (the Q71 worker and its evaluator) only ever receives the frozen
  artifact.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from research_engine.control_plane.governed_exit_evidence import (
    GovernedExitEvidence,
    _M5_CANDLE_MISSING,
    build_governed_exit_evidence,
)
from research_engine.control_plane.exit_candidate_replay import (
    BASELINE_REPRODUCTION_REQUIRED,
    CANDIDATE_POLICY_IDS,
)
from research_engine.control_plane.evidence_provenance import evidence_digest
from research_engine.registry.exit_policy_adjudication import (
    BASELINE_POLICY_ID,
    CANDIDATE_POLICIES_V1,
    HD09_ADJUDICATION_VERSION,
)


GOVERNED_COUNTERFACTUAL_EVIDENCE_SCHEMA = (
    "governed_counterfactual_exit_policy_evidence_v1")
COUNTERFACTUAL_EVIDENCE_ID_PREFIX = "CFE-"
COUNTERFACTUAL_BINDING_SCHEMA = "governed_counterfactual_evidence_binding_v1"

#: Producer identity/version.  Recorded on every artifact so a consumer can prove
#: which code produced the evidence it is about to analyse.
COUNTERFACTUAL_PRODUCER_IDENTITY = (
    "research_engine.control_plane.governed_counterfactual_evidence")
COUNTERFACTUAL_PRODUCER_VERSION = (
    "governed_counterfactual_evidence_producer_v1")

#: The governed evidence class this artifact carries.  This is the canonical
#: authority's own scientific-signal / population token, not a new invention.
COUNTERFACTUAL_EVIDENCE_CLASS = "GOVERNED_EXIT_POLICY_COUNTERFACTUAL"

#: The governed counterfactual replay method identity and version.
REPLAY_METHOD_IDENTITY = "hd09_governed_exit_counterfactual_replay"
REPLAY_METHOD_VERSION = HD09_ADJUDICATION_VERSION

#: The frozen M5 candle authority the exit-bar-path contract admits.
M5_AUTHORITY = "events_v1:CANDLE:mt5_data:M5"

#: The only governed policy catalogue an intervention may be named from.
GOVERNED_POLICY_CATALOGUE = (
    "research_engine.registry.exit_policy_adjudication.CANDIDATE_POLICIES_V1")

#: The frozen data sources the artifact is derived from.
SOURCE_DATASET_SHADOW_RUNTIME = "shadow_runtime"
SOURCE_DATASET_M5_CANDLES = "events"

TREATMENT_COMPONENT_BY_POLICY_TYPE = {
    "TRAILING": "STOP_GEOMETRY",
    "REDUCED_TP": "TARGET_GEOMETRY",
    "TIME_CAP": "TIMING",
}

# ── Closed fail-closed reason-code vocabulary ────────────────────────────────
MISSING_COUNTERFACTUAL_EVIDENCE = "MISSING_COUNTERFACTUAL_EVIDENCE"
INVALID_COUNTERFACTUAL_SCHEMA = "INVALID_COUNTERFACTUAL_SCHEMA"
INCOMPLETE_REPLAY = "INCOMPLETE_REPLAY"
LEAKAGE_GUARD_FAILED = "LEAKAGE_GUARD_FAILED"
MISSING_BASELINE = "MISSING_BASELINE"
MISSING_TREATMENT_POLICY = "MISSING_TREATMENT_POLICY"
UNKNOWN_GOVERNED_POLICY = "UNKNOWN_GOVERNED_POLICY"
TREATMENT_SIGNATURE_MISMATCH = "TREATMENT_SIGNATURE_MISMATCH"
INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
SCIENTIFICALLY_NOT_MEANINGFUL = "SCIENTIFICALLY_NOT_MEANINGFUL"
NO_INTERVENTION_MAPPING = "NO_INTERVENTION_MAPPING"
VALIDATION_CRITERIA_INCOMPLETE = "VALIDATION_CRITERIA_INCOMPLETE"
STALE_FRONTIER = "STALE_FRONTIER"
SUPERSEDED_EVIDENCE = "SUPERSEDED_EVIDENCE"
DUPLICATE_CANDIDATE = "DUPLICATE_CANDIDATE"
UPSTREAM_INVALIDATED = "UPSTREAM_INVALIDATED"
MISSING_M5_CANDLE_AUTHORITY = "MISSING_M5_CANDLE_AUTHORITY"
MISSING_SHADOW_LIFECYCLE_POPULATION = "MISSING_SHADOW_LIFECYCLE_POPULATION"

REASON_CODES = frozenset({
    MISSING_COUNTERFACTUAL_EVIDENCE, INVALID_COUNTERFACTUAL_SCHEMA,
    INCOMPLETE_REPLAY, LEAKAGE_GUARD_FAILED, MISSING_BASELINE,
    MISSING_TREATMENT_POLICY, UNKNOWN_GOVERNED_POLICY,
    TREATMENT_SIGNATURE_MISMATCH, INSUFFICIENT_SAMPLE,
    SCIENTIFICALLY_NOT_MEANINGFUL, NO_INTERVENTION_MAPPING,
    VALIDATION_CRITERIA_INCOMPLETE, STALE_FRONTIER, SUPERSEDED_EVIDENCE,
    DUPLICATE_CANDIDATE, UPSTREAM_INVALIDATED, MISSING_M5_CANDLE_AUTHORITY,
    MISSING_SHADOW_LIFECYCLE_POPULATION,
})

VALIDITY_ELIGIBLE = "ELIGIBLE"
VALIDITY_EXCLUDED = "EXCLUDED"


class CounterfactualEvidenceError(RuntimeError):
    """The governed counterfactual evidence is invalid; no analysis may proceed."""


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=False, allow_nan=False).encode("utf-8")
    ).hexdigest()


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise CounterfactualEvidenceError(
            INVALID_COUNTERFACTUAL_SCHEMA + ":" + name)
    return value


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CounterfactualEvidenceError(
            INVALID_COUNTERFACTUAL_SCHEMA + ":" + name)
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise CounterfactualEvidenceError(
            INVALID_COUNTERFACTUAL_SCHEMA + ":" + name)
    return number


def treatment_signature(policy: Mapping[str, Any]) -> str:
    """Deterministic governed treatment signature of one catalogue policy."""
    policy_id = str(policy.get("policy_id") or "")
    frozen = next(
        (item for item in CANDIDATE_POLICIES_V1 if item["policy_id"] == policy_id),
        None)
    if frozen is None:
        raise CounterfactualEvidenceError(
            UNKNOWN_GOVERNED_POLICY + ":" + policy_id)
    if dict(policy) != dict(frozen):
        raise CounterfactualEvidenceError(
            TREATMENT_SIGNATURE_MISMATCH + ":" + policy_id)
    return _digest({
        "catalogue": GOVERNED_POLICY_CATALOGUE,
        "policy": dict(frozen),
    })


def treatment_component(policy: Mapping[str, Any]) -> str:
    component = TREATMENT_COMPONENT_BY_POLICY_TYPE.get(
        str(policy.get("policy_type") or ""))
    if component is None:
        raise CounterfactualEvidenceError(
            UNKNOWN_GOVERNED_POLICY + ":" + str(policy.get("policy_id") or ""))
    return component


@dataclass(frozen=True)
class CounterfactualEvidenceRow:
    """One governed counterfactual outcome: lifecycle x governed policy.

    Every field is read from the governed HD09 authority's own analytical
    records.  A row that cannot be fully populated is never constructed; the
    producer emits an explicit exclusion instead.
    """

    lifecycle_identity: tuple[str, str, str]
    canonical_opportunity_id: str
    canonical_symbol: str
    trade_horizon: str
    timeframe: str
    entry_state: Mapping[str, Any]
    baseline_policy_id: str
    baseline_exit: Mapping[str, Any]
    baseline_r: float
    governed_policy_id: str
    treatment_component: str
    treatment_parameters: Mapping[str, Any]
    treatment_signature: str
    counterfactual_exit: Mapping[str, Any]
    counterfactual_r: float
    delta_r: float
    required_bar_availability: Mapping[str, Any]
    leakage_guard: Mapping[str, Any]
    replay_method: str
    replay_version: str
    source_evidence_lineage: Mapping[str, Any]
    validity: str = VALIDITY_ELIGIBLE
    exclusion_reason: str | None = None
    row_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if self.validity not in {VALIDITY_ELIGIBLE, VALIDITY_EXCLUDED}:
            raise CounterfactualEvidenceError(
                INVALID_COUNTERFACTUAL_SCHEMA + ":validity")
        if len(self.lifecycle_identity) != 3:
            raise CounterfactualEvidenceError(
                INVALID_COUNTERFACTUAL_SCHEMA + ":lifecycle_identity")
        object.__setattr__(self, "row_digest", _digest(self.material()))

    def material(self) -> dict[str, Any]:
        return {
            "lifecycle_identity": list(self.lifecycle_identity),
            "canonical_opportunity_id": self.canonical_opportunity_id,
            "canonical_symbol": self.canonical_symbol,
            "trade_horizon": self.trade_horizon,
            "timeframe": self.timeframe,
            "entry_state": dict(self.entry_state),
            "baseline_policy_id": self.baseline_policy_id,
            "baseline_exit": dict(self.baseline_exit),
            "baseline_r": self.baseline_r,
            "governed_policy_id": self.governed_policy_id,
            "treatment_component": self.treatment_component,
            "treatment_parameters": dict(self.treatment_parameters),
            "treatment_signature": self.treatment_signature,
            "counterfactual_exit": dict(self.counterfactual_exit),
            "counterfactual_r": self.counterfactual_r,
            "delta_r": self.delta_r,
            "required_bar_availability": dict(self.required_bar_availability),
            "leakage_guard": dict(self.leakage_guard),
            "replay_method": self.replay_method,
            "replay_version": self.replay_version,
            "source_evidence_lineage": dict(self.source_evidence_lineage),
            "validity": self.validity,
            "exclusion_reason": self.exclusion_reason,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.material(), "row_digest": self.row_digest}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CounterfactualEvidenceRow":
        if not isinstance(value, Mapping):
            raise CounterfactualEvidenceError(
                INVALID_COUNTERFACTUAL_SCHEMA + ":row")
        row = cls(
            lifecycle_identity=tuple(value.get("lifecycle_identity") or ()),
            canonical_opportunity_id=_text(
                value.get("canonical_opportunity_id"), "canonical_opportunity_id"),
            canonical_symbol=_text(
                value.get("canonical_symbol"), "canonical_symbol"),
            trade_horizon=_text(value.get("trade_horizon"), "trade_horizon"),
            timeframe=_text(value.get("timeframe"), "timeframe"),
            entry_state=dict(value.get("entry_state") or {}),
            baseline_policy_id=_text(
                value.get("baseline_policy_id"), "baseline_policy_id"),
            baseline_exit=dict(value.get("baseline_exit") or {}),
            baseline_r=_finite(value.get("baseline_r"), "baseline_r"),
            governed_policy_id=_text(
                value.get("governed_policy_id"), "governed_policy_id"),
            treatment_component=_text(
                value.get("treatment_component"), "treatment_component"),
            treatment_parameters=dict(value.get("treatment_parameters") or {}),
            treatment_signature=_text(
                value.get("treatment_signature"), "treatment_signature"),
            counterfactual_exit=dict(value.get("counterfactual_exit") or {}),
            counterfactual_r=_finite(
                value.get("counterfactual_r"), "counterfactual_r"),
            delta_r=_finite(value.get("delta_r"), "delta_r"),
            required_bar_availability=dict(
                value.get("required_bar_availability") or {}),
            leakage_guard=dict(value.get("leakage_guard") or {}),
            replay_method=_text(value.get("replay_method"), "replay_method"),
            replay_version=_text(value.get("replay_version"), "replay_version"),
            source_evidence_lineage=dict(value.get("source_evidence_lineage") or {}),
            validity=str(value.get("validity") or VALIDITY_ELIGIBLE),
            exclusion_reason=value.get("exclusion_reason"),
        )
        supplied = str(value.get("row_digest") or "")
        if supplied and supplied != row.row_digest:
            raise CounterfactualEvidenceError(
                INVALID_COUNTERFACTUAL_SCHEMA + ":row_digest")
        return row


@dataclass(frozen=True)
class CounterfactualEvidenceExclusion:
    """One fail-closed grain failure: lifecycle x governed policy, stable reason."""

    lifecycle_identity: tuple[str, str, str]
    canonical_symbol: str
    trade_horizon: str
    governed_policy_id: str | None
    reason: str
    detail: str = ""
    eligibility_state: str = VALIDITY_EXCLUDED

    def __post_init__(self) -> None:
        if self.reason not in REASON_CODES:
            raise CounterfactualEvidenceError(
                INVALID_COUNTERFACTUAL_SCHEMA + ":exclusion_reason")
        if self.eligibility_state != VALIDITY_EXCLUDED:
            raise CounterfactualEvidenceError(
                INVALID_COUNTERFACTUAL_SCHEMA + ":exclusion_state")

    def to_dict(self) -> dict[str, Any]:
        return {
            "lifecycle_identity": list(self.lifecycle_identity),
            "canonical_symbol": self.canonical_symbol,
            "trade_horizon": self.trade_horizon,
            "governed_policy_id": self.governed_policy_id,
            "reason": self.reason,
            "detail": self.detail,
            "eligibility_state": self.eligibility_state,
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any],
    ) -> "CounterfactualEvidenceExclusion":
        if not isinstance(value, Mapping):
            raise CounterfactualEvidenceError(
                INVALID_COUNTERFACTUAL_SCHEMA + ":exclusion")
        return cls(
            lifecycle_identity=tuple(value.get("lifecycle_identity") or ()),
            canonical_symbol=str(value.get("canonical_symbol") or ""),
            trade_horizon=str(value.get("trade_horizon") or ""),
            governed_policy_id=value.get("governed_policy_id"),
            reason=str(value.get("reason") or ""),
            detail=str(value.get("detail") or ""),
            eligibility_state=str(
                value.get("eligibility_state") or VALIDITY_EXCLUDED),
        )


@dataclass(frozen=True)
class GovernedCounterfactualEvidence:
    """The frozen, content-addressed governed counterfactual evidence artifact.

    This is the only thing a Q71 consumer ever receives.  It carries the exact
    governed M5 candle authority the common investigation snapshot does not bind,
    the governed counterfactual outcome rows computed from it, the fail-closed
    exclusions, and the full provenance required to prove where every value came
    from.
    """

    schema: str
    dataset_id: str
    produced_at: str
    producer_identity: str
    producer_version: str
    evidence_class: str
    baseline_policy_id: str
    governed_policy_catalogue: str
    governed_policy_ids: tuple[str, ...]
    population_identity: str
    replay_method: str
    replay_version: str
    m5_authority: str
    snapshot_id: str
    snapshot_fingerprint: str
    investigation_epoch: str
    frontier_start: str
    frontier_end: str
    source_dataset_identities: tuple[tuple[str, str], ...]
    source_shadow_runtime_digest: str
    source_candle_digest: str
    #: The governed M5 candle authority this artifact was produced from.  Empty
    #: only when no governed authority was admitted; the producer then records
    #: MISSING_M5_CANDLE_AUTHORITY and refuses to analyse.
    candle_authority_id: str
    candle_authority_digest: str
    candle_rows: tuple[Mapping[str, Any], ...]
    rows: tuple[CounterfactualEvidenceRow, ...]
    exclusions: tuple[CounterfactualEvidenceExclusion, ...]
    reason_codes: tuple[str, ...]
    scientifically_analysable: bool
    completed_lifecycles: int
    eligible_lifecycles: int
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema != GOVERNED_COUNTERFACTUAL_EVIDENCE_SCHEMA:
            raise CounterfactualEvidenceError(
                INVALID_COUNTERFACTUAL_SCHEMA + ":schema")
        if not self.dataset_id.startswith(COUNTERFACTUAL_EVIDENCE_ID_PREFIX):
            raise CounterfactualEvidenceError(
                INVALID_COUNTERFACTUAL_SCHEMA + ":dataset_id")
        if not self.evidence_class:
            raise CounterfactualEvidenceError(
                INVALID_COUNTERFACTUAL_SCHEMA + ":evidence_class")
        for code in self.reason_codes:
            if code not in REASON_CODES:
                raise CounterfactualEvidenceError(
                    INVALID_COUNTERFACTUAL_SCHEMA + ":reason_code:" + str(code))
        if self.scientifically_analysable and not self.rows:
            raise CounterfactualEvidenceError(
                MISSING_COUNTERFACTUAL_EVIDENCE + ":analysable_without_rows")
        if bool(self.rows) != bool(self.scientifically_analysable):
            raise CounterfactualEvidenceError(
                INVALID_COUNTERFACTUAL_SCHEMA + ":analysability_contradiction")
        object.__setattr__(self, "content_digest",
                           _digest(self.identity_material()))

    def identity_material(self) -> dict[str, Any]:
        """Everything that defines the artifact's evidence identity.

        ``produced_at`` is provenance, not evidence: it is recorded on the
        artifact but is deliberately excluded here, so the same governed
        evidence always yields the same dataset identity and content digest.
        """
        return {
            "schema": self.schema,
            "dataset_id": self.dataset_id,
            "producer_identity": self.producer_identity,
            "producer_version": self.producer_version,
            "evidence_class": self.evidence_class,
            "baseline_policy_id": self.baseline_policy_id,
            "governed_policy_catalogue": self.governed_policy_catalogue,
            "governed_policy_ids": list(self.governed_policy_ids),
            "population_identity": self.population_identity,
            "replay_method": self.replay_method,
            "replay_version": self.replay_version,
            "m5_authority": self.m5_authority,
            "snapshot_id": self.snapshot_id,
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "investigation_epoch": self.investigation_epoch,
            "frontier_start": self.frontier_start,
            "frontier_end": self.frontier_end,
            "source_dataset_identities": [
                list(item) for item in self.source_dataset_identities],
            "source_shadow_runtime_digest": self.source_shadow_runtime_digest,
            "source_candle_digest": self.source_candle_digest,
            "candle_authority_id": self.candle_authority_id,
            "candle_authority_digest": self.candle_authority_digest,
            "candle_rows": [dict(row) for row in self.candle_rows],
            "rows": [row.to_dict() for row in self.rows],
            "exclusions": [item.to_dict() for item in self.exclusions],
            "reason_codes": list(self.reason_codes),
            "scientifically_analysable": self.scientifically_analysable,
            "completed_lifecycles": self.completed_lifecycles,
            "eligible_lifecycles": self.eligible_lifecycles,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.identity_material(),
            "produced_at": self.produced_at,
            "content_digest": self.content_digest,
        }

    @property
    def treatment_policy_ids(self) -> tuple[str, ...]:
        return tuple(sorted({row.governed_policy_id for row in self.rows}))

    def rows_for_policy(self, policy_id: str) -> tuple[CounterfactualEvidenceRow, ...]:
        return tuple(
            row for row in self.rows if row.governed_policy_id == policy_id)


@dataclass(frozen=True)
class GovernedCounterfactualBinding:
    """Snapshot-pinned admission of one frozen artifact into a worker's boundary.

    The common investigation snapshot's bound-dataset set is closed by contract
    and is not widened here: widening it would invalidate every already-frozen
    snapshot and weaken a frozen guarantee.  Instead the admission is an explicit
    governed membership that *pins* the artifact to the exact InvestigationSnapshot
    identity it was produced from.  A consumer must verify this binding against
    its own snapshot before the evaluator may see the artifact, so the worker can
    only ever consume governed evidence admitted for the snapshot it is analysing.
    """

    schema: str
    evidence_class: str
    dataset_id: str
    content_digest: str
    snapshot_id: str
    snapshot_fingerprint: str
    investigation_epoch: str
    frontier_start: str
    frontier_end: str
    governed_policy_catalogue: str
    governed_policy_ids: tuple[str, ...]
    producer_identity: str
    producer_version: str
    bound_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "evidence_class": self.evidence_class,
            "dataset_id": self.dataset_id,
            "content_digest": self.content_digest,
            "snapshot_id": self.snapshot_id,
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "investigation_epoch": self.investigation_epoch,
            "frontier_start": self.frontier_start,
            "frontier_end": self.frontier_end,
            "governed_policy_catalogue": self.governed_policy_catalogue,
            "governed_policy_ids": list(self.governed_policy_ids),
            "producer_identity": self.producer_identity,
            "producer_version": self.producer_version,
            "bound_at": self.bound_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "GovernedCounterfactualBinding":
        if not isinstance(value, Mapping):
            raise CounterfactualEvidenceError(
                INVALID_COUNTERFACTUAL_SCHEMA + ":binding")
        binding = cls(
            schema=str(value.get("schema") or ""),
            evidence_class=str(value.get("evidence_class") or ""),
            dataset_id=str(value.get("dataset_id") or ""),
            content_digest=str(value.get("content_digest") or ""),
            snapshot_id=str(value.get("snapshot_id") or ""),
            snapshot_fingerprint=str(value.get("snapshot_fingerprint") or ""),
            investigation_epoch=str(value.get("investigation_epoch") or ""),
            frontier_start=str(value.get("frontier_start") or ""),
            frontier_end=str(value.get("frontier_end") or ""),
            governed_policy_catalogue=str(
                value.get("governed_policy_catalogue") or ""),
            governed_policy_ids=tuple(
                str(item) for item in (value.get("governed_policy_ids") or ())),
            producer_identity=str(value.get("producer_identity") or ""),
            producer_version=str(value.get("producer_version") or ""),
            bound_at=str(value.get("bound_at") or ""),
        )
        if binding.schema != COUNTERFACTUAL_BINDING_SCHEMA:
            raise CounterfactualEvidenceError(
                INVALID_COUNTERFACTUAL_SCHEMA + ":binding_schema")
        if binding.governed_policy_catalogue != GOVERNED_POLICY_CATALOGUE:
            raise CounterfactualEvidenceError(
                UNKNOWN_GOVERNED_POLICY + ":" + binding.governed_policy_catalogue)
        return binding


def bind_counterfactual_evidence(
    evidence: GovernedCounterfactualEvidence, *, bound_at: str,
) -> GovernedCounterfactualBinding:
    """Admit one frozen artifact to its own snapshot's Q71 boundary."""
    if not isinstance(evidence, GovernedCounterfactualEvidence):
        raise CounterfactualEvidenceError(
            INVALID_COUNTERFACTUAL_SCHEMA + ":binding_requires_artifact")
    return GovernedCounterfactualBinding(
        schema=COUNTERFACTUAL_BINDING_SCHEMA,
        evidence_class=evidence.evidence_class,
        dataset_id=evidence.dataset_id,
        content_digest=evidence.content_digest,
        snapshot_id=evidence.snapshot_id,
        snapshot_fingerprint=evidence.snapshot_fingerprint,
        investigation_epoch=evidence.investigation_epoch,
        frontier_start=evidence.frontier_start,
        frontier_end=evidence.frontier_end,
        governed_policy_catalogue=evidence.governed_policy_catalogue,
        governed_policy_ids=tuple(evidence.governed_policy_ids),
        producer_identity=evidence.producer_identity,
        producer_version=evidence.producer_version,
        bound_at=str(bound_at),
    )


def verify_governed_counterfactual_binding(
    binding: Any,
    *,
    snapshot_id: str,
    snapshot_fingerprint: str,
    investigation_epoch: str,
    evidence: GovernedCounterfactualEvidence | None = None,
) -> GovernedCounterfactualBinding:
    """Prove a binding belongs to *this* snapshot; otherwise fail closed.

    Nothing is repaired or defaulted.  A binding from a different frontier, a
    different epoch or a different artifact fails closed with a closed reason
    code so the caller can report exactly why no governed evidence was admitted.
    """
    if binding is None:
        raise CounterfactualEvidenceError(MISSING_COUNTERFACTUAL_EVIDENCE)
    if isinstance(binding, Mapping):
        binding = GovernedCounterfactualBinding.from_dict(binding)
    if not isinstance(binding, GovernedCounterfactualBinding):
        raise CounterfactualEvidenceError(
            INVALID_COUNTERFACTUAL_SCHEMA + ":binding_type")
    if binding.snapshot_id != str(snapshot_id):
        raise CounterfactualEvidenceError(
            STALE_FRONTIER + ":" + binding.snapshot_id + "!=" + str(snapshot_id))
    if binding.snapshot_fingerprint != str(snapshot_fingerprint):
        raise CounterfactualEvidenceError(
            SUPERSEDED_EVIDENCE + ":snapshot_fingerprint")
    if binding.investigation_epoch != str(investigation_epoch):
        raise CounterfactualEvidenceError(
            SUPERSEDED_EVIDENCE + ":investigation_epoch")
    if evidence is not None:
        if (evidence.dataset_id != binding.dataset_id
                or evidence.content_digest != binding.content_digest):
            raise CounterfactualEvidenceError(
                SUPERSEDED_EVIDENCE + ":artifact_identity")
    return binding


# ── Deterministic producer ───────────────────────────────────────────────────
def _rows_of(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        return []
    return [dict(row) for row in value if isinstance(row, Mapping)]


def _m5_guard(record: Any) -> tuple[dict[str, Any], str | None]:
    """Re-assert the governed leakage/availability guard on one path record.

    The governed ``exit_bar_path_v1`` authority already refuses a path whose bars
    are unordered, duplicated or not exit-aligned.  This re-asserts the property
    on the frozen evidence so the artifact can never carry a row whose
    counterfactual outcome was decided using information from the entry bar or
    before it.
    """
    bars = tuple(record.ordered_m5_bars)
    stamps = [int(bar.timestamp_utc_ms) for bar in bars]
    entry_ms = int(record.entry_utc_epoch_s) * 1000
    exit_ms = int(record.exit_utc_epoch_s) * 1000
    expected = max(0, (exit_ms // 300000) - (entry_ms // 300000))
    guard = {
        "entry_bar_evaluated": False,
        "post_entry_only": all(stamp > entry_ms for stamp in stamps),
        "strictly_ascending": all(
            stamps[index] < stamps[index + 1] for index in range(len(stamps) - 1)),
        "exit_aligned": bool(stamps) and stamps[-1] == exit_ms,
        "rule": (
            "the entry bar is never evaluated and replay begins with the first "
            "completed M5 bar strictly after entry (HD09 BASELINE_POLICY_V1)"),
    }
    availability = {
        "expected_m5_bars": expected,
        "supplied_m5_bars": len(stamps),
        "first_bar_utc_ms": stamps[0] if stamps else None,
        "last_bar_utc_ms": stamps[-1] if stamps else None,
        "timeframe": "M5",
        "authority": M5_AUTHORITY,
    }
    if not all((
        guard["post_entry_only"], guard["strictly_ascending"],
        guard["exit_aligned"],
    )):
        return {**availability, **guard}, LEAKAGE_GUARD_FAILED
    return {**availability, **guard}, None


def _candidate_exclusion_reason(reason: str) -> str:
    """Map a governed candidate-replay exclusion onto this closed vocabulary."""
    if reason == BASELINE_REPRODUCTION_REQUIRED:
        return MISSING_BASELINE
    return INCOMPLETE_REPLAY


def _evidence_row(
    record: Any,
    path_record: Any,
    reproduction_record: Any,
) -> CounterfactualEvidenceRow:
    """Build one row from the governed authority's own analytical records."""
    policy = next(
        item for item in CANDIDATE_POLICIES_V1
        if item["policy_id"] == record.candidate_policy_id)
    availability, failure = _m5_guard(path_record)
    if failure is not None:
        raise CounterfactualEvidenceError(failure)
    baseline_r = float(reproduction_record.replay.pnl_r_multiple)
    counterfactual_r = float(record.candidate_r)
    return CounterfactualEvidenceRow(
        lifecycle_identity=tuple(record.lifecycle_identity),
        canonical_opportunity_id=record.canonical_opportunity_id,
        canonical_symbol=record.canonical_symbol,
        trade_horizon=record.trade_horizon,
        timeframe="M5",
        entry_state={
            "direction": path_record.direction,
            "entry_price": float(path_record.entry_price),
            "baseline_stop_loss": float(path_record.baseline_stop_loss),
            "baseline_take_profit": float(path_record.baseline_take_profit),
            "baseline_timeout_bars": int(path_record.baseline_timeout_bars),
            "entry_utc_epoch_s": int(path_record.entry_utc_epoch_s),
        },
        baseline_policy_id=BASELINE_POLICY_ID,
        baseline_exit={
            "exit_reason": reproduction_record.replay.exit_reason,
            "exit_price": float(reproduction_record.replay.exit_price),
            "exit_utc_epoch_s": int(reproduction_record.replay.exit_utc_epoch_s),
            "bars_held": int(reproduction_record.replay.bars_held),
        },
        baseline_r=baseline_r,
        governed_policy_id=record.candidate_policy_id,
        treatment_component=treatment_component(policy),
        treatment_parameters=dict(policy),
        treatment_signature=treatment_signature(policy),
        counterfactual_exit={
            "exit_reason": record.exit_reason,
            "exit_price": float(record.exit_price),
            "exit_utc_epoch_s": int(record.exit_utc_epoch_s),
            "bars_held": int(record.bars_held),
        },
        counterfactual_r=counterfactual_r,
        delta_r=counterfactual_r - baseline_r,
        required_bar_availability=availability,
        leakage_guard={
            "entry_bar_evaluated": availability["entry_bar_evaluated"],
            "post_entry_only": availability["post_entry_only"],
            "strictly_ascending": availability["strictly_ascending"],
            "exit_aligned": availability["exit_aligned"],
            "rule": availability["rule"],
        },
        replay_method=REPLAY_METHOD_IDENTITY,
        replay_version=REPLAY_METHOD_VERSION,
        source_evidence_lineage={
            "hd09_adjudication_version": HD09_ADJUDICATION_VERSION,
            "path_analytical_digest": path_record.analytical_digest,
            "baseline_reproduction_digest": reproduction_record.reproduction_digest,
            "candidate_replay_digest": record.candidate_replay_digest,
            "candidate_policy_identity": dict(record.candidate_policy),
        },
    )

def _admit_candle_authority(
    authority: Any, binding: Any, *, snapshot_id: str,
    snapshot_fingerprint: str, investigation_epoch: str,
    frontier_start: str, frontier_end: str,
) -> tuple[Any, list[dict[str, Any]], str]:
    """Admit the frozen governed M5 candle authority to THIS frontier only.

    The governed candle authority is produced upstream by
    ``governed_m5_candle_authority``.  Nothing here reads storage, repairs a
    mismatch or falls back to another candle source: an authority from another
    frontier, another epoch, another window or with a different digest fails
    closed with a closed reason code.
    """
    if authority is None and binding is None:
        return None, [], ""
    if authority is None or binding is None:
        raise CounterfactualEvidenceError(
            MISSING_M5_CANDLE_AUTHORITY
            + ":a governed candle authority requires its snapshot-pinned "
            "membership, and vice versa")
    from research_engine.control_plane.governed_m5_candle_authority import (
        M5_CANDLE_AUTHORITY_IDENTITY, CandleAuthorityError,
        validate_governed_m5_candle_authority,
        verify_m5_candle_authority_binding,
    )
    try:
        artifact = validate_governed_m5_candle_authority(authority)
        if artifact.authority_identity != M5_CANDLE_AUTHORITY_IDENTITY:
            raise CounterfactualEvidenceError(
                MISSING_M5_CANDLE_AUTHORITY + ":identity")
        if (str(artifact.frontier_start) != str(frontier_start)
                or str(artifact.frontier_end) != str(frontier_end)):
            raise CounterfactualEvidenceError(
                STALE_FRONTIER + ":candle_window")
        verify_m5_candle_authority_binding(
            binding, snapshot_id=snapshot_id,
            snapshot_fingerprint=snapshot_fingerprint,
            investigation_epoch=investigation_epoch, authority=artifact)
    except CandleAuthorityError as exc:
        raise CounterfactualEvidenceError(str(exc)) from exc
    return artifact, _rows_of(artifact.candle_rows), artifact.content_digest




def build_governed_counterfactual_evidence(
    *,
    shadow_runtime_rows: Sequence[Mapping[str, Any]],
    candle_rows: Sequence[Mapping[str, Any]] = (),
    candle_authority: Any = None,
    candle_authority_binding: Any = None,
    snapshot_id: str,
    snapshot_fingerprint: str,
    investigation_epoch: str,
    frontier_start: str,
    frontier_end: str,
    produced_at: str,
    source_dataset_identities: Mapping[str, str] | Sequence[Sequence[str]] = (),
) -> GovernedCounterfactualEvidence:
    """Produce the frozen governed counterfactual exit-policy evidence.

    The producer is the only component permitted to touch the governed raw
    source.  It runs the governed HD09 builders over the supplied in-memory
    governed rows, reads only their own analytical records, and freezes the
    result.  Every row it cannot fully support becomes an explicit exclusion.

    ``candle_authority`` is the frozen governed M5 candle authority
    (``research_engine.control_plane.governed_m5_candle_authority``) admitted to
    exactly this snapshot by ``candle_authority_binding``.  When it is supplied
    its frozen rows are the *only* candle evidence used: the raw ``candle_rows``
    argument is ignored, the membership is verified against this snapshot's own
    identity, and the authority's own content digest becomes the artifact's
    ``source_candle_digest``.  When it is absent the artifact records
    ``MISSING_M5_CANDLE_AUTHORITY`` and is not scientifically analysable.
    """
    shadow = _rows_of(shadow_runtime_rows)
    authority, authority_rows, authority_digest = _admit_candle_authority(
        candle_authority, candle_authority_binding,
        snapshot_id=str(snapshot_id),
        snapshot_fingerprint=str(snapshot_fingerprint),
        investigation_epoch=str(investigation_epoch),
        frontier_start=str(frontier_start), frontier_end=str(frontier_end))
    candles = authority_rows if authority is not None else _rows_of(candle_rows)
    if isinstance(source_dataset_identities, Mapping):
        identities = tuple(sorted(
            (str(key), str(value))
            for key, value in source_dataset_identities.items()))
    else:
        identities = tuple(sorted(
            (str(item[0]), str(item[1]))
            for item in source_dataset_identities
            if len(tuple(item)) == 2))

    evidence = build_governed_exit_evidence(
        shadow, candles, with_dimensions=False)
    reasons: set[str] = set()
    if not shadow:
        reasons.add(MISSING_SHADOW_LIFECYCLE_POPULATION)
    if evidence.missing_evidence:
        # The governed M5 candle authority is absent.  That is a real governed
        # observation gap, reported explicitly, never smoothed over.
        reasons.add(MISSING_M5_CANDLE_AUTHORITY)
        reasons.add(MISSING_COUNTERFACTUAL_EVIDENCE)

    path_index = {item.lifecycle_identity: item for item in evidence.path.records}
    reproduction_index = {
        item.lifecycle_identity: item for item in evidence.reproduction.records}

    rows: list[CounterfactualEvidenceRow] = []
    exclusions: list[CounterfactualEvidenceExclusion] = []
    for record in evidence.candidate.records:
        identity = tuple(record.lifecycle_identity)
        path_record = path_index.get(identity)
        reproduction_record = reproduction_index.get(identity)
        if path_record is None or reproduction_record is None:
            exclusions.append(CounterfactualEvidenceExclusion(
                lifecycle_identity=identity,
                canonical_symbol=record.canonical_symbol,
                trade_horizon=record.trade_horizon,
                governed_policy_id=record.candidate_policy_id,
                reason=(MISSING_BASELINE if reproduction_record is None
                        else INCOMPLETE_REPLAY),
                detail="governed path or baseline reproduction record absent",
            ))
            continue
        try:
            rows.append(_evidence_row(record, path_record, reproduction_record))
        except CounterfactualEvidenceError as exc:
            exclusions.append(CounterfactualEvidenceExclusion(
                lifecycle_identity=identity,
                canonical_symbol=record.canonical_symbol,
                trade_horizon=record.trade_horizon,
                governed_policy_id=record.candidate_policy_id,
                reason=str(exc).split(":", 1)[0],
                detail=str(exc),
            ))

    for item in evidence.path.exclusions:
        exclusions.append(CounterfactualEvidenceExclusion(
            lifecycle_identity=tuple(item.lifecycle_identity or ("", "", "")),
            canonical_symbol="",
            trade_horizon="",
            governed_policy_id=None,
            reason=INCOMPLETE_REPLAY,
            detail="exit_bar_path_v1:" + str(item.reason),
        ))
    for item in evidence.reproduction.exclusions:
        exclusions.append(CounterfactualEvidenceExclusion(
            lifecycle_identity=tuple(item.lifecycle_identity),
            canonical_symbol=item.canonical_symbol,
            trade_horizon=item.trade_horizon,
            governed_policy_id=None,
            reason=MISSING_BASELINE,
            detail="shadow_baseline_reproduction_v1:" + str(item.reason),
        ))
    for item in evidence.candidate.exclusions:
        exclusions.append(CounterfactualEvidenceExclusion(
            lifecycle_identity=tuple(item.lifecycle_identity),
            canonical_symbol=item.canonical_symbol,
            trade_horizon=item.trade_horizon,
            governed_policy_id=item.candidate_policy_id,
            reason=_candidate_exclusion_reason(str(item.reason)),
            detail="exit_candidate_replay_v1:" + str(item.reason),
        ))

    order = {policy_id: index
             for index, policy_id in enumerate(CANDIDATE_POLICY_IDS)}
    rows.sort(key=lambda row: (
        tuple(row.lifecycle_identity), order.get(row.governed_policy_id, -1)))
    exclusions.sort(key=lambda item: (
        tuple(item.lifecycle_identity), str(item.governed_policy_id or ""),
        item.reason, item.detail))

    if not rows:
        reasons.add(MISSING_COUNTERFACTUAL_EVIDENCE)
    analysable = bool(rows)

    shadow_digest = evidence_digest(shadow)
    candle_digest = authority_digest or evidence_digest(candles)
    candle_authority_id = (
        "" if authority is None else str(authority.authority_id))
    dataset_material = {
        "schema": GOVERNED_COUNTERFACTUAL_EVIDENCE_SCHEMA,
        "producer_identity": COUNTERFACTUAL_PRODUCER_IDENTITY,
        "producer_version": COUNTERFACTUAL_PRODUCER_VERSION,
        "evidence_class": COUNTERFACTUAL_EVIDENCE_CLASS,
        "snapshot_id": str(snapshot_id),
        "snapshot_fingerprint": str(snapshot_fingerprint),
        "investigation_epoch": str(investigation_epoch),
        "source_shadow_runtime_digest": shadow_digest,
        "source_candle_digest": candle_digest,
        "candle_authority_id": candle_authority_id,
        "rows": [row.to_dict() for row in rows],
        "exclusions": [item.to_dict() for item in exclusions],
    }
    return GovernedCounterfactualEvidence(
        schema=GOVERNED_COUNTERFACTUAL_EVIDENCE_SCHEMA,
        dataset_id=(COUNTERFACTUAL_EVIDENCE_ID_PREFIX
                    + _digest(dataset_material)[:16].upper()),
        produced_at=str(produced_at),
        producer_identity=COUNTERFACTUAL_PRODUCER_IDENTITY,
        producer_version=COUNTERFACTUAL_PRODUCER_VERSION,
        evidence_class=COUNTERFACTUAL_EVIDENCE_CLASS,
        baseline_policy_id=BASELINE_POLICY_ID,
        governed_policy_catalogue=GOVERNED_POLICY_CATALOGUE,
        governed_policy_ids=tuple(str(item["policy_id"])
                                  for item in CANDIDATE_POLICIES_V1),
        population_identity=COUNTERFACTUAL_EVIDENCE_CLASS,
        replay_method=REPLAY_METHOD_IDENTITY,
        replay_version=REPLAY_METHOD_VERSION,
        m5_authority=M5_AUTHORITY,
        snapshot_id=str(snapshot_id),
        snapshot_fingerprint=str(snapshot_fingerprint),
        investigation_epoch=str(investigation_epoch),
        frontier_start=str(frontier_start),
        frontier_end=str(frontier_end),
        source_dataset_identities=identities,
        source_shadow_runtime_digest=shadow_digest,
        source_candle_digest=candle_digest,
        candle_authority_id=candle_authority_id,
        candle_authority_digest=(
            "" if authority is None else str(authority.content_digest)),
        candle_rows=tuple(dict(row) for row in candles),
        rows=tuple(rows),
        exclusions=tuple(exclusions),
        reason_codes=tuple(sorted(reasons)),
        scientifically_analysable=analysable,
        completed_lifecycles=int(evidence.completed_lifecycles),
        eligible_lifecycles=int(evidence.eligible_path_lifecycles),
    )


def validate_governed_counterfactual_evidence(
    value: Any,
) -> GovernedCounterfactualEvidence:
    """Strictly parse a frozen artifact; malformed input fails closed."""
    if isinstance(value, GovernedCounterfactualEvidence):
        return value
    if not isinstance(value, Mapping):
        raise CounterfactualEvidenceError(
            INVALID_COUNTERFACTUAL_SCHEMA + ":artifact")
    rows = tuple(
        CounterfactualEvidenceRow.from_dict(item)
        for item in (value.get("rows") or ()))
    exclusions = tuple(
        CounterfactualEvidenceExclusion.from_dict(item)
        for item in (value.get("exclusions") or ()))
    artifact = GovernedCounterfactualEvidence(
        schema=str(value.get("schema") or ""),
        dataset_id=str(value.get("dataset_id") or ""),
        produced_at=str(value.get("produced_at") or ""),
        producer_identity=str(value.get("producer_identity") or ""),
        producer_version=str(value.get("producer_version") or ""),
        evidence_class=str(value.get("evidence_class") or ""),
        baseline_policy_id=str(value.get("baseline_policy_id") or ""),
        governed_policy_catalogue=str(
            value.get("governed_policy_catalogue") or ""),
        governed_policy_ids=tuple(
            str(item) for item in (value.get("governed_policy_ids") or ())),
        population_identity=str(value.get("population_identity") or ""),
        replay_method=str(value.get("replay_method") or ""),
        replay_version=str(value.get("replay_version") or ""),
        m5_authority=str(value.get("m5_authority") or ""),
        snapshot_id=str(value.get("snapshot_id") or ""),
        snapshot_fingerprint=str(value.get("snapshot_fingerprint") or ""),
        investigation_epoch=str(value.get("investigation_epoch") or ""),
        frontier_start=str(value.get("frontier_start") or ""),
        frontier_end=str(value.get("frontier_end") or ""),
        source_dataset_identities=tuple(
            (str(item[0]), str(item[1]))
            for item in (value.get("source_dataset_identities") or ())),
        source_shadow_runtime_digest=str(
            value.get("source_shadow_runtime_digest") or ""),
        source_candle_digest=str(value.get("source_candle_digest") or ""),
        candle_authority_id=str(value.get("candle_authority_id") or ""),
        candle_authority_digest=str(
            value.get("candle_authority_digest") or ""),
        candle_rows=tuple(
            dict(item) for item in (value.get("candle_rows") or ())
            if isinstance(item, Mapping)),
        rows=rows,
        exclusions=exclusions,
        reason_codes=tuple(
            str(item) for item in (value.get("reason_codes") or ())),
        scientifically_analysable=bool(
            value.get("scientifically_analysable")),
        completed_lifecycles=int(value.get("completed_lifecycles") or 0),
        eligible_lifecycles=int(value.get("eligible_lifecycles") or 0),
    )
    supplied = str(value.get("content_digest") or "")
    if supplied and supplied != artifact.content_digest:
        raise CounterfactualEvidenceError(
            INVALID_COUNTERFACTUAL_SCHEMA + ":content_digest")
    if artifact.governed_policy_catalogue != GOVERNED_POLICY_CATALOGUE:
        raise CounterfactualEvidenceError(
            UNKNOWN_GOVERNED_POLICY + ":" + artifact.governed_policy_catalogue)
    return artifact


def rebuild_governed_exit_evidence(
    evidence: GovernedCounterfactualEvidence,
    shadow_runtime_rows: Sequence[Mapping[str, Any]],
) -> GovernedExitEvidence:
    """Rebuild the governed HD09 foundations from the frozen artifact.

    The artifact carries the governed M5 candle authority the common snapshot
    does not bind; the snapshot supplies its own ``shadow_runtime`` population.
    The artifact is only usable when it was produced from exactly that
    population, which is proven here before anything is rebuilt.
    """
    artifact = validate_governed_counterfactual_evidence(evidence)
    shadow = _rows_of(shadow_runtime_rows)
    if evidence_digest(shadow) != artifact.source_shadow_runtime_digest:
        raise CounterfactualEvidenceError(
            STALE_FRONTIER + ":source_shadow_runtime_population")
    return build_governed_exit_evidence(
        shadow, [dict(row) for row in artifact.candle_rows],
        with_dimensions=False)


def verify_counterfactual_rows(
    evidence: GovernedCounterfactualEvidence,
    rebuilt: GovernedExitEvidence,
) -> None:
    """Prove the frozen rows are exactly the rebuilt governed population.

    Determinism plus integrity: every frozen row must name a governed policy in
    the governed catalogue with a deterministic treatment signature, and its
    counterfactual/baseline lineage digests must reproduce the governed
    authority's own records.  Any disagreement fails closed.
    """
    artifact = validate_governed_counterfactual_evidence(evidence)
    index = {
        (tuple(row.lifecycle_identity), row.governed_policy_id): row
        for row in artifact.rows
    }
    if len(index) != len(artifact.rows):
        raise CounterfactualEvidenceError(INCOMPLETE_REPLAY + ":row_grain")
    for row in artifact.rows:
        if row.validity != VALIDITY_ELIGIBLE:
            raise CounterfactualEvidenceError(INCOMPLETE_REPLAY + ":inadmissible_row")
        if treatment_signature(dict(row.treatment_parameters)) != row.treatment_signature:
            raise CounterfactualEvidenceError(
                TREATMENT_SIGNATURE_MISMATCH + ":" + row.governed_policy_id)
    baseline = {
        item.lifecycle_identity: item for item in rebuilt.reproduction.records}
    for record in rebuilt.candidate.records:
        key = (tuple(record.lifecycle_identity), record.candidate_policy_id)
        row = index.get(key)
        if row is None:
            raise CounterfactualEvidenceError(
                INCOMPLETE_REPLAY + ":missing_frozen_row")
        if (row.source_evidence_lineage.get("candidate_replay_digest")
                != record.candidate_replay_digest):
            raise CounterfactualEvidenceError(
                INCOMPLETE_REPLAY + ":candidate_lineage")
        reproduction_record = baseline.get(tuple(record.lifecycle_identity))
        if reproduction_record is None:
            raise CounterfactualEvidenceError(MISSING_BASELINE + ":frozen_row")
        if float(row.baseline_r) != float(
                reproduction_record.replay.pnl_r_multiple):
            raise CounterfactualEvidenceError(MISSING_BASELINE + ":baseline_r")
        if float(row.counterfactual_r) != float(record.candidate_r):
            raise CounterfactualEvidenceError(
                INCOMPLETE_REPLAY + ":counterfactual_r")


# ── Immutable governed evidence store ───────────────────────────────────────
DEFAULT_COUNTERFACTUAL_EVIDENCE_DIRECTORY = Path(
    "analysis/assurance/counterfactual_evidence")
_LATEST_POINTER = "latest.json"


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _immutable_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write-once persistence: a repeated identical write is a no-op."""
    path = Path(path)
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CounterfactualEvidenceError(
                INVALID_COUNTERFACTUAL_SCHEMA + ":unreadable:" + str(path)) from exc
        if existing != json.loads(json.dumps(payload)):
            raise CounterfactualEvidenceError(
                INVALID_COUNTERFACTUAL_SCHEMA
                + ":identity_collision:" + str(path))
        return
    _atomic_json(path, payload)


class CounterfactualEvidenceStore:
    """Immutable, content-addressed store of frozen counterfactual evidence."""

    def __init__(self, directory: Path | str | None = None) -> None:
        self.directory = Path(
            directory if directory is not None
            else DEFAULT_COUNTERFACTUAL_EVIDENCE_DIRECTORY)

    def path_for(self, dataset_id: str) -> Path:
        return self.directory / (str(dataset_id) + ".json")

    def binding_path_for(self, dataset_id: str) -> Path:
        return self.directory / (str(dataset_id) + ".binding.json")

    def register(
        self, evidence: GovernedCounterfactualEvidence, *, bound_at: str = "",
    ) -> GovernedCounterfactualEvidence:
        """Freeze one artifact and its snapshot-pinned binding, write-once."""
        artifact = validate_governed_counterfactual_evidence(evidence)
        binding = bind_counterfactual_evidence(
            artifact, bound_at=bound_at or artifact.produced_at)
        _immutable_json(self.path_for(artifact.dataset_id), artifact.to_dict())
        _immutable_json(self.binding_path_for(artifact.dataset_id), binding.to_dict())
        _atomic_json(self.directory / _LATEST_POINTER, {
            "dataset_id": artifact.dataset_id,
            "content_digest": artifact.content_digest,
            "snapshot_id": artifact.snapshot_id,
            "snapshot_fingerprint": artifact.snapshot_fingerprint,
            "investigation_epoch": artifact.investigation_epoch,
            "produced_at": artifact.produced_at,
        })
        return artifact

    def dataset_ids(self) -> tuple[str, ...]:
        if not self.directory.is_dir():
            return ()
        return tuple(sorted(
            path.stem for path in self.directory.glob(
                COUNTERFACTUAL_EVIDENCE_ID_PREFIX + "*.json")
            if not path.name.endswith(".binding.json")))

    def load(self, dataset_id: str) -> GovernedCounterfactualEvidence | None:
        path = self.path_for(dataset_id)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CounterfactualEvidenceError(
                INVALID_COUNTERFACTUAL_SCHEMA + ":unreadable:" + str(path)) from exc
        return validate_governed_counterfactual_evidence(payload)

    def binding_for(
        self, dataset_id: str,
    ) -> GovernedCounterfactualBinding | None:
        path = self.binding_path_for(dataset_id)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CounterfactualEvidenceError(
                INVALID_COUNTERFACTUAL_SCHEMA + ":unreadable:" + str(path)) from exc
        return GovernedCounterfactualBinding.from_dict(payload)

    def latest_pointer(self) -> dict[str, Any] | None:
        path = self.directory / _LATEST_POINTER
        if not path.is_file():
            return None
        try:
            return dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return None

    def load_latest(self) -> GovernedCounterfactualEvidence | None:
        pointer = self.latest_pointer()
        if not pointer:
            return None
        return self.load(str(pointer.get("dataset_id") or ""))


__all__ = [
    "COUNTERFACTUAL_BINDING_SCHEMA",
    "COUNTERFACTUAL_EVIDENCE_CLASS",
    "COUNTERFACTUAL_EVIDENCE_ID_PREFIX",
    "COUNTERFACTUAL_PRODUCER_IDENTITY",
    "COUNTERFACTUAL_PRODUCER_VERSION",
    "CounterfactualEvidenceError",
    "CounterfactualEvidenceExclusion",
    "CounterfactualEvidenceRow",
    "CounterfactualEvidenceStore",
    "DEFAULT_COUNTERFACTUAL_EVIDENCE_DIRECTORY",
    "GOVERNED_COUNTERFACTUAL_EVIDENCE_SCHEMA",
    "GOVERNED_POLICY_CATALOGUE",
    "GovernedCounterfactualBinding",
    "GovernedCounterfactualEvidence",
    "INCOMPLETE_REPLAY",
    "INSUFFICIENT_SAMPLE",
    "INVALID_COUNTERFACTUAL_SCHEMA",
    "LEAKAGE_GUARD_FAILED",
    "M5_AUTHORITY",
    "MISSING_BASELINE",
    "MISSING_COUNTERFACTUAL_EVIDENCE",
    "MISSING_M5_CANDLE_AUTHORITY",
    "MISSING_SHADOW_LIFECYCLE_POPULATION",
    "NO_INTERVENTION_MAPPING",
    "REASON_CODES",
    "REPLAY_METHOD_IDENTITY",
    "REPLAY_METHOD_VERSION",
    "SCIENTIFICALLY_NOT_MEANINGFUL",
    "SOURCE_DATASET_M5_CANDLES",
    "SOURCE_DATASET_SHADOW_RUNTIME",
    "STALE_FRONTIER",
    "SUPERSEDED_EVIDENCE",
    "TREATMENT_COMPONENT_BY_POLICY_TYPE",
    "TREATMENT_SIGNATURE_MISMATCH",
    "UNKNOWN_GOVERNED_POLICY",
    "UPSTREAM_INVALIDATED",
    "VALIDATION_CRITERIA_INCOMPLETE",
    "VALIDITY_ELIGIBLE",
    "VALIDITY_EXCLUDED",
    "bind_counterfactual_evidence",
    "build_governed_counterfactual_evidence",
    "rebuild_governed_exit_evidence",
    "treatment_component",
    "treatment_signature",
    "validate_governed_counterfactual_evidence",
    "verify_counterfactual_rows",
    "verify_governed_counterfactual_binding",
]
