"""
Market Universe Builder.

Extracts market state observations from two sources:
    1. v10_market_state within decision traces (market state at decision time)
    2. logs/market_context/ (standalone market context observations)

Grain: 1 record = 1 market-state observation at a point in time,
tied to a decision via entity_id for cross-angle joins.

This universe enables questions about:
    - Regime prediction of outcomes
    - HTF alignment value
    - Volatility state impact
    - Market structure clarity
    - Location quality
    - Session-based edge variation
    - Market drift detection
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from research_engine.v10.universes.base import UniverseBuilder
from research_engine.v10.universes.models import Population, Universe

logger = logging.getLogger(__name__)

_DECISION_TRACE_DIR = Path("logs/decision_trace")
_MARKET_CONTEXT_DIR = Path("logs/market_context")


class MarketUniverseBuilder(UniverseBuilder):
    """
    Builds the Market Universe from decision traces and market context logs.

    Primary source: v10_market_state in decision traces (linked by entity_id).
    Secondary source: logs/market_context/ (standalone, linked by symbol+cycle_id).
    """

    def __init__(
        self,
        decision_trace_dir: Path | str | None = None,
        market_context_dir: Path | str | None = None,
    ):
        super().__init__()
        self._dt_dir = Path(decision_trace_dir) if decision_trace_dir else _DECISION_TRACE_DIR
        self._mc_dir = Path(market_context_dir) if market_context_dir else _MARKET_CONTEXT_DIR
        self._raw_dt: list[dict[str, Any]] = []
        self._raw_mc: list[dict[str, Any]] = []

    @property
    def universe_type(self) -> Universe:
        return Universe.MARKET

    def load(self) -> int:
        self._raw_dt = self._load_jsonl_directory(self._dt_dir)
        self._raw_mc = self._load_jsonl_directory(self._mc_dir)
        total = len(self._raw_dt) + len(self._raw_mc)
        logger.info(
            f"[MARKET] Loaded {len(self._raw_dt)} decision traces + "
            f"{len(self._raw_mc)} market context records = {total} total"
        )
        return total

    def build(self) -> list[dict[str, Any]]:
        if not self._raw_dt and not self._raw_mc:
            self.load()

        records = []

        # Primary: extract from decision traces (has entity_id for joins)
        for raw in self._raw_dt:
            mkt_state = raw.get("v10_market_state")
            if not mkt_state:
                continue
            record = self._normalise_from_decision_trace(raw, mkt_state)
            if record:
                records.append(record)

        # Secondary: standalone market context (linked by symbol+cycle_id)
        for raw in self._raw_mc:
            record = self._normalise_from_market_context(raw)
            if record:
                records.append(record)

        # Deduplicate: prefer decision-trace records (they have entity_id)
        # If a market_context record has same symbol+cycle_id as a DT record, skip it
        dt_keys = {
            (r.get("symbol", ""), r.get("cycle_id"))
            for r in records
            if r.get("source") == "decision_trace"
        }
        records = [
            r for r in records
            if r.get("source") == "decision_trace"
            or (r.get("symbol", ""), r.get("cycle_id")) not in dt_keys
        ]

        self._records = records
        self._built = True

        source_files = (str(self._dt_dir), str(self._mc_dir))
        self._metadata = self._generate_metadata(
            records=records,
            source_files=source_files,
            populations=(
                Population.ALL_MARKET_STATES.value,
                Population.TRENDING_REGIME.value,
                Population.RANGING_REGIME.value,
                Population.TRANSITIONAL_REGIME.value,
                Population.HIGH_VOLATILITY.value,
                Population.LOW_VOLATILITY.value,
                Population.SESSION_LONDON.value,
                Population.SESSION_NY.value,
                Population.SESSION_ASIA.value,
            ),
        )
        logger.info(f"[MARKET] Built {len(records)} normalised records")
        return records

    def get_population(self, population: Population) -> list[dict[str, Any]]:
        records = self.records

        if population == Population.ALL_MARKET_STATES:
            return records
        elif population == Population.TRENDING_REGIME:
            return [r for r in records if r.get("regime") == "TRENDING"]
        elif population == Population.RANGING_REGIME:
            return [r for r in records if r.get("regime") == "RANGING"]
        elif population == Population.TRANSITIONAL_REGIME:
            return [r for r in records if r.get("regime") == "TRANSITIONAL"]
        elif population == Population.HIGH_VOLATILITY:
            return [
                r for r in records
                if r.get("volatility_state") in ("HIGH", "EXPANDING", "EXPANSION")
            ]
        elif population == Population.LOW_VOLATILITY:
            return [
                r for r in records
                if r.get("volatility_state") in ("LOW", "CONTRACTING", "CONTRACTION")
            ]
        elif population == Population.SESSION_LONDON:
            return [r for r in records if r.get("session") == "LONDON"]
        elif population == Population.SESSION_NY:
            return [r for r in records if r.get("session") == "NEW_YORK"]
        elif population == Population.SESSION_ASIA:
            return [r for r in records if r.get("session") == "ASIA"]

        logger.warning(f"[MARKET] Unknown population: {population.value}")
        return []

    def _normalise_from_decision_trace(
        self, raw: dict[str, Any], mkt: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Extract market state from a decision trace record."""
        entity_id = raw.get("entity_id", "")
        if not entity_id:
            return None

        h4 = mkt.get("h4", {}) or {}
        h1 = mkt.get("h1", {}) or {}
        m15 = mkt.get("m15", {}) or {}
        m5 = mkt.get("m5", {}) or {}
        regime_obj = mkt.get("regime", {}) or {}
        location = mkt.get("location", {}) or {}
        htf = mkt.get("htf_alignment", {}) or {}

        regime = regime_obj.get("regime", "")

        return {
            # Identity & join keys
            "entity_id": entity_id,
            "symbol": raw.get("symbol", ""),
            "cycle_id": raw.get("cycle_id"),
            "timestamp_utc": raw.get("timestamp_utc", ""),
            "source": "decision_trace",
            # Regime
            "regime": regime,
            "regime_confidence": regime_obj.get("regime_confidence"),
            "volatility_state": regime_obj.get("volatility_state", ""),
            "expansion_state": regime_obj.get("expansion_state", ""),
            # H4 timeframe
            "h4_trend": h4.get("trend", ""),
            "h4_market_phase": h4.get("market_phase", ""),
            "h4_volatility_state": h4.get("volatility_state", ""),
            "h4_atr": h4.get("atr"),
            "h4_structure_type": h4.get("structure_type", ""),
            # H1 timeframe
            "h1_dominant_trend": h1.get("dominant_trend", ""),
            "h1_structural_clarity": h1.get("structural_clarity"),
            "h1_bos_confirmed": h1.get("bos_confirmed", False),
            "h1_bos_direction": h1.get("bos_direction", ""),
            "h1_choch_detected": h1.get("choch_detected", False),
            # M15 timeframe
            "m15_pullback_active": m15.get("pullback_active", False),
            "m15_displacement_present": m15.get("displacement_present", False),
            "m15_range_position": m15.get("range_position"),
            # M5 timeframe
            "m5_momentum_direction": m5.get("momentum_direction", ""),
            "m5_momentum_strength": m5.get("momentum_strength"),
            "m5_atr": m5.get("atr"),
            "m5_spread_atr_ratio": m5.get("spread_atr_ratio"),
            # Location
            "location_type": location.get("location_type", ""),
            "inside_institutional_zone": location.get("inside_institutional_zone", False),
            "zone_quality": location.get("zone_quality"),
            "range_position": location.get("range_position"),
            "premium_discount": location.get("premium_discount", ""),
            # HTF alignment
            "htf_alignment_macro_bias": htf.get("macro_bias", ""),
            "htf_alignment_strength": htf.get("macro_bias_strength"),
            "structure_alignment": htf.get("structure_alignment"),
            # Session (derived from timestamp if available)
            "session": self._derive_session(raw.get("timestamp_utc", "")),
        }

    def _normalise_from_market_context(
        self, raw: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Extract market state from a standalone market_context record."""
        symbol = raw.get("symbol", "")
        if not symbol:
            return None

        h4 = raw.get("h4", {}) or {}
        h1 = raw.get("h1", {}) or {}
        m15 = raw.get("m15", {}) or {}
        m5 = raw.get("m5", {}) or {}

        return {
            # Identity
            "entity_id": "",  # No direct entity_id link
            "symbol": symbol,
            "cycle_id": raw.get("cycle_id"),
            "timestamp_utc": str(raw.get("timestamp_utc", "")),
            "source": "market_context",
            # Regime
            "regime": raw.get("regime", ""),
            "regime_confidence": raw.get("regime_confidence"),
            "volatility_state": "",
            "expansion_state": "",
            # H4
            "h4_trend": h4.get("trend_bias", ""),
            "h4_market_phase": raw.get("phase", ""),
            "h4_volatility_state": "",
            "h4_atr": None,
            "h4_structure_type": "",
            # H1
            "h1_dominant_trend": h1.get("direction", ""),
            "h1_structural_clarity": None,
            "h1_bos_confirmed": h1.get("bos_confirmed", False),
            "h1_bos_direction": h1.get("bos_direction", ""),
            "h1_choch_detected": False,
            # M15
            "m15_pullback_active": False,
            "m15_displacement_present": False,
            "m15_range_position": None,
            # M5
            "m5_momentum_direction": m5.get("bias_direction", ""),
            "m5_momentum_strength": m5.get("bias_strength"),
            "m5_atr": None,
            "m5_spread_atr_ratio": None,
            # Location (not available in market_context)
            "location_type": "",
            "inside_institutional_zone": False,
            "zone_quality": None,
            "range_position": None,
            "premium_discount": "",
            # HTF
            "htf_alignment_macro_bias": "",
            "htf_alignment_strength": raw.get("alignment_score"),
            "structure_alignment": None,
            # Session
            "session": "",
        }

    @staticmethod
    def _derive_session(timestamp_utc: str) -> str:
        """Derive trading session from UTC timestamp string."""
        if not timestamp_utc:
            return ""
        try:
            # Format: "2026-08-07T14:30:00Z" or similar
            hour_str = timestamp_utc.split("T")[1][:2] if "T" in timestamp_utc else ""
            if not hour_str:
                return ""
            hour = int(hour_str)
            if 7 <= hour < 16:
                return "LONDON"
            elif 12 <= hour < 21:
                return "NEW_YORK"
            else:
                return "ASIA"
        except (IndexError, ValueError):
            return ""
