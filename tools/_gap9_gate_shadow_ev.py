"""One-shot Gap-9 gate for shadow_ev entry points (idempotent)."""
from pathlib import Path
import py_compile

FILES = [
    "research_engine/shadow_ev/run_shadow_ev.py",
    "research_engine/shadow_ev/run_walk_forward.py",
]
GATE = (
    "\n"
    "# Local replay candles are an EXPLICIT offline fixture mode (Gap 3/9):\n"
    "# normal Research Engine execution never consumes replay_data/.\n"
    'OFFLINE_REPLAY_FLAG = "--offline-replay"\n'
)
MAIN_GATE = (
    "def main() -> None:\n"
    "    configure_console(sys.stdout, sys.stderr)\n"
    "    if OFFLINE_REPLAY_FLAG not in sys.argv:\n"
    '        safe_print("[REFUSED] " + __name__ + " requires explicit --offline-replay.")\n'
    '        safe_print("       Local replay_data/ candles are an offline fixture mode,")\n'
    '        safe_print("       never a production-evidence source. Re-run with --offline-replay.")\n'
    "        raise SystemExit(2)\n"
)

for fname in FILES:
    p = Path(fname)
    src = p.read_text(encoding="utf-8")
    if "OFFLINE_REPLAY_FLAG" in src:
        print("already gated:", fname)
        continue
    anchor = "from research_engine.reports.generator import generate_report\n"
    assert anchor in src, fname
    src = src.replace(
        anchor,
        "from research_engine.console import configure_console, safe_print\n"
        + anchor
        + GATE,
        1,
    )
    anchor2 = "def main() -> None:\n"
    assert anchor2 in src, fname
    src = src.replace(anchor2, MAIN_GATE, 1)
    src = src.replace("print(", "safe_print(")
    p.write_text(src, encoding="utf-8")
    print("gated:", fname)

for fname in FILES:
    py_compile.compile(fname, doraise=True)
print("py_compile OK")
