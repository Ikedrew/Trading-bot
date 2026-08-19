"""
Evidence Layer — Produces a structured evidence record for every detector on every cycle.

Every observable bot surface produces an evidence record regardless of whether
the trigger threshold is crossed. This makes the research engine's observations
FULLY TRANSPARENT — nothing is hidden merely because a threshold wasn't met.

Evidence Status:
    TRIGGERED       — threshold crossed, FindingTrigger escalated
    SIGNAL          — metric shows a directional effect but below threshold
    NO_SIGNAL       — metric within normal bounds
    INSUFFICIENT_DATA — not enough observations to calculate metric
    NOT_OBSERVABLE  — required data source does not exist

The existing trigger mechanism is PRESERVED. This layer sits ALONGSIDE it,
not replacing it. Triggers still use their existing thresholds for escalation
into the investigation pipeline.

This module NEVER modifies production trading.
"""

from __future__ import annotations

import json
import os
import statistics
import time as _time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# EVIDENCE RECORD
# ═══════════════════════════════════════════════════════════════════════════════

class EvidenceStatus:
    TRIGGERED = "TRIGGERED"
    SIGNAL = "SIGNAL"
    NO_SIGNAL = "NO_SIGNAL"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    NOT_OBSERVABLE = "NOT_OBSERVABLE"


@dataclass
class EvidenceRecord:
    """Structured evidence produced by one detector examining one surface."""
    detector_id: str = ""
    detector_name: str = ""
    surface: str = ""
    timestamp: str = ""

    # Population
    population_name: str = ""
    population_size: int = 0
    groups_examined: int = 0

    # Metrics
    metric_name: str = ""
    metric_value: float | None = None
    baseline_value: float | None = None
    effect_size: float | None = None

    # Threshold
    threshold_name: str = ""
    threshold_value: float | None = None
    threshold_crossed: bool = False

    # Status
    status: str = EvidenceStatus.NO_SIGNAL
    reason: str = ""

    # Additional context
    breakdown: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "detector_id": self.detector_id,
            "detector_name": self.detector_name,
            "surface": self.surface,
            "timestamp": self.timestamp,
            "population_name": self.population_name,
            "population_size": self.population_size,
            "groups_examined": self.groups_examined,
            "metric_name": self.metric_name,
            "metric_value": self.metric_value,
            "baseline_value": self.baseline_value,
            "effect_size": self.effect_size,
            "threshold_name": self.threshold_name,
            "threshold_value": self.threshold_value,
            "threshold_crossed": self.threshold_crossed,
            "status": self.status,
            "reason": self.reason,
            "breakdown": self.breakdown,
        }


@dataclass
class EvidenceCycleReport:
    """Aggregate totals for one evidence cycle."""
    cycle_id: str = ""
    timestamp: str = ""
    total_surfaces: int = 0
    detectors_executed: int = 0
    evidence_records: int = 0
    signals_found: int = 0
    triggers_generated: int = 0
    insufficient_data: int = 0
    not_observable: int = 0
    no_signal: int = 0
    records: list[EvidenceRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "timestamp": self.timestamp,
            "total_surfaces": self.total_surfaces,
            "detectors_executed": self.detectors_executed,
            "evidence_records": self.evidence_records,
            "signals_found": self.signals_found,
            "triggers_generated": self.triggers_generated,
            "insufficient_data": self.insufficient_data,
            "not_observable": self.not_observable,
            "no_signal": self.no_signal,
            "records": [r.to_dict() for r in self.records],
        }


# ═══════════════════════════════════════════════════════════════════════════════
# EVIDENCE COLLECTION
# ═══════════════════════════════════════════════════════════════════════════════

def collect_evidence(
    *,
    real_shadows: list[dict[str, Any]],
    all_shadows: list[dict[str, Any]],
    triggers_generated: int = 0,
    cycle_id: str = "",
) -> EvidenceCycleReport:
    """
    Run all evidence calculations for one research cycle.

    Produces an EvidenceRecord for every detector regardless of trigger status.
    Does NOT modify trigger logic — triggers are generated separately.

    Args:
        real_shadows: V10_PRIMARY shadows with correlation_id
        all_shadows: ALL_SHADOW_OUTCOMES population
        triggers_generated: count of triggers from the actual detector run
        cycle_id: research cycle identifier
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    records: list[EvidenceRecord] = []

    # ─── DET-001: Pattern Performance ─────────────────────────────────
    records.append(_evidence_pattern_performance(real_shadows, timestamp))

    # ─── DET-002: Direction Asymmetry ─────────────────────────────────
    records.append(_evidence_direction_asymmetry(real_shadows, timestamp))

    # ─── DET-003: Regime Anomaly ──────────────────────────────────────
    records.append(_evidence_regime_anomaly(real_shadows, timestamp))

    # ─── DET-004: Score Monotonicity ──────────────────────────────────
    records.append(_evidence_score_monotonicity(real_shadows, timestamp))

    # ─── DET-005: Temporal Instability ────────────────────────────────
    records.append(_evidence_temporal_instability(real_shadows, timestamp))

    # ─── DET-006: Geometry Anomaly ────────────────────────────────────
    records.append(_evidence_geometry_anomaly(real_shadows, timestamp))

    # ─── DET-007: Symbol Anomaly ──────────────────────────────────────
    records.append(_evidence_symbol_anomaly(real_shadows, timestamp))

    # ─── DET-008: Exit Inefficiency ───────────────────────────────────
    records.append(_evidence_exit_inefficiency(all_shadows, timestamp))

    # ─── DET-009: Guard Value (controlled) ────────────────────────────
    records.append(_evidence_guard_value(all_shadows, timestamp))

    # ─── DET-010: Session Degradation ─────────────────────────────────
    records.append(_evidence_session_degradation(real_shadows, timestamp))

    # ─── DET-011: Spread Anomaly ──────────────────────────────────────
    records.append(_evidence_spread_anomaly(real_shadows, timestamp))

    # ─── DET-012: Slippage Deterioration ──────────────────────────────
    records.append(_evidence_slippage(timestamp))

    # ─── DET-013: Horizon Quality ─────────────────────────────────────
    records.append(_evidence_horizon_quality(real_shadows, timestamp))

    # ─── DET-014: Strategy Degradation ────────────────────────────────
    records.append(_evidence_strategy_degradation(real_shadows, timestamp))

    # ─── DET-015: Shadow↔Reality ──────────────────────────────────────
    records.append(_evidence_shadow_reality(timestamp))

    # ─── DET-016: Drawdown ────────────────────────────────────────────
    records.append(_evidence_drawdown(timestamp))

    # ─── DET-017–021: BLACK surfaces (research events) ────────────────
    records.extend(_evidence_black_surfaces(timestamp))

    # Build report
    report = EvidenceCycleReport(
        cycle_id=cycle_id,
        timestamp=timestamp,
        total_surfaces=len(records),
        detectors_executed=len(records),
        evidence_records=len(records),
        signals_found=sum(1 for r in records if r.status == EvidenceStatus.SIGNAL),
        triggers_generated=triggers_generated,
        insufficient_data=sum(1 for r in records if r.status == EvidenceStatus.INSUFFICIENT_DATA),
        not_observable=sum(1 for r in records if r.status == EvidenceStatus.NOT_OBSERVABLE),
        no_signal=sum(1 for r in records if r.status == EvidenceStatus.NO_SIGNAL),
        records=records,
    )

    # Persist
    _persist_evidence(report)

    return report


# ═══════════════════════════════════════════════════════════════════════════════
# INDIVIDUAL EVIDENCE CALCULATORS
# ═══════════════════════════════════════════════════════════════════════════════

def _evidence_pattern_performance(shadows: list[dict], ts: str) -> EvidenceRecord:
    by_pattern = defaultdict(list)
    for s in shadows:
        pat = s.get("pattern", "")
        r = s.get("r_multiple")
        if pat and r is not None:
            by_pattern[pat].append(r)

    if not by_pattern:
        return EvidenceRecord(detector_id="DET-001", detector_name="pattern_performance",
                              surface="pattern_performance", timestamp=ts,
                              status=EvidenceStatus.INSUFFICIENT_DATA, reason="No pattern data")

    # Find worst pattern
    worst_pat, worst_r = "", 0.0
    breakdown = {}
    for pat, vals in by_pattern.items():
        if len(vals) >= 20:
            m = statistics.mean(vals)
            breakdown[pat] = {"n": len(vals), "mean_r": round(m, 4), "wr": round(sum(1 for v in vals if v > 0) / len(vals), 3)}
            if m < worst_r or not worst_pat:
                worst_pat, worst_r = pat, m

    threshold = -0.15
    crossed = worst_r < threshold and worst_pat != ""
    status = EvidenceStatus.TRIGGERED if crossed else (EvidenceStatus.SIGNAL if worst_r < 0 else EvidenceStatus.NO_SIGNAL)
    if not breakdown:
        status = EvidenceStatus.INSUFFICIENT_DATA

    return EvidenceRecord(
        detector_id="DET-001", detector_name="pattern_performance",
        surface="pattern_performance", timestamp=ts,
        population_name="real_shadows (PRIMARY)", population_size=len(shadows),
        groups_examined=len(breakdown), metric_name="worst_pattern_mean_r",
        metric_value=round(worst_r, 4) if worst_pat else None,
        threshold_name="min_effect_size", threshold_value=threshold,
        threshold_crossed=crossed, status=status,
        reason=f"Worst: {worst_pat} R={worst_r:+.3f}" if worst_pat else "Insufficient per-pattern N",
        breakdown=breakdown,
    )


def _evidence_direction_asymmetry(shadows: list[dict], ts: str) -> EvidenceRecord:
    by_pat_dir = defaultdict(lambda: defaultdict(list))
    for s in shadows:
        pat = s.get("pattern", "")
        d = s.get("direction", "")
        r = s.get("r_multiple")
        if pat and d and r is not None:
            by_pat_dir[pat][d].append(r)

    max_asym = 0.0
    max_pat = ""
    groups = 0
    for pat, dirs in by_pat_dir.items():
        buy = dirs.get("BUY", [])
        sell = dirs.get("SELL", [])
        if len(buy) >= 20 and len(sell) >= 20:
            groups += 1
            delta = abs(statistics.mean(buy) - statistics.mean(sell))
            if delta > max_asym:
                max_asym, max_pat = delta, pat

    threshold = 0.30
    crossed = max_asym >= threshold
    if groups == 0:
        status = EvidenceStatus.INSUFFICIENT_DATA
    elif crossed:
        status = EvidenceStatus.TRIGGERED
    elif max_asym > 0.15:
        status = EvidenceStatus.SIGNAL
    else:
        status = EvidenceStatus.NO_SIGNAL

    return EvidenceRecord(
        detector_id="DET-002", detector_name="direction_asymmetry",
        surface="pattern_direction", timestamp=ts,
        population_name="real_shadows", population_size=len(shadows),
        groups_examined=groups, metric_name="max_direction_delta",
        metric_value=round(max_asym, 4), threshold_name="min_direction_delta",
        threshold_value=threshold, threshold_crossed=crossed, status=status,
        reason=f"Max asymmetry: {max_pat} delta={max_asym:.3f}" if max_pat else "Insufficient paired data",
    )


def _evidence_regime_anomaly(shadows: list[dict], ts: str) -> EvidenceRecord:
    by_regime = defaultdict(list)
    for s in shadows:
        reg = s.get("regime", "")
        r = s.get("r_multiple")
        if reg and r is not None:
            by_regime[reg].append(r)

    groups = sum(1 for v in by_regime.values() if len(v) >= 30)
    if groups < 2:
        return EvidenceRecord(detector_id="DET-003", detector_name="regime_anomaly",
                              surface="market_regime", timestamp=ts,
                              population_name="real_shadows", population_size=len(shadows),
                              groups_examined=groups, status=EvidenceStatus.INSUFFICIENT_DATA,
                              reason=f"Only {groups} regimes with N>=30")

    means = {k: statistics.mean(v) for k, v in by_regime.items() if len(v) >= 30}
    overall = statistics.mean([r for vals in by_regime.values() for r in vals])
    max_delta = max(abs(m - overall) for m in means.values()) if means else 0

    threshold = 0.20
    crossed = max_delta >= threshold
    status = EvidenceStatus.TRIGGERED if crossed else (EvidenceStatus.SIGNAL if max_delta > 0.10 else EvidenceStatus.NO_SIGNAL)

    return EvidenceRecord(
        detector_id="DET-003", detector_name="regime_anomaly",
        surface="market_regime", timestamp=ts,
        population_name="real_shadows", population_size=len(shadows),
        groups_examined=groups, metric_name="max_regime_delta",
        metric_value=round(max_delta, 4), baseline_value=round(overall, 4),
        threshold_name="min_regime_delta", threshold_value=threshold,
        threshold_crossed=crossed, status=status,
        reason=f"Max regime deviation: {max_delta:.3f}R from overall {overall:+.3f}",
        breakdown={k: {"n": len(by_regime[k]), "mean_r": round(v, 4)} for k, v in means.items()},
    )


def _evidence_score_monotonicity(shadows: list[dict], ts: str) -> EvidenceRecord:
    scored = [(s.get("score", 0), s.get("r_multiple")) for s in shadows
              if s.get("score") and s.get("r_multiple") is not None]
    if len(scored) < 50:
        return EvidenceRecord(detector_id="DET-004", detector_name="score_monotonicity",
                              surface="decision_scoring", timestamp=ts,
                              population_size=len(scored), status=EvidenceStatus.INSUFFICIENT_DATA,
                              reason=f"N={len(scored)} < 50")

    scored.sort(key=lambda x: x[0])
    q_size = len(scored) // 4
    quartiles = [statistics.mean([r for _, r in scored[i*q_size:(i+1)*q_size]]) for i in range(4)]
    inversions = sum(1 for i in range(3) if quartiles[i] > quartiles[i+1])
    q4_q1 = quartiles[3] - quartiles[0]

    threshold = 0.15
    crossed = inversions >= 2 or q4_q1 < -threshold
    status = EvidenceStatus.TRIGGERED if crossed else (EvidenceStatus.SIGNAL if inversions >= 1 else EvidenceStatus.NO_SIGNAL)

    return EvidenceRecord(
        detector_id="DET-004", detector_name="score_monotonicity",
        surface="decision_scoring", timestamp=ts,
        population_name="real_shadows", population_size=len(scored),
        groups_examined=4, metric_name="inversions",
        metric_value=inversions, effect_size=round(q4_q1, 4),
        threshold_name="min_score_inversion_delta", threshold_value=threshold,
        threshold_crossed=crossed, status=status,
        reason=f"{inversions} inversions, Q4-Q1={q4_q1:+.3f}R",
        breakdown={"quartile_means": [round(q, 4) for q in quartiles]},
    )


def _evidence_temporal_instability(shadows: list[dict], ts: str) -> EvidenceRecord:
    timed = [(s.get("timestamp_decision_utc", 0), s.get("r_multiple"))
             for s in shadows if s.get("timestamp_decision_utc") and s.get("r_multiple") is not None]
    if len(timed) < 60:
        return EvidenceRecord(detector_id="DET-005", detector_name="temporal_instability",
                              surface="temporal_stability", timestamp=ts,
                              population_size=len(timed), status=EvidenceStatus.INSUFFICIENT_DATA,
                              reason=f"N={len(timed)} < 60")

    timed.sort(key=lambda x: x[0])
    mid = len(timed) // 2
    early = statistics.mean([r for _, r in timed[:mid]])
    recent = statistics.mean([r for _, r in timed[mid:]])
    delta = recent - early

    threshold = 0.20
    crossed = abs(delta) >= threshold
    status = EvidenceStatus.TRIGGERED if crossed else (EvidenceStatus.SIGNAL if abs(delta) > 0.10 else EvidenceStatus.NO_SIGNAL)

    return EvidenceRecord(
        detector_id="DET-005", detector_name="temporal_instability",
        surface="temporal_stability", timestamp=ts,
        population_name="real_shadows", population_size=len(timed),
        groups_examined=2, metric_name="temporal_delta",
        metric_value=round(delta, 4), baseline_value=round(early, 4),
        effect_size=round(delta, 4),
        threshold_name="min_temporal_delta", threshold_value=threshold,
        threshold_crossed=crossed, status=status,
        reason=f"Early R={early:+.3f}, Recent R={recent:+.3f}, delta={delta:+.3f}",
    )


def _evidence_geometry_anomaly(shadows: list[dict], ts: str) -> EvidenceRecord:
    # Compare tight vs wide stops (quartiles of risk_distance)
    with_risk = [(s.get("risk_distance", 0), s.get("r_multiple"))
                 for s in shadows if s.get("risk_distance") and s.get("r_multiple") is not None]
    if len(with_risk) < 50:
        return EvidenceRecord(detector_id="DET-006", detector_name="geometry_anomaly",
                              surface="entry_geometry", timestamp=ts,
                              population_size=len(with_risk), status=EvidenceStatus.INSUFFICIENT_DATA)

    with_risk.sort(key=lambda x: x[0])
    q_size = len(with_risk) // 4
    q1_r = statistics.mean([r for _, r in with_risk[:q_size]])
    q4_r = statistics.mean([r for _, r in with_risk[3*q_size:]])
    delta = q1_r - q4_r  # tight - wide

    threshold = 0.25
    crossed = abs(delta) >= threshold
    status = EvidenceStatus.TRIGGERED if crossed else (EvidenceStatus.SIGNAL if abs(delta) > 0.12 else EvidenceStatus.NO_SIGNAL)

    return EvidenceRecord(
        detector_id="DET-006", detector_name="geometry_anomaly",
        surface="entry_geometry", timestamp=ts,
        population_name="real_shadows", population_size=len(with_risk),
        groups_examined=4, metric_name="tight_vs_wide_delta",
        metric_value=round(delta, 4),
        threshold_name="min_geometry_delta", threshold_value=threshold,
        threshold_crossed=crossed, status=status,
        reason=f"Tight stops R={q1_r:+.3f}, Wide stops R={q4_r:+.3f}, delta={delta:+.3f}",
    )


def _evidence_symbol_anomaly(shadows: list[dict], ts: str) -> EvidenceRecord:
    by_sym = defaultdict(list)
    for s in shadows:
        sym = s.get("symbol", "")
        r = s.get("r_multiple")
        if sym and r is not None:
            by_sym[sym].append(r)

    groups = sum(1 for v in by_sym.values() if len(v) >= 20)
    if groups < 2:
        return EvidenceRecord(detector_id="DET-007", detector_name="symbol_anomaly",
                              surface="symbol_performance", timestamp=ts,
                              population_size=len(shadows), groups_examined=groups,
                              status=EvidenceStatus.INSUFFICIENT_DATA)

    all_r = [r for vals in by_sym.values() for r in vals]
    overall = statistics.mean(all_r)
    max_delta = 0.0
    worst_sym = ""
    breakdown = {}
    for sym, vals in by_sym.items():
        if len(vals) >= 20:
            m = statistics.mean(vals)
            d = abs(m - overall)
            breakdown[sym] = {"n": len(vals), "mean_r": round(m, 4), "delta": round(m - overall, 4)}
            if d > max_delta:
                max_delta, worst_sym = d, sym

    threshold = 0.25
    crossed = max_delta >= threshold
    status = EvidenceStatus.TRIGGERED if crossed else (EvidenceStatus.SIGNAL if max_delta > 0.12 else EvidenceStatus.NO_SIGNAL)

    return EvidenceRecord(
        detector_id="DET-007", detector_name="symbol_anomaly",
        surface="symbol_performance", timestamp=ts,
        population_name="real_shadows", population_size=len(shadows),
        groups_examined=groups, metric_name="max_symbol_delta",
        metric_value=round(max_delta, 4), baseline_value=round(overall, 4),
        threshold_name="min_symbol_delta", threshold_value=threshold,
        threshold_crossed=crossed, status=status,
        reason=f"Max deviation: {worst_sym} delta={max_delta:.3f}R",
        breakdown=breakdown,
    )


def _evidence_exit_inefficiency(shadows: list[dict], ts: str) -> EvidenceRecord:
    by_pattern = defaultdict(list)
    for s in shadows:
        pat = s.get("pattern", "")
        r = s.get("r_multiple")
        mfe = s.get("mfe_r")
        if pat and r is not None and mfe is not None and mfe > 0:
            by_pattern[pat].append({"r": r, "mfe": mfe})

    groups = sum(1 for v in by_pattern.values() if len(v) >= 20)
    if groups == 0:
        return EvidenceRecord(detector_id="DET-008", detector_name="exit_inefficiency",
                              surface="exit_policy", timestamp=ts,
                              population_size=len(shadows), status=EvidenceStatus.INSUFFICIENT_DATA)

    worst_cap = 1.0
    worst_pat = ""
    breakdown = {}
    for pat, recs in by_pattern.items():
        if len(recs) >= 20:
            mr = statistics.mean(r["r"] for r in recs)
            mmfe = statistics.mean(r["mfe"] for r in recs)
            cap = mr / mmfe if mmfe > 0 else 0
            breakdown[pat] = {"n": len(recs), "mean_r": round(mr, 4), "mean_mfe": round(mmfe, 4), "capture": round(cap, 3)}
            if cap < worst_cap:
                worst_cap, worst_pat = cap, pat

    threshold = 0.30
    crossed = worst_cap < threshold and worst_pat != ""
    status = EvidenceStatus.TRIGGERED if crossed else (EvidenceStatus.SIGNAL if worst_cap < 0.50 else EvidenceStatus.NO_SIGNAL)

    return EvidenceRecord(
        detector_id="DET-008", detector_name="exit_inefficiency",
        surface="exit_policy", timestamp=ts,
        population_name="ALL_SHADOW_OUTCOMES", population_size=len(shadows),
        groups_examined=groups, metric_name="worst_capture_ratio",
        metric_value=round(worst_cap, 4),
        threshold_name="min_capture_ratio", threshold_value=threshold,
        threshold_crossed=crossed, status=status,
        reason=f"Worst: {worst_pat} capture={worst_cap:.0%}",
        breakdown=breakdown,
    )


def _evidence_guard_value(shadows: list[dict], ts: str) -> EvidenceRecord:
    v10_exec = [s["r_multiple"] for s in shadows
                if s.get("shadow_type") == "V10_PRIMARY" and s.get("v10_action") == "EXECUTE" and s.get("r_multiple") is not None]
    v10_notrade = [s["r_multiple"] for s in shadows
                   if s.get("shadow_type") == "V10_PRIMARY" and s.get("v10_action") == "NO_TRADE" and s.get("r_multiple") is not None]

    if len(v10_exec) < 30 or len(v10_notrade) < 30:
        return EvidenceRecord(detector_id="DET-009", detector_name="guard_value_controlled",
                              surface="risk_guards", timestamp=ts,
                              population_size=len(v10_exec) + len(v10_notrade),
                              status=EvidenceStatus.INSUFFICIENT_DATA,
                              reason=f"EXECUTE N={len(v10_exec)}, NO_TRADE N={len(v10_notrade)}")

    mean_exec = statistics.mean(v10_exec)
    mean_notrade = statistics.mean(v10_notrade)
    delta = mean_notrade - mean_exec

    threshold = 0.15
    crossed = delta > threshold
    status = EvidenceStatus.TRIGGERED if crossed else (EvidenceStatus.SIGNAL if delta > 0.05 else EvidenceStatus.NO_SIGNAL)

    return EvidenceRecord(
        detector_id="DET-009", detector_name="guard_value_controlled",
        surface="risk_guards", timestamp=ts,
        population_name="ALL_SHADOW (V10_PRIMARY only)", population_size=len(v10_exec) + len(v10_notrade),
        groups_examined=2, metric_name="notrade_minus_execute_r",
        metric_value=round(delta, 4), baseline_value=round(mean_exec, 4),
        effect_size=round(delta, 4),
        threshold_name="min_guard_delta", threshold_value=threshold,
        threshold_crossed=crossed, status=status,
        reason=f"EXECUTE R={mean_exec:+.3f} (N={len(v10_exec)}), NO_TRADE R={mean_notrade:+.3f} (N={len(v10_notrade)})",
    )


def _evidence_session_degradation(shadows: list[dict], ts: str) -> EvidenceRecord:
    by_session = defaultdict(list)
    for s in shadows:
        t = s.get("timestamp_decision_utc", 0)
        r = s.get("r_multiple")
        if t and r is not None:
            hour = _time.gmtime(int(t)).tm_hour
            if 7 <= hour < 12: session = "LONDON"
            elif 12 <= hour < 17: session = "NY"
            elif 0 <= hour < 7: session = "ASIA"
            else: session = "OFF"
            by_session[session].append(r)

    groups = sum(1 for v in by_session.values() if len(v) >= 30)
    if groups < 2:
        return EvidenceRecord(detector_id="DET-010", detector_name="session_degradation",
                              surface="session_behaviour", timestamp=ts,
                              population_size=len(shadows), groups_examined=groups,
                              status=EvidenceStatus.INSUFFICIENT_DATA)

    all_r = [r for vals in by_session.values() for r in vals]
    overall = statistics.mean(all_r)
    worst_delta = 0.0
    breakdown = {}
    for sess, vals in by_session.items():
        if len(vals) >= 30:
            m = statistics.mean(vals)
            d = m - overall
            breakdown[sess] = {"n": len(vals), "mean_r": round(m, 4), "delta": round(d, 4)}
            if d < worst_delta:
                worst_delta = d

    threshold = -0.25
    crossed = worst_delta < threshold
    status = EvidenceStatus.TRIGGERED if crossed else (EvidenceStatus.SIGNAL if worst_delta < -0.10 else EvidenceStatus.NO_SIGNAL)

    return EvidenceRecord(
        detector_id="DET-010", detector_name="session_degradation",
        surface="session_behaviour", timestamp=ts,
        population_name="real_shadows", population_size=len(shadows),
        groups_examined=groups, metric_name="worst_session_delta",
        metric_value=round(worst_delta, 4), baseline_value=round(overall, 4),
        threshold_name="min_session_delta", threshold_value=threshold,
        threshold_crossed=crossed, status=status,
        reason=f"Worst session delta={worst_delta:+.3f}R from overall {overall:+.3f}",
        breakdown=breakdown,
    )


def _evidence_spread_anomaly(shadows: list[dict], ts: str) -> EvidenceRecord:
    valid = [(s.get("spread_at_entry", 0), s.get("r_multiple"))
             for s in shadows if s.get("spread_at_entry") and s.get("r_multiple") is not None]
    if len(valid) < 60:
        return EvidenceRecord(detector_id="DET-011", detector_name="spread_anomaly",
                              surface="spread_cost", timestamp=ts,
                              population_size=len(valid), status=EvidenceStatus.INSUFFICIENT_DATA)

    spreads = [v[0] for v in valid]
    med = statistics.median(spreads)
    low = [r for sp, r in valid if sp <= med]
    high = [r for sp, r in valid if sp > med]
    delta = statistics.mean(high) - statistics.mean(low) if low and high else 0

    threshold = -0.10
    crossed = delta < threshold
    status = EvidenceStatus.TRIGGERED if crossed else (EvidenceStatus.SIGNAL if delta < -0.05 else EvidenceStatus.NO_SIGNAL)

    return EvidenceRecord(
        detector_id="DET-011", detector_name="spread_anomaly",
        surface="spread_cost", timestamp=ts,
        population_name="real_shadows", population_size=len(valid),
        groups_examined=2, metric_name="high_spread_delta",
        metric_value=round(delta, 4),
        threshold_name="spread_r_threshold", threshold_value=threshold,
        threshold_crossed=crossed, status=status,
        reason=f"High-spread R delta={delta:+.3f} (N_low={len(low)}, N_high={len(high)})",
    )


def _evidence_slippage(ts: str) -> EvidenceRecord:
    exec_dir = Path("logs/execution_results")
    if not exec_dir.exists():
        return EvidenceRecord(detector_id="DET-012", detector_name="slippage_deterioration",
                              surface="execution_slippage", timestamp=ts,
                              status=EvidenceStatus.NOT_OBSERVABLE, reason="No execution_results directory")

    slippages = []
    for f in sorted(exec_dir.rglob("*.jsonl")):
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rec = json.loads(line)
                    if rec.get("result_ok") and rec.get("slippage") is not None:
                        slippages.append((rec.get("timestamp_unix", 0), abs(rec["slippage"])))
        except Exception:
            continue

    if len(slippages) < 20:
        return EvidenceRecord(detector_id="DET-012", detector_name="slippage_deterioration",
                              surface="execution_slippage", timestamp=ts,
                              population_size=len(slippages), status=EvidenceStatus.INSUFFICIENT_DATA)

    slippages.sort(key=lambda x: x[0])
    mid = len(slippages) // 2
    early_mean = statistics.mean([s for _, s in slippages[:mid]])
    recent_mean = statistics.mean([s for _, s in slippages[mid:]])
    delta = recent_mean - early_mean

    threshold = 0.5
    crossed = delta > threshold and recent_mean > 0.5
    status = EvidenceStatus.TRIGGERED if crossed else (EvidenceStatus.SIGNAL if delta > 0.1 else EvidenceStatus.NO_SIGNAL)

    return EvidenceRecord(
        detector_id="DET-012", detector_name="slippage_deterioration",
        surface="execution_slippage", timestamp=ts,
        population_name="execution_results", population_size=len(slippages),
        groups_examined=2, metric_name="slippage_increase",
        metric_value=round(delta, 4), baseline_value=round(early_mean, 4),
        effect_size=round(delta, 4),
        threshold_name="slippage_threshold", threshold_value=threshold,
        threshold_crossed=crossed, status=status,
        reason=f"Early mean={early_mean:.3f}, Recent mean={recent_mean:.3f}, increase={delta:+.3f}",
    )


def _evidence_horizon_quality(shadows: list[dict], ts: str) -> EvidenceRecord:
    by_h = defaultdict(list)
    for s in shadows:
        h = s.get("trade_horizon", "")
        r = s.get("r_multiple")
        if h and r is not None:
            by_h[h].append(r)

    groups = sum(1 for v in by_h.values() if len(v) >= 30)
    if groups < 2:
        return EvidenceRecord(detector_id="DET-013", detector_name="horizon_quality",
                              surface="horizon_selection", timestamp=ts,
                              population_size=len(shadows), groups_examined=groups,
                              status=EvidenceStatus.INSUFFICIENT_DATA)

    all_r = [r for vals in by_h.values() for r in vals]
    overall = statistics.mean(all_r)
    worst_delta = 0.0
    breakdown = {}
    for h, vals in by_h.items():
        if len(vals) >= 30:
            m = statistics.mean(vals)
            breakdown[h] = {"n": len(vals), "mean_r": round(m, 4)}
            if m - overall < worst_delta:
                worst_delta = m - overall

    threshold = -0.20
    crossed = worst_delta < threshold
    status = EvidenceStatus.TRIGGERED if crossed else (EvidenceStatus.SIGNAL if worst_delta < -0.10 else EvidenceStatus.NO_SIGNAL)

    return EvidenceRecord(
        detector_id="DET-013", detector_name="horizon_quality",
        surface="horizon_selection", timestamp=ts,
        population_name="real_shadows", population_size=len(shadows),
        groups_examined=groups, metric_name="worst_horizon_delta",
        metric_value=round(worst_delta, 4), baseline_value=round(overall, 4),
        threshold_name="min_horizon_delta", threshold_value=threshold,
        threshold_crossed=crossed, status=status,
        reason=f"Worst horizon delta={worst_delta:+.3f}R",
        breakdown=breakdown,
    )


def _evidence_strategy_degradation(shadows: list[dict], ts: str) -> EvidenceRecord:
    by_strat = defaultdict(list)
    for s in shadows:
        strat = s.get("strategy_id", "")
        r = s.get("r_multiple")
        t = s.get("timestamp_decision_utc", 0)
        if strat and r is not None and t:
            by_strat[strat].append((t, r))

    max_deg = 0.0
    groups = 0
    for strat, recs in by_strat.items():
        if len(recs) < 60:
            continue
        groups += 1
        recs.sort(key=lambda x: x[0])
        mid = len(recs) // 2
        delta = statistics.mean([r for _, r in recs[mid:]]) - statistics.mean([r for _, r in recs[:mid]])
        if delta < max_deg:
            max_deg = delta

    if groups == 0:
        return EvidenceRecord(detector_id="DET-014", detector_name="strategy_degradation",
                              surface="strategy_selection", timestamp=ts,
                              population_size=len(shadows), status=EvidenceStatus.INSUFFICIENT_DATA)

    threshold = -0.20
    crossed = max_deg < threshold
    status = EvidenceStatus.TRIGGERED if crossed else (EvidenceStatus.SIGNAL if max_deg < -0.10 else EvidenceStatus.NO_SIGNAL)

    return EvidenceRecord(
        detector_id="DET-014", detector_name="strategy_degradation",
        surface="strategy_selection", timestamp=ts,
        population_name="real_shadows", population_size=len(shadows),
        groups_examined=groups, metric_name="worst_strategy_temporal_delta",
        metric_value=round(max_deg, 4),
        threshold_name="min_strategy_delta", threshold_value=threshold,
        threshold_crossed=crossed, status=status,
        reason=f"Worst strategy degradation: {max_deg:+.3f}R",
    )


def _evidence_shadow_reality(ts: str) -> EvidenceRecord:
    try:
        from research_engine.v10.universes.shadow_reality_universe import ShadowRealityUniverseBuilder
        builder = ShadowRealityUniverseBuilder()
        builder.build()
        stats = builder.get_statistics()
    except Exception:
        return EvidenceRecord(detector_id="DET-015", detector_name="sr_divergence",
                              surface="shadow_reality", timestamp=ts,
                              status=EvidenceStatus.NOT_OBSERVABLE, reason="SR bridge unavailable")

    n = stats.get("n", 0)
    if n < 15:
        return EvidenceRecord(detector_id="DET-015", detector_name="sr_divergence",
                              surface="shadow_reality", timestamp=ts,
                              population_size=n, status=EvidenceStatus.INSUFFICIENT_DATA,
                              reason=f"N={n} < 15 matched pairs")

    mean_delta = stats.get("mean_delta_r", 0)
    threshold = 0.30
    crossed = abs(mean_delta) > threshold
    status = EvidenceStatus.TRIGGERED if crossed else (EvidenceStatus.SIGNAL if abs(mean_delta) > 0.15 else EvidenceStatus.NO_SIGNAL)

    return EvidenceRecord(
        detector_id="DET-015", detector_name="sr_divergence",
        surface="shadow_reality", timestamp=ts,
        population_name="Shadow Reality Bridge", population_size=n,
        groups_examined=2, metric_name="mean_delta_r",
        metric_value=round(mean_delta, 4),
        threshold_name="divergence_threshold", threshold_value=threshold,
        threshold_crossed=crossed, status=status,
        reason=f"Mean delta_r={mean_delta:+.4f} (N={n}), exit_match={stats.get('exit_reason_match_rate', 0):.0%}",
        breakdown=stats,
    )


def _evidence_drawdown(ts: str) -> EvidenceRecord:
    dt_dir = Path("logs/decision_trace")
    if not dt_dir.exists():
        return EvidenceRecord(detector_id="DET-016", detector_name="drawdown_approaching",
                              surface="drawdown_daily_loss", timestamp=ts,
                              status=EvidenceStatus.NOT_OBSERVABLE)

    daily_losses = []
    for f in sorted(dt_dir.rglob("*.jsonl"), reverse=True):
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                snap = rec.get("v10_account_snapshot")
                if snap and snap.get("daily_loss_pct"):
                    daily_losses.append(abs(snap["daily_loss_pct"]))
                    if len(daily_losses) >= 50:
                        break
            except Exception:
                continue
        if len(daily_losses) >= 50:
            break

    if len(daily_losses) < 5:
        return EvidenceRecord(detector_id="DET-016", detector_name="drawdown_approaching",
                              surface="drawdown_daily_loss", timestamp=ts,
                              population_size=len(daily_losses),
                              status=EvidenceStatus.INSUFFICIENT_DATA)

    max_daily = max(daily_losses)
    limit = 0.05
    warning = limit * 0.70

    crossed = max_daily >= warning
    status = EvidenceStatus.TRIGGERED if crossed else (EvidenceStatus.SIGNAL if max_daily >= limit * 0.50 else EvidenceStatus.NO_SIGNAL)

    return EvidenceRecord(
        detector_id="DET-016", detector_name="drawdown_approaching",
        surface="drawdown_daily_loss", timestamp=ts,
        population_name="decision_trace (account_snapshot)", population_size=len(daily_losses),
        metric_name="max_daily_loss_pct", metric_value=round(max_daily, 4),
        threshold_name="daily_loss_warning (70% of 5%)", threshold_value=round(warning, 4),
        threshold_crossed=crossed, status=status,
        reason=f"Max daily loss: {max_daily:.2%} (limit: {limit:.0%}, warning: {warning:.1%})",
    )


def _evidence_black_surfaces(ts: str) -> list[EvidenceRecord]:
    """Evidence for the 5 formerly-BLACK surfaces from research_events."""
    event_dir = Path("logs/research_events")
    if not event_dir.exists():
        return [
            EvidenceRecord(detector_id=f"DET-{17+i:03d}", detector_name=name,
                           surface=surface, timestamp=ts,
                           status=EvidenceStatus.NOT_OBSERVABLE,
                           reason="No research_events data (bot has not traded since instrumentation)")
            for i, (name, surface) in enumerate([
                ("cooldown_anomaly", "trade_cooldown"),
                ("correlation_blocking", "correlation_controls"),
                ("position_limit_blocking", "position_limits"),
                ("recovery_anomaly", "recovery_restart"),
                ("config_change", "configuration"),
            ])
        ]

    events = []
    for f in sorted(event_dir.rglob("*.jsonl")):
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    events.append(json.loads(line))
        except Exception:
            continue

    records = []
    guard_events = [e for e in events if e.get("event_type") == "GUARD_DECISION"]
    total_guards = len(guard_events)

    # Cooldown
    cd_blocks = sum(1 for e in guard_events if e.get("guard_name") == "trade_cooldown" and not e.get("allowed"))
    cd_rate = cd_blocks / total_guards if total_guards > 0 else 0
    if total_guards == 0:
        cd_status = EvidenceStatus.NOT_OBSERVABLE
    elif cd_blocks == 0:
        cd_status = EvidenceStatus.NO_SIGNAL
    elif cd_rate >= 0.50:
        cd_status = EvidenceStatus.TRIGGERED
    elif cd_rate >= 0.20:
        cd_status = EvidenceStatus.SIGNAL
    else:
        cd_status = EvidenceStatus.NO_SIGNAL

    records.append(EvidenceRecord(
        detector_id="DET-017", detector_name="cooldown_anomaly",
        surface="trade_cooldown", timestamp=ts,
        population_name="research_events (GUARD_DECISION)", population_size=total_guards,
        metric_name="cooldown_block_rate", metric_value=round(cd_rate, 4),
        threshold_name="excessive_block_rate", threshold_value=0.50,
        threshold_crossed=cd_rate >= 0.50, status=cd_status,
        reason=f"{cd_blocks}/{total_guards} blocked by cooldown",
    ))

    # Correlation
    corr_blocks = sum(1 for e in guard_events if e.get("guard_name") == "correlation_guard" and not e.get("allowed"))
    records.append(EvidenceRecord(
        detector_id="DET-018", detector_name="correlation_blocking",
        surface="correlation_controls", timestamp=ts,
        population_name="research_events (GUARD_DECISION)", population_size=total_guards,
        metric_name="correlation_blocks", metric_value=corr_blocks,
        threshold_name="min_blocks", threshold_value=5,
        threshold_crossed=corr_blocks >= 5,
        status=EvidenceStatus.TRIGGERED if corr_blocks >= 5 else (EvidenceStatus.SIGNAL if corr_blocks > 0 else EvidenceStatus.NO_SIGNAL if total_guards > 0 else EvidenceStatus.NOT_OBSERVABLE),
        reason=f"{corr_blocks} correlation blocks",
    ))

    # Position limits
    pos_blocks = sum(1 for e in guard_events if e.get("guard_name") == "portfolio_exposure" and not e.get("allowed"))
    records.append(EvidenceRecord(
        detector_id="DET-019", detector_name="position_limit_blocking",
        surface="position_limits", timestamp=ts,
        population_name="research_events (GUARD_DECISION)", population_size=total_guards,
        metric_name="position_limit_blocks", metric_value=pos_blocks,
        threshold_name="min_blocks", threshold_value=5,
        threshold_crossed=pos_blocks >= 5,
        status=EvidenceStatus.TRIGGERED if pos_blocks >= 5 else (EvidenceStatus.SIGNAL if pos_blocks > 0 else EvidenceStatus.NO_SIGNAL if total_guards > 0 else EvidenceStatus.NOT_OBSERVABLE),
        reason=f"{pos_blocks} position limit blocks",
    ))

    # Recovery
    recovery_events = [e for e in events if e.get("event_type") == "RECOVERY"]
    id_fails = sum(e.get("identity_failed", 0) for e in recovery_events)
    records.append(EvidenceRecord(
        detector_id="DET-020", detector_name="recovery_anomaly",
        surface="recovery_restart", timestamp=ts,
        population_name="research_events (RECOVERY)", population_size=len(recovery_events),
        metric_name="identity_failures", metric_value=id_fails,
        threshold_name="any_failure", threshold_value=1,
        threshold_crossed=id_fails > 0,
        status=EvidenceStatus.TRIGGERED if id_fails > 0 else (EvidenceStatus.NO_SIGNAL if recovery_events else EvidenceStatus.NOT_OBSERVABLE),
        reason=f"{id_fails} identity restoration failures across {len(recovery_events)} recoveries",
    ))

    # Config
    config_events = [e for e in events if e.get("event_type") == "CONFIG_SNAPSHOT"]
    hashes = list(dict.fromkeys(e.get("config_hash", "") for e in config_events if e.get("config_hash")))
    records.append(EvidenceRecord(
        detector_id="DET-021", detector_name="config_change",
        surface="configuration", timestamp=ts,
        population_name="research_events (CONFIG_SNAPSHOT)", population_size=len(config_events),
        metric_name="config_versions", metric_value=len(hashes),
        threshold_name="version_change", threshold_value=2,
        threshold_crossed=len(hashes) >= 2,
        status=EvidenceStatus.TRIGGERED if len(hashes) >= 2 else (EvidenceStatus.NO_SIGNAL if config_events else EvidenceStatus.NOT_OBSERVABLE),
        reason=f"{len(hashes)} config versions observed across {len(config_events)} snapshots",
    ))

    return records


# ═══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE
# ═══════════════════════════════════════════════════════════════════════════════

_EVIDENCE_DIR = Path("logs/research_lifecycle/evidence")


def _persist_evidence(report: EvidenceCycleReport) -> None:
    """Persist the evidence cycle report to JSONL. Never raises."""
    try:
        _EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        path = _EVIDENCE_DIR / "evidence_cycles.jsonl"
        line = json.dumps(report.to_dict(), separators=(",", ":"), default=str) + "\n"
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
    except Exception:
        pass
