"""
Strategy Universe Builder.

Extracts strategy observations from two sources:
    1. v10_strategy within decision traces (strategy selected/rejected at decision time)
    2. logs/strategy_observations/ (detailed strategy evaluation records)

Grain: 1 record = 1 strategy evaluation event (strategy selected, rejected,
or no strategy matched for a given opportunity).

This universe enables questions about:
    - Strategy family expectancy
    - Pattern expectancy
    - Strategy selection accuracy
    - Strategy rejection patterns
    - Strategy × regime interactions
    - Strategy conditions effectiveness
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from research_engine.v10.universes.base import UniverseBuilder
from research_engine.v10.universes.models import Population, Universe

logger = logging.getLogger(__name__)

_DECISION_TRACE_DIR = Path("logs/decision_trace")
_STRATEGY_OBS_DIR = Path("logs/strategy_observations")


class StrategyUniverseBuilder(UniverseBuilder):
    """
    Builds the Strategy Universe from decision traces and strategy observation logs.

    Primary source: v10_strategy in decision traces (linked by entity_id).
    Secondary source: logs/strategy_observations/ (detailed strategy evaluations).
    """

    def __init__(
        self,
        decision_trace_dir: Path | str | None = None,
        strategy_obs_dir: Path | str | None = None,
    ):
        super().__init__()
        self._dt_dir = Path(decision_trace_dir) if decision_trace_dir else _DECISION_TRACE_DIR
        self._so_dir = Path(strategy_obs_dir) if strategy_obs_dir else _STRATEGY_OBS_DIR
        self._raw_dt: list[dict[str, Any]] = []
        self._raw_so: list[dict[str, Any]] = []

    @property
    def universe_type(self) -> Universe:
        return Universe.STRATEGY

    def load(self) -> int:
        self._raw_dt = self._load_jsonl_directory(self._dt_dir)
        self._raw_so = self._load_jsonl_directory(self._so_dir)
        total = len(self._raw_dt) + len(self._raw_so)
        logger.info(
            f"[STRATEGY] Loaded {len(self._raw_dt)} decision traces + "
            f"{len(self._raw_so)} strategy observations = {total} total"
        )
        return total

    def build(self) -> list[dict[str, Any]]:
        if not self._raw_dt and not self._raw_so:
            self.load()

        records = []

        # Primary: extract from decision traces
        for raw in self._raw_dt:
            record = self._normalise_from_decision_trace(raw)
            if record:
                records.append(record)

        # Secondary: strategy observations (more detailed)
        for raw in self._raw_so:
            record = self._normalise_from_strategy_obs(raw)
            if record:
                records.append(record)

        # Deduplicate: prefer strategy_observations (richer data)
        # Use entity_id as dedup key
        so_entities = {
            r.get("entity_id") for r in records
            if r.get("source") == "strategy_observations" and r.get("entity_id")
        }
        records = [
            r for r in records
            if r.get("source") == "strategy_observations"
            or r.get("entity_id") not in so_entities
        ]

        self._records = records
        self._built = True

        source_files = (str(self._dt_dir), str(self._so_dir))
        self._metadata = self._generate_metadata(
            records=records,
            source_files=source_files,
            populations=(
                Population.ALL_STRATEGIES.value,
                Population.TREND_CONTINUATION.value,
                Population.MEAN_REVERSION.value,
                Population.BREAKOUT.value,
                Population.MOMENTUM.value,
                Population.STRATEGY_ELIGIBLE.value,
                Population.STRATEGY_SELECTED.value,
                Population.STRATEGY_REJECTED.value,
            ),
        )
        logger.info(f"[STRATEGY] Built {len(records)} normalised records")
        return records

    def get_population(self, population: Population) -> list[dict[str, Any]]:
        records = self.records

        if population == Population.ALL_STRATEGIES:
            return records
        elif population == Population.TREND_CONTINUATION:
            return [
                r for r in records
                if r.get("family", "").upper() in ("TREND_CONTINUATION", "CONTINUATION")
            ]
        elif population == Population.MEAN_REVERSION:
            return [
                r for r in records
                if r.get("family", "").upper() in ("MEAN_REVERSION", "REVERSAL")
            ]
        elif population == Population.BREAKOUT:
            return [
                r for r in records
                if r.get("family", "").upper() in ("BREAKOUT", "FALSE_BREAK")
            ]
        elif population == Population.MOMENTUM:
            return [
                r for r in records
                if r.get("family", "").upper() == "MOMENTUM"
            ]
        elif population == Population.STRATEGY_ELIGIBLE:
            return [
                r for r in records
                if r.get("evaluation_status") in ("ELIGIBLE", "SELECTED", "EXECUTED")
                or r.get("family", "") not in ("", "NONE")
            ]
        elif population == Population.STRATEGY_SELECTED:
            return [
                r for r in records
                if r.get("action") == "EXECUTE"
                or r.get("evaluation_status") == "SELECTED"
            ]
        elif population == Population.STRATEGY_REJECTED:
            return [
                r for r in records
                if (r.get("family", "") in ("", "NONE")
                    or r.get("evaluation_status") in ("REJECTED", "NOT_MET"))
                and r.get("action") != "EXECUTE"
            ]

        logger.warning(f"[STRATEGY] Unknown population: {population.value}")
        return []

    def _normalise_from_decision_trace(
        self, raw: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Extract strategy info from a decision trace record."""
        entity_id = raw.get("entity_id", "")
        if not entity_id:
            return None

        strat = raw.get("v10_strategy", {}) or {}
        opp = raw.get("v10_opportunity", {}) or {}
        mkt = raw.get("v10_market_state", {}) or {}
        regime_obj = mkt.get("regime", {}) or {}

        family = strat.get("family", "")
        action = raw.get("action", "")

        return {
            # Identity & join keys
            "entity_id": entity_id,
            "correlation_id": raw.get("correlation_id", ""),
            "symbol": raw.get("symbol", ""),
            "cycle_id": raw.get("cycle_id"),
            "timestamp_utc": raw.get("timestamp_utc", ""),
            "source": "decision_trace",
            # Strategy core
            "family": family,
            "confidence": strat.get("confidence"),
            "direction": strat.get("direction", ""),
            "reasoning": strat.get("reasoning", []),
            # Pattern
            "pattern": raw.get("pattern_name") or "",
            "pattern_detected": raw.get("pattern_detected", False),
            # Conditions (from decision trace — limited)
            "conditions_met": None,
            "conditions_passed": None,
            "conditions_failed": None,
            "evaluation_status": "",
            # Opportunity quality (for strategy×quality analysis)
            "opportunity_quality": opp.get("overall_quality"),
            "opportunity_type": opp.get("opportunity_type", ""),
            # Market context (for strategy×regime)
            "regime": regime_obj.get("regime", ""),
            "h4_market_phase": mkt.get("h4", {}).get("market_phase", ""),
            # Decision outcome
            "action": action,
            "score": raw.get("score_strategy"),
            # r_multiple placeholder (populated via execution join)
            "r_multiple": None,
        }

    def _normalise_from_strategy_obs(
        self, raw: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Extract from strategy_observations log record."""
        entity_id = raw.get("entity_id", "")
        symbol = raw.get("symbol", "")
        if not symbol:
            return None

        return {
            # Identity & join keys
            "entity_id": entity_id,
            "correlation_id": "",
            "symbol": symbol,
            "cycle_id": raw.get("cycle_id"),
            "timestamp_utc": str(raw.get("timestamp_utc", "")),
            "source": "strategy_observations",
            # Strategy core
            "family": raw.get("strategy_family", ""),
            "confidence": raw.get("confidence"),
            "direction": raw.get("direction", ""),
            "reasoning": [],
            # Pattern
            "pattern": raw.get("detected_pattern", ""),
            "pattern_detected": bool(raw.get("detected_pattern")),
            # Conditions (rich data from strategy_observations)
            "conditions_met": raw.get("conditions_passed"),
            "conditions_passed": raw.get("conditions_passed"),
            "conditions_failed": raw.get("conditions_failed"),
            "evaluation_status": raw.get("evaluation_status", ""),
            # Opportunity
            "opportunity_quality": None,
            "opportunity_type": "",
            # Market context
            "regime": raw.get("h4_regime", ""),
            "h4_market_phase": raw.get("market_phase", ""),
            # Decision
            "action": raw.get("decision_action", ""),
            "score": raw.get("decision_score"),
            # r_multiple placeholder
            "r_multiple": None,
        }
