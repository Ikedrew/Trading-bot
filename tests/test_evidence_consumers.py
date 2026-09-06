"""Tests for the four Step-4 dataset evidence consumers.

Each consumer:
    - reads a canonical V1 dataset ONLY through the sanctioned S3 data-access layer
    - has a clearly defined research purpose
    - reports lineage coverage so ambiguous joins never silently degrade
    - never changes trading behaviour, collection, schemas, or V1 persistence

All sources are injected via FakeS3 so tests never touch real AWS.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.production_data_contract import s3_base_prefix, current_schema
from research_engine.data_access.s3_source import (
    S3ResearchDataSource, set_default_source, reset_default_source,
)
from research_engine.evidence.registry import (
    CONSUMED_EVIDENCE_DATASETS,
    DATASET_EVIDENCE_CONSUMERS,
    run_dataset_evidence,
)
from research_engine.evidence.horizon_candidates import horizon_candidate_evidence
from research_engine.evidence.strategy_candidates import strategy_candidate_evidence
from research_engine.evidence.execution_attempts import execution_attempt_evidence
from research_engine.evidence.management_actions import management_actions_evidence


# ── Fake S3 (shared pattern with test_research_loaders / test_s3_fake) ─────────

_DATE_ONLY = {"portfolio_rankings", "portfolio_shadow"}


class FakeS3:
    def __init__(self):
        self.objects: dict[str, str] = {}

    def add(self, dataset: str, records: list[dict], *, symbol: str | None = None,
            date: str = "2026-07-23"):
        base = s3_base_prefix(dataset)
        schema = current_schema(dataset)
        if dataset in _DATE_ONLY or symbol is None:
            key = f"{base}/schema_version={schema}/date={date}/part-000.jsonl"
        else:
            key = f"{base}/schema_version={schema}/symbol={symbol}/date={date}/part-000.jsonl"
        self.objects[key] = self.objects.get(key, "") + "".join(
            json.dumps(r) + "\n" for r in records
        )

    def list_objects_v2(self, **kw):
        prefix = kw.get("Prefix", "")
        keys = sorted(k for k in self.objects if k.startswith(prefix))
        return {"Contents": [{"Key": k} for k in keys], "IsTruncated": False}

    def get_object(self, **kw):
        key = kw["Key"]

        class _Body:
            def __init__(self, t): self._t = t
            def read(self): return self._t.encode("utf-8")

        return {"Body": _Body(self.objects[key])}


@pytest.fixture
def s3():
    fake = FakeS3()
    set_default_source(S3ResearchDataSource(bucket="test-bucket", client=fake))
    yield fake
    reset_default_source()


# ═══════════════════════════════════════════════════════════════════════════════
# SAMPLE DATA
# ═══════════════════════════════════════════════════════════════════════════════

def _sample_horizon_candidate(sel_status, horizon, eligible=True, conf=0.5):
    return {
        "candidate_id": f"EURUSD*1784800000*TWEEZER_TOP:{horizon}",
        "canonical_opportunity_id": "EURUSD*1784800000*TWEEZER_TOP",
        "observation_id": "EURUSD.M5.1784800000",
        "decision_id": "",
        "correlation_id": "COR-20260723-1-EURUSD-ABCD",
        "symbol": "EURUSD",
        "cycle_id": 1,
        "bar_time": 1784800000.0,
        "horizon": horizon,
        "eligible": eligible,
        "confidence": conf,
        "reasoning": "default-horizon-reasoning",
        "selection_status": sel_status,
        "evidence": {},
        "bar_time": 1784800000.0,
    }


def _sample_strategy_candidate(selected, family, rank=1, conf=0.7):
    return {
        "candidate_id": f"EURUSD*1784800000*TWEEZER_TOP:{family}",
        "canonical_opportunity_id": "EURUSD*1784800000*TWEEZER_TOP",
        "observation_id": "EURUSD.M5.1784800000",
        "correlation_id": "COR-20260723-1-EURUSD-ABCD",
        "symbol": "EURUSD",
        "bar_time": 1784800000.0,
        "cycle_id": 1,
        "strategy_family": family,
        "confidence": conf,
        "rank": rank,
        "selected": selected,
        "supporting_conditions": {"liquidity_agreement": True},
        "reasoning": ["momentum"],
        "evidence": {},
    }


def _sample_execution_attempt(action_type, attempt_num=1, ok=True, retcode="OK", retry_reason=None):
    return {
        "attempt_id": f"att_{action_type}_{attempt_num}",
        "attempt_number": attempt_num,
        "action_type": action_type,
        "retry_reason": (
            retry_reason if retry_reason is not None
            else ("" if attempt_num == 1 else "requote")
        ),
        "correlation_id": "COR-20260723-1-EURUSD-ABCD",
        "canonical_opportunity_id": "EURUSD*1784800000*TWEEZER_TOP",
        "decision_id": "DEC-123",
        "trade_id": None if action_type == "ENTRY" else "pos_12345",
        "symbol": "EURUSD",
        "bid_at_attempt": 1.1000,
        "ask_at_attempt": 1.1001,
        "spread_at_attempt": 1,
        "slippage": 0.0,
        "broker_result": {
            "ok": ok,
            "retcode": retcode,
            "deal": "pos_12345" if ok and action_type == "ENTRY" else None,
            "comment": "",
        },
        "requested_sl": 1.0900,
        "requested_tp": 1.1100,
        "ts_ms": 1784800000000 + attempt_num,
    }


def _sample_management_action(action_type, trade_id="pos_12345", action_reason="take_profit"):
    return {
        "management_action_id": f"mgmt_{action_type}_{trade_id}",
        "trade_id": trade_id,
        "decision_id": "DEC-123",
        "observation_id": "EURUSD.M5.1784800000",
        "correlation_id": "COR-20260723-1-EURUSD-ABCD",
        "canonical_opportunity_id": "EURUSD*1784800000*TWEEZER_TOP",
        "cycle_id": 1,
        "symbol": "EURUSD",
        "action_type": action_type,
        "action_reason": action_reason,
        "requested_sl": 1.0900,
        "requested_tp": 1.1100,
        "requested_volume": 1,
        "ts_ms": 1784800000001,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# HORIZON CANDIDATES
# ═══════════════════════════════════════════════════════════════════════════════

class TestHorizonCandidateEvidence:
    def test_consumer_registered(self):
        assert "horizon_candidates" in CONSUMED_EVIDENCE_DATASETS
        assert "horizon_candidates" in DATASET_EVIDENCE_CONSUMERS

    def test_reads_from_s3_only(self, s3):
        s3.add("horizon_candidates", [
            _sample_horizon_candidate("SELECTED", "SCALP", conf=0.8),
            _sample_horizon_candidate("REJECTED", "INTRADAY", conf=0.3),
            _sample_horizon_candidate("INELIGIBLE", "EXTENDED", eligible=False, conf=0.1),
        ], symbol="EURUSD")
        report = horizon_candidate_evidence("EURUSD")
        assert report["record_count"] == 3
        assert report["disposition_status"] == "DIRECTLY_CONSUMED"
        assert report["temporal_availability"] == "BEFORE_DECISION"

    def test_selection_status_distribution(self, s3):
        s3.add("horizon_candidates", [
            _sample_horizon_candidate("SELECTED", "SCALP"),
            _sample_horizon_candidate("REJECTED", "INTRADAY"),
            _sample_horizon_candidate("INELIGIBLE", "EXTENDED", eligible=False),
        ], symbol="EURUSD")
        report = horizon_candidate_evidence("EURUSD")
        sd = report["analysis"]["selection_status_distribution"]
        assert sd.get("SELECTED") == 1
        assert sd.get("REJECTED") == 1
        assert sd.get("INELIGIBLE") == 1

    def test_eligibility_by_horizon(self, s3):
        s3.add("horizon_candidates", [
            _sample_horizon_candidate("SELECTED", "SCALP", eligible=True),
            _sample_horizon_candidate("REJECTED", "SCALP", eligible=True),
            _sample_horizon_candidate("INELIGIBLE", "EXTENDED", eligible=False),
        ], symbol="EURUSD")
        report = horizon_candidate_evidence("EURUSD")
        by_h = report["analysis"]["eligibility_and_selection_by_horizon"]
        assert by_h["SCALP"]["candidates"] == 2
        assert by_h["SCALP"]["eligible"] == 2
        assert by_h["SCALP"]["selected"] == 1
        assert by_h["EXTENDED"]["candidates"] == 1
        assert by_h["EXTENDED"]["eligible"] == 0

    def test_selected_vs_rejected(self, s3):
        s3.add("horizon_candidates", [
            _sample_horizon_candidate("SELECTED", "SCALP", conf=0.9),
            _sample_horizon_candidate("REJECTED", "INTRADAY", conf=0.2),
            _sample_horizon_candidate("INELIGIBLE", "EXTENDED", conf=0.1),
        ], symbol="EURUSD")
        report = horizon_candidate_evidence("EURUSD")
        sv = report["analysis"]["selected_vs_rejected"]
        assert sv["selected_count"] == 1
        assert sv["rejected_ineligible_count"] == 2
        assert sv["selected_confidence"]["mean"] == 0.9
        assert sv["rejected_confidence"]["mean"] == 0.15

    def test_lineage_coverage(self, s):
        s3.add("horizon_candidates", [
            _sample_horizon_candidate("SELECTED", "SCALP"),
        ], symbol="EURUSD")
        report = horizon_candidate_evidence("EURUSD")
        lc = report["lineage_coverage"]
        assert lc["total_records"] == 1
        assert lc["key_coverage"]["canonical_opportunity_id"]["non_empty"] == 1
        assert lc["key_coverage"]["correlation_id"]["non_empty"] == 1
        assert lc["key_coverage"]["entity_id"]["non_empty"] == 0  # not in sample

    def test_empty_s3_returns_zero_records(self, s3):
        report = horizon_candidate_evidence("EURUSD")
        assert report["record_count"] == 0
        assert report["analysis"]["opportunities_evaluated"] == 0

    def test_guard_notes_present(self, s3):
        s3.add("horizon_candidates", [
            _sample_horizon_candidate("SELECTED", "SCALP"),
        ], symbol="EURUSD")
        report = horizon_candidate_evidence("EURUSD")
        assert any("BEFORE_DECISION" in n for n in report["guard_notes"])

    def test_lineage_coverage(self, s3):
        s3.add("horizon_candidates", [
            _sample_horizon_candidate("SELECTED", "SCALP"),
        ], symbol="EURUSD")
        report = horizon_candidate_evidence("EURUSD")
        lc = report["lineage_coverage"]
        assert lc["total_records"] == 1
        assert lc["key_coverage"]["canonical_opportunity_id"]["non_empty"] == 1
        assert lc["key_coverage"]["correlation_id"]["non_empty"] == 1
        assert lc["key_coverage"]["entity_id"]["non_empty"] == 0  # not in sample

    def test_empty_s3_returns_zero_records(self, s3):
        report = horizon_candidate_evidence("EURUSD")
        assert report["record_count"] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# STRATEGY CANDIDATES
# ═══════════════════════════════════════════════════════════════════════════════

class TestStrategyCandidateEvidence:
    def test_consumer_registered(self):
        assert "strategy_candidates" in CONSUMED_EVIDENCE_DATASETS
        assert "strategy_candidates" in DATASET_EVIDENCE_CONSUMERS

    def test_reads_from_s3_only(self, s3):
        s3.add("strategy_candidates", [
            _sample_strategy_candidate(True, "TREND", rank=1, conf=0.9),
            _sample_strategy_candidate(False, "RANGE", rank=2, conf=0.4),
        ], symbol="EURUSD")
        report = strategy_candidate_evidence("EURUSD")
        assert report["record_count"] == 2
        assert report["disposition_status"] == "DIRECTLY_CONSUMED"
        assert report["temporal_availability"] == "BEFORE_DECISION"

    def test_family_distribution(self, s3):
        s3.add("strategy_candidates", [
            _sample_strategy_candidate(True, "TREND"),
            _sample_strategy_candidate(False, "RANGE"),
        ], symbol="EURUSD")
        report = strategy_candidate_evidence("EURUSD")
        fam = report["analysis"]["strategy_family_distribution"]
        assert fam.get("TREND") == 1
        assert fam.get("RANGE") == 1
        selected = report["analysis"]["selected_family_distribution"]
        assert selected.get("TREND") == 1

    def test_selected_vs_rejected(self, s3):
        s3.add("strategy_candidates", [
            _sample_strategy_candidate(True, "TREND", rank=1, conf=0.9),
            _sample_strategy_candidate(False, "RANGE", rank=2, conf=0.4),
        ], symbol="EURUSD")
        report = strategy_candidate_evidence("EURUSD")
        sv = report["analysis"]["selected_vs_rejected"]
        assert sv["selected_count"] == 1
        assert sv["rejected_count"] == 1
        assert sv["selected_ranks"] == {"1": 1}
        assert sv["selected_confidence"]["mean"] == 0.9

    def test_candidates_per_opportunity(self, s3):
        s3.add("strategy_candidates", [
            _sample_strategy_candidate(True, "TREND", rank=1),
            _sample_strategy_candidate(False, "MEAN_REVERSION", rank=2),
        ], symbol="EURUSD")
        report = strategy_candidate_evidence("EURUSD")
        s = report["analysis"]["opportunity_grain"]
        assert s["opportunities_with_candidates"] == 1
        # one opportunity with two candidates → count=1, mean=2.0
        assert s["candidates_per_opportunity"]["count"] == 1
        assert s["candidates_per_opportunity"]["mean"] == 2.0

    def test_rank_gap(self, s3):
        s3.add("strategy_candidates", [
            _sample_strategy_candidate(True, "TREND", rank=1, conf=0.85),
            _sample_strategy_candidate(False, "RANGE", rank=2, conf=0.55),
        ], symbol="EURUSD")
        report = strategy_candidate_evidence("EURUSD")
        gap = report["analysis"]["winner_vs_best_alternative_confidence_gap"]
        assert gap["count"] == 1
        assert gap["mean"] == pytest.approx(0.30)  # 0.85 - 0.55

    def test_supporting_conditions(self, s3):
        rec1 = _sample_strategy_candidate(True, "TREND")
        rec1["supporting_conditions"] = {"liquidity_agreement": True, "trend_score": False}
        rec2 = _sample_strategy_candidate(False, "RANGE")
        rec2["supporting_conditions"] = {"liquidity_agreement": True, "range_score": True}
        s3.add("strategy_candidates", [rec1, rec2], symbol="EURUSD")
        report = strategy_candidate_evidence("EURUSD")
        sc = report["analysis"]["supporting_condition_frequency"]
        assert sc["liquidity_agreement"] == 2
        assert sc["range_score"] == 1

    def test_lineage_coverage(self, s3):
        s3.add("strategy_candidates", [
            _sample_strategy_candidate(True, "TREND"),
        ], symbol="EURUSD")
        report = strategy_candidate_evidence("EURUSD")
        lc = report["lineage_coverage"]
        assert lc["total_records"] == 1
        assert lc["key_coverage"]["canonical_opportunity_id"]["non_empty"] == 1
        assert lc["key_coverage"]["correlation_id"]["non_empty"] == 1

    def test_empty_s3_returns_zero_records(self, s3):
        report = strategy_candidate_evidence("EURUSD")
        assert report["record_count"] == 0

    def test_no_promotion(self, s3):
        """Research never auto-promotes candidates."""
        s3.add("strategy_candidates", [
            _sample_strategy_candidate(True, "TREND"),
        ], symbol="EURUSD")
        report = strategy_candidate_evidence("EURUSD")
        assert any("promotion" in n.lower() for n in report["guard_notes"])


# ═══════════════════════════════════════════════════════════════════════════════
# EXECUTION ATTEMPTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestExecutionAttemptEvidence:
    def test_consumer_registered(self):
        assert "execution_attempts" in CONSUMED_EVIDENCE_DATASETS
        assert "execution_attempts" in DATASET_EVIDENCE_CONSUMERS

    def test_reads_from_s3_only(self, s3):
        s3.add("execution_attempts", [
            _sample_execution_attempt("ENTRY", attempt_num=1),
            _sample_execution_attempt("ENTRY", attempt_num=2, ok=False, retcode="TRADE_CONTEXT", retry_reason="requote"),
            _sample_execution_attempt("SLTP_MODIFY", attempt_num=1, ok=True),
        ], symbol="EURUSD")
        report = execution_attempt_evidence("EURUSD")
        assert report["record_count"] == 3
        assert report["disposition_status"] == "SUPPORTING_CONSUMED"
        assert report["temporal_availability"] == "AFTER_DECISION_BEFORE_OUTCOME"

    def test_action_type_distribution(self, s3):
        s3.add("execution_attempts", [
            _sample_execution_attempt("ENTRY"),
            _sample_execution_attempt("ENTRY", attempt_num=2, ok=False, retcode="TRADE_CONTEXT"),
            _sample_execution_attempt("SLTP_MODIFY"),
        ], symbol="EURUSD")
        report = execution_attempt_evidence("EURUSD")
        atd = report["analysis"]["action_type_distribution"]
        assert atd.get("ENTRY") == 2
        assert atd.get("SLTP_MODIFY") == 1

    def test_retry_and_rejection(self, s3):
        s3.add("execution_attempts", [
            _sample_execution_attempt("ENTRY", attempt_num=1),
            _sample_execution_attempt("ENTRY", attempt_num=2, ok=False, retcode="TRADE_CONTEXT", retry_reason="requote"),
            _sample_execution_attempt("ENTRY", attempt_num=3, ok=False, retcode="TRADE_CONTEXT", retry_reason="requote"),
        ], symbol="EURUSD")
        report = execution_attempt_evidence("EURUSD")
        a = report["analysis"]
        assert a["retry_count"] == 2
        assert a["broker_rejected_count"] == 2
        assert a["rejection_rate"] == pytest.approx(2 / 3, abs=1e-4)
        assert a["retcode_distribution"].get("TRADE_CONTEXT") == 2
        assert a["retry_reason_distribution"].get("requote") == 2

    def test_attempts_not_treated_as_separate_trades(self, s3):
        """One correlation_id = many attempts must collapse, not multiply trade count."""
        s3.add("execution_attempts", [
            _sample_execution_attempt("ENTRY", attempt_num=1, ok=True),
            _sample_execution_attempt("ENTRY", attempt_num=2, ok=True),
            _sample_execution_attempt("SLTP_MODIFY", attempt_num=1, ok=True),
        ], symbol="EURUSD")
        report = execution_attempt_evidence("EURUSD")
        # attempts_per_correlation_id aggregates by correlation_id
        ap = report["analysis"]["attempts_per_correlation_id"]
        assert ap["count"] == 1
        assert ap["max"] == 3

    def test_lineage_coverage(self, s3):
        s3.add("execution_attempts", [
            _sample_execution_attempt("ENTRY", attempt_num=1, ok=True),
        ], symbol="EURUSD")
        report = execution_attempt_evidence("EURUSD")
        lc = report["lineage_coverage"]
        assert lc["total_records"] == 1
        assert lc["key_coverage"]["correlation_id"]["non_empty"] == 1
        # trade_id is null for ENTRY attempts by design
        assert lc["key_coverage"]["trade_id"]["non_empty"] == 0
        assert lc["key_coverage"]["broker_result.deal"]["non_empty"] == 1

    def test_empty_s3_returns_zero_records(self, s3):
        report = execution_attempt_evidence("EURUSD")
        assert report["record_count"] == 0

    def test_guard_notes_present(self, s3):
        s3.add("execution_attempts", [
            _sample_execution_attempt("ENTRY"),
        ], symbol="EURUSD")
        report = execution_attempt_evidence("EURUSD")
        assert any("attempt" in note.lower() for note in report["guard_notes"])


# ═══════════════════════════════════════════════════════════════════════════════
# MANAGEMENT ACTIONS
# ═══════════════════════════════════════════════════════════════════════════════

class TestManagementActionsEvidence:
    def test_consumer_registered(self):
        assert "management_actions" in CONSUMED_EVIDENCE_DATASETS
        assert "management_actions" in DATASET_EVIDENCE_CONSUMERS

    def test_reads_from_s3_only(self, s3):
        s3.add("management_actions", [
            _sample_management_action("SLTP_MODIFY", trade_id="pos_12345"),
            _sample_management_action("CLOSE", trade_id="pos_12345", action_reason="take_profit"),
        ], symbol="EURUSD")
        report = management_actions_evidence("EURUSD")
        assert report["record_count"] == 2
        assert report["disposition_status"] == "SUPPORTING_CONSUMED"
        assert report["temporal_availability"] == "AFTER_DECISION_BEFORE_OUTCOME"

    def test_action_type_distribution(self, s3):
        s3.add("management_actions", [
            _sample_management_action("SLTP_MODIFY"),
            _sample_management_action("SLTP_MODIFY"),
            _sample_management_action("CLOSE", action_reason="stop_loss"),
        ], symbol="EURUSD")
        report = management_actions_evidence("EURUSD")
        atd = report["analysis"]["action_type_distribution"]
        assert atd.get("SLTP_MODIFY") == 2
        assert atd.get("CLOSE") == 1

    def test_actions_per_trade(self, s3):
        s3.add("management_actions", [
            _sample_management_action("SLTP_MODIFY", trade_id="pos_12345"),
            _sample_management_action("SLTP_MODIFY", trade_id="pos_12345"),
            _sample_management_action("CLOSE", trade_id="pos_12345"),
        ], symbol="EURUSD")
        report = management_actions_evidence("EURUSD")
        apt = report["analysis"]["actions_per_trade"]
        assert apt["count"] == 1  # one trade_id with actions
        assert apt["mean"] == 3.0  # 3 actions for that trade

    def test_intent_vs_outcome_distinction(self, s3):
        """action_reason (intent) is separate from trade_truth exit_reason (result)."""
        s3.add("management_actions", [
            _sample_management_action("CLOSE", trade_id="pos_12345", action_reason="take_profit"),
        ], symbol="EURUSD")
        report = management_actions_evidence("EURUSD")
        reasons = report["analysis"]["action_reason_distribution"]
        assert reasons.get("take_profit") == 1

    def test_lineage_coverage(self, s3):
        s3.add("management_actions", [
            _sample_management_action("CLOSE", trade_id="pos_12345"),
        ], symbol="EURUSD")
        report = management_actions_evidence("EURUSD")
        lc = report["lineage_coverage"]
        assert lc["total_records"] == 1
        assert lc["key_coverage"]["trade_id"]["non_empty"] == 1
        assert lc["key_coverage"]["correlation_id"]["non_empty"] == 1
        assert lc["key_coverage"]["canonical_opportunity_id"]["non_empty"] == 1

    def test_empty_s3_returns_zero_records(self, s3):
        report = management_actions_evidence("EURUSD")
        assert report["record_count"] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# BATCH RUNNER
# ═══════════════════════════════════════════════════════════════════════════════

class TestRunAllEvidence:
    def test_run_dataset_evidence(self, s3):
        s3.add("horizon_candidates", [
            _sample_horizon_candidate("SELECTED", "SCALP"),
        ], symbol="EURUSD")
        s3.add("strategy_candidates", [
            _sample_strategy_candidate(True, "TREND"),
        ], symbol="EURUSD")
        s3.add("execution_attempts", [
            _sample_execution_attempt("ENTRY"),
        ], symbol="EURUSD")
        s3.add("management_actions", [
            _sample_management_action("CLOSE"),
        ], symbol="EURUSD")
        report = run_dataset_evidence(symbol="EURUSD")
        assert set(report.keys()) == set(CONSUMED_EVIDENCE_DATASETS)
        assert report["horizon_candidates"]["record_count"] == 1
        assert report["strategy_candidates"]["record_count"] == 1
        assert report["execution_attempts"]["record_count"] == 1
        assert report["management_actions"]["record_count"] == 1
