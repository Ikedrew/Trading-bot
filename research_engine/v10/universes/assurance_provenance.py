"""Stage IV Wave 4 provenance, evidence accounting and substitution governance.

This module owns the *proof infrastructure* that makes a Wave 4 qualification
self-explaining without redesigning the qualification model itself:

* a deduplicated, deterministically identified **blocker provenance ledger**;
* per-question / per-source **historical record accounting** whose core
  invariant is ``candidate = used + excluded + unexplained``;
* governed **field resolution** so a similarly named field can never silently
  satisfy a required field;
* governed **dataset substitution** so one dataset can never silently satisfy
  another (notably ``shadow_runtime`` vs ``shadow_trades``);
* report-authority capture so a legacy/invalidated/stale answer can never be
  consumed as a current scientific conclusion;
* historical-exhaustion evaluation so a future-data request cannot be emitted
  before every legitimate recovery route over existing evidence is exhausted.

Nothing here reads production data, reruns reconstruction, or reaches into
Wave 2 / Wave 3.  Those remain the upstream authorities; this module only
persists and validates *references* to their persisted results.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any, Iterable, Mapping, Sequence


BLOCKER_SCHEMA_VERSION = 1
ACCOUNTING_SCHEMA_VERSION = 1
SUBSTITUTION_SCHEMA_VERSION = 1
FIELD_RESOLUTION_SCHEMA_VERSION = 1


class OriginStage:
    """Where a blocker really came from.  Never invent a synthetic origin."""

    WAVE2 = "WAVE2"
    WAVE3 = "WAVE3"
    WAVE4_REQUIREMENT = "WAVE4_REQUIREMENT"
    REPORT_VALIDITY = "REPORT_VALIDITY"
    IMPLEMENTATION = "IMPLEMENTATION"
    STATISTICAL_SUFFICIENCY = "STATISTICAL_SUFFICIENCY"
    HISTORICAL_ACCOUNTING = "HISTORICAL_ACCOUNTING"

    ALL = frozenset({
        WAVE2, WAVE3, WAVE4_REQUIREMENT, REPORT_VALIDITY,
        IMPLEMENTATION, STATISTICAL_SUFFICIENCY, HISTORICAL_ACCOUNTING,
    })


class OriginType:
    INTEGRITY_FINDING = "INTEGRITY_FINDING"
    RECONSTRUCTION = "RECONSTRUCTION"
    RECONCILIATION_RESULT = "RECONCILIATION_RESULT"
    EVIDENCE_REQUIREMENT = "EVIDENCE_REQUIREMENT"
    CONTRACT_REQUIREMENT = "CONTRACT_REQUIREMENT"
    RECORD_ACCOUNTING = "RECORD_ACCOUNTING"
    REPORT_AUTHORITY = "REPORT_AUTHORITY"
    IMPLEMENTATION_BINDING = "IMPLEMENTATION_BINDING"
    STATISTICAL_ASSESSMENT = "STATISTICAL_ASSESSMENT"
    FIELD_RESOLUTION = "FIELD_RESOLUTION"
    DATASET_SUBSTITUTION = "DATASET_SUBSTITUTION"
    ANSWER_BINDING = "ANSWER_BINDING"


class FieldResolutionType:
    EXACT = "EXACT"
    GOVERNED_ALIAS = "GOVERNED_ALIAS"
    GOVERNED_PROJECTION = "GOVERNED_PROJECTION"
    RECONSTRUCTED = "RECONSTRUCTED"
    UNAVAILABLE = "UNAVAILABLE"

    ALL = frozenset({EXACT, GOVERNED_ALIAS, GOVERNED_PROJECTION, RECONSTRUCTED, UNAVAILABLE})
    #: resolution types that are allowed to satisfy a contractual requirement
    SATISFYING = frozenset({EXACT, GOVERNED_ALIAS, GOVERNED_PROJECTION, RECONSTRUCTED})


#: Only these authorities may vouch for a field resolution.  ``""`` and any
#: unknown string mean the resolution is *not* governed and cannot substitute.
GOVERNED_FIELD_RESOLUTION_AUTHORITIES = frozenset({
    "EVIDENCE_CONTRACT_CANONICAL_FIELD",
    "GOVERNED_FIELD_ALIAS_TABLE",
    "GOVERNED_FIELD_PROJECTION",
    "WAVE2_RECONSTRUCTION_RULE",
})

#: Only these authorities may vouch for a dataset substitution.
GOVERNED_DATASET_SUBSTITUTION_AUTHORITIES = frozenset({
    "WAVE2_RECONSTRUCTION_RULE",
    "GOVERNED_DATASET_ALIAS",
    "GOVERNED_DATASET_PROJECTION",
})


#: Requirement dimensions that must be backed by *upstream authoritative*
#: evidence.  A question definition, a Wave 4 result, or any other self-attested
#: claim can never satisfy one of these on its own (no self-certification).
GOVERNED_REQUIREMENT_AUTHORITIES: Mapping[str, frozenset[str]] = {
    "lineage": frozenset({"PERSISTED_LINEAGE_EVIDENCE"}),
    "resolution": frozenset({"GOVERNED_SOURCE_RESOLUTION"}),
    "integrity": frozenset({"WAVE2_INTEGRITY_REPORT"}),
    "reconciliation": frozenset({"WAVE3_RECONCILIATION_REPORT"}),
    "historical_exhaustiveness": frozenset({"RECORD_ACCOUNTING"}),
}

#: Authorities a question is *not* allowed to use to vouch for itself.
SELF_ATTESTED_AUTHORITIES = frozenset({
    "",
    "SELF",
    "QUESTION_DEFINITION",
    "QUESTION_EVIDENCE_CONTRACT",
    "WAVE4_RESULT",
    "WAVE4_QUALIFICATION",
    "ASSUMED",
})



class HistoricalExhaustionStatus:
    EXHAUSTED = "EXHAUSTED"
    RECOVERABLE_EXISTING_DATA = "RECOVERABLE_EXISTING_DATA"
    ACCOUNTING_UNRESOLVED = "ACCOUNTING_UNRESOLVED"
    HISTORICALLY_UNRECOVERABLE = "HISTORICALLY_UNRECOVERABLE"

    ALL = frozenset({
        EXHAUSTED, RECOVERABLE_EXISTING_DATA,
        ACCOUNTING_UNRESOLVED, HISTORICALLY_UNRECOVERABLE,
    })
    #: the only states that may support a future-data requirement
    SUPPORTS_FUTURE_DATA = frozenset({EXHAUSTED, HISTORICALLY_UNRECOVERABLE})


class ResultAvailability:
    CURRENT_ANALYSIS_AVAILABLE = "CURRENT_ANALYSIS_AVAILABLE"
    EVIDENCE_EXISTS_BUT_CURRENT_ANALYSIS_MISSING = "EVIDENCE_EXISTS_BUT_CURRENT_ANALYSIS_MISSING"
    NO_EVIDENCE = "NO_EVIDENCE"

    ALL = frozenset({
        CURRENT_ANALYSIS_AVAILABLE,
        EVIDENCE_EXISTS_BUT_CURRENT_ANALYSIS_MISSING,
        NO_EVIDENCE,
    })


#: Report validity classifications that may never back a *current* conclusion.
NON_CURRENT_REPORT_VALIDITIES = frozenset({
    "", "INVALIDATED", "LEGACY", "STALE", "SUPERSEDED", "MISSING", "UNKNOWN",
})


class ExclusionReason:
    """Machine-readable, non-generic reasons a record left the population."""

    WRONG_EVIDENCE_EPOCH_TRANSITIONAL = "WRONG_EVIDENCE_EPOCH_TRANSITIONAL"
    WRONG_EVIDENCE_EPOCH_LEGACY = "WRONG_EVIDENCE_EPOCH_LEGACY"
    AMBIGUOUS_OR_UNMATCHED_IDENTITY = "AMBIGUOUS_OR_UNMATCHED_IDENTITY"
    MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
    FAILED_SIZEING_QUALITY_PREDICATE = "FAILED_SIZEING_QUALITY_PREDICATE"
    SOURCE_RESOLUTION_FAILED = "SOURCE_RESOLUTION_FAILED"
    OUTSIDE_REQUIRED_HORIZON = "OUTSIDE_REQUIRED_HORIZON"
    FAILED_CANONICAL_IDENTITY = "FAILED_CANONICAL_IDENTITY"
    DUPLICATE_CANONICAL_RECORD = "DUPLICATE_CANONICAL_RECORD"
    REQUIRED_RELATIONSHIP_ABSENT = "REQUIRED_RELATIONSHIP_ABSENT"
    INCOMPLETE_LIFECYCLE = "INCOMPLETE_LIFECYCLE"
    EXCLUDED_BY_EXPLICIT_POPULATION_PREDICATE = "EXCLUDED_BY_EXPLICIT_POPULATION_PREDICATE"
    MALFORMED_SOURCE_RECORD = "MALFORMED_SOURCE_RECORD"


GOVERNED_EXCLUSION_REASONS = frozenset({
    ExclusionReason.WRONG_EVIDENCE_EPOCH_TRANSITIONAL,
    ExclusionReason.WRONG_EVIDENCE_EPOCH_LEGACY,
    ExclusionReason.AMBIGUOUS_OR_UNMATCHED_IDENTITY,
    ExclusionReason.MISSING_REQUIRED_FIELD,
    ExclusionReason.FAILED_SIZEING_QUALITY_PREDICATE,
    ExclusionReason.SOURCE_RESOLUTION_FAILED,
    ExclusionReason.OUTSIDE_REQUIRED_HORIZON,
    ExclusionReason.FAILED_CANONICAL_IDENTITY,
    ExclusionReason.DUPLICATE_CANONICAL_RECORD,
    ExclusionReason.REQUIRED_RELATIONSHIP_ABSENT,
    ExclusionReason.INCOMPLETE_LIFECYCLE,
    ExclusionReason.EXCLUDED_BY_EXPLICIT_POPULATION_PREDICATE,
    ExclusionReason.MALFORMED_SOURCE_RECORD,
})


def _native(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _native(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_native(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_native(item) for item in value)
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(_native(value), sort_keys=True, separators=(",", ":"), default=str)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _explicit(value: Any) -> Any:
    """Return ``None`` for absent values so absence is explicit, never implied."""
    if value is None:
        return None
    if isinstance(value, str):
        return value if value != "" else None
    return value


def _identity_part(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


@dataclass(frozen=True)
class BlockerRecord:
    """One deduplicated blocker instance with its full provenance."""

    blocker_id: str
    question_id: str
    reason_code: str
    origin_stage: str
    origin_type: str
    origin_id: str | None
    dataset: str | None
    universe: str | None
    relationship: str | None
    required_field: str | None
    population_scope: str | None
    affected_record_count: int | None
    fatal_to_question: bool
    evidence_references: tuple[str, ...]
    provenance_fingerprint: str
    relevance_scope: tuple[str, ...] = ()
    required_by_contract: tuple[str, ...] = ()
    affected_population: str | None = None
    dependent_impact: bool | None = None
    schema: int = BLOCKER_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _native(asdict(self))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BlockerRecord":
        data = dict(value)
        for name in ("evidence_references", "relevance_scope", "required_by_contract"):
            data[name] = tuple(data.get(name) or ())
        data["schema"] = int(data.get("schema", BLOCKER_SCHEMA_VERSION))
        return cls(**data)


def blocker_identity(
    *,
    question_id: str,
    reason_code: str,
    origin_stage: str,
    origin_id: Any = None,
    dataset: Any = None,
    universe: Any = None,
    relationship: Any = None,
    required_field: Any = None,
    population_scope: Any = None,
) -> dict[str, Any]:
    """Canonical blocker identity.

    Built from *what* the blocker is, never from list position, so repeated
    qualification over the same frozen inputs yields the same identity while
    materially different conditions stay distinct.
    """
    return {
        "question_id": str(question_id),
        "reason_code": str(reason_code),
        "origin_stage": str(origin_stage),
        "origin_id": _identity_part(origin_id),
        "dataset": _identity_part(dataset),
        "universe": _identity_part(universe),
        "relationship": _identity_part(relationship),
        "required_field": _identity_part(required_field),
        "population_scope": _identity_part(population_scope),
    }


def make_blocker(
    *,
    question_id: str,
    reason_code: str,
    origin_stage: str,
    origin_type: str,
    origin_id: Any = None,
    dataset: Any = None,
    universe: Any = None,
    relationship: Any = None,
    required_field: Any = None,
    population_scope: Any = None,
    affected_record_count: int | None = None,
    fatal_to_question: bool = True,
    evidence_references: Iterable[Any] = (),
    relevance_scope: Iterable[Any] = (),
    required_by_contract: Iterable[Any] = (),
    affected_population: Any = None,
    dependent_impact: bool | None = None,
) -> BlockerRecord:
    """Create a deterministically identified blocker instance."""
    if origin_stage not in OriginStage.ALL:
        raise ValueError(f"unknown blocker origin stage: {origin_stage!r}")
    identity = blocker_identity(
        question_id=question_id,
        reason_code=reason_code,
        origin_stage=origin_stage,
        origin_id=origin_id,
        dataset=dataset,
        universe=universe,
        relationship=relationship,
        required_field=required_field,
        population_scope=population_scope,
    )
    references = tuple(dict.fromkeys(
        str(item) for item in evidence_references if item not in (None, "")
    ))
    provenance = dict(identity)
    provenance["origin_type"] = str(origin_type)
    provenance["evidence_references"] = list(references)
    provenance["affected_record_count"] = affected_record_count
    provenance["fatal_to_question"] = bool(fatal_to_question)
    return BlockerRecord(
        blocker_id=f"BLK-{fingerprint(identity)[:24]}",
        question_id=str(question_id),
        reason_code=str(reason_code),
        origin_stage=str(origin_stage),
        origin_type=str(origin_type),
        origin_id=_explicit(origin_id),
        dataset=_explicit(dataset),
        universe=_explicit(universe),
        relationship=_explicit(relationship),
        required_field=_explicit(required_field),
        population_scope=_explicit(population_scope),
        affected_record_count=None if affected_record_count is None else int(affected_record_count),
        fatal_to_question=bool(fatal_to_question),
        evidence_references=references,
        provenance_fingerprint=fingerprint(provenance),
        relevance_scope=tuple(dict.fromkeys(
            str(item) for item in relevance_scope if item not in (None, "")
        )),
        required_by_contract=tuple(dict.fromkeys(
            str(item) for item in required_by_contract if item not in (None, "")
        )),
        affected_population=_explicit(affected_population),
        dependent_impact=dependent_impact,
    )


def deduplicate_blockers(blockers: Iterable[BlockerRecord | Iterable[BlockerRecord]]) -> tuple[BlockerRecord, ...]:
    """Collapse repeated references to the same underlying condition.

    The helper accepts both a flat blocker stream and nested per-question
    collections; both are normalized to one deterministic blocker ledger.
    """
    ordered: dict[str, BlockerRecord] = {}

    def _flatten(items: Iterable[BlockerRecord | Iterable[BlockerRecord]]) -> Iterable[BlockerRecord]:
        for item in items:
            if isinstance(item, BlockerRecord):
                yield item
            elif isinstance(item, (tuple, list, set, frozenset)):
                yield from _flatten(item)
            else:
                raise TypeError(f"unexpected blocker item type: {type(item)!r}")

    for item in _flatten(blockers):
        existing = ordered.get(item.blocker_id)
        if existing is None:
            ordered[item.blocker_id] = item
        elif existing != item:
            raise ValueError(
                f"blocker identity collision with divergent provenance: {item.blocker_id}"
            )
    return tuple(ordered[key] for key in sorted(ordered))


def build_blocker_ledger(
    per_question: Iterable[Iterable[BlockerRecord]],
) -> tuple[BlockerRecord, ...]:
    """Flatten per-question blockers into one unique, sorted ledger."""
    return deduplicate_blockers(
        item for question_blockers in per_question for item in question_blockers
    )


def ledger_category_counts(ledger: Sequence[BlockerRecord]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in ledger:
        counts[item.reason_code] = counts.get(item.reason_code, 0) + 1
    return dict(sorted(counts.items()))


def ledger_evidence_reference_count(ledger: Sequence[BlockerRecord]) -> int:
    return len({
        reference
        for item in ledger
        for reference in item.evidence_references
    })


def assert_ledger_reconciles(
    ledger: Sequence[BlockerRecord],
    question_references: Mapping[str, Sequence[str]],
    category_counts: Mapping[str, int],
) -> None:
    """Fail closed unless every ledger total agrees with every other total."""
    ledger_ids = [item.blocker_id for item in ledger]
    if len(set(ledger_ids)) != len(ledger_ids):
        raise ValueError("blocker ledger contains duplicate blocker ids")
    references = [blocker_id for ids in question_references.values() for blocker_id in ids]
    if len(set(references)) != len(references):
        raise ValueError("a question references the same blocker more than once")
    unknown = set(references) - set(ledger_ids)
    if unknown:
        raise ValueError(f"question blocker references without ledger entries: {sorted(unknown)[:5]}")
    unreferenced = set(ledger_ids) - set(references)
    if unreferenced:
        raise ValueError(f"ledger blockers never referenced by a question: {sorted(unreferenced)[:5]}")
    if dict(sorted(category_counts.items())) != ledger_category_counts(ledger):
        raise ValueError("blocker category counts do not reconcile with the blocker ledger")
    if sum(category_counts.values()) != len(ledger):
        raise ValueError("blocker category counts do not sum to the unique blocker count")
    if sum(len(ids) for ids in question_references.values()) != len(ledger):
        raise ValueError("question->blocker reference count does not equal the unique blocker count")


@dataclass(frozen=True)
class FieldResolution:
    required_field: str
    resolved_field: str
    source_dataset: str
    resolution_authority: str
    resolution_type: str
    schema: int = FIELD_RESOLUTION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _native(asdict(self))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FieldResolution":
        data = dict(value)
        data["schema"] = int(data.get("schema", FIELD_RESOLUTION_SCHEMA_VERSION))
        return cls(**data)


def evaluate_field_resolution(resolution: FieldResolution) -> tuple[bool, str]:
    """Return ``(satisfied, reason)`` for one contractual field requirement.

    A field only satisfies the contract when it is the exact canonical field or
    when a governed alias/projection/reconstruction carries it.  A similarly
    named field is never enough.
    """
    if resolution.resolution_type not in FieldResolutionType.ALL:
        return False, f"unknown resolution type {resolution.resolution_type!r}"
    if resolution.resolution_type == FieldResolutionType.UNAVAILABLE:
        return False, "required field is unavailable"
    if not str(resolution.resolution_authority).strip():
        return False, "field resolution carries no authority"
    if resolution.resolution_authority not in GOVERNED_FIELD_RESOLUTION_AUTHORITIES:
        return False, f"ungoverned field resolution authority {resolution.resolution_authority!r}"
    if resolution.resolution_type == FieldResolutionType.EXACT:
        if resolution.resolved_field != resolution.required_field:
            return False, "EXACT resolution must name the canonical required field"
        return True, "exact canonical field"
    if not str(resolution.resolved_field).strip():
        return False, "governed resolution names no resolved field"
    if resolution.resolved_field == resolution.required_field:
        return True, "canonical field restated through a governed projection"
    return True, f"governed {resolution.resolution_type} resolution"


def resolve_contract_fields(
    required_fields: Sequence[str],
    provided: Sequence[FieldResolution] | None,
    observed_fields: Sequence[str] = (),
    default_authority: str = "EVIDENCE_CONTRACT_CANONICAL_FIELD",
) -> tuple[FieldResolution, ...]:
    """Produce exactly one governed resolution per contractual field."""
    supplied = {item.required_field: item for item in provided or ()}
    observed = set(observed_fields)
    resolutions: list[FieldResolution] = []
    for name in required_fields:
        existing = supplied.get(name)
        if existing is not None and existing.required_field == name:
            resolutions.append(existing)
        elif name in observed:
            resolutions.append(FieldResolution(
                required_field=name,
                resolved_field=name,
                source_dataset="",
                resolution_authority=default_authority,
                resolution_type=FieldResolutionType.EXACT,
            ))
        else:
            resolutions.append(FieldResolution(
                required_field=name,
                resolved_field="",
                source_dataset="",
                resolution_authority="",
                resolution_type=FieldResolutionType.UNAVAILABLE,
            ))
    return tuple(resolutions)


@dataclass(frozen=True)
class DatasetSubstitution:
    requested_source: str
    actual_source: str
    substitution_authority: str
    reconstruction_rule: str
    information_loss: tuple[str, ...] = ()
    question_relevant_loss: bool = True
    governed: bool = False
    schema: int = SUBSTITUTION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _native(asdict(self))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DatasetSubstitution":
        data = dict(value)
        data["information_loss"] = tuple(data.get("information_loss") or ())
        data["schema"] = int(data.get("schema", SUBSTITUTION_SCHEMA_VERSION))
        return cls(**data)


def is_governed_substitution(substitution: DatasetSubstitution) -> bool:
    return bool(
        str(substitution.substitution_authority).strip()
        and substitution.substitution_authority in GOVERNED_DATASET_SUBSTITUTION_AUTHORITIES
    )


def evaluate_dataset_substitution(
    substitution: DatasetSubstitution,
    *,
    permitted: bool,
) -> tuple[bool, str]:
    """Return ``(accepted, reason)`` for one dataset substitution.

    Reconstruction existing is not enough: the substitution must be governed
    *and* the declared information loss must have been judged irrelevant to the
    question that is trying to consume it.
    """
    if not str(substitution.substitution_authority).strip():
        return False, "dataset substitution carries no authority"
    if substitution.substitution_authority not in GOVERNED_DATASET_SUBSTITUTION_AUTHORITIES:
        return False, f"ungoverned dataset substitution authority {substitution.substitution_authority!r}"
    if not permitted:
        return False, "declared information loss is relevant to this question"
    return True, "governed substitution with question-irrelevant loss"


@dataclass(frozen=True)
class SourceAccounting:
    source_name: str
    candidate_records: int
    used_records: int
    excluded_records: int
    unexplained_records: int
    exclusion_reason_counts: Mapping[str, int]
    source_present: bool
    source_required: bool
    substitution_used: bool = False
    substitution_authority: str | None = None
    reconstruction_used: bool = False

    def to_dict(self) -> dict[str, Any]:
        return _native(asdict(self))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SourceAccounting":
        return cls(**dict(value))


def build_source_accounting(
    *,
    source_name: str,
    candidate_records: int,
    used_records: int,
    exclusion_reason_counts: Mapping[str, int],
    source_present: bool,
    source_required: bool,
    substitution_used: bool = False,
    substitution_authority: str | None = None,
    reconstruction_used: bool = False,
) -> SourceAccounting:
    """One source's stage-one accounting.

    ``unexplained_records`` is derived, never asserted: whatever the
    conservation identity does not explain stays visibly unexplained.
    """
    unknown = set(exclusion_reason_counts) - GOVERNED_EXCLUSION_REASONS
    if unknown:
        raise ValueError(f"ungoverned exclusion reasons: {sorted(unknown)}")
    excluded_records = int(sum(int(count) for count in exclusion_reason_counts.values()))
    return SourceAccounting(
        source_name=str(source_name),
        candidate_records=int(candidate_records),
        used_records=int(used_records),
        excluded_records=excluded_records,
        unexplained_records=int(candidate_records) - int(used_records) - excluded_records,
        exclusion_reason_counts=dict(sorted(
            (str(key), int(value)) for key, value in exclusion_reason_counts.items() if int(value)
        )),
        source_present=bool(source_present),
        source_required=bool(source_required),
        substitution_used=bool(substitution_used),
        substitution_authority=substitution_authority,
        reconstruction_used=bool(reconstruction_used),
    )


def build_evidence_accounting(
    *,
    sources: Sequence[SourceAccounting],
    used_records: int,
    population_stage: Mapping[str, Any] | None = None,
    historical_exhaustion_status: str,
    historical_exhaustion_reason: str,
    resolved: bool,
) -> dict[str, Any]:
    """Question-level accounting with two separately conserved stages.

    Source rows and analytical population units are not necessarily additive:
    joins can combine several source rows into one scientific observation.
    The top-level identity therefore conserves discovered source records, while
    ``population_stage`` independently conserves the governed analytical
    denominator.  Keeping the units separate prevents join inputs from being
    silently mistaken for excluded scientific observations.
    """
    stage_one_candidate = int(sum(item.candidate_records for item in sources))
    stage_one_excluded = int(sum(item.excluded_records for item in sources))
    stage_one_unexplained = int(sum(item.unexplained_records for item in sources))
    stage_two = dict(population_stage or {})
    stage_two_excluded = int(stage_two.get("excluded_records", 0) or 0)
    source_used_total = int(sum(item.used_records for item in sources))
    population_used_total = int(stage_two.get("used_records", used_records) or 0)

    exclusion_counts: dict[str, int] = {}
    for item in sources:
        for reason, count in item.exclusion_reason_counts.items():
            exclusion_counts[reason] = exclusion_counts.get(reason, 0) + int(count)
    stage_two_candidate = int(stage_two.get("candidate_records", stage_one_candidate - stage_one_excluded))
    stage_two_declared = stage_two.get("unexplained_records")
    stage_two_unexplained = (
        int(stage_two_declared) if stage_two_declared is not None
        else stage_two_candidate - population_used_total - stage_two_excluded
    )

    return {
        "schema": ACCOUNTING_SCHEMA_VERSION,
        "resolved": bool(resolved),
        "candidate_records": stage_one_candidate,
        "used_records": source_used_total,
        "excluded_records": stage_one_excluded,
        "unexplained_records": stage_one_unexplained,
        "exclusion_reason_counts": dict(sorted(
            (str(key), int(value)) for key, value in exclusion_counts.items() if int(value)
        )),
        "source_accounting": [item.to_dict() for item in sources],
        "population_stage": {
            "candidate_records": stage_two_candidate,
            "used_records": population_used_total,
            "excluded_records": stage_two_excluded,
            "unexplained_records": stage_two_unexplained,
            "exclusion_reason_counts": dict(sorted(
                (str(key), int(value))
                for key, value in (stage_two.get("exclusion_reason_counts") or {}).items()
                if int(value)
            )),
            "stage_one_unexplained_records": stage_one_unexplained,
        },
        "historical_exhaustion_status": str(historical_exhaustion_status),
        "historical_exhaustion_reason": str(historical_exhaustion_reason),
    }


def assert_accounting_conservation(accounting: Mapping[str, Any]) -> None:
    """Fail closed unless every accounting stage conserves its records."""
    sources = accounting.get("source_accounting") or []
    for item in sources:
        residual = (
            int(item.get("candidate_records", 0) or 0)
            - int(item.get("used_records", 0) or 0)
            - int(item.get("excluded_records", 0) or 0)
        )
        if residual != int(item.get("unexplained_records", 0) or 0):
            raise ValueError(f"source accounting does not conserve records: {item.get('source_name')!r}")
    stage_one_candidate = sum(int(item.get("candidate_records", 0) or 0) for item in sources)
    stage_one_excluded = sum(int(item.get("excluded_records", 0) or 0) for item in sources)
    stage_one_used = sum(int(item.get("used_records", 0) or 0) for item in sources)
    if stage_one_candidate - stage_one_used - stage_one_excluded != sum(
        int(item.get("unexplained_records", 0) or 0) for item in sources
    ):
        raise ValueError("stage-one accounting does not conserve records")
    total = (
        int(accounting.get("candidate_records", 0) or 0)
        - int(accounting.get("used_records", 0) or 0)
        - int(accounting.get("excluded_records", 0) or 0)
    )
    if total != int(accounting.get("unexplained_records", 0) or 0):
        raise ValueError("question accounting does not conserve records")
    stage = accounting.get("population_stage") or {}
    stage_residual = (
        int(stage.get("candidate_records", 0) or 0)
        - int(stage.get("used_records", 0) or 0)
        - int(stage.get("excluded_records", 0) or 0)
    )
    if stage_residual != int(stage.get("unexplained_records", 0) or 0):
        raise ValueError("population stage accounting does not conserve records")


def evaluate_historical_exhaustion(
    accounting: Mapping[str, Any],
    *,
    required_sources: Sequence[str] = (),
    recovery_routes_remaining: bool = False,
) -> tuple[str, str]:
    """Decide whether existing historical evidence has been exhausted.

    Only ``EXHAUSTED`` or ``HISTORICALLY_UNRECOVERABLE`` may ever support a
    future-data requirement; an unresolved denominator never can.
    """
    if not accounting.get("resolved"):
        return (
            HistoricalExhaustionStatus.ACCOUNTING_UNRESOLVED,
            "record accounting denominator cannot be established",
        )
    if int(accounting.get("unexplained_records", 0) or 0) != 0:
        return (
            HistoricalExhaustionStatus.ACCOUNTING_UNRESOLVED,
            "candidate records are neither used nor excluded with a reason",
        )
    population = accounting.get("population_stage") or {}
    if int(population.get("unexplained_records", 0) or 0) != 0:
        return (
            HistoricalExhaustionStatus.ACCOUNTING_UNRESOLVED,
            "analytical population records are neither used nor excluded with a reason",
        )
    if int(accounting.get("used_records", 0) or 0) < 0 or int(accounting.get("excluded_records", 0) or 0) < 0:
        return (
            HistoricalExhaustionStatus.ACCOUNTING_UNRESOLVED,
            "record accounting produced a negative count",
        )
    sources = accounting.get("source_accounting") or []
    if required_sources and not sources:
        return (
            HistoricalExhaustionStatus.ACCOUNTING_UNRESOLVED,
            "no source accounting was supplied for required sources",
        )
    missing_required = [
        item for item in sources
        if item.get("source_required") and not item.get("source_present")
    ]
    if recovery_routes_remaining:
        return (
            HistoricalExhaustionStatus.RECOVERABLE_EXISTING_DATA,
            "a governed recovery route over existing evidence remains",
        )
    if missing_required:
        return (
            HistoricalExhaustionStatus.HISTORICALLY_UNRECOVERABLE,
            "required source absent after every governed recovery route",
        )
    return (
        HistoricalExhaustionStatus.EXHAUSTED,
        "every required source accounted for with no unexplained record",
    )


def supports_future_data(historical_exhaustion_status: str) -> bool:
    return historical_exhaustion_status in HistoricalExhaustionStatus.SUPPORTS_FUTURE_DATA


def requirement_authority_is_governed(dimension: str, authority: Any) -> bool:
    """True only when an *upstream* authority vouches for a requirement."""
    allowed = GOVERNED_REQUIREMENT_AUTHORITIES.get(str(dimension))
    declared = str(authority or "").strip()
    if allowed is None or not declared:
        return False
    if declared in SELF_ATTESTED_AUTHORITIES:
        return False
    return declared in allowed



def _hashable(value: Any) -> str:
    if isinstance(value, Mapping):
        return fingerprint(value)
    if value in (None, ""):
        return ""
    if isinstance(value, str):
        return value
    return fingerprint(value)


def build_report_authority(
    *,
    report_path: str,
    report_fingerprint_value: Any,
    report_validity: str,
    evidence_epoch: str,
    ownership_authority: str,
    evidence_present: bool,
) -> dict[str, Any]:
    """Persist the exact authority a question used for its existing answer."""
    validity = str(report_validity or "").upper()
    current = validity == "VALID_CURRENT" and bool(str(report_path).strip())
    if current:
        availability = ResultAvailability.CURRENT_ANALYSIS_AVAILABLE
    elif evidence_present:
        availability = ResultAvailability.EVIDENCE_EXISTS_BUT_CURRENT_ANALYSIS_MISSING
    else:
        availability = ResultAvailability.NO_EVIDENCE
    return {
        "report_path": str(report_path or ""),
        "report_fingerprint": _hashable(report_fingerprint_value),
        "report_validity": validity,
        "evidence_epoch": str(evidence_epoch or ""),
        "ownership_authority": str(ownership_authority or ""),
        "result_availability": availability,
        "current_conclusion_permitted": current,
    }


def consumes_non_current_authority(report_authority: Mapping[str, Any]) -> bool:
    """True when a question leans on a legacy/invalidated/stale/missing answer."""
    return str(report_authority.get("report_validity") or "").upper() in NON_CURRENT_REPORT_VALIDITIES


__all__ = [
    "ACCOUNTING_SCHEMA_VERSION",
    "BLOCKER_SCHEMA_VERSION",
    "BlockerRecord",
    "DatasetSubstitution",
    "ExclusionReason",
    "FieldResolution",
    "FieldResolutionType",
    "GOVERNED_DATASET_SUBSTITUTION_AUTHORITIES",
    "GOVERNED_EXCLUSION_REASONS",
    "GOVERNED_FIELD_RESOLUTION_AUTHORITIES",
    "GOVERNED_REQUIREMENT_AUTHORITIES",
    "HistoricalExhaustionStatus",
    "NON_CURRENT_REPORT_VALIDITIES",
    "OriginStage",
    "OriginType",
    "ResultAvailability",
    "SELF_ATTESTED_AUTHORITIES",
    "SourceAccounting",
    "assert_accounting_conservation",
    "assert_ledger_reconciles",
    "blocker_identity",
    "build_blocker_ledger",
    "build_evidence_accounting",
    "build_report_authority",
    "build_source_accounting",
    "canonical_json",
    "consumes_non_current_authority",
    "deduplicate_blockers",
    "evaluate_dataset_substitution",
    "evaluate_field_resolution",
    "evaluate_historical_exhaustion",
    "fingerprint",
    "is_governed_substitution",
    "ledger_category_counts",
    "ledger_evidence_reference_count",
    "make_blocker",
    "requirement_authority_is_governed",
    "resolve_contract_fields",
    "supports_future_data",
]









