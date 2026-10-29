"""Snapshot-bound governed exit foundations (Pass D Repair 1).

The governed canonical question cycle must never let an exit/evidence
evaluator independently reopen storage.  This module reconstructs the HD09
governed exit foundations (exit-bar path, baseline reproduction, candidate
replay, and dimension evidence) deterministically from the raw
``shadow_runtime`` events of one governed snapshot.

The governed snapshot deliberately does not bind ``events_v1`` M5 OHLC
candle objects.  The exit-bar path therefore cannot reconstruct ordered M5
paths from entry to exit.  That is a real, governable observation gap: the
builders still run, but ``missing_evidence`` records the gap and the exit
runners must surface it as an explicit INSUFFICIENT_DATA/BLOCKED result
rather than reopening the filesystem.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from research_engine.control_plane.exit_bar_path import (
    GovernedExitBarPathEvidence,
    LifecyclePathSource,
    build_exit_bar_path_v1,
)
from research_engine.control_plane.exit_baseline_replay import (
    GovernedBaselineReproductionPopulation,
    build_baseline_reproduction_population,
)
from research_engine.control_plane.exit_candidate_replay import (
    CandidateReplayPopulation,
    build_candidate_replay_population,
)
from research_engine.control_plane.exit_dimension_evidence import (
    ExitDimensionEvidence,
    build_exit_dimension_evidence_v1,
)
from research_engine.data_access.shadow_runtime_ingestion import (
    reconstruct_completed_shadow_trades,
)

# The events_v1 M5 OHLC candle stream is the only authority admitted by the
# frozen HD09 exit-bar-path contract.  It is not a bound dataset of the common
# investigation snapshot, so its absence is an observation gap, never a silent
# empty universe.
_M5_CANDLE_MISSING = (
    "governed snapshot does not bind events_v1 M5 OHLC candle paths; "
    "ordered entry-to-exit bar path cannot be reconstructed"
)


@dataclass(frozen=True)
class GovernedExitEvidence:
    """Exit foundations derived entirely from one governed snapshot."""

    path: GovernedExitBarPathEvidence
    reproduction: GovernedBaselineReproductionPopulation
    candidate: CandidateReplayPopulation
    dimensions: ExitDimensionEvidence | None
    missing_evidence: tuple[str, ...]
    completed_lifecycles: int
    eligible_path_lifecycles: int

    @property
    def snapshot_complete(self) -> bool:
        """True only when the required exit-path evidence is fully present."""
        return not self.missing_evidence and self.eligible_path_lifecycles > 0


def _lifecycle_key(event: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(event.get("shadow_trade_id", "") or ""),
        str(event.get("canonical_opportunity_id", "") or ""),
        str(event.get("horizon", "") or ""),
    )


def build_governed_exit_evidence(
    shadow_events: Iterable[Mapping[str, Any]],
    candle_events: Iterable[Mapping[str, Any]] = (),
    *,
    with_dimensions: bool = True,
) -> GovernedExitEvidence:
    """Build governed exit foundations from in-memory snapshot events.

    ``shadow_events`` are the raw ``shadow_runtime`` records of one governed
    snapshot.  ``candle_events`` are any bound M5 OHLC candle records (empty
    for the common investigation snapshot).
    """
    shadow_records = [dict(item) for item in shadow_events]
    candles = [dict(item) for item in candle_events]

    open_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    close_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for record in shadow_records:
        if record.get("event_type") == "OPEN":
            open_by_key.setdefault(_lifecycle_key(record), record)
        elif record.get("event_type") == "CLOSE":
            close_by_key.setdefault(_lifecycle_key(record), record)

    completed = reconstruct_completed_shadow_trades(shadow_records)

    candles_by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in candles:
        if (
            record.get("type") == "CANDLE"
            and record.get("source") == "mt5_data"
            and record.get("schema_version") == "events_v1"
            and record.get("timeframe") == "M5"
        ):
            candles_by_symbol[str(record.get("symbol", "")).upper()].append(record)
    for values in candles_by_symbol.values():
        values.sort(key=lambda item: int(item["payload"]["ts"]))
    times = {
        symbol: [int(item["payload"]["ts"]) for item in values]
        for symbol, values in candles_by_symbol.items()
    }

    sources: list[LifecyclePathSource] = []
    for lifecycle in completed:
        identity = lifecycle.get("identity") or {}
        key = (
            str(identity.get("shadow_trade_id", "") or ""),
            str(identity.get("canonical_opportunity_id", "") or ""),
            str(identity.get("trade_horizon", "") or identity.get("evaluated_horizon", "") or ""),
        )
        opened = open_by_key.get(key)
        closed = close_by_key.get(key)
        if opened is None or closed is None:
            continue
        symbol = str(opened.get("symbol", "")).upper()
        values = candles_by_symbol.get(symbol, [])
        stamps = times.get(symbol, [])
        entry_ms = int(opened.get("entry_market_time", 0) or 0) * 1000
        exit_ms = int(closed.get("exit_market_time", 0) or 0) * 1000
        subset = values[bisect_right(stamps, entry_ms):bisect_right(stamps, exit_ms)]
        sources.append(LifecyclePathSource(opened, closed, tuple(subset)))

    path = build_exit_bar_path_v1(sources)
    reproduction = build_baseline_reproduction_population(path)
    candidate = build_candidate_replay_population(path, reproduction)

    dimensions: ExitDimensionEvidence | None = None
    if with_dimensions:
        dimension_sources = [
            LifecyclePathSource(record, {}, ())
            for record in shadow_records
            if record.get("event_type") == "OPEN"
        ]
        dimensions = build_exit_dimension_evidence_v1(dimension_sources, path)

    missing = tuple([_M5_CANDLE_MISSING]) if not candles else ()

    return GovernedExitEvidence(
        path=path,
        reproduction=reproduction,
        candidate=candidate,
        dimensions=dimensions,
        missing_evidence=missing,
        completed_lifecycles=len(completed),
        eligible_path_lifecycles=path.summary.eligible_lifecycles,
    )


__all__ = [
    "GovernedExitEvidence",
    "build_governed_exit_evidence",
    "_M5_CANDLE_MISSING",
]
