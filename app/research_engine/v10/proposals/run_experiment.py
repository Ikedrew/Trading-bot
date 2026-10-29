"""
Run a candidate experiment end-to-end against real historical data.

Executes the full governed path:
    PROPOSAL → CANDIDATE → EXPERIMENT → VALIDATION → PROMOTION GATE

Usage:
    python -m research_engine.v10.proposals.run_experiment prop_EM-001_dcbd05

Or via CLI:
    python research.py candidate experiment prop_EM-001_dcbd05

NEVER modifies the trading bot. All results are COUNTERFACTUAL.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# Ensure project root importable
_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def run_candidate_experiment(proposal_id: str) -> dict[str, Any]:
    """
    Execute the full governed candidate experiment pipeline.

    Returns a complete result dict with baseline, candidate, validation,
    and promotion gate status.
    """
    from research_engine.v10.proposals.model import ChangeProposal, Candidate
    from research_engine.v10.proposals.designer import CandidateDesigner, ChangeType
    from research_engine.v10.proposals.experiment import ExperimentRunner
    from research_engine.v10.proposals.promotion import PromotionGate
    from research_engine.v10.proposals.store import ProposalStore
    from research_engine.v10.universes import ExecutionUniverseBuilder

    store = ProposalStore()

    # ─── 1. Load proposal ─────────────────────────────────────────────────────
    proposal_dict = store.load_proposal(proposal_id)
    if not proposal_dict:
        return {"error": f"Proposal '{proposal_id}' not found in store."}

    proposal = ChangeProposal(
        proposal_id=proposal_dict["proposal_id"],
        source_feedback_ids=proposal_dict.get("source_feedback_ids", []),
        source_finding_ids=proposal_dict.get("source_finding_ids", []),
        system_area=proposal_dict.get("system_area", ""),
        problem_statement=proposal_dict.get("problem_statement", ""),
        hypothesis=proposal_dict.get("hypothesis", ""),
        universe_versions=proposal_dict.get("universe_versions", {}),
        population_versions=proposal_dict.get("population_versions", {}),
        governance_note=proposal_dict.get("governance_note", ""),
    )

    # ─── 2. Load or create candidate ──────────────────────────────────────────
    candidate_path = Path("reports/research/proposals") / proposal_id / "candidate.json"
    if candidate_path.exists():
        cand_dict = json.loads(candidate_path.read_text(encoding="utf-8"))
        candidate = Candidate(
            candidate_id=cand_dict.get("candidate_id", ""),
            proposal_id=cand_dict.get("proposal_id", ""),
            candidate_version=cand_dict.get("candidate_version", "1"),
            description=cand_dict.get("description", ""),
            hypothesis=cand_dict.get("hypothesis", ""),
            change_type=cand_dict.get("change_type", ""),
            configuration=cand_dict.get("configuration", {}),
            target_metric=cand_dict.get("target_metric", "mean_r"),
            expected_effect=cand_dict.get("expected_effect", "increase"),
            minimum_improvement=cand_dict.get("minimum_improvement", 0.0),
            critical_metrics=cand_dict.get("critical_metrics", []),
            design_status=cand_dict.get("design_status", ""),
            source_proposal_id=cand_dict.get("source_proposal_id", ""),
            source_finding_ids=cand_dict.get("source_finding_ids", []),
            source_feedback_ids=cand_dict.get("source_feedback_ids", []),
            universe_versions=cand_dict.get("universe_versions", {}),
            population_versions=cand_dict.get("population_versions", {}),
        )
    else:
        # Create candidate using designer
        designer = CandidateDesigner()
        design_result = designer.design(
            proposal=proposal,
            change_type=ChangeType.POPULATION_FILTER,
            configuration={"field": "regime", "operator": "!=", "value": "TRANSITIONAL"},
            target_metric="mean_r",
            expected_effect="increase",
            minimum_improvement=0.0,
            critical_metrics=["win_rate"],
            description="Exclude TRANSITIONAL-regime trades from the evaluated population.",
            hypothesis="Removing trades taken during regime transitions will improve overall system expectancy.",
        )
        if not design_result.valid:
            return {"error": f"Candidate design failed: {design_result.errors}"}
        candidate = design_result.candidate

    # ─── 3. Load real historical population ───────────────────────────────────
    exe_builder = ExecutionUniverseBuilder()
    exe_builder.build()
    population = exe_builder.records
    universe_versions = {"EXECUTION": exe_builder.metadata.content_hash}

    # Population analysis
    total = len(population)
    with_regime = [r for r in population if r.get("regime")]
    transitional = [r for r in population if r.get("regime") == "TRANSITIONAL"]
    non_transitional = [r for r in population if r.get("regime") and r.get("regime") != "TRANSITIONAL"]

    pop_info = {
        "total_population": total,
        "with_regime_field": len(with_regime),
        "transitional_count": len(transitional),
        "non_transitional_count": len(non_transitional),
        "missing_regime": total - len(with_regime),
        "universe_version": exe_builder.metadata.content_hash,
    }

    # ─── 4. Build candidate filter ────────────────────────────────────────────
    designer = CandidateDesigner()
    candidate_filter = designer.build_filter(candidate.configuration)

    # ─── 5. Run experiment ────────────────────────────────────────────────────
    runner = ExperimentRunner()
    experiment = runner.run_filter_experiment(
        proposal=proposal,
        candidate=candidate,
        population=population,
        candidate_filter=candidate_filter,
        universe_versions=universe_versions,
        population_versions={"all_trades": exe_builder.metadata.content_hash},
    )

    # ─── 6. Convert to ValidationResult ───────────────────────────────────────
    validation = runner.to_validation_result(
        experiment,
        target_metric=candidate.target_metric,
        min_improvement=candidate.minimum_improvement,
        critical_metrics=candidate.critical_metrics,
        min_sample=20,
    )

    # ─── 7. Run Promotion Gate ────────────────────────────────────────────────
    gate = PromotionGate()
    promotion = gate.evaluate(proposal, candidate, validation)

    # ─── 8. Persist all artifacts ─────────────────────────────────────────────
    artifact_dir = Path("reports/research/proposals") / proposal_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "history").mkdir(exist_ok=True)

    # Candidate
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_text(json.dumps(candidate.to_dict(), indent=2, default=str), encoding="utf-8")

    # Experiment
    exp_path = artifact_dir / "experiment.json"
    exp_path.write_text(json.dumps(experiment.to_dict(), indent=2, default=str), encoding="utf-8")

    # Validation
    store.save_validation(validation.to_dict())

    # Promotion
    store.save_promotion(promotion.to_dict())

    # Immutable history
    hist_exp = artifact_dir / "history" / f"experiment_{experiment.experiment_id}.json"
    if not hist_exp.exists():
        hist_exp.write_text(json.dumps(experiment.to_dict(), indent=2, default=str), encoding="utf-8")

    # ─── 9. Build result ──────────────────────────────────────────────────────
    result = {
        "proposal_id": proposal_id,
        "candidate_id": candidate.candidate_id,
        "population": pop_info,
        "baseline_metrics": experiment.baseline_metrics,
        "candidate_metrics": experiment.candidate_metrics,
        "delta_metrics": experiment.delta_metrics,
        "experiment_status": experiment.status,
        "validation_status": validation.status,
        "improvement_detected": validation.improvement_detected,
        "regression_detected": validation.regression_detected,
        "target_metric": validation.target_metric,
        "target_improvement": validation.target_improvement,
        "promotion_eligible": promotion.eligible,
        "promotion_status": promotion.status,
        "promotion_gates": {
            "satisfied": promotion.satisfied_gates,
            "blockers": promotion.blockers,
        },
        "governance": "No trading-system modification or deployment performed.",
    }

    return result


def print_experiment_report(result: dict[str, Any]) -> None:
    """Print a human-readable experiment report."""
    if "error" in result:
        print(f"ERROR: {result['error']}")
        return

    print("\nITEM 11 CANDIDATE EXPERIMENT")
    print("=" * 50)
    print(f"\nProposal: {result['proposal_id']}")
    print(f"Candidate: {result['candidate_id']}")
    print(f"\nChange: Exclude TRANSITIONAL regime trades")

    pop = result["population"]
    print(f"\nPOPULATION")
    print(f"  Total: {pop['total_population']}")
    print(f"  With regime: {pop['with_regime_field']}")
    print(f"  TRANSITIONAL: {pop['transitional_count']}")
    print(f"  Non-TRANSITIONAL: {pop['non_transitional_count']}")
    print(f"  Removed: {pop['transitional_count']} ({pop['transitional_count']/pop['total_population']*100:.1f}%)" if pop['total_population'] else "")

    bm = result["baseline_metrics"]
    cm = result["candidate_metrics"]
    dm = result["delta_metrics"]

    print(f"\nBASELINE")
    print(f"  Sample: {bm.get('sample_size', '?')}")
    print(f"  Mean R: {bm.get('mean_r', '?')}")
    print(f"  Win Rate: {bm.get('win_rate', '?')}")
    print(f"  Profit Factor: {bm.get('profit_factor', '?')}")
    print(f"  Max Drawdown R: {bm.get('max_drawdown_r', '?')}")

    print(f"\nCANDIDATE")
    print(f"  Sample: {cm.get('sample_size', '?')}")
    print(f"  Mean R: {cm.get('mean_r', '?')}")
    print(f"  Win Rate: {cm.get('win_rate', '?')}")
    print(f"  Profit Factor: {cm.get('profit_factor', '?')}")
    print(f"  Max Drawdown R: {cm.get('max_drawdown_r', '?')}")

    print(f"\nDELTA")
    print(f"  Mean R: {dm.get('mean_r', '?'):+.4f}" if isinstance(dm.get('mean_r'), (int, float)) else f"  Mean R: ?")
    print(f"  Win Rate: {dm.get('win_rate', '?'):+.4f}" if isinstance(dm.get('win_rate'), (int, float)) else f"  Win Rate: ?")
    print(f"  Profit Factor: {dm.get('profit_factor', '?'):+.4f}" if isinstance(dm.get('profit_factor'), (int, float)) else f"  Profit Factor: ?")

    print(f"\nVALIDATION")
    print(f"  Status: {result['validation_status']}")
    print(f"  Improvement: {result['improvement_detected']}")
    print(f"  Regression: {result['regression_detected']}")
    print(f"  Target: {result['target_metric']} delta={result['target_improvement']:+.4f}" if isinstance(result.get('target_improvement'), (int, float)) else "")

    print(f"\nPROMOTION GATES")
    for gate in result["promotion_gates"]["satisfied"]:
        print(f"  ✓ {gate}")
    for blocker in result["promotion_gates"]["blockers"]:
        print(f"  ✗ {blocker}")

    print(f"\nFINAL: {result['promotion_status']}")
    print(f"  Eligible: {result['promotion_eligible']}")

    print(f"\nGovernance:")
    print(f"  {result['governance']}")


if __name__ == "__main__":
    pid = sys.argv[1] if len(sys.argv) > 1 else "prop_EM-001_dcbd05"
    result = run_candidate_experiment(pid)
    print_experiment_report(result)
