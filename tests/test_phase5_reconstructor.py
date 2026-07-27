"""Tests for Phase 5 Event Reconstructor."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from phase5.event_reconstructor import (
    TradeEvent,
    EventReconstructionReport,
    VoterSnapshot,
    WeightIntelligenceSnapshot,
    reconstruct_events,
    _validate_event,
)


def _valid_record(**overrides) -> dict:
    base = {
        "trade_id": "t001",
        "timestamp": 1716422400.0,
        "symbol": "EURUSD",
        "production_decision": "BUY",
        "shadow_decision": "NO_TRADE",
        "pnl": 15.0,
        "outcome": "win",
        "ssi": 0.65,
        "agreement_score": 0.8,
        "conflict_types": ["spread_vs_direction"],
        "conflict_severity": "low",
        "system_state": "coherent",
        "voter_snapshot": {"bias": 1.2, "structure": 0.8, "session": 0.5, "spread": -0.3, "volatility": 0.6},
        "dominant_voters": ["bias", "structure"],
        "conflicting_voters": ["spread"],
        "weight_intelligence": {"current": {}, "recommended": {}, "deltas": {}},
    }
    base.update(overrides)
    return base


def _write_jsonl(records: list[dict]) -> Path:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8")
    for r in records:
        f.write(json.dumps(r) + "\n")
    f.close()
    return Path(f.name)


class TestValidation:
    def test_valid_record_passes(self):
        errors = _validate_event(_valid_record())
        assert errors == []

    def test_missing_trade_id_fails(self):
        errors = _validate_event(_valid_record(trade_id=""))
        assert any("trade_id" in e for e in errors)

    def test_invalid_decision_fails(self):
        errors = _validate_event(_valid_record(production_decision="HOLD"))
        assert any("production_decision" in e for e in errors)

    def test_invalid_outcome_fails(self):
        errors = _validate_event(_valid_record(outcome="unknown"))
        assert any("outcome" in e for e in errors)

    def test_ssi_out_of_range_fails(self):
        errors = _validate_event(_valid_record(ssi=1.5))
        assert any("ssi" in e for e in errors)

    def test_agreement_out_of_range_fails(self):
        errors = _validate_event(_valid_record(agreement_score=-0.1))
        assert any("agreement" in e for e in errors)

    def test_invalid_severity_fails(self):
        errors = _validate_event(_valid_record(conflict_severity="extreme"))
        assert any("severity" in e for e in errors)

    def test_invalid_system_state_fails(self):
        errors = _validate_event(_valid_record(system_state="broken"))
        assert any("system_state" in e for e in errors)


class TestReconstruction:
    def test_valid_file_reconstructs(self):
        path = _write_jsonl([_valid_record(), _valid_record(trade_id="t002")])
        events, report = reconstruct_events(path)
        assert len(events) == 2
        assert report.reconstructed_events == 2
        assert report.invalid_events == 0
        assert report.dropped_records == 0
        Path(path).unlink()

    def test_invalid_json_dropped(self):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8")
        f.write("not json\n")
        f.write(json.dumps(_valid_record()) + "\n")
        f.close()
        events, report = reconstruct_events(Path(f.name))
        assert len(events) == 1
        assert report.dropped_records == 1
        Path(f.name).unlink()

    def test_invalid_record_counted(self):
        path = _write_jsonl([_valid_record(), _valid_record(outcome="bad")])
        events, report = reconstruct_events(path)
        assert len(events) == 1
        assert report.invalid_events == 1
        Path(path).unlink()

    def test_empty_file(self):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8")
        f.close()
        events, report = reconstruct_events(Path(f.name))
        assert events == []
        assert report.total_records == 0
        Path(f.name).unlink()

    def test_missing_file(self):
        events, report = reconstruct_events(Path("/nonexistent/file.jsonl"))
        assert events == []
        assert report.total_records == 0

    def test_trade_event_is_frozen(self):
        path = _write_jsonl([_valid_record()])
        events, _ = reconstruct_events(path)
        with pytest.raises(Exception):
            events[0].pnl = 999  # type: ignore
        Path(path).unlink()

    def test_voter_snapshot_populated(self):
        path = _write_jsonl([_valid_record()])
        events, _ = reconstruct_events(path)
        assert events[0].voter_snapshot.bias == 1.2
        assert events[0].voter_snapshot.spread == -0.3
        Path(path).unlink()

    def test_report_totals_correct(self):
        records = [
            _valid_record(trade_id="t1"),
            _valid_record(trade_id="t2", ssi=2.0),  # invalid
            _valid_record(trade_id="t3"),
        ]
        path = _write_jsonl(records)
        events, report = reconstruct_events(path)
        assert report.total_records == 3
        assert report.reconstructed_events == 2
        assert report.invalid_events == 1
        Path(path).unlink()
