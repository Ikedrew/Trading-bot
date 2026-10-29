"""
Campaign Engine — Runner that orchestrates multi-question investigations.

Reuses:
    - ExperimentRunner (question execution)
    - Research Governance (confidence, maturity, decision)
    - Segmentation Engine (population filtering)
    - Domain Registry (question-to-domain resolution)
"""

from __future__ import annotations

import logging
import time
from typing import Any

from research_engine.v10.campaigns.models import (
    ResearchCampaign, CampaignResult, CampaignFinding,
)
from research_engine.v10.campaigns.campaign_registry import CampaignRegistry
from research_engine.v10.research_intelligence import ExperimentRunner
from research_engine.v10.research_governance import validate_finding, rank_findings
from research_engine.v10.research_governance.models import ResearchFinding
from research_engine.v10.domains.base import get_default_registry

logger = logging.getLogger(__name__)


class CampaignRunner:
    """
    Executes complete research campaigns.

    Flow:
        Campaign definition
        → Load questions
        → Resolve domains
        → Apply filters
        → Run ExperimentRunner per question
        → Apply Governance per finding
        → Rank findings
        → Generate campaign result
    """

    def __init__(
        self,
        universe_file: str | None = None,
        reports_dir: str | None = None,
    ):
        self._campaign_registry = CampaignRegistry()
        self._experiment_runner = ExperimentRunner(
            universe_file=universe_file,
            reports_dir=reports_dir,
        )
        self._domain_registry = get_default_registry()

    def run_campaign(
        self,
        campaign_id: str,
        filters: dict[str, str] | None = None,
    ) -> CampaignResult:
        """
        Execute a registered research campaign.

        Args:
            campaign_id: Registered campaign ID (e.g., "FX_OPT_V1")
            filters: Additional filters to merge with campaign defaults

        Returns:
            CampaignResult with prioritised findings and recommendations.
        """
        start = time.time()

        campaign = self._campaign_registry.get(campaign_id)
        if not campaign:
            return CampaignResult(
                campaign_id=campaign_id,
                error=f"Campaign '{campaign_id}' not found in registry",
            )

        # Merge campaign filters with any runtime overrides
        merged_filters = {**campaign.filters, **(filters or {})}

        logger.info(f"[CAMPAIGN] Starting: {campaign.name} ({len(campaign.questions)} questions)")

        # Execute each question
        findings: list[ResearchFinding] = []
        questions_executed = 0
        questions_failed = 0
        data_gaps: list[str] = []

        for qid in campaign.questions:
            # Check if question has an active experiment module
            q_def = self._experiment_runner.registry.get(qid)
            if not q_def:
                data_gaps.append(f"{qid}: Not registered in question registry")
                continue
            if q_def.status != "active":
                data_gaps.append(f"{qid}: Status is '{q_def.status}' (not active)")
                continue

            # Execute with governance
            try:
                gov_result = self._experiment_runner.run_with_governance(qid, filters=merged_filters)
                gov = gov_result["governance"]
                finding = ResearchFinding(
                    finding_id=gov.get("finding_id", ""),
                    question_id=gov.get("question_id", qid),
                    question_name=gov.get("question_name", ""),
                    sample_size=gov.get("sample", {}).get("size", 0),
                    sample_status=gov.get("sample", {}).get("status", ""),
                    result_value=gov.get("result", {}).get("value", 0),
                    result_metric=gov.get("result", {}).get("metric", ""),
                    result_data=gov.get("result", {}).get("data", {}),
                    confidence_level=gov.get("confidence", {}).get("level", "LOW"),
                    confidence_score=gov.get("confidence", {}).get("score", 0),
                    confidence_factors=gov.get("confidence", {}).get("factors", []),
                    evidence_maturity=gov.get("evidence", {}).get("maturity", ""),
                    decision_status=gov.get("decision", {}).get("status", ""),
                    decision_reason=gov.get("decision", {}).get("reason", ""),
                    next_step=gov.get("validation", {}).get("next_step", ""),
                    status=gov.get("status", ""),
                    recommendation=gov.get("recommendation", ""),
                    limitations=gov.get("limitations", []),
                    priority=gov.get("priority", ""),
                    priority_score=gov.get("priority_score", 0),
                    population_filters=gov.get("population", {}).get("filters", {}),
                )
                findings.append(finding)
                questions_executed += 1
            except Exception as exc:
                questions_failed += 1
                logger.warning(f"[CAMPAIGN] Question {qid} failed: {exc}")

        # Rank findings
        ranked = rank_findings(findings) if findings else []

        # Build campaign findings
        campaign_findings = []
        for f in ranked:
            domain = self._domain_registry.resolve_question_domain(f.question_id)
            campaign_findings.append(CampaignFinding(
                question_id=f.question_id,
                question_name=f.question_name,
                domain=domain.domain_id if domain else "unknown",
                sample_size=f.sample_size,
                result_value=f.result_value,
                result_metric=f.result_metric,
                confidence=f.confidence_level,
                evidence_maturity=f.evidence_maturity,
                decision_status=f.decision_status,
                priority=f.priority,
                priority_score=f.priority_score,
                recommendation=f.recommendation,
                next_step=f.next_step,
                limitations=f.limitations,
            ))

        # Generate recommendations from high-priority findings
        recommendations = _generate_recommendations(campaign_findings)

        # Confidence and evidence summaries
        conf_summary = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
        evidence_summary = {}
        for f in campaign_findings:
            conf_summary[f.confidence] = conf_summary.get(f.confidence, 0) + 1
            evidence_summary[f.evidence_maturity] = evidence_summary.get(f.evidence_maturity, 0) + 1

        result = CampaignResult(
            campaign_id=campaign.campaign_id,
            campaign_name=campaign.name,
            objective=campaign.objective,
            filters_applied=merged_filters,
            questions_executed=questions_executed,
            questions_failed=questions_failed,
            findings=campaign_findings,
            recommendations=recommendations,
            data_gaps=data_gaps,
            confidence_summary=conf_summary,
            evidence_summary=evidence_summary,
            execution_time_seconds=round(time.time() - start, 1),
        )

        logger.info(
            f"[CAMPAIGN] Complete: {campaign.name} — "
            f"{questions_executed} executed, {len(campaign_findings)} findings, "
            f"{len(recommendations)} recommendations"
        )

        return result

    @property
    def registry(self) -> CampaignRegistry:
        return self._campaign_registry


def _generate_recommendations(findings: list[CampaignFinding]) -> list[str]:
    """Generate actionable recommendations from prioritised findings."""
    recs = []
    for f in findings:
        if f.priority == "HIGH" and f.decision_status in ("SUPPORTED", "REJECTED", "EARLY_FAILURE"):
            if f.decision_status == "EARLY_FAILURE":
                recs.append(f"URGENT: {f.question_name} shows early failure. {f.next_step}")
            elif f.decision_status == "REJECTED":
                recs.append(f"ACTION: {f.question_name} hypothesis rejected. Investigate alternatives.")
            elif f.decision_status == "SUPPORTED":
                recs.append(f"CONFIRMED: {f.question_name} is supported. Consider implementation.")
        elif f.priority in ("HIGH", "MEDIUM") and f.decision_status == "PROMISING":
            recs.append(f"INVESTIGATE: {f.question_name} shows promise. {f.next_step}")
    return recs
