"""
Data Quality Classifier — Non-destructive epoch labelling.

Classifies records as LEGACY or CURRENT based on lineage field completeness.
Never modifies source data. Classification is computed at read time.

LEGACY:
    Records generated before entity_id/correlation_id propagation was
    fully wired. These records may have:
    - Missing entity_id
    - Combined strategy_horizon format (e.g. "CONTINUATION_SCALP")
    - Missing h4_regime / h1_bias / market_phase
    - HORIZON- prefix correlation_ids (non-joinable)

CURRENT:
    Records generated after lineage propagation. Must have:
    - Valid entity_id (non-empty)
    - Clean strategy field (REVERSAL/CONTINUATION/FALSE_BREAK or empty)
    - Independent trade_horizon field (SCALP/INTRADAY/EXTENDED or empty)

TRANSITIONAL:
    Records with partial lineage (some fields present, some missing).
    Usable for pattern/outcome research but not for full lifecycle joins.

Usage:
    from research_engine.data_quality import classify_record, classify_dataset

    epoch = classify_record(record)  # DataEpoch.LEGACY / CURRENT / TRANSITIONAL

    classification = classify_dataset(records)
    print(classification.summary)

This module is READ-ONLY. It does NOT modify any records or data files.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# EPOCH CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════════════════


class DataEpoch(str, Enum):
    """Data quality classification epoch."""
    CURRENT = "CURRENT"           # Full lineage, clean fields
    TRANSITIONAL = "TRANSITIONAL" # Partial lineage (some fields present)
    LEGACY = "LEGACY"             # Pre-lineage data (missing key fields)


# ═══════════════════════════════════════════════════════════════════════════════
# CLASSIFICATION RULES
# ═══════════════════════════════════════════════════════════════════════════════

_VALID_STRATEGIES = frozenset({"REVERSAL", "CONTINUATION", "FALSE_BREAK", ""})
_VALID_HORIZONS = frozenset({"SCALP", "INTRADAY", "EXTENDED", ""})
_CONTAMINATED_SUFFIXES = ("_SCALP", "_INTRADAY", "_EXTENDED")


def classify_record(record: dict[str, Any]) -> DataEpoch:
    """
    Classify a single record as CURRENT, TRANSITIONAL, or LEGACY.

    Classification is based on lineage field completeness:
    - CURRENT: entity_id present + clean strategy + no contamination
    - TRANSITIONAL: some lineage fields present but incomplete
    - LEGACY: missing entity_id or contaminated strategy

    This function NEVER modifies the record.
    """
    # Extract fields (handle nested shadow trade format)
    identity = record.get("identity", {})
    decision_snapshot = record.get("decision_snapshot", {})

    entity_id = identity.get("entity_id", "") or record.get("entity_id", "")
    correlation_id = identity.get("correlation_id", "") or record.get("correlation_id", "")
    strategy = (
        identity.get("strategy_id", "")
        or decision_snapshot.get("strategy", "")
        or record.get("strategy", "")
    )
    h4_regime = (
        decision_snapshot.get("h4_regime", "")
        or decision_snapshot.get("regime", "")
        or record.get("h4_regime", "")
        or record.get("regime", "")
    )
    trade_horizon = decision_snapshot.get("trade_horizon", "") or record.get("trade_horizon", "")

    # Rule 1: Strategy contamination → LEGACY
    if strategy and any(strategy.endswith(suffix) or suffix[1:] in strategy for suffix in _CONTAMINATED_SUFFIXES):
        return DataEpoch.LEGACY

    # Rule 2: HORIZON- prefix correlation_id → LEGACY (non-joinable)
    if correlation_id.startswith("HORIZON-") and not entity_id:
        return DataEpoch.LEGACY

    # Rule 3: Full lineage → CURRENT
    # Remediation Stage 8/9: a CURRENT-epoch record REQUIRES the explicit
    # canonical_opportunity_id lineage root. Records that otherwise look
    # current but lack it fail the lineage contract → LEGACY.
    canonical = identity.get("canonical_opportunity_id", "") or record.get("canonical_opportunity_id", "")
    has_entity = bool(entity_id)
    has_clean_strategy = strategy in _VALID_STRATEGIES
    has_regime = h4_regime not in ("", "UNKNOWN", "TRANSITIONAL")

    if has_entity and has_clean_strategy:
        if has_regime:
            # Would be CURRENT-epoch → requires the canonical lineage root.
            if not canonical:
                return DataEpoch.LEGACY  # fails the canonical lineage contract
            return DataEpoch.CURRENT
        return DataEpoch.TRANSITIONAL

    # Rule 4: Partial — has some useful fields but not full lineage
    has_correlation = bool(correlation_id) and not correlation_id.startswith("HORIZON-")
    has_outcome = bool(
        record.get("simulated_outcome", {}).get("pnl_r_multiple") is not None
        or record.get("pnl_r_multiple") is not None
    )

    if (has_correlation or has_outcome) and has_clean_strategy:
        return DataEpoch.TRANSITIONAL

    # Rule 5: Everything else → LEGACY
    return DataEpoch.LEGACY


# ═══════════════════════════════════════════════════════════════════════════════
# DATASET CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class DatasetClassification:
    """Result of classifying an entire dataset."""
    total_records: int = 0
    current_count: int = 0
    transitional_count: int = 0
    legacy_count: int = 0

    @property
    def current_pct(self) -> float:
        return self.current_count / self.total_records if self.total_records > 0 else 0.0

    @property
    def transitional_pct(self) -> float:
        return self.transitional_count / self.total_records if self.total_records > 0 else 0.0

    @property
    def legacy_pct(self) -> float:
        return self.legacy_count / self.total_records if self.total_records > 0 else 0.0

    @property
    def usable_count(self) -> int:
        """Records usable for research (CURRENT + TRANSITIONAL)."""
        return self.current_count + self.transitional_count

    @property
    def usable_pct(self) -> float:
        return self.usable_count / self.total_records if self.total_records > 0 else 0.0

    @property
    def summary(self) -> str:
        return (
            f"Total: {self.total_records} | "
            f"CURRENT: {self.current_count} ({self.current_pct:.0%}) | "
            f"TRANSITIONAL: {self.transitional_count} ({self.transitional_pct:.0%}) | "
            f"LEGACY: {self.legacy_count} ({self.legacy_pct:.0%})"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_records": self.total_records,
            "current": {"count": self.current_count, "pct": round(self.current_pct, 4)},
            "transitional": {"count": self.transitional_count, "pct": round(self.transitional_pct, 4)},
            "legacy": {"count": self.legacy_count, "pct": round(self.legacy_pct, 4)},
            "usable": {"count": self.usable_count, "pct": round(self.usable_pct, 4)},
        }


def classify_dataset(records: list[dict[str, Any]]) -> DatasetClassification:
    """
    Classify an entire dataset by epoch.

    Returns aggregate counts and percentages for each epoch.
    This function NEVER modifies any records.
    """
    result = DatasetClassification(total_records=len(records))

    for record in records:
        epoch = classify_record(record)
        if epoch == DataEpoch.CURRENT:
            result.current_count += 1
        elif epoch == DataEpoch.TRANSITIONAL:
            result.transitional_count += 1
        else:
            result.legacy_count += 1

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# FILTERING HELPERS (non-destructive — returns new lists, never modifies source)
# ═══════════════════════════════════════════════════════════════════════════════


def filter_current(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return only CURRENT epoch records. Does NOT modify source list."""
    return [r for r in records if classify_record(r) == DataEpoch.CURRENT]


def filter_usable(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return CURRENT + TRANSITIONAL records. Does NOT modify source list."""
    return [r for r in records if classify_record(r) in (DataEpoch.CURRENT, DataEpoch.TRANSITIONAL)]


def filter_exclude_legacy(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return all non-LEGACY records. Alias for filter_usable()."""
    return filter_usable(records)
