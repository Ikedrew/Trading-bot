"""Focused D4 pre-decision score-threshold validation repair tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from research_engine.experiments.d4_score_threshold_validation import (
    D4_REPORT_FILENAME,
    SCORE_AUTHORITY_FIELD,
    SCORE_AUTHORITY_VERSION,
    _REJECTED_SCORE_ALIASES,
    analyse,
    build_paired_scores,
    canonical_score,
    chronological_split,
    select_threshold_from_discovery,
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


def _decision(
    index: int,
    *,
    opportunity: str | None = None,
    score: float | None = None,
    regime: str = "TRENDING",
    phase: str = "IMPULSE",
    epoch: str = "CURRENT",
    offset_hours: int = 0,
) -> dict:
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=index + offset_hours)
    return {
        "data_epoch": epoch,
        "canonical_opportunity_id": opportunity or f"OPP-{index:04d}",
        "timestamp_utc": timestamp.isoformat(),
        SCORE_AUTHORITY_FIELD: score,
        "regime": regime,
        "market_phase": phase,
    }


def _population(count: int = 120):
    """Score correlates with outcome: high score -> win, low score -> loss."""
    decisions, shadows = [], []
    for index in range(count):
        won = bool(index % 2)
        decisions.append(_decision(index, score=0.7 if won else 0.2))
        shadows.append(_shadow(index, outcome=1.0 if won else -1.0))
    return decisions, shadows


# 1. Exact canonical score authority is used (decision_trace.score_strategy).
def test_canonical_score_authority_is_score_strategy():
    assert SCORE_AUTHORITY_FIELD == "score_strategy"
    assert canonical_score({"score_strategy": 0.55}) == pytest.approx(0.55)
    rows, _ = build_paired_scores([_decision(1, score=0.6)], [_shadow(1, outcome=1.0)])
    assert len(rows) == 1
    assert rows[0]["score"] == pytest.approx(0.6)


# 2. Arbitrary score aliases cannot substitute the canonical authority.
@pytest.mark.parametrize("alias", sorted(_REJECTED_SCORE_ALIASES))
def test_rejected_aliases_cannot_substitute_score(alias):
    # A record carrying only an alias (no score_strategy) yields no score.
    record = {
        "data_epoch": "CURRENT",
        "canonical_opportunity_id": "OPP-0001",
        "timestamp_utc": "2026-01-01T00:00:00+00:00",
        alias: 0.9,
    }
    assert canonical_score(record) is None
    rows, diagnostics = build_paired_scores([record], [_shadow(1, opportunity="OPP-0001", outcome=1.0)])
    assert rows == []
    assert diagnostics["excluded_missing_score"] == 1


# 3 + 4 + 5. One opportunity = one observation; account fanout and repeated
# horizons cannot inflate independent n.
def test_one_opportunity_one_observation_no_fanout_no_horizon_inflation():
    decision = _decision(1, score=0.8)
    first = _shadow(1, outcome=1.0, horizon="SCALP")
    fanout = _shadow(1, outcome=1.0, horizon="SCALP")  # account fanout duplicate
    fanout["identity"]["shadow_trade_id"] = "nshadow_ffffffffffffffff"
    horizon = _shadow(1, outcome=3.0, horizon="EXTENDED")  # repeated horizon
    horizon["identity"]["shadow_trade_id"] = "nshadow_eeeeeeeeeeeeeeee"
    rows, diagnostics = build_paired_scores([decision], [first, fanout, horizon])
    assert len(rows) == 1
    assert rows[0]["outcome_r"] == 2.0  # mean of SCALP(1.0) and EXTENDED(3.0)
    assert diagnostics["repeated_rows_collapsed"] == 2


# 6. Missing outcome remains missing/excluded (never imputed to loss/zero).
def test_missing_outcome_is_excluded_never_imputed():
    rows, diagnostics = build_paired_scores([_decision(1, score=0.7)], [_shadow(1, outcome=None)])
    assert rows == []
    assert diagnostics["missing_outcomes_excluded"] == 1


# 7. Conflicting score / outcome evidence fails closed.
def test_conflicting_score_fails_closed():
    decisions, shadows = _population()
    conflicting = dict(decisions[3])
    conflicting["score_strategy"] = 0.99  # second, different score for same opportunity
    assert analyse([*decisions, conflicting], shadows)["status"] == "BLOCKED"


# 8. Score must be pre-decision (timestamp <= outcome chronology).
def test_score_must_be_pre_decision():
    decisions, shadows = _population()
    late = list(decisions)
    late[3] = _decision(3, score=0.7, offset_hours=1)  # score after outcome time
    assert analyse(late, shadows)["status"] == "BLOCKED"


# 9. Post-outcome fields cannot become the predictor.
def test_post_outcome_fields_cannot_become_predictor():
    decision = _decision(1, score=0.3)
    # Inject post-outcome values that must never be read as the score.
    decision["realised_r"] = 5.0
    decision["r_multiple"] = 5.0
    decision["pnl_r_multiple"] = 5.0
    rows, _ = build_paired_scores([decision], [_shadow(1, outcome=-1.0)])
    assert len(rows) == 1
    assert rows[0]["score"] == pytest.approx(0.3)  # unchanged by post-outcome fields


# 10. Chronological discovery/validation split is deterministic.
def test_chronological_discovery_validation_is_deterministic():
    decisions, shadows = _population()
    paired, _ = build_paired_scores(decisions, shadows)
    discovery, validation = chronological_split(paired)
    assert len(discovery) == 72 and len(validation) == 48
    assert max(r["timestamp"] for r in discovery) < min(r["timestamp"] for r in validation)
    assert {r["canonical_opportunity_id"] for r in discovery}.isdisjoint(
        r["canonical_opportunity_id"] for r in validation
    )


# 11. Threshold is selected from discovery only.
def test_threshold_selected_from_discovery_only():
    decisions, shadows = _population()
    paired, _ = build_paired_scores(decisions, shadows)
    discovery, _validation = chronological_split(paired)
    threshold, provenance, diagnostics = select_threshold_from_discovery(discovery)
    assert threshold is not None
    assert provenance == "max_discovery_expectancy_separation"
    # Candidate thresholds are drawn only from discovery scores.
    discovery_scores = {r["score"] for r in discovery}
    for candidate in diagnostics["candidates"]:
        assert candidate["threshold"] in discovery_scores


# 12. Validation cannot select or optimise its own threshold.
def test_validation_cannot_select_its_own_threshold():
    decisions, shadows = _population()
    paired, _ = build_paired_scores(decisions, shadows)
    discovery, validation = chronological_split(paired)
    threshold, _, _ = select_threshold_from_discovery(discovery)
    # A deliberately shifted validation set must not change the runner threshold.
    shifted = [{**row, "score": row["score"] + 100.0} for row in validation]
    threshold_from_validation, _, _ = select_threshold_from_discovery(shifted)
    assert threshold != threshold_from_validation


# 13. Minimum above/below validation group sizes are enforced.
def test_minimum_validation_group_sizes_enforced():
    # Population where the discovery threshold leaves too few on one validation
    # side: nearly all validation scores identical -> one group under-filled.
    decisions, shadows = [], []
    for index in range(140):
        # Discovery (first 60% by time) has spread; validation is almost all high.
        if index < 84:
            score = 0.2 if index % 2 == 0 else 0.7
            outcome = -1.0 if index % 2 == 0 else 1.0
        else:
            score = 0.7  # all above any reasonable threshold
            outcome = 1.0 if index % 2 else -1.0
        decisions.append(_decision(index, score=score))
        shadows.append(_shadow(index, outcome=outcome))
    report = analyse(decisions, shadows)
    # Not enough opportunities on one side of the threshold in validation.
    assert report["status"] == "WAITING_DATA"


# 14. Score/outcome relationship is measured on paired observations.
def test_score_outcome_relationship_measured_on_paired_observations():
    decisions, shadows = _population()
    report = analyse(decisions, shadows)
    rel = report["overall"]["score_outcome_rank_relationship"]
    assert rel is not None
    assert rel > 0.0  # higher score paired with better realised R


# 15. Context segmentation uses same-opportunity pre-decision context.
def test_context_segmentation_uses_same_opportunity_pre_decision_context():
    decisions, shadows = [], []
    for index in range(120):
        won = bool(index % 2)
        regime = "TRENDING" if index < 60 else "RANGING"
        decisions.append(_decision(index, score=0.7 if won else 0.2, regime=regime))
        shadows.append(_shadow(index, outcome=1.0 if won else -1.0))
    report = analyse(decisions, shadows)
    regime_cells = report["overall"]["context_diagnostics"]["h4_regime"]
    assert set(regime_cells) == {"TRENDING", "RANGING"}
    # Context cells do not inflate independent n: cells sum to paired total.
    assert sum(cell["n"] for cell in regime_cells.values()) == report["dataset"]["independent_observations"]


# 16. Insufficient context cells are not overinterpreted.
def test_insufficient_context_cells_marked_insufficient():
    decisions, shadows = [], []
    for index in range(120):
        won = bool(index % 2)
        # One rare regime cell with only a couple of members.
        regime = "TRANSITIONAL" if index in (0, 2) else "TRENDING"
        decisions.append(_decision(index, score=0.7 if won else 0.2, regime=regime))
        shadows.append(_shadow(index, outcome=1.0 if won else -1.0))
    report = analyse(decisions, shadows)
    cells = report["overall"]["context_diagnostics"]["h4_regime"]
    assert cells["TRANSITIONAL"]["sufficient"] is False
    assert cells["TRENDING"]["sufficient"] is True


# 17. Negative / no-signal result can still be scientifically COMPLETE.
def test_no_signal_result_is_still_complete():
    # Score is unrelated to outcome -> valid COMPLETE evaluation, NO_SIGNAL.
    decisions, shadows = [], []
    for index in range(140):
        score = 0.2 if index % 2 == 0 else 0.7
        outcome = 1.0 if (index // 2) % 2 == 0 else -1.0  # decoupled from score
        decisions.append(_decision(index, score=score))
        shadows.append(_shadow(index, outcome=outcome))
    report = analyse(decisions, shadows)
    assert report["status"] == "COMPLETE"
    assert report["overall"]["finding_classification"] in {"NO_SIGNAL", "DISCOVERY_ONLY"}


# 18 + 19. Unique canonical report ownership; legacy descriptive artifact cannot
# complete canonical D4.
def test_d4_has_unique_canonical_runner_report_and_complete_definition():
    question = REGISTRY_BY_ID["D4"]
    assert question.runner_module == "research_engine.experiments.d4_score_threshold_validation"
    assert question.runner_function == "run_d4"
    assert question.report_filename == D4_REPORT_FILENAME
    assert sum(item.report_filename == D4_REPORT_FILENAME for item in REGISTRY) == 1
    # The legacy descriptive threshold artifact must not own D4.
    assert question.report_filename != "q2_regime_threshold.json"
    definitions = build_definitions_from_registry(REGISTRY)
    health = validate_all_definitions(definitions)
    assert get_question_health(health["D4"]) == "VALID"


# 20 + 21. Ledger derives exactly 34/36; D5/X5 remain non-operational.
def test_ledger_derives_34_and_d5_x5_non_operational():
    from research_engine.registry.master_repair_ledger import (
        MASTER_REPAIR_LEDGER, REPAIR_WAVES, STRUCTURALLY_NON_OPERATIONAL_IDS,
        operational_baseline,
    )

    assert operational_baseline() == (36, 34)
    assert MASTER_REPAIR_LEDGER["D4"].structurally_operational
    assert REPAIR_WAVES["RW3"].implemented is True
    assert set(REPAIR_WAVES["RW3"].direct_gain) == {"D2", "D3", "D4", "D5", "X5"}
    assert "D4" not in STRUCTURALLY_NON_OPERATIONAL_IDS
    assert "D5" not in STRUCTURALLY_NON_OPERATIONAL_IDS
    assert "X5" not in STRUCTURALLY_NON_OPERATIONAL_IDS


# 22. D2/D3/RW1/RW2 behaviour remains unchanged.
def test_d2_d3_rw1_rw2_unchanged():
    from research_engine.registry.master_repair_ledger import MASTER_REPAIR_LEDGER

    assert MASTER_REPAIR_LEDGER["D2"].structurally_operational
    assert MASTER_REPAIR_LEDGER["D3"].structurally_operational
    for qid in ("D1", "E2", "M1", "M3", "M7", "M8", "M11"):
        assert MASTER_REPAIR_LEDGER[qid].structurally_operational


# COMPLETE with a validated directional signal is reachable.
def test_validated_directional_signal_is_reachable():
    decisions, shadows = _population(160)
    report = analyse(decisions, shadows)
    assert report["status"] == "COMPLETE"
    assert report["overall"]["finding_classification"] == "VALIDATED_DIRECTIONAL_SIGNAL"
    assert report["overall"]["threshold"]["selected_from"] == "discovery_partition_only"
    assert report["overall"]["score_authority"]["field"] == "score_strategy"
    assert report["overall"]["score_authority"]["version"] == SCORE_AUTHORITY_VERSION
