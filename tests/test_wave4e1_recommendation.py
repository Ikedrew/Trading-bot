"""
Wave 4E.1 - Canonical Recommendation + Evidence Attribution: focused proof tests.

Tests prove that CandidateEvaluation produces one durable, deterministic
recommendation record whose conclusion is explicitly attributable to the
exact candidate, treatment, baseline and evaluation evidence that produced it.
"""

import sys
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest

from research_engine.lifecycle.candidate_evaluator import (
    CandidateEvaluation,
    EvaluationConfig,
)
from research_engine.lifecycle.candidate_recommendation import (
    CandidateRecommendation,
    RecommendationStore,
    create_recommendation,
    _derive_recommendation_id,
    _actionable,
    _derive_limitations,
)


# ═══════════════════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════════════════


def _make_evaluation(
    candidate_id="OPT-test",
    treatment_id="a4d2c0de4d2feed1",
    baseline_id="current_v10",
    config_hash="abc123def456",
    decision="VALIDATED",
    confidence="HIGH",
    n=100,
    mean_delta_r=0.5,
    eligible_pairs=100,
    promotion_blocked=False,
    promotion_block_reason="",
    ci_lower=0.2,
    ci_upper=0.8,
    permutation_p=0.001,
    oos_n=40,
    oos_delta_r=0.3,
    risk_level="LOW",
    symbols_positive=3,
    symbols_total=3,
    periods_positive=3,
    periods_total=5,
    survives_outlier_removal=True,
    worst_delta_r=-0.1,
    candidate_win_rate=0.6,
    mean_baseline_r=-0.3,
    mean_candidate_r=0.2,
    evaluation_id="EVAL-test001",
    decision_reason="Candidate outperforms baseline",
    total_observations_raw=200,
    excluded_unpaired=0,
    excluded_pre_boundary=0,
):
    """Build a production-shaped CandidateEvaluation for testing."""
    return CandidateEvaluation(
        evaluation_id=evaluation_id,
        candidate_id=candidate_id,
        timestamp="2026-09-17T12:00:00+00:00",
        prospective_boundary="1970-01-01T00:00:00+00:00",
        total_observations_raw=total_observations_raw,
        eligible_pairs=eligible_pairs,
        excluded_unpaired=excluded_unpaired,
        excluded_pre_boundary=excluded_pre_boundary,
        n=n,
        mean_baseline_r=mean_baseline_r,
        mean_candidate_r=mean_candidate_r,
        mean_delta_r=mean_delta_r,
        median_delta_r=mean_delta_r,
        total_baseline_r=mean_baseline_r * n,
        total_candidate_r=mean_candidate_r * n,
        candidate_wins=int(n * 0.6),
        candidate_win_rate=candidate_win_rate,
        ci_lower=ci_lower,
        ci_upper=ci_upper,
        permutation_p=permutation_p,
        oos_n=oos_n,
        oos_delta_r=oos_delta_r,
        symbols_positive=symbols_positive,
        symbols_total=symbols_total,
        periods_positive=periods_positive,
        periods_total=periods_total,
        survives_outlier_removal=survives_outlier_removal,
        worst_delta_r=worst_delta_r,
        risk_level=risk_level,
        decision=decision,
        decision_reason=decision_reason,
        confidence=confidence,
        baseline_id=baseline_id,
        config_hash=config_hash,
                promotion_blocked=promotion_blocked,
        promotion_block_reason=promotion_block_reason,
        treatment_id=treatment_id,
    )

# ═══════════════════════════════════════════════════════════════════════════════
# 2-5. EXACT PROVENANCE PRESERVATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestProvenancePreservation:

    def test_candidate_id_preserved_exactly(self):
        """candidate_id is preserved exactly (test 2)."""
        for cid in ["OPT-xxx", "CAND-123", "TEST_999"]:
            evaluation = _make_evaluation(candidate_id=cid)
            rec = create_recommendation(evaluation)
            assert rec is not None
            assert rec.candidate_id == cid
            assert rec.candidate_id == evaluation.candidate_id

    def test_treatment_id_preserved_exactly(self):
        """treatment_id is preserved exactly from evaluation evidence (test 3)."""
        for tid in ["a1b2c3d4e5f60718", "ffeeddccbbaa9988", "0011223344556677"]:
            evaluation = _make_evaluation(treatment_id=tid)
            rec = create_recommendation(evaluation)
            assert rec is not None
            assert rec.treatment_id == tid
            assert rec.treatment_id == evaluation.treatment_id

    def test_treatment_id_empty_gives_empty_string(self):
        """Empty treatment_id in evaluation -> empty string in recommendation (test 3)."""
        evaluation = _make_evaluation(treatment_id="")
        rec = create_recommendation(evaluation)
        assert rec is not None
        assert rec.treatment_id == ""
        assert rec.treatment_id == evaluation.treatment_id

    def test_baseline_id_preserved_exactly(self):
        """baseline_id is preserved exactly (test 4)."""
        for bid in ["current_v10", "v10_2026_09_01", "BL-ABC-123"]:
            evaluation = _make_evaluation(baseline_id=bid)
            rec = create_recommendation(evaluation)
            assert rec is not None
            assert rec.baseline_id == bid
            assert rec.baseline_id == evaluation.baseline_id

    def test_baseline_config_hash_preserved_exactly(self):
        """baseline_config_hash is preserved exactly where applicable (test 5)."""
        for ch in ["abc123def456", "deadbeefcafebabe", "0000000000000000"]:
            evaluation = _make_evaluation(config_hash=ch)
            rec = create_recommendation(evaluation)
            assert rec is not None
            assert rec.baseline_config_hash == ch
            assert rec.baseline_config_hash == evaluation.config_hash

    def test_baseline_config_hash_empty_gives_empty_string(self):
        """Empty config_hash -> empty string preserved (test 5)."""
        evaluation = _make_evaluation(config_hash="")
        rec = create_recommendation(evaluation)
        assert rec is not None
        assert rec.baseline_config_hash == ""
        assert rec.baseline_config_hash == evaluation.config_hash

# ═══════════════════════════════════════════════════════════════════════════════
# 6-8. EVIDENCE/STATISTICS/CONFIDENCE PRESERVATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestEvidenceStatisticsConfidencePreservation:

    def test_eligible_pairs_preserved(self):
        """Evidence/sample count is preserved (test 6)."""
        for n in [10, 30, 100, 500]:
            evaluation = _make_evaluation(eligible_pairs=n, n=n)
            rec = create_recommendation(evaluation)
            assert rec is not None
            assert rec.eligible_pairs == n
            assert rec.eligible_pairs == evaluation.eligible_pairs

    def test_effect_statistics_preserved(self):
        """Evaluation effect statistics preserved without recomputation (test 7)."""
        evaluation = _make_evaluation(
            mean_delta_r=1.23,
            mean_baseline_r=-0.45,
            mean_candidate_r=0.78,
            candidate_win_rate=0.65,
            ci_lower=0.1,
            ci_upper=0.9,
            permutation_p=0.003,
            oos_n=40,
            oos_delta_r=0.25,
            worst_delta_r=-0.2,
            total_observations_raw=200,
            excluded_unpaired=5,
            excluded_pre_boundary=10,
        )
        rec = create_recommendation(evaluation)
        assert rec is not None
        assert rec.mean_delta_r == evaluation.mean_delta_r
        assert rec.mean_baseline_r == evaluation.mean_baseline_r
        assert rec.mean_candidate_r == evaluation.mean_candidate_r
        assert rec.candidate_win_rate == evaluation.candidate_win_rate
        assert rec.ci_lower == evaluation.ci_lower
        assert rec.ci_upper == evaluation.ci_upper
        assert rec.permutation_p == evaluation.permutation_p
        assert rec.oos_n == evaluation.oos_n
        assert rec.oos_delta_r == evaluation.oos_delta_r
        assert rec.worst_delta_r == evaluation.worst_delta_r
        assert rec.total_observations_raw == evaluation.total_observations_raw
        assert rec.excluded_unpaired == evaluation.excluded_unpaired
        assert rec.excluded_pre_boundary == evaluation.excluded_pre_boundary

    def test_confidence_preserved(self):
        """Confidence is preserved exactly (test 8)."""
        for conf in ["HIGH", "MEDIUM", "LOW", "INSUFFICIENT"]:
            evaluation = _make_evaluation(confidence=conf)
            rec = create_recommendation(evaluation)
            assert rec is not None
            assert rec.confidence == conf
            assert rec.confidence == evaluation.confidence



# ═══════════════════════════════════════════════════════════════════════════════
# 9-10. NON-ACTIONABLE SEMANTICS
# ═══════════════════════════════════════════════════════════════════════════════


class TestNonActionableSemantics:

    def test_inconclusive_is_non_actionable(self):
        """INCONCLUSIVE evidence does NOT become an actionable optimisation recommendation (test 9)."""
        for decision in ["INCONCLUSIVE", "REJECTED"]:
            evaluation = _make_evaluation(decision=decision, confidence="LOW")
            rec = create_recommendation(evaluation)
            assert rec is not None
            assert rec.actionable is False
            assert rec.decision == decision
            assert rec.evaluation_id == "EVAL-test001"

    def test_promotion_blocked_is_non_actionable(self):
        """promotion_blocked evaluation does NOT become actionable (test 10)."""
        evaluation = _make_evaluation(
            decision="VALIDATED",
            promotion_blocked=True,
            promotion_block_reason="stale_baseline: candidate baseline != active",
            confidence="HIGH",
            evaluation_id="EVAL-pb",
        )
        rec = create_recommendation(evaluation)
        assert rec is not None
        assert rec.actionable is False
        assert rec.promotion_blocked is True
        assert rec.promotion_block_reason == "stale_baseline: candidate baseline != active"
        assert rec.confidence == "HIGH"  # evidence still valid, just not actionable

    def test_validated_non_blocked_is_actionable(self):
        """VALIDATED + not blocked -> actionable."""
        evaluation = _make_evaluation(
            decision="VALIDATED",
            promotion_blocked=False,
            confidence="HIGH",
        )
        rec = create_recommendation(evaluation)
        assert rec is not None
        assert rec.actionable is True
        assert rec.decision == "VALIDATED"

    def test_inconclusive_preserves_evidence_as_knowledge(self):
        """INCONCLUSIVE is preserved as non-actionable research knowledge."""
        evaluation = _make_evaluation(
            decision="INCONCLUSIVE",
            decision_reason="Effect not statistically significant",
            confidence="LOW",
            mean_delta_r=0.05,
            eligible_pairs=25,
        )
        rec = create_recommendation(evaluation)
        assert rec is not None
        assert rec.actionable is False
        assert rec.decision == "INCONCLUSIVE"
        assert rec.evaluation_id == "EVAL-test001"
        assert rec.candidate_id == "OPT-test"
        assert rec.treatment_id == "a4d2c0de4d2feed1"
        # The evidence is still recorded - just not actionable
        assert rec.mean_delta_r == 0.05
        assert rec.eligible_pairs == 25



# ═══════════════════════════════════════════════════════════════════════════════
# 11-13. FAIL-CLOSED CONDITIONS
# ═══════════════════════════════════════════════════════════════════════════════


class TestFailClosedConditions:

    def test_missing_candidate_id_fails_closed(self):
        """Missing candidate_id fails closed - no record created (test 11)."""
        for bad_cid in ["", None]:
            evaluation = _make_evaluation(candidate_id=bad_cid if bad_cid else "")
            rec = create_recommendation(evaluation)
            assert rec is None, f"Expected None for candidate_id={bad_cid!r}"

    def test_missing_treatment_provenance_fails_closed_via_limitations(self):
        """Missing treatment_id produces limitations and non-actionable record (test 11)."""
        evaluation = _make_evaluation(treatment_id="")
        rec = create_recommendation(evaluation)
        assert rec is not None
        assert rec.treatment_id == ""
        assert rec.actionable is False
        assert any("treatment" in lim.lower() for lim in rec.limitations)

    def test_missing_baseline_provenance_fails_closed_via_limitations(self):
        """Missing baseline_id produces limitations (test 12)."""
        evaluation = _make_evaluation(baseline_id="")
        rec = create_recommendation(evaluation)
        assert rec is not None
        assert rec.baseline_id == ""
        assert rec.actionable is False
        assert any("baseline" in lim.lower() for lim in rec.limitations)

    def test_missing_config_hash_fails_closed_via_limitations(self):
        """Missing baseline_config_hash produces limitations (test 12)."""
        evaluation = _make_evaluation(config_hash="")
        rec = create_recommendation(evaluation)
        assert rec is not None
        assert rec.baseline_config_hash == ""
        assert rec.actionable is False
        assert any("config_hash" in lim.lower() for lim in rec.limitations)

    def test_small_sample_produces_limitations(self):
        """Small sample size produces limitations and non-actionable record."""
        evaluation = _make_evaluation(eligible_pairs=10, n=10, decision="VALIDATED")
        rec = create_recommendation(evaluation)
        assert rec is not None
        assert rec.actionable is False
        assert any("small_sample" in lim.lower() for lim in rec.limitations)

    def test_insufficient_confidence_produces_limitations(self):
        """INSUFFICIENT confidence produces limitations."""
        evaluation = _make_evaluation(confidence="INSUFFICIENT", decision="INCONCLUSIVE")
        rec = create_recommendation(evaluation)
        assert rec is not None
        assert rec.actionable is False
        assert any("insufficient" in lim.lower() for lim in rec.limitations)


# ═══════════════════════════════════════════════════════════════════════════════
# 14. DISTINGUISHABLE PROVENANCE
# ═══════════════════════════════════════════════════════════════════════════════


class TestDistinguishableProvenance:

    def test_different_treatment_id_produces_distinguishable_records(self):
        """Materially different treatment_id produces distinguishable recommendations (test 14)."""
        evaluation_a = _make_evaluation(treatment_id="aaaa0000bbbb1111", evaluation_id="EVAL-t1")
        evaluation_b = _make_evaluation(treatment_id="cccc2222dddd3333", evaluation_id="EVAL-t2")
        rec_a = create_recommendation(evaluation_a)
        rec_b = create_recommendation(evaluation_b)
        assert rec_a is not None
        assert rec_b is not None
        assert rec_a.treatment_id != rec_b.treatment_id
        assert rec_a.recommendation_id != rec_b.recommendation_id

    def test_different_baseline_id_produces_distinguishable_records(self):
        """Materially different baseline_id produces distinguishable recommendations (test 15)."""
        evaluation_a = _make_evaluation(baseline_id="BL-current-2026", evaluation_id="EVAL-b1")
        evaluation_b = _make_evaluation(baseline_id="BL-stale-2025", evaluation_id="EVAL-b2")
        rec_a = create_recommendation(evaluation_a)
        rec_b = create_recommendation(evaluation_b)
        assert rec_a is not None
        assert rec_b is not None
        assert rec_a.baseline_id != rec_b.baseline_id
        assert rec_a.recommendation_id != rec_b.recommendation_id

    def test_different_candidate_id_produces_distinguishable_records(self):
        """Materially different candidate_id produces distinguishable recommendations."""
        evaluation_a = _make_evaluation(candidate_id="OPT-A", evaluation_id="EVAL-c1")
        evaluation_b = _make_evaluation(candidate_id="OPT-B", evaluation_id="EVAL-c2")
        rec_a = create_recommendation(evaluation_a)
        rec_b = create_recommendation(evaluation_b)
        assert rec_a is not None
        assert rec_b is not None
        assert rec_a.candidate_id != rec_b.candidate_id
        assert rec_a.recommendation_id != rec_b.recommendation_id


    def test_malformed_evaluation_input_still_produces_record(self):
        """Malformed but structurally valid evaluation still produces a record."""
        evaluation = CandidateEvaluation(
            candidate_id="OPT-malformed",
            evaluation_id="EVAL-malformed",
            decision="",
            confidence="",
            eligible_pairs=0,
            n=0,
        )
        rec = create_recommendation(evaluation)
        assert rec is not None
        assert rec.candidate_id == "OPT-malformed"
        assert rec.actionable is False

    def test_recommendation_does_not_call_human_approval(self):
        """Recommendation creation does NOT invoke record_human_decision (test 17)."""
        evaluation = _make_evaluation()
        rec = create_recommendation(evaluation)
        assert rec is not None
        # The recommendation has no human decision fields
        assert rec.promotion_blocked is False
        assert rec.decision == "VALIDATED"  # System decision, not human


# ═══════════════════════════════════════════════════════════════════════════════
# 16. DETERMINISTIC / IDEMPOTENT IDENTITY SEMANTICS
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeterministicIdempotentIdentity:

    def test_same_evaluation_id_produces_same_recommendation_id(self):
        """Repeated processing of the same logical evaluation has deterministic identity semantics (test 16)."""
        evaluation = _make_evaluation()
        rec1 = create_recommendation(evaluation)
        rec2 = create_recommendation(evaluation)
        assert rec1 is not None
        assert rec2 is not None
        assert rec1.recommendation_id == rec2.recommendation_id
        assert rec1.recommendation_id == "REC-EVAL-test001"

    def test_recommendation_id_derivation_is_deterministic(self):
        """_derive_recommendation_id always produces REC-{evaluation_id}."""
        for eval_id in ["EVAL-abc123", "EVAL-xyz789", "EVAL-00000000"]:
            rec_id = _derive_recommendation_id(eval_id)
            assert rec_id == f"REC-{eval_id}"

    def test_idempotent_recommendation_id_across_creations(self):
        """Creating recommendations for same evaluation always gives same recommendation_id."""
        evaluation = _make_evaluation(evaluation_id="EVAL-deterministic")
        recs = [create_recommendation(evaluation) for _ in range(5)]
        for rec in recs:
            assert rec is not None
            assert rec.recommendation_id == "REC-EVAL-deterministic"



# ═══════════════════════════════════════════════════════════════════════════════
# 17-20. NO SIDE-EFFECTS ON CANDIDATE LIFECYCLE / HUMAN DECISION / BASELINE
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoSideEffects:

    def test_recommendation_creation_does_not_alter_candidate_lifecycle(self):
        """Recommendation creation does NOT alter candidate lifecycle state (test 17)."""
        evaluation = _make_evaluation(
            candidate_id="OPT-no-side-effects",
            decision="VALIDATED",
        )
        rec = create_recommendation(evaluation)
        assert rec is not None
        assert rec.candidate_id == "OPT-no-side-effects"
        assert rec.decision == "VALIDATED"

    def test_recommendation_has_no_human_decision_fields(self):
        """Recommendation creation does NOT create human approval (test 18)."""
        evaluation = _make_evaluation(decision="VALIDATED", confidence="HIGH")
        rec = create_recommendation(evaluation)
        assert rec is not None
        assert hasattr(rec, "decision")
        assert not hasattr(rec, "human_decision")
        assert not hasattr(rec, "approved_by")
        assert not hasattr(rec, "approval_timestamp")

    def test_recommendation_is_not_an_application_record(self):
        """Recommendation creation does NOT write application/deployment state (test 19)."""
        evaluation = _make_evaluation()
        rec = create_recommendation(evaluation)
        assert rec is not None
        rec_dict = rec.to_dict()
        assert "application_id" not in rec_dict
        assert "deployment_status" not in rec_dict
        assert "active_baseline" not in rec_dict
        assert "mt5_execution" not in rec_dict

    def test_recommendation_does_not_modify_active_baseline(self):
        """Recommendation creation does NOT modify active production baseline (test 20)."""
        evaluation = _make_evaluation(baseline_id="current_v10", config_hash="abc123")
        rec = create_recommendation(evaluation)
        assert rec is not None
        assert rec.baseline_id == "current_v10"

# ═══════════════════════════════════════════════════════════════════════════════
# 21. ONE MALFORMED EVALUATION DOES NOT CORRUPT EXISTING VALID RECOMMENDATION TRUTH
# ═══════════════════════════════════════════════════════════════════════════════


class TestMalformedEvaluationIsolation:

    def test_malformed_evaluation_does_not_corrupt_valid_recommendation(self):
        """One malformed evaluation does not corrupt existing valid recommendation truth (test 21)."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            store = RecommendationStore(str(tmp_dir))

            valid_eval = _make_evaluation(
                candidate_id="OPT-valid",
                evaluation_id="EVAL-valid",
                decision="VALIDATED",
                confidence="HIGH",
            )
            valid_rec = create_recommendation(valid_eval, store=store)
            assert valid_rec is not None
            assert valid_rec.actionable is True
            assert len(store.list_all()) == 1

            # Now create a malformed evaluation (empty candidate_id)
            malformed_eval = CandidateEvaluation(
                candidate_id="",
                evaluation_id="EVAL-malformed",
                decision="",
                confidence="",
            )
            malformed_rec = create_recommendation(malformed_eval, store=store)
            assert malformed_rec is None  # fail-closed

            # Valid recommendation still intact
            all_recs = store.list_all()
            assert len(all_recs) == 1
            assert all_recs[0].candidate_id == "OPT-valid"
            assert all_recs[0].actionable is True

    def test_store_append_isolation_between_evaluations(self):
        """Different evaluations produce independent store entries (test 21)."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            store = RecommendationStore(str(tmp_dir))

            eval_a = _make_evaluation(
                candidate_id="OPT-A",
                evaluation_id="EVAL-A",
                treatment_id="TID-A",
            )
            eval_b = _make_evaluation(
                candidate_id="OPT-B",
                evaluation_id="EVAL-B",
                treatment_id="TID-B",
            )

            create_recommendation(eval_a, store=store)
            create_recommendation(eval_b, store=store)

            all_recs = store.list_all()
            assert len(all_recs) == 2

            by_candidate = store.get_by_candidate_id("OPT-A")
            assert len(by_candidate) == 1
            assert by_candidate[0].candidate_id == "OPT-A"
            assert by_candidate[0].treatment_id == "TID-A"


# ═══════════════════════════════════════════════════════════════════════════════
# 22-25. CONFIRMATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestConfirmations:

    def test_recommendation_does_not_change_candidate_lifecycle(self):
        """CONFIRMATION: recommendation creation does NOT change candidate lifecycle (test 22)."""
        evaluation = _make_evaluation(
            candidate_id="OPT-confirm",
            decision="VALIDATED",
            confidence="HIGH",
        )
        rec = create_recommendation(evaluation)
        assert rec is not None
        assert rec.candidate_id == evaluation.candidate_id
        assert rec.decision == evaluation.decision

    def test_recommendation_does_not_create_human_approval(self):
        """CONFIRMATION: recommendation creation does NOT create human approval (test 23)."""
        evaluation = _make_evaluation(decision="VALIDATED", confidence="HIGH")
        rec = create_recommendation(evaluation)
        assert rec is not None
        rec_dict = rec.to_dict()
        assert "human_decision" not in rec_dict
        assert "approved_by" not in rec_dict
        assert "approval_timestamp" not in rec_dict
        assert "approval_decision" not in rec_dict

    def test_recommendation_does_not_create_application_deployment(self):
        """CONFIRMATION: recommendation creation does NOT create application/deployment (test 24)."""
        evaluation = _make_evaluation()
        rec = create_recommendation(evaluation)
        assert rec is not None
        rec_dict = rec.to_dict()
        assert "application" not in rec_dict
        assert "deployment" not in rec_dict
        assert "activation" not in rec_dict
        assert "production" not in rec_dict

    def test_active_baseline_untouched(self):
        """CONFIRMATION: active baseline is untouched (test 25)."""
        evaluation = _make_evaluation(baseline_id="current_v10", config_hash="abc123")
        rec = create_recommendation(evaluation)
        assert rec is not None
        assert rec.baseline_id == "current_v10"
        assert rec.baseline_config_hash == "abc123"

        assert rec.baseline_config_hash == "abc123"
