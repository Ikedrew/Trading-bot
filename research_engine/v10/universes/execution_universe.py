"""
Execution Universe Builder.

Builds the realised-execution population from S3 dataset ``trade_truth`` (the
authoritative realised-outcome source). Normalises each trade_truth record's
nested {identity, execution, timestamps, outcome, exit} structure into the flat
semantic record shape the Outcome universe + outcome enrichment consume.

Enrichment: joins entity_id from S3 dataset ``execution_results`` via
correlation_id to enable deterministic cross-universe correlation with the
Decision Universe.

Grain: 1 record = 1 completed trade with realised execution outcome.

Migration note: this universe previously wrapped the local derived file
data/research/research_universe.jsonl. That file is a rebuildable research
artifact, not a source of truth. The Execution universe is now rebuilt directly
from S3 trade_truth so it works after local logs/data are deleted.
"""

from __future__ import annotations

import logging
from typing import Any

from research_engine.v10.universes.base import UniverseBuilder, UniverseMetadata
from research_engine.v10.universes.models import Population, Universe

logger = logging.getLogger(__name__)

_DATASET = "trade_truth"
_EXEC_RESULTS_DATASET = "execution_results"


class ExecutionUniverseBuilder(UniverseBuilder):
    """
    Builds the Execution Universe from S3 dataset ``trade_truth``.

    Source: S3 trade_truth (identity/execution/timestamps/outcome/exit).
    entity_id is joined from S3 execution_results via correlation_id.
    """

    def __init__(self, symbol: str | None = None):
        super().__init__()
        self._symbol = symbol
        self._raw: list[dict[str, Any]] = []
        self._entity_id_lookup: dict[str, str] = {}  # correlation_id → entity_id

    @property
    def universe_type(self) -> Universe:
        return Universe.EXECUTION

    def load(self) -> int:
        self._raw = self._load_dataset(_DATASET, symbol=self._symbol)
        self._entity_id_lookup = self._build_entity_id_lookup()
        logger.info(
            f"[EXECUTION] Loaded {len(self._raw)} trade_truth records from S3, "
            f"{len(self._entity_id_lookup)} entity_id mappings from execution_results"
        )
        return len(self._raw)

    def build(self) -> list[dict[str, Any]]:
        if not self._raw:
            self.load()

        records = []
        excluded_missing_trade_id = 0
        excluded_missing_r_multiple = 0

        for raw in self._raw:
            # Pre-check exclusion reasons for tracking (trade_truth grain).
            trade_id = (raw.get("identity") or {}).get("trade_id", "")
            r_multiple = (raw.get("outcome") or {}).get("r_multiple_realised")

            if not trade_id:
                excluded_missing_trade_id += 1
                continue
            if r_multiple is None:
                excluded_missing_r_multiple += 1
                continue

            record = self._normalise(raw)
            if record:
                records.append(record)

        total_excluded = excluded_missing_trade_id + excluded_missing_r_multiple
        exclusions = {
            "total": total_excluded,
            "reasons": {
                "missing_trade_id": excluded_missing_trade_id,
                "missing_r_multiple": excluded_missing_r_multiple,
            },
            "source_records": len(self._raw),
            "included_records": len(records),
        }

        self._records = records
        self._built = True
        self._metadata = self._generate_metadata(
            records=records,
            source_files=(f"s3:{_DATASET}", f"s3:{_EXEC_RESULTS_DATASET}"),
            populations=(
                Population.ALL_TRADES.value,
                Population.WINNING_TRADES.value,
                Population.LOSING_TRADES.value,
                Population.ANOMALOUS_TRADES.value,
            ),
            exclusions=exclusions,
        )
        logger.info(
            f"[EXECUTION] Built {len(records)} normalised records "
            f"(excluded {total_excluded}: {excluded_missing_trade_id} missing trade_id, "
            f"{excluded_missing_r_multiple} missing r_multiple)"
        )
        return records

    def get_population(self, population: Population) -> list[dict[str, Any]]:
        records = self.records  # raises if not built

        if population == Population.ALL_TRADES:
            return records
        elif population == Population.WINNING_TRADES:
            return [r for r in records if r.get("r_multiple", 0) > 0]
        elif population == Population.LOSING_TRADES:
            return [r for r in records if r.get("r_multiple", 0) <= 0]
        elif population == Population.ANOMALOUS_TRADES:
            return [r for r in records if r.get("anomaly", False)]
        else:
            # For market/session populations, filter by session/regime
            if population == Population.SESSION_LONDON:
                return [r for r in records if r.get("session") == "LONDON"]
            elif population == Population.SESSION_NY:
                return [r for r in records if r.get("session") == "NEW_YORK"]
            elif population == Population.SESSION_ASIA:
                return [r for r in records if r.get("session") == "ASIA"]
            elif population == Population.TRENDING_REGIME:
                return [r for r in records if r.get("regime") == "TRENDING"]
            elif population == Population.RANGING_REGIME:
                return [r for r in records if r.get("regime") == "RANGING"]
            elif population == Population.TRANSITIONAL_REGIME:
                return [r for r in records if r.get("regime") == "TRANSITIONAL"]

        logger.warning(f"[EXECUTION] Unknown population: {population.value}")
        return []

    def _normalise(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        """Flatten a trade_truth record into the flat semantic execution record.

        trade_truth is the authoritative realised-outcome source. It does NOT
        carry pre-trade intent (stop_loss/take_profit) or decision/market/strategy
        context by contract — those live in the Decision/Market/Strategy universes
        and are joined downstream by entity_id. This normaliser therefore populates
        the realised-execution facts and leaves intent/context fields as their
        neutral defaults (they are not consumed from this universe).
        """
        identity = raw.get("identity", {}) or {}
        exe = raw.get("execution", {}) or {}
        ts = raw.get("timestamps", {}) or {}
        outcome = raw.get("outcome", {}) or {}
        exit_ = raw.get("exit", {}) or {}

        trade_id = identity.get("trade_id", "")
        r_multiple = outcome.get("r_multiple_realised")
        if not trade_id or r_multiple is None:
            return None

        # Enrich entity_id from execution_results, joined by correlation_id
        # (deterministic cross-universe key into the Decision universe).
        correlation_id = identity.get("correlation_id", "")
        enriched_entity_id = self._entity_id_lookup.get(correlation_id, "")

        return {
            # Identity
            "trade_id": trade_id,
            "entity_id": enriched_entity_id or trade_id,  # Enriched or fallback
            "correlation_id": correlation_id,
            # Realised execution fields (from trade_truth)
            "symbol": identity.get("symbol", ""),
            "direction": "",  # not in trade_truth (order_type only); joined elsewhere
            "entry_price": exe.get("entry_fill_price"),
            "exit_price": exe.get("exit_fill_price"),
            "entry_time": ts.get("entry_timestamp_broker"),
            "exit_time": ts.get("exit_timestamp_broker"),
            "stop_loss": None,   # intent — excluded from trade_truth by contract
            "take_profit": None,  # intent — excluded from trade_truth by contract
            "gross_profit": outcome.get("pnl_realised"),
            "commission": outcome.get("commission"),
            "swap": outcome.get("swap"),
            "net_realised_pnl": outcome.get("net_profit"),
            "r_multiple": r_multiple,
            "volume": exe.get("volume_executed"),
            "duration_seconds": ts.get("duration_seconds"),
            "exit_reason": exit_.get("exit_reason", ""),
            # Decision/market/strategy context is owned by their own universes and
            # joined by entity_id downstream — left as neutral defaults here.
            "score": None,
            "confidence": None,
            "ev": None,
            "p_success": None,
            "components": None,
            "weakest_component": None,
            "regime": "",
            "session": "",
            "volatility": "",
            "trend_state": "",
            "higher_timeframe_bias": "",
            "h4_phase": "",
            "h1_clarity": None,
            "family": "",
            "pattern": "",
            "conditions_met": None,
            "strategy_confidence": None,
            "opportunity_quality": None,
            "opportunity_type": "",
            # Quality
            "anomaly": False,
            "anomaly_reasons": [],
            "data_completeness": "",
        }

    def _build_entity_id_lookup(self) -> dict[str, str]:
        """
        Build a lookup: correlation_id → entity_id from S3 execution_results.

        Enables deterministic cross-universe correlation by incorporating
        entity_id (which links to the Decision Universe) into Execution records.
        Source: S3 dataset ``execution_results``. Join key: correlation_id.
        """
        lookup: dict[str, str] = {}
        for record in self._load_dataset(_EXEC_RESULTS_DATASET, symbol=self._symbol):
            # Only primary execution results (not protection verification records).
            if record.get("comment") == "protection_verification":
                continue
            if not record.get("result_ok", False):
                continue
            entity_id = record.get("entity_id", "")
            correlation_id = record.get("correlation_id", "")
            if entity_id and correlation_id:
                lookup[correlation_id] = entity_id

        logger.info(f"[EXECUTION] Entity_id lookup built: {len(lookup)} mappings")
        return lookup
