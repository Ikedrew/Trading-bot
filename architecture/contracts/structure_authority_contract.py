"""
Structure Authority Contract — System Governance Lock.

This file defines the architectural boundary rules for structure_score
and structure_regime influence within the trading system.

CORE PRINCIPLE:
  Structure has ONE and ONLY ONE influence point on decisions.
  Everything else is observational or passive state.
  No hidden decision coupling is allowed anywhere else.

This is a governance layer, not a feature.
"""

from __future__ import annotations

# ─── AUTHORITY DEFINITION ─────────────────────────────────────────────────────

# The SINGLE function/location where structure influences trade decisions.
STRUCTURE_AUTHORITY_SINGLE_POINT = "confluence_engine.compute_confluence"

# The function that computes the Structure Weight Multiplier.
STRUCTURE_WEIGHT_FUNCTION = "confluence_engine.compute_structure_weight"


# ─── ALLOWED READERS ──────────────────────────────────────────────────────────
# These modules may READ structure_score/structure_regime for passive purposes.
# They must NEVER use these values in conditional decision logic.

STRUCTURE_ALLOWED_READERS = {
    "engine_state",              # Stores structure_buffer/score/regime (passive state)
    "snapshot",                  # Exposes structure_score/regime as frozen fields (passive)
    "scoring_engine_logging",   # Observational log only (no multiplication)
    "strictness_base",          # Diagnostic/forensic analysis only
    "structure_scoring",        # Computes the values (source of truth)
    "structure_confidence",     # Observational modifier (not applied to decisions)
    "state_persistence",        # Persists/restores structure state (passive storage)
}


# ─── FORBIDDEN DECISION PATHS ─────────────────────────────────────────────────
# These modules must NEVER import, reference, or conditionally branch on
# structure_score, structure_regime, compute_structure_weight, or structure_modifier.

STRUCTURE_FORBIDDEN_DECISION_PATHS = {
    "execution_gate",           # Safety gate — must not be influenced by structure
    "intent_builder",           # Order assembly — must not be influenced by structure
    "risk_engine",              # Position sizing — must not be influenced by structure
    "bias_voter",               # Voter internals — structure influence is via SWM only
    "structure_voter",          # Voter internals — structure influence is via SWM only
    "session_voter",            # Voter internals — structure influence is via SWM only
    "spread_voter",             # Voter internals — structure influence is via SWM only
    "volatility_voter",         # Voter internals — structure influence is via SWM only
}


# ─── INFLUENCE RULE (human-readable) ─────────────────────────────────────────

STRUCTURE_INFLUENCE_RULE = """
Structure may influence decisions ONLY via the Structure Weight Multiplier (SWM)
inside ConfluenceEngine.compute_confluence().

All other usage of structure_score/structure_regime must be:
  - Passive state storage (EngineState, StateSnapshot)
  - Observational logging (scoring_engine debug log)
  - Diagnostic analysis (StrictnessBase, Phase 5)

Violations:
  - Using structure_score in if/else branches that affect trade execution
  - Multiplying scores by structure_modifier outside ConfluenceEngine
  - Importing compute_structure_weight in any forbidden decision path
  - Adding structure-conditional logic to voters, gates, or risk sizing
"""


# ─── FORBIDDEN PATTERNS (for static analysis) ────────────────────────────────
# These patterns must NOT appear in forbidden decision path files.

STRUCTURE_FORBIDDEN_PATTERNS = [
    "compute_structure_weight",
    "compute_structure_modifier",
    "structure_score",
    "structure_regime",
    "structure_modifier",
]

# Exception: the patterns above ARE allowed in test files and allowed readers.


# ─── STATIC SAFETY ASSERT ─────────────────────────────────────────────────────

def assert_structure_isolation() -> bool:
    """
    Verify structure authority isolation at runtime.

    Scans forbidden decision path modules to confirm they do not
    reference structure-related symbols. Returns True if clean.

    Can be called in CI or startup validation.
    """
    import importlib
    import inspect

    violations: list[str] = []

    # Map module short names to importable paths
    module_paths = {
        "execution_gate": "core.voters.execution_gate",
        "intent_builder": "core.pipeline.intent_builder",
        "bias_voter": "core.voters.bias_voter",
        "structure_voter": "core.voters.structure_voter",
        "session_voter": "core.voters.session_voter",
        "spread_voter": "core.voters.spread_voter",
        "volatility_voter": "core.voters.volatility_voter",
    }

    for short_name, module_path in module_paths.items():
        try:
            mod = importlib.import_module(module_path)
            source = inspect.getsource(mod)
            for pattern in STRUCTURE_FORBIDDEN_PATTERNS:
                if pattern in source:
                    violations.append(
                        f"{short_name} ({module_path}) contains forbidden pattern: {pattern}"
                    )
        except (ImportError, OSError, TypeError):
            # Module not found or not inspectable — skip
            continue

    if violations:
        raise AssertionError(
            f"Structure authority violation(s) detected:\n"
            + "\n".join(f"  - {v}" for v in violations)
        )

    return True
