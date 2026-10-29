"""
System Intelligence Layer — Observer v1.

Read-only query layer over existing persistence and configuration.
Answers questions about the trading system without modifying it.

AUTHORITY BOUNDARIES:
    CAN:
        - Read runtime/heartbeat.json
        - Import core.config (read-only)
        - Read all 24 persistence datasets via logs/ directory
        - Reconstruct decision chains from existing records
        - Report system health from file timestamps

    CANNOT:
        - Modify configuration
        - Place broker orders
        - Write to any persistence dataset
        - Override risk controls
        - Make trading decisions
"""

from system_intelligence.observer import Observer

__all__ = ["Observer"]
