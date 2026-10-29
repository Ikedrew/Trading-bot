"""
Shadow Reality Comparison Models — Data contracts for the Shadow→Reality Bridge.

This module defines the immutable observation record produced when a
V10_PRIMARY EXECUTE shadow trade is paired with its corresponding closed
real trade journal entry via correlation_id.

SEMANTIC RULE:
    delta_r = shadow_r - realised_gross_r

    This is an observed difference. It does NOT establish causation.
    V1 measures divergence only.

This module NEVER modifies production data or trading behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# COMPARISON STATUS
# ═══════════════════════════════════════════════════════════════════════════════

class ComparisonStatus:
    """Explicit classification of every shadow/journal observation."""

    MATCHED = "MATCHED"
    """Valid V10_PRIMARY EXECUTE shadow joined to a valid journal record
    with compatible identity and geometry."""

    SHADOW_ONLY = "SHADOW_ONLY"
    """Authoritative V10_PRIMARY EXECUTE shadow exists but no corresponding
    journal record currently exists. May indicate: pending trade, broker
    rejection, still-open position, or persistence gap."""

    REAL_ONLY = "REAL_ONLY"
    """Journal record exists but no authoritative V10_PRIMARY EXECUTE shadow exists."""

    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    """correlation_id joined but symbol or direction disagrees. DATA QUALITY FAILURE."""

    LEGACY = "LEGACY"
    """Record belongs to an excluded legacy population (V10SHADOW-* prefix,
    empty shadow_type, missing correlation_id, etc.)."""

    GEOMETRY_DIVERGED = "GEOMETRY_DIVERGED"
    """Shadow and real initial SL/TP geometry differ beyond tolerance.
    Comparison is preserved but flagged."""

    GEOMETRY_INVALID = "GEOMETRY_INVALID"
    """Initial risk distance is zero or invalid. Cannot compute R."""

    AMBIGUOUS = "AMBIGUOUS"
    """Multiple candidate records prevent deterministic pairing."""


# Geometry comparison tolerance (absolute price difference)
# This is intentionally generous — instruments range from 0.0001 (FX) to 100s (gold).
# Relative tolerance: 1e-6 of entry price is used instead.
GEOMETRY_RELATIVE_TOLERANCE = 1e-6


# ═══════════════════════════════════════════════════════════════════════════════
# SHADOW REALITY COMPARISON
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ShadowRealityComparison:
    """
    One paired comparison between a shadow prediction and its realised outcome.

    Immutable after creation. Represents a single empirical measurement of
    shadow-vs-reality correspondence for one executed trade decision.
    """

    # ─── IDENTITY ─────────────────────────────────────────────────────
    correlation_id: str = ""
    entity_id: str = ""
    symbol: str = ""
    direction: str = ""             # BUY | SELL

    # ─── SHADOW PREDICTION ────────────────────────────────────────────
    shadow_entry_price: float = 0.0
    shadow_exit_price: float = 0.0
    shadow_sl: float = 0.0
    shadow_tp: float = 0.0
    shadow_r: float = 0.0           # simulated_outcome.pnl_r_multiple (unchanged)
    shadow_exit_reason: str = ""    # stop_loss | take_profit | max_bars_timeout
    shadow_bars_held: int = 0
    shadow_mfe_r: float = 0.0
    shadow_mae_r: float = 0.0

    # ─── REALISED OUTCOME ─────────────────────────────────────────────
    real_entry_price: float = 0.0   # actual broker fill
    real_exit_price: float = 0.0    # actual close price
    real_initial_sl: float = 0.0    # SL as set on broker at entry
    real_initial_tp: float = 0.0    # TP as set on broker at entry
    realised_gross_r: float = 0.0   # computed from real prices + initial SL
    realised_net_pnl: float = 0.0   # account currency (includes commission/swap)
    real_exit_reason: str = ""      # close_reason from journal
    real_duration_seconds: float = 0.0
    real_max_favourable_price: float = 0.0
    commission: float = 0.0
    swap: float = 0.0

    # ─── DERIVED COMPARISON ───────────────────────────────────────────
    delta_r: float = 0.0            # shadow_r - realised_gross_r
    entry_slippage: float = 0.0     # real_entry_price - shadow_entry_price (signed)
    execution_slippage: float | None = None  # from execution_results if available
    exit_reason_match: bool = False
    geometry_match: bool = False

    # ─── CONTEXT ──────────────────────────────────────────────────────
    pattern: str = ""
    trade_horizon: str = ""
    spread_at_entry: float = 0.0
    timestamp_decision_utc: float = 0.0

    # ─── STATUS ───────────────────────────────────────────────────────
    comparison_status: str = ""     # ComparisonStatus value

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for persistence/reporting."""
        return {
            "correlation_id": self.correlation_id,
            "entity_id": self.entity_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "shadow_entry_price": self.shadow_entry_price,
            "shadow_exit_price": self.shadow_exit_price,
            "shadow_sl": self.shadow_sl,
            "shadow_tp": self.shadow_tp,
            "shadow_r": self.shadow_r,
            "shadow_exit_reason": self.shadow_exit_reason,
            "shadow_bars_held": self.shadow_bars_held,
            "shadow_mfe_r": self.shadow_mfe_r,
            "shadow_mae_r": self.shadow_mae_r,
            "real_entry_price": self.real_entry_price,
            "real_exit_price": self.real_exit_price,
            "real_initial_sl": self.real_initial_sl,
            "real_initial_tp": self.real_initial_tp,
            "realised_gross_r": self.realised_gross_r,
            "realised_net_pnl": self.realised_net_pnl,
            "real_exit_reason": self.real_exit_reason,
            "real_duration_seconds": self.real_duration_seconds,
            "real_max_favourable_price": self.real_max_favourable_price,
            "commission": self.commission,
            "swap": self.swap,
            "delta_r": self.delta_r,
            "entry_slippage": self.entry_slippage,
            "execution_slippage": self.execution_slippage,
            "exit_reason_match": self.exit_reason_match,
            "geometry_match": self.geometry_match,
            "pattern": self.pattern,
            "trade_horizon": self.trade_horizon,
            "spread_at_entry": self.spread_at_entry,
            "timestamp_decision_utc": self.timestamp_decision_utc,
            "comparison_status": self.comparison_status,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# COVERAGE REPORT
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ShadowRealityCoverageReport:
    """Population counts and coverage statistics."""

    # Source populations
    total_shadow_records: int = 0
    authoritative_v10_primary_execute: int = 0
    authoritative_v10_primary_no_trade: int = 0
    legacy_shadows: int = 0
    shadows_without_correlation_id: int = 0

    # Journal populations
    total_journal_records: int = 0
    journal_with_correlation_id: int = 0

    # Join results
    matched: int = 0
    shadow_only: int = 0
    real_only: int = 0
    identity_mismatch: int = 0
    geometry_diverged: int = 0
    geometry_invalid: int = 0
    ambiguous: int = 0

    # Derived rates
    match_rate: float = 0.0         # matched / authoritative_v10_primary_execute
    journal_coverage: float = 0.0   # journal_with_correlation_id / total_journal_records

    # Exclusion counts
    excluded_malformed: int = 0
    excluded_schema_mismatch: int = 0
    duplicate_shadow_correlation_ids: int = 0
    duplicate_journal_correlation_ids: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}
