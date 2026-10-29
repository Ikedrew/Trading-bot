"""
V10 Research Module — Permanent, reusable research experiment framework.

Architecture:
    research_engine/v10/
        __init__.py         - Module entry point + experiment runner
        dataset.py          - Dataset loading with views (FULL/FX_ONLY/INDEX_ONLY/CFD_ONLY)
        base.py             - Base class and utilities for experiments
        e1_expectancy.py    - V10-E1: True System Expectancy
        (future modules follow same pattern)

Usage:
    from research_engine.v10 import run_experiment
    result = run_experiment("E1", view="FX_ONLY")

Lambda-ready:
    Each experiment accepts a dataset view string and returns a structured dict.
    Lambda wrapper calls: run_experiment(event["research"], event["view"])
"""

from research_engine.v10.runner import run_experiment

__all__ = ["run_experiment"]
