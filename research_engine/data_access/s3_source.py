"""
Shared S3 Research Data-Access Layer — the SINGLE source of truth.

The Research Engine reads ALL persistent source data through this one layer.
No experiment, universe builder, dataset builder, correlation/linker, causal
replay, or research-ready builder may read production source data from local
``logs/`` — S3 is authoritative. Local logs remain only for live-runtime
persistence / debugging and are NOT a research source.

Design goals (permanent architecture, not a toggle):
    Research Engine → S3ResearchDataSource.read_dataset(...) → S3

Responsibilities owned here (and nowhere else):
    - S3 client creation (env credentials, canonical region)
    - canonical bucket selection (core.config.NEW_RUNTIME_S3_BUCKET)
    - dataset prefix resolution (core.production_data_contract.s3_base_prefix)
    - schema/version resolution (current_schema + supported_schemas)
    - list_objects_v2 pagination (continuation tokens)
    - symbol / date / start-end prefix pruning BEFORE download
    - JSON / JSONL decoding with malformed-record reporting
    - explicit missing-object behaviour (empty result, never a local fallback)
    - deterministic, dataset-appropriate ordering
    - run-level in-memory cache (load each object set once per run)
    - clear failure surfacing (S3 errors raise ResearchDataSourceError)

This module is dataset-oriented and schema-aware: callers ask for a logical
dataset name from the production contract, never a hand-built S3 request. It is
NOT built around any one schema version — new V2/V3 datasets only need a registry
entry, not an architecture change.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Iterable

from core.config import NEW_RUNTIME_S3_BUCKET
from core.production_data_contract import (
    PRODUCTION_SCHEMA_REGISTRY,
    RETIRED_DATASETS,
    current_schema,
    s3_base_prefix,
    supported_schemas,
)

logger = logging.getLogger(__name__)


class ResearchDataSourceError(RuntimeError):
    """Raised when the S3 research data source cannot be read.

    This is a RESEARCH-DATA-SOURCE failure. It must surface clearly and must
    NEVER cause a silent fallback to local logs. Live trading is unaffected —
    this path is offline-only.
    """


# ─── Dataset-appropriate deterministic ordering ──────────────────────────────
# S3 listing order must never determine research results. After loading, records
# are ordered by a dataset-appropriate key so runs are reproducible. Each entry
# is a tuple of candidate keys tried in order (first present wins); nested keys
# use dotted paths. Datasets not listed fall back to _DEFAULT_ORDER_KEYS.
_ORDER_KEYS: dict[str, tuple[str, ...]] = {
    "trade_truth": ("timestamps.exit_timestamp_broker", "timestamps.entry_timestamp_broker"),
    "decision_trace": ("timestamp_utc", "cycle_id"),
    "decision_ledger": ("timestamp_utc", "cycle_id"),
    "market_context": ("timestamp_utc", "cycle_id"),
    "shadow_trades": ("decision_snapshot.timestamp_decision_utc", "entry_time", "timestamp_utc"),
    "execution_results": ("timestamp_utc", "cycle_id"),
    "execution_context": ("timestamp_utc",),
    "opportunities": ("timestamp_utc", "cycle_id"),
    "assessments": ("timestamp_utc", "cycle_id"),
    "strategy_observations": ("timestamp_utc", "cycle_id"),
    "risk_deviation": ("timestamp_utc",),
    "protection_audit": ("timestamp_utc",),
    "portfolio_rankings": ("cycle_id", "timestamp_utc"),
    "portfolio_shadow": ("cycle_id", "timestamp_utc"),
}
_DEFAULT_ORDER_KEYS: tuple[str, ...] = ("timestamp_utc",)

# Datasets partitioned by DATE only (no symbol= partition in their S3 layout).
_DATE_ONLY_DATASETS: frozenset[str] = frozenset({"portfolio_rankings", "portfolio_shadow"})


def _dig(record: dict[str, Any], dotted: str) -> Any:
    """Resolve a dotted key path within a record; None if absent."""
    cur: Any = record
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _order_value(record: dict[str, Any], keys: tuple[str, ...]) -> tuple[int, float, str]:
    """Return a sortable tuple. Records with no usable key sort last but stably."""
    for k in keys:
        v = _dig(record, k)
        if isinstance(v, (int, float)):
            return (0, float(v), "")
        if isinstance(v, str) and v:
            return (0, 0.0, v)
    return (1, 0.0, "")


@dataclass
class MalformedReport:
    """Per-dataset malformed-record accounting for research integrity."""
    dataset: str
    malformed_lines: int = 0
    keys_with_errors: list[str] = field(default_factory=list)


class S3ResearchDataSource:
    """Shared, dataset-oriented S3 reader for the Research Engine.

    A single instance is intended to live for the duration of one research run so
    its in-memory cache serves every universe/experiment without re-downloading
    identical objects. Construct a fresh instance per run to guarantee no stale
    data crosses run boundaries.
    """

    def __init__(
        self,
        *,
        bucket: str | None = None,
        client: Any | None = None,
        region: str | None = None,
    ):
        self._bucket = bucket or NEW_RUNTIME_S3_BUCKET
        self._region = region or os.getenv("AWS_REGION", "eu-west-2")
        self._client = client  # dependency-injectable for tests
        # Run-level cache keyed by (dataset, symbol, start, end, schema-set).
        self._cache: dict[tuple, list[dict[str, Any]]] = {}
        self._malformed: dict[str, MalformedReport] = {}

    # ─── client ───────────────────────────────────────────────────────────────

    @property
    def bucket(self) -> str:
        return self._bucket

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise ResearchDataSourceError(
                "boto3 is required for the Research Engine S3 data source"
            ) from exc
        self._client = boto3.client(
            "s3",
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=self._region,
        )
        return self._client

    # ─── prefix resolution ──────────────────────────────────────────────────

    def _base_prefix(self, dataset: str) -> str:
        if dataset in RETIRED_DATASETS:
            raise ResearchDataSourceError(
                f"dataset '{dataset}' is RETIRED — migrate to its retained authority"
            )
        if dataset not in PRODUCTION_SCHEMA_REGISTRY:
            raise ResearchDataSourceError(
                f"unknown dataset '{dataset}' — not in the production data contract"
            )
        return s3_base_prefix(dataset)

    def _schema_prefixes(self, dataset: str, *, all_schemas: bool) -> list[str]:
        """S3 prefixes narrowed to schema_version partition(s).

        Default: the current schema only. ``all_schemas=True`` widens to every
        supported (current + legacy) schema — an explicit historical query
        capability, never the default source behaviour.
        """
        base = self._base_prefix(dataset)
        schemas = sorted(supported_schemas(dataset)) if all_schemas else [current_schema(dataset)]
        return [f"{base}/schema_version={s}/" for s in schemas]

    def _list_prefixes(
        self,
        dataset: str,
        *,
        symbol: str | None,
        all_schemas: bool,
    ) -> list[str]:
        """Compute the narrowest S3 prefixes for the request.

        Symbol pruning is applied at the prefix level for symbol-partitioned
        datasets so we never list the whole dataset when a symbol is requested.
        Date pruning is applied per-key after listing (keys carry date=...),
        which still avoids downloading out-of-range objects.
        """
        prefixes = self._schema_prefixes(dataset, all_schemas=all_schemas)
        if symbol and dataset not in _DATE_ONLY_DATASETS:
            return [f"{p}symbol={symbol}/" for p in prefixes]
        return prefixes

    # ─── listing (paginated) ──────────────────────────────────────────────────

    def _iter_keys(self, prefix: str) -> Iterable[str]:
        """Yield every .jsonl object key under a prefix, following pagination.

        Uses list_objects_v2 with explicit continuation-token handling so all
        pages are consumed (never assumes a single response returns everything).
        """
        client = self._get_client()
        token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"Bucket": self._bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            try:
                resp = client.list_objects_v2(**kwargs)
            except Exception as exc:
                raise ResearchDataSourceError(
                    f"S3 list_objects_v2 failed for prefix '{prefix}': {exc}"
                ) from exc
            for obj in resp.get("Contents", []) or []:
                key = obj.get("Key", "")
                if key.endswith(".jsonl"):
                    yield key
            if resp.get("IsTruncated"):
                token = resp.get("NextContinuationToken")
                if not token:
                    break
            else:
                break

    @staticmethod
    def _key_date(key: str) -> str | None:
        """Extract the date=YYYY-MM-DD partition value from an S3 key, if present."""
        for part in key.split("/"):
            if part.startswith("date="):
                return part[len("date="):]
        return None

    def _in_range(self, key: str, start: str | None, end: str | None) -> bool:
        if start is None and end is None:
            return True
        d = self._key_date(key)
        if d is None:
            return True  # cannot prune keys without a date partition — include
        if start is not None and d < start:
            return False
        if end is not None and d > end:
            return False
        return True

    # ─── object read + decode ─────────────────────────────────────────────────

    def _read_object(self, dataset: str, key: str) -> list[dict[str, Any]]:
        client = self._get_client()
        try:
            resp = client.get_object(Bucket=self._bucket, Key=key)
            body = resp["Body"].read()
            if isinstance(body, bytes):
                body = body.decode("utf-8")
        except Exception as exc:
            raise ResearchDataSourceError(
                f"S3 get_object failed for key '{key}': {exc}"
            ) from exc

        out: list[dict[str, Any]] = []
        report = self._malformed.setdefault(dataset, MalformedReport(dataset=dataset))
        for line in body.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                report.malformed_lines += 1
                if key not in report.keys_with_errors:
                    report.keys_with_errors.append(key)
                continue
            if isinstance(rec, dict):
                out.append(rec)
        return out

    # ─── public API ─────────────────────────────────────────────────────────

    def read_artifact(self, name: str) -> list[dict[str, Any]]:
        """Read a DERIVED research artifact from S3 (rebuildable, not source-of-truth).

        Research artifacts (e.g. the research-ready trade dataset) are computed
        offline from source datasets and persisted back to S3 under the
        ``research_artifacts/`` prefix so the Research Engine can rebuild its
        working dataset from S3 after local files are deleted. This is NOT a
        production-contract runtime dataset — it is a rebuildable derived copy.
        A missing artifact returns an empty list (a real gap, no local fallback).
        """
        cache_key = ("__artifact__", name, None, None, False)
        if cache_key in self._cache:
            return self._cache[cache_key]
        prefix = f"research_artifacts/{name}/"
        records: list[dict[str, Any]] = []
        for key in self._iter_keys(prefix):
            records.extend(self._read_object(f"artifact:{name}", key))
        logger.info("[S3_RESEARCH] artifact=%s loaded=%d", name, len(records))
        self._cache[cache_key] = records
        return records

    def read_dataset(
        self,
        dataset: str,
        *,
        symbol: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        all_schemas: bool = False,
    ) -> list[dict[str, Any]]:
        """Read a logical dataset from S3 with targeted pruning + ordering.

        Args:
            dataset: production-contract dataset name (e.g. "trade_truth").
            symbol: restrict to one symbol (prefix-pruned where partitioned).
            start_date / end_date: inclusive YYYY-MM-DD bounds (key-pruned).
            all_schemas: include supported legacy schemas (historical query).

        Returns: list of record dicts, deterministically ordered. An empty list
        means the requested dataset/scope has NO objects in S3 — a real
        collection gap, never a silent local fallback.
        """
        cache_key = (dataset, symbol, start_date, end_date, all_schemas)
        if cache_key in self._cache:
            return self._cache[cache_key]

        records: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        for prefix in self._list_prefixes(dataset, symbol=symbol, all_schemas=all_schemas):
            for key in self._iter_keys(prefix):
                if key in seen_keys:
                    continue
                if not self._in_range(key, start_date, end_date):
                    continue
                seen_keys.add(key)
                records.extend(self._read_object(dataset, key))

        order_keys = _ORDER_KEYS.get(dataset, _DEFAULT_ORDER_KEYS)
        records.sort(key=lambda r: _order_value(r, order_keys))

        rep = self._malformed.get(dataset)
        if rep and rep.malformed_lines:
            logger.warning(
                "[S3_RESEARCH] dataset=%s malformed_lines=%d across %d object(s)",
                dataset, rep.malformed_lines, len(rep.keys_with_errors),
            )
        logger.info(
            "[S3_RESEARCH] dataset=%s symbol=%s range=%s..%s loaded=%d objects=%d",
            dataset, symbol or "*", start_date or "-", end_date or "-",
            len(records), len(seen_keys),
        )
        self._cache[cache_key] = records
        return records

    def malformed_report(self, dataset: str) -> MalformedReport | None:
        """Return the malformed-record accounting for a dataset, if any."""
        return self._malformed.get(dataset)

    def clear_cache(self) -> None:
        """Drop the run-level cache (e.g. between independent research runs)."""
        self._cache.clear()


# ─── Run-scoped default source ────────────────────────────────────────────────
# A process-wide default instance so loaders/universes share ONE cache within a
# run without threading a source object through every call site. Replaceable in
# tests via set_default_source(); rebuildable via reset_default_source().

_default_source: S3ResearchDataSource | None = None


def get_default_source() -> S3ResearchDataSource:
    global _default_source
    if _default_source is None:
        _default_source = S3ResearchDataSource()
    return _default_source


def set_default_source(source: S3ResearchDataSource | None) -> None:
    """Inject a source (tests) or clear it (None)."""
    global _default_source
    _default_source = source


def reset_default_source() -> None:
    """Start a fresh run: new instance, empty cache, no stale data carried over."""
    global _default_source
    _default_source = None


def read_dataset(dataset: str, **kwargs: Any) -> list[dict[str, Any]]:
    """Module-level convenience: read via the run-scoped default source."""
    return get_default_source().read_dataset(dataset, **kwargs)
