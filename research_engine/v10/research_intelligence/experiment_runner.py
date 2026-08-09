"""
Research Intelligence — Experiment Runner.

Executes registered research questions against the Research Universe
with optional segmentation filters.

Usage:
    from research_engine.v10.research_intelligence import ExperimentRunner

    runner = ExperimentRunner()
    result = runner.run("E1")
    result = runner.run("R2", filters={"instrument": "FX", "regime": "TRENDING"})
    results = runner.run_all()
"""

from __future__ import annotations

import importlib
import json
import logging
from pathlib import Path
from typing import Any

from research_engine.v10.base import timestamp_now
from research_engine.v10.research_intelligence.models import (
    ExperimentResult,
    QuestionDefinition,
    classify_confidence,
)
from research_engine.v10.research_intelligence.question_registry import QuestionRegistry
from research_engine.v10.segmentation_engine import ResearchSegmenter

logger = logging.getLogger(__name__)

_UNIVERSE_FILE = "data/research/research_universe.jsonl"
_REPORTS_DIR = "reports/research/questions"


class ExperimentRunner:
    """
    Executes research questions against the validated Research Universe.

    Flow:
        1. Load question from registry
        2. Validate required fields exist
        3. Apply segmentation filters
        4. Check minimum sample size
        5. Execute experiment module
        6. Generate standardised result
    """

    def __init__(
        self,
        universe_file: str | None = None,
        reports_dir: str | None = None,
    ):
        self._universe_file = Path(universe_file or _UNIVERSE_FILE)
        self._reports_dir = Path(reports_dir or _REPORTS_DIR)
        self._registry = QuestionRegistry()
        self._segmenter: ResearchSegmenter | None = None

    @property
    def segmenter(self) -> ResearchSegmenter:
        if self._segmenter is None:
            self._segmenter = ResearchSegmenter(universe_file=str(self._universe_file))
        return self._segmenter

    def run(
        self,
        question_id: str,
        filters: dict[str, str] | None = None,
    ) -> ExperimentResult:
        """
        Execute a single research question.

        Args:
            question_id: Registered question ID (e.g., "E1", "R2")
            filters: Optional segmentation filters
                     {"instrument": "FX", "session": "LONDON", "regime": "TRENDING", ...}

        Returns:
            ExperimentResult with result, confidence, recommendation, limitations.
        """
        question = self._registry.get(question_id)
        if not question:
            return ExperimentResult(
                question_id=question_id,
                question_name="UNKNOWN",
                sample_size=0,
                error=f"Question '{question_id}' not found in registry",
            )

        if question.status != "active":
            return ExperimentResult(
                question_id=question.id,
                question_name=question.name,
                sample_size=0,
                error=f"Question '{question.id}' status is '{question.status}' (not active)",
            )

        # Apply filters
        filter_kwargs = filters or {}
        population = self.segmenter.filter(**filter_kwargs)

        if not population:
            return ExperimentResult(
                question_id=question.id,
                question_name=question.name,
                sample_size=0,
                confidence="LOW",
                recommendation="INCONCLUSIVE",
                limitations=["No trades match the specified filters"],
                filters_applied=filter_kwargs,
            )

        # Check minimum sample size
        if len(population) < question.minimum_sample_size:
            return ExperimentResult(
                question_id=question.id,
                question_name=question.name,
                sample_size=len(population),
                confidence="LOW",
                recommendation="INCONCLUSIVE",
                limitations=[
                    f"Sample size {len(population)} below minimum {question.minimum_sample_size}"
                ],
                filters_applied=filter_kwargs,
            )

        # Validate required fields
        missing_fields = _check_required_fields(population, question.required_fields)
        limitations = []
        if missing_fields:
            limitations.append(f"Missing fields in some trades: {missing_fields}")

        # Execute experiment
        try:
            exp_result = _execute_experiment(question, population)
        except Exception as exc:
            return ExperimentResult(
                question_id=question.id,
                question_name=question.name,
                sample_size=len(population),
                error=f"{type(exc).__name__}: {exc}",
                filters_applied=filter_kwargs,
            )

        # Build result
        confidence = classify_confidence(len(population))
        conclusion = exp_result.get("conclusion", "INCONCLUSIVE")
        recommendation = _map_conclusion_to_recommendation(conclusion)

        result = ExperimentResult(
            question_id=question.id,
            question_name=question.name,
            sample_size=len(population),
            result=exp_result.get("metrics", exp_result),
            confidence=confidence,
            recommendation=recommendation,
            limitations=limitations + exp_result.get("limitations", []),
            filters_applied=filter_kwargs,
            segment_population=_describe_population(filter_kwargs, len(population)),
        )

        # Save report
        self._save_report(result)
        return result

    def run_all(self, filters: dict[str, str] | None = None) -> list[ExperimentResult]:
        """Run all active questions."""
        results = []
        for q in self._registry.list_active():
            result = self.run(q.id, filters=filters)
            results.append(result)
        return results

    def run_with_governance(
        self,
        question_id: str,
        filters: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        Execute a question and apply governance validation.

        Returns:
            {"result": ExperimentResult.to_dict(), "governance": ResearchFinding.to_dict()}
        """
        from research_engine.v10.research_governance import validate_finding

        exp_result = self.run(question_id, filters=filters)
        finding = validate_finding(exp_result)

        return {
            "result": exp_result.to_dict(),
            "governance": finding.to_dict(),
        }

    def run_all_with_governance(
        self,
        filters: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """Run all active questions with governance validation."""
        from research_engine.v10.research_governance import validate_finding, rank_findings

        results = self.run_all(filters=filters)
        findings = [validate_finding(r) for r in results]
        ranked = rank_findings(findings)

        return [
            {
                "result": results[i].to_dict(),
                "governance": ranked_f.to_dict(),
            }
            for i, ranked_f in enumerate(
                sorted(ranked, key=lambda f: f.question_id)  # Re-sort by ID for stable pairing
            )
        ]

    def run_category(self, category: str, filters: dict[str, str] | None = None) -> list[ExperimentResult]:
        """Run all active questions in a category."""
        results = []
        for q in self._registry.list_by_category(category):
            result = self.run(q.id, filters=filters)
            results.append(result)
        return results

    @property
    def registry(self) -> QuestionRegistry:
        return self._registry

    def _save_report(self, result: ExperimentResult) -> None:
        """Save experiment result as JSON. Uses S3 in Lambda mode, local filesystem otherwise."""
        import os

        content = json.dumps(result.to_dict(), indent=2, default=str)

        suffix = ""
        if result.filters_applied:
            parts = [f"{k}_{v}" for k, v in sorted(result.filters_applied.items())]
            suffix = "_" + "_".join(parts)
        filename = f"questions/{result.question_id}{suffix}.json"

        if os.environ.get("RESEARCH_STORAGE") == "s3":
            # S3 mode — write via ResearchStorage (no local filesystem required)
            from research_engine.v10.operations.storage import ResearchStorage
            storage = ResearchStorage(backend="s3")
            storage.save_report(content, filename)
        else:
            # Local mode — write to filesystem as before
            self._reports_dir.mkdir(parents=True, exist_ok=True)
            path = self._reports_dir / f"{result.question_id}{suffix}.json"
            path.write_text(content, encoding="utf-8")


# ═══════════════════════════════════════════════════════════════
# INTERNAL
# ═══════════════════════════════════════════════════════════════

def _execute_experiment(question: QuestionDefinition, population: list[dict]) -> dict[str, Any]:
    """
    Import and execute the experiment module.

    The existing V10 experiments accept (view, trades) — we adapt by
    flattening the universe events back to the trade format expected
    by the legacy experiment modules.
    """
    if not question.experiment_module:
        return {"conclusion": "INCONCLUSIVE", "error": "No experiment module configured"}

    module = importlib.import_module(question.experiment_module)

    # Flatten universe events to trade dicts for legacy experiment compat
    flat_trades = [_flatten_event(e) for e in population]

    # Legacy experiments accept: run(view=DatasetView, trades=list)
    from research_engine.v10.dataset import DatasetView
    result = module.run(view=DatasetView.FULL, trades=flat_trades)
    return result


def _flatten_event(event: dict) -> dict:
    """Flatten a structured universe event back to a flat trade dict for legacy experiments."""
    ex = event.get("execution", {})
    dec = event.get("decision", {})
    mkt = event.get("market", {})
    strat = event.get("strategy", {})

    return {
        "trade_id": event.get("trade_id", ""),
        "position_ticket": ex.get("ticket", 0),
        "symbol": ex.get("symbol", ""),
        "direction": ex.get("direction", ""),
        "entry_time": ex.get("entry_time", 0),
        "exit_time": ex.get("exit_time", 0),
        "entry_price": ex.get("entry_price", 0),
        "exit_price": ex.get("exit_price", 0),
        "stop_loss": ex.get("stop_loss", 0),
        "take_profit": ex.get("take_profit", 0),
        "broker_pnl": ex.get("gross_profit", 0),
        "final_pnl": ex.get("net_realised_pnl", 0),
        "commission": ex.get("commission", 0),
        "swap": ex.get("swap", 0),
        "realised_r": ex.get("r_multiple", 0),
        "volume": ex.get("volume", 0),
        "duration_seconds": ex.get("duration_seconds", 0),
        "exit_reason_validated": ex.get("exit_reason", ""),
        "rr_ratio": abs(
            (ex.get("take_profit", 0) - ex.get("entry_price", 0)) /
            (ex.get("entry_price", 0) - ex.get("stop_loss", 0))
        ) if ex.get("entry_price", 0) != ex.get("stop_loss", 0) else 0,
        # Decision fields
        "score": dec.get("score", 0),
        "strategy": dec.get("strategy", ""),
        "dt_score_strategy": dec.get("score", 0),
        "dt_strategy": dec.get("strategy", ""),
        "dt_v10_strategy_family": strat.get("family", ""),
        "dt_components": dec.get("components", {}),
        "dt_ev": dec.get("ev"),
        "dt_p_success": dec.get("p_success"),
        "dt_confirmation_score": dec.get("confidence", 0),
        # Market fields
        "regime": mkt.get("regime", ""),
        "dt_v10_regime": mkt.get("regime", ""),
        "dt_h1_direction": mkt.get("trend_state", ""),
        # Strategy fields
        "pattern": strat.get("pattern", ""),
        "dt_pattern": strat.get("pattern", ""),
        "dt_opportunity_quality": strat.get("opportunity_quality", 0),
        "instrument_class": _infer_instrument_class(ex.get("symbol", "")),
    }


def _infer_instrument_class(symbol: str) -> str:
    """Infer instrument class from symbol."""
    fx = {"EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "USDJPY"}
    idx = {"US500", "NAS100", "US30", "GER40"}
    if symbol in fx:
        return "FX_MAJOR" if "JPY" not in symbol else "FX_JPY"
    if symbol in idx:
        return "INDEX"
    return "COMMODITY"


def _check_required_fields(population: list[dict], required: list[str]) -> list[str]:
    """Check which required fields are missing from the population."""
    if not required or not population:
        return []

    missing = []
    sample = population[0]
    for field_path in required:
        parts = field_path.split(".")
        val = sample
        for part in parts:
            if isinstance(val, dict):
                val = val.get(part)
            else:
                val = None
                break
        if val is None:
            missing.append(field_path)
    return missing


def _map_conclusion_to_recommendation(conclusion: str) -> str:
    """Map experiment conclusion strings to standardised recommendations."""
    positive = {"POSITIVE_EXPECTANCY", "SCORE_IS_PREDICTIVE", "RISK_MODEL_EFFECTIVE",
                "STOP_MODEL_EFFECTIVE", "EV_CALIBRATED", "OPPORTUNITY_LAYER_PREDICTIVE",
                "REGIMES_SHOW_DIFFERENT_EXPECTANCY"}
    negative = {"NEGATIVE_EXPECTANCY", "SCORE_HAS_NO_PREDICTIVE_POWER",
                "STOPS_NEED_REVIEW", "STOP_TOO_TIGHT", "EV_NOT_PREDICTIVE",
                "DECISION_LAYER_FAILURE"}

    c = conclusion.upper()
    if c in positive:
        return "SUPPORTED"
    if c in negative:
        return "REJECTED"
    return "INCONCLUSIVE"


def _describe_population(filters: dict[str, str], count: int) -> str:
    """Create human-readable population description."""
    if not filters:
        return f"FULL ({count} events)"
    parts = [f"{k}={v}" for k, v in sorted(filters.items())]
    return f"{' + '.join(parts)} ({count} events)"
