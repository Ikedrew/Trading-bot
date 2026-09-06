"""
Gap 5 — Windows console encoding-safety tests.

Proves Research Engine CLI output never crashes solely because the host
console cannot encode a decorative Unicode character, while REAL research
exceptions (S3/data/runner failures) still propagate untouched.

All tests are synthetic - production AWS is NEVER touched.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_engine.console import configure_console, safe_print
from research_engine.data_access.s3_source import ResearchDataSourceError

UNICODE_BANNER = "\u2500" * 60          # box-drawing (main.py/edge banners)
EMOJI_OK = "\u2705"                      # check-mark emoji
WARNING = "\u26a0\ufe0f"                 # warning sign + variation selector


def _stream(encoding: str) -> tuple[io.TextIOWrapper, io.BytesIO]:
    buf = io.BytesIO()
    return io.TextIOWrapper(buf, encoding=encoding, errors="strict"), buf


# ═══════════════════════════════════════════════════════════════════════════════
# UTF-8 CONSOLE
# ═══════════════════════════════════════════════════════════════════════════════


class TestUtf8Console:
    def test_unicode_capable_output_works_normally(self):
        stream, buf = _stream("utf-8")
        safe_print(UNICODE_BANNER, file=stream)
        safe_print(f"{EMOJI_OK} Research Engine Phase 1 complete.", file=stream)
        stream.flush()
        out = buf.getvalue().decode("utf-8")
        assert UNICODE_BANNER in out
        assert "Research Engine Phase 1 complete." in out

    def test_configure_console_reconfigures_reconfigurable_stream(self):
        stream, _ = _stream("utf-8")
        assert configure_console(stream) == 1

    def test_configure_console_replaces_instead_of_raising(self):
        stream, buf = _stream("cp1252")
        configure_console(stream)
        stream.write(UNICODE_BANNER)  # would raise before reconfigure
        stream.flush()
        assert b"?" in buf.getvalue()


# ═══════════════════════════════════════════════════════════════════════════════
# RESTRICTED WINDOWS ENCODING (cp1252)
# ═══════════════════════════════════════════════════════════════════════════════


class TestRestrictedWindowsEncoding:
    def test_cp1252_console_does_not_raise_unicodeencodeerror(self):
        stream, buf = _stream("cp1252")
        # Every decoration that previously crashed main.py / edge runners
        safe_print(UNICODE_BANNER, file=stream)
        safe_print(f"\n{WARNING}  No completed shadow outcomes available", file=stream)
        safe_print(f"{EMOJI_OK} Research Engine Phase 1 complete.", file=stream)
        stream.flush()
        out = buf.getvalue().decode("cp1252")
        assert "No completed shadow outcomes available" in out
        assert "Research Engine Phase 1 complete." in out

    def test_semantic_text_survives_ascii_fallback(self):
        """The research message/status must remain visible after degradation."""
        stream, buf = _stream("cp1252")
        safe_print("CONCLUSION: Shadow model shows moderate predictive power", file=stream)
        safe_print(f"{UNICODE_BANNER}", file=stream)
        stream.flush()
        out = buf.getvalue().decode("cp1252")
        assert "CONCLUSION: Shadow model shows moderate predictive power" in out

    def test_ascii_only_output_is_untouched(self):
        stream, buf = _stream("cp1252")
        safe_print("RESEARCH ENGINE - Phase 1: Shadow Validation (Q16)", file=stream)
        stream.flush()
        out = buf.getvalue().decode("cp1252").replace("\r\n", "\n")
        assert out == "RESEARCH ENGINE - Phase 1: Shadow Validation (Q16)\n"


# ═══════════════════════════════════════════════════════════════════════════════
# REDIRECTED / CAPTURED / NON-STANDARD STREAMS
# ═══════════════════════════════════════════════════════════════════════════════


class TestNonStandardStreams:
    def test_captured_stream_stringio_works(self, capsys):
        safe_print("RESEARCH ENGINE - Phase 1")
        safe_print(UNICODE_BANNER)  # capsys stream handles unicode
        captured = capsys.readouterr()
        assert "RESEARCH ENGINE - Phase 1" in captured.out

    def test_stream_without_reconfigure_does_not_crash_configure(self):
        class _PlainStream:  # no reconfigure attribute at all
            encoding = "cp1252"
            def write(self, text):
                return len(text)
            def flush(self):
                return None

        assert configure_console(_PlainStream(), sys.stdout if False else _PlainStream()) == 0

    def test_safe_print_works_on_stream_without_reconfigure(self):
        class _StrictCp1252:  # realistic redirected-file-like wrapper
            encoding = "cp1252"
            def __init__(self):
                self.chunks = []
            def write(self, text):
                self.chunks.append(text.encode("cp1252", errors="strict"))
                return len(text)
            def flush(self):
                return None

        stream = _StrictCp1252()
        safe_print(UNICODE_BANNER, file=stream)   # would raise via print()
        safe_print("[OK] done", file=stream)
        out = b"".join(stream.chunks).decode("cp1252")
        assert "[OK] done" in out
        assert "?" in out  # decoration degraded, process alive


# ═══════════════════════════════════════════════════════════════════════════════
# STDERR
# ═══════════════════════════════════════════════════════════════════════════════


class TestStderr:
    def test_stderr_rendering_is_encoding_safe(self):
        stream, buf = _stream("cp1252")
        safe_print(f"{WARNING} research warning", file=stream)
        stream.flush()
        assert "research warning" in buf.getvalue().decode("cp1252")

    def test_configure_console_covers_stderr(self):
        err, _ = _stream("cp1252")
        assert configure_console(err) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# REAL EXCEPTION PROPAGATION (the fix must NOT mask research errors)
# ═══════════════════════════════════════════════════════════════════════════════


class TestRealExceptionPropagation:
    def test_research_data_source_error_propagates_through_safe_print(self):
        class _FailingStream:
            encoding = "cp1252"
            def write(self, text):
                raise ResearchDataSourceError("S3 unavailable (test)")
            def flush(self):
                return None

        with pytest.raises(ResearchDataSourceError):
            safe_print("hello", file=_FailingStream())

    def test_non_encoding_write_errors_propagate(self):
        class _BrokenStream:
            encoding = "cp1252"
            def write(self, text):
                raise OSError("disk full")
            def flush(self):
                return None

        with pytest.raises(OSError):
            safe_print("hello", file=_BrokenStream())

    def test_main_entry_point_surfaces_research_errors(self, tmp_path, monkeypatch):
        """A deliberate S3 failure must still propagate out of main() —
        the encoding fix is NOT a generic exception catcher."""
        import research_engine.main as main_mod

        def _boom():
            raise ResearchDataSourceError(
                "AWS failure via research profile (test): run aws sso login"
            )

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(main_mod, "ingest_completed_shadow_trades", _boom)
        monkeypatch.setattr(main_mod, "load_trade_truth", lambda *a, **k: [])
        with pytest.raises(ResearchDataSourceError, match="aws sso login"):
            main_mod.main()

    def test_main_entry_point_encoding_failure_does_not_kill_run(self, tmp_path, monkeypatch):
        """If the console cannot encode a decoration mid-run, main() still
        completes its research work and reports its result."""
        import research_engine.main as main_mod

        class _StrictCp1252:
            encoding = "cp1252"
            def __init__(self):
                self.chunks = []
            def write(self, text):
                # simulate a real console: strict encoding, no reconfigure
                self.chunks.append(text.encode("cp1252", errors="strict"))
                return len(text)
            def flush(self):
                return None

        forced = _StrictCp1252()
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(main_mod, "ingest_completed_shadow_trades", lambda **k: [
            {"identity": {"canonical_opportunity_id": "EURUSD*1*HAMMER",
                          "shadow_trade_id": "n1", "symbol": "EURUSD",
                          "shadow_type": "PRIMARY_HORIZON_SIMULATION"},
             "decision_snapshot": {"direction": "BUY", "pattern": "HAMMER", "score": 70},
             "simulated_outcome": {"pnl_r_multiple": 1.5, "exit_reason": "take_profit"}}] * 3)
        monkeypatch.setattr(main_mod, "load_trade_truth", lambda *a, **k: [
            {"identity": {"trade_id": "p1", "correlation_id": "COR-1",
                          "canonical_opportunity_id": "EURUSD*1*HAMMER",
                          "symbol": "EURUSD"},
             "outcome": {"r_multiple_realised": 1.2},
             "exit": {"exit_reason": "take_profit_hit"}}])

        # NOTE: intentionally NOT patching console helpers — main() itself
        # must degrade safely on this restrictive stream.
        import contextlib
        stdout_backup = sys.stdout
        sys.stdout = forced
        try:
            main_mod.main()
        finally:
            sys.stdout = stdout_backup

        out = b"".join(forced.chunks).decode("cp1252")
        assert "SHADOW VALIDATION REPORT" in out          # research ran
        assert "CONCLUSION:" in out                        # real result shown
        assert "Report saved:" in out                      # report persisted
        assert "[OK] Research Engine Phase 1 complete." in out


# ═══════════════════════════════════════════════════════════════════════════════
# GAP 4 REGRESSION — status output contract unchanged
# ═══════════════════════════════════════════════════════════════════════════════


class TestGap4StatusContract:
    def test_status_output_uses_authoritative_status_verbatim(self):
        """Statuses rendered through the console path must be the
        authoritative report["status"] values, unmodified."""
        stream, buf = _stream("cp1252")
        for status in ("COMPLETE", "INSUFFICIENT_DATA", "BLOCKED", "WAIT",
                       "WAITING_DATA", "UNKNOWN_STATUS"):
            safe_print(f"  {status} (n=0)", file=stream)
        stream.flush()
        out = buf.getvalue().decode("cp1252")
        for status in ("COMPLETE", "INSUFFICIENT_DATA", "BLOCKED", "WAIT",
                       "WAITING_DATA", "UNKNOWN_STATUS"):
            assert f"  {status} (n=0)" in out
