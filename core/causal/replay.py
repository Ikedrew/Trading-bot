"""
Causal Trade Replay Engine — Forensic reconstruction of historical decisions.

Reconstructs: "What the system knew, decided, and believed at the exact
moment a trade happened."

This is NOT simulation. It is deterministic reconstruction of historical
decision states using persisted events + causal graph traversal.

USES:
    - trade_truth/ (what actually happened)
    - decision_ledger/ (authoritative terminal decision: action/reason/lineage)
    - decision_trace/ (diagnostic reasoning: score/pattern/structure/stage)
    - execution_context/ (what conditions existed)
    - shadow_trades/ (what was intended)
    - events/ (what the market was doing)
    - Causal graph + API layer (why it happened)

NOTE (Production V1): the retired ``decision_audit`` dataset was removed. The
authoritative terminal decision now comes from ``decision_ledger`` and the
diagnostic reasoning from ``decision_trace``; this module reads those retained
V1 authorities directly and joins them by canonical lineage. Absence of
``logs/decision_audit/`` is normal and never required.

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


def _load_jsonl_by_lineage(
    base_dir: str,
    lineage: dict[str, Any],
) -> dict[str, Any] | None:
    """Load the first record in a dataset matching any available lineage identity.

    Join priority (most authoritative → least): canonical_opportunity_id,
    correlation_id, entity_id, decision_id. Checks both flat and nested-identity
    record shapes. Returns None when the dataset directory is absent or no match.
    """
    path = Path(base_dir)
    if not path.exists():
        return None

    # Ordered lineage keys to attempt (skip empties).
    join_keys = [
        ("canonical_opportunity_id", lineage.get("canonical_opportunity_id")),
        ("correlation_id", lineage.get("correlation_id")),
        ("entity_id", lineage.get("entity_id")),
        ("decision_id", lineage.get("decision_id")),
    ]
    join_keys = [(k, v) for k, v in join_keys if v]
    if not join_keys:
        return None

    for f in sorted(path.rglob("*.jsonl"), reverse=True):
        try:
            for line in f.read_text(encoding="utf-8").strip().split("\n"):
                if not line.strip():
                    continue
                rec = json.loads(line)
                identity = rec.get("identity", {})
                identity = identity if isinstance(identity, dict) else {}
                for key, value in join_keys:
                    if rec.get(key) == value or identity.get(key) == value:
                        return rec
        except (json.JSONDecodeError, OSError):
            continue
    return None


def _extract_lineage(shadow: dict[str, Any] | None, truth: dict[str, Any] | None) -> dict[str, Any]:
    """Extract canonical lineage identity from a shadow or trade_truth record.

    Both carry a nested ``identity`` block owning the canonical lineage IDs.
    """
    src = shadow or truth or {}
    identity = src.get("identity", {})
    identity = identity if isinstance(identity, dict) else {}
    return {
        "canonical_opportunity_id": identity.get("canonical_opportunity_id", src.get("canonical_opportunity_id", "")),
        "correlation_id": identity.get("correlation_id", src.get("correlation_id", "")),
        "entity_id": identity.get("entity_id", src.get("entity_id", "")),
        "decision_id": identity.get("decision_id", src.get("decision_id", "")),
        "cycle_id": identity.get("cycle_id", src.get("cycle_id", "")),
    }


def _load_decision_ledger_record(lineage: dict[str, Any]) -> dict[str, Any] | None:
    """AUTHORITATIVE terminal decision facts (action/reason/lineage).

    Sourced from the retained ``decision_ledger`` dataset — the decision
    authority. Never inferred from diagnostic trace when a ledger record exists.
    """
    return _load_jsonl_by_lineage("logs/decision_ledger", lineage)


def _load_decision_trace_record(lineage: dict[str, Any]) -> dict[str, Any] | None:
    """DIAGNOSTIC reasoning/detail (score/pattern/structure/terminal stage).

    Sourced from the retained ``decision_trace`` dataset, which absorbed the
    former decision_audit diagnostic fields (structure_ok, entry_timing,
    trigger_candle, stability_policy, ...). Used only for reasoning the ledger
    does not own.
    """
    return _load_jsonl_by_lineage("logs/decision_trace", lineage)


def _merge_decision_view(
    ledger: dict[str, Any] | None,
    trace: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Build a read-only decision view from retained authorities.

    AUTHORITY RULE: terminal decision facts (action/reason/decision_id/lineage,
    execution intent) come from ``decision_ledger``. Diagnostic reasoning
    (score components, pattern, structure_ok, terminal stage) comes from
    ``decision_trace``. Terminal facts are NEVER inferred from trace when the
    ledger owns them. Returns None only when NEITHER authority has a record.

    Field keys mirror the shape the replay projection consumes; missing fields
    are represented as unavailable (None / absent), never fabricated.
    """
    if not ledger and not trace:
        return None

    ledger = ledger or {}
    trace = trace or {}

    _intent = ledger.get("execution_intent") or {}
    _decision = ledger.get("decision")  # EXECUTE | NO_TRADE | RISK_BLOCK | ...
    # Authoritative terminal decision → boolean "should_trade".
    _should_trade = (_decision == "EXECUTE") if _decision is not None else None

    return {
        # ── Terminal decision (AUTHORITATIVE — decision_ledger) ──
        "decision": _decision,
        "should_trade": _should_trade,
        "reason": ledger.get("reason", ""),
        "decision_id": ledger.get("decision_id", trace.get("decision_id", "")),
        "correlation_id": ledger.get("correlation_id", trace.get("correlation_id", "")),
        "canonical_opportunity_id": ledger.get(
            "canonical_opportunity_id", trace.get("canonical_opportunity_id", "")
        ),
        "observation_id": ledger.get("observation_id", trace.get("observation_id", "")),
        "entity_id": ledger.get("entity_id", trace.get("entity_id", "")),
        "side": (_intent.get("side") if _intent else None),
        "intent": ledger.get("execution_intent"),
        "score": ledger.get("signal_score", trace.get("score_strategy", 0)),
        # ── Diagnostic reasoning (decision_trace) ──
        "last_stage": trace.get("terminal_stage", ledger.get("last_stage", "")),
        "pattern": trace.get("pattern_name"),
        "patterns": [trace["pattern_name"]] if trace.get("pattern_name") else [],
        "structure_ok": trace.get("structure_ok"),
        "trade_horizon": trace.get("trade_horizon"),
        "selected_strategy": trace.get("selected_strategy"),
        "score_components": trace.get("components", {}),
        # bias_phase / engine_state have no retained V1 equivalent — unavailable.
        "bias_phase": trace.get("metadata", {}).get("bias_phase", "") if isinstance(trace.get("metadata"), dict) else "",
        # Provenance so consumers know which authorities backed this view.
        "_sources": {
            "ledger": bool(ledger),
            "trace": bool(trace),
        },
    }


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

        # Step 3: Load the decision from RETAINED V1 authorities.
        #   - decision_ledger = authoritative terminal decision (action/reason)
        #   - decision_trace  = diagnostic reasoning (score/pattern/structure)
        # Joined by canonical lineage from the shadow/truth record.
        _lineage = _extract_lineage(shadow, truth)
        ledger = _load_decision_ledger_record(_lineage)
        trace = _load_decision_trace_record(_lineage)
        decision = _merge_decision_view(ledger, trace)

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
        truth = _load_trade_truth(trade_id)
        _lineage = _extract_lineage(shadow, truth)
        ledger = _load_decision_ledger_record(_lineage)
        trace = _load_decision_trace_record(_lineage)
        decision = _merge_decision_view(ledger, trace)

        if not shadow and not decision:
            return {"error": f"No decision data found for trade_id '{trade_id}'"}

        # Extract from shadow trade (STR schema)
        decision_snapshot = {}
        simulation_env = {}
        if shadow:
            decision_snapshot = shadow.get("decision_snapshot", {})
            simulation_env = shadow.get("simulation_environment", {})

        # Decision state from retained authorities (ledger=terminal, trace=diagnostic).
        # engine_state has no retained V1 equivalent → represented as unavailable ({}).
        audit_state = {}
        if decision:
            audit_state = {
                "should_trade": decision.get("should_trade", None),
                "score": decision.get("score", 0),
                "side": decision.get("side", None),
                "bias_phase": decision.get("bias_phase", ""),
                "patterns": decision.get("patterns", []),
                "structure_ok": decision.get("structure_ok", None),
                "last_stage": decision.get("last_stage", ""),
                "engine_state": {},  # not owned by any retained V1 dataset — unavailable
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
