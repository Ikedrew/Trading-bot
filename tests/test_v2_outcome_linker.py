"""
Tests for V2 Outcome Linker.

Verifies:
    - Successful entity_id match
    - correlation_id fallback
    - symbol+timestamp fallback
    - Missing trade handling (no match)
    - Multiple opportunities handled correctly
    - Outcome persistence
    - No regression to shadow trades
"""

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from core.research.v2_outcome_linker import link_outcomes, LinkageReport


def _make_v2_opportunity(
    symbol: str = "EURUSD",
    timestamp_utc: float = 1753574400.0,
    correlation_id: str = "EURUSD_1753574400",
    pattern: str = "HAMMER",
) -> dict:
    """Create a minimal V2Opportunity record (as dict)."""
    return {
        "schema_version": "v2_opportunity_1.0",
        "opportunity_id": f"v2_{symbol}_{int(timestamp_utc)}_abc12345",
        "correlation_id": correlation_id,
        "timestamp_utc": timestamp_utc,
        "symbol": symbol,
        "timeframe": "M5",
        "h4_regime": "RANGING",
        "h1_bias": "BULLISH",
        "h1_bos_confirmed": True,
        "pattern_detected": pattern,
        "pattern_direction": "BUY",
        "pattern_quality": 0.8,
        "bid": 1.085,
        "ask": 1.0851,
        "spread": 0.0001,
        "atr": 0.0008,
        "session": "LONDON",
        "proposed_direction": "BUY",
        "candle_stop_distance": 0.00027,
        "structure_stop_distance": 0.00113,
        "risk_distance_pips": 11.3,
        # Outcome fields (unlinked)
        "outcome_recorded": False,
        "outcome_raw_r": None,
        "mfe": None,
        "mae": None,
        "reached_positive_target": None,
        "reached_negative_target": None,
        "bars_to_outcome": None,
    }


def _make_shadow_trade(
    symbol: str = "EURUSD",
    entity_id: str = "EURUSD_1753574400",
    correlation_id: str = "COR-100-EURUSD",
    entry_time: float = 1753574400.0,
    result_r: float = 1.8,
    mfe_r: float = 2.4,
    mae_r: float = -0.3,
    exit_reason: str = "TIMEOUT",
    bars_held: int = 9,
) -> dict:
    """Create a minimal shadow trade record (v2 schema)."""
    return {
        "schema_version": "shadow_trades_v2",
        "source": "shadow_trade_engine",
        "identity": {
            "trade_id": f"SH_{symbol}_{int(entry_time)}",
            "correlation_id": correlation_id,
            "symbol": symbol,
            "strategy_id": "m5_pattern_v1",
            "cycle_id": "100",
            "entity_id": entity_id,
        },
        "decision_snapshot": {
            "timestamp_decision_utc": entry_time,
            "entry_intent_price": 1.085,
            "stop_loss_intent": 1.0837,
            "take_profit_intent": 1.0876,
            "direction": "BUY",
            "pattern": "HAMMER",
            "score": 0.8,
        },
        "simulation_environment": {
            "htf_snapshot": None,
            "entry_bar_index": 60,
        },
        "simulated_outcome": {
            "exit_price": 1.0873,
            "exit_timestamp": entry_time + bars_held * 300,
            "pnl_r_multiple": result_r,
            "mfe_r": mfe_r,
            "mae_r": mae_r,
            "exit_reason": exit_reason,
            "bars_held": bars_held,
        },
    }


class TestEntityIdMatch:
    """Priority 1: match by entity_id."""

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.v2_dir = Path(self.temp_dir) / "v2"
        self.shadow_dir = Path(self.temp_dir) / "shadow"

    def teardown_method(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_opp(self, opp: dict):
        sym = opp["symbol"]
        path = self.v2_dir / sym / "2025-07-27.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(opp) + "\n")

    def _write_trade(self, trade: dict):
        sym = trade["identity"]["symbol"]
        path = self.shadow_dir / sym / "2025-07-27.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(trade) + "\n")

    def test_entity_id_match(self):
        """Matches via entity_id (correlation_id == entity_id)."""
        opp = _make_v2_opportunity(correlation_id="EURUSD_1753574400")
        trade = _make_shadow_trade(entity_id="EURUSD_1753574400", result_r=1.8)
        self._write_opp(opp)
        self._write_trade(trade)

        report = link_outcomes(
            symbol="EURUSD",
            v2_dir=str(self.v2_dir),
            shadow_dir=str(self.shadow_dir),
            persist=False,
        )

        assert report.matched == 1
        assert report.match_by_entity_id == 1
        assert report.linked_records[0]["outcome_recorded"] is True
        assert report.linked_records[0]["outcome_raw_r"] == 1.8
        assert report.linked_records[0]["_linkage"]["win"] is True
        assert report.linked_records[0]["_linkage"]["exit_reason"] == "TIMEOUT"

    def test_mfe_mae_attached(self):
        """MFE and MAE from shadow trade attached to opportunity."""
        opp = _make_v2_opportunity(correlation_id="EURUSD_1753574400")
        trade = _make_shadow_trade(
            entity_id="EURUSD_1753574400", mfe_r=2.4, mae_r=-0.3)
        self._write_opp(opp)
        self._write_trade(trade)

        report = link_outcomes(
            symbol="EURUSD",
            v2_dir=str(self.v2_dir),
            shadow_dir=str(self.shadow_dir),
            persist=False,
        )

        rec = report.linked_records[0]
        assert rec["mfe"] == 2.4
        assert rec["mae"] == -0.3

    def test_hold_minutes_computed(self):
        """Hold time in minutes computed from bars_held * 5."""
        opp = _make_v2_opportunity(correlation_id="EURUSD_1753574400")
        trade = _make_shadow_trade(entity_id="EURUSD_1753574400", bars_held=9)
        self._write_opp(opp)
        self._write_trade(trade)

        report = link_outcomes(
            symbol="EURUSD",
            v2_dir=str(self.v2_dir),
            shadow_dir=str(self.shadow_dir),
            persist=False,
        )

        assert report.linked_records[0]["_linkage"]["hold_minutes"] == 45


class TestCorrelationIdFallback:
    """Priority 2: fallback to correlation_id."""

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.v2_dir = Path(self.temp_dir) / "v2"
        self.shadow_dir = Path(self.temp_dir) / "shadow"

    def teardown_method(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_opp(self, opp: dict):
        sym = opp["symbol"]
        path = self.v2_dir / sym / "2025-07-27.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(opp) + "\n")

    def _write_trade(self, trade: dict):
        sym = trade["identity"]["symbol"]
        path = self.shadow_dir / sym / "2025-07-27.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(trade) + "\n")

    def test_correlation_id_fallback(self):
        """Matches via correlation_id when entity_id doesn't match."""
        opp = _make_v2_opportunity(correlation_id="COR-200-EURUSD")
        trade = _make_shadow_trade(
            entity_id="DIFFERENT_ENTITY",  # Won't match opp's correlation_id
            correlation_id="COR-200-EURUSD",
            result_r=-0.5,
        )
        self._write_opp(opp)
        self._write_trade(trade)

        report = link_outcomes(
            symbol="EURUSD",
            v2_dir=str(self.v2_dir),
            shadow_dir=str(self.shadow_dir),
            persist=False,
        )

        assert report.matched == 1
        assert report.match_by_correlation_id == 1
        assert report.linked_records[0]["_linkage"]["win"] is False
        assert report.linked_records[0]["outcome_raw_r"] == -0.5


class TestTimestampFallback:
    """Priority 3: fallback to symbol + timestamp tolerance."""

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.v2_dir = Path(self.temp_dir) / "v2"
        self.shadow_dir = Path(self.temp_dir) / "shadow"

    def teardown_method(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_opp(self, opp: dict):
        sym = opp["symbol"]
        path = self.v2_dir / sym / "2025-07-27.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(opp) + "\n")

    def _write_trade(self, trade: dict):
        sym = trade["identity"]["symbol"]
        path = self.shadow_dir / sym / "2025-07-27.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(trade) + "\n")

    def test_timestamp_fallback_within_tolerance(self):
        """Matches via timestamp when within 300s tolerance."""
        opp = _make_v2_opportunity(
            correlation_id="NO_MATCH_ID",
            timestamp_utc=1753574400.0,
        )
        trade = _make_shadow_trade(
            entity_id="DIFFERENT",
            correlation_id="ALSO_DIFFERENT",
            entry_time=1753574500.0,  # 100s difference — within tolerance
            result_r=0.5,
        )
        self._write_opp(opp)
        self._write_trade(trade)

        report = link_outcomes(
            symbol="EURUSD",
            v2_dir=str(self.v2_dir),
            shadow_dir=str(self.shadow_dir),
            persist=False,
        )

        assert report.matched == 1
        assert report.match_by_timestamp == 1

    def test_timestamp_beyond_tolerance_no_match(self):
        """No match when timestamp difference exceeds tolerance."""
        opp = _make_v2_opportunity(
            correlation_id="NO_MATCH",
            timestamp_utc=1753574400.0,
        )
        trade = _make_shadow_trade(
            entity_id="DIFFERENT",
            correlation_id="ALSO_DIFFERENT",
            entry_time=1753575000.0,  # 600s difference — beyond tolerance
            result_r=0.5,
        )
        self._write_opp(opp)
        self._write_trade(trade)

        report = link_outcomes(
            symbol="EURUSD",
            v2_dir=str(self.v2_dir),
            shadow_dir=str(self.shadow_dir),
            persist=False,
        )

        assert report.matched == 0
        assert report.unmatched == 1


class TestMissingTradeHandling:
    """Unmatched opportunities retain empty outcome."""

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.v2_dir = Path(self.temp_dir) / "v2"
        self.shadow_dir = Path(self.temp_dir) / "shadow"

    def teardown_method(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_opp(self, opp: dict):
        sym = opp["symbol"]
        path = self.v2_dir / sym / "2025-07-27.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(opp) + "\n")

    def test_no_shadow_trades(self):
        """Opportunities without matching trades stay unlinked."""
        opp = _make_v2_opportunity()
        self._write_opp(opp)
        # No shadow trades written

        report = link_outcomes(
            symbol="EURUSD",
            v2_dir=str(self.v2_dir),
            shadow_dir=str(self.shadow_dir),
            persist=False,
        )

        assert report.total_opportunities == 1
        assert report.matched == 0
        assert report.unmatched == 1
        rec = report.linked_records[0]
        assert rec["_linkage"]["linked"] is False
        assert rec["_linkage"]["result_r"] is None

    def test_empty_directories(self):
        """No crash on empty directories."""
        report = link_outcomes(
            symbol="EURUSD",
            v2_dir=str(self.v2_dir),
            shadow_dir=str(self.shadow_dir),
            persist=False,
        )
        assert report.total_opportunities == 0
        assert report.matched == 0


class TestMultipleOpportunities:
    """Multiple opportunities linked correctly."""

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.v2_dir = Path(self.temp_dir) / "v2"
        self.shadow_dir = Path(self.temp_dir) / "shadow"

    def teardown_method(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_opp(self, opp: dict):
        sym = opp["symbol"]
        path = self.v2_dir / sym / "2025-07-27.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(opp) + "\n")

    def _write_trade(self, trade: dict):
        sym = trade["identity"]["symbol"]
        path = self.shadow_dir / sym / "2025-07-27.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(trade) + "\n")

    def test_multiple_mixed_results(self):
        """Multiple opportunities: some match, some don't."""
        opp1 = _make_v2_opportunity(
            correlation_id="EURUSD_100", timestamp_utc=100.0)
        opp2 = _make_v2_opportunity(
            correlation_id="EURUSD_200", timestamp_utc=200.0)
        opp3 = _make_v2_opportunity(
            correlation_id="NO_MATCH", timestamp_utc=99999.0)

        trade1 = _make_shadow_trade(
            entity_id="EURUSD_100", entry_time=100.0, result_r=1.5)
        trade2 = _make_shadow_trade(
            entity_id="EURUSD_200", entry_time=200.0, result_r=-1.0)

        for opp in [opp1, opp2, opp3]:
            self._write_opp(opp)
        for trade in [trade1, trade2]:
            self._write_trade(trade)

        report = link_outcomes(
            symbol="EURUSD",
            v2_dir=str(self.v2_dir),
            shadow_dir=str(self.shadow_dir),
            persist=False,
        )

        assert report.total_opportunities == 3
        assert report.matched == 2
        assert report.unmatched == 1


class TestPersistence:
    """Linked records persist back to disk."""

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.v2_dir = Path(self.temp_dir) / "v2"
        self.shadow_dir = Path(self.temp_dir) / "shadow"

    def teardown_method(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_opp(self, opp: dict):
        sym = opp["symbol"]
        path = self.v2_dir / sym / "2025-07-27.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(opp) + "\n")

    def _write_trade(self, trade: dict):
        sym = trade["identity"]["symbol"]
        path = self.shadow_dir / sym / "2025-07-27.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(trade) + "\n")

    def test_linked_records_persisted(self):
        """Persist=True writes updated records to JSONL."""
        opp = _make_v2_opportunity(
            correlation_id="EURUSD_1753574400",
            timestamp_utc=1753574400.0,
        )
        trade = _make_shadow_trade(entity_id="EURUSD_1753574400", result_r=2.0)
        self._write_opp(opp)
        self._write_trade(trade)

        report = link_outcomes(
            symbol="EURUSD",
            v2_dir=str(self.v2_dir),
            shadow_dir=str(self.shadow_dir),
            persist=True,
        )

        # Read back persisted file
        files = list(self.v2_dir.rglob("*.jsonl"))
        assert len(files) >= 1
        with open(files[0]) as f:
            record = json.loads(f.readline())
        assert record["outcome_recorded"] is True
        assert record["outcome_raw_r"] == 2.0
        assert record["_linkage"]["linked"] is True


class TestSchemaIntegrity:
    """Observation fields are preserved after linkage."""

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.v2_dir = Path(self.temp_dir) / "v2"
        self.shadow_dir = Path(self.temp_dir) / "shadow"

    def teardown_method(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_opp(self, opp: dict):
        sym = opp["symbol"]
        path = self.v2_dir / sym / "2025-07-27.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(opp) + "\n")

    def _write_trade(self, trade: dict):
        sym = trade["identity"]["symbol"]
        path = self.shadow_dir / sym / "2025-07-27.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(trade) + "\n")

    def test_original_fields_preserved(self):
        """Linkage does not destroy original observation data."""
        opp = _make_v2_opportunity(correlation_id="EURUSD_1753574400")
        trade = _make_shadow_trade(entity_id="EURUSD_1753574400")
        self._write_opp(opp)
        self._write_trade(trade)

        report = link_outcomes(
            symbol="EURUSD",
            v2_dir=str(self.v2_dir),
            shadow_dir=str(self.shadow_dir),
            persist=False,
        )

        rec = report.linked_records[0]
        # Original context preserved
        assert rec["h4_regime"] == "RANGING"
        assert rec["h1_bias"] == "BULLISH"
        assert rec["pattern_detected"] == "HAMMER"
        assert rec["bid"] == 1.085
        assert rec["spread"] == 0.0001
        assert rec["session"] == "LONDON"

    def test_schema_version_preserved(self):
        """Schema version is not altered by linkage."""
        opp = _make_v2_opportunity(correlation_id="EURUSD_1753574400")
        trade = _make_shadow_trade(entity_id="EURUSD_1753574400")
        self._write_opp(opp)
        self._write_trade(trade)

        report = link_outcomes(
            symbol="EURUSD",
            v2_dir=str(self.v2_dir),
            shadow_dir=str(self.shadow_dir),
            persist=False,
        )

        assert report.linked_records[0]["schema_version"] == "v2_opportunity_1.0"


class TestNoShadowTradeRegression:
    """Linker does not modify shadow trade files."""

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.v2_dir = Path(self.temp_dir) / "v2"
        self.shadow_dir = Path(self.temp_dir) / "shadow"

    def teardown_method(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_opp(self, opp: dict):
        sym = opp["symbol"]
        path = self.v2_dir / sym / "2025-07-27.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(opp) + "\n")

    def _write_trade(self, trade: dict):
        sym = trade["identity"]["symbol"]
        path = self.shadow_dir / sym / "2025-07-27.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(trade) + "\n")

    def test_shadow_files_unchanged(self):
        """Shadow trade JSONL files are never modified."""
        opp = _make_v2_opportunity(correlation_id="EURUSD_1753574400")
        trade = _make_shadow_trade(entity_id="EURUSD_1753574400")
        self._write_opp(opp)
        self._write_trade(trade)

        # Capture shadow file content before
        shadow_files = list(self.shadow_dir.rglob("*.jsonl"))
        before = {}
        for f in shadow_files:
            before[str(f)] = f.read_text()

        link_outcomes(
            symbol="EURUSD",
            v2_dir=str(self.v2_dir),
            shadow_dir=str(self.shadow_dir),
            persist=True,
        )

        # Verify shadow files unchanged
        for f in shadow_files:
            assert f.read_text() == before[str(f)]


class TestLinkageReport:
    """Report provides useful summary."""

    def test_empty_report(self):
        """Empty report has zero match rate."""
        report = LinkageReport(
            total_opportunities=0, matched=0, unmatched=0,
            match_by_entity_id=0, match_by_correlation_id=0,
            match_by_timestamp=0, linked_records=[])
        assert report.match_rate == 0.0

    def test_summary_dict(self):
        """Summary dict has all expected keys."""
        report = LinkageReport(
            total_opportunities=10, matched=7, unmatched=3,
            match_by_entity_id=5, match_by_correlation_id=1,
            match_by_timestamp=1, linked_records=[])
        s = report.summary()
        assert s["total_opportunities"] == 10
        assert s["matched"] == 7
        assert s["match_rate"] == 0.7
        assert s["by_entity_id"] == 5
