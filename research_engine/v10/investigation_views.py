"""Read-only investigation views over canonical Production V1 evidence.

These views are deliberately not a replacement ``research_universe``.  Each
view keeps its authoritative grain, joins only on governed identities, and
reports exclusions instead of silently selecting an ambiguous source row.
Current S3 populations are mutable; consequently results identify their source
schemas and content fingerprint but do not claim a Stage 4 frozen snapshot ID.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Mapping, Protocol, Sequence

from core.production_data_contract import current_schema
from research_engine.control_plane.evidence_resolver import normalise_evidence_record
from research_engine.data_access.s3_source import get_default_source
from research_engine.v10.universes.evidence_integrity import (
    reconstruct_shadow_outcomes_report,
)
from research_engine.v10.universes.execution_universe import (
    normalise_trade_truth_record,
)

INVESTIGATION_TRANSFORMATION_VERSION = "investigation_views_v2"


class DatasetReader(Protocol):
    def read_dataset(self, dataset: str, **kwargs: Any) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class InvestigationFilters:
    symbol: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    pattern: str | None = None
    strategy: str | None = None
    regime: str | None = None
    phase: str | None = None
    broker: str | None = None
    account_id: str | None = None
    correlation_id: str | None = None
    trade_id: str | None = None
    decision_id: str | None = None
    candidate_id: str | None = None
    treatment_id: str | None = None


@dataclass(frozen=True)
class JoinContract:
    left_dataset: str
    right_dataset: str
    key: str
    cardinality: str
    ambiguity_behavior: str = "LEAVE_OPTIONAL_SIDE_UNJOINED_AND_REPORT"
    missing_side_behavior: str = "RETAIN_AUTHORITY_AND_REPORT"
    duplicate_behavior: str = "NEVER_SELECT_ARBITRARILY"


@dataclass(frozen=True)
class InvestigationResult:
    view: str
    records: tuple[dict[str, Any], ...]
    accounting: Mapping[str, Any]
    source_schemas: Mapping[str, str]
    join_contracts: tuple[JoinContract, ...] = ()
    limitations: tuple[str, ...] = ()
    population_state: str = "LIVE_MUTABLE_UNBOUND"
    dataset_snapshot_id: str | None = None
    evidence_epoch_id: str | None = None
    snapshot_id: str | None = None
    evidence_epoch: str | None = None
    snapshot_fingerprint: str | None = None
    dataset_fingerprints: Mapping[str, str] = field(default_factory=dict)
    source_object_boundaries: Mapping[str, Any] = field(default_factory=dict)
    snapshot_source_row_counts: Mapping[str, int] = field(default_factory=dict)
    snapshot_schema_versions: Mapping[str, str] = field(default_factory=dict)
    date_bounds: Mapping[str, str] = field(default_factory=dict)
    dataset_coverage: tuple[Mapping[str, str], ...] = ()
    snapshot_manifest: Mapping[str, Any] = field(default_factory=dict)
    immutable: bool = False
    source_state: str = "LIVE_MUTABLE_UNBOUND"
    view_configuration: Mapping[str, Any] = field(default_factory=dict)
    transformation_version: str = INVESTIGATION_TRANSFORMATION_VERSION
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if self.immutable:
            if not self.snapshot_id or not self.snapshot_fingerprint \
                    or not self.evidence_epoch or self.source_state != "FROZEN":
                raise ValueError("frozen investigation result lacks snapshot identity")
            if self.population_state != "FROZEN":
                raise ValueError("frozen investigation result has mutable population state")
        elif self.snapshot_id is not None or self.source_state != "LIVE_MUTABLE_UNBOUND":
            raise ValueError("live investigation result claims frozen snapshot state")
        payload = {
            "view": self.view,
            "records": self.records,
            "accounting": self.accounting,
            "source_schemas": self.source_schemas,
            "join_contracts": [asdict(item) for item in self.join_contracts],
            "limitations": self.limitations,
            "population_state": self.population_state,
            "snapshot_id": self.snapshot_id,
            "evidence_epoch": self.evidence_epoch,
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "dataset_fingerprints": self.dataset_fingerprints,
            "source_object_boundaries": self.source_object_boundaries,
            "snapshot_source_row_counts": self.snapshot_source_row_counts,
            "snapshot_schema_versions": self.snapshot_schema_versions,
            "date_bounds": self.date_bounds,
            "dataset_coverage": self.dataset_coverage,
            "snapshot_manifest": self.snapshot_manifest,
            "immutable": self.immutable,
            "source_state": self.source_state,
            "view_configuration": self.view_configuration,
            "transformation_version": self.transformation_version,
        }
        object.__setattr__(self, "fingerprint", _hash(payload))

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["records"] = list(self.records)
        return value


def _hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _nested(record: Mapping[str, Any], path: str) -> Any:
    value: Any = record
    for part in path.split("."):
        if not isinstance(value, Mapping):
            return None
        value = value.get(part)
    return value


def _first(record: Mapping[str, Any], *paths: str) -> Any:
    for path in paths:
        value = _nested(record, path)
        if value not in (None, "", [], {}):
            return value
    return None


def _index(rows: Sequence[Mapping[str, Any]], *paths: str) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in rows:
        value = _first(raw, *paths)
        if value not in (None, ""):
            result[str(value)].append(dict(raw))
    return result


def _compound_index(
    rows: Sequence[Mapping[str, Any]], *paths: str
) -> dict[tuple[str, ...], list[dict[str, Any]]]:
    result: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for raw in rows:
        values = tuple(str(_nested(raw, path) or "") for path in paths)
        if all(values):
            result[values].append(dict(raw))
    return result


def _source_identity(dataset: str, row: Mapping[str, Any]) -> str:
    if dataset == "execution_attempts":
        attempt_id = _first(row, "attempt_id")
        if attempt_id not in (None, "", 0, "0"):
            return str(attempt_id)
    if dataset == "execution_results":
        scoped_id = _first(row, "account_execution_id")
        if scoped_id not in (None, "", 0, "0"):
            return str(scoped_id)
        # execution_results_v1 is one orchestrator result, but has no dedicated
        # record ID in the persisted contract. Its governed compound grain is
        # lineage + account + write timestamp + broker response identity.
        compound = (
            _first(row, "correlation_id"), _first(row, "account_id"),
            _first(row, "timestamp_utc"), _first(row, "deal", "response.deal_id"),
            _first(row, "order_ticket", "response.order_id"), _first(row, "comment"),
        )
        if compound[0] not in (None, "") and compound[2] not in (None, ""):
            return "compound:" + _hash(compound)
    preferred = {
        "trade_truth": ("identity.trade_id",),
        "decision_trace": ("decision_id", "entity_id", "correlation_id"),
        "execution_results": ("deal", "deal_ticket", "order_ticket", "entity_id", "correlation_id"),
        "execution_attempts": ("attempt_id", "entity_id", "correlation_id"),
        "execution_context": ("entity_id", "correlation_id"),
        "protection_audit": ("audit_id", "correlation_id"),
        "risk_deviation": ("observation_id", "correlation_id"),
    }.get(dataset, ("entity_id", "correlation_id", "canonical_opportunity_id"))
    for path in preferred:
        value = _nested(row, path)
        if value not in (None, "", 0, "0"):
            return str(value)
    return f"sha256:{_hash(row)}"


def _provenance(
    sources: Sequence[tuple[str, Mapping[str, Any]]],
    *,
    missing: Sequence[str] = (),
    issues: Sequence[str] = (),
    missing_fields: Sequence[str] = (),
    ambiguous_fields: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "sources": [
            {
                "dataset": dataset,
                "schema_version": current_schema(dataset),
                "source_record_identity": _source_identity(dataset, row),
                "record_fingerprint": _hash(row),
            }
            for dataset, row in sources
        ],
        "population_state": "LIVE_MUTABLE_UNBOUND",
        "dataset_snapshot_id": None,
        "evidence_epoch_id": None,
        "missing_optional_evidence": sorted(set(missing)),
        "join_issues": sorted(set(issues)),
        "missing_evidence_fields": sorted(set(missing_fields)),
        "ambiguous_evidence_fields": sorted(set(ambiguous_fields)),
    }


def _evidence_gap_accounting(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    missing_rows = ambiguous_rows = 0
    missing_fields: dict[str, int] = defaultdict(int)
    ambiguous_fields: dict[str, int] = defaultdict(int)
    for row in records:
        provenance = row.get("_provenance") or {}
        missing = set(provenance.get("missing_evidence_fields") or ())
        ambiguous = set(provenance.get("ambiguous_evidence_fields") or ())
        missing_rows += bool(missing)
        ambiguous_rows += bool(ambiguous)
        for field_name in missing:
            missing_fields[str(field_name)] += 1
        for field_name in ambiguous:
            ambiguous_fields[str(field_name)] += 1
    return {
        "rows_missing_optional_context": missing_rows,
        "rows_with_ambiguous_optional_context": ambiguous_rows,
        "missing_evidence_by_source_field": dict(sorted(missing_fields.items())),
        "ambiguous_evidence_by_source_field": dict(sorted(ambiguous_fields.items())),
    }


def _apply_evidence_gap_accounting(
    accounting: dict[str, Any], records: Sequence[Mapping[str, Any]],
) -> None:
    accounting.update(_evidence_gap_accounting(records))


def _identity_field(dataset: str, *fields: str) -> str:
    return f"{dataset}.{'+'.join(fields)}"


def _decision_fields(row: Mapping[str, Any] | None) -> dict[str, Any]:
    if row is None:
        return {}
    market = row.get("v10_market_state") or {}
    regime = market.get("regime") if isinstance(market, Mapping) else {}
    regime = regime if isinstance(regime, Mapping) else {}
    strategy = row.get("v10_strategy") or {}
    strategy = strategy if isinstance(strategy, Mapping) else {}
    entry = row.get("v10_entry") or {}
    entry = entry if isinstance(entry, Mapping) else {}
    risk = row.get("v10_risk") or {}
    risk = risk if isinstance(risk, Mapping) else {}
    return {
        "decision_id": row.get("decision_id"),
        "decision_action": row.get("action"),
        "decision_timestamp": row.get("timestamp_utc"),
        "direction": strategy.get("direction") or entry.get("direction"),
        "strategy": strategy.get("strategy_id") or strategy.get("family"),
        "pattern": row.get("pattern_name") or strategy.get("pattern"),
        "score": row.get("score_strategy") or row.get("score_neutral"),
        "confidence": strategy.get("confidence"),
        "regime": regime.get("regime"),
        "phase": market.get("market_phase") or market.get("phase"),
        "session": market.get("session"),
        "stop_loss": entry.get("stop_loss") or entry.get("sl"),
        "take_profit": entry.get("take_profit") or entry.get("tp"),
        "risk_percentage": risk.get("risk_percentage"),
        "position_size": risk.get("position_size"),
    }


def _matches(row: Mapping[str, Any], filters: InvestigationFilters) -> bool:
    checks = {
        "symbol": filters.symbol,
        "pattern": filters.pattern,
        "strategy": filters.strategy,
        "regime": filters.regime,
        "phase": filters.phase,
        "broker": filters.broker,
        "account_id": filters.account_id,
        "correlation_id": filters.correlation_id,
        "trade_id": filters.trade_id,
        "decision_id": filters.decision_id,
        "candidate_id": filters.candidate_id,
        "treatment_id": filters.treatment_id,
    }
    for name, expected in checks.items():
        if expected is not None and str(row.get(name) or "") != str(expected):
            return False
    return True


def _timestamp_sort_key(value: Any) -> tuple[int, float | str]:
    if isinstance(value, (int, float)):
        return (0, float(value))
    text = str(value or "")
    try:
        return (0, datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return (1, text)


def _utc_day(value: Any) -> str:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc).date().isoformat()
    text = str(value or "")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc).date().isoformat()
    except ValueError:
        return text[:10]


class InvestigationViews:
    """Build focused, deterministic research views without writing evidence."""

    def __init__(self, source: DatasetReader | None = None):
        self._source = source or get_default_source()
        self._cache: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
        self._snapshot = getattr(self._source, "snapshot_provenance", None)

    @classmethod
    def from_snapshot_id(
        cls, snapshot_id: str, *, source: DatasetReader | None = None,
        manifest_directory=None,
    ) -> "InvestigationViews":
        from research_engine.v10.investigation_snapshot import (
            open_investigation_snapshot,
        )
        return cls(open_investigation_snapshot(
            snapshot_id, source=source, manifest_directory=manifest_directory))

    def _result_provenance(self, filters: InvestigationFilters) -> dict[str, Any]:
        configuration = asdict(filters)
        if not self._snapshot:
            return {"view_configuration": configuration}
        manifest = dict(self._snapshot)
        dataset_rows = {
            str(item["dataset"]): int(item["source_row_count"])
            for item in manifest.get("datasets", ())
        }
        schemas = {
            str(item["dataset"]): str(item["schema_version"])
            for item in manifest.get("datasets", ())
        }
        boundaries = {
            str(item["dataset"]): [
                dict(obj) for obj in item.get("objects", ())
            ]
            for item in manifest.get("datasets", ())
        }
        return {
            "population_state": "FROZEN",
            "evidence_epoch_id": str(manifest["evidence_epoch"]),
            "snapshot_id": str(manifest["snapshot_id"]),
            "evidence_epoch": str(manifest["evidence_epoch"]),
            "snapshot_fingerprint": str(manifest["snapshot_fingerprint"]),
            "dataset_fingerprints": {
                str(item["dataset"]): str(item["content_digest"])
                for item in manifest.get("datasets", ())
            },
            "source_object_boundaries": boundaries,
            "snapshot_source_row_counts": dataset_rows,
            "snapshot_schema_versions": schemas,
            "date_bounds": {
                "start_date": str(manifest["start_date"]),
                "end_date": str(manifest["end_date"]),
            },
            "dataset_coverage": tuple(manifest.get("dataset_coverage", ())),
            "snapshot_manifest": manifest,
            "immutable": True,
            "source_state": "FROZEN",
            "view_configuration": configuration,
        }

    def _bind_record_provenance(self, records: Sequence[dict[str, Any]]) -> None:
        if not self._snapshot:
            return
        for record in records:
            provenance = record.get("_provenance")
            if not isinstance(provenance, dict):
                continue
            provenance.update({
                "population_state": "FROZEN",
                "dataset_snapshot_id": None,
                "evidence_epoch_id": str(self._snapshot["evidence_epoch"]),
                "snapshot_id": str(self._snapshot["snapshot_id"]),
                "snapshot_fingerprint": str(self._snapshot["snapshot_fingerprint"]),
                "immutable": True,
                "source_state": "FROZEN",
            })

    def _load(self, dataset: str, filters: InvestigationFilters) -> list[dict[str, Any]]:
        key = (dataset, filters.symbol, filters.start_date, filters.end_date)
        if key not in self._cache:
            kwargs = {
                key: value for key, value in {
                    "symbol": filters.symbol,
                    "start_date": filters.start_date,
                    "end_date": filters.end_date,
                }.items() if value is not None
            }
            self._cache[key] = list(self._source.read_dataset(dataset, **kwargs))
        return [dict(row) for row in self._cache[key]]

    def build_trade_investigation(
        self, filters: InvestigationFilters = InvestigationFilters()
    ) -> InvestigationResult:
        truths = self._load("trade_truth", filters)
        executions = [
            row for row in self._load("execution_results", filters)
            if row.get("comment") != "protection_verification"
        ]
        decisions = self._load("decision_trace", filters)
        market_context = self._load("market_context", filters)
        strategy_observations = self._load("strategy_observations", filters)
        protections = self._load("protection_audit", filters)
        deviations = self._load("risk_deviation", filters)
        exec_by_identity = _compound_index(
            executions, "correlation_id", "account_id"
        )
        decision_by_corr = _index(decisions, "correlation_id")
        decision_by_entity = _index(decisions, "entity_id")
        market_by_symbol_cycle = _compound_index(market_context, "symbol", "cycle_id")
        strategy_by_entity = _index(strategy_observations, "entity_id")
        protection_by_identity = _compound_index(
            protections, "correlation_id", "account_id", "position_ticket"
        )
        deviation_by_trade_id = _index(deviations, "trade_id")
        truth_ids = _index(truths, "identity.trade_id")
        duplicate_ids = {key for key, values in truth_ids.items() if len(values) > 1}
        duplicate_execution_ids = {
            key for key, values in exec_by_identity.items() if len(values) > 1
        }
        output: list[dict[str, Any]] = []
        excluded_missing = excluded_ambiguous = filtered = joined = 0
        missing_required_fields: dict[str, int] = defaultdict(int)
        for truth in truths:
            trade_id = str(_first(truth, "identity.trade_id") or "")
            correlation_id = str(_first(truth, "identity.correlation_id") or "")
            account_id = str(_first(truth, "identity.account_id") or "")
            position_ticket = str(_first(truth, "identity.position_ticket") or "")
            missing_trade_id = not trade_id
            missing_realised_r = _first(truth, "outcome.r_multiple_realised") is None
            if missing_trade_id:
                missing_required_fields["trade_truth.identity.trade_id"] += 1
            if missing_realised_r:
                missing_required_fields["trade_truth.outcome.r_multiple_realised"] += 1
            if missing_trade_id or missing_realised_r:
                excluded_missing += 1
                continue
            if trade_id in duplicate_ids:
                excluded_ambiguous += 1
                continue
            issues: list[str] = []
            missing: list[str] = []
            missing_fields: list[str] = []
            ambiguous_fields: list[str] = []
            sources: list[tuple[str, Mapping[str, Any]]] = [("trade_truth", truth)]
            exec_key = (correlation_id, account_id)
            exec_matches = exec_by_identity.get(exec_key, []) if all(exec_key) else []
            execution = exec_matches[0] if len(exec_matches) == 1 else None
            if not all(exec_key):
                missing.append("execution_results")
                missing_fields.append(_identity_field(
                    "execution_results", "correlation_id", "account_id"
                ))
            elif len(exec_matches) > 1:
                issues.append("execution_results:ambiguous_exact_identity")
                ambiguous_fields.append(_identity_field(
                    "execution_results", "correlation_id", "account_id"
                ))
            elif execution is None:
                missing.append("execution_results")
                missing_fields.append(_identity_field(
                    "execution_results", "correlation_id", "account_id"
                ))
            else:
                sources.append(("execution_results", execution))
            entity_id = str(_first(execution or {}, "entity_id") or "")
            decision_matches = decision_by_entity.get(entity_id, []) if entity_id else []
            if not decision_matches and correlation_id:
                decision_matches = decision_by_corr.get(correlation_id, [])
            decision = decision_matches[0] if len(decision_matches) == 1 else None
            if len(decision_matches) > 1:
                issues.append("decision_trace:ambiguous_identity")
                ambiguous_fields.append(_identity_field(
                    "decision_trace", "entity_id"
                ))
            elif decision is None:
                missing.append("decision_trace")
                missing_fields.append(_identity_field(
                    "decision_trace", "entity_id"
                ))
            else:
                sources.append(("decision_trace", decision))
            normalized = normalise_trade_truth_record(truth, entity_id=entity_id)
            if normalized is None:
                excluded_missing += 1
                continue
            normalized.update({k: v for k, v in _decision_fields(decision).items() if v is not None})
            normalized["canonical_opportunity_id"] = _first(
                truth, "identity.canonical_opportunity_id"
            ) or _first(execution or {}, "canonical_opportunity_id")
            normalized["account_id"] = account_id or None
            normalized["broker"] = _first(truth, "identity.broker") or _first(execution or {}, "broker")
            normalized["broker_server"] = _first(truth, "identity.broker_server") or _first(execution or {}, "broker_server")
            normalized["mfe_r"] = _first(truth, "outcome.mfe_r")
            normalized["mae_r"] = _first(truth, "outcome.mae_r")
            cycle_id = str(_first(decision or {}, "cycle_id") or "")
            market_matches = market_by_symbol_cycle.get((str(normalized.get("symbol") or ""), cycle_id), []) if cycle_id else []
            if len(market_matches) == 1:
                normalized["market_context_evidence"] = normalise_evidence_record(market_matches[0], "market_context")
                sources.append(("market_context", market_matches[0]))
            elif len(market_matches) > 1:
                normalized["market_context_evidence"] = None
                issues.append("market_context:ambiguous_symbol_cycle_id")
                ambiguous_fields.append(_identity_field(
                    "market_context", "symbol", "cycle_id"
                ))
            else:
                normalized["market_context_evidence"] = None
                missing.append("market_context")
                missing_fields.append(_identity_field(
                    "market_context", "symbol", "cycle_id"
                ))
            strategy_matches = strategy_by_entity.get(entity_id, []) if entity_id else []
            if strategy_matches:
                normalized["strategy_observation_evidence"] = [
                    normalise_evidence_record(item, "strategy_observations")
                    for item in strategy_matches
                ]
                sources.extend(("strategy_observations", item) for item in strategy_matches)
            else:
                normalized["strategy_observation_evidence"] = None
                missing.append("strategy_observations")
                missing_fields.append(_identity_field(
                    "strategy_observations", "entity_id"
                ))
            normalized["execution_result"] = (
                normalise_evidence_record(execution, "execution_results_v1")
                if execution else None
            )
            protection_key = (correlation_id, account_id, position_ticket)
            protection_matches = (
                protection_by_identity.get(protection_key, [])
                if all(protection_key) else []
            )
            if protection_matches:
                normalized["protection_evidence"] = [
                    normalise_evidence_record(item, "protection_audit")
                    for item in protection_matches
                ]
                sources.extend(("protection_audit", item) for item in protection_matches)
            else:
                normalized["protection_evidence"] = None
                missing.append("protection_audit")
                missing_fields.append(_identity_field(
                    "protection_audit", "correlation_id", "account_id", "position_ticket"
                ))

            deviation_matches = deviation_by_trade_id.get(trade_id, []) if trade_id else []
            if len(deviation_matches) == 1:
                normalized["risk_deviation_evidence"] = [
                    normalise_evidence_record(deviation_matches[0], "risk_deviation")
                ]
                sources.append(("risk_deviation", deviation_matches[0]))
            elif len(deviation_matches) > 1:
                normalized["risk_deviation_evidence"] = None
                issues.append("risk_deviation:ambiguous_trade_id")
                ambiguous_fields.append(_identity_field("risk_deviation", "trade_id"))
            else:
                normalized["risk_deviation_evidence"] = None
                missing.append("risk_deviation")
                missing_fields.append(_identity_field("risk_deviation", "trade_id"))
            normalized["outcome_authority"] = "trade_truth_v1"
            normalized["_provenance"] = _provenance(
                sources, missing=missing, issues=issues,
                missing_fields=missing_fields, ambiguous_fields=ambiguous_fields,
            )
            if not _matches(normalized, filters):
                filtered += 1
                continue
            output.append(normalized)
            joined += int(bool(execution or decision))
        output.sort(key=lambda row: (_timestamp_sort_key(row.get("exit_time")), str(row["trade_id"])))
        accounting = {
            "primary_population": "unique trade_truth records with realised R",
            "source_rows_loaded": {
                "trade_truth": len(truths), "execution_results": len(executions),
                "decision_trace": len(decisions), "protection_audit": len(protections),
                "market_context": len(market_context), "strategy_observations": len(strategy_observations),
                "risk_deviation": len(deviations),
            },
            "primary_rows": len(truths), "eligible_rows": len(truths) - excluded_missing - excluded_ambiguous,
            "joined_rows": joined,
            "rows_excluded_ambiguous_identity": excluded_ambiguous,
            "rows_excluded_missing_required_identity": excluded_missing,
            "missing_required_identity_by_field": dict(sorted(missing_required_fields.items())),
            "duplicate_counts": {"trade_truth_identity_groups": len(duplicate_ids)},
            "filtered_out": filtered, "output_rows": len(output),
            "excluded_rows": excluded_missing + excluded_ambiguous + filtered,
            "fan_out": {"parent_rows": len(output), "output_rows": len(output), "additional_rows": 0},
            "exact_identity_keys": {
                "trade_truth_to_execution_results": "correlation_id + account_id",
                "execution_results_to_decision_trace": "entity_id; correlation_id fallback",
                "decision_trace_to_market_context": "symbol + cycle_id",
                "execution_to_strategy_observations": "entity_id",
                "trade_truth_to_protection_audit": "correlation_id + account_id + position_ticket",
                "trade_truth_to_risk_deviation": "trade_id",
            },
        }
        _apply_evidence_gap_accounting(accounting, output)
        accounting["ambiguous_rows"] = accounting["rows_with_ambiguous_optional_context"] + excluded_ambiguous
        accounting["missing_rows"] = accounting["rows_missing_optional_context"] + excluded_missing
        accounting["balanced"] = len(truths) == len(output) + filtered + excluded_missing + excluded_ambiguous
        self._bind_record_provenance(output)
        return InvestigationResult(
            view="trade_investigation", records=tuple(output), accounting=accounting,
            source_schemas={name: current_schema(name) for name in accounting["source_rows_loaded"]},
            join_contracts=(
                JoinContract("trade_truth", "execution_results", "correlation_id + account_id", "one-to-zero-or-one"),
                JoinContract("execution_results", "decision_trace", "entity_id; fallback correlation_id", "one-to-zero-or-one"),
                JoinContract("decision_trace", "market_context", "symbol + cycle_id", "one-to-zero-or-one"),
                JoinContract("decision_trace", "strategy_observations", "entity_id", "one-to-zero-or-many-evaluations"),
                JoinContract("trade_truth", "protection_audit", "correlation_id + account_id + position_ticket", "one-to-zero-or-many-exact-history"),
                JoinContract("trade_truth", "risk_deviation", "trade_id", "one-to-zero-or-one"),
            ),
            limitations=(
                "MAE/MFE remain absent unless trade_truth itself carries them.",
                "Mutable production data is not labelled as a frozen Stage 4 snapshot.",
            ),
            **self._result_provenance(filters),
        )

    def build_decision_execution_outcome(
        self, filters: InvestigationFilters = InvestigationFilters()
    ) -> InvestigationResult:
        decisions = self._load("decision_trace", filters)
        executions = [r for r in self._load("execution_results", filters) if r.get("comment") != "protection_verification"]
        truths = self._load("trade_truth", filters)
        exec_by_corr = _index(executions, "correlation_id")
        truth_by_corr = _index(truths, "identity.correlation_id")
        decision_ids = _index(decisions, "decision_id", "entity_id")
        duplicate_decisions = {key for key, rows in decision_ids.items() if len(rows) > 1}
        rows: list[dict[str, Any]] = []
        missing_required = ambiguous = filtered = 0
        missing_required_fields: dict[str, int] = defaultdict(int)
        for decision in decisions:
            decision_key = str(_first(decision, "decision_id", "entity_id") or "")
            correlation_id = str(decision.get("correlation_id") or "")
            if not decision_key or not correlation_id:
                missing_required += 1
                if not decision_key:
                    missing_required_fields["decision_trace.decision_id|entity_id"] += 1
                if not correlation_id:
                    missing_required_fields["decision_trace.correlation_id"] += 1
                continue
            if decision_key in duplicate_decisions:
                ambiguous += 1
                continue
            execs = exec_by_corr.get(correlation_id, [])
            # One decision may legitimately fan out to account-grained results,
            # but every result must carry a unique governed account/ticket grain.
            grains = [str(_first(e, "account_id", "deal_ticket", "order_ticket") or "") for e in execs]
            if len(execs) > 1 and (not all(grains) or len(set(grains)) != len(grains)):
                ambiguous += 1
                continue
            for execution in execs or [None]:
                candidates = truth_by_corr.get(correlation_id, [])
                issues: list[str] = []
                missing_fields: list[str] = []
                ambiguous_fields: list[str] = []
                account_mismatch = False
                if execution is not None and candidates:
                    execution_account = str(_first(execution, "account_id") or "")
                    truth_accounts = [
                        str(_first(item, "identity.account_id") or "")
                        for item in candidates
                    ]
                    if execution_account and all(truth_accounts):
                        scoped = [
                            item for item in candidates
                            if str(_first(item, "identity.account_id")) == execution_account
                        ]
                        account_mismatch = not scoped
                        candidates = scoped
                    else:
                        issues.append("trade_truth:account_id_missing_for_scope")
                        ambiguous_fields.append(_identity_field("trade_truth", "account_id"))
                        candidates = []
                if account_mismatch:
                    issues.append("trade_truth:account_id_mismatch")
                    ambiguous_fields.append(_identity_field("trade_truth", "account_id"))
                truth = candidates[0] if len(candidates) == 1 else None
                if len(candidates) > 1:
                    issues.append("trade_truth:ambiguous_account_scoped_identity")
                    ambiguous_fields.append(_identity_field(
                        "trade_truth", "correlation_id", "account_id"
                    ))
                missing = []
                if execution is None:
                    missing.append("execution_results")
                if truth is None and not issues:
                    missing.append("trade_truth")
                if execution is None:
                    missing_fields.append(_identity_field("execution_results", "correlation_id"))
                if truth is None and not issues:
                    missing_fields.append(_identity_field("trade_truth", "correlation_id"))
                if account_mismatch or "trade_truth:account_id_missing_for_scope" in issues:
                    missing.append("trade_truth")
                sources: list[tuple[str, Mapping[str, Any]]] = [("decision_trace", decision)]
                if execution: sources.append(("execution_results", execution))
                if truth: sources.append(("trade_truth", truth))
                row = {
                    **_decision_fields(decision),
                    "entity_id": decision.get("entity_id"), "correlation_id": correlation_id,
                    "symbol": decision.get("symbol"),
                    "execution_account_id": _first(execution or {}, "account_id"),
                    "realised_account_id": _first(truth or {}, "identity.account_id"),
                    "realised_trade_id": _first(truth or {}, "identity.trade_id"),
                    "execution_result": normalise_evidence_record(execution, "execution_results_v1") if execution else None,
                    "realised_outcome": normalise_trade_truth_record(truth) if truth else None,
                    "outcome_authority": "trade_truth_v1" if truth else None,
                    "_provenance": _provenance(
                        sources, missing=missing, issues=issues,
                        missing_fields=missing_fields,
                        ambiguous_fields=ambiguous_fields,
                    ),
                }
                if _matches(row, filters): rows.append(row)
                else: filtered += 1
        rows.sort(key=lambda row: (str(row.get("decision_timestamp") or ""), str(row.get("decision_id") or "")))
        unique_decision_ids = {str(r.get("decision_id") or "") for r in rows}
        rows_per_decision: dict[str, int] = defaultdict(int)
        for row in rows:
            rows_per_decision[str(row.get("decision_id") or "")] += 1
        execution_result_rows = sum(row["execution_result"] is not None for row in rows)
        outcome_attached_rows = sum(row["realised_outcome"] is not None for row in rows)
        decisions_with_execution = {
            str(row.get("decision_id") or "")
            for row in rows if row["execution_result"] is not None
        }
        decisions_without_execution_rows = sum(
            row["execution_result"] is None for row in rows
        )
        accounting = {
            "primary_population": "decision_trace decisions; execution results fan out by account",
            "source_rows_loaded": {"decision_trace": len(decisions), "execution_results": len(executions), "trade_truth": len(truths)},
            "primary_rows": len(decisions), "eligible_rows": len(decisions) - missing_required - ambiguous,
            "joined_rows": sum(bool(r["execution_result"] or r["realised_outcome"]) for r in rows),
            "rows_excluded_ambiguous_identity": ambiguous,
            "rows_excluded_missing_required_identity": missing_required,
            "missing_required_identity_by_field": dict(sorted(missing_required_fields.items())),
            "duplicate_counts": {"decision_identity_groups": len(duplicate_decisions)},
            "filtered_out": filtered, "output_rows": len(rows),
            "unique_decision_population": len(unique_decision_ids),
            "account_fanned_execution_result_rows": execution_result_rows,
            "outcome_attached_rows": outcome_attached_rows,
            "fan_out": {
                "decisions_with_multiple_execution_rows": sum(count > 1 for count in rows_per_decision.values()),
                "decisions_with_execution_results": len(decisions_with_execution),
                "decisions_without_execution_results": len(unique_decision_ids) - len(decisions_with_execution),
                "additional_account_fan_out_rows": execution_result_rows - len(decisions_with_execution),
                "execution_result_rows": execution_result_rows,
                "no_execution_decision_rows": decisions_without_execution_rows,
            },
            "excluded_rows": missing_required + ambiguous + filtered,
            "exact_identity_keys": {
                "decision_trace_to_execution_results": "correlation_id; account-grained result rows",
                "execution_results_to_trade_truth": "correlation_id + account_id",
                "decision_without_execution_to_trade_truth": "correlation_id only when unique",
            },
            "balanced": len(decisions) == missing_required + ambiguous + filtered + len(unique_decision_ids),
            "balanced_scope": "unique_decision_id_population_only",
            "row_expansion_balanced": len(rows) == execution_result_rows + decisions_without_execution_rows,
        }
        _apply_evidence_gap_accounting(accounting, rows)
        accounting["missing_rows"] = accounting["rows_missing_optional_context"] + missing_required
        accounting["ambiguous_rows"] = accounting["rows_with_ambiguous_optional_context"] + ambiguous
        self._bind_record_provenance(rows)
        return InvestigationResult(
            view="decision_execution_outcome", records=tuple(rows), accounting=accounting,
            source_schemas={name: current_schema(name) for name in accounting["source_rows_loaded"]},
            join_contracts=(
                JoinContract("decision_trace", "execution_results", "correlation_id", "one-to-many-account-grained"),
                JoinContract("execution_results", "trade_truth", "correlation_id + account_id whenever both are present", "one-to-zero-or-one; account mismatch remains unpaired"),
            ),
            **self._result_provenance(filters),
        )

    def build_shadow_comparison(
        self, filters: InvestigationFilters = InvestigationFilters()
    ) -> InvestigationResult:
        events = self._load("shadow_runtime", filters)
        truths = self._load("trade_truth", filters)
        timestamps = [str(_first(e, "timestamp_utc", "event_time_utc", "exit_market_time_utc_iso8601") or "") for e in events]
        valid = [t for t in timestamps if t.endswith("Z") or "+" in t]
        reconstruction_time = max(valid) if valid else "1970-01-01T00:00:00+00:00"
        report = reconstruct_shadow_outcomes_report(events, reconstruction_timestamp=reconstruction_time)
        truth_by_opp = _index(truths, "identity.canonical_opportunity_id", "canonical_opportunity_id")
        open_by_key = _index([e for e in events if e.get("event_type") == "OPEN"], "shadow_trade_id")
        rows: list[dict[str, Any]] = []
        filtered = ambiguous_live = missing_live = 0
        for envelope in report.artifacts:
            shadow = dict(envelope.artifact)
            identity = shadow.get("identity") or {}
            shadow_id = str(identity.get("shadow_trade_id") or "")
            opportunity_id = str(identity.get("canonical_opportunity_id") or "")
            live_matches = truth_by_opp.get(opportunity_id, []) if opportunity_id else []
            live = live_matches[0] if len(live_matches) == 1 else None
            if len(live_matches) > 1: ambiguous_live += 1
            elif live is None: missing_live += 1
            open_rows = open_by_key.get(shadow_id, [])
            open_event = open_rows[0] if len(open_rows) == 1 else {}
            arm = open_event.get("experiment_arm") or {}
            arm = arm if isinstance(arm, Mapping) else {}
            outcome = shadow.get("simulated_outcome") or {}
            row = {
                "shadow_trade_id": shadow_id,
                "canonical_opportunity_id": opportunity_id,
                "symbol": identity.get("symbol"),
                "strategy": identity.get("strategy_id"),
                "pattern": _first(shadow, "decision_snapshot.pattern"),
                "regime": _first(shadow, "decision_snapshot.regime"),
                "phase": _first(shadow, "decision_snapshot.market_phase"),
                "candidate_id": arm.get("candidate_id"),
                "treatment_id": arm.get("treatment_id"),
                "experiment_id": arm.get("experiment_id"),
                "experiment_arm": arm.get("experiment_arm"),
                "arm_assignment_id": arm.get("arm_assignment_id"),
                "arm_policy_version": arm.get("arm_policy_version"),
                "shadow_outcome": outcome,
                "shadow_r_multiple": outcome.get("pnl_r_multiple"),
                "incumbent_outcome": normalise_trade_truth_record(live) if live else None,
                "incumbent_r_multiple": _first(live or {}, "outcome.r_multiple_realised"),
                "comparison_semantics": "SIMULATED_SHADOW_VS_BROKER_REALISED_NOT_EXECUTION_NEUTRAL",
                "_provenance": {
                    "sources": [{"dataset": "shadow_runtime", "schema_version": current_schema("shadow_runtime"), "source_record_identity": item, "record_fingerprint": item.rsplit(":", 1)[-1]} for item in envelope.source_identities]
                    + ([{"dataset": "trade_truth", "schema_version": current_schema("trade_truth"), "source_record_identity": _source_identity("trade_truth", live), "record_fingerprint": _hash(live)}] if live else []),
                    "reconstruction": envelope.to_dict(),
                    "population_state": "LIVE_MUTABLE_UNBOUND", "dataset_snapshot_id": None, "evidence_epoch_id": None,
                    "missing_optional_evidence": ["trade_truth"] if not live_matches else [],
                    "join_issues": ["trade_truth:ambiguous_canonical_opportunity_id"] if len(live_matches) > 1 else [],
                    "missing_evidence_fields": (
                        [_identity_field("trade_truth", "canonical_opportunity_id")]
                        if not live_matches else []
                    ),
                    "ambiguous_evidence_fields": (
                        [_identity_field("trade_truth", "canonical_opportunity_id")]
                        if len(live_matches) > 1 else []
                    ),
                },
            }
            if _matches(row, filters): rows.append(row)
            else: filtered += 1
        rows.sort(key=lambda row: (str(row["canonical_opportunity_id"]), str(row["shadow_trade_id"])))
        accounting = {
            "primary_population": "completed shadow_runtime lifecycles",
            "source_rows_loaded": {"shadow_runtime": len(events), "trade_truth": len(truths)},
            "primary_rows": len(report.artifacts) + len(report.limitations), "eligible_rows": len(report.artifacts),
            "joined_rows": sum(r["incumbent_outcome"] is not None for r in rows),
            "rows_missing_optional_context": missing_live,
            "rows_excluded_ambiguous_identity": len(report.limitations),
            "ambiguous_incumbent_joins_retained_unpaired": ambiguous_live,
            "rows_excluded_missing_required_identity": 0,
            "duplicate_counts": {"invalid_or_ambiguous_shadow_lifecycles": len(report.limitations)},
            "filtered_out": filtered, "output_rows": len(rows),
            "excluded_rows": len(report.limitations) + filtered,
            "missing_rows": missing_live,
            "ambiguous_rows": ambiguous_live + len(report.limitations),
            "fan_out": {"parent_rows": len(report.artifacts), "output_rows": len(rows), "additional_rows": 0},
            "exact_identity_keys": {
                "shadow_runtime_to_trade_truth": "canonical_opportunity_id",
                "shadow_runtime_lifecycle": "shadow_trade_id",
            },
            "balanced": len(report.artifacts) == len(rows) + filtered,
        }
        _apply_evidence_gap_accounting(accounting, rows)
        self._bind_record_provenance(rows)
        return InvestigationResult(
            view="shadow_vs_incumbent", records=tuple(rows), accounting=accounting,
            source_schemas={"shadow_runtime": current_schema("shadow_runtime"), "trade_truth": current_schema("trade_truth")},
            join_contracts=(JoinContract("shadow_runtime reconstructed outcome", "trade_truth", "canonical_opportunity_id", "many-shadow-horizons-to-zero-or-one-live"),),
            limitations=("Shadow outcomes are simulated; incumbent outcomes are broker-realised.", "No timestamp-proximity matching is performed."),
            **self._result_provenance(filters),
        )

    def build_context_performance(self, filters: InvestigationFilters = InvestigationFilters()) -> InvestigationResult:
        base = self.build_trade_investigation(filters)
        records = tuple({
            key: row.get(key) for key in (
                "trade_id", "correlation_id", "symbol", "strategy", "pattern", "regime",
                "phase", "session", "entry_time", "exit_time", "r_multiple",
                "net_realised_pnl", "exit_reason", "_provenance",
            )
        } for row in base.records)
        return InvestigationResult(
            view="market_context_performance", records=records,
            accounting=dict(base.accounting), source_schemas=base.source_schemas,
            join_contracts=base.join_contracts,
            limitations=base.limitations + ("This is slice-ready row evidence, not an inferential aggregate.",),
            **self._result_provenance(filters),
        )

    def build_execution_quality(self, filters: InvestigationFilters = InvestigationFilters()) -> InvestigationResult:
        datasets = ("execution_attempts", "execution_results", "execution_context", "protection_audit", "risk_deviation", "trade_truth")
        loaded = {name: self._load(name, filters) for name in datasets}
        verification_rows = [
            row for row in loaded["execution_results"]
            if row.get("comment") == "protection_verification"
        ]
        execution_results = [
            row for row in loaded["execution_results"]
            if row.get("comment") != "protection_verification"
        ]
        primary_sources = {
            "execution_attempts": loaded["execution_attempts"],
            "execution_results": execution_results,
        }
        primary = primary_sources["execution_attempts"] + primary_sources["execution_results"]
        context_by_corr = _index(loaded["execution_context"], "correlation_id")
        protection_by_identity = _compound_index(
            loaded["protection_audit"], "correlation_id", "account_id", "position_ticket"
        )
        deviation_by_trade_id = _index(loaded["risk_deviation"], "trade_id")
        truth_by_identity = _compound_index(
            loaded["trade_truth"], "identity.correlation_id", "identity.account_id"
        )
        rows: list[dict[str, Any]] = []
        missing_required = filtered = 0
        missing_required_fields: dict[str, int] = defaultdict(int)
        identity_counts: dict[tuple[str, str], int] = defaultdict(int)
        for source_name, source_rows in primary_sources.items():
            for raw in source_rows:
                identity_counts[(source_name, _source_identity(source_name, raw))] += 1
        duplicate_keys = {key for key, count in identity_counts.items() if count > 1}
        duplicate_rows = 0
        for source_name, source_rows in primary_sources.items():
            for raw in source_rows:
                correlation_id = str(_first(raw, "correlation_id") or "")
                if not correlation_id:
                    missing_required += 1
                    missing_required_fields[f"{source_name}.correlation_id"] += 1
                    continue
                identity = (source_name, _source_identity(source_name, raw))
                if identity in duplicate_keys:
                    duplicate_rows += 1
                    continue
                sources: list[tuple[str, Mapping[str, Any]]] = [(source_name, raw)]
                missing: list[str] = []
                issues: list[str] = []
                missing_fields: list[str] = []
                ambiguous_fields: list[str] = []
                related: dict[str, Any] = {}
                context_matches = context_by_corr.get(correlation_id, [])
                if len(context_matches) == 1:
                    related["execution_context"] = normalise_evidence_record(
                        context_matches[0], "execution_context"
                    )
                    sources.append(("execution_context", context_matches[0]))
                elif len(context_matches) > 1:
                    related["execution_context"] = None
                    issues.append("execution_context:ambiguous_correlation_id")
                    ambiguous_fields.append(_identity_field("execution_context", "correlation_id"))
                else:
                    related["execution_context"] = None
                    missing.append("execution_context")
                    missing_fields.append(_identity_field("execution_context", "correlation_id"))

                account_id = str(_first(raw, "account_id") or "")
                position_ticket = str(_first(raw, "position_ticket") or "")
                protection_key = (correlation_id, account_id, position_ticket)
                protection_matches = (
                    protection_by_identity.get(protection_key, [])
                    if all(protection_key) else []
                )
                if protection_matches:
                    related["protection_audit"] = [
                        normalise_evidence_record(item, "protection_audit")
                        for item in protection_matches
                    ]
                    sources.extend(("protection_audit", item) for item in protection_matches)
                else:
                    related["protection_audit"] = None
                    missing.append("protection_audit")
                    missing_fields.append(_identity_field(
                        "protection_audit", "correlation_id", "account_id", "position_ticket"
                    ))

                truth_key = (correlation_id, account_id)
                truth_matches = truth_by_identity.get(truth_key, []) if all(truth_key) else []
                if len(truth_matches) == 1:
                    trade_truth = truth_matches[0]
                    related["trade_truth"] = normalise_trade_truth_record(trade_truth)
                    sources.append(("trade_truth", trade_truth))
                    trade_id = str(_first(trade_truth, "identity.trade_id") or "")
                elif len(truth_matches) > 1:
                    related["trade_truth"] = None
                    trade_id = ""
                    issues.append("trade_truth:ambiguous_correlation_account_identity")
                    ambiguous_fields.append(_identity_field(
                        "trade_truth", "correlation_id", "account_id"
                    ))
                else:
                    related["trade_truth"] = None
                    trade_id = str(_first(raw, "trade_id") or "")
                    missing.append("trade_truth")
                    missing_fields.append(_identity_field(
                        "trade_truth", "correlation_id", "account_id"
                    ))
                deviation_matches = deviation_by_trade_id.get(trade_id, []) if trade_id else []
                if len(deviation_matches) == 1:
                    related["risk_deviation"] = normalise_evidence_record(
                        deviation_matches[0], "risk_deviation"
                    )
                    sources.append(("risk_deviation", deviation_matches[0]))
                elif len(deviation_matches) > 1:
                    related["risk_deviation"] = None
                    issues.append("risk_deviation:ambiguous_trade_id")
                    ambiguous_fields.append(_identity_field("risk_deviation", "trade_id"))
                else:
                    related["risk_deviation"] = None
                    missing.append("risk_deviation")
                    missing_fields.append(_identity_field("risk_deviation", "trade_id"))
                row = {
                    **normalise_evidence_record(raw, "execution_results_v1" if source_name == "execution_results" else source_name),
                    "record_kind": source_name, "correlation_id": correlation_id,
                    "account_id": account_id or None,
                    "trade_id": trade_id or None,
                    "related_evidence": related,
                    "_provenance": _provenance(
                        sources, missing=missing, issues=issues,
                        missing_fields=missing_fields,
                        ambiguous_fields=ambiguous_fields,
                    ),
                }
                if _matches(row, filters): rows.append(row)
                else: filtered += 1
        rows.sort(key=lambda row: (str(row.get("correlation_id")), str(row.get("record_kind")), _hash(row)))
        accounting = {
            "primary_population": "execution_attempts plus non-verification execution_results",
            "source_rows_loaded": {name: len(value) for name, value in loaded.items()},
            "execution_results_primary_rows": len(execution_results),
            "execution_results_protection_verification_rows": len(verification_rows),
            "primary_rows": len(primary), "eligible_rows": len(primary) - missing_required - duplicate_rows,
            "joined_rows": sum(bool(r["_provenance"]["sources"][1:]) for r in rows),
            "rows_excluded_ambiguous_identity": duplicate_rows,
            "rows_excluded_missing_required_identity": missing_required,
            "missing_required_identity_by_field": dict(sorted(missing_required_fields.items())),
            "duplicate_counts": {"ambiguous_source_identity_rows": duplicate_rows, "ambiguous_identity_groups": len(duplicate_keys)},
            "filtered_out": filtered, "output_rows": len(rows),
            "excluded_rows": missing_required + duplicate_rows + filtered,
            "fan_out": {"parent_rows": len(rows), "output_rows": len(rows), "additional_rows": 0},
            "exact_identity_keys": {
                "execution_results_to_execution_context": "correlation_id",
                "execution_results_to_protection_audit": "correlation_id + account_id + position_ticket",
                "execution_attempts/results_to_trade_truth": "correlation_id + account_id",
                "trade_truth_to_risk_deviation": "trade_id",
            },
            "balanced": len(primary) == len(rows) + filtered + missing_required + duplicate_rows,
        }
        _apply_evidence_gap_accounting(accounting, rows)
        accounting["missing_rows"] = accounting["rows_missing_optional_context"] + missing_required
        accounting["ambiguous_rows"] = accounting["rows_with_ambiguous_optional_context"] + duplicate_rows
        self._bind_record_provenance(rows)
        return InvestigationResult(
            view="execution_quality", records=tuple(rows), accounting=accounting,
            source_schemas={name: current_schema(name) for name in datasets},
            join_contracts=tuple(JoinContract("execution_attempts/results", name, "correlation_id", "one-to-zero-or-one") for name in datasets[2:]),
            limitations=("Multiple protection/audit observations at one correlation are reported as ambiguous, not collapsed.",),
            **self._result_provenance(filters),
        )

    def build_risk_sequence(self, filters: InvestigationFilters = InvestigationFilters()) -> InvestigationResult:
        base = self.build_trade_investigation(filters)
        cumulative = peak = 0.0
        losing_streak = 0
        daily: dict[str, float] = defaultdict(float)
        rows: list[dict[str, Any]] = []
        for trade in base.records:
            r_value = float(trade["r_multiple"])
            cumulative += r_value
            peak = max(peak, cumulative)
            losing_streak = losing_streak + 1 if r_value < 0 else 0
            day = _utc_day(trade.get("exit_time"))
            daily[day] += r_value
            rows.append({
                "trade_id": trade["trade_id"], "correlation_id": trade.get("correlation_id"),
                "symbol": trade.get("symbol"), "exit_time": trade.get("exit_time"),
                "r_multiple": r_value, "cumulative_r": cumulative,
                "drawdown_r": cumulative - peak, "losing_streak": losing_streak,
                "day": day, "daily_r_to_this_trade": daily[day],
                "risk_percentage": trade.get("risk_percentage"),
                "position_size": trade.get("position_size"),
                "portfolio_exposure": None,
                "_provenance": trade["_provenance"],
            })
        return InvestigationResult(
            view="risk_drawdown_sequence", records=tuple(rows), accounting=dict(base.accounting),
            source_schemas=base.source_schemas, join_contracts=base.join_contracts,
            limitations=base.limitations + ("Portfolio exposure is not invented when no exact evidence is joined.", "Drawdown is an R-sequence derivation, not broker equity drawdown."),
            **self._result_provenance(filters),
        )


def build_trade_investigation(**kwargs: Any) -> InvestigationResult:
    return InvestigationViews().build_trade_investigation(InvestigationFilters(**kwargs))


def build_decision_execution_outcome(**kwargs: Any) -> InvestigationResult:
    return InvestigationViews().build_decision_execution_outcome(InvestigationFilters(**kwargs))


def build_shadow_comparison(**kwargs: Any) -> InvestigationResult:
    return InvestigationViews().build_shadow_comparison(InvestigationFilters(**kwargs))


def build_context_performance(**kwargs: Any) -> InvestigationResult:
    return InvestigationViews().build_context_performance(InvestigationFilters(**kwargs))


def build_execution_quality(**kwargs: Any) -> InvestigationResult:
    return InvestigationViews().build_execution_quality(InvestigationFilters(**kwargs))


def build_risk_sequence(**kwargs: Any) -> InvestigationResult:
    return InvestigationViews().build_risk_sequence(InvestigationFilters(**kwargs))
