"""V10 Pipeline Events — Stage completion emission and validation.

Each V10 pipeline stage emits a structured event when complete.
Events share the same observation_id and follow strict ordering.

Events are used for:
  - Runtime observability
  - S3 consistency validation
  - Research dataset integrity
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# EVENT TYPES (strict ordering)
# ═══════════════════════════════════════════════════════════════

STAGE_ORDER = [
    "V10_MARKET_STATE_COMPLETE",
    "V10_OPPORTUNITY_COMPLETE",
    "V10_STRATEGY_COMPLETE",
    "V10_HORIZON_COMPLETE",
    "V10_ENTRY_COMPLETE",
    "V10_RISK_COMPLETE",
    "V10_EXECUTION_COMPLETE",
    "V10_DECISION_COMPLETE",
]


@dataclass
class V10Event:
    """Structured V10 pipeline event."""
    event_type: str
    observation_id: str
    symbol: str
    timestamp_utc: float
    engine_version: str = "V10"
    stage: str = ""
    status: str = ""  # COMPLETE / REJECTED / ERROR
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "observation_id": self.observation_id,
            "symbol": self.symbol,
            "timestamp_utc": self.timestamp_utc,
            "engine_version": self.engine_version,
            "stage": self.stage,
            "status": self.status,
            "payload": self.payload,
        }


# ═══════════════════════════════════════════════════════════════
# EVENT COLLECTOR (per pipeline evaluation)
# ═══════════════════════════════════════════════════════════════


class PipelineEventCollector:
    """Collects events for a single pipeline evaluation.
    
    Validates ordering and completeness.
    """

    def __init__(self, observation_id: str, symbol: str, timestamp_utc: float):
        self.observation_id = observation_id
        self.symbol = symbol
        self.timestamp_utc = timestamp_utc
        self.events: list[V10Event] = []
        self._emitted_stages: list[str] = []

    def emit(self, event_type: str, status: str = "COMPLETE", payload: dict | None = None) -> None:
        """Emit a stage event. Validates ordering."""
        event = V10Event(
            event_type=event_type,
            observation_id=self.observation_id,
            symbol=self.symbol,
            timestamp_utc=self.timestamp_utc,
            stage=event_type.replace("V10_", "").replace("_COMPLETE", "").replace("_REJECTED", ""),
            status=status,
            payload=payload or {},
        )
        self.events.append(event)
        self._emitted_stages.append(event_type)

    @property
    def complete(self) -> bool:
        """True if DECISION_COMPLETE was emitted."""
        return "V10_DECISION_COMPLETE" in self._emitted_stages

    @property
    def stage_count(self) -> int:
        return len(self._emitted_stages)

    def validate_ordering(self) -> tuple[bool, list[str]]:
        """Validate that emitted events follow correct order."""
        violations: list[str] = []

        for i, emitted in enumerate(self._emitted_stages):
            if emitted not in STAGE_ORDER:
                continue  # Custom events are allowed
            expected_idx = STAGE_ORDER.index(emitted)
            # Check no later-order event was emitted before this one
            for prev in self._emitted_stages[:i]:
                if prev in STAGE_ORDER:
                    prev_idx = STAGE_ORDER.index(prev)
                    if prev_idx > expected_idx:
                        violations.append(f"{emitted} emitted after {prev} (wrong order)")

        return len(violations) == 0, violations


# ═══════════════════════════════════════════════════════════════
# TIMESTAMP VALIDATION
# ═══════════════════════════════════════════════════════════════

# Acceptable timestamp range: 2025-01-01 to 2030-01-01
_MIN_VALID_TS = 1735689600.0   # 2025-01-01 UTC
_MAX_VALID_TS = 1893456000.0   # 2030-01-01 UTC


def validate_timestamp(timestamp: float) -> tuple[bool, str]:
    """
    Validate a record timestamp is real and within acceptable range.

    Returns (valid, reason).
    """
    if timestamp is None:
        return False, "timestamp is None"
    if not isinstance(timestamp, (int, float)):
        return False, f"timestamp is not numeric: {type(timestamp)}"
    if timestamp == 0:
        return False, "timestamp is zero (epoch default)"
    if timestamp < _MIN_VALID_TS:
        return False, f"timestamp {timestamp} is before 2025-01-01 (stale/default)"
    if timestamp > _MAX_VALID_TS:
        return False, f"timestamp {timestamp} is after 2030-01-01 (impossible future)"
    return True, "valid"


def validate_record_timestamp(record: dict) -> dict[str, Any]:
    """
    Validate timestamp in a decision/execution/outcome record.
    
    Returns a validation result dict.
    """
    ts = record.get("timestamp_utc", 0)
    valid, reason = validate_timestamp(ts)

    from datetime import datetime, timezone
    try:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc) if ts > 0 else None
        date_str = dt.strftime("%Y-%m-%d %H:%M:%S UTC") if dt else "N/A"
    except (OSError, ValueError):
        date_str = "INVALID"
        valid = False
        reason = f"Cannot convert timestamp {ts}"

    return {
        "record_id": record.get("observation_id", record.get("decision_id", "unknown")),
        "timestamp": ts,
        "converted_date": date_str,
        "status": "PASS" if valid else "FAIL",
        "reason": reason,
    }
