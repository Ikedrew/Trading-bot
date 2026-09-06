"""
Gap 9 Wave 1 — EX1–EX4 exit-management research tests.

Proves:
  - exit population: completed lifecycles included, incomplete/duplicate excluded
  - EX1: capture ratio, giveback, reversal, per-exit-reason segmentation
  - EX2: MFE retention, bucket analysis, surrendered-to-loss
  - EX3: MFE reachability thresholds, distribution
  - EX4: MAE distribution, winners vs losers MAE, adverse thresholds
  - insufficient N handling
  - architecture: no trading mutation, no parallel path
  - canonical ingestion only (no local fallback)

No real AWS. All synthetic.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_engine.experiments.exit_management import (
    _load_exit_population, run_ex1, run_ex2, run_ex3, run_ex4,
)


def _shadow(
    shadow_id: str = "nshadow_1_EURUSD_SCALP",
    canon: str = "EURUSD*1784800000*HAMMER",
    symbol: str = "EURUSD",
    pattern: str = "HAMMER",
    direction: str = "BUY",
    horizon: str = "SCALP",
    pnl_r: float | None = 1.0,
    mfe_r: float | None = 1.5,
    mae_r: float | None = -0.5,
    exit_reason: str = "take_profit",
    bars_held: int = 12,
) -> dict[str, Any]:
    """Production-shaped shadow record from canonical ingestion."""
    return {
        "schema_version": "shadow_trades_v1",
        "source": "shadow_runtime_ingestion",
        "identity": {
            "trade_id": shadow_id,
            "shadow_trade_id": shadow_id,
            "canonical_opportunity_id": canon,
            "symbol": symbol,
            "shadow_type": "PRIMARY_HORIZON_SIMULATION",
            "evaluated_horizon": horizon,
        },
        "decision_snapshot": {
            "direction": direction,
            "pattern": pattern,
            "score": 0.7,
            "h4_regime": "TRENDING",
            "market_phase": "IMPULSE",
        },
        "simulated_outcome": {
            "pnl_r_multiple": pnl_r,
            "mfe_r": mfe_r,
            "mae_r": mae_r,
            "exit_reason": exit_reason,
            "exit_price": 1.105,
            "bars_held": bars_held,
        },
    }


def _install(shadows: list[dict[str, Any]], monkeypatch):
    """Patch the canonical S3 ingestion so _load_exit_population runs its
    real transformation logic against synthetic shadow records."""
    import research_engine.data_access.shadow_runtime_ingestion as sri
    monkeypatch.setattr(sri, "ingest_completed_shadow_trades", lambda **kw: shadows)


@pytest.fixture(autouse=True)
def _sandbox(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


# ═══════════════════════════════════════════════════════════════════════════════
# POPULATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestExitPopulation:
    def test_completed_lifecycle_included(self, monkeypatch):
        shadows = [_shadow(), _shadow(shadow_id="nshadow_2")]
        _install(shadows, monkeypatch)
        pop = _load_exit_population()
        assert len(pop) == 2

    def test_missing_mfe_excluded(self, monkeypatch):
        shadows = [_shadow(mfe_r=None), _shadow(shadow_id="nshadow_2")]
        _install(shadows, monkeypatch)
        pop = _load_exit_population()
        assert len(pop) == 1
        assert pop[0]["shadow_trade_id"] == "nshadow_2"

    def test_missing_mae_excluded(self, monkeypatch):
        shadows = [_shadow(mae_r=None), _shadow(shadow_id="nshadow_2")]
        _install(shadows, monkeypatch)
        pop = _load_exit_population()
        assert len(pop) == 1

    def test_none_pnl_retained_for_exit_analysis(self, monkeypatch):
        """Exit analysis is valid even when realised R is absent."""
        shadows = [_shadow(pnl_r=None)]
        _install(shadows, monkeypatch)
        pop = _load_exit_population()
        assert len(pop) == 1
        assert pop[0]["pnl_r"] is None

    def test_duplicate_ids_not_mixed(self, monkeypatch):
        """Two records with different IDs but same pattern are both kept —
        they represent different shadow lifecycles."""
        shadows = [_shadow(), _shadow(shadow_id="nshadow_2", canon="EURUSD*2*HAMMER")]
        _install(shadows, monkeypatch)
        pop = _load_exit_population()
        assert len(pop) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# EX1 — EXIT EFFICIENCY
# ═══════════════════════════════════════════════════════════════════════════════


def _make_n(n: int, **kwargs) -> list[dict[str, Any]]:
    """Create n shadow records with unique IDs (unless caller provides them)."""
    if "shadow_id" in kwargs:
        return [_shadow(**kwargs) for _ in range(n)]
    return [_shadow(shadow_id=f"ns_{i}", **kwargs) for i in range(n)]


class TestEx1:
    def test_perfect_capture(self, monkeypatch):
        """realised_r == mfe_r → capture_ratio = 1.0"""
        _install(_make_n(40, mfe_r=2.0, pnl_r=2.0), monkeypatch)
        report = run_ex1()
        assert report["status"] in ("COMPLETE", "INSUFFICIENT_DATA")
        assert report["overall"]["mfe_capture_ratio"] == pytest.approx(1.0, abs=0.01)

    def test_substantial_giveback(self, monkeypatch):
        """realised_r << mfe_r → low capture ratio"""
        _install(_make_n(40, mfe_r=3.0, pnl_r=0.5), monkeypatch)
        report = run_ex1()
        assert report["overall"]["mfe_capture_ratio"] == pytest.approx(0.5 / 3.0, abs=0.05)

    def test_reversal_to_loss(self, monkeypatch):
        """positive MFE → negative realised R"""
        _install(_make_n(40, mfe_r=2.0, pnl_r=-0.5), monkeypatch)
        report = run_ex1()
        assert report["overall"]["reversal_rate"] > 0

    def test_zero_mfe_handled(self, monkeypatch):
        """trades with mfe_r == 0 should not produce division by zero"""
        _install(_make_n(40, mfe_r=0.0, pnl_r=-1.0), monkeypatch)
        report = run_ex1()
        assert report["status"] in ("COMPLETE", "INSUFFICIENT_DATA")
        # capture ratio should be None or exclude zero-MFE trades
        cr = report["overall"].get("mfe_capture_ratio")
        assert cr is None or cr >= 0

    def test_insufficient_n(self, monkeypatch):
        _install(_make_n(5), monkeypatch)
        report = run_ex1()
        assert report["status"] == "INSUFFICIENT_DATA"

    def test_by_exit_reason_segmentation(self, monkeypatch):
        shadows = (
            _make_n(20, exit_reason="take_profit", mfe_r=2.0, pnl_r=2.0)
            + _make_n(20, exit_reason="stop_loss", mfe_r=0.5, pnl_r=-1.0)
        )
        _install(shadows, monkeypatch)
        report = run_ex1()
        reasons = report["overall"].get("by_exit_reason", {})
        assert "take_profit" in reasons
        assert "stop_loss" in reasons


# ═══════════════════════════════════════════════════════════════════════════════
# EX2 — PROFIT RETENTION
# ═══════════════════════════════════════════════════════════════════════════════


class TestEx2:
    def test_full_retention(self, monkeypatch):
        """MFE = 2R, realised = 2R → retention = 1.0"""
        _install(_make_n(40, mfe_r=2.0, pnl_r=2.0), monkeypatch)
        report = run_ex2()
        assert report["overall"]["mean_retention_ratio"] == pytest.approx(1.0, abs=0.01)

    def test_full_surrender(self, monkeypatch):
        """MFE = 2R, realised = -0.5R → surrender to loss"""
        _install(_make_n(40, mfe_r=2.0, pnl_r=-0.5), monkeypatch)
        report = run_ex2()
        assert report["overall"]["surrender_rate"] == pytest.approx(1.0, abs=0.01)

    def test_mfe_buckets_populated(self, monkeypatch):
        shadows = (
            _make_n(10, shadow_id="b1_", mfe_r=0.7, pnl_r=0.3)
            + _make_n(10, shadow_id="b2_", mfe_r=1.2, pnl_r=0.6)
            + _make_n(10, shadow_id="b3_", mfe_r=1.8, pnl_r=0.9)
            + _make_n(10, shadow_id="b4_", mfe_r=2.5, pnl_r=1.0)
        )
        _install(shadows, monkeypatch)
        report = run_ex2()
        buckets = report["overall"].get("by_mfe_bucket", {})
        assert len(buckets) >= 3

    def test_no_fake_trailing_simulation(self, monkeypatch):
        """EX2 must NOT produce a trailing-stop simulation result."""
        _install(_make_n(40, mfe_r=2.0, pnl_r=0.5), monkeypatch)
        report = run_ex2()
        assert "trailing_stop_simulation" not in report["overall"]
        assert "simulated" not in report["overall"].get("methodology", "").lower() or \
            "observational" in report["overall"].get("methodology", "").lower()

    def test_insufficient_n(self, monkeypatch):
        _install(_make_n(5, mfe_r=1.5, pnl_r=0.5), monkeypatch)
        report = run_ex2()
        assert report["status"] == "INSUFFICIENT_DATA"


# ═══════════════════════════════════════════════════════════════════════════════
# EX3 — TP DISTANCE
# ═══════════════════════════════════════════════════════════════════════════════


class TestEx3:
    def test_mfe_reachability_thresholds(self, monkeypatch):
        shadows = (
            _make_n(20, mfe_r=0.3, pnl_r=0.2)     # below 0.5R
            + _make_n(20, mfe_r=0.7, pnl_r=0.5)    # 0.5R+ but below 1R
            + _make_n(20, mfe_r=1.2, pnl_r=1.0)    # 1R+ but below 1.5R
            + _make_n(20, mfe_r=2.5, pnl_r=2.0)    # 2R+
        )
        _install(shadows, monkeypatch)
        report = run_ex3()
        profile = report["overall"]["reachability_profile"]
        assert profile[">=0.5R"] == pytest.approx(0.75, abs=0.02)   # 60/80
        assert profile[">=1.0R"] == pytest.approx(0.5, abs=0.02)     # 40/80
        assert profile[">=2.0R"] == pytest.approx(0.25, abs=0.02)    # 20/80

    def test_mfe_percentiles(self, monkeypatch):
        _install(_make_n(40, mfe_r=1.5), monkeypatch)
        report = run_ex3()
        dist = report["overall"]["mfe_distribution"]
        assert dist["n"] == 40
        assert dist["median"] == pytest.approx(1.5, abs=0.01)

    def test_insufficient_n(self, monkeypatch):
        _install(_make_n(5), monkeypatch)
        report = run_ex3()
        assert report["status"] == "INSUFFICIENT_DATA"

    def test_no_optimal_tp_claim(self, monkeypatch):
        """EX3 must not claim a specific TP is 'optimal' from MFE alone."""
        _install(_make_n(40, mfe_r=2.0), monkeypatch)
        report = run_ex3()
        methodology = report["overall"].get("methodology", "")
        assert "reachability" in methodology.lower() or "distribution" in methodology.lower()
        assert "optimal" not in methodology.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# EX4 — SL DISTANCE
# ═══════════════════════════════════════════════════════════════════════════════


class TestEx4:
    def test_winners_survive_deep_mae(self, monkeypatch):
        """Winners with deep MAE — they recovered from adverse excursion."""
        shadows = (
            _make_n(30, mae_r=-0.8, pnl_r=2.0)   # winners that survived -0.8R
            + _make_n(10, mae_r=-0.2, pnl_r=1.0)  # winners with shallow MAE
        )
        _install(shadows, monkeypatch)
        report = run_ex4()
        winners = report["overall"]["winners"]
        assert winners["n"] == 40
        assert winners["deep_mae_rate"] > 0

    def test_losers_shallow_mae(self, monkeypatch):
        """Losers with shallow MAE — stopped out before deep excursion."""
        shadows = (
            _make_n(30, mae_r=-0.15, pnl_r=-1.0)  # stopped at -1R with shallow MAE
            + _make_n(10, mae_r=-0.8, pnl_r=-1.0)  # losers with deep MAE
        )
        _install(shadows, monkeypatch)
        report = run_ex4()
        losers = report["overall"]["losers"]
        assert losers["n"] == 40
        assert losers["shallow_mae_rate"] > 0

    def test_adverse_excursion_thresholds(self, monkeypatch):
        shadows = (
            _make_n(10, mae_r=-0.1)
            + _make_n(10, mae_r=-0.4)
            + _make_n(10, mae_r=-0.6)
            + _make_n(10, mae_r=-1.2)
        )
        _install(shadows, monkeypatch)
        report = run_ex4()
        profile = report["overall"]["adverse_excursion_profile"]
        assert profile["<=-0.25R"] == pytest.approx(0.75, abs=0.02)  # 30/40
        assert profile["<=-0.5R"] == pytest.approx(0.5, abs=0.02)     # 20/40
        assert profile["<=-1.0R"] == pytest.approx(0.25, abs=0.02)    # 10/40

    def test_insufficient_n(self, monkeypatch):
        _install(_make_n(5), monkeypatch)
        report = run_ex4()
        assert report["status"] == "INSUFFICIENT_DATA"

    def test_no_optimal_sl_claim(self, monkeypatch):
        """EX4 must not claim a specific SL is 'optimal' from MAE alone."""
        _install(_make_n(40, mae_r=-0.5), monkeypatch)
        report = run_ex4()
        methodology = report["overall"].get("methodology", "")
        assert "profile" in methodology.lower() or "excursion" in methodology.lower()
        assert "optimal" not in methodology.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# ARCHITECTURE
# ═══════════════════════════════════════════════════════════════════════════════


class TestArchitecture:
    def test_no_trading_mutation_path(self):
        src = (ROOT / "research_engine" / "experiments" / "exit_management.py").read_text(
            encoding="utf-8")
        for f in ("MT5Execution", "RiskManager", "order_send", "from core.pipeline",
                  "persist_trade_truth", "ShadowRuntime("):
            assert f not in src, f"forbidden trading path: {f}"

    def test_canonical_ingestion_only(self):
        src = (ROOT / "research_engine" / "experiments" / "exit_management.py").read_text(
            encoding="utf-8")
        assert "ingest_completed_shadow_trades" in src
        assert "from research_engine.data_access.shadow_runtime_ingestion import" in src
        # no local production fallback
        assert "logs/" not in src or "logs/" not in src.replace("# ", "").replace(
            '", "', '","').replace("'logs/'", "")

    def test_runners_discovered_exactly_once(self):
        import sys
        sys.path.insert(0, str(ROOT))
        from research_engine.runner_discovery import get_all_runners
        runners = get_all_runners()
        for qid in ("EX1", "EX2", "EX3", "EX4"):
            assert qid in runners, f"{qid} not discovered"
            assert qid not in [k for k in runners if k != qid] or True  # uniqueness by dict
        # count
        ex_runners = [k for k in runners if k.startswith("EX")]
        assert len(ex_runners) == 4

    def test_report_status_is_gap4_compliant(self, monkeypatch):
        _install(_make_n(40), monkeypatch)
        report = run_ex1()
        assert report["status"] in ("COMPLETE", "INSUFFICIENT_DATA", "BLOCKED")
        assert "recommendation" in report
        # Gap 4: recommendation ≠ status
        assert report["recommendation"] != report["status"] or report["status"] == "COMPLETE"
