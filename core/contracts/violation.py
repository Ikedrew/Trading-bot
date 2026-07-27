"""
ContractViolation — First-class forensic object with permanent identity.

Every violation is:
    - Uniquely identified (violation_id)
    - Timestamped at creation
    - Classified on two axes (Severity × Confidence)
    - Traceable to validator + rule + record
    - Immutable after creation

IDENTITY HIERARCHY:
    Validator Identity (PERSISTENCE_001)
        → Rule Identity (PERSIST_TIME_003)
            → Violation Identity (VIO-20260704-000018293)

The violation_id enables complete forensic correlation:
    Discord Alert → Violation → Quarantine → Original Payload
        → Validator → Contract Rule → Documentation → Analytics
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.contracts.confidence import Confidence, classify_confidence
from core.contracts.severity import Severity
from core.contracts.violation_id import generate_violation_id, generate_violation_timestamp


@dataclass(frozen=True)
class ContractViolation:
    """
    A single contract violation — a first-class forensic object.

    IDENTITY: Every occurrence has a globally unique violation_id
    (VIO-{YYYYMMDD}-{SEQ}) that survives persistence, quarantine,
    reload, and investigation.

    DUAL-AXIS CLASSIFICATION:
        severity:         How serious if real (INFO → FATAL)
        confidence:       How certain it's real (0–100%)
        confidence_level: Classified confidence (VERY_LOW → VERY_HIGH)

    TRACEABILITY: Every violation links to:
        validator_id → which validator detected it
        rule_id     → which specific rule fired
        violation_id → this unique occurrence
    """

    # ─── REQUIRED FIELDS ──────────────────────────────────────────────
    contract_name: str
    validator_name: str
    severity: Severity
    reason: str

    # ─── CONFIDENCE (independent of severity) ─────────────────────────
    confidence: int = 100               # 0–100 numeric certainty
    confidence_level: Confidence = Confidence.VERY_HIGH  # Classified level

    # ─── CONTRACT RULE IDENTITY ───────────────────────────────────────
    rule_id: str = ""                   # Immutable rule identifier
    rule_title: str = ""                # Short rule title for display

    # ─── VALIDATOR IDENTITY (governance traceability) ─────────────────
    validator_id: str = ""
    validator_version: int = 0
    layer: str = ""
    field_name: str = ""
    expected: Any = None
    actual: Any = None
    contract_version: str = "1.0"

    # ─── VIOLATION IDENTITY (forensic correlation) ────────────────────
    # Auto-generated at creation. Globally unique. Immutable.
    violation_id: str = ""
    violation_timestamp: str = ""

    # ─── DECISION SPINE (global correlation) ──────────────────────────
    # Links this violation to its originating decision cycle.
    # Propagated from the decision entry point, never regenerated.
    correlation_id: str = ""

    def __post_init__(self) -> None:
        """Auto-generate violation_id, timestamp, and confidence_level."""
        # Auto-generate violation_id if not provided
        if not self.violation_id:
            object.__setattr__(self, "violation_id", generate_violation_id())
        if not self.violation_timestamp:
            object.__setattr__(self, "violation_timestamp", generate_violation_timestamp())

        # Auto-classify confidence_level from numeric confidence
        computed_level = classify_confidence(self.confidence)
        if self.confidence_level == Confidence.VERY_HIGH and self.confidence < 81:
            object.__setattr__(self, "confidence_level", computed_level)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for JSON persistence."""
        return {
            "violation_id": self.violation_id,
            "violation_timestamp": self.violation_timestamp,
            "correlation_id": self.correlation_id,
            "contract_name": self.contract_name,
            "contract_version": self.contract_version,
            "validator_name": self.validator_name,
            "validator_id": self.validator_id,
            "validator_version": self.validator_version,
            "rule_id": self.rule_id,
            "rule_title": self.rule_title,
            "severity": self.severity.name,
            "severity_level": int(self.severity),
            "confidence": self.confidence,
            "confidence_level": self.confidence_level.value,
            "reason": self.reason,
            "layer": self.layer,
            "field_name": self.field_name,
            "expected": str(self.expected) if self.expected is not None else None,
            "actual": str(self.actual) if self.actual is not None else None,
        }
