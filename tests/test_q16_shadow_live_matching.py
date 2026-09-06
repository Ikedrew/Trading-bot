"""
Q16/X4 shadow ↔ live matching contract tests.

Proves:
  - field mapping: normalized shadow preserves canonical_opportunity_id;
    live trade_truth_v1 extraction resolves identity.canonical_opportunity_id
    and identity.correlation_id
  - valid pairing: 1:1 on the canonical lineage root
  - rejection: same symbol/different opportunity, symbol mismatch
  - multiple shadows: PRIMARY_HORIZON_SIMULATION preferred by explicit
    semantics; horizon-alternative-only canonicals excluded with accounting
  - duplicate safety: replay rows never inflate pairs; ambiguous live
    duplicates excluded
  - status behavior: structural mismatch → BLOCKED; genuine no-overlap →
    INSUFFICIENT_DATA/WAIT; enough matches → analysis runs (COMPLETE)

All tests are synthetic — production AWS is NEVER touched.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from research_engine.correlation.linker import (
    ResearchRecord,
    build_research_records,
    extract_canonical_opportunity_id,
    match_shadow_to_live,
)

CANON_A = "EURUSD*1784800000*HAMMER"
CANON_B = "EURUSD*1784800300*ENGULFING"
CANON_C = "GBPUSD*1784800600*HAMMER"


def _shadow(
    canon: str = CANON_A,
    symbol: str | None = None,
    r: float | None = 1.5,
    shadow_type: str = "PRIMARY_HORIZON_SIMULATION",
    horizon: str = "SCALP",
    trade_id: str | None = None,
) -> dict[str, Any]:
    """Normalized shadow record (internal research shape from
    shadow_runtime_ingestion: identity / decision_snapshot / simulated_outcome)."""
    symbol = symbol or canon.split("*")[0]
    tid = trade_id or f"nshadow_1_{symbol}_{horizon}"
    return {
        "schema_version": "shadow_trades_v1",
        "identity": {
            "trade_id": tid,
            "shadow_trade_id": tid,
            "canonical_opportunity_id": canon,
            "symbol": symbol,
            "shadow_type": shadow_type,
            "evaluated_horizon": horizon,
        },
        "decision_snapshot": {
            "direction": "BUY",
            "pattern": canon.split("*")[-1],
            "score": 70.0,
        },
        "simulated_outcome": {
            "pnl_r_multiple": r,
            "exit_reason": "take_profit",
            "bars_held": 5,
        },
    }


def _truth(
    canon: str = CANON_A,
    symbol: str | None = None,
    r: float | None = 1.2,
    trade_id: str = "pos_1",
    correlation_id: str = "COR-20260904-1-EURUSD-AAAA",
) -> dict[str, Any]:
    """trade_truth_v1 nested record (identity / execution / outcome / exit)."""
    symbol = symbol or canon.split("*")[0]
    return {
        "schema_version": "trade_truth_v1",
        "identity": {
            "trade_id": trade_id,
            "correlation_id": correlation_id,
            "canonical_opportunity_id": canon,
            "symbol": symbol,
        },
        "execution": {"entry_fill_price": 1.1, "exit_fill_price": 1.12, "volume_executed": 0.1},
        "outcome": {"pnl_realised": 100.0, "r_multiple_realised": r},
        "exit": {"exit_reason": "take_profit_hit"},
    }


# ═══════════════════════════════════════════════════════════════════════════════
# FIELD MAPPING
# ═══════════════════════════════════════════════════════════════════════════════


class TestFieldMapping:
    def test_normalized_shadow_preserves_canonical_join_key(self):
        shadow = _shadow(canon=CANON_A)
        assert shadow["identity"]["canonical_opportunity_id"] == CANON_A
        assert extract_canonical_opportunity_id(shadow) == CANON_A

    def test_live_trade_truth_extraction_resolves_nested_identity(self):
        truth = _truth(canon=CANON_A)
        # trade_truth_v1 nests BOTH keys under identity — flat reads find nothing
        assert truth.get("correlation_id") is None
        assert extract_canonical_opportunity_id(truth) == CANON_A
        records = match_shadow_to_live([_shadow(canon=CANON_A)], [truth])[0]
        assert len(records) == 1
        rec = records[0]
        assert rec.is_matched()
        # the live COR-* spine is carried onto the matched record
        assert rec.correlation_id == "COR-20260904-1-EURUSD-AAAA"
        assert rec.canonical_opportunity_id == CANON_A

    def test_flat_canonical_fallback(self):
        rec = {"canonical_opportunity_id": CANON_A}
        assert extract_canonical_opportunity_id(rec) == CANON_A


# ═══════════════════════════════════════════════════════════════════════════════
# VALID PAIRING
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidPairing:
    def test_one_exact_same_opportunity_pair(self):
        records, diag = match_shadow_to_live(
            [_shadow(canon=CANON_A)], [_truth(canon=CANON_A)]
        )
        assert diag.matched_pairs == 1
        assert sum(1 for r in records if r.is_matched()) == 1
        assert diag.unmatched_shadow == 0
        assert diag.unmatched_live == 0

    def test_multiple_valid_exact_pairs(self):
        shadows = [_shadow(canon=CANON_A, trade_id="nsA"), _shadow(canon=CANON_B, trade_id="nsB")]
        truths = [
            _truth(canon=CANON_A, trade_id="pos_1", correlation_id="COR-1"),
            _truth(canon=CANON_B, trade_id="pos_2", correlation_id="COR-2"),
        ]
        records, diag = match_shadow_to_live(shadows, truths)
        assert diag.matched_pairs == 2
        assert sum(1 for r in records if r.is_matched()) == 2
        assert {r.canonical_opportunity_id for r in records if r.is_matched()} == {CANON_A, CANON_B}

    def test_prediction_error_computed(self):
        records, _ = match_shadow_to_live(
            [_shadow(canon=CANON_A, r=2.0)], [_truth(canon=CANON_A, r=0.5)]
        )
        assert records[0].prediction_error == pytest.approx(1.5)


# ═══════════════════════════════════════════════════════════════════════════════
# REJECTION
# ═══════════════════════════════════════════════════════════════════════════════


class TestRejection:
    def test_same_symbol_different_opportunity_never_pairs(self):
        records, diag = match_shadow_to_live(
            [_shadow(canon=CANON_A)], [_truth(canon=CANON_B)]
        )
        assert diag.matched_pairs == 0
        assert diag.unmatched_shadow == 1
        assert diag.unmatched_live == 1
        assert not any(r.is_matched() for r in records)

    def test_same_timestamp_different_opportunity_never_pairs(self):
        # identical bar_time in the canonical root, different symbol+pattern →
        # different opportunities even though timestamps coincide
        c1 = "EURUSD*1784800000*HAMMER"
        c2 = "GBPUSD*1784800000*HAMMER"
        records, diag = match_shadow_to_live([_shadow(canon=c1)], [_truth(canon=c2)])
        assert diag.matched_pairs == 0

    def test_mismatched_symbol_never_pairs(self):
        # canonical roots agree but identity symbols disagree — never pair
        shadow = _shadow(canon=CANON_A, symbol="EURUSD")
        truth = _truth(canon=CANON_A, symbol="GBPUSD")
        records, diag = match_shadow_to_live([shadow], [truth])
        assert diag.matched_pairs == 0
        assert diag.excluded_by_reason.get("symbol_mismatch_excluded") == 1
        assert not any(r.is_matched() for r in records)

    def test_live_only_and_shadow_only_reported(self):
        records, diag = match_shadow_to_live(
            [_shadow(canon=CANON_A)], [_truth(canon=CANON_B, trade_id="pos_9")]
        )
        assert diag.unmatched_shadow == 1 and diag.unmatched_live == 1
        assert sum(1 for r in records if r.has_shadow and not r.has_live) == 1
        assert sum(1 for r in records if r.has_live and not r.has_shadow) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# MULTIPLE SHADOWS (HORIZON SEMANTICS)
# ═══════════════════════════════════════════════════════════════════════════════


class TestMultipleShadows:
    def test_primary_horizon_preferred_over_alternative(self):
        shadows = [
            _shadow(canon=CANON_A, shadow_type="HORIZON_ALTERNATIVE", r=9.9,
                    trade_id="nshadow_1_EURUSD_EXTENDED"),
            _shadow(canon=CANON_A, shadow_type="PRIMARY_HORIZON_SIMULATION", r=1.5,
                    trade_id="nshadow_1_EURUSD_SCALP"),
        ]
        records, diag = match_shadow_to_live(shadows, [_truth(canon=CANON_A)])
        assert diag.matched_pairs == 1
        matched = [r for r in records if r.is_matched()]
        assert len(matched) == 1
        assert matched[0].shadow_r == 1.5  # the PRIMARY, never first/last chance

    def test_horizon_alternative_only_canonical_excluded(self):
        shadows = [_shadow(canon=CANON_A, shadow_type="HORIZON_ALTERNATIVE", r=9.9)]
        records, diag = match_shadow_to_live(shadows, [_truth(canon=CANON_A)])
        assert diag.matched_pairs == 0
        assert diag.excluded_by_reason.get("shadow_horizon_alternative_only") == 1
        assert diag.unmatched_shadow == 0  # never eligible in the first place

    def test_two_distinct_primaries_is_ambiguous_excluded(self):
        shadows = [
            _shadow(canon=CANON_A, r=1.0, trade_id="nshadow_1_EURUSD_SCALP"),
            _shadow(canon=CANON_A, r=2.0, trade_id="nshadow_2_EURUSD_SCALP"),
        ]
        records, diag = match_shadow_to_live(shadows, [_truth(canon=CANON_A)])
        assert diag.matched_pairs == 0
        assert diag.ambiguous_excluded == 1
        assert diag.excluded_by_reason.get("shadow_ambiguous_multiple_primaries") == 1


# ═══════════════════════════════════════════════════════════════════════════════
# DUPLICATE SAFETY
# ═══════════════════════════════════════════════════════════════════════════════


class TestDuplicateSafety:
    def test_shadow_replay_row_does_not_inflate_pairs(self):
        row = _shadow(canon=CANON_A, r=1.5)
        records, diag = match_shadow_to_live([row, dict(row)], [_truth(canon=CANON_A)])
        assert diag.matched_pairs == 1
        assert diag.excluded_by_reason.get("shadow_duplicate_replay_collapsed") == 1

    def test_live_replay_row_does_not_inflate_pairs(self):
        row = _truth(canon=CANON_A, r=1.2)
        records, diag = match_shadow_to_live([_shadow(canon=CANON_A)], [row, dict(row)])
        assert diag.matched_pairs == 1
        assert diag.excluded_by_reason.get("live_duplicate_replay_collapsed") == 1

    def test_ambiguous_distinct_live_outcomes_excluded_not_fabricated(self):
        truths = [
            _truth(canon=CANON_A, trade_id="pos_1", r=1.0),
            _truth(canon=CANON_A, trade_id="pos_2", r=-1.0),  # retry/partial ambiguity
        ]
        records, diag = match_shadow_to_live([_shadow(canon=CANON_A)], truths)
        assert diag.matched_pairs == 0
        assert diag.ambiguous_excluded == 1
        assert diag.excluded_by_reason.get("live_ambiguous_multiple_outcomes") == 1
        assert not any(r.is_matched() for r in records)

    def test_conflicting_replay_content_is_ambiguous(self):
        truths = [
            _truth(canon=CANON_A, trade_id="pos_1", r=1.0),
            _truth(canon=CANON_A, trade_id="pos_1", r=2.0),  # same id, conflicting R
        ]
        records, diag = match_shadow_to_live([_shadow(canon=CANON_A)], truths)
        assert diag.matched_pairs == 0
        assert diag.ambiguous_excluded == 1

    def test_records_missing_join_key_counted_not_dropped_silently(self):
        no_key_shadow = _shadow(canon=CANON_A)
        del no_key_shadow["identity"]["canonical_opportunity_id"]
        no_key_truth = _truth(canon=CANON_A)
        del no_key_truth["identity"]["canonical_opportunity_id"]
        _, diag = match_shadow_to_live([no_key_shadow], [no_key_truth])
        assert diag.matched_pairs == 0
        assert diag.excluded_by_reason.get("shadow_missing_canonical_key") == 1
        assert diag.excluded_by_reason.get("live_missing_canonical_key") == 1

    def test_records_missing_outcome_counted(self):
        _, diag = match_shadow_to_live(
            [_shadow(canon=CANON_A, r=None)], [_truth(canon=CANON_A, r=None)]
        )
        assert diag.matched_pairs == 0
        assert diag.excluded_by_reason.get("shadow_missing_outcome") == 1
        assert diag.excluded_by_reason.get("live_missing_outcome") == 1


# ═══════════════════════════════════════════════════════════════════════════════
# BACKWARD-COMPAT WRAPPER
# ═══════════════════════════════════════════════════════════════════════════════


class TestBuildResearchRecordsCompat:
    def test_returns_records_including_matched(self):
        records = build_research_records(
            [_shadow(canon=CANON_A), _shadow(canon=CANON_B, trade_id="nsB")],
            [_truth(canon=CANON_A)],
        )
        assert isinstance(records, list) and records
        assert sum(1 for r in records if r.is_matched()) == 1
        assert isinstance(records[0], ResearchRecord)


# ═══════════════════════════════════════════════════════════════════════════════
# STATUS BEHAVIOR (run() through its actual entry point)
# ═══════════════════════════════════════════════════════════════════════════════


class _StubSource:
    """Minimal stand-in for S3ResearchDataSource (no AWS)."""

    def __init__(self, truths: list[dict[str, Any]]):
        self._truths = truths

    def read_dataset(self, dataset: str, **kwargs: Any):
        assert dataset == "trade_truth"
        return self._truths


def _install(shadows: list[dict[str, Any]], truths: list[dict[str, Any]], monkeypatch):
    import research_engine.data_access.shadow_runtime_ingestion as sri
    import research_engine.data_access.s3_source as s3s

    monkeypatch.setattr(sri, "ingest_completed_shadow_trades", lambda **kw: shadows)
    monkeypatch.setattr(s3s, "get_default_source", lambda: _StubSource(truths))


class TestRunStatusBehavior:
    def _run(self, monkeypatch, tmp_path, shadows, truths):
        monkeypatch.chdir(tmp_path)  # report persists inside tmp, never production
        _install(shadows, truths, monkeypatch)
        from research_engine.experiments.shadow_validation import run
        return run()

    def test_empty_shadow_source_is_structurally_blocked(self, monkeypatch, tmp_path):
        report = self._run(monkeypatch, tmp_path, [], [_truth()])
        assert report["status"] == "BLOCKED"
        assert "shadow" in report["overall"]["finding"].lower()

    def test_empty_live_source_is_structurally_blocked(self, monkeypatch, tmp_path):
        report = self._run(monkeypatch, tmp_path, [_shadow()], [])
        assert report["status"] == "BLOCKED"
        assert "trade_truth" in report["overall"]["finding"].lower()

    def test_schema_mismatch_is_blocked_not_insufficient(self, monkeypatch, tmp_path):
        bad_shadow = _shadow()
        del bad_shadow["identity"]["canonical_opportunity_id"]
        report = self._run(monkeypatch, tmp_path, [bad_shadow], [_truth()])
        assert report["status"] == "BLOCKED"
        assert "schema mismatch" in report["overall"]["finding"].lower()

    def test_zero_pairs_with_valid_populations_is_insufficient_wait(self, monkeypatch, tmp_path):
        report = self._run(
            monkeypatch, tmp_path,
            [_shadow(canon=CANON_A)], [_truth(canon=CANON_B, trade_id="pos_9")],
        )
        assert report["status"] == "INSUFFICIENT_DATA"
        assert report["recommendation"] == "WAIT"
        assert "share no" in report["overall"]["finding"]
        diag = report["overall"]["match_diagnostics"]
        assert diag["matched_pairs"] == 0
        assert diag["unmatched_shadow"] == 1 and diag["unmatched_live"] == 1

    def test_enough_matches_runs_the_analysis(self, monkeypatch, tmp_path):
        shadows = [
            _shadow(canon=f"EURUSD*17848000{i}*HAMMER", r=1.0 + 0.1 * i, trade_id=f"ns{i}")
            for i in range(5)
        ]
        truths = [
            _truth(canon=f"EURUSD*17848000{i}*HAMMER", r=1.0 + 0.1 * i,
                   trade_id=f"pos_{i}", correlation_id=f"COR-{i}")
            for i in range(5)
        ]
        report = self._run(monkeypatch, tmp_path, shadows, truths)
        assert report["status"] == "COMPLETE"
        assert report["overall"]["matched_trades"] == 5
        assert report["overall"]["correlation"] is not None
        # join-key proof embedded in the report
        ex = report["overall"]["matched_examples"]
        assert ex and ex[0]["canonical_opportunity_id"].startswith("EURUSD*")
        assert ex[0]["live_correlation_id"].startswith("COR-")
        assert report["overall"]["join_contract"]["join_key"] == "canonical_opportunity_id"

    def test_report_persisted_and_valid_json(self, monkeypatch, tmp_path):
        self._run(monkeypatch, tmp_path, [_shadow()], [_truth(canon=CANON_A)])
        path = tmp_path / "analysis" / "reports" / "q16_shadow_validation.json"
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["question_id"] == "Q16"
        assert data["overall"]["matched_trades"] == 1


