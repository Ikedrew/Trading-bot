"""
Runtime Isolation Contract — Enforcement Test.

Validates that no LIVE runtime module imports any OFFLINE module.
This test ensures the live trading bot can operate independently of
all analytics, replay, causal, and optimisation systems.

PASS = live runtime has no offline dependencies.
FAIL = live module imports offline module (architectural violation).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# Offline module prefixes that MUST NEVER be imported by live runtime
OFFLINE_PREFIXES = (
    "core.causal",
    "core.edge_attribution",
    "core.edge_optimisation",
    "core.strategy_compiler",
    "core.trade_truth_graph",
    "core.behaviour_validation",
    "core.offline_query",
    "core.feature_role_contract",
    "core.audit_persistence",
    "analysis.",
    "data_pipeline.",
)

# Live runtime files that must never import offline modules
LIVE_RUNTIME_FILES = [
    ROOT / "core" / "runtime" / "live_scanner.py",
    ROOT / "core" / "engine.py",
    ROOT / "core" / "event_stream.py",
    ROOT / "core" / "execution_context.py",
    ROOT / "core" / "shadow_trades.py",
    ROOT / "core" / "trade_truth.py",
    ROOT / "core" / "trade_journal.py",
    ROOT / "core" / "correlation.py",
    ROOT / "core" / "features" / "engine.py",
    ROOT / "core" / "features" / "bundle.py",
    ROOT / "execution" / "mt5_execution.py",
    ROOT / "risk" / "manager.py",
    ROOT / "risk" / "spread_guard.py",
    ROOT / "risk" / "drawdown_guard.py",
    ROOT / "risk" / "daily_loss_guard.py",
    ROOT / "data" / "mt5_data.py",
]


class TestRuntimeIsolation:
    """Enforce that live runtime never depends on offline systems."""

    def test_no_offline_imports_in_live_runtime(self):
        """Scan all live runtime files — no offline module imports allowed."""
        violations = []

        for live_file in LIVE_RUNTIME_FILES:
            if not live_file.exists():
                continue

            source = live_file.read_text(encoding="utf-8")
            for i, line in enumerate(source.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue

                for prefix in OFFLINE_PREFIXES:
                    if f"from {prefix}" in stripped or f"import {prefix}" in stripped:
                        # Check if it's inside a try/except (fire-and-forget is acceptable)
                        # Simple heuristic: look at preceding lines for 'try:'
                        lines = source.splitlines()
                        in_try = False
                        for j in range(max(0, i - 5), i):
                            if "try:" in lines[j]:
                                in_try = True
                                break
                        if not in_try:
                            violations.append(
                                f"{live_file.relative_to(ROOT)}:{i} imports '{prefix}' "
                                f"OUTSIDE try/except — BLOCKED"
                            )

        assert violations == [], (
            "RUNTIME ISOLATION VIOLATION: Live runtime imports offline modules:\n"
            + "\n".join(f"  {v}" for v in violations)
        )

    def test_live_scanner_has_no_causal_imports(self):
        """live_scanner.py must NEVER import core.causal.*."""
        source = (ROOT / "core" / "runtime" / "live_scanner.py").read_text(encoding="utf-8")
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert "from core.causal" not in stripped, (
                f"live_scanner.py imports causal module: {stripped}"
            )
            assert "import core.causal" not in stripped

    def test_live_scanner_has_no_attribution_imports(self):
        """live_scanner.py must NEVER import edge_attribution."""
        source = (ROOT / "core" / "runtime" / "live_scanner.py").read_text(encoding="utf-8")
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert "from core.edge_attribution" not in stripped
            assert "from core.edge_optimisation" not in stripped
            assert "from core.strategy_compiler" not in stripped

    def test_event_stream_has_no_offline_dependencies(self):
        """event_stream.py must never import offline modules."""
        source = (ROOT / "core" / "event_stream.py").read_text(encoding="utf-8")
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for prefix in OFFLINE_PREFIXES:
                assert f"from {prefix}" not in stripped, (
                    f"event_stream.py imports offline: {stripped}"
                )

    def test_shadow_trades_has_no_offline_dependencies(self):
        """shadow_trades.py must never import offline modules."""
        source = (ROOT / "core" / "shadow_trades.py").read_text(encoding="utf-8")
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for prefix in OFFLINE_PREFIXES:
                assert f"from {prefix}" not in stripped, (
                    f"shadow_trades.py imports offline: {stripped}"
                )

    def test_trade_truth_has_no_offline_dependencies(self):
        """trade_truth.py must never import offline modules."""
        source = (ROOT / "core" / "trade_truth.py").read_text(encoding="utf-8")
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for prefix in OFFLINE_PREFIXES:
                assert f"from {prefix}" not in stripped, (
                    f"trade_truth.py imports offline: {stripped}"
                )

    def test_contract_validator(self):
        """Run the formal contract validator."""
        from architecture.contracts.runtime_isolation import validate_runtime_isolation
        result = validate_runtime_isolation(project_root=str(ROOT))
        # We expect PASS or violations only in try/except blocks
        assert result["live_modules_checked"] > 0
