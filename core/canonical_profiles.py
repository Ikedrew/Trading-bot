"""
Canonical V1 Record Profiles — the enforcement half of the production data contract.

A dataset may claim ``schema_version = <name>_v1`` ONLY when:
    1. a V1 profile is REGISTERED here for that dataset, AND
    2. the record carries the canonical schema string the contract declares, AND
    3. the record satisfies that profile's required-field / type / version rules.

This closes the gap the audit found: previously a writer could stamp ``*_v1``
with no registered profile governing the record. ``validate_record`` makes an
unregistered or malformed ``_v1`` claim fail visibly instead of silently passing.

Lifecycle-awareness: required vs optional fields reflect where in the funnel a
record is produced. Example: ``market_context`` and ``execution_context`` are
captured BEFORE the canonical opportunity is minted, so ``canonical_opportunity_id``
is OPTIONAL there (never fabricated). ``entity_id`` is the universal spine.

This module owns validation only. It does NOT recompute or mutate records, and it
is never on the trading hot path (persistence/observability layer).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.production_data_contract import (
    PRODUCTION_SCHEMA_REGISTRY,
    current_schema,
    current_generation,
)


@dataclass(frozen=True)
class CanonicalProfile:
    """The V1 field/version contract for one dataset's persisted record."""
    dataset: str
    schema_version: str          # canonical string, e.g. "events_v1"
    generation: int              # numeric generation (V1 baseline == 1)
    required_fields: tuple[str, ...]      # must be present AND non-empty
    optional_fields: tuple[str, ...] = ()  # may be present (lifecycle-dependent)
    # Additional numeric version-bearing fields that MUST equal 1 on this baseline.
    version_fields: tuple[str, ...] = ()


# ─── V1 PROFILES ──────────────────────────────────────────────────────────────
# Required fields are the canonical root + the lineage keys that genuinely exist
# at that dataset's lifecycle stage. entity_id is the universal join spine.
_COMMON_LINEAGE = ("entity_id",)

_PROFILES: dict[str, CanonicalProfile] = {
    "events": CanonicalProfile(
        dataset="events", schema_version=current_schema("events"),
        generation=current_generation("events"),
        required_fields=("ts_utc_ms", "type", "schema_version", "event_layout_version"),
        optional_fields=("symbol", "source", "payload", "feature_version"),
        version_fields=("event_layout_version",),
    ),
    "market_context": CanonicalProfile(
        dataset="market_context", schema_version=current_schema("market_context"),
        generation=current_generation("market_context"),
        # Captured pre-qualification: canonical root legitimately absent here.
        required_fields=("schema_version", "symbol", "entity_id"),
        optional_fields=("correlation_id", "canonical_opportunity_id", "bar_time"),
    ),
    "opportunities": CanonicalProfile(
        dataset="opportunities", schema_version=current_schema("opportunities"),
        generation=current_generation("opportunities"),
        required_fields=("schema_version", "symbol", "canonical_opportunity_id"),
        optional_fields=("observation_id", "entity_id", "correlation_id", "cycle_id",
                         "state", "dataset_version"),
        version_fields=("dataset_version",),
    ),
    "assessments": CanonicalProfile(
        dataset="assessments", schema_version=current_schema("assessments"),
        generation=current_generation("assessments"),
        required_fields=("schema_version", "symbol", "canonical_opportunity_id",
                         "entity_id", "correlation_id"),
        # decision_id minted downstream in decision_ledger — optional here.
        optional_fields=("decision_id", "cycle_id", "opportunity_id", "dataset_version"),
        version_fields=("dataset_version",),
    ),
    "decision_ledger": CanonicalProfile(
        dataset="decision_ledger", schema_version=current_schema("decision_ledger"),
        generation=current_generation("decision_ledger"),
        required_fields=("schema_version", "symbol", "decision", "decision_id",
                         "correlation_id", "observation_id"),
        # canonical root present only once minted (not for early guard rejects).
        optional_fields=("canonical_opportunity_id", "entity_id", "cycle_id"),
    ),
    "decision_trace": CanonicalProfile(
        dataset="decision_trace", schema_version=current_schema("decision_trace"),
        generation=current_generation("decision_trace"),
        required_fields=("schema_version", "symbol", "entity_id", "action"),
        optional_fields=("correlation_id", "decision_id", "cycle_id"),
    ),
    "execution_context": CanonicalProfile(
        dataset="execution_context", schema_version=current_schema("execution_context"),
        generation=current_generation("execution_context"),
        # Pre-engine snapshot: canonical root legitimately absent (never faked).
        required_fields=("schema_version", "symbol", "entity_id", "correlation_id"),
        optional_fields=("canonical_opportunity_id", "events_ref", "cycle_id"),
    ),
    "strategy_observations": CanonicalProfile(
        dataset="strategy_observations", schema_version=current_schema("strategy_observations"),
        generation=current_generation("strategy_observations"),
        required_fields=("schema_version", "symbol", "canonical_opportunity_id",
                         "observation_id", "entity_id"),
        optional_fields=("cycle_id",),
    ),
    "horizon_candidates": CanonicalProfile(
        dataset="horizon_candidates", schema_version=current_schema("horizon_candidates"),
        generation=current_generation("horizon_candidates"),
        required_fields=("schema_version", "symbol", "canonical_opportunity_id", "entity_id"),
    ),
    "strategy_candidates": CanonicalProfile(
        dataset="strategy_candidates", schema_version=current_schema("strategy_candidates"),
        generation=current_generation("strategy_candidates"),
        required_fields=("schema_version", "symbol"),
        optional_fields=("canonical_opportunity_id", "entity_id"),
    ),
    "shadow_runtime": CanonicalProfile(
        dataset="shadow_runtime", schema_version=current_schema("shadow_runtime"),
        generation=current_generation("shadow_runtime"),
        # Append-only event stream: canonical root is the authoritative key;
        # entity/correlation vary by event type (optional).
        required_fields=("schema_version", "symbol", "canonical_opportunity_id"),
        optional_fields=("entity_id", "correlation_id"),
    ),
    "portfolio_rankings": CanonicalProfile(
        dataset="portfolio_rankings", schema_version=current_schema("portfolio_rankings"),
        generation=current_generation("portfolio_rankings"),
        # Portfolio-wide / date-scoped: NO symbol; selected_symbol may be empty
        # when no candidate was eligible in the cycle.
        required_fields=("schema_version", "ranking_id", "cycle_id"),
        optional_fields=("selected_symbol", "dataset_version"),
        version_fields=("dataset_version",),
    ),
}


def has_profile(dataset: str) -> bool:
    return dataset in _PROFILES


def get_profile(dataset: str) -> CanonicalProfile:
    if dataset not in _PROFILES:
        raise KeyError(f"no canonical V1 profile registered for dataset '{dataset}'")
    return _PROFILES[dataset]


def registered_profiles() -> tuple[str, ...]:
    return tuple(_PROFILES)


def _empty(v: Any) -> bool:
    return v is None or v == "" or v == {} or v == []


def validate_record(dataset: str, record: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate a persisted record against its registered canonical V1 profile.

    Returns (ok, violations). A record may NOT claim a ``_v1`` schema unless a
    profile is registered and the record satisfies it. This is the hard invariant
    that prevents silent false ``_v1`` labelling.
    """
    violations: list[str] = []

    if dataset not in PRODUCTION_SCHEMA_REGISTRY:
        return False, [f"CANONICAL CONTRACT FAILURE: unknown dataset '{dataset}'"]
    if dataset not in _PROFILES:
        return False, [f"CANONICAL CONTRACT FAILURE: no registered V1 profile for '{dataset}'"]

    profile = _PROFILES[dataset]

    # Schema identity must equal the contract's canonical schema string.
    claimed = record.get("schema_version")
    if claimed != profile.schema_version:
        violations.append(
            f"schema_version mismatch: record={claimed!r} expected={profile.schema_version!r}"
        )
    # A record must not claim an unregistered *_v1.
    if isinstance(claimed, str) and claimed.endswith("_v1") and claimed != profile.schema_version:
        violations.append(f"unregistered _v1 claim: {claimed!r}")

    # Required fields present + non-empty.
    for f in profile.required_fields:
        if f not in record:
            violations.append(f"required field missing: {f}")
        elif _empty(record.get(f)):
            violations.append(f"required field empty: {f}")

    # Version-bearing fields must be clean-baseline generation 1.
    for f in profile.version_fields:
        if f in record and record.get(f) is not None:
            v = record.get(f)
            if v != 1 and v != profile.generation:
                violations.append(
                    f"CANONICAL CONTRACT FAILURE: {f}={v!r} > generation 1 (clean baseline)"
                )
    # event_layout_version specifically must be 1 on the clean baseline.
    if "event_layout_version" in record:
        elv = record.get("event_layout_version")
        if elv != 1:
            violations.append(
                f"CANONICAL CONTRACT FAILURE: event_layout_version={elv!r} (clean baseline requires 1)"
            )

    return (len(violations) == 0), violations
