"""
Runtime Isolation Contract — Permanent Architectural Rule.

NON-NEGOTIABLE DESIGN PRINCIPLE:
    The live trading runtime MUST operate independently of all offline
    intelligence systems. It can never depend on, wait for, or invoke
    any analytics, replay, optimisation, or reporting subsystem.

═══════════════════════════════════════════════════════════════════════════════
RULE 1 — LIVE MUST PRODUCE
═══════════════════════════════════════════════════════════════════════════════

The live runtime exists only to:
    - Observe markets
    - Compute features
    - Evaluate signals
    - Manage risk
    - Execute trades
    - Manage positions
    - Persist outcomes

Its responsibility ends once truth has been written to persistence.

═══════════════════════════════════════════════════════════════════════════════
RULE 2 — OFFLINE MAY CONSUME
═══════════════════════════════════════════════════════════════════════════════

Offline systems may:
    - Consume persisted truth from the live runtime
    - Inspect, analyse, replay, attribute, optimise
    - Compile strategies, generate reports, perform causal reasoning

They are CONSUMERS ONLY. They are NEVER producers of live execution.

═══════════════════════════════════════════════════════════════════════════════
RULE 3 — OFFLINE MUST NEVER BLOCK LIVE
═══════════════════════════════════════════════════════════════════════════════

Failure of ANY offline subsystem must never prevent:
    - Market observation
    - Signal generation
    - Risk evaluation
    - Decision making
    - Broker execution
    - Position management
    - Persistence

If every offline component is disabled, the trading bot MUST continue
operating normally.

═══════════════════════════════════════════════════════════════════════════════
INFORMATION FLOW (ONE-WAY ONLY)
═══════════════════════════════════════════════════════════════════════════════

    LIVE PRODUCES TRUTH
            │
            ▼
      PERSISTENCE
            │
            ▼
    OFFLINE CONSUMES TRUTH

Information flows DOWNWARD ONLY.
Offline systems may analyse the past but must NEVER participate in
live decision execution.

═══════════════════════════════════════════════════════════════════════════════
DEPENDENCY RULES
═══════════════════════════════════════════════════════════════════════════════

ALLOWED:
    LIVE → Persistence (events/, execution_context/, shadow_trades/, trade_truth/)
    OFFLINE → Persistence (read-only)
    OFFLINE → Causal Graph
    OFFLINE → Query Engine
    OFFLINE → Replay Engine

FORBIDDEN:
    LIVE → Replay Engine
    LIVE → Causal Graph/Query Engine
    LIVE → Attribution Engine
    LIVE → Edge Optimisation
    LIVE → Strategy Compiler (at runtime — only via static config deployment)
    LIVE → Reporting/Dashboards
    LIVE → Any offline system

═══════════════════════════════════════════════════════════════════════════════
FAILURE ISOLATION GUARANTEE
═══════════════════════════════════════════════════════════════════════════════

The following failures MUST have ZERO impact on live trading:
    - Replay engine unavailable
    - Causal graph unavailable
    - Query engine unavailable
    - Attribution engine unavailable
    - Strategy compiler unavailable
    - Reporting unavailable
    - Dashboards unavailable

The only acceptable consequence is LOSS OF OBSERVABILITY — never loss
of trading capability.

═══════════════════════════════════════════════════════════════════════════════
SYSTEM BOUNDARY DEFINITION
═══════════════════════════════════════════════════════════════════════════════

LIVE RUNTIME (must never import offline modules):
    data/mt5_data.py              — Market Feed
    core/features/engine.py       — Feature Engine
    core/engine.py                — Signal Engine (pipeline)
    core/pipeline/                — Signal Processing Stages
    risk/                         — Risk Engine
    core/runtime/live_scanner.py  — Decision Engine
    execution/mt5_execution.py    — Broker Execution
    core/trade_management/        — Position Management
    core/event_stream.py          — Observation Persistence
    core/execution_context.py     — Context Persistence
    core/shadow_trades.py         — Shadow Trade Persistence
    core/trade_truth.py           — Outcome Persistence
    core/trade_journal.py         — Journal Persistence
    core/correlation.py           — Correlation ID Generation

OFFLINE SYSTEM (may import from persistence, never imported by live):
    core/causal/                   — Causal Graph + Query + API + Replay
    core/edge_attribution.py       — Causal Attribution
    core/edge_optimisation.py      — Edge Discovery
    core/strategy_compiler.py      — Strategy Generation
    core/trade_truth_graph.py      — Relationship Graph
    analysis/                      — Analytics Pipeline
    data_pipeline/                 — Curated Data Pipeline

SHARED (persistence contracts — the ONLY interface between domains):
    events/                        — Market observations (write: LIVE, read: OFFLINE)
    logs/execution_context/        — Decision environment (write: LIVE, read: OFFLINE)
    logs/shadow_trades/            — Trade intent + outcome (write: LIVE, read: OFFLINE)
    logs/trade_truth/              — Execution reality (write: LIVE, read: OFFLINE)
    logs/trade_truth_graph/        — Relationships (write: OFFLINE, read: OFFLINE)
    logs/edge_attribution/         — Attribution (write: OFFLINE, read: OFFLINE)
    logs/edge_optimisation/        — Edge stats (write: OFFLINE, read: OFFLINE)
    logs/strategy_compiler/        — Strategies (write: OFFLINE, read: deployment)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# RUNTIME DOMAIN CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════════════════

LIVE_RUNTIME_MODULES = frozenset({
    "data.mt5_data",
    "core.features.engine",
    "core.features.bundle",
    "core.engine",
    "core.engine_state",
    "core.pipeline.market_context",
    "core.pipeline.strategy_detection",
    "core.pipeline.structure_analysis",
    "core.pipeline.confirmations",
    "core.pipeline.scoring_engine",
    "core.pipeline.trade_quality",
    "core.pipeline.intent_builder",
    "core.pipeline.decision_engine",
    "core.runtime.live_scanner",
    "core.runtime.shutdown",
    "core.runtime.risk_event_emitter",
    "core.runtime.runtime_utils",
    "core.event_stream",
    "core.execution_context",
    "core.shadow_trades",
    "core.trade_truth",
    "core.trade_journal",
    "core.correlation",
    "core.symbol_resolver",
    "core.clock",
    "core.config",
    "core.mt5_connection",
    "core.stale_monitor",
    "core.trade_management",
    "core.kill_switch",
    "core.state_persistence",
    "core.decision_audit",
    "core.decision_ledger",
    "execution.mt5_execution",
    "risk.manager",
    "risk.spread_guard",
    "risk.drawdown_guard",
    "risk.daily_loss_guard",
    "risk.daily_trade_limit",
    "risk.portfolio_exposure_guard",
    "risk.regime_guard",
    "risk.trade_cooldown",
    "risk.correlation_guard",
    "risk.position_sizing",
    "strategy.signals",
    "strategy.signal_orchestrator",
    "strategy.setup",
})

OFFLINE_MODULES = frozenset({
    "core.causal",
    "core.causal.graph",
    "core.causal.engine",
    "core.causal.query",
    "core.causal.api",
    "core.causal.replay",
    "core.edge_attribution",
    "core.edge_optimisation",
    "core.strategy_compiler",
    "core.trade_truth_graph",
    "core.behaviour_validation",
    "core.offline_query",
    "core.feature_role_contract",
    "core.audit_persistence",
    "analysis",
    "data_pipeline",
})


# ═══════════════════════════════════════════════════════════════════════════════
# ISOLATION VALIDATOR
# ═══════════════════════════════════════════════════════════════════════════════

def validate_runtime_isolation(*, project_root: str = ".") -> dict[str, Any]:
    """
    Validate that no LIVE module imports any OFFLINE module.

    Returns structured report of violations (if any).
    This is a static analysis check — run at CI/test time.
    """
    root = Path(project_root)
    violations: list[dict[str, str]] = []

    # Map module names to file paths for live modules
    live_files: list[Path] = []
    for module in LIVE_RUNTIME_MODULES:
        fpath = root / module.replace(".", "/")
        # Try both as file and as directory/__init__
        candidates = [fpath.with_suffix(".py"), fpath / "__init__.py"]
        for c in candidates:
            if c.exists():
                live_files.append(c)

    # Check each live file for imports of offline modules
    for live_file in live_files:
        try:
            source = live_file.read_text(encoding="utf-8")
        except Exception:
            continue

        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue

            for offline_mod in OFFLINE_MODULES:
                # Check for direct imports
                if f"from {offline_mod}" in stripped or f"import {offline_mod}" in stripped:
                    # Allow try/except guarded imports (fire-and-forget observability)
                    # These are acceptable IF they're in a try block that catches and passes
                    violations.append({
                        "live_module": str(live_file.relative_to(root)),
                        "imports_offline": offline_mod,
                        "line": stripped[:100],
                    })

    return {
        "status": "PASS" if not violations else "FAIL",
        "violations": violations,
        "live_modules_checked": len(live_files),
        "offline_modules_guarded": len(OFFLINE_MODULES),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# CONTRACT SUMMARY (for documentation generation)
# ═══════════════════════════════════════════════════════════════════════════════

RUNTIME_ISOLATION_CONTRACT = {
    "contract_name": "Runtime Isolation",
    "version": "1.0",
    "status": "ACTIVE",
    "introduced_in": "Arc1",

    "rules": [
        "LIVE MUST PRODUCE — runtime persists truth, then stops",
        "OFFLINE MAY CONSUME — analytics reads persistence only",
        "OFFLINE MUST NEVER BLOCK LIVE — failure = observability loss only",
    ],

    "information_flow": "ONE-WAY: LIVE → Persistence → OFFLINE",

    "forbidden_dependencies": [
        "LIVE → Replay Engine",
        "LIVE → Causal Graph",
        "LIVE → Attribution Engine",
        "LIVE → Edge Optimisation",
        "LIVE → Strategy Compiler (runtime)",
        "LIVE → Reporting",
    ],

    "failure_isolation": [
        "Replay unavailable → ZERO impact on live",
        "Causal graph unavailable → ZERO impact on live",
        "Attribution unavailable → ZERO impact on live",
        "Optimisation unavailable → ZERO impact on live",
        "Strategy compiler unavailable → ZERO impact on live",
    ],
}
