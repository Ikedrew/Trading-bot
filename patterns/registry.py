"""
Pattern registry — registration, discovery, and batch detection.

All pattern modules register themselves here on import.
The registry provides a single entry point for detecting all registered patterns.
"""

from __future__ import annotations

import logging
from typing import Type

from data.mt5_data import Candle
from patterns.base import PatternDetector
from strategy.signals import Signal

logger = logging.getLogger(__name__)

# ─── REGISTRY STATE ───────────────────────────────────────────────────────────

_registry: dict[str, PatternDetector] = {}


# ─── REGISTRATION API ─────────────────────────────────────────────────────────

def register(detector: PatternDetector) -> None:
    """
    Register a pattern detector instance.
    Raises ValueError if a pattern with the same name is already registered.
    """
    if detector.name in _registry:
        raise ValueError(f"Pattern '{detector.name}' is already registered")
    _registry[detector.name] = detector
    logger.debug("[PATTERN_REGISTRY] registered: %s (bars=%d)", detector.name, detector.bar_count)


def register_class(cls: Type[PatternDetector]) -> Type[PatternDetector]:
    """
    Class decorator — instantiates and registers a PatternDetector subclass.

    Usage:
        @register_class
        class HammerPattern(PatternDetector):
            ...
    """
    instance = cls()
    register(instance)
    return cls


# ─── DISCOVERY API ────────────────────────────────────────────────────────────

def get(name: str) -> PatternDetector | None:
    """Get a registered pattern by name. Returns None if not found."""
    return _registry.get(name)


def all_patterns() -> list[PatternDetector]:
    """Return all registered pattern detectors (ordered by registration)."""
    return list(_registry.values())


def pattern_names() -> list[str]:
    """Return names of all registered patterns."""
    return list(_registry.keys())


def count() -> int:
    """Return number of registered patterns."""
    return len(_registry)


# ─── DETECTION API ────────────────────────────────────────────────────────────

def detect_all(candles: list[Candle], closed_index: int) -> list[Signal]:
    """
    Run all registered pattern detectors against the given closed bar.

    Returns aggregated list of Signal objects from all detectors.
    Never raises — individual detector failures are logged and skipped.
    """
    if closed_index < 0 or closed_index >= len(candles):
        return []

    signals: list[Signal] = []
    for detector in _registry.values():
        # Skip detectors that need more bars than available
        if closed_index < detector.bar_count - 1:
            continue
        try:
            result = detector.detect(candles, closed_index)
            if result:
                # Stamp detector version onto signals that don't have one
                det_version = detector.version
                for sig in result:
                    if not sig.version:
                        signals.append(Signal(
                            pattern=sig.pattern,
                            side=sig.side,
                            bar_index=sig.bar_index,
                            bar_time=sig.bar_time,
                            confidence=sig.confidence,
                            version=det_version,
                        ))
                    else:
                        signals.append(sig)
        except Exception as exc:
            logger.warning(
                "[PATTERN_DETECT_ERROR] pattern=%s error=%s — skipping",
                detector.name, exc,
            )
    return signals


# ─── AUTO-DISCOVERY ───────────────────────────────────────────────────────────

def load_all_patterns() -> None:
    """
    Import all pattern modules to trigger registration.
    Call once at startup after patterns/ package is available.

    Pattern modules use @register_class decorator which auto-registers on import.
    """
    import importlib
    import pkgutil
    import patterns

    for _importer, modname, _ispkg in pkgutil.iter_modules(patterns.__path__):
        if modname in ("base", "registry", "__init__"):
            continue
        try:
            importlib.import_module(f"patterns.{modname}")
        except Exception as exc:
            logger.warning("[PATTERN_LOAD_ERROR] module=%s error=%s", modname, exc)
