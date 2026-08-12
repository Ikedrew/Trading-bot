"""
Knowledge Engine.

Deterministic synthesis of research findings and feedback into
accumulated knowledge state.

Rules:
    - Knowledge is derived from evidence, never invented
    - Supporting and contradicting evidence are tracked separately
    - Status transitions follow explicit rules
    - Historical knowledge states are preserved
    - Knowledge is reconstructable from persisted evidence
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from research_engine.v10.knowledge.model import (
    EvidenceRef,
    KnowledgeItem,
    KnowledgeStatus,
)


# ═══════════════════════════════════════════════════════════════════════════════
# STATUS DETERMINATION
# ═══════════════════════════════════════════════════════════════════════════════


def _determine_status(supporting: int, contradicting: int, total: int) -> str:
    """
    Deterministic knowledge status from evidence counts.

    Rules:
        0 supporting, 0 contradicting → UNRESOLVED
        supporting > 0, contradicting == 0 → SUPPORTED or WEAKLY_SUPPORTED
        supporting > 0, contradicting > 0 → depends on ratio
        contradicting > supporting → CONTRADICTED
        equal → INCONCLUSIVE
    """
    if total == 0:
        return KnowledgeStatus.UNRESOLVED.value

    if contradicting == 0 and supporting > 0:
        if supporting >= 3:
            return KnowledgeStatus.SUPPORTED.value
        return KnowledgeStatus.WEAKLY_SUPPORTED.value

    if supporting == 0 and contradicting > 0:
        return KnowledgeStatus.CONTRADICTED.value

    # Both exist
    if contradicting > supporting:
        return KnowledgeStatus.CONTRADICTED.value
    elif contradicting == supporting:
        return KnowledgeStatus.INCONCLUSIVE.value
    else:
        # supporting > contradicting
        ratio = supporting / (supporting + contradicting)
        if ratio >= 0.75:
            return KnowledgeStatus.WEAKLY_SUPPORTED.value
        return KnowledgeStatus.INCONCLUSIVE.value


def _determine_confidence(evidence_refs: list[EvidenceRef]) -> str:
    """Derive confidence from the quality of supporting evidence."""
    if not evidence_refs:
        return "INSUFFICIENT"

    high_count = sum(1 for e in evidence_refs if e.confidence == "HIGH")
    medium_count = sum(1 for e in evidence_refs if e.confidence == "MEDIUM")

    if high_count >= 2:
        return "HIGH"
    elif high_count >= 1 or medium_count >= 2:
        return "MEDIUM"
    elif medium_count >= 1:
        return "LOW"
    return "INSUFFICIENT"


# ═══════════════════════════════════════════════════════════════════════════════
# KNOWLEDGE ENGINE
# ═══════════════════════════════════════════════════════════════════════════════


class KnowledgeEngine:
    """
    Synthesises research evidence into accumulated knowledge.

    Deterministic. Read-only with respect to trading system.
    Never modifies bot configuration, strategy, or execution.

    Usage:
        engine = KnowledgeEngine()
        knowledge = engine.synthesise_from_findings(findings)
        knowledge = engine.update_item(existing_item, new_finding)
    """

    def synthesise_from_findings(
        self,
        findings: list[dict[str, Any]],
        feedbacks: list[dict[str, Any]] | None = None,
    ) -> list[KnowledgeItem]:
        """
        Synthesise knowledge from a batch of findings (and optional feedback).

        Groups findings by question_id and creates one KnowledgeItem per question.
        """
        # Group findings by question
        by_question: dict[str, list[dict[str, Any]]] = {}
        for f in findings:
            qid = f.get("question_id", "")
            if qid:
                by_question.setdefault(qid, []).append(f)

        # Optional: index feedback by question
        fb_by_question: dict[str, list[dict[str, Any]]] = {}
        if feedbacks:
            for fb in feedbacks:
                qid = fb.get("question_id", "")
                if qid:
                    fb_by_question.setdefault(qid, []).append(fb)

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        items: list[KnowledgeItem] = []

        for qid, question_findings in by_question.items():
            item = self._synthesise_question(qid, question_findings, fb_by_question.get(qid, []), now)
            items.append(item)

        return items

    def update_item(
        self,
        existing: KnowledgeItem,
        new_finding: dict[str, Any],
    ) -> KnowledgeItem:
        """
        Update an existing knowledge item with a new finding.

        Returns a new version (does not mutate the original).
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        ref = self._make_evidence_ref(new_finding)

        # Classify as supporting or contradicting
        outcome = new_finding.get("outcome", "").upper()
        is_positive = outcome in ("POSITIVE", "PREDICTIVE", "WELL_CALIBRATED")
        is_negative = outcome in ("NEGATIVE", "NOT_PREDICTIVE", "POORLY_CALIBRATED")

        new_supporting = list(existing.supporting_evidence)
        new_contradicting = list(existing.contradicting_evidence)

        # Determine based on existing knowledge direction
        if existing.status in (KnowledgeStatus.SUPPORTED.value, KnowledgeStatus.WEAKLY_SUPPORTED.value):
            if is_positive:
                new_supporting.append(ref)
            elif is_negative:
                new_contradicting.append(ref)
        elif existing.status == KnowledgeStatus.CONTRADICTED.value:
            if is_negative:
                new_supporting.append(ref)  # supports the contradiction
            elif is_positive:
                new_contradicting.append(ref)  # contradicts the contradiction
        else:
            # INCONCLUSIVE/UNRESOLVED — classify by direction
            if is_positive:
                new_supporting.append(ref)
            elif is_negative:
                new_contradicting.append(ref)

        total = len(new_supporting) + len(new_contradicting)
        status = _determine_status(len(new_supporting), len(new_contradicting), total)
        confidence = _determine_confidence(new_supporting)

        return KnowledgeItem(
            knowledge_id=existing.knowledge_id,
            subject=existing.subject,
            system_area=existing.system_area,
            statement=existing.statement,
            status=status,
            confidence=confidence,
            supporting_evidence=new_supporting,
            contradicting_evidence=new_contradicting,
            evidence_count=total,
            knowledge_version=existing.knowledge_version + 1,
            first_observed_at=existing.first_observed_at,
            last_updated_at=now,
            source_universes=existing.source_universes,
            universe_versions=new_finding.get("universe_versions", existing.universe_versions),
            population_versions=new_finding.get("population_versions", existing.population_versions),
        )

    def _synthesise_question(
        self,
        question_id: str,
        findings: list[dict[str, Any]],
        feedbacks: list[dict[str, Any]],
        now: str,
    ) -> KnowledgeItem:
        """Create a knowledge item from all findings for one question."""
        supporting: list[EvidenceRef] = []
        contradicting: list[EvidenceRef] = []

        latest = findings[-1] if findings else {}
        title = latest.get("title", question_id)
        universes_used = latest.get("universes_used", [])

        for f in findings:
            ref = self._make_evidence_ref(f)
            outcome = f.get("outcome", "").upper()

            if outcome in ("POSITIVE", "PREDICTIVE", "WELL_CALIBRATED", "COMPLETED"):
                supporting.append(ref)
            elif outcome in ("NEGATIVE", "NOT_PREDICTIVE", "POORLY_CALIBRATED"):
                contradicting.append(ref)
            # INCONCLUSIVE/ANALYSIS_FAILED don't count as support or contradiction

        total = len(supporting) + len(contradicting)
        status = _determine_status(len(supporting), len(contradicting), total)
        confidence = _determine_confidence(supporting) if supporting else "INSUFFICIENT"

        # Determine system area from question prefix
        system_area = self._area_from_id(question_id, universes_used)

        # Build statement from latest finding
        conclusion = latest.get("conclusion", "")
        statement = conclusion if conclusion and conclusion != "No conclusion" else f"Research question {question_id}: {title}"

        first_ts = findings[0].get("run_timestamp", now) if findings else now

        return KnowledgeItem(
            knowledge_id=f"k_{question_id}",
            subject=title,
            system_area=system_area,
            statement=statement,
            status=status,
            confidence=confidence,
            supporting_evidence=supporting,
            contradicting_evidence=contradicting,
            evidence_count=total,
            knowledge_version=1,
            first_observed_at=first_ts,
            last_updated_at=now,
            source_universes=universes_used,
            universe_versions=latest.get("universe_versions", {}),
            population_versions=latest.get("population_versions", {}),
        )

    def _make_evidence_ref(self, finding: dict[str, Any]) -> EvidenceRef:
        return EvidenceRef(
            question_id=finding.get("question_id", ""),
            run_id=finding.get("run_id", ""),
            outcome=finding.get("outcome", ""),
            confidence=finding.get("confidence", ""),
            feedback_type="",
            timestamp=finding.get("run_timestamp", ""),
        )

    def _area_from_id(self, question_id: str, universes_used: list[str]) -> str:
        qid = question_id.upper()
        cross_prefixes = ("ED", "EM", "ES", "DM", "DS", "MS", "EDM", "EDS", "DMS", "EDMS")
        if any(qid.startswith(p) for p in cross_prefixes):
            return "CROSS_UNIVERSE"
        prefix_map = {"E-": "EXECUTION", "D-": "DECISION", "M-": "MARKET", "S-": "STRATEGY", "R-": "RISK", "O-": "OUTCOME"}
        for prefix, area in prefix_map.items():
            if qid.startswith(prefix):
                return area
        if len(universes_used) == 1:
            return universes_used[0]
        if len(universes_used) > 1:
            return "CROSS_UNIVERSE"
        return "UNKNOWN"
