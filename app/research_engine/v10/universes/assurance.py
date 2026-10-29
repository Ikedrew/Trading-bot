"""Stage IV Wave 1 analytical-universe assurance contracts.

This module is a read-only metadata overlay.  It derives universe membership
and expected activity from :mod:`research_engine.v10.universes.models` and
dataset schemas from :mod:`core.production_data_contract`; it does not define
another universe topology or participate in universe construction.

The contracts describe what the existing builders already do.  They are not
permission to produce data, execute research, or reinterpret historical rows.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import json
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from core.production_data_contract import PRODUCTION_SCHEMA_REGISTRY, current_schema
from research_engine.v10.universes import models as universe_models
from research_engine.v10.universes.models import Universe


ASSURANCE_CONTRACT_SCHEMA_VERSION = 1
EXPECTED_ACTIVE_AUTHORITY = (
    "research_engine.v10.universes.models.ACTIVE_UNIVERSES"
)


class UniverseContractError(ValueError):
    """A universe assurance contract is incomplete or contradictory."""


class DatasetRole(str, Enum):
    """A dataset's role in a universe without changing dataset ownership."""

    PRIMARY_SOURCE = "PRIMARY_SOURCE"
    SUPPORTING_SOURCE = "SUPPORTING_SOURCE"
    LEGACY_COMPATIBILITY = "LEGACY_COMPATIBILITY"


class ExpectedActivity(str, Enum):
    EXPECTED_ACTIVE = "EXPECTED_ACTIVE"
    NOT_EXPECTED = "NOT_EXPECTED"
    UNKNOWN_EXPECTATION = "UNKNOWN_EXPECTATION"


class PresenceStatus(str, Enum):
    """Minimal Wave 1 presence vocabulary; no freshness scoring is performed."""

    PRESENT = "PRESENT"
    ABSENT_LEGITIMATE = "ABSENT_LEGITIMATE"
    ABSENT_UNEXPECTED = "ABSENT_UNEXPECTED"
    NOT_EXPECTED = "NOT_EXPECTED"
    STALE = "STALE"
    UNKNOWN_EXPECTATION = "UNKNOWN_EXPECTATION"
    AMBIGUOUS_AUTHORITY = "AMBIGUOUS_AUTHORITY"


@dataclass(frozen=True)
class DatasetBinding:
    dataset: str
    schema_version: str
    role: DatasetRole
    semantic_role: str


@dataclass(frozen=True)
class IdentitySemantics:
    primary_fields: tuple[str, ...]
    relationship_fields: tuple[str, ...]
    meaning: str


@dataclass(frozen=True)
class TimestampSemantics:
    event_fields: tuple[str, ...]
    meaning: str
    timezone: str
    ordering_limitations: str
    late_arrival_possible: bool
    persistence_time_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class UniverseDependency:
    """Reference to a canonical universe or production-contract dataset."""

    reference: str
    kind: str  # ``UNIVERSE`` or ``DATASET``
    reason: str


@dataclass(frozen=True)
class Counterpart:
    universe: Universe
    expectation: str
    condition: str
    join_fields: tuple[str, ...]


@dataclass(frozen=True)
class UniverseAssuranceContract:
    universe: Universe
    description: str
    authority_module: str
    builder: str
    datasets: tuple[DatasetBinding, ...]
    population_semantics: str
    resolution: str
    identity: IdentitySemantics
    timestamps: TimestampSemantics
    expected_presence: str
    legitimate_absence: tuple[str, ...]
    dependencies: tuple[UniverseDependency, ...]
    counterparts: tuple[Counterpart, ...]
    limitations: tuple[str, ...]
    internal_record_schema: str = ""
    unresolved_ambiguity: tuple[str, ...] = ()
    schema_version: int = ASSURANCE_CONTRACT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        """Return a deterministic JSON-compatible description."""
        value = asdict(self)
        value["universe"] = self.universe.value
        for dataset in value["datasets"]:
            dataset["role"] = dataset["role"].value
        for counterpart in value["counterparts"]:
            counterpart["universe"] = counterpart["universe"].value
        # ``dataclasses.asdict`` deliberately preserves tuples.  The public
        # self-knowledge payload promises a JSON-native representation so a
        # serialize/reconstruct cycle is identity-preserving.
        return json.loads(json.dumps(value, sort_keys=True))


@dataclass(frozen=True)
class PresenceContext:
    """Facts supplied by a later collector; Wave 1 does not collect them."""

    record_count: int | None
    source_available: bool | None
    qualifying_activity_expected: bool | None
    stale: bool = False
    authority_ambiguous: bool = False


def _dataset(name: str, role: DatasetRole, semantic_role: str) -> DatasetBinding:
    return DatasetBinding(name, current_schema(name), role, semantic_role)


def _dep_dataset(name: str, reason: str) -> UniverseDependency:
    return UniverseDependency(name, "DATASET", reason)


def _dep_universe(universe: Universe, reason: str) -> UniverseDependency:
    return UniverseDependency(universe.value, "UNIVERSE", reason)


_CONTRACTS: tuple[UniverseAssuranceContract, ...] = (
    UniverseAssuranceContract(
        universe=Universe.EXECUTION,
        description="Realised execution facts for completed, validated live trades.",
        authority_module="research_engine.v10.universes.execution_universe",
        builder="ExecutionUniverseBuilder",
        datasets=(
            _dataset("trade_truth", DatasetRole.PRIMARY_SOURCE,
                     "authoritative realised trade and outcome facts"),
            _dataset("execution_results", DatasetRole.SUPPORTING_SOURCE,
                     "correlation-to-entity lineage and sizing quality"),
        ),
        population_semantics=(
            "One normalized row per trade_truth record having a trade_id and "
            "realised R-multiple."
        ),
        resolution="completed live trade; symbol/account/broker coverage follows trade_truth",
        identity=IdentitySemantics(
            ("trade_id",), ("entity_id", "correlation_id", "symbol"),
            "trade_id identifies the live trade; correlation_id links execution "
            "results and entity_id links decision-side evidence.",
        ),
        timestamps=TimestampSemantics(
            ("entry_time", "exit_time"),
            "broker entry and exit time copied from trade_truth timestamps",
            "broker/record supplied; not normalized by the builder",
            "broker ordering is authoritative; S3 arrival can be later",
            True,
        ),
        expected_presence="Expected only after at least one completed valid live trade exists.",
        legitimate_absence=(
            "no live trade was executed", "executed trades remain open",
            "trade truth lacks trade_id or realised R and is excluded",
        ),
        dependencies=(_dep_dataset("trade_truth", "primary facts"),
                      _dep_dataset("execution_results", "decision lineage enrichment")),
        counterparts=(
            Counterpart(Universe.DECISION, "CONDITIONALLY_EXPECTED",
                        "an execution should originate from an EXECUTE decision",
                        ("entity_id", "correlation_id")),
            Counterpart(Universe.OUTCOME, "ALWAYS_EXPECTED",
                        "every included execution is already terminal",
                        ("trade_id", "entity_id")),
        ),
        limitations=(
            "does not contain authoritative pre-trade strategy or market context",
            "entity_id may fall back to trade_id when enrichment is unavailable",
            "absence cannot distinguish no trading from missing source partitions",
        ),
    ),
    UniverseAssuranceContract(
        universe=Universe.DECISION,
        description="Terminal pipeline decisions, including EXECUTE and NO_TRADE.",
        authority_module="research_engine.v10.universes.decision_universe",
        builder="DecisionUniverseBuilder",
        datasets=(
            _dataset("decision_trace", DatasetRole.PRIMARY_SOURCE,
                     "decision diagnostics and terminal action"),
            _dataset("execution_results", DatasetRole.SUPPORTING_SOURCE,
                     "execution-sizing eligibility annotation"),
        ),
        population_semantics=(
            "One normalized row per full decision_trace record having entity_id "
            "and action; RISK_REJECTION auxiliary events are excluded."
        ),
        resolution="terminal decision event per analytical entity/cycle",
        identity=IdentitySemantics(
            ("entity_id",), ("correlation_id", "cycle_id", "symbol"),
            "entity_id identifies the analytical decision; correlation_id links execution.",
        ),
        timestamps=TimestampSemantics(
            ("timestamp_utc",), "decision event time", "UTC by field contract",
            "late S3 arrival is possible; timestamp is not ingestion time", True,
        ),
        expected_presence="Expected when the pipeline reaches a terminal decision.",
        legitimate_absence=(
            "no qualifying opportunity reached decision", "requested scope has no pipeline activity",
        ),
        dependencies=(_dep_dataset("decision_trace", "terminal decision evidence"),),
        counterparts=(
            Counterpart(Universe.MARKET, "CONDITIONALLY_EXPECTED",
                        "market state is captured for the decision", ("entity_id",)),
            Counterpart(Universe.STRATEGY, "CONDITIONALLY_EXPECTED",
                        "strategy evaluation was reached", ("entity_id",)),
            Counterpart(Universe.RISK, "CONDITIONALLY_EXPECTED",
                        "the decision path reached risk evaluation", ("entity_id",)),
            Counterpart(Universe.EXECUTION, "CONDITIONALLY_EXPECTED",
                        "only an EXECUTE decision may produce live execution", ("entity_id", "correlation_id")),
            Counterpart(Universe.SHADOW_OUTCOME, "CONDITIONALLY_EXPECTED",
                        "an eligible shadow lifecycle was opened and completed", ("entity_id",)),
        ),
        limitations=(
            "a NO_TRADE decision legitimately has no live execution",
            "decision presence alone does not prove market/strategy/risk counterpart completeness",
        ),
    ),
    UniverseAssuranceContract(
        universe=Universe.MARKET,
        description="Decision-time or standalone market-context observations.",
        authority_module="research_engine.v10.universes.market_universe",
        builder="MarketUniverseBuilder",
        datasets=(
            _dataset("decision_trace", DatasetRole.PRIMARY_SOURCE,
                     "decision-linked v10 market state"),
            _dataset("market_context", DatasetRole.SUPPORTING_SOURCE,
                     "standalone symbol/cycle market context"),
        ),
        population_semantics=(
            "One normalized market snapshot per decision-linked entity where "
            "available, plus deduplicated standalone symbol/cycle snapshots."
        ),
        resolution="market snapshot at decision/cycle resolution, not tick history",
        identity=IdentitySemantics(
            ("entity_id",), ("symbol", "cycle_id"),
            "entity_id is primary when decision-linked; standalone rows use symbol+cycle_id.",
        ),
        timestamps=TimestampSemantics(
            ("timestamp_utc",), "market observation/decision-cycle time", "UTC expected",
            "mixed producers mean observation and persistence partition dates may differ", True,
        ),
        expected_presence="Expected when market context was captured for an observed decision or cycle.",
        legitimate_absence=(
            "no qualifying decision/cycle occurred", "pipeline terminated before context capture",
        ),
        dependencies=(_dep_dataset("decision_trace", "decision-linked state"),
                      _dep_dataset("market_context", "standalone context")),
        counterparts=(
            Counterpart(Universe.DECISION, "CONDITIONALLY_EXPECTED",
                        "snapshot was taken for a decision", ("entity_id",)),
            Counterpart(Universe.STRATEGY, "CONDITIONALLY_EXPECTED",
                        "strategy was evaluated in the same cycle", ("entity_id", "cycle_id")),
        ),
        limitations=(
            "cannot establish intrabar or tick ordering",
            "standalone rows may have no entity_id",
            "mixed source grains prevent treating every row as one decision",
        ),
    ),
    UniverseAssuranceContract(
        universe=Universe.STRATEGY,
        description="Strategy evaluations observed during a decision cycle.",
        authority_module="research_engine.v10.universes.strategy_universe",
        builder="StrategyUniverseBuilder",
        datasets=(
            _dataset("strategy_observations", DatasetRole.PRIMARY_SOURCE,
                     "strategy observation evidence"),
            _dataset("decision_trace", DatasetRole.SUPPORTING_SOURCE,
                     "decision-linked v10 strategy fallback"),
        ),
        population_semantics=(
            "One normalized strategy evaluation per entity where possible; "
            "strategy_observations take precedence over decision-trace duplicates."
        ),
        resolution="strategy evaluation at entity/decision-cycle resolution",
        identity=IdentitySemantics(
            ("entity_id",), ("correlation_id", "symbol", "cycle_id"),
            "entity_id identifies a decision-linked evaluation; symbol+cycle_id supports context.",
        ),
        timestamps=TimestampSemantics(
            ("timestamp_utc",), "strategy observation/evaluation time", "UTC expected",
            "source rows may arrive after the evaluated decision", True,
        ),
        expected_presence="Expected when the pipeline reaches strategy evaluation.",
        legitimate_absence=(
            "pipeline terminated before strategy evaluation", "no eligible strategy was evaluated",
        ),
        dependencies=(_dep_dataset("strategy_observations", "primary observations"),
                      _dep_dataset("decision_trace", "fallback decision-linked strategy state")),
        counterparts=(
            Counterpart(Universe.DECISION, "CONDITIONALLY_EXPECTED",
                        "evaluation belongs to a terminal decision", ("entity_id",)),
            Counterpart(Universe.MARKET, "CONDITIONALLY_EXPECTED",
                        "market state was captured in the same cycle", ("entity_id", "cycle_id")),
        ),
        limitations=(
            "evaluation does not imply selection or execution",
            "fallback and primary producers have different completeness",
        ),
    ),
    UniverseAssuranceContract(
        universe=Universe.RISK,
        description="Risk evaluations embedded in terminal decision traces.",
        authority_module="research_engine.v10.universes.risk_universe",
        builder="RiskUniverseBuilder",
        datasets=(
            _dataset("decision_trace", DatasetRole.PRIMARY_SOURCE,
                     "decision-linked v10 risk evaluation"),
            _dataset("execution_results", DatasetRole.SUPPORTING_SOURCE,
                     "sizing quality annotation"),
        ),
        population_semantics=(
            "One normalized risk-evaluation row per decision trace that reached "
            "risk and contains entity_id and risk data."
        ),
        resolution="risk evaluation per analytical entity/decision",
        identity=IdentitySemantics(
            ("entity_id",), ("correlation_id", "symbol", "cycle_id"),
            "entity_id links the risk decision to the surrounding lifecycle.",
        ),
        timestamps=TimestampSemantics(
            ("timestamp_utc",), "risk evaluation time inherited from decision trace",
            "UTC expected", "not an execution or broker timestamp", True,
        ),
        expected_presence="Expected only when the pipeline reaches the risk stage.",
        legitimate_absence=(
            "opportunity was rejected before risk", "no risk evaluation was required",
            "decision trace contains no risk payload",
        ),
        dependencies=(_dep_dataset("decision_trace", "risk payload"),),
        counterparts=(
            Counterpart(Universe.DECISION, "ALWAYS_EXPECTED",
                        "risk evaluation is embedded in a decision trace", ("entity_id",)),
            Counterpart(Universe.EXECUTION, "CONDITIONALLY_EXPECTED",
                        "approved risk plus later gates may lead to execution", ("entity_id", "correlation_id")),
        ),
        limitations=(
            "absence is normal for decisions that did not reach risk",
            "risk approval does not imply successful execution",
        ),
    ),
    UniverseAssuranceContract(
        universe=Universe.OUTCOME,
        description="Terminal realised outcomes projected from Execution universe records.",
        authority_module="research_engine.v10.universes.outcome_universe",
        builder="OutcomeUniverseBuilder",
        datasets=(),
        population_semantics="One derived outcome row per included completed Execution-universe trade.",
        resolution="terminal realised outcome per completed live trade",
        identity=IdentitySemantics(
            ("trade_id",), ("entity_id", "correlation_id", "symbol"),
            "identity is inherited without reminting from the Execution universe.",
        ),
        timestamps=TimestampSemantics(
            ("entry_time", "exit_time"), "inherited broker entry/exit times",
            "broker/record supplied; not normalized by the builder",
            "derived after execution build; S3 arrival can be later", True,
        ),
        expected_presence="Expected for every record included in the Execution universe.",
        legitimate_absence=(
            "Execution universe is legitimately empty", "trade has not completed and is absent from Execution",
        ),
        dependencies=(_dep_universe(Universe.EXECUTION, "sole source population"),),
        counterparts=(
            Counterpart(Universe.EXECUTION, "ALWAYS_EXPECTED",
                        "outcome is a projection of completed execution", ("trade_id", "entity_id")),
        ),
        limitations=(
            "not an independent observation source",
            "cannot represent open trades or decisions without execution",
        ),
    ),
    UniverseAssuranceContract(
        universe=Universe.SHADOW_OUTCOME,
        description="Terminal counterfactual outcomes reconstructed from shadow lifecycle events.",
        authority_module="research_engine.v10.universes.shadow_outcome_universe",
        builder="ShadowOutcomeUniverseBuilder",
        datasets=(
            _dataset("shadow_runtime", DatasetRole.PRIMARY_SOURCE,
                     "canonical append-only production shadow lifecycle events"),
            _dataset("shadow_trades", DatasetRole.LEGACY_COMPATIBILITY,
                     "historical persisted terminal dataset; never an active source for this universe"),
        ),
        internal_record_schema=current_schema("shadow_trades"),
        population_semantics=(
            "One normalized terminal counterfactual row per valid shadow_runtime "
            "OPEN+CLOSE lifecycle keyed by shadow trade, opportunity, and horizon."
        ),
        resolution="completed simulated trade per canonical opportunity and evaluated horizon; M5 bar path",
        identity=IdentitySemantics(
            ("shadow_trade_id",),
            ("entity_id", "cycle_id", "symbol", "evaluated_horizon"),
            "shadow_trade_id identifies the normalized outcome. Source ingestion "
            "pairs lifecycle events on shadow_trade_id+canonical_opportunity_id+horizon "
            "before the universe builder normalizes the result.",
        ),
        timestamps=TimestampSemantics(
            ("timestamp_decision_utc", "exit_timestamp"),
            "shadow decision/open time and simulated terminal close time",
            "UTC required by runtime schema",
            "OPEN and CLOSE may arrive separately; only a valid pair becomes an outcome",
            True,
        ),
        expected_presence=(
            "Expected only for eligible shadow plans that opened and later reached a valid terminal CLOSE."
        ),
        legitimate_absence=(
            "no eligible shadow plan", "horizon disabled or ineligible",
            "lifecycle remains open", "OPEN or CLOSE is malformed/unpairable",
            "terminal outcome is missing",
        ),
        dependencies=(_dep_dataset("shadow_runtime", "canonical lifecycle events"),),
        counterparts=(
            Counterpart(Universe.DECISION, "CONDITIONALLY_EXPECTED",
                        "shadow originated at a decision branch with entity lineage", ("entity_id",)),
        ),
        limitations=(
            "counterfactual simulation is not executable broker truth",
            "M5 OHLC cannot establish intrabar ordering; same-bar TP/SL uses SL-first policy",
            "the persisted shadow_trades dataset is legacy compatibility evidence, not interchangeable with shadow_runtime",
            "OPEN/PROGRESS without CLOSE is not an outcome",
            "the normalized universe row does not preserve canonical_opportunity_id, plan_id, or observation_id from the ingested source record",
        ),
    ),
)


UNIVERSE_ASSURANCE_CONTRACTS: Mapping[Universe, UniverseAssuranceContract] = (
    MappingProxyType({contract.universe: contract for contract in _CONTRACTS})
)


def expected_active_universes() -> tuple[Universe, ...]:
    """Return the canonical expected-active set without copying its authority."""
    return tuple(universe_models.ACTIVE_UNIVERSES)


def expected_activity(universe: Universe) -> ExpectedActivity:
    """Resolve expectation solely from the canonical universe model."""
    if universe in universe_models.ACTIVE_UNIVERSES:
        return ExpectedActivity.EXPECTED_ACTIVE
    if universe in universe_models.RETIRED_UNIVERSES:
        return ExpectedActivity.NOT_EXPECTED
    return ExpectedActivity.UNKNOWN_EXPECTATION


def validate_contracts(
    contracts: Iterable[UniverseAssuranceContract] = _CONTRACTS,
    *,
    expectation_authorities: tuple[str, ...] = (EXPECTED_ACTIVE_AUTHORITY,),
) -> None:
    """Fail closed when the assurance overlay diverges from canonical truth."""
    items = tuple(contracts)
    if expectation_authorities != (EXPECTED_ACTIVE_AUTHORITY,):
        raise UniverseContractError(
            "exactly one expected-active authority is permitted: "
            f"{EXPECTED_ACTIVE_AUTHORITY}"
        )
    ids = [item.universe for item in items]
    if len(ids) != len(set(ids)):
        raise UniverseContractError("duplicate canonical universe contract")
    expected = set(universe_models.ACTIVE_UNIVERSES)
    actual = set(ids)
    if actual != expected:
        missing = sorted(u.value for u in expected - actual)
        extra = sorted(u.value for u in actual - expected)
        raise UniverseContractError(
            f"contract topology diverges from ACTIVE_UNIVERSES: missing={missing}, extra={extra}"
        )

    for item in items:
        label = item.universe.value
        required_text = {
            "description": item.description,
            "authority_module": item.authority_module,
            "builder": item.builder,
            "population_semantics": item.population_semantics,
            "resolution": item.resolution,
            "identity.meaning": item.identity.meaning,
            "timestamps.meaning": item.timestamps.meaning,
            "timestamps.timezone": item.timestamps.timezone,
            "expected_presence": item.expected_presence,
        }
        for field_name, value in required_text.items():
            if not isinstance(value, str) or not value.strip():
                raise UniverseContractError(f"{label}: missing {field_name}")
        if not item.identity.primary_fields:
            raise UniverseContractError(f"{label}: missing primary identity fields")
        if not item.timestamps.event_fields:
            raise UniverseContractError(f"{label}: missing event timestamp fields")
        if not item.legitimate_absence:
            raise UniverseContractError(f"{label}: missing legitimate-absence rules")
        if not item.limitations:
            raise UniverseContractError(f"{label}: missing limitations")

        for binding in item.datasets:
            if binding.dataset not in PRODUCTION_SCHEMA_REGISTRY:
                raise UniverseContractError(
                    f"{label}: unknown dataset {binding.dataset!r}"
                )
            canonical_schema = current_schema(binding.dataset)
            if binding.schema_version != canonical_schema:
                raise UniverseContractError(
                    f"{label}: schema mismatch for {binding.dataset}: "
                    f"{binding.schema_version!r} != {canonical_schema!r}"
                )
        for dependency in item.dependencies:
            if dependency.kind == "DATASET":
                if dependency.reference not in PRODUCTION_SCHEMA_REGISTRY:
                    raise UniverseContractError(
                        f"{label}: invalid dataset dependency {dependency.reference!r}"
                    )
            elif dependency.kind == "UNIVERSE":
                if dependency.reference not in {u.value for u in Universe}:
                    raise UniverseContractError(
                        f"{label}: invalid universe dependency {dependency.reference!r}"
                    )
            else:
                raise UniverseContractError(
                    f"{label}: invalid dependency kind {dependency.kind!r}"
                )
        for counterpart in item.counterparts:
            if counterpart.universe not in universe_models.ACTIVE_UNIVERSES:
                raise UniverseContractError(
                    f"{label}: counterpart {counterpart.universe.value} is not active"
                )
            if not counterpart.condition.strip() or not counterpart.expectation.strip():
                raise UniverseContractError(f"{label}: incomplete counterpart declaration")

    shadow = next(item for item in items if item.universe is Universe.SHADOW_OUTCOME)
    primary = [d for d in shadow.datasets if d.role is DatasetRole.PRIMARY_SOURCE]
    legacy = [d for d in shadow.datasets if d.role is DatasetRole.LEGACY_COMPATIBILITY]
    if [d.dataset for d in primary] != ["shadow_runtime"]:
        raise UniverseContractError(
            "SHADOW_OUTCOME must have shadow_runtime as its sole primary source"
        )
    if not any(d.dataset == "shadow_trades" for d in legacy):
        raise UniverseContractError(
            "SHADOW_OUTCOME must classify persisted shadow_trades as legacy compatibility"
        )
    if shadow.internal_record_schema != current_schema("shadow_trades"):
        raise UniverseContractError(
            "SHADOW_OUTCOME internal terminal shape must be shadow_trades schema"
        )


def get_universe_contract(universe: Universe) -> UniverseAssuranceContract:
    """Return one validated active-universe contract or fail closed."""
    validate_contracts()
    try:
        return UNIVERSE_ASSURANCE_CONTRACTS[universe]
    except KeyError as exc:
        raise UniverseContractError(
            f"{universe.value} has no active assurance contract"
        ) from exc


def describe_analytical_topology() -> dict[str, Any]:
    """Structured, deterministic self-knowledge for internal consumers."""
    validate_contracts()
    return {
        "schema_version": ASSURANCE_CONTRACT_SCHEMA_VERSION,
        "universe_authority": "research_engine.v10.universes.models.Universe",
        "expected_active_authority": EXPECTED_ACTIVE_AUTHORITY,
        "expected_active_universes": [u.value for u in expected_active_universes()],
        "retired_universes": [u.value for u in universe_models.RETIRED_UNIVERSES],
        "contracts": [
            UNIVERSE_ASSURANCE_CONTRACTS[u].to_dict()
            for u in expected_active_universes()
        ],
    }


def classify_presence(universe: Universe, context: PresenceContext) -> PresenceStatus:
    """Classify supplied presence facts without inspecting files or datasets."""
    expectation = expected_activity(universe)
    if context.authority_ambiguous:
        return PresenceStatus.AMBIGUOUS_AUTHORITY
    if expectation is ExpectedActivity.NOT_EXPECTED:
        return PresenceStatus.NOT_EXPECTED
    if expectation is ExpectedActivity.UNKNOWN_EXPECTATION:
        return PresenceStatus.UNKNOWN_EXPECTATION
    if context.record_count is not None and context.record_count < 0:
        raise UniverseContractError("record_count cannot be negative")
    if context.record_count and context.record_count > 0:
        return PresenceStatus.STALE if context.stale else PresenceStatus.PRESENT
    if context.source_available is False:
        return PresenceStatus.ABSENT_UNEXPECTED
    if context.qualifying_activity_expected is False:
        return PresenceStatus.ABSENT_LEGITIMATE
    if context.qualifying_activity_expected is True:
        return PresenceStatus.ABSENT_UNEXPECTED
    return PresenceStatus.UNKNOWN_EXPECTATION


# Import-time validation is intentional: malformed static assurance metadata
# must fail loudly before a consumer can present it as project knowledge.
validate_contracts()


__all__ = [
    "ASSURANCE_CONTRACT_SCHEMA_VERSION",
    "EXPECTED_ACTIVE_AUTHORITY",
    "Counterpart",
    "DatasetBinding",
    "DatasetRole",
    "ExpectedActivity",
    "IdentitySemantics",
    "PresenceContext",
    "PresenceStatus",
    "TimestampSemantics",
    "UNIVERSE_ASSURANCE_CONTRACTS",
    "UniverseAssuranceContract",
    "UniverseContractError",
    "UniverseDependency",
    "classify_presence",
    "describe_analytical_topology",
    "expected_active_universes",
    "expected_activity",
    "get_universe_contract",
    "validate_contracts",
]
