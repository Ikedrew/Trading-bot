"""
AWS Lambda Handler — V10 Research Engine.

Runs V10 research experiments on demand. Reads dataset from S3,
executes specified experiment with specified view, uploads report to S3.

Event format:
    {"research": "E1", "view": "FX_ONLY"}

Environment variables:
    S3_BUCKET    — Bucket name (default: v10-engine)
    DATASET_PATH — S3 key for research_ready_trades.jsonl
    REPORT_PATH  — S3 prefix for output reports

Local testing:
    python lambda_function.py --local
    python lambda_function.py --local --research E1 --view FX_ONLY
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Configuration from environment
S3_BUCKET = os.environ.get("S3_BUCKET", "v10-engine")
DATASET_PATH = os.environ.get("DATASET_PATH", "research_ready_trade_dataset/research_ready_trades.jsonl")
REPORT_PATH = os.environ.get("REPORT_PATH", "reports/research/")


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    AWS Lambda entry point for V10 research experiments.

    Args:
        event: {"research": "E1", "view": "FX_ONLY"}
        context: Lambda context object

    Returns:
        Experiment result dict + execution metadata
    """
    start_time = time.time()
    invocation_time = datetime.now(timezone.utc).isoformat()

    # ─── Validate input ───────────────────────────────────────
    research_id = event.get("research", "").upper()
    view = event.get("view", "FULL").upper()

    if not research_id:
        return {"status": "ERROR", "error": "Missing 'research' field in event"}

    logger.info(f"[RESEARCH_LAMBDA] research={research_id} view={view} invoked={invocation_time}")

    # ─── Load dataset from S3 ─────────────────────────────────
    try:
        import boto3
        s3 = boto3.client("s3")
        response = s3.get_object(Bucket=S3_BUCKET, Key=DATASET_PATH)
        body = response["Body"].read().decode("utf-8")
        trades = [json.loads(line) for line in body.splitlines() if line.strip()]
        logger.info(f"[RESEARCH_LAMBDA] Loaded {len(trades)} trades from s3://{S3_BUCKET}/{DATASET_PATH}")
    except Exception as exc:
        logger.error(f"[RESEARCH_LAMBDA] Dataset load failed: {exc}")
        return {"status": "ERROR", "error": f"Dataset load failed: {exc}"}

    # ─── Run experiment ───────────────────────────────────────
    try:
        from research_engine.v10.runner import run_experiment
        from research_engine.v10.dataset import DatasetView

        # Parse view
        try:
            dataset_view = DatasetView(view)
        except ValueError:
            return {"status": "ERROR", "error": f"Invalid view: {view}"}

        # Pass pre-loaded trades to avoid local file dependency
        result = run_experiment(research_id, view=view, trades=trades)

        if "error" in result:
            return {"status": "ERROR", "error": result["error"]}

        logger.info(
            f"[RESEARCH_LAMBDA] Experiment complete: {research_id} "
            f"conclusion={result.get('conclusion', 'N/A')} "
            f"trades={result.get('sample_size', 0)}"
        )
    except Exception as exc:
        logger.error(f"[RESEARCH_LAMBDA] Experiment failed: {exc}")
        return {"status": "ERROR", "error": f"Experiment failed: {type(exc).__name__}: {exc}"}

    # ─── Upload reports to S3 ─────────────────────────────────
    try:
        report_key = f"{REPORT_PATH}v10_{research_id.lower()}_{view.lower()}_report.json"
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=report_key,
            Body=json.dumps(result, indent=2, default=str).encode("utf-8"),
            ContentType="application/json",
        )
        logger.info(f"[RESEARCH_LAMBDA] Report uploaded: s3://{S3_BUCKET}/{report_key}")

        # Upload markdown if available
        if result.get("markdown"):
            md_key = report_key.replace(".json", ".md")
            s3.put_object(
                Bucket=S3_BUCKET,
                Key=md_key,
                Body=result["markdown"].encode("utf-8"),
                ContentType="text/markdown",
            )
    except Exception as exc:
        logger.warning(f"[RESEARCH_LAMBDA] Report upload failed: {exc}")

    # ─── Return result ────────────────────────────────────────
    duration_ms = int((time.time() - start_time) * 1000)
    return {
        "status": "SUCCESS",
        "research_id": research_id,
        "view": view,
        "invocation_time": invocation_time,
        "duration_ms": duration_ms,
        "sample_size": result.get("sample_size", 0),
        "conclusion": result.get("conclusion", "N/A"),
        "metrics": result.get("metrics", {}),
        "report_location": f"s3://{S3_BUCKET}/{REPORT_PATH}v10_{research_id.lower()}_{view.lower()}_report.json",
    }


# ═══════════════════════════════════════════════════════════════
# LOCAL TEST MODE
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="V10 Research Lambda — Local Test")
    parser.add_argument("--local", action="store_true", help="Run against local files")
    parser.add_argument("--research", default="E1", help="Research ID (default: E1)")
    parser.add_argument("--view", default="FULL", help="Dataset view (default: FULL)")
    args = parser.parse_args()

    if args.local or "--local" in sys.argv:
        print(f"[LOCAL MODE] Running {args.research} with view={args.view}")

        # Add parent paths for imports
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

        from research_engine.v10.runner import run_experiment

        # Load from local file
        from pathlib import Path
        local_file = Path(__file__).parent.parent.parent / "logs" / "research_ready_trade_dataset" / "research_ready_trades.jsonl"
        if not local_file.exists():
            local_file = Path("../../logs/research_ready_trade_dataset/research_ready_trades.jsonl")

        if local_file.exists():
            trades = [json.loads(l) for l in local_file.read_text(encoding="utf-8").splitlines() if l.strip()]
            print(f"Loaded {len(trades)} trades from {local_file}")
        else:
            print(f"ERROR: Dataset not found at {local_file}")
            sys.exit(1)

        result = run_experiment(args.research, view=args.view, trades=trades)

        print(f"\nResult:")
        print(f"  Research: {result.get('research_id', args.research)}")
        print(f"  View: {result.get('dataset_view', args.view)}")
        print(f"  Trades: {result.get('sample_size', 0)}")
        print(f"  Conclusion: {result.get('conclusion', 'N/A')}")
        metrics = result.get("metrics", {})
        if metrics:
            print(f"  Win Rate: {metrics.get('win_rate', 0):.1%}")
            print(f"  Expectancy: {metrics.get('expectancy_r', 0):.4f} R/trade")
            print(f"  Total PnL: ${metrics.get('total_pnl', 0):.2f}")
            print(f"  Profit Factor: {metrics.get('profit_factor', 0):.2f}")

        # Write local output
        out_dir = Path(__file__).parent / "output"
        out_dir.mkdir(exist_ok=True)
        filename = f"v10_{args.research.lower()}_{args.view.lower()}"
        (out_dir / f"{filename}.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        if result.get("markdown"):
            (out_dir / f"{filename}.md").write_text(result["markdown"], encoding="utf-8")
        print(f"\n  Reports: {out_dir}/{filename}.*")
    else:
        # S3 mode
        print("[S3 MODE] Calling lambda_handler...")
        event = {"research": args.research, "view": args.view}
        result = lambda_handler(event, None)
        print(json.dumps(result, indent=2))
