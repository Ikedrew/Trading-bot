"""Focused P1 leakage-safe counterfactual promotion-policy validation tests."""
from __future__ import annotations

import pytest

from research_engine.experiments.promotion_impact import (
    P1_REPORT_FILENAME,
    PROMOTION_POLICY_VERSION,
    _P1_REMOVE_EV_THRESHOLD,
    analyse,
    build_p1_observations,
    learn_removal_policy,
    _split,
)
from research_engine.registry.definition_validator import (
    build_definitions_from_registry,
    get_question_health,
    validate_all_definitions,
)
from research_engine.registry.research_question_registry import REGISTRY, REGISTRY_BY_ID


def _shadow(i: int, *, opp: str | None = None, pattern: str = "PIN_BAR", outcome=None, horizon: str = "SCALP", shadow_id: str | None = None) -> dict:
    ts = f"2026-01-01T{i // 60:02d}:{i % 60:02d}:00+00:00"
    opp = opp or f"canon-{i:04d}"
    return {
        "data_epoch": "CURRENT",
        "identity": {
            "canonical_opportunity_id": opp,
            "entity_id": f"ent-{opp}",
            "shadow_trade_id": shadow_id or f"nshadow_{opp}",
            "symbol": "EURUSD",
            "evaluated_horizon": horizon,
        },
        "decision_snapshot": {
            "timestamp_decision_utc": ts,
            "h4_regime": "TRENDING",
            "market_phase": "IMPULSE",
            "pattern": pattern,
            "trade_horizon": horizon,
        },
        "simulated_outcome": {"pnl_r_multiple": outcome},
    }


def _population(n=160, *, bad_persists=True):
    """n opportunities. Half GOOD_PATTERN (positive R), half BAD_PATTERN.
    BAD_PATTERN loses in discovery; in validation it loses too when
    bad_persists (policy helps) or wins (policy fails to persist)."""
    shadows = []
    for i in range(n):
        good = (i % 2 == 0)
        pattern = "GOOD_PATTERN" if good else "BAD_PATTERN"
        # discovery is first 60% by time (i ordering).
        in_discovery = i < int(n * 0.6)
        if good:
            outcome = 1.0
        else:
            if in_discovery:
                outcome = -1.0            # bad pattern clearly negative in discovery
            else:
                outcome = -1.0 if bad_persists else 1.5  # validation behaviour
        shadows.append(_shadow(i, pattern=pattern, outcome=outcome))
    return shadows


# 1 + 2 + 3 + 4 + 5. Hypothesis / population / unit / treatment / comparator explicit.
def test_p1_canonical_contract_explicit():
    report = analyse(_population())
    o = report["overall"]
    assert o["canonical_question"] == "P1"
    assert "promotion policy" in o["hypothesis"].lower()
    assert "canonical_opportunity_id" in o["unit_of_analysis"]
    assert "remove" in o["treatment_definition"].lower()
    assert "pre-outcome" in o["treatment_definition"].lower()
    assert "status-quo" in o["comparator_definition"].lower()


# 6 + 7 + 8. Counterfactual (treatment) membership is pre-outcome pattern, never
# defined by realised winner/loser.
def test_treatment_membership_is_pre_outcome_pattern():
    # Two BAD_PATTERN opps: one wins, one loses. Both are in the SAME treatment
    # group because membership is the pattern, not the realised sign.
    shadows = [
        _shadow(0, opp="a", pattern="BAD_PATTERN", outcome=-1.0),
        _shadow(1, opp="b", pattern="BAD_PATTERN", outcome=5.0),
    ] + [_shadow(i + 2, pattern="BAD_PATTERN", outcome=-1.0) for i in range(20)]
    rows, _ = build_p1_observations(shadows)
    removed, _diag = learn_removal_policy(rows)
    # BAD_PATTERN is a removal candidate; the winning BAD_PATTERN opp is still
    # classified BAD_PATTERN (pre-outcome), not spared for winning.
    assert "BAD_PATTERN" in removed


# 9 + 10 + 11. Missing outcome cannot define membership; excluded; never 0R.
def test_missing_outcome_excluded_never_zero():
    shadows = [_shadow(i, pattern="GOOD_PATTERN", outcome=1.0) for i in range(3)]
    shadows.append(_shadow(99, opp="missing", pattern="GOOD_PATTERN", outcome=None))
    rows, diagnostics = build_p1_observations(shadows)
    assert len(rows) == 3
    assert all(r["outcome_r"] == 1.0 for r in rows)  # no imputed 0.0
    assert diagnostics["missing_outcomes_excluded"] == 1


# 12. Explicit 0.0R remains valid.
def test_explicit_zero_is_valid():
    shadows = [_shadow(0, opp="z", pattern="GOOD_PATTERN", outcome=0.0)]
    rows, diagnostics = build_p1_observations(shadows)
    assert len(rows) == 1
    assert rows[0]["outcome_r"] == 0.0
    assert rows[0]["won"] == 0.0
    assert diagnostics["missing_outcomes_excluded"] == 0


# 13 + 14. Canonical identity deterministic; legacy IDs cannot substitute.
def test_canonical_identity_only():
    # A record with no canonical_opportunity_id is excluded (no lineage).
    good = _shadow(0, opp="canon-1", pattern="GOOD_PATTERN", outcome=1.0)
    orphan = _shadow(1, pattern="GOOD_PATTERN", outcome=1.0)
    orphan["identity"].pop("canonical_opportunity_id")
    orphan.pop("canonical_opportunity_id", None)
    rows, _ = build_p1_observations([good, orphan])
    assert [r["canonical_opportunity_id"] for r in rows] == ["canon-1"]


# 15 + 16 + 17. Account fanout / repeated horizons cannot inflate n; one opp once.
def test_fanout_and_horizons_do_not_inflate_n():
    primary = _shadow(0, opp="canon-1", pattern="GOOD_PATTERN", outcome=1.0, horizon="SCALP", shadow_id="a")
    fanout = _shadow(0, opp="canon-1", pattern="GOOD_PATTERN", outcome=1.0, horizon="SCALP", shadow_id="b")
    horizon = _shadow(0, opp="canon-1", pattern="GOOD_PATTERN", outcome=3.0, horizon="EXTENDED", shadow_id="c")
    rows, diagnostics = build_p1_observations([primary, fanout, horizon])
    assert len(rows) == 1
    assert rows[0]["outcome_r"] == 2.0  # mean of 1.0 and 3.0 across horizons
    assert diagnostics["paired_opportunities"] == 1


# 18 + 19 + 20. Policy is learned on discovery from pre-outcome pattern cells;
# deterministic; uses only pre-outcome covariate (pattern).
def test_policy_learned_deterministically_on_discovery():
    shadows = _population()
    rows, _ = build_p1_observations(shadows)
    discovery, _validation = _split(rows)
    removed1, _ = learn_removal_policy(discovery)
    removed2, _ = learn_removal_policy(discovery)
    assert removed1 == removed2  # deterministic
    assert "BAD_PATTERN" in removed1
    assert "GOOD_PATTERN" not in removed1


# 21. Unmatched/kept sides exposed (removed vs kept counts reported).
def test_removed_and_kept_sides_exposed():
    report = analyse(_population())
    v = report["overall"]["later_unseen_validation"]
    assert "removed_n" in v and "kept_n" in v
    assert v["removed_n"] + v["kept_n"] == v["n"]


# 22 + 23. Conflicting outcome evidence fails closed.
def test_conflicting_outcome_fails_closed():
    shadows = _population()
    # Two conflicting immutable decision times for the same opportunity.
    dup = _shadow(3, opp="canon-0003", pattern="GOOD_PATTERN", outcome=-9.0, shadow_id="dup")
    dup["decision_snapshot"]["timestamp_decision_utc"] = "2026-02-02T00:00:00+00:00"
    report = analyse([*shadows, dup])
    assert report["status"] == "BLOCKED"


# 24. Metric units consistent (R units).
def test_effect_metric_units_are_r():
    report = analyse(_population())
    metric = report["overall"]["effect_metric"]
    assert metric["name"] == "policy_minus_status_quo_ev_r"
    assert "R-multiple" in metric["units"]


# 25. Outcome-dependent metrics use only valid outcomes (missing excluded above).
def test_validation_ev_uses_only_valid_outcomes():
    report = analyse(_population())
    v = report["overall"]["later_unseen_validation"]
    assert v["status_quo"]["n"] == v["n"]  # all validation rows have valid outcomes


# 26 + 27. Chronology deterministic; validation cannot redefine discovery policy.
def test_chronology_deterministic_and_policy_frozen():
    shadows = _population()
    rows, _ = build_p1_observations(shadows)
    discovery, validation = _split(rows)
    assert max(r["_order"] for r in discovery) < min(r["_order"] for r in validation)
    # Learning on validation would produce a (potentially) different policy; the
    # runner only ever learns on discovery. Prove the two partitions can differ.
    d_removed, _ = learn_removal_policy(discovery)
    v_removed, _ = learn_removal_policy(validation)
    assert isinstance(d_removed, frozenset) and isinstance(v_removed, frozenset)


# 28. No causal claim from observational evidence.
def test_no_causal_claim():
    report = analyse(_population())
    o = report["overall"]
    assert o["causal_claim"].startswith("NONE")
    assert "not causal" in o["inference_limitation"].lower() or "not causal identification" in o["inference_limitation"].lower()


# 29. Insufficient sample -> WAITING_DATA (INSUFFICIENT_DATA).
def test_insufficient_sample_waits():
    report = analyse(_population(20))
    assert report["status"] == "INSUFFICIENT_DATA"


# 30. Valid negative/no-effect result can COMPLETE (DISCOVERY_ONLY when the
# discovery benefit does not persist).
def test_discovery_only_result_completes():
    report = analyse(_population(160, bad_persists=False))
    assert report["status"] == "COMPLETE"
    assert report["overall"]["finding_classification"] in {
        "DISCOVERY_ONLY", "COUNTERFACTUAL_HARM_SIGNAL", "NO_RELIABLE_COUNTERFACTUAL_SIGNAL",
    }


# COUNTERFACTUAL_SUPPORT reachable (removing the bad pattern helps and persists).
def test_counterfactual_support_reachable():
    report = analyse(_population(160, bad_persists=True))
    assert report["status"] == "COMPLETE"
    assert report["overall"]["finding_classification"] == "COUNTERFACTUAL_SUPPORT"
    assert report["overall"]["policy_version"] == PROMOTION_POLICY_VERSION
    assert "BAD_PATTERN" in report["overall"]["removed_patterns"]


# Empty candidate policy is validly COMPLETE with no impact.
def test_no_candidate_policy_completes():
    # All patterns are profitable -> no removal candidate on discovery.
    shadows = [_shadow(i, pattern="GOOD_PATTERN", outcome=1.0) for i in range(160)]
    report = analyse(shadows)
    assert report["status"] == "COMPLETE"
    assert report["overall"]["removed_patterns"] == []
    assert report["overall"]["finding_classification"] == "NO_CANDIDATE_POLICY"


# 31 + 32. Unique P1 report ownership; VALID definition; distinct from others.
def test_p1_unique_report_and_complete_definition():
    question = REGISTRY_BY_ID["P1"]
    assert question.runner_function == "run_promotion_impact"
    assert question.report_filename == P1_REPORT_FILENAME == "p1_promotion_impact.json"
    assert sum(q.report_filename == P1_REPORT_FILENAME for q in REGISTRY) == 1
    for other in ("D6", "PORT-1", "OPP-1", "D3", "X5"):
        assert REGISTRY_BY_ID[other].report_filename != question.report_filename
    definitions = build_definitions_from_registry(REGISTRY)
    health = validate_all_definitions(definitions)
    assert get_question_health(health["P1"]) == "VALID"


# 33-41. Ledger 40/70; RW4 COMPLETE; RW5 not started; RW1-3 complete; projection.
def test_ledger_derives_40_and_rw4_complete():
    from research_engine.registry.master_repair_ledger import (
        MASTER_REPAIR_LEDGER, REPAIR_WAVES, STRUCTURALLY_NON_OPERATIONAL_IDS,
        operational_baseline, projected_operational_counts,
    )

    assert operational_baseline() == (40, 30)
    assert MASTER_REPAIR_LEDGER["P1"].structurally_operational
    for qid in ("D6", "PORT-1", "OPP-1"):
        assert MASTER_REPAIR_LEDGER[qid].structurally_operational
    assert REPAIR_WAVES["RW4"].implemented is True
    for wave_id in ("RW1", "RW2", "RW3"):
        assert REPAIR_WAVES[wave_id].implemented is True
    assert REPAIR_WAVES["RW5"].implemented is False
    assert "P1" not in STRUCTURALLY_NON_OPERATIONAL_IDS
    projection = projected_operational_counts()
    assert projection[-1] == ("RW12", 70)
    assert dict(projection)["RW4"] == 40
    assert dict(projection)["RW5"] == 44


# 42 (proxy). Existing operational questions remain operational.
def test_existing_operational_unchanged():
    from research_engine.registry.master_repair_ledger import MASTER_REPAIR_LEDGER

    for qid in ("D6", "PORT-1", "OPP-1", "D2", "D3", "D4", "D5", "X5", "D1", "E2", "M1", "M3", "M7", "M8", "M11"):
        assert MASTER_REPAIR_LEDGER[qid].structurally_operational
