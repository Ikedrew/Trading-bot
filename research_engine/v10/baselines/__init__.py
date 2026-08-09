"""
V10 Baseline Snapshot System.

Versioned reference points that capture bot state, configuration,
performance, and dataset identity before optimisation begins.

Usage:
    from research_engine.v10.baselines import SnapshotBuilder, SnapshotRegistry

    builder = SnapshotBuilder()
    snapshot = builder.build()

    registry = SnapshotRegistry()
    registry.save(snapshot)
    latest = registry.latest()
"""

from research_engine.v10.baselines.models import BaselineSnapshot
from research_engine.v10.baselines.snapshot_builder import SnapshotBuilder
from research_engine.v10.baselines.snapshot_registry import SnapshotRegistry
from research_engine.v10.baselines.snapshot_compare import compare_snapshots

__all__ = ["BaselineSnapshot", "SnapshotBuilder", "SnapshotRegistry", "compare_snapshots"]
