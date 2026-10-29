"""
Research Command Centre — Unified Research State Aggregation.

Single place to understand the current state of the trading system research.
Answers: What does the system know, what can it prove, what is missing,
and what should happen next?

This package is PURELY REPORTING. It does NOT modify trading logic.

Usage:
    from research_engine.command_center import generate_command_report, print_report

    report = generate_command_report()
    print_report(report)

CLI:
    python -m research_engine.command_center
"""

from research_engine.command_center.research_command_center import (
    generate_command_report,
    print_report,
)
from research_engine.command_center.command_models import ResearchCommandReport

__all__ = ["generate_command_report", "print_report", "ResearchCommandReport"]
