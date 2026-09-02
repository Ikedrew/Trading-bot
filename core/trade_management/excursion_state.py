"""
Durable open-position excursion state — makes MFE/MAE survive bot restarts.

An open position tracks max_favourable_price / max_adverse_price in memory. This
module persists that pair as a small per-ticket checkpoint so that, after a
restart, the reconstructed position restores its HISTORICAL extremes instead of
re-seeding purely from the broker's current price. The final mfe_r / mae_r then
describe the ENTIRE trade lifetime.

Design (mirrors core/state_persistence.py):
    - one JSON file per broker position ticket: logs/position_excursion/{ticket}.json
    - atomic write (temp → fsync → replace)
    - written ONLY when an excursion extreme actually changes (not per tick)
    - fire-and-forget: never raises, never blocks/affects trading
    - keyed by broker position ticket, backed by canonical lineage for proof

This is OBSERVATIONAL telemetry only. Nothing here feeds live trading decisions.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core import config

logger = logging.getLogger(__name__)

# ─── Durable runtime-state S3 mirror ──────────────────────────────────────────
# The excursion checkpoint is DURABLE RUNTIME STATE that backs the existing
# trade_truth_v1 outcome chain — NOT a research dataset. It is therefore kept out
# of the research-dataset registry (core/production_data_contract.py) and lives
# under a distinct top-level `runtime_state/` prefix so it can never be mistaken
# for a core/supporting/projection research dataset. Latest-state object per
# broker ticket: overwrite on each new extreme (no append, no event stream).
_S3_BUCKET = config.NEW_RUNTIME_S3_BUCKET
_S3_EXCURSION_PREFIX = "runtime_state/position_excursion"
_S3_SCHEMA_VERSION = "position_excursion_v1"


def _s3_key(ticket: int) -> str:
    return f"{_S3_EXCURSION_PREFIX}/schema_version={_S3_SCHEMA_VERSION}/ticket={int(ticket)}.json"


def _s3_mirror_enabled() -> bool:
    return bool(getattr(config, "POSITION_EXCURSION_S3_MIRROR", False))


def _s3_client():
    import boto3
    return boto3.client(
        "s3",
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        region_name=os.getenv("AWS_REGION", "eu-west-2"),
    )


def _s3_persist_excursion(ticket: int, payload: bytes) -> None:
    """Overwrite the per-ticket S3 checkpoint with the SAME bytes written locally.

    Secondary + non-blocking. Any failure is swallowed after a warning so local
    persistence and live trading are never affected by S3 availability.
    """
    if not _s3_mirror_enabled():
        return
    try:
        s3 = _s3_client()
        s3.put_object(
            Bucket=_S3_BUCKET,
            Key=_s3_key(ticket),
            Body=payload,
            ContentType="application/json",
        )
    except Exception as exc:
        logger.warning("[EXCURSION_STATE_S3_SAVE] mirror failed ticket=%s: %s", ticket, exc)


def _s3_load_excursion(ticket: int) -> dict[str, Any] | None:
    """Load the per-ticket checkpoint from S3. Returns validated dict or None.

    Applies the same ticket-identity + staleness guards as the local reader.
    Never raises.
    """
    if not _s3_mirror_enabled():
        return None
    try:
        s3 = _s3_client()
        obj = s3.get_object(Bucket=_S3_BUCKET, Key=_s3_key(ticket))
        data = json.loads(obj["Body"].read().decode("utf-8"))
        return _validate_snapshot(data, ticket)
    except Exception as exc:
        logger.debug("[EXCURSION_STATE_S3_LOAD] miss/fail ticket=%s: %s", ticket, exc)
        return None


def _rehydrate_local(ticket: int, snapshot: dict[str, Any]) -> None:
    """Re-create the local checkpoint from a validated S3 snapshot.

    Lets subsequent reads/restarts on this (replacement) machine use the normal
    local-first path. Best-effort: never raises, never affects trading.
    """
    try:
        d = _get_dir()
        d.mkdir(parents=True, exist_ok=True)
        filepath = d / f"{int(ticket)}.json"
        payload = json.dumps(snapshot, default=str).encode("utf-8")
        fd, tmp_path = tempfile.mkstemp(dir=str(d), suffix=".tmp", prefix=f"{int(ticket)}_")
        try:
            os.write(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp_path, str(filepath))
    except Exception as exc:
        logger.debug("[EXCURSION_STATE_REHYDRATE] failed ticket=%s: %s", ticket, exc)


def _validate_snapshot(data: Any, ticket: int) -> dict[str, Any] | None:
    """Shared validation for local + S3 snapshots: exact ticket + not stale."""
    if not isinstance(data, dict):
        return None
    if int(data.get("position_ticket", 0) or 0) != int(ticket):
        return None  # stale/mismatched — never attach to the wrong trade
    age = time.time() - float(data.get("updated_at_unix", 0) or 0)
    if age > _max_age_seconds():
        logger.debug("[EXCURSION_STATE_LOAD] stale ticket=%s age=%.0f", ticket, age)
        return None
    return data


def _get_dir() -> Path:
    return Path(getattr(config, "POSITION_EXCURSION_DIR", "logs/position_excursion"))


def _max_age_seconds() -> float:
    # Reuse the engine-state warm-start age bound unless overridden.
    return float(getattr(config, "POSITION_EXCURSION_MAX_AGE_SECONDS",
                         getattr(config, "ENGINE_STATE_MAX_AGE_SECONDS", 86400)))


def persist_excursion(position: Any) -> None:
    """Persist one open position's excursion extremes. Fire-and-forget.

    Called only when an extreme changed (see manager._process_one_position).
    Keyed by broker position ticket; carries canonical lineage for association.
    Never raises. Never affects trading.
    """
    try:
        ticket = getattr(position, "mt5_ticket", None)
        if not ticket or int(ticket) <= 0:
            return  # no durable broker key → cannot prove association on restart

        identity = getattr(position, "trade_identity", None)
        side = getattr(position, "side", None)
        side_name = side.name if side is not None and hasattr(side, "name") else str(side)

        snapshot: dict[str, Any] = {
            "position_ticket": int(ticket),
            "trade_id": getattr(position, "position_id", ""),
            "symbol": getattr(position, "symbol", ""),
            "side": side_name,
            "entry_price": getattr(position, "entry_price", None),
            "canonical_opportunity_id": getattr(identity, "canonical_opportunity_id", "") if identity else "",
            "correlation_id": getattr(identity, "correlation_id", "") if identity else "",
            "max_favourable_price": getattr(position, "max_favourable_price", None),
            "max_adverse_price": getattr(position, "max_adverse_price", None),
            "updated_at_utc": datetime.now(tz=timezone.utc).isoformat(),
            "updated_at_unix": time.time(),
        }

        d = _get_dir()
        d.mkdir(parents=True, exist_ok=True)
        filepath = d / f"{int(ticket)}.json"

        payload = json.dumps(snapshot, default=str).encode("utf-8")
        fd, tmp_path = tempfile.mkstemp(dir=str(d), suffix=".tmp", prefix=f"{int(ticket)}_")
        try:
            os.write(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp_path, str(filepath))

        # Secondary durable copy → S3 (same bytes). Non-blocking: local write has
        # already succeeded above; a mirror failure never propagates.
        _s3_persist_excursion(int(ticket), payload)
    except Exception as exc:
        logger.debug("[EXCURSION_STATE_SAVE] failed: %s", exc)


def load_excursion(ticket: int) -> dict[str, Any] | None:
    """Load persisted excursion extremes for a broker position ticket.

    Returns the snapshot dict, or None when absent/stale/invalid. Never raises.
    The caller MUST verify the ticket belongs to the recovered position (this is
    already guaranteed here since the file is keyed by the exact ticket).
    """
    if not ticket or int(ticket) <= 0:
        return None

    # 1) LOCAL FIRST — same-machine normal path.
    local = None
    try:
        filepath = _get_dir() / f"{int(ticket)}.json"
        if filepath.exists():
            with open(filepath, "r", encoding="utf-8") as f:
                local = _validate_snapshot(json.load(f), int(ticket))
    except Exception as exc:
        logger.debug("[EXCURSION_STATE_LOAD] local failed ticket=%s: %s", ticket, exc)
        local = None
    if local is not None:
        return local

    # 2) S3 FALLBACK — local missing / unreadable / invalid / stale. Used after
    #    VM restart, disk loss, instance replacement or machine migration.
    s3_snapshot = _s3_load_excursion(int(ticket))
    if s3_snapshot is not None:
        logger.info("[EXCURSION_STATE_LOAD] restored from S3 fallback ticket=%s", ticket)
        _rehydrate_local(int(ticket), s3_snapshot)  # so subsequent reads use local-first
        return s3_snapshot

    # 3) Neither local nor S3 → caller falls back to legacy recovery_seeded.
    return None


def restore_extremes(
    *,
    side_name: str,
    saved_mfe: float | None,
    saved_mae: float | None,
    current_price: float | None,
) -> tuple[float | None, float | None]:
    """Combine saved historical extremes with the current recovery price.

    The current observation may EXTEND a historical extreme but must NEVER erase
    it. Uses the tracker's side-price convention (BUY excursion=bid, SELL=ask);
    at recovery only `price_current` is available, so it is used consistently.

        BUY : mfe = max(saved_mfe, price) ; mae = min(saved_mae, price)
        SELL: mfe = min(saved_mfe, price) ; mae = max(saved_mae, price)
    """
    is_buy = str(side_name).upper() == "BUY"

    def _combine(saved: float | None, favourable: bool) -> float | None:
        if saved is None and current_price is None:
            return None
        if saved is None:
            return current_price
        if current_price is None:
            return saved
        if (favourable and is_buy) or (not favourable and not is_buy):
            return max(saved, current_price)  # higher price
        return min(saved, current_price)      # lower price

    return _combine(saved_mfe, favourable=True), _combine(saved_mae, favourable=False)
