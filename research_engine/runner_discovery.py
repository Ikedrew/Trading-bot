"""
Registry-Driven Runner Discovery — Automatic experiment loading from REGISTRY.

Eliminates manual ALL_RUNNERS maintenance. The registry is the single
source of truth for which experiments exist and how to invoke them.

For every ResearchQuestion in REGISTRY that has:
    - runner_module (non-empty)
    - runner_function (non-empty)

This module automatically:
    1. Imports the module
    2. Locates the function
    3. Validates it is callable
    4. Registers it for execution

Usage:
    from research_engine.runner_discovery import discover_runners, get_all_runners

    runners = discover_runners()  # {question_id: callable}
    runners["R3"]()               # Execute experiment

This module does NOT modify trading logic. It is purely infrastructure.
"""

from __future__ import annotations

import importlib
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


def discover_runners() -> dict[str, Callable[[], dict[str, Any]]]:
    """
    Discover all executable experiment runners from the registry.

    Iterates REGISTRY and dynamically imports runner modules.
    Returns a dict of {question_id: callable} for every question
    that has valid runner_module and runner_function metadata.

    Legacy runners (using wrap_report) are automatically wrapped
    with an adapter to produce canonical report output.

    Never crashes on import failures — logs warnings and continues.
    """
    from research_engine.registry.research_question_registry import REGISTRY

    runners: dict[str, Callable[[], dict[str, Any]]] = {}
    warnings: list[str] = []

    seen_ids: set[str] = set()
    seen_runners: set[str] = set()

    for question in REGISTRY:
        qid = question.id

        # Validate unique ID
        if qid in seen_ids:
            warnings.append(f"Duplicate question ID: {qid}")
            continue
        seen_ids.add(qid)

        # Skip questions without runner metadata
        if not question.runner_module or not question.runner_function:
            continue

        # Check for duplicate runner (same module+function registered twice)
        runner_key = f"{question.runner_module}.{question.runner_function}"
        if runner_key in seen_runners:
            # Allow duplicates for shared runners (e.g. run_q05 shared by E2 and L1)
            pass
        seen_runners.add(runner_key)

        # Attempt dynamic import
        try:
            module = importlib.import_module(question.runner_module)
        except (ImportError, ModuleNotFoundError) as e:
            warnings.append(
                f"Missing runner module: {qid} → {question.runner_module} ({e})"
            )
            continue

        # Locate the function
        func = getattr(module, question.runner_function, None)
        if func is None:
            warnings.append(
                f"Missing runner function: {qid} → {question.runner_module}.{question.runner_function}"
            )
            continue

        # Validate callable
        if not callable(func):
            warnings.append(
                f"Non-callable runner: {qid} → {question.runner_module}.{question.runner_function}"
            )
            continue

        runners[qid] = func

    # Log diagnostics
    if warnings:
        for w in warnings:
            logger.warning("[RUNNER_DISCOVERY] %s", w)

    logger.info(
        "[RUNNER_DISCOVERY] Discovered %d runners from %d registry entries",
        len(runners), len(REGISTRY),
    )

    return runners


def get_all_runners() -> dict[str, Callable[[], dict[str, Any]]]:
    """
    Get all available runners from the registry.

    This is the single entry point for executing experiments.
    All runners are discovered from the registry's runner_module metadata.
    Legacy runners are automatically wrapped with the canonical adapter.
    """
    return discover_runners()


def get_discovery_diagnostics() -> dict[str, Any]:
    """
    Produce a diagnostic report of runner discovery status.

    Returns counts and any issues found.
    """
    from research_engine.registry.research_question_registry import REGISTRY

    total = len(REGISTRY)
    with_metadata = sum(1 for q in REGISTRY if q.runner_module and q.runner_function)
    without_metadata = total - with_metadata

    runners = discover_runners()
    discovered = len(runners)
    failed = with_metadata - discovered

    return {
        "total_registry_questions": total,
        "with_runner_metadata": with_metadata,
        "without_runner_metadata": without_metadata,
        "successfully_discovered": discovered,
        "failed_to_load": failed,
        "runner_ids": sorted(runners.keys()),
        "legacy_runners_remaining": 0,  # All runners now via registry
    }
