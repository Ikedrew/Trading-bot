"""
Independent Question Product System.

Each research question has its own persistent product:
    reports/research/questions/{QID}/
        question.json    - Definition and metadata (contract)
        latest.json      - Most recent finding (structured)
        latest.md        - Human-readable latest finding
        history/         - All previous findings (never overwritten)
            {RUN_ID}.json
            {RUN_ID}.md

This module manages creation, updating, navigation, and comparison of products.
The container is standardised. The evidence inside is question-specific.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_engine.v10.control_plane.finding_schema import (
    ResearchFinding,
    compare_findings,
)

logger = logging.getLogger(__name__)

_QUESTIONS_DIR = Path("reports/research/questions")


class QuestionProductManager:
    """Manages independent question research products."""

    def __init__(self, base_dir: Path | str | None = None):
        self._base = Path(base_dir) if base_dir else _QUESTIONS_DIR

    # ─── INITIALISATION ───────────────────────────────────────────────────────

    def initialise_product(
        self,
        question_id: str,
        question_definition: dict[str, Any],
    ) -> Path:
        """
        Create the product directory and question.json for a question.

        Args:
            question_id: The question's canonical ID.
            question_definition: Full question definition (from to_dict()).

        Returns:
            Path to the product directory.
        """
        product_dir = self._base / question_id
        product_dir.mkdir(parents=True, exist_ok=True)
        (product_dir / "history").mkdir(exist_ok=True)

        defn_path = product_dir / "question.json"
        defn_path.write_text(
            json.dumps(question_definition, indent=2, default=str),
            encoding="utf-8",
        )
        return product_dir

    def initialise_all(
        self,
        questions: tuple[Any, ...],
    ) -> int:
        """
        Initialise product directories for all questions in the bank.

        Returns count of products initialised.
        """
        count = 0
        for q in questions:
            self.initialise_product(q.question_id, q.to_dict())
            count += 1
        return count

    # ─── FINDING PERSISTENCE ──────────────────────────────────────────────────

    def save_finding(
        self,
        finding: ResearchFinding,
    ) -> Path:
        """
        Save a research finding for a question.

        - Compares with previous finding and records changes
        - Writes latest.json (overwrites with newest)
        - Writes latest.md (overwrites)
        - Appends to history/ (immutable — never overwrites)

        Args:
            finding: The complete structured finding.

        Returns:
            Path to the saved latest.json.
        """
        qid = finding.question_id
        product_dir = self._base / qid
        product_dir.mkdir(parents=True, exist_ok=True)
        (product_dir / "history").mkdir(exist_ok=True)

        # Compare with previous
        previous = self.get_latest_finding(qid)
        if previous:
            finding.previous_run_id = previous.get("run_id", "")
            finding.previous_outcome = previous.get("outcome", "")
            finding.changes_from_previous = compare_findings(previous, finding)

        finding_dict = finding.to_dict()

        # Write latest.json
        latest_path = product_dir / "latest.json"
        latest_path.write_text(
            json.dumps(finding_dict, indent=2, default=str),
            encoding="utf-8",
        )

        # Write latest.md
        md_path = product_dir / "latest.md"
        md_path.write_text(
            self._generate_markdown(finding),
            encoding="utf-8",
        )

        # Write history (immutable)
        run_id = finding.run_id or "unknown"
        history_json = product_dir / "history" / f"{run_id}.json"
        history_json.write_text(
            json.dumps(finding_dict, indent=2, default=str),
            encoding="utf-8",
        )
        history_md = product_dir / "history" / f"{run_id}.md"
        history_md.write_text(
            self._generate_markdown(finding),
            encoding="utf-8",
        )

        return latest_path

    # ─── RETRIEVAL ────────────────────────────────────────────────────────────

    def get_latest_finding(self, question_id: str) -> dict[str, Any] | None:
        """Get the latest finding for a question, or None if never run."""
        latest_path = self._base / question_id / "latest.json"
        if not latest_path.exists():
            return None
        try:
            return json.loads(latest_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def get_question_definition(self, question_id: str) -> dict[str, Any] | None:
        """Get the question definition."""
        defn_path = self._base / question_id / "question.json"
        if not defn_path.exists():
            return None
        try:
            return json.loads(defn_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def get_history(self, question_id: str) -> list[dict[str, Any]]:
        """Get all historical findings for a question, newest first."""
        history_dir = self._base / question_id / "history"
        if not history_dir.exists():
            return []
        findings = []
        for f in sorted(history_dir.glob("*.json"), reverse=True):
            try:
                findings.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                continue
        return findings

    def get_history_count(self, question_id: str) -> int:
        """Get the number of historical runs for a question."""
        history_dir = self._base / question_id / "history"
        if not history_dir.exists():
            return 0
        return len(list(history_dir.glob("*.json")))

    def get_all_product_ids(self) -> list[str]:
        """List all question IDs that have products."""
        if not self._base.exists():
            return []
        return sorted(
            d.name for d in self._base.iterdir()
            if d.is_dir() and (d / "question.json").exists()
        )

    def has_product(self, question_id: str) -> bool:
        """Check if a question has an initialised product."""
        return (self._base / question_id / "question.json").exists()

    def product_health(self, question_id: str) -> dict[str, Any]:
        """Get health summary for a question product."""
        has_defn = (self._base / question_id / "question.json").exists()
        has_latest = (self._base / question_id / "latest.json").exists()
        history_count = self.get_history_count(question_id)
        latest = self.get_latest_finding(question_id)

        return {
            "question_id": question_id,
            "has_definition": has_defn,
            "has_latest_finding": has_latest,
            "history_count": history_count,
            "latest_outcome": latest.get("outcome", "") if latest else "",
            "latest_confidence": latest.get("confidence", "") if latest else "",
            "latest_run_id": latest.get("run_id", "") if latest else "",
            "gaps_count": len(latest.get("research_gaps", [])) if latest else 0,
        }

    # ─── MARKDOWN GENERATION ──────────────────────────────────────────────────

    def _generate_markdown(self, finding: ResearchFinding) -> str:
        """Generate a comprehensive human-readable research report."""
        lines = []

        # Header
        lines.append(f"# {finding.question_id}: {finding.title}")
        lines.append("")
        lines.append(f"**Run:** {finding.run_id}")
        lines.append(f"**Timestamp:** {finding.run_timestamp}")
        lines.append(f"**Outcome:** {finding.outcome}")
        lines.append(f"**Confidence:** {finding.confidence}")
        lines.append("")

        # What was asked
        lines.append("## Research Intent")
        lines.append("")
        defn = self.get_question_definition(finding.question_id)
        if defn:
            lines.append(defn.get("research_intent", "See question.json"))
        lines.append("")

        # What data was used
        lines.append("## Data Used")
        lines.append("")
        lines.append(f"- **Universes:** {', '.join(finding.universes_used)}")
        lines.append(f"- **Populations:** {', '.join(finding.populations_used)}")
        if finding.filters_applied:
            lines.append(f"- **Filters:** {finding.filters_applied}")
        for pop, size in finding.sample_sizes.items():
            lines.append(f"- **{pop}:** {size} records")
        lines.append("")

        # What did we find — primary metrics
        if finding.primary_metrics:
            lines.append("## Primary Metrics")
            lines.append("")
            for k, v in finding.primary_metrics.items():
                lines.append(f"- **{k}:** {v}")
            lines.append("")

        # Evidence (question-specific)
        if finding.evidence:
            lines.append("## Evidence")
            lines.append("")
            for k, v in finding.evidence.items():
                if isinstance(v, dict):
                    lines.append(f"### {k}")
                    for sk, sv in v.items():
                        lines.append(f"- {sk}: {sv}")
                elif isinstance(v, list):
                    lines.append(f"### {k}")
                    for item in v:
                        lines.append(f"- {item}")
                else:
                    lines.append(f"- **{k}:** {v}")
            lines.append("")

        # Four-angle breakdown
        if finding.angle_evidence:
            lines.append("## Four-Angle Evidence")
            lines.append("")
            for angle, data in finding.angle_evidence.items():
                lines.append(f"### {angle}")
                for k, v in data.items():
                    lines.append(f"- {k}: {v}")
                lines.append("")

        # Anomaly view
        if finding.anomaly_view:
            lines.append("## Anomaly View")
            lines.append("")
            for k, v in finding.anomaly_view.items():
                lines.append(f"- **{k}:** {v}")
            lines.append("")

        # Exceptional view
        if finding.exceptional_view:
            lines.append("## Exceptional View")
            lines.append("")
            for k, v in finding.exceptional_view.items():
                lines.append(f"- **{k}:** {v}")
            lines.append("")

        # Conclusion
        lines.append("## Conclusion")
        lines.append("")
        lines.append(finding.conclusion or "No conclusion recorded.")
        lines.append("")
        if finding.recommendation:
            lines.append(f"**Recommendation:** {finding.recommendation}")
            lines.append("")

        # Limitations
        if finding.limitations:
            lines.append("## Limitations")
            lines.append("")
            for lim in finding.limitations:
                lines.append(f"- {lim}")
            lines.append("")

        # Changes from previous
        if finding.changes_from_previous:
            lines.append("## Changes from Previous Run")
            lines.append("")
            changes = finding.changes_from_previous
            if changes.get("status") == "first_run":
                lines.append("*First run — no previous finding to compare.*")
            elif changes.get("status") == "no_material_change":
                lines.append("*No material change from previous run.*")
            else:
                for k, v in changes.items():
                    if k == "status":
                        continue
                    lines.append(f"- **{k}:** {v}")
            lines.append("")

        # Research gaps
        if finding.research_gaps:
            lines.append("## Research Gaps Identified")
            lines.append("")
            for gap in finding.research_gaps:
                lines.append(f"- **{gap.gap_id}** [{gap.gap_type}]: {gap.description}")
                if gap.suggested_question:
                    lines.append(f"  - Suggested follow-up: {gap.suggested_question}")
            lines.append("")

        # Reproducibility
        lines.append("## Reproducibility")
        lines.append("")
        lines.append(f"- Engine: {finding.engine_version}")
        lines.append(f"- Question version: {finding.question_version}")
        lines.append(f"- Analysis version: {finding.analysis_version}")
        if finding.population_versions:
            for pop, ver in finding.population_versions.items():
                lines.append(f"- Population {pop}: {ver}")
        lines.append("")

        return "\n".join(lines) + "\n"
