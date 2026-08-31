"""
Schema Version Normalisation — Tests for 8 newly-versioned datasets.

Validates that _SCHEMA_VERSION constants exist and match expected values.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest


class TestDecisionAuditSchema:
    def test_schema_version_constant(self):
        from core.decision_audit import _SCHEMA_VERSION
        assert _SCHEMA_VERSION == "decision_audit_v1"


class TestDecisionLedgerSchema:
    def test_schema_version_constant(self):
        from core.decision_ledger import _SCHEMA_VERSION
        assert _SCHEMA_VERSION == "decision_ledger_v1"

    def test_build_ledger_entry_contains_schema(self):
        from core.decision_ledger import build_ledger_entry, DecisionOutcome
        entry = build_ledger_entry(
            symbol="EURUSD", cycle_id=1,
            decision=DecisionOutcome.NO_TRADE, reason="test",
        )
        assert entry["schema_version"] == "decision_ledger_v1"


class TestDecisionTraceSchema:
    def test_schema_version_constant(self):
        from core.decision_trace import _SCHEMA_VERSION
        assert _SCHEMA_VERSION == "decision_trace_v1"


class TestExecutionContextSchema:
    def test_schema_version_constant(self):
        from core.execution_context import _SCHEMA_VERSION
        assert _SCHEMA_VERSION == "execution_context_v1"


class TestExecutionResultsSchema:
    def test_schema_version_constant(self):
        from core.persistence.execution_result_writer import _SCHEMA_VERSION
        assert _SCHEMA_VERSION == "execution_results_v1"


class TestOpportunityAssessmentSchema:
    def test_schema_version_constant(self):
        from core.persistence.opportunity_assessment_writer import _SCHEMA_VERSION
        assert _SCHEMA_VERSION == "opportunity_assessment_v1"


class TestAssessmentsSchema:
    def test_schema_version_constant(self):
        from core.assessment.persistence import _SCHEMA_VERSION
        # Fallback schema version for records without one (Assessment model has its own)
        assert _SCHEMA_VERSION == "assessments_v1"

    def test_assessment_model_has_own_schema(self):
        """Assessment model already carries schema_version='assessment_v1'."""
        from core.assessment.assessment import SCHEMA_VERSION
        assert SCHEMA_VERSION == "assessment_v1"


class TestLearningSchema:
    def test_schema_version_constant(self):
        from core.learning.store import _SCHEMA_VERSION
        assert _SCHEMA_VERSION == "learning_v1"


class TestManagementActionsSchema:
    def test_schema_version_constant(self):
        from core.persistence.management_actions_writer import _SCHEMA_VERSION
        assert _SCHEMA_VERSION == "management_actions_v1"
