"""
Edge Evidence Loader — canonical V1 evidence for the edge-candidate surface.

The edge surface researches: "Under what conditions does an opportunity have
positive expectancy?" Historically it consumed decision_trace (canonical S3)
plus LOCAL replay_data/ candles to simulate counterfactual outcomes for
decisions that never executed. That local dependency was silent: when
replay_data/ was absent the surface quietly had zero outcome evidence.

The canonical shadow runtime IS the production counterfactual engine: it
simulates every pattern-qualified opportunity (nshadow_* lifecycles,
including opportunities live did NOT take) and persists the simulated outcome
on S3. This module therefore maps every edge-analysis input to authoritative
canonical V1 evidence:

    decision conditions/context  ->  decision_trace        (S3, canonical)
    counterfactual outcome R     ->  shadow_runtime_v1     (S3, ingestion)
    realised outcome R           ->  trade_truth_v1        (S3, canonical)

Join key: canonical_opportunity_id (the canonical lineage root, present on
all three populations). trade_truth realised outcomes are preferred over the
shadow counterfactual when both exist (real evidence beats simulation);
the shadow counterfactual covers every shadow-observed opportunity.

Modes:
    production (default)   S3 canonical evidence only. No local reads.
    offline replay         EXPLICITLY requested fixture mode (tests/offline
                           experiments): local replay_data/ candles + the
                           counterfactual simulator. NEVER selected by a
                           production run, and NEVER a fallback for S3.

This module is READ ONLY and never modifies production data.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from research_engine.edge_attribution.models import (
    EdgeAttributionRecord,
    build_attribution_record,
)

logger = logging.getLogger(__name__)

# Same incumbent-prediction-surface semantics as the Q16/X4 matcher.
_PRIMARY_HORIZON_TYPE = "PRIMARY_HORIZON_SIMULATION"


@dataclass
class EdgeEvidenceResult:
    """Canonical edge evidence plus explicit, no-silent-drops accounting."""

    records: list[EdgeAttributionRecord] = field(default_factory=list)
    accounting: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_count": len(self.records),
            "accounting": dict(self.accounting),
        }


def _canonical_key(record: dict[str, Any]) -> str:
    """Extract canonical_opportunity_id (flat or nested under identity)."""
    canon = record.get("canonical_opportunity_id")
    if canon:
        return str(canon)
    identity = record.get("identity", {})
    if isinstance(identity, dict):
        canon = identity.get("canonical_opportunity_id")
        if canon:
            return str(canon)
    return ""


def load_edge_evidence() -> EdgeEvidenceResult:
    """
    Production edge evidence from canonical S3 sources ONLY.

    Returns attribution records (decision conditions + outcome R) with full
    accounting. Never reads local replay_data/, never falls back, never
    fabricates outcomes. Decisions without any canonical outcome are counted
    (uncovered) and excluded — they simply have no outcome evidence.
    """
    from research_engine.data_access.loaders import load_decision_trace
    from research_engine.data_access.s3_source import get_default_source
    from research_engine.data_access.shadow_runtime_ingestion import (
        ingest_completed_shadow_trades,
    )

    traces = load_decision_trace()
    shadows = ingest_completed_shadow_trades()
    truths = get_default_source().read_dataset("trade_truth")

    acc: dict[str, Any] = {
        "mode": "production_canonical_s3",
        "datasets": ["decision_trace", "shadow_runtime_v1(ingested)", "trade_truth"],
        "join_key": "canonical_opportunity_id",
        "traces_total": len(traces),
        "shadow_outcomes_total": len(shadows),
        "trade_truth_total": len(truths),
    }

    outcome_by_canon, outcome_acc = _index_outcomes(shadows, truths)
    acc.update(outcome_acc)

    records: list[EdgeAttributionRecord] = []
    eligible = 0
    for t in traces:
        # Existing evidence filter (unchanged semantics): decisions with a
        # detected pattern and scoring components are the edge population.
        if not (t.get("pattern_detected") and t.get("components")):
            continue
        eligible += 1
        canon = _canonical_key(t)
        if not canon:
            acc["trace_missing_canonical_key"] = acc.get("trace_missing_canonical_key", 0) + 1
            continue
        outcome = outcome_by_canon.get(canon)
        if outcome is None:
            acc["decisions_without_outcome"] = acc.get("decisions_without_outcome", 0) + 1
            continue
        r, source = outcome
        records.append(build_attribution_record(t, r))
        src_key = f"outcome_source_{source}"
        acc[src_key] = acc.get(src_key, 0) + 1

    acc["decisions_eligible"] = eligible
    acc["attribution_records"] = len(records)
    logger.info(
        "[EDGE_EVIDENCE] canonical S3 evidence: traces=%d eligible=%d outcomes=%d "
        "records=%d accounting=%s",
        acc["traces_total"], eligible, len(outcome_by_canon), len(records), acc,
    )
    return EdgeEvidenceResult(records=records, accounting=acc)


def _index_outcomes(
    shadows: list[dict[str, Any]],
    truths: list[dict[str, Any]],
) -> tuple[dict[str, tuple[float, str]], dict[str, Any]]:
    """
    Index canonical outcomes by canonical_opportunity_id.

    Shadow counterfactuals: PRIMARY_HORIZON_SIMULATION only (incumbent
    prediction surface). trade_truth realised outcomes override the shadow
    counterfactual for the same canonical opportunity. Exact replay rows
    collapse; conflicting duplicates are excluded and counted — never
    fabricated into a single outcome.
    """
    acc: dict[str, Any] = {
        "shadow_missing_canonical_key": 0,
        "shadow_missing_outcome": 0,
        "shadow_horizon_alternative_skipped": 0,
        "shadow_duplicate_replay_collapsed": 0,
        "shadow_ambiguous_excluded": 0,
        "truth_missing_canonical_key": 0,
        "truth_missing_outcome": 0,
        "truth_duplicate_replay_collapsed": 0,
        "truth_ambiguous_excluded": 0,
    }

    def _shadow_r(rec: dict[str, Any]) -> float | None:
        sim = rec.get("simulated_outcome", {})
        r = sim.get("pnl_r_multiple") if isinstance(sim, dict) else None
        return float(r) if r is not None else None

    def _truth_r(rec: dict[str, Any]) -> float | None:
        outcome = rec.get("outcome", {})
        r = outcome.get("r_multiple_realised") if isinstance(outcome, dict) else None
        return float(r) if r is not None else None

    # Shadow counterfactuals (primary horizon per canonical)
    by_canon: dict[str, list[dict[str, Any]]] = {}
    for s in shadows:
        canon = _canonical_key(s)
        if not canon:
            acc["shadow_missing_canonical_key"] += 1
            continue
        if _shadow_r(s) is None:
            acc["shadow_missing_outcome"] += 1
            continue
        identity = s.get("identity", {}) or {}
        if identity.get("shadow_type", "") != _PRIMARY_HORIZON_TYPE:
            acc["shadow_horizon_alternative_skipped"] += 1
            continue
        by_canon.setdefault(canon, []).append(s)
    shadow_idx: dict[str, dict[str, Any] | None] = {}
    for canon, group in by_canon.items():
        distinct: dict[str, dict[str, Any]] = {}
        conflicting = False
        for rec in group:
            tid = str((rec.get("identity", {}) or {}).get("shadow_trade_id", "") or "")
            prior = distinct.get(tid)
            if prior is None:
                distinct[tid] = rec
            elif _shadow_r(prior) != _shadow_r(rec):
                conflicting = True
            else:
                acc["shadow_duplicate_replay_collapsed"] += 1
        if conflicting or len(distinct) > 1:
            acc["shadow_ambiguous_excluded"] += 1
            shadow_idx[canon] = None  # tombstone
            continue
        shadow_idx[canon] = next(iter(distinct.values()))

    # Realised outcomes (override counterfactuals)
    truth_idx: dict[str, dict[str, Any] | None] = {}
    for t in truths:
        canon = _canonical_key(t)
        if not canon:
            acc["truth_missing_canonical_key"] += 1
            continue
        if _truth_r(t) is None:
            acc["truth_missing_outcome"] += 1
            continue
        existing = truth_idx.get(canon)
        if existing is None and canon not in truth_idx:
            truth_idx[canon] = t
            continue
        if existing is not None:
            eid = str((existing.get("identity", {}) or {}).get("trade_id", "") or "")
            tid = str((t.get("identity", {}) or {}).get("trade_id", "") or "")
            if eid == tid and _truth_r(existing) == _truth_r(t):
                acc["truth_duplicate_replay_collapsed"] += 1
                continue
        acc["truth_ambiguous_excluded"] += 1
        truth_idx[canon] = None  # tombstone

    outcomes: dict[str, tuple[float, str]] = {}
    for canon, rec in truth_idx.items():
        if rec is not None:
            outcomes[canon] = (_truth_r(rec) or 0.0, "trade_truth_realised")
    for canon, rec in shadow_idx.items():
        if rec is not None and canon not in outcomes:
            outcomes[canon] = (_shadow_r(rec) or 0.0, "shadow_counterfactual")

    acc["canonicals_with_outcome"] = len(outcomes)
    return outcomes, acc


# ═══════════════════════════════════════════════════════════════════════════════
# EXPLICIT OFFLINE FIXTURE MODE — never a production path, never a fallback
# ═══════════════════════════════════════════════════════════════════════════════


def load_edge_evidence_offline_replay(
    replay_dir: str = "replay_data",
    *,
    traces: list[dict[str, Any]] | None = None,
) -> EdgeEvidenceResult:
    """
    OFFLINE FIXTURE MODE: build edge evidence from local replay candles plus
    the counterfactual simulator.

    This is a legitimate offline fixture/replay input for tests and explicit
    offline experiments. It is NEVER production evidence: a production run
    must call load_edge_evidence() (canonical S3) and must NEVER fall back
    here. Requires explicit selection by the caller.
    """
    from research_engine.counterfactual.schema import SimulationConfidence
    from research_engine.counterfactual.simulator import simulate_blocked_decision

    if traces is None:
        from research_engine.data_access.loaders import load_decision_trace
        traces = load_decision_trace()

    acc: dict[str, Any] = {
        "mode": "offline_replay_fixture",
        "replay_dir": str(replay_dir),
        "traces_total": len(traces),
        "symbols_with_candles": 0,
        "decisions_without_candles": 0,
        "simulation_low_confidence_skipped": 0,
    }

    candle_cache = _load_local_replay_candles(replay_dir)
    acc["symbols_with_candles"] = len(candle_cache)

    records: list[EdgeAttributionRecord] = []
    eligible = 0
    for t in traces:
        if not (t.get("pattern_detected") and t.get("components")):
            continue
        eligible += 1
        symbol = t.get("symbol", "")
        candles = (
            candle_cache.get(symbol)
            or candle_cache.get(symbol + "_SB")
            or candle_cache.get(symbol.replace("_SB", ""))
            or []
        )
        if not candles:
            acc["decisions_without_candles"] += 1
            continue
        cf = simulate_blocked_decision(t, candles)
        if cf.simulation_confidence in (SimulationConfidence.HIGH, SimulationConfidence.MEDIUM):
            records.append(build_attribution_record(t, cf.hypothetical_r))
        else:
            acc["simulation_low_confidence_skipped"] += 1

    acc["decisions_eligible"] = eligible
    acc["attribution_records"] = len(records)
    return EdgeEvidenceResult(records=records, accounting=acc)


def _load_local_replay_candles(replay_dir: str = "replay_data") -> dict[str, list[dict]]:
    """Load local replay fixture candles (explicit offline mode ONLY)."""
    base = Path(replay_dir)
    cache: dict[str, list[dict]] = {}
    if not base.exists():
        return cache
    for sym_dir in base.iterdir():
        if not sym_dir.is_dir():
            continue
        tf_dir = sym_dir / "5"
        if not tf_dir.exists():
            continue
        candles: list[dict] = []
        for f in sorted(tf_dir.glob("*.jsonl")):
            with open(f) as fh:
                for line in fh:
                    if line.strip():
                        try:
                            candles.append(json.loads(line.strip()))
                        except json.JSONDecodeError:
                            pass
        if candles:
            cache[sym_dir.name] = candles
    return cache


