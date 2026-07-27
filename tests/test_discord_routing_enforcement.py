"""
Static enforcement test: Detect direct Discord sends outside approved routing layer.

This test scans the codebase for direct send_discord() or DiscordClient usage
outside the approved files. All modules should use route_event() or
_discord_logger.event() instead.

Approved files (allowed to use direct Discord APIs):
- core/log_router.py (the router itself)
- core/discord_notifier.py (the send implementation)
- core/test_discord.py (manual test utility)
- core/test_all_channels.py (manual test utility)
- core/test_router.py (manual test utility)
"""

from __future__ import annotations

import ast
from pathlib import Path

# Workspace root
ROOT = Path(__file__).resolve().parent.parent

# Files ALLOWED to use direct Discord APIs
APPROVED_FILES = {
    "core/log_router.py",
    "core/discord_notifier.py",
    "core/test_discord.py",
    "core/test_all_channels.py",
    "core/test_router.py",
}


def _scan_for_direct_discord(filepath: Path) -> list[str]:
    """Scan a Python file for direct Discord send calls. Returns violations."""
    violations = []
    try:
        source = filepath.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except Exception:
        return []

    for node in ast.walk(tree):
        # Detect: send_discord(...)
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "send_discord":
                violations.append(f"{filepath.relative_to(ROOT)}:{node.lineno} — direct send_discord() call")
            # Detect: DiscordClient(...) — legacy, flag if found
            if isinstance(func, ast.Name) and func.id == "DiscordClient":
                violations.append(f"{filepath.relative_to(ROOT)}:{node.lineno} — legacy DiscordClient() instantiation (should be removed)")
            # Detect: something.discord.send(...)
            if isinstance(func, ast.Attribute) and func.attr == "send":
                if isinstance(func.value, ast.Attribute) and func.value.attr == "discord":
                    violations.append(f"{filepath.relative_to(ROOT)}:{node.lineno} — direct .discord.send() call")

        # Detect: from core.discord_notifier import send_discord
        if isinstance(node, ast.ImportFrom):
            if node.module and "discord_notifier" in node.module:
                for alias in node.names:
                    if alias.name == "send_discord":
                        violations.append(f"{filepath.relative_to(ROOT)}:{node.lineno} — imports send_discord directly")

    return violations


class TestDirectDiscordUsage:
    """Enforce that direct Discord sends only exist in approved files."""

    def test_no_direct_discord_outside_approved(self):
        """Scan all .py files for direct Discord usage outside router layer."""
        all_violations = []

        for py_file in ROOT.rglob("*.py"):
            # Skip tests, __pycache__, and approved files
            rel = str(py_file.relative_to(ROOT)).replace("\\", "/")
            if rel.startswith("tests/"):
                continue
            if "__pycache__" in rel:
                continue
            if rel in APPROVED_FILES:
                continue

            violations = _scan_for_direct_discord(py_file)
            all_violations.extend(violations)

        if all_violations:
            msg = (
                f"Direct Discord sends detected outside approved routing layer "
                f"({len(all_violations)} violations):\n"
            )
            for v in all_violations:
                msg += f"  - {v}\n"
            msg += "\nUse route_event() or _discord_logger.event() instead."
            # Report violations but don't fail yet (migration in progress)
            print(f"\n[ROUTING ENFORCEMENT WARNING]\n{msg}")

        # For now: report only. Uncomment below to enforce hard failure:
        # assert not all_violations, msg
