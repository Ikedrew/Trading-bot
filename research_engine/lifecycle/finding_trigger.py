"""
Finding Trigger — Automatic detection of research-worthy anomalies.

Scans research findings and identifies those warranting further investigation.
Creates governed hypotheses from qualifying findings WITHOUT human intervention.

Flow:
    Finding Source (research question results, knowledge map, baseline)
        ↓
    FindingTrigger (detected anomaly)
        ↓
    Eligibility screening (rules-based)
        ↓
    Deduplication check (knowledge map + existing hypotheses)
        ↓
    Hypothesis creation (via orchestrator.detect_and_register)
        ↓
    Experiment selection (via ExperimentTemplateRegistry)
        ↓
    Optional: investigate() [if DETECT_AND_INVESTIGATE mode]

Two modes:
    DETECT_ONLY (default): Detect, screen, report. Human initiates investigation.
    DETECT_AND_INVESTIGATE: Detect, screen, auto-investigate, conclude, report.

This module NEVER modifies production V10 or bypasses GovernanceGate.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from research_engine.lifecycle.hypothesis import HypothesisCategory
from research_engine.lifecycle.experiment_protocol import ExperimentType


# ═══════════════════════════════════════════════════════════════════════════════
# TRIGGER MODEL
# ═══════════════════════════════════════════════════════════════════════════════

class TriggerStatus(str, Enum):
    """Lifecycle of a finding trigger."""
    DETECTED = "DETECTED"           # Anomaly identified from data
    SCREENED = "SCREENED"           # Passed initial quality check
    ELIGIBLE = "ELIGIBLE"           # Meets investigation criteria
    REGISTERED = "REGISTERED"       # Hypothesis created
    INVESTIGATING = "INVESTIGATING" # investigate() running
    COMPLETED = "COMPLETED"         # Investigation concluded
    DISMISSED = "DISMISSED"         # Did not meet eligibility or was deduplicated
    BLOCKED = "BLOCKED"             # Cannot proceed (existing knowledge blocks it)


class TriggerCategory(str, Enum):
    """What kind of anomaly triggered the finding."""
    POOR_PATTERN_PERFORMANCE = "POOR_PATTERN_PERFORMANCE"
    STRONG_PATTERN_PERFORMANCE = "STRONG_PATTERN_PERFORMANCE"
    DIRECTION_ASYMMETRY = "DIRECTION_ASYMMETRY"
    REGIME_ANOMALY = "REGIME_ANOMALY"
    SYMBOL_ANOMALY = "SYMBOL_ANOMALY"
    TEMPORAL_INSTABILITY = "TEMPORAL_INSTABILITY"
    EXECUTION_ANOMALY = "EXECUTION_ANOMALY"
    GEOMETRY_ANOMALY = "GEOMETRY_ANOMALY"
    SCORE_MONOTONICITY = "SCORE_MONOTONICITY"
    KNOWLEDGE_CONTRADICTION = "KNOWLEDGE_CONTRADICTION"
    EXIT_INEFFICIENCY = "EXIT_INEFFICIENCY"
    GUARD_VALUE_NEGATIVE = "GUARD_VALUE_NEGATIVE"
    DRAWDOWN_APPROACHING = "DRAWDOWN_APPROACHING"
    SESSION_DEGRADATION = "SESSION_DEGRADATION"
    SPREAD_ANOMALY = "SPREAD_ANOMALY"
    SLIPPAGE_DETERIORATION = "SLIPPAGE_DETERIORATION"
    HORIZON_QUALITY = "HORIZON_QUALITY"
    SR_DIVERGENCE = "SR_DIVERGENCE"
    STRATEGY_DEGRADATION = "STRATEGY_DEGRADATION"
    RISK_SIZING_ANOMALY = "RISK_SIZING_ANOMALY"


# Category → suggested experiment type mapping
_CATEGORY_TO_EXPERIMENT: dict[TriggerCategory, ExperimentType] = {
    TriggerCategory.POOR_PATTERN_PERFORMANCE: ExperimentType.DIRECTION_INVERSION,
    TriggerCategory.STRONG_PATTERN_PERFORMANCE: ExperimentType.ROBUSTNESS_CHECK,
    TriggerCategory.DIRECTION_ASYMMETRY: ExperimentType.DIRECTION_INVERSION,
    TriggerCategory.REGIME_ANOMALY: ExperimentType.CONDITIONING_ANALYSIS,
    TriggerCategory.SYMBOL_ANOMALY: ExperimentType.CONDITIONING_ANALYSIS,
    TriggerCategory.TEMPORAL_INSTABILITY: ExperimentType.OOS_VALIDATION,
    TriggerCategory.EXECUTION_ANOMALY: ExperimentType.POPULATION_COMPARISON,
    TriggerCategory.GEOMETRY_ANOMALY: ExperimentType.COUNTERFACTUAL_GEOMETRY,
    TriggerCategory.SCORE_MONOTONICITY: ExperimentType.CONDITIONING_ANALYSIS,
    TriggerCategory.KNOWLEDGE_CONTRADICTION: ExperimentType.ROBUSTNESS_CHECK,
    TriggerCategory.EXIT_INEFFICIENCY: ExperimentType.COUNTERFACTUAL_GEOMETRY,
    TriggerCategory.GUARD_VALUE_NEGATIVE: ExperimentType.CONDITIONING_ANALYSIS,
    TriggerCategory.DRAWDOWN_APPROACHING: ExperimentType.OOS_VALIDATION,
    TriggerCategory.SESSION_DEGRADATION: ExperimentType.CONDITIONING_ANALYSIS,
    TriggerCategory.SPREAD_ANOMALY: ExperimentType.CONDITIONING_ANALYSIS,
    TriggerCategory.SLIPPAGE_DETERIORATION: ExperimentType.POPULATION_COMPARISON,
    TriggerCategory.HORIZON_QUALITY: ExperimentType.CONDITIONING_ANALYSIS,
    TriggerCategory.SR_DIVERGENCE: ExperimentType.POPULATION_COMPARISON,
    TriggerCategory.STRATEGY_DEGRADATION: ExperimentType.OOS_VALIDATION,
    TriggerCategory.RISK_SIZING_ANOMALY: ExperimentType.CONDITIONING_ANALYSIS,
}

# Category → hypothesis category mapping
_CATEGORY_TO_HYPOTHESIS: dict[TriggerCategory, HypothesisCategory] = {
    TriggerCategory.POOR_PATTERN_PERFORMANCE: HypothesisCategory.PATTERN_SIGNAL,
    TriggerCategory.STRONG_PATTERN_PERFORMANCE: HypothesisCategory.PATTERN_SIGNAL,
    TriggerCategory.DIRECTION_ASYMMETRY: HypothesisCategory.DIRECTION_INVERSION,
    TriggerCategory.REGIME_ANOMALY: HypothesisCategory.REGIME_CONDITIONING,
    TriggerCategory.SYMBOL_ANOMALY: HypothesisCategory.OTHER,
    TriggerCategory.TEMPORAL_INSTABILITY: HypothesisCategory.OTHER,
    TriggerCategory.EXECUTION_ANOMALY: HypothesisCategory.EXECUTION_LEAKAGE,
    TriggerCategory.GEOMETRY_ANOMALY: HypothesisCategory.GEOMETRY_DEFECT,
    TriggerCategory.SCORE_MONOTONICITY: HypothesisCategory.SCORE_MONOTONICITY,
    TriggerCategory.KNOWLEDGE_CONTRADICTION: HypothesisCategory.OTHER,
    TriggerCategory.EXIT_INEFFICIENCY: HypothesisCategory.GEOMETRY_DEFECT,
    TriggerCategory.GUARD_VALUE_NEGATIVE: HypothesisCategory.GUARD_QUALITY,
    TriggerCategory.DRAWDOWN_APPROACHING: HypothesisCategory.OTHER,
    TriggerCategory.SESSION_DEGRADATION: HypothesisCategory.REGIME_CONDITIONING,
    TriggerCategory.SPREAD_ANOMALY: HypothesisCategory.REGIME_CONDITIONING,
    TriggerCategory.SLIPPAGE_DETERIORATION: HypothesisCategory.EXECUTION_LEAKAGE,
    TriggerCategory.HORIZON_QUALITY: HypothesisCategory.REGIME_CONDITIONING,
    TriggerCategory.SR_DIVERGENCE: HypothesisCategory.EXECUTION_LEAKAGE,
    TriggerCategory.STRATEGY_DEGRADATION: HypothesisCategory.OTHER,
    TriggerCategory.RISK_SIZING_ANOMALY: HypothesisCategory.OTHER,
}


@dataclass
class FindingTrigger:
    """A detected anomaly that may warrant research investigation."""
    trigger_id: str = ""
    finding_id: str = ""            # Source finding ID
    source: str = ""                # Where the finding came from
    category: TriggerCategory = TriggerCategory.POOR_PATTERN_PERFORMANCE
    title: str = ""
    observation: str = ""           # What was observed
    evidence: dict[str, Any] = field(default_factory=dict)
    confidence: str = ""
    sample_size: int = 0
    evidence_maturity: str = ""
    trigger_reason: str = ""        # Why this warrants investigation
    priority: int = 2               # 0=critical, 1=high, 2=normal, 3=low

    # Suggested actions
    suggested_experiment_type: ExperimentType = ExperimentType.DIRECTION_INVERSION
    suggested_hypothesis_category: HypothesisCategory = HypothesisCategory.OTHER
    suggested_claim: str = ""
    suggested_null: str = ""
    suggested_patterns: list[str] = field(default_factory=list)

    # Lifecycle
    status: TriggerStatus = TriggerStatus.DETECTED
    hypothesis_id: str = ""         # Populated if hypothesis was created
    dismissed_reason: str = ""
    detected_at: str = ""
    resolved_at: str = ""

    def __post_init__(self):
        if not self.trigger_id:
            self.trigger_id = f"TRG-{uuid.uuid4().hex[:8]}"
        if not self.detected_at:
            self.detected_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "trigger_id": self.trigger_id,
            "finding_id": self.finding_id,
            "source": self.source,
            "category": self.category.value,
            "title": self.title,
            "observation": self.observation,
            "evidence": self.evidence,
            "confidence": self.confidence,
            "sample_size": self.sample_size,
            "evidence_maturity": self.evidence_maturity,
            "trigger_reason": self.trigger_reason,
            "priority": self.priority,
            "suggested_experiment_type": self.suggested_experiment_type.value,
            "suggested_hypothesis_category": self.suggested_hypothesis_category.value,
            "suggested_claim": self.suggested_claim,
            "suggested_null": self.suggested_null,
            "suggested_patterns": self.suggested_patterns,
            "status": self.status.value,
            "hypothesis_id": self.hypothesis_id,
            "dismissed_reason": self.dismissed_reason,
            "detected_at": self.detected_at,
            "resolved_at": self.resolved_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FindingTrigger":
        return cls(
            trigger_id=data.get("trigger_id", ""),
            finding_id=data.get("finding_id", ""),
            source=data.get("source", ""),
            category=TriggerCategory(data.get("category", "POOR_PATTERN_PERFORMANCE")),
            title=data.get("title", ""),
            observation=data.get("observation", ""),
            evidence=data.get("evidence", {}),
            confidence=data.get("confidence", ""),
            sample_size=data.get("sample_size", 0),
            evidence_maturity=data.get("evidence_maturity", ""),
            trigger_reason=data.get("trigger_reason", ""),
            priority=data.get("priority", 2),
            suggested_experiment_type=ExperimentType(data.get("suggested_experiment_type", "DIRECTION_INVERSION")),
            suggested_hypothesis_category=HypothesisCategory(data.get("suggested_hypothesis_category", "OTHER")),
            suggested_claim=data.get("suggested_claim", ""),
            suggested_null=data.get("suggested_null", ""),
            suggested_patterns=data.get("suggested_patterns", []),
            status=TriggerStatus(data.get("status", "DETECTED")),
            hypothesis_id=data.get("hypothesis_id", ""),
            dismissed_reason=data.get("dismissed_reason", ""),
            detected_at=data.get("detected_at", ""),
            resolved_at=data.get("resolved_at", ""),
        )


# ═══════════════════════════════════════════════════════════════════════════════
# ELIGIBILITY CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class EligibilityConfig:
    """Configurable thresholds for finding eligibility."""
    min_sample_size: int = 30
    min_effect_size: float = 0.15       # Minimum |mean_r| to be interesting
    max_win_rate_for_poor: float = 0.15 # WR below this = poor performance
    min_win_rate_for_strong: float = 0.65  # WR above this = strong performance
    cooldown_hours: float = 72.0        # Don't re-trigger same finding within this window
    max_active_triggers: int = 10       # Don't create more than this many active triggers
    # New detector thresholds (from contracts)
    min_direction_delta: float = 0.30   # Direction asymmetry minimum
    min_direction_subgroup_n: int = 20  # Per-direction minimum N
    min_regime_delta: float = 0.20      # Regime anomaly minimum
    min_regime_n: int = 30              # Per-regime minimum N
    min_score_inversion_delta: float = 0.15  # Score Q4-Q1 inversion minimum
    min_score_n: int = 50               # Score monotonicity total N
    min_temporal_delta: float = 0.20    # Temporal shift minimum
    min_temporal_period_n: int = 30     # Per-period minimum N
    min_geometry_delta: float = 0.25    # Geometry quartile difference minimum
    min_geometry_n: int = 25            # Per-quartile minimum N
    min_symbol_delta: float = 0.25      # Symbol anomaly vs others minimum
    min_symbol_n: int = 20              # Per-symbol minimum N


# ═══════════════════════════════════════════════════════════════════════════════
# TRIGGER ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class ExecutionMode(str, Enum):
    DETECT_ONLY = "DETECT_ONLY"
    DETECT_AND_INVESTIGATE = "DETECT_AND_INVESTIGATE"


_TRIGGER_DIR = Path("logs/research_lifecycle")
_TRIGGER_FILE = _TRIGGER_DIR / "finding_triggers.json"


class FindingTriggerEngine:
    """
    Detects research-worthy anomalies and optionally launches investigations.
    
    Two modes:
        DETECT_ONLY (default): Detect → Screen → Report
        DETECT_AND_INVESTIGATE: Detect → Screen → Investigate → Conclude → Report
    """

    def __init__(self, *, mode: ExecutionMode = ExecutionMode.DETECT_ONLY,
                 config: EligibilityConfig | None = None):
        self._mode = mode
        self._config = config or EligibilityConfig()
        self._triggers: dict[str, FindingTrigger] = {}
        self._load()

    @property
    def mode(self) -> ExecutionMode:
        return self._mode

    # ─── DETECTION ────────────────────────────────────────────────────

    def detect_from_pattern_performance(
        self,
        pattern: str,
        mean_r: float,
        win_rate: float,
        sample_size: int,
        *,
        source: str = "baseline_analysis",
    ) -> FindingTrigger | None:
        """
        Detect if a pattern's performance is anomalous enough to trigger investigation.
        
        Returns FindingTrigger if anomalous, None if normal.
        """
        cfg = self._config

        if sample_size < cfg.min_sample_size:
            return None

        trigger = None

        # Poor performance detection
        if win_rate < cfg.max_win_rate_for_poor and mean_r < -cfg.min_effect_size:
            trigger = FindingTrigger(
                finding_id=f"perf_{pattern}_{sample_size}",
                source=source,
                category=TriggerCategory.POOR_PATTERN_PERFORMANCE,
                title=f"{pattern} shows catastrophic performance",
                observation=f"Mean R={mean_r:+.3f}, WR={win_rate:.1%}, N={sample_size}",
                evidence={"mean_r": mean_r, "win_rate": win_rate, "n": sample_size, "pattern": pattern},
                confidence="HIGH" if sample_size >= 100 else "MEDIUM",
                sample_size=sample_size,
                trigger_reason=f"WR={win_rate:.1%} < {cfg.max_win_rate_for_poor:.0%} AND mean_r={mean_r:+.3f} < -{cfg.min_effect_size}",
                priority=1,
                suggested_experiment_type=ExperimentType.DIRECTION_INVERSION,
                suggested_hypothesis_category=HypothesisCategory.DIRECTION_INVERSION,
                suggested_claim=f"Inverting {pattern} direction produces positive expected value",
                suggested_null=f"{pattern} direction has no systematic effect on outcome",
                suggested_patterns=[pattern],
            )

        # Strong performance detection (for robustness validation)
        elif win_rate > cfg.min_win_rate_for_strong and mean_r > cfg.min_effect_size:
            trigger = FindingTrigger(
                finding_id=f"strong_{pattern}_{sample_size}",
                source=source,
                category=TriggerCategory.STRONG_PATTERN_PERFORMANCE,
                title=f"{pattern} shows unusually strong performance",
                observation=f"Mean R={mean_r:+.3f}, WR={win_rate:.1%}, N={sample_size}",
                evidence={"mean_r": mean_r, "win_rate": win_rate, "n": sample_size, "pattern": pattern},
                confidence="HIGH" if sample_size >= 100 else "MEDIUM",
                sample_size=sample_size,
                trigger_reason=f"WR={win_rate:.1%} > {cfg.min_win_rate_for_strong:.0%} AND mean_r={mean_r:+.3f} > {cfg.min_effect_size}",
                priority=2,
                suggested_experiment_type=ExperimentType.ROBUSTNESS_CHECK,
                suggested_hypothesis_category=HypothesisCategory.PATTERN_SIGNAL,
                suggested_claim=f"{pattern} has genuine positive edge",
                suggested_null=f"{pattern} performance is due to sample bias or regime",
                suggested_patterns=[pattern],
            )

        if trigger:
            return self._screen(trigger)
        return None

    def detect_from_finding(self, finding: dict[str, Any]) -> FindingTrigger | None:
        """
        Detect from a generic research finding dict (e.g., from ResearchFinding.to_dict()).
        
        Examines the finding's metrics and conclusion to determine if investigation is warranted.
        """
        outcome = finding.get("outcome", "")
        metrics = finding.get("primary_metrics", {})
        sample_sizes = finding.get("sample_sizes", {})
        confidence = finding.get("confidence", "")
        question_id = finding.get("question_id", "")

        # Only trigger on ANOMALOUS or NEGATIVE outcomes with sufficient data
        if outcome not in ("ANOMALOUS", "NEGATIVE"):
            return None

        total_n = sum(sample_sizes.values()) if sample_sizes else 0
        if total_n < self._config.min_sample_size:
            return None

        trigger = FindingTrigger(
            finding_id=question_id or f"finding_{uuid.uuid4().hex[:6]}",
            source=f"research_question_{question_id}",
            category=TriggerCategory.POOR_PATTERN_PERFORMANCE,
            title=finding.get("title", f"Anomalous finding from {question_id}"),
            observation=finding.get("conclusion", ""),
            evidence=metrics,
            confidence=confidence,
            sample_size=total_n,
            trigger_reason=f"Research finding outcome={outcome}, confidence={confidence}",
            priority=1 if confidence == "HIGH" else 2,
            suggested_claim=finding.get("recommendation", ""),
            suggested_null="No actionable anomaly exists",
        )
        return self._screen(trigger)

    # ─── DETECTOR: DIRECTION ASYMMETRY ────────────────────────────────

    def detect_direction_asymmetry(
        self,
        shadows: list[dict],
        *,
        source: str = "research_cycle_runner",
    ) -> list["FindingTrigger"]:
        """
        Detect patterns where BUY and SELL produce materially different R.
        Groups by (pattern, direction), flags if |delta| > min_direction_delta.
        """
        import statistics
        from collections import defaultdict
        cfg = self._config
        triggers = []

        by_pat_dir: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
        for s in shadows:
            pat = s.get("pattern", "")
            d = s.get("direction", "")
            r = s.get("r_multiple")
            if pat and d and r is not None:
                by_pat_dir[pat][d].append(r)

        n_patterns_tested = 0
        for pat, dirs in by_pat_dir.items():
            buy_vals = dirs.get("BUY", [])
            sell_vals = dirs.get("SELL", [])
            if len(buy_vals) < cfg.min_direction_subgroup_n or len(sell_vals) < cfg.min_direction_subgroup_n:
                continue
            n_patterns_tested += 1
            mean_buy = statistics.mean(buy_vals)
            mean_sell = statistics.mean(sell_vals)
            delta = mean_buy - mean_sell

            if abs(delta) >= cfg.min_direction_delta:
                better = "BUY" if delta > 0 else "SELL"
                worse = "SELL" if delta > 0 else "BUY"
                wr_buy = sum(1 for v in buy_vals if v > 0) / len(buy_vals)
                wr_sell = sum(1 for v in sell_vals if v > 0) / len(sell_vals)
                trigger = FindingTrigger(
                    finding_id=f"dir_asym_{pat}_{len(buy_vals)}_{len(sell_vals)}",
                    source=source,
                    category=TriggerCategory.DIRECTION_ASYMMETRY,
                    title=f"{pat} shows {abs(delta):.2f}R direction asymmetry ({better} >> {worse})",
                    observation=f"BUY: N={len(buy_vals)}, R={mean_buy:+.3f} | SELL: N={len(sell_vals)}, R={mean_sell:+.3f} | delta={delta:+.3f}",
                    evidence={"pattern": pat, "n_buy": len(buy_vals), "n_sell": len(sell_vals),
                              "mean_r_buy": round(mean_buy, 4), "mean_r_sell": round(mean_sell, 4),
                              "delta": round(delta, 4), "wr_buy": round(wr_buy, 3), "wr_sell": round(wr_sell, 3),
                              "multiple_testing_count": n_patterns_tested},
                    confidence="HIGH" if min(len(buy_vals), len(sell_vals)) >= 50 else "MEDIUM",
                    sample_size=len(buy_vals) + len(sell_vals),
                    trigger_reason=f"|delta|={abs(delta):.3f} >= {cfg.min_direction_delta}",
                    priority=1,
                    suggested_experiment_type=ExperimentType.DIRECTION_INVERSION,
                    suggested_hypothesis_category=HypothesisCategory.DIRECTION_INVERSION,
                    suggested_claim=f"Inverting {pat} from {worse} to {better} produces positive R",
                    suggested_null=f"Direction has no systematic effect on {pat} outcome",
                    suggested_patterns=[pat],
                )
                result = self._screen(trigger)
                if result:
                    triggers.append(result)
        return triggers

    # ─── DETECTOR: REGIME ANOMALY ─────────────────────────────────────

    def detect_regime_anomaly(
        self,
        shadows: list[dict],
        *,
        source: str = "research_cycle_runner",
    ) -> list["FindingTrigger"]:
        """Detect regimes with materially different R from the rest."""
        import statistics
        from collections import defaultdict
        cfg = self._config
        triggers = []

        by_regime: dict[str, list] = defaultdict(list)
        for s in shadows:
            regime = s.get("h4_regime", "") or s.get("regime", "")
            r = s.get("r_multiple")
            if regime and r is not None:
                by_regime[regime].append(r)

        all_r = [r for vals in by_regime.values() for r in vals]
        if not all_r:
            return []

        for regime, vals in by_regime.items():
            if len(vals) < cfg.min_regime_n:
                continue
            other = [r for reg, rs in by_regime.items() if reg != regime for r in rs]
            if len(other) < cfg.min_regime_n:
                continue
            mean_r = statistics.mean(vals)
            mean_other = statistics.mean(other)
            delta = mean_r - mean_other

            if abs(delta) >= cfg.min_regime_delta:
                trigger = FindingTrigger(
                    finding_id=f"regime_anom_{regime}_{len(vals)}",
                    source=source,
                    category=TriggerCategory.REGIME_ANOMALY,
                    title=f"{regime} regime shows {delta:+.3f}R anomaly vs population",
                    observation=f"{regime}: N={len(vals)}, R={mean_r:+.3f} | Others: N={len(other)}, R={mean_other:+.3f}",
                    evidence={"regime": regime, "n_regime": len(vals), "mean_r_regime": round(mean_r, 4),
                              "n_other": len(other), "mean_r_other": round(mean_other, 4),
                              "delta": round(delta, 4), "multiple_testing_count": len(by_regime)},
                    confidence="HIGH" if len(vals) >= 60 else "MEDIUM",
                    sample_size=len(vals) + len(other),
                    trigger_reason=f"|delta|={abs(delta):.3f} >= {cfg.min_regime_delta}",
                    priority=2,
                    suggested_experiment_type=ExperimentType.CONDITIONING_ANALYSIS,
                    suggested_hypothesis_category=HypothesisCategory.REGIME_CONDITIONING,
                    suggested_claim=f"V10 performance is regime-dependent; {regime} is anomalous",
                    suggested_null=f"Performance does not vary meaningfully across regimes",
                    suggested_patterns=[],
                )
                result = self._screen(trigger)
                if result:
                    triggers.append(result)
        return triggers

    # ─── DETECTOR: SCORE MONOTONICITY ─────────────────────────────────

    def detect_score_monotonicity(
        self,
        shadows: list[dict],
        *,
        source: str = "research_cycle_runner",
    ) -> list["FindingTrigger"]:
        """Detect non-monotonic score-outcome relationship."""
        import statistics
        cfg = self._config
        triggers = []

        scored = [(s.get("score", 0), s.get("r_multiple")) for s in shadows
                  if s.get("score", 0) > 0 and s.get("r_multiple") is not None]
        if len(scored) < cfg.min_score_n:
            return []

        scored.sort(key=lambda x: x[0])
        n = len(scored)
        q_size = n // 4
        if q_size < 10:
            return []

        quartiles = [scored[i * q_size:(i + 1) * q_size] for i in range(4)]
        means = [statistics.mean([r for _, r in q]) for q in quartiles]
        ns = [len(q) for q in quartiles]

        inversions = sum(1 for i in range(3) if means[i] > means[i + 1])
        q4_q1_delta = means[3] - means[0]

        should_trigger = (inversions >= 2) or (q4_q1_delta < -cfg.min_score_inversion_delta)

        if should_trigger:
            trigger = FindingTrigger(
                finding_id=f"score_mono_{n}_{inversions}",
                source=source,
                category=TriggerCategory.SCORE_MONOTONICITY,
                title=f"Score non-monotonicity: {inversions} inversions, Q4-Q1={q4_q1_delta:+.3f}R",
                observation=f"Q1={means[0]:+.3f} Q2={means[1]:+.3f} Q3={means[2]:+.3f} Q4={means[3]:+.3f}",
                evidence={"quartile_means": [round(m, 4) for m in means], "quartile_ns": ns,
                          "inversions": inversions, "q4_q1_delta": round(q4_q1_delta, 4),
                          "total_n": n, "multiple_testing_count": 1},
                confidence="HIGH" if n >= 200 else "MEDIUM",
                sample_size=n,
                trigger_reason=f"inversions={inversions} OR Q4-Q1={q4_q1_delta:+.3f} < -{cfg.min_score_inversion_delta}",
                priority=2,
                suggested_experiment_type=ExperimentType.CONDITIONING_ANALYSIS,
                suggested_hypothesis_category=HypothesisCategory.SCORE_MONOTONICITY,
                suggested_claim="V10 score does not monotonically predict outcome quality",
                suggested_null="Higher score produces higher expected R",
                suggested_patterns=[],
            )
            result = self._screen(trigger)
            if result:
                triggers.append(result)
        return triggers

    # ─── DETECTOR: TEMPORAL INSTABILITY ───────────────────────────────

    def detect_temporal_instability(
        self,
        shadows: list[dict],
        *,
        source: str = "research_cycle_runner",
    ) -> list["FindingTrigger"]:
        """Detect if latest period R is materially different from earlier."""
        import statistics
        cfg = self._config
        triggers = []

        timed = [(s.get("timestamp_decision_utc", 0) or s.get("entry_time", 0), s.get("r_multiple"))
                 for s in shadows if s.get("r_multiple") is not None]
        timed = [(t, r) for t, r in timed if t > 0]
        timed.sort(key=lambda x: x[0])

        n = len(timed)
        if n < cfg.min_temporal_period_n * 2:
            return []

        # Split into 3 periods
        period_size = n // 3
        if period_size < cfg.min_temporal_period_n:
            return []

        periods = [timed[i * period_size:(i + 1) * period_size] for i in range(3)]
        period_means = [statistics.mean([r for _, r in p]) for p in periods]
        period_ns = [len(p) for p in periods]

        latest_mean = period_means[-1]
        earlier_mean = statistics.mean([r for _, r in timed[:2 * period_size]])
        delta = latest_mean - earlier_mean

        if abs(delta) >= cfg.min_temporal_delta:
            direction = "degradation" if delta < 0 else "improvement"
            trigger = FindingTrigger(
                finding_id=f"temporal_{direction}_{n}",
                source=source,
                category=TriggerCategory.TEMPORAL_INSTABILITY,
                title=f"Performance {direction}: latest period {delta:+.3f}R vs earlier",
                observation=f"P1={period_means[0]:+.3f} P2={period_means[1]:+.3f} P3={period_means[2]:+.3f} | latest vs earlier: {delta:+.3f}",
                evidence={"period_means": [round(m, 4) for m in period_means], "period_ns": period_ns,
                          "latest_mean": round(latest_mean, 4), "earlier_mean": round(earlier_mean, 4),
                          "delta": round(delta, 4), "total_n": n, "multiple_testing_count": 1},
                confidence="HIGH" if period_size >= 60 else "MEDIUM",
                sample_size=n,
                trigger_reason=f"|delta|={abs(delta):.3f} >= {cfg.min_temporal_delta}",
                priority=1 if delta < 0 else 2,
                suggested_experiment_type=ExperimentType.OOS_VALIDATION,
                suggested_hypothesis_category=HypothesisCategory.OTHER,
                suggested_claim=f"System performance has materially shifted ({direction}) in the latest period",
                suggested_null="Performance variation is within normal statistical fluctuation",
                suggested_patterns=[],
            )
            result = self._screen(trigger)
            if result:
                triggers.append(result)
        return triggers

    # ─── DETECTOR: GEOMETRY ANOMALY ───────────────────────────────────

    def detect_geometry_anomaly(
        self,
        shadows: list[dict],
        *,
        source: str = "research_cycle_runner",
    ) -> list["FindingTrigger"]:
        """Detect if stop geometry (tight vs wide) materially affects outcome."""
        import statistics
        cfg = self._config
        triggers = []

        # Extract risk_distance and r_multiple
        geo_data = []
        for s in shadows:
            rd = s.get("risk_distance", 0)
            if not rd:
                snap = s.get("risk_config_snapshot", {})
                if isinstance(snap, dict):
                    rd = snap.get("risk_price_distance", 0)
            r = s.get("r_multiple")
            if rd and rd > 0 and r is not None:
                geo_data.append((rd, r))

        if len(geo_data) < cfg.min_geometry_n * 4:
            return []

        geo_data.sort(key=lambda x: x[0])
        n = len(geo_data)
        q_size = n // 4
        if q_size < cfg.min_geometry_n:
            return []

        q1 = geo_data[:q_size]  # tightest stops
        q4 = geo_data[3 * q_size:]  # widest stops
        mean_tight = statistics.mean([r for _, r in q1])
        mean_wide = statistics.mean([r for _, r in q4])
        delta = mean_tight - mean_wide

        if abs(delta) >= cfg.min_geometry_delta:
            better = "tight" if delta > 0 else "wide"
            trigger = FindingTrigger(
                finding_id=f"geom_anom_{n}_{abs(delta):.2f}",
                source=source,
                category=TriggerCategory.GEOMETRY_ANOMALY,
                title=f"Stop geometry anomaly: {better} stops {delta:+.3f}R better",
                observation=f"Tight(Q1): N={len(q1)}, R={mean_tight:+.3f} | Wide(Q4): N={len(q4)}, R={mean_wide:+.3f}",
                evidence={"n_tight": len(q1), "n_wide": len(q4),
                          "mean_tight": round(mean_tight, 4), "mean_wide": round(mean_wide, 4),
                          "delta": round(delta, 4), "multiple_testing_count": 1},
                confidence="HIGH" if min(len(q1), len(q4)) >= 50 else "MEDIUM",
                sample_size=n,
                trigger_reason=f"|delta|={abs(delta):.3f} >= {cfg.min_geometry_delta}",
                priority=2,
                suggested_experiment_type=ExperimentType.COUNTERFACTUAL_GEOMETRY,
                suggested_hypothesis_category=HypothesisCategory.GEOMETRY_DEFECT,
                suggested_claim="Stop construction materially affects outcome",
                suggested_null="Stop width has no systematic effect on R",
                suggested_patterns=[],
            )
            result = self._screen(trigger)
            if result:
                triggers.append(result)
        return triggers

    # ─── DETECTOR: SYMBOL ANOMALY ─────────────────────────────────────

    def detect_symbol_anomaly(
        self,
        shadows: list[dict],
        *,
        source: str = "research_cycle_runner",
    ) -> list["FindingTrigger"]:
        """Detect symbols with materially different R from the portfolio."""
        import statistics
        from collections import defaultdict
        cfg = self._config
        triggers = []

        by_symbol: dict[str, list] = defaultdict(list)
        for s in shadows:
            sym = s.get("symbol", "")
            r = s.get("r_multiple")
            if sym and r is not None:
                by_symbol[sym].append(r)

        n_symbols_tested = 0
        for sym, vals in by_symbol.items():
            if len(vals) < cfg.min_symbol_n:
                continue
            other = [r for s, rs in by_symbol.items() if s != sym for r in rs]
            if len(other) < cfg.min_symbol_n:
                continue
            n_symbols_tested += 1
            mean_s = statistics.mean(vals)
            mean_other = statistics.mean(other)
            delta = mean_s - mean_other

            if abs(delta) >= cfg.min_symbol_delta:
                trigger = FindingTrigger(
                    finding_id=f"sym_anom_{sym}_{len(vals)}",
                    source=source,
                    category=TriggerCategory.SYMBOL_ANOMALY,
                    title=f"{sym} shows {delta:+.3f}R anomaly vs other symbols",
                    observation=f"{sym}: N={len(vals)}, R={mean_s:+.3f} | Others: N={len(other)}, R={mean_other:+.3f}",
                    evidence={"symbol": sym, "n_symbol": len(vals), "mean_r_symbol": round(mean_s, 4),
                              "n_other": len(other), "mean_r_other": round(mean_other, 4),
                              "delta": round(delta, 4), "multiple_testing_count": n_symbols_tested},
                    confidence="HIGH" if len(vals) >= 50 else "MEDIUM",
                    sample_size=len(vals) + len(other),
                    trigger_reason=f"|delta|={abs(delta):.3f} >= {cfg.min_symbol_delta}",
                    priority=2,
                    suggested_experiment_type=ExperimentType.CONDITIONING_ANALYSIS,
                    suggested_hypothesis_category=HypothesisCategory.OTHER,
                    suggested_claim=f"V10 performance on {sym} is materially different from portfolio",
                    suggested_null=f"Performance does not vary meaningfully across symbols",
                    suggested_patterns=[],
                )
                result = self._screen(trigger)
                if result:
                    triggers.append(result)
        return triggers

    # ─── EXIT EFFICIENCY DETECTOR ─────────────────────────────────────

    def detect_exit_inefficiency(
        self, shadows: list[dict[str, Any]], *, source: str = ""
    ) -> list[FindingTrigger]:
        """
        Detect patterns where MFE capture ratio is poor (timeout with high MFE).

        Flags patterns where mean MFE is high but mean realised R is low,
        indicating the exit policy fails to capture available profit.
        """
        import statistics
        from collections import defaultdict

        by_pattern: dict[str, list[dict]] = defaultdict(list)
        for s in shadows:
            pat = s.get("pattern", "")
            r = s.get("r_multiple")
            mfe = s.get("mfe_r")
            if pat and r is not None and mfe is not None and mfe > 0:
                by_pattern[pat].append({"r": r, "mfe": mfe, "exit": s.get("exit_reason", "")})

        triggers: list[FindingTrigger] = []
        for pat, records in by_pattern.items():
            if len(records) < self._config.min_sample_size:
                continue
            mean_r = statistics.mean(r["r"] for r in records)
            mean_mfe = statistics.mean(r["mfe"] for r in records)
            if mean_mfe <= 0:
                continue
            capture_ratio = mean_r / mean_mfe if mean_mfe > 0 else 0

            # Trigger: MFE exists but R captures less than 30% of it
            if capture_ratio < 0.30 and mean_mfe >= 0.5:
                timeout_pct = sum(1 for r in records if r["exit"] == "max_bars_timeout") / len(records)
                trigger = FindingTrigger(
                    finding_id=f"EXIT-{pat}-{uuid.uuid4().hex[:6]}",
                    source=source,
                    category=TriggerCategory.EXIT_INEFFICIENCY,
                    title=f"{pat}: poor MFE capture ({capture_ratio:.0%} of {mean_mfe:.2f}R)",
                    observation=(
                        f"Pattern {pat} has mean MFE={mean_mfe:.2f}R but realised R={mean_r:+.3f}. "
                        f"Capture ratio={capture_ratio:.0%}. Timeout exits={timeout_pct:.0%}."
                    ),
                    sample_size=len(records),
                    confidence="MEDIUM",
                    suggested_patterns=[pat],
                    trigger_reason=f"capture_ratio={capture_ratio:.2f} < 0.30, MFE={mean_mfe:.2f}",
                    priority=1,
                    suggested_experiment_type=ExperimentType.COUNTERFACTUAL_GEOMETRY,
                    suggested_hypothesis_category=HypothesisCategory.GEOMETRY_DEFECT,
                    suggested_claim=f"Modifying exit policy for {pat} captures more MFE",
                    suggested_null=f"Exit policy does not affect captured R for {pat}",
                )
                result = self._screen(trigger)
                if result:
                    triggers.append(result)
        return triggers

    # ─── GUARD VALUE DETECTOR ─────────────────────────────────────────

    def detect_guard_value(
        self, shadows: list[dict[str, Any]], *, source: str = ""
    ) -> list[FindingTrigger]:
        """
        Compare shadow R for EXECUTE vs NO_TRADE decisions to evaluate guard value.

        If NO_TRADE shadows have higher mean R than EXECUTE shadows, guards may
        be blocking the WRONG opportunities (destroying edge).
        """
        import statistics

        execute_r = [s["r_multiple"] for s in shadows
                     if s.get("v10_action") == "EXECUTE" and s.get("r_multiple") is not None]
        no_trade_r = [s["r_multiple"] for s in shadows
                      if s.get("v10_action") == "NO_TRADE" and s.get("r_multiple") is not None]

        triggers: list[FindingTrigger] = []

        if len(execute_r) < 20 or len(no_trade_r) < 20:
            return triggers

        mean_exec = statistics.mean(execute_r)
        mean_notrade = statistics.mean(no_trade_r)
        delta = mean_notrade - mean_exec

        # Trigger: NO_TRADE shadows outperform EXECUTE shadows by > 0.1R
        if delta > 0.1:
            trigger = FindingTrigger(
                finding_id=f"GUARD-VALUE-{uuid.uuid4().hex[:6]}",
                source=source,
                category=TriggerCategory.GUARD_VALUE_NEGATIVE,
                title=f"Guards may destroy edge: NO_TRADE R ({mean_notrade:+.3f}) > EXECUTE R ({mean_exec:+.3f})",
                observation=(
                    f"Rejected opportunities (N={len(no_trade_r)}) have mean R={mean_notrade:+.3f}. "
                    f"Executed opportunities (N={len(execute_r)}) have mean R={mean_exec:+.3f}. "
                    f"Delta={delta:+.3f}R. Guards may be blocking better trades."
                ),
                sample_size=len(execute_r) + len(no_trade_r),
                confidence="MEDIUM",
                suggested_patterns=[],
                trigger_reason=f"NO_TRADE R - EXECUTE R = {delta:+.3f} > 0.1",
                priority=1,
                suggested_experiment_type=ExperimentType.CONDITIONING_ANALYSIS,
                suggested_hypothesis_category=HypothesisCategory.GUARD_QUALITY,
                suggested_claim="Loosening guard criteria improves overall expectancy",
                suggested_null="Guard criteria do not affect captured edge",
            )
            result = self._screen(trigger)
            if result:
                triggers.append(result)

        return triggers

    # ─── SCREENING ────────────────────────────────────────────────────

    def _screen(self, trigger: FindingTrigger) -> FindingTrigger | None:
        """Apply eligibility rules. Returns trigger if eligible, None if dismissed."""
        trigger.status = TriggerStatus.SCREENED

        # Rule 1: Sample size
        if trigger.sample_size < self._config.min_sample_size:
            trigger.status = TriggerStatus.DISMISSED
            trigger.dismissed_reason = f"Sample size {trigger.sample_size} < {self._config.min_sample_size}"
            self._store(trigger)
            return None

        # Rule 2: Deduplication — check if same finding already active/investigated
        if self._is_duplicate(trigger):
            trigger.status = TriggerStatus.DISMISSED
            trigger.dismissed_reason = "Duplicate of existing trigger or hypothesis"
            self._store(trigger)
            return None

        # Rule 3: Check knowledge map for existing rejection
        if self._already_rejected(trigger):
            trigger.status = TriggerStatus.BLOCKED
            trigger.dismissed_reason = "Finding already investigated and REJECTED"
            self._store(trigger)
            return None

        # Rule 4: Max active triggers
        active = sum(1 for t in self._triggers.values()
                     if t.status in (TriggerStatus.ELIGIBLE, TriggerStatus.REGISTERED, TriggerStatus.INVESTIGATING))
        if active >= self._config.max_active_triggers:
            trigger.status = TriggerStatus.BLOCKED
            trigger.dismissed_reason = f"Max active triggers ({self._config.max_active_triggers}) reached"
            self._store(trigger)
            return None

        # Passed screening
        trigger.status = TriggerStatus.ELIGIBLE

        # Auto-select experiment type from category
        trigger.suggested_experiment_type = _CATEGORY_TO_EXPERIMENT.get(
            trigger.category, ExperimentType.CONDITIONING_ANALYSIS)
        trigger.suggested_hypothesis_category = _CATEGORY_TO_HYPOTHESIS.get(
            trigger.category, HypothesisCategory.OTHER)

        self._store(trigger)
        return trigger

    def _is_duplicate(self, trigger: FindingTrigger) -> bool:
        """
        Check if this finding has already been triggered.
        
        Deduplication rules:
        1. Exact finding_id match → duplicate (always)
        2. Same suggested_patterns + same category → duplicate
           BUT only when BOTH have non-empty suggested_patterns.
           Empty suggested_patterns (non-pattern detectors like SYMBOL_ANOMALY,
           REGIME_ANOMALY, etc.) must NOT match each other — they use
           finding_id uniqueness instead.
        """
        for existing in self._triggers.values():
            if existing.status in (TriggerStatus.DISMISSED, TriggerStatus.BLOCKED):
                continue
            # Rule 1: Exact finding_id match
            if existing.finding_id == trigger.finding_id:
                return True
            # Rule 2: Structural match (only when both have non-empty patterns)
            if (existing.suggested_patterns and trigger.suggested_patterns and
                    existing.suggested_patterns == trigger.suggested_patterns and
                    existing.category == trigger.category):
                return True
        return False

    def _already_rejected(self, trigger: FindingTrigger) -> bool:
        """Check if the knowledge map already has a REJECTED conclusion for this."""
        try:
            km_path = Path("analysis/summaries/research_knowledge.json")
            if not km_path.exists():
                return False
            km = json.loads(km_path.read_text(encoding="utf-8"))
            lifecycle_findings = km.get("lifecycle_findings", {})
            for hid, finding in lifecycle_findings.items():
                if (finding.get("conclusion") == "REJECTED" and
                        any(p in finding.get("title", "") for p in trigger.suggested_patterns)):
                    return True
        except Exception:
            pass
        return False

    # ─── STORAGE ──────────────────────────────────────────────────────

    def _store(self, trigger: FindingTrigger) -> None:
        self._triggers[trigger.trigger_id] = trigger
        self._save()
        self._audit(f"FINDING_{trigger.status.value}", trigger)

    def _save(self) -> None:
        try:
            _TRIGGER_DIR.mkdir(parents=True, exist_ok=True)
            data = {
                "schema_version": 1,
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "triggers": {tid: t.to_dict() for tid, t in self._triggers.items()},
            }
            tmp = _TRIGGER_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
            tmp.replace(_TRIGGER_FILE)
        except Exception:
            pass

    def _load(self) -> None:
        if not _TRIGGER_FILE.exists():
            return
        try:
            data = json.loads(_TRIGGER_FILE.read_text(encoding="utf-8"))
            for tid, t_data in data.get("triggers", {}).items():
                self._triggers[tid] = FindingTrigger.from_dict(t_data)
        except Exception:
            pass

    def _audit(self, event: str, trigger: FindingTrigger) -> None:
        try:
            _TRIGGER_DIR.mkdir(parents=True, exist_ok=True)
            audit_path = _TRIGGER_DIR / "audit_log.jsonl"
            entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": event,
                "trigger_id": trigger.trigger_id,
                "finding_id": trigger.finding_id,
                "category": trigger.category.value,
                "status": trigger.status.value,
                "title": trigger.title[:80],
            }
            fd = os.open(str(audit_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
            try:
                os.write(fd, (json.dumps(entry, separators=(",", ":")) + "\n").encode("utf-8"))
            finally:
                os.close(fd)
        except Exception:
            pass

    # ─── QUERIES ──────────────────────────────────────────────────────

    def all_triggers(self) -> list[FindingTrigger]:
        return list(self._triggers.values())

    def eligible(self) -> list[FindingTrigger]:
        return [t for t in self._triggers.values() if t.status == TriggerStatus.ELIGIBLE]

    def by_status(self, status: TriggerStatus) -> list[FindingTrigger]:
        return [t for t in self._triggers.values() if t.status == status]

    def get_summary(self) -> dict[str, Any]:
        """Summary for Command Center."""
        by_status = {}
        for t in self._triggers.values():
            by_status[t.status.value] = by_status.get(t.status.value, 0) + 1
        eligible = self.eligible()
        return {
            "total_triggers": len(self._triggers),
            "by_status": by_status,
            "eligible_count": len(eligible),
            "top_candidates": [
                {"trigger_id": t.trigger_id, "title": t.title[:50],
                 "category": t.category.value, "sample_size": t.sample_size,
                 "confidence": t.confidence,
                 "experiment_type": t.suggested_experiment_type.value}
                for t in sorted(eligible, key=lambda x: x.priority)[:5]
            ],
        }

    # ─── MARK LIFECYCLE TRANSITIONS ──────────────────────────────────

    def mark_registered(self, trigger_id: str, hypothesis_id: str) -> None:
        """Mark trigger as having created a hypothesis."""
        t = self._triggers.get(trigger_id)
        if t and t.status == TriggerStatus.ELIGIBLE:
            t.status = TriggerStatus.REGISTERED
            t.hypothesis_id = hypothesis_id
            self._save()
            self._audit("FINDING_REGISTERED", t)

    def mark_investigating(self, trigger_id: str) -> None:
        t = self._triggers.get(trigger_id)
        if t:
            t.status = TriggerStatus.INVESTIGATING
            self._save()

    def mark_completed(self, trigger_id: str) -> None:
        t = self._triggers.get(trigger_id)
        if t:
            t.status = TriggerStatus.COMPLETED
            t.resolved_at = datetime.now(timezone.utc).isoformat()
            self._save()
            self._audit("FINDING_COMPLETED", t)
