"""
Surface Detectors — Autonomous unknown-discovery detectors for all observable bot surfaces.

Extends FindingTriggerEngine with detectors for surfaces not covered by the
original 8 pattern/shadow-focused detectors.

Each detector answers:
    1. WHAT changed?
    2. WHY is it abnormal?
    3. WHAT population demonstrates it?
    4. WHAT evidence would confirm/reject the hypothesis?

Population safety:
    - V10_PRIMARY never mixed with HORIZON_ALTERNATIVE without explicit stratification
    - EXECUTE and NO_TRADE compared only when shadow_type is controlled
    - Minimum sample requirements enforced per-group
    - Insufficient data → NO_TRIGGER (never a misleading trigger)

This module NEVER modifies production trading.
"""

from __future__ import annotations

import json
import statistics
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_engine.lifecycle.finding_trigger import (
    FindingTrigger,
    FindingTriggerEngine,
    TriggerCategory,
    TriggerStatus,
)
from research_engine.lifecycle.hypothesis import HypothesisCategory
from research_engine.lifecycle.experiment_protocol import ExperimentType


# ═══════════════════════════════════════════════════════════════════════════════
# NEW TRIGGER CATEGORIES (extend existing enum dynamically would be fragile;
# instead we add values to TriggerCategory before this module is used)
# ═══════════════════════════════════════════════════════════════════════════════

# New categories are defined in finding_trigger.py TriggerCategory enum.
# No runtime extension needed.


# ═══════════════════════════════════════════════════════════════════════════════
# DETECTOR IMPLEMENTATIONS
# ═══════════════════════════════════════════════════════════════════════════════

def detect_session_degradation(
    shadows: list[dict[str, Any]],
    *,
    engine: FindingTriggerEngine,
    source: str = "",
    min_n_per_session: int = 30,
    min_delta: float = 0.25,
) -> list[FindingTrigger]:
    """
    Detect sessions (LONDON/NY/ASIA) where performance has degraded.

    Compares per-session R against overall mean. Flags sessions with
    significantly worse performance.
    """
    from collections import defaultdict

    by_session: dict[str, list[float]] = defaultdict(list)
    for s in shadows:
        ts = s.get("timestamp_decision_utc", 0)
        if not ts:
            continue
        r = s.get("r_multiple")
        if r is None:
            continue
        # Classify session from UTC hour
        import time
        hour = time.gmtime(int(ts)).tm_hour
        if 7 <= hour < 12:
            session = "LONDON"
        elif 12 <= hour < 17:
            session = "NY"
        elif 0 <= hour < 7:
            session = "ASIA"
        else:
            session = "OFF_SESSION"
        by_session[session].append(r)

    if not by_session:
        return []

    all_r = [r for vals in by_session.values() for r in vals]
    if len(all_r) < min_n_per_session * 2:
        return []
    overall_mean = statistics.mean(all_r)

    triggers: list[FindingTrigger] = []
    for session, vals in by_session.items():
        if len(vals) < min_n_per_session:
            continue
        session_mean = statistics.mean(vals)
        delta = session_mean - overall_mean
        if delta < -min_delta:
            trigger = FindingTrigger(
                finding_id=f"session_deg_{session}_{len(vals)}",
                source=source,
                category=TriggerCategory.SESSION_DEGRADATION,
                title=f"{session} session underperforms by {abs(delta):.3f}R",
                observation=(
                    f"{session} (N={len(vals)}) has mean R={session_mean:+.3f} vs "
                    f"overall {overall_mean:+.3f}. Delta={delta:+.3f}R."
                ),
                sample_size=len(vals),
                confidence="MEDIUM",
                suggested_patterns=[],
                trigger_reason=f"session_delta={delta:+.3f} < -{min_delta}",
                priority=2,
                suggested_experiment_type=ExperimentType.CONDITIONING_ANALYSIS,
                suggested_hypothesis_category=HypothesisCategory.REGIME_CONDITIONING,
                suggested_claim=f"Filtering {session} session improves expectancy",
                suggested_null=f"Session does not materially affect performance",
            )
            result = engine._screen(trigger)
            if result:
                triggers.append(result)
    return triggers


def detect_spread_anomaly(
    shadows: list[dict[str, Any]],
    *,
    engine: FindingTriggerEngine,
    source: str = "",
    min_n: int = 30,
    spread_r_threshold: float = 0.10,
) -> list[FindingTrigger]:
    """
    Detect when high-spread entries produce materially worse outcomes.

    Compares R for entries above median spread vs below.
    """
    valid = [(s.get("spread_at_entry", 0), s.get("r_multiple"))
             for s in shadows if s.get("spread_at_entry") and s.get("r_multiple") is not None]
    if len(valid) < min_n * 2:
        return []

    spreads = [v[0] for v in valid]
    median_spread = statistics.median(spreads)
    low_spread = [r for sp, r in valid if sp <= median_spread]
    high_spread = [r for sp, r in valid if sp > median_spread]

    if len(low_spread) < min_n or len(high_spread) < min_n:
        return []

    mean_low = statistics.mean(low_spread)
    mean_high = statistics.mean(high_spread)
    delta = mean_high - mean_low

    triggers: list[FindingTrigger] = []
    if delta < -spread_r_threshold:
        trigger = FindingTrigger(
            finding_id=f"spread_anom_{len(valid)}_{abs(delta):.2f}",
            source=source,
            category=TriggerCategory.SPREAD_ANOMALY,
            title=f"High-spread entries underperform by {abs(delta):.3f}R",
            observation=(
                f"Entries above median spread ({median_spread:.5f}): N={len(high_spread)}, "
                f"mean R={mean_high:+.3f}. Below: N={len(low_spread)}, mean R={mean_low:+.3f}. "
                f"Delta={delta:+.3f}R."
            ),
            sample_size=len(valid),
            confidence="MEDIUM",
            suggested_patterns=[],
            trigger_reason=f"high_spread_delta={delta:+.3f} < -{spread_r_threshold}",
            priority=2,
            suggested_experiment_type=ExperimentType.CONDITIONING_ANALYSIS,
            suggested_hypothesis_category=HypothesisCategory.REGIME_CONDITIONING,
            suggested_claim="Filtering high-spread entries improves expectancy",
            suggested_null="Spread level does not affect trade outcome",
        )
        result = engine._screen(trigger)
        if result:
            triggers.append(result)
    return triggers


def detect_slippage_deterioration(
    exec_results: list[dict[str, Any]],
    *,
    engine: FindingTriggerEngine,
    source: str = "",
    min_n: int = 20,
    slippage_threshold: float = 0.5,
) -> list[FindingTrigger]:
    """
    Detect increasing execution slippage from execution_results data.

    Compares recent slippage to historical baseline.
    """
    valid = [(r.get("timestamp_unix", 0), r.get("slippage", 0))
             for r in exec_results if r.get("result_ok") and r.get("slippage") is not None]
    if len(valid) < min_n * 2:
        return []

    valid.sort(key=lambda x: x[0])
    mid = len(valid) // 2
    early = [abs(s) for _, s in valid[:mid]]
    recent = [abs(s) for _, s in valid[mid:]]

    if not early or not recent:
        return []

    mean_early = statistics.mean(early)
    mean_recent = statistics.mean(recent)

    triggers: list[FindingTrigger] = []
    if mean_recent > mean_early + slippage_threshold and mean_recent > 0.5:
        trigger = FindingTrigger(
            finding_id=f"slippage_det_{len(valid)}_{mean_recent:.2f}",
            source=source,
            category=TriggerCategory.SLIPPAGE_DETERIORATION,
            title=f"Slippage increasing: recent {mean_recent:.2f} vs early {mean_early:.2f}",
            observation=(
                f"Mean absolute slippage increased from {mean_early:.3f} (N={len(early)}) "
                f"to {mean_recent:.3f} (N={len(recent)})."
            ),
            sample_size=len(valid),
            confidence="MEDIUM",
            suggested_patterns=[],
            trigger_reason=f"slippage_increase={mean_recent-mean_early:.3f}",
            priority=2,
            suggested_experiment_type=ExperimentType.POPULATION_COMPARISON,
            suggested_hypothesis_category=HypothesisCategory.EXECUTION_LEAKAGE,
            suggested_claim="Execution quality is deteriorating over time",
            suggested_null="Slippage is stable over time",
        )
        result = engine._screen(trigger)
        if result:
            triggers.append(result)
    return triggers


def detect_horizon_quality(
    shadows: list[dict[str, Any]],
    *,
    engine: FindingTriggerEngine,
    source: str = "",
    min_n_per_horizon: int = 30,
    min_delta: float = 0.20,
) -> list[FindingTrigger]:
    """
    Detect if a specific trade horizon consistently underperforms.
    """
    by_horizon: dict[str, list[float]] = defaultdict(list)
    for s in shadows:
        h = s.get("trade_horizon", "")
        r = s.get("r_multiple")
        if h and r is not None:
            by_horizon[h].append(r)

    if len(by_horizon) < 2:
        return []

    all_r = [r for vals in by_horizon.values() for r in vals]
    if not all_r:
        return []
    overall_mean = statistics.mean(all_r)

    triggers: list[FindingTrigger] = []
    for horizon, vals in by_horizon.items():
        if len(vals) < min_n_per_horizon:
            continue
        h_mean = statistics.mean(vals)
        delta = h_mean - overall_mean
        if delta < -min_delta:
            trigger = FindingTrigger(
                finding_id=f"horizon_q_{horizon}_{len(vals)}",
                source=source,
                category=TriggerCategory.HORIZON_QUALITY,
                title=f"{horizon} horizon underperforms by {abs(delta):.3f}R",
                observation=(
                    f"Horizon {horizon} (N={len(vals)}): mean R={h_mean:+.3f} vs "
                    f"overall {overall_mean:+.3f}. Delta={delta:+.3f}."
                ),
                sample_size=len(vals),
                confidence="MEDIUM",
                suggested_patterns=[],
                trigger_reason=f"horizon_delta={delta:+.3f} < -{min_delta}",
                priority=2,
                suggested_experiment_type=ExperimentType.CONDITIONING_ANALYSIS,
                suggested_hypothesis_category=HypothesisCategory.REGIME_CONDITIONING,
                suggested_claim=f"Excluding {horizon} horizon improves expectancy",
                suggested_null="Horizon selection does not materially affect outcome",
            )
            result = engine._screen(trigger)
            if result:
                triggers.append(result)
    return triggers


def detect_strategy_degradation(
    shadows: list[dict[str, Any]],
    *,
    engine: FindingTriggerEngine,
    source: str = "",
    min_n: int = 30,
    min_delta: float = 0.20,
) -> list[FindingTrigger]:
    """
    Detect if a specific strategy family is degrading (recent worse than historical).
    """
    by_strategy: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for s in shadows:
        strat = s.get("strategy_id", "")
        r = s.get("r_multiple")
        ts = s.get("timestamp_decision_utc", 0)
        if strat and r is not None and ts:
            by_strategy[strat].append((ts, r))

    triggers: list[FindingTrigger] = []
    for strat, records in by_strategy.items():
        if len(records) < min_n * 2:
            continue
        records.sort(key=lambda x: x[0])
        mid = len(records) // 2
        early_r = [r for _, r in records[:mid]]
        recent_r = [r for _, r in records[mid:]]
        if len(early_r) < min_n or len(recent_r) < min_n:
            continue
        delta = statistics.mean(recent_r) - statistics.mean(early_r)
        if delta < -min_delta:
            trigger = FindingTrigger(
                finding_id=f"strat_deg_{strat}_{len(records)}",
                source=source,
                category=TriggerCategory.STRATEGY_DEGRADATION,
                title=f"Strategy {strat} degrading: recent {delta:+.3f}R worse",
                observation=(
                    f"Strategy {strat}: early (N={len(early_r)}) mean R={statistics.mean(early_r):+.3f}, "
                    f"recent (N={len(recent_r)}) mean R={statistics.mean(recent_r):+.3f}. "
                    f"Deterioration={delta:+.3f}R."
                ),
                sample_size=len(records),
                confidence="MEDIUM",
                suggested_patterns=[],
                trigger_reason=f"strategy_degradation={delta:+.3f} < -{min_delta}",
                priority=1,
                suggested_experiment_type=ExperimentType.OOS_VALIDATION,
                suggested_hypothesis_category=HypothesisCategory.OTHER,
                suggested_claim=f"Strategy {strat} has lost its edge over time",
                suggested_null=f"Strategy {strat} performance is stable",
            )
            result = engine._screen(trigger)
            if result:
                triggers.append(result)
    return triggers


def detect_sr_divergence(
    *,
    engine: FindingTriggerEngine,
    source: str = "",
    min_matched: int = 15,
    divergence_threshold: float = 0.30,
) -> list[FindingTrigger]:
    """
    RETIRED (Phase 1I-C): Shadow↔Reality divergence detection.

    The ShadowRealityUniverse joined V10_PRIMARY EXECUTE shadows to the trade
    journal via COR-* correlation_ids — a relationship specific to the retired
    V10_PRIMARY architecture. It was retired together with V10_PRIMARY rather
    than being faked onto the canonical Horizon lineage (whose identity model
    intentionally does not claim fill identity with the trade journal).

    Kept as a graceful no-op so existing call sites remain stable. A new
    shadow↔reality system may be designed later around the canonical
    lineage's own identity model.
    """
    return []


def detect_drawdown_approaching(
    *,
    engine: FindingTriggerEngine,
    source: str = "",
    daily_loss_limit: float = 0.05,
    max_drawdown_limit: float = 0.10,
    warning_fraction: float = 0.70,
) -> list[FindingTrigger]:
    """
    Detect when account drawdown or daily loss approaches prop-firm limits.

    Reads decision_trace v10_account_snapshot for recent equity/drawdown state.
    """
    dt_dir = Path("logs/decision_trace")
    if not dt_dir.exists():
        return []

    # Read most recent decision traces for account snapshots
    recent_snapshots: list[dict] = []
    for f in sorted(dt_dir.rglob("*.jsonl"), reverse=True):
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                snap = rec.get("v10_account_snapshot")
                if snap and snap.get("equity"):
                    recent_snapshots.append(snap)
                    if len(recent_snapshots) >= 100:
                        break
            except Exception:
                continue
        if len(recent_snapshots) >= 100:
            break

    if len(recent_snapshots) < 5:
        return []

    triggers: list[FindingTrigger] = []

    # Check daily loss
    daily_losses = [s.get("daily_loss_pct", 0) for s in recent_snapshots if s.get("daily_loss_pct")]
    if daily_losses:
        max_daily = max(abs(d) for d in daily_losses)
        if max_daily >= daily_loss_limit * warning_fraction:
            severity = "CRITICAL" if max_daily >= daily_loss_limit * 0.9 else "WARNING"
            trigger = FindingTrigger(
                finding_id=f"dd_daily_{max_daily:.3f}",
                source=source,
                category=TriggerCategory.DRAWDOWN_APPROACHING,
                title=f"Daily loss at {max_daily:.1%} — {severity} ({daily_loss_limit:.0%} limit)",
                observation=(
                    f"Recent daily loss reached {max_daily:.2%} of equity. "
                    f"Prop limit is typically {daily_loss_limit:.0%}. "
                    f"Warning threshold {daily_loss_limit * warning_fraction:.1%} breached."
                ),
                sample_size=len(recent_snapshots),
                confidence="HIGH" if severity == "CRITICAL" else "MEDIUM",
                suggested_patterns=[],
                trigger_reason=f"daily_loss={max_daily:.3f} >= {daily_loss_limit * warning_fraction:.3f}",
                priority=0 if severity == "CRITICAL" else 1,
                suggested_experiment_type=ExperimentType.OOS_VALIDATION,
                suggested_hypothesis_category=HypothesisCategory.OTHER,
                suggested_claim="Current loss rate risks breaching prop daily limit",
                suggested_null="Daily loss is within acceptable bounds",
            )
            result = engine._screen(trigger)
            if result:
                triggers.append(result)

    return triggers


def detect_guard_value_controlled(
    shadows: list[dict[str, Any]],
    *,
    engine: FindingTriggerEngine,
    source: str = "",
    min_n_per_group: int = 30,
    min_delta: float = 0.15,
) -> list[FindingTrigger]:
    """
    Guard-value detector using controlled population comparison.

    Compares ONLY canonical-lineage (HORIZON_ALTERNATIVE) shadows:
    EXECUTE vs NO_TRADE responses, both sharing STRUCTURE_BASED geometry,
    so the comparison stays internally controlled.
    """
    # Filter to canonical shadow lineage only
    v10_execute = [s for s in shadows
                   if s.get("shadow_type") == "HORIZON_ALTERNATIVE"
                   and s.get("v10_action") == "EXECUTE"
                   and s.get("r_multiple") is not None]
    v10_no_trade = [s for s in shadows
                    if s.get("shadow_type") == "HORIZON_ALTERNATIVE"
                    and s.get("v10_action") == "NO_TRADE"
                    and s.get("r_multiple") is not None]

    triggers: list[FindingTrigger] = []

    if len(v10_execute) < min_n_per_group or len(v10_no_trade) < min_n_per_group:
        return triggers

    exec_r = [s["r_multiple"] for s in v10_execute]
    notrade_r = [s["r_multiple"] for s in v10_no_trade]

    mean_exec = statistics.mean(exec_r)
    mean_notrade = statistics.mean(notrade_r)
    delta = mean_notrade - mean_exec

    # Only trigger if NO_TRADE outperforms EXECUTE by meaningful margin
    if delta > min_delta:
        trigger = FindingTrigger(
            finding_id=f"guard_ctrl_{len(v10_execute)}_{len(v10_no_trade)}_{delta:.2f}",
            source=source,
            category=TriggerCategory.GUARD_VALUE_NEGATIVE,
            title=f"Guards may destroy edge (controlled): NO_TRADE R ({mean_notrade:+.3f}) > EXECUTE R ({mean_exec:+.3f})",
            observation=(
                f"HORIZON_ALTERNATIVE controlled comparison: "
                f"EXECUTE (N={len(v10_execute)}) mean R={mean_exec:+.3f}. "
                f"NO_TRADE (N={len(v10_no_trade)}) mean R={mean_notrade:+.3f}. "
                f"Delta={delta:+.3f}R. Same shadow_type, same geometry source."
            ),
            sample_size=len(v10_execute) + len(v10_no_trade),
            confidence="MEDIUM",
            suggested_patterns=[],
            trigger_reason=f"controlled_delta={delta:+.3f} > {min_delta}",
            priority=1,
            suggested_experiment_type=ExperimentType.CONDITIONING_ANALYSIS,
            suggested_hypothesis_category=HypothesisCategory.GUARD_QUALITY,
            suggested_claim="Pipeline guards systematically reject better opportunities",
            suggested_null="Guard rejection is not correlated with counterfactual quality",
        )
        result = engine._screen(trigger)
        if result:
            triggers.append(result)

    return triggers


# ═══════════════════════════════════════════════════════════════════════════════
# DETECTOR REGISTRY
# ═══════════════════════════════════════════════════════════════════════════════

DETECTOR_REGISTRY: list[dict[str, Any]] = [
    # Original detectors (in FindingTriggerEngine)
    {"detector_id": "DET-001", "name": "detect_from_pattern_performance", "surface": "pattern_performance", "category": "POOR/STRONG_PATTERN_PERFORMANCE", "population": "real_shadows (PRIMARY)", "shadow_testable": False, "status": "ACTIVE"},
    {"detector_id": "DET-002", "name": "detect_direction_asymmetry", "surface": "pattern_direction", "category": "DIRECTION_ASYMMETRY", "population": "real_shadows (PRIMARY)", "shadow_testable": True, "status": "ACTIVE"},
    {"detector_id": "DET-003", "name": "detect_regime_anomaly", "surface": "market_regime", "category": "REGIME_ANOMALY", "population": "real_shadows (PRIMARY)", "shadow_testable": True, "status": "ACTIVE"},
    {"detector_id": "DET-004", "name": "detect_score_monotonicity", "surface": "decision_scoring", "category": "SCORE_MONOTONICITY", "population": "real_shadows (PRIMARY)", "shadow_testable": False, "status": "ACTIVE"},
    {"detector_id": "DET-005", "name": "detect_temporal_instability", "surface": "temporal_stability", "category": "TEMPORAL_INSTABILITY", "population": "real_shadows (PRIMARY)", "shadow_testable": False, "status": "ACTIVE"},
    {"detector_id": "DET-006", "name": "detect_geometry_anomaly", "surface": "entry_geometry", "category": "GEOMETRY_ANOMALY", "population": "real_shadows (PRIMARY)", "shadow_testable": True, "status": "ACTIVE"},
    {"detector_id": "DET-007", "name": "detect_symbol_anomaly", "surface": "symbol_performance", "category": "SYMBOL_ANOMALY", "population": "real_shadows (PRIMARY)", "shadow_testable": False, "status": "ACTIVE"},
    {"detector_id": "DET-008", "name": "detect_exit_inefficiency", "surface": "exit_policy", "category": "EXIT_INEFFICIENCY", "population": "ALL_SHADOW_OUTCOMES", "shadow_testable": True, "status": "ACTIVE"},
    # Corrected guard value
    {"detector_id": "DET-009", "name": "detect_guard_value_controlled", "surface": "risk_guards", "category": "GUARD_VALUE_NEGATIVE", "population": "ALL_SHADOW (V10_PRIMARY only)", "shadow_testable": False, "status": "ACTIVE"},
    # New surface detectors
    {"detector_id": "DET-010", "name": "detect_session_degradation", "surface": "session_behaviour", "category": "SESSION_DEGRADATION", "population": "real_shadows (PRIMARY)", "shadow_testable": True, "status": "ACTIVE"},
    {"detector_id": "DET-011", "name": "detect_spread_anomaly", "surface": "spread_cost", "category": "SPREAD_ANOMALY", "population": "real_shadows (PRIMARY)", "shadow_testable": True, "status": "ACTIVE"},
    {"detector_id": "DET-012", "name": "detect_slippage_deterioration", "surface": "execution_slippage", "category": "SLIPPAGE_DETERIORATION", "population": "execution_results", "shadow_testable": False, "status": "ACTIVE"},
    {"detector_id": "DET-013", "name": "detect_horizon_quality", "surface": "horizon_selection", "category": "HORIZON_QUALITY", "population": "real_shadows (PRIMARY)", "shadow_testable": True, "status": "ACTIVE"},
    {"detector_id": "DET-014", "name": "detect_strategy_degradation", "surface": "strategy_selection", "category": "STRATEGY_DEGRADATION", "population": "real_shadows (PRIMARY)", "shadow_testable": False, "status": "ACTIVE"},
    {"detector_id": "DET-015", "name": "detect_sr_divergence", "surface": "shadow_reality", "category": "SR_DIVERGENCE", "population": "Shadow Reality Bridge", "shadow_testable": False, "status": "RETIRED"},  # Retired with V10_PRIMARY (Phase 1I-C)
    {"detector_id": "DET-016", "name": "detect_drawdown_approaching", "surface": "drawdown_daily_loss", "category": "DRAWDOWN_APPROACHING", "population": "decision_trace (account_snapshot)", "shadow_testable": False, "status": "ACTIVE"},
    # BLACK surface detectors (require research_events instrumentation)
    {"detector_id": "DET-017", "name": "detect_cooldown_anomaly", "surface": "trade_cooldown", "category": "GUARD_VALUE_NEGATIVE", "population": "research_events (GUARD_DECISION)", "shadow_testable": False, "status": "ACTIVE"},
    {"detector_id": "DET-018", "name": "detect_correlation_blocking", "surface": "correlation_controls", "category": "GUARD_VALUE_NEGATIVE", "population": "research_events (GUARD_DECISION)", "shadow_testable": False, "status": "ACTIVE"},
    {"detector_id": "DET-019", "name": "detect_position_limit_blocking", "surface": "position_limits", "category": "GUARD_VALUE_NEGATIVE", "population": "research_events (GUARD_DECISION)", "shadow_testable": False, "status": "ACTIVE"},
    {"detector_id": "DET-020", "name": "detect_recovery_anomaly", "surface": "recovery_restart", "category": "DRAWDOWN_APPROACHING", "population": "research_events (RECOVERY)", "shadow_testable": False, "status": "ACTIVE"},
    {"detector_id": "DET-021", "name": "detect_config_change", "surface": "configuration", "category": "DRAWDOWN_APPROACHING", "population": "research_events (CONFIG_SNAPSHOT)", "shadow_testable": False, "status": "ACTIVE"},
]


def get_registry() -> list[dict[str, Any]]:
    """Return the detector registry."""
    return DETECTOR_REGISTRY


# ═══════════════════════════════════════════════════════════════════════════════
# RUNNER INTEGRATION FUNCTION
# ═══════════════════════════════════════════════════════════════════════════════

def run_surface_detectors(
    *,
    engine: FindingTriggerEngine,
    real_shadows: list[dict[str, Any]],
    all_shadows: list[dict[str, Any]],
    source: str = "research_cycle_runner",
) -> list[FindingTrigger]:
    """
    Run all surface detectors in a single call.

    Called from ResearchCycleRunner._detect_findings() after the original detectors.
    """
    triggers: list[FindingTrigger] = []

    # Session degradation (on PRIMARY with correlation_id)
    triggers.extend(detect_session_degradation(real_shadows, engine=engine, source=source))

    # Spread anomaly (on PRIMARY with correlation_id)
    triggers.extend(detect_spread_anomaly(real_shadows, engine=engine, source=source))

    # Horizon quality (on PRIMARY with correlation_id)
    triggers.extend(detect_horizon_quality(real_shadows, engine=engine, source=source))

    # Strategy degradation (on PRIMARY with correlation_id)
    triggers.extend(detect_strategy_degradation(real_shadows, engine=engine, source=source))

    # Guard value controlled (on all shadows, HORIZON_ALTERNATIVE filter inside)
    triggers.extend(detect_guard_value_controlled(all_shadows, engine=engine, source=source))

    # Slippage deterioration (from execution_results)
    try:
        exec_results = _load_execution_results()
        triggers.extend(detect_slippage_deterioration(exec_results, engine=engine, source=source))
    except Exception:
        pass

    # Shadow↔Reality divergence — RETIRED with V10_PRIMARY (Phase 1I-C);
    # detect_sr_divergence is kept as a graceful no-op for API stability.

    # Drawdown approaching
    triggers.extend(detect_drawdown_approaching(engine=engine, source=source))

    # ─── BLACK SURFACE DETECTORS (from research_events) ───────────────
    try:
        research_events = _load_research_events()
        if research_events:
            triggers.extend(detect_cooldown_anomaly(research_events, engine=engine, source=source))
            triggers.extend(detect_correlation_blocking(research_events, engine=engine, source=source))
            triggers.extend(detect_position_limit_blocking(research_events, engine=engine, source=source))
            triggers.extend(detect_recovery_anomaly(research_events, engine=engine, source=source))
            triggers.extend(detect_config_change(research_events, engine=engine, source=source))
    except Exception:
        pass  # BLACK surface detectors must never block cycle

    return triggers


def _load_execution_results() -> list[dict[str, Any]]:
    """Load execution_results from disk."""
    results: list[dict] = []
    exec_dir = Path("logs/execution_results")
    if not exec_dir.exists():
        return results
    for f in sorted(exec_dir.rglob("*.jsonl")):
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    results.append(json.loads(line))
        except Exception:
            continue
    return results


def _load_research_events() -> list[dict[str, Any]]:
    """Load research events (guard decisions, recovery, config) from disk."""
    events: list[dict] = []
    event_dir = Path("logs/research_events")
    if not event_dir.exists():
        return events
    for f in sorted(event_dir.rglob("*.jsonl")):
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    events.append(json.loads(line))
        except Exception:
            continue
    return events


# ═══════════════════════════════════════════════════════════════════════════════
# BLACK SURFACE DETECTORS (require research_events instrumentation)
# ═══════════════════════════════════════════════════════════════════════════════

def detect_cooldown_anomaly(
    events: list[dict[str, Any]],
    *,
    engine: FindingTriggerEngine,
    source: str = "",
    min_blocks: int = 10,
    excessive_block_rate: float = 0.50,
) -> list[FindingTrigger]:
    """
    Detect when trade cooldown is blocking an excessive proportion of opportunities.

    Reads GUARD_DECISION events from research_events.
    """
    cooldown_blocks = [e for e in events if e.get("event_type") == "GUARD_DECISION"
                       and e.get("guard_name") == "trade_cooldown"
                       and not e.get("allowed")]
    all_guard_events = [e for e in events if e.get("event_type") == "GUARD_DECISION"]

    triggers: list[FindingTrigger] = []
    if len(all_guard_events) < min_blocks * 2:
        return triggers

    block_rate = len(cooldown_blocks) / len(all_guard_events) if all_guard_events else 0
    if block_rate >= excessive_block_rate and len(cooldown_blocks) >= min_blocks:
        trigger = FindingTrigger(
            finding_id=f"cooldown_anom_{len(cooldown_blocks)}_{block_rate:.2f}",
            source=source,
            category=TriggerCategory.GUARD_VALUE_NEGATIVE,
            title=f"Cooldown blocking {block_rate:.0%} of opportunities ({len(cooldown_blocks)}/{len(all_guard_events)})",
            observation=(
                f"Trade cooldown blocked {len(cooldown_blocks)} of {len(all_guard_events)} "
                f"guard evaluations ({block_rate:.0%}). May indicate excessive cooldown duration."
            ),
            sample_size=len(all_guard_events),
            confidence="MEDIUM",
            suggested_patterns=[],
            trigger_reason=f"cooldown_block_rate={block_rate:.2f} >= {excessive_block_rate}",
            priority=2,
            suggested_experiment_type=ExperimentType.CONDITIONING_ANALYSIS,
            suggested_hypothesis_category=HypothesisCategory.GUARD_QUALITY,
            suggested_claim="Cooldown duration is excessive relative to opportunity flow",
            suggested_null="Cooldown blocking rate is appropriate for risk management",
        )
        result = engine._screen(trigger)
        if result:
            triggers.append(result)
    return triggers


def detect_correlation_blocking(
    events: list[dict[str, Any]],
    *,
    engine: FindingTriggerEngine,
    source: str = "",
    min_blocks: int = 5,
) -> list[FindingTrigger]:
    """
    Detect when correlation controls are frequently blocking trades.
    """
    corr_blocks = [e for e in events if e.get("event_type") == "GUARD_DECISION"
                   and e.get("guard_name") == "correlation_guard"
                   and not e.get("allowed")]

    triggers: list[FindingTrigger] = []
    if len(corr_blocks) < min_blocks:
        return triggers

    # Group by symbol to find which symbols are most affected
    from collections import Counter
    by_symbol = Counter(e.get("symbol", "") for e in corr_blocks)
    most_blocked = by_symbol.most_common(3)

    trigger = FindingTrigger(
        finding_id=f"corr_block_{len(corr_blocks)}",
        source=source,
        category=TriggerCategory.GUARD_VALUE_NEGATIVE,
        title=f"Correlation guard blocked {len(corr_blocks)} trades",
        observation=(
            f"Correlation controls blocked {len(corr_blocks)} opportunities. "
            f"Most affected: {', '.join(f'{sym}({n})' for sym, n in most_blocked)}."
        ),
        sample_size=len(corr_blocks),
        confidence="LOW",
        suggested_patterns=[],
        trigger_reason=f"correlation_blocks={len(corr_blocks)} >= {min_blocks}",
        priority=3,
        suggested_experiment_type=ExperimentType.CONDITIONING_ANALYSIS,
        suggested_hypothesis_category=HypothesisCategory.GUARD_QUALITY,
        suggested_claim="Correlation limits are unnecessarily restrictive",
        suggested_null="Correlation limits are appropriate for risk",
    )
    result = engine._screen(trigger)
    if result:
        triggers.append(result)
    return triggers


def detect_position_limit_blocking(
    events: list[dict[str, Any]],
    *,
    engine: FindingTriggerEngine,
    source: str = "",
    min_blocks: int = 5,
) -> list[FindingTrigger]:
    """
    Detect when position limits are frequently reached.
    """
    pos_blocks = [e for e in events if e.get("event_type") == "GUARD_DECISION"
                  and e.get("guard_name") == "portfolio_exposure"
                  and not e.get("allowed")]

    triggers: list[FindingTrigger] = []
    if len(pos_blocks) < min_blocks:
        return triggers

    trigger = FindingTrigger(
        finding_id=f"pos_limit_{len(pos_blocks)}",
        source=source,
        category=TriggerCategory.GUARD_VALUE_NEGATIVE,
        title=f"Position limit reached {len(pos_blocks)} times",
        observation=(
            f"Portfolio exposure guard blocked {len(pos_blocks)} opportunities. "
            f"Position limit may be unnecessarily restrictive."
        ),
        sample_size=len(pos_blocks),
        confidence="LOW",
        suggested_patterns=[],
        trigger_reason=f"position_limit_blocks={len(pos_blocks)} >= {min_blocks}",
        priority=3,
        suggested_experiment_type=ExperimentType.CONDITIONING_ANALYSIS,
        suggested_hypothesis_category=HypothesisCategory.GUARD_QUALITY,
        suggested_claim="Position limits prevent otherwise profitable diversification",
        suggested_null="Position limits are appropriate for capital preservation",
    )
    result = engine._screen(trigger)
    if result:
        triggers.append(result)
    return triggers


def detect_recovery_anomaly(
    events: list[dict[str, Any]],
    *,
    engine: FindingTriggerEngine,
    source: str = "",
) -> list[FindingTrigger]:
    """
    Detect abnormal recovery/restart behaviour.
    """
    recovery_events = [e for e in events if e.get("event_type") == "RECOVERY"]

    triggers: list[FindingTrigger] = []
    if not recovery_events:
        return triggers

    # Check for identity failures
    total_identity_failed = sum(e.get("identity_failed", 0) for e in recovery_events)
    total_protection_missing = sum(e.get("protection_missing", 0) for e in recovery_events)

    if total_identity_failed > 0:
        trigger = FindingTrigger(
            finding_id=f"recovery_id_fail_{total_identity_failed}",
            source=source,
            category=TriggerCategory.DRAWDOWN_APPROACHING,  # Using existing category
            title=f"Recovery failed to restore {total_identity_failed} position identities",
            observation=(
                f"Across {len(recovery_events)} recovery events, "
                f"{total_identity_failed} positions lost their identity (correlation_id). "
                f"{total_protection_missing} had missing SL/TP protection."
            ),
            sample_size=len(recovery_events),
            confidence="HIGH" if total_protection_missing > 0 else "MEDIUM",
            suggested_patterns=[],
            trigger_reason=f"identity_failures={total_identity_failed}",
            priority=1 if total_protection_missing > 0 else 2,
            suggested_experiment_type=ExperimentType.OOS_VALIDATION,
            suggested_hypothesis_category=HypothesisCategory.OTHER,
            suggested_claim="Recovery process has reliability issues requiring attention",
            suggested_null="Recovery is functioning within acceptable parameters",
        )
        result = engine._screen(trigger)
        if result:
            triggers.append(result)

    return triggers


def detect_config_change(
    events: list[dict[str, Any]],
    *,
    engine: FindingTriggerEngine,
    source: str = "",
) -> list[FindingTrigger]:
    """
    Detect configuration changes between research cycles.

    If config_hash has changed between observations, flags for investigation.
    """
    config_events = [e for e in events if e.get("event_type") == "CONFIG_SNAPSHOT"]
    if len(config_events) < 2:
        return triggers if 'triggers' in dir() else []

    # Check if hash has changed
    hashes = list(dict.fromkeys(e.get("config_hash", "") for e in config_events if e.get("config_hash")))
    triggers: list[FindingTrigger] = []

    if len(hashes) > 1:
        trigger = FindingTrigger(
            finding_id=f"config_change_{hashes[-1][:8]}",
            source=source,
            category=TriggerCategory.DRAWDOWN_APPROACHING,  # Using existing category for research_recommendation
            title=f"Configuration changed: {len(hashes)} versions detected",
            observation=(
                f"Config hash changed from {hashes[0][:8]}... to {hashes[-1][:8]}... "
                f"across {len(config_events)} snapshots. Performance may be affected."
            ),
            sample_size=len(config_events),
            confidence="LOW",
            suggested_patterns=[],
            trigger_reason=f"config_versions={len(hashes)}",
            priority=2,
            suggested_experiment_type=ExperimentType.POPULATION_COMPARISON,
            suggested_hypothesis_category=HypothesisCategory.OTHER,
            suggested_claim="Configuration change affected trading performance",
            suggested_null="Configuration change had no material effect",
        )
        result = engine._screen(trigger)
        if result:
            triggers.append(result)

    return triggers
