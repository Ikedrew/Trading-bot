"""
Research Intelligence — Question Discovery (Interface Only).

Future purpose: Analyse available fields, segment sizes, and performance
differences to suggest potential research questions.

Currently provides the interface for Phase 5+ implementation.
Does NOT automatically execute discovered questions.
"""

from __future__ import annotations

from typing import Any


class QuestionDiscovery:
    """
    Discovers potential research questions from data characteristics.

    Future implementation will:
        - Scan available fields in research universe
        - Identify segments with statistically different performance
        - Suggest questions that could be registered as experiments

    Currently: interface only.
    """

    def __init__(self, universe_events: list[dict] | None = None):
        self._events = universe_events or []

    def discover(self) -> list[dict[str, Any]]:
        """
        Discover potential research questions.

        Returns:
            List of suggested questions (currently empty — future implementation).
        """
        # Future: implement automatic discovery
        return []

    def available_dimensions(self) -> dict[str, list[str]]:
        """
        List available segmentation dimensions and their values.

        Returns:
            {"regime": ["TRENDING", "RANGING", ...], "session": [...], ...}
        """
        if not self._events:
            return {}

        dimensions: dict[str, set[str]] = {
            "regime": set(),
            "session": set(),
            "volatility": set(),
            "instrument": set(),
            "strategy_family": set(),
        }

        for e in self._events:
            mkt = e.get("market", {})
            strat = e.get("strategy", {})
            ex = e.get("execution", {})

            if mkt.get("regime"):
                dimensions["regime"].add(mkt["regime"])
            if mkt.get("session"):
                dimensions["session"].add(mkt["session"])
            if mkt.get("volatility"):
                dimensions["volatility"].add(mkt["volatility"])
            if ex.get("symbol"):
                dimensions["instrument"].add(ex["symbol"])
            if strat.get("family"):
                dimensions["strategy_family"].add(strat["family"])

        return {k: sorted(v) for k, v in dimensions.items() if v}
