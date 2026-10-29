"""
Severity Model — Classification levels for contract violations.

INFO:     Informational only. Log, continue.
WARNING:  Minor drift. Log + audit entry, continue.
ERROR:    Record violates contract. Quarantine, prevent downstream propagation.
CRITICAL: Pipeline integrity compromised. Stop propagation at boundary, alert.
FATAL:    System integrity broken. Halt processing, require operator intervention.
"""

from __future__ import annotations

from enum import IntEnum


class Severity(IntEnum):
    """
    Contract violation severity levels.

    IntEnum so that comparisons work naturally:
        Severity.ERROR > Severity.WARNING  → True
    """

    INFO = 0
    WARNING = 1
    ERROR = 2
    CRITICAL = 3
    FATAL = 4

    @property
    def blocks_propagation(self) -> bool:
        """Whether this severity level prevents downstream propagation."""
        return self >= Severity.ERROR

    @property
    def requires_quarantine(self) -> bool:
        """Whether this severity level requires record quarantine."""
        return self >= Severity.ERROR

    @property
    def requires_alert(self) -> bool:
        """Whether this severity level requires an architecture alert."""
        return self >= Severity.CRITICAL

    @property
    def requires_halt(self) -> bool:
        """Whether this severity level requires processing halt."""
        return self >= Severity.FATAL
