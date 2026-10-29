"""
Shadow Optimisation — Main runner.

Processes opportunities/trades for all active shadow candidates.
NEVER places orders or affects the live bot.

SAFETY: This module imports NOTHING from execution/, MetaTrader5, or broker APIs.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from research_engine.v10.shadow.models import ShadowCandidate, ShadowComparison, ShadowStatus
from research_engine.v10.shadow.shadow_decision import apply_candidate_decision
from research_engine.v10.shadow.shadow_outcome import calculate_shadow_outcome, calculate_baseline_outcome
from research_engine.v10.shadow.shadow_registry import ShadowRegistry
from research_engine.v10.shadow.shadow_comparison import compute_shadow_metrics, evaluate_shadow_evidence

logger = logging.getLogger(__name__)


class ShadowRunner:
    """
    Orchestrates shadow testing for all active candidates.

    Flow per opportunity:
        1. For each active shadow candidate
        2. Apply candidate changes to determine virtual decision
        3. Compare against baseline decision
        4. Record comparison
        5. Update metrics

    SAFETY: Cannot call any broker execution method.
    """

    def __init__(self, shadow_dir: str | None = None):
        self._registry = ShadowRegistry(shadow_dir=shadow_dir)

    @property
    def registry(self) -> ShadowRegistry:
        return self._registry

    def start_shadow(
        self,
        candidate_id: str,
        change_definition: dict[str, Any],
        baseline_id: str = "",
        filters: dict[str, str] | None = None,
        target_questions: list[str] | None = None,
    ) -> ShadowCandidate:
        """
        Start shadow testing for a candidate.

        Does NOT affect the live bot in any way.
        """
        shadow_id = f"SHADOW_{candidate_id}_{int(time.time()) % 100000}"
        candidate = ShadowCandidate(
            shadow_id=shadow_id,
            candidate_id=candidate_id,
            baseline_id=baseline_id,
            change_definition=change_definition,
            filters=filters or {},
            target_questions=target_questions or [],
        )
        self._registry.add_candidate(candidate)
        logger.info(f"[SHADOW] Started: {shadow_id} for candidate {candidate_id}")
        return candidate

    def process_trade(
        self,
        trade: dict[str, Any],
        exit_price: float | None = None,
    ) -> list[ShadowComparison]:
        """
        Process a completed trade against all active shadow candidates.

        Args:
            trade: Completed trade record (flat dict with standard fields)
            exit_price: Actual exit price (defaults to trade's exit_price)

        Returns:
            List of comparisons generated (one per active candidate).
        """
        active = self._registry.list_active()
        if not active:
            return []

        actual_exit = exit_price or trade.get("exit_price", 0)
        comparisons = []

        for shadow_cand in active:
            # Check filters
            if not self._matches_filters(trade, shadow_cand.filters):
                shadow_cand.metrics["opportunities_seen"] = shadow_cand.metrics.get("opportunities_seen", 0) + 1
                continue

            shadow_cand.metrics["opportunities_seen"] = shadow_cand.metrics.get("opportunities_seen", 0) + 1
            shadow_cand.metrics["eligible_trades"] = shadow_cand.metrics.get("eligible_trades", 0) + 1

            # Apply candidate decision
            shadow_dec = apply_candidate_decision(trade, shadow_cand.change_definition)

            # Calculate outcomes
            baseline_out = calculate_baseline_outcome(trade)
            shadow_out = calculate_shadow_outcome(shadow_dec, actual_exit)

            # Determine baseline decision
            baseline_decision = "EXECUTE"  # If we have a trade record, baseline executed

            # Build comparison
            comp = ShadowComparison(
                comparison_id=f"CMP_{shadow_cand.shadow_id}_{shadow_cand.metrics.get('completed_comparisons', 0) + 1}",
                shadow_id=shadow_cand.shadow_id,
                candidate_id=shadow_cand.candidate_id,
                opportunity_id=trade.get("trade_id", ""),
                trade_id=trade.get("trade_id", ""),
                symbol=trade.get("symbol", ""),
                direction=trade.get("direction", ""),
                baseline_decision=baseline_decision,
                baseline_entry=trade.get("entry_price", 0),
                baseline_stop=trade.get("stop_loss", 0),
                baseline_target=trade.get("take_profit", 0),
                baseline_r=baseline_out["r_multiple"],
                baseline_pnl=baseline_out["pnl_direction"],
                shadow_decision=shadow_dec["decision"],
                shadow_entry=shadow_dec["entry_price"],
                shadow_stop=shadow_dec["stop_loss"],
                shadow_target=shadow_dec["take_profit"],
                shadow_r=shadow_out["r_multiple"],
                shadow_pnl=shadow_out["pnl_direction"],
                outcome_source="live_trade",
            )

            self._registry.add_comparison(comp)
            shadow_cand.metrics["shadow_trades"] = shadow_cand.metrics.get("shadow_trades", 0) + 1
            comparisons.append(comp)

        return comparisons

    def get_evidence(self, shadow_id: str) -> dict[str, Any]:
        """Get progressive evidence evaluation for a shadow candidate."""
        comparisons = self._registry.get_comparisons(shadow_id)
        return evaluate_shadow_evidence(comparisons)

    def get_metrics(self, shadow_id: str) -> dict[str, Any]:
        """Get aggregate shadow metrics."""
        comparisons = self._registry.get_comparisons(shadow_id)
        return compute_shadow_metrics(comparisons)

    def stop_shadow(self, shadow_id: str) -> None:
        """Stop a shadow test."""
        self._registry.update_status(shadow_id, ShadowStatus.COMPLETED)
        logger.info(f"[SHADOW] Completed: {shadow_id}")

    def _matches_filters(self, trade: dict, filters: dict[str, str]) -> bool:
        """Check if a trade matches shadow candidate filters."""
        if not filters:
            return True
        symbol = trade.get("symbol", "")
        for key, value in filters.items():
            if key == "instrument":
                if value.upper() == "FX":
                    fx = {"EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "USDJPY"}
                    if symbol not in fx:
                        return False
                elif symbol != value.upper():
                    return False
            elif key == "regime":
                regime = trade.get("regime", "") or trade.get("dt_v10_regime", "")
                if regime.upper() != value.upper():
                    return False
        return True
