"""
Feature Resolver — Stamps feature_version on events at emit-time.

This module is responsible for tagging every event with the feature version
that was active when the event was produced. It does NOT re-derive or
recompute any trading features — those are already resolved by the pipeline
before reaching emit().

Design Principles:
    - feature_version is a LABEL, not a computation trigger
    - The actual feature logic lives in the pipeline modules (strategy detection,
      scoring engine, etc.) — this module just stamps which version was running
    - feature_version is immutable after emit (same as pattern, regime, etc.)
    - Historical events retain their original feature_version forever

Why this is NOT a recomputation layer:
    By the time an event reaches emit(), all trading features (pattern, regime,
    bias, score, EV) have already been computed by the live pipeline. This
    resolver only stamps the version tag so Athena can partition queries.

    Re-deriving features at emit-time would violate the single-responsibility
    principle and create circular dependencies. The pipeline computes; emit()
    records.

Usage:
    from core.feature_resolver import stamp_feature_version

    # Inside emit(), after canonical field resolution:
    stamp_feature_version(event)

    # For replay with specific version:
    stamp_feature_version(event, override_version=1)
"""

from __future__ import annotations

from typing import Any

from core.feature_registry import CURRENT_FEATURE_VERSION


def stamp_feature_version(
    event: dict[str, Any],
    *,
    override_version: int | None = None,
) -> dict[str, Any]:
    """
    Stamp the feature version on an event.

    Called inside emit() AFTER canonical field resolution. The event
    already has all computed features — this just adds the version tag
    indicating which logic produced them.

    Args:
        event: Fully-resolved event dict (pattern, regime, bias, etc. present)
        override_version: Force a specific version (for replay/testing only).
                         Production always uses CURRENT_FEATURE_VERSION.

    Returns:
        Same event dict with feature_version stamped. Mutates in-place.
    """
    version = override_version if override_version is not None else CURRENT_FEATURE_VERSION
    event["feature_version"] = version
    return event


def detect_feature_version(event: dict[str, Any]) -> int:
    """
    Detect the feature version of a stored event.

    Args:
        event: Event dict from storage (local JSONL or S3)

    Returns:
        Feature version integer. Defaults to 1 for legacy events without tag.
    """
    version = event.get("feature_version")
    if isinstance(version, int) and version > 0:
        return version
    # Legacy events (pre-feature-versioning) are implicitly v1
    return 1


def ensure_feature_version(event: dict[str, Any]) -> dict[str, Any]:
    """
    Ensure an event has a feature_version field (for read-time normalisation).

    If the event already has a valid feature_version, it's unchanged.
    If missing (legacy data), stamps feature_version=1.

    This is the READ-TIME counterpart to stamp_feature_version.
    Used in read_stream() and replay to normalise historical events.

    Args:
        event: Event dict from storage

    Returns:
        Same event dict with feature_version guaranteed present.
    """
    if not isinstance(event.get("feature_version"), int):
        event["feature_version"] = 1
    return event
