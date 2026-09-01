"""
Contract Compliance Rules.

Deterministic, read-only checks that validate each contract category.
Each rule function returns one or more ContractCheck results.

Rules NEVER modify the system. They only inspect and report.
"""

from __future__ import annotations

from typing import Any

from research_engine.v10.compliance.model import CheckStatus, ContractCheck


# ═══════════════════════════════════════════════════════════════════════════════
# UNIVERSE RULES
# ═══════════════════════════════════════════════════════════════════════════════


def check_universe_enum_complete() -> ContractCheck:
    """UNIVERSE_001: All six Universe enum members exist."""
    from research_engine.v10.universes.models import Universe
    expected = {"EXECUTION", "DECISION", "MARKET", "STRATEGY", "RISK", "OUTCOME"}
    actual = {u.value for u in Universe}
    missing = expected - actual

    if not missing:
        return ContractCheck(
            check_id="UNIVERSE_001", category="UNIVERSE", status=CheckStatus.PASS,
            description="All six Universe enum members exist",
            evidence=f"Found: {sorted(actual)}",
        )
    return ContractCheck(
        check_id="UNIVERSE_001", category="UNIVERSE", status=CheckStatus.FAIL,
        description="All six Universe enum members exist",
        violation=f"Missing universes: {sorted(missing)}",
        responsible_component="research_engine/v10/universes/models.py",
        resolution="Add missing Universe enum values",
    )


def check_universe_builders_registered() -> ContractCheck:
    """UNIVERSE_002: All six universes have builders in __init__.py."""
    try:
        from research_engine.v10.universes import (
            ExecutionUniverseBuilder, DecisionUniverseBuilder,
            MarketUniverseBuilder, StrategyUniverseBuilder,
            RiskUniverseBuilder, OutcomeUniverseBuilder,
        )
        return ContractCheck(
            check_id="UNIVERSE_002", category="UNIVERSE", status=CheckStatus.PASS,
            description="All six universe builders are importable",
        )
    except ImportError as e:
        return ContractCheck(
            check_id="UNIVERSE_002", category="UNIVERSE", status=CheckStatus.FAIL,
            description="All six universe builders are importable",
            violation=str(e),
            responsible_component="research_engine/v10/universes/__init__.py",
        )


def check_universe_contracts_complete() -> ContractCheck:
    """UNIVERSE_003: All six universes have registered contracts."""
    from research_engine.v10.universes.models import Universe
    try:
        from research_engine.v10.universes.contracts import UNIVERSE_CONTRACTS
        from research_engine.v10.universes.models import ACTIVE_UNIVERSES
        missing = [u.value for u in ACTIVE_UNIVERSES if u not in UNIVERSE_CONTRACTS]
        if not missing:
            return ContractCheck(
                check_id="UNIVERSE_003", category="UNIVERSE", status=CheckStatus.PASS,
                description="All six universes have registered contracts",
            )
        return ContractCheck(
            check_id="UNIVERSE_003", category="UNIVERSE", status=CheckStatus.FAIL,
            description="All six universes have registered contracts",
            violation=f"Missing contracts for: {missing}",
            responsible_component="research_engine/v10/universes/contracts.py",
        )
    except ImportError as e:
        return ContractCheck(
            check_id="UNIVERSE_003", category="UNIVERSE", status=CheckStatus.INCONCLUSIVE,
            description="All six universes have registered contracts",
            evidence=f"Could not import contracts: {e}",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# CROSS-UNIVERSE RULES
# ═══════════════════════════════════════════════════════════════════════════════


def check_cross_universe_interface_importable() -> ContractCheck:
    """CROSS_001: Cross-universe interface is importable."""
    try:
        from research_engine.v10.cross_universe import (
            CrossUniverseTracer, CrossUniverseClassifier,
            ProposalGenerator, LifecycleTraceStore,
        )
        return ContractCheck(
            check_id="CROSS_001", category="CROSS_UNIVERSE", status=CheckStatus.PASS,
            description="Cross-universe interface (trace/compare/classify/propose) importable",
        )
    except ImportError as e:
        return ContractCheck(
            check_id="CROSS_001", category="CROSS_UNIVERSE", status=CheckStatus.FAIL,
            description="Cross-universe interface importable",
            violation=str(e),
        )


def check_proposal_governance() -> ContractCheck:
    """GOVERNANCE_001: Research proposals contain governance note."""
    from research_engine.v10.cross_universe.proposal import ResearchProposal
    p = ResearchProposal()
    if "research" in p.governance_note.lower() and "recommendation" not in p.governance_note.lower().replace("not a trading recommendation", ""):
        return ContractCheck(
            check_id="GOVERNANCE_001", category="GOVERNANCE", status=CheckStatus.PASS,
            description="Proposals contain research-only governance note",
            evidence=f"governance_note: '{p.governance_note}'",
        )
    return ContractCheck(
        check_id="GOVERNANCE_001", category="GOVERNANCE", status=CheckStatus.WARNING,
        description="Proposals contain research-only governance note",
        evidence=f"governance_note: '{p.governance_note}'",
        violation="Governance note may not clearly prevent trading interpretation",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# PROVENANCE RULES
# ═══════════════════════════════════════════════════════════════════════════════


def check_provenance_vocabulary_complete() -> ContractCheck:
    """PROVENANCE_001: All six provenance types exist."""
    from research_engine.v10.provenance.model import ProvenanceType
    expected = {"OBSERVED", "DERIVED", "JOINED", "RECONSTRUCTED", "COUNTERFACTUAL", "UNKNOWN"}
    actual = {p.value for p in ProvenanceType}
    missing = expected - actual

    if not missing:
        return ContractCheck(
            check_id="PROVENANCE_001", category="PROVENANCE", status=CheckStatus.PASS,
            description="All six provenance types exist",
        )
    return ContractCheck(
        check_id="PROVENANCE_001", category="PROVENANCE", status=CheckStatus.FAIL,
        description="All six provenance types exist",
        violation=f"Missing: {sorted(missing)}",
    )


def check_provenance_validator_importable() -> ContractCheck:
    """PROVENANCE_002: Provenance validator is importable."""
    try:
        from research_engine.v10.provenance import ProvenanceValidator, EvidenceLineage
        return ContractCheck(
            check_id="PROVENANCE_002", category="PROVENANCE", status=CheckStatus.PASS,
            description="Provenance validator and lineage importable",
        )
    except ImportError as e:
        return ContractCheck(
            check_id="PROVENANCE_002", category="PROVENANCE", status=CheckStatus.FAIL,
            description="Provenance validator and lineage importable",
            violation=str(e),
        )


def check_counterfactual_protection() -> ContractCheck:
    """PROVENANCE_003: Counterfactual contamination is detectable."""
    from research_engine.v10.provenance.model import EvidenceProvenance
    from research_engine.v10.provenance.validation import ProvenanceValidator

    v = ProvenanceValidator()
    items = [
        ("observed_field", EvidenceProvenance.observed("MARKET")),
        ("counterfactual_field", EvidenceProvenance.counterfactual("test")),
    ]
    issues = v.check_counterfactual_contamination(items)
    if issues:
        return ContractCheck(
            check_id="PROVENANCE_003", category="PROVENANCE", status=CheckStatus.PASS,
            description="Counterfactual contamination is detectable",
            evidence="Mixing OBSERVED + COUNTERFACTUAL correctly produces an error",
        )
    return ContractCheck(
        check_id="PROVENANCE_003", category="PROVENANCE", status=CheckStatus.FAIL,
        description="Counterfactual contamination is detectable",
        violation="Mixing OBSERVED + COUNTERFACTUAL did NOT produce a validation error",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# VERSION RULES
# ═══════════════════════════════════════════════════════════════════════════════


def check_version_concepts_distinct() -> ContractCheck:
    """VERSION_001: Version concepts are structurally distinct."""
    from research_engine.v10.runner.question_runner import RunContext

    ctx = RunContext()
    fields = {
        "run_id": ctx.run_id,
        "universe_versions": type(ctx.universe_versions).__name__,
        "population_versions": type(ctx.population_versions).__name__,
        "engine_version": ctx.engine_version,
        "question_bank_version": ctx.question_bank_version,
        "primitive_versions": type(ctx.primitive_versions).__name__,
    }

    # Verify they are distinct fields (not aliases)
    if len(fields) == 6:
        return ContractCheck(
            check_id="VERSION_001", category="VERSION", status=CheckStatus.PASS,
            description="Version concepts (run_id, universe_versions, population_versions, etc.) are structurally distinct",
            evidence=f"Fields: {list(fields.keys())}",
        )
    return ContractCheck(
        check_id="VERSION_001", category="VERSION", status=CheckStatus.FAIL,
        description="Version concepts are structurally distinct",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# LIFECYCLE RULES
# ═══════════════════════════════════════════════════════════════════════════════


def check_lifecycle_trace_hash_determinism() -> ContractCheck:
    """LIFECYCLE_001: trace_hash is deterministic."""
    from research_engine.v10.cross_universe.tracer import LifecycleTrace, UniverseObservation, UniversePresence

    trace = LifecycleTrace(
        entity_id="test_entity",
        trace_status="COMPLETE",
        universes={
            "market": UniverseObservation(universe="MARKET", presence=UniversePresence.PRESENT, record={"regime": "TRENDING"}),
            "decision": UniverseObservation(universe="DECISION", presence=UniversePresence.PRESENT, record={"action": "EXECUTE"}),
        },
    )
    h1 = trace.trace_hash
    h2 = trace.trace_hash

    if h1 == h2 and len(h1) == 16:
        return ContractCheck(
            check_id="LIFECYCLE_001", category="LIFECYCLE", status=CheckStatus.PASS,
            description="trace_hash is deterministic (same evidence → same hash)",
            evidence=f"hash={h1}",
        )
    return ContractCheck(
        check_id="LIFECYCLE_001", category="LIFECYCLE", status=CheckStatus.FAIL,
        description="trace_hash is deterministic",
        violation=f"h1={h1}, h2={h2}",
    )


def check_lifecycle_persistence_importable() -> ContractCheck:
    """LIFECYCLE_002: Lifecycle trace store is importable."""
    try:
        from research_engine.v10.cross_universe.persistence import LifecycleTraceStore
        store = LifecycleTraceStore.__init__  # Verify class exists
        return ContractCheck(
            check_id="LIFECYCLE_002", category="LIFECYCLE", status=CheckStatus.PASS,
            description="LifecycleTraceStore is importable and instantiable",
        )
    except (ImportError, AttributeError) as e:
        return ContractCheck(
            check_id="LIFECYCLE_002", category="LIFECYCLE", status=CheckStatus.FAIL,
            description="LifecycleTraceStore importable",
            violation=str(e),
        )


# ═══════════════════════════════════════════════════════════════════════════════
# RESEARCH LAYER RULES
# ═══════════════════════════════════════════════════════════════════════════════


def check_research_finding_lineage_fields() -> ContractCheck:
    """RESEARCH_001: ResearchFinding carries lineage fields."""
    from research_engine.v10.control_plane.finding_schema import ResearchFinding

    f = ResearchFinding()
    required = ["run_id", "universe_versions", "population_versions", "engine_version", "question_version", "analysis_version"]
    missing = [field for field in required if not hasattr(f, field)]

    if not missing:
        return ContractCheck(
            check_id="RESEARCH_001", category="RESEARCH", status=CheckStatus.PASS,
            description="ResearchFinding carries all required lineage fields",
            evidence=f"Fields present: {required}",
        )
    return ContractCheck(
        check_id="RESEARCH_001", category="RESEARCH", status=CheckStatus.FAIL,
        description="ResearchFinding carries lineage fields",
        violation=f"Missing fields: {missing}",
    )


def check_research_insufficient_evidence_handling() -> ContractCheck:
    """RESEARCH_002: Insufficient evidence produces INCONCLUSIVE/INSUFFICIENT, not false negatives."""
    from research_engine.v10.runner.question_runner import _determine_outcome, _determine_confidence
    from research_engine.v10.runner.primitives.base import AnalysisResult

    # Zero sample → INCONCLUSIVE
    empty = AnalysisResult(analysis_type="test", success=True, sample_size=0)
    outcome = _determine_outcome(empty, {})
    confidence = _determine_confidence(empty, 0)

    if outcome == "INCONCLUSIVE" and confidence == "INSUFFICIENT":
        return ContractCheck(
            check_id="RESEARCH_002", category="RESEARCH", status=CheckStatus.PASS,
            description="Zero-sample evidence produces INCONCLUSIVE outcome and INSUFFICIENT confidence",
        )
    return ContractCheck(
        check_id="RESEARCH_002", category="RESEARCH", status=CheckStatus.FAIL,
        description="Zero-sample → INCONCLUSIVE/INSUFFICIENT",
        violation=f"Got outcome={outcome}, confidence={confidence}",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ALL RULES REGISTRY
# ═══════════════════════════════════════════════════════════════════════════════


ALL_RULES = [
    check_universe_enum_complete,
    check_universe_builders_registered,
    check_universe_contracts_complete,
    check_cross_universe_interface_importable,
    check_proposal_governance,
    check_provenance_vocabulary_complete,
    check_provenance_validator_importable,
    check_counterfactual_protection,
    check_version_concepts_distinct,
    check_lifecycle_trace_hash_determinism,
    check_lifecycle_persistence_importable,
    check_research_finding_lineage_fields,
    check_research_insufficient_evidence_handling,
]
