"""
Outcome Universe Builder.

Provides the realised economic results of completed trades as an independent
analytical population.

Source: The Execution Universe's completed trade records (which already contain
r_multiple, net_realised_pnl, and all realised economic facts).

Design decision: The Outcome Universe does NOT duplicate the underlying data.
It wraps the ExecutionUniverseBuilder's completed-trade records and presents
them through the Outcome ownership lens — exposing only the fields that the
Outcome contract owns (realised economic facts) while preserving joined context
fields for cross-universe analysis.

Grain: 1 record = 1 completed trade with a realised economic result.

This universe owns:
    - Realised R-multiple
    - Realised net P&L
    - Win/loss classification
    - Exit reason (as an economic outcome fact)
    - Trade duration (as an observed outcome fact)
    - Realised costs (spread, commission, swap)

This universe does NOT own:
    - Whether the trade should have been taken (Decision)
    - Strategy intent or correctness (Strategy)
    - Risk policy or authorisation (Risk)
    - Market state interpretation (Market)
    - Mechanical execution quality (Execution)
"""

from __future__ import annotations

import logging
from typing import Any

from research_engine.v10.universes.base import UniverseBuilder
from research_engine.v10.universes.models import Population, Universe

logger = logging.getLogger(__name__)


class OutcomeUniverseBuilder(UniverseBuilder):
    """
    Builds the Outcome Universe from completed Execution Universe records.

    This is a thin analytical wrapper — it consumes the already-built
    ExecutionUniverseBuilder's records rather than reading raw source data
    independently. This avoids data duplication while establishing clear
    analytical ownership.

    Usage:
        exe_builder = ExecutionUniverseBuilder()
        exe_builder.build()
        outcome_builder = OutcomeUniverseBuilder(execution_builder=exe_builder)
        outcome_builder.build()
    """

    def __init__(self, execution_builder: "UniverseBuilder | None" = None):
        super().__init__()
        self._execution_builder = execution_builder

    @property
    def universe_type(self) -> Universe:
        return Universe.OUTCOME

    def set_execution_builder(self, builder: "UniverseBuilder") -> None:
        """Set the source execution builder (for deferred initialisation)."""
        self._execution_builder = builder

    def load(self) -> int:
        """Load is a no-op — source comes from ExecutionUniverseBuilder."""
        if self._execution_builder is None:
            logger.warning("[OUTCOME] No execution builder provided")
            return 0
        return len(self._execution_builder.records)

    def build(self) -> list[dict[str, Any]]:
        if self._execution_builder is None or not self._execution_builder.is_built:
            logger.warning(
                "[OUTCOME] Cannot build — ExecutionUniverseBuilder not available or not built"
            )
            self._records = []
            self._built = True
            self._metadata = self._generate_metadata(
                records=[],
                source_files=(),
                populations=(
                    Population.ALL_OUTCOMES.value,
                    Population.OUTCOME_WINS.value,
                    Population.OUTCOME_LOSSES.value,
                ),
                exclusions={"total": 0, "reasons": {}, "note": "No execution builder available"},
            )
            return []

        # Every Execution record with r_multiple is a valid Outcome observation
        # (ExecutionUniverseBuilder already excludes records without r_multiple)
        source_records = self._execution_builder.records
        records = []
        excluded_missing_r = 0

        for rec in source_records:
            if rec.get("r_multiple") is None:
                excluded_missing_r += 1
                continue
            records.append(self._normalise(rec))

        exclusions = {
            "total": excluded_missing_r,
            "reasons": {
                "missing_r_multiple": excluded_missing_r,
            },
            "source_records": len(source_records),
            "included_records": len(records),
        }

        self._records = records
        self._built = True

        # Source files come from the Execution builder
        exe_meta = self._execution_builder.metadata
        source_files = exe_meta.source_files

        self._metadata = self._generate_metadata(
            records=records,
            source_files=source_files,
            populations=(
                Population.ALL_OUTCOMES.value,
                Population.OUTCOME_WINS.value,
                Population.OUTCOME_LOSSES.value,
            ),
            exclusions=exclusions,
        )
        logger.info(
            f"[OUTCOME] Built {len(records)} outcome records "
            f"from {len(source_records)} execution records"
        )
        return records

    def get_population(self, population: Population) -> list[dict[str, Any]]:
        records = self.records

        if population == Population.ALL_OUTCOMES:
            return records
        elif population == Population.OUTCOME_WINS:
            return [r for r in records if r.get("r_multiple", 0) > 0]
        elif population == Population.OUTCOME_LOSSES:
            return [r for r in records if r.get("r_multiple", 0) <= 0]

        # Fall back to Execution populations for backward compatibility
        if population == Population.ALL_TRADES:
            return records
        elif population == Population.WINNING_TRADES:
            return [r for r in records if r.get("r_multiple", 0) > 0]
        elif population == Population.LOSING_TRADES:
            return [r for r in records if r.get("r_multiple", 0) <= 0]

        logger.warning(f"[OUTCOME] Unknown population: {population.value}")
        return []

    def _normalise(self, exe_record: dict[str, Any]) -> dict[str, Any]:
        """
        Project an Execution record into the Outcome ownership lens.

        Preserves all fields from the source record (for cross-universe joins)
        but the Outcome-authoritative fields are:
            r_multiple, net_realised_pnl, exit_reason, duration_seconds,
            gross_profit, commission, swap, direction, symbol

        Other fields (score, regime, family, etc.) are joined context —
        present for convenience but not Outcome-owned.
        """
        # Return the full record — Outcome is a view over Execution data
        # with clear ownership semantics defined by the contract.
        # No field duplication or transformation needed.
        return exe_record
