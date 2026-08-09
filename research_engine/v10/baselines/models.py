"""
Baseline Snapshot — Data model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from research_engine.v10.base import timestamp_now


@dataclass
class BaselineSnapshot:
    """
    Complete baseline reference point.

    Captures bot state + configuration + performance + dataset identity
    at a specific moment in time.
    """
    snapshot_id: str
    created_at: str = ""
    bot_version: str = ""
    notes: str = ""

    # Environment
    environment: dict[str, Any] = field(default_factory=dict)

    # Configuration
    configuration: dict[str, Any] = field(default_factory=dict)
    risk_configuration: dict[str, Any] = field(default_factory=dict)
    strategy_configuration: dict[str, Any] = field(default_factory=dict)

    # Performance
    performance_metrics: dict[str, Any] = field(default_factory=dict)

    # Dataset identity
    dataset_metadata: dict[str, Any] = field(default_factory=dict)

    # Research state at time of snapshot
    research_state: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = timestamp_now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "created_at": self.created_at,
            "bot_version": self.bot_version,
            "notes": self.notes,
            "environment": self.environment,
            "configuration": self.configuration,
            "risk_configuration": self.risk_configuration,
            "strategy_configuration": self.strategy_configuration,
            "performance_metrics": self.performance_metrics,
            "dataset_metadata": self.dataset_metadata,
            "research_state": self.research_state,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BaselineSnapshot:
        return cls(
            snapshot_id=data.get("snapshot_id", ""),
            created_at=data.get("created_at", ""),
            bot_version=data.get("bot_version", ""),
            notes=data.get("notes", ""),
            environment=data.get("environment", {}),
            configuration=data.get("configuration", {}),
            risk_configuration=data.get("risk_configuration", {}),
            strategy_configuration=data.get("strategy_configuration", {}),
            performance_metrics=data.get("performance_metrics", {}),
            dataset_metadata=data.get("dataset_metadata", {}),
            research_state=data.get("research_state", {}),
        )
