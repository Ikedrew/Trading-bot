"""Focused HD11 L1 implementation and integration proofs."""
from __future__ import annotations

from copy import deepcopy
import json
from unittest.mock import patch

from research_engine.control_plane.models import ReportValidity
from research_engine.control_plane.report_ownership import canonical_report_owner
from research_engine.control_plane.report_resolver import resolve_report_validity
from research_engine.control_plane.state_builder import build_question_state
from research_engine.experiments.pattern_degradation import REPORT_FILENAME, run_l1
from research_engine.control_plane.shadow_timestamp_normalization import (
    CANDLE_NORMALIZATION_VERSION, POST_NORMALIZATION_PRODUCER_EPOCH_UTC,
    TIMESTAMP_SEMANTICS,
)
from research_engine.registry.master_repair_ledger import (
    STRUCTURALLY_NON_OPERATIONAL_IDS, STRUCTURALLY_OPERATIONAL_IDS,
    operational_baseline,
)
from research_engine.registry.definition_validator import (
    build_definitions_from_registry, get_question_health, validate_all_definitions,
)
from research_engine.registry.research_question_registry import REGISTRY
from research_engine.registry.research_question_registry import REGISTRY_BY_ID
from research_engine.runner_discovery import discover_runners


def _row(opportunity: str, pattern: str, entry_time: float, r_value: float) -> dict:
    token = sum((index + 1) * ord(char) for index, char in enumerate(opportunity))
    shadow_id = f"nshadow_{token:016x}"
    return {
        "schema_version": "shadow_trades_v1",
        "source": "shadow_runtime_ingestion",
        "source_schema_version": "shadow_runtime_v1",
        "identity": {
            "trade_id": shadow_id,
            "shadow_trade_id": shadow_id,
            "canonical_opportunity_id": opportunity,
            "entity_id": f"entity-{opportunity}",
            "symbol": "EURUSD",
            "strategy_id": "MEAN_REVERSION",
            "shadow_type": "PRIMARY_HORIZON_SIMULATION",
            "evaluated_horizon": "SCALP",
            "trade_horizon": "SCALP",
        },
        "decision_snapshot": {
            "timestamp_decision_utc": entry_time,
            "pattern": pattern,
            "strategy": "MEAN_REVERSION",
            "trade_horizon": "SCALP",
            "h4_regime": "TRENDING",
        },
        "simulated_outcome": {"pnl_r_multiple": r_value},
    }


def _population(*, degrade: bool = False, count: int = 200) -> list[dict]:
    rows = []
    for index in range(count):
        pattern = "HAMMER" if index % 2 == 0 else "ENGULFING"
        entry = 1_800_000_000 + index
        r_value = 0.25 + (index % 5) / 100
        if degrade and pattern == "HAMMER" and index >= count // 2:
            r_value -= 1.0
        rows.append(_row(f"EURUSD*{entry}*{pattern}-{index:04d}", pattern, entry, r_value))
    return rows


def _current_events(rows: list[dict]) -> list[dict]:
    events: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        identity = row["identity"]
        key = (
            identity["shadow_trade_id"], identity["canonical_opportunity_id"],
            identity["trade_horizon"],
        )
        if key in seen:
            continue
        seen.add(key)
        entry = int(row.get("_governed_entry_time", row["decision_snapshot"].get("timestamp_decision_utc", 0)))
        common = {
            "schema_version": "shadow_runtime_v1",
            "shadow_trade_id": key[0], "canonical_opportunity_id": key[1],
            "symbol": "EURUSD", "horizon": key[2],
            "market_timestamp_semantics": TIMESTAMP_SEMANTICS,
            "market_timestamp_normalization_version": CANDLE_NORMALIZATION_VERSION,
        }
        events.extend([
            {
                **common, "event_type": "OPEN",
                "event_market_time": entry, "event_market_time_utc_epoch_s": entry,
                "entry_market_time": entry, "entry_market_time_utc_epoch_s": entry,
                "opportunity_market_time": entry,
                "opportunity_market_time_utc_epoch_s": entry,
            },
            {**common, "event_type": "CLOSE"},
        ])
    return events


def _run_l1(rows: list[dict], **kwargs) -> dict:
    return run_l1(
        rows,
        shadow_runtime_events=kwargs.pop("shadow_runtime_events", _current_events(rows)),
        candle_events=kwargs.pop("candle_events", []),
        **kwargs,
    )


def _historical_source(row: dict, *, entry: int, offset: int = 10_800):
    identity = row["identity"]
    common = {
        "schema_version": "shadow_runtime_v1",
        "shadow_trade_id": identity["shadow_trade_id"],
        "canonical_opportunity_id": identity["canonical_opportunity_id"],
        "symbol": "EURUSD", "horizon": identity["trade_horizon"],
        "broker_offset_seconds": offset,
    }
    opened = {
        **common, "event_type": "OPEN",
        "event_market_time": entry,
        "event_market_time_utc_epoch_s": entry - offset,
        "entry_market_time": entry,
        "entry_market_time_utc_epoch_s": entry - offset,
        "opportunity_market_time": entry,
        "opportunity_market_time_utc_epoch_s": entry - offset,
        "recorded_at_utc_ms": (entry + 60) * 1000,
    }
    closes = (1.101, 1.102)
    exit_time = entry + len(closes) * 300
    closed = {
        **common, "event_type": "CLOSE",
        "event_market_time": exit_time,
        "event_market_time_utc_epoch_s": exit_time - offset,
        "exit_market_time": exit_time,
        "exit_market_time_utc_epoch_s": exit_time - offset,
        "recorded_at_utc_ms": (exit_time + 60) * 1000,
        "bars_held": len(closes), "data_gaps": [],
        "trade_state_progression": [
            {"bar": index, "r": index / 10, "close": close}
            for index, close in enumerate(closes, 1)
        ],
    }
    candles = [{
        "ts_utc_ms": (entry + index * 300 + 60) * 1000,
        "type": "CANDLE", "symbol": "EURUSD", "timeframe": "M5",
        "payload": {
            "ts": (entry + index * 300) * 1000,
            "o": close, "h": close + 0.001, "l": close - 0.001,
            "c": close, "v": 10,
            "timestamp_semantics": TIMESTAMP_SEMANTICS,
            "timestamp_normalization_version": CANDLE_NORMALIZATION_VERSION,
            "source_broker_offset_seconds": offset,
        },
        "source": "mt5_data", "schema_version": "events_v1",
    } for index, close in enumerate(closes, 1)]
    return [opened, closed], candles


def test_l1_has_unique_runner_report_owner_and_e2_is_unchanged():
    l1 = REGISTRY_BY_ID["L1"]
    e2 = REGISTRY_BY_ID["E2"]
    assert (l1.runner_module, l1.runner_function, l1.report_filename) == (
        "research_engine.experiments.pattern_degradation", "run_l1", REPORT_FILENAME,
    )
    assert (e2.runner_module, e2.runner_function, e2.report_filename) == (
        "research_engine.experiments.legacy_canonical", "run_q05", "q5_pattern_degradation.json",
    )
    assert discover_runners()["L1"] is run_l1
    assert canonical_report_owner(REPORT_FILENAME) == "L1"
    assert canonical_report_owner(e2.report_filename) == "E2"
    definitions = build_definitions_from_registry(REGISTRY)
    assert get_question_health(validate_all_definitions(definitions)["L1"]) in {
        "VALID", "VALID_WITH_WARNINGS",
    }
    assert definitions["L1"].minimum_sample == 200


def test_sufficient_null_completes_with_frozen_windows_and_inference():
    rows = list(reversed(_population()))
    report = _run_l1(rows)
    assert report["status"] == "COMPLETE"
    assert report["recommendation"] == "NO_RELIABLE_DEGRADATION"
    assert report["overall"]["distinct_canonical_opportunities"] == 200
    assert report["overall"]["chronology"]["early_n"] == 100
    assert report["overall"]["chronology"]["late_n"] == 100
    assert report["overall"]["chronology"]["early_last"]["canonical_opportunity_id"].endswith("-0099")
    assert report["overall"]["chronology"]["late_first"]["canonical_opportunity_id"].endswith("-0100")
    assert report["overall"]["eligible_patterns"] == ["ENGULFING", "HAMMER"]
    assert all(result["early_n"] == result["late_n"] == 50 for result in report["overall"]["patterns"].values())
    assert "Holm" in report["overall"]["inference"]["multiplicity_method"]
    assert report["fingerprint"]["epoch"] == "CURRENT"


def test_affected_historical_timestamp_uses_existing_governed_correction():
    entry = POST_NORMALIZATION_PRODUCER_EPOCH_UTC + 3_581
    entry -= entry % 300
    opportunity = f"EURUSD*{entry}*HAMMER-HIST"
    row = _row(opportunity, "HAMMER", entry - 10_800, 0.25)
    events, candles = _historical_source(row, entry=entry)
    report = _run_l1(
        [row], shadow_runtime_events=events, candle_events=candles,
    )
    authority = report["provenance"]["timestamp_authority"]["records"][0]
    assert authority["mode"] == "GOVERNED_HISTORICAL_NORMALIZATION"
    assert authority["entry_utc_epoch_s"] == entry
    assert authority["normalization_contract_version"] == (
        "shadow_post_candle_utc_normalization_v1"
    )


def test_current_timestamp_is_unchanged_and_never_double_normalized():
    rows = _population()
    with patch(
        "research_engine.experiments.pattern_degradation.normalize_post_candle_utc_lifecycle",
        side_effect=AssertionError("current evidence must not be normalized again"),
    ):
        report = _run_l1(rows)
    assert report["status"] == "COMPLETE"
    authorities = report["provenance"]["timestamp_authority"]["records"]
    assert {item["mode"] for item in authorities} == {"CURRENT_PRODUCER_CANONICAL_UTC"}
    assert min(item["entry_utc_epoch_s"] for item in authorities) == 1_800_000_000


def test_ambiguous_timestamp_provenance_fails_closed():
    rows = _population()
    report = run_l1(rows, shadow_runtime_events=[], candle_events=[])
    assert report["status"] == "BLOCKED"
    assert report["recommendation"] == "EVIDENCE_CONTRACT_FAILURE"
    assert "unresolved governed shadow OPEN entry-time authority" in report["overall"]["structural_failures"]


def test_window_assignment_uses_governed_time_not_persisted_claim():
    rows = _population()
    governed_entry = 1_799_999_700
    historical = _row(
        f"EURUSD*{governed_entry}*HAMMER-HIST", "HAMMER",
        1_800_000_500, 0.25,
    )
    rows[150] = historical
    historical_events, candles = _historical_source(
        historical, entry=governed_entry,
    )
    current_rows = [row for row in rows if row is not historical]
    report = _run_l1(
        rows,
        shadow_runtime_events=_current_events(current_rows) + historical_events,
        candle_events=candles,
    )
    assert report["status"] == "COMPLETE"
    assert report["overall"]["chronology"]["early_last"]["canonical_opportunity_id"].endswith("-0098")
    assert report["overall"]["chronology"]["late_first"]["canonical_opportunity_id"].endswith("-0099")
    records = report["provenance"]["timestamp_authority"]["records"]
    historical_authority = next(item for item in records if item["identity"][1].endswith("-HIST"))
    assert historical_authority["entry_utc_epoch_s"] == governed_entry


def test_degradation_estimand_and_holm_family_are_per_pattern():
    report = _run_l1(_population(degrade=True))
    hammer = report["overall"]["patterns"]["HAMMER"]
    assert report["status"] == "COMPLETE"
    assert hammer["late_minus_early_mean_r"] == -1.0
    assert hammer["classification"] == "RELIABLE_DEGRADATION"
    assert report["overall"]["reliably_degraded_patterns"] == ["HAMMER"]
    assert report["provenance"]["multiplicity_family"] == ["ENGULFING", "HAMMER"]


def test_canonical_opportunity_identity_collapses_fanout_and_never_enlarges_n():
    rows = _population()
    fanout = deepcopy(rows[0])
    fanout["account_id"] = "second-account"
    report = _run_l1(rows + [fanout])
    assert report["status"] == "COMPLETE"
    assert report["dataset"]["sample_size"] == 200
    assert report["overall"]["exclusions"]["collapsed_duplicate_or_account_fanout"] == 1


def test_minimum_evidence_and_missing_fields_wait_for_data():
    report = _run_l1(_population(count=199))
    assert report["status"] == "WAITING_DATA"
    assert report["recommendation"] == "WAIT_FOR_EVIDENCE"
    sparse = _population()
    for row in sparse[:45]:
        row["decision_snapshot"]["pattern"] = "RARE"
    report = _run_l1(sparse)
    assert report["status"] == "WAITING_DATA"
    missing = _population()
    missing[0]["_governed_entry_time"] = missing[0]["decision_snapshot"]["timestamp_decision_utc"]
    del missing[0]["decision_snapshot"]["timestamp_decision_utc"]
    report = _run_l1(missing)
    assert report["status"] == "COMPLETE"


def test_conflicting_or_mixed_epoch_evidence_fails_closed():
    rows = _population()
    conflict = deepcopy(rows[0])
    conflict["simulated_outcome"]["pnl_r_multiple"] = -9.0
    report = _run_l1(rows + [conflict])
    assert report["status"] == "BLOCKED"
    assert report["recommendation"] == "EVIDENCE_CONTRACT_FAILURE"
    assert report["overall"]["conflicting_canonical_opportunity_ids"][0].endswith("-0000")
    stale = _row("old", "HAMMER", 1_700_000_000, 1.0)
    stale["schema_version"] = "shadow_trades_v0"
    report = _run_l1(rows + [stale])
    assert report["status"] == "BLOCKED"
    assert "mixed evidence epochs" in report["overall"]["structural_failures"]


def test_owned_current_report_is_required_and_e2_cannot_cross_complete(tmp_path):
    report = _run_l1(_population())
    validity, _ = resolve_report_validity(REPORT_FILENAME, report, expected_question_id="L1")
    assert validity == ReportValidity.VALID_CURRENT
    (tmp_path / REPORT_FILENAME).write_text(json.dumps(report), encoding="utf-8")
    l1 = build_question_state("L1", reports_dir=tmp_path, evidence_source={})
    assert l1.state_status == "COMPLETE"

    e2_report = deepcopy(report)
    e2_report["question_id"] = "E2"
    (tmp_path / "q5_pattern_degradation.json").write_text(json.dumps(e2_report), encoding="utf-8")
    assert build_question_state("E2", reports_dir=tmp_path, evidence_source={}).state_status == "COMPLETE"
    (tmp_path / REPORT_FILENAME).unlink()
    assert build_question_state("L1", reports_dir=tmp_path, evidence_source={}).state_status != "COMPLETE"

    validity, _ = resolve_report_validity(REPORT_FILENAME, e2_report, expected_question_id="L1")
    assert validity == ReportValidity.INVALIDATED
    validity, _ = resolve_report_validity("q5_pattern_degradation.json", report, expected_question_id="E2")
    assert validity == ReportValidity.INVALIDATED


def test_waiting_and_blocked_reports_integrate_with_readiness(tmp_path):
    waiting = _run_l1(_population(count=199))
    (tmp_path / REPORT_FILENAME).write_text(json.dumps(waiting), encoding="utf-8")
    assert build_question_state("L1", reports_dir=tmp_path, evidence_source={}).state_status == "WAITING_DATA"
    rows = _population()
    rows.append({**deepcopy(rows[0]), "simulated_outcome": {"pnl_r_multiple": -4.0}})
    blocked = _run_l1(rows)
    (tmp_path / REPORT_FILENAME).write_text(json.dumps(blocked), encoding="utf-8")
    assert build_question_state("L1", reports_dir=tmp_path, evidence_source={}).state_status == "BLOCKED"


def test_ledger_baseline_includes_independent_l1_and_l4_implementations():
    assert operational_baseline() == (60, 10)
    assert "L1" in STRUCTURALLY_OPERATIONAL_IDS
    assert STRUCTURALLY_NON_OPERATIONAL_IDS == {
        "EX5", "EX6", "EX8", "G1", "G2", "G3", "L2", "L3", "L6", "L7",
    }
    assert not ({"L2", "L3", "L6", "L7"} & STRUCTURALLY_OPERATIONAL_IDS)
