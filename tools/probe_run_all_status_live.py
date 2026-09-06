"""READ-ONLY live proof: run_all() summary status vs actual report status.

For representative questions, calls the real runner directly (capturing the
authoritative report["status"]) and then runs the real run_all() flow, and
verifies the two statuses match exactly. Read-only; no S3 writes.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("RESEARCH_AWS_PROFILE", "trading-bot-new")

TARGETS = ["E1", "E3", "S1", "X2", "X3", "X4", "M9", "Q16"]


def main() -> None:
    from research_engine.runner_discovery import get_all_runners
    from research_engine.experiments.research_runner import run_all

    runners = get_all_runners()

    print("=" * 78)
    print("question_id | actual report status        | run_all summary status      | n")
    print("-" * 78)

    mismatches = 0
    actual = {}
    for qid in TARGETS:
        runner = runners.get(qid)
        if runner is None:
            print(f"{qid:<11} | (no runner discovered)".ljust(78))
            continue
        try:
            report = runner()
            status = report.get("status", "?")
            rec = report.get("recommendation", "")
            sample = report.get("dataset", {}).get("sample_size", 0)
            actual[qid] = status
            print(f"{qid:<11} | {status:<27} | (see run_all below)         | {sample}")
            print(f"{'' :<11} | recommendation: {rec}")
        except Exception as e:
            actual[qid] = f"ERROR: {str(e)[:60]}"
            print(f"{qid:<11} | ERROR: {str(e)[:60]}")

    print("-" * 78)
    summary = run_all()

    print(f"{'question_id':<11} | {'actual report status':<27} | "
          f"{'run_all summary status':<27} | n   | match")
    print("-" * 78)
    for qid in TARGETS:
        if qid not in actual or qid not in summary:
            continue
        s = summary[qid]
        match = "OK" if s["status"] == actual[qid] else "MISMATCH"
        if match != "OK":
            mismatches += 1
        print(f"{qid:<11} | {actual[qid]:<27} | {s['status']:<27} | "
              f"{s['sample']:<3} | {match}")

    print("-" * 78)
    print("full run_all summary:")
    for qid, info in sorted(summary.items()):
        print(f"  {qid}: {info['status']} (n={info.get('sample', '?')}) "
              f"[source={info.get('status_source', '?')}]")

    print(f"\nmismatches: {mismatches}")
    print("PROOF COMPLETE (read-only)")


if __name__ == "__main__":
    main()
