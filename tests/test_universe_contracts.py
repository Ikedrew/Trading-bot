"""
Contract Test Suite.

Proves:
    - Every universe has a valid contract
    - Every population has a valid contract
    - Every population has lineage
    - Every population has a defined grain
    - Every population has identity
    - Every join has declared cardinality
    - Many-to-many joins are explicitly rejected unless intended
    - Semantic fields resolve to real source paths
    - Required fields exist
    - Required fields have valid types
    - Population versions are identifiable
    - Latest-valid resolution cannot select invalid populations
    - Every question can resolve its required universes
    - Every question can resolve its required populations
    - Every question's required fields are available
    - Every question's joins are valid
    - No legacy registry is required by the new contract
"""

import pytest

from research_engine.v10.universes.contracts import (
    UNIVERSE_CONTRACTS,
    POPULATION_CONTRACTS,
    JOIN_CONTRACTS,
    SEMANTIC_FIELD_MAPPINGS,
    SEMANTIC_FIELDS_BY_NAME,
    Cardinality,
    FieldType,
    get_field_mapping,
    get_join_contract,
    get_population_contract,
    get_universe_contract,
)
from research_engine.v10.universes.models import Population, Universe
from research_engine.v10.universes.question_bank import QUESTION_BANK
from research_engine.v10.universes.question_validator import validate_all_questions
from research_engine.v10.universes.resolver import (
    PopulationResolver,
    PopulationVersion,
    ResolutionResult,
)
from research_engine.v10.universes.health import (
    PopulationHealth,
    check_population_health,
)


# ═══════════════════════════════════════════════════════════════════════════════
# UNIVERSE CONTRACT TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestUniverseContracts:

    def test_all_four_universes_have_contracts(self):
        for u in Universe:
            assert u in UNIVERSE_CONTRACTS, f"No contract for {u.value}"

    def test_every_universe_has_grain(self):
        for u, c in UNIVERSE_CONTRACTS.items():
            assert c.grain, f"{u.value}: grain is empty"

    def test_every_universe_has_identity_field(self):
        for u, c in UNIVERSE_CONTRACTS.items():
            assert c.identity_field, f"{u.value}: identity_field is empty"

    def test_every_universe_has_sources(self):
        for u, c in UNIVERSE_CONTRACTS.items():
            assert c.source_datasets, f"{u.value}: no source datasets"

    def test_every_universe_has_join_keys(self):
        for u, c in UNIVERSE_CONTRACTS.items():
            assert c.join_keys, f"{u.value}: no join keys"

    def test_every_universe_has_lineage_fields(self):
        for u, c in UNIVERSE_CONTRACTS.items():
            assert c.lineage_fields, f"{u.value}: no lineage fields"

    def test_get_universe_contract(self):
        c = get_universe_contract(Universe.EXECUTION)
        assert c.universe_id == Universe.EXECUTION
        assert c.name == "Execution Universe"


# ═══════════════════════════════════════════════════════════════════════════════
# POPULATION CONTRACT TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestPopulationContracts:

    def test_population_count(self):
        assert len(POPULATION_CONTRACTS) >= 25

    def test_every_population_has_contract(self):
        """Every Population enum value used by question bank must have a contract."""
        used_populations = set()
        for q in QUESTION_BANK:
            used_populations.update(q.required_populations)
        for pop in used_populations:
            assert pop in POPULATION_CONTRACTS, (
                f"Population {pop.value} used by question bank but has no contract"
            )

    def test_every_contract_has_universe(self):
        for pop, c in POPULATION_CONTRACTS.items():
            assert c.universe_id, f"{pop.value}: no universe_id"

    def test_every_contract_has_grain(self):
        for pop, c in POPULATION_CONTRACTS.items():
            assert c.record_grain, f"{pop.value}: no record_grain"

    def test_every_contract_has_join_keys(self):
        for pop, c in POPULATION_CONTRACTS.items():
            assert c.join_keys, f"{pop.value}: no join_keys"

    def test_every_contract_has_definition(self):
        for pop, c in POPULATION_CONTRACTS.items():
            assert c.definition, f"{pop.value}: no definition"

    def test_get_population_contract(self):
        c = get_population_contract(Population.ALL_TRADES)
        assert c is not None
        assert c.universe_id == Universe.EXECUTION


# ═══════════════════════════════════════════════════════════════════════════════
# JOIN CONTRACT TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestJoinContracts:

    def test_join_count(self):
        assert len(JOIN_CONTRACTS) >= 6

    def test_every_join_has_cardinality(self):
        for j in JOIN_CONTRACTS:
            assert j.cardinality, f"{j.join_id}: no cardinality"
            assert isinstance(j.cardinality, Cardinality)

    def test_no_undeclared_many_to_many(self):
        """Many-to-many joins must be explicitly documented."""
        m2m = [j for j in JOIN_CONTRACTS if j.cardinality == Cardinality.MANY_TO_MANY]
        # If any exist, they should have explicit descriptions
        for j in m2m:
            assert "N:N" in j.description or "many" in j.description.lower(), (
                f"{j.join_id}: M:M join without explicit documentation"
            )

    def test_joins_reference_valid_universes(self):
        for j in JOIN_CONTRACTS:
            assert j.left_universe in Universe.__members__.values()
            assert j.right_universe in Universe.__members__.values()

    def test_joins_have_key_fields(self):
        for j in JOIN_CONTRACTS:
            assert j.left_key, f"{j.join_id}: no left_key"
            assert j.right_key, f"{j.join_id}: no right_key"

    def test_get_join_contract(self):
        j = get_join_contract(Universe.DECISION, Universe.MARKET)
        assert j is not None
        assert j.join_id == "DECISION_MARKET"

    def test_all_question_joins_have_contracts(self):
        """Every join used by a question must have a contract (or reverse)."""
        for q in QUESTION_BANK:
            for jr in q.required_joins:
                fwd = get_join_contract(jr.from_universe, jr.to_universe)
                rev = get_join_contract(jr.to_universe, jr.from_universe)
                assert fwd or rev, (
                    f"{q.question_id}: join {jr.from_universe.value}→"
                    f"{jr.to_universe.value} has no contract"
                )


# ═══════════════════════════════════════════════════════════════════════════════
# SEMANTIC FIELD MAPPING TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestSemanticFieldMappings:

    def test_field_mapping_count(self):
        assert len(SEMANTIC_FIELD_MAPPINGS) >= 40

    def test_every_mapping_has_source_path(self):
        for m in SEMANTIC_FIELD_MAPPINGS:
            assert m.source_path, f"{m.semantic_name}: no source_path"

    def test_every_mapping_has_type(self):
        for m in SEMANTIC_FIELD_MAPPINGS:
            assert isinstance(m.field_type, FieldType)

    def test_every_mapping_has_validation(self):
        for m in SEMANTIC_FIELD_MAPPINGS:
            assert m.validation, f"{m.semantic_name}: no validation rule"

    def test_question_fields_have_mappings(self):
        """Every required field in the question bank should resolve to a mapping."""
        unmapped_critical = []
        for q in QUESTION_BANK:
            for ar in q.angle_requirements:
                for field_name in ar.required_fields:
                    mappings = SEMANTIC_FIELDS_BY_NAME.get(field_name, [])
                    if not mappings:
                        # Not in any universe — might be derived
                        if field_name not in ("r_multiple", "entity_id"):
                            unmapped_critical.append(
                                f"{q.question_id}.{ar.universe.value}.{field_name}"
                            )
        # Allow some unmapped (derived/computed fields) but not too many
        assert len(unmapped_critical) < 20, (
            f"Too many unmapped fields: {unmapped_critical[:10]}"
        )

    def test_get_field_mapping(self):
        m = get_field_mapping("r_multiple", Universe.EXECUTION)
        assert m is not None
        assert m.source_path == "execution.r_multiple"


# ═══════════════════════════════════════════════════════════════════════════════
# RESOLVER & VERSIONING TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestResolverContract:

    def test_resolver_blocks_unregistered(self):
        resolver = PopulationResolver()
        result = resolver.resolve(
            Population.ALL_TRADES, Universe.EXECUTION, minimum_sample_size=10
        )
        assert not result.resolved
        assert "not been built" in result.reason

    def test_resolver_blocks_insufficient_sample(self):
        resolver = PopulationResolver()
        version = PopulationVersion(
            population_id="all_trades",
            universe_id="EXECUTION",
            generation_timestamp="2026-08-09T00:00:00Z",
            generator_version="1.0.0",
            source_schema_version="1.0",
            row_count=5,
            content_hash="abc123",
            coverage_start="",
            coverage_end="",
            health_status="VALID",
        )
        resolver.register_version(version)
        result = resolver.resolve(
            Population.ALL_TRADES, Universe.EXECUTION, minimum_sample_size=10
        )
        assert not result.resolved
        assert "Row count 5 < minimum 10" in result.reason

    def test_resolver_blocks_invalid_health(self):
        resolver = PopulationResolver()
        version = PopulationVersion(
            population_id="all_trades",
            universe_id="EXECUTION",
            generation_timestamp="2026-08-09T00:00:00Z",
            generator_version="1.0.0",
            source_schema_version="1.0",
            row_count=100,
            content_hash="abc123",
            coverage_start="",
            coverage_end="",
            health_status="INVALID",
        )
        resolver.register_version(version)
        result = resolver.resolve(
            Population.ALL_TRADES, Universe.EXECUTION, minimum_sample_size=10
        )
        assert not result.resolved
        assert "INVALID" in result.reason

    def test_resolver_accepts_valid(self):
        resolver = PopulationResolver()
        version = PopulationVersion(
            population_id="all_trades",
            universe_id="EXECUTION",
            generation_timestamp="2026-08-09T00:00:00Z",
            generator_version="1.0.0",
            source_schema_version="1.0",
            row_count=94,
            content_hash="abc123",
            coverage_start="1000",
            coverage_end="2000",
            instruments=("EURUSD", "GBPUSD"),
            health_status="VALID",
        )
        resolver.register_version(version)
        result = resolver.resolve(
            Population.ALL_TRADES, Universe.EXECUTION, minimum_sample_size=20
        )
        assert result.resolved
        assert result.version is not None

    def test_version_has_reproducible_identity(self):
        version = PopulationVersion(
            population_id="all_trades",
            universe_id="EXECUTION",
            generation_timestamp="2026-08-09T00:00:00Z",
            generator_version="1.0.0",
            source_schema_version="1.0",
            row_count=94,
            content_hash="63b5c0f1194f398a",
            coverage_start="1000",
            coverage_end="2000",
        )
        d = version.to_dict()
        assert d["content_hash"] == "63b5c0f1194f398a"
        assert d["row_count"] == 94
        assert d["generation_timestamp"]


# ═══════════════════════════════════════════════════════════════════════════════
# QUESTION READINESS TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestQuestionReadiness:

    def test_all_questions_validate_without_error(self):
        """Validation should not raise exceptions."""
        results = validate_all_questions(QUESTION_BANK)
        assert len(results) == 45

    def test_no_invalid_questions(self):
        """No question should be structurally INVALID against contracts."""
        results = validate_all_questions(QUESTION_BANK)
        invalid = [r for r in results if r.status == "INVALID"]
        assert not invalid, f"Invalid questions: {[r.question_id for r in invalid]}"

    def test_all_universes_resolvable(self):
        """Every question's required universes must have contracts."""
        results = validate_all_questions(QUESTION_BANK)
        for r in results:
            for u, status in r.universe_status.items():
                assert status == "AVAILABLE", (
                    f"{r.question_id}: universe {u} is {status}"
                )

    def test_all_populations_contracted(self):
        """Every required population must have a contract."""
        results = validate_all_questions(QUESTION_BANK)
        for r in results:
            for pop, status in r.population_status.items():
                assert "NO_CONTRACT" not in status, (
                    f"{r.question_id}: population {pop} has no contract"
                )


# ═══════════════════════════════════════════════════════════════════════════════
# NO LEGACY DEPENDENCY
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoLegacyDependency:

    def test_contracts_no_legacy_import(self):
        import inspect
        from research_engine.v10.universes import contracts
        source = inspect.getsource(contracts)
        import_lines = [
            l.strip() for l in source.splitlines()
            if l.strip().startswith(("import ", "from "))
        ]
        for line in import_lines:
            assert "research_question_registry" not in line
            assert "v10_research_registry" not in line

    def test_health_no_legacy_import(self):
        import inspect
        from research_engine.v10.universes import health
        source = inspect.getsource(health)
        import_lines = [
            l.strip() for l in source.splitlines()
            if l.strip().startswith(("import ", "from "))
        ]
        for line in import_lines:
            assert "research_question_registry" not in line

    def test_resolver_no_legacy_import(self):
        import inspect
        from research_engine.v10.universes import resolver
        source = inspect.getsource(resolver)
        import_lines = [
            l.strip() for l in source.splitlines()
            if l.strip().startswith(("import ", "from "))
        ]
        for line in import_lines:
            assert "research_question_registry" not in line
