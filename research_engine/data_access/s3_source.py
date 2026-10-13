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
import time
import logging
import os
import tempfile
import shutil
from pathlib import Path
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from threading import Lock
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from core.config import NEW_RUNTIME_S3_BUCKET
from core.canonical_profiles import has_profile, validate_record
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


@dataclass
class GuardCohortReport:
    """Explicit governed accounting for the historical guard-rejection cohort.

    Records are never dropped and never fabricated. This report makes the exact
    number of verified historical guard-rejection variants admitted under the
    narrow Stage A compatibility classification auditable, so they can be held
    separate from canonical decision-trace denominators, eligibility and
    research-question evidence.
    """
    dataset: str
    eligible_rows: int = 0
    eligible_objects: int = 0
    eligible_keys: list[str] = field(default_factory=list)


def _assert_freeze_record_schema(dataset: str, record: Any) -> None:
    """Enforce schema identity at the bound-object read boundary.

    This gate is used by bound-object reads, including frontier acquisition.
    The live ``read_dataset`` path keeps its skip-and-report behavior.

    Rule (never inventing fields, never weakening a production contract):
      * For a governed production dataset the record's ``schema_version`` must
        equal the contract's canonical schema for that dataset. This enforces
        schema IDENTITY for every one of the ten bound datasets.

    Registered profile rules are checked by ``read_objects_for_freeze`` only.
    Unprofiled datasets receive schema identity only.
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


# ─── Historical runtime_guard_rejection compatibility (Stage A) ──────────────
# A narrow, evidence-bounded historical compatibility classification for the
# freeze ingestion boundary ONLY. It exists solely so governed frontier
# construction can admit a specific, already-written legacy cohort without
# weakening canonical validation and without rewriting source evidence.
#
# Defect (established from immutable git provenance):
#   * commit 0b243d778 (2026-09-02) made core.decision_audit.persist_risk_rejection
#     S3-mirror risk-guard rejection records via core.decision_trace._write_s3,
#     i.e. under the canonical decision_trace_v1 key prefix.
#   * Those records carried record_role=runtime_guard_rejection but the legacy
#     producer never emitted the canonical entity_id / action identity fields.
#   * commit 5fe33a74a (2026-10-02) removed that S3 mirror; the record became
#     local-only because it "lacks decision_trace's governed entity_id".
# The defect window is therefore bounded by the deployment boundaries
# 2026-09-02 (inclusive) .. 2026-10-02 (inclusive). The upper bound is the fix
# commit date; a live read confirmed the cohort's last observed date partition
# is 2026-10-02 (192 rows across 43 objects, all lacking entity_id/action).
# Because commit 5fe33a74a made persist_risk_rejection LOCAL-ONLY (no S3 write
# at all), any runtime_guard_rejection record under the canonical S3 prefix —
# including one dated 2026-10-02 (written before that day's deploy) — is
# necessarily legacy. No legitimate post-fix record can appear on/after the fix.
_GUARD_COHORT_DATASET = "decision_trace"
_GUARD_COHORT_RECORD_ROLE = "runtime_guard_rejection"
_GUARD_COHORT_EVENT_TYPE = "RISK_REJECTION"
_GUARD_COHORT_REJECTION_TYPE = "RISK_GUARD"
# Deployment boundaries (both inclusive) of the defect window.
_GUARD_COHORT_START_DATE = "2026-09-02"
_GUARD_COHORT_END_DATE_INCLUSIVE = "2026-10-02"
# Legacy record fields that must be present and non-empty for a genuine
# persist_risk_rejection mirror. These are NEVER the canonical identity spine
# (entity_id / action), which the legacy producer omitted by defect.
_GUARD_COHORT_REQUIRED_LEGACY_FIELDS = (
    "schema_version",
    "symbol",
    "record_role",
    "event_type",
    "rejection_type",
    "guard",
    "reason",
    "should_trade",
    "cycle_id",
    "correlation_id",
    "timestamp_utc",
)
# A canonical decision trace always declares a distinct record role. Requiring
# that the canonical identity fields be ABSENT (not merely empty) prevents a
# spoofed/ambiguous record from being mis-binned as the historical cohort.
_GUARD_COHORT_FORBIDDEN_CANONICAL_FIELDS = ("entity_id", "action")
# Governed reason codes returned to the freeze boundary. A record is either a
# verified historical guard variant or an ordinary (canonical) record; anything
# else is rejected with one of these codes and NEVER silently accepted.
GUARD_COHORT_ELIGIBLE = "HISTORICAL_GUARD_REJECTION_VARIANT"
GUARD_COHORT_NOT_HISTORICAL = "ORDINARY_RECORD"
GUARD_COHORT_AMBIGUOUS = "HISTORICAL_GUARD_REJECTION_AMBIGUOUS"


def _object_date_partition(identifier: str) -> str | None:
    """Return the date= partition of an S3 object key, if present."""
    for part in str(identifier or "").split("/"):
        if part.startswith("date="):
            return part[5:]
    return None


def _within_guard_cohort_window(identifier: str) -> bool:
    """True only when the object's date partition is inside the defect window.

    Bounded by immutable deployment provenance. The window is closed:
    [2026-09-02, 2026-10-02]. This is a necessary (not sufficient) condition —
    the record body must ALSO satisfy the historical contract below.
    """
    date_part = _object_date_partition(identifier)
    if date_part is None:
        return False
    return (_GUARD_COHORT_START_DATE <= date_part
            <= _GUARD_COHORT_END_DATE_INCLUSIVE)


def _guard_candidate_record(record: Any) -> bool:
    """Cheap positive signal that a record CLAIMS the historical guard role.

    This only identifies a candidate; it does not establish eligibility. A
    candidate that fails the full contract is rejected, never accepted.
    """
    return (isinstance(record, dict)
            and record.get("record_role") == _GUARD_COHORT_RECORD_ROLE)


def classify_guard_cohort_record(
    dataset: str, record: Any, object_identifier: str,
) -> str:
    """Classify one freeze-boundary record as historical-guard variant or ordinary.

    Returns exactly one governed label:
      * ORDINARY_RECORD — not a guard candidate; canonical validation applies.
      * HISTORICAL_GUARD_REJECTION_VARIANT — a verified historical guard variant
        eligible for the narrowly scoped compatibility classification.
      * HISTORICAL_GUARD_REJECTION_AMBIGUOUS — the record CLAIMS the guard role
        but cannot be safely distinguished from a malformed canonical record;
        the caller MUST reject it (fail closed).

    This never fabricates canonical fields and never mutates the record. It is
    only ever consulted at the freeze ingestion boundary; the live read_dataset
    path is unaffected.
    """
    if dataset != _GUARD_COHORT_DATASET:
        return GUARD_COHORT_NOT_HISTORICAL
    if not _guard_candidate_record(record):
        return GUARD_COHORT_NOT_HISTORICAL

    # From here the record explicitly claims record_role=runtime_guard_rejection.
    # It is eligible ONLY if every immutable condition holds; otherwise it is an
    # ambiguous claim and must fail closed (a spoofed label is not ordinary).
    if not _within_guard_cohort_window(object_identifier):
        return GUARD_COHORT_AMBIGUOUS

    # The canonical identity fields must be ABSENT (the legacy defect omitted
    # them). Their presence means this is not the historical variant — treat as
    # ambiguous so canonical validation still governs, and never silently bin a
    # record that carries the guard label but real canonical identity.
    for field_name in _GUARD_COHORT_FORBIDDEN_CANONICAL_FIELDS:
        if field_name in record:
            return GUARD_COHORT_AMBIGUOUS

    # Semantic role consistency: the record must be a risk-guard RISK_REJECTION.
    if record.get("event_type") != _GUARD_COHORT_EVENT_TYPE:
        return GUARD_COHORT_AMBIGUOUS
    if record.get("rejection_type") != _GUARD_COHORT_REJECTION_TYPE:
        return GUARD_COHORT_AMBIGUOUS

    # All legacy provenance fields must be present and non-empty.
    for field_name in _GUARD_COHORT_REQUIRED_LEGACY_FIELDS:
        value = record.get(field_name)
        if value is None or value == "" or value == {} or value == []:
            return GUARD_COHORT_AMBIGUOUS

    # A risk-guard rejection is a no-trade decision by definition.
    if record.get("should_trade") is not False:
        return GUARD_COHORT_AMBIGUOUS

    # Schema identity must still hold exactly (never a different/absent schema).
    if record.get("schema_version") != current_schema(_GUARD_COHORT_DATASET):
        return GUARD_COHORT_AMBIGUOUS

    return GUARD_COHORT_ELIGIBLE



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
        verified_cache_directory: Path | str | None = None,
        verified_cache_max_bytes: int = 512 * 1024 * 1024,
        verified_cache_object_max_bytes: int = 64 * 1024 * 1024,
    ):
        self._verified_cache_directory = (Path(verified_cache_directory)
                                          if verified_cache_directory is not None else None)
        self._verified_cache_max_bytes = max(0, int(verified_cache_max_bytes))
        self._verified_cache_object_max_bytes = max(0, int(verified_cache_object_max_bytes))
        self._verified_cache_bytes = None
        self._verified_cache_index: dict[str, Path] | None = None
        self._verified_cache_identifiers: set[tuple[str, str]] = set()
        self._head_counter_lock = Lock()
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
        # Historical guard-rejection accounting, idempotent per (dataset, key):
        # dataset -> {source object key -> verified eligible row count}. Keyed by
        # object so repeated/incremental reads of the same verified object never
        # double-count, and the per-key cohort window can be re-proven on demand.
        self._guard_cohort: dict[str, dict[str, int]] = {}
        self._listed_objects: dict[str, dict[str, Any]] = {}
        self._dataset_objects: dict[str, tuple[dict[str, Any], ...]] = {}
        self._read_objects: dict[tuple[str, str], dict[str, Any]] = {}
        self._headed_objects: dict[tuple[str, str], dict[str, Any]] = {}
        self.performance = {"list_calls": 0, "head_calls": 0, "head_retries": 0,
                            "get_calls": 0,
                            "head_seconds_inclusive": 0.0,
                            "get_open_seconds_inclusive": 0.0,
                            "cache_index_seconds_inclusive": 0.0,
                            "cache_index_entries": 0,
                            "cache_body_verify_seconds_inclusive": 0.0,
                            "body_bytes": 0, "remote_body_bytes": 0, "hash_seconds": 0.0, "decode_seconds": 0.0,
                            "body_load_seconds_inclusive": 0.0, "verified_cache_hits": 0,
                            "verified_cache_misses": 0, "verified_cache_writes": 0,
                            "verified_cache_capacity_skips": 0,
                            "verified_cache_used_bytes": 0,
                            "verified_cache_capacity_bytes": self._verified_cache_max_bytes,
                            "verified_cache_bytes": 0}

    def performance_snapshot(self) -> dict[str, int | float]:
        """Read concurrent HEAD counters with the rest of the source metrics."""
        with self._head_counter_lock:
            return dict(self.performance)

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
            from botocore.config import Config

            # A shared client serves the 16-worker current-version HEAD batch.
            # One bounded attempt fails closed; a later checkpointed run can
            # retry without accepting an unverified object as current.
            config = Config(connect_timeout=3, read_timeout=5,
                            retries={"total_max_attempts": 1},
                            max_pool_connections=16)
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
                self._client = session.client(
                    "s3", region_name=self._region, config=config)
            else:
                session = _build_session(None, self._region)
                self._client = session.client(
                    "s3", region_name=self._region, config=config)
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
                self.performance["list_calls"] += 1
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
        progress_callback: Any | None = None,
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
        if progress_callback is not None:
            try:
                progress_callback({"stage": "client_ready", "queued": 0,
                                   "completed": 0, "successful": 0,
                                   "failed": 0, "retried": 0,
                                   "total": len(listed)})
            except Exception:
                pass
        last_report = 0.0

        def retry_count() -> int:
            with self._head_counter_lock:
                return self.performance["head_retries"]

        def report(queued: int, completed: int, successful: int,
                   failed: int, retried: int) -> None:
            nonlocal last_report
            now = time.perf_counter()
            if (completed and not failed and completed != len(listed)
                    and completed % 25 and now - last_report < 1.0):
                return
            last_report = now
            if progress_callback is not None:
                try:
                    progress_callback({"queued": queued, "completed": completed,
                                       "successful": successful, "failed": failed,
                                       "retried": retried, "total": len(listed)})
                except Exception:
                    pass

        def inspect(item: Mapping[str, Any]) -> dict[str, Any]:
            key = str(item["identifier"])
            started = time.perf_counter()
            try:
                with self._head_counter_lock:
                    self.performance["head_calls"] += 1
                response = client.head_object(Bucket=self._bucket, Key=key)
            except Exception as exc:
                retries = int((getattr(exc, "response", None) or {}).get(
                    "ResponseMetadata", {}).get("RetryAttempts", 0) or 0)
                with self._head_counter_lock:
                    self.performance["head_retries"] += retries
                raise self._diagnose(exc, operation=f"head_object key='{key}'") from exc
            else:
                retries = int(response.get("ResponseMetadata", {}).get(
                    "RetryAttempts", 0) or 0)
                with self._head_counter_lock:
                    self.performance["head_retries"] += retries
            finally:
                elapsed = time.perf_counter() - started
                with self._head_counter_lock:
                    self.performance["head_seconds_inclusive"] += elapsed
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

        workers = max(1, min(max_workers, len(listed) or 1))
        headed_slots: list[dict[str, Any] | None] = [None] * len(listed)
        queued = completed = successful = failed = 0
        retry_start = retry_count()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            pending = {}
            next_index = 0

            def fill() -> None:
                nonlocal next_index, queued
                while next_index < len(listed) and len(pending) < 2 * workers:
                    pending[pool.submit(inspect, listed[next_index])] = next_index
                    next_index += 1
                    queued += 1
                report(queued, completed, successful, failed,
                       retry_count() - retry_start)

            fill()
            while pending:
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                error = None
                for future in done:
                    index = pending.pop(future)
                    completed += 1
                    try:
                        headed_slots[index] = future.result()
                        successful += 1
                    except Exception as exc:
                        failed += 1
                        error = exc
                report(queued, completed, successful, failed,
                       retry_count() - retry_start)
                if error is not None:
                    for future in pending:
                        future.cancel()
                    raise error
                fill()
        headed = tuple(item for item in headed_slots if item is not None)
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
        _cache_discovery: bool = False,
        _fresh_authority: bool = False,
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
                strict=True,
                expected_object=(
                    item if (_verify_content or _cache_discovery) else None),
                fresh_authority=_fresh_authority))
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
        _fresh_authority: bool = False,
    ) -> list[dict[str, Any]]:
        """Acquire exactly a just-discovered key set before its digest exists.

        Each record is classified at the freeze ingestion boundary against the
        immutable source object key it came from. Canonical ``decision_trace_v1``
        records are validated against their registered profile exactly as
        before. The narrow Stage A historical compatibility classification may
        admit a *verified* legacy ``runtime_guard_rejection`` variant (which the
        historical producer wrote under the canonical prefix without the
        canonical ``entity_id``/``action`` spine); such rows are counted in an
        explicit :class:`GuardCohortReport` and are NEVER allowed to satisfy a
        canonical profile check. Ambiguous or spoofed guard labels fail closed
        with a governed reason code. No canonical field is fabricated and no
        source byte is rewritten.
        """
        profiled = has_profile(dataset)
        records: list[dict[str, Any]] = []
        observed: list[dict[str, Any]] = []
        guard_by_key = self._guard_cohort.setdefault(dataset, {})
        for item in objects:
            key = str(item.get("identifier") or "")
            partition_symbol: str | None = None
            if dataset == "trade_truth":
                prefix = canonical_s3_list_prefix(dataset)
                parts = key[len(prefix):].split("/") if key.startswith(prefix) else []
                if (len(parts) != 3 or not parts[0].startswith("symbol=")
                        or not parts[0][len("symbol="):] or not parts[1].startswith("date=")
                        or not parts[1][len("date="):] or not parts[2]):
                    raise ResearchDataSourceError(
                        f"SNAPSHOT_TRADE_TRUTH_SYMBOL_PARTITION_INVALID:{key}")
                partition_symbol = parts[0][len("symbol="):]
            object_records = self.read_bound_objects(
                dataset, (item,),
                expected_schema_version=expected_schema_version,
                start_date=start_date, end_date=end_date,
                _verify_content=False,
                _cache_discovery=True,
                _fresh_authority=_fresh_authority,
            )
            observed.append(dict(self._read_objects[(dataset, key)]))
            eligible_in_object = 0
            if profiled:
                for record in object_records:
                    classification = classify_guard_cohort_record(
                        dataset, record, key)
                    if classification == GUARD_COHORT_AMBIGUOUS:
                        # The record claims the historical guard role but cannot
                        # be safely distinguished from a malformed canonical
                        # record. Fail closed; never accept, never drop.
                        raise ResearchDataSourceError(
                            "HISTORICAL_GUARD_REJECTION_UNVERIFIED:"
                            + dataset + ":" + key)
                    if classification == GUARD_COHORT_ELIGIBLE:
                        # Verified historical variant: admitted under the narrow
                        # compatibility classification and accounted explicitly.
                        # It does NOT satisfy the canonical profile. Its exact
                        # original bytes are preserved in ``records`` unchanged.
                        eligible_in_object += 1
                        continue
                    valid, violations = validate_record(dataset, record)
                    if not valid:
                        raise ResearchDataSourceError(
                            f"SNAPSHOT_RECORD_PROFILE_INVALID:{dataset}:"
                            + ";".join(violations))
                    if (dataset == "trade_truth"
                            and record["identity"]["symbol"] != partition_symbol):
                        raise ResearchDataSourceError(
                            f"SNAPSHOT_TRADE_TRUTH_SYMBOL_PARTITION_MISMATCH:{key}")
            # Idempotent per verified object: re-reading the same key assigns the
            # same count, so incremental and repeated freezes never inflate the
            # cohort. Zero-eligible objects are simply not recorded.
            if eligible_in_object:
                guard_by_key[key] = eligible_in_object
            else:
                guard_by_key.pop(key, None)
            records.extend(object_records)
        order_keys = _ORDER_KEYS.get(dataset, _DEFAULT_ORDER_KEYS)
        records.sort(key=lambda row: _order_value(row, order_keys))
        self._dataset_objects[dataset] = tuple(observed)
        return records


    # ─── object read + decode ─────────────────────────────────────────────────

    def _read_object(
        self, dataset: str, key: str, *, version_id: str | None = None,
        strict: bool = False, expected_object: Mapping[str, Any] | None = None,
        fresh_authority: bool = False,
    ) -> list[dict[str, Any]]:
        response: Mapping[str, Any] = {}
        out: list[dict[str, Any]] = []
        report = self._malformed.setdefault(dataset, MalformedReport(dataset=dataset))
        load_started = time.perf_counter()
        decode_seconds = 0.0
        hash_seconds = 0.0
        byte_size = 0
        content_hasher = hashlib.sha256()
        pending = b""

        def decode_line(raw_line: bytes) -> None:
            nonlocal decode_seconds
            line = raw_line.decode("utf-8").strip()
            if not line:
                return
            decode_started = time.perf_counter()
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                report.malformed_lines += 1
                if key not in report.keys_with_errors:
                    report.keys_with_errors.append(key)
                return
            finally:
                decode_seconds += time.perf_counter() - decode_started
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
                # Bound-object reads enforce schema identity; live reads do not.
                _assert_freeze_record_schema(dataset, rec)
            out.append(rec)

        stream = None
        capture = None
        capture_path = None
        cached = self._open_verified_body(dataset, expected_object) if strict else None
        if cached is None and strict and expected_object is not None:
            # Bounded cold-start discovery: reuse an existing verified entry
            # found from fresh S3 authority when no content digest is known yet.
            cached = self._open_discovered_verified_body(
                dataset, expected_object, fresh_authority=fresh_authority)
        capture_limit = 0
        if strict and cached is None and self._verified_cache_directory is not None:
            self._verified_cache_directory.mkdir(parents=True, exist_ok=True)
            if self._verified_cache_bytes is None:
                self._verified_cache_bytes = sum(p.stat().st_size for p in
                    self._verified_cache_directory.iterdir() if p.is_file())
            self.performance["verified_cache_used_bytes"] = self._verified_cache_bytes
            # Leave room for spool + atomic entry + bounded header during publish.
            capture_limit = min(self._verified_cache_object_max_bytes,
                max(0, (self._verified_cache_max_bytes - self._verified_cache_bytes - 65536) // 2))
            if not capture_limit:
                self.performance["verified_cache_capacity_skips"] += 1
        if capture_limit:
            capture = tempfile.NamedTemporaryFile(mode="w+b", suffix=".partial",
                prefix="body-", dir=self._verified_cache_directory, delete=False)
            capture_path = Path(capture.name)
        try:
            request: dict[str, Any] = {"Bucket": self._bucket, "Key": key}
            if version_id is not None:
                request["VersionId"] = version_id
            if cached is None:
                self.performance["get_calls"] += 1
                get_started = time.perf_counter()
                try:
                    resp = self._get_client().get_object(**request)
                finally:
                    self.performance["get_open_seconds_inclusive"] += time.perf_counter() - get_started
                response = resp
                stream = resp["Body"]
            else:
                response, stream = cached
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
                if capture is not None:
                    if byte_size <= capture_limit:
                        capture.write(chunk_bytes)
                    else:
                        capture.close()
                        capture_path.unlink(missing_ok=True)
                        capture = None
                        self.performance["verified_cache_capacity_skips"] += 1
                hash_started = time.perf_counter()
                content_hasher.update(chunk_bytes)
                hash_seconds += time.perf_counter() - hash_started
                pending += chunk_bytes
                lines = pending.split(b"\n")
                pending = lines.pop()
                for raw_line in lines:
                    decode_line(raw_line.rstrip(b"\r"))
                if one_shot:
                    break
            if pending:
                decode_line(pending.rstrip(b"\r"))
        except BaseException as exc:
            if capture is not None:
                capture.close()
            if capture_path is not None:
                capture_path.unlink(missing_ok=True)
            if isinstance(exc, Exception) and not isinstance(exc, (ResearchDataSourceError, MemoryError)):
                raise self._diagnose(exc, operation=f"get_object key='{key}'") from exc
            raise
        finally:
            self.performance["body_bytes"] += byte_size
            if cached is None:
                self.performance["remote_body_bytes"] += byte_size
            self.performance["hash_seconds"] += hash_seconds
            self.performance["decode_seconds"] += decode_seconds
            self.performance["body_load_seconds_inclusive"] += time.perf_counter() - load_started
            if stream is not None and hasattr(stream, "close"):
                try:
                    stream.close()
                except Exception:
                    pass
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
        if capture is not None:
            try:
                if not report.malformed_lines:
                    self._save_verified_body(dataset, self._read_objects[(dataset, key)], capture)
            finally:
                capture.close()
                capture_path.unlink(missing_ok=True)
        return out

    # ─── public API ─────────────────────────────────────────────────────────

    def _verified_body_identity(self, dataset, metadata):
        # Only immutable version IDs are reusable. S3 null versions are mutable.
        if not metadata or metadata.get("version_id") in (None, "", "null"):
            return None
        fields = ("identifier", "version_id", "etag", "size", "last_modified",
                  "content_sha256", "byte_size", "row_count")
        if not metadata.get("content_sha256") or any(metadata.get(k) is None for k in fields):
            return None
        return {"cache_schema": "verified_s3_body_v1", "bucket": self.bucket,
                "region": self._region, "profile": self.research_profile,
                "dataset": dataset, "schema": current_schema(dataset),
                "object": {k: metadata[k] for k in fields}}

    def _verified_body_path(self, identity):
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True,
            separators=(",", ":")).encode()).hexdigest()
        return self._verified_cache_directory / (digest + ".cache")

    def _open_verified_body(self, dataset, metadata):
        identity = self._verified_body_identity(dataset, metadata)
        if self._verified_cache_directory is None or identity is None:
            return None
        path = self._verified_body_path(identity)
        if not path.exists():
            self.performance["verified_cache_misses"] += 1
            return None
        handle = None
        try:
            handle = path.open("rb")
            header_line = handle.readline(65537)
            if len(header_line) > 65536:
                raise ValueError("header too large")
            header = json.loads(header_line)
            if header != identity or path.stat().st_size - len(header_line) != int(metadata["byte_size"]):
                raise ValueError("identity/size mismatch")
            # Cached bytes do not grant authority or prove continued membership.
            # Require a current authorized HEAD even for an immutable version.
            current = self.head_bound_objects(dataset, [metadata],
                expected_schema_version=current_schema(dataset), max_workers=1)[0]
            for field in ("identifier", "version_id", "etag", "size", "last_modified"):
                if current.get(field) != metadata.get(field):
                    raise ValueError("fresh object authority mismatch")
            self.performance["verified_cache_hits"] += 1
            self.performance["verified_cache_bytes"] += int(metadata["byte_size"])
            return {"ETag": metadata["etag"], "ContentLength": metadata["size"],
                    "LastModified": metadata["last_modified"],
                    "VersionId": metadata["version_id"]}, handle
        except Exception as exc:
            if handle is not None:
                handle.close()
            raise ResearchDataSourceError("VERIFIED_BODY_CACHE_INVALID:" + str(path.name)) from exc

    def _discovery_key_from_identity(self, identity):
        """Authority-only key derived from a full cache identity.

        Uses only the immutable/authoritative object fields — never the content
        digest or row count — so an existing entry can be found from fresh S3
        authority (identifier + immutable version + etag/size/last-modified)
        without a prior read.
        """
        if not isinstance(identity, Mapping):
            return None
        obj = identity.get("object")
        if not isinstance(obj, Mapping):
            return None
        fields = ("identifier", "version_id", "etag", "size", "last_modified")
        if obj.get("version_id") in (None, "", "null"):
            return None
        if any(obj.get(field) in (None, "") for field in fields):
            return None
        return hashlib.sha256(json.dumps({
            "cache_schema": identity.get("cache_schema"),
            "bucket": identity.get("bucket"), "region": identity.get("region"),
            "profile": identity.get("profile"), "dataset": identity.get("dataset"),
            "schema": identity.get("schema"),
            "object": {field: obj[field] for field in fields},
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def _verified_discovery_key(self, dataset, metadata):
        """Authority-only key for fresh metadata that has no content digest."""
        if not metadata or metadata.get("version_id") in (None, "", "null"):
            return None
        identity = {"cache_schema": "verified_s3_body_v1", "bucket": self.bucket,
                    "region": self._region, "profile": self.research_profile,
                    "dataset": dataset, "schema": current_schema(dataset),
                    "object": {field: metadata.get(field) for field in (
                        "identifier", "version_id", "etag", "size", "last_modified")}}
        return self._discovery_key_from_identity(identity)

    def _ensure_verified_cache_index(self) -> dict[str, Path]:
        """Bounded one-time scan of existing entries, keyed by authority only.

        Reads only each entry's header (never a whole body) and never deletes or
        rewrites anything. Malformed entries are simply not indexed — the normal
        read path still fails closed if such an entry is ever selected by exact
        identity.
        """
        if self._verified_cache_index is not None:
            return self._verified_cache_index
        started = time.perf_counter()
        index: dict[str, Path] = {}
        identifiers: set[tuple[str, str]] = set()
        used_bytes = 0
        directory = self._verified_cache_directory
        if directory is not None and directory.exists():
            for path in sorted(directory.iterdir()):
                if not path.is_file():
                    continue
                used_bytes += path.stat().st_size
                if path.suffix != ".cache":
                    continue
                try:
                    with path.open("rb") as handle:
                        header_line = handle.readline(65537)
                        if len(header_line) > 65536:
                            continue
                        identity = json.loads(header_line)
                except Exception:
                    continue
                key = self._discovery_key_from_identity(identity)
                if key is not None and key not in index:
                    index[key] = path
                    identifiers.add((str(identity["dataset"]),
                                     str(identity["object"]["identifier"])))
        self._verified_cache_index = index
        self._verified_cache_identifiers = identifiers
        if self._verified_cache_bytes is None:
            self._verified_cache_bytes = used_bytes
        self.performance["verified_cache_used_bytes"] = self._verified_cache_bytes
        self.performance["cache_index_entries"] = len(index)
        self.performance["cache_index_seconds_inclusive"] += time.perf_counter() - started
        return index

    def cached_authority_candidates(
        self, dataset: str, objects: Sequence[Mapping[str, Any]],
    ) -> tuple[dict[str, Any], ...]:
        """Select only listed objects that may have a verified cached body."""
        self._ensure_verified_cache_index()
        return tuple(dict(item) for item in objects
                     if (dataset, str(item.get("identifier") or ""))
                     in self._verified_cache_identifiers)

    def _open_discovered_verified_body(
        self, dataset, metadata, *, fresh_authority=False,
    ):
        """Reuse an existing verified entry found from fresh S3 authority.

        Returns ``(response, handle)`` positioned at the start of the cached
        body, or ``None`` when no entry exists (the caller then falls back to a
        normal S3 GET). A located-but-invalid entry fails closed rather than
        silently serving unverified bytes.
        """
        if self._verified_cache_directory is None or not metadata:
            return None
        if self._verified_body_identity(dataset, metadata) is not None:
            return None  # exact-identity path already handles this
        headed = self._headed_objects.get((dataset, str(metadata.get("identifier") or "")))
        authority_checked = bool(
            fresh_authority and headed is not None
            and all(headed.get(field) == metadata.get(field) for field in
                    ("identifier", "version_id", "etag", "size", "last_modified")))
        if metadata.get("version_id") in (None, "", "null"):
            # Cold-start discovery requires immutable version authority; resolve
            # it with one HEAD (no body download) before looking in the cache.
            metadata = self.head_bound_objects(
                dataset, [metadata], expected_schema_version=current_schema(dataset),
                max_workers=1)[0]
            authority_checked = True
        key = self._verified_discovery_key(dataset, metadata)
        if key is None:
            return None
        path = self._ensure_verified_cache_index().get(key)
        if path is None:
            self.performance["verified_cache_misses"] += 1
            return None
        handle = None
        try:
            handle = path.open("rb")
            header_line = handle.readline(65537)
            if len(header_line) > 65536:
                raise ValueError("header too large")
            header = json.loads(header_line)
            if self._discovery_key_from_identity(header) != key:
                raise ValueError("discovery identity mismatch")
            if path.stat().st_size - len(header_line) != int(header["object"]["byte_size"]):
                raise ValueError("size mismatch")
            # Re-verify the cached body against its recorded content digest.
            hasher = hashlib.sha256()
            verify_started = time.perf_counter()
            try:
                while True:
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        break
                    hasher.update(chunk)
            finally:
                self.performance["cache_body_verify_seconds_inclusive"] += (
                    time.perf_counter() - verify_started)
            if hasher.hexdigest() != str(header["object"]["content_sha256"]):
                raise ValueError("cached content digest mismatch")
            # Fresh authorized HEAD: cached bytes never grant authority.
            current = metadata if authority_checked else self.head_bound_objects(
                dataset, [header["object"]],
                expected_schema_version=current_schema(dataset), max_workers=1)[0]
            for field in ("identifier", "version_id", "etag", "size", "last_modified"):
                if current.get(field) != header["object"].get(field):
                    raise ValueError("fresh object authority mismatch")
            handle.seek(len(header_line))
            self.performance["verified_cache_hits"] += 1
            self.performance["verified_cache_bytes"] += int(header["object"]["byte_size"])
            return {"ETag": header["object"]["etag"],
                    "ContentLength": header["object"]["size"],
                    "LastModified": header["object"]["last_modified"],
                    "VersionId": header["object"]["version_id"]}, handle
        except Exception as exc:
            if handle is not None:
                handle.close()
            raise ResearchDataSourceError(
                "VERIFIED_BODY_CACHE_INVALID:" + str(path.name)) from exc

    def _save_verified_body(self, dataset, metadata, capture):
        identity = self._verified_body_identity(dataset, metadata)
        if identity is None:
            return
        path = self._verified_body_path(identity)
        if path.exists():
            return
        if self._verified_cache_bytes is None:
            # Include interrupted temporary files in the quota. Never delete history
            # or scan outside this explicitly selected disposable cache directory.
            self._verified_cache_bytes = sum(p.stat().st_size for p in
                self._verified_cache_directory.iterdir() if p.is_file())
            self.performance["verified_cache_used_bytes"] = self._verified_cache_bytes
        header = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        size = len(header) + int(metadata["byte_size"])
        if self._verified_cache_bytes + size > self._verified_cache_max_bytes:
            self.performance["verified_cache_capacity_skips"] += 1
            return
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", suffix=".partial", prefix="entry-",
                    dir=self._verified_cache_directory, delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(header)
                capture.seek(0)
                shutil.copyfileobj(capture, handle, 1024 * 1024)
                handle.flush()
                os.fsync(handle.fileno())
            # Publish atomically without replacing an existing immutable identity.
            # A competing writer may have created the same path since the check.
            try:
                os.link(temporary, path)
            except FileExistsError:
                return
            self._verified_cache_bytes += size
            self.performance["verified_cache_writes"] += 1
            self.performance["verified_cache_used_bytes"] = self._verified_cache_bytes
            if self._verified_cache_index is not None:
                discovery_key = self._discovery_key_from_identity(identity)
                if discovery_key is not None and discovery_key not in self._verified_cache_index:
                    self._verified_cache_index[discovery_key] = path
                    self._verified_cache_identifiers.add(
                        (dataset, str(metadata["identifier"])))
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

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

    def guard_cohort_report(self, dataset: str) -> GuardCohortReport | None:
        """Return the historical guard-rejection accounting for a dataset.

        Only populated on freeze-boundary reads that admitted verified
        historical guard-rejection variants. ``None`` means no such variant was
        seen for the dataset in this run — never a silent drop. The returned
        report is deterministic: rows are the sum of per-object eligible counts
        and keys are sorted, so repeated and incremental freezes are stable.
        """
        by_key = self._guard_cohort.get(dataset)
        if not by_key:
            return None
        eligible_keys = sorted(key for key, count in by_key.items() if count)
        return GuardCohortReport(
            dataset=dataset,
            eligible_rows=sum(by_key.values()),
            eligible_objects=len(eligible_keys),
            eligible_keys=eligible_keys,
        )

    def guard_cohort_count(self, dataset: str, key: str) -> int:
        """Return the verified historical row count for one source object."""
        return self._guard_cohort.get(dataset, {}).get(key, 0)

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
        self._guard_cohort.clear()


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
