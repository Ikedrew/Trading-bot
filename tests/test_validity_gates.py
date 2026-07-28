"""
Tests for Research Validity Gates.

Verifies:
    - Invalid experiments cannot produce promotions
    - Missing metadata blocks validation
    - Valid experiments pass all gates
    - Epoch contamination blocks promotion
    - Sample size requirements enforced
"""

import pytest

from research_engine.validity_gates import (
    GATED_STATUSES,
    MIN_SAMPLE_SIZE,
    VALID_EPOCHS,
    GateResult,
    ValidityAssessment,
    validate_experiment_report,
)


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════


def _valid_report(recommendation="PROMOTE"):
    """A report that should pass all gates."""
    return {
        "question_id": "TEST1",
        "status": "COMPLETE",
        "recommendation": recommendation,
        "overall": {
            "experiment": "TEST",
            "control": {"SL": 1.0, "variable": "TP"},
            "variants": {"0.5": {"ev": 0.03}},
            "finding": "Test finding",
            "comparisons": {"0.5": {"vs_control": {"significant_05": True}}},
        },
        "confidence": "HIGH",
        "dataset": {"source": "shadow_trades_current", "sample_size": 200},
        "fingerprint": {
            "epoch": "CURRENT",
            "architecture_version": "new_pipeline_v1.2",
            "records_used": 200,
        },
        "warnings": [],
        "epoch": "CURRENT",
    }


def _invalid_epoch_report():
    """A report with ALL/MIXED epoch — should fail."""
    return {
        "question_id": "BAD1",
        "recommendation": "PROMOTE",
        "overall": {"finding": "looks good"},
        "dataset": {"source": "shadow_trades", "sample_size": 500},
        "fingerprint": {"epoch": "ALL", "records_used": 500},
        "warnings": ["EPOCH_WARNING: mixed data"],
    }


def _small_sample_report():
    """A report with insufficient sample — should fail."""
    return {
        "question_id": "SMALL1",
        "recommendation": "VALIDATE",
        "overall": {"finding": "maybe"},
        "dataset": {"source": "shadow_trades_current", "sample_size": 20},
        "fingerprint": {"epoch": "CURRENT", "records_used": 20},
        "warnings": [],
    }


def _no_metadata_report():
    """A report missing epoch and architecture metadata."""
    return {
        "question_id": "NOMETA",
        "recommendation": "PROMOTE",
        "overall": {},
        "dataset": {},
        "fingerprint": {},
        "warnings": [],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# TESTS: INVALID EXPERIMENTS CANNOT PROMOTE
# ═══════════════════════════════════════════════════════════════════════════════


class TestInvalidCannotPromote:
    """Invalid experiments must be blocked from promotion."""

    def test_mixed_epoch_blocks_promotion(self):
        """ALL/MIXED epoch report cannot be promoted."""
        report = _invalid_epoch_report()
        result = validate_experiment_report(report)
        assert result.promotion_allowed is False
        assert result.final_status == "REQUIRES_RERUN"
        assert any("epoch" in g.gate_name for g in result.gates_failed)

    def test_epoch_warning_blocks_promotion(self):
        """Epoch contamination warning blocks promotion."""
        report = _valid_report()
        report["warnings"] = ["EPOCH_WARNING: Data epoch is 'ALL'."]
        result = validate_experiment_report(report)
        assert result.promotion_allowed is False
        assert result.final_status == "REQUIRES_RERUN"

    def test_small_sample_blocks_promotion(self):
        """Insufficient sample size blocks promotion."""
        report = _small_sample_report()
        result = validate_experiment_report(report)
        assert result.promotion_allowed is False
        assert result.final_status == "REQUIRES_RERUN"

    def test_no_epoch_metadata_blocks(self):
        """Missing epoch in fingerprint blocks promotion."""
        report = _no_metadata_report()
        result = validate_experiment_report(report)
        assert result.promotion_allowed is False
        assert result.final_status == "REQUIRES_RERUN"


# ═══════════════════════════════════════════════════════════════════════════════
# TESTS: MISSING METADATA BLOCKS VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestMissingMetadataBlocks:
    """Reports without required metadata cannot pass gates."""

    def test_empty_fingerprint_fails_epoch(self):
        """Empty fingerprint = no epoch = gate fails."""
        report = _no_metadata_report()
        result = validate_experiment_report(report)
        epoch_gates = [g for g in result.gates_failed if "epoch" in g.gate_name]
        assert len(epoch_gates) > 0

    def test_empty_dataset_fails_sample_size(self):
        """Empty dataset = no sample size = gate fails."""
        report = _no_metadata_report()
        result = validate_experiment_report(report)
        sample_gates = [g for g in result.gates_failed if "sample" in g.gate_name]
        assert len(sample_gates) > 0

    def test_non_gated_status_passes_through(self):
        """Non-promotion status (MONITOR, WAIT) is not blocked."""
        report = _valid_report(recommendation="MONITOR")
        result = validate_experiment_report(report)
        assert result.final_status == "MONITOR"
        assert result.promotion_allowed is False
        assert "does not require" in result.reason


# ═══════════════════════════════════════════════════════════════════════════════
# TESTS: VALID EXPERIMENTS PASS
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidExperimentsPass:
    """Properly structured experiments pass all gates."""

    def test_valid_report_passes_all_gates(self):
        """Complete valid report passes and allows promotion."""
        report = _valid_report()
        result = validate_experiment_report(report)
        assert result.all_blocking_passed is True
        assert result.promotion_allowed is True
        assert result.final_status == "PROMOTE"

    def test_valid_report_has_no_failed_gates(self):
        """Valid report has zero failed (blocking) gates."""
        report = _valid_report()
        result = validate_experiment_report(report)
        assert len(result.gates_failed) == 0

    def test_valid_report_records_passed_gates(self):
        """Valid report records which gates passed."""
        report = _valid_report()
        result = validate_experiment_report(report)
        assert len(result.gates_passed) >= 3  # epoch, contamination, sample_size at minimum

    def test_assessment_serialises(self):
        """ValidityAssessment.to_dict() works."""
        report = _valid_report()
        result = validate_experiment_report(report)
        d = result.to_dict()
        assert d["promotion_allowed"] is True
        assert d["final_status"] == "PROMOTE"
        assert "gates_passed" in d


# ═══════════════════════════════════════════════════════════════════════════════
# TESTS: SPECIFIC GATE LOGIC
# ═══════════════════════════════════════════════════════════════════════════════


class TestSpecificGates:
    """Tests for individual gate logic."""

    def test_current_epoch_passes(self):
        """CURRENT epoch passes data gate."""
        report = _valid_report()
        result = validate_experiment_report(report)
        epoch_passes = [g for g in result.gates_passed if "epoch" in g.gate_name]
        assert len(epoch_passes) >= 1

    def test_sample_100_passes(self):
        """n=100 meets minimum threshold."""
        report = _valid_report()
        report["dataset"]["sample_size"] = 100
        report["fingerprint"]["records_used"] = 100
        result = validate_experiment_report(report)
        sample_fails = [g for g in result.gates_failed if "sample" in g.gate_name]
        assert len(sample_fails) == 0

    def test_sample_49_fails(self):
        """n=49 below minimum — blocks promotion."""
        report = _valid_report()
        report["dataset"]["sample_size"] = 49
        report["fingerprint"]["records_used"] = 49
        result = validate_experiment_report(report)
        assert result.promotion_allowed is False

    def test_gated_statuses_are_correct(self):
        """Only expected statuses trigger gate validation."""
        for status in ["PROMOTE", "VALIDATE", "IMPLEMENT", "READY"]:
            assert status in GATED_STATUSES or status.upper() in GATED_STATUSES

    def test_valid_epochs_include_current(self):
        """CURRENT is in the valid epoch set."""
        assert "CURRENT" in VALID_EPOCHS



def _valid_report(recommendation="PROMOTE"):
    return {
        "question_id": "TEST1",
        "status": "COMPLETE",
        "recommendation": recommendation,
        "overall": {
            "experiment": "TEST",
            "control": {"SL": 1.0, "variable": "TP"},
            "variants": {"0.5": {"ev": 0.03}},
            "finding": "Test finding",
            "comparisons": {"0.5": {"vs_control": {"significant_05": True}}},
        },
        "confidence": "HIGH",
        "dataset": {"source": "shadow_trades_current", "sample_size": 200},
        "fingerprint": {
            "epoch": "CURRENT",
            "architecture_version": "new_pipeline_v1.2",
            "records_used": 200,
        },
        "warnings": [],
        "epoch": "CURRENT",
    }


def _invalid_epoch_report():
    return {
        "question_id": "BAD1",
        "recommendation": "PROMOTE",
        "overall": {"finding": "looks good"},
        "dataset": {"source": "shadow_trades", "sample_size": 500},
        "fingerprint": {"epoch": "ALL", "records_used": 500},
        "warnings": ["EPOCH_WARNING: mixed data"],
    }


def _small_sample_report():
    return {
        "question_id": "SMALL1",
        "recommendation": "PROMOTE",
        "overall": {"finding": "maybe"},
        "dataset": {"source": "shadow_trades_current", "sample_size": 20},
        "fingerprint": {"epoch": "CURRENT", "records_used": 20},
        "warnings": [],
    }


def _no_metadata_report():
    return {
        "question_id": "NOMETA",
        "recommendation": "PROMOTE",
        "overall": {},
        "dataset": {},
        "fingerprint": {},
        "warnings": [],
    }


class TestInvalidCannotPromote:
    def test_mixed_epoch_blocks(self):
        result = validate_experiment_report(_invalid_epoch_report())
        assert result.promotion_allowed is False
        assert result.final_status == "REQUIRES_RERUN"
        assert any("epoch" in g.gate_name for g in result.gates_failed)

    def test_epoch_warning_blocks(self):
        report = _valid_report()
        report["warnings"] = ["EPOCH_WARNING: Data epoch is 'ALL'."]
        result = validate_experiment_report(report)
        assert result.promotion_allowed is False

    def test_small_sample_blocks(self):
        result = validate_experiment_report(_small_sample_report())
        assert result.promotion_allowed is False
        assert result.final_status == "REQUIRES_RERUN"

    def test_no_metadata_blocks(self):
        result = validate_experiment_report(_no_metadata_report())
        assert result.promotion_allowed is False
        assert result.final_status == "REQUIRES_RERUN"


class TestMissingMetadataBlocks:
    def test_empty_fingerprint_fails_epoch(self):
        result = validate_experiment_report(_no_metadata_report())
        epoch_gates = [g for g in result.gates_failed if "epoch" in g.gate_name]
        assert len(epoch_gates) > 0

    def test_empty_dataset_fails_sample(self):
        result = validate_experiment_report(_no_metadata_report())
        sample_gates = [g for g in result.gates_failed if "sample" in g.gate_name]
        assert len(sample_gates) > 0

    def test_non_gated_status_passes_through(self):
        report = _valid_report(recommendation="MONITOR")
        result = validate_experiment_report(report)
        assert result.final_status == "MONITOR"
        assert result.promotion_allowed is False


class TestValidExperimentsPass:
    def test_valid_passes_all(self):
        result = validate_experiment_report(_valid_report())
        assert result.all_blocking_passed is True
        assert result.promotion_allowed is True
        assert result.final_status == "PROMOTE"

    def test_valid_no_failed_gates(self):
        result = validate_experiment_report(_valid_report())
        assert len(result.gates_failed) == 0

    def test_valid_records_passed_gates(self):
        result = validate_experiment_report(_valid_report())
        assert len(result.gates_passed) >= 3

    def test_assessment_serialises(self):
        result = validate_experiment_report(_valid_report())
        d = result.to_dict()
        assert d["promotion_allowed"] is True
        assert "gates_passed" in d


class TestSpecificGates:
    def test_current_epoch_passes(self):
        result = validate_experiment_report(_valid_report())
        epoch_passes = [g for g in result.gates_passed if "epoch" in g.gate_name]
        assert len(epoch_passes) >= 1

    def test_sample_100_passes(self):
        report = _valid_report()
        report["dataset"]["sample_size"] = 100
        report["fingerprint"]["records_used"] = 100
        result = validate_experiment_report(report)
        sample_fails = [g for g in result.gates_failed if "sample" in g.gate_name]
        assert len(sample_fails) == 0

    def test_sample_49_fails(self):
        report = _valid_report()
        report["dataset"]["sample_size"] = 49
        result = validate_experiment_report(report)
        assert result.promotion_allowed is False

    def test_gated_statuses(self):
        for s in ["PROMOTE", "IMPLEMENT", "READY", "VALIDATED"]:
            assert s in GATED_STATUSES

    def test_valid_epochs(self):
        assert "CURRENT" in VALID_EPOCHS
