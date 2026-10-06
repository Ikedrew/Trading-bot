"""Canonical HD13 all-70 dataset-suitability audit (G1)."""
from __future__ import annotations

from collections import Counter
from typing import Any, Mapping

from research_engine.control_plane.data_governance_snapshot import (
    CurrentSnapshot, freeze_current_snapshot, validate_snapshot_manifest,
)
from research_engine.control_plane.evidence_resolver import EvidenceSnapshot, resolve_question_evidence
from research_engine.experiments.experiment_base import build_report
from research_engine.registry import data_governance_adjudication as A
from research_engine.registry.definition_validator import (
    build_definitions_from_registry, validate_all_definitions,
)
from research_engine.registry.research_question_registry import REGISTRY

# Governed evaluator semantic identity; see component_reward for the contract.
EVALUATOR_SEMANTIC_VERSIONS = {
    "run_g1": "g1_bounded_metadata_record_validation_v2",
}
EVALUATOR_REPORT_SCHEMA_VERSIONS = {
    "run_g1": {"GOVERNED_REASON_SCHEMA": "governed_reason_v1"},
}
EVALUATOR_GOVERNANCE_CONTRACT_VERSIONS = {
    "run_g1": {
        "HD13_VERSION": A.HD13_VERSION,
        "ADJUDICATION_VERSION": A.ADJUDICATION_VERSION,
    },
}

REPORT_FILENAME = "g1_dataset_suitability.json"
_REQ_STATUSES = frozenset(A.G1_REQUIREMENT_STATUSES)
_QUESTION_STATUSES = frozenset(A.G1_QUESTION_STATUSES)


def _load_persisted(source: str) -> list[dict[str, Any]]:
    from research_engine.data_access.s3_source import get_default_source
    physical = {
        "execution_results_v1": "execution_results", "protection_audit_v1": "protection_audit",
        "execution_attempts_v1": "execution_attempts", "risk_deviation_v1": "risk_deviation",
        "shadow_trades": "research_shadow_trades",
    }.get(source, source)
    return list(get_default_source().read_dataset(physical))


def _snapshot(
    datasets: Mapping[str, list[dict[str, Any]]] | None,
    as_of_utc: str | None,
    *,
    retain_record_payloads: bool | None = None,
) -> CurrentSnapshot:
    definitions = build_definitions_from_registry(REGISTRY)
    from research_engine.control_plane.evidence_resolver import authoritative_evidence_schema
    sources = sorted(
        {source.value for question in REGISTRY for source in question.data_sources}
        | {"opportunities", "assessments"}
        | {authority.dataset for definition in definitions.values() for authority in definition.evidence_authorities
           if authoritative_evidence_schema(authority.dataset) is not None}
    )
    loaded = {
        source: datasets[source] if datasets is not None and source in datasets else []
        for source in sources
    }
    if datasets is None:
        loaded = {source: _load_persisted(source) for source in sources}
    return freeze_current_snapshot(
        loaded,
        registry_material=[question.to_dict() for question in REGISTRY],
        definition_material={key: value.to_dict() for key, value in definitions.items()},
        contract_material={"HD13": A.HD13_VERSION, "snapshot": A.G1_SNAPSHOT_CONTRACT},
        as_of_utc=as_of_utc,
        # The canonical-cycle datasets are already held by its verified reader.
        # Retain only G1's authoritative counts/digests and defensively borrow
        # one source at a time while requirements are evaluated.
        retain_record_payloads=(
            datasets is None
            if retain_record_payloads is None
            else retain_record_payloads
        ),
    )


def _row(qid: str, category: str, identity: str, status: str, reason: str,
         snapshot: CurrentSnapshot, source: str | None = None, observed: Any = None) -> dict[str, Any]:
    component = snapshot.component(source) if source else None
    return {
        "question_id": qid, "category": category, "requirement_id": identity,
        "status": status, "reason": reason, "observed": observed,
        "provenance": {
            "snapshot_id": snapshot.snapshot_id, "as_of_utc": snapshot.as_of_utc,
            "source": source or "canonical_registry_definition",
            "component_digest": component["digest"] if component else snapshot.definition_digest,
        },
    }


def _question_status(rows: list[dict[str, Any]]) -> str:
    science_failed = any(item["category"] == "SCIENCE_CONTRACT" and item["status"] == "FAIL" for item in rows)
    if science_failed:
        return "BLOCKED"
    if any(item["status"] == "UNKNOWN" for item in rows):
        return "UNKNOWN"
    if any(item["status"] == "FAIL" for item in rows):
        return "UNSUITABLE"
    if any(item["status"] == "INSUFFICIENT" for item in rows):
        return "WAITING_DATA"
    return "SUITABLE"


def _overall_status(statuses: list[str]) -> str:
    for status in ("BLOCKED", "UNKNOWN", "UNSUITABLE", "WAITING_DATA"):
        if status in statuses:
            return status
    return "SUITABLE"


def frozen_requirement_ids(question: Any) -> set[str]:
    """Return the exact nonrecursive HD13 inventory identities for one question."""
    qid = question.id
    identities = {f"{qid}:science_contract"}
    identities.update(f"{qid}:source:{source.value}" for source in question.data_sources)
    identities.update(f"{qid}:field:{field}" for field in question.required_fields)
    identities.add(f"{qid}:join:{'declared' if len(question.data_sources) >= 2 else 'not_applicable'}")
    if question.validation_rules:
        identities.update(
            f"{qid}:sample:{index}:{rule.field}"
            for index, rule in enumerate(question.validation_rules)
        )
    else:
        identities.add(f"{qid}:sample:not_applicable")
    identities.update(f"{qid}:current:{source.value}" for source in question.data_sources)
    return identities


def assess_g1(snapshot: CurrentSnapshot) -> list[dict[str, Any]]:
    """Assess each canonical definition once without reading any G1 report/state."""
    definitions = build_definitions_from_registry(REGISTRY)
    health = validate_all_definitions(definitions)
    assessments: list[dict[str, Any]] = []
    for question in REGISTRY:
        qid = question.id
        # A question needs at most its declared sources.  A short-lived cache
        # prevents G1 from retaining the complete record bodies of all sources.
        evidence_snapshot = EvidenceSnapshot(loader=snapshot.records)
        resolution = resolve_question_evidence(question, evidence_snapshot)
        rows: list[dict[str, Any]] = []
        science = health[qid]
        science_blocked = science.has_errors or science.is_under_specified
        rows.append(_row(
            qid, "SCIENCE_CONTRACT", f"{qid}:science_contract", "FAIL" if science_blocked else "PASS",
            "Frozen effective definition is blocked/under-specified" if science_blocked else "Frozen effective definition is internally resolvable",
            snapshot, observed=science.to_dict(),
        ))
        source_state = {item.name: item for item in resolution.requirements if item.type == "dataset_presence"}
        for source in dict.fromkeys(item.value for item in question.data_sources):
            result = source_state.get(source)
            passed = bool(result and result.satisfied is True)
            rows.append(_row(qid, "SOURCE_AUTHORITY", f"{qid}:source:{source}", "PASS" if passed else "FAIL",
                             result.reason if result else "Declared source was not resolved", snapshot, source, result.current if result else None))
        fields = {item.name: item for item in resolution.requirements if item.type == "required_field"}
        population = int(resolution.metrics.get("total_current_population", 0) or 0)
        for field in question.required_fields:
            result = fields.get(field)
            if result is None or result.satisfied is None:
                status = "UNKNOWN"
            elif population == 0:
                status = "PASS"  # declared schema path; emptiness belongs to sample sufficiency
            else:
                status = "PASS" if int(result.current or 0) == population else "FAIL"
            rows.append(_row(qid, "FIELD_COVERAGE", f"{qid}:field:{field}", status,
                             result.reason if result else "Required field could not be assessed", snapshot, observed=result.current if result else None))
        joins = [item for item in resolution.requirements if item.type == "join_integrity"]
        if len(question.data_sources) < 2:
            rows.append(_row(qid, "JOINABILITY", f"{qid}:join:not_applicable", "NOT_APPLICABLE",
                             "No multi-source join is declared", snapshot))
        elif joins:
            status = "FAIL" if any(item.satisfied is False for item in joins) else "UNKNOWN" if any(item.satisfied is None for item in joins) else "PASS"
            rows.append(_row(qid, "JOINABILITY", f"{qid}:join:declared", status,
                             "; ".join(item.reason for item in joins), snapshot))
        else:
            status = "INSUFFICIENT" if population == 0 else "PASS"
            rows.append(_row(qid, "JOINABILITY", f"{qid}:join:declared", status,
                             "Declared canonical join has zero rows" if population == 0 else "Declared canonical join resolved without reported conflict", snapshot))
        samples = [item for item in resolution.requirements if item.type in {"coverage", "sample_size", "population_count", "unsupported"}]
        if not question.validation_rules:
            rows.append(_row(qid, "SAMPLE_SUFFICIENCY", f"{qid}:sample:not_applicable", "NOT_APPLICABLE",
                             f"No frozen threshold; observed n={resolution.usable_count}", snapshot, observed=resolution.usable_count))
        else:
            for index, rule in enumerate(question.validation_rules):
                result = samples[index] if index < len(samples) else None
                status = "UNKNOWN" if result is None or result.satisfied is None else "PASS" if result.satisfied else "INSUFFICIENT"
                rows.append(_row(qid, "SAMPLE_SUFFICIENCY", f"{qid}:sample:{index}:{rule.field}", status,
                                 result.reason if result else "Frozen sample rule could not be assessed", snapshot, observed=result.current if result else None))
        for source in dict.fromkeys(item.value for item in question.data_sources):
            component = snapshot.component(source)
            status = "PASS" if component and component["state"] == "CURRENT" else "FAIL"
            rows.append(_row(qid, "CURRENT_PROVENANCE", f"{qid}:current:{source}", status,
                             "Bound to immutable CURRENT snapshot component" if component else "No CURRENT snapshot component", snapshot, source))
        assessments.append({"question_id": qid, "status": _question_status(rows), "requirements": rows})
    return assessments


def run_g1(*, datasets: Mapping[str, list[dict[str, Any]]] | None = None,
           snapshot: CurrentSnapshot | None = None, as_of_utc: str | None = None) -> dict[str, Any]:
    frozen = snapshot or _snapshot(datasets, as_of_utc)
    assessments = assess_g1(frozen)
    statuses = [item["status"] for item in assessments]
    overall_status = _overall_status(statuses)
    has_unknown = "UNKNOWN" in statuses
    manifest = frozen.manifest()
    fingerprint = {
        "dataset_id": frozen.snapshot_id, "records_used": manifest["records_used"],
        "records_excluded": manifest["records_excluded"], "source": "MULTI_SOURCE",
        "sources": [item["source"] for item in manifest["components"]], "epoch": "CURRENT",
        "architecture_version": "new_pipeline_v1.2", "validation_score": "CURRENT",
    }
    return build_report(
        question_id="G1", status="BLOCKED" if has_unknown else "COMPLETE",
        overall={
            "finding": overall_status, "estimand": "HD13 evidence suitability across exactly 70 canonical questions",
            "question_count": len(assessments), "status_counts": dict(Counter(statuses)),
            "requirement_count": sum(len(item["requirements"]) for item in assessments),
            "assessments": assessments, "unknown_prevents_completion": has_unknown,
        },
        confidence="INSUFFICIENT_DATA" if has_unknown else "HIGH",
        dataset={"source": "immutable CURRENT canonical evidence snapshot", "sample_size": 70},
        fingerprint=fingerprint, recommendation=overall_status,
        reason_code=("HD13_UNKNOWN_REQUIREMENTS" if has_unknown else None),
        reason=(
            "HD13 assessment contains UNKNOWN requirements; completion is blocked."
            if has_unknown else None
        ),
        assumptions=["G1 is evaluated nonrecursively and its own result is never evidence."],
        provenance={"experiment_module": __name__, "registry_id": "G1", "scientific_owner": "G1",
                    "contract_version": A.HD13_VERSION, "report_identity": REPORT_FILENAME, "snapshot": manifest},
    )


def validate_g1_report(report: dict[str, Any]) -> tuple[bool, str]:
    if report.get("question_id") != "G1":
        return False, "G1 report has wrong canonical identity"
    provenance = report.get("provenance", {})
    if provenance.get("report_identity") != REPORT_FILENAME or provenance.get("contract_version") != A.HD13_VERSION:
        return False, "G1 ownership or HD13 contract identity is invalid"
    valid, reason = validate_snapshot_manifest(provenance.get("snapshot"))
    if not valid:
        return False, reason
    assessments = report.get("overall", {}).get("assessments")
    if not isinstance(assessments, list) or len(assessments) != 70:
        return False, "G1 must represent exactly 70 question assessments"
    ids = [item.get("question_id") for item in assessments if isinstance(item, dict)]
    if len(set(ids)) != 70 or set(ids) != {question.id for question in REGISTRY}:
        return False, "G1 question population is duplicated or incomplete"
    snapshot_id = provenance["snapshot"]["snapshot_id"]
    for item in assessments:
        rows = item.get("requirements")
        if not isinstance(rows, list) or {row.get("category") for row in rows} != set(A.G1_REQUIREMENT_CATEGORIES):
            return False, f"G1 requirement inventory is incomplete for {item.get('question_id')}"
        requirement_ids = [row.get("requirement_id") for row in rows]
        if len(requirement_ids) != len(set(requirement_ids)):
            return False, "G1 contains duplicate requirement identities"
        question = next(question for question in REGISTRY if question.id == item["question_id"])
        if set(requirement_ids) != frozen_requirement_ids(question):
            return False, f"G1 silently omitted or added a requirement for {item['question_id']}"
        if any(row.get("status") not in _REQ_STATUSES or row.get("provenance", {}).get("snapshot_id") != snapshot_id for row in rows):
            return False, "G1 requirement status/provenance is invalid"
        if item.get("status") not in _QUESTION_STATUSES or item.get("status") != _question_status(rows):
            return False, "G1 question aggregation is invalid"
    statuses = [item["status"] for item in assessments]
    if report.get("overall", {}).get("finding") != _overall_status(statuses):
        return False, "G1 overall aggregation is invalid"
    expected_report_status = "BLOCKED" if "UNKNOWN" in statuses else "COMPLETE"
    if report.get("status") != expected_report_status:
        return False, "UNKNOWN prevents G1 completion and exhaustive known findings must complete"
    if report.get("overall", {}).get("requirement_count") != sum(len(item["requirements"]) for item in assessments):
        return False, "G1 requirement accounting total is inconsistent"
    if report.get("overall", {}).get("status_counts") != dict(Counter(statuses)):
        return False, "G1 question status accounting is inconsistent"
    return True, "Owned exhaustive HD13 G1 report is valid"
