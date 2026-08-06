"""V10 Research Runner — Dispatches experiments by ID and view."""
from __future__ import annotations
from typing import Any
from research_engine.v10.dataset import DatasetView

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
    """Run a V10 research experiment by ID."""
    try:
        dataset_view = DatasetView(view.upper())
    except ValueError:
        return {"error": f"Invalid view: {view}. Valid: {[v.value for v in DatasetView]}"}

    module_path = _EXPERIMENT_REGISTRY.get(research_id.upper())
    if not module_path:
        return {"error": f"Unknown research_id: {research_id}", "available": list(_EXPERIMENT_REGISTRY.keys())}

    try:
        import importlib
        module = importlib.import_module(module_path)
        return module.run(view=dataset_view, **kwargs)
    except Exception as exc:
        return {"error": f"Experiment {research_id} failed: {type(exc).__name__}: {exc}"}

def list_experiments() -> list[dict[str, str]]:
    return [{"id": k, "module": v} for k, v in _EXPERIMENT_REGISTRY.items()]
