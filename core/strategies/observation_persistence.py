"""
Strategy Observation Persistence — Local JSONL + S3 mirror.

Persists strategy observations as a permanent research dataset alongside
existing events, trade_truth, and assessments.

Storage:
    Local:  logs/strategy_observations/{SYMBOL}/{YYYY-MM-DD}.jsonl
    S3:     s3://trading-bot-data-mk1/strategy_observations/symbol={SYMBOL}/date={YYYY-MM-DD}/part-000.jsonl

This module is PURELY OBSERVATIONAL. It does NOT:
    - Affect trading decisions
    - Block or gate execution
    - Modify any pipeline behaviour
    - Import from core/pipeline/, execution/, or risk/

Design: fire-and-forget secondary persistence. Local JSONL is truth.
Follows the exact pattern of core/assessment/persistence.py and
core/trade_truth_graph.py.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOCAL_DIR = "logs/strategy_observations"
from core.config import NEW_RUNTIME_S3_BUCKET
from core.production_data_contract import s3_base_prefix

_S3_BUCKET = NEW_RUNTIME_S3_BUCKET
_S3_PREFIX = s3_base_prefix("strategy_observations")
_SCHEMA_VERSION = "strategy_observation_v1"


# ═══════════════════════════════════════════════════════════════════════════════
# SCHEMA
# ═══════════════════════════════════════════════════════════════════════════════

def build_observation_record(
    *,
    observation_id: str,
    timestamp_utc: float,
    symbol: str,
    cycle_id: int = 0,
    market_phase: str = "",
    h4_regime: str = "",
    h1_bias: str = "",
    direction: str = "",
    detected_pattern: str = "",
    strategy_family: str = "",
    candidate_strategies: list[dict[str, Any]] | None = None,
    strategy_conditions: dict[str, Any] | None = None,
    conditions_passed: int = 0,
    conditions_failed: int = 0,
    conditions_missing: int = 0,
    missing_data: list[str] | None = None,
    evaluation_status: str = "",
    confidence: float = 0.0,
    tradability_score: float = 0.0,
    eligible_by_phase: bool = False,
    pattern_in_triggers: bool = False,
    source_version: str = _SCHEMA_VERSION,
) -> dict[str, Any]:
    """
    Build a canonical strategy observation record for persistence.

    This schema supports the research questions:
        "Given this exact market environment, which strategies were considered?"
        "When these conditions appeared, what happened afterwards?"

    Returns:
        Flat dict suitable for JSONL serialisation.
    """
    return {
        "record_role": "strategy_observation_projection",
        "authority": "non_decision_projection",
        "schema_version": source_version,
        "observation_id": observation_id,
        "timestamp_utc": timestamp_utc,
        "symbol": symbol,
        "cycle_id": cycle_id,
        # Market environment
        "market_phase": market_phase,
        "h4_regime": h4_regime,
        "h1_bias": h1_bias,
        "direction": direction,
        # Pattern
        "detected_pattern": detected_pattern,
        "pattern_in_triggers": pattern_in_triggers,
        # Strategy classification
        "strategy_family": strategy_family,
        "candidate_strategies": candidate_strategies or [],
        # Condition evaluation
        "strategy_conditions": strategy_conditions or {},
        "conditions_passed": conditions_passed,
        "conditions_failed": conditions_failed,
        "conditions_missing": conditions_missing,
        "missing_data": missing_data or [],
        "evaluation_status": evaluation_status,
        "confidence": round(confidence, 4),
        # Context quality
        "tradability_score": round(tradability_score, 4),
        "eligible_by_phase": eligible_by_phase,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE
# ═══════════════════════════════════════════════════════════════════════════════


def persist_strategy_observation(record: dict[str, Any]) -> bool:
    """
    Persist a strategy observation record to local JSONL + S3 mirror.

    Fire-and-forget. Never raises. Never blocks the trading pipeline.

    Args:
        record: Dict from build_observation_record() or StrategyObservation.to_dict()

    Returns:
        True if local write succeeded, False otherwise.
    """
    try:
        symbol = record.get("symbol", "UNKNOWN")
        ts = record.get("timestamp_utc", 0)

        if isinstance(ts, (int, float)) and ts > 1_000_000_000:
            date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        else:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        line = json.dumps(record, separators=(",", ":"), default=str)

        # ─── LOCAL PERSISTENCE (PRIMARY) ──────────────────────────────
        path = Path(_LOCAL_DIR) / symbol / f"{date_str}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)

        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

        # ─── S3 MIRROR (SECONDARY) ───────────────────────────────────
        _write_s3(symbol, date_str, line)

        return True

    except Exception as exc:
        logger.debug(
            "[STRATEGY_OBSERVATION_PERSIST] failed: symbol=%s error=%s",
            record.get("symbol", "?"), exc,
        )
        return False


def persist_observation_batch(records: list[dict[str, Any]]) -> int:
    """
    Persist multiple observation records. Returns count of successful writes.
    """
    success = 0
    for record in records:
        if persist_strategy_observation(record):
            success += 1
    return success


# ═══════════════════════════════════════════════════════════════════════════════
# S3 MIRROR
# ═══════════════════════════════════════════════════════════════════════════════


def _write_s3(symbol: str, date_str: str, line: str) -> None:
    """
    Mirror a single observation line to S3. Fire-and-forget.

    Pattern matches core/assessment/persistence.py and core/trade_truth_graph.py.
    Never raises. Never blocks runtime.
    """
    try:
        from core import config
        if not getattr(config, "EVENT_STREAM_S3_MIRROR", False):
            return

        import boto3
        from botocore.config import Config as BotoConfig
        s3 = boto3.client(
            "s3",
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=os.getenv("AWS_REGION", "eu-west-2"),
            config=BotoConfig(
                connect_timeout=3,
                read_timeout=5,
                retries={"max_attempts": 0},
            ),
        )
        key = f"{_S3_PREFIX}/symbol={symbol}/date={date_str}/part-000.jsonl"
        body = line + "\n"

        # Read-append-write (acceptable for observation volume)
        try:
            existing = s3.get_object(Bucket=_S3_BUCKET, Key=key)
            body = existing["Body"].read().decode("utf-8") + body
        except Exception:
            pass  # New file

        s3.put_object(
            Bucket=_S3_BUCKET, Key=key,
            Body=body.encode("utf-8"),
            ContentType="application/x-ndjson",
        )
    except Exception:
        pass  # S3 failure must never affect runtime


# ═══════════════════════════════════════════════════════════════════════════════
# READING (for research queries)
# ═══════════════════════════════════════════════════════════════════════════════


def read_observations_local(
    *,
    symbol: str | None = None,
    date: str | None = None,
) -> list[dict[str, Any]]:
    """
    Read persisted observations from local JSONL files.

    Args:
        symbol: Filter by symbol (None = all symbols)
        date: Filter by date YYYY-MM-DD (None = all dates)

    Returns:
        List of parsed observation dicts.
    """
    base = Path(_LOCAL_DIR)
    if not base.exists():
        return []

    results: list[dict[str, Any]] = []

    if symbol:
        dirs = [base / symbol]
    else:
        dirs = [d for d in base.iterdir() if d.is_dir()]

    for dir_path in dirs:
        if not dir_path.exists():
            continue

        if date:
            files = [dir_path / f"{date}.jsonl"]
        else:
            files = sorted(dir_path.glob("*.jsonl"))

        for filepath in files:
            if not filepath.exists():
                continue
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            results.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
            except OSError:
                continue

    return results


def get_observation_stats() -> dict[str, Any]:
    """
    Return summary statistics of persisted observations.

    Scans local JSONL directory for counts.
    """
    base = Path(_LOCAL_DIR)
    if not base.exists():
        return {"total_files": 0, "symbols": [], "total_observations": 0}

    symbols: list[str] = []
    total_files = 0
    total_lines = 0

    for dir_path in sorted(base.iterdir()):
        if dir_path.is_dir():
            symbols.append(dir_path.name)
            for filepath in dir_path.glob("*.jsonl"):
                total_files += 1
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        total_lines += sum(1 for line in f if line.strip())
                except OSError:
                    pass

    return {
        "total_files": total_files,
        "symbols": symbols,
        "total_observations": total_lines,
        "local_dir": str(base),
        "s3_prefix": f"s3://{_S3_BUCKET}/{_S3_PREFIX}/",
    }
