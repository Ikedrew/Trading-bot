"""
V10 Validation Lab.

Controlled testing environment where optimisation candidates are evaluated
against a frozen baseline using historical research data.

Does NOT deploy changes. Does NOT modify the live bot.
Produces validated evidence for human decision-making.

Usage:
    from research_engine.v10.validation_lab import ValidationRunner

    runner = ValidationRunner()
    result = runner.validate(candidate_id="V10.1_RISK_TEST")
"""

from research_engine.v10.validation_lab.models import ValidationRun, ValidationDecision
from research_engine.v10.validation_lab.validation_runner import ValidationRunner

__all__ = ["ValidationRun", "ValidationDecision", "ValidationRunner"]
