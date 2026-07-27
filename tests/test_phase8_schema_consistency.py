"""
Phase 8 Schema Consistency Tests.

Validates:
1. Live trade ? canonical event ? JSONL write
2. Offline loader reads same event without modification
3. Cohort classification uses ONLY stored fields
4. No recalculation of timing or confirmation occurs
5. Round-trip serialization equality

NO execution logic testing. ONLY data consistency validation.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.trade_schema import CanonicalTradeEvent, TradeOutcome, to_dict
from tools.trade_logging.live_trade_snapshot import load_canonical_trades, _dict_to_canonical
from tools.cohort_analysis.cohort_builder import build_cohort_from_trade


# --- FIXTURES -----------------------------------------------------------------

def _sample_event() -> CanonicalTradeEvent:
    return CanonicalTradeEvent(
        trade_id="test_001",
        symbol="EURUSD",
        entry_time="2026-06-10T10:00:00+00:00",
        exit_time="2026-06-10T10:30:00+00:00",
        entry_price=1.10000,
        exit_price=1.10500,
        position_size=0.01,
        entry_r=0.0,
        final_r=2.0,
        mfe=2.5,
        mae=-0.3,
        outcome=TradeOutcome.WIN,
        confirmation_strength="STRONG",
        entry_timing="EARLY",
        market_regime="TRENDING",
        breakeven_triggered=True,
        trailing_triggered=False,
        partials_taken=[1.0],
    )


# -------------------------------------------------------------------------------
# TEST 1: LIVE TRADE ? CANONICAL EVENT ? JSONL WRITE
# -------------------------------------------------------------------------------

class TestCanonicalWriteCycle:

    def test_event_serializes_to_json(self):
        """CanonicalTradeEvent can be serialized to JSON string."""
        event = _sample_event()
        data = to_dict(event)
        json_str = json.dumps(data, default=str)

        assert len(json_str) > 50
        assert "test_001" in json_str
        assert "EURUSD" in json_str

    def test_write_to_jsonl_file(self, tmp_path):
        """Event writes to JSONL file as single line."""
        event = _sample_event()
        filepath = tmp_path / "trades.jsonl"

        data = to_dict(event)
        line = json.dumps(data, default=str, separators=(",", ":")) + "\n"
        filepath.write_text(line)

        content = filepath.read_text()
        assert content.count("\n") == 1
        parsed = json.loads(content.strip())
        assert parsed["trade_id"] == "test_001"

    def test_multiple_writes_append(self, tmp_path):
        """Multiple events append correctly."""
        filepath = tmp_path / "trades.jsonl"

        for i in range(3):
            event = CanonicalTradeEvent(
                trade_id=f"t_{i}",
                symbol="EURUSD",
                entry_time="x",
                final_r=float(i),
                confirmation_strength="STRONG",
                entry_timing="MID",
                market_regime="TRENDING",
            )
            data = to_dict(event)
            with open(filepath, "a") as f:
                f.write(json.dumps(data, default=str, separators=(",", ":")) + "\n")

        lines = filepath.read_text().strip().split("\n")
        assert len(lines) == 3


# -------------------------------------------------------------------------------
# TEST 2: OFFLINE LOADER READS WITHOUT MODIFICATION
# -------------------------------------------------------------------------------

class TestOfflineLoaderFidelity:

    def test_loader_reads_exact_fields(self, tmp_path):
        """Loaded event has identical field values to written event."""
        event = _sample_event()
        filepath = tmp_path / "canonical.jsonl"

        data = to_dict(event)
        filepath.write_text(json.dumps(data, default=str, separators=(",", ":")) + "\n")

        loaded = load_canonical_trades(str(filepath))

        assert len(loaded) == 1
        result = loaded[0]
        assert result.trade_id == event.trade_id
        assert result.symbol == event.symbol
        assert result.final_r == event.final_r
        assert result.mfe == event.mfe
        assert result.mae == event.mae
        assert result.confirmation_strength == event.confirmation_strength
        assert result.entry_timing == event.entry_timing
        assert result.market_regime == event.market_regime
        assert result.breakeven_triggered == event.breakeven_triggered
        assert result.trailing_triggered == event.trailing_triggered
        assert result.outcome == event.outcome

    def test_loader_does_not_modify_confirmation_strength(self, tmp_path):
        """Loaded confirmation_strength is exactly what was written (not recalculated)."""
        filepath = tmp_path / "canonical.jsonl"
        data = to_dict(_sample_event())
        data["confirmation_strength"] = "WEAK"  # Explicitly set
        filepath.write_text(json.dumps(data, default=str, separators=(",", ":")) + "\n")

        loaded = load_canonical_trades(str(filepath))
        assert loaded[0].confirmation_strength == "WEAK"  # Not recalculated to STRONG

    def test_loader_does_not_modify_entry_timing(self, tmp_path):
        """Loaded entry_timing is exactly what was written (not recalculated)."""
        filepath = tmp_path / "canonical.jsonl"
        data = to_dict(_sample_event())
        data["entry_timing"] = "LATE"  # Override
        filepath.write_text(json.dumps(data, default=str, separators=(",", ":")) + "\n")

        loaded = load_canonical_trades(str(filepath))
        assert loaded[0].entry_timing == "LATE"  # Not reclassified

    def test_loader_does_not_modify_market_regime(self, tmp_path):
        """Loaded market_regime is exactly what was written (not recalculated)."""
        filepath = tmp_path / "canonical.jsonl"
        data = to_dict(_sample_event())
        data["market_regime"] = "RANGING"  # Override
        filepath.write_text(json.dumps(data, default=str, separators=(",", ":")) + "\n")

        loaded = load_canonical_trades(str(filepath))
        assert loaded[0].market_regime == "RANGING"  # Not reclassified


# -------------------------------------------------------------------------------
# TEST 3: COHORT CLASSIFICATION USES ONLY STORED FIELDS
# -------------------------------------------------------------------------------

class TestCohortUsesStoredFields:

    def test_cohort_from_canonical_dict(self):
        """Cohort builder uses canonical fields without recalculation."""
        record = {
            "confirmation": {"strength": "WEAK"},
            "entry_timing": "LATE",
            "engine_state": {"regime_state": "RANGING"},
        }

        cohort = build_cohort_from_trade(record)

        assert cohort.confirmation_strength == "WEAK"
        assert cohort.entry_timing == "LATE"
        assert cohort.market_regime == "RANGING"

    def test_cohort_does_not_override_stored_strength(self):
        """Cohort builder does not apply any recalculation to strength."""
        # Even if body_pct suggests STRONG, the stored field is authoritative
        record = {
            "confirmation": {"strength": "INVALID", "body_pct": 0.90},
            "entry_timing": "EARLY",
            "engine_state": {"regime_state": "TRENDING"},
        }

        cohort = build_cohort_from_trade(record)
        assert cohort.confirmation_strength == "INVALID"  # Uses stored, not recalculated

    def test_cohort_does_not_reclassify_timing(self):
        """Cohort builder does not reclassify entry_timing from metrics."""
        record = {
            "confirmation": {"strength": "STRONG", "body_pct": 0.85, "wick_ratio": 0.15},
            "entry_timing": "LATE",  # Stored as LATE despite EARLY-like metrics
            "engine_state": {"regime_state": "TRENDING"},
        }

        cohort = build_cohort_from_trade(record)
        assert cohort.entry_timing == "LATE"  # Respects stored value


# -------------------------------------------------------------------------------
# TEST 4: NO RECALCULATION OF TIMING OR CONFIRMATION
# -------------------------------------------------------------------------------

class TestNoRecalculation:

    def test_loaded_event_has_no_computed_fields(self, tmp_path):
        """CanonicalTradeEvent from JSONL has no dynamically computed attributes."""
        filepath = tmp_path / "canonical.jsonl"
        data = to_dict(_sample_event())
        filepath.write_text(json.dumps(data, default=str, separators=(",", ":")) + "\n")

        loaded = load_canonical_trades(str(filepath))[0]

        # These are stored values, not properties or computed
        assert isinstance(loaded.confirmation_strength, str)
        assert isinstance(loaded.entry_timing, str)
        assert isinstance(loaded.market_regime, str)
        assert isinstance(loaded.mfe, float)
        assert isinstance(loaded.mae, float)

    def test_cohort_pipeline_reads_not_computes(self):
        """Full cohort pipeline path reads fields, never invokes classify_entry_timing."""
        from tools.cohort_analysis.loader import _canonical_to_analysis_record

        canonical_record = {
            "trade_id": "t1",
            "symbol": "EU",
            "confirmation_strength": "WEAK",
            "entry_timing": "MID",
            "market_regime": "TRENDING",
            "final_r": 1.5,
            "outcome": "WIN",
            "mfe": 2.0,
            "mae": -0.2,
        }

        result = _canonical_to_analysis_record(canonical_record)

        # Verify it reads stored values, not recomputed
        assert result["confirmation"]["strength"] == "WEAK"
        assert result["entry_timing"] == "MID"
        assert result["engine_state"]["regime_state"] == "TRENDING"
        assert result["mfe_r"] == 2.0
        assert result["mae_r"] == -0.2


# -------------------------------------------------------------------------------
# TEST 5: ROUND-TRIP SERIALIZATION EQUALITY
# -------------------------------------------------------------------------------

class TestRoundTripEquality:

    def test_serialize_deserialize_roundtrip(self):
        """Event survives serialize ? JSON ? deserialize with identical values."""
        original = _sample_event()

        # Serialize
        data = to_dict(original)
        json_str = json.dumps(data, default=str, separators=(",", ":"))

        # Deserialize
        parsed = json.loads(json_str)
        restored = _dict_to_canonical(parsed)

        assert restored.trade_id == original.trade_id
        assert restored.symbol == original.symbol
        assert restored.entry_time == original.entry_time
        assert restored.exit_time == original.exit_time
        assert restored.entry_price == original.entry_price
        assert restored.exit_price == original.exit_price
        assert restored.position_size == original.position_size
        assert restored.final_r == original.final_r
        assert restored.mfe == original.mfe
        assert restored.mae == original.mae
        assert restored.outcome == original.outcome
        assert restored.confirmation_strength == original.confirmation_strength
        assert restored.entry_timing == original.entry_timing
        assert restored.market_regime == original.market_regime
        assert restored.breakeven_triggered == original.breakeven_triggered
        assert restored.trailing_triggered == original.trailing_triggered
        assert restored.partials_taken == original.partials_taken

    def test_file_roundtrip_equality(self, tmp_path):
        """Write to file ? read back ? values identical."""
        original = _sample_event()
        filepath = tmp_path / "roundtrip.jsonl"

        # Write
        data = to_dict(original)
        filepath.write_text(json.dumps(data, default=str, separators=(",", ":")) + "\n")

        # Read
        loaded = load_canonical_trades(str(filepath))[0]

        assert loaded.trade_id == original.trade_id
        assert loaded.final_r == original.final_r
        assert loaded.mfe == original.mfe
        assert loaded.mae == original.mae
        assert loaded.confirmation_strength == original.confirmation_strength
        assert loaded.entry_timing == original.entry_timing
        assert loaded.market_regime == original.market_regime
        assert loaded.outcome == original.outcome

    def test_none_fields_survive_roundtrip(self):
        """None/null fields survive serialization roundtrip."""
        event = CanonicalTradeEvent(
            trade_id="t_none",
            symbol="TEST",
            entry_time="x",
            exit_time=None,
            exit_price=None,
            final_r=None,
            outcome=None,
            confirmation_strength="UNKNOWN",
            entry_timing="UNKNOWN",
            market_regime="UNKNOWN",
        )

        data = to_dict(event)
        json_str = json.dumps(data, default=str, separators=(",", ":"))
        parsed = json.loads(json_str)
        restored = _dict_to_canonical(parsed)

        assert restored.exit_time is None
        assert restored.exit_price is None
        assert restored.final_r is None
        assert restored.outcome is None
