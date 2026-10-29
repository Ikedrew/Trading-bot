"""
Encoding-safe console output for the Research Engine CLI.

Guarantee: console/reporting output must never crash the process solely
because the host console cannot encode a decorative Unicode character
(e.g. Windows cp1252 consoles raising ``UnicodeEncodeError`` on box-drawing
characters or emoji).

Design (smallest robust mechanism, no global behaviour change):

    configure_console(*streams)
        Best-effort: streams that support ``reconfigure()`` (real
        TextIOWrappers) are set to ``errors="replace"`` on their CURRENT
        encoding, so unencodable characters degrade to "?" instead of
        raising, while UTF-8 consoles keep rendering everything and
        redirected files stay in the host encoding. Streams WITHOUT
        ``reconfigure()`` (pytest captures, StringIO, pipes) are left
        untouched - no crash, no assumption about the stream type.

    safe_print(*args, file=...)
        A print() that catches ONLY ``UnicodeEncodeError`` and retries the
        single message with ``errors="replace"`` against the stream's own
        encoding. Research/data exceptions are NEVER caught here - only
        the encoding/rendering failure is degraded.

This module never suppresses real research exceptions and never converts a
genuine failure into a successful exit code.
"""

from __future__ import annotations

import sys
from typing import Any

__all__ = ["configure_console", "safe_print"]


def configure_console(*streams: Any) -> int:
    """
    Best-effort encoding-safety for console streams.

    Reconfigures each stream that supports ``reconfigure()`` to use
    ``errors="replace"`` on its current encoding. Streams without
    ``reconfigure()`` are skipped silently (they may be pytest captures,
    StringIO, or other non-standard wrappers).

    Returns the number of streams reconfigured. Never raises.
    """
    if not streams:
        streams = (sys.stdout, sys.stderr)
    configured = 0
    for stream in streams:
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(errors="replace")
            configured += 1
        except (ValueError, OSError):
            # Stream closed or reconfigure unsupported for this type —
            # degrade to the safe_print path instead.
            continue
    return configured


def _degrade(text: str, encoding: str | None) -> str:
    """Replace unencodable characters for the stream's own encoding."""
    enc = encoding or "ascii"
    return text.encode(enc, errors="replace").decode(enc, errors="replace")


def safe_print(*args: Any, file: Any = None, sep: str = " ",
               end: str = "\n", flush: bool = False) -> None:
    """
    print() that degrades safely instead of raising UnicodeEncodeError.

    Only ``UnicodeEncodeError`` is handled: the exact message is re-encoded
    with ``errors="replace"`` against the target stream's own encoding and
    written directly. Every other exception (S3 failures, data errors,
    research bugs) propagates untouched.
    """
    stream = file if file is not None else sys.stdout
    try:
        print(*args, file=stream, sep=sep, end=end, flush=flush)
    except UnicodeEncodeError:
        message = sep.join(str(a) for a in args)
        degraded = _degrade(message, getattr(stream, "encoding", None))
        stream.write(degraded)
        stream.write(_degrade(end, getattr(stream, "encoding", None)))
        if flush:
            stream.flush()
