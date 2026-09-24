"""Focused HD11 L4 implementation and integration proofs."""
from __future__ import annotations

from copy import deepcopy
import json

from research_engine.control_plane.models import ReportValidity
from research_engine.control_plane.report_ownership import canonical_report_owner
from research_engine.control_plane.report_resolver import resolve_report_validity
from research_engine.control_plane.shadow_timestamp_normalization import (
    CANDLE_NORMALIZATION_VERSION, TIMESTAMP_SEMANTICS,
)
from research_engine.control_plane.state_builder import build_question_state
from research_engine.experiments.market_behaviour_stability import (
    REPORT_FILENAME, _holm_adjust, run_l4,
)
from research_engine.experiments.legacy_canonical import run_q17
from research_engine.registry.definition_validator import (
    build_definitions_from_registry, get_question_health, validate_all_definitions,
)
from research_engine.registry.master_repair_ledger import (
    STRUCTURALLY_NON_OPERATIONAL_IDS, STRUCTURALLY_OPERATIONAL_IDS,
    operational_baseline,
)
from research_engine.registry.research_question_registry import REGISTRY, REGISTRY_BY_ID
from research_engine.runner_discovery import discover_runners

CELLS = ("TRENDING", "RANGING", "TRANSITIONAL")


def _row(index: int, regime: str, r_value: float) -> dict:
    entry = 1_900_000_000 + index * 10
    opportunity = f"EURUSD*{entry}*L4-{index:04d}"
    shadow_id = f"nshadow_{index:016x}"
    return {
        "schema_version": "shadow_trades_v1", "source": "shadow_runtime_ingestion",
        "source_schema_version": "shadow_runtime_v1",
        "identity": {
            "shadow_trade_id": shadow_id, "canonical_opportunity_id": opportunity,
            "entity_id": f"shadow-entity-{index}", "symbol": "EURUSD",
            "strategy_id": "MEAN_REVERSION",
            "shadow_type": "PRIMARY_HORIZON_SIMULATION",
            "evaluated_horizon": "SCALP", "trade_horizon": "SCALP",
        },
        "decision_snapshot": {
            "timestamp_decision_utc": entry, "strategy": "MEAN_REVERSION",
            "trade_horizon": "SCALP", "h4_regime": "TRENDING",
        },
        "simulated_outcome": {"pnl_r_multiple": r_value},
        "_test_regime": regime,
    }


def _population(*, count: int = 240, late_mix: bool = False, r_drop: str | None = None) -> list[dict]:
    rows = []
    half = count // 2
    for index in range(count):
        local = index if index < half else index - half
        if late_mix and index >= half:
            trending_end = half // 2
            ranging_end = trending_end + half // 4
            regime = "TRENDING" if local < trending_end else "RANGING" if local < ranging_end else "TRANSITIONAL"
        else:
            regime = CELLS[local % 3]
        r_value = 0.20 + (local % 7) * 0.02
        if r_drop == regime and index >= half:
            r_value -= 0.75
        rows.append(_row(index, regime, r_value))
    return rows


def _events(rows: list[dict], *, open_symbol: str = "EURUSD") -> list[dict]:
    events = []
    seen = set()
    for row in rows:
        identity = row["identity"]
        key = (identity["shadow_trade_id"], identity["canonical_opportunity_id"], identity["trade_horizon"])
        if key in seen:
            continue
        seen.add(key)
        entry = int(identity["canonical_opportunity_id"].split("*")[1])
        common = {
            "schema_version": "shadow_runtime_v1", "shadow_trade_id": key[0],
            "canonical_opportunity_id": key[1], "symbol": open_symbol, "horizon": key[2],
            "market_timestamp_semantics": TIMESTAMP_SEMANTICS,
            "market_timestamp_normalization_version": CANDLE_NORMALIZATION_VERSION,
        }
        events.extend(({
            **common, "event_type": "OPEN", "event_market_time": entry,
            "event_market_time_utc_epoch_s": entry, "entry_market_time": entry,
            "entry_market_time_utc_epoch_s": entry, "opportunity_market_time": entry,
            "opportunity_market_time_utc_epoch_s": entry,
        }, {**common, "event_type": "CLOSE"}))
    return events


def _contexts(rows: list[dict]) -> list[dict]:
    return [{
        "schema_version": "market_context_v1", "data_epoch": "CURRENT",
        "entity_id": f"context-{index}",
        "symbol": "EURUSD",
        "bar_time": int(row["identity"]["canonical_opportunity_id"].split("*")[1]) - 1,
        "regime": row["_test_regime"],
    } for index, row in enumerate(rows)]


def _run(rows: list[dict], contexts: list[dict] | None = None, events: list[dict] | None = None) -> dict:
    return run_l4(
        rows, market_context_records=_contexts(rows) if contexts is None else contexts,
        shadow_runtime_events=_events(rows) if events is None else events,
        candle_events=[],
    )


def test_l4_unique_runner_report_owner_definition_and_q17_separation():
    question = REGISTRY_BY_ID["L4"]
    assert (question.runner_module, question.runner_function, question.report_filename, question.legacy_ids) == (
        "research_engine.experiments.market_behaviour_stability", "run_l4", REPORT_FILENAME, (),
    )
    assert discover_runners()["L4"] is run_l4
    assert canonical_report_owner(REPORT_FILENAME) == "L4"
    assert run_q17.__module__ == "research_engine.experiments.legacy_canonical"
    definitions = build_definitions_from_registry(REGISTRY)
    assert get_question_health(validate_all_definitions(definitions)["L4"]) in {"VALID", "VALID_WITH_WARNINGS"}
    assert definitions["L4"].minimum_sample == 200


def test_stable_sufficient_evidence_completes_all_four_endpoints():
    report = _run(_population())
    assert report["status"] == "COMPLETE"
    assert report["recommendation"] == "STABLE"
    assert report["overall"]["final_result"] == "STABLE"
    assert report["overall"]["chronology"]["early_n"] == 120
    assert report["overall"]["chronology"]["late_n"] == 120
    assert report["overall"]["endpoints"]["regime_mix_shift"]["tv_distance"] == 0.0
    regimes = report["overall"]["endpoints"]["regime_r_shift"]
    assert set(regimes) == set(CELLS)
    assert all(regimes[cell]["early_n"] == regimes[cell]["late_n"] == 40 for cell in CELLS)
    assert report["provenance"]["multiplicity_family"] == [
        "regime_mix_shift", "TRENDING_R_shift", "RANGING_R_shift", "TRANSITIONAL_R_shift",
    ]
    assert report["fingerprint"]["epoch"] == "CURRENT"


def test_material_mix_and_adverse_r_are_completed_instability_findings():
    mix_report = _run(_population(count=600, late_mix=True))
    assert mix_report["status"] == "COMPLETE"
    assert mix_report["overall"]["endpoints"]["regime_mix_shift"]["tv_distance"] >= 0.10
    assert mix_report["overall"]["final_result"] == "MATERIAL_INSTABILITY"
    r_report = _run(_population(r_drop="RANGING"))
    contrast = r_report["overall"]["endpoints"]["regime_r_shift"]["RANGING"]
    assert contrast["late_minus_early_mean_r"] <= -0.25
    assert contrast["holm_significant"] and contrast["threatening"]
    assert r_report["status"] == "COMPLETE"
    assert r_report["overall"]["final_result"] == "MATERIAL_INSTABILITY"


def test_asof_join_ignores_future_excludes_missing_and_blocks_conflicting_latest_ties():
    rows = _population()
    contexts = _contexts(rows)
    future = {**contexts[-1], "entity_id": "future", "bar_time": 2_000_000_000, "regime": "TRANSITIONAL"}
    report = _run(rows, contexts + [future])
    assert report["status"] == "COMPLETE"
    assert report["overall"]["join"]["contexts_used"] == 240
    missing = _run(rows, contexts[1:])
    assert missing["overall"]["exclusions"]["no_preceding_same_symbol_context"] == 1
    first = contexts[0]
    conflict = {**first, "entity_id": "conflict", "regime": "RANGING"}
    blocked = _run(rows, contexts + [conflict])
    assert blocked["status"] == "BLOCKED"
    assert "conflicting latest-time market context" in blocked["overall"]["structural_failures"]
    lineage_conflict = {**first, "entity_id": "different-lineage-same-regime"}
    assert _run(rows, contexts + [lineage_conflict])["status"] == "BLOCKED"


def test_identical_context_duplicates_and_account_fanout_do_not_enlarge_n():
    rows = _population()
    fanout = deepcopy(rows[0]); fanout["account_id"] = "account-2"
    contexts = _contexts(rows)
    report = _run(rows + [fanout], contexts + [deepcopy(contexts[0])])
    assert report["status"] == "COMPLETE"
    assert report["dataset"]["sample_size"] == 240
    assert report["overall"]["exclusions"]["collapsed_duplicate_or_account_fanout"] == 1
    assert report["overall"]["exclusions"]["collapsed_identical_context_duplicates"] == 1


def test_open_symbol_mismatch_and_missing_timestamp_authority_block():
    rows = _population()
    mismatch = _run(rows, events=_events(rows, open_symbol="GBPUSD"))
    assert mismatch["status"] == "BLOCKED"
    assert mismatch["overall"]["exclusions"]["open_opportunity_symbol_conflict"] == 240
    unresolved = _run(rows, events=[])
    assert unresolved["status"] == "BLOCKED"
    assert "unresolved governed shadow OPEN entry-time authority" in unresolved["overall"]["structural_failures"]


def test_odd_midpoint_minimum_cells_and_estimability_semantics():
    odd_rows = _population(count=241)
    odd = _run(odd_rows)
    assert odd["overall"]["chronology"]["midpoint_excluded"] == 1
    assert odd["overall"]["chronology"]["early_n"] == odd["overall"]["chronology"]["late_n"] == 120
    insufficient = _run(_population(count=199))
    assert insufficient["status"] == "WAITING_DATA"
    constant = _population()
    for row in constant:
        if row["_test_regime"] == "TRENDING": row["simulated_outcome"]["pnl_r_multiple"] = 0.5
    assert _run(constant)["status"] == "WAITING_DATA"


def test_holm_ties_use_frozen_order_and_positive_improvement_is_stable():
    adjusted = _holm_adjust({name: 0.01 for name in (
        "regime_mix_shift", "TRENDING_R_shift", "RANGING_R_shift", "TRANSITIONAL_R_shift",
    )})
    assert list(adjusted) == ["regime_mix_shift", "TRENDING_R_shift", "RANGING_R_shift", "TRANSITIONAL_R_shift"]
    improved = _population(r_drop="TRENDING")
    for row in improved[120:]:
        if row["_test_regime"] == "TRENDING": row["simulated_outcome"]["pnl_r_multiple"] += 1.5
    report = _run(improved)
    endpoint = report["overall"]["endpoints"]["regime_r_shift"]["TRENDING"]
    assert endpoint["holm_significant"] and endpoint["late_minus_early_mean_r"] > 0
    assert not endpoint["threatening"]
    assert report["overall"]["final_result"] == "STABLE"


def test_significant_but_immaterial_and_material_but_unsupported_are_stable():
    immaterial = _population(count=600)
    for row in immaterial[300:]:
        if row["_test_regime"] == "RANGING": row["simulated_outcome"]["pnl_r_multiple"] -= 0.10
    report = _run(immaterial)
    endpoint = report["overall"]["endpoints"]["regime_r_shift"]["RANGING"]
    assert endpoint["holm_significant"] and not endpoint["material"]
    assert report["overall"]["final_result"] == "STABLE"

    unsupported = _population()
    early_seen = late_seen = 0
    for index, row in enumerate(unsupported):
        if row["_test_regime"] != "RANGING":
            continue
        if index < 120:
            row["simulated_outcome"]["pnl_r_multiple"] = -10.0 if early_seen % 2 else 10.0
            early_seen += 1
        else:
            row["simulated_outcome"]["pnl_r_multiple"] = (-10.0 if late_seen % 2 else 10.0) - 0.30
            late_seen += 1
    report = _run(unsupported)
    endpoint = report["overall"]["endpoints"]["regime_r_shift"]["RANGING"]
    assert endpoint["material"] and not endpoint["holm_significant"]
    assert report["overall"]["final_result"] == "STABLE"


def test_owned_valid_current_report_required_and_self_declared_or_q17_cannot_complete(tmp_path):
    report = _run(_population())
    validity, _ = resolve_report_validity(REPORT_FILENAME, report, expected_question_id="L4")
    assert validity == ReportValidity.VALID_CURRENT
    (tmp_path / REPORT_FILENAME).write_text(json.dumps(report), encoding="utf-8")
    assert build_question_state("L4", reports_dir=tmp_path, evidence_source={}).state_status == "COMPLETE"
    forged = _run(_population(count=199)); forged["status"] = "COMPLETE"; forged["recommendation"] = "STABLE"
    validity, _ = resolve_report_validity(REPORT_FILENAME, forged, expected_question_id="L4")
    assert validity == ReportValidity.INVALIDATED
    (tmp_path / REPORT_FILENAME).unlink()
    (tmp_path / "q17_drawdown_precursors.json").write_text(json.dumps(report), encoding="utf-8")
    assert build_question_state("L4", reports_dir=tmp_path, evidence_source={}).state_status != "COMPLETE"


def test_structural_baseline_is_60_and_only_l4_changed_in_learning_set():
    assert operational_baseline() == (60, 10)
    assert "L4" in STRUCTURALLY_OPERATIONAL_IDS
    assert STRUCTURALLY_NON_OPERATIONAL_IDS == {
        "EX5", "EX6", "EX8", "G1", "G2", "G3", "L2", "L3", "L6", "L7",
    }
    assert not ({"L2", "L3", "L6", "L7"} & STRUCTURALLY_OPERATIONAL_IDS)
