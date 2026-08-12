"""
First Real Candidate Design.

Creates a concrete candidate for prop_EM-001_dcbd05 (Regime-Conditioned Expectancy weakness).

This is a research hypothesis test, NOT a proven improvement.
The candidate proposes: "Exclude TRANSITIONAL-regime trades."

To execute:
    python -m research_engine.v10.proposals.first_candidate
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from research_engine.v10.proposals.model import ChangeProposal, Candidate
from research_engine.v10.proposals.designer import CandidateDesigner, ChangeType
from research_engine.v10.proposals.store import ProposalStore


def create_first_candidate() -> dict:
    """Create the first concrete candidate from prop_EM-001_dcbd05."""
    store = ProposalStore()

    # Load the real proposal
    proposal_dict = store.load_proposal("prop_EM-001_dcbd05")
    if not proposal_dict:
        print("ERROR: prop_EM-001_dcbd05 not found in proposal store.")
        return {}

    # Reconstruct proposal
    proposal = ChangeProposal(
        proposal_id=proposal_dict["proposal_id"],
        source_feedback_ids=proposal_dict.get("source_feedback_ids", []),
        source_finding_ids=proposal_dict.get("source_finding_ids", []),
        system_area=proposal_dict.get("system_area", ""),
        problem_statement=proposal_dict.get("problem_statement", ""),
        hypothesis=proposal_dict.get("hypothesis", ""),
        universe_versions=proposal_dict.get("universe_versions", {}),
        population_versions=proposal_dict.get("population_versions", {}),
    )

    # Design the candidate
    designer = CandidateDesigner()
    result = designer.design(
        proposal=proposal,
        change_type=ChangeType.POPULATION_FILTER,
        configuration={
            "field": "regime",
            "operator": "!=",
            "value": "TRANSITIONAL",
        },
        target_metric="mean_r",
        expected_effect="increase",
        minimum_improvement=0.0,
        critical_metrics=["win_rate"],
        description="Exclude TRANSITIONAL-regime trades from the evaluated population.",
        hypothesis="Removing trades taken during regime transitions will improve overall system expectancy because transitional periods produce higher loss rates.",
    )

    if not result.valid:
        print(f"DESIGN FAILED: {result.errors}")
        return {}

    candidate = result.candidate
    print(f"CANDIDATE DESIGNED: {candidate.candidate_id}")
    print(f"  Proposal: {candidate.proposal_id}")
    print(f"  Type: {candidate.change_type}")
    print(f"  Status: {candidate.design_status}")
    print(f"  Config: {candidate.configuration}")
    print(f"  Target: {candidate.target_metric} (expected: {candidate.expected_effect})")
    print(f"  Critical: {candidate.critical_metrics}")

    # Persist
    store.save_proposal(proposal_dict)  # Ensure proposal exists
    candidate_path = Path("reports/research/proposals") / proposal.proposal_id / "candidate.json"
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_text(json.dumps(candidate.to_dict(), indent=2, default=str), encoding="utf-8")
    print(f"  Persisted: {candidate_path}")

    return candidate.to_dict()


if __name__ == "__main__":
    create_first_candidate()
