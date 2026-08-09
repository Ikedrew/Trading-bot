"""
V10 Shadow Optimisation Engine.

Observation-only parallel testing of optimisation candidates alongside the live bot.
The shadow engine NEVER places orders, modifies positions, or affects execution.

Usage:
    from research_engine.v10.shadow import ShadowRunner, ShadowRegistry

    runner = ShadowRunner()
    runner.start_shadow("V10.1_STOP_ATR_2.0")
    runner.process_opportunity(opportunity_event)
    report = runner.generate_report("V10.1_STOP_ATR_2.0")
"""

from research_engine.v10.shadow.shadow_runner import ShadowRunner
from research_engine.v10.shadow.shadow_registry import ShadowRegistry
from research_engine.v10.shadow.models import ShadowCandidate, ShadowComparison

__all__ = ["ShadowRunner", "ShadowRegistry", "ShadowCandidate", "ShadowComparison"]
