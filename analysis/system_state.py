"""
System State Synchronisation Layer — Single source of truth for all modules.

Maintains one canonical state file that all modules MUST read and update.
Eliminates inconsistency from modules reading different intermediate outputs.

State file: analysis/reports/system_state.json

This is NOT an analytics layer. It ONLY:
    - Reads existing module outputs
    - Aggregates into one consistent state object
    - Writes a single canonical file
    - Provides read/write API for other modules

Usage:
    from analysis.system_state import refresh_state, read_state, get_field

    # Rebuild state from all sources
    refresh_state()

    # Read current canonical state
    state = read_state()
    print(state["active_state"]["risk_state"])

    # Get a specific field
    confidence = get_field("active_state.confidence")
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

STATE_PATH = "analysis/reports/system_state.json"
STATE_VERSION = "1.0"


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE PATHS (canonical locations of module outputs)
# ═══════════════════════════════════════════════════════════════════════════════

SOURCES = {
    "rule_compression": "analysis/reports/rule_compression.json",
    "confidence_score": "analysis/reports/confidence_score.json",
    "orchestrator": "analysis/reports/trading_decision.json",
    "walk_forward": "analysis/reports/walk_forward.json",
    "shadow_execution": "analysis/reports/shadow_execution.json",
    "stress_test": "analysis/reports/regime_stress_test.json",
    "drift_monitor": "analysis/reports/drift_monitor.json",
    "compression_validation": "analysis/reports/compression_validation.json",
    "experiment": "analysis/reports/experiment_result.json",
    "edge_optimiser": "analysis/reports/edge_optimiser.json",
    "rule_interactions": "analysis/reports/rule_interactions.json",
}


def _load(path: str) -> dict[str, Any] | None:
    p = Path(path)
    if not p.exists():
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


# ═══════════════════════════════════════════════════════════════════════════════
# STATE CONSTRUCTION
# ═══════════════════════════════════════════════════════════════════════════════

def _build_state() -> dict[str, Any]:
    """Build canonical system state from all module outputs."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Load all sources
    compression = _load(SOURCES["rule_compression"])
    confidence = _load(SOURCES["confidence_score"])
    orchestrator = _load(SOURCES["orchestrator"])
    walk_forward = _load(SOURCES["walk_forward"])
    shadow = _load(SOURCES["shadow_execution"])
    stress = _load(SOURCES["stress_test"])
    drift = _load(SOURCES["drift_monitor"])
    comp_val = _load(SOURCES["compression_validation"])
    experiment = _load(SOURCES["experiment"])
    optimiser = _load(SOURCES["edge_optimiser"])
    interactions = _load(SOURCES["rule_interactions"])

    # ─── Active state (primary decision fields) ───────────────────────
    active_state = {
        "ruleset": "compressed" if compression else "unknown",
        "rule_count": compression.get("compressed_rule_count", 0) if compression else 0,
        "confidence": 0,
        "confidence_grade": "?",
        "risk_state": "UNKNOWN",
        "decision": "NO_TRADE",
        "decision_mode": "SKIP",
        "conflict_status": "unknown",
        "drift_score": 0,
        "robustness_score": 0,
    }

    if confidence:
        overall = confidence.get("overall_confidence", {})
        active_state["confidence"] = overall.get("score", 0)
        active_state["confidence_grade"] = overall.get("grade", "?")

    if orchestrator:
        active_state["risk_state"] = orchestrator.get("risk_state", "UNKNOWN")
        active_state["decision"] = orchestrator.get("decision", "NO_TRADE")
        active_state["decision_mode"] = orchestrator.get("recommended_position_action", "SKIP")

    if interactions:
        risk = interactions.get("system_risk", {})
        if risk.get("instability_flag"):
            active_state["conflict_status"] = "unstable"
        elif risk.get("rule_stack_risk_score", 0) > 40:
            active_state["conflict_status"] = "warning"
        else:
            active_state["conflict_status"] = "clean"

    if drift:
        active_state["drift_score"] = drift.get("overall_drift_score", 0)

    if stress:
        active_state["robustness_score"] = stress.get("overall_robustness_score", 0)

    # ─── Validation state ─────────────────────────────────────────────
    validation = {
        "walk_forward_valid": False,
        "shadow_positive": False,
        "compression_valid": False,
        "stress_test_passed": False,
        "experiment_status": "none",
    }

    if walk_forward:
        summary = walk_forward.get("overall_summary", {})
        validation["walk_forward_valid"] = not summary.get("overall_edge_decay", True)

    if shadow:
        sp = shadow.get("shadow_results", {}).get("total_pnl", 0)
        bp = shadow.get("baseline_results", {}).get("total_pnl", 0)
        validation["shadow_positive"] = sp >= bp

    if comp_val:
        validation["compression_valid"] = comp_val.get("summary", {}).get("compression_valid", False)

    if stress:
        validation["stress_test_passed"] = stress.get("overall_robustness_score", 0) >= 40

    if experiment:
        validation["experiment_status"] = experiment.get("decision", "none").lower()

    # ─── Health flags ─────────────────────────────────────────────────
    health = {
        "system_operational": True,
        "edge_preserved": validation["compression_valid"],
        "regime_safe": validation["stress_test_passed"],
        "drift_safe": active_state["drift_score"] <= 25,
        "rules_coherent": active_state["conflict_status"] == "clean",
    }
    health["system_operational"] = all([
        health["edge_preserved"],
        health["regime_safe"],
        health["drift_safe"],
    ])

    # ─── Source freshness ─────────────────────────────────────────────
    source_status = {}
    for name, path in SOURCES.items():
        src = _load(path)
        if src is None:
            source_status[name] = {"available": False, "path": path}
        else:
            ts = (
                src.get("metadata", {}).get("generated_at", "") or
                src.get("generated_at", "") or
                "unknown"
            )
            source_status[name] = {"available": True, "path": path, "last_generated": ts}

    return {
        "version": STATE_VERSION,
        "last_updated": now,
        "active_state": active_state,
        "validation": validation,
        "health": health,
        "sources": source_status,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def refresh_state(output_path: str = STATE_PATH) -> dict[str, Any]:
    """
    Rebuild canonical state from all module outputs and write to disk.

    This is the ONLY function that writes system_state.json.
    Call after any module produces new output.
    """
    state = _build_state()

    filepath = Path(output_path)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, default=str)

    logger.info(
        "[STATE] Refreshed — decision=%s confidence=%d risk=%s drift=%d",
        state["active_state"]["decision"],
        state["active_state"]["confidence"],
        state["active_state"]["risk_state"],
        state["active_state"]["drift_score"],
    )
    return state


def read_state(state_path: str = STATE_PATH) -> dict[str, Any]:
    """
    Read the current canonical system state.

    All modules MUST use this to get system state — never read
    individual report files directly for state information.
    """
    state = _load(state_path)
    if state is None:
        logger.warning("[STATE] State file not found — refreshing")
        return refresh_state(state_path)
    return state


def get_field(dotpath: str, state_path: str = STATE_PATH) -> Any:
    """
    Get a specific field from system state using dot notation.

    Examples:
        get_field("active_state.confidence")
        get_field("active_state.risk_state")
        get_field("health.system_operational")
        get_field("validation.walk_forward_valid")
    """
    state = read_state(state_path)
    parts = dotpath.split(".")
    current = state
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def is_safe_to_trade(state_path: str = STATE_PATH) -> bool:
    """Quick check: is the system in a state where live trading is allowed?"""
    state = read_state(state_path)
    return state.get("active_state", {}).get("decision") == "TRADE"


def is_operational(state_path: str = STATE_PATH) -> bool:
    """Quick check: is the system healthy and operational?"""
    state = read_state(state_path)
    return state.get("health", {}).get("system_operational", False)


# ═══════════════════════════════════════════════════════════════════════════════
# DISPLAY
# ═══════════════════════════════════════════════════════════════════════════════

def print_state(state: dict[str, Any] | None = None) -> None:
    """Print human-readable system state dashboard."""
    if state is None:
        state = read_state()

    active = state.get("active_state", {})
    validation = state.get("validation", {})
    health = state.get("health", {})
    sources = state.get("sources", {})

    print()
    print("═══════════════════════════════════════════════════════════════")
    print("  SYSTEM STATE (single source of truth)")
    print("═══════════════════════════════════════════════════════════════")
    print(f"  Version: {state.get('version', '?')} | Updated: {state.get('last_updated', '?')}")
    print()

    # Active state
    dec = active.get("decision", "?")
    conf = active.get("confidence", 0)
    risk = active.get("risk_state", "?")
    icons = {"TRADE": "✓", "NO_TRADE": "✗", "SHADOW_MODE": "◐"}
    print(f"  Decision:   {icons.get(dec, '?')} {dec} (confidence={conf}, risk={risk})")
    print(f"  Ruleset:    {active.get('ruleset', '?')} ({active.get('rule_count', 0)} rules)")
    print(f"  Conflicts:  {active.get('conflict_status', '?')}")
    print(f"  Drift:      {active.get('drift_score', 0)}/100")
    print(f"  Robustness: {active.get('robustness_score', 0)}/100")
    print()

    # Health
    print("─── HEALTH FLAGS ───────────────────────────────────────────────")
    for flag, value in health.items():
        icon = "✓" if value else "✗"
        print(f"  {icon} {flag}")
    print()

    # Validation
    print("─── VALIDATION ─────────────────────────────────────────────────")
    for check, value in validation.items():
        icon = "✓" if value else "✗" if isinstance(value, bool) else f"[{value}]"
        print(f"  {icon} {check}")
    print()

    # Source availability
    available = sum(1 for s in sources.values() if s.get("available"))
    total = len(sources)
    print(f"─── SOURCES ({available}/{total} available) ─────────────────────────────")
    for name, info in sources.items():
        icon = "✓" if info.get("available") else "✗"
        print(f"  {icon} {name}")
    print()
    print("═══════════════════════════════════════════════════════════════")


# ═══════════════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    action = sys.argv[1] if len(sys.argv) > 1 else "refresh"

    if action == "refresh":
        state = refresh_state()
        print_state(state)
    elif action == "read":
        state = read_state()
        print_state(state)
    elif action == "json":
        state = read_state()
        print(json.dumps(state, indent=2))
    else:
        print(f"Usage: python system_state.py [refresh|read|json]")
