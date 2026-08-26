"""
Focused regression tests for the Shadow Trial 2 encoding defect.

ROOT CAUSE (Trial 2):
    BarProvider.fetch_bar() emitted diagnostics containing non-ASCII characters
    (U+2192 'arrow' separator in [CANDLE FEED], and U+26A0/U+FE0F 'warning sign'
    emoji in [BAR STALL]). Under the production launcher stdout is redirected to
    a file, which on this Windows host resolves to the legacy locale codec
    (cp1252). cp1252 cannot encode those characters, so EVERY call raised
    UnicodeEncodeError inside fetch_bar(); the per-symbol catch-all in
    live_scanner.py then silently swallowed the exception (Discord-only notice)
    and continued. Net effect: the scanner ticked 1300 'healthy' cycles while no
    symbol ever reached pattern detection, producing a false zero-opportunity
    verdict.

These tests reproduce the exact failing condition (cp1252, strict errors,
redirected stdout) and prove that, post-fix, fetch_bar() runs to completion and
its diagnostics can never abort market-data processing. They write no runtime
artifacts (no logs/, no logs/shadow_trades).
"""

from __future__ import annotations

import io
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.runtime.bar_provider import BarProvider, BarResult  # noqa: E402


# ─── FIXTURE HELPERS ─────────────────────────────────────────────────────────


def _make_candle(t: int, o=1.1, h=1.2, l=1.0, c=1.15) -> MagicMock:
    candle = MagicMock()
    candle.time = t
    candle.open = o
    candle.high = h
    candle.low = l
    candle.close = c
    return candle


def _make_config() -> MagicMock:
    cfg = MagicMock()
    cfg.TIMEFRAME = "M5"
    cfg.CANDLE_COUNT = 100
    return cfg


def _make_sym_state(
    symbol: str = "EURUSD",
    last_closed_time: int | None = None,
    iterations: int = 0,
    stale_counter: int = 0,
) -> MagicMock:
    state = MagicMock()
    state.symbol = symbol
    state.last_closed_time = last_closed_time
    state.iterations = iterations
    state.feed = MagicMock()
    _candle_result = MagicMock()
    _candle_result.is_stale = False
    state.stale_monitor.on_candle.return_value = _candle_result
    state._feed_stale_alerted = False
    state._bar_stale_counter = stale_counter
    state._stale_warned = False
    return state


class _Cp1252StrictStream:
    """Mimic the trial launcher's redirected stdout: cp1252, strict errors.

    Any character cp1252 cannot encode raises UnicodeEncodeError on write,
    exactly as it did in production before the fix.
    """

    def __init__(self) -> None:
        self._buffer = io.BytesIO()
        self._stream = io.TextIOWrapper(
            self._buffer, encoding="cp1252", errors="strict"
        )

    def write(self, s: str) -> int:
        return self._stream.write(s)

    def flush(self) -> None:
        self._stream.flush()

    def getvalue(self) -> str:
        self._stream.flush()
        return self._buffer.getvalue().decode("cp1252")


@contextmanager
def _cp1252_captured(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    """Disable pytest's capture and route stdout to a cp1252, strict stream.

    pytest otherwise absorbs print() output, so the UnicodeEncodeError would
    never surface. Disabling capture lets us exercise the real production
    failure condition (redirected cp1252 stdout).
    """
    stream = _Cp1252StrictStream()
    with capsys.disabled():
        monkeypatch.setattr(sys, "stdout", stream, raising=False)
        try:
            yield stream
        finally:
            monkeypatch.undo()


# ─── ROOT-CAUSE DOCUMENTATION ─────────────────────────────────────────────────


def test_original_arrow_character_causes_unicode_error_under_cp1252(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Document the exact failure mechanism that invalidated Trial 2.

    The character U+2192 (right arrow) used by the pre-fix [CANDLE FEED]
    diagnostic cannot be encoded under cp1252 and raises UnicodeEncodeError,
    which killed every per-symbol scan under redirection.
    """
    with _cp1252_captured(monkeypatch, capsys) as stream:
        with pytest.raises(UnicodeEncodeError):
            print("[CANDLE FEED] EURUSD | last 5 bars: 01:50 -> 01:55 with \u2192 here")


# ─── FETCH_BAR COMPLETION UNDER REDIRECTED cp1252 STDOUT ──────────────────────


@pytest.mark.parametrize("feed_age_s", [0, 300, 599])
def test_fetch_bar_completes_under_cp1252_redirected_stdout(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    feed_age_s: int,
) -> None:
    """fetch_bar() must return a BarResult and update state under cp1252 stdout.

    Core regression for Trial 2: previously this exact call path raised
    UnicodeEncodeError at the [CANDLE FEED] print and returned nothing; the
    symbol was silently skipped every cycle.
    """
    now = int(time.time())
    bar_time = now - feed_age_s  # recent -> HEALTHY
    candles = [_make_candle(bar_time - 5 * 60 + 60 * i) for i in range(6)]
    closed_i = len(candles) - 1
    closed_time = candles[closed_i].time

    config = _make_config()
    sym_state = _make_sym_state(last_closed_time=candles[closed_i - 1].time)
    sym_state.feed.copy_rates_closed.return_value = candles

    provider = BarProvider(config)
    with patch("core.runtime.bar_provider._TICK_UTC_OFFSET_SECONDS", 0, create=True):
        with patch("data.mt5_data._TICK_UTC_OFFSET_SECONDS", 0):
            with patch("core.shadow_trades.get_shadow_engine"):
                with patch("core.discord_notifier.send_discord"):
                    with _cp1252_captured(monkeypatch, capsys) as stream:
                        result = provider.fetch_bar(sym_state)
                    captured = stream.getvalue()

    assert isinstance(result, BarResult)
    assert result.closed_time == closed_time
    assert result.closed_i == closed_i
    assert result.feed_state == "HEALTHY"
    assert result.is_new_bar is True

    # State update reached (previously unreachable because the print crashed first)
    assert sym_state.last_closed_time == closed_time
    assert sym_state.iterations == 1

    # Diagnostics still emitted, now ASCII-safe under cp1252
    assert "[CANDLE FEED]" in captured
    assert "[BAR CHECK]" in captured
    assert " -> " in captured  # dash replaces the arrow separator
    assert "\u2192" not in captured  # no raw arrow survives


def test_bar_stall_warning_is_ascii_safe(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The [BAR STALL] branch (previously containing a warning-sign emoji) must
    not raise UnicodeEncodeError under cp1252 stdout either."""
    now = int(time.time())
    bar_time = now - 30  # new bar -> not FEED_STALE
    candles = [_make_candle(bar_time - 5 * 60 + 60 * i) for i in range(6)]
    closed_i = len(candles) - 1
    closed_time = candles[closed_i].time

    config = _make_config()
    # Last closed time EQUALS current -> dedup path -> stale counter increments
    # to 100, which trips the >50 and %50==0 [BAR STALL] diagnostic print.
    sym_state = _make_sym_state(
        last_closed_time=closed_time, iterations=1, stale_counter=99
    )
    sym_state.feed.copy_rates_closed.return_value = candles

    provider = BarProvider(config)
    with patch("data.mt5_data._TICK_UTC_OFFSET_SECONDS", 0):
        with patch("core.shadow_trades.get_shadow_engine"):
            with patch("core.discord_notifier.send_discord"):
                with _cp1252_captured(monkeypatch, capsys) as stream:
                    result = provider.fetch_bar(sym_state)
                captured = stream.getvalue()

    assert result is None  # dedup branch
    assert "[BAR STALL]" in captured
    assert "\u26a0" not in captured  # warning emoji removed
    assert "[WARN] MT5 FEED MAY BE FROZEN" in captured


# ─── DOWNSTREAM REACHABILITY PROOF (Phase 6) ──────────────────────────────────


def test_downstream_pattern_gate_reachable_after_fetch_bar(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Prove the Trial-2 defect is gone end-to-end: after fetch_bar() completes
    under cp1252 redirected stdout, the pre-engine gates (which own pattern
    detection / raw_patterns) are actually invoked — i.e. the scanner can now
    proceed from BarProvider toward the opportunity layer without running LIVE.

    No broker, no orders, no Shadow gate, no position mutations: pure offline
    evaluation against synthetic candles.
    """
    from core.runtime.pre_engine_gates import evaluate_pre_engine_gates, GateResult

    now = int(time.time())
    bar_time = now - 60  # HEALTHY, new bar
    candles = [_make_candle(bar_time - 5 * 60 + 60 * i) for i in range(6)]
    closed_i = len(candles) - 1
    last_time = candles[closed_i - 1].time

    config = _make_config()
    sym_state = _make_sym_state(last_closed_time=last_time, iterations=0)
    sym_state.feed.copy_rates_closed.return_value = candles

    provider = BarProvider(config)
    with patch("data.mt5_data._TICK_UTC_OFFSET_SECONDS", 0):
        with patch("core.shadow_trades.get_shadow_engine"):
            with patch("core.discord_notifier.send_discord"):
                with _cp1252_captured(monkeypatch, capsys) as stream:
                    bar = provider.fetch_bar(sym_state)
                    # --- Pattern-detection entry point reachable offline ---
                    gate = evaluate_pre_engine_gates(
                        kill_active=False,
                        daily_loss_blocked=False,
                        candles=candles,
                        closed_i=closed_i,
                        symbol="EURUSD",
                        cycle_id=1,
                        closed_time=bar.closed_time,
                    )
                captured = stream.getvalue()

    assert isinstance(bar, BarResult)  # fetch_bar completed under cp1252
    assert isinstance(gate, GateResult)
    assert "[PIPELINE ENTRY]" in captured
    assert "[PATTERN RESULT]" in captured

    # Reachability contract: the pattern gate either produced raw_patterns
    # (allowed=True -> opportunity factory will consume them at live_scanner.py:594-608)
    # or rejected on no-pattern grounds (allowed=False, block_outcome=PATTERN_REJECT).
    # Either outcome requires pattern detection to have RUN — impossible while
    # fetch_bar() crashed in Trial 2.
    if gate.allowed:
        assert isinstance(gate.raw_patterns, list)
        assert len(gate.raw_patterns) >= 1
    else:
        assert gate.block_outcome == "PATTERN_REJECT"
        assert gate.block_reason == "no_patterns_detected"
