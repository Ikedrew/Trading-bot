"""
Proposal Prioritisation / Ranking.

Deterministic, evidence-based ranking of research proposals.

Produces RESEARCH PRIORITY, not DEPLOYMENT PRIORITY.
A high-ranked proposal means "investigate first" not "deploy immediately."

Ranking factors:
    1. Evidence strength (confidence, outcome severity)
    2. Impact (metric magnitude)
    3. Affected population size
    4. Experimentability (can current infrastructure test it?)
    5. Confidence
    6. Redundancy/overlap penalty

NEVER modifies the trading bot. NEVER deploys or promotes.
"""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# PRIORITY VOCABULARY
# ═══════════════════════════════════════════════════════════════════════════════


class PriorityBand(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    DEFERRED = "DEFERRED"


class NextAction(str, Enum):
    DESIGN_CANDIDATE = "DESIGN_CANDIDATE"
    RUN_EXPERIMENT = "RUN_EXPERIMENT"
    GATHER_MORE_DATA = "GATHER_MORE_DATA"
    INVESTIGATE_OVERLAP = "INVESTIGATE_OVERLAP"
    BLOCKED_BY_SIMULATION = "BLOCKED_BY_SIMULATION"
    DEFER = "DEFER"


# ═══════════════════════════════════════════════════════════════════════════════
# MODEL
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class ProposalPriority:
    """Deterministic priority for one proposal."""
    proposal_id: str = ""
    rank: int = 0
    priority_score: float = 0.0
    priority_band: str = PriorityBand.DEFERRED.value
    next_action: str = NextAction.DEFER.value

    # Factor scores (0-100 scale each)
    evidence_score: float = 0.0
    impact_score: float = 0.0
    population_score: float = 0.0
    experimentability_score: float = 0.0
    confidence_score: float = 0.0
    redundancy_penalty: float = 0.0

    # Context
    system_area: str = ""
    finding_outcome: str = ""
    finding_confidence: str = ""
    sample_size: int = 0
    has_candidate: bool = False
    has_validation: bool = False

    ranking_reason: str = ""
    blockers: list[str] = field(default_factory=list)

    # Lineage
    source_finding_ids: list[str] = field(default_factory=list)
    source_feedback_ids: list[str] = field(default_factory=list)

    governance_note: str = (
        "This is a research investigation priority. "
        "It does not authorize modification of the trading system."
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "rank": self.rank,
            "priority_score": round(self.priority_score, 2),
            "priority_band": self.priority_band,
            "next_action": self.next_action,
            "evidence_score": round(self.evidence_score, 2),
            "impact_score": round(self.impact_score, 2),
            "population_score": round(self.population_score, 2),
            "experimentability_score": round(self.experimentability_score, 2),
            "confidence_score": round(self.confidence_score, 2),
            "redundancy_penalty": round(self.redundancy_penalty, 2),
            "system_area": self.system_area,
            "finding_outcome": self.finding_outcome,
            "finding_confidence": self.finding_confidence,
            "sample_size": self.sample_size,
            "has_candidate": self.has_candidate,
            "has_validation": self.has_validation,
            "ranking_reason": self.ranking_reason,
            "blockers": self.blockers,
            "source_finding_ids": self.source_finding_ids,
            "source_feedback_ids": self.source_feedback_ids,
            "governance_note": self.governance_note,
        }


@dataclass
class ProposalRanking:
    """Complete deterministic ranking of all proposals."""
    ranking_version: str = ""
    ranked_at: str = ""
    methodology_version: str = "1.0.0"
    total_proposals: int = 0
    priorities: list[ProposalPriority] = field(default_factory=list)
    universe_versions: dict[str, str] = field(default_factory=dict)
    population_versions: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ranking_version": self.ranking_version,
            "ranked_at": self.ranked_at,
            "methodology_version": self.methodology_version,
            "total_proposals": self.total_proposals,
            "priorities": [p.to_dict() for p in self.priorities],
            "universe_versions": self.universe_versions,
            "population_versions": self.population_versions,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# RANKING ENGINE
# ═══════════════════════════════════════════════════════════════════════════════


# Factor weights (transparent, adjustable as provisional policy)
_WEIGHTS = {
    "evidence": 25.0,
    "impact": 30.0,
    "population": 15.0,
    "experimentability": 20.0,
    "confidence": 10.0,
}


class ProposalRanker:
    """
    Deterministic evidence-based proposal ranking.

    Scores proposals using transparent factors derived from
    existing research evidence. Never modifies the trading system.
    """

    def __init__(self, questions_dir: Path | str | None = None, proposals_dir: Path | str | None = None):
        self._questions_dir = Path(questions_dir) if questions_dir else Path("reports/research/questions")
        self._proposals_dir = Path(proposals_dir) if proposals_dir else Path("reports/research/proposals")

    def rank(self) -> ProposalRanking:
        """
        Rank all existing proposals using persisted evidence.

        Returns a deterministic, reproducible ranking.
        """
        proposals = self._load_proposals()
        findings = self._load_findings()
        feedbacks = self._load_feedbacks()

        priorities: list[ProposalPriority] = []

        for prop in proposals:
            priority = self._score_proposal(prop, findings, feedbacks)
            priorities.append(priority)

        # Apply redundancy penalty
        self._apply_redundancy(priorities)

        # Sort: higher score first. Tie-break: proposal_id alphabetically
        priorities.sort(key=lambda p: (-p.priority_score, p.proposal_id))

        # Assign ranks and bands
        for i, p in enumerate(priorities):
            p.rank = i + 1
            p.priority_band = self._assign_band(p.priority_score)
            p.next_action = self._determine_next_action(p)

        # Build ranking artifact
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        content = json.dumps([p.to_dict() for p in priorities], sort_keys=True, default=str)
        version_hash = hashlib.sha256(content.encode()).hexdigest()[:12]

        return ProposalRanking(
            ranking_version=f"rank_{version_hash}",
            ranked_at=now,
            total_proposals=len(priorities),
            priorities=priorities,
        )

    def _score_proposal(
        self,
        proposal: dict[str, Any],
        findings: dict[str, dict[str, Any]],
        feedbacks: dict[str, dict[str, Any]],
    ) -> ProposalPriority:
        """Score a single proposal using available evidence."""
        pid = proposal.get("proposal_id", "")
        system_area = proposal.get("system_area", "")
        source_finding_ids = proposal.get("source_finding_ids", [])
        source_feedback_ids = proposal.get("source_feedback_ids", [])

        # Extract question_id from finding reference
        qid = ""
        if source_finding_ids:
            # Format: "E-001_run_id"
            ref = source_finding_ids[0]
            parts = ref.split("_run_")
            if parts:
                qid = parts[0]

        finding = findings.get(qid, {})
        feedback = feedbacks.get(qid, {})

        outcome = finding.get("outcome", "")
        confidence = finding.get("confidence", "")
        sample_sizes = finding.get("sample_sizes", {})
        metrics = finding.get("primary_metrics", {})
        limitations = finding.get("limitations", [])
        analytical_sample = sample_sizes.get("analytical_sample", 0)
        population = sample_sizes.get("population", 0)

        # ─── Evidence Quality Assessment ──────────────────────────────
        # Detect metric-specific sample vs population mismatch
        metric_sample = metrics.get("count", analytical_sample)  # Actual metric observations
        if not isinstance(metric_sample, int):
            metric_sample = analytical_sample

        # Detect small-sample limitation in finding text
        has_small_sample_warning = any("small sample" in l.lower() for l in limitations)

        # Load knowledge confidence if available
        knowledge = self._load_knowledge(qid)
        knowledge_confidence = knowledge.get("confidence", "") if knowledge else ""

        # Determine effective evidence quality
        evidence_quality = self._assess_evidence_quality(
            metric_sample, analytical_sample, population,
            confidence, knowledge_confidence, has_small_sample_warning
        )

        # ─── Factor 1: Evidence Strength ──────────────────────────────
        evidence = 0.0
        if outcome in ("NEGATIVE", "NOT_PREDICTIVE", "POORLY_CALIBRATED"):
            evidence = 80.0  # Clear negative signal
        elif outcome == "POSITIVE":
            evidence = 30.0  # Positive = lower investigation priority
        elif outcome == "INCONCLUSIVE":
            evidence = 40.0
        else:
            evidence = 20.0

        # Boost for repeated evidence (multiple runs)
        # (Cannot determine from single latest.json — use confidence as proxy)
        if confidence == "HIGH":
            evidence = min(100, evidence + 20)
        elif confidence == "MEDIUM":
            evidence = min(100, evidence + 10)

        # ─── EVIDENCE QUALITY PENALTY ─────────────────────────────────
        # Critical: penalise when metric sample is much smaller than population
        evidence_quality_penalty = 0.0
        if evidence_quality == "INSUFFICIENT":
            evidence_quality_penalty = 50.0  # Severe penalty
        elif evidence_quality == "WEAK":
            evidence_quality_penalty = 25.0
        elif evidence_quality == "MODERATE":
            evidence_quality_penalty = 5.0

        # ─── Factor 2: Impact ─────────────────────────────────────────
        impact = 0.0
        mean_r = metrics.get("mean_r")
        if mean_r is not None:
            # Magnitude of negative expectancy
            if mean_r < -0.3:
                impact = 90.0
            elif mean_r < -0.1:
                impact = 70.0
            elif mean_r < 0:
                impact = 50.0
            elif mean_r > 0:
                impact = 20.0  # Positive = less urgent
            else:
                impact = 30.0
        else:
            impact = 30.0  # Unknown impact

        # ─── Factor 3: Affected Population ────────────────────────────
        pop_score = 0.0
        if population > 0 and analytical_sample > 0:
            ratio = analytical_sample / max(population, 1)
            pop_score = min(100, ratio * 100)
        elif analytical_sample > 50:
            pop_score = 80.0
        elif analytical_sample > 20:
            pop_score = 50.0

        # ─── Factor 4: Experimentability ──────────────────────────────
        exp_score = 0.0
        # Single-universe EXECUTION/MARKET/STRATEGY questions are
        # generally filterable. Cross-universe is harder.
        has_candidate = (self._proposals_dir / pid / "candidate.json").exists()
        has_validation = (self._proposals_dir / pid / "validation.json").exists()

        if has_validation:
            exp_score = 100.0  # Already validated
        elif has_candidate:
            exp_score = 90.0  # Ready to experiment
        elif system_area in ("EXECUTION", "MARKET", "STRATEGY", "DECISION"):
            exp_score = 70.0  # Likely filterable
        elif system_area == "CROSS_UNIVERSE":
            exp_score = 50.0  # May be filterable
        else:
            exp_score = 30.0

        # ─── Factor 5: Confidence ─────────────────────────────────────
        conf_score = 0.0
        if confidence == "HIGH":
            conf_score = 100.0
        elif confidence == "MEDIUM":
            conf_score = 70.0
        elif confidence == "LOW":
            conf_score = 40.0
        else:
            conf_score = 10.0

        # ─── Weighted score ───────────────────────────────────────────
        raw_score = (
            evidence * _WEIGHTS["evidence"] / 100.0
            + impact * _WEIGHTS["impact"] / 100.0
            + pop_score * _WEIGHTS["population"] / 100.0
            + exp_score * _WEIGHTS["experimentability"] / 100.0
            + conf_score * _WEIGHTS["confidence"] / 100.0
        )

        # Apply evidence quality penalty
        raw_score = max(0, raw_score - evidence_quality_penalty)

        # Generate reason
        reason_parts = []
        if outcome in ("NEGATIVE", "NOT_PREDICTIVE", "POORLY_CALIBRATED"):
            reason_parts.append(f"Clear {outcome.lower()} evidence")
        if confidence in ("HIGH", "MEDIUM"):
            reason_parts.append(f"{confidence.lower()} confidence")
        if metric_sample > 50:
            reason_parts.append(f"substantial metric sample ({metric_sample})")
        elif metric_sample < 5 and population > 100:
            reason_parts.append(f"WARNING: metric based on only {metric_sample} observation(s) despite population {population}")
        if has_candidate:
            reason_parts.append("candidate already designed")
        if has_validation:
            reason_parts.append("already validated")
        if evidence_quality == "INSUFFICIENT":
            reason_parts.append("EVIDENCE INSUFFICIENT for candidate design")

        return ProposalPriority(
            proposal_id=pid,
            priority_score=raw_score,
            evidence_score=evidence,
            impact_score=impact,
            population_score=pop_score,
            experimentability_score=exp_score,
            confidence_score=conf_score,
            redundancy_penalty=evidence_quality_penalty,  # Reusing field for total penalty
            system_area=system_area,
            finding_outcome=outcome,
            finding_confidence=confidence,
            sample_size=metric_sample,  # Now metric-specific, not population
            has_candidate=has_candidate,
            has_validation=has_validation,
            ranking_reason="; ".join(reason_parts) if reason_parts else "Standard scoring",
            source_finding_ids=source_finding_ids,
            source_feedback_ids=source_feedback_ids,
        )

    def _apply_redundancy(self, priorities: list[ProposalPriority]) -> None:
        """Apply a lightweight redundancy penalty for overlapping system areas."""
        # Count proposals per system_area
        area_counts: dict[str, int] = {}
        for p in priorities:
            area_counts[p.system_area] = area_counts.get(p.system_area, 0) + 1

        # Apply small penalty for highly concentrated areas (>3 proposals same area)
        area_seen: dict[str, int] = {}
        # Sort by score descending first to penalize lower-ranked duplicates
        for p in sorted(priorities, key=lambda x: -x.priority_score):
            seen = area_seen.get(p.system_area, 0)
            if seen >= 3:
                penalty = min(10.0, (seen - 2) * 3.0)
                p.redundancy_penalty = penalty
                p.priority_score = max(0, p.priority_score - penalty)
            area_seen[p.system_area] = seen + 1

    def _assign_band(self, score: float) -> str:
        if score >= 75:
            return PriorityBand.CRITICAL.value
        elif score >= 60:
            return PriorityBand.HIGH.value
        elif score >= 45:
            return PriorityBand.MEDIUM.value
        elif score >= 30:
            return PriorityBand.LOW.value
        return PriorityBand.DEFERRED.value

    def _determine_next_action(self, p: ProposalPriority) -> str:
        if p.has_validation:
            return NextAction.RUN_EXPERIMENT.value  # Re-run or review
        if p.has_candidate:
            return NextAction.RUN_EXPERIMENT.value
        if p.sample_size < 5:
            return NextAction.GATHER_MORE_DATA.value  # Insufficient metric evidence
        if p.finding_confidence == "INSUFFICIENT":
            return NextAction.GATHER_MORE_DATA.value
        if p.experimentability_score >= 50 and p.sample_size >= 20:
            return NextAction.DESIGN_CANDIDATE.value
        if p.redundancy_penalty >= 25:  # Evidence quality penalty (INSUFFICIENT/WEAK)
            return NextAction.GATHER_MORE_DATA.value
        if p.experimentability_score < 50:
            return NextAction.BLOCKED_BY_SIMULATION.value
        return NextAction.DEFER.value

    # ─── Evidence quality assessment ─────────────────────────────────────────

    def _assess_evidence_quality(
        self,
        metric_sample: int,
        analytical_sample: int,
        population: int,
        reported_confidence: str,
        knowledge_confidence: str,
        has_small_sample_warning: bool,
    ) -> str:
        """
        Assess the actual quality of the evidence supporting a proposal.

        Detects mismatches between reported confidence and actual metric sample.

        Returns: STRONG, MODERATE, WEAK, INSUFFICIENT
        """
        # Direct metric sample assessment (provisional policy v1.0.0)
        if metric_sample < 5:
            return "INSUFFICIENT"
        if metric_sample < 30:
            return "WEAK"
        if metric_sample < 100:
            return "MODERATE"

        # Cross-check: knowledge layer downgraded confidence
        if knowledge_confidence == "INSUFFICIENT":
            if metric_sample < 30:
                return "INSUFFICIENT"
            return "WEAK"

        # Cross-check: finding explicitly warns about small sample
        if has_small_sample_warning and metric_sample < 30:
            return "WEAK"

        return "STRONG"

    def _load_knowledge(self, question_id: str) -> dict[str, Any] | None:
        """Load knowledge item for a question if available."""
        k_path = Path("reports/research/knowledge") / f"k_{question_id}" / "latest.json"
        if not k_path.exists():
            return None
        try:
            return json.loads(k_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    # ─── Data loading ─────────────────────────────────────────────────────────

    def _load_proposals(self) -> list[dict[str, Any]]:
        if not self._proposals_dir.exists():
            return []
        proposals = []
        for pdir in sorted(self._proposals_dir.iterdir()):
            if pdir.is_dir() and (pdir / "proposal.json").exists():
                try:
                    proposals.append(json.loads((pdir / "proposal.json").read_text(encoding="utf-8")))
                except Exception:
                    continue
        return proposals

    def _load_findings(self) -> dict[str, dict[str, Any]]:
        if not self._questions_dir.exists():
            return {}
        findings = {}
        for qdir in sorted(self._questions_dir.iterdir()):
            latest = qdir / "latest.json"
            if latest.exists():
                try:
                    f = json.loads(latest.read_text(encoding="utf-8"))
                    findings[f.get("question_id", "")] = f
                except Exception:
                    continue
        return findings

    def _load_feedbacks(self) -> dict[str, dict[str, Any]]:
        fb_dir = Path("reports/research/feedback")
        if not fb_dir.exists():
            return {}
        feedbacks = {}
        for qdir in sorted(fb_dir.iterdir()):
            latest = qdir / "latest.json"
            if latest.exists():
                try:
                    fb = json.loads(latest.read_text(encoding="utf-8"))
                    feedbacks[fb.get("question_id", "")] = fb
                except Exception:
                    continue
        return feedbacks


# ═══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE
# ═══════════════════════════════════════════════════════════════════════════════


class RankingStore:
    """Persists proposal rankings with immutable history."""

    def __init__(self, base_dir: Path | str | None = None):
        self._dir = Path(base_dir) if base_dir else Path("reports/research/proposals/ranking")

    def save(self, ranking: ProposalRanking) -> Path:
        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / "history").mkdir(exist_ok=True)

        ranking_dict = ranking.to_dict()

        latest = self._dir / "latest.json"
        latest.write_text(json.dumps(ranking_dict, indent=2, default=str), encoding="utf-8")

        hist = self._dir / "history" / f"{ranking.ranking_version}.json"
        if not hist.exists():
            hist.write_text(json.dumps(ranking_dict, indent=2, default=str), encoding="utf-8")

        return latest

    def load_latest(self) -> dict[str, Any] | None:
        latest = self._dir / "latest.json"
        if not latest.exists():
            return None
        try:
            return json.loads(latest.read_text(encoding="utf-8"))
        except Exception:
            return None
