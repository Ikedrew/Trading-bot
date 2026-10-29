"""
Research Runner Framework.

Generic question execution framework + reusable analysis primitives.
Questions are declarative; the runner resolves and orchestrates.

Also re-exports run_experiment from the legacy runner for backward compatibility.
"""

# Backward compatibility: the old runner.py is now at runner/legacy_runner.py
# but the v10 __init__.py expects `from research_engine.v10.runner import run_experiment`
try:
    from research_engine.v10.runner._legacy import run_experiment, list_experiments
except ImportError:
    # If legacy module not available, provide stubs
    def run_experiment(*args, **kwargs):  # type: ignore
        return {"error": "Legacy runner not available"}

    def list_experiments():  # type: ignore
        return []

__all__ = ["run_experiment", "list_experiments"]
