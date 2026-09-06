"""
Gap 9 — single-source-of-truth question inventory guard tests.

Proves the canonical inventory (research_engine.registry.research_question_registry)
owns every executable research question, that runner discovery and the weekly
cycle agree with it, that the legacy NEW-ENGINE bank is a read-only coverage
input (aliases resolve, no runners, no duplicates), and that a synthetic
duplicate/unregistered question FAILS the guard.

No real AWS required.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import research_engine.registry.inventory_guard as guard
from research_engine.registry.inventory_guard import (
    DOCUMENTED_UNMIGRATED_INTENTS,
    QuestionInventoryViolation,
    assert_legacy_bank_is_read_only_coverage,
    assert_runners_match_registry,
    canonical_questions,
    executable_canonical_questions,
)


# ═══════════════════════════════════════════════════════════════════════════════
# CANONICAL INVENTORY
# ═══════════════════════════════════════════════════════════════════════════════


class TestCanonicalInventory:
    def test_unique_ids(self):
        questions = canonical_questions()  # raises on duplicates
        assert len(questions) >= 55   # real size, not a magic count
        assert len(questions) == len(set(questions))

    def test_every_executable_canonical_question_has_a_runner(self):
        from research_engine.runner_discovery import get_all_runners
        runners = get_all_runners()
        assert_runners_match_registry(runners)   # raises on mismatch
        assert len(runners) >= 25                # sanity floor, not a magic count

    def test_weekly_cycle_uses_the_canonical_inventory(self):
        """The weekly ResearchCycleRunner must not import ANY question bank —
        its findings feed the lifecycle through the canonical registry chain
        (runner_discovery/orchestrator), never a second inventory."""
        import inspect
        from research_engine.lifecycle import research_cycle_runner as rcr
        source = inspect.getsource(rcr)
        assert "question_bank" not in source
        assert "legacy_question_bank" not in source
        # and runner_discovery provably imports the canonical registry
        import research_engine.runner_discovery as rd
        rd_source = inspect.getsource(rd)
        assert "research_question_registry import REGISTRY" in rd_source

    def test_gap4_status_contract_intact(self):
        """Registry questions still carry the report contract semantics:
        status is authoritative, recommendation separate (Gap 4)."""
        from research_engine.experiments.research_runner import _extract_run_status
        report = {
            "question_id": "E3", "status": "INSUFFICIENT_DATA",
            "recommendation": "COMPLETE", "dataset": {"sample_size": 0},
        }
        status, source = _extract_run_status(report)
        assert (status, source) == ("INSUFFICIENT_DATA", "report")


# ═══════════════════════════════════════════════════════════════════════════════
# LEGACY BANK — read-only coverage input
# ═══════════════════════════════════════════════════════════════════════════════


class TestLegacyBank:
    def _load(self):
        from research_engine.v10.universes.legacy_question_bank import (
            QUESTION_BANK, RETIRED_QUESTIONS,
        )
        return list(QUESTION_BANK), list(RETIRED_QUESTIONS)

    def test_legacy_bank_is_read_only_coverage(self):
        bank, retired = self._load()
        assert_legacy_bank_is_read_only_coverage(bank, retired)   # raises on drift
        assert len(bank) >= 45

    def test_legacy_bank_defines_no_runners(self):
        from research_engine.v10.universes.models import NewEngineQuestion
        runner_fields = [f for f in ("runner_module", "runner_function", "runner")
                         if hasattr(NewEngineQuestion, f)]
        assert runner_fields == []

    def test_documented_unmigrated_intents_are_exactly_the_known_four(self):
        bank, _ = self._load()
        canonical = canonical_questions()
        unaliased = sorted(
            q.question_id for q in bank
            if not (set(q.source_intent or ()) & set(canonical)))
        assert set(unaliased) == set(DOCUMENTED_UNMIGRATED_INTENTS)

    def test_aliases_resolve_to_canonical_ids(self):
        """Every alias either resolves to a canonical ID (directly or via the
        canonical `legacy_ids` mapping) or is a recognised historical label
        (V10-/Lambda-/campaign/bank-internal cross-refs). A typo'd
        canonical-looking alias fails this test."""
        bank, _ = self._load()
        canonical = canonical_questions()
        # canonical questions carry legacy Q1-Q25 ids — valid alias targets
        legacy_ids = set()
        for q in canonical.values():
            legacy_ids.update(getattr(q, "legacy_ids", ()) or ())
        known_historical = {"EX_A", "EX_B", "EX_C"}
        for q in bank:
            for alias in (q.source_intent or ()):
                if alias in canonical or q.question_id in DOCUMENTED_UNMIGRATED_INTENTS:
                    continue
                is_historical = (
                    alias in legacy_ids
                    or alias.startswith(("V10-", "Lambda-"))
                    or alias in known_historical
                    or alias.endswith("-shadow")
                    or re.fullmatch(r"[A-Z]{1,4}-\d{3}", alias)  # bank-internal cross-ref
                )
                assert is_historical, \
                    f"{q.question_id} aliases unrecognised id {alias!r}"

    def test_retired_questions_cannot_execute(self):
        from research_engine.runner_discovery import get_all_runners
        _, retired = self._load()
        runners = get_all_runners()
        for q in retired:
            assert q.question_id not in runners
            assert q.question_id not in canonical_questions()


# ═══════════════════════════════════════════════════════════════════════════════
# GUARD FAILS ON SYNTHETIC VIOLATIONS
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class _FakeRunner:
    runner_module: str = "some.module"
    runner_function: str = "run"


class TestGuardFailsOnViolations:
    def test_unregistered_runner_fails(self):
        with pytest.raises(QuestionInventoryViolation, match="without a canonical question"):
            assert_runners_match_registry({"ZZ-999": _FakeRunner(), **{
                qid: _FakeRunner() for qid in executable_canonical_questions()}})

    def test_canonical_executable_without_runner_fails(self):
        partial = {qid: _FakeRunner() for qid in
                   list(executable_canonical_questions())[:-1]}
        with pytest.raises(QuestionInventoryViolation, match="without a runner"):
            assert_runners_match_registry(partial)

    def test_duplicate_canonical_id_fails(self, monkeypatch):
        from research_engine.registry.research_question_registry import REGISTRY
        first = REGISTRY[0]
        double = type(first)(**{**first.__dict__, "runner_module": "", "runner_function": ""})
        monkeypatch.setattr(guard, "REGISTRY", REGISTRY + (double,))
        with pytest.raises(QuestionInventoryViolation, match="duplicate canonical"):
            guard.canonical_questions()

    def test_legacy_bank_with_runner_field_fails(self):
        @dataclass
        class _RogueBankQuestion:
            question_id: str = "X-001"
            source_intent: tuple = ("E1",)
            runner_module: str = "rogue.module"   # the drift this guard prevents

        with pytest.raises(QuestionInventoryViolation, match="runner fields"):
            assert_legacy_bank_is_read_only_coverage([_RogueBankQuestion()])

    def test_new_unaliased_bank_question_fails(self):
        from research_engine.v10.universes.models import NewEngineQuestion

        @dataclass
        class _BankQ:
            question_id: str
            source_intent: tuple

        base, _ = _load_bank()
        rogue = _BankQ(question_id="NEW-001", source_intent=("NOT-A-CANONICAL-ID",))
        with pytest.raises(QuestionInventoryViolation, match="no canonical alias"):
            assert_legacy_bank_is_read_only_coverage(base + [rogue])

    def test_duplicate_bank_id_fails(self):
        bank, _ = _load_bank()
        with pytest.raises(QuestionInventoryViolation, match="duplicate id"):
            assert_legacy_bank_is_read_only_coverage(bank + [bank[0]])


def _load_bank():
    from research_engine.v10.universes.legacy_question_bank import (
        QUESTION_BANK, RETIRED_QUESTIONS,
    )
    bank = [
        type("BankQ", (), {"question_id": q.question_id,
                           "source_intent": tuple(q.source_intent or ())})()
        for q in QUESTION_BANK
    ]
    retired = [
        type("BankQ", (), {"question_id": q.question_id,
                           "source_intent": tuple(q.source_intent or ())})()
        for q in RETIRED_QUESTIONS
    ]
    return bank, retired
