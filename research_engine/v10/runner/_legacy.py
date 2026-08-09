"""
V10 Research Runner — Dispatches research experiments by ID and view.

Usage:
    from research_engine.v10 import run_experiment
    result = run_experiment("E1", view="FX_ONLY")

Lambda-compatible interface:
    run_experiment(research_id, view) → dict

All experiments return a structured dict with at minimum:
    research_id, title, conclusion, metrics, markdown
"""

from __future__ import annotations

from typing import Any

from research_engine.v10.dataset import DatasetView


# Registry of available experiments
_EXPERIMENT_REGISTRY: dict[str, str] = {
    "E1": "research_engine.v10.e1_expectancy",
    "E2": "research_engine.v10.e2_pattern",
    "M1": "research_engine.v10.m1_regime",
    "D1": "research_engine.v10.d1_scoring",
    "D2": "research_engine.v10.d2_ev_calibration",
    "D3": "research_engine.v10.d3_threshold_effectiveness",
    "OQ1": "research_engine.v10.oq1_opportunity_quality",
    "R1": "research_engine.v10.r1_risk_model",
    "OQ2": "research_engine.v10.oq2_opportunity_failure",
    "R2": "research_engine.v10.r2_stop_effectiveness",
}


def run_experiment(research_id: str, view: str = "FULL", **kwargs: Any) -> dict[str, Any]:
    """
    Run a V10 research experiment by ID.

    Args:
        research_id: Experiment identifier (e.g., "E1", "R1")
        view: Dataset view (FULL, FX_ONLY, INDEX_ONLY, CFD_ONLY, NORMALISED)
        **kwargs: Additional arguments passed to the experiment runner

    Returns:
        Structured report dict.
    """
    # Resolve view
    try:
        dataset_view = DatasetView(view.upper())
    except ValueError:
        return {"error": f"Invalid view: {view}. Valid: {[v.value for v in DatasetView]}"}

    # Resolve experiment
    module_path = _EXPERIMENT_REGISTRY.get(research_id.upper())
    if not module_path:
        return {
            "error": f"Unknown research_id: {research_id}",
            "available": list(_EXPERIMENT_REGISTRY.keys()),
        }

    # Import and run
    try:
        import importlib
        module = importlib.import_module(module_path)
        result = module.run(view=dataset_view, **kwargs)
        return result
    except Exception as exc:
        return {
            "error": f"Experiment {research_id} failed: {type(exc).__name__}: {exc}",
            "research_id": research_id,
            "view": view,
        }


def list_experiments() -> list[dict[str, str]]:
    """List all available experiments."""
    return [{"id": k, "module": v} for k, v in _EXPERIMENT_REGISTRY.items()]
