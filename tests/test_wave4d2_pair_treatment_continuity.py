"""
Wave 4D.2 — Treatment identity continuity through pairing: focused proofs.

Invariant: every normal candidate pair admitted for evaluation carries an
explicit ``treatment_id`` propagated verbatim from the historical candidate-
shadow evidence (the Wave 4D.1 trade_id encoding, strictly parsed and
fail-closed). The identity is NEVER invented, NEVER parsed loosely, and
NEVER recomputed from the candidate's current change_definition.

One evaluation population with mixed or missing treatment identities is
not one candidate experiment — it fails closed (INCONCLUSIVE) and is never
silently averaged.

Fixtures are production-shaped: 4D.1 trade_id mint format
``candidate_<id>_<cycle>_<symbol>_<treatment_id>`` paired against incumbent
trade_truth records by exact correlation_id. No AWS, no production state.
"""
import inspect
import sys

import pytest

sys.path.insert(0, ".")

from research_engine.lifecycle.candidate_pairing import (
    build_prospective_pairs,
    count_prospective_pairs,
)
from research_engine.lifecycle.candidate_evaluator import (
    CandidateEvaluator,
    EvaluationConfig,
)
from research_engine.lifecycle.candidate_shadow_hook import (
    candidate_trade_id,
    extract_treatment_id,
    resolve_candidate_treatment,
)

# Two materially different valid treatment digests (16 hex chars each).
_T1 = "a1b2c3d4e5f60718"
_T2 = "0f9e8d7c6b5a4321"
_EPOCH = "1970-01-01T00:00:00+00:00"


def _candidate_shadow(cor, candidate_r, *, candidate_id="OPT-test", symbol="EURUSD",
                      ts=1000.0, event_type="CLOSE", treatment_id=_T1, trade_id=None):
    """Production-shaped candidate shadow CLOSE record (V1 STR).

    treatment_id=_T1/_T2 → mints the exact 4D.1 hook trade_id format.
    treatment_id=None   → legacy (pre-4D.1) trade_id, identity missing.
    trade_id=<str>      → explicit override for malformed-identity cases.
    """
    if trade_id is None:
        if treatment_id is None:
            trade_id = f"candidate_{candidate_id}_{cor}"   # legacy: no identity
        else:
            trade_id = candidate_trade_id(candidate_id, 1, symbol, treatment_id)
    return {
        "schema_version": "shadow_trades_v1",
        "source": "shadow_trade_engine",
        "event_type": event_type,
        "identity": {
            "trade_id": trade_id,
            "correlation_id": cor,
            "canonical_opportunity_id": None,
            "symbol": symbol,
            "strategy_id": "",
            "cycle_id": "1",
            "entity_id": f"{symbol}_{cor}",
            "shadow_type": f"CANDIDATE_{candidate_id}",
            "v10_action": "CANDIDATE_SHADOW",
        },
        "decision_snapshot": {
            "timestamp_decision_utc": ts,
            "entry_intent_price": 1.1,
            "stop_loss_intent": 1.095,
            "take_profit_intent": 1.115,
            "direction": "BUY",
            "pattern": "ENGULFING",
            "score": 0.7,
            "trade_horizon": "",
        },
        "simulated_outcome": {
            "pnl_r_multiple": candidate_r,
            "mfe_r": max(candidate_r, 0.0),
            "mae_r": min(candidate_r, 0.0),
            "exit_reason": "take_profit" if candidate_r > 0 else "stop_loss",
            "bars_held": 5,
        },
    }


def _incumbent_truth(cor, baseline_r, *, symbol="EURUSD", ts=1000.0, trade_id=None):
    """Production-shaped incumbent realised outcome (trade_truth_v1).

    Note: the incumbent side carries NO treatment identity — and must never
    be assigned one.
    """
    return {
        "schema_version": "trade_truth_v1",
        "identity": {
            "trade_id": trade_id or f"pos_{cor}",
            "correlation_id": cor,
            "canonical_opportunity_id": None,
            "symbol": symbol,
        },
        "execution": {"entry_fill_price": 1.1, "exit_fill_price": 1.102,
                      "volume_executed": 0.1},
        "timestamps": {"entry_timestamp_broker": ts, "exit_timestamp_broker": ts + 300.0,
                       "duration_seconds": 300.0},
        "outcome": {"r_multiple_realised": baseline_r, "pnl_realised": baseline_r * 10.0,
                    "commission": -1.0, "swap": 0.0, "net_profit": baseline_r * 10.0 - 1.0,
                    "mfe_r": max(baseline_r, 0.0), "mae_r": min(baseline_r, 0.0)},
        "exit": {"exit_reason": "take_profit" if baseline_r > 0 else "stop_loss"},
    }


def _population(n, *, treatment_id=_T1, candidate_id="OPT-test", base_ts=1000.0):
    """n matched (candidate shadow, incumbent truth) pairs with one treatment."""
    cand, inc = [], []
    for i in range(n):
        cor = f"COR-2026-{int(base_ts)}-{i:05d}"
        cand.append(_candidate_shadow(cor, 0.3, candidate_id=candidate_id,
                                      treatment_id=treatment_id, ts=base_ts + i * 300))
        inc.append(_incumbent_truth(cor, -0.2, ts=base_ts + i * 300))
    return cand, inc


def _resolve(change_definition):
    return resolve_candidate_treatment(
        change_definition=change_definition, direction="SELL",
        entry_price=1.085, stop_loss=1.086, take_profit=1.083,
        risk_distance=0.001, symbol="EURUSD", pattern="TBC",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# STRICT MINT/PARSE ROUND TRIP (the 4D.1 encoding is the only authority)
# ═══════════════════════════════════════════════════════════════════════════════


class TestMintParseRoundTrip:
    def test_hook_mint_parses_back_exactly(self):
        res, reason = _resolve({"type": "direction_inversion"})
        assert res is not None and reason == "ok"
        trade_id = candidate_trade_id("OPT-x", 7, "EURUSD", res.treatment_id)
        assert extract_treatment_id(trade_id) == res.treatment_id

    def test_extract_fail_closed_on_missing_and_malformed(self):
        assert extract_treatment_id("") is None
        assert extract_treatment_id(None) is None
        assert extract_treatment_id(123) is None
        # legacy (pre-4D.1) format: no identity segment at all
        assert extract_treatment_id("candidate_OPT-test_COR-1") is None
        # non-hex final segment
        assert extract_treatment_id("candidate_OPT-test_1_EURUSD_zzzz") is None
        # wrong digest length
        assert extract_treatment_id("candidate_OPT-test_1_EURUSD_abc123") is None
        # bare candidate prefix only
        assert extract_treatment_id("candidate_") is None


# ═══════════════════════════════════════════════════════════════════════════════
# PAIR CARRIES THE EXPLICIT, EVIDENCE-DERIVED TREATMENT IDENTITY
# ═══════════════════════════════════════════════════════════════════════════════


class TestPairCarriesTreatment:
    def test_valid_shadow_with_treatment_T_produces_pair_carrrying_T(self):
        pr = build_prospective_pairs(
            candidate_id="OPT-test", candidate_activated_at=_EPOCH,
            candidate_records=[_candidate_shadow("COR-A", 0.5, treatment_id=_T1)],
            incumbent_records=[_incumbent_truth("COR-A", -0.2)],
        )
        assert len(pr.pairs) == 1
        assert pr.pairs[0]["treatment_id"] == _T1

    def test_pair_treatment_id_exactly_equals_shadow_evidence(self):
        """For every valid treatment_id, pair.treatment_id == extract_treatment_id(shadow.trade_id)."""
        for tid in (_T1, _T2):
            cand = [_candidate_shadow("COR-A", 0.5, treatment_id=tid)]
            inc = [_incumbent_truth("COR-A", -0.2)]
            pr = build_prospective_pairs(
                candidate_id="OPT-test", candidate_activated_at=_EPOCH,
                candidate_records=cand, incumbent_records=inc,
            )
            assert len(pr.pairs) == 1
            assert pr.pairs[0]["treatment_id"] == tid
            assert pr.pairs[0]["treatment_id"] == extract_treatment_id(cand[0]["identity"]["trade_id"])

    def test_evidence_derived_not_recomputed_from_current_state(self):
        """The pair carries the HISTORICAL identity, never a recomputed one."""
        # Evidence was generated under _T1 (historical shadow trade_id).
        cand = [_candidate_shadow("COR-A", 0.5, treatment_id=_T1)]
        inc = [_incumbent_truth("COR-A", -0.2)]
        # A "current candidate state" resolving to a materially DIFFERENT
        # treatment must have NO influence on the pair identity. The pair
        # builder has no change_definition input at all (structural proof).
        res_other, _ = _resolve({"type": "geometry_modification",
                                 "stop_multiplier": 2.0})
        assert res_other.treatment_id not in ("", _T1)
        params = inspect.signature(build_prospective_pairs).parameters
        assert "change_definition" not in params
        pr = build_prospective_pairs(
            candidate_id="OPT-test", candidate_activated_at=_EPOCH,
            candidate_records=cand, incumbent_records=inc,
        )
        assert pr.pairs[0]["treatment_id"] == _T1
        assert pr.pairs[0]["treatment_id"] != res_other.treatment_id

    def test_baseline_side_gets_no_fabricated_treatment(self):
        """Incumbent trade_id is plain (non-candidate format) and still pairs;
        treatment identity is candidate-side only — never fabricated onto
        baseline evidence."""
        pr = build_prospective_pairs(
            candidate_id="OPT-test", candidate_activated_at=_EPOCH,
            candidate_records=[_candidate_shadow("COR-A", 0.5, treatment_id=_T1)],
            incumbent_records=[_incumbent_truth("COR-A", -0.2, trade_id="pos_plain_1")],
        )
        assert len(pr.pairs) == 1
        assert pr.pairs[0]["incumbent_trade_id"] == "pos_plain_1"
        assert extract_treatment_id(pr.pairs[0]["incumbent_trade_id"]) is None
        assert pr.pairs[0]["treatment_id"] == _T1


# ═══════════════════════════════════════════════════════════════════════════════
# POPULATION HOMOGENEITY
# ═══════════════════════════════════════════════════════════════════════════════
    def test_materially_different_treatments_distinguishable(self):
        c1, i1 = _population(5, treatment_id=_T1)
        c2, i2 = _population(5, treatment_id=_T2, base_ts=90000.0)
        p1 = build_prospective_pairs(candidate_id="OPT-test",
                                     candidate_activated_at=_EPOCH,
                                     candidate_records=c1, incumbent_records=i1)
        p2 = build_prospective_pairs(candidate_id="OPT-test",
                                     candidate_activated_at=_EPOCH,
                                     candidate_records=c2, incumbent_records=i2)
        t1 = {p["treatment_id"] for p in p1.pairs}
        t2 = {p["treatment_id"] for p in p2.pairs}
        assert t1 == {_T1} and t2 == {_T2} and t1 != t2

    def test_mixed_treatment_population_fail_closed(self):
        """Two materially different treatments must never be averaged as one
        candidate experiment."""
        cand_a, inc_a = _population(20, treatment_id=_T1)
        cand_b, inc_b = _population(15, treatment_id=_T2, base_ts=90000.0)
        ev = CandidateEvaluator(EvaluationConfig(minimum_sample=30))
        result = ev.evaluate(
            candidate_id="OPT-test", candidate_activated_at=_EPOCH,
            candidate_records=cand_a + cand_b, incumbent_records=inc_a + inc_b,
        )
        assert result.n == 35
        assert result.decision == "INCONCLUSIVE"
        assert result.decision_reason.startswith("mixed_treatment_population")
        assert result.treatment_id == ""
        assert result.confidence == "INSUFFICIENT"

    def test_mixed_injected_pairs_fail_closed(self):
        pairs = [
            {"correlation_id": "C1", "candidate_id": "OPT-test",
             "treatment_id": _T1, "baseline_r": -0.2, "candidate_r": 0.3,
             "symbol": "EURUSD", "timestamp": 1000.0},
            {"correlation_id": "C2", "candidate_id": "OPT-test",
             "treatment_id": _T2, "baseline_r": -0.2, "candidate_r": 0.3,
             "symbol": "EURUSD", "timestamp": 2000.0},
        ]
        result = CandidateEvaluator(EvaluationConfig(minimum_sample=1)).evaluate(
            candidate_id="OPT-test", candidate_activated_at=_EPOCH, pairs=pairs)
        assert result.decision == "INCONCLUSIVE"
        assert result.decision_reason.startswith("mixed_treatment_population")

    def test_missing_identity_in_injected_pairs_fail_closed(self):
        pairs = [{"correlation_id": "C1", "candidate_id": "OPT-test",
                  "baseline_r": -0.2, "candidate_r": 0.3,
                  "symbol": "EURUSD", "timestamp": 1000.0}]
        result = CandidateEvaluator(EvaluationConfig(minimum_sample=1)).evaluate(
            candidate_id="OPT-test", candidate_activated_at=_EPOCH, pairs=pairs)
        assert result.decision == "INCONCLUSIVE"
        assert result.decision_reason.startswith("missing_treatment_identity")

    def test_conflicting_treatment_identity_duplicates_excluded(self):
        """Same COR, identical outcome, DIFFERENT treatment identities —
        never collapsed, never fabricated."""
        cand = [
            _candidate_shadow("COR-A", 0.5, treatment_id=_T1),
            _candidate_shadow("COR-A", 0.5, treatment_id=_T2),
        ]
        pr = build_prospective_pairs(
            candidate_id="OPT-test", candidate_activated_at=_EPOCH,
            candidate_records=cand, incumbent_records=[_incumbent_truth("COR-A", -0.2)],
        )
        assert len(pr.pairs) == 0
        assert pr.diagnostics.candidate_treatment_conflict == 2

    def test_count_excludes_invalid_treatment_observations(self):
        """The counted population and the evaluable population cannot drift:
        treatment-invalid rows reduce the count too."""
        cand, inc = _population(3)
        cand.append(_candidate_shadow("COR-LEGACY", 0.7, treatment_id=None))
        assert count_prospective_pairs(
            candidate_id="OPT-test", candidate_activated_at=_EPOCH,
            candidate_records=cand, incumbent_records=inc + [
                _incumbent_truth("COR-LEGACY", -0.2)],
        ) == 3




# ═══════════════════════════════════════════════════════════════════════════════
# FAIL CLOSED: missing / malformed / unreconcilable identity
# ═══════════════════════════════════════════════════════════════════════════════


class TestFailClosedExclusion:
    def test_missing_identity_never_pairs(self):
        pr = build_prospective_pairs(
            candidate_id="OPT-test", candidate_activated_at=_EPOCH,
            candidate_records=[_candidate_shadow("COR-A", 0.5, trade_id="")],
            incumbent_records=[_incumbent_truth("COR-A", -0.2)],
        )
        assert len(pr.pairs) == 0
        assert pr.diagnostics.candidate_missing_treatment == 1

    def test_malformed_identity_never_pairs(self):
        pr = build_prospective_pairs(
            candidate_id="OPT-test", candidate_activated_at=_EPOCH,
            candidate_records=[_candidate_shadow(
                "COR-A", 0.5, trade_id="candidate_OPT-test_1_EURUSD_not-valid-hex")],
            incumbent_records=[_incumbent_truth("COR-A", -0.2)],
        )
        assert len(pr.pairs) == 0
        assert pr.diagnostics.candidate_malformed_treatment == 1

    def test_unreconcilable_candidate_provenance_never_pairs(self):
        """trade_id minted for a DIFFERENT candidate while carrying this
        candidate's shadow_type → provenance cannot be reconciled."""
        pr = build_prospective_pairs(
            candidate_id="OPT-test", candidate_activated_at=_EPOCH,
            candidate_records=[_candidate_shadow(
                "COR-A", 0.5, candidate_id="OPT-other",
                trade_id=candidate_trade_id("OPT-other", 1, "EURUSD", _T1),
                treatment_id=_T1)],
            incumbent_records=[_incumbent_truth("COR-A", -0.2)],
        )
        assert pr.diagnostics.candidate_for_other_or_none == 1
        assert len(pr.pairs) == 0

    def test_one_invalid_does_not_destroy_valid_pairing(self):
        """A malformed candidate observation must not crash pairing for the
        other valid observations."""
        cand, inc = _population(2, base_ts=1000.0)
        cand.append(_candidate_shadow("COR-LEG", 0.7, ts=90000.0,
                                      treatment_id=None))          # legacy: malformed
        cand.append(_candidate_shadow("COR-BAD", 0.7, ts=90000.0,
                                      trade_id="candidate_OPT-test_COR-BAD_!!"))  # malformed
        inc.append(_incumbent_truth("COR-LEG", -0.2, ts=90000.0))
        inc.append(_incumbent_truth("COR-BAD", -0.2, ts=90000.0))
        pr = build_prospective_pairs(
            candidate_id="OPT-test", candidate_activated_at=_EPOCH,
            candidate_records=cand, incumbent_records=inc,
        )
        assert len(pr.pairs) == 2
        assert all(p["treatment_id"] == _T1 for p in pr.pairs)
        assert pr.diagnostics.candidate_missing_treatment == 0
        assert pr.diagnostics.candidate_malformed_treatment == 2
        assert pr.diagnostics.matched_pairs == 2


class TestTreatmentHomogeneity:
    def test_homogeneous_population_evaluates_normally(self):
        cand, inc = _population(35, treatment_id=_T1)
        ev = CandidateEvaluator(EvaluationConfig(minimum_sample=30))
        result = ev.evaluate(candidate_id="OPT-test", candidate_activated_at=_EPOCH,
                             candidate_records=cand, incumbent_records=inc)
        assert result.n == 35
        assert result.treatment_id == _T1
        assert not result.decision_reason.startswith(("missing_treatment_identity",
                                                      "mixed_treatment_population"))

    def test_mixed_two_treatments_fail_closed(self):
        """Mixed T1+T2 population is INCONCLUSIVE, never averaged."""
        cand_t1, inc_t1 = _population(10, treatment_id=_T1, base_ts=1000.0)
        cand_t2, inc_t2 = _population(10, treatment_id=_T2, base_ts=2000.0)
        mixed_cand = cand_t1 + cand_t2
        mixed_inc = inc_t1 + inc_t2
        ev = CandidateEvaluator(EvaluationConfig(minimum_sample=5))
        result = ev.evaluate(
            candidate_id="OPT-test", candidate_activated_at=_EPOCH,
            candidate_records=mixed_cand, incumbent_records=mixed_inc)
        assert result.n == 20
        assert result.decision_reason.startswith("mixed_treatment_population")
        assert result.decision == "INCONCLUSIVE"
        assert result.treatment_id == ""

    def test_missing_treatment_population_fail_closed(self):
        """Population where some pairs have no treatment identity also fails closed."""
        cand_ok, inc_ok = _population(10, treatment_id=_T1, base_ts=1000.0)
        cand_bad = [_candidate_shadow("COR-BAD", 0.5, ts=90000.0, treatment_id=None)]
        inc_bad = [_incumbent_truth("COR-BAD", -0.2, ts=90000.0)]
        ev = CandidateEvaluator(EvaluationConfig(minimum_sample=5))
        result = ev.evaluate(
            candidate_id="OPT-test", candidate_activated_at=_EPOCH,
            candidate_records=cand_ok + cand_bad,
            incumbent_records=inc_ok + inc_bad)
        assert result.n == 10
        assert result.treatment_id == _T1
        assert not result.decision_reason.startswith("missing_treatment_identity")

    def test_count_prospective_pairs_matches_evaluator_n(self):
        """count_prospective_pairs and evaluator.n must agree on homogeneous population."""
        cand, inc = _population(40, treatment_id=_T1, base_ts=1000.0)
        n = count_prospective_pairs(
            candidate_id="OPT-test", candidate_activated_at=_EPOCH,
            candidate_records=cand, incumbent_records=inc)
        ev = CandidateEvaluator(EvaluationConfig(minimum_sample=5))
        result = ev.evaluate(
            candidate_id="OPT-test", candidate_activated_at=_EPOCH,
            candidate_records=cand, incumbent_records=inc)
        assert n == 40
        assert result.n == 40
        assert result.treatment_id == _T1
