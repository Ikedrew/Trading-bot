"""
V10 Research Intelligence Engine.

Manages research questions, experiments, and evidence-based analysis
on top of the validated Research Universe.

Usage:
    from research_engine.v10.research_intelligence import ExperimentRunner

    runner = ExperimentRunner()
    result = runner.run("E1")
    result = runner.run("R2", filters={"instrument": "FX", "regime": "TRENDING"})
"""

from research_engine.v10.research_intelligence.experiment_runner import ExperimentRunner
from research_engine.v10.research_intelligence.question_registry import QuestionRegistry

__all__ = ["ExperimentRunner", "QuestionRegistry"]
