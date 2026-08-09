"""
V10 Research Operations — Production execution layer.

Routes research actions to the existing infrastructure without duplicating logic.

Usage:
    from research_engine.v10.operations import ResearchRouter
    router = ResearchRouter()
    result = router.execute({"action": "run_campaign", "campaign_id": "FX_OPT_V1"})

CLI:
    python -m research_engine.v10.operations run_campaign FX_OPT_V1
    python -m research_engine.v10.operations run_question R2 --instrument FX
    python -m research_engine.v10.operations dashboard
"""

from research_engine.v10.operations.router import ResearchRouter

__all__ = ["ResearchRouter"]
