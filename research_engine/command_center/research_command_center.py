"""
Research Command Centre — Unified Research State Aggregation (Phase 2 + Traceability + Decision Gates).

Consumes ALL existing research outputs and produces a single 12-section
report answering: "What does the system know, what can it prove,
what is missing, what should happen next, where did it come from,
and can I safely change strategy logic?"

This module is PURELY REPORTING. It does NOT:
    - Modify trading logic, scoring, execution, or risk
    - Duplicate validation logic (uses existing validators)
    - Run experiments (only reads their outputs)

Usage:
    from research_engine.command_center import generate_command_report, print_report
    report = generate_command_report()
    print_report(report)
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_engine.command_center.command_models import (
    ArchitectureAuthority,
    ArchitectureStatus,
    Blocker,
    ConfirmedFindings,
    CoverageField,
    DataHealth,
    DatasetFingerprint,
    EVBreakdownEntry,
    PatternFinding,
    QuestionEntry,
    QuestionProvenance,
    Recommendation,
    RejectedHypothesis,
    ResearchCommandReport,
    ResearchReadiness,
    ResearchTraceability,
    SystemEdge,
    SystemState,
)
from research_engine.command_center.decision_gates import (
    DecisionGateReport,
    ResearchDecisionStatus,
    evaluate_decision_gates,
)
from research_engine.registry.research_question_registry import REGISTRY, REGISTRY_BY_ID
from research_engine.registry.research_question_models import QuestionCategory


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

_REPORTS_DIR = Path("analysis/reports")
_SUMMARIES_DIR = Path("analysis/summaries")
_SHADOW_DIR = Path("logs/shadow_trades")
_RESEARCH_SHADOW_DIR = Path("logs/research_shadow_trades")
_TRACE_DIR = Path("logs/decision_trace")
_TRUTH_DIR = Path("logs/trade_truth")

_COVERAGE_HIGH = 0.80
_COVERAGE_MED = 0.50
_MIN_SAMPLES_PROMOTION = 100
_MIN_SAMPLES_CONFIDENCE = 50


# ═══════════════════════════════════════════════════════════════════════════════
# DATA LOADING (read-only)
# ═══════════════════════════════════════════════════════════════════════════════


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _count_jsonl(directory: Path) -> int:
    count = 0
    if not directory.exists():
        return 0
    for f in directory.rglob("*.jsonl"):
        try:
            count += sum(1 for line in f.read_text(encoding="utf-8").splitlines() if line.strip())
        except OSError:
            pass
    return count


def _load_jsonl_sample(directory: Path, limit: int = 500) -> list[dict[str, Any]]:
    """Load a sample of JSONL records (most recent first, capped)."""
    records: list[dict[str, Any]] = []
    if not directory.exists():
        return records
    for f in sorted(directory.rglob("*.jsonl"), reverse=True):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    records.append(json.loads(line))
                except (json.JSONDecodeError, ValueError):
                    pass
                if len(records) >= limit:
                    return records
    return records


def _load_all_reports() -> dict[str, dict[str, Any]]:
    """Load all research report JSON files from analysis/reports/ and normalize to canonical contract."""
    from research_engine.experiments.report_contract import normalize_legacy_report

    reports: dict[str, dict[str, Any]] = {}
    if not _REPORTS_DIR.exists():
        return reports
    for f in _REPORTS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            qid = data.get("question_id", "")
            if not qid:
                continue
            # Detect legacy format (has "metrics" + "finding" but no "overall")
            if "overall" not in data and "metrics" in data:
                data = normalize_legacy_report(data)
            reports[qid] = data
        except (json.JSONDecodeError, OSError):
            pass
    return reports


def _deep_get(d: dict[str, Any], *keys: str) -> Any:
    """Safely traverse nested dict."""
    current = d
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
        if current is None:
            return None
    return current


# ═══════════════════════════════════════════════════════════════════════════════
# REPORT GENERATION
# ═══════════════════════════════════════════════════════════════════════════════


def generate_command_report() -> ResearchCommandReport:
    """
    Generate the complete 10-section Research Command Centre report.

    Reads ALL existing research outputs. Does NOT run experiments or
    modify system state.
    """
    # Load sources
    dashboard = _load_json(_SUMMARIES_DIR / "research_dashboard.json")
    knowledge = _load_json(_SUMMARIES_DIR / "research_knowledge.json")
    reports = _load_all_reports()

    shadow_records = _load_jsonl_sample(_SHADOW_DIR)
    research_shadow_records = _load_jsonl_sample(_RESEARCH_SHADOW_DIR)
    all_shadow = shadow_records + research_shadow_records
    shadow_count = _count_jsonl(_SHADOW_DIR) + _count_jsonl(_RESEARCH_SHADOW_DIR)
    trace_count = _count_jsonl(_TRACE_DIR)

    # Build each section
    data_health = _build_data_health(all_shadow, shadow_count)
    system_state = _build_system_state(data_health, trace_count, shadow_count)
    research_readiness = _build_research_readiness(dashboard)
    active_questions = _build_active_questions(dashboard)
    confirmed_findings = _build_confirmed_findings(knowledge, reports)
    rejected_hypotheses = _build_rejected_hypotheses(knowledge)
    system_edge = _build_system_edge(all_shadow, reports)
    architecture_status = _build_architecture_status(knowledge)
    blockers = _build_blockers(data_health, dashboard)
    recommendation = _build_recommendation(data_health, blockers)
    traceability = _build_traceability(dashboard, reports, shadow_count)

    # Section 12: Decision Gates
    decision_gates = evaluate_decision_gates(data_health, dashboard, reports)

    # Section 13: Research Lifecycle (reads from lifecycle registry/catalogue)
    lifecycle_section = _build_lifecycle_section()

    return ResearchCommandReport(
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        system_state=system_state,
        data_health=data_health,
        research_readiness=research_readiness,
        active_questions=active_questions,
        confirmed_findings=confirmed_findings,
        rejected_hypotheses=rejected_hypotheses,
        system_edge=system_edge,
        architecture_status=architecture_status,
        blockers=blockers,
        recommendation=recommendation,
        traceability=traceability,
        decision_gates=decision_gates,
        lifecycle_section=lifecycle_section,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION BUILDERS
# ═══════════════════════════════════════════════════════════════════════════════


def _build_system_state(data_health: DataHealth, trace_count: int, shadow_count: int) -> SystemState:
    infra = "READY" if (trace_count > 0 or shadow_count > 0) else "NOT_READY"

    if shadow_count == 0:
        data_coll = "BLOCKED"
    elif data_health.lineage_coverage.pct < _COVERAGE_HIGH:
        data_coll = "COLLECTING"
    elif shadow_count < _MIN_SAMPLES_CONFIDENCE:
        data_coll = "COLLECTING"
    else:
        data_coll = "READY"

    if shadow_count >= _MIN_SAMPLES_CONFIDENCE and data_health.lineage_coverage.pct >= _COVERAGE_MED:
        strat_eval = "READY"
    else:
        strat_eval = "WAITING_DATA"

    if (shadow_count >= _MIN_SAMPLES_PROMOTION
            and data_health.lineage_coverage.pct >= _COVERAGE_HIGH
            and data_health.contamination_count == 0):
        promotion = "READY"
    else:
        promotion = "INSUFFICIENT_DATA"

    return SystemState(
        infrastructure=infra,
        data_collection=data_coll,
        strategy_evaluation=strat_eval,
        promotion_decisions=promotion,
    )


def _build_data_health(records: list[dict[str, Any]], total_count: int) -> DataHealth:
    """Assess dataset health from shadow trade records."""
    if not records:
        return DataHealth(
            record_count=total_count,
            source="shadow_trades",
            outcome_coverage=CoverageField("outcome", 0.0, "MISSING"),
            lineage_coverage=CoverageField("entity_id", 0.0, "MISSING"),
            context_fields=[
                CoverageField("H4 regime", 0.0, "MISSING"),
                CoverageField("H1 bias", 0.0, "MISSING"),
                CoverageField("market phase", 0.0, "MISSING"),
            ],
            dataset_verdict="NO DATA",
        )

    n = len(records)
    outcome_count = 0
    lineage_count = 0
    regime_count = 0
    bias_count = 0
    phase_count = 0
    strategy_count = 0
    horizon_count = 0
    contaminated = 0

    valid_strategies = {"REVERSAL", "CONTINUATION", "FALSE_BREAK"}
    valid_horizons = {"SCALP", "INTRADAY", "EXTENDED"}
    combined_suffixes = {"_SCALP", "_INTRADAY", "_EXTENDED"}

    for r in records:
        # Outcome
        outcome = _deep_get(r, "simulated_outcome", "pnl_r_multiple")
        if outcome is not None:
            outcome_count += 1

        # Lineage
        eid = _deep_get(r, "identity", "entity_id") or r.get("entity_id", "")
        cid = _deep_get(r, "identity", "correlation_id") or r.get("correlation_id", "")
        if eid or (cid and not cid.startswith("HORIZON-")):
            lineage_count += 1

        # H4 regime
        regime = (
            _deep_get(r, "decision_snapshot", "h4_regime")
            or _deep_get(r, "decision_snapshot", "regime")
            or r.get("h4_regime", "") or r.get("regime", "")
        )
        if regime and regime not in ("UNKNOWN", "unknown", "", "TRANSITIONAL"):
            regime_count += 1

        # H1 bias
        bias = _deep_get(r, "decision_snapshot", "h1_bias") or r.get("h1_bias", "")
        if bias and bias not in ("NEUTRAL", "neutral", "UNKNOWN", "unknown", ""):
            bias_count += 1

        # Market phase
        phase = _deep_get(r, "decision_snapshot", "market_phase") or r.get("market_phase", "")
        if phase and phase not in ("UNKNOWN", "unknown", "", None):
            phase_count += 1

        # Strategy
        strategy = (
            _deep_get(r, "identity", "strategy_id")
            or _deep_get(r, "decision_snapshot", "strategy")
            or r.get("strategy", "")
        )
        if strategy:
            if any(suffix in strategy for suffix in combined_suffixes):
                contaminated += 1
            elif strategy in valid_strategies:
                strategy_count += 1

        # Horizon
        horizon = _deep_get(r, "decision_snapshot", "trade_horizon") or r.get("trade_horizon", "")
        if horizon in valid_horizons:
            horizon_count += 1

    # Build coverage fields
    outcome_cov = _make_cov("outcome", outcome_count, n)
    lineage_cov = _make_cov("entity_id", lineage_count, n)
    context = [
        _make_cov("H4 regime", regime_count, n),
        _make_cov("H1 bias", bias_count, n),
        _make_cov("market phase", phase_count, n),
        _make_cov("strategy", strategy_count, n),
        _make_cov("trade_horizon", horizon_count, n),
    ]

    # Verdict
    if lineage_cov.pct >= _COVERAGE_HIGH and outcome_cov.pct >= _COVERAGE_HIGH:
        if all(f.pct >= _COVERAGE_HIGH for f in context[:3]):
            verdict = "READY FOR FULL RESEARCH"
        else:
            verdict = "PARTIAL — missing context coverage"
    elif outcome_cov.pct >= _COVERAGE_MED:
        verdict = "NOT READY FOR FULL RESEARCH"
    else:
        verdict = "INSUFFICIENT DATA"

    return DataHealth(
        record_count=total_count,
        source="shadow_trades",
        outcome_coverage=outcome_cov,
        lineage_coverage=lineage_cov,
        context_fields=context,
        contamination_count=contaminated,
        dataset_verdict=verdict,
    )


def _build_research_readiness(dashboard: dict[str, Any] | None) -> ResearchReadiness:
    """Build readiness counts from the full V2 registry as source of truth."""
    # Registry is always the canonical count
    total = len(REGISTRY)

    # Determine which have been researched (have dashboard results or reports)
    researched_ids: set[str] = set()
    if dashboard:
        for qid, info in dashboard.get("questions", {}).items():
            if info.get("status") in ("COMPLETE",):
                # Map legacy Q-IDs to v2 IDs via legacy_ids
                for rq in REGISTRY:
                    if qid in rq.legacy_ids or qid == rq.id:
                        researched_ids.add(rq.id)

    complete = len(researched_ids)
    # "ready" = registered in v2 but not yet researched, no blocking rules
    # "blocked" = has validation rules that reference missing infrastructure
    ready = 0
    blocked = 0
    waiting = 0
    for rq in REGISTRY:
        if rq.id in researched_ids:
            continue
        if not rq.validation_rules:
            ready += 1
        else:
            # Questions with validation rules are "waiting data" until evaluated
            waiting += 1

    return ResearchReadiness(
        total_questions=total,
        complete=complete,
        ready=ready,
        waiting_data=waiting,
        blocked=blocked,
    )


def _build_active_questions(dashboard: dict[str, Any] | None) -> list[QuestionEntry]:
    """
    Build question list from V2 registry as source of truth.

    Merges registry metadata with dashboard results (if available).
    Adds distinction: REGISTERED / RESEARCHED / EXECUTABLE.
    """
    entries: list[QuestionEntry] = []

    # Build lookup from dashboard (legacy Q-IDs)
    dashboard_results: dict[str, dict[str, Any]] = {}
    if dashboard:
        dashboard_results = dashboard.get("questions", {})

    for rq in REGISTRY:
        # Check if this question has results (via legacy_ids or direct match)
        result_info = None
        for legacy_id in rq.legacy_ids:
            if legacy_id in dashboard_results:
                result_info = dashboard_results[legacy_id]
                break
        if rq.id in dashboard_results:
            result_info = dashboard_results[rq.id]

        # Determine status
        if result_info and result_info.get("status") == "COMPLETE":
            status = "RESEARCHED"
        elif not rq.validation_rules:
            status = "EXECUTABLE"
        else:
            status = "REGISTERED"

        recommendation = ""
        if result_info:
            recommendation = result_info.get("recommendation", "")

        entries.append(QuestionEntry(
            question_id=rq.id,
            title=rq.title,
            priority=rq.priority.value,
            status=status,
            recommendation=recommendation,
            blocker=", ".join(r.description for r in rq.validation_rules) if status == "REGISTERED" else "",
        ))

    # Sort by priority then category prefix then ID
    entries.sort(key=lambda e: (e.priority, e.question_id))
    return entries


def _build_confirmed_findings(
    knowledge: dict[str, Any] | None,
    reports: dict[str, dict[str, Any]],
) -> ConfirmedFindings:
    findings = ConfirmedFindings()

    # Pull from knowledge map (non-ARCH confirmed facts)
    if knowledge:
        for fact in knowledge.get("confirmed_facts", []):
            if not fact.startswith("ARCH:"):
                findings.findings.append(fact)

    # Extract pattern-level findings from Q5 report
    q5 = reports.get("Q5")
    if q5:
        pattern_perf = q5.get("metrics", {}).get("pattern_performance", {})
        for pattern, stats in pattern_perf.items():
            n = stats.get("n", 0)
            if n < 10:
                continue
            avg_r = stats.get("avg_r", 0)
            wr = stats.get("wr", 0)
            conf = "HIGH" if n >= 100 else "MEDIUM" if n >= 30 else "LOW"
            findings.pattern_findings.append(PatternFinding(
                name=pattern,
                ev=avg_r,
                sample_count=n,
                confidence=conf,
                win_rate=wr,
            ))
    # Sort pattern findings by EV descending
    findings.pattern_findings.sort(key=lambda p: p.ev if p.ev else 0, reverse=True)
    return findings


def _build_rejected_hypotheses(knowledge: dict[str, Any] | None) -> list[RejectedHypothesis]:
    if not knowledge:
        return []
    results: list[RejectedHypothesis] = []
    for item in knowledge.get("rejected_hypotheses", []):
        # Format: "REJECTED: hypothesis text (reason)"
        text = item.replace("REJECTED: ", "").strip()
        # Split on parenthetical reason if present
        if "(" in text and text.endswith(")"):
            parts = text.rsplit("(", 1)
            hypothesis = parts[0].strip()
            reason = parts[1].rstrip(")")
        else:
            hypothesis = text
            reason = ""
        results.append(RejectedHypothesis(hypothesis=hypothesis, reason=reason))
    return results


def _build_system_edge(
    shadow_records: list[dict[str, Any]],
    reports: dict[str, dict[str, Any]],
) -> SystemEdge:
    """Build system edge from Q19 report or compute from shadow trades."""
    edge = SystemEdge()

    # Try Q19 report first (authoritative)
    q19 = reports.get("Q19")
    if q19:
        data = q19.get("data", {})
        metrics = q19.get("metrics", {})
        edge.current_ev = data.get("expected_value") or metrics.get("expected_value")
        edge.eligible_trades = data.get("total_trades") or q19.get("dataset", {}).get("sample_size", 0)
        edge.confidence = data.get("confidence") or metrics.get("confidence", "")
        edge.edge_classification = data.get("edge_classification") or metrics.get("edge_classification", "")
        edge.win_rate = data.get("win_rate", 0)
        edge.profit_factor = data.get("profit_factor", 0)
        edge.ev_trend = data.get("ev_trend", "")
        edge.dataset_name = f"q19_ev_{q19.get('timestamp', '')[:10]}"

        # Pattern breakdown from Q19
        for p in data.get("pattern_breakdown", [])[:3]:
            edge.best_patterns.append(EVBreakdownEntry(
                name=p.get("pattern", ""),
                ev=p.get("expected_value", 0),
                trades=p.get("trades", 0),
                win_rate=p.get("win_rate", 0),
            ))
        return edge

    # Fallback: compute from shadow records directly
    if not shadow_records:
        edge.confidence = "INSUFFICIENT_DATA"
        edge.edge_classification = "NO_EDGE"
        return edge

    r_values: list[float] = []
    by_pattern: dict[str, list[float]] = defaultdict(list)

    for r in shadow_records:
        r_mult = _deep_get(r, "simulated_outcome", "pnl_r_multiple")
        if r_mult is None:
            continue
        r_values.append(float(r_mult))
        pattern = _deep_get(r, "decision_snapshot", "pattern") or "UNKNOWN"
        by_pattern[pattern].append(float(r_mult))

    if not r_values:
        edge.confidence = "INSUFFICIENT_DATA"
        return edge

    n = len(r_values)
    wins = sum(1 for r in r_values if r > 0)
    edge.current_ev = sum(r_values) / n
    edge.eligible_trades = n
    edge.win_rate = wins / n
    edge.dataset_name = "live_shadow_computation"

    if n >= 100:
        edge.confidence = "HIGH"
    elif n >= 30:
        edge.confidence = "MEDIUM"
    else:
        edge.confidence = "LOW"

    if edge.current_ev and edge.current_ev > 0.15:
        edge.edge_classification = "STRONG_EDGE"
    elif edge.current_ev and edge.current_ev > 0.05:
        edge.edge_classification = "MARGINAL_EDGE"
    elif edge.current_ev and edge.current_ev > 0:
        edge.edge_classification = "NO_EDGE"
    else:
        edge.edge_classification = "NEGATIVE_EDGE"

    # Pattern breakdowns
    pattern_evs = []
    for pattern, rs in by_pattern.items():
        if len(rs) >= 5:
            avg = sum(rs) / len(rs)
            wr = sum(1 for x in rs if x > 0) / len(rs)
            pattern_evs.append(EVBreakdownEntry(name=pattern, ev=avg, trades=len(rs), win_rate=wr))

    pattern_evs.sort(key=lambda p: p.ev, reverse=True)
    edge.best_patterns = pattern_evs[:3]
    edge.worst_patterns = sorted(pattern_evs, key=lambda p: p.ev)[:3]

    # Warnings
    if any("_SCALP" in ((_deep_get(r, "identity", "strategy_id") or "") ) for r in shadow_records[:100]):
        edge.warnings.append("Strategy contamination exists in historical data")

    return edge


def _build_architecture_status(knowledge: dict[str, Any] | None) -> ArchitectureStatus:
    """Build architecture status from ARCH: facts in knowledge map."""
    status = ArchitectureStatus()
    if not knowledge:
        return status

    # Parse ARCH facts
    arch_map = {
        "H4": "Regime classification",
        "H1": "Structural direction + BOS",
        "M15": "Setup quality",
        "M5": "Execution timing",
    }

    for fact in knowledge.get("confirmed_facts", []):
        if not fact.startswith("ARCH:"):
            continue
        text = fact.replace("ARCH: ", "").strip()

        # Try to match known timeframes
        matched = False
        for tf, resp in arch_map.items():
            if tf in text:
                status.authorities.append(ArchitectureAuthority(
                    timeframe=tf,
                    responsibility=resp,
                    confirmed=True,
                ))
                matched = True
                break
        if not matched:
            status.additional_facts.append(text)

    # Deduplicate authorities
    seen = set()
    unique = []
    for a in status.authorities:
        if a.timeframe not in seen:
            seen.add(a.timeframe)
            unique.append(a)
    status.authorities = unique

    return status


def _build_blockers(data_health: DataHealth, dashboard: dict[str, Any] | None) -> list[Blocker]:
    """Identify current blockers from data health and question status."""
    blockers: list[Blocker] = []

    # Data coverage blockers
    if data_health.lineage_coverage.pct < _COVERAGE_HIGH:
        blockers.append(Blocker(
            area="lineage",
            description=f"entity_id coverage at {data_health.lineage_coverage.pct*100:.1f}% (need {_COVERAGE_HIGH*100:.0f}%)",
            impact="Cannot perform full lifecycle joins",
        ))

    for field in data_health.context_fields:
        if field.pct < _COVERAGE_MED:
            blockers.append(Blocker(
                area=f"context:{field.name}",
                description=f"{field.name} at {field.pct*100:.1f}%",
                impact=f"Research requiring {field.name} is unreliable",
            ))

    if data_health.contamination_count > 0:
        blockers.append(Blocker(
            area="contamination",
            description=f"{data_health.contamination_count} records with combined strategy_horizon format",
            impact="Strategy-specific analysis contaminated",
        ))

    # Blocked questions
    if dashboard:
        for qid, info in dashboard.get("questions", {}).items():
            if info.get("status") == "BLOCKED":
                blockers.append(Blocker(
                    area=f"question:{qid}",
                    description=info.get("blocker", f"{qid} is blocked"),
                    impact=f"Cannot answer: {info.get('question', '')[:50]}",
                ))

    return blockers


def _build_recommendation(data_health: DataHealth, blockers: list[Blocker]) -> Recommendation:
    """Generate recommended next action from current state."""
    missing: list[str] = []

    if data_health.lineage_coverage.pct < _COVERAGE_HIGH:
        missing.append(f"entity_id accumulation ({data_health.lineage_coverage.pct*100:.0f}% -> {_COVERAGE_HIGH*100:.0f}%)")

    for field in data_health.context_fields:
        if field.pct < _COVERAGE_MED:
            missing.append(f"{field.name} in outcomes")

    if data_health.contamination_count > 0:
        missing.append("strategy separation (contamination cleanup)")

    # Determine phase and action
    if data_health.record_count == 0:
        phase = "INITIAL SETUP"
        reason = "No shadow trade data exists"
        action = "Start live bot with full lineage enabled."
    elif data_health.lineage_coverage.pct < _COVERAGE_HIGH:
        phase = "DATA COLLECTION"
        reason = f"Lineage incomplete: {data_health.lineage_coverage.pct*100:.1f}%"
        action = "Collect live shadow trades with entity_id populated."
    elif any(f.pct < _COVERAGE_MED for f in data_health.context_fields[:3]):
        phase = "DATA COLLECTION"
        worst = min(data_health.context_fields[:3], key=lambda f: f.pct)
        reason = f"Context field {worst.name} at {worst.pct*100:.1f}%"
        action = f"Ensure pipeline populates {worst.name} in shadow trade records."
    elif data_health.dataset_verdict == "READY FOR FULL RESEARCH":
        phase = "RESEARCH EXECUTION"
        reason = "All coverage targets met"
        action = "Run full research battery: python analysis/run_all_research.py"
    else:
        phase = "DATA COLLECTION"
        reason = "Coverage targets not fully met"
        action = "Continue collecting shadow trades."

    return Recommendation(
        current_phase=phase,
        reason=reason,
        missing_items=missing,
        required_action=action,
        do_not="Do NOT modify strategy logic yet." if phase != "RESEARCH EXECUTION" else "",
    )


def _build_traceability(
    dashboard: dict[str, Any] | None,
    reports: dict[str, dict[str, Any]],
    shadow_count: int,
) -> ResearchTraceability:
    """
    Build Section 11: Research Traceability.

    For every research question, trace the chain:
    Question -> Experiment -> Output File -> Finding -> Display

    Detects:
        - Questions marked COMPLETE but with no output file
        - Stale reports (> 7 days since last run)
        - Missing dataset fingerprints
    """
    trace = ResearchTraceability()

    now = datetime.now(timezone.utc)
    _STALE_DAYS = 7

    # Questions displayed in the command centre (sections 5, 7)
    displayed_qids = set()
    for qid, report_data in reports.items():
        displayed_qids.add(qid)
    # Also map legacy report keys to v2 IDs
    for rq in REGISTRY:
        for legacy_id in rq.legacy_ids:
            if legacy_id in reports:
                displayed_qids.add(rq.id)
        if rq.id in reports:
            displayed_qids.add(rq.id)
    displayed_qids.add("Q19")
    displayed_qids.add("E1")  # E1 = Q19 (system EV)
    displayed_qids.add("Q5")
    displayed_qids.add("E2")  # E2 = Q5 (pattern expectancy)

    # Build from V2 registry first (all 48 questions)
    # Then merge dashboard data for legacy questions
    dashboard_questions = dashboard.get("questions", {}) if dashboard else {}

    # Process all registry questions
    for rq in REGISTRY:
        qid = rq.id

        # Get dashboard info if available (via legacy_ids or direct match)
        info = dashboard_questions.get(qid, {})
        if not info:
            for legacy_id in rq.legacy_ids:
                if legacy_id in dashboard_questions:
                    info = dashboard_questions[legacy_id]
                    break

        status = info.get("status", "")
        runner = rq.runner_module or info.get("runner", "")
        last_run = info.get("last_run", "")
        report_file = rq.report_filename or info.get("report_file", "")
        recommendation = info.get("recommendation", "")

        # Determine experiment module
        experiment_module = runner if runner and runner != "not_implemented" else ""

        # Expected output location
        expected_output = report_file if report_file else ""
        if not expected_output and qid:
            # Guess from convention
            expected_output = f"analysis/reports/{qid.lower()}_*.json"

        # Check result availability — try direct ID and legacy IDs
        report_data = reports.get(qid)
        if not report_data:
            for legacy_id in rq.legacy_ids:
                if legacy_id in reports:
                    report_data = reports[legacy_id]
                    break
        result_available = report_data is not None

        # Dataset fingerprint from report
        fingerprint = None
        if report_data:
            dataset_info = report_data.get("dataset", {})
            sample_size = dataset_info.get("sample_size", 0)
            source = dataset_info.get("source", "")
            timestamp = report_data.get("timestamp", "")[:10]
            dataset_id = f"{source}_{timestamp}" if source and timestamp else ""

            # Determine validation score based on sample size
            if sample_size >= 100:
                val_score = "HIGH"
            elif sample_size >= 30:
                val_score = "MEDIUM"
            elif sample_size > 0:
                val_score = "LOW"
            else:
                val_score = "UNKNOWN"

            # Excluded = total shadow count minus records used (approximate)
            excluded = max(0, shadow_count - sample_size) if shadow_count > 0 else 0

            fingerprint = DatasetFingerprint(
                dataset_id=dataset_id,
                records_used=sample_size,
                records_excluded=excluded,
                validation_score=val_score,
            )

        # Detect missing output warning
        warning = ""
        if status == "COMPLETE" and not result_available:
            warning = f"Marked COMPLETE but no experiment output found. Action: Re-run experiment."
            trace.warnings.append(f"{qid}: {warning}")

        # Detect stale report
        is_stale = False
        if last_run:
            try:
                run_dt = datetime.fromisoformat(last_run.replace("Z", "+00:00"))
                age_days = (now - run_dt).days
                if age_days > _STALE_DAYS:
                    is_stale = True
                    if not warning:
                        warning = f"Report is {age_days} days old. Consider re-running."
            except (ValueError, TypeError):
                pass

        prov = QuestionProvenance(
            question_id=qid,
            question_title=rq.title,
            experiment_module=experiment_module,
            expected_output_location=expected_output,
            last_run_timestamp=last_run,
            status=status if result_available or status != "COMPLETE" else "MISSING_OUTPUT",
            result_available=result_available,
            displayed_in_command_center=qid in displayed_qids,
            dataset_fingerprint=fingerprint,
            warning=warning,
        )
        trace.questions.append(prov)

        # Update counters
        if status == "COMPLETE":
            trace.total_complete += 1
        if result_available:
            trace.total_with_output += 1
        if status == "COMPLETE" and not result_available:
            trace.total_missing_output += 1
        if is_stale:
            trace.total_stale += 1

    # Sort by question ID
    trace.questions.sort(key=lambda q: q.question_id)
    return trace


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 13: RESEARCH LIFECYCLE
# ═══════════════════════════════════════════════════════════════════════════════


def _build_lifecycle_section():
    """
    Build the Research Lifecycle section by reading from InvestigationRegistry
    and ExperimentCatalogue. Read-only — never modifies lifecycle state.
    """
    from research_engine.command_center.command_models import LifecycleSection, LifecycleHypothesisSummary

    try:
        from research_engine.lifecycle.registry import InvestigationRegistry
        from research_engine.lifecycle.experiment_catalogue import ExperimentCatalogue
        from research_engine.lifecycle.hypothesis import HypothesisStatus, ConclusionType

        registry = InvestigationRegistry()
        catalogue = ExperimentCatalogue()

        hypotheses = registry.all()
        experiments = catalogue.all()

        if not hypotheses and not experiments:
            return LifecycleSection(available=False, unavailable_reason="No lifecycle data yet")

        # Hypothesis counts
        h_by_status = {}
        awaiting = 0
        concluded_validated = 0
        concluded_rejected = 0
        concluded_inconclusive = 0

        for h in hypotheses:
            h_by_status[h.status.value] = h_by_status.get(h.status.value, 0) + 1
            if (h.status == HypothesisStatus.CONCLUDED and
                    h.conclusion_type == ConclusionType.VALIDATED and
                    not h.human_approval_granted):
                awaiting += 1
            if h.conclusion_type == ConclusionType.VALIDATED:
                concluded_validated += 1
            elif h.conclusion_type == ConclusionType.REJECTED:
                concluded_rejected += 1
            elif h.conclusion_type == ConclusionType.INCONCLUSIVE:
                concluded_inconclusive += 1

        # Experiment counts
        cat_summary = catalogue.get_summary()

        # Recent hypotheses (last 5)
        recent = sorted(hypotheses, key=lambda h: h.detected_timestamp or "", reverse=True)[:5]
        recent_summaries = []
        for h in recent:
            recent_summaries.append(LifecycleHypothesisSummary(
                hypothesis_id=h.hypothesis_id,
                title=h.title[:60],
                status=h.status.value,
                conclusion=h.conclusion_type.value if h.conclusion_type else "",
                confidence=h.conclusion_confidence,
                classification="",
                experiments_count=len(h.experiments),
                created_at=h.detected_timestamp[:19] if h.detected_timestamp else "",
            ))

        # Research Triggers (read-only)
        _trigger_data = _load_trigger_summary()

        return LifecycleSection(
            available=True,
            total_hypotheses=len(hypotheses),
            hypotheses_by_status=h_by_status,
            hypotheses_testing=h_by_status.get("TESTING", 0),
            hypotheses_challenged=h_by_status.get("CHALLENGED", 0),
            hypotheses_concluded=h_by_status.get("CONCLUDED", 0) + h_by_status.get("PROMOTED", 0),
            hypotheses_awaiting_approval=awaiting,
            total_experiments=cat_summary.get("total_experiments", 0),
            experiments_by_status=cat_summary.get("by_status", {}),
            experiments_by_type=cat_summary.get("by_type", {}),
            experiments_running=cat_summary.get("by_status", {}).get("RUNNING", 0),
            experiments_completed=cat_summary.get("by_status", {}).get("COMPLETED", 0),
            experiments_failed=cat_summary.get("by_status", {}).get("FAILED", 0),
            conclusions_validated=concluded_validated,
            conclusions_rejected=concluded_rejected,
            conclusions_inconclusive=concluded_inconclusive,
            human_decisions_needed=awaiting,
            recent_hypotheses=recent_summaries,
            total_triggers=_trigger_data.get("total", 0),
            triggers_eligible=_trigger_data.get("eligible", 0),
            triggers_investigating=_trigger_data.get("investigating", 0),
            triggers_completed=_trigger_data.get("completed", 0),
            triggers_dismissed=_trigger_data.get("dismissed", 0),
            triggers_blocked=_trigger_data.get("blocked", 0),
            trigger_candidates=_trigger_data.get("candidates", []),
        )

    except Exception as e:
        return LifecycleSection(available=False, unavailable_reason=f"Error loading lifecycle: {str(e)[:100]}")


def _load_trigger_summary() -> dict[str, Any]:
    """Load trigger summary from FindingTriggerEngine. Read-only."""
    try:
        from research_engine.lifecycle.finding_trigger import FindingTriggerEngine
        engine = FindingTriggerEngine()
        summary = engine.get_summary()
        by_status = summary.get("by_status", {})
        return {
            "total": summary.get("total_triggers", 0),
            "eligible": by_status.get("ELIGIBLE", 0),
            "investigating": by_status.get("INVESTIGATING", 0),
            "completed": by_status.get("COMPLETED", 0),
            "dismissed": by_status.get("DISMISSED", 0),
            "blocked": by_status.get("BLOCKED", 0),
            "candidates": summary.get("top_candidates", []),
        }
    except Exception:
        return {"total": 0, "eligible": 0, "investigating": 0, "completed": 0,
                "dismissed": 0, "blocked": 0, "candidates": []}


def _make_cov(name: str, count: int, total: int) -> CoverageField:
    pct = count / total if total > 0 else 0.0
    if pct >= _COVERAGE_HIGH:
        status = "OK"
    elif pct > 0:
        status = "LOW"
    else:
        status = "MISSING"
    return CoverageField(name=name, pct=pct, status=status)


# ═══════════════════════════════════════════════════════════════════════════════
# CLI RENDERER
# ═══════════════════════════════════════════════════════════════════════════════


def print_report(report: ResearchCommandReport) -> None:
    """Print human-readable 12-section command centre output."""
    w = print
    sep = "=" * 60

    w("")
    w(sep)
    w("  RESEARCH COMMAND CENTRE")
    w(sep)

    # ─── 1. SYSTEM STATE ──────────────────────────────────────────────
    w("")
    w("  1. SYSTEM STATE")
    w("  " + "-" * 56)
    ss = report.system_state
    w(f"  Infrastructure:        {ss.infrastructure}")
    w(f"  Data Collection:       {ss.data_collection}")
    w(f"  Strategy Evaluation:   {ss.strategy_evaluation}")
    w(f"  Promotion Decisions:   {ss.promotion_decisions}")

    # ─── 2. DATA HEALTH ───────────────────────────────────────────────
    w("")
    w(sep)
    w("  2. DATA HEALTH")
    w("  " + "-" * 56)
    dh = report.data_health
    w(f"  Shadow Trades:")
    w(f"    Records: {dh.record_count}")
    w("")
    w(f"  Outcome:")
    _print_cov_line(dh.outcome_coverage)
    w("")
    w(f"  Lineage:")
    _print_cov_line(dh.lineage_coverage)
    w("")
    w(f"  Context:")
    for f in dh.context_fields:
        _print_cov_line(f)
    if dh.contamination_count > 0:
        w(f"")
        w(f"  WARNING: {dh.contamination_count} contaminated records")
    w("")
    w(f"  Dataset Status:")
    w(f"    {dh.dataset_verdict}")

    # ─── 3. RESEARCH READINESS ────────────────────────────────────────
    w("")
    w(sep)
    w("  3. RESEARCH READINESS")
    w("  " + "-" * 56)
    rr = report.research_readiness
    w(f"  Total questions:  {rr.total_questions}")
    w(f"  Complete:         {rr.complete}")
    w(f"  Ready:            {rr.ready}")
    w(f"  Waiting data:     {rr.waiting_data}")
    w(f"  Blocked:          {rr.blocked}")


    # ─── 4. ACTIVE QUESTIONS ──────────────────────────────────────────
    w("")
    w(sep)
    w("  4. ACTIVE QUESTIONS")
    w("  " + "-" * 56)

    # Three-tier distinction
    researched_qs = [q for q in report.active_questions if q.status == "RESEARCHED"]
    executable_qs = [q for q in report.active_questions if q.status == "EXECUTABLE"]
    registered_qs = [q for q in report.active_questions if q.status == "REGISTERED"]

    w(f"  Total (registry): {len(report.active_questions)}")
    w(f"  Researched:       {len(researched_qs)}")
    w(f"  Executable:       {len(executable_qs)}")
    w(f"  Registered:       {len(registered_qs)}")

    if researched_qs:
        w("")
        w(f"  RESEARCHED ({len(researched_qs)}):")
        for q in researched_qs[:12]:
            rec = f" [{q.recommendation}]" if q.recommendation else ""
            w(f"    {q.question_id:4s} [{q.priority}] {q.title[:38]}{rec}")
        if len(researched_qs) > 12:
            w(f"    ... and {len(researched_qs) - 12} more")

    if executable_qs:
        w("")
        w(f"  EXECUTABLE ({len(executable_qs)}) — can run now:")
        for q in executable_qs:
            w(f"    {q.question_id:4s} [{q.priority}] {q.title[:45]}")

    if registered_qs:
        w("")
        w(f"  REGISTERED ({len(registered_qs)}) — waiting data:")
        for q in registered_qs[:10]:
            w(f"    {q.question_id:4s} [{q.priority}] {q.title[:45]}")
        if len(registered_qs) > 10:
            w(f"    ... and {len(registered_qs) - 10} more")

    # ─── 5. CONFIRMED FINDINGS ────────────────────────────────────────
    w("")
    w(sep)
    w("  5. CONFIRMED FINDINGS")
    w("  " + "-" * 56)
    cf = report.confirmed_findings

    if cf.pattern_findings:
        w("")
        w("  Pattern Performance:")
        for pf in cf.pattern_findings[:6]:
            icon = "+" if (pf.ev and pf.ev > 0) else "-"
            ev_str = f"{pf.ev:+.3f}R" if pf.ev is not None else "?"
            wr_str = f"WR {pf.win_rate:.0%}" if pf.win_rate else ""
            w(f"    {pf.name:25s}  EV {ev_str:>8s}  N={pf.sample_count:<4d}  {wr_str}  [{pf.confidence}]")

    if cf.findings:
        w("")
        w("  Key Findings:")
        for fact in cf.findings[:8]:
            w(f"    - {fact}")

    if not cf.pattern_findings and not cf.findings:
        w("  No confirmed findings yet.")

    # ─── 6. REJECTED HYPOTHESES ───────────────────────────────────────
    w("")
    w(sep)
    w("  6. REJECTED HYPOTHESES")
    w("  " + "-" * 56)
    if report.rejected_hypotheses:
        for rh in report.rejected_hypotheses:
            w(f"  REJECTED: {rh.hypothesis}")
            if rh.reason:
                w(f"    Reason: {rh.reason}")
    else:
        w("  No rejected hypotheses recorded.")

    # ─── 7. SYSTEM EDGE / EV ──────────────────────────────────────────
    w("")
    w(sep)
    w("  7. SYSTEM EDGE STATUS")
    w("  " + "-" * 56)
    se = report.system_edge
    if se.current_ev is not None:
        w(f"  Current EV:       {se.current_ev:+.4f}R")
        w(f"  Dataset:          {se.dataset_name}")
        w(f"  Eligible trades:  {se.eligible_trades}")
        w(f"  Confidence:       {se.confidence}")
        w(f"  Classification:   {se.edge_classification}")
        w(f"  Win Rate:         {se.win_rate:.1%}")
        if se.profit_factor:
            w(f"  Profit Factor:    {se.profit_factor:.2f}")
        if se.ev_trend:
            w(f"  Trend:            {se.ev_trend}")
        if se.best_patterns:
            w("")
            w("  Best Patterns:")
            for p in se.best_patterns:
                w(f"    {p.name:25s}  {p.ev:+.3f}R  (n={p.trades})")
        if se.worst_patterns:
            w("")
            w("  Worst Patterns:")
            for p in se.worst_patterns:
                w(f"    {p.name:25s}  {p.ev:+.3f}R  (n={p.trades})")
        if se.warnings:
            w("")
            w("  Warnings:")
            for warning in se.warnings:
                w(f"    - {warning}")
    else:
        w("  No EV data available. Run Q19 experiment.")


    # ─── 8. ARCHITECTURE STATUS ───────────────────────────────────────
    w("")
    w(sep)
    w("  8. ARCHITECTURE STATUS")
    w("  " + "-" * 56)
    arch = report.architecture_status
    if arch.authorities:
        w("")
        w("  Authority Ownership:")
        for a in arch.authorities:
            icon = "Y" if a.confirmed else " "
            w(f"    {a.timeframe:5s}  {a.responsibility:40s}  [{icon}]")
    if arch.additional_facts:
        w("")
        w("  Additional:")
        for fact in arch.additional_facts:
            w(f"    - {fact}")
    if not arch.authorities and not arch.additional_facts:
        w("  No architecture data available.")

    # ─── 9. BLOCKERS ──────────────────────────────────────────────────
    w("")
    w(sep)
    w("  9. BLOCKERS")
    w("  " + "-" * 56)
    if report.blockers:
        for b in report.blockers:
            w(f"  [{b.area}]")
            w(f"    {b.description}")
            if b.impact:
                w(f"    Impact: {b.impact}")
    else:
        w("  No blockers. System is clear to proceed.")

    # ─── 10. RECOMMENDED NEXT ACTION ─────────────────────────────────
    w("")
    w(sep)
    w("  10. RECOMMENDED NEXT ACTION")
    w("  " + "-" * 56)
    rec = report.recommendation
    w(f"  Current Phase: {rec.current_phase}")
    w(f"  Reason: {rec.reason}")
    if rec.missing_items:
        w("")
        w("  Missing:")
        for item in rec.missing_items:
            w(f"    - {item}")
    w("")
    w(f"  Required action:")
    w(f"    {rec.required_action}")
    if rec.do_not:
        w("")
        w(f"  {rec.do_not}")

    # ─── 11. RESEARCH TRACEABILITY ────────────────────────────────────
    w("")
    w(sep)
    w("  11. RESEARCH TRACEABILITY")
    w("  " + "-" * 56)
    tr = report.traceability
    w(f"  Complete:        {tr.total_complete}")
    w(f"  With output:     {tr.total_with_output}")
    w(f"  Missing output:  {tr.total_missing_output}")
    w(f"  Stale (>7d):     {tr.total_stale}")

    if tr.warnings:
        w("")
        w("  WARNINGS:")
        for warning in tr.warnings:
            w(f"    ! {warning}")

    # Show provenance for questions with available results (max 10)
    available = [q for q in tr.questions if q.result_available]
    missing_out = [q for q in tr.questions if q.status == "MISSING_OUTPUT"]

    if missing_out:
        w("")
        w("  MISSING OUTPUTS:")
        for q in missing_out:
            w(f"    {q.question_id} {q.question_title[:40]}")
            w(f"      Status: COMPLETE but no output file")
            w(f"      Expected: {q.expected_output_location}")
            w(f"      Action: Re-run experiment")

    if available:
        w("")
        w("  PROVENANCE CHAIN:")
        for q in available[:10]:
            displayed_str = "YES" if q.displayed_in_command_center else "NO"
            w(f"    {q.question_id} {q.question_title[:40]}")
            if q.experiment_module:
                w(f"      Experiment: {q.experiment_module}")
            w(f"      Output:     {q.expected_output_location}")
            w(f"      Last run:   {q.last_run_timestamp}")
            w(f"      Status:     {q.status}")
            w(f"      Displayed:  {displayed_str}")
            if q.dataset_fingerprint:
                fp = q.dataset_fingerprint
                w(f"      Dataset:    {fp.dataset_id} (n={fp.records_used}, excl={fp.records_excluded}, valid={fp.validation_score})")
            if q.warning:
                w(f"      WARNING:    {q.warning}")
        if len(available) > 10:
            w(f"    ... and {len(available) - 10} more")

    # ─── 12. RESEARCH DECISION GATES ─────────────────────────────────
    w("")
    w(sep)
    w("  12. RESEARCH DECISION GATES")
    w("  " + "-" * 56)

    if report.decision_gates:
        dg = report.decision_gates
        # Show key decisions (those with interesting status)
        key_decisions = [
            d for d in dg.decisions
            if d.current_status.value in ("PROMOTE", "MODIFY", "NEEDS_DATA", "BLOCKED")
            or d.question_id in ("Q19", "Q20", "Q24", "Q5")
        ]
        # Deduplicate and limit
        seen_ids: set[str] = set()
        shown: list = []
        for d in key_decisions:
            if d.question_id not in seen_ids:
                seen_ids.add(d.question_id)
                shown.append(d)
        shown = shown[:8]

        for d in shown:
            can_str = "YES" if d.can_change_strategy_logic else "NO"
            w("")
            w(f"  {d.question_id} {d.title[:45]}")
            w(f"    Historical:    {d.historical_result[:60]}")
            w(f"    Current:       {d.current_status.value}")
            w(f"    Can change:    {can_str}")
            if d.blocking_requirements:
                unmet = [r for r in d.blocking_requirements if not r.met]
                if unmet:
                    w(f"    Requirements:")
                    for r in unmet[:4]:
                        w(f"      {r.display}")
            w(f"    Action:        {d.recommended_action[:60]}")

        # Promotion Readiness Summary
        w("")
        w(sep)
        w("  PROMOTION READINESS")
        w("  " + "-" * 56)
        ps = dg.promotion_summary
        allowed_str = "YES" if ps.strategy_changes_allowed else "NO"
        w(f"  Strategy logic changes allowed: {allowed_str}")
        w(f"  Reason: {ps.reason}")

        if ps.required_before_changes:
            w("")
            w("  Required before changes:")
            for item in ps.required_before_changes:
                w(f"    - {item}")

        if ps.safe_actions:
            w("")
            w("  Safe actions now:")
            for action in ps.safe_actions:
                w(f"    [Y] {action}")

        if ps.unsafe_actions:
            w("")
            w("  Unsafe actions now:")
            for action in ps.unsafe_actions:
                w(f"    [X] {action}")
    else:
        w("  No decision gate data available.")

    w("")

    # ─── 13. RESEARCH LIFECYCLE ───────────────────────────────────────
    w(sep)
    w("  13. RESEARCH LIFECYCLE")
    w("  " + "-" * 56)
    lc = getattr(report, 'lifecycle_section', None)
    if lc and getattr(lc, 'available', False):
        w(f"  Hypotheses:             {lc.total_hypotheses}")
        for status, count in sorted(lc.hypotheses_by_status.items()):
            w(f"    {status:<20s} {count}")
        w(f"  Experiments:            {lc.total_experiments}")
        if lc.experiments_running:
            w(f"    Running:             {lc.experiments_running}")
        w(f"    Completed:           {lc.experiments_completed}")
        if lc.experiments_failed:
            w(f"    Failed:              {lc.experiments_failed}")
        w(f"  Conclusions:")
        w(f"    VALIDATED:           {lc.conclusions_validated}")
        w(f"    REJECTED:            {lc.conclusions_rejected}")
        w(f"    INCONCLUSIVE:        {lc.conclusions_inconclusive}")
        if lc.human_decisions_needed:
            w(f"  Human decisions needed: {lc.human_decisions_needed}")
        if lc.recent_hypotheses:
            w("")
            w("  Recent investigations:")
            for h in lc.recent_hypotheses:
                conclusion = h.conclusion or "..."
                w(f"    {h.hypothesis_id} | {h.title[:40]} | {h.status} -> {conclusion}")
        # Research Triggers
        if lc.total_triggers > 0:
            w("")
            w("  Research Triggers:")
            w(f"    Detected:          {lc.total_triggers}")
            w(f"    Eligible:          {lc.triggers_eligible}")
            if lc.triggers_investigating:
                w(f"    Investigating:     {lc.triggers_investigating}")
            w(f"    Completed:         {lc.triggers_completed}")
            if lc.triggers_dismissed:
                w(f"    Dismissed:         {lc.triggers_dismissed}")
            if lc.triggers_blocked:
                w(f"    Blocked:           {lc.triggers_blocked}")
            if lc.trigger_candidates:
                w("")
                w("  Top investigation candidates:")
                for c in lc.trigger_candidates[:3]:
                    w(f"    {c.get('trigger_id','')} | {c.get('title','')[:40]} | "
                      f"N={c.get('sample_size',0)} | {c.get('experiment_type','')}")
    elif lc:
        w(f"  {lc.unavailable_reason}")
    else:
        w("  No lifecycle data available.")

    w("")
    w(sep)


def _print_cov_line(f: CoverageField) -> None:
    """Print a coverage field with status icon."""
    pct = f.pct * 100
    if f.status == "OK":
        icon = "Y"
    elif f.status == "LOW":
        icon = "!"
    else:
        icon = "X"
    print(f"    {f.name:18s} {pct:5.1f}%  [{icon}]")
