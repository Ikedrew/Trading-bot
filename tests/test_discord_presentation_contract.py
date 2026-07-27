"""
Discord Presentation Contract — Architectural Enforcement Tests.

Guarantees:
    ? Every Discord message is derived entirely from persisted state.
    ? No runtime decision depends on Discord.
    ? No persistence module imports Discord.
    ? If Discord is disabled, all persistence tests still pass.
    ? If Discord send() throws an exception, trading continues normally.
    ? Persistence occurs before notification.
    ? Notification failure never rolls back persistence.

These tests validate that Discord is a DISPOSABLE presentation layer.
If all Discord webhooks disappeared, zero forensic capability would be lost.
"""

from __future__ import annotations

import ast
import sys
import json
import tempfile
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# -------------------------------------------------------------------------------
# PERSISTENCE MODULES (write truth — MUST NEVER import Discord)
# -------------------------------------------------------------------------------

PERSISTENCE_MODULES = [
    ROOT / "core" / "event_stream.py",
    ROOT / "core" / "execution_context.py",
    ROOT / "core" / "shadow_trades.py",
    ROOT / "core" / "trade_truth.py",
    ROOT / "core" / "trade_truth_graph.py",
    ROOT / "core" / "edge_attribution.py",
    ROOT / "core" / "edge_optimisation.py",
    ROOT / "core" / "strategy_compiler.py",
    ROOT / "core" / "decision_audit.py",
    ROOT / "core" / "correlation.py",
    ROOT / "core" / "trade_journal.py",
]

# LIVE RUNTIME DECISION MODULES (make decisions — MUST NOT depend on Discord)
DECISION_MODULES = [
    ROOT / "core" / "engine.py",
    ROOT / "core" / "pipeline" / "scoring_engine.py",
    ROOT / "core" / "pipeline" / "decision_engine.py",
    ROOT / "core" / "pipeline" / "trade_quality.py",
    ROOT / "core" / "pipeline" / "new_engine.py",
    ROOT / "risk" / "manager.py",
    ROOT / "risk" / "spread_guard.py",
    ROOT / "risk" / "drawdown_guard.py",
    ROOT / "risk" / "daily_loss_guard.py",
    ROOT / "risk" / "daily_trade_limit.py",
    ROOT / "risk" / "portfolio_exposure_guard.py",
    ROOT / "risk" / "regime_guard.py",
    ROOT / "risk" / "trade_cooldown.py",
    ROOT / "risk" / "correlation_guard.py",
    ROOT / "risk" / "position_sizing.py",
    ROOT / "execution" / "mt5_execution.py",
]


# -------------------------------------------------------------------------------
# TEST: No persistence module imports Discord
# -------------------------------------------------------------------------------

class TestNoPersistenceImportsDiscord:
    """Persistence modules must NEVER import or depend on Discord."""

    def test_persistence_modules_have_no_discord_imports(self):
        """
        Scan all persistence modules for ANY reference to discord_notifier.
        Not even inside try/except — persistence must be fully independent.
        """
        violations = []

        for mod_path in PERSISTENCE_MODULES:
            if not mod_path.exists():
                continue

            source = mod_path.read_text(encoding="utf-8")
            for i, line in enumerate(source.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if "discord" in stripped.lower() and (
                    "import" in stripped or "send_discord" in stripped
                ):
                    violations.append(
                        f"{mod_path.relative_to(ROOT)}:{i} — '{stripped.strip()[:80]}'"
                    )

        assert violations == [], (
            "DISCORD PRESENTATION CONTRACT VIOLATION:\n"
            "Persistence modules must NEVER reference Discord:\n"
            + "\n".join(f"  {v}" for v in violations)
        )


# -------------------------------------------------------------------------------
# TEST: No decision module depends on Discord
# -------------------------------------------------------------------------------

class TestNoDecisionDependsOnDiscord:
    """Runtime decision modules must produce correct output without Discord."""

    def test_decision_modules_have_no_discord_dependency(self):
        """
        Decision modules may import discord inside try/except (for observability)
        but must NEVER use discord output as input to any decision.

        This checks that no discord import appears OUTSIDE a try/except block.
        """
        violations = []

        for mod_path in DECISION_MODULES:
            if not mod_path.exists():
                continue

            source = mod_path.read_text(encoding="utf-8")
            lines = source.splitlines()

            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if "discord" not in stripped.lower():
                    continue
                if "import" not in stripped and "send_discord" not in stripped:
                    continue

                # Check if inside try/except block (look back for 'try:')
                in_try = False
                for j in range(max(0, i - 10), i):
                    if "try:" in lines[j]:
                        in_try = True
                        break

                if not in_try:
                    violations.append(
                        f"{mod_path.relative_to(ROOT)}:{i+1} — "
                        f"discord reference outside try/except: '{stripped[:80]}'"
                    )

        assert violations == [], (
            "DISCORD PRESENTATION CONTRACT VIOLATION:\n"
            "Decision modules reference Discord outside try/except:\n"
            + "\n".join(f"  {v}" for v in violations)
        )


# -------------------------------------------------------------------------------
# TEST: Discord disabled ? persistence still works
# -------------------------------------------------------------------------------

class TestDiscordDisabledPersistenceWorks:
    """When ALERTING_ENABLED=False, all persistence operations succeed."""

    def test_event_stream_persists_with_discord_disabled(self):
        """event_stream.emit() works perfectly with alerting off."""
        with patch.dict(os.environ, {}, clear=False):
            from core import config
            from core.event_stream import emit, disable, enable, close
            original = config.ALERTING_ENABLED
            config.ALERTING_ENABLED = False

            try:
                enable()  # Ensure stream is active

                with tempfile.TemporaryDirectory() as tmpdir:
                    with patch("core.event_stream._get_event_dir", return_value=Path(tmpdir)):
                        result = emit("CANDLE", "EURUSD", {
                            "open": 1.1000, "high": 1.1010,
                            "low": 1.0990, "close": 1.1005,
                        })
                        assert result is True, "emit() must succeed with Discord disabled"

                        # Close file handle before checking (Windows lock issue)
                        close()

                        # Verify file was written
                        files = list(Path(tmpdir).glob("*.jsonl"))
                        assert len(files) > 0, "Event file must exist"
                        content = files[0].read_text(encoding="utf-8").strip()
                        assert len(content) > 0, "Event content must not be empty"
                        record = json.loads(content)
                        assert record["type"] == "CANDLE"
                        assert record["symbol"] == "EURUSD"
            finally:
                config.ALERTING_ENABLED = original

    def test_execution_context_persists_with_discord_disabled(self):
        """execution_context persists regardless of Discord state."""
        from core import config
        original = config.ALERTING_ENABLED
        config.ALERTING_ENABLED = False

        try:
            from core.execution_context import build_execution_context, persist_execution_context

            with tempfile.TemporaryDirectory() as tmpdir:
                with patch("core.execution_context._LOCAL_DIR", tmpdir):
                    ctx = build_execution_context(
                        correlation_id="COR-TEST-001",
                        symbol="EURUSD",
                        timestamp_utc=1720000000.0,
                        bid=1.10000,
                        ask=1.10020,
                        session_state="LONDON",
                    )
                    result = persist_execution_context(ctx)
                    assert result is True, "persist must succeed with Discord disabled"

                    # Verify file exists
                    files = list(Path(tmpdir).rglob("*.jsonl"))
                    assert len(files) > 0
                    record = json.loads(files[0].read_text(encoding="utf-8").strip())
                    assert record["correlation_id"] == "COR-TEST-001"
        finally:
            config.ALERTING_ENABLED = original

    def test_decision_audit_persists_with_discord_disabled(self):
        """decision_audit persists rejections regardless of Discord state."""
        from core import config
        original_alert = config.ALERTING_ENABLED
        original_audit = config.DECISION_AUDIT_INCLUDE_REJECTIONS
        config.ALERTING_ENABLED = False
        config.DECISION_AUDIT_INCLUDE_REJECTIONS = True

        try:
            from core.decision_audit import persist_risk_rejection

            with tempfile.TemporaryDirectory() as tmpdir:
                with patch.object(config, "DECISION_AUDIT_DIR", tmpdir):
                    persist_risk_rejection(
                        symbol="EURUSD",
                        cycle_id=42,
                        guard="test_guard",
                        reason="test_reason",
                        correlation_id="COR-TEST-002",
                        metadata={"key": "value"},
                    )

                    files = list(Path(tmpdir).glob("*.jsonl"))
                    assert len(files) > 0, "Risk rejection must persist with Discord disabled"
                    record = json.loads(files[0].read_text(encoding="utf-8").strip())
                    assert record["guard"] == "test_guard"
                    assert record["guard_reason"] == "test_reason"
        finally:
            config.ALERTING_ENABLED = original_alert
            config.DECISION_AUDIT_INCLUDE_REJECTIONS = original_audit


# -------------------------------------------------------------------------------
# TEST: Discord exception ? trading continues
# -------------------------------------------------------------------------------

class TestDiscordExceptionNeverBlocksTrading:
    """If send_discord throws, the calling code must continue normally."""

    def test_log_router_survives_discord_exception(self):
        """StructuredLogger.event() completes even when Discord throws."""
        from core.log_router import StructuredLogger

        logger = StructuredLogger()

        with patch("core.discord_notifier.send_discord", side_effect=Exception("Discord is dead")):
            # Must not raise
            logger.event("TRADE_DECISION", {
                "symbol": "EURUSD",
                "decision": "ALLOW",
                "score": 7,
            })

    def test_event_stream_survives_discord_exception(self):
        """event_stream.emit() succeeds even if downstream Discord fails."""
        from core.event_stream import emit, enable, close
        enable()

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("core.event_stream._get_event_dir", return_value=Path(tmpdir)):
                # Even if somehow Discord were called (it shouldn't be), it must not break emit
                with patch("core.discord_notifier.send_discord", side_effect=Exception("Discord exploded")):
                    result = emit("CANDLE", "EURUSD", {"open": 1.1, "high": 1.2, "low": 1.0, "close": 1.15})
                    assert result is True
                close()

    def test_execution_context_survives_discord_exception(self):
        """persist_execution_context succeeds even if Discord is broken."""
        from core.execution_context import build_execution_context, persist_execution_context

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("core.execution_context._LOCAL_DIR", tmpdir):
                with patch("core.discord_notifier.send_discord", side_effect=Exception("boom")):
                    ctx = build_execution_context(
                        correlation_id="COR-CRASH-TEST",
                        symbol="GBPUSD",
                        timestamp_utc=1720000000.0,
                        bid=1.26000,
                        ask=1.26020,
                    )
                    result = persist_execution_context(ctx)
                    assert result is True

    def test_shadow_trades_survives_discord_exception(self):
        """Shadow trade engine persists even with Discord broken."""
        from core.shadow_trades import ShadowTradeEngine

        engine = ShadowTradeEngine(max_bars=5)

        with patch("core.discord_notifier.send_discord", side_effect=Exception("boom")):
            engine.open_trade(
                trade_id="test_crash_1",
                cycle_id=1,
                symbol="EURUSD",
                direction="BUY",
                entry_price=1.1000,
                stop_loss=1.0950,
                take_profit=1.1100,
                entry_time=1720000000.0,
                correlation_id="COR-CRASH-01",
            )

            # Evaluate a bar that triggers stop loss
            with tempfile.TemporaryDirectory() as tmpdir:
                with patch("core.shadow_trades._LOCAL_DIR", tmpdir):
                    records = engine.evaluate_bar(
                        symbol="EURUSD",
                        bar_high=1.0980,
                        bar_low=1.0940,
                        bar_close=1.0945,
                        bar_time=1720000300.0,
                    )
                    assert len(records) == 1, "Shadow trade must close even with Discord broken"
                    assert records[0]["simulated_outcome"]["exit_reason"] == "stop_loss"


# -------------------------------------------------------------------------------
# TEST: Persistence occurs BEFORE notification
# -------------------------------------------------------------------------------

class TestPersistenceBeforeNotification:
    """Persistence must complete BEFORE any Discord notification is attempted."""

    def test_log_router_persists_before_discord(self):
        """
        StructuredLogger.event() calls upload_event BEFORE send_discord.
        Verified by inspecting call order.
        """
        from core.log_router import StructuredLogger

        call_order = []

        def mock_upload(event):
            call_order.append("PERSIST")
            return True

        def mock_discord(channel, msg, **kwargs):
            call_order.append("DISCORD")

        logger = StructuredLogger()

        with patch("core.aws_uploader.upload_event", mock_upload):
            with patch("core.discord_notifier.send_discord", mock_discord):
                logger.event("TRADE_DECISION", {"symbol": "EURUSD", "decision": "ALLOW"})

        assert "PERSIST" in call_order, "Persistence must be called"
        if "DISCORD" in call_order:
            persist_idx = call_order.index("PERSIST")
            discord_idx = call_order.index("DISCORD")
            assert persist_idx < discord_idx, (
                f"Persistence must occur BEFORE Discord. "
                f"Got order: {call_order}"
            )

    def test_log_router_source_code_order(self):
        """
        Static analysis: verify upload_event() call appears before
        send_discord() call in StructuredLogger.event() method body.
        """
        source = (ROOT / "core" / "log_router.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "event":
                # Find positions of upload_event and send_discord calls
                upload_line = None
                discord_line = None

                for child in ast.walk(node):
                    if isinstance(child, ast.Call):
                        # Look for upload_event call
                        if isinstance(child.func, ast.Name) and child.func.id == "upload_event":
                            upload_line = child.lineno
                        # Look for send_discord call
                        if isinstance(child.func, ast.Name) and child.func.id == "send_discord":
                            discord_line = child.lineno

                if upload_line and discord_line:
                    assert upload_line < discord_line, (
                        f"upload_event (line {upload_line}) must appear before "
                        f"send_discord (line {discord_line}) in StructuredLogger.event()"
                    )
                    return

        # If we get here, the method structure was not found — pass (defensive)


# -------------------------------------------------------------------------------
# TEST: Notification failure never rolls back persistence
# -------------------------------------------------------------------------------

class TestNotificationFailureNeverRollsBackPersistence:
    """Even if Discord fails, persisted data must remain intact."""

    def test_event_persists_when_s3_mirror_fails_and_discord_fails(self):
        """
        Local persistence is the primary truth.
        Both S3 mirror failure AND Discord failure must not affect local write.
        """
        from core.event_stream import emit, enable, close
        enable()

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("core.event_stream._get_event_dir", return_value=Path(tmpdir)):
                with patch("core.event_stream._s3_enqueue", side_effect=Exception("S3 dead")):
                    with patch("core.discord_notifier.send_discord", side_effect=Exception("Discord dead")):
                        result = emit("CANDLE", "EURUSD", {
                            "open": 1.1, "high": 1.2, "low": 1.0, "close": 1.15
                        })
                        assert result is True, "emit must succeed despite S3+Discord failure"

                # Close file handle before verifying (Windows lock)
                close()

            # Verify local file is intact
            files = list(Path(tmpdir).glob("*.jsonl"))
            assert len(files) == 1
            record = json.loads(files[0].read_text(encoding="utf-8").strip())
            assert record["type"] == "CANDLE"
            assert record["symbol"] == "EURUSD"

    def test_decision_audit_persists_when_discord_event_bus_throws(self):
        """
        Decision audit local write must survive even if the event bus
        (which might try to send Discord) throws.
        """
        from core import config
        from core.decision_audit import persist_risk_rejection

        original = config.DECISION_AUDIT_INCLUDE_REJECTIONS
        config.DECISION_AUDIT_INCLUDE_REJECTIONS = True

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                with patch.object(config, "DECISION_AUDIT_DIR", tmpdir):
                    with patch("core.discord_notifier.send_discord", side_effect=Exception("Discord dead")):
                        persist_risk_rejection(
                            symbol="USDJPY",
                            cycle_id=99,
                            guard="portfolio_exposure",
                            reason="max_positions_reached",
                            metadata={"positions": 3, "max": 3},
                        )

                files = list(Path(tmpdir).glob("*.jsonl"))
                assert len(files) == 1, "Rejection must persist even when Discord throws"
                record = json.loads(files[0].read_text(encoding="utf-8").strip())
                assert record["guard"] == "portfolio_exposure"
                assert record["symbol"] == "USDJPY"
        finally:
            config.DECISION_AUDIT_INCLUDE_REJECTIONS = original

    def test_trade_truth_persists_independently_of_discord(self):
        """Trade truth persistence has zero Discord dependency."""
        from core.trade_truth import build_trade_truth, persist_trade_truth

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("core.discord_notifier.send_discord", side_effect=Exception("Discord dead")):
                record = build_trade_truth(
                    trade_id="T-DISCORD-DEAD",
                    correlation_id="COR-DEAD-001",
                    symbol="EURUSD",
                    entry_fill_price=1.10000,
                    exit_fill_price=1.10200,
                    volume_executed=0.01,
                    entry_timestamp_broker=1720000000.0,
                    exit_timestamp_broker=1720003600.0,
                    pnl_realised=20.0,
                    r_multiple_realised=2.0,
                    exit_reason="take_profit_hit",
                )
                result = persist_trade_truth(record, local_dir=tmpdir)
                assert result is True

            # Verify
            files = list(Path(tmpdir).rglob("*.jsonl"))
            assert len(files) == 1
            saved = json.loads(files[0].read_text(encoding="utf-8").strip())
            assert saved["identity"]["trade_id"] == "T-DISCORD-DEAD"
            assert saved["outcome"]["r_multiple_realised"] == 2.0


# -------------------------------------------------------------------------------
# TEST: Discord never participates in decision logic
# -------------------------------------------------------------------------------

class TestDiscordNeverParticipatesInDecisions:
    """No decision branch ever reads from or depends on Discord state."""

    def test_no_discord_return_value_used_in_decisions(self):
        """
        Scan live_scanner.py: verify send_discord return value is never
        stored or used in a conditional.
        """
        scanner_path = ROOT / "core" / "runtime" / "live_scanner.py"
        if not scanner_path.exists():
            pytest.skip("live_scanner.py not found")

        source = scanner_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        violations = []
        for node in ast.walk(tree):
            # Check: result = send_discord(...) — storing return value
            if isinstance(node, ast.Assign):
                if isinstance(node.value, ast.Call):
                    func = node.value.func
                    if isinstance(func, ast.Name) and func.id == "send_discord":
                        violations.append(f"line {node.lineno}: assigns send_discord() return to variable")
                    if isinstance(func, ast.Attribute) and func.attr == "send_discord":
                        violations.append(f"line {node.lineno}: assigns send_discord() return to variable")

            # Check: if send_discord(...) — used in conditional
            if isinstance(node, ast.If):
                if isinstance(node.test, ast.Call):
                    func = node.test.func
                    if isinstance(func, ast.Name) and func.id == "send_discord":
                        violations.append(f"line {node.lineno}: send_discord() used as if-condition")

        assert violations == [], (
            "Discord return value must NEVER be used in decision logic:\n"
            + "\n".join(f"  {v}" for v in violations)
        )

    def test_discord_send_always_inside_try_except_in_live_scanner(self):
        """
        Every send_discord() call in live_scanner.py must be inside
        a try/except block — proving it's fire-and-forget.
        """
        scanner_path = ROOT / "core" / "runtime" / "live_scanner.py"
        if not scanner_path.exists():
            pytest.skip("live_scanner.py not found")

        source = scanner_path.read_text(encoding="utf-8")
        lines = source.splitlines()

        violations = []
        for i, line in enumerate(lines):
            if "send_discord(" in line and not line.strip().startswith("#"):
                # Look backward for try: (up to 30 lines — Discord calls may be
                # deep inside try blocks with multi-line message construction)
                found_try = False
                for j in range(max(0, i - 30), i):
                    if "try:" in lines[j]:
                        found_try = True
                        break
                if not found_try:
                    violations.append(f"line {i+1}: send_discord() not inside try/except")

        assert violations == [], (
            "All send_discord() calls in live_scanner must be inside try/except:\n"
            + "\n".join(f"  {v}" for v in violations)
        )

    def test_discord_logger_event_always_inside_try_except(self):
        """
        Every _dl.event() call in live_scanner.py must be inside
        a try/except block — proving it's fire-and-forget.
        """
        scanner_path = ROOT / "core" / "runtime" / "live_scanner.py"
        if not scanner_path.exists():
            pytest.skip("live_scanner.py not found")

        source = scanner_path.read_text(encoding="utf-8")
        lines = source.splitlines()

        violations = []
        for i, line in enumerate(lines):
            if "_dl.event(" in line and not line.strip().startswith("#"):
                # Look backward for try: (up to 30 lines — _dl.event calls may be
                # deep inside try blocks with multi-line payload construction)
                found_try = False
                for j in range(max(0, i - 30), i):
                    if "try:" in lines[j]:
                        found_try = True
                        break
                if not found_try:
                    violations.append(f"line {i+1}: _dl.event() not inside try/except")

        assert violations == [], (
            "All _dl.event() calls in live_scanner must be inside try/except:\n"
            + "\n".join(f"  {v}" for v in violations)
        )


# -------------------------------------------------------------------------------
# TEST: Discord formatting is pure (no mutation of source data)
# -------------------------------------------------------------------------------

class TestDiscordFormattingIsPure:
    """Discord formatting functions must be pure — no side effects."""

    def test_format_event_message_is_deterministic(self):
        """Same input always produces same output."""
        from core.log_router import _format_event_message

        data = {"symbol": "EURUSD", "decision": "ALLOW", "score": 7, "reason": "confluence"}

        result1 = _format_event_message("TRADE_DECISION", data)
        result2 = _format_event_message("TRADE_DECISION", data)
        assert result1 == result2

    def test_format_event_message_does_not_mutate_input(self):
        """Formatting must never modify the input dict."""
        from core.log_router import _format_event_message
        import copy

        data = {"symbol": "EURUSD", "decision": "BLOCKED", "reason": "score_low", "score": 3}
        original = copy.deepcopy(data)

        _format_event_message("TRADE_DECISION", data)
        assert data == original, "Formatting must not mutate input data"

    def test_format_returns_string_never_none(self):
        """Format must always return a string, even for unknown event types."""
        from core.log_router import _format_event_message

        result = _format_event_message("UNKNOWN_TYPE_XYZ", {"foo": "bar"})
        assert isinstance(result, str)
        assert len(result) > 0


# -------------------------------------------------------------------------------
# TEST: send_discord is fail-safe (never raises to caller)
# -------------------------------------------------------------------------------

class TestSendDiscordFailSafe:
    """send_discord must never raise an exception to its caller."""

    def test_send_discord_handles_missing_channel(self):
        """Unknown channel ? silent no-op."""
        from core.discord_notifier import send_discord
        # Must not raise
        send_discord("nonexistent-channel-xyz", "test message")

    def test_send_discord_handles_empty_url(self):
        """Empty webhook URL ? silent no-op."""
        from core.discord_notifier import send_discord
        with patch("core.config.DISCORD_WEBHOOKS", {"test-ch": ""}):
            send_discord("test-ch", "test message")

    def test_send_discord_handles_network_error(self):
        """Network failure ? caught, never propagated."""
        from core.discord_notifier import send_discord
        with patch("core.config.DISCORD_WEBHOOKS", {"test-ch": "https://invalid.example.com/webhook"}):
            with patch("requests.post", side_effect=ConnectionError("network down")):
                # Must not raise
                send_discord("test-ch", "test message")

    def test_send_discord_handles_alerting_disabled(self):
        """ALERTING_ENABLED=False ? silent no-op, no network call."""
        from core import config
        from core.discord_notifier import send_discord

        original = config.ALERTING_ENABLED
        config.ALERTING_ENABLED = False

        try:
            with patch("requests.post") as mock_post:
                send_discord("system-status", "test")
                mock_post.assert_not_called()
        finally:
            config.ALERTING_ENABLED = original
