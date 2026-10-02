"""Single governed handoff from local Production V1 writers to the outbox.

Writers call this only after their existing local persistence succeeds.  The
adapter never performs network I/O, never acknowledges delivery, and never
changes trading decisions.  A failed durable handoff is logged loudly, linked
to an existing lifecycle obligation when one exists, and re-raised for the
writer to map onto its established return semantics.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import logging
import os
from pathlib import Path
import threading
from typing import Any, Mapping, Sequence

from core.canonical_delivery_outbox import (
    CanonicalDeliveryOutbox,
    DEFAULT_OUTBOX_PATH,
    EnqueueResult,
    governed_identity,
)
from core.production_data_contract import PRODUCTION_SCHEMA_REGISTRY

logger = logging.getLogger(__name__)


class MigrationClassification(str, Enum):
    OUTBOX_MIGRATED = "OUTBOX_MIGRATED"
    READ_ONLY_LEGACY = "READ_ONLY_LEGACY"
    DERIVED_NON_LIVE = "DERIVED_NON_LIVE"
    EXPLICIT_OPERATIONAL_EXEMPTION = "EXPLICIT_OPERATIONAL_EXEMPTION"


# Machine-checkable and intentionally exhaustive. Authority/disposition remains
# owned by lifecycle_evidence_obligations.DATASET_DISPOSITIONS; transport does
# not promote legacy or derived datasets.
CANONICAL_DELIVERY_MIGRATION: dict[str, MigrationClassification] = {
    dataset: MigrationClassification.OUTBOX_MIGRATED
    for dataset in PRODUCTION_SCHEMA_REGISTRY
}

# Audited live producer modules. A tuple preserves legitimate multiple-producer
# datasets without merging their record grains. Tests verify every named module
# contains the governed handoff and no canonical producer retains a direct PUT.
CANONICAL_WRITER_MODULES: dict[str, tuple[str, ...]] = {
    "events": ("core.event_stream",),
    "market_context": ("core.market_context.persistence",),
    "opportunities": (
        "core.opportunity.persistence", "core.persistence.opportunity_writer",
    ),
    "assessments": ("core.assessment.persistence",),
    "decision_ledger": ("core.decision_ledger",),
    "execution_results": ("core.persistence.execution_result_writer",),
    "trade_truth": ("core.trade_truth",),
    "strategy_candidates": ("core.persistence.strategy_candidates_writer",),
    "horizon_candidates": ("core.persistence.horizon_candidates_writer",),
    "decision_trace": ("core.decision_trace",),
    "execution_context": ("core.execution_context",),
    "execution_attempts": ("core.persistence.execution_attempts_writer",),
    "protection_audit": ("core.protection_verification",),
    "management_actions": ("core.persistence.management_actions_writer",),
    "risk_deviation": ("core.risk_deviation",),
    "portfolio_rankings": ("core.portfolio_ranking.persistence",),
    "shadow_runtime": ("core.shadow.persistence",),
    "shadow_trades": ("core.shadow_trades",),
    "strategy_observations": ("core.strategies.observation_persistence",),
    "research_shadow_trades": ("core.research_assessment.research_shadow_engine",),
    "trade_journal": ("core.trade_journal",),
    "portfolio_shadow": ("core.portfolio_ranking.shadow_comparison",),
    "quarantine": ("core.contracts.quarantine",),
}


class CanonicalDeliveryHandoffError(RuntimeError):
    """Local evidence exists but its durable canonical handoff failed."""


@dataclass(frozen=True)
class BatchEnqueueResult:
    results: tuple[EnqueueResult, ...]

    @property
    def logical_delivery_count(self) -> int:
        return len({item.record.outbox_id for item in self.results})


_OUTBOX: CanonicalDeliveryOutbox | None = None
_OUTBOX_PATH_OVERRIDE: Path | None = None
_LOCK = threading.Lock()


def configure_delivery_outbox(path: str | Path | None) -> None:
    """Replace the process singleton path; primarily a deployment/test hook."""
    global _OUTBOX, _OUTBOX_PATH_OVERRIDE
    with _LOCK:
        if _OUTBOX is not None:
            _OUTBOX.close()
        _OUTBOX = None
        _OUTBOX_PATH_OVERRIDE = Path(path) if path is not None else None


def get_delivery_outbox() -> CanonicalDeliveryOutbox:
    global _OUTBOX
    with _LOCK:
        if _OUTBOX is None:
            configured = os.getenv("CANONICAL_DELIVERY_OUTBOX_PATH")
            path = _OUTBOX_PATH_OVERRIDE or (Path(configured) if configured else DEFAULT_OUTBOX_PATH)
            _OUTBOX = CanonicalDeliveryOutbox(path)
        return _OUTBOX


def _existing_obligation_id(dataset: str, identity: Mapping[str, Any]) -> str | None:
    try:
        from core.lifecycle_evidence_obligations import obligation_ledger
        matches = obligation_ledger().find_exact(dataset, identity)
        if len(matches) == 1:
            return matches[0].obligation_id
        if len(matches) > 1:
            logger.error(
                "[CANONICAL_OUTBOX_OBLIGATION_AMBIGUOUS] dataset=%s count=%d",
                dataset, len(matches),
            )
    except Exception as exc:
        logger.error(
            "[CANONICAL_OUTBOX_OBLIGATION_LOOKUP_FAILED] dataset=%s error=%s",
            dataset, type(exc).__name__,
        )
    return None


def _record_handoff_failure(obligation_id: str | None, dataset: str, exc: Exception) -> None:
    logger.critical(
        "[CANONICAL_OUTBOX_HANDOFF_FAILED] dataset=%s obligation=%s error=%s",
        dataset, obligation_id or "NONE", type(exc).__name__,
    )
    try:
        from core.s3_write_observability import record_s3_failure
        record_s3_failure(dataset, exc)
    except Exception:
        pass
    if not obligation_id:
        return
    try:
        from core.lifecycle_evidence_obligations import (
            ObligationStatus, obligation_ledger,
        )
        ledger = obligation_ledger()
        if ledger.get(obligation_id) is not None:
            ledger.update(
                obligation_id,
                ObligationStatus.PRODUCER_FAILED,
                failure_reason=f"CANONICAL_OUTBOX_HANDOFF_FAILED:{type(exc).__name__}",
                provenance={
                    "producer_write": "LOCAL_FSYNC_SUCCEEDED",
                    "canonical_delivery_handoff": "FAILED",
                },
            )
    except Exception as lifecycle_exc:
        logger.error(
            "[CANONICAL_OUTBOX_HANDOFF_LIFECYCLE_UPDATE_FAILED] dataset=%s error=%s",
            dataset, type(lifecycle_exc).__name__,
        )


def enqueue_canonical_delivery(
    *,
    dataset: str,
    payload: Mapping[str, Any],
    symbol: str,
    partition_date: str,
    identity: Mapping[str, Any] | None = None,
    lifecycle_obligation_id: str | None = None,
    outbox: CanonicalDeliveryOutbox | None = None,
) -> EnqueueResult:
    """Durably enqueue one already-locally-persisted canonical record."""
    classification = CANONICAL_DELIVERY_MIGRATION.get(dataset)
    if classification is None:
        raise CanonicalDeliveryHandoffError(f"UNCLASSIFIED_DATASET:{dataset}")
    if classification is not MigrationClassification.OUTBOX_MIGRATED:
        raise CanonicalDeliveryHandoffError(
            f"DATASET_NOT_OUTBOX_MIGRATED:{dataset}:{classification.value}"
        )
    try:
        exact_identity = governed_identity(dataset, payload, identity)
        obligation_id = lifecycle_obligation_id or _existing_obligation_id(
            dataset, exact_identity,
        )
        return (outbox or get_delivery_outbox()).enqueue(
            dataset=dataset,
            payload=payload,
            symbol=symbol,
            partition_date=partition_date,
            identity=exact_identity,
            lifecycle_obligation_id=obligation_id,
        )
    except Exception as exc:
        obligation_id = lifecycle_obligation_id
        if obligation_id is None:
            try:
                obligation_id = _existing_obligation_id(
                    dataset, governed_identity(dataset, payload, identity),
                )
            except Exception:
                obligation_id = None
        _record_handoff_failure(obligation_id, dataset, exc)
        raise CanonicalDeliveryHandoffError(
            f"CANONICAL_OUTBOX_HANDOFF_FAILED:{dataset}:{type(exc).__name__}"
        ) from exc


def enqueue_canonical_batch(
    *,
    dataset: str,
    payloads: Sequence[Mapping[str, Any]],
    symbol: str,
    partition_date: str,
    outbox: CanonicalDeliveryOutbox | None = None,
) -> BatchEnqueueResult:
    """Enqueue a producer batch as deterministic per-record obligations."""
    target = outbox or get_delivery_outbox()
    results = tuple(
        enqueue_canonical_delivery(
            dataset=dataset,
            payload=payload,
            symbol=symbol,
            partition_date=partition_date,
            outbox=target,
        )
        for payload in payloads
    )
    return BatchEnqueueResult(results)


def enqueue_canonical_jsonl(
    *, dataset: str, content: str, partition_date: str, symbol: str = "",
) -> BatchEnqueueResult:
    """Compatibility bridge for existing JSONL writer helpers."""
    try:
        payloads = [json.loads(line) for line in content.splitlines() if line.strip()]
    except json.JSONDecodeError as exc:
        raise CanonicalDeliveryHandoffError(
            f"CANONICAL_JSONL_INVALID:{dataset}:{exc.msg}"
        ) from exc
    if not payloads:
        return BatchEnqueueResult(())
    if not all(isinstance(payload, dict) for payload in payloads):
        raise CanonicalDeliveryHandoffError(f"CANONICAL_JSONL_OBJECT_REQUIRED:{dataset}")
    return enqueue_canonical_batch(
        dataset=dataset, payloads=payloads, symbol=symbol,
        partition_date=partition_date,
    )


def validate_migration_coverage() -> None:
    active = set(PRODUCTION_SCHEMA_REGISTRY)
    classified = set(CANONICAL_DELIVERY_MIGRATION)
    if classified != active:
        raise RuntimeError(
            f"CANONICAL_DELIVERY_MIGRATION_COVERAGE_MISMATCH:"
            f"missing={sorted(active-classified)}:extra={sorted(classified-active)}"
        )
    allowed = set(MigrationClassification)
    invalid = {dataset: value for dataset, value in CANONICAL_DELIVERY_MIGRATION.items()
               if value not in allowed}
    if invalid:
        raise RuntimeError(f"INVALID_MIGRATION_CLASSIFICATION:{invalid}")
    writer_datasets = set(CANONICAL_WRITER_MODULES)
    if writer_datasets != active or any(
        not modules for modules in CANONICAL_WRITER_MODULES.values()
    ):
        raise RuntimeError(
            "CANONICAL_WRITER_COVERAGE_MISMATCH:"
            f"missing={sorted(active-writer_datasets)}:"
            f"extra={sorted(writer_datasets-active)}"
        )


validate_migration_coverage()


__all__ = [
    "BatchEnqueueResult",
    "CANONICAL_DELIVERY_MIGRATION",
    "CANONICAL_WRITER_MODULES",
    "CanonicalDeliveryHandoffError",
    "MigrationClassification",
    "configure_delivery_outbox",
    "enqueue_canonical_batch",
    "enqueue_canonical_delivery",
    "enqueue_canonical_jsonl",
    "get_delivery_outbox",
    "validate_migration_coverage",
]
