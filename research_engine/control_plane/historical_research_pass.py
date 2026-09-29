"""Frozen-evidence historical research pass for the canonical Q1-Q70 bank.

This coordinator is deliberately separate from the generic V10 orchestrator.
It binds every declared runner to the immutable Stage 4 evidence checkpoint,
never loads S3, records scientific state independently from assurance state,
and persists a deterministic census/manifest for the later certification gate.
"""
from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import importlib
import inspect
import json
from pathlib import Path
import shutil
from typing import Any, Mapping, Sequence
from unittest.mock import patch

from research_engine.control_plane.evidence_resolver import (
    EvidenceSnapshot,
    resolve_question_evidence,
    resolve_question_population,
)
from research_engine.control_plane.report_resolver import (
    ReportValidity,
    load_report_for_question,
    resolve_report_validity,
)
from research_engine.registry.research_question_registry import REGISTRY, REGISTRY_BY_ID
from research_engine.v10.universes.production_qualification import (
    _alias_datasets,
    _load_evidence_snapshot,
    _reconstruction_from_dict,
    _validated_envelope,
)


PASS_SCHEMA = 1
DEFAULT_AS_OF = "2026-09-27T00:00:00Z"
DEFAULT_ANALYSIS_TIME = "2026-09-28T00:00:00Z"
DEFAULT_CHECKPOINT_DIR = Path("analysis/assurance/checkpoints/wave4_20260927")
DEFAULT_REPORTS_DIR = Path("analysis/reports")
DEFAULT_OUTPUT = Path("analysis/assurance/historical_research_pass_20260928.json")

SCIENTIFIC_STATES = frozenset({
    "COMPLETE", "NEGATIVE_RESULT", "NO_EFFECT", "INSUFFICIENT_DATA",
    "WAITING_DATA", "HISTORICALLY_UNANSWERABLE", "IMPLEMENTATION_BLOCKED",
})
STRICT_SPECIAL_REPORTS = frozenset({"R3", "R4", "R5", "L1", "L4", "EX10"})


def _load_scoped_snapshot(checkpoint_dir: Path, *, as_of_utc: str,
                          required_sources: set[str]):
    """Load and hash only frozen source artifacts needed by a scoped rerun."""
    snapshot = _validated_envelope(
        checkpoint_dir / "evidence_snapshot.json", stage="evidence_snapshot",
        as_of_utc=as_of_utc, upstream_fingerprint=None)
    entries = snapshot.get("datasets", {})
    # Logical shadow_trades is reconstructed below; loading the 90MB physical
    # runtime copy as well would duplicate the same governed evidence in RAM.
    physical = {source for source in required_sources if source != "shadow_trades"}
    datasets: dict[str, list[dict[str, Any]]] = {}
    for source in sorted(physical):
        metadata = entries.get(source)
        if not isinstance(metadata, Mapping):
            raise RuntimeError("FROZEN_DATASET_NOT_AVAILABLE:" + source)
        relative = Path(str(metadata.get("artifact", "")))
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError("UNSAFE_FROZEN_ARTIFACT:" + source)
        digest = hashlib.sha256()
        rows = []
        with (checkpoint_dir / relative).open("r", encoding="utf-8") as handle:
            for line in handle:
                digest.update(line.encode("utf-8"))
                if line.strip():
                    rows.append(json.loads(line))
        if digest.hexdigest() != metadata.get("content_fingerprint"):
            raise RuntimeError("FROZEN_ARTIFACT_FINGERPRINT_MISMATCH:" + source)
        if len(rows) != int(metadata.get("record_count", -1)):
            raise RuntimeError("FROZEN_ARTIFACT_COUNT_MISMATCH:" + source)
        datasets[source] = rows
    return snapshot, datasets


def _normalize_scope(question_ids: Sequence[str] | None, registry=REGISTRY) -> tuple[
        frozenset[str] | None, frozenset[str] | None]:
    """Validate a question scope and close it over scientific aliases.

    Returns ``(requested_scope, effective_scope)``.  ``None`` means the full
    Q1-Q70 pass.  Unknown or duplicate ids fail closed; an aliased question
    pulls in its scientific owner because the alias only projects the
    owner's settled result (dependency necessity, nothing broader).
    """
    if question_ids is None:
        return None, None
    requested = [str(item).strip().upper() for item in question_ids]
    if not requested:
        raise ValueError("SCOPED_RUN_EMPTY")
    if len(set(requested)) != len(requested):
        raise ValueError("SCOPED_RUN_DUPLICATE_QUESTION_ID")
    registry_by_id = {question.id: question for question in registry}
    unknown = [qid for qid in requested if qid not in registry_by_id]
    if unknown:
        raise ValueError("SCOPED_RUN_UNKNOWN_QUESTION_ID:" + ",".join(unknown))
    effective = set(requested)
    for qid in requested:
        owner = registry_by_id[qid].scientific_owner_id
        if owner:
            effective.add(owner)
    return frozenset(requested), frozenset(effective)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        default=str,
    ).encode("utf-8")


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _native(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _native(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_native(item) for item in value]
    if hasattr(value, "value"):
        return value.value
    return value


def _report_sample(report: Mapping[str, Any]) -> int | None:
    dataset = report.get("dataset", {})
    overall = report.get("overall", {})
    for section, keys in (
        (dataset, ("analysed_row_count", "independent_observations")),
        (overall, ("n", "paired_opportunities", "total_analysed")),
    ):
        if isinstance(section, Mapping):
            for key in keys:
                value = section.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                    return int(value)
    sections = (report, dataset, overall, report.get("fingerprint", {}))
    for section in sections:
        if not isinstance(section, Mapping):
            continue
        for key in (
            "sample_size", "independent_observations", "total_analysed",
            "total_trades", "records_used", "matched_trades", "paired_opportunities",
        ):
            value = section.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                return int(value)
    return None


def _report_text(report: Mapping[str, Any]) -> str:
    overall = report.get("overall", {})
    values: list[Any] = [report.get("status"), report.get("recommendation")]
    if isinstance(overall, Mapping):
        values.extend(
            overall.get(key) for key in (
                "finding", "conclusion", "finding_classification", "edge_classification",
                "classification", "completion_reason",
            )
        )
    return " ".join(str(value) for value in values if value not in (None, "")).upper()


def scientific_state_for_report(report: Mapping[str, Any]) -> str:
    """Derive scientific meaning without treating positivity as assurance."""
    explicit = str(report.get("scientific_state") or "").upper()
    status = str(report.get("status") or "").upper()
    # Early historical-pass replays mapped every BLOCKED runner report to an
    # implementation failure.  Once a runner has actually completed against
    # frozen history, BLOCKED can instead be a scientific negative/impossibility
    # result.  Do not let that stale derived label override the report itself.
    if explicit in SCIENTIFIC_STATES and not (
        explicit == "IMPLEMENTATION_BLOCKED"
        and status == "BLOCKED"
        and isinstance(report.get("historical_pass"), Mapping)
    ):
        return explicit
    if status in {"WAITING_DATA", "INSUFFICIENT_DATA"}:
        return status
    text = _report_text(report)
    if status == "BLOCKED":
        if str(report.get("question_id") or "").upper() == "G1" or "SUITAB" in text:
            return "NEGATIVE_RESULT"
        return "HISTORICALLY_UNANSWERABLE"
    if status == "ERROR":
        return "IMPLEMENTATION_BLOCKED"
    if any(token in text for token in (
        "NEGATIVE_EDGE", "NEGATIVE RESULT", "UNDERPERFORMS", "HARMFUL",
    )):
        return "NEGATIVE_RESULT"
    if any(token in text for token in (
        "NO_EFFECT", "NO EFFECT", "VALID NULL", "SUFFICIENT_NULL",
        "NO_RELIABLE", "NOT_SUPPORTED", "NO SUPPORTED", "DID NOT REJECT",
    )):
        return "NO_EFFECT"
    return "COMPLETE" if status == "COMPLETE" else "IMPLEMENTATION_BLOCKED"


@dataclass(frozen=True)
class HistoricalQuestionResult:
    question_id: str
    canonical_question: str
    initial_classification: str
    runner_status: str
    runner_module: str
    runner_function: str
    runner_inputs: tuple[str, ...]
    candidate_historical_population: int | None
    usable_historical_population: int
    excluded_historical_population: int | None
    unexplained_historical_population: int
    historical_exhaustion_state: str
    current_report_before: str
    current_result_valid_before: bool
    current_report_status: str
    current_report_path: str
    result_fingerprint: str
    scientific_state: str
    assurance_status: str
    next_action: str
    reason: str
    runner_executed: bool = False
    runner_failed: bool = False
    stale_or_legacy_replaced: bool = False
    missing_report_resolved: bool = False
    runner_analytical_population: int | None = None


class FrozenDatasetSource:
    """S3-compatible reader backed only by the checkpoint payload."""

    def __init__(self, datasets: Mapping[str, list[dict[str, Any]]]) -> None:
        self.datasets = datasets

    def read_dataset(self, name: str) -> list[dict[str, Any]]:
        aliases = {
            "research_shadow_trades": "__EMPTY_RECONSTRUCTION_SUPPLEMENT__",
            "execution_results_v1": "execution_results",
            "execution_attempts_v1": "execution_attempts",
            "protection_audit_v1": "protection_audit",
            "risk_deviation_v1": "risk_deviation",
        }
        resolved = aliases.get(name, name)
        if resolved == "__EMPTY_RECONSTRUCTION_SUPPLEMENT__":
            return []
        if resolved not in self.datasets:
            raise RuntimeError(f"FROZEN_DATASET_NOT_AVAILABLE:{name}")
        return list(self.datasets[resolved])


class HistoricalResearchPass:
    def __init__(
        self,
        *,
        checkpoint_dir: str | Path = DEFAULT_CHECKPOINT_DIR,
        reports_dir: str | Path = DEFAULT_REPORTS_DIR,
        output_path: str | Path = DEFAULT_OUTPUT,
        as_of_utc: str = DEFAULT_AS_OF,
        analysis_time: str = DEFAULT_ANALYSIS_TIME,
        question_ids: Sequence[str] | None = None,
        registry_version: str = "research_question_registry_v1",
        runner_context: Mapping[str, Any] | None = None,
    ) -> None:
        self.checkpoint_dir = Path(checkpoint_dir)
        self.reports_dir = Path(reports_dir)
        self.output_path = Path(output_path)
        self.as_of_utc = as_of_utc
        self.analysis_time = analysis_time
        self.registry_version = str(registry_version)
        if self.registry_version == "stage4_implementation_registry_v2":
            from research_engine.control_plane.stage4_registry_successor import REGISTRY_V2
            self.registry = REGISTRY_V2
        elif self.registry_version == "research_question_registry_v1":
            self.registry = REGISTRY
        else:
            raise ValueError("UNKNOWN_REGISTRY_VERSION:" + self.registry_version)
        self.registry_by_id = {question.id: question for question in self.registry}
        self.runner_context = dict(runner_context or {})
        self.requested_scope, self.scope = _normalize_scope(question_ids, self.registry)
        if self.registry_version == "stage4_implementation_registry_v2" and self.scope is not None:
            required_sources = {
                source.value for question in self._questions()
                for source in question.data_sources
            }
            snapshot, datasets = _load_scoped_snapshot(
                self.checkpoint_dir, as_of_utc=as_of_utc,
                required_sources=required_sources)
        else:
            snapshot, datasets = _load_evidence_snapshot(
                self.checkpoint_dir, as_of_utc=as_of_utc)
        reconstruction_envelope = _validated_envelope(
            self.checkpoint_dir / "reconstruction.json",
            stage="reconstruction",
            as_of_utc=as_of_utc,
            upstream_fingerprint=str(snapshot["fingerprint"]),
        )
        reconstruction = _reconstruction_from_dict(reconstruction_envelope["reconstruction"])
        baseline_manifest = json.loads(DEFAULT_OUTPUT.read_text(encoding="utf-8"))
        baseline_upstream = baseline_manifest.get("upstream_fingerprints", {})
        self.upstream_fingerprints = {
            "evidence_snapshot": str(snapshot["fingerprint"]),
            "reconstruction": str(reconstruction_envelope["fingerprint"]),
            "wave2": str(baseline_upstream.get("wave2") or json.loads((self.checkpoint_dir / "wave2_integrity.json").read_text(encoding="utf-8"))["fingerprint"]),
            "wave3": str(baseline_upstream.get("wave3") or json.loads((self.checkpoint_dir / "wave3_reconciliation.json").read_text(encoding="utf-8"))["fingerprint"]),
        }
        self.datasets = _alias_datasets(datasets)
        reconstructed = [dict(item.artifact) for item in reconstruction.artifacts]
        if reconstructed:
            self.datasets["shadow_trades"] = reconstructed
        self.snapshot = EvidenceSnapshot(self.datasets)
        self.frozen_source = FrozenDatasetSource(self.datasets)
        self.accounting = self._load_gate1_accounting()
        self.results: dict[str, HistoricalQuestionResult] = {}
        self.runner_reports: dict[str, dict[str, Any]] = {}
        self._population_cache: dict[str, list[dict[str, Any]]] = {}

    def _questions(self) -> list[Any]:
        """Registry questions in canonical order, restricted to the scope."""
        if self.scope is None:
            return list(self.registry)
        return [question for question in self.registry if question.id in self.scope]

    def _load_gate1_accounting(self) -> dict[str, dict[str, Any]]:
        checkpoint = json.loads(
            (self.checkpoint_dir / "wave4_qualification.json").read_text(encoding="utf-8")
        )
        return {
            item["question_id"]: item
            for item in checkpoint["report"]["qualifications"]
        }

    def _accounting_for(self, question_id: str) -> tuple[int | None, int, int | None, int, str]:
        item = self.accounting[question_id]
        accounting = item.get("evidence_accounting", {})
        population = accounting.get("population_stage", {})
        candidate = accounting.get("candidate_records")
        usable = int(population.get("used_records", item.get("actual_population", 0)) or 0)
        if candidate is None:
            excluded = None
        else:
            excluded = int(candidate) - usable
        unexplained = int(accounting.get("unexplained_records", 0) or 0) + int(
            population.get("unexplained_records", 0) or 0
        )
        exhaustion = str(accounting.get("historical_exhaustion_status") or "ACCOUNTING_UNRESOLVED")
        return (int(candidate) if candidate is not None else None, usable, excluded, unexplained, exhaustion)

    def _current_report(self, question: Any) -> tuple[dict[str, Any] | None, str, ReportValidity, str]:
        report, path = load_report_for_question(
            question.id, question.report_filename, reports_dir=self.reports_dir,
        )
        validity, reason = resolve_report_validity(
            question.report_filename,
            report,
            expected_question_id=question.id,
            accepted_question_ids=question.legacy_ids,
        )
        return report, path, validity, reason

    def _missing_field_reason(self, question: Any) -> str:
        resolution = resolve_question_evidence(question, self.snapshot)
        missing = [
            str(item.name) for item in resolution.requirements
            if item.type in {"required_field", "join_integrity"} and item.satisfied is False
        ]
        if missing:
            return "Historical governed population has no analytically usable rows; absent/unresolved: " + ", ".join(missing)
        return "Historical governed population has no analytically usable rows after governed eligibility and relationship filtering"

    def _initial_classification(self, question: Any, valid_before: bool, usable: int) -> tuple[str, str]:
        if question.scientific_owner_id:
            return "HISTORICAL_RUN_NOT_REQUIRED", f"Explicit scientific alias of {question.scientific_owner_id}"
        if not question.runner_module or not question.runner_function:
            return "IMPLEMENTATION_BLOCKED", "No governed runner/module is declared"
        successor = self.registry_version == "stage4_implementation_registry_v2"
        if question.id == "L3" and not successor:
            return (
                "IMPLEMENTATION_BLOCKED",
                "Declared q1_component_reward.json is adjudicated to D1; L3 has no independently owned CURRENT report path",
            )
        if question.id == "L7" and usable and not successor:
            return (
                "IMPLEMENTATION_BLOCKED",
                "Runner requires governed control_label and candidate_label, but the canonical L7 contract declares neither",
            )
        if question.id in {"R1", "R2"} and not successor:
            return (
                "IMPLEMENTATION_BLOCKED",
                f"HD10 runner analytical population is 10,803 opportunities but the canonical Gate 1 usable population for {question.id} is 635; the runner lacks an adapter for the governed required-field population",
            )
        if question.id == "G2" and not successor:
            return (
                "IMPLEMENTATION_BLOCKED",
                "HD14 runner denominator is 22,521 identities but the canonical Gate 1 usable G2 population is 261; reconciling the distinct denominator contracts requires an engineering contract repair",
            )
        if question.id == "EX2" and not valid_before and not successor:
            return (
                "IMPLEMENTATION_BLOCKED",
                "HD09 EX2 reconstructs 9,045 exit-policy observations from repository event files, but the frozen Gate 1 usable population is 8,760; the runner has no frozen governed-population adapter",
            )
        if question.id == "G3" and successor and self.runner_context.get("dependency_finding"):
            return "HISTORICAL_RUN_REQUIRED", "Governed CURRENT L6 successor dependency is pinned"
        if question.id == "G3" and not successor:
            return (
                "IMPLEMENTATION_BLOCKED",
                "G3 requires a governed CURRENT L6 dependency, but L6 has no declared runner or owned report",
            )
        if valid_before:
            return "CURRENT_VALID_RESULT", "Owned VALID_CURRENT result already exists"
        if usable <= 0:
            return "HISTORICALLY_UNANSWERABLE", self._missing_field_reason(question)
        return "HISTORICAL_RUN_REQUIRED", "Governed historical population is usable and no valid CURRENT result exists"

    def census(self) -> list[HistoricalQuestionResult]:
        rows: list[HistoricalQuestionResult] = []
        for question in self._questions():
            candidate, usable, excluded, unexplained, exhaustion = self._accounting_for(question.id)
            report, path, validity, validity_reason = self._current_report(question)
            valid = validity == ReportValidity.VALID_CURRENT
            historical_sample = _report_sample(report or {})
            if (
                valid
                and isinstance((report or {}).get("historical_pass"), Mapping)
                and historical_sample is not None
                and historical_sample > usable
            ):
                valid = False
                validity = ReportValidity.INVALIDATED
                validity_reason = (
                    "Historical runner population exceeds Gate 1 governed usable population: "
                    f"{historical_sample}>{usable}"
                )
            classification, reason = self._initial_classification(question, valid, usable)
            signature_inputs: tuple[str, ...] = ()
            runner_status = "ALIAS" if question.scientific_owner_id else "MISSING"
            if question.runner_module and question.runner_function:
                try:
                    function = getattr(importlib.import_module(question.runner_module), question.runner_function)
                    signature_inputs = tuple(inspect.signature(function).parameters)
                    runner_status = "AVAILABLE"
                except (ImportError, AttributeError) as exc:
                    classification = "IMPLEMENTATION_BLOCKED"
                    reason = f"Declared runner cannot be imported: {type(exc).__name__}: {exc}"
                    runner_status = "ERROR"
            scientific = scientific_state_for_report(report or {}) if valid else (
                "HISTORICALLY_UNANSWERABLE" if classification == "HISTORICALLY_UNANSWERABLE" else
                "IMPLEMENTATION_BLOCKED" if classification == "IMPLEMENTATION_BLOCKED" else
                "COMPLETE" if classification == "HISTORICAL_RUN_NOT_REQUIRED" else "IMPLEMENTATION_BLOCKED"
            )
            rows.append(HistoricalQuestionResult(
                question_id=question.id,
                canonical_question=question.description,
                initial_classification=classification,
                runner_status=runner_status,
                runner_module=question.runner_module,
                runner_function=question.runner_function,
                runner_inputs=signature_inputs,
                candidate_historical_population=candidate,
                usable_historical_population=usable,
                excluded_historical_population=excluded,
                unexplained_historical_population=unexplained,
                historical_exhaustion_state=exhaustion,
                current_report_before=validity.value,
                current_result_valid_before=valid,
                current_report_status=str((report or {}).get("status") or "MISSING"),
                current_report_path=path if report is not None else "",
                result_fingerprint=_fingerprint(report) if report is not None else "",
                scientific_state=scientific,
                assurance_status="NOT_REQUALIFIED",
                next_action=(
                    "NONE" if classification in {"CURRENT_VALID_RESULT", "HISTORICAL_RUN_NOT_REQUIRED", "HISTORICALLY_UNANSWERABLE"}
                    else "IMPLEMENT_RUNNER" if classification == "IMPLEMENTATION_BLOCKED"
                    else "RUN_FROZEN_HISTORY"
                ),
                reason=reason if not valid else validity_reason,
                runner_analytical_population=historical_sample,
            ))
        self.results = {row.question_id: row for row in rows}
        return rows

    def _runner_kwargs(self, question: Any, function: Any) -> dict[str, Any]:
        parameters = inspect.signature(function).parameters
        current = {
            source.value: list(self.snapshot.get(source.value).current_records)
            for source in question.data_sources
        }
        governed = self._population_cache.setdefault(
            question.id, resolve_question_population(question, self.snapshot),
        )
        from research_engine.control_plane.stage4_impl_population2 import record_identity
        governed_ids = {record_identity(row) for row in governed}
        governed_ids.discard("")
        def selected(source_name: str) -> list[dict[str, Any]]:
            return [row for row in current.get(source_name, [])
                    if record_identity(row) in governed_ids]
        values: dict[str, Any] = {
            "shadow_trades": governed if len(question.data_sources) == 1 else current.get("shadow_trades", []),
            "horizon_candidates": current.get("horizon_candidates", []),
            "strategy_candidates": current.get("strategy_candidates", []),
            "execution_results": current.get("execution_results_v1", []),
            "execution_contexts": current.get("execution_context", []),
            "decision_traces": current.get("decision_trace", []),
            "portfolio_rankings": current.get("portfolio_rankings", []),
            "decisions": current.get("decision_trace", []),
            "trade_truth": current.get("trade_truth", []),
            "decision_records": current.get("decision_trace", []),
            "outcome_records": current.get("shadow_trades", []),
            "datasets": self.datasets,
            "opportunities": [],
            "assessments": [],
            "persist": False,
            "as_of_utc": self.analysis_time,
            "records": governed,
            "governed_records": governed,
            "decision_records": selected("decision_trace"),
            "outcome_records": selected("shadow_trades"),
            "dependency_finding": self.runner_context.get("dependency_finding"),
        }
        kwargs = {name: values[name] for name in parameters if name in values}
        if "decision_records" in kwargs and "outcome_records" in kwargs:
            kwargs.pop("datasets", None)
        return kwargs

    def _invoke(self, question: Any) -> dict[str, Any]:
        if self.scope is not None and question.id not in self.scope:
            raise RuntimeError("SCOPED_RUN_OUT_OF_SCOPE:" + str(question.id))
        module = importlib.import_module(question.runner_module)
        function = getattr(module, question.runner_function)
        governed = self._population_cache.setdefault(
            question.id, resolve_question_population(question, self.snapshot),
        )
        shadow_input = (
            governed
            if len(question.data_sources) == 1 and question.data_sources[0].value == "shadow_trades"
            else list(self.snapshot.get("shadow_trades").current_records)
        )
        ingestion_target = "research_engine.data_access.shadow_runtime_ingestion.ingest_completed_shadow_trades"
        source_target = "research_engine.data_access.s3_source.get_default_source"
        loader_values = {
            "load_execution_results": list(self.snapshot.get("execution_results_v1").current_records),
            "load_execution_context": list(self.snapshot.get("execution_context").current_records),
            "load_execution_attempts": list(self.snapshot.get("execution_attempts_v1").current_records),
            "load_protection_audit": list(self.snapshot.get("protection_audit_v1").current_records),
            "load_trade_truth": list(self.snapshot.get("trade_truth").current_records),
            "load_decision_trace": list(self.snapshot.get("decision_trace").current_records),
            "load_risk_deviation": list(self.snapshot.get("risk_deviation_v1").current_records),
            "load_management_actions": list(self.snapshot.get("management_actions").current_records),
        }
        if question.id == "X1":
            # Gate 1's X1 population is already the deterministic
            # result/context join with all required fields.  Supplying the
            # same joined rows to both sides lets the governed runner retain
            # its join semantics without reintroducing excluded source rows.
            # A correlation can own multiple account-grained results.  Match
            # the exact source row embedded in each governed joined row,
            # rather than widening back to every result sharing that key.
            governed_results = [
                item for item in self.snapshot.get("execution_results_v1").current_records
                if any(all(joined.get(key) == value for key, value in item.items()) for joined in governed)
            ]
            governed_ids = {
                str(item.get("correlation_id") or "") for item in governed_results
                if item.get("correlation_id")
            }
            loader_values["load_execution_results"] = [
                *governed_results
            ]
            loader_values["load_execution_context"] = [
                item for item in self.snapshot.get("execution_context").current_records
                if str(item.get("correlation_id") or "") in governed_ids
            ]
        elif question.id == "X2":
            # execution_attempt rows do not satisfy X2's canonical
            # retcode+symbol+result_ok field contract.  Gate 1 therefore
            # excludes them from the 434-row usable population.
            loader_values["load_execution_results"] = list(governed)
            loader_values["load_execution_attempts"] = []
        with ExitStack() as stack:
            stack.enter_context(patch(source_target, return_value=self.frozen_source))
            stack.enter_context(patch(ingestion_target, return_value=shadow_input))
            if hasattr(module, "get_default_source"):
                stack.enter_context(patch.object(module, "get_default_source", return_value=self.frozen_source))
            loaders = importlib.import_module("research_engine.data_access.loaders")
            for name, records in loader_values.items():
                if hasattr(loaders, name):
                    stack.enter_context(patch.object(loaders, name, return_value=records))
            report = function(**self._runner_kwargs(question, function))
        if not isinstance(report, dict):
            raise TypeError("runner did not return a report mapping")
        return report

    def _historically_unanswerable_report(self, question: Any, row: HistoricalQuestionResult) -> dict[str, Any]:
        return {
            "question_id": question.id,
            "status": "INSUFFICIENT_DATA",
            "scientific_state": "HISTORICALLY_UNANSWERABLE",
            "epoch": "CURRENT",
            "overall": {
                "finding": row.reason,
                "historical_exhaustion": row.historical_exhaustion_state,
                "sample_size": 0,
            },
            "dataset": {"source": "+".join(source.value for source in question.data_sources), "sample_size": 0},
            "fingerprint": {
                "epoch": "CURRENT",
                "dataset_id": f"frozen_stage4_{self.upstream_fingerprints['evidence_snapshot'][:16]}",
                "records_used": 0,
                "records_excluded": row.excluded_historical_population or 0,
                "frozen_evidence_snapshot_fingerprint": self.upstream_fingerprints["evidence_snapshot"],
            },
            "confidence": "HIGH",
            "recommendation": "NO_RETROSPECTIVE_ANALYSIS_AVAILABLE",
            "provenance": {
                "authority": "GATE1_GOVERNED_HISTORICAL_ACCOUNTING",
                "runner_module": question.runner_module,
                "runner_function": question.runner_function,
                "candidate_records": row.candidate_historical_population,
                "usable_records": 0,
                "unexplained_records": row.unexplained_historical_population,
            },
            "generated": self.analysis_time,
        }

    def _prepare_report(self, question: Any, report: dict[str, Any], row: HistoricalQuestionResult) -> dict[str, Any]:
        result = deepcopy(report)
        result["question_id"] = question.id
        sample = _report_sample(result)
        if (
            str(result.get("status") or "").upper() == "COMPLETE"
            and (
                str(result.get("confidence") or "").upper() == "INSUFFICIENT_DATA"
                or (sample is not None and sample < 50)
            )
        ):
            result["status"] = "INSUFFICIENT_DATA"
        state = scientific_state_for_report(result)
        result["scientific_state"] = state
        result.setdefault("epoch", "CURRENT")
        fingerprint = result.get("fingerprint")
        if not isinstance(fingerprint, dict):
            fingerprint = {}
            result["fingerprint"] = fingerprint
        fingerprint["epoch"] = "CURRENT"
        fingerprint["frozen_evidence_snapshot_fingerprint"] = self.upstream_fingerprints["evidence_snapshot"]
        fingerprint["governed_candidate_records"] = row.candidate_historical_population
        fingerprint["governed_usable_records"] = row.usable_historical_population
        result["historical_pass"] = {
            "schema": PASS_SCHEMA,
            "analysis_time": self.analysis_time,
            "runner_module": question.runner_module,
            "runner_function": question.runner_function,
            "candidate_records": row.candidate_historical_population,
            "usable_records": row.usable_historical_population,
            "excluded_records": row.excluded_historical_population,
            "unexplained_records": row.unexplained_historical_population,
            "historical_exhaustion": row.historical_exhaustion_state,
            "source_evidence_references": [
                f"evidence-snapshot:{self.upstream_fingerprints['evidence_snapshot']}",
                f"reconstruction:{self.upstream_fingerprints['reconstruction']}",
            ],
        }
        provenance = result.get("provenance")
        if isinstance(provenance, dict) and "report_digest" in provenance:
            from research_engine.control_plane.evidence_provenance import evidence_digest
            material = deepcopy(result)
            material.pop("generated", None)
            material["provenance"].pop("report_digest", None)
            result["provenance"]["report_digest"] = evidence_digest((material,))
        return result

    def _persist(self, question: Any, report: dict[str, Any]) -> Path:
        if not question.report_filename:
            raise RuntimeError("NO_OWNED_REPORT_PATH")
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        target = self.reports_dir / Path(question.report_filename).name
        if target.is_file():
            existing = target.read_bytes()
            archive = self.reports_dir / "history" / f"{target.stem}.{hashlib.sha256(existing).hexdigest()[:16]}.json"
            archive.parent.mkdir(parents=True, exist_ok=True)
            if not archive.exists():
                shutil.copy2(target, archive)
        target.write_text(
            json.dumps(_native(report), indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False),
            encoding="utf-8",
        )
        return target

    def execute(self) -> list[HistoricalQuestionResult]:
        if not self.results:
            self.census()
        updated = dict(self.results)
        for question in self._questions():
            row = updated[question.id]
            classification = row.initial_classification
            if classification == "CURRENT_VALID_RESULT":
                # Repair only stale derived historical-pass state metadata;
                # this does not execute the runner or change its statistics.
                if not (
                    row.current_report_status.upper() == "BLOCKED"
                    and row.scientific_state == "HISTORICALLY_UNANSWERABLE"
                ):
                    continue
                report, _, validity, _ = self._current_report(question)
                if (
                    report is not None
                    and validity == ReportValidity.VALID_CURRENT
                    and str(report.get("status") or "").upper() == "BLOCKED"
                    and str(report.get("scientific_state") or "").upper() == "IMPLEMENTATION_BLOCKED"
                    and isinstance(report.get("historical_pass"), Mapping)
                ):
                    state = scientific_state_for_report(report)
                    if report.get("scientific_state") != state:
                        report = self._prepare_report(question, report, row)
                        path = self._persist(question, report)
                        updated[question.id] = HistoricalQuestionResult(
                            **{
                                **asdict(row),
                                "scientific_state": state,
                                "current_report_path": str(path),
                                "result_fingerprint": _fingerprint(report),
                            }
                        )
                continue
            if classification == "HISTORICAL_RUN_NOT_REQUIRED":
                continue
            if classification == "IMPLEMENTATION_BLOCKED":
                continue
            before_missing = row.current_report_before == ReportValidity.MISSING.value
            replacing = row.current_report_before in {
                ReportValidity.STALE.value, ReportValidity.LEGACY.value, ReportValidity.INVALIDATED.value,
            }
            try:
                if classification == "HISTORICALLY_UNANSWERABLE":
                    if question.id in STRICT_SPECIAL_REPORTS:
                        target = self.reports_dir / Path(question.report_filename).name
                        if target.is_file():
                            current = json.loads(target.read_text(encoding="utf-8"))
                            if (
                                question.id != "EX10"
                                and (current.get("provenance") or {}).get("authority") == "GATE1_GOVERNED_HISTORICAL_ACCOUNTING"
                            ):
                                archives = sorted(
                                    (self.reports_dir / "history").glob(f"{target.stem}.*.json"),
                                    key=lambda item: item.stat().st_mtime,
                                )
                                if archives:
                                    shutil.copy2(archives[0], target)
                        updated[question.id] = HistoricalQuestionResult(
                            **{
                                **asdict(row),
                                "scientific_state": "HISTORICALLY_UNANSWERABLE",
                                "current_report_status": "NO_GOVERNED_SCIENTIFIC_REPORT",
                                "current_report_path": "",
                                "result_fingerprint": _fingerprint({
                                    "question_id": question.id,
                                    "state": "HISTORICALLY_UNANSWERABLE",
                                    "accounting": self.accounting[question.id].get("evidence_accounting", {}),
                                }),
                                "next_action": "NONE",
                                "reason": row.reason,
                                "runner_executed": False,
                                "runner_failed": False,
                                "runner_analytical_population": 0,
                            }
                        )
                        continue
                    report = self._historically_unanswerable_report(question, row)
                    executed = False
                else:
                    report = self._invoke(question)
                    executed = True
                report = self._prepare_report(question, report, row)
                path = self._persist(question, report)
                validity, validity_reason = resolve_report_validity(
                    question.report_filename,
                    report,
                    expected_question_id=question.id,
                    accepted_question_ids=question.legacy_ids,
                )
                if validity != ReportValidity.VALID_CURRENT:
                    raise RuntimeError(f"PERSISTED_REPORT_NOT_CURRENT:{validity.value}:{validity_reason}")
                sample = _report_sample(report)
                if sample is not None and sample > row.usable_historical_population:
                    raise RuntimeError(
                        f"RUNNER_POPULATION_EXCEEDS_GOVERNED_USABLE:{sample}>{row.usable_historical_population}"
                    )
                state = scientific_state_for_report(report)
                updated[question.id] = HistoricalQuestionResult(
                    **{
                        **asdict(row),
                        "current_report_status": str(report.get("status") or "UNKNOWN"),
                        "current_report_path": str(path),
                        "result_fingerprint": _fingerprint(report),
                        "scientific_state": state,
                        "next_action": (
                            "NONE" if state in {"COMPLETE", "NEGATIVE_RESULT", "NO_EFFECT", "HISTORICALLY_UNANSWERABLE"}
                            else "COLLECT_QUANTIFIED_FUTURE_EVIDENCE"
                        ),
                        "reason": validity_reason,
                        "runner_executed": executed,
                        "runner_failed": False,
                        "stale_or_legacy_replaced": replacing,
                        "missing_report_resolved": before_missing,
                        "runner_analytical_population": sample,
                    }
                )
                self.runner_reports[question.id] = report
            except Exception as exc:
                updated[question.id] = HistoricalQuestionResult(
                    **{
                        **asdict(row),
                        "scientific_state": "IMPLEMENTATION_BLOCKED",
                        "next_action": f"Repair {question.runner_module}.{question.runner_function} frozen-input execution",
                        "reason": f"{type(exc).__name__}: {exc}",
                        "runner_executed": classification == "HISTORICAL_RUN_REQUIRED",
                        "runner_failed": True,
                    }
                )
        # Project explicit aliases only after their scientific owner is settled.
        for question in self._questions():
            if not question.scientific_owner_id:
                continue
            if question.scientific_owner_id not in updated:
                raise RuntimeError(
                    "SCOPED_ALIAS_OWNER_OUT_OF_SCOPE:" + question.id)
            row = updated[question.id]
            owner = updated[question.scientific_owner_id]
            updated[question.id] = HistoricalQuestionResult(
                **{
                    **asdict(row),
                    "scientific_state": owner.scientific_state,
                    "current_report_status": owner.current_report_status,
                    "current_report_path": owner.current_report_path,
                    "result_fingerprint": owner.result_fingerprint,
                    "next_action": owner.next_action,
                    "reason": f"Governed scientific alias of {owner.question_id}: {owner.reason}",
                    "runner_analytical_population": owner.runner_analytical_population,
                }
            )
        self.results = updated
        return [updated[question.id] for question in self._questions()]

    def apply_assurance(self, wave4_checkpoint: str | Path | None = None) -> None:
        path = Path(wave4_checkpoint) if wave4_checkpoint else self.checkpoint_dir / "wave4_qualification.json"
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
        qualifications = {
            item["question_id"]: item for item in checkpoint["report"]["qualifications"]
        }
        updated = {}
        for qid, row in self.results.items():
            status = str(qualifications[qid].get("qualification_status") or "UNKNOWN")
            updated[qid] = HistoricalQuestionResult(**{**asdict(row), "assurance_status": status})
        self.results = updated

    def manifest(self) -> dict[str, Any]:
        rows = [asdict(self.results[question.id]) for question in self._questions()]
        counts: dict[str, int] = {state: 0 for state in sorted(SCIENTIFIC_STATES)}
        for row in rows:
            counts[row["scientific_state"]] = counts.get(row["scientific_state"], 0) + 1
        material = {
            "schema": PASS_SCHEMA,
            "stage": "STAGE4_HISTORICAL_RESEARCH_PASS",
            "as_of_utc": self.as_of_utc,
            "analysis_time": self.analysis_time,
            "upstream_fingerprints": self.upstream_fingerprints,
            "question_count": len(rows),
            "scientific_state_counts": counts,
            "questions": rows,
            "summary": {
                "historical_runners_found": sum(row["runner_status"] == "AVAILABLE" for row in rows),
                "historical_runners_executed": sum(row["runner_executed"] for row in rows),
                "current_valid_results_before_pass": sum(row["current_result_valid_before"] for row in rows),
                "new_current_results_generated": sum(
                    row["missing_report_resolved"] or row["stale_or_legacy_replaced"] for row in rows
                ),
                "stale_legacy_results_replaced": sum(row["stale_or_legacy_replaced"] for row in rows),
                "missing_reports_resolved": sum(row["missing_report_resolved"] for row in rows),
                "runner_failures": sum(row["runner_failed"] for row in rows),
                "assurance_verified": sum(row["assurance_status"] == "VERIFIED" for row in rows),
            },
        }
        return {**material, "fingerprint": _fingerprint(material)}

    def scoped_manifest(self) -> dict[str, Any]:
        """Deterministic scientific manifest for the governed scope only.

        This is the artifact consumed by the question-scoped assurance
        recertification.  It deliberately cannot describe the full bank, and
        it fails closed when the executed scope is incomplete.
        """
        if self.scope is None:
            raise RuntimeError("SCOPED_MANIFEST_REQUIRES_QUESTION_SCOPE")
        rows = [asdict(self.results[question.id]) for question in self._questions()
                if question.id in self.results]
        if len(rows) != len(self.scope):
            raise RuntimeError("SCOPED_MANIFEST_INCOMPLETE")
        counts: dict[str, int] = {state: 0 for state in sorted(SCIENTIFIC_STATES)}
        for row in rows:
            counts[row["scientific_state"]] = counts.get(row["scientific_state"], 0) + 1
        material = {
            "schema": PASS_SCHEMA,
            "stage": "STAGE4_HISTORICAL_SCOPED_RESEARCH_PASS",
            "as_of_utc": self.as_of_utc,
            "analysis_time": self.analysis_time,
            "upstream_fingerprints": self.upstream_fingerprints,
            "requested_scope": sorted(self.requested_scope or ()),
            "scope": sorted(self.scope),
            "scope_size": len(self.scope),
            "question_count": len(rows),
            "scientific_state_counts": counts,
            "questions": rows,
            "summary": {
                "historical_runners_found": sum(
                    row["runner_status"] == "AVAILABLE" for row in rows),
                "historical_runners_executed": sum(row["runner_executed"] for row in rows),
                "runner_failures": sum(row["runner_failed"] for row in rows),
                "unrelated_questions_executed": 0,
            },
            "live_or_s3_reads": False,
            "new_scientific_research_run": False,
        }
        return {**material, "fingerprint": _fingerprint(material)}

    def persist_manifest(self) -> Path:
        value = self.manifest()
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(
            json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False),
            encoding="utf-8",
        )
        return self.output_path


def run_questions(
        question_ids: Sequence[str],
        **kwargs: Any) -> HistoricalResearchPass:
    """Question-scoped entry point: execute ONLY the governed scope.

    Reuses the frozen-evidence machinery, the exact governed population
    resolution, the report authority and the deterministic fingerprints of the
    historical pass.  It never reads S3/live data, never widens the scope and
    never executes an unrelated question.
    """
    coordinator = HistoricalResearchPass(question_ids=question_ids, **kwargs)
    coordinator.census()
    coordinator.execute()
    return coordinator


def run_question(question_id: str, **kwargs: Any) -> HistoricalResearchPass:
    """Run exactly one question through the frozen-evidence machinery."""
    return run_questions([question_id], **kwargs)


__all__ = [
    "HistoricalQuestionResult", "HistoricalResearchPass", "run_question",
    "run_questions", "scientific_state_for_report",
]
