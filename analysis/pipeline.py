"""
Pipeline Execution Engine — Single entrypoint for full trading intelligence pipeline.

Executes the complete system in strict order and returns ONE final trading decision.
This is the runtime execution engine, not an analytics module.

Order of execution:
    1. Update rule state (compression)
    2. Update confidence score
    3. Validate system (walk-forward, shadow, stress)
    4. Update live drift
    5. Experiment override check
    6. Final orchestration → TRADE / SHADOW_MODE / NO_TRADE

Usage:
    from analysis.pipeline import run_pipeline

    result = run_pipeline()
    print(result["decision"])
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

CURATED_DIR = "events/curated"
RULES_PATH = "analysis/reports/rules_latest.json"
COMPRESSION_PATH = "analysis/reports/rule_compression.json"
STATE_PATH = "analysis/reports/system_state.json"


def _is_stale(path: str, max_age_minutes: int = 60) -> bool:
    """Check if a report file is stale (older than max_age_minutes)."""
    p = Path(path)
    if not p.exists():
        return True
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        ts = (
            data.get("metadata", {}).get("generated_at", "") or
            data.get("last_updated", "") or ""
        )
        if not ts:
            return True
        generated = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - generated).total_seconds() / 60
        return age > max_age_minutes
    except Exception:
        return True


# ═══════════════════════════════════════════════════════════════════════════════
# PIPELINE STAGES
# ═══════════════════════════════════════════════════════════════════════════════

def _step_compression(status: dict[str, str]) -> None:
    """Step 1: Update rule state via compression."""
    try:
        from analysis.rule_compression import compress_rules
        compress_rules(rules_path=RULES_PATH)
        status["compression"] = "OK"
        logger.info("[PIPELINE] Step 1: Compression ✓")
    except Exception as e:
        status["compression"] = f"ERROR: {e}"
        logger.warning("[PIPELINE] Step 1: Compression failed: %s", e)


def _step_confidence(status: dict[str, str]) -> None:
    """Step 2: Update confidence score."""
    try:
        from analysis.confidence_score import compute_confidence_score, export_results
        result = compute_confidence_score()
        export_results(result)
        status["confidence"] = "OK"
        logger.info("[PIPELINE] Step 2: Confidence ✓ (score=%d)", result.get("overall_confidence", {}).get("score", 0))
    except Exception as e:
        status["confidence"] = f"ERROR: {e}"
        logger.warning("[PIPELINE] Step 2: Confidence failed: %s", e)


def _step_validation(status: dict[str, str]) -> None:
    """Step 3: Ensure walk-forward, shadow, and stress test are current."""
    try:
        # Walk-forward
        if _is_stale("analysis/reports/walk_forward.json"):
            from analysis.walk_forward import run_walk_forward_validation, export_results as wf_export
            wf = run_walk_forward_validation(curated_dir=CURATED_DIR)
            wf_export(wf)

        # Shadow execution
        if _is_stale("analysis/reports/shadow_execution.json"):
            from analysis.shadow_execution import run_shadow_execution, export_results as sh_export
            sh = run_shadow_execution(curated_dir=CURATED_DIR)
            sh_export(sh)

        # Regime stress test
        if _is_stale("analysis/reports/regime_stress_test.json"):
            from analysis.regime_stress_test import run_stress_test, export_results as st_export
            st = run_stress_test(curated_dir=CURATED_DIR, rules_path=COMPRESSION_PATH)
            st_export(st)

        status["validation"] = "OK"
        logger.info("[PIPELINE] Step 3: Validation ✓")
    except Exception as e:
        status["validation"] = f"ERROR: {e}"
        logger.warning("[PIPELINE] Step 3: Validation failed: %s", e)


def _step_drift(status: dict[str, str]) -> str | None:
    """
    Step 4: Update live drift monitor.

    Returns early-exit decision if drift is critical, else None.
    """
    try:
        from analysis.live_drift_monitor import run_drift_check, export_results as drift_export
        drift = run_drift_check(curated_dir=CURATED_DIR)
        drift_export(drift)

        risk = drift.get("risk_state", "STABLE")
        status["drift"] = "OK"
        logger.info("[PIPELINE] Step 4: Drift ✓ (state=%s)", risk)

        if risk == "BROKEN_REGIME":
            return "NO_TRADE"
        elif risk == "DEGRADED":
            return "SHADOW_MODE"
        return None
    except Exception as e:
        status["drift"] = f"ERROR: {e}"
        logger.warning("[PIPELINE] Step 4: Drift failed: %s", e)
        return None


def _step_experiment(status: dict[str, str]) -> None:
    """Step 5: Experiment override check."""
    try:
        exp_path = "analysis/reports/experiment_result.json"
        p = Path(exp_path)
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            decision = data.get("decision", "")
            if decision == "REJECT":
                logger.info("[PIPELINE] Step 5: Experiment REJECTED — no change applied")
            elif decision == "ACCEPT":
                logger.info("[PIPELINE] Step 5: Experiment ACCEPTED — improvement noted")
            else:
                logger.info("[PIPELINE] Step 5: Experiment %s", decision)
        status["experiment"] = "OK"
    except Exception as e:
        status["experiment"] = f"ERROR: {e}"
        logger.warning("[PIPELINE] Step 5: Experiment check failed: %s", e)


def _step_orchestrate() -> dict[str, Any]:
    """Step 6: Final orchestration using system_state.json only."""
    from analysis.orchestrator import make_decision
    return make_decision()


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def run_pipeline() -> dict[str, Any]:
    """
    Execute the full trading intelligence pipeline end-to-end.

    Returns ONE final trading decision with pipeline status.
    """
    from analysis.system_state import refresh_state

    pipeline_status: dict[str, str] = {
        "compression": "PENDING",
        "confidence": "PENDING",
        "validation": "PENDING",
        "drift": "PENDING",
        "experiment": "PENDING",
    }

    # ─── Step 1: Update rule state ────────────────────────────────────
    _step_compression(pipeline_status)
    refresh_state()

    # ─── Step 2: Update confidence ───────────────────────────────────
    _step_confidence(pipeline_status)
    refresh_state()

    # ─── Step 3: Validate system ─────────────────────────────────────
    _step_validation(pipeline_status)
    refresh_state()

    # ─── Step 4: Update live drift ───────────────────────────────────
    drift_override = _step_drift(pipeline_status)
    refresh_state()

    # Early exit on critical drift
    if drift_override:
        state = refresh_state()
        return {
            "decision": drift_override,
            "confidence": 0 if drift_override == "NO_TRADE" else 30,
            "risk_state": "BROKEN" if drift_override == "NO_TRADE" else "DEGRADED",
            "active_ruleset": f"compressed ({state.get('active_state', {}).get('rule_count', 0)} rules)",
            "reasoning": [f"Drift monitor forced {drift_override} — pipeline halted"],
            "pipeline_status": pipeline_status,
            "metadata": {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
        }

    # ─── Step 5: Experiment override check ───────────────────────────
    _step_experiment(pipeline_status)

    # ─── Step 6: Final orchestration ─────────────────────────────────
    orchestrator_result = _step_orchestrate()
    refresh_state()

    # Build final output
    state = refresh_state()
    active = state.get("active_state", {})

    return {
        "decision": orchestrator_result.get("decision", "NO_TRADE"),
        "confidence": orchestrator_result.get("confidence", 0),
        "risk_state": orchestrator_result.get("risk_state", "UNKNOWN"),
        "active_ruleset": f"compressed ({active.get('rule_count', 0)} rules)",
        "reasoning": orchestrator_result.get("reasoning", []),
        "pipeline_status": pipeline_status,
        "metadata": {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
    }


# ═══════════════════════════════════════════════════════════════════════════════
# DISPLAY & CLI
# ═══════════════════════════════════════════════════════════════════════════════

def print_result(result: dict[str, Any]) -> None:
    """Print final pipeline decision."""
    decision = result.get("decision", "?")
    confidence = result.get("confidence", 0)
    risk = result.get("risk_state", "?")
    status = result.get("pipeline_status", {})
    reasoning = result.get("reasoning", [])

    icons = {"TRADE": "✓", "NO_TRADE": "✗", "SHADOW_MODE": "◐"}
    icon = icons.get(decision, "?")

    print()
    print("═══════════════════════════════════════════════════════════════")
    print(f"  {icon} FINAL DECISION: {decision}")
    print("═══════════════════════════════════════════════════════════════")
    print(f"  Confidence: {confidence}/100")
    print(f"  Risk:       {risk}")
    print(f"  Ruleset:    {result.get('active_ruleset', '?')}")
    print()

    # Pipeline status
    print("─── PIPELINE STATUS ────────────────────────────────────────────")
    for step, st in status.items():
        icon_s = "✓" if st == "OK" else "✗"
        print(f"  {icon_s} {step}: {st}")
    print()

    # Reasoning
    if reasoning:
        print("─── REASONING ──────────────────────────────────────────────────")
        for r in reasoning[:5]:
            print(f"  • {r}")
        print()

    print("═══════════════════════════════════════════════════════════════")


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    result = run_pipeline()
    print_result(result)

    # Export
    output = sys.argv[1] if len(sys.argv) > 1 else "analysis/reports/pipeline_decision.json"
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, default=str)
    print(f"  Decision saved to: {output}")
