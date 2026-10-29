"""
V10 Research Campaign Engine.

Transforms individual research questions into repeatable multi-domain investigations.

Usage:
    from research_engine.v10.campaigns import CampaignRunner

    runner = CampaignRunner()
    result = runner.run_campaign("FX_OPT_V1")
    result = runner.run_campaign("RISK_INVESTIGATION_V1", filters={"instrument": "FX"})
"""

from research_engine.v10.campaigns.campaign_runner import CampaignRunner
from research_engine.v10.campaigns.campaign_registry import CampaignRegistry
from research_engine.v10.campaigns.models import ResearchCampaign, CampaignResult

__all__ = ["CampaignRunner", "CampaignRegistry", "ResearchCampaign", "CampaignResult"]
