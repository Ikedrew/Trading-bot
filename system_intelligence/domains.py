"""
Domain Knowledge Model — Maps the 13 architecture domains to evidence sources.

Each domain knows:
    - what it owns
    - where evidence lives
    - what questions it answers
    - which files are authoritative
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Domain:
    """One architecture domain with its knowledge sources."""
    name: str
    description: str
    owns: list[str]
    evidence_sources: list[str]
    answers: list[str]
    authority_files: list[str]
    keywords: list[str]


# ═══════════════════════════════════════════════════════════════════════════════
# THE 13 ARCHITECTURE DOMAINS (+ 2 discovered in blueprint validation)
# ═══════════════════════════════════════════════════════════════════════════════

DOMAINS: dict[str, Domain] = {
    "configuration": Domain(
        name="Configuration",
        description="All runtime flags, limits, feature toggles, and behaviour switches.",
        owns=["feature flags", "risk limits", "execution toggles", "guard enables", "horizon permissions"],
        evidence_sources=["core/config.py"],
        answers=["what config is active", "what limits apply", "what is enabled", "what is disabled", "what horizons are permitted"],
        authority_files=["core/config.py"],
        keywords=["config", "setting", "flag", "enabled", "disabled", "limit", "threshold", "toggle", "parameter"],
    ),
    "runtime": Domain(
        name="Runtime",
        description="Process lifecycle, health monitoring, cycle orchestration.",
        owns=["main loop", "cycle guards", "heartbeat", "startup", "shutdown", "recovery"],
        evidence_sources=["runtime/heartbeat.json", "core/runtime/live_scanner.py", "core/runtime/health_monitor.py"],
        answers=["is the bot running", "when did it start", "is it healthy", "what cycle is it on", "why did it stop"],
        authority_files=["architecture/02_authority/TOP_LEVEL_SYSTEM_AUTHORITY_AUDIT.md"],
        keywords=["running", "status", "heartbeat", "cycle", "restart", "shutdown", "alive", "health", "uptime"],
    ),
    "decision": Domain(
        name="Decision",
        description="Pattern detection, scoring, strategy selection, execution policy.",
        owns=["trade decisions", "scoring", "strategy classification", "EV gate", "policy rejection"],
        evidence_sources=["logs/decision_ledger/", "logs/decision_trace/", "logs/decision_audit/"],
        answers=["why was trade taken", "why was trade rejected", "what score", "what strategy", "what blocked", "which stage failed"],
        authority_files=["architecture/04_execution/HORIZON_EXECUTION_POLICY.md"],
        keywords=["decision", "score", "strategy", "pattern", "reject", "approve", "execute", "no_trade", "why", "blocked"],
    ),
    "risk": Domain(
        name="Risk",
        description="Guards, veto authority, exposure limits, position sizing.",
        owns=["guard chain", "daily limit", "cooldown", "correlation", "exposure", "regime guard", "spread guard"],
        evidence_sources=["logs/decision_ledger/ (RISK_BLOCK records)", "risk/runtime_guard_chain.py"],
        answers=["which guard blocked", "why was trade blocked", "what risk limit hit", "exposure level"],
        authority_files=["architecture/02_authority/TOP_LEVEL_SYSTEM_AUTHORITY_AUDIT.md"],
        keywords=["guard", "risk", "block", "veto", "limit", "exposure", "correlation", "cooldown", "spread"],
    ),
    "execution": Domain(
        name="Execution",
        description="Broker order submission, fill results, execution quality.",
        owns=["order submission", "broker response", "fill price", "slippage", "protection verification"],
        evidence_sources=["logs/execution_results/", "logs/execution_context/", "logs/protection_audit/"],
        answers=["did order fill", "what was fill price", "why did execution fail", "broker retcode", "was protection verified"],
        authority_files=["execution/execution_orchestrator.py"],
        keywords=["execution", "fill", "broker", "order", "retcode", "slippage", "mt5", "fail"],
    ),
    "trade_management": Domain(
        name="Trade Management",
        description="Position lifecycle: break-even, trailing, partial TP, time exits.",
        owns=["position management", "stop loss moves", "break-even", "trailing", "time exit", "partial close"],
        evidence_sources=["logs/trade_journal/", "core/trade_management/manager.py"],
        answers=["how long was trade held", "did break-even trigger", "what was exit reason", "how was position managed"],
        authority_files=["core/trade_management/manager.py"],
        keywords=["position", "trailing", "break-even", "exit", "hold", "duration", "management", "close"],
    ),
    "market_intelligence": Domain(
        name="Market Intelligence",
        description="Regime classification, multi-timeframe context, horizon classification.",
        owns=["H4 regime", "H1 structure", "M15 quality", "market context", "horizon classification"],
        evidence_sources=["logs/market_context/", "logs/decision_trace/ (regime fields)"],
        answers=["what regime is active", "what is market state", "what horizon was classified", "htf alignment"],
        authority_files=["architecture/06_market_intelligence/CURRENT_MARKET_CONTEXT_ARCHITECTURE.md"],
        keywords=["regime", "market", "context", "timeframe", "h4", "h1", "m15", "structure", "horizon", "trending", "ranging"],
    ),
    "persistence": Domain(
        name="Persistence",
        description="24 datasets, local + S3, schema versioning, data health.",
        owns=["dataset storage", "S3 mirrors", "schema versions", "data integrity"],
        evidence_sources=["logs/ (all subdirectories)", "events/"],
        answers=["is data being persisted", "are datasets healthy", "what schema version", "when was last write"],
        authority_files=["architecture/07_persistence/PERSISTENCE_ARCHITECTURE_AUDIT_FINAL.md"],
        keywords=["data", "persist", "s3", "dataset", "schema", "storage", "health", "fresh", "stale", "missing"],
    ),
    "research": Domain(
        name="Research",
        description="Research engine, experiments, shadow evaluation, horizon research.",
        owns=["research experiments", "shadow trades", "horizon observations", "activation readiness"],
        evidence_sources=["logs/shadow_trades/", "research_reports/", "logs/research_shadow_trades/"],
        answers=["what does research show", "is intraday ready", "shadow performance", "what experiments ran"],
        authority_files=["architecture/09_research/RESEARCH_ENGINE_ARCHITECTURE.md"],
        keywords=["research", "shadow", "experiment", "intraday", "extended", "activation", "readiness", "hypothesis"],
    ),
    "learning": Domain(
        name="Learning",
        description="Edge attribution, edge optimisation, strategy compilation.",
        owns=["causal attribution", "edge discovery", "strategy generation", "feature weights"],
        evidence_sources=["logs/edge_attribution/", "logs/edge_optimisation/", "logs/strategy_compiler/", "logs/learning/"],
        answers=["which edges are strong", "which features matter", "is strategy degrading", "what did learning find"],
        authority_files=["architecture/09_research/CANDIDATE_PROMOTION_ASSESSMENT.md"],
        keywords=["edge", "attribution", "learning", "strategy", "feature", "weight", "degrading", "improving"],
    ),
    "observability": Domain(
        name="Observability",
        description="Events, monitoring, system diagnostics, production readiness.",
        owns=["event stream", "candle events", "feature updates", "system health events"],
        evidence_sources=["events/", "runtime/heartbeat.json"],
        answers=["what events were emitted", "is observation working", "production readiness score"],
        authority_files=["architecture/08_observability/PRODUCTION_INTELLIGENCE_READINESS_REVIEW.md"],
        keywords=["event", "candle", "observe", "monitor", "diagnostic", "readiness", "production"],
    ),
    "portfolio": Domain(
        name="Portfolio",
        description="Rankings, allocation, horizon authority, multi-symbol selection.",
        owns=["portfolio rankings", "shadow comparison", "allocation authority", "symbol selection"],
        evidence_sources=["logs/portfolio_rankings/", "logs/portfolio_shadow/"],
        answers=["what ranking was applied", "would authority have disagreed", "allocation state"],
        authority_files=["architecture/04_execution/HORIZON_EXECUTION_POLICY.md"],
        keywords=["portfolio", "ranking", "allocation", "symbol", "selection", "authority"],
    ),
    "infrastructure": Domain(
        name="Infrastructure",
        description="MT5 connection, process lifecycle, deployment, external integrations.",
        owns=["MT5 connection", "reconnection", "signal handlers", "instance lock", "Discord"],
        evidence_sources=["runtime/heartbeat.json", "main.py"],
        answers=["is mt5 connected", "when did process start", "any connection issues"],
        authority_files=["main.py"],
        keywords=["mt5", "connection", "deploy", "process", "pid", "discord", "aws", "s3"],
    ),
    "patterns": Domain(
        name="Patterns",
        description="Signal detection, candlestick patterns, pattern quality.",
        owns=["pattern detection", "signal generation", "candle analysis"],
        evidence_sources=["logs/decision_trace/ (pattern_name, pattern_quality fields)", "logs/opportunities/"],
        answers=["what patterns were detected", "which patterns are most profitable", "pattern quality distribution"],
        authority_files=["strategy/signal_orchestrator.py"],
        keywords=["pattern", "signal", "candle", "detection", "tweezer", "engulfing", "hammer", "star"],
    ),
}


def route_question(question: str) -> list[tuple[str, Domain, float]]:
    """
    Route a natural-language question to the most relevant domains.

    Returns list of (domain_name, domain, relevance_score) sorted by relevance.
    Relevance is based on keyword matching against domain keywords + answers.
    """
    question_lower = question.lower()
    scores: list[tuple[str, Domain, float]] = []

    for name, domain in DOMAINS.items():
        score = 0.0

        # Keyword matching
        for kw in domain.keywords:
            if kw in question_lower:
                score += 1.0

        # Answer matching (partial)
        for ans in domain.answers:
            # Check if question overlaps with what this domain answers
            ans_words = set(ans.split())
            q_words = set(question_lower.split())
            overlap = len(ans_words & q_words)
            if overlap >= 2:
                score += 0.5 * overlap

        if score > 0:
            scores.append((name, domain, score))

    scores.sort(key=lambda x: x[2], reverse=True)
    return scores[:3]  # Top 3 most relevant domains
