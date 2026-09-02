"""
Risk Universe Builder.

Extracts risk-evaluation observations from decision trace logs.

Source: logs/decision_trace/**/*.jsonl (v10_risk sub-object)

Grain: 1 record = 1 risk evaluation event (the moment the risk-control
mechanism assessed whether a proposed trade satisfies risk constraints).

This universe owns:
    - Risk-control result (approved / blocked)
    - Risk-control reason
    - Risk percentage calculated
    - Position size authorised
    - Risk evaluation identity and timing

This universe does NOT own:
    - The final EXECUTE / NO_TRADE decision (Decision)
    - Strategy intent or selection (Strategy)
    - Market state interpretation (Market)
    - Mechanical execution (Execution)
    - Realised economic outcome (Outcome)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from research_engine.v10.universes.base import UniverseBuilder
from research_engine.v10.universes.models import Population, Universe

logger = logging.getLogger(__name__)

_DEFAULT_DIR = Path("logs/decision_trace")


class RiskUniverseBuilder(UniverseBuilder):
    """
    Builds the Risk Universe from decision trace logs.

    Extracts the v10_risk sub-object from each decision trace record
    that reached the risk evaluation stage.

    Records that never reached risk evaluation (rejected at earlier stages)
    are excluded because no risk assessment was performed.
    """

    def __init__(self, source_dir: Path | str | None = None):
        super().__init__()
        self._source_dir = Path(source_dir) if source_dir else _DEFAULT_DIR
        self._raw: list[dict[str, Any]] = []

    @property
    def universe_type(self) -> Universe:
        return Universe.RISK

    def load(self) -> int:
        self._raw = self._load_jsonl_directory(self._source_dir)
        logger.info(
            f"[RISK] Loaded {len(self._raw)} raw decision trace records "
            f"from {self._source_dir}"
        )
        return len(self._raw)

    def build(self) -> list[dict[str, Any]]:
        if not self._raw:
            self.load()

        records = []
        excluded_no_entity_id = 0
        excluded_no_risk_data = 0
        excluded_not_reached_risk = 0

        for raw in self._raw:
            # Skip RISK_REJECTION runtime-guard rejection records.
            if raw.get("event_type") == "RISK_REJECTION":
                continue
            entity_id = raw.get("entity_id", "")
            if not entity_id:
                excluded_no_entity_id += 1
                continue

            # Only include records that actually reached the risk evaluation stage
            risk = raw.get("v10_risk", {}) or {}
            if not risk:
                # No v10_risk sub-object means risk evaluation was never performed
                excluded_no_risk_data += 1
                continue

            # Check that the record has meaningful risk evaluation evidence
            # (at minimum, an approval status must be present)
            if risk.get("approved") is None:
                excluded_not_reached_risk += 1
                continue

            record = self._normalise(raw, risk)
            if record:
                records.append(record)

        total_excluded = (
            excluded_no_entity_id + excluded_no_risk_data + excluded_not_reached_risk
        )
        exclusions = {
            "total": total_excluded,
            "reasons": {
                "missing_entity_id": excluded_no_entity_id,
                "no_v10_risk_data": excluded_no_risk_data,
                "risk_not_reached": excluded_not_reached_risk,
            },
            "source_records": len(self._raw),
            "included_records": len(records),
        }

        self._records = records
        self._built = True

        source_files = tuple(
            str(p) for p in sorted(self._source_dir.rglob("*.jsonl"))
        ) if self._source_dir.exists() else ()

        self._metadata = self._generate_metadata(
            records=records,
            source_files=source_files[:5] + ("...",) if len(source_files) > 5 else source_files,
            populations=(
                Population.ALL_RISK_EVALUATIONS.value,
                Population.RISK_APPROVED.value,
                Population.RISK_BLOCKED.value,
            ),
            exclusions=exclusions,
        )
        logger.info(
            f"[RISK] Built {len(records)} normalised records "
            f"(excluded {total_excluded}: {excluded_no_entity_id} no entity_id, "
            f"{excluded_no_risk_data} no risk data, "
            f"{excluded_not_reached_risk} risk not reached)"
        )
        return records

    def get_population(self, population: Population) -> list[dict[str, Any]]:
        records = self.records

        if population == Population.ALL_RISK_EVALUATIONS:
            return records
        elif population == Population.RISK_APPROVED:
            return [r for r in records if r.get("risk_control_result") == "APPROVED"]
        elif population == Population.RISK_BLOCKED:
            return [r for r in records if r.get("risk_control_result") == "BLOCKED"]

        logger.warning(f"[RISK] Unknown population: {population.value}")
        return []

    def _normalise(
        self, raw: dict[str, Any], risk: dict[str, Any]
    ) -> dict[str, Any] | None:
        """
        Extract risk-evaluation evidence from a decision trace record.

        Produces a flat record containing only risk-owned fields + identity.
        """
        approved = risk.get("approved")

        # Canonical risk-control result
        if approved is True:
            control_result = "APPROVED"
        elif approved is False:
            control_result = "BLOCKED"
        else:
            control_result = "UNKNOWN"

        return {
            # Identity & join keys
            "entity_id": raw.get("entity_id", ""),
            "correlation_id": raw.get("correlation_id", ""),
            "symbol": raw.get("symbol", ""),
            "cycle_id": raw.get("cycle_id"),
            "timestamp_utc": raw.get("timestamp_utc", ""),
            # Risk-control assessment (Risk-owned)
            "risk_control_result": control_result,
            "risk_control_reason": risk.get("rejection_reason", ""),
            "risk_percentage": risk.get("risk_percentage"),
            "position_size": risk.get("position_size"),
            "stop_distance_pips": risk.get("stop_distance_pips"),
            "risk_reward_ratio": risk.get("risk_reward_ratio"),
            "max_position_check": risk.get("max_position_check"),
            "exposure_check": risk.get("exposure_check"),
            # r_multiple placeholder (populated via outcome enrichment)
            "r_multiple": None,
        }
