"""Read-only bridge: production continuous-research completed cycles → Lab.

The production continuous coordinator publishes authoritative, verified
``unified_research_projection_v1`` artifacts into its OWN checkout under
``data/research/continuous`` (projection + cycle history).  The original
Research Lab (this Stage A checkout) reads the identical layout from its own
``data/research/continuous`` ``STATE_ROOT``.  This module is the smallest safe
consumer that copies a *newly completed, verified* production cycle into the
Lab's ``STATE_ROOT`` so the existing server surfaces it automatically.

Design constraints (enforced, never relaxed):

* READ-ONLY on the production source.  Files are opened and copied; nothing in
  the source is created, modified, renamed or deleted.  No coordinator, SDK
  client, evaluator or scientific transition is invoked.
* FAIL CLOSED.  A cycle becomes visible only when every integrity, identity,
  lineage and completeness check passes.  Any failure leaves the Lab's prior
  authoritative state untouched and is reported, never silently swallowed.
* SUCCESS ONLY.  Only the production ``latest_success.json`` cycle whose backing
  projection exists and self-identifies is imported.  In-progress / failed
  attempts (``latest_attempt.json`` with ``cycle_outcome != COMPLETED`` or a
  null ``projection_version``) are never surfaced.
* IDEMPOTENT / INCREMENTAL.  A persisted marker records the last imported
  projection_version.  When the source has not advanced, the bridge is a cheap
  stat-only no-op; it never re-reads or re-copies the ~12 MB projection, and it
  never reloads tens of thousands of objects per browser refresh.
* GOVERNANCE PRESERVED.  Copying a projection changes no candidate status, runs
  no promotion, approves no live trading.  The projection's own governed
  verdicts are reproduced verbatim.
* DISTINCTION PRESERVED.  This is the *current production* path only.  It is
  independent of the historical offline archive importer
  (``research_lab_archive``) and of separate local candidate registries; it
  only ever writes under ``STATE_ROOT`` (``projection/`` and ``cycles/``).

The projection schema and the version-identity algorithm are reused verbatim
from the production modules so the bridge can never diverge from the authority.
"""
from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from research_engine.control_plane.stage4_dataset_snapshot import canonical_json
from research_engine.v10.continuous.research_projection import PROJECTION_SCHEMA

# Canonical 70 is an invariant of the unified projection; the Lab enforces the
# same count, so the bridge refuses to import anything that fails it.
_CANONICAL_COUNT = 70

# Environment override for the production continuous source directory (the
# coordinator's ``data/research/continuous``). When unset, use this checkout's
# continuous state. A separate producer checkout requires the environment override.
_SOURCE_ENV = "RESEARCH_LAB_CONTINUOUS_SOURCE"
_DEFAULT_SOURCE = Path(__file__).resolve().parents[3] / "data" / "research" / "continuous"

# Idempotency marker written inside the Lab STATE_ROOT.
_MARKER_NAME = "continuous_bridge_state.json"
_MARKER_SCHEMA = "research_lab_continuous_bridge_v1"


class ContinuousBridgeError(RuntimeError):
    """A production completed cycle could not be safely imported."""


@dataclass(frozen=True)
class BridgeResult:
    """Outcome of one bridge pass (never raised for a benign no-op)."""

    status: str                     # IMPORTED | UNCHANGED | NO_SOURCE | NO_COMPLETED_CYCLE | ERROR
    imported_projection_version: str | None = None
    imported_cycle_id: str | None = None
    source: str | None = None
    reason: str | None = None
    # Real production completion time (from the cycle record), the projection
    # history file's publication mtime (independent evidence of when production
    # wrote it), and the local import/sync time — kept DISTINCT so the Lab can
    # never present an import as a newly completed research cycle.
    production_completed_at: str | None = None
    projection_published_at: str | None = None
    imported_at: str | None = None
    # Read-only diagnostic of production's LATEST ATTEMPT (which may be a FAILED
    # or in-progress cycle that is deliberately NOT imported).  Surfaced so the
    # Lab can honestly show "last attempt + outcome" without ever treating a
    # non-successful attempt as a completed research cycle.
    latest_attempt: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "research_lab_continuous_bridge_result_v1",
            "status": self.status,
            "imported_projection_version": self.imported_projection_version,
            "imported_cycle_id": self.imported_cycle_id,
            "source": self.source,
            "reason": self.reason,
            "production_completed_at": self.production_completed_at,
            "projection_published_at": self.projection_published_at,
            "imported_at": self.imported_at,
            "latest_attempt": self.latest_attempt,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Source resolution
# ─────────────────────────────────────────────────────────────────────────────
def resolve_source(explicit: Path | str | None = None) -> Path | None:
    """Resolve the production continuous source directory, or None.

    Precedence: explicit argument → ``RESEARCH_LAB_CONTINUOUS_SOURCE`` env →
    this checkout's continuous state. Returns None when the resolved
    directory does not exist (the bridge then no-ops; the Lab reports its own
    empty state truthfully instead of inventing one).
    """
    candidate: Path | None = None
    if explicit:
        candidate = Path(explicit)
    else:
        env = os.environ.get(_SOURCE_ENV)
        candidate = Path(env) if env else _DEFAULT_SOURCE
    try:
        if candidate.is_dir():
            return candidate.resolve()
    except OSError:
        return None
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Verification helpers (fail closed)
# ─────────────────────────────────────────────────────────────────────────────
def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ContinuousBridgeError(f"UNREADABLE:{path.name}:{exc}") from exc


def _mtime_iso(path: Path) -> str | None:
    """File modification time (UTC ISO).  For the source projection history
    file this is the only independent local evidence of when production wrote
    (published) the artifact — it is NOT the research completion time."""
    try:
        return datetime.fromtimestamp(
            path.stat().st_mtime, timezone.utc).isoformat()
    except OSError:
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _production_latest_attempt(src: Path) -> dict[str, Any] | None:
    """Read-only diagnostic of the production coordinator's latest attempt.

    Returns a small labelled summary (never the full record) so the Lab can
    show the real last-attempt outcome/failure.  This is observational only and
    is NEVER imported as a cycle.  A failed/in-progress attempt reported here
    does not and cannot replace the last successful imported projection.
    """
    attempt_path = src / "cycles" / "latest_attempt.json"
    if not attempt_path.exists():
        return None
    try:
        row = json.loads(attempt_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(row, Mapping):
        return None
    # The pointer carries stage but often not the full failure_reason; read it
    # from the attempt's own history record when present (read-only).
    failure_reason = row.get("failure_reason")
    attempt_cycle_id = row.get("continuous_cycle_id")
    if not failure_reason and attempt_cycle_id:
        hist = src / "cycles" / "history" / f"{attempt_cycle_id}.json"
        if hist.exists():
            try:
                hrow = json.loads(hist.read_text(encoding="utf-8"))
                if isinstance(hrow, Mapping):
                    failure_reason = hrow.get("failure_reason")
            except (OSError, ValueError):
                pass
    return {
        "scope": "PRODUCTION_LATEST_ATTEMPT_DIAGNOSTIC",
        "continuous_cycle_id": row.get("continuous_cycle_id"),
        "cycle_outcome": row.get("cycle_outcome"),
        "failure_stage": row.get("failure_stage"),
        "failure_reason": failure_reason,
        "projection_version": row.get("projection_version"),
        "predecessor_cycle_id": row.get("predecessor_cycle_id"),
        "observed_at": _mtime_iso(attempt_path),
        "is_successful": row.get("cycle_outcome") == "COMPLETED"
                         and bool(row.get("projection_version")),
        "imported": False,
        "note": "Observational only; a non-successful attempt is never imported "
                "and never replaces the last successful projection.",
    }


def _expected_version(projection: Mapping[str, Any]) -> str:
    """Recompute the governed projection_version from the artifact itself.

    Mirrors ``build_unified_research_projection`` exactly: the version is
    ``"RPROJ-" + sha256(canonical_json(material))[:32].upper()`` where material
    is the projection with ``projection_schema``/``projection_version`` removed.
    A copied artifact whose content does not reproduce its own stated version is
    rejected — this is the integrity + identity check in one.
    """
    import hashlib

    material = {
        k: v for k, v in projection.items()
        if k not in {"projection_schema", "projection_version"}
    }
    digest = hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()
    return "RPROJ-" + digest[:32].upper()


def _verify_projection(projection: Any, *, expected_version: str) -> None:
    """Fail closed unless the projection is a complete, self-identifying v1."""
    if not isinstance(projection, Mapping):
        raise ContinuousBridgeError("PROJECTION_NOT_OBJECT")
    if projection.get("projection_schema") != PROJECTION_SCHEMA:
        raise ContinuousBridgeError(
            "PROJECTION_SCHEMA_INVALID:"
            + str(projection.get("projection_schema") or "MISSING"))
    stated = str(projection.get("projection_version") or "")
    if not stated:
        raise ContinuousBridgeError("PROJECTION_VERSION_MISSING")
    if stated != expected_version:
        raise ContinuousBridgeError(
            f"PROJECTION_VERSION_LINEAGE_MISMATCH:{stated}!={expected_version}")
    recomputed = _expected_version(projection)
    if recomputed != stated:
        # Content does not reproduce its own governed identity → corrupt/forged.
        raise ContinuousBridgeError(
            f"PROJECTION_IDENTITY_MISMATCH:{recomputed}!={stated}")
    canonical = projection.get("canonical_questions")
    if not isinstance(canonical, list) or len(canonical) != _CANONICAL_COUNT:
        raise ContinuousBridgeError(
            "PROJECTION_CANONICAL_COUNT_INVALID:"
            + str(len(canonical) if isinstance(canonical, list) else "NONE"))


def _verify_success_cycle(cycle: Any, *, expected_version: str,
                          expected_cycle_id: str) -> None:
    """Fail closed unless the cycle record is a COMPLETED success for this id."""
    if not isinstance(cycle, Mapping):
        raise ContinuousBridgeError("CYCLE_RECORD_NOT_OBJECT")
    if cycle.get("cycle_outcome") != "COMPLETED":
        raise ContinuousBridgeError(
            "CYCLE_NOT_COMPLETED:" + str(cycle.get("cycle_outcome") or "MISSING"))
    if str(cycle.get("continuous_cycle_id") or "") != expected_cycle_id:
        raise ContinuousBridgeError("CYCLE_ID_MISMATCH")
    if str(cycle.get("projection_version") or "") != expected_version:
        raise ContinuousBridgeError("CYCLE_PROJECTION_VERSION_MISMATCH")


# ─────────────────────────────────────────────────────────────────────────────
# Marker (idempotency)
# ─────────────────────────────────────────────────────────────────────────────
def _marker_path(state_root: Path) -> Path:
    return state_root / _MARKER_NAME


def _read_marker(state_root: Path) -> dict[str, Any]:
    path = _marker_path(state_root)
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _atomic_copy(source: Path, destination: Path) -> None:
    """Byte-for-byte atomic copy; destination parent is created as needed."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        # Copy bytes, then flush+fsync the temp via a write-mode handle so the
        # replace publishes durable content (fsync requires a writable fd).
        with source.open("rb") as src_handle, temp.open("wb") as dst_handle:
            shutil.copyfileobj(src_handle, dst_handle, length=1024 * 1024)
            dst_handle.flush()
            os.fsync(dst_handle.fileno())
        os.replace(temp, destination)
    finally:
        temp.unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────
def sync_latest_completed_cycle(
    state_root: Path | str,
    *,
    source: Path | str | None = None,
) -> BridgeResult:
    """Import the production latest completed verified cycle into ``state_root``.

    Returns a :class:`BridgeResult`.  Never raises for benign conditions
    (missing source, no completed cycle yet, already up to date).  Raising is
    reserved for integrity failures that must be surfaced to the operator; the
    caller (the Lab server) treats any raise as "do not change Lab state".
    """
    state_root = Path(state_root)
    src = resolve_source(source)
    if src is None:
        return BridgeResult(status="NO_SOURCE",
                            reason="No production continuous source directory present")

    attempt_diag = _production_latest_attempt(src)
    completed_pointer = src / "cycles" / "latest_completed.json"
    success_pointer = (completed_pointer if completed_pointer.exists()
                       else src / "cycles" / "latest_success.json")
    if not success_pointer.exists():
        return BridgeResult(status="NO_COMPLETED_CYCLE", source=str(src),
                            reason="Production has published no completed cycle yet",
                            latest_attempt=attempt_diag)

    pointer = _read_json(success_pointer)
    if not isinstance(pointer, Mapping):
        raise ContinuousBridgeError("SUCCESS_POINTER_INVALID")
    if pointer.get("cycle_outcome") != "COMPLETED":
        return BridgeResult(status="NO_COMPLETED_CYCLE", source=str(src),
                            reason="Latest successful attempt has no completed publication pointer",
                            latest_attempt=attempt_diag)
    cycle_id = str(pointer.get("continuous_cycle_id") or "")
    version = str(pointer.get("projection_version") or "")
    if not cycle_id or not version:
        raise ContinuousBridgeError("SUCCESS_POINTER_INCOMPLETE")

    # ── Idempotent / incremental: stat-only no-op when already current. ──────
    marker = _read_marker(state_root)
    latest_dst = state_root / "projection" / "latest.json"
    if (marker.get("imported_projection_version") == version
            and marker.get("imported_cycle_id") == cycle_id
            and latest_dst.exists()):
        return BridgeResult(
            status="UNCHANGED", source=str(src),
            imported_projection_version=version, imported_cycle_id=cycle_id,
            production_completed_at=marker.get("production_completed_at"),
            projection_published_at=marker.get("projection_published_at"),
            imported_at=marker.get("imported_at"),
            latest_attempt=attempt_diag)

    # ── Locate and verify the backing artifacts (fail closed). ───────────────
    projection_history = src / "projection" / "history" / f"{version}.json"
    if not projection_history.exists():
        raise ContinuousBridgeError("PROJECTION_HISTORY_MISSING:" + version)
    cycle_history = src / "cycles" / "history" / f"{cycle_id}.json"
    if not cycle_history.exists():
        raise ContinuousBridgeError("CYCLE_HISTORY_MISSING:" + cycle_id)

    projection = _read_json(projection_history)
    _verify_projection(projection, expected_version=version)
    cycle_record = _read_json(cycle_history)
    _verify_success_cycle(cycle_record, expected_version=version,
                          expected_cycle_id=cycle_id)

    # Defence in depth: if production's latest.json exists it must match the
    # verified history artifact exactly (same governed identity).
    src_latest = src / "projection" / "latest.json"
    if src_latest.exists():
        if canonical_json(_read_json(src_latest)) != canonical_json(projection):
            raise ContinuousBridgeError("SOURCE_LATEST_DIVERGES_FROM_HISTORY")

    # ── Publish into the Lab STATE_ROOT (immutable history + replaceable
    #    latest), then the cycle history + success pointer, then the marker.
    #    Order: content before pointers so a crash never advertises an absent
    #    artifact. ────────────────────────────────────────────────────────────
    if src != state_root.resolve():
        dst_proj_history = state_root / "projection" / "history" / f"{version}.json"
        if not dst_proj_history.exists():
            _atomic_copy(projection_history, dst_proj_history)
        elif canonical_json(_read_json(dst_proj_history)) != canonical_json(projection):
            # Immutable history must never silently change under a stable version id.
            raise ContinuousBridgeError("DEST_HISTORY_IDENTITY_COLLISION:" + version)

        dst_cycle_history = state_root / "cycles" / "history" / f"{cycle_id}.json"
        if not dst_cycle_history.exists():
            _atomic_copy(cycle_history, dst_cycle_history)

        # latest.json is a replaceable read model — copy the verified artifact.
        _atomic_copy(projection_history, latest_dst)

        # The Lab copy points at its imported completed cycle. A producer that
        # shares this root retains its own latest-attempt/latest-success pointers.
        _atomic_write(state_root / "cycles" / "latest_success.json", dict(pointer))
        _atomic_write(state_root / "cycles" / "latest_completed.json", dict(pointer))

    # Distinct, auditable timestamps (never conflated):
    #   production_completed_at — real research completion from the cycle record
    #   projection_published_at — when production wrote the projection artifact
    #                             (source history file mtime; local evidence)
    #   imported_at             — when THIS Lab synced it locally
    production_completed_at = (
        str(pointer.get("completed_at")
            or cycle_record.get("completed_at") or "") or None)
    projection_published_at = _mtime_iso(projection_history)
    imported_at = _now_iso()

    # Marker last: records the completed import for cheap future no-ops.
    _atomic_write(_marker_path(state_root), {
        "schema": _MARKER_SCHEMA,
        "imported_projection_version": version,
        "imported_cycle_id": cycle_id,
        "source": str(src),
        "production_completed_at": production_completed_at,
        "projection_published_at": projection_published_at,
        "imported_at": imported_at,
    })

    return BridgeResult(status="IMPORTED", source=str(src),
                        imported_projection_version=version,
                        imported_cycle_id=cycle_id,
                        production_completed_at=production_completed_at,
                        projection_published_at=projection_published_at,
                        imported_at=imported_at,
                        latest_attempt=attempt_diag)


__all__ = [
    "ContinuousBridgeError",
    "BridgeResult",
    "resolve_source",
    "sync_latest_completed_cycle",
]
