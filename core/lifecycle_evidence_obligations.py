"""Durable lifecycle evidence obligations and exact-identity reconciliation.

This runtime ledger records what evidence a lifecycle event is expected to
produce. It does not replace canonical datasets, change trading decisions, or
claim S3 durability: producer observations and persistence scope are retained
in provenance so a later outbox/reconciler can verify the canonical mirror.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import logging
import os
from pathlib import Path
import threading
from typing import Any, Iterable, Mapping, Sequence

from core.production_data_contract import current_schema

logger = logging.getLogger(__name__)

DEFAULT_LEDGER_PATH = Path("logs/lifecycle_evidence_obligations.jsonl")
ACCOUNT_SCOPED_DATASETS = frozenset({
    "execution_results", "execution_attempts", "protection_audit",
    "management_actions", "risk_deviation", "trade_truth", "trade_journal",
    "account_snapshots", "position_snapshots", "account_open_risk",
    # Block 2C: portfolio/correlation exposure is ACCOUNT-SCOPED per-account
    # telemetry (grains A and B). The cross-account aggregate (grain C) is NOT
    # in this set: it is explicitly multi-account by contract and carries its
    # own aggregate identity instead of an account identity.
    "portfolio_exposure", "correlation_exposure",
})


class ObligationStatus(str, Enum):
    PRESENT = "PRESENT"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    NOT_YET_DUE = "NOT_YET_DUE"
    EXPECTED_BUT_MISSING = "EXPECTED_BUT_MISSING"
    AMBIGUOUS = "AMBIGUOUS"
    PRODUCER_FAILED = "PRODUCER_FAILED"


STATUS_TRANSITIONS: dict[ObligationStatus, frozenset[ObligationStatus]] = {
    ObligationStatus.PRESENT: frozenset({
        ObligationStatus.PRESENT, ObligationStatus.AMBIGUOUS,
        ObligationStatus.EXPECTED_BUT_MISSING,
    }),
    ObligationStatus.NOT_APPLICABLE: frozenset({ObligationStatus.NOT_APPLICABLE}),
    ObligationStatus.NOT_YET_DUE: frozenset({
        ObligationStatus.NOT_YET_DUE, ObligationStatus.PRESENT,
        ObligationStatus.NOT_APPLICABLE, ObligationStatus.EXPECTED_BUT_MISSING,
        ObligationStatus.AMBIGUOUS, ObligationStatus.PRODUCER_FAILED,
    }),
    ObligationStatus.EXPECTED_BUT_MISSING: frozenset({
        ObligationStatus.EXPECTED_BUT_MISSING, ObligationStatus.PRESENT,
        ObligationStatus.AMBIGUOUS, ObligationStatus.PRODUCER_FAILED,
    }),
    ObligationStatus.AMBIGUOUS: frozenset({ObligationStatus.AMBIGUOUS}),
    ObligationStatus.PRODUCER_FAILED: frozenset({
        ObligationStatus.PRODUCER_FAILED, ObligationStatus.PRESENT,
        ObligationStatus.AMBIGUOUS, ObligationStatus.EXPECTED_BUT_MISSING,
    }),
}


# Exact governed join identity per dataset. Paths are record paths, not
# timestamp fallbacks. Multi-account evidence includes account_id by contract.
EXACT_IDENTITY_FIELDS: dict[str, tuple[str, ...]] = {
    "events": ("ts_utc_ms", "type", "symbol"),
    "market_context": ("entity_id",),
    "opportunities": ("opportunity_record_id",),
    "assessments": ("assessment_id",),
    "decision_ledger": ("decision_id",),
    "decision_trace": ("entity_id", "cycle_id", "runtime_session_id"),
    "execution_context": ("correlation_id",),
    "strategy_observations": ("entity_id", "observation_id"),
    "horizon_candidates": ("candidate_id",),
    "strategy_candidates": ("candidate_id",),
    "execution_results": ("correlation_id", "account_id"),
    "execution_attempts": ("correlation_id", "account_id", "attempt_id"),
    "protection_audit": ("correlation_id", "account_id", "position_ticket"),
    "management_actions": ("management_action_id", "account_id", "position_ticket"),
    "risk_deviation": ("trade_id", "account_id"),
    # Account state is identified by the exact account plus the deterministic
    # observation identity (account + observation instant + source). Monetary
    # floats are never part of this identity.
    "account_snapshots": ("account_id", "snapshot_id"),
    # Block 2B position telemetry. A position row is identified by the exact
    # account plus its deterministic per-observation identity; a position-SET
    # boundary row is identified by the exact account plus the observation-cycle
    # id it closes. Monetary risk is NEVER part of identity.
    "position_snapshots": ("account_id", "position_snapshot_id"),
    "account_open_risk": ("account_id", "open_risk_id"),
    # Block 2C portfolio / correlation exposure. Each row is identified by the
    # exact account plus a deterministic per-observation identity that already
    # embeds the exact 2B observation lineage AND the exact correlation model
    # version. Monetary risk is NEVER part of identity.
    "portfolio_exposure": ("account_id", "portfolio_exposure_id"),
    "correlation_exposure": ("account_id", "cluster_exposure_id"),
    # Grain C is a DIFFERENT grain: an explicitly requested multi-account
    # aggregate identified by its OWN aggregate identity, never by one account.
    "cross_account_portfolio_exposure": ("cross_account_exposure_id",),
    "trade_truth": ("trade_id", "account_id"),
    "shadow_runtime": ("event_id",),
    "shadow_trades": ("trade_id", "event_type"),
    "portfolio_rankings": ("ranking_id",),
    "portfolio_shadow": ("cycle_id", "runtime_session_id"),
    "research_shadow_trades": ("trade_id", "event_type"),
    "trade_journal": ("trade_id", "account_id"),
    "quarantine": ("record_id",),
}
IDENTITY_FIELD_PATHS: dict[str, dict[str, str]] = {
    "trade_truth": {"trade_id": "identity.trade_id", "account_id": "identity.account_id"},
    "shadow_trades": {"trade_id": "identity.trade_id"},
    "research_shadow_trades": {"trade_id": "identity.trade_id"},
}


# Every active Production V1 dataset has an explicit evidence/authority
# disposition. Tests compare this map with the canonical registry.
DATASET_DISPOSITIONS: dict[str, dict[str, str]] = {
    "events": {"class": "D_OPERATIONAL", "policy": "TELEMETRY_ONLY"},
    "market_context": {"class": "B_LIVE_CONDITIONAL", "policy": "MATERIAL_CHANGE"},
    "opportunities": {"class": "B_LIVE_CONDITIONAL", "policy": "CANONICAL_OPPORTUNITY"},
    "assessments": {"class": "B_LIVE_CONDITIONAL", "policy": "SCORING_REACHED"},
    "decision_ledger": {"class": "A_LIVE_REQUIRED", "policy": "TERMINAL_DECISION"},
    "execution_results": {"class": "B_LIVE_CONDITIONAL", "policy": "PER_ACCOUNT_ROUTE"},
    "trade_truth": {"class": "A_LIVE_REQUIRED", "policy": "CLOSED_TRADE"},
    "strategy_candidates": {"class": "B_LIVE_CONDITIONAL", "policy": "CANDIDATE_EVALUATED"},
    "horizon_candidates": {"class": "B_LIVE_CONDITIONAL", "policy": "HORIZON_CLASSIFIER_REACHED"},
    "decision_trace": {"class": "B_LIVE_CONDITIONAL", "policy": "OBSERVER_REACHED"},
    "execution_context": {"class": "A_LIVE_REQUIRED", "policy": "ACTIVE_CYCLE"},
    "execution_attempts": {"class": "B_LIVE_CONDITIONAL", "policy": "ORDER_SEND"},
    "protection_audit": {"class": "B_LIVE_CONDITIONAL", "policy": "FILLED_POSITION"},
    "management_actions": {"class": "B_LIVE_CONDITIONAL", "policy": "ACTION_INITIATED"},
    "risk_deviation": {"class": "B_LIVE_CONDITIONAL", "policy": "VALID_RISK_GEOMETRY"},
    "account_snapshots": {"class": "B_LIVE_CONDITIONAL", "policy": "ACCOUNT_STATE_OBSERVATION"},
    "position_snapshots": {"class": "B_LIVE_CONDITIONAL", "policy": "OPEN_POSITION_OBSERVATION"},
    "account_open_risk": {"class": "B_LIVE_CONDITIONAL", "policy": "OPEN_RISK_OBSERVATION"},
    "portfolio_exposure": {"class": "B_LIVE_CONDITIONAL", "policy": "PORTFOLIO_EXPOSURE_OBSERVATION"},
    "correlation_exposure": {"class": "B_LIVE_CONDITIONAL", "policy": "CORRELATION_EXPOSURE_OBSERVATION"},
    "cross_account_portfolio_exposure": {"class": "B_LIVE_CONDITIONAL", "policy": "CROSS_ACCOUNT_EXPOSURE_REQUESTED"},
    "portfolio_rankings": {"class": "B_LIVE_CONDITIONAL", "policy": "RANKING_POOL_EXISTS"},
    "shadow_runtime": {"class": "B_LIVE_CONDITIONAL", "policy": "SHADOW_LIFECYCLE_EVENT"},
    "shadow_trades": {"class": "E_LEGACY_NON_AUTHORITY", "policy": "NEVER_SATISFIES_SHADOW_RUNTIME"},
    "strategy_observations": {"class": "B_LIVE_CONDITIONAL", "policy": "STRATEGY_OBSERVER_REACHED"},
    "research_shadow_trades": {"class": "C_DERIVED_RESEARCH", "policy": "NEVER_SATISFIES_LIVE_EVIDENCE"},
    "trade_journal": {"class": "B_LIVE_CONDITIONAL", "policy": "CLOSED_TRADE_JOURNAL_ENABLED"},
    "portfolio_shadow": {"class": "B_LIVE_CONDITIONAL", "policy": "COMPARISON_ENABLED"},
    "quarantine": {"class": "D_OPERATIONAL", "policy": "CONTRACT_REJECTION_ONLY"},
}


LIFECYCLE_CONTRACT: dict[str, dict[str, str]] = {
    "ACTIVE_CYCLE": {
        "execution_context": "EXPECTED_NOW",
        "market_context": "CONDITIONALLY_EXPECTED_MATERIAL_CHANGE",
    },
    "OPPORTUNITY_EVALUATED": {
        "opportunities": "CONDITIONALLY_EXPECTED_IF_CANONICAL_OPPORTUNITY",
        "assessments": "CONDITIONALLY_EXPECTED_IF_SCORING_REACHED",
        "strategy_observations": "CONDITIONALLY_EXPECTED_IF_STRATEGY_OBSERVER_REACHED",
        "strategy_candidates": "CONDITIONALLY_EXPECTED_IF_STRATEGY_CANDIDATES_EXIST",
        "horizon_candidates": "CONDITIONALLY_EXPECTED_IF_HORIZON_CLASSIFIER_REACHED",
    },
    "TERMINAL_NO_TRADE": {
        "decision_ledger": "EXPECTED_NOW",
        "decision_trace": "CONDITIONALLY_EXPECTED_OBSERVER_REACHED",
        "execution_attempts": "NOT_APPLICABLE_NO_ORDER_SEND",
        "execution_results": "NOT_APPLICABLE_NO_ACCOUNT_ROUTE",
        "protection_audit": "NOT_APPLICABLE_NO_FILL",
        "trade_truth": "NOT_APPLICABLE_NO_CLOSED_TRADE",
    },
    "ACCOUNT_EXECUTION_BRANCH": {
        "execution_results": "EXPECTED_NOW_PER_ROUTED_ACCOUNT",
        "execution_attempts": "EXPECTED_ONLY_IF_ORDER_SEND_OCCURRED",
    },
    "FILLED_POSITION": {
        "protection_audit": "EXPECTED_NOW_IF_POSITION_TICKET",
        "management_actions": "CONDITIONALLY_EXPECTED_WHEN_ACTION_INITIATED",
        "trade_truth": "EXPECTED_LATER_ON_CLOSE",
        "risk_deviation": "EXPECTED_LATER_AFTER_CLOSED_OUTCOME",
    },
    "CLOSED_TRADE": {
        "trade_truth": "EXPECTED_NOW",
        "trade_journal": "CONDITIONALLY_EXPECTED_IF_JOURNAL_ENABLED",
        "risk_deviation": "CONDITIONALLY_EXPECTED_IF_RISK_GEOMETRY_VALID",
        "management_actions": "CONDITIONALLY_EXPECTED_IF_FINAL_ACTION_INITIATED",
    },
    "SHADOW_ELIGIBLE": {"shadow_runtime": "CONDITIONALLY_EXPECTED_IF_SHADOW_ENABLED"},
    "PORTFOLIO_CYCLE": {
        "portfolio_rankings": "CONDITIONALLY_EXPECTED_IF_RANKING_POOL_EXISTS",
        "portfolio_shadow": "CONDITIONALLY_EXPECTED_IF_COMPARISON_ENABLED",
    },
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, default=str)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _value(record: Mapping[str, Any], path: str) -> Any:
    value: Any = record
    for part in path.split("."):
        if not isinstance(value, Mapping):
            return None
        value = value.get(part)
    return value


def exact_identity_key(
    dataset: str, identity: Mapping[str, Any], *, require_account: bool = True,
) -> tuple[str, dict[str, Any]]:
    """Return dataset contract key and the non-empty values available for it."""
    fields = EXACT_IDENTITY_FIELDS.get(str(dataset))
    if fields is None:
        raise ValueError(f"NO_GOVERNED_IDENTITY_CONTRACT:{dataset}")
    values: dict[str, Any] = {}
    for name in fields:
        value = identity.get(name)
        if value not in (None, "", 0, "0"):
            values[name] = value
    if require_account and str(dataset) in ACCOUNT_SCOPED_DATASETS \
            and identity.get("account_id") in (None, "", 0, "0"):
        raise ValueError(f"ACCOUNT_ID_REQUIRED:{dataset}")
    return "+".join(fields), values


@dataclass(frozen=True)
class EvidenceObligation:
    obligation_id: str
    lifecycle_event_id: str
    lifecycle_stage: str
    expected_dataset: str
    expected_schema_version: str
    canonical_opportunity_id: str | None
    correlation_id: str | None
    decision_id: str | None
    entity_id: str | None
    trade_id: str | None
    account_id: str | None
    broker: str | None
    broker_server: str | None
    position_ticket: int | None
    symbol: str
    cycle_id: int | None
    originating_timestamp: str
    due_state: str
    due_after: str
    requirement_type: str
    current_status: str
    producer: str
    producer_trigger: str
    expected_identity_key: str
    expected_identity: Mapping[str, Any]
    observed_record_id: str | None
    failure_reason: str | None
    created_at: str
    updated_at: str
    provenance: Mapping[str, Any]
    revision: int = 1
    previous_status: str | None = None

    def __post_init__(self) -> None:
        if not self.obligation_id or not self.lifecycle_event_id or not self.lifecycle_stage:
            raise ValueError("OBLIGATION_IDENTITY_REQUIRED")
        if self.expected_dataset not in EXACT_IDENTITY_FIELDS:
            raise ValueError(f"UNKNOWN_OBLIGATION_DATASET:{self.expected_dataset}")
        if self.expected_schema_version != current_schema(self.expected_dataset):
            raise ValueError(f"OBLIGATION_SCHEMA_MISMATCH:{self.expected_dataset}")
        if self.current_status not in {item.value for item in ObligationStatus}:
            raise ValueError("INVALID_OBLIGATION_STATUS")
        if self.requirement_type not in {"REQUIRED", "CONDITIONAL"}:
            raise ValueError("INVALID_REQUIREMENT_TYPE")
        if self.current_status not in {ObligationStatus.NOT_APPLICABLE.value,
                           ObligationStatus.PRODUCER_FAILED.value} \
            and self.expected_dataset in ACCOUNT_SCOPED_DATASETS and not self.account_id:
            raise ValueError(f"ACCOUNT_ID_REQUIRED:{self.expected_dataset}")
        if not self.symbol:
            raise ValueError("OBLIGATION_SYMBOL_REQUIRED")
        if self.revision < 1:
            raise ValueError("INVALID_OBLIGATION_REVISION")

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "provenance": dict(self.provenance),
            "expected_identity": dict(self.expected_identity),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvidenceObligation":
        return cls(**dict(value))


class LifecycleEvidenceLedger:
    """Append-only JSONL obligation history; latest revision survives restart."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_LEDGER_PATH
        self._lock = threading.RLock()
        self._latest: dict[str, EvidenceObligation] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                obligation = EvidenceObligation.from_dict(json.loads(line))
                existing = self._latest.get(obligation.obligation_id)
                if existing is not None:
                    if obligation.revision != existing.revision + 1 \
                            or obligation.previous_status != existing.current_status:
                        raise ValueError("OBLIGATION_HISTORY_REVISION_GAP")
                elif obligation.revision != 1:
                    raise ValueError("OBLIGATION_HISTORY_MISSING_INITIAL_REVISION")
                self._latest[obligation.obligation_id] = obligation
        except Exception as exc:
            raise ValueError(f"OBLIGATION_LEDGER_CORRUPT:{self.path}") from exc

    @staticmethod
    def obligation_id(
        *, lifecycle_event_id: str, expected_dataset: str,
        expected_identity_key: str, expected_identity: Mapping[str, Any],
    ) -> str:
        return "EOB-" + _sha256({
            "lifecycle_event_id": lifecycle_event_id,
            "expected_dataset": expected_dataset,
            "expected_identity_key": expected_identity_key,
            "expected_identity": dict(expected_identity),
        })[:32].upper()

    def create(
        self,
        *, lifecycle_event_id: str, lifecycle_stage: str,
        expected_dataset: str, identity: Mapping[str, Any],
        originating_timestamp: str, due_state: str, due_after: str,
        requirement_type: str, current_status: ObligationStatus | str,
        producer: str, producer_trigger: str,
        observed_record_id: str | None = None,
        failure_reason: str | None = None,
        provenance: Mapping[str, Any] | None = None,
    ) -> EvidenceObligation:
        initial_status = ObligationStatus(current_status)
        key, expected_identity = exact_identity_key(
            expected_dataset, identity,
            require_account=initial_status not in {
                ObligationStatus.NOT_APPLICABLE, ObligationStatus.PRODUCER_FAILED,
            },
        )
        required_fields = EXACT_IDENTITY_FIELDS[expected_dataset]
        missing_key_fields = [name for name in required_fields
                              if identity.get(name) in (None, "", 0, "0")]
        if initial_status not in {ObligationStatus.NOT_APPLICABLE,
                      ObligationStatus.PRODUCER_FAILED} \
            and missing_key_fields:
            raise ValueError(
                f"EXACT_IDENTITY_INCOMPLETE:{expected_dataset}:"
                + ",".join(missing_key_fields))
        event_identity = dict(identity)
        account_id = event_identity.get("account_id")
        obligation_id = self.obligation_id(
            lifecycle_event_id=lifecycle_event_id,
            expected_dataset=expected_dataset,
            expected_identity_key=key,
            expected_identity=expected_identity,
        )
        now = _utc_now()
        obligation = EvidenceObligation(
            obligation_id=obligation_id,
            lifecycle_event_id=lifecycle_event_id,
            lifecycle_stage=lifecycle_stage,
            expected_dataset=expected_dataset,
            expected_schema_version=current_schema(expected_dataset),
            canonical_opportunity_id=event_identity.get("canonical_opportunity_id"),
            correlation_id=event_identity.get("correlation_id"),
            decision_id=event_identity.get("decision_id"),
            entity_id=event_identity.get("entity_id"),
            trade_id=event_identity.get("trade_id"),
            account_id=account_id,
            broker=event_identity.get("broker"),
            broker_server=event_identity.get("broker_server"),
            position_ticket=(int(event_identity["position_ticket"])
                             if event_identity.get("position_ticket") not in (None, "", 0, "0")
                             else None),
            symbol=str(event_identity.get("symbol") or ""),
            cycle_id=(int(event_identity["cycle_id"])
                      if event_identity.get("cycle_id") not in (None, "", 0, "0")
                      else None),
            originating_timestamp=str(originating_timestamp or ""),
            due_state=str(due_state), due_after=str(due_after),
            requirement_type=str(requirement_type),
            current_status=initial_status.value,
            producer=str(producer), producer_trigger=str(producer_trigger),
            expected_identity_key=key, expected_identity=expected_identity,
            observed_record_id=observed_record_id, failure_reason=failure_reason,
            created_at=now, updated_at=now,
            provenance=dict(provenance or {}),
        )
        with self._lock:
            existing = self._latest.get(obligation_id)
            if existing is not None:
                if self._immutable_material(existing) != self._immutable_material(obligation):
                    raise ValueError("OBLIGATION_IDENTITY_CONFLICT")
                return existing
            self._append(obligation)
            self._latest[obligation_id] = obligation
        return obligation

    @staticmethod
    def _immutable_material(item: EvidenceObligation) -> dict[str, Any]:
        value = item.to_dict()
        for key in ("current_status", "observed_record_id", "failure_reason",
                    "originating_timestamp", "created_at", "updated_at",
                    "revision", "previous_status"):
            value.pop(key, None)
        return value

    def update(
        self, obligation_id: str, status: ObligationStatus | str, *,
        observed_record_id: str | None = None, failure_reason: str | None = None,
        provenance: Mapping[str, Any] | None = None,
    ) -> EvidenceObligation:
        new_status = ObligationStatus(status)
        with self._lock:
            existing = self._latest.get(obligation_id)
            if existing is None:
                raise KeyError(f"UNKNOWN_OBLIGATION:{obligation_id}")
            old_status = ObligationStatus(existing.current_status)
            if new_status not in STATUS_TRANSITIONS[old_status]:
                raise ValueError(f"INVALID_OBLIGATION_TRANSITION:{old_status.value}:{new_status.value}")
            updated = replace(
                existing,
                current_status=new_status.value,
                observed_record_id=observed_record_id or existing.observed_record_id,
                failure_reason=failure_reason,
                updated_at=_utc_now(), revision=existing.revision + 1,
                previous_status=old_status.value,
                provenance={**dict(existing.provenance), **dict(provenance or {})},
            )
            self._append(updated)
            self._latest[obligation_id] = updated
            return updated

    def _append(self, obligation: EvidenceObligation) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            encoded = (_canonical_json(obligation.to_dict()) + "\n").encode("utf-8")
            fd = os.open(str(self.path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                written = os.write(fd, encoded)
                if written != len(encoded):
                    raise OSError("SHORT_OBLIGATION_LEDGER_WRITE")
                os.fsync(fd)
            finally:
                os.close(fd)
        except Exception as exc:
            logger.critical(
                "[LIFECYCLE_EVIDENCE_LEDGER_WRITE_FAILED] obligation=%s dataset=%s error=%s",
                obligation.obligation_id, obligation.expected_dataset, type(exc).__name__,
            )
            raise

    def get(self, obligation_id: str) -> EvidenceObligation | None:
        return self._latest.get(obligation_id)

    def obligations(self, *, status: ObligationStatus | str | None = None) -> tuple[EvidenceObligation, ...]:
        selected = tuple(self._latest[key] for key in sorted(self._latest))
        if status is None:
            return selected
        wanted = ObligationStatus(status).value
        return tuple(item for item in selected if item.current_status == wanted)

    def find_exact(
        self, dataset: str, identity: Mapping[str, Any],
    ) -> tuple[EvidenceObligation, ...]:
        key, values = exact_identity_key(dataset, identity)
        return tuple(item for item in self.obligations()
                     if item.expected_dataset == dataset
                     and item.expected_identity_key == key
                     and dict(item.expected_identity) == values)


def reconcile_obligation(
    ledger: LifecycleEvidenceLedger, obligation: EvidenceObligation,
    evidence: Sequence[Mapping[str, Any]], *, due: bool,
    producer_failed: bool = False, failure_reason: str | None = None,
) -> EvidenceObligation:
    """Reconcile exact identity only; never use timestamp proximity or fallback keys."""
    if obligation.current_status == ObligationStatus.NOT_APPLICABLE.value:
        return obligation
    if producer_failed:
        return ledger.update(
            obligation.obligation_id, ObligationStatus.PRODUCER_FAILED,
            failure_reason=failure_reason or "PRODUCER_REPORTED_FAILURE",
        )
    if not due:
        if obligation.current_status == ObligationStatus.NOT_YET_DUE.value:
            return obligation
        return ledger.update(obligation.obligation_id, ObligationStatus.NOT_YET_DUE)
    if obligation.expected_dataset in ACCOUNT_SCOPED_DATASETS \
            and not obligation.expected_identity.get("account_id"):
        return ledger.update(
            obligation.obligation_id, ObligationStatus.PRODUCER_FAILED,
            failure_reason="ACCOUNT_ID_MISSING_CANNOT_RECONCILE",
        )
    matches = [
        row for row in evidence
        if all(_value(row, IDENTITY_FIELD_PATHS.get(
                   obligation.expected_dataset, {}).get(key, key)) == value
               for key, value in obligation.expected_identity.items())
    ]
    if len(matches) > 1:
        return ledger.update(obligation.obligation_id, ObligationStatus.AMBIGUOUS)
    if not matches:
        return ledger.update(
            obligation.obligation_id, ObligationStatus.EXPECTED_BUT_MISSING,
            failure_reason="NO_EXACT_IDENTITY_MATCH",
        )
    identity = obligation.expected_identity
    # AID is part of all account-scoped obligation identity contracts. This
    # explicit guard prevents a malformed obligation from being satisfied.
    if obligation.expected_dataset in ACCOUNT_SCOPED_DATASETS and not identity.get("account_id"):
        raise ValueError(f"ACCOUNT_ID_REQUIRED:{obligation.expected_dataset}")
    record_id = next((str(_value(matches[0], IDENTITY_FIELD_PATHS.get(
        obligation.expected_dataset, {}).get(key, key)) or "") for key in (
        "attempt_id", "management_action_id", "decision_id", "trade_id",
        "audit_id", "record_id", "canonical_opportunity_id",
    ) if _value(matches[0], IDENTITY_FIELD_PATHS.get(
        obligation.expected_dataset, {}).get(key, key))), None)
    return ledger.update(
        obligation.obligation_id, ObligationStatus.PRESENT,
        observed_record_id=record_id,
    )


def begin_active_cycle(
    ledger: LifecycleEvidenceLedger, *, event_id: str, symbol: str,
    cycle_id: int, entity_id: str, correlation_id: str,
    timestamp: str,
) -> tuple[EvidenceObligation, EvidenceObligation]:
    identity = {"symbol": symbol, "cycle_id": cycle_id, "entity_id": entity_id,
                "correlation_id": correlation_id}
    execution_context = ledger.create(
        lifecycle_event_id=event_id, lifecycle_stage="ACTIVE_CYCLE",
        expected_dataset="execution_context", identity=identity,
        originating_timestamp=timestamp, due_state="EXPECTED_NOW",
        due_after="CYCLE_CONTEXT_CAPTURE", requirement_type="REQUIRED",
        current_status=ObligationStatus.NOT_YET_DUE,
        producer="core.runtime.execution_context_builder.build_cycle_context",
        producer_trigger="ACTIVE_SYMBOL_CYCLE",
    )
    market_context = ledger.create(
        lifecycle_event_id=event_id, lifecycle_stage="ACTIVE_CYCLE",
        expected_dataset="market_context", identity=identity,
        originating_timestamp=timestamp, due_state="CONDITIONALLY_EXPECTED",
        due_after="MATERIAL_CHANGE_GATE", requirement_type="CONDITIONAL",
        current_status=ObligationStatus.NOT_YET_DUE,
        producer="core.market_context.builder.MarketContextBuilder.build",
        producer_trigger="MATERIAL_CONTEXT_CHANGE",
    )
    return execution_context, market_context


def create_market_context_obligation(
    ledger: LifecycleEvidenceLedger, *, event_id: str, symbol: str,
    cycle_id: int, entity_id: str, timestamp: str,
) -> EvidenceObligation:
    return ledger.create(
        lifecycle_event_id=event_id, lifecycle_stage="ACTIVE_CYCLE",
        expected_dataset="market_context",
        identity={"symbol": symbol, "cycle_id": cycle_id, "entity_id": entity_id},
        originating_timestamp=timestamp, due_state="CONDITIONALLY_EXPECTED",
        due_after="MATERIAL_CHANGE_GATE", requirement_type="CONDITIONAL",
        current_status=ObligationStatus.NOT_YET_DUE,
        producer="core.market_context.builder.MarketContextBuilder.build",
        producer_trigger="MATERIAL_CONTEXT_CHANGE",
    )


def record_producer_outcome(
    ledger: LifecycleEvidenceLedger, obligation: EvidenceObligation, *,
    succeeded: bool, observed_record_id: str | None = None,
    failure_reason: str | None = None,
    provenance: Mapping[str, Any] | None = None,
) -> EvidenceObligation:
    """Record a local producer outcome without claiming canonical S3 presence."""
    if succeeded:
        existing = ledger.get(obligation.obligation_id)
        if existing is None:
            raise KeyError(f"UNKNOWN_OBLIGATION:{obligation.obligation_id}")
        current = ObligationStatus(existing.current_status)
        pending_status = (
            ObligationStatus.NOT_YET_DUE
            if current is ObligationStatus.NOT_YET_DUE
            else current
        )
        return ledger.update(
            obligation.obligation_id, pending_status,
            provenance={"producer_write": "LOCAL_FSYNC_SUCCEEDED",
                        "canonical_mirror_acknowledgement": "NOT_OBSERVED",
                        "local_record_id": observed_record_id,
                        **dict(provenance or {})},
        )
    return ledger.update(
        obligation.obligation_id, ObligationStatus.PRODUCER_FAILED,
        failure_reason=failure_reason or "PRODUCER_RETURNED_FAILURE",
        provenance={"producer_write": "FAILED", **dict(provenance or {})},
    )


def create_terminal_decision_obligations(
    ledger: LifecycleEvidenceLedger, *, event_id: str,
    identity: Mapping[str, Any], timestamp: str, no_trade: bool,
) -> tuple[EvidenceObligation, ...]:
    values: list[EvidenceObligation] = [ledger.create(
        lifecycle_event_id=event_id, lifecycle_stage="TERMINAL_NO_TRADE",
        expected_dataset="decision_ledger", identity=identity,
        originating_timestamp=timestamp, due_state="EXPECTED_NOW",
        due_after="DECISION_LEDGER_DURABLE_FLUSH", requirement_type="REQUIRED",
        current_status=ObligationStatus.NOT_YET_DUE,
        producer="core.decision_ledger.DecisionLedgerWriter",
        producer_trigger="TERMINAL_DECISION",
    )]
    if no_trade:
        for dataset in ("execution_attempts", "execution_results", "protection_audit", "trade_truth"):
            values.append(ledger.create(
                lifecycle_event_id=event_id, lifecycle_stage="TERMINAL_NO_TRADE",
                expected_dataset=dataset, identity=identity,
                originating_timestamp=timestamp, due_state="NOT_REQUIRED",
                due_after="NO_TRADE_TERMINAL_STATE", requirement_type="CONDITIONAL",
                current_status=ObligationStatus.NOT_APPLICABLE,
                producer="core.runtime.live_scanner", producer_trigger="NO_TRADE",
                provenance={"reason": "NO_BROKER_ORDER_OR_FILLED_TRADE"},
            ))
    return tuple(values)


def create_no_trade_obligations(
    ledger: LifecycleEvidenceLedger, *, event_id: str,
    identity: Mapping[str, Any], timestamp: str,
) -> tuple[EvidenceObligation, ...]:
    return create_terminal_decision_obligations(
        ledger, event_id=event_id, identity=identity, timestamp=timestamp,
        no_trade=True,
    )


def create_account_execution_obligations(
    ledger: LifecycleEvidenceLedger, *, event_id: str,
    identity: Mapping[str, Any], timestamp: str, order_send_expected: bool,
    attempt_id: str | None = None,
) -> tuple[EvidenceObligation, EvidenceObligation]:
    account_identity = dict(identity)
    if not account_identity.get("account_id"):
        raise ValueError("ACCOUNT_ID_REQUIRED:execution_results")
    if order_send_expected and not attempt_id:
        raise ValueError("ATTEMPT_ID_REQUIRED:order_send_expected")
    result_identity = dict(account_identity)
    result_identity.pop("attempt_id", None)
    result = ledger.create(
        lifecycle_event_id=event_id, lifecycle_stage="ACCOUNT_EXECUTION_BRANCH",
        expected_dataset="execution_results", identity=result_identity,
        originating_timestamp=timestamp, due_state="EXPECTED_NOW",
        due_after="ACCOUNT_ROUTE_TERMINAL_STATE", requirement_type="REQUIRED",
        current_status=ObligationStatus.NOT_YET_DUE,
        producer="core.runtime.fanout_execution._persist_account_results",
        producer_trigger="PER_ROUTED_ACCOUNT",
    )
    attempt = ledger.create(
        lifecycle_event_id=event_id, lifecycle_stage="ACCOUNT_EXECUTION_BRANCH",
        expected_dataset="execution_attempts",
        identity={**account_identity, **({"attempt_id": attempt_id} if attempt_id else {})},
        originating_timestamp=timestamp,
        due_state="EXPECTED_NOW" if order_send_expected else "NOT_REQUIRED",
        due_after="ORDER_SEND_RESULT" if order_send_expected else "PRE_DISPATCH_BLOCK",
        requirement_type="CONDITIONAL",
        current_status=(ObligationStatus.NOT_YET_DUE if order_send_expected
                        else ObligationStatus.NOT_APPLICABLE),
        producer="core.runtime.fanout_execution._persist_account_attempts",
        producer_trigger="ROUTE_IS_EXECUTABLE" if order_send_expected else "PRE_DISPATCH_BLOCK",
    )
    return result, attempt


def create_filled_position_obligations(
    ledger: LifecycleEvidenceLedger, *, event_id: str,
    identity: Mapping[str, Any], timestamp: str,
) -> tuple[EvidenceObligation, EvidenceObligation]:
    values = dict(identity)
    account_present = bool(values.get("account_id"))
    protection = ledger.create(
        lifecycle_event_id=event_id, lifecycle_stage="FILLED_POSITION",
        expected_dataset="protection_audit", identity=values,
        originating_timestamp=timestamp, due_state="EXPECTED_NOW",
        due_after="POST_FILL_PROTECTION_VERIFICATION", requirement_type="REQUIRED",
        current_status=(ObligationStatus.NOT_YET_DUE
                if account_present and values.get("position_ticket")
                        else ObligationStatus.PRODUCER_FAILED),
        producer="core.protection_verification.verify_protection",
        producer_trigger="BROKER_FILL_WITH_POSITION_TICKET",
        failure_reason=(None if account_present and values.get("position_ticket")
                else "ACCOUNT_OR_POSITION_TICKET_MISSING_AFTER_FILL"),
    )
    truth = ledger.create(
        lifecycle_event_id=f"trade:{values.get('account_id')}:{values.get('trade_id') or 'UNKNOWN'}",
        lifecycle_stage="TRADE_OUTCOME",
        expected_dataset="trade_truth", identity=values,
        originating_timestamp=timestamp, due_state="EXPECTED_LATER",
        due_after="POSITION_CLOSED", requirement_type="REQUIRED",
        current_status=(ObligationStatus.NOT_YET_DUE
                if account_present and values.get("trade_id")
                        else ObligationStatus.PRODUCER_FAILED),
        producer="core.trade_journal.persist_trade",
        producer_trigger="TRADE_CLOSE",
        failure_reason=(None if account_present and values.get("trade_id")
                else "ACCOUNT_OR_TRADE_ID_MISSING_AFTER_FILL"),
    )
    return protection, truth


def create_closed_trade_obligations(
    ledger: LifecycleEvidenceLedger, *, event_id: str,
    identity: Mapping[str, Any], timestamp: str, risk_geometry_valid: bool,
) -> tuple[EvidenceObligation, EvidenceObligation, EvidenceObligation]:
    values = dict(identity)
    account_id = str(values.get("account_id") or "")
    trade_id = str(values.get("trade_id") or "")
    truth_event_id = (f"trade:{account_id}:{trade_id or 'UNKNOWN'}" if account_id
                      else f"closed-trade-missing-account:{event_id}")
    prior_truth = ledger.find_exact("trade_truth", values) if trade_id and account_id else ()
    if len(prior_truth) > 1:
        raise ValueError("AMBIGUOUS_EXISTING_TRADE_TRUTH_OBLIGATION")
    if prior_truth:
        truth = prior_truth[0]
        if truth.current_status == ObligationStatus.NOT_YET_DUE.value:
            truth = ledger.update(
                truth.obligation_id, ObligationStatus.EXPECTED_BUT_MISSING,
                failure_reason="CLOSED_TRADE_AWAITING_CANONICAL_TRADE_TRUTH",
                provenance={"closed_trade_event_id": event_id, "due": True},
            )
    else:
        truth = ledger.create(
            lifecycle_event_id=truth_event_id, lifecycle_stage="TRADE_OUTCOME",
            expected_dataset="trade_truth", identity=values,
            originating_timestamp=timestamp, due_state="EXPECTED_LATER",
            due_after="POSITION_CLOSED", requirement_type="REQUIRED",
            current_status=(ObligationStatus.EXPECTED_BUT_MISSING
                            if trade_id and account_id
                            else ObligationStatus.PRODUCER_FAILED),
            producer="core.trade_journal.persist_trade",
            producer_trigger="CLOSED_POSITION",
            failure_reason=("CLOSED_TRADE_AWAITING_CANONICAL_TRADE_TRUTH"
                            if trade_id and account_id
                            else "ACCOUNT_OR_TRADE_ID_MISSING_AT_CLOSE"),
            provenance={"closed_trade_event_id": event_id, "due": True},
        )
    risk_identity = {
        "trade_id": trade_id,
        "account_id": account_id,
        "symbol": values.get("symbol", ""),
    }
    risk_applicable = risk_geometry_valid and bool(trade_id and account_id)
    risk = ledger.create(
        lifecycle_event_id=f"trade-risk:{account_id}:{trade_id or event_id}",
        lifecycle_stage="CLOSED_TRADE",
        expected_dataset="risk_deviation", identity=risk_identity,
        originating_timestamp=timestamp,
        due_state="EXPECTED_LATER" if risk_applicable else "NOT_REQUIRED",
        due_after=("RISK_GEOMETRY_VALID" if risk_applicable
                   else "NO_VALID_RISK_GEOMETRY_OR_IDENTITY"),
        requirement_type="CONDITIONAL",
        current_status=(ObligationStatus.NOT_YET_DUE if risk_applicable
                        else ObligationStatus.PRODUCER_FAILED
                        if risk_geometry_valid else ObligationStatus.NOT_APPLICABLE),
        producer="core.risk_deviation.persist_risk_deviation",
        producer_trigger="CLOSED_TRADE_VALID_RISK_GEOMETRY",
        failure_reason=("RISK_DEVIATION_ACCOUNT_OR_TRADE_ID_MISSING"
                        if risk_geometry_valid and not (trade_id and account_id)
                        else None),
    )
    journal = ledger.create(
        lifecycle_event_id=f"trade-journal:{account_id}:{trade_id or event_id}",
        lifecycle_stage="CLOSED_TRADE",
        expected_dataset="trade_journal", identity=values,
        originating_timestamp=timestamp, due_state="CONDITIONALLY_EXPECTED",
        due_after="TRADE_JOURNAL_LOCAL_WRITE", requirement_type="CONDITIONAL",
        current_status=(ObligationStatus.NOT_YET_DUE if trade_id and account_id
                        else ObligationStatus.PRODUCER_FAILED),
        producer="core.trade_journal.persist_trade",
        producer_trigger="CLOSED_POSITION",
        failure_reason=(None if trade_id and account_id
                else "TRADE_JOURNAL_ACCOUNT_OR_TRADE_ID_MISSING"),
    )
    return truth, risk, journal


def create_conditional_obligations(
    ledger: LifecycleEvidenceLedger, *, event_id: str, lifecycle_stage: str,
    datasets: Iterable[str], identity: Mapping[str, Any], timestamp: str,
    producer: str, trigger: str,
) -> tuple[EvidenceObligation, ...]:
    """Create explicit conditional expectations for shadow/portfolio/evaluations."""
    return tuple(ledger.create(
        lifecycle_event_id=event_id, lifecycle_stage=lifecycle_stage,
        expected_dataset=dataset, identity=identity,
        originating_timestamp=timestamp, due_state="CONDITIONALLY_EXPECTED",
        due_after=trigger, requirement_type="CONDITIONAL",
        current_status=ObligationStatus.NOT_YET_DUE,
        producer=producer, producer_trigger=trigger,
    ) for dataset in datasets)


def create_dataset_obligation(
    ledger: LifecycleEvidenceLedger, *, event_id: str, lifecycle_stage: str,
    dataset: str, identity: Mapping[str, Any], timestamp: str,
    producer: str, trigger: str, applicable: bool = True,
    requirement_type: str = "CONDITIONAL",
) -> EvidenceObligation:
    """Create one event obligation, failing closed on incomplete exact identity."""
    missing = [key for key in EXACT_IDENTITY_FIELDS[dataset]
               if identity.get(key) in (None, "", 0, "0")]
    if not applicable:
        status = ObligationStatus.NOT_APPLICABLE
        due_state = "NOT_REQUIRED"
        reason = None
    elif missing:
        status = ObligationStatus.PRODUCER_FAILED
        due_state = "EXPECTED_NOW"
        reason = "EXACT_IDENTITY_MISSING:" + ",".join(missing)
    else:
        status = ObligationStatus.NOT_YET_DUE
        due_state = "EXPECTED_NOW"
        reason = None
    return ledger.create(
        lifecycle_event_id=event_id, lifecycle_stage=lifecycle_stage,
        expected_dataset=dataset, identity=identity,
        originating_timestamp=timestamp, due_state=due_state,
        due_after=trigger, requirement_type=requirement_type,
        current_status=status, producer=producer, producer_trigger=trigger,
        failure_reason=reason,
    )


def obligation_ledger(path: str | Path | None = None) -> LifecycleEvidenceLedger:
    """Return the shared process ledger, or an isolated store for an explicit path."""
    if path is not None:
        return LifecycleEvidenceLedger(path)
    global _DEFAULT_LEDGER
    if _DEFAULT_LEDGER is None:
        _DEFAULT_LEDGER = LifecycleEvidenceLedger(DEFAULT_LEDGER_PATH)
    return _DEFAULT_LEDGER


_DEFAULT_LEDGER: LifecycleEvidenceLedger | None = None


__all__ = [
    "ACCOUNT_SCOPED_DATASETS", "DATASET_DISPOSITIONS", "EXACT_IDENTITY_FIELDS",
    "LIFECYCLE_CONTRACT",
    "EvidenceObligation", "LifecycleEvidenceLedger", "ObligationStatus",
    "begin_active_cycle", "create_account_execution_obligations",
    "create_closed_trade_obligations", "create_conditional_obligations",
    "create_dataset_obligation", "create_filled_position_obligations",
    "create_market_context_obligation",
    "create_no_trade_obligations", "create_terminal_decision_obligations",
    "exact_identity_key", "obligation_ledger",
    "reconcile_obligation", "record_producer_outcome",
]