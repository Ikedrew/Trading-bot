"""Build the canonical, read-only state projection for research questions."""
from __future__ import annotations

import ast
from functools import lru_cache
from importlib.util import find_spec
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from research_engine.control_plane.evidence_resolver import (
    EvidenceResolution,
    EvidenceSnapshot,
    resolve_question_evidence,
)
from research_engine.control_plane.invalidation import build_invalidations_dict
from research_engine.control_plane.models import (
    ApplicationStatus,
    CandidateState,
    CanonicalReportRecord,
    QuestionState,
    ReadinessStatus,
    ReportValidity,
    RunnerStatus,
)
from research_engine.control_plane.report_history import resolve_report_history
from research_engine.control_plane.report_resolver import (
    _extract_confidence,
    _extract_epoch,
    _extract_finding,
    _extract_sample_size,
    _extract_status,
    _extract_timestamp,
    load_report_for_question,
    report_contains_authoritative_result,
    resolve_report_validity,
)
from research_engine.control_plane.readiness import resolve_readiness
from research_engine.control_plane.decision_ledger import DecisionLedger, DecisionState
from research_engine.control_plane.application_ledger import ApplicationLedger, ApplicationState
from research_engine.registry.research_question_registry import REGISTRY


_CALIBRATION_ARTIFACT = Path(
    "analysis/artifacts/calibration/score_calibration_curve.json"
)


def _required_sample_size(question: Any) -> int | None:
    values: list[int] = []
    for rule in question.validation_rules:
        if (
            rule.field == "sample_size"
            and rule.operator in (">", ">=")
            and isinstance(rule.threshold, (int, float))
        ):
            values.append(int(rule.threshold) + (1 if rule.operator == ">" else 0))
    return max(values) if values else None


def _required_evidence(question: Any) -> list[str]:
    evidence = [f"data_source:{source.value}" for source in question.data_sources]
    evidence.extend(f"required_field:{field}" for field in question.required_fields)
    evidence.extend(
        f"validation_rule:{rule.field} {rule.operator} {rule.threshold}"
        for rule in question.validation_rules
    )
    evidence.extend(f"depends_on:{question_id}" for question_id in question.depends_on)
    return evidence


@lru_cache(maxsize=None)
def _inspect_runner(module_name: str, function_name: str) -> tuple[RunnerStatus, str]:
    if not module_name or not function_name:
        return RunnerStatus.NO_RUNNER, "No runner metadata declared"
    try:
        spec = find_spec(module_name)
    except (ImportError, ModuleNotFoundError, AttributeError) as exc:
        return RunnerStatus.ERROR, f"Runner module resolution failed: {type(exc).__name__}: {exc}"
    if spec is None:
        return RunnerStatus.ERROR, "Declared runner module cannot be resolved"
    if not spec.origin:
        return RunnerStatus.ERROR, "Declared runner module has no inspectable source"
    try:
        tree = ast.parse(Path(spec.origin).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        return RunnerStatus.ERROR, f"Runner source inspection failed: {type(exc).__name__}: {exc}"
    declared_functions = {
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    if function_name not in declared_functions:
        return RunnerStatus.ERROR, "Declared runner function is absent from module source"
    return RunnerStatus.READY, "Runner module and function are present (not executed)"


def _runner_status(question: Any) -> tuple[RunnerStatus, str]:
    return _inspect_runner(question.runner_module, question.runner_function)


@lru_cache(maxsize=1)
def _unique_legacy_aliases() -> frozenset[str]:
    counts: dict[str, int] = {}
    for question in REGISTRY:
        for alias in question.legacy_ids:
            alias = alias.upper()
            counts[alias] = counts.get(alias, 0) + 1
    return frozenset(alias for alias, count in counts.items() if count == 1)


def _candidate_states(question: Any, candidates: Iterable[Any]) -> list[CandidateState]:
    unique_aliases = _unique_legacy_aliases()
    accepted = {question.id.upper()}
    accepted.update(alias.upper() for alias in question.legacy_ids if alias.upper() in unique_aliases)
    matched: list[CandidateState] = []
    for candidate in candidates:
        source = str(candidate.created_from_question or "").upper()
        if not source or source not in accepted:
            continue
        history = list(candidate.validation_history)
        matched.append(CandidateState(
            candidate_id=candidate.candidate_id,
            status=candidate.status,
            created_from_question=candidate.created_from_question,
            description=candidate.description,
            risk_level=candidate.risk_level,
            has_validation=bool(history),
            validation_count=len(history),
            latest_validation_decision=(history[-1].decision if history else ""),
        ))
    return sorted(matched, key=lambda item: item.candidate_id)


def _unmapped_candidate_ids(candidates: Iterable[Any]) -> list[str]:
    reliable_sources = {question.id.upper() for question in REGISTRY}
    reliable_sources.update(_unique_legacy_aliases())
    return sorted(
        candidate.candidate_id
        for candidate in candidates
        if not str(candidate.created_from_question or "").upper()
        or str(candidate.created_from_question).upper() not in reliable_sources
    )


def _load_candidates(candidate_registry: Any | None) -> tuple[list[Any], str]:
    try:
        if candidate_registry is None:
            from research_engine.v10.candidates import CandidateRegistry

            candidate_registry = CandidateRegistry()
        return list(candidate_registry.list_all()), ""
    except Exception as exc:
        return [], f"Candidate registry unavailable: {type(exc).__name__}: {exc}"


def _production_application_status(
    question: Any,
    application_evidence: Mapping[str, str] | None,
) -> tuple[ApplicationStatus, str]:
    if application_evidence and question.id in application_evidence:
        try:
            return ApplicationStatus(application_evidence[question.id]), "Explicit evidence supplied"
        except ValueError:
            return ApplicationStatus.UNKNOWN, "Unrecognised application evidence"

    # Q20 is the canonical D2 question's declared legacy identity.  This
    # artifact directly says it is research-only and does not alter runtime.
    if question.id == "D2" and "Q20" in question.legacy_ids and _CALIBRATION_ARTIFACT.is_file():
        try:
            artifact = json.loads(_CALIBRATION_ARTIFACT.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            artifact = {}
        note = str(artifact.get("note", "")).lower()
        if artifact.get("status") == "RESEARCH_ARTIFACT" and "does not alter runtime" in note:
            return ApplicationStatus.NOT_APPLIED, "Calibration artifact explicitly says it does not alter runtime"

    return ApplicationStatus.UNKNOWN, "No direct runtime/configuration application evidence"


def _next_action(
    state_status: str,
    report_validity: ReportValidity,
    runner_status: RunnerStatus,
    candidate_status: str,
) -> str:
    if runner_status == RunnerStatus.NO_RUNNER:
        return "Implement or explicitly retire the missing runner"
    if runner_status == RunnerStatus.ERROR:
        return "Repair the declared runner import/function"
    if report_validity in (ReportValidity.INVALIDATED, ReportValidity.STALE, ReportValidity.LEGACY):
        return "Rerun with CURRENT evidence when the question is ready"
    if state_status == "NOT_RUN":
        return "Run the experiment when readiness is confirmed"
    if state_status == "WAITING_DATA":
        return "Collect required evidence; do not treat the report as a finding"
    if state_status == "BLOCKED":
        return "Resolve the reported blocker before running"
    if candidate_status == "READY_FOR_REVIEW":
        return "Human candidate review required"
    if state_status == "COMPLETE":
        return "Review the finding; production application remains separately governed"
    return "Establish trustworthy readiness before action"


def _build_state(
    question: Any,
    *,
    reports_dir: str | Path | None,
    evidence: EvidenceResolution,
    dependency_states: Mapping[str, str],
    candidates: Iterable[Any],
    unmapped_candidate_ids: list[str],
    candidate_error: str,
    application_evidence: Mapping[str, str] | None,
) -> QuestionState:
    report, report_path = load_report_for_question(
        question.id, question.report_filename, reports_dir=reports_dir
    )
    validity, validity_reason = resolve_report_validity(
        question.report_filename,
        report,
        expected_question_id=question.id,
        accepted_question_ids=question.legacy_ids,
    )
    runner_status, runner_reason = _runner_status(question)
    mapped_candidates = _candidate_states(question, candidates)
    if not mapped_candidates:
        candidate_status = "NONE"
        candidate_id = ""
        candidate_mapping_status = "UNMAPPED" if unmapped_candidate_ids else "NONE"
    else:
        statuses = sorted({candidate.status for candidate in mapped_candidates})
        candidate_status = statuses[0] if len(statuses) == 1 else "MULTIPLE"
        candidate_id = mapped_candidates[0].candidate_id
        candidate_mapping_status = "MAPPED"

    application_status, application_reason = _production_application_status(
        question, application_evidence
    )
    has_result = report_contains_authoritative_result(report, validity)
    extracted_report_status = _extract_status(report or {})
    report_status = extracted_report_status or ("UNKNOWN" if report else "NOT_RUN")
    readiness_status, readiness_reason = resolve_readiness(
        question,
        evidence,
        runner_status,
        validity,
        report_status,
        dependency_states,
    )
    state_status = readiness_status.value

    warnings: list[str] = []
    if report and isinstance(report.get("warnings"), list):
        warnings.extend(str(value) for value in report["warnings"])
    if validity != ReportValidity.VALID_CURRENT:
        warnings.append(validity_reason)
    if runner_status != RunnerStatus.READY:
        warnings.append(runner_reason)
    if evidence.error:
        warnings.append(evidence.error)
    if candidate_error:
        warnings.append(candidate_error)
    if unmapped_candidate_ids:
        warnings.append(
            "Candidate registry contains candidates without a reliable canonical question mapping"
        )
    if application_status == ApplicationStatus.UNKNOWN:
        warnings.append(application_reason)

    overall = report.get("overall") if report else None
    latest_result = overall if has_result and isinstance(overall, dict) else None
    current_sample = evidence.usable_count
    evidence_metrics = dict(evidence.metrics)
    if current_sample is None and has_result:
        current_sample = _extract_sample_size(report or {})
        evidence_metrics["sample_source"] = "VALID_CURRENT completed report"
    available_evidence = []
    if report is not None:
        available_evidence.append(f"report:{report_path}")
    epoch = _extract_epoch(report or {})
    if epoch:
        available_evidence.append(f"epoch:{epoch}")

    # Canonical report layer integration
    reports_dir_path = Path(reports_dir) if reports_dir is not None else Path("analysis/reports")
    known_invalidations = build_invalidations_dict()
    canonical_history = resolve_report_history(
        question.id,
        question.report_filename,
        reports_dir_path,
        known_invalidations,
    )

    # Governance layer integration
    governance = _resolve_governance(
        question,
        mapped_candidates,
    )
    resolved_application_status = application_status
    if governance["latest_application_event"] is not None:
        resolved_application_status = ApplicationStatus(
            governance["production_application_status"]
        )

    return QuestionState(
        question_id=question.id,
        title=question.title,
        description=question.description,
        category=question.category.value,
        state_status=state_status,
        readiness_status=readiness_status,
        readiness_reason=readiness_reason,
        required_sample_size=_required_sample_size(question),
        required_evidence=_required_evidence(question),
        available_evidence=available_evidence,
        evidence_sources=evidence.sources,
        evidence_metrics=evidence_metrics,
        requirements=evidence.requirements_dict(),
        excluded_evidence_count=evidence.excluded_count,
        runner_status=runner_status,
        runner_module=question.runner_module,
        runner_function=question.runner_function,
        current_sample_size=current_sample,
        evidence_epoch=epoch,
        latest_report_path=report_path if report is not None else "",
        latest_report_status=report_status,
        latest_result=latest_result,
        latest_finding=_extract_finding(report or {}) if has_result else "",
        confidence=_extract_confidence(report or {}) if has_result else "",
        report_validity=validity,
        report_validity_reason=validity_reason,
        last_run_at=_extract_timestamp(report or {}),
        candidate_status=candidate_status,
        candidate_id=candidate_id,
        candidates=[candidate.to_dict() for candidate in mapped_candidates],
        candidate_mapping_status=candidate_mapping_status,
        unmapped_candidate_ids=unmapped_candidate_ids,
        production_application_status=resolved_application_status,
        warnings=sorted(set(warnings)),
        next_action=_next_action(
            state_status, validity, runner_status, candidate_status
        ),
        # Canonical report layer
        authoritative_report=canonical_history.authoritative_report,
        report_history=canonical_history.report_history,
        report_history_count=canonical_history.report_history_count,
        invalidated_report_count=canonical_history.invalidated_report_count,
        stale_report_count=canonical_history.stale_report_count,
        superseded_report_count=canonical_history.superseded_report_count,
        legacy_report_count=canonical_history.legacy_report_count,
        malformed_report_count=canonical_history.malformed_report_count,
        latest_valid_run_timestamp=canonical_history.latest_valid_run_timestamp,
        # Governance layer
        candidate_count=governance["candidate_count"],
        decision_status=governance["decision_status"],
        latest_decision=governance["latest_decision"],
        latest_application_event=governance["latest_application_event"],
        governance_warnings=governance["governance_warnings"],
        next_governance_action=governance["next_governance_action"],
    )


def _resolve_governance(
    question: Any,
    mapped_candidates: list[CandidateState],
    decision_ledger: DecisionLedger | None = None,
    application_ledger: ApplicationLedger | None = None,
) -> dict[str, Any]:
    """Resolve governance state for a question from decision and application ledgers."""
    if decision_ledger is None:
        decision_ledger = DecisionLedger()
    if application_ledger is None:
        application_ledger = ApplicationLedger()

    candidate_count = len(mapped_candidates)

    if candidate_count == 0:
        return {
            "candidate_count": 0,
            "decision_status": DecisionState.NOT_REVIEWED,
            "latest_decision": None,
            "production_application_status": ApplicationState.NOT_APPLIED,
            "latest_application_event": None,
            "governance_warnings": [],
            "next_governance_action": "No candidate exists; research finding is not yet proposed as a change",
        }

    # Decision status: use the most advanced decision across all mapped candidates
    all_decisions = decision_ledger.get_for_question(question.id)
    latest_decision = None
    decision_status = DecisionState.NOT_REVIEWED
    if all_decisions:
        latest = max(all_decisions, key=lambda d: d.decided_at)
        latest_decision = latest.to_dict()
        decision_status = latest.decision

    # Application status: use the most recent application event across all mapped candidates
    all_events = application_ledger.get_for_question(question.id)
    latest_event = None
    app_status = ApplicationState.NOT_APPLIED
    if all_events:
        latest = max(all_events, key=lambda e: e.occurred_at)
        latest_event = latest.to_dict()
        app_status = latest.state

    # Build governance warnings
    gov_warnings: list[str] = []
    if decision_status == DecisionState.APPROVED and app_status == ApplicationState.NOT_APPLIED:
        gov_warnings.append("Candidate approved but not yet deployed")
    if app_status == ApplicationState.DEPLOYED and decision_status != DecisionState.APPROVED:
        gov_warnings.append("Deployment recorded without explicit APPROVED decision")
    if app_status == ApplicationState.VERIFIED and decision_status != DecisionState.APPROVED:
        gov_warnings.append("Verification recorded without explicit APPROVED decision")

    # Next governance action
    next_gov = "No production change is authorized"
    if decision_status == DecisionState.NOT_REVIEWED:
        next_gov = "Awaiting human governance review"
    elif decision_status == DecisionState.UNDER_REVIEW:
        next_gov = "Governance review in progress"
    elif decision_status == DecisionState.APPROVED:
        if app_status == ApplicationState.NOT_APPLIED:
            next_gov = "Approved for deployment; awaiting deployment execution"
        elif app_status == ApplicationState.DEPLOYED:
            next_gov = "Deployed; verification recommended"
        elif app_status == ApplicationState.VERIFIED:
            next_gov = "Deployed and verified"
    elif decision_status == DecisionState.REJECTED:
        next_gov = "Candidate rejected; no production change authorized"
    elif decision_status == DecisionState.DEFERRED:
        next_gov = "Decision deferred; awaiting further information"

    return {
        "candidate_count": candidate_count,
        "decision_status": decision_status,
        "latest_decision": latest_decision,
        "production_application_status": app_status,
        "latest_application_event": latest_event,
        "governance_warnings": gov_warnings,
        "next_governance_action": next_gov,
    }


def build_all_question_states(
    *,
    reports_dir: str | Path | None = None,
    evidence_source: EvidenceSnapshot | Mapping[str, list[dict[str, Any]]] | None = None,
    candidate_registry: Any | None = None,
    application_evidence: Mapping[str, str] | None = None,
) -> list[QuestionState]:
    """Return all canonical states in canonical registry order."""
    snapshot = evidence_source if isinstance(evidence_source, EvidenceSnapshot) else EvidenceSnapshot(evidence_source)
    candidates, candidate_error = _load_candidates(candidate_registry)
    unmapped = _unmapped_candidate_ids(candidates)
    return _build_selected_states(
        {question.id for question in REGISTRY},
        snapshot=snapshot,
        reports_dir=reports_dir,
        candidates=candidates,
        unmapped=unmapped,
        candidate_error=candidate_error,
        application_evidence=application_evidence,
        isolate_failures=True,
    )


def _build_selected_states(
    selected_ids: set[str],
    *,
    snapshot: EvidenceSnapshot,
    reports_dir: str | Path | None,
    candidates: list[Any],
    unmapped: list[str],
    candidate_error: str,
    application_evidence: Mapping[str, str] | None,
    isolate_failures: bool = False,
) -> list[QuestionState]:
    """Build a dependency-resolved subset, returned in canonical registry order."""
    by_id = {question.id: question for question in REGISTRY}
    states_by_id: dict[str, QuestionState] = {}
    visiting: set[str] = set()

    def error_state(question: Any, exc: Exception) -> QuestionState:
        reason = f"{type(exc).__name__}: {exc}"
        try:
            runner_status, _ = _runner_status(question)
        except Exception:
            runner_status = RunnerStatus.ERROR
        return QuestionState(
            question_id=question.id,
            title=question.title,
            description=question.description,
            category=question.category.value,
            state_status="ERROR",
            readiness_status=ReadinessStatus.ERROR,
            readiness_reason=f"Question state resolution failed: {reason}",
            required_sample_size=_required_sample_size(question),
            required_evidence=_required_evidence(question),
            runner_status=runner_status,
            runner_module=question.runner_module,
            runner_function=question.runner_function,
            latest_report_status="ERROR",
            report_validity=ReportValidity.UNKNOWN,
            report_validity_reason="Question state resolution did not complete",
            warnings=[reason],
            next_action="Repair the reported question state resolution error",
            governance_warnings=["Governance state could not be resolved"],
            next_governance_action="Resolve the question state error before governance action",
        )

    def build(canonical_id: str) -> QuestionState:
        if canonical_id in states_by_id:
            return states_by_id[canonical_id]
        if canonical_id in visiting:
            raise ValueError(f"Cyclic canonical research dependency at {canonical_id}")
        visiting.add(canonical_id)
        question = by_id[canonical_id]
        try:
            dependency_states = {
                dependency: build(dependency).state_status
                for dependency in question.depends_on
            }
            evidence = resolve_question_evidence(question, snapshot)
            state = _build_state(
                question,
                reports_dir=reports_dir,
                evidence=evidence,
                dependency_states=dependency_states,
                candidates=candidates,
                unmapped_candidate_ids=unmapped,
                candidate_error=candidate_error,
                application_evidence=application_evidence,
            )
        except Exception as exc:
            if not isolate_failures:
                raise
            state = error_state(question, exc)
        finally:
            visiting.discard(canonical_id)
        states_by_id[canonical_id] = state
        return state

    for question in REGISTRY:
        if question.id in selected_ids:
            build(question.id)
    return [states_by_id[question.id] for question in REGISTRY if question.id in selected_ids]


def _resolve_question(question_id: str) -> Any:
    requested = question_id.upper()
    exact = [question for question in REGISTRY if question.id.upper() == requested]
    if exact:
        return exact[0]
    aliases = [
        question for question in REGISTRY
        if requested in {alias.upper() for alias in question.legacy_ids}
    ]
    if len(aliases) == 1:
        return aliases[0]
    if len(aliases) > 1:
        raise KeyError(f"Legacy question ID {question_id!r} maps to multiple canonical questions")
    raise KeyError(f"Unknown canonical question ID {question_id!r}")


def build_question_state(
    question_id: str,
    *,
    reports_dir: str | Path | None = None,
    evidence_source: EvidenceSnapshot | Mapping[str, list[dict[str, Any]]] | None = None,
    candidate_registry: Any | None = None,
    application_evidence: Mapping[str, str] | None = None,
) -> QuestionState:
    """Build one canonical question state; unique declared legacy IDs resolve safely."""
    question = _resolve_question(question_id)
    by_id = {item.id: item for item in REGISTRY}
    selected: set[str] = set()

    def add_with_dependencies(canonical_id: str) -> None:
        if canonical_id in selected:
            return
        selected.add(canonical_id)
        for dependency in by_id[canonical_id].depends_on:
            add_with_dependencies(dependency)

    add_with_dependencies(question.id)
    snapshot = evidence_source if isinstance(evidence_source, EvidenceSnapshot) else EvidenceSnapshot(evidence_source)
    candidates, candidate_error = _load_candidates(candidate_registry)
    states = _build_selected_states(
        selected,
        snapshot=snapshot,
        reports_dir=reports_dir,
        candidates=candidates,
        unmapped=_unmapped_candidate_ids(candidates),
        candidate_error=candidate_error,
        application_evidence=application_evidence,
        isolate_failures=False,
    )
    return next(state for state in states if state.question_id == question.id)
