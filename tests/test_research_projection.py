"""Tests for the research_data projection layer (research_projection).

Covers the Phase 7B / research-layer acceptance criteria:
  * logs/ is read-only (bit-for-bit unchanged across runs)
  * projection is idempotent (same source bytes -> no new records)
  * source changes are detectable via fingerprints
  * lineage is preserved verbatim; nothing is fabricated
  * field-level reconciliation records conflicts instead of overwriting
  * outcome fields never leak into live/execution records
  * LIVE and SHADOW remain separately identifiable
"""

from __future__ import annotations

import hashlib
import json

import pytest

from research_projection.projector import Projector


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


def read_jsonl(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def tree_hash(root):
    digest = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            digest.update(str(p.relative_to(root)).encode())
            digest.update(p.read_bytes())
    return digest.hexdigest()


def read_area(research_root, area):
    out = {}
    base = research_root / area
    if not base.is_dir():
        return out
    for f in base.rglob("*.jsonl"):
        out[str(f.parent.name)] = read_jsonl(f)
    return out


# ---------------------------------------------------------------------------
# fixture: a small but representative logs/ capture
# ---------------------------------------------------------------------------

@pytest.fixture()
def logs_tree(tmp_path):
    logs = tmp_path / "logs"

    # LIVE observation
    write_jsonl(logs / "strategy_observations" / "EURUSD" / "2026-07-28.jsonl", [
        {
            "schema_version": "strategy_observation_v1",
            "observation_id": "EURUSD_1_1785205500",
            "timestamp_utc": 1785205500.0,
            "symbol": "EURUSD",
            "cycle_id": 1,
            "h4_regime": "RANGE",
            "detected_pattern": "TWEEZER_BOTTOM",
            "decision_action": "NO_TRADE",
            "entity_id": "EURUSD_1785205500",
        },
    ])

    # LIVE opportunity (opportunity_id present, canonical absent -> stays absent)
    write_jsonl(logs / "opportunities" / "EURUSD" / "2026-07-28.jsonl", [
        {
            "schema_version": "opportunity_v1",
            "opportunity_id": "EURUSD_1785255900_EVENING_STAR",
            "symbol": "EURUSD",
            "cycle_id": 1,
            "direction": "SELL",
            "pattern": "EVENING_STAR",
            "state": "DETECTED",
            "entity_id": "EURUSD_1785255900",
            "correlation_id": "",
            "decision_id": "",
        },
    ])

    # LIVE decision spine + contributors (exact entity_id match)
    ledger_rec = {
        "schema_version": "decision_ledger_v1",
        "timestamp": "2026-07-28T13:27:16.000Z",
        "symbol": "EURUSD",
        "cycle_id": 1,
        "decision": "EXECUTE",
        "reason": "pattern_execute",
        "correlation_id": "COR-20260728-1-EURUSD-D6EF",
        "entity_id": "EURUSD_1785255900",
        "engine_version": "new_engine",
    }
    write_jsonl(logs / "decision_ledger" / "EURUSD" / "2026-07-28.jsonl",
                [ledger_rec])
    write_jsonl(logs / "decision_trace" / "EURUSD" / "2026-07-28.jsonl", [
        {
            "schema_version": "decision_trace_v1",
            "entity_id": "EURUSD_1785255900",
            "symbol": "EURUSD",
            "cycle_id": 1,
            # conflicting copy: ledger `decision` is authoritative
            "action": "EXECUTE",
            "reason": "trace_says_something_else",
            "correlation_id": "v10_EURUSD_1785255900_1",
            "stages_reached": ["pattern_detection", "risk", "execute"],
            "stages_passed": ["pattern_detection", "risk"],
            "terminal_stage": "execute",
        },
    ])
    write_jsonl(logs / "decision_audit" / "EURUSD_2026-07-28.jsonl", [
        {
            "schema_version": "decision_audit_v1",
            "symbol": "EURUSD",
            "cycle_id": 1,
            "should_trade": True,
            "decision_id": "deadbeefdeadbeefdeadbeefdeadbeef",
            "correlation_id": "COR-20260728-1-EURUSD-D6EF",
            "entity_id": "EURUSD_1785255900",
            "ev_gate_enabled": True,
        },
    ])
    write_jsonl(logs / "assessments" / "EURUSD" / "2026-07-28.jsonl", [
        {
            "schema_version": "assessment_v1",
            "assessment_id": "EURUSD_1785255900__assessment",
            "symbol": "EURUSD",
            "cycle_id": 1,
            "entity_id": "EURUSD_1785255900",
            "canonical_opportunity_id": "",
            "ev": 0.25,
            "ev_positive": True,
            "p_success": 0.61,
            "weights_used": "global_v3",
        },
    ])

    # LIVE execution: context (owner) + result with SAME correlation id,
    # plus an orphan result nobody owns
    write_jsonl(logs / "execution_context" / "EURUSD" / "2026-07-28.jsonl", [
        {
            "schema_version": "execution_context_v1",
            "correlation_id": "COR-20260728-1-EURUSD-D6EF",
            "symbol": "EURUSD",
            "cycle_id": 1,
            "market_access": {"spread": 1e-05, "bid": 1.13674},
            "infrastructure": {"latency_ms": 45},
        },
    ])
    write_jsonl(logs / "execution_results" / "EURUSD" / "2026-07-28.jsonl", [
        {
            "schema_version": "execution_results_v1",
            "correlation_id": "COR-20260728-1-EURUSD-D6EF",
            "symbol": "EURUSD",
            "cycle_id": 1,
            "result_ok": True,
            "retcode": 10009,
            "fill_price": 1.13674,
            "side": "SELL",
            "volume": 0.01,
        },
        {
            "schema_version": "execution_results_v1",
            "correlation_id": "COR-20260728-1-EURUSD-ORPHAN",
            "symbol": "EURUSD",
            "cycle_id": 1,
            "result_ok": True,
            "retcode": 10009,
            "fill_price": 1.13680,
            "side": "SELL",
            "volume": 0.01,
        },
    ])

    # LIVE outcome: truth (owner) + journal + risk (exact trade_id match)
    write_jsonl(logs / "trade_truth" / "EURUSD" / "2026-07-28.jsonl", [
        {
            "schema_version": "trade_truth_v3",
            "identity": {
                "trade_id": "pos_81177610",
                "correlation_id": "COR-20260728-1-EURUSD-D6EF",
                "symbol": "EURUSD",
            },
            "execution": {"entry_fill_price": 1.13674},
            "outcome": {"pnl_realised": -0.35, "net_profit": -0.35},
            "exit": {"exit_reason": "stop_loss"},
        },
    ])
    write_jsonl(logs / "trade_journal" / "2026-07-28.jsonl", [
        {
            "schema_version": "trade_journal_v1",
            "trade_id": "pos_81177610",
            "symbol": "EURUSD",
            # conflicting pnl copy: trade_truth owns outcome facts
            "realised_pnl": -99.0,
            "net_pnl": -99.0,
            "initial_sl": 1.13706,
            "initial_tp": 1.13610,
            "trade_horizon": "SCALP",
        },
    ])
    write_jsonl(logs / "risk_deviation" / "EURUSD" / "2026-07-28.jsonl", [
        {
            "schema_version": "risk_deviation_v1",
            "trade_id": "pos_81177610",
            "symbol": "EURUSD",
            "planned_risk_R": -1.0,
            "actual_risk_R": -1.05,
            "risk_deviation": 0.05,
            "risk_classification": "NORMAL",
        },
    ])

    # SHADOW event stream (kept separate from LIVE)
    write_jsonl(logs / "shadow_runtime_v1" / "EURUSD" / "2026-08-26.jsonl", [
        {
            "event_type": "PLAN",
            "schema_version": "shadow_runtime_v1",
            "canonical_opportunity_id": "EURUSD*1787769600*MEAN_REVERSION",
            "shadow_trade_id": "",
            "plan_id": "nplan_3736_EURUSD_1787769600",
            "symbol": "EURUSD",
            "cycle_id": 3736,
            "lifecycle": {"state_log_tail": [{"x": 1}]},
        },
        {
            "event_type": "OPEN",
            "schema_version": "shadow_runtime_v1",
            "canonical_opportunity_id": "EURUSD*1787769600*MEAN_REVERSION",
            "shadow_trade_id": "nshadow_3736_EURUSD_SCALP",
            "plan_id": "nplan_3736_EURUSD_1787769600",
            "symbol": "EURUSD",
            "cycle_id": 3736,
        },
    ])

    # market context: v3 (primary, missing cycle/entity) + fallback source
    write_jsonl(
        logs / "v3_shadow" / "market_context" / "EURUSD" / "2026-07-28.jsonl", [
            {
                "schema_version": "v3_market_context_v1",
                "symbol": "EURUSD",
                "timestamp_utc": 1785205500.0,
                "overall_confidence": 0.8,
                "htf_structure": {"macro_bias": "BEARISH"},
            },
        ])
    write_jsonl(logs / "market_context" / "GBPUSD" / "2026-07-28.jsonl", [
        {
            "schema_version": "market_context_v1",
            "symbol": "GBPUSD",
            "cycle_id": 5,
            "timestamp_utc": 1785205500.0,
            "regime": "RANGING",
        },
    ])

    return logs


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

def test_structure_and_basic_projection(tmp_path, logs_tree):
    research = tmp_path / "research_data"
    summary = Projector(logs_root=logs_tree, research_root=research).run()

    assert (research / "live" / "observation" / "EURUSD").is_dir()
    assert (research / "live" / "opportunity" / "EURUSD").is_dir()
    assert (research / "live" / "decision" / "EURUSD").is_dir()
    assert (research / "live" / "execution" / "EURUSD").is_dir()
    assert (research / "live" / "outcome" / "EURUSD").is_dir()
    assert (research / "shadow" / "plan" / "EURUSD").is_dir()
    assert (research / "market_context" / "EURUSD").is_dir()
    assert (research / "manifest" / "schema.json").is_file()

    obs = read_area(research, "live/observation")["EURUSD"]
    assert len(obs) == 1
    rec = obs[0]
    assert rec["research_schema"] == "research_observation_v1"
    assert rec["research_lineage"]["observation_id"] == "EURUSD_1_1785205500"
    assert rec["research_lineage"]["entity_id"] == "EURUSD_1785205500"
    assert rec["research_source"]["path"].startswith("logs/")
    assert rec["research_source"]["fingerprint"].startswith("sha256:")
    assert summary["anomalies"] == []


def test_logs_are_read_only(tmp_path, logs_tree):
    research = tmp_path / "research_data"
    before = tree_hash(logs_tree)
    projector = Projector(logs_root=logs_tree, research_root=research)
    projector.run()
    projector.run()
    assert tree_hash(logs_tree) == before


def test_idempotency_second_run_projects_nothing(tmp_path, logs_tree):
    research = tmp_path / "research_data"
    projector = Projector(logs_root=logs_tree, research_root=research)
    first = projector.run()
    second = projector.run()

    projected_first = sum(b["records_projected"] for b in first["areas"].values())
    assert projected_first > 0
    # second run over unchanged bytes: nothing read, nothing written
    projected_second = sum(b["records_projected"] for b in second["areas"].values())
    skipped = sum(b["records_skipped_existing"] for b in second["areas"].values())
    assert projected_second == 0
    assert skipped == 0

    areas_before = {a: read_area(research, a) for a in
                    ["live/observation", "live/decision", "shadow/plan"]}
    Projector(logs_root=logs_tree, research_root=research).run()
    areas_after = {a: read_area(research, a) for a in
                   ["live/observation", "live/decision", "shadow/plan"]}
    assert areas_before == areas_after


def test_appended_source_bytes_are_projected(tmp_path, logs_tree):
    research = tmp_path / "research_data"
    projector = Projector(logs_root=logs_tree, research_root=research)
    projector.run()

    extra = {
        "schema_version": "strategy_observation_v1",
        "observation_id": "EURUSD_2_1785205800",
        "timestamp_utc": 1785205800.0,
        "symbol": "EURUSD",
        "cycle_id": 2,
        "entity_id": "EURUSD_1785205800",
    }
    with open(logs_tree / "strategy_observations" / "EURUSD" /
              "2026-07-28.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(extra) + "\n")

    second = projector.run()
    obs_projected = second["areas"]["live/observation"]["records_projected"]
    assert obs_projected == 1
    obs = read_area(research, "live/observation")["EURUSD"]
    ids = {r["research_lineage"]["observation_id"] for r in obs}
    assert ids == {"EURUSD_1_1785205500", "EURUSD_2_1785205800"}


def test_lineage_preserved_and_never_fabricated(tmp_path, logs_tree):
    research = tmp_path / "research_data"
    Projector(logs_root=logs_tree, research_root=research).run()

    # opportunity: canonical_opportunity_id absent in source -> stays absent
    opp = read_area(research, "live/opportunity")["EURUSD"][0]
    assert "canonical_opportunity_id" not in opp["research_lineage"]
    assert opp["research_lineage"]["opportunity_id"] == \
        "EURUSD_1785255900_EVENING_STAR"

    # shadow plan row: shadow_trade_id empty in source -> stays absent
    plan = read_area(research, "shadow/plan")["EURUSD"][0]
    assert "shadow_trade_id" not in plan["research_lineage"]
    assert plan["research_lineage"]["plan_id"] == "nplan_3736_EURUSD_1787769600"
    assert plan["research_lineage"]["canonical_opportunity_id"] == \
        "EURUSD*1787769600*MEAN_REVERSION"
    assert plan["research_lineage"]["canonical_root_valid"] is True

    # decision joined on exact entity_id -> resolved
    dec = read_area(research, "live/decision")["EURUSD"][0]
    assert dec["research_lineage"]["link_status"] == "resolved"
    assert dec["research_lineage"]["entity_id"] == "EURUSD_1785255900"
    assert dec["research_lineage"]["decision_id"] == \
        "deadbeefdeadbeefdeadbeefdeadbeef"
    # trace correlation_id is a DIFFERENT id space -> preserved as trace_id
    assert dec["research_lineage"]["trace_id"] == "v10_EURUSD_1785255900_1"
    assert dec["research_lineage"]["correlation_id"] == \
        "COR-20260728-1-EURUSD-D6EF"

    # orphan execution result: no context match -> unresolved, never faked
    execs = read_area(research, "live/execution")["EURUSD"]
    orphan = [r for r in execs
              if r["research_lineage"]["correlation_id"] ==
              "COR-20260728-1-EURUSD-ORPHAN"]
    assert len(orphan) == 1
    assert orphan[0]["research_lineage"]["link_status"] == "unresolved"
    assert orphan[0]["research_source"]["owner"] == "execution_results_only"


def test_field_level_reconciliation(tmp_path, logs_tree):
    research = tmp_path / "research_data"
    Projector(logs_root=logs_tree, research_root=research).run()

    dec = read_area(research, "live/decision")["EURUSD"][0]
    # ledger `decision`/`reason` are authoritative; trace copies dropped
    assert dec["decision"] == "EXECUTE"
    assert dec["reason"] == "pattern_execute"
    assert "action" not in dec
    assert dec["stages_reached_count"] == 3
    assert dec["stages_passed_count"] == 2
    assert "stages_reached" not in dec
    # EV block contributed by assessments (owner of that block)
    assert dec["ev"] == 0.25
    assert dec["weights_used"] == "global_v3"

    out = read_area(research, "live/outcome")["EURUSD"][0]
    # trade_truth owns outcome facts; journal's conflicting pnl is rejected
    assert out["outcome"]["pnl_realised"] == -0.35
    assert "realised_pnl" not in out
    # journal keep-list fields are contributed
    assert out["initial_sl"] == 1.13706
    assert out["trade_horizon"] == "SCALP"
    assert out["planned_risk_R"] == -1.0
    assert out["risk_classification"] == "NORMAL"


def test_outcome_boundary_on_execution_records(tmp_path, logs_tree):
    research = tmp_path / "research_data"
    Projector(logs_root=logs_tree, research_root=research).run()

    execs = read_area(research, "live/execution")["EURUSD"]
    linked = [r for r in execs
              if r["research_lineage"]["correlation_id"] ==
              "COR-20260728-1-EURUSD-D6EF"]
    assert len(linked) == 1
    rec = linked[0]
    for forbidden in ("pnl", "pnl_realised", "net_profit", "exit_price",
                      "close_reason", "duration_seconds"):
        assert forbidden not in rec
    # fill fields from execution_results are contributed
    assert rec["fill_price"] == 1.13674
    assert rec["retcode"] == 10009


def test_live_and_shadow_stay_separate(tmp_path, logs_tree):
    research = tmp_path / "research_data"
    Projector(logs_root=logs_tree, research_root=research).run()

    live_records = []
    for area in ["live/observation", "live/opportunity", "live/decision",
                 "live/execution", "live/outcome"]:
        for recs in read_area(research, area).values():
            live_records.extend(recs)
    shadow_records = []
    for area in ["shadow/plan", "shadow/open", "shadow/progress",
                 "shadow/close"]:
        for recs in read_area(research, area).values():
            shadow_records.extend(recs)

    assert len(shadow_records) == 2  # PLAN + OPEN
    assert all(r["research_area"].startswith("shadow/") for r in shadow_records)
    assert all(r["research_area"].startswith("live/") for r in live_records)
    assert all(r["research_schema"].startswith("research_shadow_")
               for r in shadow_records)
    # SHADOW_DROPS applied
    plan = [r for r in shadow_records
            if r["research_area"] == "shadow/plan"][0]
    assert "state_log_tail" not in plan.get("lifecycle", {})


def test_market_context_same_bar_reconciliation(tmp_path, logs_tree):
    research = tmp_path / "research_data"
    Projector(logs_root=logs_tree, research_root=research).run()

    eurusd = read_area(research, "market_context")["EURUSD"][0]
    # v3 row had no cycle_id/entity_id; reconciled ONLY via exact same-bar obs
    assert eurusd["cycle_id"] == 1
    assert eurusd["entity_id"] == "EURUSD_1785205500"
    kinds = [e["kind"] for e in eurusd["research_reconciliation"]]
    assert kinds.count("same_bar_reconciliation") == 2

    gbpusd = read_area(research, "market_context")["GBPUSD"][0]
    # fallback source row keeps its own verbatim cycle_id, no fabricated ids
    assert gbpusd["cycle_id"] == 5
    assert "entity_id" not in gbpusd
    assert "research_reconciliation" not in gbpusd


def test_manifest_answers_provenance_questions(tmp_path, logs_tree):
    research = tmp_path / "research_data"
    Projector(logs_root=logs_tree, research_root=research).run()

    schema_doc = json.loads(
        (research / "manifest" / "schema.json").read_text(encoding="utf-8"))
    assert schema_doc["schemas"]["research_decision_v1"]["sources"][0][
        "dataset"] == "decision_ledger"
    assert "canonical_root" in schema_doc["lineage_rules"]

    source_map = json.loads(
        (research / "manifest" / "source_map.json").read_text(encoding="utf-8"))
    assert source_map["research_areas"]["shadow/plan"]["schema"] == \
        "research_shadow_plan_v1"

    ownership = json.loads(
        (research / "manifest" / "field_ownership.json").read_text(
            encoding="utf-8"))
    assert "trade_truth_role" in ownership["rules"]

    state = json.loads(
        (research / "manifest" / "projection_state.json").read_text(
            encoding="utf-8"))
    assert state["run_count"] == 1
    assert any(k.startswith("strategy_observations")
               for k in state["cursors"])

    # every projected record traces back to a source file that exists
    dec = read_area(research, "live/decision")["EURUSD"][0]
    src_rel = dec["research_source"]["path"]
    assert (logs_tree.parent / src_rel).is_file()
    # and the fingerprint matches the source record's content
    src_lines = read_jsonl(logs_tree.parent / src_rel)
    fingerprints = {
        "sha256:" + hashlib.sha256(
            json.dumps(r, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False).encode()).hexdigest()
        for r in src_lines
    }
    assert dec["research_source"]["fingerprint"] in fingerprints


def test_no_backfill_mode_skips_history(tmp_path, logs_tree):
    research = tmp_path / "research_data"
    summary = Projector(logs_root=logs_tree, research_root=research,
                        backfill=False).run()
    assert sum(b["records_projected"] for b in summary["areas"].values()) == 0
    # cursor set to EOF: a later no-backfill run still sees nothing new
    Projector(logs_root=logs_tree, research_root=research,
              backfill=False).run()
    obs = read_area(research, "live/observation")
    assert obs == {}



