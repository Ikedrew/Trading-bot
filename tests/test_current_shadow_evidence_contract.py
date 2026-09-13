"""Focused regression tests for the CURRENT shadow-evidence contract.

All tests are deterministic and offline.  The end-to-end test captures the
runtime writer's event dictionaries in memory; it never touches S3 or runs a
research calculation.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.shadow.integration import handle_live_opportunity_shadow
from core.shadow.runtime import ShadowRuntime, _shadow_trade_id
from core.v10.strategy_family import StrategyFamily
from research_engine.control_plane.models import ReportValidity
from research_engine.control_plane.report_resolver import resolve_report_validity
from research_engine.control_plane.state_builder import build_all_question_states
from research_engine.data_access.shadow_runtime_ingestion import (
    reconstruct_completed_shadow_trades,
)
from research_engine.data_quality.classifier import DataEpoch, classify_record
from research_engine.experiments.experiment_base import build_fingerprint
from research_engine.registry.research_question_registry import REGISTRY


class _CapturingWriter:
    """Shadow writer stand-in that preserves exact persistence payloads."""

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = str(base_dir)
        self.events: list[dict] = []

    @property
    def base_dir(self) -> str:
        return self._base_dir

    def append(self, *, event, symbol, market_time_raw, broker_offset_seconds):
        del symbol, market_time_raw, broker_offset_seconds
        self.events.append(copy.deepcopy(event))


def _horizon_result():
    return SimpleNamespace(
        to_dict=lambda: {
            "assessments": [
                {
                    "horizon": "SCALP",
                    "eligible": True,
                    "confidence": 0.8,
                    "reasoning": "contract fixture",
                }
            ]
        }
    )


def _v10_result(regime: str, strategy: str = "MEAN_REVERSION") -> dict:
    return {
        "action": "NO_TRADE",
        "side": "SELL",
        "pattern": "TEST_PATTERN",
        "strategy": strategy,
        "score": 0.8,
        # Deliberately contradictory: the V10 pipeline value must win.
        "activation_regime": "LEGACY_FALLBACK",
        "v10_pipeline_result": SimpleNamespace(
            horizon=SimpleNamespace(horizon_type="SCALP"),
            rejection_stage="risk",
            market_state=SimpleNamespace(
                regime=SimpleNamespace(regime=regime),
            ),
        ),
    }


def _write_complete_v10_lifecycle(
    monkeypatch,
    tmp_path: Path,
    *,
    regime: str = "TRENDING",
    strategy: str = "MEAN_REVERSION",
    root: str = "EURUSD*1784800000*TEST_PATTERN",
) -> tuple[list[dict], list[dict]]:
    writer = _CapturingWriter(tmp_path)
    runtime = ShadowRuntime(writer=writer)
    monkeypatch.setattr("core.shadow.runtime.get_shadow_runtime", lambda: runtime)

    # Isolate this contract test from the geometry implementation.  The fixed
    # object is the construction result consumed by the shadow runtime.
    trade = SimpleNamespace(
        entry=1.1000,
        stop_loss=1.1010,
        take_profit=1.0980,
        rr=2.0,
        sl_source="fixture",
        reasoning=("fixture stop", "fixture target"),
    )
    monkeypatch.setattr(
        "core.horizon.horizon_trade_builder.build_horizon_trade",
        lambda **kwargs: trade,
    )

    handle_live_opportunity_shadow(
        symbol="EURUSD",
        cycle_id=42,
        closed_time=1_784_800_000,
        candles=[SimpleNamespace(high=1.1005, low=1.0995)],
        closed_i=0,
        bid=1.1000,
        ask=1.1002,
        htf_context=None,
        new_result=_v10_result(regime, strategy),
        horizon_result=_horizon_result(),
        canonical_opportunity_id=root,
        entity_id="EURUSD_1784800000",
        observation_id="obs_contract_fixture",
    )
    # Close the SELL lifecycle at its stop on the next authoritative bar.
    runtime.evaluate_bar(
        symbol="EURUSD",
        bar_time=1_784_800_300,
        bar_high=1.1020,
        bar_low=1.0990,
        bar_close=1.1010,
        bar_index=1,
    )
    records = reconstruct_completed_shadow_trades(writer.events)
    return writer.events, records


@pytest.mark.parametrize("regime", ["TRENDING", "RANGING"])
def test_authoritative_v10_regime_reaches_persisted_open(
    monkeypatch, tmp_path, regime
):
    events, records = _write_complete_v10_lifecycle(
        monkeypatch, tmp_path, regime=regime
    )
    opened = next(event for event in events if event["event_type"] == "OPEN")
    assert opened["live_facts"]["h4_regime"] == regime
    assert opened["live_facts"]["regime"] == regime
    assert records[0]["decision_snapshot"]["h4_regime"] == regime


@pytest.mark.parametrize(
    "strategy",
    [
        StrategyFamily.MEAN_REVERSION.value,
        StrategyFamily.TREND_CONTINUATION.value,
    ],
)
def test_canonical_v10_strategy_families_are_current_eligible(strategy):
    assert classify_record(_current_record(strategy=strategy)) == DataEpoch.CURRENT


def test_contaminated_strategy_remains_legacy():
    record = _current_record(strategy="MEAN_REVERSION_SCALP")
    assert classify_record(record) == DataEpoch.LEGACY


def test_shadow_id_contract_is_stable_and_cycle_independent():
    root = "EURUSD*1784800000*TEST_PATTERN"
    first = _shadow_trade_id(root, "SCALP")
    replay_after_restart = _shadow_trade_id(root, "SCALP")
    assert first == replay_after_restart
    assert re.fullmatch(r"nshadow_[0-9a-f]{16}", first)
    assert first != _shadow_trade_id(root + "_NEXT", "SCALP")
    assert first != _shadow_trade_id(root, "INTRADAY")
    # cycle_id is intentionally absent from the identity input and API.
    assert "cycle" not in first


def test_complete_new_v10_lifecycle_reconstructs_and_classifies_current(
    monkeypatch, tmp_path
):
    events, records = _write_complete_v10_lifecycle(monkeypatch, tmp_path)
    assert [event["event_type"] for event in events] == ["PLAN", "OPEN", "CLOSE"]
    assert len(records) == 1
    assert records[0]["schema_version"] == "shadow_trades_v1"
    assert classify_record(records[0]) == DataEpoch.CURRENT


def test_current_negative_controls(monkeypatch, tmp_path):
    _, records = _write_complete_v10_lifecycle(monkeypatch, tmp_path)
    good = records[0]

    missing_regime = copy.deepcopy(good)
    missing_regime["decision_snapshot"]["h4_regime"] = ""
    missing_regime["decision_snapshot"]["regime"] = ""
    assert classify_record(missing_regime) != DataEpoch.CURRENT

    malformed_strategy = copy.deepcopy(good)
    malformed_strategy["identity"]["strategy_id"] = "MEAN_REVERSION_SCALP"
    assert classify_record(malformed_strategy) != DataEpoch.CURRENT

    missing_root = copy.deepcopy(good)
    missing_root["identity"]["canonical_opportunity_id"] = ""
    assert classify_record(missing_root) != DataEpoch.CURRENT


def _current_record(
    *,
    strategy: str = "MEAN_REVERSION",
    regime: str = "TRENDING",
    canonical_opportunity_id: str = "EURUSD*1784800000*TEST_PATTERN",
) -> dict:
    return {
        "schema_version": "shadow_trades_v1",
        "identity": {
            "entity_id": "EURUSD_1784800000",
            "canonical_opportunity_id": canonical_opportunity_id,
            "strategy_id": strategy,
        },
        "decision_snapshot": {
            "h4_regime": regime,
            "trade_horizon": "SCALP",
        },
        "simulated_outcome": {"pnl_r_multiple": 0.0},
    }


def test_expected_value_runner_filters_injected_population_before_analysis(
    monkeypatch
):
    import research_engine.experiments.expected_value as module
    import research_engine.experiments.experiment_base as base

    seen: list[dict] = []

    def no_math_stub(records):
        seen.extend(records)
        return module.ExpectedValueResult(
            total_trades=len(records),
            confidence="LOW",
            conclusion="contract-only stub",
            edge_classification="NO_EDGE",
        )

    monkeypatch.setattr(module, "run_expected_value", no_math_stub)
    monkeypatch.setattr(base, "persist_report", lambda report, filename: None)

    current = _current_record()
    transitional = _current_record(regime="")
    report = module.run([transitional, current])

    assert seen == [current]
    assert report["fingerprint"]["epoch"] == "CURRENT"
    assert report["fingerprint"]["records_used"] == 1
    assert report["fingerprint"]["records_excluded"] == 1


def test_expected_value_default_loader_requests_current_only(monkeypatch):
    import research_engine.experiments.expected_value as module
    import research_engine.experiments.experiment_base as base

    requested: list[str] = []

    def current_loader(*, epoch):
        requested.append(epoch)
        return []

    monkeypatch.setattr(base, "load_shadow_trades", current_loader)
    monkeypatch.setattr(
        module,
        "run_expected_value",
        lambda records: module.ExpectedValueResult(
            total_trades=0,
            confidence="INSUFFICIENT_DATA",
            conclusion="contract-only stub",
            edge_classification="NO_EDGE",
        ),
    )
    monkeypatch.setattr(base, "persist_report", lambda report, filename: None)

    report = module.run()
    assert requested == ["CURRENT"]
    assert report["status"] == "INSUFFICIENT_DATA"
    assert report["recommendation"] == "INSUFFICIENT_DATA"
    assert report["fingerprint"]["epoch"] == "CURRENT"


def test_omitted_fingerprint_epoch_is_explicitly_unverified():
    fingerprint = build_fingerprint(1253, 0, "shadow_trades")
    assert fingerprint["epoch"] == "UNVERIFIED"


def test_epoch_unverified_historical_q19_is_invalidated():
    report = {
        "question_id": "Q19",
        "status": "COMPLETE",
        "epoch": "CURRENT",
        "fingerprint": {
            "dataset_id": "shadow_trades_2026-09-06",
            "records_used": 1253,
            "epoch": "CURRENT",
        },
    }
    validity, reason = resolve_report_validity(
        "q19_expected_value.json",
        report,
        expected_question_id="E1",
        accepted_question_ids=("Q19",),
    )
    assert validity == ReportValidity.INVALIDATED
    assert "bypassed canonical CURRENT filtering" in reason


def test_canonical_state_still_contains_all_baseline_questions(tmp_path):
    from research_engine.registry.baseline_manifest import BASELINE_QUESTION_IDS
    states = build_all_question_states(reports_dir=tmp_path, evidence_source={})
    assert len(states) == len(REGISTRY)
    assert len({state.question_id for state in states}) == len(REGISTRY)
    assert set(BASELINE_QUESTION_IDS) <= {state.question_id for state in states}
