"""
Research Engine — Dataset Evidence Consumers.

Consumers for the Step-4 connected V1 datasets. Each consumer:
    - reads a canonical V1 dataset ONLY through the sanctioned S3 data-access
      layer (research_engine.data_access.loaders / .s3_source);
    - has a clearly defined research purpose;
    - reports lineage coverage so ambiguous unlocks never silently degrade;
    - never changes trading behaviour, collection, schemas, or V1 persistence.

Packages:
    horizon_candidates     selected-vs-rejected horizon comparison (A)
    strategy_candidates    rejected-vs-selected strategy candidate analysis (A)
    execution_attempts     execution-quality diagnostics (B)
    management_actions     management-effectiveness analysis (B)

Run all consumers:
    python -m research_engine.evidence
"""

from __future__ import annotations

from research_engine.evidence.execution_attempts import (  # noqa: F401
    execution_attempt_evidence,
)
from research_engine.evidence.horizon_candidates import (  # noqa: F401
    horizon_candidate_evidence,
)
from research_engine.evidence.management_actions import (  # noqa: F401
    management_actions_evidence,
)
from research_engine.evidence.registry import (  # noqa: F401
    CONSUMED_EVIDENCE_DATASETS,
    DATASET_EVIDENCE_CONSUMERS,
    run_dataset_evidence,
)
from research_engine.evidence.strategy_candidates import (  # noqa: F401
    strategy_candidate_evidence,
)

__all__ = [
    "horizon_candidate_evidence",
    "strategy_candidate_evidence",
    "execution_attempt_evidence",
    "management_actions_evidence",
    "DATASET_EVIDENCE_CONSUMERS",
    "CONSUMED_EVIDENCE_DATASETS",
    "run_dataset_evidence",
]