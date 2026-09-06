"""
Edge Candidate Lifecycle Bridge — the ONE canonical bridge from the
edge-candidate discovery surface into the existing research lifecycle.

Flow (existing governance, nothing new invented):

    canonical V1 S3 evidence
        -> edge analysis / generation (existing discovery gates)
        -> EdgeCandidate  (an evidence-backed FINDING/proposal)
        -> lifecycle Hypothesis (REGISTERED) via ResearchOrchestrator
        -> existing validation / challenge / conclusion (orchestrator)
        -> VALIDATED -> orchestrator.create_optimisation_candidate
        -> CandidateRegistry (PROPOSED)
        -> existing activation -> candidate shadows -> CandidateEvaluator
        -> GovernanceGate (HUMAN-ONLY promotion)

GOVERNANCE INVARIANTS (non-negotiable):
    - A raw edge candidate is evidence/finding, NOT a deployable candidate.
      It enters the lifecycle ONLY as a Hypothesis that still requires full
      validation. It is NEVER registered directly into the CandidateRegistry;
      candidate creation remains exclusively inside the orchestrator's
      VALIDATED path.
    - This module NEVER touches the CandidateRegistry, candidate activation,
      candidate shadow testing, candidate evaluation, GovernanceGate state,
      or any production/trading configuration. Research may detect, analyse,
      propose, test, reject, recommend — never deploy.
    - Existing statistical gates are preserved unchanged: only ACCEPTED
      EdgeCandidates (the generator's own discovery gates: n>=30, EV>0,
      total_r>0) are eligible for hypothesis registration. Discovery is not
      validation.

IDEMPOTENCY (week-on-week reruns):
    Stable identity = EdgeCandidate.candidate_id (deterministic hash of the
    condition set). Reruns on unchanged evidence re-confirm the SAME
    hypothesis — no duplicate lifecycle objects. Materially changed evidence
    updates the hypothesis record and appends an audit event (history is
    preserved in the append-only registry audit log). Distinct edges map to
    distinct hypotheses.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from research_engine.edge_candidates.models import EdgeCandidate

logger = logging.getLogger(__name__)

_SOURCE_SURFACE = "edge_candidate_generation"


@dataclass
class EdgeLifecycleSubmission:
    """Accounting for one bridge run (idempotent, no silent duplicates)."""

    registered: list[dict[str, Any]] = field(default_factory=list)
    reconfirmed: list[dict[str, Any]] = field(default_factory=list)
    evidence_updated: list[dict[str, Any]] = field(default_factory=list)
    skipped_concluded: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "registered": list(self.registered),
            "reconfirmed": list(self.reconfirmed),
            "evidence_updated": list(self.evidence_updated),
            "skipped_already_concluded": list(self.skipped_concluded),
            "totals": {
                "registered": len(self.registered),
                "reconfirmed": len(self.reconfirmed),
                "evidence_updated": len(self.evidence_updated),
                "skipped_already_concluded": len(self.skipped_concluded),
            },
        }


def _category_for(conditions: dict[str, str]):
    """Map an edge condition set to the existing lifecycle hypothesis category."""
    from research_engine.lifecycle.hypothesis import HypothesisCategory

    keys = set(conditions)
    if "regime" in keys:
        return HypothesisCategory.REGIME_CONDITIONING
    if "score_bin" in keys:
        return HypothesisCategory.SCORE_MONOTONICITY
    if "pattern" in keys:
        return HypothesisCategory.PATTERN_SIGNAL
    return HypothesisCategory.OTHER


def _fingerprint(edge: EdgeCandidate) -> str:
    """Stable evidence fingerprint of an edge finding."""
    return (
        f"n={edge.sample_size};ev={edge.expectancy:.4f};total_r={edge.total_r:.2f};"
        f"wr={edge.win_rate:.4f};pf={edge.profit_factor:.2f}"
    )


def _find_existing_hypothesis(registry, edge_id: str):
    """Locate an existing lifecycle hypothesis for this edge finding (idempotency)."""
    for h in registry.all():
        if h.source_finding_id == edge_id:
            return h
    return None


def _population_description(edge: EdgeCandidate, conditions_str: str) -> str:
    return (
        f"Opportunities matching: {conditions_str} "
        f"(n={edge.sample_size} observed; outcome evidence: canonical "
        f"shadow_runtime_v1 counterfactuals + trade_truth realised outcomes, "
        f"joined by canonical_opportunity_id)"
    )


def _reconcile_existing(registry, existing, edge, edge_id, fingerprint):
    """Idempotent reconciliation for an edge that already has a hypothesis."""
    from research_engine.lifecycle.hypothesis import HypothesisStatus

    if existing.status in (HypothesisStatus.CONCLUDED, HypothesisStatus.PROMOTED):
        # Preserve history: a concluded hypothesis is never silently reopened
        # by repeated discovery evidence.
        registry._log_event(
            "EDGE_REOBSERVED_AFTER_CONCLUSION", existing.hypothesis_id,
            f"{edge_id} evidence={fingerprint}",
        )
        return "skipped_concluded", {
            "edge_id": edge_id, "hypothesis_id": existing.hypothesis_id,
            "status": existing.status.value,
        }

    prior_fp = ""
    for tag in existing.tags:
        if tag.startswith("edge_evidence:"):
            prior_fp = tag[len("edge_evidence:"):]
            break
    if prior_fp == fingerprint:
        registry._log_event(
            "EDGE_RECONFIRMED", existing.hypothesis_id,
            f"{edge_id} evidence={fingerprint}",
        )
        return "reconfirmed", {"edge_id": edge_id, "hypothesis_id": existing.hypothesis_id}

    existing.tags = [
        t for t in existing.tags if not t.startswith("edge_evidence:")
    ] + [f"edge_evidence:{fingerprint}"]
    existing.population_description = _population_description(
        edge, ", ".join(f"{k}={v}" for k, v in sorted(edge.conditions.items()))
    )
    registry.update(existing)
    registry._log_event(
        "EDGE_EVIDENCE_UPDATED", existing.hypothesis_id,
        f"{edge_id} {prior_fp} -> {fingerprint}",
    )
    return "evidence_updated", {
        "edge_id": edge_id, "hypothesis_id": existing.hypothesis_id,
        "prior_evidence": prior_fp, "new_evidence": fingerprint,
    }


def submit_edge_candidates_to_lifecycle(
    generation_result,
    *,
    orchestrator=None,
) -> EdgeLifecycleSubmission:
    """
    Register ACCEPTED edge candidates (findings) into the existing lifecycle
    as Hypotheses requiring validation.

    Idempotent: unchanged evidence re-confirms; changed evidence updates;
    concluded hypotheses are never silently reopened. This function NEVER
    creates CandidateRecords — candidate creation stays exclusively inside
    the orchestrator's VALIDATED conclusion path.
    """
    from research_engine.lifecycle.orchestrator import ResearchOrchestrator
    from research_engine.lifecycle.registry import InvestigationRegistry

    orch = orchestrator or ResearchOrchestrator()
    registry = orch.registry if isinstance(orch.registry, InvestigationRegistry) else InvestigationRegistry()

    submission = EdgeLifecycleSubmission()
    combos_tested = max(int(getattr(generation_result, "combinations_tested", 1) or 1), 1)

    for edge in getattr(generation_result, "accepted", []):
        edge_id = edge.candidate_id
        conditions_str = ", ".join(f"{k}={v}" for k, v in sorted(edge.conditions.items()))
        fingerprint = _fingerprint(edge)
        existing = _find_existing_hypothesis(registry, edge_id)

        if existing is not None:
            bucket, entry = _reconcile_existing(registry, existing, edge, edge_id, fingerprint)
            getattr(submission, bucket).append(entry)
            continue

        # NEW edge finding -> lifecycle Hypothesis (REGISTERED stage only)
        hypothesis = _register_new_hypothesis(orch, edge, edge_id, conditions_str, combos_tested)
        submission.registered.append({
            "edge_id": edge_id,
            "hypothesis_id": hypothesis.hypothesis_id,
            "category": hypothesis.category.value,
        })

    return submission


def _register_new_hypothesis(orch, edge, edge_id, conditions_str, combos_tested):
    """Register one accepted edge finding as a lifecycle Hypothesis (REGISTERED)."""
    hypothesis = orch.detect_and_register(
        title=f"Edge: positive expectancy when {conditions_str}",
        description=(
            f"Edge-candidate surface finding {edge_id}. Condition set with "
            f"positive counterfactual expectancy on canonical evidence."
        ),
        claim=(
            f"{edge.hypothesis} — expectancy {edge.expectancy:+.3f}R per "
            f"opportunity over n={edge.sample_size} observations "
            f"(win rate {edge.win_rate:.0%}, PF {edge.profit_factor:.2f})."
        ),
        null_hypothesis=(
            f"Expectancy of opportunities matching [{conditions_str}] is "
            f"<= 0 R (no edge)."
        ),
        category=_category_for(edge.conditions),
        source=_SOURCE_SURFACE,
        source_finding_id=edge_id,
        population_description=_population_description(edge, conditions_str),
        falsification_conditions=[
            f"Expectancy <= 0 across the {edge.sample_size} observed opportunities",
            "Out-of-sample (walk-forward) expectancy <= 0",
            "Positive expectancy disappears after outlier removal",
            "Positive expectancy does not replicate across symbols/periods",
        ],
        discovery_bias_notes=(
            f"Discovered by scanning {combos_tested} condition combinations "
            f"(multiple-testing aware); discovery gates only — full "
            f"validation (OOS, placebo, robustness) still required."
        ),
        multiple_testing_count=combos_tested,
        tags=_edge_lineage_tags(edge),
    )
    logger.info(
        "[EDGE_LIFECYCLE] registered hypothesis %s for edge %s (n=%d EV=%+.3f)",
        hypothesis.hypothesis_id, edge_id, edge.sample_size, edge.expectancy,
    )
    return hypothesis


def _edge_lineage_tags(edge: EdgeCandidate) -> list[str]:
    """Source lineage carried from the edge finding into the lifecycle."""
    return [
        "edge_candidate",
        f"edge_evidence:{_fingerprint(edge)}",
        f"overfit_risk:{edge.overfit_risk}",
        "source_datasets:decision_trace,shadow_runtime_v1,trade_truth",
        "join_key:canonical_opportunity_id",
    ]


