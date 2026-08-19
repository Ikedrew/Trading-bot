"""
Research Engine — Scheduled Execution Entry Point.

Designed to be invoked by Windows Task Scheduler, cron, or any external scheduler.

Performs ONE complete research cycle:
    1. Detects anomalies in accumulating shadow/execution data
    2. Investigates eligible findings (if mode allows)
    3. Activates eligible optimisation candidates for shadow testing
    4. Evaluates candidates with sufficient paired evidence
    5. Produces research reports and audit trail

Safety:
    - Uses existing ResearchCycleRunner with built-in locking and cooldown
    - Never modifies production trading configuration
    - Never calls MT5Execution, RiskManager, or broker
    - Failure exits with non-zero status without affecting trading
    - Concurrent invocations are safely rejected via file lock
    - Cooldown prevents excessive research cycles

Exit codes:
    0 = cycle completed successfully
    1 = cycle failed (error)
    2 = cycle skipped (cooldown not elapsed)
    3 = cycle locked (another instance running)

Usage:
    python scripts/run_research_cycle.py
    python scripts/run_research_cycle.py --mode=DETECT_AND_INVESTIGATE
    python scripts/run_research_cycle.py --mode=DETECT_ONLY

Task Scheduler command:
    "C:\\Program Files\\Python311\\python.exe" "C:\\Users\\Administrator\\Desktop\\Trading bot build\\scripts\\run_research_cycle.py"

Working directory:
    C:\\Users\\Administrator\\Desktop\\Trading bot build
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is on path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(str(_PROJECT_ROOT))
sys.path.insert(0, str(_PROJECT_ROOT))

from research_engine.lifecycle.research_cycle_runner import (
    ResearchCycleRunner,
    ResearchCycleConfig,
)
from research_engine.lifecycle.finding_trigger import (
    EligibilityConfig,
    ExecutionMode,
)

# ─── LOGGING ──────────────────────────────────────────────────────────────────

_LOG_DIR = Path("logs/research_lifecycle")
_LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            str(_LOG_DIR / "scheduled_runs.log"),
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger("research_scheduler")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main() -> int:
    """
    Execute one research cycle. Returns process exit code.
    """
    parser = argparse.ArgumentParser(description="Research Engine — Scheduled Cycle")
    parser.add_argument(
        "--mode",
        choices=["DETECT_ONLY", "DETECT_AND_INVESTIGATE"],
        default="DETECT_AND_INVESTIGATE",
        help="Research execution mode (default: DETECT_AND_INVESTIGATE)",
    )
    parser.add_argument(
        "--cooldown",
        type=float,
        default=3600.0,
        help="Minimum seconds between cycles (default: 3600 = 1 hour)",
    )
    args = parser.parse_args()

    mode = ExecutionMode(args.mode)
    start_time = time.time()

    logger.info(
        "RESEARCH CYCLE STARTING | mode=%s | cooldown=%ds | pid=%d",
        mode.value, int(args.cooldown), os.getpid(),
    )

    # ─── Configure ────────────────────────────────────────────────────
    config = ResearchCycleConfig(
        mode=mode,
        min_cycle_interval_seconds=args.cooldown,
        max_investigations_per_cycle=2,
        max_active_investigations=5,
        eligibility=EligibilityConfig(
            min_sample_size=30,
            min_effect_size=0.15,
            max_win_rate_for_poor=0.15,
            min_win_rate_for_strong=0.65,
            cooldown_hours=72.0,
            max_active_triggers=10,
        ),
    )

    # ─── Execute ──────────────────────────────────────────────────────
    runner = ResearchCycleRunner(config)
    result = runner.run_cycle()

    duration = time.time() - start_time

    # ─── Interpret result ─────────────────────────────────────────────
    if result.status == "complete":
        logger.info(
            "RESEARCH CYCLE COMPLETE | id=%s | duration=%.1fs | "
            "triggers=%d | eligible=%d | investigated=%d | errors=%d",
            result.cycle_id, duration,
            result.triggers_detected, result.triggers_eligible,
            result.investigations_started, len(result.errors),
        )
        _persist_run_summary(result, duration)
        _refresh_cockpit()
        return 0

    elif result.status == "skipped":
        logger.info(
            "RESEARCH CYCLE SKIPPED (cooldown) | reason=%s",
            result.errors[0] if result.errors else "cooldown",
        )
        return 2

    elif result.status == "locked":
        logger.info(
            "RESEARCH CYCLE LOCKED (another instance running)",
        )
        return 3

    else:  # "failed" or unexpected
        logger.error(
            "RESEARCH CYCLE FAILED | id=%s | duration=%.1fs | errors=%s",
            result.cycle_id, duration, result.errors,
        )
        return 1


def _persist_run_summary(result, duration: float) -> None:
    """Append a one-line summary to the scheduled runs log."""
    try:
        summary_path = _LOG_DIR / "scheduled_run_history.jsonl"
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cycle_id": result.cycle_id,
            "status": result.status,
            "duration_seconds": round(duration, 2),
            "triggers_detected": result.triggers_detected,
            "triggers_eligible": result.triggers_eligible,
            "investigations_started": result.investigations_started,
            "investigations_completed": result.investigations_completed,
            "dataset_fingerprint": result.dataset_fingerprint,
        }
        fd = os.open(str(summary_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, (json.dumps(entry, separators=(",", ":")) + "\n").encode("utf-8"))
        finally:
            os.close(fd)
    except Exception:
        pass  # Summary persistence must never prevent clean exit


def _refresh_cockpit() -> None:
    """Refresh the cockpit HTML after a successful research cycle. Never raises."""
    try:
        from research_engine.v10.cockpit.refresh import refresh_cockpit
        result = refresh_cockpit(skip_s3=True)
        if result.success:
            logger.info("COCKPIT REFRESHED | path=%s", result.local_path)
        else:
            logger.debug("COCKPIT REFRESH SKIPPED | reason=%s", result.error)
    except Exception as e:
        logger.debug("COCKPIT REFRESH FAILED | error=%s", str(e)[:100])


if __name__ == "__main__":
    sys.exit(main())
