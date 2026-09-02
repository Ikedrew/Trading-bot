"""
Shadow Outcome Universe Builder.

Consumes runtime shadow trade data and produces the Shadow research world's
counterfactual outcome populations.

Source: logs/shadow_trades/<SYMBOL>/*.jsonl (production: shadow_trades_v1)

Grain: 1 record = 1 closed shadow trade = 1 counterfactual outcome observation.

This universe provides:
    - Counterfactual R-multiple (NOT realised trading performance)
    - MFE / MAE in R-units
    - Exit reason (stop_loss / take_profit / max_bars_timeout)
    - Bars held
    - Entry geometry (entry/SL/TP/direction/position_size)
    - Risk parameters (risk_distance, R:R ratio)
    - Shadow type classification (HORIZON_ALTERNATIVE horizons)
    - Lineage to originating decision via entity_id

This universe does NOT provide:
    - Realised broker execution outcomes
    - Actual slippage or commission
    - Trade management adjustments
    - Live position lifecycle

Populations:
    ALL_SHADOW_OUTCOMES       — every valid shadow record
    SHADOW_WINS               — counterfactual R > 0
    SHADOW_LOSSES             — counterfactual R <= 0
    PRIMARY_V10_SHADOW        — V10 engine geometry (EXECUTE decisions)
    HORIZON_SCALP             — horizon SCALP geometry
    HORIZON_INTRADAY          — horizon INTRADAY geometry
    HORIZON_EXTENDED          — horizon EXTENDED geometry
    SHADOW_FROM_EXECUTE       — originates from EXECUTE decision (requires join)
    SHADOW_FROM_NO_TRADE      — originates from NO_TRADE decision (requires join)
    SHADOW_TP_HIT             — exit via take_profit
    SHADOW_SL_HIT             — exit via stop_loss
    SHADOW_TIMEOUT            — exit via max_bars_timeout

Evidence contract:
    ALL R-multiples in this universe are COUNTERFACTUAL.
    They represent model-simulated outcomes, not realised broker performance.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from core.production_data_contract import supported_schemas

from research_engine.v10.universes.base import UniverseBuilder
from research_engine.v10.universes.models import Population, Universe

logger = logging.getLogger(__name__)

_DEFAULT_DIR = Path("logs/shadow_trades")

# Valid trade_id prefixes for production shadow records
_VALID_PREFIXES = ("hshadow_", "shadow_")


class ShadowOutcomeUniverseBuilder(UniverseBuilder):
    """
    Builds the Shadow Outcome Universe from runtime shadow trade data.

    Reads production V1 and supported legacy shadow-trade JSONL files.
    records, and classifies into shadow populations.

    Excludes:
        - Records without valid R-multiple
        - Records with non-production trade_id patterns (test contamination)
        - Records outside the explicit production/legacy schema allowlist

    Preserves:
        - Records with empty entity_id (usable for non-join populations)
        - Records from all Horizon shadow horizons
    """

    def __init__(self, source_dir: Path | str | None = None):
        super().__init__()
        self._source_dir = Path(source_dir) if source_dir else _DEFAULT_DIR
        self._raw: list[dict[str, Any]] = []

    @property
    def universe_type(self) -> Universe:
        return Universe.SHADOW_OUTCOME

    def load(self) -> int:
        self._raw = self._load_jsonl_directory(self._source_dir)
        logger.info(
            f"[SHADOW_OUTCOME] Loaded {len(self._raw)} raw shadow records "
            f"from {self._source_dir}"
        )
        return len(self._raw)

    def build(self) -> list[dict[str, Any]]:
        if not self._raw:
            self.load()

        records = []
        excluded_no_r = 0
        excluded_bad_schema = 0
        excluded_test_data = 0
        excluded_normalise_fail = 0

        for raw in self._raw:
            # Schema check
            schema = raw.get("schema_version", "")
            if schema not in supported_schemas("shadow_trades"):
                excluded_bad_schema += 1
                continue

            # Test data exclusion: trade_id must start with valid prefix
            identity = raw.get("identity", {})
            trade_id = identity.get("trade_id", "")
            if not any(trade_id.startswith(p) for p in _VALID_PREFIXES):
                excluded_test_data += 1
                continue

            # R-multiple validity
            outcome = raw.get("simulated_outcome", {})
            r_multiple = outcome.get("pnl_r_multiple")
            if r_multiple is None:
                excluded_no_r += 1
                continue

            record = self._normalise(raw)
            if record:
                records.append(record)
            else:
                excluded_normalise_fail += 1

        total_excluded = (
            excluded_no_r + excluded_bad_schema
            + excluded_test_data + excluded_normalise_fail
        )
        exclusions = {
            "total": total_excluded,
            "reasons": {
                "bad_schema": excluded_bad_schema,
                "test_data": excluded_test_data,
                "no_r_multiple": excluded_no_r,
                "normalise_fail": excluded_normalise_fail,
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
                Population.ALL_SHADOW_OUTCOMES.value,
                Population.SHADOW_WINS.value,
                Population.SHADOW_LOSSES.value,
                Population.HORIZON_SCALP.value,
                Population.HORIZON_INTRADAY.value,
                Population.HORIZON_EXTENDED.value,
                Population.SHADOW_TP_HIT.value,
                Population.SHADOW_SL_HIT.value,
                Population.SHADOW_TIMEOUT.value,
            ),
            exclusions=exclusions,
        )
        logger.info(
            f"[SHADOW_OUTCOME] Built {len(records)} normalised records "
            f"(excluded {total_excluded}: {excluded_bad_schema} bad_schema, "
            f"{excluded_test_data} test_data, {excluded_no_r} no_r, "
            f"{excluded_normalise_fail} normalise_fail)"
        )
        return records

    def get_population(self, population: Population) -> list[dict[str, Any]]:
        records = self.records

        if population == Population.ALL_SHADOW_OUTCOMES:
            return records
        elif population == Population.SHADOW_WINS:
            return [r for r in records if r.get("r_multiple", 0) > 0]
        elif population == Population.SHADOW_LOSSES:
            return [r for r in records if r.get("r_multiple", 0) <= 0]
        elif population == Population.HORIZON_SCALP:
            return [r for r in records if r.get("evaluated_horizon") == "SCALP" and r.get("shadow_type") == "HORIZON_ALTERNATIVE"]
        elif population == Population.HORIZON_INTRADAY:
            return [r for r in records if r.get("evaluated_horizon") == "INTRADAY" and r.get("shadow_type") == "HORIZON_ALTERNATIVE"]
        elif population == Population.HORIZON_EXTENDED:
            return [r for r in records if r.get("evaluated_horizon") == "EXTENDED" and r.get("shadow_type") == "HORIZON_ALTERNATIVE"]
        elif population == Population.SHADOW_FROM_EXECUTE:
            # Canonical lineage: horizon shadows that responded to an EXECUTE verdict
            return [r for r in records if r.get("shadow_type") == "HORIZON_ALTERNATIVE" and r.get("v10_action") == "EXECUTE"]
        elif population == Population.SHADOW_FROM_NO_TRADE:
            # Canonical lineage: horizon shadows that responded to a NO_TRADE verdict
            return [r for r in records if r.get("shadow_type") == "HORIZON_ALTERNATIVE" and r.get("v10_action") == "NO_TRADE"]
        elif population == Population.SHADOW_TP_HIT:
            return [r for r in records if r.get("exit_reason") == "take_profit"]
        elif population == Population.SHADOW_SL_HIT:
            return [r for r in records if r.get("exit_reason") == "stop_loss"]
        elif population == Population.SHADOW_TIMEOUT:
            return [r for r in records if r.get("exit_reason") == "max_bars_timeout"]

        logger.warning(f"[SHADOW_OUTCOME] Unknown population: {population.value}")
        return []

    def _normalise(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        """
        Flatten a shadow_trades_v1 record into a normalised research record.

        Produces flat dict with semantic field names compatible with
        existing research primitives (expectancy, segmentation, etc.).
        """
        identity = raw.get("identity", {})
        snapshot = raw.get("decision_snapshot", {})
        outcome = raw.get("simulated_outcome", {})
        risk_config = snapshot.get("risk_config_snapshot", {})

        trade_id = identity.get("trade_id", "")
        entity_id = identity.get("entity_id", "") or ""
        r_multiple = outcome.get("pnl_r_multiple")

        if r_multiple is None:
            return None

        # Classify shadow type — prefer explicit field, fall back to trade_id prefix
        # NOTE: the legacy "shadow_" prefix no longer maps to V10_PRIMARY (Phase 1I-C);
        # historical shadow_-prefixed records normalise as UNKNOWN and enter no
        # active canonical-lineage population.
        shadow_type_explicit = identity.get("shadow_type", "") or ""
        if shadow_type_explicit:
            shadow_type = shadow_type_explicit
        elif trade_id.startswith("hshadow_"):
            shadow_type = "HORIZON_ALTERNATIVE"
        else:
            shadow_type = "UNKNOWN"

        # Extract horizon — prefer explicit fields, fall back to trade_id parsing
        evaluated_horizon = identity.get("evaluated_horizon", "") or ""
        trade_horizon = snapshot.get("trade_horizon", "") or ""
        if not evaluated_horizon and not trade_horizon:
            if trade_id.startswith("hshadow_"):
                parts = trade_id.split("_")
                if len(parts) >= 4:
                    evaluated_horizon = parts[-1].upper()
                    trade_horizon = evaluated_horizon
        if not evaluated_horizon:
            evaluated_horizon = trade_horizon

        # Shadow lineage fields (new contract — empty/None for historical records)
        v10_selected_horizon = identity.get("v10_selected_horizon", "") or ""
        horizon_selection_status = identity.get("horizon_selection_status", "") or "UNKNOWN"
        horizon_geometry_source = identity.get("horizon_geometry_source", "") or ""
        v10_rejection_stage = identity.get("v10_rejection_stage", "") or ""
        v10_action = identity.get("v10_action", "") or ""

        # Determine data quality classification
        has_lineage_contract = bool(v10_selected_horizon and horizon_selection_status != "UNKNOWN")
        if has_lineage_contract:
            data_quality = "VALID"
        elif entity_id:
            data_quality = "CONDITIONAL"
        else:
            data_quality = "CONDITIONAL"

        return {
            # Identity & join keys
            "shadow_trade_id": trade_id,
            "entity_id": entity_id,
            "correlation_id": identity.get("correlation_id", ""),
            "symbol": identity.get("symbol", ""),
            "cycle_id": identity.get("cycle_id"),
            "strategy_id": identity.get("strategy_id", ""),

            # Shadow classification (approved lineage contract)
            "shadow_type": shadow_type,
            "trade_horizon": trade_horizon,
            "evaluated_horizon": evaluated_horizon,
            "evidence_source": "COUNTERFACTUAL",
            "v10_selected_horizon": v10_selected_horizon,
            "horizon_selection_status": horizon_selection_status,
            "horizon_geometry_source": horizon_geometry_source,
            "v10_rejection_stage": v10_rejection_stage,
            "v10_action": v10_action,
            "data_quality": data_quality,

            # Decision-time snapshot (frozen at shadow creation)
            "direction": snapshot.get("direction", ""),
            "entry_price": snapshot.get("entry_intent_price"),
            "stop_loss": snapshot.get("stop_loss_intent"),
            "take_profit": snapshot.get("take_profit_intent"),
            "position_size": snapshot.get("position_size"),
            "pattern": snapshot.get("pattern", ""),
            "score": snapshot.get("score"),
            "regime": snapshot.get("regime", "") or "",
            "h4_regime": snapshot.get("h4_regime", "") or "",
            "h1_bias": snapshot.get("h1_bias", "") or "",
            "market_phase": snapshot.get("market_phase", "") or "",
            "spread_at_entry": snapshot.get("spread_at_entry"),
            "timestamp_decision_utc": snapshot.get("timestamp_decision_utc"),

            # Risk geometry
            "risk_distance": risk_config.get("risk_price_distance"),
            "risk_pips": risk_config.get("risk_pips"),
            "reward_risk_ratio": risk_config.get("reward_risk_ratio"),

            # Counterfactual outcome (shadow-owned)
            "r_multiple": r_multiple,
            "mfe_r": outcome.get("mfe_r"),
            "mae_r": outcome.get("mae_r"),
            "exit_reason": outcome.get("exit_reason", ""),
            "bars_held": outcome.get("bars_held"),
            "exit_price": outcome.get("exit_price"),
            "exit_timestamp": outcome.get("exit_timestamp"),

            # Lineage quality flags
            "has_entity_id": bool(entity_id),
            "has_correlation_id": bool(identity.get("correlation_id", "")),
            "has_lineage_contract": has_lineage_contract,
        }
