"""
Tests for canonical observation_id on the strategy_observations dataset.

Verifies that every persisted observation record carries the repository's
canonical observation identity — core.identity.canonical.mint_observation_id —
deterministic from symbol + timeframe + bar timestamp.

The REAL production persistence path is exercised:
    core/pipeline/observers.py (dispatch)
        -> observe_strategy_intelligence(ctx)           [fire-and-forget]
            -> _do_observe(ctx)
                -> build_observation_record(observation_id=mint_observation_id(...))
                -> persist_strategy_observation(record)  [local JSONL + S3]

Proves:
    1. A normal observation receives the canonical observation_id.
    2. The ID is deterministic for the same symbol/timeframe/bar timestamp.
    3. Different bars produce different IDs.
    4. Different timeframes produce different IDs.
    5. Existing observation fields remain unchanged.
    6. No UUID/hash/alternative ID is generated for observation_id.
    8. Persistence failure does not affect observation generation.

(7 — downstream lineage tests still pass — is covered by running the
existing canonical/V10 lineage test suites.)
"""

import re
import shutil
import tempfile
import uuid as uuid_mod
from dataclasses import dataclass
from typing import Any

import pytest

from core.identity.canonical import mint_observation_id
from core.strategies.observation_persistence import read_observations_local
from core.strategies.strategy_intelligence_observer import (
    get_observer_instance,
    observe_strategy_intelligence,
    reset_observer,
)


@dataclass
class MockObserverContext:
    """Minimal mock of ObserverContext (same shape the pipeline dispatch sends)."""
    symbol: str = "EURUSD"
    cycle_id: int = 1
    bar_time: float = 1719000000.0
    engine_result: dict = None
    engine_state: Any = None
    candles: Any = None
    closed_i: int = 0
    bid: float = 1.08500
    ask: float = 1.08510
    config: Any = None
    detected_patterns: list = None
    risk_manager: Any = None
    htf_context: Any = None
    runtime_session_id: str = "test-session"
    decision_funnel: Any = None

    def __post_init__(self):
        if self.engine_result is None:
            self.engine_result = {
                "action": "NO_TRADE",
                "reason": "score_below_threshold",
                "score": 0.25,
                "pattern": "HAMMER",
                "market_phase": "REVERSAL",
                "activation_regime": "RANGING",
                "side": "",
            }
        if self.detected_patterns is None:
            self.detected_patterns = []


# Canonical format: {SYMBOL}.{TIMEFRAME}.{BAR_TIME}  e.g. EURUSD.M5.1719000000
_CANONICAL_RE = re.compile(r"^[A-Z0-9]+\.[A-Z0-9]+\.\d+$")


class TestCanonicalObservationID:
    """Canonical observation_id through the real persistence path."""

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        import core.strategies.observation_persistence as mod
        self._original = mod._LOCAL_DIR
        mod._LOCAL_DIR = self.temp_dir
        reset_observer()

    def teardown_method(self):
        import core.strategies.observation_persistence as mod
        mod._LOCAL_DIR = self._original
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        reset_observer()

    def _persist_one(self, **ctx_kwargs) -> dict:
        """Run the real production path and return the last persisted record."""
        ctx = MockObserverContext(**ctx_kwargs)
        observe_strategy_intelligence(ctx)
        results = read_observations_local(symbol=ctx.symbol)
        assert len(results) >= 1, "expected at least one persisted observation"
        return results[-1]

    # ── 1. Normal observation receives the canonical ID ──────────────────

    def test_normal_observation_gets_canonical_id(self):
        rec = self._persist_one(symbol="EURUSD", bar_time=1719000000.0)
        assert rec["observation_id"] == "EURUSD.M5.1719000000"
        # And it is exactly what the canonical function produces
        assert rec["observation_id"] == mint_observation_id(
            symbol="EURUSD", bar_time=1719000000.0, timeframe="M5",
        )

    # ── 2. Deterministic for the same symbol/timeframe/bar timestamp ─────

    def test_id_deterministic_for_same_bar(self):
        rec1 = self._persist_one(symbol="EURUSD", bar_time=1719000000.0)
        rec2 = self._persist_one(symbol="EURUSD", bar_time=1719000000.0)
        assert rec1["observation_id"] == rec2["observation_id"]
        assert rec1["observation_id"] == "EURUSD.M5.1719000000"

    # ── 3. Different bars produce different IDs ──────────────────────────

    def test_different_bars_produce_different_ids(self):
        rec1 = self._persist_one(symbol="EURUSD", bar_time=1719000000.0)
        rec2 = self._persist_one(symbol="EURUSD", bar_time=1719000300.0)
        assert rec1["observation_id"] != rec2["observation_id"]
        assert rec2["observation_id"] == "EURUSD.M5.1719000300"

    # ── 4. Different timeframes produce different IDs ────────────────────

    def test_different_timeframes_produce_different_ids(self):
        m5 = mint_observation_id(symbol="EURUSD", bar_time=1719000000.0, timeframe="M5")
        h1 = mint_observation_id(symbol="EURUSD", bar_time=1719000000.0, timeframe="H1")
        assert m5 == "EURUSD.M5.1719000000"
        assert h1 == "EURUSD.H1.1719000000"
        assert m5 != h1

    # ── 5. Existing observation fields remain unchanged ──────────────────

    def test_existing_observation_fields_unchanged(self):
        rec = self._persist_one(symbol="EURUSD", cycle_id=42, bar_time=1719000000.0)
        # Core schema fields
        assert rec["schema_version"] == "strategy_observation_v1"
        assert rec["timestamp_utc"] == 1719000000.0
        assert rec["symbol"] == "EURUSD"
        assert rec["cycle_id"] == 42
        assert rec["market_phase"] == "REVERSAL"
        assert rec["detected_pattern"] == "HAMMER"
        # Existing enrichment fields (unchanged by this pass)
        assert rec["bar_time"] == 1719000000
        assert rec["timeframe"] == "M5"
        assert "entity_id" in rec
        assert rec["canonical_opportunity_id"] == ""
        assert "decision_action" in rec
        assert "decision_score" in rec
        assert "decision_reason" in rec
        # Condition-evaluation fields still present
        assert "candidate_strategies" in rec
        assert "strategy_conditions" in rec
        assert "evaluation_status" in rec
        assert "confidence" in rec
        assert "tradability_score" in rec

    # ── 6. No UUID/hash/alternative ID for observation_id ────────────────

    def test_no_uuid_or_hash_used_as_observation_id(self):
        rec = self._persist_one(symbol="EURUSD", bar_time=1719000000.0)
        oid = rec["observation_id"]
        assert _CANONICAL_RE.match(oid), f"non-canonical observation_id: {oid}"
        # Explicitly not a UUID
        with pytest.raises(ValueError):
            uuid_mod.UUID(oid)
        # Not a bare hash (canonical form contains '.' separators)
        assert "." in oid

    # ── 8. Persistence failure does not affect observation generation ────

    def test_persistence_failure_does_not_affect_observation(self, monkeypatch):
        import core.strategies.observation_persistence as mod

        def _boom(record):
            raise RuntimeError("simulated disk failure")

        monkeypatch.setattr(mod, "persist_strategy_observation", _boom)

        ctx = MockObserverContext(symbol="EURUSD", bar_time=1719000000.0)
        # Must NOT raise despite persistence failing
        observe_strategy_intelligence(ctx)

        # Observation generation itself was unaffected
        obs = get_observer_instance().get_observations()
        assert len(obs) > 0
        assert all(o.symbol == "EURUSD" for o in obs)
