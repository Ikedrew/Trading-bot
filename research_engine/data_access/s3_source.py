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
    - S3 client creation (RESEARCH_AWS_PROFILE or the standard boto3 chain,
      canonical region, actionable secret-free diagnostics)
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
import hashlib
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from core.config import NEW_RUNTIME_S3_BUCKET
from core.production_data_contract import (
    PRODUCTION_SCHEMA_REGISTRY,
    RETIRED_DATASETS,
    canonical_s3_list_prefix,
    current_schema,
    s3_base_prefix,
    supported_schemas,
)

logger = logging.getLogger(__name__)

_CANONICAL_REGION = "eu-west-2"
_RESEARCH_AWS_PROFILE_ENV = "RESEARCH_AWS_PROFILE"
# S3 permission/credential denial codes — a permissions/account problem, NEVER an
# SSO-expiry or network error.
_DENIED_CODES = {
    "AccessDenied", "ExpiredToken", "InvalidToken",
    "SignatureDoesNotMatch", "AuthorizationHeaderMalformed",
}


class ResearchDataSourceError(RuntimeError):
    """Raised when the S3 research data source cannot be read.

    This is a RESEARCH-DATA-SOURCE failure. It must surface clearly and must
    NEVER cause a silent fallback to local logs. Live trading is unaffected —
    this path is offline-only.
    """


# ─── AWS credential/session resolution ────────────────────────────────────────
# Research Engine readers authenticate to S3 through ONE path below.
#
# Priority 1 — explicit research profile:  RESEARCH_AWS_PROFILE=<profile>
#     A boto3 Session is built with that NAMED profile (SSO/session keys) and its
#     credentials are resolved eagerly so a misconfigured/expired profile fails
#     here with an actionable error. Local research therefore NEVER falls through
#     to the laptop machine's wrong default AWS profile.
# Priority 2 — standard boto3 chain:       RESEARCH_AWS_PROFILE unset/empty
#     No profile is forced: the normal default chain runs (AWS_PROFILE,
#     environment credentials, shared config, web identity, EC2 instance role via
#     IMDS). This is what the production VM relies on (instance role
#     Trading-Bot-S3-Access); nothing here may block/force IMDS credential use.
# Priority 3 — explicit failure:           any S3/credential error surfaces as a
#     ResearchDataSourceError with actionable, secret-free diagnostics. Never a
#     silent local fallback, never a silent account switch.


def _build_session(profile: str | None, region: str) -> Any:
    """Create the ONE sanctioned boto3 Session for Research Engine reads.

    Explicit profile → Session(profile_name=...)      (RESEARCH_AWS_PROFILE)
    No profile       → Session(region_name=...) using the standard default chain
                       (AWS_PROFILE / env / shared config / EC2 instance role).

    Dependency hook for tests — patched via monkeypatch. Real calls never touch
    AWS/Ec2Metadata at construction time (credentials resolve lazily by boto3).
    """
    import boto3

    if profile:
        return boto3.Session(profile_name=profile, region_name=region)
    return boto3.Session(region_name=region)


def _describe_aws_error(
    exc: Exception,
    *,
    profile: str | None,
    bucket: str,
    region: str,
    operation: str,
) -> str:
    """Build a secret-free, actionable diagnostic for an AWS failure.

    The ``aws sso login`` hint is emitted ONLY when the failure is genuinely an
    SSO/profile-authentication problem (SSO token load / SSO refresh failure with
    expiry evidence). Access-denied, network, and generic errors are never
    mislabelled as SSO expiry, and no credential material is ever included.
    """
    name = type(exc).__name__
    detail = str(exc) or "(no detail)"
    context = f"bucket={bucket}, region={region}, operation={operation}"
    via = f"research profile '{profile}'" if profile else "the default AWS chain"

    # ── SSO authentication evidence (explicit research profile only) ──────────
    if profile and name in ("SSOTokenLoadError", "UnauthorizedSSOTokenError"):
        return (
            f"AWS SSO credentials for profile '{profile}' are unavailable or "
            f"expired ({context}); run: aws sso login --profile {profile}."
        )
    if profile and name in ("SSOError", "CredentialRetrievalError"):
        if any(
            k in detail.lower()
            for k in ("expired", "token", "unauthorized", "401", "invalid session")
        ):
            return (
                f"RESEARCH_AWS_PROFILE='{profile}': AWS SSO credentials are "
                f"unavailable or expired ({context}); run: aws sso login "
                f"--profile {profile}. Original failure: {detail}"
            )
    if name == "ProfileNotFound":
        who = (
            f"research profile '{profile}' (from RESEARCH_AWS_PROFILE)"
            if profile
            else "the profile selected by the default AWS chain"
        )
        fix = (
            "Fix the profile name or unset RESEARCH_AWS_PROFILE."
            if profile
            else "Check AWS_PROFILE / the shared AWS config files."
        )
        return (
            f"AWS profile for {who} does not exist ({context}). {fix} "
            f"Original failure: {detail}"
        )
    if name in ("NoCredentialsError", "InvalidConfigError"):
        return (
            f"No AWS credentials were resolved via {via} ({context}). Set "
            f"RESEARCH_AWS_PROFILE to a configured local SSO profile, or rely on "
            f"AWS_PROFILE / environment credentials / EC2 instance role."
        )
    if name == "PartialCredentialsError":
        return f"Incomplete AWS credentials via {via} ({context}): {detail}"
    if name == "ClientError" or name in _DENIED_CODES:
        # Denial detection works across botocore versions: classic ClientError
        # carries the code in exc.response; newer botocore raises dedicated
        # exception classes (e.g. `AccessDenied`) whose NAME is the denial code.
        resp = getattr(exc, "response", None) or {}
        code = str((resp.get("Error") or {}).get("Code") or "")
        denied = code in _DENIED_CODES or name in _DENIED_CODES
        if denied:
            return (
                f"AWS access was DENIED via {via} ({context}, code='{code or name}'). "
                f"Confirm the active credentials belong to the account allowed "
                f"to read the research bucket (check RESEARCH_AWS_PROFILE / "
                f"AWS_PROFILE). This is a permissions/account problem, NOT an "
                f"SSO-expiry or network error. Original failure: {detail}"
            )
        return f"AWS request failed via {via} ({context}, code='{code or name}'): {detail}"
    # ── everything else (network, timeouts, 5xx, plugin errors) ───────────────
    return f"AWS failure via {via} ({context}, type={name}): {detail}"


# ─── Dataset-appropriate deterministic ordering ──────────────────────────────
# S3 listing order must never determine research results. After loading, records
# are ordered by a dataset-appropriate key so runs are reproducible. Each entry
# is a tuple of candidate keys tried in order (first present wins); nested keys
# use dotted paths. Datasets not listed fall back to _DEFAULT_ORDER_KEYS.
_ORDER_KEYS: dict[str, tuple[str, ...]] = {
    "trade_truth": ("timestamps.exit_timestamp_broker", "timestamps.entry_timestamp_broker"),
    "trade_journal": ("exit_time", "entry_time", "timestamp_utc"),
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
    # Step-4 connected datasets:
    "horizon_candidates": ("bar_time", "cycle_id"),
    "strategy_candidates": ("bar_time", "cycle_id"),
    "execution_attempts": ("timestamp_unix", "timestamp_utc", "cycle_id"),
    "management_actions": ("timestamp_unix", "timestamp_utc", "cycle_id"),
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


def _assert_freeze_record_schema(dataset: str, record: Any) -> None:
    """Reject a wrong-schema record at the freeze boundary (schema identity).

    This is the strict per-record gate used ONLY when acquiring a frozen
    frontier (``read_bound_objects``/``read_objects_for_freeze``). It is
    deliberately NOT used by the live ``read_dataset`` path, whose contract is
    to skip-and-report malformed lines rather than fail closed, so no existing
    loader regresses.

    Rule (never inventing fields, never weakening a production contract):
      * For a governed production dataset the record's ``schema_version`` must
        equal the contract's canonical schema for that dataset. This enforces
        schema IDENTITY for every one of the ten bound datasets.

    Scope note: the richer field/type rules of ``core.canonical_profiles``
    (required fields, version-field baselines) are the WRITER/ingestion
    obligation enforced on the persist path; they are intentionally not
    re-imposed on the freeze READ boundary, which legitimately consumes the
    exact governed bytes the writer already validated. Unprofiled datasets
    therefore receive schema identity here and never a fabricated field set.
    A non-object record (never a legitimate governed record) is rejected by
    the caller before this runs.
    """
    if dataset not in PRODUCTION_SCHEMA_REGISTRY:
        # Not a governed production dataset (e.g. derived artifacts): the freeze
        # boundary does not impose a schema contract here.
        return

    expected = current_schema(dataset)
    claimed = record.get("schema_version") if isinstance(record, dict) else None
    if claimed != expected:
        raise ResearchDataSourceError(
            f"SNAPSHOT_RECORD_SCHEMA_MISMATCH:{dataset}:"
            f"record={claimed!r}!={expected!r}")



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
        profile: str | None = None,
    ):
        self._bucket = bucket or NEW_RUNTIME_S3_BUCKET
        self._region = region or os.getenv("AWS_REGION", _CANONICAL_REGION)
        self._client = client  # dependency-injectable for tests
        # Explicit Research Engine profile (RESEARCH_AWS_PROFILE) → priority 1.
        # None → the standard boto3 chain (AWS_PROFILE / env / shared config /
        # EC2 instance role), preserving EC2 instance-role behaviour verbatim.
        self._research_profile = (
            profile
            if profile is not None
            else (os.getenv(_RESEARCH_AWS_PROFILE_ENV, "") or "").strip() or None
        )
        # Run-level cache keyed by (dataset, symbol, start, end, schema-set).
        self._cache: dict[tuple, list[dict[str, Any]]] = {}
        self._malformed: dict[str, MalformedReport] = {}
        self._listed_objects: dict[str, dict[str, Any]] = {}
        self._dataset_objects: dict[str, tuple[dict[str, Any], ...]] = {}
        self._read_objects: dict[tuple[str, str], dict[str, Any]] = {}
        self._headed_objects: dict[tuple[str, str], dict[str, Any]] = {}

    # ─── client ───────────────────────────────────────────────────────────────

    @property
    def bucket(self) -> str:
        return self._bucket

    @property
    def research_profile(self) -> str | None:
        """Explicit RESEARCH_AWS_PROFILE in effect for this source, if any."""
        return self._research_profile

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            if self._research_profile:
                session = _build_session(self._research_profile, self._region)
                # Eager credential resolution: a missing/misnamed/expired profile
                # fails HERE with an actionable error instead of surfacing as a
                # cryptic mid-read failure. (The standard-chain path stays lazy so
                # EC2 instance-role / normal chain behaviour is unchanged.)
                if session.get_credentials() is None:
                    raise ResearchDataSourceError(
                        f"RESEARCH_AWS_PROFILE='{self._research_profile}': the "
                        f"profile resolved but returned no credentials "
                        f"(bucket={self._bucket}, region={self._region}); for an "
                        f"SSO profile run: aws sso login --profile "
                        f"{self._research_profile}."
                    )
                self._client = session.client("s3", region_name=self._region)
            else:
                session = _build_session(None, self._region)
                self._client = session.client("s3", region_name=self._region)
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise ResearchDataSourceError(
                "boto3 is required for the Research Engine S3 data source"
            ) from exc
        except ResearchDataSourceError:
            raise
        except Exception as exc:
            raise self._diagnose(exc, operation="AWS session/client construction") from exc
        return self._client

    def _diagnose(self, exc: Exception, *, operation: str) -> ResearchDataSourceError:
        """Wrap an AWS failure in an actionable, secret-free ResearchDataSourceError."""
        return ResearchDataSourceError(
            _describe_aws_error(
                exc,
                profile=self._research_profile,
                bucket=self._bucket,
                region=self._region,
                operation=operation,
            )
        )

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

    def _list_prefixes(
        self,
        dataset: str,
        *,
        symbol: str | None,
        all_schemas: bool,
    ) -> list[str]:
        """Compute the narrowest S3 read prefixes for the request.

        The prefix is generated by the SAME central production contract the
        writers use (``canonical_s3_list_prefix``), so a writer and this loader
        can never diverge on path convention. Symbol pruning is applied only for
        symbol-scoped datasets (date-scoped/portfolio datasets omit symbol=).
        Date pruning is applied per-key after listing.

        ``all_schemas=True`` widens to every supported (current + legacy) schema
        — an explicit historical-query capability, never the default behaviour.
        """
        # Validate/normalise dataset via the contract (rejects retired/unknown).
        self._base_prefix(dataset)
        schemas = sorted(supported_schemas(dataset)) if all_schemas else [current_schema(dataset)]
        return [
            canonical_s3_list_prefix(dataset, symbol=symbol, schema=s)
            for s in schemas
        ]

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
                raise self._diagnose(
                    exc, operation=f"list_objects_v2 prefix='{prefix}'"
                ) from exc
            for obj in resp.get("Contents", []) or []:
                key = obj.get("Key", "")
                if key.endswith(".jsonl"):
                    modified = obj.get("LastModified")
                    self._listed_objects[key] = {
                        "identifier": key,
                        "etag": str(obj.get("ETag", "")).strip('"'),
                        "size": int(obj.get("Size", 0) or 0),
                        "last_modified": (
                            modified.isoformat() if hasattr(modified, "isoformat")
                            else str(modified or "")
                        ),
                    }
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

    def discover_dataset_objects(
        self,
        dataset: str,
        *,
        symbol: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> tuple[dict[str, Any], ...]:
        """Discover the exact canonical object set for one bounded dataset read."""
        seen: set[str] = set()
        for prefix in self._list_prefixes(
                dataset, symbol=symbol, all_schemas=False):
            for key in self._iter_keys(prefix):
                if key in seen or not self._in_range(key, start_date, end_date):
                    continue
                seen.add(key)
        return tuple(dict(self._listed_objects[key]) for key in sorted(seen))

    def head_bound_objects(
        self,
        dataset: str,
        objects: Sequence[Mapping[str, Any]],
        *,
        expected_schema_version: str,
        max_workers: int = 16,
    ) -> tuple[dict[str, Any], ...]:
        """Resolve exact current object versions without downloading bodies."""
        if expected_schema_version != current_schema(dataset):
            raise ResearchDataSourceError(
                f"SNAPSHOT_SCHEMA_MISMATCH:{dataset}:{expected_schema_version}"
                f"!={current_schema(dataset)}")
        prefixes = self._list_prefixes(dataset, symbol=None, all_schemas=False)
        listed = [dict(item) for item in objects]
        for item in listed:
            key = str(item.get("identifier") or "")
            if not key or not any(key.startswith(prefix) for prefix in prefixes):
                raise ResearchDataSourceError(
                    f"SNAPSHOT_OBJECT_OUTSIDE_CANONICAL_DATASET:{dataset}:{key}")

        client = self._get_client()

        def inspect(item: Mapping[str, Any]) -> dict[str, Any]:
            key = str(item["identifier"])
            try:
                response = client.head_object(Bucket=self._bucket, Key=key)
            except Exception as exc:
                raise self._diagnose(exc, operation=f"head_object key='{key}'") from exc
            modified = response.get("LastModified")
            return {
                **dict(item),
                "etag": str(response.get("ETag") or item.get("etag") or "").strip('"'),
                "size": int(response.get("ContentLength") or item.get("size") or 0),
                "last_modified": (
                    modified.isoformat() if hasattr(modified, "isoformat")
                    else str(modified or item.get("last_modified") or "")
                ),
                "version_id": response.get("VersionId"),
            }

        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(listed) or 1))) as pool:
            headed = tuple(pool.map(inspect, listed))
        for item in headed:
            self._headed_objects[(dataset, str(item["identifier"]))] = dict(item)
        return headed

    def read_bound_objects(
        self,
        dataset: str,
        objects: Sequence[Mapping[str, Any]],
        *,
        expected_schema_version: str,
        start_date: str | None = None,
        end_date: str | None = None,
        _verify_content: bool = True,
    ) -> list[dict[str, Any]]:
        """Read only explicitly bound canonical S3 objects and verify each digest.

        This method deliberately performs no listing and has no latest/live
        fallback. The caller supplies the complete object manifest captured at
        freeze time. Repeated calls fetch those exact keys again, so a missing or
        changed object fails closed instead of being replaced by newer data.
        """
        if expected_schema_version != current_schema(dataset):
            raise ResearchDataSourceError(
                f"SNAPSHOT_SCHEMA_MISMATCH:{dataset}:{expected_schema_version}"
                f"!={current_schema(dataset)}")
        prefixes = self._list_prefixes(
            dataset, symbol=None, all_schemas=False)
        keys: list[str] = []
        for item in objects:
            key = str(item.get("identifier") or "")
            if not key or not any(key.startswith(prefix) for prefix in prefixes):
                raise ResearchDataSourceError(
                    f"SNAPSHOT_OBJECT_OUTSIDE_CANONICAL_DATASET:{dataset}:{key}")
            if not self._in_range(key, start_date, end_date):
                raise ResearchDataSourceError(
                    f"SNAPSHOT_OBJECT_OUTSIDE_DATE_BOUNDS:{dataset}:{key}")
            keys.append(key)
        if len(set(keys)) != len(keys):
            raise ResearchDataSourceError(
                f"SNAPSHOT_DUPLICATE_OBJECT_KEY:{dataset}")

        records: list[dict[str, Any]] = []
        observed: list[dict[str, Any]] = []
        for item, key in zip(objects, keys):
            records.extend(self._read_object(
                dataset, key,
                version_id=(None if item.get("version_id") is None
                            else str(item.get("version_id"))),
                strict=True))
            actual = dict(self._read_objects[(dataset, key)])
            expected_digest = str(item.get("content_sha256") or "")
            if _verify_content:
                if not expected_digest or actual["content_sha256"] != expected_digest:
                    raise ResearchDataSourceError(
                        f"SNAPSHOT_OBJECT_DIGEST_MISMATCH:{dataset}:{key}")
                if int(item.get("row_count", -1)) != actual["row_count"]:
                    raise ResearchDataSourceError(
                        f"SNAPSHOT_OBJECT_ROW_COUNT_MISMATCH:{dataset}:{key}")
                if int(item.get("byte_size", -1)) != actual["byte_size"]:
                    raise ResearchDataSourceError(
                        f"SNAPSHOT_OBJECT_BYTE_SIZE_MISMATCH:{dataset}:{key}")
                for field_name in ("etag", "size", "last_modified"):
                    expected_value = item.get(field_name)
                    if expected_value not in (None, "", 0) \
                            and str(expected_value) != str(actual.get(field_name)):
                        raise ResearchDataSourceError(
                            f"SNAPSHOT_OBJECT_{field_name.upper()}_MISMATCH:{dataset}:{key}")
                expected_version = item.get("version_id")
                actual_version = actual.get("version_id")
                if expected_version is not None and str(expected_version) != str(actual_version):
                    raise ResearchDataSourceError(
                        f"SNAPSHOT_OBJECT_VERSION_MISMATCH:{dataset}:{key}")
            observed.append(actual)
        order_keys = _ORDER_KEYS.get(dataset, _DEFAULT_ORDER_KEYS)
        records.sort(key=lambda row: _order_value(row, order_keys))
        self._dataset_objects[dataset] = tuple(observed)
        return records

    def read_objects_for_freeze(
        self,
        dataset: str,
        objects: Sequence[Mapping[str, Any]],
        *,
        expected_schema_version: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        """Acquire exactly a just-discovered key set before its digest exists."""
        return self.read_bound_objects(
            dataset, objects,
            expected_schema_version=expected_schema_version,
            start_date=start_date, end_date=end_date,
            _verify_content=False,
        )

    # ─── object read + decode ─────────────────────────────────────────────────

    def _read_object(
        self, dataset: str, key: str, *, version_id: str | None = None,
        strict: bool = False,
    ) -> list[dict[str, Any]]:
        client = self._get_client()
        response: Mapping[str, Any] = {}
        out: list[dict[str, Any]] = []
        report = self._malformed.setdefault(dataset, MalformedReport(dataset=dataset))
        byte_size = 0
        content_hasher = hashlib.sha256()
        pending = b""

        def decode_line(raw_line: bytes) -> None:
            line = raw_line.decode("utf-8").strip()
            if not line:
                return
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                report.malformed_lines += 1
                if key not in report.keys_with_errors:
                    report.keys_with_errors.append(key)
                return
            if not isinstance(rec, dict):
                # A valid-JSON non-object row is never a legitimate
                # governed record. On the freeze path this MUST fail
                # closed rather than silently reduce the frozen count;
                # on the live path preserve the skip-and-report contract.
                if strict:
                    raise ResearchDataSourceError(
                        f"SNAPSHOT_RECORD_NOT_OBJECT:{dataset}:{key}")
                report.malformed_lines += 1
                if key not in report.keys_with_errors:
                    report.keys_with_errors.append(key)
                return
            if strict:
                # Freeze boundary only: enforce schema identity for the
                # dataset and its registered canonical profile (where one
                # exists). Never fabricate fields for unprofiled datasets.
                _assert_freeze_record_schema(dataset, rec)
            out.append(rec)

        try:
            request: dict[str, Any] = {"Bucket": self._bucket, "Key": key}
            if version_id is not None:
                request["VersionId"] = version_id
            resp = client.get_object(**request)
            response = resp
            stream = resp["Body"]
            while True:
                one_shot = False
                try:
                    chunk = stream.read(1024 * 1024)
                except TypeError:  # minimal test doubles may only expose read()
                    chunk = stream.read()
                    one_shot = True
                if not chunk:
                    break
                chunk_bytes = chunk.encode("utf-8") if isinstance(chunk, str) else bytes(chunk)
                byte_size += len(chunk_bytes)
                content_hasher.update(chunk_bytes)
                pending += chunk_bytes
                lines = pending.split(b"\n")
                pending = lines.pop()
                for raw_line in lines:
                    decode_line(raw_line.rstrip(b"\r"))
                if one_shot:
                    break
            if pending:
                decode_line(pending.rstrip(b"\r"))
        except MemoryError:
            raise
        except ResearchDataSourceError:
            # A freeze-boundary schema/profile rejection is already an
            # actionable, secret-free error; re-raise it unchanged instead of
            # masking it as a generic AWS get_object failure.
            raise
        except Exception as exc:
            raise self._diagnose(
                exc, operation=f"get_object key='{key}'"
            ) from exc
        listed = dict(self._listed_objects.get(key, {"identifier": key}))
        response_etag = str(response.get("ETag") or "").strip('"')
        response_last_modified = response.get("LastModified")
        response_last_modified_text = (
            response_last_modified.isoformat()
            if hasattr(response_last_modified, "isoformat")
            else str(response_last_modified or "")
        )
        self._read_objects[(dataset, key)] = {
            **listed,
            "etag": response_etag or listed.get("etag", ""),
            "size": int(response.get("ContentLength") or byte_size),
            "last_modified": response_last_modified_text or listed.get("last_modified", ""),
            "version_id": response.get("VersionId"),
            "content_sha256": content_hasher.hexdigest(),
            "byte_size": byte_size,
            "row_count": len(out),
        }
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

        object_records: list[tuple[str, dict[str, Any]]] = []
        seen_keys: set[str] = set()
        for prefix in self._list_prefixes(dataset, symbol=symbol, all_schemas=all_schemas):
            for key in self._iter_keys(prefix):
                if key in seen_keys:
                    continue
                if not self._in_range(key, start_date, end_date):
                    continue
                seen_keys.add(key)
                object_records.extend(
                    (key, record) for record in self._read_object(dataset, key)
                )

        legacy_counts: dict[str, int] = {}
        for key, record in object_records:
            if key.rsplit("/", 1)[-1] == "part-000.jsonl":
                fingerprint = json.dumps(
                    record, sort_keys=True, separators=(",", ":"),
                    ensure_ascii=True,
                )
                legacy_counts[fingerprint] = legacy_counts.get(fingerprint, 0) + 1
        outbox_counts: dict[str, int] = {}
        records: list[dict[str, Any]] = []
        for key, record in object_records:
            filename = key.rsplit("/", 1)[-1]
            if filename.startswith("part-outbox-") and filename.endswith(".jsonl"):
                fingerprint = json.dumps(
                    record, sort_keys=True, separators=(",", ":"),
                    ensure_ascii=True,
                )
                count = outbox_counts.get(fingerprint, 0)
                outbox_counts[fingerprint] = count + 1
                if count < legacy_counts.get(fingerprint, 0):
                    continue
            records.append(record)

        order_keys = _ORDER_KEYS.get(dataset, _DEFAULT_ORDER_KEYS)
        records.sort(key=lambda r: _order_value(r, order_keys))
        self._dataset_objects[dataset] = tuple(
            dict(self._read_objects.get(
                (dataset, key), self._listed_objects.get(key, {"identifier": key})))
            for key in sorted(seen_keys)
        )

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

    def object_metadata(self, dataset: str) -> tuple[dict[str, Any], ...]:
        """Objects consumed by the current cached dataset read, in key order."""
        return self._dataset_objects.get(dataset, ())

    def verified_object_metadata(
        self, dataset: str, key: str,
    ) -> dict[str, Any] | None:
        """Return identity already proven by GET or HEAD in this source run."""
        value = self._read_objects.get((dataset, key))
        if value is None:
            value = self._headed_objects.get((dataset, key))
        return None if value is None else dict(value)

    def clear_cache(self) -> None:
        """Drop the run-level cache (e.g. between independent research runs)."""
        self._cache.clear()
        self._dataset_objects.clear()
        self._listed_objects.clear()
        self._read_objects.clear()
        self._headed_objects.clear()


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
