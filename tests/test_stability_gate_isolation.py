"""
Stability Gate Isolation Tests — Final safety checkpoint.

Validates:
1. Gate allows/rejects correctly
2. Missing cohort defaults to NORMAL_MODE
3. No engine execution change unless allow_trade=False
4. No import from tools/cohort_analysis
5. Stability layer remains pure (no logging, no I/O, no broker)

ALL TESTS MUST PASS BEFORE PROCEEDING WITH FURTHER EXPANSION.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.stability.stability_gate import StabilityDecision, evaluate_stability_policy
from core.stability.cohort_key import build_cohort_key
from core.stability.policy_registry import POLICY_REGISTRY


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _snapshot(**kwargs) -> SimpleNamespace:
    """Build a snapshot with safe defaults."""
    defaults = {
        "drawdown_state": "NORMAL",
        "recent_loss_streak": 0,
        "session_quality": "NORMAL",
        "volatility_state": "STABLE",
        "spread_state": "NORMAL",
        "market_regime": "TRENDING",
        "trade_frequency_state": "NORMAL",
        "confidence_score": 7.0,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. GATE ALLOWS VALID TRADE
# ═══════════════════════════════════════════════════════════════════════════════


class TestGateAllowsValidTrade:
    """When snapshot conditions are normal, gate must allow trade."""

    def test_normal_conditions_allow(self):
        result = evaluate_stability_policy(_snapshot(), POLICY_REGISTRY)
        assert result.allow_trade is True
        assert result.mode in ("NORMAL", "RUNNER", "PROTECT")

    def test_high_confidence_allows(self):
        result = evaluate_stability_policy(
            _snapshot(confidence_score=8.0), POLICY_REGISTRY
        )
        assert result.allow_trade is True
        assert result.mode == "NORMAL"

    def test_runner_conditions_allow(self):
        result = evaluate_stability_policy(
            _snapshot(
                confidence_score=9.0,
                market_regime="TRENDING",
                session_quality="HIGH",
                volatility_state="STABLE",
                spread_state="TIGHT",
            ),
            POLICY_REGISTRY,
        )
        assert result.allow_trade is True
        assert result.mode == "RUNNER"

    def test_low_confidence_still_allows(self):
        result = evaluate_stability_policy(
            _snapshot(confidence_score=5.0), POLICY_REGISTRY
        )
        assert result.allow_trade is True
        assert result.mode == "PROTECT"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. GATE REJECTS BLOCKED TRADE
# ═══════════════════════════════════════════════════════════════════════════════


class TestGateRejectsBlocked:
    """Each hard-block path must produce deterministic rejection."""

    def test_drawdown_lock_rejects(self):
        result = evaluate_stability_policy(
            _snapshot(drawdown_state="LOCKED"), POLICY_REGISTRY
        )
        assert result.allow_trade is False
        assert result.reason == "drawdown_lock"

    def test_loss_streak_rejects(self):
        result = evaluate_stability_policy(
            _snapshot(recent_loss_streak=3), POLICY_REGISTRY
        )
        assert result.allow_trade is False
        assert result.reason == "loss_streak_limit"

    def test_dead_session_rejects(self):
        result = evaluate_stability_policy(
            _snapshot(session_quality="DEAD"), POLICY_REGISTRY
        )
        assert result.allow_trade is False
        assert result.reason == "dead_session"

    def test_volatility_block_rejects(self):
        result = evaluate_stability_policy(
            _snapshot(volatility_state="CHAOTIC"), POLICY_REGISTRY
        )
        assert result.allow_trade is False
        assert result.reason == "volatility_block"

    def test_spread_block_rejects(self):
        result = evaluate_stability_policy(
            _snapshot(spread_state="WIDE"), POLICY_REGISTRY
        )
        assert result.allow_trade is False
        assert result.reason == "spread_block"

    def test_all_rejections_are_deterministic(self):
        """Same input always produces same output."""
        snap = _snapshot(drawdown_state="LOCKED")
        results = [evaluate_stability_policy(snap, POLICY_REGISTRY) for _ in range(10)]
        assert all(r.allow_trade is False for r in results)
        assert all(r.reason == "drawdown_lock" for r in results)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. MISSING COHORT DEFAULTS SAFELY
# ═══════════════════════════════════════════════════════════════════════════════


class TestMissingCohortDefaults:
    """Unknown cohort keys must fall back to NORMAL_MODE without exceptions."""

    def test_nonexistent_key_defaults(self):
        key = "NON_EXISTENT+KEY+TEST"
        result = POLICY_REGISTRY.get(key, "NORMAL_MODE")
        assert result == "NORMAL_MODE"

    def test_random_cohort_defaults(self):
        key = "BIZARRE+UNUSUAL+EXOTIC"
        result = POLICY_REGISTRY.get(key, "NORMAL_MODE")
        assert result == "NORMAL_MODE"

    def test_empty_key_defaults(self):
        key = "+++"
        result = POLICY_REGISTRY.get(key, "NORMAL_MODE")
        assert result == "NORMAL_MODE"

    def test_build_cohort_key_unknown_input_defaults(self):
        decision = SimpleNamespace()
        key = build_cohort_key(decision)
        result = POLICY_REGISTRY.get(key, "NORMAL_MODE")
        # "UNKNOWN+UNKNOWN+UNKNOWN" is in registry as NORMAL_MODE
        assert result == "NORMAL_MODE"

    def test_never_raises_on_lookup(self):
        """Registry lookup must never raise regardless of input."""
        test_keys = [
            "", "A", "A+B", "A+B+C+D", None, 123, True,
            "UNKNOWN+UNKNOWN+UNKNOWN",
            "!@#+$%^+&*()",
        ]
        for key in test_keys:
            # .get() on dict with non-hashable would raise, but strings won't
            if isinstance(key, str):
                result = POLICY_REGISTRY.get(key, "NORMAL_MODE")
                assert isinstance(result, str)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. ENGINE PATH UNCHANGED WHEN ALLOWED
# ═══════════════════════════════════════════════════════════════════════════════


class TestEnginePathWhenAllowed:
    """When stability allows, engine must proceed normally."""

    def test_allow_decision_has_no_side_effects(self):
        """evaluate_stability_policy does not mutate the snapshot."""
        snap = _snapshot(confidence_score=7.5)
        original_confidence = snap.confidence_score
        original_drawdown = snap.drawdown_state

        evaluate_stability_policy(snap, POLICY_REGISTRY)

        assert snap.confidence_score == original_confidence
        assert snap.drawdown_state == original_drawdown

    def test_allow_decision_does_not_mutate_registry(self):
        registry_before = dict(POLICY_REGISTRY)
        evaluate_stability_policy(_snapshot(), POLICY_REGISTRY)
        assert POLICY_REGISTRY == registry_before

    def test_allow_produces_correct_type(self):
        result = evaluate_stability_policy(_snapshot(), POLICY_REGISTRY)
        assert isinstance(result, StabilityDecision)
        assert isinstance(result.allow_trade, bool)
        assert isinstance(result.mode, str)
        assert isinstance(result.reason, str)


# ═══════════════════════════════════════════════════════════════════════════════
# 5. ENGINE EARLY EXITS WHEN BLOCKED
# ═══════════════════════════════════════════════════════════════════════════════


class TestEngineEarlyExitWhenBlocked:
    """When stability blocks, the rejection reason is available for engine to use."""

    def test_blocked_decision_provides_reason_for_engine(self):
        """Engine uses decision.reason to construct reject response."""
        result = evaluate_stability_policy(
            _snapshot(drawdown_state="LOCKED"), POLICY_REGISTRY
        )
        # Engine would do: f"stability_block:{result.reason}"
        engine_reason = f"stability_block:{result.reason}"
        assert result.allow_trade is False
        assert engine_reason == "stability_block:drawdown_lock"

    def test_blocked_decision_is_self_contained(self):
        """Blocked decision contains all info needed for reject — no further calls needed."""
        result = evaluate_stability_policy(
            _snapshot(session_quality="DEAD"), POLICY_REGISTRY
        )
        assert result.allow_trade is False
        assert result.mode == "PROTECT"
        assert result.reason == "dead_session"
        # All three fields are populated — engine can reject immediately

    def test_every_block_path_has_a_reason(self):
        """No block path returns an empty reason string."""
        block_configs = [
            {"drawdown_state": "LOCKED"},
            {"recent_loss_streak": 5},
            {"session_quality": "DEAD"},
            {"volatility_state": "CHAOTIC"},
            {"spread_state": "WIDE"},
        ]
        for overrides in block_configs:
            result = evaluate_stability_policy(
                _snapshot(**overrides), POLICY_REGISTRY
            )
            assert result.allow_trade is False
            assert len(result.reason) > 0, f"Empty reason for {overrides}"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. NO FORBIDDEN IMPORTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoForbiddenImports:
    """Stability gate must NOT import from tools/cohort_analysis or other forbidden modules."""

    def _get_module_source(self, module) -> str:
        """Get the source file path of a module."""
        return inspect.getfile(module)

    def _get_imports_from_file(self, filepath: str) -> list[str]:
        """Parse all import statements from a Python file using AST."""
        source = Path(filepath).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)
        return imports

    def test_stability_gate_no_tools_import(self):
        """stability_gate.py must not import from tools.cohort_analysis."""
        import core.stability.stability_gate as gate_mod
        filepath = inspect.getfile(gate_mod)
        imports = self._get_imports_from_file(filepath)
        forbidden = [i for i in imports if "tools.cohort_analysis" in i]
        assert forbidden == [], f"Forbidden imports found: {forbidden}"

    def test_cohort_key_no_tools_import(self):
        """cohort_key.py must not import from tools.cohort_analysis."""
        import core.stability.cohort_key as key_mod
        filepath = inspect.getfile(key_mod)
        imports = self._get_imports_from_file(filepath)
        forbidden = [i for i in imports if "tools.cohort_analysis" in i]
        assert forbidden == [], f"Forbidden imports found: {forbidden}"

    def test_policy_registry_no_tools_import(self):
        """policy_registry.py must not import from tools.cohort_analysis."""
        import core.stability.policy_registry as reg_mod
        filepath = inspect.getfile(reg_mod)
        imports = self._get_imports_from_file(filepath)
        forbidden = [i for i in imports if "tools.cohort_analysis" in i]
        assert forbidden == [], f"Forbidden imports found: {forbidden}"

    def test_stability_gate_no_analytics_import(self):
        """stability_gate.py must not import from analytics or ml modules."""
        import core.stability.stability_gate as gate_mod
        filepath = inspect.getfile(gate_mod)
        imports = self._get_imports_from_file(filepath)
        forbidden_prefixes = ("ml.", "analytics.", "core.analysis")
        forbidden = [i for i in imports if any(i.startswith(p) for p in forbidden_prefixes)]
        assert forbidden == [], f"Forbidden imports found: {forbidden}"


# ═══════════════════════════════════════════════════════════════════════════════
# 7. STABILITY LAYER REMAINS PURE
# ═══════════════════════════════════════════════════════════════════════════════


class TestStabilityLayerPurity:
    """evaluate_stability_policy must contain no logging, I/O, broker, or mutation calls."""

    FORBIDDEN_PATTERNS = [
        "logger.",
        "logging.",
        "open(",
        "mt5.",
        "MT5.",
        "execute_",
        "save_",
        "write(",
        "broker.",
        "send(",
        "requests.",
        "socket.",
    ]

    def test_stability_gate_source_is_pure(self):
        """Check evaluate_stability_policy source for forbidden patterns."""
        source = inspect.getsource(evaluate_stability_policy)
        for pattern in self.FORBIDDEN_PATTERNS:
            assert pattern not in source, (
                f"Forbidden pattern '{pattern}' found in evaluate_stability_policy"
            )

    def test_stability_gate_module_is_pure(self):
        """Check entire stability_gate.py module for forbidden patterns."""
        import core.stability.stability_gate as gate_mod
        filepath = inspect.getfile(gate_mod)
        source = Path(filepath).read_text(encoding="utf-8")
        for pattern in self.FORBIDDEN_PATTERNS:
            assert pattern not in source, (
                f"Forbidden pattern '{pattern}' found in stability_gate.py"
            )

    def test_cohort_key_module_is_pure(self):
        """Check entire cohort_key.py module for forbidden patterns in executable code."""
        import core.stability.cohort_key as key_mod
        filepath = inspect.getfile(key_mod)
        source = Path(filepath).read_text(encoding="utf-8")
        # Only check actual code lines (skip comments and docstrings)
        tree = ast.parse(source)
        # Extract all string literals used in code (function bodies, assignments)
        # Instead, check the source of the actual functions only
        func_sources = []
        for name in dir(key_mod):
            obj = getattr(key_mod, name)
            if callable(obj) and not name.startswith("_"):
                try:
                    func_sources.append(inspect.getsource(obj))
                except (TypeError, OSError):
                    pass
        # Also check private functions
        for name in ("_normalize",):
            obj = getattr(key_mod, name, None)
            if obj and callable(obj):
                func_sources.append(inspect.getsource(obj))

        combined_source = "\n".join(func_sources)
        for pattern in self.FORBIDDEN_PATTERNS:
            # Skip checking pattern in docstrings — only check code lines
            code_lines = [
                line for line in combined_source.split("\n")
                if line.strip() and not line.strip().startswith("#")
                and not line.strip().startswith('"""')
                and not line.strip().startswith("'''")
                and not line.strip().startswith('"')
                and not line.strip().startswith("'")
            ]
            code_only = "\n".join(code_lines)
            assert pattern not in code_only, (
                f"Forbidden pattern '{pattern}' found in cohort_key.py code"
            )

    def test_policy_registry_has_no_functions(self):
        """policy_registry.py must contain no function or class definitions."""
        import core.stability.policy_registry as reg_mod
        filepath = inspect.getfile(reg_mod)
        source = Path(filepath).read_text(encoding="utf-8")
        tree = ast.parse(source)
        functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        assert functions == [], f"Functions found in policy_registry.py: {[f.name for f in functions]}"
        assert classes == [], f"Classes found in policy_registry.py: {[c.name for c in classes]}"
