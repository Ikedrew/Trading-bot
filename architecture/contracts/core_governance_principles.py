"""
Core Governance Principles — System-wide influence isolation rules.

PRINCIPLE: Nothing in the system is allowed to silently become a decision gate.
All influence paths must be explicitly declared and test-covered.

This is a governance layer, not a feature.
"""

from __future__ import annotations


# ─── SYSTEM GOVERNANCE PRINCIPLE ──────────────────────────────────────────────

SYSTEM_GOVERNANCE_PRINCIPLE = """
1. Only Execution Authority Layer (EA-1 → EA-4) may decide trade execution outcomes.
2. Voters may only emit scores, never final decisions.
3. ConfluenceEngine may only aggregate weighted inputs, not introduce hidden rules.
4. FeatureEngine may only transform raw market data into features.
5. Snapshot is immutable after creation (read-only contract).
6. No diagnostic, log, or metric may influence decisions unless explicitly
   routed through ConfluenceEngine.
7. Any influence path must be explicitly declared and test-covered.
"""


# ─── EXECUTION AUTHORITY LAYERS ───────────────────────────────────────────────
# Only these modules may approve/reject/modify trade execution.

EXECUTION_AUTHORITY_MODULES = {
    "EA-1": "core.pipeline.scoring_engine",       # Score threshold gate
    "EA-2": "core.pipeline.trade_quality",        # Quality filter gate
    "EA-3": "core.voters.execution_gate",         # Safety checkpoint
    "EA-4": "core.voters.risk_engine",            # Position sizing gate
}


# ─── VOTER PURITY RULES ──────────────────────────────────────────────────────
# Voters must ONLY emit VoteResult. They must NEVER:

VOTER_FORBIDDEN_BEHAVIOURS = [
    "Import ConfluenceEngine internals (weights, thresholds)",
    "Import RiskEngine or ExecutionGate",
    "Contain threshold logic tied to execution decisions",
    "Access EngineState directly (only StateSnapshot allowed)",
    "Return anything other than VoteResult",
    "Modify any external state",
]

VOTER_MODULES = {
    "core.voters.bias_voter",
    "core.voters.structure_voter",
    "core.voters.session_voter",
    "core.voters.spread_voter",
    "core.voters.volatility_voter",
}


# ─── CONFLUENCE BOUNDARY RULES ────────────────────────────────────────────────
# ConfluenceEngine must ONLY aggregate VoteResult objects + SWM.

CONFLUENCE_FORBIDDEN_BEHAVIOURS = [
    "Access EngineState directly",
    "Contain execution logic (approve/reject trades)",
    "Know about risk rules or position sizing",
    "Re-evaluate voter logic",
    "Recompute market features",
]


# ─── FEATURE ENGINE RULES ────────────────────────────────────────────────────
# FeatureEngine must ONLY compute raw market features.

FEATURE_ENGINE_FORBIDDEN_BEHAVIOURS = [
    "Apply thresholds or gates",
    "Contain directional bias logic",
    "Make trading decisions",
    "Access EngineState or FSM counters",
    "Import voter, confluence, or execution modules",
]


# ─── SNAPSHOT INTEGRITY RULES ─────────────────────────────────────────────────
# StateSnapshot is frozen after creation. No downstream modification allowed.

SNAPSHOT_RULES = [
    "Created exactly once per bar evaluation cycle",
    "Frozen (immutable) — no field modification after creation",
    "No downstream code may mutate snapshot fields",
    "All evaluation stages receive the SAME snapshot instance",
]
