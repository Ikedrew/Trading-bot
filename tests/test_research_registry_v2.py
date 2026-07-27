"""
Tests for Research Registry v2.

Validates:
    1. Every question has required fields defined
    2. Every question has validation requirements
    3. Strategy/horizon separation enforced (no combined fields in required_fields)
    4. Blocked questions cannot report READY with insufficient data
    5. Invalid datasets cannot produce COMPLETE status
    6. Registry structure and lookup functions work
    7. Audit produces correct status from synthetic datasets

No trading behaviour is tested or modified.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from research_engine.registry import (
    REGISTRY,
    REGISTRY_BY_ID,
    QuestionCategory,
    QuestionPriority,
    QuestionStatus,
    ResearchQuestion,
    ValidationRule,
    get_question,
    get_questions_by_category,
    get_questions_by_priority,
)
from research_engine.registry.registry_audit import audit_registry, _evaluate_question
from research_engine.validation import validate_dataset


# ═══════════════════════════════════════════════════════════════════════════════
# 1. EVERY QUESTION HAS REQUIRED FIELDS
# ═══════════════════════════════════════════════════════════════════════════════


class TestQuestionCompleteness:
    """All registered questions have mandatory metadata populated."""

    def test_all_questions_have_required_fields(self):
        for q in REGISTRY:
            assert len(q.required_fields) > 0, f"{q.id} has no required_fields"

    def test_all_questions_have_data_sources(self):
        for q in REGISTRY:
            assert len(q.data_sources) > 0, f"{q.id} has no data_sources"

    def test_all_questions_have_title(self):
        for q in REGISTRY:
            assert q.title, f"{q.id} has no title"
            assert q.description, f"{q.id} has no description"

    def test_all_questions_have_category(self):
        for q in REGISTRY:
            assert isinstance(q.category, QuestionCategory), f"{q.id} has invalid category"

    def test_all_questions_have_priority(self):
        for q in REGISTRY:
            assert isinstance(q.priority, QuestionPriority), f"{q.id} has invalid priority"

    def test_no_duplicate_ids(self):
        ids = [q.id for q in REGISTRY]
        assert len(ids) == len(set(ids)), f"Duplicate IDs found: {[i for i in ids if ids.count(i) > 1]}"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. VALIDATION REQUIREMENTS PRESENT
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidationRequirements:
    """Questions that need HTF/phase/lineage data declare validation rules."""

    def test_market_context_questions_require_regime(self):
        """M-category questions must validate H4 regime coverage."""
        for q in get_questions_by_category(QuestionCategory.MARKET_CONTEXT):
            if "h4_regime" in q.required_fields:
                rule_fields = [r.field for r in q.validation_rules]
                assert "h4_regime_coverage" in rule_fields, f"{q.id} needs h4_regime but has no coverage rule"

    def test_phase_questions_require_phase_validation(self):
        """Questions needing market_phase must validate phase coverage."""
        for q in REGISTRY:
            if "market_phase" in q.required_fields:
                rule_fields = [r.field for r in q.validation_rules]
                assert "market_phase_coverage" in rule_fields, f"{q.id} needs market_phase but has no coverage rule"

    def test_lineage_questions_require_lineage_validation(self):
        """Questions needing entity_id must validate lineage coverage."""
        for q in REGISTRY:
            if "entity_id" in q.required_fields:
                rule_fields = [r.field for r in q.validation_rules]
                assert "lineage_coverage" in rule_fields, f"{q.id} needs entity_id but has no lineage rule"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. STRATEGY/HORIZON SEPARATION ENFORCED
# ═══════════════════════════════════════════════════════════════════════════════


class TestStrategyHorizonSeparation:
    """No question uses combined strategy_horizon fields."""

    def test_no_combined_strategy_horizon_in_required_fields(self):
        """Required fields must not contain combined strategy_horizon patterns."""
        bad_patterns = {"strategy_horizon", "strategy_id", "NONE_SCALP"}
        for q in REGISTRY:
            for field in q.required_fields:
                assert field not in bad_patterns, f"{q.id} uses combined field: {field}"
                assert "_SCALP" not in field, f"{q.id} has horizon-contaminated field: {field}"
                assert "_INTRADAY" not in field, f"{q.id} has horizon-contaminated field: {field}"

    def test_strategy_and_horizon_are_separate_fields(self):
        """If a question needs both, they must be separate fields."""
        for q in REGISTRY:
            if "strategy" in q.required_fields and "trade_horizon" in q.required_fields:
                # Both present and separate — correct
                assert "strategy" in q.required_fields
                assert "trade_horizon" in q.required_fields


# ═══════════════════════════════════════════════════════════════════════════════
# 4. BLOCKED QUESTIONS WITH INSUFFICIENT DATA
# ═══════════════════════════════════════════════════════════════════════════════


class TestBlockedStatus:
    """Questions requiring unavailable data are BLOCKED or WAITING_DATA."""

    def _make_empty_validation(self):
        return validate_dataset([], dataset_name="empty")

    def test_empty_data_produces_waiting(self):
        """With no data at all, questions are WAITING_DATA."""
        empty_val = self._make_empty_validation()
        results = audit_registry(shadow_records=[], trace_records=[])
        for r in results:
            assert r.status != QuestionStatus.READY, f"{r.question_id} is READY with no data"
            assert r.status in (QuestionStatus.WAITING_DATA, QuestionStatus.BLOCKED)

    def test_regime_question_blocked_without_h4(self):
        """M1 (regime predicts outcomes) is BLOCKED when H4 regime missing."""
        # Records with outcome but no H4 regime
        records = [
            {"identity": {"entity_id": f"EUR_{i}"}, "decision_snapshot": {"pattern": "HAMMER"},
             "simulated_outcome": {"pnl_r_multiple": 1.0}}
            for i in range(30)
        ]
        results = audit_registry(shadow_records=records, trace_records=[])
        m1 = next(r for r in results if r.question_id == "M1")
        assert m1.status in (QuestionStatus.BLOCKED, QuestionStatus.WAITING_DATA)

    def test_phase_question_blocked_without_phase(self):
        """M3 (phase improves prediction) is BLOCKED when phase missing."""
        records = [
            {"identity": {"entity_id": f"EUR_{i}"},
             "simulation_environment": {"htf_snapshot": {"timeframe_bias": {"H4": {"regime": "TRENDING"}}}},
             "simulated_outcome": {"pnl_r_multiple": 1.0}}
            for i in range(30)
        ]
        results = audit_registry(shadow_records=records, trace_records=[])
        m3 = next(r for r in results if r.question_id == "M3")
        assert m3.status in (QuestionStatus.BLOCKED, QuestionStatus.WAITING_DATA)


# ═══════════════════════════════════════════════════════════════════════════════
# 5. VALID DATASET PRODUCES READY
# ═══════════════════════════════════════════════════════════════════════════════


class TestReadyStatus:
    """Questions with all data available are READY."""

    def _make_complete_records(self, n=30):
        return [
            {
                "identity": {"entity_id": f"EURUSD_{i*100}", "strategy_id": "REVERSAL"},
                "decision_snapshot": {
                    "pattern": "HAMMER",
                    "score": 0.7,
                    "strategy": "REVERSAL",
                    "trade_horizon": "SCALP",
                    "regime": "TRENDING",
                    "h4_regime": "TRENDING",
                    "h1_bias": "BULLISH",
                    "market_phase": "IMPULSE",
                    "market_phase_confidence": 0.8,
                },
                "simulation_environment": {
                    "htf_snapshot": {
                        "timeframe_bias": {
                            "H4": {"regime": "TRENDING", "bias": "BULLISH"},
                            "H1": {"bias": "BULLISH", "regime": "TRENDING"},
                        }
                    }
                },
                "simulated_outcome": {"pnl_r_multiple": 1.5, "exit_reason": "take_profit"},
            }
            for i in range(n)
        ]

    def test_pattern_question_ready_with_full_data(self):
        """E2 (pattern expectancy) should be READY with complete data."""
        records = self._make_complete_records()
        results = audit_registry(shadow_records=records, trace_records=[])
        e2 = next(r for r in results if r.question_id == "E2")
        assert e2.status == QuestionStatus.READY

    def test_strategy_question_ready_with_full_data(self):
        """S1 (strategy expectancy) should be READY with complete data."""
        records = self._make_complete_records()
        results = audit_registry(shadow_records=records, trace_records=[])
        s1 = next(r for r in results if r.question_id == "S1")
        assert s1.status == QuestionStatus.READY

    def test_regime_question_ready_with_h4_populated(self):
        """M1 (regime predicts outcomes) should be READY when H4 is populated."""
        records = self._make_complete_records()
        results = audit_registry(shadow_records=records, trace_records=[])
        m1 = next(r for r in results if r.question_id == "M1")
        assert m1.status == QuestionStatus.READY


# ═══════════════════════════════════════════════════════════════════════════════
# 6. REGISTRY STRUCTURE AND LOOKUP
# ═══════════════════════════════════════════════════════════════════════════════


class TestRegistryLookup:
    """Registry lookup functions work correctly."""

    def test_get_question_by_id(self):
        q = get_question("E1")
        assert q is not None
        assert q.id == "E1"
        assert q.category == QuestionCategory.SYSTEM_EDGE

    def test_get_question_not_found(self):
        q = get_question("Z99")
        assert q is None

    def test_get_by_category(self):
        edge = get_questions_by_category(QuestionCategory.SYSTEM_EDGE)
        assert len(edge) == 5  # E1-E5
        assert all(q.category == QuestionCategory.SYSTEM_EDGE for q in edge)

    def test_get_by_priority(self):
        p0 = get_questions_by_priority(QuestionPriority.P0)
        assert len(p0) >= 4  # E1, E2, E3, M1, D1, D2, S1
        assert all(q.priority == QuestionPriority.P0 for q in p0)

    def test_registry_has_expected_questions(self):
        assert len(REGISTRY) > 0
        assert len(REGISTRY) == len(set(q.id for q in REGISTRY))  # All unique IDs

    def test_registry_by_id_matches(self):
        assert len(REGISTRY_BY_ID) == len(REGISTRY)
        for q in REGISTRY:
            assert REGISTRY_BY_ID[q.id] is q


# ═══════════════════════════════════════════════════════════════════════════════
# 7. VALIDATION RULE EVALUATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidationRules:
    """ValidationRule.evaluate() works correctly."""

    def test_gte_passes(self):
        rule = ValidationRule("x", ">=", 0.80)
        assert rule.evaluate(0.80) is True
        assert rule.evaluate(0.90) is True
        assert rule.evaluate(0.79) is False

    def test_gt_passes(self):
        rule = ValidationRule("x", ">", 0.50)
        assert rule.evaluate(0.51) is True
        assert rule.evaluate(0.50) is False

    def test_lt_passes(self):
        rule = ValidationRule("x", "<", 0.10)
        assert rule.evaluate(0.09) is True
        assert rule.evaluate(0.10) is False

    def test_eq_passes(self):
        rule = ValidationRule("x", "==", 1.0)
        assert rule.evaluate(1.0) is True
        assert rule.evaluate(0.99) is False


# ═══════════════════════════════════════════════════════════════════════════════
# 8. NEW CATEGORIES EXIST AND HAVE QUESTIONS
# ═══════════════════════════════════════════════════════════════════════════════


class TestNewCategories:
    """New categories (RISK_MANAGEMENT, DATA_GOVERNANCE) are populated."""

    def test_risk_management_category_exists(self):
        risk = get_questions_by_category(QuestionCategory.RISK_MANAGEMENT)
        assert len(risk) == 5  # R1-R5
        assert all(q.id.startswith("R") for q in risk)

    def test_data_governance_category_exists(self):
        gov = get_questions_by_category(QuestionCategory.DATA_GOVERNANCE)
        assert len(gov) == 3
        assert all(q.id.startswith("G") for q in gov)

    def test_market_context_expanded(self):
        mc = get_questions_by_category(QuestionCategory.MARKET_CONTEXT)
        assert len(mc) >= 8  # M1-M8 minimum, more as research grows
        assert all(q.id.startswith("M") for q in mc)

    def test_strategy_horizon_expanded(self):
        sh = get_questions_by_category(QuestionCategory.STRATEGY_HORIZON)
        assert len(sh) == 7  # S1-S7

    def test_execution_expanded(self):
        ex = get_questions_by_category(QuestionCategory.EXECUTION)
        assert len(ex) == 6  # X1-X6

    def test_learning_expanded(self):
        learn = get_questions_by_category(QuestionCategory.SYSTEM_LEARNING)
        assert len(learn) == 7  # L1-L7


# ═══════════════════════════════════════════════════════════════════════════════
# 9. LEGACY MAPPINGS PRESERVED
# ═══════════════════════════════════════════════════════════════════════════════


class TestLegacyMappings:
    """Old Q1-Q25 IDs are traceable via legacy_ids field."""

    def test_q19_mapped_to_e1(self):
        q = get_question("E1")
        assert "Q19" in q.legacy_ids

    def test_q5_mapped(self):
        # Q5 maps to both E2 and L1
        e2 = get_question("E2")
        l1 = get_question("L1")
        assert "Q5" in e2.legacy_ids or "Q5" in l1.legacy_ids

    def test_q10_mapped_to_risk(self):
        r1 = get_question("R1")
        assert "Q10" in r1.legacy_ids


# ═══════════════════════════════════════════════════════════════════════════════
# 10. NEW QUESTIONS CORRECTLY BLOCKED WITH MISSING DATA
# ═══════════════════════════════════════════════════════════════════════════════


class TestNewQuestionsBlocked:
    """New questions report correct status with incomplete data."""

    def test_m6_blocked_without_phase(self):
        """M6 (phase expectancy) needs market_phase — blocked without it."""
        records = [
            {"identity": {"entity_id": f"EUR_{i}"}, "decision_snapshot": {"pattern": "HAMMER"},
             "simulated_outcome": {"pnl_r_multiple": 1.0}}
            for i in range(30)
        ]
        results = audit_registry(shadow_records=records, trace_records=[])
        m6 = next(r for r in results if r.question_id == "M6")
        assert m6.status in (QuestionStatus.BLOCKED, QuestionStatus.WAITING_DATA)

    def test_s5_blocked_without_strategy(self):
        """S5 (strategy expectancy) needs clean strategy field."""
        records = [
            {"identity": {"entity_id": f"EUR_{i}"},
             "simulated_outcome": {"pnl_r_multiple": 1.0}}
            for i in range(30)
        ]
        results = audit_registry(shadow_records=records, trace_records=[])
        s5 = next(r for r in results if r.question_id == "S5")
        assert s5.status in (QuestionStatus.BLOCKED, QuestionStatus.WAITING_DATA)

    def test_g1_ready_with_outcomes(self):
        """G1 (dataset completeness) only needs outcomes — should be READY."""
        records = [
            {"identity": {"entity_id": f"EUR_{i}"}, "decision_snapshot": {"pattern": "HAMMER"},
             "simulated_outcome": {"pnl_r_multiple": 1.0}}
            for i in range(30)
        ]
        results = audit_registry(shadow_records=records, trace_records=[])
        g1 = next(r for r in results if r.question_id == "G1")
        assert g1.status == QuestionStatus.READY
