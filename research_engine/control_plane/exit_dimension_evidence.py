"""Governed immutable OPEN dimensions for HD09 EX6-EX8 research.

This is a narrow companion projection to ``exit_bar_path_v1``.  It does not
change path or replay semantics.  Values are copied only from the authoritative
``shadow_runtime_v1`` OPEN ``live_facts`` object and are bound to the exact
eligible path record and its analytical digest.  Missing values remain missing;
there is no reconstruction or fallback.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from research_engine.control_plane.evidence_provenance import evidence_digest
from research_engine.control_plane.exit_bar_path import (
    GovernedExitBarPathEvidence,
    LifecyclePathSource,
)
from research_engine.registry.exit_policy_adjudication import (
    HD09_ADJUDICATED_CONTRACT,
    HD09_ADJUDICATION_VERSION,
    HETEROGENEITY_CONTRACT,
)

DIMENSION_EVIDENCE_SCHEMA_VERSION = "exit_dimension_evidence_v1"
STRATEGY_SOURCE = "shadow_runtime_v1 OPEN.live_facts.strategy"
REGIME_SOURCE = "shadow_runtime_v1 OPEN.live_facts.regime"
PATTERN_SOURCE = "shadow_runtime_v1 OPEN.live_facts.pattern"


@dataclass(frozen=True)
class ExitDimensionRecord:
    lifecycle_identity: tuple[str, str, str]
    canonical_opportunity_id: str
    trade_horizon: str
    strategy_family: str | None
    market_regime: str | None
    candlestick_pattern: str | None
    source_path_analytical_digest: str
    open_authority_digest: str
    record_digest: str

    def digest_material(self) -> dict[str, Any]:
        return {
            "schema_version": DIMENSION_EVIDENCE_SCHEMA_VERSION,
            "hd09_adjudication_version": HD09_ADJUDICATION_VERSION,
            "lifecycle_identity": list(self.lifecycle_identity),
            "canonical_opportunity_id": self.canonical_opportunity_id,
            "trade_horizon": self.trade_horizon,
            "strategy_family": self.strategy_family,
            "strategy_family_source": STRATEGY_SOURCE,
            "market_regime": self.market_regime,
            "market_regime_source": REGIME_SOURCE,
            "candlestick_pattern": self.candlestick_pattern,
            "candlestick_pattern_source": PATTERN_SOURCE,
            "source_path_analytical_digest": self.source_path_analytical_digest,
            "open_authority_digest": self.open_authority_digest,
        }

    def analytical_record(self) -> dict[str, Any]:
        value = self.digest_material()
        value["record_digest"] = self.record_digest
        return value


@dataclass(frozen=True)
class ExitDimensionEvidence:
    schema_version: str
    records: tuple[ExitDimensionRecord, ...]
    provenance: dict[str, Any]


def _identity(open_event: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(open_event.get("shadow_trade_id", "") or ""),
        str(open_event.get("canonical_opportunity_id", "") or ""),
        str(open_event.get("horizon", "") or ""),
    )


def _entry_value(live_facts: Mapping[str, Any], key: str) -> str | None:
    value = live_facts.get(key)
    return value if isinstance(value, str) and bool(value) else None


def dimension_record_digest(record: ExitDimensionRecord) -> str:
    return evidence_digest((record.digest_material(),))


def build_exit_dimension_evidence_v1(
    lifecycle_sources: Iterable[LifecyclePathSource],
    path: GovernedExitBarPathEvidence,
) -> ExitDimensionEvidence:
    """Project the three HD09-frozen OPEN dimensions without mutation."""
    if HD09_ADJUDICATED_CONTRACT.get("version") != HD09_ADJUDICATION_VERSION:
        raise ValueError("invalid HD09 authority")
    extension = str(HETEROGENEITY_CONTRACT.get("later_path_extension", ""))
    if "OPEN.live_facts" not in extension or "no inference or fallback" not in extension:
        raise ValueError("invalid HD09 dimension-extension authority")
    sources = tuple(lifecycle_sources)
    counts = Counter(_identity(source.open_event) for source in sources)
    source_by_identity: dict[tuple[str, str, str], LifecyclePathSource] = {}
    for source in sources:
        identity = _identity(source.open_event)
        if all(identity) and counts[identity] == 1:
            source_by_identity[identity] = source

    records: list[ExitDimensionRecord] = []
    for path_record in sorted(path.records, key=lambda item: item.lifecycle_identity):
        source = source_by_identity.get(path_record.lifecycle_identity)
        if source is None:
            raise ValueError("eligible path lacks unique authoritative OPEN source")
        opened = source.open_event
        live_facts = opened.get("live_facts")
        if not isinstance(live_facts, Mapping):
            live_facts = {}
        open_digest = evidence_digest((dict(opened),))
        material = {
            "schema_version": DIMENSION_EVIDENCE_SCHEMA_VERSION,
            "hd09_adjudication_version": HD09_ADJUDICATION_VERSION,
            "lifecycle_identity": list(path_record.lifecycle_identity),
            "canonical_opportunity_id": path_record.canonical_opportunity_id,
            "trade_horizon": path_record.trade_horizon,
            "strategy_family": _entry_value(live_facts, "strategy"),
            "strategy_family_source": STRATEGY_SOURCE,
            "market_regime": _entry_value(live_facts, "regime"),
            "market_regime_source": REGIME_SOURCE,
            "candlestick_pattern": _entry_value(live_facts, "pattern"),
            "candlestick_pattern_source": PATTERN_SOURCE,
            "source_path_analytical_digest": path_record.analytical_digest,
            "open_authority_digest": open_digest,
        }
        records.append(ExitDimensionRecord(
            lifecycle_identity=path_record.lifecycle_identity,
            canonical_opportunity_id=path_record.canonical_opportunity_id,
            trade_horizon=path_record.trade_horizon,
            strategy_family=material["strategy_family"],
            market_regime=material["market_regime"],
            candlestick_pattern=material["candlestick_pattern"],
            source_path_analytical_digest=path_record.analytical_digest,
            open_authority_digest=open_digest,
            record_digest=evidence_digest((material,)),
        ))
    provenance = {
        "schema_version": DIMENSION_EVIDENCE_SCHEMA_VERSION,
        "hd09_adjudication_version": HD09_ADJUDICATION_VERSION,
        "source_exit_bar_path_provenance_digest": path.provenance["digest"],
        "source_exit_bar_path_analytical_digest": path.provenance["analytical_digest"],
        "dimension_sources": {
            "strategy_family": STRATEGY_SOURCE,
            "market_regime": REGIME_SOURCE,
            "candlestick_pattern": PATTERN_SOURCE,
        },
        "fallbacks": [],
        "record_count": len(records),
        "records_digest": evidence_digest(item.analytical_record() for item in records),
        "digest_algorithm": "sha256",
    }
    provenance["digest"] = evidence_digest((provenance,))
    return ExitDimensionEvidence(
        schema_version=DIMENSION_EVIDENCE_SCHEMA_VERSION,
        records=tuple(records),
        provenance=provenance,
    )


def validate_exit_dimension_evidence(
    dimensions: ExitDimensionEvidence,
    path: GovernedExitBarPathEvidence,
) -> None:
    if dimensions.schema_version != DIMENSION_EVIDENCE_SCHEMA_VERSION:
        raise ValueError("invalid governed dimension evidence schema")
    material = dict(dimensions.provenance)
    supplied = material.pop("digest", None)
    if supplied != evidence_digest((material,)):
        raise ValueError("invalid governed dimension provenance digest")
    if dimensions.provenance.get("source_exit_bar_path_provenance_digest") != path.provenance["digest"]:
        raise ValueError("dimension/path provenance mismatch")
    if dimensions.provenance.get("records_digest") != evidence_digest(
        item.analytical_record() for item in dimensions.records
    ):
        raise ValueError("dimension analytical population digest mismatch")
    path_index = {item.lifecycle_identity: item for item in path.records}
    if len(path_index) != len(path.records) or len(dimensions.records) != len(path.records):
        raise ValueError("dimension/path population cardinality mismatch")
    for record in dimensions.records:
        path_record = path_index.get(record.lifecycle_identity)
        if (
            path_record is None
            or record.source_path_analytical_digest != path_record.analytical_digest
            or record.canonical_opportunity_id != path_record.canonical_opportunity_id
            or record.trade_horizon != path_record.trade_horizon
            or dimension_record_digest(record) != record.record_digest
        ):
            raise ValueError("invalid governed dimension record binding")
