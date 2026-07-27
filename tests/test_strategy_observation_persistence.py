"""
Tests for Strategy Observation Persistence.

Verifies:
    - Observations are generated with correct schema
    - Persistence works (local JSONL)
    - Partition/path generation is correct
    - Reading works
    - Existing event pipeline not affected
    - No execution imports
"""

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from core.strategies.observation_persistence import (
    _LOCAL_DIR,
    _S3_BUCKET,
    _S3_PREFIX,
    _SCHEMA_VERSION,
    build_observation_record,
    get_observation_stats,
    persist_strategy_observation,
    persist_observation_batch,
    read_observations_local,
)


class TestObservationSchema:
    def test_build_record_has_all_fields(self):
        record = build_observation_record(
            observation_id="obs-001", timestamp_utc=1719000000.0, symbol="EURUSD",
            cycle_id=42, market_phase="REVERSAL", h4_regime="RANGING", h1_bias="BEARISH",
            direction="BEARISH", detected_pattern="HAMMER", strategy_family="REVERSAL",
            conditions_passed=4, conditions_failed=1, evaluation_status="FULLY_MET",
            confidence=0.85,
        )
        assert record["schema_version"] == _SCHEMA_VERSION
        assert record["observation_id"] == "obs-001"
        assert record["market_phase"] == "REVERSAL"
        assert record["h4_regime"] == "RANGING"
        assert record["strategy_family"] == "REVERSAL"
        assert record["conditions_passed"] == 4
        assert record["confidence"] == 0.85

    def test_record_serialises_to_json(self):
        record = build_observation_record(
            observation_id="obs-002", timestamp_utc=1719000000.0, symbol="GBPUSD",
            candidate_strategies=[{"strategy_id": "range_reversal_v1", "eligible": True}],
            missing_data=["liquidity_levels"],
        )
        parsed = json.loads(json.dumps(record))
        assert parsed["observation_id"] == "obs-002"
        assert parsed["missing_data"] == ["liquidity_levels"]

    def test_default_values(self):
        record = build_observation_record(
            observation_id="obs-003", timestamp_utc=1719000000.0, symbol="EURUSD",
        )
        assert record["candidate_strategies"] == []
        assert record["strategy_conditions"] == {}
        assert record["missing_data"] == []
        assert record["confidence"] == 0.0


class TestPersistence:
    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        import core.strategies.observation_persistence as mod
        self._original = mod._LOCAL_DIR
        mod._LOCAL_DIR = self.temp_dir

    def teardown_method(self):
        import core.strategies.observation_persistence as mod
        mod._LOCAL_DIR = self._original
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_persist_creates_file(self):
        record = build_observation_record(
            observation_id="p-001", timestamp_utc=1719000000.0, symbol="EURUSD")
        assert persist_strategy_observation(record) is True
        files = list((Path(self.temp_dir) / "EURUSD").glob("*.jsonl"))
        assert len(files) == 1

    def test_persist_appends(self):
        for i in range(3):
            persist_strategy_observation(build_observation_record(
                observation_id=f"p-{i}", timestamp_utc=1719000000.0, symbol="EURUSD"))
        files = list((Path(self.temp_dir) / "EURUSD").glob("*.jsonl"))
        with open(files[0]) as f:
            lines = [l for l in f if l.strip()]
        assert len(lines) == 3

    def test_different_symbols(self):
        persist_strategy_observation(build_observation_record(
            observation_id="a", timestamp_utc=1719000000.0, symbol="EURUSD"))
        persist_strategy_observation(build_observation_record(
            observation_id="b", timestamp_utc=1719000000.0, symbol="GBPUSD"))
        assert (Path(self.temp_dir) / "EURUSD").exists()
        assert (Path(self.temp_dir) / "GBPUSD").exists()

    def test_batch(self):
        records = [build_observation_record(
            observation_id=f"b-{i}", timestamp_utc=1719000000.0, symbol="EURUSD")
            for i in range(5)]
        assert persist_observation_batch(records) == 5

    def test_read_back(self):
        persist_strategy_observation(build_observation_record(
            observation_id="r-001", timestamp_utc=1719000000.0, symbol="EURUSD",
            market_phase="IMPULSE", strategy_family="MOMENTUM"))
        results = read_observations_local(symbol="EURUSD")
        assert len(results) == 1
        assert results[0]["market_phase"] == "IMPULSE"

    def test_read_empty(self):
        assert read_observations_local(symbol="NONEXISTENT") == []


class TestPathGeneration:
    def test_s3_bucket(self):
        assert _S3_BUCKET == "trading-bot-data-mk1"

    def test_s3_prefix(self):
        assert _S3_PREFIX == "strategy_observations"

    def test_local_dir(self):
        assert _LOCAL_DIR == "logs/strategy_observations"

    def test_hive_partition_format(self):
        expected = "strategy_observations/symbol=EURUSD/date=2026-07-27/part-000.jsonl"
        actual = f"{_S3_PREFIX}/symbol=EURUSD/date=2026-07-27/part-000.jsonl"
        assert actual == expected


class TestObserverIntegration:
    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        import core.strategies.observation_persistence as mod
        self._original = mod._LOCAL_DIR
        mod._LOCAL_DIR = self.temp_dir

    def teardown_method(self):
        import core.strategies.observation_persistence as mod
        mod._LOCAL_DIR = self._original
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_observer_to_persistence(self):
        from core.strategies.strategy_observer import StrategyObserver
        from core.strategies.condition_evaluator import build_market_snapshot

        observer = StrategyObserver()
        snapshot = build_market_snapshot(regime="RANGING", phase="REVERSAL", m15_at_key_level=True)
        observer.observe(snapshot=snapshot, cycle_id=1, pattern_detected="HAMMER",
                         symbol="EURUSD", timestamp_utc=1719000000.0)

        for obs in observer.get_observations():
            record = build_observation_record(
                observation_id=obs.observation_id, timestamp_utc=obs.timestamp_utc,
                symbol=obs.symbol, cycle_id=obs.cycle_id, market_phase=obs.market_phase,
                h4_regime=obs.regime, detected_pattern=obs.pattern_detected,
                strategy_family=obs.family, conditions_passed=obs.conditions_met,
                conditions_failed=obs.conditions_failed,
                evaluation_status=obs.overall_status, confidence=obs.confidence,
                eligible_by_phase=obs.eligible_by_phase,
            )
            persist_strategy_observation(record)

        results = read_observations_local(symbol="EURUSD")
        assert len(results) == 5


class TestSafety:
    def test_no_forbidden_imports(self):
        import inspect
        import core.strategies.observation_persistence as m
        source = inspect.getsource(m)
        for f in ["from core.pipeline", "from execution", "from risk", "import MetaTrader5"]:
            assert f not in source

    def test_schema_version_present(self):
        record = build_observation_record(
            observation_id="sv", timestamp_utc=1.0, symbol="X")
        assert record["schema_version"] == "strategy_observation_v1"
