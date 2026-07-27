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

__all__ = ["DataEpoch", "classify_record", "classify_dataset", "DatasetClassification"]
