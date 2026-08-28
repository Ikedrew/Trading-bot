"""research_projection — read-only projector from logs/ capture to research_data/.

Phase 7B/7C contract:
  * logs/ is the RAW CAPTURE layer: opened read-only, never modified.
  * research_data/ is a NEW derived research layer (field-level reconciliation,
    never folder/file mirroring).
  * canonical lineage preserved verbatim; empty canonical roots stay empty.
  * outcome data stays strictly on the outcome side.
  * cursor-based incremental projection: no historical backfill; new runtime
    bytes are projected as they appear.
"""

from .ownership import (
    ASSESSMENT_DROPS,
    ASSESSMENT_EV_FIELDS,
    CANONICAL_FIELD,
    DECISION_AUDIT_DROPS,
    DECISION_TRACE_DROPS,
    DECISION_TRACE_RENAMES,
    EXECUTION_CONTEXT_DROPS,
    EXECUTION_RESULTS_DROPS,
    OUTCOME_FORBIDDEN_IN_EXECUTION,
    OWNERSHIP_REGISTRY,
    PROJECTOR_VERSION,
    RISK_DEVIATION_KEEP,
    SHADOW_DROPS,
    SHADOW_EVENT_TYPES,
    TRADE_JOURNAL_DROPS,
    TRADE_JOURNAL_KEEP,
    drop_nested,
    validate_canonical_root,
)
from .projector import Projector
from .manifest import write_manifest

__all__ = [
    "Projector",
    "write_manifest",
    "PROJECTOR_VERSION",
    "OWNERSHIP_REGISTRY",
    "validate_canonical_root",
    "CANONICAL_FIELD",
]
