"""
AWS Lambda Entry Point — Anomaly Analysis Research Job.

Reads research-ready trades from S3, runs anomaly classification,
uploads JSON + Markdown reports back to S3.

Environment variables:
    S3_BUCKET      — S3 bucket name (default: v10-engine)
    DATASET_PATH   — Key path to research_ready_trades.jsonl
    REPORT_PATH    — S3 prefix for output reports

Local testing:
    python lambda_function.py
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

import boto3

from config import S3_BUCKET, DATASET_PATH, REPORT_PATH, EXTREME_R_HIGH, EXTREME_R_LOW, EXTREME_PNL_PERCENTILE
from research_anomaly import build_anomaly_report, format_markdown_report

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    AWS Lambda entry point.

    Args:
        event: Lambda trigger event (can be empty for scheduled runs)
        context: Lambda context object

    Returns:
        Execution summary dict.
    """
    start_time = time.time()
    invocation_time = datetime.now(timezone.utc).isoformat()
    logger.info(f"[ANOMALY_LAMBDA] Invoked at {invocation_time}")

    s3 = boto3.client("s3")

    # ─── 1. Load dataset from S3 ─────────────────────────────────
    logger.info(f"[ANOMALY_LAMBDA] Reading s3://{S3_BUCKET}/{DATASET_PATH}")
    try:
        response = s3.get_object(Bucket=S3_BUCKET, Key=DATASET_PATH)
        body = response["Body"].read().decode("utf-8")
        trades = [json.loads(line) for line in body.splitlines() if line.strip()]
    except Exception as exc:
        logger.error(f"[ANOMALY_LAMBDA] Failed to read dataset: {exc}")
        return {"status": "ERROR", "error": f"Dataset read failed: {exc}"}

    logger.info(f"[ANOMALY_LAMBDA] Loaded {len(trades)} trades from S3")

    # ─── 2. Run anomaly analysis ─────────────────────────────────
    analysis_config = {
        "extreme_r_high": EXTREME_R_HIGH,
        "extreme_r_low": EXTREME_R_LOW,
        "extreme_pnl_percentile": EXTREME_PNL_PERCENTILE,
    }

    report = build_anomaly_report(trades, analysis_config)

    flagged_count = report["dataset_counts"]["flagged"]
    total_count = report["dataset_counts"]["full"]
    logger.info(f"[ANOMALY_LAMBDA] Analysis complete: {total_count} trades, {flagged_count} anomalies detected")

    # ─── 3. Generate reports ──────────────────────────────────────
    json_report = json.dumps(report, indent=2, default=str)
    md_report = format_markdown_report(report)

    # ─── 4. Upload reports to S3 ──────────────────────────────────
    json_key = f"{REPORT_PATH}anomaly_analysis_report.json"
    md_key = f"{REPORT_PATH}anomaly_analysis_report.md"

    try:
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=json_key,
            Body=json_report.encode("utf-8"),
            ContentType="application/json",
        )
        logger.info(f"[ANOMALY_LAMBDA] Uploaded s3://{S3_BUCKET}/{json_key}")

        s3.put_object(
            Bucket=S3_BUCKET,
            Key=md_key,
            Body=md_report.encode("utf-8"),
            ContentType="text/markdown",
        )
        logger.info(f"[ANOMALY_LAMBDA] Uploaded s3://{S3_BUCKET}/{md_key}")
    except Exception as exc:
        logger.error(f"[ANOMALY_LAMBDA] Failed to upload reports: {exc}")
        return {"status": "ERROR", "error": f"Report upload failed: {exc}"}

    # ─── 5. Return summary ────────────────────────────────────────
    duration_ms = int((time.time() - start_time) * 1000)
    summary = {
        "status": "SUCCESS",
        "invocation_time": invocation_time,
        "duration_ms": duration_ms,
        "trades_analysed": total_count,
        "anomalies_detected": flagged_count,
        "reports_written": [
            f"s3://{S3_BUCKET}/{json_key}",
            f"s3://{S3_BUCKET}/{md_key}",
        ],
    }
    logger.info(f"[ANOMALY_LAMBDA] Complete in {duration_ms}ms: {json.dumps(summary)}")
    return summary


# ═══════════════════════════════════════════════════════════════
# LOCAL TEST MODE
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    """Run locally to simulate Lambda execution."""
    import sys

    # Check if we can use S3 or fall back to local files
    use_local = "--local" in sys.argv

    if use_local:
        print("[LOCAL MODE] Reading from local filesystem...")
        from pathlib import Path

        local_file = Path("../../logs/research_ready_trade_dataset/research_ready_trades.jsonl")
        if not local_file.exists():
            # Try relative to script location
            local_file = Path(__file__).parent.parent.parent / "logs" / "research_ready_trade_dataset" / "research_ready_trades.jsonl"

        if not local_file.exists():
            print(f"ERROR: Cannot find {local_file}")
            sys.exit(1)

        trades = [json.loads(l) for l in local_file.read_text(encoding="utf-8").splitlines() if l.strip()]
        print(f"Loaded {len(trades)} trades from {local_file}")

        config = {
            "extreme_r_high": EXTREME_R_HIGH,
            "extreme_r_low": EXTREME_R_LOW,
            "extreme_pnl_percentile": EXTREME_PNL_PERCENTILE,
        }
        report = build_anomaly_report(trades, config)

        # Write locally
        out_dir = Path(__file__).parent / "output"
        out_dir.mkdir(exist_ok=True)
        (out_dir / "anomaly_analysis_report.json").write_text(json.dumps(report, indent=2, default=str))
        (out_dir / "anomaly_analysis_report.md").write_text(format_markdown_report(report))

        print(f"\nResults:")
        print(f"  Trades: {report['dataset_counts']['full']}")
        print(f"  Flagged: {report['dataset_counts']['flagged']}")
        print(f"  Reports: {out_dir}/")
    else:
        print("[S3 MODE] Running Lambda handler with S3 access...")
        result = lambda_handler({}, None)
        print(json.dumps(result, indent=2))
