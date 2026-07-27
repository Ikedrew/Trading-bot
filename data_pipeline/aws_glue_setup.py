"""
AWS Glue + Athena Infrastructure Setup — Query Layer for Curated Trading Events.

This module provisions the AWS data catalog and query infrastructure so that
curated trading events in S3 become a clean SQL table in Athena.

Pipeline:
    S3 (curated JSONL) → Glue Crawler → Glue Data Catalog → Athena SQL

Components:
    1. Glue Database:   trading_bot
    2. Glue Crawler:    trading_bot_curated_crawler
    3. Glue Table:      curated_events (auto-created by crawler, or manual DDL)
    4. Athena Workgroup: primary (default), results to s3://<bucket>/athena-results/

Usage:
    from data_pipeline.aws_glue_setup import setup_all, upload_curated_to_s3

    # Upload local curated data to S3
    upload_curated_to_s3()

    # Create Glue database + crawler + run
    setup_all()

    # Or step-by-step:
    create_glue_database()
    create_glue_crawler()
    run_crawler()

Requirements:
    - boto3 installed
    - AWS credentials configured (env vars or ~/.aws/credentials)
    - IAM role with Glue + S3 permissions (see GLUE_ROLE_ARN)
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

S3_BUCKET = "trading-bot-data-mk1"
S3_CURATED_PREFIX = "events/curated/"
S3_ATHENA_RESULTS = f"s3://{S3_BUCKET}/athena-results/"

GLUE_DATABASE = "trading_bot"
GLUE_TABLE = "curated_events"
GLUE_CRAWLER_NAME = "trading_bot_curated_crawler"

# IAM role for Glue crawler — must have S3 read + Glue catalog write permissions
# Set via environment variable or replace with your actual role ARN
GLUE_ROLE_ARN = os.getenv(
    "GLUE_CRAWLER_ROLE_ARN",
    "arn:aws:iam::ACCOUNT_ID:role/GlueCrawlerRole",
)

AWS_REGION = os.getenv("AWS_REGION", "eu-west-2")


# ═══════════════════════════════════════════════════════════════════════════════
# S3 UPLOAD (local curated → S3)
# ═══════════════════════════════════════════════════════════════════════════════

def upload_curated_to_s3(
    local_dir: str = "events/curated",
    bucket: str = S3_BUCKET,
    prefix: str = S3_CURATED_PREFIX,
) -> dict[str, Any]:
    """
    Upload local curated JSONL files to S3 curated prefix.

    Args:
        local_dir: Path to local curated events directory
        bucket: S3 bucket name
        prefix: S3 key prefix for curated data

    Returns:
        Stats: {"uploaded": int, "failed": int, "files": list[str]}
    """
    import boto3

    s3 = boto3.client("s3", region_name=AWS_REGION)
    local_path = Path(local_dir)

    uploaded = 0
    failed = 0
    files: list[str] = []

    for jsonl_file in sorted(local_path.glob("*.jsonl")):
        s3_key = f"{prefix}{jsonl_file.name}"
        try:
            s3.upload_file(
                str(jsonl_file),
                bucket,
                s3_key,
                ExtraArgs={"ContentType": "application/x-ndjson"},
            )
            uploaded += 1
            files.append(s3_key)
            logger.info("[S3_UPLOAD] %s → s3://%s/%s", jsonl_file.name, bucket, s3_key)
        except Exception as exc:
            failed += 1
            logger.error("[S3_UPLOAD] FAILED %s: %s", jsonl_file.name, exc)

    return {"uploaded": uploaded, "failed": failed, "files": files}


# ═══════════════════════════════════════════════════════════════════════════════
# GLUE DATABASE
# ═══════════════════════════════════════════════════════════════════════════════

def create_glue_database(
    database_name: str = GLUE_DATABASE,
) -> bool:
    """
    Create the Glue database if it doesn't exist.

    Returns True if created or already exists, False on error.
    """
    import boto3

    glue = boto3.client("glue", region_name=AWS_REGION)

    try:
        glue.get_database(Name=database_name)
        logger.info("[GLUE] Database '%s' already exists", database_name)
        return True
    except glue.exceptions.EntityNotFoundException:
        pass

    try:
        glue.create_database(
            DatabaseInput={
                "Name": database_name,
                "Description": "Trading bot observability — curated event analytics",
            }
        )
        logger.info("[GLUE] Created database '%s'", database_name)
        return True
    except Exception as exc:
        logger.error("[GLUE] Failed to create database: %s", exc)
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# GLUE CRAWLER
# ═══════════════════════════════════════════════════════════════════════════════

def create_glue_crawler(
    crawler_name: str = GLUE_CRAWLER_NAME,
    database_name: str = GLUE_DATABASE,
    s3_target: str | None = None,
    role_arn: str = GLUE_ROLE_ARN,
) -> bool:
    """
    Create or update the Glue crawler for curated events.

    The crawler auto-detects schema from JSONL files and creates/updates
    the table in the Glue Data Catalog.

    Returns True on success, False on error.
    """
    import boto3

    glue = boto3.client("glue", region_name=AWS_REGION)
    target_path = s3_target or f"s3://{S3_BUCKET}/{S3_CURATED_PREFIX}"

    crawler_config = {
        "Name": crawler_name,
        "Role": role_arn,
        "DatabaseName": database_name,
        "Description": "Crawls curated trading bot events for Athena analytics",
        "Targets": {
            "S3Targets": [
                {
                    "Path": target_path,
                    "Exclusions": [],
                }
            ]
        },
        "SchemaChangePolicy": {
            "UpdateBehavior": "UPDATE_IN_DATABASE",
            "DeleteBehavior": "LOG",
        },
        "RecrawlPolicy": {
            "RecrawlBehavior": "CRAWL_EVERYTHING",
        },
        "Configuration": json.dumps({
            "Version": 1.0,
            "Grouping": {
                "TableGroupingPolicy": "CombineCompatibleSchemas",
            },
        }),
    }

    try:
        glue.get_crawler(Name=crawler_name)
        # Crawler exists — update it
        glue.update_crawler(**crawler_config)
        logger.info("[GLUE] Updated crawler '%s'", crawler_name)
        return True
    except glue.exceptions.EntityNotFoundException:
        pass

    try:
        glue.create_crawler(**crawler_config)
        logger.info("[GLUE] Created crawler '%s' → %s", crawler_name, target_path)
        return True
    except Exception as exc:
        logger.error("[GLUE] Failed to create crawler: %s", exc)
        return False


def run_crawler(
    crawler_name: str = GLUE_CRAWLER_NAME,
    wait: bool = True,
    timeout_seconds: int = 300,
) -> bool:
    """
    Start the Glue crawler and optionally wait for completion.

    Args:
        crawler_name: Name of the crawler to run
        wait: If True, polls until crawler finishes
        timeout_seconds: Max wait time before giving up

    Returns True if crawler completed successfully.
    """
    import boto3

    glue = boto3.client("glue", region_name=AWS_REGION)

    try:
        glue.start_crawler(Name=crawler_name)
        logger.info("[GLUE] Started crawler '%s'", crawler_name)
    except glue.exceptions.CrawlerRunningException:
        logger.info("[GLUE] Crawler '%s' already running", crawler_name)
    except Exception as exc:
        logger.error("[GLUE] Failed to start crawler: %s", exc)
        return False

    if not wait:
        return True

    # Poll for completion
    start = time.time()
    while time.time() - start < timeout_seconds:
        try:
            response = glue.get_crawler(Name=crawler_name)
            state = response["Crawler"]["State"]
            if state == "READY":
                last_crawl = response["Crawler"].get("LastCrawl", {})
                status = last_crawl.get("Status", "UNKNOWN")
                logger.info(
                    "[GLUE] Crawler completed — status=%s tables_created=%s",
                    status,
                    last_crawl.get("TablesCreated", 0),
                )
                return status == "SUCCEEDED"
            logger.debug("[GLUE] Crawler state: %s", state)
        except Exception:
            pass
        time.sleep(10)

    logger.warning("[GLUE] Crawler timed out after %ds", timeout_seconds)
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# MANUAL TABLE DDL (alternative to crawler — explicit schema control)
# ═══════════════════════════════════════════════════════════════════════════════

def create_table_manual(
    database_name: str = GLUE_DATABASE,
    table_name: str = GLUE_TABLE,
) -> bool:
    """
    Create the curated_events table with explicit schema in Glue catalog.

    Use this instead of the crawler when you want guaranteed column types
    without auto-detection. This ensures schema stability.

    Returns True on success.
    """
    import boto3

    glue = boto3.client("glue", region_name=AWS_REGION)

    columns = [
        {"Name": "timestamp", "Type": "string", "Comment": "ISO 8601 UTC event time"},
        {"Name": "symbol", "Type": "string", "Comment": "Trading symbol"},
        {"Name": "event_type", "Type": "string", "Comment": "Event type classification"},
        {"Name": "pattern", "Type": "string", "Comment": "Trading pattern name"},
        {"Name": "htf_bias", "Type": "string", "Comment": "Higher timeframe bias: bullish/bearish/neutral"},
        {"Name": "liquidity_swept", "Type": "boolean", "Comment": "Whether liquidity was swept"},
        {"Name": "bos_confirmed", "Type": "boolean", "Comment": "Break of structure confirmed"},
        {"Name": "atr_regime", "Type": "string", "Comment": "ATR regime: expansion/contraction/neutral"},
        {"Name": "pnl", "Type": "double", "Comment": "Profit/loss value"},
        {"Name": "trade_id", "Type": "string", "Comment": "Trade identifier"},
    ]

    table_input = {
        "Name": table_name,
        "Description": "Curated trading bot events — flat schema, Athena-ready",
        "StorageDescriptor": {
            "Columns": columns,
            "Location": f"s3://{S3_BUCKET}/{S3_CURATED_PREFIX}",
            "InputFormat": "org.apache.hadoop.mapred.TextInputFormat",
            "OutputFormat": "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat",
            "SerdeInfo": {
                "SerializationLibrary": "org.openx.data.jsonserde.JsonSerDe",
                "Parameters": {
                    "paths": ",".join(c["Name"] for c in columns),
                },
            },
            "Compressed": False,
        },
        "TableType": "EXTERNAL_TABLE",
        "Parameters": {
            "classification": "json",
            "typeOfData": "file",
        },
    }

    try:
        # Delete if exists (replace)
        try:
            glue.delete_table(DatabaseName=database_name, Name=table_name)
        except glue.exceptions.EntityNotFoundException:
            pass

        glue.create_table(
            DatabaseName=database_name,
            TableInput=table_input,
        )
        logger.info("[GLUE] Created table '%s.%s' with explicit schema", database_name, table_name)
        return True
    except Exception as exc:
        logger.error("[GLUE] Failed to create table: %s", exc)
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# ATHENA CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

def configure_athena_workgroup(
    workgroup: str = "primary",
    output_location: str = S3_ATHENA_RESULTS,
) -> bool:
    """
    Configure Athena workgroup with query result location.

    Returns True on success.
    """
    import boto3

    athena = boto3.client("athena", region_name=AWS_REGION)

    try:
        athena.update_work_group(
            WorkGroup=workgroup,
            ConfigurationUpdates={
                "ResultConfigurationUpdates": {
                    "OutputLocation": output_location,
                },
                "EnforceWorkGroupConfiguration": False,
            },
        )
        logger.info("[ATHENA] Configured workgroup '%s' → %s", workgroup, output_location)
        return True
    except Exception as exc:
        logger.error("[ATHENA] Failed to configure workgroup: %s", exc)
        return False


def run_athena_query(
    query: str,
    database: str = GLUE_DATABASE,
    output_location: str = S3_ATHENA_RESULTS,
    wait: bool = True,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    """
    Execute an Athena query and optionally wait for results.

    Args:
        query: SQL query string
        database: Glue database to query against
        output_location: S3 location for query results
        wait: If True, polls until query completes
        timeout_seconds: Max wait time

    Returns:
        {"status": str, "execution_id": str, "results": list | None}
    """
    import boto3

    athena = boto3.client("athena", region_name=AWS_REGION)

    try:
        response = athena.start_query_execution(
            QueryString=query,
            QueryExecutionContext={"Database": database},
            ResultConfiguration={"OutputLocation": output_location},
        )
        execution_id = response["QueryExecutionId"]
        logger.info("[ATHENA] Started query %s", execution_id)
    except Exception as exc:
        logger.error("[ATHENA] Failed to start query: %s", exc)
        return {"status": "FAILED", "execution_id": "", "results": None, "error": str(exc)}

    if not wait:
        return {"status": "RUNNING", "execution_id": execution_id, "results": None}

    # Poll for completion
    start = time.time()
    while time.time() - start < timeout_seconds:
        try:
            status_response = athena.get_query_execution(QueryExecutionId=execution_id)
            state = status_response["QueryExecution"]["Status"]["State"]

            if state == "SUCCEEDED":
                # Fetch results
                results = athena.get_query_results(QueryExecutionId=execution_id)
                rows = _parse_athena_results(results)
                return {"status": "SUCCEEDED", "execution_id": execution_id, "results": rows}
            elif state in ("FAILED", "CANCELLED"):
                reason = status_response["QueryExecution"]["Status"].get("StateChangeReason", "")
                return {"status": state, "execution_id": execution_id, "results": None, "error": reason}
        except Exception:
            pass
        time.sleep(2)

    return {"status": "TIMEOUT", "execution_id": execution_id, "results": None}


def _parse_athena_results(response: dict[str, Any]) -> list[dict[str, str]]:
    """Parse Athena query results into list of dicts."""
    rows = response.get("ResultSet", {}).get("Rows", [])
    if len(rows) < 2:
        return []

    # First row is headers
    headers = [col.get("VarCharValue", "") for col in rows[0].get("Data", [])]

    results = []
    for row in rows[1:]:
        values = [col.get("VarCharValue", "") for col in row.get("Data", [])]
        results.append(dict(zip(headers, values)))

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# VALIDATION QUERIES
# ═══════════════════════════════════════════════════════════════════════════════

VALIDATION_QUERIES = {
    "pattern_performance": """
        SELECT pattern, AVG(pnl) as expectancy
        FROM curated_events
        GROUP BY pattern
        ORDER BY expectancy DESC
    """,
    "market_context": """
        SELECT htf_bias, AVG(pnl) as avg_pnl
        FROM curated_events
        GROUP BY htf_bias
    """,
    "execution_quality": """
        SELECT liquidity_swept, bos_confirmed, AVG(pnl) as avg_pnl
        FROM curated_events
        GROUP BY liquidity_swept, bos_confirmed
    """,
    "regime_analysis": """
        SELECT atr_regime, COUNT(*) as cnt, AVG(pnl) as avg_pnl
        FROM curated_events
        GROUP BY atr_regime
    """,
    "symbol_breakdown": """
        SELECT symbol, pattern, COUNT(*) as trades, AVG(pnl) as avg_pnl
        FROM curated_events
        WHERE pnl != 0
        GROUP BY symbol, pattern
        ORDER BY trades DESC
    """,
}


def validate_setup() -> dict[str, Any]:
    """
    Run all validation queries to confirm the query layer is working.

    Returns results for each validation query.
    """
    results = {}
    for name, query in VALIDATION_QUERIES.items():
        logger.info("[VALIDATE] Running: %s", name)
        result = run_athena_query(query)
        results[name] = result
        if result["status"] != "SUCCEEDED":
            logger.warning("[VALIDATE] %s FAILED: %s", name, result.get("error", "unknown"))
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# FULL SETUP ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════════

def setup_all(
    *,
    upload_data: bool = True,
    use_crawler: bool = False,
    use_manual_table: bool = True,
    run_validation: bool = False,
) -> dict[str, Any]:
    """
    Complete AWS query layer setup.

    Steps:
        1. Upload curated data to S3 (optional)
        2. Create Glue database
        3. Create table (manual DDL or crawler)
        4. Configure Athena workgroup
        5. Run validation queries (optional)

    Args:
        upload_data: Whether to upload local curated JSONL to S3
        use_crawler: Use Glue crawler for table creation (auto-schema)
        use_manual_table: Use explicit DDL for table creation (guaranteed types)
        run_validation: Run validation queries after setup

    Returns:
        Status dict with results of each step.
    """
    status: dict[str, Any] = {}

    # Step 1: Upload curated data
    if upload_data:
        status["upload"] = upload_curated_to_s3()

    # Step 2: Create database
    status["database"] = create_glue_database()

    # Step 3: Create table
    if use_manual_table:
        status["table"] = create_table_manual()
    if use_crawler:
        status["crawler_create"] = create_glue_crawler()
        status["crawler_run"] = run_crawler()

    # Step 4: Configure Athena
    status["athena"] = configure_athena_workgroup()

    # Step 5: Validation
    if run_validation:
        status["validation"] = validate_setup()

    return status


# ═══════════════════════════════════════════════════════════════════════════════
# ATHENA DDL (for manual execution in Athena console)
# ═══════════════════════════════════════════════════════════════════════════════

ATHENA_CREATE_TABLE_DDL = f"""
CREATE EXTERNAL TABLE IF NOT EXISTS {GLUE_DATABASE}.{GLUE_TABLE} (
    `timestamp` string,
    `symbol` string,
    `event_type` string,
    `pattern` string,
    `htf_bias` string,
    `liquidity_swept` boolean,
    `bos_confirmed` boolean,
    `atr_regime` string,
    `pnl` double,
    `trade_id` string
)
ROW FORMAT SERDE 'org.openx.data.jsonserde.JsonSerDe'
WITH SERDEPROPERTIES (
    'serialization.format' = '1'
)
LOCATION 's3://{S3_BUCKET}/{S3_CURATED_PREFIX}'
TBLPROPERTIES ('has_encrypted_data'='false');
"""

ATHENA_DROP_TABLE_DDL = f"""
DROP TABLE IF EXISTS {GLUE_DATABASE}.{GLUE_TABLE};
"""


# ═══════════════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    action = sys.argv[1] if len(sys.argv) > 1 else "setup"

    if action == "setup":
        print("[AWS GLUE SETUP] Starting full setup...")
        print(f"  Bucket:   {S3_BUCKET}")
        print(f"  Prefix:   {S3_CURATED_PREFIX}")
        print(f"  Database: {GLUE_DATABASE}")
        print(f"  Table:    {GLUE_TABLE}")
        print(f"  Region:   {AWS_REGION}")
        print()
        result = setup_all(upload_data=True, use_manual_table=True, run_validation=False)
        print()
        print("Setup complete:")
        for step, status in result.items():
            print(f"  {step}: {status}")

    elif action == "ddl":
        print("═══ CREATE TABLE DDL (paste into Athena console) ═══")
        print(ATHENA_CREATE_TABLE_DDL)

    elif action == "validate":
        print("[VALIDATION] Running Athena queries...")
        results = validate_setup()
        for name, result in results.items():
            print(f"\n{'─' * 60}")
            print(f"  {name}: {result['status']}")
            if result.get("results"):
                for row in result["results"][:10]:
                    print(f"    {row}")

    elif action == "upload":
        print("[UPLOAD] Uploading curated data to S3...")
        result = upload_curated_to_s3()
        print(f"  Uploaded: {result['uploaded']}, Failed: {result['failed']}")

    else:
        print(f"Unknown action: {action}")
        print("Usage: python aws_glue_setup.py [setup|ddl|validate|upload]")
