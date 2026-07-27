"""
Causal Trade Replay Engine — Forensic reconstruction of historical decisions.

Reconstructs: "What the system knew, decided, and believed at the exact
moment a trade happened."

This is NOT simulation. It is deterministic reconstruction of historical
decision states using persisted events + causal graph traversal.

USES:
    - trade_truth/ (what actually happened)
    - decision_audit/ (what was decided)
    - execution_context/ (what conditions existed)
    - shadow_trades/ (what was intended)
    - events/ (what the market was doing)
    - Causal graph + API layer (why it happened)

MUST NOT:
    - Infer missing history
    - Guess signals
    - Regenerate decisions
    - Simulate future trades as truth
    - Overwrite historical state
    - Modify causal graph

Position in system:
    ONTOLOGY → GRAPH → QUERY ENGINE → API → REPLAY ENGINE ← THIS

Usage:
    from core.causal.replay import get_replay_engine

    replay = get_replay_engine()
    result = replay.replay_trade("shadow_100_EURUSD")
    result = replay.risk_autopsy("shadow_100_EURUSD")
    result = replay.what_if_trade("shadow_100_EURUSD", "SIGNAL.BIAS_TRANSITION")
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from core.causal.api import CausalAPI, get_causal_api

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# DATA LOADERS (read-only access to persistence layers)
# ═══════════════════════════════════════════════════════════════════════════════

def _load_jsonl_by_field(
    base_dir: str,
    field: str,
    value: str,
) -> dict[str, Any] | None:
    """Load first record matching field=value from any JSONL file in directory."""
    path = Path(base_dir)
    if not path.exists():
        return None
    for f in sorted(path.rglob("*.jsonl"), reverse=True):
        try:
            for line in f.read_text(encoding="utf-8").strip().split("\n"):
                if not line.strip():
                    continue
                rec = json.loads(line)
                # Check both flat and nested identity structures
                if rec.get(field) == value:
                    return rec
                identity = rec.get("identity", {})
                if isinstance(identity, dict) and identity.get(field) == value:
                    return rec
        except (json.JSONDecodeError, OSError):
            continue
    return None


def _load_shadow_trade(trade_id: str) -> dict[str, Any] | None:
    """Load a shadow trade record by trade_id."""
    return _load_jsonl_by_field("logs/shadow_trades", "trade_id", trade_id)


def _load_trade_truth(trade_id: str) -> dict[str, Any] | None:
    """Load a trade_truth record by trade_id."""
    return _load_jsonl_by_field("logs/trade_truth", "trade_id", trade_id)


def _load_decision_audit(trade_id: str) -> dict[str, Any] | None:
    """Load a decision audit record. Searches by trade_id or decision_id."""
    # Decision audits may reference trade via different fields
    rec = _load_jsonl_by_field("logs/decision_audit", "trade_id", trade_id)
    if rec:
        return rec
    # Try matching by cycle pattern in trade_id (shadow_CYCLE_SYMBOL)
    parts = trade_id.split("_")
    if len(parts) >= 2:
        cycle = parts[1] if parts[0] == "shadow" else parts[0]
        return _load_jsonl_by_field("logs/decision_audit", "cycle_id", int(cycle) if cycle.isdigit() else cycle)
    return None


def _load_execution_context(correlation_id: str) -> dict[str, Any] | None:
    """Load execution context by correlation_id."""
    if not correlation_id:
        return None
    return _load_jsonl_by_field("logs/execution_context", "correlation_id", correlation_id)


def _load_market_events(symbol: str, timestamp: float, window_seconds: int = 300) -> list[dict[str, Any]]:
    """Load market events around a timestamp (±window)."""
    events: list[dict[str, Any]] = []
    events_dir = Path("events")
    if not events_dir.exists():
        return events

    start_ms = int((timestamp - window_seconds) * 1000)
    end_ms = int((timestamp + window_seconds) * 1000)

    for f in sorted(events_dir.glob("*.jsonl"), reverse=True):
        try:
            for line in f.read_text(encoding="utf-8").strip().split("\n"):
                if not line.strip():
                    continue
                evt = json.loads(line)
                if evt.get("symbol") != symbol:
                    continue
                ts = evt.get("ts_utc_ms", 0)
                if start_ms <= ts <= end_ms:
                    etype = evt.get("type", "")
                    if etype in ("CANDLE", "FEATURE_UPDATE"):
                        events.append(evt)
        except (json.JSONDecodeError, OSError):
            continue
        if events:
            break  # Found relevant file

    return sorted(events, key=lambda e: e.get("ts_utc_ms", 0))


# ═══════════════════════════════════════════════════════════════════════════════
# REPLAY ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class CausalReplayEngine:
    """
    Reconstructs historical trade decisions using causal graph + persisted events.

    Deterministic. Read-only. Never modifies state.
    """

    def __init__(self, api: CausalAPI) -> None:
        self.api = api

    # ─── FULL TRADE REPLAY ────────────────────────────────────────────

    def replay_trade(self, trade_id: str) -> dict[str, Any]:
        """
        Complete forensic reconstruction of a trade decision.

        Returns the full causal context: what the system knew, decided,
        and believed at the exact moment the trade happened.
        """
        # Step 1: Load persisted truth
        shadow = _load_shadow_trade(trade_id)
        truth = _load_trade_truth(trade_id)

        # Extract identity from either schema
        if shadow:
            identity = shadow.get("identity", {})
            correlation_id = identity.get("correlation_id", shadow.get("correlation_id", ""))
            symbol = identity.get("symbol", shadow.get("symbol", ""))
            decision_snapshot = shadow.get("decision_snapshot", {})
            timestamp = decision_snapshot.get("timestamp_decision_utc", 0)
        elif truth:
            identity = truth.get("identity", {})
            correlation_id = identity.get("correlation_id", truth.get("correlation_id", ""))
            symbol = identity.get("symbol", truth.get("symbol", ""))
            timestamp = truth.get("timestamps", {}).get("entry_timestamp_broker", 0)
            decision_snapshot = {}
        else:
            return {"error": f"trade_id '{trade_id}' not found in any persistence layer"}

        # Step 2: Load execution context
        context = _load_execution_context(correlation_id)

        # Step 3: Load decision audit
        decision = _load_decision_audit(trade_id)

        # Step 4: Reconstruct causal lineage
        lineage = self.api.lineage("DECISION.SHADOW_TRADE")

        # Step 5: Reconstruct risk surface
        risk_surface = self.api.risk_surface("DECISION.SHADOW_TRADE")

        # Step 6: Load market snapshot around decision time
        market_events = _load_market_events(symbol, timestamp) if timestamp > 0 else []

        # Step 7: Extract signal state from decision
        signals = {}
        if decision:
            signals = {
                "confluence_score": decision.get("score", 0),
                "bias_state": decision.get("side", None),
                "bias_phase": decision.get("bias_phase", ""),
                "patterns": decision.get("patterns", []),
                "structure_ok": decision.get("structure_ok", None),
            }
        elif decision_snapshot:
            signals = {
                "confluence_score": decision_snapshot.get("score", 0),
                "pattern": decision_snapshot.get("pattern", ""),
                "direction": decision_snapshot.get("direction", ""),
            }

        # Step 8: Build summary
        root_causes = []
        if lineage.get("paths"):
            shortest = lineage.get("shortest_path", [])
            if shortest:
                root_causes = [shortest[0]] if shortest else []

        return {
            "trade_id": trade_id,
            "correlation_id": correlation_id,
            "symbol": symbol,
            "timestamp": timestamp,

            "decision": decision,
            "decision_snapshot": decision_snapshot,
            "context": context,
            "market": {
                "events_count": len(market_events),
                "window_seconds": 300,
                "candles": [e for e in market_events if e.get("type") == "CANDLE"],
                "features": [e for e in market_events if e.get("type") == "FEATURE_UPDATE"],
            },

            "causal_lineage": lineage,
            "risk_surface": risk_surface,

            "execution": truth,
            "shadow_trade": shadow,

            "signals": signals,

            "summary": {
                "root_causes": root_causes,
                "critical_path_length": len(lineage.get("shortest_path", [])),
                "total_causal_paths": lineage.get("path_count", 0),
                "nodes_at_risk": risk_surface.get("total_at_risk", 0),
                "hard_dependencies": len(risk_surface.get("hard_failures", [])),
            },
        }

    # ─── WHAT-IF REPLAY (counterfactual) ──────────────────────────────

    def what_if_trade(self, trade_id: str, change_node: str) -> dict[str, Any]:
        """
        Counterfactual analysis: what would have happened if a node were removed?

        Does NOT re-simulate. Only shows causal impact based on graph structure.
        """
        original = self.replay_trade(trade_id)
        if "error" in original:
            return original

        # Run what-if simulation on the graph
        impact = self.api.what_if(change_node)

        # Check if the change would have prevented the trade
        decision_affected = "DECISION.SHADOW_TRADE" in impact.get("hard_failures", [])
        scoring_affected = "SIGNAL.CONFLUENCE_SCORE" in impact.get("hard_failures", [])

        return {
            "original_trade": trade_id,
            "change_node": change_node,
            "impact": impact,
            "would_trade_have_occurred": not (decision_affected or scoring_affected),
            "affected_nodes": impact.get("hard_failures", []) + impact.get("degraded", []),
            "severity": "TRADE_PREVENTED" if decision_affected else "TRADE_DEGRADED" if scoring_affected else "NO_EFFECT",
        }

    # ─── RISK AUTOPSY ─────────────────────────────────────────────────

    def risk_autopsy(self, trade_id: str) -> dict[str, Any]:
        """
        Post-trade risk analysis: which gates passed, which almost failed,
        which upstream signals contributed to the risk state.
        """
        shadow = _load_shadow_trade(trade_id)
        context = None
        if shadow:
            identity = shadow.get("identity", {})
            cor_id = identity.get("correlation_id", shadow.get("correlation_id", ""))
            context = _load_execution_context(cor_id)

        # Get risk surface from causal graph
        risk_surface = self.api.risk_surface("DECISION.SHADOW_TRADE")

        # Extract risk state from execution context
        risk_state = {}
        if context:
            risk_env = context.get("risk_environment", {})
            risk_state = {
                "drawdown_at_decision": risk_env.get("drawdown_pct", 0),
                "daily_loss_at_decision": risk_env.get("daily_loss_pct", 0),
                "open_positions_at_decision": risk_env.get("open_positions", 0),
                "correlation_exposure": risk_env.get("correlation_exposure", 0),
            }

            market = context.get("market_access", {})
            risk_state["spread_at_decision"] = market.get("spread", 0)
            risk_state["session_at_decision"] = market.get("session_state", "UNKNOWN")

        # Get all risk guard nodes and their causal relationships
        risk_guards = self.api.find(domain="RISK")
        guard_analysis = []
        for node in risk_guards.get("nodes", []):
            backward = self.api.backward(node["id"])
            guard_analysis.append({
                "guard": node["id"],
                "feature": node["feature"],
                "causal_inputs": backward.get("cause_count", 0),
                "status": "PASSED",  # All guards passed (trade was taken)
            })

        return {
            "trade_id": trade_id,
            "risk_surface": risk_surface,
            "risk_state_at_decision": risk_state,
            "guard_analysis": guard_analysis,
            "all_gates_passed": True,  # Trade exists = all gates passed
            "total_gates_evaluated": len(guard_analysis),
        }

    # ─── DECISION RECONSTRUCTION ──────────────────────────────────────

    def reconstruct_decision(self, trade_id: str) -> dict[str, Any]:
        """
        Rebuilds the complete decision state at the moment of trade entry.

        Returns: signal state, bias state, confluence inputs, risk evaluation
        order, and execution intent — the full "why did the bot think this
        trade was good?" answer.
        """
        shadow = _load_shadow_trade(trade_id)
        decision = _load_decision_audit(trade_id)

        if not shadow and not decision:
            return {"error": f"No decision data found for trade_id '{trade_id}'"}

        # Extract from shadow trade (STR schema)
        decision_snapshot = {}
        simulation_env = {}
        if shadow:
            decision_snapshot = shadow.get("decision_snapshot", {})
            simulation_env = shadow.get("simulation_environment", {})

        # Extract from decision audit (if available)
        audit_state = {}
        if decision:
            audit_state = {
                "should_trade": decision.get("should_trade", None),
                "score": decision.get("score", 0),
                "side": decision.get("side", None),
                "bias_phase": decision.get("bias_phase", ""),
                "patterns": decision.get("patterns", []),
                "structure_ok": decision.get("structure_ok", None),
                "last_stage": decision.get("last_completed_stage", ""),
                "engine_state": decision.get("engine_state", {}),
                "intent": decision.get("intent", None),
            }

        # Causal analysis of the decision
        decision_causes = self.api.backward("DECISION.SHADOW_TRADE")
        score_causes = self.api.backward("SIGNAL.CONFLUENCE_SCORE")

        return {
            "trade_id": trade_id,

            "decision_snapshot": decision_snapshot,
            "simulation_environment": simulation_env,
            "audit_state": audit_state,

            "causal_explanation": {
                "decision_ancestors": decision_causes.get("causal_ancestors", []),
                "score_ancestors": score_causes.get("causal_ancestors", []),
                "decision_by_domain": decision_causes.get("by_domain", {}),
            },

            "signal_state": {
                "pattern": decision_snapshot.get("pattern", audit_state.get("patterns", [])),
                "score": decision_snapshot.get("score", audit_state.get("score", 0)),
                "direction": decision_snapshot.get("direction", audit_state.get("side", "")),
            },

            "risk_state": {
                "all_guards_passed": True,
                "evaluation_order": [
                    "RISK.DRAWDOWN_GUARD", "RISK.DAILY_LOSS_GUARD", "RISK.STALE_DATA",
                    "RISK.DAILY_TRADE_LIMIT", "RISK.TRADE_COOLDOWN", "RISK.CORRELATION",
                    "RISK.PORTFOLIO_EXPOSURE", "RISK.REGIME", "RISK.CHALLENGE",
                    "RISK.CONSISTENCY", "RISK.PROP_FIRM", "RISK.WEEKEND", "RISK.CONTROL_GATE",
                ],
            },
        }


# ═══════════════════════════════════════════════════════════════════════════════
# SINGLETON ACCESS
# ═══════════════════════════════════════════════════════════════════════════════

_replay_engine: CausalReplayEngine | None = None


def get_replay_engine() -> CausalReplayEngine:
    """Get or create the singleton replay engine."""
    global _replay_engine
    if _replay_engine is None:
        _replay_engine = CausalReplayEngine(get_causal_api())
    return _replay_engine
