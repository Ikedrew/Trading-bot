"""
Outcome Enrichment Layer.

Joins realised execution outcomes (r_multiple, etc.) from the Execution Universe
into Decision, Market, and Strategy universe records via the canonical entity_id.

This is NOT a new correlation mechanism — it reuses the existing entity_id join
established by the CR-001 fix (ExecutionUniverseBuilder enriches entity_id from
execution_results at build time).

Rules:
    - Only records with a valid execution match receive outcomes
    - NO_TRADE decisions do NOT receive fabricated outcomes
    - Unmatched records remain with r_multiple = None
    - Enrichment is deterministic and reproducible
    - No duplicate outcome joins (first match wins)
    - Universe populations are NOT modified — enrichment is applied post-build
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from research_engine.v10.universes.base import UniverseBuilder
from research_engine.v10.universes.models import Universe

logger = logging.getLogger(__name__)


@dataclass
class EnrichmentResult:
    """Result of outcome enrichment for one universe."""
    universe: str
    total_records: int = 0
    matched: int = 0
    unmatched: int = 0
    enrichment_fields: tuple[str, ...] = ("r_multiple", "execution_id", "exit_reason", "net_realised_pnl")

    def to_dict(self) -> dict[str, Any]:
        return {
            "universe": self.universe,
            "total_records": self.total_records,
            "matched": self.matched,
            "unmatched": self.unmatched,
            "match_rate": round(self.matched / self.total_records, 4) if self.total_records else 0,
            "enrichment_fields": list(self.enrichment_fields),
        }


class OutcomeEnrichment:
    """
    Enriches Decision/Market/Strategy universe records with execution outcomes.

    Uses the canonical entity_id join between Execution Universe and the other
    three universes. The Execution Universe's entity_id was already enriched
    from execution_results by the CR-001 fix.

    Usage:
        enrichment = OutcomeEnrichment(execution_builder)
        enrichment.enrich(decision_builder)
        enrichment.enrich(market_builder)
        enrichment.enrich(strategy_builder)
    """

    def __init__(self, execution_builder: UniverseBuilder):
        """
        Build the outcome lookup from the Execution Universe.

        Args:
            execution_builder: A built ExecutionUniverseBuilder with enriched entity_ids.
        """
        self._outcome_lookup: dict[str, dict[str, Any]] = {}
        self._build_lookup(execution_builder)

    def _build_lookup(self, execution_builder: UniverseBuilder) -> None:
        """Build entity_id → outcome mapping from execution records."""
        for record in execution_builder.records:
            entity_id = record.get("entity_id", "")
            # Skip records where entity_id is just the trade_id fallback (pos_NNNNN)
            if not entity_id or entity_id.startswith("pos_"):
                continue

            r_multiple = record.get("r_multiple")
            if r_multiple is None:
                continue

            # Only store first occurrence (no duplicates)
            if entity_id not in self._outcome_lookup:
                self._outcome_lookup[entity_id] = {
                    "r_multiple": r_multiple,
                    "execution_id": record.get("trade_id", ""),
                    "exit_reason": record.get("exit_reason", ""),
                    "net_realised_pnl": record.get("net_realised_pnl"),
                }

        logger.info(
            f"[OUTCOME_ENRICHMENT] Built lookup: {len(self._outcome_lookup)} "
            f"execution outcomes with valid entity_id"
        )

    @property
    def available_outcomes(self) -> int:
        """Number of execution outcomes available for enrichment."""
        return len(self._outcome_lookup)

    def enrich(self, builder: UniverseBuilder) -> EnrichmentResult:
        """
        Enrich a universe's records with execution outcomes.

        Modifies records in-place by adding:
            - r_multiple (float or remains None)
            - execution_match (bool)
            - outcome_available (bool)
            - execution_id (str, the matching trade_id)
            - exit_reason (str)
            - net_realised_pnl (float or None)

        Args:
            builder: A built universe builder (Decision, Market, or Strategy).

        Returns:
            EnrichmentResult with counts.
        """
        universe_name = builder.universe_type.value
        records = builder.records
        matched = 0
        unmatched = 0

        for record in records:
            entity_id = record.get("entity_id", "")

            if entity_id and entity_id in self._outcome_lookup:
                outcome = self._outcome_lookup[entity_id]
                record["r_multiple"] = outcome["r_multiple"]
                record["execution_match"] = True
                record["outcome_available"] = True
                record["execution_id"] = outcome["execution_id"]
                record["exit_reason"] = outcome.get("exit_reason", "")
                record["net_realised_pnl"] = outcome.get("net_realised_pnl")
                matched += 1
            else:
                # Explicitly mark as unmatched — no fabricated outcome
                record["execution_match"] = False
                record["outcome_available"] = False
                # r_multiple remains as-is (None)
                unmatched += 1

        result = EnrichmentResult(
            universe=universe_name,
            total_records=len(records),
            matched=matched,
            unmatched=unmatched,
        )

        logger.info(
            f"[OUTCOME_ENRICHMENT] {universe_name}: "
            f"{matched}/{len(records)} enriched ({result.to_dict()['match_rate']:.1%})"
        )
        return result

    def enrich_all(
        self,
        builders: dict[Universe, UniverseBuilder],
    ) -> dict[str, EnrichmentResult]:
        """
        Enrich all non-execution universes with outcomes.

        Args:
            builders: All built universe builders (including Execution).

        Returns:
            Dict of universe → EnrichmentResult.
        """
        results = {}
        for universe, builder in builders.items():
            if universe == Universe.EXECUTION:
                continue  # Don't enrich Execution with itself
            if not builder.is_built:
                continue
            results[universe.value] = self.enrich(builder)
        return results
