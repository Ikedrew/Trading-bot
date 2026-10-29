"""
Campaign Engine — Registry of defined campaigns.
"""

from __future__ import annotations

from typing import Any

from research_engine.v10.campaigns.models import ResearchCampaign


# ═══════════════════════════════════════════════════════════════
# CAMPAIGN DEFINITIONS
# ═══════════════════════════════════════════════════════════════

_CAMPAIGNS: list[ResearchCampaign] = [
    ResearchCampaign(
        campaign_id="FX_OPT_V1",
        name="FX Optimisation Investigation",
        objective="Identify causes of negative FX expectancy and prioritise improvement areas.",
        domains=["trade", "decision", "market", "strategy"],
        questions=["E1", "R1", "R2", "D1", "D2", "D3", "M1", "OQ1"],
        filters={"instrument": "FX"},
    ),
    ResearchCampaign(
        campaign_id="RISK_INVESTIGATION_V1",
        name="Risk Management Investigation",
        objective="Determine whether risk management is reducing overall performance.",
        domains=["trade"],
        questions=["E1", "R1", "R2"],
        filters={},
    ),
    ResearchCampaign(
        campaign_id="DECISION_QUALITY_V1",
        name="Decision Quality Investigation",
        objective="Determine whether decision logic selects quality opportunities.",
        domains=["decision", "strategy"],
        questions=["D1", "D2", "D3", "OQ1", "OQ2"],
        filters={},
    ),
    ResearchCampaign(
        campaign_id="STRATEGY_REVIEW_V1",
        name="Strategy Logic Review",
        objective="Determine whether strategy logic produces valid edge.",
        domains=["strategy", "trade"],
        questions=["E1", "E2", "OQ1", "OQ2", "M1"],
        filters={},
    ),
]


# ═══════════════════════════════════════════════════════════════
# REGISTRY
# ═══════════════════════════════════════════════════════════════

class CampaignRegistry:
    """Central registry for research campaigns."""

    def __init__(self):
        self._campaigns = {c.campaign_id: c for c in _CAMPAIGNS}

    def get(self, campaign_id: str) -> ResearchCampaign | None:
        return self._campaigns.get(campaign_id)

    def list_campaigns(self) -> list[ResearchCampaign]:
        return list(self._campaigns.values())

    def register(self, campaign: ResearchCampaign) -> None:
        if campaign.campaign_id in self._campaigns:
            raise ValueError(f"Campaign '{campaign.campaign_id}' already registered")
        self._campaigns[campaign.campaign_id] = campaign

    @property
    def campaign_ids(self) -> list[str]:
        return list(self._campaigns.keys())
