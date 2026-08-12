"""
Cockpit Data Aggregator.

Reads all existing research state and produces a unified data model
for the cockpit UI. No research logic — only data consumption.

Sources:
    - Control Plane state (reports/research/control_plane_state.json)
    - Question Products (reports/research/questions/*/latest.json)
    - Run Manifests (reports/research/runs/*.json)
    - Universe Contract Audit (reports/research/universe_contract_audit.json)
    - Correlation Audit (reports/research/execution_decision_correlation_audit.json)
    - Question Bank (canonical definitions)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_REPORTS_DIR = Path("reports/research")


@dataclass
class QuestionSummary:
    """Summary of one question for the cockpit."""
    question_id: str = ""
    title: str = ""
    research_intent: str = ""
    angles: list[str] = field(default_factory=list)
    status: str = ""  # COMPLETE, INCONCLUSIVE, BLOCKED, ERROR, NOT_RUN
    outcome: str = ""
    confidence: str = ""
    sample_size: int = 0
    last_run: str = ""
    anomaly_status: str = ""
    exceptional_status: str = ""
    gaps_count: int = 0
    history_count: int = 0
    primary_metrics: dict[str, Any] = field(default_factory=dict)
    conclusion: str = ""
    previous_outcome: str = ""
    change_summary: str = ""


@dataclass
class CockpitData:
    """Complete aggregated data for the cockpit UI."""
    # Overview
    engine_version: str = ""
    last_run_id: str = ""
    last_run_timestamp: str = ""
    last_run_duration: float = 0.0

    # Question counts
    total_questions: int = 0
    complete: int = 0
    inconclusive: int = 0
    blocked: int = 0
    error: int = 0
    not_run: int = 0

    # Development
    active_questions: int = 0
    candidate_questions: int = 0
    total_gaps: int = 0

    # Universe health
    universes: list[dict[str, Any]] = field(default_factory=list)

    # Population health
    populations_valid: int = 0
    populations_empty: int = 0
    populations_degraded: int = 0

    # Correlation
    correlation_summary: dict[str, Any] = field(default_factory=dict)

    # Questions by angle
    execution_questions: list[QuestionSummary] = field(default_factory=list)
    decision_questions: list[QuestionSummary] = field(default_factory=list)
    market_questions: list[QuestionSummary] = field(default_factory=list)
    strategy_questions: list[QuestionSummary] = field(default_factory=list)
    cross_angle_questions: list[QuestionSummary] = field(default_factory=list)

    # All questions (flat)
    all_questions: list[QuestionSummary] = field(default_factory=list)

    # Run history
    run_history: list[dict[str, Any]] = field(default_factory=list)

    # Changes from previous run
    finding_changes: list[dict[str, Any]] = field(default_factory=list)


class CockpitDataAggregator:
    """
    Reads all existing research state and produces CockpitData.

    Does NOT perform any analysis. Only reads persisted state.
    """

    def __init__(self, reports_dir: Path | str | None = None):
        self._reports = Path(reports_dir) if reports_dir else _REPORTS_DIR

    def aggregate(self) -> CockpitData:
        """Aggregate all research state into CockpitData."""
        data = CockpitData()

        # Load run history FIRST — establishes canonical latest run
        self._load_runs(data)

        # Load control plane state (non-run metadata only)
        self._load_control_plane(data)

        # Override header metadata from canonical latest run manifest
        self._apply_canonical_run_metadata(data)

        # Load question products
        self._load_questions(data)

        # Load universe health
        self._load_universe_health(data)

        # Load correlation
        self._load_correlation(data)

        # Compute changes
        self._compute_changes(data)

        return data

    def _apply_canonical_run_metadata(self, data: CockpitData) -> None:
        """
        Derive header metadata from the latest run manifest.

        The run manifests in reports/research/runs/ are the canonical source
        of truth for run identity, timestamp, and duration. This ensures the
        cockpit header always reflects the actual latest run regardless of
        whether control_plane_state.json is up to date.
        """
        if not data.run_history:
            return
        latest = data.run_history[0]  # Already sorted newest-first by _load_runs
        data.last_run_id = latest.get("run_id", data.last_run_id)
        data.last_run_timestamp = latest.get("timestamp", data.last_run_timestamp)
        data.last_run_duration = latest.get("duration_seconds", data.last_run_duration)

    def _load_control_plane(self, data: CockpitData) -> None:
        path = self._reports / "control_plane_state.json"
        if not path.exists():
            return
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            data.engine_version = state.get("engine_version", "")
            data.last_run_id = state.get("last_run_id", "")
            data.last_run_timestamp = state.get("last_run_timestamp", "")
            data.active_questions = state.get("questions_active", 0)
            data.candidate_questions = state.get("candidate_questions_count", 0)
            data.populations_valid = state.get("populations_valid", 0)
            data.populations_empty = state.get("populations_empty", 0)
            data.populations_degraded = state.get("populations_degraded", 0)

            run = state.get("latest_run")
            if run:
                data.last_run_duration = run.get("duration_seconds", 0)
        except Exception as e:
            logger.warning(f"[COCKPIT] Failed to load control plane: {e}")

    def _load_questions(self, data: CockpitData) -> None:
        questions_dir = self._reports / "questions"
        if not questions_dir.exists():
            return

        from research_engine.v10.universes.question_bank import QUESTION_BANK

        for q in QUESTION_BANK:
            summary = self._build_question_summary(q, questions_dir)
            data.all_questions.append(summary)

            # Classify by angle
            angles = [u.value for u in q.required_universes]
            if len(angles) == 1:
                if "EXECUTION" in angles:
                    data.execution_questions.append(summary)
                elif "DECISION" in angles:
                    data.decision_questions.append(summary)
                elif "MARKET" in angles:
                    data.market_questions.append(summary)
                elif "STRATEGY" in angles:
                    data.strategy_questions.append(summary)
            else:
                data.cross_angle_questions.append(summary)

            # Count statuses
            if summary.status == "COMPLETE":
                data.complete += 1
            elif summary.status == "INCONCLUSIVE":
                data.inconclusive += 1
            elif summary.status == "BLOCKED":
                data.blocked += 1
            elif summary.status == "ERROR":
                data.error += 1
            else:
                data.not_run += 1

            data.total_gaps += summary.gaps_count

        data.total_questions = len(data.all_questions)

    def _build_question_summary(
        self, question: Any, questions_dir: Path
    ) -> QuestionSummary:
        qid = question.question_id
        latest_path = questions_dir / qid / "latest.json"
        history_dir = questions_dir / qid / "history"

        summary = QuestionSummary(
            question_id=qid,
            title=question.title,
            research_intent=question.research_intent[:200],
            angles=[u.value for u in question.required_universes],
        )

        # History count
        if history_dir.exists():
            summary.history_count = len(list(history_dir.glob("*.json")))

        # Load latest finding
        if latest_path.exists():
            try:
                finding = json.loads(latest_path.read_text(encoding="utf-8"))
                summary.outcome = finding.get("outcome", "")
                summary.confidence = finding.get("confidence", "")
                summary.last_run = finding.get("run_timestamp", "")
                summary.conclusion = finding.get("conclusion", "")[:300]
                summary.sample_size = finding.get("sample_sizes", {}).get("total", 0)
                summary.primary_metrics = finding.get("primary_metrics", {})
                summary.gaps_count = len(finding.get("research_gaps", []))
                summary.previous_outcome = finding.get("previous_outcome", "")

                # Anomaly/exceptional status
                av = finding.get("anomaly_view", {})
                summary.anomaly_status = av.get("status", "N/A") if av else "N/A"
                ev = finding.get("exceptional_view", {})
                summary.exceptional_status = ev.get("status", "N/A") if ev else "N/A"

                # Determine display status
                conf = summary.confidence
                if conf == "INSUFFICIENT":
                    summary.status = "INCONCLUSIVE"
                elif summary.outcome == "ANALYSIS_FAILED":
                    summary.status = "ERROR"
                else:
                    summary.status = "COMPLETE"

                # Change summary
                changes = finding.get("changes_from_previous", {})
                if changes.get("outcome_changed"):
                    c = changes["outcome_changed"]
                    summary.change_summary = f"{c['from']} -> {c['to']}"
                elif changes.get("status") == "first_run":
                    summary.change_summary = "First run"
                elif changes.get("status") == "no_material_change":
                    summary.change_summary = "No change"

            except Exception:
                summary.status = "ERROR"
        else:
            # Check if blocked in contract
            from research_engine.v10.universes.models import QuestionStatus
            if question.status == QuestionStatus.BLOCKED:
                summary.status = "BLOCKED"
            else:
                summary.status = "NOT_RUN"

        return summary

    def _load_runs(self, data: CockpitData) -> None:
        runs_dir = self._reports / "runs"
        if not runs_dir.exists():
            return

        for run_file in sorted(runs_dir.glob("*.json"), reverse=True):
            try:
                run = json.loads(run_file.read_text(encoding="utf-8"))
                data.run_history.append({
                    "run_id": run.get("run_id", ""),
                    "timestamp": run.get("timestamp", ""),
                    "questions_requested": run.get("questions_requested", 0),
                    "questions_executed": run.get("questions_executed", 0),
                    "questions_blocked": run.get("questions_blocked", 0),
                    "questions_failed": run.get("questions_failed", 0),
                    "questions_inconclusive": run.get("questions_inconclusive", 0),
                    "duration_seconds": run.get("duration_seconds", 0),
                })
            except Exception:
                continue

    def _load_universe_health(self, data: CockpitData) -> None:
        path = self._reports / "universe_contract_audit.json"
        if not path.exists():
            return
        try:
            audit = json.loads(path.read_text(encoding="utf-8"))
            data.universes = audit.get("universe_summary", [])
        except Exception:
            pass

    def _load_correlation(self, data: CockpitData) -> None:
        path = self._reports / "execution_decision_correlation_audit.json"
        if not path.exists():
            return
        try:
            corr = json.loads(path.read_text(encoding="utf-8"))
            data.correlation_summary = {
                "classification": corr.get("classification", ""),
                "coverage_rate": corr.get("historical_coverage", {}).get("coverage_rate", 0),
                "correlated": corr.get("historical_coverage", {}).get("correlated", 0),
                "uncorrelated": corr.get("historical_coverage", {}).get("uncorrelated", 0),
                "method": corr.get("contract", {}).get("correlation_method", ""),
            }
        except Exception:
            pass

    def _compute_changes(self, data: CockpitData) -> None:
        """Collect finding changes for the changes view."""
        for q in data.all_questions:
            if q.change_summary and q.change_summary not in ("First run", "No change"):
                data.finding_changes.append({
                    "question_id": q.question_id,
                    "title": q.title,
                    "change": q.change_summary,
                    "current_outcome": q.outcome,
                    "previous_outcome": q.previous_outcome,
                })
