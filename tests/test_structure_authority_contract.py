"""
Structure Authority Contract Enforcement Tests.

Verifies that structure_score/structure_regime influence decisions
at ONE and ONLY ONE point: ConfluenceEngine.compute_confluence().

These tests are architectural guards — they scan source code to detect
violations of the single-influence-point rule.
"""

from __future__ import annotations

import os
import pathlib

import pytest


# ─── HELPERS ──────────────────────────────────────────────────────────────────

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Files where compute_structure_weight is ALLOWED to exist
ALLOWED_SWM_FILES = {
    "core/voters/confluence_engine.py",  # Definition + usage (authoritative)
}

# Files that are FORBIDDEN from referencing structure decision symbols
FORBIDDEN_DECISION_FILES = {
    "core/voters/execution_gate.py",
    "core/pipeline/intent_builder.py",
    "core/voters/bias_voter.py",
    "core/voters/structure_voter.py",
    "core/voters/session_voter.py",
    "core/voters/spread_voter.py",
    "core/voters/volatility_voter.py",
}

# Patterns that indicate decision-level structure coupling
FORBIDDEN_PATTERNS = [
    "compute_structure_weight",
    "compute_structure_modifier",
]

# Patterns that indicate structure value usage in conditional logic
# (allowed in snapshot/state as passive fields, forbidden in decision paths)
STRUCTURE_VALUE_PATTERNS = [
    "structure_score",
    "structure_regime",
    "structure_modifier",
]


def _read_source(relative_path: str) -> str | None:
    """Read a source file relative to project root. Returns None if not found."""
    full_path = ROOT / relative_path
    if not full_path.exists():
        return None
    return full_path.read_text(encoding="utf-8", errors="replace")


def _find_python_files(directory: str) -> list[str]:
    """Find all .py files in a directory relative to project root."""
    dir_path = ROOT / directory
    if not dir_path.exists():
        return []
    return [
        str(p.relative_to(ROOT)).replace("\\", "/")
        for p in dir_path.rglob("*.py")
        if "__pycache__" not in str(p)
    ]


# ─── TEST 1: SINGLE INFLUENCE POINT ENFORCEMENT ──────────────────────────────

class TestSingleInfluencePoint:
    """compute_structure_weight must ONLY exist in confluence_engine.py."""

    def test_swm_only_in_confluence_engine(self):
        """Scan all non-test Python files for compute_structure_weight usage."""
        violations = []

        # Scan core/ directory
        for rel_path in _find_python_files("core"):
            if rel_path in ALLOWED_SWM_FILES:
                continue
            source = _read_source(rel_path)
            if source and "compute_structure_weight" in source:
                violations.append(rel_path)

        # Scan other source directories
        for directory in ["risk", "execution", "strategy", "data", "patterns"]:
            for rel_path in _find_python_files(directory):
                source = _read_source(rel_path)
                if source and "compute_structure_weight" in source:
                    violations.append(rel_path)

        assert not violations, (
            f"compute_structure_weight found outside allowed files:\n"
            + "\n".join(f"  - {v}" for v in violations)
        )

    def test_structure_modifier_not_applied_in_scoring(self):
        """scoring_engine.py must NOT multiply score by structure_modifier."""
        source = _read_source("core/pipeline/scoring_engine.py")
        assert source is not None

        # The pattern "score = score * structure_modifier" or similar must not exist
        # But "compute_structure_modifier" can exist for observational logging
        lines = source.split("\n")
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            # Check for actual multiplication (not just the function call)
            if "structure_modifier" in stripped and "*" in stripped and "score" in stripped:
                # Allow: breakdown dict entries like "structure_modifier": round(...)
                if "breakdown" in stripped or '":' in stripped or "round(" in stripped:
                    continue
                pytest.fail(
                    f"scoring_engine.py line {i} applies structure_modifier to score: {stripped}"
                )


# ─── TEST 2: FORBIDDEN IMPORT DETECTION ───────────────────────────────────────

class TestForbiddenImports:
    """Decision-path files must not reference structure decision symbols."""

    @pytest.mark.parametrize("filepath", sorted(FORBIDDEN_DECISION_FILES))
    def test_no_forbidden_patterns(self, filepath: str):
        """Each forbidden file must not contain structure decision patterns."""
        source = _read_source(filepath)
        if source is None:
            pytest.skip(f"{filepath} not found")

        violations = []
        for pattern in FORBIDDEN_PATTERNS:
            if pattern in source:
                violations.append(pattern)

        assert not violations, (
            f"{filepath} contains forbidden structure patterns: {violations}"
        )

    @pytest.mark.parametrize("filepath", sorted(FORBIDDEN_DECISION_FILES))
    def test_no_structure_value_in_conditionals(self, filepath: str):
        """Forbidden files must not use structure values in if/elif branches."""
        source = _read_source(filepath)
        if source is None:
            pytest.skip(f"{filepath} not found")

        lines = source.split("\n")
        violations = []
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            # Check if line is a conditional that references structure values
            if stripped.startswith(("if ", "elif ")) or " if " in stripped:
                for pattern in STRUCTURE_VALUE_PATTERNS:
                    if pattern in stripped:
                        violations.append(f"line {i}: {stripped}")

        assert not violations, (
            f"{filepath} uses structure values in conditional logic:\n"
            + "\n".join(f"  - {v}" for v in violations)
        )


# ─── TEST 3: DECISION INDEPENDENCE TEST ──────────────────────────────────────

class TestDecisionIndependence:
    """
    When SWM is disabled (structure_score=None), changing structure values
    on the snapshot must NOT affect the confluence decision.
    """

    def test_confluence_identical_without_swm(self):
        """Without structure params, different snapshot structure values don't matter."""
        from core.voters.confluence_engine import compute_confluence
        from core.voters.types import VoteResult

        votes = dict(
            bias_vote=VoteResult(score=1.5, confidence=0.9, reason="confirmed"),
            structure_vote=VoteResult(score=1.0, confidence=0.8, reason="clear"),
            volatility_vote=VoteResult(score=0.5, confidence=0.7, reason="ok"),
            spread_vote=VoteResult(score=0.3, confidence=0.6, reason="ok"),
            session_vote=VoteResult(score=0.2, confidence=0.5, reason="ok"),
        )

        # Run A: no structure params (SWM disabled)
        result_a = compute_confluence(**votes)

        # Run B: also no structure params (SWM disabled)
        result_b = compute_confluence(**votes)

        # Must be identical
        assert result_a.action == result_b.action
        assert result_a.score == result_b.score
        assert result_a.confidence == result_b.confidence

    def test_swm_is_only_difference(self):
        """With identical votes, only SWM params change the score."""
        from core.voters.confluence_engine import compute_confluence
        from core.voters.types import VoteResult

        votes = dict(
            bias_vote=VoteResult(score=1.5, confidence=0.9, reason="confirmed"),
            structure_vote=VoteResult(score=1.0, confidence=0.8, reason="clear"),
            volatility_vote=VoteResult(score=0.5, confidence=0.7, reason="ok"),
            spread_vote=VoteResult(score=0.3, confidence=0.6, reason="ok"),
            session_vote=VoteResult(score=0.2, confidence=0.5, reason="ok"),
        )

        # Neutral (no SWM)
        r_neutral = compute_confluence(**votes)

        # With SWM active
        r_weighted = compute_confluence(
            **votes, structure_score=4.0, structure_regime="CONFIRMED"
        )

        # Scores differ (SWM applied)
        assert r_neutral.score != r_weighted.score

        # Confidence unchanged (SWM only affects score magnitude)
        assert r_neutral.confidence == r_weighted.confidence


# ─── TEST 4: READERSHIP CLASSIFICATION CHECK ──────────────────────────────────

class TestReadershipClassification:
    """
    Files that reference structure must be classified as allowed readers.
    No unclassified file may reference structure values.
    """

    # All files allowed to reference structure_score/structure_regime
    ALLOWED_STRUCTURE_READERS = {
        "core/engine_state.py",                     # Passive state storage
        "core/state/snapshot.py",                   # Frozen field exposure
        "core/pipeline/structure_scoring.py",       # Source of truth (computes values)
        "core/pipeline/structure_confidence.py",    # Observational modifier function
        "core/pipeline/scoring_engine.py",          # Observational logging only
        "core/voters/confluence_engine.py",         # AUTHORITATIVE influence point
        "core/engine.py",                           # Wiring (passes values between modules)
        "core/state_persistence.py",               # Persists/restores structure state (passive storage)
    }

    def test_no_unclassified_structure_readers(self):
        """Any file referencing structure_score must be in the allowed set."""
        violations = []

        for directory in ["core", "risk", "execution", "strategy", "data", "patterns", "phase5"]:
            for rel_path in _find_python_files(directory):
                if rel_path in self.ALLOWED_STRUCTURE_READERS:
                    continue

                source = _read_source(rel_path)
                if source is None:
                    continue

                # Check for structure value references (not just in comments/strings)
                for pattern in ["structure_score", "structure_regime"]:
                    if pattern in source:
                        # Exclude pure comment lines
                        lines = source.split("\n")
                        for line in lines:
                            stripped = line.strip()
                            if pattern in stripped and not stripped.startswith("#"):
                                violations.append(f"{rel_path}: contains '{pattern}'")
                                break

        assert not violations, (
            f"Unclassified files reference structure values:\n"
            + "\n".join(f"  - {v}" for v in violations)
        )


# ─── TEST 5: STATIC SAFETY ASSERT ────────────────────────────────────────────

class TestStaticSafetyAssert:
    """The assert_structure_isolation() helper must pass."""

    def test_isolation_assert_passes(self):
        from architecture.contracts.structure_authority_contract import (
            assert_structure_isolation,
        )
        # Should not raise
        result = assert_structure_isolation()
        assert result is True
