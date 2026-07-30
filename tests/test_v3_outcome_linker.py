"""
Tests for V3 Outcome Linker.

Verifies:
    1. V3 observation ID survives decision creation (entity_id match)
    2. Decision ID survives shadow trade creation (correlation_id fallback)
    3. Shadow trade links to outcome correctly
    4. Missing linkage is detected (NO_TRADE)
    5. NO_TRADE observations remain traceable
    6. Persistence works
    7. No shadow trade modification
"""

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from core.research.v3_outcome_linker import link_v3_outcomes, V3LinkageReport


def _make_v3_observation(
    symbol: str = "EURUSD",
    timestamp_utc: float = 1785255900.0,
    correlation_id: str = "EURUSD_1785255900",
) -> dict:
    """Create a minimal V3Opportunity record."""
    return {
        "schema_version": "v3_opportunity_v1",
        "opportunity_id": f"v3_{symbol}_{int(timestamp_utc)}_abc12345",
        "correlation_id": correlation_id,
        "timestamp_utc": timestamp_utc,
        "symbol": symbol,
        "timeframe": "M5",
        "price_at_observation": 1.085,
        "h1_range_position": 0.5,
        "equal_highs_above": True,
        "equal_highs_count": 2,
        "nearest_fvg_above_price": 1.087,
        "nearest_demand_ob_price": 1.083,
        "atr": 0.0012,
        "spread": 0.0001,
        # Outcome not linked yet
        "outcome_linked": False,
        "outcome_raw_r": None,
    }


def _make_shadow_trade(
    symbol: str = "EURUSD",
    entity_id: str = "EURUSD_1785255900",
    correlation_id: str = "",
    entry_time: float = 1785255900.0,
    result_r: float = 1.5,
    mfe_r: float = 2.0,
    mae_r: float = -0.4,
    exit_reason: str = "TP",
    bars_held: int = 12,
    direction: str = "BUY",
) -> dict:
    """Create a minimal shadow trade record."""
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
            "take_profit_intent": 1.087,
            "direction": direction,
            "pattern": "HAMMER",
            "score": 0.7,
        },
        "simulated_outcome": {
            "exit_price": 1.087,
            "exit_timestamp": entry_time + bars_held * 300,
            "pnl_r_multiple": result_r,
            "mfe_r": mfe_r,
            "mae_r": mae_r,
            "exit_reason": exit_reason,
            "bars_held": bars_held,
        },
    }


class _LinkageTestBase:
    """Base with temp dir setup for all linkage tests."""

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.v3_dir = Path(self.temp_dir) / "v3"
        self.shadow_dir = Path(self.temp_dir) / "shadow"

    def teardown_method(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_v3(self, obs: dict):
        sym = obs["symbol"]
        path = self.v3_dir / sym / "2025-07-28.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(obs) + "\n")

    def _write_shadow(self, trade: dict):
        sym = trade["identity"]["symbol"]
        path = self.shadow_dir / sym / "2025-07-28.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(trade) + "\n")


class TestEntityIdMatch(_LinkageTestBase):
    """Test 1: V3 correlation_id matches shadow entity_id."""

    def test_entity_id_match(self):
        """V3.correlation_id == shadow.identity.entity_id links correctly."""
        obs = _make_v3_observation(correlation_id="EURUSD_1785255900")
        trade = _make_shadow_trade(entity_id="EURUSD_1785255900", result_r=1.5)
        self._write_v3(obs)
        self._write_shadow(trade)

        report = link_v3_outcomes(
            v3_dir=str(self.v3_dir), shadow_dir=str(self.shadow_dir), persist=False)

        assert report.matched == 1
        assert report.match_by_entity_id == 1
        rec = report.linked_records[0]
        assert rec["outcome_linked"] is True
        assert rec["outcome_raw_r"] == 1.5
        assert rec["outcome_win"] is True
        assert rec["_linkage"]["match_method"] == "entity_id"


class TestCorrelationIdFallback(_LinkageTestBase):
    """Test 2: Falls back to correlation_id when entity_id doesn't match."""

    def test_correlation_id_fallback(self):
        """Matches via shadow.identity.correlation_id."""
        obs = _make_v3_observation(correlation_id="COR-200-EURUSD")
        trade = _make_shadow_trade(
            entity_id="DIFFERENT", correlation_id="COR-200-EURUSD", result_r=-0.8)
        self._write_v3(obs)
        self._write_shadow(trade)

        report = link_v3_outcomes(
            v3_dir=str(self.v3_dir), shadow_dir=str(self.shadow_dir), persist=False)

        assert report.matched == 1
        assert report.match_by_correlation_id == 1
        assert report.linked_records[0]["outcome_raw_r"] == -0.8


class TestTimestampFallback(_LinkageTestBase):
    """Test 2b: Falls back to symbol + timestamp when IDs don't match."""

    def test_timestamp_match_within_tolerance(self):
        """Matches by timestamp within 300s tolerance."""
        obs = _make_v3_observation(
            correlation_id="NO_MATCH", timestamp_utc=1785255900.0)
        trade = _make_shadow_trade(
            entity_id="ALSO_NO_MATCH", correlation_id="NOPE",
            entry_time=1785256000.0, result_r=0.5)  # 100s difference
        self._write_v3(obs)
        self._write_shadow(trade)

        report = link_v3_outcomes(
            v3_dir=str(self.v3_dir), shadow_dir=str(self.shadow_dir), persist=False)

        assert report.matched == 1
        assert report.match_by_timestamp == 1

    def test_no_match_beyond_tolerance(self):
        """No match when timestamp exceeds 300s tolerance."""
        obs = _make_v3_observation(
            correlation_id="NO_MATCH", timestamp_utc=1785255900.0)
        trade = _make_shadow_trade(
            entity_id="NOPE", correlation_id="NOPE",
            entry_time=1785256500.0, result_r=0.5)  # 600s — too far
        self._write_v3(obs)
        self._write_shadow(trade)

        report = link_v3_outcomes(
            v3_dir=str(self.v3_dir), shadow_dir=str(self.shadow_dir), persist=False)

        assert report.matched == 0
        assert report.unmatched == 1


class TestOutcomeFields(_LinkageTestBase):
    """Test 3: Shadow trade outcome fields attached correctly."""

    def test_all_outcome_fields(self):
        """MFE, MAE, exit_reason, bars_held all attached."""
        obs = _make_v3_observation(correlation_id="EURUSD_100")
        trade = _make_shadow_trade(
            entity_id="EURUSD_100", result_r=2.0,
            mfe_r=2.5, mae_r=-0.2, exit_reason="TP", bars_held=8)
        self._write_v3(obs)
        self._write_shadow(trade)

        report = link_v3_outcomes(
            v3_dir=str(self.v3_dir), shadow_dir=str(self.shadow_dir), persist=False)

        rec = report.linked_records[0]
        assert rec["outcome_mfe_r"] == 2.5
        assert rec["outcome_mae_r"] == -0.2
        assert rec["outcome_exit_reason"] == "TP"
        assert rec["outcome_bars_held"] == 8
        assert rec["_linkage"]["hold_minutes"] == 40  # 8 bars * 5 min


class TestMissingLinkage(_LinkageTestBase):
    """Test 4: Missing linkage detected and reported."""

    def test_no_shadow_trades(self):
        """Observation without matching trade stays unlinked."""
        obs = _make_v3_observation()
        self._write_v3(obs)
        # No shadow trade written

        report = link_v3_outcomes(
            v3_dir=str(self.v3_dir), shadow_dir=str(self.shadow_dir), persist=False)

        assert report.total_observations == 1
        assert report.matched == 0
        assert report.unmatched == 1
        assert report.linked_records[0]["_linkage"]["linked"] is False

    def test_empty_directories(self):
        """Handles empty directories gracefully."""
        report = link_v3_outcomes(
            v3_dir=str(self.v3_dir), shadow_dir=str(self.shadow_dir), persist=False)
        assert report.total_observations == 0


class TestNoTradeObservations(_LinkageTestBase):
    """Test 5: NO_TRADE observations remain traceable."""

    def test_no_trade_marked(self):
        """Unmatched observations get NO_TRADE reason."""
        obs = _make_v3_observation(correlation_id="UNIQUE_NO_TRADE")
        self._write_v3(obs)

        report = link_v3_outcomes(
            v3_dir=str(self.v3_dir), shadow_dir=str(self.shadow_dir), persist=False)

        assert report.no_trade_observations == 1
        rec = report.linked_records[0]
        assert rec["_linkage"]["reason"] == "NO_TRADE_MATCH"

    def test_mixed_trade_and_no_trade(self):
        """Some observations link, some don't."""
        obs1 = _make_v3_observation(correlation_id="EURUSD_100", timestamp_utc=100.0)
        obs2 = _make_v3_observation(correlation_id="NO_MATCH", timestamp_utc=99999.0)
        trade = _make_shadow_trade(entity_id="EURUSD_100", entry_time=100.0)
        self._write_v3(obs1)
        self._write_v3(obs2)
        self._write_shadow(trade)

        report = link_v3_outcomes(
            v3_dir=str(self.v3_dir), shadow_dir=str(self.shadow_dir), persist=False)

        assert report.matched == 1
        assert report.no_trade_observations == 1


class TestPersistence(_LinkageTestBase):
    """Test 6: Linked records persist to disk."""

    def test_persist_writes_file(self):
        """Linked records written back to V3 JSONL."""
        obs = _make_v3_observation(
            correlation_id="EURUSD_1785255900", timestamp_utc=1785255900.0)
        trade = _make_shadow_trade(entity_id="EURUSD_1785255900", result_r=1.0)
        self._write_v3(obs)
        self._write_shadow(trade)

        link_v3_outcomes(
            v3_dir=str(self.v3_dir), shadow_dir=str(self.shadow_dir), persist=True)

        # Find the persisted file (date derived from timestamp_utc)
        files = list(self.v3_dir.rglob("*.jsonl"))
        assert len(files) >= 1
        # Read the last written file (linkage output)
        found_linked = False
        for f in files:
            with open(f) as fh:
                for line in fh:
                    if line.strip():
                        record = json.loads(line)
                        if record.get("outcome_linked"):
                            found_linked = True
                            assert record["outcome_raw_r"] == 1.0
                            break
            if found_linked:
                break
        assert found_linked, "No linked record found in persisted files"


class TestShadowTradeIntegrity(_LinkageTestBase):
    """Test 7: Shadow trade files are never modified."""

    def test_shadow_files_unchanged(self):
        """Linkage does not write to shadow trade files."""
        obs = _make_v3_observation(correlation_id="EURUSD_100")
        trade = _make_shadow_trade(entity_id="EURUSD_100")
        self._write_v3(obs)
        self._write_shadow(trade)

        shadow_files = list(self.shadow_dir.rglob("*.jsonl"))
        before = {str(f): f.read_text() for f in shadow_files}

        link_v3_outcomes(
            v3_dir=str(self.v3_dir), shadow_dir=str(self.shadow_dir), persist=True)

        for f in shadow_files:
            assert f.read_text() == before[str(f)]


class TestV3FeaturesPreserved(_LinkageTestBase):
    """Original V3 features are not lost during linkage."""

    def test_features_preserved(self):
        """Location/liquidity fields survive linkage."""
        obs = _make_v3_observation(correlation_id="EURUSD_100")
        trade = _make_shadow_trade(entity_id="EURUSD_100")
        self._write_v3(obs)
        self._write_shadow(trade)

        report = link_v3_outcomes(
            v3_dir=str(self.v3_dir), shadow_dir=str(self.shadow_dir), persist=False)

        rec = report.linked_records[0]
        assert rec["h1_range_position"] == 0.5
        assert rec["equal_highs_above"] is True
        assert rec["equal_highs_count"] == 2
        assert rec["nearest_fvg_above_price"] == 1.087
        assert rec["nearest_demand_ob_price"] == 1.083
        assert rec["atr"] == 0.0012


class TestReportSummary:
    """Report provides useful summary."""

    def test_match_rate_calculation(self):
        """Match rate computed correctly."""
        report = V3LinkageReport(total_observations=10, matched=7, unmatched=3)
        assert report.match_rate == 0.7

    def test_summary_keys(self):
        """Summary dict has all expected keys."""
        report = V3LinkageReport(
            total_observations=100, matched=80, unmatched=20,
            match_by_entity_id=60, match_by_correlation_id=10,
            match_by_timestamp=10, no_trade_observations=20)
        s = report.summary()
        assert s["total_observations"] == 100
        assert s["match_rate"] == 0.8
        assert s["no_trade_observations"] == 20
