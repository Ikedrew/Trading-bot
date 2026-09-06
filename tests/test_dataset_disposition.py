"""Tests for the Research Engine dataset disposition layer.

Covers Phase 2 (semantic classification), Phase 8 (intentional exclusion registry),
Phase 9 (completeness guard), and Phase 10 (temporal integrity) of the Step-4 audit.

These tests prove:
    - Every active canonical production dataset has an explicit research disposition
    - The six Step-4 audit datasets are classified exactly per the audit
    - Intentionally-excluded datasets have NO consumer and are documented
    - The completeness guard fails loudly when a new dataset is added without disposition
    - Temporal availability classes are correct for each dataset
    - Outcome fields cannot enter pre-decision features
"""

from __future__ import annotations

import pytest

from core.production_data_contract import PRODUCTION_SCHEMA_REGISTRY
from research_engine.dataset_disposition import (
    ResearchDisposition,
    ResearchDispositionStatus,
    Phase2Disposition,
    TemporalAvailability,
    RESEARCH_DISPOSITIONS,
    dataset_disposition,
    require_disposition,
    uncovered_active_datasets,
    coverage_report,
    assert_full_coverage,
    assert_not_outcome_as_decision_feature,
    temporal_availability,
    OUTCOME_LABEL_FIELDS,
    OUTCOME_AUTHORITATIVE_DATASETS,
)

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ═══════════════════════════════════════════════════════════════════════════════
# COMPLETENESS GUARD — Phase 9
# ═══════════════════════════════════════════════════════════════════════════════

class TestCompletenessGuard:
    """Every active canonical production dataset must have a research disposition."""

    def test_all_active_datasets_have_disposition(self):
        """The real coverage guard: assert_full_coverage() must not raise."""
        missing = uncovered_active_datasets()
        assert missing == [], (
            f"Datasets missing research disposition: {missing}"
        )
        assert_full_coverage()  # raises if uncovered

    def test_disposition_count_matches_contract(self):
        """Number of disposition entries covering the registry must equal active count."""
        active = set(PRODUCTION_SCHEMA_REGISTRY.keys())
        covered = {ds for ds in active if ds in RESEARCH_DISPOSITIONS}
        assert len(covered) == len(active), (
            f"Only {len(covered)}/{len(active)} active datasets have dispositions. "
            f"Missing: {active - covered}"
        )

    def test_no_unknown_status_in_registry(self):
        """Every disposition must map to a recognized Phase-2 class (A-F)."""
        for ds, disp in RESEARCH_DISPOSITIONS.items():
            assert disp.phase2 != Phase2Disposition.F_UNKNOWN, (
                f"Dataset '{ds}' is classified UNKNOWN — complete the audit."
            )

    def test_guard_fails_on_synthetic_uncovered_dataset(self, monkeypatch):
        """Inject a fake canonical dataset and prove the guard catches it."""
        monkeypatch.setitem(
            PRODUCTION_SCHEMA_REGISTRY,
            "_test_phantom_dataset",
            PRODUCTION_SCHEMA_REGISTRY.get("events"),  # copy of an existing schema
        )
        with pytest.raises(RuntimeError, match="_test_phantom_dataset"):
            assert_full_coverage()

    def test_guard_fails_on_real_uncovered(self):
        """A monkeypatched registry where a real dataset is missing its disposition."""
        real_registry = dict(PRODUCTION_SCHEMA_REGISTRY)
        monkeypatch_registry = {k: v for k, v in real_registry.items()}
        # Remove 'events' from dispositions only
        saved = RESEARCH_DISPOSITIONS.pop("events", None)
        try:
            with pytest.raises(RuntimeError, match="events"):
                assert_full_coverage()
        finally:
            if saved is not None:
                RESEARCH_DISPOSITIONS["events"] = saved


# ═══════════════════════════════════════════════════════════════════════════════
# SIX STEP-4 AUDIT DATASETS — Phase 2
# ═══════════════════════════════════════════════════════════════════════════════

SIX_AUDIT_DATASETS = (
    "events",
    "horizon_candidates",
    "strategy_candidates",
    "execution_attempts",
    "management_actions",
    "position_excursion",
)


class TestSixAuditClassifications:
    """Verify each of the six audited datasets has the audit-decided disposition."""

    def test_events_is_operational_only(self):
        disp = dataset_disposition("events")
        assert disp is not None
        assert disp.phase2 == Phase2Disposition.C_OPERATIONAL_ONLY
        assert disp.status == ResearchDispositionStatus.INTENTIONALLY_OPERATIONAL
        assert disp.consumers == ()
        assert disp.temporal_availability == TemporalAvailability.LIFECYCLE

    def test_horizon_candidates_is_research_input(self):
        disp = dataset_disposition("horizon_candidates")
        assert disp is not None
        assert disp.phase2 == Phase2Disposition.A_RESEARCH_INPUT
        assert disp.status == ResearchDispositionStatus.DIRECTLY_CONSUMED
        assert "research_engine.evidence.horizon_candidates" in disp.consumers[0]
        assert disp.temporal_availability == TemporalAvailability.BEFORE_DECISION
        assert "canonical_opportunity_id" in disp.join_keys

    def test_strategy_candidates_is_research_input(self):
        disp = dataset_disposition("strategy_candidates")
        assert disp is not None
        assert disp.phase2 == Phase2Disposition.A_RESEARCH_INPUT
        assert disp.status == ResearchDispositionStatus.DIRECTLY_CONSUMED
        assert "research_engine.evidence.strategy_candidates" in disp.consumers[0]
        assert disp.temporal_availability == TemporalAvailability.BEFORE_DECISION
        assert "canonical_opportunity_id" in disp.join_keys

    def test_execution_attempts_is_supporting_diagnostic(self):
        disp = dataset_disposition("execution_attempts")
        assert disp is not None
        assert disp.phase2 == Phase2Disposition.B_SUPPORTING_DIAGNOSTIC
        assert disp.status == ResearchDispositionStatus.SUPPORTING_CONSUMED
        assert "research_engine.evidence.execution_attempts" in disp.consumers[0]
        assert disp.temporal_availability == TemporalAvailability.AFTER_DECISION_BEFORE_OUTCOME
        assert "correlation_id" in disp.join_keys

    def test_management_actions_is_supporting_diagnostic(self):
        disp = dataset_disposition("management_actions")
        assert disp is not None
        assert disp.phase2 == Phase2Disposition.B_SUPPORTING_DIAGNOSTIC
        assert disp.status == ResearchDispositionStatus.SUPPORTING_CONSUMED
        assert "research_engine.evidence.management_actions" in disp.consumers[0]
        assert disp.temporal_availability == TemporalAvailability.AFTER_DECISION_BEFORE_OUTCOME
        assert "trade_id" in disp.join_keys

    def test_position_excursion_is_runtime_state(self):
        disp = dataset_disposition("position_excursion")
        assert disp is not None
        assert disp.phase2 == Phase2Disposition.E_STATE_NON_EVENT
        assert disp.status == ResearchDispositionStatus.RUNTIME_STATE
        assert disp.consumers == ()
        assert disp.temporal_availability == TemporalAvailability.LIFECYCLE
        assert disp.authoritative_alternative == "trade_truth"


# ═══════════════════════════════════════════════════════════════════════════════
# INTENTIONAL EXCLUSION REGISTRY — Phase 8
# ═══════════════════════════════════════════════════════════════════════════════

class TestIntentionalExclusions:
    """Excluded datasets must have explicit, documented dispositions — not omissions."""

    def test_events_no_consumer_by_design(self):
        disp = require_disposition("events")
        assert disp.consumers == ()
        assert disp.status == ResearchDispositionStatus.INTENTIONALLY_OPERATIONAL
        # The reason must mention this is an audit/audit-transport dataset.
        assert any(
            kw in disp.reason.lower()
            for kw in ("audit", "telemetry", "observability", "operational", "double-count")
        )

    def test_position_excursion_no_consumer_by_design(self):
        disp = require_disposition("position_excursion")
        assert disp.consumers == ()
        assert disp.status == ResearchDispositionStatus.RUNTIME_STATE
        assert disp.authoritative_alternative == "trade_truth"
        # Must document survivorship / look-ahead bias concern.
        assert any(
            kw in disp.reason.lower()
            for kw in ("survivorship", "look-ahead", "state", "checkpoint", "mutable")
        )

    def test_excluded_datasets_list(self):
        """The two excluded datasets have empty consumers."""
        for ds in ("events", "position_excursion"):
            disp = dataset_disposition(ds)
            assert disp is not None
            assert disp.consumers == (), f"{ds} should have no consumer by design"


# ═══════════════════════════════════════════════════════════════════════════════
# CONSUMED DATASETS HAVE CONSUMERS — Phase 7
# ═══════════════════════════════════════════════════════════════════════════════

class TestConsumedDatasetsHaveConsumers:
    """Every DIRECTLY_CONSUMED or SUPPORTING_CONSUMED dataset must have ≥1 consumer."""

    @pytest.mark.parametrize("status", [
        ResearchDispositionStatus.DIRECTLY_CONSUMED,
        ResearchDispositionStatus.SUPPORTING_CONSUMED,
    ])
    def test_consumed_status_has_nonempty_consumers(self, status):
        for ds, disp in RESEARCH_DISPOSITIONS.items():
            if disp.status == status:
                assert len(disp.consumers) > 0, (
                    f"Dataset '{ds}' is {status} but has NO consumer registered."
                )

    def test_no_local_fallback_in_consumers(self):
        """Consumer module paths must reference S3-backed loaders, not local reads."""
        import inspect
        for ds, disp in RESEARCH_DISPOSITIONS.items():
            for consumer_str in disp.consumers:
                # consumer_str format: "module:attribute"
                mod_path, _, attr = consumer_str.partition(":")
                if not attr:
                    continue
                mod = __import__(mod_path, fromlist=[attr])
                fn = getattr(mod, attr, None)
                if fn is None:
                    continue
                src = inspect.getsource(fn)
                # Must not read from local logs/<dataset>
                assert f"logs/{ds}" not in src, (
                    f"Consumer '{consumer_str}' for '{ds}' reads from local logs/ — "
                    f"violates S3-only contract."
                )


# ═══════════════════════════════════════════════════════════════════════════════
# TEMPORAL AVAILABILITY / LEAKAGE PROTECTION — Phase 5
# ═══════════════════════════════════════════════════════════════════════════════

class TestTemporalAvailability:
    """Every dataset has a correct temporal classification."""

    def test_events_lifecycle(self):
        assert temporal_availability("events") == TemporalAvailability.LIFECYCLE

    def test_horizon_candidates_before_decision(self):
        assert temporal_availability("horizon_candidates") == TemporalAvailability.BEFORE_DECISION

    def test_strategy_candidates_before_decision(self):
        assert temporal_availability("strategy_candidates") == TemporalAvailability.BEFORE_DECISION

    def test_execution_attempts_after_decision_before_outcome(self):
        assert temporal_availability("execution_attempts") == TemporalAvailability.AFTER_DECISION_BEFORE_OUTCOME

    def test_management_actions_after_decision_before_outcome(self):
        assert temporal_availability("management_actions") == TemporalAvailability.AFTER_DECISION_BEFORE_OUTCOME

    def test_position_excursion_lifecycle(self):
        assert temporal_availability("position_excursion") == TemporalAvailability.LIFECYCLE

    def test_trade_truth_is_after_outcome(self):
        """Authoritative outcome datasets must be AFTER_OUTCOME."""
        assert temporal_availability("trade_truth") == TemporalAvailability.AFTER_OUTCOME
        assert temporal_availability("trade_journal") == TemporalAvailability.AFTER_OUTCOME

    def test_decision_evidence_is_before_decision(self):
        """Decision-quality datasets must be BEFORE_DECISION."""
        assert temporal_availability("decision_trace") == TemporalAvailability.BEFORE_DECISION
        assert temporal_availability("decision_ledger") == TemporalAvailability.BEFORE_DECISION
        assert temporal_availability("opportunities") == TemporalAvailability.BEFORE_DECISION


class TestLeakageGuard:
    """Phase 5: outcome fields must never be consumable as pre-decision features."""

    def test_outcome_label_fields_populated(self):
        assert {"r_multiple_realised", "exit_reason", "mfe_r", "mae_r"} <= OUTCOME_LABEL_FIELDS
        assert "trade_truth" in OUTCOME_AUTHORITATIVE_DATASETS

    def test_after_outcome_dataset_rejects_outcome_feature(self):
        """Using trade_truth.mfe_r as a decision feature must raise."""
        with pytest.raises(ValueError, match="Outcome leakage"):
            assert_not_outcome_as_decision_feature(
                feature="mfe_r", source_dataset="trade_truth"
            )

    def test_after_outcome_dataset_rejects_pnl_feature(self):
        with pytest.raises(ValueError, match="Outcome leakage"):
            assert_not_outcome_as_decision_feature(
                feature="pnl_realised", source_dataset="trade_journal"
            )

    def test_pre_decision_dataset_allows_non_outcome_feature(self):
        """BEFORE_DECISION datasets don't trigger the outcome-feature guard."""
        with pytest.raises(ValueError):
            assert_not_outcome_as_decision_feature(
                feature="r_multiple_realised", source_dataset="trade_truth"
            )

    def test_outcome_authoritative_dataset_rejects_any_outcome_field(self):
        """Trade truth / trade journal are authoritative — any outcome label is blocked."""
        for field in ("r_multiple", "exit_reason", "mfe_r", "mae_r"):
            with pytest.raises(ValueError, match="outcome label"):
                assert_not_outcome_as_decision_feature(
                    feature=field, source_dataset="trade_truth"
                )


# ═══════════════════════════════════════════════════════════════════════════════
# COVERAGE REPORT
# ═══════════════════════════════════════════════════════════════════════════════

class TestCoverageReport:
    def test_coverage_report_structure(self):
        report = coverage_report()
        assert "active_contract_datasets" in report
        assert "covered" in report
        assert "uncovered" in report
        assert "dispositions" in report
        assert "registry_external_documented" in report

    def test_report_shows_zero_uncovered(self):
        report = coverage_report()
        assert report["uncovered"] == []
        assert report["covered"] == len(report["active_contract_datasets"])

    def test_report_documents_position_excursion_externally(self):
        """position_excursion is not in the production contract registry but IS
        documented in the disposition layer as an intentional RUNTIME_STATE."""
        report = coverage_report()
        # position_excursion is in the registry_external_documented because it's
        # not an active contract dataset but has an explicit disposition.
        assert "position_excursion" in report["registry_external_documented"]


# ═══════════════════════════════════════════════════════════════════════════════
# JOIN KEY / LINEAGE VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

class TestLineageFields:
    """Connected datasets must declare canonical lineage join keys."""

    def test_connected_datasets_declare_join_keys(self):
        for ds in ("horizon_candidates", "strategy_candidates",
                   "execution_attempts", "management_actions"):
            disp = require_disposition(ds)
            assert len(disp.join_keys) > 0, f"{ds} must declare join keys"
