"""
Global Governance Isolation Tests — System-wide influence boundary enforcement.

Verifies that:
  - Structure has ONE influence point (ConfluenceEngine SWM)
  - Voters are pure (emit scores only, no execution logic)
  - Snapshot is immutable after creation
  - FeatureEngine is isolated (no decision logic)
  - ConfluenceEngine only aggregates (no execution logic)
  - Execution authority is isolated to EA modules only

These are architectural guards that prevent silent decision coupling.
"""

from __future__ import annotations

import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _read_source(relative_path: str) -> str | None:
    """Read source file relative to project root."""
    full_path = ROOT / relative_path
    if not full_path.exists():
        return None
    return full_path.read_text(encoding="utf-8", errors="replace")


def _find_python_files(directory: str) -> list[str]:
    """Find all .py files in directory relative to project root."""
    dir_path = ROOT / directory
    if not dir_path.exists():
        return []
    return [
        str(p.relative_to(ROOT)).replace("\\", "/")
        for p in dir_path.rglob("*.py")
        if "__pycache__" not in str(p)
    ]


# ─── TEST 1: GLOBAL STRUCTURE LEAK SCAN ──────────────────────────────────────

class TestGlobalStructureLeakScan:
    """No file except allowed readers may use structure in conditional logic."""

    # Files allowed to reference structure_score in ANY context
    ALLOWED_STRUCTURE_FILES = {
        "core/voters/confluence_engine.py",
        "core/pipeline/structure_scoring.py",
        "core/pipeline/structure_confidence.py",
        "core/pipeline/scoring_engine.py",
        "core/engine_state.py",
        "core/state/snapshot.py",
        "core/engine.py",
    }

    def test_no_structure_in_conditionals_outside_allowed(self):
        """structure_score/regime must not appear in if/elif outside allowed files."""
        violations = []
        structure_patterns = ["structure_score", "structure_regime"]

        for directory in ["core", "risk", "execution", "strategy", "patterns"]:
            for rel_path in _find_python_files(directory):
                if rel_path in self.ALLOWED_STRUCTURE_FILES:
                    continue

                source = _read_source(rel_path)
                if source is None:
                    continue

                lines = source.split("\n")
                for i, line in enumerate(lines, 1):
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        continue
                    # Check for conditional usage
                    if stripped.startswith(("if ", "elif ")) or " if " in stripped:
                        for pattern in structure_patterns:
                            if pattern in stripped:
                                violations.append(f"{rel_path}:{i}: {stripped}")

        assert not violations, (
            f"Structure values used in conditionals outside allowed files:\n"
            + "\n".join(f"  - {v}" for v in violations)
        )


# ─── TEST 2: VOTER PURITY TEST ───────────────────────────────────────────────

class TestVoterPurity:
    """Voters must be pure: emit VoteResult only, no execution logic."""

    VOTER_FILES = [
        "core/voters/bias_voter.py",
        "core/voters/structure_voter.py",
        "core/voters/session_voter.py",
        "core/voters/spread_voter.py",
        "core/voters/volatility_voter.py",
    ]

    # Imports that voters must NEVER have
    FORBIDDEN_VOTER_IMPORTS = [
        "from core.voters.execution_gate",
        "from core.voters.risk_engine",
        "from core.pipeline.scoring_engine",
        "from core.pipeline.trade_quality",
        "import execution_gate",
        "import risk_engine",
    ]

    # Patterns indicating execution decision logic
    FORBIDDEN_VOTER_PATTERNS = [
        "should_trade",
        "approve_trade",
        "reject_trade",
        "position_size",
        "send_order",
    ]

    @pytest.mark.parametrize("filepath", VOTER_FILES)
    def test_no_forbidden_imports(self, filepath: str):
        source = _read_source(filepath)
        if source is None:
            pytest.skip(f"{filepath} not found")

        violations = []
        for pattern in self.FORBIDDEN_VOTER_IMPORTS:
            if pattern in source:
                violations.append(pattern)

        assert not violations, (
            f"{filepath} contains forbidden imports: {violations}"
        )

    @pytest.mark.parametrize("filepath", VOTER_FILES)
    def test_no_execution_logic(self, filepath: str):
        source = _read_source(filepath)
        if source is None:
            pytest.skip(f"{filepath} not found")

        violations = []
        for pattern in self.FORBIDDEN_VOTER_PATTERNS:
            if pattern in source:
                # Exclude comments and strings
                for line in source.split("\n"):
                    stripped = line.strip()
                    if pattern in stripped and not stripped.startswith("#") and not stripped.startswith('"'):
                        violations.append(f"{pattern} in: {stripped}")
                        break

        assert not violations, (
            f"{filepath} contains execution logic patterns: {violations}"
        )

    @pytest.mark.parametrize("filepath", VOTER_FILES)
    def test_no_engine_state_import(self, filepath: str):
        """Voters must use StateSnapshot, never EngineState directly."""
        source = _read_source(filepath)
        if source is None:
            pytest.skip(f"{filepath} not found")

        assert "from core.engine_state" not in source, (
            f"{filepath} imports EngineState directly (must use StateSnapshot)"
        )
        assert "import engine_state" not in source, (
            f"{filepath} imports engine_state directly"
        )


# ─── TEST 3: SNAPSHOT IMMUTABILITY TEST ──────────────────────────────────────

class TestSnapshotImmutability:
    """StateSnapshot must be frozen and never mutated after creation."""

    def test_snapshot_is_frozen_dataclass(self):
        from core.state.snapshot import StateSnapshot
        import dataclasses

        assert dataclasses.is_dataclass(StateSnapshot)
        # Check frozen=True by attempting mutation
        snap = StateSnapshot(
            bias_phase="EXPIRED",
            current_bias=None,
            bias_strength=0.0,
            bias_age_seconds=0.0,
            bias_confirmation_count=0,
            bias_contradiction_count=0,
            regime_state="RANGING",
            last_sweep_high=None,
            last_sweep_low=None,
            last_strong_impulse_direction=None,
            current_time=0.0,
            last_bias_time=None,
            volatility_filter=0.0,
            last_trade_side=None,
            last_trade_bar=None,
            last_successful_open_mono=None,
            bias_flip_bars_count=0,
            can_trade_bias=False,
            bias_lock_until_candle=-1,
            bias_lock_until_time=0.0,
        )
        with pytest.raises(Exception):  # FrozenInstanceError
            snap.bias_phase = "CONFIRMED"  # type: ignore

    def test_no_snapshot_mutation_in_pipeline(self):
        """Pipeline files must not assign to snapshot fields."""
        mutation_patterns = [
            "snapshot.",  # We'll check for assignment specifically
        ]
        pipeline_files = [
            "core/pipeline/scoring_engine.py",
            "core/pipeline/trade_quality.py",
            "core/pipeline/intent_builder.py",
            "core/pipeline/confirmations.py",
        ]

        violations = []
        for filepath in pipeline_files:
            source = _read_source(filepath)
            if source is None:
                continue

            lines = source.split("\n")
            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                # Check for snapshot.field = value (mutation attempt)
                if "snapshot." in stripped and "=" in stripped:
                    # Exclude comparisons (==, !=, >=, <=)
                    after_snapshot = stripped.split("snapshot.")[1] if "snapshot." in stripped else ""
                    if "=" in after_snapshot:
                        eq_pos = after_snapshot.index("=")
                        if eq_pos > 0 and after_snapshot[eq_pos - 1] not in ("!", ">", "<", "="):
                            if after_snapshot[eq_pos + 1:eq_pos + 2] != "=":
                                violations.append(f"{filepath}:{i}: {stripped}")

        assert not violations, (
            f"Snapshot mutation detected in pipeline:\n"
            + "\n".join(f"  - {v}" for v in violations)
        )


# ─── TEST 4: FEATURE ENGINE ISOLATION TEST ───────────────────────────────────

class TestFeatureEngineIsolation:
    """FeatureEngine must only compute raw features — no decisions."""

    def test_no_voter_imports(self):
        source = _read_source("core/features/engine.py")
        assert source is not None

        forbidden = ["voters", "confluence", "execution_gate", "risk_engine"]
        for pattern in forbidden:
            assert pattern not in source, (
                f"core/features/engine.py imports forbidden module: {pattern}"
            )

    def test_no_threshold_logic(self):
        source = _read_source("core/features/engine.py")
        assert source is not None

        # Feature engine must not contain trading thresholds
        forbidden_patterns = [
            "should_trade",
            "MIN_SCORE",
            "threshold",
            "approve",
            "reject",
            "NO_TRADE",
            "BUY",
            "SELL",
        ]
        for pattern in forbidden_patterns:
            assert pattern not in source, (
                f"core/features/engine.py contains decision pattern: {pattern}"
            )

    def test_no_engine_state_access(self):
        source = _read_source("core/features/engine.py")
        assert source is not None

        # Check actual import lines only (not comments/docstrings)
        lines = source.split("\n")
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'"):
                continue
            assert "from core.engine_state" not in stripped, (
                f"core/features/engine.py imports EngineState: {stripped}"
            )
            if "import" in stripped and "engine_state" in stripped:
                pytest.fail(f"core/features/engine.py imports engine_state: {stripped}")


# ─── TEST 5: CONFLUENCE BOUNDARY TEST ────────────────────────────────────────

class TestConfluenceBoundary:
    """ConfluenceEngine must only aggregate — no execution logic."""

    def test_no_engine_state_access(self):
        source = _read_source("core/voters/confluence_engine.py")
        assert source is not None

        # Check actual import lines only (not comments/docstrings)
        lines = source.split("\n")
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'"):
                continue
            assert "from core.engine_state" not in stripped, (
                f"confluence_engine.py imports EngineState: {stripped}"
            )
            if "import" in stripped and "engine_state" in stripped:
                pytest.fail(f"confluence_engine.py imports engine_state: {stripped}")

    def test_no_execution_logic(self):
        source = _read_source("core/voters/confluence_engine.py")
        assert source is not None

        forbidden = [
            "should_trade",
            "send_order",
            "position_size",
            "mt5",
            "order_send",
        ]
        for pattern in forbidden:
            assert pattern not in source, (
                f"confluence_engine.py contains execution pattern: {pattern}"
            )

    def test_no_risk_rules(self):
        source = _read_source("core/voters/confluence_engine.py")
        assert source is not None

        forbidden = [
            "risk_percent",
            "max_drawdown",
            "lot_size",
            "equity",
        ]
        for pattern in forbidden:
            assert pattern not in source, (
                f"confluence_engine.py contains risk pattern: {pattern}"
            )

    def test_only_consumes_vote_results(self):
        """ConfluenceEngine's compute_confluence must only take VoteResult + SWM params."""
        from core.voters.confluence_engine import compute_confluence
        import inspect

        sig = inspect.signature(compute_confluence)
        params = set(sig.parameters.keys())

        # Expected parameters
        expected = {
            "bias_vote", "structure_vote", "volatility_vote",
            "spread_vote", "session_vote", "weights",
            "threshold", "min_confidence",
            "structure_score", "structure_regime",
        }
        assert params == expected, (
            f"compute_confluence has unexpected params: {params - expected}"
        )


# ─── TEST 6: EXECUTION AUTHORITY ISOLATION TEST ──────────────────────────────

class TestExecutionAuthorityIsolation:
    """Only EA modules may approve/reject trades."""

    # Non-EA modules that must NEVER contain trade approval logic
    NON_EA_MODULES = [
        "core/voters/bias_voter.py",
        "core/voters/structure_voter.py",
        "core/voters/session_voter.py",
        "core/voters/spread_voter.py",
        "core/voters/volatility_voter.py",
        "core/voters/confluence_engine.py",
        "core/features/engine.py",
        "core/engine_state.py",
        "core/state/snapshot.py",
    ]

    EXECUTION_PATTERNS = [
        "should_trade = True",
        "should_trade=True",
        "send_order(",
        "order_send(",
        "mt5.order_send",
    ]

    @pytest.mark.parametrize("filepath", NON_EA_MODULES)
    def test_no_trade_approval_logic(self, filepath: str):
        source = _read_source(filepath)
        if source is None:
            pytest.skip(f"{filepath} not found")

        violations = []
        for pattern in self.EXECUTION_PATTERNS:
            if pattern in source:
                violations.append(pattern)

        assert not violations, (
            f"{filepath} contains execution authority patterns: {violations}"
        )


# ─── TEST 7: INFLUENCE PATH REGISTRY VALIDATION ──────────────────────────────

class TestInfluencePathRegistry:
    """Validate the influence registry static guard passes."""

    def test_no_hidden_influence_paths(self):
        from architecture.contracts.influence_registry import (
            assert_no_hidden_influence_paths,
        )
        result = assert_no_hidden_influence_paths()
        assert result is True

    def test_structure_authority_isolation(self):
        from architecture.contracts.structure_authority_contract import (
            assert_structure_isolation,
        )
        result = assert_structure_isolation()
        assert result is True
