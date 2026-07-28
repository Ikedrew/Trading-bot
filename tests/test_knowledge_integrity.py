"""
Tests for Research Knowledge Integrity.

Verifies:
    - Invalidated findings cannot become implementation actions
    - Current findings override obsolete findings
    - Promotion gates respect finding status
    - Knowledge structure is correct
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research_engine.knowledge_integrity import (
    PROMOTION_BLOCKED_STATUSES,
    PROMOTABLE_STATUSES,
    get_finding_status,
    get_invalidated_findings,
    get_promotable_findings,
    get_promotion_blockers,
    is_invalidated,
    is_promotable,
    load_knowledge,
    validate_promotion_attempt,
)


class TestInvalidatedFindingsCannotPromote:
    """Invalidated findings must be blocked from becoming implementation actions."""

    def test_q19_is_invalidated(self):
        """Q19 positive EV finding is marked INVALIDATED."""
        assert is_invalidated("Q19") is True

    def test_r3_is_invalidated(self):
        """R3 probability of ruin is marked INVALIDATED."""
        assert is_invalidated("R3") is True

    def test_r4_is_invalidated(self):
        """R4 drawdown recommendation is marked INVALIDATED."""
        assert is_invalidated("R4") is True

    def test_r5_is_invalidated(self):
        """R5 position sizing is marked INVALIDATED."""
        assert is_invalidated("R5") is True

    def test_invalidated_cannot_promote_q19(self):
        """Q19 cannot be promoted."""
        assert is_promotable("Q19") is False

    def test_invalidated_cannot_promote_r3(self):
        """R3 cannot be promoted."""
        assert is_promotable("R3") is False

    def test_invalidated_cannot_promote_r4(self):
        """R4 cannot be promoted."""
        assert is_promotable("R4") is False

    def test_invalidated_cannot_promote_r5(self):
        """R5 cannot be promoted."""
        assert is_promotable("R5") is False

    def test_promotion_attempt_blocked_with_reason(self):
        """Promotion attempt returns False with explanatory reason."""
        allowed, reason = validate_promotion_attempt("Q19")
        assert allowed is False
        assert "INVALIDATED" in reason
        assert "epoch" in reason.lower() or "mixed" in reason.lower()

    def test_all_invalidated_in_list(self):
        """All four invalidated findings appear in get_invalidated_findings()."""
        invalidated = get_invalidated_findings()
        assert "Q19" in invalidated
        assert "R3" in invalidated
        assert "R4" in invalidated
        assert "R5" in invalidated


class TestCurrentFindingsOverrideObsolete:
    """VALIDATED findings from CURRENT epoch override historical ones."""

    def test_m9_is_validated(self):
        """M9 (CURRENT epoch) is VALIDATED and promotable (if no global blocker)."""
        status = get_finding_status("M9")
        assert status == "VALIDATED"

    def test_m10_is_validated(self):
        """M10 (CURRENT epoch) is VALIDATED."""
        status = get_finding_status("M10")
        assert status == "VALIDATED"

    def test_requires_rerun_blocks_promotion(self):
        """REQUIRES_RERUN status blocks promotion."""
        status = get_finding_status("Q1")
        assert status == "REQUIRES_RERUN"
        assert is_promotable("Q1") is False

    def test_q4_requires_rerun(self):
        """Q4 calibration needs re-verification."""
        status = get_finding_status("Q4")
        assert status == "REQUIRES_RERUN"
        assert is_promotable("Q4") is False


class TestPromotionGates:
    """Promotion gates respect all status types."""

    def test_only_validated_is_promotable(self):
        """Only VALIDATED status allows promotion."""
        for status in PROMOTION_BLOCKED_STATUSES:
            assert status not in PROMOTABLE_STATUSES

    def test_promotable_statuses_are_correct(self):
        """Only VALIDATED is in PROMOTABLE_STATUSES."""
        assert PROMOTABLE_STATUSES == {"VALIDATED"}

    def test_global_blockers_prevent_all_promotion(self):
        """System-level promotion blockers block even VALIDATED findings."""
        blockers = get_promotion_blockers()
        # Current system has negative EV — promotion blocked
        assert len(blockers) > 0
        assert any("negative" in b.lower() or "ev" in b.lower() for b in blockers)

    def test_validated_finding_blocked_by_global_blocker(self):
        """M9 is VALIDATED but system blockers prevent promotion."""
        allowed, reason = validate_promotion_attempt("M9")
        # Should be blocked by system-level "EV is negative" blocker
        assert allowed is False
        assert "BLOCKED" in reason

    def test_unknown_finding_returns_not_found(self):
        """Unknown question ID returns NOT_FOUND."""
        status = get_finding_status("FAKE_QUESTION_99")
        assert status == "NOT_FOUND"
        assert is_promotable("FAKE_QUESTION_99") is False


class TestKnowledgeStructure:
    """Knowledge JSON has correct structure."""

    def test_knowledge_loads(self):
        """research_knowledge.json loads without error."""
        knowledge = load_knowledge()
        assert isinstance(knowledge, dict)

    def test_has_findings_section(self):
        """Knowledge has 'findings' dict."""
        knowledge = load_knowledge()
        assert "findings" in knowledge
        assert isinstance(knowledge["findings"], dict)

    def test_has_invalidated_findings_section(self):
        """Knowledge has 'invalidated_findings' list."""
        knowledge = load_knowledge()
        assert "invalidated_findings" in knowledge
        assert isinstance(knowledge["invalidated_findings"], list)

    def test_has_confirmed_facts(self):
        """Knowledge has CURRENT-epoch confirmed facts."""
        knowledge = load_knowledge()
        assert "confirmed_facts" in knowledge
        facts = knowledge["confirmed_facts"]
        # Should contain current truth
        assert any("-0.1999R" in f or "-0.20" in f for f in facts)

    def test_has_promotion_blockers(self):
        """Knowledge has promotion_blockers list."""
        knowledge = load_knowledge()
        assert "promotion_blockers" in knowledge
        assert len(knowledge["promotion_blockers"]) > 0

    def test_invalidated_findings_have_reasons(self):
        """Every invalidated finding has a reason."""
        knowledge = load_knowledge()
        findings = knowledge.get("findings", {})
        for qid, f in findings.items():
            if f.get("status") == "INVALIDATED":
                assert "reason" in f, f"{qid} missing reason"
                assert len(f["reason"]) > 10, f"{qid} reason too short"
                assert "invalidated_date" in f, f"{qid} missing date"

    def test_findings_have_epoch(self):
        """Every finding records its epoch."""
        knowledge = load_knowledge()
        findings = knowledge.get("findings", {})
        for qid, f in findings.items():
            assert "epoch" in f, f"{qid} missing epoch field"

    def test_current_epoch_ev_recorded(self):
        """Knowledge records the current system EV."""
        knowledge = load_knowledge()
        assert "current_epoch_ev" in knowledge
        assert knowledge["current_epoch_ev"] < 0  # Confirmed negative
