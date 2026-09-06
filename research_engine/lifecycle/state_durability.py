"""
Research-State Durability — durable S3 checkpointing of lifecycle state.

Authority model (one state authority, one durability authority):

    RUNTIME AUTHORITY
        The existing local atomic lifecycle files (investigation registry,
        candidate registry, finding triggers, cycle state, Gap-6 snapshots,
        governance decisions, knowledge map) remain the ACTIVE working state.
        All research mutations keep their current local semantics unchanged.

    DURABLE AUTHORITY
        After research actions complete, a CHECKPOINT of the lifecycle files
        is mirrored to S3 under research_state/ (canonical research bucket).
        A checkpoint is an atomic generation: artifacts + a manifest that is
        the completion marker. The latest_success pointer is advanced LAST
        and only ever references a COMPLETE manifest. S3 is never an
        independently mutated competing registry.

    RECOVERY
        If required local lifecycle state is MISSING (fresh/rebuilt VM), the
        latest complete checkpoint is restored before research actions. If
        local state exists, it stays authoritative (S3 is never merged
        field-by-field); a checkpoint simply records the newer generation.

    CANONICAL PRODUCTION EVIDENCE (trade_truth, decision_trace,
    shadow_runtime, ...) is a different ownership class entirely — it is
    already durable in S3 and is NEVER copied into or restored from
    research_state checkpoints.

Checkpoint layout (V1 contract):

    research_state/checkpoints/<checkpoint_id>/artifacts/<relative path>
    research_state/checkpoints/<checkpoint_id>/manifest.json   (completion marker)
    research_state/latest_success.json                          (pointer, advanced last)

Manifest: checkpoint_id, created_at, contract_version "V1", generation,
previous_checkpoint_id, cycle_id/dataset_fingerprint (when available),
artifacts [{path, sha256, size}], status "complete".

Excluded by construction: lock/PID/temp files, scheduled-run logs, derived
reports/evaluations/evidence-cycle logs (reconstructable), raw production
evidence, credentials/secrets.

Credential model (Gap 3 preserved): local development may set
RESEARCH_AWS_PROFILE; when unset the standard boto3 chain applies (EC2 IAM
instance role). No hardcoded profile/account/keys.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_S3_PREFIX = "research_state"
_CONTRACT_VERSION = "V1"
_MAX_FALLBACK_DEPTH = 5

# ─── Durable-state allowlist (Phase-1 inventory classification) ──────────────
# Every entry is a REQUIRED-after-VM-loss lifecycle artifact (classification A),
# included only when it exists. Anything not listed here is excluded by
# construction; the exclusion guard additionally rejects locks/temp/secrets.
CHECKPOINT_ARTIFACTS: tuple[str, ...] = (
    "logs/research_lifecycle/registry.json",                 # hypotheses (A)
    "logs/research_lifecycle/finding_triggers.json",         # triggers + dedup identity (A)
    "logs/research_lifecycle/cycle_state.json",              # cycle runner state (A)
    "logs/research_lifecycle/governance_decisions.jsonl",    # human governance decisions (A)
    "logs/research_lifecycle/experiment_registry.json",      # experiment catalogue state (A)
    "logs/research_lifecycle/audit_log.jsonl",               # append-only audit history (C, retained)
    "logs/research_lifecycle/cycles/latest_success.json",    # last-successful-cycle pointer (A)
    "data/research/candidates/candidates.jsonl",             # candidate registry + validation history (A)
    "analysis/summaries/research_knowledge.json",            # knowledge map (dedup depends on it) (A)
)

# Glob-style additions: Gap-6 weekly snapshots (small, required for comparison).
_SNAPSHOT_GLOB = "logs/research_lifecycle/cycles/*_snapshot.json"

_EXCLUDED_NAME_PARTS = (".lock", ".tmp", ".pem", ".env", ".aws", "scheduled_runs.log")


class DurabilityError(RuntimeError):
    """Research-state durability failure (loud — never silently swallowed)."""


@dataclass
class DurabilityResult:
    """Observable durability outcome for one checkpoint/restore attempt."""
    status: str = ""            # durable | checkpoint_failed | recovered | skipped
    checkpoint_id: str = ""
    generation: int = 0
    artifact_count: int = 0
    manifest_key: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_excluded(rel_path: str) -> bool:
    """Defense-in-depth exclusion guard for anything that must not be
    checkpointed (locks, temps, secrets). The allowlist already excludes them."""
    lowered = rel_path.lower()
    return any(part in lowered for part in _EXCLUDED_NAME_PARTS)


def _collect_local_artifacts() -> dict[str, bytes]:
    """Read the durable-state allowlist from local disk (present files only)."""
    artifacts: dict[str, bytes] = {}
    root = Path(".")
    candidates: list[str] = list(CHECKPOINT_ARTIFACTS)
    for snap in sorted(root.glob(_SNAPSHOT_GLOB)):
        rel = snap.as_posix()
        if rel not in candidates:
            candidates.append(rel)
    for rel in candidates:
        if _is_excluded(rel):
            continue
        path = root / rel
        if path.is_file():
            artifacts[rel] = path.read_bytes()
    return artifacts


def _local_state_present() -> bool:
    """True when any required durable lifecycle artifact exists locally."""
    required_now = ("logs/research_lifecycle/registry.json",
                    "data/research/candidates/candidates.jsonl",
                    "logs/research_lifecycle/finding_triggers.json")
    return any((Path(".") / rel).exists() for rel in required_now)


class ResearchStateDurability:
    """S3 checkpoint/restore for the Research Engine lifecycle state."""

    def __init__(self, *, bucket: str | None = None, client: Any = None,
                 prefix: str = _S3_PREFIX, profile: str | None = None):
        self._prefix = prefix.strip("/")
        self._bucket = bucket
        self._client = client
        self._profile = profile

    # ─── S3 plumbing ──────────────────────────────────────────────────────

    def _get_client(self):
        if self._client is None:
            from core.config import RESEARCH_AWS_PROFILE
            profile = self._profile if self._profile is not None else RESEARCH_AWS_PROFILE
            from research_engine.data_access.s3_source import _build_session
            session = _build_session(profile or None, "eu-west-2")
            self._client = session.client("s3")
        return self._client

    def _get_bucket(self) -> str:
        if self._bucket:
            return self._bucket
        from core.config import NEW_RUNTIME_S3_BUCKET
        return NEW_RUNTIME_S3_BUCKET

    def _put(self, key: str, body: bytes) -> None:
        self._get_client().put_object(Bucket=self._get_bucket(), Key=key, Body=body)

    def _get(self, key: str) -> bytes:
        resp = self._get_client().get_object(Bucket=self._get_bucket(), Key=key)
        return resp["Body"].read()

    def _key(self, *parts: str) -> str:
        return "/".join((self._prefix, *parts))

    @staticmethod
    def _is_no_such_key(exc: Exception) -> bool:
        """True only for a genuine missing-key response (never other errors)."""
        code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
        return code in ("NoSuchKey", "NotFound", "404")

    @staticmethod
    def _client_exceptions():
        try:
            from botocore.exceptions import BotoCoreError, ClientError
            return (BotoCoreError, ClientError)
        except ImportError:  # pragma: no cover
            return (OSError,)

    # ─── CHECKPOINT ───────────────────────────────────────────────────────

    def checkpoint(self, *, cycle_id: str = "", dataset_fingerprint: str = "") -> DurabilityResult:
        """
        Create a durable checkpoint of the current local lifecycle state.

        Sequence: upload artifacts → upload manifest (completion marker) →
        advance the latest pointer LAST. Any failure raises DurabilityError
        and leaves the previous checkpoint authoritative.
        """
        artifacts = _collect_local_artifacts()
        if not artifacts:
            return DurabilityResult(status="skipped",
                                    error="no durable lifecycle state present locally")

        checkpoint_id = ("ckpt-" +
                         datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") +
                         "-" + uuid.uuid4().hex[:6])

        # previous pointer → generation chain
        generation = 1
        previous_id = ""
        try:
            prev = json.loads(self._get(self._key("latest_success.json")))
            generation = int(prev.get("generation", 0)) + 1
            previous_id = prev.get("checkpoint_id", "")
        except self._client_exceptions() as exc:
            if self._is_no_such_key(exc):
                pass  # fresh durable chain
            else:
                raise DurabilityError(f"S3 unavailable during checkpoint: {exc}") from exc
        except (json.JSONDecodeError, ValueError, KeyError):
            pass  # invalid pointer — start a fresh chain

        # 1. upload artifacts
        artifact_entries: list[dict[str, Any]] = []
        try:
            for rel in sorted(artifacts):
                body = artifacts[rel]
                self._put(self._key("checkpoints", checkpoint_id, "artifacts", rel), body)
                artifact_entries.append({
                    "path": rel, "size": len(body), "sha256": _sha256_bytes(body),
                })
        except self._client_exceptions() as exc:
            raise DurabilityError(
                f"artifact upload failed for checkpoint {checkpoint_id}: {exc}") from exc

        # 2. manifest = completion marker (uploaded after all artifacts)
        manifest = {
            "schema": "research_state_checkpoint_v1",
            "contract_version": _CONTRACT_VERSION,
            "checkpoint_id": checkpoint_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "generation": generation,
            "previous_checkpoint_id": previous_id,
            "cycle_id": cycle_id,
            "dataset_fingerprint": dataset_fingerprint,
            "status": "complete",
            "artifacts": artifact_entries,
        }
        manifest_key = self._key("checkpoints", checkpoint_id, "manifest.json")
        try:
            self._put(manifest_key, json.dumps(manifest, indent=2).encode("utf-8"))
        except self._client_exceptions() as exc:
            raise DurabilityError(
                f"manifest upload failed for checkpoint {checkpoint_id}: {exc}") from exc

        # 3. advance the latest pointer LAST (only complete checkpoints referenced)
        pointer = {
            "checkpoint_id": checkpoint_id,
            "generation": generation,
            "previous_checkpoint_id": previous_id,
            "manifest_key": manifest_key,
            "completed_at": manifest["created_at"],
            "artifact_count": len(artifact_entries),
            "contract_version": _CONTRACT_VERSION,
        }
        try:
            self._put(self._key("latest_success.json"),
                      json.dumps(pointer, indent=2).encode("utf-8"))
        except self._client_exceptions() as exc:
            raise DurabilityError(
                f"latest-pointer update failed for checkpoint {checkpoint_id}: {exc}") from exc

        logger.info("[STATE_DURABILITY] checkpoint %s complete (gen=%d, artifacts=%d)",
                    checkpoint_id, generation, len(artifact_entries))
        return DurabilityResult(status="durable", checkpoint_id=checkpoint_id,
                                generation=generation, artifact_count=len(artifact_entries),
                                manifest_key=manifest_key)

    # ─── RECOVERY ─────────────────────────────────────────────────────────

    def restore_if_needed(self) -> DurabilityResult:
        """
        Restore the latest complete checkpoint when required local lifecycle
        state is MISSING. If local state exists, it remains authoritative
        (status "skipped"). Never restores lock/temp artifacts (excluded at
        checkpoint time anyway). Falls back along the previous_checkpoint_id
        chain when the latest checkpoint is incomplete/corrupt.
        """
        if _local_state_present():
            return DurabilityResult(status="skipped",
                                    error="local lifecycle state present - runtime authority")

        pointer = self._load_pointer()
        if pointer is None:
            # No durable state anywhere: a genuinely fresh research machine.
            return DurabilityResult(status="skipped",
                                    error="no durable checkpoint exists (fresh research state)")

        tried: list[str] = []
        checkpoint_id = str(pointer.get("checkpoint_id", ""))
        manifest_key = str(pointer.get("manifest_key", ""))
        # The pointer's previous id is the fallback when the LATEST manifest
        # itself is unreadable (corrupt/missing).
        fallback_id = str(pointer.get("previous_checkpoint_id", ""))
        for _ in range(_MAX_FALLBACK_DEPTH):
            try:
                manifest, artifacts = self._download_checkpoint(checkpoint_id, manifest_key)
            except DurabilityError as exc:
                tried.append(f"{checkpoint_id or manifest_key}: {exc}")
                # fall back to the previous complete checkpoint, if chained
                nxt = self._previous_checkpoint_id(checkpoint_id) or fallback_id
                if not nxt or nxt in tried:
                    break
                fallback_id = ""  # pointer fallback only valid one step back
                checkpoint_id, manifest_key = nxt, ""
                continue

            self._write_local(artifacts)
            logger.info("[STATE_DURABILITY] restored checkpoint %s (gen=%s, artifacts=%d)",
                        manifest["checkpoint_id"], manifest.get("generation"), len(artifacts))
            return DurabilityResult(status="recovered",
                                    checkpoint_id=manifest["checkpoint_id"],
                                    generation=int(manifest.get("generation", 0)),
                                    artifact_count=len(artifacts),
                                    manifest_key=manifest_key or "")

        raise DurabilityError(
            "no complete durable checkpoint could be restored; tried: " + "; ".join(tried))

    def _previous_checkpoint_id(self, checkpoint_id: str) -> str:
        try:
            raw = self._get(self._manifest_key_for(checkpoint_id))
            return str(json.loads(raw).get("previous_checkpoint_id", "") or "")
        except Exception:
            return ""

    def _load_pointer(self) -> dict[str, Any] | None:
        try:
            raw = self._get(self._key("latest_success.json"))
        except self._client_exceptions() as exc:
            if self._is_no_such_key(exc):
                return None
            raise DurabilityError(f"S3 unavailable during recovery: {exc}") from exc
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def _manifest_key_for(self, checkpoint_id: str) -> str:
        return self._key("checkpoints", checkpoint_id, "manifest.json")

    def _write_local(self, artifacts: dict[str, bytes]) -> None:
        for rel, body in artifacts.items():
            path = Path(".") / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".restore.tmp")
            tmp.write_bytes(body)
            tmp.replace(path)

    def _download_checkpoint(self, checkpoint_id: str,
                             manifest_key: str) -> tuple[dict[str, Any], dict[str, bytes]]:
        """Download + verify one checkpoint. Raises DurabilityError on any
        incompleteness/corruption (caller may fall back to an older one)."""
        if not checkpoint_id and not manifest_key:
            raise DurabilityError("no checkpoint identity to restore")
        mkey = manifest_key or self._manifest_key_for(checkpoint_id)
        try:
            raw = self._get(mkey)
        except self._client_exceptions() as exc:
            if self._is_no_such_key(exc):
                raise DurabilityError(f"manifest missing: {mkey}")
            raise DurabilityError(f"S3 unavailable during recovery: {exc}") from exc
        try:
            manifest = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DurabilityError(f"manifest corrupt: {mkey}") from exc

        if (manifest.get("status") != "complete"
                or manifest.get("contract_version") != _CONTRACT_VERSION):
            raise DurabilityError(f"checkpoint incomplete or foreign contract: {mkey}")

        artifacts: dict[str, bytes] = {}
        for entry in manifest.get("artifacts", []):
            rel, expected = entry["path"], entry["sha256"]
            if _is_excluded(rel):
                continue  # never restore excluded artifacts
            try:
                body = self._get(self._key("checkpoints",
                                           manifest["checkpoint_id"], "artifacts", rel))
            except self._client_exceptions() as exc:
                if self._is_no_such_key(exc):
                    raise DurabilityError(f"artifact missing: {rel}")
                raise DurabilityError(f"S3 unavailable during recovery: {exc}") from exc
            if _sha256_bytes(body) != expected:
                raise DurabilityError(f"artifact checksum mismatch: {rel}")
            artifacts[rel] = body
        if not artifacts:
            raise DurabilityError(f"checkpoint has no artifacts: {mkey}")
        return manifest, artifacts




