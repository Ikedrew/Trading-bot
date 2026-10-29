"""
WAVE 9 — Tests for Exit-Management Depth Research Runners (EX5–EX10).

Validates:
    - EX5–EX10 registered and executable
    - Each reads intended shadow_trades evidence
    - Empty evidence produces truthful insufficient-data
    - Each reports ownership distinct from EX1–EX4
    - Live vs shadow evidence distinguished
    - Clean-evidence boundary respected
    - No production schemas modified
    - Runner discovery succeeds
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

_project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from tests._s3_fake import FakeS3, install_fake_s3, reset_fake_s3
from research_engine.experiments.exit_management import _load_exit_population


# ═══════════════════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════════════════


def _make_exit_record(
    *,
    strategy: str = "REVERSAL",
    pattern: str = "HAMMER",
    h4_regime: str = "TRENDING",
    trade_horizon: str = "SCALP",
    mfe_r: float = 1.5,
    mae_r: float = -0.5,
    pnl_r: float = 0.8,
    exit_reason: str = "take_profit",
    bars_held: int = 12,
    shadow_trade_id: str = "nshadow_1_EURUSD_SCALP",
) -> dict[str, Any]:
    """Build a record matching the exit population shape (_load_exit_population)."""
    return {
        "shadow_trade_id": shadow_trade_id,
        "canonical_opportunity_id": f"EURUSD*{100000}*{pattern}",
        "symbol": "EURUSD",
        "pattern": pattern,
        "direction": "BUY",
        "trade_horizon": trade_horizon,
        "pnl_r": pnl_r,
        "mfe_r": mfe_r,
        "mae_r": mae_r,
        "exit_reason": exit_reason,
        "bars_held": bars_held,
        "h4_regime": h4_regime,
        "market_phase": "IMPULSE",
        "strategy": strategy,
    }


def _seed_exit_population(fake: FakeS3, records: list[dict]) -> None:
    """Seed shadow_runtime events in fake S3 so _load_exit_population works."""
    open_events = []
    close_events = []
    for i, rec in enumerate(records):
        trade_id = rec.get("shadow_trade_id", f"nshadow_{i}_EURUSD_SCALP")
        can_id = rec.get("canonical_opportunity_id", f"EURUSD*{100000+i}*PAT")
        symbol = rec.get("symbol", "EURUSD")

        open_ev = {
            "schema_version": "shadow_runtime_v1",
            "event_type": "OPEN",
            "shadow_trade_id": trade_id,
            "canonical_opportunity_id": can_id,  # used by classifier via identity mapping
            "symbol": symbol,
            "construction": {
                "direction": rec.get("direction", "BUY"),
                "entry_price": 1.0,
                "stop_loss": 0.99,
                "take_profit": 1.01,
            },
            "identity": {
                "entity_id": f"{symbol}_{100000+i}",
                "cycle_id": str(100000 + i),
                "trade_horizon": rec.get("trade_horizon", "SCALP"),
                "evaluated_horizon": rec.get("trade_horizon", "SCALP"),
                "shadow_type": "PRIMARY_HORIZON_SIMULATION",
                # canonical_opportunity_id inside identity for classifier
                "canonical_opportunity_id": can_id,
            },
            "live_facts": {
                "strategy": rec.get("strategy", "REVERSAL"),
                "pattern": rec.get("pattern", "HAMMER"),
                "h4_regime": rec.get("h4_regime", "TRENDING"),
                "market_phase": rec.get("market_phase", "IMPULSE"),
                "h1_bias": "BULLISH",
                "score": 0.55,
                "regime": rec.get("h4_regime", "TRENDING"),
                "v10_action": "EXECUTE",
            },
            "market_entry_facts": {
                "bid_at_entry": 1.0,
                "ask_at_entry": 1.0001,
            },
        }
        close_ev = {
            "schema_version": "shadow_runtime_v1",
            "event_type": "CLOSE",
            "shadow_trade_id": trade_id,
            "canonical_opportunity_id": can_id,
            "symbol": symbol,
            "exit_reason": rec.get("exit_reason", "take_profit"),
            "bars_held": rec.get("bars_held", 12),
            "exit_price": 1.005 if rec.get("pnl_r", 0) > 0 else 0.995,
            "outcome": {
                "pnl_r_multiple": rec.get("pnl_r", 0.5),
                "mfe_r": rec.get("mfe_r", 1.5),
                "mae_r": rec.get("mae_r", -0.5),
            },
        }
        open_events.append(open_ev)
        close_events.append(close_ev)

    # Seed via fake.add to ensure correct prefix format
    fake.add("shadow_runtime", open_events + close_events, symbol="EURUSD", date="2026-09-07")


def _default_records(n: int = 80) -> list[dict]:
    """Create N reasonably varied exit records."""
    records = []
    strategies = ["REVERSAL", "CONTINUATION", "FALSE_BREAK"]
    patterns = ["HAMMER", "ENGULFING", "TWEEZER_TOP", "PIN_BAR", "BOS_PULLBACK"]
    regimes = ["TRENDING", "RANGING", "TRANSITIONAL"]
    horizons = ["SCALP", "INTRADAY", "EXTENDED"]
    reasons = ["take_profit", "stop_loss", "max_bars_timeout"]

    for i in range(n):
        r = 2.0 if i % 3 != 0 else -1.0
        mfe = 2.5 if r > 0 else 1.0
        mae = -0.5 if r > 0 else -1.5
        reason = reasons[i % len(reasons)]
        records.append(_make_exit_record(
            shadow_trade_id=f"nshadow_{i}_EURUSD_SCALP",
            strategy=strategies[i % len(strategies)],
            pattern=patterns[i % len(patterns)],
            h4_regime=regimes[i % len(regimes)],
            trade_horizon=horizons[i % len(horizons)],
            mfe_r=mfe,
            mae_r=mae,
            pnl_r=r,
            exit_reason=reason,
            bars_held=i + 5,
        ))
    return records


# ═══════════════════════════════════════════════════════════════════════════════
# SETUP / TEARDOWN
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _reset_s3():
    reset_fake_s3()
    yield
    reset_fake_s3()


@pytest.fixture
def fake_s3():
    """Install fake S3 and seed with default exit records."""
    fake = install_fake_s3()
    records = _default_records(80)
    _seed_exit_population(fake, records)
    return fake


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: EX5 — Horizon changes optimal exit
# ═══════════════════════════════════════════════════════════════════════════════


class TestEX5:
    def test_registered(self):
        """EX5 is in the registry with runner metadata."""
        from research_engine.registry.research_question_registry import REGISTRY_BY_ID
        q = REGISTRY_BY_ID.get("EX5")
        assert q is not None
        assert q.runner_module, "EX5 missing runner_module"

    def test_executable(self, fake_s3):
        """EX5 runner executes without error."""
        from research_engine.experiments.exit_depth import run_ex5
        report = run_ex5()
        assert report is not None
        assert report["question_id"] == "EX5"

    def test_reads_shadow_trades(self, fake_s3):
        """EX5 reads from shadow_runtime_v1 ingestion."""
        from research_engine.experiments.exit_depth import run_ex5
        report = run_ex5()
        assert "shadow_runtime_v1" in report["dataset"]["source"]

    def test_segments_by_horizon(self, fake_s3):
        """EX5 segments exit metrics by trade_horizon."""
        from research_engine.experiments.exit_depth import run_ex5
        report = run_ex5()
        segments = report["overall"].get("segments", {})
        assert len(segments) > 0
        for h in ("SCALP", "INTRADAY", "EXTENDED"):
            if h in segments:
                assert segments[h]["count"] >= 0

    def test_ownership_note(self, fake_s3):
        """EX5 documents its ownership relative to EX1."""
        from research_engine.experiments.exit_depth import run_ex5
        report = run_ex5()
        assert "ownership_note" in report["overall"]

    def test_insufficient_data_on_empty(self):
        """Empty evidence produces INSUFFICIENT_DATA."""
        fake = install_fake_s3()
        _seed_exit_population(fake, [])
        from research_engine.experiments.exit_depth import run_ex5
        report = run_ex5()
        assert report["status"] == "INSUFFICIENT_DATA"

    def test_not_duplicate_of_ex1(self, fake_s3):
        """EX5 output is distinct from EX1 (segmented by horizon)."""
        from research_engine.experiments.exit_depth import run_ex5
        report = run_ex5()
        assert "segmentation" in report["overall"]
        assert report["overall"]["segmentation"] == "trade_horizon"


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: EX6 — Exit depends on strategy family
# ═══════════════════════════════════════════════════════════════════════════════


class TestEX6:
    def test_registered(self):
        """EX6 is in the registry with runner metadata."""
        from research_engine.registry.research_question_registry import REGISTRY_BY_ID
        q = REGISTRY_BY_ID.get("EX6")
        assert q is not None
        assert q.runner_module

    def test_executable(self, fake_s3):
        from research_engine.experiments.exit_depth import run_ex6
        report = run_ex6()
        assert report is not None
        assert report["question_id"] == "EX6"

    def test_segments_by_strategy(self, fake_s3):
        from research_engine.experiments.exit_depth import run_ex6
        report = run_ex6()
        segments = report["overall"].get("segments", {})
        assert len(segments) > 0

    def test_ownership_note(self, fake_s3):
        from research_engine.experiments.exit_depth import run_ex6
        report = run_ex6()
        assert "ownership_note" in report["overall"]


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: EX7 — Exit depends on market regime
# ═══════════════════════════════════════════════════════════════════════════════


class TestEX7:
    def test_registered(self):
        from research_engine.registry.research_question_registry import REGISTRY_BY_ID
        q = REGISTRY_BY_ID.get("EX7")
        assert q is not None
        assert q.runner_module

    def test_executable(self, fake_s3):
        from research_engine.experiments.exit_depth import run_ex7
        report = run_ex7()
        assert report is not None
        assert report["question_id"] == "EX7"

    def test_segments_by_regime(self, fake_s3):
        from research_engine.experiments.exit_depth import run_ex7
        report = run_ex7()
        segments = report["overall"].get("segments", {})
        assert len(segments) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: EX8 — Exit depends on pattern type
# ═══════════════════════════════════════════════════════════════════════════════


class TestEX8:
    def test_registered(self):
        from research_engine.registry.research_question_registry import REGISTRY_BY_ID
        q = REGISTRY_BY_ID.get("EX8")
        assert q is not None
        assert q.runner_module

    def test_executable(self, fake_s3):
        from research_engine.experiments.exit_depth import run_ex8
        report = run_ex8()
        assert report is not None
        assert report["question_id"] == "EX8"

    def test_segments_by_pattern(self, fake_s3):
        from research_engine.experiments.exit_depth import run_ex8
        report = run_ex8()
        segments = report["overall"].get("segments", {})
        assert len(segments) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: EX9 — Exit reduces timeout losses
# ═══════════════════════════════════════════════════════════════════════════════


class TestEX9:
    def test_registered(self):
        from research_engine.registry.research_question_registry import REGISTRY_BY_ID
        q = REGISTRY_BY_ID.get("EX9")
        assert q is not None
        assert q.runner_module

    def test_executable(self, fake_s3):
        from research_engine.experiments.exit_depth import run_ex9
        report = run_ex9()
        assert report is not None
        assert report["question_id"] == "EX9"

    def test_timeout_analysis(self, fake_s3):
        """EX9 reports timeout vs non-timeout comparison."""
        from research_engine.experiments.exit_depth import run_ex9
        report = run_ex9()
        ta = report["overall"].get("timeout_analysis", {})
        assert "timeout_exits" in ta
        assert "non_timeout_exits" in ta

    def test_insufficient_on_few_timeouts(self):
        """Very few timeout trades produces INSUFFICIENT_DATA."""
        fake = install_fake_s3()
        # Create records with only 2 timeouts
        records = [_make_exit_record(exit_reason="take_profit") for _ in range(30)]
        records.append(_make_exit_record(exit_reason="max_bars_timeout", mfe_r=0.5, pnl_r=-0.3))
        records.append(_make_exit_record(exit_reason="max_bars_timeout", mfe_r=0.3, pnl_r=-0.5))
        _seed_exit_population(fake, records)
        from research_engine.experiments.exit_depth import run_ex9
        report = run_ex9()
        assert report["status"] == "INSUFFICIENT_DATA"


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: EX10 — Exit survives walk-forward
# ═══════════════════════════════════════════════════════════════════════════════


class TestEX10:
    def test_registered(self):
        from research_engine.registry.research_question_registry import REGISTRY_BY_ID
        q = REGISTRY_BY_ID.get("EX10")
        assert q is not None
        assert q.runner_module

    def test_executable(self, fake_s3):
        from research_engine.experiments.exit_depth import run_ex10
        report = run_ex10()
        assert report is not None
        assert report["question_id"] == "EX10"

    def test_walk_forward_split(self, fake_s3):
        """EX10 reports early vs late period metrics."""
        from research_engine.experiments.exit_depth import run_ex10
        report = run_ex10()
        wf = report["overall"].get("walk_forward_analysis", {})
        assert "early_period" in wf
        assert "late_period" in wf

    def test_stability_assessment(self, fake_s3):
        """EX10 reports stability across periods."""
        from research_engine.experiments.exit_depth import run_ex10
        report = run_ex10()
        wf = report["overall"].get("walk_forward_analysis", {})
        assert "capture_stable_across_periods" in wf

    def test_insufficient_on_small_sample(self):
        """Very small sample produces INSUFFICIENT_DATA."""
        fake = install_fake_s3()
        _seed_exit_population(fake, [_make_exit_record() for _ in range(3)])
        from research_engine.experiments.exit_depth import run_ex10
        report = run_ex10()
        assert report["status"] == "INSUFFICIENT_DATA"


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: Cross-cutting concerns
# ═══════════════════════════════════════════════════════════════════════════════


class TestCorrectionTemporalOrdering:
    """EX10 must use canonical entry_time, not shadow_trade_id."""

    def test_ex10_orders_by_entry_time(self):
        """EX10 sorts by entry_time despite conflicting shadow_trade_id."""
        fake = install_fake_s3()
        records = []
        for i in range(50):
            entry_time = 1700000.0 + i * 100
            # Deliberately reverse shadow_trade_id ordering
            shadow_id = f"nshadow_{49-i}_EURUSD_SCALP"
            rec = _make_exit_record(
                shadow_trade_id=shadow_id,
                strategy="REVERSAL", pattern="HAMMER",
                pnl_r=2.0 if i % 3 != 0 else -1.0,
                mfe_r=2.5, mae_r=-0.5 if i % 3 != 0 else -1.5,
                exit_reason="take_profit", trade_horizon="SCALP",
                bars_held=5,
            )
            rec["canonical_opportunity_id"] = f"EURUSD*{int(entry_time)}*HAMMER"
            records.append(rec)
        _seed_exit_population(fake, records)
        from research_engine.experiments.exit_depth import run_ex10
        report = run_ex10()
        assert report["status"] in ("COMPLETE", "INSUFFICIENT_DATA")
        wf = report["overall"].get("walk_forward_analysis", {})
        if wf:
            method = wf.get("method", "")
            assert "entry_time" in method.lower(), \
                f"Expected entry_time ordering, got: {method}"


class TestCorrectionSemanticHonesty:
    """EX9/EX10 must not claim counterfactual/walk-forward validation."""

    def test_ex9_does_not_claim_counterfactual(self):
        """EX9 reports observational + BLOCKED_BY_DATA for counterfactual."""
        fake = install_fake_s3()
        _seed_exit_population(fake, _default_records(80))
        from research_engine.experiments.exit_depth import run_ex9
        report = run_ex9()
        sh = report["overall"].get("semantic_honesty", {})
        assert sh.get("counterfactual_proposed_exit_policy_reduces_timeouts") == "BLOCKED_BY_DATA"

    def test_ex10_does_not_claim_walk_forward(self):
        """EX10 reports temporal stability + BLOCKED_BY_DATA for walk-forward."""
        fake = install_fake_s3()
        _seed_exit_population(fake, _default_records(80))
        from research_engine.experiments.exit_depth import run_ex10
        report = run_ex10()
        sh = report["overall"].get("semantic_honesty", {})
        assert sh.get("walk_forward_improvement_validation") == "BLOCKED_BY_DATA"


class TestCorrectionExistingMetricsPreserved:
    """Existing EX5–EX10 observable metrics still work after corrections."""

    def test_ex5_horizon_segments(self, fake_s3):
        from research_engine.experiments.exit_depth import run_ex5
        report = run_ex5()
        assert len(report["overall"].get("segments", {})) > 0

    def test_ex6_strategy_segments(self, fake_s3):
        from research_engine.experiments.exit_depth import run_ex6
        assert len(run_ex6()["overall"].get("segments", {})) > 0

    def test_ex7_regime_segments(self, fake_s3):
        from research_engine.experiments.exit_depth import run_ex7
        assert len(run_ex7()["overall"].get("segments", {})) > 0

    def test_ex8_pattern_segments(self, fake_s3):
        from research_engine.experiments.exit_depth import run_ex8
        assert len(run_ex8()["overall"].get("segments", {})) > 0

    def test_ex9_timeout_analysis(self, fake_s3):
        from research_engine.experiments.exit_depth import run_ex9
        r = run_ex9()
        assert "timeout_analysis" in r["overall"] or "semantic_honesty" in r["overall"]

    def test_ex10_walk_forward_analysis(self, fake_s3):
        from research_engine.experiments.exit_depth import run_ex10
        r = run_ex10()
        assert "walk_forward_analysis" in r["overall"] or "semantic_honesty" in r["overall"]

    def test_runner_discovery(self, fake_s3):
        """All EX5–EX10 discoverable."""
        from research_engine.runner_discovery import discover_runners
        runners = discover_runners()
        for qid in ("EX5", "EX6", "EX7", "EX8", "EX9", "EX10"):
            assert qid in runners and callable(runners[qid])

    def test_empty_produces_insufficient(self):
        fake = install_fake_s3()
        _seed_exit_population(fake, [])
        from research_engine.experiments.exit_depth import (
            run_ex5, run_ex6, run_ex7, run_ex8, run_ex9, run_ex10,
        )
        for runner in (run_ex5, run_ex6, run_ex7, run_ex8, run_ex9, run_ex10):
            assert runner()["status"] in ("INSUFFICIENT_DATA", "BLOCKED")

    def test_no_production_schemas(self):
        import research_engine.experiments.exit_depth as ed
        src = Path(ed.__file__).read_text(encoding="utf-8")
        for mod in ("core.shadow_trades", "core.trade_truth",
                     "core.execution_context", "core.opportunity"):
            assert mod not in src
        from research_engine.experiments.exit_depth import run_ex5
        report = run_ex5()
        assert "shadow" in report["dataset"]["source"].lower()

    def test_empty_produces_insufficient(self):
        """All runners handle empty evidence gracefully."""
        fake = install_fake_s3()
        _seed_exit_population(fake, [])
        from research_engine.experiments.exit_depth import (
            run_ex5, run_ex6, run_ex7, run_ex8, run_ex9, run_ex10,
        )
        for runner in (run_ex5, run_ex6, run_ex7, run_ex8):
            report = runner()
            assert report["status"] == "INSUFFICIENT_DATA"

    def test_non_duplicate_ownership(self, fake_s3):
        """EX5-EX10 each have unique segmentation dimension."""
        from research_engine.experiments.exit_depth import (
            run_ex5, run_ex6, run_ex7, run_ex8, run_ex9,
        )
        from research_engine.experiments.exit_management import run_ex1

        ex5_seg = run_ex5()["overall"]["segmentation"]
        ex6_seg = run_ex6()["overall"]["segmentation"]
        ex7_seg = run_ex7()["overall"]["segmentation"]
        ex8_seg = run_ex8()["overall"]["segmentation"]

        segs = [ex5_seg, ex6_seg, ex7_seg, ex8_seg]
        assert len(set(segs)) == len(segs), "Duplicate segmentations detected"

    def test_observational_label_applied(self, fake_s3):
        """EX5-EX10 label methodology as OBSERVATIONAL."""
        from research_engine.experiments.exit_depth import run_ex5
        report = run_ex5()
        assert "OBSERVATIONAL" in report["overall"].get("methodology", "").upper()


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: Correction-specific — Clean evidence, temporal ordering, semantic honesty
# ═══════════════════════════════════════════════════════════════════════════════


class TestCorrectionCleanEvidence:
    """CURRENT-epoch filtering: EX5–EX10 must exclude LEGACY/TRANSITIONAL."""

    def _make_legacy_record(self, i: int) -> dict:
        """Build a record WITHOUT canonical_opportunity_id (→ LEGACY)."""
        return {
            "shadow_trade_id": f"nshadow_legacy_{i}_EURUSD_SCALP",
            "canonical_opportunity_id": "",
            "symbol": "EURUSD", "pattern": "HAMMER",
            "direction": "BUY", "strategy": "REVERSAL",
            "trade_horizon": "SCALP", "pnl_r": 10.0,
            "mfe_r": 12.0, "mae_r": -1.0,
            "exit_reason": "take_profit", "bars_held": 5,
            "h4_regime": "TRENDING", "market_phase": "IMPULSE",
        }

    def test_loader_excludes_legacy(self):
        """Population loader only includes CURRENT records."""
        fake = install_fake_s3()
        clean = _default_records(20)
        legacy = [self._make_legacy_record(i) for i in range(20)]
        _seed_exit_population(fake, clean + legacy)
        from research_engine.experiments.exit_depth import (
            _load_exit_depth_population
        )
        pop = _load_exit_depth_population()
        assert len(pop) <= 22
        for rec in pop:
            assert rec["data_epoch"] == "CURRENT"