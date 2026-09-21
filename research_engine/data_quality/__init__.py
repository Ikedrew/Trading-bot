"""
Data Quality Classification Layer.

Non-destructive classification of research records as LEGACY or CURRENT
based on field completeness. Does NOT modify, move, or delete any data.
"""

from research_engine.data_quality.classifier import (
    DataEpoch,
    classify_record,
    classify_dataset,
    DatasetClassification,
)
from research_engine.data_quality.execution_sizing import (
    eligible_for_fields,
    governed_record,
    EvidencePurpose,
    ExecutionSizingQuality,
    FillSizingAssessment,
    TradeSizingEligibility,
    annotate_record,
    build_fill_index,
    build_trade_eligibility,
    classify_distortion,
    classify_fill,
    eligibility_for_trade,
    extract_fill_price,
    extract_reference_entry,
    extract_submitted_sl,
    fill_key,
    filter_monetary_eligible,
    filter_price_r_eligible,
    is_eligible,
    is_primary_fill_record,
    reconciliation_counts,
    require_monetary_eligible,
    sizing_distortion,
)

__all__ = [
    "DataEpoch", "classify_record", "classify_dataset", "DatasetClassification",
    "eligible_for_fields", "governed_record", "EvidencePurpose", "ExecutionSizingQuality", "FillSizingAssessment",
    "TradeSizingEligibility", "annotate_record", "build_fill_index",
    "build_trade_eligibility", "classify_distortion", "classify_fill",
    "eligibility_for_trade", "extract_fill_price", "extract_reference_entry",
    "extract_submitted_sl", "fill_key", "filter_monetary_eligible",
    "filter_price_r_eligible", "is_eligible", "is_primary_fill_record",
    "reconciliation_counts", "require_monetary_eligible", "sizing_distortion",
]
