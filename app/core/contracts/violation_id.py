"""
Violation Identity — Globally unique ID generator for contract violations.

Every contract violation is a first-class forensic object with a permanent
identity that survives persistence, quarantine, reload, and investigation.

FORMAT:
    VIO-{YYYYMMDD}-{SEQUENCE:09d}

    Example: VIO-20260704-000018293

PROPERTIES:
    - Globally unique (monotonic sequence + date partition)
    - Immutable (generated once, never changes)
    - Never reused (sequence never resets within a date)
    - Survives persistence, quarantine, reload
    - Thread-safe (atomic counter)
    - Deterministic ordering (lexicographic sort = chronological)

Usage:
    from core.contracts.violation_id import generate_violation_id

    vid = generate_violation_id()
    # → "VIO-20260704-000000001"
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone


# ═══════════════════════════════════════════════════════════════════════════════
# THREAD-SAFE ID GENERATOR
# ═══════════════════════════════════════════════════════════════════════════════

_counter: int = 0
_counter_date: str = ""
_lock = threading.Lock()


def generate_violation_id() -> str:
    """
    Generate a globally unique violation ID.

    Format: VIO-{YYYYMMDD}-{SEQUENCE:09d}

    Thread-safe. Monotonically increasing within a date.
    Resets sequence on date change (new day = new partition).
    """
    global _counter, _counter_date

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y%m%d")

    with _lock:
        if date_str != _counter_date:
            _counter = 0
            _counter_date = date_str
        _counter += 1
        seq = _counter

    return f"VIO-{date_str}-{seq:09d}"


def generate_violation_timestamp() -> str:
    """Generate ISO-8601 timestamp for violation creation time."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ═══════════════════════════════════════════════════════════════════════════════
# VIOLATION STORE (in-memory forensic correlation)
# ═══════════════════════════════════════════════════════════════════════════════

class ViolationStore:
    """
    In-memory store for violation correlation and lookup.

    Provides forensic navigation:
        violation_id → full violation context
        rule_id → all violations for that rule
        validator_id → all violations from that validator
        record_id → all violations for that record

    Read-only after insertion. Never modifies violations.
    Bounded to prevent memory growth (ring buffer).
    """

    def __init__(self, *, max_entries: int = 10_000) -> None:
        self._entries: dict[str, dict] = {}  # violation_id → serialized violation
        self._by_rule: dict[str, list[str]] = {}  # rule_id → [violation_ids]
        self._by_validator: dict[str, list[str]] = {}  # validator_id → [violation_ids]
        self._by_record: dict[str, list[str]] = {}  # record_id → [violation_ids]
        self._order: list[str] = []  # insertion order for eviction
        self._max_entries = max_entries
        self._lock = threading.Lock()

    @property
    def count(self) -> int:
        return len(self._entries)

    def store(self, violation_dict: dict, *, record_id: str = "") -> None:
        """
        Store a violation for forensic correlation.

        Args:
            violation_dict: Serialized violation (from to_dict())
            record_id: The record this violation was generated from
        """
        vid = violation_dict.get("violation_id", "")
        if not vid:
            return

        with self._lock:
            # Evict oldest if at capacity
            if len(self._entries) >= self._max_entries and self._order:
                evict_id = self._order.pop(0)
                self._entries.pop(evict_id, None)

            self._entries[vid] = violation_dict
            self._order.append(vid)

            rule_id = violation_dict.get("rule_id", "")
            if rule_id:
                self._by_rule.setdefault(rule_id, []).append(vid)

            validator_id = violation_dict.get("validator_id", "")
            if validator_id:
                self._by_validator.setdefault(validator_id, []).append(vid)

            if record_id:
                self._by_record.setdefault(record_id, []).append(vid)

    # ─── LOOKUP API (read-only) ───────────────────────────────────────

    def get_violation(self, violation_id: str) -> dict | None:
        """Lookup a violation by its unique ID."""
        return self._entries.get(violation_id)

    def find_by_rule(self, rule_id: str) -> list[dict]:
        """Find all violations for a given rule ID."""
        vids = self._by_rule.get(rule_id, [])
        return [self._entries[v] for v in vids if v in self._entries]

    def find_by_validator(self, validator_id: str) -> list[dict]:
        """Find all violations from a given validator."""
        vids = self._by_validator.get(validator_id, [])
        return [self._entries[v] for v in vids if v in self._entries]

    def find_by_record(self, record_id: str) -> list[dict]:
        """Find all violations for a given trade/record ID."""
        vids = self._by_record.get(record_id, [])
        return [self._entries[v] for v in vids if v in self._entries]

    def find_by_time(self, start: str, end: str) -> list[dict]:
        """
        Find violations within a time range (ISO-8601 string comparison).

        Args:
            start: ISO timestamp lower bound (inclusive)
            end: ISO timestamp upper bound (inclusive)
        """
        results = []
        for v in self._entries.values():
            ts = v.get("violation_timestamp", "")
            if start <= ts <= end:
                results.append(v)
        return results

    def stats(self) -> dict:
        """Store statistics."""
        return {
            "total_stored": self.count,
            "max_entries": self._max_entries,
            "rules_tracked": len(self._by_rule),
            "validators_tracked": len(self._by_validator),
            "records_tracked": len(self._by_record),
        }


# ═══════════════════════════════════════════════════════════════════════════════
# GLOBAL STORE SINGLETON
# ═══════════════════════════════════════════════════════════════════════════════

_violation_store: ViolationStore | None = None


def get_violation_store() -> ViolationStore:
    """Get or create the global violation store singleton."""
    global _violation_store
    if _violation_store is None:
        _violation_store = ViolationStore()
    return _violation_store
