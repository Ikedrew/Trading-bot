"""Canonical HD14 decision-to-outcome lineage coverage audit (G2)."""
from __future__ import annotations

from collections import Counter, defaultdict
import math
from typing import Any, Iterable, Mapping

from research_engine.control_plane.data_governance_snapshot import (
    CurrentSnapshot, freeze_current_snapshot, validate_snapshot_manifest,
)
from research_engine.experiments.experiment_base import build_report
from research_engine.registry import data_governance_adjudication as A
from research_engine.registry.definition_validator import build_definitions_from_registry
from research_engine.registry.research_question_registry import REGISTRY

# Governed evaluator semantic identity; see component_reward for the contract.
EVALUATOR_SEMANTIC_VERSIONS = {
    "run_g2": "g2_exhaustive_snapshot_scoped_lineage_v2",
}
EVALUATOR_GOVERNANCE_CONTRACT_VERSIONS = {
    "run_g2": {
        "HD14_VERSION": A.HD14_VERSION,
        "ADJUDICATION_VERSION": A.ADJUDICATION_VERSION,
    },
}

REPORT_FILENAME = "g2_lineage_coverage.json"
_METADATA = ("symbol", "strategy", "strategy_family", "evaluated_horizon", "trade_horizon", "horizon")


def _value(record: Any, names: tuple[str, ...]) -> Any:
    if not isinstance(record, dict):
        return None
    for name in names:
        value = record.get(name)
        if value is not None and value != "":
            return value
    for value in record.values():
        if isinstance(value, dict):
            found = _value(value, names)
            if found is not None and found != "":
                return found
    return None


def _identity(record: dict[str, Any]) -> tuple[str, str] | None:
    entity = _value(record, ("entity_id",))
    opportunity = _value(record, ("canonical_opportunity_id",))
    if entity is None or opportunity is None or not str(entity).strip() or not str(opportunity).strip():
        return None
    return str(entity), str(opportunity)


def _eligible_outcome(record: dict[str, Any]) -> bool:
    shadow_type = str(_value(record, ("shadow_type",)) or "").upper()
    return not shadow_type or shadow_type == "PRIMARY_HORIZON_SIMULATION"


def _finite_outcome(record: dict[str, Any]) -> bool:
    value = _value(record, ("r_multiple", "pnl_r_multiple", "pnl_r", "r_multiple_realised"))
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _collapse_lifecycles(records: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Collapse a declared OPEN+CLOSE lifecycle, never distinct outcomes."""
    events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    flat: list[dict[str, Any]] = []
    excluded_alternative = 0
    for record in records:
        if not _eligible_outcome(record):
            excluded_alternative += 1
            continue
        event_type = str(_value(record, ("event_type",)) or "").upper()
        trade_id = str(_value(record, ("shadow_trade_id", "trade_id")) or "")
        if event_type in {"OPEN", "CLOSE"} and trade_id:
            events[trade_id].append(record)
        else:
            flat.append(record)
    for grouped in events.values():
        opens = [item for item in grouped if str(_value(item, ("event_type",)) or "").upper() == "OPEN"]
        closes = [item for item in grouped if str(_value(item, ("event_type",)) or "").upper() == "CLOSE"]
        if len(opens) == 1 and len(closes) == 1:
            merged = dict(opens[0])
            merged.update(closes[0])
            merged["_lifecycle_pair"] = True
            flat.append(merged)
        else:
            flat.extend(grouped)
    return flat, excluded_alternative


def _metadata_conflict(decisions: list[dict[str, Any]], outcomes: list[dict[str, Any]]) -> bool:
    for name in _METADATA:
        left = {str(_value(item, (name,))) for item in decisions if _value(item, (name,)) not in (None, "")}
        right = {str(_value(item, (name,))) for item in outcomes if _value(item, (name,)) not in (None, "")}
        if len(left) > 1 or len(right) > 1 or (left and right and left != right):
            return True
    return False


def classify_lineage(
    decision_records: Iterable[dict[str, Any]], outcome_records: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Classify the exhaustive composite-key union exactly once."""
    decisions_by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    outcomes_by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    unresolved: list[dict[str, Any]] = []
    for record in decision_records:
        key = _identity(record)
        if key is None:
            unresolved.append({"side": "DECISION", "reason": "incomplete composite identity"})
        else:
            decisions_by_key[key].append(record)
    collapsed, excluded_alternatives = _collapse_lifecycles(outcome_records)
    for record in collapsed:
        key = _identity(record)
        if key is None:
            unresolved.append({"side": "OUTCOME", "reason": "incomplete composite identity"})
        else:
            outcomes_by_key[key].append(record)
    keys = sorted(set(decisions_by_key) | set(outcomes_by_key))
    classifications: list[dict[str, Any]] = []
    for key in keys:
        decisions, outcomes = decisions_by_key[key], outcomes_by_key[key]
        completed = [item for item in outcomes if _finite_outcome(item)]
        if _metadata_conflict(decisions, outcomes) or len(completed) > 1:
            classification, reason = "CONFLICTING", "canonical metadata or completed outcome multiplicity conflicts"
        elif len(decisions) != 1 or len(outcomes) != 1:
            if len(decisions) > 1 or len(outcomes) > 1:
                classification, reason = "AMBIGUOUS", "multiple plausible records prevent a determinate pair"
            else:
                classification, reason = "MISSING", "decision side missing" if not decisions else "completed outcome side missing"
        elif not completed:
            classification, reason = "MISSING", "completed outcome side missing"
        else:
            classification, reason = "VALID", "one decision and one finite completed outcome agree"
        classifications.append({
            "entity_id": key[0], "canonical_opportunity_id": key[1],
            "classification": classification, "reason": reason,
            "decision_records": len(decisions), "outcome_records": len(outcomes),
            "completed_outcomes": len(completed),
        })
    counts = Counter(item["classification"] for item in classifications)
    for classification in A.G2_CLASSIFICATIONS:
        counts.setdefault(classification, 0)
    denominator = len(classifications)
    coverage = counts["VALID"] / denominator if denominator else None
    return {
        "identity": ["entity_id", "canonical_opportunity_id"],
        "classifications": classifications, "class_counts": dict(counts),
        "valid": counts["VALID"], "denominator": denominator,
        "coverage": coverage, "unresolved_orphans": unresolved,
        "unresolved_orphan_count": len(unresolved),
        "excluded_alternative_horizon_records": excluded_alternatives,
    }


def _load_persisted() -> dict[str, list[dict[str, Any]]]:
    from research_engine.data_access.s3_source import get_default_source
    source = get_default_source()
    return {
        "decision_trace": list(source.read_dataset("decision_trace")),
        "shadow_runtime": list(source.read_dataset("shadow_runtime")),
    }


def _snapshot(datasets: Mapping[str, list[dict[str, Any]]] | None, as_of_utc: str | None) -> CurrentSnapshot:
    supplied = dict(datasets) if datasets is not None else _load_persisted()
    if "decision_trace" not in supplied:
        supplied["decision_trace"] = []
    if "shadow_runtime" not in supplied and "shadow_trades" not in supplied:
        supplied["shadow_trades"] = []
    definitions = build_definitions_from_registry(REGISTRY)
    return freeze_current_snapshot(
        supplied, registry_material=[question.to_dict() for question in REGISTRY],
        definition_material={key: value.to_dict() for key, value in definitions.items()},
        contract_material={"HD14": A.HD14_VERSION, "snapshot": A.G2_SNAPSHOT_CONTRACT},
        as_of_utc=as_of_utc,
    )


def _current_audit_inputs(snapshot: CurrentSnapshot, source: str) -> list[dict[str, Any]]:
    """Retain current-schema partial identities so they become visible orphans."""
    component = snapshot.component(source)
    if component is None:
        return []
    schema = component["schema"]
    rows = []
    for record in snapshot.input_records(source):
        epoch = str(_value(record, ("data_epoch", "epoch")) or "CURRENT").upper()
        if record.get("schema_version") == schema and epoch in {"CURRENT", "CURRENT_ONLY", "SHADOW_TRADES_CURRENT"}:
            rows.append(record)
    return rows


def run_g2(*, decision_records: list[dict[str, Any]] | None = None,
           outcome_records: list[dict[str, Any]] | None = None,
           datasets: Mapping[str, list[dict[str, Any]]] | None = None,
           snapshot: CurrentSnapshot | None = None, as_of_utc: str | None = None,
           governed_records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if governed_records is not None:
        from research_engine.control_plane.stage4_impl_population2 import (
            enforce_exact_population, filter_sources_to_governed_population,
        )
        governed = enforce_exact_population("G2", governed_records)
        decisions, outcomes = filter_sources_to_governed_population(
            "G2", governed, decision_records or (), outcome_records or ())
        result = classify_lineage(decisions, outcomes)
        if result["denominator"] != len(governed):
            raise ValueError(
                f"G2_RUNNER_POPULATION_MISMATCH:{result['denominator']}!={len(governed)}")
        frozen = _snapshot({"decision_trace": decisions, "shadow_trades": outcomes}, as_of_utc)
    else:
        result = None
    if decision_records is not None or outcome_records is not None:
        if datasets is not None or snapshot is not None:
            raise ValueError("Supply either explicit records, datasets, or a snapshot")
        datasets = {"decision_trace": decision_records or [], "shadow_trades": outcome_records or []}
    frozen = frozen if governed_records is not None else (snapshot or _snapshot(datasets, as_of_utc))
    if result is None:
        decisions = _current_audit_inputs(frozen, "decision_trace")
        outcome_source = "shadow_runtime" if frozen.component("shadow_runtime") else "shadow_trades"
        outcomes = _current_audit_inputs(frozen, outcome_source)
        result = classify_lineage(decisions, outcomes)
    denominator = result["denominator"]
    unresolved = result["unresolved_orphan_count"]
    if unresolved:
        status, threshold = "BLOCKED", "UNRESOLVED_ACCOUNTING"
    elif denominator < A.G2_MINIMUM_ELIGIBLE_OPPORTUNITIES:
        status, threshold = "INSUFFICIENT_DATA", "INSUFFICIENT_DATA"
    elif result["coverage"] >= A.G2_COVERAGE_THRESHOLD:
        status, threshold = "COMPLETE", "LINEAGE_THRESHOLD_MET"
    else:
        status, threshold = "COMPLETE", "LINEAGE_THRESHOLD_NOT_MET"
    result["threshold"] = A.G2_COVERAGE_THRESHOLD
    result["minimum_denominator"] = A.G2_MINIMUM_ELIGIBLE_OPPORTUNITIES
    result["threshold_result"] = threshold
    result["finding"] = threshold
    manifest = frozen.manifest()
    fingerprint = {
        "dataset_id": frozen.snapshot_id, "records_used": manifest["records_used"],
        "records_excluded": manifest["records_excluded"], "source": "MULTI_SOURCE",
        "sources": [item["source"] for item in manifest["components"]], "epoch": "CURRENT",
        "architecture_version": "new_pipeline_v1.2", "validation_score": "CURRENT",
    }
    return build_report(
        question_id="G2", status=status, overall=result,
        confidence="HIGH" if status == "COMPLETE" else "INSUFFICIENT_DATA",
        dataset={"source": "immutable CURRENT decision/outcome snapshot", "sample_size": denominator},
        fingerprint=fingerprint, recommendation=threshold,
        assumptions=["No entity-only, correlation-ID, account, broker, or horizon fallback identity is used."],
        provenance={"experiment_module": __name__, "registry_id": "G2", "scientific_owner": "G2",
                    "contract_version": A.HD14_VERSION, "report_identity": REPORT_FILENAME, "snapshot": manifest},
    )


def _contains_interval(value: Any) -> bool:
    if isinstance(value, dict):
        return any("interval" in str(key).lower() or "confidence" in str(key).lower() for key in value) or any(_contains_interval(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_interval(item) for item in value)
    return False


def validate_g2_report(report: dict[str, Any]) -> tuple[bool, str]:
    if report.get("question_id") != "G2":
        return False, "G2 report has wrong canonical identity"
    provenance = report.get("provenance", {})
    if provenance.get("report_identity") != REPORT_FILENAME or provenance.get("contract_version") != A.HD14_VERSION:
        return False, "G2 ownership or HD14 contract identity is invalid"
    valid, reason = validate_snapshot_manifest(provenance.get("snapshot"))
    if not valid:
        return False, reason
    overall = report.get("overall", {})
    if overall.get("identity") != ["entity_id", "canonical_opportunity_id"]:
        return False, "G2 composite identity is invalid"
    rows = overall.get("classifications")
    if not isinstance(rows, list):
        return False, "G2 classifications are missing"
    keys = [(item.get("entity_id"), item.get("canonical_opportunity_id")) for item in rows if isinstance(item, dict)]
    if len(keys) != len(set(keys)) or any(not all(key) for key in keys):
        return False, "G2 denominator identities are missing or duplicated"
    counts = Counter(item.get("classification") for item in rows)
    if set(counts) - set(A.G2_CLASSIFICATIONS):
        return False, "G2 has a noncanonical classification"
    expected = {name: counts.get(name, 0) for name in A.G2_CLASSIFICATIONS}
    if overall.get("class_counts") != expected or overall.get("denominator") != len(rows):
        return False, "G2 exhaustive class accounting is inconsistent"
    denominator = len(rows)
    coverage = expected["VALID"] / denominator if denominator else None
    if overall.get("valid") != expected["VALID"] or overall.get("coverage") != coverage:
        return False, "G2 coverage is not exactly VALID / D"
    if _contains_interval(overall):
        return False, "HD14 does not permit an invented confidence-interval requirement"
    orphan_count = overall.get("unresolved_orphan_count")
    expected_status = (
        "BLOCKED" if orphan_count else
        "INSUFFICIENT_DATA" if denominator < A.G2_MINIMUM_ELIGIBLE_OPPORTUNITIES else
        "COMPLETE"
    )
    if report.get("status") != expected_status:
        return False, "G2 report status contradicts denominator readiness/accounting"
    expected_threshold = (
        "UNRESOLVED_ACCOUNTING" if orphan_count else
        "INSUFFICIENT_DATA" if denominator < A.G2_MINIMUM_ELIGIBLE_OPPORTUNITIES else
        "LINEAGE_THRESHOLD_MET" if coverage is not None and coverage >= A.G2_COVERAGE_THRESHOLD else
        "LINEAGE_THRESHOLD_NOT_MET"
    )
    if overall.get("threshold_result") != expected_threshold:
        return False, "G2 point-estimate threshold result is invalid"
    return True, "Owned exhaustive HD14 G2 report is valid"
