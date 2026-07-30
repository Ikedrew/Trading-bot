"""
Discovery Report — Generates consolidated report across all 4 research questions.

Orchestrates CQ1-CQ4 analysis and produces a structured discovery report
with findings, conclusions, and next-step recommendations.

Safety:
    - Never modifies trades or execution
    - Never promotes findings automatically
    - Reports are descriptive — decisions remain with research director
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_engine.v2_discovery.feature_analyser import (
    analyse_features,
    get_significant_features,
    summarise_top_features,
    FeatureAnalysis,
)
from research_engine.v2_discovery.context_combiner import (
    analyse_combinations,
    CombinationAnalysis,
)
from research_engine.v2_discovery.environment_classifier import (
    classify_environments,
    get_best_environments,
    get_worst_environments,
    EnvironmentAnalysis,
)
from research_engine.v2_discovery.probability_model import (
    build_probability_model,
    ProbabilityAnalysis,
)

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class DiscoveryConclusion:
    """Final conclusion from the discovery process."""
    outcome: str  # "PREDICTIVE_INFORMATION_FOUND" or "NO_PREDICTIVE_VALUE"
    confidence: str  # "HIGH" / "MEDIUM" / "LOW"
    summary: str
    evidence: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)


@dataclass
class DiscoveryReport:
    """Complete V2 Discovery Report."""
    report_id: str
    generated_utc: str
    total_records: int
    linked_records: int
    # CQ1 results
    cq1_features_analysed: int
    cq1_significant_features: int
    cq1_top_features: list[dict[str, Any]] = field(default_factory=list)
    cq1_conclusion: str = ""
    # CQ2 results
    cq2_hypotheses_tested: int = 0
    cq2_validated_combinations: int = 0
    cq2_best_combination: str = ""
    cq2_best_ev: float = 0.0
    cq2_conclusion: str = ""
    # CQ3 results
    cq3_favourable_environments: int = 0
    cq3_unfavourable_environments: int = 0
    cq3_best_environments: list[dict[str, Any]] = field(default_factory=list)
    cq3_worst_environments: list[dict[str, Any]] = field(default_factory=list)
    cq3_conclusion: str = ""
    # CQ4 results
    cq4_model_accuracy: float = 0.0
    cq4_baseline_accuracy: float = 0.0
    cq4_model_useful: bool = False
    cq4_brier_score: float = 1.0
    cq4_mean_calibration_error: float = 0.0
    cq4_top_features: list[dict[str, Any]] = field(default_factory=list)
    cq4_conclusion: str = ""
    # Overall
    conclusion: DiscoveryConclusion | None = None


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════


def run_full_discovery(
    records: list[dict[str, Any]],
    *,
    min_sample: int = 30,
    spread_cost_r: float = 0.48,
) -> DiscoveryReport:
    """
    Run all four discovery questions and produce a consolidated report.

    Args:
        records: V2Opportunity dicts (should include linked outcomes)
        min_sample: Minimum sample size for statistical tests
        spread_cost_r: Spread cost in R-multiples

    Returns:
        DiscoveryReport with all findings and conclusion.
    """
    linked = [r for r in records if _has_outcome(r)]
    now = datetime.now(timezone.utc).isoformat()

    report = DiscoveryReport(
        report_id=f"DISCOVERY_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
        generated_utc=now,
        total_records=len(records),
        linked_records=len(linked),
        cq1_features_analysed=0,
        cq1_significant_features=0,
    )

    if not linked:
        report.conclusion = DiscoveryConclusion(
            outcome="NO_PREDICTIVE_VALUE",
            confidence="LOW",
            summary="No linked records available for analysis.",
            evidence=["Zero linked V2Opportunity records found."],
            next_steps=["Collect more observations and link outcomes."],
        )
        return report

    # ─── CQ1: Feature Analysis ─────────────────────────────────────────
    logger.info("[DISCOVERY] Running CQ1 — Feature Analysis...")
    cq1_results = analyse_features(
        linked, min_sample=min_sample, spread_cost_r=spread_cost_r)
    significant = get_significant_features(cq1_results)

    report.cq1_features_analysed = len(cq1_results)
    report.cq1_significant_features = len(significant)
    report.cq1_top_features = summarise_top_features(cq1_results, top_n=10)
    report.cq1_conclusion = _cq1_conclusion(cq1_results, significant)

    # ─── CQ2: Combination Analysis ─────────────────────────────────────
    logger.info("[DISCOVERY] Running CQ2 — Combination Analysis...")
    cq2_results = analyse_combinations(
        linked, min_sample=min_sample, spread_cost_r=spread_cost_r)

    report.cq2_hypotheses_tested = cq2_results.hypotheses_tested
    report.cq2_validated_combinations = cq2_results.validated_combinations
    report.cq2_best_combination = cq2_results.best_combination
    report.cq2_best_ev = cq2_results.best_validated_ev
    report.cq2_conclusion = _cq2_conclusion(cq2_results)

    # ─── CQ3: Environment Classification ───────────────────────────────
    logger.info("[DISCOVERY] Running CQ3 — Environment Classification...")
    cq3_results = classify_environments(
        linked, min_sample=min_sample, spread_cost_r=spread_cost_r)

    report.cq3_favourable_environments = len(cq3_results.favourable_environments)
    report.cq3_unfavourable_environments = len(cq3_results.unfavourable_environments)
    report.cq3_best_environments = get_best_environments(cq3_results, top_n=5)
    report.cq3_worst_environments = get_worst_environments(cq3_results, top_n=5)
    report.cq3_conclusion = _cq3_conclusion(cq3_results)

    # ─── CQ4: Probability Model ────────────────────────────────────────
    logger.info("[DISCOVERY] Running CQ4 — Probability Model...")
    cq4_results = build_probability_model(
        linked, min_cohort=max(10, min_sample // 2), spread_cost_r=spread_cost_r)

    report.cq4_model_accuracy = cq4_results.model_accuracy
    report.cq4_baseline_accuracy = max(
        cq4_results.baseline_win_rate, 1 - cq4_results.baseline_win_rate)
    report.cq4_model_useful = cq4_results.model_useful
    report.cq4_brier_score = cq4_results.model_brier_score
    report.cq4_mean_calibration_error = cq4_results.mean_calibration_error
    report.cq4_top_features = [
        {"feature": fi.feature_name, "importance": fi.importance_score}
        for fi in cq4_results.feature_importance[:5]
    ]
    report.cq4_conclusion = _cq4_conclusion(cq4_results)

    # ─── Overall Conclusion ─────────────────────────────────────────────
    report.conclusion = _overall_conclusion(
        report, cq1_results, cq2_results, cq3_results, cq4_results
    )

    return report


def save_report(report: DiscoveryReport, output_dir: str = "analysis/reports") -> str:
    """Save discovery report to JSON file."""
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    filename = f"v2_discovery_{report.report_id}.json"
    filepath = path / filename

    data = _report_to_dict(report)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)

    return str(filepath)


# ═══════════════════════════════════════════════════════════════════════════════
# CONCLUSION GENERATORS
# ═══════════════════════════════════════════════════════════════════════════════


def _cq1_conclusion(
    results: list[FeatureAnalysis], significant: list[FeatureAnalysis]
) -> str:
    """Generate CQ1 conclusion text."""
    if not results:
        return "Insufficient data for feature analysis."
    if not significant:
        return (
            f"Analysed {len(results)} features. "
            "No individual feature shows statistically significant positive "
            "cost-adjusted EV. Individual features alone do not predict outcomes."
        )
    best = significant[0]
    return (
        f"Analysed {len(results)} features. "
        f"{len(significant)} show significant predictive value. "
        f"Best: {best.feature_name} ({best.best_category}) with "
        f"cost-adjusted EV = {best.best_ev:.4f}R."
    )


def _cq2_conclusion(results: CombinationAnalysis) -> str:
    """Generate CQ2 conclusion text."""
    if results.hypotheses_tested == 0:
        return "No hypotheses tested (insufficient data)."
    if results.validated_combinations == 0:
        return (
            f"Tested {results.hypotheses_tested} hypotheses. "
            "No combination achieves positive cost-adjusted EV in "
            "out-of-sample validation."
        )
    return (
        f"Tested {results.hypotheses_tested} hypotheses. "
        f"{results.validated_combinations} validated out-of-sample. "
        f"Best: {results.best_combination} with OOS EV = {results.best_validated_ev:.4f}R."
    )


def _cq3_conclusion(results: EnvironmentAnalysis) -> str:
    """Generate CQ3 conclusion text."""
    n_fav = len(results.favourable_environments)
    n_unfav = len(results.unfavourable_environments)
    if n_fav == 0 and n_unfav == 0:
        return "No statistically significant environment effects detected."
    parts = []
    if n_fav > 0:
        best = results.favourable_environments[0]
        parts.append(
            f"{n_fav} favourable environments found. "
            f"Best: {best.dimension}={best.state} (EV={best.cost_adjusted_ev:.4f}R)."
        )
    if n_unfav > 0:
        worst = results.unfavourable_environments[0]
        parts.append(
            f"{n_unfav} unfavourable environments identified. "
            f"Worst: {worst.dimension}={worst.state} (EV={worst.cost_adjusted_ev:.4f}R)."
        )
    return " ".join(parts)


def _cq4_conclusion(results: ProbabilityAnalysis) -> str:
    """Generate CQ4 conclusion text."""
    if results.train_size == 0:
        return "Insufficient data for probability modelling."
    baseline = max(results.baseline_win_rate, 1 - results.baseline_win_rate)
    if results.model_useful:
        return (
            f"Model accuracy: {results.model_accuracy:.1%} vs "
            f"baseline {baseline:.1%}. "
            f"Brier score: {results.model_brier_score:.4f}. "
            f"Mean calibration error: {results.mean_calibration_error:.4f}. "
            "Model provides useful probability discrimination."
        )
    return (
        f"Model accuracy: {results.model_accuracy:.1%} vs "
        f"baseline {baseline:.1%}. "
        f"Brier score: {results.model_brier_score:.4f}. "
        "Model does NOT beat baseline — available features do not "
        "reliably estimate outcome probability."
    )


def _overall_conclusion(
    report: DiscoveryReport,
    cq1: list[FeatureAnalysis],
    cq2: CombinationAnalysis,
    cq3: EnvironmentAnalysis,
    cq4: ProbabilityAnalysis,
) -> DiscoveryConclusion:
    """Synthesise overall discovery conclusion."""
    evidence: list[str] = []
    has_signal = False

    # CQ1 check
    sig_features = get_significant_features(cq1)
    positive_features = [f for f in sig_features if f.best_ev > 0]
    if positive_features:
        has_signal = True
        evidence.append(
            f"CQ1: {len(positive_features)} features with positive cost-adjusted EV")
    else:
        evidence.append("CQ1: No individual feature predicts positive outcomes")

    # CQ2 check
    if cq2.validated_combinations > 0 and cq2.best_validated_ev > 0:
        has_signal = True
        evidence.append(
            f"CQ2: {cq2.validated_combinations} validated combinations, "
            f"best OOS EV = {cq2.best_validated_ev:.4f}R")
    else:
        evidence.append("CQ2: No combination validates out-of-sample")

    # CQ3 check
    if cq3.favourable_environments:
        has_signal = True
        evidence.append(
            f"CQ3: {len(cq3.favourable_environments)} favourable environments found")
    else:
        evidence.append("CQ3: No significant favourable environments")

    # CQ4 check
    if cq4.model_useful:
        has_signal = True
        evidence.append(
            f"CQ4: Probability model beats baseline "
            f"({cq4.model_accuracy:.1%} vs {max(cq4.baseline_win_rate, 1-cq4.baseline_win_rate):.1%})")
    else:
        evidence.append("CQ4: Probability model does not beat baseline")

    # Determine outcome
    if has_signal:
        outcome = "PREDICTIVE_INFORMATION_FOUND"
        confidence = "HIGH" if sum([
            bool(positive_features),
            cq2.validated_combinations > 0,
            bool(cq3.favourable_environments),
            cq4.model_useful,
        ]) >= 3 else "MEDIUM" if sum([
            bool(positive_features),
            cq2.validated_combinations > 0,
            bool(cq3.favourable_environments),
            cq4.model_useful,
        ]) >= 2 else "LOW"
        summary = (
            "Predictive information exists in the V2Opportunity feature set. "
            "Further validation with larger samples recommended before any "
            "production changes."
        )
        next_steps = [
            "Validate best features/combinations on new epoch data",
            "Collect minimum 200 additional linked observations",
            "Run walk-forward test on best combination",
            "Do NOT implement as trading filter until n>=500 validated",
        ]
    else:
        outcome = "NO_PREDICTIVE_VALUE"
        confidence = "HIGH" if report.linked_records >= 200 else "MEDIUM"
        summary = (
            "No available feature or combination in the V2Opportunity schema "
            "predicts positive cost-adjusted outcomes. The information captured "
            "does not contain exploitable predictive value."
        )
        next_steps = [
            "Consider alternative information sources (order flow, news, cross-pair)",
            "Increase observation granularity (tick data, L2)",
            "Test different market (different pair, different asset class)",
            "Accept null result — current architecture lacks edge",
        ]

    return DiscoveryConclusion(
        outcome=outcome,
        confidence=confidence,
        summary=summary,
        evidence=evidence,
        next_steps=next_steps,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════


def _has_outcome(rec: dict) -> bool:
    """Check if record has a linked outcome."""
    if rec.get("outcome_recorded"):
        return True
    if rec.get("_linkage", {}).get("linked"):
        return True
    return False


def _report_to_dict(report: DiscoveryReport) -> dict[str, Any]:
    """Convert report to serializable dict."""
    d: dict[str, Any] = {
        "report_id": report.report_id,
        "generated_utc": report.generated_utc,
        "total_records": report.total_records,
        "linked_records": report.linked_records,
        "cq1": {
            "features_analysed": report.cq1_features_analysed,
            "significant_features": report.cq1_significant_features,
            "top_features": report.cq1_top_features,
            "conclusion": report.cq1_conclusion,
        },
        "cq2": {
            "hypotheses_tested": report.cq2_hypotheses_tested,
            "validated_combinations": report.cq2_validated_combinations,
            "best_combination": report.cq2_best_combination,
            "best_ev": report.cq2_best_ev,
            "conclusion": report.cq2_conclusion,
        },
        "cq3": {
            "favourable_environments": report.cq3_favourable_environments,
            "unfavourable_environments": report.cq3_unfavourable_environments,
            "best_environments": report.cq3_best_environments,
            "worst_environments": report.cq3_worst_environments,
            "conclusion": report.cq3_conclusion,
        },
        "cq4": {
            "model_accuracy": report.cq4_model_accuracy,
            "baseline_accuracy": report.cq4_baseline_accuracy,
            "model_useful": report.cq4_model_useful,
            "brier_score": report.cq4_brier_score,
            "calibration_error": report.cq4_mean_calibration_error,
            "top_features": report.cq4_top_features,
            "conclusion": report.cq4_conclusion,
        },
        "conclusion": None,
    }

    if report.conclusion:
        d["conclusion"] = {
            "outcome": report.conclusion.outcome,
            "confidence": report.conclusion.confidence,
            "summary": report.conclusion.summary,
            "evidence": report.conclusion.evidence,
            "next_steps": report.conclusion.next_steps,
        }

    return d
