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
    dataset_snapshot_id: None = None
    evidence_epoch_id: None = None
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        payload = {
            "view": self.view,
            "records": self.records,
            "accounting": self.accounting,
            "source_schemas": self.source_schemas,
            "join_contracts": [asdict(item) for item in self.join_contracts],
            "limitations": self.limitations,
            "population_state": self.population_state,
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
    }


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
        exec_by_corr = _index(executions, "correlation_id", "identity.correlation_id")
        decision_by_corr = _index(decisions, "correlation_id")
        decision_by_entity = _index(decisions, "entity_id")
        market_by_symbol_cycle = _compound_index(market_context, "symbol", "cycle_id")
        strategy_by_entity = _index(strategy_observations, "entity_id")
        protection_by_corr = _index(protections, "correlation_id")
        deviation_by_corr = _index(deviations, "correlation_id")
        truth_ids = _index(truths, "identity.trade_id")
        duplicate_ids = {key for key, values in truth_ids.items() if len(values) > 1}
        output: list[dict[str, Any]] = []
        excluded_missing = excluded_ambiguous = filtered = missing_optional = joined = 0
        for truth in truths:
            trade_id = str(_first(truth, "identity.trade_id") or "")
            correlation_id = str(_first(truth, "identity.correlation_id") or "")
            if not trade_id or _first(truth, "outcome.r_multiple_realised") is None:
                excluded_missing += 1
                continue
            if trade_id in duplicate_ids:
                excluded_ambiguous += 1
                continue
            issues: list[str] = []
            missing: list[str] = []
            sources: list[tuple[str, Mapping[str, Any]]] = [("trade_truth", truth)]
            exec_matches = exec_by_corr.get(correlation_id, []) if correlation_id else []
            execution = exec_matches[0] if len(exec_matches) == 1 else None
            if len(exec_matches) > 1:
                issues.append("execution_results:ambiguous_correlation_id")
            elif execution is None:
                missing.append("execution_results")
            else:
                sources.append(("execution_results", execution))
            entity_id = str(_first(execution or {}, "entity_id") or "")
            decision_matches = decision_by_entity.get(entity_id, []) if entity_id else []
            if not decision_matches and correlation_id:
                decision_matches = decision_by_corr.get(correlation_id, [])
            decision = decision_matches[0] if len(decision_matches) == 1 else None
            if len(decision_matches) > 1:
                issues.append("decision_trace:ambiguous_identity")
            elif decision is None:
                missing.append("decision_trace")
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
            normalized["account_id"] = _first(
                truth, "identity.account_id"
            ) or _first(execution or {}, "account_id")
            normalized["broker"] = _first(execution or {}, "broker")
            normalized["broker_server"] = _first(execution or {}, "broker_server")
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
            else:
                normalized["market_context_evidence"] = None
                missing.append("market_context")
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
            normalized["execution_result"] = (
                normalise_evidence_record(execution, "execution_results_v1")
                if execution else None
            )
            for name, index, dataset in (
                ("protection_evidence", protection_by_corr, "protection_audit"),
                ("risk_deviation_evidence", deviation_by_corr, "risk_deviation"),
            ):
                matches = index.get(correlation_id, []) if correlation_id else []
                if matches:
                    # Audit/observation streams are governed one-to-many
                    # histories. Preserve every exact match; do not collapse a
                    # sequence into a fabricated single "current" state.
                    normalized[name] = [normalise_evidence_record(item, dataset) for item in matches]
                    sources.extend((dataset, item) for item in matches)
                else:
                    normalized[name] = None
                    missing.append(dataset)
            normalized["outcome_authority"] = "trade_truth_v1"
            normalized["_provenance"] = _provenance(sources, missing=missing, issues=issues)
            if not _matches(normalized, filters):
                filtered += 1
                continue
            output.append(normalized)
            joined += int(bool(execution or decision))
            missing_optional += int(bool(missing or issues))
        output.sort(key=lambda row: (_timestamp_sort_key(row.get("exit_time")), str(row["trade_id"])))
        accounting = {
            "source_rows_loaded": {
                "trade_truth": len(truths), "execution_results": len(executions),
                "decision_trace": len(decisions), "protection_audit": len(protections),
                "market_context": len(market_context), "strategy_observations": len(strategy_observations),
                "risk_deviation": len(deviations),
            },
            "primary_rows": len(truths), "eligible_rows": len(truths) - excluded_missing - excluded_ambiguous,
            "joined_rows": joined, "rows_missing_optional_context": missing_optional,
            "rows_excluded_ambiguous_identity": excluded_ambiguous,
            "rows_excluded_missing_required_identity": excluded_missing,
            "duplicate_counts": {"trade_truth_identity_groups": len(duplicate_ids)},
            "filtered_out": filtered, "output_rows": len(output),
        }
        accounting["balanced"] = len(truths) == len(output) + filtered + excluded_missing + excluded_ambiguous
        return InvestigationResult(
            view="trade_investigation", records=tuple(output), accounting=accounting,
            source_schemas={name: current_schema(name) for name in accounting["source_rows_loaded"]},
            join_contracts=(
                JoinContract("trade_truth", "execution_results", "correlation_id", "one-to-zero-or-one"),
                JoinContract("execution_results", "decision_trace", "entity_id; fallback correlation_id", "one-to-zero-or-one"),
                JoinContract("decision_trace", "market_context", "symbol + cycle_id", "one-to-zero-or-one"),
                JoinContract("decision_trace", "strategy_observations", "entity_id", "one-to-zero-or-many-evaluations"),
                JoinContract("trade_truth", "protection_audit/risk_deviation", "correlation_id", "one-to-zero-or-many-history"),
            ),
            limitations=(
                "MAE/MFE remain absent unless trade_truth itself carries them.",
                "Mutable production data is not labelled as a frozen Stage 4 snapshot.",
            ),
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
        for decision in decisions:
            decision_key = str(_first(decision, "decision_id", "entity_id") or "")
            correlation_id = str(decision.get("correlation_id") or "")
            if not decision_key or not correlation_id:
                missing_required += 1
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
                if execution is not None and len(candidates) > 1:
                    account = str(_first(execution, "account_id") or "")
                    scoped = [t for t in candidates if str(_first(t, "identity.account_id") or "") == account]
                    candidates = scoped if account else candidates
                truth = candidates[0] if len(candidates) == 1 else None
                issues = ["trade_truth:ambiguous_correlation_id"] if len(candidates) > 1 else []
                missing = [name for name, value in (("execution_results", execution), ("trade_truth", truth)) if value is None]
                sources: list[tuple[str, Mapping[str, Any]]] = [("decision_trace", decision)]
                if execution: sources.append(("execution_results", execution))
                if truth: sources.append(("trade_truth", truth))
                row = {
                    **_decision_fields(decision),
                    "entity_id": decision.get("entity_id"), "correlation_id": correlation_id,
                    "symbol": decision.get("symbol"),
                    "execution_result": normalise_evidence_record(execution, "execution_results_v1") if execution else None,
                    "realised_outcome": normalise_trade_truth_record(truth) if truth else None,
                    "outcome_authority": "trade_truth_v1" if truth else None,
                    "_provenance": _provenance(sources, missing=missing, issues=issues),
                }
                if _matches(row, filters): rows.append(row)
                else: filtered += 1
        rows.sort(key=lambda row: (str(row.get("decision_timestamp") or ""), str(row.get("decision_id") or "")))
        accounting = {
            "source_rows_loaded": {"decision_trace": len(decisions), "execution_results": len(executions), "trade_truth": len(truths)},
            "primary_rows": len(decisions), "eligible_rows": len(decisions) - missing_required - ambiguous,
            "joined_rows": sum(bool(r["execution_result"] or r["realised_outcome"]) for r in rows),
            "rows_missing_optional_context": sum(bool(r["_provenance"]["missing_optional_evidence"]) for r in rows),
            "rows_excluded_ambiguous_identity": ambiguous,
            "rows_excluded_missing_required_identity": missing_required,
            "duplicate_counts": {"decision_identity_groups": len(duplicate_decisions)},
            "filtered_out": filtered, "output_rows": len(rows),
            "balanced": len(decisions) == missing_required + ambiguous + filtered + len({str(r.get('decision_id')) for r in rows}),
        }
        return InvestigationResult(
            view="decision_execution_outcome", records=tuple(rows), accounting=accounting,
            source_schemas={name: current_schema(name) for name in accounting["source_rows_loaded"]},
            join_contracts=(
                JoinContract("decision_trace", "execution_results", "correlation_id", "one-to-many-account-grained"),
                JoinContract("execution_results", "trade_truth", "correlation_id + account_id where available", "one-to-zero-or-one"),
            ),
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
                    "missing_optional_evidence": ["trade_truth"] if live is None else [],
                    "join_issues": ["trade_truth:ambiguous_canonical_opportunity_id"] if len(live_matches) > 1 else [],
                },
            }
            if _matches(row, filters): rows.append(row)
            else: filtered += 1
        rows.sort(key=lambda row: (str(row["canonical_opportunity_id"]), str(row["shadow_trade_id"])))
        accounting = {
            "source_rows_loaded": {"shadow_runtime": len(events), "trade_truth": len(truths)},
            "primary_rows": len(report.artifacts) + len(report.limitations), "eligible_rows": len(report.artifacts),
            "joined_rows": sum(r["incumbent_outcome"] is not None for r in rows),
            "rows_missing_optional_context": missing_live,
            "rows_excluded_ambiguous_identity": len(report.limitations),
            "ambiguous_incumbent_joins_retained_unpaired": ambiguous_live,
            "rows_excluded_missing_required_identity": 0,
            "duplicate_counts": {"invalid_or_ambiguous_shadow_lifecycles": len(report.limitations)},
            "filtered_out": filtered, "output_rows": len(rows),
            "balanced": len(report.artifacts) == len(rows) + filtered,
        }
        return InvestigationResult(
            view="shadow_vs_incumbent", records=tuple(rows), accounting=accounting,
            source_schemas={"shadow_runtime": current_schema("shadow_runtime"), "trade_truth": current_schema("trade_truth")},
            join_contracts=(JoinContract("shadow_runtime reconstructed outcome", "trade_truth", "canonical_opportunity_id", "many-shadow-horizons-to-zero-or-one-live"),),
            limitations=("Shadow outcomes are simulated; incumbent outcomes are broker-realised.", "No timestamp-proximity matching is performed."),
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
        )

    def build_execution_quality(self, filters: InvestigationFilters = InvestigationFilters()) -> InvestigationResult:
        datasets = ("execution_attempts", "execution_results", "execution_context", "protection_audit", "risk_deviation")
        loaded = {name: self._load(name, filters) for name in datasets}
        primary = loaded["execution_attempts"] + loaded["execution_results"]
        context_indexes = {name: _index(loaded[name], "correlation_id") for name in datasets[2:]}
        rows: list[dict[str, Any]] = []
        missing_required = filtered = missing_optional = ambiguous_optional = 0
        identity_counts: dict[tuple[str, str], int] = defaultdict(int)
        for source_name, source_rows in (("execution_attempts", loaded["execution_attempts"]), ("execution_results", loaded["execution_results"])):
            for raw in source_rows:
                identity_counts[(source_name, _source_identity(source_name, raw))] += 1
        duplicate_keys = {key for key, count in identity_counts.items() if count > 1}
        duplicate_rows = 0
        for source_name, source_rows in (("execution_attempts", loaded["execution_attempts"]), ("execution_results", loaded["execution_results"])):
            for raw in source_rows:
                correlation_id = str(_first(raw, "correlation_id") or "")
                if not correlation_id:
                    missing_required += 1
                    continue
                identity = (source_name, _source_identity(source_name, raw))
                if identity in duplicate_keys:
                    duplicate_rows += 1
                    continue
                sources: list[tuple[str, Mapping[str, Any]]] = [(source_name, raw)]
                missing: list[str] = []
                issues: list[str] = []
                related: dict[str, Any] = {}
                for dataset, index in context_indexes.items():
                    matches = index.get(correlation_id, [])
                    if len(matches) == 1:
                        related[dataset] = normalise_evidence_record(matches[0], dataset)
                        sources.append((dataset, matches[0]))
                    elif len(matches) > 1:
                        related[dataset] = None
                        issues.append(f"{dataset}:ambiguous_correlation_id")
                    else:
                        related[dataset] = None
                        missing.append(dataset)
                row = {
                    **normalise_evidence_record(raw, "execution_results_v1" if source_name == "execution_results" else source_name),
                    "record_kind": source_name, "correlation_id": correlation_id,
                    "related_evidence": related,
                    "_provenance": _provenance(sources, missing=missing, issues=issues),
                }
                if _matches(row, filters): rows.append(row)
                else: filtered += 1
                missing_optional += int(bool(missing)); ambiguous_optional += int(bool(issues))
        rows.sort(key=lambda row: (str(row.get("correlation_id")), str(row.get("record_kind")), _hash(row)))
        accounting = {
            "source_rows_loaded": {name: len(value) for name, value in loaded.items()},
            "primary_rows": len(primary), "eligible_rows": len(primary) - missing_required - duplicate_rows,
            "joined_rows": sum(bool(r["_provenance"]["sources"][1:]) for r in rows),
            "rows_missing_optional_context": missing_optional,
            "rows_with_ambiguous_optional_context": ambiguous_optional,
            "rows_excluded_ambiguous_identity": duplicate_rows,
            "rows_excluded_missing_required_identity": missing_required,
            "duplicate_counts": {"ambiguous_source_identity_rows": duplicate_rows, "ambiguous_identity_groups": len(duplicate_keys)},
            "filtered_out": filtered, "output_rows": len(rows),
            "balanced": len(primary) == len(rows) + filtered + missing_required + duplicate_rows,
        }
        return InvestigationResult(
            view="execution_quality", records=tuple(rows), accounting=accounting,
            source_schemas={name: current_schema(name) for name in datasets},
            join_contracts=tuple(JoinContract("execution_attempts/results", name, "correlation_id", "one-to-zero-or-one") for name in datasets[2:]),
            limitations=("Multiple protection/audit observations at one correlation are reported as ambiguous, not collapsed.",),
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
