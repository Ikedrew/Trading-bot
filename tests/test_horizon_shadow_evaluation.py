"""
Horizon Shadow Evaluation Framework — Tests.

Validates:
    1. Shadow results are created from records
    2. Inactive horizons generate shadow results
    3. SCALP execution remains unchanged
    4. INTRADAY remains disabled
    5. EXTENDED remains disabled
    6. No broker calls occur (research-only)
    7. Shadow results generate observations
    8. Reports include contract versions
    9. Empty shadow datasets return insufficient data
    10. Activation readiness assessment
"""

from __future__ import annotations

import sys
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from dataclasses import dataclass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest

from core.horizon.shadow_evaluation import (
    HorizonShadowResult,
    ActivationReadiness,
    ActivationReport,
    build_shadow_observation,
    assess_activation_readiness,
    load_horizon_shadow_results,
    run_shadow_evaluation,
    _extract_horizon,
    _build_shadow_result,
)
from core.horizon.research_contract import HorizonObservation


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _make_shadow_record(
    horizon: str = "INTRADAY",
    cycle: int = 100,
    symbol: str = "EURUSD",
    direction: str = "BUY",
    pnl_r: float = 2.0,
    exit_reason: str = "tp_hit",
    bars_held: int = 36,
) -> dict:
    return {
        "trade_id": f"hshadow_{cycle}_{symbol}_{horizon}",
        "correlation_id": f"HORIZON-{cycle}-{symbol}",
        "symbol": symbol,
        "direction": direction,
        "entry_price": 1.1000,
        "stop_loss": 1.0980,
        "take_profit": 1.1060,
        "entry_time": 1719000000.0,
        "exit_time": 1719000000.0 + bars_held * 300,
        "exit_price": 1.1040,
        "pnl_r_multiple": pnl_r,
        "mfe_r": abs(pnl_r) * 1.2,
        "mae_r": 0.5,
        "exit_reason": exit_reason,
        "bars_held": bars_held,
        "strategy": f"BREAKOUT_{horizon}",
    }


def _make_shadow_result(
    horizon: str = "INTRADAY",
    r: float = 2.0,
    win: bool = True,
    hold_min: float = 120.0,
) -> HorizonShadowResult:
    return HorizonShadowResult(
        shadow_id=f"hshadow_1_EURUSD_{horizon}",
        source_opportunity_id="HORIZON-1-EURUSD",
        symbol="EURUSD",
        direction="BUY",
        horizon=horizon,
        entry_price=1.1,
        hypothetical_stop_loss=1.098,
        hypothetical_take_profit=1.106,
        entry_time=1000.0,
        exit_time=1000.0 + hold_min * 60,
        exit_price=1.104 if win else 1.098,
        realised_r=r,
        max_favourable_excursion=abs(r) * 1.1,
        max_adverse_excursion=0.4,
        close_reason="tp_hit" if win else "sl_hit",
        profile_version=f"{horizon}_RESEARCH_V1",
        bars_held=int(hold_min / 5),
    )


def _write_shadow_files(tmpdir: Path, records: list[dict], symbol: str = "EURUSD") -> None:
    shadow_dir = tmpdir / "logs" / "shadow_trades" / symbol
    shadow_dir.mkdir(parents=True, exist_ok=True)
    filepath = shadow_dir / "2026-07-23.jsonl"
    with open(filepath, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Shadow Results Created From Records
# ═══════════════════════════════════════════════════════════════════════════════

class TestShadowResultCreation:
    def test_extract_horizon_from_trade_id(self):
        assert _extract_horizon({"trade_id": "hshadow_100_EURUSD_INTRADAY"}) == "INTRADAY"
        assert _extract_horizon({"trade_id": "hshadow_100_EURUSD_SCALP"}) == "SCALP"
        assert _extract_horizon({"trade_id": "hshadow_100_EURUSD_EXTENDED"}) == "EXTENDED"

    def test_extract_horizon_from_strategy_fallback(self):
        assert _extract_horizon({"trade_id": "hshadow_x", "strategy": "BREAKOUT_INTRADAY"}) == "INTRADAY"

    def test_build_shadow_result(self):
        record = _make_shadow_record(horizon="INTRADAY", pnl_r=2.5)
        result = _build_shadow_result(record, "INTRADAY")
        assert result is not None
        assert result.horizon == "INTRADAY"
        assert result.realised_r == 2.5
        assert result.close_reason == "tp_hit"

    def test_result_to_dict(self):
        r = _make_shadow_result()
        d = r.to_dict()
        assert d["horizon"] == "INTRADAY"
        assert d["realised_r"] == 2.0
        assert "shadow_id" in d


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Inactive Horizons Generate Shadow Results
# ═══════════════════════════════════════════════════════════════════════════════

class TestInactiveHorizons:
    def test_load_intraday_shadows(self, tmp_path):
        records = [_make_shadow_record(horizon="INTRADAY") for _ in range(5)]
        _write_shadow_files(tmp_path, records)

        with patch("core.horizon.shadow_evaluation._get_project_root", return_value=tmp_path):
            results = load_horizon_shadow_results(horizon="INTRADAY")
            assert len(results) == 5
            assert all(r.horizon == "INTRADAY" for r in results)

    def test_load_extended_shadows(self, tmp_path):
        records = [_make_shadow_record(horizon="EXTENDED") for _ in range(3)]
        _write_shadow_files(tmp_path, records)

        with patch("core.horizon.shadow_evaluation._get_project_root", return_value=tmp_path):
            results = load_horizon_shadow_results(horizon="EXTENDED")
            assert len(results) == 3


# ═══════════════════════════════════════════════════════════════════════════════
# 3, 4, 5. Execution Unchanged
# ═══════════════════════════════════════════════════════════════════════════════

class TestExecutionUnchanged:
    def test_permitted_horizons_scalp_only(self):
        from core import config
        assert config.PERMITTED_HORIZONS == ["SCALP"]

    def test_authority_blocks_intraday(self):
        from core.horizon.execution_authority import HorizonExecutionAuthority
        auth = HorizonExecutionAuthority()
        assert auth.can_open(symbol="EURUSD", horizon="INTRADAY", current_positions=[]).allowed is False

    def test_authority_blocks_extended(self):
        from core.horizon.execution_authority import HorizonExecutionAuthority
        auth = HorizonExecutionAuthority()
        assert auth.can_open(symbol="EURUSD", horizon="EXTENDED", current_positions=[]).allowed is False


# ═══════════════════════════════════════════════════════════════════════════════
# 6. No Broker Calls (Research-Only)
# ═══════════════════════════════════════════════════════════════════════════════

class TestNoBrokerCalls:
    def test_shadow_evaluation_no_mt5_import(self):
        """Module must not import MetaTrader5."""
        import core.horizon.shadow_evaluation as mod
        import inspect
        source = inspect.getsource(mod)
        assert "import MetaTrader5" not in source
        assert "mt5" not in source.lower().split("def ")[0]  # not in top-level imports


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Shadow Results Generate Observations
# ═══════════════════════════════════════════════════════════════════════════════

class TestObservationBuilding:
    def test_builds_observation_from_results(self):
        results = [
            _make_shadow_result(r=2.0, win=True, hold_min=120.0),
            _make_shadow_result(r=-1.0, win=False, hold_min=60.0),
            _make_shadow_result(r=3.0, win=True, hold_min=180.0),
        ]
        obs = build_shadow_observation(results, "INTRADAY")
        assert obs.sample_size == 3
        assert obs.horizon == "INTRADAY"
        assert obs.observed_win_rate == pytest.approx(2/3, abs=0.01)

    def test_observation_rr(self):
        results = [
            _make_shadow_result(r=2.0, win=True),
            _make_shadow_result(r=2.0, win=True),
            _make_shadow_result(r=-1.0, win=False),
        ]
        obs = build_shadow_observation(results, "INTRADAY")
        # Average R = (2 + 2 + (-1)) / 3 = 1.0
        assert obs.observed_rr == pytest.approx(1.0, abs=0.01)

    def test_observation_profit_factor(self):
        results = [
            _make_shadow_result(r=2.0, win=True),
            _make_shadow_result(r=3.0, win=True),
            _make_shadow_result(r=-1.0, win=False),
        ]
        obs = build_shadow_observation(results, "INTRADAY")
        # PF = gross_win(5.0) / gross_loss(1.0) = 5.0
        assert obs.observed_profit_factor == pytest.approx(5.0, abs=0.01)

    def test_empty_results_zero_sample(self):
        obs = build_shadow_observation([], "EXTENDED")
        assert obs.sample_size == 0
        assert obs.horizon == "EXTENDED"


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Reports Include Contract Versions
# ═══════════════════════════════════════════════════════════════════════════════

class TestVersions:
    def test_shadow_result_has_version(self):
        r = _make_shadow_result(horizon="INTRADAY")
        assert r.profile_version == "INTRADAY_RESEARCH_V1"

    def test_observation_has_version(self):
        results = [_make_shadow_result(horizon="EXTENDED")]
        obs = build_shadow_observation(results, "EXTENDED")
        assert obs.profile_version == "EXTENDED_RESEARCH_V1"

    def test_activation_report_has_version(self):
        obs = HorizonObservation(
            horizon="INTRADAY",
            profile_version="INTRADAY_RESEARCH_V1",
            sample_size=100,
            observed_rr=2.5,
            observed_win_rate=0.45,
            observed_expectancy=0.6,
            observed_profit_factor=2.0,
        )
        report = assess_activation_readiness("INTRADAY", obs)
        assert report.profile_version == "INTRADAY_RESEARCH_V1"


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Empty Datasets Return Insufficient Data
# ═══════════════════════════════════════════════════════════════════════════════

class TestEmptyData:
    def test_no_shadow_dir(self, tmp_path):
        with patch("core.horizon.shadow_evaluation._get_project_root", return_value=tmp_path):
            results = load_horizon_shadow_results()
            assert results == []

    def test_activation_insufficient_data(self):
        obs = HorizonObservation(
            horizon="INTRADAY",
            profile_version="INTRADAY_RESEARCH_V1",
            sample_size=5,
        )
        report = assess_activation_readiness("INTRADAY", obs)
        assert report.readiness == ActivationReadiness.INSUFFICIENT_DATA

    def test_full_pipeline_empty(self, tmp_path):
        with patch("core.horizon.shadow_evaluation._get_project_root", return_value=tmp_path):
            result = run_shadow_evaluation(persist=False)
            assert result["total_shadow_results"] == 0
            for h in ("SCALP", "INTRADAY", "EXTENDED"):
                assert h in result["horizons"]


# ═══════════════════════════════════════════════════════════════════════════════
# 10. Activation Readiness Assessment
# ═══════════════════════════════════════════════════════════════════════════════

class TestActivationReadiness:
    def test_ready_for_review(self):
        obs = HorizonObservation(
            horizon="INTRADAY",
            profile_version="INTRADAY_RESEARCH_V1",
            sample_size=100,
            observed_rr=2.5,
            observed_win_rate=0.45,
            observed_expectancy=0.6,
            observed_profit_factor=2.0,
        )
        report = assess_activation_readiness("INTRADAY", obs)
        assert report.readiness == ActivationReadiness.READY_FOR_REVIEW
        assert "activation review" in report.recommendation.lower()

    def test_not_recommended_negative_expectancy(self):
        obs = HorizonObservation(
            horizon="EXTENDED",
            profile_version="EXTENDED_RESEARCH_V1",
            sample_size=100,
            observed_rr=-0.3,
            observed_win_rate=0.25,
            observed_expectancy=-0.4,
            observed_profit_factor=0.5,
        )
        report = assess_activation_readiness("EXTENDED", obs)
        assert report.readiness == ActivationReadiness.NOT_RECOMMENDED

    def test_continue_shadow_low_win_rate(self):
        obs = HorizonObservation(
            horizon="INTRADAY",
            profile_version="INTRADAY_RESEARCH_V1",
            sample_size=100,
            observed_rr=0.5,
            observed_win_rate=0.20,
            observed_expectancy=0.01,
            observed_profit_factor=1.05,
        )
        report = assess_activation_readiness("INTRADAY", obs)
        assert report.readiness == ActivationReadiness.CONTINUE_SHADOW

    def test_report_serializes(self):
        obs = HorizonObservation(
            horizon="INTRADAY",
            profile_version="INTRADAY_RESEARCH_V1",
            sample_size=100,
            observed_rr=2.0,
            observed_win_rate=0.40,
            observed_expectancy=0.4,
            observed_profit_factor=1.8,
        )
        report = assess_activation_readiness("INTRADAY", obs)
        d = report.to_dict()
        assert d["readiness"] == "READY_FOR_REVIEW"
        assert d["sample_size"] == 100
        serialized = json.dumps(d)
        assert isinstance(serialized, str)
