"""Read-only production binding for the Stage IV Wave 2 -> 3 -> 4 chain.

The canonical S3 data source is the only production evidence reader used here.
No local-log fallback exists.  A missing AWS profile/credential therefore
raises ``ResearchDataSourceError`` instead of manufacturing an empty audit.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from core.production_data_contract import PRODUCTION_SCHEMA_REGISTRY, current_schema
from research_engine.control_plane.evidence_resolver import EvidenceSnapshot
from research_engine.control_plane.report_resolver import (
    load_report_for_question,
    resolve_report_validity,
)
from research_engine.control_plane.state_builder import build_all_question_states
from research_engine.data_access.s3_source import S3ResearchDataSource, get_default_source
from research_engine.registry.research_question_registry import REGISTRY
from research_engine.v10.universes.evidence_integrity import (
    INTEGRITY_SCHEMA_VERSION,
    EvidenceBatch,
    EvidenceManifest,
    IntegrityFinding,
    IntegrityReport,
    ReconstructedArtifact,
    ReconstructionLimitation,
    ShadowReconstructionResult,
    build_report,
    reconstruct_shadow_outcomes_report,
)
from research_engine.v10.universes.question_qualification import (
    QUALIFICATION_SCHEMA_VERSION,
    AssuranceContradiction,
    QualificationEngine,
    QuestionEvidenceInput,
    QuestionQualification,
    QuestionQualificationReport,
    SufficiencyAssessment,
    StatisticalState,
    build_question_evidence_contracts,
    universes_for_dataset,
)
from research_engine.v10.universes.reconciliation import (
    RECONCILIATION_SCHEMA_VERSION,
    ReconciliationEngine,
    ReconciliationInput,
    ReconciliationReport,
    ReconciliationResult,
    ReconciliationTrace,
    SemanticCheck,
)


_PHYSICAL_DATASET = {
    "execution_results_v1": "execution_results",
    "execution_attempts_v1": "execution_attempts",
    "protection_audit_v1": "protection_audit",
    "risk_deviation_v1": "risk_deviation",
}

CHECKPOINT_SCHEMA_VERSION = 1


class CheckpointValidationError(RuntimeError):
    """A checkpoint cannot safely be combined with the requested snapshot."""


@dataclass(frozen=True)
class CanonicalAnswerBinding:
    question_id: str
    report_filename: str
    report_path: str
    validity: str
    validity_reason: str
    status: str
    result: Any
    finding: str
    sample_size: int | None
    fingerprint: Mapping[str, Any]
    provenance: Mapping[str, Any]


@dataclass(frozen=True)
class ProductionQualificationBundle:
    as_of_utc: str
    dataset_counts: Mapping[str, int]
    answers: tuple[CanonicalAnswerBinding, ...]
    batches: tuple[EvidenceBatch, ...]
    integrity: IntegrityReport
    reconciliation: ReconciliationReport
    qualification: QuestionQualificationReport

    def summary(self) -> dict[str, Any]:
        return {
            "as_of_utc": self.as_of_utc,
            "dataset_counts": dict(self.dataset_counts),
            "answers": [asdict(item) for item in self.answers],
            "integrity": self.integrity.to_dict(),
            "reconciliation": self.reconciliation.to_dict(),
            "qualification": self.qualification.to_dict(),
        }


def _physical(name: str) -> str:
    return _PHYSICAL_DATASET.get(name, name)


def _report_status(report: Mapping[str, Any] | None) -> str:
    if not report:
        return "NO_CURRENT_RESULT"
    return str(report.get("status") or (report.get("overall") or {}).get("status") or "UNKNOWN")


def _finding(report: Mapping[str, Any] | None) -> str:
    if not report:
        return ""
    overall = report.get("overall")
    if isinstance(overall, Mapping):
        return str(overall.get("finding") or overall.get("conclusion") or "")
    return ""


def _sample(report: Mapping[str, Any] | None) -> int | None:
    if not report:
        return None
    candidates = [
        (report.get("dataset") or {}).get("sample_size") if isinstance(report.get("dataset"), Mapping) else None,
        (report.get("overall") or {}).get("sample_size") if isinstance(report.get("overall"), Mapping) else None,
        (report.get("overall") or {}).get("total_trades") if isinstance(report.get("overall"), Mapping) else None,
    ]
    for value in candidates:
        if isinstance(value, (int, float)):
            return int(value)
    return None


def bind_canonical_answers(reports_dir: str | Path = "analysis/reports") -> tuple[CanonicalAnswerBinding, ...]:
    """Bind owned reports without treating stale/legacy artifacts as current."""
    bindings = []
    for question in REGISTRY:
        report, path = load_report_for_question(
            question.id, question.report_filename, reports_dir=reports_dir
        )
        validity, reason = resolve_report_validity(
            question.report_filename,
            report,
            expected_question_id=question.id,
            accepted_question_ids=question.legacy_ids,
        )
        overall = report.get("overall") if isinstance(report, Mapping) else None
        bindings.append(CanonicalAnswerBinding(
            question_id=question.id,
            report_filename=question.report_filename,
            report_path=path if report is not None else "",
            validity=validity.value,
            validity_reason=reason,
            status=_report_status(report),
            result=dict(overall) if isinstance(overall, Mapping) else None,
            finding=_finding(report),
            sample_size=_sample(report),
            fingerprint=dict(report.get("fingerprint") or {}) if isinstance(report, Mapping) else {},
            provenance=dict(report.get("provenance") or {}) if isinstance(report, Mapping) else {},
        ))
    return tuple(bindings)


def required_production_datasets() -> tuple[str, ...]:
    """Return the deduplicated physical dataset scan inventory."""
    requested = {_physical(source.value) for question in REGISTRY for source in question.data_sources}
    # Wave 1 primary lifecycle authority is required for Wave 3 shadow
    # reconciliation even though canonical questions consume terminal rows.
    requested.add("shadow_runtime")
    return tuple(sorted(name for name in requested if name in PRODUCTION_SCHEMA_REGISTRY))


def _timestamp(record: Mapping[str, Any]) -> str:
    candidates = (
        record.get("timestamp_utc"), record.get("entry_time"), record.get("exit_time"),
        (record.get("timestamps") or {}).get("entry_timestamp_broker") if isinstance(record.get("timestamps"), Mapping) else None,
        (record.get("decision_snapshot") or {}).get("timestamp_decision_utc") if isinstance(record.get("decision_snapshot"), Mapping) else None,
    )
    return next((str(value) for value in candidates if value not in (None, "")), "")


def _scope(records: list[dict[str, Any]]) -> dict[str, Any]:
    symbols, accounts, brokers = set(), set(), set()
    for record in records:
        identity = record.get("identity") if isinstance(record.get("identity"), Mapping) else {}
        for target, names in (
            (symbols, ("symbol", "broker_symbol")),
            (accounts, ("account_id", "account")),
            (brokers, ("broker", "broker_id")),
        ):
            for name in names:
                value = record.get(name) or identity.get(name)
                if value not in (None, ""):
                    target.add(str(value))
    timestamps = sorted(value for value in (_timestamp(record) for record in records) if value)
    return {
        "symbols": sorted(symbols), "accounts": sorted(accounts), "brokers": sorted(brokers),
        "first_timestamp": timestamps[0] if timestamps else "",
        "last_timestamp": timestamps[-1] if timestamps else "",
    }


def _batches(datasets: Mapping[str, list[dict[str, Any]]]) -> tuple[EvidenceBatch, ...]:
    batches = []
    for dataset in sorted(datasets):
        records = datasets[dataset]
        scope = _scope(records)
        timestamps = sorted(value for value in (_timestamp(record) for record in records) if value)
        for universe in universes_for_dataset(dataset):
            batches.append(EvidenceBatch(
                universe=universe,
                dataset=dataset,
                records=tuple(records),
                scope=scope,
                observation_start=timestamps[0] if timestamps else "",
                observation_end=timestamps[-1] if timestamps else "",
                schema_version=current_schema(dataset),
                source_available=True,
                qualifying_activity_expected=None,
            ))
    return tuple(batches)


def _alias_datasets(datasets: Mapping[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    result = {key: list(value) for key, value in datasets.items()}
    for logical, physical in _PHYSICAL_DATASET.items():
        if physical in result:
            result[logical] = result[physical]
    return result


def _question_inputs(states, answers, contracts) -> dict[str, QuestionEvidenceInput]:
    answers_by_id = {item.question_id: item for item in answers}
    contracts_by_id = {item.question_id: item for item in contracts}
    values = {}
    for state in states:
        answer = answers_by_id[state.question_id]
        contract = contracts_by_id[state.question_id]
        requirements = tuple(state.requirements)
        source_requirements = [item for item in requirements if item.get("type") == "dataset_presence"]
        field_requirements = [item for item in requirements if item.get("type") == "required_field"]
        join_requirements = [item for item in requirements if item.get("type") == "join_integrity"]
        population_requirements = [
            item for item in requirements
            if item.get("type") in {"coverage", "population_count"}
        ]
        sample_requirements = [item for item in requirements if item.get("type") == "sample_size"]
        collection_functional = bool(source_requirements) and all(item.get("satisfied") is True for item in source_requirements)
        population_complete = (
            collection_functional
            and all(item.get("satisfied") is not False for item in (*field_requirements, *join_requirements, *population_requirements))
            and (state.current_sample_size or 0) > 0
        )
        observed_fields = tuple(sorted(
            item.get("name") for item in field_requirements if item.get("satisfied") is True
        ))
        joins_pass = not join_requirements or all(item.get("satisfied") is True for item in join_requirements)
        statistical = StatisticalState.UNKNOWN
        if sample_requirements:
            statistical = (
                StatisticalState.SUFFICIENT
                if all(item.get("satisfied") is True for item in sample_requirements)
                else StatisticalState.INSUFFICIENT
            )
        elif contract.minimum_sample is None:
            statistical = StatisticalState.NOT_REQUIRED
        elif state.current_sample_size is not None:
            statistical = (
                StatisticalState.SUFFICIENT
                if state.current_sample_size >= contract.minimum_sample
                else StatisticalState.INSUFFICIENT
            )
        existing_result = {
            "report_validity": answer.validity,
            "report_validity_reason": answer.validity_reason,
            "report_status": answer.status,
            "result": answer.result,
            "finding": answer.finding,
            "fingerprint": dict(answer.fingerprint),
            "provenance": dict(answer.provenance),
            "report_path": answer.report_path,
        }
        failed = tuple(
            str(item.get("name")) for item in requirements if item.get("satisfied") is False
        )
        values[state.question_id] = QuestionEvidenceInput(
            question_id=state.question_id,
            contract_fingerprint=contract.contract_fingerprint,
            existing_state=state.state_status,
            existing_result=existing_result,
            actual_population=state.current_sample_size or 0,
            population_complete=population_complete,
            collection_functional=collection_functional,
            observed_resolution=contract.resolution if population_complete else "",
            observed_fields=observed_fields,
            available_lineage=contract.required_lineage if joins_pass and population_complete else (),
            evidence_scope={
                "sources": state.evidence_sources,
                "metrics": state.evidence_metrics,
                "excluded_count": state.excluded_evidence_count,
                "epoch": state.evidence_epoch,
            },
            supported_scope={
                "population": state.current_sample_size or 0,
                "epoch": state.evidence_epoch or "CURRENT",
            } if (state.current_sample_size or 0) > 0 else {},
            unsupported_scope={"failed_requirements": failed} if failed else {},
            statistical_state=statistical,
            negative_result=any(token in answer.finding.upper() for token in ("NO_RELIABLE", "NEGATIVE", "ABSENT", "NULL")),
            negative_observable=population_complete,
            evidence_references=tuple(filter(None, (answer.report_path,))),
        )
    return values


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(_canonical(value) + "\n", encoding="utf-8")
    temporary.replace(path)


def _atomic_jsonl(path: Path, records: list[dict[str, Any]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    digest = hashlib.sha256()
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            line = _canonical(record) + "\n"
            handle.write(line)
            digest.update(line.encode("utf-8"))
    temporary.replace(path)
    return digest.hexdigest()


def _read_json(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CheckpointValidationError(f"unreadable checkpoint {path}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise CheckpointValidationError(f"checkpoint {path} is not a JSON object")
    return value


def _validated_envelope(
    path: Path, *, stage: str, as_of_utc: str, upstream_fingerprint: str | None,
) -> Mapping[str, Any]:
    value = _read_json(path)
    if value.get("schema") != CHECKPOINT_SCHEMA_VERSION:
        raise CheckpointValidationError(
            f"{stage} schema mismatch: expected {CHECKPOINT_SCHEMA_VERSION}, got {value.get('schema')!r}"
        )
    if value.get("stage") != stage:
        raise CheckpointValidationError(f"stage mismatch: expected {stage!r}, got {value.get('stage')!r}")
    if value.get("as_of_utc") != as_of_utc:
        raise CheckpointValidationError(
            f"mixed snapshot: expected as_of_utc={as_of_utc!r}, got {value.get('as_of_utc')!r}"
        )
    if upstream_fingerprint is not None and value.get("upstream_fingerprint") != upstream_fingerprint:
        raise CheckpointValidationError(
            f"upstream fingerprint mismatch: expected {upstream_fingerprint}, "
            f"got {value.get('upstream_fingerprint')!r}"
        )
    stated = value.get("fingerprint")
    core = {key: item for key, item in value.items() if key != "fingerprint"}
    actual = _fingerprint(core)
    if stated != actual:
        raise CheckpointValidationError(
            f"checkpoint fingerprint mismatch: expected {stated!r}, calculated {actual}"
        )
    return value


def _checkpoint_envelope(
    stage: str, as_of_utc: str, upstream_fingerprint: str | None, **payload: Any,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema": CHECKPOINT_SCHEMA_VERSION,
        "stage": stage,
        "as_of_utc": as_of_utc,
        **payload,
    }
    if upstream_fingerprint is not None:
        value["upstream_fingerprint"] = upstream_fingerprint
    value["fingerprint"] = _fingerprint(value)
    return value


def _emit(progress: Callable[[str], None] | None, marker: str) -> None:
    if progress is not None:
        progress(marker)


def _malformed_reports(reader: Any, datasets: Mapping[str, list[dict[str, Any]]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    getter = getattr(reader, "malformed_report", None)
    if not callable(getter):
        return result
    for dataset in sorted(datasets):
        report = getter(dataset)
        if report is not None:
            result[dataset] = {
                "malformed_lines": int(getattr(report, "malformed_lines", 0)),
                "keys_with_errors": sorted(str(item) for item in getattr(report, "keys_with_errors", ())),
            }
    return result


def _write_evidence_snapshot(
    checkpoint_dir: Path,
    *,
    as_of_utc: str,
    datasets: Mapping[str, list[dict[str, Any]]],
    malformed: Mapping[str, Any],
    source_objects: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    entries: dict[str, Any] = {}
    evidence_dir = checkpoint_dir / "evidence"
    for dataset in sorted(datasets):
        relative = Path("evidence") / f"{dataset}.jsonl"
        content_fingerprint = _atomic_jsonl(checkpoint_dir / relative, datasets[dataset])
        physical_objects = list((source_objects or {}).get(dataset, ()))
        entries[dataset] = {
            "artifact": relative.as_posix(),
            "record_count": len(datasets[dataset]),
            "content_fingerprint": content_fingerprint,
            # The immutable local artifact is the run's physical source object.
            # Its hash prevents a resumed run from drifting with live S3.
            "source_objects": physical_objects or [{
                "identifier": relative.as_posix(),
                "fingerprint": content_fingerprint,
            }],
        }
    value = _checkpoint_envelope(
        "evidence_snapshot", as_of_utc, None,
        dataset_counts={key: len(value) for key, value in sorted(datasets.items())},
        datasets=entries,
        malformed_input=dict(malformed),
        canonical_snapshot_identity=_fingerprint({
            "as_of_utc": as_of_utc,
            "datasets": entries,
            "malformed_input": malformed,
        }),
    )
    _atomic_json(checkpoint_dir / "evidence_snapshot.json", value)
    return value


def _source_object_reports(reader: Any, datasets: Mapping[str, Any]) -> dict[str, Any]:
    getter = getattr(reader, "object_metadata", None)
    if not callable(getter):
        return {}
    return {
        dataset: [dict(item) for item in getter(dataset)]
        for dataset in sorted(datasets)
    }


def _load_evidence_snapshot(
    checkpoint_dir: Path, *, as_of_utc: str,
) -> tuple[Mapping[str, Any], dict[str, list[dict[str, Any]]]]:
    value = _validated_envelope(
        checkpoint_dir / "evidence_snapshot.json",
        stage="evidence_snapshot", as_of_utc=as_of_utc, upstream_fingerprint=None,
    )
    entries = value.get("datasets")
    if not isinstance(entries, Mapping) or set(entries) != set(required_production_datasets()):
        raise CheckpointValidationError("evidence snapshot dataset inventory is not canonical")
    datasets: dict[str, list[dict[str, Any]]] = {}
    for dataset, metadata in sorted(entries.items()):
        if not isinstance(metadata, Mapping):
            raise CheckpointValidationError(f"invalid evidence metadata for {dataset}")
        source_objects = metadata.get("source_objects")
        if not isinstance(source_objects, list) or not source_objects:
            raise CheckpointValidationError(f"missing source object identity for {dataset}")
        relative = Path(str(metadata.get("artifact", "")))
        if relative.is_absolute() or ".." in relative.parts:
            raise CheckpointValidationError(f"unsafe evidence artifact path for {dataset}")
        path = checkpoint_dir / relative
        digest = hashlib.sha256()
        records: list[dict[str, Any]] = []
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, 1):
                    digest.update(line.encode("utf-8"))
                    item = json.loads(line)
                    if not isinstance(item, dict):
                        raise CheckpointValidationError(
                            f"{dataset} snapshot line {line_number} is not an object"
                        )
                    records.append(item)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CheckpointValidationError(f"invalid evidence artifact {path}: {exc}") from exc
        if digest.hexdigest() != metadata.get("content_fingerprint"):
            raise CheckpointValidationError(f"evidence artifact fingerprint mismatch for {dataset}")
        if len(records) != metadata.get("record_count"):
            raise CheckpointValidationError(f"evidence artifact record count mismatch for {dataset}")
        datasets[str(dataset)] = records
    actual_counts = {key: len(item) for key, item in sorted(datasets.items())}
    if value.get("dataset_counts") != actual_counts:
        raise CheckpointValidationError("evidence snapshot dataset counts mismatch")
    expected_identity = _fingerprint({
        "as_of_utc": as_of_utc,
        "datasets": entries,
        "malformed_input": value.get("malformed_input", {}),
    })
    if value.get("canonical_snapshot_identity") != expected_identity:
        raise CheckpointValidationError("canonical snapshot identity mismatch")
    return value, datasets


def _reconstruction_from_dict(value: Mapping[str, Any]) -> ShadowReconstructionResult:
    artifacts = tuple(ReconstructedArtifact(
        artifact=dict(item["artifact"]),
        target_universe=str(item["target_universe"]),
        target_dataset=str(item["target_dataset"]),
        source_datasets=tuple(item.get("source_datasets", ())),
        source_identities=tuple(item.get("source_identities", ())),
        reconstruction_rule=str(item["reconstruction_rule"]),
        rule_version=str(item["rule_version"]),
        source_complete=bool(item["source_complete"]),
        exact=bool(item["exact"]),
        information_loss=tuple(item.get("information_loss", ())),
        reconstruction_timestamp=str(item["reconstruction_timestamp"]),
        state=str(item.get("state", "RECONSTRUCTED")),
    ) for item in value.get("artifacts", ()))
    limitations = tuple(ReconstructionLimitation(
        identity=dict(item.get("identity", {})),
        reason=str(item.get("reason", "")),
        source_identities=tuple(item.get("source_identities", ())),
    ) for item in value.get("limitations", ()))
    result = ShadowReconstructionResult(
        artifacts=artifacts, limitations=limitations,
        fingerprint=str(value.get("fingerprint", "")),
    )
    expected = _fingerprint({
        "artifacts": [item.to_dict() for item in artifacts],
        "limitations": [item.to_dict() for item in limitations],
    })
    if result.fingerprint != expected:
        raise CheckpointValidationError("reconstruction payload fingerprint mismatch")
    return result


def _integrity_from_dict(value: Mapping[str, Any]) -> IntegrityReport:
    if value.get("schema") != INTEGRITY_SCHEMA_VERSION:
        raise CheckpointValidationError(
            f"Wave 2 report schema mismatch: expected {INTEGRITY_SCHEMA_VERSION}, got {value.get('schema')!r}"
        )
    manifests = tuple(EvidenceManifest(**{
        **item,
        "source_objects": tuple(item.get("source_objects", ())),
    }) for item in value.get("manifests", ()))
    findings = tuple(IntegrityFinding(**{
        **item,
        "evidence": tuple(item.get("evidence", ())),
    }) for item in value.get("findings", ()))
    reconstruction = _reconstruction_from_dict({
        "artifacts": value.get("reconstructions", ()),
        "limitations": (),
        "fingerprint": _fingerprint({
            "artifacts": value.get("reconstructions", ()), "limitations": [],
        }),
    })
    return IntegrityReport(
        manifests=manifests, findings=findings,
        reconstructions=reconstruction.artifacts,
        schema=int(value.get("schema", 0)),
    )


def _reconciliation_from_dict(value: Mapping[str, Any]) -> ReconciliationReport:
    if value.get("schema") != RECONCILIATION_SCHEMA_VERSION:
        raise CheckpointValidationError(
            f"Wave 3 report schema mismatch: expected {RECONCILIATION_SCHEMA_VERSION}, got {value.get('schema')!r}"
        )
    results = []
    for item in value.get("results", ()):
        semantic = tuple(SemanticCheck(**check) for check in item.get("semantic_checks", ()))
        trace_value = item.get("trace", {})
        trace = ReconciliationTrace(**{
            **trace_value,
            "condition_evidence": tuple(trace_value.get("condition_evidence", ())),
            "candidate_references": tuple(trace_value.get("candidate_references", ())),
            "integrity_blockers": tuple(trace_value.get("integrity_blockers", ())),
            "semantic_comparisons": tuple(trace_value.get("semantic_comparisons", ())),
        })
        results.append(ReconciliationResult(**{
            **item,
            "target_identities": tuple(item.get("target_identities", ())),
            "semantic_checks": semantic,
            "integrity_blockers": tuple(item.get("integrity_blockers", ())),
            "historical_limitations": tuple(item.get("historical_limitations", ())),
            "source_evidence_references": tuple(item.get("source_evidence_references", ())),
            "counterpart_evidence_references": tuple(item.get("counterpart_evidence_references", ())),
            "provenance": tuple(item.get("provenance", ())),
            "root_finding_ids": tuple(item.get("root_finding_ids", ())),
            "trace": trace,
        }))
    return ReconciliationReport(
        results=tuple(results), counts=dict(value.get("counts", {})),
        rule_counts={key: dict(item) for key, item in value.get("rule_counts", {}).items()},
        manifests=tuple(value.get("manifests", ())), schema=int(value.get("schema", 0)),
    )


def _qualification_from_dict(value: Mapping[str, Any]) -> QuestionQualificationReport:
    if value.get("schema") != QUALIFICATION_SCHEMA_VERSION:
        raise CheckpointValidationError(
            f"Wave 4 report schema mismatch: expected {QUALIFICATION_SCHEMA_VERSION}, got {value.get('schema')!r}"
        )
    qualifications = []
    tuple_fields = {
        "required_universes", "required_relationships", "required_lineage",
        "integrity_state", "reconciliation_state", "historical_limitations",
        "reconstruction_involvement", "reason_codes", "evidence_references", "blocker_ids",
    }
    for item in value.get("qualifications", ()):
        converted = dict(item)
        converted["sufficiency"] = SufficiencyAssessment(**converted["sufficiency"])
        for name in tuple_fields:
            converted[name] = tuple(converted.get(name, ()))
        qualifications.append(QuestionQualification(**converted))
    contradictions = tuple(AssuranceContradiction(**item) for item in value.get("contradictions", ()))
    return QuestionQualificationReport(
        qualifications=tuple(qualifications), contradictions=contradictions,
        counts=dict(value.get("counts", {})), blocker_counts=dict(value.get("blocker_counts", {})),
        contract_set_fingerprint=str(value.get("contract_set_fingerprint", "")),
        report_fingerprint=str(value.get("report_fingerprint", "")),
        schema=int(value.get("schema", 0)),
    )


def _answers_from_dict(values: Any) -> tuple[CanonicalAnswerBinding, ...]:
    return tuple(CanonicalAnswerBinding(**item) for item in values or ())


def qualify_production(
    *,
    as_of_utc: str,
    source: S3ResearchDataSource | None = None,
    reports_dir: str | Path = "analysis/reports",
) -> ProductionQualificationBundle:
    """Execute the actual read-only production Wave 2 -> 3 -> 4 audit."""
    parsed_now = datetime.fromisoformat(as_of_utc.replace("Z", "+00:00"))
    if parsed_now.tzinfo is None:
        raise ValueError("as_of_utc must be timezone-aware")
    reader = source or get_default_source()
    datasets = {
        dataset: list(reader.read_dataset(dataset))
        for dataset in required_production_datasets()
    }
    batches = _batches(datasets)
    reconstruction = reconstruct_shadow_outcomes_report(
        datasets.get("shadow_runtime", ()),
        reconstruction_timestamp=as_of_utc,
    )
    integrity = build_report(
        batches,
        reconstructions=reconstruction.artifacts,
        now=parsed_now,
    )
    reconciliation = ReconciliationEngine(ReconciliationInput(
        batches=batches,
        integrity_findings=integrity.findings,
        manifests=integrity.manifests,
        reconstructions=integrity.reconstructions,
    )).reconcile_all()
    answers = bind_canonical_answers(reports_dir)
    aliased = _alias_datasets(datasets)
    # Reconstructed rows are an in-memory derived evidence surface only.  They
    # never replace or write the physical shadow_trades source.  Wave 2 remains
    # the provenance authority and Wave 3 consumes the same governed artifacts.
    reconstructed_shadow = [
        dict(item.artifact)
        for item in integrity.reconstructions
        if item.target_dataset == "shadow_trades"
    ]
    if reconstructed_shadow:
        aliased["shadow_trades"] = reconstructed_shadow
    states = build_all_question_states(
        reports_dir=reports_dir,
        evidence_source=EvidenceSnapshot(aliased),
    )
    contracts = build_question_evidence_contracts()
    inputs = _question_inputs(states, answers, contracts)
    qualification = QualificationEngine(
        inputs=inputs,
        batches=batches,
        integrity_report=integrity,
        reconciliation_report=reconciliation,
        contracts=contracts,
    ).qualify_all()
    return ProductionQualificationBundle(
        as_of_utc=as_of_utc,
        dataset_counts={key: len(value) for key, value in sorted(datasets.items())},
        answers=answers,
        batches=batches,
        integrity=integrity,
        reconciliation=reconciliation,
        qualification=qualification,
    )


def qualify_production_checkpointed(
    *,
    as_of_utc: str,
    checkpoint_dir: str | Path,
    final_path: str | Path,
    source: S3ResearchDataSource | None = None,
    reports_dir: str | Path = "analysis/reports",
    progress: Callable[[str], None] | None = print,
) -> ProductionQualificationBundle:
    """Run the production audit with fail-closed, fingerprinted stage reuse.

    The immutable local evidence artifacts, rather than live S3, are the source
    for every resumed downstream stage. Determinism verification is deliberately
    not performed here: the first complete result is written before callers may
    choose to compare an independent second run.
    """
    parsed_now = datetime.fromisoformat(as_of_utc.replace("Z", "+00:00"))
    if parsed_now.tzinfo is None:
        raise ValueError("as_of_utc must be timezone-aware")
    root = Path(checkpoint_dir)
    root.mkdir(parents=True, exist_ok=True)

    # A — immutable evidence population.
    _emit(progress, "PHASE:evidence_snapshot:start")
    try:
        snapshot_checkpoint, datasets = _load_evidence_snapshot(root, as_of_utc=as_of_utc)
        _emit(progress, "CHECKPOINT:evidence_snapshot:reused")
    except CheckpointValidationError as exc:
        _emit(progress, f"CHECKPOINT:evidence_snapshot:invalid:{exc}")
        reader = source or get_default_source()
        datasets = {
            dataset: list(reader.read_dataset(dataset))
            for dataset in required_production_datasets()
        }
        snapshot_checkpoint = _write_evidence_snapshot(
            root, as_of_utc=as_of_utc, datasets=datasets,
            malformed=_malformed_reports(reader, datasets),
            source_objects=_source_object_reports(reader, datasets),
        )
        _emit(progress, "CHECKPOINT:evidence_snapshot:written")
    _emit(progress, "PHASE:evidence_snapshot:complete")
    snapshot_fingerprint = str(snapshot_checkpoint["fingerprint"])
    batches = _batches(datasets)

    # B — governed reconstruction.
    reconstruction_path = root / "reconstruction.json"
    _emit(progress, "PHASE:reconstruction:start")
    try:
        reconstruction_checkpoint = _validated_envelope(
            reconstruction_path, stage="reconstruction", as_of_utc=as_of_utc,
            upstream_fingerprint=snapshot_fingerprint,
        )
        reconstruction = _reconstruction_from_dict(reconstruction_checkpoint["reconstruction"])
        _emit(progress, "CHECKPOINT:reconstruction:reused")
    except (CheckpointValidationError, KeyError, TypeError, ValueError) as exc:
        _emit(progress, f"CHECKPOINT:reconstruction:invalid:{exc}")
        reconstruction = reconstruct_shadow_outcomes_report(
            datasets.get("shadow_runtime", ()), reconstruction_timestamp=as_of_utc,
        )
        reconstruction_checkpoint = _checkpoint_envelope(
            "reconstruction", as_of_utc, snapshot_fingerprint,
            reconstruction_count=len(reconstruction.artifacts),
            limitation_count=len(reconstruction.limitations),
            reconstruction=reconstruction.to_dict(),
        )
        _atomic_json(reconstruction_path, reconstruction_checkpoint)
        _emit(progress, "CHECKPOINT:reconstruction:written")
    _emit(progress, "PHASE:reconstruction:complete")

    # C — Wave 2.
    wave2_upstream = _fingerprint({
        "snapshot": snapshot_fingerprint,
        "reconstruction": reconstruction_checkpoint["fingerprint"],
    })
    wave2_path = root / "wave2_integrity.json"
    _emit(progress, "PHASE:wave2:start")
    try:
        wave2_checkpoint = _validated_envelope(
            wave2_path, stage="wave2", as_of_utc=as_of_utc,
            upstream_fingerprint=wave2_upstream,
        )
        integrity = _integrity_from_dict(wave2_checkpoint["report"])
        if wave2_checkpoint.get("report_fingerprint") != _fingerprint(integrity.to_dict()):
            raise CheckpointValidationError("Wave 2 report fingerprint mismatch")
        _emit(progress, "CHECKPOINT:wave2:reused")
    except (CheckpointValidationError, KeyError, TypeError, ValueError) as exc:
        _emit(progress, f"CHECKPOINT:wave2:invalid:{exc}")
        integrity = build_report(
            batches, reconstructions=reconstruction.artifacts, now=parsed_now,
        )
        wave2_checkpoint = _checkpoint_envelope(
            "wave2", as_of_utc, wave2_upstream,
            report_fingerprint=_fingerprint(integrity.to_dict()),
            report=integrity.to_dict(),
        )
        _atomic_json(wave2_path, wave2_checkpoint)
        _emit(progress, "CHECKPOINT:wave2:written")
    _emit(progress, "PHASE:wave2:complete")

    # D — Wave 3.
    wave3_upstream = str(wave2_checkpoint["fingerprint"])
    wave3_path = root / "wave3_reconciliation.json"
    _emit(progress, "PHASE:wave3:start")
    try:
        wave3_checkpoint = _validated_envelope(
            wave3_path, stage="wave3", as_of_utc=as_of_utc,
            upstream_fingerprint=wave3_upstream,
        )
        reconciliation = _reconciliation_from_dict(wave3_checkpoint["report"])
        if wave3_checkpoint.get("report_fingerprint") != _fingerprint(reconciliation.to_dict()):
            raise CheckpointValidationError("Wave 3 report fingerprint mismatch")
        _emit(progress, "CHECKPOINT:wave3:reused")
    except (CheckpointValidationError, KeyError, TypeError, ValueError) as exc:
        _emit(progress, f"CHECKPOINT:wave3:invalid:{exc}")
        reconciliation = ReconciliationEngine(ReconciliationInput(
            batches=batches,
            integrity_findings=integrity.findings,
            manifests=integrity.manifests,
            reconstructions=integrity.reconstructions,
        )).reconcile_all()
        wave3_checkpoint = _checkpoint_envelope(
            "wave3", as_of_utc, wave3_upstream,
            report_fingerprint=_fingerprint(reconciliation.to_dict()),
            report=reconciliation.to_dict(),
        )
        _atomic_json(wave3_path, wave3_checkpoint)
        _emit(progress, "CHECKPOINT:wave3:written")
    _emit(progress, "PHASE:wave3:complete")

    # E — Wave 4 and answer binding. Local answer changes invalidate only this
    # stage because their fingerprint is part of the Wave 4 dependency.
    answers = bind_canonical_answers(reports_dir)
    answers_payload = [asdict(item) for item in answers]
    contracts = build_question_evidence_contracts()
    wave4_upstream = _fingerprint({
        "wave3": wave3_checkpoint["fingerprint"],
        "answers": _fingerprint(answers_payload),
        "contracts": _fingerprint([item.to_dict() for item in contracts]),
    })
    wave4_path = root / "wave4_qualification.json"
    _emit(progress, "PHASE:wave4:start")
    try:
        wave4_checkpoint = _validated_envelope(
            wave4_path, stage="wave4", as_of_utc=as_of_utc,
            upstream_fingerprint=wave4_upstream,
        )
        qualification = _qualification_from_dict(wave4_checkpoint["report"])
        checkpoint_answers = _answers_from_dict(wave4_checkpoint.get("answers"))
        if checkpoint_answers != answers:
            raise CheckpointValidationError("canonical answer binding changed")
        if wave4_checkpoint.get("report_fingerprint") != qualification.report_fingerprint:
            raise CheckpointValidationError("Wave 4 report fingerprint mismatch")
        semantic = {
            "contract_set_fingerprint": qualification.contract_set_fingerprint,
            "qualifications": [item.to_dict() for item in qualification.qualifications],
            "counts": dict(sorted(qualification.counts.items())),
            "blocker_counts": dict(sorted(qualification.blocker_counts.items())),
            "contradictions": [asdict(item) for item in qualification.contradictions],
        }
        if qualification.report_fingerprint != _fingerprint(semantic):
            raise CheckpointValidationError("Wave 4 semantic fingerprint mismatch")
        if len(qualification.qualifications) != len(REGISTRY):
            raise CheckpointValidationError("Wave 4 checkpoint does not contain all canonical questions")
        _emit(progress, "CHECKPOINT:wave4:reused")
    except (CheckpointValidationError, KeyError, TypeError, ValueError) as exc:
        _emit(progress, f"CHECKPOINT:wave4:invalid:{exc}")
        aliased = _alias_datasets(datasets)
        reconstructed_shadow = [dict(item.artifact) for item in reconstruction.artifacts]
        if reconstructed_shadow:
            aliased["shadow_trades"] = reconstructed_shadow
        states = build_all_question_states(
            reports_dir=reports_dir, evidence_source=EvidenceSnapshot(aliased),
        )
        inputs = _question_inputs(states, answers, contracts)
        qualification = QualificationEngine(
            inputs=inputs, batches=batches, integrity_report=integrity,
            reconciliation_report=reconciliation, contracts=contracts,
        ).qualify_all()
        if len(qualification.qualifications) != len(REGISTRY):
            raise RuntimeError("Wave 4 did not qualify all canonical questions")
        wave4_checkpoint = _checkpoint_envelope(
            "wave4", as_of_utc, wave4_upstream,
            report_fingerprint=qualification.report_fingerprint,
            answers=answers_payload,
            report=qualification.to_dict(),
        )
        _atomic_json(wave4_path, wave4_checkpoint)
        _emit(progress, "CHECKPOINT:wave4:written")
    _emit(progress, "PHASE:wave4:complete")

    bundle = ProductionQualificationBundle(
        as_of_utc=as_of_utc,
        dataset_counts={key: len(value) for key, value in sorted(datasets.items())},
        answers=answers, batches=batches, integrity=integrity,
        reconciliation=reconciliation, qualification=qualification,
    )
    write_bundle(bundle, final_path)
    _emit(progress, "CHECKPOINT:final_bundle:written")
    return bundle


def compare_checkpoint_runs(left: str | Path, right: str | Path) -> Mapping[str, bool]:
    """Compare two completed checkpoint runs after the first result is safe."""
    left_root, right_root = Path(left), Path(right)
    names = {
        "evidence_snapshot": "evidence_snapshot.json",
        "reconstruction": "reconstruction.json",
        "wave2": "wave2_integrity.json",
        "wave3": "wave3_reconciliation.json",
        "wave4": "wave4_qualification.json",
    }
    return {
        stage: _read_json(left_root / filename).get("fingerprint")
        == _read_json(right_root / filename).get("fingerprint")
        for stage, filename in names.items()
    }


def write_bundle(bundle: ProductionQualificationBundle, path: str | Path) -> Path:
    """Persist assurance output only; never write source evidence."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(bundle.summary(), sort_keys=True, separators=(",", ":"), default=str) + "\n"
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(target)
    return target


__all__ = [
    "CHECKPOINT_SCHEMA_VERSION", "CanonicalAnswerBinding",
    "CheckpointValidationError", "ProductionQualificationBundle",
    "bind_canonical_answers", "compare_checkpoint_runs", "qualify_production",
    "qualify_production_checkpointed",
    "required_production_datasets", "write_bundle",
]
