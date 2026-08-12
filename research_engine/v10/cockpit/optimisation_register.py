"""
Optimisation Register — Lightweight tracking of research-driven changes.

Records: evidence → decision → change → validation
The human remains the decision-maker.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REGISTER_FILE = Path("reports/research/optimisation_register.jsonl")


def handle_optimisation_command(args: list[str]):
    """Handle optimisation sub-commands."""
    if not args:
        print("Usage: research optimisation [list|create|validate <ID>]")
        return

    cmd = args[0].lower()
    if cmd == "list":
        _list_optimisations()
    elif cmd == "create":
        _create_optimisation()
    elif cmd == "validate" and len(args) >= 2:
        _validate_optimisation(args[1])
    else:
        print(f"Unknown optimisation command: {cmd}")


def _list_optimisations():
    """List all recorded optimisations."""
    entries = _load_register()
    if not entries:
        print("No optimisations recorded.")
        return

    print("OPTIMISATION REGISTER")
    print("=" * 60)
    for e in entries:
        print(f"\n  [{e['id']}] {e['status']}")
        print(f"  Date: {e['date']}")
        print(f"  Bottleneck: {e['bottleneck']}")
        print(f"  Change: {e.get('proposed_change', '')[:80]}")
        print(f"  Evidence: {', '.join(e.get('evidence_questions', []))}")


def _create_optimisation():
    """Interactively create a new optimisation record."""
    print("CREATE OPTIMISATION")
    print("=" * 40)

    entry = {
        "id": f"OPT-{uuid.uuid4().hex[:6].upper()}",
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "bottleneck": input("  Bottleneck/area: ").strip(),
        "evidence_questions": input("  Evidence question IDs (comma-sep): ").strip().split(","),
        "proposed_change": input("  Proposed change: ").strip(),
        "actual_change": "",
        "expected_effect": input("  Expected effect: ").strip(),
        "validation_questions": input("  Validation question IDs (comma-sep): ").strip().split(","),
        "status": "PROPOSED",
    }

    _append_register(entry)
    print(f"\n  Created: {entry['id']}")
    print(f"  Status: PROPOSED")
    print(f"  Next: implement the change, then run: research optimisation validate {entry['id']}")


def _validate_optimisation(opt_id: str):
    """Show validation status for an optimisation."""
    entries = _load_register()
    entry = next((e for e in entries if e["id"] == opt_id), None)

    if entry is None:
        print(f"Optimisation '{opt_id}' not found.")
        return

    print(f"OPTIMISATION VALIDATION: {opt_id}")
    print("=" * 50)
    print(f"  Bottleneck: {entry['bottleneck']}")
    print(f"  Change: {entry.get('proposed_change', '')}")
    print(f"  Status: {entry['status']}")
    print(f"\n  Validation questions:")

    questions_dir = Path("reports/research/questions")
    for qid in entry.get("validation_questions", []):
        qid = qid.strip()
        if not qid:
            continue
        latest = questions_dir / qid / "latest.json"
        if latest.exists():
            f = json.loads(latest.read_text(encoding="utf-8"))
            print(f"    {qid}: {f.get('outcome', '?')} ({f.get('confidence', '?')})")
            print(f"      Run: {f.get('run_id', '?')}")
        else:
            print(f"    {qid}: NOT RUN")

    print(f"\n  To validate: rerun the validation questions after implementing the change.")
    print(f"  Compare pre/post results to determine if the change helped.")


def _load_register() -> list[dict]:
    if not _REGISTER_FILE.exists():
        return []
    entries = []
    for line in _REGISTER_FILE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                entries.append(json.loads(line))
            except Exception:
                pass
    return entries


def _append_register(entry: dict):
    _REGISTER_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_REGISTER_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")
