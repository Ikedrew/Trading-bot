"""Gap 6 sandbox proof: weekly cycle + change report on real evidence counts.

Research lifecycle state is redirected to a SANDBOX cwd (no real research
state mutation). Evidence counts are the REAL canonical counts captured live
from S3 earlier in this session (shadow=1253, trade_truth=12, trace=1458);
week-2 growth is production-shaped synthetic per the task's fallback rule.
The scheduled entry point is additionally exercised against real (expired-SSO)
AWS to prove loud failure behaviour.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("RESEARCH_AWS_PROFILE", "trading-bot-new")

# Real canonical evidence counts captured LIVE from S3 earlier this session.
WEEK1 = {"shadow_outcomes": 1253, "trade_truth": 12, "decision_trace": 1458}
WEEK2 = {"shadow_outcomes": 1811, "trade_truth": 37, "decision_trace": 1520}


def _install_evidence(counts: dict[str, int]) -> None:
    import research_engine.data_access.loaders as loaders
    import research_engine.data_access.shadow_runtime_ingestion as sri
    import research_engine.experiments.research_runner as rr

    sri.ingest_completed_shadow_trades = lambda **k: [{"s": i} for i in range(counts["shadow_outcomes"])]
    loaders.load_trade_truth = lambda *a, **k: [{"t": i} for i in range(counts["trade_truth"])]
    loaders.load_decision_trace = lambda *a, **k: [{"d": i} for i in range(counts["decision_trace"])]
    # Real live question statuses captured in the Gap-4 live probes this session;
    # week-2 shows the legitimate transition produced by accumulated evidence.
    rr.run_all = lambda: {
        "Q16": {"status": "COMPLETE", "recommendation": "SHADOW_TRUSTED",
                "sample": 9, "confidence": "LOW", "status_source": "report"},
        "E3": {"status": "INSUFFICIENT_DATA", "recommendation": "COMPLETE",
               "sample": 0, "confidence": "LOW", "status_source": "report"},
    } if counts["trade_truth"] == 12 else {
        "Q16": {"status": "COMPLETE", "recommendation": "SHADOW_TRUSTED",
                "sample": 24, "confidence": "MEDIUM", "status_source": "report"},
        "E3": {"status": "COMPLETE", "recommendation": "COMPLETE",
               "sample": 37, "confidence": "MEDIUM", "status_source": "report"},
    }


def main() -> None:
    from research_engine.lifecycle import cycle_snapshot as cs
    from research_engine.lifecycle.research_cycle_runner import (
        ResearchCycleConfig,
        ResearchCycleRunner,
    )

    with tempfile.TemporaryDirectory() as sandbox:
        os.chdir(sandbox)
        print("=" * 72)
        print("GAP 6 SANDBOX PROOF (sandboxed research state, real counts)")
        print("=" * 72)

        runner = ResearchCycleRunner(ResearchCycleConfig(min_cycle_interval_seconds=0.0))

        # ── week 1: real evidence counts -> baseline ─────────────────────
        _install_evidence(WEEK1)
        r1 = runner.run_cycle()
        print(f"\n[week 1] status={r1.status} kind={r1.change_kind}")
        snap1 = json.loads(Path(r1.snapshot_path).read_text(encoding="utf-8"))
        print("  snapshot evidence:", snap1["evidence"])
        print("  snapshot questions: Q16 ->", snap1["questions"]["Q16"])

        # ── idempotency: identical rerun ──────────────────────────────────
        r1b = runner.run_cycle()
        print(f"\n[rerun unchanged] status={r1b.status} kind={r1b.change_kind} "
              "(no material change expected)")

        # ── week 2: evidence accumulates -> material change report ───────
        _install_evidence(WEEK2)
        r2 = runner.run_cycle()
        print(f"\n[week 2] status={r2.status} kind={r2.change_kind}")
        report = json.loads(Path(r2.change_report_path).read_text(encoding="utf-8"))
        print("  previous_cycle_id:", report["previous_cycle_id"])
        print("  evidence_growth:", json.dumps(report["evidence_growth"]))
        print("  question_changes:", json.dumps(report["question_changes"]))

        print("\n" + "=" * 72)
        print("HUMAN-READABLE WEEKLY CHANGE REPORT (week 2)")
        print("=" * 72)
        print((cs._CYCLES_DIR / f"{r2.cycle_id}_change_report.txt").read_text(encoding="utf-8"))

        # baseline invariant: week-2 compared against the last SUCCESSFUL
        # cycle (the idempotent rerun, which succeeded with unchanged evidence)
        assert report["previous_cycle_id"] == r1b.cycle_id
        # leave the sandbox before cleanup (Windows cwd lock)
        os.chdir(ROOT)

    # ── scheduled entry point against real AWS (expired SSO) ─────────────
    print("=" * 72)
    print("SCHEDULED ENTRY POINT (real invocation, expired SSO expected)")
    print("=" * 72)
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "run_research_cycle.py"),
         "--mode=DETECT_ONLY", "--cooldown=0"],
        capture_output=True, text=True, timeout=300, cwd=str(ROOT),
    )
    combined = (proc.stdout or "") + (proc.stderr or "")
    print("exit code:", proc.returncode, "(1 = cycle failed, per exit-code contract)")
    for line in combined.splitlines():
        if "RESEARCH CYCLE" in line or "ResearchDataSourceError" in line or "sso" in line.lower():
            print("  ", line.strip()[:150])
    assert "UnicodeEncodeError" not in combined
    print("\nPROOF COMPLETE")


if __name__ == "__main__":
    main()
