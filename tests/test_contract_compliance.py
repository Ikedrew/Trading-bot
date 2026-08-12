"""
Tests for Contract Compliance Validation (Item 7).

Verifies that the compliance validator:
- Executes all rules
- Produces deterministic results
- Correctly detects PASS/FAIL/WARNING/INCONCLUSIVE
- Reports violations explicitly
- Never modifies system state
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research_engine.v10.compliance.model import (
    CheckStatus,
    ContractCheck,
    ContractComplianceReport,
)
from research_engine.v10.compliance.rules import (
    ALL_RULES,
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
)
from research_engine.v10.compliance.validator import ContractComplianceValidator


# ═══════════════════════════════════════════════════════════════════════════════
# INDIVIDUAL RULE TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestUniverseRules:

    def test_universe_enum_complete(self):
        check = check_universe_enum_complete()
        assert check.status == CheckStatus.PASS
        assert check.check_id == "UNIVERSE_001"

    def test_universe_builders_registered(self):
        check = check_universe_builders_registered()
        assert check.status == CheckStatus.PASS

    def test_universe_contracts_complete(self):
        check = check_universe_contracts_complete()
        assert check.status == CheckStatus.PASS


class TestCrossUniverseRules:

    def test_cross_universe_importable(self):
        check = check_cross_universe_interface_importable()
        assert check.status == CheckStatus.PASS

    def test_proposal_governance(self):
        check = check_proposal_governance()
        assert check.status == CheckStatus.PASS


class TestProvenanceRules:

    def test_vocabulary_complete(self):
        check = check_provenance_vocabulary_complete()
        assert check.status == CheckStatus.PASS

    def test_validator_importable(self):
        check = check_provenance_validator_importable()
        assert check.status == CheckStatus.PASS

    def test_counterfactual_protection(self):
        check = check_counterfactual_protection()
        assert check.status == CheckStatus.PASS


class TestVersionRules:

    def test_version_concepts_distinct(self):
        check = check_version_concepts_distinct()
        assert check.status == CheckStatus.PASS


class TestLifecycleRules:

    def test_trace_hash_determinism(self):
        check = check_lifecycle_trace_hash_determinism()
        assert check.status == CheckStatus.PASS

    def test_persistence_importable(self):
        check = check_lifecycle_persistence_importable()
        assert check.status == CheckStatus.PASS


class TestResearchRules:

    def test_finding_lineage_fields(self):
        check = check_research_finding_lineage_fields()
        assert check.status == CheckStatus.PASS

    def test_insufficient_evidence_handling(self):
        check = check_research_insufficient_evidence_handling()
        assert check.status == CheckStatus.PASS


# ═══════════════════════════════════════════════════════════════════════════════
# VALIDATOR TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidator:

    def test_validate_runs_all_rules(self):
        validator = ContractComplianceValidator()
        report = validator.validate()
        assert len(report.checks) == len(ALL_RULES)

    def test_validate_deterministic(self):
        validator = ContractComplianceValidator()
        r1 = validator.validate()
        r2 = validator.validate()
        assert r1.status == r2.status
        assert r1.pass_count == r2.pass_count
        assert r1.fail_count == r2.fail_count

    def test_report_computes_status(self):
        validator = ContractComplianceValidator()
        report = validator.validate()
        assert report.status in ("COMPLIANT", "PARTIALLY_COMPLIANT", "NON_COMPLIANT", "INCONCLUSIVE")

    def test_all_checks_pass(self):
        """Full compliance — all rules should pass against current architecture."""
        validator = ContractComplianceValidator()
        report = validator.validate()
        failures = [c for c in report.checks if c.status == CheckStatus.FAIL]
        assert len(failures) == 0, f"Failures: {[f.check_id + ': ' + f.violation for f in failures]}"
        assert report.status == "COMPLIANT"

    def test_summary(self):
        validator = ContractComplianceValidator()
        s = validator.summary()
        assert "status" in s
        assert "total" in s
        assert s["total"] == len(ALL_RULES)

    def test_to_dict_serializable(self):
        validator = ContractComplianceValidator()
        report = validator.validate()
        d = report.to_dict()
        assert "checks" in d
        assert "status" in d
        assert isinstance(d["checks"], list)


# ═══════════════════════════════════════════════════════════════════════════════
# MODEL TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestModel:

    def test_check_to_dict(self):
        c = ContractCheck(
            check_id="TEST_001", category="TEST", status=CheckStatus.PASS,
            description="test check",
        )
        d = c.to_dict()
        assert d["check_id"] == "TEST_001"
        assert d["status"] == "PASS"

    def test_report_compute_status_compliant(self):
        report = ContractComplianceReport()
        report.checks = [
            ContractCheck(check_id="A", category="X", status=CheckStatus.PASS, description=""),
            ContractCheck(check_id="B", category="X", status=CheckStatus.PASS, description=""),
        ]
        report.compute_status()
        assert report.status == "COMPLIANT"

    def test_report_compute_status_non_compliant(self):
        report = ContractComplianceReport()
        report.checks = [
            ContractCheck(check_id="A", category="X", status=CheckStatus.PASS, description=""),
            ContractCheck(check_id="B", category="X", status=CheckStatus.FAIL, description="", violation="bad"),
        ]
        report.compute_status()
        assert report.status == "NON_COMPLIANT"

    def test_report_compute_status_partially_compliant(self):
        report = ContractComplianceReport()
        report.checks = [
            ContractCheck(check_id="A", category="X", status=CheckStatus.PASS, description=""),
            ContractCheck(check_id="B", category="X", status=CheckStatus.WARNING, description=""),
        ]
        report.compute_status()
        assert report.status == "PARTIALLY_COMPLIANT"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v", "--tb=short", "-p", "no:conftest"]))
