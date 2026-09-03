"""
Research Orchestrator — Ties the complete autonomous research lifecycle together.

Flow:
    1. DETECT: Receive a finding/anomaly from the research engine
    2. REGISTER: Create a Hypothesis entity with formal claim + falsification conditions
    3. EXPERIMENT: Select and execute appropriate experiments
    4. VALIDATE: Run OOS, bootstrap, permutation tests
    5. CHALLENGE: Run placebo controls and robustness checks
    6. CONCLUDE: Reach a governed verdict (VALIDATED / REJECTED / INCONCLUSIVE)
    7. RECORD: Update knowledge map and produce human-readable report
    8. GATE: If VALIDATED, create promotion request for human review

The orchestrator composes existing modules:
    - InvestigationRegistry (persistence)
    - ExperimentProtocol (experiment definitions)
    - ValidationHarness (statistical tests)
    - PlaceboController (negative controls)
    - GovernanceGate (human approval)
    - evidence_maturity (governance classification)
    - Research Command Center (reporting)

This module NEVER modifies production V10 autonomously.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from research_engine.lifecycle.hypothesis import (
    ConclusionType,
    Hypothesis,
    HypothesisCategory,
    HypothesisStatus,
)
from research_engine.lifecycle.registry import InvestigationRegistry
from research_engine.lifecycle.experiment_protocol import (
    ExperimentDefinition,
    ExperimentResult,
    ExperimentStatus,
    ExperimentType,
)
from research_engine.lifecycle.validation_harness import (
    bootstrap_ci,
    compute_full_validation,
    outlier_influence,
    permutation_test,
    symbol_robustness,
    temporal_stability,
)
from research_engine.lifecycle.placebo_controller import PlaceboTestOutcome, run_placebo_test
from research_engine.lifecycle.governance_gate import GovernanceGate

# Destination for hypothesis research reports. Module-level so alternate
# runtimes (tests) can redirect persistence via the same module-attr injection
# pattern used across the persistence layer; default path unchanged.
_REPORT_DIR = Path("reports/research/lifecycle")


class ResearchOrchestrator:
    """
    Autonomous orchestration of the complete research lifecycle.
    
    Usage:
        orchestrator = ResearchOrchestrator()
        
        # 1. Detect and register
        hypothesis = orchestrator.detect_and_register(
            title="TBC contains reversal information",
            claim="Inverting TBC direction produces positive R",
            ...
        )
        
        # 2. Run primary experiment
        result = orchestrator.run_experiment(hypothesis, experiment_def, run_fn)
        
        # 3. Challenge with validation
        orchestrator.challenge(hypothesis, results, placebo_fn)
        
        # 4. Conclude
        orchestrator.conclude(hypothesis)
        
        # 5. Report
        report = orchestrator.generate_report(hypothesis)
    """

    def __init__(self):
        self._registry = InvestigationRegistry()
        self._gate = GovernanceGate()
        self._knowledge_path = Path("analysis/summaries/research_knowledge.json")

        from research_engine.lifecycle.experiment_catalogue import ExperimentCatalogue
        self._catalogue = ExperimentCatalogue()

    @property
    def registry(self) -> InvestigationRegistry:
        return self._registry

    @property
    def gate(self) -> GovernanceGate:
        return self._gate

    @property
    def catalogue(self):
        """Experiment catalogue — permanent registry of all experiments."""
        return self._catalogue

    # ─── 1. DETECT & REGISTER ─────────────────────────────────────────

    def detect_and_register(
        self,
        *,
        title: str,
        description: str,
        claim: str,
        null_hypothesis: str,
        category: HypothesisCategory = HypothesisCategory.OTHER,
        source: str = "",
        source_finding_id: str = "",
        population_description: str = "",
        falsification_conditions: list[str] | None = None,
        discovery_bias_notes: str = "",
        multiple_testing_count: int = 1,
        tags: list[str] | None = None,
    ) -> Hypothesis:
        """
        Create and register a new hypothesis from a detected finding.
        
        Transitions: DETECTED → REGISTERED
        """
        h = Hypothesis(
            title=title,
            description=description,
            category=category,
            claim=claim,
            null_hypothesis=null_hypothesis,
            source=source,
            source_finding_id=source_finding_id,
            population_description=population_description,
            falsification_conditions=falsification_conditions or [],
            discovery_bias_notes=discovery_bias_notes,
            multiple_testing_count=multiple_testing_count,
            tags=tags or [],
            bonferroni_threshold=0.05 / max(multiple_testing_count, 1),
        )

        h.transition(HypothesisStatus.REGISTERED, reason="Formally registered for investigation")
        self._registry.register(h)
        return h

    # ─── 2. RUN EXPERIMENT ────────────────────────────────────────────

    def run_experiment(
        self,
        hypothesis: Hypothesis,
        experiment: ExperimentDefinition,
        execute_fn: Callable[[ExperimentDefinition], ExperimentResult],
    ) -> ExperimentResult:
        """
        Execute an experiment and record results against the hypothesis.
        
        Automatically registers, starts, and completes the experiment in the
        ExperimentCatalogue for permanent traceability.
        
        Transitions hypothesis to TESTING if not already there.
        """
        if hypothesis.status == HypothesisStatus.REGISTERED:
            hypothesis.transition(HypothesisStatus.TESTING,
                                  reason=f"Starting experiment {experiment.experiment_id}")

        # Register experiment on hypothesis
        exp_ref = hypothesis.add_experiment(
            experiment.experiment_id, experiment.experiment_type.value)

        # Register in permanent catalogue
        from research_engine.lifecycle.experiment_catalogue import ExperimentRecord
        catalogue_record = ExperimentRecord(
            experiment_id=experiment.experiment_id,
            title=experiment.title,
            description=experiment.description,
            experiment_type=experiment.experiment_type.value,
            hypothesis_id=hypothesis.hypothesis_id,
            population=experiment.population.pattern_filter[0] if experiment.population.pattern_filter else "",
            filters_applied=[f"pattern={experiment.population.pattern_filter}",
                             f"symbol={experiment.population.symbol_filter}"] if experiment.population.pattern_filter else [],
            definition=experiment.to_dict(),
            parameters={
                "stop_multiplier": experiment.simulation.stop_multiplier,
                "tp_multiplier": experiment.simulation.tp_multiplier,
                "max_bars": experiment.simulation.max_bars,
                "direction": experiment.simulation.direction,
            },
            control_description=f"Original direction at {experiment.simulation.stop_multiplier}R stop",
            treatment_description=f"{experiment.simulation.direction} direction, {experiment.simulation.tp_multiplier}R TP",
            null_hypothesis=hypothesis.null_hypothesis,
        )
        try:
            self._catalogue.register(catalogue_record)
        except ValueError:
            pass  # Already registered (idempotent on re-run)

        # Start in catalogue
        self._catalogue.start(experiment.experiment_id)

        # Execute
        experiment.status = ExperimentStatus.RUNNING
        try:
            result = execute_fn(experiment)
            experiment.status = ExperimentStatus.COMPLETE
            hypothesis.update_experiment(
                experiment.experiment_id,
                status="complete",
                result_summary=f"Mean R={result.mean_r:+.4f}, N={result.n}, WR={result.win_rate:.1%}",
            )

            # Complete in catalogue with result summary
            self._catalogue.complete(
                experiment.experiment_id,
                result_summary={
                    "n": result.n, "mean_r": result.mean_r, "total_r": result.total_r,
                    "win_rate": result.win_rate, "ci_lower": result.ci_lower,
                    "ci_upper": result.ci_upper, "permutation_p": result.permutation_p,
                },
                conclusion=result.classification or "",
                classification=result.classification or "",
            )

            # Attach dataset fingerprint if available
            if result.dataset_fingerprint:
                self._catalogue.update_result(
                    experiment.experiment_id,
                    **{"dataset_fingerprint": result.dataset_fingerprint} if hasattr(catalogue_record, 'dataset_fingerprint') else {},
                )
                cat_rec = self._catalogue.get(experiment.experiment_id)
                if cat_rec:
                    cat_rec.dataset_fingerprint = result.dataset_fingerprint
                    cat_rec.observation_count = result.n
                    self._catalogue._save()

        except Exception as e:
            experiment.status = ExperimentStatus.FAILED
            hypothesis.update_experiment(experiment.experiment_id, status="failed",
                                         result_summary=f"FAILED: {str(e)[:100]}")
            self._catalogue.fail(experiment.experiment_id, reason=str(e)[:200])
            result = ExperimentResult(experiment_id=experiment.experiment_id,
                                      hypothesis_id=hypothesis.hypothesis_id, status="failed")

        self._registry.update(hypothesis)
        return result

    # ─── 3. CHALLENGE ─────────────────────────────────────────────────

    def challenge(
        self,
        hypothesis: Hypothesis,
        primary_result: ExperimentResult,
        placebo_outcome: PlaceboTestOutcome | None = None,
    ) -> None:
        """
        Transition to CHALLENGED state after validation/placebo tests.
        
        Records the challenge evidence on the hypothesis.
        """
        if hypothesis.status == HypothesisStatus.TESTING:
            evidence = []
            if primary_result.oos_mean_r > 0:
                evidence.append("OOS positive")
            if placebo_outcome and placebo_outcome.placebo_passes:
                evidence.append("Placebo passes")
            if primary_result.survives_top20_removal:
                evidence.append("Outlier-robust")

            hypothesis.transition(
                HypothesisStatus.CHALLENGED,
                reason=f"Challenged with validation: {', '.join(evidence) or 'tests complete'}",
                evidence_ref=primary_result.experiment_id,
            )
            self._registry.update(hypothesis)

    # ─── 4. CONCLUDE ──────────────────────────────────────────────────

    def conclude(
        self,
        hypothesis: Hypothesis,
        result: ExperimentResult,
        placebo: PlaceboTestOutcome | None = None,
    ) -> ConclusionType:
        """
        Reach a governed conclusion based on all available evidence.
        
        Uses the existing evidence_maturity module for classification.
        Applies multiple-testing correction.
        """
        # Import governance
        try:
            from research_engine.v10.research_governance.evidence_maturity import (
                assess_maturity, assess_decision,
            )
        except ImportError:
            assess_maturity = lambda n, **kw: "DEVELOPING"
            assess_decision = lambda **kw: {"status": "INCONCLUSIVE", "reason": "governance unavailable"}

        # Determine maturity
        consistency = result.periods_positive / max(result.periods_total, 1)
        maturity = assess_maturity(
            sample_size=result.n,
            effect_size=result.mean_r,
            consistency=consistency,
        )
        result.evidence_maturity = maturity

        # Determine verdict
        p_value = result.permutation_p or 1.0
        bonferroni = hypothesis.bonferroni_threshold
        passes_significance = p_value < bonferroni

        # For CONDITIONING_ANALYSIS (no paired control): use bootstrap CI as significance proxy
        # The correct statistical question is "is the observed metric meaningfully non-zero?"
        # A bootstrap CI that excludes zero is the appropriate test for unpaired analyses.
        if p_value == 1.0 and result.ci_lower is not None and result.ci_upper is not None:
            # CI-based significance: lower bound > 0 (or upper bound < 0 for negative findings)
            if result.ci_lower > 0 or result.ci_upper < 0:
                passes_significance = True  # CI excludes zero — statistically meaningful

        # Classification logic
        if not passes_significance:
            conclusion = ConclusionType.INCONCLUSIVE
            reason = f"p={p_value:.4f} does not pass Bonferroni threshold ({bonferroni:.4f})"
            confidence = "LOW"
            classification = "RED"
        elif placebo and not placebo.placebo_passes:
            conclusion = ConclusionType.REJECTED
            reason = (f"Placebo FAILS: {placebo.positive_fraction:.0%} of controls positive "
                      f"(threshold {placebo.threshold:.0%}). Effect is general, not specific.")
            confidence = "HIGH"
            classification = "RED"
        elif result.oos_mean_r <= 0 and result.oos_n > 20:
            conclusion = ConclusionType.INCONCLUSIVE
            reason = f"OOS mean R = {result.oos_mean_r:+.4f} (not positive)"
            confidence = "MEDIUM"
            classification = "AMBER"
        elif result.passes_validation:
            conclusion = ConclusionType.VALIDATED
            reason = (f"Passes all gates: p={p_value:.4f}, OOS={result.oos_mean_r:+.4f}, "
                      f"symbols={result.symbols_positive}/{result.symbols_total}, "
                      f"placebo={'PASS' if (not placebo or placebo.placebo_passes) else 'FAIL'}")
            confidence = "HIGH" if result.n >= 200 else "MEDIUM"
            classification = "GREEN"
        else:
            conclusion = ConclusionType.INCONCLUSIVE
            reason = "Mixed evidence — does not meet all validation criteria"
            confidence = "LOW"
            classification = "AMBER"

        result.classification = classification
        result.decision_status = conclusion.value

        # Apply conclusion
        hypothesis.conclude(conclusion, reason=reason, confidence=confidence,
                            evidence_ref=result.experiment_id)
        self._registry.update(hypothesis)

        return conclusion

    # ─── 5. KNOWLEDGE MAP UPDATE ──────────────────────────────────────

    def update_knowledge_map(self, hypothesis: Hypothesis, result: ExperimentResult) -> bool:
        """
        Persist conclusion to the research knowledge map.
        
        Compatibility contract:
        - Preserves ALL existing entries regardless of format
        - Adds lifecycle findings under "lifecycle_findings" key (dict, keyed by hypothesis_id)
        - Never overwrites/modifies the top-level "findings" dict (used by existing consumers)
        - Validates structure before writing
        - Returns True on success, False on failure (logs to audit trail)
        - Handles: empty file, existing v2 format, malformed JSON, missing keys
        """
        try:
            self._knowledge_path.parent.mkdir(parents=True, exist_ok=True)

            # Load existing safely
            knowledge = {}
            if self._knowledge_path.exists():
                raw = self._knowledge_path.read_text(encoding="utf-8").strip()
                if raw:
                    try:
                        knowledge = json.loads(raw)
                    except json.JSONDecodeError:
                        backup = self._knowledge_path.with_suffix(".json.bak")
                        backup.write_text(raw, encoding="utf-8")
                        knowledge = {}

            if not isinstance(knowledge, dict):
                backup = self._knowledge_path.with_suffix(".json.bak")
                backup.write_text(json.dumps(knowledge, default=str), encoding="utf-8")
                knowledge = {"_migrated_from_non_dict": True}

            # Add lifecycle findings in SEPARATE namespace (never touch existing "findings")
            lifecycle_findings = knowledge.setdefault("lifecycle_findings", {})
            lifecycle_findings[hypothesis.hypothesis_id] = {
                "title": hypothesis.title,
                "conclusion": hypothesis.conclusion_type.value if hypothesis.conclusion_type else "UNKNOWN",
                "confidence": hypothesis.conclusion_confidence,
                "classification": result.classification,
                "mean_r": round(result.mean_r, 4),
                "n": result.n,
                "win_rate": round(result.win_rate, 3),
                "oos_mean_r": round(result.oos_mean_r, 4),
                "placebo_passes": result.placebo_passes,
                "reason": hypothesis.conclusion_reason,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "experiments": len(hypothesis.experiments),
                "category": hypothesis.category.value,
            }

            knowledge["last_updated"] = datetime.now(timezone.utc).isoformat()

            # Write atomically
            tmp = self._knowledge_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(knowledge, indent=2, default=str), encoding="utf-8")
            tmp.replace(self._knowledge_path)
            return True

        except Exception as e:
            self._registry._log_event("KNOWLEDGE_MAP_WRITE_FAILED",
                                       hypothesis.hypothesis_id, str(e)[:200])
            return False

    # ─── 6. REPORT GENERATION ─────────────────────────────────────────

    def generate_report(self, hypothesis: Hypothesis, result: ExperimentResult,
                        placebo: PlaceboTestOutcome | None = None) -> str:
        """
        Generate a complete human-readable research report.
        
        Returns the report as a string (also persists to file).
        """
        lines = []
        lines.append(f"# Research Report: {hypothesis.title}")
        lines.append("")
        lines.append(f"**Hypothesis ID**: {hypothesis.hypothesis_id}")
        lines.append(f"**Status**: {hypothesis.status.value}")
        lines.append(f"**Conclusion**: {hypothesis.conclusion_type.value if hypothesis.conclusion_type else 'PENDING'}")
        lines.append(f"**Confidence**: {hypothesis.conclusion_confidence}")
        lines.append(f"**Classification**: {result.classification}")
        lines.append("")

        lines.append("## Claim")
        lines.append(f"> {hypothesis.claim}")
        lines.append("")

        lines.append("## Results")
        lines.append(f"| Metric | Value |")
        lines.append(f"|---|---|")
        lines.append(f"| N | {result.n} |")
        lines.append(f"| Mean R | {result.mean_r:+.4f} |")
        lines.append(f"| Total R | {result.total_r:+.1f} |")
        lines.append(f"| Win Rate | {result.win_rate:.1%} |")
        lines.append(f"| 90% CI | [{result.ci_lower:+.3f}, {result.ci_upper:+.3f}] |" if result.ci_lower else "")
        lines.append(f"| Permutation p | {result.permutation_p:.4f} |" if result.permutation_p else "")
        lines.append("")

        # Dataset Provenance
        fp = result.dataset_fingerprint
        if fp:
            lines.append("## Dataset Provenance")
            lines.append(f"| Property | Value |")
            lines.append(f"|---|---|")
            lines.append(f"| Dataset ID | {fp.get('dataset_id', 'N/A')} |")
            lines.append(f"| Version | {fp.get('dataset_version', 'N/A')} |")
            lines.append(f"| Population | {fp.get('population', 'N/A')} |")
            lines.append(f"| Observations | {fp.get('observation_count', 'N/A')} |")
            lines.append(f"| Content SHA-256 | `{fp.get('content_hash', 'N/A')[:16]}...` |")
            lines.append(f"| Algorithm | {fp.get('fingerprint_algorithm', 'N/A')} |")
            lines.append(f"| Schema | {fp.get('schema_version', 'N/A')} |")
            lines.append(f"| First observation | {fp.get('first_timestamp', 'N/A')} |")
            lines.append(f"| Last observation | {fp.get('last_timestamp', 'N/A')} |")
            lines.append(f"| Symbols | {', '.join(fp.get('symbols', []))} |" if fp.get('symbols') else "")
            lines.append(f"| Filters | {'; '.join(fp.get('filters_applied', []))} |" if fp.get('filters_applied') else "")
            lines.append("")
        else:
            lines.append("## Dataset Provenance")
            lines.append("*Fingerprint: UNAVAILABLE (historical experiment)*")
            lines.append("")

        lines.append("## Validation")
        lines.append(f"- OOS (N={result.oos_n}): Mean R = {result.oos_mean_r:+.4f}")
        lines.append(f"- Symbols positive: {result.symbols_positive}/{result.symbols_total}")
        lines.append(f"- Temporal stability: {result.periods_positive}/{result.periods_total} periods positive")
        lines.append(f"- Outlier robust (top-20 removed): {'YES' if result.survives_top20_removal else 'NO'}")
        lines.append("")

        if placebo:
            lines.append("## Placebo Control")
            lines.append(f"- Positive placebos: {placebo.positive_placebos}/{placebo.total_placebos}")
            lines.append(f"- Passes: {'YES' if placebo.placebo_passes else 'NO'}")
            lines.append(f"- {placebo.interpretation}")
            lines.append("")

        lines.append("## Discovery Bias")
        lines.append(f"- Variants tested before discovery: {hypothesis.multiple_testing_count}")
        lines.append(f"- Bonferroni threshold: p < {hypothesis.bonferroni_threshold:.4f}")
        lines.append(f"- {hypothesis.discovery_bias_notes}")
        lines.append("")

        lines.append("## Conclusion")
        lines.append(f"**{hypothesis.conclusion_type.value if hypothesis.conclusion_type else 'PENDING'}**: {hypothesis.conclusion_reason}")
        lines.append("")

        lines.append("## Governance")
        lines.append(f"- Human approval required: {hypothesis.human_approval_required}")
        lines.append(f"- Human approval granted: {hypothesis.human_approval_granted}")
        if hypothesis.human_approval_granted:
            lines.append(f"- Approved: {hypothesis.human_approval_timestamp}")
            lines.append(f"- Notes: {hypothesis.human_approval_notes}")
        lines.append("")

        lines.append("## Audit Trail")
        for t in hypothesis.transitions:
            lines.append(f"- {t.timestamp}: {t.from_status} → {t.to_status} ({t.reason})")
        lines.append("")

        report_text = "\n".join(lines)

        # Persist
        try:
            report_dir = _REPORT_DIR
            report_dir.mkdir(parents=True, exist_ok=True)
            report_path = report_dir / f"{hypothesis.hypothesis_id}_report.md"
            report_path.write_text(report_text, encoding="utf-8")

            # Update catalogue with report path for all experiments of this hypothesis
            for exp in hypothesis.experiments:
                self._catalogue.update_result(
                    exp.experiment_id, report_path=str(report_path))
        except Exception:
            pass

        return report_text


# ═══════════════════════════════════════════════════════════════════════════════
# OPTIMISATION CANDIDATE CREATION
# ═══════════════════════════════════════════════════════════════════════════════

    def create_optimisation_candidate(
        self,
        hypothesis: "Hypothesis",
        result: "ExperimentResult",
    ) -> dict[str, Any] | None:
        """
        Create an optimisation candidate from a VALIDATED conclusion.
        
        Returns candidate dict on success, None if not VALIDATED or cannot create.
        Uses existing CandidateRecord + CandidateRegistry infrastructure.
        
        GOVERNANCE: Candidate is created in PROPOSED status.
        It CANNOT reach production without explicit human approval.
        """
        if hypothesis.conclusion_type != ConclusionType.VALIDATED:
            return None

        try:
            from research_engine.v10.candidates.models import CandidateRecord
            from research_engine.v10.candidates.candidate_registry import CandidateRegistry
            from research_engine.lifecycle.investigation_contracts import get_contract

            contract = get_contract(hypothesis.category) if hasattr(hypothesis, 'category') else None

            # Determine proposed change from category
            change_def = self._derive_change_definition(hypothesis, result, contract)
            if not change_def:
                return None

            # Calculate expected impact
            impact = {
                "delta_r_per_trade": round(result.mean_r, 4),
                "confidence_interval": [round(result.ci_lower or 0, 4), round(result.ci_upper or 0, 4)],
                "sample_size": result.n,
                "oos_effect": round(result.oos_mean_r, 4),
                "win_rate": round(result.win_rate, 3),
                "evidence_maturity": result.evidence_maturity,
            }

            # Risk assessment
            risk_level = "LOW"
            if result.n < 100:
                risk_level = "MEDIUM"
            if result.n < 50 or not result.survives_top20_removal:
                risk_level = "HIGH"

            candidate_id = f"OPT-{hypothesis.hypothesis_id[-8:]}"

            record = CandidateRecord(
                candidate_id=candidate_id,
                hypothesis_id=hypothesis.hypothesis_id,
                baseline_id="current_v10",
                component=hypothesis.category.value if hasattr(hypothesis.category, 'value') else "OTHER",
                description=f"From lifecycle: {hypothesis.title}",
                change_definition={
                    **change_def,
                    "source_finding_id": hypothesis.source_finding_id,
                    "expected_impact": impact,
                    "risk_assessment": risk_level,
                    "experiment_id": result.experiment_id,
                },
                expected_outcome=f"+{result.mean_r:.3f}R/trade (CI: {result.ci_lower:+.3f} to {result.ci_upper:+.3f})" if result.ci_lower else "",
                risk_level=risk_level,
                status="PROPOSED",
            )

            # Register (idempotent — skip if exists)
            try:
                registry = CandidateRegistry()
                registry.create(record)
            except ValueError:
                pass  # Already exists

            self._registry._log_event("OPTIMISATION_CANDIDATE_CREATED",
                                       hypothesis.hypothesis_id, candidate_id)

            return record.to_dict()

        except Exception as e:
            self._registry._log_event("CANDIDATE_CREATION_FAILED",
                                       hypothesis.hypothesis_id, str(e)[:100])
            return None

    def _derive_change_definition(self, hypothesis, result, contract) -> dict | None:
        """Derive what the proposed change would be from the hypothesis category."""
        from research_engine.lifecycle.hypothesis import HypothesisCategory

        cat = hypothesis.category
        if cat == HypothesisCategory.DIRECTION_INVERSION:
            return {"type": "direction_inversion", "action": "invert_pattern_direction",
                    "rationale": hypothesis.conclusion_reason}
        elif cat == HypothesisCategory.PATTERN_SIGNAL:
            return {"type": "pattern_weighting", "action": "adjust_pattern_confidence",
                    "rationale": hypothesis.conclusion_reason}
        elif cat == HypothesisCategory.REGIME_CONDITIONING:
            return {"type": "regime_conditioning", "action": "add_regime_gate",
                    "rationale": hypothesis.conclusion_reason}
        elif cat == HypothesisCategory.GEOMETRY_DEFECT:
            return {"type": "geometry_modification", "action": "adjust_stop_construction",
                    "rationale": hypothesis.conclusion_reason}
        elif cat == HypothesisCategory.SCORE_MONOTONICITY:
            return {"type": "score_recalibration", "action": "recalibrate_scoring",
                    "rationale": hypothesis.conclusion_reason}
        elif cat == HypothesisCategory.OTHER:
            # Symbol anomaly, temporal — these produce research recommendations, not direct changes
            return {"type": "research_recommendation", "action": "further_investigation_required",
                    "rationale": hypothesis.conclusion_reason}
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# INVESTIGATION RESULT
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class InvestigationResult:
    """
    Complete result of a governed investigation.
    
    Contains everything needed to understand what happened without
    reading individual reports or logs.
    """
    hypothesis_id: str = ""
    experiment_id: str = ""
    experiment_type: str = ""
    status: str = ""                    # "complete" | "failed" | "validation_failed"

    # Experiment result
    experiment_result: ExperimentResult | None = None

    # Validation
    validation_performed: list[str] = field(default_factory=list)
    placebo_performed: bool = False
    placebo_outcome: Any = None         # PlaceboTestOutcome or None

    # Conclusion
    conclusion: str = ""                # VALIDATED / REJECTED / INCONCLUSIVE
    confidence: str = ""
    classification: str = ""            # GREEN / AMBER / RED
    conclusion_reason: str = ""

    # Governance
    governance_status: str = "BLOCKED"  # Always BLOCKED — human approval required
    promotion_eligible: bool = False

    # Artefacts
    report_path: str = ""
    report_text: str = ""

    # Next action
    next_recommended_action: str = ""

    # Failure info
    failure_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "experiment_id": self.experiment_id,
            "experiment_type": self.experiment_type,
            "status": self.status,
            "conclusion": self.conclusion,
            "confidence": self.confidence,
            "classification": self.classification,
            "conclusion_reason": self.conclusion_reason,
            "governance_status": self.governance_status,
            "promotion_eligible": self.promotion_eligible,
            "validation_performed": self.validation_performed,
            "placebo_performed": self.placebo_performed,
            "report_path": self.report_path,
            "next_recommended_action": self.next_recommended_action,
            "failure_reason": self.failure_reason,
            "n": self.experiment_result.n if self.experiment_result else 0,
            "mean_r": self.experiment_result.mean_r if self.experiment_result else 0,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# UNIFIED INVESTIGATE() ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

# Import here to avoid circular at module level


class _InvestigateMethod:
    """Mixin-style implementation. Added to ResearchOrchestrator below."""
    pass


def _add_investigate_to_orchestrator():
    """Attach investigate() to ResearchOrchestrator after class definition."""

    def investigate(
        self,
        hypothesis: "Hypothesis",
        experiment_type: "ExperimentType",
        experiment_definition: "ExperimentDefinition",
        placebo_populations: dict[str, list[dict]] | None = None,
    ) -> "InvestigationResult":
        """
        Single governed entry point for a complete research investigation.
        
        Composes the full lifecycle automatically:
            1. Validate definition against template
            2. Select canonical execute_fn from template registry
            3. Execute experiment (auto-registers in catalogue)
            4. Perform placebo controls (if required by template)
            5. Challenge hypothesis with validation evidence
            6. Reach governed conclusion
            7. Update knowledge map
            8. Generate complete research report
            9. Return structured investigation result
        
        The caller does NOT need to manually invoke run_experiment(), challenge(),
        conclude(), or generate_report().
        
        GOVERNANCE: This method NEVER approves or promotes. It stops at the
        governance boundary. Human approval is ALWAYS required for promotion.
        
        Args:
            hypothesis: Registered hypothesis to investigate
            experiment_type: Which experiment methodology to use
            experiment_definition: Full experiment parameters
            placebo_populations: Optional dict of pattern → records for placebo test.
                If None and template requires placebo, auto-derives from shadow population.
        
        Returns:
            InvestigationResult with complete status, result, and governance info.
        """
        from research_engine.lifecycle.experiment_templates import ExperimentTemplateRegistry
        from research_engine.lifecycle.experiment_protocol import ExperimentType as _ET

        inv_result = InvestigationResult(
            hypothesis_id=hypothesis.hypothesis_id,
            experiment_id=experiment_definition.experiment_id,
            experiment_type=experiment_type.value if hasattr(experiment_type, 'value') else str(experiment_type),
        )

        # ─── STEP 1: VALIDATE ────────────────────────────────────────
        template_registry = ExperimentTemplateRegistry()

        if not template_registry.supports(experiment_type):
            inv_result.status = "failed"
            inv_result.failure_reason = f"Unsupported experiment type: {experiment_type.value}"
            self._registry._log_event("INVESTIGATION_FAILED", hypothesis.hypothesis_id,
                                       inv_result.failure_reason)
            return inv_result

        template = template_registry.get(experiment_type)
        valid, reason = template_registry.validate(experiment_definition)
        if not valid:
            inv_result.status = "failed"
            inv_result.failure_reason = f"Definition validation failed: {reason}"
            self._registry._log_event("INVESTIGATION_VALIDATION_FAILED",
                                       hypothesis.hypothesis_id, reason)
            return inv_result

        # ─── STEP 2: SELECT EXECUTE_FN ────────────────────────────────
        execute_fn = template_registry.get_execute_fn(experiment_type)
        if execute_fn is None:
            inv_result.status = "failed"
            inv_result.failure_reason = f"No canonical execute_fn for {experiment_type.value}"
            return inv_result

        # ─── STEP 3: EXECUTE EXPERIMENT ───────────────────────────────
        # Uses existing run_experiment() which auto-registers in catalogue
        self._registry._log_event("INVESTIGATION_STARTED", hypothesis.hypothesis_id,
                                   experiment_definition.experiment_id)

        result = self.run_experiment(hypothesis, experiment_definition, execute_fn)

        inv_result.experiment_result = result
        inv_result.experiment_id = experiment_definition.experiment_id

        if result.status == "failed":
            inv_result.status = "failed"
            inv_result.failure_reason = "Experiment execution failed"
            return inv_result

        # ─── STEP 4: RECORD VALIDATION PERFORMED ─────────────────────
        inv_result.validation_performed = list(template.validation_methods)

        # ─── STEP 5: PLACEBO (if required) ────────────────────────────
        placebo_outcome = None
        if template.requires_placebo:
            from research_engine.lifecycle.placebo_controller import run_placebo_test
            from research_engine.lifecycle.experiment_templates import (
                _load_shadow_population, _filter_population, _load_candles, _simulate_trade,
            )

            # Auto-derive placebo populations if not provided
            if placebo_populations is None:
                all_pop = _load_shadow_population()
                target_patterns = set(experiment_definition.population.pattern_filter)
                placebo_populations = {}
                from collections import defaultdict
                by_pattern = defaultdict(list)
                for p in all_pop:
                    pat = p.get("pattern", "")
                    if pat and pat not in target_patterns:
                        by_pattern[pat].append(p)
                placebo_populations = {k: v for k, v in by_pattern.items() if len(v) >= 20}

            if placebo_populations:
                def _placebo_fn(pop, pat_name):
                    """Run same experimental protocol on control population."""
                    results = []
                    sim = experiment_definition.simulation
                    for p in pop[:80]:
                        risk = abs(p.get("entry", 0) - p.get("sl", 0))
                        if risk <= 0:
                            continue
                        candles = _load_candles(p.get("symbol", ""), p.get("time", 0))
                        if len(candles) < 10:
                            continue
                        inv_dir = "BUY" if p.get("dir") == "SELL" else "SELL"
                        if inv_dir == "BUY":
                            new_sl = p["entry"] - risk * sim.stop_multiplier
                            new_tp = p["entry"] + risk * sim.tp_multiplier
                        else:
                            new_sl = p["entry"] + risk * sim.stop_multiplier
                            new_tp = p["entry"] - risk * sim.tp_multiplier
                        r = _simulate_trade(direction=inv_dir, entry_price=p["entry"],
                                             stop_loss=new_sl, take_profit=new_tp,
                                             candles=candles, max_bars=sim.max_bars)
                        results.append(r["r_multiple"])
                    return results

                placebo_outcome = run_placebo_test(
                    hypothesis_id=hypothesis.hypothesis_id,
                    experiment_fn=_placebo_fn,
                    control_populations=placebo_populations,
                    min_n=15,
                    positive_threshold=0.5,
                )
                inv_result.placebo_performed = True
                inv_result.placebo_outcome = placebo_outcome

                # Transfer to result for conclude()
                result.placebo_positive_fraction = placebo_outcome.positive_fraction
                result.placebo_patterns_tested = placebo_outcome.total_placebos
                result.placebo_passes = placebo_outcome.placebo_passes

        # ─── STEP 6: CHALLENGE ────────────────────────────────────────
        self.challenge(hypothesis, result, placebo_outcome)

        # ─── STEP 7: CONCLUDE ─────────────────────────────────────────
        conclusion = self.conclude(hypothesis, result, placebo_outcome)

        inv_result.conclusion = conclusion.value
        inv_result.confidence = hypothesis.conclusion_confidence
        inv_result.classification = result.classification
        inv_result.conclusion_reason = hypothesis.conclusion_reason

        # ─── STEP 8: KNOWLEDGE MAP ───────────────────────────────────
        self.update_knowledge_map(hypothesis, result)

        # ─── STEP 8b: OPTIMISATION CANDIDATE (if VALIDATED) ──────────
        if conclusion == ConclusionType.VALIDATED:
            self.create_optimisation_candidate(hypothesis, result)

        # ─── STEP 9: REPORT ──────────────────────────────────────────
        report_text = self.generate_report(hypothesis, result, placebo_outcome)
        inv_result.report_text = report_text

        # Determine report path
        report_path = str(_REPORT_DIR / f"{hypothesis.hypothesis_id}_report.md")
        inv_result.report_path = report_path

        # ─── STEP 10: GOVERNANCE STATUS ──────────────────────────────
        eligible, _ = self._gate.can_promote(hypothesis)
        inv_result.promotion_eligible = eligible
        inv_result.governance_status = "AWAITING_HUMAN_APPROVAL" if eligible else "BLOCKED"

        # ─── STEP 11: NEXT ACTION ────────────────────────────────────
        if conclusion == ConclusionType.VALIDATED:
            inv_result.next_recommended_action = (
                "Finding VALIDATED. Submit for human governance review. "
                "Do NOT implement without explicit human approval."
            )
        elif conclusion == ConclusionType.REJECTED:
            inv_result.next_recommended_action = (
                "Finding REJECTED. Stop pursuing this hypothesis. "
                "Investigate root cause of failure or alternative hypotheses."
            )
        else:
            inv_result.next_recommended_action = (
                "Finding INCONCLUSIVE. Collect more evidence or "
                "reformulate the hypothesis with tighter constraints."
            )

        inv_result.status = "complete"
        self._registry._log_event("INVESTIGATION_COMPLETED", hypothesis.hypothesis_id,
                                   f"{conclusion.value}/{result.classification}")
        return inv_result

    # Attach to the class
    ResearchOrchestrator.investigate = investigate


# Run the attachment
_add_investigate_to_orchestrator()
