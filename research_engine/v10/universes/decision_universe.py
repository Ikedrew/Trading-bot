"""
Decision Universe Builder.

Loads all decision_trace JSONL files (EXECUTE + NO_TRADE) and produces
a normalised decision-level population.

Grain: 1 record = 1 decision event (the moment the pipeline decided to
EXECUTE or NO_TRADE).

Sources:
    - logs/decision_trace/<SYMBOL>/*.jsonl (V2 schema with v10_* sub-objects)

This universe enables questions about:
    - Decision quality and scoring
    - EV calibration
    - Rejection stage analysis
    - Opportunity quality prediction
    - Risk gate effectiveness
    - Missed opportunity counterfactuals
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from research_engine.v10.universes.base import UniverseBuilder
from research_engine.v10.universes.models import Population, Universe

logger = logging.getLogger(__name__)

_DATASET = "decision_trace"


class DecisionUniverseBuilder(UniverseBuilder):
    """
    Builds the Decision Universe from the decision_trace dataset (S3).

    Loads all V2-schema decision traces across all symbols and normalises
    them into flat records with semantic field names. Source of truth is S3
    (dataset ``decision_trace``), resolved via the shared S3 access layer.
    """

    def __init__(self, symbol: str | None = None):
        super().__init__()
        self._symbol = symbol
        self._raw: list[dict[str, Any]] = []

    @property
    def universe_type(self) -> Universe:
        return Universe.DECISION

    def load(self) -> int:
        self._raw = self._load_dataset(_DATASET, symbol=self._symbol)
        logger.info(
            f"[DECISION] Loaded {len(self._raw)} raw decision records "
            f"from S3 dataset '{_DATASET}'"
        )
        return len(self._raw)

    def build(self) -> list[dict[str, Any]]:
        if not self._raw:
            self.load()

        records = []
        excluded_missing_entity_id = 0
        excluded_missing_action = 0

        for raw in self._raw:
            # Skip RISK_REJECTION event records appended into the decision_trace
            # dataset — they are runtime-guard rejections, not full decision records.
            if raw.get("event_type") == "RISK_REJECTION":
                continue
            entity_id = raw.get("entity_id", "")
            action = raw.get("action", "")
            if not entity_id:
                excluded_missing_entity_id += 1
                continue
            if not action:
                excluded_missing_action += 1
                continue

            record = self._normalise(raw)
            if record:
                records.append(record)

        total_excluded = excluded_missing_entity_id + excluded_missing_action
        exclusions = {
            "total": total_excluded,
            "reasons": {
                "missing_entity_id": excluded_missing_entity_id,
                "missing_action": excluded_missing_action,
            },
            "source_records": len(self._raw),
            "included_records": len(records),
        }

        self._records = records
        self._built = True

        # Source is the S3 dataset (resolved via the shared access layer).
        source_files = (f"s3:{_DATASET}",)

        self._metadata = self._generate_metadata(
            records=records,
            source_files=source_files,
            populations=(
                Population.ALL_DECISIONS.value,
                Population.EXECUTE_DECISIONS.value,
                Population.NO_TRADE_DECISIONS.value,
                Population.REJECTED_AT_OPPORTUNITY.value,
                Population.REJECTED_AT_STRATEGY.value,
                Population.REJECTED_AT_ENTRY.value,
                Population.REJECTED_AT_RISK.value,
                Population.REJECTED_AT_EXECUTION.value,
                Population.HIGH_SCORE_DECISIONS.value,
                Population.LOW_SCORE_DECISIONS.value,
            ),
            exclusions=exclusions,
        )
        logger.info(
            f"[DECISION] Built {len(records)} normalised records "
            f"(excluded {total_excluded}: {excluded_missing_entity_id} missing entity_id, "
            f"{excluded_missing_action} missing action)"
        )
        return records

    def get_population(self, population: Population) -> list[dict[str, Any]]:
        records = self.records

        if population == Population.ALL_DECISIONS:
            return records
        elif population == Population.EXECUTE_DECISIONS:
            return [r for r in records if r.get("action") == "EXECUTE"]
        elif population == Population.NO_TRADE_DECISIONS:
            return [r for r in records if r.get("action") == "NO_TRADE"]
        elif population == Population.REJECTED_AT_OPPORTUNITY:
            return [
                r for r in records
                if r.get("action") == "NO_TRADE"
                and "opportunity" in (r.get("terminal_reason") or "").lower()
            ]
        elif population == Population.REJECTED_AT_STRATEGY:
            return [
                r for r in records
                if r.get("action") == "NO_TRADE"
                and "strategy" in (r.get("terminal_reason") or "").lower()
            ]
        elif population == Population.REJECTED_AT_ENTRY:
            return [
                r for r in records
                if r.get("action") == "NO_TRADE"
                and "entry" in (r.get("terminal_reason") or "").lower()
            ]
        elif population == Population.REJECTED_AT_RISK:
            return [
                r for r in records
                if r.get("action") == "NO_TRADE"
                and "risk" in (r.get("terminal_reason") or "").lower()
            ]
        elif population == Population.REJECTED_AT_EXECUTION:
            return [
                r for r in records
                if r.get("action") == "NO_TRADE"
                and "exec" in (r.get("terminal_reason") or "").lower()
                and "entry" not in (r.get("terminal_reason") or "").lower()
            ]
        elif population == Population.HIGH_SCORE_DECISIONS:
            return [
                r for r in records
                if (r.get("score") or 0) >= 70
            ]
        elif population == Population.LOW_SCORE_DECISIONS:
            return [
                r for r in records
                if (r.get("score") or 0) < 50 and r.get("score") is not None
            ]

        logger.warning(f"[DECISION] Unknown population: {population.value}")
        return []

    def _normalise(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        """Flatten a V2-schema decision trace into semantic fields."""
        entity_id = raw.get("entity_id", "")
        action = raw.get("action", "")
        if not entity_id or not action:
            return None

        # Extract v10 sub-objects
        opp = raw.get("v10_opportunity", {}) or {}
        strat = raw.get("v10_strategy", {}) or {}
        risk = raw.get("v10_risk", {}) or {}
        entry = raw.get("v10_entry", {}) or {}
        mkt = raw.get("v10_market_state", {}) or {}
        regime_obj = mkt.get("regime", {}) or {}

        # Compute score: prefer score_strategy, fall back to score_neutral
        score = raw.get("score_strategy") or raw.get("score_neutral")

        return {
            # Identity & join keys
            "entity_id": entity_id,
            "correlation_id": raw.get("correlation_id", ""),
            "decision_id": raw.get("decision_id", ""),
            "symbol": raw.get("symbol", ""),
            "cycle_id": raw.get("cycle_id"),
            "timestamp_utc": raw.get("timestamp_utc", ""),
            # Decision outcome
            "action": action,
            "terminal_stage": raw.get("terminal_stage", ""),
            "terminal_reason": raw.get("terminal_reason", ""),
            "stages_reached": raw.get("stages_reached", []),
            "stages_passed": raw.get("stages_passed", []),
            # Scoring
            "score": score,
            "score_neutral": raw.get("score_neutral"),
            "score_strategy": raw.get("score_strategy"),
            "score_delta": raw.get("score_delta"),
            "components": raw.get("components", {}),
            "weakest_component": raw.get("weakest_component"),
            "threshold_gap": raw.get("threshold_gap"),
            # EV and probability
            "ev": raw.get("ev"),
            "ev_positive": raw.get("ev_positive"),
            "p_success": raw.get("p_success"),
            "rr_effective": raw.get("rr_effective"),
            # Opportunity quality (from v10_opportunity)
            "opportunity_quality": opp.get("overall_quality"),
            "opportunity_state": opp.get("state", ""),
            "location_score": opp.get("location_score"),
            "structure_score": opp.get("structure_score"),
            "behaviour_score": opp.get("behaviour_score"),
            "formation_score": opp.get("formation_score"),
            # Strategy (from v10_strategy)
            "strategy_family": strat.get("family", ""),
            "strategy_confidence": strat.get("confidence"),
            "strategy_direction": strat.get("direction", ""),
            # Risk gate (from v10_risk)
            "risk_approved": risk.get("approved"),
            "risk_rejection_reason": risk.get("rejection_reason", ""),
            "risk_percentage": risk.get("risk_percentage"),
            "position_size": risk.get("position_size"),
            # Entry (from v10_entry)
            "entry_method": entry.get("method", ""),
            "entry_status": entry.get("status", ""),
            "expected_rr": entry.get("expected_rr"),
            # Market context (for cross-angle joins)
            "regime": regime_obj.get("regime", ""),
            "regime_confidence": regime_obj.get("regime_confidence"),
            "volatility_state": regime_obj.get("volatility_state", ""),
            # Pattern
            "pattern_detected": raw.get("pattern_detected", False),
            "pattern_name": raw.get("pattern_name"),
            # r_multiple placeholder (populated via execution join)
            "r_multiple": None,
        }
