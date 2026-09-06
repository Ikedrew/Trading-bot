"""After-fix proof: run_all summary status == actual report status.

Uses (a) the REAL runner functions on their deterministic insufficient-data
code paths (real S3 dependency stubbed to the same n=0 state as the live
before-run) and (b) the REAL persisted reports from the earlier live run.
Read-only; no AWS required (SSO session expired mid-task - see report).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("RESEARCH_AWS_PROFILE", "trading-bot-new")


class _EmptySource:
    """Stub matching a genuinely empty canonical scope (n=0, as in the live
    before-run where E3/S1/M9 reported n=0)."""

    def read_dataset(self, dataset, **kwargs):
        return []


def main() -> None:
    from research_engine.experiments.research_runner import _extract_run_status, _extract_sample

    print("=" * 86)
    print("question_id | actual report status    | run_all summary status  | n    | before (buggy)")
    print("-" * 86)

    # ── E3/S1: the REAL run_q24 runner, n=0 (same live state as before-run) ──
    import research_engine.experiments.legacy_canonical as lc
    lc._load_jsonl = lambda dataset: []
    report = lc.run_q24()
    status, source = _extract_run_status(report)
    print(f"{'E3/S1':<11} | {report['status']:<23} | {status:<23} | "
          f"{_extract_sample(report):<4} | COMPLETE (n=0)")

    # ── M9: the REAL runner on its insufficient-data code path (n=0) ────────
    import research_engine.data_access.s3_source as s3s
    import research_engine.data_access.shadow_runtime_ingestion as sri
    orig_source = s3s.get_default_source
    orig_ingest = sri.ingest_completed_shadow_trades
    s3s.get_default_source = lambda: _EmptySource()
    sri.ingest_completed_shadow_trades = lambda **k: []
    try:
        from research_engine.experiments.m9_phase_pattern import run_m9_phase_pattern
        report = run_m9_phase_pattern()
    finally:
        s3s.get_default_source = orig_source
        sri.ingest_completed_shadow_trades = orig_ingest
    status, source = _extract_run_status(report)
    print(f"{'M9':<11} | {report['status']:<23} | {status:<23} | "
          f"{_extract_sample(report):<4} | WAIT (n=0)")

    # ── E1: the REAL persisted report from the earlier live run ─────────────
    q19 = json.loads((ROOT / "analysis/reports/q19_expected_value.json").read_text(encoding="utf-8"))
    status, source = _extract_run_status(q19)
    print(f"{'E1':<11} | {q19['status']:<23} | {status:<23} | "
          f"{_extract_sample(q19):<4} | NEGATIVE_EDGE (n=1253)"
          f"   rec={q19['recommendation']}")

    # ── X4/Q16: the REAL persisted report from the Gap-2 live run ───────────
    q16 = json.loads((ROOT / "analysis/reports/q16_shadow_validation.json").read_text(encoding="utf-8"))
    status, source = _extract_run_status(q16)
    print(f"{'X4/Q16':<11} | {q16['status']:<23} | {status:<23} | "
          f"{_extract_sample(q16):<4} | BLOCKED (n=0)"
          f"   rec={q16['recommendation']}")

    print("-" * 86)

    # ── bulk consistency: every persisted canonical report ──────────────────
    print("\n[persisted canonical reports -> authoritative summary mapping]")
    reports_dir = ROOT / "analysis/reports"
    checked = 0
    for path in sorted(reports_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, dict) or "status" not in data or "question_id" not in data:
            continue  # legacy family (no top-level status) - different contract
        status, source = _extract_run_status(data)
        assert status == data["status"], f"{path.name}: {status} != {data['status']}"
        checked += 1
        print(f"  {path.name:<42} {status:<22} [source={source}] n="
              f"{_extract_sample(data)}")
    print(f"\n  {checked} persisted canonical reports: summary status == report status for ALL")

    print("\nPROOF COMPLETE (offline-deterministic, real runner code + real reports)")


if __name__ == "__main__":
    main()
