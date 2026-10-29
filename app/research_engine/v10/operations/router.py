"""
Research Operations — Execution router.

Dispatches research actions to the existing infrastructure.
Does NOT duplicate research logic — delegates to existing runners.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from research_engine.v10.base import timestamp_now

logger = logging.getLogger(__name__)


class ResearchRouter:
    """
    Central dispatch for all research operations.

    Supported actions:
        run_question            — Execute a single research question
        run_campaign            — Execute a registered campaign
        run_segmented_research  — Execute a question with segmentation filters
        run_candidate_validation — Validate a candidate against baseline
        run_shadow_processing   — Process trades against active shadow candidates
        generate_dashboard      — Generate the candidate evaluation dashboard
        generate_report         — Generate the operational research report
        get_state               — Return current research state
    """

    def __init__(self, universe_file: str | None = None):
        self._universe_file = universe_file or self._resolve_universe_file()
        self._init_error: str | None = None

    @classmethod
    def create(cls, universe_file: str | None = None) -> "ResearchRouter":
        """Factory that captures init errors gracefully."""
        try:
            return cls(universe_file=universe_file)
        except RuntimeError as exc:
            router = object.__new__(cls)
            router._universe_file = None
            router._init_error = str(exc)
            return router

    def _resolve_universe_file(self) -> str | None:
        """
        Resolve the universe file path.

        When RESEARCH_STORAGE=s3, downloads the universe from S3 to /tmp/
        so ExperimentRunner/ResearchSegmenter can read it as a local file.

        When RESEARCH_STORAGE=local (default), returns None to let
        downstream modules use their default local paths.
        """
        import os
        backend = os.environ.get("RESEARCH_STORAGE", "local")
        if backend != "s3":
            return None  # Local mode — use default paths

        from research_engine.v10.operations.storage import ResearchStorage
        storage = ResearchStorage(backend="s3")
        content = storage.load_universe()

        if not content:
            raise RuntimeError(
                "Research universe could not be loaded from S3. "
                f"Bucket={os.environ.get('RESEARCH_BUCKET', '?')} "
                f"Key={os.environ.get('RESEARCH_UNIVERSE_KEY', '?')}"
            )

        # Validate it's non-empty JSONL
        lines = [l for l in content.splitlines() if l.strip()]
        if not lines:
            raise RuntimeError("Research universe from S3 is empty (0 records)")

        # Write to temp file for downstream consumption
        import tempfile
        tmp_dir = os.environ.get("LAMBDA_TASK_ROOT_TMP", "/tmp")
        tmp_path = os.path.join(tmp_dir, "research_universe.jsonl")
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)

        logger.info(f"[ROUTER] S3 universe downloaded: {len(lines)} records -> {tmp_path}")
        return tmp_path

    def execute(self, event: dict[str, Any]) -> dict[str, Any]:
        """
        Execute a research action from an event payload.

        Args:
            event: {"action": str, ...params}

        Returns:
            Result dict (JSON-serialisable).
        """
        # Check for initialization errors (e.g., S3 download failure)
        if hasattr(self, "_init_error") and self._init_error:
            return {
                "error": f"Research infrastructure error: {self._init_error}",
                "_action": event.get("action", ""),
                "_duration_seconds": 0,
                "_timestamp": timestamp_now(),
            }

        action = event.get("action", "")
        start = time.time()

        try:
            if action == "run_question":
                result = self._run_question(event)
            elif action == "run_campaign":
                result = self._run_campaign(event)
            elif action == "run_segmented_research":
                result = self._run_segmented(event)
            elif action == "run_candidate_validation":
                result = self._run_validation(event)
            elif action == "run_shadow_processing":
                result = self._run_shadow(event)
            elif action == "generate_dashboard":
                result = self._generate_dashboard(event)
            elif action == "generate_report":
                result = self._generate_report(event)
            elif action == "get_state":
                result = self._get_state(event)
            elif action in ("run_canonical_question", "run_canonical_bank", "resolve_question"):
                result = self._run_canonical(event)
            else:
                result = {"error": f"Unknown action: '{action}'"}
        except Exception as exc:
            result = {"error": f"{type(exc).__name__}: {exc}"}

        result["_action"] = action
        result["_duration_seconds"] = round(time.time() - start, 2)
        result["_timestamp"] = timestamp_now()
        return result

    # ─── ACTION HANDLERS ──────────────────────────────────────

    def _run_question(self, event: dict) -> dict:
        from research_engine.v10.research_intelligence import ExperimentRunner
        runner = ExperimentRunner(universe_file=self._universe_file)
        qid = event.get("question_id", "")
        filters = event.get("filters", {})
        result = runner.run_with_governance(qid, filters=filters or None)
        return {"question_id": qid, "result": result}

    def _run_campaign(self, event: dict) -> dict:
        from research_engine.v10.campaigns import CampaignRunner
        runner = CampaignRunner(universe_file=self._universe_file)
        campaign_id = event.get("campaign_id", "")
        filters = event.get("filters")
        result = runner.run_campaign(campaign_id, filters=filters)
        return result.to_dict()

    def _run_segmented(self, event: dict) -> dict:
        from research_engine.v10.research_intelligence import ExperimentRunner
        runner = ExperimentRunner(universe_file=self._universe_file)
        qid = event.get("question_id", "")
        filters = event.get("filters", {})
        result = runner.run_with_governance(qid, filters=filters)
        return {"question_id": qid, "filters": filters, "result": result}

    def _run_validation(self, event: dict) -> dict:
        from research_engine.v10.validation_lab import ValidationRunner
        from research_engine.v10.candidates import CandidateRegistry
        runner = ValidationRunner(universe_file=self._universe_file)
        candidate_id = event.get("candidate_id", "")
        # Try to load candidate from registry for change definition
        registry = CandidateRegistry()
        candidate = registry.get(candidate_id)
        if candidate:
            changes = candidate.change_definition
            baseline_id = candidate.baseline_id
        else:
            changes = event.get("changes", {})
            baseline_id = event.get("baseline_id", "")
        filters = event.get("filters")
        result = runner.validate(
            candidate_id=candidate_id,
            changes=changes,
            baseline_id=baseline_id,
            filters=filters,
        )
        return result.to_dict()

    def _run_shadow(self, event: dict) -> dict:
        from research_engine.v10.shadow import ShadowRunner
        runner = ShadowRunner()
        trades = event.get("trades", [])
        total = 0
        for trade in trades:
            runner.process_trade(trade)
            total += 1
        active = runner.registry.list_active()
        return {
            "trades_processed": total,
            "active_shadows": len(active),
            "shadow_ids": [c.shadow_id for c in active],
        }

    def _generate_dashboard(self, event: dict) -> dict:
        from research_engine.v10.candidates import CandidateEvaluationReport
        report = CandidateEvaluationReport()
        return report.generate()

    def _generate_report(self, event: dict) -> dict:
        from research_engine.v10.operations.research_report import generate_operational_report
        return generate_operational_report(universe_file=self._universe_file)

    def _get_state(self, event: dict) -> dict:
        from research_engine.v10.operations.state import get_research_state
        return get_research_state()

    def _run_canonical(self, event: dict) -> dict:
        """Delegate to the canonical Lambda research adapter."""
        from research_engine.v10.runner.lambda_adapter import LambdaResearchAdapter
        adapter = LambdaResearchAdapter()
        return adapter.handle(event)
