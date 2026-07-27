"""
Influence Path Registry — Explicit declaration of all allowed data flow paths.

Any influence path not declared here is an architecture violation.
This registry is enforced by test_global_governance_isolation.py.

RULE: If data flows from module A to module B and affects a decision,
      that path MUST be declared here.
"""

from __future__ import annotations


# ─── ALLOWED INFLUENCE PATHS ──────────────────────────────────────────────────
# Format: "Source -> Destination"
# Only these paths may carry decision-influencing data.

ALLOWED_INFLUENCE_PATHS = {
    # Feature computation → Voter evaluation
    "FeatureEngine -> Voters",

    # State preparation → Snapshot creation
    "EngineState -> StateSnapshot",

    # Voters → Confluence aggregation
    "Voters -> ConfluenceEngine",

    # Structure scoring → Confluence (via SWM only)
    "StructureScoring -> ConfluenceEngine.SWM",

    # Confluence → Execution authorities
    "ConfluenceEngine -> ExecutionGate",
    "ConfluenceEngine -> RiskEngine",

    # Execution authorities → Trade execution
    "ExecutionGate -> RiskEngine",
    "RiskEngine -> MT5Execution",

    # Scoring engine → Trade decision (legacy pipeline)
    "ScoringEngine -> ThresholdGate",
    "TradeQuality -> QualityGate",

    # MTF → Scoring engine (score adjustment only)
    "MTF -> ScoringEngine.ScoreAdjustment",
}


# ─── FORBIDDEN INFLUENCE PATHS ────────────────────────────────────────────────
# These paths must NEVER exist. Tests enforce their absence.

FORBIDDEN_INFLUENCE_PATHS = {
    # Structure must not influence outside SWM
    "StructureScoring -> ExecutionGate",
    "StructureScoring -> RiskEngine",
    "StructureScoring -> Voters",
    "StructureScoring -> IntentBuilder",

    # Voters must not influence each other
    "BiasVoter -> StructureVoter",
    "StructureVoter -> BiasVoter",
    "SessionVoter -> SpreadVoter",

    # Diagnostics must not influence decisions
    "StrictnessBase -> ScoringEngine",
    "StrictnessBase -> ConfluenceEngine",
    "StrictnessBase -> ExecutionGate",
    "Phase5 -> AnyDecisionModule",

    # Intelligence layers must not influence decisions
    "AgreementAnalysis -> ConfluenceEngine",
    "ConflictClassification -> ConfluenceEngine",
    "WeightIntelligence -> ConfluenceEngine",
    "ABTesting -> ConfluenceEngine",
}


# ─── MODULE CLASSIFICATION ────────────────────────────────────────────────────
# Every module is classified by its role in the influence graph.

MODULE_ROLES = {
    # Signal producers (input layer)
    "core.features.engine": "signal_producer",
    "core.pipeline.structure_scoring": "signal_producer",

    # Evaluators (voter layer — emit scores only)
    "core.voters.bias_voter": "evaluator",
    "core.voters.structure_voter": "evaluator",
    "core.voters.session_voter": "evaluator",
    "core.voters.spread_voter": "evaluator",
    "core.voters.volatility_voter": "evaluator",

    # Aggregator (single decision integration point)
    "core.voters.confluence_engine": "aggregator",

    # Execution authorities (may approve/reject/size trades)
    "core.pipeline.scoring_engine": "execution_authority",
    "core.pipeline.trade_quality": "execution_authority",
    "core.voters.execution_gate": "execution_authority",
    "core.voters.risk_engine": "execution_authority",

    # Passive state (stores data, never decides)
    "core.engine_state": "passive_state",
    "core.state.snapshot": "passive_state",

    # Observational (logs, diagnostics — never influences)
    "phase5.strictness_base": "observational",
    "phase5.event_reconstructor": "observational",
    "core.voters.agreement_analysis": "observational",
    "core.voters.conflict_classification": "observational",
    "core.voters.influence_tracker": "observational",
    "core.voters.system_synthesis": "observational",
    "core.voters.weight_intelligence": "observational",
    "core.voters.ab_testing": "observational",
    "core.voters.shadow_calibration": "observational",
}


# ─── STATIC SAFETY ASSERT ─────────────────────────────────────────────────────

def assert_no_hidden_influence_paths() -> bool:
    """
    Scan imports and data flow to ensure no illegal coupling exists.

    Checks:
      1. Voter modules do not import execution authority modules
      2. Observational modules do not import aggregator/authority modules
      3. Feature engine does not import voter/confluence/execution modules

    Returns True if clean. Raises AssertionError with details on violation.
    Can be called in CI.
    """
    import importlib
    import inspect

    violations: list[str] = []

    # Rule 1: Voters must not import execution authorities or confluence internals
    voter_modules = [
        "core.voters.bias_voter",
        "core.voters.structure_voter",
        "core.voters.session_voter",
        "core.voters.spread_voter",
        "core.voters.volatility_voter",
    ]
    voter_forbidden_imports = [
        "execution_gate",
        "risk_engine",
        "scoring_engine",
        "trade_quality",
    ]

    for mod_path in voter_modules:
        try:
            mod = importlib.import_module(mod_path)
            source = inspect.getsource(mod)
            for forbidden in voter_forbidden_imports:
                if f"import {forbidden}" in source or f"from {forbidden}" in source:
                    violations.append(f"{mod_path} imports forbidden module: {forbidden}")
                # Also check relative imports
                if f".{forbidden}" in source and "import" in source:
                    # More careful check
                    for line in source.split("\n"):
                        if forbidden in line and ("import" in line or "from" in line):
                            if not line.strip().startswith("#"):
                                violations.append(f"{mod_path} imports forbidden: {line.strip()}")
                                break
        except (ImportError, OSError, TypeError):
            continue

    # Rule 2: Feature engine must not import voter/confluence/execution
    feature_forbidden = [
        "voters",
        "confluence_engine",
        "execution_gate",
        "risk_engine",
        "scoring_engine",
    ]
    try:
        mod = importlib.import_module("core.features.engine")
        source = inspect.getsource(mod)
        for forbidden in feature_forbidden:
            if forbidden in source and "import" in source.split(forbidden)[0].split("\n")[-1]:
                violations.append(f"core.features.engine imports forbidden: {forbidden}")
    except (ImportError, OSError, TypeError):
        pass

    # Rule 3: Observational modules must not import in a way that creates influence
    observational_modules = [
        "core.voters.agreement_analysis",
        "core.voters.conflict_classification",
        "core.voters.system_synthesis",
        "core.voters.weight_intelligence",
        "core.voters.ab_testing",
    ]
    obs_forbidden_writes = [
        "apply_delta(",
        "EngineState(",
    ]

    for mod_path in observational_modules:
        try:
            mod = importlib.import_module(mod_path)
            source = inspect.getsource(mod)
            for forbidden in obs_forbidden_writes:
                if forbidden in source:
                    # Check it's not just a type annotation or comment
                    for line in source.split("\n"):
                        stripped = line.strip()
                        if forbidden in stripped and not stripped.startswith("#") and not stripped.startswith('"') and not stripped.startswith("'"):
                            violations.append(f"{mod_path} contains state-mutating pattern: {forbidden}")
                            break
        except (ImportError, OSError, TypeError):
            continue

    if violations:
        raise AssertionError(
            f"Hidden influence path violation(s) detected:\n"
            + "\n".join(f"  - {v}" for v in violations)
        )

    return True
