"""
Tests for the new-engine canonical question bank.

Validates:
    - Structural integrity of all questions
    - Four-angle coverage requirements
    - ID uniqueness and format
    - Cross-angle join consistency
    - Population and view declarations
    - No runtime dependency on old registries
"""

import sys
sys.path.insert(0, ".")

import pytest

from research_engine.v10.universes.models import (
    AnalysisType,
    AngleRequirement,
    JoinRequirement,
    JoinType,
    NewEngineQuestion,
    Population,
    QuestionStatus,
    Universe,
    ViewType,
)
from research_engine.v10.universes.question_bank import (
    QUESTION_BANK,
    QUESTION_BANK_BY_ID,
    get_cross_angle_questions,
    get_question,
    get_questions_by_status,
    get_questions_by_universe,
    get_questions_requiring_join,
    get_questions_with_view,
    get_ready_questions,
    get_single_angle_questions,
)


# ═══════════════════════════════════════════════════════════════════════════════
# STRUCTURAL INTEGRITY
# ═══════════════════════════════════════════════════════════════════════════════


class TestStructuralIntegrity:
    """Every question must have valid structure."""

    def test_question_bank_is_not_empty(self):
        assert len(QUESTION_BANK) >= 40, "Question bank should have 40+ questions"

    def test_all_ids_unique(self):
        ids = [q.question_id for q in QUESTION_BANK]
        assert len(ids) == len(set(ids)), "Duplicate question IDs found"

    def test_by_id_dict_matches_tuple(self):
        assert len(QUESTION_BANK_BY_ID) == len(QUESTION_BANK)
        for q in QUESTION_BANK:
            assert q.question_id in QUESTION_BANK_BY_ID
            assert QUESTION_BANK_BY_ID[q.question_id] is q

    def test_every_question_has_required_fields(self):
        for q in QUESTION_BANK:
            assert q.question_id, f"Missing question_id"
            assert q.title, f"{q.question_id}: Missing title"
            assert q.research_intent, f"{q.question_id}: Missing research_intent"
            assert q.required_universes, f"{q.question_id}: Missing required_universes"
            assert q.required_populations, f"{q.question_id}: Missing required_populations"
            assert q.views, f"{q.question_id}: Missing views"
            assert q.decision_enabled, f"{q.question_id}: Missing decision_enabled"

    def test_every_question_has_angle_requirements(self):
        for q in QUESTION_BANK:
            assert q.angle_requirements, (
                f"{q.question_id}: Missing angle_requirements"
            )

    def test_status_is_valid_enum(self):
        for q in QUESTION_BANK:
            assert isinstance(q.status, QuestionStatus), (
                f"{q.question_id}: Invalid status type"
            )

    def test_analysis_type_is_valid_enum(self):
        for q in QUESTION_BANK:
            assert isinstance(q.analysis_type, AnalysisType), (
                f"{q.question_id}: Invalid analysis_type"
            )

    def test_minimum_sample_size_positive(self):
        for q in QUESTION_BANK:
            assert q.minimum_sample_size > 0, (
                f"{q.question_id}: minimum_sample_size must be positive"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# ID FORMAT VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestIDFormat:
    """IDs must follow the new-engine naming convention."""

    VALID_PREFIXES = {
        "E-", "D-", "M-", "S-",
        "ED-", "EM-", "ES-",
        "DM-", "DS-", "MS-",
        "EDM-", "EDS-", "DMS-",
        "EDMS-",
    }

    def test_all_ids_have_valid_prefix(self):
        for q in QUESTION_BANK:
            prefix = q.question_id.rsplit("-", 1)[0] + "-"
            assert prefix in self.VALID_PREFIXES, (
                f"{q.question_id}: Invalid prefix '{prefix}'"
            )

    def test_all_ids_have_numeric_suffix(self):
        for q in QUESTION_BANK:
            suffix = q.question_id.rsplit("-", 1)[1]
            assert suffix.isdigit(), (
                f"{q.question_id}: Suffix must be numeric, got '{suffix}'"
            )

    def test_id_prefix_matches_universes(self):
        """The ID prefix should correspond to the declared universes."""
        prefix_to_universes = {
            "E": {Universe.EXECUTION},
            "D": {Universe.DECISION},
            "M": {Universe.MARKET},
            "S": {Universe.STRATEGY},
        }
        for q in QUESTION_BANK:
            prefix = q.question_id.rsplit("-", 1)[0]
            expected_universes = set()
            for char in prefix:
                if char in prefix_to_universes:
                    expected_universes.update(prefix_to_universes[char])
            actual = set(q.required_universes)
            assert actual == expected_universes, (
                f"{q.question_id}: prefix implies {expected_universes} "
                f"but declared {actual}"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# FOUR-ANGLE COVERAGE
# ═══════════════════════════════════════════════════════════════════════════════


class TestFourAngleCoverage:
    """Ensure all four angles have adequate representation."""

    def test_execution_universe_has_questions(self):
        qs = get_questions_by_universe(Universe.EXECUTION)
        assert len(qs) >= 10, f"EXECUTION has only {len(qs)} questions"

    def test_decision_universe_has_questions(self):
        qs = get_questions_by_universe(Universe.DECISION)
        assert len(qs) >= 10, f"DECISION has only {len(qs)} questions"

    def test_market_universe_has_questions(self):
        qs = get_questions_by_universe(Universe.MARKET)
        assert len(qs) >= 10, f"MARKET has only {len(qs)} questions"

    def test_strategy_universe_has_questions(self):
        qs = get_questions_by_universe(Universe.STRATEGY)
        assert len(qs) >= 8, f"STRATEGY has only {len(qs)} questions"

    def test_cross_angle_questions_exist(self):
        qs = get_cross_angle_questions()
        assert len(qs) >= 10, f"Only {len(qs)} cross-angle questions"

    def test_all_universe_pairs_covered(self):
        """Every pair of universes must have at least one question spanning both."""
        universes = list(Universe)
        for i, u1 in enumerate(universes):
            for u2 in universes[i + 1:]:
                covering = [
                    q for q in QUESTION_BANK
                    if u1 in q.required_universes and u2 in q.required_universes
                ]
                assert covering, (
                    f"No question covers {u1.value} + {u2.value}"
                )

    def test_at_least_one_four_angle_question(self):
        four_angle = [q for q in QUESTION_BANK if q.angle_count == 4]
        assert four_angle, "No four-angle questions exist"

    def test_anomaly_view_coverage(self):
        qs = get_questions_with_view(ViewType.ANOMALOUS)
        assert len(qs) >= 10, f"Only {len(qs)} questions with ANOMALOUS view"

    def test_exceptional_view_coverage(self):
        qs = get_questions_with_view(ViewType.EXCEPTIONAL)
        assert len(qs) >= 5, f"Only {len(qs)} questions with EXCEPTIONAL view"


# ═══════════════════════════════════════════════════════════════════════════════
# JOIN CONSISTENCY
# ═══════════════════════════════════════════════════════════════════════════════


class TestJoinConsistency:
    """Cross-angle questions must declare proper joins."""

    def test_cross_angle_questions_have_joins(self):
        """Every question using 2+ universes must declare at least one join."""
        for q in get_cross_angle_questions():
            assert q.required_joins, (
                f"{q.question_id}: cross-angle but no required_joins declared"
            )

    def test_join_universes_are_in_required(self):
        """Joins must reference universes that are in required_universes."""
        for q in QUESTION_BANK:
            for j in q.required_joins:
                assert j.from_universe in q.required_universes, (
                    f"{q.question_id}: join from {j.from_universe.value} "
                    f"not in required_universes"
                )
                assert j.to_universe in q.required_universes, (
                    f"{q.question_id}: join to {j.to_universe.value} "
                    f"not in required_universes"
                )

    def test_joins_connect_different_universes(self):
        """A join must connect two different universes."""
        for q in QUESTION_BANK:
            for j in q.required_joins:
                assert j.from_universe != j.to_universe, (
                    f"{q.question_id}: join from/to same universe "
                    f"({j.from_universe.value})"
                )

    def test_join_type_is_valid(self):
        for q in QUESTION_BANK:
            for j in q.required_joins:
                assert isinstance(j.join_type, JoinType), (
                    f"{q.question_id}: invalid join type"
                )


# ═══════════════════════════════════════════════════════════════════════════════
# POPULATION CONSISTENCY
# ═══════════════════════════════════════════════════════════════════════════════


class TestPopulationConsistency:
    """Populations must be valid and consistent with universes."""

    def test_required_populations_not_empty(self):
        for q in QUESTION_BANK:
            assert q.required_populations, (
                f"{q.question_id}: required_populations is empty"
            )

    def test_populations_are_valid_enum(self):
        for q in QUESTION_BANK:
            for p in q.required_populations:
                assert isinstance(p, Population), (
                    f"{q.question_id}: invalid population {p}"
                )

    def test_angle_requirement_populations_are_valid(self):
        for q in QUESTION_BANK:
            for ar in q.angle_requirements:
                for p in ar.populations:
                    assert isinstance(p, Population), (
                        f"{q.question_id}: invalid population in "
                        f"angle requirement for {ar.universe.value}"
                    )

    def test_angle_requirements_match_universes(self):
        """Each angle requirement's universe must be in required_universes."""
        for q in QUESTION_BANK:
            for ar in q.angle_requirements:
                assert ar.universe in q.required_universes, (
                    f"{q.question_id}: angle requirement for "
                    f"{ar.universe.value} not in required_universes"
                )


# ═══════════════════════════════════════════════════════════════════════════════
# DEPENDENCY CONSISTENCY
# ═══════════════════════════════════════════════════════════════════════════════


class TestDependencyConsistency:
    """Dependencies must reference existing questions."""

    def test_dependencies_reference_existing_ids(self):
        all_ids = set(q.question_id for q in QUESTION_BANK)
        for q in QUESTION_BANK:
            for dep in q.dependencies:
                assert dep in all_ids, (
                    f"{q.question_id}: depends on '{dep}' which does not exist"
                )

    def test_no_self_dependency(self):
        for q in QUESTION_BANK:
            assert q.question_id not in q.dependencies, (
                f"{q.question_id}: depends on itself"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# ACCESSOR FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════


class TestAccessorFunctions:
    """Test that accessor functions work correctly."""

    def test_get_question_found(self):
        q = get_question("E-001")
        assert q is not None
        assert q.question_id == "E-001"

    def test_get_question_not_found(self):
        q = get_question("NONEXISTENT")
        assert q is None

    def test_get_questions_by_universe(self):
        qs = get_questions_by_universe(Universe.EXECUTION)
        assert all(Universe.EXECUTION in q.required_universes for q in qs)

    def test_get_questions_by_status(self):
        qs = get_questions_by_status(QuestionStatus.READY)
        assert all(q.status == QuestionStatus.READY for q in qs)
        assert len(qs) > 0

    def test_get_cross_angle_questions(self):
        qs = get_cross_angle_questions()
        assert all(q.angle_count > 1 for q in qs)

    def test_get_single_angle_questions(self):
        qs = get_single_angle_questions()
        assert all(q.angle_count == 1 for q in qs)

    def test_get_ready_questions(self):
        qs = get_ready_questions()
        assert all(q.status == QuestionStatus.READY for q in qs)

    def test_get_questions_with_view(self):
        qs = get_questions_with_view(ViewType.ANOMALOUS)
        assert all(ViewType.ANOMALOUS in q.views for q in qs)

    def test_get_questions_requiring_join(self):
        qs = get_questions_requiring_join(Universe.DECISION, Universe.EXECUTION)
        assert len(qs) > 0
        for q in qs:
            assert any(
                j.from_universe == Universe.DECISION
                and j.to_universe == Universe.EXECUTION
                for j in q.required_joins
            )


# ═══════════════════════════════════════════════════════════════════════════════
# SERIALISATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestSerialisation:
    """Questions must serialise to valid dicts."""

    def test_to_dict_returns_dict(self):
        for q in QUESTION_BANK:
            d = q.to_dict()
            assert isinstance(d, dict), f"{q.question_id}: to_dict() failed"

    def test_to_dict_has_required_keys(self):
        required_keys = {
            "question_id", "title", "research_intent", "angles",
            "required_universes", "required_populations", "required_joins",
            "views", "analysis_type", "minimum_sample_size", "dependencies",
            "status", "source_intent", "decision_enabled",
        }
        for q in QUESTION_BANK:
            d = q.to_dict()
            missing = required_keys - set(d.keys())
            assert not missing, (
                f"{q.question_id}: to_dict() missing keys: {missing}"
            )

    def test_angles_dict_has_four_booleans(self):
        for q in QUESTION_BANK:
            d = q.to_dict()
            angles = d["angles"]
            assert set(angles.keys()) == {"execution", "decision", "market", "strategy"}
            for v in angles.values():
                assert isinstance(v, bool)


# ═══════════════════════════════════════════════════════════════════════════════
# NO LEGACY DEPENDENCY
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoLegacyDependency:
    """The new question bank must NOT import from old registries at runtime."""

    def test_no_import_of_old_registry(self):
        """Verify question_bank.py does not import from legacy registries."""
        import inspect
        from research_engine.v10.universes import question_bank as qb_module

        source = inspect.getsource(qb_module)
        # Check for actual import statements, not documentation mentions
        import_lines = [
            line.strip() for line in source.splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        for line in import_lines:
            assert "research_question_registry" not in line, (
                f"Legacy import found: {line}"
            )
            assert "v10_research_registry" not in line, (
                f"Legacy import found: {line}"
            )
            assert "research_intelligence.question_registry" not in line, (
                f"Legacy import found: {line}"
            )

    def test_no_import_of_old_models(self):
        """Verify models.py does not import from legacy registry models."""
        import inspect
        from research_engine.v10.universes import models as models_module

        source = inspect.getsource(models_module)
        import_lines = [
            line.strip() for line in source.splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        for line in import_lines:
            assert "research_question_models" not in line, (
                f"Legacy import found: {line}"
            )
            assert "V10ResearchQuestion" not in line, (
                f"Legacy import found: {line}"
            )
            assert "QuestionDefinition" not in line, (
                f"Legacy import found: {line}"
            )
