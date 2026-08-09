"""
Execution Universe Builder.

Wraps the existing execution universe at data/research/research_universe.jsonl.
Normalises the nested {execution, decision, market, strategy, quality} structure
into flat records with semantic field names.

Enrichment: joins entity_id from logs/execution_results/ via deal/ticket matching
to enable deterministic cross-universe correlation with the Decision Universe.

Grain: 1 record = 1 completed trade with execution outcome.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from research_engine.v10.universes.base import UniverseBuilder, UniverseMetadata
from research_engine.v10.universes.models import Population, Universe

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path("data/research/research_universe.jsonl")
_EXECUTION_RESULTS_DIR = Path("logs/execution_results")


class ExecutionUniverseBuilder(UniverseBuilder):
    """
    Builds the Execution Universe from the existing research universe file.

    Source: data/research/research_universe.jsonl
    Schema: {trade_id, execution{...}, decision{...}, market{...}, strategy{...}, quality{...}}
    """

    def __init__(
        self,
        source_path: Path | str | None = None,
        execution_results_dir: Path | str | None = None,
    ):
        super().__init__()
        self._source_path = Path(source_path) if source_path else _DEFAULT_PATH
        self._exec_results_dir = (
            Path(execution_results_dir) if execution_results_dir
            else _EXECUTION_RESULTS_DIR
        )
        self._raw: list[dict[str, Any]] = []
        self._entity_id_lookup: dict[int, str] = {}  # deal → entity_id

    @property
    def universe_type(self) -> Universe:
        return Universe.EXECUTION

    def load(self) -> int:
        self._raw = self._load_jsonl(self._source_path)
        self._entity_id_lookup = self._build_entity_id_lookup()
        logger.info(
            f"[EXECUTION] Loaded {len(self._raw)} records from {self._source_path}, "
            f"{len(self._entity_id_lookup)} entity_id mappings from execution_results"
        )
        return len(self._raw)

    def build(self) -> list[dict[str, Any]]:
        if not self._raw:
            self.load()

        records = []
        for raw in self._raw:
            record = self._normalise(raw)
            if record:
                records.append(record)

        self._records = records
        self._built = True
        self._metadata = self._generate_metadata(
            records=records,
            source_files=(str(self._source_path),),
            populations=(
                Population.ALL_TRADES.value,
                Population.WINNING_TRADES.value,
                Population.LOSING_TRADES.value,
                Population.ANOMALOUS_TRADES.value,
            ),
        )
        logger.info(
            f"[EXECUTION] Built {len(records)} normalised records"
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
        """Flatten the nested execution universe record into semantic fields."""
        exe = raw.get("execution", {})
        dec = raw.get("decision", {})
        mkt = raw.get("market", {})
        strat = raw.get("strategy", {})
        qual = raw.get("quality", {})

        # Must have at minimum a trade_id and r_multiple
        trade_id = raw.get("trade_id", "")
        r_multiple = exe.get("r_multiple")
        if not trade_id or r_multiple is None:
            return None

        # Enrich entity_id from execution_results (CR-001 fix)
        # The execution_results dataset persists entity_id alongside deal/ticket.
        # We join via ticket number extracted from trade_id (format: pos_DEAL).
        ticket = exe.get("ticket")
        enriched_entity_id = ""
        if ticket and ticket in self._entity_id_lookup:
            enriched_entity_id = self._entity_id_lookup[ticket]

        return {
            # Identity
            "trade_id": trade_id,
            "entity_id": enriched_entity_id or trade_id,  # Enriched or fallback
            # Execution fields
            "ticket": exe.get("ticket"),
            "symbol": exe.get("symbol", ""),
            "direction": exe.get("direction", ""),
            "entry_price": exe.get("entry_price"),
            "exit_price": exe.get("exit_price"),
            "entry_time": exe.get("entry_time"),
            "exit_time": exe.get("exit_time"),
            "stop_loss": exe.get("stop_loss"),
            "take_profit": exe.get("take_profit"),
            "gross_profit": exe.get("gross_profit"),
            "commission": exe.get("commission"),
            "swap": exe.get("swap"),
            "net_realised_pnl": exe.get("net_realised_pnl"),
            "r_multiple": r_multiple,
            "volume": exe.get("volume"),
            "duration_seconds": exe.get("duration_seconds"),
            "exit_reason": exe.get("exit_reason", ""),
            # Decision fields (from execution universe)
            "score": dec.get("score"),
            "confidence": dec.get("confidence"),
            "ev": dec.get("ev"),
            "p_success": dec.get("p_success"),
            "components": dec.get("components"),
            "weakest_component": dec.get("weakest_component"),
            # Market fields (from execution universe)
            "regime": mkt.get("regime", ""),
            "session": mkt.get("session", ""),
            "volatility": mkt.get("volatility", ""),
            "trend_state": mkt.get("trend_state", ""),
            "higher_timeframe_bias": mkt.get("higher_timeframe_bias", ""),
            "h4_phase": mkt.get("h4_phase", ""),
            "h1_clarity": mkt.get("h1_clarity"),
            # Strategy fields (from execution universe)
            "family": strat.get("family", ""),
            "pattern": strat.get("pattern", ""),
            "conditions_met": strat.get("conditions_met"),
            "strategy_confidence": strat.get("strategy_confidence"),
            "opportunity_quality": strat.get("opportunity_quality"),
            "opportunity_type": strat.get("opportunity_type", ""),
            # Quality fields
            "anomaly": qual.get("anomaly", False),
            "anomaly_reasons": qual.get("anomaly_reasons", []),
            "data_completeness": qual.get("data_completeness", ""),
        }

    def _build_entity_id_lookup(self) -> dict[int, str]:
        """
        Build a lookup table: deal/ticket → entity_id from execution_results.

        This is the CR-001 fix: it enables deterministic cross-universe
        correlation by incorporating entity_id (which links to Decision Universe)
        into Execution Universe records.

        Source: logs/execution_results/{SYMBOL}/{DATE}.jsonl
        Join key: deal (int) → entity_id (str, format SYMBOL_CYCLE_TS)
        """
        lookup: dict[int, str] = {}
        if not self._exec_results_dir.exists():
            return lookup

        for jsonl_file in sorted(self._exec_results_dir.rglob("*.jsonl")):
            try:
                with open(jsonl_file, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        # Only use primary execution records (not protection_verification)
                        if record.get("comment") == "protection_verification":
                            continue
                        if not record.get("result_ok", False):
                            continue

                        entity_id = record.get("entity_id", "")
                        deal = record.get("deal")

                        if entity_id and deal:
                            lookup[int(deal)] = entity_id
            except Exception:
                continue

        logger.info(f"[EXECUTION] Entity_id lookup built: {len(lookup)} mappings")
        return lookup
