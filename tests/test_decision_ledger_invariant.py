"""
Decision Ledger Invariant Enforcement — Static Analysis Test.

Guarantees:
    ✓ Every `continue` after decision init has a preceding _finalize_decision()
    ✓ Only ONE _ledger.record() call exists (inside DecisionRecorder.finalize)
    ✓ No scattered ledger writes outside the finalization method
    ✓ DecisionRecorder.finalize() contains invariant enforcement
    ✓ DecisionRecorder.finalize() is idempotent
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
import inspect

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCANNER_PATH = ROOT / "core" / "runtime" / "live_scanner.py"
RECORDER_PATH = ROOT / "core" / "runtime" / "decision_recorder.py"


class TestDecisionLedgerInvariant:
    """Enforce that every exit path mutates _cycle_decision before finalization."""

    def _get_scanner_source(self) -> str:
        return SCANNER_PATH.read_text(encoding="utf-8")

    def _get_recorder_source(self) -> str:
        return RECORDER_PATH.read_text(encoding="utf-8")

    def test_single_ledger_record_call(self):
        """Only ONE _ledger.record() call should exist (inside DecisionRecorder.finalize)."""
        source = self._get_recorder_source()
        matches = re.findall(r"self\._ledger\.record\(", source)
        assert len(matches) == 1, (
            f"Expected exactly 1 self._ledger.record() call (inside finalize), "
            f"found {len(matches)}"
        )
        # Verify NO direct ledger.record() in live_scanner
        scanner_source = self._get_scanner_source()
        scanner_matches = re.findall(r"_ledger\.record\(", scanner_source)
        assert len(scanner_matches) == 0, (
            f"Expected 0 _ledger.record() in live_scanner.py (delegated to DecisionRecorder), "
            f"found {len(scanner_matches)}"
        )

    def test_no_scattered_ledger_writes(self):
        """_ledger.record() must ONLY appear inside DecisionRecorder.finalize()."""
        source = self._get_recorder_source()
        lines = source.splitlines()

        for i, line in enumerate(lines):
            if "self._ledger.record(" in line:
                # Look backward for finalize definition
                found_in_finalize = False
                for j in range(max(0, i - 50), i):
                    if "def finalize" in lines[j]:
                        found_in_finalize = True
                        break
                assert found_in_finalize, (
                    f"Line {i+1}: _ledger.record() found outside finalize()"
                )

    def test_every_continue_after_init_has_finalize(self):
        """
        Every `continue` statement after decision init must be preceded
        by _finalize_decision() within 25 lines.
        """
        source = self._get_scanner_source()
        lines = source.splitlines()

        # Find where _decision_recorder.init_cycle is called
        init_line = None
        for i, line in enumerate(lines):
            if '_decision_recorder.init_cycle(' in line:
                init_line = i
                break

        assert init_line is not None, "_decision_recorder.init_cycle() not found"

        # Find the per-symbol try block end
        per_symbol_except = None
        for i in range(init_line, len(lines)):
            if lines[i].strip().startswith("except Exception") and lines[i].startswith("              except"):
                per_symbol_except = i
                break

        if per_symbol_except is None:
            per_symbol_except = len(lines)

        # Check every `continue` between init and per-symbol-except
        violations = []
        for i in range(init_line, per_symbol_except):
            line = lines[i]
            stripped = line.strip()
            if stripped != "continue":
                continue

            # Look backward up to 25 lines for _finalize_decision()
            found_finalize = False
            for j in range(max(init_line, i - 25), i):
                if "_finalize_decision()" in lines[j]:
                    found_finalize = True
                    break

            if not found_finalize:
                # Check if this continue is inside the _finalize_decision def itself
                in_finalize_def = False
                for j in range(max(0, i - 10), i):
                    if "def _finalize_decision" in lines[j]:
                        in_finalize_def = True
                        break
                if not in_finalize_def:
                    violations.append(f"line {i+1}: `continue` without preceding _finalize_decision()")

        assert violations == [], (
            "INVARIANT VIOLATION: continue without _finalize_decision():\n"
            + "\n".join(f"  {v}" for v in violations)
        )

    def test_finalize_has_invariant_check(self):
        """DecisionRecorder.finalize() must contain an invariant assertion."""
        from core.runtime.decision_recorder import DecisionRecorder
        source = inspect.getsource(DecisionRecorder.finalize)
        assert 'decision") is None' in source or "decision_not_set" in source, (
            "DecisionRecorder.finalize() must contain a None-check on decision field"
        )

    def test_decision_enum_covers_all_outcomes(self):
        """DecisionOutcome enum must have entries for all used outcomes."""
        from core.decision_ledger import DecisionOutcome

        required = {
            "EXECUTE", "NO_TRADE", "RISK_BLOCK",
            "SESSION_BLOCK", "PATTERN_REJECT",
            "KILL_SWITCH", "DAILY_LOSS_BLOCK",
        }
        actual = {e.value for e in DecisionOutcome}
        missing = required - actual
        assert missing == set(), f"DecisionOutcome missing values: {missing}"

    def test_finalize_is_idempotent(self):
        """DecisionRecorder.finalize() must have an idempotency guard."""
        from core.runtime.decision_recorder import DecisionRecorder
        source = inspect.getsource(DecisionRecorder.finalize)
        assert "self._written" in source, (
            "DecisionRecorder.finalize() must check self._written for idempotency"
        )
        assert "return" in source, (
            "DecisionRecorder.finalize() must early-return if already written"
        )
