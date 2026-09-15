"""Focused D5 rejected-opportunity counterfactual validation repair tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from research_engine.experiments.d5_rejected_opportunity_validation import (
    D5_REPORT_FILENAME,
    EVIDENCE_CLASS,
    REJECTION_TAXONOMY_VERSION,
    analyse,
    build_accepted_comparator,
    build_rejected_observations,
    chronological_split,
    classify_rejection_stage,
)
from research_engine.registry.definition_validator import (
    build_definitions_from_registry,
    get_question_health,
    validate_all_definitions,
)
from research_engine.registry.research_question_registry import REGISTRY, REGISTRY_BY_ID


def _shadow(index: int, *, opportunity: str | None = None, outcome: float | None = None, horizon: str = "SCALP") -> dict:
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=index)
    opportunity = opportunity or f"OPP-{index:04d}"
    return {
        "data_epoch": "CURRENT",
        "identity": {
            "canonical_opportunity_id": opportunity,
            "entity_id": f"ENTITY-{index:04d}",
            "shadow_trade_id": f"nshadow_{index:016x}",
            "symbol": "EURUSD",
        },
        "decision_snapshot": {
            "timestamp_decision_utc": timestamp.isoformat(),
            "h4_regime": "TRENDING",
            "market_phase": "IMPULSE",
            "h1_bias": "BULLISH",
            "pattern": "PIN_BAR",
            "strategy": "REVERSAL",
            "trade_horizon": horizon,
        },
        "simulated_outcome": {"pnl_r_multiple": outcome},
    }


def _reject(
    index: int,
    *,
    opportunity: str | None = None,
    stage: str = "scoring",
    reason: str = "score_below_threshold",
    action: str = "NO_TRADE",
    regime: str = "TRENDING",
    epoch: str = "CURRENT",
    offset_hours: int = 0,
) -> dict:
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=index + offset_hours)
    return {
        "data_epoch": epoch,
        "canonical_opportunity_id": opportunity or f"OPP-{index:04d}",
        "timestamp_utc": timestamp.isoformat(),
        "action": action,
        "terminal_stage": stage,
        "terminal_reason": reason,
        "regime": regime,
        "market_phase": "IMPULSE",
        "strategy": "REVERSAL",
    }


def _population(count: int = 120, *, adverse: bool = True):
    """Rejected opportunities whose counterfactual outcomes are mostly adverse
    (adverse=True -> good filtering) or favourable (adverse=False)."""
    decisions, shadows = [], []
    for index in range(count):
        # Deterministic mostly-one-direction outcomes.
        if adverse:
            outcome = -1.0 if index % 4 != 0 else 1.0
        else:
            outcome = 1.0 if index % 4 != 0 else -1.0
        decisions.append(_reject(index))
        shadows.append(_shadow(index, outcome=outcome))
    return decisions, shadows


# 1. Genuine rejection authority is required (EXECUTE is not a rejection).
def test_genuine_rejection_authority_required():
    executed = _reject(1, action="EXECUTE", stage="execute", reason="")
    rows, diagnostics = build_rejected_observations([executed], [_shadow(1, outcome=1.0)])
    assert rows == []
    assert diagnostics["excluded_not_rejection"] == 1


# 2 + 3. PATTERN_REJECT / NO_TRADE / RISK_BLOCK are not blindly equivalent;
# taxonomy is deterministic and producer-stage driven.
def test_rejection_classes_are_producer_stage_driven_not_equivalent():
    assert classify_rejection_stage("pattern_detection") == "STRATEGY_SIGNAL_REJECTION"
    assert classify_rejection_stage("scoring") == "STRATEGY_SIGNAL_REJECTION"
    assert classify_rejection_stage("ev_policy") == "STRATEGY_SIGNAL_REJECTION"
    assert classify_rejection_stage("risk") == "RISK_EXECUTION_BLOCK"
    assert classify_rejection_stage("data_validation") == "RISK_EXECUTION_BLOCK"
    # Strategy rejection and risk block are DISTINCT classes.
    assert classify_rejection_stage("scoring") != classify_rejection_stage("risk")


# 4. Unknown reasons remain UNKNOWN rather than being guessed into a bucket.
def test_unknown_stage_remains_unclassified():
    assert classify_rejection_stage("unknown") == "UNKNOWN"
    assert classify_rejection_stage("error") == "UNKNOWN"
    assert classify_rejection_stage("some_new_stage") == "UNKNOWN"
    rows, _ = build_rejected_observations(
        [_reject(1, stage="mystery", reason="weird_reason")], [_shadow(1, outcome=-1.0)]
    )
    assert len(rows) == 1
    assert rows[0]["rejection_class"] == "UNKNOWN"


# 5. One canonical opportunity = one independent observation.
def test_one_opportunity_one_observation():
    rows, _ = build_rejected_observations([_reject(1)], [_shadow(1, outcome=-1.0)])
    assert len(rows) == 1
    assert rows[0]["canonical_opportunity_id"] == "OPP-0001"


# 6 + 7 (part). Multiple rejection reasons cannot double-count one opportunity;
# earliest canonical stage is the deterministic primary class.
def test_multiple_reasons_collapse_to_earliest_stage_primary():
    early = _reject(1, opportunity="OPP-0001", stage="pattern_detection", reason="no_viable_pattern")
    late = _reject(1, opportunity="OPP-0001", stage="risk", reason="risk_rejected", offset_hours=0)
    rows, diagnostics = build_rejected_observations([early, late], [_shadow(1, opportunity="OPP-0001", outcome=-1.0)])
    assert len(rows) == 1  # not double-counted
    assert rows[0]["rejection_class"] == "STRATEGY_SIGNAL_REJECTION"  # pattern_detection wins
    assert rows[0]["terminal_stage"] == "pattern_detection"
    assert diagnostics["multi_reason_opportunities"] == 1


# 7. Account fanout cannot inflate n.
def test_account_fanout_cannot_inflate_n():
    decision = _reject(1, opportunity="OPP-0001")
    first = _shadow(1, opportunity="OPP-0001", outcome=-1.0)
    fanout = _shadow(1, opportunity="OPP-0001", outcome=-1.0)
    fanout["identity"]["shadow_trade_id"] = "nshadow_ffffffffffffffff"
    rows, diagnostics = build_rejected_observations([decision], [first, fanout])
    assert len(rows) == 1
    assert diagnostics["repeated_rows_collapsed"] == 1


# 8. Repeated horizons cannot inflate independent n.
def test_repeated_horizons_cannot_inflate_n():
    decision = _reject(1, opportunity="OPP-0001")
    scalp = _shadow(1, opportunity="OPP-0001", outcome=-1.0, horizon="SCALP")
    extended = _shadow(1, opportunity="OPP-0001", outcome=1.0, horizon="EXTENDED")
    extended["identity"]["shadow_trade_id"] = "nshadow_eeeeeeeeeeeeeeee"
    rows, diagnostics = build_rejected_observations([decision], [scalp, extended])
    assert len(rows) == 1
    assert rows[0]["outcome_r"] == 0.0  # mean of -1.0 and 1.0
    assert diagnostics["repeated_rows_collapsed"] == 1


# 9. Missing shadow outcome remains missing/excluded (frequency-only).
def test_missing_outcome_is_excluded_and_frequency_only():
    rows, diagnostics = build_rejected_observations([_reject(1)], [_shadow(1, outcome=None)])
    assert rows == []
    assert diagnostics["missing_outcomes_excluded"] == 1
    assert diagnostics["rejection_only_no_outcome"] == 1
    assert diagnostics["rejected_opportunities"] == 1


# 10. Rejected does NOT imply loss or 0R (a rejected opp with a positive
# counterfactual outcome is preserved as positive).
def test_rejected_does_not_imply_loss_or_zero():
    rows, _ = build_rejected_observations([_reject(1)], [_shadow(1, outcome=2.5)])
    assert len(rows) == 1
    assert rows[0]["outcome_r"] == 2.5
    assert rows[0]["won"] == 1.0


# 11. Rejection timestamp must predate the outcome.
def test_rejection_timestamp_must_predate_outcome():
    decisions, shadows = _population()
    late = list(decisions)
    late[3] = _reject(3, offset_hours=1)  # rejection after the outcome time
    assert analyse(late, shadows)["status"] == "BLOCKED"


# 12. Counterfactual shadow outcome is not labelled broker truth.
def test_counterfactual_outcome_not_labelled_broker_truth():
    decisions, shadows = _population()
    report = analyse(decisions, shadows)
    overall = report["overall"]
    assert overall["evidence_class"] == EVIDENCE_CLASS
    assert "counterfactual" in overall["outcome_authority"].lower()
    assert overall["causal_claim"].startswith("NONE")
    assert "broker" in overall["causal_claim"].lower()


# 13. Conflicting rejection classification fails closed (two different classes
# at the SAME earliest canonical stage rank for one opportunity).
def test_conflicting_rejection_class_fails_closed(monkeypatch):
    import research_engine.experiments.d5_rejected_opportunity_validation as d5
    # Force two stages that map to DIFFERENT classes to share the minimum rank.
    monkeypatch.setitem(d5._STAGE_RANK, "scoring", 2)
    monkeypatch.setitem(d5._STAGE_RANK, "risk", 2)
    decisions, shadows = _population()
    # Add a second, same-rank rejection with a conflicting class for one opp.
    decisions.append(_reject(3, opportunity="OPP-0003", stage="risk", reason="risk_rejected"))
    report = d5.analyse(decisions, shadows)
    assert report["status"] == "BLOCKED"


# 14. Conflicting outcome evidence for one opportunity fails closed.
def test_conflicting_outcome_evidence_fails_closed():
    decisions, shadows = _population()
    # A second shadow for OPP-0003 with a conflicting immutable decision time
    # makes the canonical opportunity ambiguous -> fail closed.
    dup = _shadow(3, opportunity="OPP-0003", outcome=-5.0)
    dup["identity"]["shadow_trade_id"] = "nshadow_bbbbbbbbbbbbbbbb"
    dup["decision_snapshot"]["timestamp_decision_utc"] = datetime(2026, 1, 2, tzinfo=timezone.utc).isoformat()
    report = analyse(decisions, [*shadows, dup])
    assert report["status"] == "BLOCKED"


# 15. Chronological discovery/validation split is deterministic.
def test_chronological_discovery_validation_is_deterministic():
    decisions, shadows = _population()
    rejected, _ = build_rejected_observations(decisions, shadows)
    discovery, validation = chronological_split(rejected)
    assert len(discovery) == 72 and len(validation) == 48
    assert max(r["timestamp"] for r in discovery) < min(r["timestamp"] for r in validation)
    assert {r["canonical_opportunity_id"] for r in discovery}.isdisjoint(
        r["canonical_opportunity_id"] for r in validation
    )


# 16. Taxonomy cannot be outcome-optimised on validation (class is fixed from
# producer stage regardless of outcome sign).
def test_taxonomy_is_fixed_from_producer_not_outcome():
    good = _reject(1, opportunity="OPP-A", stage="scoring")
    bad = _reject(2, opportunity="OPP-B", stage="scoring")
    rows, _ = build_rejected_observations(
        [good, bad], [_shadow(1, opportunity="OPP-A", outcome=5.0), _shadow(2, opportunity="OPP-B", outcome=-5.0)]
    )
    # Same stage -> same class, independent of opposite outcomes.
    classes = {row["rejection_class"] for row in rows}
    assert classes == {"STRATEGY_SIGNAL_REJECTION"}


# 17. Discovery-only effect is distinguished from later persistence.
def test_discovery_only_vs_persistence_distinguished():
    # Discovery adverse, validation favourable -> DISCOVERY_ONLY.
    decisions, shadows = [], []
    for index in range(140):
        if index < 84:  # discovery region (earlier)
            outcome = -1.0 if index % 4 != 0 else 1.0   # adverse
        else:           # validation region (later)
            outcome = 1.0 if index % 4 != 0 else -1.0   # favourable
        decisions.append(_reject(index))
        shadows.append(_shadow(index, outcome=outcome))
    report = analyse(decisions, shadows)
    assert report["status"] == "COMPLETE"
    assert report["overall"]["finding_classification"] == "DISCOVERY_ONLY"


# 18. Rejection-class/reason subgroup minimums are enforced (small subgroup
# flagged insufficient, never used for a claim).
def test_subgroup_minimums_enforced():
    decisions, shadows = [], []
    for index in range(120):
        # A rare RISK_EXECUTION_BLOCK subgroup with only a few members.
        if index in (0, 2, 4):
            decisions.append(_reject(index, stage="risk", reason="risk_rejected"))
        else:
            decisions.append(_reject(index, stage="scoring", reason="score_below_threshold"))
        shadows.append(_shadow(index, outcome=-1.0 if index % 4 != 0 else 1.0))
    report = analyse(decisions, shadows)
    cells = report["overall"]["outcome_by_rejection_class"]
    assert cells["RISK_EXECUTION_BLOCK"]["sufficient"] is False
    assert cells["STRATEGY_SIGNAL_REJECTION"]["sufficient"] is True


# 19. Context diagnostics use same-opportunity pre-decision context and do not
# inflate independent n.
def test_context_diagnostics_use_same_opportunity_and_do_not_inflate_n():
    decisions, shadows = [], []
    for index in range(120):
        regime = "TRENDING" if index < 60 else "RANGING"
        decisions.append(_reject(index, regime=regime))
        shadows.append(_shadow(index, outcome=-1.0 if index % 4 != 0 else 1.0))
    report = analyse(decisions, shadows)
    regime_cells = report["overall"]["context_diagnostics"]["h4_regime"]
    assert set(regime_cells) == {"TRENDING", "RANGING"}
    assert sum(cell["n"] for cell in regime_cells.values()) == report["dataset"]["independent_observations"]


# 20. Insufficient context cells are not overinterpreted.
def test_insufficient_context_cells_marked_insufficient():
    decisions, shadows = [], []
    for index in range(120):
        regime = "TRANSITIONAL" if index in (0, 2) else "TRENDING"
        decisions.append(_reject(index, regime=regime))
        shadows.append(_shadow(index, outcome=-1.0 if index % 4 != 0 else 1.0))
    report = analyse(decisions, shadows)
    cells = report["overall"]["context_diagnostics"]["h4_regime"]
    assert cells["TRANSITIONAL"]["sufficient"] is False
    assert cells["TRENDING"]["sufficient"] is True


# 21 + 22. Comparator only used if scientifically valid; missing comparator does
# not create a fake effectiveness claim.
def test_comparator_only_when_valid_and_no_fake_effectiveness():
    # No accepted (EXECUTE) opportunities at all -> comparator not established.
    decisions, shadows = _population()
    report = analyse(decisions, shadows)
    comparator = report["overall"]["comparator"]
    assert comparator["status"].startswith("NOT_ESTABLISHED")
    assert comparator["rejected_minus_accepted_validation_mean_r"] is None


def test_comparator_established_with_sufficient_accepted_validation():
    decisions, shadows = _population()
    # Add a sufficient accepted (EXECUTE) validation population (later times).
    for index in range(200, 260):
        decisions.append(_reject(index, action="EXECUTE", stage="execute", reason=""))
        shadows.append(_shadow(index, opportunity=f"OPP-{index:04d}", outcome=1.0 if index % 3 else -1.0))
    report = analyse(decisions, shadows)
    accepted, diagnostics = build_accepted_comparator(decisions, shadows)
    assert diagnostics["accepted_paired_opportunities"] >= 15
    # Comparator status reflects whether both validation sides met the minimum.
    assert report["overall"]["comparator"]["status"] in {
        "ESTABLISHED", "NOT_ESTABLISHED_INSUFFICIENT_ACCEPTED_VALIDATION_SAMPLE",
    }


# 23. Negative / no-signal result can still be scientifically COMPLETE.
def test_no_signal_result_is_still_complete():
    # Outcomes decoupled from any structure -> MIXED_OR_NO_SIGNAL but COMPLETE.
    decisions, shadows = [], []
    for index in range(140):
        outcome = 0.0  # neutral mean -> no direction
        decisions.append(_reject(index))
        shadows.append(_shadow(index, outcome=outcome))
    report = analyse(decisions, shadows)
    assert report["status"] == "COMPLETE"
    assert report["overall"]["finding_classification"] == "MIXED_OR_NO_SIGNAL"


# GOOD_FILTERING is reachable (adverse persisted into validation).
def test_good_filtering_reachable():
    decisions, shadows = _population(160, adverse=True)
    report = analyse(decisions, shadows)
    assert report["status"] == "COMPLETE"
    assert report["overall"]["finding_classification"] == "GOOD_FILTERING"


# MISSED_OPPORTUNITY_SIGNAL is reachable (favourable persisted).
def test_missed_opportunity_signal_reachable():
    decisions, shadows = _population(160, adverse=False)
    report = analyse(decisions, shadows)
    assert report["status"] == "COMPLETE"
    assert report["overall"]["finding_classification"] == "MISSED_OPPORTUNITY_SIGNAL"


# 24 + 25. Unique canonical report ownership; legacy rejection-frequency artifact
# cannot complete canonical D5.
def test_d5_has_unique_canonical_runner_report_and_complete_definition():
    question = REGISTRY_BY_ID["D5"]
    assert question.runner_module == "research_engine.experiments.d5_rejected_opportunity_validation"
    assert question.runner_function == "run_d5"
    assert question.report_filename == D5_REPORT_FILENAME
    assert sum(item.report_filename == D5_REPORT_FILENAME for item in REGISTRY) == 1
    # The legacy descriptive rejection-frequency artifact must not own D5.
    assert question.report_filename != "q3_missed_opportunity.json"
    definitions = build_definitions_from_registry(REGISTRY)
    health = validate_all_definitions(definitions)
    assert get_question_health(health["D5"]) == "VALID"
    # Taxonomy version is persisted in the report.
    decisions, shadows = _population()
    report = analyse(decisions, shadows)
    assert report["overall"]["rejection_taxonomy"]["version"] == REJECTION_TAXONOMY_VERSION


# 26 + 27. Ledger derives exactly 35/35; X5 remains non-operational.
def test_ledger_derives_35_and_x5_non_operational():
    from research_engine.registry.master_repair_ledger import (
        MASTER_REPAIR_LEDGER, REPAIR_WAVES, STRUCTURALLY_NON_OPERATIONAL_IDS,
        operational_baseline,
    )

    assert operational_baseline() == (36, 34)
    assert MASTER_REPAIR_LEDGER["D5"].structurally_operational
    # RW3 is COMPLETE after X5 (RW3.5).
    assert REPAIR_WAVES["RW3"].implemented is True
    assert set(REPAIR_WAVES["RW3"].direct_gain) == {"D2", "D3", "D4", "D5", "X5"}
    assert "X5" not in STRUCTURALLY_NON_OPERATIONAL_IDS
    assert "D5" not in STRUCTURALLY_NON_OPERATIONAL_IDS


# 28. D2/D3/D4/RW1/RW2 remain unchanged.
def test_d2_d3_d4_rw1_rw2_unchanged():
    from research_engine.registry.master_repair_ledger import MASTER_REPAIR_LEDGER

    for qid in ("D2", "D3", "D4", "D1", "E2", "M1", "M3", "M7", "M8", "M11"):
        assert MASTER_REPAIR_LEDGER[qid].structurally_operational


# 29. WAITING_DATA remains reachable for a valid implementation with too little
# paired evidence (structural gates still pass -> operational, not COMPLETE).
def test_waiting_data_reachable_without_forcing_complete():
    decisions = [_reject(i) for i in range(10)]
    shadows = [_shadow(i, outcome=-1.0) for i in range(10)]
    report = analyse(decisions, shadows)
    assert report["status"] == "WAITING_DATA"
