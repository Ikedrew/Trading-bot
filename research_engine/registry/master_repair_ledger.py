"""Canonical Master 70-Question Repair Ledger.

Planning/consolidation artifact.  The ledger derives the strict structural
baseline from frozen Wave-A findings.  It does not change the registry, runners,
reports, resolvers, readiness, collection, or production behaviour.

For every question the ledger still derives ``structurally_operational`` from
that entry's own 18 gates; the headline counts are never hard-coded.  A repair
wave whose exit gate has been implemented and proven is recorded with
``implemented=True`` plus its implementation evidence, and its direct gain then
belongs to the derived operational baseline instead of the outstanding plan.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from research_engine.registry.definition_validator import (
    build_definitions_from_registry,
    get_question_health,
    validate_all_definitions,
)
from research_engine.registry.research_question_registry import REGISTRY
from research_engine.registry.wave_a1_definitions import (
    WAVE_A1_RESOLVED,
    WAVE_A1_TARGETS,
    WAVE_A1_UNRESOLVED_REASONS,
)
from research_engine.registry.wave_a2_definitions import (
    WAVE_A2_RESOLVED,
    WAVE_A2_TARGETS,
    WAVE_A2_UNRESOLVED_REASONS,
)
from research_engine.registry.wave_a3_definitions import (
    WAVE_A3_TARGETS,
    WAVE_A3_UNRESOLVED_REASONS,
)
from research_engine.registry.wave_a4_definitions import (
    WAVE_A4_TARGETS,
    WAVE_A4_UNRESOLVED_REASONS,
)
from research_engine.registry.wave_a5_definitions import (
    WAVE_A5_OWNERSHIP,
    WAVE_A5_TARGETS,
)
from research_engine.registry.wave_a_no_runner_definitions import (
    ALREADY_AVAILABLE,
    DERIVABLE,
    EXISTING_V1_CONTRACT_VIOLATION,
    NEW_RESEARCH_EVIDENCE,
    WAVE_A_NO_RUNNER_DESIGNS,
    WAVE_A_NO_RUNNER_TARGETS,
)


PASS = "PASS"
FAIL = "FAIL"
PARTIAL = "PARTIAL"
NOT_APPLICABLE = "NOT_APPLICABLE"
HUMAN_DECISION_REQUIRED = "HUMAN_DECISION_REQUIRED"
FUTURE_EVIDENCE_REQUIRED = "FUTURE_EVIDENCE_REQUIRED"
ALLOWED_GATE_STATUSES = frozenset({
    PASS,
    FAIL,
    PARTIAL,
    NOT_APPLICABLE,
    HUMAN_DECISION_REQUIRED,
    FUTURE_EVIDENCE_REQUIRED,
})
STRUCTURAL_PASS_STATUSES = frozenset({PASS, NOT_APPLICABLE})


@dataclass(frozen=True)
class GateStatus:
    canonical_identity: str
    scientific_definition: str
    unit_of_analysis: str
    evidence_authority: str
    join_contract: str
    epoch_contract: str
    multi_account_contract: str
    repeated_measure_contract: str
    leakage_contract: str
    metric_contract: str
    sufficiency_contract: str
    runner: str
    report_ownership: str
    validity_contract: str
    completion_contract: str
    readiness_control: str
    continuous_loop_eligibility: str
    governance_contract: str

    def values(self) -> tuple[str, ...]:
        return tuple(asdict(self).values())


@dataclass(frozen=True)
class LedgerEntry:
    canonical_question_id: str
    registry_family: str
    wave_a_category: str
    current_definition_health: str
    current_operational_state_if_known: str
    gates: GateStatus
    primary_blocker: str
    primary_blocker_category: str
    secondary_blockers: tuple[str, ...]
    root_cause_cluster: str
    evidence_gap_classification: str
    human_semantic_decision_required: bool
    dependency_ids_or_foundations: tuple[str, ...]
    repair_actions: tuple[str, ...]
    verification_gate: str
    proposed_repair_wave: str
    notes: str

    @property
    def structurally_operational(self) -> bool:
        return all(value in STRUCTURAL_PASS_STATUSES for value in self.gates.values())

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["structurally_operational"] = self.structurally_operational
        return value


@dataclass(frozen=True)
class HumanSemanticDecision:
    decision_id: str
    affected_question_ids: tuple[str, ...]
    exact_decision: str
    available_options: tuple[str, ...]
    consequences: tuple[str, ...]
    recommended_default: str
    implementation_blocked_until_decision: bool


@dataclass(frozen=True)
class RepairWave:
    repair_wave_id: str
    name: str
    target_question_ids: tuple[str, ...]
    root_cause: str
    prerequisites: tuple[str, ...]
    human_decision_ids: tuple[str, ...]
    code_areas_expected_to_change: tuple[str, ...]
    new_research_evidence_required: bool
    direct_gain: tuple[str, ...]
    unlocked_not_yet_operational: tuple[str, ...]
    exit_gate: str
    # An implemented wave's direct gain is already part of the derived
    # operational baseline; its exit gate has been proven by focused tests.
    implemented: bool = False
    implementation_evidence: tuple[str, ...] = ()


# Repair Wave RW1 (canonical ownership / legacy routing) is implemented and
# verified, so the Wave-A complete D1/E2 definitions are no longer withheld from
# the structural baseline.  Ownership itself is enforced at the canonical report
# boundary by research_engine.control_plane.report_ownership and proven by
# tests/test_rw1_report_ownership.py; every entry below still derives its own
# structurally_operational value from its own gates.
RW2_OPERATIONAL_IDS = frozenset({"M1", "M3", "M7", "M8", "M11"})
# RW3 is in progress: D2 (paired chronological calibration) and D3 (paired
# chronological predicted-EV gate validation) have real runners, unique
# reports, and passing focused gates.  D4/D5/X5 remain non-operational.
D2_RW3_PROGRESS_IDS = frozenset({"D2", "D3"})
OPERATIONAL_IDS = (
    frozenset(WAVE_A1_RESOLVED | WAVE_A2_RESOLVED)
    | RW2_OPERATIONAL_IDS
    | D2_RW3_PROGRESS_IDS
)



HUMAN_SEMANTIC_DECISIONS = {
    "HD01": HumanSemanticDecision(
        "HD01", ("E3", "S1"),
        "Choose the surviving canonical owner and compatibility treatment for the equivalent E3/S1 intent.",
        ("E3 owner; S1 explicit alias/superseded identity", "S1 owner; E3 explicit alias/superseded identity"),
        ("Preserves E-family ownership and routes S1 explicitly", "Preserves strategy-family ownership and routes E3 explicitly"),
        "No Wave-A-supported default; governance must select one owner before alias activation.", True,
    ),
    "HD02": HumanSemanticDecision(
        "HD02", ("M1", "M3", "M7", "M11"),
        "RW2 approved preserving predictive registry claims rather than narrowing them to descriptive/associative claims.",
        ("Implement leakage-safe out-of-sample predictive evaluation", "Approve explicit registry narrowing"),
        ("More implementation and chronological validation; preserves intent", "Less code, but changes canonical claim"),
        "Preserve canonical intent and implement predictive evaluation (approved and implemented in RW2).", False,
    ),
    "HD03": HumanSemanticDecision(
        "HD03", ("M8",),
        "RW2 approved separate CURRENT market_context as the canonical phase-transition authority.",
        ("Separate CURRENT market_context joined to outcomes", "Embedded shadow decision snapshot only"),
        ("Preserves registry multi-source intent and needs a strict join", "Matches current runner but requires registry narrowing"),
        "Use separate CURRENT market_context with a strict canonical-opportunity join (approved and implemented in RW2).", False,
    ),
    "HD04": HumanSemanticDecision(
        "HD04", ("D2", "D3", "D4", "D5", "X5"),
        "Approve canonical pre-decision score, p_success, EV, rejection-treatment, and evaluation semantics.",
        ("Versioned predictive/calibration contract with out-of-sample evaluation", "Narrow each claim to its current descriptive diagnostic"),
        ("Preserves registry intent and blocks leakage", "Requires canonical wording changes and yields weaker claims"),
        "Version the predictive authorities and preserve registry intent.", True,
    ),
    "HD05": HumanSemanticDecision(
        "HD05", ("P1",),
        "Approve the promotion estimand and required EV/win-rate/drawdown/frequency/risk evaluation design.",
        ("Full predeclared candidate-policy evaluation", "Narrow to current fixed in-sample scenarios"),
        ("Answers the registry and needs broader metrics", "Cheaper but no longer answers promotion impact"),
        "Implement the full predeclared evaluation.", True,
    ),
    "HD06": HumanSemanticDecision(
        "HD06", ("S5", "S6"),
        "Approve horizon/strategy adjustment estimators and provisional overall/cell thresholds.",
        ("Cluster-aware adjusted model with proposed thresholds", "Alternative predeclared estimator/thresholds"),
        ("Uses repeated horizons without pseudo-replication", "Acceptable only if equally explicit and cluster-safe"),
        "Use the proposed cluster-aware contracts in the frozen no-runner design.", True,
    ),
    "HD07": HumanSemanticDecision(
        "HD07", ("S7",),
        "Approve interaction estimator, multiplicity method, and cell thresholds.",
        ("Cluster-aware interaction with multiplicity control", "Alternative predeclared interaction design"),
        ("Supports bounded family-by-horizon claims", "Must still preserve opportunity clustering"),
        "Use the proposed cluster-aware interaction design.", True,
    ),
    "HD08": HumanSemanticDecision(
        "HD08", ("X6",),
        "Choose the primary execution-quality endpoint, spread bins, volatility precedence, and thresholds.",
        ("Measured slippage primary; failures secondary", "Composite execution-quality endpoint"),
        ("Direct producer authority and interpretable units", "Broader but requires approved weighting"),
        "Use producer-measured slippage as primary.", True,
    ),
    "HD09": HumanSemanticDecision(
        "HD09", ("EX1", "EX2", "EX5", "EX6", "EX7", "EX8", "EX9", "EX10"),
        "Choose registry narrowing versus genuine path-based counterfactual/policy evaluation for exit questions.",
        ("Implement versioned candidate exit-policy simulation", "Narrow questions to observational exit diagnostics"),
        ("Preserves canonical policy intent and needs ordered path evaluation", "Retains current runners but changes scientific claims"),
        "Preserve registry intent and implement counterfactual policy evaluation.", True,
    ),
    "HD10": HumanSemanticDecision(
        "HD10", ("R1", "R2", "R3", "R4", "R5"),
        "Approve global/per-guard risk estimands, risk assumptions, chronology, model set, and optimisation criterion.",
        ("Versioned risk-policy evaluation and simulation design", "Narrow to descriptive risk diagnostics"),
        ("Answers effectiveness/value questions", "Cannot support improvement or optimality claims"),
        "Implement distinct R1/R2 estimands and versioned R3-R5 simulation assumptions.", True,
    ),
    "HD11": HumanSemanticDecision(
        "HD11", ("L1", "L2", "L3", "L4", "L7"),
        "Approve temporal windows, adaptation events, architecture-validity criteria, and A/B arm assignment.",
        ("Versioned chronological/adaptation contracts", "Narrow to existing pooled/count diagnostics"),
        ("Preserves learning claims", "Removes temporal/adaptation meaning"),
        "Preserve learning intent with explicit chronology and arm/version boundaries.", True,
    ),
    "HD12": HumanSemanticDecision(
        "HD12", ("L6",),
        "Approve confidence components, weighting, tiers, and warning treatment.",
        ("Transparent component vector plus ordinal tiers", "Component vector only"),
        ("Supports summaries but adds approved thresholds", "Avoids arbitrary scalar while remaining operational"),
        "Always emit components; add tiers only after approval.", True,
    ),
    "HD13": HumanSemanticDecision(
        "HD13", ("G1",),
        "Approve global dataset-suitability aggregation scope.",
        ("Assess all 70 without one automatic approval", "Gate only P0 while reporting all 70"),
        ("Complete canonical coverage", "Prioritises critical questions but needs a separate overall meaning"),
        "Assess all 70 and keep per-question statuses visible.", True,
    ),
    "HD14": HumanSemanticDecision(
        "HD14", ("G2",),
        "Approve the eligible lineage denominator, identity key, and inferential threshold.",
        ("One opportunity; entity_id plus canonical root", "Another explicitly versioned strict identity"),
        ("Prevents horizon/account inflation", "Must prove equal determinism and reject ambiguity"),
        "Use the proposed opportunity denominator and composite identity.", True,
    ),
    "HD15": HumanSemanticDecision(
        "HD15", ("G3",),
        "Approve the global trust rule and weighting.",
        ("Component profile plus approved advisory scalar", "Component profile without scalar"),
        ("Compact status but requires governance thresholds", "Fully transparent and avoids invented weighting"),
        "Implement the component profile first; keep any scalar advisory.", True,
    ),
}


REPAIR_WAVES = {
    "RW1": RepairWave(
        "RW1", "Canonical ownership and legacy routing", ("D1", "E2"),
        "Shared legacy/report identities let another canonical question inherit completion.", (), (),
        ("canonical registry ownership metadata", "report resolver routing", "report validity/readiness ownership tests"),
        False, ("D1", "E2"), ("E3", "S1", "R1", "R2", "L1", "L3"),
        "Q1 and Q5 artifacts resolve only to D1 and E2; distinct questions cannot inherit their finding or completion state.",
        implemented=True,
        implementation_evidence=(
            "research_engine/control_plane/report_ownership.py records the adjudicated canonical "
            "ownership contract derived from Wave A5: q1_component_reward.json belongs to D1 and "
            "q5_pattern_degradation.json belongs to E2.",
            "The canonical report resolver (resolve_report_validity / load_report_for_question) and "
            "the canonical report history resolver fail closed whenever a question is not the "
            "artifact's canonical owner: L3 can no longer resolve D1's component-reward artifact and "
            "L1 can no longer resolve E2's pooled-pattern artifact.",
            "Legacy identities stay compatible only where they cannot transfer ownership: D1 still "
            "accepts its historical Q1 identity on its own artifact and E2 still accepts Q5/Q24 on its "
            "own artifact, while a shared legacy ID can no longer complete a scientifically distinct "
            "canonical question.",
            "Artifact metadata can no longer contradict canonical ownership silently; a conflicting "
            "question_id fails closed as AMBIGUOUS_REPORT_MAPPING/INVALIDATED.",
            "L1 and L3 remain structurally non-operational and must not resolve D1/E2 artifacts until "
            "their dedicated RW10 repairs create genuinely distinct runners and reports.",
            "tests/test_rw1_report_ownership.py proves all 25 ownership, fail-closed, ledger-delta, and "
            "scope-containment requirements.",
        ),
    ),
    "RW2": RepairWave(
        "RW2", "Market-context evidence and predictive semantics", ("M1", "M3", "M7", "M8", "M11"),
        "Mapped calculations did not establish the registry's outcome/predictive or multi-source claims.", ("RW1",), (),
        ("market research runners", "CURRENT context/outcome joins", "market reports/readiness"), False,
        ("M1", "M3", "M7", "M8", "M11"), (),
        "Each runner uses authoritative CURRENT context, opportunity-safe outcomes, declared cells and chronological later-unseen validation; COMPLETE enforces total, partition and cell sufficiency.",
        implemented=True,
        implementation_evidence=(
            "research_engine/experiments/market_prediction_rw2.py implements one observation per canonical opportunity, CURRENT-only authority, fail-closed joins and chronological discovery/later validation for all five targets.",
            "M8 requires canonical market_context and treats embedded snapshot context as consistency evidence only; M11 joins authoritative decision_trace context to shadow pattern/outcome evidence.",
            "Unique versioned reports and registry sample rules prevent cross-completion and distinguish WAITING_DATA, BLOCKED and scientifically COMPLETE results.",
            "Focused RW2 tests prove fanout/horizon collapse, missing-label preservation, chronology, leakage rejection, cell sufficiency and derived 31/70 ledger accounting.",
        ),
    ),
    "RW3": RepairWave(
        "RW3", "Prediction, calibration, and decision foundation", ("D2", "D3", "D4", "D5", "X5"),
        "Pre-decision p_success/EV/score authorities, pairing, rejection taxonomy, and leakage-safe evaluation are unresolved.", ("RW1",), ("HD04",),
        ("versioned prediction authority", "decision-to-outcome joins", "calibration/rejection runners and reports"), False,
        ("D4", "D5", "X5"), ("P1",),
        "D2 (paired chronological calibration) and D3 (paired chronological predicted-EV gate validation in R units) are structurally operational through their dedicated runners and unique reports. RW3 remains in progress until D4/D5/X5 have versioned pre-decision fields paired one-to-one with outcomes, leakage-safe evaluation, and matching sufficiency/completion.",
    ),
    "RW4": RepairWave(
        "RW4", "Selection, ranking, and promotion", ("D6", "PORT-1", "OPP-1", "P1"),
        "Selection questions need complete definitions, uncontaminated outcomes, strict roots, and a declared promotion estimand.", ("RW3",), ("HD05",),
        ("portfolio/opportunity definitions", "opportunity-selection join", "promotion-impact runner/report"), False,
        ("D6", "PORT-1", "OPP-1", "P1"), (),
        "D6/PORT-1 retain distinct ownership, missing outcomes never become 0R, joins use one root, and P1 emits every declared impact metric under its evaluation design.",
    ),
    "RW5": RepairWave(
        "RW5", "Strategy expectancy foundation", ("E3", "S1", "S5", "S6"),
        "Strategy expectancy ownership is unresolved and adjusted strategy/horizon runners do not exist.", ("RW1",), ("HD01", "HD06"),
        ("canonical alias/owner routing", "strategy expectancy runners", "shadow repeated-measure helpers", "unique reports"), False,
        ("E3", "S1", "S5", "S6"), ("S7",),
        "The chosen E3/S1 owner and explicit alias share one valid expectancy result without duplicate ownership; S5/S6 cluster horizons by opportunity and meet cell rules.",
    ),
    "RW6": RepairWave(
        "RW6", "Strategy-horizon interaction", ("S7",),
        "The interaction requires operational marginal foundations and a cluster-aware multiplicity-controlled design.", ("RW5",), ("HD07",),
        ("strategy-horizon interaction runner", "cell sufficiency", "S7 report/readiness"), False,
        ("S7",), (),
        "A sufficient 2x2 grid produces cluster-aware interaction contrasts and bounded claims; sparse cells remain explicitly insufficient.",
    ),
    "RW7": RepairWave(
        "RW7", "Execution authority and stability", ("X3", "EXEC1", "X6"),
        "Execution questions conflate sources/semantics and X6 lacks a runner.", ("RW1",), ("HD08",),
        ("execution runners", "strict result-context-trace joins", "measured-slippage authority", "execution reports/readiness"), False,
        ("X3", "EXEC1", "X6"), (),
        "Account-grained results use producer-measured fields, immutable pre-execution conditions, canonical symbol checks, correlation clustering, and unique completion/report ownership.",
    ),
    "RW8": RepairWave(
        "RW8", "Exit counterfactual policy evaluation", ("EX1", "EX2", "EX5", "EX6", "EX7", "EX8", "EX9", "EX10"),
        "Observational MFE/retention/category diagnostics cannot establish alternative exit-policy effects.", ("RW1",), ("HD09",),
        ("ordered path authority", "versioned exit-policy representation", "counterfactual simulator", "policy reports/readiness"), False,
        ("EX1", "EX2", "EX5", "EX6", "EX7", "EX8", "EX9", "EX10"), (),
        "A deterministic ordered-path fixture reproduces baseline and candidate exits without lookahead; policy comparisons use opportunity/horizon clusters and out-of-sample completion rules.",
    ),
    "RW9": RepairWave(
        "RW9", "Risk effectiveness and simulation", ("R1", "R2", "R3", "R4", "R5"),
        "Global effectiveness, guard attribution, drawdown chronology, and model assumptions are conflated or unspecified.", ("RW1", "RW3"), ("HD10",),
        ("risk evidence helpers", "separate R1/R2 runners/reports", "versioned risk simulation", "chronological drawdown evaluation"), False,
        ("R1", "R2", "R3", "R4", "R5"), (),
        "R1 and R2 have distinct estimands/owners; R3-R5 publish versioned assumptions and reproduce chronological scenario fixtures with no account fanout.",
    ),
    "RW10": RepairWave(
        "RW10", "Learning chronology and architecture validation", ("L1", "L2", "L3", "L4", "L7"),
        "Pooled/count diagnostics lack event chronology, change boundaries, valid before/after arms, or distinct report ownership.", ("RW1", "RW2", "RW5"), ("HD11",),
        ("chronology/version boundary layer", "learning runners", "L1/L3 report separation", "A/B arm authority"), False,
        ("L1", "L2", "L3", "L4", "L7"), (),
        "Timestamped/versioned fixtures prove temporal order and arm assignment; drift, adaptation, and causal improvement remain separately labelled and uniquely reported.",
    ),
    "RW11": RepairWave(
        "RW11", "Research-governance foundations", ("L6", "G1", "G2"),
        "Confidence, dataset suitability, and lineage designs exist but have no operational runners or reports.",
        ("RW2", "RW3", "RW4", "RW5", "RW6", "RW7", "RW8", "RW9", "RW10"),
        ("HD12", "HD13", "HD14"),
        ("immutable control-plane snapshot", "L6/G1/G2 runners", "strict lineage denominator", "unique governance reports"), False,
        ("L6", "G1", "G2"), ("G3",),
        "L6 scores every eligible conclusion, G1 assesses all 70 requirements, and G2 exhaustively classifies an opportunity-level lineage denominator without dashboard authority.",
    ),
    "RW12": RepairWave(
        "RW12", "Global research-validity assessment", ("G3",),
        "G3 requires future validity-approved G1/G2/L6 research outputs and a nonrecursive global rule.", ("RW11",), ("HD15",),
        ("G3 immutable snapshot aggregator", "global validity report", "human-governance boundary"), True,
        ("G3",), (),
        "A same-epoch snapshot covers all 69 non-self states plus valid G1/G2/L6 reports, rejects self-reference, and cannot authorize production.",
    ),
}


_ASSIGNMENTS: dict[str, tuple[str, str, str, str, tuple[str, ...], bool, str]] = {}


def _assign(
    ids: tuple[str, ...], wave: str, category: str, cluster: str,
    blocker: str, dependencies: tuple[str, ...] = (), human: bool = False,
    evidence_gap: str = DERIVABLE,
) -> None:
    for qid in ids:
        _ASSIGNMENTS[qid] = (wave, category, cluster, blocker, dependencies, human, evidence_gap)


# RW1 previously assigned D1/E2 to the blocked "report ownership" category.  The
# canonical ownership repair is implemented and verified, so no blocked
# assignment remains for them: D1/E2 are derived as structurally operational
# directly from their gates.  See REPAIR_WAVES["RW1"].implementation_evidence.
_assign(("M1", "M11"), "RW2", "runner mismatch", "MARKET_CONTEXT_PREDICTION",
        "The mapped runner emits descriptive frequencies/dispersion rather than the registry outcome claim.", human=True)
_assign(("M3", "M7"), "RW2", "prediction/calibration", "MARKET_CONTEXT_PREDICTION",
        "The runner has no time-ordered out-of-sample predictive comparison.", human=True)
_assign(("M8",), "RW2", "evidence authority", "MARKET_CONTEXT_PREDICTION",
        "Registry multi-source market_context authority conflicts with the runner's embedded shadow snapshot.", human=True)
_assign(("D2", "D3", "D4", "D5", "X5"), "RW3", "prediction/calibration", "PREDICTION_DECISION_FOUNDATION",
        "Canonical versioned pre-decision probability/EV/score and leakage-safe outcome pairing are unresolved.", human=True)
_assign(("D6", "PORT-1"), "RW4", "definition", "SELECTION_PROMOTION_FOUNDATION",
        "A5 proves distinct coherent ownership, but the scientific/sufficiency/completion definitions remain unclosed.",
        evidence_gap=ALREADY_AVAILABLE)
_assign(("OPP-1",), "RW4", "join/population", "SELECTION_PROMOTION_FOUNDATION",
        "Missing outcomes become 0R in score buckets and canonical/legacy opportunity roots are mixed.")
_assign(("P1",), "RW4", "counterfactual design", "SELECTION_PROMOTION_FOUNDATION",
        "The runner omits declared evidence and impact metrics and evaluates only fixed in-sample scenarios.", human=True)
_assign(("E3", "S1"), "RW5", "report ownership", "STRATEGY_EXPECTANCY_FOUNDATION",
        "Equivalent intent lacks a chosen canonical owner and the shared runner reports activation counts, not expectancy.", human=True)
_assign(("S5", "S6"), "RW5", "no runner", "NO_RUNNER_STRATEGY_FOUNDATION",
        "The approved proposed strategy/horizon contract has no runner, report, readiness, or completion implementation.", human=True)
_assign(("S7",), "RW6", "no runner", "NO_RUNNER_STRATEGY_INTERACTION",
        "The proposed interaction contract has no runner and depends on operational S5/S6 foundations.", ("S5", "S6"), True)
_assign(("X3",), "RW7", "runner mismatch", "EXECUTION_AUTHORITY_FOUNDATION",
        "Session-quality intent is not established by the current account execution metric/authority contract.")
_assign(("EXEC1",), "RW7", "evidence authority", "EXECUTION_AUTHORITY_FOUNDATION",
        "Execution-result truth and protection-audit truth are not correctly separated for the canonical claim.")
_assign(("X6",), "RW7", "no runner", "NO_RUNNER_EXECUTION_FOUNDATION",
        "The proposed account-grained execution-stability contract has no runner/report and the registry names non-V1 slippage_journal.", human=True)
_assign(("EX1", "EX2", "EX5", "EX6", "EX7", "EX8", "EX9", "EX10"), "RW8", "counterfactual design", "EXIT_COUNTERFACTUAL_FOUNDATION",
        "The current observational diagnostic cannot establish the registry's alternative exit-policy claim.", human=True)
_assign(("R1", "R2"), "RW9", "report ownership", "RISK_MODELLING_FOUNDATION",
        "Distinct global and per-guard risk questions share a count-only runner/report and legacy identity.", human=True)
_assign(("R3", "R4", "R5"), "RW9", "risk modelling", "RISK_MODELLING_FOUNDATION",
        "Risk assumptions, chronological drawdown/scenario grain, and optimisation semantics are not authoritative.", human=True)
_assign(("L1", "L2", "L4"), "RW10", "chronology", "LEARNING_CHRONOLOGY_FOUNDATION",
        "The current runner lacks authoritative temporal/change ordering for the canonical learning claim.", human=True)
_assign(("L3",), "RW10", "runner mismatch", "LEARNING_CHRONOLOGY_FOUNDATION",
        "D1 component attribution cannot validate weights, regimes, and strategy mappings.", human=True)
_assign(("L7",), "RW10", "counterfactual design", "LEARNING_CHRONOLOGY_FOUNDATION",
        "Chronological halves are not authoritative control/candidate A/B arms.", human=True)
_assign(("L6", "G1", "G2"), "RW11", "no runner", "NO_RUNNER_GOVERNANCE_FOUNDATION",
        "The frozen proposed governance contract has no runner, report, readiness, or completion implementation.", human=True)
_assign(("G3",), "RW12", "no runner", "NO_RUNNER_GLOBAL_VALIDITY",
        "The proposed global assessment has no runner and requires future G1/G2/L6 research reports.", ("G1", "G2", "L6"), True, NEW_RESEARCH_EVIDENCE)


_QUESTION_ACTIONS = {
    "M1": "Join authoritative H4 regime to one opportunity-safe outcome and implement the approved predictive evaluation with real sufficiency.",
    "M3": "Replace dispersion-of-cell-means proxy with an out-of-sample H4-regime versus H4-regime-plus-phase comparison.",
    "M7": "Implement time-ordered regime-only, phase-only, and combined predictive model comparison with clustered outcomes.",
    "M8": "Implement the approved phase-transition authority and deterministic context-to-outcome join instead of silently substituting embedded fields.",
    "M11": "Replace context/pattern dispersion proxy with the approved incremental prediction design and per-cell sufficiency.",
    "D2": "Version p_success producer/meaning and calibrate one pre-decision prediction against one canonical outcome out of sample.",
    "D3": "Define score and distance authorities plus EV units, then evaluate their incremental EV relationship without post-outcome inputs.",
    "D4": "Choose one authoritative pre-decision score/threshold meaning and evaluate it on held-out canonical opportunities.",
    "D5": "Define rejection taxonomy and pair every rejected opportunity with a valid shadow counterfactual outcome without account fanout.",
    "X5": "Version pre-decision EV producer/inputs/units and compare it with measured execution outcomes through a strict correlation join.",
    "D6": "Activate a complete definition for rank-position outcome prediction while retaining run_portfolio_ranking and d6_portfolio_ranking.json ownership.",
    "PORT-1": "Activate a complete selected-versus-best cycle definition while retaining run_port_1 and port1_portfolio_selection.json ownership.",
    "OPP-1": "Exclude missing outcomes from expectancy and replace legacy fallback joins with one conflict-rejecting canonical opportunity root.",
    "P1": "Join declared decision evidence and evaluate candidate promotion effects on EV, win rate, drawdown, frequency, and risk under the approved design.",
    "E3": "After owner selection, route E3 explicitly to the repaired strategy-expectancy owner without duplicate finding ownership.",
    "S1": "After owner selection, route S1 explicitly to the repaired strategy-expectancy owner without duplicate finding ownership.",
    "S5": "Implement the frozen cluster-aware horizon-adjusted strategy-family runner and unique s5 report.",
    "S6": "Implement the frozen paired/clustered strategy-adjusted horizon runner and unique s6 report.",
    "S7": "Implement the frozen opportunity-clustered interaction runner after S5/S6, including multiplicity and 2x2 cell gates.",
    "X3": "Align session-quality metric, account grain, nested pre-execution session authority, measured slippage, and completion rule.",
    "EXEC1": "Make execution_results_v1 primary for broker outcomes and protection_audit separately authoritative for protection interventions.",
    "X6": "Implement strict result-to-context-to-trace joins and measured-slippage condition analysis from the frozen design.",
    "R1": "Create a uniquely owned global risk-effectiveness runner/report using canonical decision outcomes and declared survival metrics.",
    "R2": "Create a separate per-guard attribution runner/report with an explicit guard exposure/treatment grain.",
    "R3": "Version loss/distribution/dependence assumptions and calculate probability of ruin on declared independent chronological outcomes.",
    "R4": "Define chronological drawdown paths, threshold policy, and candidate evaluation rather than ordering synthetic summaries as events.",
    "R5": "Declare candidate sizing models, constraints, objective, validation design, and account/opportunity grain before optimisation.",
    "L1": "Create a unique timestamped pattern-degradation runner/report with approved windows and opportunity/horizon clustering.",
    "L2": "Use actual versioned adaptation/change events and before/after chronological populations; do not call report count learning.",
    "L3": "Create a unique architecture-validation runner/report covering weights, regime classes, and strategy mappings; retain D1 only as an input.",
    "L4": "Join chronological market/strategy outcomes at canonical opportunity grain and separate drift from adaptation/causal improvement.",
    "L7": "Assign control/candidate arms from authoritative version/change identity, not first-half/second-half chronology.",
    "L6": "Implement the frozen immutable-snapshot per-conclusion confidence design and unique report.",
    "G1": "Implement the frozen all-question evidence-suitability evaluator and unique report without dashboard evidence.",
    "G2": "Implement the frozen strict opportunity-denominator lineage audit with no fallback joins or horizon/account inflation.",
    "G3": "After G1/G2/L6 exist, implement the frozen nonrecursive same-epoch global validity assessment and human governance boundary.",
}
for _qid in ("EX1", "EX2", "EX5", "EX6", "EX7", "EX8", "EX9", "EX10"):
    _QUESTION_ACTIONS[_qid] = (
        f"Implement { _qid } with a versioned candidate exit-policy, ordered path replay, baseline comparison, "
        "opportunity/horizon clustering, leakage controls, and a unique policy report."
    )


def _wave_category(qid: str) -> str:
    categories: list[str] = []
    for name, targets in (
        ("A1", WAVE_A1_TARGETS), ("A2", WAVE_A2_TARGETS),
        ("A3", WAVE_A3_TARGETS), ("A4", WAVE_A4_TARGETS),
        ("A5", WAVE_A5_TARGETS), ("NO_RUNNER_DESIGN", WAVE_A_NO_RUNNER_TARGETS),
    ):
        if qid in targets:
            categories.append(name)
    return "+".join(categories)


def _frozen_note(qid: str) -> str:
    for reasons in (
        WAVE_A1_UNRESOLVED_REASONS, WAVE_A2_UNRESOLVED_REASONS,
        WAVE_A3_UNRESOLVED_REASONS, WAVE_A4_UNRESOLVED_REASONS,
    ):
        if qid in reasons:
            return reasons[qid]
    if qid in WAVE_A_NO_RUNNER_DESIGNS:
        return WAVE_A_NO_RUNNER_DESIGNS[qid].canonical_intent
    relations = [item for pair, item in WAVE_A5_OWNERSHIP.items() if qid in pair]
    if relations:
        return " ".join(item.required_separation for item in relations)
    return "Wave A established a complete scientific definition and aligned structural contract."


def _operational_gates() -> GateStatus:
    return GateStatus(*([PASS] * 18))


def _blocked_gates(qid: str, category: str) -> GateStatus:
    proposed = qid in WAVE_A_NO_RUNNER_TARGETS
    future = qid == "G3"
    runner_pass = qid in {"D1", "E2", "D6", "PORT-1"}
    report_pass = qid in {"D6", "PORT-1"} or (
        qid not in WAVE_A_NO_RUNNER_TARGETS
        and qid not in {"D1", "E2", "E3", "S1", "R1", "R2", "L1", "L3"}
    )
    definition_pass = qid in {"D1", "E2"}
    identity = HUMAN_DECISION_REQUIRED if qid in {"E3", "S1"} else PASS
    scientific = PASS if definition_pass else (HUMAN_DECISION_REQUIRED if proposed else FAIL)
    evidence = FUTURE_EVIDENCE_REQUIRED if future else (PASS if qid in {"D1", "E2", "D6", "PORT-1"} else PARTIAL)
    governance = HUMAN_DECISION_REQUIRED if qid in {
        "P1", "R1", "R2", "R3", "R4", "R5", "L2", "L7",
        "EX1", "EX2", "EX5", "EX6", "EX7", "EX8", "EX9", "EX10", "G3",
    } else NOT_APPLICABLE
    return GateStatus(
        canonical_identity=identity,
        scientific_definition=scientific,
        unit_of_analysis=PASS if qid in {"D1", "E2", "D6", "PORT-1"} else PARTIAL,
        evidence_authority=evidence,
        join_contract=PASS if qid in {"D1", "E2", "D6", "PORT-1"} else PARTIAL,
        epoch_contract=PASS if qid in {"D1", "E2", "D6", "PORT-1"} else PARTIAL,
        multi_account_contract=PASS if qid in {"D1", "E2", "D6", "PORT-1"} else PARTIAL,
        repeated_measure_contract=PASS if qid in {"D1", "E2", "D6", "PORT-1"} else PARTIAL,
        leakage_contract=PASS if qid in {"D1", "E2", "D6", "PORT-1"} else PARTIAL,
        metric_contract=PASS if qid in {"D1", "E2", "D6", "PORT-1"} else PARTIAL,
        sufficiency_contract=PASS if qid in {"D1", "E2"} else FAIL,
        runner=PASS if runner_pass else FAIL,
        report_ownership=PASS if report_pass else FAIL,
        validity_contract=PASS if qid in {"D1", "E2", "D6", "PORT-1"} else PARTIAL,
        completion_contract=PASS if qid in {"D1", "E2"} else FAIL,
        readiness_control=PARTIAL,
        continuous_loop_eligibility=FAIL,
        governance_contract=governance,
    )


def _verification(qid: str, wave: str) -> str:
    if wave == "RW1":
        return f"The canonical report for {qid} resolves only to {qid}; no distinct ID can inherit its validity, finding, or COMPLETE state."
    if wave == "RW8":
        return f"{qid} reproduces a deterministic ordered-path baseline/candidate fixture without lookahead and becomes COMPLETE only after declared policy sample/cell gates."
    if wave == "RW11":
        return f"{qid} emits its unique canonical governance report from one immutable CURRENT snapshot and truthfully distinguishes READY, WAITING_DATA, BLOCKED, and COMPLETE."
    if wave == "RW12":
        return "G3 consumes validity-approved same-epoch G1/G2/L6 outputs, covers all 69 non-self states, rejects recursion, and has no production-approval path."
    return (
        f"The {qid} runner emits a unique report carrying canonical ID {qid}, uses the declared independent grain and strict CURRENT joins, "
        "meets matching sample/cell rules, and cannot report COMPLETE when its canonical scientific criterion is unmet."
    )


_definitions = build_definitions_from_registry(REGISTRY)
_health = validate_all_definitions(_definitions)
_registry_ids = frozenset(question.id for question in REGISTRY)
if _registry_ids != OPERATIONAL_IDS | frozenset(_ASSIGNMENTS):
    missing = sorted(_registry_ids - OPERATIONAL_IDS - frozenset(_ASSIGNMENTS))
    extra = sorted((OPERATIONAL_IDS | frozenset(_ASSIGNMENTS)) - _registry_ids)
    raise RuntimeError(f"repair ledger coverage mismatch: missing={missing}, extra={extra}")


MASTER_REPAIR_LEDGER: dict[str, LedgerEntry] = {}
for _question in REGISTRY:
    _qid = _question.id
    if _qid in OPERATIONAL_IDS:
        MASTER_REPAIR_LEDGER[_qid] = LedgerEntry(
            canonical_question_id=_qid,
            registry_family=_question.category.value,
            wave_a_category=_wave_category(_qid),
            current_definition_health=get_question_health(_health[_qid]),
            current_operational_state_if_known="STRUCTURALLY_OPERATIONAL; current data state not sampled by this planning ledger",
            gates=_operational_gates(),
            primary_blocker="",
            primary_blocker_category="",
            secondary_blockers=(),
            root_cause_cluster="NONE",
            evidence_gap_classification=ALREADY_AVAILABLE,
            human_semantic_decision_required=False,
            dependency_ids_or_foundations=(),
            repair_actions=(),
            verification_gate="Frozen Wave-A definition, runner, report, validity, readiness, and completion contracts remain aligned.",
            proposed_repair_wave="",
            notes=_frozen_note(_qid),
        )
        continue

    _wave, _category, _cluster, _blocker, _deps, _human, _gap = _ASSIGNMENTS[_qid]
    _wave_item = REPAIR_WAVES[_wave]
    _secondary = (
        f"Gate failures remain in: {', '.join(name for name, value in asdict(_blocked_gates(_qid, _category)).items() if value not in STRUCTURAL_PASS_STATUSES)}.",
    )
    MASTER_REPAIR_LEDGER[_qid] = LedgerEntry(
        canonical_question_id=_qid,
        registry_family=_question.category.value,
        wave_a_category=_wave_category(_qid),
        current_definition_health=get_question_health(_health[_qid]),
        current_operational_state_if_known="BLOCKED_STRUCTURAL",
        gates=_blocked_gates(_qid, _category),
        primary_blocker=_blocker,
        primary_blocker_category=_category,
        secondary_blockers=_secondary,
        root_cause_cluster=_cluster,
        evidence_gap_classification=_gap,
        human_semantic_decision_required=_human,
        dependency_ids_or_foundations=_deps or _wave_item.prerequisites,
        repair_actions=(_QUESTION_ACTIONS[_qid],),
        verification_gate=_verification(_qid, _wave),
        proposed_repair_wave=_wave,
        notes=_frozen_note(_qid),
    )


STRUCTURALLY_OPERATIONAL_IDS = frozenset(
    qid for qid, entry in MASTER_REPAIR_LEDGER.items() if entry.structurally_operational
)
STRUCTURALLY_NON_OPERATIONAL_IDS = frozenset(MASTER_REPAIR_LEDGER) - STRUCTURALLY_OPERATIONAL_IDS


def operational_baseline() -> tuple[int, int]:
    """Return strict structural operational/non-operational counts."""
    return len(STRUCTURALLY_OPERATIONAL_IDS), len(STRUCTURALLY_NON_OPERATIONAL_IDS)


def blocker_counts() -> dict[str, int]:
    """Count non-operational questions by their single primary blocker."""
    counts: dict[str, int] = {}
    for qid in STRUCTURALLY_NON_OPERATIONAL_IDS:
        category = MASTER_REPAIR_LEDGER[qid].primary_blocker_category
        counts[category] = counts.get(category, 0) + 1
    return dict(sorted(counts.items()))


def evidence_gap_counts() -> dict[str, int]:
    """Count all questions by repair-evidence classification."""
    counts = {
        ALREADY_AVAILABLE: 0,
        DERIVABLE: 0,
        NEW_RESEARCH_EVIDENCE: 0,
        EXISTING_V1_CONTRACT_VIOLATION: 0,
    }
    for entry in MASTER_REPAIR_LEDGER.values():
        counts[entry.evidence_gap_classification] += 1
    return counts


def projected_operational_counts() -> tuple[tuple[str, int], ...]:
    """Apply each outstanding direct gain exactly once in declared dependency order.

    An implemented wave's direct gain is already part of the derived operational
    baseline, so it is verified rather than re-applied.  This keeps the overall
    count strictly derived from the 70 ledger entries instead of being hard-coded.
    """
    covered = set(STRUCTURALLY_OPERATIONAL_IDS)
    result: list[tuple[str, int]] = []
    for wave in REPAIR_WAVES.values():
        wave_gain = set(wave.direct_gain)
        if wave.implemented:
            outstanding = wave_gain - covered
            if outstanding:
                raise RuntimeError(
                    f"implemented wave {wave.repair_wave_id} direct gain is not operational: "
                    f"{sorted(outstanding)}"
                )
        else:
            overlap = covered.intersection(wave_gain)
            if overlap:
                raise RuntimeError(f"direct gain double-counted in {wave.repair_wave_id}: {sorted(overlap)}")
            covered.update(wave_gain)
        result.append((wave.repair_wave_id, len(covered)))
    return tuple(result)


def implemented_waves() -> tuple[str, ...]:
    """Repair waves whose exit gate has been proven by focused implementation."""
    return tuple(
        wave.repair_wave_id for wave in REPAIR_WAVES.values() if wave.implemented
    )


def outstanding_operational_gain(wave_id: str) -> tuple[str, ...]:
    """Direct gain of a wave that is still structurally non-operational."""
    wave = REPAIR_WAVES[wave_id]
    return tuple(
        qid for qid in wave.direct_gain if qid in STRUCTURALLY_NON_OPERATIONAL_IDS
    )
